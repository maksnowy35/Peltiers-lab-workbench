"""Planning the physical build and test of P-N pairs (lab experiment setup).

Given a design (ideally calibrated against ANSYS) and the test conditions, the
planner computes what has to be prepared: thermoelectric material, electrodes,
ceramics, Ni barrier, solder paste, heater and cooling capacity, electrical
instrumentation, a current-sweep plan concentrated around the expected
optimum, an uncertainty budget and a printable run sheet with a CSV template
whose columns import straight back into the analysis page.

Property values of solders/ceramics are typical datasheet numbers; check the
datasheet of the product you actually buy.
"""

from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel, Field

from .design import PairDesign
from .materials import MaterialLibrary, default_library
from .solver import build_model, max_power, solve

__all__ = ["SOLDERS", "CERAMICS", "LabPlanRequest", "plan_experiment"]

SIGMA_SB = 5.670374419e-8

SOLDERS: dict[str, dict] = {
    s["id"]: s
    for s in [
        {
            "id": "snpb60",
            "name": "Sn60Pb40",
            "solidus_c": 183,
            "liquidus_c": 190,
            "density": 8.5,
            "k": 49,
            "rho": 1.5e-7,
            "pb_free": False,
            "soak_c": (100, 150),
            "peak_c": (210, 225),
            "note": "Classic lab solder (thesis ref. [1]); contains lead.",
        },
        {
            "id": "sn63pb37",
            "name": "Sn63Pb37 (eutectic)",
            "solidus_c": 183,
            "liquidus_c": 183,
            "density": 8.4,
            "k": 50,
            "rho": 1.45e-7,
            "pb_free": False,
            "soak_c": (100, 150),
            "peak_c": (205, 220),
            "note": "Eutectic, sharp melting point.",
        },
        {
            "id": "sac305",
            "name": "SAC305 (Sn96.5Ag3Cu0.5)",
            "solidus_c": 217,
            "liquidus_c": 220,
            "density": 7.4,
            "k": 58,
            "rho": 1.3e-7,
            "pb_free": True,
            "soak_c": (150, 200),
            "peak_c": (235, 250),
            "note": "Standard lead-free alloy.",
        },
        {
            "id": "snsb5",
            "name": "Sn95Sb5",
            "solidus_c": 235,
            "liquidus_c": 240,
            "density": 7.25,
            "k": 28,
            "rho": 1.45e-7,
            "pb_free": True,
            "soak_c": (170, 210),
            "peak_c": (255, 270),
            "note": "Better creep resistance, higher melting point.",
        },
        {
            "id": "bisn58",
            "name": "Bi58Sn42",
            "solidus_c": 138,
            "liquidus_c": 138,
            "density": 8.56,
            "k": 19,
            "rho": 3.8e-7,
            "pb_free": True,
            "soak_c": (90, 120),
            "peak_c": (160, 180),
            "note": "Low-temperature solder (cold side / low-ΔT tests only).",
        },
        {
            "id": "in",
            "name": "Indium",
            "solidus_c": 157,
            "liquidus_c": 157,
            "density": 7.31,
            "k": 82,
            "rho": 8.4e-8,
            "pb_free": True,
            "soak_c": (100, 130),
            "peak_c": (175, 190),
            "note": "Soft, compliant joints; low melting point.",
        },
        {
            "id": "pb92sn5ag",
            "name": "Pb92.5Sn5Ag2.5 (high-lead)",
            "solidus_c": 287,
            "liquidus_c": 296,
            "density": 11.0,
            "k": 26,
            "rho": 2.0e-7,
            "pb_free": False,
            "soak_c": (200, 250),
            "peak_c": (320, 340),
            "note": "High-temperature soft solder.",
        },
        {
            "id": "ausn80",
            "name": "Au80Sn20",
            "solidus_c": 280,
            "liquidus_c": 280,
            "density": 14.5,
            "k": 57,
            "rho": 1.6e-7,
            "pb_free": True,
            "soak_c": (200, 250),
            "peak_c": (300, 320),
            "note": "Hard, fluxless, expensive; high reliability.",
        },
        {
            "id": "agcu72",
            "name": "Ag72Cu28 braze",
            "solidus_c": 780,
            "liquidus_c": 780,
            "density": 10.0,
            "k": 370,
            "rho": 2.2e-8,
            "pb_free": True,
            "soak_c": (600, 700),
            "peak_c": (800, 830),
            "note": "Brazing for skutterudite / half-Heusler / SiGe legs (vacuum furnace).",
        },
    ]
}

