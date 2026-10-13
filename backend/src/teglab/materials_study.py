"""Material-level studies: high-temperature material selection, segmented legs, Thomson effect."""

from __future__ import annotations

import math

import numpy as np

from .analytic import eta_max_ideal
from .design import Boundary, Contacts, LegSpec, PairDesign, Passive, Segment
from .materials import MaterialLibrary, default_library
from .solver import K0, build_model, material_temperatures, max_efficiency, max_power, solve

__all__ = ["efficiency_map", "segmented_study", "thomson_study", "pair_design"]


def pair_design(
    p_segments: list[tuple[str, float]],
    n_segments: list[tuple[str, float]],
    t_hot_c: float,
    t_cold_c: float,
    *,
    a_mm: float = 1.4,
    height_mm: float = 1.4,
    n_area_ratio: float = 1.0,
    contacts: Contacts | None = None,
    passive: Passive | None = None,
    n_elements: int = 30,
) -> PairDesign:
    s = math.sqrt(n_area_ratio)
    return PairDesign(
        name="material study",
        height_mm=height_mm,
        p_leg=LegSpec(a_hot_mm=a_mm, a_cold_mm=a_mm, segments=[Segment(material=m, fraction=f) for m, f in p_segments]),
        n_leg=LegSpec(
            a_hot_mm=a_mm * s, a_cold_mm=a_mm * s, segments=[Segment(material=m, fraction=f) for m, f in n_segments]
        ),
        contacts=contacts or Contacts(active=False),
        passive=passive or Passive(ceramic_thickness_mm=0.0, cu_thickness_mm=0.0),
        boundary=Boundary(hot_mode="T", cold_mode="T", t_hot_c=t_hot_c, t_cold_c=t_cold_c),
        n_elements=n_elements,
    )


def _mean_zt(lib: MaterialLibrary, p: str, n: str, t_hot_k: float, t_cold_k: float) -> float:
    ts = np.linspace(t_cold_k, t_hot_k, 40)
    mp, mn = lib.get(p), lib.get(n)
    ap, rp, kp = mp.props(ts)
    an, rn, kn = mn.props(ts)
    # device ZT of a matched pair with optimal area ratio
    z = (ap - an) ** 2 / (np.sqrt(rp * kp) + np.sqrt(rn * kn)) ** 2
    return float(np.mean(z) * 0.5 * (t_hot_k + t_cold_k))


def efficiency_map(
    pairs: list[tuple[str, str]],
    t_cold_c: float,
    t_hot_values_c: list[float],
    lib: MaterialLibrary | None = None,
    *,
    with_contacts: Contacts | None = None,
    a_mm: float = 1.4,
    height_mm: float = 1.4,
) -> dict:
    """Maximum efficiency and power density vs hot-side temperature for P/N material pairs.

    Legs are prisms between fixed junction temperatures (no ceramics), so the
    result characterizes the material pair itself (plus optional contacts).
    """
    lib = lib or default_library()
    out = []
    for p_id, n_id in pairs:
        mp, mn = lib.get(p_id), lib.get(n_id)
        # optimal area ratio for a mid-range temperature (keeps sweep comparable)
        t_mid = 0.5 * (t_cold_c + max(t_hot_values_c)) + K0
        _, rp, kp = mp.props(np.array([t_mid]))
        _, rn, kn = mn.props(np.array([t_mid]))
        ratio = float(math.sqrt((rn[0] * kp[0]) / (rp[0] * kn[0])))
        rows = []
        t_prev = None
        for th in t_hot_values_c:
            if th <= t_cold_c + 1:
                continue
            d = pair_design(
                [(p_id, 1.0)],
                [(n_id, 1.0)],
                th,
                t_cold_c,
                a_mm=a_mm,
                height_mm=height_mm,
                n_area_ratio=ratio,
                contacts=with_contacts,
                n_elements=16,
            )
            model = build_model(d, lib)
            oc = solve(model, 0.0, t_prev)
            t_prev = None  # (node count identical, but the BC changed - start from the new OC field)
            me = max_efficiency(model, oc, rel_tol=1e-3)
            mpw = max_power(model, oc, rel_tol=1e-3)
            area = (d.p_leg.a_cold_mm**2 + d.n_leg.a_cold_mm**2) * 1e-6
            limit_ok = th + K0 <= min(mp.t_limit_k, mn.t_limit_k)
            rows.append(
                {
                    "t_hot_c": th,
                    "eta_max": me.efficiency,
                    "eta_ideal_zt": eta_max_ideal(
                        _mean_zt(lib, p_id, n_id, th + K0, t_cold_c + K0), th + K0, t_cold_c + K0
                    ),
                    "carnot": 1.0 - (t_cold_c + K0) / (th + K0),
                    "p_max_w": mpw.power_w,
                    "power_density_w_cm2": mpw.power_w / area * 1e-4,
                    "u_oc_v": oc.voltage_v,
                    "within_limits": limit_ok,
                }
            )
        out.append({"p": p_id, "n": n_id, "label": f"{mp.name} / {mn.name}", "n_area_ratio": ratio, "points": rows})
    return {"t_cold_c": t_cold_c, "series": out}


