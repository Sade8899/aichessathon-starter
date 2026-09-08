"""Platform-reproduction & failure-mechanism attribution harness. DIAGNOSIS ONLY.

Nothing here modifies ``agent.py``. The control source is read, its SHA-256
asserted, and loaded under a throwaway module name. The ``Engine.choose`` root loop
is re-implemented (``root_search``) so the search can be capped by wall clock,
completed depth or node count while every node primitive stays the real control
code. ``equivalence`` proves the re-implementation reproduces ``Engine.choose``
exactly (committed move, completed depth, every root score, exact node count).

Conditions per fixture:
  A  local wall-clock budget on the i7 (5 sequential timed reps + warm-up)
  B  platform-calibrated cap: fixed completed depth (+ node cap where historical)
  C  fixed-depth ladder 1..deepest feasible stable depth (deterministic)
  D  generous diagnostic budget -> the stable reference move

Native Windows / CPython.
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
OUT = ROOT / "tests" / "results" / "platform_repro"


def control_source() -> str:
    raw = CONTROL.read_bytes()
    got = hashlib.sha256(raw).hexdigest()
    assert got == CONTROL_SHA, f"control SHA mismatch: {got}"
    return raw.decode().replace("\r\n", "\n")


def _load(name: str, src: str) -> types.ModuleType:
    m = types.ModuleType(name)
    m.__dict__["__file__"] = str(CONTROL)
    sys.modules[name] = m
    exec(compile(src, name, "exec"), m.__dict__)
    return m


AG = _load("pr_agent", control_source())
INF, MATE, MAX_PLY = AG.INF, AG.MATE, AG.MAX_PLY
ROOT_WINDOW_MARGIN, TT_SIZE = AG.ROOT_WINDOW_MARGIN, AG.TT_SIZE


# --------------------------------------------------------------------------- #
# re-implemented root loop with pluggable caps
# --------------------------------------------------------------------------- #

def _new_engine(node_cap: int | None = None) -> Any:
    base = AG.Engine
    if node_cap is None:
        eng = base()
    else:
        class Capped(base):  # type: ignore[misc, valid-type]
            def tick(self) -> None:
                self.nodes += 1
                if self.nodes >= node_cap:
                    raise AG.Deadline
                if self.nodes % 16 == 0 and time.perf_counter() >= self.deadline:
                    raise AG.Deadline
        eng = Capped()
    return eng


def _prep(eng: Any, board: chess.Board, deadline: float) -> None:
    eng.deadline = deadline
    eng.stats = {"depth": 0, "model_seconds": 0.0, "adapted": 0.0, "nodes": 0}
    eng.nodes = 0
    eng.age += 1
    obs = eng.reconstruct(board)
    if obs is None:
        eng.seen.clear()
        eng.context = 0
        eng.duplicates = 0
        eng.model.reset()
    eng.enter(board)
    eng.history = {k: v // 2 for k, v in eng.history.items() if v > 1}
    eng.killers.clear()
    eng.completed_scores = {}
    eng.pattern = AG.recognise(board, AG.evaluate(board))


def _iteration(eng: Any, board: chess.Board, order: list[chess.Move], depth: int,
               deadline: float, current: dict[chess.Move, int]) -> None:
    eng.iteration_evidence = {}
    leaders = order[:3]
    root_best = -INF
    for move in order:
        if time.perf_counter() >= deadline:
            raise AG.Deadline
        eng.root_move = move
        eng.collect = AG.PASSIVE and move in leaders
        floor = max(-INF, root_best - ROOT_WINDOW_MARGIN - 1)
        board.push(move)
        key = eng.enter(board)
        try:
            current[move] = -eng.search(board, depth - 1, -INF, -floor, 1)
            root_best = max(root_best, current[move])
        finally:
            eng.leave(key)
            board.pop()


def root_search(fen: str, clock_ms: float | None, *, node_cap: int | None = None,
                depth_cap: int | None = None, max_depth: int = 64) -> dict[str, Any]:
    """Engine.choose's root loop; stops at wall clock, node cap or depth cap."""
    eng = _new_engine(node_cap)
    started = time.perf_counter()
    board = chess.Board(fen)
    if clock_ms is None:
        budget = 1e9
    else:
        available = clock_ms / 1000
        budget = min(available / 32, max(0.001, available - 0.025))
    deadline = started + budget * 0.96
    soft = started + budget * 0.65
    _prep(eng, board, deadline)

    moves = list(eng.order(board, list(board.legal_moves), None, 0))
    fallback = next(iter(board.legal_moves), None)
    chosen = fallback if fallback is not None else moves[0]
    completed: dict[chess.Move, int] = {}
    per_iter: list[dict[str, Any]] = []
    interrupted: dict[str, Any] | None = None
    hi = max_depth if depth_cap is None else depth_cap

    for depth in range(1, hi + 1):
        order = list(moves)
        current: dict[chess.Move, int] = {}
        try:
            _iteration(eng, board, order, depth, deadline, current)
        except AG.Deadline:
            done = len(current)
            interrupted = {
                "iteration": depth,
                "root_moves_total": len(order),
                "root_moves_done": done,
                "root_move_at_deadline": order[done].uci() if done < len(order) else None,
                "partial_scores": {m.uci(): v for m, v in current.items()},
                "nodes": eng.nodes,
            }
            break
        completed = current
        eng.completed_scores = current
        moves = sorted(order, key=lambda m: (
            current[m], int(not board.is_capture(m)) if eng.pattern.swindle else 0),
            reverse=True)
        chosen = moves[0]
        best_before_sort = order[0]  # noqa: F841
        per_iter.append({
            "depth": depth,
            "committed": chosen.uci(),
            "committed_index_in_search_order": order.index(chosen),
            "scores": {m.uci(): v for m, v in current.items()},
            "post_sort": [m.uci() for m in moves],
            "nodes": eng.nodes,
            "seconds": time.perf_counter() - started,
        })
        if clock_ms is not None and time.perf_counter() >= soft:
            break
        if abs(current[chosen]) > MATE - MAX_PLY:
            break

    cd = per_iter[-1]["depth"] if per_iter else 0
    return {
        "move": chosen.uci(),
        "completed_depth": cd,
        "score": completed.get(chosen) if completed else None,
        "scores": {m.uci(): v for m, v in completed.items()},
        "nodes": eng.nodes,
        "seconds": time.perf_counter() - started,
        "budget_s": None if clock_ms is None else budget,
        "next_iter_started_and_interrupted": interrupted is not None,
        "interrupted": interrupted,
        "committed_index_in_search_order": per_iter[-1]["committed_index_in_search_order"] if per_iter else None,
        "per_iter": per_iter,
    }


