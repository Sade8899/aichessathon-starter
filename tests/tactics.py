"""Motif-labelled tactical corpus, and the measurement that drives Phase 2.

No trusted reference engine is installed in this repository, the platform image ships
none, and downloading one is out of scope. Labelling with a deeper search by the
control itself was tried first and does not discriminate: over random reachable
positions the engine's depth-3 choice equals its own depth-6 choice in 11 of 14 cases
and never loses more than 105 cp, which is what an engine agreeing with itself looks
like rather than a corpus of tactics.

The tactical corpus is therefore built from forced mates, proved from the rules by
`mating_moves` rather than scored by the engine, so a label owes nothing to the
evaluation under test. "Solved" means the engine chose a move that forces mate in the
proved number of moves. `tactics_solved.json` keeps an earlier, engine-scored corpus
that every position solves at depth 2; it serves as the previously-solved regression
set, not as a discriminating measurement.

Nothing here imports the working `agent.py` as a module; sources come from the
experiment manifest by hash.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
import time
import types
from pathlib import Path
from typing import Any

import chess
import numba_validation as numeric
import time_checks

CORPUS = Path("tests/tactics_corpus.json")
VALUE = (0, 100, 320, 335, 500, 950, 20000)
HEAVY = 300
ORACLE_DEPTH = 6
SCREEN_DEPTH = 3
MARGIN_CP = 150
MATE_MOVES = 3
MOTIFS = (
    "mate_threat",
    "check_evasion",
    "fork",
    "pin",
    "skewer",
    "discovered_attack",
    "deflection",
    "overloaded_defender",
    "sacrifice",
    "recapture",
    "promotion",
    "defensive_only",
    "zwischenzug",
    "trapped_piece",
)


def load(name: str, source: str) -> types.ModuleType:
    module = types.ModuleType(name)
    sys.modules[name] = module
    exec(compile(source, name, "exec"), module.__dict__)
    return module


def engine_module(config: str, manifest: Path) -> types.ModuleType:
    time_checks.MANIFEST = manifest
    record = time_checks.manifest()
    sha = record[config + "_sha256"]
    raw = time_checks.resolve(record, config, sha)
    assert hashlib.sha256(raw).hexdigest() == sha
    return load("tactics_" + config, raw.decode())


def counting(module: types.ModuleType, depth: int | None) -> Any:
    """A development engine that counts quiescence calls and stops at a depth."""

    class Counted(module.Engine):  # type: ignore[name-defined, misc]
        def __init__(self) -> None:
            super().__init__()
            self.qnodes = 0

        def quiesce(self, board: chess.Board, alpha: int, beta: int, ply: int) -> int:
            self.qnodes += 1
            return int(super().quiesce(board, alpha, beta, ply))

        def search(
            self, board: chess.Board, remaining: int, alpha: int, beta: int, ply: int
        ) -> int:
            if depth is not None and ply == 1 and remaining == depth:
                raise module.Deadline
            return int(super().search(board, remaining, alpha, beta, ply))

    return Counted()


def variation(module: types.ModuleType, fen: str, first: str, limit: int = 8) -> list[str]:
    """Reconstruct the principal variation from the transposition table.

    The engine keeps no PV array, so the line is walked out of the table it filled
    during the search. A missing or stale entry ends the line, which is why this is
    reported as a reconstruction rather than as the engine's own PV.
    """
    board = chess.Board(fen)
    engine = module._engine
    line = [first]
    board.push(chess.Move.from_uci(first))
    seen = {module.position_key(board)}
    while len(line) < limit:
        key = module.position_key(board)
        stored = engine.table[hash(key) % module.TT_SIZE]
        entry = stored if stored is not None and stored.key == key else None
        if entry is None or entry.move not in board.legal_moves:
            break
        board.push(entry.move)
        line.append(entry.move.uci())
        marker = module.position_key(board)
        if marker in seen:
            break
        seen.add(marker)
    return line


def fixed(module: types.ModuleType, fen: str, depth: int, counted: bool = False) -> dict[str, Any]:
    """One clock-free search to a fixed completed depth, through the real interface."""
    if counted:
        vars(module)["_engine"] = counting(module, depth)
        module._eval_table[:] = [None] * len(module._eval_table)
    else:
        numeric.fresh(module, depth)
    real = module.time
    vars(module)["time"] = types.SimpleNamespace(perf_counter=lambda: 0.0)
    started = time.perf_counter()
    try:
        move = module.get_move(fen, 120_000)
    finally:
        vars(module)["time"] = real
    elapsed = time.perf_counter() - started
    engine = module._engine
    assert chess.Move.from_uci(move) in chess.Board(fen).legal_moves
    scores = {m.uci(): v for m, v in engine.completed_scores.items()}
    reached = int(engine.stats["depth"])
    # `choose` stops iterating as soon as it has a mate score, so a mating position
    # legitimately completes fewer plies than were asked for. Any other shortfall is
    # a fault, because the development clock is frozen and cannot expire.
    decisive = abs(scores.get(move, 0)) > module.MATE - module.MAX_PLY
    assert reached == depth or (reached < depth and decisive), (fen, depth, reached)
    return {
        "move": move,
        "scores": scores,
        "score": scores.get(move),
        "depth": reached,
        "decisive": decisive,
        "nodes": engine.nodes,
        "qnodes": getattr(engine, "qnodes", None),
        "seconds": elapsed,
        "pv": variation(module, fen, move),
    }


def timed(module: types.ModuleType, fen: str, clock_ms: int) -> dict[str, Any]:
    """One search on a real clock, exactly as a game would call it."""
    numeric.fresh(module)
    started = time.perf_counter()
    move = module.get_move(fen, clock_ms)
    seconds = time.perf_counter() - started
    engine = module._engine
    assert chess.Move.from_uci(move) in chess.Board(fen).legal_moves
    assert seconds * 1000 < clock_ms, (clock_ms, seconds)
    scores = {m.uci(): v for m, v in engine.completed_scores.items()}
    return {
        "move": move,
        "score": scores.get(move),
        "depth": int(engine.stats["depth"]),
        "nodes": int(engine.stats["nodes"]),
        "seconds": seconds,
        "pv": variation(module, fen, move),
    }


def oracle_module(manifest: Path) -> types.ModuleType:
    """A labelling copy whose root window is widened to the margin being tested.

    `Engine.choose` searches inferior root moves on a null window below
    `ROOT_WINDOW_MARGIN`, so it reports them as upper bounds pinned 61 cp under the
    best. That is the right economy for playing, but labelling a position as a tactic
    means knowing the best move wins by a clear margin, which those bounds cannot
    show. Widening the window to `MARGIN_CP` extends the exact band far enough to
    decide it, and cannot change which move is best: a move reported at the floor
    never scores above one reported exactly. Only this labelling copy is altered;
    every engine under measurement runs unmodified through `get_move`.
    """
    module = engine_module("candidate", manifest)
    sys.modules.pop("tactics_candidate", None)
    vars(module)["ROOT_WINDOW_MARGIN"] = MARGIN_CP
    return module


def attacker_mates(board: chess.Board, moves_left: int, checks_only: bool) -> bool:
    """Can the side to move force mate within `moves_left` of its own moves?

    Exact and completely independent of the engine: no evaluation is consulted, only
    the rules. With `checks_only` the attacker is restricted to checking moves, which
    keeps the search small. That restriction can only *miss* mates, never invent one,
    so a line it finds is a forced mate by proof, which is what makes these labels
    objective where a self-oracle label would not be.
    """
    if moves_left <= 0:
        return False
    # Materialised, not lazy: `board.legal_moves` generates from the live board, so
    # iterating it while pushing and popping inside the loop yields moves for the
    # wrong position.
    for move in list(board.legal_moves):
        if checks_only and not board.gives_check(move):
            continue
        board.push(move)
        try:
            if board.is_checkmate():
                return True
            if moves_left > 1 and any(board.legal_moves) and defender_loses(board, moves_left):
                return True
        finally:
            board.pop()
    return False


def defender_loses(board: chess.Board, moves_left: int) -> bool:
    """Every reply for the side to move runs into mate within `moves_left - 1`."""
    replies = list(board.legal_moves)
    if not replies:
        return False
    for reply in replies:
        board.push(reply)
        try:
            if not attacker_mates(board, moves_left - 1, True):
                return False
        finally:
            board.pop()
    return True


def mating_moves(board: chess.Board, moves: int) -> list[str]:
    """Every first move that forces mate in at most `moves`.

    The first ply considers all legal moves, so a quiet move that sets up a forced
    mating net is not missed at the root; the continuation is restricted to checks.
    """
    found = []
    for move in list(board.legal_moves):
        board.push(move)
        try:
            mates = board.is_checkmate() or (
                moves > 1 and any(board.legal_moves) and defender_loses(board, moves)
            )
            if mates:
                found.append(move.uci())
        finally:
            board.pop()
    return found


def targets(board: chess.Board, square: int, colour: bool) -> set[int]:
    """Enemy men worth a knight or more, plus the king, attacked from `square`."""
    found = set()
    for target in chess.scan_forward(board.attacks_mask(square) & board.occupied_co[not colour]):
        piece = board.piece_type_at(target)
        if piece is not None and VALUE[piece] >= HEAVY:
            found.add(target)
    return found


def motifs(board: chess.Board, move: chess.Move, score: int, previous: int | None) -> list[str]:
    """Mechanical motif detection. Deliberately permissive: a position may carry several."""
    colour = board.turn
    after = board.copy(stack=False)
    after.push(move)
    found: list[str] = []

    if score >= 30_000 - 96:
        found.append("mate_threat")
    if board.is_check():
        found.append("check_evasion")
    if move.promotion:
        found.append("promotion")
    if previous is not None and move.to_square == previous and board.is_capture(move):
        found.append("recapture")

    hit = targets(after, move.to_square, colour)
    enemy_king = after.king(not colour)
    if after.is_check() and enemy_king is not None:
        hit = hit | {enemy_king}
    if len(hit) >= 2:
        found.append("fork")

    # Discovered attack: some other man of ours gains a heavy target.
    for square in chess.scan_forward(board.occupied_co[colour]):
        if square == move.from_square or after.piece_type_at(square) is None:
            continue
        if targets(after, square, colour) - targets(board, square, colour):
            found.append("discovered_attack")
            break

    # Pin: an enemy man is pinned after the move that was not pinned before.
    for square in chess.scan_forward(after.occupied_co[not colour]):
        if after.is_pinned(not colour, square) and not board.is_pinned(not colour, square):
            found.append("pin")
            break

    # Skewer: a heavy enemy man is attacked with a man of no greater value behind it.
    for front in hit:
        front_piece = after.piece_type_at(front)
        if front_piece is None or VALUE[front_piece] < 500:
            continue
        for back in chess.scan_forward(after.occupied_co[not colour]):
            behind = after.piece_type_at(back)
            if back == front or behind is None:
                continue
            behind_front = front in chess.scan_forward(chess.between(move.to_square, back))
            if behind_front and VALUE[behind] <= VALUE[front_piece]:
                found.append("skewer")
                break
        if "skewer" in found:
            break

    # Deflection / overload: our move hits the one man defending another.
    for guarded in chess.scan_forward(board.occupied_co[not colour]):
        piece = board.piece_type_at(guarded)
        if piece is None or VALUE[piece] < HEAVY:
            continue
        defenders = board.attackers(not colour, guarded)
        if len(defenders) != 1:
            continue
        only = next(iter(chess.scan_forward(int(defenders))))
        if move.to_square == only:
            found.append("deflection")
        elif only in targets(after, move.to_square, colour):
            found.append("overloaded_defender")

    # Sacrifice: a heavy man is offered and the deep score is still winning.
    mover = board.piece_type_at(move.from_square)
    victim = board.piece_type_at(move.to_square)
    if (
        mover is not None
        and VALUE[mover] >= HEAVY
        and after.is_attacked_by(not colour, move.to_square)
        and VALUE[mover] > VALUE[victim or 0] + 100
        and score >= 150
    ):
        found.append("sacrifice")

    # Trapped piece: a heavy enemy man is attacked and has nowhere safe to go.
    for square in chess.scan_forward(after.occupied_co[not colour]):
        piece = after.piece_type_at(square)
        if piece is None or VALUE[piece] < HEAVY or not after.is_attacked_by(colour, square):
            continue
        escapes = [
            m
            for m in after.generate_legal_moves(chess.BB_SQUARES[square])
            if not after.is_attacked_by(colour, m.to_square)
        ]
        if not escapes:
            found.append("trapped_piece")
            break

    forcing = board.is_capture(move) or bool(move.promotion) or after.is_check()
    if not forcing and not board.is_check():
        found.append("defensive_only")
    if previous is not None and move.to_square != previous:
        recaptures = any(m.to_square == previous for m in board.generate_legal_captures())
        if recaptures and (after.is_check() or VALUE[victim or 0] >= HEAVY):
            found.append("zwischenzug")

    return sorted(set(found))


def mined(count: int, seed: int) -> list[tuple[str, int | None]]:
    """Reachable positions from seeded random play out of the standard and held-out starts.

    The walk is long, 40 to 160 plies, because forced mates are what this corpus is
    made of and they are almost absent from the opening: at 1 to 26 plies the rate of
    a mate in three with no shorter mate is 0 in 400, and at 40 to 160 plies it is
    about 1.5%. The square of the previous capture travels with the position so that
    recaptures and zwischenzugs can be told apart from ordinary shots.
    """
    rng = random.Random(seed)
    starts: list[tuple[str, ...]] = [(), *time_checks.openings()]
    result: list[tuple[str, int | None]] = []
    seen: set[str] = set()
    guard = 0
    while len(result) < count and guard < count * 60:
        guard += 1
        board = time_checks.start_board(starts[rng.randrange(len(starts))])
        previous: int | None = None
        for _ in range(rng.randrange(40, 160)):
            if board.is_game_over():
                break
            move = rng.choice(list(board.legal_moves))
            previous = move.to_square if board.is_capture(move) else None
            board.push(move)
        if board.is_game_over() or board.legal_moves.count() < 2:
            continue
        fen = board.fen()
        if fen in seen:
            continue
        seen.add(fen)
        result.append((fen, previous))
    return result


def mate_line(board: chess.Board, moves: int) -> list[str]:
    """The proved mating line against the most stubborn defence.

    Following the first legal reply instead would print a line shorter than the mate
    it proves, because the first reply is often the quickest loss. The defender here
    picks a reply that cannot be mated any faster, so the line's length is the real
    distance to mate and its shape is the sequence the search actually has to see.
    """
    line: list[str] = []
    walk = board.copy(stack=False)
    remaining = moves
    while remaining > 0:
        options = mating_moves(walk, remaining)
        if not options:
            break
        walk.push(chess.Move.from_uci(options[0]))
        line.append(options[0])
        if walk.is_checkmate():
            break
        replies = list(walk.legal_moves)
        if not replies:
            break
        stubborn = replies[0]
        for reply in replies:
            walk.push(reply)
            faster = remaining >= 2 and attacker_mates(walk, remaining - 2, True)
            walk.pop()
            if not faster:
                stubborn = reply
                break
        walk.push(stubborn)
        line.append(stubborn.uci())
        remaining -= 1
    return line


def verify_corpus(path: Path) -> None:
    """Re-prove every label from the rules, independently of how it was mined."""
    rows = corpus(path)
    for row in rows:
        board = chess.Board(row["fen"])
        assert board.is_valid(), row["fen"]
        moves = int(row["mate_in"])
        assert not mating_moves(board, moves - 1), ("a shorter mate exists", row["fen"])
        proved = mating_moves(board, moves)
        assert proved == row["expected_set"], ("label does not re-prove", row["fen"])
        assert row["expected"] in proved
    print(
        "VERIFY "
        + json.dumps(
            {
                "positions": len(rows),
                "mate_in": sorted({int(r["mate_in"]) for r in rows}),
                "shorter_mate_exists": 0,
                "labels_reproved": len(rows),
            }
        ),
        flush=True,
    )


def build(shard: int, shards: int, count: int, seed: int, manifest: Path) -> None:
    """Mine positions whose shortest forced mate is exactly `MATE_MOVES` moves.

    Two earlier corpora built on engine scores failed to discriminate, and the second
    failure is a measurement worth keeping: over random reachable positions this
    engine's depth-3 choice equals its own depth-6 choice in 11 of 14 cases and never
    loses more than 105 cp, so "the deep search disagrees with the shallow one" is too
    rare to build a corpus from. That is a limit of labelling an engine with itself,
    and no reference engine is installed to replace it.

    Forced mate removes the oracle problem entirely. `mating_moves` proves the line
    from the rules alone, so the label owes nothing to the evaluation under test, and
    a mate in `MATE_MOVES` is exactly the forcing sequence this phase is about.
    Positions with a shorter mate are rejected, and that rejection is exact: the
    mate-in-two test searches every first move and every reply.
    """
    del manifest
    for index, (fen, previous) in enumerate(mined(count, seed)):
        if index % shards != shard:
            continue
        board = chess.Board(fen)
        if not attacker_mates(board, MATE_MOVES, True):
            continue
        if mating_moves(board, MATE_MOVES - 1):
            continue
        expected = mating_moves(board, MATE_MOVES)
        if not expected:
            continue
        tags = motifs(board, chess.Move.from_uci(expected[0]), 30_000 - 5, previous)
        print(
            "TACTIC "
            + json.dumps(
                {
                    "fen": fen,
                    "expected": expected[0],
                    "expected_set": expected,
                    "mate_in": MATE_MOVES,
                    "plies": 2 * MATE_MOVES - 1,
                    "oracle": "exact forced-mate proof, independent of the engine",
                    "oracle_pv": mate_line(board, MATE_MOVES),
                    "shorter_mate_exists": False,
                    "previous_capture": previous,
                    "motifs": tags,
                    "in_check": board.is_check(),
                    "index": index,
                }
            ),
            flush=True,
        )


def assemble() -> None:
    """Merge shard output, then split development and holdout before anything is built."""
    rows: dict[str, dict[str, Any]] = {}
    for line in sys.stdin:
        if line.startswith("TACTIC "):
            row = json.loads(line[7:])
            rows[row["fen"]] = row
    ordered = sorted(rows.values(), key=lambda r: int(r["index"]))
    for row in ordered:
        # A deterministic, content-derived split: nothing about any candidate can
        # move a position between the groups, and the seal is checkable later.
        digest = hashlib.sha256(row["fen"].encode()).hexdigest()
        row["group"] = "holdout" if int(digest[:8], 16) % 2 else "development"
    record = {
        "source": "seeded random play from the standard start and the 30 held-out prefixes",
        "oracle": "the promoted control at fixed depth 6; no external engine is installed",
        "margin_cp": MARGIN_CP,
        "positions": ordered,
        "counts": {
            "total": len(ordered),
            "development": sum(r["group"] == "development" for r in ordered),
            "holdout": sum(r["group"] == "holdout" for r in ordered),
        },
        "motif_counts": {m: sum(m in r["motifs"] for r in ordered) for m in MOTIFS},
        "seal": hashlib.sha256(
            "".join(r["fen"] + r["expected"] + r["group"] for r in ordered).encode()
        ).hexdigest(),
    }
    print(json.dumps(record, indent=1))


def corpus(path: Path = CORPUS) -> list[dict[str, Any]]:
    record = json.loads(path.read_text())
    seal = hashlib.sha256(
        "".join(r["fen"] + r["expected"] + r["group"] for r in record["positions"]).encode()
    ).hexdigest()
    assert seal == record["seal"], "the corpus split has been altered"
    return [dict(row) for row in record["positions"]]


def measure(config: str, depths: list[int], clocks: list[int], shard: int, shards: int,
            manifest: Path, repeats: int, path: Path) -> None:
    module = engine_module(config, manifest)
    for index, row in enumerate(corpus(path)):
        if index % shards != shard:
            continue
        result: dict[str, Any] = {
            "config": config,
            "index": index,
            "fen": row["fen"],
            "group": row["group"],
            "expected": row["expected"],
            "motifs": row["motifs"],
            "depths": {},
            "clocks": {},
        }
        for depth in depths:
            counted = fixed(module, row["fen"], depth, True)
            plain = fixed(module, row["fen"], depth, False)
            result["depths"][str(depth)] = {
                "move": plain["move"],
                "solved": plain["move"] in row.get("expected_set", [row["expected"]]),
                "root_score": plain["score"],
                "expected_score": plain["scores"].get(row["expected"]),
                "reached_depth": plain["depth"],
                "decisive": plain["decisive"],
                "nodes": counted["nodes"],
                "qnodes": counted["qnodes"],
                "main_nodes": counted["nodes"] - (counted["qnodes"] or 0),
                "seconds": plain["seconds"],
                "pv": plain["pv"],
            }
        for clock in clocks:
            samples = [timed(module, row["fen"], clock) for _ in range(repeats)]
            wanted = row.get("expected_set", [row["expected"]])
            result["clocks"][str(clock)] = {
                "move": samples[-1]["move"],
                "solved": [s["move"] in wanted for s in samples],
                "root_score": samples[-1]["score"],
                "depth": [s["depth"] for s in samples],
                "nodes": [s["nodes"] for s in samples],
                "seconds": [s["seconds"] for s in samples],
                "pv": samples[-1]["pv"],
            }
        solved = [d for d in depths if result["depths"][str(d)]["solved"]]
        result["earliest_depth"] = min(solved) if solved else None
        print("MEASURE " + json.dumps(result), flush=True)


def paired(
    clocks: list[int],
    repeats: int,
    shard: int,
    shards: int,
    manifest: Path,
    path: Path,
    noise: bool,
) -> None:
    """Both engines on the same position and clock, in counterbalanced order.

    Measuring the two configurations back to back on one position is what keeps a
    wall-clock comparison honest: they meet the same machine, the same moment and the
    same cache state, and the order alternates so that neither is systematically
    first. With `noise` the control is loaded under both names, so whatever the run
    then reports is drift alone and is the floor a real effect has to clear.
    """
    modules = {name: engine_module(name, manifest) for name in ("control", "candidate")}
    if noise:
        sys.modules.pop("tactics_control", None)
        modules["candidate"] = engine_module("control", manifest)
    for index, row in enumerate(corpus(path)):
        if index % shards != shard:
            continue
        wanted = row.get("expected_set", [row["expected"]])
        for repeat in range(repeats):
            for position, clock in enumerate(clocks):
                order = ("control", "candidate")
                if (repeat + index + position) % 2:
                    order = ("candidate", "control")
                measured = {name: timed(modules[name], row["fen"], clock) for name in order}
                print(
                    "PAIRED "
                    + json.dumps(
                        {
                            "index": index,
                            "fen": row["fen"],
                            "group": row["group"],
                            "clock_ms": clock,
                            "repeat": repeat,
                            "order": list(order),
                            "noise": noise,
                            **{
                                name: {
                                    "move": measured[name]["move"],
                                    "solved": measured[name]["move"] in wanted,
                                    "depth": measured[name]["depth"],
                                    "nodes": measured[name]["nodes"],
                                    "seconds": measured[name]["seconds"],
                                }
                                for name in ("control", "candidate")
                            },
                        }
                    ),
                    flush=True,
                )


def paired_report() -> None:
    rows = [json.loads(line[7:]) for line in sys.stdin if line.startswith("PAIRED ")]
    assert rows, "no PAIRED rows"
    out: dict[str, Any] = {"measurements": len(rows), "noise": any(r["noise"] for r in rows)}
    for group in ("development", "holdout", "ALL"):
        sub = [r for r in rows if group == "ALL" or r["group"] == group]
        if not sub:
            continue
        positions = {r["index"] for r in sub}
        entry: dict[str, Any] = {"positions": len(positions), "measurements": len(sub)}
        for name in ("control", "candidate"):
            entry[name] = {
                "solved": sum(r[name]["solved"] for r in sub),
                "solved_rate": sum(r[name]["solved"] for r in sub) / len(sub),
                "mean_depth": sum(r[name]["depth"] for r in sub) / len(sub),
                "mean_nodes": sum(r[name]["nodes"] for r in sub) / len(sub),
                "worst_seconds": max(r[name]["seconds"] for r in sub),
            }
        # A position counts as solved for a configuration only if every repeat of it
        # solved it, so a single lucky run cannot be reported as a solve.
        stable = {
            name: sum(
                1
                for index in positions
                if all(r[name]["solved"] for r in sub if r["index"] == index)
            )
            for name in ("control", "candidate")
        }
        entry["stable_solved"] = stable
        entry["stable_delta"] = stable["candidate"] - stable["control"]
        entry["stable_points"] = round(
            100 * (stable["candidate"] - stable["control"]) / len(positions), 2
        )
        entry["newly_solved"] = sum(
            1
            for index in positions
            if all(r["candidate"]["solved"] for r in sub if r["index"] == index)
            and not all(r["control"]["solved"] for r in sub if r["index"] == index)
        )
        entry["newly_unsolved"] = sum(
            1
            for index in positions
            if all(r["control"]["solved"] for r in sub if r["index"] == index)
            and not all(r["candidate"]["solved"] for r in sub if r["index"] == index)
        )
        entry["mean_depth_change"] = round(
            entry["candidate"]["mean_depth"] - entry["control"]["mean_depth"], 4
        )
        entry["node_ratio"] = round(
            entry["candidate"]["mean_nodes"] / max(1e-9, entry["control"]["mean_nodes"]), 4
        )
        out[group] = entry
    by_clock = {}
    for clock in sorted({r["clock_ms"] for r in rows}):
        sub = [r for r in rows if r["clock_ms"] == clock]
        by_clock[str(clock)] = {
            "control_solved_rate": sum(r["control"]["solved"] for r in sub) / len(sub),
            "candidate_solved_rate": sum(r["candidate"]["solved"] for r in sub) / len(sub),
            "control_mean_depth": sum(r["control"]["depth"] for r in sub) / len(sub),
            "candidate_mean_depth": sum(r["candidate"]["depth"] for r in sub) / len(sub),
        }
    out["by_clock"] = by_clock
    print(json.dumps(out, indent=1))


def pv_shape(fen: str, line: list[str]) -> dict[str, int]:
    """What kind of sequence the oracle's line is: how forcing, and how deep."""
    board = chess.Board(fen)
    checks = captures = recaptures = forced = plies = 0
    last: int | None = None
    for uci in line:
        move = chess.Move.from_uci(uci)
        if move not in board.legal_moves:
            break
        if board.is_capture(move):
            captures += 1
            recaptures += int(last == move.to_square)
            last = move.to_square
        else:
            last = None
        board.push(move)
        plies += 1
        checks += int(board.is_check())
        forced += int(board.legal_moves.count() == 1)
    return {
        "plies": plies,
        "checks": checks,
        "captures": captures,
        "recaptures": recaptures,
        "single_reply": forced,
    }


