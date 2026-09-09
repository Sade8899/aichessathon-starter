"""Paired throughput comparison between the control and the NNUE candidate.

Alternating repetitions, so drift in machine load falls on both sides equally rather
than on whichever was measured second. Cold and warm figures are reported separately:
the cold pass is the first search after import, which is the one a real first move
would pay.
"""

from __future__ import annotations

import argparse
import importlib
import json
import pathlib
import statistics
import sys
import time

import chess

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

POSITIONS = [
    chess.STARTING_FEN,
    "r1bqkb1r/pppp1ppp/2n2n2/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 4 4",
    "r4rk1/p1pq2pp/1np3p1/4P3/1P1p1BP1/1P3P2/P2Q4/2RK1B1R w - - 0 22",
    "r2q1rk1/pp2ppbp/2np1np1/8/2PNP3/2N1B3/PP2BPPP/R2Q1RK1 b - - 0 10",
    "8/2p5/3p4/KP5r/1R3p1k/8/4P1P1/8 w - - 0 1",
    "6k1/5ppp/8/8/8/8/5PPP/3R2K1 w - - 0 1",
]


def measure(module, fen: str, clock_ms: int) -> dict:
    module._engine = module.Engine()
    module._eval_table = [None] * len(module._eval_table)
    started = time.perf_counter()
    move = module.get_move(fen, clock_ms)
    elapsed = time.perf_counter() - started
    nodes = module._engine.nodes
    return {
        "move": move,
        "seconds": elapsed,
        "nodes": nodes,
        "nps": nodes / elapsed if elapsed > 0 else 0.0,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--clock-ms", type=int, default=20000)
    ap.add_argument("--reps", type=int, default=5)
    ap.add_argument("--out", type=pathlib.Path, default=None)
    args = ap.parse_args()

    control = importlib.import_module("agent")
    candidate = importlib.import_module("agent_nnue")

    cold = {
        "control": measure(control, POSITIONS[0], args.clock_ms),
        "candidate": measure(candidate, POSITIONS[0], args.clock_ms),
        "note": "first search after import; includes any cache and branch warm-up",
    }

    rows: list[dict] = []
    for rep in range(args.reps):
        for fen in POSITIONS:
            # alternate which side goes first so load drift is shared
            order = (control, candidate) if rep % 2 == 0 else (candidate, control)
            names = ("control", "candidate") if rep % 2 == 0 else ("candidate", "control")
            pair: dict = {"rep": rep, "fen": fen}
            for module, name in zip(order, names, strict=True):
                pair[name] = measure(module, fen, args.clock_ms)
            rows.append(pair)

    def aggregate(name: str) -> dict:
        nps = [r[name]["nps"] for r in rows]
        nodes = [r[name]["nodes"] for r in rows]
        return {
            "nps_mean": round(statistics.fmean(nps), 1),
            "nps_median": round(statistics.median(nps), 1),
            "nodes_total": int(sum(nodes)),
            "seconds_total": round(sum(r[name]["seconds"] for r in rows), 2),
        }

    control_agg = aggregate("control")
    candidate_agg = aggregate("candidate")
    ratio = candidate_agg["nps_mean"] / control_agg["nps_mean"]
    loss = 1.0 - ratio

    per_position = []
    for fen in POSITIONS:
        subset = [r for r in rows if r["fen"] == fen]
        c = statistics.fmean(r["control"]["nps"] for r in subset)
        n = statistics.fmean(r["candidate"]["nps"] for r in subset)
        agree = sum(r["control"]["move"] == r["candidate"]["move"] for r in subset)
        per_position.append(
            {
                "fen": fen,
                "control_nps": round(c, 1),
                "candidate_nps": round(n, 1),
                "nps_loss_pct": round((1 - n / c) * 100, 2),
                "same_move_reps": f"{agree}/{len(subset)}",
                "control_moves": sorted({r["control"]["move"] for r in subset}),
                "candidate_moves": sorted({r["candidate"]["move"] for r in subset}),
            }
        )

    report = {
        "clock_ms": args.clock_ms,
        "reps": args.reps,
        "positions": len(POSITIONS),
        "method": "alternating paired repetitions; cold pass reported separately",
        "candidate_nnue_status": candidate._nnue_status,
        "candidate_uses_fused_path": candidate.fast_evaluate is candidate.compiled_evaluate_nnue,
        "cold": cold,
        "warm_control": control_agg,
        "warm_candidate": candidate_agg,
        "nps_ratio": round(ratio, 4),
        "nps_loss_pct": round(loss * 100, 2),
        "gate_max_loss_pct": 10.0,
        "throughput_gate_passes": loss <= 0.10,
        "per_position": per_position,
    }
    text = json.dumps(report, indent=2)
    print(text)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
