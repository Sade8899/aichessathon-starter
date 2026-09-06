"""Isolated time-allocation experiment: one budget expression, identical search.

The control is the working development agent `be5da869...`. The candidate is that
source with the constant 3.0 s per-move ceiling removed from the budget expression
and nothing else changed; `available / 32` and the final clock reserve stay.

Every mode here loads both sources from `time_experiment.json` and never imports the
working `agent.py` as a module, so the repository copy is untouched throughout.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import random
import resource
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
import types
from pathlib import Path
from typing import Any, cast

import chess
import numba_validation as numeric
import selection
from passive_arena import percentile
from selection import MeasuredAgent, load

from harness.rules import INIT_BUDGET_S

ROOT = Path("/workspace")
MANIFEST = Path("tests/time_experiment.json")
CONTROL_SHA = "be5da8696f01924e2f752b1686e1d6a94b38dc72fff86a1f7fedb05f006c56e0"
CANDIDATE_SHA = "e5f63625a30f23ef7f1d625fbb5830f2bdbbed1b5480142a6e31b83cf731325b"
FLAGS = ("ADAPTIVE", "PASSIVE", "FAST_EVAL", "EVAL_CACHE", "DEPTH_EVIDENCE", "TIGHT_ROOT")
# The cap binds only above 96 s, so the ladder brackets that boundary on both sides.
CLOCKS = (120_000, 113_600, 100_000, 96_000, 48_000, 10_000)
CAP_S = 3.0
DIVISOR = 32
RESERVE_S = 0.025
HARD_FRACTION = 0.96


def manifest() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(MANIFEST.read_text()))


def sources() -> dict[str, str]:
    record = manifest()
    assert record["control_sha256"] == CONTROL_SHA
    assert record["candidate_sha256"] == CANDIDATE_SHA
    result = {}
    for name, sha in (("control", CONTROL_SHA), ("candidate", CANDIDATE_SHA)):
        raw = Path(record[name + "_path"]).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == sha, name
        assert b"\r" not in raw, name
        result[name] = raw.decode()
    # The control is the working agent.py; its frozen copy must be the same bytes.
    frozen_control = Path(record["control_frozen_path"]).read_bytes()
    assert hashlib.sha256(frozen_control).hexdigest() == CONTROL_SHA
    return result


def engines() -> dict[str, types.ModuleType]:
    """Cold import of both sources, inside the 90 s platform initialization budget."""
    result = {}
    for name, source in sources().items():
        started = time.perf_counter()
        result[name] = load("time_" + name, source)
        seconds = time.perf_counter() - started
        assert seconds < 90.0, (name, seconds)
        print(
            "INIT "
            + json.dumps(
                {
                    "name": name,
                    "seconds": seconds,
                    "rss_mb": resource.getrusage(0).ru_maxrss / 1024,
                }
            ),
            flush=True,
        )
    return result


def cold(repeats: int) -> None:
    """Cold import in a fresh process, through the harness the platform mirrors.

    Loading both sources into one interpreter is not a cold measurement: the second
    reuses Numba's warmed state. Each repeat here is a new runner subprocess that
    imports one source and answers one move, exactly as a rated game starts.
    """
    engine_sources = sources()
    rows: list[dict[str, Any]] = []
    for name, source in engine_sources.items():
        for repeat in range(repeats):
            with tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                (directory / "agent.py").write_text(source)
                agent = MeasuredAgent(directory)
                started = time.perf_counter()
                try:
                    agent.start(INIT_BUDGET_S)
                    initialization = time.perf_counter() - started
                    move = agent.move(chess.STARTING_FEN, 120_000)
                finally:
                    agent.stop()
            assert chess.Move.from_uci(move) in chess.Board().legal_moves
            assert initialization < INIT_BUDGET_S, (name, initialization)
            rows.append(
                {
                    "config": name,
                    "repeat": repeat,
                    "import_seconds": initialization,
                    "first_move": move,
                    "first_move_seconds": agent.times[0],
                }
            )
            print("COLD " + json.dumps(rows[-1]), flush=True)
    summary = {
        name: {
            "repeats": repeats,
            "median_import_s": statistics.median(
                r["import_seconds"] for r in rows if r["config"] == name
            ),
            "worst_import_s": max(r["import_seconds"] for r in rows if r["config"] == name),
            "budget_s": INIT_BUDGET_S,
            "first_moves": sorted({str(r["first_move"]) for r in rows if r["config"] == name}),
        }
        for name in engine_sources
    }
    print("COLD_SUMMARY " + json.dumps(summary), flush=True)


def frozen(module: types.ModuleType, fen: str, clock: int, depth: int) -> dict[str, Any]:
    """Run one root iteration with the clock frozen, and read the budget back.

    `deadline` is `started + budget * 0.96` and `started` is zero under a frozen
    counter, so this recovers the budget the engine actually set without reading
    the source expression.
    """
    numeric.fresh(module, depth)
    real = module.time
    vars(module)["time"] = types.SimpleNamespace(perf_counter=lambda: 0.0)
    try:
        move = module.get_move(fen, clock)
    finally:
        vars(module)["time"] = real
    engine = module._engine
    assert engine.stats["depth"] == depth
    assert chess.Move.from_uci(move) in chess.Board(fen).legal_moves
    return {
        "move": move,
        "budget_s": engine.deadline / HARD_FRACTION,
        "deadline_s": engine.deadline,
        "scores": {m.uci(): v for m, v in engine.completed_scores.items()},
        "nodes": engine.nodes,
    }


def budget_of(module: types.ModuleType, clock_ms: int) -> float:
    """The engine's own per-move budget at this clock, measured not re-derived."""
    return cast(float, frozen(module, chess.STARTING_FEN, clock_ms, 1)["budget_s"])


