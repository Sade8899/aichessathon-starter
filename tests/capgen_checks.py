"""Gates for the direct capture-generation candidate. See CAPGEN.md.

Control  65ec40ceb29a8f6fe14a74ab2ed6ca446164f701e46d2b9d3fe8d5af94655bda  (agent.py)
Candidate 12907f5441c507d2b27a83eaf8b8146aed8811321c157e073bf2ae118bfb7566

Both are loaded from their bytes under private module names, so the repository's
`agent.py` is read and never written.

Modes:

    identity    AST comparison: only `captures_and_promotions` may differ
    equality    the generated list must equal the control's, element for element
    special     hand-authored en passant, castling, promotion and underpromotion cases
    fixed       fixed-depth moves, scores and node counts must be identical
    timing      paired wall-time measurement, counterbalanced order
    imports     three cold imports of the candidate
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.util
import json
import random
import statistics
import sys
import time
import types
from pathlib import Path
from typing import Any

import chess
import chess.pgn

CONTROL_PATH = Path("agent.py")
CONTROL_SHA = "65ec40ceb29a8f6fe14a74ab2ed6ca446164f701e46d2b9d3fe8d5af94655bda"
CANDIDATE_SHA = "12907f5441c507d2b27a83eaf8b8146aed8811321c157e073bf2ae118bfb7566"
CANDIDATE_PATH = Path("tests/capgen_candidate") / CANDIDATE_SHA / "agent.py"
CHANGED = "captures_and_promotions"

# Special-move fixtures. Each is a position where the quiescence move set is easy to get
# wrong: en passant for both colours, a pinned en passant, promotions and underpromotions,
# a capture promotion beside a quiet one, and positions with no captures at all.
SPECIAL = [
    ("ep-white", "rnbqkbnr/ppp1p1pp/8/3pPp2/8/8/PPPP1PPP/RNBQKBNR w KQkq f6 0 3"),
    ("ep-black", "rnbqkbnr/ppp1pppp/8/8/3pP3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 3"),
    ("ep-pinned-horizontal", "8/8/8/K2pP2q/8/8/8/3k4 w - d6 0 1"),
    ("ep-two-capturers", "8/8/8/2k5/3Pp3/8/5K2/8 b - d3 0 1"),
    ("quiet-promotion", "8/3P4/8/8/8/8/3k4/K7 w - - 0 1"),
    ("capture-and-quiet-promotion", "3r4/3P4/8/8/8/8/3k4/K7 w - - 0 1"),
    ("black-promotion-by-capture", "8/8/8/8/8/8/3p4/K1R4k b - - 0 1"),
    ("blocked-seventh-rank", "3r4/3P4/8/8/8/8/3k4/K7 w - - 0 1"),
    ("promotion-and-ep-together", "8/2P5/8/3pP3/8/8/8/K6k w - d6 0 1"),
    ("no-captures-at-all", "8/8/4k3/8/8/3K4/8/8 w - - 0 1"),
    ("castling-available", "r3k2r/pppppppp/8/8/8/8/PPPPPPPP/R3K2R w KQkq - 0 1"),
    ("castling-with-captures", "r3k2r/pp1ppppp/8/2b5/2B5/8/PP1PPPPP/R3K2R w KQkq - 0 1"),
    ("rook-eyes-ep-square", "8/8/8/R2pP2k/8/8/8/K7 w - d6 0 1"),
    ("stalemate-side", "7k/5Q2/6K1/8/8/8/8/8 b - - 0 1"),
]


def load(name: str, text: str) -> types.ModuleType:
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


def control() -> types.ModuleType:
    return load("capgen_control", frozen(CONTROL_PATH, CONTROL_SHA))


def candidate() -> types.ModuleType:
    return load("capgen_candidate", frozen(CANDIDATE_PATH, CANDIDATE_SHA))


# ------------------------------------------------------------------------ identity


def identity() -> dict[str, Any]:
    def definitions(text: str) -> dict[str, str]:
        tree = ast.parse(text)
        out: dict[str, str] = {}
        ordinal = 0
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                for item in node.body:
                    if isinstance(item, ast.FunctionDef):
                        out[f"{node.name}.{item.name}"] = ast.dump(item)
                rest = [i for i in node.body if not isinstance(i, ast.FunctionDef)]
                out[node.name] = ast.dump(ast.Module(body=rest, type_ignores=[]))
            elif isinstance(node, ast.FunctionDef):
                out[node.name] = ast.dump(node)
            else:
                out[f"<statement {ordinal}>"] = ast.dump(node)
                ordinal += 1
        return out

    left = definitions(frozen(CONTROL_PATH, CONTROL_SHA))
    right = definitions(frozen(CANDIDATE_PATH, CANDIDATE_SHA))
    added = sorted(set(right) - set(left))
    removed = sorted(set(left) - set(right))
    differing = sorted(k for k in set(left) & set(right) if left[k] != right[k])
    return {
        "control_sha256": CONTROL_SHA,
        "candidate_sha256": CANDIDATE_SHA,
        "added_definitions": added,
        "removed_definitions": removed,
        "differing_definitions": differing,
        "passed": differing == [CHANGED] and not added and not removed,
    }


# -------------------------------------------------------------------------- corpora


def reachable(limit: int, seed: int) -> list[str]:
    """Non-check positions reached by seeded random play from the rated openings."""
    rng = random.Random(seed)
    starts = [chess.STARTING_FEN]
    games = Path("submission 0609v4/games")
    if games.exists():
        for path in sorted(games.glob("*.pgn")):
            with path.open(encoding="utf-8") as handle:
                game = chess.pgn.read_game(handle)
            if game is not None:
                starts.append(game.board().fen())
    seen: list[str] = []
    while len(seen) < limit:
        board = chess.Board(rng.choice(starts))
        for _ in range(rng.randint(1, 90)):
            moves = list(board.legal_moves)
            if not moves:
                break
            board.push(rng.choice(moves))
            if not board.is_check():
                seen.append(board.fen())
                if len(seen) >= limit:
                    break
    return seen


def special_positions(seed: int) -> list[tuple[str, str]]:
    """The hand-authored fixtures plus seeded play that actually reaches ep/promotion."""
    rows = list(SPECIAL)
    rng = random.Random(seed)
    found = 0
    while found < 400:
        board = chess.Board()
        for _ in range(rng.randint(1, 120)):
            moves = list(board.legal_moves)
            if not moves:
                break
            board.push(rng.choice(moves))
            if board.is_check():
                continue
            interesting = board.ep_square is not None or bool(
                (board.pawns & board.occupied_co[board.turn])
                & (chess.BB_RANK_7 if board.turn else chess.BB_RANK_2)
            )
            if interesting:
                rows.append((f"reached-{found}", board.fen()))
                found += 1
                if found >= 400:
                    break
    return rows


# -------------------------------------------------------------------------- gates


def equality(limit: int, seed: int) -> dict[str, Any]:
    a, b = control(), candidate()
    mismatches: list[dict[str, Any]] = []
    checked = 0
    for fen in reachable(limit, seed):
        board = chess.Board(fen)
        left = a.captures_and_promotions(board)
        right = b.captures_and_promotions(chess.Board(fen))
        checked += 1
        if left != right:
            mismatches.append(
                {
                    "fen": fen,
                    "control": [m.uci() for m in left],
                    "candidate": [m.uci() for m in right],
                }
            )
    return {
        "positions": checked,
        "mismatches": mismatches[:20],
        "mismatch_count": len(mismatches),
        "passed": not mismatches,
    }


def special(seed: int) -> dict[str, Any]:
    a, b = control(), candidate()
    rows: list[dict[str, Any]] = []
    mismatches = 0
    illegal = 0
    for name, fen in special_positions(seed):
        board = chess.Board(fen)
        left = a.captures_and_promotions(board)
        right = b.captures_and_promotions(chess.Board(fen))
        legal = set(chess.Board(fen).legal_moves)
        bad = [m.uci() for m in right if m not in legal]
        illegal += len(bad)
        same = left == right
        mismatches += 0 if same else 1
        if name in dict(SPECIAL) or not same or bad:
            rows.append(
                {
                    "name": name,
                    "fen": fen,
                    "equal": same,
                    "illegal_moves": bad,
                    "moves": [m.uci() for m in right],
                }
            )
    return {
        "cases": len(special_positions(seed)),
        "mismatches": mismatches,
        "illegal_moves": illegal,
        "detail": rows,
        "passed": mismatches == 0 and illegal == 0,
    }


def prepare(module: types.ModuleType, board: chess.Board) -> Any:
    engine = module.Engine()
    engine.stats = {"model_seconds": 0.0, "depth": 0, "nodes": 0}
    engine.deadline = time.perf_counter() + 3600.0
    engine.enter(board)
    engine.pattern = module.recognise(board, module.evaluate(board))
    return engine


def root(module: types.ModuleType, board: chess.Board, depth: int) -> tuple[str, int, int]:
    work = board.copy()
    engine = prepare(module, work)
    best_score = -module.INF
    best_move = next(iter(work.legal_moves))
    for move in engine.order(work, list(work.legal_moves), None, 0):
        work.push(move)
        key = engine.enter(work)
        try:
            value = -engine.search(work, depth - 1, -module.INF, -best_score, 1)
        finally:
            engine.leave(key)
            work.pop()
        if value > best_score:
            best_score, best_move = value, move
    return best_move.uci(), best_score, engine.nodes


def suite(group: str) -> list[dict[str, str]]:
    if group == "rated":
        rows: list[dict[str, str]] = []
        data = json.loads(Path("tests/results/rated_v4/scan_r55.json").read_text())
        rows += [
            {"id": "r55 " + e["move"], "fen": e["fen_before"]}
            for e in data["rounds"]["55"]["moves"]
        ]
        other = json.loads(Path("tests/results/rated_v4/scan_r54_r56.json").read_text())
        for round_id, block in other["rounds"].items():
            rows += [
                {"id": f"r{round_id} " + e["move"], "fen": e["fen_before"]}
                for e in block["moves"]
                if max(v["loss_cp"] for v in e["depths"].values()) >= 50
            ]
        return rows
    if group == "tactics":
        data = json.loads(Path("tests/tactics_corpus.json").read_text())
        items = data["positions"] if isinstance(data, dict) else data
        return [{"id": str(i), "fen": item["fen"]} for i, item in enumerate(items)]
    data = json.loads(Path("tests/quiet_openings.json").read_text())
    return [{"id": p["san"], "fen": p["fen"]} for p in data["positions"]]


def fixed(depth: int, groups: list[str]) -> dict[str, Any]:
    a, b = control(), candidate()
    mismatches: list[dict[str, Any]] = []
    compared = 0
    for group in groups:
        for row in suite(group):
            board = chess.Board(row["fen"])
            left = root(a, board, depth)
            right = root(b, board, depth)
            compared += 1
            if left != right:
                mismatches.append({"id": row["id"], "control": left, "candidate": right})
    return {
        "depth": depth,
        "groups": groups,
        "positions": compared,
        "mismatches": mismatches[:20],
        "mismatch_count": len(mismatches),
        "passed": not mismatches,
    }


def timing(depth: int, group: str, repeats: int) -> dict[str, Any]:
    """Paired, counterbalanced: each repetition flips which side searches first."""
    a, b = control(), candidate()
    rows: list[dict[str, Any]] = []
    for index, row in enumerate(suite(group)):
        board = chess.Board(row["fen"])
        control_best: list[float] = []
        candidate_best: list[float] = []
        for repeat in range(repeats):
            order = [("control", a), ("candidate", b)]
            if (index + repeat) % 2:
                order.reverse()
            for name, module in order:
                started = time.perf_counter()
                root(module, board, depth)
                spent = time.perf_counter() - started
                (control_best if name == "control" else candidate_best).append(spent)
        best_a, best_b = min(control_best), min(candidate_best)
        rows.append(
            {
                "id": row["id"],
                "control_s": round(best_a, 4),
                "candidate_s": round(best_b, 4),
                "gain_pct": round((1 - best_b / best_a) * 100, 2),
            }
        )
        print(
            f"{row['id']:>24} control {best_a:7.3f}s candidate {best_b:7.3f}s "
            f"{rows[-1]['gain_pct']:+6.2f}%",
            file=sys.stderr,
            flush=True,
        )
    gains = [float(r["gain_pct"]) for r in rows]
    total_control = sum(float(r["control_s"]) for r in rows)
    total_candidate = sum(float(r["candidate_s"]) for r in rows)
    return {
        "group": group,
        "depth": depth,
        "repeats": repeats,
        "positions": len(rows),
        "total_control_s": round(total_control, 3),
        "total_candidate_s": round(total_candidate, 3),
        "total_gain_pct": round((1 - total_candidate / total_control) * 100, 2),
        "median_paired_gain_pct": round(statistics.median(gains), 2) if gains else None,
        "mean_paired_gain_pct": round(statistics.fmean(gains), 2) if gains else None,
        "worst_position_gain_pct": round(min(gains), 2) if gains else None,
        "positions_regressed": sum(1 for g in gains if g < 0),
        "detail": rows,
    }


def imports() -> dict[str, Any]:
    """Three cold imports, each in a fresh module namespace, timed."""
    rows = []
    for attempt in range(3):
        started = time.perf_counter()
        module = load(f"capgen_cold_{attempt}", frozen(CANDIDATE_PATH, CANDIDATE_SHA))
        spent = time.perf_counter() - started
        move = module.get_move(chess.STARTING_FEN, 5_000)
        rows.append(
            {
                "attempt": attempt,
                "import_seconds": round(spent, 3),
                "move": move,
                "legal": chess.Move.from_uci(move) in chess.Board().legal_moves,
            }
        )
    return {
        "attempts": rows,
        "worst_import_seconds": max(float(r["import_seconds"]) for r in rows),
        "within_90s_budget": all(float(r["import_seconds"]) < 90.0 for r in rows),
        "passed": all(bool(r["legal"]) for r in rows)
        and all(float(r["import_seconds"]) < 90.0 for r in rows),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "mode", choices=("identity", "equality", "special", "fixed", "timing", "imports")
    )
    parser.add_argument("--depth", type=int, default=4)
    parser.add_argument("--group", default="rated")
    parser.add_argument("--groups", default="rated,tactics,quiet")
    parser.add_argument("--limit", type=int, default=20000)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--seed", type=int, default=20260907)
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    if args.mode == "identity":
        report: dict[str, Any] = identity()
    elif args.mode == "equality":
        report = equality(args.limit, args.seed)
    elif args.mode == "special":
        report = special(args.seed)
    elif args.mode == "fixed":
        report = fixed(args.depth, args.groups.split(","))
    elif args.mode == "timing":
        report = timing(args.depth, args.group, args.repeats)
    else:
        report = imports()

    text = json.dumps(report, indent=2, default=str)
    if args.out:
        target = Path(args.out)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        print(f"wrote {target} ({len(text)} bytes)")
    else:
        print(text)


if __name__ == "__main__":
    main()
