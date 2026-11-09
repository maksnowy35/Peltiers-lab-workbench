"""Pydantic v2 contracts of the ingestion pipeline.

Four groups of models:

* **input metadata** - what a reader found in a file (parameter definitions, analysis systems);
* **project profile** - how the exact names of *this* ANSYS project map to canonical keys, plus the
  geometry, materials, contacts and boundary conditions needed to interpret the numbers;
* **results** - what the pipeline derives, with the method used for every number;
* **API requests/responses** of the nodal and overlay endpoints.

Raw numbers themselves travel as pandas DataFrames (see ``frames.py``); the models describe and
validate everything around them.  These models are the API contract: ``backend/scripts/gen_ts_types.py``
mirrors them to ``frontend/src/app/core/pipeline.models.ts`` and a test fails when the mirror is stale.
"""

from __future__ import annotations

import json
import math
import re
from enum import StrEnum
from functools import cached_property
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .keys import NodalKey, QuantityKey

__all__ = [
    "AnalyticPower",
    "Axis",
    "BodyInfo",
    "BoundaryCondition",
    "CalibrationSettings",
    "CaseCondition",
    "ContactCheck",
    "ContactProfile",
    "ContactVerdict",
    "ConvergenceStatus",
    "DatasetImage",
    "DeleteResult",
    "Direction",
    "ExampleInfo",
    "GeometryProfile",
    "GridConvergence",
    "ImageCalibration",
    "ImageSpec",
    "Issue",
    "LayerProfile",
    "LayerRow",
    "LayerSplit",
    "LayerStack",
    "Level",
    "MaterialProfile",
    "MaxPowerFit",
    "MeshLevel",
    "NodalDatasetInfo",
    "NodalFileInfo",
    "NodalFileSpec",
    "NodalManifest",
    "NodalPoints",
    "OpenCircuit",
    "OverlayPlacement",
    "ParameterDef",
    "PipelineReport",
    "PointsRequest",
    "ProfileRequest",
    "ProjectProfile",
    "QaGate",
    "QaStatus",
    "ResistanceBreakdown",
    "ResolvedParameter",
    "RunSettings",
    "SourceFormat",
    "SourceSummary",
    "StackLayer",
    "SystemRef",
    "SystemReport",
    "SystemRule",
    "ThermalSplit",
    "TwinCalibration",
    "TwinMetric",
    "TwinPoint",
    "TwinProfile",
    "TwinSystem",
    "VariantComparison",
    "View",
    "load_profile",
]


# --------------------------------------------------------------------------------------------- enums
class SourceFormat(StrEnum):
    WORKBENCH_OUTLINE = "workbench-outline"  # "Outline of All Parameters": ID | Parameter Name | Value | Unit
    WORKBENCH_DESIGN_POINTS = "workbench-design-points"  # "Table of Design Points" CSV export
    MECHANICAL_NODAL = "mechanical-nodal"  # Mechanical result "Export Text File"


class Direction(StrEnum):
    INPUT = "input"
    OUTPUT = "output"


class BoundaryCondition(StrEnum):
    TT = "TT"  # both ceramic faces at prescribed temperatures
    QT = "QT"  # prescribed heat input on the hot face, prescribed temperature on the cold face


