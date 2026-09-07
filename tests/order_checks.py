"""Isolated search-efficiency experiment: conservative delta pruning in quiescence.

The control is the submitted engine `e5f63625...`, which is also the working
`agent.py`. The candidate is that source with a delta-pruning test added inside the
quiescence move loop and one constant beside the other search constants.

Unlike the earlier time-allocation experiment, this candidate is *not* expected to
search identically: pruning is the point. So the equivalence claim here is scoped —
only `Engine.quiesce` and the constant block may differ — and behaviour is compared
rather than asserted equal.

The shared gates (cold import, clock safety, official gate, arena) reuse the
`time_checks` harness with this experiment's manifest bound in, so the established
drivers are not duplicated. Nothing here imports the working `agent.py` as a module.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
import types
from pathlib import Path
from typing import Any, cast

import chess
import numba_validation as numeric
import time_checks

CONTROL_SHA = "e5f63625a30f23ef7f1d625fbb5830f2bdbbed1b5480142a6e31b83cf731325b"
CANDIDATE_SHA = "65ec40ceb29a8f6fe14a74ab2ed6ca446164f701e46d2b9d3fe8d5af94655bda"
MANIFEST = Path("tests/order_experiment.json")
FLAGS = ("ADAPTIVE", "PASSIVE", "FAST_EVAL", "EVAL_CACHE", "DEPTH_EVIDENCE", "TIGHT_ROOT")
DEPTHS = (2, 3, 4)
CLOCKS = (120_000, 30_000)
ADDED_CONSTANTS = {"DELTA_MARGIN"}
CHANGED_METHODS = {"quiesce"}


def bind() -> None:
    """Point the shared harness at this experiment without editing it."""
    time_checks.MANIFEST = MANIFEST
    time_checks.CONTROL_SHA = CONTROL_SHA
    time_checks.CANDIDATE_SHA = CANDIDATE_SHA


def sources() -> dict[str, str]:
    bind()
    return time_checks.sources()


def engines() -> dict[str, types.ModuleType]:
    bind()
    return time_checks.engines()


def counting_engine(module: types.ModuleType, depth: int | None) -> Any:
    """A development engine that counts quiescence calls and can stop at a depth.

    The wrapper adds a Python frame per quiescence node, so it is used for counts
    only; every timing number is taken from an unwrapped run.
    """

    class Counted(module.Engine):  # type: ignore[name-defined, misc]
        def __init__(self) -> None:
            super().__init__()
            self.qnodes = 0

        def quiesce(self, board: chess.Board, alpha: int, beta: int, ply: int) -> int:
            self.qnodes += 1
            return int(super().quiesce(board, alpha, beta, ply))

        def search(
            self, board: chess.Board, remaining: int, alpha: int, beta: int, ply: int
        ) -> int:
            if depth is not None and ply == 1 and remaining == depth:
                raise module.Deadline
            return int(super().search(board, remaining, alpha, beta, ply))

    return Counted()


def fixed(module: types.ModuleType, fen: str, depth: int, counted: bool) -> dict[str, Any]:
    """One clock-free search to a fixed completed depth, through the real interface."""
    if counted:
        vars(module)["_engine"] = counting_engine(module, depth)
        module._eval_table[:] = [None] * len(module._eval_table)
    else:
        numeric.fresh(module, depth)
    real = module.time
    vars(module)["time"] = types.SimpleNamespace(perf_counter=lambda: 0.0)
    started = time.perf_counter()
    try:
        move = module.get_move(fen, 120_000)
    finally:
        vars(module)["time"] = real
    elapsed = time.perf_counter() - started
    engine = module._engine
    assert engine.stats["depth"] == depth
    assert chess.Move.from_uci(move) in chess.Board(fen).legal_moves
    return {
        "move": move,
        "scores": {m.uci(): v for m, v in engine.completed_scores.items()},
        "nodes": engine.nodes,
        "qnodes": getattr(engine, "qnodes", None),
        "seconds": elapsed,
        "nps": engine.nodes / elapsed if elapsed else 0.0,
    }


def identity() -> None:
    """Scope: only Engine.quiesce and one added constant may differ."""
    record = json.loads(MANIFEST.read_text())
    raw = {
        "control": time_checks.resolve(record, "control", CONTROL_SHA),
        "candidate": time_checks.resolve(record, "candidate", CANDIDATE_SHA),
    }
    digests = {name: hashlib.sha256(v).hexdigest() for name, v in raw.items()}
    assert digests["control"] == CONTROL_SHA, digests
    assert digests["candidate"] == CANDIDATE_SHA, digests
    assert all(b"\r" not in v for v in raw.values())

    trees = {name: ast.parse(v.decode()) for name, v in raw.items()}
    control, candidate = trees["control"], trees["candidate"]
    names = [
        {n.name for n in tree.body if isinstance(n, ast.ClassDef | ast.FunctionDef)}
        for tree in (control, candidate)
    ]
    assert names[0] == names[1], "no definition may be added, removed or renamed"

    constants = [
        {t.id for n in tree.body if isinstance(n, ast.Assign) for t in n.targets
         if isinstance(t, ast.Name)}
        for tree in (control, candidate)
    ]
    assert not constants[0] - constants[1], "no constant may be removed"
    assert constants[1] - constants[0] == ADDED_CONSTANTS, constants[1] - constants[0]

    for node in control.body:
        name = getattr(node, "name", "")
        if not isinstance(node, ast.ClassDef | ast.FunctionDef):
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
        differing = {
            m for m in methods[0] if ast.dump(methods[0][m]) != ast.dump(methods[1][m])
        }
        assert differing == CHANGED_METHODS, differing

    modules = engines()
    for flag in FLAGS:
        assert getattr(modules["control"], flag) == getattr(modules["candidate"], flag), flag
    signatures = {n: numeric.signatures(m) for n, m in modules.items()}
    assert signatures["control"] == signatures["candidate"], "Numba signatures must match"
    assert all(signatures["candidate"].values())
    assert modules["candidate"].DELTA_MARGIN == 200
    # The public API and the time allocation this experiment must preserve.
    assert modules["candidate"].get_move.__doc__ == modules["control"].get_move.__doc__
    control_budget = time_checks.budget_of(modules["control"], 120_000)
    candidate_budget = time_checks.budget_of(modules["candidate"], 120_000)
    assert control_budget == candidate_budget, (control_budget, candidate_budget)

    count = 0
    reachable = [time_checks.start_board(o).fen() for o in time_checks.openings()]
    for fen in numeric.suite() + reachable:
        board = chess.Board(fen)
        for tested in (board, board.mirror()):
            assert modules["candidate"].evaluate(tested) == modules["control"].evaluate(tested)
            count += 1
    print(
        "IDENTITY "
        + json.dumps(
            {
                "sha256": digests,
                "bytes": {n: len(v) for n, v in raw.items()},
                "definitions_added_or_removed": 0,
                "constants_added": sorted(ADDED_CONSTANTS),
                "engine_methods_changed": sorted(CHANGED_METHODS),
                "every_other_definition_identical": True,
                "flags": {f: getattr(modules["candidate"], f) for f in FLAGS},
                "numba_signatures_identical": True,
                "budget_s_at_120000ms": control_budget,
                "evaluation_comparisons": count,
                "evaluation_identical": True,
            }
        ),
        flush=True,
    )


def corpus(repeats: int) -> None:
    """Fixed-depth behaviour on the 24-position tactical suite at depths 2, 3 and 4."""
    modules = engines()
    suite = numeric.suite()
    for depth in DEPTHS:
        for index, fen in enumerate(suite):
            counts = {n: fixed(m, fen, depth, True) for n, m in modules.items()}
            for repeat in range(repeats):
                order = ("control", "candidate")
                if (repeat + index + depth) % 2:
                    order = ("candidate", "control")
                timed = {n: fixed(modules[n], fen, depth, False) for n in order}
                row = {
                    "position": index,
                    "fen": fen,
                    "depth": depth,
                    "repeat": repeat,
                    "order": list(order),
                }
                for name in ("control", "candidate"):
                    row[name] = {
                        "move": counts[name]["move"],
                        "nodes": counts[name]["nodes"],
                        "qnodes": counts[name]["qnodes"],
                        "root_score": counts[name]["scores"].get(counts[name]["move"]),
                        "root_moves_scored": len(counts[name]["scores"]),
                        "seconds": timed[name]["seconds"],
                        "nps": timed[name]["nps"],
                    }
                control_row = cast(dict[str, Any], row["control"])
                candidate_row = cast(dict[str, Any], row["candidate"])
                row["same_move"] = control_row["move"] == candidate_row["move"]
                row["node_ratio"] = candidate_row["nodes"] / max(1, control_row["nodes"])
                row["qnode_ratio"] = candidate_row["qnodes"] / max(1, control_row["qnodes"])
                row["nps_gain"] = candidate_row["nps"] / max(1e-9, control_row["nps"]) - 1
                print("CORPUS " + json.dumps(row), flush=True)


def adjudicate() -> None:
    """Re-examine every fixed-depth disagreement against a deeper control search."""
    modules = engines()
    suite = numeric.suite()
    rows = []
    for depth in DEPTHS:
        for index, fen in enumerate(suite):
            control = fixed(modules["control"], fen, depth, False)
            candidate = fixed(modules["candidate"], fen, depth, False)
            if control["move"] == candidate["move"]:
                continue
            deeper = fixed(modules["control"], fen, depth + 1, False)
            scores = deeper["scores"]
            best = max(scores.values()) if scores else 0
            loss = best - scores.get(candidate["move"], best)
            control_loss = best - scores.get(control["move"], best)
            rows.append(
                {
                    "position": index,
                    "fen": fen,
                    "depth": depth,
                    "control_move": control["move"],
                    "candidate_move": candidate["move"],
                    "referee_depth": depth + 1,
                    "referee_best_cp": best,
                    "candidate_loss_cp": loss,
                    "control_loss_cp": control_loss,
                    "tactical_regression": loss >= 100 and loss > control_loss,
                }
            )
            print("ADJUDICATE " + json.dumps(rows[-1]), flush=True)
    print(
        "ADJUDICATION "
        + json.dumps(
            {
                "disagreements": len(rows),
                "comparisons": len(DEPTHS) * len(suite),
                "tactical_regressions": sum(r["tactical_regression"] for r in rows),
                "worst_candidate_loss_cp": max((r["candidate_loss_cp"] for r in rows), default=0),
            }
        ),
        flush=True,
    )


def timed(repeats: int, noise: bool = False) -> None:
    """Realistic-clock completed depth and throughput, counterbalanced by order.

    With `noise`, the control is loaded under both names. Any difference the run then
    reports is wall-clock noise alone, which is the floor a real effect must clear.
    """
    modules = engines()
    if noise:
        modules = {"control": modules["control"], "candidate": engines()["control"]}
    suite = numeric.suite()
    for repeat in range(repeats):
        for index, fen in enumerate(suite):
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
                        "hard_slack_s": module._engine.deadline - started - seconds,
                    }
                print(
                    "TIMED "
                    + json.dumps(
                        {
                            "repeat": repeat,
                            "position": index,
                            "clock_ms": clock,
                            "order": list(order),
                            "control": measured["control"],
                            "candidate": measured["candidate"],
                            "same_move": measured["control"]["move"]
                            == measured["candidate"]["move"],
                            "depth_change": measured["candidate"]["depth"]
                            - measured["control"]["depth"],
                            "nps_gain": measured["candidate"]["nps"]
                            / max(1e-9, measured["control"]["nps"])
                            - 1,
                        }
                    ),
                    flush=True,
                )


GATE_COMMANDS: tuple[tuple[str, list[str]], ...] = (
    ("official_gate", ["make", "gate"]),
    ("verify", ["python", "tests/verify.py"]),
    ("determinism", ["python", "tests/determinism.py"]),
    # units, invariants and lifecycle only. unchanged_search is a historical AST
    # identity check against the pre-Numba checkpoint that no engine since the qcap
    # experiment can pass; it is left intact and not run, as QCAP.md records.
    ("passive", ["python", "tests/qgen_checks.py", "passive"]),
)


def official_gate(configs: list[str], engine_sources: dict[str, str] | None = None) -> None:
    """The official gate plus the correctness suites, run against each source.

    A later experiment can pass its own pair of sources so this driver, and the exact
    set of commands it runs, is shared rather than copied.
    """
    engine_sources = sources() if engine_sources is None else engine_sources
    for name in configs:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "agent.py").write_text(engine_sources[name])
            for directory in ("harness", "baselines", "tests"):
                shutil.copytree(time_checks.ROOT / directory, root / directory)
            for filename in ("Makefile", "pyproject.toml", "uv.lock"):
                shutil.copyfile(time_checks.ROOT / filename, root / filename)
            environment = {**os.environ, "PYTHONPATH": f"{root}:{root / 'tests'}"}
            for label, command in GATE_COMMANDS:
                checked = subprocess.run(
                    command, cwd=root, capture_output=True, text=True, env=environment
                )
                if checked.returncode:
                    tail = (checked.stdout + checked.stderr).splitlines()[-80:]
                    print("\n".join(tail))
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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "mode",
        choices=("identity", "corpus", "adjudicate", "timed", "cold", "clock", "gate", "arena"),
    )
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--noise", action="store_true")
    parser.add_argument("--moves", type=int, default=300)
    parser.add_argument("--stress-moves", type=int, default=600)
    parser.add_argument("--cases", type=int, default=10)
    parser.add_argument("--case-offset", type=int, default=0)
    parser.add_argument("--seed", type=int, default=63000)
    parser.add_argument("--base-ms", type=int, default=120_000)
    parser.add_argument("--increment-ms", type=int, default=500)
    parser.add_argument("--configs", default="control,candidate")
    args = parser.parse_args()
    bind()
    if args.mode == "identity":
        identity()
    elif args.mode == "corpus":
        corpus(args.repeats)
    elif args.mode == "adjudicate":
        adjudicate()
    elif args.mode == "timed":
        timed(args.repeats, args.noise)
    elif args.mode == "cold":
        time_checks.cold(args.repeats)
    elif args.mode == "clock":
        time_checks.clock_safety(args.moves, args.stress_moves, args.base_ms, args.increment_ms)
    elif args.mode == "gate":
        official_gate(args.configs.split(","))
    else:
        time_checks.arena(args)


if __name__ == "__main__":
    main()
