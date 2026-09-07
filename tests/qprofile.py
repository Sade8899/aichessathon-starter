"""Quiescence profile of the promoted control.

Two tiers, deliberately kept apart.

*Counts* come from an instrumented development copy of `agent.py`. The copy replaces
exactly one method, `Engine.quiesce`, with a version that keeps the control's logic
line for line and adds counters. `identity` proves the copy is faithful: every other
module-level definition and every other `Engine` method is compared by AST, and a
fixed-depth search over the corpus must return identical moves, identical scores and
identical node counts. Counters never influence a decision, so an instrumented run and
a control run explore the same tree.

*Times* come from `cProfile` over the unmodified control. Timing an instrumented build
would measure the instrumentation, so wall-time attribution never touches the copy.

The control is `agent.py`, SHA-256 `65ec40ce...`. It is opened read-only and loaded
under a private module name; nothing here writes to it.

Modes:

    identity    prove the instrumented copy matches the control exactly
    counts      the quiescence counter profile over one corpus
    hotspots    cProfile attribution over the clean control
    ply         what a further iterative-deepening ply actually costs
"""

from __future__ import annotations

import argparse
import ast
import cProfile
import hashlib
import importlib.util
import json
import pstats
import statistics
import sys
import time
import types
from collections import Counter
from pathlib import Path
from typing import Any

import chess

CONTROL_PATH = Path("agent.py")
CONTROL_SHA = "65ec40ceb29a8f6fe14a74ab2ed6ca446164f701e46d2b9d3fe8d5af94655bda"
RESULTS = Path("tests/results/qprofile")

# The control's `quiesce`, reproduced exactly, with counters interleaved. Every branch,
# every comparison and every early return matches the control; the only additions are
# `probe` bookkeeping calls, which return None and are never read by the logic.
INSTRUMENTED_QUIESCE = '''
    def quiesce(self, board: chess.Board, alpha: int, beta: int, ply: int) -> int:
        probe = self.probe
        probe["nodes"] += 1
        probe["depth_histogram"][ply] += 1
        if ply > probe["max_ply"]:
            probe["max_ply"] = ply
        self.tick()
        check = board.is_check()
        moves = list(board.legal_moves) if check else None
        if check:
            probe["in_check_nodes"] += 1
            probe["legal_generation_calls"] += 1
            probe["legal_moves_generated"] += len(moves or [])
        if check and not moves:
            probe["exit_mate"] += 1
            return -MATE + ply
        if self.drawn(board):
            probe["exit_draw"] += 1
            return 0
        if not check:
            probe["stalemate_probe_calls"] += 1
            if not any(board.generate_legal_moves()):
                probe["exit_stalemate"] += 1
                return 0
        if ply >= MAX_PLY:
            probe["exit_ply_cap"] += 1
            return evaluate(board)
        if not check:
            stand = evaluate(board)
            if stand >= beta:
                probe["stand_pat_beta_cutoffs"] += 1
                return stand
            if stand > alpha:
                probe["stand_pat_alpha_raises"] += 1
            alpha = max(alpha, stand)
            if ply < 3 and (self.pattern.tactical or self.pattern.attack):
                probe["quiet_check_generation_calls"] += 1
                moves = [
                    m
                    for m in board.legal_moves
                    if board.is_capture(m) or m.promotion or board.gives_check(m)
                ]
                probe["quiet_check_nodes"] += 1
            else:
                probe["tactical_generation_calls"] += 1
                moves = captures_and_promotions(board)
            probe["tactical_moves_generated"] += len(moves)
        assert moves is not None
        pruning = not check and not self.pattern.endgame
        searched = 0
        for index, move in enumerate(self.order(board, moves, None, ply)):
            if pruning and not move.promotion:
                victim = board.piece_type_at(move.to_square) or (
                    chess.PAWN if board.is_en_passant(move) else 0
                )
                if victim:
                    probe["delta_offers"] += 1
                    if stand + VALUES[victim] + DELTA_MARGIN <= alpha:
                        probe["delta_prunes"] += 1
                        probe["delta_prunes_by_victim"][victim] += 1
                        probe["delta_shortfall"].append(
                            alpha - (stand + VALUES[victim] + DELTA_MARGIN)
                        )
                        if board.gives_check(move):
                            probe["delta_pruned_checking_captures"] += 1
                        continue
                else:
                    probe["delta_skipped_quiet_check"] += 1
            searched += 1
            board.push(move)
            key = self.enter(board)
            if self.seen[key] > 1:
                probe["quiescence_repeats"] += 1
            try:
                score = -self.quiesce(board, -beta, -alpha, ply + 1)
            finally:
                self.leave(key)
                board.pop()
            if score >= beta:
                probe["beta_cutoffs"] += 1
                probe["cutoff_index"][min(index, 15)] += 1
                probe["tactical_moves_searched"] += searched
                return score
            alpha = max(alpha, score)
        probe["tactical_moves_searched"] += searched
        probe["exit_exhausted"] += 1
        return alpha
'''


