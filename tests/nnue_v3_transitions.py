"""Where the candidate's lost points come from, opening by opening.

A head-to-head says the candidate is worse; it does not say what changed. Because the
null run (control against control) was played on the *same seed*, it drew the same
opening book, so for every opening there is a control-vs-control outcome and a
candidate-vs-control outcome and the two can be compared game for game.

That turns a score difference into a transition table: how many held draws became losses,
how many wins became draws, and so on. V1's failure had exactly this signature -- draws
collapsing into losses -- and the relative form was built to prevent it, so whether it
did is worth measuring rather than assuming.

Game length is reported alongside, because a correction that loses points early and one
that loses them in long endgames are different faults.
"""

from __future__ import annotations

import argparse
import collections
import csv
import json
import pathlib
import statistics
from typing import Any

REPO = pathlib.Path(__file__).resolve().parent.parent
V3 = REPO / "tests" / "results" / "nnue" / "v3"


def read(path: pathlib.Path) -> dict[tuple[str, bool], dict[str, Any]]:
    """Index rows by (opening, which side the tested agent had)."""
    rows: dict[tuple[str, bool], dict[str, Any]] = {}
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get("candidate_score") in (None, ""):
                continue
            key = (row["opening_id"], row["candidate_is_white"] == "True")
            rows[key] = row
    return rows


def outcome(score: float) -> str:
    return "win" if score > 0.75 else ("loss" if score < 0.25 else "draw")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", required=True)
    ap.add_argument("--candidate-label", default="confirm_eval")
    ap.add_argument("--null-label", default="null_control")
    args = ap.parse_args()

    cand = read(V3 / args.tag / f"{args.candidate_label}.csv")
    null = read(V3 / args.tag / f"{args.null_label}.csv")
    shared = sorted(set(cand) & set(null))
    if not shared:
        raise SystemExit("no shared openings; were the two runs played on the same seed?")

    table: collections.Counter[str] = collections.Counter()
    delta = 0.0
    plies_by_transition: dict[str, list[int]] = collections.defaultdict(list)
    for key in shared:
        before = outcome(float(null[key]["candidate_score"]))
        after = outcome(float(cand[key]["candidate_score"]))
        table[f"{before}->{after}"] += 1
        delta += float(cand[key]["candidate_score"]) - float(null[key]["candidate_score"])
        plies_by_transition[f"{before}->{after}"].append(int(cand[key]["plies"]))

    lost = sum(v for k, v in table.items() if k in {"draw->loss", "win->draw", "win->loss"})
    gained = sum(v for k, v in table.items() if k in {"loss->draw", "draw->win", "loss->win"})

    payload: dict[str, Any] = {
        "tag": args.tag,
        "shared_games": len(shared),
        "paired_score_delta_per_game": round(delta / len(shared), 4),
        "transitions": dict(sorted(table.items(), key=lambda kv: -kv[1])),
        "transitions_costing_points": lost,
        "transitions_gaining_points": gained,
        "draw_to_loss": table["draw->loss"],
        "loss_to_draw": table["loss->draw"],
        "win_to_draw": table["win->draw"],
        "draw_to_win": table["draw->win"],
        "win_to_loss": table["win->loss"],
        "loss_to_win": table["loss->win"],
        "mean_plies_by_transition": {
            k: round(statistics.fmean(v), 1) for k, v in sorted(plies_by_transition.items())
        },
        "mean_plies_candidate": round(
            statistics.fmean(int(cand[k]["plies"]) for k in shared), 1
        ),
        "mean_plies_null": round(
            statistics.fmean(int(null[k]["plies"]) for k in shared), 1
        ),
        "note": (
            "the null is the control playing itself on the same openings, so a "
            "transition is attributable to the candidate rather than to the book"
        ),
    }
    out = V3 / args.tag / "transitions.json"
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    for key, value in payload.items():
        if key != "note":
            print(f"  {key}: {value}")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
