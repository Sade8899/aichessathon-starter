"""Write a live progress snapshot for the NNUE run.

Reads whatever artifacts exist and reports counts, rates and an estimate of remaining
time. Stages that have not started are shown as pending, never as complete.
"""

from __future__ import annotations

import contextlib
import json
import pathlib
import time

REPO = pathlib.Path(__file__).resolve().parent.parent
NNUE = REPO / "tests" / "results" / "nnue"
OUT = NNUE / "PROGRESS.md"


def read(path: pathlib.Path):
    with contextlib.suppress(Exception):
        return json.loads(path.read_text(encoding="utf-8"))
    return None


def count_lines(path: pathlib.Path) -> int:
    if not path.exists():
        return 0
    with path.open(encoding="utf-8") as handle:
        return sum(1 for _ in handle)


def main() -> None:
    manifest = read(NNUE / "game_manifest.json") or {}
    planned = manifest.get("games", 0)
    games_done = len(list((NNUE / "games").glob("*.json"))) if (NNUE / "games").exists() else 0

    collect = read(NNUE / "dataset" / "collect_summary.json") or {}
    positions = collect.get("selected", 0)

    labels_done = 0
    labels_dir = NNUE / "labels"
    if labels_dir.exists():
        seen: set[str] = set()
        for shard in labels_dir.glob("shard-*.jsonl"):
            with shard.open(encoding="utf-8") as handle:
                for line in handle:
                    with contextlib.suppress(Exception):
                        seen.add(json.loads(line)["fen_key"])
        labels_done = len(seen)

    training = read(NNUE / "model" / "training_report.json") or {}
    throughput = read(NNUE / "gates" / "throughput.json") or {}
    gate = read(NNUE / "gates" / "rated_v5_gate.json") or {}
    arena = read(NNUE / "gates" / "arena.json") or {}
    invariants = read(NNUE / "gates" / "invariants.json") or {}

    # 92,033 positions/hour measured at 6 Stockfish workers, 200k nodes each
    remaining_labels = max(0, positions - labels_done)
    eta_hours = remaining_labels / 92_033 if remaining_labels else 0.0

    label_state = (
        "complete"
        if positions and labels_done >= positions
        else f"{eta_hours:.2f} h remaining at 92,033/h"
    )
    lines = [
        "# NNUE run — live progress",
        "",
        f"Updated {time.strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "| stage | state | detail |",
        "| --- | --- | --- |",
        f"| games generated | {games_done}/{planned} | "
        f"{'complete' if planned and games_done >= planned else 'running'} |",
        f"| positions retained (unique) | {positions:,} | "
        f"{collect.get('unique_positions_available', 0):,} available before capping |",
        f"| phase mix | {collect.get('phase_fractions', {})} | "
        f"target 40/40/20 unreachable, see NNUE_EXPERIMENT addendum |",
        f"| splits | {collect.get('split_counts', {})} | "
        f"leakage free: {collect.get('leakage_free')} |",
        f"| labels completed | {labels_done:,}/{positions:,} | "
        f"{label_state} |",
        f"| invariants | {len(invariants.get('checks') or [])} checks | "
        f"all pass: {invariants.get('all_pass')} |",
        f"| training | {'done' if training else 'pending'} | "
        f"{training.get('epochs_run', 0)} epochs, best val MAE {training.get('best_val_mae')} |",
        f"| throughput gate | {'done' if throughput else 'pending'} | "
        f"NPS loss {throughput.get('nps_loss_pct')}% |",
        f"| RATED_V5 gate | {'done' if gate else 'pending'} | "
        f"class-A corrected {gate.get('class_a_fixtures_corrected')} |",
        f"| arena | {'done' if arena else 'pending'} | "
        f"{arena.get('paired_comparisons', 0)} paired comparisons |",
        "",
        "Active workers: 6 (measured optimum, see docker_calibration/concurrency.json).",
        "",
    ]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