def segmented_study(
    p_hot: str,
    p_cold: str,
    n_hot: str,
    n_cold: str,
    t_hot_c: float,
    t_cold_c: float,
    fractions: list[float],
    lib: MaterialLibrary | None = None,
    *,
    a_mm: float = 1.4,
    height_mm: float = 5.0,
    contacts: Contacts | None = None,
) -> dict:
    """Two-segment legs: hot segment fraction f of the leg height (same f for P and N).

    f = 0 means the leg is entirely the cold-side material, f = 1 entirely hot-side.
    The interface temperature is reported so that the cold-side material limit
    (e.g. ~250 C for Bi2Te3) can be respected.
    """
    lib = lib or default_library()
    rows = []
    for f in fractions:
        f = float(min(max(f, 0.0), 1.0))
        if f <= 1e-6:
            ps, ns = [(p_cold, 1.0)], [(n_cold, 1.0)]
        elif f >= 1 - 1e-6:
            ps, ns = [(p_hot, 1.0)], [(n_hot, 1.0)]
        else:
            ps, ns = [(p_hot, f), (p_cold, 1 - f)], [(n_hot, f), (n_cold, 1 - f)]
        d = pair_design(ps, ns, t_hot_c, t_cold_c, a_mm=a_mm, height_mm=height_mm, contacts=contacts, n_elements=24)
        model = build_model(d, lib)
        oc = solve(model, 0.0)
        me = max_efficiency(model, oc, rel_tol=1e-3)
        mpw = max_power(model, oc, rel_tol=1e-3)
        mats = material_temperatures(model, me)
        interface = None
        if 0 < f < 1:
            x_if = f * height_mm * 1e-3
            ip = int(np.argmin(np.abs(model.legs[0].x - x_if)))
            interface = float(me.temps[model.leg_nodes(0)][ip] - K0)
        rows.append(
            {
                "fraction_hot": f,
                "eta_max": me.efficiency,
                "p_max_w": mpw.power_w,
                "u_oc_v": oc.voltage_v,
                "t_interface_c": interface,
                "materials": mats,
                "feasible": all(m["ok"] for m in mats),
            }
        )
    feasible = [r for r in rows if r["feasible"]]
    best = max(feasible, key=lambda r: r["eta_max"]) if feasible else None
    return {"t_hot_c": t_hot_c, "t_cold_c": t_cold_c, "rows": rows, "optimum": best}


def thomson_study(design: PairDesign, lib: MaterialLibrary | None = None) -> dict:
    """Full alpha(T) vs alpha frozen at its mean over each leg's operating range.

    Both variants share rho(T), kappa(T) and the open-circuit voltage (same
    Seebeck integral), so the difference isolates the distributed Thomson heat.
    """
    lib = lib or default_library()
    base_model = build_model(design, lib)
    oc = solve(base_model, 0.0)
    mp = max_power(base_model, oc)
    me = max_efficiency(base_model, oc)
    frozen_lib = lib.copy()
    d2 = design.model_copy(deep=True)
    for k, spec in enumerate((d2.p_leg, d2.n_leg)):
        t_h = oc.t_leg_hot_c[k] + K0
        t_c = oc.t_leg_cold_c[k] + K0
        new_segments = []
        for seg in spec.segments:
            m = lib.get(seg.material)
            a_mean = m.seebeck_integral(t_c, t_h) / (t_h - t_c) if t_h > t_c else float(m.props(np.array([t_c]))[0][0])
            m2 = m.with_constant_alpha(a_mean, suffix=f"const-alpha-{k}")
            frozen_lib.add(m2)
            new_segments.append(Segment(material=m2.id, fraction=seg.fraction))
        spec.segments = new_segments
    fm = build_model(d2, frozen_lib)
    oc2 = solve(fm, 0.0)
    mp2 = max_power(fm, oc2)
    me2 = max_efficiency(fm, oc2)
    # distributed Thomson heat at the max-power point (sum over elements of -tau*I*dT)
    q_th = 0.0
    for k, lg in enumerate(base_model.legs):
        nodes = base_model.leg_nodes(k)
        t = mp.temps[nodes]
        t_el = 0.5 * (t[:-1] + t[1:])
        dadt = np.zeros_like(t_el)
        for i_m, m in enumerate(lg.materials):
            sel = lg.mat_index == i_m
            dadt[sel] = m.dalpha_dt(t_el[sel])
        tau = t_el * dadt * design.alpha_factor
        q_th += float(np.sum(-tau * lg.sign * mp.current_a * (t[1:] - t[:-1])))
    return {
        "with_thomson": {
            "p_max_w": mp.power_w,
            "eta_max": me.efficiency,
            "u_oc_v": oc.voltage_v,
            "q_hot_pmax_w": mp.q_hot_w,
        },
        "without_thomson": {
            "p_max_w": mp2.power_w,
            "eta_max": me2.efficiency,
            "u_oc_v": oc2.voltage_v,
            "q_hot_pmax_w": mp2.q_hot_w,
        },
        "delta_p_rel": mp.power_w / mp2.power_w - 1.0 if mp2.power_w else None,
        "delta_eta_rel": me.efficiency / me2.efficiency - 1.0 if me2.efficiency else None,
        "thomson_heat_w": q_th,
        "note": "Constant-property materials have tau = 0, so both variants coincide.",
    }
