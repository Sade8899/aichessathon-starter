"""Screen checkpoints against RATED_V4 and RATED_V5 before spending an arena on one.

V2 screened against RATED_V5 only. V3's gate 8 requires both suites at the control's
full baseline, and the first checkpoint the frozen composite selected passed V5 16/16
while breaking three V4 fixtures -- a regression V2's screen could not have seen.

This applies the hard admission filter the selection rule always implied: a checkpoint
that fails a hard gate is not ranked, it is excluded. The ranking itself is untouched;
what changes is only which checkpoints are admitted to it.

Each checkpoint is installed at the repo root and built into a candidate, because the
fixture harness execs an agent's source without setting `__file__` and therefore resolves
its weight file from the working directory. The previous root weight file is restored
when the screen finishes.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import pathlib
import shutil
import subprocess
import sys
import time
from typing import Any

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))

import nnue_v3_build_agent as builder  # noqa: E402

V3 = REPO / "tests" / "results" / "nnue" / "v3"
ROOT_WEIGHTS = REPO / "nnue_v2_weights.npz"
ROOT_AGENT = REPO / "agent_nnue_v3_screen.py"


def rated(script: str, source: str) -> dict[str, Any]:
    proc = subprocess.run(
        [sys.executable, str(REPO / "tests" / script), "fixtures", "--source", source],
        check=True, capture_output=True, text=True, cwd=REPO,
    )
    return json.loads(proc.stdout)


def screen(tag: str, form: str, mode: str) -> dict[str, Any]:
    source = V3 / tag / "quantized.npz"
    if not source.exists():
        raise SystemExit(f"no checkpoint at {source}")
    shutil.copyfile(source, ROOT_WEIGHTS)
    digest = hashlib.sha256(ROOT_WEIGHTS.read_bytes()).hexdigest()
    ROOT_AGENT.write_text(builder.build(digest, mode, form), encoding="utf-8")

    started = time.perf_counter()
    v5 = rated("rated_v5.py", ROOT_AGENT.name)
    v4 = rated("rated_v4.py", ROOT_AGENT.name)
    return {
        "tag": tag,
        "form": form,
        "mode": mode,
        "weight_sha256": digest,
        "v5_passed": v5["enforced_passed"],
        "v5_total": v5["enforced_total"],
        "v5_results": {r["id"]: r["passed"] for r in v5["results"]},
        "v4_passed": v4.get("enforced_passed", 0),
        "v4_total": v4.get("enforced_total", 0),
        "v4_results": {r["id"]: r["passed"] for r in v4["results"]},
        "seconds": round(time.perf_counter() - started, 1),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("tags", nargs="+")
    ap.add_argument("--form", default="relative")
    ap.add_argument("--mode", default="eval")
    ap.add_argument("--label", default="screen")
    args = ap.parse_args()

    backup = ROOT_WEIGHTS.read_bytes() if ROOT_WEIGHTS.exists() else None
    baseline_v5 = rated("rated_v5.py", "agent.py")
    baseline_v4 = rated("rated_v4.py", "agent.py")
    print(
        f"control baseline: V5 {baseline_v5['enforced_passed']}/"
        f"{baseline_v5['enforced_total']}, "
        f"V4 {baseline_v4.get('enforced_passed')}/{baseline_v4.get('enforced_total')}"
    )
    control_v4 = {r["id"]: r["passed"] for r in baseline_v4["results"]}
    control_v5 = {r["id"]: r["passed"] for r in baseline_v5["results"]}

    rows: list[dict[str, Any]] = []
    try:
        for tag in args.tags:
            row = screen(tag, args.form, args.mode)
            row["v4_broke"] = sorted(
                f for f, ok in control_v4.items() if ok and not row["v4_results"].get(f, False)
            )
            row["v5_broke"] = sorted(
                f for f, ok in control_v5.items() if ok and not row["v5_results"].get(f, False)
            )
            row["admitted"] = not row["v4_broke"] and not row["v5_broke"]
            rows.append(row)
            print(
                f"{tag:<30} V5 {row['v5_passed']}/{row['v5_total']} "
                f"V4 {row['v4_passed']}/{row['v4_total']} "
                f"broke {row['v5_broke'] + row['v4_broke'] or 'none'} "
                f"-> {'ADMITTED' if row['admitted'] else 'EXCLUDED'} ({row['seconds']}s)",
                flush=True,
            )
    finally:
        if backup is not None:
            ROOT_WEIGHTS.write_bytes(backup)
        with contextlib.suppress(FileNotFoundError):
            ROOT_AGENT.unlink()

    payload = {
        "control_v5": f"{baseline_v5['enforced_passed']}/{baseline_v5['enforced_total']}",
        "control_v4": f"{baseline_v4.get('enforced_passed')}/{baseline_v4.get('enforced_total')}",
        "rows": rows,
    }
    out = V3 / f"fixture_screen_{args.label}.json"
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
