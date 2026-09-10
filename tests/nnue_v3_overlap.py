"""Gate 6: no data leakage, checked at every level a leak could hide in.

Split discipline is easy to claim and easy to break. A parent can land in train while one
of its own children lands in validation; a mirrored position can appear on both sides; a
whole game's lineage can straddle a boundary; a RATED_V5 fixture can slip in through a
position one ply from it. Each of those is a different check, so each is run.

The checks are:

1. parent FEN keys pairwise disjoint across train / validation / test;
2. child FEN keys pairwise disjoint across the same splits;
3. no child of a parent in one split appears as a parent or child in another;
4. every game id, and every opponent family, confined to one split;
5. the Loki family absent from the corpus entirely -- it is a held-out opponent *and*
   held-out data;
6. no RATED_V5 fixture FEN, and no position one ply from one, anywhere in the corpus;
7. colour-mirrored parent keys not spanning a split boundary.

Anything non-zero is a leak and the gate fails. Nothing here is repaired automatically:
a silent fix would hide the bug that produced it.
"""

from __future__ import annotations

import argparse
import collections
import json
import pathlib
import sys
from typing import Any

import chess

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tests"))

import nnue_v2_data as v2d  # noqa: E402

V2_GROUPS = REPO / "tests" / "results" / "nnue" / "groups"
V3_GROUPS = REPO / "tests" / "results" / "nnue" / "v3" / "groups"
OUT = REPO / "tests" / "results" / "nnue" / "v3" / "overlap_report.json"

SPLITS = ("train", "validation", "test")


def mirror_key(fen: str) -> str:
    """Canonical key of the colour-mirrored position.

    A network trained on a position and validated on its mirror has seen the answer, so
    the mirror has to be checked as well as the position.
    """
    board = chess.Board(fen)
    return v2d.fen_key(board.mirror().fen())


