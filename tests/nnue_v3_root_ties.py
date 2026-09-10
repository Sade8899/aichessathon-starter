"""How much headroom does a root-ordering hook actually have?

The ORDER integration mode is attractive because it cannot move a drawn evaluation off
zero, which is the mechanism V2 traced from a small residual to a broken repetition
defence. But attractive is not the same as useful, and the control's own search structure
bounds what a root hook can possibly change:

- the control re-sorts root moves by the previous iteration's completed scores, so the
  initial ordering only survives inside iteration 1;
- once an iteration completes, the move played is argmax over the completed scores, and
  the initial ordering is irrelevant *except* where two moves tie exactly.

So the honest ceiling for a safe root hook is the tie rate: how often the control's own
search is indifferent between two or more root moves, and how often the tied set holds a
better move than the one the control's list order happens to hand it. Anything a hook
does outside a tie is overruling the control's search, which is the V1/V2 failure mode in
another costume.

This measures that ceiling before any ORDER-mode code is written.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import pathlib
import random
import sys
import time
from typing import Any

import chess

REPO = pathlib.Path(__file__).resolve().parent.parent
OUT = REPO / "tests" / "results" / "nnue" / "v3"
GROUPS = REPO / "tests" / "results" / "nnue" / "groups"


def load_control() -> Any:
    spec = importlib.util.spec_from_file_location("root_tie_control", REPO / "agent.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["root_tie_control"] = module
    spec.loader.exec_module(module)
    return module


def sample_groups(count: int, seed: int) -> list[dict[str, Any]]:
    """Validation-split parents only, so this reads nothing the trainer selects on."""
    rng = random.Random(seed)
    pool: list[dict[str, Any]] = []
    for path in sorted(GROUPS.glob("groups-*.jsonl")):
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    group = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if group.get("split") == "validation":
                    pool.append(group)
    rng.shuffle(pool)
    return pool[:count]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--positions", type=int, default=300)
    ap.add_argument("--clock-ms", type=int, default=8000)
    ap.add_argument("--seed", type=int, default=20260910)
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    control = load_control()
    groups = sample_groups(args.positions, args.seed)
    print(f"{len(groups)} validation parents at {args.clock_ms} ms")

    control.get_move(chess.Board().fen(), 5000)  # warm numba

    tied_positions = 0
    tie_holds_better = 0
    tie_recoverable_cp = 0.0
    total_regret = 0.0
    played_matches_control_static = 0
    n = 0
    tie_sizes: list[int] = []
    started = time.perf_counter()

    for group in groups:
        board = chess.Board(group["parent_fen"])
        if board.is_game_over():
            continue
        control._engine.__init__()
        played = control.get_move(board.fen(), args.clock_ms)
        scores = dict(control._engine.completed_scores)
        if not scores:
            continue
        n += 1
        regret_by_uci = {c["uci"]: float(c["regret"]) for c in group["candidates"]}
        best = max(scores.values())
        tied = [m for m, s in scores.items() if s == best]
        tie_sizes.append(len(tied))
        played_regret = regret_by_uci.get(played)
        if played_regret is not None:
            total_regret += played_regret
        if len(tied) > 1:
            tied_positions += 1
            known = [
                (regret_by_uci[m.uci()], m.uci()) for m in tied if m.uci() in regret_by_uci
            ]
            if len(known) > 1 and played_regret is not None:
                best_in_tie = min(r for r, _ in known)
                if best_in_tie < played_regret:
                    tie_holds_better += 1
                    tie_recoverable_cp += played_regret - best_in_tie
        if group.get("control_static_best_uci") == played:
            played_matches_control_static += 1

    elapsed = time.perf_counter() - started
    payload = {
        "positions_scored": n,
        "clock_ms": args.clock_ms,
        "seed": args.seed,
        "mean_root_tie_size": round(sum(tie_sizes) / max(1, len(tie_sizes)), 3),
        "tied_root_rate": round(tied_positions / max(1, n), 4),
        "tie_holds_better_move_rate": round(tie_holds_better / max(1, n), 4),
        "mean_recoverable_cp_per_position": round(tie_recoverable_cp / max(1, n), 3),
        "mean_played_regret_cp": round(total_regret / max(1, n), 2),
        "played_equals_control_static_best_rate": round(
            played_matches_control_static / max(1, n), 4
        ),
        "elapsed_seconds": round(elapsed, 1),
        "note": (
            "tie_holds_better_move_rate is the ceiling for a root hook that only breaks "
            "ties; mean_recoverable_cp_per_position is the regret such a hook could "
            "recover if it broke every tie perfectly"
        ),
    }
    (OUT / "root_ties.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    for key, value in payload.items():
        if key != "note":
            print(f"  {key}: {value}")
    print(f"wrote {OUT / 'root_ties.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
