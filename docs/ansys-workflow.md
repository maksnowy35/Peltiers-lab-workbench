# Working with ANSYS Workbench

## 1 · Export results

**Table of design points (recommended).** In the Project Schematic open *Parameter Set*, right-click the
*Table of Design Points* → *Export Table Data as CSV*. The file starts with comment lines listing the
parameters (`# P1 - Current Magnitude, P2 - …`), then `Name, P1, P2, …`, an optional `Units` row and one
row per design point. teglab reads the names from the comments, the units from the `Units` row, and
normalizes the sign of reaction probes.

**Several analysis systems in one table.** If systems A/B/C (e.g. variants A0/A5/A10) share one parameter
set, each output appears once per system. Repeated output names are split into separate cases automatically;
you only name them and set their boundary condition and geometry.

**Outline of parameters.** A single design point can also be exported from the *Outline of All
Parameters* (`ID, Parameter Name, Value, Unit`); upload several such files at once and they become rows.

**Mechanical tabular data.** Copy the *Tabular Data* of a result (Time, Minimum, Maximum, Average) and paste
it into the import page. If the current was ramped with the load-step time, set *Time → current factor*
(e.g. 0.8 A/s when t = 1 s corresponds to 0.8 A).

## 2 · Name probes so that they map automatically

| quantity | good parameter names |
| --- | --- |
| current | `Current Magnitude`, `I` |
| terminal voltage | `Voltage Probe …`, `U` (not the domain-average potential) |
| heat in / out | `Reaction Probe Hot …` / `Reaction Probe Cold …` (or just two reaction probes: sign decides) |
| hot / cold face temperature | `T_hot_face`, `T_cold_face` |
| leg-end temperatures | `T_leg_hot_avg`, `T_leg_cold_avg` |
| geometry inputs | `a_h`, `a_c`, `H`, `angle` |

Anything that is not recognized can be mapped by hand in the column table; the confidence badge shows how
sure the suggestion is.

## 3 · Checks you get for free

* energy balance Q_h − Q_c − P (criterion 0.1 %),
* linearity of U(I) under BC-TT,
* sampling density around the maximum-power point (with suggested extra currents),
* effective Seebeck coefficient U_oc/ΔT_TE vs the material,
* implied internal resistance (α·ΔT_TE − U)/I vs the model with finite and with ideal contacts – flags
  systems in which the contact conductances did not take effect,
* hot-side passive resistance vs the same two model variants,
* leg conductance K = Q_h/ΔT_TE vs the model.

## 4 · Close the loop

1. Calibrate the 1D model on the reference system (Model & calibration page).
2. Search geometry on the Optimizer page (with the joint and material limits you will face in the lab).
3. *Export to ANSYS*: a design-point CSV and a Workbench journal (`.wbjn`, IronPython) that creates the
   design points in your Parameter Set. Map teglab keys (`a_hot_mm`, `a_cold_mm`, `height_mm`, `current_a`)
   to your parameter IDs first. Run it via *File → Scripting → Run Script File…* (save the project before).
   The journal uses the standard Workbench calls (`Parameters.GetParameter`, `CreateDesignPoint`,
   `SetParameterExpression`); check it on your Workbench version.
4. Solve, export the table again, import it – and compare the new FEM points with the model prediction.
