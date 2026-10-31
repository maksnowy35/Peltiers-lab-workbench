"""Canonical frame: exact-name mapping, units, sign convention, system ownership, validation."""

from __future__ import annotations

import pandas as pd
import pytest
from pydantic import ValidationError

from teglab.pipeline.frames import build_canonical, resolve_parameters, validate_frame
from teglab.pipeline.keys import QuantityKey
from teglab.pipeline.readers import read_source
from teglab.pipeline.schemas import CaseCondition, ParameterDef, ProjectProfile

from .conftest import FULL_LOAD_D, QT_THESIS


def test_exact_aliases_and_patterns(profile):
    params = [
        ParameterDef(pid="P97", name="Reaction Probe 2 Heat", unit="W"),
        ParameterDef(pid="P1", name="Current Magnitude", unit="A"),
        ParameterDef(pid="P7", name="Some Other Probe", unit="V"),
        ParameterDef(pid="P8", name="  Temperature   Average ", unit="C"),
    ]
    r = {p.pid: p for p in resolve_parameters(params, profile)}
    assert (r["P97"].key, r["P97"].rule, r["P97"].confidence) == (QuantityKey.Q_COLD, "alias", 1.0)
    assert (r["P1"].key, r["P1"].rule, r["P1"].confidence) == (QuantityKey.CURRENT, "pattern", 0.8)
    assert r["P1"].direction == "input"
    assert r["P7"].key is None and r["P7"].rule == "unresolved"
    assert r["P8"].key is QuantityKey.T_LEG_HOT  # whitespace-normalized, case-insensitive


def test_units_are_converted_vectorized(profile):
    text = "# P1 - Current Magnitude, P2 - Voltage Probe Electric Voltage, P3 - T_max_global Maximum\n"
    text += "Name,P1,P2,P3\nUnits,mA,mV,K\nDP 0,800,12.385,372.778\n"
    data = build_canonical([read_source(text.encode(), "u.csv")], profile, conditions={"A": QT_THESIS})
    row = data.frame.loc[("A", "DP 0")]
    assert row["current_a"] == pytest.approx(0.8)
    assert row["voltage_v"] == pytest.approx(0.012385)
    assert row["t_max_c"] == pytest.approx(99.628)


def test_fig_3_10_reproduces_table_3_5_and_the_energy_balance(profile, fig310):
    data = build_canonical([read_source(fig310[1], fig310[0])], profile, conditions={"D": FULL_LOAD_D})
    row = data.frame.loc[("D", "Current")]
    assert row["q_cold_w"] == pytest.approx(0.30505)  # reaction sign normalized
    assert row["dt_te_k"] == pytest.approx(76.187)  # Table 3.5, mesh C2
    assert row["power_w"] == pytest.approx(9.908e-3)  # Table 3.5, mesh C2
    # Section 3.5.3: "the energy balance closes with a deficit of 0.24 mW, i.e. 0.08 % of the heat input"
    assert row["balance_w"] == pytest.approx(0.24e-3, abs=0.005e-3)
    assert row["balance_rel"] == pytest.approx(0.0008, abs=0.00005)
    sys = data.systems.loc["D"]
    assert (sys["variant"], sys["theta_deg"], sys["mesh"], sys["bc"]) == ("A10", 10.0, "C2", "QT")
    assert any(i.code == "sign-normalized" for i in data.issues)


def test_repeated_names_split_into_systems_and_outline_fixes_ownership(profile, aseries, fig310):
    tables = [read_source(aseries[1], aseries[0]), read_source(fig310[1], fig310[0])]
    data = build_canonical(tables, profile, conditions={"D": FULL_LOAD_D}, variants={"A": "A0", "B": "A5", "C": "A10"})
    assert list(data.systems.index) == ["A", "B", "C", "D"]
    owners = {p.pid: p.system for p in data.catalog}
    assert owners["P2"] == "A" and owners["P7"] == "B" and owners["P12"] == "C" and owners["P99"] == "D"
    assert owners["P1"] is None  # the current is a shared input
    # the shared current of the design-point table is broadcast to its own systems only
    assert set(data.frame.index.get_level_values("system")) == {"A", "B", "C", "D"}
    assert list(data.frame.loc["D"].index) == ["Current"]
    assert data.frame.loc[("C", "DP 1"), "voltage_v"] == 0.012384
    assert any(i.code == "systems-inferred" for i in data.issues)


def test_imposed_boundary_conditions_fill_gaps(profile, aseries):
    data = build_canonical([read_source(aseries[1], aseries[0])], profile)
    assert (data.frame["q_hot_w"] == 0.3152).all()
    assert (data.frame["t_cold_face_c"] == 20.0).all()


def test_validation_reports_out_of_range_values():
    frame = pd.DataFrame(
        {"t_max_c": [25.0, -300.0], "current_a": [0.1, 0.2]},
        index=pd.MultiIndex.from_tuples([("A", "DP 0"), ("A", "DP 1")], names=["system", "design_point"]),
    )
    from teglab.pipeline.frames import DESIGN_POINT_SPEC

    issues = validate_frame(frame, DESIGN_POINT_SPEC)
    assert len(issues) == 1 and "t_max_c" in issues[0].message and "A/DP 1" in issues[0].message


def test_profile_contracts():
    with pytest.raises(ValidationError, match="mapped to both"):
        ProjectProfile(name="x", aliases={"voltage_v": ["U"], "current_a": ["u"]})
    with pytest.raises(ValidationError):
        ProjectProfile(name="x", aliases={}, patterns={"voltage_v": ["(unclosed"]})
    with pytest.raises(ValidationError, match="BC-TT needs t_hot_face_c"):
        CaseCondition(bc="TT", t_cold_face_c=20)
    with pytest.raises(ValidationError, match="BC-QT needs q_hot_w"):
        CaseCondition(bc="QT", t_cold_face_c=20)
