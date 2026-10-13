# Validation against the thesis

The reduced-order (1D) model is checked against every number printed in the master's thesis
*Numerical analysis and optimization of Peltier thermoelectric modules* (M. Kruk, Warsaw University of
Technology, 2026), whose ANSYS Workbench Thermal-Electric model of a TEC1-12706 unit cell is the reference.

Reproduce everything below with:

```bash
cd backend
python scripts/validate_thesis.py
```

The same comparisons run as unit tests (`tests/test_thesis_validation.py`).

## 1 · Calibration on A0-TT only

Three lumped parameters are fitted to thesis Tables 4.2–4.4 (prismatic legs, 100 °C / 20 °C):
the ceramic + Cu thermal resistance on each side (3D spreading) and a series-resistance correction.

| parameter | fitted value |
| --- | --- |
| hot-side ceramic+Cu factor | 1.48 |
| cold-side ceramic+Cu factor | 0.936 |
| series resistance correction | 0.002 mΩ (≈ 0) |

RMS relative error over U, Q_h, Q_c and leg-end temperatures: 0.73 % uncalibrated → 0.010 % calibrated.

| quantity | thesis | model | deviation |
| --- | --- | --- | --- |
| U_oc | 30.02 mV | 30.02 mV | +0.00 % |
| R_int from the U(I) regression | 17.26 mΩ | 17.26 mΩ | −0.03 % |
| I_sc | 1.739 A | 1.740 A | +0.04 % |
| P_max | 13.05 mW | 13.05 mW | +0.01 % |
| I_opt | 0.870 A | 0.870 A | −0.04 % |
| Q_h (I = 0) | 0.3152 W | 0.3152 W | +0.01 % |
| ΔT_TE | 75.11 K | 75.05 K | −0.08 % |
| ΔT hot passive | 2.84 K | 2.85 K | +0.4 % |
| ΔT cold passive | 2.09 K | 2.10 K | +0.4 % |

Heat balance vs current (Table 4.4):

| I [A] | Q_h thesis | Q_h model | Q_c thesis | Q_c model | P thesis [mW] | P model [mW] |
| --- | --- | --- | --- | --- | --- | --- |
| 0.0 | 0.31519 | 0.31521 | 0.31519 | 0.31521 | 0.000 | 0.000 |
| 0.4 | 0.36971 | 0.36973 | 0.36046 | 0.36048 | 9.246 | 9.245 |
| 0.8 | 0.42145 | 0.42149 | 0.40848 | 0.40852 | 12.966 | 12.968 |
| 1.8 | 0.53880 | 0.53888 | 0.54068 | 0.54075 | −1.887 | −1.871 |

**Observation – the 17.26 mΩ is not purely ohmic.** The electrical resistance of the calibrated model is
16.47 mΩ (legs 14.29 + four contacts 2.04 + Cu bridge ≈ 0.15 mΩ). The U(I) regression gives 17.26 mΩ because
under current the Peltier heat increases the heat flow through the passive layers, so ΔT_TE – and with it the
EMF – drops by ≈ 1.6 K at 0.8 A. About 0.8 mΩ of the regression slope is this thermal effect, not
current crowding in the copper.

## 2 · Predictions for the tapered variants (BC-QT, no further fitting)

The frustum 3D coefficient c3d = 0.5 (R₃D = R₁D·(1 + c3d·tan²θ)) reproduces the thesis K values
(−0.4 % for A5, −1.6 % for A10 relative to the 1D formula).

