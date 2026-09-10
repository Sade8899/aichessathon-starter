"""How many concurrent games can this machine run before the clock stops being honest?

Wall-clock time controls and parallelism interact badly: if N workers oversubscribe the
CPU, every agent gets less work done per millisecond, the realised search depth falls,
and the arena measures contention instead of chess. This runs the *control against
itself* at several worker counts and reports both throughput and the realised depth. The
fastest configuration whose depth still matches the single-worker reference is the one
V3 uses. More containers is not more valid throughput.

i7-8700: 6 physical cores, 12 logical. Agents are single-threaded Python.
"""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
import pathlib
import statistics
import sys
import time
from typing import Any

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))

for _var in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ.setdefault(_var, "1")

OUT = REPO / "tests" / "results" / "nnue" / "v3"


def _play(entry: dict[str, Any]) -> dict[str, Any]:
    import importlib.util

    import chess

    if "control" not in globals().get("_cache", {}):
        globals().setdefault("_cache", {})
        spec = importlib.util.spec_from_file_location("wc_control", REPO / "agent.py")
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules["wc_control"] = module
        spec.loader.exec_module(module)
        globals()["_cache"]["control"] = module
    base = globals()["_cache"]["control"]

    white = base
    black = base
    engines = {True: base.Engine(), False: base.Engine()}

    board = chess.Board(entry["fen"])
    clocks = {True: float(entry["clock_ms"]), False: float(entry["clock_ms"])}
    depths: list[int] = []
    started = time.perf_counter()
    plies = 0
    flagged = False
    while plies < entry["max_plies"] and not board.is_game_over(claim_draw=True):
        side = board.turn
        eng = engines[side]
        t0 = time.perf_counter()
        uci = eng.choose(board.fen(), int(clocks[side]))
        spent = (time.perf_counter() - t0) * 1000.0
        depths.append(int(eng.stats.get("depth", 0)))
        clocks[side] -= spent
        if clocks[side] <= 0:
            flagged = True
            break
        clocks[side] += entry["increment_ms"]
        move = chess.Move.from_uci(uci)
        if move not in board.legal_moves:
            break
        board.push(move)
        plies += 1
    del white, black
    return {
        "wall_seconds": time.perf_counter() - started,
        "plies": plies,
        "mean_depth": statistics.fmean(depths) if depths else 0.0,
        "flagged": flagged,
    }


def openings(count: int, seed: int) -> list[str]:
    import random

    import chess

    rng = random.Random(seed)
    out: list[str] = []
    while len(out) < count:
        board = chess.Board()
        for _ in range(rng.randint(4, 12)):
            moves = list(board.legal_moves)
            if not moves:
                break
            board.push(rng.choice(moves))
        if not board.is_game_over():
            out.append(board.fen())
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=8, help="games per worker-count setting")
    ap.add_argument("--clock-ms", type=int, default=8000)
    ap.add_argument("--increment-ms", type=int, default=500)
    ap.add_argument("--max-plies", type=int, default=120)
    ap.add_argument("--workers", default="1,2,4,6")
    ap.add_argument("--seed", type=int, default=20260910)
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    fens = openings(args.games, args.seed)
    rows: list[dict[str, Any]] = []
    reference_depth: float | None = None

    for workers in [int(x) for x in args.workers.split(",")]:
        entries = [
            {
                "fen": f,
                "clock_ms": args.clock_ms,
                "increment_ms": args.increment_ms,
                "max_plies": args.max_plies,
            }
            for f in fens
        ]
        started = time.perf_counter()
        if workers == 1:
            results = [_play(e) for e in entries]
        else:
            with mp.Pool(workers) as pool:
                results = pool.map(_play, entries)
        wall = time.perf_counter() - started
        depth = statistics.fmean(r["mean_depth"] for r in results)
        if reference_depth is None:
            reference_depth = depth
        row = {
            "workers": workers,
            "games": len(results),
            "wall_seconds": round(wall, 1),
            "games_per_hour": round(3600.0 * len(results) / wall, 1),
            "mean_depth": round(depth, 3),
            "depth_vs_1_worker_pct": round(100.0 * (depth / reference_depth - 1.0), 2),
            "mean_plies": round(statistics.fmean(r["plies"] for r in results), 1),
            "flags": sum(1 for r in results if r["flagged"]),
        }
        rows.append(row)
        print(
            f"workers {workers:>2}  {row['games_per_hour']:>8} games/h  "
            f"depth {row['mean_depth']:.3f} ({row['depth_vs_1_worker_pct']:+.2f}% vs 1)  "
            f"flags {row['flags']}",
            flush=True,
        )

    payload = {
        "clock_ms": args.clock_ms,
        "increment_ms": args.increment_ms,
        "games_per_setting": args.games,
        "seed": args.seed,
        "rows": rows,
        "note": "depth_vs_1_worker_pct is the clock-distortion measure; throughput without it is meaningless",
    }
    (OUT / "worker_calibration.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\nwrote {OUT / 'worker_calibration.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
