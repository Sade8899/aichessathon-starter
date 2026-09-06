"""Development-only search profile of the submitted control.

Nothing here touches `agent.py`. The frozen control source is read by hash, and the
counters are spliced into a *development copy* by exact textual substitution, each
asserted to occur once. `equivalence` proves the spliced copy searches identically to
the untouched control, so its counts describe the control rather than themselves.

Component timings come from cProfile over the untouched control, because the splices
add work of their own. cProfile's cumulative times for nested callers overlap and
must not be summed.
"""

from __future__ import annotations

import argparse
import cProfile
import hashlib
import io
import json
import pstats
import statistics
import time
import types
from pathlib import Path
from typing import Any, cast

import chess
import numba_validation as numeric
from selection import load

CONTROL_SHA = "e5f63625a30f23ef7f1d625fbb5830f2bdbbed1b5480142a6e31b83cf731325b"
CONTROL = Path("tests/submitted") / CONTROL_SHA / "agent.py"

COUNTERS = """


_P: dict[str, object] = {}


def _reset() -> None:
    _P.clear()
    for name in (
        "search_nodes", "qnodes", "search_generations", "search_moves_generated",
        "q_generations", "q_moves_generated", "tt_probes", "tt_key_hits", "tt_usable",
        "tt_cutoffs", "cutoffs", "cutoff_first", "quiet_cutoffs", "late_quiet_cutoffs",
        "killer_cutoffs", "history_cutoffs", "other_quiet_cutoffs", "tt_move_cutoffs",
        "capture_cutoffs", "researches", "null_windows", "stand_pat_cutoffs",
        "nodes_with_cutoff", "nodes_searched_all", "order_calls",
    ):
        _P[name] = 0
    _P["cutoff_index"] = [0] * 12
    _P["quiet_cutoff_index"] = [0] * 12
"""

SPLICES: tuple[tuple[str, str], ...] = (
    # Quiescence node count and stand pat cutoffs.
    (
        """    def quiesce(self, board: chess.Board, alpha: int, beta: int, ply: int) -> int:
        self.tick()
""",
        """    def quiesce(self, board: chess.Board, alpha: int, beta: int, ply: int) -> int:
        self.tick()
        _P["qnodes"] += 1
""",
    ),
    (
        """            stand = evaluate(board)
            if stand >= beta:
                return stand
""",
        """            stand = evaluate(board)
            if stand >= beta:
                _P["stand_pat_cutoffs"] += 1
                return stand
""",
    ),
    (
        """            else:
                moves = captures_and_promotions(board)
""",
        """            else:
                moves = captures_and_promotions(board)
        if moves is not None:
            _P["q_generations"] += 1
            _P["q_moves_generated"] += len(moves)
""",
    ),
    # Main search node count and generated move volume.
    (
        """        self.tick()
        moves = list(board.legal_moves)
        if not moves:
""",
        """        self.tick()
        moves = list(board.legal_moves)
        _P["search_nodes"] += 1
        _P["search_generations"] += 1
        _P["search_moves_generated"] += len(moves)
        if not moves:
""",
    ),
    # Transposition table probe, key hit, usable entry and cutoff.
    (
        """        entry = self.table[slot]
""",
        """        entry = self.table[slot]
        _P["tt_probes"] += 1
""",
    ),
    (
        """        if entry is not None and entry.key == key:
            preferred = entry.move
""",
        """        if entry is not None and entry.key == key:
            preferred = entry.move
            _P["tt_key_hits"] += 1
""",
    ),
    (
        """                value = unpack_mate(entry.score, ply)
                if (
                    entry.bound == 0
                    or (entry.bound == 1 and value >= beta)
                    or (entry.bound == -1 and value <= alpha)
                ):
                    return value
""",
        """                value = unpack_mate(entry.score, ply)
                _P["tt_usable"] += 1
                if (
                    entry.bound == 0
                    or (entry.bound == 1 and value >= beta)
                    or (entry.bound == -1 and value <= alpha)
                ):
                    _P["tt_cutoffs"] += 1
                    return value
""",
    ),
    # Principal variation re-searches and the null windows that provoke them.
    (
        """                    score = -self.search(board, depth - 1, -alpha - 1, -alpha, ply + 1)
                    if alpha < score < beta:
""",
        """                    score = -self.search(board, depth - 1, -alpha - 1, -alpha, ply + 1)
                    _P["null_windows"] += 1
                    if alpha < score < beta:
                        _P["researches"] += 1
""",
    ),
    # Beta cutoff attribution, recorded before the killer table is updated.
    (
        """            if alpha >= beta:
                if quiet:
""",
        """            if alpha >= beta:
                _P["cutoffs"] += 1
                _P["cutoff_index"][min(index, 11)] += 1
                _P["cutoff_first"] += int(index == 0)
                _P["tt_move_cutoffs"] += int(move == preferred)
                if quiet:
                    _P["quiet_cutoffs"] += 1
                    _P["quiet_cutoff_index"][min(index, 11)] += 1
                    _P["late_quiet_cutoffs"] += int(index >= 3)
                    if move in self.killers.get(ply, (None, None)):
                        _P["killer_cutoffs"] += 1
                    elif self.history.get(
                        (board.turn, move.from_square, move.to_square), 0
                    ) > 0:
                        _P["history_cutoffs"] += 1
                    else:
                        _P["other_quiet_cutoffs"] += 1
                else:
                    _P["capture_cutoffs"] += 1
                if quiet:
""",
    ),
    # Ordering call volume.
    (
        """        return sorted(moves, key=priority, reverse=True)
""",
        """        _P["order_calls"] += 1
        return sorted(moves, key=priority, reverse=True)
""",
    ),
)


