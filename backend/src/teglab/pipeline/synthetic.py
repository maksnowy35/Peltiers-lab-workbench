"""A synthetic, physically consistent unit cell for demos and end-to-end tests - **not ANSYS output**.

Geometry: the thesis A0 cell (Table 3.1) with the hot face on top (y = 3.4 mm)::

    ceramic_hot  y 2.6-3.4   (4.4 × 2.0 mm plate)
    cu_hot       y 2.4-2.6   (bridge over both legs)
    leg_p/leg_n  y 1.0-2.4   (1.4 × 1.4 mm legs, 1.0 mm gap)
    cu_cold_p/n  y 0.8-1.0   (one electrode per leg)
    ceramic_cold y 0.0-0.8

Physics: a 1D thermal chain at a prescribed heat input (BC-QT) and current, with constant properties.
Heat crosses each contact by conduction (ΔT = q/TCC); the Peltier heat α·I·T and the contact Joule heat
I²/(ECC·A) act on the leg side of each junction, so the conductive flux in a leg differs from the metal
side by exactly those heats - the same situation as in a thermoelectric FE model.  Legs carry Joule heat
(parabolic T); contacts drop ΔV = J/ECC; the energy balance Q_in − Q_out = U·I closes to round-off.
Fields are uniform across each layer, which keeps every face average exact,
so the reductions in ``fields.py`` and the contact checks in ``nodal.py`` can be tested against the
closed-form values below.  Copper spreading is not modeled (flux over the leg footprints only).
"""

from __future__ import annotations

import io
import json
import struct
import zipfile
import zlib
from collections.abc import Iterator
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .keys import NodalKey
from .schemas import ImageCalibration

__all__ = ["CELL", "Box", "CellParameters", "CellSolution", "demo_zip", "mechanical_text", "render_front", "solve"]

T0 = 273.15


@dataclass(frozen=True)
class Box:
    x: tuple[float, float]
    y: tuple[float, float]
    z: tuple[float, float]


CELL: dict[str, Box] = {
    "ceramic_hot": Box((-0.3, 4.1), (2.6, 3.4), (-0.3, 1.7)),
    "cu_hot": Box((0.0, 3.8), (2.4, 2.6), (0.0, 1.4)),
    "leg_p": Box((0.0, 1.4), (1.0, 2.4), (0.0, 1.4)),
    "leg_n": Box((2.4, 3.8), (1.0, 2.4), (0.0, 1.4)),
    "cu_cold_p": Box((0.0, 1.4), (0.8, 1.0), (0.0, 1.4)),
    "cu_cold_n": Box((2.4, 3.8), (0.8, 1.0), (0.0, 1.4)),
    "ceramic_cold": Box((-0.3, 4.1), (0.0, 0.8), (-0.3, 1.7)),
}
LEG_A_M2 = 1.4e-3**2
LEG_L_M = 1.4e-3
CU_T_M = 0.2e-3
CU_BRIDGE_W_M = 1.4e-3
CERAMIC_T_M = 0.8e-3
CERAMIC_A_M2 = 4.4e-3 * 2.0e-3


@dataclass(frozen=True)
class CellParameters:
    """Operating point and properties (legs and contacts as in thesis Table 3.2 / Section 3.7.2)."""

    q_hot_w: float = 0.3152
    t_cold_c: float = 20.0
    current_a: float = 0.8
    tcc_w_m2k: float | None = 1.0e5  # None = bonded (no thermal contact resistance)
    ecc_s_m2: float | None = 1.0e9  # None = bonded (no electrical contact resistance)
    alpha_p_v_k: float = 2.0e-4
    alpha_n_v_k: float = -2.0e-4
    rho_te_ohm_m: float = 1.0e-5
    kappa_te_w_mk: float = 1.5
    k_cu_w_mk: float = 400.0  # typical copper
    rho_cu_ohm_m: float = 1.7e-8
    k_ceramic_w_mk: float = 25.0  # typical alumina

    @property
    def alpha_pn_v_k(self) -> float:
        return self.alpha_p_v_k - self.alpha_n_v_k


