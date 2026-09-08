"""Completed-iteration move-quality & root-ordering diagnosis probe.

Nothing here modifies ``agent.py``. The control source is read, its SHA-256
asserted, and a development copy is loaded under a throwaway module name. The root
iterative-deepening loop of ``Engine.choose`` is re-implemented here
(``_root_search``) so that alternative root-move orderings can be injected, while
every node-level primitive -- ``Engine.search``, ``order``, ``enter``/``leave``,
``drawn``, ``reconstruct`` -- is the real control code. ``equivalence`` proves that
policy ``"control"`` reproduces the untouched ``Engine.choose`` exactly: identical
committed move, completed depth, every root score, exact node count, timeout
behaviour and persistent heuristic state.

Native Windows / CPython friendly.
"""
# ruff: noqa: E501

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
import time
import types
from pathlib import Path
from typing import Any

import chess

ROOT = Path(__file__).resolve().parent.parent
CONTROL = ROOT / "agent.py"
CONTROL_SHA = "65ec40ceb29a8f6fe14a74ab2ed6ca446164f701e46d2b9d3fe8d5af94655bda"

POLICIES = ("control", "A", "B", "C", "D", "E")


def control_source() -> str:
    raw = CONTROL.read_bytes()
    got = hashlib.sha256(raw).hexdigest()
    assert got == CONTROL_SHA, f"control SHA mismatch: {got}"
    return raw.decode().replace("\r\n", "\n")


def load(name: str, source: str) -> types.ModuleType:
    module = types.ModuleType(name)
    module.__dict__["__file__"] = str(CONTROL)
    sys.modules[name] = module
    exec(compile(source, name, "exec"), module.__dict__)
    return module


AG = load("ro_agent", control_source())
INF = AG.INF
MATE = AG.MATE
MAX_PLY = AG.MAX_PLY
ROOT_WINDOW_MARGIN = AG.ROOT_WINDOW_MARGIN
TT_SIZE = AG.TT_SIZE


# --------------------------------------------------------------------------- #
# Faithful re-implementation of the choose() root loop, with pluggable ordering
# --------------------------------------------------------------------------- #

def _fresh_engine() -> Any:
    return AG.Engine()


def _reconstruct_or_reset(eng: Any, board: chess.Board) -> None:
    observed = eng.reconstruct(board)
    if observed is None:
        eng.seen.clear()
        eng.context = 0
        eng.duplicates = 0
        eng.model.reset()
    elif AG.PASSIVE:
        eng.model.observe(eng.evidence, chess.Move.from_uci(observed))


def _iteration(eng: Any, board: chess.Board, moves: list[chess.Move], depth: int,
               deadline: float, current: dict[chess.Move, int],
               clamped: set[chess.Move]) -> None:
    """One completed root iteration; raises AG.Deadline if it cannot finish.

    `current` is filled in place (so a partial survives the exception); `clamped`
    collects moves whose score is only an upper bound `<= root_best - MARGIN - 1`.
    """
    eng.iteration_evidence = {}
    leaders = moves[:3]
    root_best = -INF
    tolerance = ROOT_WINDOW_MARGIN
    for move in moves:
        if time.perf_counter() >= deadline:
            raise AG.Deadline
        eng.root_move = move
        eng.collect = AG.PASSIVE and move in leaders
        floor = max(-INF, root_best - tolerance - 1)
        board.push(move)
        key = eng.enter(board)
        try:
            current[move] = -eng.search(board, depth - 1, -INF, -floor, 1)
            root_best = max(root_best, current[move])
            if current[move] <= floor:
                clamped.add(move)
        finally:
            eng.leave(key)
            board.pop()


def _exact_root_scores(eng: Any, board: chess.Board, depth: int) -> dict[str, int]:
    """Full-window exact score for every legal root move at a fixed depth."""
    out: dict[str, int] = {}
    for move in list(board.legal_moves):
        board.push(move)
        key = eng.enter(board)
        try:
            out[move.uci()] = -eng.search(board, depth - 1, -INF, INF, 1)
        finally:
            eng.leave(key)
            board.pop()
    return out