# ------------------------------------------------------------------------- loading


def frozen(path: Path, sha: str) -> str:
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    assert digest == sha, (str(path), digest, sha)
    return raw.decode()


def load(name: str, text: str) -> types.ModuleType:
    spec = importlib.util.spec_from_loader(name, loader=None)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    exec(compile(text, name, "exec"), module.__dict__)
    return module


def control_source() -> str:
    return frozen(CONTROL_PATH, CONTROL_SHA)


def instrumented_source() -> str:
    """The control with `Engine.quiesce` swapped for the counting version."""
    text = control_source()
    tree = ast.parse(text)
    start, end = quiesce_span(tree)
    lines = text.splitlines(keepends=True)
    replaced = "".join(lines[: start - 1]) + INSTRUMENTED_QUIESCE.strip("\n") + "\n" + "".join(
        lines[end:]
    )
    return replaced


def quiesce_span(tree: ast.Module) -> tuple[int, int]:
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "Engine":
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == "quiesce":
                    assert item.end_lineno is not None
                    return item.lineno, item.end_lineno
    raise AssertionError("Engine.quiesce not found")


def fresh_probe() -> dict[str, Any]:
    return {
        "nodes": 0,
        "max_ply": 0,
        "depth_histogram": Counter(),
        "in_check_nodes": 0,
        "legal_generation_calls": 0,
        "legal_moves_generated": 0,
        "stalemate_probe_calls": 0,
        "quiet_check_generation_calls": 0,
        "quiet_check_nodes": 0,
        "tactical_generation_calls": 0,
        "tactical_moves_generated": 0,
        "tactical_moves_searched": 0,
        "stand_pat_beta_cutoffs": 0,
        "stand_pat_alpha_raises": 0,
        "delta_offers": 0,
        "delta_prunes": 0,
        "delta_prunes_by_victim": Counter(),
        "delta_pruned_checking_captures": 0,
        "delta_skipped_quiet_check": 0,
        "delta_shortfall": [],
        "beta_cutoffs": 0,
        "cutoff_index": Counter(),
        "quiescence_repeats": 0,
        "exit_mate": 0,
        "exit_draw": 0,
        "exit_stalemate": 0,
        "exit_ply_cap": 0,
        "exit_exhausted": 0,
    }


def prepare(module: types.ModuleType, board: chess.Board, instrumented: bool) -> Any:
    engine = module.Engine()
    engine.stats = {"model_seconds": 0.0, "depth": 0, "nodes": 0}
    engine.deadline = time.perf_counter() + 3600.0
    engine.enter(board)
    engine.pattern = module.recognise(board, module.evaluate(board))
    if instrumented:
        engine.probe = fresh_probe()
    return engine


def root(module: types.ModuleType, board: chess.Board, depth: int, instrumented: bool) -> Any:
    """Fixed-depth root search using the engine's own ordering; mirrors rated_v4.root."""
    work = board.copy()
    engine = prepare(module, work, instrumented)
    best_score = -module.INF
    best_move = next(iter(work.legal_moves))
    for move in engine.order(work, list(work.legal_moves), None, 0):
        work.push(move)
        key = engine.enter(work)
        try:
            value = -engine.search(work, depth - 1, -module.INF, -best_score, 1)
        finally:
            engine.leave(key)
            work.pop()
        if value > best_score:
            best_score, best_move = value, move
    return engine, best_move, best_score