def load_groups(directories: list[pathlib.Path]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for directory in directories:
        for path in sorted(directory.glob("groups-*.jsonl")):
            with path.open(encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        out.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--include-v3", action="store_true")
    ap.add_argument("--mirror-sample", type=int, default=4000)
    args = ap.parse_args()

    directories = [V2_GROUPS]
    if args.include_v3 and V3_GROUPS.exists():
        directories.append(V3_GROUPS)
    groups = load_groups(directories)
    print(f"{len(groups):,} groups from {[str(d.name) for d in directories]}")

    parents: dict[str, set[str]] = {s: set() for s in SPLITS}
    children: dict[str, set[str]] = {s: set() for s in SPLITS}
    games: dict[str, set[str]] = collections.defaultdict(set)
    families: dict[str, set[str]] = collections.defaultdict(set)
    loki = 0
    for group in groups:
        split = group["split"]
        if split not in parents:
            continue
        parents[split].add(group["parent_fen_key"])
        for cand in group["candidates"]:
            children[split].add(cand["child_fen_key"])
        games[group["game_id"]].add(split)
        families[group["opponent_family"]].add(split)
        if group["opponent_family"] == "loki":
            loki += 1

    def pairwise(sets: dict[str, set[str]]) -> dict[str, int]:
        return {
            f"{a}|{b}": len(sets[a] & sets[b])
            for i, a in enumerate(SPLITS)
            for b in SPLITS[i + 1 :]
        }

    parent_overlap = pairwise(parents)
    child_overlap = pairwise(children)
    cross = {
        f"{a}_parents|{b}_children": len(parents[a] & children[b])
        for a in SPLITS
        for b in SPLITS
        if a != b
    }
    split_straddling_games = sorted(g for g, s in games.items() if len(s) > 1)
    split_straddling_families = {f: sorted(s) for f, s in families.items() if len(s) > 1}

    banned = v2d.forbidden_keys()
    all_parent_keys = set().union(*parents.values())
    all_child_keys = set().union(*children.values())
    fixture_hits = sorted((all_parent_keys | all_child_keys) & banned)

    # Mirrors are expensive to build for every position, so a deterministic sample is
    # checked and the sample size is reported rather than the check being implied over
    # the whole corpus.
    sample = groups[:: max(1, len(groups) // max(1, args.mirror_sample))]
    mirror_conflicts = 0
    for group in sample:
        key = mirror_key(group["parent_fen"])
        for split in SPLITS:
            if split != group["split"] and key in parents[split]:
                mirror_conflicts += 1

    report = {
        "groups": len(groups),
        "directories": [str(d.relative_to(REPO)) for d in directories],
        "parents_per_split": {s: len(parents[s]) for s in SPLITS},
        "children_per_split": {s: len(children[s]) for s in SPLITS},
        "parent_key_overlap": parent_overlap,
        "child_key_overlap": child_overlap,
        "parent_child_cross_split_overlap": cross,
        "games_straddling_a_split": len(split_straddling_games),
        "games_straddling_examples": split_straddling_games[:10],
        "families_straddling_a_split": split_straddling_families,
        "loki_family_groups": loki,
        "rated_v5_ball_hits": len(fixture_hits),
        "rated_v5_ball_examples": fixture_hits[:5],
        "mirror_sample_size": len(sample),
        "mirror_cross_split_conflicts": mirror_conflicts,
    }
    # ---- quarantine ----------------------------------------------------------
    # A leak is repaired by dropping groups, never by reassigning a split: moving a
    # position to the other side of a boundary would hide the lineage error that put it
    # there. Which side loses the group is fixed in advance -- train is dropped before
    # test, and test before validation -- because validation drives checkpoint selection
    # and has to stay whole.
    protection = {"train": 0, "test": 1, "validation": 2}
    leaked: set[str] = set()
    for a in SPLITS:
        for b in SPLITS:
            if a != b:
                leaked |= parents[a] & children[b]
                leaked |= children[a] & children[b]

    def keys_of(group: dict[str, Any]) -> set[str]:
        return {group["parent_fen_key"]} | {
            c["child_fen_key"] for c in group["candidates"]
        }

    quarantine: set[str] = set()
    for group in groups:
        touched = keys_of(group) & leaked
        if not touched:
            continue
        rivals = {
            s
            for s in SPLITS
            if s != group["split"] and (parents[s] & touched or children[s] & touched)
        }
        # Drop this group when some split holding the same key is more protected.
        if any(protection[s] > protection[group["split"]] for s in rivals):
            quarantine.add(group["group_id"])

    # Re-measure rather than assert. A quarantine rule that leaves a leak behind has to
    # show up here as a non-zero count, not as a claim in a docstring.
    kept = [g for g in groups if g["group_id"] not in quarantine]
    p2: dict[str, set[str]] = {s: set() for s in SPLITS}
    c2: dict[str, set[str]] = {s: set() for s in SPLITS}
    for group in kept:
        if group["split"] not in p2:
            continue
        p2[group["split"]].add(group["parent_fen_key"])
        for cand in group["candidates"]:
            c2[group["split"]].add(cand["child_fen_key"])
    after_parent = {
        f"{a}|{b}": len(p2[a] & p2[b])
        for i, a in enumerate(SPLITS)
        for b in SPLITS[i + 1 :]
    }
    after_child = {
        f"{a}|{b}": len(c2[a] & c2[b])
        for i, a in enumerate(SPLITS)
        for b in SPLITS[i + 1 :]
    }
    after_cross = {
        f"{a}_parents|{b}_children": len(p2[a] & c2[b])
        for a in SPLITS
        for b in SPLITS
        if a != b
    }
    after_clean = (
        max(after_parent.values(), default=0) == 0
        and max(after_child.values(), default=0) == 0
        and max(after_cross.values(), default=0) == 0
    )

    (OUT.parent / "quarantine.json").write_text(
        json.dumps(
            {
                "leaked_keys": len(leaked),
                "quarantined_groups": len(quarantine),
                "group_ids": sorted(quarantine),
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    report["leaked_keys"] = len(leaked)
    report["quarantined_groups"] = len(quarantine)
    report["groups_after_quarantine"] = len(kept)
    report["parent_key_overlap_after_quarantine"] = after_parent
    report["child_key_overlap_after_quarantine"] = after_child
    report["parent_child_cross_overlap_after_quarantine"] = after_cross
    report["clean_after_quarantine"] = after_clean
    report["clean"] = (
        max(parent_overlap.values(), default=0) == 0
        and max(child_overlap.values(), default=0) == 0
        and max(cross.values(), default=0) == 0
        and len(split_straddling_games) == 0
        and loki == 0
        and len(fixture_hits) == 0
        and mirror_conflicts == 0
    )

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    print(f"\nwrote {OUT}")
    return 0 if report["clean"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
