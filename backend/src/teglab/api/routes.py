"""REST endpoints.  CPU-bound handlers are plain ``def`` so FastAPI runs them in its thread pool."""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import PlainTextResponse

from .. import __version__
from ..analysis import analyze_case, compare_cases, gci
from ..analytic import thesis_table_5_2
from ..ansys_export import DEFAULT_PARAM_MAP, design_point_rows, design_points_csv, workbench_journal
from ..ansys_io import ParsedTable, build_cases, parse_files, suggest
from ..calibration import PARAMS, calibrate, compare_model
from ..cases import Case
from ..demo import EXAMPLES_DIR, seed
from ..design import reference_design
from ..lab import CERAMICS, SOLDERS, LabPlanRequest, plan_experiment
from ..materials import Material, MaterialLibrary
from ..materials_study import efficiency_map, segmented_study, thomson_study
from ..optimize import geometry_map, geometry_sweep
from ..reference import summary as reference_summary
from ..roles import ROLES
from ..solver import build_model, characterize, solve, solve_for_load
from ..store import Store
from .schemas import (
    AnalysisRequest,
    CalibrationRequest,
    CasePatch,
    CharacterizeRequest,
    CompareRequest,
    EfficiencyMapRequest,
    ExportRequest,
    GciRequest,
    ImportCommitRequest,
    MapRequest,
    SegmentedRequest,
    SolveRequest,
    SweepRequest,
    ThomsonRequest,
)

router = APIRouter(prefix="/api")

EXAMPLE_INFO = {
    "thesis_A0-TT_table-4-4.csv": "Thesis Table 4.4 (A0-TT) in Workbench design-point format",
    "thesis_A-series_QT.csv": "Thesis A0/A5/A10 BC-QT values as one 3-system Workbench table",
    "synthetic_A-series_QT_model.csv": "SYNTHETIC dense sweeps (1D model) in Workbench format",
    "synthetic_mechanical_tabular.txt": "SYNTHETIC Mechanical tabular export (time → current)",
}


def get_store(request: Request) -> Store:
    return request.app.state.store


def get_library(store: Store = Depends(get_store)) -> MaterialLibrary:
    return store.library()


def _cases(store: Store, ids: list[str]) -> list[Case]:
    out = []
    for i in ids:
        c = store.get_case(i)
        if c is None:
            raise HTTPException(404, f"case {i} not found")
        out.append(c)
    return out


def _case_summary(c: Case) -> dict:
    return {
        "id": c.id,
        "name": c.name,
        "meta": c.meta.model_dump(),
        "n_points": c.n_points,
        "roles": [r for r in c.data if c.has(r)],
        "created": c.created,
    }


# ------------------------------------------------------------------ general
@router.get("/health", tags=["general"])
def health() -> dict:
    return {"status": "ok", "version": __version__}


@router.get("/reference", tags=["general"])
def reference() -> dict:
    return {
        **reference_summary(),
        "table_5_2_analytic": thesis_table_5_2(),
        "design": reference_design().model_dump(),
    }


@router.get("/roles", tags=["general"])
def roles() -> list[dict]:
    return [
        {"key": r.key, "label": r.label, "dimension": r.dimension, "group": r.group, "description": r.description}
        for r in ROLES.values()
    ]


# ------------------------------------------------------------------ model
@router.post("/model/solve", tags=["model"])
def model_solve(req: SolveRequest, lib: MaterialLibrary = Depends(get_library)) -> dict:
    model = build_model(req.design, lib)
    if req.load_resistance_ohm is not None:
        sol = solve_for_load(model, req.load_resistance_ohm)
        if req.profiles:
            sol = solve(model, sol.current_a, sol.temps, with_profiles=True)
    else:
        sol = solve(model, req.current_a or 0.0, with_profiles=req.profiles)
    return sol.to_dict(profiles=req.profiles)


@router.post("/model/characterize", tags=["model"])
def model_characterize(req: CharacterizeRequest, lib: MaterialLibrary = Depends(get_library)) -> dict:
    return characterize(build_model(req.design, lib), n_sweep=req.n_sweep)


@router.post("/model/compare", tags=["model"])
def model_compare(req: CompareRequest, store: Store = Depends(get_store)) -> list[dict]:
    return compare_model(req.design, _cases(store, req.case_ids), store.library(), dense=req.dense)


