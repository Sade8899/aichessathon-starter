"""Three-way paired experiments using the official Chessathon lifecycle."""

import argparse
import hashlib
import json
import random
import statistics
import tempfile
import time
from pathlib import Path

import chess

from harness.referee import FAILED_TERMINATIONS, play_match
from harness.sandbox import Agent

ROOT = Path("/workspace")
CONFIGS = ("baseline", "passive", "adaptive")
OPPONENTS = ("random", "material", "forcing", "shallow", "deep", "switching")
OPENINGS = (
    ("e2e4", "e7e5", "g1f3", "b8c6"),
    ("d2d4", "d7d5", "c2c4", "e7e6"),
    ("c2c4", "e7e5", "b1c3", "g8f6"),
    ("g1f3", "d7d5", "g2g3", "g8f6"),
    ("e2e4", "c7c5", "g1f3", "d7d6"),
)
TELEMETRY = """
_tested_get_move = get_move
def get_move(fen: str, time_left_ms: int) -> str:
    move = _tested_get_move(fen, time_left_ms)
    data = {'stats': _engine.stats}
    if hasattr(_engine, 'model'):
        state = _engine.model.confidence()
        data['posterior'] = state.posterior
        data['informative'] = state.informative
    print(__import__('json').dumps(data), flush=True)
    return move
"""


class TimedAgent(Agent):
    def __init__(self, directory: Path) -> None:
        import sys

        super().__init__([sys.executable, str(ROOT / "harness/runner.py"), str(directory)])
        self.times: list[float] = []

    def move(self, fen: str, time_left_ms: int) -> str:
        start = time.perf_counter()
        result = super().move(fen, time_left_ms)
        self.times.append(time.perf_counter() - start)
        return result


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int((len(ordered) - 1) * fraction))] if ordered else 0


def interval(differences: list[tuple[int, float]]) -> dict[str, object]:
    groups: dict[int, list[float]] = {}
    for group, value in differences:
        groups.setdefault(group, []).append(value)
    means = [statistics.mean(values) for values in groups.values()]
    rng = random.Random(821)
    samples = [statistics.mean(rng.choices(means, k=len(means))) for _ in range(4000)]
    limits = [percentile(samples, 0.025), percentile(samples, 0.975)]
    method = "paired colour-cluster bootstrap"
    if all(value == 0 for value in means):
        radius = 1 - 0.05 ** (1 / len(means))
        limits = [-radius, radius]
        method = "zero-discordance exact bound on paired mean difference"
    return {
        "difference": statistics.mean(v for _, v in differences),
        "ci95": limits,
        "method": method,
        "colour_pair_clusters": len(means),
    }


def sources() -> dict[str, str]:
    current = (ROOT / "agent.py").read_text()
    baseline = (ROOT / "tests/checkpoint/agent.py").read_text()
    return {
        "baseline": baseline,
        "passive": current,
        "adaptive": current.replace("ADAPTIVE = False", "ADAPTIVE = True"),
    }


