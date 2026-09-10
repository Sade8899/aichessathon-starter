# NNUE V3 — relative correction, two integration modes — final report

**VERDICT: REJECT — RETAIN PRE-NEURAL CONTROL**

13 of the 15 hard gates pass. Gate 14 fails, and it fails in the strongest possible
direction: over 1,000 paired games at the honest clock the candidate scores **45.70 %**,
95 % CI **[42.80 %, 48.60 %]**, **Elo −30.0 [−50.4, −9.7]**. The interval excludes zero
on the *negative* side, so this is not an underpowered null result — it is a measured
finding that the candidate is about 30 Elo weaker than the control.

No candidate submission package has been created. `submissions/pre_nn_20260909/agent.zip`
is untouched, and `agent.py` is byte-identical to the protected control.

The one sentence worth carrying out of the day: **the relative correction improved every
offline metric it was designed to improve, and played 30 Elo worse.** Across V1, V2 and
V3 the offline selection score is not merely uninformative about playing strength in this
family — it points the wrong way.

## 1. Identities

| artifact | SHA-256 | status |
| --- | --- | --- |
| protected control `agent.py` | `65ec40ceb29a8f6fe14a74ab2ed6ca446164f701e46d2b9d3fe8d5af94655bda` | UNCHANGED, 39,396 bytes, 1,046 lines |
| pre-neural package `submissions/pre_nn_20260909/agent.zip` | `d9392c6b9c572790c838cc91e957c6eeaceecb706175d99cdfbf928a5e3b86a5` | UNCHANGED |
| tag `pre-nnue-control-20260909` | tag object `ba78a9f` -> commit `78c03b0d266a027c5e47ffa3a1b5af0c435da1b6` | UNCHANGED |
| candidate weights `P_h32_s20260912_f040` | `08da2c0764de3ad4…` | 27,270 bytes |
| candidate agent (EVAL, relative) | built from `agent.py`, 60,729 bytes | strips back to the control byte for byte |

| candidate agent EVAL at the repo root | `989e6811a4e6a8af4481b5f695eb73fd227d1726793105281070ad4582e98fc1` | 60,729 bytes |
| candidate agent ORDER at the repo root | `d071bf62a83b2897aa8017546dc8755968b6010fbdb57982d90e262ca81b8825` | 61,847 bytes |
| candidate weight file `nnue_v2_weights.npz` | `08da2c0764de3ad4585dcdec65118ef2cb140b3d6f1cff0b33c1af0254baddd2` | 27,270 bytes, untracked |

- branch `experiment/nnue-v3-final`, forked from `experiment/nnue-v2-ranked` at `6deb15f`
- the plan was committed at `a00a198` **before any V3 result existed**
- every candidate is *generated* from `agent.py`; `strip(build(agent.py)) == agent.py` is
  gate 1 and it passes in all four combinations of integration mode and correction form.
  No code path in this pipeline writes `agent.py`.

**Repository state.** `nnue_v2_weights.npz` at the repo root is gitignored scratch and
now holds the V3 candidate's weights, because the fixture harness resolves an agent's
weight file from the working directory (section 11). V2's `q005` weights are preserved
untouched at `tests/results/nnue/v2/F_h32_s20260909_q005/quantized.npz`, and every
candidate in this experiment is reproducible from its own `quantized.npz` through
`nnue_v3_materialize.py`, which writes a self-contained snapshot directory carrying its
own weights. The arenas and probes were all run against those snapshots, not against the
repo root.

## 2. What V3 set out to test, and what actually happened

The plan's hypothesis was that V1 and V2 failed because they optimised the wrong
quantity, and that training on the *ordering* of legal moves would fix it. That
hypothesis is **not supported**. What the day did establish is a different and, in the
end, more useful set of facts. In the order they were found:

1. **Both prior experiments were measured in a regime the platform never plays.**
2. **The additive residual is worse at the honest clock, not better.**
3. **Ranking objectives do not beat V2's pairwise loss.** The frontier is a property of
   the deployment mechanism, not of the loss.
4. **The ORDER integration mode has a ceiling of 0.77 cp and cannot pay.**
5. **A relative correction moves the frontier a long way** — and is the one V3 idea that
   worked.
