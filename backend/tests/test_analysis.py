import numpy as np
import pytest

from teglab.analysis import analyze_case, compare_cases, gci, load_curve
from teglab.reference import TABLE_3_5, TABLE_4_2


def test_load_curve_linear_tt():
    i = np.array([0, 0.4, 0.8, 1.2, 1.6, 1.8])
    u = 30.02e-3 - 17.26e-3 * i
    lc = load_curve(i, u, bc="TT")
    assert lc["linear"]["r_ohm"] == pytest.approx(17.26e-3)
    assert lc["best"]["method"] == "linear U(I)"
    assert lc["best"]["p_max_w"] == pytest.approx(TABLE_4_2["p_max_w"], rel=2e-3)
    assert lc["best"]["i_opt_a"] == pytest.approx(TABLE_4_2["i_opt_a"], rel=2e-3)


def test_analysis_of_thesis_a0_tt(a0tt_case, ref_design):
    a = analyze_case(a0tt_case, ref_design)
    m = a["metrics"]
    assert m["u_oc_v"] == pytest.approx(0.03002)
    assert m["k_pair_w_k"] == pytest.approx(4.196e-3, rel=2e-3)
    assert m["alpha_eff_v_k"] == pytest.approx(400e-6, rel=2e-3)
    checks = {c["id"]: c for c in a["checks"]}
    assert checks["energy_balance"]["level"] == "ok"
    assert checks["linearity"]["level"] == "ok"
    assert checks["hot_passive"]["level"] != "warn"


def test_contact_check_flags_a10(aseries_cases, ref_design):
    a10 = aseries_cases[2]
    checks = {c["id"]: c for c in analyze_case(a10, ref_design)["checks"]}
    assert checks["implied_rint"]["level"] == "warn"
    assert checks["implied_rint"]["value"] == pytest.approx(
        (4e-4 * TABLE_3_5["C1"]["dt_te_k"] - TABLE_3_5["C1"]["voltage_v"]) / 0.8, rel=1e-3
    )
    assert checks["hot_passive"]["level"] == "warn"


def test_compare_cases_relative_changes(aseries_cases, ref_design):
    res = [analyze_case(c, ref_design) for c in aseries_cases]
    cmp = compare_cases(res, 0)
    rows = {r["name"]: r for r in cmp["rows"]}
    assert rows["A10-QT"]["d_k_pair_w_k"] == pytest.approx(2.676 / 4.196 - 1, abs=0.005)
    assert rows["A10-QT"]["d_te_volume_mm3"] == pytest.approx(-0.311, abs=0.003)


def test_gci_reproduces_thesis_numbers():
    # thesis Section 3.5.3: dT_TE on C1/C2, r = 1.99, p = 2, Fs = 3 -> GCI ~0.04 %, Richardson 76.197 K
    g = gci(76.159, 76.187, 1.99)
    assert g["gci_fine"] == pytest.approx(3.7e-4, rel=0.05)
    assert g["f_extrapolated"] == pytest.approx(76.197, abs=1e-3)


def test_gci_three_grids_observed_order():
    f = lambda h: 10 + 0.5 * h**2  # noqa: E731  exact second-order convergence
    g = gci(f(0.4), f(0.1), 2.0, f_medium=f(0.2))
    assert g["p_observed"] == pytest.approx(2.0, rel=1e-6)
    assert g["f_extrapolated"] == pytest.approx(10.0, rel=1e-9)
