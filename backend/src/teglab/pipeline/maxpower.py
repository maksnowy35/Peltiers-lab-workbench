"""Analytic maximum-power references for a P-N pair with constant properties (legs only).

Three references, all vectorized over NumPy arrays (one element per design):

``p_max_frozen``   α²Q²/(4K²R)  - BC-QT with the open-circuit ΔT = Q/K held fixed.  This is the corrected
                   form of the thesis relation (Section 5.1 prints Q²/(4·K·ρκ), which lacks α² and, for a
                   pair with K·R = 4ρκ, the factor 4).  It is an upper bound: under a fixed heat input the
                   Peltier term pulls ΔT down as soon as current flows.
``p_max_qt_exact`` the true BC-QT optimum of the same ideal pair, from the junction balance
                       Q = K·ΔT + α·I·T_h − ½·I²·R,   T_h = T_c + ΔT
                   ⇒  ΔT(I) = (Q − α·I·T_c + ½·I²·R) / (K + α·I),   P(I) = I·(α·ΔT(I) − I·R),
                   maximized by a vectorized golden-section search on I ∈ (0, αQ/(KR)).
``p_max_tt``       α²ΔT²/(4R) - BC-TT: junction temperatures fixed, linear source.

With K·R constant (square frusta: K·R = 4ρκ for the pair), the frozen BC-QT power scales as 1/K - every
percent of conductance removed by tapering is a percent of power - while at fixed ΔT (BC-TT) the same
taper only adds resistance.  ``compare.py`` uses these to attribute variant gains.
"""

from __future__ import annotations

import numpy as np
from numpy.typing import ArrayLike, NDArray

__all__ = ["p_max_frozen", "p_max_qt_exact", "p_max_tt", "qt_state"]

GOLDEN = (np.sqrt(5.0) - 1.0) / 2.0


def p_max_frozen(alpha_v_k: ArrayLike, q_hot_w: ArrayLike, k_w_k: ArrayLike, r_ohm: ArrayLike) -> NDArray[np.float64]:
    """P_max = α²·Q²/(4·K²·R)."""
    a, q, k, r = (np.asarray(v, dtype=float) for v in (alpha_v_k, q_hot_w, k_w_k, r_ohm))
    return a**2 * q**2 / (4.0 * k**2 * r)


def p_max_tt(alpha_v_k: ArrayLike, dt_k: ArrayLike, r_ohm: ArrayLike) -> NDArray[np.float64]:
    """P_max = α²·ΔT²/(4·R)."""
    a, dt, r = (np.asarray(v, dtype=float) for v in (alpha_v_k, dt_k, r_ohm))
    return a**2 * dt**2 / (4.0 * r)


def qt_state(
    current_a: ArrayLike,
    alpha_v_k: ArrayLike,
    q_hot_w: ArrayLike,
    k_w_k: ArrayLike,
    r_ohm: ArrayLike,
    t_cold_k: ArrayLike,
) -> tuple[NDArray[np.float64], NDArray[np.float64], NDArray[np.float64]]:
    """(ΔT, U, P) of the ideal pair under BC-QT at the given current(s)."""
    i, a, q, k, r, tc = (np.asarray(v, dtype=float) for v in (current_a, alpha_v_k, q_hot_w, k_w_k, r_ohm, t_cold_k))
    dt = (q - a * i * tc + 0.5 * i**2 * r) / (k + a * i)
    u = a * dt - i * r
    return dt, u, i * u


def p_max_qt_exact(
    alpha_v_k: ArrayLike,
    q_hot_w: ArrayLike,
    k_w_k: ArrayLike,
    r_ohm: ArrayLike,
    t_cold_k: ArrayLike,
    iterations: int = 90,
) -> tuple[NDArray[np.float64], NDArray[np.float64], NDArray[np.float64]]:
    """Exact BC-QT optimum (P_max, I_opt, ΔT at I_opt) for every element of the broadcast inputs.

    P(0) = 0 and P < 0 at the frozen short-circuit current αQ/(KR) (there ΔT < Q/K), so the optimum
    lies strictly inside that bracket; 90 golden-section steps shrink it by 0.618⁹⁰ ≈ 2·10⁻¹⁹.
    """
    a, q, k, r, tc = np.broadcast_arrays(
        *(np.asarray(v, dtype=float) for v in (alpha_v_k, q_hot_w, k_w_k, r_ohm, t_cold_k))
    )
    lo = np.zeros_like(a)
    hi = a * q / (k * r)

    def power(i: NDArray[np.float64]) -> NDArray[np.float64]:
        return qt_state(i, a, q, k, r, tc)[2]

    for _ in range(iterations):
        c = hi - GOLDEN * (hi - lo)
        d = lo + GOLDEN * (hi - lo)
        left = power(c) > power(d)  # the maximum lies in [lo, d]
        hi = np.where(left, d, hi)
        lo = np.where(left, lo, c)
    i_opt = 0.5 * (lo + hi)
    dt, _, p = qt_state(i_opt, a, q, k, r, tc)
    return p, i_opt, dt
