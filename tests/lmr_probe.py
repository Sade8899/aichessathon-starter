"""Late-move-reduction OPPORTUNITY profile for the working control.

Same isolation approach as ``tests/nullmove_probe.py``: nothing here modifies
``agent.py``. The control source is read, its SHA-256 asserted, and a development
copy is produced by unique textual splices. For every move the control searches in
its main negamax/PVS loop, a hook runs a fully side-effect-free *shadow reduced
scout* (one ply shallower, same zero-window) with the transposition table, killers,
history, repetition counters and node counter swapped out and restored, then
compares it against the control's own full-depth result for that move. It tallies
what a conservative one-ply LMR would have done -- eligible late quiet moves,
reduced fail-lows (real saves), reduced alpha-raisers (re-search overhead), missed
alpha-raisers and mate suppression -- without ever perturbing the control's tree.

``equivalence`` proves the spliced copy returns identical root move, completed
depth, every root score and exact node count vs the untouched control, probe both
disabled and enabled.

Native Windows / CPython friendly.
"""
# ruff: noqa: E501  (embedded source-splice strings mirror agent.py line length)

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
    # (D) bypass the TT read while inside a shadow reduced search
    (
        "        entry = self.table[slot]\n",
        "        entry = None if getattr(self, '_lmr_in', 0) else self.table[slot]\n",
    ),
    # (A) probe each move before it is pushed; the probe does its own push/pop
    (
        """            quiet = not board.is_capture(move) and not move.promotion
            gain = 0
""",
        """            quiet = not board.is_capture(move) and not move.promotion
            _lmr_ctx = _lmr_probe(self, board, move, index, quiet, depth, alpha, beta, ply, preferred)
            gain = 0
""",
    ),
    # (B) mark the node counter just before the real full-depth search of this move
    (
        """            child = self.enter(board)
            try:
""",
        """            child = self.enter(board)
            _lmr_before = self.nodes
            try:
""",
    ),
    # (C) record the real per-move result against the shadow reduced result
    (
        """            if score > best:
                best, best_move = score, move
""",
        """            _lmr_record(self, _lmr_ctx, self.nodes - _lmr_before, score, alpha, beta, best)
            if score > best:
                best, best_move = score, move
""",
    ),
    # (E) bypass the TT write while inside a shadow reduced search
    (
        """        bound = -1 if best <= original else (1 if best >= beta else 0)
        if entry is None or entry.age != self.age or depth >= entry.depth:
""",
        """        bound = -1 if best <= original else (1 if best >= beta else 0)
        if not getattr(self, '_lmr_in', 0) and (
            entry is None or entry.age != self.age or depth >= entry.depth
        ):
""",
    ),
)

