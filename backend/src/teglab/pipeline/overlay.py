"""Place an ANSYS contour screenshot (PNG) under interactive Plotly traces.

A contour export is a picture of a *view*, so it needs a calibration: two reference points whose
pixel positions and model coordinates are known (for example two corners of the part in a front view).
From them a per-axis linear map pixel → model follows, and the image can be stretched onto the plot
axes with ``layout.images`` so nodal samples, probe positions or 1D-model profiles line up with it.

Only orthographic, axis-aligned views can be calibrated this way (Mechanical: View → Front/Top/…,
perspective off).  The image is a backdrop: quantitative values always come from the nodal data.
"""

from __future__ import annotations

import base64
import struct

import numpy as np
from numpy.typing import ArrayLike, NDArray

from .schemas import ImageCalibration, OverlayPlacement

__all__ = ["ImageCalibration", "data_uri", "pixel_to_model", "placement", "plotly_layout_image", "png_size"]

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def pixel_to_model(
    cal: ImageCalibration, px: ArrayLike, py: ArrayLike
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    sx, sy = cal.scale
    x = cal.p1_model[0] + (np.asarray(px, dtype=float) - cal.p1_px[0]) * sx
    y = cal.p1_model[1] + (np.asarray(py, dtype=float) - cal.p1_px[1]) * sy
    return x, y


def placement(cal: ImageCalibration) -> OverlayPlacement:
    """Where the whole picture lands on the model axes (left/top anchored, stretched)."""
    (x0, x1), (y_top, y_bottom) = pixel_to_model(cal, [0.0, cal.width_px], [0.0, cal.height_px])
    sx, sy = cal.scale
    return OverlayPlacement(
        x=float(min(x0, x1)),
        y=float(max(y_top, y_bottom)),
        sizex=float(abs(x1 - x0)),
        sizey=float(abs(y_top - y_bottom)),
        mm_per_px=(float(sx), float(sy)),
    )


def plotly_layout_image(cal: ImageCalibration, source: str, opacity: float = 1.0) -> dict:
    """``layout.images`` entry that stretches the picture over its calibrated model extent."""
    p = placement(cal).model_dump(exclude={"mm_per_px"})
    return {"source": source, "xref": "x", "yref": "y", **p, "opacity": opacity}


def png_size(data: bytes) -> tuple[int, int]:
    """(width, height) from the IHDR chunk; raises ValueError for anything that is not a PNG."""
    if len(data) < 24 or not data.startswith(PNG_SIGNATURE) or data[12:16] != b"IHDR":
        raise ValueError("not a PNG image")
    width, height = struct.unpack(">II", data[16:24])
    if width == 0 or height == 0:
        raise ValueError("PNG with zero size")
    return width, height


def data_uri(png: bytes) -> str:
    """Embed a PNG so the layout is self-contained (keep screenshots small: they travel in JSON)."""
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")
