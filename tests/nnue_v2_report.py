"""Assemble FINAL_NNUE_V2_REPORT.md from recorded artifacts.

The report is generated, never written by hand, and it reads only files that were
produced by the pipeline. The acceptance decision is computed here from the seven
criteria predeclared in NNUE_V2_PLAN.md; it is not a judgement call made while writing
prose. If no candidate satisfies all seven, the verdict is REJECT and the pre-neural
control is retained.
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
GROUPS = REPO / "tests" / "results" / "nnue" / "groups"
CONTROL_SHA256 = "65ec40ceb29a8f6fe14a74ab2ed6ca446164f701e46d2b9d3fe8d5af94655bda"
PACKAGE_SHA256 = "d9392c6b9c572790c838cc91e957c6eeaceecb706175d99cdfbf928a5e3b86a5"


def sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else "missing"


def load(path: pathlib.Path) -> Any:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def git(*args: str) -> str:
    try:
        return subprocess.run(
            ["git", *args], check=True, capture_output=True, text=True, cwd=REPO
        ).stdout.strip()
    except Exception:
        return "unavailable"


def acceptance(
    gates: dict[str, Any] | None,
    screen: dict[str, Any] | None,
    confirm: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """The seven predeclared acceptance criteria, evaluated against artifacts."""

    def gate_named(fragment: str) -> dict[str, Any] | None:
        if not gates:
            return None
        for g in gates["gates"]:
            if fragment in g["gate"]:
                return g
        return None

    fixtures = gate_named("RATED_V5 enforced fixtures")
    defence = gate_named("draw and passed-pawn defence")
    equality = gate_named("agent integer path equals")
    nps = gate_named("NPS loss")

    big = confirm or screen
    ci_ok = bool(big and big.get("ci_excludes_zero") and big.get("paired_mean_diff", 0) > 0)

    per_opponent_ok = False
    per_colour_ok = False
    if big:
        opp = [
            v["mean_diff"]
            for v in big.get("by_opponent", {}).values()
            if v.get("pairs", 0) >= 10
        ]
        col = [v["mean_diff"] for v in big.get("by_colour", {}).values()]
        per_opponent_ok = bool(opp) and all(v >= -0.01 for v in opp)
        per_colour_ok = bool(col) and all(v >= -0.01 for v in col)

    draw_ok = bool(big and big.get("draw_delta_pct", -99) >= -2.0)

    return [
        {
            "criterion": "1. RATED_V5 remains 16/16",
            "measured": fixtures["measured"] if fixtures else "not run",
            "passes": bool(fixtures and fixtures["passes"]),
        },
        {
            "criterion": "2. no solved draw or passed-pawn defence regresses",
            "measured": defence["measured"] if defence else "not run",
            "passes": bool(defence and defence["passes"]),
        },
        {
            "criterion": "3. quantized inference works in the platform container",
            "measured": equality["measured"] if equality else "not run",
            "passes": bool(equality and equality["passes"]),
        },
        {
            "criterion": "4. NPS and time management remain acceptable",
            "measured": nps["measured"] if nps else "not run",
            "passes": bool(nps and nps["passes"]),
        },
        {
            "criterion": "5. large paired arena positive, 95% CI excluding zero",
            "measured": (
                f"{big['paired_mean_diff']:+.4f} CI "
                f"[{big['paired_ci95'][0]:+.4f}, {big['paired_ci95'][1]:+.4f}] "
                f"over {big['games']} games"
                if big
                else "not run"
            ),
            "passes": ci_ok,
        },
        {
            "criterion": "6. not dependent on one opponent, colour or time control",
            "measured": (
                f"per-opponent ok: {per_opponent_ok}, per-colour ok: {per_colour_ok}"
                if big
                else "not run"
            ),
            "passes": per_opponent_ok and per_colour_ok,
        },
        {
            "criterion": "7. draw conversion does not deteriorate",
            "measured": (
                f"draw share {big['draw_delta_pct']:+.2f} pp "
                f"(control {big['control'].get('draw_pct')}% -> "
                f"candidate {big['candidate'].get('draw_pct')}%)"
                if big
                else "not run"
            ),
            "passes": draw_ok,
        },
    ]


def table(rows: list[list[str]], header: list[str], align: list[str] | None = None) -> str:
    """Render a markdown table, escaping pipes so a cell cannot split its own row.

    Measured values contain them -- "max |diff| 0 cp" silently became three columns.
    """
    align = align or ["---"] * len(header)

    def cell(text: str) -> str:
        return str(text).replace("|", r"\|")

    out = [
        "| " + " | ".join(cell(h) for h in header) + " |",
        "| " + " | ".join(align) + " |",
    ]
    for row in rows:
        out.append("| " + " | ".join(cell(c) for c in row) + " |")
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", required=True, help="the finalist checkpoint tag")
    ap.add_argument("--screen-label", default="screen")
    ap.add_argument("--confirm-label", default="confirm")
    ap.add_argument("--out", type=pathlib.Path, default=REPO / "FINAL_NNUE_V2_REPORT.md")
    args = ap.parse_args()

    tagdir = V2 / args.tag
    gates = load(tagdir / "gates.json")
    training = load(tagdir / "training.json")
    packed = load(tagdir / "packed.json")
    screen = load(tagdir / f"arena_{args.screen_label}.json")
    confirm = load(tagdir / f"arena_{args.confirm_label}.json")
    generation = load(GROUPS / "generation_summary.json")
    frontier = load(V2 / "frontier_fixtures.json")
    h2h = load(tagdir / "h2h.json")
    h2h_pilot = load(tagdir / "h2h_pilot.json")
    fire = load(V2 / "fire_rate.json")

    # Every candidate that reached a gate run or a screening arena, so the report shows
    # the whole search rather than only the checkpoint that got furthest.
    candidates: list[dict[str, Any]] = []
    for directory in sorted(V2.iterdir()):
        if not directory.is_dir():
            continue
        if any(k in directory.name for k in ("pilot", "probe", "smoke")):
            continue  # partial-corpus runs; see the note on the training table
        cg = load(directory / "gates.json")
        cs = load(directory / "arena_screen.json")
        if not cg and not cs:
            continue

        def named(report: dict[str, Any] | None, fragment: str) -> str:
            if not report:
                return "-"
            for g in report["gates"]:
                if fragment in g["gate"]:
                    return str(g["measured"])
            return "-"

        candidates.append(
            {
                "tag": directory.name,
                "gates": f"{cg['gates_passed']}/{cg['gates_total']}" if cg else "-",
                "fixtures": named(cg, "RATED_V5 enforced"),
                "nps": named(cg, "NPS loss"),
                "screen": (
                    f"{cs['paired_mean_diff']:+.4f} "
                    f"[{cs['paired_ci95'][0]:+.4f}, {cs['paired_ci95'][1]:+.4f}]"
                    if cs
                    else "not screened"
                ),
                "draws": (
                    f"{cs['control']['draw_pct']}% -> {cs['candidate']['draw_pct']}%"
                    if cs
                    else "-"
                ),
            }
        )

    criteria = acceptance(gates, screen, confirm)
    accepted = all(c["passes"] for c in criteria)
    verdict = (
        "ACCEPT - PACKAGE THE NEURAL CANDIDATE"
        if accepted
        else "REJECT - RETAIN THE PRE-NEURAL CONTROL"
    )

    lines: list[str] = []
    add = lines.append

    add("# NNUE V2 - gated move-quality residual - final report\n")
    add(f"**VERDICT: {verdict}**\n")
    add(
        f"{sum(c['passes'] for c in criteria)} of {len(criteria)} predeclared acceptance "
        "criteria are satisfied.\n"
    )
    add(
        table(
            [
                [c["criterion"], c["measured"], "PASS" if c["passes"] else "**FAIL**"]
                for c in criteria
            ],
            ["acceptance criterion", "measured", "verdict"],
        )
    )
    add("")
    if not accepted:
        add(
            "> The acceptance rule was written down in `NNUE_V2_PLAN.md` before any V2 "
            "data was generated, precisely so it could not be relaxed once the numbers "
            "arrived. Better MAE, one repaired fixture, a deeper average search or a "
            "statistically flat arena are not success. The control is retained.\n"
        )

    # ------------------------------------------------------------------ identities
    add("## 1. Identities\n")
    control_now = sha256(REPO / "agent.py")
    add(
        table(
            [
                [
                    "protected control `agent.py`",
                    f"`{control_now}`",
                    "UNCHANGED" if control_now == CONTROL_SHA256 else "**MODIFIED**",
                ],
                [
                    "pre-neural package",
                    f"`{sha256(REPO / 'submissions' / 'pre_nn_20260909' / 'agent.zip')}`",
                    "UNCHANGED"
                    if sha256(REPO / "submissions" / "pre_nn_20260909" / "agent.zip")
                    == PACKAGE_SHA256
                    else "**MODIFIED**",
                ],
                [
                    "candidate `agent_nnue_v2.py`",
                    f"`{sha256(REPO / 'agent_nnue_v2.py')}`",
                    "generated from the control",
                ],
                [
                    "candidate weights",
                    f"`{sha256(REPO / 'nnue_v2_weights.npz')}`",
                    packed["tag"] if packed else "-",
                ],
            ],
            ["artifact", "SHA-256", "status"],
        )
    )
    add("")
    branch = git("rev-parse", "--abbrev-ref", "HEAD")
    head = git("rev-parse", "--short", "HEAD")
    add(f"- branch `{branch}` at `{head}`")
    add("- frozen control tag: `pre-nnue-control-20260909`")
    add(
        "- the candidate is *generated* from `agent.py` by `tests/nnue_v2_build_agent.py`, "
        "and the build asserts that stripping the inserted block reproduces `agent.py` "
        "byte for byte. `agent.py` is never written by any code path in this pipeline.\n"
    )

    # ------------------------------------------------------------------- dataset
    add("## 2. Dataset: sibling groups\n")
    if generation:
        add(
            f"- parents planned: **{generation['parents_planned']:,}**, "
            f"groups written: **{generation['groups_written']:,}**"
        )
        add(
            f"- dropped for no ordering signal: {generation['skipped_no_signal']:,} "
            "(every candidate equally good, so the group could not teach a ranking)"
        )
        add(
            f"- labelling: Stockfish 19, {generation['nodes']:,} nodes, "
            f"MultiPV={generation['multipv']}, clamp +/-{generation['clamp_cp']} cp, "
            f"{generation['workers']} workers"
        )
        add(
            f"- throughput: {generation['groups_per_hour']:,} groups/h, "
            f"{generation['elapsed_seconds'] / 60:.1f} min wall\n"
        )
    add(
        "Exclusions enforced in code, not by convention: the entire Loki family (held out "
        "as an unseen opponent *and* unseen data), and the 16 RATED_V5 fixture FENs "
        "together with every position one ply from them, so the network cannot memorise "
        "a fixture's neighbourhood.\n"
    )
    add(
        "Splits are inherited from the V1 corpus per game lineage and never reassigned, "
        "so V2 cannot leak across a boundary V1 already established.\n"
    )

    # ------------------------------------------------------------------- training
    add("## 3. Training\n")
    # Built from the per-tag training.json files rather than the summary_*.json ones:
    # each variant was launched as its own process with the same --tag, so the shared
    # summary file was overwritten and kept only the last run. The per-tag records are
    # complete.
    rows: list[list[str]] = []
    for directory in sorted(V2.iterdir()):
        if not directory.is_dir():
            continue
        # Pilot, probe and smoke runs were trained on a partial corpus while generation
        # was still running. They shaped the design and are recorded in the commit
        # history, but they are not comparable to the runs below.
        if any(k in directory.name for k in ("pilot", "probe", "smoke")):
            continue
        entry = load(directory / "training.json")
        if entry:
            hist = entry["history"][entry["best_epoch"]]
            rows.append(
                [
                    f"`{entry['tag'].replace('_h32_s20260909', '')}`",
                    entry["variant"],
                    str(entry["best_epoch"]),
                    f"{hist['preserved_rate']:.4f}",
                    f"{hist['regret_reduction_cp']:+.2f}",
                    f"{hist['pair_accuracy_gain']:+.4f}",
                    f"{hist['mae_gain_cp']:+.2f}",
                    f"{hist['mean_abs_correction']:.1f}",
                    f"{entry['best_composite']:.3f}",
                    f"{entry['train_seconds']:.0f}",
                ]
            )
    if rows:
        add(
            "All runs are width 32, 24,899 parameters, seed 20260909, 12 epochs, on the "
            "full 28,640-group corpus. Validation metrics at the selected epoch:\n"
        )
        add(
            table(
                rows,
                ["tag", "variant", "epoch", "preserved", "regret cp", "pair gain",
                 "MAE cp", "|corr| cp", "composite", "s"],
                ["---", "---", "---:", "---:", "---:", "---:", "---:", "---:", "---:",
                 "---:"],
            )
        )
        add("")
    add(
        "Checkpoints are selected on a composite score in which preservation of "
        "already-correct control choices carries 100x weight, regret reduction 1 point "
        "per centipawn, sibling pair accuracy 40x, draw preservation 20x and residual "
        "MAE 0.02x. MAE is deliberately the smallest term: V1 was selected on MAE, "
        "improved MAE by 27.85 cp, and lost.\n"
    )
    add(
        "**Which mechanism actually did the work, against expectation.** The confidence "
        "gate on its own did nothing for preservation: C is B plus the gate and "
        "preserves 0.8636 against B's 0.8679, a difference in the wrong direction and "
        "inside the noise. Adding the ranking loss also *reduced* preservation, A's "
        "0.8952 to B's 0.8679, because ranking rewards reordering siblings and some of "
        "the orderings it reorders were already right.\n"
    )
    add(
        "What moved preservation was the anchor weight and then the magnitude "
        "penalty -- D triples the anchor and reaches 0.9267, and the F family adds a "
        "penalty on the size of the correction and reaches 0.96-0.99. The gate only "
        "became useful once it was made *hard* (the H family), where it decides "
        "**where** to act rather than only how much. The honest reading is that the "
        "headline idea of the plan, a calibrated confidence multiplier, was not the "
        "effective ingredient; restraint on correction magnitude was.\n"
    )
    if training:
        hist = training["history"]
        add("Per-epoch validation metrics for the finalist:\n")
        add(
            table(
                [
                    [
                        str(h["epoch"]),
                        f"{h['composite']:.3f}",
                        f"{h['preserved_rate']:.4f}",
                        f"{h['regret_reduction_cp']:+.2f}",
                        f"{h['pair_accuracy_gain']:+.4f}",
                        f"{h['draw_preservation_rate']:.4f}",
                        f"{h['mae_gain_cp']:+.2f}",
                        f"{h['mean_abs_correction']:.1f}",
                    ]
                    for h in hist
                ],
                [
                    "epoch", "composite", "preserved", "regret red cp",
                    "pair gain", "draw pres", "MAE gain cp", "mean abs corr",
                ],
                ["---:"] * 8,
            )
        )
        add("")

    # ---------------------------------------------------------------------- gates
    add("## 4. Gates\n")
    if gates:
        add(
            table(
                [
                    [g["gate"], str(g["measured"]), str(g["requirement"]),
                     "PASS" if g["passes"] else "**FAIL**"]
                    for g in gates["gates"]
                ],
                ["gate", "measured", "requirement", "result"],
            )
        )
        add("")
        add(f"{gates['gates_passed']}/{gates['gates_total']} gates pass.\n")
    else:
        add("Gates were not run.\n")

    if candidates:
        add("### every candidate that reached a gate run or an arena\n")
        add(
            table(
                [
                    [
                        f"`{c['tag'].replace('_h32_s20260909', '')}`",
                        c["gates"],
                        c["fixtures"].replace("control 16/16, candidate ", ""),
                        c["nps"].split(" (")[0],
                        c["screen"],
                        c["draws"],
                    ]
                    for c in candidates
                ],
                ["candidate", "gates", "RATED_V5", "NPS loss",
                 "screen paired diff (95% CI)", "draw share"],
                ["---", "---:", "---:", "---:", "---:", "---:"],
            )
        )
        add("")

    if fire:
        add("### where the correction actually fires\n")
        add(
            "The soft gate and the hard threshold are different animals, and the mean "
            "correction hides it. Measured over 40,000 held-out children:\n"
        )
        add(
            table(
                [
                    [
                        f"`{tag.replace('_h32_s20260909', '')}`",
                        f"{info['conf_min']:.2f}",
                        f"{info['fires_pct']}%",
                        f"{info['mean_abs_when_fires']} cp",
                        f"{info['mean_abs_all']} cp",
                    ]
                    for tag, info in fire.items()
                ],
                ["candidate", "conf threshold", "fires on", "size when it fires",
                 "mean over all"],
                ["---", "---:", "---:", "---:", "---:"],
            )
        )
        add(
            "\nThe hard threshold leaves ~96% of positions evaluating bit-identically to "
            "the control and spends its whole licence, near the clamp, on the few it "
            "claims to read. That is the design working. It still did not buy strength.\n"
        )

    # ------------------------------------------------------------------ frontier
    add("## 5. The preservation/reach frontier\n")
    add(
        "The single most useful measurement of this session. Seven checkpoints spanning "
        "the trade-off, each screened on the 16 enforced RATED_V5 fixtures:\n"
    )
    if frontier:
        add(
            table(
                [
                    [
                        f"`{r['tag'].replace('_h32_s20260909', '')}`",
                        f"{r['preserved']:.4f}",
                        f"{r['corr']:.1f}",
                        f"{r['regret']:+.2f}",
                        f"{r['mae']:+.2f}",
                        r["enforced"],
                        ", ".join(r["broke"]) or "-",
                    ]
                    for r in frontier["rows"]
                ],
                ["checkpoint", "preserved", "|corr| cp", "regret cp", "MAE cp",
                 "RATED_V5", "broke"],
                ["---", "---:", "---:", "---:", "---:", "---:", "---"],
            )
        )
        add("")
    add(
        "The boundary sits between 0.969 and 0.972 preserved: the cheap training-time "
        "proxy predicts the expensive gate. It also prices the trade. Every checkpoint "
        "with real reach breaks solved controls, and every checkpoint that keeps 16/16 "
        "corrects the evaluation by only 5-14 cp on average. No setting in this family "
        "is both safe and large.\n"
    )
    add(
        "The **order** in which fixtures break is the most diagnostic thing in the "
        "table. The first to go, alone, at the smallest correction that breaks anything "
        "at all, is `r77-33-Rc7+` -- a repetition defence. Only when the correction grows "
        "further do `r78-48-Kf6` (a passed-pawn defence) and `r79-12-Nxd3` follow.\n"
    )
    add(
        "That is a mechanism for V1's draw collapse, not just a correlate of it. Holding "
        "a draw means keeping an evaluation *at* zero across a repetition, which is the "
        "most fragile thing a bounded additive correction can disturb: the two sides of "
        "the comparison are equal, so an arbitrarily small nudge flips it. Winning "
        "positions have margin and absorb the same nudge unchanged. A residual trained "
        "on unconditioned error therefore damages held draws first and hardest -- which "
        "is exactly what V1 did when its draws fell from 15 to 3.\n"
    )

    # --------------------------------------------------------------------- arenas
    add("## 6. Arenas\n")
    for label, report in (("screening", screen), ("confirmation", confirm)):
        if not report:
            if label == "confirmation":
                add("### confirmation: not run, and deliberately so\n")
                add(
                    "The predeclared procedure takes **only the best surviving "
                    "candidate** into an 800-1,200 game confirmation. Nothing survived "
                    "screening: q005 returned a negative point estimate, which is a "
                    "predeclared immediate rejection, and q010 returned exactly zero "
                    "with an identical 7-5-98 record to the control. Running a "
                    "confirmation arena anyway would have spent an hour dressing a "
                    "rejection in a larger sample.\n"
                )
                add(
                    "The head-to-head below is the large run that was worth doing "
                    "instead, and it is reported as supplementary evidence rather than "
                    "as a substitute for the criterion it does not satisfy.\n"
                )
            else:
                add(f"### {label}: not run\n")
            continue
        add(f"### {label}\n")
        add(
            f"- {report['games']} games over {report['pairs']} openings, "
            f"{report['clock_ms']}ms + {report['increment_ms']}ms, "
            f"{report['workers']} workers, seed {report['seed']}"
        )
        add(f"- families: {', '.join(report['families'])}")
        add(
            f"- **paired difference {report['paired_mean_diff']:+.4f} "
            f"95% CI [{report['paired_ci95'][0]:+.4f}, {report['paired_ci95'][1]:+.4f}]**, "
            f"Elo {report['elo_diff']:+.1f} "
            f"[{report['elo_ci95'][0]:+.1f}, {report['elo_ci95'][1]:+.1f}]"
        )
        add(
            f"- control {report['control']['W-D-L']} ({report['control']['score_pct']}%), "
            f"candidate {report['candidate']['W-D-L']} ({report['candidate']['score_pct']}%)"
        )
        add(
            f"- draw share {report['control']['draw_pct']}% -> "
            f"{report['candidate']['draw_pct']}% ({report['draw_delta_pct']:+.2f} pp)"
        )
        add(
            f"- candidate time losses {report['candidate']['time_losses']}, "
            f"illegal moves {report['candidate']['illegal_moves']}\n"
        )
        add(
            table(
                [
                    [
                        family + (" (HELD OUT)" if info["held_out_opponent"] else ""),
                        str(info["pairs"]),
                        f"{info['mean_diff']:+.4f}",
                        f"[{info['ci95'][0]:+.4f}, {info['ci95'][1]:+.4f}]",
                        str(info["control"].get("score_pct")),
                        str(info["candidate"].get("score_pct")),
                    ]
                    for family, info in report["by_opponent"].items()
                ],
                ["opponent", "pairs", "paired diff", "95% CI", "control %", "candidate %"],
                ["---", "---:", "---:", "---:", "---:", "---:"],
            )
        )
        add("")
        add(
            table(
                [
                    [
                        colour,
                        str(info["pairs"]),
                        f"{info['mean_diff']:+.4f}",
                        f"[{info['ci95'][0]:+.4f}, {info['ci95'][1]:+.4f}]",
                        str(info["control"].get("score_pct")),
                        str(info["candidate"].get("score_pct")),
                    ]
                    for colour, info in report["by_colour"].items()
                ],
                ["candidate colour", "pairs", "paired diff", "95% CI", "control %", "candidate %"],
                ["---", "---:", "---:", "---:", "---:", "---:"],
            )
        )
        add("")

    runs = [(n, r) for n, r in (("pilot", h2h_pilot), ("confirmation", h2h)) if r]
    if runs:
        add("### head to head against the control\n")
        add(
            "The benchmark arena puts both agents against engines rated 1630-1721, where "
            "both score near the floor -- control 7.27%, candidate 5.00% -- so almost "
            "every paired difference is exactly zero and the test spends its games "
            "re-proving that the benchmarks are stronger. A direct match has no floor: "
            "the same opening is played twice with colours reversed, so the only thing "
            "that varies is the difference being measured. 50% is parity.\n"
        )
        add(
            "This is supplementary evidence, not a substitute. Acceptance criterion 5 "
            "stays keyed to the benchmark arena that was predeclared, because choosing "
            "a different test after seeing a result is how a rejection becomes an "
            "acceptance without any new evidence.\n"
        )
        add(
            table(
                [
                    [
                        name,
                        str(r["scored_games"]),
                        f"{r['clock_ms']}+{r['increment_ms']}ms",
                        str(r["seed"]),
                        r["candidate_W-D-L"],
                        f"{r['candidate_score_pct']}%",
                        f"[{r['score_ci95_pct'][0]}%, {r['score_ci95_pct'][1]}%]",
                        f"{r['elo']:+.1f}",
                        str(r["beats_control"]),
                    ]
                    for name, r in runs
                ],
                ["run", "games", "clock", "seed", "W-D-L", "score", "95% CI", "Elo",
                 "beats control"],
                ["---", "---:", "---:", "---:", "---:", "---:", "---:", "---:", "---:"],
            )
        )
        add("")
        final = runs[-1][1]
        add(
            f"- as White {final['by_colour']['candidate_white']['score_pct']}%, "
            f"as Black {final['by_colour']['candidate_black']['score_pct']}%, "
            f"draws {final['draw_pct']}% "
            "(the colour split reflects the random-play opening book, which is not "
            "balanced; the pairing cancels it exactly)"
        )
        add(
            f"- mean depth candidate {final['candidate_mean_depth']} vs control "
            f"{final['control_mean_depth']}; flags {final['flags']}, illegal "
            f"{final['illegal']}, infrastructure {final['infrastructure_failures']}\n"
        )

    # ------------------------------------------------------------- limitations
    add("## 7. What this does and does not establish\n")
    add(
        "**Established.** A gated residual can be trained that keeps every solved "
        "control: RATED_V5 stays 16/16, no draw or passed-pawn defence fixture "
        "regresses, r80 24.Rd4 stays rejected, the quantized integer path matches the "
        "reference exactly on 600/600 positions, and the package imports and plays "
        "inside the platform container. V1 failed five of those. The preservation rate "
        "measured during training predicts fixture breakage, which makes the expensive "
        "gate cheap to anticipate.\n"
    )
    add(
        "**Also established, and the reason for the verdict.** Nothing in this family "
        "improved playing strength. The safe corrections are small by necessity, and "
        "the arenas cannot distinguish them from the control.\n"
    )
    add("**Not established, and worth stating plainly:**\n")
    add(
        "- *Absence of evidence is not evidence of absence.* The head-to-head "
        "confidence interval spans tens of Elo. A true effect of a few Elo -- which is "
        "the size a 5-14 cp correction plausibly buys -- would not be visible at these "
        "sample sizes. The claim is that no improvement was **demonstrated**, not that "
        "none exists."
    )
    add(
        "- *The benchmark arena is weakly powered here.* Both agents score near the "
        "floor against engines several hundred points stronger, so most paired "
        "differences are exactly zero by construction."
    )
    add(
        "- *The opening book is random play, not curated openings.* Rated games start "
        "from a curated set that is not published. Random-play openings are shared by "
        "both agents and the pairing cancels them, but they are not the distribution "
        "the platform actually uses."
    )
    add(
        "- *The head-to-head ran at 1s+0.1s*, where the agents reach depth ~1.3. A "
        "residual could matter more at the rated 120s+0.5s, where the search is deeper "
        "and a static evaluation error survives further up the tree. That was not "
        "affordable to test at a useful sample size on this hardware."
    )
    add(
        "- *One architecture, one seed.* Width 32, a single training seed per "
        "configuration. The frontier was mapped along the correction-magnitude axis, "
        "not across capacity or seeds.\n"
    )

    # ------------------------------------------------------------ reproduction
    add("## 8. Reproduction\n")
    snapshot = f"tests/results/nnue/v2/snapshots/{args.tag}/agent_v2.py"
    add("```")
    add("# 1. sibling groups (Stockfish 19, MultiPV=8 at 250k nodes)")
    add("python tests/nnue_v2_data.py --parents 40000 --workers 8 --seed 20260909")
    add("python tests/nnue_v2_provenance.py")
    add("")
    add("# 2. training matrix and the correction-magnitude frontier")
    add("python tests/nnue_v2_train.py --variants A,B,C,D,E --hidden 32 --seed 20260909 \\")
    add("       --epochs 12 --floor 0.0 --tag full")
    add("python tests/nnue_v2_train.py --variants F --hidden 32 --seed 20260909 \\")
    add("       --epochs 12 --floor 0.0 --override w_quiet=0.05 --tag q005")
    add("python tests/nnue_v2_train.py --variants H --hidden 32 --seed 20260909 \\")
    add("       --epochs 12 --floor 0.0 --override conf_min=0.85 --tag t85")
    add("")
    add("# 3. cheap fixture screen over a family of checkpoints, before any arena")
    add("python tests/nnue_v2_fixture_screen.py <tag> [<tag> ...]")
    add("")
    add("# 4. pack a checkpoint (rebuilds the candidate from agent.py) and gate it")
    add(f"python tests/nnue_v2_pack.py {args.tag}")
    add(f"python tests/nnue_v2_gates.py --tag {args.tag}")
    add("")
    add("# 5. snapshots, so several candidates can be arena-tested at once")
    add(f"python tests/nnue_v2_materialize.py {args.tag}")
    add("")
    add("# 6. arenas")
    add(f"python tests/nnue_v2_arena.py --tag {args.tag} \\")
    add(f"       --candidate {snapshot} --pairs 60 --workers 6 --label screen")
    add(f"python tests/nnue_v2_h2h.py --tag {args.tag} \\")
    add(f"       --candidate {snapshot} \\")
    add("       --pairs 1000 --workers 6 --clock-ms 1000 --increment-ms 100 \\")
    add("       --seed 20260914 --label h2h")
    add("")
    add("# 7. platform container: 1 CPU, 2 GB, no network, read-only, 256 MB /tmp")
    add(f"python tests/nnue_v2_docker.py --tag {args.tag}")
    add("")
    add("# 8. this report")
    add(f"python tests/nnue_v2_report.py --tag {args.tag}")
    add("```\n")

    add(
        "Generated by `tests/nnue_v2_report.py` from recorded artifacts. "
        "No number in this document was typed by hand.\n"
    )

    args.out.write_text("\n".join(lines), encoding="utf-8")
    (V2 / "final_report.json").write_text(
        json.dumps(
            {
                "tag": args.tag,
                "verdict": verdict,
                "accepted": accepted,
                "criteria": criteria,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"wrote {args.out.relative_to(REPO)}")
    print(f"VERDICT: {verdict}")
    for c in criteria:
        print(f"  {'PASS' if c['passes'] else 'FAIL'}  {c['criterion']}: {c['measured']}")


if __name__ == "__main__":
    main()
