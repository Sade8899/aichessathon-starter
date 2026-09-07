"""Rated rounds 44-53: validation, per-move diagnosis, and the regression fixtures.

The engine that played these games is the frozen submitted source `e5f63625...`. The
working `agent.py` is `65ec40ce...`, which is that source plus the accepted quiescence
delta-pruning change; it did not play a single one of these games and no rated result
here is attributed to it. Both are loaded from their bytes under private module names,
so this never imports or mutates the repository's `agent.py`.

Modes:

    validate      header, legality, clock and CSV agreement for all ten PGNs
    scan          cheap depth ladder over every Sassori move, to locate the swings
    deep          the full per-move record for one nominated ply, both engines
    replay        re-play each game's own side under the clock it actually had
    ladder        one position under a range of clocks, policy untouched
    critical      every candidate fixture at every depth, under each nominated source
    clocks        the clock multiple at which each rated error stops being played
    mate          rising full-window searches on one FEN, reporting forced mates
    king-safety   probe: can a static king-danger proxy separate the losing king move
    passed-pawn   probe: would a stronger advanced-passer term change any decision
    fixtures      run `rated_v4_positions.json` against a nominated source
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import sys
import time
import types
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import chess
import chess.pgn

GAMES = Path("submission 0609v4/games")
CSV_PATH = Path("submission 0609v4/aichessathon-games.csv")
RESULTS = Path("tests/results/rated_v4")
POSITIONS = Path("tests/rated_v4_positions.json")
RATED_SHA = "e5f63625a30f23ef7f1d625fbb5830f2bdbbed1b5480142a6e31b83cf731325b"
WORKING_SHA = "65ec40ceb29a8f6fe14a74ab2ed6ca446164f701e46d2b9d3fe8d5af94655bda"
RATED_PATH = Path("tests/submitted") / RATED_SHA / "agent.py"
WORKING_PATH = Path("agent.py")
SASSORI = "Sassori"
INCREMENT_S = 0.5
BASE_S = 120.0
ROUNDS = tuple(range(44, 54))
REQUIRED_HEADERS = (
    "Event",
    "Site",
    "Date",
    "Round",
    "White",
    "Black",
    "Result",
    "FEN",
    "SetUp",
    "Termination",
)
# Only these have to hold for every game. `final_is_checkmate` and `final_is_threefold`
# are recorded for all ten but are true of different games, so the condition actually
# asserted is `termination_condition_holds`, which picks the one the header claims.
ASSERTIONS = (
    "headers_present",
    "start_fen_legal",
    "setup_flag",
    "not_standard_start",
    "all_moves_legal",
    "under_600_plies",
    "colour_matches_csv",
    "colour_matches_expected",
    "outcome_matches_csv",
    "outcome_matches_expected",
    "termination_matches_csv",
    "termination_matches_expected",
    "termination_condition_holds",
    "mated_side_matches_result",
    "moves_matches_csv",
    "clocks_present",
    "clock_never_negative",
    "no_move_exceeded_budget",
    "slowest_matches_csv",
    "clock_left_matches_csv",
    "time_used_matches_csv",
    "round_header_matches_filename",
    "round_in_range",
)
EXPECTED = {
    44: ("White", "Loss", "checkmate"),
    45: ("Black", "Loss", "checkmate"),
    46: ("White", "Loss", "checkmate"),
    47: ("Black", "Win", "checkmate"),
    48: ("White", "Win", "checkmate"),
    49: ("Black", "Win", "checkmate"),
    50: ("Black", "Win", "checkmate"),
    51: ("White", "Draw", "threefold_repetition"),
    52: ("White", "Win", "checkmate"),
    53: ("Black", "Loss", "checkmate"),
}


# --------------------------------------------------------------------------- loading


def load(name: str, text: str) -> types.ModuleType:
    """Import one source under a private module name, the way `selection.load` does."""
    spec = importlib.util.spec_from_loader(name, loader=None)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    exec(compile(text, name, "exec"), module.__dict__)
    return module


def frozen(path: Path, sha: str) -> str:
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    assert digest == sha, (str(path), digest, sha)
    return raw.decode()


def rated() -> types.ModuleType:
    return load("rated_v4_rated", frozen(RATED_PATH, RATED_SHA))


def working() -> types.ModuleType:
    return load("rated_v4_working", frozen(WORKING_PATH, WORKING_SHA))


def source(role: str) -> types.ModuleType:
    if role == "rated":
        return rated()
    if role == "working":
        return working()
    return load("rated_v4_" + hashlib.sha256(role.encode()).hexdigest()[:8], Path(role).read_text())


# ------------------------------------------------------------------------ PGN replay


@dataclass(frozen=True)
class Ply:
    """One half-move of a rated game with the clock the platform recorded for it."""

    index: int
    fullmove: int
    colour: bool
    san: str
    uci: str
    fen_before: str
    clock_after_s: float | None


@dataclass(frozen=True)
class Game:
    round: int
    path: Path
    headers: dict[str, str]
    start_fen: str
    sassori: bool
    result: str
    termination: str
    plies: tuple[Ply, ...]

    def ours(self) -> list[Ply]:
        return [p for p in self.plies if p.colour == self.sassori]

    def board_at(self, index: int) -> chess.Board:
        board = chess.Board(self.start_fen)
        for ply in self.plies[:index]:
            board.push_uci(ply.uci)
        return board

    def label(self, ply: Ply) -> str:
        return f"{ply.fullmove}{'.' if ply.colour else '...'}{ply.san}"


def read(path: Path) -> Game:
    with path.open(encoding="utf-8") as handle:
        game = chess.pgn.read_game(handle)
    assert game is not None, str(path)
    headers = dict(game.headers)
    board = game.board()
    start = board.fen()
    plies: list[Ply] = []
    node: chess.pgn.GameNode = game
    index = 0
    while node.variations:
        child = node.variations[0]
        assert isinstance(child, chess.pgn.ChildNode)
        seconds = child.clock()
        plies.append(
            Ply(
                index=index,
                fullmove=board.fullmove_number,
                colour=board.turn,
                san=board.san(child.move),
                uci=child.move.uci(),
                fen_before=board.fen(),
                clock_after_s=None if seconds is None else round(seconds, 3),
            )
        )
        board.push(child.move)
        node = child
        index += 1
    return Game(
        round=int(headers["Round"]),
        path=path,
        headers=headers,
        start_fen=start,
        sassori=headers["White"] == SASSORI,
        result=headers["Result"],
        termination=headers.get("Termination", ""),
        plies=tuple(plies),
    )


def games() -> list[Game]:
    return sorted((read(p) for p in GAMES.glob("*.pgn")), key=lambda g: g.round)


def clocks(game: Game) -> list[dict[str, Any]]:
    """Clock before, clock after and seconds spent for every Sassori move.

    The platform stamps the clock remaining *after* the move with the increment already
    added, so the previous own move supplies the clock the engine actually saw. The
    first Sassori move of a game has no predecessor and starts from the 120 s base.
    """
    rows = []
    previous = BASE_S
    for ply in game.ours():
        after = ply.clock_after_s
        spent = None if after is None else round(previous - after + INCREMENT_S, 3)
        rows.append(
            {
                "index": ply.index,
                "move": game.label(ply),
                "uci": ply.uci,
                "fen_before": ply.fen_before,
                "clock_before_s": round(previous, 3),
                "clock_after_s": after,
                "seconds_used": spent,
            }
        )
        if after is not None:
            previous = after
    return rows


def csv_rows() -> dict[int, dict[str, str]]:
    with CSV_PATH.open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    return {int(row["round"].split()[-1]): row for row in rows}


# --------------------------------------------------------------------------- phase 1


def validate() -> dict[str, Any]:
    table = csv_rows()
    report: list[dict[str, Any]] = []
    for game in games():
        row = table[game.round]
        checks: dict[str, Any] = {}
        checks["missing_headers"] = sorted(set(REQUIRED_HEADERS) - set(game.headers))
        checks["headers_present"] = not checks["missing_headers"]
        board = chess.Board(game.start_fen)
        status = board.status()
        checks["start_fen"] = game.start_fen
        checks["start_fen_legal"] = status == chess.STATUS_VALID
        checks["setup_flag"] = game.headers.get("SetUp") == "1"
        checks["not_standard_start"] = game.start_fen != chess.STARTING_FEN

        replay = chess.Board(game.start_fen)
        illegal: list[str] = []
        for ply in game.plies:
            move = chess.Move.from_uci(ply.uci)
            if move not in replay.legal_moves:
                illegal.append(game.label(ply))
                break
            replay.push(move)
        checks["all_moves_legal"] = not illegal
        checks["first_illegal"] = illegal[0] if illegal else None
        checks["plies"] = len(game.plies)
        checks["under_600_plies"] = len(game.plies) < 600

        colour = "White" if game.sassori else "Black"
        checks["sassori_colour"] = colour
        checks["colour_matches_csv"] = colour == row["colour"]
        checks["colour_matches_expected"] = colour == EXPECTED[game.round][0]
        outcome = (
            "Draw"
            if game.result == "1/2-1/2"
            else ("Win" if (game.result == "1-0") == game.sassori else "Loss")
        )
        checks["outcome"] = outcome
        checks["outcome_matches_csv"] = outcome == row["result"]
        checks["outcome_matches_expected"] = outcome == EXPECTED[game.round][1]
        checks["termination"] = game.termination
        checks["termination_matches_csv"] = (
            game.termination.replace("_", " ").lower() == row["termination"].lower()
        )
        checks["termination_matches_expected"] = game.termination == EXPECTED[game.round][2]

        checks["final_fen"] = replay.fen()
        checks["final_is_checkmate"] = replay.is_checkmate()
        repetition = replay.is_repetition(3)
        checks["final_is_threefold"] = repetition
        if game.termination == "checkmate":
            checks["termination_condition_holds"] = replay.is_checkmate()
            checks["mated_side_is_sassori"] = replay.turn == game.sassori
            checks["mated_side_matches_result"] = (replay.turn == game.sassori) == (
                outcome == "Loss"
            )
        else:
            checks["termination_condition_holds"] = repetition
            checks["mated_side_is_sassori"] = None
            checks["mated_side_matches_result"] = True

        rows = clocks(game)
        spent = [r["seconds_used"] for r in rows if r["seconds_used"] is not None]
        checks["moves"] = len(rows)
        checks["moves_matches_csv"] = len(rows) == int(row["moves"])
        checks["clocks_present"] = len(spent) == len(rows)
        checks["clock_never_negative"] = all(
            r["clock_after_s"] is not None and r["clock_after_s"] >= 0 for r in rows
        )
        checks["no_move_exceeded_budget"] = max(spent) <= 3.75
        checks["slowest_s"] = round(max(spent), 3)
        checks["slowest_matches_csv"] = abs(max(spent) - float(row["slowest_s"])) <= 0.05
        checks["clock_left_s"] = rows[-1]["clock_after_s"]
        checks["clock_left_matches_csv"] = (
            abs(float(rows[-1]["clock_after_s"] or 0.0) - float(row["clock_left_s"])) <= 0.05
        )
        checks["time_used_s"] = round(sum(spent), 3)
        checks["time_used_matches_csv"] = abs(sum(spent) - float(row["time_used_s"])) <= 0.6
        checks["round_header_matches_filename"] = f"round-{game.round}-" in game.path.name
        checks["round_in_range"] = game.round in ROUNDS
        checks["sha256"] = hashlib.sha256(game.path.read_bytes()).hexdigest()
        checks["file"] = str(game.path).replace("\\", "/")
        report.append({"round": game.round, "opponent": row["opponent"], **checks})

    failures = [
        [entry["round"], name] for entry in report for name in ASSERTIONS if not entry[name]
    ]
    return {
        "rounds": [entry["round"] for entry in report],
        "failures": failures,
        "passed": not failures,
        "totals": {
            "wins": sum(e["outcome"] == "Win" for e in report),
            "draws": sum(e["outcome"] == "Draw" for e in report),
            "losses": sum(e["outcome"] == "Loss" for e in report),
        },
        "csv_is_cumulative_rounds": sorted(csv_rows()),
        "games": report,
    }


# ------------------------------------------------------------------------- searching


def prepare(module: types.ModuleType, board: chess.Board) -> Any:
    """A fresh engine positioned on `board`, with the pattern `choose` would recognise."""
    engine = module.Engine()
    engine.stats = {"model_seconds": 0.0, "depth": 0, "nodes": 0}
    engine.deadline = time.perf_counter() + 3600.0
    engine.enter(board)
    engine.pattern = module.recognise(board, module.evaluate(board))
    return engine


def instrument(engine: Any) -> dict[str, int]:
    """Count quiescence entries so main and quiescence nodes can be reported apart."""
    counter = {"quiescence": 0}
    original = engine.quiesce

    def counted(board: chess.Board, alpha: int, beta: int, ply: int) -> int:
        counter["quiescence"] += 1
        return int(original(board, alpha, beta, ply))

    engine.quiesce = counted
    return counter


def principal(
    module: types.ModuleType, engine: Any, board: chess.Board, limit: int, first: chess.Move | None
) -> list[str]:
    """Walk the transposition table from `first` for as far as it still agrees.

    The root loop searches children, so no table entry is ever written for the root
    itself. The caller supplies the root move it settled on and the walk continues
    from the position after it.
    """
    line: list[str] = []
    replay = board.copy()
    if first is not None:
        line.append(replay.san(first))
        replay.push(first)
    seen: set[Any] = set()
    while len(line) < limit:
        key = module.position_key(replay)
        if key in seen:
            break
        seen.add(key)
        entry = engine.table[hash(key) % module.TT_SIZE]
        if entry is None or entry.key != key or entry.move not in replay.legal_moves:
            break
        line.append(replay.san(entry.move))
        replay.push(entry.move)
    return line


def root(module: types.ModuleType, board: chess.Board, depth: int) -> dict[str, Any]:
    """Best move and score at a fixed depth, using the engine's own root ordering."""
    work = board.copy()
    engine = prepare(module, work)
    counter = instrument(engine)
    started = time.perf_counter()
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
        "depth": depth,
        "best": best_move.uci(),
        "best_san": board.san(best_move),
        "score": best_score,
        "nodes": engine.nodes,
        "quiescence_nodes": counter["quiescence"],
        "main_nodes": engine.nodes - counter["quiescence"],
        "seconds": round(time.perf_counter() - started, 3),
        "pv": principal(module, engine, board, 12, best_move),
        "root_scores": scores,
    }


