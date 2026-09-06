"""Diagnose the rated tactical loss with the game's own persistent state.

Loads the current LF agent as a development copy whose per-move budget can be
overridden, replays the rated game into one instance so the transposition table,
history, repetition counters and opponent model match the rated position, and then
re-analyses the blunder at fixed depths and at several thinking limits.

Nothing here edits agent.py. The only source change is an injected budget override
used to ask what a longer think would have found.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
import types
from pathlib import Path
from typing import Any

import chess
import qgen_checks
from selection import load

MANIFEST = Path("tests/qcap_validation.json")
FIXTURE = Path("tests/tournament/fixtures/rated.json")
SUBMITTED = Path("tests/tournament/submitted.json")
REGRESSION = Path("tests/rated_regression.json")
BUDGET_LINE = "        budget = min(3.0, available / 32, max(0.001, available - 0.025))"
BUDGET_PATCH = (
    "        budget = _THINK[0] or min(3.0, available / 32, max(0.001, available - 0.025))"
)


def instrumented(config: str) -> tuple[types.ModuleType, str]:
    """One engine, with a development override for the per-move budget only.

    "submitted" is the engine actually deployed on the platform and is the primary
    subject; "candidate" is the unsubmitted development agent, used for comparison.
    """
    if config == "submitted":
        manifest = json.loads(SUBMITTED.read_text())
        raw = Path(manifest["frozen_path"]).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == manifest["agent_sha256"]
        source = raw.decode()
    else:
        qgen_checks.MANIFEST = MANIFEST
        source = qgen_checks.sources()[config]
    digest = hashlib.sha256(source.encode()).hexdigest()
    assert source.count(BUDGET_LINE) == 1, "budget expression not found"
    patched = source.replace(BUDGET_LINE, BUDGET_PATCH) + "\n_THINK = [0.0]\n"
    module = load("rated_" + config, patched)
    return module, digest


def replay(module: types.ModuleType, fixture: dict[str, Any]) -> dict[str, Any]:
    """Play the rated White moves into one fresh instance at the rated clocks."""
    vars(module)["_engine"] = module.Engine()
    if hasattr(module, "_eval_table"):
        module._eval_table[:] = [None] * len(module._eval_table)
    module._THINK[0] = 0.0
    engine = module._engine
    records = []
    for index, call in enumerate(fixture["calls"][:-1]):
        observed = engine.reconstruct(chess.Board(call["fen"]))
        move = module.get_move(call["fen"], call["clock_ms"])
        records.append(
            {
                "call": index,
                "fen": call["fen"],
                "played": move,
                "rated": call["expected"],
                "matched_rated": move == call["expected"],
                "observed_opponent_move": observed,
                "depth": engine.stats["depth"],
                "nodes": engine.stats["nodes"],
                "seconds": engine.stats["seconds"],
            }
        )
        if move != call["expected"]:
            # State continuity needs the rated move, not our replacement for it.
            board = chess.Board(call["fen"])
            board.push_uci(call["expected"])
            engine.pending = board
    return {
        "moves": records,
        "state": {
            "age": engine.age,
            "seen_positions": len(engine.seen),
            "history_entries": len(engine.history),
            "table_entries": sum(entry is not None for entry in engine.table),
            "duplicates": engine.duplicates,
        },
    }


def depth_limited(engine: Any, module: types.ModuleType, depth: int) -> None:
    """Stop the root iteration after `depth`, keeping this instance's state intact."""
    original = type(engine).search

    def patched(self: Any, board: chess.Board, remaining: int, a: int, b: int, ply: int) -> int:
        if ply == 1 and remaining == depth:
            raise module.Deadline
        return int(original(self, board, remaining, a, b, ply))

    engine.search = types.MethodType(patched, engine)


