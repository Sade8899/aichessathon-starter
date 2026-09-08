"""Null-move-pruning OPPORTUNITY profile for the working control.

Nothing here modifies ``agent.py``. The control source is read and its SHA-256 is
asserted, then a *development copy* is produced by unique textual splices and loaded
under a throwaway module name. Two module-level hooks (``_nm_probe`` before the move
loop, ``_nm_record`` after it) run a fully side-effect-free shadow null-move search
at every eligible main-search node and tally what a conservative null-move pruning
policy *would* have done -- attempts, fail-highs, verified vs false cutoffs, and the
subtree work it would have removed -- without ever changing the control's search.
The shadow search runs with the transposition table, killers, history and
repetition counters swapped out, so it cannot perturb the control's tree.

``equivalence`` proves the spliced copy returns identical moves, root scores and node
counts to the untouched control at fixed depth, with the probe both disabled and
enabled.

Native Windows / CPython friendly: no ``resource``, no container assumptions, no
dependency on the repo's Linux-only test drivers.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import types
from pathlib import Path
from typing import Any

import chess

ROOT = Path(__file__).resolve().parent.parent
CONTROL = ROOT / "agent.py"
CONTROL_SHA = "65ec40ceb29a8f6fe14a74ab2ed6ca446164f701e46d2b9d3fe8d5af94655bda"

# --------------------------------------------------------------------------- #
# Instrumentation spliced into a development copy of the control.
# --------------------------------------------------------------------------- #

SPLICES: tuple[tuple[str, str], ...] = (
    # bypass the TT read while inside a shadow null / verification search
    (
        "        entry = self.table[slot]\n",
        "        entry = None if getattr(self, '_nm_in', 0) else self.table[slot]\n",
    ),
    # probe just before the move loop; capture node counter for subtree costing
    (
        """        original = alpha
        best = -INF
        best_move = moves[0]
        for index, move in enumerate(self.order(board, moves, preferred, ply)):
""",
        """        original = alpha
        best = -INF
        best_move = moves[0]
        _nm_ctx = _nm_probe(self, board, depth, alpha, beta, ply)
        _nm_entry_nodes = self.nodes
        for index, move in enumerate(self.order(board, moves, preferred, ply)):
""",
    ),
    # record after the loop; bypass the TT write while inside a shadow search
    (
        """        bound = -1 if best <= original else (1 if best >= beta else 0)
        if entry is None or entry.age != self.age or depth >= entry.depth:
""",
        """        bound = -1 if best <= original else (1 if best >= beta else 0)
        _nm_record(self, _nm_ctx, self.nodes - _nm_entry_nodes, best, beta, ply)
        if not getattr(self, '_nm_in', 0) and (
            entry is None or entry.age != self.age or depth >= entry.depth
        ):
