"""Leg geometry: truncated square pyramids (frusta), prisms and comparison constraints.

Conventions follow the thesis (Section 3.2.2): x runs from the hot end (x=0) to
the cold end (x=H), the side of the square cross-section varies linearly
a(x) = a_h + (a_c - a_h) x / H, and the taper angle is
tan(theta) = (a_c - a_h) / (2 H).  Positive theta means the narrow end is hot.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

__all__ = [
    "Frustum",
    "a_hot_from_angle",
    "angle_from_sides",
    "constant_volume_sides",
    "frustum_volume",
    "geometric_factor",
    "k_ratio_constant_volume",
]


def a_hot_from_angle(a_cold: float, height: float, theta_deg: float) -> float:
    """Hot-end side for a frustum with fixed cold base (thesis A-series)."""
    return a_cold - 2.0 * height * math.tan(math.radians(theta_deg))


def angle_from_sides(a_hot: float, a_cold: float, height: float) -> float:
    return math.degrees(math.atan((a_cold - a_hot) / (2.0 * height)))


def frustum_volume(a_hot: float, a_cold: float, height: float) -> float:
    """V = H/3 (a_h^2 + a_h a_c + a_c^2)   (thesis eq. 3.22)."""
    return height / 3.0 * (a_hot**2 + a_hot * a_cold + a_cold**2)


def geometric_factor(a_hot: float, a_cold: float, height: float) -> float:
    """Integral of dx/A(x) = H/(a_h a_c) [1/length]; K = kappa/factor, R = rho*factor."""
    return height / (a_hot * a_cold)


def constant_volume_sides(volume: float, height: float, ratio: float) -> tuple[float, float]:
    """Frustum sides (a_h, a_c) with prescribed volume, height and ratio r = a_h/a_c."""
    a_c = math.sqrt(3.0 * volume / (height * (1.0 + ratio + ratio**2)))
    return ratio * a_c, a_c


def k_ratio_constant_volume(ratio: float) -> float:
    """K_frustum/K_prism at equal volume and height = 3r/(1+r+r^2) <= 1   (thesis eq. 3.23)."""
    return 3.0 * ratio / (1.0 + ratio + ratio**2)


@dataclass(frozen=True)
class Frustum:
    a_hot: float
    a_cold: float
    height: float

    @property
    def theta_deg(self) -> float:
        return angle_from_sides(self.a_hot, self.a_cold, self.height)

    @property
    def volume(self) -> float:
        return frustum_volume(self.a_hot, self.a_cold, self.height)

    @property
    def factor(self) -> float:
        return geometric_factor(self.a_hot, self.a_cold, self.height)

    @property
    def area_hot(self) -> float:
        return self.a_hot**2

    @property
    def area_cold(self) -> float:
        return self.a_cold**2

    @property
    def a_min(self) -> float:
        return min(self.a_hot, self.a_cold)
