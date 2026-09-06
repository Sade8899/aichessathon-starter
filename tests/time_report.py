"""Per-game clock reconstruction for the time-allocation arena.

`numba_arena_summary.py` stays the canonical merge and owns every structural
assertion about matched cases. This adds only what a time-allocation experiment
needs and that summary does not carry: the clock each game actually ended on,
rebuilt move by move the way `harness/referee.py` keeps it.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from typing import Any

from passive_arena import percentile

FAILED = ("flag", "illegal", "crash", "init", "both_failed")


def clock_trace(move_seconds: list[float], base_ms: int, increment_ms: int) -> dict[str, Any]:
    """Replay the referee's clock over one side's moves, in the referee's order."""
    clock = float(base_ms)
    lowest = clock
    for seconds in move_seconds:
        clock -= seconds * 1000.0
        lowest = min(lowest, clock)
        clock += increment_ms
    return {
        "moves": len(move_seconds),
        "clock_left_s": clock / 1000.0,
        "min_clock_s": lowest / 1000.0,
        "time_used_s": sum(move_seconds),
        "slowest_s": max(move_seconds) if move_seconds else 0.0,
        "average_s": statistics.mean(move_seconds) if move_seconds else 0.0,
        "flagged": lowest < 0,
    }


def game_row(row: dict[str, Any], base_ms: int, increment_ms: int) -> dict[str, Any]:
    telemetry = row["telemetry"]
    trace = clock_trace(row["move_seconds"], base_ms, increment_ms)
    return {
        "case": row["case"],
        "config": row["config"],
        "white": row["white"],
        "seed": row["seed"],
        "start_fen": row["start_fen"],
        "score": row["score"],
        "termination": row["termination"],
        "failure": row["failure"],
        "flag": row["termination"] == "flag",
        "illegal": row["termination"] == "illegal",
        "crash": row["termination"] in ("crash", "init", "both_failed"),
        "initialization_ms": row["initialization_ms"],
        "max_depth": max((t["depth"] for t in telemetry), default=0),
        "mean_depth": statistics.mean(t["depth"] for t in telemetry) if telemetry else 0.0,
        "total_nodes": sum(t["nodes"] for t in telemetry),
        "max_peak_rss_mb": max((t.get("peak_rss_mb", 0.0) for t in telemetry), default=0.0),
        **trace,
    }


def summarize(games: list[dict[str, Any]]) -> dict[str, Any]:
    scores = [g["score"] for g in games]
    return {
        "games": len(games),
        "wdl": [scores.count(1), scores.count(0.5), scores.count(0)],
        "score": statistics.mean(scores),
        "flags": sum(g["flag"] for g in games),
        "illegal_moves": sum(g["illegal"] for g in games),
        "crashes": sum(g["crash"] for g in games),
        "failures": sum(g["failure"] for g in games),
        "clock_reconstruction_flagged": sum(g["flagged"] for g in games),
        "terminations": dict(
            sorted({t: sum(g["termination"] == t for g in games) for t in
                    {g["termination"] for g in games}}.items())
        ),
        "median_moves": statistics.median(g["moves"] for g in games),
        "total_moves": sum(g["moves"] for g in games),
        "mean_depth": statistics.mean(g["mean_depth"] for g in games),
        "max_depth": max(g["max_depth"] for g in games),
        "median_time_used_s": statistics.median(g["time_used_s"] for g in games),
        "median_clock_left_s": statistics.median(g["clock_left_s"] for g in games),
        "min_clock_left_s": min(g["clock_left_s"] for g in games),
        "worst_min_clock_s": min(g["min_clock_s"] for g in games),
        "median_slowest_move_s": statistics.median(g["slowest_s"] for g in games),
        "worst_move_s": max(g["slowest_s"] for g in games),
        "p95_average_move_s": percentile([g["average_s"] for g in games], 0.95),
        "median_average_move_s": statistics.median(g["average_s"] for g in games),
        "total_nodes": sum(g["total_nodes"] for g in games),
        "max_initialization_ms": max(g["initialization_ms"] for g in games),
        "max_peak_rss_mb": max(g["max_peak_rss_mb"] for g in games),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=int, required=True)
    parser.add_argument("--base-ms", type=int, default=120_000)
    parser.add_argument("--increment-ms", type=int, default=500)
    parser.add_argument("--control", default="control")
    parser.add_argument("--candidate", default="candidate")
    args = parser.parse_args()
    raw, runs = [], []
    for line in sys.stdin:
        if line.startswith("RUN "):
            runs.append(json.loads(line[4:]))
        elif line.startswith('{"case":'):
            raw.append(json.loads(line))
    assert runs and all(run == runs[0] for run in runs), "Sources, clocks or seeds differ"
    assert runs[0]["base_ms"] == args.base_ms and runs[0]["increment_ms"] == args.increment_ms
    assert len(raw) == args.cases * 2, (len(raw), args.cases * 2)
    games = [game_row(row, args.base_ms, args.increment_ms) for row in raw]

    paired = []
    by_case: dict[int, dict[str, dict[str, Any]]] = {}
    for game in games:
        by_case.setdefault(game["case"], {})[game["config"]] = game
    assert sorted(by_case) == list(range(args.cases))
    for case in sorted(by_case):
        pair = by_case[case]
        assert set(pair) == {args.control, args.candidate}, case
        control, candidate = pair[args.control], pair[args.candidate]
        for field in ("start_fen", "seed", "white"):
            assert control[field] == candidate[field], (case, field)
        paired.append(
            {
                "case": case,
                "colour_pair": case // 2,
                "white": control["white"],
                "start_fen": control["start_fen"],
                "seed": control["seed"],
                "score_difference": candidate["score"] - control["score"],
                "depth_change": candidate["mean_depth"] - control["mean_depth"],
                "time_used_change_s": candidate["time_used_s"] - control["time_used_s"],
                "clock_left_change_s": candidate["clock_left_s"] - control["clock_left_s"],
                "control": control,
                "candidate": candidate,
            }
        )
    configs = {
        name: summarize([g for g in games if g["config"] == name])
        for name in (args.control, args.candidate)
    }
    totals = {
        "flags": sum(c["flags"] for c in configs.values()),
        "illegal_moves": sum(c["illegal_moves"] for c in configs.values()),
        "crashes": sum(c["crashes"] for c in configs.values()),
        "failures": sum(c["failures"] for c in configs.values()),
    }
    print(
        json.dumps(
            {
                "conditions": runs[0],
                "cases": args.cases,
                "games": len(games),
                "colour_pairs": args.cases // 2,
                "unique_openings": len({g["start_fen"] for g in games}),
                "configs": configs,
                "clean": all(value == 0 for value in totals.values()),
                "totals": totals,
                "mean_depth_change": statistics.mean(p["depth_change"] for p in paired),
                "mean_time_used_change_s": statistics.mean(p["time_used_change_s"] for p in paired),
                "mean_clock_left_change_s": statistics.mean(
                    p["clock_left_change_s"] for p in paired
                ),
                "paired": paired,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