def classify(row: dict[str, Any], clock: str) -> str:
    """Why the engine missed this tactic, from the measurements alone.

    The classes are disjoint and decided in a fixed order, so no row is counted twice.
    """
    timed_row = row["clocks"].get(clock)
    if timed_row is None:
        return "unmeasured"
    if all(timed_row["solved"]):
        return "solved_at_clock"
    if row["earliest_depth"] is None:
        return "horizon_beyond_measured_depth"
    if max(timed_row["depth"]) < row["earliest_depth"]:
        return "depth_not_reached_in_time"
    return "unstable_at_clock"


def summarise(sub: list[dict[str, Any]], clock: str, depths: list[int]) -> dict[str, Any]:
    timed_rows = [r["clocks"][clock] for r in sub if clock in r["clocks"]]
    solved = sum(all(t["solved"]) for t in timed_rows)
    classes: dict[str, int] = {}
    motif_fail: dict[str, int] = {}
    for row in sub:
        label = classify(row, clock)
        classes[label] = classes.get(label, 0) + 1
        if label != "solved_at_clock":
            for motif in row["motifs"]:
                motif_fail[motif] = motif_fail.get(motif, 0) + 1
    return {
        "positions": len(sub),
        "solved_by_depth": {
            str(d): sum(r["depths"][str(d)]["solved"] for r in sub if str(d) in r["depths"])
            for d in depths
        },
        "solved_at_clock": solved,
        "solved_at_clock_rate": solved / max(1, len(timed_rows)),
        "mean_depth_at_clock": (
            sum(sum(t["depth"]) / len(t["depth"]) for t in timed_rows) / max(1, len(timed_rows))
        ),
        "total_nodes_by_depth": {
            str(d): sum(r["depths"][str(d)]["nodes"] for r in sub if str(d) in r["depths"])
            for d in depths
        },
        "total_qnodes_by_depth": {
            str(d): sum(r["depths"][str(d)]["qnodes"] for r in sub if str(d) in r["depths"])
            for d in depths
        },
        "failures": dict(sorted(classes.items())),
        "motifs_unsolved": dict(sorted(motif_fail.items())),
    }


