"""Paired arena between the control and the NNUE candidate.

Pairing is the point. Each opening is played four times: control as White, control as
Black, candidate as White, candidate as Black, all against the same opponent with the
same seed. The two agents therefore face identical work, and the statistic that matters
is the per-pair score difference rather than two independent win rates.

Confidence intervals are computed on the paired differences, which is why a modest
number of pairs can still be informative: the opening variance cancels.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import multiprocessing as mp
import os
import pathlib
import statistics
import sys
import time
import types

import chess

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))

for _var in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
    os.environ.setdefault(_var, "1")

BIN = REPO / "tests" / "external_engines" / "bin"
MAX_PLIES = 300

_loaded: dict[str, types.ModuleType] = {}


def agent_for(name: str) -> types.ModuleType:
    if name not in _loaded:
        path = REPO / ("agent.py" if name == "control" else "agent_nnue.py")
        spec = importlib.util.spec_from_file_location(f"arena_{name}", path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[f"arena_{name}"] = module
        spec.loader.exec_module(module)
        _loaded[name] = module
    module = _loaded[name]
    module._engine = module.Engine()
    module._eval_table = [None] * len(module._eval_table)
    return module


def play(entry: dict) -> dict:
    from nnue_generate import UciEngine, legal_uci

    module = agent_for(entry["agent"])
    board = chess.Board(entry["opening_fen"])
    agent_white = entry["agent_is_white"]
    clock = float(entry["clock_ms"])
    increment = entry["increment_ms"]
    movetime = entry["engine_movetime_ms"]

    moves: list[str] = []
    failure: str | None = None
    times: list[float] = []
    depths: list[int] = []
    nodes = 0
    engine = None
    started = time.perf_counter()
    try:
        engine = UciEngine(BIN / entry["opponent"])
        while len(moves) < MAX_PLIES and not board.is_game_over(claim_draw=True):
            mine = board.turn == (chess.WHITE if agent_white else chess.BLACK)
            if mine:
                before = module._engine.nodes
                t0 = time.perf_counter()
                uci = module.get_move(board.fen(), int(clock))
                spent = (time.perf_counter() - t0) * 1000.0
                times.append(spent)
                nodes += max(0, module._engine.nodes - before)
                stats = getattr(module._engine, "stats", {}) or {}
                if "depth" in stats:
                    depths.append(int(stats["depth"]))
                clock -= spent
                if clock <= 0:
                    failure = "flag"
                    break
                clock += increment
            else:
                uci = engine.bestmove(entry["opening_fen"], moves, movetime)
                if not legal_uci(uci, board):
                    uci = engine.bestmove(entry["opening_fen"], moves, movetime * 4)
                if uci is None:
                    failure = "engine_no_bestmove"
                    break
            try:
                move = chess.Move.from_uci(uci)
            except ValueError:
                failure = "malformed:" + ("agent" if mine else "engine")
                break
            if move not in board.legal_moves:
                failure = "illegal:" + ("agent" if mine else "engine")
                break
            board.push(move)
            moves.append(uci)
    except Exception as exc:  # an infrastructure fault must not score as a loss
        failure = f"exception:{type(exc).__name__}"
    finally:
        if engine is not None:
            engine.close()

    # A flag or an illegal move by the agent is a real loss and scores zero. An engine
    # or harness fault is not a chess result at all and is excluded from the statistics.
    agent_fault = failure == "flag" or bool(
        failure and failure.startswith(("illegal:agent", "malformed:agent"))
    )
    if agent_fault:
        score = 0.0
    elif failure and failure.startswith(("engine_no_bestmove", "exception")):
        score = None  # infrastructure, not a chess result
    else:
        result = board.result(claim_draw=True) if board.is_game_over(claim_draw=True) else "1/2-1/2"
        if result == "1-0":
            score = 1.0 if agent_white else 0.0
        elif result == "0-1":
            score = 0.0 if agent_white else 1.0
        else:
            score = 0.5

    times.sort()
    return {
        "pair_id": entry["pair_id"],
        "agent": entry["agent"],
        "agent_is_white": agent_white,
        "opponent": entry["opponent"],
        "opponent_family": entry["opponent_family"],
        "opening_id": entry["opening_id"],
        "score": score,
        "failure": failure,
        "infrastructure_failure": score is None,
        "plies": len(moves),
        "nodes": nodes,
        "wall_seconds": round(time.perf_counter() - started, 2),
        "move_ms_mean": round(statistics.fmean(times), 1) if times else None,
        "move_ms_p95": round(times[int(len(times) * 0.95)], 1) if times else None,
        "nps": round(nodes / max(1e-9, sum(times) / 1000.0), 1) if times else None,
        "mean_depth": round(statistics.fmean(depths), 2) if depths else None,
        "time_loss": failure == "flag",
        "illegal": bool(failure and failure.startswith(("illegal:agent", "malformed:agent"))),
    }


ARENA_SEED = 20260911


def arena_openings(manifest: dict, count: int) -> list[dict]:
    """A held-out opening book, disjoint from the training manifest by construction."""
    import random

    from nnue_openings import FAMILIES, fen_key, forbidden_fens

    trained = {fen_key(o["fen"]) for o in manifest["openings"]}
    banned = forbidden_fens() | trained
    binaries = [b for fam in FAMILIES.values() for b in fam]
    rng = random.Random(ARENA_SEED)

    out: list[dict] = []
    seen: set[str] = set()
    attempts = 0
    while len(out) < count and attempts < count * 200:
        attempts += 1
        board = chess.Board()
        for _ in range(rng.choice((6, 8, 10, 12))):
            legal = list(board.legal_moves)
            if not legal:
                break
            board.push(rng.choice(legal))
        if board.is_game_over() or board.is_check():
            continue
        key = fen_key(board.fen())
        if key in banned or key in seen:
            continue
        seen.add(key)
        opponent = binaries[len(out) % len(binaries)]
        family = next(f for f, bs in FAMILIES.items() if opponent in bs)
        out.append(
            {
                "pair_id": f"a{len(out):05d}",
                "opening_id": f"arena-{key[:1]}{len(out):05d}",
                "opening_fen": board.fen(),
                "opponent": opponent,
                "opponent_family": family,
            }
        )
    return out


def elo(score_fraction: float) -> float:
    score_fraction = min(max(score_fraction, 1e-6), 1 - 1e-6)
    return -400.0 * math.log10(1.0 / score_fraction - 1.0)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pairs", type=int, default=200)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--clock-ms", type=int, default=5000)
    ap.add_argument("--increment-ms", type=int, default=500)
    ap.add_argument("--engine-movetime-ms", type=int, default=150)
    ap.add_argument(
        "--offset", type=int, default=1400, help="manifest offset, clear of training games"
    )
    ap.add_argument(
        "--out",
        type=pathlib.Path,
        default=REPO / "tests" / "results" / "nnue" / "gates" / "arena.json",
    )
    args = ap.parse_args()

    manifest = json.loads(
        (REPO / "tests" / "results" / "nnue" / "game_manifest.json").read_text(encoding="utf-8")
    )
    # Arena openings must be disjoint from every opening the network trained on, so they
    # are drawn from a separate seeded book and any collision with the training book is
    # skipped. Reusing training openings here would flatter the candidate.
    sources = arena_openings(manifest, args.pairs)
    jobs: list[dict] = []
    for source in sources:
        for agent in ("control", "candidate"):
            for white in (True, False):
                jobs.append(
                    {
                        "pair_id": source["pair_id"],
                        "agent": agent,
                        "agent_is_white": white,
                        "opponent": source["opponent"],
                        "opponent_family": source["opponent_family"],
                        "opening_id": source["opening_id"],
                        "opening_fen": source["opening_fen"],
                        "clock_ms": args.clock_ms,
                        "increment_ms": args.increment_ms,
                        "engine_movetime_ms": args.engine_movetime_ms,
                    }
                )

    print(f"{len(sources)} openings -> {len(jobs)} games at {args.workers} workers", flush=True)
    started = time.perf_counter()
    rows: list[dict] = []
    with mp.Pool(processes=args.workers) as pool:
        for index, row in enumerate(pool.imap_unordered(play, jobs, chunksize=1), start=1):
            rows.append(row)
            if index % 40 == 0 or index == len(jobs):
                rate = index / max(1e-9, time.perf_counter() - started) * 3600
                print(f"  {index}/{len(jobs)} games  {rate:,.0f}/h", flush=True)

    def summarise(name: str, subset: list[dict]) -> dict:
        scored = [r for r in subset if r["agent"] == name and r["score"] is not None]
        if not scored:
            return {"games": 0}
        wins = sum(1 for r in scored if r["score"] == 1.0)
        draws = sum(1 for r in scored if r["score"] == 0.5)
        losses = sum(1 for r in scored if r["score"] == 0.0)
        fraction = sum(r["score"] for r in scored) / len(scored)
        nps = [r["nps"] for r in scored if r["nps"]]
        depths = [r["mean_depth"] for r in scored if r["mean_depth"]]
        p95 = [r["move_ms_p95"] for r in scored if r["move_ms_p95"]]
        return {
            "games": len(scored),
            "W-D-L": f"{wins}-{draws}-{losses}",
            "score_pct": round(fraction * 100, 2),
            "elo": round(elo(fraction), 1),
            "time_losses": sum(1 for r in scored if r["time_loss"]),
            "illegal_moves": sum(1 for r in scored if r["illegal"]),
            "nps_mean": round(statistics.fmean(nps), 1) if nps else None,
            "mean_depth": round(statistics.fmean(depths), 2) if depths else None,
            "move_ms_p95_mean": round(statistics.fmean(p95), 1) if p95 else None,
        }

    # paired differences: candidate score minus control score, per opening and colour
    index: dict[tuple, float] = {}
    for row in rows:
        if row["score"] is not None:
            index[(row["pair_id"], row["agent"], row["agent_is_white"])] = row["score"]
    diffs: list[float] = []
    for source in sources:
        for white in (True, False):
            c = index.get((source["pair_id"], "control", white))
            n = index.get((source["pair_id"], "candidate", white))
            if c is not None and n is not None:
                diffs.append(n - c)

    mean_diff = statistics.fmean(diffs) if diffs else 0.0
    stdev = statistics.stdev(diffs) if len(diffs) > 1 else 0.0
    stderr = stdev / math.sqrt(len(diffs)) if diffs else 0.0
    lo, hi = mean_diff - 1.96 * stderr, mean_diff + 1.96 * stderr

    by_opponent = {}
    for family in sorted({r["opponent_family"] for r in rows}):
        subset = [r for r in rows if r["opponent_family"] == family]
        by_opponent[family] = {
            "control": summarise("control", subset),
            "candidate": summarise("candidate", subset),
        }
    by_colour = {}
    for white in (True, False):
        subset = [r for r in rows if r["agent_is_white"] == white]
        by_colour["white" if white else "black"] = {
            "control": summarise("control", subset),
            "candidate": summarise("candidate", subset),
        }

    report = {
        "openings": len(sources),
        "paired_comparisons": len(diffs),
        "games_played": len(rows),
        "infrastructure_failures": sum(1 for r in rows if r["infrastructure_failure"]),
        "clock_ms": args.clock_ms,
        "increment_ms": args.increment_ms,
        "engine_movetime_ms": args.engine_movetime_ms,
        "elapsed_seconds": round(time.perf_counter() - started, 1),
        "control": summarise("control", rows),
        "candidate": summarise("candidate", rows),
        "paired_difference": {
            "mean_score_diff": round(mean_diff, 4),
            "stdev": round(stdev, 4),
            "stderr": round(stderr, 4),
            "ci95_low": round(lo, 4),
            "ci95_high": round(hi, 4),
            "ci_excludes_zero": lo > 0 or hi < 0,
            "direction": "candidate better" if mean_diff > 0 else "control better",
            "elo_estimate": round(elo(0.5 + mean_diff / 2), 1),
            "elo_ci95": [round(elo(0.5 + lo / 2), 1), round(elo(0.5 + hi / 2), 1)],
        },
        "by_opponent_family": by_opponent,
        "by_colour": by_colour,
        "games": rows,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print()
    print(f"control   {report['control']}")
    print(f"candidate {report['candidate']}")
    pd = report["paired_difference"]
    print(
        f"paired diff {pd['mean_score_diff']:+.4f}  "
        f"95% CI [{pd['ci95_low']:+.4f}, {pd['ci95_high']:+.4f}]  "
        f"excludes zero: {pd['ci_excludes_zero']}"
    )
    print(f"Elo {pd['elo_estimate']:+.1f}  CI {pd['elo_ci95']}")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
