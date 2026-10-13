import math

import pytest

from teglab.analytic import eta_max_ideal, ideal_pair, thesis_table_5_2
from teglab.geometry import (
    a_hot_from_angle,
    constant_volume_sides,
    frustum_volume,
    geometric_factor,
    k_ratio_constant_volume,
)


@pytest.mark.parametrize("theta, a_h", [(5, 1.155), (10, 0.906), (15, 0.650)])
def test_hot_base_of_thesis_variants(theta, a_h):
    # thesis Section 5.2: a_c - a_h = 2 H tan(theta), H = a_c = 1.4 mm
    assert a_hot_from_angle(1.4, 1.4, theta) == pytest.approx(a_h, abs=6e-4)


def test_frustum_volume_and_factor():
    assert frustum_volume(1.4, 1.4, 1.4) == pytest.approx(2.744)
    assert frustum_volume(0.906, 1.4, 1.4) == pytest.approx(1.890, abs=2e-3)
    # prism: integral dx/A = H/A
    assert geometric_factor(1.4e-3, 1.4e-3, 1.4e-3) == pytest.approx(1.4e-3 / 1.96e-6)


def test_constant_volume_relations():
    v = frustum_volume(1.4, 1.4, 1.4)
    for r in (0.4, 0.7, 1.0, 1.3):
        a_h, a_c = constant_volume_sides(v, 1.4, r)
        assert frustum_volume(a_h, a_c, 1.4) == pytest.approx(v)
        assert a_h / a_c == pytest.approx(r)
    assert k_ratio_constant_volume(1.0) == pytest.approx(1.0)
    assert k_ratio_constant_volume(0.5) < 1.0


def test_analytic_reference_pair_eq_3_33_to_3_36():
    p = ideal_pair(1.4, 1.4, 1.4)
    assert p.r_pair_ohm == pytest.approx(0.0143, rel=2e-3)
    assert p.k_pair_w_k == pytest.approx(0.0042, rel=1e-3)
    tt = p.tt(80.0)
    assert tt["u_oc_v"] == pytest.approx(0.032)
    assert tt["p_max_w"] == pytest.approx(17.9e-3, rel=2e-3)


def test_thesis_table_5_2():
    rows = {r["variant"]: r for r in thesis_table_5_2()}
    expected = {  # K [mW/K], R [mOhm], dT_TE [K], P_max [mW]
        "A0": (4.200, 14.29, 75.05, 15.77),
        "A5": (3.465, 17.32, 90.97, 19.11),
        "A10": (2.719, 22.07, 115.92, 24.36),
        "A15": (1.949, 30.78, 161.7, 33.98),
    }
    for v, (k, r, dt, p) in expected.items():
        assert rows[v]["k_pair_mw_k"] == pytest.approx(k, rel=2e-3)
        assert rows[v]["r_pair_mohm"] == pytest.approx(r, rel=2e-3)
        assert rows[v]["dt_te_k"] == pytest.approx(dt, rel=2e-3)
        assert rows[v]["p_max_mw"] == pytest.approx(p, rel=3e-3)


def test_kr_product_is_rho_kappa():
    # thesis eq. 3.20: tapering does not change K*R
    for th in (0, 5, 10):
        p = ideal_pair(a_hot_from_angle(1.4, 1.4, th), 1.4, 1.4)
        assert p.k_pair_w_k * p.r_pair_ohm == pytest.approx(4 * 1e-5 * 1.5)


def test_eta_max_ideal_limits():
    assert eta_max_ideal(0.0, 373.15, 293.15) == 0.0
    carnot = 1 - 293.15 / 373.15
    assert 0 < eta_max_ideal(1.0, 373.15, 293.15) < carnot
    assert eta_max_ideal(1e6, 373.15, 293.15) == pytest.approx(carnot, rel=2e-3)
    assert math.isclose(eta_max_ideal(1.0, 300, 300), 0.0)
