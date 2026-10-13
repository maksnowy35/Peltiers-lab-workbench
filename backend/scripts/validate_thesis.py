"""Reproduce docs/validation.md: the 1D model against every number printed in the thesis.

python scripts/validate_thesis.py > ../docs/validation-tables.md
"""

from __future__ import annotations

import numpy as np

from teglab.ansys_io import build_cases, parse_files
from teglab.calibration import calibrate
from teglab.demo import thesis_a0_tt_csv
from teglab.design import reference_design
from teglab.reference import (
    T_MAX_AT_1_6A_C,
    TABLE_3_5,
    TABLE_4_2,
    TABLE_4_3,
    TABLE_5_4,
    THESIS_A0_TT_POINTS,
    VARIANTS,
)
from teglab.solver import build_model, max_power, short_circuit_current, solve


def row(*cells) -> str:
    return "| " + " | ".join(str(c) for c in cells) + " |"


def dev(model: float, ref: float) -> str:
    return f"{(model / ref - 1) * 100:+.2f} %"


def variant(name: str, contacts: bool | None = None):
    g = VARIANTS[name]
    d = reference_design().with_geometry(g["a_hot_mm"], g["a_cold_mm"], g["height_mm"])
    if contacts is not None:
        d.contacts.active = contacts
    return d


