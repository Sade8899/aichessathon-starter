"""Build a V3 candidate from the protected control, in either integration mode.

Both modes reuse V2's inserted block verbatim -- the de Bruijn feature scan, the int8
accumulator, the fused Numba call and the gated blend were all proved correct in V1/V2,
and re-deriving them here would only create a second thing that can drift. What differs
is where the network's output is allowed to go.

EVAL mode is V2's shape: the correction is added at leaf evaluation, so it can move any
score the search consumes, including a drawn one.

ORDER mode adds the block but leaves `_uncached_evaluate` bound to the control's own
function, so **every evaluation the search returns is bit-identical to the control's**.
The network is consulted once per root move, purely to order the root list. That is
structurally immune to the mechanism V2 traced from a small residual to a broken
repetition defence, because nothing it does can move an evaluation off zero.

The single inserted line at the root is also the tie-break, without any second hook:
the control re-sorts root moves with `list.sort`, which is stable, so two moves the
search scores exactly equally keep the order the network gave them.

`strip()` is the exact inverse of `build()` in both modes, and the gate asserts
`strip(build(agent.py)) == agent.py` byte for byte. That is what makes "only the
evaluation, or only the ordering, was touched" a checked fact rather than a claim.
"""

from __future__ import annotations

import argparse
import hashlib
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tests"))

import nnue_v2_build_agent as v2b  # noqa: E402

CONTROL = REPO / "agent.py"
CONTROL_SHA256 = v2b.CONTROL_SHA256

# The control's root ordering call, and the replacement that consults the network.
OLD_ROOT = "        moves = self.order(board, list(board.legal_moves), None, 0)\n"
NEW_ROOT = (
    "        moves = self.order(board, list(board.legal_moves), None, 0)\n"
    "        moves = _nnue_root_order(board, moves)  # NNUE-V3-ORDER\n"
)

ORDER_HOOK = '''

# ===== NNUE-V3 ORDER HOOK BEGIN =====
def _nnue_root_order(board: chess.Board, moves: list[chess.Move]) -> list[chess.Move]:
    """Order the root move list by the network's view of each child.

    Called once per move, not once per node, so its cost is a few dozen evaluations
    against the tens of thousands of nodes the search will spend afterwards.

    The value compared is the child's evaluation, which is from the child's side to
    move -- the opponent's -- so lower is better for us. The control's own ordering is
    carried as the tie-break, and because `list.sort` is stable this ordering also
    survives as the tie-break of every later completed-score sort.

    Nothing here changes a number the search returns. `_uncached_evaluate` is still the
    control's own function in this mode, so every evaluation is bit-identical.
    """
    if not _nnue_ready or len(moves) < 2:
        return moves
    scored: list[tuple[int, int, chess.Move]] = []
    for rank, move in enumerate(moves):
        board.push(move)
        try:
            value = compiled_evaluate(board) + nnue_correction(board)
        finally:
            board.pop()
        scored.append((value, rank, move))
    scored.sort(key=lambda item: (item[0], item[1]))
    return [item[2] for item in scored]
# ===== NNUE-V3 ORDER HOOK END =====
'''


def build(weight_sha256: str, mode: str) -> str:
    source = CONTROL.read_text(encoding="utf-8")
    digest = hashlib.sha256(CONTROL.read_bytes()).hexdigest()
    if digest != CONTROL_SHA256:
        raise SystemExit(
            f"REFUSING TO BUILD: agent.py is {digest}, expected {CONTROL_SHA256}. "
            "The protected control has changed."
        )
    if source.count(v2b.ANCHOR) != 1:
        raise SystemExit("expected exactly one warm-up anchor in agent.py")
    if source.count(v2b.OLD_IMPORTS) != 1:
        raise SystemExit("expected exactly one import block in agent.py")

    block = v2b.BLOCK.replace("__WEIGHT_SHA256__", weight_sha256)
    if mode == "order":
        block = block.replace(v2b.END, ORDER_HOOK.lstrip("\n") + v2b.END)

    out = source.replace(v2b.ANCHOR, v2b.ANCHOR + "\n" + block, 1)
    out = out.replace(v2b.OLD_IMPORTS, v2b.NEW_IMPORTS, 1)

    if mode == "eval":
        if source.count(v2b.OLD_TAIL) != 1:
            raise SystemExit("expected exactly one evaluation-binding tail in agent.py")
        if source.count(v2b.OLD_KEY) != 1:
            raise SystemExit("expected exactly one eval cache key in agent.py")
        out = out.replace(v2b.OLD_TAIL, v2b.NEW_TAIL, 1)
        out = out.replace(v2b.OLD_KEY, v2b.NEW_KEY, 1)
    elif mode == "order":
        if source.count(OLD_ROOT) != 1:
            raise SystemExit("expected exactly one root ordering call in agent.py")
        out = out.replace(OLD_ROOT, NEW_ROOT, 1)
    else:
        raise SystemExit(f"unknown mode {mode!r}")
    return out


def strip(candidate: str, mode: str) -> str:
    """Exact inverse of build()."""
    start = candidate.index(v2b.BEGIN)
    end = candidate.index(v2b.END) + len(v2b.END)
    out = candidate[:start] + candidate[end:]
    out = out.replace(v2b.ANCHOR + "\n\n", v2b.ANCHOR + "\n", 1)
    if mode == "eval":
        out = out.replace(v2b.NEW_TAIL, v2b.OLD_TAIL, 1)
        out = out.replace(v2b.NEW_KEY, v2b.OLD_KEY, 1)
    else:
        out = out.replace(NEW_ROOT, OLD_ROOT, 1)
    out = out.replace(v2b.NEW_IMPORTS, v2b.OLD_IMPORTS, 1)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--weight-sha256", default="")
    ap.add_argument("--mode", choices=("eval", "order"), default="eval")
    ap.add_argument("--out", type=pathlib.Path, default=None)
    args = ap.parse_args()

    out = args.out or REPO / f"agent_nnue_v3_{args.mode}.py"
    text = build(args.weight_sha256, args.mode)
    out.write_text(text, encoding="utf-8")

    restored = strip(text, args.mode)
    if restored != CONTROL.read_text(encoding="utf-8"):
        print("FAIL: strip(build(agent.py)) != agent.py", file=sys.stderr)
        raise SystemExit(1)

    print(
        f"wrote {out} mode={args.mode} "
        f"({len(text.splitlines())} lines, sha256 "
        f"{hashlib.sha256(text.encode()).hexdigest()[:16]}); "
        "round-trip to the control verified"
    )


if __name__ == "__main__":
    main()
