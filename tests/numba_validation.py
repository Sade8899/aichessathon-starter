"""Exact submitted-source A/B, real-interface benchmarks and official-referee games."""

import argparse
import ast
import cProfile
import hashlib
import json
import random
import statistics
import time
import types
from pathlib import Path
from typing import Any

import chess
import selection
from passive_arena import percentile
from selection import fixed_engine, load, positions

MANIFEST = json.loads(Path("tests/tournament/submitted.json").read_text())


def sources() -> dict[str, str]:
    frozen = Path(MANIFEST["frozen_path"]).read_bytes()
    assert hashlib.sha256(frozen).hexdigest() == MANIFEST["agent_sha256"]
    return {"submitted": frozen.decode(), "numba": Path("agent.py").read_text()}


def modules() -> dict[str, types.ModuleType]:
    result, initialization = {}, {}
    for name, source in sources().items():
        started = time.perf_counter()
        result[name] = load("numeric_" + name, source)
        initialization[name] = time.perf_counter() - started
    print("INITIALIZATION " + json.dumps(initialization), flush=True)
    assert max(initialization.values()) < 90
    return result


def signatures(module: types.ModuleType) -> dict[str, tuple[str, ...]]:
    return {
        name: tuple(map(str, getattr(module, name).signatures))
        for name in ("bit_count", "first_square", "numeric_attacks", "numeric_evaluate")
    }


def fresh(module: types.ModuleType, depth: int | None = None) -> None:
    vars(module)["_engine"] = module.Engine() if depth is None else fixed_engine(module, depth)
    module._eval_table[:] = [None] * len(module._eval_table)


def suite() -> list[str]:
    result = positions(20)
    result += [
        "8/5pk1/6p1/3p4/3P4/5KP1/5P2/8 w - - 0 40",
        "4r1k1/5pp1/7p/8/8/5P2/5KPP/4R3 b - - 0 35",
        "rnbqk1nr/pp3ppp/8/2bp4/8/1N6/PPP2PPP/R1BQKBNR b KQkq - 1 6",
        "r1bqk2r/pp2n1pp/1bn2p2/1B1p2B1/3N4/8/PPP2PPP/R2QK1NR w KQkq - 0 10",
    ]
    assert len(result) == 24 and all(chess.Board(f).is_valid() for f in result)
    return result


def check() -> None:
    engines = modules()
    baseline, candidate = engines.values()
    for flag in ("ADAPTIVE", "PASSIVE", "FAST_EVAL", "EVAL_CACHE", "DEPTH_EVIDENCE", "TIGHT_ROOT"):
        assert getattr(baseline, flag) == getattr(candidate, flag), flag
    assert candidate.ADAPTIVE is False
    before = signatures(candidate)
    assert all(before.values()) and len(before["numeric_evaluate"]) == 1
    # Search and model code must remain byte-for-byte equivalent at the AST level.
    trees = [ast.parse(source) for source in sources().values()]
    for name in ("Engine", "OpponentModel", "adaptive_choice", "recognise", "get_move"):
        nodes = [next(n for n in tree.body if getattr(n, "name", "") == name) for tree in trees]
        assert ast.dump(nodes[0]) == ast.dump(nodes[1]), name
    rng = random.Random(39017)
    board = chess.Board()
    count = 0
    unique = set()
    for index in range(1200):
        if board.is_game_over() or index % 120 == 0:
            board = chess.Board()
        board.push(rng.choice(list(board.legal_moves)))
        for tested in (board, board.mirror()):
            unique.add(tested.fen())
            assert candidate.compiled_evaluate(tested) == baseline.fast_evaluate(tested), (
                tested.fen()
            )
            assert candidate.evaluate(tested) == baseline.evaluate(tested)
            # Mirroring swaps the mover too, so side-to-move scores have the same sign.
            assert candidate.evaluate(tested) == candidate.evaluate(tested.mirror())
            count += 1
        if index < 200:
            for square in chess.scan_forward(board.occupied & ~board.pawns):
                actual = candidate.numeric_attacks(
                    board.piece_type_at(square), square, candidate.np.uint64(board.occupied)
                )
                assert int(actual) == board.attacks_mask(square), (board.fen(), square)
    assert len(unique) >= 1000
    for fen in suite():
        outcomes = []
        for module in engines.values():
            fresh(module, 2)
            move = module.get_move(fen, 120000)
            assert module._engine.stats["depth"] == 2
            outcomes.append((move, module._engine.nodes, module._engine.completed_scores))
        assert outcomes[0] == outcomes[1], (fen, outcomes)
    for clock in (1, 10, 50, 100, 500):
        for fen in suite()[:4]:
            fresh(candidate)
            started = time.perf_counter()
            move = candidate.get_move(fen, clock)
            assert (time.perf_counter() - started) * 1000 < clock
            assert chess.Move.from_uci(move) in chess.Board(fen).legal_moves
    assert signatures(candidate) == before
    print(
        json.dumps(
            {
                "evaluations_exact": count,
                "fixed_depth_positions": 24,
                "moves_scores_nodes_exact": True,
                "signatures": before,
                "no_move_time_compilation": True,
            }
        ),
        flush=True,
    )