# ------------------------------------------------------------------ cases
@router.get("/cases", tags=["cases"])
def list_cases(store: Store = Depends(get_store)) -> list[dict]:
    return [_case_summary(c) for c in store.list_cases()]


@router.get("/cases/{case_id}", tags=["cases"])
def get_case(case_id: str, store: Store = Depends(get_store)) -> dict:
    c = store.get_case(case_id)
    if c is None:
        raise HTTPException(404, "case not found")
    return c.model_dump()


@router.patch("/cases/{case_id}", tags=["cases"])
def patch_case(case_id: str, patch: CasePatch, store: Store = Depends(get_store)) -> dict:
    c = store.update_case(case_id, patch.name, patch.meta)
    if c is None:
        raise HTTPException(404, "case not found")
    return _case_summary(c)


@router.delete("/cases/{case_id}", tags=["cases"])
def delete_case(case_id: str, store: Store = Depends(get_store)) -> dict:
    return {"deleted": store.delete_case(case_id)}


@router.post("/demo/seed", tags=["cases"])
def demo_seed(store: Store = Depends(get_store)) -> dict:
    store.set_setting("seeded", "0")
    return {"added": seed(store)}


# ------------------------------------------------------------------ import
@router.post("/import/parse", tags=["import"])
async def import_parse(files: list[UploadFile] | None = File(None), text: str | None = Form(None)) -> dict:
    payload: list[tuple[str, bytes]] = []
    for f in files or []:
        payload.append((f.filename or "upload.csv", await f.read()))
    if text and text.strip():
        payload.append(("pasted.txt", text.encode("utf-8")))
    if not payload:
        raise HTTPException(400, "upload a file or paste a table")
    try:
        table = parse_files(payload)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return table.to_dict()


@router.post("/import/resuggest", tags=["import"])
def import_resuggest(table: dict) -> dict:
    return suggest(ParsedTable.from_dict(table)).to_dict()


@router.post("/import/commit", tags=["import"])
def import_commit(req: ImportCommitRequest, store: Store = Depends(get_store)) -> dict:
    table = ParsedTable.from_dict(req.table)
    cases, notes = build_cases(
        table,
        [c.model_dump() for c in req.cases],
        split_by=req.split_by,
        time_to_current=req.time_to_current,
    )
    if not cases:
        raise HTTPException(400, "no case could be built – check the column mapping")
    store.add_cases(cases)
    return {"cases": [_case_summary(c) for c in cases], "notes": notes}


@router.get("/import/examples", tags=["import"])
def import_examples() -> list[dict]:
    return [
        {"name": p.name, "description": EXAMPLE_INFO.get(p.name, ""), "size": p.stat().st_size}
        for p in sorted(EXAMPLES_DIR.glob("*"))
        if p.is_file()
    ]


@router.get("/import/examples/{name}", tags=["import"], response_class=PlainTextResponse)
def import_example(name: str) -> str:
    if not re.fullmatch(r"[\w.\-]+", name):
        raise HTTPException(400, "bad name")
    p = EXAMPLES_DIR / name
    if not p.is_file():
        raise HTTPException(404, "example not found")
    return p.read_text(encoding="utf-8")


# ------------------------------------------------------------------ analysis
@router.post("/analysis", tags=["analysis"])
def analysis(req: AnalysisRequest, store: Store = Depends(get_store)) -> dict:
    lib = store.library()
    design = req.design or reference_design()
    cases = _cases(store, req.case_ids)
    results = [analyze_case(c, design, lib) for c in cases]
    ref_idx = next((k for k, c in enumerate(cases) if c.id == req.reference_id), 0)
    return {"analyses": results, "comparison": compare_cases(results, ref_idx)}


@router.post("/analysis/gci", tags=["analysis"])
def analysis_gci(req: GciRequest) -> dict:
    return gci(req.f_coarse, req.f_fine, req.r, req.p, req.fs, req.f_medium)


# ------------------------------------------------------------------ calibration
@router.get("/calibration/params", tags=["calibration"])
def calibration_params() -> list[dict]:
    return [{"key": p.key, "label": p.label, "min": p.lo, "max": p.hi, "log": p.log} for p in PARAMS.values()]