def opponent_source(name: str, seed: int) -> str:
    supplied = {"random": "random", "material": "greedy", "shallow": "minimax"}
    if name in supplied:
        return (ROOT / "baselines" / supplied[name] / "agent.py").read_text() + (
            f"\nrandom.seed({seed})\n"
        )
    return (
        (ROOT / "tests/policy_opponents.py")
        .read_text()
        .replace('POLICY = "forcing"', f'POLICY = "{name}"')
        .replace("SEED = 9000", f"SEED = {seed}")
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke", "--games", type=int, default=0)
    parser.add_argument("--cases", type=int, default=60)
    parser.add_argument("--seed", type=int, default=9100)
    parser.add_argument("--base-ms", type=int, default=5000)
    parser.add_argument("--increment-ms", type=int, default=100)
    args = parser.parse_args()
    engines = sources()
    records = []
    stats = {name: [] for name in CONFIGS}
    elapsed = {name: [] for name in CONFIGS}
    posteriors = {name: {opponent: [] for opponent in OPPONENTS} for name in CONFIGS}
    total = args.smoke or args.cases * 3
    with tempfile.TemporaryDirectory() as temporary:
        directory = Path(temporary)
        for game in range(total):
            case, config_index = divmod(game, 3)
            name = CONFIGS[config_index]
            opponent_name = OPPONENTS[case % 6]
            plays_white = (case // 6) % 2 == 0
            seed = args.seed + case // 12
            board = chess.Board()
            for move in OPENINGS[(case // 12) % len(OPENINGS)]:
                board.push_uci(move)
            own_path, rival_path = directory / name, directory / "rival"
            own_path.mkdir(exist_ok=True)
            rival_path.mkdir(exist_ok=True)
            (own_path / "agent.py").write_text(engines[name] + TELEMETRY)
            (rival_path / "agent.py").write_text(opponent_source(opponent_name, seed))
            own, rival = TimedAgent(own_path), TimedAgent(rival_path)
            outcome = play_match(
                own if plays_white else rival,
                rival if plays_white else own,
                args.base_ms,
                args.increment_ms,
                start_fen=board.fen(),
            )
            failed = outcome.termination in FAILED_TERMINATIONS
            score = (
                0.5
                if outcome.result == "draw"
                else float((outcome.result == "white") == plays_white)
            )
            elapsed[name].extend(own.times)
            for line in own.stderr_tail.splitlines():
                if not line.startswith('{"stats":'):
                    continue
                data = json.loads(line)
                item = data["stats"]
                stats[name].append(item)
                if item.get("adapted", 0):
                    assert item["cp_loss"] <= item["limit"] <= 25
                    assert item["bonus"] <= 12 and item["depth"] >= 1
                if data.get("informative", 0) >= 6:
                    posteriors[name][opponent_name].append(data["posterior"])
            row = {
                "case": case,
                "config": name,
                "opponent": opponent_name,
                "white": plays_white,
                "seed": seed,
                "score": score,
                "termination": outcome.termination,
                "failure": failed,
            }
            records.append(row)
            print(json.dumps(row), flush=True)
            if failed:
                print("FAILURE DETAIL " + own.stderr_tail[-2000:] + rival.stderr_tail[-2000:])
                raise AssertionError(row)
    summary = {}
    for name in CONFIGS:
        games = [r for r in records if r["config"] == name]
        data = stats[name]
        ratios = [s["model_seconds"] / s["seconds"] for s in data]
        summary[name] = {
            "games": len(games),
            "wins": sum(r["score"] == 1 for r in games),
            "draws": sum(r["score"] == 0.5 for r in games),
            "losses": sum(r["score"] == 0 for r in games),
            "score": statistics.mean(r["score"] for r in games),
            "median_ms": 1000 * statistics.median(elapsed[name]),
            "worst_ms": 1000 * max(elapsed[name]),
            "overhead_median": statistics.median(ratios),
            "overhead_p95": percentile(ratios, 0.95),
            "depth_mean": statistics.mean(s["depth"] for s in data),
            "coverage_mean": statistics.mean(s.get("coverage", 0) for s in data),
            "coverage_over_70": sum(s.get("coverage", 0) > 0.7 for s in data) / len(data),
            "adaptive_moves": sum(s.get("adapted", 0) for s in data),
            "posteriors": {
                opponent: [statistics.mean(v[i] for v in values) for i in range(6)]
                if values
                else []
                for opponent, values in posteriors[name].items()
            },
        }
    paired = {}
    for name, control in (
        ("passive", "baseline"),
        ("adaptive", "baseline"),
        ("adaptive", "passive"),
    ):
        differences = []
        for case in range((total + 2) // 3):
            matched = {r["config"]: r for r in records if r["case"] == case}
            if name in matched and control in matched:
                group = case // 12 * 6 + case % 6
                differences.append((group, matched[name]["score"] - matched[control]["score"]))
        paired[f"{name}-{control}"] = interval(differences)
    timing_pass = all(
        summary[name]["overhead_median"] < 0.005 and summary[name]["overhead_p95"] < 0.01
        for name in CONFIGS[1:]
    )
    improvement = (
        paired["adaptive-baseline"]["difference"] > 0
        and paired["adaptive-passive"]["difference"] > 0
        and summary["adaptive"]["adaptive_moves"] > 0
    )
    print(
        "SUMMARY "
        + json.dumps(
            {
                "configs": summary,
                "paired": paired,
                "baseline_sha256": hashlib.sha256(engines["baseline"].encode()).hexdigest(),
                "failures": 0,
                "timing_gate": timing_pass,
                "screen_indicates_improvement": improvement and timing_pass,
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
