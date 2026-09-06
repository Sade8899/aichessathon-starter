"""Standard-chess test opponents; never imported by the submitted agent."""

import random
import time

import chess

POLICY = "forcing"
SEED = 9000
_rng = random.Random(SEED)
_calls = 0
VALUES = (0, 100, 320, 335, 500, 950, 0)


def static(board: chess.Board) -> int:
    score = 0
    for colour in (True, False):
        own = board.occupied_co[colour]
        value = sum(
            VALUES[piece] * (board.pieces_mask(piece, colour)).bit_count() for piece in range(1, 6)
        )
        for square in chess.scan_forward(own & (board.knights | board.bishops)):
            file, rank = chess.square_file(square), chess.square_rank(square)
            value += 14 - abs(2 * file - 7) - abs(2 * rank - 7)
        score += value if colour == board.turn else -value
    return score


class Timeout(Exception):
    pass


def minimax(board: chess.Board, depth: int, alpha: int, beta: int, stop: float) -> int:
    if time.perf_counter() >= stop:
        raise Timeout
    moves = list(board.legal_moves)
    if not moves:
        return -30000 - depth if board.is_check() else 0
    if board.is_insufficient_material() or board.halfmove_clock >= 100:
        return 0
    if depth == 0:
        return static(board)
    moves.sort(key=lambda m: (bool(m.promotion), board.is_capture(m)), reverse=True)
    best = -40000
    for move in moves:
        board.push(move)
        try:
            score = -minimax(board, depth - 1, -beta, -alpha, stop)
        finally:
            board.pop()
        best = max(best, score)
        alpha = max(alpha, score)
        if alpha >= beta:
            break
    return best


def get_move(fen: str, time_left_ms: int) -> str:
    global _calls
    _calls += 1
    board = chess.Board(fen)
    moves = list(board.legal_moves)
    _rng.shuffle(moves)
    chosen = moves[0]
    if time_left_ms <= 10:
        return chosen.uci()
    policy = POLICY
    if policy == "switching":
        policy = ("random", "material", "deep")[((_calls - 1) // 8) % 3]
    if policy == "random":
        return chosen.uci()
    if policy in ("material", "forcing"):
        best = -40000
        for move in moves:
            capture = int(board.is_capture(move))
            board.push(move)
            score = -static(board)
            if board.is_checkmate():
                score = 30000
            elif policy == "forcing":
                score += 160 * int(board.is_check()) + 60 * capture
            board.pop()
            if score > best:
                chosen, best = move, score
        return chosen.uci()
    stop = time.perf_counter() + min(0.20, time_left_ms / 40000)
    for depth in range(1, 4):
        current = []
        try:
            for move in moves:
                board.push(move)
                try:
                    value = -minimax(board, depth - 1, -40000, 40000, stop)
                finally:
                    board.pop()
                current.append((value, move))
        except Timeout:
            break
        current.sort(key=lambda pair: pair[0], reverse=True)
        chosen = current[0][1]
        moves = [move for _, move in current]
    return chosen.uci()