CERAMICS: dict[str, dict] = {
    "al2o3_96": {"name": "Al2O3 96%", "k": 24.0, "density": 3.75},
    "al2o3_99": {"name": "Al2O3 99.6%", "k": 30.0, "density": 3.9},
    "aln": {"name": "AlN", "k": 170.0, "density": 3.26},
    "thesis": {"name": "Ceramic (thesis, k = 35)", "k": 35.0, "density": 3.8},
}

CU_DENSITY = 8.96
NI_DENSITY = 8.9


class LabPlanRequest(BaseModel):
    design: PairDesign
    n_pairs: int = Field(1, ge=1, le=500)
    spare_fraction: float = Field(0.3, ge=0, le=5)
    kerf_mm: float = Field(0.2, ge=0, description="saw kerf lost per cut when dicing legs")
    solder_id: str = "sac305"
    joint_thickness_um: float = Field(50.0, gt=0)
    fillet_factor: float = Field(1.3, ge=1.0)
    paste_metal_mass_fraction: float = Field(0.885, gt=0.5, lt=1.0)
    ni_thickness_um: float = Field(2.0, ge=0)
    ceramic_id: str = "al2o3_96"
    cu_on_ceramic: Literal["dbc", "soldered"] = "dbc"
    test_mode: Literal["TT", "QT"] = "TT"
    t_hot_test_c: float = 100.0
    t_cold_test_c: float = 20.0
    q_hot_test_w: float = Field(0.3152, gt=0, description="per pair, for QT tests")
    t_ambient_c: float = 22.0
    supply_voltage_v: float = Field(24.0, gt=0)
    heater_safety_factor: float = Field(2.0, ge=1.0)
    heater_block_area_cm2: float = Field(6.0, ge=0)
    emissivity: float = Field(0.3, ge=0, le=1)
    h_conv: float = Field(6.0, ge=0)
    insulation_loss_fraction: float = Field(0.2, ge=0, le=5)
    coolant: Literal["water", "air"] = "water"
    coolant_dt_k: float = Field(1.0, gt=0)
    joint_margin_k: float = Field(40.0, ge=0)
    clamp_pressure_mpa: float = Field(0.5, ge=0)
    sigma_t_k: float = Field(0.5, ge=0)
    sigma_u_rel: float = Field(5e-4, ge=0)
    sigma_u_abs_v: float = Field(2e-6, ge=0)
    sigma_i_rel: float = Field(2e-3, ge=0)
    sigma_q_rel: float = Field(0.05, ge=0)


def _r(x: float, n: int = 4) -> float:
    return float(f"{x:.{n}g}")


