"""Calibration of the 1D model against ANSYS design points (least squares).

The 3D FEM model contains effects that a 1D model can only represent through
a few lumped parameters: spreading in the ceramic/Cu plates, the 3D shape of
a tapered leg, current crowding at the electrode edges.  These parameters are
fitted to the imported cases; the calibrated model is then used for fast
design sweeps (optimizer) between/after ANSYS runs.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy.optimize import least_squares

from .cases import Case, case_array, design_for_case
from .design import PairDesign
from .materials import MaterialLibrary, default_library
from .solver import build_model, sweep_current

__all__ = ["PARAMS", "QUANTITIES", "predict_case", "compare_model", "calibrate"]


@dataclass(frozen=True)
class Param:
    key: str
    label: str
    lo: float
    hi: float
    log: bool
    scale: float = 1.0


PARAMS: dict[str, Param] = {
    p.key: p
    for p in [
        Param("hot_side_factor", "Hot-side ceramic+Cu factor", 0.2, 5.0, True),
        Param("cold_side_factor", "Cold-side ceramic+Cu factor", 0.2, 5.0, True),
        Param("r_el_extra_mohm", "Series resistance correction [mΩ]", -20.0, 50.0, False, 1.0),
        Param("c3d", "Frustum 3D coefficient c3d", 0.0, 5.0, False, 0.5),
        Param("alpha_factor", "Seebeck scale factor", 0.8, 1.2, False, 0.05),
        Param("tcc", "Thermal contact conductance [W/(m²·K)]", 1e3, 1e8, True),
        Param("ecc", "Electrical contact conductance [S/m²]", 1e6, 1e12, True),
    ]
}

# quantity -> (case role, weight)
QUANTITIES: dict[str, float] = {
    "voltage": 1.0,
    "power": 0.5,
    "q_hot": 1.0,
    "q_cold": 1.0,
    "t_hot_ext": 1.0,
    "t_leg_hot": 1.0,
    "t_leg_cold": 1.0,
    "t_max": 0.3,
}


def _get(d: PairDesign, key: str) -> float:
    if key in ("hot_side_factor", "cold_side_factor", "r_el_extra_mohm"):
        return float(getattr(d.passive, key))
    if key in ("tcc", "ecc"):
        return float(getattr(d.contacts, key))
    return float(getattr(d, key))


def _set(d: PairDesign, key: str, value: float) -> None:
    if key in ("hot_side_factor", "cold_side_factor", "r_el_extra_mohm"):
        setattr(d.passive, key, value)
    elif key in ("tcc", "ecc"):
        setattr(d.contacts, key, value)
    else:
        setattr(d, key, value)


def predict_case(design: PairDesign, case: Case, lib: MaterialLibrary, dense: int = 0) -> dict:
    """Model predictions at the case's currents (and optionally on a dense current grid)."""
    d = design_for_case(design, case)
    model = build_model(d, lib)
    I = case_array(case, "current")
    ok = np.isfinite(I)
    order = np.argsort(np.where(ok, I, np.inf))
    sols_sorted = sweep_current(model, I[order][: int(ok.sum())])
    sols: list = [None] * I.size
    for k, s in zip(order[: int(ok.sum())], sols_sorted, strict=True):
        sols[int(k)] = s

    def arr(fn) -> np.ndarray:
        return np.array([fn(s) if s is not None else np.nan for s in sols], dtype=float)

    pred = {
        "current": I,
        "voltage": arr(lambda s: s.voltage_v),
        "power": arr(lambda s: s.power_w),
        "q_hot": arr(lambda s: s.q_hot_w),
        "q_cold": arr(lambda s: s.q_cold_w),
        "t_hot_ext": arr(lambda s: s.t_hot_ext_c),
        "t_leg_hot": arr(lambda s: 0.5 * (s.t_leg_hot_c[0] + s.t_leg_hot_c[1])),
        "t_leg_cold": arr(lambda s: 0.5 * (s.t_leg_cold_c[0] + s.t_leg_cold_c[1])),
        "t_max": arr(lambda s: s.t_max_c),
        "efficiency": arr(lambda s: s.efficiency),
    }
    if dense and np.any(ok):
        i_max = max(float(np.nanmax(I)), 1e-6)
        grid = np.linspace(0.0, i_max, dense)
        ds = sweep_current(model, grid)
        pred["dense"] = {
            "current_a": grid.tolist(),
            "voltage_v": [s.voltage_v for s in ds],
            "power_w": [s.power_w for s in ds],
            "q_hot_w": [s.q_hot_w for s in ds],
            "q_cold_w": [s.q_cold_w for s in ds],
            "t_hot_ext_c": [s.t_hot_ext_c for s in ds],
            "dt_te_k": [s.dt_te_k for s in ds],
            "efficiency": [s.efficiency for s in ds],
        }
    return pred


