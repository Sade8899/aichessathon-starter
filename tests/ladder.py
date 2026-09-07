"""The public-engine benchmark ladder: adapter verification and matched-condition matches.

Our side always runs through the official harness. `harness.sandbox.local` starts the
agent exactly as the platform's runner does and `harness.referee.play_match` owns the
clock, the legality check and the draw rules, so a result here is produced by the same
referee that decides a rated game. The only thing this module adds is the opponent
seat, which `tests.uci_adapter.UciAgent` fills with a public engine binary.

Nothing here is imported by `agent.py`. The binaries live untracked under
`tests/external_engines/bin`; `tests/external_engines.json` records their provenance.

Modes:

    checks      verify the adapter: handshake, legality, colour, clock, cleanup, determinism
    match       play one matched-condition match against a named engine
"""

from __future__ import annotations

import argparse
import io
import json
import math
import random
import statistics
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import chess
import chess.pgn

from harness.referee import FAILED_TERMINATIONS, play_match
from harness.rules import INIT_BUDGET_S
from harness.sandbox import Agent, local
from tests.uci_adapter import ClockBook, UciAgent

BIN = Path("tests/external_engines/bin")
RESULTS = Path("tests/results/ladder")
AGENT_DIR = Path(".")
BASE_MS = 120_000
INCREMENT_MS = 500

ENGINES = {
    "shallowblue": BIN / "shallowblue_x86-64.exe",
    "rustic3": BIN / "rustic-alpha-3.0.6-win64.exe",
    "zagreus5": BIN / "Zagreus-v5.0-Windows-x86-64.exe",
    "loki3": BIN / "Loki3.0.0-x64.exe",
}
EXPECTED_ID = {
    "shallowblue": "Shallow Blue 2.0.0",
    "rustic3": "Rustic Alpha 3.0.6",
    "zagreus5": "Zagreus v5.0",
    "loki3": "Loki 3.0.0",
}
# The opening positions the platform actually dealt us in rounds 44-56. They are curated
# competition openings rather than anything chosen to flatter either side, and using the
# real ones keeps the local ladder on the same distribution as the rated games.
OPENINGS_FILE = Path("tests/results/ladder/openings.json")


# --------------------------------------------------------------------------- openings


def rated_openings() -> list[dict[str, str]]:
    """The start position of every preserved rated PGN, in round order."""
    out: list[dict[str, str]] = []
    for path in sorted(Path("submission 0609v4/games").glob("*.pgn")):
        with path.open(encoding="utf-8") as handle:
            game = chess.pgn.read_game(handle)
        assert game is not None, str(path)
        out.append(
            {
                "round": game.headers["Round"],
                "fen": game.board().fen(),
                "name": path.stem.replace("aichessathon-round-", "r"),
            }
        )
    return sorted(out, key=lambda row: int(row["round"]))


# ---------------------------------------------------------------------------- seating


@dataclass(frozen=True)
class Seating:
    """One scheduled game: which opening, which colour we take, which repeat."""

    index: int
    opening: dict[str, str]
    agent_is_white: bool
    pair: int


def schedule(openings: list[dict[str, str]], games: int) -> list[Seating]:
    """Colour-counterbalanced pairs: every opening is played once with each colour.

    Pairing matters more than raw count at this sample size, so the schedule is built
    as pairs and the paired difference is reported alongside the raw score.
    """
    seats: list[Seating] = []
    pair = 0
    while len(seats) < games:
        opening = openings[pair % len(openings)]
        for white in (True, False):
            if len(seats) >= games:
                break
            seats.append(Seating(len(seats), opening, white, pair))
        pair += 1
    return seats


# ------------------------------------------------------------------------ adapter QA


def build(engine: str, book: ClockBook) -> UciAgent:
    path = ENGINES[engine]
    assert path.exists(), f"missing engine binary: {path}"
    return UciAgent(path, book, name=engine)


