"""Validate a submission zip inside a clean platform container.

The container sees the contents of the zip and nothing else: no repository, no engines,
no datasets, one CPU, 2 GB, no network. This is the only place final initialization,
memory and legality numbers are taken from, because the host interpreter is Python 3.14
and the platform's is 3.12.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import shutil
import subprocess
import tempfile
import zipfile

REPO = pathlib.Path(__file__).resolve().parent.parent
IMAGE = "chessathon-nnue:platform"

INNER = r'''
import json, os, resource, sys, time
sys.path.insert(0, "/agent")

report = {}
t0 = time.perf_counter()
import agent
report["import_seconds"] = round(time.perf_counter() - t0, 3)
report["import_under_90s"] = report["import_seconds"] < 90

import chess
report["has_get_move"] = callable(getattr(agent, "get_move", None))
report["nnue_status"] = getattr(agent, "_nnue_status", "n/a")
report["nnue_ready"] = getattr(agent, "_nnue_ready", None)

# Numba must already be compiled: the first timed move must not pay for it.
warm = []
for name in ("numeric_evaluate", "numeric_evaluate_nnue", "compiled_evaluate"):
    fn = getattr(agent, name, None)
    sigs = getattr(fn, "signatures", None)
    if sigs is not None:
        warm.append({"function": name, "compiled_signatures": len(sigs)})
report["numba_warm_at_import"] = warm

def game(agent_white, base_ms=120000, inc_ms=500, cap=300):
    board = chess.Board()
    clock = float(base_ms)
    plies = 0
    times = []
    while plies < cap and not board.is_game_over(claim_draw=True):
        mine = board.turn == (chess.WHITE if agent_white else chess.BLACK)
        if mine:
            t = time.perf_counter()
            uci = agent.get_move(board.fen(), int(clock))
            spent = (time.perf_counter() - t) * 1000.0
            times.append(spent)
            clock -= spent
            if clock <= 0:
                return {"result": "flag", "plies": plies, "legal": True}
            clock += inc_ms
        else:
            # a deterministic, dependency-free opponent: first legal move in UCI order
            uci = sorted(m.uci() for m in board.legal_moves)[0]
        move = chess.Move.from_uci(uci)
        if move not in board.legal_moves:
            return {"result": "illegal", "move": uci, "plies": plies, "legal": False}
        board.push(move)
        plies += 1
    times.sort()
    return {
        "result": (
            board.result(claim_draw=True)
            if board.is_game_over(claim_draw=True)
            else "capped"
        ),
        "plies": plies,
        "legal": True,
        "agent_moves": len(times),
        "move_ms_mean": round(sum(times)/len(times), 1) if times else None,
        "move_ms_max": round(times[-1], 1) if times else None,
        "first_move_ms": None,
    }

report["smoke_white"] = game(True)
report["smoke_black"] = game(False)
report["all_legal"] = report["smoke_white"]["legal"] and report["smoke_black"]["legal"]
report["no_flags"] = "flag" not in (
    report["smoke_white"]["result"],
    report["smoke_black"]["result"],
)

peak_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
report["peak_memory_bytes"] = peak_kb * 1024
report["peak_memory_mb"] = round(peak_kb / 1024, 1)
report["peak_under_2gb"] = peak_kb * 1024 < 2 * 1024**3

report["files_visible"] = sorted(os.listdir("/agent"))
print("###REPORT###")
print(json.dumps(report))
'''


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("zip", type=pathlib.Path)
    ap.add_argument("--out", type=pathlib.Path, default=None)
    ap.add_argument("--image", default=IMAGE)
    args = ap.parse_args()

    payload = args.zip.read_bytes()
    with zipfile.ZipFile(args.zip) as archive:
        members = [
            {
                "name": i.filename,
                "bytes": i.file_size,
                "sha256": hashlib.sha256(archive.read(i.filename)).hexdigest(),
            }
            for i in archive.infolist()
        ]
        uncompressed = sum(i.file_size for i in archive.infolist())

    workdir = pathlib.Path(tempfile.mkdtemp(prefix="nnue-docker-"))
    try:
        agent_dir = workdir / "agent"
        agent_dir.mkdir()
        with zipfile.ZipFile(args.zip) as archive:
            archive.extractall(agent_dir)
        (workdir / "validate.py").write_text(INNER, encoding="utf-8")

        command = [
            "docker", "run", "--rm",
            "--cpus=1", "--memory=2g", "--memory-swap=2g",
            "--network", "none",
            "--read-only", "--tmpfs", "/tmp:size=256m",
            "-v", f"{agent_dir}:/agent:ro",
            "-v", f"{workdir / 'validate.py'}:/validate.py:ro",
            args.image, "python", "/validate.py",
        ]
        print(" ".join(command))
        proc = subprocess.run(command, capture_output=True, text=True, timeout=1800)
        stdout = proc.stdout
        inner: dict = {}
        if "###REPORT###" in stdout:
            inner = json.loads(stdout.split("###REPORT###", 1)[1].strip())
        else:
            inner = {"error": "no report", "stdout": stdout[-3000:], "stderr": proc.stderr[-3000:]}
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    image_id = subprocess.run(
        ["docker", "image", "inspect", args.image, "--format", "{{.Id}}"],
        capture_output=True, text=True,
    ).stdout.strip()
    docker_version = subprocess.run(
        ["docker", "--version"], capture_output=True, text=True
    ).stdout.strip()
    dockerfile = REPO / "tests" / "Dockerfile.nnue"

    report = {
        "zip": str(args.zip),
        "zip_sha256": hashlib.sha256(payload).hexdigest(),
        "zip_bytes": len(payload),
        "members": members,
        "uncompressed_bytes": uncompressed,
        "under_50mb": uncompressed < 50_000_000,
        "image": args.image,
        "image_id": image_id,
        "docker_version": docker_version,
        "dockerfile_sha256": hashlib.sha256(dockerfile.read_bytes()).hexdigest(),
        "constraints": {
            "cpus": 1,
            "memory": "2g",
            "network": "none",
            "root_filesystem": "read-only",
            "tmpfs": "/tmp size=256m",
            "repository_mounted": False,
        },
        "container": inner,
        "exit_code": proc.returncode,
    }
    report["passes"] = bool(
        proc.returncode == 0
        and inner.get("import_under_90s")
        and inner.get("has_get_move")
        and inner.get("all_legal")
        and inner.get("no_flags")
        and inner.get("peak_under_2gb")
        and report["under_50mb"]
    )
    text = json.dumps(report, indent=2)
    print(text)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
    print()
    print(f"DOCKER PLATFORM VALIDATION PASSES: {report['passes']}")


if __name__ == "__main__":
    main()
