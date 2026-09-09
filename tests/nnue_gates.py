"""RATED_V5 gate for the NNUE candidate, plus the shallow-depth boundary tests.

Why a depth sweep rather than only the fixture's minimum correcting depth: the control
already passes all sixteen enforced fixtures when it is allowed to search to that depth.
The rated losses did not happen there. They happened when the real clock let the search
finish only a shallower iteration, and at that shallower depth the control prefers the
move it later rejects. `control_prefers_by_depth` in the fixture file records exactly
which depths those are, so the gate measures every depth in the sweep and counts:

  correction   the control plays the unacceptable move at this depth and the candidate
               does not
  regression   the control avoids it at this depth and the candidate plays it

A solved control that changes its move at any depth is a break, reported separately.

The round 80 boundary case is also run against perturbed siblings of its position. A
residual that only rejects 24.Rd4 in that exact FEN has memorised a fixture; one that
also moves the evaluation the right way in neighbouring positions has learned something.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import pathlib
import sys
import types

import chess

REPO = pathlib.Path(__file__).resolve().parent.parent
HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(HERE))

POSITIONS = HERE / "rated_v5_positions.json"
OUT = REPO / "tests" / "results" / "nnue" / "gates"
DEPTHS = (2, 3, 4, 5)


def load_module(path: pathlib.Path, name: str) -> types.ModuleType:
    """Import an agent from its real file, so `__file__` and its weights resolve."""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _rated_v4() -> types.ModuleType:
    """The repo's own fixed-depth probe, reused rather than reimplemented.

    rated_v4.root sets the engine up through `prepare`, orders the root with the
    engine's own ordering and tracks repetitions with enter/leave. A hand-rolled root
    would quietly differ from every existing measurement in tests/results, so the gate
    calls the established one.
    """
    return load_module(HERE / "rated_v4.py", "nnue_gate_rated_v4")


V4 = _rated_v4()


def root_choice(module: types.ModuleType, board: chess.Board, depth: int) -> dict:
    outcome = V4.root(module, board, depth)
    return {
        "best": outcome["best"],
        "best_san": outcome["best_san"],
        "score": int(outcome["score"]),
        "nodes": outcome["nodes"],
    }


def sweep(module: types.ModuleType, cases: list[dict], depths: tuple[int, ...]) -> dict:
    out: dict[str, dict[str, dict]] = {}
    for case in cases:
        per_depth: dict[str, dict] = {}
        for depth in depths:
            board = chess.Board(case["fen"])
            per_depth[str(depth)] = root_choice(module, board, depth)
        out[case["id"]] = per_depth
    return out


def perturb(fen: str, limit: int = 12) -> list[str]:
    """Legal siblings of a position: one reversible king or rook step away from it.

    These share the position's character without being the fixture FEN, so a residual
    that generalises should still push the evaluation the same way, while one that has
    memorised the fixture should not.
    """
    board = chess.Board(fen)
    siblings: list[str] = []
    for move in board.legal_moves:
        board.push(move)
        if not board.is_game_over():
            for reply in board.legal_moves:
                board.push(reply)
                if not board.is_game_over() and board.turn == chess.Board(fen).turn:
                    siblings.append(board.fen())
                board.pop()
                if len(siblings) >= limit:
                    break
        board.pop()
        if len(siblings) >= limit:
            break
    return siblings[:limit]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--control", type=pathlib.Path, default=REPO / "agent.py")
    ap.add_argument("--candidate", type=pathlib.Path, default=REPO / "agent_nnue.py")
    ap.add_argument("--depths", default="2,3,4,5")
    ap.add_argument("--out", type=pathlib.Path, default=OUT / "rated_v5_gate.json")
    args = ap.parse_args()

    depths = tuple(int(d) for d in args.depths.split(","))
    record = json.loads(POSITIONS.read_text(encoding="utf-8"))
    cases = record["positions"]

    control = load_module(args.control, "gate_control")
    candidate = load_module(args.candidate, "gate_candidate")
    candidate_status = getattr(candidate, "_nnue_status", "n/a")
    print("candidate NNUE status:", candidate_status, flush=True)

    print("sweeping control...", flush=True)
    control_sweep = sweep(control, cases, depths)
    print("sweeping candidate...", flush=True)
    candidate_sweep = sweep(candidate, cases, depths)

    corrections: list[dict] = []
    regressions: list[dict] = []
    solved_breaks: list[dict] = []
    changed: list[dict] = []

    for case in cases:
        cid = case["id"]
        bad = set(case.get("unacceptable_uci") or [])
        keep = set(case.get("acceptable_uci") or [])
        for depth in depths:
            key = str(depth)
            c = control_sweep[cid][key]
            n = candidate_sweep[cid][key]
            if c["best"] != n["best"]:
                changed.append(
                    {
                        "id": cid,
                        "kind": case["kind"],
                        "depth": depth,
                        "control": c["best"],
                        "candidate": n["best"],
                        "control_score": c["score"],
                        "candidate_score": n["score"],
                    }
                )
            if bad:
                control_bad = c["best"] in bad
                candidate_bad = n["best"] in bad
                if control_bad and not candidate_bad:
                    corrections.append(
                        {"id": cid, "depth": depth, "was": c["best"], "now": n["best"]}
                    )
                if not control_bad and candidate_bad:
                    regressions.append(
                        {"id": cid, "depth": depth, "was": c["best"], "now": n["best"]}
                    )
            solved = case["kind"] == "solved_control" and keep and case["enforced"]
            if solved and c["best"] in keep and n["best"] not in keep:
                solved_breaks.append(
                    {"id": cid, "depth": depth, "was": c["best"], "now": n["best"]}
                )

    # The five reproducible class-A failures, defined by where the control actually
    # prefers the unacceptable move rather than by assertion.
    class_a: list[dict] = []
    for case in cases:
        bad = set(case.get("unacceptable_uci") or [])
        if not bad or case["kind"] != "critical" or not case["enforced"]:
            continue
        bad_depths = [
            d for d in depths if control_sweep[case["id"]][str(d)]["best"] in bad
        ]
        if bad_depths:
            fixed = [
                d for d in bad_depths if candidate_sweep[case["id"]][str(d)]["best"] not in bad
            ]
            class_a.append(
                {
                    "id": case["id"],
                    "unacceptable": sorted(bad),
                    "control_plays_it_at_depths": bad_depths,
                    "candidate_fixes_depths": fixed,
                    "fully_corrected": len(fixed) == len(bad_depths),
                    "any_corrected": bool(fixed),
                }
            )

    # Round 80 boundary test with generalization probe.
    r80 = next(c for c in cases if c["id"] == "r80-24-Rd4")
    boundary = {
        "id": r80["id"],
        "fen": r80["fen"],
        "unacceptable": r80["unacceptable_uci"],
        "control_depth2": control_sweep[r80["id"]]["2"],
        "candidate_depth2": candidate_sweep[r80["id"]]["2"],
        "control_plays_bad_at_depth2": control_sweep[r80["id"]]["2"]["best"]
        in set(r80["unacceptable_uci"]),
        "candidate_plays_bad_at_depth2": candidate_sweep[r80["id"]]["2"]["best"]
        in set(r80["unacceptable_uci"]),
    }
    boundary["corrected_at_depth2"] = (
        boundary["control_plays_bad_at_depth2"] and not boundary["candidate_plays_bad_at_depth2"]
    )

    siblings = perturb(r80["fen"])
    sibling_rows = []
    correction_fn = getattr(candidate, "nnue_correction", None)
    for fen in siblings:
        board = chess.Board(fen)
        row = {
            "fen": fen,
            "control_static": int(control.cached_evaluate(board)),
            "candidate_static": int(candidate.cached_evaluate(board)),
        }
        if correction_fn is not None:
            row["nnue_correction"] = int(correction_fn(board))
        row["delta"] = row["candidate_static"] - row["control_static"]
        sibling_rows.append(row)
    nonzero = sum(1 for r in sibling_rows if r["delta"] != 0)
    boundary["generalization"] = {
        "siblings_tested": len(sibling_rows),
        "siblings_with_nonzero_correction": nonzero,
        "memorisation_suspected": bool(sibling_rows) and nonzero == 0,
        "note": (
            "a residual that moves only the fixture FEN and none of its legal siblings "
            "would be memorising rather than generalising"
        ),
        "rows": sibling_rows,
    }

    r79 = next(c for c in cases if c["id"] == "r79-12-Nxd3")
    r79_report = {
        "id": r79["id"],
        "unacceptable": r79["unacceptable_uci"],
        "by_depth": {
            str(d): {
                "control": control_sweep[r79["id"]][str(d)]["best"],
                "candidate": candidate_sweep[r79["id"]][str(d)]["best"],
            }
            for d in depths
        },
    }

    corrected_fixtures = sum(1 for r in class_a if r["any_corrected"])
    report = {
        "depths": list(depths),
        "candidate_nnue_status": candidate_status,
        "class_a_failures_found": len(class_a),
        "class_a_fixtures_corrected": corrected_fixtures,
        "class_a_detail": class_a,
        "corrections": corrections,
        "regressions": regressions,
        "solved_control_breaks": solved_breaks,
        "moves_changed": changed,
        "round80_boundary": boundary,
        "round79": r79_report,
        "gate": {
            "min_class_a_corrections": 3,
            "class_a_corrections_met": corrected_fixtures >= 3,
            "max_solved_control_breaks": 0,
            "solved_controls_intact": not solved_breaks,
            "no_new_regressions": not regressions,
            "passes": corrected_fixtures >= 3 and not solved_breaks and not regressions,
        },
        "control_sweep": control_sweep,
        "candidate_sweep": candidate_sweep,
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print()
    print(f"class-A failures found at shallow depth : {len(class_a)}")
    for row in class_a:
        print(
            f"  {row['id']:22} control plays {row['unacceptable']} at depths "
            f"{row['control_plays_it_at_depths']}, candidate fixes {row['candidate_fixes_depths']}"
        )
    print(f"class-A fixtures corrected              : {corrected_fixtures} (need >= 3)")
    print(f"solved-control breaks                   : {len(solved_breaks)} (need 0)")
    print(f"new regressions                         : {len(regressions)} (need 0)")
    print(f"round 80 corrected at depth 2           : {boundary['corrected_at_depth2']}")
    print(
        f"round 80 siblings moved by residual     : "
        f"{boundary['generalization']['siblings_with_nonzero_correction']}"
        f"/{boundary['generalization']['siblings_tested']}"
    )
    print(f"RATED_V5 GATE PASSES                    : {report['gate']['passes']}")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
