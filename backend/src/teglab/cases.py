"""Imported simulation / measurement cases in canonical units.

Canonical storage units: A, V, W, degC, A/m2, W/m3, mm, deg, W/(m2 K), S/m2, Ohm, s.
Heat flows are stored as positive magnitudes (heat in at the hot side, heat
out at the cold side), whatever sign convention the source used.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Literal

import numpy as np
from pydantic import BaseModel, Field

from .design import PairDesign

__all__ = ["CaseMeta", "Case", "design_for_case", "case_array"]


class CaseMeta(BaseModel):
    kind: Literal["design_points", "profile", "measurement"] = "design_points"
    bc: Literal["TT", "QT", "other"] = "QT"
    t_hot_c: float | None = Field(None, description="imposed hot-face temperature (BC-TT)")
    t_cold_c: float | None = Field(20.0, description="imposed cold-face temperature")
    q_hot_w: float | None = Field(None, description="imposed heat input (BC-QT)")
    a_hot_mm: float | None = None
    a_cold_mm: float | None = None
    height_mm: float | None = None
    contacts_active: bool | None = Field(None, description="None = use the design setting")
    variant: str | None = None
    mesh: str | None = None
    leg: Literal["P", "N"] | None = Field(None, description="for path profiles")
    current_a: float | None = Field(None, description="operating current of a profile case")
    source: str = ""
    notes: str = ""


class Case(BaseModel):
    id: str
    name: str
    meta: CaseMeta = CaseMeta()
    data: dict[str, list[float | None]] = Field(default_factory=dict)
    created: str = Field(default_factory=lambda: datetime.now(UTC).isoformat(timespec="seconds"))

    def has(self, role: str) -> bool:
        vals = self.data.get(role)
        return bool(vals) and any(v is not None and math.isfinite(v) for v in vals)

    @property
    def n_points(self) -> int:
        return max((len(v) for v in self.data.values()), default=0)


def case_array(case: Case, role: str) -> np.ndarray:
    vals = case.data.get(role)
    if not vals:
        return np.full(case.n_points, np.nan)
    return np.array([np.nan if v is None else float(v) for v in vals], dtype=float)


def design_for_case(base: PairDesign, case: Case) -> PairDesign:
    """Base design adapted to the case's geometry, contacts and thermal boundary condition."""
    m = case.meta
    d = base.model_copy(deep=True)
    a_h = m.a_hot_mm if m.a_hot_mm is not None else _first(case, "a_hot")
    a_c = m.a_cold_mm if m.a_cold_mm is not None else _first(case, "a_cold")
    h = m.height_mm if m.height_mm is not None else _first(case, "height")
    theta = _first(case, "angle")
    if h is None:
        h = d.height_mm
    if a_c is None:
        a_c = d.p_leg.a_cold_mm
    if a_h is None and theta is not None:
        a_h = a_c - 2.0 * h * math.tan(math.radians(theta))
    if a_h is None:
        a_h = d.p_leg.a_hot_mm
    ratio = (d.n_leg.a_cold_mm / d.p_leg.a_cold_mm) ** 2 if d.p_leg.a_cold_mm > 0 else 1.0
    d = d.with_geometry(a_h, a_c, h, n_area_ratio=ratio)
    if m.contacts_active is not None:
        d.contacts.active = m.contacts_active
    tcc, ecc = _first(case, "tcc"), _first(case, "ecc")
    if tcc:
        d.contacts.tcc = tcc
    if ecc:
        d.contacts.ecc = ecc
    b = d.boundary
    b.cold_mode = "T"
    if m.t_cold_c is not None:
        b.t_cold_c = m.t_cold_c
    elif case.has("t_cold_ext"):
        b.t_cold_c = float(np.nanmean(case_array(case, "t_cold_ext")))
    if m.bc == "TT":
        b.hot_mode = "T"
        if m.t_hot_c is not None:
            b.t_hot_c = m.t_hot_c
        elif case.has("t_hot_ext"):
            b.t_hot_c = float(np.nanmean(case_array(case, "t_hot_ext")))
    elif m.bc == "QT":
        b.hot_mode = "Q"
        if m.q_hot_w is not None:
            b.q_hot_w = m.q_hot_w
        elif case.has("q_hot"):
            b.q_hot_w = float(np.nanmean(case_array(case, "q_hot")))
    d.name = case.name
    return d


def _first(case: Case, role: str) -> float | None:
    vals = [v for v in case.data.get(role, []) if v is not None and math.isfinite(v)]
    return float(vals[0]) if vals else None
