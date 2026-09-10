"""Paired search-level probe: the regret of the move the engine actually plays.

Every offline metric V1, V2 and V3 have used so far scores the *static* evaluation's
ordering of siblings. The engine does not play the static ordering. Measured on 200
validation parents at 8,000 ms, the move the control plays equals its own static-eval
best move only 30.5% of the time -- the search overrules the evaluation seven times in
ten.

So a static composite can improve while the played move does not change at all, or
changes for the worse. This probe closes that gap for a few minutes of CPU: it runs the
real agent, at a real clock, on labelled parents, and looks the played move up in the
Stockfish labels the group already carries.

It is paired -- the same positions, the same clock, the same process -- and it reports
depth, nodes and time alongside regret, so a candidate that buys regret by spending
nodes is visible as such. This is a *selection* aid on the validation split, not an
acceptance test; the arena remains the acceptance test.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import pathlib
import random
import statistics
import sys
import time
from typing import Any

import chess

REPO = pathlib.Path(__file__).resolve().parent.parent
OUT = REPO / "tests" / "results" / "nnue" / "v3"
GROUPS = REPO / "tests" / "results" / "nnue" / "groups"


def load_agent(path: pathlib.Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def sample_groups(count: int, seed: int, split: str) -> list[dict[str, Any]]:
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
                if group.get("split") == split:
                    pool.append(group)
    rng.shuffle(pool)
    return pool[:count]


def run(module: Any, groups: list[dict[str, Any]], clock_ms: int) -> dict[str, Any]:
    regrets: list[float] = []
    depths: list[int] = []
    nodes: list[int] = []
    seconds: list[float] = []
    unlabelled = 0
    per_position: list[dict[str, Any]] = []

    module.get_move(chess.Board().fen(), 5000)  # warm numba before anything is timed

    for group in groups:
        board = chess.Board(group["parent_fen"])
        if board.is_game_over():
            continue
        module._engine = module.Engine()
        played = module.get_move(board.fen(), clock_ms)
        stats = dict(module._engine.stats)
        table = {c["uci"]: float(c["regret"]) for c in group["candidates"]}
        regret = table.get(played)
        if regret is None:
            # The move was outside the labelled candidate set. Charging it the worst
            # labelled regret would flatter whichever agent plays inside the set more
            # often, so these are counted and excluded rather than imputed.
            unlabelled += 1
            continue
        regrets.append(regret)
        depths.append(int(stats.get("depth", 0)))
        nodes.append(int(stats.get("nodes", 0)))
        seconds.append(float(stats.get("seconds", 0.0)))
        per_position.append(
            {"group_id": group["group_id"], "played": played, "regret": regret}
        )

    return {
        "scored": len(regrets),
        "unlabelled_moves": unlabelled,
        "mean_regret_cp": round(statistics.fmean(regrets), 3) if regrets else 0.0,
        "median_regret_cp": round(statistics.median(regrets), 3) if regrets else 0.0,
        "p90_regret_cp": round(sorted(regrets)[int(0.90 * len(regrets))], 1) if regrets else 0.0,
        "p99_regret_cp": round(sorted(regrets)[min(len(regrets) - 1, int(0.99 * len(regrets)))], 1)
        if regrets
        else 0.0,
        "top_move_rate": round(sum(1 for r in regrets if r == 0.0) / len(regrets), 4)
        if regrets
        else 0.0,
        "mean_depth": round(statistics.fmean(depths), 3) if depths else 0.0,
        "mean_nodes": round(statistics.fmean(nodes), 1) if nodes else 0.0,
        "mean_seconds": round(statistics.fmean(seconds), 4) if seconds else 0.0,
        "per_position": per_position,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidate", type=pathlib.Path, required=True)
    ap.add_argument("--tag", required=True)
    ap.add_argument("--positions", type=int, default=250)
    ap.add_argument("--clock-ms", type=int, default=8000)
    ap.add_argument("--seed", type=int, default=20260911)
    ap.add_argument("--split", default="validation")
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    groups = sample_groups(args.positions, args.seed, args.split)
    print(f"{len(groups)} {args.split} parents at {args.clock_ms} ms")

    control = load_agent(REPO / "agent.py", "probe_control")
    candidate = load_agent(args.candidate, "probe_candidate")

    ctl = run(control, groups, args.clock_ms)
    cand = run(candidate, groups, args.clock_ms)

    # Pair on group id so the difference is taken within a position, not between means
    # of two different position sets.
    ctl_by_id = {r["group_id"]: r["regret"] for r in ctl["per_position"]}
    cand_by_id = {r["group_id"]: r["regret"] for r in cand["per_position"]}
    shared = sorted(set(ctl_by_id) & set(cand_by_id))
    deltas = [cand_by_id[g] - ctl_by_id[g] for g in shared]
    changed = [
        g
        for g in shared
        if next(r["played"] for r in ctl["per_position"] if r["group_id"] == g)
        != next(r["played"] for r in cand["per_position"] if r["group_id"] == g)
    ]

    payload = {
        "tag": args.tag,
        "candidate": str(args.candidate),
        "clock_ms": args.clock_ms,
        "split": args.split,
        "seed": args.seed,
        "control": {k: v for k, v in ctl.items() if k != "per_position"},
        "candidate_metrics": {k: v for k, v in cand.items() if k != "per_position"},
        "paired_positions": len(shared),
        "moves_changed": len(changed),
        "moves_changed_rate": round(len(changed) / max(1, len(shared)), 4),
        "paired_mean_regret_delta_cp": round(statistics.fmean(deltas), 3) if deltas else 0.0,
        "paired_improved": sum(1 for d in deltas if d < 0),
        "paired_worsened": sum(1 for d in deltas if d > 0),
        "paired_worst_regression_cp": round(max(deltas), 1) if deltas else 0.0,
        "paired_best_improvement_cp": round(min(deltas), 1) if deltas else 0.0,
        "note": (
            "negative paired_mean_regret_delta_cp means the candidate plays better moves; "
            "moves_changed_rate bounds how much any of this can matter"
        ),
    }
    (OUT / f"search_probe_{args.tag}.json").write_text(
        json.dumps(payload, indent=2), encoding="utf-8"
    )
    for key, value in payload.items():
        if key not in {"per_position", "note"}:
            print(f"  {key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
