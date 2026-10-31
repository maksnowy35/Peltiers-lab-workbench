"""Per-system physics against the numbers printed in the thesis."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from teglab.pipeline.extract import linear_fit_by_system, max_power_by_system
from teglab.pipeline.run import RunOptions, run_pipeline

from .conftest import FULL_LOAD_D, TT_THESIS


def _summary(files, profile, **kw):
    result = run_pipeline(files, RunOptions(profile=profile, **kw))
    return result.summary, result.report


@pytest.fixture()
def a0_tt(profile, a0tt):
    return _summary([a0tt], profile, default_condition=TT_THESIS, variants={"A": "A0"})


@pytest.fixture()
def a_series(profile, aseries, fig310):
    return _summary(
        [aseries, fig310], profile, conditions={"D": FULL_LOAD_D}, variants={"A": "A0", "B": "A5", "C": "A10"}
    )


def test_a0_tt_load_curve_table_4_2(a0_tt):
    s = a0_tt[0].loc["A"]
    assert s["u_oc_v"] == pytest.approx(30.02e-3)
    assert s["apparent_ohm"] == pytest.approx(17.26e-3, rel=1e-3)
    assert s["apparent_is_thevenin"]
    assert s["p_max_w"] == pytest.approx(13.05e-3, rel=1e-3)
    assert s["i_opt_a"] == pytest.approx(0.870, abs=0.001)
    assert s["p_method"].startswith("linear source")
    assert s["balance_rel_max"] < 1.4e-5  # Table 4.2: energy-balance error ≤ 0.0014 %


def test_a0_tt_passive_split_table_4_3(a0_tt):
    s = a0_tt[0].loc["A"]
    assert s["dt_hot_passive_k"] == pytest.approx(2.84)
    assert s["dt_cold_passive_k"] == pytest.approx(2.09)
    # 1 − ΔT_TE/80 K: 6.1 % with ΔT_TE = 75.11 K (Table 4.2); the printed drops 2.84 + 2.09 K give 6.16 %
    assert s["passive_share"] == pytest.approx(0.0616, abs=0.0005)
    assert s["r_th_cold_k_w"] == pytest.approx(6.63, abs=0.01)
    assert s["r_th_hot_k_w"] == pytest.approx(9.01, abs=0.01)  # Table 4.3 prints 8.85 K/W for the same 2.84 K
    assert s["r_th_legs_k_w"] == pytest.approx(238.2, abs=0.2)  # 238.3 K/W with ΔT_TE = 75.11 K
    assert s["contact_dt_hot_expected_k"] == pytest.approx(0.80, abs=0.005)  # Section 5.2: 0.80 K per contact


def test_a_series_open_circuit_table_5_4(a_series):
    s = a_series[0]
    assert s.loc[["A", "B", "C"], "k_te_w_k"].to_numpy() == pytest.approx([4.196e-3, 3.451e-3, 2.676e-3], abs=1e-6)
    assert s.loc[["A", "B", "C"], "alpha_eff_v_k"].to_numpy() == pytest.approx([400e-6] * 3, rel=2e-3)
    # Section 5.3: passive share 4.93 K for A0 vs 3.42 K and 3.51 K for the tapered variants
    passive = s.loc[["A", "B", "C"], "dt_hot_passive_k"] + s.loc[["A", "B", "C"], "dt_cold_passive_k"]
    assert passive.to_numpy() == pytest.approx([4.93, 3.42, 3.51], abs=0.005)


def test_ohmic_resistance_isolates_contacts(a_series):
    s = a_series[0]
    for system in ("C", "D"):  # mesh C1 (Table 3.5) and mesh C2 (Fig. 3.10)
        assert s.at[system, "ohmic_ohm"] == pytest.approx(22.6e-3, abs=0.05e-3)
        assert s.at[system, "contacts_expected_ohm"] == pytest.approx(3.46e-3, abs=0.01e-3)
        assert abs(s.at[system, "contacts_isolated_ohm"]) < 0.2e-3
        assert s.at[system, "contact_verdict"] == "inactive"
    assert s.at["A", "contacts_expected_ohm"] == pytest.approx(2.04e-3, abs=0.005e-3)  # Section 4.2


def test_contact_verdict_is_active_when_the_data_contain_the_contacts(profile):
    # A10 at 0.8 A with the contact drop present: U lower by I·R_contacts = 0.8 A · 3.455 mΩ
    text = (
        "# P1 - Current Magnitude, P2 - Voltage Probe Electric Voltage, P3 - Temperature Average, "
        "P4 - T_leg_cold_avg Average\nName,P1,P2,P3,P4\nUnits,A,V,C,C\n"
        "DP 0,0,0.047137,139.13,21.33\nDP 1,0.8,0.009620,97.45,21.291\n"
    )
    s, _ = _summary([("a10.csv", text.encode())], profile, variants={"A": "A10"})
    assert s.at["A", "contact_verdict"] == "active"


def test_batched_linear_fit_matches_polyfit():
    rng = np.random.default_rng(1)
    frames = []
    for g, (a, b) in enumerate([(0.03, -0.017), (0.047, -0.0226), (0.01, 0.2)]):
        x = rng.uniform(0, 2, 9)
        frames.append(pd.DataFrame({"system": chr(65 + g), "x": x, "y": a + b * x + rng.normal(0, 1e-4, 9)}))
    f = pd.concat(frames)
    fit = linear_fit_by_system(f, "x", "y")
    for s, grp in f.groupby("system"):
        slope, intercept = np.polyfit(grp["x"], grp["y"], 1)
        assert fit.at[s, "slope"] == pytest.approx(slope) and fit.at[s, "intercept"] == pytest.approx(intercept)


def _curve(points: dict[str, list[tuple[float, float]]]) -> pd.DataFrame:
    rows = [(s, f"DP {k}", i, p) for s, pts in points.items() for k, (i, p) in enumerate(pts)]
    f = pd.DataFrame(rows, columns=["system", "design_point", "current_a", "power_w"])
    f["voltage_v"] = f["power_w"] / f["current_a"].where(f["current_a"] > 0)
    return f.set_index(["system", "design_point"])


def test_parabola_vertex_is_bracketed_or_flagged():
    p = lambda i: 8.27e-3 - 30e-3 * (i - 0.56) ** 2  # noqa: E731
    frame = _curve(
        {
            "A": [(i, p(i)) for i in (0.0, 0.3, 0.5, 0.6, 0.8, 1.2)],  # samples around the optimum
            "B": [(i, p(i)) for i in (0.0, 0.1, 0.2, 0.3)],  # sweep stops before the optimum
        }
    )
    res = pd.DataFrame({"apparent_is_thevenin": [False, False], "apparent_ohm": [np.nan, np.nan]}, index=["A", "B"])
    out = max_power_by_system(frame, res)
    assert out.at["A", "bracketed"] and out.at["A", "p_max_w"] == pytest.approx(8.27e-3)
    assert out.at["A", "i_opt_a"] == pytest.approx(0.56)
    assert not out.at["B", "bracketed"] and out.at["B", "method"].startswith("largest sampled point")
