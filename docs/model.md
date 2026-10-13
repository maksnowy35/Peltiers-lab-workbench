# The 1D thermoelectric pair model

`teglab.solver` solves one P–N pair (unit cell of a module) in steady state. It is designed as a *fast
surrogate* of the 3D ANSYS Thermal-Electric model: milliseconds per operating point, so geometry and
material studies with thousands of evaluations stay interactive, while a handful of lumped parameters are
calibrated against the FEM results.

## Governing equations

Inside a thermoelectric segment (thesis eq. 3.6):

∇·(κ∇T) + ρJ² − τ J·∇T = 0,  with τ = T dα/dT (Thomson coefficient)

and the total heat flux q = −κ∇T + αTJ. In one dimension with a variable cross-section A(x):

d/dx(κA dT/dx) + ρI²/A − τ I dT/dx = 0

## Discretization

* **Legs** are split into elements with uniform properties evaluated at the element mean temperature
  (vertex-centered finite volumes). For a linearly tapered square section a(x) = a_h + (a_c − a_h)x/H the
  exact integral ∫dx/A = Δx/(a₁a₂) is used for both the thermal conductance and the electrical resistance.
* **Frustum 3D correction.** A tapered leg conducts slightly worse than the 1D formula predicts because the
  flux lines are not parallel. Both resistances are multiplied by 1 + c3d·tan²θ (c3d = 0.5 reproduces the
  ANSYS K of the thesis A5/A10 legs within 0.1 %).
* **Joule heat** I²R of each element is split half/half to its end nodes.
* **Peltier and Thomson heat.** Every node receives (α_left − α_right)·T·I_x, where α_left/right are the
  Seebeck coefficients of the adjacent elements (copper: α = 0). At a leg end this is the junction Peltier heat;
  inside a leg with α(T) it converges to the distributed Thomson term; at the interface of a segmented leg it
  is the Peltier heat of the material junction. This form is *exactly* energy-conservative:
  Q_h − Q_c − Q_side = P_el to machine precision (reported as `energy_balance_rel`).
* **EMF and voltage.** U = Σ α_e ΔT_e (with the leg sign) − I·R_int, consistent with the Peltier terms (Kelvin
  relation Π = αT holds discretely).
* **Temperature-dependent properties** (PCHIP tables, C¹-continuous so that τ has no artificial jumps) are
  handled by Picard iteration with automatic under-relaxation; constant properties need a single linear solve.

## Passive elements and contacts

| element | thermal | electrical |
| --- | --- | --- |
| ceramic plate (per side, shared by both legs) | t/(k·A_cer) × side factor | insulator |
| Cu electrode (per leg and side) | t/(k·A_Cu) × side factor | bridge + terminal path ρ·L/(t·w) |
| metallized joint (4 per pair) | 1/(TCC·A_end) | 1/(ECC·A_end) |
| series correction | – | ΔR (calibration) |

The Joule heat of a joint is deposited half on each side of it. A_cer defaults to the pair footprint
(2a + g)·a. Side walls can lose heat to the ambient with a coefficient h (radiation + convection, thesis eq. 3.24).

## Boundary conditions

| hot side | cold side |
| --- | --- |
| fixed temperature (BC-TT) | fixed temperature |
| fixed heat input Q_h (BC-QT) | heat sink T_sink + R_sink |
| heat exchanger T_source + R_source | |

The exchanger form is what a waste-heat-recovery system really sees; it produces a genuine optimum of the
leg shape (thermal impedance matching), whereas BC-QT keeps rewarding lower K until a temperature or
manufacturing limit is hit, and BC-TT always prefers the prism.

## Operating points

* `solve(model, I)` – prescribed current (as in the ANSYS current sweep);
* `solve_for_load(model, R_L)` – external load;
* `max_power`, `max_efficiency` – bounded Brent search on I;
* `short_circuit_current` – U = 0 (under BC-QT well below EMF/R_int).

## Calibration

`teglab.calibration.calibrate` fits any subset of: hot/cold ceramic+Cu factors, series-resistance correction,
c3d, Seebeck scale, TCC, ECC – by least squares on U, P, Q_h, Q_c and the leg-end/face temperatures of the
selected cases (each case solved with its own geometry, boundary condition and contact setting). Residuals are
normalized by the case's voltage/heat scale and by the temperature rise.

## Limits of the model

* Steady state only; no thermomechanics (stress in the narrow end must be checked separately).
* Current crowding at electrode edges and non-uniform contact pressure are lumped into ΔR.
* Material curves in the built-in library are representative, not measured – upload your own data.
