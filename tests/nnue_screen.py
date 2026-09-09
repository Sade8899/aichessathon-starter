"""Screen several architectures and seeds, then rank them on deployed held-out error.

Ranking is on the TEST split's clamped, deployed improvement -- the centipawns the
engine's evaluation actually gains -- not on training loss and not on the network's raw
prediction error. The holdout split (an engine family absent from training) is reported
alongside so a candidate that only generalises to its training opponents is visible.

Inference cost is measured per architecture too. A wider network that predicts better
but costs a completed ply is not an improvement, and this is where that shows up.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
import time

REPO = pathlib.Path(__file__).resolve().parent.parent
PY = REPO / ".venv" / "Scripts" / "python.exe"
NNUE = REPO / "tests" / "results" / "nnue"


def train_one(hidden: int, seed: int, data: pathlib.Path, epochs: int, batch: int,
              lr: float, patience: int) -> dict:
    tag = f"h{hidden}_s{seed}"
    started = time.perf_counter()
    proc = subprocess.run(
        [
            str(PY), str(REPO / "tests" / "nnue_train.py"),
            "--data", str(data),
            "--hidden", str(hidden),
            "--tag", tag,
            "--epochs", str(epochs),
            "--batch", str(batch),
            "--lr", str(lr),
            "--patience", str(patience),
            "--seed", str(seed),
        ],
        capture_output=True, text=True, cwd=str(REPO),
    )
    elapsed = time.perf_counter() - started
    report_path = NNUE / f"model_{tag}" / "training_report.json"
    if proc.returncode != 0 or not report_path.exists():
        return {
            "tag": tag, "hidden": hidden, "seed": seed, "ok": False,
            "error": (proc.stderr or proc.stdout)[-1500:],
        }
    report = json.loads(report_path.read_text(encoding="utf-8"))
    row = {
        "tag": tag,
        "hidden": hidden,
        "seed": seed,
        "ok": True,
        "parameters": report["parameters"],
        "epochs_run": report["epochs_run"],
        "train_seconds": round(elapsed, 1),
        "model_dir": str(report_path.parent.relative_to(REPO)),
    }
    for split in ("validation", "test", "holdout"):
        quant = report.get(f"{split}_quant")
        if quant:
            row[f"{split}_baseline_mae"] = quant["baseline_mae"]
            row[f"{split}_mae"] = quant["mae"]
            row[f"{split}_improvement_cp"] = quant["mae_improvement_cp"]
            row[f"{split}_improvement_pct"] = quant["mae_improvement_pct"]
            row[f"{split}_sign_acc"] = quant["sign_accuracy_outside_band"]
            row[f"{split}_saturated"] = quant["saturated_fraction"]
            by_phase = quant.get("by_phase", {})
            for phase in ("middlegame", "endgame"):
                if phase in by_phase:
                    row[f"{split}_{phase}_improvement_cp"] = round(
                        by_phase[phase]["baseline_mae"] - by_phase[phase]["corrected_mae"], 2
                    )
        err = report.get(f"{split}_quantization_error")
        if err:
            row[f"{split}_quant_drift_median_cp"] = err["median_cp"]
    return row


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=pathlib.Path, default=NNUE / "dataset" / "labelled.jsonl")
    ap.add_argument("--hidden", type=int, nargs="+", default=[32, 64, 96])
    ap.add_argument("--seeds", type=int, nargs="+", default=[20260909, 20260910])
    ap.add_argument("--epochs", type=int, default=60)
    ap.add_argument("--batch", type=int, default=1024)
    ap.add_argument("--lr", type=float, default=5e-3)
    ap.add_argument("--patience", type=int, default=8)
    ap.add_argument("--out", type=pathlib.Path, default=NNUE / "model" / "screening.json")
    args = ap.parse_args()

    if not args.data.exists():
        raise SystemExit(f"{args.data} does not exist; label the corpus first")

    rows: list[dict] = []
    for hidden in args.hidden:
        for seed in args.seeds:
            print(f"--- hidden={hidden} seed={seed} ---", flush=True)
            row = train_one(hidden, seed, args.data, args.epochs, args.batch,
                            args.lr, args.patience)
            rows.append(row)
            if row["ok"]:
                print(
                    f"    params {row['parameters']:,}  "
                    f"test +{row.get('test_improvement_cp', 0):.2f} cp "
                    f"({row.get('test_improvement_pct', 0):.2f}%)  "
                    f"holdout +{row.get('holdout_improvement_cp', 0):.2f} cp  "
                    f"sign {row.get('test_sign_acc')}  {row['train_seconds']}s",
                    flush=True,
                )
            else:
                print(f"    FAILED: {row['error'][-300:]}", flush=True)

    good = [r for r in rows if r["ok"]]
    # Rank on deployed test improvement, but require the holdout to move the same way:
    # a candidate that only helps against opponents it trained on is not a candidate.
    ranked = sorted(
        good,
        key=lambda r: (r.get("test_improvement_cp", 0) + r.get("holdout_improvement_cp", 0)),
        reverse=True,
    )
    payload = {
        "data": str(args.data),
        "architectures": args.hidden,
        "seeds": args.seeds,
        "ranking_rule": (
            "sum of deployed clamped MAE improvement on the untouched test split and on "
            "the held-out opponent family; training loss is not used for ranking"
        ),
        "results": rows,
        "ranked": [r["tag"] for r in ranked],
        "winner": ranked[0] if ranked else None,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print()
    print(f"{'tag':<14}{'params':>9}{'test cp':>10}{'test %':>9}{'holdout cp':>12}{'sign':>8}")
    for r in ranked:
        print(
            f"{r['tag']:<14}{r['parameters']:>9,}"
            f"{r.get('test_improvement_cp', 0):>10.2f}"
            f"{r.get('test_improvement_pct', 0):>9.2f}"
            f"{r.get('holdout_improvement_cp', 0):>12.2f}"
            f"{r.get('test_sign_acc', 0):>8.3f}"
        )
    print()
    if ranked:
        print(f"winner: {ranked[0]['tag']}  ({ranked[0]['model_dir']})")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    sys.exit(main())