OPPORTUNITY: tuple[tuple[str, str], ...] = (
    # Expose the stand pat value to the move loop without altering control flow.
    (
        """        check = board.is_check()
""",
        """        check = board.is_check()
        _stand = None
""",
    ),
    (
        """            stand = evaluate(board)
            if stand >= beta:
                return stand
""",
        """            stand = evaluate(board)
            _stand = stand
            if stand >= beta:
                _O["stand_pat_cutoffs"] += 1
                return stand
""",
    ),
    (
        """        for move in self.order(board, moves, None, ply):
            board.push(move)
            key = self.enter(board)
""",
        """        for move in self.order(board, moves, None, ply):
            if _stand is not None:
                _quiescence_probe(board, move, _stand, alpha)
            board.push(move)
            key = self.enter(board)
""",
    ),
    # Late quiet moves in the main search that failed low are what a reduction targets.
    (
        """            if score > best:
                best, best_move = score, move
""",
        """            _O["main_moves_searched"] += 1
            if quiet:
                _O["main_quiet_searched"] += 1
                if index >= 3:
                    _O["late_quiet_searched"] += 1
                    if score <= alpha:
                        _O["late_quiet_failed_low"] += 1
            if score > best:
                best, best_move = score, move
""",
    ),
)

OPPORTUNITY_COUNTERS = """


_DELTA_MARGINS = (0, 100, 200, 400)
_O: dict[str, object] = {}


def _quiescence_probe(board, move, stand, alpha):
    _O["q_moves_considered"] += 1
    victim = board.piece_type_at(move.to_square) or (1 if board.is_en_passant(move) else 0)
    gain = VALUES[victim] + (VALUES[move.promotion] - 100 if move.promotion else 0)
    if not move.promotion:
        for index, margin in enumerate(_DELTA_MARGINS):
            if stand + gain + margin <= alpha:
                _O["delta_prunable"][index] += 1
    attacker = board.piece_type_at(move.from_square) or 0
    if victim and VALUES[victim] < VALUES[attacker]:
        _O["mvv_losing"] += 1
        if board.is_attacked_by(not board.turn, move.to_square):
            _O["see_losing_candidates"] += 1


def _reset_opportunity() -> None:
    for name in (
        "q_moves_considered", "see_losing_candidates", "mvv_losing", "stand_pat_cutoffs",
        "main_moves_searched", "main_quiet_searched", "late_quiet_searched",
        "late_quiet_failed_low",
    ):
        _O[name] = 0
    _O["delta_prunable"] = [0] * len(_DELTA_MARGINS)
"""


