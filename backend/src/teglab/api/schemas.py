"""Request bodies of the REST API (responses are plain JSON dictionaries)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from ..cases import CaseMeta
from ..design import Contacts, PairDesign
from ..optimize import Constraints


class SolveRequest(BaseModel):
    design: PairDesign
    current_a: float | None = None
    load_resistance_ohm: float | None = None
    profiles: bool = True


class CharacterizeRequest(BaseModel):
    design: PairDesign
    n_sweep: int = Field(25, ge=3, le=200)


class CompareRequest(BaseModel):
    design: PairDesign
    case_ids: list[str]
    dense: int = Field(41, ge=0, le=400)


class CaseDef(BaseModel):
    index: int = 1
    name: str
    meta: CaseMeta = CaseMeta()


class ImportCommitRequest(BaseModel):
    table: dict
    cases: list[CaseDef]
    split_by: str | None = None
    time_to_current: float | None = None


class CasePatch(BaseModel):
    name: str | None = None
    meta: CaseMeta | None = None


class AnalysisRequest(BaseModel):
    case_ids: list[str]
    design: PairDesign | None = None
    reference_id: str | None = None


class GciRequest(BaseModel):
    f_coarse: float
    f_fine: float
    r: float = Field(gt=1.0)
    p: float = Field(2.0, gt=0)
    fs: float | None = Field(None, gt=0)
    f_medium: float | None = None


class CalibrationRequest(BaseModel):
    design: PairDesign
    case_ids: list[str]
    fit: list[str] = ["hot_side_factor", "cold_side_factor", "r_el_extra_mohm"]
    weights: dict[str, float] | None = None


class SweepRequest(BaseModel):
    design: PairDesign
    mode: Literal["fixed_cold_base", "fixed_hot_base", "constant_volume"] = "fixed_cold_base"
    values: list[float] = Field(min_length=1, max_length=121)
    heights_mm: list[float] | None = Field(None, max_length=8)
    objective: Literal["p_max", "eta_at_p_max", "power_per_volume"] = "p_max"
    constraints: Constraints = Constraints()
    volume_mm3: float | None = None


class MapRequest(BaseModel):
    design: PairDesign
    mode: Literal["fixed_cold_base", "fixed_hot_base", "constant_volume"] = "fixed_cold_base"
    x_values: list[float] = Field(min_length=2, max_length=41)
    heights_mm: list[float] = Field(min_length=2, max_length=31)
    objective: Literal["p_max", "eta_at_p_max", "power_per_volume"] = "p_max"
    constraints: Constraints = Constraints()
    volume_mm3: float | None = None


class EfficiencyMapRequest(BaseModel):
    pairs: list[tuple[str, str]] = Field(min_length=1, max_length=8)
    t_cold_c: float = 30.0
    t_hot_values_c: list[float] = Field(min_length=1, max_length=60)
    contacts: Contacts | None = None
    a_mm: float = 1.4
    height_mm: float = 1.4


class SegmentedRequest(BaseModel):
    p_hot: str
    p_cold: str = "bi2te3_p"
    n_hot: str
    n_cold: str = "bi2te3_n"
    t_hot_c: float = 500.0
    t_cold_c: float = 30.0
    fractions: list[float] = Field(min_length=2, max_length=41)
    a_mm: float = 1.4
    height_mm: float = 5.0
    contacts: Contacts | None = None


class ThomsonRequest(BaseModel):
    design: PairDesign


class ExportRequest(BaseModel):
    designs: list[dict] = Field(min_length=1, max_length=50)
    currents: list[float] = Field(min_length=1, max_length=50)
    param_map: dict[str, str] | None = None
    update: bool = False
