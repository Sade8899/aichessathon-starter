"""Audit the promoted delta-pruning exemptions against the working `agent.py`.

`order_checks.py identity` already proves the *scope* of the change: no definition
added or removed, `Engine.quiesce` the only differing method, identical evaluation,
budget, flags and Numba signatures. This module proves the *behaviour* of the block
that scope permits, by recording every move the pruning test actually skips.

Nothing here modifies `agent.py`. The promoted source is read by hash and a recorder
is spliced into a development copy by exact textual substitution, asserted unique.
`equivalence` proves the spliced copy searches identically to the untouched source,
so what it records describes the real engine rather than itself.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import types
from pathlib import Path
from typing import Any

import chess
import numba_validation as numeric

PROMOTED_SHA = "65ec40ceb29a8f6fe14a74ab2ed6ca446164f701e46d2b9d3fe8d5af94655bda"
PROMOTED = Path("agent.py")
DEPTHS = (2, 3, 4)

RECORDER = """


_D: dict[str, object] = {}


def _reset_delta() -> None:
    _D.clear()
    _D["pruned"] = []
    _D["considered"] = 0
    _D["nodes_in_check"] = 0
    _D["nodes_endgame"] = 0
    _D["ep_seen"] = 0


def _note_delta(board, move, check, pattern, stand, alpha, victim) -> None:
    entry = board.piece_type_at(move.to_square)
    _D["pruned"].append(
        {
            "uci": move.uci(),
            "in_check": bool(check),
            "promotion": move.promotion is not None,
            "is_capture": bool(board.is_capture(move)),
            "occupied_target": entry is not None,
            "en_passant": bool(board.is_en_passant(move)),
            "gives_check": bool(board.gives_check(move)),
            "endgame_pattern": bool(pattern.endgame),
            "victim": int(victim),
            "stand": int(stand),
            "alpha": int(alpha),
            "margin_ok": bool(stand + VALUES[victim] + DELTA_MARGIN <= alpha),
        }
    )
"""

SPLICES: tuple[tuple[str, str], ...] = (
    # Record every move the delta test skips, before the board is touched.
    (
        """                if victim and stand + VALUES[victim] + DELTA_MARGIN <= alpha:
                    continue
""",
        """                if victim and stand + VALUES[victim] + DELTA_MARGIN <= alpha:
                    _note_delta(board, move, check, self.pattern, stand, alpha, victim)
                    continue
""",
    ),
    # Count the moves offered to the loop, and the exempt node classes.
    (
        """        pruning = not check and not self.pattern.endgame
""",
        """        pruning = not check and not self.pattern.endgame
        _D["considered"] += len(moves)
        _D["nodes_in_check"] += int(bool(check))
        _D["nodes_endgame"] += int(bool(self.pattern.endgame))
        for _probe in moves:
            if board.is_en_passant(_probe):
                _D["ep_seen"] += 1
                assert board.piece_type_at(_probe.to_square) is None
                assert board.piece_type_at(
                    _probe.to_square + (-8 if board.turn else 8)
                ) == chess.PAWN