# ------------------------------------------------------------------------ corpora


def corpus(name: str) -> list[dict[str, str]]:
    """The named position group. Every group is drawn from files already in the repo."""
    if name == "rated":
        rows: list[dict[str, str]] = []
        data = json.loads(Path("tests/results/rated_v4/scan_r55.json").read_text())
        for entry in data["rounds"]["55"]["moves"]:
            rows.append({"id": "r55 " + entry["move"], "fen": entry["fen_before"]})
        other = json.loads(Path("tests/results/rated_v4/scan_r54_r56.json").read_text())
        for round_id, block in other["rounds"].items():
            for entry in block["moves"]:
                worst = max(v["loss_cp"] for v in entry["depths"].values())
                if worst >= 50:
                    rows.append({"id": f"r{round_id} " + entry["move"], "fen": entry["fen_before"]})
        return rows
    if name == "tactics":
        data = json.loads(Path("tests/tactics_corpus.json").read_text())
        items = data["positions"] if isinstance(data, dict) else data
        return [
            {"id": str(item.get("id", index)), "fen": item["fen"]}
            for index, item in enumerate(items)
        ]
    if name == "quiet":
        data = json.loads(Path("tests/quiet_openings.json").read_text())
        return [
            {"id": item["san"], "fen": item["fen"]}
            for item in data["positions"]
        ]
    raise SystemExit(f"unknown corpus: {name}")


# ------------------------------------------------------------------------ identity


def identity(depth: int, groups: list[str]) -> dict[str, Any]:
    """The instrumented copy must be the control everywhere except inside `quiesce`."""
    control_text = control_source()
    probe_text = instrumented_source()
    control_tree = ast.parse(control_text)
    probe_tree = ast.parse(probe_text)

    def definitions(tree: ast.Module) -> dict[str, str]:
        # Keyed by name, and for bare module-level statements by their ordinal position,
        # never by line number: replacing `quiesce` shifts every later line, and a shifted
        # line is not a changed statement.
        out: dict[str, str] = {}
        ordinal = 0
        for node in tree.body:
            if isinstance(node, ast.FunctionDef | ast.ClassDef):
                if isinstance(node, ast.ClassDef):
                    for item in node.body:
                        if isinstance(item, ast.FunctionDef):
                            out[f"{node.name}.{item.name}"] = ast.dump(item)
                    body = [i for i in node.body if not isinstance(i, ast.FunctionDef)]
                    out[node.name] = ast.dump(ast.Module(body=body, type_ignores=[]))
                else:
                    out[node.name] = ast.dump(node)
            else:
                out[f"<statement {ordinal}>"] = ast.dump(node)
                ordinal += 1
        return out

    left, right = definitions(control_tree), definitions(probe_tree)
    added = sorted(set(right) - set(left))
    removed = sorted(set(left) - set(right))
    differing = sorted(k for k in set(left) & set(right) if left[k] != right[k])

    control = load("qprofile_control", control_text)
    probe = load("qprofile_probe", probe_text)
    mismatches: list[dict[str, Any]] = []
    compared = 0
    for group in groups:
        for row in corpus(group):
            board = chess.Board(row["fen"])
            a_engine, a_move, a_score = root(control, board, depth, False)
            b_engine, b_move, b_score = root(probe, board, depth, True)
            compared += 1
            if (a_move, a_score, a_engine.nodes) != (b_move, b_score, b_engine.nodes):
                mismatches.append(
                    {
                        "id": row["id"],
                        "control": [a_move.uci(), a_score, a_engine.nodes],
                        "instrumented": [b_move.uci(), b_score, b_engine.nodes],
                    }
                )
    return {
        "depth": depth,
        "groups": groups,
        "positions_compared": compared,
        "added_definitions": added,
        "removed_definitions": removed,
        "differing_definitions": differing,
        "only_quiesce_differs": differing == ["Engine.quiesce"] and not added and not removed,
        "search_mismatches": mismatches,
        "passed": (
            differing == ["Engine.quiesce"]
            and not added
            and not removed
            and not mismatches
        ),
    }


