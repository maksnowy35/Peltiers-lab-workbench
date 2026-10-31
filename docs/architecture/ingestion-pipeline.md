# Ingestion pipeline

`teglab.pipeline` turns raw ANSYS exports into validated, unit-safe tables and derives the quantities
that used to be computed by hand after every Workbench run: open-circuit voltage, the temperature
difference across the legs, the split between active and passive temperature drops, the internal
resistance with the contacts isolated, and the maximum power - with analytic references and a
comparison of geometric variants.

```bash
python -m teglab.pipeline inspect exports/*.csv                  # what is in these files?
python -m teglab.pipeline run design_points.csv outline.csv \
       --system A=A0 --system B=A5 --system C=A10                  # full analysis
```

The same runs are available at `POST /api/pipeline/inspect` and `POST /api/pipeline/run`
(multipart upload, typed JSON response, schema at `/docs`).

---

## 1 · Input data structures

The contracts are built on the ANSYS project of the thesis. Names are data, not code: they live in one
JSON profile ([`profiles/thesis-2026.json`](../../backend/src/teglab/pipeline/profiles/thesis-2026.json)),
so another project only needs another profile.

### 1.1 Analysis systems (thesis Fig. 3.9)

All systems share one Parameter Set, so a single design-point table holds every geometry.

| Letter | Workbench title | Variant | Mesh |
| --- | --- | --- | --- |
| A | `Thermal-Electric` | A0 (θ = 0°) | C1 (0.2 mm) |
| B | `Thermal-Electric B 5st` | A5 (θ = 5°) | C1 |
| C | `Thermal-Electric C 10st` | A10 (θ = 10°) | C1 |
| D | `Dense mesh copy C 10st` | A10 | C2 (0.1 mm) |

The profile maps titles to variants with ordered regular expressions (`^dense mesh copy` → A10/C2 before
`\b10\s*st\b` → A10/C1). Without a rule, the taper angle is parsed from `5st`, `10deg` or `10°`.

### 1.2 Output parameters (thesis Fig. 3.10, system D at full load)

| ID | Exact name | Unit as exported | Canonical key | Value | Meaning |
| --- | --- | --- | --- | --- | --- |
| P97 | `Reaction Probe 2 Heat` | `W` | `q_cold_w` | −0.30505 | heat through the cold plate (reaction: negative = leaving) |
| P99 | `Voltage Probe Electric Voltage` | `V` | `voltage_v` | 0.012385 | terminal voltage |
| P100 | `Total Current Density Average` | `A m^-2` | `j_avg_a_m2` | 8.7566E+05 | legs |
| P101 | `Total Current Density Maximum` | `A m^-2` | `j_max_a_m2` | 3.7328E+06 | unaveraged, edge-dominated |
| P102 | `T_max_global Maximum` | `C` | `t_max_c` | 99.628 | global maximum temperature |
| P103 | `Electric Voltage Average` | `V` | `v_avg_v` | 0.0061923 | domain average potential (diagnostic only) |
| P104 | `Joule Heat Average` | `W m^-3` | `joule_avg_w_m3` | 2.1012E+06 | legs |
| P105 | `Temperature Average` | `C` | `t_leg_hot_c` | 97.501 | hot metallization surfaces (leg hot ends) |
| P106 | `T_leg_cold_avg Average` | `C` | `t_leg_cold_c` | 21.314 | cold metallization surfaces (leg cold ends) |

Workbench names parameters `<object name> <property>`: `T_leg_cold_avg Average` is the *Average*
of a result object renamed `T_leg_cold_avg`. `Temperature Average` is an unrenamed temperature result;
it can only be interpreted with project knowledge (thesis Section 3.7.5), which is why the profile maps
exact names and never guesses from words like "temperature".

**Inputs and boundary conditions.** The input panel is not shown in the thesis. The current load is
matched by the Workbench default name (`Current Magnitude`, pattern, confidence 0.8). Under BC-QT the
heat input (0.3152 W) and the cold-face temperature (20 °C) are prescribed values rather than
parameters, so they come from the case condition of the profile and are reported as *imposed*.

### 1.3 Export layouts

| Layout | Where it comes from | What the reader keeps |
| --- | --- | --- |
| **Outline of All Parameters** | Parameter Set → outline pane copied to Excel/CSV | `ID, Parameter Name, Value, Unit`; system rows like `Dense mesh copy C 10st (D1)` give the exact ownership of every parameter |
| **Table of Design Points** | *Export Table Data as CSV* | `# P1 - Current Magnitude, …` definitions, `Name, P1, …` header, optional `Units` row, one row per design point; no system ownership |
| **Mechanical Export Text File** | right-click a result → Export | tab-separated `Node Number, X/Y/Z Location (unit), <Result> (unit)`; Windows code page (`°C` = 0xB0); repeated node numbers for unaveraged results |

