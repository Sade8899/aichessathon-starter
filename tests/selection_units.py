"""Cache identity, bounded state, provenance and candidate-interface checks."""

import argparse
import json
import runpy
import sys

import chess
from selection import fixed_engine, load, positions, sources


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', default='objective')
    args = parser.parse_args()
    reference = load("selection_oracle", sources()["reference"])
    module = load("selection_candidate", sources()[args.config])
    corpus = positions(1000, 882)
    for fen in corpus:
        board = chess.Board(fen)
        for tested in (board, board.mirror()):
            assert module.evaluate(tested) == reference.evaluate(tested)
            before = tested.fen()
            expected = module.evaluate(tested)
            tested.halfmove_clock = 99
            tested.fullmove_number = 300
            tested.castling_rights = 0
            tested.ep_square = None
            assert module.evaluate(tested) == expected
            assert tested.fen() != before
    assert len(module._eval_table) == 32768
    assert len(module._engine.table) == 65536
    board = chess.Board()
    key = (
        board.pawns,
        board.knights,
        board.bishops,
        board.rooks,
        board.queens,
        board.kings,
        board.occupied_co[True],
        board.turn,
    )
    slot = hash(key) % len(module._eval_table)
    other_key = (*key[:-1], not key[-1])
    module._eval_table[slot] = (other_key, 29999)
    assert module.evaluate(board) == reference.evaluate(board)
    module._engine = module.Engine()
    board = chess.Board()
    for _ in range(8):
        move = chess.Move.from_uci(module.get_move(board.fen(), 5000))
        assert move in board.legal_moves
        board.push(move)
        if board.is_game_over():
            break
        board.push(next(iter(board.legal_moves)))
        if board.is_game_over():
            break
    assert module._engine.model.confidence().informative == 0
    assert not module._engine.completed_evidence
    depth = load("provenance_candidate", sources()["depth"])
    depth._engine = fixed_engine(depth, 3)
    depth.get_move(chess.STARTING_FEN, 120000)
    assert depth._engine.stats["depth"] == 3
    assert len(depth._engine.completed_evidence) <= 3
    for frame in depth._engine.completed_evidence.values():
        assert frame.depth == 2
        if frame.shallow:
            assert frame.shallow.depth == 1 and frame.shallow.shallow is None
    assert depth._engine.iteration_evidence == {}
    sys.modules["agent"] = module
    runpy.run_path("/workspace/tests/verify.py", run_name="__main__")
    print(
        json.dumps(
            {
                "score_equivalence": len(corpus) * 2,
                "cache_identity": "passed",
                "bounded_state": "passed",
                "completed_provenance": "passed",
                "objective_interface": "passed",
            }
        )
    )


if __name__ == "__main__":
    main()
