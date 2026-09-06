"""Recompute paired intervals from a saved arena log without replaying games."""

import json
import sys
from pathlib import Path

from passive_arena import CONFIGS, interval


def main() -> None:
    content = Path(sys.argv[1]).read_bytes()
    lines = content.decode(
        "utf-16" if content.startswith(b"\xff\xfe") else "utf-8-sig"
    ).splitlines()
    records = [json.loads(line) for line in lines if line.startswith('{"case":')]
    summaries = [json.loads(line[8:]) for line in lines if line.startswith("SUMMARY ")]
    assert summaries, "arena has not finished"
    result = summaries[-1]
    paired = {}
    for name, control in (
        ("passive", "baseline"),
        ("adaptive", "baseline"),
        ("adaptive", "passive"),
    ):
        differences = []
        for case in sorted({r["case"] for r in records}):
            matched = {r["config"]: r for r in records if r["case"] == case}
            if name in matched and control in matched:
                differences.append(
                    (case // 12 * 6 + case % 6, matched[name]["score"] - matched[control]["score"])
                )
        paired[f"{name}-{control}"] = interval(differences)
    result["paired"] = paired
    configurations = result["configs"]
    result["timing_gate"] = all(
        configurations[name]["overhead_median"] < 0.005
        and configurations[name]["overhead_p95"] < 0.01
        for name in CONFIGS[1:]
    )
    result["screen_indicates_improvement"] = (
        result["timing_gate"]
        and paired["adaptive-baseline"]["difference"] > 0
        and paired["adaptive-passive"]["difference"] > 0
        and configurations["adaptive"]["adaptive_moves"] > 0
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
