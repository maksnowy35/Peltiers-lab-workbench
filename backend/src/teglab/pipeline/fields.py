"""Reductions of Mechanical nodal fields: face averages, contact jumps and the layer-by-layer ΔT split.

The thesis probes average temperature over whole surface groups, so ceramic, copper and contact of one
side can only be reported together (thesis Section 4.2).  With nodal exports scoped to bodies, every
layer and every contact is resolved separately:

    hot face ─ ceramic ─ Cu ─ [contact jump] ─ legs ─ [contact jump] ─ Cu ─ ceramic ─ cold face

Faces are integrated with area weights from a Delaunay triangulation of the face nodes (exact for
fields that vary linearly over each triangle; faces are assumed convex, as every face of the unit cell
is), so graded meshes do not bias the averages.  Contact jumps are evaluated on the smaller of the two
touching faces (the leg end, not the larger electrode) after interpolating the other side onto its
nodes - the meshes on both sides need not match.  Nodes without a value (NaN, e.g. no electric
degree of freedom in a ceramic) are left out of an average but keep their share of the face area.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

import numpy as np
import pandas as pd
from scipy.interpolate import LinearNDInterpolator, NearestNDInterpolator
from scipy.spatial import Delaunay, QhullError

__all__ = [
    "FaceStat",
    "Interface",
    "Layer",
    "contact_state",
    "face_stat",
    "find_interfaces",
    "interface_jump",
    "layer_profile",
    "passive_active_split",
    "vertex_weights",
]

AXES = ("x", "y", "z")
Side = Literal["hot", "cold"]


@dataclass(frozen=True)
class FaceStat:
    mean: float
    minimum: float
    maximum: float
    n_nodes: int
    area_mm2: float


@dataclass(frozen=True)
class Layer:
    name: str
    bodies: tuple[str, ...]
    active: bool = False  # thermoelectric legs


@dataclass(frozen=True)
class Interface:
    """Two touching bodies of consecutive layers; ``hot`` belongs to the layer nearer the hot face."""

    index: int  # index of the hot-side layer
    hot: str
    cold: str
    level_mm: float


def _plane_axes(axis: str) -> tuple[str, str]:
    u, v = (a for a in AXES if a != axis)
    return f"{u}_mm", f"{v}_mm"


def on_plane(frame: pd.DataFrame, axis: str, level_mm: float, tol_mm: float) -> pd.DataFrame:
    return frame[(frame[f"{axis}_mm"] - level_mm).abs() <= tol_mm]


def _dedupe(nodes: pd.DataFrame, values: str | Sequence[str]) -> pd.DataFrame:
    """Unaveraged (element-nodal) exports repeat node numbers: average them per node."""
    if "node_id" not in nodes or nodes["node_id"].is_unique:
        return nodes
    cols = [values] if isinstance(values, str) else list(values)
    coords = [c for c in ("x_mm", "y_mm", "z_mm") if c in nodes]
    agg = {**dict.fromkeys(coords, "first"), **dict.fromkeys((c for c in cols if c in nodes), "mean")}
    return nodes.groupby("node_id", as_index=False).agg(agg)


def vertex_weights(u: np.ndarray, v: np.ndarray) -> np.ndarray:
    """Nodal area weights: one third of the area of every Delaunay triangle a node belongs to."""
    n = len(u)
    if n < 3:
        return np.ones(n)
    try:
        tri = Delaunay(np.column_stack([u, v]))
    except QhullError:  # collinear nodes: no area, fall back to a plain mean
        return np.ones(n)
    p = tri.points[tri.simplices]
    area = 0.5 * np.abs(
        (p[:, 1, 0] - p[:, 0, 0]) * (p[:, 2, 1] - p[:, 0, 1]) - (p[:, 2, 0] - p[:, 0, 0]) * (p[:, 1, 1] - p[:, 0, 1])
    )
    w = np.zeros(n)
    np.add.at(w, tri.simplices, (area / 3.0)[:, None])
    return w


def _weighted(w: np.ndarray, vals: np.ndarray) -> float | None:
    ok = np.isfinite(vals)
    if not ok.any():
        return None
    wo = w[ok]
    return float(np.sum(wo * vals[ok]) / np.sum(wo)) if wo.sum() > 0 else float(vals[ok].mean())


def face_stat(nodes: pd.DataFrame, value: str, axis: str, weighted: bool = True) -> FaceStat:
    """Area-weighted (or plain nodal) average of ``value`` over nodes lying on one plane."""
    if nodes.empty:
        raise ValueError("no nodes on this face")
    u, v = _plane_axes(axis)
    vals = nodes[value].to_numpy(dtype=float)
    ok = np.isfinite(vals)
    if not ok.any():
        raise ValueError(f"no '{value}' values on this face")
    w = vertex_weights(nodes[u].to_numpy(dtype=float), nodes[v].to_numpy(dtype=float))
    mean = _weighted(w, vals) if weighted else float(vals[ok].mean())
    return FaceStat(float(mean), float(vals[ok].min()), float(vals[ok].max()), int(ok.sum()), float(w.sum()))


class _Faces:
    """Both faces of one interface, with the smaller one (the target) carrying the evaluation."""

    def __init__(self, hot: pd.DataFrame, cold: pd.DataFrame, axis: str, level_mm: float, tol_mm: float):
        cols = [c for c in hot.columns.union(cold.columns) if c not in ("node_id", "x_mm", "y_mm", "z_mm")]
        a = _dedupe(on_plane(hot, axis, level_mm, tol_mm), cols)
        b = _dedupe(on_plane(cold, axis, level_mm, tol_mm), cols)
        if a.empty or b.empty:
            raise ValueError(f"no nodes at {axis} = {level_mm:g} mm on one side of the interface")
        self.u, self.v = _plane_axes(axis)
        wa = vertex_weights(a[self.u].to_numpy(dtype=float), a[self.v].to_numpy(dtype=float))
        wb = vertex_weights(b[self.u].to_numpy(dtype=float), b[self.v].to_numpy(dtype=float))
        self.target_side: Side = "hot" if wa.sum() <= wb.sum() else "cold"
        self.faces = {"hot": a, "cold": b}
        self.weights = wa if self.target_side == "hot" else wb
        self.target = self.faces[self.target_side]
        self.uv_target = self.target[[self.u, self.v]].to_numpy(dtype=float)

    @property
    def area_mm2(self) -> float:
        return float(self.weights.sum())

    def values(self, side: Side, column: str) -> np.ndarray | None:
        """``column`` of the body on ``side``, evaluated at the target-face nodes."""
        face = self.faces[side]
        if column not in face:
            return None
        vals = face[column].to_numpy(dtype=float)
        if side == self.target_side:
            return vals
        ok = np.isfinite(vals)
        if not ok.any():
            return None
        uv = face[[self.u, self.v]].to_numpy(dtype=float)[ok]
        vals = vals[ok]
        try:
            out = LinearNDInterpolator(uv, vals)(self.uv_target)
        except (QhullError, ValueError):  # degenerate (collinear) face
            out = np.full(len(self.uv_target), np.nan)
        missing = np.isnan(out)
        if missing.any():  # target nodes just outside the other face's hull
            out[missing] = NearestNDInterpolator(uv, vals)(self.uv_target[missing])
        return out

    def jump(self, column: str) -> np.ndarray | None:
        hot, cold = self.values("hot", column), self.values("cold", column)
        return None if hot is None or cold is None else hot - cold

    def mean(self, arr: np.ndarray | None) -> float | None:
        return None if arr is None else _weighted(self.weights, arr)


def interface_jump(
    hot_side: pd.DataFrame, cold_side: pd.DataFrame, value: str, axis: str, level_mm: float, tol_mm: float = 1e-4
) -> FaceStat:
    """Jump ``value(hot side) − value(cold side)`` across one interface plane, on the smaller face."""
    faces = _Faces(hot_side, cold_side, axis, level_mm, tol_mm)
    jump = faces.jump(value)
    if jump is None:
        raise ValueError(f"'{value}' is missing on one side of the interface")
    return face_stat(faces.target[[faces.u, faces.v]].assign(jump=jump), "jump", axis)


def contact_state(
    hot_side: pd.DataFrame,
    cold_side: pd.DataFrame,
    axis: str,
    level_mm: float,
    tol_mm: float = 1e-4,
    flux_side: Side | None = None,
    current_side: Side | None = None,
) -> dict[str, float | None]:
    """Jumps of temperature and potential across one interface, plus the fluxes through it.

    ``flux_side``/``current_side`` choose the body whose heat flux/current density is used (default:
    the smaller face).  Returns ``dt_k``, ``dv_v``, ``heat_flux_w_m2``, ``current_density_a_m2``
    (area-weighted means; None where a field is missing) and ``area_mm2``.
    """
    faces = _Faces(hot_side, cold_side, axis, level_mm, tol_mm)
    q = faces.mean(faces.values(flux_side or faces.target_side, "heat_flux_w_m2"))
    j = faces.mean(faces.values(current_side or faces.target_side, "current_density_a_m2"))
    return {
        "dt_k": faces.mean(faces.jump("temperature_c")),
        "dv_v": faces.mean(faces.jump("voltage_v")),
        "heat_flux_w_m2": None if q is None else abs(q),
        "current_density_a_m2": None if j is None else abs(j),
        "area_mm2": faces.area_mm2,
    }


def _faces(frame: pd.DataFrame, axis: str, hot_at_min: bool) -> tuple[float, float]:
    col = frame[f"{axis}_mm"]
    return (float(col.min()), float(col.max())) if hot_at_min else (float(col.max()), float(col.min()))


def _overlap(a: pd.DataFrame, b: pd.DataFrame, axis: str) -> bool:
    u, v = _plane_axes(axis)
    return bool(
        a[u].min() < b[u].max() and b[u].min() < a[u].max() and a[v].min() < b[v].max() and b[v].min() < a[v].max()
    )


def _combine(stats: Sequence[FaceStat]) -> FaceStat:
    area = sum(s.area_mm2 for s in stats)
    mean = sum(s.mean * s.area_mm2 for s in stats) / area if area > 0 else float(np.mean([s.mean for s in stats]))
    return FaceStat(
        mean, min(s.minimum for s in stats), max(s.maximum for s in stats), sum(s.n_nodes for s in stats), area
    )


def hot_at_min(bodies: Mapping[str, pd.DataFrame], layers: Sequence[Layer], axis: str) -> bool:
    """True if the first (hot) layer lies at smaller coordinates than the last one."""
    first = pd.concat([bodies[b][f"{axis}_mm"] for b in layers[0].bodies])
    last = pd.concat([bodies[b][f"{axis}_mm"] for b in layers[-1].bodies])
    return bool(first.mean() < last.mean())


def find_interfaces(
    bodies: Mapping[str, pd.DataFrame], layers: Sequence[Layer], axis: str = "y", tol_mm: float = 1e-4
) -> list[Interface]:
    """Pairs of bodies of consecutive layers whose facing planes coincide and overlap in the plane."""
    at_min = hot_at_min(bodies, layers, axis)
    found = []
    for k in range(len(layers) - 1):
        for ta in layers[k].bodies:
            a = bodies[ta]
            level_a = _faces(a, axis, at_min)[1]
            for tb in layers[k + 1].bodies:
                b = bodies[tb]
                level_b = _faces(b, axis, at_min)[0]
                if abs(level_a - level_b) <= tol_mm and _overlap(
                    on_plane(a, axis, level_a, tol_mm), on_plane(b, axis, level_b, tol_mm), axis
                ):
                    found.append(Interface(k, ta, tb, 0.5 * (level_a + level_b)))
    return found


def layer_profile(
    bodies: Mapping[str, pd.DataFrame],
    layers: Sequence[Layer],
    axis: str = "y",
    value: str = "temperature_c",
    tol_mm: float = 1e-4,
) -> pd.DataFrame:
    """Face averages of every layer and the jump across every interface, ordered from hot to cold.

    ``layers`` lists body tags from the hot face to the cold face, e.g.
    ``[Layer("ceramic hot", ("ceramic_hot",)), Layer("Cu hot", ("cu_hot",)),
    Layer("legs", ("leg_p", "leg_n"), active=True), Layer("Cu cold", ("cu_cold_p", "cu_cold_n")),
    Layer("ceramic cold", ("ceramic_cold",))]``.

    ``s_hot_mm``/``s_cold_mm`` locate each layer's faces along the stack, measured from the hot face.
    """
    at_min = hot_at_min(bodies, layers, axis)
    origin = float(np.mean([_faces(bodies[b], axis, at_min)[0] for b in layers[0].bodies]))
    interfaces = find_interfaces(bodies, layers, axis, tol_mm)
    rows = []
    for k, layer in enumerate(layers):
        hot_stats, cold_stats, s_hot, s_cold = [], [], [], []
        for tag in layer.bodies:
            frame = bodies[tag]
            hot_level, cold_level = _faces(frame, axis, at_min)
            hot_stats.append(face_stat(_dedupe(on_plane(frame, axis, hot_level, tol_mm), value), value, axis))
            cold_stats.append(face_stat(_dedupe(on_plane(frame, axis, cold_level, tol_mm), value), value, axis))
            s_hot.append(abs(hot_level - origin))
            s_cold.append(abs(cold_level - origin))
        hot, cold = _combine(hot_stats), _combine(cold_stats)
        rows.append(
            {
                "kind": "layer",
                "name": layer.name,
                "active": layer.active,
                "t_hot_c": hot.mean,
                "t_cold_c": cold.mean,
                "dt_k": hot.mean - cold.mean,
                "area_mm2": hot.area_mm2,
                "s_hot_mm": float(np.mean(s_hot)),
                "s_cold_mm": float(np.mean(s_cold)),
            }
        )
        pairs = [i for i in interfaces if i.index == k]
        if pairs:
            jumps = [interface_jump(bodies[i.hot], bodies[i.cold], value, axis, i.level_mm, tol_mm) for i in pairs]
            j = _combine(jumps)
            s = float(np.mean([abs(i.level_mm - origin) for i in pairs]))
            rows.append(
                {
                    "kind": "interface",
                    "name": f"{layer.name} | {layers[k + 1].name}",
                    "active": False,
                    "t_hot_c": np.nan,
                    "t_cold_c": np.nan,
                    "dt_k": j.mean,
                    "area_mm2": j.area_mm2,
                    "s_hot_mm": s,
                    "s_cold_mm": s,
                }
            )
    return pd.DataFrame(rows)


def passive_active_split(profile: pd.DataFrame) -> dict[str, float]:
    """Sum the layer/interface drops before, inside and after the active (leg) layers."""
    active = profile["active"].to_numpy()
    if not active.any():
        raise ValueError("no active layer in the profile")
    first, last = np.flatnonzero(active)[[0, -1]]
    hot = float(profile["dt_k"].iloc[:first].sum())
    legs = float(profile["dt_k"].iloc[first : last + 1][profile["kind"].iloc[first : last + 1] == "layer"].sum())
    cold = float(profile["dt_k"].iloc[last + 1 :].sum())
    total = hot + legs + cold
    contacts = profile[(profile["kind"] == "interface")]["dt_k"].abs()
    return {
        "dt_hot_passive_k": hot,
        "dt_te_k": legs,
        "dt_cold_passive_k": cold,
        "dt_total_k": total,
        "passive_share": (hot + cold) / total if total else np.nan,
        "dt_contacts_k": float(contacts.sum()),
    }
