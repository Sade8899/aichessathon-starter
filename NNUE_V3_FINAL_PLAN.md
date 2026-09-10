# NNUE V3 — sibling ranking, two integration modes, and an honest arena

Predeclared 2026-09-10 on branch `experiment/nnue-v3-final`, before any V3 training
data, checkpoint or arena result existed. Written first so the acceptance rule, the
arena clock and the selection metric cannot be chosen after the numbers arrive.

The only measurements taken before this file was committed are **control-only**
calibrations — the control's realised depth against the clock, and how many concurrent
games this machine can run before the clock stops being honest. They involve no
candidate, they are reported in section 3, and they are what makes the rest of the plan
choosable rather than arbitrary.

## 1. Protected identities, verified not assumed

| artifact | expected | measured | status |
| --- | --- | --- | --- |
| `agent.py` SHA-256 | `65ec40ce...` | `65ec40ceb29a8f6fe14a74ab2ed6ca446164f701e46d2b9d3fe8d5af94655bda` | MATCH |
| `agent.py` size | 39,396 bytes | 39,396 bytes | MATCH |
| `agent.py` lines | 1,046 | 1,046 | MATCH |
| `submissions/pre_nn_20260909/agent.zip` | `d9392c6b...` | `d9392c6b9c572790c838cc91e957c6eeaceecb706175d99cdfbf928a5e3b86a5` | MATCH |
| tag `pre-nnue-control-20260909` | commit `78c03b0` | tag object `ba78a9f` -> commit `78c03b0d266a027c5e47ffa3a1b5af0c435da1b6` | MATCH |
| previous work | `experiment/nnue-v2-ranked` | at `6deb15f` | MATCH |

`agent.py` and the pre-neural package are read-only for the whole session. Every V3
candidate is *generated* from `agent.py` by a builder whose inverse must reproduce
`agent.py` byte for byte, and that round-trip is gate 1.

## 2. What V1 and V2 established, and what therefore must not be repeated

- V1 optimised static MAE, improved it by 27.85 cp on test and 48.67 cp on the Loki
  holdout, fell to RATED_V5 11/16, converted draws into losses and scored about -10 Elo.
- V2 optimised sibling ordering with an anchored, magnitude-penalised, confidence-gated
  residual. `q005` passed all 20 gates including RATED_V5 16/16, and scored 48.45 %
  over 2,000 head-to-head games — 95 % CI [46.5 %, 50.45 %], Elo -10.8.
- V2's frontier table prices the trade: every checkpoint with real reach breaks a solved
  fixture, and every checkpoint that keeps 16/16 corrects by only 5-14 cp. The first
  fixture to break is always `r77-33-Rc7+`, a repetition defence, because holding a draw
  means holding an evaluation *at* zero, where an arbitrarily small nudge flips the
  comparison.
- The confidence gate was not the effective ingredient. Restraint on correction
  magnitude was.

So V3 does not enlarge the scalar residual, add epochs, or add more Stockfish-labelled
positions under the same loss. Those optimise a metric already shown not to predict
strength.

## 3. The measurement V1 and V2 never made, and why it changes the plan

The control's time manager spends `time_left_ms / 32` on a move. Every V1 and V2
strength number was taken at a 1,000 ms clock, which buys 31 ms of search.

Control-only depth calibration, 20 positions per clock,
`tests/nnue_v3_depth_calibration.py`:

| clock | per-move budget | mean depth | median | nodes |
| ---: | ---: | ---: | ---: | ---: |
| 1,000 ms | 31 ms | **0.80** | 1 | 398 |
| 2,000 ms | 63 ms | 1.30 | 1 | 767 |
| 4,000 ms | 125 ms | 2.15 | 2 | 2,212 |
| 8,000 ms | 250 ms | 2.80 | 3 | 4,266 |
| 16,000 ms | 500 ms | 3.35 | 3 | 9,015 |
| 30,000 ms | 938 ms | 3.90 | 4 | 16,428 |
| 60,000 ms | 1,875 ms | 4.45 | 4 | 35,099 |
| **120,000 ms (rated)** | 3,750 ms | **4.85** | 5 | 74,945 |

At the clock on which V1 and V2 were rejected, the control reaches mean depth **0.8** —
a large share of moves are played with no completed search at all, straight off the
static ordering. Rated play reaches 4.85. The two prior experiments measured a regime
the platform never plays.

Worker calibration, control against itself at 8,000 ms + 500 ms,
`tests/nnue_v3_worker_calibration.py`:

