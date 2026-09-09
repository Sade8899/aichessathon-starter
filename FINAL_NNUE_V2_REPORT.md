# NNUE V2 - gated move-quality residual - final report

**VERDICT: REJECT - RETAIN THE PRE-NEURAL CONTROL**

5 of 7 predeclared acceptance criteria are satisfied.

| acceptance criterion | measured | verdict |
| --- | --- | --- |
| 1. RATED_V5 remains 16/16 | control 16/16, candidate 16/16 | PASS |
| 2. no solved draw or passed-pawn defence regresses | broken: none | PASS |
| 3. quantized inference works in the platform container | 600/600 exact, max \|diff\| 0 cp | PASS |
| 4. NPS and time management remain acceptable | 0.57% (17,367 -> 17,269 nps) | PASS |
| 5. large paired arena positive, 95% CI excluding zero | -0.0227 CI [-0.0727, +0.0227] over 240 games | **FAIL** |
| 6. not dependent on one opponent, colour or time control | per-opponent ok: False, per-colour ok: False | **FAIL** |
| 7. draw conversion does not deteriorate | draw share +0.91 pp (control 3.64% -> candidate 4.55%) | PASS |

> The acceptance rule was written down in `NNUE_V2_PLAN.md` before any V2 data was generated, precisely so it could not be relaxed once the numbers arrived. Better MAE, one repaired fixture, a deeper average search or a statistically flat arena are not success. The control is retained.

## 1. Identities

| artifact | SHA-256 | status |
| --- | --- | --- |
| protected control `agent.py` | `65ec40ceb29a8f6fe14a74ab2ed6ca446164f701e46d2b9d3fe8d5af94655bda` | UNCHANGED |
| pre-neural package | `d9392c6b9c572790c838cc91e957c6eeaceecb706175d99cdfbf928a5e3b86a5` | UNCHANGED |
| candidate `agent_nnue_v2.py` | `6bb80b6eea6ab78dee5f5f9a38f65e8aa9a9189f0ac3e2721a271b790d35cf18` | generated from the control |
| candidate weights | `f2062a0483ad805b01dfee6c9d170652ed7051d8abc27c33c140e1d807353f66` | F_h32_s20260909_q005 |

- branch `experiment/nnue-v2-ranked` at `8b1c385`
- frozen control tag: `pre-nnue-control-20260909`
- the candidate is *generated* from `agent.py` by `tests/nnue_v2_build_agent.py`, and the build asserts that stripping the inserted block reproduces `agent.py` byte for byte. `agent.py` is never written by any code path in this pipeline.

## 2. Dataset: sibling groups

- parents planned: **40,000**, groups written: **28,640**
- dropped for no ordering signal: 11,360 (every candidate equally good, so the group could not teach a ranking)
- labelling: Stockfish 19, 250,000 nodes, MultiPV=8, clamp +/-1000 cp, 8 workers
- throughput: 32,413 groups/h, 53.0 min wall

Exclusions enforced in code, not by convention: the entire Loki family (held out as an unseen opponent *and* unseen data), and the 16 RATED_V5 fixture FENs together with every position one ply from them, so the network cannot memorise a fixture's neighbourhood.

Splits are inherited from the V1 corpus per game lineage and never reassigned, so V2 cannot leak across a boundary V1 already established.

## 3. Training

All runs are width 32, 24,899 parameters, seed 20260909, 12 epochs, on the full 28,640-group corpus. Validation metrics at the selected epoch:

| tag | variant | epoch | preserved | regret cp | pair gain | MAE cp | \|corr\| cp | composite | s |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `A_full` | A | 11 | 0.8952 | +41.56 | +0.0167 | +21.07 | 114.2 | 18.062 | 36 |
| `B_full` | B | 11 | 0.8679 | +51.89 | +0.0171 | +20.02 | 115.7 | 26.143 | 36 |
| `C_full` | C | 11 | 0.8636 | +43.87 | +0.0175 | +25.02 | 101.6 | 18.885 | 38 |
| `D_full` | D | 11 | 0.9267 | +26.02 | +0.0150 | +16.78 | 50.9 | 9.299 | 38 |
| `E_full` | E | 11 | 0.8745 | +38.66 | +0.0142 | +20.21 | 93.8 | 14.677 | 38 |
| `F_q000` | F | 10 | 0.9691 | +9.13 | +0.0076 | +4.84 | 18.5 | -0.099 | 48 |
| `F_q002` | F | 11 | 0.9648 | +9.32 | +0.0085 | +5.04 | 18.6 | -0.213 | 47 |
| `F_q005` | F | 10 | 0.9745 | +6.39 | +0.0075 | +4.08 | 14.4 | -1.703 | 47 |
| `F_q010` | F | 11 | 0.9721 | +6.29 | +0.0062 | +2.86 | 10.6 | -1.458 | 48 |
| `F_q020` | F | 10 | 0.9842 | +4.38 | +0.0052 | +1.66 | 5.5 | -0.672 | 47 |
| `F_q040` | F | 10 | 0.9952 | +0.80 | +0.0018 | +0.49 | 1.6 | -0.809 | 47 |
| `H_t85` | H | 11 | 0.9921 | +3.42 | +0.0004 | +4.78 | 7.4 | 2.630 | 42 |
| `H_t90` | H | 11 | 0.9970 | +1.19 | +0.0007 | +2.85 | 4.0 | 0.938 | 43 |
| `H_t95` | H | 11 | 0.9994 | +0.54 | +0.0005 | +0.76 | 0.9 | 0.510 | 43 |

