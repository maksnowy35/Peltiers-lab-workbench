"""Command line of the pipeline (python -m teglab.pipeline)."""

from __future__ import annotations

import json

from teglab.pipeline.__main__ import main

from .conftest import DATA


def test_cli_inspect(capsys):
    assert main(["inspect", str(DATA / "outline_fig-3-10_system-D.csv")]) == 0
    out = capsys.readouterr().out
    assert "workbench-outline" in out and "Dense mesh copy C 10st" in out and "t_leg_cold_c" in out


def test_cli_run_json(capsys):
    args = ["run", str(DATA / "outline_fig-3-10_system-D.csv"), "--current", "0.8", "--json"]
    assert main(args) == 0
    report = json.loads(capsys.readouterr().out)
    d = report["systems"][0]
    assert d["system"] == "D" and d["variant"] == "A10"
    assert d["resistance"]["contact_verdict"] == "inactive"
