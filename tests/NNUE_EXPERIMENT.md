# NNUE-lite residual evaluator — predeclared experiment

Declared **2026-09-09**, before any training position was generated or labelled.
Nothing below is edited to match a result. Where a number changed after measurement,
the change is recorded in `tests/results/nnue/FINAL_NNUE_REPORT.md` with its reason,
never by rewriting this file's gates.

Machine-readable twin: `tests/nnue_manifest.json`.

## Hypothesis

> A tiny quantized residual network can improve shallow leaf evaluation sufficiently
> to correct search-boundary errors without sacrificing enough throughput to weaken
> the engine overall.

The network is **not** presumed superior. The null result — that the residual either
fails to correct the class-A fixtures, or corrects them but costs more throughput than
it returns — is a fully acceptable outcome and will be reported as a rejection.

Falsifiable failure modes, declared up front:

1. The residual learns the label distribution but the correction is too small at the
   depths the engine actually reaches, so no fixture changes.
2. The residual corrects fixtures but the per-leaf cost drops NPS enough to lose a
   ply of depth, which costs more than the correction wins.
3. The residual overfits the control's own blind spots because both the features and
   the target are derived from the control's evaluation.

## Baseline under test

| Item | Value |
| --- | --- |
| Control `agent.py` SHA-256 | `65ec40ceb29a8f6fe14a74ab2ed6ca446164f701e46d2b9d3fe8d5af94655bda` |
| Control size | 39,396 bytes, 1,046 lines |
| Git tag | `pre-nnue-control-20260909` → `78c03b0` |
| Pre-neural package | `submissions/pre_nn_20260909/agent.zip` |
| Package SHA-256 | `d9392c6b9c572790c838cc91e957c6eeaceecb706175d99cdfbf928a5e3b86a5` |

The control is **not** the agent inside `submission 0609v4/agent.zip`. That zip carries
the `a43da91` revision (`e5f63625…`, 38,673 bytes, 1,034 lines) and lacks the twelve
lines of quiescence delta pruning added at `2ed8811`. Measured in
`tests/results/nnue/pre_training/baseline_proof.json`.

## Network architecture

Trained from random initialization. No weights, tables or code are taken from any
existing engine.

| Component | Shape | Detail |
| --- | --- | --- |
| Input | 768 sparse | 12 piece-types × 64 squares, canonical side-to-move orientation |
| Auxiliary input | 6 dense | side to move, 4 castling rights, material phase (0–1) |
| Accumulator | 768 × 64 | embedding table; only active rows are summed |
| Auxiliary projection | 6 × 64 | added into the accumulator |
| Activation | clipped ReLU | clamped to [0, 127] in the quantized path |
| Output heads | 64 × 1 twice | middlegame residual, endgame residual |
| Blend | scalar | phase-interpolated between the two heads |

Parameter count ≈ 768·64 + 6·64 + 2·64 + biases ≈ **49,600**, inside the ~50,000 target.

The first layer is used strictly as an embedding table. A dense 768-vector is never
materialised and never multiplied at a leaf.

## Data sources

**Source A — fresh benchmark games.** Games generated during this task between the
immutable control and the runnable benchmark engines under
`tests/external_engines/bin/`, both colours, paired openings, seeded.

**Source B — stored PGNs.** The repository's own PGN corpus, restricted to eligible
games. Measured in `tests/results/nnue/pre_training/pgn_inventory.json`: 53 files, all
parsing legally, 40 unique games after deduplication, of which **16 games / 1,424 plies**
are eligible. Source B therefore contributes roughly 1% of the corpus and cannot carry
the dataset; Source A supplies the rest. This was measured, not assumed, and is a
declared deviation from the original expectation that the stored PGNs would be a major
source.

### Contamination rule (immutable)

Evaluation-only, never trained on:

- every game from **rounds 57–81**;
- every position whose first four FEN fields appear in `tests/rated_v5_positions.json`;
- every opening position used to seed an arena game.

