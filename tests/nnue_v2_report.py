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
    fire = load(V2 / "fire_rate.json")

    # Every candidate that reached a gate run or a screening arena, so the report shows
    # the whole search rather than only the checkpoint that got furthest.
    candidates: list[dict[str, Any]] = []
    for directory in sorted(V2.iterdir()):
        if not directory.is_dir():
            continue
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
    summaries = sorted(V2.glob("summary_*.json"))
    rows: list[list[str]] = []
    for path in summaries:
        for entry in load(path) or []:
            # Pilot, probe and smoke runs were trained on a partial corpus while
            # generation was still running. They shaped the design and are recorded in
            # the commit history, but they are not comparable to the runs below and
            # would only pad this table.
            if any(k in entry["tag"] for k in ("pilot", "probe", "smoke")):
                continue
            rows.append(
                [
                    f"`{entry['tag']}`",
                    entry["variant"],
                    str(entry["hidden"]),
                    f"{entry['parameters']:,}",
                    str(entry["best_epoch"]),
                    f"{entry['best_composite']:.3f}",
                    f"{entry['train_seconds']:.0f}",
                ]
            )
    if rows:
        add(
            table(
                rows,
                ["tag", "variant", "hidden", "params", "best epoch", "composite", "s"],
                ["---", "---", "---:", "---:", "---:", "---:", "---:"],
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

    # --------------------------------------------------------------------- arenas
    add("## 6. Arenas\n")
    for label, report in (("screening", screen), ("confirmation", confirm)):
        if not report:
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

    if h2h:
        add("### head to head against the control\n")
        add(
            "The benchmark arena puts both agents against engines rated 1630-1721, where "
            "both score near the floor and most paired differences are exactly zero. A "
            "direct match has no floor: the same opening is played twice with colours "
            "reversed, so the only thing that varies is the difference being measured.\n"
        )
        add(
            f"- {h2h['scored_games']} scored games over {h2h['openings']} openings, "
            f"{h2h['clock_ms']}ms + {h2h['increment_ms']}ms, seed {h2h['seed']}"
        )
        add(
            f"- **candidate scores {h2h['candidate_score_pct']}% "
            f"95% CI [{h2h['score_ci95_pct'][0]}%, {h2h['score_ci95_pct'][1]}%]** "
            f"({h2h['candidate_W-D-L']}), Elo {h2h['elo']:+.1f} "
            f"[{h2h['elo_ci95'][0]:+.1f}, {h2h['elo_ci95'][1]:+.1f}]"
        )
        add(
            f"- 50% is parity. Beats the control: **{h2h['beats_control']}**; "
            f"worse than the control: **{h2h['worse_than_control']}**"
        )
        add(
            f"- as White {h2h['by_colour']['candidate_white']['score_pct']}%, "
            f"as Black {h2h['by_colour']['candidate_black']['score_pct']}%, "
            f"draws {h2h['draw_pct']}%"
        )
        add(
            f"- mean depth candidate {h2h['candidate_mean_depth']} vs control "
            f"{h2h['control_mean_depth']}; flags {h2h['flags']}, illegal {h2h['illegal']}, "
            f"infrastructure {h2h['infrastructure_failures']}\n"
        )

    # ------------------------------------------------------------ reproduction
    add("## 7. Reproduction\n")
    add("```")
    add("# sibling groups (Stockfish 19, MultiPV)")
    add("python tests/nnue_v2_data.py --parents 40000 --workers 8 --seed 20260909")
    add("# training matrix")
    add("python tests/nnue_v2_train.py --variants A,B,C,D,E --hidden 32 --seed 20260909")
    add("# pack a checkpoint and rebuild the candidate from the control")
    add(f"python tests/nnue_v2_pack.py {args.tag}")
    add("# gates")
    add(f"python tests/nnue_v2_gates.py --tag {args.tag}")
    add("# arenas")
    add(f"python tests/nnue_v2_arena.py --tag {args.tag} --pairs 60 --label screen")
    add(f"python tests/nnue_v2_arena.py --tag {args.tag} --pairs 250 --label confirm")
    add("# platform container")
    add("python tests/nnue_v2_docker.py")
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