def summary(values: list[float]) -> dict[str, float]:
    return {
        "median": statistics.median(values),
        "p95": percentile(values, 0.95),
        "mean": statistics.mean(values),
        "worst": max(values),
    }


def benchmark(repeats: int) -> None:
    engines = modules()
    records: dict[str, list[dict[str, Any]]] = {name: [] for name in engines}
    evaluation: dict[str, list[float]] = {name: [] for name in engines}
    fixed: dict[str, list[dict[str, float]]] = {name: [] for name in engines}
    for repeat in range(repeats):
        for index, fen in enumerate(suite()):
            order = list(engines)
            if (index + repeat) % 2:
                order.reverse()
            board = chess.Board(fen)
            for name in order:
                module = engines[name]
                started_ns = time.perf_counter_ns()
                for _ in range(100):
                    module._uncached_evaluate(board)
                evaluation[name].append((time.perf_counter_ns() - started_ns) / 100000)
                fresh(module, 2)
                started = time.perf_counter()
                module.get_move(fen, 120000)
                elapsed = time.perf_counter() - started
                fixed[name].append(
                    {
                        "ms": elapsed * 1000,
                        "nodes": module._engine.nodes,
                        "nps": module._engine.nodes / elapsed,
                    }
                )
                fresh(module)
                started = time.perf_counter()
                move = module.get_move(fen, 10000)
                elapsed = time.perf_counter() - started
                assert chess.Move.from_uci(move) in board.legal_moves and elapsed < 10
                records[name].append(
                    {
                        "fen": fen,
                        "ms": elapsed * 1000,
                        "nodes": module._engine.nodes,
                        "nps": module._engine.nodes / elapsed,
                        "depth": module._engine.stats["depth"],
                    }
                )
    results = {}
    for name in engines:
        results[name] = {
            "evaluation_us": summary(evaluation[name]),
            "fixed_depth": {
                key: summary([r[key] for r in fixed[name]]) for key in ("ms", "nodes", "nps")
            },
            "timed": {
                key: summary([r[key] for r in records[name]])
                for key in ("ms", "nodes", "nps", "depth")
            },
        }
    print("RAW " + json.dumps({"timed": records, "fixed": fixed, "evaluation_us": evaluation}))
    print(
        "SUMMARY "
        + json.dumps(
            {
                "positions": 24,
                "repeats": repeats,
                "results": results,
                "sha256": {n: hashlib.sha256(s.encode()).hexdigest() for n, s in sources().items()},
            }
        ),
        flush=True,
    )


def profile() -> None:
    engines = modules()
    for name, module in engines.items():
        profiler = cProfile.Profile()
        nodes = 0
        for fen in suite():
            fresh(module, 2)
            profiler.enable()
            module.get_move(fen, 120000)
            profiler.disable()
            assert module._engine.stats["depth"] == 2
            nodes += module._engine.nodes
        names = {
            "cached_evaluate",
            "fast_evaluate",
            "compiled_evaluate",
            "quiesce",
            "generate_legal_moves",
            "generate_pseudo_legal_moves",
            "choose",
        }
        data = {
            e.code.co_name: {
                "calls": e.callcount,
                "self_s": e.inlinetime,
                "inclusive_s": e.totaltime,
            }
            for e in profiler.getstats()
            if not isinstance(e.code, str) and e.code.co_name in names
        }
        print(
            "PROFILE " + json.dumps({"config": name, "nodes": nodes, "functions": data}), flush=True
        )