@dataclass
class CellSolution:
    params: CellParameters
    t_hot_face_c: float
    t_cu_hot_top_c: float
    t_cu_hot_bottom_c: float
    t_hot_junction_c: float  # leg hot end
    t_cold_junction_c: float  # leg cold end
    t_cu_cold_top_c: float
    t_ceramic_cold_top_c: float
    q_legs_hot_w: float  # conductive heat entering the legs (both)
    q_legs_cold_w: float  # conductive heat leaving the legs
    q_out_w: float  # heat leaving through the cold face
    voltage_v: float  # terminal voltage (cu_cold_p − cu_cold_n)
    potentials: dict[str, float] = field(default_factory=dict)

    @property
    def power_w(self) -> float:
        return self.voltage_v * self.params.current_a

    @property
    def dt_te_k(self) -> float:
        return self.t_hot_junction_c - self.t_cold_junction_c

    @property
    def dt_contact_hot_k(self) -> float:
        return self.t_cu_hot_bottom_c - self.t_hot_junction_c

    @property
    def dt_contact_cold_k(self) -> float:
        return self.t_cold_junction_c - self.t_cu_cold_top_c

    @property
    def current_density_a_m2(self) -> float:
        return self.params.current_a / LEG_A_M2

    @property
    def dv_contact_v(self) -> float:
        ecc = self.params.ecc_s_m2
        return 0.0 if ecc is None else self.current_density_a_m2 / ecc


def solve(p: CellParameters | None = None) -> CellSolution:
    """Fixed-point solution of the chain (converges in a few iterations: α·I/K ≈ 0.08)."""
    p = p or CellParameters()
    i = p.current_a
    r_con = 0.0 if p.tcc_w_m2k is None else 1.0 / (p.tcc_w_m2k * 2 * LEG_A_M2)  # two legs in parallel
    r_cer = CERAMIC_T_M / (p.k_ceramic_w_mk * CERAMIC_A_M2)
    r_cu = CU_T_M / (p.k_cu_w_mk * 2 * LEG_A_M2)
    k_legs = 2 * p.kappa_te_w_mk * LEG_A_M2 / LEG_L_M
    r_legs = 2 * p.rho_te_ohm_m * LEG_L_M / LEG_A_M2
    r_ce = 0.0 if p.ecc_s_m2 is None else 2.0 / (p.ecc_s_m2 * LEG_A_M2)  # two contacts in series per side
    a = p.alpha_pn_v_k
    t_hj, t_cj = p.t_cold_c + 60.0, p.t_cold_c + 5.0
    for _ in range(200):
        # contact Joule heat and Peltier heat act on the leg side of each junction
        q_leg_h = p.q_hot_w + i * i * r_ce - a * i * (t_hj + T0)
        q_leg_c = q_leg_h + i * i * r_legs
        q_out = q_leg_c + i * i * r_ce + a * i * (t_cj + T0)
        t_cer_top = p.t_cold_c + q_out * r_cer
        t_cu_cold_top = t_cer_top + q_out * r_cu
        t_cj_new = t_cu_cold_top + q_out * r_con
        t_hj_new = t_cj_new + (q_leg_h + 0.5 * i * i * r_legs) / k_legs
        done = abs(t_hj_new - t_hj) < 1e-12 and abs(t_cj_new - t_cj) < 1e-12
        t_hj, t_cj = t_hj_new, t_cj_new
        if done:
            break
    t_cu_hot_bottom = t_hj + p.q_hot_w * r_con
    t_cu_hot_top = t_cu_hot_bottom + p.q_hot_w * r_cu
    t_hot_face = t_cu_hot_top + p.q_hot_w * r_cer

    j = i / LEG_A_M2
    dv_con = 0.0 if p.ecc_s_m2 is None else j / p.ecc_s_m2
    r_leg_one = p.rho_te_ohm_m * LEG_L_M / LEG_A_M2
    dt = t_hj - t_cj
    pot = {"cu_cold_n": 0.0}  # current: cu_cold_n → leg_n (up) → cu_hot → leg_p (down) → cu_cold_p
    pot["leg_n_bottom"] = -dv_con
    pot["leg_n_top"] = pot["leg_n_bottom"] - i * r_leg_one - p.alpha_n_v_k * dt
    pot["cu_hot"] = pot["leg_n_top"] - dv_con
    pot["leg_p_top"] = pot["cu_hot"] - dv_con
    pot["leg_p_bottom"] = pot["leg_p_top"] - i * r_leg_one + p.alpha_p_v_k * dt
    pot["cu_cold_p"] = pot["leg_p_bottom"] - dv_con
    return CellSolution(
        params=p,
        t_hot_face_c=t_hot_face,
        t_cu_hot_top_c=t_cu_hot_top,
        t_cu_hot_bottom_c=t_cu_hot_bottom,
        t_hot_junction_c=t_hj,
        t_cold_junction_c=t_cj,
        t_cu_cold_top_c=t_cu_cold_top,
        t_ceramic_cold_top_c=t_cer_top,
        q_legs_hot_w=q_leg_h,
        q_legs_cold_w=q_leg_c,
        q_out_w=q_out,
        voltage_v=pot["cu_cold_p"] - pot["cu_cold_n"],
        potentials=pot,
    )