def choose_body(tree: ast.Module) -> list[ast.stmt]:
    engine = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Engine")
    method = next(n for n in engine.body if isinstance(n, ast.FunctionDef) and n.name == "choose")
    return method.body


def identity() -> None:
    """The two sources differ on one line, inside one statement, inside one method."""
    record = manifest()
    raw = {
        "control": Path(record["control_path"]).read_bytes(),
        "candidate": Path(record["candidate_path"]).read_bytes(),
    }
    lines = {name: value.decode().splitlines() for name, value in raw.items()}
    assert len(lines["control"]) == len(lines["candidate"])
    differing: list[dict[str, Any]] = [
        {"line": index + 1, "control": a, "candidate": b}
        for index, (a, b) in enumerate(zip(lines["control"], lines["candidate"], strict=True))
        if a != b
    ]
    assert len(differing) == 1, differing
    assert "budget = min(" in differing[0]["control"]
    assert "3.0" in differing[0]["control"] and "3.0" not in differing[0]["candidate"]
    for text in (differing[0]["control"], differing[0]["candidate"]):
        assert "available / 32" in text and "max(0.001, available - 0.025)" in text

    trees = {name: ast.parse(value.decode()) for name, value in raw.items()}
    control, candidate = trees["control"], trees["candidate"]
    names = [
        {n.name for n in tree.body if isinstance(n, ast.ClassDef | ast.FunctionDef)}
        for tree in (control, candidate)
    ]
    assert names[0] == names[1], names
    # Nothing outside Engine.choose may move, including every module-level constant.
    for position, node in enumerate(control.body):
        name = getattr(node, "name", "")
        if not isinstance(node, ast.ClassDef | ast.FunctionDef):
            assert ast.dump(node) == ast.dump(candidate.body[position]), ast.dump(node)[:120]
            continue
        other = next(n for n in candidate.body if getattr(n, "name", "") == name)
        if name != "Engine":
            assert ast.dump(node) == ast.dump(other), name
            continue
        methods = [
            {n.name: n for n in cls.body if isinstance(n, ast.FunctionDef)}
            for cls in (node, cast(ast.ClassDef, other))
        ]
        assert set(methods[0]) == set(methods[1])
        for method in methods[0]:
            if method == "choose":
                continue
            assert ast.dump(methods[0][method]) == ast.dump(methods[1][method]), method
    bodies = [choose_body(control), choose_body(candidate)]
    assert len(bodies[0]) == len(bodies[1])
    changed = [
        index
        for index, (a, b) in enumerate(zip(bodies[0], bodies[1], strict=True))
        if ast.dump(a) != ast.dump(b)
    ]
    assert len(changed) == 1, changed
    statement = [ast.unparse(body[changed[0]]) for body in bodies]
    assert all(text.startswith("budget = min(") for text in statement), statement

    modules = engines()
    for flag in FLAGS:
        assert getattr(modules["control"], flag) == getattr(modules["candidate"], flag), flag
    signatures = {name: numeric.signatures(m) for name, m in modules.items()}
    assert signatures["control"] == signatures["candidate"]
    assert all(signatures["candidate"].values())
    print(
        "IDENTITY "
        + json.dumps(
            {
                "sha256": {n: hashlib.sha256(v).hexdigest() for n, v in raw.items()},
                "bytes": {n: len(v) for n, v in raw.items()},
                "lines_differing": len(differing),
                "difference": differing[0],
                "statements_differing_in_choose": len(changed),
                "statement": {"control": statement[0], "candidate": statement[1]},
                "every_other_definition_identical": True,
                "flags": {f: getattr(modules["candidate"], f) for f in FLAGS},
                "numba_signatures_identical": True,
            }
        ),
        flush=True,
    )


