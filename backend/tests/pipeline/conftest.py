"""Fixtures of the pipeline tests.

The parameter tables are the bundled examples (``teglab/pipeline/examples``, served by the API):
``outline_fig-3-10_system-D.csv`` - the Outline of All Parameters of system D exactly as shown in
thesis Fig. 3.10 (IDs, names, values, units);
``design-points_A-series_QT.csv`` - systems A/B/C written as a Workbench design-point table from
numbers printed in the thesis (see the header of the file for the source of every value);
``design-points_A0-TT_table-4-4.csv`` - case A0-TT from thesis Tables 4.2-4.4.
Nodal fields are synthetic: a seven-body unit cell with a known piecewise-linear temperature profile
(below), and the physically consistent cell of ``teglab.pipeline.synthetic`` for end-to-end tests.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from teglab.pipeline.schemas import CaseCondition, load_profile

DATA = Path(__file__).parents[2] / "src" / "teglab" / "pipeline" / "examples"

QT_THESIS = CaseCondition(bc="QT", q_hot_w=0.3152, t_cold_face_c=20.0)
TT_THESIS = CaseCondition(bc="TT", t_hot_face_c=100.0, t_cold_face_c=20.0)
FULL_LOAD_D = CaseCondition(bc="QT", q_hot_w=0.3152, t_cold_face_c=20.0, current_a=0.8)


@pytest.fixture(scope="session")
def profile():
    return load_profile("thesis-2026")


@pytest.fixture(scope="session")
def fig310() -> tuple[str, bytes]:
    p = DATA / "outline_fig-3-10_system-D.csv"
    return p.name, p.read_bytes()


@pytest.fixture(scope="session")
def aseries() -> tuple[str, bytes]:
    p = DATA / "design-points_A-series_QT.csv"
    return p.name, p.read_bytes()


@pytest.fixture(scope="session")
def a0tt() -> tuple[str, bytes]:
    p = DATA / "design-points_A0-TT_table-4-4.csv"
    return p.name, p.read_bytes()


# --------------------------------------------------------------------------- synthetic nodal model
# hot face at the top (y = 3.4 mm); drops [K] chosen so every layer and contact is distinguishable
STACK = {
    "ceramic_hot": ((-0.3, 4.1), (-0.3, 1.7), (2.6, 3.4)),
    "cu_hot": ((0.0, 3.8), (0.0, 1.4), (2.4, 2.6)),
    "leg_p": ((0.0, 1.4), (0.0, 1.4), (1.0, 2.4)),
    "leg_n": ((2.4, 3.8), (0.0, 1.4), (1.0, 2.4)),
    "cu_cold_p": ((0.0, 1.4), (0.0, 1.4), (0.8, 1.0)),
    "cu_cold_n": ((2.4, 3.8), (0.0, 1.4), (0.8, 1.0)),
    "ceramic_cold": ((-0.3, 4.1), (-0.3, 1.7), (0.0, 0.8)),
}
# temperature at the (top, bottom) face of each body
FACE_T = {
    "ceramic_hot": (100.0, 98.80),
    "cu_hot": (98.80, 98.76),
    "leg_p": (97.96, 22.96),  # 0.80 K contact jump below the hot copper
    "leg_n": (97.96, 22.96),
    "cu_cold_p": (22.16, 22.12),  # 0.80 K contact jump below the legs
    "cu_cold_n": (22.16, 22.12),
    "ceramic_cold": (22.12, 21.12),
}
SPACING = {"ceramic_hot": 9, "cu_hot": 13, "leg_p": 8, "leg_n": 11, "cu_cold_p": 6, "cu_cold_n": 7, "ceramic_cold": 10}


def body_nodes(tag: str, graded: bool = False) -> pd.DataFrame:
    (x0, x1), (z0, z1), (y0, y1) = STACK[tag]
    n = SPACING[tag]
    s = np.linspace(0.0, 1.0, n) ** (2.0 if graded else 1.0)
    x = x0 + (x1 - x0) * s
    z = np.linspace(z0, z1, n)
    y = np.linspace(y0, y1, 4)
    X, Y, Z = np.meshgrid(x, y, z, indexing="ij")
    t_top, t_bottom = FACE_T[tag]
    T = t_bottom + (t_top - t_bottom) * (Y - y0) / (y1 - y0)
    base = 10_000 * (list(STACK).index(tag) + 1)
    return pd.DataFrame(
        {
            "node_id": pd.array(base + np.arange(X.size), dtype="Int64"),
            "x_mm": X.ravel(),
            "y_mm": Y.ravel(),
            "z_mm": Z.ravel(),
            "temperature_c": T.ravel(),
        }
    )


@pytest.fixture()
def nodal_bodies() -> dict[str, pd.DataFrame]:
    return {tag: body_nodes(tag, graded=(tag == "leg_n")) for tag in STACK}


def mechanical_export(frame: pd.DataFrame, unit: str = "m") -> bytes:
    """Write nodes the way Mechanical's 'Export Text File' does (tab separated, Windows code page)."""
    scale = 1e-3 if unit == "m" else 1.0
    lines = [f"Node Number\tX Location ({unit})\tY Location ({unit})\tZ Location ({unit})\tTemperature (°C)"]
    for r in frame.itertuples(index=False):
        lines.append(
            f"{r.node_id}\t{r.x_mm * scale:.9g}\t{r.y_mm * scale:.9g}\t{r.z_mm * scale:.9g}\t{r.temperature_c:.6f}"
        )
    return ("\r\n".join(lines) + "\r\n").encode("cp1252")