Openings for generated games are drawn only from eligible (pre-round-57) PGNs and from
seeded random-ply positions. No rated opening is reused as a training seed.

### Split rules (immutable, fixed before labelling)

- Splits are assigned at **game level**, never at position level. Every position from
  one game lands in exactly one split.
- 70% train / 15% validation / 15% held-out test.
- Assignment is by `sha256(game_id)` modulo 100, so it is reproducible and independent
  of generation order, file order and shard order.
- If at least four distinct benchmark opponents run, one entire opponent is held out of
  training to test opponent generalization. Four binaries run but only **three families**
  exist (Rustic ×3, Shallow Blue, Loki), so the holdout is applied at family level and
  the limitation is reported.

## Random seeds

| Purpose | Seed |
| --- | --- |
| Opening sampling | 20260909 |
| Game manifest / colour pairing | 20260909 |
| Split assignment | sha256 of game id (no seed needed) |
| Torch / NumPy training | 20260909 |
| Arena pairing | 20260909 |

## Label oracle

| Item | Value |
| --- | --- |
| Engine | Stockfish 19 |
| Release | `sf_19`, published 2026-09-05 |
| Asset | `stockfish-windows-x86-64-universal.zip` |
| Asset SHA-256 | `3c8bf1f9ea66a09350a40df4f632288285ac206d99f33ab5842c408fc30b48a7` |
| Binary | `tests/external_engines/stockfish/stockfish/stockfish-windows-x86-64-universal.exe` |
| Binary SHA-256 | `45bc8e4969147db9c2eb533810637994619bff0eacc81ccfd9854394901bcbd0` |
| Licence | GPL-3.0 |
| Role | offline labelling only |

Stockfish is **never packaged**, never invoked at play time, and lives under a
gitignored directory. The AI Chessathon rules permit training on positions an engine
labelled; they forbid shipping the engine or a table of its moves. Neither the binary
nor any lookup table of its evaluations enters the submission — only the weights of a
network trained from random initialization.

### Labelling limit

**Fixed nodes = 200,000, Threads = 1, Hash = 64 MB.** Deterministic: verified in
`tests/results/nnue/pre_training/stockfish_probe.json` that repeated searches, including
in a fresh process, return identical scores and principal variations.

Measured rate (6 processes on the i7-8700):

| Nodes | ms/position | positions/hour | 200k positions |
| --- | --- | --- | --- |
| 50,000 | 84.1 | 256,988 | 0.78 h |
| **200,000** | **234.7** | **92,033** | **2.17 h** |
| 500,000 | 562.6 | 38,396 | 5.21 h |
| 1,000,000 | 1,146.4 | 18,842 | 10.61 h |

200,000 nodes is the strongest setting that lets the corpus finish inside today's
budget, which is the declared selection rule.

### Stored per position

`normalized FEN`, `side to move`, `game id`, `ply`, `phase`, Stockfish centipawns from
the side-to-move perspective, optional WDL, the control's own static evaluation, and the
residual target `stockfish_cp − control_static_cp`.

Mate and extreme scores are clamped to **±1000 cp**. Game result alone is never used as
the principal value label.

Augmentation: colour swap (mirror ranks, swap piece colours, negate the label) is applied
only after a unit test proves it is legal and label-preserving on a sample. Left-right
mirroring is **not** applied, because castling rights are not mirror-symmetric.

## Quantization

- Accumulator weights → int8, per-tensor symmetric scale.
- Accumulators held in int32.
- Output heads → int8 with their own scale; biases in int32.
- Dequantized correction clamped to **±250 cp** before it reaches the search.

## Integration

Branch `experiment/quantized-nnue-residual`, candidate source `agent_nnue.py`. The
control `agent.py` is not modified until every gate passes.

- The network is a **residual correction added to** the handcrafted evaluation, never a
  replacement for it.
- Terminal handling, material evaluation, legality, repetition and mate scoring are
  untouched.
- No network call for terminal positions.
- Weights load at import, inside the 90 s budget; the weight file's SHA-256 is verified
  at initialization and a mismatch falls back deterministically to the handcrafted
  evaluator.
