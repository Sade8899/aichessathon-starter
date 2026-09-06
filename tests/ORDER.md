# Search efficiency: conservative delta pruning in quiescence

One isolated experiment against the submitted control `e5f63625...`, which is also
the working `agent.py` and the `agent.py` inside the submitted `agent.zip`
(`267400857f19b0c5ca9d5a4d6640f42201db1f42be9ba8cafae5e8bd63c6a74b`, promoted in
commit `a43da91f93709fc46d9c17f51acc14add61b224b`). All three identities were verified
before anything else ran. The control was frozen separately at
`submitted/e5f63625.../agent.py`.

The Numba evaluation, selective quiescence capture generation and the uncapped
`available / 32` time allocation are all preserved. `agent.py` was never modified.

## The measured bottleneck

`profile_search.py` splices counters into a *development copy* of the control by
exact textual substitution, each asserted unique. The copy is proved to search
identically to the untouched control — 96 comparisons over the 24-position suite at
depths 2 and 3, identical move, root scores and node counts — so its counts describe
the control. Component times come from cProfile over the untouched control, because
the splices add work of their own.

At a 120,000 ms clock over the 24-position suite, mean completed depth 4.54:

| Measurement | Value |
|---|---:|
| Main search nodes | 180,565 |
| Quiescence nodes | 1,533,837 |
| **Quiescence share of nodes** | **89.5%** |
| Nodes per second | 20,498 |
| Transposition probes | 180,550 |
| Probes whose key matched | 45.4% |
| Probes usable (depth, clock and context all matched) | 5.5% |
| **Probes producing a cutoff** | **0.73%** |
| Stand pat cutoffs | 932,369 |
| Beta cutoffs | 144,571 |
| **Beta cutoffs on the first move** | **85.6%** |
| Quiet beta cutoffs | 61,293 (42.4% of cutoffs) |
| Quiet cutoffs explained by killers | 91.0% |
| Quiet cutoffs explained by history | 7.9% |
| **Quiet cutoffs explained by neither** | **1.2%** |
| Late quiet cutoffs, index 3 or later | 3,673 (2.5% of all cutoffs) |
| Principal variation re-searches | 2,479 of 732,184 null windows (0.34%) |

Beta cutoff index histogram: 123,811 / 11,609 / 5,351 / 1,972 / 650 / 343 / 181 /
119 / 75 / 50 / 48 / 362.

Component time, cProfile at a 20,000 ms clock, total 13.80 s. Cumulative times of
nested callers overlap and must not be summed:

| Component | tottime share | cumtime |
|---|---:|---:|
| `quiesce` | 5.2% | 9.75 s (70.7%) |
| `generate_legal_moves` | 5.4% | 5.33 s (38.6%) |
| `generate_pseudo_legal_moves` | **10.8%** | 3.12 s |
| `push` | 6.5% | 1.94 s |
| `compiled_evaluate` | 4.5% | 0.63 s |
| `priority` (ordering key) | 4.1% | 1.05 s |
| `piece_type_at`, 1,142,909 calls | 2.4% | 0.33 s |

**The bottleneck is quiescence and the legal move generation inside it.** Numba
already removed evaluation as a cost: it is 4.5% of tottime. Main-search move
ordering is close to its ceiling at 85.6% first-move cutoffs.

## Why not a countermove heuristic

A countermove table targets quiet cutoffs that killers and history miss. Measured,
that residual is **727 of 144,571 cutoffs, under 0.5%**, and late quiet cutoffs of
every kind are 2.5%. The profile does not justify it, so the task's alternative list
applies. `order_experiment.json` records this decision, predeclared.

`profile_search.py opportunity` then measured how much work each alternative could
actually remove, over the same suite and clock:

| Technique | Measured opportunity | Verdict |
|---|---|---|
| Aspiration windows | re-search rate already 0.34%; the root already narrows with `ROOT_WINDOW_MARGIN` | rejected, no headroom and a failed window costs a full re-search |
| Late move reductions | late quiet moves are **68.6%** of main moves searched and fail low **99.2%** of the time | large, but it lowers depth speculatively and the one rejected experiment here (QUIET_CHECKS) failed by collapsing depth |
| SEE quiescence filtering | **36.2%** of quiescence moves are captures by a dearer piece onto a defended square | comparable opportunity, but needs a whole static exchange evaluator and a per-move loop |
| **Delta pruning** | **54.9% / 44.6% / 35.2% / 22.5%** of quiescence moves prunable at margins 0 / 100 / 200 / 400 cp | **selected** |