P-IDs are project-global: when both an outline and a design-point table are uploaded, the outline fixes
the ownership of the same IDs in the table. Otherwise repeated output names are split into systems in
column order (reported as an issue, since ownership is then inferred).

### 1.4 Gaps worth closing in the ANSYS model

* **No probe on the heated ceramic face.** Under BC-QT the pipeline substitutes `T_max_global` at I = 0
  (at open circuit the global maximum sits on that face). A face probe makes the passive split exact.
* **Probes lump ceramic, copper and contact per side.** Nodal exports scoped to bodies resolve each
  layer and each contact separately (Section 5).
* **Sparse current sweep around the BC-QT optimum.** The pipeline reports when P_max is not bracketed
  and proposes currents around the analytic optimum (≈ 0.55 A for A0-A10).

---

## 2 · Architecture

```mermaid
flowchart LR
    F[uploaded files] --> S{sniff}
    S -->|outline| RO[OutlineReader]
    S -->|design points| RD[DesignPointReader]
    S -->|nodal text| RN[NodalReader]
    RO & RD --> RT[RawTable<br/>params + matrix]
    RN --> NT[NodalTable]
    P[(JSON profile)] --> M
    RT --> M[resolve names<br/>assign systems]
    M --> C[canonical frame<br/>melt · merge · affine map · pivot]
    C --> V[validate<br/>ColumnSpec]
    V --> X[extract<br/>per system]
    X --> A[analytic P_max]
    X --> K[compare variants]
    A & K --> R[PipelineReport<br/>numbers + issues]
    NT --> DS[dataset<br/>bodies · stack]
    DS --> FD[fields<br/>faces · jumps · contacts]
    DS --> PR[view projection]
    PNG[contour PNG] --> OV[calibration]
    FD --> LP[LayerProfile]
    PR & OV --> OVL[overlay]
    R & LP & OVL --> API[/api/pipeline/ → Angular]
```

```text
backend/src/teglab/pipeline/
├── keys.py          canonical quantity keys (unit in the name: current_a, t_leg_hot_c, j_max_a_m2 …)
├── schemas.py       Pydantic v2 contracts: parameters, systems, profile, conditions, results
├── profiles/        project profiles (exact ANSYS names, system rules, geometry, materials, BCs)
├── readers.py       adapters: outline, design-point CSV, Mechanical nodal text; format registry
├── frames.py        canonical frame builder, imposed boundary conditions, column validation
├── extract.py       per-system physics: open circuit, R_int split, thermal split, P_max fits
├── maxpower.py      analytic references: α²Q²/(4K²R), exact BC-QT optimum, α²ΔT²/(4R)
├── compare.py       variant ratios, gain attribution, cold-side contact signature
├── fields.py        nodal reductions: area-weighted faces, contact jumps and states, layer profile
├── nodal.py         datasets: ingestion, stack inference, contact checks, view projection, store
├── synthetic.py     physically consistent unit cell, Mechanical text writer, PNG renderer
├── overlay.py       PNG contour calibration → Plotly layout image placement
├── run.py           orchestration into a PipelineReport
├── examples/        thesis-derived parameter tables served by the API
└── __main__.py      command line (inspect / run)
backend/src/teglab/api/pipeline_routes.py   REST adapter
backend/scripts/gen_ts_types.py             Pydantic → TypeScript contract
backend/tests/pipeline/                     thesis-anchored and synthetic tests
tools/ansys/                                Mechanical export script, Workbench journal
```

| Pattern | Where | Why |
| --- | --- | --- |
| Ports and adapters | `readers.py` → `RawTable` / `NodalTable` | the physics never sees a file format; a PyDPF result-file adapter can be added without touching it |
| Strategy + registry | `READERS`, `sniff()` | format detection by confidence instead of `if extension == …` |
| Pipeline of pure stages | `run.py` | each stage is a function of the previous output: testable in isolation, reproducible from bytes + profile |
| Configuration as data | `ProjectProfile` (JSON) | exact names, system titles, geometry and BCs change per project, the code does not |
| Specification | `ColumnSpec`, `validate_frame` | declarative physical bounds, reported not raised |
| Notification | `Issue` records | a run never aborts on bad data; every problem sits next to the numbers it concerns |
| Units in names | `q_hot_w`, `t_leg_cold_c`, `x_mm` | a value cannot be read in the wrong unit |
| Strangler fig | legacy `ansys_io` | the UI import keeps working while its parsing moves to the pipeline readers |

