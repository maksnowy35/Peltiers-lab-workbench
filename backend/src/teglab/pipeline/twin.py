"""1D twin of every ANSYS system: the same pair solved with the finite-volume model of ``solver.py``.

Each system is rebuilt from the project profile (geometry, constant materials, contact conductances,
passive layers) and its own boundary condition and variant angle.  The twin is then solved

* at open circuit, for U_oc, ΔT_TE and the pair conductance K,
* at its own maximum-power current, for P_max and I_opt,
* at every ANSYS design-point current, for U, P, Q_h, Q_c and ΔT_TE point by point.

The deviations are what the FEM study adds to (or loses against) the 1D picture: frustum spreading,
current crowding at the contacts, passive-layer conduction.  They feed the QA gates in ``qa.py``.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from ..design import Boundary, Contacts, LegSpec, PairDesign, Passive, Segment
from ..materials import Material, MaterialLibrary
from ..solver import PairModel, build_model, max_power, solve
from .keys import QuantityKey as Q
from .schemas import BoundaryCondition, ProjectProfile, QaStatus, TwinMetric, TwinPoint, TwinProfile, TwinSystem

__all__ = ["twin_design", "twin_library", "twin_system", "verdict"]

P_ID, N_ID = "profile_p", "profile_n"


def twin_library(profile: ProjectProfile) -> MaterialLibrary:
    """Constant p and n materials exactly as declared in the profile (thesis Table 3.2 for the thesis)."""
    m = profile.materials

    def mat(id_: str, kind: str, alpha: float) -> Material:
        return Material(
            id=id_,
            name=f"{kind}-type leg ({profile.name})",
            kind=kind,
            family="profile",
            t_k=np.array([300.0]),
            alpha=np.array([alpha]),
            rho=np.array([m.rho_te_ohm_m]),
            kappa=np.array([m.kappa_te_w_mk]),
            t_limit_k=1000.0,
            density_g_cm3=7.0,
            source=f"project profile {profile.name}",
        )

    return MaterialLibrary([mat(P_ID, "p", m.alpha_p_v_k), mat(N_ID, "n", m.alpha_n_v_k)])


def twin_design(
    profile: ProjectProfile,
    theta_deg: float,
    bc: BoundaryCondition,
    t_cold_c: float,
    q_hot_w: float | None = None,
    t_hot_c: float | None = None,
    settings: TwinProfile | None = None,
) -> PairDesign:
    """Pair design of one system; ``settings`` overrides the profile's twin block (used by calibration)."""
    g, c, tw = profile.geometry, profile.contacts, settings or profile.twin
    a_hot = g.a_hot_mm(theta_deg)
    if a_hot <= 0:
        raise ValueError(f"θ = {theta_deg}° gives a non-positive hot-end side ({a_hot:.3f} mm)")
    if bc is BoundaryCondition.QT:
        boundary = Boundary(hot_mode="Q", cold_mode="T", q_hot_w=float(q_hot_w or 0.0), t_cold_c=t_cold_c)
    else:
        boundary = Boundary(hot_mode="T", cold_mode="T", t_hot_c=float(t_hot_c or t_cold_c), t_cold_c=t_cold_c)
    leg = {"a_hot_mm": a_hot, "a_cold_mm": g.a_cold_mm}
    return PairDesign(
        name=f"twin θ={theta_deg:g}°",
        height_mm=g.height_mm,
        p_leg=LegSpec(**leg, segments=[Segment(material=P_ID)]),
        n_leg=LegSpec(**leg, segments=[Segment(material=N_ID)]),
        contacts=Contacts(active=True, tcc=c.tcc_w_m2k, ecc=c.ecc_s_m2),
        passive=Passive(
            ceramic_thickness_mm=g.t_ceramic_mm,
            ceramic_k=tw.ceramic_k_w_mk,
            cu_thickness_mm=g.t_cu_mm,
            cu_k=tw.cu_k_w_mk,
            cu_rho=profile.materials.rho_cu_ohm_m,
            gap_mm=g.gap_mm,
            hot_side_factor=tw.hot_side_factor,
            cold_side_factor=tw.cold_side_factor,
            r_el_extra_mohm=tw.r_el_extra_mohm,
        ),
        boundary=boundary,
        c3d=g.c3d,
        n_elements=tw.n_elements,
    )


