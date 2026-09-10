"""The relative correction form, and its reference integer implementation.

V1 and V2 both shipped an *additive* correction: `eval = control + clamp(residual)`.
V2's frontier table showed why that caps out. The first solved fixture to break, at the
smallest correction that breaks anything, is always `r77-33-Rc7+`, a repetition defence,
because holding a draw means holding an evaluation *at* zero and an additive nudge of
any size flips a comparison between two equal numbers.

Measured on the 28,640-group corpus, that fragile region is also where almost none of
the available improvement lives:

| control's own evaluation of its pick | groups | share of all control regret |
| --- | ---: | ---: |
| under 50 cp | 2,099 | 9.0 % |
| 50-150 cp | 4,239 | 16.0 % |
| 150-400 cp | 7,797 | 31.3 % |
| 400 cp and above | 14,505 | 43.7 % |

91 % of the control's regret sits in positions the control does *not* think are equal.
So a correction whose size is proportional to the control's own evaluation is aimed at
where the value is and is structurally silent where the danger is:

    correction = trunc(base * gain / 1024),  clamped to +/- NNUE_CLAMP

with `gain` the same bounded, gated integer the additive form deploys. If the control
evaluates a position at exactly zero the correction is exactly zero -- not small, zero --
so a held draw cannot be nudged off equality, and the sign of an evaluation can never
flip while |gain| < 1024.

This module is the reference. The agent's Numba path must equal it exactly, and the gate
compares integers so "agrees to within a rounding step" cannot hide in it.
"""

from __future__ import annotations

import numpy as np

# The denominator is a power of two so the scaling is exact in both integer and binary
# floating-point arithmetic, which is what lets the reference and the agent agree bit for
# bit rather than approximately.
RELATIVE_UNIT = 1024
# With |gain| <= 250 the correction can move an evaluation by at most 250/1024 = 24.4 %
# of itself, and never across zero.
MAX_RELATIVE_FRACTION = 250.0 / RELATIVE_UNIT


def apply_relative(base: np.ndarray, gain: np.ndarray, clamp: int) -> np.ndarray:
    """Reference integer form of `trunc(base * gain / 1024)`, clamped.

    `base * gain` is an exact integer well inside float64's exact range, and dividing by
    a power of two is exact, so `np.trunc` here reproduces integer truncation toward zero
    rather than approximating it.
    """
    value = np.trunc(base * gain / RELATIVE_UNIT)
    return np.clip(value, -clamp, clamp)


def relative_torch(base, gain, clamp: int):  # type: ignore[no-untyped-def]
    """Differentiable counterpart used during training.

    Deliberately *not* truncated: truncation has zero gradient almost everywhere, so the
    trainer optimises the continuous form and every validation and selection metric is
    then recomputed through the truncated reference above. A checkpoint is therefore
    never chosen on behaviour it will not exhibit.
    """
    import torch

    return torch.clamp(base * gain / RELATIVE_UNIT, -float(clamp), float(clamp))