def move_score(
    module: types.ModuleType, board: chess.Board, move: chess.Move, depth: int
) -> dict[str, Any]:
    """The exact full-window value of one nominated move at a fixed depth."""
    work = board.copy()
    engine = prepare(module, work)
    counter = instrument(engine)
    work.push(move)
    key = engine.enter(work)
    try:
        value = -engine.search(work, depth - 1, -module.INF, module.INF, 1)
    finally:
        engine.leave(key)
        work.pop()
    return {
        "depth": depth,
        "score": value,
        "nodes": engine.nodes,
        "quiescence_nodes": counter["quiescence"],
        "main_nodes": engine.nodes - counter["quiescence"],
        "pv": principal(module, engine, board, 12, move),
    }


def think(module: types.ModuleType, fen: str, clock_ms: int) -> dict[str, Any]:
    """One move under the engine's real time policy, from a cold engine."""
    engine = module.Engine()
    module.__dict__["_engine"] = engine
    started = time.perf_counter()
    move = module.get_move(fen, clock_ms)
    return {
        "clock_ms": clock_ms,
        "move": move,
        "san": chess.Board(fen).san(chess.Move.from_uci(move)),
        "completed_depth": int(engine.stats["depth"]),
        "seconds": round(time.perf_counter() - started, 3),
        "nodes": int(engine.stats["nodes"]),
    }


