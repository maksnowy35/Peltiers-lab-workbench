"""REST endpoints of the pipeline: parameter runs, nodal datasets, the overlay placement and the examples."""

from __future__ import annotations

import json

import pytest

from .conftest import DATA

API = "/api/pipeline"


@pytest.fixture()
def demo(client):
    r = client.post(f"{API}/nodal/demo")
    assert r.status_code == 201
    return r.json()


def test_demo_dataset_life_cycle(client, demo):
    ds = demo["dataset_id"]
    assert len(ds) == 32 and demo["synthetic"] and demo["proposed_stack"]["axis"] == "y"
    assert {b["tag"] for b in demo["bodies"]} >= {"leg_p", "leg_n", "cu_hot"}
    assert client.get(f"{API}/nodal/{ds}").json() == demo

    image = demo["images"][0]
    r = client.get(image["url"])
    assert r.status_code == 200 and r.headers["content-type"] == "image/png" and r.content.startswith(b"\x89PNG")
    assert image["calibration_source"] == "exact"
    placement = client.post(f"{API}/overlay", json=image["calibration"]).json()
    assert placement["sizex"] > 4.4 and placement["layer"] == "below"  # the picture is wider than the cell

    prof = client.post(f"{API}/nodal/{ds}/profile", json={}).json()
    legs = [c for c in prof["contacts"] if c["at_active_layer"]]
    assert {c["thermal_verdict"] for c in legs} == {"as-specified"}
    assert prof["expected"] == {"tcc_w_m2k": 1e5, "ecc_s_m2": 1e9}  # from the bundled thesis profile
    assert prof["split"]["dt_te_k"] == pytest.approx(49.998, abs=1e-3)

    pts = client.post(f"{API}/nodal/{ds}/points", json={"view": "front", "quantity": "voltage_v"}).json()
    assert pts["unit"] == "V" and len(pts["h_mm"]) == len(pts["value"]) == pts["n_returned"]

    assert client.delete(f"{API}/nodal/{ds}").json() == {"deleted": True}
    assert client.get(f"{API}/nodal/{ds}").status_code == 404
    assert client.delete(f"{API}/nodal/{ds}").json() == {"deleted": False}


def test_upload_of_the_bonded_example_flags_the_contacts(client):
    examples = {e["name"]: e for e in client.get(f"{API}/examples").json()}
    assert examples["synthetic-cell_bonded.zip"]["kind"] == "nodal"
    archive = client.get(f"{API}/examples/synthetic-cell_bonded.zip")
    assert archive.headers["content-type"] == "application/zip"
    assert len(archive.content) == examples["synthetic-cell_bonded.zip"]["size_bytes"]
    r = client.post(f"{API}/nodal", files=[("files", ("bonded.zip", archive.content, "application/zip"))])
    assert r.status_code == 201
    assert r.json()["synthetic"] and {i["calibration_source"] for i in r.json()["images"]} == {"exact"}
    ds = r.json()["dataset_id"]
    expected = {"contacts": {"tcc_w_m2k": 1e5, "ecc_s_m2": 1e9}}
    prof = client.post(f"{API}/nodal/{ds}/profile", json=expected).json()
    codes = [i["code"] for i in prof["issues"]]
    assert codes.count("contact-thermal-bonded") == 4


def test_parameter_examples_run_through_the_pipeline(client):
    examples = [e for e in client.get(f"{API}/examples").json() if e["kind"] == "parameters"]
    assert len(examples) == 3
    files = []
    for e in examples:
        r = client.get(f"{API}/examples/{e['name']}")
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
        files.append(("files", (e["name"], r.content, "text/csv")))
    settings = {"variants": {"A": "A0", "B": "A5", "C": "A10"}}
    report = client.post(f"{API}/run", files=files[1:2], data={"settings": json.dumps(settings)}).json()
    assert [s["variant"] for s in report["systems"]] == ["A0", "A5", "A10"]