def _exact_root_scores(eng: Any, board: chess.Board, depth: int) -> dict[str, int]:
    out: dict[str, int] = {}
    for mv in list(board.legal_moves):
        board.push(mv)
        key = eng.enter(board)
        try:
            out[mv.uci()] = -eng.search(board, depth - 1, -INF, INF, 1)
        finally:
            eng.leave(key)
            board.pop()
    return out


def fixed_depth(fen: str, depth: int) -> dict[str, Any]:
    """Deterministic: full-window exact score of every root move at fixed `depth`,
    plus the principal variation of the best move (reconstructed from the TT)."""
    eng = _new_engine()
    eng.deadline = time.perf_counter() + 3600
    eng.stats = {"model_seconds": 0.0}
    board = chess.Board(fen)
    eng.enter(board)
    n0 = eng.nodes
    scores = _exact_root_scores(eng, board, depth)
    best = max(scores, key=lambda m: scores[m])
    # PV via TT
    pv: list[str] = [best]
    b2 = chess.Board(fen)
    b2.push(chess.Move.from_uci(best))
    seen_keys = set()
    for _ in range(depth + 6):
        k = AG.position_key(b2)
        if k in seen_keys:
            break
        seen_keys.add(k)
        e = eng.table[hash(k) % TT_SIZE]
        if e is None or e.key != k or e.move is None or e.move == chess.Move.null():
            break
        if e.move not in b2.legal_moves:
            break
        pv.append(e.move.uci())
        b2.push(e.move)
        if b2.is_game_over():
            break
    return {
        "depth": depth, "move": best, "score": scores[best], "scores": scores,
        "nodes": eng.nodes - n0, "pv": pv,
    }