# -------------------------------------------------------------------------- counts


def counts(depth: int, group: str) -> dict[str, Any]:
    module = load("qprofile_probe_counts", instrumented_source())
    per_position: list[dict[str, Any]] = []
    total = fresh_probe()
    total_nodes = 0
    for row in corpus(group):
        board = chess.Board(row["fen"])
        engine, move, score = root(module, board, depth, True)
        probe = engine.probe
        total_nodes += engine.nodes
        for key, value in probe.items():
            if isinstance(value, Counter):
                total[key].update(value)
            elif isinstance(value, list):
                total[key].extend(value)
            elif key == "max_ply":
                total[key] = max(total[key], value)
            else:
                total[key] += value
        per_position.append(
            {
                "id": row["id"],
                "best": move.uci(),
                "score": score,
                "total_nodes": engine.nodes,
                "quiescence_nodes": probe["nodes"],
                "quiescence_share": round(probe["nodes"] / max(engine.nodes, 1), 4),
                "max_q_ply": probe["max_ply"],
                "stand_pat_beta_cutoffs": probe["stand_pat_beta_cutoffs"],
                "tactical_generated": probe["tactical_moves_generated"],
                "tactical_searched": probe["tactical_moves_searched"],
                "delta_offers": probe["delta_offers"],
                "delta_prunes": probe["delta_prunes"],
            }
        )
    shortfalls = total.pop("delta_shortfall")
    generating_calls = total["tactical_generation_calls"] + total["quiet_check_generation_calls"]
    summary = {
        "group": group,
        "depth": depth,
        "positions": len(per_position),
        "total_nodes": total_nodes,
        "quiescence_nodes": total["nodes"],
        "main_nodes": total_nodes - total["nodes"],
        "quiescence_share": round(total["nodes"] / max(total_nodes, 1), 4),
        "quiescence_share_median": round(
            statistics.median(p["quiescence_share"] for p in per_position), 4
        )
        if per_position
        else 0.0,
        "depth_histogram": dict(sorted(total["depth_histogram"].items())),
        "max_q_ply": total["max_ply"],
        "delta_prunes_by_victim": dict(sorted(total["delta_prunes_by_victim"].items())),
        "cutoff_index": dict(sorted(total["cutoff_index"].items())),
        "delta_shortfall_median": round(statistics.median(shortfalls), 1) if shortfalls else None,
        "counters": {
            k: v
            for k, v in total.items()
            if not isinstance(v, Counter | list)
        },
        "derived": {
            "stand_pat_beta_cutoff_rate": round(
                total["stand_pat_beta_cutoffs"] / max(total["nodes"], 1), 4
            ),
            "stalemate_probes_per_quiescence_node": round(
                total["stalemate_probe_calls"] / max(total["nodes"], 1), 4
            ),
            "stalemate_probes_wasted_on_beta_cutoff": total["stand_pat_beta_cutoffs"],
            "tactical_moves_per_generating_node": round(
                total["tactical_moves_generated"] / max(generating_calls, 1), 3
            ),
            "tactical_searched_share": round(
                total["tactical_moves_searched"] / max(total["tactical_moves_generated"], 1), 4
            ),
            "delta_prune_rate": round(total["delta_prunes"] / max(total["delta_offers"], 1), 4),
        },
        "positions_detail": per_position,
    }
    return summary


# ------------------------------------------------------------------------ hotspots


