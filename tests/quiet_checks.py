"""Isolated quiet-check experiment using the official interface and referee."""

import argparse
import ast
import hashlib
import json
import os
import resource
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
from selection import load

CONTROL_SHA = "59f99079f1db99221683dd3f06391f4fc502c1dae11fb712b08170242649830a"
CONTROL = Path("tests/numba_checkpoint") / CONTROL_SHA / "agent.py"
FIXTURE = Path("tests/tournament/fixtures/rated.json")


def sources() -> dict[str, str]:
    raw = CONTROL.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == CONTROL_SHA
    manifest = json.loads(Path("tests/quiet_experiment.json").read_text())
    candidate = Path(manifest["candidate_path"]).read_bytes()
    assert hashlib.sha256(candidate).hexdigest() == manifest["candidate_sha256"]
    return {"control": raw.decode(), "candidate": candidate.decode()}


def engines() -> dict[str, types.ModuleType]:
    result = {}
    for name, source in sources().items():
        started = time.perf_counter()
        result[name] = load("quiet_" + name, source)
        print(
            "INIT "
            + json.dumps(
                {
                    "name": name,
                    "seconds": time.perf_counter() - started,
                    "rss_mb": resource.getrusage(0).ru_maxrss / 1024,
                }
            ),
            flush=True,
        )
    return result


def fixed(module: types.ModuleType, fen: str, depth: int) -> dict[str, Any]:
    numeric.fresh(module, depth)
    clock = module.time
    vars(module)["time"] = types.SimpleNamespace(perf_counter=lambda: 0.0)
    started = time.perf_counter()
    try:
        move = module.get_move(fen, 120000)
    finally:
        vars(module)["time"] = clock
    elapsed = time.perf_counter() - started
    assert module._engine.stats["depth"] == depth
    assert chess.Move.from_uci(move) in chess.Board(fen).legal_moves
    return {
        "move": move,
        "scores": {m.uci(): v for m, v in module._engine.completed_scores.items()},
        "nodes": module._engine.nodes,
        "seconds": elapsed,
        "nps": module._engine.nodes / elapsed,
    }


def trace_engine(module: types.ModuleType) -> Any:
    class Traced(module.Engine):  # type: ignore[name-defined, misc]
        def __init__(self) -> None:
            super().__init__()
            self.qnodes = 0
            self.considered = 0
            self.searched = 0
            self.maximum_optional = 0
            self.stack: list[tuple[int, int, bool, int]] = []
            self.edges: list[dict[str, Any]] = []
            self.lists: list[tuple[str, list[str]]] = []

        def order(
            self,
            board: chess.Board,
            moves: list[chess.Move],
            preferred: chess.Move | None,
            ply: int,
        ) -> list[chess.Move]:
            if self.stack:
                self.lists.append((board.fen(), [m.uci() for m in moves]))
                if not board.is_check():
                    self.considered += sum(
                        not board.is_capture(m) and not m.promotion and board.gives_check(m)
                        for m in moves
                    )
            return cast(list[chess.Move], super().order(board, moves, preferred, ply))

        def quiesce(
            self, board: chess.Board, alpha: int, beta: int, ply: int, quiet_checks_left: int = 1
        ) -> int:
            self.qnodes += 1
            optional = 0
            if self.stack:
                parent_len, budget, parent_check, optional = self.stack[-1]
                assert len(board.move_stack) == parent_len + 1
                move = board.peek()
                checking = board.is_check()
                board.pop()
                quiet = not board.is_capture(move) and not move.promotion
                parent_fen = board.fen()
                assert move in board.legal_moves
                board.push(move)
                if quiet and checking:
                    self.searched += 1
                    optional += int(not parent_check)
                assert quiet_checks_left <= budget
                if module.QUIET_CHECKS:
                    assert quiet_checks_left == (0 if quiet and checking else budget)
                    assert optional <= 1
                self.maximum_optional = max(self.maximum_optional, optional)
                self.edges.append(
                    {
                        "fen": parent_fen,
                        "move": move.uci(),
                        "quiet": quiet,
                        "check": checking,
                        "evasion": parent_check,
                        "before": budget,
                        "after": quiet_checks_left,
                    }
                )
            self.stack.append(
                (len(board.move_stack), quiet_checks_left, board.is_check(), optional)
            )
            try:
                return int(super().quiesce(board, alpha, beta, ply, quiet_checks_left))
            finally:
                self.stack.pop()

    return Traced()


