"""Isolated quiescence move-generation experiment: one generator, identical search."""

import argparse
import ast
import hashlib
import json
import random
import resource
import statistics
import sys
import time
import types
from pathlib import Path
from typing import Any, cast

import chess
import numba_validation as numeric
import selection
from passive_arena import percentile
from selection import load, positions

CONTROL_SHA = "59f99079f1db99221683dd3f06391f4fc502c1dae11fb712b08170242649830a"
CONTROL = Path("tests/numba_checkpoint") / CONTROL_SHA / "agent.py"
MANIFEST = Path("tests/qgen_experiment.json")
FLAGS = ("ADAPTIVE", "PASSIVE", "FAST_EVAL", "EVAL_CACHE", "DEPTH_EVIDENCE", "TIGHT_ROOT")


def sources() -> dict[str, str]:
    raw = CONTROL.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == CONTROL_SHA
    manifest = json.loads(MANIFEST.read_text())
    assert manifest["control_sha256"] == CONTROL_SHA
    candidate = Path(manifest["candidate_path"]).read_bytes()
    assert hashlib.sha256(candidate).hexdigest() == manifest["candidate_sha256"]
    return {"control": raw.decode(), "candidate": candidate.decode()}


def engines() -> dict[str, types.ModuleType]:
    result = {}
    for name, source in sources().items():
        started = time.perf_counter()
        result[name] = load("qgen_" + name, source)
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
    """Record every quiescence move list, in the order quiescence consumes them."""

    class Traced(module.Engine):  # type: ignore[name-defined, misc]
        def __init__(self) -> None:
            super().__init__()
            self.qdepth = 0
            self.qnodes = 0
            self.lists: list[tuple[str, list[str]]] = []

        def order(
            self,
            board: chess.Board,
            moves: list[chess.Move],
            preferred: chess.Move | None,
            ply: int,
        ) -> list[chess.Move]:
            if self.qdepth:
                self.lists.append((board.fen(), [m.uci() for m in moves]))
            return cast(list[chess.Move], super().order(board, moves, preferred, ply))

        def quiesce(self, board: chess.Board, alpha: int, beta: int, ply: int) -> int:
            self.qnodes += 1
            self.qdepth += 1
            try:
                return int(super().quiesce(board, alpha, beta, ply))
            finally:
                self.qdepth -= 1

    return Traced()


def state(engine: Any, board: chess.Board) -> tuple[Any, ...]:
    return (
        board.fen(),
        list(board.move_stack),
        dict(engine.seen),
        engine.context,
        engine.duplicates,
    )


def probe(
    module: types.ModuleType, board: chess.Board, alpha: int, beta: int, ply: int, tactical: bool
) -> tuple[int, int, list[tuple[str, list[str]]]]:
    """Run one quiescence tree and assert the board and repetition state are restored."""
    engine = trace_engine(module)
    engine.deadline = float("inf")
    if tactical:
        engine.pattern = module.recognise(board, module.evaluate(board))
    engine.enter(board)
    before = state(engine, board)
    score = engine.quiesce(board, alpha, beta, ply)
    assert before == state(engine, board)
    return score, engine.qnodes, engine.lists


def corpus() -> list[str]:
    """Reachable positions plus the fixed suite; quiescence needs tactical material."""
    result = positions(120, 941) + positions(60, 702) + numeric.suite()
    return sorted(set(result))


