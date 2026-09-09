"""Dataset provenance and the hard-negative generation report.

Answers, from the artifacts rather than from memory: where the groups came from, what is
in them, what was excluded and whether the exclusion actually held, and how the split
hashes line up. Everything here is recomputed from the group shards, so a claim in the
final report can be checked against a number produced here.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import pathlib
import statistics
import sys
from typing import Any

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tests"))

GROUPS = REPO / "tests" / "results" / "nnue" / "groups"
V2 = REPO / "tests" / "results" / "nnue" / "v2"


def load_groups() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for path in sorted(GROUPS.glob("groups-*.jsonl")):
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    out.append(json.loads(line))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=pathlib.Path, default=V2 / "provenance.json")
    args = ap.parse_args()

    import nnue_v2_data as data

    groups = load_groups()
    banned = data.forbidden_keys()

    by_split: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for g in groups:
        by_split[g["split"]].append(g)

    # Split hashes: a stable fingerprint of exactly which parents are in which split.
    split_hashes = {}
    for split, rows in by_split.items():
        keys = sorted(r["parent_fen_key"] for r in rows)
        digest = hashlib.sha256("\n".join(keys).encode()).hexdigest()
        split_hashes[split] = {"groups": len(rows), "sha256": digest}

    # Leakage: parent keys must be pairwise disjoint across splits.
    key_sets = {s: {r["parent_fen_key"] for r in rows} for s, rows in by_split.items()}
    overlaps = {}
    names = sorted(key_sets)
    for i, a in enumerate(names):
        for b in names[i + 1 :]:
            overlaps[f"{a}&{b}"] = len(key_sets[a] & key_sets[b])

    # Exclusions, verified rather than asserted.
    loki_leaks = sum(1 for g in groups if g["opponent_family"] == "loki")
    fixture_leaks = sum(1 for g in groups if g["parent_fen_key"] in banned)

    children = [c for g in groups for c in g["candidates"]]
    sizes = [len(g["candidates"]) for g in groups]
    regrets = [c["regret"] for c in children]
    depths = [g["label_depth"] for g in groups]

    hard_negatives = sum(1 for c in children if c["source"] == "control_static_best")
    control_agrees = sum(1 for g in groups if g["control_static_best_is_sf_best"])
    control_regret = [g["control_static_best_regret"] for g in groups]

    phases = collections.Counter(g["phase"] for g in groups)
    families = collections.Counter(g["opponent_family"] for g in groups)

    report = {
        "groups": len(groups),
        "children": len(children),
        "children_per_group": {
            "median": statistics.median(sizes),
            "mean": round(statistics.fmean(sizes), 2),
            "min": min(sizes),
            "max": max(sizes),
        },
        "label_depth": {
            "median": statistics.median(depths),
            "min": min(depths),
            "max": max(depths),
        },
        "split_hashes": split_hashes,
        "split_parent_overlaps": overlaps,
        "leakage_free": all(v == 0 for v in overlaps.values()),
        "exclusions": {
            "loki_family_groups_present": loki_leaks,
            "rated_v5_ball_groups_present": fixture_leaks,
            "rated_v5_ball_size": len(banned),
            "exclusions_held": loki_leaks == 0 and fixture_leaks == 0,
        },
        "hard_negatives": {
            "control_static_best_added_by_second_search": hard_negatives,
            "control_static_best_share_pct": round(
                hard_negatives / max(1, len(groups)) * 100, 2
            ),
            "groups_where_control_static_best_is_sf_best": control_agrees,
            "control_static_best_agreement_pct": round(
                control_agrees / max(1, len(groups)) * 100, 2
            ),
            "control_static_best_regret_median_cp": statistics.median(control_regret),
            "control_static_best_regret_mean_cp": round(
                statistics.fmean(control_regret), 1
            ),
        },
        "regret_distribution_cp": {
            "zero": sum(1 for r in regrets if r == 0),
            "1_to_25": sum(1 for r in regrets if 0 < r <= 25),
            "26_to_100": sum(1 for r in regrets if 25 < r <= 100),
            "101_to_300": sum(1 for r in regrets if 100 < r <= 300),
            "over_300": sum(1 for r in regrets if r > 300),
            "median": statistics.median(regrets),
        },
        "position_classes": {
            "drawish_groups": sum(1 for g in groups if g["drawish"]),
            "passer_defence_groups": sum(1 for g in groups if g["passer_defence"]),
            "tactical_groups": sum(1 for g in groups if g["tactical"]),
            "quiet_children": sum(1 for c in children if c["quiet"]),
            "queen_trade_children": sum(1 for c in children if c["queen_trade"]),
            "check_children": sum(1 for c in children if c["gives_check"]),
            "capture_children": sum(1 for c in children if c["capture"]),
        },
        "phase_counts": dict(phases),
        "opponent_family_counts": dict(families),
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
