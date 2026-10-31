"""Canonical quantity keys of the ingestion pipeline.

Every raw ANSYS parameter or nodal result column is mapped to one of these keys.  The key
carries its unit in the suffix (``_a``, ``_v``, ``_w``, ``_c``, ...), so DataFrame columns are
self-describing and a value can never be read in the wrong unit.
"""

from __future__ import annotations

from enum import StrEnum

__all__ = ["DIMENSION", "INPUT_KEYS", "NODAL_DIMENSION", "NodalKey", "QuantityKey"]


class QuantityKey(StrEnum):
    """Scalar quantities of one design point (one row of the Workbench parameter table)."""

    CURRENT = "current_a"  # prescribed terminal current (input)
    VOLTAGE = "voltage_v"  # terminal voltage from the voltage probe
    Q_HOT = "q_hot_w"  # heat entering through the hot ceramic face (positive = in)
    Q_COLD = "q_cold_w"  # heat leaving through the cold ceramic face (positive = out)
    T_HOT_FACE = "t_hot_face_c"  # outer face of the hot ceramic plate
    T_COLD_FACE = "t_cold_face_c"  # outer face of the cold ceramic plate
    T_LEG_HOT = "t_leg_hot_c"  # average over the hot metallization (leg-end) surfaces
    T_LEG_COLD = "t_leg_cold_c"  # average over the cold metallization (leg-end) surfaces
    T_MAX = "t_max_c"  # maximum temperature in the leg volume
    J_AVG = "j_avg_a_m2"  # average current density in the legs
    J_MAX = "j_max_a_m2"  # maximum (unaveraged) current density
    JOULE_AVG = "joule_avg_w_m3"  # average volumetric Joule heat in the legs
    V_AVG = "v_avg_v"  # domain-average electric potential (diagnostic only, not a terminal voltage)
    TCC = "tcc_w_m2k"  # thermal contact conductance (swept input)
    ECC = "ecc_s_m2"  # electrical contact conductance (swept input)
    LEAK_CURRENT = "leak_current_a"  # current through the ceramic (leakage check)


# QuantityKey -> dimension understood by ``teglab.units``
DIMENSION: dict[QuantityKey, str] = {
    QuantityKey.CURRENT: "current",
    QuantityKey.VOLTAGE: "voltage",
    QuantityKey.Q_HOT: "power",
    QuantityKey.Q_COLD: "power",
    QuantityKey.T_HOT_FACE: "temperature",
    QuantityKey.T_COLD_FACE: "temperature",
    QuantityKey.T_LEG_HOT: "temperature",
    QuantityKey.T_LEG_COLD: "temperature",
    QuantityKey.T_MAX: "temperature",
    QuantityKey.J_AVG: "current_density",
    QuantityKey.J_MAX: "current_density",
    QuantityKey.JOULE_AVG: "volumetric_heat",
    QuantityKey.V_AVG: "voltage",
    QuantityKey.TCC: "tcc",
    QuantityKey.ECC: "ecc",
    QuantityKey.LEAK_CURRENT: "current",
}

INPUT_KEYS = frozenset({QuantityKey.CURRENT, QuantityKey.TCC, QuantityKey.ECC})


class NodalKey(StrEnum):
    """Field quantities of a Mechanical nodal export (one row per node)."""

    TEMPERATURE = "temperature_c"
    VOLTAGE = "voltage_v"
    CURRENT_DENSITY = "current_density_a_m2"
    JOULE_HEAT = "joule_heat_w_m3"
    HEAT_FLUX = "heat_flux_w_m2"


NODAL_DIMENSION: dict[NodalKey, str] = {
    NodalKey.TEMPERATURE: "temperature",
    NodalKey.VOLTAGE: "voltage",
    NodalKey.CURRENT_DENSITY: "current_density",
    NodalKey.JOULE_HEAT: "volumetric_heat",
    NodalKey.HEAT_FLUX: "heat_flux",
}