def report(clock: str, depths: list[int]) -> None:
    rows: dict[tuple[str, int], dict[str, Any]] = {}
    for line in sys.stdin:
        if line.startswith("MEASURE "):
            row = json.loads(line[8:])
            rows[(row["config"], row["index"])] = row
    configs = sorted({key[0] for key in rows})
    record: dict[str, Any] = {"clock_ms": clock, "depths": depths, "configs": {}}
    for config in configs:
        mine = [r for r in rows.values() if r["config"] == config]
        groups = {
            group: summarise([r for r in mine if r["group"] == group], clock, depths)
            for group in ("development", "holdout")
            if any(r["group"] == group for r in mine)
        }
        record["configs"][config] = {"positions": len(mine), "groups": groups}
    if len(configs) == 2:
        # Named, not sorted: "candidate" sorts first and would invert every delta.
        control, candidate = "control", "candidate"
        assert set(configs) == {control, candidate}, configs
        deltas = {}
        for group in ("development", "holdout"):
            a = record["configs"][control]["groups"].get(group)
            b = record["configs"][candidate]["groups"].get(group)
            if not a or not b:
                continue
            deltas[group] = {
                "solved_at_clock": b["solved_at_clock"] - a["solved_at_clock"],
                "rate_points": round(
                    100 * (b["solved_at_clock_rate"] - a["solved_at_clock_rate"]), 2
                ),
                "by_depth": {
                    d: b["solved_by_depth"][d] - a["solved_by_depth"][d]
                    for d in a["solved_by_depth"]
                },
                "node_ratio_by_depth": {
                    d: round(
                        b["total_nodes_by_depth"][d] / max(1, a["total_nodes_by_depth"][d]), 4
                    )
                    for d in a["total_nodes_by_depth"]
                },
                "mean_depth_change": round(b["mean_depth_at_clock"] - a["mean_depth_at_clock"], 4),
            }
        record["paired"] = {"control": control, "candidate": candidate, "groups": deltas}
    print(json.dumps(record, indent=1))


