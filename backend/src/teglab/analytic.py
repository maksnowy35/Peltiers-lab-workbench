"""Closed-form constant-property relations (thesis Section 3.2, 3.5.4, 5.2).

These ignore ceramics, electrodes and contacts and are used as quick
estimates and as sanity checks for the numerical model.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass

from .geometry import a_hot_from_angle, frustum_volume, geometric_factor

__all__ = ["IdealPair", "ideal_pair", "eta_max_ideal", "thesis_table_5_2"]


@dataclass
class IdealPair:
    a_hot_mm: float
    a_cold_mm: float
    height_mm: float
    volume_leg_mm3: float
    k_pair_w_k: float
    r_pair_ohm: float
    alpha_pn_v_k: float

    # --- fixed temperatures on the leg ends (BC-TT) ---------------------
    def tt(self, dt: float) -> dict:
        u_oc = self.alpha_pn_v_k * dt
        return {
            "dt_k": dt,
            "u_oc_v": u_oc,
            "i_sc_a": u_oc / self.r_pair_ohm,
            "p_max_w": u_oc**2 / (4.0 * self.r_pair_ohm),
            "q_oc_w": self.k_pair_w_k * dt,
        }

    # --- fixed heat input (BC-QT) at open circuit -------------------------
    def qt(self, q_hot_w: float) -> dict:
        dt = q_hot_w / self.k_pair_w_k
        u_oc = self.alpha_pn_v_k * dt
        return {
            "dt_k": dt,
            "u_oc_v": u_oc,
            # P = U^2/4R with the open-circuit dT held fixed (optimistic, see thesis 5.3)
            "p_max_w": u_oc**2 / (4.0 * self.r_pair_ohm),
        }

    def as_dict(self) -> dict:
        return asdict(self)


def ideal_pair(
    a_hot_mm: float,
    a_cold_mm: float,
    height_mm: float,
    alpha_p: float = 2e-4,
    alpha_n: float = -2e-4,
    rho: float = 1e-5,
    kappa: float = 1.5,
) -> IdealPair:
    """Two identical legs, constant properties (P and N share rho and kappa)."""
    f = geometric_factor(a_hot_mm * 1e-3, a_cold_mm * 1e-3, height_mm * 1e-3)
    k_leg = kappa / f
    r_leg = rho * f
    return IdealPair(
        a_hot_mm=a_hot_mm,
        a_cold_mm=a_cold_mm,
        height_mm=height_mm,
        volume_leg_mm3=frustum_volume(a_hot_mm, a_cold_mm, height_mm),
        k_pair_w_k=2.0 * k_leg,
        r_pair_ohm=2.0 * r_leg,
        alpha_pn_v_k=alpha_p - alpha_n,
    )


def eta_max_ideal(zt_mean: float, t_hot_k: float, t_cold_k: float) -> float:
    """Maximum efficiency of an ideal generator with average device ZT."""
    if t_hot_k <= t_cold_k:
        return 0.0
    m = math.sqrt(1.0 + max(zt_mean, 0.0))
    return (1.0 - t_cold_k / t_hot_k) * (m - 1.0) / (m + t_cold_k / t_hot_k)


def thesis_table_5_2(q_hot_w: float = 0.3152, angles=(0.0, 5.0, 10.0, 15.0)) -> list[dict]:
    """Reproduces thesis Table 5.2 (H = a_c = 1.4 mm, kappa = 1.5, rho = 1e-5)."""
    rows = []
    for th in angles:
        a_h = a_hot_from_angle(1.4, 1.4, th)
        p = ideal_pair(a_h, 1.4, 1.4)
        q = p.qt(q_hot_w)
        rows.append(
            {
                "variant": f"A{int(th)}",
                "theta_deg": th,
                "a_hot_mm": a_h,
                "volume_leg_mm3": p.volume_leg_mm3,
                "k_pair_mw_k": p.k_pair_w_k * 1e3,
                "r_pair_mohm": p.r_pair_ohm * 1e3,
                "dt_te_k": q["dt_k"],
                "p_max_mw": q["p_max_w"] * 1e3,
            }
        )
    return rows