---

## 3 · Canonical data and vectorization

**Canonical frame.** One row per (system, design point), one column per `QuantityKey` in canonical units
(A, V, W, °C, A/m², W/m³), plus derived columns: `dt_te_k`, `power_w`, `efficiency`, `load_resistance_ohm`,
`balance_w`, `balance_rel`.

**From a raw matrix to the frame - no Python loops over design points:**

1. `melt` the (design points × P-IDs) matrix to long form;
2. `merge` with the parameter catalog (P-ID → key, system, factor, offset, sign);
3. apply the affine unit map and the reaction sign in one expression: `(raw · factor + offset) · sign`;
4. broadcast shared inputs (the current) to the systems of the same table with a cross join;
5. `pivot_table` to the wide frame, then fill imposed boundary conditions and derive columns.

**Per-system statistics** are also vectorized:

* straight-line fits for every system at once from centered `groupby` sums (U_oc, apparent resistance, R²);
* the local parabola around the best power sample is a stack of 3×3 Vandermonde systems solved by one
  batched `np.linalg.solve`;
* the exact BC-QT optimum is a golden-section search on NumPy arrays (all designs in lockstep).

Parsing a 55 000-node Mechanical export (C2 mesh size, 2.4 MB) takes ≈ 0.3 s and an area-weighted face
average ≈ 13 ms in the test environment.

---

## 4 · Physics in the pipeline

### 4.1 Internal resistance: apparent, ohmic, contacts

A straight line through U(I) mixes two effects: the ohmic drop and the fall of ΔT_TE under current
(Peltier heat pumped through the passive layers). The pipeline separates them:

| Quantity | Definition | Thesis A0-TT / A10-QT |
| --- | --- | --- |
| R_apparent | −dU/dI of a straight line | 17.26 mΩ (linear source, R² = 1) - load-matching value, not purely ohmic |
| R_ohmic(I) | (α_eff · ΔT_TE(I) − U(I)) / I with α_eff = U_oc / ΔT_TE(0) | 22.61 mΩ for A10 (C1), 22.62 mΩ (C2) |
| R_legs | ρ·H/(a_h·a_c) · (1 + c3d·tan²θ) for both legs | 14.29 mΩ (A0), 22.41 mΩ (A10) |
| R_copper | Cu bridge centre to centre | 0.15 mΩ |
| R_contacts expected | 2/(ECC·A_h) + 2/(ECC·A_c) | 2.04 mΩ (A0), 3.46 mΩ (A10) |
| R_contacts isolated | R_ohmic − R_legs − R_copper | compared with the expected value → verdict |

The verdict is `active` when the isolated value is within ±35 % of the expected one, `inactive` when it is
within ±35 % of zero. When ΔT_TE is probed at several currents, R_apparent − R_ohmic gives the thermal
feedback term directly.

### 4.2 Active and passive temperature drops

At open circuit: ΔT_hot = T_hot_face − T_leg_hot, ΔT_cold = T_leg_cold − T_cold_face, passive share
= 1 − ΔT_TE/ΔT_imposed (A0-TT: 2.84 K + 2.09 K, 6.1-6.2 %), thermal resistances per side, and the
drop expected across the contacts, Q/(TCC·2A) = 0.80 K per side for A0.

**Cold-side signature.** Variants A0, A5 and A10 share the cold side, so their cold passive drop must
agree. When a variant differs from the reference by the expected cold-contact drop, the pipeline flags
that the contact conductance probably did not take effect in one of the systems.

### 4.3 Maximum power

| Reference | Formula | A0 / A5 / A10 at 0.3152 W |
| --- | --- | --- |
| frozen BC-QT (upper bound) | α²Q²/(4K²R) | 15.77 / 19.11 / 24.36 mW (thesis Table 5.2) |
| exact BC-QT, ideal legs | max over I of I·(α·ΔT(I) − I·R), ΔT(I) = (Q − α·I·T_c + ½·I²·R)/(K + α·I) | 8.27 / 9.89 / 12.34 mW at ≈ 0.55 A |
| BC-TT | α²ΔT²/(4R) | 17.9 mW for A0 at 80 K |
| from data | U_oc²/(4R) for a linear source, else a local parabola, flagged when not bracketed | 13.05 mW (A0-TT) |

The exact optimum agrees with the finite-volume model of `teglab.solver` (ideal passives, no contacts)
to 0.02 %, two independent implementations. Most of the gap between the frozen estimate and the
simulated BC-QT power comes from the collapse of ΔT under current, not from the passive layers.