def budget() -> None:
    """The budget curve itself: measured from both engines over the whole clock range."""
    modules = engines()
    grid = sorted(
        {50, 100, 250, 500, 1_000, 2_000, 5_000, 10_000, 20_000, 30_000, 48_000, 60_000}
        | {90_000, 95_000, 95_900, 96_000, 96_100, 100_000, 110_000, 113_600, 120_000}
        | set(CLOCKS)
    )
    rows = []
    for clock in grid:
        measured = {name: budget_of(m, clock) for name, m in modules.items()}
        available = clock / 1000
        expected = {
            "control": min(CAP_S, available / DIVISOR, max(0.001, available - RESERVE_S)),
            "candidate": min(available / DIVISOR, max(0.001, available - RESERVE_S)),
        }
        for name, value in measured.items():
            assert abs(value - expected[name]) < 1e-9, (clock, name, value, expected[name])
            # The reserve is what keeps the last move on the clock; it must survive.
            assert value <= available - RESERVE_S or available < 0.03, (clock, name)
            assert value > 0
        capped = available > CAP_S * DIVISOR
        if capped:
            assert measured["candidate"] > measured["control"], clock
        else:
            assert measured["candidate"] == measured["control"], clock
        rows.append(
            {
                "clock_ms": clock,
                "control_budget_s": measured["control"],
                "candidate_budget_s": measured["candidate"],
                "extra_s": measured["candidate"] - measured["control"],
                "cap_binds_for_control": capped,
            }
        )
        print("BUDGET " + json.dumps(rows[-1]), flush=True)
    curve = [r["candidate_budget_s"] for r in rows]
    assert curve == sorted(curve), "candidate budget must not fall as the clock rises"
    above = [r for r in rows if r["cap_binds_for_control"]]
    print(
        "BUDGET_SUMMARY "
        + json.dumps(
            {
                "clocks": len(rows),
                "identical_at_or_below_96s": all(
                    r["extra_s"] == 0 for r in rows if not r["cap_binds_for_control"]
                ),
                "cap_boundary_ms": CAP_S * DIVISOR * 1000,
                "max_extra_s": max(r["extra_s"] for r in rows),
                "extra_at_120000_ms": next(r["extra_s"] for r in rows if r["clock_ms"] == 120_000),
                "extra_at_113600_ms": next(r["extra_s"] for r in rows if r["clock_ms"] == 113_600),
                "clocks_where_candidate_thinks_longer": [r["clock_ms"] for r in above],
                "reserve_s": RESERVE_S,
                "divisor": DIVISOR,
            }
        ),
        flush=True,
    )


