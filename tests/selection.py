"""Independent evaluator ablations and paired official-referee selection."""

import argparse
import cProfile
import hashlib
import importlib.util
import io
import json
import random
import resource
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
import types
from collections import Counter
from pathlib import Path

import chess
import chess.pgn
from passive_arena import OPENINGS, TELEMETRY, TimedAgent, interval, opponent_source, percentile

from harness.referee import FAILED_TERMINATIONS, play_match

ROOT = Path("/workspace")
MEASURED_TELEMETRY = TELEMETRY.replace(
    "data = {'stats': _engine.stats}",
    "data = {'stats': dict(_engine.stats)}\n"
    "    data['stats']['peak_rss_mb'] = __import__('resource').getrusage(0).ru_maxrss / 1024",
)


class MeasuredAgent(TimedAgent):
    def start(self, init_budget_s: float) -> None:
        started = time.perf_counter()
        super().start(init_budget_s)
        self.initialization = time.perf_counter() - started


def sources() -> dict[str, str]:
    current = (ROOT / "agent.py").read_text()
    template_path = ROOT / "tests/experiments/agent.py"
    template = template_path.read_text() if template_path.exists() else current
    reference = (ROOT / "tests/passive_checkpoint/agent.py").read_text()

    def variant(fast: bool, cache: bool, passive: bool = True) -> str:
        result = template
        for key, value in {
            "FAST_EVAL": fast,
            "EVAL_CACHE": cache,
            "PASSIVE": passive,
            "ADAPTIVE": False,
            "DEPTH_EVIDENCE": False,
            "TIGHT_ROOT": False,
        }.items():
            for previous in ("True", "False"):
                result = result.replace(f"{key} = {previous}", f"{key} = {value}")
        return result

    combined = variant(True, True)
    depth = combined.replace("DEPTH_EVIDENCE = False", "DEPTH_EVIDENCE = True")
    return {
        "reference": reference,
        "fast": variant(True, False),
        "cache": variant(False, True),
        "combined": variant(True, True),
        "objective": variant(True, True, False),
        "narrow": variant(True, True, False).replace("TIGHT_ROOT = False", "TIGHT_ROOT = True"),
        "depth": depth,
        "adaptive": depth.replace("ADAPTIVE = False", "ADAPTIVE = True"),
        "final": current,
    }


def load(name: str, source: str):
    module = types.ModuleType(name)
    sys.modules[name] = module
    exec(compile(source, name, "exec"), module.__dict__)
    return module


def positions(count: int = 24, seed: int = 702) -> list[str]:
    rng = random.Random(seed)
    result = []
    for _ in range(count):
        board = chess.Board()
        for _ in range(rng.randrange(4, 28)):
            if board.is_game_over():
                break
            board.push(rng.choice(list(board.legal_moves)))
        if not board.is_game_over():
            result.append(board.fen())
    return result


def fixed_engine(module, depth: int):
    class Fixed(module.Engine):
        def search(self, board, remaining, alpha, beta, ply):
            if ply == 1 and remaining == depth:
                raise module.Deadline
            return super().search(board, remaining, alpha, beta, ply)

    return Fixed()


