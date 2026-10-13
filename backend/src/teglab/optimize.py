"""Geometry studies with the (calibrated) 1D model.

Comparison constraints (thesis Section 3.2.3 - the constraint must be explicit):
* ``fixed_cold_base`` - a_c and H fixed, taper angle varied (thesis A-series);
* ``fixed_hot_base``  - a_h and H fixed, taper angle varied;
* ``constant_volume`` - leg volume and H fixed, side ratio r = a_h/a_c varied.

For every geometry the maximum-power operating point is found, and the
design is checked against manufacturing and temperature limits: minimum leg
side (brittle Bi2Te3 breaks at the narrow end, thesis ref. [1]), material
temperature limit and joint (solder) temperature limit.
"""

from __future__ import annotations

import math
from typing import Literal

import numpy as np
from pydantic import BaseModel, Field

from .design import PairDesign
from .geometry import a_hot_from_angle, constant_volume_sides, frustum_volume
from .materials import MaterialLibrary, default_library
from .solver import build_model, material_temperatures, max_efficiency, max_power, solve

__all__ = ["Constraints", "design_metrics", "geometry_sweep", "geometry_map", "pareto_front"]

Mode = Literal["fixed_cold_base", "fixed_hot_base", "constant_volume"]
Objective = Literal["p_max", "eta_at_p_max", "power_per_volume"]


class Constraints(BaseModel):
    a_min_mm: float = Field(0.5, ge=0, description="smallest manufacturable leg side")
    t_joint_max_c: float | None = Field(None, description="max allowed joint (hot Cu) temperature, e.g. solder limit")
    respect_material_limit: bool = True
    theta_max_deg: float | None = Field(None, description="optional brittleness limit on taper angle")


def design_metrics(
    design: PairDesign,
    lib: MaterialLibrary,
    constraints: Constraints | None = None,
    with_eta_max: bool = False,
) -> dict:
    c = constraints or Constraints()
    model = build_model(design, lib)
    oc = solve(model, 0.0)
    mp = max_power(model, oc)
    lg = model.legs
    v_te = model.te_volume * 1e9
    theta = lg[0].theta_deg
    a_min = min(min(x.a_hot, x.a_cold) for x in lg) * 1e3
    t_joint = max(oc.t_hot_plate_c, mp.t_hot_plate_c)
    t_mat = max(oc.t_max_c, mp.t_max_c)
    violations = []
    if a_min < c.a_min_mm - 1e-12:
        violations.append(f"leg side {a_min:.3f} mm < {c.a_min_mm} mm")
    if c.t_joint_max_c is not None and t_joint > c.t_joint_max_c:
        violations.append(f"joint {t_joint:.0f} °C > {c.t_joint_max_c:.0f} °C")
    if c.respect_material_limit:
        seen = set()
        for sol in (oc, mp):
            for mt in material_temperatures(model, sol):
                if not mt["ok"] and mt["material"] not in seen:
                    seen.add(mt["material"])
                    violations.append(f"{mt['name']}: {mt['t_max_c']:.0f} °C > limit {mt['t_limit_c']:.0f} °C")
    if c.theta_max_deg is not None and abs(theta) > c.theta_max_deg:
        violations.append(f"|theta| {abs(theta):.1f} deg > {c.theta_max_deg} deg")
    out = {
        "a_hot_mm": lg[0].a_hot * 1e3,
        "a_cold_mm": lg[0].a_cold * 1e3,
        "height_mm": lg[0].height * 1e3,
        "theta_deg": theta,
        "ratio": lg[0].a_hot / lg[0].a_cold,
        "te_volume_mm3": v_te,
        "k_pair_w_k": oc.q_hot_w / oc.dt_te_k if oc.dt_te_k > 0 else None,
        "u_oc_v": oc.voltage_v,
        "r_int_ohm": mp.r_int_ohm,
        "p_max_w": mp.power_w,
        "i_opt_a": mp.current_a,
        "eta_at_p_max": mp.efficiency,
        "exergy_eta_at_p_max": mp.exergy_efficiency,
        "q_hot_w": mp.q_hot_w,
        "t_hot_ext_oc_c": oc.t_hot_ext_c,
        "t_hot_ext_pmax_c": mp.t_hot_ext_c,
        "t_joint_max_c": t_joint,
        "t_max_c": t_mat,
        "dt_te_pmax_k": mp.dt_te_k,
        "j_max_a_m2": mp.j_max_a_m2,
        "power_per_volume_mw_mm3": mp.power_w * 1e3 / v_te if v_te > 0 else None,
        "volume_per_power_mm3_mw": v_te / (mp.power_w * 1e3) if mp.power_w > 0 else None,
        "feasible": not violations,
        "violations": violations,
    }
    if with_eta_max:
        me = max_efficiency(model, oc)
        out["eta_max"] = me.efficiency
        out["i_eta_max_a"] = me.current_a
    return out


