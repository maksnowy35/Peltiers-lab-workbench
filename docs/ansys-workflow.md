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

**Nodal fields per body.** For the layer-by-layer temperature profile and the contact checks
(TCC_eff = q/ΔT, ECC_eff = J/ΔV at every leg contact) run [`tools/ansys/teglab_export_nodal.py`](../tools/ansys)
in Mechanical, or the Workbench journal next to it for every system. It needs *File → Options → Export →
Text File Export → Include Node Location = Yes* and writes one zip per system; upload it on
**ANSYS pipeline → Nodal fields**. Exporting single results by hand works too (*Export → Export Text File*,
one file per body, named `<body>__<quantity>.txt`).

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
* implied internal resistance (α·ΔT_TE − U)/I vs the model with finite and with ideal contacts - flags
  systems in which the contact conductances did not take effect,
* hot-side passive resistance vs the same two model variants,
* leg conductance K = Q_h/ΔT_TE vs the model.

### 1D twin and QA gates

Each system is rebuilt from the project profile and solved by the 1D model: open circuit (U_oc, ΔT_TE, K),
the twin's own maximum-power point (P_max, I_opt) and every ANSYS design-point current (U, P, Q_h, Q_c).
The variant angle sets the frustum, the system's boundary condition the BC. The `twin` block of the profile
holds the passive-layer factors and the tolerances:

```json
"twin": {"hot_side_factor": 1.479, "cold_side_factor": 0.935, "r_el_extra_mohm": 0.0,
         "tol_u_oc": 0.02, "tol_dt_te": 0.03, "tol_p_max": 0.05, "tol_point": 0.05}
```

The thesis profile carries the factors of the A0-TT calibration, so case A0-TT is reproduced within 0.01 %.
Without them (all factors 1) the passive drop is smaller than in ANSYS and U_oc comes out ~0.7 % high.

| Gate | Pass | Meaning of a failure |
| --- | --- | --- |
| `energy-balance` | max \|Q_h − Q_c − U·I\|/Q_h ≤ 0.5 % (warn ≤ 2 %) | not converged or a probe on the wrong face |
| `open-circuit` | a design point at 0 A | U_oc, ΔT_TE and K are extrapolated |
| `p-max-bracketed` | the optimum lies between swept currents | the fitted P_max is an extrapolation |
| `upper-bound` | P_max ≤ α²Q²/(4K²R) (BC-QT) or α²ΔT²/(4R) (BC-TT) | inconsistent material data, BC or sign convention |
| `contacts` | implied contact resistance as specified | the contact conductances did not take effect |
| `seebeck` | \|U_oc/ΔT_TE − α_pn\|/α_pn ≤ 2 % | ΔT_TE read at the wrong faces, or α differs |
| `twin-*` | FEM vs 1D within the profile tolerances (warn up to 2×) | 3D effects or a modeling difference worth a look |

U and P at single points are compared on the scale of U_oc and P_max, because both pass through zero.
An unbracketed P_max is a point value, not the optimum, and is not compared with the twin's maximum.

### Calibrating the twin

*Calibrate the 1D twin* (page) or `--calibrate [LETTERS]` (CLI) fits the hot- and cold-side passive factors
and the series resistance to the open circuit and every design point of the reference variant (or the
listed systems), then reruns every twin with the fitted factors. The misfit is reported before and after,
on the fit systems and on the others (validation): factors fitted on A0 that also hold for A5 and A10 mean
the calibrated 1D model can stand in for FEM between runs. A0-TT from factors 1.0: RMS misfit 0.84 % → 0.010 %
(12 observations). The fit needs more observations than parameters, so a table with only an open circuit
for the reference variant is reported and left uncalibrated.

### Mesh convergence

Declare the meshes in the profile and give every system its mesh (system rules, the page, or `--mesh C=C1`):

```json
"meshes": {"C1": {"element_size_mm": 0.2}, "C2": {"element_size_mm": 0.1}}
```

Systems of the same variant and BC on different meshes are compared at every design-point current they share
(ΔT_TE, U, P, Q_c, T_max) and on U_oc, K and R. Two meshes use p = 2 and Fs = 3, three meshes with a constant
ratio the observed order and Fs = 1.25; r comes from node counts when both meshes have them, else from the
element sizes. Converged: GCI ≤ 1 %, check: ≤ 5 %; three meshes that do not converge monotonically are flagged
as oscillatory.

## 4 · Close the loop

1. Calibrate the 1D model on the reference system (Model & calibration page).
2. Search geometry on the Optimizer page (with the joint and material limits you will face in the lab).
3. *Export to ANSYS*: a design-point CSV and a Workbench journal (`.wbjn`, IronPython) that creates the
   design points in your Parameter Set. Map teglab keys (`a_hot_mm`, `a_cold_mm`, `height_mm`, `current_a`)
   to your parameter IDs first. Run it via *File → Scripting → Run Script File…* (save the project before).
   The journal uses the standard Workbench calls (`Parameters.GetParameter`, `CreateDesignPoint`,
   `SetParameterExpression`); check it on your Workbench version.
4. Solve, export the table again, import it - and compare the new FEM points with the model prediction.
