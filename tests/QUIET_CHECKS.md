# Bounded quiet-check experiment

The primary control is the retained Numba engine,
SHA-256 `59f99079f1db99221683dd3f06391f4fc502c1dae11fb712b08170242649830a`.
It is frozen in `numba_checkpoint/<sha>/agent.py`. The experimental candidate is
`55ea89aef94b31ab53e026469864bbe04a3c0d50d9362c773306a9001978eddd`, frozen in
`quiet_candidate/<sha>/agent.py`. `quiet_experiment.json` pins both identities.
The original submitted `4551f4e...` is only the historical evaluation oracle.

## Change and bound

Normal search enters quiescence with one optional quiet check available.
Captures and promotions preserve the budget. A noncapture, nonpromotion check
consumes it, including a countercheck used as an evasion. No child replenishes
the budget. This is independent of absolute search ply and root position mode.
The development toggle chooses either the original rule or the new rule;
they do not operate simultaneously. Evaluation, caches, time allocation, TT
logic, repetition and passive SCOPE are unchanged. `ADAPTIVE=False` and
`PASSIVE=True` in both engines. No gameplay opening assets were added.

There can be at most one **optional** quiet checking move per quiescence line.
Mandatory evasions always remain eligible, including a quiet countercheck when
the budget is already zero. A literal bound of one on *all* quiet checks would
conflict with the requirement to retain every legal evasion. The dedicated
countercheck test covers that exception. The existing `MAX_PLY=96`, terminal
checks, draw detection and deadline checks remain the overall safeguards.

The tests instrument only development copies. They audit budget monotonicity,
legal moves, optional-check count, capture/promotion checks, pins, all evasion
candidates, near-maximum ply, repetition, fifty moves and board/state restoration
after a deadline exception. Alpha-beta cutoffs still apply to legal evasions.

## Tactical findings

The frozen control reproduces Bf4 at fixed depth three: +31 cp, 15,315 nodes.
The candidate selects Qh5 at -23 cp with 19,527 nodes. The ordinary completed
root search reports Bf4 at an upper bound of -84 cp; a separate full-window
depth-three analysis gives **-338 cp**, versus the control's +31 cp.

Tracing confirms the searched continuation
`Bf4 Bxd4 Qxd4 Qa5+ Qc3 Qxb5`. Quiescence at ply three after Qxd4 searches
Qa5+ and returns +338 cp for Black. Both engines assess the chosen Qh5 at
-23 cp in a separate depth-four continuation search. This rejects an immediate
forcing loss within that analysis horizon; it is not a proof of Qh5's value.

Two fresh-process cold-FEN tests chose Qh5 at depth three, in 2.267 and 2.290 s.
Both four-call stateful candidate replays selected Nd4, Nf3, Nf3, Qh5, with
completed depths 2, 2, 2, 3. They intentionally diverged at move eight, so the
later supplied FENs no longer represent the candidate's own game path. State
age remains 1–4; ordinary two-process lifecycle tests separately passed.
The Numba control's stateful final moves were Qh5 at depth four and Bf4 at
depth three; its cold-FEN runs both chose Bf4 at depth three. Wall-clock depth
completion still changes the control's tactical result.

## Method and performance

All compute uses the existing `chessathon-scope:test` Python 3.12 image, one CPU,
2 GB RAM, 128 processes, read-only filesystem/workspace, 256 MB `/tmp` and no
runtime network. The 24-position suite is unchanged from the Numba experiment.
Three repetitions alternate control/candidate order per position. Each query
starts with empty engine and evaluation caches. Fixed-depth two uses a stopped
development clock; timed calls use the actual `get_move(fen, 10000)` clock.
The uninstrumented benchmark ran before the parallel arena.

| Timed metric | Control median / P95 / maximum | Candidate median / P95 / maximum |
|---|---:|---:|
| Move milliseconds | 300.38 / 301.30 / 326.18 | 300.68 / 302.51 / 303.37 |
| Nodes | 4,560 / 5,920 / 6,480 | 2,808 / 3,872 / 4,160 |
| Nodes/second | 15,434 / 19,717 / 21,583 | 9,615 / 13,438 / 15,922 |
| Completed depth | 3 / 4 / 5 | 2 / 3 / 5 |
| Passive overhead, percent | 0.346 / 0.535 / 0.845 | 0.222 / 0.402 / 0.687 |

“Maximum” is not the worst value for throughput or depth. The machine-readable
report also includes minima. Mean depth fell from 2.556 to 1.708, a -0.847-ply
change; 17 of 24 positions lost mean completed depth. A separate run of the
existing `timing.py` gate failed its original 0.05-ply tolerance: 2.438 versus
1.667. No move exceeded its remaining clock. Small hard-deadline overshoots
remain possible because polling is periodic; raw slack is retained.

Separate instrumented fixed-depth counts match the uninstrumented total node
counts. The mean per-position quiescence-node ratio is 2.733. Maximum total-node
expansion is 15.045 at suite index 22 (the supplied round-30 starting FEN).
Counter timings are not used as production latency measurements.

Raw measurements: `%TEMP%/qcheck-benchmark.log`, `qcheck-counts.log`,
`qcheck-performance.json`, `qcheck-targeted-final.log`, `qcheck-replay1.log`,
`qcheck-replay2.log`, `qcheck-cold1.log`, `qcheck-cold2.log`.

## Held-out games

`quiet_openings.py` records 30 hand-authored common opening prefixes. Every
line is included in source order; none is selected using candidate results.
Python-chess validates legality, uniqueness and nonterminal/noncheck endpoints.
An audit using only the frozen control at depth two gives -33 to +89 cp;
no positions were discarded or replaced after this audit. These are development
fixtures, not a gameplay book. The round-30 tactic is excluded.