| workers | games/h | mean depth | depth vs 1 worker | flags |
| ---: | ---: | ---: | ---: | ---: |
| 1 | 102 | 3.128 | +0.00 % | 0 |
| 2 | 210 | 3.137 | +0.28 % | 0 |
| **4** | **401** | **3.009** | **-3.79 %** | 0 |
| 6 | 614 | 2.482 | -20.66 % | 0 |

V2's arenas ran at 6 workers, which costs a fifth of the realised depth. The paired
design makes that unbiased between the two agents, but it deepens the regime error.

**Predeclared, before any V3 candidate has been played:**

- the standard V3 arena clock is **8,000 ms + 500 ms at 4 workers** — the deepest
  regime that fits the day's budget at honest clocks (depth 3.01 against rated 4.85,
  versus 0.80 for the clock V2 used);
- the official-time confirmation is **30,000 ms + 500 ms at 2 workers** (depth 3.90);
- no result at 1,000 ms will be used to accept or reject a V3 candidate.

### 3.1 Regime calibration on an existing candidate

Before spending compute on a new direction, the already-built, already-gated V2 `q005`
is replayed against the control at the new clock. This is a **calibration measurement,
not an acceptance test**: it decides where V3's compute goes, and `q005` could only ever
be packaged after passing the full V3 gate battery and a fresh confirmation arena.

- If `q005` is still negative at depth about 3, the bounded-residual evaluation family
  is refuted in the regime that matters and V3 spends its compute on policy/order mode.
- If it turns positive, the V2 rejection was partly an artefact of a 31 ms clock and the
  evaluation family is re-opened.

Either outcome is reported.

## 4. Data

Existing artifacts are reused, not regenerated: 128,950 Stockfish-19 labelled positions
(train 77,589 / validation 17,035 / test 15,225 / Loki holdout 19,101, all pairwise
overlaps zero) and 28,640 sibling groups (train 20,135 / validation 4,423 / test 4,082;
families rustic 21,426 and shallowblue 7,214; phases endgame 17,357 / middlegame 8,380 /
opening 2,903). Splits are inherited per game lineage and never reassigned.

The existing group corpus is 61 % endgame. New generation is therefore **targeted**, not
bulk, and concentrates on the categories V3 names and the corpus lacks:

1. positions where the control's choice carries meaningful Stockfish regret;
2. positions where a V1 or V2 candidate changed the control's preferred move;
3. neighbourhoods of reproducible rated failures (positions, never the fixtures);
4. middlegames, king safety, liquidation and passed-pawn decisions;
5. near-tied top two moves, near-zero and repetition-sensitive positions;
6. hard negatives mined from previous candidates.

Exclusions enforced in code and asserted in a provenance report: the whole Loki family
stays an unseen holdout opponent *and* unseen data; the RATED_V5 fixture FENs and every
position one ply from them are excluded; parents whose candidates all carry equal regret
are dropped for having no ordering signal. Deduplication uses the existing canonical
FEN key. A parent and all of its children share one split.

No new rated PGNs exist: the newest round in the repository is 80, ingested on
2026-09-09 before V2. There is therefore no post-V2 rated evidence to add, and that is
reported as an absence rather than filled in.

## 5. Objectives

All variants share the V2 feature extractor and quantized integer inference path
unchanged — both were proved correct in V1/V2 and re-deriving them would only create a
second thing that can drift. What changes is the loss:

| tag | objective |
| --- | --- |
| P | pairwise ranking on sibling pairs, regret-scaled margins |
| L | listwise softmax cross-entropy over the legal-move score distribution |
| R | regret-weighted top-move loss, weight proportional to the value lost by choosing wrongly |
| RA | R plus an auxiliary Huber residual for calibration |
| RAP | RA plus preservation/anchor, near-zero sign penalty, small-margin preservation |

Every variant carries, at fixed weight: the control anchor (breaking an ordering the
control already had right costs more than fixing a broken one gains), an extra penalty
on sign or order changes in near-zero positions, colour-symmetry consistency, and
quantization-aware regularisation. A retained trust head predicts whether applying the
correction **improves sibling ordering**, not whether the residual is large.

Loss coefficients are tuned on train and validation only. Test, the Loki holdout and
every arena stay sealed until checkpoint selection is frozen.

Widths 16, 32 and 48. Early stopping, small-seed averaging where it helps, any ensemble
distilled into one quantized network.

## 6. Two integration modes, both measured

1. **EVAL** — conservative gated residual at leaf evaluation. The V2 shape.
2. **ORDER** — the learned sibling score is used *only* for root and shallow move
   ordering; the control's static evaluation is returned unchanged at every node.

