"""The V2 model: a bounded residual multiplied by a calibrated confidence.

V1 shipped `eval = control + clamp(residual)`. It was free to move any evaluation by up
to the clamp, including in the positions the control already ordered correctly, and it
broke five solved fixtures doing exactly that.

V2 ships

    correction = confidence * clamp(residual, +/- CLAMP)

with `confidence` in [0, 1] predicted by a third head sharing the same accumulator. The
network must therefore *earn* the right to move an evaluation: the confidence head is
trained against the detached indicator that applying the correction actually reduced
error without damaging sibling order, so a position the network cannot read produces a
correction near zero and the control's own judgement survives untouched.

Feature extraction is imported unchanged from the V1 module. It is already canonical and
player-relative, it was proved a no-op under colour swap on 2,000/2,000 positions, and
the de Bruijn scan bug in it was found and fixed during V1. Re-deriving it here would
only create a second thing that can drift.
"""

from __future__ import annotations

from typing import Any

from nnue_model import (  # re-exported so V2 callers have one import site
    NUM_AUX,
    NUM_FEATURES,
    NUM_PLANES,
    NUM_SQUARES,
    PHASE_MAX,
    active_features,
    material_phase,
    phase_blend,
)

__all__ = [
    "NUM_AUX",
    "NUM_FEATURES",
    "NUM_PLANES",
    "NUM_SQUARES",
    "PHASE_MAX",
    "active_features",
    "build_v2_model",
    "material_phase",
    "parameter_count",
    "phase_blend",
]

HIDDEN = 32
CLAMP_CP = 250


def build_v2_model(hidden: int = HIDDEN, clamp_cp: int = CLAMP_CP) -> Any:
    import torch
    from torch import nn

    class GatedResidual(nn.Module):
        """768-sparse + 6-dense -> clipped ReLU -> {mg, eg, confidence}.

        The two value heads are phase-blended exactly as V1 did, so the residual half of
        this model is directly comparable to the rejected candidate. The confidence head
        is new and is the whole point.
        """

        def __init__(self) -> None:
            super().__init__()
            self.hidden = hidden
            self.clamp_cp = float(clamp_cp)
            self.embed = nn.Embedding(NUM_FEATURES, hidden)
            self.aux = nn.Linear(NUM_AUX, hidden, bias=True)
            self.head_mg = nn.Linear(hidden, 1, bias=True)
            self.head_eg = nn.Linear(hidden, 1, bias=True)
            self.head_conf = nn.Linear(hidden, 1, bias=True)

            # V1 established these by measurement, not taste: a first attempt used
            # std=0.01 with zeroed heads, the accumulator sat at ~0, half the clipped
            # ReLU units were dead with no gradient, and six epochs moved the loss by
            # 0.04 cp. A position activates about 32 embedding rows, so std=0.5/sqrt(32)
            # centres the accumulator in the live part of the [0, 1] clip.
            nn.init.normal_(self.embed.weight, std=0.5 / (32**0.5))
            nn.init.normal_(self.aux.weight, std=0.05)
            nn.init.constant_(self.aux.bias, 0.5)
            nn.init.normal_(self.head_mg.weight, std=0.1)
            nn.init.normal_(self.head_eg.weight, std=0.1)
            nn.init.zeros_(self.head_mg.bias)
            nn.init.zeros_(self.head_eg.bias)
            # Confidence starts near 0.5 rather than near 1. An untrained gate that is
            # wide open reproduces V1's failure mode for the whole first epoch.
            nn.init.normal_(self.head_conf.weight, std=0.05)
            nn.init.zeros_(self.head_conf.bias)

        def accumulate(
            self, indices: Any, mask: Any, aux: Any
        ) -> Any:  # (B, P), (B, P), (B, NUM_AUX)
            rows = self.embed(indices) * mask.unsqueeze(-1)
            return rows.sum(dim=1) + self.aux(aux)

        def raw(self, indices: Any, mask: Any, aux: Any, phase: Any) -> tuple[Any, Any]:
            """Return (unbounded residual in cp, confidence in [0, 1])."""

            hidden_act = torch.clamp(self.accumulate(indices, mask, aux), 0.0, 1.0)
            mg = self.head_mg(hidden_act).squeeze(-1)
            eg = self.head_eg(hidden_act).squeeze(-1)
            residual = mg * phase + eg * (1.0 - phase)
            confidence = torch.sigmoid(self.head_conf(hidden_act).squeeze(-1))
            return residual, confidence

        def forward(self, indices: Any, mask: Any, aux: Any, phase: Any) -> Any:
            """The deployed correction: confidence * bounded residual."""

            residual, confidence = self.raw(indices, mask, aux, phase)
            bounded = torch.clamp(residual, -self.clamp_cp, self.clamp_cp)
            return confidence * bounded

        def clip_weights(self) -> None:
            """Keep the first layer inside the int8 range the quantizer assumes."""

            with torch.no_grad():
                self.embed.weight.clamp_(-1.0, 1.0)
                self.aux.weight.clamp_(-1.0, 1.0)
                self.aux.bias.clamp_(-1.0, 1.0)

    return GatedResidual


def parameter_count(hidden: int = HIDDEN) -> int:
    return (
        NUM_FEATURES * hidden  # embedding
        + NUM_AUX * hidden  # auxiliary projection
        + hidden  # auxiliary bias
        + 3 * hidden  # mg, eg and confidence heads
        + 3  # three head biases
    )
