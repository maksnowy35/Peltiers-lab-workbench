"""Analytic maximum-power references and the variant comparison (thesis Tables 5.2 and 5.3)."""

from __future__ import annotations

import numpy as np
import pytest

from teglab.design import reference_design
from teglab.pipeline.maxpower import p_max_frozen, p_max_qt_exact, p_max_tt, qt_state
from teglab.pipeline.run import RunOptions, run_pipeline
from teglab.solver import build_model, max_power

from .conftest import FULL_LOAD_D

ALPHA = 400e-6
Q = 0.3152
TC = 293.15
K = np.array([4.200e-3, 3.465e-3, 2.719e-3, 1.949e-3])  # Table 5.2, A0 / A5 / A10 / A15
R = np.array([14.29e-3, 17.32e-3, 22.07e-3, 30.78e-3])


def test_frozen_formula_reproduces_table_5_2():
    assert p_max_frozen(ALPHA, Q, K, R) * 1e3 == pytest.approx([15.77, 19.11, 24.36, 33.98], abs=0.01)


def test_tt_formula_reproduces_the_analytic_column_of_table_4_2():
    assert p_max_tt(ALPHA, 80.0, 14.29e-3) == pytest.approx(17.9e-3, abs=0.05e-3)


def test_exact_qt_optimum_matches_a_brute_force_scan():
    p, i, dt = p_max_qt_exact(ALPHA, Q, K, R, TC)
    for k, r, p_k, i_k in zip(K, R, p, i, strict=True):
        grid = np.linspace(0.0, ALPHA * Q / (k * r), 400_001)
        pw = qt_state(grid, ALPHA, Q, k, r, TC)[2]
        assert p_k == pytest.approx(pw.max(), rel=1e-9)
        assert i_k == pytest.approx(grid[pw.argmax()], abs=1e-5)
    assert np.all(p < p_max_frozen(ALPHA, Q, K, R))  # the frozen relation is an upper bound
    assert dt[0] < Q / K[0]  # ΔT collapses under current


def test_exact_qt_tends_to_frozen_for_vanishing_figure_of_merit():
    tiny = 1e-7
    p, _, _ = p_max_qt_exact(tiny, Q, K[0], R[0], TC)
    assert p == pytest.approx(p_max_frozen(tiny, Q, K[0], R[0]), rel=1e-6)


def test_exact_qt_agrees_with_the_1d_finite_volume_model():
    d = reference_design()  # BC-QT, 0.3152 W / 20 °C, constant thesis properties
    d.contacts.active = False
    d.passive.ceramic_k = d.passive.cu_k = 1e9  # thermally transparent passives ...
    d.passive.cu_rho = 1e-15  # ... and a resistance-free copper path: legs only
    d.c3d = 0.0
    fv = max_power(build_model(d)).power_w
    exact, _, _ = p_max_qt_exact(ALPHA, Q, K[0], R[0], TC)
    assert fv == pytest.approx(float(exact), rel=2e-3)  # two independent implementations


def test_variant_comparison_and_gain_attribution(profile, aseries, fig310):
    report = run_pipeline(
        [aseries, fig310],
        RunOptions(profile=profile, conditions={"D": FULL_LOAD_D}, variants={"A": "A0", "B": "A5", "C": "A10"}),
    ).report
    rows = {c.system: c for c in report.comparison}
    # Table 5.3: K −17.5 % / −35.3 %, R +21.2 % / +54.4 %, P_max (analytic) +21.2 % / +54.4 %
    assert rows["B"].r_ratio == pytest.approx(1.212, abs=0.001)
    assert rows["C"].r_ratio == pytest.approx(1.545, abs=0.001)
    assert rows["B"].p_frozen_ratio == pytest.approx(1.212, abs=0.001)
    assert rows["C"].p_frozen_ratio == pytest.approx(1.545, abs=0.001)
    for row in (rows["B"], rows["C"]):
        assert np.prod(list(row.attribution.values())) == pytest.approx(row.p_frozen_ratio)
        assert row.p_tt_ratio == pytest.approx(1.0 / row.r_ratio)  # at fixed ΔT the taper only adds resistance
        assert 1.0 < row.p_exact_ratio < row.p_frozen_ratio  # the gain shrinks once ΔT collapses
    assert rows["C"].k_ratio == pytest.approx(2.676 / 4.196, abs=0.001)  # measured K (Table 5.4)
    assert rows["C"].volume_ratio == pytest.approx(1.890 / 2.744, abs=0.001)  # Table 5.2 leg volumes


def test_cold_side_signature_flags_the_tapered_systems(profile, aseries):
    report = run_pipeline([aseries], RunOptions(profile=profile, variants={"A": "A0", "B": "A5", "C": "A10"})).report
    flags = {i.system: i for i in report.issues if i.code == "contact-signature-cold"}
    assert set(flags) == {"B", "C"}  # 22.09 °C vs 21.34 / 21.33 °C (Section 5.3) ≈ the 0.80 K cold-contact drop
    assert "0.80 K" in flags["C"].message
