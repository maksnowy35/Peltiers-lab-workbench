"""Nodal datasets: synthetic physics, ingestion, stack inference, contact checks and view projection."""

from __future__ import annotations

import io
import json
import zipfile

import numpy as np
import pandas as pd
import pytest

from teglab.pipeline import synthetic
from teglab.pipeline.nodal import (
    DatasetStore,
    build_dataset,
    demo_archive,
    demo_dataset,
    evaluate_profile,
    infer_stack,
    project_points,
)
from teglab.pipeline.overlay import placement
from teglab.pipeline.schemas import ContactProfile, LayerStack, PointsRequest, ProfileRequest, StackLayer
from teglab.units import conversion

from .conftest import body_nodes, mechanical_export

EXPECTED = ContactProfile(tcc_w_m2k=1e5, ecc_s_m2=1e9)


def _zip(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


# ----------------------------------------------------------------------------------- synthetic cell
def test_synthetic_cell_obeys_teg_theory():
    p = synthetic.CellParameters()
    k = 2 * p.kappa_te_w_mk * synthetic.LEG_A_M2 / synthetic.LEG_L_M
    oc = synthetic.solve(synthetic.CellParameters(current_a=0.0))
    assert oc.dt_te_k == pytest.approx(p.q_hot_w / k)  # all heat through the legs at I = 0
    assert oc.voltage_v == pytest.approx(p.alpha_pn_v_k * oc.dt_te_k)
    sol = synthetic.solve(p)
    r_int = 2 * p.rho_te_ohm_m * synthetic.LEG_L_M / synthetic.LEG_A_M2 + 4 / (p.ecc_s_m2 * synthetic.LEG_A_M2)
    assert r_int == pytest.approx(0.01429 + 0.00204, rel=1e-3)  # legs + the thesis 2.04 mΩ of contacts
    assert sol.voltage_v == pytest.approx(p.alpha_pn_v_k * sol.dt_te_k - p.current_a * r_int)
    assert p.q_hot_w - sol.q_out_w == pytest.approx(sol.power_w, abs=1e-15)  # energy balance closes
    assert sol.dt_contact_hot_k == pytest.approx(p.q_hot_w / (p.tcc_w_m2k * 2 * synthetic.LEG_A_M2))


def test_synthetic_png_is_calibrated():
    png, cal = synthetic.render_front(synthetic.solve(), width=600, height=400)
    assert png.startswith(b"\x89PNG") and (cal.width_px, cal.height_px) == (600, 400)
    p = placement(cal)
    sx, sy = p.mm_per_px
    assert sx == pytest.approx(-sy)  # square pixels
    assert p.x + p.sizex / 2 == pytest.approx(1.9)  # the cell (x −0.3 … 4.1 mm) is centered
    assert p.y - p.sizey / 2 == pytest.approx(1.7)


# ------------------------------------------------------------------------------ end-to-end archive
@pytest.fixture(scope="module")
def applied():
    return demo_dataset(contacts_applied=True)


def test_demo_archive_round_trip_recovers_the_contact_conductances(applied):
    sol = synthetic.solve()
    info = applied.info()
    assert info.synthetic and len(info.bodies) == 7 and len(info.images) == 2
    assert [layer.name for layer in info.proposed_stack.layers] == [
        "ceramic hot",
        "Cu hot",
        "legs",
        "Cu cold",
        "ceramic cold",
    ]
    prof = evaluate_profile(applied, ProfileRequest(), EXPECTED)
    assert prof.split.dt_te_k == pytest.approx(sol.dt_te_k, abs=1e-5)
    total = sol.t_hot_face_c - sol.params.t_cold_c
    assert prof.split.dt_total_k == pytest.approx(total, abs=1e-5)
    assert prof.split.dt_contacts_k == pytest.approx(sol.dt_contact_hot_k + sol.dt_contact_cold_k, abs=1e-5)
    legs = [c for c in prof.contacts if c.at_active_layer]
    assert len(legs) == 4
    for c in legs:
        assert c.tcc_eff_w_m2k == pytest.approx(1e5, rel=1e-6)
        assert c.ecc_eff_s_m2 == pytest.approx(1e9, rel=1e-6)
        assert c.current_density_a_m2 == pytest.approx(sol.current_density_a_m2, rel=1e-8)
        assert (c.thermal_verdict, c.electrical_verdict) == ("as-specified", "as-specified")
    hot = next(c for c in legs if c.hot_body == "cu_hot")
    assert hot.heat_flux_w_m2 == pytest.approx(sol.params.q_hot_w / (2 * synthetic.LEG_A_M2), rel=1e-8)
    passive = [c for c in prof.contacts if not c.at_active_layer]
    assert all(c.thermal_verdict == "bonded" and c.electrical_verdict == "not-evaluated" for c in passive)
    assert all(c.dt_k == 0.0 and c.tcc_eff_w_m2k is None for c in passive)  # round-off reads as zero
    assert [r.dt_k for r in prof.rows if r.name == "ceramic hot | Cu hot"] == [0.0]
    assert not [i for i in prof.issues if i.code.startswith("contact-")]


def test_bonded_contacts_are_flagged():
    prof = evaluate_profile(demo_dataset(contacts_applied=False), ProfileRequest(), EXPECTED)
    legs = [c for c in prof.contacts if c.at_active_layer]
    assert {(c.thermal_verdict, c.electrical_verdict) for c in legs} == {("bonded", "bonded")}
    codes = [i.code for i in prof.issues]
    assert codes.count("contact-thermal-bonded") == 4 and codes.count("contact-electrical-bonded") == 4


def test_deviating_conductance_is_quantified():
    params = synthetic.CellParameters(tcc_w_m2k=2.5e4)  # four times less than specified
    ds = build_dataset([("x.zip", synthetic.demo_zip(params, spacing_mm=0.2))])
    prof = evaluate_profile(ds, ProfileRequest(), EXPECTED)
    legs = [c for c in prof.contacts if c.at_active_layer]
    assert all(c.thermal_verdict == "deviates" for c in legs)
    assert legs[0].tcc_eff_w_m2k == pytest.approx(2.5e4, rel=1e-6)
    assert any("0.25×" in i.message for i in prof.issues)


def test_custom_stack_and_bad_stacks(applied):
    stack = LayerStack(
        axis="y",
        layers=[
            StackLayer(name="Cu hot", bodies=["cu_hot"]),
            StackLayer(name="legs", bodies=["leg_p", "leg_n"], active=True),
            StackLayer(name="Cu cold", bodies=["cu_cold_p", "cu_cold_n"]),
        ],
    )
    prof = evaluate_profile(applied, ProfileRequest(stack=stack), EXPECTED)
    assert prof.rows[0].s_hot_mm == 0.0 and prof.rows[-1].s_cold_mm == pytest.approx(1.8)
    assert any(i.code == "unused-bodies" for i in prof.issues)
    unknown = LayerStack(axis="y", layers=[StackLayer(name="x", bodies=["nope"])])
    with pytest.raises(ValueError, match="unknown bodies"):
        evaluate_profile(applied, ProfileRequest(stack=unknown), EXPECTED)
    with pytest.raises(ValueError, match="more than one layer"):
        LayerStack(axis="y", layers=[StackLayer(name="a", bodies=["b"]), StackLayer(name="c", bodies=["b"])])


# ------------------------------------------------------------------------------------ stack inference
def test_stack_inference_orients_hot_first(nodal_bodies):
    stack, _ = infer_stack(nodal_bodies)
    assert stack.axis == "y" and stack.layers[0].bodies == ["ceramic_hot"] and stack.layers[2].active
    flipped = {t: f.assign(y_mm=-f["y_mm"]) for t, f in nodal_bodies.items()}  # hot face now at the bottom
    assert infer_stack(flipped)[0].layers[0].bodies == ["ceramic_hot"]
    swapped = {t: f.rename(columns={"y_mm": "z_mm", "z_mm": "y_mm"}) for t, f in nodal_bodies.items()}
    assert infer_stack(swapped)[0].axis == "z"


def test_stack_inference_without_temperature(nodal_bodies):
    geometry_only = {t: f.drop(columns="temperature_c") for t, f in nodal_bodies.items()}
    stack, issues = infer_stack(geometry_only)
    assert stack.layers[0].bodies == ["ceramic_hot"] and stack.layers[2].name == "legs" and stack.layers[2].active
    assert [i.code for i in issues] == ["orientation-assumed"]


# ------------------------------------------------------------------------------------------ ingestion
def test_ingestion_merges_files_that_share_nodes():
    leg = body_nodes("leg_p")
    t = mechanical_export(leg)
    v = t.decode("cp1252").replace("Temperature (°C)", "Electric Voltage (V)").encode("cp1252")
    cu = mechanical_export(body_nodes("cu_hot"))
    ds = build_dataset([("T.txt", t), ("V.txt", v), ("cu_hot__temperature.txt", cu)])
    assert sorted(ds.bodies) == ["cu_hot", "t"]
    assert {"temperature_c", "voltage_v"} <= set(ds.bodies["t"].columns)
    assert any(i.code == "merged" for i in ds.issues)
    assert {f.body for f in ds.files} == {"t", "cu_hot"}


def test_ingestion_rejects_mixed_meshes_and_non_nodal_files():
    a = body_nodes("leg_p")
    b = a.assign(x_mm=a["x_mm"] + 5.0)  # same node numbers, different place
    ds = build_dataset(
        [
            ("leg__temperature.txt", mechanical_export(a)),
            ("leg__t2.txt", mechanical_export(b)),
            ("notes.csv", b"a,b\n1,2\n"),
        ]
    )
    codes = [i.code for i in ds.issues]
    assert "node-mismatch" in codes and "skipped" in codes
    with pytest.raises(ValueError, match="no Mechanical nodal export"):
        build_dataset([("notes.csv", b"a,b\n1,2\n")])


def test_archives_are_bounded():
    manifest = json.dumps({"files": []}).encode()
    with pytest.raises(ValueError, match="2 manifests"):
        build_dataset([("two.zip", _zip({"a/manifest.json": manifest, "b/manifest.json": manifest}))])
    with pytest.raises(ValueError, match="not a valid zip"):
        build_dataset([("broken.zip", b"PK\x03\x04garbage")])


def test_manifest_assigns_bodies_and_checks_calibrated_images():
    frame = body_nodes("leg_p")
    png, cal = synthetic.render_front(synthetic.solve(), width=300, height=200)
    manifest = {
        "generator": "test",
        "system": "Dense mesh copy C 10st",
        "unknown_key": "ignored",
        "files": [{"file": "export_1.txt", "body": "Leg P", "quantity": "temperature_c"}],
        "images": [
            {"file": "front.png", "view": "front", "calibration": cal.model_dump(mode="json")},
            {"file": "wrong.png", "calibration": {**cal.model_dump(mode="json"), "width_px": 999}},
        ],
    }
    ds = build_dataset(
        [
            ("export_1.txt", mechanical_export(frame)),
            ("manifest.json", json.dumps(manifest).encode()),
            ("front.png", png),
            ("wrong.png", png),
        ]
    )
    assert list(ds.bodies) == ["leg_p"] and ds.title == "Dense mesh copy C 10st"
    assert ds.images["front.png"].info.calibration_source == "camera"
    assert ds.images["wrong.png"].info.calibration is None
    assert any(i.code == "calibration-size" for i in ds.issues)


def test_utf16_exports_are_read():
    text = mechanical_export(body_nodes("leg_p")).decode("cp1252")
    ds = build_dataset([("leg_p__temperature.txt", text.encode("utf-16"))])
    assert ds.bodies["leg_p"]["temperature_c"].notna().all()


def test_mechanical_unit_systems():
    assert conversion("mA/mm²", "current_density")[0] == pytest.approx(1e3)
    assert conversion("mW/mm²", "heat_flux")[0] == pytest.approx(1e3)
    assert conversion("mW/mm³", "volumetric_heat")[0] == pytest.approx(1e6)
    assert conversion("MA/m²", "current_density")[2] is not None  # ambiguous after case folding: not guessed


# ----------------------------------------------------------------------------------- view projection
def test_front_view_shows_the_nearest_faces(applied):
    pts = project_points(applied, PointsRequest(view="front"))
    assert pts.h_label == "x [mm]" and pts.v_label == "y [mm]" and pts.unit == "°C"
    assert pts.h_range_mm == pytest.approx((-0.3, 4.1)) and pts.v_range_mm == pytest.approx((0.0, 3.4))
    by_body = pd.DataFrame({"body": pts.body_index, "h": pts.h_mm, "v": pts.v_mm})
    assert by_body["body"].nunique() == 7
    assert pts.n_returned == pts.n_total  # one face per body: well below the default limit
    sol = synthetic.solve()
    assert pts.value_range == pytest.approx((sol.params.t_cold_c, sol.t_hot_face_c), abs=1e-4)


def test_other_views_and_limits(applied):
    top = project_points(applied, PointsRequest(view="top", bodies=["leg_p", "leg_n"], surface_only=False))
    assert top.v_label == "−z [mm]" and top.v_range_mm == pytest.approx((-1.4, 0.0))
    assert top.n_total == 2 * 3375
    few = project_points(applied, PointsRequest(view="right", max_points=500, surface_only=False))
    assert few.n_returned == 500 and few.n_total > 500 and few.h_label == "−z [mm]"
    volts = project_points(applied, PointsRequest(quantity="voltage_v"))
    assert "ceramic_hot" in volts.bodies and 0 not in volts.body_index  # ceramics have no voltage
    with pytest.raises(ValueError, match="unknown bodies"):
        project_points(applied, PointsRequest(bodies=["nope"]))
    with pytest.raises(ValueError, match="none of the selected bodies"):
        project_points(applied, PointsRequest(bodies=["ceramic_hot"], quantity="voltage_v"))


def test_dataset_store_is_bounded():
    store = DatasetStore(max_items=2, ttl_s=3600)
    ds = [build_dataset([(f"b{k}__temperature.txt", mechanical_export(body_nodes("leg_p")))]) for k in range(3)]
    for d in ds:
        store.put(d)
    assert len(store) == 2
    with pytest.raises(KeyError):
        store.get(ds[0].dataset_id)
    assert store.get(ds[2].dataset_id) is ds[2]
    assert store.delete(ds[2].dataset_id) and not store.delete(ds[2].dataset_id)
    expired = DatasetStore(ttl_s=-1.0)
    expired.put(ds[1])
    with pytest.raises(KeyError):
        expired.get(ds[1].dataset_id)


def test_demo_archive_is_what_the_export_script_writes():
    with zipfile.ZipFile(io.BytesIO(demo_archive(True))) as zf:
        names = zf.namelist()
        manifest = json.loads(zf.read("teglab_export/manifest.json"))
    assert "teglab_export/leg_p__temperature.txt" in names and "teglab_export/front__temperature.png" in names
    assert {f["body"] for f in manifest["files"]} == set(synthetic.CELL)
    assert np.isclose(manifest["images"][0]["calibration"]["p1_model"][0], -0.3)