- Numba jitted functions are warmed at import so compilation never lands on the clock.
- Sparse Numba integer inference is preferred; ONNX batch-one is used only if measured
  to be faster end to end.
- Nothing is obfuscated. A judge can read the integration.

## Gates

A gate is never weakened after a measurement. If a gate is wrong, it is recorded as
wrong and the candidate is still judged against the declared version.

### Model gates

- No train/test leakage (game-level split verified by id intersection).
- Float→quantized median deviation ≤ 10 cp.
- Colour-symmetry disagreement effectively zero.
- Held-out performance materially better than the handcrafted static evaluator.
- No NaN, no overflow, no perspective inversion.

### Engine gates

- Import and `get_move` API pass.
- Legal moves in every smoke and regression game.
- Deterministic under fixed seeds and fixed limits.
- No clock failures, no mate-score corruption.
- Zero solved-control regressions.
- Uncompressed submission < 50 MB.
- Initialization < 90 s in the platform environment.
- Peak memory < 2 GB.

### Throughput gate

Full-search NPS may fall at most **10%** versus the control, unless strength results
clearly compensate. Measured as alternating paired repetitions; warm and cold figures
reported separately.

### RATED_V5 gate

Fixture set: 16 positions, 11 enforced critical and 5 enforced solved controls, over
rounds 57, 58, 66, 77, 78, 79 and 80.

- Correct at least **three of the five reproducible class-A failures**.
- Break **zero** solved controls.
- Report every changed move and score.
- Round 80 `24.Rd4` (`d1d4`, min correcting depth 3, margin 372 cp) is the boundary
  test: the control reproduces it 1/5 and only in a run that completes depth 2 alone.
  The candidate must reject `Rd4` **at depth 2** without memorising that exact FEN —
  checked by confirming the residual also moves the evaluation correctly on perturbed
  siblings of the position, not just the fixture itself.
- Round 79 `12...Nxd3` (`e5d3`, min correcting depth 2, margin 201 cp) must also be
  tested.

### Arena gate

Identical seeds, openings and colours for control and candidate. At least **200 paired
games**, extended toward 400 if inconclusive. Opponents: every runnable benchmark engine
and the immutable control.

Reported: W-D-L, score %, paired score difference, Elo with a 95% confidence interval,
results by opponent, colour and opening phase, time losses, illegal moves, mean and
percentile move times, mean completed depth, NPS.

**A positive point estimate alone does not pass.** Passing requires either a confidence
interval excluding zero, or overwhelming fixture improvement with zero control breakage,
labelled provisional if the game count is short.

## Rejection criteria

The candidate is rejected, the control retained, and the experiment preserved for later
work if any of the following holds:

- any model gate fails;
- any engine gate fails;
- NPS loss exceeds 10% without compensating strength evidence;
- fewer than three class-A corrections, or any solved control broken;
- the arena confidence interval includes zero and fixture evidence is not overwhelming;
- the residual is found to have memorised fixture FENs rather than generalised.

## Platform environment

Inference and strength gates run in a Docker reproduction of the platform: Linux,
Python 3.12, one core per playing worker, 2 GB for final validation, no GPU, no network
during agent execution, and the official pinned stack — torch 2.13.0+cpu, numpy 2.5.2,
python-chess 1.11.2, onnxruntime 1.29.0, numba 0.67.0.

The host `.venv` carries that exact package stack but on **Python 3.14.4**, so it is used
for exploration and training only. No final performance number is taken from it.

Host: Intel i7-8700, 6 physical cores, 12 logical, 15.9 GB RAM. Docker Desktop 29.7.2,
12 CPUs and 8.29 GB visible to the daemon. Concurrency is calibrated at 1, 2, 4 and 6
simultaneous games; the previously rejected 24-worker configuration is not repeated.

## Planned arena size

200 paired games minimum (400 total), extending toward 400 paired (800 total) if the
interval is inconclusive and time allows.