Each matched colour pair consists of four official-referee games: candidate
and control each face the frozen control as both colours. The same opening,
seed, clock and opponent source are used for both configurations. Fresh agent
processes start each game and persist through its moves. Seeds identify trials;
these engines are deterministic apart from wall-clock search completion.

The 20-game smoke completed without failures: control +6 =0 -4 (60%),
candidate +3 =1 -6 (35%). Paired difference -25 percentage points, 95%
colour-pair bootstrap interval [-65, 0], with only five clusters.

The main screen uses 60 matched colour pairs / 240 games, 30 distinct openings,
and two trial seeds per opening. Six disjoint shards are pinned to logical
CPUs 0, 2, 4, 6, 8, 10, one thread from each physical core. Each retains the
one-CPU and memory restrictions. The merge validates complete cases, identical
conditions, source hashes and colour reversal. Intervals are bootstrapped both
by colour pair and by unique opening. Normalized Elo and SPRT are not supplied
by this official-referee driver; neither is inferred from raw win rate.

The main screen finished: six shards, all `exited 0`, 120 matched cases and
240 games. The merge confirmed one identical `RUN` record across every shard
(both source hashes, seed 53000, 10,000 ms base and 100 ms increment), 120
complete case pairs, 60 verified colour reversals, 30 distinct openings and
zero engine failures.

| Screen result | Control | Candidate |
|---|---:|---:|
| Games | 120 | 120 |
| W / D / L | 45 / 20 / 55 | 16 / 12 / 92 |
| Score, percent | 45.83 | 18.33 |
| Move milliseconds, median / P95 / worst | 207.13 / 290.88 / 307.37 | 222.50 / 294.97 / 315.54 |
| Mean completed depth | 1.809 | 1.089 |
| Median nodes/second | 8,720 | 5,603 |
| Passive overhead, median / P95 percent | 0.551 / 1.636 | 0.240 / 1.189 |
| Maximum initialization, milliseconds | 5,712.28 | 5,177.59 |
| Maximum RSS, megabytes | 172.73 | 168.20 |

The paired difference is **-27.5 percentage points**. The colour-pair bootstrap
over 60 clusters gives a 95% interval of **[-36.25, -18.33]**; the opening
bootstrap over 30 clusters gives **[-37.08, -17.50]**. Both exclude zero, so
the point estimate and both intervals agree with the smoke result. All 240 games
ended by rule: 208 checkmates, 30 threefold repetitions, one fifty-move draw and
one insufficient-material draw. There were no flag falls, no ply-cap draws, no
illegal or malformed moves and no crashes across 7,946 recorded moves, whose
slowest was 315.54 ms. No shard produced a duplicate or missing case. Normalized Elo and SPRT are not supplied by this
official-referee driver and are not inferred from the raw win rate.

Merged report: `%TEMP%/qcheck-screen-merged.json`; raw shard records:
`%TEMP%/qcheck-screen.log`.

## Correctness and decision

Candidate evaluation equality passed all 2,400 comparisons against the original
pure Python evaluator, including colour symmetry. Turning the development
toggle off reproduces the Numba control's fixed-depth moves, scores and nodes
on all 24 positions. Numba signatures remain unchanged after gameplay calls.
Evaluation kernels, cache code, SCOPE and unaffected engine methods retain
their ASTs. Search-result differences with the toggle on are expected and are
reported separately, not accepted as equivalence.

The 500 reachable-position legality/clock suite, special moves and draw states,
12-position determinism, Bayesian tests, all 93 adaptive safe-set cases,
completed-iteration and persistent-process lifecycle checks pass. Candidate
Ruff, strict agent typing and the unchanged official two-game gate passed.
Historical AST identity checks remain intact in `passive_units.unchanged_search`;
they apply to the original search, while its unchanged behavioral tests can be
run independently against the experiment.

## Decision

The extension is **rejected**. Two mandatory retention conditions had already
failed before the screen reported: the 20-game smoke gave candidate 35% against
control 60%, and the timed completed-depth gate regressed 2.438 to 1.667 mean
ply, far outside its 0.05-ply tolerance. Either failure alone is disqualifying,
so the rejection does not depend on the final arena point estimate. The screen
is recorded as confirmation, not as the deciding evidence: -27.5 points with
both 95% intervals below zero is consistent with, and no weaker than, the
smoke. The feature is rejected despite fixing round 30.

`agent.py` was restored to the retained Numba baseline
`59f99079f1db99221683dd3f06391f4fc502c1dae11fb712b08170242649830a` and compared
byte for byte against `numba_checkpoint/<sha>/agent.py`. `PASSIVE = True` and
`ADAPTIVE = False` are unchanged. Re-running the existing gates on the restored
file passed: Ruff, `mypy --strict` over the agent and the seven development
modules, the Numba import/signature and 2,400-comparison evaluation-equality
check with exact fixed-depth moves/scores/nodes on 24 positions, the
500-position legality and clock suite with castling, en passant, promotion,
underpromotion, stalemate, fifty-move and threefold fixtures, 12-position
determinism with colour symmetry, the Bayesian and 93-case safe-set units,
completed-iteration and interface lifecycle, and the official two-game referee
gate (+2 =0 -0).

The experiment stays reproducible: `quiet_experiment.json` pins both identities,
`quiet_candidate/<sha>/agent.py` keeps the candidate source, `quiet_checks.py`,
`quiet_suite.py`, `quiet_report.py` and `quiet_openings.py` keep the harnesses,
`quiet_openings.json` keeps the 30 held-out fixtures, and the raw logs remain in
host temporary files. No package, archive, commit or push is produced.