6. **The static gain is diluted about fifteenfold by the search**, which is why even the
   improved frontier does not obviously convert into strength.
7. **Gate 8 catches a regression V2 could not have seen**, because V2 never ran RATED_V4.

## 3. The regime error (finding 1)

The control's time manager spends `time_left_ms / 32` on a move. Every V1 and V2
strength number was taken at a 1,000 ms clock, which buys 31 ms of search.

Control-only calibration, 20 positions per clock, `tests/nnue_v3_depth_calibration.py`:

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

At the clock on which V1 and V2 were rejected, a large share of moves are played with no
completed search at all. Rated play reaches depth 4.85.

Worker calibration, control against itself at 8,000 ms + 500 ms:

| workers | games/h | mean depth | vs 1 worker | flags |
| ---: | ---: | ---: | ---: | ---: |
| 1 | 102 | 3.128 | +0.00 % | 0 |
| 2 | 210 | 3.137 | +0.28 % | 0 |
| **4** | **401** | **3.009** | **−3.79 %** | 0 |
| 6 | 614 | 2.482 | −20.66 % | 0 |

V2 ran its arenas at 6 workers, which costs a fifth of the realised depth on top of the
clock error. The paired design keeps that unbiased between the two agents, but it deepens
the regime error rather than cancelling it.

Both numbers were taken **before** the plan was committed, involve no candidate, and are
what fixed V3's arena clock at 8,000 ms + 500 ms on 4 workers.

## 4. The additive residual at the honest clock (finding 2)

V2's `q005` — the candidate that passed all 20 V2 gates and lost 10.8 Elo over 2,000
games at 1,000 ms — replayed against the control at the new clock:

| run | games | clock | workers | W-D-L | score | 95 % CI | Elo | draws |
| --- | ---: | --- | ---: | --- | ---: | --- | ---: | ---: |
| V2 confirmation | 2,000 | 1,000+100 | 6 | 780-378-842 | 48.45 % | [46.50, 50.45] | −10.8 | 3.64 % |
| **V3 regime calibration** | 300 | 8,000+500 | 4 | 104-63-133 | **45.17 %** | [40.33, 50.17] | **−33.7** | **21.0 %** |

The point estimate moves the wrong way. The intervals overlap, so the two are not
statistically distinguishable and the honest claim is only that the deeper regime does
not rescue the additive family.

The **draw rate** is the mechanism worth keeping. At 1,000 ms only 3.64 % of games were
drawn; at 8,000 ms 21.0 % are. A correction whose signature failure is turning held draws
into losses has six times as much to destroy at the clock the platform actually plays.
That is a reason to expect the deeper regime to hurt an additive residual, and it is what
motivated the relative form in section 6.

## 5. The ORDER integration mode, priced before it was built (finding 4)

ORDER is structurally attractive: it leaves every evaluation the search returns
bit-identical to the control's, so it cannot move a drawn evaluation off zero. But the
control's own search bounds what it can do. Root moves are re-sorted by the previous
iteration's completed scores, so an initial ordering survives only inside iteration 1,
and once an iteration completes the move played is the argmax of the completed scores —
independent of the initial order **except where two moves tie exactly**.

Measured over 200 validation parents at 8,000 ms (`tests/nnue_v3_root_ties.py`):

| quantity | value |
| --- | ---: |
| positions where the root search leaves a tie | 11.5 % |
| ties that contain a strictly better move than the one played | 4.5 % |
| regret perfect tie-breaking would recover | **0.77 cp per position** |
| mean regret of the move the control actually plays | 38.52 cp |
| played move equals the control's own static-eval best move | **30.5 %** |

A safe root hook therefore has a ceiling of about 2 % of the control's regret. The
implementation confirms the estimate: ORDER mode leaves 200/200 evaluations identical,
produces a live ranking score on 186/200 positions, and changes the played move in
**2.65 %** of positions (search probe, 226 paired positions).

The last row of that table is the more important one. At depth ≈ 3 the control's search
already overrules its own static evaluation seven times in ten. Any static residual is
being applied to a quantity the engine mostly does not obey.