def targeted() -> None:
    modules = engines()
    control, candidate = modules["control"], modules["candidate"]
    assert candidate.PASSIVE is True and candidate.ADAPTIVE is False
    for flag in FLAGS:
        assert getattr(control, flag) == getattr(candidate, flag), flag

    matched = 0
    for fen in corpus():
        for ply, tactical in ((0, False), (2, True), (5, True)):
            board = chess.Board(fen)
            outcomes = [
                probe(m, board, -m.INF, m.INF, ply, tactical) for m in (control, candidate)
            ]
            assert outcomes[0] == outcomes[1], (fen, ply)
            matched += 1
    # Narrow windows exercise the stand pat cutoff, which must not generate a move list.
    cutoffs = 0
    for fen in corpus()[:60]:
        board = chess.Board(fen)
        stand = candidate.evaluate(board)
        for beta in (stand, stand - 40, stand + 40):
            outcomes = [probe(m, board, beta - 1, beta, 1, False) for m in (control, candidate)]
            assert outcomes[0] == outcomes[1], (fen, beta)
            cutoffs += int(not board.is_check() and not outcomes[1][2])

    # The move the existence probe consumes must still reach the forcing scan.
    preserved = 0
    only = 0
    for fen in corpus():
        board = chess.Board(fen)
        if board.is_check():
            continue
        first = next(board.generate_legal_moves(), None)
        assert first is not None
        if not (board.is_capture(first) or first.promotion):
            continue
        _, _, lists = probe(candidate, board, -candidate.INF, candidate.INF, 5, False)
        assert lists and lists[0][0] == board.fen()
        assert first.uci() in lists[0][1], (fen, first.uci(), lists[0][1])
        preserved += 1
        only += int(lists[0][1] == [first.uci()])
    assert preserved >= 20 and only >= 1, (preserved, only)

    cases = {
        "checkmate": ("7k/6Q1/6K1/8/8/8/8/8 b - - 0 1", 7),
        "checkmate_at_fifty": ("7k/6Q1/6K1/8/8/8/8/8 b - - 100 1", 7),
        "stalemate": ("7k/5Q2/6K1/8/8/8/8/8 b - - 0 1", 7),
        "stalemate_at_max_ply": ("7k/5Q2/6K1/8/8/8/8/8 b - - 0 1", None),
        "fifty": ("7k/8/8/8/8/8/R7/7K w - - 100 1", 7),
        "fifty_at_max_ply": ("7k/8/8/8/8/8/R7/7K w - - 100 1", None),
        "insufficient": ("7k/8/8/8/8/8/8/6BK w - - 0 1", 7),
        "in_check": ("4k3/8/8/8/8/8/4r3/4K3 w - - 0 1", 7),
        "max_ply_quiet": ("4k3/8/8/8/8/8/8/R3K3 w - - 0 1", None),
    }
    details = {}
    for name, (fen, given) in cases.items():
        board = chess.Board(fen)
        assert board.is_valid(), name
        ply = candidate.MAX_PLY if given is None else given
        outcomes = [probe(m, board, -m.INF, m.INF, ply, False) for m in (control, candidate)]
        assert outcomes[0] == outcomes[1], name
        score, qnodes, lists = outcomes[1]
        if name.startswith("checkmate"):
            assert score == -candidate.MATE + ply and qnodes == 1, name
        if name.startswith(("stalemate", "fifty", "insufficient")):
            assert score == 0 and qnodes == 1, name
        if name == "max_ply_quiet":
            assert score == candidate.evaluate(board) and qnodes == 1
        if name == "in_check":
            assert set(lists[0][1]) == {m.uci() for m in board.legal_moves}
        details[name] = {"score": score, "qnodes": qnodes}
    # A stalemate at the ply cap must still score zero rather than fall through to evaluate.
    assert candidate.evaluate(chess.Board(cases["stalemate"][0])) != 0

    # Threefold repetition inside quiescence, reached through the engine's own seen counter.
    board = chess.Board("4k3/8/8/8/8/8/8/R3K3 w - - 0 1")
    for m in (control, candidate):
        engine = trace_engine(m)
        engine.deadline = float("inf")
        for _ in range(3):
            engine.enter(board)
        before = state(engine, board)
        assert engine.quiesce(board, -m.INF, m.INF, 7) == 0 and engine.qnodes == 1
        assert before == state(engine, board)

    # A deadline raised mid-tree must leave the board and repetition state untouched.
    sharp = chess.Board("r1bqk2r/pp2n1pp/1bn2p2/1B1p2B1/3N4/8/PPP2PPP/R2QK1NR w KQkq - 0 10")
    for m in (control, candidate):
        engine = trace_engine(m)
        engine.deadline = 0.0
        engine.enter(sharp)
        before = state(engine, sharp)
        try:
            engine.quiesce(sharp, -m.INF, m.INF, 1)
        except m.Deadline:
            pass
        else:
            raise AssertionError("Deadline fixture did not interrupt quiescence")
        assert before == state(engine, sharp)
        assert engine.qdepth == 0

    print(
        "INVARIANTS "
        + json.dumps(
            {
                "positions": len(corpus()),
                "matched_trees": matched,
                "stand_pat_cutoffs": cutoffs,
                "first_move_preserved": preserved,
                "first_move_only_forcing": only,
                "cases": details,
            }
        ),
        flush=True,
    )


