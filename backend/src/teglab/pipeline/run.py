"""Orchestration: files in, ``PipelineReport`` out.

    read (sniff → reader) ─▶ canonical frame (map, convert, validate) ─▶ per-system summary
        ─▶ analytic references ─▶ variant comparison ─▶ report with issues

Every stage is a pure function of the previous stage's output, so each can be tested in isolation and
the whole run is reproducible from the uploaded bytes plus the profile.  Problems never abort the
run; they become ``Issue`` records next to the numbers they concern.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .compare import cold_side_signature, compare_variants, pick_reference
from .extract import (
    LINEAR_R2,
    geometry_table,
    max_power_by_system,
    open_circuit,
    resistance_table,
    suggest_currents,
    thermal_split,
)
from .frames import CanonicalData, build_canonical, resolve_tables
from .maxpower import p_max_frozen, p_max_qt_exact, p_max_tt
from .qa import qa_gates
from .readers import NodalTable, RawTable, read_source
from .schemas import (
    AnalyticPower,
    CaseCondition,
    Issue,
    Level,
    MaxPowerFit,
    OpenCircuit,
    PipelineReport,
    ProjectProfile,
    ResistanceBreakdown,
    ResolvedParameter,
    SourceFormat,
    SourceSummary,
    SystemReport,
    ThermalSplit,
)
from .twin import twin_system

__all__ = ["PipelineResult", "RunOptions", "inspect_files", "run_pipeline", "summarize"]

BALANCE_LIMIT = 1e-3  # thesis Section 3.5.2: energy-balance error ≤ 0.1 %


@dataclass(frozen=True)
class RunOptions:
    profile: ProjectProfile
    default_condition: CaseCondition | None = None  # overrides the profile default for every system
    conditions: Mapping[str, CaseCondition] = field(default_factory=dict)  # per system letter
    variants: Mapping[str, str] = field(default_factory=dict)  # system letter -> variant, e.g. {"B": "A5"}
    reference_variant: str | None = None


@dataclass
class PipelineResult:
    report: PipelineReport
    canonical: CanonicalData | None = None
    summary: pd.DataFrame | None = None
    nodal: list[NodalTable] = field(default_factory=list)


# ------------------------------------------------------------------------------------------ reading
def _read_all(
    files: Sequence[tuple[str, bytes]],
) -> tuple[list[RawTable], list[NodalTable], list[SourceSummary], list[Issue]]:
    tables, nodal, sources, issues = [], [], [], []
    for name, data in files:
        try:
            parsed = read_source(data, name)
        except ValueError as exc:
            issues.append(Issue(level=Level.ERROR, code="unreadable", message=str(exc), source=name))
            continue
        if isinstance(parsed, NodalTable):
            nodal.append(parsed)
            sources.append(
                SourceSummary(
                    source=name,
                    format=SourceFormat.MECHANICAL_NODAL,
                    confidence=parsed.confidence,
                    n_rows=len(parsed.frame),
                    notes=parsed.notes,
                )
            )
        else:
            tables.append(parsed)
            sources.append(
                SourceSummary(
                    source=name,
                    format=parsed.format,
                    confidence=parsed.confidence,
                    n_parameters=len(parsed.parameters),
                    n_rows=len(parsed.values),
                    systems=parsed.systems,
                    notes=parsed.notes,
                )
            )
    return tables, nodal, sources, issues


def inspect_files(files: Sequence[tuple[str, bytes]], profile: ProjectProfile) -> PipelineReport:
    """Structure discovery only: formats, systems and how every parameter would be interpreted."""
    tables, _, sources, issues = _read_all(files)
    _, per_table = resolve_tables(tables, profile, issues)
    params: list[ResolvedParameter] = [p for resolved in per_table for p in resolved]
    return PipelineReport(profile=profile.name, sources=sources, parameters=params, issues=issues)


# ---------------------------------------------------------------------------------------- analysis
def summarize(frame: pd.DataFrame, systems: pd.DataFrame, profile: ProjectProfile) -> pd.DataFrame:
    """All per-system numbers in one table (index: system letter)."""
    oc = open_circuit(frame).reindex(systems.index)
    geom = geometry_table(systems, profile)
    res = resistance_table(frame, oc, systems, geom, profile.materials.alpha_pn_v_k)
    thermal = thermal_split(oc, systems, geom)
    power = max_power_by_system(frame, res)

    alpha = profile.materials.alpha_pn_v_k
    q = systems["imposed_q_hot_w"].to_numpy(dtype=float)
    t_cold_k = systems["imposed_t_cold_face_c"].to_numpy(dtype=float) + 273.15
    k1, r1 = geom["k_legs_1d_w_k"].to_numpy(), geom["r_legs_1d_ohm"].to_numpy()
    exact_p, exact_i, exact_dt = p_max_qt_exact(alpha, np.nan_to_num(q, nan=0.0), k1, r1, t_cold_k)
    has_q = np.isfinite(q)
    dt_imposed = (systems["imposed_t_hot_face_c"] - systems["imposed_t_cold_face_c"]).to_numpy(dtype=float)

    s = systems.copy()
    s = s.join(oc.drop(columns=["q_cold_w"]).rename(columns={"method": "oc_method"}))
    s = s.join(geom)
    s = s.join(res)
    s = s.join(thermal.drop(columns=["dt_te_k"]))
    s = s.join(power.rename(columns={"method": "p_method"}))
    s["q_hot_w"] = s["q_hot_w"].fillna(s["imposed_q_hot_w"])
    s["alpha_v_k"] = alpha
    s["k_1d_w_k"] = k1
    s["r_1d_ohm"] = r1
    s["p_frozen_w"] = np.where(has_q, p_max_frozen(alpha, np.nan_to_num(q), k1, r1), np.nan)
    s["p_exact_w"] = np.where(has_q, exact_p, np.nan)
    s["i_opt_exact_a"] = np.where(has_q, exact_i, np.nan)
    s["dt_exact_k"] = np.where(has_q, exact_dt, np.nan)
    s["p_tt_w"] = np.where(np.isfinite(dt_imposed), p_max_tt(alpha, np.nan_to_num(dt_imposed), r1), np.nan)
    balance = frame["balance_rel"].abs().groupby(level="system").max()
    s["balance_rel_max"] = balance.reindex(s.index)
    s["n_points"] = frame.groupby(level="system").size().reindex(s.index).fillna(0).astype(int)
    return s


def _f(value: object) -> float | None:
    try:
        v = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return v if np.isfinite(v) else None


def _system_issues(letter: str, r: pd.Series, profile: ProjectProfile) -> list[Issue]:
    out: list[Issue] = []

    def add(level: Level, code: str, message: str) -> None:
        out.append(Issue(level=level, code=code, message=message, system=letter))

    verdict = r.get("contact_verdict")
    if verdict in ("inactive", "active", "inconclusive"):
        detail = (
            f"R_ohmic {r['ohmic_ohm'] * 1e3:.2f} mΩ = legs {r['legs_ohm'] * 1e3:.2f} "
            f"+ copper {r['copper_ohm'] * 1e3:.2f} + contacts {r['contacts_isolated_ohm'] * 1e3:.2f} mΩ; "
            f"with ECC = {profile.contacts.ecc_s_m2:.3g} S/m² the four contacts should add "
            f"{r['contacts_expected_ohm'] * 1e3:.2f} mΩ"
        )
        if verdict == "inactive":
            add(
                Level.WARNING,
                "contacts-inactive",
                f"{detail} - the data behave like ideal (bonded) contacts; check Connections → Contact Region → "
                "Electric and Thermal Conductance = Manual in this system",
            )
        else:
            add(Level.INFO, f"contacts-{verdict}", detail)
    if pd.notna(r.get("apparent_ohm")):
        if r.get("apparent_is_thevenin"):
            fb = r.get("thermal_feedback_ohm")
            extra = (
                f"; {fb * 1e3:.2f} mΩ of it is thermal feedback (ΔT_TE drops under current), not ohmic"
                if pd.notna(fb)
                else ""
            )
            add(
                Level.INFO,
                "linear-source",
                f"U(I) is linear (R² = {r['apparent_r2']:.6f}); "
                f"load-matching R = {r['apparent_ohm'] * 1e3:.2f} mΩ{extra}",
            )
        elif r.get("apparent_r2", 1.0) < LINEAR_R2:
            add(
                Level.INFO,
                "curved-load-curve",
                f"U(I) is curved (R² = {r['apparent_r2']:.5f}); the regression slope "
                f"{r['apparent_ohm'] * 1e3:.2f} mΩ is not an internal resistance - use R_ohmic",
            )
    if not r.get("bracketed") and pd.notna(r.get("p_max_w")):
        center = r.get("i_opt_exact_a") if r.get("bc") == "QT" else r.get("i_opt_a")
        currents = suggest_currents(float(center)) if pd.notna(center) else []
        hint = f" Add design points at {', '.join(f'{c:g}' for c in currents)} A." if currents else ""
        add(
            Level.WARNING,
            "pmax-not-bracketed",
            f"P_max {r['p_max_w'] * 1e3:.3f} mW is only the largest sample (lower bound).{hint}",
        )
    bal = r.get("balance_rel_max")
    if pd.notna(bal):
        level = Level.WARNING if bal > BALANCE_LIMIT else Level.INFO
        add(level, "energy-balance", f"largest |Q_h − Q_c − P|/Q_h = {bal * 100:.3f} % (criterion 0.1 %)")
    if r.get("hot_face_source") == "t_max":
        add(Level.INFO, "hot-face-from-tmax", "no hot-face probe: T_max at I = 0 used as the heated-face temperature")
    if profile.materials.temperature_dependent and pd.notna(r.get("ohmic_ohm")):
        add(Level.INFO, "alpha-varies", "α(T) tables: α_eff from the open circuit is approximate at other currents")
    return out


def _system_report(
    letter: str, r: pd.Series, profile: ProjectProfile, frame: pd.DataFrame | None = None
) -> SystemReport:
    points = (
        frame.xs(letter, level="system")
        if frame is not None and letter in frame.index.get_level_values("system")
        else None
    )
    twin = twin_system(r, points, profile)
    return SystemReport(
        system=letter,
        title=str(r.get("title") or ""),
        variant=r.get("variant") if pd.notna(r.get("variant")) else None,
        theta_deg=_f(r.get("theta_deg")),
        mesh=r.get("mesh") if pd.notna(r.get("mesh")) else None,
        bc=r.get("bc") if pd.notna(r.get("bc")) else None,
        n_points=int(r.get("n_points", 0)),
        open_circuit=OpenCircuit(
            u_oc_v=_f(r.get("u_oc_v")),
            dt_te_k=_f(r.get("dt_te_k")),
            q_hot_w=_f(r.get("q_hot_w")),
            k_te_w_k=_f(r.get("k_te_w_k")),
            alpha_eff_v_k=_f(r.get("alpha_eff_v_k")),
            method=str(r.get("oc_method") or ""),
        ),
        thermal=ThermalSplit(
            imposed_dt_k=_f(r.get("imposed_dt_k")),
            dt_te_k=_f(r.get("dt_te_k")),
            dt_hot_passive_k=_f(r.get("dt_hot_passive_k")),
            dt_cold_passive_k=_f(r.get("dt_cold_passive_k")),
            passive_share=_f(r.get("passive_share")),
            r_th_hot_k_w=_f(r.get("r_th_hot_k_w")),
            r_th_cold_k_w=_f(r.get("r_th_cold_k_w")),
            r_th_legs_k_w=_f(r.get("r_th_legs_k_w")),
            contact_dt_hot_expected_k=_f(r.get("contact_dt_hot_expected_k")),
            contact_dt_cold_expected_k=_f(r.get("contact_dt_cold_expected_k")),
        ),
        resistance=ResistanceBreakdown(
            apparent_ohm=_f(r.get("apparent_ohm")),
            apparent_r2=_f(r.get("apparent_r2")),
            apparent_is_internal=bool(r.get("apparent_is_thevenin", False)),
            ohmic_ohm=_f(r.get("ohmic_ohm")),
            ohmic_spread=_f(r.get("ohmic_spread")),
            ohmic_points=int(r.get("ohmic_points", 0) or 0),
            legs_ohm=_f(r.get("legs_ohm")),
            copper_ohm=_f(r.get("copper_ohm")),
            contacts_expected_ohm=_f(r.get("contacts_expected_ohm")),
            contacts_isolated_ohm=_f(r.get("contacts_isolated_ohm")),
            thermal_feedback_ohm=_f(r.get("thermal_feedback_ohm")),
            contact_verdict=r.get("contact_verdict") or "unknown",
        ),
        max_power=MaxPowerFit(
            p_max_w=_f(r.get("p_max_w")),
            i_opt_a=_f(r.get("i_opt_a")),
            method=str(r.get("p_method") or ""),
            bracketed=bool(r.get("bracketed", False)),
            suggested_currents_a=[]
            if r.get("bracketed")
            else suggest_currents(float(r.get("i_opt_exact_a") if r.get("bc") == "QT" else r.get("i_opt_a") or np.nan)),
        ),
        analytic=AnalyticPower(
            frozen_w=_f(r.get("p_frozen_w")),
            exact_w=_f(r.get("p_exact_w")),
            exact_i_opt_a=_f(r.get("i_opt_exact_a")),
            exact_dt_te_k=_f(r.get("dt_exact_k")),
            tt_w=_f(r.get("p_tt_w")),
            k_w_k=_f(r.get("k_1d_w_k")),
            r_ohm=_f(r.get("r_1d_ohm")),
            alpha_v_k=_f(r.get("alpha_v_k")),
        ),
        twin=twin,
        qa=qa_gates(r, twin, profile),
        issues=_system_issues(letter, r, profile),
    )


def run_pipeline(files: Sequence[tuple[str, bytes]], options: RunOptions) -> PipelineResult:
    profile = options.profile
    if options.default_condition is not None:
        profile = profile.model_copy(update={"default_condition": options.default_condition})
    tables, nodal, sources, issues = _read_all(files)
    report = PipelineReport(profile=profile.name, sources=sources, issues=issues)
    if not tables:
        if not nodal:
            report.issues.append(Issue(level=Level.ERROR, code="no-tables", message="no parameter table was read"))
        return PipelineResult(report, nodal=nodal)

    canonical = build_canonical(tables, profile, options.conditions, options.variants)
    report.parameters = canonical.catalog
    report.issues.extend(canonical.issues)
    if canonical.frame.empty:
        return PipelineResult(report, canonical, nodal=nodal)

    summary = summarize(canonical.frame, canonical.systems, profile)
    report.systems = [_system_report(str(letter), row, profile, canonical.frame) for letter, row in summary.iterrows()]
    reference = pick_reference(summary, options.reference_variant or profile.reference_variant)
    if reference is not None and summary["variant"].notna().sum() > 1:
        report.comparison = compare_variants(summary, reference)
        report.issues.extend(cold_side_signature(summary, reference))
    return PipelineResult(report, canonical, summary, nodal)