**Gain attribution.** For the frozen relation the power ratio of two designs factorizes exactly:
P/P₀ = (α/α₀)²·(Q/Q₀)²·(K₀/K)²·(R₀/R). For A10 against A0: conductance ×2.39, resistance ×0.65,
net ×1.54 (+54 %, thesis Table 5.3); at fixed temperatures (BC-TT) only the resistance factor remains,
so the same taper loses 35 %.

---

## 5 · Nodal fields, contact checks and contour overlays

### 5.1 Datasets

A *dataset* is one analysis system exported body by body. `tools/ansys/teglab_export_nodal.py` writes
`<body>__<quantity>.txt` (Mechanical *Export Text File* with node numbers and locations), optional contour
PNGs and a `manifest.json`; the zip of that folder is the unit of upload (`POST /api/pipeline/nodal`).
Ingestion (`nodal.build_dataset`) is tolerant of hand-made exports:

* the body comes from the manifest, else from the `<body>__` file-name prefix, else from the file name;
  files without a prefix that share every node number are merged into one body (`T.txt` + `V.txt`);
* files of one body are merged on the node number (melt → pivot, element-nodal repeats averaged); the
  same node number with different coordinates is reported as an error (files of different meshes);
* archives are bounded (2000 members, 512 MB uncompressed); one manifest per upload; PNGs are checked
  against the size in their calibration; UTF-8, UTF-16 and cp1252 text are read.

Datasets live in an in-process LRU store (8 datasets, 4 h sliding expiry) on `app.state` - hence one
server process (the Docker image runs a single uvicorn worker).

### 5.2 Layer stack inference

`nodal.infer_stack` groups bodies whose extents overlap along an axis into layers (interval clustering;
touching end to end does not count). The stack axis is the one with the most layers - ties are broken by
the correlation of temperature with the coordinate. With temperatures, the hot end comes first and the
layer with the largest temperature span holds the legs (active); without, the hot face is assumed at the
largest coordinate and the thickest inner layer is taken as the legs (reported as a warning). Five layers
with the legs in the middle are named *ceramic hot, Cu hot, legs, Cu cold, ceramic cold*. The stack is a
proposal: the UI edits it and sends it back with `POST /nodal/{id}/profile`.

### 5.3 Temperature profile and ΔT split

`fields.layer_profile` reduces every layer to area-weighted averages of its hot and cold faces and every
touching pair of consecutive layers to the jump across their interface:

* **face averages** use area weights from a Delaunay triangulation of the face nodes (exact for fields
  linear over each triangle; a plain nodal mean is biased on graded meshes; faces are assumed convex, as
  every face of the unit cell is);
* **jumps** are evaluated on the smaller face (the leg end) after linear interpolation of the other side
  onto its nodes, so contact and target meshes need not match; round-off below 1e-9 K reads as 0;
* `passive_active_split` sums hot-side, leg and cold-side drops and the contact share separately - the
  nodal counterpart of thesis Table 4.3, with ceramic, copper and contact resolved instead of lumped.

### 5.4 Contact checks: TCC_eff and ECC_eff

For every body pair at an interface `contact_state` returns ΔT, ΔV and the fluxes through the face, and
`evaluate_profile` turns them into effective conductances:

| Quantity | Definition | Field used | Why that side |
| --- | --- | --- | --- |
| TCC_eff | q / ΔT | total heat flux of the **metal** body, interpolated onto the leg end | the contact carries heat by conduction only; on the thermoelectric side the conductive flux differs from it by the Peltier heat α·I·T absorbed or released at the junction |
| ECC_eff | J / ΔV | total current density of the **leg** | the normal current is continuous; in the metal the current turns sideways, so its magnitude is not the normal component |

Verdicts compare with the specified values (profile `contacts`, overridable per request):
**as-specified** within 0.67-1.5×, **bonded** when the jump is below 5 % of the expected jump (the
conductance was not applied), **deviates** otherwise, **not-evaluated** without flux data or below
1 W/m² (1 A/m²). A bonded leg contact is raised as a warning naming the interface.

This is the direct test behind the cold-side signature of section 4.2: the design-point data can only
show that the cold leg ends of B and C sit 0.75-0.76 K lower than A's, which matches the 0.80 K the
contacts should add; a nodal export of B or C shows whether the jump exists at each of the four contacts
and what conductance it corresponds to.

### 5.5 Overlay of contour pictures

`POST /nodal/{id}/points` projects nodes onto a standard view with +Y up and returns only the face of each
body that faces the camera (`surface_only`), sampled down to `max_points`:

