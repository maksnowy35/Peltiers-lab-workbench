# Test plan

Four automated layers - run the first three before every push - plus the checks only a person or your own
ANSYS data can do.

| Layer | Command | Count / time | Runs in CI |
| --- | --- | --- | --- |
| 1 · Static checks | `./dev.sh lint`, `./dev.sh contract --check`, `./dev.sh build` | 66 Python files, strict Angular build | yes |
| 2 · Backend tests | `./dev.sh test` | 155 tests, ~15 s | yes, Python 3.11-3.13 |
| 3 · Browser tests | `./dev.sh e2e` (app running) | 12 tests, ~40 s | yes, against the container |
| 4 · Container | `./dev.sh docker` | health check + smoke test | yes |
| 5 · Manual acceptance | checklist below | ~15 min | - |
| 6 · Your ANSYS data | section 6 | - | - |

`./dev.sh check` runs layer 1 and 2 in one go.

## 1 · Static checks

* **Ruff** lints and format-checks all Python: the backend, the e2e tests and the Mechanical script (which
  has its own IronPython-safe rules).
* **API contract**: `frontend/src/app/core/pipeline.models.ts` must equal what
  `backend/scripts/gen_ts_types.py` generates from the Pydantic models. If it fails, run
  `./dev.sh contract` and commit the regenerated file.
* **Angular production build**: `strict`, `strictTemplates` and `strictStandalone` type-check every
  template against the generated models; size budgets fail the build if the initial bundle grows past 1 MB.

## 2 · Backend tests (`./dev.sh test`)

| Suite | Tests | What it proves |
| --- | --- | --- |
| `test_solver.py`, `test_geometry_analytic.py` | 19 | 1D finite-volume model: energy conservation, analytic limits, frustum geometry |
| `test_thesis_validation.py` | 9 | the calibrated model reproduces thesis Tables 4.2-4.4 and predicts A5/A10 |
| `test_analysis.py`, `test_studies.py` | 20 | load-curve fits, consistency checks, GCI, optimizer and material studies |
| `test_ansys_io.py`, `test_api.py` | 19 | the original import page and its REST API |
| `pipeline/test_readers.py` | 7 | exact thesis Fig. 3.10 structure, xlsx outlines, decimal commas, encodings |
| `pipeline/test_frames.py` | 7 | name resolution through the profile, units, signs, Table 3.5, energy balance |
| `pipeline/test_extract.py`, `test_maxpower_compare.py` | 14 | U_oc, R_int split, P_max, Tables 4.2-4.4 and 5.2-5.4, gain attribution |
| `pipeline/test_cli.py` | 2 | `python -m teglab.pipeline inspect / run` |
| `pipeline/test_fields_overlay.py` | 6 | face averages on graded meshes, contact jumps, picture calibration |
| `pipeline/test_nodal.py` | 18 | synthetic cell vs TEG theory, export → zip → upload recovering TCC/ECC exactly, bonded and deviating contacts, stack inference, view projection |
| `pipeline/test_api.py` | 7 | every `/api/pipeline` endpoint, error codes, OpenAPI schema |
| `pipeline/test_ts_contract.py` | 2 | the TypeScript mirror is current; request vs response shapes |
| `test_ansys_export_script.py` | 4 | the Mechanical script against a stand-in API: export → zip → upload → contact checks → overlay placement; missing node locations |

Useful variants: `./dev.sh test -k nodal`, `./dev.sh test -x` (stop at the first failure),
`./dev.sh test tests/pipeline/test_api.py -vv`, or the Testing panel in VS Code.
`cd backend && ../.venv/Scripts/python scripts/validate_thesis.py` (`.venv/bin/python` on Linux/macOS) prints
the comparison tables of [validation.md](validation.md).

## 3 · Browser tests (`./dev.sh e2e`)

Headless Chromium drives the running app; any console error, uncaught exception or HTTP error fails the test.

```bash
./dev.sh e2e-setup        # once: Playwright + Chromium (~150 MB)
./dev.sh serve            # terminal 1: built UI + API on :8000 (or: ./dev.sh docker)
./dev.sh e2e              # terminal 2
TEGLAB_URL=http://localhost:4200 ./dev.sh e2e   # against ./dev.sh api + ./dev.sh ui instead
```

