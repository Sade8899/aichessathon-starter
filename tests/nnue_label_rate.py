"""Measure the real labelling rate on ACTUAL corpus positions, 6 processes.

The earlier calibration used six hand-picked positions -- the opening position and some
simple endgames -- which search far faster per node than a real middlegame. It predicted
92,033 positions/hour and the real run delivered 26,400. This measures the corpus.
"""

import json
import multiprocessing as mp
import pathlib
import random
import statistics
import sys
import time

import chess
import chess.engine

REPO = pathlib.Path(
    "C:/Users/44740/Desktop/Comp2526/Personal project/Chessatohon optiver/aichessathon-starter"
)
SF = (
    REPO / "tests" / "external_engines" / "stockfish" / "stockfish"
    / "stockfish-windows-x86-64-universal.exe"
)
SAMPLE = 90  # positions per worker per node limit


def worker(args):
    nodes, fens = args
    eng = chess.engine.SimpleEngine.popen_uci(str(SF))
    eng.configure({"Threads": 1, "Hash": 64})
    depths = []
    t0 = time.perf_counter()
    try:
        for fen in fens:
            info = eng.analyse(chess.Board(fen), chess.engine.Limit(nodes=nodes), game=object())
            depths.append(int(info.get("depth") or 0))
    finally:
        eng.quit()
    return nodes, time.perf_counter() - t0, len(fens), depths


def main() -> None:
    rows = [json.loads(l) for l in (REPO / "tests/results/nnue/dataset/positions.jsonl").open(encoding="utf-8")]
    rng = random.Random(4242)
    rng.shuffle(rows)
    pool_fens = [r["fen"] for r in rows[: SAMPLE * 6 * 4]]

    print(f"corpus sample drawn from {len(rows):,} real positions")
    results = {}
    cursor = 0
    for nodes in (50_000, 100_000, 200_000):
        jobs = []
        for _ in range(6):
            jobs.append((nodes, pool_fens[cursor : cursor + SAMPLE]))
            cursor += SAMPLE
        started = time.perf_counter()
        with mp.Pool(6) as pool:
            out = pool.map(worker, jobs)
        wall = time.perf_counter() - started
        total = sum(o[2] for o in out)
        depths = [d for o in out for d in o[3]]
        rate = total / wall * 3600
        results[nodes] = {
            "positions": total,
            "wall_seconds": round(wall, 1),
            "positions_per_hour_6proc": round(rate),
            "ms_per_position_per_worker": round(wall / (total / 6) * 1000, 1),
            "median_depth": statistics.median(depths),
            "mean_depth": round(statistics.fmean(depths), 2),
            "min_depth": min(depths),
            "hours_for_129k": round(129_000 / rate, 2),
        }
        r = results[nodes]
        print(
            f"  nodes={nodes:>7}  {r['positions_per_hour_6proc']:>8,}/h  "
            f"median depth {r['median_depth']:>4}  mean {r['mean_depth']:>5}  "
            f"min {r['min_depth']:>2}  129k in {r['hours_for_129k']}h"
        )
    (REPO / "tests/results/nnue/dataset/label_rate_real.json").write_text(
        json.dumps(results, indent=2), encoding="utf-8"
    )
    print("wrote tests/results/nnue/dataset/label_rate_real.json")


if __name__ == "__main__":
    sys.exit(main())