def benchmark(names: list[str], repeats: int) -> None:
    modules = {}
    initialization = {}
    for name in names:
        before = time.perf_counter()
        modules[name] = load("selection_" + name, sources()[name])
        initialization[name] = time.perf_counter() - before
    reference = load("evaluation_oracle", sources()["reference"])
    corpus = positions(1000, 882)
    for fen in corpus:
        board = chess.Board(fen)
        expected = reference.evaluate(board)
        for module in modules.values():
            assert module.evaluate(board) == expected, fen
            assert module.evaluate(board.mirror()) == expected, fen
    rows = {name: [] for name in names}
    corpus = positions()
    expected = {}
    for repeat in range(repeats):
        for index, fen in enumerate(corpus):
            order = names[repeat % len(names) :] + names[: repeat % len(names)]
            for name in order:
                module = modules[name]
                if hasattr(module, "_eval_table"):
                    module._eval_table[:] = [None] * len(module._eval_table)
                module._engine = fixed_engine(module, 2)
                started = time.perf_counter()
                move = module.get_move(fen, 120000)
                elapsed = time.perf_counter() - started
                result = (move, module._engine.stats["depth"], module._engine.nodes)
                expected.setdefault(index, result)
                assert result == expected[index], (name, fen, result, expected[index])
                rows[name].append({"seconds": elapsed, "nodes": result[2]})
    timed = {name: [] for name in names}
    for repeat in range(repeats):
        for index, fen in enumerate(corpus):
            order = names[(index + repeat) % len(names) :] + names[: (index + repeat) % len(names)]
            for name in order:
                module = modules[name]
                if hasattr(module, "_eval_table"):
                    module._eval_table[:] = [None] * len(module._eval_table)
                module._engine = module.Engine()
                started = time.perf_counter()
                move = module.get_move(fen, 5000)
                elapsed = time.perf_counter() - started
                assert chess.Move.from_uci(move) in chess.Board(fen).legal_moves
                timed[name].append({**module._engine.stats, "elapsed": elapsed})
    result = {}
    for name in names:
        fixed = rows[name]
        wall = timed[name]
        result[name] = {
            "fixed_median_ms": statistics.median(r["seconds"] for r in fixed) * 1000,
            "fixed_nps": sum(r["nodes"] for r in fixed) / sum(r["seconds"] for r in fixed),
            "timed_mean_depth": statistics.mean(r["depth"] for r in wall),
            "timed_median_ms": statistics.median(r["elapsed"] for r in wall) * 1000,
            "timed_worst_ms": max(r["elapsed"] for r in wall) * 1000,
            "overhead_median": statistics.median(r["model_seconds"] / r["seconds"] for r in wall),
            "overhead_p95": percentile([r["model_seconds"] / r["seconds"] for r in wall], 0.95),
            "initialization_ms": initialization[name] * 1000,
        }
    print(
        json.dumps(
            {
                "evaluation_equivalent": len(positions(1000, 882)) * 2,
                "fixed_positions": len(corpus),
                "repeats": repeats,
                "configs": result,
            },
            indent=2,
        )
    )


def profile() -> None:
    module = load("profiled_reference", sources()["reference"])
    profiler = cProfile.Profile()
    profiler.enable()
    for fen in positions(3):
        module._engine = module.Engine()
        module.get_move(fen, 10000)
    profiler.disable()
    stats = profiler.getstats()
    names = {
        "evaluate",
        "position_key",
        "order",
        "quiesce",
        "drawn",
        "enter",
        "leave",
        "tick",
        "search",
        "generate_legal_moves",
        "generate_pseudo_legal_moves",
    }
    result = {
        entry.code.co_name: {
            "calls": entry.callcount,
            "self_seconds": entry.inlinetime,
            "inclusive_seconds": entry.totaltime,
        }
        for entry in stats
        if not isinstance(entry.code, str) and entry.code.co_name in names
    }
    board = chess.Board()
    key = module.position_key(board)
    module._engine.table[hash(key) % module.TT_SIZE] = module.Entry(
        key, 0, 0, 2, 0, 35, chess.Move.from_uci("g1f3"), 1
    )
    samples = 100000
    started = time.perf_counter()
    for _ in range(samples):
        entry = module._engine.table[hash(key) % module.TT_SIZE]
        if entry is not None and entry.key == key:
            module.unpack_mate(entry.score, 2)
    lookup_us = (time.perf_counter() - started) * 1e6 / samples
    print(
        json.dumps(
            {"profile": result, "tt_lookup_microseconds": lookup_us, "tt_lookup_samples": samples},
            indent=2,
        )
    )


def root_benchmark(repeats: int) -> None:
    modules = {name: load("root_" + name, sources()[name]) for name in ("objective", "narrow")}
    rows = {name: [] for name in modules}
    for repeat in range(repeats):
        for fen in positions():
            results = {}
            for name in tuple(modules) if repeat % 2 else tuple(reversed(modules)):
                module = modules[name]
                module._eval_table[:] = [None] * len(module._eval_table)
                module._engine = fixed_engine(module, 2)
                started = time.perf_counter()
                move = chess.Move.from_uci(module.get_move(fen, 120000))
                elapsed = time.perf_counter() - started
                results[name] = (move, module._engine.completed_scores)
                rows[name].append({"seconds": elapsed, "nodes": module._engine.nodes})
            best = max(results["objective"][1].values())
            assert max(results["narrow"][1].values()) == best
            assert results["objective"][1][results["narrow"][0]] == best
    print(
        json.dumps(
            {
                "positions": len(positions()),
                "repeats": repeats,
                "completed_value_equivalence": "passed",
                "configs": {
                    name: {
                        "median_ms": statistics.median(r["seconds"] for r in data) * 1000,
                        "mean_nodes": statistics.mean(r["nodes"] for r in data),
                        "nps": sum(r["nodes"] for r in data) / sum(r["seconds"] for r in data),
                    }
                    for name, data in rows.items()
                },
            },
            indent=2,
        )
    )