def targeted() -> None:
    modules = engines()
    _control, candidate = modules.values()
    fixture = json.loads(FIXTURE.read_text())
    fen = fixture["round30_before_bf4"]
    result = {name: fixed(m, fen, 3) for name, m in modules.items()}
    assert result["control"]["move"] == "g5f4"
    assert result["control"]["scores"]["g5f4"] == 31
    assert result["candidate"]["move"] != "g5f4"
    board = chess.Board(fen)
    for san in ("Bf4", "Bxd4", "Qxd4"):
        board.push_san(san)
    tested = trace_engine(candidate)
    tested.deadline = float("inf")
    tested.enter(board)
    before = (board.fen(), list(board.move_stack), tested.seen.copy(), tested.context)
    value = tested.quiesce(board, -candidate.INF, candidate.INF, 3, 1)
    assert before == (board.fen(), list(board.move_stack), tested.seen, tested.context)
    assert any(e["move"] == "d8a5" and e["fen"] == board.fen() for e in tested.edges)
    continuation = board.copy()
    for san in ("Qa5+", "Qc3", "Qxb5"):
        expected = continuation.parse_san(san)
        assert any(
            e["fen"] == continuation.fen() and e["move"] == expected.uci() for e in tested.edges
        ), san
        continuation.push_san(san)
    full_bf4 = {}
    alternative = {}
    for name, m in modules.items():
        b = chess.Board(fen)
        b.push_uci("g5f4")
        engine = m.Engine()
        engine.deadline = float("inf")
        engine.pattern = m.recognise(chess.Board(fen), m.evaluate(chess.Board(fen)))
        engine.enter(b)
        full_bf4[name] = -engine.search(b, 2, -m.INF, m.INF, 1)
        b = chess.Board(fen)
        b.push_uci(result["candidate"]["move"])
        engine = m.Engine()
        engine.deadline = float("inf")
        engine.enter(b)
        alternative[name] = -engine.search(b, 3, -m.INF, m.INF, 1)
    assert full_bf4["candidate"] < -250
    assert alternative["control"] > -150 and alternative["candidate"] > -150
    print(
        "TACTIC "
        + json.dumps(
            {
                "depth3": result,
                "bf4_full_window": full_bf4,
                "alternative_depth4": alternative,
                "qscore": value,
                "qnodes": tested.qnodes,
                "quiet_searched": tested.searched,
                "continuation": "Bf4 Bxd4 Qxd4 Qa5+ Qc3 Qxb5",
            }
        ),
        flush=True,
    )
    cases = {
        "multiple_evasions": "4k3/8/8/8/8/8/8/R3K3 w - - 0 1",
        "capture_check": "k7/8/8/8/8/8/p7/R3K3 w - - 0 1",
        "promotion_check": "k7/6P1/8/8/8/8/8/K7 w - - 0 1",
        "already_checked": "4k3/8/8/8/8/8/8/K3R3 b - - 0 1",
        "pinned": "4r1k1/8/8/8/8/8/4R3/4K3 w - - 0 1",
        "near_max": "4k3/8/8/8/8/8/8/R3K3 w - - 0 1",
        "fifty": "4k3/8/8/8/8/8/8/R3K3 w - - 100 1",
        "repetition": "4k3/8/8/8/8/8/8/R3K3 w - - 0 1",
        "forced_countercheck": "4r3/8/8/1B5k/8/8/8/4K3 w - - 0 1",
    }
    details = {}
    for name, position in cases.items():
        b = chess.Board(position)
        assert b.is_valid(), name
        engine = trace_engine(candidate)
        engine.deadline = float("inf")
        for _ in range(3 if name == "repetition" else 1):
            engine.enter(b)
        snapshot = (b.fen(), engine.seen.copy(), engine.context, engine.duplicates)
        ply = candidate.MAX_PLY - 1 if name == "near_max" else 7
        score = engine.quiesce(
            b, -candidate.INF, candidate.INF, ply, 0 if name == "forced_countercheck" else 1
        )
        assert snapshot == (b.fen(), engine.seen, engine.context, engine.duplicates)
        if name in ("fifty", "repetition"):
            assert score == 0 and engine.qnodes == 1
        if name == "already_checked":
            assert set(engine.lists[0][1]) == {m.uci() for m in b.legal_moves}
        if name == "forced_countercheck":
            assert any(
                e["move"] == "b5e2" and e["after"] == 0 and e["evasion"] for e in engine.edges
            )
        if name in ("capture_check", "promotion_check"):
            assert any(e["check"] and not e["quiet"] and e["after"] == 1 for e in engine.edges)
        if name == "multiple_evasions":
            assert any(chess.Board(f).is_check() and len(moves) > 1 for f, moves in engine.lists)
        if name == "pinned":
            pseudo = {
                m.uci() for m in b.pseudo_legal_moves if b.gives_check(m) and not b.is_legal(m)
            }
            assert pseudo and not pseudo.intersection(engine.lists[0][1])
        details[name] = {"nodes": engine.qnodes, "maximum_optional": engine.maximum_optional}
    # The first q-move captures the bishop; the later optional check must remain available.
    b = chess.Board(fen)
    b.push_san("Bf4")
    engine = trace_engine(candidate)
    engine.deadline = float("inf")
    engine.enter(b)
    engine.quiesce(b, -candidate.INF, candidate.INF, 3, 1)
    assert any(e["move"] == "d8a5" and e["before"] == 1 for e in engine.edges)
    interrupted = trace_engine(candidate)
    interrupted.deadline = 0.0
    interrupted.enter(b)
    snapshot = (b.fen(), list(b.move_stack), interrupted.seen.copy(), interrupted.context)
    try:
        interrupted.quiesce(b, -candidate.INF, candidate.INF, 3, 1)
    except candidate.Deadline:
        pass
    else:
        raise AssertionError("Deadline fixture did not interrupt quiescence")
    assert snapshot == (b.fen(), list(b.move_stack), interrupted.seen, interrupted.context)
    assert not interrupted.stack
    print("INVARIANTS " + json.dumps(details), flush=True)


