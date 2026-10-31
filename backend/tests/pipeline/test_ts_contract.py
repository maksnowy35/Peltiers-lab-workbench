"""The TypeScript mirror of the API contract must match the Pydantic models it is generated from."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[2] / "scripts" / "gen_ts_types.py"


@pytest.fixture(scope="module")
def gen():
    spec = importlib.util.spec_from_file_location("gen_ts_types", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_generated_typescript_is_up_to_date(gen):
    if not gen.TARGET.parent.is_dir():
        pytest.skip("frontend sources not present (backend-only checkout)")
    assert gen.TARGET.read_text(encoding="utf-8") == gen.render(), (
        "frontend/src/app/core/pipeline.models.ts is stale: run python backend/scripts/gen_ts_types.py"
    )


def test_request_and_response_shapes(gen):
    ts = gen.render()
    # responses: defaults are always serialized, so every key is present
    assert "export interface StackLayer {\n  name: string;\n  bodies: string[];" in ts
    assert "  active: boolean;\n}" in ts
    # requests: defaults are optional, nested dual models use their Input variant
    assert "  active?: boolean;" in ts and "  stack?: LayerStackInput | null;" in ts
    assert "export interface LayerStackInput {\n  axis: Axis;\n  layers: StackLayerInput[];\n}" in ts
    assert "  default_condition?: CaseConditionInput | null;" in ts
    # enums and literal aliases become unions with value arrays the UI can iterate
    assert "export type View = 'front' | 'back' | 'right' | 'left' | 'top' | 'bottom';" in ts
    assert "export const NODAL_KEY_VALUES: readonly NodalKey[] = [" in ts
    assert "  p1_px: [number, number];" in ts and "  aliases: Partial<Record<QuantityKey, string[]>>;" in ts
