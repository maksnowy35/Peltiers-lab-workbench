"""Quantity roles that imported columns can be mapped to, and name-based suggestions."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from .units import unit_dimension

__all__ = ["ROLES", "Role", "INPUT_ROLES", "suggest_role"]


@dataclass(frozen=True)
class Role:
    key: str
    label: str
    dimension: str
    group: str  # "electrical", "thermal", "field", "input", "other"
    description: str = ""


ROLES: dict[str, Role] = {
    r.key: r
    for r in [
        Role("current", "Current I", "current", "input", "load current (swept parameter)"),
        Role("voltage", "Terminal voltage U", "voltage", "electrical", "voltage between the two terminals"),
        Role("power", "Electrical power P", "power", "electrical"),
        Role("load_resistance", "Load resistance R_L", "resistance", "input"),
        Role(
            "q_hot",
            "Heat in, hot side Q_h",
            "power",
            "thermal",
            "reaction/heat flow at hot face (sign normalized to +)",
        ),
        Role("q_cold", "Heat out, cold side Q_c", "power", "thermal", "reaction at cold face (sign normalized to +)"),
        Role("t_hot_ext", "T hot face", "temperature", "thermal", "outer face of the hot ceramic"),
        Role("t_cold_ext", "T cold face", "temperature", "thermal"),
        Role("t_leg_hot", "T leg hot ends", "temperature", "thermal", "average over the hot ends of the legs"),
        Role("t_leg_cold", "T leg cold ends", "temperature", "thermal", "average over the cold ends of the legs"),
        Role("t_max", "T max", "temperature", "thermal"),
        Role("t_avg", "T average (location unknown)", "temperature", "thermal"),
        Role("j_avg", "Current density avg", "current_density", "field"),
        Role("j_max", "Current density max", "current_density", "field"),
        Role("joule_avg", "Joule heat avg", "volumetric_heat", "field"),
        Role("joule_max", "Joule heat max", "volumetric_heat", "field"),
        Role(
            "v_avg", "Electric potential avg", "voltage", "field", "domain average potential, not the terminal voltage"
        ),
        Role("tcc", "Thermal contact conductance", "tcc", "input"),
        Role("ecc", "Electrical contact conductance", "ecc", "input"),
        Role("angle", "Taper angle θ", "angle", "input"),
        Role("a_hot", "Leg side a_h (hot)", "length", "input"),
        Role("a_cold", "Leg side a_c (cold)", "length", "input"),
        Role("height", "Leg height H", "length", "input"),
        Role("time", "Time / load step", "time", "input", "can be converted to current with a scale factor"),
        Role("position", "Position along path", "length", "input", "for path (profile) exports"),
        Role("t_profile", "Temperature along path", "temperature", "field"),
        Role("label", "Row label", "label", "other"),
        Role("ignore", "Ignore", "label", "other"),
    ]
}

INPUT_ROLES = {"current", "load_resistance", "tcc", "ecc", "angle", "a_hot", "a_cold", "height", "time", "position"}


def _has(pattern: str, text: str) -> bool:
    return re.search(pattern, text) is not None


def _fold(name: str) -> str:
    """Lower-case and strip accents so that matching works on plain ASCII keywords."""
    decomposed = unicodedata.normalize("NFKD", name.lower())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def suggest_role(name: str, unit: str | None, values: list[float | None], numeric: bool) -> tuple[str, float]:
    """Return (role, confidence 0..1) for a column."""
    if not numeric:
        return ("label", 0.9) if _has(r"^(name|dp|design point|id)$", name.strip().lower()) else ("ignore", 0.6)
    n = _fold(name)
    dim = unit_dimension(unit)
    vals = [v for v in values if v is not None]
    mean = sum(vals) / len(vals) if vals else 0.0

    if dim == "current_density" or _has(r"current density", n):
        return ("j_max", 0.9) if _has(r"max", n) else ("j_avg", 0.8)
    if dim == "volumetric_heat" or _has(r"joule", n):
        return ("joule_max", 0.85) if _has(r"max", n) else ("joule_avg", 0.8)
    if dim == "tcc" or _has(r"thermal conductance|\btcc\b", n):
        return "tcc", 0.9
    if dim == "ecc" or _has(r"electric(al)? conductance|\becc\b", n):
        return "ecc", 0.9
    if _has(r"flux", n):
        return "ignore", 0.5
    if _has(r"electric voltage.*(average|avg)|voltage (average|avg)|potential (average|avg)", n):
        return "v_avg", 0.7
    if dim == "resistance" or _has(r"load resistance|\br_?l\b", n):
        return "load_resistance", 0.7
    if _has(r"(^|[^a-z])current([^a-z]|$)|current magnitude|^i$|^i[ _\[]", n):
        return "current", 0.9 if dim in (None, "current") else 0.6
    if _has(r"voltage probe|terminal voltage|^u$|^u[ _\[]|^u_?out", n):
        return "voltage", 0.9
    if _has(r"voltage", n) or dim == "voltage":
        return "voltage", 0.6
    if _has(r"(^|[^a-z])power([^a-z]|$)|^p_?el|^p$", n):
        return "power", 0.8
    if _has(r"heat|reaction|q_?h|q_?c|^q[ _\[]|^q$", n) or (dim == "power" and not _has(r"power", n)):
        if _has(r"hot|q_?h|heat in|input", n):
            return "q_hot", 0.8
        if _has(r"cold|q_?c|heat out|output", n):
            return "q_cold", 0.8
        return ("q_cold", 0.5) if mean < 0 else ("q_hot", 0.5)
    if dim == "temperature" or _has(r"temp|^t[_ ]|^t$|tmax|t_max", n):
        if _has(r"max", n):
            return "t_max", 0.85
        if _has(r"min", n):
            return "ignore", 0.5
        if _has(r"leg.?cold|cold.?leg|leg_c|cold.?end", n):
            return "t_leg_cold", 0.85
        if _has(r"leg.?hot|hot.?leg|leg_h|hot.?end", n):
            return "t_leg_hot", 0.85
        if _has(r"hot|t_?h\b|th_ext", n):
            return "t_hot_ext", 0.7
        if _has(r"cold|t_?c\b|tc_ext", n):
            return "t_cold_ext", 0.7
        if _has(r"path|along|profile", n):
            return "t_profile", 0.6
        return "t_avg", 0.4
    if dim == "angle" or _has(r"angle|theta|taper", n):
        return "angle", 0.85
    if _has(r"\ba_?h\b|hot base", n):
        return "a_hot", 0.8
    if _has(r"\ba_?c\b|cold base", n):
        return "a_cold", 0.8
    if _has(r"height|^h$|^h0$|^h[ _\[]", n):
        return "height", 0.8
    if dim == "time" or _has(r"time|step", n):
        return "time", 0.8
    if dim == "length" or _has(r"position|distance|length|arc|^x$|^s$", n):
        return "position", 0.6
    return "ignore", 0.3