def _geometry(
    mode: Mode, x: float, base: PairDesign, height_mm: float, volume_mm3: float | None
) -> tuple[float, float] | None:
    if mode == "fixed_cold_base":
        a_c = base.p_leg.a_cold_mm
        a_h = a_hot_from_angle(a_c, height_mm, x)
    elif mode == "fixed_hot_base":
        a_h = base.p_leg.a_hot_mm
        a_c = a_h + 2.0 * height_mm * math.tan(math.radians(x))
    else:
        v = volume_mm3 if volume_mm3 else frustum_volume(base.p_leg.a_hot_mm, base.p_leg.a_cold_mm, base.height_mm)
        a_h, a_c = constant_volume_sides(v, height_mm, x)
    if min(a_h, a_c) < 0.1:  # not a meaningful leg any more
        return None
    return a_h, a_c


def _objective(m: dict, objective: Objective) -> float:
    v = m.get(
        {"p_max": "p_max_w", "eta_at_p_max": "eta_at_p_max", "power_per_volume": "power_per_volume_mw_mm3"}[objective]
    )
    return float(v) if v is not None else -math.inf


def geometry_sweep(
    base: PairDesign,
    mode: Mode,
    values: list[float],
    heights_mm: list[float] | None = None,
    objective: Objective = "p_max",
    constraints: Constraints | None = None,
    volume_mm3: float | None = None,
    lib: MaterialLibrary | None = None,
) -> dict:
    """1D sweep of the shape variable (angle in deg, or ratio a_h/a_c for constant volume)."""
    lib = lib or default_library()
    heights = heights_mm or [base.height_mm]
    n_ratio = (base.n_leg.a_cold_mm / base.p_leg.a_cold_mm) ** 2 if base.p_leg.a_cold_mm else 1.0
    series = []
    best = None
    ref = design_metrics(base, lib, constraints)
    for h in heights:
        pts = []
        for x in values:
            g = _geometry(mode, x, base, h, volume_mm3)
            if g is None:
                continue
            d = base.with_geometry(g[0], g[1], h, n_area_ratio=n_ratio)
            m = design_metrics(d, lib, constraints)
            m["x"] = x
            pts.append(m)
            if m["feasible"] and (best is None or _objective(m, objective) > _objective(best, objective)):
                best = m
        series.append({"height_mm": h, "points": pts})
    return {
        "mode": mode,
        "variable": "ratio" if mode == "constant_volume" else "angle_deg",
        "objective": objective,
        "series": series,
        "optimum": best,
        "reference": ref,
        "pareto": pareto_front([p for s in series for p in s["points"]]),
    }


def geometry_map(
    base: PairDesign,
    mode: Mode,
    x_values: list[float],
    heights_mm: list[float],
    objective: Objective = "p_max",
    constraints: Constraints | None = None,
    volume_mm3: float | None = None,
    lib: MaterialLibrary | None = None,
) -> dict:
    """2D map objective(shape variable, height)."""
    lib = lib or default_library()
    n_ratio = (base.n_leg.a_cold_mm / base.p_leg.a_cold_mm) ** 2 if base.p_leg.a_cold_mm else 1.0
    z = np.full((len(heights_mm), len(x_values)), np.nan)
    feas = np.zeros_like(z, dtype=bool)
    t_hot = np.full_like(z, np.nan)
    best = None
    coarse = base.model_copy(deep=True)
    coarse.n_elements = min(base.n_elements, 20)
    for i, h in enumerate(heights_mm):
        for j, x in enumerate(x_values):
            g = _geometry(mode, x, coarse, h, volume_mm3)
            if g is None:
                continue
            m = design_metrics(coarse.with_geometry(g[0], g[1], h, n_area_ratio=n_ratio), lib, constraints)
            z[i, j] = _objective(m, objective)
            feas[i, j] = m["feasible"]
            t_hot[i, j] = m["t_joint_max_c"]
            if m["feasible"] and (best is None or z[i, j] > _objective(best, objective)):
                best = {**m, "x": x}
    return {
        "mode": mode,
        "objective": objective,
        "x": x_values,
        "y": heights_mm,
        "z": [[None if not math.isfinite(v) else float(v) for v in row] for row in z],
        "feasible": feas.tolist(),
        "t_joint_c": [[None if not math.isfinite(v) else float(v) for v in row] for row in t_hot],
        "optimum": best,
    }


def pareto_front(points: list[dict]) -> list[dict]:
    """Non-dominated designs: maximum P_max for minimum TE volume (feasible only)."""
    pts = sorted(
        (p for p in points if p.get("feasible") and p.get("p_max_w") is not None),
        key=lambda p: (p["te_volume_mm3"], -p["p_max_w"]),
    )
    front, best_p = [], -math.inf
    for p in pts:
        if p["p_max_w"] > best_p + 1e-15:
            front.append(
                {k: p[k] for k in ("te_volume_mm3", "p_max_w", "theta_deg", "height_mm", "a_hot_mm", "a_cold_mm")}
            )
            best_p = p["p_max_w"]
    return front
