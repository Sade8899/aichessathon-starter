"""Repeated cold-process-state timing through the real agent interface."""

import argparse
import json
import statistics
import time

import chess
from passive_arena import percentile
from selection import load, positions, sources


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--configs", default="reference,objective,narrow")
    parser.add_argument("--clock", type=int, default=5000)
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--positions", type=int, default=24)
    args = parser.parse_args()
    names = args.configs.split(",")
    modules = {name: load("timed_" + name, sources()[name]) for name in names}
    rows = {name: [] for name in names}
    for repeat in range(args.repeats):
        for index, fen in enumerate(positions(args.positions)):
            offset = (index + repeat) % len(names)
            for name in names[offset:] + names[:offset]:
                module = modules[name]
                module._engine = module.Engine()
                if hasattr(module, "_eval_table"):
                    module._eval_table[:] = [None] * len(module._eval_table)
                started = time.perf_counter()
                move = module.get_move(fen, args.clock)
                elapsed = time.perf_counter() - started
                assert chess.Move.from_uci(move) in chess.Board(fen).legal_moves
                assert elapsed * 1000 < args.clock
                rows[name].append({**module._engine.stats, "elapsed": elapsed})
    result = {}
    for name, data in rows.items():
        ratios = [r["model_seconds"] / r["seconds"] for r in data]
        result[name] = {
            "mean_depth": statistics.mean(r["depth"] for r in data),
            "depths": [r["depth"] for r in data],
            "median_ms": statistics.median(r["elapsed"] for r in data) * 1000,
            "worst_ms": max(r["elapsed"] for r in data) * 1000,
            "nps": sum(r["nodes"] for r in data) / sum(r["elapsed"] for r in data),
            "overhead_median": statistics.median(ratios),
            "overhead_p95": percentile(ratios, 0.95),
        }
    baseline_depth = result[names[0]]["mean_depth"]
    for name in names[1:]:
        assert result[name]["mean_depth"] >= baseline_depth - 0.05, result
    print(
        json.dumps(
            {
                "clock_ms": args.clock,
                "repeats": args.repeats,
                "positions": args.positions,
                "cold_cache": True,
                "configs": result,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
