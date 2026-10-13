import io

import pytest

from teglab.ansys_io import build_cases, parse_files, parse_text
from teglab.demo import synthetic_a_series_csv, synthetic_mechanical_tabular, thesis_a_series_csv
from teglab.units import normalize_unit, to_canonical, unit_dimension


def test_unit_parsing():
    assert unit_dimension("W m^-2 C^-1") == "tcc"
    assert unit_dimension("A m^-2") == "current_density"
    assert unit_dimension("[°C]") == "temperature"
    assert unit_dimension("mV") == "voltage"
    assert unit_dimension("W/(m²·K)") == "tcc"
    assert normalize_unit("S m^-2") == "sm-2"
    vals, note = to_canonical([300.0], "K", "temperature")
    assert vals[0] == pytest.approx(26.85) and note is None
    vals, note = to_canonical([12.0], "mV", "voltage")
    assert vals[0] == pytest.approx(0.012)
    _, note = to_canonical([1.0], None, "voltage")
    assert "assumed" in note


def test_workbench_design_points_with_definitions(a0tt_case):
    c = a0tt_case
    assert c.data["current"] == [0.0, 0.4, 0.8, 1.8]
    assert c.data["q_hot"][0] == pytest.approx(0.31519)
    assert c.data["q_cold"][0] == pytest.approx(0.31519)  # reaction sign normalized
    assert c.data["t_leg_hot"][0] == pytest.approx(97.16)  # 'Temperature Average' paired with T_leg_cold


def test_multi_system_table_is_split():
    t = parse_files([("aseries.csv", thesis_a_series_csv().encode())])
    assert t.format == "workbench-design-points"
    assert t.n_cases == 3
    volt = [c for c in t.columns if c.role == "voltage"]
    assert [c.case for c in volt] == [1, 2, 3]
    cur = next(c for c in t.columns if c.role == "current")
    assert cur.case == 0  # shared input


def test_build_cases_drops_empty_rows(aseries_cases):
    a0, a5, a10 = aseries_cases
    assert a0.data["current"] == [0.0, 1.6]
    assert a10.data["current"] == [0.0, 0.8, 1.6]
    assert a10.data["voltage"][1] == pytest.approx(0.012384)


def test_dense_synthetic_sweep_roundtrip():
    t = parse_files([("syn.csv", synthetic_a_series_csv().encode())])
    cases, notes = build_cases(
        t, [{"index": k, "name": f"c{k}", "meta": {"bc": "QT", "q_hot_w": 0.3152}} for k in (1, 2, 3)]
    )
    assert len(cases) == 3 and all(c.n_points == 15 for c in cases)
    assert {r for r in cases[0].data} >= {"current", "voltage", "q_cold", "t_hot_ext", "j_max"}


def test_mechanical_tabular_time_to_current():
    t = parse_files([("tab.txt", synthetic_mechanical_tabular().encode())])
    assert t.format == "mechanical-tabular"
    roles = {c.name: c.role for c in t.columns}
    assert roles["Time"] == "time" and roles["Maximum"] == "t_max"
    cases, notes = build_cases(t, [{"index": 1, "name": "A10", "meta": {"bc": "QT"}}], time_to_current=0.8)
    assert cases[0].data["current"] == pytest.approx([0.16, 0.32, 0.48, 0.64, 0.8])


def test_semicolon_decimal_comma_and_brackets():
    text = "I [A];U [mV];Q_h [W];T hot [°C]\n0;30,02;0,31519;100\n0,4;23,116;0,36971;100\n"
    t = parse_files([("pl.csv", text.encode("cp1250"))])
    roles = {c.role: c for c in t.columns}
    assert roles["current"].values == [0.0, 0.4]
    assert roles["voltage"].values == [30.02, 23.116]
    assert "decimal commas detected" in t.notes
    cases, _ = build_cases(t, [{"index": 1, "name": "x", "meta": {"bc": "TT"}}])
    assert cases[0].data["voltage"] == pytest.approx([0.03002, 0.023116])


def test_outline_files_are_merged_into_rows():
    def outline(i, u):
        return f"ID,Parameter Name,Value,Unit\nP1,Current Magnitude,{i},A\nP99,Voltage Probe Electric Voltage,{u},V\n"

    t = parse_files([("dp0.csv", outline(0, 0.03).encode()), ("dp1.csv", outline(0.8, 0.012).encode())])
    assert t.n_rows == 2
    cur = next(c for c in t.columns if c.role == "current")
    assert cur.values == [0.0, 0.8]


def test_xlsx_import():
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["# exported from Excel"])
    ws.append(["Current [A]", "Voltage [V]", "Reaction Probe 2 Heat [W]"])
    ws.append([0, 0.03, -0.3152])
    ws.append([0.5, 0.015, -0.307])
    buf = io.BytesIO()
    wb.save(buf)
    t = parse_files([("book.xlsx", buf.getvalue())])
    roles = [c.role for c in t.columns]
    assert roles == ["current", "voltage", "q_cold"]


def test_split_by_column():
    text = "angle [deg],I [A],U [V]\n0,0,0.03\n0,0.5,0.02\n10,0,0.047\n10,0.5,0.03\n"
    t = parse_text(text, "stacked.csv")
    from teglab.ansys_io import suggest

    t = suggest(t)
    key = next(c.key for c in t.columns if c.role == "angle")
    cases, _ = build_cases(t, [{"index": 1, "name": "sweep", "meta": {"bc": "QT"}}], split_by=key)
    assert [c.name for c in cases] == ["sweep [0]", "sweep [10]"]
    assert cases[1].data["voltage"] == [0.047, 0.03]