def verdict(deviation: float | None, tolerance: float) -> QaStatus:
    """pass within the tolerance, warn up to twice the tolerance, fail beyond."""
    if deviation is None or not math.isfinite(deviation):
        return "skip"
    d = abs(deviation)
    return "pass" if d <= tolerance else "warn" if d <= 2.0 * tolerance else "fail"


def _num(value: object) -> float | None:
    try:
        v = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _dev(model: float | None, ansys: float | None) -> float | None:
    if model is None or ansys is None or abs(ansys) < 1e-15:
        return None
    return (model - ansys) / abs(ansys)


def _metric(key: str, label: str, unit: str, ansys: object, model: float | None, tol: float | None) -> TwinMetric:
    a = _num(ansys)
    d = _dev(model, a)
    scale = 1000.0 if unit.startswith("m") else 1.0
    return TwinMetric(
        key=key,
        label=label,
        unit=unit,
        scale=scale,
        ansys=a,
        model=model,
        deviation=d,
        tolerance=tol,
        status=verdict(d, tol) if tol is not None else "skip",
    )


def _system_inputs(
    row: pd.Series, profile: ProjectProfile
) -> tuple[float, BoundaryCondition, float, float | None, float | None]:
    theta = _num(row.get("theta_deg"))
    if theta is None:
        raise LookupError("no variant angle: assign a variant (A0, A5, ...) or a profile system rule")
    bc_raw = row.get("bc")
    if bc_raw is None or (isinstance(bc_raw, float) and math.isnan(bc_raw)):
        raise LookupError("no boundary condition")
    bc = BoundaryCondition(str(bc_raw))
    t_cold = _num(row.get("imposed_t_cold_face_c"))
    if t_cold is None:
        raise LookupError("no cold-face temperature")
    q_hot = _num(row.get("imposed_q_hot_w"))
    t_hot = _num(row.get("imposed_t_hot_face_c"))
    if bc is BoundaryCondition.QT and q_hot is None:
        raise LookupError("BC-QT without the imposed heat input")
    if bc is BoundaryCondition.TT and t_hot is None:
        raise LookupError("BC-TT without the imposed hot-face temperature")
    return theta, bc, t_cold, q_hot, t_hot


def _point(
    dp: str, r: pd.Series, model: PairModel, t_init: np.ndarray | None, u_ref: float, p_ref: float, q_hot_imposed: bool
) -> tuple[TwinPoint, np.ndarray]:
    """Solve one design point.  U and P are compared on the scale of U_oc and P_max, because both pass
    through zero (open circuit, short circuit) where a plain relative deviation means nothing."""
    i = float(r[Q.CURRENT.value])
    sol = solve(model, i, t_init)
    u_a, p_a = _num(r.get(Q.VOLTAGE.value)), _num(r.get("power_w"))
    qh_a, qc_a = _num(r.get(Q.Q_HOT.value)), _num(r.get(Q.Q_COLD.value))
    devs = []
    for model_v, ansys_v, floor in (
        (sol.voltage_v, u_a, 0.25 * u_ref),
        (sol.power_w, p_a, 0.25 * p_ref),
        (sol.q_hot_w, None if q_hot_imposed else qh_a, 0.0),  # under BC-QT Q_h is an input
        (sol.q_cold_w, qc_a, 0.0),
    ):
        if ansys_v is None:
            continue
        scale = max(abs(ansys_v), floor)
        if scale > 1e-15:
            devs.append(abs(model_v - ansys_v) / scale)
    point = TwinPoint(
        design_point=str(dp),
        current_a=i,
        voltage_ansys_v=u_a,
        voltage_model_v=sol.voltage_v,
        power_ansys_w=p_a,
        power_model_w=sol.power_w,
        q_hot_ansys_w=qh_a,
        q_hot_model_w=sol.q_hot_w,
        q_cold_ansys_w=qc_a,
        q_cold_model_w=sol.q_cold_w,
        dt_te_ansys_k=_num(r.get("dt_te_k")),
        dt_te_model_k=sol.dt_te_k,
        worst_deviation=max(devs) if devs else None,
    )
    return point, sol.temps


