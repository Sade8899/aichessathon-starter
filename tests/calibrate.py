"""Held-out policy calibration using only evidence retained by get_move."""

import importlib.util
import json
import math
import random
import statistics
import sys
import tempfile
from pathlib import Path

import chess
from passive_arena import OPPONENTS, opponent_source

import agent


def main() -> None:
    rng = random.Random(191)
    models = {name: agent.OpponentModel() for name in OPPONENTS}
    loss = {name: [] for name in OPPONENTS}
    uniform_loss = []
    coverage = []
    exact = []
    with tempfile.TemporaryDirectory() as temporary:
        opponents = {}
        for name in OPPONENTS:
            path = Path(temporary) / f"{name}.py"
            path.write_text(opponent_source(name, 6200))
            spec = importlib.util.spec_from_file_location(f"policy_{name}", path)
            assert spec is not None and spec.loader is not None
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
            opponents[name] = module
        for sample in range(30):
            board = chess.Board()
            for _ in range(8 + sample * 2):
                if board.is_game_over():
                    break
                board.push(rng.choice(list(board.legal_moves)))
            if board.is_game_over():
                continue
            agent._engine = agent.Engine()
            move = agent.get_move(board.fen(), 20000)
            board.push_uci(move)
            frame = agent._engine.evidence
            if frame is None or len(frame.moves) < 3 or board.is_game_over():
                continue
            coverage.append(frame.coverage)
            exact.append(sum(r.bound == 0 for r in frame.replies.values()) / len(frame.moves))
            uniform_loss.append(math.log(len(frame.moves)))
            for name, opponent in opponents.items():
                observed = chess.Move.from_uci(opponent.get_move(board.fen(), 5000))
                assert observed in board.legal_moves
                probabilities = models[name].predict(frame).probabilities
                loss[name].append(-math.log(probabilities[frame.moves.index(observed)]))
                models[name].observe(frame, observed)
    result = {
        "positions": len(coverage),
        "coverage": statistics.mean(coverage),
        "exact_fraction": statistics.mean(exact),
        "uniform_log_loss": statistics.mean(uniform_loss),
        "policies": {},
    }
    for name, model in models.items():
        state = model.confidence()
        result["policies"][name] = {
            "posterior": dict(zip(agent.POLICIES, state.posterior, strict=True)),
            "confidence": state.value,
            "informative": state.informative,
            "predictive_log_loss": statistics.mean(loss[name]),
        }
    assert len(coverage) >= 12
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
