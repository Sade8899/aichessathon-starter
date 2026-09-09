"""Hard-negative sibling groups for the V2 move-quality objective.

V1 trained on static evaluation error and broke five solved fixtures because nothing in
its loss could see a move ordering. V2 needs groups of sibling children with a ground
truth ordering, and it needs them cheaply.

One Stockfish MultiPV search at the parent produces the top-k moves *and* their scores
from a single shared search tree. Measured on real corpus positions, MultiPV=6 returns
43.5 child labels/s/worker against 4.6 for single-PV searches -- 9.5x -- which is what
makes a ranking dataset affordable at all. The child's side-to-move-relative Stockfish
value is the negation of the parent-perspective score of the move that reaches it, so
children are labelled without ever being searched.

The hard negative that matters is the control's own static-best child: the move the
deployed evaluator likes most. When Stockfish's top-k does not already contain it, it is
scored by a second search restricted to that one root move.

Parents are drawn from the existing V1 corpus, inheriting its per-game-lineage split, so
the V2 splits cannot leak across the V1 boundary. The Loki family and the RATED_V5
fixtures (and their one-ply neighbourhoods) are excluded here, in code.
"""

from __future__ import annotations

import argparse
import contextlib
import importlib.util
import json
import multiprocessing as mp
import os
import pathlib
import random
import sys
import time
import types
from typing import Any

import chess
import chess.engine

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))

for _var in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
    os.environ.setdefault(_var, "1")

STOCKFISH = (
    REPO
    / "tests"
    / "external_engines"
    / "stockfish"
    / "stockfish"
    / "stockfish-windows-x86-64-universal.exe"
)
DATASET = REPO / "tests" / "results" / "nnue" / "dataset"
POSITIONS = DATASET / "positions.jsonl"
OUT_DIR = REPO / "tests" / "results" / "nnue" / "groups"
RATED_V5 = REPO / "tests" / "rated_v5_positions.json"

# Matches the V1 labelling convention exactly, so residual targets are comparable.
CLAMP_CP = 1000
NODES = 250_000
MULTIPV = 8
LABEL_ENGINE = "Stockfish 19 sf_19"


def fen_key(fen: str) -> str:
    parts = fen.split()
    return " ".join(parts[:4])


def forbidden_keys() -> set[str]:
    """RATED_V5 fixture positions and everything one ply from them.

    A fixture is a final test. Training on a position one move away from it would let
    the network memorise the neighbourhood, so the whole one-ply ball is excluded.
    """
    banned: set[str] = set()
    data = json.loads(RATED_V5.read_text(encoding="utf-8"))
    for entry in data["positions"]:
        board = chess.Board(entry["fen"])
        banned.add(fen_key(board.fen()))
        for move in board.legal_moves:
            board.push(move)
            banned.add(fen_key(board.fen()))
            board.pop()
    return banned


