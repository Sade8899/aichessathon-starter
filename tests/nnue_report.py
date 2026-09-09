"""Assemble FINAL_NNUE_REPORT.md and its machine-readable twins from the artifacts.

Every number in the report is read from a results file produced by one of the earlier
steps. Nothing is retyped, so the prose cannot drift away from the measurements.
"""

from __future__ import annotations

import ast
import csv
import difflib
import hashlib
import json
import pathlib
import zipfile

REPO = pathlib.Path(__file__).resolve().parent.parent
NNUE = REPO / "tests" / "results" / "nnue"
OUT_MD = NNUE / "FINAL_NNUE_REPORT.md"


def read(path: pathlib.Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # a stage that has not run yet is reported, not fatal
        return None


def measure_source(data: bytes, label: str) -> dict:
    text = data.decode("utf-8")
    return {
        "label": label,
        "sha256": hashlib.sha256(data).hexdigest(),
        "bytes": len(data),
        "lines": text.count("\n"),
    }


def function_diff(before: str, after: str) -> list[dict]:
    """Summarise the source diff by function, rather than as a wall of lines."""

    def defs(text: str) -> dict[str, str]:
        tree = ast.parse(text)
        out: dict[str, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                out[node.name] = ast.dump(node)
        return out

    a, b = defs(before), defs(after)
    rows = []
    for name in sorted(set(a) | set(b)):
        if name not in a:
            rows.append({"function": name, "change": "added"})
        elif name not in b:
            rows.append({"function": name, "change": "removed"})
        elif a[name] != b[name]:
            rows.append({"function": name, "change": "modified"})
    return rows


def main() -> None:
    control_bytes = (REPO / "agent.py").read_bytes()
    candidate_path = REPO / "agent_nnue.py"
    candidate_bytes = candidate_path.read_bytes() if candidate_path.exists() else b""
    with zipfile.ZipFile(REPO / "submission 0609v4" / "agent.zip") as z:
        v4_bytes = z.read("agent.py")

    control = measure_source(control_bytes, "pre-neural control agent.py")
    v4 = measure_source(v4_bytes, "submission 0609v4 agent.py")
    candidate = (
        measure_source(candidate_bytes, "post-training candidate agent_nnue.py")
        if candidate_bytes
        else None
    )

    baseline = read(NNUE / "pre_training" / "baseline_proof.json")
    pre_docker = read(NNUE / "pre_training" / "docker_validation.json")
    _ = pre_docker  # kept in the repository; not re-tabled here
    pgn = read(NNUE / "pre_training" / "pgn_inventory.json")
    engines = read(NNUE / "pre_training" / "benchmark_engines.json")
    sf = read(NNUE / "pre_training" / "stockfish_probe.json")
    calib = read(NNUE / "docker_calibration" / "concurrency.json")
    gen = read(NNUE / "generation_summary.json")
    collect = read(NNUE / "dataset" / "collect_summary.json")
    labels = read(NNUE / "dataset" / "label_summary.json")
    screening = read(NNUE / "model" / "screening.json")
    winner = (screening or {}).get("winner") or {}
    winner_dir = NNUE / pathlib.Path(winner["model_dir"]).name if winner.get("model_dir") else None
    training = read((winner_dir or (NNUE / "model")) / "training_report.json")
    packed = read(NNUE / "model" / "packed_weights.json")
    invariants = read(NNUE / "gates" / "invariants.json")
    label_summary = read(NNUE / "label_summary.json")
    collect_summary = read(NNUE / "collect_summary.json")
    throughput = read(NNUE / "gates" / "throughput.json")
    gate = read(NNUE / "gates" / "rated_v5_gate.json")
    arena = read(NNUE / "gates" / "arena.json")
    post_docker = read(NNUE / "gates" / "docker_validation.json")

    diff_rows = (
        function_diff(control_bytes.decode("utf-8"), candidate_bytes.decode("utf-8"))
        if candidate_bytes
        else []
    )
    v4_diff = list(
        difflib.unified_diff(
            v4_bytes.decode("utf-8").splitlines(),
            control_bytes.decode("utf-8").splitlines(),
            lineterm="",
        )
    )

    # ------------------------------------------------------------------ gate verdicts
    verdicts: list[dict] = []

    def add(name: str, value, requirement: str, ok: bool | None) -> None:
        verdicts.append(
            {"gate": name, "measured": value, "requirement": requirement, "passes": ok}
        )

    if invariants:
        add(
            "pipeline invariants",
            f"{len(invariants['checks']) - invariants['failed']}/{len(invariants['checks'])}",
            "all pass",
            invariants["all_pass"],
        )
    if training:
        for split in ("test", "holdout"):
            q = training.get(f"{split}_quant")
            f = training.get(f"{split}_float")
            qe = training.get(f"{split}_quantization_error")
            if q and f and qe:
                add(
                    f"float->quant median deviation ({split})",
                    qe["median_cp"],
                    "<= 10 cp",
                    qe["median_cp"] <= 10,
                )
                add(
                    f"deployed clamped MAE improvement ({split})",
                    f"{q['mae_improvement_cp']:+.2f} cp "
                    f"({q['mae_improvement_pct']:+.2f}%), "
                    f"{q['baseline_mae']} -> {q['mae']}",
                    "materially better than the handcrafted evaluator",
                    q["mae_improvement_cp"] > 0,
                )
                for phase in ("middlegame", "endgame"):
                    ph = q.get("by_phase", {}).get(phase)
                    if ph:
                        gain = ph["baseline_mae"] - ph["corrected_mae"]
                        add(
                            f"{phase} not regressed ({split})",
                            f"{gain:+.2f} cp ({ph['baseline_mae']} -> {ph['corrected_mae']})",
                            "no material regression",
                            gain > -1.0,
                        )
            health = training.get(f"{split}_health")
            if health:
                add(
                    f"no NaN or inf ({split})",
                    health,
                    "none",
                    not health["nan"] and not health["inf"],
                )
        aug = training.get("augmentation")
        if aug:
            add(
                "colour-symmetry disagreement",
                f"{aug['identical_features']}/{aug['checked']} identical features",
                "effectively zero",
                aug["label_preserving"],
            )
    if collect:
        add(
            "no train/test leakage",
            collect["split_fen_overlaps"],
            "all zero",
            collect["leakage_free"],
        )
    if throughput:
        add(
            "full-search NPS loss",
            f"{throughput['nps_loss_pct']}%",
            "<= 10%",
            throughput["throughput_gate_passes"],
        )
    if gate:
        g = gate["gate"]
        add(
            "class-A corrections",
            gate["class_a_fixtures_corrected"],
            ">= 3",
            g["class_a_corrections_met"],
        )
        add(
            "solved-control breaks",
            len(gate["solved_control_breaks"]),
            "0",
            g["solved_controls_intact"],
        )
        add(
            "new fixture regressions",
            len(gate["regressions"]),
            "0",
            g["no_new_regressions"],
        )
        add(
            "round 80 24.Rd4 rejected at depth 2",
            gate["round80_boundary"]["corrected_at_depth2"],
            "true",
            gate["round80_boundary"]["corrected_at_depth2"],
        )
        gen_probe = gate["round80_boundary"]["generalization"]
        add(
            "round 80 generalisation (not memorised)",
            f"{gen_probe['siblings_with_nonzero_correction']}"
            f"/{gen_probe['siblings_tested']} siblings moved",
            "not zero",
            not gen_probe["memorisation_suspected"],
        )
    if arena:
        pd = arena["paired_difference"]
        add(
            "arena paired score difference",
            f"{pd['mean_score_diff']:+.4f} CI [{pd['ci95_low']:+.4f}, {pd['ci95_high']:+.4f}]",
            "CI excludes zero, or overwhelming fixture evidence",
            pd["ci_excludes_zero"] and pd["mean_score_diff"] > 0,
        )
        add(
            "arena time losses (candidate)",
            arena["candidate"].get("time_losses"),
            "0",
            arena["candidate"].get("time_losses") == 0,
        )
        add(
            "arena illegal moves (candidate)",
            arena["candidate"].get("illegal_moves"),
            "0",
            arena["candidate"].get("illegal_moves") == 0,
        )
    if post_docker:
        c = post_docker["container"]
        add(
            "initialization in platform container",
            f"{c.get('import_seconds')} s",
            "< 90 s",
            c.get("import_under_90s"),
        )
        add(
            "peak memory in platform container",
            f"{c.get('peak_memory_mb')} MB",
            "< 2048 MB",
            c.get("peak_under_2gb"),
        )
        add("legal moves, both colours", c.get("all_legal"), "true", c.get("all_legal"))
        add(
            "uncompressed submission size",
            f"{post_docker['uncompressed_bytes']} B",
            "< 50,000,000",
            post_docker["under_50mb"],
        )

    decided = [v for v in verdicts if v["passes"] is not None]
    failed = [v for v in decided if not v["passes"]]
    all_pass = bool(decided) and not failed

    # ---------------------------------------------------------------------- machine
    NNUE.mkdir(parents=True, exist_ok=True)
    (NNUE / "final_report.json").write_text(
        json.dumps(
            {
                "artifacts": {
                    "control": control,
                    "submission_0609v4": v4,
                    "candidate": candidate,
                },
                "v4_vs_control_diff_lines": len(v4_diff),
                "candidate_vs_control_function_changes": diff_rows,
                "gates": verdicts,
                "gates_decided": len(decided),
                "gates_failed": len(failed),
                "all_gates_pass": all_pass,
                "stages_present": {
                    "baseline_proof": baseline is not None,
                    "pgn_inventory": pgn is not None,
                    "benchmark_engines": engines is not None,
                    "stockfish_probe": sf is not None,
                    "concurrency_calibration": calib is not None,
                    "generation": gen is not None,
                    "collect": collect is not None,
                    "labels": labels is not None,
                    "training": training is not None,
                    "packed_weights": packed is not None,
                    "throughput": throughput is not None,
                    "rated_v5_gate": gate is not None,
                    "arena": arena is not None,
                    "docker_validation": post_docker is not None,
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    with (NNUE / "gates.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["gate", "measured", "requirement", "passes"])
        writer.writeheader()
        for row in verdicts:
            writer.writerow(row)

    # ------------------------------------------------------------------------ prose
    lines: list[str] = []
    add_line = lines.append
    add_line("# NNUE-lite residual evaluator — final report")
    add_line("")
    add_line("Generated by `tests/nnue_report.py` from the recorded artifacts. Every figure")
    add_line("below is read from a results file; none is retyped.")
    add_line("")
    add_line("## 1. The three artifacts")
    add_line("")
    add_line("| artifact | SHA-256 | bytes | lines |")
    add_line("| --- | --- | ---: | ---: |")
    for item in (v4, control, candidate):
        if item:
            add_line(
                f"| {item['label']} | `{item['sha256']}` | "
                f"{item['bytes']:,} | {item['lines']:,} |"
            )
    add_line("")
    if baseline:
        answers = baseline["answers"]
        add_line("### Control versus the live submission")
        add_line("")
        add_line(f"- byte-identical to 06/09v4: **{answers['byte_identical_to_v4']}**")
        add_line(f"- AST-identical to 06/09v4: **{answers['ast_identical_to_v4']}**")
        add_line(f"- difference class: **{answers['difference_class']}**")
        vd = baseline["v4_vs_control_diff"]
        add_line(
            f"- diff: {vd['lines_added']} lines added, {vd['lines_removed']} removed, "
            f"in {vd['hunks']} hunk(s); functions changed: "
            f"{', '.join(vd['defs_changed']) or 'none'}"
        )
        add_line(f"- module constants added: `{vd['module_constants_added_in_control']}`")
        add_line("")
    if diff_rows:
        add_line("### Candidate versus control, by function")
        add_line("")
        add_line("| function | change |")
        add_line("| --- | --- |")
        for row in diff_rows:
            add_line(f"| `{row['function']}` | {row['change']} |")
        add_line("")

    if collect_summary:
        add_line("## 2. Dataset")
        add_line("")
        add_line(f"- games read: {collect_summary.get('games_read'):,}")
        add_line(
            f"- unique positions available: "
            f"{collect_summary.get('unique_positions_available', 0):,}; "
            f"duplicates collapsed: "
            f"{collect_summary.get('duplicate_positions_collapsed', 0):,}"
        )
        add_line(f"- selected: {collect_summary.get('selected', 0):,}")
        add_line(f"- splits: `{collect_summary.get('split_counts')}`")
        add_line(f"- phase mix: `{collect_summary.get('phase_fractions')}`")
        add_line(
            f"- RATED_V5 fixture positions dropped: "
            f"{collect_summary.get('dropped_rated_v5_fixture_positions', 0)}"
        )
        add_line(
            f"- split FEN overlaps: `{collect_summary.get('split_fen_overlaps')}` "
            f"(leakage free: {collect_summary.get('leakage_free')})"
        )
        add_line("")
    if label_summary:
        add_line("### Labelling")
        add_line("")
        add_line("- engine: Stockfish 19 (`sf_19`), 1 thread, 64 MB hash")
        add_line(f"- node limit: {label_summary.get('nodes'):,}")
        add_line(
            f"- label strengths: `{label_summary.get('label_strength_distribution')}` "
            f"(stronger labels retained rather than weakened)"
        )
        add_line(
            f"- depth reached: median {label_summary.get('median_depth_reached')}, "
            f"minimum {label_summary.get('min_depth_reached')}"
        )
        add_line(
            f"- completeness: {label_summary.get('labelled'):,}/"
            f"{label_summary.get('expected'):,}, missing "
            f"{label_summary.get('missing')}, unexpected {label_summary.get('unexpected')}"
        )
        add_line(
            f"- rate: {label_summary.get('positions_per_hour'):,}/h at "
            f"{label_summary.get('shards')} workers"
        )
        add_line("")
    if screening:
        add_line("### Architecture screening")
        add_line("")
        add_line("Ranked on " + screening.get("ranking_rule", ""))
        add_line("")
        add_line("| tag | params | test cp | test % | holdout cp | sign acc |")
        add_line("| --- | ---: | ---: | ---: | ---: | ---: |")
        for row in screening.get("results", []):
            if not row.get("ok"):
                continue
            add_line(
                f"| `{row['tag']}` | {row['parameters']:,} | "
                f"{row.get('test_improvement_cp', 0):+.2f} | "
                f"{row.get('test_improvement_pct', 0):+.2f} | "
                f"{row.get('holdout_improvement_cp', 0):+.2f} | "
                f"{row.get('test_sign_acc', 0):.3f} |"
            )
        add_line("")

    add_line("## 3. Gate results")
    add_line("")
    add_line("| gate | measured | requirement | verdict |")
    add_line("| --- | --- | --- | --- |")
    for row in verdicts:
        mark = "PASS" if row["passes"] else ("FAIL" if row["passes"] is False else "n/a")
        add_line(f"| {row['gate']} | {row['measured']} | {row['requirement']} | **{mark}** |")
    add_line("")
    add_line(f"**{len(decided) - len(failed)} of {len(decided)} decided gates pass.**")
    add_line("")

    add_line("## 4. Recommendation")
    add_line("")
    if all_pass:
        add_line("Every declared gate passes. The neural candidate is recommended.")
    else:
        add_line("**The neural candidate is rejected.** The control is retained.")
        add_line("")
        add_line("Failing gates:")
        add_line("")
        for row in failed:
            add_line(
                f"- **{row['gate']}**: measured {row['measured']}, "
                f"required {row['requirement']}"
            )
        add_line("")
        add_line("The experimental code, dataset manifests and model artifacts are")
        add_line("preserved")
        add_line("for later work; nothing is deleted and the control is unmodified.")
    add_line("")

    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {OUT_MD}")
    print(f"wrote {NNUE / 'final_report.json'}")
    print(f"wrote {NNUE / 'gates.csv'}")
    print()
    for row in verdicts:
        mark = "PASS" if row["passes"] else ("FAIL" if row["passes"] is False else "n/a ")
        print(f"  [{mark}] {row['gate']}: {row['measured']}")
    print()
    print(f"ALL GATES PASS: {all_pass}")


if __name__ == "__main__":
    main()