Delta pruning was chosen because it removes work from the measured dominant cost,
its test is one integer comparison on `VALUES` — which the engine already indexes for
move ordering — so unlike SEE almost all of the saving is net, and unlike a reduction
it never lowers main-search depth.

## The change

Candidate `65ec40ceb29a8f6fe14a74ab2ed6ca446164f701e46d2b9d3fe8d5af94655bda`,
frozen at `order_candidate/65ec40ce.../agent.py`. Diff in
`results/order/delta-quiesce.patch`. One constant and one block inside
`Engine.quiesce`:

```python
DELTA_MARGIN = 200
...
        pruning = not check and not self.pattern.endgame
        for move in self.order(board, moves, None, ply):
            if pruning and not move.promotion:
                victim = board.piece_type_at(move.to_square) or (
                    chess.PAWN if board.is_en_passant(move) else 0
                )
                if victim and stand + VALUES[victim] + DELTA_MARGIN <= alpha:
                    continue
```

`order_checks.py identity` asserts the scope mechanically: no definition added,
removed or renamed; `DELTA_MARGIN` the only constant added; **`quiesce` the only
`Engine` method whose AST differs**; every other definition identical. Flags, the four
Numba signatures, the `get_move` docstring and the per-move budget at 120,000 ms
(3.75 s) are unchanged, and evaluation is identical over 108 comparisons.

Four exemptions keep the recovery path safe:

- **never in check** — every evasion stays mandatory;
- **never a promotion** — the victim term does not represent its value;
- **never a non-capture** (`victim` must be non-zero) — this protects the quiet checks
  the `ply < 3` tactical branch exists to examine, including the `Qa5+` of the round 30
  refutation;
- **never when the root pattern is an endgame** — where positional swings dominate
  material. Suite positions 20 and 21, the two endgames, show a node ratio of exactly
  1.000, which is the exemption working.

Skipping a move that cannot raise alpha leaves the return value a valid lower bound,
so the node still fails low correctly and the caller's alpha-beta contract is unchanged.

## Validation

| # | Gate | Control | Candidate |
|---|---|---|---|
| 1 | Identity verified mechanically | `e5f63625...` | `65ec40ce...` |
| 2 | Cold import, 3 fresh runner processes | 2.61 s median | 2.70 s median |
| 3 | `ruff check .` | pass | pass |
| 4 | `mypy --strict`, 14 modules | pass | pass |
| 5 | Official `make gate` | pass, +2 =0 -0 | pass, +2 =0 -0 |
| 6 | `verify.py`, 500 positions and special moves | pass | pass |
| 7 | `determinism.py`, 12 positions, colour symmetry | pass | pass |
| 8 | Passive units, invariants, lifecycle (93 safe-set cases) | pass | pass |
| 9 | Round 30 rated regression | `d1h5`, depth 4 | `d1h5`, depth 4 |
| 10 | 600-ply clock safety | 19,044 ms left, 0 flags | **19,139 ms left, 0 flags** |
| 11 | Fixed corpus, depths 2/3/4 | — | 100% move agreement |
| 12 | Root moves, scores, nodes, quiescence nodes, time, NPS | — | see below |
| 13 | Repeated counterbalanced realistic clocks | — | +0.069 mean depth |
| 14 | 20-game smoke | 50.0% | **60.0%**, +10 points |
| 15 | 240-game paired screen, six workers | 46.67% | **49.58%**, +2.92 points |

`passive_units.unchanged_search` and `numba_validation.py check` are historical AST
identity checks against the pre-Numba checkpoint. No engine since the qcap experiment
can pass them; they were left intact and **not run**, exactly as QCAP.md records. No
test was weakened and the harness was not touched.

### Fixed tactical corpus, 24 positions, depths 2, 3 and 4

