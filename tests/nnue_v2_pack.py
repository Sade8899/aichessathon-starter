"""Install a trained V2 checkpoint as the candidate's shipped weights.

Copies the chosen `quantized.npz` to `nnue_v2_weights.npz` at the repo root, hashes it,
and rebuilds `agent_nnue_v2.py` with that hash compiled in. The agent refuses to use a
weight file whose hash does not match, so packing and building are one step and cannot
drift apart.

`agent.py` is never written.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import shutil
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
V2 = REPO / "tests" / "results" / "nnue" / "v2"
WEIGHTS = REPO / "nnue_v2_weights.npz"
AGENT = REPO / "agent_nnue_v2.py"
CONTROL_SHA256 = "65ec40ceb29a8f6fe14a74ab2ed6ca446164f701e46d2b9d3fe8d5af94655bda"


def pack(tag: str) -> dict[str, str | int]:
    control_digest = hashlib.sha256((REPO / "agent.py").read_bytes()).hexdigest()
    if control_digest != CONTROL_SHA256:
        raise SystemExit(f"REFUSING: agent.py is {control_digest}, not the control")

    source = V2 / tag / "quantized.npz"
    if not source.exists():
        raise SystemExit(f"no checkpoint at {source}")
    shutil.copyfile(source, WEIGHTS)
    digest = hashlib.sha256(WEIGHTS.read_bytes()).hexdigest()

    subprocess.run(
        [sys.executable, str(REPO / "tests" / "nnue_v2_build_agent.py"),
         "--weight-sha256", digest],
        check=True,
        cwd=REPO,
    )
    return {
        "tag": tag,
        "weights": str(WEIGHTS.relative_to(REPO)),
        "weight_sha256": digest,
        "weight_bytes": WEIGHTS.stat().st_size,
        "agent": str(AGENT.relative_to(REPO)),
        "agent_sha256": hashlib.sha256(AGENT.read_bytes()).hexdigest(),
        "agent_bytes": AGENT.stat().st_size,
        "control_sha256": control_digest,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("tag", help="checkpoint directory under tests/results/nnue/v2")
    args = ap.parse_args()
    info = pack(args.tag)
    (V2 / args.tag / "packed.json").write_text(json.dumps(info, indent=2), encoding="utf-8")
    print(json.dumps(info, indent=2))


if __name__ == "__main__":
    main()