def openings() -> list[tuple[str, ...]]:
    corpus = json.loads(Path("tests/quiet_openings.json").read_text())
    return [tuple(row["uci"]) for row in corpus["positions"]]


def start_board(moves: tuple[str, ...]) -> chess.Board:
    board = chess.Board()
    for uci in moves:
        board.push_uci(uci)
    return board


def walk(
    module: types.ModuleType, name: str, moves: int, base_ms: int, inc_ms: int
) -> dict[str, Any]:
    """Draw `moves` consecutive budgets on one live clock, as a longest game would.

    The referee stops at 600 plies, so 300 is every move one side can ever make.
    A random legal opponent replies; when a position terminates before the walk is
    over the board advances to the next declared opening and the clock carries on,
    because what is under test is the clock recurrence, not the positions.
    """
    numeric.fresh(module)
    rng = random.Random(4409)
    corpus = openings()
    index = 0
    board = start_board(corpus[0])
    clock = float(base_ms)
    restarts = 0
    rows: list[dict[str, Any]] = []
    for ply in range(moves):
        while board.is_game_over() or not any(board.legal_moves):
            index += 1
            restarts += 1
            board = start_board(corpus[index % len(corpus)])
        before = clock
        started = time.perf_counter()
        uci = module.get_move(board.fen(), int(clock))
        elapsed = (time.perf_counter() - started) * 1000.0
        clock -= elapsed
        move = chess.Move.from_uci(uci)
        assert move in board.legal_moves, (ply, uci, board.fen())
        assert clock > 0, {"ply": ply, "clock_ms": clock, "elapsed_ms": elapsed}
        estimated = (module._engine.deadline - started) / HARD_FRACTION * 1000.0
        rows.append(
            {
                "ply": ply,
                "clock_before_ms": before,
                "elapsed_ms": elapsed,
                "budget_ms": estimated,
                "spend_ratio": elapsed / estimated if estimated > 0 else 0.0,
                "clock_after_ms": clock,
                "depth": module._engine.stats["depth"],
                "nodes": module._engine.stats["nodes"],
            }
        )
        board.push(move)
        clock += inc_ms
        if not board.is_game_over() and any(board.legal_moves):
            board.push(rng.choice(list(board.legal_moves)))
    ratios = [r["spend_ratio"] for r in rows]
    record = {
        "config": name,
        "moves": moves,
        "plies_covered": moves * 2,
        "base_ms": base_ms,
        "increment_ms": inc_ms,
        "restarts": restarts,
        "min_clock_ms": min(r["clock_after_ms"] for r in rows),
        "final_clock_ms": rows[-1]["clock_after_ms"] + inc_ms,
        "max_elapsed_ms": max(r["elapsed_ms"] for r in rows),
        "median_elapsed_ms": statistics.median(r["elapsed_ms"] for r in rows),
        "max_spend_ratio": max(ratios),
        "p95_spend_ratio": percentile(ratios, 0.95),
        "median_spend_ratio": statistics.median(ratios),
        "mean_depth": statistics.mean(r["depth"] for r in rows),
        "total_nodes": sum(r["nodes"] for r in rows),
        "flagged": False,
    }
    print("WALK " + json.dumps(record), flush=True)
    return record


def recurrence(
    module: types.ModuleType, name: str, moves: int, base_ms: int, inc_ms: int, ratio: float
) -> dict[str, Any]:
    """Propagate the worst measured overshoot for a whole game, budget by budget."""
    clock = float(base_ms)
    trace = []
    for _ in range(moves):
        spend = budget_of(module, max(1, int(clock))) * 1000.0 * ratio
        clock -= spend
        trace.append(clock)
        if clock <= 0:
            break
        clock += inc_ms
    record = {
        "config": name,
        "moves": moves,
        "spend_ratio": ratio,
        "steps_survived": len(trace),
        "min_clock_ms": min(trace),
        "final_clock_ms": trace[-1],
        "flagged": min(trace) <= 0,
    }
    print("RECURRENCE " + json.dumps(record), flush=True)
    return record