def branch_pv(fen: str, first_move: str, depth: int) -> dict[str, Any]:
    """Score + PV of the sub-tree after a specific first move, at fixed depth."""
    eng = _new_engine()
    eng.deadline = time.perf_counter() + 3600
    eng.stats = {"model_seconds": 0.0}
    board = chess.Board(fen)
    eng.enter(board)
    mv = chess.Move.from_uci(first_move)
    board.push(mv)
    key = eng.enter(board)
    n0 = eng.nodes
    sc = -eng.search(board, depth - 1, -INF, INF, 1)
    eng.leave(key)
    board.pop()
    # reconstruct PV from the child position
    b2 = chess.Board(fen)
    b2.push(mv)
    pv = [first_move]
    seen_keys = set()
    for _ in range(depth + 6):
        k = AG.position_key(b2)
        if k in seen_keys:
            break
        seen_keys.add(k)
        e = eng.table[hash(k) % TT_SIZE]
        if e is None or e.key != k or e.move is None or e.move == chess.Move.null() or e.move not in b2.legal_moves:
            break
        pv.append(e.move.uci())
        b2.push(e.move)
        if b2.is_game_over():
            break
    return {"first_move": first_move, "depth": depth, "score": sc, "nodes": eng.nodes - n0, "pv": pv}


def reference(fen: str, max_d: int = 8, cap_s: float = 60.0, cap_n: int = 4_000_000) -> dict[str, Any]:
    eng = _new_engine()
    eng.deadline = time.perf_counter() + cap_s
    board = chess.Board(fen)
    eng.enter(board)
    prev: dict[str, int] = {}
    scores: dict[str, int] = {}
    reached = 0
    ladder = []
    for d in range(2, max_d + 1):
        t0, n0 = time.perf_counter(), eng.nodes
        try:
            s = _exact_root_scores(eng, board, d)
        except AG.Deadline:
            break
        prev, scores, reached = scores, s, d
        ladder.append({"depth": d, "best": max(s, key=lambda m: s[m]), "nodes": eng.nodes - n0,
                       "seconds": round(time.perf_counter() - t0, 2)})
        if time.perf_counter() - t0 > cap_s / 3 or eng.nodes > cap_n:
            break
    best = max(scores, key=lambda m: scores[m]) if scores else None
    bprev = max(prev, key=lambda m: prev[m]) if prev else None
    stable = best is not None and best == bprev
    is_mate = best is not None and abs(scores[best]) > MATE - MAX_PLY
    mate_prev = bprev is not None and prev and abs(prev.get(bprev, 0)) > MATE - MAX_PLY
    return {
        "depth": reached, "best": best, "best_prev": bprev, "stable": bool(stable),
        "mate": bool(is_mate and mate_prev), "scores": scores, "ladder": ladder,
    }


# --------------------------------------------------------------------------- #
# equivalence
# --------------------------------------------------------------------------- #

def _real_choose_fixed(fen: str, depth: int) -> dict[str, Any]:
    base = AG.Engine

    class F(base):  # type: ignore[misc, valid-type]
        def search(self, b: Any, rem: int, a: int, be: int, ply: int) -> int:
            if ply == 1 and rem == depth:
                raise AG.Deadline
            r: int = base.search(self, b, rem, a, be, ply)
            return r
    eng = F()
    AG._engine = eng  # type: ignore[attr-defined]
    mv = AG.get_move(fen, 3_600_000)
    return {"move": mv, "depth": eng.stats["depth"],
            "scores": {m.uci(): v for m, v in eng.completed_scores.items()}, "nodes": eng.nodes}