def benchmark(repeats: int) -> None:
    modules = engines()
    rows = []
    for repeat in range(repeats):
        for index, fen in enumerate(numeric.suite()):
            for name in tuple(modules) if (repeat + index) % 2 else tuple(reversed(modules)):
                m = modules[name]
                depth2 = fixed(m, fen, 2)
                numeric.fresh(m)
                started = time.perf_counter()
                move = m.get_move(fen, 10000)
                seconds = time.perf_counter() - started
                assert seconds < 10 and chess.Move.from_uci(move) in chess.Board(fen).legal_moves
                row = {
                    "repeat": repeat,
                    "position": index,
                    "fen": fen,
                    "name": name,
                    "fixed": depth2,
                    "move": move,
                    "seconds": seconds,
                    "nodes": m._engine.nodes,
                    "nps": m._engine.nodes / seconds,
                    "depth": m._engine.stats["depth"],
                    "model_fraction": m._engine.stats["model_seconds"] / seconds,
                    "hard_slack": m._engine.deadline - started - seconds,
                    "rss_mb": resource.getrusage(0).ru_maxrss / 1024,
                }
                rows.append(row)
                print("BENCH " + json.dumps(row), flush=True)
    summary = {}
    for name in modules:
        own = [r for r in rows if r["name"] == name]
        summary[name] = {
            k: {
                "median": statistics.median(r[k] for r in own),
                "p95": percentile([r[k] for r in own], 0.95),
                "worst": max(r[k] for r in own),
                "mean": statistics.mean(r[k] for r in own),
            }
            for k in ("seconds", "nodes", "nps", "depth", "model_fraction", "rss_mb")
        }
    print("SUMMARY " + json.dumps(summary), flush=True)