def clock_safety(moves: int, stress: int, base_ms: int, inc_ms: int) -> None:
    modules = engines()
    walks = {name: walk(m, name, moves, base_ms, inc_ms) for name, m in modules.items()}
    observed = max(cast(float, w["max_spend_ratio"]) for w in walks.values())
    results = []
    for name, module in modules.items():
        for ratio in (1.0, observed, observed * 1.5, observed * 2.0):
            for horizon in (moves, stress):
                results.append(recurrence(module, name, horizon, base_ms, inc_ms, ratio))
    assert not any(w["flagged"] for w in walks.values())
    assert not any(r["flagged"] for r in results)
    print(
        "CLOCK "
        + json.dumps(
            {
                "walks": walks,
                "worst_observed_spend_ratio": observed,
                "recurrences": results,
                "min_clock_ms_over_everything": min(
                    [cast(float, w["min_clock_ms"]) for w in walks.values()]
                    + [cast(float, r["min_clock_ms"]) for r in results]
                ),
                "flags": 0,
            }
        ),
        flush=True,
    )


def fixed_identity() -> None:
    """At a fixed depth the two engines are the same program: move, scores and nodes."""
    modules = engines()
    rows = []
    for index, fen in enumerate(numeric.suite()):
        for depth in (2, 3):
            first = frozen(modules["control"], fen, 120_000, depth)
            second = frozen(modules["candidate"], fen, 120_000, depth)
            for key in ("move", "scores", "nodes"):
                assert first[key] == second[key], (fen, depth, key)
            rows.append(
                {
                    "position": index,
                    "depth": depth,
                    "move": second["move"],
                    "nodes": second["nodes"],
                    "root_moves_scored": len(second["scores"]),
                }
            )
    print(
        "FIXED_IDENTITY "
        + json.dumps({"positions": len(numeric.suite()), "comparisons": len(rows), "rows": rows}),
        flush=True,
    )


def ladder(repeats: int, offset: int, count: int, repeat_offset: int = 0) -> None:
    """Completed depth and chosen move at every clock, on the fixed tactical corpus."""
    modules = engines()
    chosen = list(enumerate(numeric.suite()))[offset : offset + count]
    for repeat in range(repeat_offset, repeat_offset + repeats):
        for index, fen in chosen:
            for clock in CLOCKS:
                order = ("control", "candidate")
                if (repeat + index + CLOCKS.index(clock)) % 2:
                    order = ("candidate", "control")
                measured = {}
                for name in order:
                    module = modules[name]
                    numeric.fresh(module)
                    started = time.perf_counter()
                    move = module.get_move(fen, clock)
                    seconds = time.perf_counter() - started
                    assert chess.Move.from_uci(move) in chess.Board(fen).legal_moves
                    assert seconds * 1000 < clock, (name, clock, seconds)
                    measured[name] = {
                        "move": move,
                        "depth": module._engine.stats["depth"],
                        "nodes": module._engine.stats["nodes"],
                        "seconds": seconds,
                        "nps": module._engine.stats["nodes"] / seconds,
                        "budget_s": (module._engine.deadline - started) / HARD_FRACTION,
                        "hard_slack_s": module._engine.deadline - started - seconds,
                    }
                row = {
                    "repeat": repeat,
                    "position": index,
                    "fen": fen,
                    "clock_ms": clock,
                    "order": list(order),
                    "control": measured["control"],
                    "candidate": measured["candidate"],
                    "same_move": measured["control"]["move"] == measured["candidate"]["move"],
                    "depth_change": measured["candidate"]["depth"] - measured["control"]["depth"],
                }
                print("LADDER " + json.dumps(row), flush=True)