# --------------------------------------------------------------------------- phase 2


def ladder(
    module: types.ModuleType, board: chess.Board, played: chess.Move, depths: tuple[int, ...]
) -> dict[str, Any]:
    """Best move and the played move's own value, side by side, at each fixed depth."""
    rows: dict[str, Any] = {}
    correcting = None
    for depth in depths:
        best = root(module, board, depth)
        actual = best if best["best"] == played.uci() else move_score(module, board, played, depth)
        loss = best["score"] - actual["score"]
        rows[str(depth)] = {
            "best": best["best"],
            "best_san": best["best_san"],
            "best_score": best["score"],
            "played_score": actual["score"],
            "loss_cp": loss,
            "agrees": best["best"] == played.uci(),
            "nodes": best["nodes"],
            "main_nodes": best["main_nodes"],
            "quiescence_nodes": best["quiescence_nodes"],
            "seconds": best["seconds"],
            "pv": best["pv"],
        }
        if correcting is None and best["best"] != played.uci() and loss >= 100:
            correcting = depth
    return {"depths": rows, "correcting_depth": correcting}


def scan(role: str, rounds: list[int], depths: tuple[int, ...]) -> dict[str, Any]:
    module = source(role)
    out: dict[str, Any] = {"source": role, "depths": list(depths), "rounds": {}}
    for game in games():
        if game.round not in rounds:
            continue
        timings = {row["index"]: row for row in clocks(game)}
        entries = []
        for ply in game.ours():
            board = chess.Board(ply.fen_before)
            played = chess.Move.from_uci(ply.uci)
            result = ladder(module, board, played, depths)
            row = timings[ply.index]
            entries.append(
                {
                    "index": ply.index,
                    "move": game.label(ply),
                    "uci": ply.uci,
                    "fen_before": ply.fen_before,
                    "clock_before_s": row["clock_before_s"],
                    "clock_after_s": row["clock_after_s"],
                    "seconds_used": row["seconds_used"],
                    **result,
                }
            )
            print(
                f"r{game.round} {game.label(ply):>12}  "
                + " ".join(
                    f"d{d}:{result['depths'][str(d)]['loss_cp']:+d}"
                    for d in depths
                    if str(d) in result["depths"]
                ),
                file=sys.stderr,
                flush=True,
            )
        out["rounds"][str(game.round)] = {
            "outcome": EXPECTED[game.round][1],
            "colour": EXPECTED[game.round][0],
            "moves": entries,
        }
    return out


