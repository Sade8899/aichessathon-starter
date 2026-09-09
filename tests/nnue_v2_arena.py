"""Colour-balanced paired arena for a V2 candidate.

Pairing is the point. Each opening is played four times -- control as White, control as
Black, candidate as White, candidate as Black -- against the same opponent with the same
seed. Both agents therefore face identical work and the statistic that matters is the
per-opening, per-colour score *difference*, not two independent win rates. Opening
variance cancels, which is why a few hundred pairs can still be informative.

Differences from the V1 arena, each for a reason V1 exposed:

- the candidate path is a parameter, so a screening run does not require overwriting a
  packaged agent;
- Zagreus is included as a third training-visible benchmark family, and Loki stays a
  held-out opponent that was never in the training data;
- the confidence interval is a bootstrap over paired differences rather than a normal
  approximation, because paired differences are lumpy at +/-1 and +/-0.5;
- draw counts are reported per agent, because V1's draws collapsed from 15 to 3 and the
  aggregate score alone did not say so loudly enough;
- every game is written out as a PGN so a result can be inspected rather than trusted.
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import math
import multiprocessing as mp
import os
import pathlib
import random
import statistics
import sys
import time
import types
from typing import Any

import chess
import chess.pgn

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))

for _var in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
    os.environ.setdefault(_var, "1")

BIN = REPO / "tests" / "external_engines" / "bin"
MAX_PLIES = 300
ARENA_SEED = 20260912

# Loki is deliberately last and deliberately separate: it is the held-out family, absent
# from the V1 training corpus and excluded from V2 group generation in code.
FAMILIES: dict[str, tuple[str, ...]] = {
    "rustic": (
        "rustic-alpha-1.5-win64.exe",
        "rustic-alpha-2.4-win64.exe",
        "rustic-alpha-3.0.6-win64.exe",
    ),
    "shallowblue": ("shallowblue_x86-64.exe",),
    "zagreus": ("Zagreus-v5.0-Windows-x86-64.exe",),
    "loki": ("Loki3.0.0-x64.exe",),
}
HELD_OUT_FAMILY = "loki"

_loaded: dict[str, types.ModuleType] = {}


def agent_for(name: str) -> types.ModuleType:
    if name not in _loaded:
        if name == "control":
            path = REPO / "agent.py"
        else:
            path = pathlib.Path(os.environ["NNUE_V2_CANDIDATE"])
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


def play(entry: dict[str, Any]) -> dict[str, Any]:
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
        score: float | None = 0.0
        result = "0-1" if agent_white else "1-0"
    elif failure and failure.startswith(("engine_no_bestmove", "exception")):
        score = None  # infrastructure, not a chess result
        result = "*"
    else:
        result = (
            board.result(claim_draw=True)
            if board.is_game_over(claim_draw=True)
            else "1/2-1/2"
        )
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
        "opening_fen": entry["opening_fen"],
        "moves": " ".join(moves),
        "result": result,
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


def arena_openings(count: int, families: list[str], seed: int) -> list[dict[str, Any]]:
    """A held-out opening book, disjoint from everything the network saw."""
    from nnue_openings import fen_key, forbidden_fens

    manifest = json.loads(
        (REPO / "tests" / "results" / "nnue" / "game_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    trained = {fen_key(o["fen"]) for o in manifest["openings"]}
    banned = forbidden_fens() | trained
    binaries = [b for fam in families for b in FAMILIES[fam]]
    rng = random.Random(seed)

    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    attempts = 0
    while len(out) < count and attempts < count * 400:
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
        binary = binaries[len(out) % len(binaries)]
        family = next(f for f in families if binary in FAMILIES[f])
        out.append(
            {
                "pair_id": f"a{len(out):05d}",
                "opening_id": f"arena-{len(out):05d}",
                "opening_fen": board.fen(),
                "opponent": binary,
                "opponent_family": family,
            }
        )
    return out


def elo(fraction: float) -> float:
    fraction = min(max(fraction, 1e-6), 1 - 1e-6)
    return -400.0 * math.log10(1.0 / fraction - 1.0)


def bootstrap_ci(
    diffs: list[float], iterations: int, seed: int
) -> tuple[float, float, float]:
    """Percentile bootstrap over paired differences.

    Paired differences take values in {-1, -0.5, 0, +0.5, +1} with heavy mass at zero,
    which is exactly the shape a normal approximation handles worst near the boundary.
    """
    if not diffs:
        return 0.0, 0.0, 0.0
    rng = random.Random(seed)
    n = len(diffs)
    means = []
    for _ in range(iterations):
        means.append(sum(diffs[rng.randrange(n)] for _ in range(n)) / n)
    means.sort()
    lo = means[int(0.025 * iterations)]
    hi = means[min(iterations - 1, int(0.975 * iterations))]
    return statistics.fmean(diffs), lo, hi


def summarise(rows: list[dict[str, Any]], name: str) -> dict[str, Any]:
    scored = [r for r in rows if r["agent"] == name and r["score"] is not None]
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
        "wins": wins,
        "draws": draws,
        "losses": losses,
        "draw_pct": round(draws / len(scored) * 100, 2),
        "score_pct": round(fraction * 100, 2),
        "elo": round(elo(fraction), 1),
        "time_losses": sum(1 for r in scored if r["time_loss"]),
        "illegal_moves": sum(1 for r in scored if r["illegal"]),
        "infrastructure_failures": sum(
            1 for r in rows if r["agent"] == name and r["infrastructure_failure"]
        ),
        "nps_mean": round(statistics.fmean(nps), 1) if nps else None,
        "mean_depth": round(statistics.fmean(depths), 2) if depths else None,
        "move_ms_p95_mean": round(statistics.fmean(p95), 1) if p95 else None,
    }


def paired_diffs(
    rows: list[dict[str, Any]], sources: list[dict[str, Any]], predicate: Any = None
) -> list[float]:
    index: dict[tuple[Any, ...], float] = {}
    for row in rows:
        if row["score"] is not None:
            index[(row["pair_id"], row["agent"], row["agent_is_white"])] = row["score"]
    diffs: list[float] = []
    for source in sources:
        for white in (True, False):
            if predicate is not None and not predicate(source, white):
                continue
            c = index.get((source["pair_id"], "control", white))
            n = index.get((source["pair_id"], "candidate", white))
            if c is not None and n is not None:
                diffs.append(n - c)
    return diffs


def write_pgns(rows: list[dict[str, Any]], path: pathlib.Path, label: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            board = chess.Board(row["opening_fen"])
            game = chess.pgn.Game()
            game.setup(board)
            node: Any = game
            for uci in row["moves"].split():
                try:
                    move = chess.Move.from_uci(uci)
                except ValueError:
                    break
                if move not in board.legal_moves:
                    break
                node = node.add_variation(move)
                board.push(move)
            white = row["agent"] if row["agent_is_white"] else row["opponent"]
            black = row["opponent"] if row["agent_is_white"] else row["agent"]
            game.headers["Event"] = label
            game.headers["White"] = str(white)
            game.headers["Black"] = str(black)
            game.headers["Result"] = row["result"]
            game.headers["Round"] = str(row["pair_id"])
            game.headers["Opponent"] = str(row["opponent_family"])
            if row["failure"]:
                game.headers["Termination"] = str(row["failure"])
            handle.write(str(game) + "\n\n")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidate", type=pathlib.Path, default=REPO / "agent_nnue_v2.py")
    ap.add_argument("--tag", required=True)
    ap.add_argument("--pairs", type=int, default=60)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--clock-ms", type=int, default=5000)
    ap.add_argument("--increment-ms", type=int, default=500)
    ap.add_argument("--engine-movetime-ms", type=int, default=150)
    ap.add_argument("--families", default="rustic,shallowblue,zagreus,loki")
    ap.add_argument("--seed", type=int, default=ARENA_SEED)
    ap.add_argument("--bootstrap", type=int, default=20000)
    ap.add_argument("--label", default="screen")
    args = ap.parse_args()

    os.environ["NNUE_V2_CANDIDATE"] = str(args.candidate.resolve())
    families = [f.strip() for f in args.families.split(",") if f.strip()]
    sources = arena_openings(args.pairs, families, args.seed)

    jobs: list[dict[str, Any]] = []
    for source in sources:
        for agent in ("control", "candidate"):
            for white in (True, False):
                jobs.append(
                    {
                        **source,
                        "agent": agent,
                        "agent_is_white": white,
                        "clock_ms": args.clock_ms,
                        "increment_ms": args.increment_ms,
                        "engine_movetime_ms": args.engine_movetime_ms,
                    }
                )

    print(
        f"{len(sources)} openings -> {len(jobs)} games, {args.workers} workers, "
        f"{args.clock_ms}ms+{args.increment_ms}ms, families {families}",
        flush=True,
    )
    started = time.perf_counter()
    rows: list[dict[str, Any]] = []
    with mp.Pool(processes=args.workers) as pool:
        for index, row in enumerate(pool.imap_unordered(play, jobs, chunksize=1), start=1):
            rows.append(row)
            if index % 40 == 0 or index == len(jobs):
                elapsed = time.perf_counter() - started
                rate = index / max(1e-9, elapsed) * 3600
                eta = (len(jobs) - index) / max(1e-9, index / elapsed) / 60
                print(
                    f"  {index}/{len(jobs)} games  {rate:,.0f}/h  eta {eta:.1f} min",
                    flush=True,
                )

    mean, lo, hi = bootstrap_ci(paired_diffs(rows, sources), args.bootstrap, args.seed)

    by_opponent = {}
    for family in families:
        diffs = paired_diffs(rows, sources, lambda s, w, f=family: s["opponent_family"] == f)
        m, low, high = bootstrap_ci(diffs, max(2000, args.bootstrap // 4), args.seed)
        subset = [r for r in rows if r["opponent_family"] == family]
        by_opponent[family] = {
            "pairs": len(diffs),
            "mean_diff": round(m, 4),
            "ci95": [round(low, 4), round(high, 4)],
            "held_out_opponent": family == HELD_OUT_FAMILY,
            "control": summarise(subset, "control"),
            "candidate": summarise(subset, "candidate"),
        }

    by_colour = {}
    for colour, flag in (("white", True), ("black", False)):
        diffs = paired_diffs(rows, sources, lambda s, w, f=flag: w == f)
        m, low, high = bootstrap_ci(diffs, max(2000, args.bootstrap // 4), args.seed)
        subset = [r for r in rows if r["agent_is_white"] == flag]
        by_colour[colour] = {
            "pairs": len(diffs),
            "mean_diff": round(m, 4),
            "ci95": [round(low, 4), round(high, 4)],
            "control": summarise(subset, "control"),
            "candidate": summarise(subset, "candidate"),
        }

    control = summarise(rows, "control")
    candidate = summarise(rows, "candidate")
    report = {
        "tag": args.tag,
        "label": args.label,
        "candidate_path": str(args.candidate),
        "pairs": len(sources),
        "games": len(rows),
        "families": families,
        "clock_ms": args.clock_ms,
        "increment_ms": args.increment_ms,
        "engine_movetime_ms": args.engine_movetime_ms,
        "workers": args.workers,
        "seed": args.seed,
        "wall_seconds": round(time.perf_counter() - started, 1),
        "control": control,
        "candidate": candidate,
        "paired_mean_diff": round(mean, 4),
        "paired_ci95": [round(lo, 4), round(hi, 4)],
        "paired_pairs": len(paired_diffs(rows, sources)),
        "elo_diff": round(elo(0.5 + mean / 2.0), 1),
        "elo_ci95": [round(elo(0.5 + lo / 2.0), 1), round(elo(0.5 + hi / 2.0), 1)],
        "ci_excludes_zero": lo > 0.0,
        "draw_delta_pct": round(
            (candidate.get("draw_pct", 0) or 0) - (control.get("draw_pct", 0) or 0), 2
        ),
        "by_opponent": by_opponent,
        "by_colour": by_colour,
    }

    outdir = REPO / "tests" / "results" / "nnue" / "v2" / args.tag
    outdir.mkdir(parents=True, exist_ok=True)
    stem = f"arena_{args.label}"
    (outdir / f"{stem}.json").write_text(json.dumps(report, indent=2), encoding="utf-8")

    fields = [
        "pair_id", "agent", "agent_is_white", "opponent", "opponent_family",
        "opening_id", "result", "score", "failure", "plies", "nodes",
        "wall_seconds", "move_ms_mean", "move_ms_p95", "nps", "mean_depth",
        "time_loss", "illegal",
    ]
    with (outdir / f"{stem}.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    write_pgns(rows, outdir / f"{stem}.pgn", f"{args.tag}-{args.label}")

    print(json.dumps({k: v for k, v in report.items() if k != "by_opponent"}, indent=2))
    print("\nby opponent:")
    for family, info in by_opponent.items():
        flag = " (HELD OUT)" if info["held_out_opponent"] else ""
        print(
            f"  {family:<12}{flag:<12} diff {info['mean_diff']:+.4f} "
            f"CI [{info['ci95'][0]:+.4f}, {info['ci95'][1]:+.4f}]  "
            f"control {info['control'].get('score_pct')}% "
            f"candidate {info['candidate'].get('score_pct')}%"
        )


if __name__ == "__main__":
    main()
