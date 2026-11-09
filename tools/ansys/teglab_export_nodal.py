# -*- coding: ascii -*-
"""teglab nodal export for ANSYS Mechanical (Thermal-Electric analyses).

For every analysis of the open Mechanical model, and every body that is not suppressed, this script
creates one result object per quantity scoped to that body, evaluates them and writes Mechanical's own
"Export Text File" as <body>__<quantity>.txt.  Optionally it also saves contour pictures of the whole
model in standard views, with the calibration of the camera it set.  A manifest.json, a log and a zip of
the folder complete the export: upload the zip in teglab (ANSYS pipeline > Nodal fields).

Run it
------
* Inside Mechanical: Automation > Scripting > open this file > Run (or paste it into the console).
* For every system of a Workbench project: run teglab_export_all_systems.wbjn (File > Scripting >
  Run Script File...), which sends this script to each Mechanical model.

One-time setting (the script checks it and stops with a message if it is off):
    Mechanical > File > Options > Export > Text File Export > Include Node Location = Yes

The script is plain IronPython 2.7 / Python 3 (no f-strings, ASCII only) and resolves every Mechanical
name defensively, so it also runs in PyMechanical.  It never modifies the analysis settings; the result
objects it adds are deleted again at the end (CONFIG["cleanup"]).
"""

import json
import os
import re
import time
import zipfile

VERSION = "0.4.0"

CONFIG = {
    # None = <project>_files/user_files/teglab_export/<system or model name>
    "output_dir": None,
    # quantities to export per body (keys of QUANTITIES)
    "quantities": ["temperature", "voltage", "current_density", "heat_flux", "joule_heat"],
    # contour pictures of the whole model (not needed for the numbers, only for the overlay)
    "images": True,
    "image_views": ["front"],
    "image_quantities": ["temperature", "voltage"],
    "image_size": [1600, 1000],
    # fraction of free space around the model in the pictures
    "image_margin": 0.08,
    "zip": True,
    "cleanup": True,
    # solve analyses that are not solved yet (False = stop with a message instead)
    "solve_if_needed": False,
}

# key -> (teglab quantity, Solution method that adds the result)
QUANTITIES = {
    "temperature": ("temperature_c", "AddTemperature"),
    "voltage": ("voltage_v", "AddElectricVoltage"),
    "current_density": ("current_density_a_m2", "AddTotalCurrentDensity"),
    "heat_flux": ("heat_flux_w_m2", "AddTotalHeatFlux"),
    "joule_heat": ("joule_heat_w_m3", "AddJouleHeat"),
}

# Standard views with +Y up: (horizontal axis, sign), (vertical axis, sign).
# Must match teglab.pipeline.nodal.VIEW_AXES - the overlay projects the nodes the same way.
VIEW_AXES = {
    "front": (("x", 1), ("y", 1)),
    "back": (("x", -1), ("y", 1)),
    "right": (("z", -1), ("y", 1)),
    "left": (("z", 1), ("y", 1)),
    "top": (("x", 1), ("z", -1)),
    "bottom": (("x", 1), ("z", 1)),
}
VIEW_ENUM = {"front": "Front", "back": "Back", "right": "Right", "left": "Left", "top": "Top", "bottom": "Bottom"}
LENGTH_TO_MM = {"m": 1000.0, "mm": 1.0, "cm": 10.0, "um": 0.001, "micron": 0.001, "in": 25.4, "ft": 304.8}
AXIS_INDEX = {"x": 0, "y": 1, "z": 2}

NO_LOCATION_HELP = (
    "The text export has no node locations. In Mechanical set File > Options > Export > "
    "Text File Export > Include Node Location = Yes, then run the script again."
)


# ----------------------------------------------------------------------------------- name lookup
def _lookup(name, module_path=None):
    """A Mechanical global (ExtAPI, enums, Point, ...) - from the script scope, builtins or a .NET namespace."""
    scope = globals()
    if name in scope:
        return scope[name]
    try:
        import __builtin__ as builtins  # IronPython 2.7
    except ImportError:
        import builtins
    if hasattr(builtins, name):
        return getattr(builtins, name)
    if module_path:
        module = __import__(module_path, globals(), locals(), [name])
        return getattr(module, name)
    raise NameError(name)