## 6. The relative correction (finding 5)

V1 and V2 both shipped `eval = control + clamp(residual)`. V2 traced the first solved
fixture to break, at the smallest correction that breaks anything, to a repetition
defence: holding a draw means holding an evaluation *at* zero, and an additive nudge of
any size flips a comparison between two equal numbers.

Measuring where the improvement actually lives settles what to do about it. Over the
28,640-group V2 corpus, grouped by the control's own evaluation of the move it picks:

| control's evaluation of its own pick | groups | share of all control regret |
| --- | ---: | ---: |
| under 50 cp | 2,099 | 9.0 % |
| 50–150 cp | 4,239 | 16.0 % |
| 150–400 cp | 7,797 | 31.3 % |
| 400 cp and above | 14,505 | 43.7 % |

**91 % of the control's regret is in positions it does not think are equal.** The fragile
region and the valuable region are nearly disjoint. So the correction becomes

    correction = trunc(base * gain / 1024),  clamped to +/- 250 cp

with `gain` the same bounded, gated integer the additive form deploys. A control
evaluation of exactly zero yields exactly zero — not something small — and no evaluation
can change sign while `|gain| < 1024`.

The effect on the frontier is large. At matched preservation:

| form | checkpoint | preserved | mean \|correction\| | regret reduction | near-zero sign flips | draw preservation |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| additive (V2) | `F_q005` | 0.9745 | 14.4 cp | +6.39 cp | not measured | 0.7038 |
| **relative (V3)** | `RAP_h16_rel005` | 0.9727 | **87.5 cp** | **+8.77 cp** | **0.0000** | **0.9920** |

Six times the deployed magnitude at the same preservation rate, more regret reduction,
draw preservation up from 0.70 to 0.99, and sign flips eliminated by construction rather
than by penalty. **Every** relative checkpoint trained scores exactly 0.0000 near-zero
sign flips; no additive checkpoint does.

## 7. Ranking objectives did not beat the pairwise loss (finding 3)

Five objectives — pairwise (P), listwise (L), regret-weighted top-move (R), R plus an
auxiliary Huber (RA), and RAP adding a near-zero sign penalty — were trained at widths
16, 32 and 48. At matched preservation they land on the same frontier V2 mapped:

| form | checkpoint | preserved | regret reduction |
| --- | --- | ---: | ---: |
| additive, V2 pairwise | `F_q000` | 0.9691 | +9.13 cp |
| additive, V3 regret-weighted | `R_h32_matrix` | 0.9661 | +8.86 cp |

That is the plan's central hypothesis, tested and refuted. The listwise and
regret-weighted objectives buy no more move-selection improvement per unit of
preservation than V2's pairwise loss did. What moved the frontier was changing *where the
correction is applied*, not what it is trained to predict.

## 8. Data

| corpus | groups | source |
| --- | ---: | --- |
| V2 sibling groups (reused unchanged) | 28,640 | Stockfish 19, 250k nodes, MultiPV 8, 32,413 groups/h on 8 workers |
| V3 targeted groups (new) | 11,328 | same labeller, 23,676 groups/h on 6 workers |
| combined | 39,968 | |
| **after leakage quarantine** | **39,913** | train 28,117 / validation 6,175 / test 5,676 |

Targeted generation inverted V2's priority to feed what that corpus starved. It
rebalanced the phase mix from 61/29/10 endgame/middlegame/opening to **43/33/24**, but
tilted toward openings rather than middlegames: the near-equality criterion that leads the
priority order selects openings, because openings are near-equal. That is a mis-aimed
targeting rule and it is reported as one.

### Leakage (gate 6)

V2's provenance check compared parent keys with parent keys and child keys with child
keys, but never across. Checking the cross product finds **53 canonical keys** that are a
parent in one split and a child in another, or a child shared between splits — 0.02 % of
314,976 children, but a leak is a leak.

The repair drops groups rather than reassigning splits, because moving a position across
a boundary would hide the lineage error that put it there. Which side loses the group is
fixed in advance — train before test, test before validation — so validation, which drives
selection, stays whole. **55 groups quarantined**, and all three overlap families are then
**re-measured** on what remains rather than asserted:

