"""Score the selected checkpoint on the sealed test split, after selection is frozen.

The plan keeps test and the Loki-family holdout untouched until a checkpoint has been
chosen, so that the number reported here is not the number the choice was made on. This
runs only after that point, and it runs the *deployed* arithmetic -- the truncated
integer correction the agent ships, not the float the trainer optimised.

Test-split metrics are reported, never used to select. If they disagreed with validation
that would be worth knowing and worth saying; it would not license going back and
picking a different checkpoint.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from typing import Any

import numpy as np

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))

import nnue_v2_train as v2  # noqa: E402
import nnue_v3_metrics as v3m  # noqa: E402
import nnue_v3_relative as v3r  # noqa: E402

V3 = REPO / "tests" / "results" / "nnue" / "v3"


def load_quantized(tag: str) -> tuple[dict[str, Any], bool]:
    blob = np.load(V3 / tag / "quantized.npz")
    flags = blob["flags"]
    q = {
        "embed_q": blob["embed"],
        "aux_w_q": blob["aux_w"],
        "aux_b_q": blob["aux_b"],
        "mg_q": blob["mg"],
        "eg_q": blob["eg"],
        "conf_q": blob["conf"],
        "mg_scale": float(blob["scales"][0]),
        "eg_scale": float(blob["scales"][1]),
        "conf_scale": float(blob["scales"][2]),
        "mg_bias": float(blob["biases"][0]),
        "eg_bias": float(blob["biases"][1]),
        "conf_bias": float(blob["biases"][2]),
        "gate": bool(flags[0]),
        "phase_gate": bool(flags[1]),
        "conf_min": float(flags[3]) / 1000.0 if flags.shape[0] > 3 else 0.0,
    }
    return q, bool(flags.shape[0] > 4 and int(flags[4]) == 1)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", required=True)
    args = ap.parse_args()

    groups = v2.load_groups()
    v3_dir = V3 / "groups"
    for path in sorted(v3_dir.glob("groups-*.jsonl")):
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    try:
                        groups.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
    quarantine = set(
        json.loads((V3 / "quarantine.json").read_text(encoding="utf-8"))["group_ids"]
    )
    groups = [g for g in groups if g["group_id"] not in quarantine]
    print(f"{len(groups)} groups after quarantine")

    enc = v2.encode_groups(groups, V3 / "encoded_combined.npz")
    splits = np.array([g["split"] for g in groups])
    q, relative = load_quantized(args.tag)
    print(f"form: {'relative' if relative else 'additive'}")

    out: dict[str, Any] = {"tag": args.tag, "relative": relative}
    for name in ("validation", "test"):
        ids = np.nonzero(splits == name)[0]
        rows = np.concatenate(
            [np.arange(enc["offsets"][g], enc["offsets"][g + 1]) for g in ids]
        )
        gain = v2.quant_correction(q, enc, rows)
        correction = np.zeros(enc["base"].shape[0], dtype=np.float64)
        if relative:
            correction[rows] = v3r.apply_relative(
                enc["base"][rows], gain, v2.CORRECTION_CLAMP
            )
        else:
            correction[rows] = gain
        gm = v3m.ranking_metrics(correction, enc, ids)
        vm = v3m.value_metrics(correction, enc, rows)
        record = {**gm, **vm, "composite": v3m.composite({**gm, **vm})}
        out[name] = {k: round(float(v), 4) for k, v in record.items()}
        print(
            f"{name:<11} groups {int(gm['groups']):>6} "
            f"composite {record['composite']:8.3f} "
            f"preserved {gm['preserved_rate']:.4f} "
            f"regret {gm['regret_reduction_cp']:+7.2f} "
            f"top+ {gm['top_move_accuracy_gain']:+.4f} "
            f"zflip {gm['near_zero_sign_flip_rate']:.4f} "
            f"drawpres {vm['draw_preservation_rate']:.4f} "
            f"mae {vm['mae_gain_cp']:+.2f}"
        )

    (V3 / args.tag / "test_split.json").write_text(
        json.dumps(out, indent=2), encoding="utf-8"
    )
    print(f"wrote {V3 / args.tag / 'test_split.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
