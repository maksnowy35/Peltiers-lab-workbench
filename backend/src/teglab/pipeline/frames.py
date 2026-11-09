"""From raw tables to one canonical, validated DataFrame (vectorized).

The canonical design-point frame has one row per (system, design point) and one column per
``QuantityKey`` in canonical units, plus derived columns (ΔT_TE, P, η, R_L, energy-balance residual).

Pipeline inside this module::

    RawTable(s) ──resolve──▶ catalog (pid → key, factor, offset, sign, system)
                └─melt────▶ long (design point, pid, raw)
    long ⋈ catalog ──vectorized unit/sign map──▶ long (system, design point, key, value)
                    ──broadcast shared inputs──▶ ──pivot──▶ wide frame ──fill imposed BCs──▶ derive
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

import pandas as pd
from pydantic import BaseModel, ConfigDict

from ..units import conversion
from .keys import DIMENSION, INPUT_KEYS, QuantityKey
from .readers import RawTable
from .schemas import (
    BoundaryCondition,
    CaseCondition,
    Issue,
    Level,
    ParameterDef,
    ProjectProfile,
    ResolvedParameter,
    SystemRef,
)

__all__ = [
    "DESIGN_POINT_SPEC",
    "CanonicalData",
    "ColumnSpec",
    "build_canonical",
    "resolve_parameters",
    "resolve_tables",
    "system_table",
    "validate_frame",
]

Q = QuantityKey
THETA_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:st|deg|°)\b", re.IGNORECASE)
VARIANT_THETA_RE = re.compile(r"^A(\d+(?:\.\d+)?)$", re.IGNORECASE)


# ------------------------------------------------------------------------------ parameter mapping
def _fold(name: str) -> str:
    return " ".join(name.split()).casefold()


def resolve_parameters(params: Sequence[ParameterDef], profile: ProjectProfile) -> list[ResolvedParameter]:
    """Map exact ANSYS names to canonical keys: exact alias (1.0) > regex pattern (0.8) > unresolved."""
    resolved: list[ResolvedParameter] = []
    for p in params:
        folded = _fold(p.name)
        key: QuantityKey | None = None
        rule, confidence = "unresolved", 0.0
        if folded in profile.ignore_index:
            rule, confidence = "ignored", 1.0
        elif folded in profile.alias_index:
            key, rule, confidence = profile.alias_index[folded], "alias", 1.0
        else:
            for candidate, patterns in profile.patterns.items():
                if any(re.search(pat, folded, flags=re.IGNORECASE) for pat in patterns):
                    key, rule, confidence = candidate, "pattern", 0.8
                    break
        factor, offset, note = (1.0, 0.0, None) if key is None else conversion(p.unit, DIMENSION[key])
        direction = p.direction
        if key is not None and direction is None:
            direction = "input" if key in INPUT_KEYS else "output"
        resolved.append(
            ResolvedParameter(
                **p.model_dump(exclude={"direction"}),
                direction=direction,
                key=key,
                confidence=confidence,
                rule=rule,
                factor=factor,
                offset=offset,
                unit_note=note,
            )
        )
    return resolved


def _assign_systems(
    resolved: list[ResolvedParameter],
    known: Sequence[SystemRef],
    owning: set[str],
    issues: list[Issue],
    source: str,
) -> list[ResolvedParameter]:
    """Design-point CSVs carry no system ownership: split repeated output names into systems in order.

    Outputs already owned (from an Outline of All Parameters of the same project) are kept; only the
    remaining ones are distributed, k-th occurrence of a key -> k-th system.  Letters come first from
    known systems that own no parameter yet, then A, B, C, ... skipping letters in use.
    """
    unowned = [r for r in resolved if r.key is not None and r.key not in INPUT_KEYS and not r.system]
    if not unowned:
        return resolved
    occurrences: dict[QuantityKey, int] = {}
    slot: dict[str, int] = {}
    for r in unowned:
        slot[r.pid] = occurrences.get(r.key, 0)
        occurrences[r.key] = slot[r.pid] + 1
    n_systems = max(occurrences.values())
    letters = [s.letter for s in known if s.letter not in owning][:n_systems]
    candidates = (chr(ord("A") + k) for k in range(26))
    letters += [c for c in candidates if c not in owning and c not in letters][: n_systems - len(letters)]
    partial = len(unowned) < sum(1 for r in resolved if r.key is not None and r.key not in INPUT_KEYS)
    if n_systems > 1 or partial:
        issues.append(
            Issue(
                level=Level.WARNING if partial else Level.INFO,
                code="systems-inferred",
                message=f"system ownership inferred from repeated output names ({n_systems} system(s): "
                f"{', '.join(letters)}, in column order); add the Outline of All Parameters to make it exact",
                source=source,
            )
        )
    return [r.model_copy(update={"system": letters[slot[r.pid]]}) if r.pid in slot else r for r in resolved]


def _mapping_issues(resolved: Sequence[ResolvedParameter], source: str) -> list[Issue]:
    issues = []
    for r in resolved:
        if r.rule == "unresolved":
            issues.append(
                Issue(
                    level=Level.WARNING,
                    code="unresolved-parameter",
                    message=f"{r.pid} '{r.name}' [{r.unit or '-'}] has no canonical meaning; add it to the "
                    "profile aliases (or to 'ignore')",
                    source=source,
                )
            )
        if r.unit_note:
            issues.append(
                Issue(level=Level.WARNING, code="unit", message=f"{r.pid} '{r.name}': {r.unit_note}", source=source)
            )
    return issues


# ------------------------------------------------------------------------------- canonical frames
@dataclass
class CanonicalData:
    """Result of ``build_canonical``: the wide frame plus everything needed to explain it."""

    frame: pd.DataFrame  # index: (system, design_point); columns: QuantityKey values + derived
    systems: pd.DataFrame  # index: system letter; title, variant, theta_deg, mesh, bc, imposed BCs
    catalog: list[ResolvedParameter]
    issues: list[Issue] = field(default_factory=list)


def system_table(
    refs: Sequence[SystemRef],
    letters: Sequence[str],
    profile: ProjectProfile,
    conditions: Mapping[str, CaseCondition] | None = None,
    variants: Mapping[str, str] | None = None,
    meshes: Mapping[str, str] | None = None,
) -> pd.DataFrame:
    """One row per analysis system with its variant, taper angle, mesh and imposed boundary condition."""
    titles = {r.letter: r.title for r in refs}
    rows = []
    for letter in letters:
        title = titles.get(letter, "")
        rule = profile.classify_system(title) if title else None
        variant = (variants or {}).get(letter) or (rule.variant if rule else None)
        theta = rule.theta_deg if rule and rule.variant == variant else None
        if theta is None and variant and (m := VARIANT_THETA_RE.match(variant)):
            theta = float(m.group(1))
        if theta is None and (m := THETA_RE.search(title)):
            theta = float(m.group(1).replace(",", "."))
        cond = (conditions or {}).get(letter) or profile.condition_for(letter)
        rows.append(
            {
                "system": letter,
                "title": title,
                "variant": variant,
                "theta_deg": theta,
                "mesh": (meshes or {}).get(letter) or (rule.mesh if rule else None),
                "bc": cond.bc.value if cond else None,
                "imposed_t_hot_face_c": cond.t_hot_face_c if cond else None,
                "imposed_q_hot_w": cond.q_hot_w if cond else None,
                "imposed_t_cold_face_c": cond.t_cold_face_c if cond else None,
                "imposed_current_a": cond.current_a if cond else None,
            }
        )
    table = pd.DataFrame(rows).set_index("system")
    num = [c for c in table.columns if c.startswith("imposed_") or c == "theta_deg"]
    table[num] = table[num].astype("float64")
    return table


def _melt(values: pd.DataFrame) -> pd.DataFrame:
    """(design points × parameter IDs) matrix -> long (design_point, pid, raw)."""
    return (
        values.rename_axis("design_point")
        .reset_index()
        .melt(id_vars="design_point", var_name="pid", value_name="raw")
        .dropna(subset=["raw"])
    )


def _catalog_frame(resolved: Sequence[ResolvedParameter]) -> pd.DataFrame:
    mapped = [r for r in resolved if r.key is not None]
    return pd.DataFrame(
        {
            "pid": [r.pid for r in mapped],
            "key": [r.key.value for r in mapped if r.key is not None],
            "system": [r.system for r in mapped],
            "factor": [r.factor for r in mapped],
            "offset": [r.offset for r in mapped],
            "sign": [r.sign for r in mapped],
        }
    )


def _sign_convention(raw: pd.DataFrame, catalog: pd.DataFrame, issues: list[Issue], source: str) -> pd.DataFrame:
    """Reaction probes report heat *into* the body, so the cold side is negative. Canonical: both positive."""
    catalog = catalog.copy()
    for key in (Q.Q_HOT.value, Q.Q_COLD.value):
        rows = catalog["key"] == key
        if not rows.any():
            continue
        median = raw.loc[raw["pid"].isin(catalog.loc[rows, "pid"]), "raw"].median()
        if pd.notna(median) and median < 0:
            catalog.loc[rows, "sign"] = -1.0
            issues.append(
                Issue(
                    level=Level.INFO,
                    code="sign-normalized",
                    message=f"{key}: negative reaction values (heat leaving the body) flipped to positive",
                    source=source,
                )
            )
    return catalog


def _to_canonical_long(raw: pd.DataFrame, catalog: pd.DataFrame, source: str) -> pd.DataFrame:
    """Vectorized unit and sign map: value = (raw · factor + offset) · sign."""
    long = raw.merge(catalog, on="pid", how="inner")
    long["value"] = (long["raw"] * long["factor"] + long["offset"]) * long["sign"]
    long["source"] = source
    return long


def resolve_tables(
    tables: Sequence[RawTable], profile: ProjectProfile, issues: list[Issue]
) -> tuple[dict[str, SystemRef], list[list[ResolvedParameter]]]:
    """Resolve every table's parameters and their owning systems (P-IDs are project-global, so an
    outline of one system also fixes the ownership of the same IDs in a design-point table)."""
    known: dict[str, SystemRef] = {}
    for t in tables:
        for ref in t.systems:
            known.setdefault(ref.letter, ref)
    owner = {p.pid: p.system for t in tables for p in t.parameters if p.system}
    per_table = []
    for t in tables:
        resolved = [
            r.model_copy(update={"system": owner[r.pid]}) if not r.system and r.pid in owner else r
            for r in resolve_parameters(t.parameters, profile)
        ]
        per_table.append(_assign_systems(resolved, list(known.values()), set(owner.values()), issues, t.source))
    return known, per_table


def build_canonical(
    tables: Sequence[RawTable],
    profile: ProjectProfile,
    conditions: Mapping[str, CaseCondition] | None = None,
    variants: Mapping[str, str] | None = None,
    meshes: Mapping[str, str] | None = None,
) -> CanonicalData:
    """Merge any number of outline / design-point tables into the canonical wide frame."""
    issues: list[Issue] = []
    known, per_table = resolve_tables(tables, profile, issues)

    longs: list[pd.DataFrame] = []
    catalog_all: list[ResolvedParameter] = []
    for t, resolved in zip(tables, per_table, strict=True):
        issues.extend(_mapping_issues(resolved, t.source))
        catalog = _catalog_frame(resolved)
        if catalog.empty:
            catalog_all.extend(resolved)
            continue
        raw = _melt(t.values)
        catalog = _sign_convention(raw, catalog, issues, t.source)
        signs = dict(zip(catalog["pid"], catalog["sign"], strict=True))
        catalog_all.extend(r.model_copy(update={"sign": signs[r.pid]}) if r.pid in signs else r for r in resolved)
        longs.append(_to_canonical_long(raw, catalog, t.source))

    letters = sorted({r.system for r in catalog_all if r.system} | set(known)) or ["A"]
    systems = system_table(list(known.values()), letters, profile, conditions, variants, meshes)
    if not longs:
        issues.append(Issue(level=Level.ERROR, code="no-data", message="no parameter could be mapped"))
        return CanonicalData(pd.DataFrame(), systems, catalog_all, issues)

    # shared inputs (e.g. the current) apply to every system *of the same table* at the same design point
    owned_parts = []
    for part in longs:
        own = part[part["system"].notna()]
        shared = part[part["system"].isna()].drop(columns="system")
        targets = sorted(own["system"].unique()) or letters
        if not shared.empty:
            own = pd.concat([own, shared.merge(pd.DataFrame({"system": targets}), how="cross")], ignore_index=True)
        owned_parts.append(own)
    owned = _drop_outline_duplicates(pd.concat(owned_parts, ignore_index=True), issues)
    wide = owned.pivot_table(index=["system", "design_point"], columns="key", values="value", aggfunc="first")
    wide.columns = [str(c) for c in wide.columns]
    wide = wide.reindex(columns=[*wide.columns, *(k.value for k in Q if k.value not in wide.columns)])
    wide = _fill_imposed(wide, systems, issues)
    wide = derive_columns(wide)
    issues.extend(validate_frame(wide, DESIGN_POINT_SPEC))
    return CanonicalData(wide.sort_index(), systems, catalog_all, issues)


def _drop_outline_duplicates(long: pd.DataFrame, issues: list[Issue]) -> pd.DataFrame:
    """An outline shows the *current* design point, which a design-point table of the same system repeats."""
    is_outline = long["design_point"] == "Current"
    has_table = long.loc[~is_outline].groupby("system").size()
    duplicate = is_outline & long["system"].isin(has_table.index)
    if duplicate.any():
        for letter in sorted(long.loc[duplicate, "system"].unique()):
            issues.append(
                Issue(
                    level=Level.INFO,
                    code="outline-duplicate",
                    message="outline values ignored: the design-point table already covers this system",
                    system=letter,
                )
            )
    return long.loc[~duplicate]


def _fill_imposed(wide: pd.DataFrame, systems: pd.DataFrame, issues: list[Issue]) -> pd.DataFrame:
    """Boundary conditions that were prescribed (not probed) come from the case condition."""
    sys_of_row = wide.index.get_level_values("system")
    common = {Q.CURRENT.value: "imposed_current_a", Q.T_COLD_FACE.value: "imposed_t_cold_face_c"}
    per_bc = {
        BoundaryCondition.TT.value: {Q.T_HOT_FACE.value: "imposed_t_hot_face_c"},
        BoundaryCondition.QT.value: {Q.Q_HOT.value: "imposed_q_hot_w"},
    }
    filled: dict[tuple[str, float], list[str]] = {}
    for letter in systems.index:
        rows = sys_of_row == letter
        for column, imposed in (common | per_bc.get(systems.at[letter, "bc"], {})).items():
            value = systems.at[letter, imposed]
            if pd.isna(value):
                continue
            missing = rows & wide[column].isna().to_numpy()
            if missing.any():
                wide.loc[missing, column] = value
                filled.setdefault((column, float(value)), []).append(str(letter))
    for (column, value), letters in filled.items():
        issues.append(
            Issue(
                level=Level.INFO,
                code="imposed",
                message=f"{column} = {value:g} taken from the boundary condition (systems {', '.join(letters)})",
            )
        )
    return wide


def derive_columns(wide: pd.DataFrame) -> pd.DataFrame:
    """Vectorized per-row quantities (no loops, NaN-propagating)."""
    i = wide[Q.CURRENT.value]
    u = wide[Q.VOLTAGE.value]
    out = wide.assign(
        dt_te_k=wide[Q.T_LEG_HOT.value] - wide[Q.T_LEG_COLD.value],
        power_w=u * i,
    )
    out["efficiency"] = out["power_w"] / out[Q.Q_HOT.value]
    out["load_resistance_ohm"] = (u / i).where(i.abs() > 1e-12)
    out["balance_w"] = out[Q.Q_HOT.value] - out[Q.Q_COLD.value] - out["power_w"]
    out["balance_rel"] = out["balance_w"] / out[Q.Q_HOT.value]
    return out


# ------------------------------------------------------------------------------------ validation
class ColumnSpec(BaseModel):
    """Declarative column contract: physical bounds of a canonical column."""

    model_config = ConfigDict(frozen=True)
    name: str
    ge: float | None = None
    le: float | None = None


DESIGN_POINT_SPEC: tuple[ColumnSpec, ...] = (
    ColumnSpec(name=Q.CURRENT.value, ge=-1e3, le=1e3),
    ColumnSpec(name=Q.VOLTAGE.value, ge=-10.0, le=10.0),
    ColumnSpec(name=Q.Q_HOT.value, ge=-1e3, le=1e3),
    ColumnSpec(name=Q.Q_COLD.value, ge=-1e3, le=1e3),
    *(ColumnSpec(name=k.value, ge=-273.15, le=3000.0) for k in DIMENSION if DIMENSION[k] == "temperature"),
    ColumnSpec(name=Q.J_AVG.value, ge=0.0),
    ColumnSpec(name=Q.J_MAX.value, ge=0.0),
    ColumnSpec(name="dt_te_k", ge=-5.0),
)


def validate_frame(frame: pd.DataFrame, spec: Sequence[ColumnSpec]) -> list[Issue]:
    """Check bounds column-wise (vectorized); returns issues instead of raising."""
    issues = []
    for col in spec:
        if col.name not in frame.columns:
            continue
        values = frame[col.name]
        bad = pd.Series(False, index=frame.index)
        if col.ge is not None:
            bad |= values < col.ge
        if col.le is not None:
            bad |= values > col.le
        if bad.any():
            where = ", ".join(f"{s}/{dp}" for s, dp in frame.index[bad.to_numpy()][:5])
            issues.append(
                Issue(
                    level=Level.ERROR,
                    code="out-of-range",
                    message=f"{col.name} outside [{col.ge}, {col.le}] at {where}",
                )
            )
    return issues