def equivalence(reps: int = 2) -> None:
    fixtures = load_fixtures()
    fens = [fx["fen"] for fx in fixtures]
    import random
    rng = random.Random(702)
    while len(fens) < 64:
        b = chess.Board()
        for _ in range(rng.randrange(4, 26)):
            if b.is_game_over():
                break
            b.push(rng.choice(list(b.legal_moves)))
        if not b.is_game_over():
            fens.append(b.fen())
    mism = 0
    n = 0
    for i, fen in enumerate(fens[:64]):
        for d in (2, 3):
            a = _real_choose_fixed(fen, d)
            for _ in range(reps):
                pb = root_search(fen, None, depth_cap=d)
                got = {"move": pb["move"], "depth": pb["completed_depth"], "scores": pb["scores"], "nodes": pb["nodes"]}
                for f in ("move", "depth", "scores", "nodes"):
                    if a[f] != got[f]:
                        mism += 1
                        print(f"  MISMATCH {fen} d{d} {f}: real={a[f]} probe={got[f]}", flush=True)
                n += 1
        if (i + 1) % 16 == 0:
            print(f"  eq {i + 1}/64 ok", flush=True)
    print("EQUIVALENCE " + json.dumps({"control_sha256": CONTROL_SHA, "comparisons": n,
                                       "mismatches": mism, "identical": mism == 0}))


# --------------------------------------------------------------------------- #
# fixtures / calibration
# --------------------------------------------------------------------------- #

def load_fixtures() -> list[dict[str, Any]]:
    d: Any = json.loads((ROOT / "tests" / "rated_v4_positions.json").read_text())
    return list(d["positions"])


TARGET_IDS = ["r45-54-Rc7", "r46-30-Ka4", "r53-35-Rf7", "r55-19-g5", "r55-20-Nxd4", "r56-17-f4"]
HIST_NODES = {"r55-19-g5": 54_720}  # only fixture with a recorded historical node count


def _forcing_first(fen: str, mv_uci: str) -> str:
    b = chess.Board(fen)
    m = chess.Move.from_uci(mv_uci)
    if m not in b.legal_moves:
        return "illegal"
    if b.is_capture(m):
        return "capture"
    if m.promotion:
        return "promotion"
    if b.gives_check(m):
        return "check"
    return "quiet"


def _klass(cp: int) -> str:
    return "win" if cp > 150 else ("loss" if cp < -150 else "draw")


