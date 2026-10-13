from teglab.demo import thesis_a0_tt_csv


def test_health_and_seed(client):
    assert client.get("/api/health").json()["status"] == "ok"
    cases = client.get("/api/cases").json()
    assert len(cases) == 7


def test_import_parse_commit_analyze(client):
    r = client.post("/api/import/parse", files={"files": ("a0.csv", thesis_a0_tt_csv(), "text/csv")})
    assert r.status_code == 200
    table = r.json()
    assert table["format"] == "workbench-design-points"
    body = {"table": table, "cases": [{"index": 1, "name": "uploaded", "meta": {"bc": "TT", "t_hot_c": 100}}]}
    r = client.post("/api/import/commit", json=body)
    assert r.status_code == 200
    cid = r.json()["cases"][0]["id"]
    r = client.post("/api/analysis", json={"case_ids": [cid]})
    assert r.status_code == 200
    assert r.json()["analyses"][0]["metrics"]["u_oc_v"] == 0.03002
    assert client.delete(f"/api/cases/{cid}").json()["deleted"]


def test_import_paste_text(client):
    r = client.post("/api/import/parse", data={"text": "I\tU [mV]\n0\t30\n0.5\t20\n"})
    assert r.status_code == 200
    roles = [c["role"] for c in r.json()["columns"]]
    assert roles == ["current", "voltage"]


def test_bad_import_is_400(client):
    assert client.post("/api/import/parse", data={"text": "  "}).status_code == 400


def test_model_endpoints(client):
    d = client.get("/api/reference").json()["design"]
    r = client.post("/api/model/solve", json={"design": d, "current_a": 0.5})
    assert r.status_code == 200 and "profiles" in r.json()
    r = client.post("/api/model/solve", json={"design": d, "load_resistance_ohm": 0.03, "profiles": False})
    assert r.status_code == 200 and r.json()["current_a"] > 0
    r = client.post("/api/model/characterize", json={"design": d, "n_sweep": 5})
    assert r.status_code == 200 and len(r.json()["sweep"]) == 5


def test_unknown_material_is_400(client):
    d = client.get("/api/reference").json()["design"]
    d["p_leg"]["segments"] = [{"material": "unobtainium", "fraction": 1}]
    r = client.post("/api/model/solve", json={"design": d})
    assert r.status_code == 400


def test_calibration_endpoint(client):
    cases = client.get("/api/cases").json()
    d = client.get("/api/reference").json()["design"]
    r = client.post("/api/calibration", json={"design": d, "case_ids": [cases[0]["id"]]})
    assert r.status_code == 200 and r.json()["success"]


def test_material_upload(client):
    csv_text = "T_C,alpha_uV_K,rho_uOhm_m,kappa\n25,200,10,1.5\n200,210,14,1.6\n"
    r = client.post(
        "/api/materials/upload",
        files={"file": ("m.csv", csv_text, "text/csv")},
        data={"name": "My BiSbTe", "kind": "p", "t_limit_c": "250"},
    )
    assert r.status_code == 200
    ids = [m["id"] for m in client.get("/api/materials").json()]
    assert "user_my_bisbte" in ids


def test_examples_listing(client):
    ex = client.get("/api/import/examples").json()
    assert any(e["name"].startswith("thesis_") for e in ex)
    assert client.get("/api/import/examples/thesis_A0-TT_table-4-4.csv").status_code == 200
    assert client.get("/api/import/examples/..%2Fsecret").status_code in (400, 404)
