import numpy as np
import pytest

from teglab.design import Segment
from teglab.materials import Material
from teglab.solver import build_model, max_efficiency, max_power, solve, solve_for_load, sweep_current


def test_energy_conservation_all_boundary_conditions(ref_design):
    for hot, cold in (("T", "T"), ("Q", "T"), ("HX", "HX"), ("Q", "HX")):
        d = ref_design.model_copy(deep=True)
        d.boundary.hot_mode, d.boundary.cold_mode = hot, cold
        d.side_loss_h = 8.5 * 0.3  # thesis eq. 3.24 order of magnitude
        m = build_model(d)
        for i in (0.0, 0.3, 0.9):
            s = solve(m, i)
            assert s.energy_balance_rel < 1e-10, (hot, cold, i)


def test_open_circuit_has_no_current_effects(tt_design):
    s = solve(build_model(tt_design), 0.0)
    assert s.power_w == 0.0
    assert s.q_hot_w == pytest.approx(s.q_cold_w)
    assert s.voltage_v == pytest.approx(s.emf_v)


def test_constant_properties_tt_is_linear_in_current(tt_design):
    """With constant properties the only nonlinearity is the Peltier-driven change of dT (small)."""
    m = build_model(tt_design)
    currents = np.linspace(0, 1.6, 9)
    u = np.array([s.voltage_v for s in sweep_current(m, currents)])
    slope, icpt = np.polyfit(currents, u, 1)
    r2 = 1 - np.sum((u - (icpt + slope * currents)) ** 2) / np.sum((u - u.mean()) ** 2)
    assert r2 > 0.9999


def test_mesh_convergence(ref_design):
    vals = []
    for n in (10, 20, 40, 80):
        d = ref_design.model_copy(deep=True)
        d.n_elements = n
        vals.append(solve(build_model(d), 0.6).power_w)
    # constant properties: Joule lumping is exact for the element resistance -> tiny change
    assert abs(vals[-1] - vals[-2]) / vals[-1] < 1e-4


def test_max_power_matches_load_matching_in_tt(tt_design):
    m = build_model(tt_design)
    mp = max_power(m)
    s = solve_for_load(m, mp.load_resistance_ohm)
    assert s.current_a == pytest.approx(mp.current_a, rel=1e-4)
    me = max_efficiency(m)
    assert me.efficiency >= mp.efficiency - 1e-12
    assert me.current_a <= mp.current_a * 1.01


def test_temperature_dependent_materials_converge(ref_design):
    d = ref_design.model_copy(deep=True)
    d.p_leg.segments = [Segment(material="bi2te3_p")]
    d.n_leg.segments = [Segment(material="bi2te3_n")]
    m = build_model(d)
    s = solve(m, 0.5)
    assert s.converged
    assert s.energy_balance_rel < 1e-7


def test_seebeck_voltage_equals_alpha_integral(lib):
    """Open circuit EMF must equal the integral of alpha over each leg (Kelvin relations, Section 3.1.3)."""
    t = np.array([300.0, 400.0, 500.0])
    lin = Material(
        id="lin_p",
        name="linear alpha p",
        kind="p",
        family="test",
        t_k=t,
        alpha=np.array([150e-6, 200e-6, 250e-6]),
        rho=np.full(3, 1e-5),
        kappa=np.full(3, 1.5),
        t_limit_k=900,
        density_g_cm3=7,
    )
    lib.add(lin)
    from teglab.design import Boundary, Contacts, LegSpec, PairDesign, Passive

    d = PairDesign(
        p_leg=LegSpec(segments=[Segment(material="lin_p")]),
        n_leg=LegSpec(segments=[Segment(material="thesis_n")]),
        contacts=Contacts(active=False),
        passive=Passive(ceramic_thickness_mm=0, cu_thickness_mm=0),
        boundary=Boundary(hot_mode="T", t_hot_c=200, t_cold_c=40),
        n_elements=60,
    )
    s = solve(build_model(d, lib), 0.0)
    th, tc = s.t_leg_hot_c[0] + 273.15, s.t_leg_cold_c[0] + 273.15
    expected_p = lin.seebeck_integral(tc, th)
    expected_n = 200e-6 * (s.t_leg_hot_c[1] - s.t_leg_cold_c[1])
    assert s.emf_v == pytest.approx(expected_p + expected_n, rel=1e-4)


def test_thomson_heat_changes_heat_flow_but_not_balance(lib):
    """Linear alpha(T): Thomson heat appears in Q_h while energy is still conserved."""
    t = np.array([250.0, 600.0])
    lin = Material(
        id="lin2",
        name="lin",
        kind="p",
        family="t",
        t_k=t,
        alpha=np.array([100e-6, 300e-6]),
        rho=np.full(2, 1e-5),
        kappa=np.full(2, 1.5),
        t_limit_k=900,
        density_g_cm3=7,
    )
    lib.add(lin)
    from teglab.materials_study import pair_design

    d = pair_design([("lin2", 1.0)], [("thesis_n", 1.0)], 250, 30)
    s = solve(build_model(d, lib), 1.0)
    assert s.energy_balance_rel < 1e-7


def test_segmented_leg_interface_peltier_is_conservative(lib):
    from teglab.materials_study import pair_design

    d = pair_design([("skd_p", 0.6), ("bi2te3_p", 0.4)], [("skd_n", 0.6), ("bi2te3_n", 0.4)], 450, 30, height_mm=5)
    s = solve(build_model(d, lib), 1.0)
    assert s.converged and s.energy_balance_rel < 1e-7


def test_contacts_increase_resistance(ref_design):
    on = build_model(ref_design)
    d = ref_design.model_copy(deep=True)
    d.contacts.active = False
    off = build_model(d)
    r_on, r_off = solve(on, 0).r_int_ohm, solve(off, 0).r_int_ohm
    # 4 contacts of 1/(ECC*A) = 0.51 mOhm each (thesis Section 4.2: 2.04 mOhm)
    assert r_on - r_off == pytest.approx(2.04e-3, rel=1e-3)