def replay(role: str, rounds: list[int]) -> dict[str, Any]:
    """Re-play each game's own side under the clock it actually had.

    The engine keeps its state between moves in a real game, so the replay feeds one
    module-level engine the same sequence of positions and clocks the platform did.
    Completed depth is hardware-dependent: this machine is not the platform's core, so
    a depth here is evidence about the policy, not a claim about what the platform hit.
    """
    module = source(role)
    out: dict[str, Any] = {"source": role, "rounds": {}}
    for game in games():
        if game.round not in rounds:
            continue
        module.__dict__["_engine"] = module.Engine()
        entries = []
        for row in clocks(game):
            board = chess.Board(row["fen_before"])
            started = time.perf_counter()
            chosen = module.get_move(row["fen_before"], int(row["clock_before_s"] * 1000))
            stats = dict(module.__dict__["_engine"].stats)
            entries.append(
                {
                    "move": row["move"],
                    "rated_uci": row["uci"],
                    "replay_uci": chosen,
                    "replay_san": board.san(chess.Move.from_uci(chosen)),
                    "same": chosen == row["uci"],
                    "completed_depth": int(stats["depth"]),
                    "rated_seconds": row["seconds_used"],
                    "replay_seconds": round(time.perf_counter() - started, 3),
                    "nodes": int(stats["nodes"]),
                    "clock_before_s": row["clock_before_s"],
                }
            )
            print(
                f"r{game.round} {row['move']:>12} rated={row['uci']} replay={chosen} "
                f"depth={stats['depth']} {entries[-1]['replay_seconds']}s",
                file=sys.stderr,
                flush=True,
            )
        agree = sum(e["same"] for e in entries)
        out["rounds"][str(game.round)] = {
            "moves": len(entries),
            "agreed": agree,
            "agreement": round(agree / len(entries), 3),
            "depths": sorted({e["completed_depth"] for e in entries}),
            "entries": entries,
        }
    return out