Counts come from a quiescence-counting development engine; every time is from an
unwrapped run, three repeats with counterbalanced execution order.

| Depth | Move agreement | Control nodes | Candidate nodes | Node ratio | Quiescence ratio | Control ms | Candidate ms | Latency |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 2 | **1.000** | 36,369 | 25,950 | 0.842 | 0.836 | 45.50 | 39.65 | **-12.85%** |
| 3 | **1.000** | 138,991 | 109,718 | 0.915 | 0.910 | 216.23 | 194.17 | **-10.20%** |
| 4 | **1.000** | 527,107 | 400,012 | 0.828 | 0.805 | 921.85 | 747.46 | **-18.92%** |

Totals: **702,467 to 535,680 nodes, a 23.74% reduction**; quiescence nodes 653,624 to
487,585, a **25.40% reduction**. Root scores are identical in **71 of 72** cases; the
one difference is suite position 8 at depth 3, the same move `c8g4` scored 1311 against
1291, a 20 cp shift in a position already won by more than a queen.

`order_checks.py adjudicate` re-ran all 72 fixed-depth comparisons looking for
disagreements to referee against a depth *d+1* control search. **There were none**:
0 disagreements, 0 tactical regressions.

Position 23 is the round 30 blunder position. The candidate reaches the same depth-4
verdict, `d1h5`, with 37% fewer nodes: 37,994 to 24,014.

### Realistic clocks, and the noise floor

144 paired measurements, 24 positions x 2 clocks x 3 repeats, counterbalanced order.
The noise floor is the identical run with the control loaded under both names.

| | mean depth change | deeper | shallower | move agreement | median NPS gain |
|---|---:|---:|---:|---:|---:|
| **Noise floor**, control vs control | -0.0069 | 0 | 1 | 1.0000 | +0.13% |
| **Candidate vs control** | **+0.0694** | **10** | **0** | 0.9861 | -4.86% |
| — at 120,000 ms | +0.0278 | 2 | 0 | | |
| — at 30,000 ms | +0.1111 | 8 | 0 | | |

Mean completed depth rises from 4.0625 to 4.1319. The effect is ten times the noise
floor's magnitude and opposite in sign to it, and it is one-sided: ten measurements
deeper and none shallower, against a noise run that produced none of either.

**Nodes per second falls 4.9%, and that is real rather than noise** — the noise run
measured +0.13%. It is the expected shape of a pruning change: the test costs time per
move while removing whole subtrees. The metric that matters is work for the same
result, and by that measure the candidate wins: 23.7% fewer nodes and 10 to 19% less
wall time for identical fixed-depth outcomes. The predeclared criterion was written
with both arms for exactly this reason, and it is the node arm that is met.

Worst move 3.602 s against the control's 3.611 s; smallest hard-deadline slack
-4.0 ms against the control's -11.2 ms, the usual periodic-polling overshoot.

### Clock safety through 600 plies

300 consecutive budgets on one live clock at 120,000 ms + 500 ms, which is every move
one side can make before the referee's 600-ply cap.

| | Control | Candidate |
|---|---:|---:|
| Minimum clock | 19,044 ms | **19,139 ms** |
| Final clock | 21,152 ms | 20,591 ms |
| Worst move | 3,601 ms | 3,600 ms |
| Worst spend / budget | 0.979 | 0.967 |
| Mean depth over the walk | 2.700 | **3.037** |
| Total nodes | 4,952,491 | **4,505,370** |
| Flags | 0 | 0 |

The candidate finishes a maximum-length game with slightly *more* clock than the
control, and searched deeper on 9% fewer nodes. Sixteen analytic recurrences at the
measured worst overshoot and at 1.5x and 2x it never fall below 7,674 ms.

### 20-game smoke

Ten matched cases, seed 63000, 10,000 ms + 100 ms, sparring against the frozen control.

| | Control | Candidate |
|---|---:|---:|
| W / D / L | 5 / 0 / 5 | 6 / 0 / 4 |
| Score | 50.0% | **60.0%** |
| Median / worst move, ms | 220.6 / 302.8 | 209.7 / 303.5 |
| Failures | 0 | 0 |

Paired difference **+10 points**, 95% colour-pair interval [-20, +40] over five
clusters. Nonnegative, so the screen was authorized.

