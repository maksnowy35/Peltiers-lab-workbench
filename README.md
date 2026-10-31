# Peltier lab - thermoelectric pair workbench

**From ANSYS Thermal-Electric results to an optimized, buildable Peltier/TEG pair.**
Import design points from ANSYS Workbench, check them automatically, calibrate a fast 1D model on them,
search leg geometry (including constant-volume designs) and high-temperature materials, and plan the
soldering and the measurements in the lab - in one web app.

![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3776ab)
![FastAPI](https://img.shields.io/badge/FastAPI-backend-009688)
![Angular 22](https://img.shields.io/badge/Angular-22-dd0031)
![Plotly](https://img.shields.io/badge/charts-Plotly.js-3f4f75)
![License: MIT](https://img.shields.io/badge/license-MIT-blue)

![Analysis page: load curves of the thesis variants A0/A5/A10 with the 1D model overlaid](docs/img/analysis.png)

The project grew out of my master's thesis *Numerical analysis and optimization of Peltier thermoelectric
modules* (Warsaw University of Technology, Faculty of Power and Aeronautical Engineering, 2026) - a 3D
thermal-electric FEM study of a TEC1-12706 unit cell with prismatic and frustum-shaped legs. Peltier lab turns that
one-off study into a reusable tool; the thesis results are reproduced by the model and the comparison runs in the
test suite ([validation report](docs/validation.md)).

---

## What it does

| Page | What you get |
| --- | --- |
| **Data import** | Drop Workbench *Table of Design Points* CSVs, *Outline of Parameters*, Mechanical *Tabular Data* (paste) or lab CSV/XLSX. Units, decimal commas and sign conventions are normalized; repeated outputs of several analysis systems are split into cases automatically; every column gets a suggested physical meaning with a confidence score. |
| **ANSYS pipeline** | Raw Workbench exports through a JSON *project profile* that maps the exact parameter names (thesis Fig. 3.10) to canonical quantities: open circuit, internal resistance split into ohmic, contact and thermal-feedback parts, P<sub>max</sub> from the data and from exact/frozen analytic references, and a variant comparison with the gain attributed to K and R. **Nodal fields**: Mechanical exports per body are grouped into layers automatically; the page shows the temperature through the stack, the passive/active ΔT split and effective contact conductances **TCC<sub>eff</sub> = q/ΔT and ECC<sub>eff</sub> = J/ΔV** at every leg contact. **Overlay**: the nodes drawn over ANSYS contour pictures (calibrated by the export script, or by two clicks). |
| **Analysis** | U(I), P(I), η(I), hot-face temperature, ΔT and heat balance of all cases with the 1D model overlaid; fitted P<sub>max</sub> (local parabola / spline, not just the largest sample); variant comparison in the style of thesis Table 5.3; automatic consistency checks (energy balance, sampling around P<sub>max</sub>, effective Seebeck, implied internal and contact resistance, passive layers, conductance); Roache GCI for mesh studies. |
| **Model & calibration** | A steady-state 1D finite-volume model of one P-N pair with Joule, Peltier and Thomson heat, temperature-dependent properties, contact conductances, ceramics and electrodes, three thermal boundary conditions (fixed temperatures, fixed heat input, heat exchangers). Solves in milliseconds; least-squares calibration of lumped parameters against the imported ANSYS cases with a parity plot. |
| **Optimizer** | Taper angle × leg height sweeps under three explicit comparison constraints - **fixed cold base** (the thesis A-series), **fixed hot base** or **constant volume of thermoelectric material** - with limits for the narrow leg end, the solder joint and the material temperature. Pareto front of power vs tellurium volume, 2D maps, and **export of the chosen designs back to ANSYS** (design-point CSV + Workbench journal). |
| **Materials** | Library of Bi₂Te₃, Te-free MgAgSb / Mg₃(Sb,Bi)₂, PbTe, skutterudites, half-Heuslers and SiGe (representative curves, upload your own). zT, Seebeck, resistivity, conductivity and compatibility factor; maximum efficiency and power density vs hot-side temperature; **segmented legs** with optimized segment length; Thomson contribution of the active design. |
| **Lab planner** | Everything needed to build and measure the active design: leg count and material mass, Cu electrodes, ceramic plates, Ni barrier, solder paste mass, stencil and reflow profile (with alternatives checked against the joint temperature), heater and cooling capacity, clamp force, current set-points concentrated around I<sub>opt</sub>, shunt and voltmeter resolution, thermocouples, an uncertainty budget, a printable run sheet, a bill of materials and a measurement template that imports straight back into the app. |

<table>
  <tr>
    <td><img src="docs/img/import.png" alt="Import: a three-system Workbench table split into cases"></td>
    <td><img src="docs/img/model.png" alt="Model page: design editor, key figures and load curves"></td>
  </tr>
  <tr>
    <td><img src="docs/img/optimizer.png" alt="Optimizer: maximum power vs taper angle for three heights"></td>
    <td><img src="docs/img/materials.png" alt="Materials: maximum efficiency and power density vs hot-side temperature"></td>
  </tr>
  <tr>
    <td><img src="docs/img/lab.png" alt="Lab planner: expected results and the build form"></td>
    <td><img src="docs/img/overview.png" alt="Overview page with the active design and data health"></td>
  </tr>
</table>

---

## Quick start

### Everything in one container

```bash
docker compose up --build
```

Open <http://localhost:8000> (interactive REST documentation at <http://localhost:8000/docs>).
The example cases (thesis tables and synthetic sweeps) are loaded on first start; imported data persists in the
`teglab-data` volume.

### Development setup

Requirements: Python 3.11-3.13 (3.13 recommended), Node.js 24 LTS (Angular 22 needs ≥ 24.15 or ≥ 22.22.3),
Git - on Windows, Git for Windows provides the Git Bash terminal used below.

```bash
./dev.sh setup     # .venv + backend with pinned versions + frontend
./dev.sh test      # 134 tests, incl. the thesis validation and the ANSYS export script
./dev.sh api       # terminal 1 - API on http://127.0.0.1:8000 (docs at /docs)
./dev.sh ui        # terminal 2 - UI on http://localhost:4200 (/api is proxied to :8000)
./dev.sh check     # lint, tests, API contract and production build - before every push
```

The repository is ready for VS Code: recommended extensions, the `.venv` interpreter, the Testing panel,
tasks and debug configurations for the API, pytest, the pipeline CLI and the Angular app in Chrome.
Setup, versions and Windows notes: [docs/development.md](docs/development.md) · what to test and how:
[docs/testing.md](docs/testing.md).

| Environment variable | Meaning | Default |
| --- | --- | --- |
| `TEGLAB_DB` | SQLite file for imported cases and materials | `data/teglab.db` |
| `TEGLAB_STATIC` | directory of the built Angular app to serve on `/` | - |
| `TEGLAB_CORS` | comma-separated allowed origins | `http://localhost:4200` |
| `TEGLAB_SEED` | `0` = do not load the example cases | `1` |

---

## ANSYS pipeline (web page, REST API, command line)

`teglab.pipeline` reads Workbench outlines, design-point tables and Mechanical nodal exports, maps the
exact ANSYS parameter names through a JSON project profile, and derives per system: U_oc, ΔT_TE, the
active/passive temperature split, the internal resistance split into apparent, ohmic and contact parts,
P_max from the data and from analytic references, plus a variant comparison with the gain attributed to
conductance and resistance. Nodal exports add the layer-by-layer temperature profile and the effective
conductance of every contact - the direct test of whether a contact definition took effect.

![ANSYS pipeline page: temperature through the stack and contact checks of a nodal export](docs/img/pipeline.png)

```bash
cd backend
python -m teglab.pipeline inspect src/teglab/pipeline/examples/*.csv
python -m teglab.pipeline run src/teglab/pipeline/examples/design-points_A-series_QT.csv \
       src/teglab/pipeline/examples/outline_fig-3-10_system-D.csv --current 0.8 --system A=A0 --system B=A5 --system C=A10
```

| Endpoint (`/api/pipeline/…`) | Does |
| --- | --- |
| `GET profiles`, `GET profiles/{name}` | bundled project profiles |
| `POST inspect`, `POST run` | parameter tables → recognized parameters / full report (`settings` = `RunSettings` JSON) |
| `GET examples`, `GET examples/{name}` | thesis-derived tables and synthetic nodal exports to try everything |
| `POST nodal`, `POST nodal/demo` | upload Mechanical exports (text files, a zip, `manifest.json`, PNGs) → dataset with a proposed layer stack |
| `GET`/`DELETE nodal/{id}`, `GET nodal/{id}/images/{name}` | dataset info, removal, contour pictures |
| `POST nodal/{id}/profile` | temperature through the stack, ΔT split, TCC_eff / ECC_eff per contact with verdicts |
| `POST nodal/{id}/points` | nodes projected onto a standard view (the overlay's data layer) |
| `POST overlay` | two-point calibration → Plotly `layout.images` placement |

The Pydantic models are the contract: `backend/scripts/gen_ts_types.py` writes
`frontend/src/app/core/pipeline.models.ts` from them and CI fails when the TypeScript mirror is stale.
Design, data contracts and physics: [docs/architecture/ingestion-pipeline.md](docs/architecture/ingestion-pipeline.md).

## Working with ANSYS

1. In Workbench, export the *Table of Design Points* (right-click → *Export Table Data as CSV*) - or paste
   Mechanical *Tabular Data*.
2. Drop it on **Data import**. Check the suggested column meanings, name the cases and give each one its boundary
   condition and geometry (the importer pre-fills what it can read from the parameters).
3. **Analysis** runs the consistency checks; **Model & calibration** fits the 1D model to the cases.
4. Explore geometry and materials, then *Export to ANSYS*: the design-point CSV and a Workbench journal
   (`.wbjn`) create the new design points in your project. Solve, re-import, compare.

For nodal fields, [`tools/ansys`](tools/ansys) has a Mechanical script that exports every body and quantity
(plus calibrated contour pictures and a manifest) into one zip, and a Workbench journal that runs it for every
system of the project. Upload the zip on **ANSYS pipeline → Nodal fields**.

Probe naming conventions and details: [docs/ansys-workflow.md](docs/ansys-workflow.md).
Example files (thesis tables and synthetic sweeps) are available on the import and pipeline pages and in
[`backend/src/teglab/data/examples`](backend/src/teglab/data/examples) and
[`backend/src/teglab/pipeline/examples`](backend/src/teglab/pipeline/examples).

## The model

The 1D model is a surrogate of the 3D FEM model, not a replacement: a vertex-centered finite-volume
discretization of ∇·(κ∇T) + ρJ² − τJ·∇T = 0 along each leg with an exact integral for the tapered cross-section,
a 3D correction for frustum legs, Peltier heat at every junction (exactly energy-conservative), contact
conductances, ceramics and Cu electrodes, and Picard iteration for temperature-dependent properties.
Equations, assumptions and limits: [docs/model.md](docs/model.md).

## Validation against the thesis

Calibrated on the prismatic reference case A0-TT only (two passive-layer factors; the fitted series-resistance
correction is ≈ 0), the model reproduces the thesis tables and predicts the tapered variants without further
fitting:

| Quantity | Thesis (ANSYS) | Peltier lab |
| --- | --- | --- |
| A0-TT open-circuit voltage | 30.02 mV | 30.02 mV |
| A0-TT maximum power | 13.05 mW | 13.05 mW |
| A0-TT heat input at I = 0 / 0.8 A | 0.3152 / 0.4215 W | 0.3152 / 0.4215 W |
| A5 / A10 pair conductance K | 3.451 / 2.676 mW/K | 3.452 / 2.677 mW/K |
| A10-QT at 0.8 A: voltage, ΔT across the legs | 12.38 mV, 76.16 K | 12.41 mV, 76.14 K ¹ |

¹ with the contact settings that match the FEM data of that system - see the
[validation report](docs/validation.md) for the full tables, the mesh-convergence (GCI) check and the
discussion of contact resistance.

## Using the physics core from Python

The web app is optional - `teglab` is a plain Python package:

```python
from teglab.design import reference_design
from teglab.solver import build_model, max_power, solve

design = reference_design().with_geometry(a_hot_mm=0.98, a_cold_mm=1.4, height_mm=2.0)  # 6° taper
model = build_model(design)
oc = solve(model, 0.0)          # open circuit
mp = max_power(model, oc)       # bounded search on the current
print(f"P_max = {mp.power_w * 1e3:.2f} mW at {mp.current_a:.3f} A, T_hot = {oc.t_hot_ext_c:.0f} °C")
# P_max = 15.04 mW at 0.506 A, T_hot = 180 °C
```

## Tech stack

| Layer | Choice | Why |
| --- | --- | --- |
| Numerics | Python, NumPy, SciPy, pandas | The language of engineering scripting (and of PyAnsys). One operating point is a small linear system (0.2 ms with constant, ~1 ms with temperature-dependent properties); SciPy supplies Brent search, least squares and splines; the ingestion pipeline is vectorized with pandas. |
| API | FastAPI + Pydantic v2 | Typed request/response models validated at the boundary, auto-generated OpenAPI docs at `/docs`; the pipeline's models are mirrored to TypeScript by a generator checked in CI. |
| Storage | SQLite | Zero-setup persistence of imported cases and uploaded materials. |
| UI | Angular 22 (standalone components, signals, zoneless change detection) | A structured, strictly typed SPA with lazy-loaded pages and strict template type-checking. |
| Charts | Plotly.js (lazy-loaded) | Zoom, hover read-out and PNG export for scientific plots; every chart also has a table view (the overlay exports its nodes as CSV). Color palette validated for color-vision deficiency, light and dark themes. |
| Delivery | Docker (multi-stage), docker compose, GitHub Actions | One image serves UI + API (with a health check); CI runs lint and tests on Python 3.11-3.13, the API-contract check, a production Angular build and `docker compose up --build` with a smoke test. |

## Project structure

```text
backend/
  src/teglab/
    solver.py            1D finite-volume pair model, operating points
    materials.py         property curves (PCHIP), zT, compatibility, library
    design.py            design schema (geometry, contacts, passives, boundary conditions)
    ansys_io.py          Workbench / Mechanical / lab file parsing and column-role suggestions
    analysis.py          load curves, P_max fitting, consistency checks, GCI
    calibration.py       least-squares calibration against imported cases
    optimize.py          geometry sweeps, maps, Pareto front
    materials_study.py   efficiency maps, segmented legs, Thomson study
    lab.py               experiment and soldering planner
    ansys_export.py      design points + Workbench journal
    reference.py         numbers from the thesis (for tests and validation)
    pipeline/            ANSYS ingestion pipeline: readers, canonical frames, per-system physics, CLI,
                         nodal datasets (layer stack, contact checks), overlay, synthetic unit cell, examples
    api/                 FastAPI app and routes
  tests/                 pytest suite (physics, import, analysis, API, pipeline, thesis validation, export script)
  scripts/validate_thesis.py
  scripts/gen_ts_types.py  Pydantic → TypeScript contract generator
frontend/                Angular app (pages: home, import, pipeline, analysis, model, optimizer, materials, lab)
  src/app/core/pipeline.models.ts   generated API contract of the pipeline
tools/ansys/             Mechanical export script and Workbench journal for nodal results
e2e/                     Playwright browser tests of the running app
docs/                    model, validation, pipeline architecture, ANSYS workflow, development setup, test plan
dev.sh                   every developer command (setup, api, ui, test, lint, check, e2e, docker)
.vscode/                 recommended extensions, settings, tasks and debug configurations
```

## Limitations and ideas

* Steady state only; no thermomechanical stress (the narrow end of a strongly tapered leg must be checked in FEM).
* Built-in material curves are representative literature-level data, not measurements - upload your own.
* One P-N pair (module = n identical pairs in series); interconnect and edge effects of a full module are not modeled.
* Next steps: transient load steps, thermal-cycling fatigue of joints, direct PyAnsys (PyMechanical) coupling.

## License

[MIT](LICENSE) © 2026 Maksymilian Kruk
