"""Automatic QA gates of one ANSYS system: the checks that used to be done by hand before a result
went into a table of the manuscript.

Every gate returns pass / warn / fail / skip with the tested value and the limit, so the verdict can be
reproduced.  ``skip`` means the data needed for the check were not exported.
"""

from __future__ import annotations

import math

import pandas as pd

from .schemas import BoundaryCondition, ProjectProfile, QaGate, QaStatus, TwinSystem
from .twin import verdict

__all__ = ["BALANCE_PASS", "BALANCE_WARN", "qa_gates"]

BALANCE_PASS = 0.005  # |Q_h − Q_c − U·I| / Q_h
BALANCE_WARN = 0.02
BOUND_SLACK = 0.01  # FE P_max may exceed the frozen-ΔT bound only by round-off
SEEBECK_TOL = 0.02


def _num(value: object) -> float | None:
    try:
        v = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _balance(r: pd.Series) -> QaGate:
    b = _num(r.get("balance_rel_max"))
    title = "Energy balance Q_h − Q_c = U·I"
    if b is None:
        return QaGate(code="energy-balance", title=title, status="skip", detail="Q_h and Q_c are not both exported")
    status: QaStatus = "pass" if b <= BALANCE_PASS else "warn" if b <= BALANCE_WARN else "fail"
    return QaGate(
        code="energy-balance",
        title=title,
        status=status,
        value=b,
        limit=BALANCE_PASS,
        detail="worst design point; above 2 % the solution is not converged or a probe is misplaced",
    )


def _open_circuit(r: pd.Series) -> QaGate:
    method = str(r.get("oc_method") or "")
    title = "Open-circuit point (I = 0)"
    if not method or method == "nan":
        return QaGate(code="open-circuit", title=title, status="skip", detail="no open-circuit data")
    if "I = 0" in method:
        return QaGate(code="open-circuit", title=title, status="pass", detail=method)
    return QaGate(
        code="open-circuit",
        title=title,
        status="warn",
        detail=f"{method}; add a design point at 0 A so that U_oc, ΔT_TE and K are measured, not extrapolated",
    )


def _bracketed(r: pd.Series) -> QaGate:
    p = _num(r.get("p_max_w"))
    title = "P_max bracketed by design points"
    if p is None:
        return QaGate(code="p-max-bracketed", title=title, status="skip", detail="fewer than two loaded points")
    if bool(r.get("bracketed", False)):
        return QaGate(code="p-max-bracketed", title=title, status="pass", detail=str(r.get("p_method") or ""))
    return QaGate(
        code="p-max-bracketed",
        title=title,
        status="warn",
        detail="the optimum lies outside the swept currents; the fitted P_max is an extrapolation",
    )


def _bound(r: pd.Series) -> QaGate:
    p = _num(r.get("p_max_w"))
    bc = r.get("bc")
    bound, name = (
        (_num(r.get("p_frozen_w")), "α²Q²/(4K²R)")
        if bc == BoundaryCondition.QT.value
        else (_num(r.get("p_tt_w")), "α²ΔT²/(4R)")
    )
    title = "P_max below the analytic upper bound"
    if p is None or bound is None or bound <= 0:
        return QaGate(code="upper-bound", title=title, status="skip", detail="P_max or the bound is not available")
    ratio = p / bound
    status: QaStatus = "pass" if ratio <= 1.0 + BOUND_SLACK else "fail"
    return QaGate(
        code="upper-bound",
        title=title,
        status=status,
        value=ratio,
        limit=1.0 + BOUND_SLACK,
        detail=f"P_max / {name}; a ratio above 1 means inconsistent inputs (material data, BC or sign convention)",
    )


def _contacts(r: pd.Series) -> QaGate:
    v = str(r.get("contact_verdict") or "unknown")
    status: QaStatus = {"active": "pass", "inactive": "fail", "inconclusive": "warn"}.get(v, "skip")
    detail = {
        "active": "the load points show the expected contact resistance",
        "inactive": "the contact conductances did not take effect: check the contact definitions",
        "inconclusive": "the contact resistance is within the noise of the ohmic estimate",
    }.get(v, "needs loaded points and the 1D leg resistance")
    return QaGate(code="contacts", title="Contact definitions took effect", status=status, detail=detail)


def _seebeck(r: pd.Series, profile: ProjectProfile) -> QaGate:
    title = "Effective Seebeck coefficient matches the material data"
    a = _num(r.get("alpha_eff_v_k"))
    if profile.materials.temperature_dependent:
        return QaGate(code="seebeck", title=title, status="skip", detail="temperature-dependent materials")
    if a is None or "I = 0" not in str(r.get("oc_method") or ""):
        return QaGate(code="seebeck", title=title, status="skip", detail="needs a measured open circuit")
    ref = profile.materials.alpha_pn_v_k
    d = (a - ref) / abs(ref)
    return QaGate(
        code="seebeck",
        title=title,
        status=verdict(d, SEEBECK_TOL),
        value=d,
        limit=SEEBECK_TOL,
        detail="(U_oc/ΔT_TE − α_pn)/α_pn; a deviation means ΔT_TE is read at the wrong faces or α differs",
    )


def _twin(twin: TwinSystem, tol_point: float) -> list[QaGate]:
    if twin.status != "ok":
        return [QaGate(code="twin", title="1D twin", status="skip", detail=twin.note)]
    gates = []
    titles = {"u_oc_v": "1D twin: U_oc", "dt_te_k": "1D twin: ΔT_TE at open circuit", "p_max_w": "1D twin: P_max"}
    for m in twin.metrics:
        if m.key in titles:
            gates.append(
                QaGate(
                    code=f"twin-{m.key}",
                    title=titles[m.key],
                    status=m.status,
                    value=m.deviation,
                    limit=m.tolerance,
                    detail="relative deviation FEM vs 1D" if m.status != "skip" else "not exported",
                )
            )
    worst = [p.worst_deviation for p in twin.points if p.worst_deviation is not None]
    if worst:
        w = max(worst)
        gates.append(
            QaGate(
                code="twin-points",
                title="1D twin: U, P, Q at every design point",
                status=verdict(w, tol_point),
                value=w,
                limit=tol_point,
                detail="largest relative deviation over all points",
            )
        )
    return gates


def qa_gates(r: pd.Series, twin: TwinSystem, profile: ProjectProfile) -> list[QaGate]:
    """All gates of one system, in the order they are worth reading."""
    return [
        _balance(r),
        _open_circuit(r),
        _bracketed(r),
        _bound(r),
        _contacts(r),
        _seebeck(r, profile),
        *_twin(twin, profile.twin.tol_point),
    ]
