"""tools/ansys/teglab_export_nodal.py against a stand-in of the Mechanical scripting API.

The stand-in answers the calls the script makes (bodies, selections, result objects, text export, camera,
picture export) from the synthetic unit cell, so the whole chain - script → folder → zip → upload →
contact checks → overlay placement - runs in CI.  It checks the script's logic and its agreement with the
backend; it cannot prove that a given Mechanical version exposes exactly these calls (see tools/ansys/README).
"""

from __future__ import annotations

import ast
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from teglab.pipeline import synthetic
from teglab.pipeline.nodal import VIEW_AXES, build_dataset, evaluate_profile
from teglab.pipeline.overlay import placement
from teglab.pipeline.schemas import ContactProfile, ImageCalibration, ProfileRequest

TOOLS = Path(__file__).parents[2] / "tools" / "ansys"
SCRIPT = TOOLS / "teglab_export_nodal.py"
JOURNAL = TOOLS / "teglab_export_all_systems.wbjn"
pytestmark = pytest.mark.skipif(not SCRIPT.is_file(), reason="tools/ansys not present")

VIEWS = ("Front", "Back", "Right", "Left", "Top", "Bottom")
KEY_OF_METHOD = {
    "AddTemperature": "temperature_c",
    "AddElectricVoltage": "voltage_v",
    "AddTotalCurrentDensity": "current_density_a_m2",
    "AddTotalHeatFlux": "heat_flux_w_m2",
    "AddJouleHeat": "joule_heat_w_m3",
}
NAMES = {  # Mechanical body names as a user might have typed them
    "ceramic_hot": "Ceramic Hot",
    "cu_hot": "Cu hot",
    "leg_p": "Leg P",
    "leg_n": "Leg N",
    "cu_cold_p": "Cu cold P",
    "cu_cold_n": "Cu cold N",
    "ceramic_cold": "Ceramic cold",
}


class FakeMechanical:
    """Just enough of ExtAPI for the export script, answering from the synthetic cell."""

    def __init__(self, with_locations: bool = True):
        self.sol = synthetic.solve()
        self.frames = synthetic.bodies(self.sol, spacing_mm=0.2)
        self.with_locations = with_locations
        self.by_id = {100 + k: tag for k, tag in enumerate(self.frames)}
        self.created, self.deleted, self.evaluations = [], [], 0
        self.active = None
        self.camera = SimpleNamespace(
            orientation=None,
            FocalPoint=None,
            SceneWidth=None,
            SceneHeight=None,
            SetSpecificViewOrientation=lambda o: setattr(self.camera, "orientation", o),
            SetFit=lambda: None,
        )
        bodies = [
            SimpleNamespace(Name=NAMES[tag], Suppressed=False, GetGeoBody=lambda i=i: SimpleNamespace(Id=i))
            for i, tag in self.by_id.items()
        ]
        solution = SimpleNamespace(Status="Done", EvaluateAllResults=self._evaluate)
        for method, key in KEY_OF_METHOD.items():
            setattr(solution, method, lambda key=key: self._result(key))
        analysis = SimpleNamespace(Name="Thermal-Electric", Solution=solution, WorkingDir="/tmp", SystemCaption="")
        self.ext = SimpleNamespace(
            DataModel=SimpleNamespace(
                Project=SimpleNamespace(
                    Model=SimpleNamespace(
                        Analyses=[analysis],
                        Geometry=SimpleNamespace(GetChildren=lambda category, recursive: bodies),
                    )
                )
            ),
            SelectionManager=SimpleNamespace(CreateSelectionInfo=lambda kind: SimpleNamespace(Ids=[])),
            Graphics=SimpleNamespace(Camera=self.camera, ExportImage=self._export_image),
        )

    def _evaluate(self):
        self.evaluations += 1

    def _result(self, key):
        result = SimpleNamespace(Location=None, Name="", key=key)
        result.ExportToTextFile = lambda path: self._export_text(result, path)
        result.Activate = lambda: setattr(self, "active", result)
        result.Delete = lambda: self.deleted.append(result)
        self.created.append(result)
        return result

    def _export_text(self, result, path):
        tag = self.by_id[result.Location.Ids[0]]
        frame = self.frames[tag]
        if result.key not in frame:  # no electric degree of freedom in a ceramic
            raise RuntimeError("result not available for this body")
        data = synthetic.mechanical_text(frame, synthetic.NodalKey(result.key))
        if not self.with_locations:  # "Include Node Location = No"
            text = data.decode("cp1252").splitlines()
            data = "\r\n".join("\t".join([ln.split("\t")[0], ln.split("\t")[-1]]) for ln in text).encode("cp1252")
        Path(path).write_bytes(data)

    def _export_image(self, path, fmt, settings):
        assert self.camera.orientation == "Front" and fmt == "PNG"
        (fx, fy, _), w, h = self.camera.FocalPoint.coords, self.camera.SceneWidth.value, self.camera.SceneHeight.value
        window = (fx - w / 2, fx + w / 2, fy - h / 2, fy + h / 2)  # the camera shows exactly the scene box
        key = synthetic.NodalKey(self.active.key)
        png, _ = synthetic.render_front(self.sol, key, settings.Width, settings.Height, window=window)
        Path(path).write_bytes(png)
        self.last_window = window

    def namespace(self, out_dir: Path) -> dict:
        return {
            "__name__": "teglab_export",
            "ExtAPI": self.ext,
            "DataModelObjectCategory": SimpleNamespace(Body="Body"),
            "SelectionTypeEnum": SimpleNamespace(GeometryEntities="GeometryEntities"),
            "GraphicsCaptureType": SimpleNamespace(ImageOnly="ImageOnly"),
            "GraphicsImageExportFormat": SimpleNamespace(PNG="PNG"),
            "GraphicsBackgroundType": SimpleNamespace(White="White"),
            "ViewOrientationType": SimpleNamespace(**{v: v for v in VIEWS}),
            "Ansys": SimpleNamespace(
                Mechanical=SimpleNamespace(Graphics=SimpleNamespace(GraphicsImageExportSettings=SimpleNamespace))
            ),
            "Point": lambda coords, unit: SimpleNamespace(coords=list(coords), unit=unit),
            "Quantity": lambda value, unit: SimpleNamespace(value=value, unit=unit),
            "TEGLAB_NO_AUTORUN": True,
            "TEGLAB_SYSTEM": "Synthetic cell via fake Mechanical",
            "TEGLAB_OUTPUT_DIR": str(out_dir),
        }