def equality() -> None:
    modules = engines()
    control, candidate = modules["control"], modules["candidate"]
    trees = [ast.parse(source) for source in sources().values()]
    for node in trees[0].body:
        name = getattr(node, "name", "")
        if not isinstance(node, ast.ClassDef | ast.FunctionDef):
            continue
        other = next(n for n in trees[1].body if getattr(n, "name", "") == name)
        if name != "Engine":
            assert ast.dump(node) == ast.dump(other), name
            continue
        # quiesce is the whole experiment; every other Engine method must be identical.
        methods = [
            {n.name: n for n in cls.body if isinstance(n, ast.FunctionDef)}
            for cls in (node, cast(ast.ClassDef, other))
        ]
        assert set(methods[0]) == set(methods[1])
        for method in methods[0]:
            if method == "quiesce":
                continue
            assert ast.dump(methods[0][method]) == ast.dump(methods[1][method]), method
    before = numeric.signatures(candidate)
    assert all(before.values()) and len(before["numeric_evaluate"]) == 1

    historical = load("qgen_oracle", numeric.sources()["submitted"])
    rng = random.Random(39017)
    board = chess.Board()
    count = 0
    for index in range(1200):
        if board.is_game_over() or index % 120 == 0:
            board = chess.Board()
        board.push(rng.choice(list(board.legal_moves)))
        for tested in (board, board.mirror()):
            assert candidate.compiled_evaluate(tested) == historical.fast_evaluate(tested)
            assert candidate.evaluate(tested) == control.evaluate(tested)
            assert candidate.evaluate(tested) == candidate.evaluate(tested.mirror())
            count += 1

    rows = []
    for index, fen in enumerate(numeric.suite()):
        first, second = fixed(control, fen, 2), fixed(candidate, fen, 2)
        assert all(first[k] == second[k] for k in ("move", "scores", "nodes")), fen
        rows.append(
            {
                "position": index,
                "move": second["move"],
                "nodes": second["nodes"],
                "root_scores": len(second["scores"]),
                "control_ms": first["seconds"] * 1000,
                "candidate_ms": second["seconds"] * 1000,
            }
        )
        print("FIXED " + json.dumps(rows[-1]), flush=True)
    for clock in (1, 10, 50, 100, 500):
        for fen in numeric.suite()[:4]:
            numeric.fresh(candidate)
            started = time.perf_counter()
            move = candidate.get_move(fen, clock)
            assert (time.perf_counter() - started) * 1000 < clock
            assert chess.Move.from_uci(move) in chess.Board(fen).legal_moves
    assert numeric.signatures(candidate) == before
    print(
        "EQUALITY "
        + json.dumps(
            {
                "exact_evaluations": count,
                "fixed_depth_positions": len(rows),
                "moves_scores_nodes_exact": True,
                "signatures": before,
                "sha256": {n: hashlib.sha256(s.encode()).hexdigest() for n, s in sources().items()},
            }
        ),
        flush=True,
    )


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
    summary: dict[str, Any] = {}
    for name in modules:
        own = [r for r in rows if r["name"] == name]
        summary[name] = {
            key: {
                "median": statistics.median(r[key] for r in own),
                "p95": percentile([r[key] for r in own], 0.95),
                "worst": max(r[key] for r in own),
                "mean": statistics.mean(r[key] for r in own),
            }
            for key in ("seconds", "nodes", "nps", "depth", "model_fraction", "rss_mb")
        }
        fixed_rows = [cast(dict[str, float], r["fixed"]) for r in own]
        summary[name]["fixed_depth"] = {
            key: {
                "median": statistics.median(r[key] for r in fixed_rows),
                "p95": percentile([r[key] for r in fixed_rows], 0.95),
                "worst": max(r[key] for r in fixed_rows),
                "mean": statistics.mean(r[key] for r in fixed_rows),
            }
            for key in ("seconds", "nodes", "nps")
        }
    gains = {}
    for key, scale in (("nps", 1), ("seconds", -1)):
        control = [r[key] for r in rows if r["name"] == "control"]
        candidate = [r[key] for r in rows if r["name"] == "candidate"]
        ratio = statistics.median(candidate) / statistics.median(control)
        gains["timed_" + key] = (ratio - 1) * 100 * scale
    fixed_control = [cast(dict[str, float], r["fixed"]) for r in rows if r["name"] == "control"]
    fixed_candidate = [cast(dict[str, float], r["fixed"]) for r in rows if r["name"] == "candidate"]
    for key, scale in (("nps", 1), ("seconds", -1)):
        ratio = statistics.median(r[key] for r in fixed_candidate) / statistics.median(
            r[key] for r in fixed_control
        )
        gains["fixed_" + key] = (ratio - 1) * 100 * scale
    print("SUMMARY " + json.dumps({"results": summary, "median_gain_percent": gains}), flush=True)


