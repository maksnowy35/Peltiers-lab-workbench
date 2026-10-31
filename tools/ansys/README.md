# ANSYS export scripts

Two scripts get nodal results out of ANSYS Mechanical without clicking through every body and result:

| File | Runs in | Does |
|---|---|---|
| `teglab_export_nodal.py` | Mechanical (Automation → Scripting), or PyMechanical | exports one analysis system |
| `teglab_export_all_systems.wbjn` | Workbench (File → Scripting → Run Script File…) | sends the script to every system |

The output is a folder per system plus a zip of it. Upload the zip in teglab → **ANSYS pipeline** →
**Nodal fields**.

## Before the first run

In Mechanical: **File → Options → Export → Text File Export → Include Node Location = Yes**
(leave *Include Node Numbers = Yes*). The script reads back the header of its first export and stops with
this instruction if the locations are missing, so a wrong setting cannot produce an unusable export.

The systems have to be solved. Set `CONFIG["solve_if_needed"] = True` to let the script solve them instead.

## One system (inside Mechanical)

1. Open the solved system in Mechanical.
2. **Automation → Scripting**, open `teglab_export_nodal.py`, **Run**.
3. The log in the scripting pane ends with the path of the zip, by default
   `<project>_files/user_files/teglab_export/<system>.zip`.

## Every system (Workbench journal)

1. Copy `teglab_export_nodal.py` into the project's `user_files` folder (or keep it next to the journal).
2. Workbench: **File → Scripting → Run Script File…** → `teglab_export_all_systems.wbjn`.
3. Each system's Mechanical opens in the background, exports and closes again. A summary is written to
   `user_files/teglab_export/teglab_export_summary.txt`; per-system details are in each `teglab_export.log`.

Edit the constants at the top of the journal to export only some systems (`SYSTEM_FILTER`), change the
output folder or pass `SCRIPT_CONFIG` overrides (for example `{"images": False}`).

## What is written

```
teglab_export/dense_mesh_copy_c_10st/
  leg_p__temperature.txt        Mechanical "Export Text File", one per body and quantity
  leg_p__voltage.txt            (temperature, voltage, current_density, heat_flux, joule_heat)
  ...
  front__temperature.png        optional contour pictures of the whole model
  manifest.json                 bodies, quantities, pictures and their camera calibration
  teglab_export.log
teglab_export/dense_mesh_copy_c_10st.zip
```

* Body tags come from the Mechanical body names (`Leg P` → `leg_p`; repeated names get `_2`, `_3`).
  teglab groups the bodies into layers from their extents, so names do not have to follow any pattern.
* A body without a quantity (no electric degree of freedom in a ceramic) is logged and skipped.
* Units are whatever Mechanical displays; teglab reads them from the headers (m, mm, µm; W/m², mW/mm², …).
* The result objects the script adds are deleted at the end (`CONFIG["cleanup"]`); nothing else in the
  model is changed.

### Pictures and calibration

For each picture the script sets a standard view (`Front`, …), then points the camera at the center of the
exported nodes and sets the scene width and height to the model window with the picture's aspect ratio.
That window is written to `manifest.json` as the calibration, so teglab can place the picture under the
nodes without clicks. It relies on the exported image showing exactly the camera's scene, which depends on
the Mechanical version and a parallel (non-perspective) projection; teglab therefore labels these pictures
*"from the export camera - check that the outlines line up"*. If they do not, two clicks on the part's
corners recalibrate the picture in the overlay viewer. Pictures are optional: `CONFIG["images"] = False`
skips them and the numbers are unaffected.

## Mechanical calls used

The script only uses documented scripting calls, each resolved defensively (script scope, builtins, then
the .NET namespace):

* `ExtAPI.DataModel.Project.Model.Analyses`, `…Geometry.GetChildren(DataModelObjectCategory.Body, True)`,
  `body.GetGeoBody().Id`, `body.Suppressed`
* `ExtAPI.SelectionManager.CreateSelectionInfo(SelectionTypeEnum.GeometryEntities)` → `.Ids`
* `Solution.AddTemperature / AddElectricVoltage / AddTotalCurrentDensity / AddTotalHeatFlux / AddJouleHeat`,
  `result.Location`, `Solution.EvaluateAllResults()`, `result.ExportToTextFile(path)`, `result.Delete()`
* `Graphics.Camera.SetSpecificViewOrientation`, `.FocalPoint`, `.SceneWidth`, `.SceneHeight`, `.SetFit()`,
  `Graphics.ExportImage(path, GraphicsImageExportFormat.PNG, GraphicsImageExportSettings)`
* Workbench: `GetAllSystems()`, `system.DisplayText`, `system.GetContainer(ComponentName="Model")`,
  `model.SendCommand(Language="Python", Command=…)`, `model.Exit()`, `GetUserFilesDirectory()`

## How it was tested

`backend/tests/test_ansys_export_script.py` runs the real script against a stand-in of these calls that
answers from teglab's synthetic unit cell: script → folder → zip → upload → contact checks (TCC_eff and
ECC_eff recovered exactly) → overlay placement. It also checks the stop-and-explain path when node
locations are missing, the view conventions shared with the backend, and that both files are plain ASCII
Python without f-strings (IronPython 2.7).

It has **not** been run inside ANSYS here. On the first run in your version, check the log: a call that
does not exist in your release shows up there by name.
