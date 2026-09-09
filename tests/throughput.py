"""Throughput and exact-equivalence harness for a semantics-preserving optimisation.

The rule this file enforces is that a candidate may only be faster, never different.
`equivalence` compares the candidate against the pinned control on every position of
every corpus group at several fixed depths and requires the chosen move, the score and
the **node count** to agree exactly, plus every root move's score. A candidate that
changes a single node count is not semantics preserving and is rejected here, before any
timing is believed.

`bench` measures end-to-end wall time on the clean sources, alternating control and
candidate within each repetition so that machine drift affects both equally, and reports
the median-of-repetitions ratio.

Modes:

    bench        wall-clock throughput, control vs candidate, alternating repetitions
    equivalence  exact move, score, node and root-score agreement at fixed depths
"""

from __future__ import annotations

import argparse
import gc
import hashlib
import importlib.util
import json
import statistics
import sys
import time
import types
from pathlib import Path
from typing import Any

import chess

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
CONTROL_PATH = ROOT / "agent.py"
CONTROL_SHA = "65ec40ceb29a8f6fe14a74ab2ed6ca446164f701e46d2b9d3fe8d5af94655bda"
RESULTS = ROOT / "tests" / "results" / "throughput"


def _qprofile() -> types.ModuleType:
    """Reuse `qprofile.py`'s corpus and root probe rather than restating them."""
    name = "throughput_qp"
    cached = sys.modules.get(name)
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location(name, HERE / "qprofile.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


QP = _qprofile()


def load(name: str, text: str) -> types.ModuleType:
    spec = importlib.util.spec_from_loader(name, loader=None)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    exec(compile(text, name, "exec"), module.__dict__)
    return module


def control_text() -> str:
    raw = CONTROL_PATH.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    assert digest == CONTROL_SHA, ("control moved", digest)
    return raw.decode()


def candidate_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def positions(groups: list[str]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for group in groups:
        for row in QP.corpus(group):
            rows.append({"group": group, "id": row["id"], "fen": row["fen"]})
    return rows


def probe(module: types.ModuleType, board: chess.Board, depth: int) -> dict[str, Any]:
    """One fixed-depth root search, reporting everything a candidate must not change."""
    work = board.copy()
    engine = module.Engine()
    engine.stats = {"model_seconds": 0.0, "depth": 0, "nodes": 0}
    engine.deadline = time.perf_counter() + 36000.0
    engine.enter(work)
    engine.pattern = module.recognise(work, module.evaluate(work))
    best_score = -module.INF
    best_move = next(iter(work.legal_moves))
    scores: dict[str, int] = {}
    for move in engine.order(work, list(work.legal_moves), None, 0):
        work.push(move)
        key = engine.enter(work)
        try:
            value = -engine.search(work, depth - 1, -module.INF, -best_score, 1)
        finally:
            engine.leave(key)
            work.pop()
        scores[move.uci()] = value
        if value > best_score:
            best_score, best_move = value, move
    return {
        "best": best_move.uci(),
        "score": best_score,
        "nodes": engine.nodes,
        "root_scores": scores,
    }


def equivalence(candidate: Path, groups: list[str], depths: tuple[int, ...]) -> dict[str, Any]:
    control = load("throughput_control", control_text())
    trial = load("throughput_candidate", candidate_text(candidate))
    rows = positions(groups)
    mismatches: list[dict[str, Any]] = []
    compared = 0
    for depth in depths:
        for row in rows:
            board = chess.Board(row["fen"])
            a = probe(control, board, depth)
            b = probe(trial, board, depth)
            compared += 1
            if a != b:
                mismatches.append(
                    {
                        "id": row["id"],
                        "group": row["group"],
                        "fen": row["fen"],
                        "depth": depth,
                        "control": {k: a[k] for k in ("best", "score", "nodes")},
                        "candidate": {k: b[k] for k in ("best", "score", "nodes")},
                        "root_scores_differ": a["root_scores"] != b["root_scores"],
                    }
                )
                print(
                    f"MISMATCH {row['id']} d{depth} "
                    f"control={a['best']}/{a['score']}/{a['nodes']} "
                    f"candidate={b['best']}/{b['score']}/{b['nodes']}",
                    file=sys.stderr,
                    flush=True,
                )
        print(f"depth {depth}: {len(rows)} positions compared", file=sys.stderr, flush=True)
    return {
        "candidate": str(candidate),
        "candidate_sha256": hashlib.sha256(candidate.read_bytes()).hexdigest(),
        "groups": groups,
        "depths": list(depths),
        "comparisons": compared,
        "mismatches": len(mismatches),
        "passed": not mismatches,
        "detail": mismatches[:50],
    }


def workload(module: types.ModuleType, boards: list[chess.Board], depth: int) -> int:
    total = 0
    for board in boards:
        total += int(probe(module, board, depth)["nodes"])
    return total


def bench(
    candidate: Path, groups: list[str], depth: int, repeats: int
) -> dict[str, Any]:
    """Alternate control and candidate within each repetition, so drift hits both."""
    control = load("throughput_control", control_text())
    trial = load("throughput_candidate", candidate_text(candidate))
    boards = [chess.Board(r["fen"]) for r in positions(groups)]

    # One untimed warm-up each: Numba compilation and the OS page cache must not land
    # inside a measured repetition.
    workload(control, boards, depth)
    workload(trial, boards, depth)

    control_times: list[float] = []
    trial_times: list[float] = []
    control_nodes = trial_nodes = 0
    for index in range(repeats):
        gc.collect()
        started = time.perf_counter()
        control_nodes = workload(control, boards, depth)
        control_times.append(time.perf_counter() - started)
        gc.collect()
        started = time.perf_counter()
        trial_nodes = workload(trial, boards, depth)
        trial_times.append(time.perf_counter() - started)
        print(
            f"rep {index + 1}: control {control_times[-1]:.3f}s  "
            f"candidate {trial_times[-1]:.3f}s  "
            f"speedup {control_times[-1] / trial_times[-1]:.4f}x",
            file=sys.stderr,
            flush=True,
        )

    control_median = statistics.median(control_times)
    trial_median = statistics.median(trial_times)
    speedup = control_median / trial_median
    return {
        "candidate": str(candidate),
        "candidate_sha256": hashlib.sha256(candidate.read_bytes()).hexdigest(),
        "groups": groups,
        "depth": depth,
        "positions": len(boards),
        "repeats": repeats,
        "control_seconds": [round(t, 4) for t in control_times],
        "candidate_seconds": [round(t, 4) for t in trial_times],
        "control_median_s": round(control_median, 4),
        "candidate_median_s": round(trial_median, 4),
        "control_nodes": control_nodes,
        "candidate_nodes": trial_nodes,
        "nodes_identical": control_nodes == trial_nodes,
        "control_nps": round(control_nodes / control_median),
        "candidate_nps": round(trial_nodes / trial_median),
        "speedup": round(speedup, 4),
        "improvement_pct": round((speedup - 1.0) * 100, 2),
        "per_rep_speedup": [
            round(c / t, 4) for c, t in zip(control_times, trial_times, strict=True)
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("bench", "equivalence"))
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--groups", default="rated,quiet")
    parser.add_argument("--depth", type=int, default=4)
    parser.add_argument("--depths", default="2,3,4")
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--out", default="")
    args = parser.parse_args()
    groups = args.groups.split(",")
    path = Path(args.candidate)

    if args.mode == "bench":
        report: dict[str, Any] = bench(path, groups, args.depth, args.repeats)
    else:
        report = equivalence(path, groups, tuple(int(d) for d in args.depths.split(",")))

    text = json.dumps(report, indent=2)
    if args.out:
        target = RESULTS / args.out
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        print(f"wrote {target}", file=sys.stderr)
    print(text)


if __name__ == "__main__":
    main()
