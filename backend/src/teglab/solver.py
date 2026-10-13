"""Reduced-order (1D) thermoelectric model of one P-N pair.

Numerical scheme
----------------
Each leg is split into elements with uniform properties evaluated at the
element mean temperature (vertex-centered finite volumes).  Thermal
conductance and electrical resistance of an element use the exact integral of
dx/A(x) for the linearly tapered square section, multiplied by the frustum 3D
correction (1 + c3d*tan^2(theta)).

Heat sources on the nodes:
* Joule heat I^2*R of every element, split half/half to its end nodes;
* Peltier heat (alpha_left - alpha_right)*T*I_x at every node, where alpha is
  the element Seebeck coefficient on each side (copper: alpha = 0).  At the
  leg ends this is the junction Peltier heat; inside a leg with alpha(T) it
  converges to the Thomson term -tau*I*dT/dx; at a segment interface it is
  the Peltier heat of the material junction.  The formulation is exactly
  energy-conservative: Q_h - Q_c - Q_side = P_el to machine precision.

Passive elements are lumped resistances: ceramic plate (common to both legs),
copper electrode + contact per leg and side, and contact/copper/extra
electrical resistances whose Joule heat is deposited next to them.  Boundary
conditions: hot face at fixed T, fixed heat input Q, or a heat exchanger
(T_source + R_source); cold face at fixed T or heat sink (T_sink + R_sink).
Temperature-dependent properties are handled by Picard iteration.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import brentq, minimize_scalar

from .design import PairDesign
from .materials import Material, MaterialLibrary, default_library

K0 = 273.15
_R_MIN = 1e-4  # K/W, lower bound for lumped resistances (keeps heat-flow read-out accurate)

__all__ = [
    "LegModel",
    "PairModel",
    "PairSolution",
    "build_model",
    "solve",
    "sweep_current",
    "max_power",
    "max_efficiency",
    "solve_for_load",
    "characterize",
    "material_temperatures",
    "short_circuit_current",
]


# --------------------------------------------------------------------------- model
@dataclass
class LegModel:
    name: str
    sign: int  # +1: positive pair current flows hot->cold in this leg (P), -1 for N
    x: np.ndarray  # node positions [m], 0 = hot end
    a: np.ndarray  # square side at nodes [m]
    geo: np.ndarray  # per element: f3d * integral(dx/A) [1/m]
    lat: np.ndarray  # lateral surface area attributed to each node [m2]
    mat_index: np.ndarray  # per element
    materials: list[Material]
    a_hot: float
    a_cold: float
    height: float
    theta_deg: float

    @property
    def n_nodes(self) -> int:
        return self.x.size

    @property
    def volume(self) -> float:
        return self.height / 3.0 * (self.a_hot**2 + self.a_hot * self.a_cold + self.a_cold**2)

    def props(self, t_elem: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        if len(self.materials) == 1:
            return self.materials[0].props(t_elem)
        alpha = np.empty_like(t_elem)
        rho = np.empty_like(t_elem)
        kappa = np.empty_like(t_elem)
        for i, m in enumerate(self.materials):
            sel = self.mat_index == i
            if np.any(sel):
                alpha[sel], rho[sel], kappa[sel] = m.props(t_elem[sel])
        return alpha, rho, kappa

    def out_of_range(self, t_elem: np.ndarray) -> float:
        """Fraction of elements whose temperature lies outside the material table."""
        bad = 0
        for i, m in enumerate(self.materials):
            sel = self.mat_index == i
            if m.is_constant or not np.any(sel):
                continue
            t = t_elem[sel]
            bad += int(np.sum((t < m.t_min - 1e-9) | (t > m.t_max + 1e-9)))
        return bad / max(t_elem.size, 1)


@dataclass
class PairModel:
    design: PairDesign
    legs: tuple[LegModel, LegModel]
    r_cer_hot: float
    r_cer_cold: float
    r_branch_hot: np.ndarray  # Cu + contact, per leg [K/W]
    r_branch_cold: np.ndarray
    r_contact_hot: np.ndarray  # thermal contact part only (reporting)
    r_contact_cold: np.ndarray
    re_ct_hot: np.ndarray  # electrical contact resistance per leg [Ohm]
    re_ct_cold: np.ndarray
    r_cu_el: float
    r_el_extra: float
    alpha_factor: float
    side_h: float
    t_amb: float
    constant: bool
    # node bookkeeping
    i_hext: int = 0
    i_hpl: int = 1
    leg_offsets: tuple[int, int] = (2, 0)
    i_cpl: int = 0
    i_cext: int = 0
    n: int = 0
    edges_i: np.ndarray = field(default_factory=lambda: np.zeros(0, int))
    edges_j: np.ndarray = field(default_factory=lambda: np.zeros(0, int))

    def leg_nodes(self, k: int) -> np.ndarray:
        off = self.leg_offsets[k]
        return np.arange(off, off + self.legs[k].n_nodes)

    @property
    def te_volume(self) -> float:
        return self.legs[0].volume + self.legs[1].volume


def _discretize_leg(
    name: str,
    sign: int,
    a_hot: float,
    a_cold: float,
    height: float,
    segments: list[tuple[Material, float]],
    n_elements: int,
    c3d: float,
) -> LegModel:
    fr = np.array([f for _, f in segments], dtype=float)
    fr = fr / fr.sum()
    counts = np.maximum(2, np.round(fr * n_elements).astype(int))
    xs = [0.0]
    mat_index = []
    x0 = 0.0
    for k, (cnt, f) in enumerate(zip(counts, fr, strict=True)):
        seg_x = np.linspace(x0, x0 + f * height, cnt + 1)[1:]
        xs.extend(seg_x.tolist())
        mat_index.extend([k] * cnt)
        x0 += f * height
    x = np.array(xs)
    x[-1] = height
    a = a_hot + (a_cold - a_hot) * x / height
    tan_t = (a_cold - a_hot) / (2.0 * height)
    f3d = 1.0 + c3d * tan_t * tan_t
    dx = np.diff(x)
    geo = f3d * dx / (a[:-1] * a[1:])
    slant = dx * math.sqrt(1.0 + tan_t * tan_t)
    lat_elem = 4.0 * 0.5 * (a[:-1] + a[1:]) * slant
    lat = np.zeros_like(x)
    lat[:-1] += 0.5 * lat_elem
    lat[1:] += 0.5 * lat_elem
    return LegModel(
        name=name,
        sign=sign,
        x=x,
        a=a,
        geo=geo,
        lat=lat,
        mat_index=np.array(mat_index, dtype=int),
        materials=[m for m, _ in segments],
        a_hot=a_hot,
        a_cold=a_cold,
        height=height,
        theta_deg=math.degrees(math.atan(tan_t)),
    )


def build_model(design: PairDesign, library: MaterialLibrary | None = None) -> PairModel:
    lib = library or default_library()
    h = design.height_mm * 1e-3
    legs = []
    for name, sign, spec in (("P", +1, design.p_leg), ("N", -1, design.n_leg)):
        segs = [(lib.get(s.material), s.fraction) for s in spec.segments]
        legs.append(
            _discretize_leg(
                name, sign, spec.a_hot_mm * 1e-3, spec.a_cold_mm * 1e-3, h, segs, design.n_elements, design.c3d
            )
        )
    p = design.passive
    a_max = [max(lg.a_hot, lg.a_cold) for lg in legs]
    a_ref = max(a_max)
    gap = p.gap_mm * 1e-3
    a_cer = p.ceramic_area_mm2 * 1e-6 if p.ceramic_area_mm2 else (a_max[0] + a_max[1] + gap) * a_ref
    t_cer, t_cu = p.ceramic_thickness_mm * 1e-3, p.cu_thickness_mm * 1e-3
    r_cer = t_cer / (p.ceramic_k * a_cer)
    r_cu = np.array([t_cu / (p.cu_k * (am + 0.5 * gap) * am) for am in a_max])
    c = design.contacts
    area_hot = np.array([lg.a_hot**2 for lg in legs])
    area_cold = np.array([lg.a_cold**2 for lg in legs])
    if c.active:
        rct_h, rct_c = 1.0 / (c.tcc * area_hot), 1.0 / (c.tcc * area_cold)
        re_h, re_c = 1.0 / (c.ecc * area_hot), 1.0 / (c.ecc * area_cold)
    else:
        rct_h = rct_c = re_h = re_c = np.zeros(2)
    if t_cu > 0:
        # bridge: center-to-center path between the legs; terminals: optional lead distance
        l_path = a_ref + gap + 2.0 * p.terminal_length_mm * 1e-3
        r_cu_el = p.cu_rho / (t_cu * a_ref) * l_path
    else:
        r_cu_el = 0.0
    model = PairModel(
        design=design,
        legs=(legs[0], legs[1]),
        r_cer_hot=max(r_cer * p.hot_side_factor, _R_MIN),
        r_cer_cold=max(r_cer * p.cold_side_factor, _R_MIN),
        r_branch_hot=np.maximum(r_cu * p.hot_side_factor + rct_h, _R_MIN),
        r_branch_cold=np.maximum(r_cu * p.cold_side_factor + rct_c, _R_MIN),
        r_contact_hot=np.asarray(rct_h, float),
        r_contact_cold=np.asarray(rct_c, float),
        re_ct_hot=np.asarray(re_h, float),
        re_ct_cold=np.asarray(re_c, float),
        r_cu_el=r_cu_el,
        r_el_extra=p.r_el_extra_mohm * 1e-3,
        alpha_factor=design.alpha_factor,
        side_h=design.side_loss_h,
        t_amb=design.t_ambient_c + K0,
        constant=all(m.is_constant for lg in legs for m in lg.materials),
    )
    # node numbering: hot ext, hot plate, P leg, N leg, cold plate, cold ext
    off_p = 2
    off_n = off_p + legs[0].n_nodes
    model.leg_offsets = (off_p, off_n)
    model.i_cpl = off_n + legs[1].n_nodes
    model.i_cext = model.i_cpl + 1
    model.n = model.i_cext + 1
    ei, ej = [model.i_hext, model.i_hpl, model.i_hpl], [model.i_hpl, off_p, off_n]
    for k in range(2):
        nodes = model.leg_nodes(k)
        ei.extend(nodes[:-1].tolist())
        ej.extend(nodes[1:].tolist())
    ei.extend([off_n - 1, model.i_cpl - 1, model.i_cpl])
    ej.extend([model.i_cpl, model.i_cpl, model.i_cext])
    model.edges_i = np.array(ei, dtype=int)
    model.edges_j = np.array(ej, dtype=int)
    return model


# ------------------------------------------------------------------------ solution
@dataclass
class PairSolution:
    current_a: float
    voltage_v: float
    power_w: float
    q_hot_w: float
    q_cold_w: float
    q_side_w: float
    efficiency: float
    exergy_efficiency: float
    carnot: float
    emf_v: float
    r_int_ohm: float
    t_hot_ext_c: float
    t_cold_ext_c: float
    t_hot_plate_c: float
    t_cold_plate_c: float
    t_leg_hot_c: tuple[float, float]
    t_leg_cold_c: tuple[float, float]
    dt_te_k: float
    t_max_c: float
    j_max_a_m2: float
    energy_balance_rel: float
    iterations: int
    converged: bool
    warnings: list[str]
    temps: np.ndarray = field(repr=False)
    profiles: dict | None = None

    @property
    def load_resistance_ohm(self) -> float:
        return self.voltage_v / self.current_a if abs(self.current_a) > 1e-15 else math.inf

    @property
    def dt_passive_hot_k(self) -> float:
        return self.t_hot_ext_c - 0.5 * (self.t_leg_hot_c[0] + self.t_leg_hot_c[1])

    @property
    def dt_passive_cold_k(self) -> float:
        return 0.5 * (self.t_leg_cold_c[0] + self.t_leg_cold_c[1]) - self.t_cold_ext_c

    def to_dict(self, profiles: bool = False) -> dict:
        d = {
            "current_a": self.current_a,
            "voltage_v": self.voltage_v,
            "power_w": self.power_w,
            "q_hot_w": self.q_hot_w,
            "q_cold_w": self.q_cold_w,
            "q_side_w": self.q_side_w,
            "efficiency": self.efficiency,
            "exergy_efficiency": self.exergy_efficiency,
            "carnot": self.carnot,
            "emf_v": self.emf_v,
            "r_int_ohm": self.r_int_ohm,
            "load_resistance_ohm": None if math.isinf(self.load_resistance_ohm) else self.load_resistance_ohm,
            "t_hot_ext_c": self.t_hot_ext_c,
            "t_cold_ext_c": self.t_cold_ext_c,
            "t_hot_plate_c": self.t_hot_plate_c,
            "t_cold_plate_c": self.t_cold_plate_c,
            "t_leg_hot_c": list(self.t_leg_hot_c),
            "t_leg_cold_c": list(self.t_leg_cold_c),
            "dt_te_k": self.dt_te_k,
            "dt_passive_hot_k": self.dt_passive_hot_k,
            "dt_passive_cold_k": self.dt_passive_cold_k,
            "t_max_c": self.t_max_c,
            "j_max_a_m2": self.j_max_a_m2,
            "energy_balance_rel": self.energy_balance_rel,
            "iterations": self.iterations,
            "converged": self.converged,
            "warnings": list(self.warnings),
        }
        if profiles and self.profiles is not None:
            d["profiles"] = self.profiles
        return d


def _initial_guess(model: PairModel) -> np.ndarray:
    b = model.design.boundary
    tc = (b.t_cold_c if b.cold_mode == "T" else b.t_sink_c) + K0
    if b.hot_mode == "T":
        th = b.t_hot_c + K0
    elif b.hot_mode == "HX":
        th = b.t_source_c + K0
    else:
        leg_r = []
        for lg in model.legs:
            k_est = 1.5 if lg.materials[0].is_constant else float(np.mean(lg.materials[0].kappa))
            leg_r.append(float(np.sum(lg.geo)) / k_est)
        r_legs = 1.0 / sum(1.0 / r for r in leg_r)
        th = tc + b.q_hot_w * (model.r_cer_hot + model.r_cer_cold + r_legs)
    return np.full(model.n, 0.5 * (th + tc))


def solve(
    model: PairModel,
    current: float,
    t_init: np.ndarray | None = None,
    *,
    tol: float = 1e-11,
    max_iter: int = 300,
    with_profiles: bool = False,
) -> PairSolution:
    """Steady state for a prescribed pair current I [A] (I > 0: generator direction)."""
    b = model.design.boundary
    n = model.n
    legs = model.legs
    I = float(current)
    T = _initial_guess(model) if t_init is None else np.array(t_init, dtype=float)
    ei, ej = model.edges_i, model.edges_j
    omega = 1.0
    converged = False
    it = 0
    prev_step = math.inf
    alphas: list[np.ndarray] = []
    rhos: list[np.ndarray] = []
    diag_idx = np.arange(n)
    nodes_k = [model.leg_nodes(k) for k in range(2)]
    g_head = np.array([1.0 / model.r_cer_hot, 1.0 / model.r_branch_hot[0], 1.0 / model.r_branch_hot[1]])
    g_tail = np.array([1.0 / model.r_branch_cold[0], 1.0 / model.r_branch_cold[1], 1.0 / model.r_cer_cold])
    src0 = np.zeros(n)  # current-dependent but temperature-independent sources
    for k in range(2):
        nodes = nodes_k[k]
        src0[nodes[0]] += 0.5 * I * I * model.re_ct_hot[k]
        src0[model.i_hpl] += 0.5 * I * I * model.re_ct_hot[k]
        src0[nodes[-1]] += 0.5 * I * I * model.re_ct_cold[k]
        src0[model.i_cpl] += 0.5 * I * I * model.re_ct_cold[k]
    r_plates = model.r_cu_el + model.r_el_extra
    src0[model.i_hpl] += 0.5 * I * I * r_plates
    src0[model.i_cpl] += 0.5 * I * I * r_plates
    side = [model.side_h * lg.lat if model.side_h > 0.0 else None for lg in legs]
    for it in range(1, max_iter + 1):
        src = src0.copy()
        diag_extra = np.zeros(n)
        alphas, rhos = [], []
        g_legs = []
        for k, lg in enumerate(legs):
            nodes = nodes_k[k]
            t_el = 0.5 * (T[nodes[:-1]] + T[nodes[1:]])
            alpha, rho, kappa = lg.props(t_el)
            if model.alpha_factor != 1.0:
                alpha = alpha * model.alpha_factor
            alphas.append(alpha)
            rhos.append(rho)
            g_legs.append(kappa / lg.geo)
            q_j = I * I * rho * lg.geo
            src[nodes[:-1]] += 0.5 * q_j
            src[nodes[1:]] += 0.5 * q_j
            ix = lg.sign * I
            if ix != 0.0:
                a_left = np.concatenate(([0.0], alpha))
                a_right = np.concatenate((alpha, [0.0]))
                diag_extra[nodes] -= (a_left - a_right) * ix  # released Peltier heat c*T moved to LHS
            if side[k] is not None:
                diag_extra[nodes] += side[k]
                src[nodes] += side[k] * model.t_amb
        g = np.concatenate((g_head, g_legs[0], g_legs[1], g_tail))
        A = np.zeros((n, n))
        A[ei, ej] = -g
        A[ej, ei] = -g
        A[diag_idx, diag_idx] = np.bincount(ei, g, n) + np.bincount(ej, g, n) + diag_extra
        rhs = src
        # hot boundary
        if b.hot_mode == "T":
            A[model.i_hext, :] = 0.0
            A[model.i_hext, model.i_hext] = 1.0
            rhs[model.i_hext] = b.t_hot_c + K0
        elif b.hot_mode == "Q":
            rhs[model.i_hext] += b.q_hot_w
        else:
            A[model.i_hext, model.i_hext] += 1.0 / b.r_source_k_w
            rhs[model.i_hext] += (b.t_source_c + K0) / b.r_source_k_w
        # cold boundary
        if b.cold_mode == "T":
            A[model.i_cext, :] = 0.0
            A[model.i_cext, model.i_cext] = 1.0
            rhs[model.i_cext] = b.t_cold_c + K0
        else:
            A[model.i_cext, model.i_cext] += 1.0 / b.r_sink_k_w
            rhs[model.i_cext] += (b.t_sink_c + K0) / b.r_sink_k_w
        T_new = np.linalg.solve(A, rhs)
        if model.constant:
            T = T_new
            converged = True
            break
        step = float(np.max(np.abs(T_new - T)))
        if step > prev_step and it > 2:  # oscillation/divergence -> under-relax
            omega = max(0.25, omega * 0.6)
        prev_step = step
        T = T + omega * (T_new - T)
        if step < tol * max(1.0, float(np.max(T))):
            converged = True
            break

    if not model.constant:
        # properties consistent with the final field (for EMF / R reporting)
        alphas, rhos = [], []
        for k, lg in enumerate(legs):
            nodes = model.leg_nodes(k)
            t_el = 0.5 * (T[nodes[:-1]] + T[nodes[1:]])
            alpha, rho, _ = lg.props(t_el)
            alphas.append(alpha * model.alpha_factor)
            rhos.append(rho)

    return _postprocess(model, I, T, alphas, rhos, it, converged, with_profiles)


def _postprocess(
    model: PairModel,
    I: float,
    T: np.ndarray,
    alphas: list[np.ndarray],
    rhos: list[np.ndarray],
    iterations: int,
    converged: bool,
    with_profiles: bool,
) -> PairSolution:
    b = model.design.boundary
    legs = model.legs
    emf = 0.0
    r_legs = 0.0
    t_hot_ends, t_cold_ends = [], []
    j_max = 0.0
    q_side = 0.0
    warnings: list[str] = []
    profiles: dict = {}
    for k, lg in enumerate(legs):
        nodes = model.leg_nodes(k)
        tl = T[nodes]
        emf += lg.sign * float(np.sum(alphas[k] * (tl[:-1] - tl[1:])))
        r_legs += float(np.sum(rhos[k] * lg.geo))
        t_hot_ends.append(float(tl[0] - K0))
        t_cold_ends.append(float(tl[-1] - K0))
        j_max = max(j_max, abs(I) / float(np.min(lg.a)) ** 2)
        if model.side_h > 0:
            q_side += float(np.sum(model.side_h * lg.lat * (tl - model.t_amb)))
        t_el = 0.5 * (tl[:-1] + tl[1:])
        oor = lg.out_of_range(t_el)
        if oor > 0:
            warnings.append(f"{lg.name} leg: {oor * 100:.0f}% of elements outside material data range (held constant)")
        for i_m, m in enumerate(lg.materials):
            sel = lg.mat_index == i_m
            if not np.any(sel):
                continue
            t_seg = max(float(np.max(tl[:-1][sel])), float(np.max(tl[1:][sel])))
            if t_seg > m.t_limit_k:
                warnings.append(
                    f"{lg.name} leg: {m.name} reaches {t_seg - K0:.0f} °C, above its limit {m.t_limit_k - K0:.0f} °C"
                )
        if with_profiles:
            a_el = 0.5 * (lg.a[:-1] + lg.a[1:])
            j_el = I / (a_el * a_el)
            x_el = 0.5 * (lg.x[:-1] + lg.x[1:])
            dadt = np.zeros_like(t_el)
            for i_m, m in enumerate(lg.materials):
                sel = lg.mat_index == i_m
                dadt[sel] = m.dalpha_dt(t_el[sel]) * model.alpha_factor
            profiles[lg.name] = {
                "x_mm": (lg.x * 1e3).tolist(),
                "t_c": (tl - K0).tolist(),
                "a_mm": (lg.a * 1e3).tolist(),
                "x_el_mm": (x_el * 1e3).tolist(),
                "j_a_m2": np.abs(j_el).tolist(),
                "joule_w_m3": (rhos[k] * j_el * j_el).tolist(),
                "alpha_uv_k": (alphas[k] * 1e6).tolist(),
                "tau_uv_k": (t_el * dadt * 1e6).tolist(),
            }
    r_int = r_legs + float(np.sum(model.re_ct_hot) + np.sum(model.re_ct_cold)) + model.r_cu_el + model.r_el_extra
    voltage = emf - I * r_int
    power = voltage * I
    if b.hot_mode == "T":
        q_hot = (T[model.i_hext] - T[model.i_hpl]) / model.r_cer_hot
    elif b.hot_mode == "Q":
        q_hot = b.q_hot_w
    else:
        q_hot = (b.t_source_c + K0 - T[model.i_hext]) / b.r_source_k_w
    q_cold = (T[model.i_cpl] - T[model.i_cext]) / model.r_cer_cold
    balance = q_hot - q_cold - q_side - power
    bal_rel = abs(balance) / max(abs(q_hot), 1e-15)
    t_h_ref = (b.t_source_c + K0) if b.hot_mode == "HX" else T[model.i_hext]
    t_0 = (b.t_sink_c + K0) if b.cold_mode == "HX" else T[model.i_cext]
    carnot = 1.0 - t_0 / t_h_ref if t_h_ref > t_0 else 0.0
    eff = power / q_hot if q_hot > 0 else 0.0
    ex_eff = eff / carnot if carnot > 0 else 0.0
    if not converged:
        warnings.append("Picard iteration did not converge")
    sol = PairSolution(
        current_a=I,
        voltage_v=voltage,
        power_w=power,
        q_hot_w=float(q_hot),
        q_cold_w=float(q_cold),
        q_side_w=q_side,
        efficiency=float(eff),
        exergy_efficiency=float(ex_eff),
        carnot=float(carnot),
        emf_v=emf,
        r_int_ohm=r_int,
        t_hot_ext_c=float(T[model.i_hext] - K0),
        t_cold_ext_c=float(T[model.i_cext] - K0),
        t_hot_plate_c=float(T[model.i_hpl] - K0),
        t_cold_plate_c=float(T[model.i_cpl] - K0),
        t_leg_hot_c=(t_hot_ends[0], t_hot_ends[1]),
        t_leg_cold_c=(t_cold_ends[0], t_cold_ends[1]),
        dt_te_k=0.5 * ((t_hot_ends[0] - t_cold_ends[0]) + (t_hot_ends[1] - t_cold_ends[1])),
        t_max_c=float(np.max(T) - K0),
        j_max_a_m2=j_max,
        energy_balance_rel=float(bal_rel),
        iterations=iterations,
        converged=converged,
        warnings=warnings,
        temps=T,
        profiles=profiles if with_profiles else None,
    )
    return sol


def material_temperatures(model: PairModel, sol: PairSolution) -> list[dict]:
    """Highest temperature reached by every material segment (for limit checks)."""
    out = []
    for k, lg in enumerate(model.legs):
        tl = sol.temps[model.leg_nodes(k)]
        for i_m, m in enumerate(lg.materials):
            sel = lg.mat_index == i_m
            if not np.any(sel):
                continue
            t_seg = max(float(np.max(tl[:-1][sel])), float(np.max(tl[1:][sel])))
            t_min = min(float(np.min(tl[:-1][sel])), float(np.min(tl[1:][sel])))
            out.append(
                {
                    "leg": lg.name,
                    "material": m.id,
                    "name": m.name,
                    "t_max_c": t_seg - K0,
                    "t_min_c": t_min - K0,
                    "t_limit_c": m.t_limit_k - K0,
                    "ok": t_seg <= m.t_limit_k,
                }
            )
    return out


# ------------------------------------------------------------------ operating points
def sweep_current(model: PairModel, currents, with_profiles: bool = False) -> list[PairSolution]:
    out: list[PairSolution] = []
    t_prev = None
    for i in currents:
        s = solve(model, float(i), t_prev, with_profiles=with_profiles)
        t_prev = s.temps
        out.append(s)
    return out


def _short_circuit_estimate(oc: PairSolution) -> float:
    return max(oc.emf_v / oc.r_int_ohm, 1e-9)


def short_circuit_current(model: PairModel, oc: PairSolution | None = None) -> float:
    """Current at which the terminal voltage reaches zero (under fixed heat input this is well
    below EMF/R_int because the temperature difference collapses with current)."""
    oc = oc or solve(model, 0.0)
    guess = _short_circuit_estimate(oc)
    cache = {"t": oc.temps}

    def u(i: float) -> float:
        s = solve(model, i, cache["t"])
        cache["t"] = s.temps
        return s.voltage_v

    hi = 1.05 * guess
    if u(hi) > 0:  # unusual (e.g. strong Thomson effect) - fall back to the estimate
        return hi
    return float(brentq(u, 0.0, hi, xtol=1e-9 * max(guess, 1e-9)))


def max_power(model: PairModel, oc: PairSolution | None = None, rel_tol: float = 2e-5) -> PairSolution:
    """Operating point of maximum electrical power (bounded Brent search on I)."""
    oc = oc or solve(model, 0.0)
    i_hi = 1.02 * _short_circuit_estimate(oc)
    cache = {"t": oc.temps}

    def neg_p(i: float) -> float:
        s = solve(model, i, cache["t"])
        cache["t"] = s.temps
        return -s.power_w

    res = minimize_scalar(neg_p, bounds=(0.0, i_hi), method="bounded", options={"xatol": rel_tol * i_hi})
    return solve(model, float(res.x), cache["t"], with_profiles=True)


def max_efficiency(model: PairModel, oc: PairSolution | None = None, rel_tol: float = 2e-5) -> PairSolution:
    oc = oc or solve(model, 0.0)
    i_hi = 1.02 * _short_circuit_estimate(oc)
    cache = {"t": oc.temps}

    def neg_eta(i: float) -> float:
        s = solve(model, i, cache["t"])
        cache["t"] = s.temps
        return -s.efficiency

    res = minimize_scalar(neg_eta, bounds=(0.0, i_hi), method="bounded", options={"xatol": rel_tol * i_hi})
    return solve(model, float(res.x), cache["t"])


def solve_for_load(model: PairModel, r_load_ohm: float) -> PairSolution:
    """Operating point with an external load resistance R_L."""
    oc = solve(model, 0.0)
    i_hi = 1.5 * _short_circuit_estimate(oc)
    cache = {"t": oc.temps}

    def f(i: float) -> float:
        s = solve(model, i, cache["t"])
        cache["t"] = s.temps
        return s.voltage_v - i * r_load_ohm

    i = brentq(f, 0.0, i_hi, xtol=1e-12)
    return solve(model, i, cache["t"])


def characterize(model: PairModel, n_sweep: int = 25) -> dict:
    """Open circuit, max-power and max-efficiency points plus an I-sweep (JSON-ready)."""
    oc = solve(model, 0.0, with_profiles=True)
    mp = max_power(model, oc)
    me = max_efficiency(model, oc)
    i_sc = short_circuit_current(model, oc)
    currents = np.linspace(0.0, 1.03 * i_sc, n_sweep)
    sweep = sweep_current(model, currents)
    k_pair = oc.q_hot_w / oc.dt_te_k if oc.dt_te_k > 0 else float("nan")
    v_te_mm3 = model.te_volume * 1e9
    return {
        "open_circuit": oc.to_dict(profiles=True),
        "max_power": mp.to_dict(profiles=True),
        "max_efficiency": me.to_dict(),
        "sweep": [s.to_dict() for s in sweep],
        "summary": {
            "u_oc_v": oc.voltage_v,
            "r_int_ohm": mp.r_int_ohm,
            "k_pair_w_k": k_pair,
            "p_max_w": mp.power_w,
            "i_opt_a": mp.current_a,
            "i_sc_a": i_sc,
            "r_load_opt_ohm": mp.load_resistance_ohm,
            "eta_at_p_max": mp.efficiency,
            "eta_max": me.efficiency,
            "i_eta_max_a": me.current_a,
            "exergy_eta_at_p_max": mp.exergy_efficiency,
            "t_hot_ext_oc_c": oc.t_hot_ext_c,
            "t_max_oc_c": oc.t_max_c,
            "dt_te_oc_k": oc.dt_te_k,
            "te_volume_mm3": v_te_mm3,
            "volume_per_power_mm3_mw": v_te_mm3 / (mp.power_w * 1e3) if mp.power_w > 0 else None,
            "power_per_volume_mw_mm3": mp.power_w * 1e3 / v_te_mm3 if v_te_mm3 > 0 else None,
        },
        "material_temperatures": [{**m, "state": "open circuit"} for m in material_temperatures(model, oc)]
        + [{**m, "state": "max power"} for m in material_temperatures(model, mp)],
        "passive": {
            "r_ceramic_hot_k_w": model.r_cer_hot,
            "r_ceramic_cold_k_w": model.r_cer_cold,
            "r_branch_hot_k_w": model.r_branch_hot.tolist(),
            "r_branch_cold_k_w": model.r_branch_cold.tolist(),
            "r_contact_el_ohm": float(np.sum(model.re_ct_hot) + np.sum(model.re_ct_cold)),
            "r_cu_el_ohm": model.r_cu_el,
            "r_el_extra_ohm": model.r_el_extra,
        },
        "geometry": [
            {
                "leg": lg.name,
                "a_hot_mm": lg.a_hot * 1e3,
                "a_cold_mm": lg.a_cold * 1e3,
                "height_mm": lg.height * 1e3,
                "theta_deg": lg.theta_deg,
                "volume_mm3": lg.volume * 1e9,
                "materials": [m.id for m in lg.materials],
            }
            for lg in model.legs
        ],
    }
