"""Assemble every recorded V3 artifact into the tables the final report cites.

The prose of `FINAL_NNUE_V3_REPORT.md` is written by hand; every number in it comes from
here, so a figure in the report can always be traced back to the JSON a run wrote. Where
an artifact is missing this prints the absence rather than a plausible-looking blank --
an experiment that was not run must not read as one that returned nothing.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import subprocess
from typing import Any

REPO = pathlib.Path(__file__).resolve().parent.parent
V2 = REPO / "tests" / "results" / "nnue" / "v2"
V3 = REPO / "tests" / "results" / "nnue" / "v3"


def load(path: pathlib.Path) -> Any:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def identities() -> list[str]:
    out = ["## identities", ""]
    control = REPO / "agent.py"
    digest = hashlib.sha256(control.read_bytes()).hexdigest()
    lines = len(control.read_text(encoding="utf-8").splitlines())
    out.append(f"control agent.py sha256 {digest}")
    out.append(f"control agent.py bytes  {control.stat().st_size}")
    out.append(f"control agent.py lines  {lines}")
    pkg = REPO / "submissions" / "pre_nn_20260909" / "agent.zip"
    if pkg.exists():
        out.append(
            f"pre-neural package sha256 {hashlib.sha256(pkg.read_bytes()).hexdigest()}"
        )
    for cmd, label in (
        (["git", "rev-parse", "--abbrev-ref", "HEAD"], "branch"),
        (["git", "rev-parse", "HEAD"], "commit"),
        (["git", "rev-parse", "pre-nnue-control-20260909^{commit}"], "control tag commit"),
    ):
        value = subprocess.run(cmd, capture_output=True, text=True, cwd=REPO).stdout.strip()
        out.append(f"{label}: {value}")
    return [*out, ""]


def calibrations() -> list[str]:
    out = ["## control-only calibrations", ""]
    depth = load(V3 / "depth_calibration.json")
    if depth:
        out.append("| clock ms | budget ms | mean depth | median | nodes |")
        out.append("| ---: | ---: | ---: | ---: | ---: |")
        for row in depth["rows"]:
            out.append(
                f"| {row['time_left_ms']:,} | {row['budget_ms']} | {row['mean_depth']} "
                f"| {row['median_depth']} | {row['mean_nodes']:,.0f} |"
            )
    else:
        out.append("depth calibration: NOT RUN")
    out.append("")
    workers = load(V3 / "worker_calibration.json")
    if workers:
        out.append(
            f"worker calibration at {workers['clock_ms']} ms + "
            f"{workers['increment_ms']} ms, {workers['games_per_setting']} games each"
        )
        out.append("")
        out.append("| workers | games/h | mean depth | vs 1 worker | flags |")
        out.append("| ---: | ---: | ---: | ---: | ---: |")
        for row in workers["rows"]:
            out.append(
                f"| {row['workers']} | {row['games_per_hour']:,.0f} | {row['mean_depth']} "
                f"| {row['depth_vs_1_worker_pct']:+.2f}% | {row['flags']} |"
            )
    else:
        out.append("worker calibration: NOT RUN")
    out.append("")
    ties = load(V3 / "root_ties.json")
    if ties:
        out.append("root-tie headroom for the ORDER mode:")
        for key in (
            "positions_scored", "clock_ms", "mean_root_tie_size", "tied_root_rate",
            "tie_holds_better_move_rate", "mean_recoverable_cp_per_position",
            "mean_played_regret_cp", "played_equals_control_static_best_rate",
        ):
            out.append(f"  {key}: {ties[key]}")
    else:
        out.append("root-tie measurement: NOT RUN")
    return [*out, ""]


def checkpoints() -> list[str]:
    out = ["## every checkpoint trained", ""]
    rows: list[dict[str, Any]] = []
    for summary in sorted(V3.glob("training_summary_*.json")):
        data = load(summary)
        if not data:
            continue
        for entry in data:
            history = entry["history"][entry["best_epoch"]]
            rows.append(
                {
                    "name": entry["name"],
                    "form": entry.get("form", "additive"),
                    "epoch": entry["best_epoch"],
                    "composite": entry["best_composite"],
                    "eligible": entry["eligible"],
                    **history,
                }
            )
    rows.sort(key=lambda r: -r["composite"])
    out.append(
        "| checkpoint | form | ep | composite | preserved | top-move gain | "
        "regret cp | p95 harm | p99 harm | zero flips | draw pres | fire | \\|corr\\| | eligible |"
    )
    out.append(
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: "
        "| ---: | ---: | ---: | :-: |"
    )
    for r in rows:
        out.append(
            f"| `{r['name']}` | {r['form']} | {r['epoch']} | {r['composite']:.3f} "
            f"| {r['preserved_rate']:.4f} | {r['top_move_accuracy_gain']:+.4f} "
            f"| {r['regret_reduction_cp']:+.2f} | {r['p95_harmful_regret_cp']:.0f} "
            f"| {r['p99_harmful_regret_cp']:.0f} | {r['near_zero_sign_flip_rate']:.4f} "
            f"| {r['draw_preservation_rate']:.4f} | {r['fire_rate']:.3f} "
            f"| {r['mean_abs_correction']:.2f} | {'yes' if r['eligible'] else 'no'} |"
        )
    out.append("")
    out.append(f"{len(rows)} checkpoints trained")
    return [*out, ""]


def gate_tables() -> list[str]:
    out = ["## gates", ""]
    found = False
    for path in sorted(V3.glob("*/gates_*.json")):
        data = load(path)
        if not data:
            continue
        found = True
        out.append(f"### `{data['tag']}` mode={data['mode']}")
        out.append("")
        out.append("| gate | measured | requirement | result |")
        out.append("| --- | --- | --- | :-: |")
        for g in data["gates"]:
            out.append(
                f"| {g['gate']} | {g['measured']} | {g['requirement']} | "
                f"{'PASS' if g['passes'] else 'FAIL'} |"
            )
        out.append("")
        out.append(f"{data['gates_passed']}/{data['gates_total']} pass")
        if data["failing"]:
            out.append("")
            out.append("FAILING: " + ", ".join(data["failing"]))
        out.append("")
    if not found:
        out.append("no gate run recorded")
    return [*out, ""]


def arenas() -> list[str]:
    """Every head-to-head and benchmark arena recorded, in whichever tree wrote it.

    Keys are read as the arena scripts actually write them rather than guessed: the
    head-to-head writes candidate_W-D-L, candidate_score_pct and score_ci95_pct, and the
    benchmark arena writes a paired difference instead of a score.
    """
    out = ["## arenas", ""]
    out.append(
        "| run | tag | games | clock | workers | W-D-L | score % | 95% CI % | Elo | "
        "Elo CI | draws % | cand depth | ctl depth | flags | illegal |"
    )
    out.append(
        "| --- | --- | ---: | --- | ---: | --- | ---: | --- | ---: | --- | ---: "
        "| ---: | ---: | ---: | ---: |"
    )
    found = False
    seen: set[str] = set()
    for root in (V3, V2):
        for path in sorted(root.glob("**/*.json")):
            data = load(path)
            if not isinstance(data, dict) or "candidate_W-D-L" not in data:
                continue
            key = f"{path.parent.name}/{path.stem}"
            if key in seen:
                continue
            seen.add(key)
            found = True
            ci = data.get("score_ci95_pct") or []
            ci_txt = f"[{ci[0]:.2f}, {ci[1]:.2f}]" if len(ci) == 2 else "-"
            eci = data.get("elo_ci95") or []
            eci_txt = f"[{eci[0]:.1f}, {eci[1]:.1f}]" if len(eci) == 2 else "-"
            out.append(
                f"| {path.stem} | `{path.parent.name}` | {data.get('games', '-')} "
                f"| {data.get('clock_ms', '-')}+{data.get('increment_ms', '-')} "
                f"| {data.get('workers', '-')} | {data['candidate_W-D-L']} "
                f"| {data.get('candidate_score_pct', '-')} | {ci_txt} "
                f"| {data.get('elo', '-')} | {eci_txt} | {data.get('draw_pct', '-')} "
                f"| {data.get('candidate_mean_depth', '-')} "
                f"| {data.get('control_mean_depth', '-')} | {data.get('flags', '-')} "
                f"| {data.get('illegal', '-')} |"
            )
    if not found:
        out.append("| no arena result recorded | | | | | | | | | | | | | | |")
    out.append("")

    # Colour and opponent-family splits decide gate 15, so they are printed in full
    # rather than summarised into a single pass/fail.
    for root in (V3, V2):
        for path in sorted(root.glob("**/*.json")):
            data = load(path)
            if not isinstance(data, dict) or "by_colour" not in data:
                continue
            out.append(f"### {path.parent.name}/{path.stem} by colour")
            out.append("")
            out.append("| candidate colour | games | score % | W | D | L |")
            out.append("| --- | ---: | ---: | ---: | ---: | ---: |")
            for colour, row in data["by_colour"].items():
                out.append(
                    f"| {colour} | {row['games']} | {row['score_pct']} | {row['wins']} "
                    f"| {row['draws']} | {row['losses']} |"
                )
            out.append("")
            if "by_family" in data:
                out.append("| opponent family | pairs | paired diff | 95% CI |")
                out.append("| --- | ---: | ---: | --- |")
                for fam, row in data["by_family"].items():
                    out.append(
                        f"| {fam} | {row.get('pairs', '-')} | "
                        f"{row.get('paired_diff', '-')} | {row.get('ci95', '-')} |"
                    )
                out.append("")
    return [*out, ""]


def probes() -> list[str]:
    out = ["## search-level probes (played move, real clock)", ""]
    found = False
    for path in sorted(V3.glob("search_probe_*.json")):
        data = load(path)
        if not data:
            continue
        found = True
        out.append(f"### {data['tag']} at {data['clock_ms']} ms on the {data['split']} split")
        out.append("")
        for key in (
            "paired_positions", "moves_changed", "moves_changed_rate",
            "paired_mean_regret_delta_cp", "paired_improved", "paired_worsened",
            "paired_worst_regression_cp", "paired_best_improvement_cp",
        ):
            out.append(f"  {key}: {data[key]}")
        out.append("")
        for label in ("control", "candidate_metrics"):
            m = data[label]
            out.append(
                f"  {label}: mean regret {m['mean_regret_cp']} cp, top-move rate "
                f"{m['top_move_rate']}, depth {m['mean_depth']}, nodes {m['mean_nodes']:,.0f}"
            )
        out.append("")
    if not found:
        out.append("no search probe recorded")
    return [*out, ""]


def docker() -> list[str]:
    out = ["## platform container", ""]
    found = False
    for path in sorted(V3.glob("*/docker_*.json")):
        data = load(path)
        if not data:
            continue
        found = True
        pack = data["packaging"]
        cont = data["container"]
        out.append(f"### `{data['tag']}` mode={data['mode']}")
        out.append("")
        out.append(f"  image: {data['image']} ({data['image_id'][:19]})")
        out.append(
            f"  zip: {pack['zip_bytes']:,} bytes, "
            f"uncompressed {pack['uncompressed_bytes']:,}"
        )
        out.append(f"  agent at root: {pack['agent_at_root']}, no folders: {pack['no_folders']}")
        for member in pack["members"]:
            out.append(f"    {member['name']}  {member['bytes']:,} bytes  {member['sha256'][:16]}")
        for key, value in cont.items():
            if key not in {"stdout", "stderr"}:
                out.append(f"  {key}: {value}")
        out.append("")
    if not found:
        out.append("no container validation recorded")
    return [*out, ""]


def data_provenance() -> list[str]:
    out = ["## data", ""]
    for label, path in (
        ("V2 sibling groups", REPO / "tests/results/nnue/groups/generation_summary.json"),
        ("V3 targeted groups", V3 / "groups" / "generation_summary.json"),
    ):
        data = load(path)
        if data:
            out.append(
                f"{label}: {data['groups_written']:,} written of "
                f"{data['parents_planned']:,} planned, "
                f"{data['skipped_no_signal']:,} dropped for no ordering signal, "
                f"{data['groups_per_hour']:,.0f} groups/h on {data['workers']} workers"
            )
        else:
            out.append(f"{label}: NOT RUN")
    overlap = load(V3 / "overlap_report.json")
    if overlap:
        out.append("")
        out.append("split overlap check:")
        for key, value in overlap.items():
            out.append(f"  {key}: {value}")
    else:
        out.append("")
        out.append("split overlap check: NOT RUN")
    return [*out, ""]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=pathlib.Path, default=V3 / "report_tables.md")
    args = ap.parse_args()

    blocks = (
        identities()
        + calibrations()
        + data_provenance()
        + checkpoints()
        + probes()
        + gate_tables()
        + arenas()
        + docker()
    )
    text = "\n".join(blocks)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, encoding="utf-8")
    print(text)
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