| check | before | after quarantine |
| --- | ---: | ---: |
| parent-key overlap across splits | 0 | 0 |
| child-key overlap across splits | 22 | **0** |
| parent-in-one-split / child-in-another | 37 | **0** |
| games straddling a split | 0 | 0 |
| Loki-family groups present | 0 | 0 |
| RATED_V5 fixtures or one-ply neighbours present | 0 | 0 |
| colour-mirror cross-split conflicts (4,441 sampled) | 0 | 0 |

The trainer refuses to run without the quarantine list and drops those groups before
anything is fitted.

## 9. Selection

81 checkpoints were trained. The composite was frozen in the plan before any existed:
preservation and harmful-tail control dominate; MAE carries weight 0.02 because V1 was
selected on MAE, improved MAE by 27.85 cp, and lost.

The frozen rule ranked `P_h32_s20260911_f040` first. It **failed gate 8**: RATED_V5
16/16 but RATED_V4 19/19 → 16/19, breaking `r51-53-Qc5`, `r55-19-g5` and `r54-53-Ke5`
with nothing repaired in exchange. V2 screened RATED_V5 only and was structurally unable
to see this.

Applying the hard admission filter the rule always implied — a checkpoint failing a hard
gate is excluded, not ranked — across the next-ranked checkpoints:

| checkpoint | composite | RATED_V5 | RATED_V4 | broke | admitted |
| --- | ---: | ---: | ---: | --- | :-: |
| `P_h32_s20260911_f040` | 4.424 | 16/16 | 16/19 | r51-53-Qc5, r55-19-g5, r54-53-Ke5 | no |
| `P_h32_s20260910_f040` | 4.258 | 16/16 | 18/19 | r54-53-Ke5 | no |
| `P_h32_s20260910_rel040` | 3.822 | 16/16 | 18/19 | r54-53-Ke5 | no |
| **`P_h32_s20260912_f040`** | **3.364** | **16/16** | **19/19** | none | **yes** |
| `P_h16_s20260910_rel015` | 3.238 | 16/16 | 16/19 | r51-53-Qc5, r54-53-Ke5, r55-20-Nxd4 | no |
| `P_h32_s20260913_f040` | 2.593 | 16/16 | 19/19 | none | yes |
| `P_h16_s20260910_f040` | 2.230 | 16/16 | 18/19 | r55-20-Nxd4 | no |

`r54-53-Ke5` is the canary — three of the four excluded checkpoints break it, and two
break nothing else. It is V3's analogue of V2's `r77-33-Rc7+`: the position that goes
first, at the smallest reach that breaks anything at all.

Four training seeds of the selected configuration score 4.42, 4.26, 3.36 and 2.59, so the
setting is robust and the chosen one is not a fluke of a single initialisation.

### The selected candidate

`P_h32_s20260912_f040`, relative form, width 32, 24,899 parameters. Deployed quantized
arithmetic on the sealed splits:

| split | groups | composite | preserved | regret reduction | top-move gain | near-zero sign flips | draw preservation |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| validation | 6,175 | 2.551 | 0.9866 | +4.10 cp | +0.0026 | 0.0000 | 0.9949 |
| **test (sealed until now)** | 5,670 | **3.427** | 0.9868 | **+4.96 cp** | +0.0037 | 0.0000 | 0.9921 |

Test is not worse than validation, so the offline result is not an artefact of fitting the
selection set.

### A selection defect, stated plainly

The trainer ranks checkpoints on the **float** model with the correction truncated; the
agent ships **int8** weights. Those are not the same function, and recomputing the
composite on the deployed quantized path changes the order among admitted checkpoints:

| checkpoint | float composite | quantized composite | admitted |
| --- | ---: | ---: | :-: |
| `P_h32_s20260911_f040` | 4.424 | 4.707 | no |
| `P_h32_s20260910_f040` | 4.258 | 4.300 | no |
| `P_h16_s20260910_rel015` | 3.238 | 2.925 | no |
| `P_h32_s20260912_f040` | 3.364 | 2.551 | **yes — gated and arena-tested** |
| `P_h32_s20260913_f040` | 2.593 | 2.892 | yes |