Checkpoints are selected on a composite score in which preservation of already-correct control choices carries 100x weight, regret reduction 1 point per centipawn, sibling pair accuracy 40x, draw preservation 20x and residual MAE 0.02x. MAE is deliberately the smallest term: V1 was selected on MAE, improved MAE by 27.85 cp, and lost.

**Which mechanism actually did the work, against expectation.** The confidence gate on its own did nothing for preservation: C is B plus the gate and preserves 0.8636 against B's 0.8679, a difference in the wrong direction and inside the noise. Adding the ranking loss also *reduced* preservation, A's 0.8952 to B's 0.8679, because ranking rewards reordering siblings and some of the orderings it reorders were already right.

What moved preservation was the anchor weight and then the magnitude penalty -- D triples the anchor and reaches 0.9267, and the F family adds a penalty on the size of the correction and reaches 0.96-0.99. The gate only became useful once it was made *hard* (the H family), where it decides **where** to act rather than only how much. The honest reading is that the headline idea of the plan, a calibrated confidence multiplier, was not the effective ingredient; restraint on correction magnitude was.

Per-epoch validation metrics for the finalist:

| epoch | composite | preserved | regret red cp | pair gain | draw pres | MAE gain cp | mean abs corr |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 0 | -4.518 | 0.9836 | +2.51 | +0.0061 | 0.7170 | +1.13 | 6.2 |
| 1 | -3.666 | 0.9836 | +3.27 | +0.0050 | 0.7240 | +0.87 | 4.8 |
| 2 | -4.022 | 0.9836 | +3.06 | +0.0052 | 0.7162 | +0.79 | 4.8 |
| 3 | -3.388 | 0.9830 | +3.08 | +0.0049 | 0.7505 | +0.93 | 5.0 |
| 4 | -3.009 | 0.9812 | +3.72 | +0.0060 | 0.7436 | +2.05 | 7.5 |
| 5 | -5.330 | 0.9800 | +2.34 | +0.0066 | 0.7011 | +2.27 | 8.9 |
| 6 | -3.159 | 0.9782 | +4.02 | +0.0061 | 0.7356 | +2.23 | 8.5 |
| 7 | -2.914 | 0.9733 | +4.88 | +0.0066 | 0.7275 | +2.88 | 10.2 |
| 8 | -3.374 | 0.9770 | +4.75 | +0.0072 | 0.6916 | +3.19 | 12.0 |
| 9 | -3.074 | 0.9739 | +4.82 | +0.0074 | 0.7174 | +3.47 | 12.2 |
| 10 | -1.703 | 0.9745 | +6.39 | +0.0075 | 0.7038 | +4.08 | 14.4 |
| 11 | -2.400 | 0.9685 | +6.42 | +0.0077 | 0.6972 | +3.79 | 14.6 |

## 4. Gates