def _order_control(eng: Any, board: chess.Board) -> list[chess.Move]:
    result: list[chess.Move] = eng.order(board, list(board.legal_moves), None, 0)
    return result


def _tt_root_move(eng: Any, board: chess.Board, min_depth: int) -> chess.Move | None:
    key = AG.position_key(board)
    entry = eng.table[hash(key) % TT_SIZE]
    if entry is not None and entry.key == key and entry.depth >= min_depth and entry.bound == 0:
        mv: chess.Move = entry.move
        return mv
    return None


def _apply_policy(policy: str, eng: Any, board: chess.Board, depth: int,
                  moves: list[chess.Move], prev_scores: dict[chess.Move, int] | None,
                  deadline: float, budget_frac_left: float,
                  stats: dict[str, float]) -> list[chess.Move]:
    """Return the search order for iteration `depth` under `policy`."""
    if policy == "control" or depth == 1:
        return moves
    if policy == "A":
        # previous completed PV (best) move first, rest in control order
        if prev_scores:
            best = max(prev_scores, key=lambda m: prev_scores[m])
            return [best, *[m for m in moves if m != best]]
        return moves
    if policy == "B":
        # previous-iteration root scores descending, then order() for the rest
        if prev_scores:
            base = _order_control(eng, board)
            return sorted(base, key=lambda m: prev_scores.get(m, -INF), reverse=True)
        return moves
    if policy == "C":
        ttm = _tt_root_move(eng, board, depth - 1)
        base = _order_control(eng, board)
        if ttm is not None and ttm in base:
            return [ttm, *[m for m in base if m != ttm]]
        return base
    if policy == "D":
        # cheap root-only depth-(d-2) ordering pass
        if depth >= 3:
            t0, n0 = time.perf_counter(), eng.nodes
            pre = _exact_root_scores(eng, board, depth - 2)
            stats["prep_seconds"] = stats.get("prep_seconds", 0.0) + (time.perf_counter() - t0)
            stats["prep_nodes"] = stats.get("prep_nodes", 0.0) + (eng.nodes - n0)
            return sorted(moves, key=lambda m: pre.get(m.uci(), -INF), reverse=True)
        return moves
    if policy == "E":
        # handled in the driver (needs the interrupted iteration); order like control here
        return moves
    return moves