def shapes(path: Path) -> None:
    """The forcing shape of every corpus line, which is what picks the technique."""
    totals: dict[str, int] = {}
    for row in corpus(path):
        shape = pv_shape(row["fen"], row["oracle_pv"])
        for key, value in shape.items():
            totals[key] = totals.get(key, 0) + value
        print(
            "SHAPE " + json.dumps({"fen": row["fen"], "group": row["group"], **shape}), flush=True
        )
    print("SHAPES " + json.dumps({"totals": totals}), flush=True)


def quiet(config: str, depths: list[int], manifest: Path) -> None:
    """Ordinary, non-tactical positions: the search a tactical change must not damage."""
    module = engine_module(config, manifest)
    positions = [time_checks.start_board(o).fen() for o in time_checks.openings()]
    positions += numeric.suite()
    for index, fen in enumerate(positions):
        result: dict[str, Any] = {"config": config, "index": index, "fen": fen, "depths": {}}
        for depth in depths:
            counted = fixed(module, fen, depth, True)
            result["depths"][str(depth)] = {
                "move": counted["move"],
                "score": counted["score"],
                "nodes": counted["nodes"],
                "qnodes": counted["qnodes"],
            }
        print("QUIET " + json.dumps(result), flush=True)


def quiet_adjudicate(depths: list[int], manifest: Path) -> None:
    """Referee every quiet disagreement against a deeper control search.

    Move agreement alone cannot say whether a change hurt: an extension is supposed
    to change some moves. This is the rule `order_checks.adjudicate` already uses --
    score both moves with the control one ply deeper, and call it a regression only
    when the candidate's move loses ground the control's move keeps.
    """
    modules = {name: engine_module(name, manifest) for name in ("control", "candidate")}
    positions = [time_checks.start_board(o).fen() for o in time_checks.openings()]
    positions += numeric.suite()
    rows = []
    for depth in depths:
        for index, fen in enumerate(positions):
            control = fixed(modules["control"], fen, depth)
            candidate = fixed(modules["candidate"], fen, depth)
            if control["move"] == candidate["move"]:
                continue
            deeper = fixed(modules["control"], fen, depth + 1)
            scores = deeper["scores"]
            best = max(scores.values()) if scores else 0
            loss = best - scores.get(candidate["move"], best)
            control_loss = best - scores.get(control["move"], best)
            rows.append(
                {
                    "position": index,
                    "fen": fen,
                    "depth": depth,
                    "control_move": control["move"],
                    "candidate_move": candidate["move"],
                    "referee_depth": depth + 1,
                    "candidate_loss_cp": loss,
                    "control_loss_cp": control_loss,
                    "regression": loss >= 100 and loss > control_loss,
                    "improvement": control_loss >= 100 and control_loss > loss,
                }
            )
            print("QADJ " + json.dumps(rows[-1]), flush=True)
    print(
        "QADJUDICATION "
        + json.dumps(
            {
                "positions": len(positions),
                "depths": depths,
                "disagreements": len(rows),
                "regressions": sum(r["regression"] for r in rows),
                "improvements": sum(r["improvement"] for r in rows),
                "worst_candidate_loss_cp": max((r["candidate_loss_cp"] for r in rows), default=0),
                "worst_control_loss_cp": max((r["control_loss_cp"] for r in rows), default=0),
            }
        ),
        flush=True,
    )