""",
    ),
)


def load(name: str, source: str) -> types.ModuleType:
    module = types.ModuleType(name)
    sys.modules[name] = module
    exec(compile(source, name, "exec"), module.__dict__)
    return module


def promoted_source() -> str:
    raw = PROMOTED.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    assert digest == PROMOTED_SHA, digest
    assert b"\r" not in raw
    return raw.decode()


def recorded_source() -> str:
    source = promoted_source()
    for old, new in SPLICES:
        assert source.count(old) == 1, ("splice not unique", old[:60])
        source = source.replace(old, new)
    return source + RECORDER


def engines() -> tuple[types.ModuleType, types.ModuleType]:
    plain = load("delta_plain", promoted_source())
    marked = load("delta_marked", recorded_source())
    marked._reset_delta()
    return plain, marked


def fixed(module: types.ModuleType, fen: str, depth: int) -> dict[str, Any]:
    """One clock-free search to a fixed completed depth, through the real interface."""
    numeric.fresh(module, depth)
    real = module.time
    vars(module)["time"] = types.SimpleNamespace(perf_counter=lambda: 0.0)
    try:
        move = module.get_move(fen, 120_000)
    finally:
        vars(module)["time"] = real
    engine = module._engine
    assert engine.stats["depth"] == depth
    assert chess.Move.from_uci(move) in chess.Board(fen).legal_moves
    return {
        "move": move,
        "nodes": engine.nodes,
        "scores": {m.uci(): v for m, v in engine.completed_scores.items()},
    }


def equivalence() -> None:
    """The recorder must not change what the engine searches."""
    plain, marked = engines()
    suite = numeric.suite()
    compared = 0
    for depth in DEPTHS:
        for index, fen in enumerate(suite):
            a = fixed(plain, fen, depth)
            b = fixed(marked, fen, depth)
            assert a["move"] == b["move"], (index, depth, a["move"], b["move"])
            assert a["nodes"] == b["nodes"], (index, depth, a["nodes"], b["nodes"])
            assert a["scores"] == b["scores"], (index, depth)
            compared += 1
    print(
        "EQUIVALENCE "
        + json.dumps(
            {"comparisons": compared, "positions": len(suite), "depths": list(DEPTHS)}
        ),
        flush=True,
    )


def invariants() -> None:
    """Every exemption the accepted block claims, measured over real searches."""
    _, marked = engines()
    suite = numeric.suite()
    for depth in DEPTHS:
        for fen in suite:
            fixed(marked, fen, depth)
    pruned: list[dict[str, Any]] = marked._D["pruned"]
    considered = marked._D["considered"]
    facts = {
        "pruned_moves": len(pruned),
        "moves_considered": considered,
        "prune_rate": len(pruned) / max(1, considered),
        "quiescence_nodes_in_check": marked._D["nodes_in_check"],
        "quiescence_nodes_endgame_pattern": marked._D["nodes_endgame"],
        "en_passant_moves_offered": marked._D["ep_seen"],
        "pruned_while_in_check": sum(p["in_check"] for p in pruned),
        "pruned_promotions": sum(p["promotion"] for p in pruned),
        "pruned_noncaptures": sum(not p["is_capture"] for p in pruned),
        "pruned_checking_moves": sum(p["gives_check"] for p in pruned),
        "pruned_quiet_checks": sum(
            p["gives_check"] and not p["is_capture"] for p in pruned
        ),
        "pruned_capturing_checks": sum(
            p["gives_check"] and p["is_capture"] for p in pruned
        ),
        "pruned_in_endgame_pattern": sum(p["endgame_pattern"] for p in pruned),
        "pruned_en_passant": sum(p["en_passant"] for p in pruned),
        "pruned_with_zero_victim": sum(not p["victim"] for p in pruned),
        "margin_test_held": all(p["margin_ok"] for p in pruned),
    }
    # The exemptions the accepted candidate encodes.
    assert facts["pruned_while_in_check"] == 0
    assert facts["pruned_promotions"] == 0
    assert facts["pruned_noncaptures"] == 0
    assert facts["pruned_quiet_checks"] == 0
    assert facts["pruned_in_endgame_pattern"] == 0
    assert facts["pruned_with_zero_victim"] == 0
    assert facts["margin_test_held"]
    # An en passant capture is a capture, so its victim must be a pawn, never zero.
    assert all(p["victim"] == chess.PAWN for p in pruned if p["en_passant"])
    # Every pruned move is a real capture whose victim value is the piece it wins.
    assert all(
        p["occupied_target"] or p["en_passant"] for p in pruned
    ), "a pruned move must land on a piece or be en passant"
    print("INVARIANTS " + json.dumps(facts), flush=True)


def round30() -> None:
    """The Qa5+ refutation must stay reachable: it is a quiet check, never pruned."""
    _, marked = engines()
    blunder = "r1bqk2r/pp2n1pp/1bn2p2/1B1p2B1/3N4/8/PPP2PPP/R2QK1NR w KQkq - 0 10"
    marked._reset_delta()
    result = fixed(marked, blunder, 4)
    pruned: list[dict[str, Any]] = marked._D["pruned"]
    quiet_checks = [p for p in pruned if p["gives_check"] and not p["is_capture"]]
    facts = {
        "fen": blunder,
        "depth": 4,
        "move": result["move"],
        "rejects_g5f4": result["move"] != "g5f4",
        "selects_d1h5": result["move"] == "d1h5",
        "bf4_score_cp": result["scores"].get("g5f4"),
        "nodes": result["nodes"],
        "pruned_moves": len(pruned),
        "pruned_quiet_checks": len(quiet_checks),
        "pruned_a5_moves": sum(p["uci"].endswith("a5") for p in pruned),
        "a5_prunes": [
            {k: p[k] for k in ("uci", "is_capture", "gives_check", "victim")}
            for p in pruned
            if p["uci"].endswith("a5")
        ],
        "pruned_d8a5": sum(p["uci"] == "d8a5" for p in pruned),
        "pruned_d8a5_quiet": sum(
            p["uci"] == "d8a5" and not p["is_capture"] for p in pruned
        ),
    }
    assert facts["selects_d1h5"], facts
    assert facts["pruned_quiet_checks"] == 0, facts
    # The refutation's Qa5+ lands on an empty a5, so it is a quiet check and the
    # non-capture exemption covers it. Elsewhere in the tree White can reach a5
    # with a knight, and Qxa5+ there is an ordinary capture the test may skip.
    assert facts["pruned_d8a5_quiet"] == 0, facts
    assert facts["bf4_score_cp"] == -338, facts
    print("ROUND30 " + json.dumps(facts), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("equivalence", "invariants", "round30", "all"))
    args = parser.parse_args()
    if args.mode in ("equivalence", "all"):
        equivalence()
    if args.mode in ("invariants", "all"):
        invariants()
    if args.mode in ("round30", "all"):
        round30()


if __name__ == "__main__":
    main()
