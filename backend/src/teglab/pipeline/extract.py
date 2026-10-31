"""Derived quantities per analysis system, computed column-wise on the canonical frame.

Everything here is vectorized over *systems* (groupby aggregates, batched least squares, NumPy
broadcasting); nothing loops over design points in Python.

Definitions (thesis notation)
-----------------------------
ΔT_TE          T_leg_hot − T_leg_cold (probe averages on the metallization surfaces)
U_oc           terminal voltage at I = 0
α_eff          U_oc / ΔT_TE(I = 0)                     effective pair Seebeck coefficient
K_TE           Q_h / ΔT_TE at I = 0                     only point without Peltier heat (thesis Section 4.4)
R_apparent     −dU/dI of a straight line through all points (thesis Table 4.2: 17.26 mΩ)
R_ohmic(I)     (α_eff · ΔT_TE(I) − U(I)) / I            EMF from the *measured* ΔT_TE at that current, so
                                                        the drop of ΔT_TE under current is not mistaken
                                                        for resistance
R_contacts     R_ohmic − R_legs − R_copper              compared with 2/(ECC·A_h) + 2/(ECC·A_c)
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from .keys import QuantityKey
from .schemas import BoundaryCondition, ProjectProfile

__all__ = [
    "geometry_table",
    "linear_fit_by_system",
    "max_power_by_system",
    "ohmic_resistance",
    "open_circuit",
    "resistance_table",
    "thermal_split",
]

Q = QuantityKey
I_ZERO_A = 1e-9  # currents below this count as open circuit
LINEAR_R2 = 0.99999  # a load curve this straight is a linear (Thevenin) source


def _flat(frame: pd.DataFrame) -> pd.DataFrame:
    return frame.reset_index()


# ------------------------------------------------------------------------------------ open circuit
def open_circuit(frame: pd.DataFrame) -> pd.DataFrame:
    """One row per system: U_oc, ΔT_TE, Q_h at I = 0, K_TE and α_eff.

    If no design point has I = 0, U_oc is extrapolated from the straight line through the two lowest
    currents and K_TE / α_eff are left empty (they are only defined without current).
    """
    f = _flat(frame).dropna(subset=[Q.CURRENT.value])
    f = f.assign(abs_i=f[Q.CURRENT.value].abs())
    idx = f.groupby("system")["abs_i"].idxmin()
    oc = f.loc[idx].set_index("system")
    at_zero = oc["abs_i"] <= I_ZERO_A

    two = f.sort_values("abs_i").groupby("system").head(2).dropna(subset=[Q.VOLTAGE.value])
    fit = linear_fit_by_system(two, Q.CURRENT.value, Q.VOLTAGE.value)
    extrapolated = fit["intercept"].reindex(oc.index)

    out = pd.DataFrame(index=oc.index)
    out["u_oc_v"] = oc[Q.VOLTAGE.value].where(at_zero, extrapolated)
    out["dt_te_k"] = oc["dt_te_k"].where(at_zero)
    out["q_hot_w"] = oc[Q.Q_HOT.value].where(at_zero)
    out["q_cold_w"] = oc[Q.Q_COLD.value].where(at_zero)
    out["t_hot_face_c"] = oc[Q.T_HOT_FACE.value].where(at_zero)
    out["t_cold_face_c"] = oc[Q.T_COLD_FACE.value].where(at_zero)
    out["t_leg_hot_c"] = oc[Q.T_LEG_HOT.value].where(at_zero)
    out["t_leg_cold_c"] = oc[Q.T_LEG_COLD.value].where(at_zero)
    out["t_max_c"] = oc[Q.T_MAX.value].where(at_zero)
    out["k_te_w_k"] = out["q_hot_w"] / out["dt_te_k"]
    out["alpha_eff_v_k"] = out["u_oc_v"] / out["dt_te_k"]
    out["method"] = np.where(at_zero, "measured at I = 0", "extrapolated from the two lowest currents")
    return out


# ------------------------------------------------------------------------------ batched regression
def linear_fit_by_system(f: pd.DataFrame, x: str, y: str) -> pd.DataFrame:
    """Least-squares line y = a + b·x for every system at once (centered sums, no Python loop)."""
    d = f[["system", x, y]].dropna()
    g = d.groupby("system")
    xc = d[x] - g[x].transform("mean")
    yc = d[y] - g[y].transform("mean")
    sums = pd.DataFrame({"system": d["system"], "sxx": xc * xc, "sxy": xc * yc, "syy": yc * yc}).groupby("system").sum()
    n = g.size()
    slope = sums["sxy"] / sums["sxx"].where(sums["sxx"] > 0)
    intercept = g[y].mean() - slope * g[x].mean()
    r2 = (sums["sxy"] ** 2 / (sums["sxx"] * sums["syy"])).where(sums["syy"] > 0, 1.0)
    return pd.DataFrame({"n": n, "slope": slope, "intercept": intercept, "r2": r2})


# ------------------------------------------------------------------------------------- geometry
def geometry_table(systems: pd.DataFrame, profile: ProjectProfile) -> pd.DataFrame:
    """Leg sides, areas, volume, 1D conductance/resistance and contact/copper terms per system."""
    g, m, c = profile.geometry, profile.materials, profile.contacts
    theta = systems["theta_deg"].astype("float64")
    tan = np.tan(np.radians(theta))
    a_c = g.a_cold_mm * 1e-3
    a_h = a_c - 2.0 * g.height_mm * 1e-3 * tan
    h = g.height_mm * 1e-3
    shape = h / (a_h * a_c)  # ∫dx/A for a square frustum
    corr = 1.0 + g.c3d * tan**2
    out = pd.DataFrame(index=systems.index)
    out["a_hot_mm"] = a_h * 1e3
    out["a_cold_mm"] = g.a_cold_mm
    out["volume_mm3"] = 2.0 * (h / 3.0 * (a_h**2 + a_h * a_c + a_c**2)) * 1e9  # both legs
    out["k_legs_1d_w_k"] = 2.0 * m.kappa_te_w_mk / shape  # thesis Table 5.2 (pure 1D)
    out["r_legs_1d_ohm"] = 2.0 * m.rho_te_ohm_m * shape
    out["r_legs_ohm"] = out["r_legs_1d_ohm"] * corr  # with the frustum correction (docs/model.md)
    bridge = (a_c + g.gap_mm * 1e-3) / (g.t_cu_mm * 1e-3 * a_c)
    out["r_copper_ohm"] = m.rho_cu_ohm_m * bridge  # center-to-center Cu bridge; terminal leads excluded
    out["r_contacts_expected_ohm"] = 2.0 / (c.ecc_s_m2 * a_h**2) + 2.0 / (c.ecc_s_m2 * a_c**2)
    out["r_th_contact_hot_k_w"] = 1.0 / (c.tcc_w_m2k * 2.0 * a_h**2)  # two legs in parallel
    out["r_th_contact_cold_k_w"] = 1.0 / (c.tcc_w_m2k * 2.0 * a_c**2)
    return out


# ------------------------------------------------------------------------------------ resistance
def ohmic_resistance(frame: pd.DataFrame, oc: pd.DataFrame, alpha_fallback: float) -> pd.DataFrame:
    """Per system: median of (α_eff·ΔT_TE(I) − U(I))/I over the points with current, and its spread.

    α_eff comes from the system's own open circuit; systems without one (e.g. a refined-mesh copy that
    was only read at full load) use ``alpha_fallback`` - the same material, so the same coefficient.
    """
    f = _flat(frame)
    alpha = f["system"].map(oc["alpha_eff_v_k"]).fillna(alpha_fallback)
    i = f[Q.CURRENT.value]
    r = ((alpha * f["dt_te_k"] - f[Q.VOLTAGE.value]) / i).where(i.abs() > 1e-6)
    per_point = pd.DataFrame({"system": f["system"], "r": r}).dropna()
    g = per_point.groupby("system")["r"]
    out = pd.DataFrame({"ohmic_ohm": g.median(), "ohmic_points": g.size()})
    out["ohmic_spread"] = (g.max() - g.min()) / out["ohmic_ohm"]
    return out


def resistance_table(
    frame: pd.DataFrame, oc: pd.DataFrame, systems: pd.DataFrame, geometry: pd.DataFrame, alpha_default: float
) -> pd.DataFrame:
    """Apparent (regression), ohmic, legs, copper and contact resistances with a contact verdict."""
    f = _flat(frame)
    fit = linear_fit_by_system(f, Q.CURRENT.value, Q.VOLTAGE.value)
    out = pd.DataFrame(index=systems.index)
    out["apparent_ohm"] = (-fit["slope"]).where(fit["n"] >= 3)
    out["apparent_r2"] = fit["r2"].where(fit["n"] >= 3)
    out["apparent_is_thevenin"] = (systems["bc"] == BoundaryCondition.TT.value) & (out["apparent_r2"] >= LINEAR_R2)
    measured = oc["alpha_eff_v_k"].dropna()
    fallback = float(measured.median()) if not measured.empty else alpha_default
    out = out.join(ohmic_resistance(frame, oc, fallback))
    out["ohmic_points"] = out["ohmic_points"].fillna(0).astype(int)
    out["legs_ohm"] = geometry["r_legs_ohm"]
    out["copper_ohm"] = geometry["r_copper_ohm"]
    out["contacts_expected_ohm"] = geometry["r_contacts_expected_ohm"]
    out["contacts_isolated_ohm"] = out["ohmic_ohm"] - out["legs_ohm"] - out["copper_ohm"]
    out["thermal_feedback_ohm"] = out["apparent_ohm"] - out["ohmic_ohm"]
    ratio = out["contacts_isolated_ohm"] / out["contacts_expected_ohm"]
    out["contact_verdict"] = np.select(
        [ratio.isna(), ratio.abs() <= 0.35, (ratio - 1.0).abs() <= 0.35],
        ["unknown", "inactive", "active"],
        default="inconclusive",
    )
    return out


# --------------------------------------------------------------------------------------- thermal
def thermal_split(oc: pd.DataFrame, systems: pd.DataFrame, geometry: pd.DataFrame) -> pd.DataFrame:
    """Open-circuit split of the imposed ΔT into hot passives, legs and cold passives (thesis Table 4.3).

    Under BC-QT without a probe on the heated face, T_max_global at I = 0 stands in for the face
    temperature: at open circuit the global maximum sits on the heated ceramic (thesis Section 4.3).
    """
    t_hot = oc["t_hot_face_c"].fillna(oc["t_max_c"].where(systems["bc"] == BoundaryCondition.QT.value))
    out = pd.DataFrame(index=oc.index)
    out["hot_face_source"] = np.where(oc["t_hot_face_c"].notna(), "face", np.where(t_hot.notna(), "t_max", ""))
    out["imposed_dt_k"] = t_hot - oc["t_cold_face_c"]
    out["dt_te_k"] = oc["dt_te_k"]
    out["dt_hot_passive_k"] = t_hot - oc["t_leg_hot_c"]
    out["dt_cold_passive_k"] = oc["t_leg_cold_c"] - oc["t_cold_face_c"]
    out["passive_share"] = 1.0 - out["dt_te_k"] / out["imposed_dt_k"]
    out["r_th_hot_k_w"] = out["dt_hot_passive_k"] / oc["q_hot_w"]
    q_cold = oc["q_cold_w"].fillna(oc["q_hot_w"])  # identical at open circuit up to side losses
    out["r_th_cold_k_w"] = out["dt_cold_passive_k"] / q_cold
    out["r_th_legs_k_w"] = out["dt_te_k"] / oc["q_hot_w"]
    out["contact_dt_hot_expected_k"] = oc["q_hot_w"] * geometry["r_th_contact_hot_k_w"]
    out["contact_dt_cold_expected_k"] = q_cold * geometry["r_th_contact_cold_k_w"]
    return out


# ------------------------------------------------------------------------------------- max power
def max_power_by_system(frame: pd.DataFrame, resistance: pd.DataFrame) -> pd.DataFrame:
    """Maximum electrical power from the sampled load curve of every system.

    * linear (Thevenin) source: P_max = U_oc²/(4R), I_opt = U_oc/(2R) - exact, independent of sampling;
    * otherwise the parabola through the best sample and its two neighbours, solved for all systems in
      one batched ``np.linalg.solve``.  ``bracketed`` is False when the best sample sits at the end of the
      sweep or the vertex falls outside the three points: the value is then only a lower bound.
    """
    systems = resistance.index
    out = pd.DataFrame(
        {"p_max_w": np.nan, "i_opt_a": np.nan, "method": "no design point with current", "bracketed": False},
        index=systems,
    ).astype({"method": object})
    f = _flat(frame)[["system", Q.CURRENT.value, "power_w"]].dropna()
    f = f[f[Q.CURRENT.value] >= 0].sort_values(["system", Q.CURRENT.value]).reset_index(drop=True)
    if f.empty:
        return out

    # fallback for every system with a loaded point: the largest sample is a lower bound of P_max
    f["rank"] = f.groupby("system").cumcount()
    f["n"] = f.groupby("system")["power_w"].transform("size")
    best = f.loc[f.groupby("system")["power_w"].idxmax()].set_index("system")
    loaded = f[f[Q.CURRENT.value] > 0].groupby("system").size().reindex(best.index).fillna(0) > 0
    lower = best[loaded]
    out.loc[lower.index, "p_max_w"] = lower["power_w"]
    out.loc[lower.index, "i_opt_a"] = lower[Q.CURRENT.value]
    out.loc[lower.index, "method"] = "largest sampled point (lower bound)"

    # local parabola through the best sample and its neighbours, all systems at once
    f["center"] = f["system"].map(np.minimum(best["rank"].clip(lower=1), best["n"] - 2))
    window = f[(f["n"] >= 3) & ((f["rank"] - f["center"]).abs() <= 1)]
    if not window.empty:
        order = window["system"].to_numpy()[::3]
        x = window[Q.CURRENT.value].to_numpy().reshape(-1, 3)
        y = window["power_w"].to_numpy().reshape(-1, 3)
        vander = np.stack([x**2, x, np.ones_like(x)], axis=-1)  # (systems, 3, 3)
        a, b, c = np.linalg.solve(vander, y[..., None])[..., 0].T
        concave = a < 0
        safe_a = np.where(concave, a, -1.0)
        i_star, p_star = -b / (2.0 * safe_a), c - b**2 / (4.0 * safe_a)
        interior = (best.loc[order, "rank"] > 0).to_numpy() & (
            best.loc[order, "rank"] < best.loc[order, "n"] - 1
        ).to_numpy()
        ok = concave & interior & (i_star > x[:, 0]) & (i_star < x[:, 2])
        sel = order[ok]
        out.loc[sel, "p_max_w"] = p_star[ok]
        out.loc[sel, "i_opt_a"] = i_star[ok]
        out.loc[sel, "method"] = "local parabola (3 points)"
        out.loc[sel, "bracketed"] = True

    # a linear source has an exact optimum
    linear = resistance["apparent_is_thevenin"].fillna(False).astype(bool)
    fit = linear_fit_by_system(_flat(frame), Q.CURRENT.value, Q.VOLTAGE.value).reindex(systems)
    r = resistance["apparent_ohm"]
    out.loc[linear, "p_max_w"] = (fit["intercept"] ** 2 / (4.0 * r))[linear]
    out.loc[linear, "i_opt_a"] = (fit["intercept"] / (2.0 * r))[linear]
    out.loc[linear, "method"] = "linear source: U_oc²/(4R)"
    out.loc[linear, "bracketed"] = True
    return out


def suggest_currents(i_opt_a: float, span: tuple[float, ...] = (0.5, 0.7, 0.85, 1.0, 1.15, 1.3, 1.5)) -> list[float]:
    """Currents that bracket an expected optimum (rounded to 0.05 A), for the next ANSYS sweep."""
    if not math.isfinite(i_opt_a) or i_opt_a <= 0:
        return []
    return sorted({round(round(i_opt_a * s / 0.05) * 0.05, 2) for s in span if i_opt_a * s > 0})