def _setting(name, default=None):
    """Values injected by the Workbench journal (TEGLAB_SYSTEM, TEGLAB_OUTPUT_DIR, TEGLAB_CONFIG)."""
    try:
        return _lookup(name)
    except NameError:
        return default


class Log(object):
    def __init__(self):
        self.lines = []

    def __call__(self, text):
        line = time.strftime("%H:%M:%S") + "  " + text
        self.lines.append(line)
        print(line)

    def save(self, folder):
        with open(os.path.join(folder, "teglab_export.log"), "w") as fh:
            fh.write("\n".join(self.lines) + "\n")


# ------------------------------------------------------------------------------------------ helpers
def tag(name):
    """Body name -> file-safe tag ('Leg P' -> 'leg_p')."""
    return re.sub(r"[^a-z0-9]+", "_", (name or "").lower()).strip("_") or "body"


def unique_tags(names):
    out, seen = [], {}
    for name in names:
        base = tag(name)
        seen[base] = seen.get(base, 0) + 1
        out.append(base if seen[base] == 1 else base + "_" + str(seen[base]))
    return out


def read_first_line(path):
    with open(path, "rb") as fh:
        head = fh.read(4096)
    if head[:2] in (b"\xff\xfe", b"\xfe\xff"):
        text = head.decode("utf-16", "ignore")
    else:
        text = head.decode("latin-1")
    return text.splitlines()[0] if text else ""


def read_nodes_mm(path):
    """(x, y, z) in mm of every row of a Mechanical text export (for the picture window)."""
    with open(path, "rb") as fh:
        raw = fh.read()
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        text = raw.decode("utf-16", "ignore")
    else:
        text = raw.decode("latin-1")
    lines = [ln for ln in text.splitlines() if ln.strip()]
    header = [h.strip() for h in lines[0].split("\t")]
    cols, scale = [], []
    for axis in ("x", "y", "z"):
        for k, h in enumerate(header):
            m = re.match(r"^" + axis + r" location\s*\((.*)\)\s*$", h.lower())
            if m:
                cols.append(k)
                scale.append(LENGTH_TO_MM.get(m.group(1).strip().replace("\xb5", "u"), 1.0))
                break
    if len(cols) != 3:
        return []
    out = []
    for ln in lines[1:]:
        parts = ln.split("\t")
        try:
            out.append(tuple(float(parts[c].replace(",", ".")) * s for c, s in zip(cols, scale)))
        except (ValueError, IndexError):
            continue
    return out


def view_window(points, view, aspect, margin):
    """Center (3D, mm) and model window (h0, h1, v0, v1) of a view that fits all points with the image aspect."""
    lo = [min(p[i] for p in points) for i in range(3)]
    hi = [max(p[i] for p in points) for i in range(3)]
    center = [(a + b) / 2.0 for a, b in zip(lo, hi)]
    (h_axis, h_sign), (v_axis, v_sign) = VIEW_AXES[view]
    hi_, vi_ = AXIS_INDEX[h_axis], AXIS_INDEX[v_axis]
    width = (hi[hi_] - lo[hi_]) * (1.0 + 2.0 * margin)
    height = (hi[vi_] - lo[vi_]) * (1.0 + 2.0 * margin)
    width, height = max(width, 1e-9), max(height, 1e-9)
    if width / height > aspect:
        height = width / aspect
    else:
        width = height * aspect
    hc, vc = h_sign * center[hi_], v_sign * center[vi_]
    return center, (hc - width / 2.0, hc + width / 2.0, vc - height / 2.0, vc + height / 2.0)


def calibration(window, size):
    """teglab ImageCalibration: the picture spans exactly the model window."""
    h0, h1, v0, v1 = window
    w, h = size
    return {
        "width_px": int(w),
        "height_px": int(h),
        "p1_px": [0.0, float(h)],
        "p1_model": [h0, v0],
        "p2_px": [float(w), 0.0],
        "p2_model": [h1, v1],
    }


