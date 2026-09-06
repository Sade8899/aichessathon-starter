"""Deterministic correctness and timing checks, run inside Docker."""

import json
import random
import statistics
import time

import chess

import agent


def fresh_move(fen: str, clock: int) -> str:
    agent._engine = agent.Engine()
    return agent.get_move(fen, clock)


def engine(board: chess.Board) -> agent.Engine:
    result = agent.Engine()
    result.deadline = time.perf_counter() + 30
    result.stats = {"model_seconds": 0.0}
    result.enter(board)
    return result


def reference(board: chess.Board, depth: int, search: agent.Engine, ply: int = 0) -> int:
    moves = list(board.legal_moves)
    if not moves:
        return -agent.MATE + ply if board.is_check() else 0
    if search.drawn(board):
        return 0
    if depth == 0:
        return search.quiesce(board, -agent.INF, agent.INF, ply)
    best = -agent.INF
    for move in moves:
        board.push(move)
        key = search.enter(board)
        best = max(best, -reference(board, depth - 1, search, ply + 1))
        search.leave(key)
        board.pop()
    return best


def main() -> None:
    fixtures = {
        "check": "4k3/8/8/8/8/8/4r3/4K3 w - - 0 1",
        "castle": "r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1",
        "ep": "4k3/8/8/3pP3/8/8/8/4K3 w - d6 0 1",
        "promotion": "7k/P7/8/8/8/8/8/7K w - - 0 1",
        "underpromotion": "8/1P6/k7/8/1K6/8/8/8 w - - 0 1",
        "mate": "7k/5Q2/6K1/8/8/8/8/8 w - - 0 1",
        "stalemate": "7k/5Q2/6K1/8/8/8/8/8 b - - 0 1",
        "mated": "7k/6Q1/6K1/8/8/8/8/8 b - - 100 1",
        "fifty": "7k/8/8/8/8/8/R7/7K w - - 100 1",
    }
    for name, fen in fixtures.items():
        board = chess.Board(fen)
        for tested in (board, board.mirror()):
            move = fresh_move(tested.fen(), 2000)
            assert (
                move == "0000"
                if tested.is_game_over() and not any(tested.legal_moves)
                else chess.Move.from_uci(move) in tested.legal_moves
            ), name
            assert agent.evaluate(tested) == agent.evaluate(tested.mirror()), name
        if name in ("check", "ep", "promotion", "fifty", "stalemate", "mated"):
            tested_engine = engine(board)
            expected = reference(board, 2, tested_engine)
            actual = tested_engine.search(board, 2, -agent.INF, agent.INF, 0)
            assert actual == expected, (name, actual, expected)
    board = chess.Board(fixtures["mate"])
    board.push_uci(fresh_move(board.fen(), 5000))
    assert board.is_checkmate()
    board = chess.Board(fixtures["underpromotion"])
    assert fresh_move(board.fen(), 5000) == "b7b8r"
    for score in (29990, -29990, 350, -500):
        assert agent.unpack_mate(agent.pack_mate(score, 7), 7) == score
    board = chess.Board()
    tracked = engine(board)
    for uci in ("g1f3", "g8f6", "f3g1", "f6g8") * 2:
        board.push_uci(uci)
        tracked.enter(board)
    assert tracked.drawn(board)
    assert tracked.search(board, 2, -agent.INF, agent.INF, 0) == 0
    board = chess.Board(fixtures["fifty"].replace("100 1", "99 1"))
    assert engine(board).drawn(board)
    board = chess.Board()
    tracked = engine(board)
    for uci in ("g1f3", "g8f6", "f3g1", "f6g8", "g1f3", "g8f6", "f3g1"):
        board.push_uci(uci)
        tracked.enter(board)
    assert tracked.drawn(board) and board.can_claim_threefold_repetition()
    for name in ("castle", "ep", "promotion"):
        board = chess.Board(fixtures[name])
        tracked.pending = board
        for move in list(board.legal_moves):
            successor = board.copy()
            successor.push(move)
            assert tracked.reconstruct(successor) == move.uci(), (name, move)
    tracked = engine(chess.Board())
    board = chess.Board()
    before = (board.fen(), tracked.seen.copy(), tracked.context, tracked.duplicates)
    tracked.deadline = time.perf_counter() - 1
    try:
        tracked.search(board, 4, -agent.INF, agent.INF, 0)
    except agent.Deadline:
        pass
    else:
        raise AssertionError("search ignored deadline")
    assert before == (board.fen(), tracked.seen, tracked.context, tracked.duplicates)
    rng = random.Random(2526)
    board = chess.Board()
    positions = []
    while len(positions) < 500:
        if board.is_game_over(claim_draw=True) or board.ply() > 200:
            board = chess.Board()
        board.push(rng.choice(list(board.legal_moves)))
        if any(board.legal_moves):
            positions.append(board.fen())
    timings: dict[int, list[float]] = {clock: [] for clock in (1, 10, 50, 100, 500)}
    overhead = []
    for i, fen in enumerate(positions):
        board = chess.Board(fen)
        assert agent.evaluate(board) == agent.evaluate(board.mirror()), fen
        clock = (1, 10, 50, 100, 500)[i % 5]
        tested = agent.Engine()
        start = time.perf_counter()
        agent._engine = tested
        move = agent.get_move(fen, clock)
        elapsed = (time.perf_counter() - start) * 1000
        assert chess.Move.from_uci(move) in chess.Board(fen).legal_moves
        timings[clock].append(elapsed)
        overhead.append(tested.stats["model_seconds"] / tested.stats["seconds"])
        assert elapsed < clock, (clock, elapsed, fen)
    print(
        json.dumps(
            {
                "targeted": "passed",
                "reachable_positions": len(positions),
                "clocks_ms": {
                    str(k): {"median": statistics.median(v), "worst": max(v)}
                    for k, v in timings.items()
                },
                "model_overhead_median": statistics.median(overhead),
                "model_overhead_worst": max(overhead),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
