"""V2 trainer: move quality first, static MAE last.

V1 minimised |stockfish - (control + correction)| and won that game by 27.85 cp while
losing 2.88 percentage points of actual score. The loss it minimised could not see a
move ordering, so it had no reason not to destroy one.

This trainer optimises the quantity the search actually consumes -- the order of sibling
children at a node -- under an anchor that makes breaking an ordering the control
already gets right cost more than fixing a broken one gains. Checkpoint selection uses a
composite score in which preservation dominates and MAE is the smallest term. A
checkpoint is never selected because its MAE is best; that is precisely the mistake
under repair.

Sign conventions, verified in `tests/nnue_v2_data.py` against the V1 labels:

- every value is side-to-move relative;
- a child's evaluation is from the *opponent's* point of view, so the mover prefers the
  smallest child evaluation;
- regret is parent-perspective and non-negative, so the mover prefers the smallest.

Both therefore sort the same way, which is what makes the ranking hinge well posed.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import random
import sys
import time
from typing import Any

import chess
import numpy as np

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))

import nnue_v2_model as nn_features  # noqa: E402

GROUPS = REPO / "tests" / "results" / "nnue" / "groups"
OUTROOT = REPO / "tests" / "results" / "nnue" / "v2"
MAX_PIECES = 32

# Same parameterisation V1 established by measurement: the network trains in units of
# OUTPUT_SCALE centipawns so that ordinary learning rates apply, and the scale equals
# the inference clamp so a saturated output is exactly the largest accepted correction.
OUTPUT_SCALE = 250.0
CORRECTION_CLAMP = 250

# A move within this much of Stockfish's best is treated as "already correct". The
# preservation gate is defined against it, so it is stated once, here.
NEAR_BEST_CP = 25
# Stockfish calls the position equal within this band.
DRAW_BAND_CP = 40
# Corrections are suppressed above this material phase in the phase-gated variant.
# 24 is a full board; rated games start from curated openings, where a static residual
# changes the fewest root decisions and V1 nonetheless made its largest change.
PHASE_GATE_MAX = 20


def set_seeds(seed: int) -> None:
    import torch

    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.use_deterministic_algorithms(True, warn_only=True)


# --------------------------------------------------------------------------- encoding


def load_groups() -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    for path in sorted(GROUPS.glob("groups-*.jsonl")):
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    groups.append(json.loads(line))
    return groups


def encode_groups(groups: list[dict[str, Any]], cache: pathlib.Path) -> dict[str, Any]:
    """Flatten groups into child-level arrays plus group offsets.

    Feature extraction through python-chess is the slow part, so the result is cached;
    the cache is keyed on the group count and dropped whenever that changes.
    """
    if cache.exists():
        blob = np.load(cache, allow_pickle=False)
        if int(blob["n_groups"]) == len(groups):
            print(f"reusing encoded cache {cache.name}")
            return {k: blob[k] for k in blob.files}

    total = sum(len(g["candidates"]) for g in groups)
    indices = np.zeros((total, MAX_PIECES), dtype=np.int64)
    mask = np.zeros((total, MAX_PIECES), dtype=np.float32)
    aux = np.zeros((total, nn_features.NUM_AUX), dtype=np.float32)
    phase = np.zeros(total, dtype=np.float32)
    phase_units = np.zeros(total, dtype=np.int64)
    base = np.zeros(total, dtype=np.float32)
    sf = np.zeros(total, dtype=np.float32)
    target = np.zeros(total, dtype=np.float32)
    regret = np.zeros(total, dtype=np.float32)
    quiet = np.zeros(total, dtype=np.float32)
    queen_trade = np.zeros(total, dtype=np.float32)
    offsets = np.zeros(len(groups) + 1, dtype=np.int64)

    cursor = 0
    started = time.perf_counter()
    for gi, group in enumerate(groups):
        offsets[gi] = cursor
        for cand in group["candidates"]:
            board = chess.Board(cand["child_fen"])
            idx, a = nn_features.active_features(board)
            k = min(len(idx), MAX_PIECES)
            indices[cursor, :k] = idx[:k]
            mask[cursor, :k] = 1.0
            aux[cursor] = a
            units = nn_features.material_phase(board)
            phase_units[cursor] = units
            phase[cursor] = units / nn_features.PHASE_MAX
            base[cursor] = float(cand["control_static_child"])
            sf[cursor] = float(cand["sf_cp_child"])
            target[cursor] = float(cand["residual_target"])
            regret[cursor] = float(cand["regret"])
            quiet[cursor] = 1.0 if cand["quiet"] else 0.0
            queen_trade[cursor] = 1.0 if cand["queen_trade"] else 0.0
            cursor += 1
        if gi % 5000 == 0 and gi:
            print(f"  encoded {gi}/{len(groups)} groups", flush=True)
    offsets[len(groups)] = cursor

    out = {
        "indices": indices,
        "mask": mask,
        "aux": aux,
        "phase": phase,
        "phase_units": phase_units,
        "base": base,
        "sf": sf,
        "target": target,
        "regret": regret,
        "quiet": quiet,
        "queen_trade": queen_trade,
        "offsets": offsets,
        "n_groups": np.array(len(groups)),
    }
    cache.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(cache, **out)
    print(f"encoded {total} children in {time.perf_counter() - started:.1f}s")
    return out


def build_all_pairs(enc: dict[str, Any]) -> dict[str, np.ndarray]:
    """Every ordered sibling pair (better, worse), once, with per-group slices.

    A pair exists when two children of the same parent have different Stockfish regret.
    `anchor` marks the pairs the control's own static evaluation already orders
    correctly -- exactly the pairs V1 was free to destroy.

    Built once rather than per batch: at 40k groups the per-batch Python construction
    dominated training time, and nothing about a pair changes between epochs.
    """
    offsets = enc["offsets"]
    regret = enc["regret"]
    base = enc["base"]
    n_groups = len(offsets) - 1
    better: list[np.ndarray] = []
    worse: list[np.ndarray] = []
    pair_offsets = np.zeros(n_groups + 1, dtype=np.int64)
    cursor = 0
    for gi in range(n_groups):
        lo, hi = int(offsets[gi]), int(offsets[gi + 1])
        r = regret[lo:hi]
        i, j = np.nonzero(r[:, None] < r[None, :])
        pair_offsets[gi] = cursor
        cursor += len(i)
        better.append(i.astype(np.int64) + lo)
        worse.append(j.astype(np.int64) + lo)
    pair_offsets[n_groups] = cursor
    b = np.concatenate(better) if better else np.zeros(0, dtype=np.int64)
    w = np.concatenate(worse) if worse else np.zeros(0, dtype=np.int64)
    anchor = (base[b] < base[w]).astype(np.float32)
    # Margins are fixed properties of the labels, so they are precomputed too.
    gap = np.minimum(regret[w] - regret[b], CORRECTION_CLAMP).astype(np.float32)
    ctrl_gap = np.minimum(base[w] - base[b], CORRECTION_CLAMP).astype(np.float32)
    return {
        "better": b,
        "worse": w,
        "anchor": anchor,
        "gap": gap,
        "ctrl_gap": ctrl_gap,
        "offsets": pair_offsets,
    }


# ------------------------------------------------------------------------- evaluation


def group_metrics(
    correction: np.ndarray, enc: dict[str, Any], group_ids: np.ndarray
) -> dict[str, float]:
    """Everything the composite selection score is built from."""
    offsets, regret, base = enc["offsets"], enc["regret"], enc["base"]
    deployed = base + correction

    ctrl_regret: list[float] = []
    cand_regret: list[float] = []
    preserved_total = 0
    preserved_kept = 0
    repaired = 0
    broken = 0
    pair_ok_ctrl = 0
    pair_ok_cand = 0
    pair_n = 0

    for gi in group_ids:
        lo, hi = int(offsets[gi]), int(offsets[gi + 1])
        r = regret[lo:hi]
        b = base[lo:hi]
        d = deployed[lo:hi]
        ci = int(np.argmin(b))
        di = int(np.argmin(d))
        ctrl_regret.append(float(r[ci]))
        cand_regret.append(float(r[di]))
        was_ok = r[ci] <= NEAR_BEST_CP
        now_ok = r[di] <= NEAR_BEST_CP
        if was_ok:
            preserved_total += 1
            if now_ok:
                preserved_kept += 1
            else:
                broken += 1
        elif now_ok:
            repaired += 1
        for i in range(hi - lo):
            for j in range(hi - lo):
                if r[i] < r[j]:
                    pair_n += 1
                    pair_ok_ctrl += int(b[i] < b[j])
                    pair_ok_cand += int(d[i] < d[j])

    return {
        "groups": float(len(group_ids)),
        "control_regret_mean": float(np.mean(ctrl_regret)),
        "candidate_regret_mean": float(np.mean(cand_regret)),
        "regret_reduction_cp": float(np.mean(ctrl_regret) - np.mean(cand_regret)),
        "preserved_rate": float(preserved_kept / preserved_total)
        if preserved_total
        else 1.0,
        "preserved_total": float(preserved_total),
        "broken": float(broken),
        "repaired": float(repaired),
        "pair_accuracy_control": float(pair_ok_ctrl / pair_n) if pair_n else 0.0,
        "pair_accuracy_candidate": float(pair_ok_cand / pair_n) if pair_n else 0.0,
        "pair_accuracy_gain": float((pair_ok_cand - pair_ok_ctrl) / pair_n)
        if pair_n
        else 0.0,
    }


def value_metrics(
    correction: np.ndarray, enc: dict[str, Any], rows: np.ndarray
) -> dict[str, float]:
    base, sf = enc["base"][rows], enc["sf"][rows]
    corr = correction[rows]
    mae_ctrl = float(np.mean(np.abs(base - sf)))
    mae_cand = float(np.mean(np.abs(base + corr - sf)))

    drawish = np.abs(sf) <= DRAW_BAND_CP
    if drawish.any():
        allow = np.maximum(np.abs(base[drawish]), np.abs(sf[drawish]))
        excess = np.maximum(np.abs(base[drawish] + corr[drawish]) - allow, 0.0)
        draw_ok = float(np.mean(excess <= 1.0))
        draw_excess = float(np.mean(excess))
    else:
        draw_ok, draw_excess = 1.0, 0.0

    return {
        "mae_control": mae_ctrl,
        "mae_candidate": mae_cand,
        "mae_gain_cp": mae_ctrl - mae_cand,
        "draw_preservation_rate": draw_ok,
        "draw_excess_cp": draw_excess,
        "mean_abs_correction": float(np.mean(np.abs(corr))),
        "correction_bias_cp": float(np.mean(corr)),
    }


def composite(metrics: dict[str, float]) -> float:
    """The selection score. Preservation dominates; MAE is deliberately the smallest term.

    V1 was chosen on MAE, improved MAE, and lost. The weights below encode the ordering
    the evidence forces: never break what already works (100x), then choose better moves
    (1 point per cp of regret saved), then order siblings better, then keep draws drawn,
    and only then reduce error.
    """
    return (
        100.0 * (metrics["preserved_rate"] - 1.0)
        + 1.0 * metrics["regret_reduction_cp"]
        + 40.0 * metrics["pair_accuracy_gain"]
        + 20.0 * (metrics["draw_preservation_rate"] - 1.0)
        + 0.02 * metrics["mae_gain_cp"]
    )


# --------------------------------------------------------------------------- variants

VARIANTS: dict[str, dict[str, Any]] = {
    # value, ranking, anchor, draw, confidence-calibration weights; gate and phase-gate
    "A": {"w_value": 1.0, "w_rank": 0.0, "w_anchor": 1.0, "w_draw": 0.5,
          "w_conf": 0.0, "gate": False, "phase_gate": False},
    "B": {"w_value": 1.0, "w_rank": 1.0, "w_anchor": 1.0, "w_draw": 0.5,
          "w_conf": 0.0, "gate": False, "phase_gate": False},
    "C": {"w_value": 1.0, "w_rank": 1.0, "w_anchor": 1.0, "w_draw": 0.5,
          "w_conf": 0.5, "gate": True, "phase_gate": False},
    "D": {"w_value": 0.7, "w_rank": 1.0, "w_anchor": 3.0, "w_draw": 2.0,
          "w_conf": 0.5, "gate": True, "phase_gate": False},
    "E": {"w_value": 1.0, "w_rank": 1.0, "w_anchor": 1.0, "w_draw": 0.5,
          "w_conf": 0.5, "gate": True, "phase_gate": True},
}


# ------------------------------------------------------------------------- quantizing


def quantize(model: Any, gate: bool, phase_gate: bool) -> dict[str, Any]:
    """int8 weights, int32 accumulators, per-tensor symmetric scales.

    Identical to V1 for the two value heads, so the residual half stays comparable. The
    confidence head is a logit, not a centipawn quantity, so its scale is *not* taken
    through OUTPUT_SCALE -- a bug that would silently saturate the gate wide open.
    """
    import torch

    with torch.no_grad():
        embed = model.embed.weight.detach().cpu().numpy()
        aux_w = model.aux.weight.detach().cpu().numpy()
        aux_b = model.aux.bias.detach().cpu().numpy()
        mg_w = model.head_mg.weight.detach().cpu().numpy()[0]
        mg_b = float(model.head_mg.bias.detach().cpu().numpy()[0])
        eg_w = model.head_eg.weight.detach().cpu().numpy()[0]
        eg_b = float(model.head_eg.bias.detach().cpu().numpy()[0])
        cf_w = model.head_conf.weight.detach().cpu().numpy()[0]
        cf_b = float(model.head_conf.bias.detach().cpu().numpy()[0])

    embed_q = np.clip(np.rint(embed * 127.0), -127, 127).astype(np.int8)
    aux_w_q = np.clip(np.rint(aux_w * 127.0), -127, 127).astype(np.int8)
    aux_b_q = np.rint(aux_b * 127.0).astype(np.int32)

    def head_scale(weights: np.ndarray) -> tuple[np.ndarray, float]:
        peak = float(np.max(np.abs(weights))) or 1e-8
        scale = peak / 127.0
        return np.clip(np.rint(weights / scale), -127, 127).astype(np.int8), scale

    mg_q, mg_scale = head_scale(mg_w)
    eg_q, eg_scale = head_scale(eg_w)
    cf_q, cf_scale = head_scale(cf_w)

    mg_scale *= OUTPUT_SCALE
    eg_scale *= OUTPUT_SCALE
    mg_b *= OUTPUT_SCALE
    eg_b *= OUTPUT_SCALE

    return {
        "embed_q": embed_q,
        "aux_w_q": aux_w_q,
        "aux_b_q": aux_b_q,
        "mg_q": mg_q,
        "mg_scale": mg_scale,
        "mg_bias": mg_b,
        "eg_q": eg_q,
        "eg_scale": eg_scale,
        "eg_bias": eg_b,
        "conf_q": cf_q,
        "conf_scale": cf_scale,
        "conf_bias": cf_b,
        "gate": bool(gate),
        "phase_gate": bool(phase_gate),
    }


def bias_table(aux_w: np.ndarray, aux_b: np.ndarray) -> np.ndarray:
    """Fold the auxiliary layer into a 16 x 25 x width integer lookup.

    Five of the six auxiliary features are 0/1 and the sixth, material phase, takes 25
    integer values, so the layer collapses to a table the agent builds once at import.
    This function is the *reference* for that table: the phase term rounds in integers,
    (phase * w + 12) // 24, so agent and reference agree bit for bit rather than to
    within a rounding step. V1 computed the reference half of this in floating point and
    carried an unexplained ~1.4 cp float-to-quant drift as a result.
    """
    width = aux_b.shape[0]
    table = np.zeros((16, 25, width), dtype=np.int64)
    for combo in range(16):
        bits = (combo & 1, (combo >> 1) & 1, (combo >> 2) & 1, (combo >> 3) & 1)
        for units in range(25):
            value = aux_b.astype(np.int64) + aux_w[:, 0].astype(np.int64)
            for slot in range(4):
                if bits[slot]:
                    value = value + aux_w[:, slot + 1].astype(np.int64)
            term = aux_w[:, 5].astype(np.int64) * units
            rounded = np.where(term >= 0, (term + 12) // 24, -((-term + 12) // 24))
            table[combo, units] = value + rounded
    return table


def quant_correction(
    q: dict[str, Any], enc: dict[str, Any], rows: np.ndarray
) -> np.ndarray:
    """Reference integer inference. The agent's Numba path must match this exactly."""
    indices = enc["indices"][rows]
    mask = enc["mask"][rows]
    aux = enc["aux"][rows]
    phase = enc["phase"][rows]
    units = enc["phase_units"][rows]

    n = indices.shape[0]
    width = q["embed_q"].shape[1]
    table = bias_table(q["aux_w_q"], q["aux_b_q"])
    combo = (
        aux[:, 1].astype(np.int64)
        | (aux[:, 2].astype(np.int64) << 1)
        | (aux[:, 3].astype(np.int64) << 2)
        | (aux[:, 4].astype(np.int64) << 3)
    )
    acc = np.zeros((n, width), dtype=np.int32)
    for i in range(n):
        active = indices[i][mask[i] > 0]
        if active.size:
            acc[i] = q["embed_q"][active].astype(np.int32).sum(axis=0)
    acc += table[combo, units].astype(np.int32)
    acc = np.clip(acc, 0, 127)

    mg = acc @ q["mg_q"].astype(np.int32)
    eg = acc @ q["eg_q"].astype(np.int32)
    mg_cp = mg * q["mg_scale"] / 127.0 + q["mg_bias"]
    eg_cp = eg * q["eg_scale"] / 127.0 + q["eg_bias"]
    residual = mg_cp * phase + eg_cp * (1.0 - phase)
    residual = np.clip(residual, -CORRECTION_CLAMP, CORRECTION_CLAMP)

    if q["gate"]:
        logit = (acc @ q["conf_q"].astype(np.int32)) * q["conf_scale"] / 127.0 + q[
            "conf_bias"
        ]
        residual = residual / (1.0 + np.exp(-logit))
    if q["phase_gate"]:
        residual = np.where(units <= PHASE_GATE_MAX, residual, 0.0)
    return np.trunc(residual).astype(np.float64)