def zip_folder(folder, zip_path):
    try:
        zf = zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED)
    except (RuntimeError, ImportError):  # no zlib in this interpreter
        zf = zipfile.ZipFile(zip_path, "w", zipfile.ZIP_STORED)
    base = os.path.basename(folder.rstrip("/\\"))
    try:
        for name in sorted(os.listdir(folder)):
            zf.write(os.path.join(folder, name), base + "/" + name)
    finally:
        zf.close()


def default_output_root(ext, analysis):
    try:
        import wbjn  # available inside Mechanical when it was opened from Workbench

        return os.path.join(wbjn.ExecuteCommand(ext, "returnValue(GetUserFilesDirectory())"), "teglab_export")
    except Exception:
        return os.path.join(str(analysis.WorkingDir), "teglab_export")


def export_text(result, path):
    try:
        result.ExportToTextFile(path)
    except TypeError:  # older signature: (showDialog, path)
        result.ExportToTextFile(False, path)


# ------------------------------------------------------------------------------------ the export
def export_analysis(ext, analysis, out_dir, config, log, system_title):
    solution = analysis.Solution
    status = str(getattr(solution, "Status", "Done"))
    if "Done" not in status:
        if not config["solve_if_needed"]:
            raise RuntimeError(analysis.Name + ": not solved (status " + status + "); solve it or set solve_if_needed")
        log(analysis.Name + ": solving ...")
        solution.Solve(True)

    category = _lookup("DataModelObjectCategory", "Ansys.Mechanical.DataModel.Enums")
    selection_type = _lookup("SelectionTypeEnum", "Ansys.ACT.Interfaces.Common")
    bodies = [b for b in ext.DataModel.Project.Model.Geometry.GetChildren(category.Body, True) if not b.Suppressed]
    if not bodies:
        raise RuntimeError("no active bodies in the model")
    tags = unique_tags([b.Name for b in bodies])
    if not os.path.isdir(out_dir):
        os.makedirs(out_dir)
    log(analysis.Name + ": " + str(len(bodies)) + " bodies -> " + out_dir)

    created, planned = [], []
    try:
        for body, body_tag in zip(bodies, tags):
            selection = ext.SelectionManager.CreateSelectionInfo(selection_type.GeometryEntities)
            selection.Ids = [body.GetGeoBody().Id]
            for key in config["quantities"]:
                canonical, method = QUANTITIES[key]
                try:
                    result = getattr(solution, method)()
                except Exception as exc:
                    log("  " + key + ": not available in this analysis (" + str(exc) + ")")
                    continue
                result.Location = selection
                result.Name = "teglab " + body_tag + " " + key
                created.append(result)
                planned.append((result, body_tag, key, canonical))
        solution.EvaluateAllResults()

        files, first = [], True
        for result, body_tag, key, canonical in planned:
            name = body_tag + "__" + key + ".txt"
            path = os.path.join(out_dir, name)
            try:
                export_text(result, path)
            except Exception as exc:
                log("  " + name + ": export failed (" + str(exc) + ")")
                continue
            if first:
                if "location" not in read_first_line(path).lower():
                    raise RuntimeError(NO_LOCATION_HELP)
                first = False
            files.append({"file": name, "body": body_tag, "quantity": canonical})
        log("  " + str(len(files)) + " nodal files written")

        images = []
        if config["images"] and files:
            images = export_images(ext, solution, out_dir, files, config, log, created)
    finally:
        if config["cleanup"]:
            for obj in created:
                try:
                    obj.Delete()
                except Exception:
                    pass

    manifest = {
        "generator": "teglab_export_nodal.py " + VERSION,
        "system": system_title or analysis.Name,
        "analysis": analysis.Name,
        "axis": None,
        "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "files": files,
        "images": images,
    }
    with open(os.path.join(out_dir, "manifest.json"), "w") as fh:
        json.dump(manifest, fh, indent=2)
    return manifest