def test_nodal_endpoints_reject_bad_input(client, demo):
    ds = demo["dataset_id"]
    assert client.get(f"{API}/nodal/not-an-id").status_code == 422
    assert client.get(f"{API}/nodal/{'0' * 32}").status_code == 404
    assert client.get(f"{API}/nodal/{ds}/images/missing.png").status_code == 404
    assert client.get(f"{API}/examples/nope.csv").status_code == 404
    csv = [("files", ("params.csv", b"a,b\n1,2\n", "text/csv"))]
    r = client.post(f"{API}/nodal", files=csv)
    assert r.status_code == 400 and "no Mechanical nodal export" in r.json()["detail"]
    dup = {"stack": {"axis": "y", "layers": [{"name": "a", "bodies": ["x"]}, {"name": "b", "bodies": ["x"]}]}}
    assert client.post(f"{API}/nodal/{ds}/profile", json=dup).status_code == 422
    unknown = {"stack": {"axis": "y", "layers": [{"name": "a", "bodies": ["nope"]}]}}
    assert client.post(f"{API}/nodal/{ds}/profile", json=unknown).status_code == 400
    assert client.post(f"{API}/nodal/{ds}/points", json={"view": "diagonal"}).status_code == 422
    assert client.post(f"{API}/nodal/{ds}/points", json={"max_points": 10}).status_code == 422
    assert client.post(f"{API}/nodal/{ds}/points", json={"extra": 1}).status_code == 422
    flat = {"width_px": 10, "height_px": 10, "p1_px": [1, 1], "p1_model": [0, 0], "p2_px": [1, 5], "p2_model": [1, 1]}
    assert client.post(f"{API}/overlay", json=flat).status_code == 422


def test_openapi_documents_the_contract(client):
    spec = client.get("/openapi.json").json()
    paths = spec["paths"]
    for path in ("/api/pipeline/nodal", "/api/pipeline/nodal/{dataset_id}/profile", "/api/pipeline/overlay"):
        assert path in paths
    schemas = spec["components"]["schemas"]
    assert {"NodalDatasetInfo", "LayerProfile", "ContactCheck", "NodalPoints", "OverlayPlacement"} <= set(schemas)


def test_api_inspect_and_run(client):
    files = [("files", ("outline.csv", (DATA / "outline_fig-3-10_system-D.csv").read_bytes(), "text/csv"))]
    assert client.get("/api/pipeline/profiles").json() == ["thesis-2026"]
    r = client.post("/api/pipeline/inspect", files=files)
    assert r.status_code == 200 and len(r.json()["parameters"]) == 9
    condition = {"bc": "QT", "q_hot_w": 0.3152, "t_cold_face_c": 20, "current_a": 0.8}
    r = client.post("/api/pipeline/run", files=files, data={"settings": json.dumps({"default_condition": condition})})
    assert r.status_code == 200
    assert r.json()["systems"][0]["open_circuit"]["method"] == "extrapolated from the two lowest currents"


def test_api_rejects_bad_input(client):
    files = [("files", ("outline.csv", (DATA / "outline_fig-3-10_system-D.csv").read_bytes(), "text/csv"))]
    bad = {"settings": '{"default_condition": {"bc": "TT"}}'}
    r = client.post("/api/pipeline/run", files=files, data=bad)
    assert r.status_code == 422 and r.json()["detail"][0]["loc"][0] == "default_condition"
    assert client.post("/api/pipeline/run", files=files, data={"settings": '{"profile": "../x"}'}).status_code == 422
    assert client.post("/api/pipeline/run", files=files, data={"settings": '{"unknown": 1}'}).status_code == 422
    assert client.post("/api/pipeline/inspect", files=files, data={"profile": "../secrets"}).status_code == 400
    assert client.post("/api/pipeline/inspect", files=files, data={"profile": "nope"}).status_code == 404
