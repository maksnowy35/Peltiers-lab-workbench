"""Minimal unit handling for imported ANSYS tables.

Every quantity role (see ``roles.py``) has a canonical storage unit; this module
parses unit strings as written by ANSYS Workbench ("W m^-2 C^-1", "A m^-2", "C"),
Mechanical tabular exports ("[°C]", "[mV]") or spreadsheets, and converts to the
canonical unit of the role's dimension.
"""

from __future__ import annotations

import math
import re

__all__ = ["normalize_unit", "unit_dimension", "to_canonical", "CANONICAL"]

# dimension -> canonical unit label
CANONICAL = {
    "temperature": "°C",
    "voltage": "V",
    "power": "W",
    "current": "A",
    "current_density": "A/m²",
    "volumetric_heat": "W/m³",
    "length": "mm",
    "angle": "deg",
    "tcc": "W/(m²·K)",
    "ecc": "S/m²",
    "resistance": "Ω",
    "time": "s",
    "dimensionless": "",
}

_SUP = str.maketrans({"²": "^2", "³": "^3", "⁻": "^-", "¹": "^1", "·": "", "*": "", "μ": "u", "µ": "u", "Ω": "ohm"})


def normalize_unit(unit: str | None) -> str:
    if unit is None:
        return ""
    u = unit.strip().strip("[]()").translate(_SUP).lower()
    u = u.replace("°", "deg").replace(" ", "").replace("(", "").replace(")", "")
    u = u.replace("^-1", "-1").replace("^-2", "-2").replace("^-3", "-3").replace("^2", "2").replace("^3", "3")
    return u


# normalized unit -> (dimension, factor, offset)   canonical = value*factor + offset
_UNITS: dict[str, tuple[str, float, float]] = {}


def _reg(dim: str, factor: float, offset: float, *names: str) -> None:
    for n in names:
        _UNITS[n] = (dim, factor, offset)


_reg("temperature", 1.0, 0.0, "c", "degc", "celsius", "deg_c", "oc")
_reg("temperature", 1.0, -273.15, "k", "kelvin")
_reg("temperature", 5.0 / 9.0, -32.0 * 5.0 / 9.0, "f", "degf")
_reg("voltage", 1.0, 0.0, "v", "volt")
_reg("voltage", 1e-3, 0.0, "mv")
_reg("voltage", 1e-6, 0.0, "uv")
_reg("power", 1.0, 0.0, "w", "watt")
_reg("power", 1e-3, 0.0, "mw")
_reg("power", 1e-6, 0.0, "uw")
_reg("power", 1e3, 0.0, "kw")
_reg("current", 1.0, 0.0, "a", "amp")
_reg("current", 1e-3, 0.0, "ma")
_reg("current_density", 1.0, 0.0, "am-2", "a/m2")
_reg("current_density", 1e6, 0.0, "amm-2", "a/mm2", "ma/m2")
_reg("volumetric_heat", 1.0, 0.0, "wm-3", "w/m3")
_reg("volumetric_heat", 1e9, 0.0, "wmm-3", "w/mm3")
_reg("length", 1e3, 0.0, "m")
_reg("length", 1.0, 0.0, "mm")
_reg("length", 1e-3, 0.0, "um", "micron")
_reg("length", 10.0, 0.0, "cm")
_reg("angle", 1.0, 0.0, "deg", "degree", "degrees", "°")
_reg("angle", 180.0 / math.pi, 0.0, "rad", "radian")
for _n in ("wm-2c-1", "wm-2k-1", "w/m2k", "w/m2c", "w/m2/k", "w/m2/c", "wm-2degc-1"):
    _reg("tcc", 1.0, 0.0, _n)
for _n in ("wmm-2c-1", "wmm-2k-1", "w/mm2k", "w/mm2c"):
    _reg("tcc", 1e6, 0.0, _n)
for _n in ("sm-2", "s/m2", "siemens/m2"):
    _reg("ecc", 1.0, 0.0, _n)
for _n in ("smm-2", "s/mm2"):
    _reg("ecc", 1e6, 0.0, _n)
_reg("resistance", 1.0, 0.0, "ohm", "ohms")
_reg("resistance", 1e-3, 0.0, "mohm", "milliohm")
_reg("time", 1.0, 0.0, "s", "sec")


def unit_dimension(unit: str | None) -> str | None:
    info = _UNITS.get(normalize_unit(unit))
    return info[0] if info else None


def to_canonical(values: list[float | None], unit: str | None, dimension: str) -> tuple[list[float | None], str | None]:
    """Convert values to the canonical unit of ``dimension``.

    Returns (converted, note).  If the unit is missing or unknown the values
    are assumed to be canonical already and a note is returned.
    """
    if dimension in ("dimensionless", "label", ""):
        return values, None
    u = normalize_unit(unit)
    info = _UNITS.get(u)
    if not u:
        return values, f"no unit given; assumed {CANONICAL.get(dimension, '')}"
    if info is None:
        return values, f"unknown unit '{unit}'; assumed {CANONICAL.get(dimension, '')}"
    dim, factor, offset = info
    if dim != dimension:
        return values, f"unit '{unit}' is {dim}, expected {dimension}; values left unconverted"
    return [None if v is None else v * factor + offset for v in values], None


_BRACKET = re.compile(r"^(.*?)[\s_]*[\[(]([^\[\]()]+)[\])]\s*$")


def split_name_unit(header: str) -> tuple[str, str | None]:
    """'Maximum [°C]' -> ('Maximum', '°C');  'U (mV)' -> ('U', 'mV')."""
    m = _BRACKET.match(header.strip())
    if m and m.group(2).strip():
        return m.group(1).strip(), m.group(2).strip()
    return header.strip(), None
