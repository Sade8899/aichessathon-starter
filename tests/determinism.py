"""Fixed-depth search equivalence and repeatability independent of clock jitter."""

import json
import random
import time

import chess

import agent


def fixed(board: chess.Board, depth: int) -> tuple[int, int]:
    search = agent.Engine()
    search.stats = {"model_seconds": 0.0}
    search.deadline = time.perf_counter() + 30
    search.enter(board)
    score = search.search(board, depth, -agent.INF, agent.INF, 0)
    nodes = search.nodes
    repeated = search.search(board, depth, -agent.INF, agent.INF, 0)
    assert repeated == score
    return score, nodes


def main() -> None:
    rng = random.Random(84)
    fixtures = []
    for _ in range(12):
        board = chess.Board()
        for _ in range(rng.randrange(8, 32)):
            if board.is_game_over():
                break
            board.push(rng.choice(list(board.legal_moves)))
        if not board.is_game_over():
            fixtures.append(board.fen())
    for fen in fixtures:
        board = chess.Board(fen)
        first = fixed(board, 2)
        second = fixed(board, 2)
        assert first == second, (fen, first, second)
        assert fixed(board.mirror(), 2)[0] == first[0], fen
    choices = []
    for _ in range(5):
        agent._engine = agent.Engine()
        choices.append(agent.get_move(chess.STARTING_FEN, 5000))
    assert len(set(choices)) == 1, choices
    print(
        json.dumps(
            {
                "fixed_depth_positions": len(fixtures),
                "repeatability": "passed",
                "colour_symmetry": "passed",
                "opening_repeats": choices,
            }
        )
    )


if __name__ == "__main__":
    main()