def round30() -> None:
    fixture = json.loads(Path("tests/tournament/fixtures/rated.json").read_text())
    board = chess.Board(fixture["round30_start"])
    calls = []
    for san in fixture["prefix_san"]:
        board.push_san(san)
        if board.turn == chess.WHITE:
            calls.append(board.fen())
    assert calls == [call["fen"] for call in fixture["calls"]]
    for san in fixture["round30_fragment"]:
        board.push_san(san)
    engines = modules()
    records: dict[str, list[dict[str, Any]]] = {}
    for name, module in engines.items():
        records[name] = []
        fresh(module)
        diverged = False
        for index, call in enumerate(fixture["calls"]):
            observed = module._engine.reconstruct(chess.Board(call["fen"]))
            if index and not diverged:
                assert observed is not None
            move = module.get_move(call["fen"], call["clock_ms"])
            assert chess.Move.from_uci(move) in chess.Board(call["fen"]).legal_moves
            assert module._engine.age == index + 1
            diverged |= move != call["expected"]
            records[name].append(
                {
                    "move": move,
                    "expected": call["expected"],
                    "observed": observed,
                    "diverged_game_path": diverged,
                    "age": module._engine.age,
                    "clock_ms": call["clock_ms"],
                    "score_cp": module._engine.completed_scores.get(chess.Move.from_uci(move)),
                    "nps": module._engine.nodes / module._engine.stats["seconds"],
                    **module._engine.stats,
                }
            )
    reproduced = all(r["move"] == r["expected"] for r in records["submitted"])
    print(
        "ROUND30 "
        + json.dumps(
            {
                "records": records,
                "prefix_and_fragment_legal": True,
                "one_persistent_instance_each": True,
                "submitted_reproduced": reproduced,
            }
        ),
        flush=True,
    )
    assert reproduced, "Local timed baseline did not reproduce the supplied rounded-clock trace"


def horizon() -> None:
    fixture = json.loads(Path("tests/tournament/fixtures/rated.json").read_text())
    engines = modules()
    rows = []
    for depth in (3, 4):
        outcomes = []
        for name, module in engines.items():
            # Fixed-depth analysis has no wall-clock cutoff; gameplay keeps its real clock.
            vars(module)["time"] = types.SimpleNamespace(perf_counter=lambda: 0.0)
            fresh(module, depth)
            started = time.perf_counter()
            move = module.get_move(fixture["round30_before_bf4"], 113600)
            elapsed = time.perf_counter() - started
            scores = module._engine.completed_scores
            outcomes.append((move, scores, module._engine.nodes))
            rows.append(
                {
                    "config": name,
                    "depth": depth,
                    "move": move,
                    "score_cp": scores[chess.Move.from_uci(move)],
                    "bf4_score_or_upper_bound": scores[chess.Move.from_uci("g5f4")],
                    "nodes": module._engine.nodes,
                    "seconds": elapsed,
                    "nps": module._engine.nodes / elapsed,
                }
            )
        assert outcomes[0] == outcomes[1]
    print(
        "HORIZON " + json.dumps({"rows": rows, "exact_fixed_depth_equivalence": True}), flush=True
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "mode", choices=("check", "bench", "profile", "arena", "round30", "horizon")
    )
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--cases", type=int, default=20)
    parser.add_argument("--case-offset", type=int, default=0)
    parser.add_argument("--base-ms", type=int, default=10000)
    parser.add_argument("--increment-ms", type=int, default=100)
    parser.add_argument("--seed", type=int, default=39000)
    args = parser.parse_args()
    if args.mode == "check":
        check()
    elif args.mode == "bench":
        benchmark(args.repeats)
    elif args.mode == "profile":
        profile()
    elif args.mode == "round30":
        round30()
    elif args.mode == "horizon":
        horizon()
    else:
        # Each case plays both configurations against the same exact submitted rival.
        # Consecutive cases reverse colours with the same opening and seed.
        frozen = sources()["submitted"]
        selection.sources = lambda: {**sources(), "reference": frozen}
        args.configs = "submitted,numba"
        args.sparring = True
        args.spread_openings = False
        args.games = 0
        assert args.cases % 2 == 0
        assert args.case_offset >= 0 and args.case_offset % 2 == 0
        selection.arena(args)


if __name__ == "__main__":
    main()
