"""Pack the trained weights for shipping and stamp their hash into the candidate.

The candidate validates the weight file's SHA-256 at import, so the hash has to be
written into the source before the file is shipped. Doing that by hand is how a
mismatch gets missed, so it is done here and verified immediately afterwards.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import subprocess
import sys

import numpy as np

REPO = pathlib.Path(__file__).resolve().parent.parent
MODELDIR = REPO / "tests" / "results" / "nnue" / "model"
SOURCE = MODELDIR / "quantized.npz"
SHIPPED = REPO / "nnue_weights.npz"
CANDIDATE = REPO / "agent_nnue.py"


def pack(source: pathlib.Path, shipped: pathlib.Path) -> dict:
    with np.load(source) as data:
        arrays = {
            "embed_q": data["embed_q"].astype(np.int8),
            "aux_w_q": data["aux_w_q"].astype(np.int8),
            "aux_b_q": data["aux_b_q"].astype(np.int32),
            "mg_q": data["mg_q"].astype(np.int8),
            "eg_q": data["eg_q"].astype(np.int8),
            "scales": data["scales"].astype(np.float64),
            "biases": data["biases"].astype(np.float64),
        }
    for name in ("embed_q", "aux_w_q", "mg_q", "eg_q"):
        peak = int(np.abs(arrays[name]).max())
        if peak > 127:
            raise SystemExit(f"{name} exceeds int8 range: {peak}")
    # savez without compression, so the bytes are stable and the hash is reproducible
    with shipped.open("wb") as handle:
        np.savez(handle, **arrays)
    payload = shipped.read_bytes()
    return {
        "path": str(shipped.relative_to(REPO)),
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "arrays": {k: {"shape": list(v.shape), "dtype": str(v.dtype)} for k, v in arrays.items()},
        "int8_peaks": {
            name: int(np.abs(arrays[name]).max())
            for name in ("embed_q", "aux_w_q", "mg_q", "eg_q")
        },
    }


def stamp(candidate: pathlib.Path, digest: str) -> bool:
    text = candidate.read_text(encoding="utf-8")
    pattern = re.compile(r'^NNUE_SHA256 = ".*?"', re.MULTILINE)
    if not pattern.search(text):
        raise SystemExit("NNUE_SHA256 assignment not found in the candidate")
    updated = pattern.sub(f'NNUE_SHA256 = "{digest}"', text, count=1)
    if updated == text:
        return False
    candidate.write_text(updated, encoding="utf-8")
    return True


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", type=pathlib.Path, default=SOURCE)
    ap.add_argument("--shipped", type=pathlib.Path, default=SHIPPED)
    ap.add_argument("--candidate", type=pathlib.Path, default=CANDIDATE)
    ap.add_argument("--no-stamp", action="store_true")
    args = ap.parse_args()

    if not args.source.exists():
        raise SystemExit(f"{args.source} does not exist; train the model first")

    info = pack(args.source, args.shipped)
    print(json.dumps(info, indent=2))

    if not args.no_stamp:
        changed = stamp(args.candidate, info["sha256"])
        print(f"stamped NNUE_SHA256 into {args.candidate.name}: {changed}")

    # Verify by importing the candidate in a fresh interpreter: the hash check must
    # pass and the fused path must actually be selected.
    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; sys.path.insert(0, r'"
                + str(REPO)
                + "');\n"
                "import agent_nnue as a, chess;\n"
                "print('STATUS', a._nnue_status);\n"
                "print('READY', a._nnue_ready);\n"
                "print('FUSED', a.fast_evaluate is a.compiled_evaluate_nnue);\n"
                "print('MOVE', a.get_move(chess.Board().fen(), 5000))"
            ),
        ],
        capture_output=True,
        text=True,
        cwd=str(REPO),
    )
    print(probe.stdout.strip() or probe.stderr[-1500:])
    ok = "READY True" in probe.stdout and "FUSED True" in probe.stdout
    print("weight hash validated at import:", ok)
    if not ok:
        raise SystemExit("the packed weights did not load cleanly in a fresh interpreter")

    record = MODELDIR / "packed_weights.json"
    record.write_text(json.dumps(info, indent=2), encoding="utf-8")
    print(f"wrote {record}")


if __name__ == "__main__":
    main()
