"""Command line: inspect ANSYS exports or run the full pipeline on them.

python -m teglab.pipeline inspect exports/*.csv
python -m teglab.pipeline run design_points.csv outline.csv --system A=A0 --system B=A5 --system C=A10
python -m teglab.pipeline run a0_tt.csv --bc TT --t-hot 100 --t-cold 20 --json > report.json
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .run import RunOptions, inspect_files, run_pipeline
from .schemas import CaseCondition, Level, PipelineReport, ProjectProfile, load_profile


def _condition(args: argparse.Namespace, profile: ProjectProfile) -> CaseCondition | None:
    if args.bc:
        return CaseCondition(
            bc=args.bc, t_hot_face_c=args.t_hot, q_hot_w=args.q_hot, t_cold_face_c=args.t_cold, current_a=args.current
        )
    if args.current is not None and profile.default_condition is not None:
        return profile.default_condition.model_copy(update={"current_a": args.current})
    return None


def _fmt(value: float | None, scale: float = 1.0, digits: int = 4) -> str:
    return "-" if value is None else f"{value * scale:.{digits}g}"


def _print_inspect(report: PipelineReport) -> None:
    for s in report.sources:
        print(
            f"\n{s.source}: {s.format.value} (confidence {s.confidence:.2f}), "
            f"{s.n_parameters} parameters, {s.n_rows} rows"
        )
        for sys_ref in s.systems:
            print(f"  system {sys_ref.letter}: {sys_ref.title}")
        for note in s.notes:
            print(f"  · {note}")
    if report.parameters:
        print(f"\n{'ID':<6} {'system':<7} {'name':<36} {'unit':<8} {'→ key':<16} rule")
        for p in report.parameters:
            key = p.key.value if p.key else "?"
            print(
                f"{p.pid:<6} {p.system or '':<7} {p.name[:36]:<36} {p.unit or '':<8} {key:<16} "
                f"{p.rule} ({p.confidence:.1f})"
            )
        unresolved = sorted({p.name for p in report.parameters if p.rule == "unresolved"})
        if unresolved:
            print("\nUnresolved names - add them to the profile, e.g.:")
            print('  "aliases": {"<canonical key>": [' + ", ".join(f'"{n}"' for n in unresolved) + "]}")
    _print_issues(report)


def _print_run(report: PipelineReport) -> None:
    for s in report.systems:
        oc, th, r, mp, an = s.open_circuit, s.thermal, s.resistance, s.max_power, s.analytic
        title = f" ({s.title})" if s.title else ""
        print(f"\nSystem {s.system}{title} - variant {s.variant or '?'}, θ = {_fmt(s.theta_deg)}°, BC-{s.bc or '?'}")
        print(
            f"  U_oc {_fmt(oc.u_oc_v, 1e3)} mV   ΔT_TE {_fmt(oc.dt_te_k)} K   "
            f"K {_fmt(oc.k_te_w_k, 1e3)} mW/K   α_eff {_fmt(oc.alpha_eff_v_k, 1e6)} µV/K"
        )
        print(
            f"  passive: hot {_fmt(th.dt_hot_passive_k)} K, cold {_fmt(th.dt_cold_passive_k)} K, "
            f"share {_fmt(th.passive_share, 100, 3)} % of {_fmt(th.imposed_dt_k)} K"
        )
        print(
            f"  R: apparent {_fmt(r.apparent_ohm, 1e3)} mΩ, ohmic {_fmt(r.ohmic_ohm, 1e3)} mΩ "
            f"(legs {_fmt(r.legs_ohm, 1e3)}, "
            f"Cu {_fmt(r.copper_ohm, 1e3)}, contacts {_fmt(r.contacts_isolated_ohm, 1e3)} vs expected "
            f"{_fmt(r.contacts_expected_ohm, 1e3)}) → contacts {r.contact_verdict}"
        )
        if mp.p_max_w is None:
            print(f"  P_max: {mp.method}")
        else:
            print(f"  P_max {_fmt(mp.p_max_w, 1e3)} mW at {_fmt(mp.i_opt_a)} A ({mp.method})")
        print(
            f"  legs-only references: frozen α²Q²/(4K²R) {_fmt(an.frozen_w, 1e3)} mW, "
            f"exact BC-QT {_fmt(an.exact_w, 1e3)} mW "
            f"at {_fmt(an.exact_i_opt_a)} A, BC-TT α²ΔT²/(4R) {_fmt(an.tt_w, 1e3)} mW"
        )
        if s.twin.status == "ok":
            tw = "calibrated" if s.twin.calibrated else "profile factors"
            print(f"  1D twin ({tw}):")
            for m in s.twin.metrics:
                if m.ansys is not None:
                    dev = "" if m.deviation is None else f"  {m.deviation:+.2%}"
                    print(f"    {m.label:<26} FEM {_fmt(m.ansys, m.scale)}  1D {_fmt(m.model, m.scale)} {m.unit}{dev}")
        elif s.twin.note:
            print(f"  1D twin skipped: {s.twin.note}")
        marks = {"pass": "ok", "warn": "WARN", "fail": "FAIL", "skip": "-"}
        print("  QA: " + ", ".join(f"{g.code} {marks[g.status]}" for g in s.qa))
        for i in s.issues:
            print(f"  [{i.level.value}] {i.message}")
    if report.comparison:
        print("\nVariant comparison (ratios to the reference):")
        print(
            f"  {'variant':<8} {'sys':<4} {'K':>7} {'R':>7} {'V':>7} {'ΔT':>7} "
            f"{'P frozen':>9} {'P exact':>8} {'P at TT':>8}"
        )
        for c in report.comparison:
            print(
                f"  {c.variant:<8} {c.system:<4} {_fmt(c.k_ratio):>7} {_fmt(c.r_ratio):>7} {_fmt(c.volume_ratio):>7} "
                f"{_fmt(c.dt_te_ratio):>7} {_fmt(c.p_frozen_ratio):>9} "
                f"{_fmt(c.p_exact_ratio):>8} {_fmt(c.p_tt_ratio):>8}"
            )
    _print_issues(report)


def _print_issues(report: PipelineReport) -> None:
    shown = [i for i in report.issues if i.level is not Level.INFO]
    if shown:
        print()
    for i in shown:
        where = f" [{i.system}]" if i.system else ""
        print(f"{i.level.value.upper()}{where}: {i.message}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m teglab.pipeline", description=__doc__.splitlines()[0])
    ap.add_argument("command", choices=["inspect", "run"])
    ap.add_argument("files", nargs="+", type=Path)
    ap.add_argument("--profile", default="thesis-2026", help="bundled profile name or path to a JSON profile")
    ap.add_argument("--bc", choices=["TT", "QT"], help="boundary condition for every system (overrides the profile)")
    ap.add_argument("--t-hot", type=float, help="hot-face temperature [°C] (BC-TT)")
    ap.add_argument("--q-hot", type=float, help="heat input per pair [W] (BC-QT)")
    ap.add_argument("--t-cold", type=float, default=20.0, help="cold-face temperature [°C] (with --bc)")
    ap.add_argument("--current", type=float, help="operating current [A] for single-point outline exports")
    ap.add_argument("--system", action="append", default=[], metavar="LETTER=VARIANT", help="e.g. B=A5 (repeatable)")
    ap.add_argument("--reference", help="reference variant for the comparison (default from the profile)")
    ap.add_argument("--json", action="store_true", help="print the full report as JSON")
    args = ap.parse_args(argv)

    profile = load_profile(args.profile)
    files = [(p.name, p.read_bytes()) for p in args.files]
    if args.command == "inspect":
        report = inspect_files(files, profile)
        print(report.model_dump_json(indent=2)) if args.json else _print_inspect(report)
        return 0
    variants = dict(item.split("=", 1) for item in args.system)
    options = RunOptions(
        profile=profile,
        default_condition=_condition(args, profile),
        variants=variants,
        reference_variant=args.reference,
    )
    report = run_pipeline(files, options).report
    print(report.model_dump_json(indent=2)) if args.json else _print_run(report)
    return 1 if any(i.level is Level.ERROR for i in report.issues) else 0


if __name__ == "__main__":
    sys.exit(main())
