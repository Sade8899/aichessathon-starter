"""Concurrency calibration: identical arena workloads at several worker counts.

Runs on the host and drives Docker directly. Every container at every level plays
exactly the same matched colour pair with the same sources, opening, seed and clock,
so the only variable is how many of them run at once. Block B reverses the container
to CPU mapping so no core index systematically serves the same worker index.
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import time
from pathlib import Path
from typing import Any

IMAGE = "chessathon-scope:test"
RESULTS = Path("tests/results/concurrency")
LOGICAL = 12
PHYSICAL = 6
GAMES_PER_WORKER = 4
WORKLOAD = [
    "python",
    "tests/qgen_checks.py",
    "arena",
    "--cases",
    "2",
    "--seed",
    "59000",
    "--base-ms",
    "10000",
    "--increment-ms",
    "100",
    "--experiment",
    "tests/qcap_validation.json",
]
# Declared before the calibration ran, so the thresholds cannot follow the numbers.
MATERIAL_GAIN = 0.10
MAX_IMBALANCE = 0.03


def placement(workers: int, reverse: bool) -> list[int]:
    """One logical CPU per worker, wrapping when workers exceed the CPU count."""
    cpus = [index % LOGICAL for index in range(workers)]
    if workers <= PHYSICAL:
        # One thread from each distinct physical core.
        cpus = [2 * index for index in range(workers)]
    return list(reversed(cpus)) if reverse else cpus


def sample_stats(names: list[str]) -> dict[str, float]:
    """One docker stats snapshot, returned as megabytes per container."""
    try:
        output = subprocess.run(
            ["docker", "stats", "--no-stream", "--format", "{{.Name}}\t{{.MemUsage}}", *names],
            capture_output=True,
            text=True,
            timeout=120,
        ).stdout
    except subprocess.TimeoutExpired:
        return {}
    usage = {}
    for line in output.splitlines():
        if "\t" not in line:
            continue
        name, memory = line.split("\t", 1)
        amount = memory.split("/")[0].strip()
        for suffix, scale in (("GiB", 1024.0), ("MiB", 1.0), ("KiB", 1 / 1024), ("B", 1 / 1048576)):
            if amount.endswith(suffix):
                usage[name] = float(amount[: -len(suffix)]) * scale
                break
    return usage


def run_block(workers: int, block: str, root: Path) -> dict[str, Any]:
    reverse = block == "B"
    cpus = placement(workers, reverse)
    names = [f"calib-{workers}{block}-{index}" for index in range(workers)]
    subprocess.run(["docker", "rm", "-f", *names], capture_output=True)
    mount = f"type=bind,source={Path.cwd()},target=/workspace,readonly"
    started = time.perf_counter()
    for name, cpu in zip(names, cpus, strict=True):
        subprocess.run(
            [
                "docker", "run", "--detach", "--name", name,
                "--network", "none", "--cpus", "1", "--memory", "2g",
                "--pids-limit", "128", "--read-only", "--tmpfs", "/tmp:rw,size=256m",
                "--cpuset-cpus", str(cpu), "--mount", mount, IMAGE, *WORKLOAD,
            ],
            capture_output=True,
            check=True,
        )
    peak: dict[str, float] = {}
    samples = 0
    while True:
        states = subprocess.run(
            ["docker", "inspect", "--format", "{{.State.Status}}", *names],
            capture_output=True,
            text=True,
        ).stdout.split()
        if "running" not in states:
            break
        for name, megabytes in sample_stats(names).items():
            peak[name] = max(peak.get(name, 0.0), megabytes)
        samples += 1
        time.sleep(5)
    makespan = time.perf_counter() - started
    exits = subprocess.run(
        ["docker", "inspect", "--format", "{{.Name}} {{.State.ExitCode}}", *names],
        capture_output=True,
        text=True,
    ).stdout
    codes = {line.split()[0].lstrip("/"): int(line.split()[1]) for line in exits.splitlines()}
    for name in names:
        log = subprocess.run(["docker", "logs", name], capture_output=True).stdout
        (root / f"{name}.log").write_bytes(log)
    subprocess.run(["docker", "rm", "-f", *names], capture_output=True)
    record = {
        "workers": workers,
        "block": block,
        "cpuset": cpus,
        "makespan_s": makespan,
        "games": workers * GAMES_PER_WORKER,
        "games_per_hour": workers * GAMES_PER_WORKER * 3600 / makespan,
        "exit_codes": codes,
        "nonzero_exits": sorted(n for n, code in codes.items() if code),
        "peak_container_mb": peak,
        "max_container_mb": max(peak.values()) if peak else None,
        "total_container_mb": sum(peak.values()) if peak else None,
        "stats_samples": samples,
    }
    (root / f"block-{workers}{block}.json").write_text(json.dumps(record, indent=2) + "\n")
    print(
        "BLOCK "
        + json.dumps(
            {
                key: record[key]
                for key in (
                    "workers", "block", "makespan_s", "games", "games_per_hour",
                    "nonzero_exits", "max_container_mb", "total_container_mb",
                )
            }
        ),
        flush=True,
    )
    return record


def engine_rows(root: Path, workers: int, block: str) -> list[dict[str, Any]]:
    rows = []
    for path in sorted(root.glob(f"calib-{workers}{block}-*.log")):
        for line in path.read_text(errors="ignore").splitlines():
            if line.startswith('{"case":'):
                rows.append(json.loads(line))
    return rows


def describe(rows: list[dict[str, Any]], config: str) -> dict[str, Any]:
    own = [r for r in rows if r["config"] == config]
    moves = [t for r in own for t in r["move_seconds"]]
    telemetry = [t for r in own for t in r["telemetry"]]
    nps = [t["nodes"] / t["seconds"] for t in telemetry if t["seconds"] > 0]
    return {
        "games": len(own),
        "failures": sum(r["failure"] for r in own),
        "moves": len(moves),
        "median_move_ms": statistics.median(moves) * 1000 if moves else None,
        "worst_move_ms": max(moves) * 1000 if moves else None,
        "median_nps": statistics.median(nps) if nps else None,
        "mean_depth": statistics.mean(t["depth"] for t in telemetry) if telemetry else None,
        "median_initialization_ms": statistics.median(r["initialization_ms"] for r in own),
        "max_initialization_ms": max(r["initialization_ms"] for r in own),
        "max_peak_rss_mb": max((t.get("peak_rss_mb", 0) for t in telemetry), default=None),
    }


def report(root: Path, levels: list[int]) -> None:
    blocks = [json.loads(p.read_text()) for p in sorted(root.glob("block-*.json"))]
    per_level: dict[str, Any] = {}
    for workers in levels:
        own = [b for b in blocks if b["workers"] == workers]
        if not own:
            continue
        rows = [r for b in own for r in engine_rows(root, workers, b["block"])]
        per_level[str(workers)] = {
            "blocks": [
                {
                    "block": b["block"],
                    "cpuset": b["cpuset"],
                    "makespan_s": b["makespan_s"],
                    "games_per_hour": b["games_per_hour"],
                    "max_container_mb": b["max_container_mb"],
                    "total_container_mb": b["total_container_mb"],
                    "nonzero_exits": b["nonzero_exits"],
                }
                for b in own
            ],
            "games": sum(b["games"] for b in own),
            "games_per_hour": (
                sum(b["games"] for b in own) * 3600 / sum(b["makespan_s"] for b in own)
            ),
            "container_exit_failures": sum(len(b["nonzero_exits"]) for b in own),
            "engine_failures": sum(r["failure"] for r in rows),
            "control": describe(rows, "control"),
            "candidate": describe(rows, "candidate"),
        }

    baseline = per_level.get(str(min(levels)))
    contention: dict[str, Any] = {}
    if baseline:
        for workers, data in per_level.items():
            factors = {}
            for config in ("control", "candidate"):
                base = baseline[config]["median_nps"]
                now = data[config]["median_nps"]
                factors[config] = now / base if base and now else None
            imbalance = None
            if factors["control"] and factors["candidate"]:
                imbalance = factors["candidate"] / factors["control"] - 1
            contention[workers] = {
                "nps_retained_vs_baseline": factors,
                "candidate_vs_control_imbalance": imbalance,
                "within_3_percent": abs(imbalance) <= MAX_IMBALANCE
                if imbalance is not None
                else None,
            }

    decision: dict[str, Any] = {"material_gain_threshold": MATERIAL_GAIN}
    if {"6", "12", "24"} <= set(per_level):
        best_small = max(per_level["6"]["games_per_hour"], per_level["12"]["games_per_hour"])
        gain = per_level["24"]["games_per_hour"] / best_small - 1
        clean = (
            per_level["24"]["engine_failures"] == 0
            and per_level["24"]["container_exit_failures"] == 0
        )
        decision |= {
            "best_of_6_and_12_games_per_hour": best_small,
            "games_per_hour_at_24": per_level["24"]["games_per_hour"],
            "gain_over_best_small": gain,
            "material": gain >= MATERIAL_GAIN,
            "imbalance_within_3_percent": contention.get("24", {}).get("within_3_percent"),
            "no_failures_at_24": clean,
            "approve_24_for_exploratory_screens": bool(
                gain >= MATERIAL_GAIN
                and contention.get("24", {}).get("within_3_percent")
                and clean
            ),
        }
    output = {"levels": per_level, "contention": contention, "decision": decision}
    (root / "calibration.json").write_text(json.dumps(output, indent=2) + "\n")
    print(json.dumps(output, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("run", "report"))
    parser.add_argument("--levels", default="6,12,24")
    parser.add_argument("--blocks", default="A,B")
    args = parser.parse_args()
    levels = [int(value) for value in args.levels.split(",")]
    RESULTS.mkdir(parents=True, exist_ok=True)
    if args.mode == "run":
        for workers in levels:
            for block in args.blocks.split(","):
                run_block(workers, block, RESULTS)
    else:
        report(RESULTS, levels)


if __name__ == "__main__":
    main()
