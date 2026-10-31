"""Variant comparison relative to a reference geometry, with the gain split into its causes.

For the frozen BC-QT relation P = α²Q²/(4K²R) the power ratio of two designs factorizes exactly::

    P/P₀ = (α/α₀)² · (Q/Q₀)² · (K₀/K)² · (R₀/R)
           seebeck    heat input  conductance  resistance

so a taper's effect is reported as "conductance ×2.39, resistance ×0.65 → ×1.55" instead of a bare
percentage.  At fixed junction temperatures (BC-TT, P = α²ΔT²/(4R)) only the resistance factor
remains, which is why the prism wins there.

``cold_side_signature`` is a data-only consistency check between variants that share the cold side:
their cold passive drop must agree; a difference equal to the expected drop across the cold contacts
means the contact conductance did not take effect in one of the systems.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .schemas import Issue, Level, VariantComparison

__all__ = ["cold_side_signature", "compare_variants", "pick_reference"]


def pick_reference(summary: pd.DataFrame, variant: str) -> str | None:
    """First system (letter order) of the reference variant, preferring the base mesh."""
    rows = summary[summary["variant"] == variant]
    if rows.empty:
        return None
    base = rows[rows["mesh"].fillna("").isin(["", "C1"])]
    return str((base if not base.empty else rows).index.sort_values()[0])


def compare_variants(summary: pd.DataFrame, reference: str) -> list[VariantComparison]:
    """``summary``: one row per system with variant, theta_deg, k_1d_w_k, r_1d_ohm, k_te_w_k, volume_mm3,
    dt_te_k, alpha_v_k, q_hot_w, p_frozen_w, p_exact_w, p_max_w and bracketed."""
    ref = summary.loc[reference]
    ratio = summary.select_dtypes("number") / ref[summary.select_dtypes("number").columns]
    rows = []
    for system, r in summary.drop(index=reference).iterrows():
        k_ratio = (
            ratio.at[system, "k_te_w_k"] if pd.notna(ratio.at[system, "k_te_w_k"]) else ratio.at[system, "k_1d_w_k"]
        )
        both_bracketed = bool(r.get("bracketed")) and bool(ref.get("bracketed"))
        factors = {
            "seebeck": ratio.at[system, "alpha_v_k"] ** 2,
            "heat_input": ratio.at[system, "q_hot_w"] ** 2,
            "conductance": 1.0 / ratio.at[system, "k_1d_w_k"] ** 2,
            "resistance": 1.0 / ratio.at[system, "r_1d_ohm"],
        }
        rows.append(
            VariantComparison(
                variant=str(r["variant"]),
                system=str(system),
                theta_deg=_num(r.get("theta_deg")),
                k_ratio=_num(k_ratio),
                r_ratio=_num(ratio.at[system, "r_1d_ohm"]),
                volume_ratio=_num(ratio.at[system, "volume_mm3"]),
                dt_te_ratio=_num(ratio.at[system, "dt_te_k"]),
                p_frozen_ratio=_num(ratio.at[system, "p_frozen_w"]),
                p_exact_ratio=_num(ratio.at[system, "p_exact_w"]),
                p_measured_ratio=_num(ratio.at[system, "p_max_w"]) if both_bracketed else None,
                p_tt_ratio=_num(1.0 / ratio.at[system, "r_1d_ohm"]),
                attribution={k: float(v) for k, v in factors.items() if pd.notna(v)},
            )
        )
    return rows


def cold_side_signature(summary: pd.DataFrame, reference: str, tolerance: float = 0.35) -> list[Issue]:
    """Flag systems whose cold passive drop differs from the reference by the cold-contact drop.

    ``summary`` needs a_cold_mm, dt_cold_passive_k and contact_dt_cold_expected_k.
    """
    if reference not in summary.index:
        return []
    ref = summary.loc[reference]
    same_cold = np.isclose(summary["a_cold_mm"], ref["a_cold_mm"])
    delta = summary["dt_cold_passive_k"] - ref["dt_cold_passive_k"]
    expected = ref["contact_dt_cold_expected_k"]
    issues = []
    for system in summary.index[same_cold & delta.notna()]:
        d = float(delta[system])
        if system == reference or abs(d) < 0.25 or not np.isfinite(expected):
            continue
        if abs(abs(d) - expected) <= tolerance * expected:
            lower = "lower" if d < 0 else "higher"
            which = "this system" if d < 0 else "the reference system"
            issues.append(
                Issue(
                    level=Level.WARNING,
                    code="contact-signature-cold",
                    message=f"cold leg ends read {abs(d):.2f} K {lower} than in {reference} although the cold side is "
                    f"identical; the drop across the cold contacts should be {expected:.2f} K - the contact "
                    f"conductance probably did not take effect in {which}",
                    system=str(system),
                )
            )
    return issues


def _num(value: object) -> float | None:
    try:
        v = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return v if np.isfinite(v) else None
