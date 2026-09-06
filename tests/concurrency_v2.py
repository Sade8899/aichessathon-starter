"""Concurrency calibration, version two: counterbalanced order, fixed-depth fairness.

Version one is preserved untouched in concurrency_calibration.py and
results/concurrency/. This driver fixes three methodological faults in it:

  * fairness is measured on the identical fixed-depth position corpus rather than on
    arena games, whose positions diverge under contention;
  * configuration execution order is counterbalanced, so neither configuration owns a
    fixed slot in the sequence a container runs;
  * workers start together on a shared future timestamp instead of as they are created.

Arena games remain, but only for throughput, initialization, memory and failures.
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
RESULTS = Path("tests/results/concurrency_v2")
LOGICAL = 12
GAMES_PER_WORKER = 4
EXPERIMENT = "tests/qcap_validation.json"
ARENA = [
    "python", "tests/qgen_checks.py", "arena",
    "--cases", "2", "--seed", "59000",
    "--base-ms", "10000", "--increment-ms", "100",
    "--experiment", EXPERIMENT,
]
FAIRNESS = ["python", "tests/concurrency_fairness.py", "--repeats", "3", "--experiment", EXPERIMENT]
# Declared before version two ran, and unchanged from version one.
MATERIAL_GAIN = 0.10
MAX_IMBALANCE = 0.03


def topology() -> dict[str, Any]:
    """What Docker actually exposes. Guest topology under a hypervisor is virtual."""
    probe = (
        "import json,os;"
        "cpus=sorted(os.sched_getaffinity(0));"
        "sib={};"
        "sib.update({c:open('/sys/devices/system/cpu/cpu%d/topology/thread_siblings_list'%c)"
        ".read().strip() for c in range(os.cpu_count() or 0)});"
        "core={c:open('/sys/devices/system/cpu/cpu%d/topology/core_id'%c).read().strip()"
        " for c in range(os.cpu_count() or 0)};"
        "info=open('/proc/cpuinfo').read();"
        "print(json.dumps({'affinity':cpus,'cpu_count':os.cpu_count(),"
        "'thread_siblings':sib,'core_id':core,"
        "'hypervisor_flag':'hypervisor' in info,"
        "'model':[l.split(':',1)[1].strip() for l in info.splitlines()"
        " if l.startswith('model name')][:1],"
        "'kernel':os.uname().release}))"
    )
    output = subprocess.run(
        ["docker", "run", "--rm", "--network", "none", "--memory", "2g",
         IMAGE, "python", "-c", probe],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    data = json.loads(output)
    cores: dict[str, list[int]] = {}
    for cpu, core in data["core_id"].items():
        cores.setdefault(core, []).append(int(cpu))
    data["cores_reported"] = len(cores)
    data["virtual_topology"] = bool(data["hypervisor_flag"])
    data["physical_core_isolation_claimed"] = False
    data["note"] = (
        "Guest reports 6 core ids with adjacent thread siblings, but the hypervisor "
        "flag is set and the kernel is WSL2, so guest vCPU to host logical processor "
        "placement is not controlled here. cpuset pins the guest scheduler only."
    )
    (RESULTS / "topology.json").write_text(json.dumps(data, indent=2) + "\n")
    print("TOPOLOGY " + json.dumps({k: data[k] for k in
          ("cpu_count", "cores_reported", "hypervisor_flag", "kernel", "model")}), flush=True)
    return data


def clock_offset() -> float:
    """Docker's clock minus this host's clock, so a shared start instant is honoured."""
    host = time.time()
    inside = float(
        subprocess.run(
            ["docker", "run", "--rm", "--network", "none", IMAGE, "python", "-c",
             "import time;print(time.time())"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
    )
    return inside - (host + time.time()) / 2


def layout(workers: int) -> list[dict[str, Any]]:
    """Pin one guest CPU per worker and counterbalance order within every core."""
    plan = []
    for index in range(workers):
        cpu = 2 * index if workers <= LOGICAL // 2 else index % LOGICAL
        core, sibling, replica = cpu // 2, cpu % 2, index // LOGICAL
        control_first = (core + sibling + replica) % 2 == 0
        plan.append(
            {
                "index": index,
                "cpu": cpu,
                "core": core,
                "sibling": sibling,
                "replica": replica,
                "order": "control,candidate" if control_first else "candidate,control",
                "stratum": "control_first" if control_first else "candidate_first",
            }
        )
    return plan


def sample_memory(names: list[str], peak: dict[str, float]) -> None:
    try:
        output = subprocess.run(
            ["docker", "stats", "--no-stream", "--format", "{{.Name}}\t{{.MemUsage}}", *names],
            capture_output=True, text=True, timeout=120,
        ).stdout
    except subprocess.TimeoutExpired:
        return
    for line in output.splitlines():
        if "\t" not in line:
            continue
        name, memory = line.split("\t", 1)
        amount = memory.split("/")[0].strip()
        for suffix, scale in (("GiB", 1024.0), ("MiB", 1.0), ("KiB", 1 / 1024), ("B", 1 / 1048576)):
            if amount.endswith(suffix):
                peak[name] = max(peak.get(name, 0.0), float(amount[: -len(suffix)]) * scale)
                break


def run_phase(phase: str, workers: int, offset: float) -> dict[str, Any]:
    plan = layout(workers)
    names = [f"c2-{phase}-{workers}-{item['index']}" for item in plan]
    subprocess.run(["docker", "rm", "-f", *names], capture_output=True)
    mount = f"type=bind,source={Path.cwd()},target=/workspace,readonly"
    margin = 20 + 1.5 * workers
    start_at = int(time.time() + margin + offset)
    launch_started = time.perf_counter()
    for name, item in zip(names, plan, strict=True):
        if phase == "arena":
            configs = item["order"]
            command = [*ARENA, "--configs", configs]
        else:
            command = [*FAIRNESS, "--order", item["order"], "--label", name]
        barrier = (
            f'while [ "$(date +%s)" -lt {start_at} ]; do sleep 0.2; done; exec '
            + " ".join(command)
        )
        subprocess.run(
            [
                "docker", "run", "--detach", "--name", name,
                "--network", "none", "--cpus", "1", "--memory", "2g",
                "--pids-limit", "128", "--read-only", "--tmpfs", "/tmp:rw,size=256m",
                "--cpuset-cpus", str(item["cpu"]), "--mount", mount, IMAGE,
                "sh", "-c", barrier,
            ],
            capture_output=True, check=True,
        )
    launch_seconds = time.perf_counter() - launch_started
    all_up_before_start = time.time() + offset < start_at
    while time.time() + offset < start_at:
        time.sleep(0.5)
    barrier_released = time.perf_counter()
    peak: dict[str, float] = {}
    while True:
        states = subprocess.run(
            ["docker", "inspect", "--format", "{{.State.Status}}", *names],
            capture_output=True, text=True,
        ).stdout.split()
        if "running" not in states:
            break
        sample_memory(names, peak)
        time.sleep(5)
    makespan = time.perf_counter() - barrier_released
    exits = subprocess.run(
        ["docker", "inspect", "--format", "{{.Name}} {{.State.ExitCode}}", *names],
        capture_output=True, text=True,
    ).stdout
    codes = {line.split()[0].lstrip("/"): int(line.split()[1]) for line in exits.splitlines()}
    for name in names:
        (RESULTS / f"{name}.log").write_bytes(
            subprocess.run(["docker", "logs", name], capture_output=True).stdout
        )
    subprocess.run(["docker", "rm", "-f", *names], capture_output=True)
    record = {
        "phase": phase,
        "workers": workers,
        "plan": plan,
        "launch_seconds": launch_seconds,
        "all_containers_created_before_start": all_up_before_start,
        "makespan_s": makespan,
        "exit_codes": codes,
        "nonzero_exits": sorted(n for n, code in codes.items() if code),
        "peak_container_mb": peak,
        "max_container_mb": max(peak.values()) if peak else None,
        "total_container_mb": sum(peak.values()) if peak else None,
    }
    if phase == "arena":
        record["games"] = workers * GAMES_PER_WORKER
        record["games_per_hour"] = workers * GAMES_PER_WORKER * 3600 / makespan
    (RESULTS / f"phase-{phase}-{workers}.json").write_text(json.dumps(record, indent=2) + "\n")
    print(
        "PHASE "
        + json.dumps(
            {
                k: record[k]
                for k in ("phase", "workers", "makespan_s", "nonzero_exits",
                          "max_container_mb", "total_container_mb",
                          "all_containers_created_before_start")
                if k in record
            }
            | ({"games_per_hour": record["games_per_hour"]} if phase == "arena" else {})
        ),
        flush=True,
    )
    return record


def fairness_rows(workers: int) -> list[dict[str, Any]]:
    rows = []
    for path in sorted(RESULTS.glob(f"c2-fair-{workers}-*.log")):
        for line in path.read_text(errors="ignore").splitlines():
            if line.startswith("FAIR "):
                rows.append(json.loads(line[5:]))
    return rows


def position_speed(
    rows: list[dict[str, Any]], config: str, stratum: str | None
) -> dict[int, float]:
    """Median seconds per position, which at fixed depth is a pure speed measurement."""
    chosen = [r for r in rows if r["name"] == config]
    if stratum:
        want = "control,candidate" if stratum == "control_first" else "candidate,control"
        chosen = [r for r in chosen if r["order"] == want]
    grouped: dict[int, list[float]] = {}
    for row in chosen:
        grouped.setdefault(row["position"], []).append(row["seconds"])
    return {position: statistics.median(values) for position, values in grouped.items()}


def retention(rows_l: list[dict[str, Any]], rows_6: list[dict[str, Any]], stratum: str | None):
    result = {}
    for config in ("control", "candidate"):
        now, base = position_speed(rows_l, config, stratum), position_speed(rows_6, config, stratum)
        shared = sorted(set(now) & set(base))
        # Speed retained on the same position: baseline time over current time.
        result[config] = statistics.median(base[p] / now[p] for p in shared) if shared else None
        result[config + "_positions"] = len(shared)
    if result["control"] and result["candidate"]:
        result["imbalance"] = result["candidate"] / result["control"] - 1
        result["within_limit"] = abs(result["imbalance"]) <= MAX_IMBALANCE
    else:
        result["imbalance"] = result["within_limit"] = None
    return result


def describe_arena(workers: int) -> dict[str, Any]:
    rows = []
    for path in sorted(RESULTS.glob(f"c2-arena-{workers}-*.log")):
        for line in path.read_text(errors="ignore").splitlines():
            if line.startswith('{"case":'):
                rows.append(json.loads(line))
    summary: dict[str, Any] = {"games": len(rows), "failures": sum(r["failure"] for r in rows)}
    for config in ("control", "candidate"):
        own = [r for r in rows if r["config"] == config]
        moves = [t for r in own for t in r["move_seconds"]]
        telemetry = [t for r in own for t in r["telemetry"]]
        summary[config] = {
            "games": len(own),
            "median_move_ms": statistics.median(moves) * 1000 if moves else None,
            "worst_move_ms": max(moves) * 1000 if moves else None,
            "mean_depth": statistics.mean(t["depth"] for t in telemetry) if telemetry else None,
            "median_initialization_ms": (
                statistics.median(r["initialization_ms"] for r in own) if own else None
            ),
            "max_initialization_ms": max((r["initialization_ms"] for r in own), default=None),
            "max_peak_rss_mb": max((t.get("peak_rss_mb", 0) for t in telemetry), default=None),
        }
    return summary


def report(levels: list[int]) -> None:
    phases = {p.stem: json.loads(p.read_text()) for p in RESULTS.glob("phase-*.json")}
    baseline = min(levels)
    rows_base = fairness_rows(baseline)
    per_level: dict[str, Any] = {}
    for workers in levels:
        arena = phases.get(f"phase-arena-{workers}")
        fair = phases.get(f"phase-fair-{workers}")
        if not arena or not fair:
            continue
        rows = fairness_rows(workers)
        per_level[str(workers)] = {
            "arena": {
                "makespan_s": arena["makespan_s"],
                "games": arena["games"],
                "games_per_hour": arena["games_per_hour"],
                "nonzero_exits": arena["nonzero_exits"],
                "max_container_mb": arena["max_container_mb"],
                "total_container_mb": arena["total_container_mb"],
                "synchronized_start": arena["all_containers_created_before_start"],
                **describe_arena(workers),
            },
            "fairness": {
                "makespan_s": fair["makespan_s"],
                "nonzero_exits": fair["nonzero_exits"],
                "measurements": len(rows),
                "synchronized_start": fair["all_containers_created_before_start"],
                "aggregate": retention(rows, rows_base, None),
                "control_first": retention(rows, rows_base, "control_first"),
                "candidate_first": retention(rows, rows_base, "candidate_first"),
            },
        }

    decision: dict[str, Any] = {
        "material_gain_threshold": MATERIAL_GAIN,
        "imbalance_limit": MAX_IMBALANCE,
    }
    if {"6", "12", "24"} <= set(per_level):
        best_small = max(
            per_level["6"]["arena"]["games_per_hour"], per_level["12"]["arena"]["games_per_hour"]
        )
        gain = per_level["24"]["arena"]["games_per_hour"] / best_small - 1
        fair24 = per_level["24"]["fairness"]
        strata = [fair24[key]["within_limit"] for key in
                  ("aggregate", "control_first", "candidate_first")]
        failures = sum(
            per_level[str(w)]["arena"]["failures"]
            + len(per_level[str(w)]["arena"]["nonzero_exits"])
            + len(per_level[str(w)]["fairness"]["nonzero_exits"])
            for w in (6, 12, 24)
        )
        decision |= {
            "best_of_6_and_12_games_per_hour": best_small,
            "games_per_hour_at_24": per_level["24"]["arena"]["games_per_hour"],
            "gain_over_best_small": gain,
            "material": gain >= MATERIAL_GAIN,
            "imbalance_aggregate": fair24["aggregate"]["imbalance"],
            "imbalance_control_first": fair24["control_first"]["imbalance"],
            "imbalance_candidate_first": fair24["candidate_first"]["imbalance"],
            "all_strata_within_limit": all(strata),
            "total_failures": failures,
            "approve_24_for_exploratory_screens": bool(
                gain >= MATERIAL_GAIN and all(strata) and failures == 0
            ),
        }
    output = {
        "version": 2,
        "topology": json.loads((RESULTS / "topology.json").read_text()),
        "levels": per_level,
        "decision": decision,
    }
    (RESULTS / "calibration_v2.json").write_text(json.dumps(output, indent=2) + "\n")
    print(json.dumps({"levels": per_level, "decision": decision}, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("topology", "run", "report"))
    parser.add_argument("--levels", default="6,12,24")
    args = parser.parse_args()
    levels = [int(v) for v in args.levels.split(",")]
    RESULTS.mkdir(parents=True, exist_ok=True)
    if args.mode == "topology":
        topology()
        return
    if args.mode == "report":
        report(levels)
        return
    if not (RESULTS / "topology.json").exists():
        topology()
    offset = clock_offset()
    print(f"CLOCK_OFFSET {offset:.3f}s", flush=True)
    for workers in levels:
        run_phase("fair", workers, offset)
        run_phase("arena", workers, offset)


if __name__ == "__main__":
    main()
