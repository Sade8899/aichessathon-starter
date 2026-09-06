"""Aggregate raw fixed-position measurements without treating search changes as equivalence."""

import json
import statistics
import sys
from typing import Any

from passive_arena import percentile


def describe(values: list[float]) -> dict[str, float]:
    return {
        "median": statistics.median(values),
        "p95": percentile(values, 0.95),
        "minimum": min(values),
        "maximum": max(values),
        "mean": statistics.mean(values),
    }


def main() -> None:
    rows, counts = [], []
    for line in sys.stdin:
        if line.startswith("BENCH "):
            rows.append(json.loads(line[6:]))
        elif line.startswith("COUNTERS "):
            counts.append(json.loads(line[9:]))
    assert len(rows) == 144 and len(counts) == 48
    cases: list[dict[str, Any]] = []
    deltas, growth, expanded = [], [], []
    for index in range(24):
        data = {
            name: [r for r in rows if r["position"] == index and r["name"] == name]
            for name in ("control", "candidate")
        }
        a, b = data["control"][0], data["candidate"][0]
        for group in data.values():
            assert len(group) == 3
            assert all(
                all(r["fixed"][k] == group[0]["fixed"][k] for k in ("move", "nodes", "scores"))
                for r in group
            )
        counted = {r["name"]: r for r in counts if r["position"] == index}
        for name in data:
            assert counted[name]["nodes"] == data[name][0]["fixed"]["nodes"]
        depth_delta = statistics.mean(r["depth"] for r in data["candidate"]) - statistics.mean(
            r["depth"] for r in data["control"]
        )
        deltas.append(depth_delta)
        ratio = counted["candidate"]["qnodes"] / counted["control"]["qnodes"]
        growth.append(ratio)
        expanded.append(b["fixed"]["nodes"] / a["fixed"]["nodes"])
        cases.append(
            {
                "position": index,
                "fen": a["fen"],
                "control_move": a["fixed"]["move"],
                "candidate_move": b["fixed"]["move"],
                "control_score": a["fixed"]["scores"][a["fixed"]["move"]],
                "candidate_score": b["fixed"]["scores"][b["fixed"]["move"]],
                "changed_root_scores_or_bounds": {
                    move: [score, b["fixed"]["scores"][move]]
                    for move, score in a["fixed"]["scores"].items()
                    if score != b["fixed"]["scores"][move]
                },
                "fixed_nodes": [a["fixed"]["nodes"], b["fixed"]["nodes"]],
                "qnode_ratio": ratio,
                "timed_depth_delta": depth_delta,
            }
        )
    summary = {}
    for name in ("control", "candidate"):
        selected = [r for r in rows if r["name"] == name]
        counted_rows = [r for r in counts if r["name"] == name]
        summary[name] = {
            "timed": {
                k: describe([r[k] for r in selected])
                for k in ("nodes", "nps", "depth", "seconds", "model_fraction", "hard_slack")
            },
            "fixed_counters": {
                k: describe([r[k] for r in counted_rows])
                for k in ("nodes", "qnodes", "quiet_check_tests", "quiet_checks_searched")
            },
        }
    print(
        json.dumps(
            {
                "summary": summary,
                "positions": cases,
                "mean_depth_delta": statistics.mean(deltas),
                "positions_with_lower_mean_depth": sum(d < 0 for d in deltas),
                "mean_qnode_ratio": statistics.mean(growth),
                "largest_node_expansion": max(expanded),
                "largest_expansion_position": expanded.index(max(expanded)),
                "changed_selected_moves": sum(
                    r["control_move"] != r["candidate_move"] for r in cases
                ),
                "counter_timing": (
                    "Separate instrumented fixed-depth pass; timed benchmarks are uninstrumented"
                ),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
