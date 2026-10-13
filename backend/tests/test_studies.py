import pytest

from teglab.ansys_export import design_point_rows, design_points_csv, workbench_journal
from teglab.ansys_io import parse_files
from teglab.lab import LabPlanRequest, plan_experiment
from teglab.materials_study import efficiency_map, segmented_study, thomson_study
from teglab.optimize import Constraints, geometry_map, geometry_sweep


def test_qt_taper_increases_power_until_a_limit(ref_design):
    res = geometry_sweep(ref_design, "fixed_cold_base", [0, 5, 10], constraints=Constraints(a_min_mm=0.3))
    p = [pt["p_max_w"] for pt in res["series"][0]["points"]]
    assert p[0] < p[1] < p[2]  # thesis tendency (BC-QT)
    assert res["optimum"]["theta_deg"] == pytest.approx(10.0)


def test_tt_taper_decreases_power(tt_design):
    res = geometry_sweep(tt_design, "fixed_cold_base", [0, 5, 10])
    p = [pt["p_max_w"] for pt in res["series"][0]["points"]]
    assert p[0] > p[1] > p[2]  # fixed temperatures: only the resistance grows


def test_constraints_mark_infeasible(ref_design):
    res = geometry_sweep(ref_design, "fixed_cold_base", [20], constraints=Constraints(a_min_mm=0.5))
    pt = res["series"][0]["points"][0]
    assert not pt["feasible"] and any("leg side" in v for v in pt["violations"])


def test_constant_volume_keeps_volume(ref_design):
    res = geometry_sweep(ref_design, "constant_volume", [0.6, 0.8, 1.0, 1.2])
    vols = [pt["te_volume_mm3"] for pt in res["series"][0]["points"]]
    assert max(vols) - min(vols) < 1e-9
    assert len(res["pareto"]) >= 1


def test_hx_boundary_has_interior_optimum(ref_design):
    d = ref_design.model_copy(deep=True)
    d.boundary.hot_mode, d.boundary.cold_mode = "HX", "HX"
    d.boundary.t_source_c, d.boundary.r_source_k_w = 180.0, 400.0
    d.boundary.t_sink_c, d.boundary.r_sink_k_w = 20.0, 30.0
    res = geometry_sweep(d, "fixed_cold_base", [0, 4, 8, 12, 16, 20], constraints=Constraints(a_min_mm=0.0))
    p = [pt["p_max_w"] for pt in res["series"][0]["points"]]
    k = p.index(max(p))
    assert 0 < k < len(p) - 1  # thermal impedance matching


def test_geometry_map_shapes(ref_design):
    m = geometry_map(ref_design, "fixed_cold_base", [0, 5, 10], [1.0, 1.4])
    assert len(m["z"]) == 2 and len(m["z"][0]) == 3


def test_efficiency_map_orders_materials(lib):
    res = efficiency_map([("bi2te3_p", "bi2te3_n")], 30.0, [100.0, 200.0])
    pts = res["series"][0]["points"]
    assert 0 < pts[0]["eta_max"] < pts[1]["eta_max"] < pts[1]["carnot"]


def test_segmented_leg_beats_single_material_at_high_t(lib):
    res = segmented_study("skd_p", "bi2te3_p", "skd_n", "bi2te3_n", 500, 30, [0.0, 0.5, 0.7, 1.0])
    rows = {r["fraction_hot"]: r for r in res["rows"]}
    assert not rows[0.0]["feasible"]  # Bi2Te3 alone would exceed its limit
    assert rows[0.7]["eta_max"] > rows[1.0]["eta_max"]
    assert res["optimum"]["fraction_hot"] in (0.5, 0.7)


def test_thomson_study_constant_properties_is_neutral(ref_design):
    t = thomson_study(ref_design)
    assert t["delta_p_rel"] == pytest.approx(0.0, abs=1e-9)


def test_thomson_study_t_dependent(tt_design):
    from teglab.design import Segment

    d = tt_design.model_copy(deep=True)
    d.boundary.t_hot_c = 200
    d.p_leg.segments = [Segment(material="bi2te3_p")]
    d.n_leg.segments = [Segment(material="bi2te3_n")]
    t = thomson_study(d)
    assert t["with_thomson"]["u_oc_v"] == pytest.approx(t["without_thomson"]["u_oc_v"], rel=2e-3)
    assert abs(t["delta_p_rel"]) < 0.2


def test_lab_plan_quantities(ref_design):
    plan = plan_experiment(LabPlanRequest(design=ref_design, n_pairs=4, solder_id="snpb60", joint_thickness_um=50))
    s = plan["solder"]
    # 4 pairs x 2 legs x 2 ends x 1.96 mm2
    assert s["joint_area_mm2"] == pytest.approx(4 * 2 * 2 * 1.96, rel=1e-3)
    assert s["volume_mm3"] == pytest.approx(31.36 * 0.05 * 1.3, rel=1e-3)
    assert plan["build"]["electrodes"]["count"] == 9
    assert plan["expected"]["module"]["u_oc_v"] == pytest.approx(4 * plan["expected"]["per_pair"]["u_oc_v"])
    assert plan["electrical"]["current_setpoints_a"][0] == 0.0
    assert "Run sheet" in plan["run_sheet_md"]


def test_lab_template_imports_back(ref_design):
    plan = plan_experiment(LabPlanRequest(design=ref_design))
    lines = plan["measurement_template_csv"].splitlines()
    # simulate a filled-in template: measured values in the empty columns
    filled = [
        ln if ln.startswith("#") or ln.startswith("current") else ln.replace(",,,,,", ",0.01,99.5,20.2,0.3,", 1)
        for ln in lines
    ]
    t = parse_files([("measurement.csv", "\n".join(filled).encode())])
    roles = {c.name: c.role for c in t.columns}
    assert roles["current"] == "current"
    assert roles["voltage"] == "voltage"
    assert roles["t_hot_ext"] == "t_hot_ext"
    assert roles["q_hot (heater)"] == "q_hot"


def test_solder_check_flags_low_melting_alloy(ref_design):
    d = ref_design.model_copy(deep=True)
    plan = plan_experiment(LabPlanRequest(design=d, solder_id="bisn58", test_mode="TT", t_hot_test_c=130))
    assert not plan["solder"]["ok"]


def test_ansys_export_roundtrip():
    rows = design_point_rows([{"name": "A10", "a_hot_mm": 0.906, "a_cold_mm": 1.4, "height_mm": 1.4}], [0, 0.5])
    csv_text = design_points_csv(rows)
    t = parse_files([("dp.csv", csv_text.encode())])
    assert t.n_rows == 2
    j = workbench_journal(rows)
    assert "Parameters.CreateDesignPoint" in j and "SetParameterExpression" in j
    compile(j.replace("Parameters.", "P_."), "journal", "exec")  # valid Python syntax
