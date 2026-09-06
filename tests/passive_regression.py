"""Compare preserved and passive engines on exactly the same standard-chess FENs."""

import importlib.util
import json
import random
import statistics
import sys
import time
from pathlib import Path

import chess

import agent


def baseline_module():
    spec = importlib.util.spec_from_file_location(
        "frozen_checkpoint", Path("tests/checkpoint/agent.py")
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def fixed_engine(module):
    class FixedEngine(module.Engine):
        objective_nodes = 0

        def search(self, board, depth, alpha, beta, ply):
            if ply == 1 and depth == 2:
                self.objective_nodes = self.nodes
                raise module.Deadline
            return super().search(board, depth, alpha, beta, ply)

    return FixedEngine()


def main() -> None:
    baseline = baseline_module()
    rng = random.Random(702)
    positions = []
    for _ in range(24):
        board = chess.Board()
        for _ in range(rng.randrange(4, 28)):
            if board.is_game_over():
                break
            board.push(rng.choice(list(board.legal_moves)))
        if not board.is_game_over():
            positions.append(board.fen())
    equivalent = 0
    timings = {name: [] for name in ("baseline", "passive")}
    depths = {name: [] for name in timings}
    losses = 0
    for index, fen in enumerate(positions):
        outcomes = []
        for module in (baseline, agent):
            module._engine = fixed_engine(module)
            move = module.get_move(fen, 120000)
            outcomes.append((move, module._engine.stats["depth"], module._engine.objective_nodes))
        assert outcomes[0] == outcomes[1], (fen, outcomes)
        equivalent += 1
        order = [("baseline", baseline), ("passive", agent)]
        if index % 2:
            order.reverse()
        for name, module in order:
            module._engine = module.Engine()
            start = time.perf_counter()
            move = module.get_move(fen, 5000)
            timings[name].append(time.perf_counter() - start)
            depths[name].append(module._engine.stats["depth"])
            assert chess.Move.from_uci(move) in chess.Board(fen).legal_moves
        losses += depths["passive"][-1] < depths["baseline"][-1]
    delta = statistics.mean(
        p - b for p, b in zip(depths["passive"], depths["baseline"], strict=True)
    )
    print(
        json.dumps(
            {
                "fixed_depth_equivalent": equivalent,
                "same_search_nodes": True,
                "paired_depth_delta": delta,
                "lower_depth_positions": losses,
                "positions": len(positions),
                "median_ms": {
                    name: statistics.median(values) * 1000 for name, values in timings.items()
                },
            },
            indent=2,
        )
    )
    assert delta >= -0.05, "completed-depth regression exceeds 0.05 ply"


if __name__ == "__main__":
    main()