| View | horizontal | vertical | toward the camera |
| --- | --- | --- | --- |
| front | x | y | +z |
| back | −x | y | −z |
| right | −z | y | +x |
| left | z | y | −x |
| top | x | −z | +y |
| bottom | x | z | −y |

A picture is placed with a two-point calibration (pixel ↔ view coordinates), `POST /overlay` returns the
`layout.images` placement. Calibrations come from the synthetic renderer (*exact*), from the export script,
which sets the camera's focal point and scene size itself (*camera* - depends on the Mechanical version and
a parallel projection, so the UI asks to verify), or from two clicks in the viewer: on the part's corners
(matched to the extent of the nodes in that view) or on two points with typed coordinates. The picture is
a backdrop; values always come from the nodes.

### 5.6 Synthetic unit cell

`synthetic.py` builds a physically consistent A0 cell for demos and end-to-end tests (not ANSYS output):
a 1D chain at a prescribed heat input and current with Peltier and contact Joule heat on the leg side of
each junction, parabolic temperature in the legs, ΔT = q/TCC and ΔV = J/ECC at the contacts. The energy
balance Q_in − Q_out = U·I closes to round-off, and fields are uniform across each layer, so face averages
and the recovered TCC_eff/ECC_eff are exact. It also writes the export folder of the script (text files,
ANSYS-like banded PNGs with their calibration, manifest) - the two examples *contacts applied* and
*bonded* on the pipeline page.

---

## 6 · API contract

Every request and response of `/api/pipeline` is a Pydantic model in `schemas.py`.
`backend/scripts/gen_ts_types.py` mirrors them to `frontend/src/app/core/pipeline.models.ts`:

* response models: every field present (the server serializes defaults), `X | None` → `X | null`;
* request models: fields with defaults are optional (`name?:`);
* a model used in both directions and shaped differently (own defaults, or a nested model that is)
  gets `Model` and `ModelInput` - a response value is assignable to the request type, so a proposed
  `LayerStack` goes straight back into `ProfileRequest`;
* enums and named literal aliases become string unions plus `*_VALUES` arrays; label maps in the UI are
  `Record<NodalKey, string>`, so a new key fails the Angular build until it is labeled.

`test_ts_contract.py` and a CI step fail when the generated file is stale; request models forbid unknown
fields, so a misspelled key from the client is a 422, not a silent default.

---

## 7 · Testing

The full test plan - static checks, browser tests, container and manual checks - is in
[../testing.md](../testing.md). The pipeline suites:

| Suite | What it pins down |
| --- | --- |
| `test_readers.py` | exact Fig. 3.10 structure, xlsx outlines, decimal commas, cp1252 nodal exports, unaveraged nodes |
| `test_frames.py` | alias vs pattern resolution, unit maps, reaction signs, system ownership, Table 3.5 values and the 0.24 mW (0.08 %) energy-balance residual |
| `test_extract.py` | Tables 4.2-4.3 (U_oc, 17.26 mΩ, 13.05 mW, 2.84/2.09 K), Table 5.4 conductances, contact isolation, batched fits vs `np.polyfit` |
| `test_maxpower_compare.py` | Table 5.2 and 5.3, exact optimum vs brute force and vs the finite-volume model, attribution identities |
| `test_fields_overlay.py` | synthetic seven-body unit cell with known drops, non-matching meshes, graded faces, image calibration |
| `test_nodal.py` | synthetic cell vs TEG theory (energy balance, U = αΔT − I·R), export → zip → ingest round trip recovering TCC_eff/ECC_eff to 1e-6, bonded and deviating contacts, stack inference (flipped, rotated, without T), ingestion edge cases, view projection, store bounds |
| `test_ts_contract.py` | the TypeScript mirror is up to date; request/response shapes |
| `test_cli.py`, `test_api.py` | command line; REST endpoints (parameter runs, dataset life cycle, examples, uploads, error codes, OpenAPI schema) |
| `../test_ansys_export_script.py` | the Mechanical export script against a stand-in API: script → zip → upload → contact checks → overlay placement; missing node locations; view conventions shared with the backend |

## 8 · Extension points

* another ANSYS project → a new JSON profile; nothing else changes;
* another source (PyDPF result files, lab CSV) → a new reader returning `RawTable` / `NodalTable`;
* another quantity → a `QuantityKey`, its dimension and a profile alias;
* another nodal field → a `NodalKey`, its header name in `readers.NODAL_NAMES` and a label in the UI
  (the build fails until it has one);
* another API model → add it to `schemas.py` and to the roots in `gen_ts_types.py`, then regenerate.
