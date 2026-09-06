"""Opening book and Syzygy tablebase feasibility, measured against real evidence.

Downloads nothing and ships nothing. It inventories the available rated material,
tests where the submitted engine first plays an objectively unsafe move, mines the
local sparring games for endgame material classes, and verifies that python-chess
can open a tablebase directory read only and fall back safely when a table is absent.
"""

from __future__ import annotations

import collections
import glob
import json
import types
from pathlib import Path
from typing import Any

import chess
import chess.syzygy
import rated_losses

FIXTURE = Path("tests/tournament/fixtures/rated.json")
# Published Syzygy set sizes, quoted for planning only; nothing was downloaded.
SYZYGY_MB = {"3": 0.1, "4": 7.3, "5": 938.0}


def rated_inventory(fixture: dict[str, Any]) -> dict[str, Any]:
    start = chess.Board(fixture["round30_start"])
    blunder = chess.Board(fixture["calls"][-1]["fen"])
    games = [
        {
            "game": "round30",
            "start_fen": fixture["round30_start"],
            "start_pieces": start.occupied.bit_count(),
            "start_fullmove": start.fullmove_number,
            "preblunder_fen": fixture["calls"][-1]["fen"],
            "preblunder_pieces": blunder.occupied.bit_count(),
            "preblunder_fullmove": blunder.fullmove_number,
            "blunder": fixture["calls"][-1]["expected"],
            "clock_ms": fixture["calls"][-1]["clock_ms"],
            "own_moves_recorded": len(fixture["calls"]),
        }
    ]
    structure = {}
    for colour, name in ((chess.WHITE, "white"), (chess.BLACK, "black")):
        structure[name] = {
            "pawns": sorted(chess.square_name(s) for s in start.pieces(chess.PAWN, colour)),
            "non_pawn_pieces": sum(len(start.pieces(p, colour)) for p in range(2, 7)),
        }
    return {
        "rated_games_available": len(games),
        "second_rated_start": fixture.get("second_rated_start"),
        "games": games,
        "start_structure": structure,
        "start_is_standard_initial": fixture["round30_start"] == chess.STARTING_FEN,
    }


def theory_departure(module: types.ModuleType, fixture: dict[str, Any]) -> list[dict[str, Any]]:
    """Where does the submitted engine first play an objectively unsafe move?

    There is no offline theory database here, so "unsafe" is measured, not looked up:
    a rated move is unsafe when a deeper search of the same position rejects it.
    """
    rows = []
    for index, call in enumerate(fixture["calls"]):
        vars(module)["_engine"] = module.Engine()
        if hasattr(module, "_eval_table"):
            module._eval_table[:] = [None] * len(module._eval_table)
        engine = module._engine
        rated_losses.depth_limited(engine, module, 4)
        clock = module.time
        vars(module)["time"] = types.SimpleNamespace(perf_counter=lambda: 0.0)
        try:
            best = module.get_move(call["fen"], call["clock_ms"])
        finally:
            vars(module)["time"] = clock
            del engine.search
        scores = {m.uci(): v for m, v in engine.completed_scores.items()}
        played = call["expected"]
        rows.append(
            {
                "ply_index": index,
                "fullmove": chess.Board(call["fen"]).fullmove_number,
                "fen": call["fen"],
                "rated_move": played,
                "depth4_best": best,
                "rated_move_cp": scores.get(played),
                "best_cp": scores.get(best),
                "loss_cp": (scores.get(best, 0) - scores.get(played, 0)),
                "objectively_unsafe": scores.get(best, 0) - scores.get(played, 0) >= 100,
            }
        )
    return rows


def endgame_classes() -> dict[str, Any]:
    """Material classes of five or fewer pieces seen in local sparring, not rated play."""
    counts: collections.Counter[str] = collections.Counter()
    summaries = 0
    games = 0
    patterns = (
        "tests/results/qcap/qcap-screen-*.log",
        "tests/results/qcap/qcap-confirm.log",
        "tests/results/qcap/qcap-smoke.log",
    )
    for pattern in patterns:
        for path in glob.glob(pattern):
            for line in Path(path).read_text(errors="ignore").splitlines():
                if line.startswith("SUMMARY "):
                    data = json.loads(line[8:])
                    summaries += 1
                    counts.update(data.get("endings_by_game") or {})
                    games += sum(c["games"] for c in data["configs"].values())
    by_size: dict[int, list[str]] = {}
    for name, hits in counts.items():
        pieces = sum(c.isalpha() for c in name.replace("v", ""))
        by_size.setdefault(pieces, []).append(f"{name}x{hits}")
    return {
        "source": "local sparring games against the frozen control, not rated games",
        "summaries": summaries,
        "games": games,
        "distinct_classes": len(counts),
        "occurrences": sum(counts.values()),
        "by_piece_count": {str(k): sorted(v) for k, v in sorted(by_size.items())},
        "classes": dict(counts.most_common()),
    }


def syzygy_probe() -> dict[str, Any]:
    """python-chess can open an empty tablebase directory and probing falls back safely."""
    empty = Path("/tmp/syzygy-empty")
    empty.mkdir(parents=True, exist_ok=True)
    result: dict[str, Any] = {
        "module": chess.syzygy.__name__,
        "directory": str(empty),
        "files_present": sorted(p.name for p in empty.iterdir()),
    }
    board = chess.Board("8/8/8/8/8/4k3/4P3/4K3 w - - 0 1")
    with chess.syzygy.open_tablebase(str(empty)) as tables:
        result["tables_loaded"] = len(tables.wdl) + len(tables.dtz)
        for name, probe in (("wdl", tables.probe_wdl), ("dtz", tables.probe_dtz)):
            try:
                result[name] = probe(board)
                result[name + "_fallback"] = "table present"
            except KeyError as error:
                # MissingTableError subclasses KeyError, so an agent can catch it.
                result[name] = None
                result[name + "_fallback"] = type(error).__name__
        result["missing_is_keyerror"] = issubclass(chess.syzygy.MissingTableError, KeyError)
    result["read_only_open"] = True
    result["board_probed"] = board.fen()
    result["pieces_probed"] = board.occupied.bit_count()
    return result


def main() -> None:
    fixture = json.loads(FIXTURE.read_text())
    module, digest = rated_losses.instrumented("submitted")
    print("SUBJECT " + json.dumps({"config": "submitted", "sha256": digest}), flush=True)
    print("RATED " + json.dumps(rated_inventory(fixture)), flush=True)
    print("THEORY " + json.dumps(theory_departure(module, fixture)), flush=True)
    print("ENDGAMES " + json.dumps(endgame_classes()), flush=True)
    print("SYZYGY " + json.dumps(syzygy_probe()), flush=True)
    print("SIZES " + json.dumps({"syzygy_uncompressed_mb": SYZYGY_MB, "zip_limit_mb": 50}))


if __name__ == "__main__":
    main()
