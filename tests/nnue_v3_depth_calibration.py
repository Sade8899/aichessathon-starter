"""Measure the control's realised search depth as a function of the clock.

The V2 strength evidence was all taken at 1000 ms + 100 ms. The control's time
manager spends ``time_left_ms / 32`` on a move, so that clock buys ~31 ms of
search and the agents played at depth ~1.2. Rated games run at 120 s + 0.5 s,
which buys 3.75 s on move one. This script measures where the depth actually
lands so V3 can choose an arena clock that is representative rather than
convenient. Control only -- no candidate is involved.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import statistics
import sys
from pathlib import Path
from typing import Any

import chess

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "tests" / "results" / "nnue" / "v3"


def load_agent(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def sample_positions(count: int, seed: int) -> list[str]:
    """Positions reached by random-ish play, spread across the game."""
    import random

    rng = random.Random(seed)
    out: list[str] = []
    while len(out) < count:
        board = chess.Board()
        target = rng.randint(4, 60)
        for _ in range(target):
            moves = list(board.legal_moves)
            if not moves:
                break
            board.push(rng.choice(moves))
        if board.is_game_over():
            continue
        out.append(board.fen())
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--positions", type=int, default=24)
    parser.add_argument("--seed", type=int, default=20260910)
    parser.add_argument(
        "--clocks",
        default="1000,2000,4000,8000,16000,30000,60000,120000",
        help="values of time_left_ms to probe",
    )
    args = parser.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    agent = load_agent(ROOT / "agent.py", "control_depth_probe")
    fens = sample_positions(args.positions, args.seed)

    # Warm numba and the transposition tables once so compilation does not land
    # inside a measured budget.
    agent.get_move(chess.Board().fen(), 5000)

    rows: list[dict[str, Any]] = []
    for clock in [int(x) for x in args.clocks.split(",")]:
        depths: list[int] = []
        nodes: list[int] = []
        seconds: list[float] = []
        for fen in fens:
            agent._engine.__init__()  # fresh game state per probe
            agent.get_move(fen, clock)
            stats = agent._engine.stats
            depths.append(int(stats["depth"]))
            nodes.append(int(stats["nodes"]))
            seconds.append(float(stats["seconds"]))
        row = {
            "time_left_ms": clock,
            "budget_ms": round(clock / 32, 1),
            "mean_depth": round(statistics.fmean(depths), 2),
            "median_depth": statistics.median(depths),
            "min_depth": min(depths),
            "max_depth": max(depths),
            "mean_nodes": round(statistics.fmean(nodes), 1),
            "mean_seconds": round(statistics.fmean(seconds), 4),
            "nps": round(statistics.fmean(nodes) / max(1e-9, statistics.fmean(seconds)), 1),
        }
        rows.append(row)
        print(
            f"clock {clock:>7} ms  budget {row['budget_ms']:>7} ms  "
            f"depth mean {row['mean_depth']:>5} median {row['median_depth']:>2} "
            f"range {row['min_depth']}-{row['max_depth']}  "
            f"nodes {row['mean_nodes']:>10}  {row['mean_seconds']:.3f} s",
            flush=True,
        )

    payload = {
        "positions": args.positions,
        "seed": args.seed,
        "control_sha256": __import__("hashlib").sha256((ROOT / "agent.py").read_bytes()).hexdigest(),
        "rows": rows,
    }
    (OUT / "depth_calibration.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\nwrote {OUT / 'depth_calibration.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