def ladder_report() -> None:
    rows = [json.loads(line[7:]) for line in sys.stdin if line.startswith("LADDER ")]
    assert rows, "no LADDER rows on standard input"
    per_clock = []
    for clock in sorted({r["clock_ms"] for r in rows}, reverse=True):
        own = [r for r in rows if r["clock_ms"] == clock]
        entry: dict[str, Any] = {
            "clock_ms": clock,
            "measurements": len(own),
            "cap_binds_for_control": clock / 1000 > CAP_S * DIVISOR,
            "move_agreement": statistics.mean(float(r["same_move"]) for r in own),
            "positions_deeper": sum(r["depth_change"] > 0 for r in own),
            "positions_shallower": sum(r["depth_change"] < 0 for r in own),
            "mean_depth_change": statistics.mean(r["depth_change"] for r in own),
        }
        for name in ("control", "candidate"):
            entry[name] = {
                "mean_depth": statistics.mean(r[name]["depth"] for r in own),
                "median_depth": statistics.median(r[name]["depth"] for r in own),
                "median_seconds": statistics.median(r[name]["seconds"] for r in own),
                "worst_seconds": max(r[name]["seconds"] for r in own),
                "median_nodes": statistics.median(r[name]["nodes"] for r in own),
                "median_nps": statistics.median(r[name]["nps"] for r in own),
                "min_hard_slack_s": min(r[name]["hard_slack_s"] for r in own),
                "median_budget_s": statistics.median(r[name]["budget_s"] for r in own),
            }
        per_clock.append(entry)
    capped = [e for e in per_clock if e["cap_binds_for_control"]]
    uncapped = [e for e in per_clock if not e["cap_binds_for_control"]]
    below = [r for r in rows if r["clock_ms"] / 1000 <= CAP_S * DIVISOR]
    above = [r for r in rows if r["clock_ms"] / 1000 > CAP_S * DIVISOR]
    # At or below 96 s the budget mode proves the two budgets are equal, so every
    # difference there is wall-clock noise. That is the floor the signal must clear;
    # a single measurement flipping one iteration is not a behaviour change.
    floor = {
        "measurements": len(below),
        "mean_depth_change": statistics.mean(r["depth_change"] for r in below),
        "deeper": sum(r["depth_change"] > 0 for r in below),
        "shallower": sum(r["depth_change"] < 0 for r in below),
        "move_agreement": statistics.mean(float(r["same_move"]) for r in below),
        "max_absolute_clock_level_mean": max(abs(e["mean_depth_change"]) for e in uncapped),
        "median_seconds_difference_s": statistics.median(
            e["candidate"]["median_seconds"] - e["control"]["median_seconds"] for e in uncapped
        ),
    }
    signal = {
        "measurements": len(above),
        "mean_depth_change": statistics.mean(r["depth_change"] for r in above),
        "deeper": sum(r["depth_change"] > 0 for r in above),
        "shallower": sum(r["depth_change"] < 0 for r in above),
        "move_agreement": statistics.mean(float(r["same_move"]) for r in above),
        "clock_levels_net_deeper": sum(e["mean_depth_change"] > 0 for e in capped),
        "clock_levels_net_shallower": sum(e["mean_depth_change"] < 0 for e in capped),
        "extra_thinking_time_s": statistics.mean(
            e["candidate"]["median_seconds"] - e["control"]["median_seconds"] for e in capped
        ),
    }
    aggregate = {
        "measurements": len(rows),
        "positions": len({r["position"] for r in rows}),
        "repeats": len({r["repeat"] for r in rows}),
        "noise_floor_at_identical_budgets": floor,
        "signal_where_the_cap_binds": signal,
        "depth_regressions": [
            {"position": r["position"], "clock_ms": r["clock_ms"], "repeat": r["repeat"]}
            for r in rows
            if r["depth_change"] < 0
        ],
        "signal_exceeds_noise": bool(
            signal["mean_depth_change"] > floor["max_absolute_clock_level_mean"]
        ),
        "no_clock_level_net_shallower": signal["clock_levels_net_shallower"] == 0,
        "converts_time_into_depth": bool(
            signal["deeper"] > signal["shallower"]
            and signal["mean_depth_change"] > floor["max_absolute_clock_level_mean"]
            and signal["clock_levels_net_shallower"] == 0
            and signal["extra_thinking_time_s"] > 0
        ),
    }
    print(json.dumps({"aggregate": aggregate, "per_clock": per_clock}, indent=2))


