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

import nnue_model as M  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parent.parent
DATASET = REPO / "tests" / "results" / "nnue" / "dataset"
MODELDIR = REPO / "tests" / "results" / "nnue" / "model"
MAX_PIECES = 32
SEED = 20260909


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
        i1, a1 = M.active_features(board)
        i2, a2 = M.active_features(mirrored)
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
    aux = np.zeros((n, M.NUM_AUX), dtype=np.float32)
    phase = np.zeros(n, dtype=np.float32)
    target = np.zeros(n, dtype=np.float32)
    for i, row in enumerate(rows):
        board = chess.Board(row["fen"])
        idx, a = M.active_features(board)
        k = min(len(idx), MAX_PIECES)
        indices[i, :k] = idx[:k]
        mask[i, :k] = 1.0
        aux[i] = a
        phase[i] = M.phase_blend(board)
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
    splits: dict[str, list[dict]] = {"train": [], "validation": [], "test": []}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            splits[row["split"]].append(row)
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
    acc = np.zeros((n, M.HIDDEN), dtype=np.int32)
    for i in range(n):
        active = indices[i][mask[i] > 0]
        if active.size:
            acc[i] = q["embed_q"][active].astype(np.int32).sum(axis=0)
    aux_contrib = np.rint(aux @ q["aux_w_q"].T.astype(np.float32)).astype(np.int32)
    acc += aux_contrib + q["aux_b_q"]
    hidden = np.clip(acc, 0, 127).astype(np.int32)
    mg = hidden @ q["mg_q"].astype(np.int32)
    eg = hidden @ q["eg_q"].astype(np.int32)
    mg_cp = mg * q["mg_scale"] / 127.0 + q["mg_bias"]
    eg_cp = eg * q["eg_scale"] / 127.0 + q["eg_bias"]
    return mg_cp * phase + eg_cp * (1.0 - phase)


# --------------------------------------------------------------------------- metrics


def metrics(pred: np.ndarray, truth: np.ndarray, rows: list[dict], band: int = 25) -> dict:
    err = pred - truth
    absolute = np.abs(err)
    outside = np.abs(truth) > band
    sign_ok = (
        float(np.mean(np.sign(pred[outside]) == np.sign(truth[outside])))
        if outside.any()
        else None
    )
    by_phase: dict[str, dict] = {}
    for name in ("opening", "middlegame", "endgame"):
        sel = np.array([r["phase"] == name for r in rows])
        if sel.any():
            by_phase[name] = {
                "n": int(sel.sum()),
                "mae": round(float(np.mean(absolute[sel])), 2),
                "median_ae": round(float(np.median(absolute[sel])), 2),
            }
    return {
        "n": int(len(truth)),
        "mae": round(float(np.mean(absolute)), 2),
        "median_ae": round(float(np.median(absolute)), 2),
        "rmse": round(float(np.sqrt(np.mean(err**2))), 2),
        "sign_accuracy_outside_band": round(sign_ok, 4) if sign_ok is not None else None,
        "band_cp": band,
        "by_phase": by_phase,
        "target_mae_of_zero_predictor": round(float(np.mean(np.abs(truth))), 2),
    }


# ---------------------------------------------------------------------------- train


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--epochs", type=int, default=40)
    ap.add_argument("--batch", type=int, default=2048)
    ap.add_argument("--lr", type=float, default=2e-3)
    ap.add_argument("--patience", type=int, default=6)
    ap.add_argument("--data", type=pathlib.Path, default=DATASET / "labelled.jsonl")
    args = ap.parse_args()

    import torch
    from torch import nn

    torch.set_num_threads(6)  # training only; inference is pinned to one thread
    set_seeds(SEED)
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

    Residual = M.build_torch_model()
    model = Residual()
    print("parameters:", sum(p.numel() for p in model.parameters()))

    optimiser = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=1e-5)
    schedule = torch.optim.lr_scheduler.CosineAnnealingLR(optimiser, T_max=args.epochs)
    loss_fn = nn.SmoothL1Loss(beta=50.0)

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
            loss = loss_fn(out, tr[4][sel])
            loss.backward()
            optimiser.step()
            model.clip_weights()
            total += float(loss) * len(sel)
            seen += len(sel)
        schedule.step()

        model.eval()
        with torch.no_grad():
            vp = model(va[0], va[1], va[2], va[3]).numpy()
        vm = metrics(vp, data["validation"]["target"], data["validation"]["rows"])
        row = {
            "epoch": epoch,
            "train_loss": round(total / seen, 4),
            "val_mae": vm["mae"],
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
        "seed": SEED,
        "epochs_run": len(history),
        "best_val_mae": best,
        "history": history,
        "parameters": int(sum(p.numel() for p in model.parameters())),
        "augmentation": aug,
        "splits": {k: len(v) for k, v in splits.items()},
    }

    for name in ("train", "validation", "test"):
        if name not in data:
            continue
        pack = data[name]
        with torch.no_grad():
            fp = model(*[torch.from_numpy(pack[k]) for k in ("indices", "mask", "aux", "phase")]).numpy()
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