def plan_experiment(req: LabPlanRequest, lib: MaterialLibrary | None = None) -> dict:
    lib = lib or default_library()
    d = req.design.model_copy(deep=True)
    solder = SOLDERS.get(req.solder_id)
    if solder is None:
        raise ValueError(f"unknown solder '{req.solder_id}'")
    ceramic = CERAMICS.get(req.ceramic_id, CERAMICS["al2o3_96"])
    d.passive.ceramic_k = ceramic["k"]
    b = d.boundary
    b.cold_mode = "T"
    b.t_cold_c = req.t_cold_test_c
    if req.test_mode == "TT":
        b.hot_mode, b.t_hot_c = "T", req.t_hot_test_c
    else:
        b.hot_mode, b.q_hot_w = "Q", req.q_hot_test_w
    model = build_model(d, lib)
    oc = solve(model, 0.0)
    mp = max_power(model, oc)
    n = req.n_pairs

    # -------------------------------------------------------------- build / BOM
    legs = []
    te_mass_total = 0.0
    for lg, spec in zip(model.legs, (d.p_leg, d.n_leg), strict=True):
        count = n
        spares = math.ceil(n * req.spare_fraction)
        v_leg = lg.volume * 1e9  # mm3
        a_max = max(lg.a_hot, lg.a_cold) * 1e3
        # raw block per leg incl. kerf on two sides of the footprint and on the height
        v_raw = (a_max + req.kerf_mm) ** 2 * (lg.height * 1e3 + req.kerf_mm)
        dens = sum(lib.get(s.material).density_g_cm3 * s.fraction for s in spec.segments)
        mass = (count + spares) * v_raw * dens * 1e-3  # g
        te_mass_total += mass
        legs.append(
            {
                "type": lg.name,
                "materials": [
                    lib.get(s.material).name + (f" ({s.fraction * 100:.0f}%)" if len(spec.segments) > 1 else "")
                    for s in spec.segments
                ],
                "count": count,
                "spares": spares,
                "a_hot_mm": _r(lg.a_hot * 1e3),
                "a_cold_mm": _r(lg.a_cold * 1e3),
                "height_mm": _r(lg.height * 1e3),
                "theta_deg": _r(lg.theta_deg, 3),
                "volume_mm3": _r(v_leg),
                "raw_volume_per_leg_mm3": _r(v_raw),
                "material_mass_g": _r(mass),
                "fragile": min(lg.a_hot, lg.a_cold) * 1e3 < 0.7 or abs(lg.theta_deg) > 10,
            }
        )
    a_ref = max(max(lg.a_hot, lg.a_cold) for lg in model.legs) * 1e3
    pitch = a_ref + d.passive.gap_mm
    t_cu = d.passive.cu_thickness_mm
    n_electrodes = 2 * n + 1  # n inner bridges + (n-1) outer bridges + 2 terminals
    electrode_len = 2 * a_ref + d.passive.gap_mm
    cu_mass = n_electrodes * electrode_len * a_ref * t_cu * CU_DENSITY * 1e-3
    cols = math.ceil(math.sqrt(n))
    rows = math.ceil(n / cols)
    plate_w = cols * 2 * pitch + 1.0
    plate_l = rows * pitch + 1.0
    t_cer = d.passive.ceramic_thickness_mm
    ceramic_mass = 2 * plate_w * plate_l * t_cer * ceramic["density"] * 1e-3
    joints_te = 4 * n
    area_joints = sum(n * (lg.a_hot**2 + lg.a_cold**2) for lg in model.legs) * 1e6  # mm2
    joints_cu = n_electrodes if req.cu_on_ceramic == "soldered" else 0
    area_cu = n_electrodes * electrode_len * a_ref if joints_cu else 0.0
    t_joint = req.joint_thickness_um * 1e-3
    v_solder = (area_joints + area_cu) * t_joint * req.fillet_factor  # mm3
    m_solder = v_solder * solder["density"] * 1e-3  # g metal
    m_paste = m_solder / req.paste_metal_mass_fraction
    vol_frac_metal = (req.paste_metal_mass_fraction / solder["density"]) / (
        req.paste_metal_mass_fraction / solder["density"] + (1 - req.paste_metal_mass_fraction) / 1.0
    )
    stencil_um = req.joint_thickness_um * req.fillet_factor / vol_frac_metal
    ni_area = area_joints
    ni_mass = ni_area * req.ni_thickness_um * 1e-3 * NI_DENSITY * 1e-3

    # -------------------------------------------------------------- thermal budget
    t_joint_max = max(oc.t_hot_plate_c, mp.t_hot_plate_c)
    t_joint_legs = max(oc.t_leg_hot_c + mp.t_leg_hot_c)
    margin = solder["solidus_c"] - t_joint_max
    homologous = (t_joint_max + 273.15) / (solder["solidus_c"] + 273.15)
    solder_checks = []
    for s in SOLDERS.values():
        m_ = s["solidus_c"] - t_joint_max
        solder_checks.append(
            {
                "id": s["id"],
                "name": s["name"],
                "solidus_c": s["solidus_c"],
                "margin_k": _r(m_, 3),
                "homologous": _r((t_joint_max + 273.15) / (s["solidus_c"] + 273.15), 3),
                "ok": m_ >= req.joint_margin_k,
                "pb_free": s["pb_free"],
            }
        )
    te_limits = [
        {"material": lib.get(sg.material).name, "limit_c": lib.get(sg.material).t_limit_k - 273.15}
        for spec in (d.p_leg, d.n_leg)
        for sg in spec.segments
    ]
    reflow_peak = solder["peak_c"][1]
    reflow_warnings = [
        f"reflow peak {reflow_peak} °C exceeds the {x['material']} limit {x['limit_c']:.0f} °C – keep time above "
        f"liquidus short and use an inert atmosphere"
        for x in te_limits
        if reflow_peak > x["limit_c"]
    ]

    # heater (per module)
    q_pairs = n * max(oc.q_hot_w, mp.q_hot_w)
    t_heater = max(oc.t_hot_ext_c, mp.t_hot_ext_c) + 5.0
    a_block = req.heater_block_area_cm2 * 1e-4
    q_rad = req.emissivity * SIGMA_SB * a_block * ((t_heater + 273.15) ** 4 - (req.t_ambient_c + 273.15) ** 4)
    q_conv = req.h_conv * a_block * (t_heater - req.t_ambient_c)
    q_ins = req.insulation_loss_fraction * q_pairs
    q_heater = q_pairs + q_rad + q_conv + q_ins
    p_rated = req.heater_safety_factor * q_heater
    r_heater = req.supply_voltage_v**2 / p_rated
    i_heater = p_rated / req.supply_voltage_v
    # cooling
    q_cold = n * max(oc.q_cold_w, mp.q_cold_w)
    cooling: dict = {"q_reject_w": _r(q_cold)}
    if req.coolant == "water":
        mdot = q_cold / (4186.0 * req.coolant_dt_k)
        cooling.update(
            {
                "medium": "water",
                "flow_ml_min": _r(mdot / 1000 * 1e6 * 60, 3),
                "coolant_dt_k": req.coolant_dt_k,
                "note": "Chiller/thermostat bath set so that the cold plate reads the target temperature.",
            }
        )
    else:
        headroom = req.t_cold_test_c - req.t_ambient_c
        cooling.update(
            {
                "medium": "air",
                "max_heatsink_r_k_w": _r(headroom / q_cold, 3) if headroom > 0 else None,
                "note": "Air cooling cannot hold the cold plate at or below ambient; use water or a Peltier chiller."
                if headroom <= 0
                else "Heat sink + fan with thermal resistance below the value above.",
            }
        )

    # -------------------------------------------------------------- electrical plan
    u_oc = n * oc.voltage_v
    r_el = n * mp.r_int_ohm
    i_opt = mp.current_a
    i_sc_est = oc.voltage_v / oc.r_int_ohm
    factors = [0.0, 0.25, 0.5, 0.7, 0.85, 0.95, 1.0, 1.05, 1.15, 1.3, 1.5, 1.8]
    setpoints = sorted({_r(f * i_opt, 3) for f in factors if f * i_opt < 0.98 * i_sc_est})
    sweep = []
    t_prev = oc.temps
    for i in setpoints:
        s = solve(model, i, t_prev)
        t_prev = s.temps
        sweep.append(
            {
                "current_a": i,
                "voltage_v": n * s.voltage_v,
                "power_w": n * s.power_w,
                "load_ohm": None if i == 0 else n * s.voltage_v / i,
                "t_hot_ext_c": s.t_hot_ext_c,
                "q_hot_w": n * s.q_hot_w,
            }
        )
    i_max = max(setpoints) if setpoints else i_opt
    r_shunt = 0.05 / max(i_max, 1e-6)
    shunt_std = min(
        [0.001, 0.002, 0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1.0], key=lambda r: abs(math.log(r / r_shunt))
    )
    u_min_meas = min((abs(s["voltage_v"]) for s in sweep if abs(s["voltage_v"]) > 0), default=u_oc)
    resolution = max(u_min_meas * 1e-3, 1e-7)
    load_points = [_r(f * r_el, 3) for f in (0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0, 5.0)]

    # -------------------------------------------------------------- uncertainty
    u_mp = n * mp.voltage_v
    s_u = req.sigma_u_rel * abs(u_mp) + req.sigma_u_abs_v
    rel_p = math.sqrt((s_u / abs(u_mp)) ** 2 + req.sigma_i_rel**2) if u_mp else None
    rel_eta = math.sqrt(rel_p**2 + req.sigma_q_rel**2) if rel_p is not None else None
    dt_plates = oc.t_hot_ext_c - oc.t_cold_ext_c
    s_uoc = req.sigma_u_rel * u_oc + req.sigma_u_abs_v
    rel_alpha = (
        math.sqrt((s_uoc / u_oc) ** 2 + (math.sqrt(2) * req.sigma_t_k / dt_plates) ** 2) if dt_plates > 0 else None
    )
    # sensitivity of P_max to the temperature difference (TT: P ~ dT^2)
    rel_p_from_t = 2 * math.sqrt(2) * req.sigma_t_k / dt_plates if (req.test_mode == "TT" and dt_plates > 0) else None

    # clamping
    plate_area_mm2 = plate_w * plate_l
    clamp_force_n = req.clamp_pressure_mpa * plate_area_mm2  # MPa * mm2 = N

    expected = {
        "per_pair": {
            "u_oc_v": oc.voltage_v,
            "r_int_ohm": mp.r_int_ohm,
            "i_sc_estimate_a": i_sc_est,
            "i_opt_a": i_opt,
            "p_max_w": mp.power_w,
            "eta_at_p_max": mp.efficiency,
            "q_hot_w": mp.q_hot_w,
            "t_hot_ext_c": mp.t_hot_ext_c,
            "t_leg_hot_c": mp.t_leg_hot_c[0],
            "t_leg_cold_c": mp.t_leg_cold_c[0],
            "dt_te_k": mp.dt_te_k,
        },
        "module": {"n_pairs": n, "u_oc_v": u_oc, "r_int_ohm": r_el, "p_max_w": n * mp.power_w},
    }
    plan = {
        "design_name": d.name,
        "test": {
            "mode": req.test_mode,
            "t_hot_c": req.t_hot_test_c if req.test_mode == "TT" else None,
            "q_hot_w_per_pair": req.q_hot_test_w if req.test_mode == "QT" else None,
            "t_cold_c": req.t_cold_test_c,
        },
        "expected": expected,
        "build": {
            "legs": legs,
            "te_material_total_g": _r(te_mass_total),
            "electrodes": {
                "count": n_electrodes,
                "length_mm": _r(electrode_len),
                "width_mm": _r(a_ref),
                "thickness_mm": t_cu,
                "cu_mass_g": _r(cu_mass),
            },
            "ceramic": {
                "material": ceramic["name"],
                "plates": 2,
                "width_mm": _r(plate_w),
                "length_mm": _r(plate_l),
                "thickness_mm": t_cer,
                "mass_g": _r(ceramic_mass),
            },
            "nickel": {"thickness_um": req.ni_thickness_um, "area_mm2": _r(ni_area), "mass_mg": _r(ni_mass * 1e3)},
            "layout": {"rows": rows, "cols": cols, "pitch_mm": _r(pitch)},
        },
        "solder": {
            "alloy": solder["name"],
            "joints_te_cu": joints_te,
            "joints_cu_ceramic": joints_cu,
            "joint_area_mm2": _r(area_joints + area_cu),
            "volume_mm3": _r(v_solder),
            "metal_mass_g": _r(m_solder),
            "paste_mass_g": _r(m_paste),
            "paste_with_waste_g": _r(max(m_paste * 5, 1.0)),
            "stencil_thickness_um": _r(stencil_um, 3),
            "reflow": {
                "soak_c": solder["soak_c"],
                "peak_c": solder["peak_c"],
                "time_above_liquidus_s": [45, 90],
                "atmosphere": "N₂ or forming gas recommended (Te/Sb oxidation)",
            },
            "t_joint_max_c": _r(t_joint_max, 4),
            "t_leg_hot_end_max_c": _r(t_joint_legs, 4),
            "margin_to_solidus_k": _r(margin, 3),
            "homologous_temperature": _r(homologous, 3),
            "ok": margin >= req.joint_margin_k,
            "alternatives": solder_checks,
            "warnings": reflow_warnings
            + (
                ["homologous temperature > 0.8: expect creep/drift of joint resistance during long tests"]
                if homologous > 0.8
                else []
            ),
        },
        "heater": {
            "q_pairs_w": _r(q_pairs),
            "q_radiation_w": _r(q_rad),
            "q_convection_w": _r(q_conv),
            "q_insulation_w": _r(q_ins),
            "q_required_w": _r(q_heater),
            "rated_power_w": _r(p_rated, 3),
            "supply_v": req.supply_voltage_v,
            "resistance_ohm": _r(r_heater, 3),
            "current_a": _r(i_heater, 3),
            "block_temperature_c": _r(t_heater, 4),
            "control": "PID controller + SSR/MOSFET, control thermocouple in the heater block",
        },
        "cooling": cooling,
        "clamping": {
            "pressure_mpa": req.clamp_pressure_mpa,
            "plate_area_mm2": _r(plate_area_mm2),
            "force_n": _r(clamp_force_n, 3),
        },
        "electrical": {
            "method": "4-wire (Kelvin) voltage sensing directly on the terminal electrodes",
            "current_setpoints_a": setpoints,
            "load_resistances_ohm": load_points,
            "expected_sweep": sweep,
            "shunt_ohm": shunt_std,
            "shunt_power_w": _r(shunt_std * i_max**2, 3),
            "voltmeter_resolution_v": _r(resolution, 2),
            "notes": [
                "Lead and probe resistance is of the order of the pair resistance – "
                "never measure U through the current leads.",
                "Record U_oc at several ΔT (e.g. 20/40/60/80 K) before the load sweep to get α_eff.",
                "Reverse the current polarity once to check thermal EMF offsets of the wiring.",
            ],
        },
        "thermocouples": [
            {"position": "heater block (control)", "type": "K", "count": 1},
            {
                "position": "hot plate surface",
                "type": "T" if t_heater < 350 else "K",
                "count": 2,
                "expected_c": _r(mp.t_hot_ext_c, 4),
            },
            {"position": "cold plate surface", "type": "T", "count": 2, "expected_c": _r(mp.t_cold_ext_c, 4)},
            {"position": "cooling water in/out", "type": "T", "count": 2},
            {"position": "ambient", "type": "T", "count": 1, "expected_c": req.t_ambient_c},
        ],
        "uncertainty": {
            "rel_power": rel_p,
            "rel_efficiency": rel_eta,
            "rel_alpha_eff": rel_alpha,
            "rel_power_from_temperature": rel_p_from_t,
            "inputs": {
                "sigma_t_k": req.sigma_t_k,
                "sigma_u_rel": req.sigma_u_rel,
                "sigma_u_abs_v": req.sigma_u_abs_v,
                "sigma_i_rel": req.sigma_i_rel,
                "sigma_q_rel": req.sigma_q_rel,
            },
        },
    }
    plan["run_sheet_md"] = run_sheet_markdown(plan)
    plan["measurement_template_csv"] = measurement_template(plan)
    plan["bom_csv"] = bom_csv(plan)
    return plan