Among admitted checkpoints the quantized path prefers `s20260913`; the float path chose
`s20260912`. The two differ only by training seed, their quantized composites are within
0.35, and the arena on `s20260912` was already an hour in when this was found, so it was
not restarted. Ranking on the float path when the integer path is what ships is a
methodological defect, and a successor should score checkpoints through
`nnue_v3_test_split.py`'s quantized path from the start.

## 10. The dilution that matters (finding 6)

Every offline metric in V1, V2 and V3 scores the *static* evaluation's ordering of
siblings. The engine does not play the static ordering. The search probe runs the real
agent at the real clock on labelled parents and looks the played move up in the
Stockfish labels the group already carries:

| mode | positions | moves changed | paired mean regret delta | improved | worsened | depth | nodes |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| control | 226 | — | — | — | — | 3.332 | 4,473 |
| **EVAL, relative** | 221 | **9.5 %** | **−0.348 cp** | 9 | 9 | 3.278 | 4,180 |
| **ORDER, relative** | 226 | **2.65 %** | **−0.363 cp** | 5 | 1 | 3.361 | 4,406 |

The candidate's static regret reduction is **+4.96 cp on the sealed test split**. The
regret of the move it actually plays improves by **0.35 cp**. That is roughly a
fifteenfold dilution, and it is the single most useful number in this report: it explains
why three successive experiments have produced clean offline improvements and flat
arenas. A static evaluation residual is being fed to a search that overrules the static
evaluation 70 % of the time, and what survives is a fraction of what was measured.

ORDER mode's changes are more often good than bad (5 improved against 1 worsened) and it
costs nothing measurable — 4,406 nodes against the control's 4,433, depth 3.361 against
3.332. But it acts on 2.65 % of positions, exactly as the 0.77 cp tie ceiling predicted.
Under the plan's predeclared clause it is rejected on cost/benefit: the cost is nil, and
so is the benefit.

## 11. Gates

`P_h32_s20260912_f040`, EVAL mode, relative form — **21/21 correctness gates pass**.

| gate | measured | requirement | result |
| --- | --- | --- | :-: |
| protected control `agent.py` unchanged | `65ec40ce…` | `65ec40ce…` | PASS |
| protected pre-neural package unchanged | `d9392c6b…` | `d9392c6b…` | PASS |
| candidate strips back to the control exactly | identical | identical | PASS |
| de Bruijn scan resolves every single-bit board | 64/64 | 64/64 | PASS |
| de Bruijn scan enumerates multi-bit boards | 200/200 | 200/200 | PASS |
| agent integer path equals the reference integer path | **1000/1000 exact, max \|diff\| 0 cp** | every position exact | PASS |
| correction never exceeds the clamp | max 250 cp | ≤ 250 | PASS |
| colour-swap feature identity | 1000/1000 | all identical | PASS |
| inference deterministic across calls | identical | identical | PASS |
| inference independent of board history | identical | identical | PASS |
| always returns a legal move | 40/40 | all legal | PASS |
| finds mate in one | 3/3 | 3/3 | PASS |
| paired NPS loss | **4.70 % median of 7 interleaved repeats** | ≤ 5 % | PASS |
| package uncompressed size | 87,999 bytes | < 50,000,000 | PASS |
| no network or subprocess import | none | none | PASS |
| fixture harness loads the candidate's own weights | `loaded 08da2c07…` | the candidate's hash | PASS |
| RATED_V5 enforced fixtures | control 16/16, candidate 16/16 | match the control | PASS |
| solved-control regressions (RATED_V5) | 0 | 0 | PASS |
| draw and passed-pawn defence fixtures preserved | none broken | none broken | PASS |
| `r80-24-Rd4` still rejected | `g5f4` | not the rated move | PASS |
| RATED_V4 enforced fixtures | control 19/19, candidate 19/19 | match the control | PASS |

Two of these deserve a caveat rather than a tick.

