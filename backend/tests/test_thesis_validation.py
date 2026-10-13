"""The calibrated 1D model against the numbers printed in the thesis.

Calibration uses A0-TT only (Tables 4.2–4.4).  Everything else is a prediction.
A5/A10 match the thesis only with ideal (bonded) contacts - see docs/validation.md.
"""

import pytest

from teglab.calibration import calibrate
from teglab.reference import T_MAX_AT_1_6A_C, TABLE_3_5, TABLE_4_2, TABLE_4_3, TABLE_5_4, THESIS_A0_TT_POINTS
from teglab.solver import build_model, max_power, solve

from .conftest import variant


def test_a0_tt_open_circuit(tt_design):
    s = solve(build_model(tt_design), 0.0)
    assert s.voltage_v == pytest.approx(TABLE_4_2["u_oc_v"], rel=2e-3)
    assert s.q_hot_w == pytest.approx(TABLE_4_2["q_hot_oc_w"], rel=2e-3)
    assert s.dt_te_k == pytest.approx(TABLE_4_2["dt_te_k"], rel=2e-3)
    assert s.dt_passive_hot_k == pytest.approx(TABLE_4_3["dt_hot_passive_k"], abs=0.02)
    assert s.dt_passive_cold_k == pytest.approx(TABLE_4_3["dt_cold_passive_k"], abs=0.02)


def test_a0_tt_load_points_tab_4_4(tt_design):
    m = build_model(tt_design)
    t = THESIS_A0_TT_POINTS
    for i, qh, qc, p in zip(t["current_a"], t["q_hot_w"], t["q_cold_w"], t["power_w"], strict=True):
        s = solve(m, i)
        assert s.q_hot_w == pytest.approx(qh, rel=1.5e-3)
        assert s.q_cold_w == pytest.approx(qc, rel=1.5e-3)
        assert s.power_w == pytest.approx(p, abs=0.05e-3)


def test_a0_tt_max_power(tt_design):
    mp = max_power(build_model(tt_design))
    assert mp.power_w == pytest.approx(TABLE_4_2["p_max_w"], rel=3e-3)
    assert mp.current_a == pytest.approx(TABLE_4_2["i_opt_a"], rel=5e-3)


def test_regression_slope_contains_thermal_emf_drop(tt_design):
    """Table 4.2 R_int = 17.26 mOhm is the U(I) slope; the electrical resistance is lower."""
    import numpy as np

    m = build_model(tt_design)
    currents = np.array([0, 0.4, 0.8, 1.2, 1.6, 1.8])
    u = np.array([solve(m, i).voltage_v for i in currents])
    slope = -np.polyfit(currents, u, 1)[0]
    r_el = solve(m, 0).r_int_ohm
    assert slope == pytest.approx(TABLE_4_2["r_int_ohm"], rel=3e-3)
    assert r_el == pytest.approx(16.47e-3, rel=5e-3)  # 14.29 legs + 2.04 contacts + ~0.15 Cu bridge


def test_calibration_recovers_reference_factors(a0tt_case, ref_design):
    d = ref_design.model_copy(deep=True)
    d.passive.hot_side_factor = d.passive.cold_side_factor = 1.0
    res = calibrate(d, [a0tt_case], ["hot_side_factor", "cold_side_factor", "r_el_extra_mohm"])
    assert res["success"]
    assert res["rms_after"] < 5e-4
    assert res["params"]["hot_side_factor"] == pytest.approx(1.479, rel=0.01)
    assert res["params"]["cold_side_factor"] == pytest.approx(0.935, rel=0.01)
    assert abs(res["params"]["r_el_extra_mohm"]) < 0.05


def test_a0_qt_prediction(ref_design):
    s = solve(build_model(variant(ref_design, "A0")), 0.0)
    t = TABLE_5_4["A0"]
    assert s.t_hot_ext_c == pytest.approx(t["t_hot_ext_c"], abs=0.2)
    assert s.voltage_v == pytest.approx(t["u_oc_v"], rel=2e-3)
    assert solve(build_model(variant(ref_design, "A0")), 1.6).t_max_c == pytest.approx(T_MAX_AT_1_6A_C["A0"], abs=0.3)
    mp = max_power(build_model(variant(ref_design, "A0")))
    assert mp.power_w == pytest.approx(t["p_max_w"], rel=0.01)  # thesis: 3-point parabola
    assert mp.efficiency == pytest.approx(t["eta"], abs=0.0005)


@pytest.mark.parametrize("name", ["A5", "A10"])
def test_tapered_variants_match_only_with_ideal_contacts(ref_design, name):
    t = TABLE_5_4[name]
    d_off = variant(ref_design, name)
    d_off.contacts.active = False
    m_off = build_model(d_off)
    oc = solve(m_off, 0.0)
    assert oc.voltage_v == pytest.approx(t["u_oc_v"], rel=2e-3)
    assert oc.dt_te_k == pytest.approx(t["dt_te_k"], rel=2e-3)
    assert oc.q_hot_w / oc.dt_te_k == pytest.approx(t["k_pair_w_k"], rel=2e-3)  # c3d = 0.5
    assert oc.t_hot_ext_c == pytest.approx(t["t_hot_ext_c"], abs=0.5)
    assert solve(m_off, 1.6).t_max_c == pytest.approx(T_MAX_AT_1_6A_C[name], abs=0.3)
    assert max_power(m_off).power_w == pytest.approx(t["p_max_w"], rel=0.01)
    # with the contacts that A0 has, the hot face would be clearly hotter
    oc_on = solve(build_model(variant(ref_design, name)), 0.0)
    assert oc_on.t_hot_ext_c - t["t_hot_ext_c"] > 1.5


def test_a10_at_0p8a_tab_3_5(ref_design):
    d = variant(ref_design, "A10")
    d.contacts.active = False
    s = solve(build_model(d), TABLE_3_5["current_a"])
    c1 = TABLE_3_5["C1"]
    assert s.voltage_v == pytest.approx(c1["voltage_v"], rel=0.01)
    assert s.power_w == pytest.approx(c1["power_w"], rel=0.01)
    assert s.q_cold_w == pytest.approx(c1["q_cold_w"], rel=2e-3)
    assert s.dt_te_k == pytest.approx(c1["dt_te_k"], abs=0.1)
    assert s.t_leg_cold_c[0] == pytest.approx(c1["t_leg_cold_c"], abs=0.1)
    # implied internal resistance of the thesis data is that of ideal contacts
    r_implied = (4e-4 * c1["dt_te_k"] - c1["voltage_v"]) / TABLE_3_5["current_a"]
    r_ideal = solve(build_model(d), 0).r_int_ohm
    r_contacts = solve(build_model(variant(ref_design, "A10")), 0).r_int_ohm
    assert abs(r_implied - r_ideal) < 0.1 * abs(r_implied - r_contacts)