def think_ladder(role: str, fen: str, clocks_ms: tuple[int, ...]) -> dict[str, Any]:
    """The same position under a range of clocks, each from a cold engine.

    The budget is `available / 32`, so a larger clock is the only way the existing
    policy can buy depth; nothing here changes the policy itself.
    """
    module = source(role)
    return {
        "source": role,
        "fen": fen,
        "rungs": [think(module, fen, clock) for clock in clocks_ms],
    }


CRITICAL: tuple[tuple[str, int, str, str | None, tuple[int, ...]], ...] = (
    ("r44-14-b4", 44, "r1bqr1k1/ppp3pp/5b2/n2npp2/2B5/2PP1N2/PPQN1PPP/R1B1R1K1 w - - 4 14",
     "b2b4", (2, 3, 4, 5)),
    ("r44-21-Bf4", 44, "r1b3k1/ppp3pp/8/5q2/1PN5/1Q6/P4PPP/b1B1N1K1 w - - 0 21",
     "c1f4", (2, 3, 4, 5)),
    ("r45-54-Rc7", 45, "1k6/6R1/8/p5P1/8/2r5/5PK1/8 b - - 0 54", "c3c7", (2, 3, 4, 5, 6, 7)),
    ("r45-55-a4", 45, "1k6/2R5/8/p5P1/8/8/5PK1/8 b - - 0 55", "a5a4", (2, 3, 4, 5, 6, 7)),
    ("r46-29-a5", 46, "r5k1/5rq1/1pp1p2p/3bPp2/PP2pP2/K1Q3P1/4P1B1/3R3R w - - 1 29",
     "a4a5", (2, 3, 4, 5)),
    ("r46-30-Ka4", 46, "r5k1/5rq1/2p1p2p/p2bPp2/1P2pP2/K1Q3P1/4P1B1/3R3R w - - 0 30",
     "a3a4", (2, 3, 4, 5, 6)),
    ("r51-53-Qc5", 51, "5nk1/2R5/3P1p1r/P2q2p1/6P1/Q6P/7K/2R5 w - - 18 53", "a3c5", (2, 3, 4, 5)),
    ("r51-62-Ke3", 51, "2R2n1k/8/Q2P1p2/P5p1/6P1/8/3K1r2/2R3q1 w - - 8 62",
     "d2e3", (2, 3, 4, 5, 6)),
    ("r51-66-Qc4", 51, "2R2n1k/8/Q2P1p2/P1K3p1/6P1/8/5r2/2q5 w - - 0 66", "a6c4", (2, 3, 4, 5)),
    ("r51-79-repetition", 51, "7r/PK1P1k2/5p2/2n3p1/6P1/8/8/8 w - - 11 80", None, (4, 5, 6)),
    ("r53-35-Rf7", 53, "1rb5/2p1r1k1/P2P1n2/2N3pp/1p2P3/3B2BP/6P1/1R4K1 b - - 0 35",
     "e7f7", (2, 3, 4, 5, 6)),
    ("r53-52-Bc6", 53, "2r5/rBPb4/P7/6p1/7p/4B2P/5RP1/3k2K1 b - - 9 52",
     "d7c6", (2, 3, 4, 5, 6)),
    ("r47-63-e2", 47, "7r/7r/1pb1k3/5p2/p5P1/P2pp3/RP1P2Rp/2B4K b - - 0 63", "e3e2", (2, 3, 4)),
    ("r48-26-Qxf6", 48, "8/p1p2pkp/5r2/3rQ3/8/1PP1P3/PK4PP/5R2 w - - 0 26", "e5f6", (2, 3, 4)),
    ("r49-53-h3", 49, "8/4p3/1k6/1p2p3/p2pP2p/P7/8/3K4 b - - 0 53", "h4h3", (2, 3, 4)),
    ("r50-35-d3", 50, "8/5p2/8/2bk2p1/2np3p/r4P1P/P3R1P1/3K4 b - - 7 35", "d4d3", (2, 3, 4)),
    ("r52-40-c7", 52, "8/5pp1/2P1p3/6rk/NR5p/4P2P/5KP1/8 w - - 1 40", "c6c7", (2, 3, 4)),
    ("r52-55-Re4", 52, "8/8/4R3/8/N7/1k2P2P/3Q2PK/8 w - - 1 55", "e6e4", (2, 3, 4)),
    ("round30-Bf4", 30, "r1bqk2r/pp2n1pp/1bn2p2/1B1p2B1/3N4/8/PPP2PPP/R2QK1NR w KQkq - 0 10",
     "g5f4", (2, 3, 4, 5)),
)
# Each rated clock, and the multiples of it the clock ladder walks. The allocation is
# left exactly as it is: a larger clock is the only lever, so a rung that finally plays
# the correcting move says what `available / 32` would have needed to buy that depth.
LADDER_CLOCKS_MS = {
    "r46-30-Ka4": 68_720,
    "r51-62-Ke3": 39_130,
    "r51-53-Qc5": 44_890,
    "r51-66-Qc4": 36_930,
    "r53-35-Rf7": 62_180,
    "r53-52-Bc6": 44_700,
    "r45-54-Rc7": 42_620,
}
MULTIPLES = (1, 2, 4, 8, 16, 32)


