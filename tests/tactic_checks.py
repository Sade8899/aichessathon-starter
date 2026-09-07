"""Isolated tactical-search experiment against the promoted delta-pruning engine.

The control is `65ec40ce...`, the engine Phase 1 promoted, which is also the working
`agent.py`. The candidate is that source with exactly one tactical search technique
added. Like the delta-pruning experiment before it, the candidate is *not* expected
to search identically — finding more tactics is the point — so the equivalence claim
is scoped rather than absolute: only the definitions the manifest predeclares may
differ, and behaviour is compared rather than asserted equal.

The scope this asserts is read from the manifest, which is written before the
candidate exists, so the check cannot be widened after the fact to fit whatever was
built. The shared gates (cold import, clock safety, official gate, arena) reuse the
`time_checks` harness with this experiment's manifest bound in.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import types
from pathlib import Path
from typing import Any, cast

import chess
import numba_validation as numeric
import time_checks

MANIFEST = Path("tests/tactic_experiment.json")
FLAGS = ("ADAPTIVE", "PASSIVE", "FAST_EVAL", "EVAL_CACHE", "DEPTH_EVIDENCE", "TIGHT_ROOT")
PRESERVED = ("DELTA_MARGIN", "ROOT_WINDOW_MARGIN", "TT_SIZE", "MAX_PLY", "VALUES", "MATE", "INF")


def record() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(MANIFEST.read_text()))


def bind() -> None:
    """Point the shared harness at this experiment without editing it."""
    manifest = record()
    time_checks.MANIFEST = MANIFEST
    time_checks.CONTROL_SHA = manifest["control_sha256"]
    time_checks.CANDIDATE_SHA = manifest["candidate_sha256"]


def sources() -> dict[str, str]:
    bind()
    return time_checks.sources()


def engines() -> dict[str, types.ModuleType]:
    bind()
    return time_checks.engines()


def constants(tree: ast.Module) -> set[str]:
    return {
        target.id
        for node in tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }


def identity() -> None:
    """Only the definitions the manifest predeclared may differ, and nothing else."""
    manifest = record()
    scope = manifest["scope"]
    added = set(scope["added_constants"])
    changed = set(scope["changed_methods"])
    added_definitions = set(scope["added_definitions"])
    raw = {
        "control": time_checks.resolve(manifest, "control", manifest["control_sha256"]),
        "candidate": time_checks.resolve(manifest, "candidate", manifest["candidate_sha256"]),
    }
    digests = {name: hashlib.sha256(value).hexdigest() for name, value in raw.items()}
    assert digests["control"] == manifest["control_sha256"], digests
    assert digests["candidate"] == manifest["candidate_sha256"], digests
    assert all(b"\r" not in value for value in raw.values())

    trees = {name: ast.parse(value.decode()) for name, value in raw.items()}
    control, candidate = trees["control"], trees["candidate"]
    names = [
        {n.name for n in tree.body if isinstance(n, ast.ClassDef | ast.FunctionDef)}
        for tree in (control, candidate)
    ]
    assert not names[0] - names[1], "no definition may be removed or renamed"
    assert names[1] - names[0] == added_definitions, names[1] - names[0]

    control_constants, candidate_constants = constants(control), constants(candidate)
    assert not control_constants - candidate_constants, "no constant may be removed"
    assert candidate_constants - control_constants == added, candidate_constants - control_constants

    for node in control.body:
        if not isinstance(node, ast.ClassDef | ast.FunctionDef):
            continue
        other = next(n for n in candidate.body if getattr(n, "name", "") == node.name)
        if node.name != "Engine":
            assert ast.dump(node) == ast.dump(other), node.name
            continue
        methods = [
            {n.name: n for n in cls.body if isinstance(n, ast.FunctionDef)}
            for cls in (node, cast(ast.ClassDef, other))
        ]
        assert not set(methods[0]) - set(methods[1]), "no Engine method may be removed"
        assert set(methods[1]) - set(methods[0]) == set(scope.get("added_methods", []))
        differing = {m for m in methods[0] if ast.dump(methods[0][m]) != ast.dump(methods[1][m])}
        assert differing == changed, differing

    modules = engines()
    for flag in FLAGS:
        assert getattr(modules["control"], flag) == getattr(modules["candidate"], flag), flag
    for name in PRESERVED:
        assert getattr(modules["control"], name) == getattr(modules["candidate"], name), name
    signatures = {name: numeric.signatures(m) for name, m in modules.items()}
    assert signatures["control"] == signatures["candidate"], "Numba signatures must match"
    assert all(signatures["candidate"].values())
    assert modules["candidate"].get_move.__doc__ == modules["control"].get_move.__doc__
    budgets = {
        name: [time_checks.budget_of(module, clock) for clock in (120_000, 113_600, 10_000)]
        for name, module in modules.items()
    }
    assert budgets["control"] == budgets["candidate"], budgets

    count = 0
    reachable = [time_checks.start_board(o).fen() for o in time_checks.openings()]
    for fen in numeric.suite() + reachable:
        board = chess.Board(fen)
        for tested in (board, board.mirror()):
            assert modules["candidate"].evaluate(tested) == modules["control"].evaluate(tested)
            count += 1
    print(
        "IDENTITY "
        + json.dumps(
            {
                "sha256": digests,
                "bytes": {name: len(value) for name, value in raw.items()},
                "definitions_removed": 0,
                "definitions_added": sorted(added_definitions),
                "constants_added": sorted(added),
                "engine_methods_changed": sorted(changed),
                "every_other_definition_identical": True,
                "flags": {flag: getattr(modules["candidate"], flag) for flag in FLAGS},
                "preserved_constants": {
                    name: getattr(modules["candidate"], name) for name in PRESERVED
                },
                "numba_signatures_identical": True,
                "budgets_s": budgets["candidate"],
                "evaluation_comparisons": count,
                "evaluation_identical": True,
            }
        ),
        flush=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "mode", choices=("identity", "cold", "clock", "gate", "arena", "sources")
    )
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--moves", type=int, default=300)
    parser.add_argument("--stress-moves", type=int, default=600)
    parser.add_argument("--cases", type=int, default=10)
    parser.add_argument("--case-offset", type=int, default=0)
    parser.add_argument("--seed", type=int, default=73000)
    parser.add_argument("--base-ms", type=int, default=120_000)
    parser.add_argument("--increment-ms", type=int, default=500)
    parser.add_argument("--configs", default="control,candidate")
    args = parser.parse_args()
    bind()
    if args.mode == "identity":
        identity()
    elif args.mode == "cold":
        time_checks.cold(args.repeats)
    elif args.mode == "clock":
        time_checks.clock_safety(args.moves, args.stress_moves, args.base_ms, args.increment_ms)
    elif args.mode == "gate":
        import order_checks

        order_checks.official_gate(args.configs.split(","), sources())
    elif args.mode == "sources":
        print(json.dumps({name: len(text) for name, text in sources().items()}))
    else:
        time_checks.arena(args)


if __name__ == "__main__":
    main()