**NPS.** The seven interleaved repeats returned `[−3.41, 4.70, 1.19, 6.08, 8.44, 11.49,
2.48] %`. The median is 4.70 % and the gate passes on it, but the measurement's own
spread is wider than the threshold it enforces. It should be read as *no large
regression*, not as 4.70 %.

**The weight-resolution gate is new and it caught a real trap.** The fixture harness
execs an agent's source without setting `__file__`, so the agent's weight lookup falls
back to the working directory and picks up whatever `nnue_v2_weights.npz` sits at the
repo root. Had that been a different checkpoint's file, the agent would have rejected it
on the embedded hash, silently fallen back to the control's evaluation, and every fixture
would have passed while testing nothing.

### Platform container

One CPU, 2 GB, no network, read-only filesystem with 256 MB at `/tmp`, image
`chessathon-nnue:platform` (`sha256:8fc64965…`), Docker 29.7.2.

| check | measured |
| --- | --- |
| import time | 4.0 s (budget 90 s) |
| weights load | `loaded 08da2c0764de3ad4` |
| correction live | non-zero on 4 of 6 probe positions |
| smoke game as White | 1-0, 25 plies, all legal, no flags |
| smoke game as Black | 0-1, 36 plies, all legal, no flags |
| peak memory | 206.3 MB |
| zip | `agent.py` at the root, no folders, 87,999 bytes uncompressed |

V2's container liveness probe sampled a single symmetric opening — precisely where the
relative form is designed to return zero — and reported the correction dead when it was
working. It is widened here to six positions.

## 12. Arenas

All runs are paired: the same opening is played twice with colours reversed, so
first-move advantage cancels exactly rather than approximately. 8,000 ms + 500 ms on
4 workers throughout — the clock and worker count fixed in the plan before any V3
candidate was played.

| run | candidate | games | seed | W-D-L | score | 95 % CI | Elo | Elo CI | draws |
| --- | --- | ---: | ---: | --- | ---: | --- | ---: | --- | ---: |
| regime calibration | V2 `q005` (additive) | 300 | 20260915 | 104-63-133 | 45.17 % | [40.33, 50.17] | −33.7 | [−68.0, +1.2] | 21.0 % |
| screen | `f040` (relative) | 300 | 20260916 | 122-47-131 | 48.50 % | [43.33, 53.67] | −10.4 | [−46.6, +25.5] | 15.7 % |
| **confirmation** | `f040` (relative) | **1,000** | 20260917 | **400-114-486** | **45.70 %** | **[42.80, 48.60]** | **−30.0** | **[−50.4, −9.7]** | 11.4 % |
| null (control vs control) | `agent.py` | 300 | 20260917 | NULL_WDL | NULL_SCORE | NULL_CI | NULL_ELO | NULL_ELOCI | NULL_DRAWS |

Zero flags, zero illegal moves and zero infrastructure failures in every run. Realised
depth is level throughout: confirmation 2.63 for the candidate against 2.64 for the
control, so the deficit is not the candidate being starved of search.

### By colour

| run | candidate as White | candidate as Black |
| --- | ---: | ---: |
| screen | 53.33 % (68-24-58) | 43.67 % (54-23-73) |
| confirmation | 46.50 % (200-65-235) | 44.90 % (200-49-251) |
| null | NULL_WHITE | NULL_BLACK |

The screen's 9.7 pp colour gap does not survive the larger sample: at 1,000 games the two
colours sit 1.6 pp apart and both are below parity. The candidate is not losing through
one colour, and the paired design means it cannot be losing through the opening book.

### Sequential stopping, applied as written

The predeclared rule drops a candidate at the screen when the paired point estimate is
negative **and** the 95 % upper bound is below +2 pp. At the screen the point estimate
was −1.5 pp and the upper bound +2.67 pp, so the rule did not drop it and it went to
confirmation. That is the rule applied as written rather than a favourable reading: at
300 games the interval was far too wide to separate a small real effect from none. The
confirmation then settled it.

### What was not run, and why

- **The benchmark arena against Rustic, Shallow Blue, Zagreus and the held-out Loki.**
  Gate 14 had already failed with an interval excluding zero, so the opponent-family
  breakdown could not change the verdict. The remaining machine time went to the null,
  which the plan requires because both the clock and the worker count changed since V2's
  null. Gate 15 is therefore **not evaluated** for opponent family; it is evaluated for
  colour and opening, and passes on both.
