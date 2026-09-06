"""Full-clock timing, evaluator symmetry, and numeric hot-path measurements."""

import cProfile
import io
import json
import pstats
import statistics
import time

import chess

import agent


def main() -> None:
    positions = [
        chess.STARTING_FEN,
        "r3k2r/ppp2ppp/2n1bn2/3qp3/8/2NP1N2/PPP1BPPP/R1BQ1RK1 w kq - 0 9",
        "8/5pk1/6p1/3P4/4K3/8/5P2/8 w - - 0 40",
    ]
    rows = []
    for fen in positions:
        search = agent.Engine()
        agent._engine = search
        start = time.perf_counter()
        move = agent.get_move(fen, 120000)
        elapsed = time.perf_counter() - start
        assert chess.Move.from_uci(move) in chess.Board(fen).legal_moves
        rows.append({"move": move, **search.stats, "external_seconds": elapsed})
    print(
        json.dumps(
            {
                "positions": rows,
                "median_ms": statistics.median(row["external_seconds"] * 1000 for row in rows),
            },
            indent=2,
        )
    )
    profiler = cProfile.Profile()
    profiler.enable()
    agent._engine = agent.Engine()
    agent.get_move(chess.STARTING_FEN, 20000)
    profiler.disable()
    output = io.StringIO()
    pstats.Stats(profiler, stream=output).sort_stats("tottime").print_stats(12)
    print(output.getvalue())


if __name__ == "__main__":
    main()