def counters(timed: bool = False) -> None:
    targets = (
        [json.loads(line[6:]) for line in sys.stdin if line.startswith("BENCH ")] if timed else []
    )
    for name, source in sources().items():
        tree = ast.parse(source)
        engine = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Engine")
        qsearch = next(
            n for n in engine.body if isinstance(n, ast.FunctionDef) and n.name == "quiesce"
        )

        class Instrument(ast.NodeTransformer):
            def visit_Call(self, node: ast.Call) -> ast.AST:
                if isinstance(node.func, ast.Attribute) and node.func.attr == "gives_check":
                    return ast.copy_location(
                        ast.Call(
                            func=ast.Name(id="_count_check", ctx=ast.Load()),
                            args=[node.func.value, *node.args],
                            keywords=[],
                        ),
                        node,
                    )
                return self.generic_visit(node)

        Instrument().visit(qsearch)
        qsearch.body[0:0] = ast.parse("_qcounts[0] += 1").body
        loop = next(n for n in qsearch.body if isinstance(n, ast.For))
        insertion = next(
            i
            for i, n in enumerate(loop.body)
            if isinstance(n, ast.Expr)
            and isinstance(n.value, ast.Call)
            and isinstance(n.value.func, ast.Attribute)
            and n.value.func.attr == "push"
        )
        loop.body[insertion:insertion] = ast.parse(
            "if not board.is_capture(move) and not move.promotion and board.gives_check(move):\n"
            "    _qcounts[2] += 1\n"
        ).body
        instrumented = ast.unparse(ast.fix_missing_locations(tree)) + (
            "\n_qcounts = [0, 0, 0]\n"
            "def _count_check(board, move):\n"
            "    _qcounts[1] += 1\n"
            "    return board.gives_check(move)\n"
        )
        m = load("counted_" + name, instrumented)
        if timed:
            for target in targets:
                if target["name"] != name:
                    continue
                numeric.fresh(m)
                m._qcounts[:] = [0, 0, 0]
                if target["hard_slack"] > 0:
                    numeric.fresh(m, int(target["depth"]))
                    vars(m)["time"] = types.SimpleNamespace(perf_counter=lambda: 0.0)
                else:
                    vars(m)["time"] = types.SimpleNamespace(
                        perf_counter=lambda m=m, target=target: (
                            1e10 if m._engine.nodes >= target["nodes"] else 0.0
                        )
                    )
                move = m.get_move(target["fen"], 10000)
                assert m._engine.nodes == target["nodes"], (
                    name,
                    target["position"],
                    m._engine.nodes,
                    target["nodes"],
                )
                assert move == target["move"] and m._engine.stats["depth"] == target["depth"]
                print(
                    "TIMED_COUNTERS "
                    + json.dumps(
                        {
                            "name": name,
                            "position": target["position"],
                            "repeat": target["repeat"],
                            "nodes": m._engine.nodes,
                            "qnodes": m._qcounts[0],
                            "quiet_check_tests": m._qcounts[1],
                            "quiet_checks_searched": m._qcounts[2],
                        }
                    ),
                    flush=True,
                )
            continue
        for index, fen in enumerate(numeric.suite()):
            m._qcounts[:] = [0, 0, 0]
            row = fixed(m, fen, 2)
            print(
                "COUNTERS "
                + json.dumps(
                    {
                        "name": name,
                        "position": index,
                        "qnodes": m._qcounts[0],
                        "quiet_check_tests": m._qcounts[1],
                        "quiet_checks_searched": m._qcounts[2],
                        "nodes": row["nodes"],
                    }
                ),
                flush=True,
            )


def replay(cold: bool) -> None:
    fixture = json.loads(FIXTURE.read_text())
    for name, m in engines().items():
        numeric.fresh(m)
        records = []
        for call in fixture["calls"][-1:] if cold else fixture["calls"]:
            move = m.get_move(call["fen"], call["clock_ms"])
            assert chess.Move.from_uci(move) in chess.Board(call["fen"]).legal_moves
            records.append(
                {
                    "move": move,
                    "expected_historical": call["expected"],
                    "age": m._engine.age,
                    **m._engine.stats,
                }
            )
        print("REPLAY " + json.dumps({"name": name, "cold": cold, "records": records}), flush=True)


def initialization(repeats: int) -> None:
    code = (
        "import time,resource,json; started=time.perf_counter(); import agent; "
        "print(json.dumps({'seconds':time.perf_counter()-started,"
        "'rss_mb':resource.getrusage(0).ru_maxrss/1024}))"
    )
    with tempfile.TemporaryDirectory() as temporary:
        directory = Path(temporary)
        for repeat in range(repeats):
            for name, source in sources().items():
                (directory / "agent.py").write_text(source)
                env = os.environ | {"PYTHONPATH": str(directory)}
                output = subprocess.check_output(
                    [sys.executable, "-c", code], cwd=directory, env=env, text=True, timeout=90
                )
                print(
                    "FRESH_INIT "
                    + json.dumps({"name": name, "repeat": repeat, **json.loads(output)}),
                    flush=True,
                )


