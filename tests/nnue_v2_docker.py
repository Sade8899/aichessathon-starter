"""Package the V2 candidate and validate it inside the platform container.

Builds the submission zip -- `agent.py` at the root (the generated candidate) plus the
weight file beside it -- and runs it in an image that carries the competition stack and
nothing else: no Stockfish, no benchmark engines, no training data, no repository. One
CPU, 2 GB, no network, read-only filesystem with 256 MB at /tmp, exactly as documented.

Separate from `nnue_docker_validate.py` rather than an edit to it: that script's recorded
output is what the V1 report cites, and it warms a Numba function name V2 does not have.
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
V2 = REPO / "tests" / "results" / "nnue" / "v2"

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
report["nnue_hidden"] = getattr(agent, "NNUE_HIDDEN", None)
report["nnue_gate"] = getattr(agent, "_NNUE_GATE", None)
report["nnue_phase_gate"] = getattr(agent, "_NNUE_PHASE_GATE", None)

# Numba must already be compiled at import: the first timed move must not pay for it.
warm = []
for name in ("numeric_evaluate", "nnue_accumulate", "numeric_evaluate_v2", "compiled_evaluate"):
    fn = getattr(agent, name, None)
    sigs = getattr(fn, "signatures", None)
    if sigs is not None:
        warm.append({"function": name, "compiled_signatures": len(sigs)})
report["numba_warm_at_import"] = warm
report["fused_kernel_warm"] = any(
    w["function"] == "numeric_evaluate_v2" and w["compiled_signatures"] > 0 for w in warm
)

# The correction must actually be live in the container, not silently degraded.
probe = chess.Board("r1bqkb1r/pppp1ppp/2n2n2/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 4 4")
report["correction_sample"] = (
    int(agent.nnue_correction(probe)) if hasattr(agent, "nnue_correction") else None
)
report["eval_differs_from_base"] = bool(report["correction_sample"])

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


def build_zip(target: pathlib.Path) -> dict[str, object]:
    """agent.py at the ROOT of the zip, not inside a folder, plus the weight file."""
    agent_src = (REPO / "agent_nnue_v2.py").read_bytes()
    weights = (REPO / "nnue_v2_weights.npz").read_bytes()
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("agent.py", agent_src)
        archive.writestr("nnue_v2_weights.npz", weights)
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
        "zip": str(target.relative_to(REPO)),
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
    ap.add_argument("--image", default=IMAGE)
    args = ap.parse_args()

    outdir = V2 / args.tag
    outdir.mkdir(parents=True, exist_ok=True)
    zip_path = outdir / "candidate.zip"
    packaging = build_zip(zip_path)

    workdir = pathlib.Path(tempfile.mkdtemp(prefix="nnue-v2-docker-"))
    try:
        agent_dir = workdir / "agent"
        agent_dir.mkdir()
        with zipfile.ZipFile(zip_path) as archive:
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
        print(" ".join(command), flush=True)
        proc = subprocess.run(command, capture_output=True, text=True, timeout=3600)
        if "###REPORT###" in proc.stdout:
            inner = json.loads(proc.stdout.split("###REPORT###", 1)[1].strip())
        else:
            inner = {
                "error": "no report",
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
        **packaging,
        "image": args.image,
        "image_id": image_id,
        "docker_version": docker_version,
        "dockerfile_sha256": hashlib.sha256(
            (REPO / "tests" / "Dockerfile.nnue").read_bytes()
        ).hexdigest(),
        "constraints": {
            "cpus": 1,
            "memory": "2g",
            "network": "none",
            "read_only": True,
            "tmpfs": "/tmp:size=256m",
        },
        "container": inner,
        "passes": bool(
            packaging["under_50mb"]
            and packaging["agent_at_root"]
            and packaging["no_folders"]
            and inner.get("import_under_90s")
            and inner.get("nnue_ready")
            and inner.get("fused_kernel_warm")
            and inner.get("all_legal")
            and inner.get("no_flags")
            and inner.get("peak_under_2gb")
        ),
    }
    (outdir / "docker.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