def measure(reps: int) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    fixtures = load_fixtures()
    rows: list[dict[str, Any]] = []

    for fx in fixtures:
        fid, fen = fx["id"], fx["fen"]
        board = chess.Board(fen)
        rg = fx.get("rated_game") or {}
        rated_uci = fx.get("rated_move_uci")
        acc = set(fx.get("acceptable_uci") or [])
        unacc = fx.get("unacceptable_uci")
        clk = rg.get("clock_before_s")
        plat_d = rg.get("completed_depth_in_replay")
        reproduced_flag = rg.get("replay_reproduced_rated_move")
        mcd = fx.get("minimum_correcting_depth")

        # D: reference
        ref = reference(fen)
        ref_best = ref["best"]
        ref_scores: dict[str, int] = ref["scores"]

        def ref_cp(m: str | None, _s: dict[str, int] = ref_scores) -> int | None:
            return (max(_s.values()) - _s[m]) if (m is not None and m in _s) else None

        # C: fixed-depth ladder 1..min(mcd+1, ref depth, 6)
        top = min(6, max(3, (mcd or 4) + 1), ref["depth"] or 6)
        ladder = []
        for d in range(1, top + 1):
            fd = fixed_depth(fen, d)
            ladder.append({"depth": d, "move": fd["move"], "score": fd["score"],
                           "cp_loss_vs_ref": ref_cp(fd["move"]), "nodes": fd["nodes"], "pv": fd["pv"]})

        # B: platform-calibrated cap
        b_runs: dict[str, Any] = {}
        if isinstance(plat_d, int):
            cap_d = plat_d if reproduced_flag else max(1, plat_d - 1)
            fd = fixed_depth(fen, cap_d)
            b_runs["depth_cap"] = {"cap_depth": cap_d, "approx": not reproduced_flag,
                                   "move": fd["move"], "score": fd["score"],
                                   "cp_loss_vs_ref": ref_cp(fd["move"]), "nodes": fd["nodes"]}
        if fid in HIST_NODES:
            rs = root_search(fen, clk * 1000 if clk else 90_000, node_cap=HIST_NODES[fid])
            b_runs["node_cap"] = {"cap_nodes": HIST_NODES[fid], "move": rs["move"],
                                  "completed_depth": rs["completed_depth"], "score": rs["score"],
                                  "cp_loss_vs_ref": ref_cp(rs["move"]), "nodes": rs["nodes"]}

        # A: local wall-clock, 5 timed reps + warm-up
        a_clk_ms = (clk if clk else 90.0) * 1000
        root_search(fen, a_clk_ms)  # warm-up
        a_reps = []
        for _ in range(reps):
            rs = root_search(fen, a_clk_ms)
            a_reps.append({
                "move": rs["move"], "completed_depth": rs["completed_depth"],
                "score": rs["score"], "nodes": rs["nodes"], "seconds": round(rs["seconds"], 3),
                "cp_loss_vs_ref": ref_cp(rs["move"]),
                "next_iter_interrupted": rs["next_iter_started_and_interrupted"],
                "root_move_at_deadline": (rs["interrupted"] or {}).get("root_move_at_deadline"),
                "committed_index": rs["committed_index_in_search_order"],
            })
        a_depths = [r["completed_depth"] for r in a_reps]
        a_moves = [r["move"] for r in a_reps]
        a_move_mode = max(set(a_moves), key=a_moves.count)
        a_secs = [r["seconds"] for r in a_reps]

        # classification
        b_move = (b_runs.get("depth_cap") or {}).get("move") or (b_runs.get("node_cap") or {}).get("move")
        reproduces_b = b_move is not None and (b_move == rated_uci or (unacc and b_move == unacc)
                                               or (rated_uci and b_move == rated_uci))
        a_corrects_ratio = sum(1 for m in a_moves if m in acc or m == ref_best) / max(1, len(a_moves))
        a_bad_ratio = sum(1 for m in a_moves if m == rated_uci or (unacc and m == unacc)) / max(1, len(a_moves))

        b_cp = (b_runs.get("depth_cap") or {}).get("cp_loss_vs_ref")
        consequential = (b_cp is not None and b_cp >= 40)

        # find correcting depth in the reference-aligned ladder (first depth where
        # ladder move is in acc / == ref_best and stays so through the top)
        corr_d = None
        for k, row in enumerate(ladder):
            good_here = row["move"] in acc or row["move"] == ref_best
            stays_good = all(ladder[j]["move"] in acc or ladder[j]["move"] == ref_best
                             for j in range(k, len(ladder)))
            if good_here and stays_good:
                corr_d = row["depth"]
                break

        primary = "UNRESOLVED"
        secondary: list[str] = []
        is_failure = bool(fx.get("enforced")) and fid not in (
            "r47-63-e2", "r48-26-Qxf6", "r49-53-h3", "r50-35-d3", "r52-40-c7", "r52-55-Re4")
        if not ref["stable"]:
            primary = "UNRESOLVED"
        elif not is_failure:
            primary = "NEGATIVE-CONTROL-OK" if (a_move_mode in acc or a_move_mode == ref_best) else "NEGATIVE-CONTROL-DRIFT"
        elif reproduces_b and a_corrects_ratio >= 0.8 and not (a_bad_ratio >= 0.6):
            primary = "PLATFORM-SPEED ARTIFACT"
        elif reproduces_b or a_bad_ratio >= 0.6:
            primary = "REPRODUCES LOCALLY"
        # refine failure mechanism
        if primary in ("PLATFORM-SPEED ARTIFACT", "REPRODUCES LOCALLY") and isinstance(plat_d, int) and corr_d:
            gap = corr_d - plat_d
            _refmv = (sorted(acc)[0] if acc else None) or ref_best or rated_uci or ""
            first = _forcing_first(fen, _refmv)
            if gap == 1 and first in ("capture", "check", "promotion"):
                secondary.append("QUIESCENCE-BOUNDARY")
            if gap >= 1:
                secondary.append(f"HORIZON+{gap}")
            cls = (fx.get("classification") or "").lower()
            if "promotion" in cls or "pawn" in cls or "endgame" in cls or "conversion" in cls:
                secondary.append("ENDGAME/PASSED-PAWN")
            if "repetition" in cls:
                secondary.append("REPETITION")

        row = {
            "id": fid, "target": fid in TARGET_IDS, "enforced": fx.get("enforced", False),
            "is_failure": is_failure, "side_to_move": "white" if board.turn else "black",
            "kind": fx.get("kind"), "repo_classification": fx.get("classification"),
            "rated_move": rated_uci, "acceptable": sorted(acc), "unacceptable": unacc,
            "clock_before_s": clk, "platform_completed_depth": plat_d,
            "replay_reproduced_rated_move": reproduced_flag,
            "minimum_correcting_depth": mcd,
            "reference": {"depth": ref["depth"], "best": ref_best, "best_prev": ref["best_prev"],
                          "stable": ref["stable"], "mate": ref["mate"], "ladder": ref["ladder"]},
            "condition_C_fixed_ladder": ladder,
            "condition_B_platform": b_runs,
            "condition_B_reproduces_bad_move": reproduces_b,
            "condition_B_cp_loss": b_cp,
            "condition_B_consequential": consequential,
            "condition_A_wallclock": {
                "clock_ms": a_clk_ms, "reps": a_reps,
                "move_mode": a_move_mode, "moves": a_moves,
                "depth_median": statistics.median(a_depths), "depth_min": min(a_depths), "depth_max": max(a_depths),
                "seconds_median": statistics.median(a_secs), "seconds_min": min(a_secs), "seconds_max": max(a_secs),
                "seconds_iqr": (statistics.quantiles(a_secs, n=4)[2] - statistics.quantiles(a_secs, n=4)[0]) if len(a_secs) >= 4 else None,
                "corrects_ratio": a_corrects_ratio, "bad_ratio": a_bad_ratio,
            },
            "correcting_depth_in_ladder": corr_d,
            "primary_category": primary, "secondary_categories": secondary,
        }
        rows.append(row)
        print(f"[{'T' if fid in TARGET_IDS else ('F' if is_failure else '.')}] {fid:20} "
              f"platD={plat_d} corrD={corr_d} ref d{ref['depth']}{'/stable' if ref['stable'] else '/UNSTABLE'}->{ref_best}  "
              f"B={b_move}({b_cp}) A(md{statistics.median(a_depths):.0f})={a_move_mode} "
              f"corr={a_corrects_ratio:.1f} bad={a_bad_ratio:.1f}  => {primary} {secondary}", flush=True)

    # aggregate
    failures = [r for r in rows if r["is_failure"] and r["reference"]["stable"]]
    reproduced = [r for r in failures if r["primary_category"] == "REPRODUCES LOCALLY"]
    artifacts = [r for r in failures if r["primary_category"] == "PLATFORM-SPEED ARTIFACT"]
    reproduced_conseq = [r for r in reproduced if r["condition_B_consequential"]]
    from collections import Counter
    prim = Counter(r["primary_category"] for r in failures)
    sec = Counter(s.split("+")[0] for r in reproduced for s in r["secondary_categories"])
    colour = Counter(r["side_to_move"] for r in reproduced)
    negctl = [r for r in rows if not r["is_failure"] and r["enforced"]]
    negctl_ok = sum(1 for r in negctl if r["primary_category"] == "NEGATIVE-CONTROL-OK")
    summary = {
        "control_sha256": CONTROL_SHA, "corpus_rounds": "30, 44-56 (INCOMPLETE: 57-66 missing)",
        "fixtures": len(rows), "stable_failures": len(failures),
        "reproduces_locally": len(reproduced), "platform_speed_artifact": len(artifacts),
        "reproduced_consequential": len(reproduced_conseq),
        "reproduced_consequential_ids": [r["id"] for r in reproduced_conseq],
        "primary_category_counts": dict(prim),
        "reproduced_secondary_counts": dict(sec),
        "reproduced_colour_split": dict(colour),
        "negative_controls": len(negctl), "negative_controls_ok": negctl_ok,
        "targets": {r["id"]: {"platform_depth": r["platform_completed_depth"],
                              "correcting_depth": r["correcting_depth_in_ladder"],
                              "B_move": (r["condition_B_platform"].get("depth_cap") or {}).get("move"),
                              "B_cp_loss": r["condition_B_cp_loss"],
                              "A_move_mode": r["condition_A_wallclock"]["move_mode"],
                              "A_depth_median": r["condition_A_wallclock"]["depth_median"],
                              "primary": r["primary_category"], "secondary": r["secondary_categories"]}
                    for r in rows if r["target"]},
    }
    g: dict[str, Any] = {}
    summary["decision_gate"] = g
    g["g1_ge3_reproduce_under_platform"] = len(reproduced) >= 3
    horizon_like = sum(1 for r in reproduced_conseq
                       if any(s.startswith("HORIZON") for s in r["secondary_categories"]))
    g["g2_ge60pct_share_mechanism"] = (horizon_like / max(1, len(reproduced_conseq))) >= 0.60
    g["g2_share_value"] = horizon_like / max(1, len(reproduced_conseq))
    g["g3_mechanism_affects_completed_iteration"] = "SEE_ANALYSIS"  # decided in prose
    g["g4_negative_controls_present"] = len(negctl) >= 3
    print("SUMMARY " + json.dumps(summary, indent=1), flush=True)
    (OUT / "measure.json").write_text(json.dumps({"summary": summary, "rows": rows}, indent=1))
    print("WROTE " + str(OUT / "measure.json"), flush=True)


