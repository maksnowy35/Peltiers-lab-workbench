"""Calibration of the 1D twin on ANSYS systems and validation on the others.

The 3D model has passive-layer spreading and current paths a 1D model can only carry as lumped factors:
the hot- and cold-side passive resistance factors and a series resistance.  They are fitted (bounded
least squares) to the open circuit and the design points of the calibration systems, by default the
systems of the reference variant.  The misfit on the remaining systems is the validation: if the factors
fitted on A0 also hold for A5 and A10, the calibrated 1D model can stand in for FEM between runs.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
from scipy.optimize import least_squares

from ..solver import build_model, solve
from .keys import QuantityKey as Q
from .schemas import BoundaryCondition, CalibrationSettings, ProjectProfile, TwinCalibration, TwinProfile
from .twin import _system_inputs, twin_design, twin_library

__all__ = ["BOUNDS", "calibrate_twin"]

BOUNDS: dict[str, tuple[float, float, bool]] = {  # lo, hi, fitted in log10
    "hot_side_factor": (0.2, 5.0, True),
    "cold_side_factor": (0.2, 5.0, True),
    "r_el_extra_mohm": (-20.0, 50.0, False),
}


def _num(value: object) -> float | None:
    try:
        v = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


class _Target:
    """What one system offers to the fit: its inputs and the observed values."""

    def __init__(self, letter: str, row: pd.Series, points: pd.DataFrame | None, profile: ProjectProfile) -> None:
        self.letter = letter
        self.inputs = _system_inputs(row, profile)
        self.u_oc = _num(row.get("u_oc_v")) if "I = 0" in str(row.get("oc_method") or "") else None
        self.dt_oc = _num(row.get("dt_te_k")) if self.u_oc is not None else None
        self.q_oc = _num(row.get("q_hot_w")) if self.inputs[1] is BoundaryCondition.TT else None
        self.points: list[tuple[float, float | None, float | None, float | None]] = []
        if points is not None and Q.CURRENT.value in points:
            for _, r in points.iterrows():
                i = _num(r.get(Q.CURRENT.value))
                if i is None or abs(i) < 1e-12:
                    continue
                qh = _num(r.get(Q.Q_HOT.value)) if self.inputs[1] is BoundaryCondition.TT else None
                self.points.append((i, _num(r.get(Q.VOLTAGE.value)), _num(r.get(Q.Q_COLD.value)), qh))

    def residuals(self, tw: TwinProfile, profile: ProjectProfile) -> list[float]:
        theta, bc, t_cold, q_hot, t_hot = self.inputs
        model = build_model(twin_design(profile, theta, bc, t_cold, q_hot, t_hot, tw), twin_library(profile))
        oc = solve(model, 0.0)
        out: list[float] = []
        u_ref = self.u_oc or abs(oc.voltage_v)
        if self.u_oc is not None:
            out.append((oc.voltage_v - self.u_oc) / abs(self.u_oc))
        if self.dt_oc is not None:
            out.append((oc.dt_te_k - self.dt_oc) / abs(self.dt_oc))
        if self.q_oc is not None:
            out.append((oc.q_hot_w - self.q_oc) / abs(self.q_oc))
        t_init = oc.temps
        for i, u, qc, qh in sorted(self.points):
            sol = solve(model, i, t_init)
            t_init = sol.temps
            if u is not None and u_ref > 0:
                out.append((sol.voltage_v - u) / u_ref)  # on the U_oc scale: U crosses zero
            if qc is not None:
                out.append((sol.q_cold_w - qc) / abs(qc))
            if qh is not None:
                out.append((sol.q_hot_w - qh) / abs(qh))
        return out


def _rms(r: list[float]) -> float | None:
    return float(np.sqrt(np.mean(np.square(r)))) if r else None


def calibrate_twin(
    frame: pd.DataFrame,
    summary: pd.DataFrame,
    profile: ProjectProfile,
    settings: CalibrationSettings,
    reference_variant: str,
) -> tuple[TwinCalibration | None, TwinProfile, list[str]]:
    """Fit the twin factors; returns the result, the twin settings to use and notes for the report."""
    letters = [str(s) for s in summary.index]
    fit_on = [s for s in settings.systems if s in letters] or [
        s for s in letters if summary.loc[s].get("variant") == reference_variant
    ]
    targets: dict[str, _Target] = {}
    notes: list[str] = []
    for s in letters:
        pts = frame.xs(s, level="system") if s in frame.index.get_level_values("system") else None
        try:
            targets[s] = _Target(s, summary.loc[s], pts, profile)
        except LookupError as exc:
            if s in fit_on:
                notes.append(f"system {s} cannot be calibrated: {exc}")
    fit_on = [s for s in fit_on if s in targets]
    validate_on = [s for s in letters if s in targets and s not in fit_on]
    if not fit_on:
        notes.append("no system to calibrate on: assign the reference variant or list the systems")
        return None, profile.twin, notes

    base = profile.twin
    params = list(dict.fromkeys(settings.params))

    def settings_for(x: np.ndarray) -> TwinProfile:
        upd = {}
        for k, xi in zip(params, x, strict=True):
            upd[k] = 10.0**xi if BOUNDS[k][2] else float(xi)
        return base.model_copy(update=upd)

    def res(tw: TwinProfile, systems: list[str]) -> list[float]:
        out: list[float] = []
        for s in systems:
            out.extend(targets[s].residuals(tw, profile))
        return out

    x0, lo, hi = [], [], []
    for k in params:
        a, b, log = BOUNDS[k]
        v = min(max(getattr(base, k), a), b)
        x0.append(math.log10(v) if log else v)
        lo.append(math.log10(a) if log else a)
        hi.append(math.log10(b) if log else b)
    r_fit0, r_val0 = res(base, fit_on), res(base, validate_on)
    if len(r_fit0) < len(params):
        notes.append(f"{len(r_fit0)} observations for {len(params)} parameters: not enough to calibrate")
        return None, base, notes
    sol = least_squares(
        lambda x: np.asarray(res(settings_for(x), fit_on)),
        np.clip(np.array(x0), np.array(lo) + 1e-9, np.array(hi) - 1e-9),
        bounds=(np.array(lo), np.array(hi)),
        method="trf",
        diff_step=1e-4,
        max_nfev=120,
    )
    fitted = settings_for(sol.x)
    r_fit1, r_val1 = res(fitted, fit_on), res(fitted, validate_on)
    result = TwinCalibration(
        fitted_on=fit_on,
        validated_on=validate_on,
        params=params,
        initial={k: float(getattr(base, k)) for k in params},
        fitted={k: float(getattr(fitted, k)) for k in params},
        rms_fit_before=_rms(r_fit0),
        rms_fit_after=_rms(r_fit1),
        rms_validation_before=_rms(r_val0),
        rms_validation_after=_rms(r_val1),
        n_residuals=len(r_fit0),
        success=bool(sol.success),
        message=str(sol.message),
    )
    return result, fitted, notes