HOOKS = r'''

# ===================== late-move-reduction opportunity instrumentation =======

_LMR_ENABLED = True
_LMR_R = 1            # exactly one ply, per the predeclared candidate shape
_LMR_MIN_DEPTH = 3
_LMR_MIN_INDEX = 3   # "sufficiently late in the ordered move list"

_LMR: dict[str, Any] = {}


def _lmr_reset() -> None:
    _LMR.clear()
    for name in (
        "main_nodes", "shadow_nodes",
        "child_searches", "quiet_child_searches", "first_move_searches",
        "eligible", "reduced_searches", "reduced_nodes_total",
        "reduced_fail_low", "reduced_alpha_raise",
        "correct_saves", "saved_nodes", "research_overhead_events",
        "research_overhead_nodes",
        "missed_alpha_raisers", "consequential_missed_alpha_raisers",
        "mate_suppression",
        "stm_white_eligible", "stm_black_eligible",
        "stm_white_saved_nodes", "stm_black_saved_nodes",
        "skip_not_quiet", "skip_first_moves", "skip_early_index", "skip_shallow",
        "skip_gives_check", "skip_node_in_check", "skip_tt_move", "skip_killer",
        "skip_mate_window", "skip_near_rep",
    ):
        _LMR[name] = 0
    for bucket in (
        "child_by_depth", "eligible_by_depth", "saved_by_depth",
        "child_by_index", "eligible_by_index",
        "reduced_fail_low_by_depth", "reduced_alpha_raise_by_depth",
    ):
        _LMR[bucket] = {}
    _LMR["missed_detail"] = []
    _LMR["mate_suppression_detail"] = []


def _lmr_bump(bucket: str, key: Any) -> None:
    d = _LMR[bucket]
    d[key] = d.get(key, 0) + 1


def _lmr_add(bucket: str, key: Any, amount: int) -> None:
    d = _LMR[bucket]
    d[key] = d.get(key, 0) + amount


def _lmr_probe(
    engine: Any, board: chess.Board, move: chess.Move, index: int, quiet: bool,
    depth: int, alpha: int, beta: int, ply: int, preferred: Any,
):
    if not _LMR_ENABLED:
        return None
    if getattr(engine, "_lmr_in", 0):
        _LMR["shadow_nodes"] += 1
        return None

    _LMR["child_searches"] += 1
    _lmr_bump("child_by_depth", depth)
    _lmr_bump("child_by_index", min(index, 12))
    if quiet:
        _LMR["quiet_child_searches"] += 1
    if index == 0:
        _LMR["first_move_searches"] += 1

    node_in_check = board.is_check()
    stm_white = board.turn == chess.WHITE
    # A genuine mate score sits in [MATE - MAX_PLY, MATE]; the +-INF search
    # sentinel is 32000 > MATE (30000), so it must NOT count as a mate window.
    def _is_mate_score(x: int) -> bool:
        return MATE - MAX_PLY <= abs(x) <= MATE
    mate_window = _is_mate_score(alpha) or _is_mate_score(beta)
    # A repetition boundary is the *next* occurrence being the third (draw). One
    # prior occurrence on the search stack is an ordinary transposition, not a risk.
    near_rep = engine.seen.get(position_key(board), 0) >= 2 or board.halfmove_clock >= 90
    killers = engine.killers.get(ply, (None, None))
    gives_check = board.gives_check(move)

    # ---- conservative eligibility, adapted to this negamax/PVS engine --------
    if not quiet:
        _LMR["skip_not_quiet"] += 1
        return {"eligible": False}
    if index == 0:
        _LMR["skip_first_moves"] += 1
        return {"eligible": False}
    if index < _LMR_MIN_INDEX:
        _LMR["skip_early_index"] += 1
        return {"eligible": False}
    if depth < _LMR_MIN_DEPTH:
        _LMR["skip_shallow"] += 1
        return {"eligible": False}
    if gives_check:
        _LMR["skip_gives_check"] += 1
        return {"eligible": False}
    if node_in_check:
        _LMR["skip_node_in_check"] += 1
        return {"eligible": False}
    if move == preferred:
        _LMR["skip_tt_move"] += 1
        return {"eligible": False}
    if move in killers:
        _LMR["skip_killer"] += 1
        return {"eligible": False}
    if mate_window:
        _LMR["skip_mate_window"] += 1
        return {"eligible": False}
    if near_rep:
        _LMR["skip_near_rep"] += 1
        return {"eligible": False}

    _LMR["eligible"] += 1
    _lmr_bump("eligible_by_depth", depth)
    _lmr_bump("eligible_by_index", min(index, 12))
    _LMR["stm_white_eligible" if stm_white else "stm_black_eligible"] += 1

    # ---- shadow reduced scout, state fully swapped out ----------------------
    saved = (
        engine.killers, engine.history, engine.seen,
        engine.context, engine.duplicates, engine.nodes,
    )
    engine.killers = {}
    engine.history = {}
    engine.seen = type(engine.seen)()
    engine._lmr_in = getattr(engine, "_lmr_in", 0) + 1
    reduced_depth = max(1, depth - 1 - _LMR_R)
    board.push(move)
    aborted = False
    try:
        red = -engine.search(board, reduced_depth, -alpha - 1, -alpha, ply + 1)
    except Deadline:
        red, aborted = alpha, True
    finally:
        board.pop()
        engine._lmr_in -= 1
        red_nodes = engine.nodes - saved[5]
        (engine.killers, engine.history, engine.seen,
         engine.context, engine.duplicates, engine.nodes) = saved
    if aborted:
        return {"eligible": False}

    _LMR["reduced_searches"] += 1
    _LMR["reduced_nodes_total"] += red_nodes
    red_fail_low = red <= alpha
    if red_fail_low:
        _LMR["reduced_fail_low"] += 1
        _lmr_bump("reduced_fail_low_by_depth", depth)
    else:
        _LMR["reduced_alpha_raise"] += 1
        _lmr_bump("reduced_alpha_raise_by_depth", depth)
    return {
        "eligible": True, "red": red, "red_nodes": red_nodes,
        "red_fail_low": red_fail_low, "depth": depth, "index": index,
        "ply": ply, "stm_white": stm_white, "near_rep": near_rep,
    }


def _lmr_record(
    engine: Any, ctx: Any, real_move_nodes: int, real_score: int,
    alpha: int, beta: int, best: int,
) -> None:
    if not _LMR_ENABLED or not ctx or not ctx.get("eligible") or getattr(engine, "_lmr_in", 0):
        return
    real_alpha_raise = real_score > alpha
    stmk = "stm_white_saved_nodes" if ctx["stm_white"] else "stm_black_saved_nodes"
    if ctx["red_fail_low"] and not real_alpha_raise:
        # LMR would have skipped the full-depth scout and been right.
        saved = real_move_nodes - ctx["red_nodes"]
        if saved > 0:
            _LMR["correct_saves"] += 1
            _LMR["saved_nodes"] += saved
            _LMR[stmk] += saved
            _lmr_add("saved_by_depth", ctx["depth"], saved)
    elif ctx["red_fail_low"] and real_alpha_raise:
        # reduced said "skip", full depth says this move raises alpha -> unprotected miss
        _LMR["missed_alpha_raisers"] += 1
        if real_score > best:
            _LMR["consequential_missed_alpha_raisers"] += 1
        if real_score >= MATE - MAX_PLY:
            _LMR["mate_suppression"] += 1
            if len(_LMR["mate_suppression_detail"]) < 100:
                _LMR["mate_suppression_detail"].append({
                    "depth": ctx["depth"], "ply": ctx["ply"], "index": ctx["index"],
                    "real_score": real_score, "alpha": alpha, "reduced": ctx["red"],
                })
        if len(_LMR["missed_detail"]) < 200:
            _LMR["missed_detail"].append({
                "depth": ctx["depth"], "ply": ctx["ply"], "index": ctx["index"],
                "real_score": real_score, "alpha": alpha, "reduced": ctx["red"],
                "best_before": best, "consequential": real_score > best,
                "near_rep": ctx["near_rep"], "stm_white": ctx["stm_white"],
            })
    else:
        # reduced scout raised alpha -> a real LMR would re-search full: wasted work
        _LMR["research_overhead_events"] += 1
        _LMR["research_overhead_nodes"] += ctx["red_nodes"]


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
    hooks = HOOKS if enabled else HOOKS.replace("_LMR_ENABLED = True", "_LMR_ENABLED = False")
    return src + hooks


def load(name: str, source: str) -> types.ModuleType:
    module = types.ModuleType(name)
    module.__dict__["__file__"] = str(CONTROL)
    sys.modules[name] = module
    exec(compile(source, name, "exec"), module.__dict__)
    return module


def fixed_search(module: types.ModuleType, fen: str, depth: int) -> dict[str, Any]:
    """One search stopped exactly after completing iteration ``depth``."""
    base = module.Engine

    class Fixed(base):  # type: ignore[misc, valid-type]
        def search(self, board: Any, remaining: int, alpha: int, beta: int, ply: int) -> int:
            if ply == 1 and remaining == depth and not getattr(self, "_lmr_in", 0):
                raise module.Deadline
            result: int = base.search(self, board, remaining, alpha, beta, ply)
            return result

    module._engine = Fixed()  # type: ignore[attr-defined]
    if hasattr(module, "_lmr_reset"):
        module._lmr_reset()
    move = module.get_move(fen, 3_600_000)
    engine = module._engine
    return {
        "move": move,
        "depth": engine.stats["depth"],
        "scores": {m.uci(): v for m, v in engine.completed_scores.items()},
        "nodes": engine.nodes,
    }


def timed_search(module: types.ModuleType, fen: str, clock_ms: int) -> dict[str, Any]:
    module._engine = module.Engine()  # type: ignore[attr-defined]
    if hasattr(module, "_lmr_reset"):
        module._lmr_reset()
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


def suite(n: int = 64) -> list[str]:
    import random
    rng = random.Random(702)
    out: list[str] = []
    while len(out) < n:
        b = chess.Board()
        for _ in range(rng.randrange(4, 30)):
            if b.is_game_over():
                break
            b.push(rng.choice(list(b.legal_moves)))
        if not b.is_game_over():
            out.append(b.fen())
    return out


def equivalence() -> None:
    control = load("lmr_control", control_source())
    off = load("lmr_off", instrumented_source(enabled=False))
    on = load("lmr_on", instrumented_source(enabled=True))
    fens = suite(64)
    rows = 0
    for i, fen in enumerate(fens):
        for depth in (2, 3):
            base = fixed_search(control, fen, depth)
            for mod, label in ((off, "disabled"), (on, "enabled")):
                got = fixed_search(mod, fen, depth)
                for f in ("move", "depth", "scores", "nodes"):
                    assert base[f] == got[f], (label, fen, depth, f, base[f], got[f])
                rows += 1
        if (i + 1) % 8 == 0:
            print(f"  eq {i + 1}/{len(fens)} ok", flush=True)
    print("EQUIVALENCE " + json.dumps({
        "control_sha256": CONTROL_SHA,
        "positions": len(fens),
        "comparisons": rows,
        "identical_move_depth_scores_nodes": True,
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
HIST_CLOCK_MS = {"r46-30-Ka4": 68_700, "r55-19-g5": 86_656, "r56-17-f4": 93_400}


def _row(fx: dict[str, Any], mode: str, depth: int, res: dict[str, Any],
         lmr: dict[str, Any]) -> dict[str, Any]:
    board = chess.Board(fx["fen"])
    child = lmr["child_searches"]
    elig = lmr["eligible"]
    net_saved = lmr["saved_nodes"] - lmr["research_overhead_nodes"]

    def _ratio(bucket_a: str, bucket_b: str, key: int) -> float:
        a = lmr[bucket_a].get(key, 0) or lmr[bucket_a].get(str(key), 0)
        b = lmr[bucket_b].get(key, 0) or lmr[bucket_b].get(str(key), 0)
        return a / b if b else 0.0

    return {
        "id": fx["id"], "target": fx["id"] in TARGET_IDS,
        "enforced": fx.get("enforced", False), "kind": fx.get("kind"),
        "side_to_move": "white" if board.turn else "black",
        "mode": mode, "depth_req": depth, "completed_depth": res["depth"],
        "move": res["move"], "acceptable_uci": fx.get("acceptable_uci"),
        "unacceptable_uci": fx.get("unacceptable_uci"),
        "engine_nodes": res["nodes"], "main_nodes": lmr["main_nodes"],
        "quiescence_share": 1.0 - lmr["main_nodes"] / max(1, res["nodes"]),
        "child_searches": child, "quiet_child_searches": lmr["quiet_child_searches"],
        "eligible": elig, "eligible_pct_of_child": elig / max(1, child),
        "eligible_pct_of_quiet_child": elig / max(1, lmr["quiet_child_searches"]),
        "elig_pct_child_d3": _ratio("eligible_by_depth", "child_by_depth", 3),
        "elig_pct_child_d4": _ratio("eligible_by_depth", "child_by_depth", 4),
        "reduced_searches": lmr["reduced_searches"],
        "reduced_fail_low": lmr["reduced_fail_low"],
        "reduced_alpha_raise": lmr["reduced_alpha_raise"],
        "correct_saves": lmr["correct_saves"], "saved_nodes": lmr["saved_nodes"],
        "research_overhead_events": lmr["research_overhead_events"],
        "research_overhead_nodes": lmr["research_overhead_nodes"],
        "net_saved_nodes": net_saved,
        "projected_tree_reduction": net_saved / max(1, res["nodes"]),
        "missed_alpha_raisers": lmr["missed_alpha_raisers"],
        "consequential_missed_alpha_raisers": lmr["consequential_missed_alpha_raisers"],
        "mate_suppression": lmr["mate_suppression"],
        "saved_by_depth": lmr["saved_by_depth"],
        "eligible_by_depth": lmr["eligible_by_depth"],
        "child_by_depth": lmr["child_by_depth"],
        "eligible_by_index": lmr["eligible_by_index"],
        "child_by_index": lmr["child_by_index"],
        "stm_white_eligible": lmr["stm_white_eligible"],
        "stm_black_eligible": lmr["stm_black_eligible"],
        "stm_white_saved_nodes": lmr["stm_white_saved_nodes"],
        "stm_black_saved_nodes": lmr["stm_black_saved_nodes"],
        "missed_detail": lmr["missed_detail"][:20],
        "mate_suppression_detail": lmr["mate_suppression_detail"][:20],
        "seconds": res.get("seconds"),
        "R": 1, "min_depth": _LMR_CFG["min_depth"], "min_index": _LMR_CFG["min_index"],
    }


_LMR_CFG = {"min_depth": 3, "min_index": 3}


def profile(mode: str, depths: list[int], clock_ms: int) -> None:
    mod = load("lmr_profile", instrumented_source(enabled=True))
    _LMR_CFG["min_depth"] = mod._LMR_MIN_DEPTH
    _LMR_CFG["min_index"] = mod._LMR_MIN_INDEX
    fixtures = load_fixtures()
    rows: list[dict[str, Any]] = []

    for fx in fixtures:
        fid, fen = fx["id"], fx["fen"]
        want = depths if mode == "fixed" else [fx.get("minimum_correcting_depth", 4)]
        for depth in want:
            mod._lmr_reset()
            if mode == "fixed":
                res = fixed_search(mod, fen, depth)
            else:
                res = timed_search(mod, fen, HIST_CLOCK_MS.get(fid, clock_ms))
            r = _row(fx, mode, depth, res, dict(mod._LMR))
            rows.append(r)
            tag = "TARGET" if r["target"] else ("enf" if r["enforced"] else "ctx")
            print(
                f"[{tag:6}] {fid:20} d{depth}->{res['depth']} {res['move']:6} "
                f"child={r['child_searches']:7d} elig={r['eligible']:6d} "
                f"(d3 {r['elig_pct_child_d3'] * 100:4.1f}% d4 {r['elig_pct_child_d4'] * 100:4.1f}%) "
                f"save={r['projected_tree_reduction'] * 100:5.1f}% "
                f"miss={r['missed_alpha_raisers']:3d}/{r['consequential_missed_alpha_raisers']:2d} "
                f"matesupp={r['mate_suppression']}",
                flush=True,
            )

    tgt = [r for r in rows if r["target"]]
    enf = [r for r in rows if r["enforced"]]

    def _sum(rs: list[dict[str, Any]], k: str) -> int:
        return sum(int(r[k]) for r in rs)

    def _depth_bucket_ratio(rs: list[dict[str, Any]], num: str, den: str, key: int) -> float:
        n = sum(r[num].get(key, 0) or r[num].get(str(key), 0) for r in rs)
        d = sum(r[den].get(key, 0) or r[den].get(str(key), 0) for r in rs)
        return n / d if d else 0.0

    all_nodes = _sum(rows, "engine_nodes")
    all_child = _sum(rows, "child_searches")
    all_elig = _sum(rows, "eligible")
    net_saved = _sum(rows, "net_saved_nodes")
    saved_le4 = sum(
        v for r in rows for k, v in r["saved_by_depth"].items() if int(k) <= 4
    )
    saved_ge5 = sum(
        v for r in rows for k, v in r["saved_by_depth"].items() if int(k) >= 5
    )
    targets_with_elig = sum(
        1 for fid in TARGET_IDS
        if any(r["id"] == fid and r["eligible"] > 0 for r in rows)
    )
    d3 = _depth_bucket_ratio(rows, "eligible_by_depth", "child_by_depth", 3)
    d4 = _depth_bucket_ratio(rows, "eligible_by_depth", "child_by_depth", 4)
    summary = {
        "mode": mode, "rows": len(rows), "control_sha256": CONTROL_SHA,
        "config": {"R": 1, "min_depth": _LMR_CFG["min_depth"], "min_index": _LMR_CFG["min_index"]},
        "totals": {
            "engine_nodes": all_nodes, "child_searches": all_child,
            "eligible": all_elig, "eligible_pct_of_child": all_elig / max(1, all_child),
            "eligible_pct_child_remaining_d3": d3,
            "eligible_pct_child_remaining_d4": d4,
            "reduced_searches": _sum(rows, "reduced_searches"),
            "reduced_fail_low": _sum(rows, "reduced_fail_low"),
            "reduced_alpha_raise": _sum(rows, "reduced_alpha_raise"),
            "correct_saves": _sum(rows, "correct_saves"),
            "saved_nodes": _sum(rows, "saved_nodes"),
            "research_overhead_nodes": _sum(rows, "research_overhead_nodes"),
            "net_saved_nodes": net_saved,
            "projected_total_tree_reduction": net_saved / max(1, all_nodes),
            "net_saved_at_depth_le4": saved_le4,
            "net_saved_at_depth_ge5": saved_ge5,
            "missed_alpha_raisers": _sum(rows, "missed_alpha_raisers"),
            "consequential_missed_alpha_raisers": _sum(rows, "consequential_missed_alpha_raisers"),
            "mate_suppression": _sum(rows, "mate_suppression"),
        },
        "enforced_corpus": {
            "rows": len(enf),
            "missed_alpha_raisers": _sum(enf, "missed_alpha_raisers"),
            "consequential_missed_alpha_raisers": _sum(enf, "consequential_missed_alpha_raisers"),
            "mate_suppression": _sum(enf, "mate_suppression"),
        },
        "targets": {
            "ids": sorted(TARGET_IDS),
            "with_eligible_moves": targets_with_elig,
            "eligible_pct_of_child": _sum(tgt, "eligible") / max(1, _sum(tgt, "child_searches")),
            "net_saved_nodes": _sum(tgt, "net_saved_nodes"),
            "mate_suppression": _sum(tgt, "mate_suppression"),
        },
        "opportunity_gate": {
            "g1_elig_ge_15pct_child_d3_and_d4": d3 >= 0.15 and d4 >= 0.15,
            "g2_ge4_targets_with_eligible": targets_with_elig >= 4,
            "g3_projected_tree_reduction_ge_20pct": (net_saved / max(1, all_nodes)) >= 0.20,
            "g4_zero_mate_suppression": _sum(rows, "mate_suppression") == 0,
            "g5_zero_enforced_consequential_missed": _sum(enf, "consequential_missed_alpha_raisers") == 0,
            "g6_saving_at_completed_depths": saved_le4 > 0 and saved_le4 >= saved_ge5,
        },
    }
    gate: dict[str, bool] = summary["opportunity_gate"]  # type: ignore[assignment]
    gate["ALL_PASS"] = all(gate.values())
    print("SUMMARY " + json.dumps(summary, indent=1), flush=True)
    outdir = ROOT / "tests" / "results" / "lmr"
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / f"opportunity_{mode}.json").write_text(
        json.dumps({"summary": summary, "rows": rows}, indent=1)
    )
    print("WROTE " + str(outdir / f"opportunity_{mode}.json"), flush=True)


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