def control_source() -> str:
    raw = CONTROL.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == CONTROL_SHA, "control source hash mismatch"
    assert b"\r" not in raw
    return raw.decode()


def instrumented_source() -> str:
    source = control_source()
    for old, new in SPLICES:
        assert source.count(old) == 1, ("splice not unique", old[:60])
        source = source.replace(old, new)
    return source + COUNTERS


def opportunity_source() -> str:
    source = control_source()
    for old, new in OPPORTUNITY:
        assert source.count(old) == 1, ("opportunity splice not unique", old[:60])
        source = source.replace(old, new)
    return source + OPPORTUNITY_COUNTERS


def engines() -> tuple[types.ModuleType, types.ModuleType]:
    plain = load("profile_control", control_source())
    marked = load("profile_marked", instrumented_source())
    marked._reset()
    return plain, marked


def opportunity_engine() -> types.ModuleType:
    module: types.ModuleType = load("profile_opportunity", opportunity_source())
    module._reset_opportunity()
    return module


def fixed(module: types.ModuleType, fen: str, depth: int) -> dict[str, Any]:
    """One clock-free search to a fixed completed depth, through the real interface."""
    numeric.fresh(module, depth)
    real = module.time
    vars(module)["time"] = types.SimpleNamespace(perf_counter=lambda: 0.0)
    try:
        move = module.get_move(fen, 120_000)
    finally:
        vars(module)["time"] = real
    engine = module._engine
    assert engine.stats["depth"] == depth
    assert chess.Move.from_uci(move) in chess.Board(fen).legal_moves
    return {
        "move": move,
        "scores": {m.uci(): v for m, v in engine.completed_scores.items()},
        "nodes": engine.nodes,
    }


def equivalence() -> None:
    """Both spliced copies must search exactly as the control does."""
    plain, marked = engines()
    chance = opportunity_engine()
    rows = 0
    for fen in numeric.suite():
        for depth in (2, 3):
            expected = fixed(plain, fen, depth)
            for copy in (marked, chance):
                actual = fixed(copy, fen, depth)
                for field in ("move", "scores", "nodes"):
                    assert expected[field] == actual[field], (fen, depth, field)
                rows += 1
    print(
        "EQUIVALENCE "
        + json.dumps(
            {
                "control_sha256": CONTROL_SHA,
                "comparisons": rows,
                "moves_scores_nodes_identical": True,
                "counter_splices": len(SPLICES),
                "opportunity_splices": len(OPPORTUNITY),
            }
        ),
        flush=True,
    )