def checks(engine: str) -> dict[str, Any]:
    """Prove the adapter is a fair, legal, well-behaved opponent before it scores anything."""
    results: dict[str, Any] = {"engine": engine, "binary": str(ENGINES[engine]).replace("\\", "/")}

    # 1. handshake and identity
    book = ClockBook(BASE_MS, INCREMENT_MS)
    agent = build(engine, book)
    agent.start(INIT_BUDGET_S)
    results["identified"] = agent.identified
    results["identity_matches_expected"] = agent.identified == EXPECTED_ID[engine]

    # 2. legality and colour: the engine must answer a legal move for the side to move,
    #    from a white-to-move and a black-to-move position alike.
    legal_ok = True
    colour_ok = True
    for fen in (
        chess.STARTING_FEN,
        "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1",
    ):
        board = chess.Board(fen)
        uci = agent.move(fen, 5_000)
        move = chess.Move.from_uci(uci)
        legal_ok = legal_ok and move in board.legal_moves
        colour_ok = colour_ok and board.color_at(move.from_square) == board.turn
    results["answers_legal_moves"] = legal_ok
    results["answers_for_side_to_move"] = colour_ok

    # 3. time control: the engine must respect a small clock rather than think forever.
    board = chess.Board()
    started = time.monotonic()
    agent.move(board.fen(), 3_000)
    spent = time.monotonic() - started
    results["small_clock_seconds"] = round(spent, 3)
    results["respects_small_clock"] = spent < 3.0

    started = time.monotonic()
    agent.move(board.fen(), 20_000)
    longer = time.monotonic() - started
    results["large_clock_seconds"] = round(longer, 3)
    results["scales_with_clock"] = longer >= spent

    # 4. process cleanup
    process = agent._process
    assert process is not None
    agent.stop()
    results["process_exited"] = process.poll() is not None
    results["exit_code"] = process.poll()

    # 5. determinism at a fixed position with a fixed clock, from a cold process
    fixed = "r1bqkb1r/pppp1ppp/2n2n2/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 4 4"
    seen: list[str] = []
    for _ in range(3):
        cold = build(engine, ClockBook(BASE_MS, INCREMENT_MS))
        cold.start(INIT_BUDGET_S)
        seen.append(cold.move(fixed, 4_000))
        cold.stop()
    results["fixed_position_moves"] = seen
    results["fixed_position_legal"] = all(
        chess.Move.from_uci(m) in chess.Board(fixed).legal_moves for m in seen
    )
    results["fixed_position_deterministic"] = len(set(seen)) == 1

    mandatory = [
        "identity_matches_expected",
        "answers_legal_moves",
        "answers_for_side_to_move",
        "respects_small_clock",
        "process_exited",
        "fixed_position_legal",
    ]
    results["mandatory"] = mandatory
    results["passed"] = all(bool(results[name]) for name in mandatory)
    return results


# ------------------------------------------------------------------------------ match


def wilson(wins: float, games: int) -> tuple[float, float]:
    """A 95% Wilson interval on the score rate; honest at these sample sizes."""
    if games == 0:
        return (0.0, 1.0)
    z = 1.959963985
    p = wins / games
    denominator = 1 + z * z / games
    centre = (p + z * z / (2 * games)) / denominator
    spread = z * math.sqrt(p * (1 - p) / games + z * z / (4 * games * games)) / denominator
    return (round(centre - spread, 4), round(centre + spread, 4))


def play_one(
    seat: Seating, engine: str, base_ms: int, increment_ms: int, source: Path
) -> dict[str, Any]:
    book = ClockBook(base_ms, increment_ms)
    opponent = build(engine, book)
    ours: Agent = local(source)
    # `play_match` is annotated for the harness `Agent` class but only ever uses
    # `start`, `move`, `stop` and `stderr_tail`, which `UciAgent` implements. The cast
    # records that this is structural, not a claim that `UciAgent` subclasses `Agent`;
    # `harness/` is deliberately left untouched.
    seated = cast(Agent, opponent)
    white, black = (ours, seated) if seat.agent_is_white else (seated, ours)
    started = time.monotonic()
    outcome = play_match(
        white, black, base_ms, increment_ms, start_fen=seat.opening["fen"]
    )
    elapsed = time.monotonic() - started
    # A void game means both seats failed to start; it is scored as a half point and
    # counted in `terminations` so it can never be mistaken for a real draw.
    if outcome.result in ("draw", "void"):
        points = 0.5
    else:
        points = 1.0 if (outcome.result == "white") == seat.agent_is_white else 0.0
    board = chess.Board(seat.opening["fen"])
    plies = 0
    game = chess.pgn.read_game(io.StringIO(outcome.pgn))
    if game is not None:
        for node in game.mainline():
            board.push(node.move)
            plies += 1
    our_moves = (plies + (1 if seat.agent_is_white else 0)) // 2
    return {
        "index": seat.index,
        "pair": seat.pair,
        "opening": seat.opening["name"],
        "opening_fen": seat.opening["fen"],
        "agent_colour": "White" if seat.agent_is_white else "Black",
        "result": outcome.result,
        "termination": outcome.termination,
        "points": points,
        "plies": plies,
        "our_moves": our_moves,
        "wall_seconds": round(elapsed, 2),
        "mean_move_seconds": round(elapsed / max(plies, 1), 3),
        "agent_failed": outcome.termination in FAILED_TERMINATIONS
        and points == 0.0,
        "pgn": outcome.pgn,
    }