def fields_at(sol: CellSolution, tag: str, y_mm: np.ndarray) -> dict[NodalKey, np.ndarray]:
    """Field values of body ``tag`` at heights ``y_mm`` (uniform across the layer)."""
    p, box = sol.params, CELL[tag]
    y = np.asarray(y_mm, dtype=float)
    frac = (box.y[1] - y) / (box.y[1] - box.y[0])  # 0 at the top (hot) face, 1 at the bottom
    nan = np.full_like(y, np.nan)

    def linear(top: float, bottom: float) -> np.ndarray:
        return top + (bottom - top) * frac

    j_leg = sol.current_density_a_m2
    if tag == "ceramic_hot":
        t, q = linear(sol.t_hot_face_c, sol.t_cu_hot_top_c), p.q_hot_w / CERAMIC_A_M2
        return {NodalKey.TEMPERATURE: t, NodalKey.HEAT_FLUX: np.full_like(y, q)}
    if tag == "ceramic_cold":
        t, q = linear(sol.t_ceramic_cold_top_c, p.t_cold_c), sol.q_out_w / CERAMIC_A_M2
        return {NodalKey.TEMPERATURE: t, NodalKey.HEAT_FLUX: np.full_like(y, q)}
    if tag == "cu_hot":
        j = p.current_a / (CU_T_M * CU_BRIDGE_W_M)  # the bridge carries the current sideways
        return {
            NodalKey.TEMPERATURE: linear(sol.t_cu_hot_top_c, sol.t_cu_hot_bottom_c),
            NodalKey.HEAT_FLUX: np.full_like(y, p.q_hot_w / (2 * LEG_A_M2)),
            NodalKey.VOLTAGE: np.full_like(y, sol.potentials["cu_hot"]),
            NodalKey.CURRENT_DENSITY: np.full_like(y, j),
            NodalKey.JOULE_HEAT: np.full_like(y, p.rho_cu_ohm_m * j * j),
        }
    if tag in ("cu_cold_p", "cu_cold_n"):
        return {
            NodalKey.TEMPERATURE: linear(sol.t_cu_cold_top_c, sol.t_ceramic_cold_top_c),
            NodalKey.HEAT_FLUX: np.full_like(y, sol.q_out_w / (2 * LEG_A_M2)),
            NodalKey.VOLTAGE: np.full_like(y, sol.potentials[tag]),
            NodalKey.CURRENT_DENSITY: np.full_like(y, j_leg),
            NodalKey.JOULE_HEAT: np.full_like(y, p.rho_cu_ohm_m * j_leg * j_leg),
        }
    if tag in ("leg_p", "leg_n"):
        s = frac * LEG_L_M  # distance from the hot end [m]
        q_h = sol.q_legs_hot_w / (2 * LEG_A_M2)
        joule = p.rho_te_ohm_m * j_leg * j_leg
        t = sol.t_hot_junction_c - (q_h * s + 0.5 * joule * s * s) / p.kappa_te_w_mk
        if tag == "leg_p":  # current flows down: V(s) = V_top − ρ·J·s + α_p·(T_hot − T)
            v = sol.potentials["leg_p_top"] - p.rho_te_ohm_m * j_leg * s + p.alpha_p_v_k * (sol.t_hot_junction_c - t)
        else:  # current flows up: V(s) = V_bottom − ρ·J·(L − s) − α_n·(T − T_cold)
            v = (
                sol.potentials["leg_n_bottom"]
                - p.rho_te_ohm_m * j_leg * (LEG_L_M - s)
                - p.alpha_n_v_k * (t - sol.t_cold_junction_c)
            )
        return {
            NodalKey.TEMPERATURE: t,
            NodalKey.HEAT_FLUX: q_h + joule * s,
            NodalKey.VOLTAGE: v,
            NodalKey.CURRENT_DENSITY: np.full_like(y, j_leg),
            NodalKey.JOULE_HEAT: np.full_like(y, joule),
        }
    return {NodalKey.TEMPERATURE: nan}


