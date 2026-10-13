"""Reference numbers from the thesis (M. Kruk, Warsaw University of Technology, 2026).

Used for validation and demos.

Only values printed in the thesis tables/figures are stored here; nothing is
interpolated.  Units: V, A, W, K, degC as noted in the key names.
"""

from __future__ import annotations

from .geometry import a_hot_from_angle

# Calibration of the 1D model against thesis A0-TT (Tables 4.2–4.4); produced by
# ``teglab.calibration.calibrate`` on ``THESIS_A0_TT_POINTS`` (see docs/validation.md).
CALIBRATED_A0: dict[str, float] = {
    "hot_side_factor": 1.479,
    "cold_side_factor": 0.935,
    "r_el_extra_mohm": 0.0,
    "c3d": 0.5,
}

# Table 3.1 / 3.2 -----------------------------------------------------------------
GEOMETRY = {
    "a0_mm": 1.4,
    "b0_mm": 1.4,
    "h0_mm": 1.4,
    "gap_mm": 1.0,
    "t_cu_mm": 0.2,
    "t_ceramic_mm": 0.8,
}
MATERIALS = {
    "ceramic_k": 35.0,
    "cu_k": 400.0,
    "cu_rho": 1.7e-8,
    "te_k": 1.5,
    "te_rho": 1.0e-5,
    "alpha_p": 2.0e-4,
    "alpha_n": -2.0e-4,
    "tcc": 1.0e5,
    "ecc": 1.0e9,
}

# Table 4.2: A0-TT verification (100 C / 20 C, mesh C1) ---------------------------
TABLE_4_2 = {
    "u_oc_v": 30.02e-3,
    "r_int_ohm": 17.26e-3,
    "i_sc_a": 1.739,
    "r_load_opt_ohm": 17.26e-3,
    "p_max_w": 13.05e-3,
    "i_opt_a": 0.870,
    "k_te_w_k": 4.196e-3,
    "q_hot_oc_w": 0.31519,
    "dt_te_k": 75.11,
}

# Table 4.3: split of the imposed 80 K (A0-TT, open circuit)
TABLE_4_3 = {"dt_hot_passive_k": 2.84, "dt_legs_k": 75.11, "dt_cold_passive_k": 2.09}

# Table 4.4: heat balance vs current (A0-TT); U from the regression U = U_oc - R_int*I
THESIS_A0_TT_POINTS = {
    "current_a": [0.0, 0.4, 0.8, 1.8],
    "q_hot_w": [0.31519, 0.36971, 0.42145, 0.53880],
    "q_cold_w": [0.31519, 0.36046, 0.40848, 0.54068],  # magnitudes (thesis lists them negative)
    "power_w": [0.0, 9.246e-3, 12.966e-3, -1.887e-3],
}

# Table 5.4: BC-QT results (Q_h = 0.3152 W, T_c = 20 C); P_max from a parabola through I = 0, 0.4, 0.8 A
TABLE_5_4 = {
    "A0": {
        "t_hot_ext_c": 100.04,
        "dt_te_k": 75.11,
        "k_pair_w_k": 4.196e-3,
        "u_oc_v": 30.024e-3,
        "p_max_w": 7.66e-3,
        "eta": 0.0243,
    },
    "A5": {
        "t_hot_ext_c": 114.74,
        "dt_te_k": 91.32,
        "k_pair_w_k": 3.451e-3,
        "u_oc_v": 36.531e-3,
        "p_max_w": 9.91e-3,
        "eta": 0.0314,
    },
    "A10": {
        "t_hot_ext_c": 141.31,
        "dt_te_k": 117.80,
        "k_pair_w_k": 2.676e-3,
        "u_oc_v": 47.137e-3,
        "p_max_w": 12.54e-3,
        "eta": 0.0398,
    },
}

# Table 3.5: A10-QT at I = 0.8 A, meshes C1 (0.2 mm) and C2 (0.1 mm)
TABLE_3_5 = {
    "current_a": 0.8,
    "C1": {
        "voltage_v": 12.384e-3,
        "power_w": 9.907e-3,
        "q_cold_w": 0.30505,
        "t_leg_hot_c": 97.450,
        "t_leg_cold_c": 21.291,
        "dt_te_k": 76.159,
        "t_max_c": 99.607,
        "j_avg_a_m2": 0.870e6,
        "j_max_a_m2": 3.647e6,
        "joule_avg_w_m3": 2.007e6,
    },
    "C2": {
        "voltage_v": 12.385e-3,
        "power_w": 9.908e-3,
        "q_cold_w": 0.30505,
        "t_leg_hot_c": 97.501,
        "t_leg_cold_c": 21.314,
        "dt_te_k": 76.187,
        "t_max_c": 99.628,
        "j_avg_a_m2": 0.876e6,
        "j_max_a_m2": 3.733e6,
        "joule_avg_w_m3": 2.101e6,
    },
    "refinement_ratio": 1.99,
}

# Figure 5.1: maximum temperature at I = 1.6 A (BC-QT)
T_MAX_AT_1_6A_C = {"A0": 55.62, "A5": 60.03, "A10": 70.56}

Q_HOT_QT_W = 0.3152
T_COLD_C = 20.0


def variant_geometry(theta_deg: float, a_cold_mm: float = 1.4, height_mm: float = 1.4) -> dict:
    """Thesis A-series: fixed cold base and height, narrow end on the hot side."""
    return {
        "a_hot_mm": a_hot_from_angle(a_cold_mm, height_mm, theta_deg),
        "a_cold_mm": a_cold_mm,
        "height_mm": height_mm,
        "theta_deg": theta_deg,
    }


VARIANTS = {f"A{t}": variant_geometry(float(t)) for t in (0, 5, 10, 15)}


def summary() -> dict:
    return {
        "geometry": GEOMETRY,
        "materials": MATERIALS,
        "table_4_2": TABLE_4_2,
        "table_4_3": TABLE_4_3,
        "table_4_4": THESIS_A0_TT_POINTS,
        "table_5_4": TABLE_5_4,
        "table_3_5": TABLE_3_5,
        "t_max_at_1_6a_c": T_MAX_AT_1_6A_C,
        "variants": VARIANTS,
        "calibrated_a0": CALIBRATED_A0,
    }
