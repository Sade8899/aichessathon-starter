"""Rated rounds 57-80: corpus completion, historical-clock reproduction, diagnosis.

Rounds 44-56 are handled by `rated_v4.py` and are not re-litigated here. This module
reuses that file's loaders, PGN reader and fixed-depth probes verbatim -- it imports it
by path rather than copying it, so there is exactly one implementation of `root`,
`ladder`, `think` and `clocks` in the repository.

The engine under test is the working `agent.py`. Unlike rounds 44-56, the working source
*is* the engine that played rounds 57-78, so a rated error here is evidence about the
file that is currently on disk.

Modes:

    validate    headers, legality, clocks and team-page agreement for rounds 57-80
    scan        fixed-depth ladder over every Sassori move, to locate the swings
    repro       N cold repetitions of one fixture set at the exact historical clock
    fixtures    run `rated_v5_positions.json` against a nominated source
"""

from __future__ import annotations

import argparse
import gc
import importlib.util
import json
import sys
import types
from pathlib import Path
from typing import Any

import chess
import chess.pgn

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
GAMES = ROOT / "submission 0609v4"
RESULTS = ROOT / "tests" / "results" / "rated_v5"
POSITIONS = HERE / "rated_v5_positions.json"
SASSORI = "Sassori"
ROUNDS = tuple(range(57, 81))

# Colour, outcome and termination for every round 57-80, transcribed from the team page
# at https://aichessathon.com/team/daf4188f-c437-4905-92a1-3935ff41c690 on 2026-09-09.
# `validate` asserts each PGN against this table, so a mis-downloaded or mis-transcribed
# game fails loudly instead of quietly becoming a fixture.
EXPECTED: dict[int, tuple[str, str, str]] = {
    57: ("White", "Loss", "checkmate"),
    58: ("Black", "Loss", "checkmate"),
    59: ("White", "Win", "checkmate"),
    60: ("Black", "Win", "checkmate"),
    61: ("Black", "Draw", "threefold_repetition"),
    62: ("White", "Win", "checkmate"),
    63: ("Black", "Win", "checkmate"),
    64: ("White", "Draw", "insufficient_material"),
    65: ("White", "Win", "checkmate"),
    66: ("Black", "Loss", "checkmate"),
    67: ("Black", "Win", "checkmate"),
    68: ("White", "Win", "checkmate"),
    69: ("White", "Win", "checkmate"),
    70: ("Black", "Loss", "checkmate"),
    71: ("Black", "Win", "checkmate"),
    72: ("White", "Loss", "checkmate"),
    73: ("Black", "Win", "illegal"),
    74: ("White", "Loss", "checkmate"),
    75: ("Black", "Win", "checkmate"),
    76: ("White", "Loss", "checkmate"),
    77: ("White", "Draw", "threefold_repetition"),
    78: ("Black", "Win", "checkmate"),
    79: ("Black", "Win", "checkmate"),
    80: ("White", "Loss", "checkmate"),
}
# Round 73 ended when the opponent emitted an illegal move, so the final position is an
# ordinary middlegame and no board-level termination condition can be asserted for it.
NO_TERMINAL_CHECK = frozenset({73})
BASE_S = 120.0
INCREMENT_S = 0.5
BUDGET_CEILING_S = 3.75