def _axis_nodes(lo: float, hi: float, spacing: float) -> np.ndarray:
    return np.linspace(lo, hi, max(3, int(round((hi - lo) / spacing)) + 1))


def bodies(sol: CellSolution, spacing_mm: float = 0.1) -> dict[str, pd.DataFrame]:
    """Structured node clouds of every body with all fields (canonical columns of ``NodalTable``)."""
    out: dict[str, pd.DataFrame] = {}
    first_id = 1
    for tag, box in CELL.items():
        x = _axis_nodes(*box.x, spacing_mm)
        y = _axis_nodes(*box.y, spacing_mm)
        z = _axis_nodes(*box.z, spacing_mm)
        X, Y, Z = (a.ravel() for a in np.meshgrid(x, y, z, indexing="ij"))
        frame = pd.DataFrame(
            {"node_id": pd.array(first_id + np.arange(X.size), dtype="Int64"), "x_mm": X, "y_mm": Y, "z_mm": Z}
        )
        for key, values in fields_at(sol, tag, Y).items():
            frame[key.value] = values
        out[tag] = frame
        first_id += X.size
    return out


# ------------------------------------------------------------------------------ Mechanical text export
EXPORT_HEADERS: dict[NodalKey, tuple[str, str, float]] = {  # name, unit, factor from canonical
    NodalKey.TEMPERATURE: ("Temperature", "°C", 1.0),
    NodalKey.VOLTAGE: ("Electric Voltage", "V", 1.0),
    NodalKey.CURRENT_DENSITY: ("Total Current Density", "A/m²", 1.0),
    NodalKey.HEAT_FLUX: ("Total Heat Flux", "W/m²", 1.0),
    NodalKey.JOULE_HEAT: ("Joule Heat", "W/m³", 1.0),
}
FILE_SUFFIX: dict[NodalKey, str] = {
    NodalKey.TEMPERATURE: "temperature",
    NodalKey.VOLTAGE: "voltage",
    NodalKey.CURRENT_DENSITY: "current_density",
    NodalKey.HEAT_FLUX: "heat_flux",
    NodalKey.JOULE_HEAT: "joule_heat",
}


def mechanical_text(frame: pd.DataFrame, key: NodalKey) -> bytes:
    """One quantity of one body written like Mechanical's *Export Text File* (SI units, cp1252, CRLF)."""
    name, unit, factor = EXPORT_HEADERS[key]
    header = f"Node Number\tX Location (m)\tY Location (m)\tZ Location (m)\t{name} ({unit})"
    body = pd.DataFrame(
        {
            "n": frame["node_id"].astype("int64"),
            "x": frame["x_mm"] * 1e-3,
            "y": frame["y_mm"] * 1e-3,
            "z": frame["z_mm"] * 1e-3,
            "v": frame[key.value] * factor,
        }
    ).to_csv(sep="\t", header=False, index=False, float_format="%.9g", lineterminator="\r\n")
    return (header + "\r\n" + body).encode("cp1252")


# --------------------------------------------------------------------------------------- PNG render
# default 9-band contour legend of ANSYS Mechanical (blue = minimum … red = maximum)
ANSYS_BANDS = np.array(
    [
        (0, 0, 255),
        (0, 178, 255),
        (0, 255, 255),
        (0, 255, 178),
        (0, 255, 0),
        (178, 255, 0),
        (255, 255, 0),
        (255, 178, 0),
        (255, 0, 0),
    ],
    dtype=np.uint8,
)


def _png(rgb: np.ndarray) -> bytes:
    h, w, _ = rgb.shape
    raw = np.concatenate([np.zeros((h, 1), dtype=np.uint8), rgb.reshape(h, w * 3)], axis=1).tobytes()

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b"")