def measurement_template(plan: dict) -> str:
    lines = [
        "# teglab lab measurement template - fill the empty columns and import on the Data import page",
        f"# design: {plan['design_name']}; mode {plan['test']['mode']}; heat flow per module",
        "current [A],voltage [V],t_hot_ext [°C],t_cold_ext [°C],q_hot (heater) [W],notes",
    ]
    for s in plan["electrical"]["expected_sweep"]:
        lines.append(f"{s['current_a']:g},,,,,expected U = {s['voltage_v'] * 1e3:.4g} mV")
    return "\n".join(lines) + "\n"


def bom_csv(plan: dict) -> str:
    b = plan["build"]
    s = plan["solder"]
    rows = ["item,specification,quantity,unit"]
    for leg in b["legs"]:
        spec = f"{'; '.join(leg['materials'])}, {leg['a_hot_mm']}->{leg['a_cold_mm']} x {leg['height_mm']} mm"
        rows.append(f'{leg["type"]} legs,"{spec}",{leg["count"] + leg["spares"]},pcs')
        rows.append(f"{leg['type']} thermoelectric material (incl. kerf and spares),,{leg['material_mass_g']},g")
    e = b["electrodes"]
    rows.append(f'Cu electrodes,"{e["length_mm"]} x {e["width_mm"]} x {e["thickness_mm"]} mm",{e["count"]},pcs')
    c = b["ceramic"]
    dims = f"{c['width_mm']} x {c['length_mm']} x {c['thickness_mm']} mm"
    rows.append(f'Ceramic plates,"{c["material"]} {dims}",{c["plates"]},pcs')
    rows.append(f"Ni diffusion barrier,{b['nickel']['thickness_um']} um on leg ends,{b['nickel']['area_mm2']},mm2")
    rows.append(f"Solder paste,{s['alloy']},{s['paste_with_waste_g']},g")
    h = plan["heater"]
    rows.append(f'Heater,"{h["rated_power_w"]} W @ {h["supply_v"]} V ({h["resistance_ohm"]} Ohm)",1,pcs')
    rows.append(f"Current shunt,{plan['electrical']['shunt_ohm']} Ohm 4-terminal,1,pcs")
    n_tc = sum(t["count"] for t in plan["thermocouples"])
    rows.append(f"Thermocouples,type T/K 0.1-0.25 mm,{n_tc},pcs")
    return "\n".join(rows) + "\n"