### 240-game paired screen

The established design: 60 matched colour pairs, 120 cases, 240 games, the 30
`quiet_openings.json` prefixes in file order with two seeds each, seed 64000,
10,000 ms + 100 ms, six shards of 20 cases pinned to logical CPUs 0, 2, 4, 6, 8, 10,
one thread per physical core. Configuration order is counterbalanced twice: within a
shard by case parity, and across shards by flipping `--configs`. All six shards
`exited 0`; the merge confirmed one identical `RUN` record, 120 complete case pairs,
60 verified colour reversals and 30 distinct openings.

| | Control | Candidate |
|---|---:|---:|
| W / D / L | 51 / 10 / 59 | 56 / 7 / 57 |
| Score | 46.67% | **49.58%** |
| Median / P95 / worst move, ms | 189.5 / 288.4 / 308.4 | 192.8 / 288.9 / 306.8 |
| Terminations | 110 mate, 5 fifty-move, 5 threefold | 113 mate, 1 fifty-move, 6 threefold |
| Lowest clock left in any game | 3.62 s | 3.70 s |
| Max initialization / RSS | 4,196 ms / 173.6 MB | 3,908 ms / 174.1 MB |
| **Flags / illegal / crashes / failures** | **0 / 0 / 0 / 0** | **0 / 0 / 0 / 0** |

Paired difference **+2.92 points**, 95% interval **[-7.08, +12.92]** by colour pair
over 60 clusters and the same by opening over 30 clusters. Nonnegative, and the
interval includes zero, so **this is a screen, not a strength result. No Elo claim is
made or implied.**

Game inspection found no recurring tactical failure. Across 120 matched cases the
candidate was better in 28, worse in 22 and level in 70. Losses inside 20 moves, the
signature of a tactical collapse, number **seven for each engine**; loss lengths are
near identical (median 46 against 47, shortest 7 for both). The worst opening for the
candidate is -0.500 over a four-game sample, which is one game, and the best is +0.750
over the same sample size.

Mean completed depth in the screen table is *lower* for the candidate, 2.265 against
2.343. That is a game-path statistic — the two configurations played different games
from the same starts, so the positions differ. The paired fixed-position measurement
above is the one that governs depth, and there the candidate gained. QCAP.md records
the same distinction for the same reason.

## Verdict

**Accepted.** Every predeclared requirement is met:

| Requirement | Result |
|---|---|
| Zero flags, crashes, illegal moves, malformed outputs | 0 across 260 games and every gate |
| Every applicable correctness and regression gate | all pass |
| No fixed-depth tactical regression after repeated measurement | 100% move agreement, 0 adjudicated regressions |
| No material completed depth regression at realistic clocks | +0.069 ply, 10 deeper and 0 shallower |
| Node reduction for equivalent results, or 5% throughput | **23.7% fewer nodes** for identical fixed-depth results |
| Smoke nonnegative | +10 points |
| 240-game point estimate nonnegative, no recurring tactical failure | +2.92 points, 7 short losses each |
| Improvement exceeds the wall-clock noise floor | +0.069 against a floor of -0.0069 |

## Limitations

- **No strength claim.** The 240-game interval [-7.08, +12.92] includes zero. What is
  established is efficiency and safety, not Elo.
- **Nodes per second falls 4.9%.** The change buys fewer nodes, not faster nodes. If a
  future change makes the per-move test relatively more expensive, that trade could
  invert.
- **The screen ran at 10,000 ms + 100 ms**, where the budget is 0.3125 s and completed
  depth is near 2. Rated games run at 120,000 ms + 500 ms and reach depth 4 to 5, where
  the fixed-position measurements show a larger node saving. No full-clock arena was run.
- **`DELTA_MARGIN = 200` was not tuned.** It was chosen before any candidate existed,
  from the measured prunable-share curve, and left alone. A tuned margin was not tested
  and would be a separate experiment.
- **The corpus is 24 positions.** Move agreement of 1.000 on it does not prove the
  pruning never changes a move; it proves it did not on this corpus at these depths.
