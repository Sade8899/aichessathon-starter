"""Paired held-out policy predictions from the same passively captured searches."""

import json
import math
import random
import statistics
from dataclasses import replace

import chess
from passive_arena import OPPONENTS, opponent_source
from selection import load, sources


def main() -> None:
    module = load("depth_candidate", sources()["depth"])
    opponents = {name: load("heldout_" + name, opponent_source(name, 6200)) for name in OPPONENTS}
    models = {
        variant: {name: module.OpponentModel() for name in OPPONENTS}
        for variant in ("single_depth", "successive_depths")
    }
    metrics = {variant: {name: [] for name in OPPONENTS} for variant in models}
    rng = random.Random(191)
    coverage, exact, shallow_coverage = [], [], []
    for sample in range(40):
        board = chess.Board()
        for _ in range(8 + sample * 2):
            if board.is_game_over():
                break
            board.push(rng.choice(list(board.legal_moves)))
        if board.is_game_over():
            continue
        module._engine = module.Engine()
        board.push_uci(module.get_move(board.fen(), 20000))
        frame = module._engine.evidence
        if frame is None or len(frame.moves) < 3 or board.is_game_over():
            continue
        coverage.append(frame.coverage)
        exact.append(sum(r.bound == 0 for r in frame.replies.values()) / len(frame.moves))
        shallow_coverage.append(frame.shallow.coverage if frame.shallow else 0)
        if frame.shallow is not None:
            assert frame.shallow.depth < frame.depth and frame.shallow.shallow is None
            assert set(frame.shallow.moves) == set(frame.moves)
        for name, opponent in opponents.items():
            move = chess.Move.from_uci(opponent.get_move(board.fen(), 5000))
            assert move in board.legal_moves
            index = frame.moves.index(move)
            for variant in models:
                evidence = replace(frame, shallow=None) if variant == "single_depth" else frame
                model = models[variant][name]
                probs = model.predict(evidence).probabilities
                top = max(range(len(probs)), key=lambda i: probs[i])
                metrics[variant][name].append(
                    {
                        "loss": -math.log(probs[index]),
                        "uniform_loss": math.log(len(probs)),
                        "brier": sum((p - int(i == index)) ** 2 for i, p in enumerate(probs)),
                        "confidence": probs[top],
                        "correct": int(top == index),
                    }
                )
                model.observe(evidence, move)
    result = {}
    for variant in models:
        result[variant] = {}
        for name, model in models[variant].items():
            rows = metrics[variant][name]
            error = 0.0
            for low in range(10):
                bucket = [r for r in rows if low / 10 <= r["confidence"] < (low + 1) / 10]
                if bucket:
                    error += (
                        len(bucket)
                        / len(rows)
                        * abs(statistics.mean(r["confidence"] - r["correct"] for r in bucket))
                    )
            state = model.confidence()
            result[variant][name] = {
                "log_loss": statistics.mean(r["loss"] for r in rows),
                "uniform_loss": statistics.mean(r["uniform_loss"] for r in rows),
                "brier": statistics.mean(r["brier"] for r in rows),
                "ece": error,
                "posterior": dict(zip(module.POLICIES, state.posterior, strict=True)),
                "confidence": state.value,
                "informative": state.informative,
            }
    assert len(coverage) >= 12
    print(
        json.dumps(
            {
                "positions": len(coverage),
                "coverage": statistics.mean(coverage),
                "exact": statistics.mean(exact),
                "shallow_coverage": statistics.mean(shallow_coverage),
                "variants": result,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
