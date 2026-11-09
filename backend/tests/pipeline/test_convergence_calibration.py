"""Mesh convergence (GCI) across systems and the calibration of the 1D twin."""

from __future__ import annotations

import json

import pandas as pd
import pytest

from teglab.pipeline import RunOptions, run_pipeline
from teglab.pipeline.__main__ import main
from teglab.pipeline.convergence import grid_convergence
from teglab.pipeline.schemas import CalibrationSettings, MeshLevel, TwinProfile

from .conftest import DATA, FULL_LOAD_D, TT_THESIS

A_SERIES = {"A": "A0", "B": "A5", "C": "A10"}
C1 = {"A": "C1", "B": "C1", "C": "C1"}


def _thesis_mesh_study(aseries, fig310, profile):
    rep = run_pipeline(
        [aseries, fig310], RunOptions(profile=profile, default_condition=FULL_LOAD_D, variants=A_SERIES, meshes=C1)
    ).report
    return {(g.key, g.current_a): g for g in rep.convergence}


def test_gci_of_systems_c_and_d_reproduces_the_thesis_mesh_study(aseries, fig310, profile):
    """Thesis Section 3.5.3: ΔT_TE 76.159 / 76.187 K on C1/C2 → GCI 0.037 %, Richardson 76.197 K."""
    by = _thesis_mesh_study(aseries, fig310, profile)
    g = by[("dt_te_k", 0.8)]
    assert g.systems == ["C", "D"] and g.meshes == ["C1", "C2"] and g.variant == "A10"
    assert g.values == pytest.approx([76.159, 76.187])
    assert g.r == pytest.approx(2.0) and g.p_used == 2.0 and g.fs == 3.0
    assert g.gci_fine == pytest.approx(3.68e-4, rel=0.01)
    assert g.f_extrapolated == pytest.approx(76.196, abs=1e-3)
    assert g.status == "converged"
    assert by[("ohmic_ohm", None)].status == "converged"
    assert by[("q_cold_w", 0.8)].status == "identical"


def test_no_mesh_labels_no_study(aseries, fig310, profile):
    rep = run_pipeline([aseries, fig310], RunOptions(profile=profile, default_condition=FULL_LOAD_D)).report
    assert rep.convergence == []


def _three_meshes(values: list[float], profile):
    prof = profile.model_copy(
        update={"meshes": {k: MeshLevel(element_size_mm=h) for k, h in (("M1", 0.4), ("M2", 0.2), ("M3", 0.1))}}
    )
    summary = pd.DataFrame(
        {"variant": "A0", "bc": "QT", "mesh": ["M1", "M2", "M3"], "u_oc_v": values},
        index=pd.Index(list("XYZ"), name="system"),
    )
    idx = pd.MultiIndex.from_tuples([(s, "DP 1") for s in "XYZ"], names=["system", "design_point"])
    frame = pd.DataFrame({"current_a": 0.5, "dt_te_k": values}, index=idx)
    return {g.key: g for g in grid_convergence(frame, summary, prof)}


def test_three_meshes_give_the_observed_order(profile):
    exact, c = 50.0, 4.0
    by = _three_meshes([exact + c * h**2 for h in (0.4, 0.2, 0.1)], profile)
    g = by["dt_te_k"]
    assert g.p_observed == pytest.approx(2.0) and g.fs == 1.25
    assert g.f_extrapolated == pytest.approx(exact)
    assert g.systems == ["X", "Y", "Z"]


def test_oscillating_meshes_are_flagged(profile):
    assert _three_meshes([50.0, 50.4, 50.1], profile)["dt_te_k"].status == "oscillatory"


def test_calibration_recovers_the_passive_factors_on_a0_tt(a0tt, profile):
    raw = profile.model_copy(update={"twin": TwinProfile()})
    rep = run_pipeline(
        [a0tt],
        RunOptions(profile=raw, default_condition=TT_THESIS, variants={"A": "A0"}, calibration=CalibrationSettings()),
    ).report
    c = rep.calibration
    assert c is not None and c.success and c.fitted_on == ["A"] and c.validated_on == []
    assert c.n_residuals == 12
    assert c.rms_fit_after < c.rms_fit_before / 20
    assert 1.2 < c.fitted["hot_side_factor"] < 1.8 and 0.7 < c.fitted["cold_side_factor"] < 1.1
    s = rep.systems[0]
    assert s.twin.calibrated
    assert all(abs(m.deviation) < 1e-3 for m in s.twin.metrics if m.deviation is not None)


def test_calibration_needs_enough_observations(aseries, profile):
    rep = run_pipeline(
        [aseries], RunOptions(profile=profile, variants=A_SERIES, calibration=CalibrationSettings(systems=["A"]))
    ).report
    assert rep.calibration is None
    assert any(i.code == "calibration" and "not enough" in i.message for i in rep.issues)
    assert not any(s.twin.calibrated for s in rep.systems)


def test_api_run_with_meshes_and_calibration(client):
    files = [
        ("files", (n, (DATA / n).read_bytes(), "text/csv"))
        for n in ("design-points_A-series_QT.csv", "outline_fig-3-10_system-D.csv")
    ]
    settings = {
        "default_condition": FULL_LOAD_D.model_dump(),
        "variants": A_SERIES,
        "meshes": C1,
        "calibration": {"systems": ["C"]},
    }
    r = client.post("/api/pipeline/run", files=files, data={"settings": json.dumps(settings)})
    assert r.status_code == 200
    body = r.json()
    assert any(g["key"] == "dt_te_k" and g["status"] == "converged" for g in body["convergence"])
    assert body["calibration"]["fitted_on"] == ["C"] and set(body["calibration"]["validated_on"]) == {"A", "B", "D"}


def test_cli_prints_calibration_and_mesh_study(capsys):
    args = ["run", str(DATA / "design-points_A0-TT_table-4-4.csv"), "--bc", "TT", "--t-hot", "100"]
    assert main([*args, "--system", "A=A0", "--calibrate"]) == 0
    assert "1D twin calibration on A" in capsys.readouterr().out
    args = ["run", str(DATA / "design-points_A-series_QT.csv"), str(DATA / "outline_fig-3-10_system-D.csv")]
    args += ["--current", "0.8", "--system", "C=A10", "--mesh", "C=C1"]
    main(args)
    assert "Mesh convergence" in capsys.readouterr().out