def equality() -> None:
    modules = engines()
    baseline, candidate = modules.values()
    trees = [ast.parse(source) for source in sources().values()]
    for name in (
        "numeric_evaluate",
        "numeric_attacks",
        "compiled_evaluate",
        "cached_evaluate",
        "OpponentModel",
        "adaptive_choice",
        "recognise",
        "get_move",
    ):
        nodes = [next(n for n in t.body if getattr(n, "name", "") == name) for t in trees]
        assert ast.dump(nodes[0]) == ast.dump(nodes[1]), name
    before = numeric.signatures(candidate)
    classes = [
        next(n for n in t.body if isinstance(n, ast.ClassDef) and n.name == "Engine") for t in trees
    ]
    for method in classes[0].body:
        if isinstance(method, ast.FunctionDef) and method.name not in ("quiesce", "search"):
            other = next(
                n
                for n in classes[1].body
                if isinstance(n, ast.FunctionDef) and n.name == method.name
            )
            assert ast.dump(method) == ast.dump(other), method.name
    import random

    rng = random.Random(39017)
    board = chess.Board()
    historical = load("historical_oracle", numeric.sources()["submitted"])
    for index in range(1200):
        if board.is_game_over() or index % 120 == 0:
            board = chess.Board()
        board.push(rng.choice(list(board.legal_moves)))
        for b in (board, board.mirror()):
            assert candidate.compiled_evaluate(b) == historical.fast_evaluate(b)
            assert candidate.evaluate(b) == baseline.evaluate(b)
            assert candidate.evaluate(b) == candidate.evaluate(b.mirror())
    vars(candidate)["QUIET_CHECKS"] = False
    for fen in numeric.suite():
        first, second = fixed(baseline, fen, 2), fixed(candidate, fen, 2)
        assert all(first[k] == second[k] for k in ("move", "scores", "nodes"))
    vars(candidate)["QUIET_CHECKS"] = True
    for fen in numeric.suite():
        numeric.fresh(candidate)
        candidate.get_move(fen, 100)
    assert numeric.signatures(candidate) == before
    print(
        'EQUALITY {"exact_evaluations": 2400, "disabled_toggle_fixed_equivalence": 24, '
        '"stable_signatures": true}',
        flush=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "mode",
        choices=(
            "targeted",
            "bench",
            "replay",
            "cold",
            "equality",
            "arena",
            "counters",
            "passive",
            "timing",
            "timed-counters",
            "init",
        ),
    )
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--cases", type=int, default=20)
    parser.add_argument("--case-offset", type=int, default=0)
    parser.add_argument("--seed", type=int, default=51000)
    parser.add_argument("--base-ms", type=int, default=10000)
    parser.add_argument("--increment-ms", type=int, default=100)
    args = parser.parse_args()
    if args.mode == "targeted":
        targeted()
    elif args.mode == "bench":
        benchmark(args.repeats)
    elif args.mode in ("replay", "cold"):
        replay(args.mode == "cold")
    elif args.mode == "equality":
        equality()
    elif args.mode == "counters":
        counters()
    elif args.mode == "timed-counters":
        counters(True)
    elif args.mode == "init":
        initialization(args.repeats)
    elif args.mode == "passive":
        import passive_units

        passive_units.units()
        passive_units.invariants()
        passive_units.lifecycle()
        print('PASSIVE {"bayesian": true, "safe_set_cases": 93, "lifecycle": true}')
    elif args.mode == "timing":
        import timing

        vars(timing)["sources"] = sources
        sys.argv = [
            sys.argv[0],
            "--configs",
            "control,candidate",
            "--clock",
            "10000",
            "--positions",
            "24",
            "--repeats",
            str(args.repeats),
        ]
        timing.main()
    else:
        corpus = json.loads(Path("tests/quiet_openings.json").read_text())
        vars(selection)["OPENINGS"] = tuple(tuple(r["uci"]) for r in corpus["positions"])
        selection.sources = lambda: {**sources(), "reference": sources()["control"]}
        args.configs = "control,candidate"
        args.sparring = True
        args.spread_openings = False
        args.games = 0
        assert args.cases % 2 == 0 and args.case_offset % 2 == 0
        selection.arena(args)


if __name__ == "__main__":
    main()
