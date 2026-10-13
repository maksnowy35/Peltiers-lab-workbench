"""Design specification of a single P-N thermoelectric pair (unit cell).

The specification mirrors the thesis model: two legs (square cross-section,
prism or truncated pyramid), copper electrodes, two ceramic plates and four
metallized contacts with finite thermal (TCC) and electrical (ECC)
conductance.  All user-facing inputs use engineering units (mm, degC, mOhm).
"""

from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel, Field, model_validator

HotMode = Literal["T", "Q", "HX"]
ColdMode = Literal["T", "HX"]


class Segment(BaseModel):
    material: str = Field(description="material id from the library")
    fraction: float = Field(1.0, gt=0, le=1, description="fraction of leg height, listed from the hot end")


class LegSpec(BaseModel):
    a_hot_mm: float = Field(1.4, gt=0, description="side of the square cross-section at the hot end")
    a_cold_mm: float = Field(1.4, gt=0, description="side of the square cross-section at the cold end")
    segments: list[Segment]

    @model_validator(mode="after")
    def _check_fractions(self) -> LegSpec:
        total = sum(s.fraction for s in self.segments)
        if not self.segments or abs(total - 1.0) > 1e-6:
            raise ValueError(f"segment fractions must sum to 1 (got {total:.4f})")
        return self


class Contacts(BaseModel):
    active: bool = Field(True, description="False = ideal (bonded, infinite conductance) joints")
    tcc: float = Field(1.0e5, gt=0, description="thermal contact conductance [W/(m2 K)]")
    ecc: float = Field(1.0e9, gt=0, description="electrical contact conductance [S/m2]")


class Passive(BaseModel):
    ceramic_thickness_mm: float = Field(0.8, ge=0)
    ceramic_k: float = Field(35.0, gt=0)
    ceramic_area_mm2: float | None = Field(
        None, gt=0, description="ceramic footprint per pair; default (2a+g)*a with a = larger leg side"
    )
    cu_thickness_mm: float = Field(0.2, ge=0)
    cu_k: float = Field(400.0, gt=0)
    cu_rho: float = Field(1.7e-8, gt=0)
    gap_mm: float = Field(1.0, ge=0, description="gap between P and N legs")
    terminal_length_mm: float = Field(
        0.0, ge=0, description="Cu path from each leg to the current lead attachment (0 = lead right at the leg)"
    )
    hot_side_factor: float = Field(1.0, gt=0, description="calibration factor on ceramic+Cu resistance (hot side)")
    cold_side_factor: float = Field(1.0, gt=0, description="calibration factor on ceramic+Cu resistance (cold side)")
    r_el_extra_mohm: float = Field(
        0.0, ge=-50, le=500, description="series resistance correction (3D current crowding, leads); may be negative"
    )


class Boundary(BaseModel):
    hot_mode: HotMode = "Q"
    cold_mode: ColdMode = "T"
    t_hot_c: float = Field(100.0, description="hot face temperature (hot_mode=T)")
    q_hot_w: float = Field(0.3152, ge=0, description="heat input per pair (hot_mode=Q)")
    t_source_c: float = Field(250.0, description="hot source temperature (hot_mode=HX)")
    r_source_k_w: float = Field(150.0, gt=0, description="hot-side heat-exchanger resistance per pair (HX)")
    t_cold_c: float = Field(20.0, description="cold face temperature (cold_mode=T)")
    t_sink_c: float = Field(20.0, description="sink temperature (cold_mode=HX)")
    r_sink_k_w: float = Field(20.0, gt=0, description="cold-side heat-sink resistance per pair (HX)")

    @property
    def label(self) -> str:
        hot = {"T": "T", "Q": "Q", "HX": "HX"}[self.hot_mode]
        cold = {"T": "T", "HX": "HX"}[self.cold_mode]
        if hot == "HX" or cold == "HX":
            return f"{hot}-{cold}"
        return f"{hot}{cold}"


class PairDesign(BaseModel):
    name: str = "A0 reference"
    height_mm: float = Field(1.4, gt=0)
    p_leg: LegSpec
    n_leg: LegSpec
    contacts: Contacts = Contacts()
    passive: Passive = Passive()
    boundary: Boundary = Boundary()
    c3d: float = Field(0.5, ge=0, description="frustum 3D shape correction: R3D = R1D*(1 + c3d*tan^2(theta))")
    alpha_factor: float = Field(1.0, gt=0)
    side_loss_h: float = Field(0.0, ge=0, description="side-wall loss coefficient [W/(m2 K)] (radiation+convection)")
    t_ambient_c: float = 20.0
    n_elements: int = Field(30, ge=4, le=400)

    # -------------------------------------------------------------- helpers
    def taper_deg(self, leg: Literal["p", "n"] = "p") -> float:
        spec = self.p_leg if leg == "p" else self.n_leg
        return math.degrees(math.atan((spec.a_cold_mm - spec.a_hot_mm) / (2.0 * self.height_mm)))

    def with_geometry(
        self,
        a_hot_mm: float,
        a_cold_mm: float,
        height_mm: float | None = None,
        n_area_ratio: float = 1.0,
    ) -> PairDesign:
        """Copy with both legs set to the given frustum; the N leg can be scaled in area."""
        s = math.sqrt(n_area_ratio)
        d = self.model_copy(deep=True)
        d.p_leg.a_hot_mm, d.p_leg.a_cold_mm = a_hot_mm, a_cold_mm
        d.n_leg.a_hot_mm, d.n_leg.a_cold_mm = a_hot_mm * s, a_cold_mm * s
        if height_mm is not None:
            d.height_mm = height_mm
        return d


def reference_design(name: str = "A0 reference (thesis)") -> PairDesign:
    """Thesis reference pair (Tables 3.1 and 3.2, TCC=1e5, ECC=1e9, BC-QT 0.3152 W / 20 C).

    The ceramic/Cu calibration factors and the extra resistance come from the
    calibration against the thesis A0-TT results (see ``reference.py`` and
    ``docs/validation.md``).
    """
    from .reference import CALIBRATED_A0

    return PairDesign(
        name=name,
        height_mm=1.4,
        p_leg=LegSpec(a_hot_mm=1.4, a_cold_mm=1.4, segments=[Segment(material="thesis_p")]),
        n_leg=LegSpec(a_hot_mm=1.4, a_cold_mm=1.4, segments=[Segment(material="thesis_n")]),
        contacts=Contacts(active=True, tcc=1e5, ecc=1e9),
        passive=Passive(
            hot_side_factor=CALIBRATED_A0["hot_side_factor"],
            cold_side_factor=CALIBRATED_A0["cold_side_factor"],
            r_el_extra_mohm=CALIBRATED_A0["r_el_extra_mohm"],
        ),
        boundary=Boundary(hot_mode="Q", cold_mode="T", q_hot_w=0.3152, t_cold_c=20.0, t_hot_c=100.0),
        c3d=CALIBRATED_A0["c3d"],
    )