class Level(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class Issue(BaseModel):
    """A finding of the pipeline that a user should read (never raised as an exception)."""

    level: Level
    code: str
    message: str
    system: str | None = None
    source: str | None = None


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


# ------------------------------------------------------------------------------------ input metadata
class ParameterDef(_Frozen):
    """One Workbench parameter as found in an export, before interpretation."""

    pid: str = Field(pattern=r"^P\d+$", description="Workbench parameter ID, e.g. 'P97'")
    name: str = Field(min_length=1, description="Exact Workbench name, e.g. 'Reaction Probe 2 Heat'")
    unit: str | None = Field(None, description="Unit exactly as exported, e.g. 'A m^-2' or 'C'")
    system: str | None = Field(None, pattern=r"^[A-Z]{1,2}$", description="Owning system letter; None = unknown")
    direction: Direction | None = None

    @field_validator("name")
    @classmethod
    def _strip(cls, v: str) -> str:
        return " ".join(v.split())


class SystemRef(_Frozen):
    """An analysis system of the Workbench project, e.g. ``D`` = 'Dense mesh copy C 10st'."""

    letter: str = Field(pattern=r"^[A-Z]{1,2}$")
    title: str
    cell: str | None = Field(None, description="Outline cell reference, e.g. 'D1'")


class ResolvedParameter(ParameterDef):
    """A parameter mapped to a canonical key, with the linear map to canonical units."""

    key: QuantityKey | None
    confidence: float = Field(ge=0.0, le=1.0)
    rule: str = Field(description="How the key was found: 'alias', 'pattern', 'ignored' or 'unresolved'")
    factor: float = 1.0
    offset: float = 0.0
    sign: float = Field(1.0, description="+1/-1 applied after unit conversion (reaction-probe convention)")
    unit_note: str | None = None


class SourceSummary(BaseModel):
    source: str
    format: SourceFormat
    confidence: float
    n_parameters: int = 0
    n_rows: int = 0
    systems: list[SystemRef] = []
    notes: list[str] = []


# ----------------------------------------------------------------------------------- project profile
class SystemRule(_Frozen):
    """Maps an analysis-system title to a geometric variant (first matching rule wins)."""

    match: str = Field(description="Regular expression tested against the system title, case-insensitive")
    variant: str
    theta_deg: float | None = None
    mesh: str | None = None

    @field_validator("match")
    @classmethod
    def _compiles(cls, v: str) -> str:
        _compile(v)
        return v


class GeometryProfile(_Frozen):
    """Unit-cell geometry (thesis Table 3.1); legs are square frusta with a fixed cold base."""

    a_cold_mm: float = Field(1.4, gt=0)
    height_mm: float = Field(1.4, gt=0)
    gap_mm: float = Field(1.0, ge=0)
    t_cu_mm: float = Field(0.2, gt=0)
    t_ceramic_mm: float = Field(0.8, gt=0)
    c3d: float = Field(0.5, ge=0, description="Frustum correction R = R_1D (1 + c3d tan²θ), see docs/model.md")

    def a_hot_mm(self, theta_deg: float) -> float:
        return self.a_cold_mm - 2.0 * self.height_mm * math.tan(math.radians(theta_deg))


class MaterialProfile(_Frozen):
    """Constant material data (thesis Table 3.2)."""

    alpha_p_v_k: float = 2.0e-4
    alpha_n_v_k: float = -2.0e-4
    rho_te_ohm_m: float = Field(1.0e-5, gt=0)
    kappa_te_w_mk: float = Field(1.5, gt=0)
    rho_cu_ohm_m: float = Field(1.7e-8, gt=0)
    temperature_dependent: bool = Field(False, description="True if α(T), ρ(T), κ(T) tables were used in ANSYS")

    @property
    def alpha_pn_v_k(self) -> float:
        return self.alpha_p_v_k - self.alpha_n_v_k


class ContactProfile(_Frozen):
    """Finite contact conductances on the four barrier-semiconductor interfaces (thesis Section 3.7.2)."""

    tcc_w_m2k: float = Field(1.0e5, gt=0)
    ecc_s_m2: float = Field(1.0e9, gt=0)


class MeshLevel(_Frozen):
    """Size of one mesh of the project; r between two meshes comes from node counts if both have them."""

    element_size_mm: float = Field(gt=0, description="Representative element size in the legs")
    nodes: int | None = Field(None, gt=0, description="Node count of the whole model (optional)")


class TwinProfile(_Frozen):
    """Settings of the 1D twin that is run for every system (``twin.py``).

    The factors scale the passive (ceramic + Cu) thermal resistance on each side and add a series
    resistance; defaults are uncalibrated physics.  Tolerances are relative and decide the QA verdicts.
    """

    ceramic_k_w_mk: float = Field(35.0, gt=0)
    cu_k_w_mk: float = Field(400.0, gt=0)
    hot_side_factor: float = Field(1.0, gt=0, description="Calibration factor on the hot-side passive resistance")
    cold_side_factor: float = Field(1.0, gt=0, description="Calibration factor on the cold-side passive resistance")
    r_el_extra_mohm: float = Field(0.0, ge=-50, le=500, description="Series resistance correction (leads, crowding)")
    n_elements: int = Field(30, ge=8, le=200, description="Finite volumes per leg")
    tol_u_oc: float = Field(0.02, gt=0, lt=1, description="Relative tolerance on U_oc")
    tol_dt_te: float = Field(0.03, gt=0, lt=1, description="Relative tolerance on ΔT_TE at open circuit")
    tol_p_max: float = Field(0.05, gt=0, lt=1, description="Relative tolerance on P_max (FEM vs 1D)")
    tol_point: float = Field(0.05, gt=0, lt=1, description="Relative tolerance on U, P and Q at single points")


class CaseCondition(_Frozen):
    """Thermal boundary condition of one analysis system (thesis Table 3.3)."""

    bc: BoundaryCondition
    t_hot_face_c: float | None = None
    q_hot_w: float | None = Field(None, gt=0)
    t_cold_face_c: float
    current_a: float | None = Field(None, description="Operating current of single-point sources (outline panels)")

    @model_validator(mode="after")
    def _complete(self) -> CaseCondition:
        if self.bc is BoundaryCondition.TT:
            if self.t_hot_face_c is None:
                raise ValueError("BC-TT needs t_hot_face_c")
            if self.t_hot_face_c <= self.t_cold_face_c:
                raise ValueError("BC-TT needs t_hot_face_c > t_cold_face_c")
        if self.bc is BoundaryCondition.QT and self.q_hot_w is None:
            raise ValueError("BC-QT needs q_hot_w")
        return self


class ProjectProfile(_Frozen):
    """Everything project-specific: exact ANSYS names, system titles, geometry, materials, conditions.

    Exact names are matched case-insensitively after whitespace normalization; ``patterns`` are regular
    expressions used only when no exact alias matches (reported with a lower confidence).
    """

    name: str
    description: str = ""
    aliases: dict[QuantityKey, list[str]]
    patterns: dict[QuantityKey, list[str]] = {}
    ignore: list[str] = Field([], description="Exact names deliberately not mapped (diagnostic probes)")
    systems: list[SystemRule] = []
    default_condition: CaseCondition | None = None
    conditions: dict[str, CaseCondition] = Field({}, description="Per system letter; overrides the default")
    geometry: GeometryProfile = GeometryProfile()
    materials: MaterialProfile = MaterialProfile()
    contacts: ContactProfile = ContactProfile()
    twin: TwinProfile = TwinProfile()
    meshes: dict[str, MeshLevel] = Field({}, description="Mesh label (from the system rules) → size, for the GCI")
    reference_variant: str = "A0"

    @field_validator("patterns")
    @classmethod
    def _patterns_compile(cls, v: dict[QuantityKey, list[str]]) -> dict[QuantityKey, list[str]]:
        for pats in v.values():
            for p in pats:
                _compile(p)
        return v

    @model_validator(mode="after")
    def _unique_aliases(self) -> ProjectProfile:
        seen: dict[str, QuantityKey] = {}
        for key, names in self.aliases.items():
            for n in names:
                folded = _fold(n)
                if folded in seen and seen[folded] is not key:
                    raise ValueError(f"alias '{n}' is mapped to both {seen[folded]} and {key}")
                seen[folded] = key
        return self

    @cached_property
    def alias_index(self) -> dict[str, QuantityKey]:
        return {_fold(n): key for key, names in self.aliases.items() for n in names}

    @cached_property
    def ignore_index(self) -> frozenset[str]:
        return frozenset(_fold(n) for n in self.ignore)

    def condition_for(self, letter: str | None) -> CaseCondition | None:
        if letter is not None and letter in self.conditions:
            return self.conditions[letter]
        return self.default_condition

    def classify_system(self, title: str) -> SystemRule | None:
        for rule in self.systems:
            if re.search(rule.match, title, flags=re.IGNORECASE):
                return rule
        return None


def _fold(name: str) -> str:
    return " ".join(name.split()).casefold()


def _compile(pattern: str) -> re.Pattern[str]:
    try:
        return re.compile(pattern)
    except re.error as exc:  # surface as a validation error, not a crash
        raise ValueError(f"invalid regular expression {pattern!r}: {exc}") from exc


PROFILE_DIR = Path(__file__).with_name("profiles")


def load_profile(name_or_path: str = "thesis-2026") -> ProjectProfile:
    """Load a bundled profile by name (``profiles/<name>.json``) or any JSON file by path."""
    path = Path(name_or_path)
    if not path.suffix:
        path = PROFILE_DIR / f"{name_or_path}.json"
    return ProjectProfile.model_validate(json.loads(path.read_text(encoding="utf-8")))


# ------------------------------------------------------------------------------------- nodal inputs
Axis = Literal["x", "y", "z"]
View = Literal["front", "back", "right", "left", "top", "bottom"]


class ImageCalibration(_Frozen):
    """Two reference points: pixel coordinates (origin top-left, y down) and model coordinates [mm].

    Model coordinates are the horizontal/vertical coordinates of the view (see ``nodal.VIEW_AXES``).
    """

    width_px: int = Field(gt=0)
    height_px: int = Field(gt=0)
    p1_px: tuple[float, float]
    p1_model: tuple[float, float]
    p2_px: tuple[float, float]
    p2_model: tuple[float, float]

    @model_validator(mode="after")
    def _distinct(self) -> ImageCalibration:
        if self.p1_px[0] == self.p2_px[0] or self.p1_px[1] == self.p2_px[1]:
            raise ValueError("reference points must differ in both pixel coordinates")
        if self.p1_model[0] == self.p2_model[0] or self.p1_model[1] == self.p2_model[1]:
            raise ValueError("reference points must differ in both model coordinates")
        return self

    @property
    def scale(self) -> tuple[float, float]:
        """Model units per pixel along x and y (y is usually negative: pixel rows grow downwards)."""
        sx = (self.p2_model[0] - self.p1_model[0]) / (self.p2_px[0] - self.p1_px[0])
        sy = (self.p2_model[1] - self.p1_model[1]) / (self.p2_px[1] - self.p1_px[1])
        return sx, sy


class NodalFileSpec(_Frozen):
    """One Mechanical nodal export and the body (or named selection) it was scoped to."""

    file: str
    body: str = Field(description="Body tag, e.g. 'leg_p', 'cu_hot', 'ceramic_hot'")
    quantity: NodalKey | None = Field(None, description="None = infer from the header")


class ImageSpec(BaseModel):
    """A contour screenshot listed in the manifest, optionally with the calibration of its camera."""

    model_config = ConfigDict(frozen=True, extra="ignore")
    file: str
    view: View | None = None
    quantity: NodalKey | None = None
    calibration: ImageCalibration | None = None
    calibration_source: Literal["exact", "camera"] = Field(
        "camera", description="'camera' = set by the export script's camera (verify); 'exact' = rendered from the model"
    )


class NodalManifest(BaseModel):
    """``manifest.json`` written by ``tools/ansys/teglab_export_nodal.py`` (unknown keys are ignored)."""

    model_config = ConfigDict(frozen=True, extra="ignore")
    generator: str | None = None
    system: str | None = Field(None, description="Workbench system title, e.g. 'Dense mesh copy C 10st'")
    analysis: str | None = None
    axis: Axis | None = Field(None, description="Stack axis; None = infer from the geometry")
    synthetic: bool = Field(False, description="Generated by teglab.pipeline.synthetic, not by ANSYS")
    files: list[NodalFileSpec] = []
    images: list[ImageSpec] = []


# ------------------------------------------------------------------------------------------ results
class OpenCircuit(BaseModel):
    u_oc_v: float | None = None
    dt_te_k: float | None = None
    q_hot_w: float | None = None
    k_te_w_k: float | None = Field(None, description="Pair thermal conductance Q_h/ΔT_TE at I = 0")
    alpha_eff_v_k: float | None = Field(None, description="U_oc/ΔT_TE - effective Seebeck coefficient of the pair")
    method: str = ""


class ThermalSplit(BaseModel):
    """Where the imposed temperature difference goes at open circuit (thesis Table 4.3)."""

    imposed_dt_k: float | None = None
    dt_te_k: float | None = None
    dt_hot_passive_k: float | None = None
    dt_cold_passive_k: float | None = None
    passive_share: float | None = Field(None, description="1 − ΔT_TE/ΔT_imposed")
    r_th_hot_k_w: float | None = None
    r_th_cold_k_w: float | None = None
    r_th_legs_k_w: float | None = None
    contact_dt_hot_expected_k: float | None = Field(None, description="Q/(TCC·2A_h) - drop across the hot contacts")
    contact_dt_cold_expected_k: float | None = None


class ResistanceBreakdown(BaseModel):
    """Internal resistance separated into what a regression sees and what is actually ohmic."""

    apparent_ohm: float | None = Field(None, description="−dU/dI of a straight line through all points")
    apparent_r2: float | None = None
    apparent_is_internal: bool = Field(False, description="True only for a linear source (BC-TT, R² ≈ 1)")
    ohmic_ohm: float | None = Field(None, description="median of (α_eff·ΔT_TE(I) − U)/I over I > 0")
    ohmic_spread: float | None = None
    ohmic_points: int = 0
    legs_ohm: float | None = None
    copper_ohm: float | None = None
    contacts_expected_ohm: float | None = Field(None, description="2/(ECC·A_h) + 2/(ECC·A_c)")
    contacts_isolated_ohm: float | None = Field(None, description="ohmic − legs − copper")
    thermal_feedback_ohm: float | None = Field(None, description="apparent − ohmic (ΔT_TE drop under current)")
    contact_verdict: Literal["active", "inactive", "inconclusive", "unknown"] = "unknown"


class MaxPowerFit(BaseModel):
    p_max_w: float | None = None
    i_opt_a: float | None = None
    method: str = ""
    bracketed: bool = False
    suggested_currents_a: list[float] = []


class AnalyticPower(BaseModel):
    """Closed-form references at constant properties, legs only (no ceramics, copper or contacts)."""

    frozen_w: float | None = Field(None, description="α²Q²/(4K²R): open-circuit ΔT held fixed (upper bound)")
    exact_w: float | None = Field(None, description="Exact BC-QT optimum incl. Peltier and Joule feedback")
    exact_i_opt_a: float | None = None
    exact_dt_te_k: float | None = None
    tt_w: float | None = Field(None, description="α²ΔT²/(4R) at the imposed ΔT (BC-TT)")
    k_w_k: float | None = None
    r_ohm: float | None = None
    alpha_v_k: float | None = None


QaStatus = Literal["pass", "warn", "fail", "skip"]


class QaGate(BaseModel):
    """One automatic check of a system: what was tested, against which limit, and the verdict."""

    code: str
    title: str
    status: QaStatus
    value: float | None = None
    limit: float | None = None
    percent: bool = Field(True, description="value and limit are fractions to be shown in percent")
    detail: str = ""


class TwinMetric(BaseModel):
    """One system-level quantity: ANSYS value, 1D twin value and their relative deviation."""

    key: str
    label: str
    unit: str = Field(description="Display unit")
    scale: float = Field(1.0, description="Multiply SI values by this for the display unit")
    ansys: float | None = None
    model: float | None = None
    deviation: float | None = Field(None, description="(model − ANSYS)/|ANSYS|")
    tolerance: float | None = None
    status: QaStatus = "skip"


class TwinPoint(BaseModel):
    """One design point solved by the 1D twin at the ANSYS current."""

    design_point: str
    current_a: float
    voltage_ansys_v: float | None = None
    voltage_model_v: float | None = None
    power_ansys_w: float | None = None
    power_model_w: float | None = None
    q_hot_ansys_w: float | None = None
    q_hot_model_w: float | None = None
    q_cold_ansys_w: float | None = None
    q_cold_model_w: float | None = None
    dt_te_ansys_k: float | None = None
    dt_te_model_k: float | None = None
    worst_deviation: float | None = Field(
        None, description="Largest deviation of U, P (on the U_oc, P_max scale) and Q (relative) at this point"
    )


class TwinSystem(BaseModel):
    """The 1D twin of one system: same geometry, materials, contacts and boundary condition as in ANSYS."""

    status: Literal["ok", "skipped", "failed"] = "skipped"
    note: str = ""
    calibrated: bool = Field(False, description="True if the twin used factors fitted to these ANSYS results")
    a_hot_mm: float | None = None
    a_cold_mm: float | None = None
    metrics: list[TwinMetric] = []
    points: list[TwinPoint] = []


class SystemReport(BaseModel):
    system: str
    title: str = ""
    variant: str | None = None
    theta_deg: float | None = None
    mesh: str | None = None
    bc: BoundaryCondition | None = None
    n_points: int = 0
    open_circuit: OpenCircuit = OpenCircuit()
    thermal: ThermalSplit = ThermalSplit()
    resistance: ResistanceBreakdown = ResistanceBreakdown()
    max_power: MaxPowerFit = MaxPowerFit()
    analytic: AnalyticPower = AnalyticPower()
    twin: TwinSystem = TwinSystem()
    qa: list[QaGate] = []
    issues: list[Issue] = []


class VariantComparison(BaseModel):
    """One variant relative to the reference variant (thesis Table 5.3 view, plus attribution)."""

    variant: str
    system: str
    theta_deg: float | None = None
    k_ratio: float | None = None
    r_ratio: float | None = None
    volume_ratio: float | None = None
    dt_te_ratio: float | None = None
    p_frozen_ratio: float | None = Field(None, description="BC-QT, α²Q²/(4K²R) - K and R effects only")
    p_exact_ratio: float | None = None
    p_measured_ratio: float | None = None
    p_tt_ratio: float | None = Field(None, description="Same legs at a fixed ΔT (BC-TT): only the R penalty")
    attribution: dict[str, float] = Field({}, description="Multiplicative factors of the frozen BC-QT gain")


ConvergenceStatus = Literal["converged", "check", "not-converged", "oscillatory", "identical"]


class GridConvergence(BaseModel):
    """GCI of one quantity of one variant over two or three meshes (Roache; Celik et al. 2008)."""

    variant: str
    bc: BoundaryCondition | None = None
    key: str
    label: str
    unit: str
    scale: float = 1.0
    current_a: float | None = Field(None, description="Design-point current; None = system-level quantity")
    systems: list[str] = Field(description="Coarse → fine")
    meshes: list[str]
    element_size_mm: list[float]
    values: list[float]
    r: float = Field(description="Refinement ratio of the two finest meshes")
    p_observed: float | None = None
    p_used: float
    fs: float
    gci_fine: float = Field(description="Relative uncertainty band of the finest mesh")
    f_extrapolated: float | None = None
    status: ConvergenceStatus


class CalibrationSettings(_Frozen):
    """Fit the passive-layer factors of the 1D twin to ANSYS systems, then rerun every twin with them."""

    systems: list[str] = Field([], description="System letters to fit on; empty = systems of the reference variant")
    params: list[Literal["hot_side_factor", "cold_side_factor", "r_el_extra_mohm"]] = [
        "hot_side_factor",
        "cold_side_factor",
        "r_el_extra_mohm",
    ]


class TwinCalibration(BaseModel):
    """Result of the twin calibration: fitted factors and the misfit on the fit and the validation systems."""

    fitted_on: list[str]
    validated_on: list[str] = []
    params: list[str]
    initial: dict[str, float]
    fitted: dict[str, float]
    rms_fit_before: float | None = None
    rms_fit_after: float | None = None
    rms_validation_before: float | None = None
    rms_validation_after: float | None = None
    n_residuals: int = 0
    success: bool = False
    message: str = ""


class PipelineReport(BaseModel):
    profile: str
    sources: list[SourceSummary] = []
    parameters: list[ResolvedParameter] = []
    systems: list[SystemReport] = []
    comparison: list[VariantComparison] = []
    convergence: list[GridConvergence] = []
    calibration: TwinCalibration | None = None
    issues: list[Issue] = []


# ------------------------------------------------------------------------------------ API requests
PROFILE_NAME_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"


class RunSettings(_Frozen):
    """Everything ``POST /api/pipeline/run`` needs besides the files (sent as one JSON form field)."""

    profile: str = Field("thesis-2026", pattern=PROFILE_NAME_PATTERN, description="Bundled profile name")
    default_condition: CaseCondition | None = Field(None, description="None = the profile's default")
    conditions: dict[str, CaseCondition] = Field({}, description="Per system letter; overrides the default")
    variants: dict[str, str] = Field({}, description="System letter → variant, e.g. {'B': 'A5'}")
    meshes: dict[str, str] = Field({}, description="System letter → mesh label of the profile, e.g. {'C': 'C1'}")
    reference_variant: str | None = None
    calibration: CalibrationSettings | None = Field(None, description="None = twin with the profile factors")


class StackLayer(_Frozen):
    """One layer of the stack: bodies that share the same extent along the stack axis."""

    name: str = Field(min_length=1, max_length=64)
    bodies: list[str] = Field(min_length=1)
    active: bool = Field(False, description="Thermoelectric legs (the ΔT_TE layer)")


class LayerStack(_Frozen):
    """Layers ordered from the hot face to the cold face."""

    axis: Axis
    layers: list[StackLayer] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_bodies(self) -> LayerStack:
        seen: set[str] = set()
        for layer in self.layers:
            for body in layer.bodies:
                if body in seen:
                    raise ValueError(f"body '{body}' is assigned to more than one layer")
                seen.add(body)
        return self


class ProfileRequest(_Frozen):
    """``POST /api/pipeline/nodal/{id}/profile``: evaluate the layer profile and the contact checks."""

    stack: LayerStack | None = Field(None, description="None = the stack proposed for the dataset")
    tol_mm: float = Field(2e-3, gt=0, le=0.1, description="Distance tolerance for faces and interfaces")
    contacts: ContactProfile | None = Field(None, description="Expected TCC/ECC; None = the profile's values")
    profile: str = Field("thesis-2026", pattern=PROFILE_NAME_PATTERN)


class PointsRequest(_Frozen):
    """``POST /api/pipeline/nodal/{id}/points``: nodal samples projected onto a standard view."""

    view: View = "front"
    quantity: NodalKey = NodalKey.TEMPERATURE
    bodies: list[str] | None = Field(None, description="None = every body")
    surface_only: bool = Field(True, description="Only the face of each body that is visible in the view")
    max_points: int = Field(20_000, ge=100, le=200_000)


# ---------------------------------------------------------------------------------- nodal results
ContactVerdict = Literal["as-specified", "bonded", "deviates", "not-evaluated"]


class NodalFileInfo(BaseModel):
    file: str
    body: str
    quantities: list[NodalKey]
    n_rows: int
    averaged: bool
    notes: list[str] = []


class BodyInfo(BaseModel):
    tag: str
    n_nodes: int
    quantities: list[NodalKey]
    min_mm: tuple[float, float, float]
    max_mm: tuple[float, float, float]
    t_mean_c: float | None = None


class DatasetImage(BaseModel):
    name: str
    url: str = Field(description="Relative API path of the PNG")
    view: View | None = None
    quantity: NodalKey | None = None
    width_px: int
    height_px: int
    calibration: ImageCalibration | None = None
    calibration_source: Literal["exact", "camera", "none"] = "none"


class NodalDatasetInfo(BaseModel):
    dataset_id: str
    title: str | None = None
    synthetic: bool = False
    files: list[NodalFileInfo] = []
    bodies: list[BodyInfo] = []
    quantities: list[NodalKey] = []
    proposed_stack: LayerStack | None = None
    images: list[DatasetImage] = []
    issues: list[Issue] = []


class LayerRow(BaseModel):
    """Face-averaged temperatures of one layer, or the jump across one interface."""

    kind: Literal["layer", "interface"]
    name: str
    active: bool = False
    t_hot_c: float | None = None
    t_cold_c: float | None = None
    dt_k: float
    area_mm2: float
    s_hot_mm: float = Field(description="Position of the hot face, measured from the hot face of the stack")
    s_cold_mm: float


class LayerSplit(BaseModel):
    """Where the imposed temperature difference goes (the nodal counterpart of ``ThermalSplit``)."""

    dt_hot_passive_k: float
    dt_te_k: float
    dt_cold_passive_k: float
    dt_total_k: float
    passive_share: float | None = None
    dt_contacts_k: float


class ContactCheck(BaseModel):
    """Effective conductances of one body-to-body interface, measured from the nodal jumps.

    TCC_eff = q/ΔT uses the heat flux on the metal side (on the thermoelectric side the conductive flux
    differs by the Peltier heat); ECC_eff = J/ΔV uses the current density on the leg side (in the metal the
    current turns sideways).  Both are area-weighted averages over the smaller face.
    """

    interface: str
    hot_body: str
    cold_body: str
    at_active_layer: bool = Field(description="A barrier-semiconductor contact (leg end)")
    level_mm: float
    area_mm2: float
    dt_k: float | None = None
    heat_flux_w_m2: float | None = None
    tcc_eff_w_m2k: float | None = Field(None, description="None if ΔT ≈ 0 (bonded) or no heat flux data")
    dv_v: float | None = None
    current_density_a_m2: float | None = None
    ecc_eff_s_m2: float | None = None
    thermal_verdict: ContactVerdict = "not-evaluated"
    electrical_verdict: ContactVerdict = "not-evaluated"


class LayerProfile(BaseModel):
    stack: LayerStack
    rows: list[LayerRow]
    split: LayerSplit | None = None
    contacts: list[ContactCheck] = []
    expected: ContactProfile
    issues: list[Issue] = []


class NodalPoints(BaseModel):
    """Nodal samples in the 2D coordinates of a standard view (h to the right, v up) [mm]."""

    view: View
    quantity: NodalKey
    unit: str
    h_label: str
    v_label: str
    h_mm: list[float]
    v_mm: list[float]
    value: list[float]
    body_index: list[int]
    bodies: list[str]
    h_range_mm: tuple[float, float] = Field(description="Extent of the selected bodies in this view")
    v_range_mm: tuple[float, float]
    value_range: tuple[float, float]
    n_total: int
    n_returned: int


class OverlayPlacement(BaseModel):
    """Plotly ``layout.images`` placement of a calibrated picture (the client adds source and opacity)."""

    x: float
    y: float
    sizex: float
    sizey: float
    xanchor: Literal["left"] = "left"
    yanchor: Literal["top"] = "top"
    sizing: Literal["stretch"] = "stretch"
    layer: Literal["below"] = "below"
    mm_per_px: tuple[float, float]


class ExampleInfo(BaseModel):
    name: str
    title: str
    description: str
    kind: Literal["parameters", "nodal"]
    size_bytes: int


class DeleteResult(BaseModel):
    deleted: bool