| variant | contacts in model | T_h,ext [°C] | ΔT_TE [K] | U_oc [mV] | K [mW/K] | P_max [mW] | T_max at 1.6 A [°C] |
| --- | --- | --- | --- | --- | --- | --- | --- |
| **A0 thesis** | – | 100.04 | 75.11 | 30.024 | 4.196 | 7.66 ¹ | 55.62 |
| A0 | finite TCC/ECC | 100.00 | 75.05 | 30.019 | 4.200 | 7.63 | 55.52 |
| A0 | ideal | 98.39 | 75.05 | 30.019 | 4.200 | 8.21 | 53.41 |
| **A5 thesis** | – | 114.74 | 91.32 | 36.531 | 3.451 | 9.91 ¹ | 60.03 |
| A5 | finite TCC/ECC | 116.64 | 91.31 | 36.525 | 3.452 | 9.16 | 62.76 |
| A5 | ideal | 114.65 | 91.31 | 36.525 | 3.452 | 9.86 | 59.88 |
| **A10 thesis** | – | 141.31 | 117.80 | 47.137 | 2.676 | 12.54 ¹ | 70.56 |
| A10 | finite TCC/ECC | 143.80 | 117.73 | 47.093 | 2.677 | 11.54 | 74.91 |
| A10 | ideal | 141.07 | 117.73 | 47.093 | 2.677 | 12.45 | 70.35 |

¹ parabola through I = 0, 0.4, 0.8 A (thesis method); the model value is the true maximum.

A0 matches the thesis with the contacts that the thesis defines (TCC = 10⁵ W/(m²·K), ECC = 10⁹ S/m²).
**A5 and A10 match only when the contacts are ideal** – hot-face temperature, T_max at 1.6 A and
P_max all agree within ≈ 0.5 % in that case and are clearly off with finite contacts.

### A10-QT at I = 0.8 A (Table 3.5, mesh C1)

| quantity | thesis | model, finite contacts | model, ideal contacts |
| --- | --- | --- | --- |
| U | 12.38 mV | 9.82 mV | 12.41 mV |
| P | 9.907 mW | 7.856 mW | 9.928 mW |
| Q_c | 0.3050 W | 0.3073 W | 0.3053 W |
| T leg hot ends | 97.45 °C | 98.62 °C | 97.39 °C |
| T leg cold ends | 21.29 °C | 22.04 °C | 21.25 °C |
| ΔT_TE | 76.16 K | 76.58 K | 76.14 K |
| T_max | 99.61 °C | 102.6 °C | 99.44 °C |

The same conclusion follows from the thesis numbers alone, without any model: with α = 400 µV/K the
implied internal resistance is (α·ΔT_TE − U)/I = (0.4·76.159 − 12.384)/0.8 = **22.6 mΩ**. Legs (22.4 mΩ
with the 3D correction) plus the Cu bridge already give 22.56 mΩ; the four contacts of 1/(ECC·A) would add
another 3.5 mΩ (26.0 mΩ in total). Likewise the passive temperature drop of A5/A10 (3.4–3.5 K) is smaller
than that of A0 (4.9 K) although the hot contact area shrank.

**Likely cause:** in analysis systems B and C (copies of A with replaced geometry) the contact regions fell
back to the default *program-controlled* (practically ideal) thermal and electrical conductance. Worth
checking in Mechanical → Connections → Contacts → each contact region → *Thermal Conductance* and
*Electric Conductance* = Manual with the parameter values. The importer runs this check automatically
(“Implied internal resistance” and “Hot-side passive resistance” on the Analysis page).

## 3 · Fair comparison – every variant with the same contacts

| variant | P_max [mW] | gain vs A0 | η at P_max | T_h,ext at open circuit [°C] |
| --- | --- | --- | --- | --- |
| A0 | 7.63 | – | 2.42 % | 100.0 |
| A5 | 9.16 | +20.1 % | 2.91 % | 116.6 |
| A10 | 11.54 | +51.3 % | 3.66 % | 143.8 |
| A15 | 15.68 | +105.5 % | 4.97 % | 195.4 |

With identical contact definitions the gains are +20 % (A5) and +51 % (A10) – slightly below the analytic
+21.2 % / +54.4 % of thesis Table 5.3, as expected once ΔT collapses under current – instead of the
+29.4 % / +63.7 % obtained with contacts only in the reference system. The qualitative conclusions of the
thesis (taper helps under fixed heat input; the gain comes from lower K, not from better material) stand.

## 4 · Mesh study (Section 3.5.3)

The GCI calculator on the Analysis page reproduces the thesis numbers: ΔT_TE on meshes C1/C2
(76.159 / 76.187 K, r = 1.99, p = 2, Fs = 3) gives GCI = 0.037 % and a Richardson value of 76.197 K.
