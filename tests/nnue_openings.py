"""Build the opening book and the pre-execution game manifest for NNUE data generation.

Two hard rules are enforced here rather than left to the generator:

* No opening may come from rounds 57-81 or touch a RATED_V5 fixture FEN. Those games
  are evaluation-only and seeding a training game from one would contaminate the
  regression fixtures.
* Every game is written into the manifest *before* any game is played, with its id,
  seed, opening, colours, split and output path, so a failed game is rerunnable from
  the manifest without disturbing the rest of the dataset.

Splits are assigned at game level from sha256(game_id), so the assignment does not
depend on generation order, file order or shard order.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import random
import re
from dataclasses import asdict, dataclass

import chess
import chess.pgn

REPO = pathlib.Path(__file__).resolve().parent.parent
ROUND_RE = re.compile(r"round-(\d+)")
EVAL_ONLY_ROUNDS = frozenset(range(57, 82))
OPENING_SEED = 20260909
MANIFEST_SEED = 20260909

# Engine family -> binaries. The holdout is applied per family because three Rustic
# builds are one engine, not three opponents.
FAMILIES: dict[str, tuple[str, ...]] = {
    "rustic": (
        "rustic-alpha-1.5-win64.exe",
        "rustic-alpha-2.4-win64.exe",
        "rustic-alpha-3.0.6-win64.exe",
    ),
    "shallowblue": ("shallowblue_x86-64.exe",),
    "loki": ("Loki3.0.0-x64.exe",),
}
HELD_OUT_FAMILY = "loki"


def fen_key(fen: str) -> str:
    """First four FEN fields: placement, side, castling, en passant."""
    return " ".join(fen.split()[:4])


def split_of(game_id: str) -> str:
    bucket = int(hashlib.sha256(game_id.encode()).hexdigest(), 16) % 100
    if bucket < 70:
        return "train"
    if bucket < 85:
        return "validation"
    return "test"


@dataclass(frozen=True)
class Opening:
    opening_id: str
    fen: str
    source: str
    plies: int


def forbidden_fens() -> set[str]:
    data = json.loads((REPO / "tests" / "rated_v5_positions.json").read_text(encoding="utf-8"))
    return {fen_key(p["fen"]) for p in data["positions"]}


def openings_from_pgns(banned: set[str]) -> list[Opening]:
    """Opening positions from eligible (pre-round-57) stored games only."""
    out: list[Opening] = []
    seen: set[str] = set()
    for path in sorted(REPO.rglob("*.pgn")):
        if ".git" in path.parts:
            continue
        match = ROUND_RE.search(path.name)
        rnd = int(match.group(1)) if match else None
        if rnd is not None and rnd in EVAL_ONLY_ROUNDS:
            continue
        with path.open(encoding="utf-8", errors="replace") as handle:
            while (game := chess.pgn.read_game(handle)) is not None:
                board = game.board()
                moves = list(game.mainline_moves())
                if any(fen_key(board.fen()) in banned for _ in (0,)):
                    continue
                contaminated = False
                probe = board.copy()
                for mv in moves:
                    if fen_key(probe.fen()) in banned:
                        contaminated = True
                        break
                    probe.push(mv)
                if contaminated:
                    continue
                for cut in (8, 12, 16):
                    if len(moves) <= cut + 10:
                        continue
                    b = board.copy()
                    for mv in moves[:cut]:
                        b.push(mv)
                    key = fen_key(b.fen())
                    if key in banned or key in seen or b.is_game_over():
                        continue
                    seen.add(key)
                    out.append(
                        Opening(
                            opening_id=f"pgn-{hashlib.sha256(key.encode()).hexdigest()[:10]}",
                            fen=b.fen(),
                            source=f"{path.relative_to(REPO).as_posix()}@ply{cut}",
                            plies=cut,
                        )
                    )
    return out


def openings_random(count: int, banned: set[str], seen: set[str]) -> list[Opening]:
    """Seeded random-ply openings: balanced, non-terminal, both sides still developed."""
    rng = random.Random(OPENING_SEED)
    out: list[Opening] = []
    attempts = 0
    while len(out) < count and attempts < count * 60:
        attempts += 1
        board = chess.Board()
        for _ in range(rng.choice((6, 8, 10, 12))):
            legal = list(board.legal_moves)
            if not legal:
                break
            board.push(rng.choice(legal))
        if board.is_game_over() or board.is_check():
            continue
        # reject openings already materially decided; the label corpus wants playable
        # positions, not positions where one side is already a piece up by accident
        white = sum(
            len(board.pieces(pt, chess.WHITE)) * v
            for pt, v in ((chess.PAWN, 1), (chess.KNIGHT, 3), (chess.BISHOP, 3),
                          (chess.ROOK, 5), (chess.QUEEN, 9))
        )
        black = sum(
            len(board.pieces(pt, chess.BLACK)) * v
            for pt, v in ((chess.PAWN, 1), (chess.KNIGHT, 3), (chess.BISHOP, 3),
                          (chess.ROOK, 5), (chess.QUEEN, 9))
        )
        if abs(white - black) > 1:
            continue
        key = fen_key(board.fen())
        if key in banned or key in seen:
            continue
        seen.add(key)
        out.append(
            Opening(
                opening_id=f"rnd-{hashlib.sha256(key.encode()).hexdigest()[:10]}",
                fen=board.fen(),
                source=f"seeded-random(seed={OPENING_SEED})",
                plies=board.ply(),
            )
        )
    return out


def build_manifest(
    openings: list[Opening],
    games: int,
    control_clock_ms: int,
    increment_ms: int,
    engine_movetime_ms: int,
) -> list[dict]:
    """One entry per game, paired so each opening is played with both colour assignments."""
    rng = random.Random(MANIFEST_SEED)
    binaries = [b for fam in FAMILIES.values() for b in fam]
    entries: list[dict] = []
    pair_index = 0
    while len(entries) < games:
        opening = openings[pair_index % len(openings)]
        opponent = binaries[pair_index % len(binaries)]
        family = next(f for f, bs in FAMILIES.items() if opponent in bs)
        pair_id = f"p{pair_index:06d}"
        for control_white in (True, False):
            if len(entries) >= games:
                break
            game_id = f"{pair_id}-{'w' if control_white else 'b'}"
            entries.append(
                {
                    "game_id": game_id,
                    "pair_id": pair_id,
                    "seed": rng.randrange(2**31),
                    "opening_id": opening.opening_id,
                    "opening_fen": opening.fen,
                    "opening_source": opening.source,
                    "white": "control" if control_white else opponent,
                    "black": opponent if control_white else "control",
                    "opponent": opponent,
                    "opponent_family": family,
                    "control_is_white": control_white,
                    "control_clock_ms": control_clock_ms,
                    "increment_ms": increment_ms,
                    "engine_movetime_ms": engine_movetime_ms,
                    "split": split_of(pair_id),
                    "held_out_family": family == HELD_OUT_FAMILY,
                    "output": f"tests/results/nnue/games/{game_id}.json",
                }
            )
        pair_index += 1
    return entries


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=3000)
    ap.add_argument("--random-openings", type=int, default=600)
    ap.add_argument("--control-clock-ms", type=int, default=2000)
    ap.add_argument("--increment-ms", type=int, default=500)
    ap.add_argument("--engine-movetime-ms", type=int, default=50)
    ap.add_argument(
        "--out", type=pathlib.Path, default=REPO / "tests" / "results" / "nnue" / "game_manifest.json"
    )
    args = ap.parse_args()

    banned = forbidden_fens()
    from_pgn = openings_from_pgns(banned)
    seen = {fen_key(o.fen) for o in from_pgn}
    from_rng = openings_random(args.random_openings, banned, seen)
    openings = from_pgn + from_rng

    entries = build_manifest(
        openings,
        args.games,
        args.control_clock_ms,
        args.increment_ms,
        args.engine_movetime_ms,
    )

    counts: dict[str, int] = {}
    for e in entries:
        counts[e["split"]] = counts.get(e["split"], 0) + 1
    payload = {
        "seed_openings": OPENING_SEED,
        "seed_manifest": MANIFEST_SEED,
        "openings_total": len(openings),
        "openings_from_pgn": len(from_pgn),
        "openings_random": len(from_rng),
        "banned_fixture_fens": len(banned),
        "eval_only_rounds": [57, 81],
        "held_out_family": HELD_OUT_FAMILY,
        "games": len(entries),
        "split_counts": counts,
        "control_clock_ms": args.control_clock_ms,
        "increment_ms": args.increment_ms,
        "engine_movetime_ms": args.engine_movetime_ms,
        "openings": [asdict(o) for o in openings],
        "entries": entries,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print(f"openings: {len(openings)} ({len(from_pgn)} from eligible PGNs, {len(from_rng)} seeded random)")
    print(f"banned fixture FENs: {len(banned)}")
    print(f"games in manifest: {len(entries)}")
    print(f"split counts: {counts}")
    print(f"held-out family: {HELD_OUT_FAMILY}")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
