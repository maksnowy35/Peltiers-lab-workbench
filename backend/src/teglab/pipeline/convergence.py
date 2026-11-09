"""Mesh convergence of the ANSYS study: GCI for every variant that was solved on more than one mesh.

Systems of the same variant and boundary condition on different meshes (labels from the profile system
rules, sizes from ``profile.meshes``) are compared at every design-point current they share and on the
system-level quantities.  Two meshes use the formal order p = 2 and Fs = 3, three meshes the observed
order and Fs = 1.25 (``analysis.gci``, Roache; Celik et al. 2008).
"""

from __future__ import annotations

import math

import pandas as pd

from ..analysis import gci
from .keys import QuantityKey as Q
from .schemas import BoundaryCondition, ConvergenceStatus, GridConvergence, ProjectProfile

__all__ = ["GCI_CHECK", "GCI_CONVERGED", "grid_convergence"]

GCI_CONVERGED = 0.01  # GCI of the fine mesh ≤ 1 %: the mesh is not what limits the result
GCI_CHECK = 0.05
FORMAL_ORDER = 2.0  # quadratic thermal-electric elements

POINT_QUANTITIES = [
    ("dt_te_k", "ΔT_TE", "K", 1.0),
    (Q.VOLTAGE.value, "U", "mV", 1e3),
    ("power_w", "P", "mW", 1e3),
    (Q.Q_COLD.value, "Q_c", "mW", 1e3),
    (Q.T_MAX.value, "T_max", "°C", 1.0),
]
SYSTEM_QUANTITIES = [
    ("u_oc_v", "U_oc", "mV", 1e3),
    ("k_te_w_k", "Pair conductance K", "mW/K", 1e3),
    ("ohmic_ohm", "R ohmic", "mΩ", 1e3),
]


def _num(value: object) -> float | None:
    try:
        v = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _ratio(profile: ProjectProfile, coarse: str, fine: str) -> float:
    a, b = profile.meshes[coarse], profile.meshes[fine]
    if a.nodes and b.nodes:
        return (b.nodes / a.nodes) ** (1.0 / 3.0)
    return a.element_size_mm / b.element_size_mm


def _status(values: list[float], g: float, p_obs: float | None, three: bool) -> ConvergenceStatus:
    if max(values) - min(values) <= 1e-12 * max(1.0, abs(values[-1])):
        return "identical"
    if three and p_obs is None:
        return "oscillatory"
    return "converged" if g <= GCI_CONVERGED else "check" if g <= GCI_CHECK else "not-converged"


def _study(
    variant: str,
    bc: object,
    key: str,
    label: str,
    unit: str,
    scale: float,
    current: float | None,
    systems: list[str],
    meshes: list[str],
    values: list[float],
    profile: ProjectProfile,
) -> GridConvergence | None:
    if len(values) < 2 or abs(values[-1]) < 1e-15:
        return None
    sizes = [profile.meshes[m].element_size_mm for m in meshes]
    r = _ratio(profile, meshes[-2], meshes[-1])
    if r <= 1.0 + 1e-9:
        return None
    three = len(values) == 3 and abs(_ratio(profile, meshes[0], meshes[1]) - r) < 0.05 * r
    if len(values) == 3 and not three:  # GCI with three grids needs a constant ratio: use the two finest
        systems, meshes, values, sizes = systems[1:], meshes[1:], values[1:], sizes[1:]
    if three:
        res = gci(values[0], values[2], r, FORMAL_ORDER, None, values[1])
    else:
        res = gci(values[0], values[1], r, FORMAL_ORDER)
    g = float(res["gci_fine"])
    p_obs = res.get("p_observed")
    return GridConvergence(
        variant=variant,
        bc=BoundaryCondition(str(bc)) if bc in ("QT", "TT") else None,
        key=key,
        label=label,
        unit=unit,
        scale=scale,
        current_a=current,
        systems=systems,
        meshes=meshes,
        element_size_mm=sizes,
        values=values,
        r=r,
        p_observed=p_obs,
        p_used=float(res["p_used"]),
        fs=float(res["fs"]),
        gci_fine=g,
        f_extrapolated=_num(res.get("f_extrapolated")),
        status=_status(values, g, p_obs, three),
    )


def grid_convergence(frame: pd.DataFrame, summary: pd.DataFrame, profile: ProjectProfile) -> list[GridConvergence]:
    """GCI of every quantity shared by the meshes of one variant (coarse → fine, at most three meshes)."""
    if not profile.meshes or "mesh" not in summary or "variant" not in summary:
        return []
    known = summary[summary["mesh"].isin(list(profile.meshes)) & summary["variant"].notna()]
    out: list[GridConvergence] = []
    for (variant, bc), group in known.groupby(["variant", "bc"], dropna=False, sort=True):
        per_mesh = group.reset_index().drop_duplicates("mesh")
        if len(per_mesh) < 2:
            continue
        per_mesh = per_mesh.assign(h=per_mesh["mesh"].map(lambda m: profile.meshes[m].element_size_mm))
        per_mesh = per_mesh.sort_values("h", ascending=False).tail(3)  # coarse → fine
        systems = [str(s) for s in per_mesh["system"]]
        meshes = [str(m) for m in per_mesh["mesh"]]
        rows = [summary.loc[s] for s in systems]

        for key, label, unit, scale in SYSTEM_QUANTITIES:
            vals = [_num(r.get(key)) for r in rows]
            if all(v is not None for v in vals):
                st = _study(str(variant), bc, key, label, unit, scale, None, systems, meshes, vals, profile)  # type: ignore[arg-type]
                if st:
                    out.append(st)

        points = {s: frame.xs(s, level="system") for s in systems if s in frame.index.get_level_values("system")}
        if len(points) != len(systems):
            continue
        currents = [set(round(float(i), 9) for i in points[s][Q.CURRENT.value].dropna()) for s in systems]
        for current in sorted(set.intersection(*currents)):
            at = [points[s][points[s][Q.CURRENT.value].round(9) == current].iloc[0] for s in systems]
            for key, label, unit, scale in POINT_QUANTITIES:
                if key == "power_w" and current == 0.0:
                    continue
                vals = [_num(r.get(key)) for r in at]
                if all(v is not None for v in vals):
                    st = _study(str(variant), bc, key, label, unit, scale, current, systems, meshes, vals, profile)  # type: ignore[arg-type]
                    if st:
                        out.append(st)
    return out