| gate | measured | requirement | result |
| --- | --- | --- | --- |
| protected control agent.py unchanged | 65ec40ceb29a8f6fe14a74ab2ed6ca446164f701e46d2b9d3fe8d5af94655bda | 65ec40ceb29a8f6fe14a74ab2ed6ca446164f701e46d2b9d3fe8d5af94655bda | PASS |
| protected pre-neural package unchanged | d9392c6b9c572790c838cc91e957c6eeaceecb706175d99cdfbf928a5e3b86a5 | d9392c6b9c572790c838cc91e957c6eeaceecb706175d99cdfbf928a5e3b86a5 | PASS |
| candidate strips back to the control exactly | identical | identical | PASS |
| de Bruijn scan resolves every single-bit board | 64/64 | 64/64 | PASS |
| de Bruijn scan enumerates multi-bit boards exactly | 200/200 | 200/200 | PASS |
| agent integer path equals the reference integer path | 600/600 exact, max \|diff\| 0 cp | every position exact | PASS |
| correction never exceeds the clamp | max \|correction\| 160 cp, clamp 250 | <= 250 | PASS |
| colour-swap feature identity | 600/600 | all identical | PASS |
| inference is deterministic across calls | identical | identical | PASS |
| inference depends only on the position, not on board history | identical | identical | PASS |
| always returns a legal move | 40/40 legal | all legal | PASS |
| finds mate in one | 3/3 | 3/3 | PASS |
| unseen Loki-family holdout static MAE (reported, not enforced) | 366.84 -> 360.25 cp (+6.60) | reported only; MAE is not an acceptance criterion | PASS |
| full-search NPS loss | 0.57% (17,367 -> 17,269 nps) | <= 5.0% | PASS |
| package uncompressed size | 85,952 bytes (agent 58,698 + weights 27,254) | < 50,000,000 | PASS |
| no dependency outside the preinstalled stack | chess, numpy, numba, stdlib only | torch/numpy/chess/onnxruntime/numba only | PASS |
| RATED_V5 enforced fixtures | control 16/16, candidate 16/16 | candidate matches the control's 16/16 | PASS |
| solved-control regressions | 0 [] | 0 | PASS |
| draw and passed-pawn defence fixtures preserved | broken: none | none broken | PASS |
| r80-24-Rd4 still rejected | g5e3 | not the rated move | PASS |

20/20 gates pass.

### every candidate that reached a gate run or an arena

| candidate | gates | RATED_V5 | NPS loss | screen paired diff (95% CI) | draw share |
| --- | ---: | ---: | ---: | ---: | ---: |
| `F_q005` | 20/20 | 16/16 | 0.57% | -0.0227 [-0.0727, +0.0227] | 3.64% -> 4.55% |
| `F_q010` | - | - | - | +0.0000 [-0.0500, +0.0500] | 4.55% -> 4.55% |
| `H_t85` | 19/20 | 16/16 | 6.33% | not screened | - |

### where the correction actually fires

The soft gate and the hard threshold are different animals, and the mean correction hides it. Measured over 40,000 held-out children:

| candidate | conf threshold | fires on | size when it fires | mean over all |
| --- | ---: | ---: | ---: | ---: |
| `F_q005` | 0.00 | 91.44% | 14.84 cp | 13.57 cp |
| `H_t85` | 0.85 | 4.29% | 220.82 cp | 9.46 cp |
| `H_t90` | 0.90 | 2.03% | 231.3 cp | 4.7 cp |
| `H_t95` | 0.95 | 0.5% | 240.23 cp | 1.19 cp |

The hard threshold leaves ~96% of positions evaluating bit-identically to the control and spends its whole licence, near the clamp, on the few it claims to read. That is the design working. It still did not buy strength.

## 5. The preservation/reach frontier

The single most useful measurement of this session. Seven checkpoints spanning the trade-off, each screened on the 16 enforced RATED_V5 fixtures:

| checkpoint | preserved | \|corr\| cp | regret cp | MAE cp | RATED_V5 | broke |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| `B_full` | 0.8679 | 115.7 | +51.89 | +20.02 | 13/16 | r77-33-Rc7+, r78-48-Kf6, r79-12-Nxd3 |
| `D_full` | 0.9267 | 50.9 | +26.02 | +16.78 | 13/16 | r77-33-Rc7+, r78-48-Kf6, r79-12-Nxd3 |
| `F_q000` | 0.9691 | 18.5 | +9.13 | +4.84 | 15/16 | r77-33-Rc7+ |
| `F_q005` | 0.9745 | 14.4 | +6.39 | +4.08 | 16/16 | - |
| `F_q010` | 0.9721 | 10.6 | +6.29 | +2.86 | 16/16 | - |
| `F_q020` | 0.9842 | 5.5 | +4.38 | +1.66 | 16/16 | - |
| `F_q040` | 0.9952 | 1.6 | +0.80 | +0.49 | 16/16 | - |

The boundary sits between 0.969 and 0.972 preserved: the cheap training-time proxy predicts the expensive gate. It also prices the trade. Every checkpoint with real reach breaks solved controls, and every checkpoint that keeps 16/16 corrects the evaluation by only 5-14 cp on average. No setting in this family is both safe and large.