def run_sheet_markdown(plan: dict) -> str:
    e = plan["expected"]["per_pair"]
    m = plan["expected"]["module"]
    s = plan["solder"]
    h = plan["heater"]
    el = plan["electrical"]
    t = plan["test"]
    cond = (
        f"T_hot = {t['t_hot_c']} °C, T_cold = {t['t_cold_c']} °C"
        if t["mode"] == "TT"
        else (f"Q_hot = {t['q_hot_w_per_pair']} W per pair, T_cold = {t['t_cold_c']} °C")
    )
    lines = [
        f"# Run sheet – {plan['design_name']}",
        "",
        f"Test mode **{t['mode']}** ({cond}); {m['n_pairs']} pair(s) in series.",
        "",
        "## Expected values (reduced-order model)",
        "",
        "| quantity | per pair | module |",
        "|---|---|---|",
        f"| U_oc | {e['u_oc_v'] * 1e3:.3f} mV | {m['u_oc_v'] * 1e3:.3f} mV |",
        f"| R_int (electrical) | {e['r_int_ohm'] * 1e3:.3f} mΩ | {m['r_int_ohm'] * 1e3:.3f} mΩ |",
        f"| I_opt | {e['i_opt_a']:.3f} A | {e['i_opt_a']:.3f} A |",
        f"| P_max | {e['p_max_w'] * 1e3:.3f} mW | {m['p_max_w'] * 1e3:.3f} mW |",
        f"| efficiency at P_max | {e['eta_at_p_max'] * 100:.2f} % | |",
        f"| T hot plate at P_max | {e['t_hot_ext_c']:.1f} °C | |",
        "",
        "## Materials and parts",
        "",
    ]
    for leg in plan["build"]["legs"]:
        size = f"{leg['a_hot_mm']} → {leg['a_cold_mm']} mm × {leg['height_mm']} mm (θ = {leg['theta_deg']}°)"
        lines.append(
            f"- {leg['type']} legs: {leg['count']} + {leg['spares']} spare, {size}, material {leg['material_mass_g']} g"
            + (" – **fragile: handle the narrow end with care**" if leg["fragile"] else "")
        )
    b = plan["build"]
    ele, cer = b["electrodes"], b["ceramic"]
    lines += [
        f"- Cu electrodes: {ele['count']} pcs {ele['length_mm']} × {ele['width_mm']} × {ele['thickness_mm']} mm",
        f"- Ceramic plates: 2 pcs {cer['material']} {cer['width_mm']} × {cer['length_mm']} × {cer['thickness_mm']} mm",
        f"- Ni barrier {b['nickel']['thickness_um']} µm on {b['nickel']['area_mm2']} mm² of leg ends",
        f"- Solder: {s['alloy']}, {s['metal_mass_g']} g metal ({s['paste_mass_g']} g paste, "
        f"order ≥ {s['paste_with_waste_g']} g), stencil ~{s['stencil_thickness_um']} µm",
        "",
        "## Assembly",
        "",
        "1. Clean legs (IPA), electroplate/sputter Ni on both end faces, protect side walls.",
        "2. Print paste through the stencil on the electrodes; place legs with a jig (P/N alternation!).",
        f"3. Reflow: soak {s['reflow']['soak_c'][0]}–{s['reflow']['soak_c'][1]} °C, peak {s['reflow']['peak_c'][0]}–"
        f"{s['reflow']['peak_c'][1]} °C, 45–90 s above liquidus, {s['reflow']['atmosphere']}.",
        f"4. Inspect joints; measure 4-wire R at room temperature (expect ~{e['r_int_ohm'] * 1e3:.2f} mΩ per pair "
        "at the test temperature, slightly different at RT for T-dependent materials).",
        f"5. Mount between heater and cold plate, thermal paste on both faces, clamp {plan['clamping']['force_n']} N "
        f"({plan['clamping']['pressure_mpa']} MPa).",
        "",
        f"Joint temperature in test: {s['t_joint_max_c']} °C, margin to solidus {s['margin_to_solidus_k']} K "
        + ("(OK)" if s["ok"] else "(**too small – choose another alloy**)"),
    ]
    for w in s["warnings"]:
        lines.append(f"- warning: {w}")
    lines += [
        "",
        "## Rig",
        "",
        f"- Heater: ≥ {h['rated_power_w']} W at {h['supply_v']} V (~{h['resistance_ohm']} Ω, {h['current_a']} A), "
        f"block at ~{h['block_temperature_c']} °C. {h['control']}.",
        f"- Cooling: {plan['cooling'].get('medium')} – reject {plan['cooling']['q_reject_w']} W"
        + (
            f", {plan['cooling']['flow_ml_min']} mL/min for ΔT = {plan['cooling']['coolant_dt_k']} K"
            if plan["cooling"].get("flow_ml_min")
            else ""
        ),
        f"- Current shunt {el['shunt_ohm']} Ω (4-terminal), DMM resolution ≤ {el['voltmeter_resolution_v']:.1e} V.",
        "",
        "## Measurement procedure",
        "",
        "1. Heat up at ≤ 5 K/min; steady state when all temperatures drift < 0.1 K/min for 5 min.",
        "2. Open circuit: record U_oc at ΔT steps (20/40/60/80 K).",
        f"3. At the target condition sweep the current: {', '.join(f'{x:g}' for x in el['current_setpoints_a'])} A "
        "(dense around I_opt).",
        "4. Wait for steady state at every point (heat flow changes with current), "
        "record U, I, T_hot, T_cold, heater power.",
        "5. Repeat the sweep 3 times (up and down) to check hysteresis and drift.",
        "6. Cool down, repeat the room-temperature 4-wire R check (joint degradation).",
        "",
        "## Uncertainty (1σ)",
        "",
    ]
    u = plan["uncertainty"]
    for key, label in (
        ("rel_power", "P"),
        ("rel_efficiency", "efficiency"),
        ("rel_alpha_eff", "α_eff"),
        ("rel_power_from_temperature", "P_max from ΔT error"),
    ):
        if u.get(key) is not None:
            lines.append(f"- {label}: {u[key] * 100:.2f} %")
    lines += [
        "",
        "Fill `measurement_template.csv` and import it on the Data import page to compare with ANSYS/model.",
        "",
    ]
    return "\n".join(lines)
