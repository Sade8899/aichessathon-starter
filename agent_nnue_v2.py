"""Classical chess search with conservative, root-only opponent adaptation."""

from __future__ import annotations

import hashlib
import math
import os
import sys
import time
from collections import Counter
from dataclasses import dataclass

import chess
import numpy as np
from numba import njit

VALUES = (0, 100, 320, 335, 500, 950, 0)
MATE = 30_000
INF = 32_000
MAX_PLY = 96
TT_SIZE = 65_536
PRIOR = (0.17, 0.17, 0.17, 0.17, 0.17, 0.15)
POLICIES = ("noisy", "material", "forcing", "shallow", "deep", "unknown")
ADAPTIVE = False
PASSIVE = True
FAST_EVAL = True
EVAL_CACHE = True
DEPTH_EVIDENCE = False
TIGHT_ROOT = False
NORMAL_MARGIN = 15
WINNING_MARGIN = 8
SWINDLE_MARGIN = 25
ROOT_WINDOW_MARGIN = 60
DELTA_MARGIN = 200
EXPLOIT_WEIGHT = 0.20
MAX_BONUS = 12.0
MIN_OBSERVATIONS = 6
MIN_CONFIDENCE = 0.30
MIN_COVERAGE = 0.70
Key = tuple[int, int, int, int, int, int, int, int, bool, int, int | None]


def position_key(board: chess.Board) -> Key:
    return (
        board.pawns,
        board.knights,
        board.bishops,
        board.rooks,
        board.queens,
        board.kings,
        board.occupied_co[chess.WHITE],
        board.occupied_co[chess.BLACK],
        board.turn,
        board.clean_castling_rights(),
        board.ep_square if board.has_legal_en_passant() else None,
    )


def material(board: chess.Board) -> int:
    return sum(
        VALUES[p] * (len(board.pieces(p, board.turn)) - len(board.pieces(p, not board.turn)))
        for p in range(1, 6)
    )


def evaluate(board: chess.Board) -> int:
    """Colour-symmetric tapered evaluation, from the mover's perspective."""
    phase = min(
        24,
        (board.knights | board.bishops).bit_count()
        + 2 * board.rooks.bit_count()
        + 4 * board.queens.bit_count(),
    )
    total = 0
    for colour in (chess.WHITE, chess.BLACK):
        own = board.occupied_co[colour]
        pawns = board.pawns & own
        enemy_pawns = board.pawns & board.occupied_co[not colour]
        king = board.king(colour)
        enemy_king = board.king(not colour)
        score = 0
        files = [(pawns & chess.BB_FILES[f]).bit_count() for f in range(8)]
        for square in chess.scan_forward(own):
            piece = board.piece_type_at(square)
            if piece is None:
                continue
            rank = chess.square_rank(square) if colour else 7 - chess.square_rank(square)
            file = chess.square_file(square)
            centre = 7 - abs(2 * file - 7) - abs(2 * rank - 7)
            score += VALUES[piece]
            if piece == chess.PAWN:
                score += rank * 6 + max(0, centre) * 2
                adjacent = chess.BB_FILES[file - 1] if file else 0
                adjacent |= chess.BB_FILES[file + 1] if file < 7 else 0
                if not pawns & adjacent:
                    score -= 13
                if files[file] > 1:
                    score -= 11
                front = (
                    (chess.BB_ALL << (8 * (chess.square_rank(square) + 1))) & chess.BB_ALL
                    if colour
                    else (1 << (8 * chess.square_rank(square))) - 1
                )
                if not enemy_pawns & (adjacent | chess.BB_FILES[file]) & front:
                    score += (rank * rank * (40 - phase)) // 24
                    if king is not None and enemy_king is not None:
                        score += (
                            (
                                chess.square_distance(enemy_king, square)
                                - chess.square_distance(king, square)
                            )
                            * (24 - phase)
                            // 6
                        )
            elif piece in (chess.KNIGHT, chess.BISHOP):
                score += centre * (5 if piece == chess.KNIGHT else 3)
            elif piece == chess.ROOK:
                if files[file] == 0:
                    score += 12 if enemy_pawns & chess.BB_FILES[file] else 24
                if rank == 6:
                    score += 18
            elif piece == chess.KING:
                score += (centre * 7 * (24 - phase) - centre * 4 * phase) // 24
                if phase > 8:
                    score += (chess.BB_KING_ATTACKS[square] & pawns).bit_count() * phase // 3
            if piece != chess.PAWN:
                attacks = board.attacks_mask(square)
                if piece != chess.KING:
                    score += (attacks & ~own).bit_count() * (2 if piece == chess.QUEEN else 3)
                if enemy_king is not None:
                    pressure = (attacks & chess.BB_KING_ATTACKS[enemy_king]).bit_count()
                    score += pressure * phase // 4
        if (board.bishops & own).bit_count() >= 2:
            score += 28
        total += score if colour == board.turn else -score
    return total