The **order** in which fixtures break is the most diagnostic thing in the table. The first to go, alone, at the smallest correction that breaks anything at all, is `r77-33-Rc7+` -- a repetition defence. Only when the correction grows further do `r78-48-Kf6` (a passed-pawn defence) and `r79-12-Nxd3` follow.

That is a mechanism for V1's draw collapse, not just a correlate of it. Holding a draw means keeping an evaluation *at* zero across a repetition, which is the most fragile thing a bounded additive correction can disturb: the two sides of the comparison are equal, so an arbitrarily small nudge flips it. Winning positions have margin and absorb the same nudge unchanged. A residual trained on unconditioned error therefore damages held draws first and hardest -- which is exactly what V1 did when its draws fell from 15 to 3.

## 6. Arenas

### screening

- 240 games over 60 openings, 5000ms + 500ms, 6 workers, seed 20260912
- families: rustic, shallowblue, zagreus, loki
- **paired difference -0.0227 95% CI [-0.0727, +0.0227]**, Elo -7.9 [-25.3, +7.9]
- control 6-4-100 (7.27%), candidate 3-5-102 (5.0%)
- draw share 3.64% -> 4.55% (+0.91 pp)
- candidate time losses 0, illegal moves 0

| opponent | pairs | paired diff | 95% CI | control % | candidate % |
| --- | ---: | ---: | ---: | ---: | ---: |
| rustic | 60 | -0.0083 | [-0.0750, +0.0583] | 8.33 | 7.5 |
| shallowblue | 20 | -0.1250 | [-0.3000, +0.0250] | 15.0 | 2.5 |
| zagreus | 10 | +0.0000 | [+0.0000, +0.0000] | 0.0 | 0.0 |
| loki (HELD OUT) | 20 | +0.0250 | [+0.0000, +0.0750] | 0.0 | 2.5 |

| candidate colour | pairs | paired diff | 95% CI | control % | candidate % |
| --- | ---: | ---: | ---: | ---: | ---: |
| white | 50 | -0.0300 | [-0.1100, +0.0400] | 11.0 | 8.0 |
| black | 60 | -0.0167 | [-0.0833, +0.0417] | 4.17 | 2.5 |

### confirmation: not run, and deliberately so

The predeclared procedure takes **only the best surviving candidate** into an 800-1,200 game confirmation. Nothing survived screening: q005 returned a negative point estimate, which is a predeclared immediate rejection, and q010 returned exactly zero with an identical 7-5-98 record to the control. Running a confirmation arena anyway would have spent an hour dressing a rejection in a larger sample.

The head-to-head below is the large run that was worth doing instead, and it is reported as supplementary evidence rather than as a substitute for the criterion it does not satisfy.

### head to head against the control

The benchmark arena puts both agents against engines rated 1630-1721, where both score near the floor -- control 7.27%, candidate 5.00% -- so almost every paired difference is exactly zero and the test spends its games re-proving that the benchmarks are stronger. A direct match has no floor: the same opening is played twice with colours reversed, so the only thing that varies is the difference being measured. 50% is parity.

This is supplementary evidence, not a substitute. Acceptance criterion 5 stays keyed to the benchmark arena that was predeclared, because choosing a different test after seeing a result is how a rejection becomes an acceptance without any new evidence.

| run | games | clock | seed | W-D-L | score | 95% CI | Elo | beats control |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| null (control vs control) | 1000 | 1000+100ms | 20260914 | 416-175-409 | 50.35% | [47.55%, 53.25%] | +2.4 | False |
| pilot | 300 | 1000+100ms | 20260913 | 127-44-129 | 49.67% | [44.5%, 54.83%] | -2.3 | False |
| confirmation | 2000 | 1000+100ms | 20260914 | 780-378-842 | 48.45% | [46.5%, 50.45%] | -10.8 | False |

The **null run is the control playing itself** through the same harness, in the same slot the candidate occupies, at the same seed. It returns 50.35% [47.55%, 53.25%], centred on parity. The harness therefore does not disadvantage the candidate slot, and the confirmation's shortfall is a property of the candidate rather than of the measurement.

The null run also reproduces the colour split almost exactly (29.6% as White, 71.1% as Black), which settles what that split means: it is the random-play opening book being unbalanced, not either agent preferring a colour. The pairing cancels it exactly, which is why it is played both ways.

- confirmation: as White 30.35%, as Black 66.55%, draws 18.9%
- mean depth candidate 1.24 vs control 1.23; flags 0, illegal 0, infrastructure 0

