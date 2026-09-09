"""Compare native and containerised runs of the same deterministic suite.

Fixed-depth searches must agree exactly: same move, same score, same node count. If they
do not, one of the two environments is not running the engine the other is, and no
strength result from either can be trusted.

Wall-clock speed is expected to differ and is measured rather than assumed.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parent.parent
IMAGE = "chessathon-nnue:platform"

SUITE = r'''
import json, sys, time
import os
sys.path.insert(0, "/agent" if os.path.isdir("/agent") else os.getcwd())
import chess, agent

FENS = [
    chess.STARTING_FEN,
    "r1bqkb1r/pppp1ppp/2n2n2/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 4 4",
    "r4rk1/p1pq2pp/1np3p1/4P3/1P1p1BP1/1P3P2/P2Q4/2RK1B1R w - - 0 22",
    "1r2r1k1/6p1/2p1b3/p1qnQpBp/P1B5/1PP5/5PPP/3RR1K1 w - - 1 24",
    "8/2p5/3p4/KP5r/1R3p1k/8/4P1P1/8 w - - 0 1",
    "6k1/5ppp/8/8/8/8/5PPP/3R2K1 w - - 0 1",
]

def root(board, depth):
    engine = agent.Engine()
    engine.deadline = float("inf")
    engine.nodes = 0
    best, best_score = None, -agent.INF
    for move in engine.order(board, list(board.legal_moves), None, 0):
        board.push(move)
        key = engine.enter(board)
        try:
            value = -engine.search(board, depth - 1, -agent.INF, -best_score, 1)
        finally:
            engine.leave(key)
            board.pop()
        if value > best_score:
            best_score, best = value, move
    return {"best": best.uci(), "score": int(best_score), "nodes": engine.nodes}

rows = []
for fen in FENS:
    for depth in (3, 4):
        t0 = time.perf_counter()
        out = root(chess.Board(fen), depth)
        out.update(fen=fen, depth=depth, seconds=round(time.perf_counter() - t0, 4))
        rows.append(out)

t0 = time.perf_counter()
nodes = 0
for fen in FENS:
    r = root(chess.Board(fen), 4)
    nodes += r["nodes"]
elapsed = time.perf_counter() - t0

print("###REPORT###")
print(json.dumps({
    "rows": rows,
    "nps": round(nodes / elapsed, 1),
    "nodes": nodes,
    "seconds": round(elapsed, 3),
    "python": sys.version.split()[0],
    "nnue_status": getattr(agent, "_nnue_status", "n/a"),
}))
'''


def extract(stdout: str) -> dict:
    if "###REPORT###" not in stdout:
        return {"error": "no report", "tail": stdout[-2000:]}
    return json.loads(stdout.split("###REPORT###", 1)[1].strip())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent", type=pathlib.Path, default=REPO / "agent.py")
    ap.add_argument("--weights", type=pathlib.Path, default=None)
    ap.add_argument("--image", default=IMAGE)
    ap.add_argument(
        "--out",
        type=pathlib.Path,
        default=(
            REPO / "tests" / "results" / "nnue" / "docker_calibration" / "native_vs_docker.json"
        ),
    )
    args = ap.parse_args()

    workdir = pathlib.Path(tempfile.mkdtemp(prefix="nvd-"))
    agent_dir = workdir / "agent"
    agent_dir.mkdir()
    (agent_dir / "agent.py").write_bytes(args.agent.read_bytes())
    if args.weights and args.weights.exists():
        (agent_dir / args.weights.name).write_bytes(args.weights.read_bytes())
    suite = workdir / "suite.py"
    suite.write_text(SUITE, encoding="utf-8")

    native = subprocess.run(
        [str(REPO / ".venv" / "Scripts" / "python.exe"), str(suite)],
        capture_output=True, text=True, cwd=str(agent_dir),
    )
    docker = subprocess.run(
        [
            "docker", "run", "--rm", "--cpus=1", "--memory=2g", "--network", "none",
            "-v", f"{agent_dir}:/agent:ro", "-v", f"{suite}:/suite.py:ro",
            "-w", "/agent", args.image, "python", "/suite.py",
        ],
        capture_output=True, text=True,
    )

    n = extract(native.stdout)
    d = extract(docker.stdout)

    mismatches = []
    if "rows" in n and "rows" in d:
        for a, b in zip(n["rows"], d["rows"], strict=False):
            if (a["best"], a["score"], a["nodes"]) != (b["best"], b["score"], b["nodes"]):
                mismatches.append(
                    {
                        "fen": a["fen"],
                        "depth": a["depth"],
                        "native": {k: a[k] for k in ("best", "score", "nodes")},
                        "docker": {k: b[k] for k in ("best", "score", "nodes")},
                    }
                )

    report = {
        "agent": str(args.agent),
        "image": args.image,
        "native": {k: n.get(k) for k in ("python", "nps", "nodes", "seconds", "nnue_status")},
        "docker": {k: d.get(k) for k in ("python", "nps", "nodes", "seconds", "nnue_status")},
        "fixed_depth_decisions_identical": not mismatches and bool(n.get("rows")),
        "mismatches": mismatches,
        "nps_ratio_docker_over_native": (
            round(d["nps"] / n["nps"], 4) if n.get("nps") and d.get("nps") else None
        ),
        "native_stderr": native.stderr[-1500:] if native.returncode else "",
        "docker_stderr": docker.stderr[-1500:] if docker.returncode else "",
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    print()
    print(f"fixed-depth decisions identical: {report['fixed_depth_decisions_identical']}")
    if not report["fixed_depth_decisions_identical"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
