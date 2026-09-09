"""Measure deployed evaluation error as a function of the correction clamp.

The RATED_V5 gate failed with the declared +/-250 clamp: the residual corrected seven
depth-points across five class-A fixtures but also broke three solved controls and
introduced five new regressions. The question this answers is whether the damage lives
in the large corrections. If most of the evaluation gain survives a tighter bound, a
tighter bound is the better integration, and the brief allows one on evidence.

This is an offline measurement over the held-out splits. It does not touch a gate.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

import numpy as np

REPO = pathlib.Path(__file__).resolve().parent.parent
HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(HERE))

import nnue_model as nn_features  # noqa: E402
import nnue_train as trainer  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--clamps", type=int, nargs="+", default=[25, 50, 75, 100, 150, 200, 250])
    ap.add_argument(
        "--data",
        type=pathlib.Path,
        default=REPO / "tests" / "results" / "nnue" / "dataset" / "labelled.jsonl",
    )
    ap.add_argument(
        "--out",
        type=pathlib.Path,
        default=REPO / "tests" / "results" / "nnue" / "gates" / "clamp_sweep.json",
    )
    args = ap.parse_args()

    import torch

    screening = json.loads(
        (REPO / "tests" / "results" / "nnue" / "model" / "screening.json").read_text("utf-8")
    )
    winner = screening["winner"]
    model_dir = REPO / pathlib.Path(winner["model_dir"])
    hidden = winner["hidden"]

    residual_class = nn_features.build_torch_model(hidden)
    model = residual_class()
    model.load_state_dict(torch.load(model_dir / "float_model.pt", weights_only=True))
    model.eval()

    splits = trainer.load_split(args.data)
    results: dict[str, dict] = {}
    for split in ("test", "holdout"):
        rows = splits[split]
        pack = trainer.encode(rows)
        with torch.no_grad():
            pred = (
                model(
                    torch.from_numpy(pack["indices"]),
                    torch.from_numpy(pack["mask"]),
                    torch.from_numpy(pack["aux"]),
                    torch.from_numpy(pack["phase"]),
                ).numpy()
                * trainer.OUTPUT_SCALE
            )
        truth = pack["target"]
        baseline = float(np.mean(np.abs(truth)))
        per_clamp = {}
        for clamp in args.clamps:
            applied = np.clip(pred, -clamp, clamp)
            corrected = float(np.mean(np.abs(truth - applied)))
            saturated = float(np.mean(np.abs(pred) >= clamp))
            per_clamp[str(clamp)] = {
                "corrected_mae": round(corrected, 2),
                "improvement_cp": round(baseline - corrected, 2),
                "improvement_pct": round((baseline - corrected) / baseline * 100, 2),
                "saturated_fraction": round(saturated, 4),
                "mean_abs_correction_cp": round(float(np.mean(np.abs(applied))), 2),
            }
        results[split] = {"baseline_mae": round(baseline, 2), "by_clamp": per_clamp}

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(results, indent=2), encoding="utf-8")

    print(f"{'clamp':>7}", end="")
    for split in ("test", "holdout"):
        print(f"{split + ' cp':>13}{split + ' %':>10}{'sat':>8}", end="")
    print()
    for clamp in args.clamps:
        print(f"{clamp:>7}", end="")
        for split in ("test", "holdout"):
            row = results[split]["by_clamp"][str(clamp)]
            print(
                f"{row['improvement_cp']:>13.2f}{row['improvement_pct']:>10.2f}"
                f"{row['saturated_fraction']:>8.3f}",
                end="",
            )
        print()
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    sys.exit(main())
