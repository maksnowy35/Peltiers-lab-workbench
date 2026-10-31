"""ANSYS ingestion pipeline: exports in, validated frames and derived thermoelectric quantities out.

Layers (each depends only on the ones above it)::

    keys, schemas        canonical quantity keys and Pydantic contracts (+ JSON project profiles)
    readers              adapters for Workbench outline / design-point CSV and Mechanical nodal text
    frames               vectorized mapping to one canonical DataFrame, validation
    extract, maxpower    per-system physics: U_oc, ΔT_TE, R_int split, thermal split, P_max
    compare, fields      variant comparison and nodal-field reductions
    nodal, overlay       nodal datasets (layer stack, contact checks, view projection), picture placement
    synthetic            a physically consistent unit cell for demos and end-to-end tests
    run                  orchestration into a PipelineReport (used by the CLI and the REST API)

Command line: ``python -m teglab.pipeline inspect|run FILES...``.
"""

from .keys import NodalKey, QuantityKey
from .run import PipelineResult, RunOptions, inspect_files, run_pipeline
from .schemas import CaseCondition, PipelineReport, ProjectProfile, load_profile

__all__ = [
    "CaseCondition",
    "NodalKey",
    "PipelineReport",
    "PipelineResult",
    "ProjectProfile",
    "QuantityKey",
    "RunOptions",
    "inspect_files",
    "load_profile",
    "run_pipeline",
]