@router.post("/calibration", tags=["calibration"])
def calibration(req: CalibrationRequest, store: Store = Depends(get_store)) -> dict:
    try:
        return calibrate(req.design, _cases(store, req.case_ids), req.fit, store.library(), req.weights)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


# ------------------------------------------------------------------ optimizer
@router.post("/optimize/sweep", tags=["optimizer"])
def optimize_sweep(req: SweepRequest, lib: MaterialLibrary = Depends(get_library)) -> dict:
    return geometry_sweep(
        req.design, req.mode, req.values, req.heights_mm, req.objective, req.constraints, req.volume_mm3, lib
    )


@router.post("/optimize/map", tags=["optimizer"])
def optimize_map(req: MapRequest, lib: MaterialLibrary = Depends(get_library)) -> dict:
    return geometry_map(
        req.design, req.mode, req.x_values, req.heights_mm, req.objective, req.constraints, req.volume_mm3, lib
    )


# ------------------------------------------------------------------ materials
@router.get("/materials", tags=["materials"])
def materials(lib: MaterialLibrary = Depends(get_library)) -> list[dict]:
    return [m.to_dict() for m in lib.all()]


@router.post("/materials/upload", tags=["materials"])
async def material_upload(
    file: UploadFile = File(...),
    name: str = Form(...),
    kind: str = Form(...),
    family: str = Form("custom"),
    t_limit_c: float | None = Form(None),
    density_g_cm3: float = Form(7.0),
    store: Store = Depends(get_store),
) -> dict:
    if kind not in ("p", "n"):
        raise HTTPException(400, "kind must be 'p' or 'n'")
    text = (await file.read()).decode("utf-8-sig", errors="replace")
    mat_id = "user_" + re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    try:
        m = Material.from_csv(
            text, id=mat_id, name=name, kind=kind, family=family, t_limit_c=t_limit_c, density_g_cm3=density_g_cm3
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    store.add_material(m)
    return m.to_dict()


@router.delete("/materials/{mat_id}", tags=["materials"])
def material_delete(mat_id: str, store: Store = Depends(get_store)) -> dict:
    return {"deleted": store.delete_material(mat_id)}


@router.post("/materials/efficiency-map", tags=["materials"])
def materials_efficiency(req: EfficiencyMapRequest, lib: MaterialLibrary = Depends(get_library)) -> dict:
    try:
        return efficiency_map(
            [tuple(p) for p in req.pairs],
            req.t_cold_c,
            req.t_hot_values_c,
            lib,
            with_contacts=req.contacts,
            a_mm=req.a_mm,
            height_mm=req.height_mm,
        )
    except KeyError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/materials/segmented", tags=["materials"])
def materials_segmented(req: SegmentedRequest, lib: MaterialLibrary = Depends(get_library)) -> dict:
    try:
        return segmented_study(
            req.p_hot,
            req.p_cold,
            req.n_hot,
            req.n_cold,
            req.t_hot_c,
            req.t_cold_c,
            req.fractions,
            lib,
            a_mm=req.a_mm,
            height_mm=req.height_mm,
            contacts=req.contacts,
        )
    except KeyError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/materials/thomson", tags=["materials"])
def materials_thomson(req: ThomsonRequest, lib: MaterialLibrary = Depends(get_library)) -> dict:
    return thomson_study(req.design, lib)


# ------------------------------------------------------------------ lab & ANSYS export
@router.get("/lab/catalog", tags=["lab"])
def lab_catalog() -> dict:
    return {
        "solders": list(SOLDERS.values()),
        "ceramics": [{"id": k, **v} for k, v in CERAMICS.items()],
    }


@router.post("/lab/plan", tags=["lab"])
def lab_plan(req: LabPlanRequest, lib: MaterialLibrary = Depends(get_library)) -> dict:
    try:
        return plan_experiment(req, lib)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/ansys/export", tags=["ansys"])
def ansys_export(req: ExportRequest) -> dict:
    rows = design_point_rows(req.designs, req.currents)
    pm = req.param_map or DEFAULT_PARAM_MAP
    return {
        "rows": rows,
        "csv": design_points_csv(rows, pm),
        "journal": workbench_journal(rows, pm, update=req.update),
        "param_map": pm,
    }
