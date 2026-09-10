"""Package a V3 candidate and validate it inside the platform container.

Builds the submission zip -- `agent.py` at the ROOT of the archive, not inside a folder,
plus the weight file beside it -- and runs it in an image carrying the competition stack
and nothing else: no Stockfish, no benchmark engines, no training data, no repository.
One CPU, 2 GB, no network, read-only filesystem with 256 MB at /tmp.

Separate from `nnue_v2_docker.py` rather than an edit to it, for the same reason that
script was separate from V1's: the recorded output of each is what its own report cites,
and a candidate path that moved under an earlier report would make it unreproducible.
The inner validation script is imported from the V2 module unchanged, so the two reports
are measuring the same thing.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile
import zipfile
from typing import Any

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tests"))

import nnue_v2_docker as v2d  # noqa: E402

IMAGE = v2d.IMAGE
V3 = REPO / "tests" / "results" / "nnue" / "v3"


def build_zip(
    target: pathlib.Path, agent_path: pathlib.Path, weights_path: pathlib.Path
) -> dict[str, Any]:
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("agent.py", agent_path.read_bytes())
        # The block's loader looks for this exact name beside the agent.
        archive.writestr("nnue_v2_weights.npz", weights_path.read_bytes())
    with zipfile.ZipFile(target) as archive:
        members = [
            {
                "name": info.filename,
                "bytes": info.file_size,
                "sha256": hashlib.sha256(archive.read(info.filename)).hexdigest(),
            }
            for info in archive.infolist()
        ]
        uncompressed = sum(info.file_size for info in archive.infolist())
    return {
        "zip": str(target),
        "zip_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        "zip_bytes": target.stat().st_size,
        "members": members,
        "uncompressed_bytes": uncompressed,
        "under_50mb": uncompressed < 50_000_000,
        "agent_at_root": any(m["name"] == "agent.py" for m in members),
        "no_folders": all("/" not in str(m["name"]) for m in members),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", required=True)
    ap.add_argument("--agent", type=pathlib.Path, required=True)
    ap.add_argument("--weights", type=pathlib.Path, required=True)
    ap.add_argument("--mode", default="eval")
    ap.add_argument("--image", default=IMAGE)
    args = ap.parse_args()

    outdir = V3 / args.tag
    outdir.mkdir(parents=True, exist_ok=True)
    zip_path = outdir / f"candidate_{args.mode}.zip"
    packaging = build_zip(zip_path, args.agent, args.weights)

    workdir = pathlib.Path(tempfile.mkdtemp(prefix="nnue-v3-docker-"))
    try:
        agent_dir = workdir / "agent"
        agent_dir.mkdir()
        with zipfile.ZipFile(zip_path) as archive:
            archive.extractall(agent_dir)
        (workdir / "validate.py").write_text(v2d.INNER, encoding="utf-8")
        command = [
            "docker", "run", "--rm",
            "--cpus=1", "--memory=2g", "--memory-swap=2g",
            "--network", "none",
            "--read-only", "--tmpfs", "/tmp:size=256m",
            "-v", f"{agent_dir}:/agent:ro",
            "-v", f"{workdir / 'validate.py'}:/validate.py:ro",
            args.image, "python", "/validate.py",
        ]
        print(" ".join(command), flush=True)
        proc = subprocess.run(command, capture_output=True, text=True, timeout=3600)
        if "###REPORT###" in proc.stdout:
            inner = json.loads(proc.stdout.split("###REPORT###", 1)[1].strip())
        else:
            inner = {
                "error": "no report",
                "returncode": proc.returncode,
                "stdout": proc.stdout[-3000:],
                "stderr": proc.stderr[-3000:],
            }
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    image_id = subprocess.run(
        ["docker", "image", "inspect", args.image, "--format", "{{.Id}}"],
        capture_output=True, text=True,
    ).stdout.strip()
    docker_version = subprocess.run(
        ["docker", "--version"], capture_output=True, text=True
    ).stdout.strip()

    report = {
        "tag": args.tag,
        "mode": args.mode,
        "image": args.image,
        "image_id": image_id,
        "docker_version": docker_version,
        "packaging": packaging,
        "container": inner,
        "control_sha256": hashlib.sha256((REPO / "agent.py").read_bytes()).hexdigest(),
    }
    (outdir / f"docker_{args.mode}.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