- **One duplicated lookup remains.** `Engine.order`'s `priority` already computes
  `piece_type_at(move.to_square)` for the same move. Threading that value out of `order`
  would remove it, but that widens the change beyond `quiesce` and was deliberately not
  done.
- The candidate is **not submitted, not packaged, not committed and not promoted**, and
  no rated result is attributed to it.

## Commands

```powershell
.\docker-test.ps1 -CpuSet 0 -LogName order-equiv python tests/profile_search.py equivalence
.\docker-test.ps1 -CpuSet 0 -LogName order-counts python tests/profile_search.py counts --clock 120000 --depths "2,3,4" --repeats 2
.\docker-test.ps1 -CpuSet 0 -LogName order-components python tests/profile_search.py components --clock 20000
.\docker-test.ps1 -CpuSet 0 -LogName order-opportunity python tests/profile_search.py opportunity --clock 120000
.\docker-test.ps1 -LogName order-lint ruff check .
.\docker-test.ps1 -LogName order-types mypy --strict agent.py tests/order_checks.py tests/profile_search.py tests/time_checks.py tests/time_report.py tests/rated_losses.py tests/rated_games.py tests/qgen_checks.py tests/numba_validation.py tests/numba_arena_summary.py tests/quiet_checks.py tests/quiet_report.py tests/quiet_openings.py tests/quiet_suite.py
.\docker-test.ps1 -CpuSet 0 -LogName order-identity python tests/order_checks.py identity
.\docker-test.ps1 -CpuSet 0 -LogName order-cold python tests/order_checks.py cold --repeats 3
.\docker-test.ps1 -Detached -CpuSet 0 -LogName order-corpus python tests/order_checks.py corpus --repeats 3
.\docker-test.ps1 -Detached -CpuSet 2 -LogName order-adjudicate python tests/order_checks.py adjudicate
.\docker-test.ps1 -Detached -CpuSet 0 -LogName order-timed python tests/order_checks.py timed --repeats 3
.\docker-test.ps1 -Detached -CpuSet 0 -LogName order-noise python tests/order_checks.py timed --repeats 3 --noise
.\docker-test.ps1 -CpuSet 4 -LogName order-rated-can python tests/rated_losses.py --config candidate --regress --manifest tests/order_experiment.json
.\docker-test.ps1 -CpuSet 4 -LogName order-gate python tests/order_checks.py gate --configs "control,candidate"
.\docker-test.ps1 -Detached -CpuSet 0 -LogName order-clock python tests/order_checks.py clock --moves 300 --stress-moves 600
.\docker-test.ps1 -Detached -CpuSet 0 -LogName order-smoke python tests/order_checks.py arena --cases 10 --seed 63000 --base-ms 10000 --increment-ms 100 --configs "control,candidate"
```

Screen, six shards with counterbalanced configuration order:

```powershell
for ($s=0; $s -lt 6; $s++) {
    $cpu = 2*$s; $offset = 20*$s
    $order = if ($s % 2 -eq 0) { "control,candidate" } else { "candidate,control" }
    .\docker-test.ps1 -Detached -CpuSet "$cpu" -LogName "order-screen-$s" python tests/order_checks.py arena --cases 20 --case-offset $offset --seed 64000 --base-ms 10000 --increment-ms 100 --configs "$order"
}
$logs = for ($s=0; $s -lt 6; $s++) { $p="tests/results/order/screen-$s.log"; docker logs "order-screen-$s" *> $p; $p }
Get-Content -LiteralPath $logs | docker run --rm -i --network none --cpus 1 --memory 2g --pids-limit 128 --read-only --tmpfs /tmp:rw,size=256m --mount "type=bind,source=$PWD,target=/workspace,readonly" chessathon-scope:test python tests/numba_arena_summary.py --cases 120 --control control --candidate candidate *> "tests/results/order/screen-merged.json"
```

Raw logs and merged reports are in `results/order/`.

## Next

**Obtain the platform PGNs and match logs for rounds 31 and 33-40.** Five of the eight
rated losses still have no move record, so they remain undiagnosable, and a 240-game
interval that spans zero is not a reason to pick the next engine change on its own.
That was the standing recommendation in RATED_LOSSES.md before this experiment and
nothing measured here displaces it.