def arena(args) -> None:
    engines = sources()
    configs = args.configs.split(",")
    hashes = {name: hashlib.sha256(engines[name].encode()).hexdigest() for name in configs}
    print(
        "RUN "
        + json.dumps(
            {
                "source_sha256": hashes,
                "seed": args.seed,
                "base_ms": args.base_ms,
                "increment_ms": args.increment_ms,
            }
        ),
        flush=True,
    )
    opponents = ("random", "material", "forcing", "shallow", "deep", "switching")
    if args.sparring:
        opponents = ("sparring",)
    records, telemetry = [], {name: [] for name in configs}
    timings = {name: [] for name in configs}
    initialization = {name: [] for name in configs}
    endings = Counter()
    total = args.games or args.cases * len(configs)
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        for game in range(total):
            case, configuration = divmod(game, len(configs))
            case += getattr(args, "case_offset", 0)
            # Alternate execution order to balance host drift within each matched case.
            name = configs[(configuration + case) % len(configs)]
            opponent = opponents[case % len(opponents)]
            pair = case // (2 * len(opponents))
            if args.spread_openings:
                pair += case % len(opponents)
            white = (case // len(opponents)) % 2 == 0
            seed = args.seed + pair
            board = chess.Board()
            for move in OPENINGS[pair % len(OPENINGS)]:
                board.push_uci(move)
            start_fen = board.fen()
            own_dir, rival_dir = root / "own", root / "rival"
            own_dir.mkdir(exist_ok=True)
            rival_dir.mkdir(exist_ok=True)
            (own_dir / "agent.py").write_text(engines[name] + MEASURED_TELEMETRY)
            rival_source = (
                engines["reference"] if opponent == "sparring" else opponent_source(opponent, seed)
            )
            (rival_dir / "agent.py").write_text(rival_source)
            own, rival = MeasuredAgent(own_dir), MeasuredAgent(rival_dir)
            outcome = play_match(
                own if white else rival,
                rival if white else own,
                args.base_ms,
                args.increment_ms,
                start_fen=start_fen,
            )
            failed = outcome.termination in FAILED_TERMINATIONS
            initialization[name].append(getattr(own, "initialization", 90.0))
            score = 0.5 if outcome.result == "draw" else float((outcome.result == "white") == white)
            timings[name].extend(own.times)
            game_stats = []
            for line in own.stderr_tail.splitlines():
                if line.startswith('{"stats":'):
                    data = json.loads(line)["stats"]
                    assert data.get("cp_loss", 0) <= data.get("limit", 25)
                    assert data.get("bonus", 0) <= 12
                    telemetry[name].append(data)
                    game_stats.append(data)
            pgn = chess.pgn.read_game(io.StringIO(outcome.pgn))
            assert pgn is not None
            seen = set()
            for position in pgn.mainline():
                board = position.board()
                if board.occupied.bit_count() <= 5:
                    parts = [
                        "".join(
                            chess.piece_symbol(p).upper() * board.pieces_mask(p, c).bit_count()
                            for p in (6, 5, 4, 3, 2, 1)
                        )
                        for c in (True, False)
                    ]
                    seen.add("v".join(sorted(parts, key=lambda p: (-len(p), p))))
            endings.update(seen)
            row = {
                "case": case,
                "config": name,
                "opponent": opponent,
                "white": white,
                "seed": seed,
                "start_fen": start_fen,
                "score": score,
                "termination": outcome.termination,
                "failure": failed,
                "move_seconds": own.times,
                "telemetry": game_stats,
                "initialization_ms": initialization[name][-1] * 1000,
            }
            records.append(row)
            print(json.dumps(row), flush=True)
            if failed:
                print(own.stderr_tail[-2000:] + rival.stderr_tail[-2000:])
                raise AssertionError(row)
    matched_cases = {}
    for row in records:
        identity = (row["start_fen"], row["seed"], row["white"])
        previous = matched_cases.setdefault(row["case"], identity)
        assert identity == previous, "Configurations received different game conditions"
    reversed_pairs = 0
    for case, identity in matched_cases.items():
        partner = matched_cases.get(case + len(opponents)) if identity[2] else None
        if partner is not None:
            assert identity[:2] == partner[:2] and not partner[2]
            reversed_pairs += 1
    summaries = {}
    for name in configs:
        data = telemetry[name]
        scores = [r["score"] for r in records if r["config"] == name]
        ratios = [r["model_seconds"] / r["seconds"] for r in data]
        summaries[name] = {
            "wdl": [scores.count(1), scores.count(0.5), scores.count(0)],
            "score": statistics.mean(scores),
            "games": len(scores),
            "mean_depth": statistics.mean(r["depth"] for r in data),
            "median_ms": statistics.median(timings[name]) * 1000,
            "worst_ms": max(timings[name]) * 1000,
            "overhead_median": statistics.median(ratios),
            "overhead_p95": percentile(ratios, 0.95),
            "coverage": statistics.mean(r.get("coverage", 0) for r in data),
            "overrides": sum(r.get("adapted", 0) for r in data),
            "peak_rss_mb": max(r.get("peak_rss_mb", 0) for r in data),
            "max_initialization_ms": max(initialization[name]) * 1000,
        }
    paired = {}
    for index, name in enumerate(configs[1:], 1):
        for control in configs[:index]:
            differences = []
            for case in {r["case"] for r in records}:
                matched = {r["config"]: r for r in records if r["case"] == case}
                if name in matched and control in matched:
                    differences.append(
                        (
                            case // (2 * len(opponents)) * len(opponents) + case % len(opponents),
                            matched[name]["score"] - matched[control]["score"],
                        )
                    )
            paired[name + "-" + control] = interval(differences)
    print(
        "SUMMARY "
        + json.dumps(
            {
                "configs": summaries,
                "paired": paired,
                "endings_by_game": dict(endings),
                "source_sha256": hashes,
                "verified_colour_pairs": reversed_pairs,
                "failures": 0,
            }
        ),
        flush=True,
    )


def gate(configs: list[str]) -> None:
    for name in configs:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "agent.py").write_text(sources()[name])
            for directory in ("harness", "baselines"):
                shutil.copytree(ROOT / directory, root / directory)
            for filename in ("Makefile", "pyproject.toml", "uv.lock"):
                shutil.copyfile(ROOT / filename, root / filename)
            checked = subprocess.run(["make", "gate"], cwd=root, capture_output=True, text=True)
            if checked.returncode:
                print("\n".join((checked.stdout + checked.stderr).splitlines()[-80:]))
                raise AssertionError(name)
            print(json.dumps({"config": name, "official_gate": "passed"}))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("bench", "root", "profile", "arena", "gate", "init"))
    parser.add_argument("--configs", default="reference,fast,cache,combined,objective")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--cases", type=int, default=60)
    parser.add_argument("--games", type=int, default=0)
    parser.add_argument("--seed", type=int, default=17000)
    parser.add_argument("--base-ms", type=int, default=5000)
    parser.add_argument("--increment-ms", type=int, default=100)
    parser.add_argument("--sparring", action="store_true")
    parser.add_argument("--spread-openings", action="store_true")
    args = parser.parse_args()
    if args.mode == "bench":
        benchmark(args.configs.split(","), args.repeats)
    elif args.mode == "root":
        root_benchmark(args.repeats)
    elif args.mode == "profile":
        profile()
    elif args.mode == "arena":
        arena(args)
    elif args.mode == "gate":
        gate(args.configs.split(","))
    else:
        start = time.perf_counter()
        spec = importlib.util.spec_from_file_location("initialization_test", ROOT / "agent.py")
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        print(
            json.dumps(
                {
                    "import_ms": (time.perf_counter() - start) * 1000,
                    "peak_rss_mb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024,
                    "sha256": hashlib.sha256((ROOT / "agent.py").read_bytes()).hexdigest(),
                }
            )
        )


if __name__ == "__main__":
    main()