## 7. What this does and does not establish

**Established.** A gated residual can be trained that keeps every solved control: RATED_V5 stays 16/16, no draw or passed-pawn defence fixture regresses, r80 24.Rd4 stays rejected, the quantized integer path matches the reference exactly on 600/600 positions, and the package imports and plays inside the platform container. V1 failed five of those. The preservation rate measured during training predicts fixture breakage, which makes the expensive gate cheap to anticipate.

**Also established, and the reason for the verdict.** Nothing in this family improved playing strength. The safe corrections are small by necessity, and the arenas cannot distinguish them from the control.

**Not established, and worth stating plainly:**

- *Absence of evidence is not evidence of absence.* The head-to-head confidence interval spans tens of Elo. A true effect of a few Elo -- which is the size a 5-14 cp correction plausibly buys -- would not be visible at these sample sizes. The claim is that no improvement was **demonstrated**, not that none exists.
- *The benchmark arena is weakly powered here.* Both agents score near the floor against engines several hundred points stronger, so most paired differences are exactly zero by construction.
- *The opening book is random play, not curated openings.* Rated games start from a curated set that is not published. Random-play openings are shared by both agents and the pairing cancels them, but they are not the distribution the platform actually uses.
- *The head-to-head ran at 1s+0.1s*, where the agents reach depth ~1.3. A residual could matter more at the rated 120s+0.5s, where the search is deeper and a static evaluation error survives further up the tree. That was not affordable to test at a useful sample size on this hardware.
- *One architecture, one seed.* Width 32, a single training seed per configuration. The frontier was mapped along the correction-magnitude axis, not across capacity or seeds.
- *The NPS gate is at the edge of what this harness can resolve.* Repeated runs of the identical q005 candidate on an otherwise idle machine returned 4.49% and 0.57%, and the **control's own** throughput moved between 17,367 and 19,122 nps -- about 9% -- across runs. Three interleaved repeats with best-of taken per side reduced that but did not remove it. The gate's own noise is therefore comparable to the 5% threshold it enforces, so it should be read as 'no large regression' rather than as a number good to a point. The one comparison it does support is the relative one, because it is taken within a single run: t85 needed 99,032 nodes to reach the same depth the control reached in 92,396, and q005 needed 88,235.

## 8. Reproduction

```
# 1. sibling groups (Stockfish 19, MultiPV=8 at 250k nodes)
python tests/nnue_v2_data.py --parents 40000 --workers 8 --seed 20260909
python tests/nnue_v2_provenance.py

# 2. training matrix and the correction-magnitude frontier
python tests/nnue_v2_train.py --variants A,B,C,D,E --hidden 32 --seed 20260909 \
       --epochs 12 --floor 0.0 --tag full
python tests/nnue_v2_train.py --variants F --hidden 32 --seed 20260909 \
       --epochs 12 --floor 0.0 --override w_quiet=0.05 --tag q005
python tests/nnue_v2_train.py --variants H --hidden 32 --seed 20260909 \
       --epochs 12 --floor 0.0 --override conf_min=0.85 --tag t85

# 3. cheap fixture screen over a family of checkpoints, before any arena
python tests/nnue_v2_fixture_screen.py <tag> [<tag> ...]

# 4. pack a checkpoint (rebuilds the candidate from agent.py) and gate it
python tests/nnue_v2_pack.py F_h32_s20260909_q005
python tests/nnue_v2_gates.py --tag F_h32_s20260909_q005

# 5. snapshots, so several candidates can be arena-tested at once
python tests/nnue_v2_materialize.py F_h32_s20260909_q005

# 6. arenas
python tests/nnue_v2_arena.py --tag F_h32_s20260909_q005 \
       --candidate tests/results/nnue/v2/snapshots/F_h32_s20260909_q005/agent_v2.py --pairs 60 --workers 6 --label screen
python tests/nnue_v2_h2h.py --tag F_h32_s20260909_q005 \
       --candidate tests/results/nnue/v2/snapshots/F_h32_s20260909_q005/agent_v2.py \
       --pairs 1000 --workers 6 --clock-ms 1000 --increment-ms 100 \
       --seed 20260914 --label h2h

# 7. platform container: 1 CPU, 2 GB, no network, read-only, 256 MB /tmp
python tests/nnue_v2_docker.py --tag F_h32_s20260909_q005

# 8. this report
python tests/nnue_v2_report.py --tag F_h32_s20260909_q005
```

Generated by `tests/nnue_v2_report.py` from recorded artifacts. No number in this document was typed by hand.
