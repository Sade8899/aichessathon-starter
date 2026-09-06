"""Merge complete, disjoint colour-pair shards; never average shard percentiles."""

import argparse
import json
import math
import statistics
import sys
from typing import Any

from passive_arena import interval, percentile


def bounded_interval(differences: list[tuple[int, float]]) -> dict[str, Any]:
    result: dict[str, Any] = interval(differences)
    if result["ci95"][0] == result["ci95"][1]:
        count = len({group for group, _ in differences})
        radius = math.sqrt(2 * math.log(40) / count)
        centre = result["difference"]
        result["ci95"] = [max(-1, centre - radius), min(1, centre + radius)]
        result["method"] = "Hoeffding 95% bound for a degenerate bootstrap"
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=int, required=True)
    parser.add_argument("--control", default="submitted")
    parser.add_argument("--candidate", default="numba")
    args = parser.parse_args()
    rows, runs, summaries = [], [], []
    for line in sys.stdin:
        if line.startswith("RUN "):
            runs.append(json.loads(line[4:]))
        elif line.startswith("SUMMARY "):
            summaries.append(json.loads(line[8:]))
        elif line.startswith('{"case":'):
            rows.append(json.loads(line))
    assert runs and len(runs) == len(summaries), "An arena shard did not finish"
    assert all(run == runs[0] for run in runs), "Sources, clocks or seeds differ"
    assert len(rows) == args.cases * 2
    by_case: dict[int, dict[str, dict[str, Any]]] = {}
    for row in rows:
        assert not row["failure"], row
        configurations = by_case.setdefault(row["case"], {})
        assert row["config"] not in configurations, "Duplicate case/configuration"
        configurations[row["config"]] = row
    assert sorted(by_case) == list(range(args.cases))
    differences = []
    opening_differences = []
    openings = sorted({row["start_fen"] for row in rows})
    for case, pair in by_case.items():
        assert set(pair) == {args.control, args.candidate}
        a, b = pair[args.control], pair[args.candidate]
        for field in ("start_fen", "seed", "white", "opponent"):
            assert a[field] == b[field], (case, field)
        partner = by_case[case ^ 1][args.control]
        assert a["white"] != partner["white"]
        assert a["start_fen"] == partner["start_fen"] and a["seed"] == partner["seed"]
        assert a["opponent"] == partner["opponent"]
        differences.append((case // 2, b["score"] - a["score"]))
        opening_differences.append((openings.index(a["start_fen"]), b["score"] - a["score"]))
    configs = {}
    for name in (args.control, args.candidate):
        games = [row for row in rows if row["config"] == name]
        scores = [row["score"] for row in games]
        times = [t * 1000 for row in games for t in row["move_seconds"]]
        stats = [s for row in games for s in row["telemetry"]]
        ratios = [s["model_seconds"] / s["seconds"] for s in stats]
        configs[name] = {
            "games": len(games),
            "wdl": [scores.count(1), scores.count(0.5), scores.count(0)],
            "score": statistics.mean(scores),
            "median_ms": statistics.median(times),
            "p95_ms": percentile(times, 0.95),
            "worst_ms": max(times),
            "mean_depth": statistics.mean(s["depth"] for s in stats),
            "median_nps": statistics.median(s["nodes"] / s["seconds"] for s in stats),
            "model_median_fraction": statistics.median(ratios),
            "model_p95_fraction": percentile(ratios, 0.95),
            "coverage": statistics.mean(s.get("coverage", 0) for s in stats),
            "max_initialization_ms": max(r["initialization_ms"] for r in games),
            "max_rss_mb": max(s["peak_rss_mb"] for s in stats),
        }
    opening_result = bounded_interval(opening_differences)
    opening_result["opening_clusters"] = opening_result.pop("colour_pair_clusters")
    if opening_result["method"] == "paired colour-cluster bootstrap":
        opening_result["method"] = "paired opening-cluster bootstrap"
    print(
        json.dumps(
            {
                "conditions": runs[0],
                "cases": args.cases,
                "games": len(rows),
                "verified_colour_pairs": args.cases // 2,
                "configs": configs,
                "paired": bounded_interval(differences),
                "opening_clustered": opening_result,
                "unique_openings": len(openings),
                "failures": 0,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