def critical(roles: tuple[str, ...]) -> list[dict[str, Any]]:
    """Every candidate fixture, at every depth, under each nominated source."""
    engines = {role: source(role) for role in roles}
    out = []
    for cid, rnd, fen, played, depths in CRITICAL:
        board = chess.Board(fen)
        row: dict[str, Any] = {"id": cid, "round": rnd, "fen": fen, "rated_move": played}
        row["engines"] = {}
        for name, module in engines.items():
            rungs: dict[str, Any] = {}
            for depth in depths:
                started = time.perf_counter()
                best = root(module, board, depth)
                if played is None or best["best"] == played:
                    actual = best
                else:
                    actual = move_score(module, board, chess.Move.from_uci(played), depth)
                rungs[str(depth)] = {
                    "best": best["best"],
                    "best_san": best["best_san"],
                    "best_score": best["score"],
                    "played_score": actual["score"],
                    "loss_cp": best["score"] - actual["score"],
                    "nodes": best["nodes"],
                    "q_nodes": best["quiescence_nodes"],
                    "seconds": round(time.perf_counter() - started, 2),
                    "pv": " ".join(best["pv"][:10]),
                }
                print(
                    f"{cid:<20} {name:<8} d{depth} {best['best_san']:<8} "
                    f"best={best['score']:+7d} played={actual['score']:+7d} "
                    f"loss={best['score'] - actual['score']:+6d}",
                    file=sys.stderr,
                    flush=True,
                )
            row["engines"][name] = rungs
        row["delta_pruning_changes_choice"] = len(roles) > 1 and any(
            row["engines"][roles[0]][str(d)]["best"] != row["engines"][roles[1]][str(d)]["best"]
            for d in depths
        )
        out.append(row)
    return out


def clock_ladder(role: str) -> list[dict[str, Any]]:
    """The smallest multiple of the rated clock at which each error stops being played."""
    module = source(role)
    out = []
    for cid, _, fen, played, _ in CRITICAL:
        if cid not in LADDER_CLOCKS_MS or played is None:
            continue
        clock = LADDER_CLOCKS_MS[cid]
        board = chess.Board(fen)
        rungs = []
        for multiple in MULTIPLES:
            result = think(module, fen, clock * multiple)
            rungs.append(
                {
                    "multiple": multiple,
                    "clock_ms": clock * multiple,
                    "budget_s": round(clock * multiple / 32_000, 2),
                    "move": result["move"],
                    "san": board.san(chess.Move.from_uci(result["move"])),
                    "completed_depth": result["completed_depth"],
                    "seconds": result["seconds"],
                    "avoided": result["move"] != played,
                }
            )
            print(
                f"{cid:<12} x{multiple:<3} clock={clock * multiple / 1000:8.1f}s "
                f"budget={clock * multiple / 32_000:6.2f}s depth={result['completed_depth']} "
                f"move={rungs[-1]['san']:<6} "
                f"{'AVOIDED' if rungs[-1]['avoided'] else 'repeats rated move'}",
                file=sys.stderr,
                flush=True,
            )
            if rungs[-1]["avoided"]:
                break
        out.append({"id": cid, "fen": fen, "rated_move": played, "rated_clock_ms": clock,
                    "rungs": rungs})
    return out