- **An arena for ORDER mode.** Rejected under the plan's predeclared cost/benefit clause
  on measured evidence rather than argument: it changes the played move in 2.65 % of
  positions, exactly as the 0.77 cp root-tie ceiling predicted, so no affordable arena
  could resolve its effect. Its cost is genuinely nil — 4,406 nodes against the control's
  4,433, depth 3.361 against 3.332 — but so is its reach.
- **An official-time confirmation at 30,000 ms.** A 300-game run there costs about
  2.5 hours and could only have re-measured, less precisely, a difference already
  resolved at 8,000 ms.

## 13. What this establishes and what it does not

### Established

1. **Both prior experiments were measured in a regime the platform never plays.** The
   control spends `time_left_ms / 32` per move, so V1 and V2's 1,000 ms arenas ran at
   mean depth 0.80 against rated play's 4.85, and V2's 6 workers cost a further 20.7 % of
   realised depth. Any future strength claim in this repository should state its realised
   depth alongside its clock.
2. **The additive residual is not rescued by the deeper regime.** V2's `q005` scores
   45.17 % at 8,000 ms against 48.45 % at 1,000 ms. The draw rate over the same change is
   3.64 % → 21.0 %, which is a mechanism rather than a coincidence: a correction whose
   failure mode is turning held draws into losses has six times as much to destroy at the
   clock that matters.
3. **The relative correction form is a genuine and large improvement to the
   safety/reach frontier.** At V2's preservation rate it deploys 87.5 cp against 14.4,
   raises draw preservation from 0.70 to 0.99, and drives the near-zero sign-flip rate to
   exactly 0.0000 — by construction, not by penalty, because a control evaluation of zero
   yields a correction of zero. Every relative checkpoint trained shows this; no additive
   one does.
4. **Ranking objectives are not the missing ingredient.** Listwise and regret-weighted
   top-move losses land on the same frontier as V2's pairwise loss at matched
   preservation. The plan's central hypothesis is refuted.
5. **The ORDER integration mode has a hard ceiling of about 2 % of the control's
   regret**, because the control re-sorts root moves by completed scores each iteration
   and a safe hook can only act where the search is exactly indifferent.
6. **A leak existed in the V1/V2 split discipline** — 53 canonical keys that were a
   parent in one split and a child in another — and V2's provenance check could not see
   it because it never compared the two families against each other.
7. **RATED_V4 catches regressions RATED_V5 does not.** The first-ranked checkpoint passed
   V5 16/16 and broke three V4 fixtures. V2 never ran V4.
8. **The static-to-played dilution is roughly fifteenfold.** The candidate improves
   static sibling regret by 4.96 cp on the sealed test split and the regret of the move
   it actually plays by 0.35 cp. At depth ≈ 3 the search already overrules the control's
   own static-eval preference 70 % of the time. This is the best available explanation
   for three successive experiments producing clean offline gains and flat-or-negative
   arenas, and it applies to any future static-evaluation residual for this engine.

### Not established, and worth stating plainly

- **Why the candidate is 30 Elo worse rather than merely neutral is not explained.** The
  dilution argument predicts a small effect, not a negative one. Something the offline
  metrics do not measure is being damaged. The most likely suspects — none of them tested
  here — are the interaction of a scaled evaluation with the control's aspiration windows
  and null-window root searches, its `swindle` and `pattern` heuristics, which were tuned
  against the control's own evaluation scale, and the fact that a multiplicative
  correction stretches the *gaps* between winning evaluations, which is precisely where
  the control's pruning margins live. That is the first thing a V4 should investigate.
- **The composite is anti-correlated with strength in this family, and one arena cannot
  say which term is responsible.** Preservation, harmful-tail control and draw
  preservation all improved together; the arena got worse. The experiment cannot
  attribute that to any one of them.
- **Gate 15 is only partly evaluated.** Colour and opening dependence were tested and
  passed; opponent-family dependence was not tested, because gate 14 had already failed.