def hotspots(depth: int, group: str, top: int) -> dict[str, Any]:
    """cProfile attribution over the clean control, so nothing measures instrumentation."""
    module = load("qprofile_hot", control_source())
    boards = [chess.Board(row["fen"]) for row in corpus(group)]

    def workload() -> None:
        for board in boards:
            root(module, board, depth, False)

    started = time.perf_counter()
    profiler = cProfile.Profile()
    profiler.enable()
    workload()
    profiler.disable()
    profiled_wall = time.perf_counter() - started

    stats = pstats.Stats(profiler)
    # `Stats.stats` is populated at runtime but missing from the stubs, and it is the
    # only place per-function tottime lives, so the attribute is read through `vars`.
    table: dict[tuple[str, int, str], tuple[int, int, float, float, Any]] = vars(stats)["stats"]
    rows: list[dict[str, Any]] = []
    for (filename, line, name), (calls, _, tottime, cumtime, _) in table.items():
        rows.append(
            {
                "function": f"{Path(filename).name}:{line}:{name}",
                "calls": calls,
                "tottime_s": round(tottime, 4),
                "cumtime_s": round(cumtime, 4),
            }
        )
    rows.sort(key=lambda r: -float(r["tottime_s"]))
    total_tottime = sum(float(r["tottime_s"]) for r in rows)

    # An unprofiled repeat gives the honest wall time; cProfile roughly doubles it.
    started = time.perf_counter()
    workload()
    clean_wall = time.perf_counter() - started

    for row in rows:
        row["share_of_tottime"] = round(float(row["tottime_s"]) / max(total_tottime, 1e-9), 4)
        # Amdahl: removing this hotspot entirely can save at most its own share.
        row["max_speedup_if_removed"] = round(
            1.0 / max(1.0 - float(row["share_of_tottime"]), 1e-9), 3
        )
    return {
        "group": group,
        "depth": depth,
        "positions": len(boards),
        "profiled_wall_s": round(profiled_wall, 3),
        "clean_wall_s": round(clean_wall, 3),
        "profiler_overhead_ratio": round(profiled_wall / max(clean_wall, 1e-9), 2),
        "total_tottime_s": round(total_tottime, 3),
        "hotspots": rows[:top],
    }


# ----------------------------------------------------------------------------- ply


def ply(group: str, depths: tuple[int, ...]) -> dict[str, Any]:
    """What one more iterative-deepening ply costs, measured on the clean control."""
    module = load("qprofile_ply", control_source())
    rows: list[dict[str, Any]] = []
    for row in corpus(group):
        board = chess.Board(row["fen"])
        entry: dict[str, Any] = {"id": row["id"], "fen": row["fen"], "depths": {}}
        previous: float | None = None
        for depth in depths:
            started = time.perf_counter()
            engine, move, score = root(module, board, depth, False)
            spent = time.perf_counter() - started
            entry["depths"][str(depth)] = {
                "seconds": round(spent, 3),
                "nodes": engine.nodes,
                "best": move.uci(),
                "score": score,
                "branch_factor": round(spent / previous, 2) if previous else None,
            }
            previous = spent
        rows.append(entry)
        print(
            f"{row['id']:>22} "
            + " ".join(
                f"d{d}:{entry['depths'][str(d)]['seconds']:.2f}s" for d in depths
            ),
            file=sys.stderr,
            flush=True,
        )
    factors = [
        v["branch_factor"]
        for r in rows
        for v in r["depths"].values()
        if v["branch_factor"] is not None
    ]
    return {
        "group": group,
        "depths": list(depths),
        "median_time_multiplier_per_ply": round(statistics.median(factors), 2)
        if factors
        else None,
        "mean_time_multiplier_per_ply": round(statistics.fmean(factors), 2) if factors else None,
        "note": (
            "A speedup of at least the per-ply multiplier is needed before the next ply "
            "completes inside the same clock."
        ),
        "positions": rows,
    }


# ---------------------------------------------------------------------------- main


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("identity", "counts", "hotspots", "ply"))
    parser.add_argument("--depth", type=int, default=4)
    parser.add_argument("--depths", default="2,3,4,5")
    parser.add_argument("--group", default="rated")
    parser.add_argument("--groups", default="rated,tactics,quiet")
    parser.add_argument("--top", type=int, default=25)
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    if args.mode == "identity":
        report: dict[str, Any] = identity(args.depth, args.groups.split(","))
    elif args.mode == "counts":
        report = counts(args.depth, args.group)
    elif args.mode == "hotspots":
        report = hotspots(args.depth, args.group, args.top)
    else:
        report = ply(args.group, tuple(int(d) for d in args.depths.split(",")))

    text = json.dumps(report, indent=2, default=str)
    if args.out:
        target = Path(args.out)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        print(f"wrote {target} ({len(text)} bytes)")
    else:
        print(text)


if __name__ == "__main__":
    main()
