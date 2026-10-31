"""Nodal-field reductions on a synthetic unit cell with a known temperature profile, and PNG calibration."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from teglab.pipeline.fields import Layer, face_stat, interface_jump, layer_profile, passive_active_split, vertex_weights
from teglab.pipeline.overlay import ImageCalibration, pixel_to_model, plotly_layout_image
from teglab.pipeline.readers import read_source

from .conftest import body_nodes, mechanical_export

LAYERS = [
    Layer("ceramic hot", ("ceramic_hot",)),
    Layer("Cu hot", ("cu_hot",)),
    Layer("legs", ("leg_p", "leg_n"), active=True),
    Layer("Cu cold", ("cu_cold_p", "cu_cold_n")),
    Layer("ceramic cold", ("ceramic_cold",)),
]


def test_area_weights_are_exact_for_linear_fields_on_graded_meshes():
    s = np.linspace(0.0, 1.0, 12) ** 3  # strongly graded towards x = 0
    x, z = np.meshgrid(1.4 * s, np.linspace(0.0, 1.4, 5), indexing="ij")
    nodes = pd.DataFrame({"x_mm": x.ravel(), "y_mm": 2.4, "z_mm": z.ravel(), "t": 20.0 + x.ravel()})
    stat = face_stat(nodes, "t", axis="y")
    assert stat.mean == pytest.approx(20.7)  # value at the face centre
    assert stat.area_mm2 == pytest.approx(1.96)
    assert face_stat(nodes, "t", axis="y", weighted=False).mean < 20.5  # a nodal mean is biased
    assert vertex_weights(np.array([0.0, 1.0]), np.array([0.0, 0.0])).tolist() == [1.0, 1.0]


def test_contact_jump_between_non_matching_meshes(nodal_bodies):
    jump = interface_jump(nodal_bodies["cu_hot"], nodal_bodies["leg_p"], "temperature_c", "y", 2.4)
    assert jump.mean == pytest.approx(0.80)
    assert jump.area_mm2 == pytest.approx(1.96)  # evaluated on the leg end, not on the larger bridge


def test_layer_profile_separates_ceramic_copper_contact_and_legs(nodal_bodies):
    prof = layer_profile(nodal_bodies, LAYERS)
    drops = dict(zip(prof["name"], prof["dt_k"], strict=True))
    assert drops["ceramic hot"] == pytest.approx(1.20)
    assert drops["ceramic hot | Cu hot"] == pytest.approx(0.0, abs=1e-9)  # bonded
    assert drops["Cu hot"] == pytest.approx(0.04)
    assert drops["Cu hot | legs"] == pytest.approx(0.80)  # contact
    assert drops["legs"] == pytest.approx(75.0)
    assert drops["legs | Cu cold"] == pytest.approx(0.80)
    assert drops["ceramic cold"] == pytest.approx(1.00)
    split = passive_active_split(prof)
    assert split["dt_te_k"] == pytest.approx(75.0)
    assert split["dt_hot_passive_k"] == pytest.approx(2.04)
    assert split["dt_cold_passive_k"] == pytest.approx(1.84)
    assert split["dt_contacts_k"] == pytest.approx(1.60)
    assert split["passive_share"] == pytest.approx(3.88 / 78.88)


def test_profile_from_mechanical_text_exports(nodal_bodies):
    bodies = {tag: read_source(mechanical_export(frame), f"T_{tag}.txt").frame for tag, frame in nodal_bodies.items()}
    split = passive_active_split(layer_profile(bodies, LAYERS))
    assert split["dt_te_k"] == pytest.approx(75.0, abs=1e-5)


def test_unaveraged_nodes_are_averaged_per_node():
    leg = body_nodes("leg_p")
    duplicated = pd.concat([leg, leg.assign(temperature_c=leg["temperature_c"] + 0.2)])
    cu = body_nodes("cu_hot")
    jump = interface_jump(cu, duplicated, "temperature_c", "y", 2.4)
    assert jump.mean == pytest.approx(0.70)


def test_image_calibration_round_trip():
    cal = ImageCalibration(
        width_px=900, height_px=319, p1_px=(100, 250), p1_model=(0.0, 0.0), p2_px=(800, 50), p2_model=(3.8, 3.4)
    )
    x, y = pixel_to_model(cal, [100, 800, 450], [250, 50, 150])
    assert x.tolist() == pytest.approx([0.0, 3.8, 1.9]) and y.tolist() == pytest.approx([0.0, 3.4, 1.7])
    img = plotly_layout_image(cal, "data:image/png;base64,AAAA")
    assert img["x"] == pytest.approx(-100 * 3.8 / 700) and img["sizex"] == pytest.approx(900 * 3.8 / 700)
    assert img["y"] == pytest.approx(250 * 3.4 / 200) and img["sizey"] == pytest.approx(319 * 3.4 / 200)
    assert img["layer"] == "below" and img["sizing"] == "stretch"
    with pytest.raises(ValueError):
        ImageCalibration(width_px=10, height_px=10, p1_px=(1, 1), p1_model=(0, 0), p2_px=(1, 5), p2_model=(1, 1))
