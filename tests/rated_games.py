"""Map the rated games to the submission that played them, and aggregate what is there.

Two archives exist. They are distinguished physically: the pre-Numba submission
imports in a fraction of a second, the Numba submission spends seconds compiling at
import. The platform reports that import time per game, so `init_s` in the results
CSV is a fingerprint of which generation was live.
"""

from __future__ import annotations

import csv
import hashlib
import json
import statistics
import zipfile
from pathlib import Path
from typing import Any

import chess
import chess.pgn

ROOT = Path("tests/results/rated/platform")
CSV = ROOT / "aichessathon-games.csv"
ARCHIVES = {
    "agent.zip": "020340a3afcab4c41b2f0a1e2ab526a1bdd5e88b79338283d8af87a8b9136490",
    "agent_05_09.zip": "4cf5c8885c49360f6a60328cdd062aa5a45e697e8ee5122d243639c675640dfd",
}
# Measured cold-process import, three runs each, in the calibration image.
IMPORT_SECONDS = {
    "4551f4e4f2fc09fa56801e52a9ad38f0485ebb32d3c84aa4adbb4166fbb598c0": 0.16,
    "59f99079f1db99221683dd3f06391f4fc502c1dae11fb712b08170242649830a": 2.32,
}
GENERATIONS = {
    "4551f4e4f2fc09fa56801e52a9ad38f0485ebb32d3c84aa4adbb4166fbb598c0": "agent_05_09.zip",
    "59f99079f1db99221683dd3f06391f4fc502c1dae11fb712b08170242649830a": "agent.zip",
}
INIT_SPLIT_S = 1.2


def archives() -> dict[str, Any]:
    result = {}
    for name, expected in ARCHIVES.items():
        raw = Path(name).read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        with zipfile.ZipFile(name) as archive:
            members = archive.namelist()
            inner = {m: hashlib.sha256(archive.read(m)).hexdigest() for m in members}
        agent = inner.get("agent.py", "")
        frozen = Path("tests/submitted") / agent / "agent.py"
        result[name] = {
            "archive_sha256": digest,
            "matches_recorded": digest == expected,
            "members": members,
            "agent_sha256": agent,
            "frozen_copy": str(frozen),
            "frozen_matches": frozen.exists()
            and hashlib.sha256(frozen.read_bytes()).hexdigest() == agent,
            "cold_import_s": IMPORT_SECONDS.get(agent),
        }
    return result


def games() -> list[dict[str, Any]]:
    rows = []
    with CSV.open() as handle:
        for row in csv.DictReader(handle):
            init = float(row["init_s"])
            agent = next(
                sha
                for sha, seconds in IMPORT_SECONDS.items()
                if (seconds > INIT_SPLIT_S) == (init > INIT_SPLIT_S)
            )
            number = int(row["round"].split()[-1])
            pgn = next(ROOT.glob(f"aichessathon-round-{number}-*.pgn"), None)
            rows.append(
                {
                    "round": number,
                    "colour": row["colour"],
                    "opponent": row["opponent"],
                    "result": row["result"],
                    "termination": row["termination"],
                    "init_s": init,
                    "moves": int(row["moves"]),
                    "time_used_s": float(row["time_used_s"]),
                    "slowest_s": float(row["slowest_s"]),
                    "average_s": float(row["average_s"]),
                    "clock_left_s": float(row["clock_left_s"]),
                    "finished_at": row["finished_at"],
                    "agent_sha256": agent,
                    "generation": GENERATIONS[agent],
                    "assigned_by": "init_s fingerprint",
                    "move_record": pgn.name if pgn else None,
                }
            )
    return sorted(rows, key=lambda r: r["round"])


def confirm_from_pgn(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Games with a move record can be checked against the fixture reproduction."""
    notes = []
    for row in rows:
        if not row["move_record"]:
            continue
        with (ROOT / row["move_record"]).open() as handle:
            game = chess.pgn.read_game(handle)
        assert game is not None
        board = game.board()
        plies = sum(1 for _ in game.mainline_moves())
        notes.append(
            {
                "round": row["round"],
                "start_fen": board.fen(),
                "plies": plies,
                "result": game.headers.get("Result"),
                "termination": game.headers.get("Termination"),
                "opening": game.headers.get("Opening"),
            }
        )
    return notes


def aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    by_generation = [
        (name, [r for r in rows if r["generation"] == name])
        for name in sorted(set(GENERATIONS.values()))
    ]
    for label, subset in [("all", rows), *by_generation]:
        if not subset:
            continue
        losses = [r for r in subset if r["result"] == "Loss"]
        out[label] = {
            "games": len(subset),
            "rounds": [r["round"] for r in subset],
            "wins": sum(r["result"] == "Win" for r in subset),
            "losses": len(losses),
            "as_white": f"{sum(r['result'] == 'Win' for r in subset if r['colour'] == 'White')}"
            f"/{sum(r['colour'] == 'White' for r in subset)}",
            "as_black": f"{sum(r['result'] == 'Win' for r in subset if r['colour'] == 'Black')}"
            f"/{sum(r['colour'] == 'Black' for r in subset)}",
            "terminations": sorted({r["termination"] for r in subset}),
            "slowest_move_s": sorted({r["slowest_s"] for r in subset}),
            "init_s": sorted({r["init_s"] for r in subset}),
            "median_clock_left_s": statistics.median(r["clock_left_s"] for r in subset),
            "clock_left_range_s": [
                min(r["clock_left_s"] for r in subset),
                max(r["clock_left_s"] for r in subset),
            ],
            "median_clock_left_in_losses_s": (
                statistics.median(r["clock_left_s"] for r in losses) if losses else None
            ),
            "median_average_move_s": statistics.median(r["average_s"] for r in subset),
            "games_with_move_records": [r["round"] for r in subset if r["move_record"]],
            "losses_with_move_records": [r["round"] for r in losses if r["move_record"]],
            "losses_without_move_records": [r["round"] for r in losses if not r["move_record"]],
        }
    return out


def main() -> None:
    rows = games()
    report = {
        "archives": archives(),
        "import_fingerprint": {
            "measured_cold_import_s": IMPORT_SECONDS,
            "split_threshold_s": INIT_SPLIT_S,
            "platform_init_s_observed": sorted({r["init_s"] for r in rows}),
        },
        "games": rows,
        "pgn_confirmation": confirm_from_pgn(rows),
        "aggregate": aggregate(rows),
    }
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