def analyse(
    module: types.ModuleType, fixture: dict[str, Any], depth: int | None, think: float | None
) -> dict[str, Any]:
    call = fixture["calls"][-1]
    engine = module._engine
    clock = module.time
    if depth is not None:
        depth_limited(engine, module, depth)
        vars(module)["time"] = types.SimpleNamespace(perf_counter=lambda: 0.0)
    module._THINK[0] = think or 0.0
    started = time.perf_counter()
    try:
        move = module.get_move(call["fen"], call["clock_ms"])
    finally:
        vars(module)["time"] = clock
        module._THINK[0] = 0.0
        if depth is not None:
            del engine.search
    elapsed = time.perf_counter() - started
    scores = {m.uci(): v for m, v in engine.completed_scores.items()}
    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    return {
        "condition": f"depth {depth}" if depth is not None else f"think {think}s",
        "fixed_depth": depth,
        "think_seconds": think,
        "move": move,
        "blunder_selected": move == fixture["calls"][-1]["expected"],
        "completed_depth": engine.stats["depth"],
        "nodes": engine.stats["nodes"],
        "seconds": elapsed,
        "nps": engine.stats["nodes"] / elapsed if elapsed else None,
        "blunder_score_cp": scores.get(fixture["calls"][-1]["expected"]),
        "best_score_cp": ranked[0][1] if ranked else None,
        "top_root_scores": ranked[:6],
        "root_moves_scored": len(scores),
    }


def refutation(fixture: dict[str, Any], module: types.ModuleType) -> dict[str, Any]:
    board = chess.Board(fixture["calls"][-1]["fen"])
    before = module.material(board)
    line = []
    for san in fixture["round30_fragment"]:
        move = board.parse_san(san)
        line.append({"san": san, "uci": move.uci(), "capture": board.is_capture(move)})
        board.push(move)
    # material() is from the side to move, so read it with White to move again.
    after = module.material(board) if board.turn == chess.WHITE else -module.material(board)
    return {
        "position_before_error": fixture["calls"][-1]["fen"],
        "played": fixture["calls"][-1]["expected"],
        "clock_remaining_ms": fixture["calls"][-1]["clock_ms"],
        "line": line,
        "final_fen": board.fen(),
        "white_material_before_cp": before,
        "white_material_after_cp": after,
        "material_swing_cp": after - before,
    }


def regress(module: types.ModuleType, fixture: dict[str, Any], config: str) -> None:
    """The reusable gate: with the rated state and the real budget, do not play g5f4."""
    rule = json.loads(REGRESSION.read_text())["regression_rule"]
    replay(module, fixture)
    row = analyse(module, fixture, None, None)
    passed = (
        row["move"] != rule["assert_move_not"]
        and row["completed_depth"] >= rule["assert_min_completed_depth"]
    )
    print(
        "REGRESSION "
        + json.dumps(
            {
                "config": config,
                "rule": rule["statement"],
                "move": row["move"],
                "completed_depth": row["completed_depth"],
                "seconds": row["seconds"],
                "blunder_score_cp": row["blunder_score_cp"],
                "passed": passed,
            }
        ),
        flush=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--depths", default="1,2,3,4,5")
    parser.add_argument("--thinks", default="3.0,4.5,6.0")
    parser.add_argument("--cold", action="store_true")
    parser.add_argument("--regress", action="store_true")
    parser.add_argument(
        "--config", default="submitted", choices=("submitted", "candidate", "control")
    )
    args = parser.parse_args()
    fixture = json.loads(FIXTURE.read_text())
    module, digest = instrumented(args.config)
    print("AGENT " + json.dumps({"config": args.config, "sha256": digest}), flush=True)
    print("LOSS " + json.dumps(refutation(fixture, module)), flush=True)
    if args.regress:
        regress(module, fixture, args.config)
        return

    for depth in [int(v) for v in args.depths.split(",") if v]:
        state = replay(module, fixture)
        row = analyse(module, fixture, depth, None)
        print("RESULT " + json.dumps({"config": args.config, "state": state["state"], "replay_ok":
              all(m["matched_rated"] for m in state["moves"]), **row}), flush=True)

    for think in [float(v) for v in args.thinks.split(",") if v]:
        state = replay(module, fixture)
        row = analyse(module, fixture, None, think)
        print("RESULT " + json.dumps({"config": args.config, "state": state["state"], "replay_ok":
              all(m["matched_rated"] for m in state["moves"]), **row}), flush=True)

    if args.cold:
        for think in [float(v) for v in args.thinks.split(",") if v]:
            vars(module)["_engine"] = module.Engine()
            if hasattr(module, "_eval_table"):
                module._eval_table[:] = [None] * len(module._eval_table)
            row = analyse(module, fixture, None, think)
            print("RESULT " + json.dumps({"config": args.config, "state": "cold",
                  "replay_ok": None, **row}), flush=True)

    state = replay(module, fixture)
    print("REPLAY " + json.dumps(state), flush=True)


if __name__ == "__main__":
    main()