# ---------------------------------------------------------------------------- training


def train_variant(
    variant: str,
    hidden: int,
    seed: int,
    enc: dict[str, Any],
    split_groups: dict[str, np.ndarray],
    epochs: int,
    batch_groups: int,
    lr: float,
    outdir: pathlib.Path,
) -> dict[str, Any]:
    import torch
    from torch import nn

    cfg = VARIANTS[variant]
    set_seeds(seed)
    model_cls = nn_features.build_v2_model(hidden=hidden, clamp_cp=CORRECTION_CLAMP)
    model = model_cls()
    opt = torch.optim.Adam(model.parameters(), lr=lr)
    huber = nn.SmoothL1Loss(beta=50.0 / OUTPUT_SCALE, reduction="none")
    bce = nn.BCELoss(reduction="none")

    t_indices = torch.from_numpy(enc["indices"])
    t_mask = torch.from_numpy(enc["mask"])
    t_aux = torch.from_numpy(enc["aux"])
    t_phase = torch.from_numpy(enc["phase"])
    t_units = torch.from_numpy(enc["phase_units"])
    t_base = torch.from_numpy(enc["base"])
    t_sf = torch.from_numpy(enc["sf"])
    t_target = torch.from_numpy(enc["target"])

    train_ids = split_groups["train"]
    val_ids = split_groups["validation"]
    offsets = enc["offsets"]
    all_pairs = build_all_pairs(enc)
    poff = all_pairs["offsets"]
    scratch = np.zeros(enc["base"].shape[0], dtype=np.int64)
    print(f"  {len(all_pairs['better'])} sibling pairs, "
          f"{float(all_pairs['anchor'].mean()):.3f} already ordered by the control",
          flush=True)
    history: list[dict[str, Any]] = []
    best_score = -1e18
    best_state: dict[str, Any] | None = None
    best_epoch = -1
    rng = np.random.default_rng(seed)
    started = time.perf_counter()

    def corrections_for(rows: np.ndarray) -> np.ndarray:
        model.eval()
        out = np.zeros(len(rows), dtype=np.float64)
        with torch.no_grad():
            for start in range(0, len(rows), 8192):
                chunk = rows[start : start + 8192]
                t = torch.from_numpy(chunk)
                residual, conf = model.raw(
                    t_indices[t], t_mask[t], t_aux[t], t_phase[t]
                )
                residual = torch.clamp(residual * OUTPUT_SCALE, -CORRECTION_CLAMP, CORRECTION_CLAMP)
                if cfg["gate"]:
                    residual = residual * conf
                value = residual.numpy().astype(np.float64)
                if cfg["phase_gate"]:
                    value = np.where(
                        t_units[t].numpy() <= PHASE_GATE_MAX, value, 0.0
                    )
                out[start : start + len(chunk)] = np.trunc(value)
        return out

    val_rows = np.concatenate(
        [np.arange(offsets[g], offsets[g + 1]) for g in val_ids]
    )

    for epoch in range(epochs):
        model.train()
        order = rng.permutation(train_ids)
        totals = {"value": 0.0, "rank": 0.0, "anchor": 0.0, "draw": 0.0, "conf": 0.0}
        batches = 0
        for start in range(0, len(order), batch_groups):
            gids = order[start : start + batch_groups]
            rows = np.concatenate(
                [np.arange(offsets[g], offsets[g + 1]) for g in gids]
            )
            pair_rows = np.concatenate(
                [np.arange(poff[g], poff[g + 1]) for g in gids]
            )
            if len(pair_rows) == 0:
                continue
            # Remap global child ids to positions within this batch with a scratch
            # lookup rather than a dict: the dict version was the hot loop.
            scratch[rows] = np.arange(len(rows), dtype=np.int64)
            pb = torch.from_numpy(scratch[all_pairs["better"][pair_rows]])
            pw = torch.from_numpy(scratch[all_pairs["worse"][pair_rows]])
            anchor_mask = torch.from_numpy(all_pairs["anchor"][pair_rows])

            t = torch.from_numpy(rows)
            residual, conf = model.raw(t_indices[t], t_mask[t], t_aux[t], t_phase[t])
            bounded = torch.clamp(residual, -1.0, 1.0)  # in OUTPUT_SCALE units
            correction = bounded * conf if cfg["gate"] else bounded
            if cfg["phase_gate"]:
                keep = (t_units[t] <= PHASE_GATE_MAX).float()
                correction = correction * keep

            base = t_base[t]
            sf = t_sf[t]
            target = torch.clamp(
                t_target[t], -CORRECTION_CLAMP, CORRECTION_CLAMP
            ) / OUTPUT_SCALE
            deployed = base + correction * OUTPUT_SCALE

            # 1. value
            loss_value = huber(correction, target).mean()

            # 2. pairwise ranking: the lower-regret child must get the lower deployed
            #    evaluation, with a margin scaled by how much the regret gap deserves.
            gap = torch.from_numpy(all_pairs["gap"][pair_rows])
            margin = 0.25 * gap
            violation = torch.relu(margin - (deployed[pw] - deployed[pb]))

            # 3. anchor: on pairs the control already orders correctly, the penalty is
            #    for *eroding* the existing gap, not merely for being wrong. Breaking
            #    one of these is what cost V1 five solved fixtures.
            ctrl_gap = torch.from_numpy(all_pairs["ctrl_gap"][pair_rows])
            keep_gap = 0.5 * torch.clamp(ctrl_gap, min=0.0)
            erosion = torch.relu(keep_gap - (deployed[pw] - deployed[pb]))
            if anchor_mask.sum() > 0:
                loss_anchor = (erosion * anchor_mask).sum() / anchor_mask.sum()
            else:
                loss_anchor = torch.zeros(())
            free = 1.0 - anchor_mask
            loss_rank = (
                (violation * free).sum() / free.sum()
                if free.sum() > 0
                else torch.zeros(())
            )

            # 4. draw preservation: in a position Stockfish calls equal, never end up
            #    further from equality than both the control and the truth already are.
            drawish = (torch.abs(sf) <= DRAW_BAND_CP).float()
            allow = torch.maximum(torch.abs(base), torch.abs(sf))
            excess = torch.relu(torch.abs(deployed) - allow)
            loss_draw = (
                (excess * drawish).sum() / drawish.sum()
                if drawish.sum() > 0
                else torch.zeros(())
            )

            # 5. confidence calibration. The indicator is computed from the *ungated*
            #    bounded residual and detached, which breaks the circularity: the gate
            #    is trained to predict whether the raw residual would help here.
            if cfg["gate"]:
                with torch.no_grad():
                    full = bounded.detach() * OUTPUT_SCALE
                    helps = (
                        torch.abs(base + full - sf) < torch.abs(base - sf)
                    ).float()
                loss_conf = bce(conf.clamp(1e-6, 1 - 1e-6), helps).mean()
            else:
                loss_conf = torch.zeros(())

            loss = (
                cfg["w_value"] * loss_value
                + cfg["w_rank"] * loss_rank / OUTPUT_SCALE
                + cfg["w_anchor"] * loss_anchor / OUTPUT_SCALE
                + cfg["w_draw"] * loss_draw / OUTPUT_SCALE
                + cfg["w_conf"] * loss_conf
            )

            opt.zero_grad()
            loss.backward()
            opt.step()
            model.clip_weights()

            totals["value"] += float(loss_value.detach())
            totals["rank"] += float(loss_rank.detach())
            totals["anchor"] += float(loss_anchor.detach())
            totals["draw"] += float(loss_draw.detach())
            totals["conf"] += float(loss_conf.detach())
            batches += 1

        corr = np.zeros(enc["base"].shape[0], dtype=np.float64)
        corr[val_rows] = corrections_for(val_rows)
        gm = group_metrics(corr, enc, val_ids)
        vm = value_metrics(corr, enc, val_rows)
        record = {
            "epoch": epoch,
            "train_loss": {k: round(v / max(1, batches), 6) for k, v in totals.items()},
            **{k: round(v, 4) for k, v in gm.items()},
            **{k: round(v, 4) for k, v in vm.items()},
        }
        record["composite"] = round(composite({**gm, **vm}), 4)
        history.append(record)
        print(
            f"  [{variant} h{hidden} s{seed}] epoch {epoch:2d} "
            f"composite={record['composite']:8.3f} "
            f"preserved={gm['preserved_rate']:.4f} "
            f"regret_red={gm['regret_reduction_cp']:+7.2f} "
            f"pair_gain={gm['pair_accuracy_gain']:+.4f} "
            f"mae_gain={vm['mae_gain_cp']:+7.2f} "
            f"|corr|={vm['mean_abs_correction']:6.2f}",
            flush=True,
        )

        if record["composite"] > best_score:
            best_score = record["composite"]
            best_epoch = epoch
            best_state = {
                k: v.detach().clone() for k, v in model.state_dict().items()
            }

    assert best_state is not None
    model.load_state_dict(best_state)
    q = quantize(model, cfg["gate"], cfg["phase_gate"])

    outdir.mkdir(parents=True, exist_ok=True)
    np.savez(
        outdir / "quantized.npz",
        embed=q["embed_q"],
        aux_w=q["aux_w_q"],
        aux_b=q["aux_b_q"],
        mg=q["mg_q"],
        eg=q["eg_q"],
        conf=q["conf_q"],
        scales=np.array([q["mg_scale"], q["eg_scale"], q["conf_scale"]], dtype=np.float64),
        biases=np.array([q["mg_bias"], q["eg_bias"], q["conf_bias"]], dtype=np.float64),
        flags=np.array([int(q["gate"]), int(q["phase_gate"]), PHASE_GATE_MAX], dtype=np.int64),
    )
    import torch

    torch.save(best_state, outdir / "float.pt")

    return {
        "variant": variant,
        "hidden": hidden,
        "seed": seed,
        "config": cfg,
        "epochs_run": epochs,
        "best_epoch": best_epoch,
        "best_composite": best_score,
        "train_seconds": round(time.perf_counter() - started, 1),
        "parameters": nn_features.parameter_count(hidden),
        "history": history,
        "quantized": str((outdir / "quantized.npz").relative_to(REPO)),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--variants", default="A,B,C,D,E")
    ap.add_argument("--hidden", type=int, default=32)
    ap.add_argument("--seed", type=int, default=20260909)
    ap.add_argument("--epochs", type=int, default=12)
    ap.add_argument("--batch-groups", type=int, default=96)
    ap.add_argument("--lr", type=float, default=2e-3)
    ap.add_argument("--tag", default="")
    args = ap.parse_args()

    groups = load_groups()
    print(f"loaded {len(groups)} groups")
    enc = encode_groups(groups, OUTROOT / "encoded.npz")

    split_of = np.array([g["split"] for g in groups])
    split_groups = {
        name: np.flatnonzero(split_of == name)
        for name in ("train", "validation", "test", "holdout")
    }
    print({k: int(len(v)) for k, v in split_groups.items()})

    results = []
    for variant in args.variants.split(","):
        variant = variant.strip()
        if not variant:
            continue
        tag = f"{variant}_h{args.hidden}_s{args.seed}" + (f"_{args.tag}" if args.tag else "")
        outdir = OUTROOT / tag
        print(f"=== training variant {tag} ===", flush=True)
        result = train_variant(
            variant,
            args.hidden,
            args.seed,
            enc,
            split_groups,
            args.epochs,
            args.batch_groups,
            args.lr,
            outdir,
        )
        result["tag"] = tag
        (outdir / "training.json").write_text(
            json.dumps(result, indent=2), encoding="utf-8"
        )
        results.append(result)

    summary = [
        {
            k: r[k]
            for k in ("tag", "variant", "hidden", "seed", "best_epoch",
                      "best_composite", "train_seconds", "parameters")
        }
        for r in results
    ]
    OUTROOT.mkdir(parents=True, exist_ok=True)
    name = f"summary_h{args.hidden}_s{args.seed}" + (f"_{args.tag}" if args.tag else "")
    (OUTROOT / f"{name}.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
