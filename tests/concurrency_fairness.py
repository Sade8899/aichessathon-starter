"""Fixed-depth fairness probe: identical positions and identical node counts, timed.

Runs inside a calibration container. Both configurations search the same 24-position
suite to the same fixed depth, so their node counts are equal by construction and the
only thing wall time can measure is how much CPU the container actually received.
The execution order is an argument, so callers can counterbalance it across workers.
"""

from __future__ import annotations

import argparse
import json
import time
import types
from pathlib import Path

import numba_validation as numeric
import qgen_checks
from selection import load


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--order", default="control,candidate")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--depth", type=int, default=2)
    parser.add_argument("--experiment", default="tests/qcap_validation.json")
    parser.add_argument("--label", default="")
    args = parser.parse_args()
    order = args.order.split(",")
    assert sorted(order) == ["candidate", "control"], args.order

    qgen_checks.MANIFEST = Path(args.experiment)
    sources = qgen_checks.sources()
    modules: dict[str, types.ModuleType] = {}
    for name in order:
        started = time.perf_counter()
        modules[name] = load("fair_" + name, sources[name])
        print(
            "LOAD "
            + json.dumps(
                {"label": args.label, "name": name, "seconds": time.perf_counter() - started}
            ),
            flush=True,
        )

    suite = numeric.suite()
    for repeat in range(args.repeats):
        for index, fen in enumerate(suite):
            for slot, name in enumerate(order):
                row = qgen_checks.fixed(modules[name], fen, args.depth)
                print(
                    "FAIR "
                    + json.dumps(
                        {
                            "label": args.label,
                            "order": args.order,
                            "name": name,
                            "slot": slot,
                            "repeat": repeat,
                            "position": index,
                            "nodes": row["nodes"],
                            "seconds": row["seconds"],
                            "nps": row["nps"],
                            "move": row["move"],
                        }
                    ),
                    flush=True,
                )
    print("FAIR_DONE " + json.dumps({"label": args.label, "positions": len(suite)}), flush=True)


if __name__ == "__main__":
    main()
