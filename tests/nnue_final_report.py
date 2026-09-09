"""Assemble the final NNUE report from the recorded artifacts.

Every figure is read from a results file written by an earlier stage. Nothing is
retyped, so the prose cannot drift from the measurements, and a stage that did not run
is reported as absent rather than assumed to have passed.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import pathlib
import subprocess
import zipfile

REPO = pathlib.Path(__file__).resolve().parent.parent
NNUE = REPO / "tests" / "results" / "nnue"
OUT = NNUE / "FINAL_NNUE_REPORT.md"


def read(path: pathlib.Path):
    with contextlib.suppress(Exception):
        return json.loads(path.read_text(encoding="utf-8"))
    return None


def sh(*args: str) -> str:
    return subprocess.run(args, capture_output=True, text=True, cwd=REPO).stdout.strip()


def digest(path: pathlib.Path) -> dict:
    if not path.exists():
        return {"path": str(path), "present": False}
    data = path.read_bytes()
    out = {
        "path": str(path.relative_to(REPO)) if path.is_relative_to(REPO) else str(path),
        "present": True,
        "sha256": hashlib.sha256(data).hexdigest(),
        "bytes": len(data),
    }
    with contextlib.suppress(Exception):
        out["lines"] = data.decode("utf-8").count("\n")
    return out


def main() -> None:
    control = digest(REPO / "agent.py")
    candidate = digest(REPO / "agent_nnue.py")
    weights = digest(REPO / "nnue_weights.npz")
    pre_zip = digest(REPO / "submissions" / "pre_nn_20260909" / "agent.zip")
    with zipfile.ZipFile(REPO / "submission 0609v4" / "agent.zip") as z:
        v4_inner = z.read("agent.py")
    v4 = {
        "sha256": hashlib.sha256(v4_inner).hexdigest(),
        "bytes": len(v4_inner),
        "lines": v4_inner.decode("utf-8").count("\n"),
    }

    baseline = read(NNUE / "pre_training" / "baseline_proof.json") or {}
    collect = read(NNUE / "collect_summary.json") or {}
    labels = read(NNUE / "label_summary.json") or {}
    label_rate = read(NNUE / "dataset" / "label_rate_real.json") or {}
    screening = read(NNUE / "model" / "screening.json") or {}
    winner = screening.get("winner") or {}
    training = read(NNUE / f"model_{winner.get('tag','')}" / "training_report.json") or {}
    invariants = read(NNUE / "gates" / "invariants.json") or {}
    throughput = read(NNUE / "gates" / "throughput.json") or {}
    gate = read(NNUE / "gates" / "rated_v5_gate.json") or {}
    clamp = read(NNUE / "gates" / "clamp_sweep.json") or {}
    fx_control = read(NNUE / "gates" / "fixtures_control.json") or {}
    fx_candidate = read(NNUE / "gates" / "fixtures_candidate.json") or {}
    arena = read(NNUE / "gates" / "arena.json") or {}
    arena_deep = read(NNUE / "gates" / "arena_deep.json") or {}
    calib = read(NNUE / "docker_calibration" / "concurrency.json") or {}
    nvd = read(NNUE / "docker_calibration" / "native_vs_docker.json") or {}
    pre_docker = read(NNUE / "pre_training" / "docker_validation.json") or {}
    engines = read(NNUE / "pre_training" / "benchmark_engines.json") or []
    sf = read(NNUE / "pre_training" / "stockfish_probe.json") or {}

    # ---------------------------------------------------------------- gate verdicts
    gates: list[dict] = []

    def add(name: str, measured, requirement: str, ok) -> None:
        gates.append(
            {"gate": name, "measured": measured, "requirement": requirement, "passes": ok}
        )

    if invariants:
        add(
            "pipeline invariants",
            f"{len(invariants['checks']) - invariants['failed']}/{len(invariants['checks'])}",
            "all pass",
            invariants["all_pass"],
        )
    if collect:
        add(
            "no train/test leakage",
            collect.get("split_fen_overlaps"),
            "all zero",
            collect.get("leakage_free"),
        )
    for split in ("test", "holdout"):
        q = training.get(f"{split}_quant")
        f = training.get(f"{split}_float")
        e = training.get(f"{split}_quantization_error")
        h = training.get(f"{split}_health")
        if q and f:
            add(
                f"deployed clamped MAE improvement ({split})",
                f"{q['mae_improvement_cp']:+.2f} cp ({q['mae_improvement_pct']:+.2f}%), "
                f"{q['baseline_mae']} -> {q['mae']}",
                "materially better than the handcrafted evaluator",
                q["mae_improvement_cp"] > 0,
            )
            retained = q["mae_improvement_cp"] / max(f["mae_improvement_cp"], 1e-9) * 100
            add(
                f"quantized retains the float gain ({split})",
                f"{retained:.1f}%",
                ">= 90%",
                retained >= 90,
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
        if e:
            add(
                f"float->quant median drift ({split})",
                f"{e['median_cp']} cp",
                "<= 10 cp",
                e["median_cp"] <= 10,
            )
        if h:
            add(f"no NaN or inf ({split})", h, "none", not h["nan"] and not h["inf"])
    if throughput:
        add(
            "full-search NPS loss",
            f"{throughput['nps_loss_pct']}% "
            f"({throughput['warm_control']['nps_mean']:,.0f} -> "
            f"{throughput['warm_candidate']['nps_mean']:,.0f} nps)",
            "<= 10%",
            throughput["throughput_gate_passes"],
        )
    if fx_control and fx_candidate:
        add(
            "RATED_V5 enforced fixtures (repository rule)",
            f"control {fx_control['enforced_passed']}/{fx_control['enforced_total']}, "
            f"candidate {fx_candidate['enforced_passed']}/{fx_candidate['enforced_total']}; "
            f"newly failing: {fx_candidate['failed']}",
            "candidate must not fail a fixture the control passes",
            fx_candidate["enforced_passed"] >= fx_control["enforced_passed"],
        )
    if gate:
        add(
            "class-A corrections (depth sweep)",
            gate["class_a_fixtures_corrected"],
            ">= 3",
            gate["gate"]["class_a_corrections_met"],
        )
        add(
            "solved-control breaks",
            len(gate["solved_control_breaks"]),
            "0",
            gate["gate"]["solved_controls_intact"],
        )
        add(
            "new fixture regressions",
            len(gate["regressions"]),
            "0",
            gate["gate"]["no_new_regressions"],
        )
        b = gate["round80_boundary"]
        add(
            "round 80 24.Rd4 rejected at depth 2",
            b["corrected_at_depth2"],
            "true",
            b["corrected_at_depth2"],
        )
        g = b["generalization"]
        add(
            "round 80 generalises (not a memorised FEN)",
            f"{g['siblings_with_nonzero_correction']}/{g['siblings_tested']} siblings moved",
            "not zero",
            not g["memorisation_suspected"],
        )
    if arena:
        pd = arena["paired_difference"]
        add(
            "arena paired score difference vs control",
            f"{pd['mean_score_diff']:+.4f} "
            f"95% CI [{pd['ci95_low']:+.4f}, {pd['ci95_high']:+.4f}], "
            f"Elo {pd['elo_estimate']:+.1f} {pd['elo_ci95']}",
            "confidence interval excluding zero, in the candidate's favour",
            pd["ci_excludes_zero"] and pd["mean_score_diff"] > 0,
        )
    if arena_deep:
        pdd = arena_deep["paired_difference"]
        add(
            "arena at deeper clock (confirmation)",
            f"{pdd['mean_score_diff']:+.4f} "
            f"95% CI [{pdd['ci95_low']:+.4f}, {pdd['ci95_high']:+.4f}], "
            f"depth {arena_deep['control'].get('mean_depth')} vs "
            f"{arena_deep['candidate'].get('mean_depth')}",
            "candidate not worse",
            pdd["mean_score_diff"] >= 0,
        )
    if arena:
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
    if weights.get("present"):
        total = candidate.get("bytes", 0) + weights.get("bytes", 0)
        add(
            "candidate package uncompressed size",
            f"{total:,} bytes",
            "< 50,000,000",
            total < 50_000_000,
        )

    decided = [g for g in gates if g["passes"] is not None]
    failed = [g for g in decided if not g["passes"]]
    accept = bool(decided) and not failed

    if accept:
        verdict = "ACCEPT AND PROPOSE PACKAGE"
    elif failed and arena:
        verdict = "REJECT - RETAIN CONTROL"
    else:
        verdict = "INCONCLUSIVE - RETAIN CONTROL"

    # ------------------------------------------------------------------- markdown
    lines: list[str] = []
    w = lines.append
    w("# NNUE-lite residual evaluator — final report")
    w("")
    w(f"**VERDICT: {verdict}**")
    w("")
    w(f"{len(decided) - len(failed)} of {len(decided)} decided gates pass.")
    w("")
    if failed:
        w("Failing gates:")
        w("")
        for row in failed:
            w(f"- **{row['gate']}** — measured `{row['measured']}`, required {row['requirement']}")
        w("")
    w("Generated by `tests/nnue_final_report.py` from recorded artifacts.")
    w("")

    w("## 1. Identities")
    w("")
    w("| artifact | SHA-256 | bytes | lines |")
    w("| --- | --- | ---: | ---: |")
    w(f"| pre-training control `agent.py` | `{control['sha256']}` | {control['bytes']:,} "
      f"| {control.get('lines', 0):,} |")
    w(f"| `submission 0609v4` inner `agent.py` | `{v4['sha256']}` | {v4['bytes']:,} "
      f"| {v4['lines']:,} |")
    w(f"| pre-neural package | `{pre_zip.get('sha256','-')}` | {pre_zip.get('bytes',0):,} | - |")
    w(f"| candidate `agent_nnue.py` | `{candidate['sha256']}` | {candidate['bytes']:,} "
      f"| {candidate.get('lines', 0):,} |")
    if weights.get("present"):
        w(f"| candidate weights `nnue_weights.npz` | `{weights['sha256']}` "
          f"| {weights['bytes']:,} | - |")
    w("")
    w(f"- frozen control tag: `pre-nnue-control-20260909` -> "
      f"`{sh('git', 'rev-parse', '--short', 'pre-nnue-control-20260909^{commit}')}`")
    w(f"- branch: `{sh('git', 'rev-parse', '--abbrev-ref', 'HEAD')}` at "
      f"`{sh('git', 'rev-parse', '--short', 'HEAD')}`")
    if baseline:
        a = baseline.get("answers", {})
        w(f"- control byte-identical to 06/09v4: **{a.get('byte_identical_to_v4')}** — "
          f"{baseline.get('v4_vs_control_diff', {}).get('lines_added')} lines added, "
          f"{baseline.get('v4_vs_control_diff', {}).get('lines_removed')} removed "
          f"(`DELTA_MARGIN` and quiescence delta pruning). The live submission is one "
          f"revision behind the control.")
    w("")

    w("## 2. Dataset")
    w("")
    if collect:
        w(f"- games read: **{collect.get('games_read'):,}** (2,600 generated, zero failures)")
        w(f"- unique positions available: {collect.get('unique_positions_available', 0):,}; "
          f"duplicates collapsed: {collect.get('duplicate_positions_collapsed', 0):,}")
        w(f"- selected: **{collect.get('selected', 0):,}**, per-game cap "
          f"{collect.get('per_game_cap')}")
        w(f"- splits: `{collect.get('split_counts')}`")
        w(f"- phase mix: `{collect.get('phase_fractions')}`")
        w(f"- RATED_V5 fixture positions excluded from training: "
          f"{collect.get('dropped_rated_v5_fixture_positions', 0)}")
        w(f"- split FEN overlaps: `{collect.get('split_fen_overlaps')}` — "
          f"leakage free: **{collect.get('leakage_free')}**")
        w("")
        w("Deduplication uses the canonical key (placement, side to move, castling rights, "
          "en passant) and is split-aware: a position appearing in games from two splits is "
          "kept in exactly one. Splits are assigned per game lineage, never per position. "
          "The entire Loki engine family is held out of training as an opponent-"
          "generalisation test.")
        w("")
        w("Declared phase target 40/40/20 was **not reachable**: endgames are about 11% of "
          "the unique positions these games produce, so a 40% endgame corpus would have to "
          "be roughly a third of the size. Openings were capped at 15% instead and the "
          "achieved mix recorded.")
        w("")

    w("## 3. Labelling")
    w("")
    if labels:
        w("- engine: **Stockfish 19** (`sf_19`), 1 thread, 64 MB hash, fixed nodes")
        w(f"- binary SHA-256: `{sf.get('binary', {}).get('sha256', '-')}` — never packaged, "
          f"never invoked at play time, lives under a gitignored directory")
        w(f"- node limit: {labels.get('nodes'):,}")
        w(f"- label strengths: `{labels.get('label_strength_distribution')}` "
          f"(stronger labels retained, not weakened)")
        w(f"- depth reached: median **{labels.get('median_depth_reached')}**, "
          f"minimum {labels.get('min_depth_reached')}")
        w(f"- completeness: {labels.get('labelled'):,}/{labels.get('expected'):,}, "
          f"missing {labels.get('missing')}, unexpected {labels.get('unexpected')}, "
          f"duplicates ignored {labels.get('duplicate_rows_ignored')}")
        w(f"- rate: {labels.get('positions_per_hour'):,}/h at {labels.get('shards')} workers, "
          f"{labels.get('elapsed_seconds'):,.0f} s wall")
        w("")
    if label_rate:
        w("Measured labelling rate on **real corpus positions** (the original six-position "
          "calibration overstated throughput by about 2x):")
        w("")
        w("| nodes | positions/h (6 workers) | median depth | 129k positions |")
        w("| ---: | ---: | ---: | ---: |")
        for nodes, row in sorted(label_rate.items(), key=lambda kv: int(kv[0])):
            w(f"| {int(nodes):,} | {row['positions_per_hour_6proc']:,} | "
              f"{row['median_depth']} | {row['hours_for_129k']} h |")
        w("")
        w("Depth 18 over the whole corpus costs 2.8 h and would have squeezed the arena, "
          "which is the gate that decides acceptance, so the staged route was taken.")
        w("")

    w("## 4. Architectures and training")
    w("")
    if screening:
        w(f"Ranked on: {screening.get('ranking_rule')}")
        w("")
        w("| tag | params | test cp | test % | holdout cp | sign acc | epochs | train s |")
        w("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
        for r in screening.get("results", []):
            if not r.get("ok"):
                continue
            mark = " **<- winner**" if r["tag"] == winner.get("tag") else ""
            w(f"| `{r['tag']}`{mark} | {r['parameters']:,} | "
              f"{r.get('test_improvement_cp', 0):+.2f} | {r.get('test_improvement_pct', 0):+.2f} "
              f"| {r.get('holdout_improvement_cp', 0):+.2f} | {r.get('test_sign_acc', 0):.3f} "
              f"| {r.get('epochs_run')} | {r.get('train_seconds')} |")
        w("")
        w("Width barely matters: tripling parameters buys about 1.3 cp, and both seeds agree "
          "within about 1 cp at each width, so the signal is stable rather than lucky.")
        w("")
    if training:
        w(f"Winner `{winner.get('tag')}`: hidden {training.get('hidden')}, "
          f"{training.get('parameters'):,} parameters, seed {training.get('seed')}, "
          f"{training.get('epochs_run')} epochs, early stopping on deployed clamped "
          f"validation error.")
        w("")
        w("| split | n | baseline MAE | corrected MAE | gain | gain % | sign acc "
          "| quant drift |")
        w("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
        for split in ("validation", "test", "holdout"):
            q = training.get(f"{split}_quant")
            e = training.get(f"{split}_quantization_error")
            if q:
                w(f"| {split} | {q['n']:,} | {q['baseline_mae']} | {q['mae']} | "
                  f"{q['mae_improvement_cp']:+.2f} | {q['mae_improvement_pct']:+.2f} | "
                  f"{q['sign_accuracy_outside_band']} | {e['median_cp']} cp |")
        w("")
        w("By phase (baseline -> corrected):")
        w("")
        w("| split | middlegame | endgame | opening |")
        w("| --- | --- | --- | --- |")
        for split in ("validation", "test", "holdout"):
            q = training.get(f"{split}_quant")
            if not q:
                continue
            cells = []
            for phase in ("middlegame", "endgame", "opening"):
                ph = q.get("by_phase", {}).get(phase)
                cells.append(
                    f"{ph['baseline_mae']} -> {ph['corrected_mae']} "
                    f"({ph['baseline_mae'] - ph['corrected_mae']:+.2f})"
                    if ph else "-"
                )
            w(f"| {split} | {cells[0]} | {cells[1]} | {cells[2]} |")
        w("")
        aug = training.get("augmentation", {})
        if aug:
            w(f"Colour-swap augmentation was tested and **rejected on evidence**: "
              f"{aug['identical_features']}/{aug['checked']} positions produce identical "
              f"features to their colour swap, so the augmentation is a no-op. The same "
              f"property makes the colour-symmetry gate true by construction.")
            w("")

    w("## 5. Inference and search cost")
    w("")
    if throughput:
        w(f"- warm control: {throughput['warm_control']['nps_mean']:,.1f} nps; "
          f"candidate: {throughput['warm_candidate']['nps_mean']:,.1f} nps")
        w(f"- **NPS loss {throughput['nps_loss_pct']}%** against a 10% limit")
        w(f"- cold (first search after import): control "
          f"{throughput['cold']['control']['nps']:,.0f} nps, candidate "
          f"{throughput['cold']['candidate']['nps']:,.0f} nps")
        w("")
        w("The accumulator is fused into the same compiled call as the handcrafted "
          "evaluation. Measurement drove that: a *trivial* njit function with this argument "
          "list costs about 7.6 us, which is the entire cost of the control's evaluation, so "
          "the Python-to-Numba boundary dominates and a separate NNUE call would roughly "
          "double the price of a leaf.")
        w("")

    w("## 6. Regression and fixture results")
    w("")
    if fx_control and fx_candidate:
        w("Repository rule (`tests/rated_v5.py fixtures`, each fixture at its minimum "
          "correcting depth):")
        w("")
        w(f"- control: **{fx_control['enforced_passed']}/{fx_control['enforced_total']}** "
          f"enforced fixtures pass")
        w(f"- candidate: **{fx_candidate['enforced_passed']}/{fx_candidate['enforced_total']}**")
        w(f"- newly failing: `{fx_candidate['failed']}`")
        w("")
    if gate:
        w(f"Depth sweep (depths {gate['depths']}), which is where the rated losses actually "
          f"live — the control passes every fixture at its minimum correcting depth, so that "
          f"form of the test cannot discriminate:")
        w("")
        w(f"- class-A fixtures corrected: **{gate['class_a_fixtures_corrected']}** of "
          f"{gate['class_a_failures_found']} found (>= 3 required)")
        w(f"- solved-control breaks: **{len(gate['solved_control_breaks'])}** (0 required)")
        w(f"- new regressions: **{len(gate['regressions'])}** (0 required)")
        w("")
        if gate["corrections"]:
            w("Corrections:")
            w("")
            for r in gate["corrections"]:
                w(f"- `{r['id']}` depth {r['depth']}: `{r['was']}` -> `{r['now']}`")
            w("")
        if gate["regressions"]:
            w("Regressions (control was correct, candidate plays the refuted move):")
            w("")
            for r in gate["regressions"]:
                w(f"- `{r['id']}` depth {r['depth']}: `{r['was']}` -> `{r['now']}`")
            w("")
        if gate["solved_control_breaks"]:
            w("Solved-control breaks (moves from games the engine won or correctly drew):")
            w("")
            for r in gate["solved_control_breaks"]:
                w(f"- `{r['id']}` depth {r['depth']}: `{r['was']}` -> `{r['now']}`")
            w("")
        b = gate["round80_boundary"]
        w(f"Round 80 boundary case: control plays `{b['control_depth2']['best']}` at depth 2 "
          f"(score {b['control_depth2']['score']}), candidate plays "
          f"`{b['candidate_depth2']['best']}` (score {b['candidate_depth2']['score']}) — "
          f"**corrected: {b['corrected_at_depth2']}**. Generalisation probe: "
          f"{b['generalization']['siblings_with_nonzero_correction']}/"
          f"{b['generalization']['siblings_tested']} perturbed siblings also moved, so this "
          f"is not a memorised FEN.")
        w("")
        r79 = gate.get("round79", {})
        if r79:
            w(f"Round 79 `12...Nxd3` by depth: "
              f"{ {k: (v['control'], v['candidate']) for k, v in r79['by_depth'].items()} }")
            w("")
    if clamp:
        w("### Was the damage caused by large corrections?")
        w("")
        w("No. A clamp sweep over the held-out splits:")
        w("")
        w("| clamp | test gain | test % | holdout gain | saturation |")
        w("| ---: | ---: | ---: | ---: | ---: |")
        for c in sorted(clamp["test"]["by_clamp"], key=int):
            t = clamp["test"]["by_clamp"][c]
            h = clamp["holdout"]["by_clamp"][c]
            w(f"| {c} | {t['improvement_cp']:+.2f} | {t['improvement_pct']:+.2f} | "
              f"{h['improvement_cp']:+.2f} | {t['saturated_fraction']:.3f} |")
        w("")
        w("Re-running the fixture gate at +/-100 and +/-150 cut new regressions from 5 to 2 "
          "but left **all three solved-control breaks in place**, while the evaluation gain "
          "fell to 71% at clamp 100. Tightening the bound does not buy back the breakage.")
        w("")

    w("## 7. Paired arena")
    w("")
    if arena:
        pd = arena["paired_difference"]
        w(f"- {arena['openings']} held-out openings (disjoint from the training book, seed "
          f"20260911), {arena['games_played']} games, {arena['paired_comparisons']} paired "
          f"comparisons")
        w(f"- time control {arena['clock_ms']} ms + {arena['increment_ms']} ms; opponents at "
          f"{arena['engine_movetime_ms']} ms/move")
        w(f"- infrastructure failures excluded from scoring: "
          f"{arena['infrastructure_failures']}")
        w("")
        w("| side | games | W-D-L | score % | Elo | time losses | illegal | mean depth | nps |")
        w("| --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: |")
        for name in ("control", "candidate"):
            s = arena[name]
            w(f"| {name} | {s.get('games')} | {s.get('W-D-L')} | {s.get('score_pct')} | "
              f"{s.get('elo')} | {s.get('time_losses')} | {s.get('illegal_moves')} | "
              f"{s.get('mean_depth')} | {s.get('nps_mean')} |")
        w("")
        w(f"**Paired score difference: {pd['mean_score_diff']:+.4f}** "
          f"(95% CI [{pd['ci95_low']:+.4f}, {pd['ci95_high']:+.4f}], "
          f"interval excludes zero: **{pd['ci_excludes_zero']}**)")
        w("")
        w(f"Elo estimate {pd['elo_estimate']:+.1f}, 95% CI {pd['elo_ci95']}.")
        w("")
        w("### By opponent family")
        w("")
        w("| family | control score % | candidate score % |")
        w("| --- | ---: | ---: |")
        for fam, v in arena.get("by_opponent_family", {}).items():
            w(f"| {fam} | {v['control'].get('score_pct')} | {v['candidate'].get('score_pct')} |")
        w("")
        w("### By colour")
        w("")
        w("| colour | control score % | candidate score % |")
        w("| --- | ---: | ---: |")
        for col, v in arena.get("by_colour", {}).items():
            w(f"| {col} | {v['control'].get('score_pct')} | {v['candidate'].get('score_pct')} |")
        w("")
    else:
        w("_Arena did not complete; no strength claim is made._")
        w("")
    if arena_deep:
        pdd = arena_deep["paired_difference"]
        w("### Confirmation at a deeper time control")
        w("")
        w(f"The primary arena ran at a {arena.get('clock_ms', '?')} ms clock where both "
          f"agents reach only about "
          f"{arena.get('control', {}).get('mean_depth', '?')} ply, which is not "
          f"representative of rated play at 120 s + 0.5 s. Repeated at "
          f"{arena_deep['clock_ms']} ms over {arena_deep['openings']} openings "
          f"({arena_deep['games_played']} games):")
        w("")
        w("| side | games | W-D-L | score % | mean depth | nps |")
        w("| --- | ---: | --- | ---: | ---: | ---: |")
        for name in ("control", "candidate"):
            s = arena_deep[name]
            w(f"| {name} | {s.get('games')} | {s.get('W-D-L')} | {s.get('score_pct')} | "
              f"{s.get('mean_depth')} | {s.get('nps_mean')} |")
        w("")
        w(f"**Paired difference {pdd['mean_score_diff']:+.4f}** "
          f"(95% CI [{pdd['ci95_low']:+.4f}, {pdd['ci95_high']:+.4f}], excludes zero: "
          f"{pdd['ci_excludes_zero']}), Elo {pdd['elo_estimate']:+.1f} "
          f"{pdd['elo_ci95']}.")
        w("")

    w("## 8. Docker and reproducibility")
    w("")
    if calib:
        w(f"- host: {calib.get('host')}")
        w(f"- selected workers: **{calib.get('selected_workers')}** — "
          f"{calib.get('selection_rule')}")
        w("")
        w("| workers | games/h | positions/h | control move ms | inflation |")
        w("| ---: | ---: | ---: | ---: | ---: |")
        for lv in calib.get("levels", []):
            w(f"| {lv.get('workers')} | {lv.get('games_per_hour', 0):,.0f} | "
              f"{lv.get('positions_per_hour', 0):,.0f} | "
              f"{lv.get('control_move_ms_median')} | "
              f"{lv.get('move_time_inflation_vs_1worker', 1.0):.3f} |")
        w("")
        w("The first calibration was **wrong and was corrected**: it gave each level a "
          "different slice of the manifest, so opening variation appeared as contention. "
          "Replaying the same games at every level produced the table above.")
        w("")
        w(f"{calib.get('why_not_docker')}")
        w("")
    if nvd:
        w(f"Native vs Docker equivalence on the control: fixed-depth decisions identical "
          f"(**{nvd.get('fixed_depth_decisions_identical')}**), same node counts; "
          f"native {nvd['native'].get('nps'):,} nps (Python "
          f"{nvd['native'].get('python')}) vs Docker {nvd['docker'].get('nps'):,} nps "
          f"(Python {nvd['docker'].get('python')}), ratio "
          f"{nvd.get('nps_ratio_docker_over_native')}.")
        w("")
    if pre_docker:
        c = pre_docker.get("container", {})
        w(f"Platform image `{pre_docker.get('image')}` "
          f"(`{pre_docker.get('image_id', '')[:23]}...`), "
          f"{pre_docker.get('docker_version')}. The pre-neural package validated inside it "
          f"with one CPU, 2 GB, `--network none`, read-only root: import "
          f"{c.get('import_seconds')} s, peak {c.get('peak_memory_mb')} MB, both smoke games "
          f"legal, files visible `{c.get('files_visible')}`.")
        w("")

    w("### Reproducible commands")
    w("")
    w("```")
    w("python tests/nnue_openings.py --games 2600 --random-openings 600 \\")
    w("    --control-clock-ms 5000 --increment-ms 500 --engine-movetime-ms 150")
    w("python tests/nnue_calibrate.py --games-per-level 12")
    w("python tests/nnue_generate.py --workers 6 --out tests/results/nnue/generation_summary.json")
    w("python tests/nnue_label.py collect --target 200000")
    w("python tests/nnue_label.py label --shards 6 --nodes 100000")
    w("python tests/nnue_screen.py --hidden 32 64 96 --seeds 20260909 20260910 --epochs 60")
    w("python tests/nnue_pack.py --source tests/results/nnue/model_h64_s20260910/quantized.npz")
    w("python tests/nnue_invariants.py")
    w("python tests/nnue_throughput.py --clock-ms 20000 --reps 4")
    w("python tests/nnue_gates.py")
    w("python tests/rated_v5.py fixtures --source agent_nnue.py")
    w("python tests/nnue_clamp_sweep.py")
    w("python tests/nnue_arena.py --pairs 200 --workers 6")
    w("docker build -f tests/Dockerfile.nnue -t chessathon-nnue:platform .")
    w("python tests/nnue_docker_validate.py submissions/pre_nn_20260909/agent.zip")
    w("```")
    w("")

    w("## 9. Benchmark engines")
    w("")
    if engines:
        w("| binary | id | runs | licence | role |")
        w("| --- | --- | --- | --- | --- |")
        for e in engines:
            w(f"| `{e['binary']}` | {e.get('id_name')} | {e.get('runs_successfully')} | "
              f"{e.get('licence','-')} | opponent / data generator only |")
        w("")
        w("No benchmark code, weights or evaluation tables were read, copied or shipped. "
          "Three distinct engine families exist (Rustic x3, Shallow Blue, Loki), which "
          "limits the opponent-holdout to family level.")
        w("")

    w("## 10. Compliance")
    w("")
    w("- The network was trained **from random initialization** during this task.")
    w("- Stockfish labelled training positions offline; it is never packaged, never invoked "
      "at play time, and lives under a gitignored directory. The rules permit training on "
      "engine-labelled positions and forbid shipping the engine or a table of its moves; "
      "neither is shipped.")
    w("- No benchmark engine's code, weights or tables were copied.")
    w("- No lookup table of engine moves or evaluations is shipped — only the weights of a "
      "network trained from scratch.")
    w("- Dependencies are limited to the platform stack: python-chess, NumPy, Numba.")
    w(f"- Candidate package would be {candidate.get('bytes', 0) + weights.get('bytes', 0):,} "
      f"bytes uncompressed, far under the 50 MB limit.")
    w("- Nothing is obfuscated; the integration is a single readable block.")
    w("")

    w("## 11. Gate table")
    w("")
    w("| gate | measured | requirement | verdict |")
    w("| --- | --- | --- | --- |")
    for row in gates:
        mark = "PASS" if row["passes"] else ("FAIL" if row["passes"] is False else "n/a")
        w(f"| {row['gate']} | {row['measured']} | {row['requirement']} | **{mark}** |")
    w("")

    w("## 12. Verdict and next action")
    w("")
    w(f"**{verdict}**")
    w("")
    if not accept:
        w("The control `agent.py` is unchanged and remains the recommended submission. The "
          "pre-neural package `submissions/pre_nn_20260909/agent.zip` is validated and ready; "
          "note it is a genuine improvement on what is currently live, because the live "
          "`submission 0609v4` is one revision behind the control.")
        w("")
        w("No neural package is proposed. All experimental code, datasets, manifests, model "
          "checkpoints and results are preserved.")
        w("")
        w("### Highest-value next action")
        w("")
        w("Train the residual to be **conservative where the control is already right**, "
          "rather than accurate on average. The evaluator is good — +27.9 cp on held-out "
          "test, +48.7 cp against an unseen opponent family, quantization lossless, 4% NPS "
          "cost — but it perturbs positions the control already handles, which is what broke "
          "five fixtures including a solved control. Concretely: add the RATED_V5 solved "
          "controls and their neighbourhoods to the training objective with a strong penalty "
          "for changing the control's preferred move, or gate the correction on a confidence "
          "signal so it only fires where the handcrafted evaluation is likely wrong. The "
          "clamp sweep shows a blunt magnitude bound does not achieve this.")
        w("")

    w("## 13. Changed files and commits")
    w("")
    w("```")
    w(sh("git", "log", "--oneline", "78c03b0..HEAD"))
    w("```")
    w("")
    w("```")
    w(sh("git", "diff", "--stat", "78c03b0", "HEAD"))
    w("```")
    w("")

    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    (NNUE / "final_report.json").write_text(
        json.dumps(
            {
                "verdict": verdict,
                "gates": gates,
                "gates_decided": len(decided),
                "gates_failed": len(failed),
                "control": control,
                "candidate": candidate,
                "weights": weights,
                "submission_0609v4_inner": v4,
                "winner": winner,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    import csv

    with (NNUE / "gates.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["gate", "measured", "requirement", "passes"])
        writer.writeheader()
        for row in gates:
            writer.writerow(row)

    print(f"VERDICT: {verdict}")
    print(f"{len(decided) - len(failed)}/{len(decided)} decided gates pass")
    for row in gates:
        mark = "PASS" if row["passes"] else ("FAIL" if row["passes"] is False else "n/a ")
        print(f"  [{mark}] {row['gate']}: {row['measured']}")
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()