def mate_search(fen: str, depths: tuple[int, ...]) -> list[dict[str, Any]]:
    """Rising full-window searches, reported with whether a mate score came back.

    A mate score out of this alpha-beta is sound in the direction that matters: the
    defender generates every legal move, and the only shortcut on its side is a
    quiescence stand-pat, which can suppress a mate claim but never manufacture one.
    So a mate here is a forced mate; its absence proves nothing.
    """
    module = rated()
    board = chess.Board(fen)
    rows = []
    for depth in depths:
        outcome = root(module, board, depth)
        mate = abs(outcome["score"]) > module.MATE - module.MAX_PLY
        rows.append(
            {
                "depth": depth,
                "best": outcome["best_san"],
                "score": outcome["score"],
                "forced_mate": mate,
                "nodes": outcome["nodes"],
                "seconds": outcome["seconds"],
                "pv": " ".join(outcome["pv"][:10]),
            }
        )
        print(f"d{depth} {outcome['best_san']:<8} {outcome['score']:+7d} "
              f"{'MATE' if mate else '':<4} nodes={outcome['nodes']}",
              file=sys.stderr, flush=True)
        if mate:
            break
    return rows


def king_danger(board: chess.Board, colour: bool) -> dict[str, int]:
    """Cheap exposure proxies around one side's king, for the king-safety probe."""
    king = board.king(colour)
    assert king is not None
    ring = chess.BB_KING_ATTACKS[king]
    file = chess.square_file(king)
    return {
        "attacked_ring": sum(board.is_attacked_by(not colour, s) for s in chess.scan_forward(ring)),
        "shield": (ring & board.pawns & board.occupied_co[colour]).bit_count(),
        "own_file_pawns": (
            board.pawns & board.occupied_co[colour] & chess.BB_FILES[file]
        ).bit_count(),
    }


def probe_king_safety() -> list[dict[str, Any]]:
    """Does a static king-danger proxy separate the losing king move from the safe one?"""
    module = rated()
    out = []
    for cid, _, fen, played, _ in CRITICAL:
        board = chess.Board(fen)
        if played is None or board.piece_type_at(chess.Move.from_uci(played).from_square) != (
            chess.KING
        ):
            continue
        mover = board.turn
        rows = []
        for move in board.legal_moves:
            board.push(move)
            rows.append(
                {"uci": move.uci(), "static": -module.evaluate(board), **king_danger(board, mover)}
            )
            board.pop()
        rows.sort(key=lambda r: -int(r["static"]))
        out.append({"id": cid, "fen": fen, "played": played, "moves": rows})
        for row in rows:
            print(f"{cid:<14} {row['uci']:<6} static={row['static']:+5d} "
                  f"ring={row['attacked_ring']} shield={row['shield']}"
                  f"{'  <- PLAYED' if row['uci'] == played else ''}",
                  file=sys.stderr, flush=True)
    return out


def probe_passed_pawn(limit: int = 4) -> list[dict[str, Any]]:
    """Would a stronger advanced-passed-pawn term change any rated decision?

    Both probe engines run the pure-Python evaluation, which the repository already
    asserts equal to the Numba one, so only the single edited expression differs.
    Nothing here is a candidate; it is a direction test before any experiment.
    """
    base = frozen(RATED_PATH, RATED_SHA)
    assert base.count("FAST_EVAL = True") == 1
    control_src = base.replace("FAST_EVAL = True", "FAST_EVAL = False")
    needle = "                    score += (rank * rank * (40 - phase)) // 24\n"
    assert control_src.count(needle) == 1
    strong = needle + (
        "                    if rank >= 4:\n"
        "                        score += (rank - 3) * (rank - 3) * (40 - phase) // 6\n"
    )
    control = load("rated_v4_probe_control", control_src)
    candidate = load("rated_v4_probe_passer", control_src.replace(needle, strong))
    out = []
    for cid, _, fen, played, depths in CRITICAL:
        if played is None:
            continue
        board = chess.Board(fen)
        rows = []
        for depth in [d for d in depths if d <= limit]:
            first = root(control, board, depth)
            second = root(candidate, board, depth)
            rows.append(
                {
                    "depth": depth,
                    "control": first["best"],
                    "control_score": first["score"],
                    "passer": second["best"],
                    "passer_score": second["score"],
                    "changed": first["best"] != second["best"],
                }
            )
            print(f"{cid:<20} d{depth} control={first['best_san']:<8}{first['score']:+6d} "
                  f"passer={second['best_san']:<8}{second['score']:+6d}"
                  f"{'  CHANGED' if rows[-1]['changed'] else ''}",
                  file=sys.stderr, flush=True)
        out.append({"id": cid, "fen": fen, "played": played, "depths": rows})
    return out