ORDER matters because it cannot move a draw evaluation off zero — structurally immune to
the mechanism that broke `r77-33-Rc7+` and collapsed V1's draws. It earns its keep only
through search efficiency, so it is rejected if inference cost or node growth outweighs
the benefit. Neither mode is assumed to win; both are measured on the same trained
knowledge.

The shipped implementation uses the exact quantized integer arithmetic exercised in
training validation. A float reference path exists only to be asserted equal to it.

## 7. Selection rule, frozen before any arena

Checkpoints are scored offline and ranked by a composite in which **move regret and
harmful-tail control dominate, and MAE is nearly irrelevant** — V1 was selected on MAE,
improved MAE, and lost.

    composite =  120 * preservation_rate_where_control_already_correct
               +  60 * teacher_top_move_accuracy_gain
               +   1 * mean_control_regret_reduction_cp
               -   3 * p95_harmful_regret_cp
               -   8 * p99_harmful_regret_cp
               -  40 * near_zero_sign_flip_rate
               +  25 * draw_repetition_neighbourhood_preservation
               + 0.02 * residual_MAE_gain_cp

with hard admission filters applied first — a checkpoint is not ranked at all unless
quantized and reference inference agree exactly, colour symmetry is violated zero times,
and RATED_V5 is 16/16. Middlegame and endgame are additionally reported separately, and
a checkpoint whose gain is confined to one phase is flagged.

Only the top-ranked checkpoint per integration mode enters an arena. The rule above is
not re-tuned after an arena is read.

## 8. Staged arenas

Balanced deterministic opening pairs, colours reversed, against the pre-neural control
directly and against the benchmark engines already in the repository (Rustic Alpha 3,
Shallow Blue 2.0, Zagreus 5.0), with **Loki 3.0 held out** as an unseen opponent.

| stage | games | clock | workers |
| --- | ---: | --- | ---: |
| screen | 200-300 per viable candidate | 8,000 + 500 | 4 |
| confirmation | 600-1,000 for survivors | 8,000 + 500 | 4 |
| final head-to-head | 2,000 or more if time permits | 8,000 + 500 | 4 |
| official-time | as many as fit | 30,000 + 500 | 2 |

**Sequential stopping, predeclared.** A candidate is dropped at the screen if the paired
score difference point estimate is negative *and* the upper bound of its 95 % CI is
below +2 pp, or if illegal moves, flags or crashes exceed the control's. Saved compute
goes to the strongest survivor. A control-versus-control null run is included because
the arena clock and worker count have both changed since V2's null.

Reported per stage: W/D/L, paired score difference, Elo with a 95 % bootstrap CI,
breakdown by colour, opponent family and game phase, draw-to-loss and loss-to-draw
transitions, completed depth, NPS and node counts, and timeout/illegal/crash counts.

## 9. Hard gates — all fifteen, no exceptions

1. control files and tags byte-identical;
2. clean import in the platform-equivalent container;
3. zero illegal moves, crashes, unexpected timeouts;
4. exact integer-vs-reference inference equality on at least 1,000 diverse positions;
5. zero colour-symmetry violations;
6. no data leakage or split overlap;
7. all enforced legacy fixtures pass;
8. RATED_V4 and RATED_V5 at the control's full baseline;
9. no solved-control, repetition-defence or passed-pawn-defence regression;
10. `r80-24-Rd4` still rejected at the relevant shallow depth;
11. paired NPS cost within the existing limit, measured by repeated interleaved runs
    (the control's own throughput moves about 9 % run to run, so a single run cannot
    decide this);
12. package size and imports satisfy the platform constraints;
13. runs successfully inside the platform container;
14. **candidate-versus-control strength positive with a 95 % CI excluding zero**;
15. the result does not depend on one colour, one opening family or one weak opponent.

Offline metrics and fixtures passing without gate 14 is still a rejection. No gate is
weakened after a result is seen.

## 10. Budget

Measured remaining wall clock at the time of writing: about 13 h. Reserved for gates,
arenas, packaging and reporting: at least 30 %, not less than 3 h — allocated 4.5 h. The
rest: about 25 % targeted sibling generation and labelling, about 35 % training and
checkpoint search, about 10 % offline selection and quantization checks. Training may run
outside Docker; inference, legality and final verification run in the platform container.

## 11. Verdict

The final report states exactly one of `ACCEPT — PACKAGE CANDIDATE` or
`REJECT — RETAIN PRE-NEURAL CONTROL`, with hashes, sizes, line counts, commit, branch,
every failed gate, confidence intervals and limitations. If no candidate passes every
gate, the pre-neural control is retained and no candidate package is created. A better
composite, a repaired fixture, a deeper average search or a statistically flat arena are
not success and will not be reported as success.
