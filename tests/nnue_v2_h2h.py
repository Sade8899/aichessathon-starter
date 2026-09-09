"""Head-to-head: the candidate plays the control directly, colours reversed per opening.

Why this exists alongside the paired benchmark arena. In the benchmark arena both agents
face engines rated several hundred points above them at this time control, and both score
near the floor -- measured, control 7.27% and candidate 5.00% over 240 games. When almost
every game is a loss for both sides, almost every paired difference is exactly zero, and
the comparison spends its games proving something already known (the benchmark engines are
stronger) instead of the thing being asked (which of these two is stronger).

A direct match has no floor. Every game produces a result that is *about* the difference
between the two agents, because the difference is the only thing that varies. The same
opening is played twice with colours reversed, so first-move advantage cancels exactly
rather than approximately.

This does not replace the benchmark arena -- an agent can beat its predecessor and still
be worse against the field, which is why both are reported -- but it is the higher-powered
of the two tests and it is the one that can actually resolve a small difference.
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

MAX_PLIES = 300
_loaded: dict[str, types.ModuleType] = {}


def agent_for(name: str) -> types.ModuleType:
    if name not in _loaded:
        path = (
            REPO / "agent.py"
            if name == "control"
            else pathlib.Path(os.environ["NNUE_V2_CANDIDATE"])
        )
        spec = importlib.util.spec_from_file_location(f"h2h_{name}", path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[f"h2h_{name}"] = module
        spec.loader.exec_module(module)
        _loaded[name] = module
    module = _loaded[name]
    module._engine = module.Engine()
    module._eval_table = [None] * len(module._eval_table)
    return module


def play(entry: dict[str, Any]) -> dict[str, Any]:
    white = agent_for(entry["white"])
    black = agent_for(entry["black"])
    board = chess.Board(entry["opening_fen"])
    clocks = {True: float(entry["clock_ms"]), False: float(entry["clock_ms"])}
    increment = entry["increment_ms"]

    moves: list[str] = []
    failure: str | None = None
    depths: dict[str, list[int]] = {"white": [], "black": []}
    started = time.perf_counter()
    try:
        while len(moves) < MAX_PLIES and not board.is_game_over(claim_draw=True):
            side = board.turn
            module = white if side == chess.WHITE else black
            t0 = time.perf_counter()
            uci = module.get_move(board.fen(), int(clocks[side]))
            spent = (time.perf_counter() - t0) * 1000.0
            stats = getattr(module._engine, "stats", {}) or {}
            if "depth" in stats:
                depths["white" if side else "black"].append(int(stats["depth"]))
            clocks[side] -= spent
            if clocks[side] <= 0:
                failure = "flag:" + ("white" if side else "black")
                break
            clocks[side] += increment
            try:
                move = chess.Move.from_uci(uci)
            except ValueError:
                failure = "malformed:" + ("white" if side else "black")
                break
            if move not in board.legal_moves:
                failure = "illegal:" + ("white" if side else "black")
                break
            board.push(move)
            moves.append(uci)
    except Exception as exc:
        failure = f"exception:{type(exc).__name__}"

    if failure and failure.startswith(("flag", "illegal", "malformed")):
        loser_white = failure.endswith("white")
        result = "0-1" if loser_white else "1-0"
        infrastructure = False
    elif failure:
        result = "*"
        infrastructure = True
    else:
        result = (
            board.result(claim_draw=True)
            if board.is_game_over(claim_draw=True)
            else "1/2-1/2"
        )
        infrastructure = False

    if infrastructure:
        candidate_score: float | None = None
    else:
        candidate_is_white = entry["white"] == "candidate"
        if result == "1-0":
            candidate_score = 1.0 if candidate_is_white else 0.0
        elif result == "0-1":
            candidate_score = 0.0 if candidate_is_white else 1.0
        else:
            candidate_score = 0.5

    return {
        "pair_id": entry["pair_id"],
        "opening_id": entry["opening_id"],
        "opening_fen": entry["opening_fen"],
        "white": entry["white"],
        "black": entry["black"],
        "candidate_is_white": entry["white"] == "candidate",
        "result": result,
        "candidate_score": candidate_score,
        "failure": failure,
        "infrastructure_failure": infrastructure,
        "moves": " ".join(moves),
        "plies": len(moves),
        "wall_seconds": round(time.perf_counter() - started, 2),
        "candidate_depth": round(
            statistics.fmean(depths["white" if entry["white"] == "candidate" else "black"]), 2
        )
        if depths["white" if entry["white"] == "candidate" else "black"]
        else None,
        "control_depth": round(
            statistics.fmean(depths["white" if entry["white"] == "control" else "black"]), 2
        )
        if depths["white" if entry["white"] == "control" else "black"]
        else None,
    }


def openings(count: int, seed: int) -> list[dict[str, Any]]:
    from nnue_openings import fen_key, forbidden_fens

    manifest = json.loads(
        (REPO / "tests" / "results" / "nnue" / "game_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    banned = forbidden_fens() | {fen_key(o["fen"]) for o in manifest["openings"]}
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
        out.append(
            {
                "pair_id": f"h{len(out):05d}",
                "opening_id": f"h2h-{len(out):05d}",
                "opening_fen": board.fen(),
            }
        )
    return out


def elo(fraction: float) -> float:
    fraction = min(max(fraction, 1e-6), 1 - 1e-6)
    return -400.0 * math.log10(1.0 / fraction - 1.0)


def bootstrap(values: list[float], iterations: int, seed: int) -> tuple[float, float, float]:
    if not values:
        return 0.0, 0.0, 0.0
    rng = random.Random(seed)
    n = len(values)
    means = sorted(
        sum(values[rng.randrange(n)] for _ in range(n)) / n for _ in range(iterations)
    )
    return (
        statistics.fmean(values),
        means[int(0.025 * iterations)],
        means[min(iterations - 1, int(0.975 * iterations))],
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidate", type=pathlib.Path, required=True)
    ap.add_argument("--tag", required=True)
    ap.add_argument("--pairs", type=int, default=250)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--clock-ms", type=int, default=5000)
    ap.add_argument("--increment-ms", type=int, default=500)
    ap.add_argument("--seed", type=int, default=20260913)
    ap.add_argument("--bootstrap", type=int, default=20000)
    ap.add_argument("--label", default="h2h")
    args = ap.parse_args()

    os.environ["NNUE_V2_CANDIDATE"] = str(args.candidate.resolve())
    books = openings(args.pairs, args.seed)
    jobs: list[dict[str, Any]] = []
    for book in books:
        for candidate_white in (True, False):
            jobs.append(
                {
                    **book,
                    "white": "candidate" if candidate_white else "control",
                    "black": "control" if candidate_white else "candidate",
                    "clock_ms": args.clock_ms,
                    "increment_ms": args.increment_ms,
                }
            )

    print(
        f"{len(books)} openings -> {len(jobs)} head-to-head games, "
        f"{args.workers} workers, {args.clock_ms}ms+{args.increment_ms}ms",
        flush=True,
    )
    started = time.perf_counter()
    rows: list[dict[str, Any]] = []
    with mp.Pool(processes=args.workers) as pool:
        for index, row in enumerate(pool.imap_unordered(play, jobs, chunksize=1), start=1):
            rows.append(row)
            if index % 50 == 0 or index == len(jobs):
                elapsed = time.perf_counter() - started
                eta = (len(jobs) - index) / max(1e-9, index / elapsed) / 60
                print(f"  {index}/{len(jobs)} games  eta {eta:.1f} min", flush=True)

    scored = [r for r in rows if r["candidate_score"] is not None]
    scores = [r["candidate_score"] for r in scored]
    mean, lo, hi = bootstrap(scores, args.bootstrap, args.seed)

    # Per opening, the candidate's score summed over both colours, out of 2. This is the
    # paired unit: the same opening played both ways, so colour cancels exactly.
    by_pair: dict[str, list[float]] = {}
    for row in scored:
        by_pair.setdefault(row["pair_id"], []).append(row["candidate_score"])
    paired = [sum(v) / 2.0 for v in by_pair.values() if len(v) == 2]
    pmean, plo, phi = bootstrap(paired, args.bootstrap, args.seed)

    def side(flag: bool) -> dict[str, Any]:
        subset = [r for r in scored if r["candidate_is_white"] == flag]
        if not subset:
            return {"games": 0}
        frac = statistics.fmean([r["candidate_score"] for r in subset])
        return {
            "games": len(subset),
            "score_pct": round(frac * 100, 2),
            "wins": sum(1 for r in subset if r["candidate_score"] == 1.0),
            "draws": sum(1 for r in subset if r["candidate_score"] == 0.5),
            "losses": sum(1 for r in subset if r["candidate_score"] == 0.0),
        }

    wins = sum(1 for r in scored if r["candidate_score"] == 1.0)
    draws = sum(1 for r in scored if r["candidate_score"] == 0.5)
    losses = sum(1 for r in scored if r["candidate_score"] == 0.0)
    cand_depths = [r["candidate_depth"] for r in scored if r["candidate_depth"]]
    ctl_depths = [r["control_depth"] for r in scored if r["control_depth"]]

    report = {
        "tag": args.tag,
        "label": args.label,
        "candidate_path": str(args.candidate),
        "openings": len(books),
        "games": len(rows),
        "scored_games": len(scored),
        "infrastructure_failures": sum(1 for r in rows if r["infrastructure_failure"]),
        "clock_ms": args.clock_ms,
        "increment_ms": args.increment_ms,
        "workers": args.workers,
        "seed": args.seed,
        "wall_seconds": round(time.perf_counter() - started, 1),
        "candidate_W-D-L": f"{wins}-{draws}-{losses}",
        "candidate_score_pct": round(mean * 100, 2),
        "score_ci95_pct": [round(lo * 100, 2), round(hi * 100, 2)],
        "elo": round(elo(mean), 1),
        "elo_ci95": [round(elo(lo), 1), round(elo(hi), 1)],
        # A candidate stronger than the control scores above 0.5.
        "beats_control": lo > 0.5,
        "worse_than_control": hi < 0.5,
        "paired_by_opening_mean": round(pmean, 4),
        "paired_ci95": [round(plo, 4), round(phi, 4)],
        "draw_pct": round(draws / max(1, len(scored)) * 100, 2),
        "by_colour": {"candidate_white": side(True), "candidate_black": side(False)},
        "candidate_mean_depth": round(statistics.fmean(cand_depths), 2) if cand_depths else None,
        "control_mean_depth": round(statistics.fmean(ctl_depths), 2) if ctl_depths else None,
        "flags": sum(1 for r in rows if r["failure"] and r["failure"].startswith("flag")),
        "illegal": sum(
            1 for r in rows if r["failure"] and r["failure"].startswith(("illegal", "malformed"))
        ),
    }

    outdir = REPO / "tests" / "results" / "nnue" / "v2" / args.tag
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / f"{args.label}.json").write_text(json.dumps(report, indent=2), encoding="utf-8")

    fields = [
        "pair_id", "opening_id", "white", "black", "candidate_is_white", "result",
        "candidate_score", "failure", "plies", "wall_seconds",
        "candidate_depth", "control_depth",
    ]
    with (outdir / f"{args.label}.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    with (outdir / f"{args.label}.pgn").open("w", encoding="utf-8") as handle:
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
            game.headers["Event"] = f"{args.tag}-{args.label}"
            game.headers["White"] = row["white"]
            game.headers["Black"] = row["black"]
            game.headers["Result"] = row["result"]
            game.headers["Round"] = str(row["pair_id"])
            if row["failure"]:
                game.headers["Termination"] = str(row["failure"])
            handle.write(str(game) + "\n\n")

    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