# --------------------------------------------------------------------------- phase 3


def fixtures(role: str) -> dict[str, Any]:
    """Run every rated v4 fixture against one source and report pass or fail.

    A fixture is satisfied when the source's fixed-depth choice is in `acceptable_uci`,
    or, where that list is deliberately empty because several moves keep the result, is
    anything other than `unacceptable_uci`. Fixtures marked `enforced: false` are
    context: they are measured and reported, and they never decide a candidate.
    """
    module = source(role)
    record = json.loads(POSITIONS.read_text(encoding="utf-8"))
    results = []
    for case in record["positions"]:
        board = chess.Board(case["fen"])
        depth = case["minimum_correcting_depth"]
        outcome = root(module, board, depth)
        acceptable = set(case["acceptable_uci"])
        rejected = case.get("unacceptable_uci")
        passed = outcome["best"] in acceptable if acceptable else outcome["best"] != rejected
        if rejected is not None:
            passed = passed and outcome["best"] != rejected
        results.append(
            {
                "id": case["id"],
                "round": case["round"],
                "kind": case["kind"],
                "enforced": case["enforced"],
                "depth": depth,
                "chose": outcome["best"],
                "chose_san": outcome["best_san"],
                "score": outcome["score"],
                "acceptable": sorted(acceptable),
                "must_not_play": rejected,
                "passed": passed,
                "seconds": outcome["seconds"],
                "nodes": outcome["nodes"],
            }
        )
    enforced = [r for r in results if r["enforced"]]
    return {
        "source": role,
        "enforced_total": len(enforced),
        "enforced_passed": sum(r["passed"] for r in enforced),
        "passed": all(r["passed"] for r in enforced),
        "failed": [r["id"] for r in enforced if not r["passed"]],
        "context_failed": [r["id"] for r in results if not r["enforced"] and not r["passed"]],
        "results": results,
    }


# ------------------------------------------------------------------------------ main


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "mode",
        choices=(
            "validate", "scan", "deep", "replay", "ladder", "fixtures",
            "critical", "clocks", "mate", "king-safety", "passed-pawn",
        ),
    )
    parser.add_argument("--source", default="rated")
    parser.add_argument("--rounds", default=",".join(str(r) for r in ROUNDS))
    parser.add_argument("--depths", default="2,3,4,5")
    parser.add_argument("--index", type=int, default=-1)
    parser.add_argument("--fen", default="")
    parser.add_argument("--clocks", default="120000,67000,33500,16750")
    parser.add_argument("--out", default="")
    args = parser.parse_args()
    depths = tuple(int(d) for d in args.depths.split(","))
    rounds = [int(r) for r in args.rounds.split(",")]

    if args.mode == "validate":
        report: dict[str, Any] = validate()
    elif args.mode == "scan":
        report = scan(args.source, rounds, depths)
    elif args.mode == "replay":
        report = replay(args.source, rounds)
    elif args.mode == "ladder":
        report = think_ladder(
            args.source, args.fen, tuple(int(c) for c in args.clocks.split(","))
        )
    elif args.mode == "fixtures":
        report = fixtures(args.source)
    elif args.mode == "critical":
        report = {"positions": critical(tuple(args.source.split(",")))}
    elif args.mode == "clocks":
        report = {"ladders": clock_ladder(args.source)}
    elif args.mode == "mate":
        report = {"fen": args.fen, "rungs": mate_search(args.fen, depths)}
    elif args.mode == "king-safety":
        report = {"probe": "king safety separability", "cases": probe_king_safety()}
    elif args.mode == "passed-pawn":
        report = {
            "probe": "advanced passed pawn term",
            "depth_limit": max(depths),
            "cases": probe_passed_pawn(max(depths)),
        }
    else:
        game = next(g for g in games() if g.round == rounds[0])
        ply = next(p for p in game.plies if p.index == args.index)
        board = chess.Board(ply.fen_before)
        played = chess.Move.from_uci(ply.uci)
        report = {
            "round": game.round,
            "move": game.label(ply),
            "fen": ply.fen_before,
            "played": ply.uci,
            "rated": ladder(rated(), board, played, depths),
            "working": ladder(working(), board, played, depths),
        }
    text = json.dumps(report, indent=2)
    if args.out:
        target = Path(args.out)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text + "\n", encoding="utf-8")
        print(f"wrote {target} ({len(text)} bytes)")
    else:
        print(text)


if __name__ == "__main__":
    main()