def report() -> None:
    """Per-position paired statistics merged from a benchmark log on standard input."""
    rows = [json.loads(line[6:]) for line in sys.stdin if line.startswith("BENCH ")]
    assert rows, "no BENCH rows on standard input"
    per_position = []
    for index in sorted({r["position"] for r in rows}):
        entry: dict[str, Any] = {"position": index}
        for name in ("control", "candidate"):
            own = [r for r in rows if r["position"] == index and r["name"] == name]
            entry[name] = {
                "fixed_nps": statistics.median(r["fixed"]["nps"] for r in own),
                "fixed_ms": statistics.median(r["fixed"]["seconds"] * 1000 for r in own),
                "fixed_nodes": statistics.median(r["fixed"]["nodes"] for r in own),
                "timed_nps": statistics.median(r["nps"] for r in own),
                "mean_depth": statistics.mean(r["depth"] for r in own),
                "worst_ms": max(r["seconds"] * 1000 for r in own),
            }
        entry["fixed_nps_gain"] = (
            entry["candidate"]["fixed_nps"] / entry["control"]["fixed_nps"] - 1
        ) * 100
        entry["timed_nps_gain"] = (
            entry["candidate"]["timed_nps"] / entry["control"]["timed_nps"] - 1
        ) * 100
        entry["depth_change"] = entry["candidate"]["mean_depth"] - entry["control"]["mean_depth"]
        entry["nodes_match"] = entry["candidate"]["fixed_nodes"] == entry["control"]["fixed_nodes"]
        per_position.append(entry)
    aggregate = {
        "positions": len(per_position),
        "median_fixed_nps_gain": statistics.median(r["fixed_nps_gain"] for r in per_position),
        "median_timed_nps_gain": statistics.median(r["timed_nps_gain"] for r in per_position),
        "fixed_nps_regressions": [
            r["position"] for r in per_position if r["fixed_nps_gain"] < 0
        ],
        "timed_nps_regressions": [
            r["position"] for r in per_position if r["timed_nps_gain"] < 0
        ],
        "depth_regressions": [r["position"] for r in per_position if r["depth_change"] < 0],
        "fixed_nodes_identical": all(r["nodes_match"] for r in per_position),
    }
    for name in ("control", "candidate"):
        own = [r for r in rows if r["name"] == name]
        aggregate[name] = {
            "mean_completed_depth": statistics.mean(r["depth"] for r in own),
            "median_fixed_ms": statistics.median(r["fixed"]["seconds"] * 1000 for r in own),
            "p95_fixed_ms": percentile([r["fixed"]["seconds"] * 1000 for r in own], 0.95),
            "median_timed_ms": statistics.median(r["seconds"] * 1000 for r in own),
            "worst_timed_ms": max(r["seconds"] * 1000 for r in own),
            "minimum_hard_slack_s": min(r["hard_slack"] for r in own),
        }
    print(json.dumps({"aggregate": aggregate, "per_position": per_position}, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "mode",
        choices=("targeted", "equality", "bench", "report", "passive", "timing", "arena"),
    )
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--cases", type=int, default=10)
    parser.add_argument("--case-offset", type=int, default=0)
    parser.add_argument("--seed", type=int, default=51000)
    parser.add_argument("--base-ms", type=int, default=10000)
    parser.add_argument("--increment-ms", type=int, default=100)
    args = parser.parse_args()
    if args.mode == "targeted":
        targeted()
    elif args.mode == "equality":
        equality()
    elif args.mode == "bench":
        benchmark(args.repeats)
    elif args.mode == "report":
        report()
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
        corpus_json = json.loads(Path("tests/quiet_openings.json").read_text())
        vars(selection)["OPENINGS"] = tuple(tuple(r["uci"]) for r in corpus_json["positions"])
        selection.sources = lambda: {**sources(), "reference": sources()["control"]}
        args.configs = "control,candidate"
        args.sparring = True
        args.spread_openings = False
        args.games = 0
        assert args.cases % 2 == 0 and args.case_offset % 2 == 0
        selection.arena(args)


if __name__ == "__main__":
    main()
