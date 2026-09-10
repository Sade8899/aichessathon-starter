"""V3 selection metrics: move regret and the harmful tail, not average error.

V1 selected on MAE, improved MAE by 27.85 cp, and lost about 10 Elo. V2 selected on a
composite dominated by preservation and still lost about 10.8 Elo, but its cheap metrics
did predict the expensive fixture gate exactly, which is what makes this worth extending
rather than replacing.

What V2's composite could not see is the *tail*. A checkpoint that reduces mean regret
by 6 cp while occasionally choosing a move that throws away 300 cp is a losing trade
that a mean cannot express. V3 therefore scores the 95th and 99th percentile of the
regret it *adds* to the control's own choice, and weights those against it.
"""

from __future__ import annotations

from typing import Any

import numpy as np

NEAR_BEST_CP = 25
DRAW_BAND_CP = 40
# A position is "near zero" when the teacher calls it within this of equality. These are
# the positions where an additive correction is most dangerous: the two sides of a
# sibling comparison are nearly equal, so an arbitrarily small nudge flips it. V2 traced
# the first fixture break -- a repetition defence -- to exactly this.
NEAR_ZERO_CP = 30
# Material units at or below this count as an endgame for the per-phase split.
ENDGAME_UNITS = 10

# The frozen V3 composite. Predeclared in NNUE_V3_FINAL_PLAN.md section 7 before any
# checkpoint existed, and not re-tuned after any arena.
WEIGHTS: dict[str, float] = {
    "preserved_rate": 120.0,
    "top_move_accuracy_gain": 60.0,
    "regret_reduction_cp": 1.0,
    "p95_harmful_regret_cp": -3.0,
    "p99_harmful_regret_cp": -8.0,
    "near_zero_sign_flip_rate": -40.0,
    "draw_preservation_rate": 25.0,
    "mae_gain_cp": 0.02,
}


def ranking_metrics(
    correction: np.ndarray, enc: dict[str, Any], group_ids: np.ndarray
) -> dict[str, float]:
    """Per-group move-selection quality for the control and for the candidate.

    Everything here is about *which move the engine would pick* from the group, because
    that is the only thing the deployed evaluation is ultimately consumed for.
    """
    offsets, regret, base = enc["offsets"], enc["regret"], enc["base"]
    phase_units = enc["phase_units"]
    sf = enc["sf"]
    deployed = base + correction

    n = len(group_ids)
    ctrl_regret = np.zeros(n, dtype=np.float64)
    cand_regret = np.zeros(n, dtype=np.float64)
    ctrl_top = np.zeros(n, dtype=np.float64)
    cand_top = np.zeros(n, dtype=np.float64)
    is_endgame = np.zeros(n, dtype=bool)
    preserved_total = 0
    preserved_kept = 0
    repaired = 0
    broken = 0
    pair_ok_ctrl = 0
    pair_ok_cand = 0
    pair_n = 0

    for k, gi in enumerate(group_ids):
        lo, hi = int(offsets[gi]), int(offsets[gi + 1])
        r = regret[lo:hi]
        b = base[lo:hi]
        d = deployed[lo:hi]
        ci = int(np.argmin(b))
        di = int(np.argmin(d))
        ctrl_regret[k] = r[ci]
        cand_regret[k] = r[di]
        ctrl_top[k] = float(r[ci] == 0.0)
        cand_top[k] = float(r[di] == 0.0)
        # The parent's own material phase is not stored per group, so the first child's
        # units stand in: every child is one move from the parent.
        is_endgame[k] = phase_units[lo] <= ENDGAME_UNITS
        was_ok = r[ci] <= NEAR_BEST_CP
        now_ok = r[di] <= NEAR_BEST_CP
        if was_ok:
            preserved_total += 1
            preserved_kept += int(now_ok)
            broken += int(not now_ok)
        elif now_ok:
            repaired += 1
        worse_than = r[:, None] < r[None, :]
        pair_n += int(worse_than.sum())
        pair_ok_ctrl += int((worse_than & (b[:, None] < b[None, :])).sum())
        pair_ok_cand += int((worse_than & (d[:, None] < d[None, :])).sum())

    # The harmful tail: the regret this candidate *adds* to the control's own choice.
    added = np.maximum(cand_regret - ctrl_regret, 0.0)

    def phase_reduction(sel: np.ndarray) -> float:
        if not sel.any():
            return 0.0
        return float(np.mean(ctrl_regret[sel]) - np.mean(cand_regret[sel]))

    # Near-zero sign flips, measured per child rather than per group: a correction that
    # moves an evaluation across zero in a position the teacher calls equal is the
    # mechanism that turned V1's draws into losses.
    rows = np.concatenate([np.arange(offsets[g], offsets[g + 1]) for g in group_ids])
    near_zero = np.abs(sf[rows]) <= NEAR_ZERO_CP
    if near_zero.any():
        b0 = base[rows][near_zero]
        d0 = deployed[rows][near_zero]
        flips = (np.sign(b0) != np.sign(d0)) & (np.abs(b0) > 1e-9)
        sign_flip_rate = float(np.mean(flips))
    else:
        sign_flip_rate = 0.0

    return {
        "groups": float(n),
        "control_regret_mean": float(np.mean(ctrl_regret)),
        "candidate_regret_mean": float(np.mean(cand_regret)),
        "regret_reduction_cp": float(np.mean(ctrl_regret) - np.mean(cand_regret)),
        "regret_reduction_median_cp": float(
            np.median(ctrl_regret) - np.median(cand_regret)
        ),
        "p90_harmful_regret_cp": float(np.percentile(added, 90)) if added.size else 0.0,
        "p95_harmful_regret_cp": float(np.percentile(added, 95)) if added.size else 0.0,
        "p99_harmful_regret_cp": float(np.percentile(added, 99)) if added.size else 0.0,
        "max_harmful_regret_cp": float(added.max()) if added.size else 0.0,
        "harmed_group_rate": float(np.mean(added > 0.0)) if added.size else 0.0,
        "top_move_accuracy_control": float(np.mean(ctrl_top)),
        "top_move_accuracy_candidate": float(np.mean(cand_top)),
        "top_move_accuracy_gain": float(np.mean(cand_top) - np.mean(ctrl_top)),
        "preserved_rate": float(preserved_kept / preserved_total)
        if preserved_total
        else 1.0,
        "preserved_total": float(preserved_total),
        "broken": float(broken),
        "repaired": float(repaired),
        "pair_accuracy_control": float(pair_ok_ctrl / pair_n) if pair_n else 0.0,
        "pair_accuracy_candidate": float(pair_ok_cand / pair_n) if pair_n else 0.0,
        "pair_accuracy_gain": float((pair_ok_cand - pair_ok_ctrl) / pair_n)
        if pair_n
        else 0.0,
        "regret_reduction_endgame_cp": phase_reduction(is_endgame),
        "regret_reduction_middlegame_cp": phase_reduction(~is_endgame),
        "endgame_groups": float(is_endgame.sum()),
        "middlegame_groups": float((~is_endgame).sum()),
        "near_zero_sign_flip_rate": sign_flip_rate,
    }


