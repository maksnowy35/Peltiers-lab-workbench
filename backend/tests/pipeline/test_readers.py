"""Readers: exact structure of the thesis exports, encodings, delimiters, format detection."""

from __future__ import annotations

import io

import numpy as np
import pytest
from openpyxl import Workbook

from teglab.pipeline.readers import NodalTable, RawTable, read_source, sniff
from teglab.pipeline.schemas import SourceFormat

from .conftest import body_nodes, mechanical_export


def test_outline_of_all_parameters_fig_3_10(fig310):
    t = read_source(fig310[1], fig310[0])
    assert isinstance(t, RawTable)
    assert t.format is SourceFormat.WORKBENCH_OUTLINE
    assert [(s.letter, s.title, s.cell) for s in t.systems] == [("D", "Dense mesh copy C 10st", "D1")]
    names = {p.pid: (p.name, p.unit, p.system) for p in t.parameters}
    assert names == {
        "P97": ("Reaction Probe 2 Heat", "W", "D"),
        "P99": ("Voltage Probe Electric Voltage", "V", "D"),
        "P100": ("Total Current Density Average", "A m^-2", "D"),
        "P101": ("Total Current Density Maximum", "A m^-2", "D"),
        "P102": ("T_max_global Maximum", "C", "D"),
        "P103": ("Electric Voltage Average", "V", "D"),
        "P104": ("Joule Heat Average", "W m^-3", "D"),
        "P105": ("Temperature Average", "C", "D"),
        "P106": ("T_leg_cold_avg Average", "C", "D"),
    }
    row = t.values.loc["Current"]
    assert row["P97"] == -0.30505 and row["P99"] == 0.012385 and row["P100"] == 8.7566e5 and row["P105"] == 97.501


def _cell(text: str) -> float | str | None:
    try:
        return float(text)
    except ValueError:
        return text or None


def test_outline_copied_to_excel(fig310):
    wb = Workbook()
    ws = wb.active
    for line in fig310[1].decode().splitlines():
        ws.append([_cell(c) for c in line.split(",")])
    buf = io.BytesIO()
    wb.save(buf)
    t = read_source(buf.getvalue(), "outline.xlsx")
    assert t.format is SourceFormat.WORKBENCH_OUTLINE
    assert len(t.parameters) == 9
    assert t.values.loc["Current", "P106"] == pytest.approx(21.314)


def test_design_point_table(aseries):
    t = read_source(aseries[1], aseries[0])
    assert t.format is SourceFormat.WORKBENCH_DESIGN_POINTS
    assert t.confidence == 0.95
    assert [p.pid for p in t.parameters][:3] == ["P1", "P2", "P3"]
    assert t.parameters[0].name == "Current Magnitude" and t.parameters[0].unit == "A"
    assert t.parameters[3].name == "Temperature Average" and t.parameters[3].unit == "C"
    assert list(t.values.index) == ["DP 0", "DP 1", "DP 2"]
    assert t.values.loc["DP 1", "P12"] == 0.012384
    assert np.isnan(t.values.loc["DP 1", "P2"])  # empty cell = not reported


def test_design_points_with_decimal_commas_and_semicolons():
    text = (
        "# P1 - Current Magnitude, P2 - Voltage Probe Electric Voltage\n"
        "Name;P1;P2\nUnits;A;mV\nDP 0;0;30,02\nDP 1;0,4;23,116\n"
    )
    t = read_source(text.encode(), "dp.csv")
    assert t.values.loc["DP 1", "P1"] == 0.4 and t.values.loc["DP 0", "P2"] == 30.02
    assert t.parameters[1].unit == "mV"


def test_mechanical_nodal_export_windows_code_page():
    frame = body_nodes("leg_p")
    t = read_source(mechanical_export(frame, unit="m"), "T_leg_p.txt")
    assert isinstance(t, NodalTable)
    assert t.averaged
    assert list(t.frame.columns) == ["node_id", "x_mm", "y_mm", "z_mm", "temperature_c"]
    assert t.frame["y_mm"].max() == pytest.approx(2.4)  # metres converted to millimetres
    assert t.frame["temperature_c"].max() == pytest.approx(97.96)


def test_unaveraged_export_is_detected():
    frame = body_nodes("cu_hot")
    doubled = (
        mechanical_export(frame.iloc[:10], unit="mm")
        + mechanical_export(frame.iloc[:10], unit="mm").split(b"\r\n", 1)[1]
    )
    t = read_source(doubled, "unaveraged.txt")
    assert not t.averaged
    assert any("unaveraged" in n for n in t.notes)


def test_sniff_orders_formats_and_rejects_unknown(fig310):
    assert sniff(fig310[1], fig310[0])[0] == (SourceFormat.WORKBENCH_OUTLINE, 0.95)
    with pytest.raises(ValueError, match="not a recognized ANSYS export"):
        read_source(b"just,some\n1,2\n", "random.csv")
