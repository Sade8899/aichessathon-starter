"""Materialise a checkpoint as a standalone candidate in its own directory.

`nnue_v2_pack.py` installs one checkpoint at the repo root, which means only one
candidate exists at a time and any two things that want different candidates race each
other. Screening several points on the preservation frontier needs several candidates to
exist at once, so this writes each into its own directory.

The agent resolves its weight file from the directory of its own `__file__` first, so a
snapshot directory containing `agent_v2.py` and `nnue_v2_weights.npz` is self-contained
and loads its own weights regardless of what sits at the repo root.
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

import nnue_v2_build_agent as builder  # noqa: E402

V2 = REPO / "tests" / "results" / "nnue" / "v2"
SNAPSHOTS = V2 / "snapshots"


def materialise(tag: str) -> dict[str, object]:
    source = V2 / tag / "quantized.npz"
    if not source.exists():
        raise SystemExit(f"no checkpoint at {source}")

    dest = SNAPSHOTS / tag
    dest.mkdir(parents=True, exist_ok=True)
    weights = dest / "nnue_v2_weights.npz"
    shutil.copyfile(source, weights)
    digest = hashlib.sha256(weights.read_bytes()).hexdigest()

    agent = dest / "agent_v2.py"
    text = builder.build(digest)
    agent.write_text(text, encoding="utf-8")

    # The same round-trip assertion the packer makes: the control half is unmodified.
    if builder.strip(text) != (REPO / "agent.py").read_text(encoding="utf-8"):
        raise SystemExit("round trip to the control failed")

    info = {
        "tag": tag,
        "agent": str(agent),
        "agent_sha256": hashlib.sha256(agent.read_bytes()).hexdigest(),
        "weights": str(weights),
        "weight_sha256": digest,
        "weight_bytes": weights.stat().st_size,
    }
    (dest / "snapshot.json").write_text(json.dumps(info, indent=2), encoding="utf-8")
    return info


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("tags", nargs="+")
    args = ap.parse_args()
    for tag in args.tags:
        info = materialise(tag)
        print(f"{tag}: {info['agent']}  weights {info['weight_sha256'][:16]}")


if __name__ == "__main__":
    main()