def _rated_v4() -> types.ModuleType:
    """Import `rated_v4.py` by path, so its probes are shared rather than duplicated."""
    name = "rated_v5_v4"
    cached = sys.modules.get(name)
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location(name, HERE / "rated_v4.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


V4 = _rated_v4()


def games(rounds: tuple[int, ...] = ROUNDS) -> list[Any]:
    """Every PGN in the corpus whose round header is in `rounds`, ordered by round."""
    found = []
    for path in sorted(GAMES.glob("*.pgn")):
        game = V4.read(path)
        if game.round in rounds:
            found.append(game)
    return sorted(found, key=lambda g: g.round)


# ------------------------------------------------------------------------- phase 1


def validate() -> dict[str, Any]:
    """Assert every round 57-80 PGN against the team page and against chess itself."""
    reports = []
    for game in games():
        rnd = game.round
        colour, outcome, termination = EXPECTED[rnd]
        board = chess.Board(game.start_fen)
        legal = True
        for ply in game.plies:
            move = chess.Move.from_uci(ply.uci)
            if move not in board.legal_moves:
                legal = False
                break
            board.push(move)

        ours = game.sassori
        won = (game.result == "1-0") == ours if game.result != "1/2-1/2" else None
        actual_outcome = "Draw" if won is None else ("Win" if won else "Loss")
        rows = V4.clocks(game)
        seconds = [r["seconds_used"] for r in rows if r["seconds_used"] is not None]
        after = [r["clock_after_s"] for r in rows if r["clock_after_s"] is not None]

        if rnd in NO_TERMINAL_CHECK:
            terminal = None
        elif termination == "checkmate":
            terminal = board.is_checkmate()
        elif termination == "threefold_repetition":
            terminal = board.is_repetition(3) or board.can_claim_threefold_repetition()
        elif termination == "insufficient_material":
            terminal = board.is_insufficient_material()
        else:
            terminal = None

        mated_ok: bool | None = None
        if terminal and termination == "checkmate":
            mated_ok = (board.turn == ours) == (actual_outcome == "Loss")

        checks = {
            "headers_present": all(h in game.headers for h in V4.REQUIRED_HEADERS),
            "start_fen_legal": chess.Board(game.start_fen).is_valid(),
            "setup_flag": game.headers.get("SetUp") == "1",
            "not_standard_start": game.start_fen != chess.STARTING_FEN,
            "all_moves_legal": legal,
            "under_600_plies": len(game.plies) <= 600,
            "colour_matches_expected": ("White" if ours else "Black") == colour,
            "outcome_matches_expected": actual_outcome == outcome,
            "termination_matches_expected": game.termination == termination,
            "termination_condition_holds": terminal,
            "mated_side_matches_result": mated_ok,
            "clocks_present": len(after) == len(rows),
            "clock_never_negative": all(c >= 0 for c in after),
            "no_move_exceeded_budget": all(s <= BUDGET_CEILING_S for s in seconds),
            "round_header_matches_filename": f"-round-{rnd}-" in game.path.name,
            "round_in_range": 57 <= rnd <= 80,
        }
        failed = [k for k, v in checks.items() if v is False]
        reports.append(
            {
                "round": rnd,
                "file": game.path.name,
                "opponent": game.headers["Black" if ours else "White"],
                "colour": "White" if ours else "Black",
                "result": game.result,
                "outcome": actual_outcome,
                "termination": game.termination,
                "plies": len(game.plies),
                "our_moves": len(game.ours()),
                "slowest_s": max(seconds) if seconds else None,
                "clock_left_s": after[-1] if after else None,
                "checks": checks,
                "passed": not failed,
                "failed": failed,
            }
        )
    return {
        "rounds_expected": list(ROUNDS),
        "rounds_found": [r["round"] for r in reports],
        "missing": sorted(set(ROUNDS) - {int(r["round"]) for r in reports}),
        "passed": all(r["passed"] for r in reports) and not set(ROUNDS) - {
            int(r["round"]) for r in reports
        },
        "games": reports,
    }


# ------------------------------------------------------------------------- phase 2


def scan(role: str, rounds: tuple[int, ...], depths: tuple[int, ...]) -> dict[str, Any]:
    """Fixed-depth ladder over every Sassori move, reporting each move's own loss."""
    module = V4.source(role)
    out: dict[str, Any] = {"source": role, "depths": list(depths), "rounds": {}}
    for game in games(rounds):
        timings = {row["index"]: row for row in V4.clocks(game)}
        entries = []
        for ply in game.ours():
            board = chess.Board(ply.fen_before)
            played = chess.Move.from_uci(ply.uci)
            rung = V4.ladder(module, board, played, depths)
            deepest = rung["depths"][str(depths[-1])]
            row = timings[ply.index]
            entries.append(
                {
                    "index": ply.index,
                    "move": game.label(ply),
                    "uci": ply.uci,
                    "fen": ply.fen_before,
                    "clock_before_ms": round(row["clock_before_s"] * 1000),
                    "seconds_used": row["seconds_used"],
                    "loss_cp": deepest["loss_cp"],
                    "best_at_depth": deepest["best"],
                    "best_san": deepest["best_san"],
                    "correcting_depth": rung["correcting_depth"],
                    "depths": rung["depths"],
                }
            )
            print(
                f"r{game.round} {game.label(ply):<12} loss={deepest['loss_cp']:+6d} "
                f"best={deepest['best_san']:<8} clock={row['clock_before_s']:.1f}",
                file=sys.stderr,
                flush=True,
            )
        entries.sort(key=lambda e: -int(e["loss_cp"]))
        out["rounds"][str(game.round)] = {
            "opponent": game.headers["Black" if game.sassori else "White"],
            "colour": "White" if game.sassori else "Black",
            "outcome": EXPECTED[game.round][1],
            "moves": entries,
        }
    return out


def repro(role: str, ids: tuple[str, ...], repeats: int) -> dict[str, Any]:
    """Replay each fixture at its exact historical clock, `repeats` times, cold.

    Every repetition rebuilds the engine from the source bytes and drops the previous
    module object, so no transposition table, evaluation cache, opponent model or
    killer table survives from one repetition to the next.
    """
    record = json.loads(POSITIONS.read_text(encoding="utf-8"))
    cases = [c for c in record["positions"] if not ids or c["id"] in ids]
    results = []
    for case in cases:
        runs = []
        for _ in range(repeats):
            module = V4.source(role)
            gc.collect()
            outcome = V4.think(module, case["fen"], case["clock_ms"])
            runs.append(outcome)
            for key in list(sys.modules):
                if key.startswith("rated_v4_") and key not in ("rated_v4_rated",):
                    del sys.modules[key]
            del module
            gc.collect()
        chosen = [r["move"] for r in runs]
        rated_move = case["rated_move_uci"]
        matches = sum(m == rated_move for m in chosen)
        unacceptable = set(case.get("unacceptable_uci") or [])
        bad = sum(m in unacceptable for m in chosen) if unacceptable else matches
        deterministic = len(set(chosen)) == 1
        if bad >= 4:
            verdict = "A_reliably_reproduces"
        elif bad > 0:
            verdict = "B_intermittently_reproduces"
        elif deterministic:
            verdict = "C_platform_speed_artefact"
        else:
            verdict = "D_fixture_unstable"
        if case.get("kind") == "solved_control":
            acceptable = set(case.get("acceptable_uci") or [])
            good = sum(m in acceptable for m in chosen)
            verdict = "E_solved_control" if good == repeats else "D_fixture_unstable"
        results.append(
            {
                "id": case["id"],
                "round": case["round"],
                "kind": case["kind"],
                "fen": case["fen"],
                "side_to_move": case["side_to_move"],
                "clock_ms": case["clock_ms"],
                "rated_move_uci": rated_move,
                "rated_move_san": case["rated_move_san"],
                "acceptable_uci": case.get("acceptable_uci"),
                "unacceptable_uci": case.get("unacceptable_uci"),
                "repeats": repeats,
                "chosen": chosen,
                "distinct": sorted(set(chosen)),
                "deterministic": deterministic,
                "reproduced": bad,
                "depths": [r["completed_depth"] for r in runs],
                "seconds": [r["seconds"] for r in runs],
                "nodes": [r["nodes"] for r in runs],
                "classification": verdict,
                "runs": runs,
            }
        )
        print(
            f"{case['id']:<24} {results[-1]['classification']:<28} "
            f"chose={','.join(sorted(set(chosen)))} depths={results[-1]['depths']}",
            file=sys.stderr,
            flush=True,
        )
    return {"source": role, "repeats": repeats, "results": results}


NEEDLE = "                    score += (rank * rank * (40 - phase)) // 24\n"
# The strongest defensible form of the proposed passed-pawn urgency term, written as a
# source transformation so that only this one expression differs between the two probe
# engines. It is scaled by how close the pawn is to promotion and by whether the pawn can
# actually be stopped -- the promotion path clear, no defender of the promotion square,
# and the defending king outside the square of the pawn with the tempo counted. It is a
# direction test, not a candidate: nothing here is written into agent.py.
URGENCY = """
                    if rank >= 4:
                        promotion = chess.square_file(square) + (56 if colour else 0)
                        steps = 7 - rank
                        walk = square
                        clear = True
                        while walk != promotion:
                            walk += 8 if colour else -8
                            if board.piece_at(walk) is not None:
                                clear = False
                        guards = board.attackers(not colour, promotion)
                        tempo = 1 if board.turn != colour else 0
                        caught = enemy_king is not None and (
                            chess.square_distance(enemy_king, promotion) - tempo <= steps
                        )
                        if clear and not guards and not caught:
                            score += WEIGHT * (rank - 3)
                        elif clear and not (guards & ~board.kings) and not caught:
                            score += WEIGHT * (rank - 3) // 2
"""


def probe(weights: tuple[int, ...], depths: tuple[int, ...]) -> dict[str, Any]:
    """Would a passed-pawn urgency term change any rated decision, at any weight?

    Both engines run the pure-Python evaluation, which `capgen_checks.py equality`
    already asserts equal to the Numba one, so only the added expression differs.
    """
    base = V4.frozen(Path("agent.py"), V4.WORKING_SHA)
    assert base.count("FAST_EVAL = True") == 1
    control_src = base.replace("FAST_EVAL = True", "FAST_EVAL = False")
    assert control_src.count(NEEDLE) == 1
    control = V4.load("rated_v5_probe_control", control_src)
    record = json.loads(POSITIONS.read_text(encoding="utf-8"))
    out: list[dict[str, Any]] = []
    for weight in weights:
        text = control_src.replace(NEEDLE, NEEDLE + URGENCY.replace("WEIGHT", str(weight)))
        candidate = V4.load(f"rated_v5_probe_{weight}", text)
        for case in record["positions"]:
            board = chess.Board(case["fen"])
            acceptable = set(case.get("acceptable_uci") or [])
            for depth in depths:
                first = V4.root(control, board, depth)
                second = V4.root(candidate, board, depth)
                fixed = second["best"] in acceptable and first["best"] not in acceptable
                broke = first["best"] in acceptable and second["best"] not in acceptable
                out.append(
                    {
                        "weight": weight,
                        "id": case["id"],
                        "kind": case["kind"],
                        "depth": depth,
                        "control": first["best"],
                        "control_san": first["best_san"],
                        "control_score": first["score"],
                        "probe": second["best"],
                        "probe_san": second["best_san"],
                        "probe_score": second["score"],
                        "changed": first["best"] != second["best"],
                        "fixed": fixed,
                        "broke": broke,
                    }
                )
                print(
                    f"w{weight:<4} {case['id']:<16} d{depth} "
                    f"control={first['best_san']:<8}{first['score']:+6d} "
                    f"probe={second['best_san']:<8}{second['score']:+6d}"
                    f"{'  FIXED' if fixed else ''}{'  BROKE' if broke else ''}"
                    f"{'  changed' if first['best'] != second['best'] else ''}",
                    file=sys.stderr,
                    flush=True,
                )
    return {
        "weights": list(weights),
        "depths": list(depths),
        "fixed": [r["id"] for r in out if r["fixed"]],
        "broke": [r["id"] for r in out if r["broke"]],
        "rows": out,
    }


def fixtures(role: str) -> dict[str, Any]:
    """Run every round 57-80 fixture against one source at its correcting depth."""
    module = V4.source(role)
    record = json.loads(POSITIONS.read_text(encoding="utf-8"))
    results = []
    for case in record["positions"]:
        board = chess.Board(case["fen"])
        depth = case["minimum_correcting_depth"]
        outcome = V4.root(module, board, depth)
        acceptable = set(case.get("acceptable_uci") or [])
        rejected = set(case.get("unacceptable_uci") or [])
        passed = outcome["best"] in acceptable if acceptable else outcome["best"] not in rejected
        if rejected:
            passed = passed and outcome["best"] not in rejected
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
                "must_not_play": sorted(rejected),
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
    parser.add_argument("mode", choices=("validate", "scan", "repro", "fixtures", "probe"))
    parser.add_argument("--source", default="working")
    parser.add_argument("--rounds", default=",".join(str(r) for r in ROUNDS))
    parser.add_argument("--depths", default="2,3,4,5")
    parser.add_argument("--ids", default="")
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--weights", default="20,40,80")
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    if args.mode == "validate":
        report: dict[str, Any] = validate()
    elif args.mode == "scan":
        report = scan(
            args.source,
            tuple(int(r) for r in args.rounds.split(",")),
            tuple(int(d) for d in args.depths.split(",")),
        )
    elif args.mode == "repro":
        report = repro(
            args.source,
            tuple(i for i in args.ids.split(",") if i),
            args.repeats,
        )
    elif args.mode == "probe":
        report = probe(
            tuple(int(w) for w in args.weights.split(",")),
            tuple(int(d) for d in args.depths.split(",")),
        )
    else:
        report = fixtures(args.source)

    text = json.dumps(report, indent=2)
    if args.out:
        target = RESULTS / args.out
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        print(f"wrote {target}", file=sys.stderr)
    print(text)


if __name__ == "__main__":
    main()