def r5520_trace() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    fen = "r4r1k/p2bn3/qpnBp2p/1R1p2p1/P1pP3P/2P2N2/2P1BPP1/R1Q3K1 b - - 0 20"
    board = chess.Board(fen)
    out: dict[str, Any] = {"fen": fen, "depths": []}
    # candidate first moves
    cands = ["c6d4", "f8g8"]
    for d in range(2, 8):
        row: dict[str, Any] = {"depth": d}
        fd = fixed_depth(fen, d)
        row["overall_best"] = fd["move"]
        row["overall_best_score"] = fd["score"]
        row["overall_pv"] = fd["pv"]
        row["nodes_full_root"] = fd["nodes"]
        for c in cands:
            bp = branch_pv(fen, c, d)
            row[c] = {"score": bp["score"], "pv": bp["pv"], "nodes": bp["nodes"]}
        row["Nxd4_minus_Rg8_cp"] = row["c6d4"]["score"] - row["f8g8"]["score"]
        out["depths"].append(row)
        print(f"d{d}: best={fd['move']}  Nxd4={row['c6d4']['score']} (pv {' '.join(row['c6d4']['pv'][:8])})  "
              f"Rg8={row['f8g8']['score']} (pv {' '.join(row['f8g8']['pv'][:8])})  diff={row['Nxd4_minus_Rg8_cp']}", flush=True)
    # static (leaf / quiescence stand-pat) eval of each branch, 1 ply in
    eng = _new_engine()
    eng.deadline = time.perf_counter() + 60
    eng.stats = {"model_seconds": 0.0}
    eng.enter(board)
    statics: dict[str, Any] = {}
    for c in cands:
        bb = chess.Board(fen)
        bb.push(chess.Move.from_uci(c))
        stat: dict[str, int] = {"static_after_move": -int(AG.evaluate(bb))}
        if c == "c6d4":
            bb.push_uci("f3d4")  # Nxd4 recapture
            stat["static_after_recapture"] = int(AG.evaluate(bb))
            key = eng.enter(bb)
            stat["quiescence_after_recapture"] = int(eng.quiesce(bb, -INF, INF, 0))
            eng.leave(key)
        statics[c] = stat
    out["branch_statics"] = statics
    # first move of the refuting line for Nxd4 (what White does after ...Nxd4 Nxd4)
    dref = fixed_depth(fen, 6)
    out["depth6_overall_pv"] = dref["pv"]
    bp6 = branch_pv(fen, "c6d4", 6)
    out["depth6_Nxd4_pv"] = bp6["pv"]
    if len(bp6["pv"]) >= 3:
        third = bp6["pv"][2]
        b = chess.Board(fen)
        b.push_uci(bp6["pv"][0])
        b.push_uci(bp6["pv"][1])
        out["Nxd4_line_white_3rd_move"] = third
        out["Nxd4_line_white_3rd_move_type"] = _forcing_first(b.fen(), third)
    (OUT / "r5520_trace.json").write_text(json.dumps(out, indent=1))
    print("WROTE " + str(OUT / "r5520_trace.json"), flush=True)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("mode", choices=("equivalence", "measure", "r5520"))
    p.add_argument("--reps", type=int, default=5)
    a = p.parse_args()
    if a.mode == "equivalence":
        equivalence()
    elif a.mode == "measure":
        measure(a.reps)
    else:
        r5520_trace()


if __name__ == "__main__":
    main()