ADJACENT = tuple(
    (chess.BB_FILES[f - 1] if f else 0) | (chess.BB_FILES[f + 1] if f < 7 else 0) for f in range(8)
)
FORWARD = tuple(
    tuple(
        ((chess.BB_ALL << (8 * (s // 8 + 1))) & chess.BB_ALL)
        if colour
        else (1 << (8 * (s // 8))) - 1
        for s in range(64)
    )
    for colour in (False, True)
)
DISTANCE = tuple(tuple(chess.square_distance(a, b) for b in range(64)) for a in range(64))


def placement(phase: int, colour: bool, piece: int, square: int) -> int:
    rank = square // 8 if colour else 7 - square // 8
    centre = 7 - abs(2 * (square % 8) - 7) - abs(2 * rank - 7)
    score = VALUES[piece]
    if piece == chess.PAWN:
        score += rank * 6 + max(0, centre) * 2
    elif piece in (chess.KNIGHT, chess.BISHOP):
        score += centre * (5 if piece == chess.KNIGHT else 3)
    elif piece == chess.ROOK and rank == 6:
        score += 18
    elif piece == chess.KING:
        score += (centre * 7 * (24 - phase) - centre * 4 * phase) // 24
    return score


PLACEMENT = tuple(
    tuple(
        tuple(tuple(placement(phase, colour, p, s) for s in range(64)) for p in range(7))
        for colour in (False, True)
    )
    for phase in range(25)
)


_NUM_PLACEMENT = np.asarray(PLACEMENT, dtype=np.int32)
_NUM_FILES = np.asarray(chess.BB_FILES, dtype=np.uint64)
_NUM_ADJACENT = np.asarray(ADJACENT, dtype=np.uint64)
_NUM_FORWARD = np.asarray(FORWARD, dtype=np.uint64)
_NUM_DISTANCE = np.asarray(DISTANCE, dtype=np.int32)
_NUM_KING = np.asarray(chess.BB_KING_ATTACKS, dtype=np.uint64)
_NUM_KNIGHT = np.asarray(chess.BB_KNIGHT_ATTACKS, dtype=np.uint64)


@njit(cache=False)
def bit_count(bits: np.uint64) -> int:
    count = 0
    while bits:
        bits &= bits - np.uint64(1)
        count += 1
    return count


@njit(cache=False)
def first_square(bits: np.uint64) -> int:
    square = 0
    while not bits & np.uint64(1):
        bits >>= np.uint64(1)
        square += 1
    return square


@njit(cache=False)
def numeric_attacks(piece: int, square: int, occupied: np.uint64) -> np.uint64:
    if piece == 2:
        return np.uint64(_NUM_KNIGHT[square])
    if piece == 6:
        return np.uint64(_NUM_KING[square])
    attacks = np.uint64(0)
    for direction in range(8):
        if (piece == 3 and direction < 4) or (piece == 4 and direction >= 4):
            continue
        dr = (1, -1, 0, 0, 1, 1, -1, -1)[direction]
        df = (0, 0, 1, -1, 1, -1, 1, -1)[direction]
        rank, file = square // 8 + dr, square % 8 + df
        while 0 <= rank < 8 and 0 <= file < 8:
            target = np.uint64(1) << np.uint64(rank * 8 + file)
            attacks |= target
            if occupied & target:
                break
            rank += dr
            file += df
    return attacks


@njit(cache=False)
def numeric_evaluate(
    pawns_bb: np.uint64,
    knights: np.uint64,
    bishops: np.uint64,
    rooks: np.uint64,
    queens: np.uint64,
    kings: np.uint64,
    white: np.uint64,
    turn: bool,
) -> int:
    pieces = (np.uint64(0), pawns_bb, knights, bishops, rooks, queens, kings)
    occupied = pawns_bb | knights | bishops | rooks | queens | kings
    phase = min(24, bit_count(knights | bishops) + 2 * bit_count(rooks) + 4 * bit_count(queens))
    total = 0
    for colour in range(2):
        own = white if colour else occupied ^ white
        pawns = pawns_bb & own
        enemy_pawns = pawns_bb & ~own
        king_bits, enemy_bits = kings & own, kings & ~own
        king = first_square(king_bits) if king_bits else -1
        enemy_king = first_square(enemy_bits) if enemy_bits else -1
        ring = _NUM_KING[enemy_king] if enemy_king >= 0 else np.uint64(0)
        score = 28 if bit_count(bishops & own) >= 2 else 0
        for piece in range(1, 7):
            bits = pieces[piece] & own
            while bits:
                square = first_square(bits)
                bits &= bits - np.uint64(1)
                score += _NUM_PLACEMENT[phase, colour, piece, square]
                file = square % 8
                if piece == 1:
                    adjacent = _NUM_ADJACENT[file]
                    if not pawns & adjacent:
                        score -= 13
                    if bit_count(pawns & _NUM_FILES[file]) > 1:
                        score -= 11
                    if (
                        not enemy_pawns
                        & (adjacent | _NUM_FILES[file])
                        & _NUM_FORWARD[colour, square]
                    ):
                        rank = square // 8 if colour else 7 - square // 8
                        score += rank * rank * (40 - phase) // 24
                        if king >= 0 and enemy_king >= 0:
                            score += (
                                (_NUM_DISTANCE[enemy_king, square] - _NUM_DISTANCE[king, square])
                                * (24 - phase)
                                // 6
                            )
                    continue
                if piece == 4 and not pawns & _NUM_FILES[file]:
                    score += 12 if enemy_pawns & _NUM_FILES[file] else 24
                if piece == 6 and phase > 8:
                    score += bit_count(_NUM_KING[square] & pawns) * phase // 3
                attacks = numeric_attacks(piece, square, occupied)
                if piece != 6:
                    score += bit_count(attacks & ~own) * (2 if piece == 5 else 3)
                score += bit_count(attacks & ring) * phase // 4
        total += score if bool(colour) == turn else -score
    return total


def compiled_evaluate(board: chess.Board) -> int:
    return int(
        numeric_evaluate(
            np.uint64(board.pawns),
            np.uint64(board.knights),
            np.uint64(board.bishops),
            np.uint64(board.rooks),
            np.uint64(board.queens),
            np.uint64(board.kings),
            np.uint64(board.occupied_co[True]),
            board.turn,
        )
    )


# Compile the scalar signature before the game clock starts; no disk cache is needed.
compiled_evaluate(chess.Board())

# ===== NNUE-V2 BLOCK BEGIN =====
# ----------------------------------------------------------------------------------
# NNUE-lite residual correction, V2: a *gated* bounded residual.
#
#     correction = confidence * clamp(residual, +/- NNUE_CLAMP)
#
# V1 shipped the bounded residual alone. It reduced static evaluation error by 27.85 cp
# and still lost 2.88 percentage points of score, because nothing in its training
# objective could see a move ordering and it was free to overturn orderings the
# handcrafted evaluation already had right. V2 adds a third head, trained to predict
# whether applying the residual here actually helps, so a position the network cannot
# read produces a correction near zero and the control's own judgement survives intact.
#
# Why one fused compiled call rather than two: measurement. A trivial njit function with
# this argument list costs about 7.6 us, which is the whole cost of the handcrafted
# evaluation, so the Python-to-Numba boundary dominates and a separate NNUE call would
# roughly double the price of a leaf. A leaf still crosses that boundary exactly once.
#
# Anything wrong with the weight file -- missing, truncated, wrong shape, bad hash --
# leaves `_uncached_evaluate` bound to the control's own function, so the engine
# degrades to exactly the control's behaviour rather than to a broken one.
# ----------------------------------------------------------------------------------

NNUE_HIDDEN = 32
NNUE_CLAMP = 250  # the correction may never move the evaluation more than this
NNUE_WEIGHTS = "nnue_v2_weights.npz"
# Set by tests/nnue_v2_pack.py when the weights are packed. A mismatch degrades the
# agent to the control rather than playing on weights it cannot vouch for.
NNUE_SHA256 = "f2062a0483ad805b01dfee6c9d170652ed7051d8abc27c33c140e1d807353f66"

_NNUE_DEBRUIJN = np.uint64(0x03F79D71B4CB0A89)
_NNUE_INDEX = np.array(
    [
        0, 47, 1, 56, 48, 27, 2, 60, 57, 49, 41, 37, 28, 16, 3, 61,
        54, 58, 35, 52, 50, 42, 21, 44, 38, 32, 29, 23, 17, 11, 4, 62,
        46, 55, 26, 59, 40, 36, 15, 53, 34, 51, 20, 43, 31, 22, 10, 45,
        25, 39, 14, 33, 19, 30, 9, 24, 13, 18, 8, 12, 7, 6, 5, 63,
    ],
    dtype=np.int64,
)

# Populated by load_nnue() BEFORE the fused kernel is compiled. Numba freezes module
# globals at compile time, so the load must happen first; it does, at import.
_NNUE_EMBED = np.zeros((768, NNUE_HIDDEN), dtype=np.int32)
_NNUE_BIAS = np.zeros((16, 25, NNUE_HIDDEN), dtype=np.int32)
_NNUE_MG = np.zeros(NNUE_HIDDEN, dtype=np.int32)
_NNUE_EG = np.zeros(NNUE_HIDDEN, dtype=np.int32)
_NNUE_CONF = np.zeros(NNUE_HIDDEN, dtype=np.int32)
_NNUE_MG_SCALE = 0.0
_NNUE_EG_SCALE = 0.0
_NNUE_CONF_SCALE = 0.0
_NNUE_MG_BIAS = 0.0
_NNUE_EG_BIAS = 0.0
_NNUE_CONF_BIAS = 0.0
_NNUE_GATE = False
_NNUE_PHASE_GATE = False
_NNUE_PHASE_GATE_MAX = 24
_NNUE_CONF_MIN = 0.0
_nnue_ready = False
_nnue_status = "not loaded"


def _nnue_bias_table(aux_w: np.ndarray, aux_b: np.ndarray) -> np.ndarray:
    """Fold the auxiliary layer into a lookup over castling rights and material phase.

    Five of the six auxiliary features are 0/1 and the sixth, material phase, takes 25
    integer values, so the layer collapses to a 16 x 25 x width table built once at
    import. The phase term rounds in integers, (phase * w + 12) // 24, so the table
    reproduces `tests/nnue_v2_train.py:bias_table` bit for bit rather than approximately.
    """
    table = np.zeros((16, 25, NNUE_HIDDEN), dtype=np.int32)
    for combo in range(16):
        bits = (combo & 1, (combo >> 1) & 1, (combo >> 2) & 1, (combo >> 3) & 1)
        for phase_units in range(25):
            for unit in range(NNUE_HIDDEN):
                value = int(aux_b[unit]) + int(aux_w[unit, 0])
                for slot in range(4):
                    if bits[slot]:
                        value += int(aux_w[unit, slot + 1])
                term = int(aux_w[unit, 5]) * phase_units
                value += (term + 12) // 24 if term >= 0 else -((-term + 12) // 24)
                table[combo, phase_units, unit] = value
    return table


def _nnue_weights_path() -> str | None:
    """Find the weight file beside this module.

    On the platform the zip root leads sys.path and `__file__` is always set, so the
    first candidate resolves. The fallbacks exist because the repo's own fixture harness
    execs an agent's source text without setting `__file__`, and a NameError there would
    break the import rather than degrade it.
    """
    candidates = []
    module_file = globals().get("__file__")
    if module_file:
        candidates.append(os.path.dirname(os.path.abspath(module_file)))
    if sys.path and sys.path[0]:
        candidates.append(os.path.abspath(sys.path[0]))
    candidates.append(os.getcwd())
    for directory in candidates:
        candidate = os.path.join(directory, NNUE_WEIGHTS)
        if os.path.exists(candidate):
            return candidate
    return None


def load_nnue() -> str:
    """Load and validate the weights. Any failure keeps the control's exact behaviour."""
    global _NNUE_EMBED, _NNUE_BIAS, _NNUE_MG, _NNUE_EG, _NNUE_CONF
    global _NNUE_MG_SCALE, _NNUE_EG_SCALE, _NNUE_CONF_SCALE
    global _NNUE_MG_BIAS, _NNUE_EG_BIAS, _NNUE_CONF_BIAS
    global _NNUE_GATE, _NNUE_PHASE_GATE, _NNUE_PHASE_GATE_MAX, _NNUE_CONF_MIN
    global _nnue_ready, _nnue_status, NNUE_HIDDEN

    path = _nnue_weights_path()
    if path is None:
        _nnue_status = "weights missing; handcrafted evaluation only"
        return _nnue_status
    try:
        with open(path, "rb") as handle:
            payload = handle.read()
        digest = hashlib.sha256(payload).hexdigest()
        if NNUE_SHA256 and digest != NNUE_SHA256:
            _nnue_status = f"weight hash mismatch ({digest[:16]}); handcrafted only"
            return _nnue_status
        with np.load(path) as data:
            embed = data["embed"]
            aux_w = data["aux_w"]
            aux_b = data["aux_b"]
            mg = data["mg"]
            eg = data["eg"]
            conf = data["conf"]
            scales = data["scales"]
            biases = data["biases"]
            flags = data["flags"]
        # The accumulator width comes from the file, not from a constant, so a narrower
        # or wider trained network ships without editing this source. It is fixed before
        # the kernel is compiled below, and Numba freezes it there.
        if embed.ndim != 2 or embed.shape[0] != 768:
            _nnue_status = "embedding shape mismatch; handcrafted evaluation only"
            return _nnue_status
        width = int(embed.shape[1])
        if not 8 <= width <= 256:
            _nnue_status = f"implausible accumulator width {width}; handcrafted only"
            return _nnue_status
        if aux_w.shape != (width, 6) or aux_b.shape != (width,):
            _nnue_status = "auxiliary shape mismatch; handcrafted evaluation only"
            return _nnue_status
        if mg.shape != (width,) or eg.shape != (width,) or conf.shape != (width,):
            _nnue_status = "head shape mismatch; handcrafted evaluation only"
            return _nnue_status
        if scales.shape != (3,) or biases.shape != (3,) or flags.shape[0] < 3:
            _nnue_status = "scale or flag shape mismatch; handcrafted only"
            return _nnue_status
        if not (np.isfinite(scales).all() and np.isfinite(biases).all()):
            _nnue_status = "non-finite scales; handcrafted evaluation only"
            return _nnue_status
        peak = max(
            int(np.abs(embed).max()),
            int(np.abs(mg).max()),
            int(np.abs(eg).max()),
            int(np.abs(conf).max()),
        )
        if peak > 127:
            _nnue_status = "weights outside int8 range; handcrafted only"
            return _nnue_status
    except Exception as error:  # a bad weight file must never lose a game
        _nnue_status = f"weight load failed ({type(error).__name__}); handcrafted only"
        return _nnue_status

    # int8 on disk and int8 in value; widened to int32 in memory only so the
    # accumulator loop compiles to wider integer adds. No value changes.
    NNUE_HIDDEN = width
    _NNUE_EMBED = np.ascontiguousarray(embed, dtype=np.int32)
    _NNUE_BIAS = _nnue_bias_table(
        np.asarray(aux_w, dtype=np.int64), np.asarray(aux_b, dtype=np.int64)
    )
    _NNUE_MG = np.ascontiguousarray(mg, dtype=np.int32)
    _NNUE_EG = np.ascontiguousarray(eg, dtype=np.int32)
    _NNUE_CONF = np.ascontiguousarray(conf, dtype=np.int32)
    _NNUE_MG_SCALE = float(scales[0])
    _NNUE_EG_SCALE = float(scales[1])
    _NNUE_CONF_SCALE = float(scales[2])
    _NNUE_MG_BIAS = float(biases[0])
    _NNUE_EG_BIAS = float(biases[1])
    _NNUE_CONF_BIAS = float(biases[2])
    _NNUE_GATE = bool(flags[0])
    _NNUE_PHASE_GATE = bool(flags[1])
    _NNUE_PHASE_GATE_MAX = int(flags[2])
    # Fourth flag, in thousandths: a HARD confidence threshold. Below it the
    # correction is exactly zero and the position evaluates bit-identically to
    # the control. Older weight files carry three flags and mean "no threshold".
    _NNUE_CONF_MIN = (float(flags[3]) / 1000.0) if flags.shape[0] > 3 else 0.0
    _nnue_ready = True
    _nnue_status = f"loaded {digest[:16]}"
    return _nnue_status


_nnue_status = load_nnue()


@njit(cache=False, inline="always")
def nnue_popcount(value: np.uint64) -> np.int64:
    """SWAR population count: constant work, no per-bit loop."""
    value = value - ((value >> np.uint64(1)) & np.uint64(0x5555555555555555))
    value = (value & np.uint64(0x3333333333333333)) + (
        (value >> np.uint64(2)) & np.uint64(0x3333333333333333)
    )
    value = (value + (value >> np.uint64(4))) & np.uint64(0x0F0F0F0F0F0F0F0F)
    return np.int64((value * np.uint64(0x0101010101010101)) >> np.uint64(56))


@njit(cache=False)
def nnue_accumulate(
    pawns_bb: np.uint64,
    knights: np.uint64,
    bishops: np.uint64,
    rooks: np.uint64,
    queens: np.uint64,
    kings: np.uint64,
    white: np.uint64,
    turn: bool,
    castling: np.uint64,
) -> tuple[int, int, int, int]:
    """Sparse int8 accumulator. Returns (mg sum, eg sum, confidence sum, phase units).

    Only the rows of the embedding table named by the pieces actually on the board are
    summed. No dense 768-element vector is ever built or multiplied. The confidence head
    reads the same clipped accumulator as the two value heads, so gating costs one more
    multiply-add per unit against a ~2,000-add accumulation -- not a second pass.
    """
    phase_units = int(
        nnue_popcount(knights)
        + nnue_popcount(bishops)
        + 2 * nnue_popcount(rooks)
        + 4 * nnue_popcount(queens)
    )
    if phase_units > 24:
        phase_units = 24

    flip = not turn  # black to move: mirror ranks so the mover is always "us"
    if flip:
        combo = (
            np.int64((castling >> np.uint64(63)) & np.uint64(1))
            | (np.int64((castling >> np.uint64(56)) & np.uint64(1)) << 1)
            | (np.int64((castling >> np.uint64(7)) & np.uint64(1)) << 2)
            | (np.int64((castling >> np.uint64(0)) & np.uint64(1)) << 3)
        )
    else:
        combo = (
            np.int64((castling >> np.uint64(7)) & np.uint64(1))
            | (np.int64((castling >> np.uint64(0)) & np.uint64(1)) << 1)
            | (np.int64((castling >> np.uint64(63)) & np.uint64(1)) << 2)
            | (np.int64((castling >> np.uint64(56)) & np.uint64(1)) << 3)
        )

    acc = np.empty(NNUE_HIDDEN, dtype=np.int32)
    for unit in range(NNUE_HIDDEN):
        acc[unit] = _NNUE_BIAS[combo, phase_units, unit]

    for piece_index in range(6):
        if piece_index == 0:
            board_bb = pawns_bb
        elif piece_index == 1:
            board_bb = knights
        elif piece_index == 2:
            board_bb = bishops
        elif piece_index == 3:
            board_bb = rooks
        elif piece_index == 4:
            board_bb = queens
        else:
            board_bb = kings

        base_us = (piece_index * 2) * 64
        base_them = (piece_index * 2 + 1) * 64
        remaining = board_bb
        while remaining:
            # _NNUE_INDEX is the classic de Bruijn table, which indexes on the FOLDED
            # low bits, bb ^ (bb - 1), not on the isolated low bit bb & -bb. Pairing the
            # table with the isolated bit mis-scanned 63 of 64 squares, so every feature
            # row was wrong. tests/nnue_v2_invariants.py pins both halves of this.
            folded = remaining ^ (remaining - np.uint64(1))
            square = _NNUE_INDEX[(folded * _NNUE_DEBRUIJN) >> np.uint64(58)]
            remaining &= remaining - np.uint64(1)
            is_white = ((white >> np.uint64(square)) & np.uint64(1)) == np.uint64(1)
            oriented = (square ^ 56) if flip else square
            row = (base_us if is_white == turn else base_them) + oriented
            for unit in range(NNUE_HIDDEN):
                acc[unit] += _NNUE_EMBED[row, unit]

    mg_sum = 0
    eg_sum = 0
    conf_sum = 0
    for unit in range(NNUE_HIDDEN):
        value = acc[unit]
        if value < 0:
            value = 0
        elif value > 127:
            value = 127
        mg_sum += value * _NNUE_MG[unit]
        eg_sum += value * _NNUE_EG[unit]
        conf_sum += value * _NNUE_CONF[unit]
    return mg_sum, eg_sum, conf_sum, phase_units


@njit(cache=False)
def numeric_evaluate_v2(
    pawns_bb: np.uint64,
    knights: np.uint64,
    bishops: np.uint64,
    rooks: np.uint64,
    queens: np.uint64,
    kings: np.uint64,
    white: np.uint64,
    turn: bool,
    castling: np.uint64,
) -> tuple[int, int, int, int, int]:
    """The handcrafted evaluation and the accumulator, in one compiled call."""
    base = numeric_evaluate(pawns_bb, knights, bishops, rooks, queens, kings, white, turn)
    mg_sum, eg_sum, conf_sum, phase_units = nnue_accumulate(
        pawns_bb, knights, bishops, rooks, queens, kings, white, turn, castling
    )
    return base, mg_sum, eg_sum, conf_sum, phase_units


def _nnue_blend(mg_sum: int, eg_sum: int, conf_sum: int, phase_units: int) -> int:
    """The deployed correction: confidence times the bounded residual.

    Order matters and is asserted by the gates: clamp first, then gate. Gating a
    pre-clamp residual would let a confident head emit more than NNUE_CLAMP.
    """
    if _NNUE_PHASE_GATE and phase_units > _NNUE_PHASE_GATE_MAX:
        return 0
    phase = phase_units / 24.0
    mg_cp = mg_sum * _NNUE_MG_SCALE / 127.0 + _NNUE_MG_BIAS
    eg_cp = eg_sum * _NNUE_EG_SCALE / 127.0 + _NNUE_EG_BIAS
    residual = mg_cp * phase + eg_cp * (1.0 - phase)
    if residual > NNUE_CLAMP:
        residual = float(NNUE_CLAMP)
    elif residual < -NNUE_CLAMP:
        residual = float(-NNUE_CLAMP)
    if _NNUE_GATE:
        logit = conf_sum * _NNUE_CONF_SCALE / 127.0 + _NNUE_CONF_BIAS
        # A plain logistic. math.exp is guarded so a saturated logit cannot overflow.
        if logit >= 0.0:
            confidence = 1.0 / (1.0 + math.exp(-logit))
        else:
            scaled = math.exp(logit)
            confidence = scaled / (1.0 + scaled)
        # The hard threshold is the point of the design. A soft gate still nudges every
        # leaf by a little, and enough small nudges across a subtree flip a root choice
        # the control had right -- measured, that is what caps the safe correction at
        # about 14 cp. Below the threshold the correction is exactly zero, so the great
        # majority of positions evaluate bit-identically to the control and the network
        # spends its licence only where it claims to know something.
        if confidence < _NNUE_CONF_MIN:
            return 0
        residual *= confidence
    return int(residual)


def nnue_correction(board: chess.Board) -> int:
    """The gated correction alone, for tests and reporting. Not used in search."""
    if not _nnue_ready:
        return 0
    _, mg_sum, eg_sum, conf_sum, phase_units = numeric_evaluate_v2(
        np.uint64(board.pawns),
        np.uint64(board.knights),
        np.uint64(board.bishops),
        np.uint64(board.rooks),
        np.uint64(board.queens),
        np.uint64(board.kings),
        np.uint64(board.occupied_co[True]),
        board.turn,
        np.uint64(board.castling_rights),
    )
    return _nnue_blend(mg_sum, eg_sum, conf_sum, phase_units)


def compiled_evaluate_v2(board: chess.Board) -> int:
    """Handcrafted evaluation plus the gated residual, one boundary crossing."""
    base, mg_sum, eg_sum, conf_sum, phase_units = numeric_evaluate_v2(
        np.uint64(board.pawns),
        np.uint64(board.knights),
        np.uint64(board.bishops),
        np.uint64(board.rooks),
        np.uint64(board.queens),
        np.uint64(board.kings),
        np.uint64(board.occupied_co[True]),
        board.turn,
        np.uint64(board.castling_rights),
    )
    return int(base) + _nnue_blend(mg_sum, eg_sum, conf_sum, phase_units)


# Compile the fused signature before the game clock starts, exactly as the handcrafted
# evaluation above does. Compilation must never land on the first timed move.
numeric_evaluate_v2(
    np.uint64(chess.Board().pawns),
    np.uint64(chess.Board().knights),
    np.uint64(chess.Board().bishops),
    np.uint64(chess.Board().rooks),
    np.uint64(chess.Board().queens),
    np.uint64(chess.Board().kings),
    np.uint64(chess.Board().occupied_co[True]),
    True,
    np.uint64(chess.Board().castling_rights),
)
# ===== NNUE-V2 BLOCK END =====

# ===== NNUE-V2 TAIL BEGIN =====
# The eval cache key gains castling rights, because the residual depends on them and
# the control's key does not distinguish positions that differ only there.
EvalKey = tuple[int, int, int, int, int, int, int, int, bool]
_eval_table: list[tuple[EvalKey, int] | None] = [None] * 32768
fast_evaluate = compiled_evaluate_v2 if _nnue_ready else compiled_evaluate
_uncached_evaluate = fast_evaluate if FAST_EVAL else evaluate
# ===== NNUE-V2 TAIL END =====


def cached_evaluate(board: chess.Board) -> int:
    key = (
        board.pawns,
        board.knights,
        board.bishops,
        board.rooks,
        board.queens,
        board.kings,
        board.occupied_co[True],
        board.castling_rights,
        board.turn,
    )
    slot = hash(key) % len(_eval_table)
    entry = _eval_table[slot]
    if entry is not None and entry[0] == key:
        return entry[1]
    score = _uncached_evaluate(board)
    _eval_table[slot] = (key, score)
    return score


evaluate = cached_evaluate if EVAL_CACHE else _uncached_evaluate


@dataclass(frozen=True)
class Pattern:
    tactical: bool
    closed: bool
    attack: bool
    endgame: bool
    conversion: bool
    swindle: bool


def recognise(board: chess.Board, score: int) -> Pattern:
    nonpawns = (board.occupied & ~board.pawns & ~board.kings).bit_count()
    locked = (
        (board.pawns & board.occupied_co[chess.WHITE]) << 8
        & board.pawns
        & board.occupied_co[chess.BLACK]
    ).bit_count()
    pressure = 0
    for colour in (chess.WHITE, chess.BLACK):
        king = board.king(colour)
        if king is not None:
            pressure += sum(
                board.is_attacked_by(not colour, s)
                for s in chess.scan_forward(chess.BB_KING_ATTACKS[king])
            )
    captures = sum(1 for _ in board.generate_legal_captures())
    return Pattern(
        board.is_check() or captures >= 4,
        locked >= 2,
        pressure >= 4,
        nonpawns <= 4 or not board.queens,
        score > 250,
        score < -250,
    )


def pack_mate(score: int, ply: int) -> int:
    return (
        score + ply
        if score > MATE - MAX_PLY
        else (score - ply if score < -MATE + MAX_PLY else score)
    )


def unpack_mate(score: int, ply: int) -> int:
    return (
        score - ply
        if score > MATE - MAX_PLY
        else (score + ply if score < -MATE + MAX_PLY else score)
    )


@dataclass
class Entry:
    key: Key
    clock: int
    context: int
    depth: int
    bound: int
    score: int
    move: chess.Move
    age: int


@dataclass(frozen=True, slots=True)
class ReplyEvidence:
    move: chess.Move
    gain: int
    forcing: int
    depth: int
    score: int | None
    bound: int


@dataclass(frozen=True, slots=True)
class ReplySet:
    depth: int
    moves: tuple[chess.Move, ...]
    replies: dict[chess.Move, ReplyEvidence]
    shallow: ReplySet | None = None

    @property
    def coverage(self) -> float:
        return sum(r.score is not None for r in self.replies.values()) / max(1, len(self.moves))


@dataclass(frozen=True, slots=True)
class ModelConfidence:
    value: float
    informative: int
    coverage: float
    reliability: float
    entropy: float
    change: float
    posterior: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class Prediction:
    probabilities: tuple[float, ...]
    coverage: float


def distribution(values: list[float], temperature: float) -> list[float]:
    top = max(values)
    weights = [math.exp(max(-30.0, (v - top) / temperature)) for v in values]
    total = sum(weights)
    return [0.96 * w / total + 0.04 / len(values) for w in weights]


def interval_policy(evidence: ReplySet, temperature: float) -> list[float]:
    """Project a neutral likelihood onto the probabilities permitted by score bounds."""
    records = [evidence.replies.get(move) for move in evidence.moves]
    top = max((r.score for r in records if r is not None and r.score is not None), default=0)
    lower: list[float] = []
    upper: list[float] = []
    for reply in records:
        if reply is None or reply.score is None:
            lower.append(0.0)
            upper.append(math.inf)
        else:
            weight = math.exp(max(-30.0, min(30.0, (reply.score - top) / temperature)))
            lower.append(0.0 if reply.bound == -1 else weight)
            upper.append(math.inf if reply.bound == 1 else weight)
    sum_lower = sum(lower)
    finite_upper = sum(v for v in upper if math.isfinite(v))
    infinite = sum(math.isinf(v) for v in upper)
    result = []
    neutral = 1.0 / len(records)
    for lo, hi in zip(lower, upper, strict=True):
        other_infinite = infinite - int(math.isinf(hi))
        other_upper = finite_upper - (hi if math.isfinite(hi) else 0)
        minimum = 0.0 if other_infinite or lo + other_upper == 0 else lo / (lo + other_upper)
        maximum = (
            1.0 if math.isinf(hi) or hi + sum_lower - lo == 0 else (hi / (hi + sum_lower - lo))
        )
        result.append(max(minimum, min(neutral, maximum)))
    total = sum(result)
    return [0.96 * v / total + 0.04 * neutral for v in result]


def policy_likelihoods(evidence: ReplySet) -> list[list[float]]:
    count = len(evidence.moves)
    result = [[1.0 / count] * count]
    for forcing, temperature in ((False, 30.0), (True, 55.0)):
        values = [
            float((r.forcing * 90 + r.gain * 0.2) if forcing else r.gain)
            if (r := evidence.replies.get(move)) is not None
            else 0.0
            for move in evidence.moves
        ]
        probabilities = distribution(values, temperature)
        coverage = len(evidence.replies) / count
        result.append([coverage * p + (1 - coverage) / count for p in probabilities])
    result.append(interval_policy(evidence.shallow or evidence, 100.0))
    result.append(interval_policy(evidence, 25.0) if evidence.depth >= 2 else result[0])
    result.append(
        [0.5 / count + 0.125 * sum(result[k][i] for k in range(1, 5)) for i in range(count)]
    )
    return result


class OpponentModel:
    __slots__ = ("_belief", "_change", "_informative", "_reliability", "_state")

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._belief = tuple(PRIOR)
        self._informative = 0
        self._change = 0.0
        self._reliability = 0.0
        self._state = ModelConfidence(0, 0, 0, 0, 1, 0, self._belief)

    def observe(self, evidence: ReplySet | None, move: chess.Move) -> None:
        if evidence is None or len(evidence.moves) < 3:
            self._decay()
            return
        observed = evidence.replies.get(move)
        if observed is None or observed.score is None:
            self._decay()
            return
        count = len(evidence.moves)
        matrix = policy_likelihoods(evidence)
        informativeness = min(1.0, max(max(p) - min(p) for p in matrix) * count / 2)
        coverage = evidence.coverage
        quality = (
            sum(
                1.0 if r.bound == 0 else 0.4
                for r in evidence.replies.values()
                if r.score is not None
            )
            / count
        )
        reliability = coverage * quality * informativeness
        if reliability < 0.08:
            self._decay()
            return
        index = evidence.moves.index(move)
        likelihoods = [p[index] for p in matrix]
        dominant = max(range(5), key=lambda k: self._belief[k])
        contradiction = self._belief[dominant] > 0.65 and likelihoods[dominant] < 0.15 / count
        self._change = 0.8 * self._change + 0.2 * float(contradiction)
        self._reliability = 0.8 * self._reliability + 0.2 * reliability
        posterior = [
            (0.97 * p + 0.03 * prior) * likelihood**reliability
            for p, prior, likelihood in zip(self._belief, PRIOR, likelihoods, strict=True)
        ]
        if contradiction:
            posterior[5] *= 2.5
        total = sum(posterior)
        self._belief = tuple(p / total for p in posterior)
        self._informative = min(600, self._informative + 1)
        entropy = -sum(p * math.log(p) for p in self._belief) / math.log(6)
        value = (1 - entropy) * (1 - self._belief[5]) * (1 - self._change)
        self._state = ModelConfidence(
            value,
            self._informative,
            coverage,
            self._reliability,
            entropy,
            self._change,
            self._belief,
        )

    def predict(self, evidence: ReplySet) -> Prediction:
        if not evidence.moves:
            return Prediction((), 0)
        matrix = policy_likelihoods(evidence)
        probabilities = tuple(
            sum(self._belief[k] * matrix[k][i] for k in range(6))
            for i in range(len(evidence.moves))
        )
        return Prediction(probabilities, evidence.coverage)

    def confidence(self) -> ModelConfidence:
        return self._state

    def _decay(self) -> None:
        self._belief = tuple(
            0.98 * p + 0.02 * prior for p, prior in zip(self._belief, PRIOR, strict=True)
        )
        entropy = -sum(p * math.log(p) for p in self._belief) / math.log(6)
        self._reliability *= 0.98
        self._change *= 0.95
        value = (1 - entropy) * (1 - self._belief[5]) * (1 - self._change)
        self._state = ModelConfidence(
            value, self._informative, 0, self._reliability, entropy, self._change, self._belief
        )


def safe_margin(pattern: Pattern, score: int) -> int:
    if pattern.tactical or pattern.attack:
        return 0
    return WINNING_MARGIN if score > 250 else SWINDLE_MARGIN if score < -250 else NORMAL_MARGIN


def adaptive_choice(
    scores: dict[chess.Move, int],
    evidence: dict[chess.Move, ReplySet],
    best: chess.Move,
    model: OpponentModel,
    pattern: Pattern,
    time_left_ms: int,
) -> tuple[chess.Move, float]:
    state = model.confidence()
    top = scores[best]
    if (
        state.informative < MIN_OBSERVATIONS
        or state.value <= MIN_CONFIDENCE
        or state.coverage <= MIN_COVERAGE
        or pattern.tactical
        or pattern.attack
        or abs(top) >= MATE - MAX_PLY
        or time_left_ms < 2000
    ):
        return best, 0.0
    margin = safe_margin(pattern, top)
    chosen, utility, chosen_bonus = best, float(top), 0.0
    for move, replies in evidence.items():
        if move not in scores or scores[move] < top - margin or replies.coverage <= MIN_COVERAGE:
            continue
        if any(
            r.score is not None and abs(r.score) >= MATE - MAX_PLY for r in replies.replies.values()
        ):
            continue
        prediction = model.predict(replies)
        expectation = 0.0
        for response, probability in zip(replies.moves, prediction.probabilities, strict=True):
            record = replies.replies.get(response)
            value = float(scores[move])
            # Exact scores and opponent upper bounds establish a conservative value for us.
            if record is not None and record.score is not None and record.bound <= 0:
                value = max(value, float(-record.score))
            expectation += probability * value
        weight = EXPLOIT_WEIGHT * state.value * state.reliability
        bonus = min(MAX_BONUS, max(0.0, weight * (expectation - scores[move])))
        if scores[move] + bonus > utility:
            chosen, utility, chosen_bonus = move, scores[move] + bonus, bonus
    assert chosen in scores and top - scores[chosen] <= margin
    assert 0 <= chosen_bonus <= MAX_BONUS
    return chosen, chosen_bonus


class Deadline(Exception):
    pass


def captures_and_promotions(board: chess.Board) -> list[chess.Move]:
    """The quiescence move set, generated rather than filtered out of every legal move.

    Two masked passes reproduce python-chess's own yield order, so the result is the
    list the unmasked filter produced, move for move. The first pass takes everything
    landing on an enemy piece, which is every capture and every capture promotion.
    Quiet promotions and en passant land on empty squares, so the first pass cannot
    reach them and the second cannot repeat it; both are pawn moves, and the generator
    yields advances before en passant, which is the order the filter saw them in.
    """
    moves = list(board.generate_legal_moves(chess.BB_ALL, board.occupied_co[not board.turn]))
    pawns = board.pawns & board.occupied_co[board.turn]
    if board.turn:
        quiet = (pawns & chess.BB_RANK_7) << 8 & ~board.occupied
    else:
        quiet = (pawns & chess.BB_RANK_2) >> 8 & ~board.occupied
    if board.ep_square:
        quiet |= chess.BB_SQUARES[board.ep_square]
    if quiet:
        moves += board.generate_legal_moves(pawns, quiet)
    return moves


class Engine:
    def __init__(self) -> None:
        self.table: list[Entry | None] = [None] * TT_SIZE
        self.history: dict[tuple[bool, int, int], int] = {}
        self.killers: dict[int, tuple[chess.Move | None, chess.Move | None]] = {}
        self.seen: Counter[Key] = Counter()
        self.context = 0
        self.duplicates = 0
        self.pending: chess.Board | None = None
        self.evidence: ReplySet | None = None
        self.model = OpponentModel()
        self.age = 0
        self.nodes = 0
        self.collect = False
        self.deadline = 0.0
        self.pattern = Pattern(False, False, False, False, False, False)
        self.iteration_evidence: dict[chess.Move, ReplySet] = {}
        self.completed_evidence: dict[chess.Move, ReplySet] = {}
        self.completed_scores: dict[chess.Move, int] = {}
        self.root_move = chess.Move.null()
        self.stats: dict[str, float] = {}

    def tick(self) -> None:
        self.nodes += 1
        if self.nodes % 16 == 0 and time.perf_counter() >= self.deadline:
            raise Deadline

    def enter(self, board: chess.Board) -> Key:
        key = position_key(board)
        old = self.seen[key]
        self.duplicates += int(old == 1)
        self.context ^= hash((key, old)) ^ hash((key, old + 1))
        self.seen[key] += 1
        return key

    def leave(self, key: Key) -> None:
        old = self.seen[key]
        self.duplicates -= int(old == 2)
        self.context ^= hash((key, old)) ^ hash((key, old - 1))
        self.seen[key] -= 1
        if not self.seen[key]:
            del self.seen[key]

    def drawn(self, board: chess.Board) -> bool:
        if (
            board.halfmove_clock >= 100
            or self.seen[position_key(board)] >= 3
            or board.is_insufficient_material()
        ):
            return True
        if board.halfmove_clock < 99 and not self.duplicates:
            return False
        # The referee claims a draw even when it is available on the next move.
        for move in board.legal_moves:
            if board.is_zeroing(move):
                continue
            board.push(move)
            claim = self.seen[position_key(board)] >= 2 or (
                board.halfmove_clock >= 100 and any(board.legal_moves)
            )
            board.pop()
            if claim:
                return True
        return False

    def order(
        self, board: chess.Board, moves: list[chess.Move], preferred: chess.Move | None, ply: int
    ) -> list[chess.Move]:
        killers = self.killers.get(ply, (None, None))

        def priority(move: chess.Move) -> int:
            if move == preferred:
                return 1_000_000
            piece = board.piece_type_at(move.from_square) or 0
            victim = board.piece_type_at(move.to_square) or (1 if board.is_en_passant(move) else 0)
            if victim or move.promotion:
                return 100_000 + 16 * VALUES[victim] - VALUES[piece] + VALUES[move.promotion or 0]
            value = self.history.get((board.turn, move.from_square, move.to_square), 0)
            if move in killers:
                value += 80_000
            if self.pattern.closed and piece == chess.PAWN:
                value += 100
                if (
                    chess.BB_PAWN_ATTACKS[board.turn][move.to_square]
                    & board.occupied_co[not board.turn]
                ):
                    value += 100
            if self.pattern.endgame and piece == chess.KING:
                file, rank = chess.square_file(move.to_square), chess.square_rank(move.to_square)
                value += 14 - abs(2 * file - 7) - abs(2 * rank - 7)
            if (
                ply == 0
                and (self.pattern.tactical or self.pattern.attack)
                and board.gives_check(move)
            ):
                value += 300
            return value

        return sorted(moves, key=priority, reverse=True)

    def quiesce(self, board: chess.Board, alpha: int, beta: int, ply: int) -> int:
        self.tick()
        check = board.is_check()
        moves = list(board.legal_moves) if check else None
        if check and not moves:
            return -MATE + ply
        if self.drawn(board):
            return 0
        if not check and not any(board.generate_legal_moves()):
            return 0
        if ply >= MAX_PLY:
            return evaluate(board)
        if not check:
            stand = evaluate(board)
            if stand >= beta:
                return stand
            alpha = max(alpha, stand)
            if ply < 3 and (self.pattern.tactical or self.pattern.attack):
                moves = [
                    m
                    for m in board.legal_moves
                    if board.is_capture(m) or m.promotion or board.gives_check(m)
                ]
            else:
                moves = captures_and_promotions(board)
        assert moves is not None
        # A capture can lift this node by at most the piece it wins, plus a margin for
        # the positional terms material does not see. One that still cannot reach alpha
        # cannot change the value here, so it is skipped; the result stays a lower
        # bound. Evasions, promotions, quiet checks and endgames are never skipped.
        pruning = not check and not self.pattern.endgame
        for move in self.order(board, moves, None, ply):
            if pruning and not move.promotion:
                victim = board.piece_type_at(move.to_square) or (
                    chess.PAWN if board.is_en_passant(move) else 0
                )
                if victim and stand + VALUES[victim] + DELTA_MARGIN <= alpha:
                    continue
            board.push(move)
            key = self.enter(board)
            try:
                score = -self.quiesce(board, -beta, -alpha, ply + 1)
            finally:
                self.leave(key)
                board.pop()
            if score >= beta:
                return score
            alpha = max(alpha, score)
        return alpha

    def search(self, board: chess.Board, depth: int, alpha: int, beta: int, ply: int) -> int:
        if depth <= 0:
            return self.quiesce(board, alpha, beta, ply)
        self.tick()
        moves = list(board.legal_moves)
        if not moves:
            return -MATE + ply if board.is_check() else 0
        if self.drawn(board):
            return 0
        if ply >= MAX_PLY:
            return evaluate(board)
        frame = None
        if self.collect and ply == 1:
            capture_start = time.perf_counter()
            prior_frame = self.completed_evidence.get(self.root_move) if DEPTH_EVIDENCE else None
            shallow = (prior_frame.shallow or prior_frame) if prior_frame is not None else None
            frame = ReplySet(depth, tuple(moves), {}, shallow)
            self.iteration_evidence[self.root_move] = frame
            self.stats["model_seconds"] += time.perf_counter() - capture_start
        key = position_key(board)
        slot = hash(key) % TT_SIZE
        entry = self.table[slot]
        context = self.context ^ hash(
            (self.pattern.tactical or self.pattern.attack, max(0, 3 - ply))
        )
        preferred = None
        if entry is not None and entry.key == key:
            preferred = entry.move
            if (
                entry.depth >= depth
                and entry.clock == board.halfmove_clock
                and entry.context == context
            ):
                value = unpack_mate(entry.score, ply)
                if (
                    entry.bound == 0
                    or (entry.bound == 1 and value >= beta)
                    or (entry.bound == -1 and value <= alpha)
                ):
                    return value
        original = alpha
        best = -INF
        best_move = moves[0]
        for index, move in enumerate(self.order(board, moves, preferred, ply)):
            quiet = not board.is_capture(move) and not move.promotion
            gain = 0
            if frame is not None:
                capture_start = time.perf_counter()
                victim = board.piece_type_at(move.to_square) or (
                    chess.PAWN if board.is_en_passant(move) else 0
                )
                gain = VALUES[victim] + (VALUES[move.promotion] - 100 if move.promotion else 0)
                self.stats["model_seconds"] += time.perf_counter() - capture_start
            board.push(move)
            forcing = 0
            if frame is not None:
                capture_start = time.perf_counter()
                forcing = 3 * int(board.is_check()) + int(not quiet) + int(bool(move.promotion))
                self.stats["model_seconds"] += time.perf_counter() - capture_start
            child = self.enter(board)
            try:
                if index == 0:
                    score = -self.search(board, depth - 1, -beta, -alpha, ply + 1)
                else:
                    score = -self.search(board, depth - 1, -alpha - 1, -alpha, ply + 1)
                    if alpha < score < beta:
                        score = -self.search(board, depth - 1, -beta, -alpha, ply + 1)
            finally:
                self.leave(child)
                board.pop()
            if frame is not None:
                capture_start = time.perf_counter()
                bound = -1 if score <= alpha else 1 if score >= beta else 0
                frame.replies[move] = ReplyEvidence(move, gain, forcing, depth, score, bound)
                self.stats["model_seconds"] += time.perf_counter() - capture_start
            if score > best:
                best, best_move = score, move
            alpha = max(alpha, score)
            if alpha >= beta:
                if quiet:
                    previous = self.killers.get(ply, (None, None))
                    if move != previous[0]:
                        self.killers[ply] = (move, previous[0])
                    hkey = (board.turn, move.from_square, move.to_square)
                    self.history[hkey] = min(20_000, self.history.get(hkey, 0) + depth * depth)
                break
        bound = -1 if best <= original else (1 if best >= beta else 0)
        if entry is None or entry.age != self.age or depth >= entry.depth:
            self.table[slot] = Entry(
                key,
                board.halfmove_clock,
                context,
                depth,
                bound,
                pack_mate(best, ply),
                best_move,
                self.age,
            )
        return best

    def reconstruct(self, board: chess.Board) -> str | None:
        if self.pending is None:
            return None
        target = position_key(board)
        for move in self.pending.legal_moves:
            self.pending.push(move)
            matches = (
                position_key(self.pending) == target
                and self.pending.halfmove_clock == board.halfmove_clock
                and self.pending.fullmove_number == board.fullmove_number
            )
            self.pending.pop()
            if matches:
                return move.uci()
        return None

    def choose(self, fen: str, time_left_ms: int) -> str:
        started = time.perf_counter()
        board = chess.Board(fen)
        fallback = next(iter(board.legal_moves), None)
        if fallback is None:
            return "0000"
        self.stats = {"depth": 0, "model_seconds": 0, "adapted": 0, "nodes": 0}
        if time_left_ms <= 10:
            self.pending = None
            self.stats["seconds"] = time.perf_counter() - started
            return fallback.uci()
        available = time_left_ms / 1000
        budget = min(available / 32, max(0.001, available - 0.025))
        self.deadline = started + budget * 0.96
        soft = started + budget * 0.65
        self.nodes = 0
        self.age += 1
        observed = self.reconstruct(board)
        if observed is None:
            self.seen.clear()
            self.context = 0
            self.duplicates = 0
            self.model.reset()
        elif PASSIVE:
            model_start = time.perf_counter()
            self.model.observe(self.evidence, chess.Move.from_uci(observed))
            self.stats["model_seconds"] += time.perf_counter() - model_start
        self.enter(board)
        self.history = {key: value // 2 for key, value in self.history.items() if value > 1}
        self.killers.clear()
        self.iteration_evidence = {}
        self.completed_evidence = {}
        self.completed_scores = {}
        self.pattern = recognise(board, evaluate(board))
        moves = self.order(board, list(board.legal_moves), None, 0)
        chosen = fallback
        completed: dict[chess.Move, int] = {}
        for depth in range(1, 65):
            current: dict[chess.Move, int] = {}
            self.iteration_evidence = {}
            leaders = moves[:3]
            root_best = -INF
            tolerance = 0 if TIGHT_ROOT and not ADAPTIVE else ROOT_WINDOW_MARGIN
            try:
                for move in moves:
                    if time.perf_counter() >= self.deadline:
                        raise Deadline
                    self.root_move = move
                    self.collect = PASSIVE and move in leaders
                    board.push(move)
                    key = self.enter(board)
                    try:
                        # Inferior moves need only an upper bound below the safe set.
                        floor = max(-INF, root_best - tolerance - 1)
                        current[move] = -self.search(board, depth - 1, -INF, -floor, 1)
                        root_best = max(root_best, current[move])
                    finally:
                        self.leave(key)
                        board.pop()
            except Deadline:
                break
            completed = current
            self.completed_scores = current
            self.completed_evidence = self.iteration_evidence
            moves.sort(
                key=lambda m: (
                    completed[m],
                    int(not board.is_capture(m)) if self.pattern.swindle else 0,
                ),
                reverse=True,
            )
            chosen = moves[0]
            self.stats["depth"] = depth
            if time.perf_counter() >= soft or abs(completed[chosen]) > MATE - MAX_PLY:
                break
        self.iteration_evidence = {}
        self.collect = False
        model_start = time.perf_counter()
        bonus = 0.0
        if ADAPTIVE and completed:
            chosen, bonus = adaptive_choice(
                completed, self.completed_evidence, chosen, self.model, self.pattern, time_left_ms
            )
        self.evidence = self.completed_evidence.get(chosen)
        state = self.model.confidence()
        self.stats["coverage"] = self.evidence.coverage if self.evidence is not None else 0.0
        self.stats["informative"] = state.informative
        self.stats["confidence"] = state.value
        self.stats["bonus"] = bonus
        self.stats["cp_loss"] = max(completed.values()) - completed[chosen] if completed else 0
        self.stats["limit"] = safe_margin(self.pattern, max(completed.values())) if completed else 0
        self.stats["adapted"] = float(bool(completed) and chosen != moves[0])
        self.stats["model_seconds"] += time.perf_counter() - model_start
        return self.finish(board, chosen, started)

    def finish(self, board: chess.Board, chosen: chess.Move, started: float) -> str:
        board.push(chosen)
        if board.halfmove_clock == 0:
            self.seen.clear()
            self.context = 0
            self.duplicates = 0
        self.enter(board)
        self.pending = board
        self.stats["seconds"] = time.perf_counter() - started
        self.stats["nodes"] = self.nodes
        return chosen.uci()


_engine = Engine()


def get_move(fen: str, time_left_ms: int) -> str:
    """Return a legal UCI move; persistent state belongs to this game only."""
    return _engine.choose(fen, time_left_ms)
