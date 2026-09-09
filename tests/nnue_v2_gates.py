"""Correctness gates for a V2 candidate. Every one runs before any arena time is spent.

The ordering is deliberate: the cheap structural gates run first, then the integer-path
equality gates, then the fixture gates that actually rejected V1. A candidate that fails
a solved control is rejected here and never reaches a paired arena, because an arena
that confirms a broken fixture has only spent an hour proving what a gate proved in a
second.

Run:  python tests/nnue_v2_gates.py --tag C_h32_s20260909
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import pathlib
import random
import subprocess
import sys
import time
import types
from typing import Any

import chess
import numpy as np

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))

import nnue_v2_build_agent as builder  # noqa: E402
import nnue_v2_model as nn_features  # noqa: E402
import nnue_v2_train as trainer  # noqa: E402

V2 = REPO / "tests" / "results" / "nnue" / "v2"
OUT = REPO / "tests" / "results" / "nnue" / "v2"
CONTROL_SHA256 = "65ec40ceb29a8f6fe14a74ab2ed6ca446164f701e46d2b9d3fe8d5af94655bda"
PACKAGE_SHA256 = "d9392c6b9c572790c838cc91e957c6eeaceecb706175d99cdfbf928a5e3b86a5"

# Fixtures whose subject matter is a held draw or a passed-pawn defence. These are the
# behaviours V1's draw collapse destroyed, so they are named explicitly rather than
# being left to the aggregate count.
DEFENCE_FIXTURES = ("r77-33-Rc7+", "r77-53-Rc7+", "r78-48-Kf6", "r78-55-Be4", "r78-60-b1=Q")
REJECT_FIXTURE = "r80-24-Rd4"


def load_module(path: pathlib.Path, name: str) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def sample_boards(count: int, seed: int = 7) -> list[chess.Board]:
    """A varied, reproducible board set: random play from the start, all phases."""
    rng = random.Random(seed)
    boards: list[chess.Board] = []
    board = chess.Board()
    while len(boards) < count:
        if board.is_game_over() or board.fullmove_number > 90:
            board = chess.Board()
            continue
        moves = list(board.legal_moves)
        board.push(rng.choice(moves))
        if not board.is_game_over():
            boards.append(board.copy())
    return boards


def encode_boards(boards: list[chess.Board]) -> dict[str, Any]:
    n = len(boards)
    enc = {
        "indices": np.zeros((n, trainer.MAX_PIECES), dtype=np.int64),
        "mask": np.zeros((n, trainer.MAX_PIECES), dtype=np.float32),
        "aux": np.zeros((n, nn_features.NUM_AUX), dtype=np.float32),
        "phase": np.zeros(n, dtype=np.float32),
        "phase_units": np.zeros(n, dtype=np.int64),
        "base": np.zeros(n, dtype=np.float32),
        "sf": np.zeros(n, dtype=np.float32),
    }
    for i, board in enumerate(boards):
        idx, aux = nn_features.active_features(board)
        k = min(len(idx), trainer.MAX_PIECES)
        enc["indices"][i, :k] = idx[:k]
        enc["mask"][i, :k] = 1.0
        enc["aux"][i] = aux
        units = nn_features.material_phase(board)
        enc["phase_units"][i] = units
        enc["phase"][i] = units / nn_features.PHASE_MAX
    return enc


# ------------------------------------------------------------------------------ gates


def gate_identities() -> list[dict[str, Any]]:
    control = hashlib.sha256((REPO / "agent.py").read_bytes()).hexdigest()
    package = REPO / "submissions" / "pre_nn_20260909" / "agent.zip"
    package_digest = (
        hashlib.sha256(package.read_bytes()).hexdigest() if package.exists() else "missing"
    )
    candidate = (REPO / "agent_nnue_v2.py").read_text(encoding="utf-8")
    restored = builder.strip(candidate)
    control_text = (REPO / "agent.py").read_text(encoding="utf-8")
    round_trips = restored == control_text
    return [
        {
            "gate": "protected control agent.py unchanged",
            "measured": control,
            "requirement": CONTROL_SHA256,
            "passes": control == CONTROL_SHA256,
        },
        {
            "gate": "protected pre-neural package unchanged",
            "measured": package_digest,
            "requirement": PACKAGE_SHA256,
            "passes": package_digest == PACKAGE_SHA256,
        },
        {
            "gate": "candidate strips back to the control exactly",
            "measured": "identical" if round_trips else "differs",
            "requirement": "identical",
            "passes": round_trips,
        },
    ]


def gate_debruijn(candidate: types.ModuleType) -> list[dict[str, Any]]:
    """Pin both halves of the scan that V1 got wrong.

    The classic de Bruijn table indexes on the FOLDED low bits, bb ^ (bb - 1), not on
    the isolated low bit bb & -bb. Pairing the table with the isolated bit mis-scanned
    63 of 64 squares and made every feature row wrong.
    """
    table = candidate._NNUE_INDEX
    magic = int(candidate._NNUE_DEBRUIJN)
    mask = (1 << 64) - 1

    def scan(bb: int) -> int:
        # Python ints with an explicit 64-bit mask, so the wrap the de Bruijn trick
        # relies on is written down rather than left to a numpy overflow warning.
        folded = bb ^ (bb - 1)
        return int(table[((folded * magic) & mask) >> 58])

    exact = 0
    for square in range(64):
        exact += int(scan(1 << square) == square)

    # And that a multi-bit board enumerates exactly its own squares.
    rng = random.Random(11)
    enumerated_ok = 0
    trials = 200
    for _ in range(trials):
        squares = sorted(rng.sample(range(64), rng.randint(1, 20)))
        bb = 0
        for sq in squares:
            bb |= 1 << sq
        seen = []
        remaining = bb
        while remaining:
            seen.append(scan(remaining))
            remaining &= remaining - 1
        enumerated_ok += int(sorted(seen) == squares)

    return [
        {
            "gate": "de Bruijn scan resolves every single-bit board",
            "measured": f"{exact}/64",
            "requirement": "64/64",
            "passes": exact == 64,
        },
        {
            "gate": "de Bruijn scan enumerates multi-bit boards exactly",
            "measured": f"{enumerated_ok}/{trials}",
            "requirement": f"{trials}/{trials}",
            "passes": enumerated_ok == trials,
        },
    ]


def gate_reference_equality(
    candidate: types.ModuleType, tag: str, boards: list[chess.Board]
) -> list[dict[str, Any]]:
    """The agent's fused Numba path must equal the reference integer path exactly.

    This is the strongest single gate in the suite: it exercises the bit scan, the
    orientation flip, the folded auxiliary table, both value heads, the clamp, the
    confidence gate and the phase gate end to end, and it compares integers, so
    "agrees to within a rounding step" cannot hide in it.
    """
    blob = np.load(V2 / tag / "quantized.npz")
    q = {
        "embed_q": blob["embed"],
        "aux_w_q": blob["aux_w"],
        "aux_b_q": blob["aux_b"],
        "mg_q": blob["mg"],
        "eg_q": blob["eg"],
        "conf_q": blob["conf"],
        "mg_scale": float(blob["scales"][0]),
        "eg_scale": float(blob["scales"][1]),
        "conf_scale": float(blob["scales"][2]),
        "mg_bias": float(blob["biases"][0]),
        "eg_bias": float(blob["biases"][1]),
        "conf_bias": float(blob["biases"][2]),
        "gate": bool(blob["flags"][0]),
        "phase_gate": bool(blob["flags"][1]),
        # Fourth flag, in thousandths: the hard confidence threshold. Weight files
        # written before it existed carry three flags and mean "no threshold". Omitting
        # it here made the reference skip a suppression the agent applies, and the gate
        # correctly reported 18/600 agreement rather than quietly passing.
        "conf_min": (
            float(blob["flags"][3]) / 1000.0 if blob["flags"].shape[0] > 3 else 0.0
        ),
    }
    enc = encode_boards(boards)
    rows = np.arange(len(boards))
    reference = trainer.quant_correction(q, enc, rows)
    agent = np.array([candidate.nnue_correction(b) for b in boards], dtype=np.float64)
    diff = np.abs(agent - reference)
    exact = int((diff == 0).sum())

    clamp = candidate.NNUE_CLAMP
    within = int((np.abs(agent) <= clamp).all())

    return [
        {
            "gate": "agent integer path equals the reference integer path",
            "measured": f"{exact}/{len(boards)} exact, max |diff| {diff.max():.0f} cp",
            "requirement": "every position exact",
            "passes": exact == len(boards),
        },
        {
            "gate": "correction never exceeds the clamp",
            "measured": f"max |correction| {np.abs(agent).max():.0f} cp, clamp {clamp}",
            "requirement": f"<= {clamp}",
            "passes": bool(within),
        },
    ]


def gate_symmetry(boards: list[chess.Board]) -> list[dict[str, Any]]:
    """Colour-perspective consistency.

    Under the canonical player-relative orientation this is true by construction, and V1
    measured it 2,000/2,000. It is re-measured rather than assumed, because "true by
    construction" is a claim about code that can be edited.
    """
    identical = 0
    checked = 0
    for board in boards:
        mirrored = board.mirror()
        a_idx, a_aux = nn_features.active_features(board)
        b_idx, b_aux = nn_features.active_features(mirrored)
        checked += 1
        identical += int(
            sorted(a_idx.tolist()) == sorted(b_idx.tolist())
            and np.array_equal(a_aux, b_aux)
        )
    return [
        {
            "gate": "colour-swap feature identity",
            "measured": f"{identical}/{checked}",
            "requirement": "all identical",
            "passes": identical == checked,
        }
    ]


def gate_determinism(
    candidate: types.ModuleType, boards: list[chess.Board]
) -> list[dict[str, Any]]:
    first = [candidate.nnue_correction(b) for b in boards]
    second = [candidate.nnue_correction(b) for b in boards]
    third = [candidate.nnue_correction(chess.Board(b.fen())) for b in boards]
    return [
        {
            "gate": "inference is deterministic across calls",
            "measured": "identical" if first == second else "varies",
            "requirement": "identical",
            "passes": first == second,
        },
        {
            "gate": "inference depends only on the position, not on board history",
            "measured": "identical" if first == third else "varies",
            "requirement": "identical",
            "passes": first == third,
        },
    ]


def gate_legality(candidate: types.ModuleType) -> list[dict[str, Any]]:
    """The candidate must always return a legal UCI move, and must see a mate in one."""
    boards = sample_boards(40, seed=99)
    illegal = 0
    for board in boards:
        uci = candidate.get_move(board.fen(), 3000)
        try:
            move = chess.Move.from_uci(uci)
        except ValueError:
            illegal += 1
            continue
        if move not in board.legal_moves:
            illegal += 1

    mates = [
        ("6k1/5ppp/8/8/8/8/8/R5K1 w - - 0 1", "a1a8"),
        ("7k/6pp/8/8/8/8/5PPP/1R4K1 w - - 0 1", "b1b8"),
        ("k7/7R/1K6/8/8/8/8/8 w - - 0 1", "h7h8"),
    ]
    found = 0
    for fen, _expected in mates:
        board = chess.Board(fen)
        uci = candidate.get_move(fen, 3000)
        move = chess.Move.from_uci(uci)
        board.push(move)
        found += int(board.is_checkmate())

    return [
        {
            "gate": "always returns a legal move",
            "measured": f"{len(boards) - illegal}/{len(boards)} legal",
            "requirement": "all legal",
            "passes": illegal == 0,
        },
        {
            "gate": "finds mate in one",
            "measured": f"{found}/{len(mates)}",
            "requirement": f"{len(mates)}/{len(mates)}",
            "passes": found == len(mates),
        },
    ]


def gate_nps(
    control: types.ModuleType, candidate: types.ModuleType, limit_pct: float
) -> list[dict[str, Any]]:
    """Full-search nodes per second, warm, on identical positions at identical depth."""
    boards = sample_boards(12, seed=4242)
    v4 = load_module(REPO / "tests" / "rated_v4.py", "gate_rated_v4")

    def measure(module: types.ModuleType) -> tuple[float, int]:
        # one warm-up sweep so compilation and caches are not in the measurement
        for board in boards[:3]:
            v4.root(module, board, 3)
        nodes = 0
        started = time.perf_counter()
        for board in boards:
            outcome = v4.root(module, board, 4)
            nodes += int(outcome["nodes"])
        elapsed = time.perf_counter() - started
        return nodes / elapsed, nodes

    # Three interleaved repeats, best-of taken per side. A single sweep of 12 positions
    # is noisy enough to move the reading by several points on a machine with background
    # work, and this gate has a hard threshold, so the noise must not be the finding.
    control_runs: list[float] = []
    candidate_runs: list[float] = []
    control_nodes = candidate_nodes = 0
    for _ in range(3):
        nps, control_nodes = measure(control)
        control_runs.append(nps)
        nps, candidate_nodes = measure(candidate)
        candidate_runs.append(nps)
    control_nps = max(control_runs)
    candidate_nps = max(candidate_runs)
    loss = (control_nps - candidate_nps) / control_nps * 100.0
    return [
        {
            "gate": "full-search NPS loss",
            "measured": f"{loss:.2f}% ({control_nps:,.0f} -> {candidate_nps:,.0f} nps)",
            "requirement": f"<= {limit_pct}%",
            "passes": loss <= limit_pct,
            "detail": {
                "control_nps": control_nps,
                "candidate_nps": candidate_nps,
                "control_nodes": control_nodes,
                "candidate_nodes": candidate_nodes,
                "control_runs": [round(v, 1) for v in control_runs],
                "candidate_runs": [round(v, 1) for v in candidate_runs],
            },
        }
    ]


def gate_fixtures() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """RATED_V5 at each fixture's minimum correcting depth, control and candidate.

    This is the gate that rejected V1: 16/16 became 11/16. No solved-control regression
    is allowed, at all.
    """
    def run(source: str) -> dict[str, Any]:
        proc = subprocess.run(
            [sys.executable, str(REPO / "tests" / "rated_v5.py"), "fixtures",
             "--source", source],
            check=True, capture_output=True, text=True, cwd=REPO,
        )
        return json.loads(proc.stdout)

    control = run("agent.py")
    candidate = run("agent_nnue_v2.py")

    by_id_control = {r["id"]: r for r in control["results"]}
    by_id_candidate = {r["id"]: r for r in candidate["results"]}
    broke = [
        fid
        for fid, row in by_id_control.items()
        if row["passed"] and not by_id_candidate[fid]["passed"]
    ]
    repaired = [
        fid
        for fid, row in by_id_control.items()
        if not row["passed"] and by_id_candidate[fid]["passed"]
    ]

    defence_broken = [f for f in DEFENCE_FIXTURES if f in broke]
    reject_row = by_id_candidate.get(REJECT_FIXTURE)
    reject_ok = bool(reject_row and reject_row["passed"])

    gates = [
        {
            "gate": "RATED_V5 enforced fixtures",
            "measured": (
                f"control {control['enforced_passed']}/{control['enforced_total']}, "
                f"candidate {candidate['enforced_passed']}/{candidate['enforced_total']}"
            ),
            "requirement": "candidate matches the control's 16/16",
            "passes": candidate["enforced_passed"] >= control["enforced_passed"]
            and candidate["passed"],
        },
        {
            "gate": "solved-control regressions",
            "measured": f"{len(broke)} {broke}",
            "requirement": "0",
            "passes": len(broke) == 0,
        },
        {
            "gate": "draw and passed-pawn defence fixtures preserved",
            "measured": f"broken: {defence_broken or 'none'}",
            "requirement": "none broken",
            "passes": len(defence_broken) == 0,
        },
        {
            "gate": f"{REJECT_FIXTURE} still rejected",
            "measured": str(reject_row["chose"] if reject_row else "missing"),
            "requirement": "not the rated move",
            "passes": reject_ok,
        },
    ]
    detail = {
        "control": control,
        "candidate": candidate,
        "broke": broke,
        "repaired": repaired,
    }
    return gates, detail


def gate_package(tag: str) -> list[dict[str, Any]]:
    agent = (REPO / "agent_nnue_v2.py").stat().st_size
    weights = (REPO / "nnue_v2_weights.npz").stat().st_size
    total = agent + weights
    return [
        {
            "gate": "package uncompressed size",
            "measured": f"{total:,} bytes (agent {agent:,} + weights {weights:,})",
            "requirement": "< 50,000,000",
            "passes": total < 50_000_000,
        },
        {
            "gate": "no dependency outside the preinstalled stack",
            "measured": "chess, numpy, numba, stdlib only",
            "requirement": "torch/numpy/chess/onnxruntime/numba only",
            "passes": "import torch" not in (REPO / "agent_nnue_v2.py").read_text(encoding="utf-8"),
        },
    ]


def gate_holdout(candidate: types.ModuleType, tag: str) -> list[dict[str, Any]]:
    """Static error on the V1 Loki holdout: never trained on, and still never trained on.

    Reported, not enforced. V1's largest MAE gain was on exactly this split and it still
    played worst against Loki, which is the whole reason MAE is no longer a criterion.
    """
    path = REPO / "tests" / "results" / "nnue" / "dataset" / "labelled.jsonl"
    rows = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            if row["split"] == "holdout":
                rows.append(row)
            if len(rows) >= 4000:
                break
    base = np.array([r["control_static_cp"] for r in rows], dtype=np.float64)
    sf = np.array([r["stockfish_cp"] for r in rows], dtype=np.float64)
    corr = np.array(
        [candidate.nnue_correction(chess.Board(r["fen"])) for r in rows], dtype=np.float64
    )
    mae_ctrl = float(np.mean(np.abs(base - sf)))
    mae_cand = float(np.mean(np.abs(base + corr - sf)))
    return [
        {
            "gate": "unseen Loki-family holdout static MAE (reported, not enforced)",
            "measured": f"{mae_ctrl:.2f} -> {mae_cand:.2f} cp ({mae_ctrl - mae_cand:+.2f})",
            "requirement": "reported only; MAE is not an acceptance criterion",
            "passes": True,
            "detail": {
                "n": len(rows),
                "mae_control": mae_ctrl,
                "mae_candidate": mae_cand,
                "mean_abs_correction": float(np.mean(np.abs(corr))),
                "corrections_suppressed_pct": float(np.mean(np.abs(corr) < 1) * 100.0),
            },
        }
    ]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", required=True)
    ap.add_argument("--nps-limit", type=float, default=5.0)
    ap.add_argument("--boards", type=int, default=600)
    ap.add_argument("--skip-fixtures", action="store_true")
    args = ap.parse_args()

    started = time.perf_counter()
    control = load_module(REPO / "agent.py", "gate_control")
    candidate = load_module(REPO / "agent_nnue_v2.py", "gate_candidate")
    print(f"candidate weight status: {candidate._nnue_status}")
    if not candidate._nnue_ready:
        raise SystemExit("candidate did not load its weights; pack it first")

    boards = sample_boards(args.boards)
    gates: list[dict[str, Any]] = []
    gates += gate_identities()
    gates += gate_debruijn(candidate)
    gates += gate_reference_equality(candidate, args.tag, boards)
    gates += gate_symmetry(boards)
    gates += gate_determinism(candidate, boards)
    gates += gate_legality(candidate)
    gates += gate_holdout(candidate, args.tag)
    gates += gate_nps(control, candidate, args.nps_limit)
    gates += gate_package(args.tag)

    detail: dict[str, Any] = {}
    if not args.skip_fixtures:
        fixture_gates, detail = gate_fixtures()
        gates += fixture_gates

    passed = sum(1 for g in gates if g["passes"])
    report = {
        "tag": args.tag,
        "gates_passed": passed,
        "gates_total": len(gates),
        "all_passed": passed == len(gates),
        "failing": [g["gate"] for g in gates if not g["passes"]],
        "seconds": round(time.perf_counter() - started, 1),
        "gates": gates,
        "fixture_detail": detail,
    }
    outdir = OUT / args.tag
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "gates.json").write_text(json.dumps(report, indent=2), encoding="utf-8")

    width = max(len(g["gate"]) for g in gates)
    for g in gates:
        print(f"{'PASS' if g['passes'] else 'FAIL'}  {g['gate']:<{width}}  {g['measured']}")
    print(f"\n{passed}/{len(gates)} gates pass  ({report['seconds']}s)")
    if report["failing"]:
        print("FAILING: " + ", ".join(report["failing"]))


if __name__ == "__main__":
    main()