def control_module() -> types.ModuleType:
    spec = importlib.util.spec_from_file_location("v2_control", REPO / "agent.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["v2_control"] = module
    spec.loader.exec_module(module)
    return module


# --------------------------------------------------------------------------- selection


def _drawish(cp: int) -> bool:
    return abs(cp) <= 40


def _queen_trade(board: chess.Board, move: chess.Move) -> bool:
    victim = board.piece_at(move.to_square)
    return victim is not None and victim.piece_type == chess.QUEEN


def _passer_defence(board: chess.Board) -> bool:
    """True when either side has a pawn on its sixth rank or beyond.

    Round 78 was lost defending a passed pawn, so positions with an advanced passer are
    deliberately over-sampled rather than left to chance.
    """
    for square in board.pieces(chess.PAWN, chess.WHITE):
        if chess.square_rank(square) >= 5:
            return True
    for square in board.pieces(chess.PAWN, chess.BLACK):
        if chess.square_rank(square) <= 2:
            return True
    return False


def select_parents(target: int, seed: int) -> list[dict[str, Any]]:
    """Choose parents, over-sampling the position types V1 damaged."""
    banned = forbidden_keys()
    rows: list[dict[str, Any]] = []
    dropped_loki = 0
    dropped_banned = 0
    with POSITIONS.open(encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            if row["held_out_family"]:
                dropped_loki += 1
                continue
            if row["fen_key"] in banned:
                dropped_banned += 1
                continue
            rows.append(row)

    # Priority: the position classes where V1 lost games. Openings are deliberately
    # starved -- rated games start from curated openings, but V1's largest MAE gain was
    # in exactly the phase where a static residual changes the fewest root decisions.
    def priority(row: dict[str, Any]) -> int:
        if row["phase"] == "endgame":
            return 0
        if _passer_defence(chess.Board(row["fen"])):
            return 1
        if row["phase"] == "middlegame":
            return 2
        if row["tactical"]:
            return 3
        return 4

    rng = random.Random(seed)
    rng.shuffle(rows)
    for row in rows:
        row["_priority"] = priority(row)
    rows.sort(key=lambda r: r["_priority"])

    # Keep the split mix representative, so a genuinely unseen validation group set
    # exists. Splits are inherited from the V1 corpus and never reassigned.
    by_split: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_split.setdefault(row["split"], []).append(row)
    total = sum(len(v) for v in by_split.values())
    chosen: list[dict[str, Any]] = []
    for split, bucket in by_split.items():
        quota = max(1, round(target * len(bucket) / total))
        chosen.extend(bucket[:quota])
    rng.shuffle(chosen)

    print(
        json.dumps(
            {
                "available": len(rows),
                "dropped_loki_family": dropped_loki,
                "dropped_rated_v5_ball": dropped_banned,
                "selected": len(chosen),
                "per_split": {
                    k: sum(1 for c in chosen if c["split"] == k) for k in by_split
                },
            },
            indent=2,
        )
    )
    return chosen


# --------------------------------------------------------------------------- labelling


def _score_cp(pov: chess.engine.PovScore, turn: chess.Color) -> tuple[int, int | None]:
    relative = pov.pov(turn)
    mate = relative.mate()
    raw = relative.score(mate_score=100_000)
    return max(-CLAMP_CP, min(CLAMP_CP, int(raw))), mate


def build_group(
    engine: chess.engine.SimpleEngine,
    control: types.ModuleType,
    parent: dict[str, Any],
) -> dict[str, Any] | None:
    board = chess.Board(parent["fen"])
    legal = list(board.legal_moves)
    if len(legal) < 2:
        return None

    infos = engine.analyse(
        board, chess.engine.Limit(nodes=NODES), multipv=MULTIPV, game=object()
    )
    if not isinstance(infos, list):
        infos = [infos]

    scored: dict[str, tuple[int, int | None, str]] = {}
    depth = 0
    for info in infos:
        pv = info.get("pv")
        if not pv:
            continue
        depth = max(depth, int(info.get("depth") or 0))
        cp, mate = _score_cp(info["score"], board.turn)
        scored[pv[0].uci()] = (cp, mate, "multipv")

    if not scored:
        return None

    # The control's own static-best child: the hard negative that actually matters,
    # because it is the move a static evaluator is most easily talked into.
    static_by_move: dict[str, int] = {}
    for move in legal:
        board.push(move)
        # compiled_evaluate is side-to-move relative, so a child's score is from the
        # opponent's point of view and the mover prefers the smallest.
        static_by_move[move.uci()] = int(control.compiled_evaluate(board))
        board.pop()
    control_best = min(static_by_move, key=lambda m: static_by_move[m])

    if control_best not in scored:
        with contextlib.suppress(
            chess.engine.EngineError, chess.engine.EngineTerminatedError
        ):
            extra = engine.analyse(
                board,
                chess.engine.Limit(nodes=NODES // 2),
                root_moves=[chess.Move.from_uci(control_best)],
                game=object(),
            )
            if isinstance(extra, list):
                extra = extra[0]
            cp, mate = _score_cp(extra["score"], board.turn)
            scored[control_best] = (cp, mate, "control_static_best")

    best_cp = max(cp for cp, _, _ in scored.values())
    sf_best = max(scored, key=lambda m: scored[m][0])

    candidates: list[dict[str, Any]] = []
    for uci, (cp, mate, source) in scored.items():
        move = chess.Move.from_uci(uci)
        capture = board.is_capture(move)
        queen_trade = _queen_trade(board, move)
        board.push(move)
        child_fen = board.fen()
        child_static = int(control.compiled_evaluate(board))
        gives_check = board.is_check()
        insufficient = board.is_insufficient_material()
        board.pop()
        candidates.append(
            {
                "uci": uci,
                "child_fen": child_fen,
                "child_fen_key": fen_key(child_fen),
                # Parent-perspective Stockfish score of the move.
                "sf_cp_parent": cp,
                "sf_mate_parent": mate,
                # Child-perspective label, by definition of a negamax score.
                "sf_cp_child": -cp,
                "regret": best_cp - cp,
                "control_static_child": child_static,
                "residual_target": -cp - child_static,
                "source": source,
                "is_sf_best": uci == sf_best,
                "is_control_static_best": uci == control_best,
                "capture": capture,
                "queen_trade": queen_trade,
                "gives_check": gives_check,
                "insufficient_material": insufficient,
                "quiet": not capture and not gives_check,
            }
        )

    if len({c["regret"] for c in candidates}) < 2:
        # No ordering signal: every candidate is equally good, so the group cannot teach
        # a ranking and would only add unweighted value-loss mass.
        return None

    return {
        "group_id": f"{parent['game_id']}#{parent['ply']}",
        "parent_fen": parent["fen"],
        "parent_fen_key": parent["fen_key"],
        "game_id": parent["game_id"],
        "pair_id": parent["pair_id"],
        "ply": parent["ply"],
        "split": parent["split"],
        "phase": parent["phase"],
        "tactical": parent["tactical"],
        "opponent_family": parent["opponent_family"],
        "material_phase": parent["material_phase"],
        "parent_control_static_cp": parent["control_static_cp"],
        "parent_sf_cp": best_cp,
        "sf_best_uci": sf_best,
        "control_static_best_uci": control_best,
        "control_static_best_is_sf_best": control_best == sf_best,
        "control_static_best_regret": best_cp - scored[control_best][0],
        "drawish": _drawish(best_cp),
        "passer_defence": _passer_defence(board),
        "legal_moves": len(legal),
        "label_nodes": NODES,
        "label_multipv": MULTIPV,
        "label_depth": depth,
        "label_engine": LABEL_ENGINE,
        "candidates": candidates,
    }


def worker(args: tuple[int, int, str]) -> dict[str, Any]:
    shard, shards, path = args
    parents: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as handle:
        for index, line in enumerate(handle):
            if index % shards == shard:
                parents.append(json.loads(line))

    out = OUT_DIR / f"groups-{shard:02d}.jsonl"
    done: set[str] = set()
    if out.exists():
        with out.open(encoding="utf-8") as handle:
            for line in handle:
                with contextlib.suppress(Exception):
                    done.add(json.loads(line)["parent_fen_key"])
    todo = [p for p in parents if p["fen_key"] not in done]

    control = control_module()
    engine = chess.engine.SimpleEngine.popen_uci(str(STOCKFISH))
    engine.configure({"Threads": 1, "Hash": 64})
    written = 0
    skipped = 0
    started = time.perf_counter()
    try:
        with out.open("a", encoding="utf-8") as handle:
            for parent in todo:
                try:
                    group = build_group(engine, control, parent)
                except (
                    chess.engine.EngineError,
                    chess.engine.EngineTerminatedError,
                ):
                    with contextlib.suppress(Exception):
                        engine.quit()
                    engine = chess.engine.SimpleEngine.popen_uci(str(STOCKFISH))
                    engine.configure({"Threads": 1, "Hash": 64})
                    continue
                if group is None:
                    skipped += 1
                    continue
                handle.write(json.dumps(group) + "\n")
                written += 1
                if written % 200 == 0:
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
    ap.add_argument("--parents", type=int, default=16000)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--seed", type=int, default=20260909)
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
            worker, [(i, args.workers, str(plan)) for i in range(args.workers)]
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
        "nodes": NODES,
        "multipv": MULTIPV,
        "clamp_cp": CLAMP_CP,
        "label_engine": LABEL_ENGINE,
        "elapsed_seconds": round(elapsed, 1),
        "groups_per_hour": round(written / elapsed * 3600) if elapsed else 0,
        "shards": results,
    }
    (OUT_DIR / "generation_summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