def match(
    engine: str, games: int, base_ms: int, increment_ms: int, seed: int, source: Path
) -> dict[str, Any]:
    random.seed(seed)
    openings = rated_openings()
    seats = schedule(openings, games)
    rows: list[dict[str, Any]] = []
    for seat in seats:
        row = play_one(seat, engine, base_ms, increment_ms, source)
        rows.append(row)
        print(
            f"g{row['index'] + 1:>3}/{games} {row['opening']:<24} "
            f"{row['agent_colour']:<5} {row['result']:<5} {row['termination']:<22} "
            f"pts={row['points']} plies={row['plies']}",
            flush=True,
        )
    return summarise(engine, games, base_ms, increment_ms, seed, source, rows)


def summarise(
    engine: str,
    games: int,
    base_ms: int,
    increment_ms: int,
    seed: int,
    source: Path,
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    points = sum(r["points"] for r in rows)
    wins = sum(1 for r in rows if r["points"] == 1.0)
    draws = sum(1 for r in rows if r["points"] == 0.5)
    losses = sum(1 for r in rows if r["points"] == 0.0)
    terminations: dict[str, int] = {}
    for r in rows:
        terminations[r["termination"]] = terminations.get(r["termination"], 0) + 1
    by_colour = {
        colour: {
            "games": sum(1 for r in rows if r["agent_colour"] == colour),
            "points": sum(r["points"] for r in rows if r["agent_colour"] == colour),
        }
        for colour in ("White", "Black")
    }
    by_opening: dict[str, dict[str, float]] = {}
    for r in rows:
        entry = by_opening.setdefault(r["opening"], {"games": 0.0, "points": 0.0})
        entry["games"] += 1
        entry["points"] += r["points"]
    pairs: dict[int, list[dict[str, Any]]] = {}
    for r in rows:
        pairs.setdefault(r["pair"], []).append(r)
    paired = [
        sum(x["points"] for x in members) - 1.0
        for members in pairs.values()
        if len(members) == 2
    ]
    lengths = [r["plies"] for r in rows]
    move_times = [r["mean_move_seconds"] for r in rows]
    failures = [r for r in rows if r["agent_failed"]]
    return {
        "engine": engine,
        "engine_binary": str(ENGINES[engine]).replace("\\", "/"),
        "our_source": str(source).replace("\\", "/"),
        "games": games,
        "base_ms": base_ms,
        "increment_ms": increment_ms,
        "seed": seed,
        "wins": wins,
        "draws": draws,
        "losses": losses,
        "points": points,
        "score": round(points / games, 4) if games else 0.0,
        "score_ci95": wilson(points, games),
        "paired_pairs": len(paired),
        "paired_mean_difference": round(statistics.fmean(paired), 4) if paired else None,
        "paired_stdev": round(statistics.stdev(paired), 4) if len(paired) > 1 else None,
        "by_colour": by_colour,
        "by_opening": by_opening,
        "terminations": terminations,
        "agent_failures": len(failures),
        "flags": terminations.get("flag", 0),
        "crashes": terminations.get("crash", 0),
        "illegal": terminations.get("illegal", 0),
        "init_failures": terminations.get("init", 0),
        "mean_plies": round(statistics.fmean(lengths), 1) if lengths else 0.0,
        "mean_move_seconds": round(statistics.fmean(move_times), 3) if move_times else 0.0,
        "games_detail": rows,
    }


# ------------------------------------------------------------------------------ main


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("checks", "match", "openings"))
    parser.add_argument("--engine", default="shallowblue", choices=sorted(ENGINES))
    parser.add_argument("--games", type=int, default=20)
    parser.add_argument("--base-ms", type=int, default=BASE_MS)
    parser.add_argument("--increment-ms", type=int, default=INCREMENT_MS)
    parser.add_argument("--seed", type=int, default=20260907)
    parser.add_argument("--source", type=Path, default=AGENT_DIR)
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    if args.mode == "checks":
        report: dict[str, Any] = checks(args.engine)
    elif args.mode == "openings":
        report = {"openings": rated_openings()}
    else:
        report = match(
            args.engine, args.games, args.base_ms, args.increment_ms, args.seed, args.source
        )

    text = json.dumps(report, indent=2)
    if args.out:
        target = Path(args.out)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        print(f"wrote {target} ({len(text)} bytes)")
    else:
        print(text)


if __name__ == "__main__":
    main()