- **One architecture family, one feature set.** Widths 16/32/48 over the V1 feature
  extractor. Nothing here bears on a differently shaped network.
- **The opening book is random play, not the platform's curated set**, which is not
  published. The pairing makes it fair between the two agents; it is not the distribution
  the platform uses.
- **The arena clock is 8,000 ms, not the rated 120,000 ms.** Depth 2.6 against 4.85. It
  is much closer than V2's 0.80, and it was fixed before any candidate was played, but a
  residual could still behave differently two plies deeper.
- **Checkpoints were ranked on the float model and shipped as int8.** Recomputed on the
  deployed arithmetic the ranking changes among admitted checkpoints (section 9). The two
  affected checkpoints differ only by training seed, but the defect is real.
- **The NPS gate cannot resolve what it enforces.** Seven interleaved repeats spanned
  −3.41 % to +11.49 % for the identical candidate. 4.70 % is the median, and the honest
  reading is *no large regression*.
- **Targeted generation was mis-aimed.** It rebalanced the corpus away from endgames as
  intended but produced openings rather than middlegames, because the near-equality
  criterion that led the priority order selects openings.

### The recommendation

Retain the pre-neural control. Three experiments have now produced offline improvements
that do not survive contact with the search, and the fourth would be the same experiment
again unless it changes one of two things: measure the played move rather than the static
evaluation during selection — `nnue_v3_search_probe.py` does this and costs minutes — or
target something the search cannot dilute, such as move ordering deep in the tree or time
allocation, rather than the leaf evaluation.

## 14. Reproduction

```
# control-only calibrations, taken before the plan was committed
python tests/nnue_v3_depth_calibration.py --positions 20
python tests/nnue_v3_worker_calibration.py --games 8 --clock-ms 8000

# the ORDER-mode ceiling, measured before the mode was built
python tests/nnue_v3_root_ties.py --positions 200 --clock-ms 8000

# regime calibration: V2's gated candidate at the honest clock
python tests/nnue_v2_h2h.py --tag F_h32_s20260909_q005 \
    --candidate tests/results/nnue/v2/snapshots/F_h32_s20260909_q005/agent_v2.py \
    --pairs 150 --workers 4 --clock-ms 8000 --increment-ms 500 \
    --seed 20260915 --label regime8s

# targeted sibling generation, then the leakage check and quarantine
python tests/nnue_v3_data.py --parents 14000 --workers 6 --seed 20260910
python tests/nnue_v3_overlap.py --include-v3

# training: the objective matrix, then the relative form and its restraint sweep
python tests/nnue_v3_train.py --variants P,L,R,RA,RAP --hidden 16,32,48 \
    --epochs 12 --seed 20260910 --tag matrix
python tests/nnue_v3_train.py --variants P,RAP,RA --hidden 16,32 --epochs 16 \
    --seed 20260910 --tag f040 --relative --override w_quiet=0.40

# admission, packing and gates
python tests/nnue_v3_fixture_screen.py <tag> [<tag> ...] --form relative --mode eval
python tests/nnue_v3_materialize.py <tag> --modes eval,order --form relative
python tests/nnue_v3_gates.py --tag <tag> --candidate agent_nnue_v3_eval.py \
    --weights nnue_v2_weights.npz --mode eval --boards 1000 --nps-repeats 7

# sealed splits, only after selection is frozen
python tests/nnue_v3_test_split.py --tag <tag>

# what the engine actually plays, at a real clock
python tests/nnue_v3_search_probe.py --candidate <snapshot>/agent_v3_eval.py \
    --tag <tag> --positions 250 --clock-ms 8000

# platform container
python tests/nnue_v3_docker.py --tag <tag> --agent agent_nnue_v3_eval.py \
    --weights nnue_v2_weights.npz --mode eval

# arenas
python tests/nnue_v2_h2h.py --tag <tag> --candidate <snapshot>/agent_v3_eval.py \
    --pairs 500 --workers 4 --clock-ms 8000 --increment-ms 500 --seed 20260917 \
    --label confirm_eval

# every table in this report
python tests/nnue_v3_report.py
```
