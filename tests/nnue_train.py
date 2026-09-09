"""Train the NNUE-lite residual from random initialization, then quantize it.

The target is `stockfish_cp - control_static_cp`, both side-to-move relative, so the
network learns only the correction the handcrafted evaluation is missing. It never
learns the evaluation itself, which keeps the residual small and bounded.

Early stopping is on game-separated validation MAE. The test split is never read until
the final metrics pass, and never influences a training decision.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import random
import sys
import time

import chess
import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import nnue_model as nn_features

M = nn_features
M_PHASE_MAX = nn_features.PHASE_MAX

REPO = pathlib.Path(__file__).resolve().parent.parent
DATASET = REPO / "tests" / "results" / "nnue" / "dataset"
MODELDIR = REPO / "tests" / "results" / "nnue" / "model"
MAX_PIECES = 32
SEED = 20260909

# The network is trained in units of OUTPUT_SCALE centipawns, not in raw centipawns.
# Trained directly on a +/-300 cp target the heads start at about 0.4 cp of output and
# would need roughly ten thousand optimiser steps to reach a useful magnitude; the first
# run moved the training loss from 292.39 to 291.64 over twenty epochs and predicted
# nothing. Working in O(1) units makes ordinary learning rates apply.
#
# The scale equals the inference clamp, so a saturated model output of 1.0 corresponds
# exactly to the largest correction the engine will accept.
OUTPUT_SCALE = 250.0
CORRECTION_CLAMP = 250


def set_seeds(seed: int) -> None:
    import torch

    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.use_deterministic_algorithms(True, warn_only=True)


# ------------------------------------------------------------------ colour symmetry


def colour_swapped(board: chess.Board) -> chess.Board:
    """Mirror ranks and swap colours. python-chess `mirror()` does exactly this."""
    return board.mirror()


def prove_augmentation_is_label_preserving(fens: list[str]) -> dict:
    """The canonical orientation must make a position and its colour swap identical.

    If that holds, colour-swap augmentation is a no-op on the features and cannot
    change the label, which is the only condition under which it is legal to apply.
    """
    same_features = 0
    same_aux = 0
    checked = 0
    mismatches: list[str] = []
    for fen in fens:
        board = chess.Board(fen)
        mirrored = colour_swapped(board)
        i1, a1 = nn_features.active_features(board)
        i2, a2 = nn_features.active_features(mirrored)
        checked += 1
        if np.array_equal(np.sort(i1), np.sort(i2)):
            same_features += 1
        else:
            mismatches.append(fen)
        if np.allclose(a1, a2):
            same_aux += 1
    return {
        "checked": checked,
        "identical_features": same_features,
        "identical_aux": same_aux,
        "label_preserving": same_features == checked and same_aux == checked,
        "mismatch_examples": mismatches[:5],
        "conclusion": (
            "canonical orientation already folds colour symmetry into the features, so "
            "colour-swap augmentation adds no information and is NOT applied"
            if same_features == checked
            else "orientation is not colour-symmetric; augmentation would change labels"
        ),
    }


# ------------------------------------------------------------------------- encoding


def encode(rows: list[dict]) -> dict:
    n = len(rows)
    indices = np.zeros((n, MAX_PIECES), dtype=np.int64)
    mask = np.zeros((n, MAX_PIECES), dtype=np.float32)
    aux = np.zeros((n, nn_features.NUM_AUX), dtype=np.float32)
    phase = np.zeros(n, dtype=np.float32)
    target = np.zeros(n, dtype=np.float32)
    for i, row in enumerate(rows):
        board = chess.Board(row["fen"])
        idx, a = nn_features.active_features(board)
        k = min(len(idx), MAX_PIECES)
        indices[i, :k] = idx[:k]
        mask[i, :k] = 1.0
        aux[i] = a
        phase[i] = nn_features.phase_blend(board)
        target[i] = float(row["residual_target"])
    return {
        "indices": indices,
        "mask": mask,
        "aux": aux,
        "phase": phase,
        "target": target,
        "rows": rows,
    }


def load_split(path: pathlib.Path) -> dict[str, list[dict]]:
    splits: dict[str, list[dict]] = {
        "train": [],
        "validation": [],
        "test": [],
        "holdout": [],  # the held-out opponent family, never trained on
    }
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            splits.setdefault(row["split"], []).append(row)
    return splits


# ------------------------------------------------------------------------ quantizing


def quantize(model) -> dict:
    """int8 weights, int32 accumulators, per-tensor symmetric scales.

    The first layer is scaled by a fixed 127 so the accumulator is already in units of
    1/127 and the clipped ReLU is a plain clamp to [0, 127] with no rescale. Weights are
    clipped to [-1, 1] during training to make that safe.
    """
    import torch

    with torch.no_grad():
        embed = model.embed.weight.detach().cpu().numpy()  # (768, 64)
        aux_w = model.aux.weight.detach().cpu().numpy()  # (64, 6)
        aux_b = model.aux.bias.detach().cpu().numpy()  # (64,)
        mg_w = model.head_mg.weight.detach().cpu().numpy()[0]  # (64,)
        mg_b = float(model.head_mg.bias.detach().cpu().numpy()[0])
        eg_w = model.head_eg.weight.detach().cpu().numpy()[0]
        eg_b = float(model.head_eg.bias.detach().cpu().numpy()[0])

    embed_q = np.clip(np.rint(embed * 127.0), -127, 127).astype(np.int8)
    aux_w_q = np.clip(np.rint(aux_w * 127.0), -127, 127).astype(np.int8)
    aux_b_q = np.rint(aux_b * 127.0).astype(np.int32)

    def head_scale(weights: np.ndarray) -> tuple[np.ndarray, float]:
        peak = float(np.max(np.abs(weights))) or 1e-8
        scale = peak / 127.0
        return np.clip(np.rint(weights / scale), -127, 127).astype(np.int8), scale

    mg_q, mg_scale = head_scale(mg_w)
    eg_q, eg_scale = head_scale(eg_w)
    # The model was trained in units of OUTPUT_SCALE centipawns, so the scale that
    # leaves the quantizer is the head scale times OUTPUT_SCALE. The agent then needs no
    # knowledge of the training parameterisation at all.
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
    }


def quant_forward(q: dict, indices: np.ndarray, mask: np.ndarray, aux: np.ndarray,
                  phase: np.ndarray) -> np.ndarray:
    """Reference integer inference, vectorised. The agent's Numba path must match this."""
    n = indices.shape[0]
    hidden_width = q["embed_q"].shape[1]
    acc = np.zeros((n, hidden_width), dtype=np.int32)
    for i in range(n):
        active = indices[i][mask[i] > 0]
        if active.size:
            acc[i] = q["embed_q"][active].astype(np.int32).sum(axis=0)
    # The auxiliary contribution is computed exactly as the agent computes it, in
    # integers, not as a rounded float dot product. Five of the six features are 0/1 and
    # only the phase term needs a multiply, which the agent rounds as
    # (phase * w + 12) // 24. Rounding the summed float instead disagreed with the
    # deployed kernel by up to 2.2 cp, which would make every offline metric describe a
    # slightly different evaluator from the one that actually plays.
    aux_w = q["aux_w_q"].astype(np.int64)
    phase_units = np.rint(aux[:, 5] * M_PHASE_MAX).astype(np.int64)
    bits = np.rint(aux[:, 1:5]).astype(np.int64)
    aux_contrib = np.zeros((n, hidden_width), dtype=np.int64)
    aux_contrib += aux_w[:, 0][None, :]
    for slot in range(4):
        aux_contrib += bits[:, slot][:, None] * aux_w[:, slot + 1][None, :]
    term = phase_units[:, None] * aux_w[:, 5][None, :]
    aux_contrib += np.where(term >= 0, (term + 12) // 24, -((-term + 12) // 24))
    acc += aux_contrib.astype(np.int32) + q["aux_b_q"]
    hidden = np.clip(acc, 0, 127).astype(np.int32)
    mg = hidden @ q["mg_q"].astype(np.int32)
    eg = hidden @ q["eg_q"].astype(np.int32)
    mg_cp = mg * q["mg_scale"] / 127.0 + q["mg_bias"]
    eg_cp = eg * q["eg_scale"] / 127.0 + q["eg_bias"]
    return mg_cp * phase + eg_cp * (1.0 - phase)


# --------------------------------------------------------------------------- metrics


def metrics(pred_cp: np.ndarray, truth: np.ndarray, rows: list[dict], band: int = 25) -> dict:
    """Score the residual the way the engine will use it.

    `pred_cp` is the raw network output in centipawns. The engine clamps the correction
    to +/-250 before adding it, so the gate compares

        corrected error = truth - clamp(prediction)      against
        baseline error  = truth                          (the handcrafted evaluator alone)

    Both are measured against the UNCLIPPED Stockfish residual, so the improvement
    claimed is the improvement the evaluation actually gets, not an improvement on a
    convenient restatement of the target.
    """
    applied = np.clip(pred_cp, -CORRECTION_CLAMP, CORRECTION_CLAMP)
    corrected = truth - applied
    baseline_abs = np.abs(truth)
    corrected_abs = np.abs(corrected)

    outside = baseline_abs > band
    sign_ok = (
        float(np.mean(np.sign(applied[outside]) == np.sign(truth[outside])))
        if outside.any()
        else None
    )
    by_phase: dict[str, dict] = {}
    for name in ("opening", "middlegame", "endgame"):
        sel = np.array([r["phase"] == name for r in rows])
        if sel.any():
            by_phase[name] = {
                "n": int(sel.sum()),
                "baseline_mae": round(float(np.mean(baseline_abs[sel])), 2),
                "corrected_mae": round(float(np.mean(corrected_abs[sel])), 2),
                "corrected_median_ae": round(float(np.median(corrected_abs[sel])), 2),
            }
    improvement = float(np.mean(baseline_abs) - np.mean(corrected_abs))
    return {
        "n": len(truth),
        "baseline_mae": round(float(np.mean(baseline_abs)), 2),
        "mae": round(float(np.mean(corrected_abs)), 2),
        "median_ae": round(float(np.median(corrected_abs)), 2),
        "rmse": round(float(np.sqrt(np.mean(corrected**2))), 2),
        "mae_improvement_cp": round(improvement, 2),
        "mae_improvement_pct": round(
            improvement / max(1e-9, float(np.mean(baseline_abs))) * 100, 2
        ),
        "sign_accuracy_outside_band": round(sign_ok, 4) if sign_ok is not None else None,
        "band_cp": band,
        "by_phase": by_phase,
        "target_mae_of_zero_predictor": round(float(np.mean(baseline_abs)), 2),
        "mean_applied_correction_cp": round(float(np.mean(applied)), 2),
        "mean_abs_applied_correction_cp": round(float(np.mean(np.abs(applied))), 2),
        "saturated_fraction": round(float(np.mean(np.abs(pred_cp) >= CORRECTION_CLAMP)), 4),
    }


# ---------------------------------------------------------------------------- train


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--epochs", type=int, default=40)
    ap.add_argument("--batch", type=int, default=2048)
    ap.add_argument("--lr", type=float, default=2e-3)
    ap.add_argument("--patience", type=int, default=6)
    ap.add_argument("--hidden", type=int, default=nn_features.HIDDEN)
    ap.add_argument("--seed", type=int, default=SEED)
    ap.add_argument("--tag", default="", help="name this architecture in the outputs")
    ap.add_argument("--data", type=pathlib.Path, default=DATASET / "labelled.jsonl")
    args = ap.parse_args()

    import torch
    from torch import nn

    torch.set_num_threads(6)  # training only; inference is pinned to one thread
    set_seeds(args.seed)
    global MODELDIR
    if args.tag:
        MODELDIR = MODELDIR.parent / f"model_{args.tag}"
    MODELDIR.mkdir(parents=True, exist_ok=True)

    splits = load_split(args.data)
    print({k: len(v) for k, v in splits.items()})

    aug = prove_augmentation_is_label_preserving(
        [r["fen"] for r in splits["train"][:2000]]
    )
    print("augmentation proof:", aug["conclusion"])
    (MODELDIR / "augmentation_proof.json").write_text(
        json.dumps(aug, indent=2), encoding="utf-8"
    )

    print("encoding...")
    data = {name: encode(rows) for name, rows in splits.items() if rows}

    residual_class = nn_features.build_torch_model(args.hidden)
    model = residual_class()
    print("parameters:", sum(p.numel() for p in model.parameters()))

    optimiser = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=1e-5)
    schedule = torch.optim.lr_scheduler.CosineAnnealingLR(optimiser, T_max=args.epochs)
    # beta is 50 cp expressed in the scaled units the network works in
    loss_fn = nn.SmoothL1Loss(beta=50.0 / OUTPUT_SCALE)

    def tensors(pack: dict) -> tuple:
        return (
            torch.from_numpy(pack["indices"]),
            torch.from_numpy(pack["mask"]),
            torch.from_numpy(pack["aux"]),
            torch.from_numpy(pack["phase"]),
            torch.from_numpy(pack["target"]),
        )

    tr = tensors(data["train"])
    va = tensors(data["validation"])
    n_train = tr[0].shape[0]

    # Targets are clipped to the correction clamp before scaling: the engine will never
    # apply more than CORRECTION_CLAMP, so asking the network to predict beyond it only
    # spends capacity on values it cannot express. Every metric below is still measured
    # against the UNCLIPPED residual.
    tr_target_scaled = torch.clamp(tr[4], -CORRECTION_CLAMP, CORRECTION_CLAMP) / OUTPUT_SCALE

    history: list[dict] = []
    best = float("inf")
    best_state = None
    stale = 0
    for epoch in range(args.epochs):
        model.train()
        order = torch.randperm(n_train)
        total = 0.0
        seen = 0
        started = time.perf_counter()
        for start in range(0, n_train, args.batch):
            sel = order[start : start + args.batch]
            optimiser.zero_grad()
            out = model(tr[0][sel], tr[1][sel], tr[2][sel], tr[3][sel])
            loss = loss_fn(out, tr_target_scaled[sel])
            loss.backward()
            optimiser.step()
            model.clip_weights()
            total += float(loss) * len(sel)
            seen += len(sel)
        schedule.step()

        model.eval()
        with torch.no_grad():
            vp = model(va[0], va[1], va[2], va[3]).numpy() * OUTPUT_SCALE
        vm = metrics(vp, data["validation"]["target"], data["validation"]["rows"])
        row = {
            "epoch": epoch,
            "train_loss": round(total / seen, 4),
            "val_mae": vm["mae"],
            "val_baseline_mae": vm["baseline_mae"],
            "val_improvement_cp": vm["mae_improvement_cp"],
            "val_median_ae": vm["median_ae"],
            "val_sign_acc": vm["sign_accuracy_outside_band"],
            "seconds": round(time.perf_counter() - started, 1),
        }
        history.append(row)
        print(row, flush=True)

        if vm["mae"] < best - 0.05:
            best = vm["mae"]
            best_state = {k: v.clone() for k, v in model.state_dict().items()}
            stale = 0
        else:
            stale += 1
            if stale >= args.patience:
                print(f"early stop at epoch {epoch}; best validation MAE {best:.2f}")
                break

    if best_state is not None:
        model.load_state_dict(best_state)
    model.eval()

    torch.save(model.state_dict(), MODELDIR / "float_model.pt")
    q = quantize(model)

    report: dict = {
        "seed": args.seed,
        "output_scale_cp": OUTPUT_SCALE,
        "correction_clamp_cp": CORRECTION_CLAMP,
        "target_clipped_for_training": True,
        "metrics_measured_against_unclipped_residual": True,
        "epochs_run": len(history),
        "best_val_mae": best,
        "history": history,
        "parameters": int(sum(p.numel() for p in model.parameters())),
        "hidden": args.hidden,
        "tag": args.tag or "base",
        "augmentation": aug,
        "splits": {k: len(v) for k, v in splits.items()},
    }

    for name in ("train", "validation", "test", "holdout"):
        if name not in data:
            continue
        pack = data[name]
        with torch.no_grad():
            args_t = [torch.from_numpy(pack[k]) for k in ("indices", "mask", "aux", "phase")]
            fp = model(*args_t).numpy() * OUTPUT_SCALE
        qp = quant_forward(q, pack["indices"], pack["mask"], pack["aux"], pack["phase"])
        report[f"{name}_float"] = metrics(fp, pack["target"], pack["rows"])
        report[f"{name}_quant"] = metrics(qp, pack["target"], pack["rows"])
        deviation = np.abs(qp - fp)
        report[f"{name}_quantization_error"] = {
            "median_cp": round(float(np.median(deviation)), 3),
            "mean_cp": round(float(np.mean(deviation)), 3),
            "p95_cp": round(float(np.percentile(deviation, 95)), 3),
            "max_cp": round(float(np.max(deviation)), 3),
        }
        report[f"{name}_health"] = {
            "nan": bool(np.isnan(qp).any() or np.isnan(fp).any()),
            "inf": bool(np.isinf(qp).any() or np.isinf(fp).any()),
        }

    np.savez(
        MODELDIR / "quantized.npz",
        embed_q=q["embed_q"],
        aux_w_q=q["aux_w_q"],
        aux_b_q=q["aux_b_q"],
        mg_q=q["mg_q"],
        eg_q=q["eg_q"],
        scales=np.array([q["mg_scale"], q["eg_scale"]], dtype=np.float64),
        biases=np.array([q["mg_bias"], q["eg_bias"]], dtype=np.float64),
    )
    weights_bytes = (MODELDIR / "quantized.npz").read_bytes()
    report["quantized_weights"] = {
        "path": "tests/results/nnue/model/quantized.npz",
        "bytes": len(weights_bytes),
        "sha256": hashlib.sha256(weights_bytes).hexdigest(),
    }
    report["float_checkpoint_sha256"] = hashlib.sha256(
        (MODELDIR / "float_model.pt").read_bytes()
    ).hexdigest()

    (MODELDIR / "training_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "history"}, indent=2))
    print("weights sha256:", report["quantized_weights"]["sha256"])


if __name__ == "__main__":
    main()
