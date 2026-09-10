"""The fifteen V3 hard gates, run against a built candidate in either mode.

The correctness gates are imported from `nnue_v2_gates` rather than rewritten -- the de
Bruijn scan check, the reference-integer equality check, colour symmetry, determinism and
legality are the same properties and a second copy could only drift from the first. What
this module adds is the V3 requirements V2 did not have: RATED_V4 as well as RATED_V5,
a candidate path that is not hardcoded, an NPS gate measured by repeated interleaved
runs rather than one, and the ORDER-mode-specific assertion that the deployed evaluation
is bit-identical to the control's.

Gate 14, playing strength, is not decided here. It cannot be: it needs an arena. This
module reports it as PENDING and the final report resolves it.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import statistics
import subprocess
import sys
import time
import types
from typing import Any

import chess
import numpy as np

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))

import nnue_v2_gates as v2g  # noqa: E402
import nnue_v2_train as trainer  # noqa: E402

OUT = REPO / "tests" / "results" / "nnue" / "v3"
DEFENCE_FIXTURES = v2g.DEFENCE_FIXTURES
REJECT_FIXTURE = v2g.REJECT_FIXTURE


def gate_reference_equality(
    candidate: types.ModuleType, tag: str, boards: list[chess.Board]
) -> list[dict[str, Any]]:
    """The agent's fused Numba path must equal the reference integer path exactly.

    The strongest single gate in the suite: it exercises the bit scan, the orientation
    flip, the folded auxiliary table, both value heads, the clamp and the gate end to
    end, and it compares integers, so "agrees to within a rounding step" cannot hide.
    """
    blob = np.load(OUT / tag / "quantized.npz")
    q = {
        "embed_q": blob["embed"],
        "aux_w_q": blob["aux_w"],
        "aux_b_q": blob["aux_b"],
        "mg_q": blob["mg"],
        "eg_q": blob["eg"],
        "conf_q": blob["conf"],
        "mg_scale": float(blob["scales"][0]),
        "eg_scale": float(blob["scales"][1]),
        "conf_scale": float(blob["scales"][2]),
        "mg_bias": float(blob["biases"][0]),
        "eg_bias": float(blob["biases"][1]),
        "conf_bias": float(blob["biases"][2]),
        "gate": bool(blob["flags"][0]),
        "phase_gate": bool(blob["flags"][1]),
        "conf_min": (
            float(blob["flags"][3]) / 1000.0 if blob["flags"].shape[0] > 3 else 0.0
        ),
    }
    enc = v2g.encode_boards(boards)
    rows = np.arange(len(boards))
    reference = trainer.quant_correction(q, enc, rows)
    agent = np.array([candidate.nnue_correction(b) for b in boards], dtype=np.float64)
    diff = np.abs(agent - reference)
    exact = int((diff == 0).sum())
    clamp = candidate.NNUE_CLAMP
    return [
        {
            "gate": "agent integer path equals the reference integer path",
            "measured": f"{exact}/{len(boards)} exact, max |diff| {diff.max():.0f} cp",
            "requirement": "every position exact",
            "passes": exact == len(boards),
        },
        {
            "gate": "correction never exceeds the clamp",
            "measured": f"max |correction| {np.abs(agent).max():.0f} cp, clamp {clamp}",
            "requirement": f"<= {clamp}",
            "passes": bool((np.abs(agent) <= clamp).all()),
        },
    ]


def gate_mode_isolation(
    control: types.ModuleType, candidate: types.ModuleType, mode: str,
    boards: list[chess.Board],
) -> list[dict[str, Any]]:
    """In ORDER mode the deployed evaluation must be the control's, exactly.

    This is the whole claim of the mode. If a single position evaluates differently the
    mode is not what it says it is, and the draw-preservation argument for it collapses.
    """
    if mode != "order":
        return []
    same = sum(
        1 for b in boards if control.compiled_evaluate(b) == candidate._uncached_evaluate(b)
    )
    corrections = [candidate.nnue_correction(b) for b in boards]
    nonzero = sum(1 for c in corrections if c != 0)
    return [
        {
            "gate": "ORDER mode leaves the deployed evaluation bit-identical",
            "measured": f"{same}/{len(boards)} identical",
            "requirement": "every position identical",
            "passes": same == len(boards),
        },
        {
            "gate": "ORDER mode network is actually live",
            "measured": f"{nonzero}/{len(boards)} positions produce a non-zero ranking score",
            "requirement": "> 0, else the hook is inert",
            "passes": nonzero > 0,
        },
    ]


def gate_nps_repeated(
    control_path: pathlib.Path, candidate_path: pathlib.Path, limit: float, repeats: int
) -> list[dict[str, Any]]:
    """Paired throughput, measured several times and interleaved.

    V2 measured this once and got 4.49% and 0.57% from two runs of the identical
    candidate, while the control's own throughput moved about 9% between runs. A single
    run cannot decide a 5% gate. Each repeat times both agents back to back on the same
    positions, so a machine-wide slowdown lands on both sides of the ratio.
    """
    import importlib.util

    def load(path: pathlib.Path, name: str) -> types.ModuleType:
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module

    boards = v2g.sample_boards(24, seed=99)
    ratios: list[float] = []
    control_nps: list[float] = []
    candidate_nps: list[float] = []
    for r in range(repeats):
        pair: dict[str, float] = {}
        for label, path in (("control", control_path), ("candidate", candidate_path)):
            module = load(path, f"nps_{label}_{r}")
            module.get_move(chess.Board().fen(), 5000)
            nodes = 0
            seconds = 0.0
            for board in boards:
                module._engine = module.Engine()
                module.get_move(board.fen(), 8000)
                nodes += int(module._engine.stats.get("nodes", 0))
                seconds += float(module._engine.stats.get("seconds", 0.0))
            pair[label] = nodes / max(1e-9, seconds)
        control_nps.append(pair["control"])
        candidate_nps.append(pair["candidate"])
        ratios.append(100.0 * (1.0 - pair["candidate"] / pair["control"]))

    loss = statistics.median(ratios)
    return [
        {
            "gate": "paired NPS loss (median of repeated interleaved runs)",
            "measured": (
                f"{loss:.2f}% median of {[round(x, 2) for x in ratios]}; "
                f"control {statistics.median(control_nps):,.0f} nps, "
                f"candidate {statistics.median(candidate_nps):,.0f} nps"
            ),
            "requirement": f"<= {limit}%",
            "passes": loss <= limit,
        }
    ]


def _rated(script: str, source: str) -> dict[str, Any]:
    proc = subprocess.run(
        [sys.executable, str(REPO / "tests" / script), "fixtures", "--source", source],
        check=True, capture_output=True, text=True, cwd=REPO,
    )
    return json.loads(proc.stdout)


def gate_fixtures(candidate_name: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """RATED_V5 and RATED_V4, control against candidate. No regression is allowed."""
    v5_control = _rated("rated_v5.py", "agent.py")
    v5_candidate = _rated("rated_v5.py", candidate_name)
    by_c = {r["id"]: r for r in v5_control["results"]}
    by_k = {r["id"]: r for r in v5_candidate["results"]}
    broke = [f for f, row in by_c.items() if row["passed"] and not by_k[f]["passed"]]
    repaired = [f for f, row in by_c.items() if not row["passed"] and by_k[f]["passed"]]
    defence_broken = [f for f in DEFENCE_FIXTURES if f in broke]
    reject_row = by_k.get(REJECT_FIXTURE)

    gates = [
        {
            "gate": "RATED_V5 enforced fixtures",
            "measured": (
                f"control {v5_control['enforced_passed']}/{v5_control['enforced_total']}, "
                f"candidate {v5_candidate['enforced_passed']}/{v5_candidate['enforced_total']}"
            ),
            "requirement": "candidate matches the control's baseline",
            "passes": v5_candidate["enforced_passed"] >= v5_control["enforced_passed"]
            and v5_candidate["passed"],
        },
        {
            "gate": "solved-control regressions (RATED_V5)",
            "measured": f"{len(broke)} {broke}",
            "requirement": "0",
            "passes": len(broke) == 0,
        },
        {
            "gate": "draw and passed-pawn defence fixtures preserved",
            "measured": f"broken: {defence_broken or 'none'}",
            "requirement": "none broken",
            "passes": len(defence_broken) == 0,
        },
        {
            "gate": f"{REJECT_FIXTURE} still rejected",
            "measured": str(reject_row["chose"] if reject_row else "missing"),
            "requirement": "not the rated move",
            "passes": bool(reject_row and reject_row["passed"]),
        },
    ]
    detail: dict[str, Any] = {
        "rated_v5_control": v5_control,
        "rated_v5_candidate": v5_candidate,
        "broke": broke,
        "repaired": repaired,
    }

    try:
        v4_control = _rated("rated_v4.py", "agent.py")
        v4_candidate = _rated("rated_v4.py", candidate_name)
        c_pass = v4_control.get("enforced_passed", v4_control.get("passed_count", 0))
        k_pass = v4_candidate.get("enforced_passed", v4_candidate.get("passed_count", 0))
        c_tot = v4_control.get("enforced_total", v4_control.get("total", 0))
        gates.append(
            {
                "gate": "RATED_V4 enforced fixtures",
                "measured": f"control {c_pass}/{c_tot}, candidate {k_pass}/{c_tot}",
                "requirement": "candidate matches the control's baseline",
                "passes": k_pass >= c_pass,
            }
        )
        detail["rated_v4_control"] = v4_control
        detail["rated_v4_candidate"] = v4_candidate
    except (subprocess.CalledProcessError, json.JSONDecodeError, KeyError) as exc:
        # Reported as a failure rather than silently dropped: an unrunnable gate is not
        # a passed gate.
        gates.append(
            {
                "gate": "RATED_V4 enforced fixtures",
                "measured": f"could not run: {type(exc).__name__}",
                "requirement": "candidate matches the control's baseline",
                "passes": False,
            }
        )
    return gates, detail


def gate_package(candidate_path: pathlib.Path, weights_path: pathlib.Path) -> list[dict[str, Any]]:
    total = candidate_path.stat().st_size + weights_path.stat().st_size
    source = candidate_path.read_text(encoding="utf-8")
    banned = [m for m in ("requests", "urllib", "subprocess", "socket") if f"import {m}" in source]
    return [
        {
            "gate": "package uncompressed size",
            "measured": (
                f"{total:,} bytes (agent {candidate_path.stat().st_size:,} + "
                f"weights {weights_path.stat().st_size:,})"
            ),
            "requirement": "< 50,000,000",
            "passes": total < 50_000_000,
        },
        {
            "gate": "no network or subprocess import in the candidate",
            "measured": str(banned or "none"),
            "requirement": "none",
            "passes": not banned,
        },
    ]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", required=True)
    ap.add_argument("--candidate", type=pathlib.Path, required=True)
    ap.add_argument("--weights", type=pathlib.Path, required=True)
    ap.add_argument("--mode", choices=("eval", "order"), default="eval")
    ap.add_argument("--nps-limit", type=float, default=5.0)
    ap.add_argument("--nps-repeats", type=int, default=3)
    ap.add_argument("--boards", type=int, default=1000)
    ap.add_argument("--skip-fixtures", action="store_true")
    args = ap.parse_args()

    started = time.perf_counter()
    control = v2g.load_module(REPO / "agent.py", "v3_gate_control")
    candidate = v2g.load_module(args.candidate, "v3_gate_candidate")
    print(f"candidate weight status: {candidate._nnue_status}")
    if not candidate._nnue_ready:
        raise SystemExit("candidate did not load its weights; pack it first")

    boards = v2g.sample_boards(args.boards)
    gates: list[dict[str, Any]] = []
    gates += v2g.gate_identities()
    gates += v2g.gate_debruijn(candidate)
    gates += gate_reference_equality(candidate, args.tag, boards)
    gates += v2g.gate_symmetry(boards)
    gates += v2g.gate_determinism(candidate, boards)
    gates += v2g.gate_legality(candidate)
    gates += gate_mode_isolation(control, candidate, args.mode, boards[:200])
    gates += gate_nps_repeated(
        REPO / "agent.py", args.candidate, args.nps_limit, args.nps_repeats
    )
    gates += gate_package(args.candidate, args.weights)

    detail: dict[str, Any] = {}
    if not args.skip_fixtures:
        fixture_gates, detail = gate_fixtures(args.candidate.name)
        gates += fixture_gates

    passed = sum(1 for g in gates if g["passes"])
    report = {
        "tag": args.tag,
        "mode": args.mode,
        "candidate": str(args.candidate),
        "gates_passed": passed,
        "gates_total": len(gates),
        "all_passed": passed == len(gates),
        "failing": [g["gate"] for g in gates if not g["passes"]],
        "seconds": round(time.perf_counter() - started, 1),
        "gates": gates,
        "fixture_detail": detail,
        "note": "gate 14, playing strength, needs an arena and is resolved in the report",
    }
    outdir = OUT / args.tag
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / f"gates_{args.mode}.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )

    width = max(len(g["gate"]) for g in gates)
    for g in gates:
        print(f"{'PASS' if g['passes'] else 'FAIL'}  {g['gate']:<{width}}  {g['measured']}")
    print(f"\n{passed}/{len(gates)} gates pass  ({report['seconds']}s)")
    if report["failing"]:
        print("FAILING: " + ", ".join(report["failing"]))


if __name__ == "__main__":
    main()
