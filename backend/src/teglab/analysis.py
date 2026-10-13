"""Post-processing of imported ANSYS (or lab) design-point series.

For every case: load curve U(I), P(I) with fitted optimum, efficiencies, heat
balance, derived pair properties (K, alpha_eff, passive share) and a set of
automatic consistency checks.  One of them - the *implied internal
resistance* R = (alpha*dT_TE - U)/I - compares the electrical behavior with
the expected contact/electrode resistances and flags systems in which the
contact definitions probably did not take effect.
"""

from __future__ import annotations

import math

import numpy as np
from scipy.interpolate import CubicSpline

from .cases import Case, case_array, design_for_case
from .design import PairDesign
from .geometry import frustum_volume
from .materials import MaterialLibrary, default_library
from .solver import K0, build_model, solve

__all__ = ["analyze_case", "compare_cases", "gci", "load_curve"]


def _finite(*arrays: np.ndarray) -> np.ndarray:
    mask = np.ones_like(arrays[0], dtype=bool)
    for a in arrays:
        mask &= np.isfinite(a)
    return mask


def load_curve(
    current: np.ndarray, voltage: np.ndarray, power: np.ndarray | None = None, bc: str | None = None
) -> dict:
    """Fits of U(I) and P(I) and the resulting maximum-power estimates.

    Under BC-TT with constant properties U(I) is a straight line and the
    optimum follows from the regression.  Otherwise (BC-QT, T-dependent
    properties) the optimum is located with a local least-squares parabola
    around the largest sampled power, and a cubic spline gives a second
    estimate; their spread is reported as a sampling-quality indicator.
    """
    m = _finite(current, voltage)
    i, u = current[m], voltage[m]
    p = (u * i) if power is None else power[m]
    order = np.argsort(i)
    i, u, p = i[order], u[order], p[order]
    out: dict = {"n_points": int(i.size)}
    if i.size >= 2 and np.ptp(i) > 0:
        slope, icpt = np.polyfit(i, u, 1)
        pred = icpt + slope * i
        ss_res = float(np.sum((u - pred) ** 2))
        ss_tot = float(np.sum((u - np.mean(u)) ** 2)) or 1e-30
        r2 = 1.0 - ss_res / ss_tot
        out["linear"] = {
            "u0_v": float(icpt),
            "r_ohm": float(-slope),
            "r2": r2,
            "i_sc_a": float(icpt / -slope) if slope < 0 else None,
            "i_opt_a": float(icpt / (-2 * slope)) if slope < 0 else None,
            "p_max_w": float(icpt**2 / (-4 * slope)) if slope < 0 else None,
        }
    estimates = {}
    if i.size >= 2:
        # parabola through the origin, P = c1*I + c2*I^2 (least squares over all points)
        A = np.column_stack([i, i * i])
        (c1, c2), *_ = np.linalg.lstsq(A, p, rcond=None)
        if c2 < 0:
            estimates["parabola_origin"] = {"i_opt_a": float(-c1 / (2 * c2)), "p_max_w": float(-c1 * c1 / (4 * c2))}
    if i.size >= 3:
        k = int(np.argmax(p))
        width = 5 if i.size >= 5 else 3
        lo = min(max(k - width // 2, 0), i.size - width)
        sel = slice(lo, lo + width)
        c = np.polyfit(i[sel], p[sel], 2)
        if c[0] < 0:
            estimates["parabola_local"] = {
                "i_opt_a": float(-c[1] / (2 * c[0])),
                "p_max_w": float(c[2] - c[1] ** 2 / (4 * c[0])),
                "points_a": i[sel].tolist(),
            }
    if i.size >= 4 and np.all(np.diff(i) > 0):
        f = CubicSpline(i, p)
        dense = np.linspace(i[0], i[-1], 4001)
        pd = f(dense)
        k = int(np.argmax(pd))
        if 0 < k < dense.size - 1:
            estimates["spline"] = {"i_opt_a": float(dense[k]), "p_max_w": float(pd[k])}
    lin = out.get("linear")
    if lin and lin.get("p_max_w") and i.size >= 3 and lin["r2"] > 0.99999 and bc != "QT":
        best = {"method": "linear U(I)", "i_opt_a": lin["i_opt_a"], "p_max_w": lin["p_max_w"]}
    elif "parabola_local" in estimates:
        n_loc = len(estimates["parabola_local"]["points_a"])
        best = {
            "method": f"local parabola ({n_loc} points)",
            **{k: v for k, v in estimates["parabola_local"].items() if k != "points_a"},
        }
    elif "parabola_origin" in estimates:
        best = {"method": f"parabola through origin ({i.size} points)", **estimates["parabola_origin"]}
    elif np.sum(i > 1e-9) >= 1:
        k = int(np.argmax(p))
        best = {"method": "largest sampled point", "i_opt_a": float(i[k]), "p_max_w": float(p[k])}
    else:
        best = None  # open-circuit data only
    local = [e["p_max_w"] for k, e in estimates.items() if k in ("parabola_local", "spline")]
    vals = local if len(local) > 1 else [e["p_max_w"] for e in estimates.values()]
    out["estimates"] = estimates
    out["best"] = best
    out["p_max_spread_rel"] = float((max(vals) - min(vals)) / max(abs(max(vals)), 1e-30)) if len(vals) > 1 else None
    return out


def _sampling_advice(i: np.ndarray, i_opt: float | None) -> list[float]:
    if i_opt is None or i.size == 0 or not math.isfinite(i_opt) or i_opt <= 0:
        return []
    xs = np.sort(i[np.isfinite(i)])
    gaps = np.diff(xs)
    k = np.searchsorted(xs, i_opt)
    if 0 < k < xs.size and gaps[k - 1] <= 0.25 * i_opt:
        return []
    step = 0.05 if i_opt > 0.3 else 0.01
    cand = sorted({round(i_opt * f / step) * step for f in (0.6, 0.8, 0.9, 1.0, 1.1, 1.2, 1.4)})
    have = set(np.round(xs / step) * step)
    return [float(round(c, 4)) for c in cand if c > 0 and round(c / step) * step not in have]


def _check(level: str, cid: str, title: str, detail: str, **extra) -> dict:
    return {"id": cid, "level": level, "title": title, "detail": detail, **extra}


def analyze_case(case: Case, design: PairDesign | None = None, library: MaterialLibrary | None = None) -> dict:
    lib = library or default_library()
    meta = case.meta
    I = case_array(case, "current")
    n = I.size
    U = case_array(case, "voltage")
    P = case_array(case, "power") if case.has("power") else U * I
    if not case.has("power") and not case.has("voltage"):
        P = np.full(n, np.nan)
    if case.has("q_hot"):
        Qh = case_array(case, "q_hot")
    elif meta.bc == "QT" and meta.q_hot_w is not None:
        Qh = np.full(n, meta.q_hot_w)
    else:
        Qh = np.full(n, np.nan)
    Qc = case_array(case, "q_cold")
    Th = case_array(case, "t_hot_ext")
    if not np.any(np.isfinite(Th)) and meta.bc == "TT" and meta.t_hot_c is not None:
        Th = np.full(n, meta.t_hot_c)
    Tc = case_array(case, "t_cold_ext")
    if not np.any(np.isfinite(Tc)) and meta.t_cold_c is not None:
        Tc = np.full(n, meta.t_cold_c)
    Tlh, Tlc = case_array(case, "t_leg_hot"), case_array(case, "t_leg_cold")
    dT_te = Tlh - Tlc
    with np.errstate(divide="ignore", invalid="ignore"):
        eta = np.where(Qh > 0, P / Qh, np.nan)
        carnot = 1.0 - (Tc + K0) / (Th + K0)
        eta_ex = np.where(carnot > 0, eta / carnot, np.nan)
        balance = (Qh - Qc - P) / Qh

    curve = load_curve(I, U, P, meta.bc) if case.has("voltage") or case.has("power") else {"n_points": 0}
    best = curve.get("best") or {}
    i0 = int(np.argmin(np.abs(I))) if n and np.nanmin(np.abs(I)) < 1e-9 else None

    def at0(a: np.ndarray) -> float | None:
        return float(a[i0]) if i0 is not None and math.isfinite(a[i0]) else None

    u_oc = at0(U) if at0(U) is not None else (curve.get("linear") or {}).get("u0_v")
    dte0 = at0(dT_te)
    qh0 = at0(Qh)
    metrics: dict = {
        "u_oc_v": u_oc,
        "dt_te_oc_k": dte0,
        "q_hot_oc_w": qh0,
        "t_hot_ext_oc_c": at0(Th),
        "k_pair_w_k": qh0 / dte0 if qh0 and dte0 else None,
        "alpha_eff_v_k": u_oc / dte0 if u_oc and dte0 else None,
        "p_max_w": best.get("p_max_w"),
        "i_opt_a": best.get("i_opt_a"),
        "p_max_method": best.get("method"),
        "r_int_fit_ohm": (curve.get("linear") or {}).get("r_ohm"),
        "r2_linear": (curve.get("linear") or {}).get("r2"),
    }
    if dte0 and at0(Th) is not None and at0(Tc) is not None:
        metrics["passive_share"] = 1.0 - dte0 / (at0(Th) - at0(Tc))
    if np.any(np.isfinite(eta)):
        k = int(np.nanargmax(eta))
        metrics["eta_max_sampled"] = float(eta[k])
        metrics["i_eta_max_sampled_a"] = float(I[k])
        if best.get("i_opt_a") is not None and np.sum(np.isfinite(eta)) >= 2:
            m = _finite(I, eta)
            metrics["eta_at_p_max"] = float(np.interp(best["i_opt_a"], I[m], eta[m]))
        if np.any(np.isfinite(eta_ex)):
            metrics["exergy_eta_max_sampled"] = float(np.nanmax(eta_ex))
    geo = _case_geometry(case, design)
    if geo:
        v_te = 2.0 * frustum_volume(geo["a_hot_mm"], geo["a_cold_mm"], geo["height_mm"])
        metrics["te_volume_mm3"] = v_te
        if best.get("p_max_w"):
            metrics["volume_per_power_mm3_mw"] = v_te / (best["p_max_w"] * 1e3)
            metrics["power_per_volume_mw_mm3"] = best["p_max_w"] * 1e3 / v_te
        metrics["geometry"] = geo

    checks: list[dict] = []
    if np.any(np.isfinite(balance)):
        worst = float(np.nanmax(np.abs(balance)))
        lvl = "ok" if worst <= 1e-3 else "warn"
        checks.append(
            _check(
                lvl,
                "energy_balance",
                "Energy balance Q_h − Q_c − P",
                f"largest relative residual {worst * 100:.4f} % (criterion 0.1 %)",
                value=worst,
            )
        )
    lin = curve.get("linear")
    if lin:
        if meta.bc == "TT":
            lvl = "ok" if lin["r2"] > 0.9999 else "warn"
            checks.append(
                _check(
                    lvl,
                    "linearity",
                    "Linearity of U(I) (BC-TT)",
                    f"R² = {lin['r2']:.6f}; constant properties + fixed temperatures give a straight line",
                    value=lin["r2"],
                )
            )
        elif meta.bc == "QT":
            checks.append(
                _check(
                    "info",
                    "linearity",
                    "U(I) under fixed heat input",
                    f"R² = {lin['r2']:.5f}. ΔT collapses with current, so the regression slope "
                    f"({lin['r_ohm'] * 1e3:.2f} mΩ) is not the internal resistance.",
                    value=lin["r2"],
                )
            )
    linear_best = best.get("method") == "linear U(I)"
    bracketed = best.get("method") != "largest sampled point"
    advice = [] if linear_best else _sampling_advice(I[np.isfinite(P)], best.get("i_opt_a"))
    if advice and bracketed:
        checks.append(
            _check(
                "warn",
                "sampling",
                "Sparse sampling around the optimum",
                f"I_opt ≈ {best['i_opt_a']:.3f} A lies in a wide gap between sampled currents. "
                f"Add design points at {', '.join(f'{a:g}' for a in advice)} A.",
                currents=advice,
            )
        )
    if not bracketed and best.get("p_max_w"):
        extra = f" Suggested currents: {', '.join(f'{a:g}' for a in advice)} A." if advice else ""
        checks.append(
            _check(
                "warn",
                "pmax_bracket",
                "Maximum power not bracketed",
                f"P_max = {best['p_max_w'] * 1e3:.3f} mW is only the largest sampled value (a lower bound). "
                "Add currents on both sides of the expected optimum." + extra,
                currents=advice,
            )
        )
    spread = curve.get("p_max_spread_rel")
    if spread is not None and spread > 0.02:
        checks.append(
            _check(
                "warn",
                "pmax_method",
                "P_max depends on the fitting method",
                f"estimates differ by {spread * 100:.1f} % – refine the current sweep near I_opt",
                value=spread,
            )
        )
    if design is not None:
        checks.extend(_physics_checks(case, design, lib, I, U, dT_te, Th, Tlh, Tlc, Qh, metrics))

    return {
        "case_id": case.id,
        "name": case.name,
        "meta": meta.model_dump(),
        "points": {
            "current_a": _j(I),
            "voltage_v": _j(U),
            "power_w": _j(P),
            "q_hot_w": _j(Qh),
            "q_cold_w": _j(Qc),
            "efficiency": _j(eta),
            "exergy_efficiency": _j(eta_ex),
            "balance_rel": _j(balance),
            "t_hot_ext_c": _j(Th),
            "t_cold_ext_c": _j(Tc),
            "t_leg_hot_c": _j(Tlh),
            "t_leg_cold_c": _j(Tlc),
            "dt_te_k": _j(dT_te),
            "t_max_c": _j(case_array(case, "t_max")),
            "j_max_a_m2": _j(case_array(case, "j_max")),
        },
        "curve": curve,
        "metrics": metrics,
        "checks": checks,
    }


def _case_geometry(case: Case, design: PairDesign | None) -> dict | None:
    m = case.meta
    if design is None and (m.a_hot_mm is None or m.a_cold_mm is None or m.height_mm is None):
        if not (case.has("angle") or case.has("a_hot")):
            return None
    base = design or _fallback_design()
    d = design_for_case(base, case)
    return {
        "a_hot_mm": d.p_leg.a_hot_mm,
        "a_cold_mm": d.p_leg.a_cold_mm,
        "height_mm": d.height_mm,
        "theta_deg": d.taper_deg("p"),
    }


def _fallback_design() -> PairDesign:
    from .design import reference_design

    return reference_design()


def _physics_checks(case, design, lib, I, U, dT_te, Th, Tlh, Tlc, Qh, metrics) -> list[dict]:
    """Compare the data with the reduced-order model for contacts on / off."""
    out: list[dict] = []
    d_on = design_for_case(design, case)
    d_on.contacts.active = True
    d_off = d_on.model_copy(deep=True)
    d_off.contacts.active = False
    try:
        m_on, m_off = build_model(d_on, lib), build_model(d_off, lib)
    except Exception as exc:  # unknown material etc.
        return [_check("info", "model", "Model checks skipped", str(exc))]
    oc_on = solve(m_on, 0.0)
    alpha_pn = oc_on.emf_v / oc_on.dt_te_k if oc_on.dt_te_k > 0 else None
    a_eff = metrics.get("alpha_eff_v_k")
    if alpha_pn and a_eff:
        dev = a_eff / alpha_pn - 1.0
        out.append(
            _check(
                "ok" if abs(dev) < 0.01 else "warn",
                "seebeck",
                "Effective Seebeck coefficient",
                f"U_oc/ΔT_TE = {a_eff * 1e6:.1f} µV/K vs material {alpha_pn * 1e6:.1f} µV/K ({dev * 100:+.2f} %)",
                value=a_eff,
                expected=alpha_pn,
            )
        )
    # implied internal resistance from EMF = alpha * dT_TE
    sel = _finite(I, U, dT_te) & (I > 1e-6)
    if alpha_pn and np.any(sel):
        r_impl = (alpha_pn * dT_te[sel] - U[sel]) / I[sel]
        r_data = float(np.median(r_impl))
        r_on = oc_on.r_int_ohm
        r_off = solve(m_off, 0.0).r_int_ohm
        closer = (
            "ideal (bonded) contacts" if abs(r_data - r_off) < abs(r_data - r_on) else "finite-conductance contacts"
        )
        lvl = "ok"
        expect_on = case.meta.contacts_active if case.meta.contacts_active is not None else design.contacts.active
        if expect_on and abs(r_data - r_off) < abs(r_data - r_on) and (r_on - r_off) > 0.02 * r_on:
            lvl = "warn"
        out.append(
            _check(
                lvl,
                "implied_rint",
                "Implied internal resistance (α·ΔT_TE − U)/I",
                f"data: {r_data * 1e3:.2f} mΩ; model with contacts: {r_on * 1e3:.2f} mΩ; "
                f"with ideal contacts: {r_off * 1e3:.2f} mΩ → the data behaves like {closer}."
                + (" Check that TCC/ECC are active in this analysis system." if lvl == "warn" else ""),
                value=r_data,
                expected=r_on,
                alternative=r_off,
            )
        )
    # thermal: hot-side passive resistance at open circuit
    i0 = np.flatnonzero(np.abs(I) < 1e-9)
    if i0.size and np.isfinite(Th[i0[0]]) and np.isfinite(Tlh[i0[0]]) and np.isfinite(Qh[i0[0]]) and Qh[i0[0]] > 0:
        k = i0[0]
        r_data = (Th[k] - Tlh[k]) / Qh[k]
        off = solve(m_off, 0.0)
        r_on = oc_on.dt_passive_hot_k / oc_on.q_hot_w
        r_off = off.dt_passive_hot_k / off.q_hot_w
        closer_off = abs(r_data - r_off) < abs(r_data - r_on)
        out.append(
            _check(
                "warn" if closer_off and design.contacts.active and (r_on - r_off) > 0.1 * r_on else "info",
                "hot_passive",
                "Hot-side passive resistance (ceramic + Cu + contact)",
                f"data: {r_data:.2f} K/W; model with contacts: {r_on:.2f} K/W; ideal contacts: {r_off:.2f} K/W",
                value=float(r_data),
                expected=r_on,
                alternative=r_off,
            )
        )
    k_data = metrics.get("k_pair_w_k")
    if k_data:
        k_model = oc_on.q_hot_w / oc_on.dt_te_k
        dev = k_data / k_model - 1.0
        out.append(
            _check(
                "ok" if abs(dev) < 0.03 else "info",
                "k_pair",
                "Thermal conductance of the legs",
                f"K = {k_data * 1e3:.3f} mW/K vs model {k_model * 1e3:.3f} mW/K ({dev * 100:+.2f} %)",
                value=k_data,
                expected=k_model,
            )
        )
    return out


def _j(a: np.ndarray) -> list[float | None]:
    return [None if not math.isfinite(float(v)) else float(v) for v in a]


def compare_cases(analyses: list[dict], reference: int = 0) -> dict:
    """Variant comparison table (thesis Table 5.3 style) relative to ``reference``."""
    rows = []
    for a in analyses:
        m = a["metrics"]
        rows.append(
            {
                "case_id": a["case_id"],
                "name": a["name"],
                "theta_deg": (m.get("geometry") or {}).get("theta_deg"),
                "k_pair_w_k": m.get("k_pair_w_k"),
                "u_oc_v": m.get("u_oc_v"),
                "dt_te_oc_k": m.get("dt_te_oc_k"),
                "t_hot_ext_oc_c": m.get("t_hot_ext_oc_c"),
                "p_max_w": m.get("p_max_w"),
                "i_opt_a": m.get("i_opt_a"),
                "eta_at_p_max": m.get("eta_at_p_max"),
                "te_volume_mm3": m.get("te_volume_mm3"),
                "power_per_volume_mw_mm3": m.get("power_per_volume_mw_mm3"),
                "volume_per_power_mm3_mw": m.get("volume_per_power_mm3_mw"),
            }
        )
    if not rows:
        return {"rows": [], "reference": None}
    ref = rows[min(max(reference, 0), len(rows) - 1)]
    for r in rows:
        for key in ("k_pair_w_k", "u_oc_v", "p_max_w", "te_volume_mm3", "power_per_volume_mw_mm3", "eta_at_p_max"):
            a, b = r.get(key), ref.get(key)
            r[f"d_{key}"] = (a / b - 1.0) if (a is not None and b not in (None, 0)) else None
    return {"rows": rows, "reference": ref["case_id"]}


def gci(
    f_coarse: float,
    f_fine: float,
    r: float,
    p: float = 2.0,
    fs: float | None = None,
    f_medium: float | None = None,
) -> dict:
    """Grid Convergence Index (Roache).

    Two grids: GCI = Fs*|eps|/(r^p - 1) with the formal order p (Fs = 3 by default).
    Three grids (coarse, medium, fine with constant r): observed order p and Fs = 1.25.
    """
    if f_medium is not None:
        e32, e21 = f_medium - f_coarse, f_fine - f_medium
        if e21 == 0 or e32 == 0 or e32 / e21 <= 0:
            p_obs = None
        else:
            p_obs = math.log(abs(e32 / e21)) / math.log(r)
        p_use = p_obs if p_obs and p_obs > 0 else p
        fs = 1.25 if fs is None else fs
        eps = (f_fine - f_medium) / f_fine
        g = fs * abs(eps) / (r**p_use - 1.0)
        f_ext = f_fine + (f_fine - f_medium) / (r**p_use - 1.0)
        g32 = fs * abs((f_medium - f_coarse) / f_medium) / (r**p_use - 1.0)
        return {
            "p_observed": p_obs,
            "p_used": p_use,
            "fs": fs,
            "gci_fine": g,
            "gci_medium": g32,
            "asymptotic_ratio": g32 / (r**p_use * g) if g > 0 else None,
            "f_extrapolated": f_ext,
            "rel_change": abs(eps),
        }
    fs = 3.0 if fs is None else fs
    eps = (f_fine - f_coarse) / f_fine
    return {
        "p_used": p,
        "fs": fs,
        "gci_fine": fs * abs(eps) / (r**p - 1.0),
        "f_extrapolated": f_fine + (f_fine - f_coarse) / (r**p - 1.0),
        "rel_change": abs(eps),
    }
