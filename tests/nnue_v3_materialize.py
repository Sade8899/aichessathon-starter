"""Materialise a V3 checkpoint as a standalone candidate, in one or both modes.

Each snapshot is self-contained: the agent resolves its weight file from the directory
of its own `__file__` before anywhere else, so several candidates can exist and be
arena-tested at the same time without racing over one file at the repo root.

The same checkpoint can be materialised in both integration modes from the same weights,
which is what makes EVAL and ORDER a controlled comparison rather than two experiments:
the trained knowledge is byte-identical and only its point of application differs.

`agent.py` is never written. Every build asserts that stripping the inserted block
reproduces the control byte for byte.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import shutil
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tests"))

import nnue_v3_build_agent as builder  # noqa: E402

V3 = REPO / "tests" / "results" / "nnue" / "v3"
SNAPSHOTS = V3 / "snapshots"


def materialise(tag: str, mode: str, form: str) -> dict[str, object]:
    source = V3 / tag / "quantized.npz"
    if not source.exists():
        raise SystemExit(f"no checkpoint at {source}")

    dest = SNAPSHOTS / f"{tag}_{mode}_{form}"
    dest.mkdir(parents=True, exist_ok=True)
    # The block's loader looks for this exact filename beside the agent; keeping V2's
    # name means the block itself needs no edit and cannot drift from the proved copy.
    weights = dest / "nnue_v2_weights.npz"
    shutil.copyfile(source, weights)
    digest = hashlib.sha256(weights.read_bytes()).hexdigest()

    agent = dest / f"agent_v3_{mode}.py"
    text = builder.build(digest, mode, form)
    agent.write_text(text, encoding="utf-8")
    if builder.strip(text, mode) != (REPO / "agent.py").read_text(encoding="utf-8"):
        raise SystemExit(f"round trip to the control failed for {tag} in {mode} mode")

    info = {
        "tag": tag,
        "mode": mode,
        "form": form,
        "agent": str(agent),
        "agent_sha256": hashlib.sha256(agent.read_bytes()).hexdigest(),
        "agent_bytes": agent.stat().st_size,
        "weights": str(weights),
        "weight_sha256": digest,
        "weight_bytes": weights.stat().st_size,
        "control_sha256": hashlib.sha256((REPO / "agent.py").read_bytes()).hexdigest(),
    }
    (dest / "snapshot.json").write_text(json.dumps(info, indent=2), encoding="utf-8")
    return info


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("tags", nargs="+")
    ap.add_argument("--modes", default="eval")
    ap.add_argument("--form", default="additive")
    args = ap.parse_args()
    for tag in args.tags:
        for mode in args.modes.split(","):
            info = materialise(tag, mode, args.form)
            print(f"{tag} [{mode}/{args.form}]: {info['agent']}  weights {info['weight_sha256'][:16]}")


if __name__ == "__main__":
    main()