def main() -> None:
    # --- calibration on A0-TT --------------------------------------------------------
    table = parse_files([("a0tt.csv", thesis_a0_tt_csv().encode())])
    case = build_cases(
        table,
        [
            {
                "index": 1,
                "name": "A0-TT",
                "meta": {
                    "bc": "TT",
                    "t_hot_c": 100,
                    "t_cold_c": 20,
                    "a_hot_mm": 1.4,
                    "a_cold_mm": 1.4,
                    "height_mm": 1.4,
                },
            }
        ],
    )[0][0]
    d0 = reference_design()
    d0.passive.hot_side_factor = d0.passive.cold_side_factor = 1.0
    cal = calibrate(d0, [case], ["hot_side_factor", "cold_side_factor", "r_el_extra_mohm"])
    print("## Calibration on A0-TT (thesis Tables 4.2–4.4)\n")
    print(row("parameter", "fitted value"))
    print(row("---", "---"))
    for k, v in cal["params"].items():
        print(row(k, f"{v:.4g}"))
    print(f"\nRMS relative error {cal['rms_before'] * 100:.2f} % (uncalibrated) → {cal['rms_after'] * 100:.3f} %\n")

    tt = reference_design()
    tt.boundary.hot_mode, tt.boundary.t_hot_c = "T", 100.0
    m = build_model(tt)
    oc = solve(m, 0.0)
    mp = max_power(m)
    cur = np.array([0, 0.4, 0.8, 1.2, 1.6, 1.8])
    u = np.array([solve(m, i).voltage_v for i in cur])
    slope = -np.polyfit(cur, u, 1)[0]
    print("## A0-TT: model vs thesis\n")
    print(row("quantity", "thesis", "model", "deviation"))
    print(row("---", "---", "---", "---"))
    for label, ref, val, scale, unit in [
        ("U_oc", TABLE_4_2["u_oc_v"], oc.voltage_v, 1e3, "mV"),
        ("R_int from U(I) regression", TABLE_4_2["r_int_ohm"], slope, 1e3, "mΩ"),
        ("I_sc", TABLE_4_2["i_sc_a"], short_circuit_current(m, oc), 1, "A"),
        ("P_max", TABLE_4_2["p_max_w"], mp.power_w, 1e3, "mW"),
        ("I_opt", TABLE_4_2["i_opt_a"], mp.current_a, 1, "A"),
        ("Q_h (I = 0)", TABLE_4_2["q_hot_oc_w"], oc.q_hot_w, 1, "W"),
        ("ΔT_TE", TABLE_4_2["dt_te_k"], oc.dt_te_k, 1, "K"),
        ("ΔT hot passive", TABLE_4_3["dt_hot_passive_k"], oc.dt_passive_hot_k, 1, "K"),
        ("ΔT cold passive", TABLE_4_3["dt_cold_passive_k"], oc.dt_passive_cold_k, 1, "K"),
    ]:
        print(row(label, f"{ref * scale:.4g} {unit}", f"{val * scale:.4g} {unit}", dev(val, ref)))
    print(
        f"\nElectrical resistance of the model: {oc.r_int_ohm * 1e3:.2f} mΩ "
        f"(legs + 4 contacts + Cu bridge). The regression slope is larger because ΔT_TE drops under current.\n"
    )

    print("## A0-TT heat balance vs current (thesis Table 4.4)\n")
    print(row("I [A]", "Q_h thesis", "Q_h model", "Q_c thesis", "Q_c model", "P thesis [mW]", "P model [mW]"))
    print(row(*["---"] * 7))
    t = THESIS_A0_TT_POINTS
    for i, qh, qc, p in zip(t["current_a"], t["q_hot_w"], t["q_cold_w"], t["power_w"], strict=True):
        s = solve(m, i)
        print(
            row(
                i,
                f"{qh:.5f}",
                f"{s.q_hot_w:.5f}",
                f"{qc:.5f}",
                f"{s.q_cold_w:.5f}",
                f"{p * 1e3:.3f}",
                f"{s.power_w * 1e3:.3f}",
            )
        )

    print("\n## BC-QT variants: predictions (no further fitting)\n")
    print(
        row(
            "variant",
            "contacts in model",
            "T_h,ext [°C]",
            "ΔT_TE [K]",
            "U_oc [mV]",
            "K [mW/K]",
            "P_max [mW]",
            "T_max @1.6 A [°C]",
        )
    )
    print(row(*["---"] * 8))
    for name in ("A0", "A5", "A10"):
        ref = TABLE_5_4[name]
        print(
            row(
                f"**{name} thesis**",
                "–",
                ref["t_hot_ext_c"],
                ref["dt_te_k"],
                f"{ref['u_oc_v'] * 1e3:.3f}",
                f"{ref['k_pair_w_k'] * 1e3:.3f}",
                f"{ref['p_max_w'] * 1e3:.2f} (3-pt parabola)",
                T_MAX_AT_1_6A_C[name],
            )
        )
        for contacts in (True, False):
            mm = build_model(variant(name, contacts))
            o = solve(mm, 0.0)
            pm = max_power(mm, o)
            print(
                row(
                    name,
                    "finite TCC/ECC" if contacts else "ideal",
                    f"{o.t_hot_ext_c:.2f}",
                    f"{o.dt_te_k:.2f}",
                    f"{o.voltage_v * 1e3:.3f}",
                    f"{o.q_hot_w / o.dt_te_k * 1e3:.3f}",
                    f"{pm.power_w * 1e3:.2f}",
                    f"{solve(mm, 1.6).t_max_c:.2f}",
                )
            )

    c1 = TABLE_3_5["C1"]
    print("\n## A10-QT at I = 0.8 A (thesis Table 3.5, mesh C1)\n")
    print(row("quantity", "thesis", "model, finite contacts", "model, ideal contacts"))
    print(row(*["---"] * 4))
    s_on = solve(build_model(variant("A10", True)), 0.8)
    s_off = solve(build_model(variant("A10", False)), 0.8)
    for label, ref, a, b, sc, unit in [
        ("U", c1["voltage_v"], s_on.voltage_v, s_off.voltage_v, 1e3, "mV"),
        ("P", c1["power_w"], s_on.power_w, s_off.power_w, 1e3, "mW"),
        ("Q_c", c1["q_cold_w"], s_on.q_cold_w, s_off.q_cold_w, 1, "W"),
        ("T leg hot ends", c1["t_leg_hot_c"], s_on.t_leg_hot_c[0], s_off.t_leg_hot_c[0], 1, "°C"),
        ("T leg cold ends", c1["t_leg_cold_c"], s_on.t_leg_cold_c[0], s_off.t_leg_cold_c[0], 1, "°C"),
        ("ΔT_TE", c1["dt_te_k"], s_on.dt_te_k, s_off.dt_te_k, 1, "K"),
        ("T_max", c1["t_max_c"], s_on.t_max_c, s_off.t_max_c, 1, "°C"),
    ]:
        print(row(label, f"{ref * sc:.4g} {unit}", f"{a * sc:.4g} {unit}", f"{b * sc:.4g} {unit}"))
    r_impl = (4e-4 * c1["dt_te_k"] - c1["voltage_v"]) / 0.8
    print(
        f"\nImplied internal resistance of the thesis data (α·ΔT_TE − U)/I = {r_impl * 1e3:.2f} mΩ; "
        f"model: {solve(build_model(variant('A10', True)), 0).r_int_ohm * 1e3:.2f} mΩ with contacts, "
        f"{solve(build_model(variant('A10', False)), 0).r_int_ohm * 1e3:.2f} mΩ without.\n"
    )

    print("## Fair comparison: every variant with the same contacts\n")
    print(row("variant", "P_max [mW]", "gain vs A0", "η at P_max", "T_h,ext OC [°C]"))
    print(row(*["---"] * 5))
    base = None
    for name in ("A0", "A5", "A10", "A15"):
        mm = build_model(variant(name, True))
        o = solve(mm, 0.0)
        pm = max_power(mm, o)
        base = base or pm.power_w
        print(
            row(
                name,
                f"{pm.power_w * 1e3:.2f}",
                f"{(pm.power_w / base - 1) * 100:+.1f} %",
                f"{pm.efficiency * 100:.2f} %",
                f"{o.t_hot_ext_c:.1f}",
            )
        )


if __name__ == "__main__":
    main()
