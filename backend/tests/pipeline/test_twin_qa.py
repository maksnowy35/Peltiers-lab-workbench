"""1D twin of every ANSYS system and the QA gates built on it."""

from __future__ import annotations

import json

import pandas as pd
import pytest

from teglab.pipeline import RunOptions, run_pipeline
from teglab.pipeline.qa import qa_gates
from teglab.pipeline.schemas import TwinProfile, TwinSystem
from teglab.pipeline.twin import twin_design, verdict

from .conftest import DATA, QT_THESIS, TT_THESIS


def _run(files, profile, **kw):
    return run_pipeline([files], RunOptions(profile=profile, **kw)).report


def _metric(system, key):
    return next(m for m in system.twin.metrics if m.key == key)


def _gate(system, code):
    return next(g for g in system.qa if g.code == code)


def test_twin_reproduces_the_thesis_a0_tt_case(a0tt, profile):
    """Thesis profile factors (A0-TT calibration): FEM and 1D agree to 0.1 % on every quantity."""
    s = _run(a0tt, profile, default_condition=TT_THESIS, variants={"A": "A0"}).systems[0]
    assert s.twin.status == "ok" and s.twin.a_hot_mm == pytest.approx(1.4)
    for key in ("u_oc_v", "dt_te_k", "k_te_w_k", "p_max_w", "i_opt_a", "q_hot_w"):
        m = _metric(s, key)
        assert abs(m.deviation) < 1e-3, key
    assert len(s.twin.points) == 4
    assert max(p.worst_deviation for p in s.twin.points) < 0.01
    assert all(g.status in ("pass", "skip") for g in s.qa)
    assert _gate(s, "twin-p_max_w").status == "pass"


def test_uncalibrated_twin_shows_the_passive_layer_effect(a0tt, profile):
    raw = profile.model_copy(update={"twin": TwinProfile()})
    s = _run(a0tt, raw, default_condition=TT_THESIS, variants={"A": "A0"}).systems[0]
    u = _metric(s, "u_oc_v")
    assert 0.003 < u.deviation < 0.02  # thinner passive drop than in ANSYS, still inside the 2 % gate
    assert u.status == "pass"


def test_variant_angle_sets_the_twin_geometry(aseries, profile):
    rep = _run(aseries, profile, default_condition=QT_THESIS, variants={"A": "A0", "B": "A5", "C": "A10"})
    by = {s.system: s for s in rep.systems}
    assert by["C"].twin.a_hot_mm == pytest.approx(profile.geometry.a_hot_mm(10.0))
    # the open circuit of all three variants agrees with the thesis numbers within 0.2 %
    for s in rep.systems:
        assert abs(_metric(s, "u_oc_v").deviation) < 2e-3
        assert abs(_metric(s, "dt_te_k").deviation) < 2e-3


def test_unbracketed_p_max_is_not_compared_and_inactive_contacts_fail(aseries, profile):
    c = next(s for s in _run(aseries, profile, variants={"C": "A10"}).systems if s.system == "C")
    assert _metric(c, "p_max_w").ansys is None and _metric(c, "p_max_w").status == "skip"
    assert _gate(c, "p-max-bracketed").status == "warn"
    assert _gate(c, "contacts").status == "fail"
    # the twin carries the contact resistance the ANSYS model lost: lower voltage under load
    loaded = next(p for p in c.twin.points if p.current_a > 0 and p.voltage_ansys_v is not None)
    assert loaded.voltage_model_v < loaded.voltage_ansys_v


def test_system_without_variant_skips_the_twin(aseries, profile):
    s = _run(aseries, profile).systems[0]
    assert s.twin.status == "skipped" and "variant" in s.twin.note
    assert _gate(s, "twin").status == "skip"


def test_twin_design_rejects_impossible_frustum(profile):
    with pytest.raises(ValueError, match="hot-end side"):
        twin_design(profile, 30.0, QT_THESIS.bc, 20.0, q_hot_w=0.3)


@pytest.mark.parametrize(
    ("deviation", "expected"), [(0.01, "pass"), (-0.05, "pass"), (0.07, "warn"), (-0.11, "fail"), (None, "skip")]
)
def test_verdict_bands(deviation, expected):
    assert verdict(deviation, 0.05) == expected


def test_gates_flag_inconsistent_inputs(profile):
    row = pd.Series(
        {
            "bc": "QT",
            "p_max_w": 0.02,
            "p_frozen_w": 0.015,
            "balance_rel_max": 0.03,
            "bracketed": True,
            "oc_method": "measured at I = 0",
            "alpha_eff_v_k": 4.12e-4,
            "contact_verdict": "inconclusive",
        }
    )
    gates = {g.code: g for g in qa_gates(row, TwinSystem(), profile)}
    assert gates["upper-bound"].status == "fail" and gates["upper-bound"].value == pytest.approx(4 / 3)
    assert gates["energy-balance"].status == "fail"
    assert gates["seebeck"].status == "warn"
    assert gates["contacts"].status == "warn"


def test_api_run_returns_twin_and_gates(client):
    files = [("files", ("a0tt.csv", (DATA / "design-points_A0-TT_table-4-4.csv").read_bytes(), "text/csv"))]
    settings = {"default_condition": TT_THESIS.model_dump(), "variants": {"A": "A0"}}
    r = client.post("/api/pipeline/run", files=files, data={"settings": json.dumps(settings)})
    assert r.status_code == 200
    system = r.json()["systems"][0]
    assert system["twin"]["status"] == "ok" and system["twin"]["metrics"]
    assert {g["code"] for g in system["qa"]} >= {"energy-balance", "upper-bound", "twin-p_max_w"}
