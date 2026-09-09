"""Build the position corpus from generated games and label it with Stockfish.

Two steps, deliberately separate so the expensive one is restartable:

  collect   read every game, deduplicate positions, assign phases, write the immutable
            FEN manifest with its split already fixed
  label     shard the manifest deterministically and run Stockfish at a fixed node
            limit, one process per physical core, one thread each

Deduplication is global and split-aware. A position that appears in games from two
different splits is kept in exactly one of them, so the same FEN can never sit on both
sides of the train/test boundary. The winning split is decided by sorted game id, which
does not depend on file order or shard order.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import multiprocessing as mp
import os
import pathlib
import random
import sys
import time

import chess
import chess.engine

REPO = pathlib.Path(__file__).resolve().parent.parent
GAMES = REPO / "tests" / "results" / "nnue" / "games"
LABELS = REPO / "tests" / "results" / "nnue" / "labels"
DATASET = REPO / "tests" / "results" / "nnue" / "dataset"
STOCKFISH = (
    REPO
    / "tests"
    / "external_engines"
    / "stockfish"
    / "stockfish"
    / "stockfish-windows-x86-64-universal.exe"
)
NODES = 200_000
CLAMP_CP = 1000
SWING_CP = 100
SAMPLE_SEED = 20260909

for _var in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
    os.environ.setdefault(_var, "1")


def fen_key(fen: str) -> str:
    return " ".join(fen.split()[:4])


def forbidden_fens() -> set[str]:
    data = json.loads((REPO / "tests" / "rated_v5_positions.json").read_text(encoding="utf-8"))
    return {fen_key(p["fen"]) for p in data["positions"]}


# --------------------------------------------------------------------------- collect


def collect(target: int) -> dict:
    banned = forbidden_fens()
    best: dict[str, dict] = {}
    games_read = 0
    dropped_banned = 0
    duplicate_hits = 0

    for path in sorted(GAMES.glob("*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        games_read += 1
        split = record["split"]
        game_id = record["game_id"]
        previous: int | None = None
        for pos in record["positions"]:
            key = fen_key(pos["fen"])
            if key in banned:
                dropped_banned += 1
                previous = pos["control_static_cp"]
                continue
            swing = (
                previous is not None
                and abs(pos["control_static_cp"] - previous) >= SWING_CP
            )
            previous = pos["control_static_cp"]
            entry = {
                "fen": pos["fen"],
                "fen_key": key,
                "game_id": game_id,
                "pair_id": record["pair_id"],
                "ply": pos["ply"],
                "split": split,
                "phase": pos["phase"],
                "material_phase": pos["material_phase"],
                "side_to_move": pos["side_to_move"],
                "control_static_cp": pos["control_static_cp"],
                "in_check": pos["in_check"],
                "opponent_family": record["opponent_family"],
                "held_out_family": record["held_out_family"],
                "tactical": bool(swing or pos["in_check"]),
            }
            if key in best:
                duplicate_hits += 1
                # deterministic winner: lowest game id, then lowest ply
                incumbent = best[key]
                if (game_id, pos["ply"]) < (incumbent["game_id"], incumbent["ply"]):
                    best[key] = entry
            else:
                best[key] = entry

    pool = list(best.values())
    rng = random.Random(SAMPLE_SEED)
    rng.shuffle(pool)

    # Target mix: 40% middlegame, 40% endgame, 20% tactical/swing. Endgames are the
    # scarce class in engine games, so the achieved mix is reported rather than forced.
    buckets: dict[str, list[dict]] = {"middlegame": [], "endgame": [], "tactical": [], "opening": []}
    for entry in pool:
        if entry["tactical"]:
            buckets["tactical"].append(entry)
        elif entry["phase"] == "endgame":
            buckets["endgame"].append(entry)
        elif entry["phase"] == "middlegame":
            buckets["middlegame"].append(entry)
        else:
            buckets["opening"].append(entry)

    want = {
        "middlegame": int(target * 0.40),
        "endgame": int(target * 0.40),
        "tactical": int(target * 0.20),
    }
    chosen: list[dict] = []
    for name, quota in want.items():
        chosen.extend(buckets[name][:quota])
    # backfill any shortfall from what is left, openings included, so the corpus still
    # reaches its size when a bucket is thin
    taken = {id(e) for e in chosen}
    leftovers = [e for e in pool if id(e) not in taken]
    chosen.extend(leftovers[: max(0, target - len(chosen))])

    # cap how much any one game may contribute, so adjacent positions from a long game
    # cannot dominate
    per_game_cap = max(6, target // max(1, games_read) * 3)
    counts: dict[str, int] = {}
    capped: list[dict] = []
    for entry in chosen:
        seen = counts.get(entry["game_id"], 0)
        if seen >= per_game_cap:
            continue
        counts[entry["game_id"]] = seen + 1
        capped.append(entry)

    capped.sort(key=lambda e: hashlib.sha256(e["fen_key"].encode()).hexdigest())

    split_counts: dict[str, int] = {}
    phase_counts: dict[str, int] = {}
    for entry in capped:
        split_counts[entry["split"]] = split_counts.get(entry["split"], 0) + 1
        label = "tactical" if entry["tactical"] else entry["phase"]
        phase_counts[label] = phase_counts.get(label, 0) + 1

    # leakage proof: FEN sets of the three splits must be pairwise disjoint
    by_split: dict[str, set[str]] = {}
    for entry in capped:
        by_split.setdefault(entry["split"], set()).add(entry["fen_key"])
    overlaps = {
        f"{a}&{b}": len(by_split.get(a, set()) & by_split.get(b, set()))
        for a, b in (("train", "validation"), ("train", "test"), ("validation", "test"))
    }

    DATASET.mkdir(parents=True, exist_ok=True)
    out = DATASET / "positions.jsonl"
    with out.open("w", encoding="utf-8") as handle:
        for entry in capped:
            handle.write(json.dumps(entry) + "\n")

    summary = {
        "games_read": games_read,
        "unique_positions_available": len(pool),
        "duplicate_positions_collapsed": duplicate_hits,
        "dropped_rated_v5_fixture_positions": dropped_banned,
        "target": target,
        "selected": len(capped),
        "per_game_cap": per_game_cap,
        "split_counts": split_counts,
        "phase_counts": phase_counts,
        "phase_fractions": {
            k: round(v / max(1, len(capped)), 4) for k, v in phase_counts.items()
        },
        "split_fen_overlaps": overlaps,
        "leakage_free": all(v == 0 for v in overlaps.values()),
        "bucket_sizes_available": {k: len(v) for k, v in buckets.items()},
        "output": str(out.relative_to(REPO)),
    }
    (DATASET / "collect_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return summary


# ----------------------------------------------------------------------------- label


def label_shard(args: tuple[int, int, str]) -> dict:
    shard, shards, path = args
    entries = []
    with open(path, encoding="utf-8") as handle:
        for index, line in enumerate(handle):
            if index % shards == shard:
                entries.append(json.loads(line))

    out = LABELS / f"shard-{shard:02d}.jsonl"
    done: set[str] = set()
    if out.exists():
        with out.open(encoding="utf-8") as handle:
            for line in handle:
                try:
                    done.add(json.loads(line)["fen_key"])
                except Exception:  # noqa: BLE001
                    pass

    todo = [e for e in entries if e["fen_key"] not in done]
    if not todo:
        return {"shard": shard, "labelled": 0, "already": len(done), "total": len(entries)}

    engine = chess.engine.SimpleEngine.popen_uci(str(STOCKFISH))
    engine.configure({"Threads": 1, "Hash": 64})
    written = 0
    started = time.perf_counter()
    try:
        with out.open("a", encoding="utf-8") as handle:
            for entry in todo:
                board = chess.Board(entry["fen"])
                info = engine.analyse(
                    board, chess.engine.Limit(nodes=NODES), game=object()
                )
                pov = info["score"].pov(board.turn)
                mate = pov.mate()
                raw = pov.score(mate_score=100_000)
                clamped = max(-CLAMP_CP, min(CLAMP_CP, int(raw)))
                row = dict(entry)
                row["stockfish_cp_raw"] = int(raw)
                row["stockfish_cp"] = clamped
                row["stockfish_mate"] = mate
                row["stockfish_depth"] = info.get("depth")
                row["residual_target"] = clamped - entry["control_static_cp"]
                handle.write(json.dumps(row) + "\n")
                written += 1
                if written % 500 == 0:
                    handle.flush()
    finally:
        engine.quit()

    elapsed = time.perf_counter() - started
    return {
        "shard": shard,
        "labelled": written,
        "already": len(done),
        "total": len(entries),
        "seconds": round(elapsed, 1),
        "positions_per_second": round(written / max(1e-9, elapsed), 2),
    }


def label(shards: int) -> dict:
    LABELS.mkdir(parents=True, exist_ok=True)
    source = DATASET / "positions.jsonl"
    if not source.exists():
        raise SystemExit(f"{source} does not exist; run `collect` first")
    total = sum(1 for _ in source.open(encoding="utf-8"))
    print(f"labelling {total:,} positions at {NODES:,} nodes across {shards} shards")

    started = time.perf_counter()
    with mp.Pool(processes=shards) as pool:
        results = pool.map(label_shard, [(i, shards, str(source)) for i in range(shards)])
    elapsed = time.perf_counter() - started

    # merge and validate: every expected position exactly once, nothing unexpected
    expected = {json.loads(line)["fen_key"] for line in source.open(encoding="utf-8")}
    merged: dict[str, dict] = {}
    duplicates = 0
    for path in sorted(LABELS.glob("shard-*.jsonl")):
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                row = json.loads(line)
                if row["fen_key"] in merged:
                    duplicates += 1
                    continue
                merged[row["fen_key"]] = row

    missing = expected - set(merged)
    unexpected = set(merged) - expected
    out = DATASET / "labelled.jsonl"
    with out.open("w", encoding="utf-8") as handle:
        for key in sorted(merged, key=lambda k: hashlib.sha256(k.encode()).hexdigest()):
            handle.write(json.dumps(merged[key]) + "\n")

    summary = {
        "nodes": NODES,
        "threads_per_process": 1,
        "hash_mb": 64,
        "shards": shards,
        "expected": len(expected),
        "labelled": len(merged),
        "missing": len(missing),
        "unexpected": len(unexpected),
        "duplicate_rows_ignored": duplicates,
        "complete": not missing and not unexpected,
        "elapsed_seconds": round(elapsed, 1),
        "positions_per_hour": round(len(merged) / max(1e-9, elapsed) * 3600),
        "shard_results": results,
        "output": str(out.relative_to(REPO)),
    }
    (DATASET / "label_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "shard_results"}, indent=2))
    return summary


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["collect", "label"])
    ap.add_argument("--target", type=int, default=200_000)
    ap.add_argument("--shards", type=int, default=6)
    args = ap.parse_args()
    if args.step == "collect":
        collect(args.target)
    else:
        label(args.shards)


if __name__ == "__main__":
    sys.exit(main())
