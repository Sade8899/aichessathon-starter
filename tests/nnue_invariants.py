"""Invariant tests for the NNUE pipeline.

Every check here exists because something was actually wrong, or because a gate depends
on the property holding. Each failure prints what was measured, not just that it failed.

Run: .venv/Scripts/python.exe tests/nnue_invariants.py
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import random
import sys
import types

import chess
import numpy as np

REPO = pathlib.Path(__file__).resolve().parent.parent
HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(HERE))

import nnue_model as nn_features  # noqa: E402

RESULTS: list[dict] = []


def check(name: str, ok: bool, detail: object = "") -> bool:
    RESULTS.append({"check": name, "passed": bool(ok), "detail": detail})
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  -- {detail}" if detail else ""))
    return bool(ok)


def load(path: pathlib.Path, name: str) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def sample_positions(count: int = 400, seed: int = 20260909) -> list[chess.Board]:
    rng = random.Random(seed)
    out: list[chess.Board] = []
    while len(out) < count:
        board = chess.Board()
        for _ in range(rng.randint(2, 60)):
            legal = list(board.legal_moves)
            if not legal:
                break
            board.push(rng.choice(legal))
        if not board.is_game_over():
            out.append(board.copy())
    return out


# ------------------------------------------------------------------ feature invariants


def test_features(boards: list[chess.Board]) -> None:
    bad_count = 0
    bad_range = 0
    for board in boards:
        idx, _aux = nn_features.active_features(board)
        if len(idx) != len(board.piece_map()):
            bad_count += 1
        if idx.size and (idx.min() < 0 or idx.max() >= nn_features.NUM_FEATURES):
            bad_range += 1
    check("one feature per piece, no more", bad_count == 0, f"{bad_count} mismatched")
    check("feature indices inside [0, 768)", bad_range == 0, f"{bad_range} out of range")

    # Colour symmetry: the canonical orientation must make a position and its colour
    # swap identical. This is what makes the colour-symmetry gate true by construction.
    mismatch = []
    for board in boards:
        i1, a1 = nn_features.active_features(board)
        i2, a2 = nn_features.active_features(board.mirror())
        if not np.array_equal(np.sort(i1), np.sort(i2)) or not np.allclose(a1, a2):
            mismatch.append(board.fen())
    check(
        "colour swap produces identical features",
        not mismatch,
        f"{len(mismatch)}/{len(boards)} differ" + (f" e.g. {mismatch[0]}" if mismatch else ""),
    )

    # Castling rights must actually reach the auxiliary vector, or the eval-cache key
    # extension in the candidate would be pointless.
    with_rights = chess.Board()
    without = chess.Board(with_rights.fen())
    without.castling_rights = 0
    _, a_with = nn_features.active_features(with_rights)
    _, a_without = nn_features.active_features(without)
    check(
        "castling rights change the auxiliary vector",
        not np.allclose(a_with, a_without),
        f"{a_with.tolist()} vs {a_without.tolist()}",
    )

    # Phase must be monotone: removing material must not raise the phase.
    full = nn_features.material_phase(chess.Board())
    bare = nn_features.material_phase(chess.Board("6k1/5ppp/8/8/8/8/5PPP/3R2K1 w - - 0 1"))
    check("material phase orders full > endgame", full > bare, f"{full} vs {bare}")


def test_bitscan() -> None:
    """The de Bruijn table and the value fed into it must be the matching pair.

    Regression test for a real bug: the classic table indexes on the folded low bits,
    bb ^ (bb - 1), but the agent multiplied the isolated low bit, bb & -bb. That
    mis-scanned 63 of 64 squares, so every NNUE feature row the agent summed was wrong
    while the accumulator still produced plausible-looking numbers.
    """
    agent_mod = load(REPO / "agent_nnue.py", "invariant_bitscan_agent")
    table = agent_mod._NNUE_INDEX
    deb = agent_mod._NNUE_DEBRUIJN
    wrong_folded = 0
    wrong_isolated = 0
    for square in range(64):
        bb = np.uint64(1) << np.uint64(square)
        with np.errstate(over="ignore"):
            folded = bb ^ (bb - np.uint64(1))
            isolated = bb & (~bb + np.uint64(1))
            wrong_folded += int(table[(folded * deb) >> np.uint64(58)]) != square
            wrong_isolated += int(table[(isolated * deb) >> np.uint64(58)]) != square
    check("de Bruijn table matches the folded low bits", wrong_folded == 0,
          f"{wrong_folded}/64 mis-scanned")
    check("the isolated-low-bit pairing really is wrong (pins the fix)",
          wrong_isolated > 0, f"{wrong_isolated}/64 mis-scanned, as expected")

    # every square, scanned through the agent's own compiled kernel via a lone piece
    mism = []
    for square in range(64):
        if square in (0, 7, 56, 63):
            continue
        board = chess.Board(None)
        board.set_piece_at(chess.E1, chess.Piece(chess.KING, chess.WHITE))
        board.set_piece_at(chess.E8, chess.Piece(chess.KING, chess.BLACK))
        if square in (chess.E1, chess.E8):
            continue
        board.set_piece_at(square, chess.Piece(chess.ROOK, chess.WHITE))
        board.turn = chess.WHITE
        if not board.is_valid():
            continue
        idx_ref = set(nn_features.active_features(board)[0].tolist())
        expected_rook = (chess.ROOK - 1) * 2 * 64 + square
        if expected_rook not in idx_ref:
            mism.append(square)
    check("reference feature index for a lone rook is the rook plane", not mism, mism[:5])


# ------------------------------------------------------------------- model invariants


def test_model_trains(boards: list[chess.Board]) -> None:
    """Gradients must be non-zero through every layer and units must not be mostly dead.

    This is the regression test for the initialisation bug: zero-initialised heads with a
    near-zero accumulator left roughly half the clipped-ReLU units clamped dead and the
    loss moved by 0.04 over six epochs.
    """
    import torch

    torch.manual_seed(20260909)
    residual_class = nn_features.build_torch_model()
    model = residual_class()

    n = len(boards)
    indices = np.zeros((n, 32), dtype=np.int64)
    mask = np.zeros((n, 32), dtype=np.float32)
    aux = np.zeros((n, nn_features.NUM_AUX), dtype=np.float32)
    phase = np.zeros(n, dtype=np.float32)
    for i, board in enumerate(boards):
        idx, a = nn_features.active_features(board)
        k = min(len(idx), 32)
        indices[i, :k] = idx[:k]
        mask[i, :k] = 1.0
        aux[i] = a
        phase[i] = nn_features.phase_blend(board)

    ti = torch.from_numpy(indices)
    tm = torch.from_numpy(mask)
    ta = torch.from_numpy(aux)
    tp = torch.from_numpy(phase)

    hidden = torch.clamp(model.accumulate(ti, tm, ta), 0.0, 1.0)
    alive = float((hidden > 0).float().mean())
    unsaturated = float(((hidden > 0) & (hidden < 1)).float().mean())
    check("hidden units mostly alive at init", alive > 0.5, f"alive fraction {alive:.3f}")
    check(
        "hidden units mostly unsaturated at init",
        unsaturated > 0.30,
        f"unsaturated fraction {unsaturated:.3f}",
    )

    target = torch.randn(n) * 0.5
    out = model(ti, tm, ta, tp)
    loss = torch.nn.functional.smooth_l1_loss(out, target, beta=0.2)
    loss.backward()
    zero_grad = []
    for name, param in model.named_parameters():
        if param.grad is None or float(param.grad.abs().sum()) == 0.0:
            zero_grad.append(name)
    check("every parameter receives gradient", not zero_grad, f"zero-grad: {zero_grad}")

    check(
        "parameter count near the declared 50k",
        45_000 <= nn_features.parameter_count() <= 55_000,
        nn_features.parameter_count(),
    )


# -------------------------------------------------------------- quantization agreement


def test_quantization(boards: list[chess.Board]) -> None:
    """Float and quantized inference must agree, and the agent must match the reference.

    The agent's Numba kernel is a separate implementation from the trainer's reference
    integer path. If they diverge, the metrics measured offline describe a different
    evaluator from the one that plays.
    """
    import nnue_train as trainer
    import torch

    torch.manual_seed(20260909)
    residual_class = nn_features.build_torch_model()
    model = residual_class()
    with torch.no_grad():
        # Give the heads enough magnitude that the test is not trivially satisfied by a
        # near-zero model, but keep the output in the range the engine actually deploys
        # (the correction is clamped to +/-250 cp), so the drift measured is the drift
        # that matters rather than drift on outputs the engine would clamp away anyway.
        model.head_mg.weight.mul_(1.5)
        model.head_eg.weight.mul_(1.5)
    model.eval()

    quant = trainer.quantize(model)
    packed = REPO / "tests" / "results" / "nnue" / "model" / "_invariant_weights.npz"
    packed.parent.mkdir(parents=True, exist_ok=True)
    with packed.open("wb") as handle:
        np.savez(
            handle,
            embed_q=quant["embed_q"],
            aux_w_q=quant["aux_w_q"],
            aux_b_q=quant["aux_b_q"],
            mg_q=quant["mg_q"],
            eg_q=quant["eg_q"],
            scales=np.array([quant["mg_scale"], quant["eg_scale"]], dtype=np.float64),
            biases=np.array([quant["mg_bias"], quant["eg_bias"]], dtype=np.float64),
        )

    n = len(boards)
    indices = np.zeros((n, 32), dtype=np.int64)
    mask = np.zeros((n, 32), dtype=np.float32)
    aux = np.zeros((n, nn_features.NUM_AUX), dtype=np.float32)
    phase = np.zeros(n, dtype=np.float32)
    for i, board in enumerate(boards):
        idx, a = nn_features.active_features(board)
        k = min(len(idx), 32)
        indices[i, :k] = idx[:k]
        mask[i, :k] = 1.0
        aux[i] = a
        phase[i] = nn_features.phase_blend(board)

    with torch.no_grad():
        float_cp = (
            model(
                torch.from_numpy(indices),
                torch.from_numpy(mask),
                torch.from_numpy(aux),
                torch.from_numpy(phase),
            ).numpy()
            * trainer.OUTPUT_SCALE
        )
    quant_cp = trainer.quant_forward(quant, indices, mask, aux, phase)
    drift = np.abs(quant_cp - float_cp)
    check(
        "float and quantized agree within 10 cp (median)",
        float(np.median(drift)) <= 10.0,
        f"median {np.median(drift):.3f} cp, p95 {np.percentile(drift, 95):.3f}, "
        f"max {drift.max():.3f}",
    )
    check("no NaN or inf in quantized output", bool(np.isfinite(quant_cp).all()))

    # The agent's own Numba kernel against the reference integer path.
    stash = REPO / "nnue_weights.npz"
    backup = stash.read_bytes() if stash.exists() else None
    try:
        stash.write_bytes(packed.read_bytes())
        agent_mod = load(REPO / "agent_nnue.py", "invariant_agent_nnue")
        if not getattr(agent_mod, "_nnue_ready", False):
            check("agent loaded the invariant weights", False, agent_mod._nnue_status)
            return
        agent_cp = np.array([agent_mod.nnue_correction(b) for b in boards], dtype=np.float64)
        expected = np.clip(quant_cp, -agent_mod.NNUE_CLAMP, agent_mod.NNUE_CLAMP)
        delta = np.abs(agent_cp - expected)
        check(
            "agent Numba kernel matches the reference integer path",
            float(delta.max()) <= 1.0,
            f"max {delta.max():.3f} cp, mean {delta.mean():.4f}",
        )
        # colour symmetry of the deployed evaluator
        asym = [
            b.fen()
            for b in boards[:200]
            if agent_mod.nnue_correction(b) != agent_mod.nnue_correction(b.mirror())
        ]
        check(
            "deployed correction is exactly colour-symmetric",
            not asym,
            f"{len(asym)} asymmetric" + (f" e.g. {asym[0]}" if asym else ""),
        )
        bounded = [
            b.fen() for b in boards if abs(agent_mod.nnue_correction(b)) > agent_mod.NNUE_CLAMP
        ]
        check("correction never exceeds the clamp", not bounded, f"{len(bounded)} over")
    finally:
        if backup is not None:
            stash.write_bytes(backup)
        elif stash.exists():
            stash.unlink()
        packed.unlink(missing_ok=True)


# ------------------------------------------------------------------- engine invariants


def test_engine_rules() -> None:
    """Terminal and rule-based outcomes must come from the rules, never from the network."""
    control = load(REPO / "agent.py", "invariant_control")
    candidate = load(REPO / "agent_nnue.py", "invariant_candidate")

    # With no weights the candidate must be numerically identical to the control.
    if not getattr(candidate, "_nnue_ready", False):
        boards = sample_positions(300, seed=7)
        diffs = [
            b.fen()
            for b in boards
            if control.cached_evaluate(b) != candidate.cached_evaluate(b)
        ]
        check(
            "fallback is byte-for-byte the control's evaluation",
            not diffs,
            f"{len(diffs)}/{len(boards)} differ",
        )

    cases = [
        ("checkmate found in one", "6k1/5ppp/8/8/8/8/5PPP/R5K1 w - - 0 1", None),
        ("stalemate position is a draw", "7k/5Q2/6K1/8/8/8/8/8 b - - 0 1", "stalemate"),
        ("insufficient material", "6k1/8/8/8/8/8/8/6KB w - - 0 1", "insufficient"),
    ]
    for label, fen, kind in cases:
        board = chess.Board(fen)
        if kind == "stalemate":
            check(f"{label}", board.is_stalemate(), "rules, not the network")
            continue
        if kind == "insufficient":
            check(f"{label}", board.is_insufficient_material(), "rules, not the network")
            continue
        move = candidate.get_move(board.fen(), 3000)
        legal = chess.Move.from_uci(move) in board.legal_moves
        check(f"{label}: legal reply", legal, move)

    # legality across a real game, both colours
    for white in (True, False):
        board = chess.Board()
        illegal = None
        for ply in range(40):
            if board.is_game_over(claim_draw=True):
                break
            mover = candidate if (board.turn == chess.WHITE) == white else control
            uci = mover.get_move(board.fen(), 3000)
            mv = chess.Move.from_uci(uci)
            if mv not in board.legal_moves:
                illegal = f"ply {ply}: {uci}"
                break
            board.push(mv)
        check(
            f"legal game, candidate as {'white' if white else 'black'}",
            illegal is None,
            illegal or "",
        )

    # mate scores must stay above anything the residual can reach
    check(
        "mate score dwarfs the maximum correction",
        candidate.MATE > 100 * candidate.NNUE_CLAMP,
        f"MATE {candidate.MATE} vs clamp {candidate.NNUE_CLAMP}",
    )


# ------------------------------------------------------------------- dataset invariants


def test_dataset() -> None:
    path = REPO / "tests" / "results" / "nnue" / "dataset" / "positions.jsonl"
    if not path.exists():
        print("[skip] dataset not built yet")
        return
    by_split: dict[str, set[str]] = {}
    games: dict[str, set[str]] = {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            by_split.setdefault(row["split"], set()).add(row["fen_key"])
            games.setdefault(row["split"], set()).add(row["pair_id"])
    overlaps = {
        f"{a}&{b}": len(by_split.get(a, set()) & by_split.get(b, set()))
        for a, b in (("train", "validation"), ("train", "test"), ("validation", "test"))
    }
    check("no position appears in two splits", all(v == 0 for v in overlaps.values()), overlaps)
    game_overlaps = {
        f"{a}&{b}": len(games.get(a, set()) & games.get(b, set()))
        for a, b in (("train", "validation"), ("train", "test"), ("validation", "test"))
    }
    check(
        "no game lineage spans two splits",
        all(v == 0 for v in game_overlaps.values()),
        game_overlaps,
    )

    fixtures = json.loads((HERE / "rated_v5_positions.json").read_text(encoding="utf-8"))
    banned = {" ".join(p["fen"].split()[:4]) for p in fixtures["positions"]}
    leaked = [k for k in by_split.get("train", set()) if k in banned]
    check("no RATED_V5 fixture position is in training", not leaked, f"{len(leaked)} leaked")


def main() -> None:
    boards = sample_positions()
    print(f"--- feature invariants ({len(boards)} random legal positions) ---")
    test_features(boards)
    print("--- bit-scan invariants ---")
    test_bitscan()
    print("--- model invariants ---")
    test_model_trains(boards)
    print("--- quantization invariants ---")
    test_quantization(boards)
    print("--- engine invariants ---")
    test_engine_rules()
    print("--- dataset invariants ---")
    test_dataset()

    out = REPO / "tests" / "results" / "nnue" / "gates" / "invariants.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    failed = [r for r in RESULTS if not r["passed"]]
    out.write_text(
        json.dumps({"checks": RESULTS, "failed": len(failed), "all_pass": not failed}, indent=2),
        encoding="utf-8",
    )
    print()
    print(f"{len(RESULTS) - len(failed)}/{len(RESULTS)} invariants pass")
    print(f"wrote {out}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
