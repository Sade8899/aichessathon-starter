"""Screen checkpoints on the RATED_V5 fixtures alone, before spending arena time.

The fixture gate is the cheap test that actually rejected V1, and it takes about a
minute per checkpoint against roughly ten minutes for a screening arena. Running it
first over a family of checkpoints costs almost nothing and rejects the ones that cannot
pass regardless of how they play.

Each checkpoint is packed in turn, which rewrites the repo-root candidate, so nothing
else may use `agent_nnue_v2.py` while this runs.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
from typing import Any

REPO = pathlib.Path(__file__).resolve().parent.parent
PY_EXE = str(pathlib.Path(sys.executable))
V2 = REPO / "tests" / "results" / "nnue" / "v2"


def fixtures(source: str) -> dict[str, Any]:
    proc = subprocess.run(
        [PY_EXE, str(REPO / "tests" / "rated_v5.py"), "fixtures", "--source", source],
        capture_output=True,
        text=True,
        cwd=REPO,
        check=True,
    )
    return json.loads(proc.stdout)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("tags", nargs="+")
    ap.add_argument("--out", type=pathlib.Path, default=V2 / "fixture_screen.json")
    args = ap.parse_args()

    control = fixtures("agent.py")
    control_pass = {r["id"]: r["passed"] for r in control["results"]}
    print(
        f"CONTROL enforced {control['enforced_passed']}/{control['enforced_total']}\n",
        flush=True,
    )

    rows: list[dict[str, Any]] = []
    for tag in args.tags:
        training = json.loads((V2 / tag / "training.json").read_text(encoding="utf-8"))
        hist = training["history"][training["best_epoch"]]
        subprocess.run(
            [PY_EXE, str(REPO / "tests" / "nnue_v2_pack.py"), tag],
            capture_output=True,
            text=True,
            cwd=REPO,
            check=True,
        )
        candidate = fixtures("agent_nnue_v2.py")
        got = {r["id"]: r["passed"] for r in candidate["results"]}
        broke = [i for i, ok in control_pass.items() if ok and not got[i]]
        row = {
            "tag": tag,
            "config": training["config"],
            "preserved": hist["preserved_rate"],
            "corr": hist["mean_abs_correction"],
            "regret": hist["regret_reduction_cp"],
            "mae": hist["mae_gain_cp"],
            "enforced": f"{candidate['enforced_passed']}/{candidate['enforced_total']}",
            "passed": candidate["passed"],
            "broke": broke,
        }
        rows.append(row)
        print(
            f"{tag:34s} preserved={row['preserved']:.4f} |corr|={row['corr']:6.2f} "
            f"regret={row['regret']:+6.2f} RATED_V5={row['enforced']} broke={broke}",
            flush=True,
        )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps({"control_enforced": control["enforced_passed"], "rows": rows}, indent=2),
        encoding="utf-8",
    )
    survivors = [r["tag"] for r in rows if r["passed"]]
    print(f"\nsurvivors ({len(survivors)}): {survivors}")


if __name__ == "__main__":
    main()
