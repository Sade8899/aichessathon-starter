"""Targeted sibling generation: the position classes the V2 corpus is short of.

The existing 28,640 groups are reused unchanged -- relabelling them would spend hours to
reproduce numbers that already exist. What they are missing is balance. V2's parent
selection put endgames first by design, and the result is 61 % endgame, 29 % middlegame,
10 % opening. A checkpoint selected on a validation set shaped like that is selected
mostly on endgame behaviour, whatever it then does in a middlegame.

So this run inverts the priority and adds only what is scarce: middlegames, tactical
positions, king-safety and liquidation decisions, and positions whose evaluation sits
near zero, where V2 traced the first fixture break. Parents already carrying a group are
skipped on the canonical FEN key, so nothing is labelled twice.

Every exclusion the V2 generator enforced is enforced here by importing the same
functions: the whole Loki family stays an unseen holdout opponent and unseen data, and
the RATED_V5 fixture FENs together with every position one ply from them are banned.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import multiprocessing as mp
import os
import pathlib
import random
import sys
import time
from typing import Any

import chess
import chess.engine

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))

for _var in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
    os.environ.setdefault(_var, "1")

import nnue_v2_data as v2d  # noqa: E402

V2_GROUPS = REPO / "tests" / "results" / "nnue" / "groups"
OUT_DIR = REPO / "tests" / "results" / "nnue" / "v3" / "groups"

# A parent counts as "near zero" when the control's own static evaluation sits inside
# this band. The teacher score is not known before labelling, so the control's view is
# the only pre-label signal available, and it is the right one anyway: these are the
# positions where the control's ordering is most fragile.
NEAR_ZERO_CP = 40


def existing_parent_keys() -> set[str]:
    keys: set[str] = set()
    for directory in (V2_GROUPS, OUT_DIR):
        for path in sorted(directory.glob("groups-*.jsonl")):
            with path.open(encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    with contextlib.suppress(Exception):
                        keys.add(json.loads(line)["parent_fen_key"])
    return keys


def select_parents(target: int, seed: int) -> list[dict[str, Any]]:
    banned = v2d.forbidden_keys()
    already = existing_parent_keys()
    print(f"{len(already)} parents already carry a group; they are skipped")

    rows: list[dict[str, Any]] = []
    dropped_loki = dropped_banned = dropped_dupe = 0
    with v2d.POSITIONS.open(encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            if row["held_out_family"]:
                dropped_loki += 1
                continue
            if row["fen_key"] in banned:
                dropped_banned += 1
                continue
            if row["fen_key"] in already:
                dropped_dupe += 1
                continue
            rows.append(row)

    control = v2d.control_module()

    def priority(row: dict[str, Any]) -> int:
        """Inverted against V2: what that corpus starved, this one feeds first."""
        board = chess.Board(row["fen"])
        static = control.compiled_evaluate(board)
        if abs(static) <= NEAR_ZERO_CP and row["phase"] != "endgame":
            return 0  # near-equal non-endgame: the fragile ordering class
        if row["tactical"]:
            return 1
        if row["phase"] == "middlegame":
            return 2
        if row["phase"] == "opening":
            return 3
        return 4  # endgames last; the corpus already has 17,357 of them

    rng = random.Random(seed)
    rng.shuffle(rows)
    for row in rows:
        row["_priority"] = priority(row)
    rows.sort(key=lambda r: r["_priority"])

    by_split: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_split.setdefault(row["split"], []).append(row)
    total = sum(len(v) for v in by_split.values())
    chosen: list[dict[str, Any]] = []
    for bucket in by_split.values():
        quota = max(1, round(target * len(bucket) / total))
        chosen.extend(bucket[:quota])
    rng.shuffle(chosen)

    print(
        json.dumps(
            {
                "available": len(rows),
                "dropped_loki_family": dropped_loki,
                "dropped_rated_v5_ball": dropped_banned,
                "dropped_already_generated": dropped_dupe,
                "selected": len(chosen),
                "per_split": {k: sum(1 for c in chosen if c["split"] == k) for k in by_split},
                "per_phase": {
                    p: sum(1 for c in chosen if c["phase"] == p)
                    for p in {c["phase"] for c in chosen}
                },
            },
            indent=2,
        )
    )
    return chosen


def worker(args: tuple[int, int, str, str]) -> dict[str, Any]:
    """Identical to the V2 worker except that the output directory is passed in.

    It has to be passed rather than read from a module global: Windows spawns worker
    processes, which re-import this module fresh, so a global set in the parent would
    silently revert to its default in the child and the run would append to the V2
    corpus instead of the V3 one.
    """
    shard, shards, path, outdir = args
    parents: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as handle:
        for index, line in enumerate(handle):
            if index % shards == shard:
                parents.append(json.loads(line))

    out = pathlib.Path(outdir) / f"groups-{shard:02d}.jsonl"
    done: set[str] = set()
    if out.exists():
        with out.open(encoding="utf-8") as handle:
            for line in handle:
                with contextlib.suppress(Exception):
                    done.add(json.loads(line)["parent_fen_key"])
    todo = [p for p in parents if p["fen_key"] not in done]

    control = v2d.control_module()
    engine = chess.engine.SimpleEngine.popen_uci(str(v2d.STOCKFISH))
    engine.configure({"Threads": 1, "Hash": 64})
    written = skipped = 0
    started = time.perf_counter()
    try:
        with out.open("a", encoding="utf-8") as handle:
            for parent in todo:
                try:
                    group = v2d.build_group(engine, control, parent)
                except (chess.engine.EngineError, chess.engine.EngineTerminatedError):
                    with contextlib.suppress(Exception):
                        engine.quit()
                    engine = chess.engine.SimpleEngine.popen_uci(str(v2d.STOCKFISH))
                    engine.configure({"Threads": 1, "Hash": 64})
                    continue
                if group is None:
                    skipped += 1
                    continue
                handle.write(json.dumps(group) + "\n")
                written += 1
                if written % 100 == 0:
                    handle.flush()
    finally:
        with contextlib.suppress(Exception):
            engine.quit()
    elapsed = time.perf_counter() - started
    return {
        "shard": shard,
        "written": written,
        "skipped_no_signal": skipped,
        "already": len(done),
        "seconds": round(elapsed, 1),
        "groups_per_second": round(written / elapsed, 2) if elapsed else 0.0,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--parents", type=int, default=12000)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--seed", type=int, default=20260910)
    args = ap.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    plan = OUT_DIR / "parents.jsonl"
    if not plan.exists():
        chosen = select_parents(args.parents, args.seed)
        with plan.open("w", encoding="utf-8") as handle:
            for row in chosen:
                row.pop("_priority", None)
                handle.write(json.dumps(row) + "\n")
    else:
        print(f"reusing existing parent plan {plan}")

    started = time.perf_counter()
    with mp.Pool(args.workers) as pool:
        results = pool.map(
            worker,
            [(i, args.workers, str(plan), str(OUT_DIR)) for i in range(args.workers)],
        )
    elapsed = time.perf_counter() - started

    written = sum(r["written"] for r in results)
    with plan.open(encoding="utf-8") as handle:
        planned = sum(1 for _ in handle)
    summary = {
        "parents_planned": planned,
        "groups_written": written,
        "skipped_no_signal": sum(r["skipped_no_signal"] for r in results),
        "workers": args.workers,
        "nodes": v2d.NODES,
        "multipv": v2d.MULTIPV,
        "clamp_cp": v2d.CLAMP_CP,
        "label_engine": v2d.LABEL_ENGINE,
        "elapsed_seconds": round(elapsed, 1),
        "groups_per_hour": round(3600.0 * written / elapsed, 1) if elapsed else 0.0,
        "shards": results,
    }
    (OUT_DIR / "generation_summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