""",
    ),
)

HOOKS = r'''

# ===================== null-move opportunity instrumentation ==================

_NM_ENABLED = True
_NM_R = 2
_NM_MIN_DEPTH = 3

_NM: dict[str, Any] = {}


def _nm_reset() -> None:
    _NM.clear()
    for name in (
        "main_nodes", "shadow_nodes",
        "in_check", "zero_window", "pv_window", "mate_window",
        "near_repetition", "pawn_only_stm", "low_material", "no_nonpawn_stm",
        "eligible", "eligible_stand_ge_beta",
        "null_attempts", "null_cutoffs", "null_nodes_total",
        "verified_cutoffs", "false_cutoffs",
        "false_cutoff_fail_low", "false_cutoff_suppressed_mate",
        "cutoff_in_pawn_only", "cutoff_in_low_material", "cutoff_in_zugzw_risk",
        "verified_in_pawn_only", "verified_in_low_material",
        "false_in_pawn_only", "false_in_low_material", "false_in_zugzw_risk",
        "saved_events", "saved_nodes",
        "stm_white_eligible", "stm_black_eligible",
        "stm_white_cutoff", "stm_black_cutoff",
        "stm_white_false", "stm_black_false",
    ):
        _NM[name] = 0
    for bucket in (
        "eligible_by_depth", "cutoff_by_depth", "false_by_depth",
        "eligible_by_phase", "cutoff_by_phase",
    ):
        _NM[bucket] = {}
    _NM["false_cutoff_detail"] = []


def _nm_phase(board: chess.Board) -> int:
    return min(
        24,
        (board.knights | board.bishops).bit_count()
        + 2 * board.rooks.bit_count()
        + 4 * board.queens.bit_count(),
    )


def _nm_bump(bucket: str, key: Any) -> None:
    d = _NM[bucket]
    d[key] = d.get(key, 0) + 1


def _nm_probe(engine: Any, board: chess.Board, depth: int, alpha: int, beta: int, ply: int):
    if not _NM_ENABLED:
        return None
    if getattr(engine, "_nm_in", 0):
        _NM["shadow_nodes"] += 1
        return None
    _NM["main_nodes"] += 1

    stm_white = board.turn == chess.WHITE
    in_check = board.is_check()
    zero_window = (beta - alpha) == 1
    mate_window = abs(beta) >= MATE - MAX_PLY or abs(alpha) >= MATE - MAX_PLY
    nonpawn_stm = bool(board.occupied_co[board.turn] & ~board.pawns & ~board.kings)
    pawn_only_stm = not nonpawn_stm
    nonpawn_all = (board.occupied & ~board.pawns & ~board.kings).bit_count()
    low_material = nonpawn_all <= 3
    stm_minor = (board.knights | board.bishops) & board.occupied_co[board.turn]
    stm_heavy = (board.rooks | board.queens) & board.occupied_co[board.turn]
    zugzw_risk = pawn_only_stm or (not stm_heavy and stm_minor.bit_count() <= 1)
    near_rep = engine.seen.get(position_key(board), 0) >= 1 or board.halfmove_clock >= 80
    phase = _nm_phase(board)

    _NM["in_check"] += int(in_check)
    _NM["zero_window"] += int(zero_window)
    _NM["pv_window"] += int(not zero_window)
    _NM["mate_window"] += int(mate_window)
    _NM["near_repetition"] += int(near_rep)
    _NM["pawn_only_stm"] += int(pawn_only_stm)
    _NM["low_material"] += int(low_material)
    _NM["no_nonpawn_stm"] += int(not nonpawn_stm)

    eligible = (
        not in_check and depth >= _NM_MIN_DEPTH and not mate_window and nonpawn_stm
    )
    if not eligible:
        return {"eligible": False}

    _NM["eligible"] += 1
    _nm_bump("eligible_by_depth", depth)
    _nm_bump("eligible_by_phase", phase)
    _NM["stm_white_eligible" if stm_white else "stm_black_eligible"] += 1

    stand = evaluate(board)
    if stand < beta:
        return {"eligible": True, "stand_ge_beta": False}
    _NM["eligible_stand_ge_beta"] += 1

    saved = (
        engine.killers, engine.history, engine.seen,
        engine.context, engine.duplicates, engine.nodes,
    )
    engine.killers = {}
    engine.history = {}
    engine.seen = type(engine.seen)()
    engine._nm_in = getattr(engine, "_nm_in", 0) + 1
    reduced = max(0, depth - 1 - _NM_R)
    board.push(chess.Move.null())
    try:
        null_score = -engine.search(board, reduced, -beta, -beta + 1, ply + 1)
    finally:
        board.pop()
        engine._nm_in -= 1
        null_nodes = engine.nodes - saved[5]
        (engine.killers, engine.history, engine.seen,
         engine.context, engine.duplicates, engine.nodes) = saved

    _NM["null_attempts"] += 1
    _NM["null_nodes_total"] += null_nodes
    cutoff = null_score >= beta
    ctx = {
        "eligible": True, "stand_ge_beta": True, "cutoff": cutoff,
        "null_nodes": null_nodes, "depth": depth, "ply": ply, "phase": phase,
        "pawn_only": pawn_only_stm, "low_material": low_material,
        "zugzw_risk": zugzw_risk, "stm_white": stm_white,
        "attribute": False, "null_score": null_score,
    }
    if cutoff:
        _NM["null_cutoffs"] += 1
        _nm_bump("cutoff_by_depth", depth)
        _nm_bump("cutoff_by_phase", phase)
        _NM["cutoff_in_pawn_only"] += int(pawn_only_stm)
        _NM["cutoff_in_low_material"] += int(low_material)
        _NM["cutoff_in_zugzw_risk"] += int(zugzw_risk)
        _NM["stm_white_cutoff" if stm_white else "stm_black_cutoff"] += 1
        if null_score >= MATE - MAX_PLY:
            _NM["false_cutoff_suppressed_mate"] += 1
        if getattr(engine, "_nm_under_cut", 0) == 0:
            ctx["attribute"] = True
            engine._nm_under_cut = getattr(engine, "_nm_under_cut", 0) + 1
    return ctx


def _nm_record(engine: Any, ctx: Any, subtree_nodes: int, best: int, beta: int, ply: int) -> None:
    if not _NM_ENABLED or not ctx or not ctx.get("stand_ge_beta") or not ctx.get("cutoff"):
        return
    if best >= beta:
        _NM["verified_cutoffs"] += 1
        _NM["verified_in_pawn_only"] += int(ctx["pawn_only"])
        _NM["verified_in_low_material"] += int(ctx["low_material"])
    else:
        _NM["false_cutoffs"] += 1
        _nm_bump("false_by_depth", ctx["depth"])
        _NM["false_in_pawn_only"] += int(ctx["pawn_only"])
        _NM["false_in_low_material"] += int(ctx["low_material"])
        _NM["false_in_zugzw_risk"] += int(ctx["zugzw_risk"])
        _NM["stm_white_false" if ctx["stm_white"] else "stm_black_false"] += 1
        if best <= 0 < beta or best < -300:
            _NM["false_cutoff_fail_low"] += 1
        if len(_NM["false_cutoff_detail"]) < 200:
            _NM["false_cutoff_detail"].append({
                "depth": ctx["depth"], "ply": ctx["ply"], "phase": ctx["phase"],
                "best": best, "beta": beta, "null_score": ctx["null_score"],
                "pawn_only": ctx["pawn_only"], "low_material": ctx["low_material"],
                "zugzw_risk": ctx["zugzw_risk"], "stm_white": ctx["stm_white"],
            })
    if ctx.get("attribute"):
        engine._nm_under_cut -= 1
        saving = subtree_nodes - ctx["null_nodes"]
        if saving > 0:
            _NM["saved_events"] += 1
            _NM["saved_nodes"] += saving


# ============================================================================
'''


def control_source() -> str:
    raw = CONTROL.read_bytes()
    got = hashlib.sha256(raw).hexdigest()
    assert got == CONTROL_SHA, f"control SHA mismatch: {got}"
    return raw.decode().replace("\r\n", "\n")


def instrumented_source(enabled: bool = True) -> str:
    src = control_source()
    for old, new in SPLICES:
        assert src.count(old) == 1, ("splice not unique", old[:60])
        src = src.replace(old, new)
    hooks = HOOKS if enabled else HOOKS.replace("_NM_ENABLED = True", "_NM_ENABLED = False")
    return src + hooks


def load(name: str, source: str) -> types.ModuleType:
    module = types.ModuleType(name)
    module.__dict__["__file__"] = str(CONTROL)
    sys.modules[name] = module
    exec(compile(source, name, "exec"), module.__dict__)
    return module


def fresh(module: types.ModuleType) -> None:
    module._engine = module.Engine()  # type: ignore[attr-defined]
    if hasattr(module, "_nm_reset"):
        module._nm_reset()


def fixed_search(module: types.ModuleType, fen: str, depth: int) -> dict[str, Any]:
    """One search stopped exactly after completing iteration ``depth``.

    Iterative deepening is halted by raising ``Deadline`` the instant the engine
    begins iteration ``depth + 1`` (``ply == 1 and remaining == depth``), matching
    the repo's own ``selection.fixed_engine`` mechanism. A large clock is passed so
    the real wall-clock deadline never bites first.
    """
    base = module.Engine

    class Fixed(base):  # type: ignore[misc, valid-type]
        def search(self, board: Any, remaining: int, alpha: int, beta: int, ply: int) -> int:
            if ply == 1 and remaining == depth and not getattr(self, "_nm_in", 0):
                raise module.Deadline
            result: int = base.search(self, board, remaining, alpha, beta, ply)
            return result

    module._engine = Fixed()  # type: ignore[attr-defined]
    if hasattr(module, "_nm_reset"):
        module._nm_reset()
    move = module.get_move(fen, 3_600_000)
    engine = module._engine
    return {
        "move": move,
        "depth": engine.stats["depth"],
        "scores": {m.uci(): v for m, v in engine.completed_scores.items()},
        "nodes": engine.nodes,
    }


def timed_search(module: types.ModuleType, fen: str, clock_ms: int) -> dict[str, Any]:
    fresh(module)
    started = time.perf_counter()
    move = module.get_move(fen, clock_ms)
    elapsed = time.perf_counter() - started
    engine = module._engine
    return {
        "move": move,
        "depth": engine.stats["depth"],
        "nodes": engine.nodes,
        "seconds": elapsed,
        "score": engine.completed_scores.get(chess.Move.from_uci(move)),
    }


def suite(n: int = 16) -> list[str]:
    import random
    rng = random.Random(702)
    out: list[str] = []
    while len(out) < n:
        b = chess.Board()
        for _ in range(rng.randrange(4, 26)):
            if b.is_game_over():
                break
            b.push(rng.choice(list(b.legal_moves)))
        if not b.is_game_over():
            out.append(b.fen())
    return out


def equivalence() -> None:
    control = load("nm_control", control_source())
    off = load("nm_off", instrumented_source(enabled=False))
    on = load("nm_on", instrumented_source(enabled=True))
    fens = suite(16)
    rows = 0
    for i, fen in enumerate(fens):
        for depth in (2, 3):
            base = fixed_search(control, fen, depth)
            for mod, label in ((off, "disabled"), (on, "enabled")):
                got = fixed_search(mod, fen, depth)
                for f in ("move", "depth", "scores", "nodes"):
                    assert base[f] == got[f], (label, fen, depth, f, base[f], got[f])
                rows += 1
        print(f"  eq {i + 1}/{len(fens)} ok", flush=True)
    print("EQUIVALENCE " + json.dumps({
        "control_sha256": CONTROL_SHA,
        "comparisons": rows,
        "identical_move_score_nodes": True,
        "depths": [2, 3],
        "probe_disabled_and_enabled_both_match_control": True,
    }))


def load_fixtures() -> list[dict[str, Any]]:
    data: Any = json.loads((ROOT / "tests" / "rated_v4_positions.json").read_text())
    return list(data["positions"])


TARGET_IDS = {
    "r45-54-Rc7", "r46-30-Ka4", "r53-35-Rf7",
    "r55-19-g5", "r55-20-Nxd4", "r56-17-f4",
}

HIST_CLOCK_MS = {
    "r46-30-Ka4": 68_700,
    "r55-19-g5": 86_656,
    "r56-17-f4": 93_400,
}


def profile(mode: str, depths: list[int], clock_ms: int) -> None:
    mod = load("nm_profile", instrumented_source(enabled=True))
    fixtures = load_fixtures()
    rows: list[dict[str, Any]] = []
    agg_key = "fixed" if mode == "fixed" else "timed"

    for fx in fixtures:
        fid, fen = fx["id"], fx["fen"]
        board = chess.Board(fen)
        is_target = fid in TARGET_IDS
        want = depths if mode == "fixed" else [fx.get("minimum_correcting_depth", 4)]
        for depth in want:
            mod._nm_reset()
            if mode == "fixed":
                res = fixed_search(mod, fen, depth)
            else:
                res = timed_search(mod, fen, HIST_CLOCK_MS.get(fid, clock_ms))
            nm = dict(mod._NM)
            main_nodes = nm["main_nodes"]
            elig = nm["eligible"]
            row = {
                "id": fid, "target": is_target,
                "enforced": fx.get("enforced", False), "kind": fx.get("kind"),
                "side_to_move": "white" if board.turn else "black",
                "mode": agg_key, "depth_req": depth,
                "completed_depth": res["depth"], "move": res["move"],
                "acceptable_uci": fx.get("acceptable_uci"),
                "unacceptable_uci": fx.get("unacceptable_uci"),
                "engine_nodes": res["nodes"], "main_search_nodes": main_nodes,
                "quiescence_share": 1.0 - (main_nodes / max(1, res["nodes"])),
                "eligible_nodes": elig,
                "eligible_pct_of_main": elig / max(1, main_nodes),
                "eligible_stand_ge_beta": nm["eligible_stand_ge_beta"],
                "null_attempts": nm["null_attempts"],
                "null_cutoffs": nm["null_cutoffs"],
                "verified_cutoffs": nm["verified_cutoffs"],
                "false_cutoffs": nm["false_cutoffs"],
                "false_cutoff_fail_low": nm["false_cutoff_fail_low"],
                "false_cutoff_suppressed_mate": nm["false_cutoff_suppressed_mate"],
                "cutoff_in_pawn_only": nm["cutoff_in_pawn_only"],
                "cutoff_in_low_material": nm["cutoff_in_low_material"],
                "cutoff_in_zugzw_risk": nm["cutoff_in_zugzw_risk"],
                "false_in_zugzw_risk": nm["false_in_zugzw_risk"],
                "saved_events": nm["saved_events"], "saved_nodes": nm["saved_nodes"],
                "projected_tree_reduction": nm["saved_nodes"] / max(1, res["nodes"]),
                "in_check_nodes": nm["in_check"],
                "mate_window_nodes": nm["mate_window"],
                "near_repetition_nodes": nm["near_repetition"],
                "pawn_only_stm_nodes": nm["pawn_only_stm"],
                "low_material_nodes": nm["low_material"],
                "stm_white_eligible": nm["stm_white_eligible"],
                "stm_black_eligible": nm["stm_black_eligible"],
                "stm_white_cutoff": nm["stm_white_cutoff"],
                "stm_black_cutoff": nm["stm_black_cutoff"],
                "eligible_by_depth": nm["eligible_by_depth"],
                "cutoff_by_depth": nm["cutoff_by_depth"],
                "false_by_depth": nm["false_by_depth"],
                "false_cutoff_detail": nm["false_cutoff_detail"][:20],
                "seconds": res.get("seconds"),
                "R": mod._NM_R, "min_depth": mod._NM_MIN_DEPTH,
            }
            rows.append(row)
            tag = "TARGET" if is_target else ("enf" if fx.get("enforced") else "ctx")
            print(
                f"[{tag:6}] {fid:20} d{depth}->{res['depth']} {res['move']:6} "
                f"main={main_nodes:8d} elig={elig:6d} ({row['eligible_pct_of_main'] * 100:5.1f}%) "
                f"cut={nm['null_cutoffs']:5d} ver={nm['verified_cutoffs']:5d} "
                f"false={nm['false_cutoffs']:4d} "
                f"save={row['projected_tree_reduction'] * 100:5.1f}% "
                f"q={row['quiescence_share'] * 100:4.1f}%",
                flush=True,
            )

    tgt = [r for r in rows if r["target"]]
    all_main = sum(r["main_search_nodes"] for r in rows)
    all_nodes = sum(r["engine_nodes"] for r in rows)
    all_elig = sum(r["eligible_nodes"] for r in rows)
    all_cut = sum(r["null_cutoffs"] for r in rows)
    all_ver = sum(r["verified_cutoffs"] for r in rows)
    all_false = sum(r["false_cutoffs"] for r in rows)
    all_saved = sum(r["saved_nodes"] for r in rows)
    all_supp_mate = sum(r["false_cutoff_suppressed_mate"] for r in rows)
    summary = {
        "mode": agg_key, "rows": len(rows), "control_sha256": CONTROL_SHA,
        "config": {"R": mod._NM_R, "min_depth": mod._NM_MIN_DEPTH},
        "totals": {
            "engine_nodes": all_nodes, "main_search_nodes": all_main,
            "main_share_of_tree": all_main / max(1, all_nodes),
            "quiescence_share_of_tree": 1 - all_main / max(1, all_nodes),
            "eligible_nodes": all_elig,
            "eligible_pct_of_main_nodes": all_elig / max(1, all_main),
            "null_cutoffs": all_cut, "verified_cutoffs": all_ver,
            "false_cutoffs": all_false,
            "false_cutoff_rate": all_false / max(1, all_cut),
            "false_cutoffs_suppressing_mate": all_supp_mate,
            "saved_nodes": all_saved,
            "projected_total_tree_reduction": all_saved / max(1, all_nodes),
        },
        "gate_10pct_eligible_main": (all_elig / max(1, all_main)) >= 0.10,
        "gate_20pct_tree_reduction": (all_saved / max(1, all_nodes)) >= 0.20,
        "gate_zero_mate_suppression": all_supp_mate == 0,
        "targets": {
            "ids": sorted(TARGET_IDS),
            "eligible_pct_of_main": (
                sum(r["eligible_nodes"] for r in tgt)
                / max(1, sum(r["main_search_nodes"] for r in tgt))
            ),
            "projected_tree_reduction": (
                sum(r["saved_nodes"] for r in tgt)
                / max(1, sum(r["engine_nodes"] for r in tgt))
            ),
            "false_cutoffs": sum(r["false_cutoffs"] for r in tgt),
            "false_cutoffs_suppressing_mate": sum(
                r["false_cutoff_suppressed_mate"] for r in tgt
            ),
        },
    }
    print("SUMMARY " + json.dumps(summary, indent=1), flush=True)
    outdir = ROOT / "tests" / "results" / "nullmove"
    outdir.mkdir(parents=True, exist_ok=True)
    out = outdir / f"opportunity_{agg_key}.json"
    out.write_text(json.dumps({"summary": summary, "rows": rows}, indent=1))
    print("WROTE " + str(out), flush=True)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("mode", choices=("equivalence", "fixed", "timed"))
    p.add_argument("--depths", default="2,3,4,5")
    p.add_argument("--clock-ms", type=int, default=90_000)
    a = p.parse_args()
    if a.mode == "equivalence":
        equivalence()
    else:
        profile(a.mode, [int(x) for x in a.depths.split(",") if x], a.clock_ms)


if __name__ == "__main__":
    main()