def twin_system(
    row: pd.Series,
    points: pd.DataFrame | None,
    profile: ProjectProfile,
    settings: TwinProfile | None = None,
    calibrated: bool = False,
) -> TwinSystem:
    """Solve the twin of one system (``row`` from ``summarize``, ``points`` its design-point rows)."""
    tw = settings or profile.twin
    try:
        theta, bc, t_cold, q_hot, t_hot = _system_inputs(row, profile)
        design = twin_design(profile, theta, bc, t_cold, q_hot, t_hot, tw)
    except (LookupError, ValueError) as exc:
        return TwinSystem(status="skipped", note=str(exc))
    try:
        model = build_model(design, twin_library(profile))
        oc = solve(model, 0.0)
        mp = max_power(model, oc)
    except (ArithmeticError, ValueError, RuntimeError) as exc:  # pragma: no cover - solver safety net
        return TwinSystem(status="failed", note=f"1D solver: {exc}")

    k_model = oc.q_hot_w / oc.dt_te_k if oc.dt_te_k > 0 else None
    bracketed = bool(row.get("bracketed", False))  # an unbracketed fit is a point value, not the optimum
    metrics = [
        _metric("u_oc_v", "U_oc", "mV", row.get("u_oc_v"), oc.voltage_v, tw.tol_u_oc),
        _metric("dt_te_k", "ΔT_TE at open circuit", "K", row.get("dt_te_k"), oc.dt_te_k, tw.tol_dt_te),
        _metric("k_te_w_k", "Pair conductance K", "mW/K", row.get("k_te_w_k"), k_model, tw.tol_dt_te),
        _metric("ohmic_ohm", "R ohmic", "mΩ", row.get("ohmic_ohm"), mp.r_int_ohm, None),
        _metric("p_max_w", "P_max", "mW", row.get("p_max_w") if bracketed else None, mp.power_w, tw.tol_p_max),
        _metric("i_opt_a", "I_opt", "A", row.get("i_opt_a") if bracketed else None, mp.current_a, None),
    ]
    if bc is BoundaryCondition.TT:
        metrics.append(_metric("q_hot_w", "Q_h at open circuit", "mW", row.get("q_hot_w"), oc.q_hot_w, tw.tol_point))
    else:
        metrics.append(
            _metric("t_hot_face_c", "T_h,ext at open circuit", "°C", row.get("t_hot_face_c"), oc.t_hot_ext_c, None)
        )

    out_points: list[TwinPoint] = []
    if points is not None and not points.empty and Q.CURRENT.value in points:
        t_init = oc.temps
        for dp, r in points.sort_values(Q.CURRENT.value).iterrows():
            if _num(r.get(Q.CURRENT.value)) is None:
                continue
            point, t_init = _point(dp, r, model, t_init, abs(oc.voltage_v), abs(mp.power_w), bc is BoundaryCondition.QT)
            out_points.append(point)

    return TwinSystem(
        status="ok",
        note=""
        if not profile.materials.temperature_dependent
        else "ANSYS used α(T), ρ(T), κ(T) tables; the twin uses the constant values of the profile",
        calibrated=calibrated,
        a_hot_mm=design.p_leg.a_hot_mm,
        a_cold_mm=design.p_leg.a_cold_mm,
        metrics=metrics,
        points=out_points,
    )