def opportunity(clock: int) -> None:
    """How much work each candidate technique could actually remove, measured."""
    module = opportunity_engine()
    suite = numeric.suite()
    module._reset_opportunity()
    started = time.perf_counter()
    nodes, depths_reached = 0, []
    for fen in suite:
        numeric.fresh(module)
        move = module.get_move(fen, clock)
        assert chess.Move.from_uci(move) in chess.Board(fen).legal_moves
        nodes += module._engine.nodes
        depths_reached.append(module._engine.stats["depth"])
    elapsed = time.perf_counter() - started
    counters = cast(dict[str, Any], module._O)
    considered = max(1, counters["q_moves_considered"])
    late = max(1, counters["late_quiet_searched"])
    print(
        "OPPORTUNITY "
        + json.dumps(
            {
                "clock_ms": clock,
                "positions": len(suite),
                "seconds": elapsed,
                "engine_nodes": nodes,
                "mean_completed_depth": statistics.mean(depths_reached),
                "quiescence_moves_considered": counters["q_moves_considered"],
                "delta_margins_cp": list(module._DELTA_MARGINS),
                "delta_prunable": counters["delta_prunable"],
                "delta_prunable_share": [
                    v / considered for v in cast(list[int], counters["delta_prunable"])
                ],
                "see_losing_candidates": counters["see_losing_candidates"],
                "see_losing_share": counters["see_losing_candidates"] / considered,
                "mvv_losing": counters["mvv_losing"],
                "main_moves_searched": counters["main_moves_searched"],
                "main_quiet_searched": counters["main_quiet_searched"],
                "late_quiet_searched": counters["late_quiet_searched"],
                "late_quiet_failed_low": counters["late_quiet_failed_low"],
                "late_quiet_share_of_main_moves": counters["late_quiet_searched"]
                / max(1, counters["main_moves_searched"]),
                "late_quiet_fail_low_rate": counters["late_quiet_failed_low"] / late,
            }
        ),
        flush=True,
    )


def summarize(counters: dict[str, Any], seconds: float, nodes: int) -> dict[str, Any]:
    total = counters["search_nodes"] + counters["qnodes"]
    cutoffs = max(1, counters["cutoffs"])
    quiet = max(1, counters["quiet_cutoffs"])
    return {
        "seconds": seconds,
        "engine_nodes": nodes,
        "search_nodes": counters["search_nodes"],
        "quiescence_nodes": counters["qnodes"],
        "quiescence_share": counters["qnodes"] / max(1, total),
        "nodes_per_second": nodes / seconds if seconds else 0.0,
        "moves_generated_search": counters["search_moves_generated"],
        "moves_generated_quiescence": counters["q_moves_generated"],
        "mean_branching_search": counters["search_moves_generated"]
        / max(1, counters["search_generations"]),
        "mean_branching_quiescence": counters["q_moves_generated"]
        / max(1, counters["q_generations"]),
        "order_calls": counters["order_calls"],
        "tt_probes": counters["tt_probes"],
        "tt_key_hit_rate": counters["tt_key_hits"] / max(1, counters["tt_probes"]),
        "tt_usable_rate": counters["tt_usable"] / max(1, counters["tt_probes"]),
        "tt_cutoff_rate": counters["tt_cutoffs"] / max(1, counters["tt_probes"]),
        "tt_cutoffs": counters["tt_cutoffs"],
        "stand_pat_cutoffs": counters["stand_pat_cutoffs"],
        "beta_cutoffs": counters["cutoffs"],
        "cutoff_first_move_rate": counters["cutoff_first"] / cutoffs,
        "cutoff_index_histogram": counters["cutoff_index"],
        "quiet_cutoffs": counters["quiet_cutoffs"],
        "quiet_cutoff_share": counters["quiet_cutoffs"] / cutoffs,
        "quiet_cutoff_index_histogram": counters["quiet_cutoff_index"],
        "late_quiet_cutoffs": counters["late_quiet_cutoffs"],
        "late_quiet_share_of_all_cutoffs": counters["late_quiet_cutoffs"] / cutoffs,
        "late_quiet_share_of_quiet_cutoffs": counters["late_quiet_cutoffs"] / quiet,
        "killer_cutoffs": counters["killer_cutoffs"],
        "killer_share_of_quiet": counters["killer_cutoffs"] / quiet,
        "history_cutoffs": counters["history_cutoffs"],
        "history_share_of_quiet": counters["history_cutoffs"] / quiet,
        "unattributed_quiet_cutoffs": counters["other_quiet_cutoffs"],
        "unattributed_share_of_quiet": counters["other_quiet_cutoffs"] / quiet,
        "tt_move_cutoffs": counters["tt_move_cutoffs"],
        "capture_cutoffs": counters["capture_cutoffs"],
        "null_windows": counters["null_windows"],
        "researches": counters["researches"],
        "research_rate": counters["researches"] / max(1, counters["null_windows"]),
    }