def quiet_report() -> None:
    rows: dict[tuple[str, int], dict[str, Any]] = {}
    for line in sys.stdin:
        if line.startswith("QUIET "):
            row = json.loads(line[6:])
            rows[(row["config"], row["index"])] = row
    # Named, not sorted: "candidate" sorts before "control" and swapping the two
    # silently inverts every ratio in the report.
    control, candidate = "control", "candidate"
    assert {key[0] for key in rows} == {control, candidate}, sorted({k[0] for k in rows})
    indices = sorted({key[1] for key in rows if key[0] == control})
    depths = sorted(rows[(control, indices[0])]["depths"], key=int)
    out: dict[str, Any] = {"positions": len(indices), "control": control, "candidate": candidate}
    for depth in depths:
        agree = 0
        control_nodes = candidate_nodes = 0
        for index in indices:
            a = rows[(control, index)]["depths"][depth]
            b = rows[(candidate, index)]["depths"][depth]
            agree += a["move"] == b["move"]
            control_nodes += a["nodes"]
            candidate_nodes += b["nodes"]
        out[depth] = {
            "move_agreement": round(agree / len(indices), 4),
            "disagreements": len(indices) - agree,
            "control_nodes": control_nodes,
            "candidate_nodes": candidate_nodes,
            "node_ratio": round(candidate_nodes / max(1, control_nodes), 4),
        }
    print(json.dumps(out, indent=1))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "mode",
        choices=(
            "build",
            "assemble",
            "measure",
            "report",
            "shapes",
            "quiet",
            "quiet-report",
            "quiet-adjudicate",
            "paired",
            "paired-report",
            "verify",
        ),
    )
    parser.add_argument("--shard", type=int, default=0)
    parser.add_argument("--shards", type=int, default=1)
    parser.add_argument("--count", type=int, default=1800)
    parser.add_argument("--seed", type=int, default=70000)
    parser.add_argument("--config", default="candidate")
    parser.add_argument("--depths", default="2,3,4,5")
    parser.add_argument("--clocks", default="100000,113600,120000")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--clock", default="120000")
    parser.add_argument("--corpus", default=str(CORPUS))
    parser.add_argument("--noise", action="store_true")
    parser.add_argument("--experiment", default="tests/order_experiment.json")
    args = parser.parse_args()
    manifest = Path(args.experiment)
    depth_list = [int(d) for d in args.depths.split(",") if d]
    if args.mode == "build":
        build(args.shard, args.shards, args.count, args.seed, manifest)
    elif args.mode == "assemble":
        assemble()
    elif args.mode == "report":
        report(args.clock, depth_list)
    elif args.mode == "shapes":
        shapes(Path(args.corpus))
    elif args.mode == "verify":
        verify_corpus(Path(args.corpus))
    elif args.mode == "paired":
        paired(
            [int(c) for c in args.clocks.split(",") if c],
            args.repeats,
            args.shard,
            args.shards,
            manifest,
            Path(args.corpus),
            args.noise,
        )
    elif args.mode == "paired-report":
        paired_report()
    elif args.mode == "quiet":
        quiet(args.config, depth_list, manifest)
    elif args.mode == "quiet-report":
        quiet_report()
    elif args.mode == "quiet-adjudicate":
        quiet_adjudicate(depth_list, manifest)
    else:
        measure(
            args.config,
            depth_list,
            [int(c) for c in args.clocks.split(",") if c],
            args.shard,
            args.shards,
            manifest,
            args.repeats,
            Path(args.corpus),
        )


if __name__ == "__main__":
    main()