def official_gate(configs: list[str]) -> None:
    """Run the official gate, verify.py and determinism.py against each source."""
    engine_sources = sources()
    for name in configs:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "agent.py").write_text(engine_sources[name])
            for directory in ("harness", "baselines", "tests"):
                shutil.copytree(ROOT / directory, root / directory)
            for filename in ("Makefile", "pyproject.toml", "uv.lock"):
                shutil.copyfile(ROOT / filename, root / filename)
            environment = {**os.environ, "PYTHONPATH": f"{root}:{root / 'tests'}"}
            for label, command in (
                ("official_gate", ["make", "gate"]),
                ("verify", ["python", "tests/verify.py"]),
                ("determinism", ["python", "tests/determinism.py"]),
            ):
                checked = subprocess.run(
                    command, cwd=root, capture_output=True, text=True, env=environment
                )
                if checked.returncode:
                    print("\n".join((checked.stdout + checked.stderr).splitlines()[-80:]))
                    raise AssertionError((name, label))
                print(
                    "GATE "
                    + json.dumps(
                        {
                            "config": name,
                            "check": label,
                            "passed": True,
                            "tail": checked.stdout.strip().splitlines()[-1:],
                        }
                    ),
                    flush=True,
                )


def arena(args: argparse.Namespace) -> None:
    """Twelve matched cases, both configs, full clock, against the control as sparring."""
    corpus = json.loads(Path("tests/quiet_openings.json").read_text())
    vars(selection)["OPENINGS"] = tuple(tuple(row["uci"]) for row in corpus["positions"])
    engine_sources = sources()
    vars(selection)["sources"] = lambda: {**engine_sources, "reference": engine_sources["control"]}
    assert sorted(args.configs.split(",")) == ["candidate", "control"], args.configs
    assert args.cases % 2 == 0 and args.case_offset % 2 == 0
    args.sparring = True
    args.spread_openings = False
    args.games = 0
    selection.arena(args)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "mode",
        choices=(
            "identity",
            "cold",
            "budget",
            "clock",
            "fixed",
            "ladder",
            "ladder-report",
            "gate",
            "arena",
        ),
    )
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--repeat-offset", type=int, default=0)
    parser.add_argument("--count", type=int, default=24)
    parser.add_argument("--moves", type=int, default=300)
    parser.add_argument("--stress-moves", type=int, default=600)
    parser.add_argument("--cases", type=int, default=12)
    parser.add_argument("--case-offset", type=int, default=0)
    parser.add_argument("--seed", type=int, default=61000)
    parser.add_argument("--base-ms", type=int, default=120_000)
    parser.add_argument("--increment-ms", type=int, default=500)
    parser.add_argument("--configs", default="control,candidate")
    parser.add_argument("--experiment", default=str(MANIFEST))
    args = parser.parse_args()
    globals()["MANIFEST"] = Path(args.experiment)
    if args.mode == "identity":
        identity()
    elif args.mode == "cold":
        cold(args.repeats)
    elif args.mode == "budget":
        budget()
    elif args.mode == "clock":
        clock_safety(args.moves, args.stress_moves, args.base_ms, args.increment_ms)
    elif args.mode == "fixed":
        fixed_identity()
    elif args.mode == "ladder":
        ladder(args.repeats, args.offset, args.count, args.repeat_offset)
    elif args.mode == "ladder-report":
        ladder_report()
    elif args.mode == "gate":
        official_gate(args.configs.split(","))
    else:
        arena(args)


if __name__ == "__main__":
    main()
