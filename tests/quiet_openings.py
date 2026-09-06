"""Author-selected standard opening prefixes, fixed before any arena result."""

import json

import chess

LINES = (
    "e4 e5 Nf3 Nc6 Bb5 a6 Ba4 Nf6",
    "e4 e5 Nf3 Nc6 Bc4 Bc5 c3 Nf6",
    "e4 e5 Nf3 Nc6 Bc4 Nf6 d3 Bc5",
    "e4 e5 Nf3 Nc6 d4 exd4 Nxd4 Nf6",
    "e4 e5 Nf3 Nf6 Nxe5 d6 Nf3 Nxe4",
    "e4 e5 Nc3 Nf6 Bc4 Nc6 d3 Bc5",
    "e4 e5 Nf3 Nc6 Nc3 Nf6 Bb5 Bb4",
    "e4 e5 Bc4 Nf6 d3 Nc6 Nf3 Bc5",
    "e4 c5 Nf3 d6 d4 cxd4 Nxd4 Nf6",
    "e4 c5 Nf3 Nc6 d4 cxd4 Nxd4 g6",
    "e4 c5 Nf3 e6 d4 cxd4 Nxd4 a6",
    "e4 c5 c3 Nf6 e5 Nd5 d4 cxd4",
    "e4 c5 Nc3 Nc6 g3 g6 Bg2 Bg7",
    "e4 e6 d4 d5 Nc3 Nf6 Bg5 Be7",
    "e4 e6 d4 d5 Nd2 Nf6 e5 Nfd7",
    "e4 e6 d4 d5 exd5 exd5 Nf3 Nf6",
    "e4 c6 d4 d5 Nc3 dxe4 Nxe4 Bf5",
    "e4 c6 d4 d5 e5 Bf5 Nf3 e6",
    "e4 d6 d4 Nf6 Nc3 g6 Nf3 Bg7",
    "e4 g6 d4 Bg7 Nc3 d6 Nf3 a6",
    "d4 d5 c4 e6 Nc3 Nf6 Bg5 Be7",
    "d4 d5 c4 c6 Nf3 Nf6 Nc3 e6",
    "d4 d5 c4 dxc4 Nf3 Nf6 e3 e6",
    "d4 Nf6 c4 e6 Nc3 Bb4 e3 O-O",
    "d4 Nf6 c4 g6 Nc3 Bg7 e4 d6",
    "d4 Nf6 c4 g6 Nc3 d5 cxd5 Nxd5",
    "d4 Nf6 c4 e6 Nf3 b6 g3 Bb7",
    "c4 e5 Nc3 Nf6 g3 d5 cxd5 Nxd5",
    "c4 c5 Nc3 Nc6 g3 g6 Bg2 Bg7",
    "Nf3 d5 g3 Nf6 Bg2 e6 O-O Be7",
)


def main() -> None:
    positions = []
    for line in LINES:
        board = chess.Board()
        for san in line.split():
            board.push_san(san)
        assert board.is_valid() and not board.is_game_over() and not board.is_check()
        positions.append(
            {"san": line, "uci": [m.uci() for m in board.move_stack], "fen": board.fen()}
        )
    assert len({p["fen"] for p in positions}) == 30
    print(
        json.dumps(
            {
                "source": "Hand-authored common standard-chess opening prefixes; no engine labels",
                "selection": "All 30 listed lines in source order; no candidate-based filtering",
                "positions": positions,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