def render_front(
    sol: CellSolution,
    key: NodalKey = NodalKey.TEMPERATURE,
    width: int = 1200,
    height: int = 800,
    margin: int = 90,
    window: tuple[float, float, float, float] | None = None,
) -> tuple[bytes, ImageCalibration]:
    """An ANSYS-like banded contour picture of the front view (x right, y up) and its exact calibration.

    By default the cell is centered with ``margin`` pixels around it; ``window = (x0, x1, y0, y1)`` [mm]
    instead maps exactly that model rectangle onto the whole picture (what a camera with a given focal
    point and scene size would show).
    """
    h0, h1 = min(b.x[0] for b in CELL.values()), max(b.x[1] for b in CELL.values())
    v0, v1 = min(b.y[0] for b in CELL.values()), max(b.y[1] for b in CELL.values())
    if window is not None:
        h0, h1, v0, v1 = window
        sx, sy = width / (h1 - h0), height / (v1 - v0)
        left, top = 0.0, 0.0
    else:
        sx = sy = min((width - 2 * margin) / (h1 - h0), (height - 2 * margin) / (v1 - v0))  # px per mm
        left = (width - sx * (h1 - h0)) / 2
        top = (height - sy * (v1 - v0)) / 2
    h = h0 + (np.arange(width) + 0.5 - left) / sx
    v = v1 - (np.arange(height) + 0.5 - top) / sy

    label = np.full((height, width), -1, dtype=int)
    value = np.full((height, width), np.nan)
    order = sorted(CELL, key=lambda t: CELL[t].z[1])  # nearer bodies are painted last
    for tag in order:
        box = CELL[tag]
        cols = (h >= box.x[0]) & (h <= box.x[1])
        rows = (v >= box.y[0]) & (v <= box.y[1])
        if not cols.any() or not rows.any():
            continue
        row_values = fields_at(sol, tag, v[rows]).get(key)
        block = np.ix_(rows, cols)
        label[block] = list(CELL).index(tag)
        value[block] = np.nan if row_values is None else row_values[:, None]

    rgb = np.full((height, width, 3), 255, dtype=np.uint8)
    finite = np.isfinite(value)
    lo, hi = float(value[finite].min()), float(value[finite].max())
    band = np.clip(((value[finite] - lo) / (hi - lo if hi > lo else 1.0) * 9).astype(int), 0, 8)
    rgb[finite] = ANSYS_BANDS[band]
    rgb[(label >= 0) & ~finite] = (200, 200, 200)  # body without this quantity (e.g. no voltage in a ceramic)
    edge = np.zeros_like(label, dtype=bool)
    edge[:, :-1] |= label[:, :-1] != label[:, 1:]
    edge[:-1, :] |= label[:-1, :] != label[1:, :]
    rgb[edge] = (40, 40, 40)

    cal = ImageCalibration(
        width_px=width,
        height_px=height,
        p1_px=(left, top + sy * (v1 - v0)),
        p1_model=(h0, v0),
        p2_px=(left + sx * (h1 - h0), top),
        p2_model=(h1, v1),
    )
    return _png(rgb), cal


# ------------------------------------------------------------------------------------ export archive
def export_files(sol: CellSolution, spacing_mm: float = 0.1, images: bool = True) -> Iterator[tuple[str, bytes]]:
    """The files ``tools/ansys/teglab_export_nodal.py`` writes, generated from the synthetic cell."""
    manifest: dict = {
        "generator": "teglab synthetic cell",
        "system": "Synthetic A0 cell (" + ("contacts applied" if sol.params.tcc_w_m2k else "bonded") + ")",
        "analysis": "Thermal-Electric",
        "axis": None,
        "synthetic": True,
        "files": [],
        "images": [],
    }
    for tag, frame in bodies(sol, spacing_mm).items():
        for key in EXPORT_HEADERS:
            if key.value in frame and frame[key.value].notna().any():
                name = f"{tag}__{FILE_SUFFIX[key]}.txt"
                manifest["files"].append({"file": name, "body": tag, "quantity": key.value})
                yield name, mechanical_text(frame, key)
    if images:
        for key in (NodalKey.TEMPERATURE, NodalKey.VOLTAGE):
            png, cal = render_front(sol, key)
            name = f"front__{FILE_SUFFIX[key]}.png"
            manifest["images"].append(
                {
                    "file": name,
                    "view": "front",
                    "quantity": key.value,
                    "calibration": cal.model_dump(mode="json"),
                    "calibration_source": "exact",
                }
            )
            yield name, png
    yield "manifest.json", json.dumps(manifest, indent=2).encode("utf-8")


def demo_zip(params: CellParameters | None = None, spacing_mm: float = 0.1) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in export_files(solve(params), spacing_mm):
            zf.writestr(f"teglab_export/{name}", data)
    return buf.getvalue()