def counts(clock: int, depths: str, repeats: int) -> None:
    _, marked = engines()
    suite = numeric.suite()
    wanted = [int(v) for v in depths.split(",") if v]

    for depth in wanted:
        marked._reset()
        started = time.perf_counter()
        nodes = 0
        for fen in suite:
            fixed(marked, fen, depth)
            nodes += marked._engine.nodes
        elapsed = time.perf_counter() - started
        row = {"mode": f"fixed depth {depth}", "positions": len(suite)}
        row |= summarize(cast(dict[str, Any], marked._P), elapsed, nodes)
        print("PROFILE " + json.dumps(row), flush=True)

    for repeat in range(repeats):
        marked._reset()
        started = time.perf_counter()
        nodes, depths_reached = 0, []
        for fen in suite:
            numeric.fresh(marked)
            move = marked.get_move(fen, clock)
            assert chess.Move.from_uci(move) in chess.Board(fen).legal_moves
            nodes += marked._engine.nodes
            depths_reached.append(marked._engine.stats["depth"])
        elapsed = time.perf_counter() - started
        row = {
            "mode": f"timed {clock} ms",
            "repeat": repeat,
            "positions": len(suite),
            "mean_completed_depth": statistics.mean(depths_reached),
            "completed_depths": depths_reached,
        }
        row |= summarize(cast(dict[str, Any], marked._P), elapsed, nodes)
        print("PROFILE " + json.dumps(row), flush=True)


WATCHED = (
    "generate_legal_moves",
    "generate_pseudo_legal_moves",
    "_slider_blockers",
    "is_capture",
    "piece_type_at",
    "gives_check",
    "attacks_mask",
    "numeric_evaluate",
    "cached_evaluate",
    "compiled_evaluate",
    "captures_and_promotions",
    "quiesce",
    "search",
    "order",
    "priority",
    "position_key",
    "is_en_passant",
    "push",
    "pop",
)


def component_profile(clock: int) -> None:
    """cProfile the untouched control; cumulative times of nested callers overlap."""
    plain, _ = engines()
    suite = numeric.suite()

    def workload() -> None:
        for fen in suite:
            numeric.fresh(plain)
            plain.get_move(fen, clock)

    profiler = cProfile.Profile()
    profiler.enable()
    workload()
    profiler.disable()
    stream = io.StringIO()
    stats = pstats.Stats(profiler, stream=stream)
    total = cast(float, getattr(stats, "total_tt", 0.0))
    rows = []
    for (path, _, name), entry in stats.stats.items():  # type: ignore[attr-defined]
        if name not in WATCHED:
            continue
        calls, _, tottime, cumtime = entry[0], entry[1], entry[2], entry[3]
        rows.append(
            {
                "function": name,
                "module": Path(path).name,
                "calls": calls,
                "tottime_s": tottime,
                "cumtime_s": cumtime,
                "tottime_share": tottime / total if total else 0.0,
            }
        )
    rows.sort(key=lambda r: cast(float, r["tottime_s"]), reverse=True)
    print(
        "COMPONENTS "
        + json.dumps(
            {
                "clock_ms": clock,
                "positions": len(suite),
                "profiled_total_s": total,
                "note": "cumtime of nested callers overlaps; never sum these",
                "rows": rows,
            }
        ),
        flush=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "mode", choices=("equivalence", "counts", "components", "opportunity")
    )
    parser.add_argument("--clock", type=int, default=120_000)
    parser.add_argument("--depths", default="2,3,4")
    parser.add_argument("--repeats", type=int, default=2)
    args = parser.parse_args()
    if args.mode == "equivalence":
        equivalence()
    elif args.mode == "counts":
        counts(args.clock, args.depths, args.repeats)
    elif args.mode == "opportunity":
        opportunity(args.clock)
    else:
        component_profile(args.clock)


if __name__ == "__main__":
    main()
