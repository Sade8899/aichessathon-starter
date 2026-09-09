"""Calibrate game-generation concurrency at 1, 2, 4 and 6 workers.

Selection is by measured total throughput, not by the largest worker count. A level is
rejected if per-agent search quality degrades: the control's median move time is the
proxy for that here, because the control is a fixed-clock searcher and a slower core
means fewer nodes inside the same wall-clock slice.

The i7-8700 has six physical cores and twelve logical. Six simultaneous games is the
declared ceiling; the previously rejected 24-worker configuration is not repeated.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import shutil
import statistics
import subprocess
import sys
import time

REPO = pathlib.Path(__file__).resolve().parent.parent
PY = REPO / ".venv" / "Scripts" / "python.exe"
GAMES_DIR = REPO / "tests" / "results" / "nnue" / "games"


def run_level(workers: int, games: int, offset: int) -> dict:
    """Play `games` games at `workers` concurrency, from a clean output directory."""
    if GAMES_DIR.exists():
        shutil.rmtree(GAMES_DIR)
    GAMES_DIR.mkdir(parents=True, exist_ok=True)

    started = time.perf_counter()
    proc = subprocess.run(
        [
            str(PY),
            str(REPO / "tests" / "nnue_generate.py"),
            "--workers",
            str(workers),
            "--limit",
            str(games),
            "--offset",
            str(offset),
        ],
        capture_output=True,
        text=True,
        cwd=str(REPO),
    )
    elapsed = time.perf_counter() - started
    if proc.returncode != 0:
        return {"workers": workers, "error": proc.stderr[-2000:]}

    move_times: list[float] = []
    plies: list[int] = []
    positions = 0
    failures = 0
    for path in GAMES_DIR.glob("*.json"):
        rec = json.loads(path.read_text(encoding="utf-8"))
        positions += len(rec["positions"])
        plies.append(rec["plies"])
        if rec["failure"]:
            failures += 1
        if rec["control_move_ms_mean"] is not None:
            move_times.append(rec["control_move_ms_mean"])

    return {
        "workers": workers,
        "games": games,
        "elapsed_seconds": round(elapsed, 2),
        "games_per_hour": round(games / elapsed * 3600, 1),
        "positions": positions,
        "positions_per_hour": round(positions / elapsed * 3600, 1),
        "control_move_ms_median": round(statistics.median(move_times), 2) if move_times else None,
        "control_move_ms_mean": round(statistics.fmean(move_times), 2) if move_times else None,
        "mean_plies": round(statistics.fmean(plies), 1) if plies else None,
        "failures": failures,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--games-per-level", type=int, default=12)
    ap.add_argument("--levels", type=int, nargs="+", default=[1, 2, 4, 6])
    args = ap.parse_args()

    rows = []
    # Every level plays the SAME games. Using a different slice per level would let
    # opening variation masquerade as contention, which is exactly the effect the
    # calibration is trying to isolate.
    offset = 100  # keep calibration games away from the head of the manifest
    for workers in args.levels:
        print(f"--- {workers} worker(s) ---", flush=True)
        row = run_level(workers, args.games_per_level, offset)
        rows.append(row)
        print(json.dumps(row, indent=2), flush=True)

    baseline = next((r for r in rows if r.get("workers") == 1), None)
    for row in rows:
        base_ms = baseline.get("control_move_ms_median") if baseline else None
        if base_ms and row.get("control_move_ms_median"):
            slow = row["control_move_ms_median"] / base_ms
            row["move_time_inflation_vs_1worker"] = round(slow, 3)
            # a slower move at a fixed clock means fewer nodes, i.e. degraded search
            row["degradation_exceeds_10pct"] = slow > 1.10

    ok = [r for r in rows if not r.get("degradation_exceeds_10pct") and not r.get("error")]
    best = max(ok, key=lambda r: r.get("positions_per_hour", 0)) if ok else None

    out = REPO / "tests" / "results" / "nnue" / "docker_calibration" / "concurrency.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "host": "Intel Core i7-8700, 6 physical cores, 12 logical",
        "execution": "native Windows",
        "why_not_docker": (
            "The benchmark opponents are Windows .exe binaries and cannot run in a Linux "
            "container. Game generation is therefore native; Docker is used for the "
            "agent-only platform-reproduction gates, where no external engine is needed."
        ),
        "thread_env": {
            "OMP_NUM_THREADS": "1",
            "MKL_NUM_THREADS": "1",
            "OPENBLAS_NUM_THREADS": "1",
            "NUMEXPR_NUM_THREADS": "1",
            "TORCH_NUM_THREADS": "1",
        },
        "rejected_configuration": "24 workers (previously rejected, not retried)",
        "games_identical_across_levels": True,
        "calibration_offset": 100,
        "levels": rows,
        "selected_workers": best["workers"] if best else 1,
        "selection_rule": (
            "highest positions/hour among levels with <=10% move-time inflation"
        ),
    }
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print()
    header = f"{'workers':>8} {'games/h':>10} {'pos/h':>10} {'move ms':>9}"
    print(header + f" {'inflation':>10} {'fail':>5}")
    for row in rows:
        print(
            f"{row.get('workers'):>8} {row.get('games_per_hour', 0):>10,.0f} "
            f"{row.get('positions_per_hour', 0):>10,.0f} "
            f"{row.get('control_move_ms_median', 0):>9.1f} "
            f"{row.get('move_time_inflation_vs_1worker', 1.0):>10.3f} "
            f"{row.get('failures', 0):>5}"
        )
    print(f"\nselected workers: {payload['selected_workers']}")
    print(f"wrote {out}")
    if GAMES_DIR.exists():
        shutil.rmtree(GAMES_DIR)
    return 0


if __name__ == "__main__":
    sys.exit(main())