def value_metrics(
    correction: np.ndarray, enc: dict[str, Any], rows: np.ndarray
) -> dict[str, float]:
    """Calibration and draw preservation. Reported in full, barely weighted."""
    base, sf = enc["base"][rows], enc["sf"][rows]
    corr = correction[rows]
    mae_ctrl = float(np.mean(np.abs(base - sf)))
    mae_cand = float(np.mean(np.abs(base + corr - sf)))

    drawish = np.abs(sf) <= DRAW_BAND_CP
    if drawish.any():
        allow = np.maximum(np.abs(base[drawish]), np.abs(sf[drawish]))
        excess = np.maximum(np.abs(base[drawish] + corr[drawish]) - allow, 0.0)
        draw_ok = float(np.mean(excess <= 1.0))
        draw_excess = float(np.mean(excess))
    else:
        draw_ok, draw_excess = 1.0, 0.0

    return {
        "mae_control": mae_ctrl,
        "mae_candidate": mae_cand,
        "mae_gain_cp": mae_ctrl - mae_cand,
        "draw_preservation_rate": draw_ok,
        "draw_excess_cp": draw_excess,
        "mean_abs_correction": float(np.mean(np.abs(corr))),
        "correction_bias_cp": float(np.mean(corr)),
        "fire_rate": float(np.mean(np.abs(corr) > 0.0)),
    }


def composite(metrics: dict[str, float]) -> float:
    """The frozen V3 selection score.

    Rates enter as (rate - 1) so an untouched checkpoint scores 0 on them and every
    imperfection is a debit, which keeps V2's sign convention and makes the number
    readable as "how much worse than leaving the control alone". The two harmful-regret
    percentiles are divided by 100 so a centipawn tail and a rate share one scale.
    """
    return (
        WEIGHTS["preserved_rate"] * (metrics["preserved_rate"] - 1.0)
        + WEIGHTS["top_move_accuracy_gain"] * metrics["top_move_accuracy_gain"]
        + WEIGHTS["regret_reduction_cp"] * metrics["regret_reduction_cp"]
        + WEIGHTS["p95_harmful_regret_cp"] * metrics["p95_harmful_regret_cp"] / 100.0
        + WEIGHTS["p99_harmful_regret_cp"] * metrics["p99_harmful_regret_cp"] / 100.0
        + WEIGHTS["near_zero_sign_flip_rate"] * metrics["near_zero_sign_flip_rate"]
        + WEIGHTS["draw_preservation_rate"] * (metrics["draw_preservation_rate"] - 1.0)
        + WEIGHTS["mae_gain_cp"] * metrics["mae_gain_cp"]
    )