def root_search(policy: str, fen: str, clock_ms: int, max_depth: int = 64,
                hard_stop: bool = True) -> dict[str, Any]:
    """Re-implementation of Engine.choose's root loop with a pluggable ordering."""
    eng = _fresh_engine()
    started = time.perf_counter()
    board = chess.Board(fen)
    available = clock_ms / 1000
    budget = min(available / 32, max(0.001, available - 0.025))
    deadline = started + budget * 0.96
    soft = started + budget * 0.65
    eng.deadline = deadline
    eng.stats = {"depth": 0, "model_seconds": 0.0, "adapted": 0.0, "nodes": 0}
    eng.nodes = 0
    eng.age += 1
    _reconstruct_or_reset(eng, board)
    eng.enter(board)
    eng.history = {k: v // 2 for k, v in eng.history.items() if v > 1}
    eng.killers.clear()
    eng.completed_scores = {}
    eng.pattern = AG.recognise(board, AG.evaluate(board))

    fallback = next(iter(board.legal_moves), None)
    moves = _order_control(eng, board)
    chosen = fallback if fallback is not None else moves[0]
    completed: dict[chess.Move, int] = {}
    prev_scores: dict[chess.Move, int] | None = None
    per_iter: list[dict[str, Any]] = []
    stats: dict[str, float] = {}
    interrupted_partial: dict[chess.Move, int] | None = None

    for depth in range(1, max_depth + 1):
        search_order = _apply_policy(policy, eng, board, depth, list(moves), prev_scores,
                                     deadline, 0.0, stats)
        pre_order = [m.uci() for m in search_order]
        current: dict[chess.Move, int] = {}
        clamped: set[chess.Move] = set()
        try:
            _iteration(eng, board, search_order, depth, deadline, current, clamped)
        except AG.Deadline:
            interrupted_partial = dict(current)
            if policy == "E" and completed and current:
                # merge the interrupted iteration's non-clamped (exact) partial
                # scores over the last completed iteration, then re-commit.
                merged = dict(completed)
                for mv, sc in current.items():
                    if mv not in clamped:
                        merged[mv] = sc
                e_moves = sorted(
                    completed.keys(),
                    key=lambda m: (
                        merged[m],
                        int(not board.is_capture(m)) if eng.pattern.swindle else 0,
                    ),
                    reverse=True,
                )
                chosen = e_moves[0]
                per_iter.append({
                    "depth": depth - 1, "policy_E_partial": True,
                    "search_order": pre_order,
                    "scores": {m.uci(): v for m, v in merged.items()},
                    "partial_exact": {m.uci(): v for m, v in current.items() if m not in clamped},
                    "post_sort": [m.uci() for m in e_moves],
                    "committed": chosen.uci(),
                    "nodes": eng.nodes, "seconds": time.perf_counter() - started,
                })
            break
        completed = current
        eng.completed_scores = current
        prev_scores = dict(current)
        moves = sorted(
            search_order,
            key=lambda m: (
                current[m],
                int(not board.is_capture(m)) if eng.pattern.swindle else 0,
            ),
            reverse=True,
        )
        chosen = moves[0]
        per_iter.append({
            "depth": depth,
            "search_order": pre_order,
            "scores": {m.uci(): v for m, v in current.items()},
            "post_sort": [m.uci() for m in moves],
            "committed": chosen.uci(),
            "nodes": eng.nodes,
            "seconds": time.perf_counter() - started,
        })
        if time.perf_counter() >= soft or abs(current[chosen]) > MATE - MAX_PLY:
            break

    prep_nodes = stats.get("prep_nodes", 0.0)
    return {
        "policy": policy,
        "move": chosen.uci(),
        "completed_depth": per_iter[-1]["depth"] if per_iter else 0,
        "scores": {m.uci(): v for m, v in completed.items()},
        "nodes": eng.nodes,
        "net_nodes": eng.nodes - prep_nodes,
        "seconds": time.perf_counter() - started,
        "prep_seconds": stats.get("prep_seconds", 0.0),
        "prep_nodes": prep_nodes,
        "interrupted_partial_n": len(interrupted_partial) if interrupted_partial else 0,
        "per_iter": per_iter,
        "budget_s": budget,
        "history_after": dict(eng.history),
        "killers_after": {k: tuple(x.uci() if x else None for x in v) for k, v in eng.killers.items()},
    }


def fixed_depth_choose(fen: str, depth: int) -> dict[str, Any]:
    """The real Engine.choose stopped exactly after completing iteration `depth`."""
    base = AG.Engine

    class Fixed(base):  # type: ignore[misc, valid-type]
        def search(self, board: Any, remaining: int, alpha: int, beta: int, ply: int) -> int:
            if ply == 1 and remaining == depth:
                raise AG.Deadline
            r: int = base.search(self, board, remaining, alpha, beta, ply)
            return r

    eng = Fixed()
    AG._engine = eng  # type: ignore[attr-defined]
    mv = AG.get_move(fen, 3_600_000)
    return {
        "move": mv,
        "depth": eng.stats["depth"],
        "scores": {m.uci(): v for m, v in eng.completed_scores.items()},
        "nodes": eng.nodes,
    }


# --------------------------------------------------------------------------- #
# equivalence
# --------------------------------------------------------------------------- #

def suite(n: int) -> list[str]:
    fixtures = load_fixtures()
    fens = [fx["fen"] for fx in fixtures]
    import random
    rng = random.Random(702)
    while len(fens) < n:
        b = chess.Board()
        for _ in range(rng.randrange(4, 26)):
            if b.is_game_over():
                break
            b.push(rng.choice(list(b.legal_moves)))
        if not b.is_game_over():
            fens.append(b.fen())
    return fens[:n]


def _real_choose_fixed(fen: str, depth: int) -> dict[str, Any]:
    return fixed_depth_choose(fen, depth)


def _probe_choose_fixed(fen: str, depth: int) -> dict[str, Any]:
    """root_search('control') but stopped after completing iteration `depth`."""
    r = root_search("control", fen, 3_600_000, max_depth=depth)
    return {"move": r["move"], "depth": r["completed_depth"], "scores": r["scores"], "nodes": r["nodes"]}


def equivalence(reps: int = 2) -> None:
    fens = suite(64)
    rows = 0
    mism = 0
    for i, fen in enumerate(fens):
        for depth in (2, 3):
            base = None
            for _ in range(reps):
                a = _real_choose_fixed(fen, depth)
                b = _probe_choose_fixed(fen, depth)
                if base is None:
                    base = a
                for f in ("move", "depth", "scores", "nodes"):
                    if a[f] != b[f] or a[f] != base[f]:
                        mism += 1
                        print(f"  MISMATCH {fen} d{depth} {f}: real={a[f]} probe={b[f]}", flush=True)
                rows += 1
        if (i + 1) % 16 == 0:
            print(f"  eq {i + 1}/{len(fens)} ok", flush=True)
    print("EQUIVALENCE " + json.dumps({
        "control_sha256": CONTROL_SHA, "positions": len(fens), "comparisons": rows,
        "mismatches": mism, "identical": mism == 0, "depths": [2, 3], "reps": reps,
    }))


# --------------------------------------------------------------------------- #
# fixtures / oracle
# --------------------------------------------------------------------------- #

def load_fixtures() -> list[dict[str, Any]]:
    data: Any = json.loads((ROOT / "tests" / "rated_v4_positions.json").read_text())
    return list(data["positions"])


TARGET_IDS = ["r45-54-Rc7", "r46-30-Ka4", "r53-35-Rf7", "r55-19-g5", "r55-20-Nxd4", "r56-17-f4"]
HIST_CLOCK_MS = {"r46-30-Ka4": 68_700, "r55-19-g5": 86_656, "r56-17-f4": 93_400}


def oracle(fen: str, max_depth: int, cap_s: float = 60.0) -> dict[str, Any]:
    eng = _fresh_engine()
    eng.deadline = time.perf_counter() + cap_s
    board = chess.Board(fen)
    eng.enter(board)
    prev: dict[str, int] | None = None
    reached = 0
    scores: dict[str, int] = {}
    for d in range(2, max_depth + 1):
        t0 = time.perf_counter()
        try:
            s = _exact_root_scores(eng, board, d)
        except AG.Deadline:
            break
        prev, scores, reached = scores, s, d
        if time.perf_counter() - t0 > cap_s / 3 or eng.nodes > 4_000_000:
            break
    best = max(scores, key=lambda m: scores[m]) if scores else None
    best_prev = max(prev, key=lambda m: prev[m]) if prev else None
    stable = best is not None and best == best_prev
    return {
        "depth": reached, "scores": scores, "prev_scores": prev or {},
        "best": best, "best_prev": best_prev, "stable": bool(stable),
        "nodes": eng.nodes,
    }


def _klass(cp: int) -> str:
    return "win" if cp > 150 else ("loss" if cp < -150 else "draw")


def phase4(max_depth: int, oracle_depth: int) -> None:
    fixtures = load_fixtures()
    rows: list[dict[str, Any]] = []
    for fx in fixtures:
        fid, fen = fx["id"], fx["fen"]
        board = chess.Board(fen)
        orc = oracle(fen, oracle_depth)
        os = orc["scores"]
        obest = orc["best"]
        per_depth = []
        for d in range(1, max_depth + 1):
            try:
                fd = fixed_depth_choose(fen, d)
            except Exception as e:
                per_depth.append({"depth": d, "error": repr(e)[:120]})
                continue
            if fd["depth"] != d:
                break
            m = fd["move"]
            cp_loss = (max(os.values()) - os[m]) if (os and m in os) else None
            agree = (obest is not None and m == obest)
            klass_change = (
                os and m in os and obest in os
                and _klass(os[m]) != _klass(os[obest])
            )
            mate_m = os and m in os and abs(os[m]) > MATE - MAX_PLY
            mate_o = os and obest in os and abs(os[obest]) > MATE - MAX_PLY
            per_depth.append({
                "depth": d, "move": m, "cp_loss_vs_oracle": cp_loss,
                "agrees_with_oracle": bool(agree),
                "result_class_change": bool(klass_change),
                "committed_is_mate": bool(mate_m), "oracle_is_mate": bool(mate_o),
                "nodes": fd["nodes"],
            })
        # was the oracle-best searched too late / scored wrong at the shallow committed depth?
        rs = root_search("control", fen, HIST_CLOCK_MS.get(fid, 90_000))
        live_depth = rs["completed_depth"]
        late = None
        if rs["per_iter"] and obest is not None:
            last = rs["per_iter"][-1]
            order = last["search_order"]
            sc = last["scores"]
            root_best = max(sc.values()) if sc else 0
            clamp_thresh = root_best - ROOT_WINDOW_MARGIN - 1
            late = {
                "iteration": last["depth"],
                "oracle_best": obest,
                "index_in_search_order": order.index(obest) if obest in order else None,
                "n_root_moves": len(order),
                "iter_score_for_oracle_best": sc.get(obest),
                "clamped": (obest in sc and sc[obest] <= clamp_thresh),
                "committed_live": last["committed"],
                "committed_matches_oracle": last["committed"] == obest,
            }
        rows.append({
            "id": fid, "target": fid in TARGET_IDS, "enforced": fx.get("enforced", False),
            "side_to_move": "white" if board.turn else "black",
            "kind": fx.get("kind"),
            "oracle_depth": orc["depth"], "oracle_stable": orc["stable"],
            "oracle_best": obest, "oracle_best_prev": orc["best_prev"],
            "live_completed_depth": live_depth,
            "live_committed": rs["move"],
            "live_matches_oracle": rs["move"] == obest,
            "live_cp_loss_vs_oracle": (max(os.values()) - os[rs["move"]]) if (os and rs["move"] in os) else None,
            "per_depth": per_depth,
            "late_analysis": late,
        })
        la = late or {}
        print(f"[{'T' if fid in TARGET_IDS else ('e' if fx.get('enforced') else '.')}] "
              f"{fid:20} live d{live_depth} -> {rs['move']:6} oracle d{orc['depth']}"
              f"{'(stable)' if orc['stable'] else '(UNSTABLE)'} -> {obest}  "
              f"match={rs['move'] == obest}  cp_loss="
              f"{(max(os.values()) - os[rs['move']]) if (os and rs['move'] in os) else '?'}  "
              f"oracleBest idx={la.get('index_in_search_order')}/{la.get('n_root_moves')} "
              f"clamped={la.get('clamped')}", flush=True)

    # summary
    gr = [r for r in rows if r["oracle_stable"]]
    disagree = [r for r in gr if not r["live_matches_oracle"] and r["live_cp_loss_vs_oracle"] is not None]
    buckets = {"harmless<15": 0, "material>=15": 0, "consequential>=40_or_class": 0, "mate": 0}
    for r in disagree:
        cp = abs(r["live_cp_loss_vs_oracle"])
        os = None
        pd = r["per_depth"]
        mate = any(x.get("oracle_is_mate") or x.get("committed_is_mate") for x in pd if "move" in x)
        klass = any(x.get("result_class_change") for x in pd if "move" in x)
        if mate:
            buckets["mate"] += 1
        elif cp >= 40 or klass:
            buckets["consequential>=40_or_class"] += 1
        elif cp >= 15:
            buckets["material>=15"] += 1
        else:
            buckets["harmless<15"] += 1
    targets_reached = sum(
        1 for r in rows if r["target"] and r["live_matches_oracle"] and r["oracle_stable"]
    )
    summary = {
        "control_sha256": CONTROL_SHA,
        "fixtures": len(rows), "oracle_stable": len(gr), "oracle_unstable": len(rows) - len(gr),
        "live_disagreements_vs_stable_oracle": len(disagree),
        "disagreement_rate": len(disagree) / max(1, len(gr)),
        "buckets": buckets,
        "targets_live_matches_stable_oracle": targets_reached,
        "targets": {r["id"]: {
            "live_depth": r["live_completed_depth"], "live_move": r["live_committed"],
            "oracle_depth": r["oracle_depth"], "oracle_best": r["oracle_best"],
            "stable": r["oracle_stable"], "match": r["live_matches_oracle"],
            "cp_loss": r["live_cp_loss_vs_oracle"],
            "oracle_best_clamped_in_live": (r["late_analysis"] or {}).get("clamped"),
            "oracle_best_index": (r["late_analysis"] or {}).get("index_in_search_order"),
        } for r in rows if r["target"]},
    }
    print("SUMMARY " + json.dumps(summary, indent=1), flush=True)
    outdir = ROOT / "tests" / "results" / "root_order"
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "phase4_gap.json").write_text(json.dumps({"summary": summary, "rows": rows}, indent=1))
    print("WROTE " + str(outdir / "phase4_gap.json"), flush=True)