def _scales(case: Case) -> dict[str, float]:
    s: dict[str, float] = {}
    for q in ("voltage", "power", "q_hot", "q_cold"):
        a = np.abs(case_array(case, q))
        if np.any(np.isfinite(a)):
            s[q] = max(float(np.nanmax(a)), 1e-9)
    temps = [case_array(case, r) for r in ("t_hot_ext", "t_leg_hot", "t_max")]
    t_hot = max((float(np.nanmax(t)) for t in temps if np.any(np.isfinite(t))), default=None)
    t_cold = case.meta.t_cold_c if case.meta.t_cold_c is not None else 20.0
    rise = max((t_hot - t_cold) if t_hot is not None else 1.0, 1.0)
    for q in ("t_hot_ext", "t_leg_hot", "t_leg_cold", "t_max"):
        s[q] = rise
    return s


def _residuals(design: PairDesign, cases: list[Case], lib: MaterialLibrary, weights: dict[str, float]) -> np.ndarray:
    res = []
    for case in cases:
        pred = predict_case(design, case, lib)
        sc = _scales(case)
        for q, w in weights.items():
            if w <= 0 or not case.has(q):
                continue
            obs = case_array(case, q)
            m = np.isfinite(obs) & np.isfinite(pred[q])
            res.append(math.sqrt(w) * (pred[q][m] - obs[m]) / sc[q])
    return np.concatenate(res) if res else np.zeros(1)


def compare_model(
    design: PairDesign, cases: list[Case], lib: MaterialLibrary | None = None, dense: int = 41
) -> list[dict]:
    """ROM vs data for each case: predictions, errors per quantity."""
    lib = lib or default_library()
    out = []
    for case in cases:
        pred = predict_case(design, case, lib, dense=dense)
        sc = _scales(case)
        errors = {}
        for q in QUANTITIES:
            if not case.has(q):
                continue
            obs = case_array(case, q)
            m = np.isfinite(obs) & np.isfinite(pred[q])
            if not np.any(m):
                continue
            e = (pred[q][m] - obs[m]) / sc[q]
            errors[q] = {
                "rms_rel": float(np.sqrt(np.mean(e * e))),
                "max_abs": float(np.max(np.abs(pred[q][m] - obs[m]))),
            }
        out.append(
            {
                "case_id": case.id,
                "name": case.name,
                "observed": {q: _j(case_array(case, q)) for q in ["current", *QUANTITIES] if case.has(q)},
                "predicted": {q: _j(v) for q, v in pred.items() if q != "dense"},
                "dense": pred.get("dense"),
                "errors": errors,
                "design": design_for_case(design, case).model_dump(),
            }
        )
    return out


def calibrate(
    design: PairDesign,
    cases: list[Case],
    fit: list[str],
    lib: MaterialLibrary | None = None,
    weights: dict[str, float] | None = None,
) -> dict:
    lib = lib or default_library()
    unknown = [k for k in fit if k not in PARAMS]
    if unknown:
        raise ValueError(f"unknown calibration parameters: {unknown}")
    if not cases:
        raise ValueError("no cases selected")
    w = dict(QUANTITIES)
    if weights:
        w.update(weights)
    params = [PARAMS[k] for k in fit]

    def to_x(d: PairDesign) -> np.ndarray:
        xs = []
        for p in params:
            v = min(max(_get(d, p.key), p.lo), p.hi)
            xs.append(math.log10(v) if p.log else v / p.scale)
        return np.array(xs)

    def from_x(x: np.ndarray) -> PairDesign:
        d = design.model_copy(deep=True)
        for p, xi in zip(params, x, strict=True):
            _set(d, p.key, 10.0**xi if p.log else xi * p.scale)
        return d

    lo = np.array([math.log10(p.lo) if p.log else p.lo / p.scale for p in params])
    hi = np.array([math.log10(p.hi) if p.log else p.hi / p.scale for p in params])
    x0 = np.clip(to_x(design), lo + 1e-9, hi - 1e-9)
    r0 = _residuals(design, cases, lib, w)
    cost0 = float(0.5 * np.sum(r0 * r0))
    if params:
        sol = least_squares(
            lambda x: _residuals(from_x(x), cases, lib, w),
            x0,
            bounds=(lo, hi),
            method="trf",
            x_scale=1.0,
            diff_step=1e-4,
            max_nfev=200,
        )
        fitted = from_x(sol.x)
        success, message, nfev = bool(sol.success), str(sol.message), int(sol.nfev)
    else:
        fitted, success, message, nfev = design.model_copy(deep=True), True, "nothing to fit", 0
    r1 = _residuals(fitted, cases, lib, w)
    before = compare_model(design, cases, lib, dense=0)
    after = compare_model(fitted, cases, lib, dense=41)
    return {
        "fit": fit,
        "initial": {k: _get(design, k) for k in fit},
        "params": {k: _get(fitted, k) for k in fit},
        "cost_before": cost0,
        "cost_after": float(0.5 * np.sum(r1 * r1)),
        "rms_before": float(np.sqrt(np.mean(r0 * r0))),
        "rms_after": float(np.sqrt(np.mean(r1 * r1))),
        "success": success,
        "message": message,
        "nfev": nfev,
        "before": before,
        "after": after,
        "design": fitted.model_dump(),
    }


def _j(a: np.ndarray) -> list[float | None]:
    return [None if not math.isfinite(float(v)) else float(v) for v in np.asarray(a, dtype=float)]
