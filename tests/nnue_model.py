"""Feature extraction and the float model definition for the NNUE-lite residual.

Shared by training, quantization and the candidate agent, so the features the network
is trained on and the features it is served cannot drift apart. The agent ships its own
copy of the extraction logic; this module is the reference the tests compare against.

Orientation is canonical and player-relative: the side to move is always "us", and when
black is to move the board is mirrored vertically and the colours are swapped. The
label is already side-to-move relative, so the network never has to learn a sign.
"""

from __future__ import annotations

import chess
import numpy as np

NUM_SQUARES = 64
NUM_PLANES = 12  # 6 piece types x {us, them}
NUM_FEATURES = NUM_PLANES * NUM_SQUARES  # 768
NUM_AUX = 6  # side-to-move flag, 4 castling rights, material phase
HIDDEN = 64

# The 0-24 material phase scale the control uses, so the blend matches the engine.
PHASE_WEIGHTS = {chess.KNIGHT: 1, chess.BISHOP: 1, chess.ROOK: 2, chess.QUEEN: 4}
PHASE_MAX = 24


def material_phase(board: chess.Board) -> int:
    total = 0
    for piece_type, weight in PHASE_WEIGHTS.items():
        total += weight * (
            len(board.pieces(piece_type, chess.WHITE))
            + len(board.pieces(piece_type, chess.BLACK))
        )
    return min(total, PHASE_MAX)


def active_features(board: chess.Board) -> tuple[np.ndarray, np.ndarray]:
    """Return (indices of active piece-square features, auxiliary vector).

    Only the indices are returned. The dense 768-vector is never built.
    """
    us = board.turn
    flip = us == chess.BLACK
    indices = np.empty(len(board.piece_map()), dtype=np.int32)
    count = 0
    for square, piece in board.piece_map().items():
        oriented = square ^ 56 if flip else square
        mine = piece.color == us
        plane = (piece.piece_type - 1) * 2 + (0 if mine else 1)
        indices[count] = plane * NUM_SQUARES + oriented
        count += 1
    indices = indices[:count]

    if flip:
        castling = (
            board.has_kingside_castling_rights(chess.BLACK),
            board.has_queenside_castling_rights(chess.BLACK),
            board.has_kingside_castling_rights(chess.WHITE),
            board.has_queenside_castling_rights(chess.WHITE),
        )
    else:
        castling = (
            board.has_kingside_castling_rights(chess.WHITE),
            board.has_queenside_castling_rights(chess.WHITE),
            board.has_kingside_castling_rights(chess.BLACK),
            board.has_queenside_castling_rights(chess.BLACK),
        )
    aux = np.array(
        [
            1.0,  # constant "side to move is us" marker, kept for parity with the spec
            float(castling[0]),
            float(castling[1]),
            float(castling[2]),
            float(castling[3]),
            material_phase(board) / PHASE_MAX,
        ],
        dtype=np.float32,
    )
    return indices, aux


def phase_blend(board: chess.Board) -> float:
    """1.0 in a full-material middlegame, 0.0 in a bare endgame."""
    return material_phase(board) / PHASE_MAX


def build_torch_model():  # pragma: no cover - imported lazily by the trainer
    import torch
    from torch import nn

    class Residual(nn.Module):
        """768-sparse + 6-dense -> 64 clipped ReLU -> two heads blended by phase."""

        def __init__(self) -> None:
            super().__init__()
            self.embed = nn.Embedding(NUM_FEATURES, HIDDEN)
            self.aux = nn.Linear(NUM_AUX, HIDDEN, bias=True)
            self.head_mg = nn.Linear(HIDDEN, 1, bias=True)
            self.head_eg = nn.Linear(HIDDEN, 1, bias=True)
            nn.init.normal_(self.embed.weight, std=0.01)
            nn.init.zeros_(self.head_mg.weight)
            nn.init.zeros_(self.head_eg.weight)
            nn.init.zeros_(self.head_mg.bias)
            nn.init.zeros_(self.head_eg.bias)

        def accumulate(self, indices, mask, aux):
            # indices: (B, P) padded, mask: (B, P) 1 for real features
            rows = self.embed(indices) * mask.unsqueeze(-1)
            return rows.sum(dim=1) + self.aux(aux)

        def forward(self, indices, mask, aux, phase):
            hidden = torch.clamp(self.accumulate(indices, mask, aux), 0.0, 1.0)
            mg = self.head_mg(hidden).squeeze(-1)
            eg = self.head_eg(hidden).squeeze(-1)
            return mg * phase + eg * (1.0 - phase)

        def clip_weights(self) -> None:
            """Keep the first layer inside the int8 range the quantizer assumes."""
            with torch.no_grad():
                self.embed.weight.clamp_(-1.0, 1.0)
                self.aux.weight.clamp_(-1.0, 1.0)
                self.aux.bias.clamp_(-1.0, 1.0)

    return Residual


def parameter_count() -> int:
    return (
        NUM_FEATURES * HIDDEN  # embedding
        + NUM_AUX * HIDDEN  # auxiliary projection
        + HIDDEN  # auxiliary bias
        + 2 * HIDDEN  # two heads
        + 2  # two head biases
    )