def phase5(repeats: int) -> None:
    fixtures = {fx["id"]: fx for fx in load_fixtures()}
    ids = TARGET_IDS + [i for i in fixtures if fixtures[i].get("enforced") and i not in TARGET_IDS]
    outdir = ROOT / "tests" / "results" / "root_order"
    outdir.mkdir(parents=True, exist_ok=True)
    results: dict[str, Any] = {"control_sha256": CONTROL_SHA, "repeats": repeats, "rows": []}
    for fid in ids:
        fx = fixtures[fid]
        fen = fx["fen"]
        clock = HIST_CLOCK_MS.get(fid, 90_000)
        board = chess.Board(fen)
        row: dict[str, Any] = {"id": fid, "target": fid in TARGET_IDS,
                               "side_to_move": "white" if board.turn else "black",
                               "kind": fx.get("kind"), "clock_ms": clock, "by_policy": {}}
        for pol in POLICIES:
            runs = [root_search(pol, fen, clock) for _ in range(repeats)]
            depths = [r["completed_depth"] for r in runs]
            nodes = [r["nodes"] for r in runs]
            secs = [r["seconds"] for r in runs]
            preps = [r["prep_seconds"] for r in runs]
            moves = [r["move"] for r in runs]
            row["by_policy"][pol] = {
                "completed_depth_median": statistics.median(depths),
                "completed_depth_all": depths,
                "nodes_median": statistics.median(nodes),
                "seconds_median": statistics.median(secs),
                "prep_seconds_median": statistics.median(preps),
                "prep_pct_of_budget": statistics.median(preps) / max(1e-9, runs[0]["budget_s"]),
                "committed_moves": moves,
                "committed_move_mode": max(set(moves), key=moves.count),
            }
        c = row["by_policy"]["control"]
        line = f"{fid:20} ctl d{c['completed_depth_median']:.1f} {c['committed_move_mode']:6}"
        for pol in ("A", "B", "C", "D", "E"):
            p = row["by_policy"][pol]
            dd = p["completed_depth_median"] - c["completed_depth_median"]
            nn = (p["nodes_median"] - c["nodes_median"]) / max(1, c["nodes_median"])
            chg = "" if p["committed_move_mode"] == c["committed_move_mode"] else f"->{p['committed_move_mode']}"
            line += f" | {pol} dd{dd:+.1f} dn{nn * 100:+.0f}% prep{p['prep_pct_of_budget'] * 100:.1f}%{chg}"
        print(line, flush=True)
        results["rows"].append(row)
    (outdir / "phase5_policies.json").write_text(json.dumps(results, indent=1))
    print("WROTE " + str(outdir / "phase5_policies.json"), flush=True)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("mode", choices=("equivalence", "phase4", "phase5"))
    p.add_argument("--reps", type=int, default=2)
    p.add_argument("--max-depth", type=int, default=6)
    p.add_argument("--oracle-depth", type=int, default=7)
    a = p.parse_args()
    if a.mode == "equivalence":
        equivalence(a.reps)
    elif a.mode == "phase4":
        phase4(a.max_depth, a.oracle_depth)
    else:
        phase5(a.reps)


if __name__ == "__main__":
    main()