def _load(ns: dict) -> dict:
    exec(compile(SCRIPT.read_text(encoding="ascii"), str(SCRIPT), "exec"), ns)
    return ns


def test_export_round_trip_through_the_pipeline(tmp_path):
    fake = FakeMechanical()
    ns = _load(fake.namespace(tmp_path / "export"))
    (zip_path,) = ns["main"]()
    manifest = json.loads((tmp_path / "export" / "manifest.json").read_text())
    assert manifest["system"] == "Synthetic cell via fake Mechanical"
    assert len(manifest["files"]) == 2 * 2 + 5 * 5  # ceramics: T and q only
    assert {f["body"] for f in manifest["files"]} == set(synthetic.CELL)
    assert len(fake.deleted) == len(fake.created) > 0  # every helper result object removed again
    with zipfile.ZipFile(zip_path) as zf:
        assert "export/manifest.json" in zf.namelist() and "export/teglab_export.log" in zf.namelist()

    ds = build_dataset([(Path(zip_path).name, Path(zip_path).read_bytes())])
    prof = evaluate_profile(ds, ProfileRequest(), ContactProfile())
    legs = [c for c in prof.contacts if c.at_active_layer]
    assert len(legs) == 4 and all(c.thermal_verdict == c.electrical_verdict == "as-specified" for c in legs)
    assert prof.split.dt_te_k == pytest.approx(fake.sol.dt_te_k, abs=1e-5)

    image = ds.images["front__temperature.png"].info
    assert image.calibration_source == "camera" and image.view == "front"
    p = placement(image.calibration)
    x0, x1, y0, y1 = fake.last_window
    assert (p.x, p.y, p.sizex, p.sizey) == pytest.approx((x0, y1, x1 - x0, y1 - y0))


def test_missing_node_locations_stop_the_export(tmp_path):
    fake = FakeMechanical(with_locations=False)
    ns = _load(fake.namespace(tmp_path / "export"))
    with pytest.raises(RuntimeError, match="Include Node Location = Yes"):
        ns["main"]()
    assert len(fake.deleted) == len(fake.created)  # cleaned up even when stopping
    assert "Include Node Location" in (tmp_path / "export" / "teglab_export.log").read_text()


def test_view_conventions_match_the_backend(tmp_path):
    ns = _load(FakeMechanical().namespace(tmp_path))
    assert {v: axes[:2] for v, axes in VIEW_AXES.items()} == {v: tuple(a) for v, a in ns["VIEW_AXES"].items()}
    points = [(0.0, 0.0, 0.0), (4.0, 2.0, 1.0)]
    center, window = ns["view_window"](points, "top", 2.0, 0.0)  # top: h = x, v = −z
    assert center == [2.0, 1.0, 0.5] and window == pytest.approx((0.0, 4.0, -1.5, 0.5))
    cal = ImageCalibration(**ns["calibration"](window, (800, 400)))
    assert placement(cal).mm_per_px == pytest.approx((4.0 / 800, -2.0 / 400))
    assert ns["unique_tags"](["Solid", "Solid", "Leg P"]) == ["solid", "solid_2", "leg_p"]


def test_scripts_are_plain_ascii_python():
    for path in (SCRIPT, JOURNAL):
        text = path.read_bytes().decode("ascii")  # IronPython-safe: no encoding surprises in SendCommand
        tree = ast.parse(text)
        assert not any(isinstance(node, ast.JoinedStr) for node in ast.walk(tree))  # no f-strings (IronPython 2.7)