def export_images(ext, solution, out_dir, files, config, log, created):
    """Contour pictures of the whole model; the camera is set so each picture spans a known window."""
    try:
        enums = "Ansys.Mechanical.DataModel.Enums"
        capture = _lookup("GraphicsCaptureType", enums)
        image_format = _lookup("GraphicsImageExportFormat", enums)
        background = _lookup("GraphicsBackgroundType", enums)
        orientation = _lookup("ViewOrientationType", enums)
        try:
            ansys = _lookup("Ansys")
            settings = ansys.Mechanical.Graphics.GraphicsImageExportSettings()
        except NameError:
            graphics_ns = __import__("Ansys.Mechanical.Graphics", globals(), locals(), ["GraphicsImageExportSettings"])
            settings = graphics_ns.GraphicsImageExportSettings()
    except Exception as exc:
        log("  pictures skipped: graphics API not available (" + str(exc) + ")")
        return []
    width, height = int(config["image_size"][0]), int(config["image_size"][1])
    settings.Width = width
    settings.Height = height
    settings.Capture = capture.ImageOnly
    settings.Background = background.White
    settings.CurrentGraphicsDisplay = False

    points, seen = [], set()
    for f in files:  # one file per body: the window has to contain the whole model
        if f["body"] not in seen:
            seen.add(f["body"])
            points.extend(read_nodes_mm(os.path.join(out_dir, f["file"])))
    try:
        point_cls = _lookup("Point", "Ansys.Mechanical.Graphics")
        quantity_cls = _lookup("Quantity", "Ansys.Core.Units")
    except Exception:
        point_cls = quantity_cls = None

    images = []
    camera = ext.Graphics.Camera
    for key in config["image_quantities"]:
        canonical, method = QUANTITIES[key]
        try:
            result = getattr(solution, method)()  # default scoping: all bodies
        except Exception:
            continue
        result.Name = "teglab picture " + key
        created.append(result)
        solution.EvaluateAllResults()
        result.Activate()
        for view in config["image_views"]:
            name = view + "__" + key + ".png"
            camera.SetSpecificViewOrientation(getattr(orientation, VIEW_ENUM[view]))
            cal = None
            if points and point_cls is not None and quantity_cls is not None:
                try:
                    center, window = view_window(points, view, float(width) / height, config["image_margin"])
                    camera.FocalPoint = point_cls(center, "mm")
                    camera.SceneWidth = quantity_cls(window[1] - window[0], "mm")
                    camera.SceneHeight = quantity_cls(window[3] - window[2], "mm")
                    cal = calibration(window, (width, height))
                except Exception as exc:
                    log("  " + name + ": camera not set (" + str(exc) + "), picture is uncalibrated")
                    camera.SetFit()
            else:
                camera.SetFit()
            try:
                ext.Graphics.ExportImage(os.path.join(out_dir, name), image_format.PNG, settings)
            except Exception as exc:
                log("  " + name + ": picture failed (" + str(exc) + ")")
                continue
            entry = {"file": name, "view": view, "quantity": canonical, "calibration_source": "camera"}
            if cal is not None:
                entry["calibration"] = cal
            images.append(entry)
    log("  " + str(len(images)) + " pictures written")
    return images


def main():
    log = Log()
    ext = _lookup("ExtAPI")
    config = dict(CONFIG)
    config.update(_setting("TEGLAB_CONFIG", {}) or {})
    system_title = _setting("TEGLAB_SYSTEM")
    analyses = list(ext.DataModel.Project.Model.Analyses)
    if not analyses:
        raise RuntimeError("the model has no analysis")
    if system_title:  # a Workbench system may share its model with others: prefer the matching analysis
        matching = [a for a in analyses if str(getattr(a, "SystemCaption", "")) == system_title]
        analyses = matching or analyses
    root = _setting("TEGLAB_OUTPUT_DIR") or config["output_dir"]
    written = []
    for analysis in analyses:
        base = root or os.path.join(default_output_root(ext, analysis), tag(system_title or analysis.Name))
        out_dir = base if len(analyses) == 1 else os.path.join(base, tag(analysis.Name))
        try:
            try:
                export_analysis(ext, analysis, out_dir, config, log, system_title)
            except Exception as exc:
                log("ERROR: " + str(exc))
                raise
            if config["zip"]:
                zip_path = out_dir.rstrip("/\\") + ".zip"
                log.save(out_dir)
                zip_folder(out_dir, zip_path)
                log("  upload to teglab: " + zip_path)
                written.append(zip_path)
            else:
                written.append(out_dir)
        finally:
            if os.path.isdir(out_dir):
                log.save(out_dir)
    return written


if not _setting("TEGLAB_NO_AUTORUN", False):
    main()