| Test | Checks |
| --- | --- |
| `test_every_page_renders[light/dark]` | all eight pages load with their heading and title, in both themes |
| `test_phone_width_has_no_horizontal_scroll` | 390 px wide: overview, import and the three pipeline tabs fit the screen |
| `test_tabs_are_deep_links` | `?tab=` selects the tab and follows tab changes |
| `test_parameter_run_flags_the_inactive_contacts` | thesis A-series example → 3 systems, C marked *Inactive*, the 0.75 K cold-side finding |
| `test_nodal_contact_checks[applied/bonded]` | demo datasets → 8 × *As specified* with ΔT_TE = 50.00 K / 8 × *Not applied* |
| `test_overlay_places_calibrates_and_follows_the_view` | picture placed exactly, two-click recalibration within 0.05 mm, picture follows view and quantity, CSV export |

## 4 · Container

```bash
./dev.sh docker                       # builds UI + backend images, starts on http://localhost:8000
docker compose ps                     # STATUS must turn "healthy" within ~30 s
./dev.sh e2e                          # the browser tests against the container
docker compose down                   # add -v to delete the data volume too
```

## 5 · Manual acceptance (before the defense demo or a release)

Light and dark theme (button at the bottom of the sidebar), and once at phone width
(DevTools → Ctrl+Shift+M → 390 px):

- [ ] **Overview** shows the active design's figures; *Data health* lists the warnings of the example cases.
- [ ] **Data import**: load `thesis_A-series_QT.csv`, check the column mapping, create the cases.
- [ ] **Analysis**: load curves of A0/A5/A10 with the model overlaid; *Table* switch on a chart works.
- [ ] **Model**, **Optimizer**, **Materials**, **Lab planner**: each computes and plots without errors.
- [ ] **ANSYS pipeline → Parameters**: add the three examples; the outline shows system D as *Dense mesh
      copy C 10st*; run with variants A0/A5/A10 and current 0.8 A; the systems table gives U_oc 30.02 mV
      (A), K 4.197 / 3.452 / 2.676 mW/K, R ohmic 22.61 mΩ for C with contacts *Inactive*.
- [ ] **Nodal fields**: load both synthetic examples, evaluate: ΔT across the legs 50.00 K, contact jumps
      0.804 / 0.790 K, TCC_eff 1.00·10⁵, ECC_eff 1.00·10⁹; the bonded one shows 8 × *Not applied*.
      Edit the stack (reverse, rename, move a body) - the stale-result notice appears - evaluate again.
- [ ] **Overlay**: picture under the nodes, *Recalibrate* with two corner clicks, opacity and node size
      sliders, views and quantities, *Download nodes (CSV)*, *Upload PNG…* with any screenshot.
- [ ] `/docs` lists the `/api/pipeline/...` endpoints; *Try it out* on `GET /api/pipeline/examples`.
- [ ] Restart the backend: imported cases are still there (SQLite); nodal datasets are gone (kept in memory
      by design - upload again).

## 6 · Your ANSYS data - what only real exports can show

1. **Parameter exports.** For every exported table:
   `python -m teglab.pipeline inspect <files>` - no row may end in `unresolved`; add missing names to
   `backend/src/teglab/pipeline/profiles/thesis-2026.json` (`aliases`). Then `run` and compare with the
   thesis: A0-TT U_oc 30.02 mV, R from the regression 17.26 mΩ, P_max 13.05 mW at 0.870 A; Table 5.4
   conductances 4.196 / 3.451 / 2.676 mW/K.
2. **Mechanical export script - first run** (see `tools/ansys/README.md`): enable *Include Node Location*,
   export system **A first** as the control. Read `teglab_export.log`; a call your ANSYS version lacks is
   named there. Upload the zip: A's four leg contacts must read *As specified* (TCC_eff ≈ 1·10⁵ W/(m²·K),
   ECC_eff ≈ 1·10⁹ S/m²) - if they do not, the export or the stack is wrong, not the physics.
3. **Systems B and C** - the contact hypothesis: *Not applied (bonded)* at the leg contacts confirms that
   the contact conductances were not applied; *As specified* rejects it (then look at probe scoping).
4. **Pictures**: on the *Overlay* tab the outlines of the nodes must match the contour picture; if the
   camera calibration is off, two clicks on the part corners fix it - and tell you to check the projection
   setting of your Mechanical version.

## 7 · Before tagging a release

- [ ] `./dev.sh check` passes; `./dev.sh e2e` passes against `./dev.sh serve`.
- [ ] CI on GitHub is green on the release commit (backend 3.11-3.13, frontend build, Docker + e2e).
- [ ] Version bumped in `backend/pyproject.toml`, `backend/src/teglab/__init__.py`, `frontend/package.json`.
- [ ] `CHANGELOG.md` generated and committed; tag `vX.Y.Z` pushed.
