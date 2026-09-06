# Isolated evaluation optimization

The control is the user-confirmed submitted source, SHA-256
`4551f4e4f2fc09fa56801e52a9ad38f0485ebb32d3c84aa4adbb4166fbb598c0`.
Its immutable path and original archive identity are in
`tournament/submitted.json`. No archive was modified or created.

The candidate uses Numba only for integer evaluation. Seven uint64 bitboards
and a boolean cross the Python boundary; no per-node NumPy board is allocated.
Placement tables, pawn masks and distances are computed once from existing
constants. Numeric attack rays include the first blocker, preserving the
previous pseudo-attack mobility semantics. Integer floor division, bishop pair,
king safety and passed-pawn terms retain their original values. The original
Python evaluator remains available through the existing slow-evaluation flag;
the frozen submitted module supplies the independent test oracle.

All four compiled functions use `njit(cache=False)`, with no parallel execution
or fast-math. One initial-board evaluation warms every production signature.
All search/model classes, root selection, mode recognition and `get_move`
match the submitted AST. ADAPTIVE, PASSIVE, cache and search flags are preserved.
The candidate SHA-256 is
`59f99079f1db99221683dd3f06391f4fc502c1dae11fb712b08170242649830a`.

## Method

The existing Python 3.12 Docker image is
`sha256:d06cdcb427cdf1853de87e69c41dd3797b49070d3dec070f5929d36e64a42b5a`.
Every run has one CPU, 2 GB memory, 128 processes, no network, a read-only
filesystem/workspace, and 256 MB writable `/tmp`. Docker Desktop supplies the
host CPU; these results do not emulate the tournament EPYC's exact speed.

`numba_validation.py` validates source identity before loading the control.
The benchmark uses 24 positions: 20 reproducible reachable positions, two
endgames, and both supplied round-30 FENs. Three repetitions alternate A/B
execution order. TT, history and evaluation cache start empty for each query.
Uncached evaluation samples average 100 calls, including scalar conversion;
reported percentiles describe these per-position batch means. Fixed-depth
queries use the real interface and stop before iteration three. Timed queries
use `get_move(fen, 10000)` with unchanged soft/hard deadlines. Percentiles are
empirical order statistics. Raw rows are in `%TEMP%/numba-benchmark-final.log`.
This final repetition ran without another development compute container.

The cProfile run is separate from uninstrumented timing. Inclusive quiescence,
evaluation and move-generation times overlap and must not be added together.

## Measurements

| Metric | Submitted median / P95 | Numba median / P95 |
|---|---:|---:|
| Uncached evaluation, microseconds | 29.32 / 37.41 | 4.24 / 5.64 |
| Completed depth-two move, milliseconds | 86.51 / 381.30 | 67.40 / 327.13 |
| Depth-two nodes/second | 11,346 / 16,658 | 14,681 / 20,661 |
| Timed move, milliseconds | 300.57 / 301.41 | 300.36 / 301.27 |
| Timed nodes/second | 11,619 / 17,724 | 14,685 / 19,128 |
| Completed depth | 2 / 3 | 3 / 3 |

Mean completed depth increased from 2.361 to 2.528; timed mean nodes from
3,467.35 to 4,377.07. Worst benchmark move times were 301.72 and 301.98 ms.
Fixed-depth nodes matched in every case (mean 1,515.375). The time allocator
uses saved work for more search, so timed move latency is intentionally similar.

For the 24 fixed-depth profiles, both engines visited 36,369 nodes, including
35,677 quiescence calls, and made 32,937 cached-evaluation calls / 21,389 misses.
Submitted versus candidate inclusive evaluation time was 2.178 versus 0.330 s;
quiescence 8.445 versus 6.514 s; legal generation 3.432 versus 3.403 s; total
interface calls 8.983 versus 7.092 s. Generator call counts include resumptions.
The earlier `numba-before-profile.log` used the older selection reference and
is not the submitted-source comparison; `numba-profile.log` is authoritative.

Initialization measured roughly 2.3–3.1 seconds including Numba import and
compilation, versus 0.02–0.04 seconds for loading the submitted source into the
test process. Arena initialization additionally includes fresh process startup.
The signature check found exactly one signature for each production kernel,
unchanged after equality, search and low-clock tests.

## Correctness and round 30

Exact integer equality passed on 1,200 reachable positions and their colour
mirrors (2,400 comparisons). The evaluator is from the side to move; mirroring
also swaps the mover, so its symmetry contract is equality, not negation.
All 24 fixed-depth move/root-score/node comparisons passed. The independent
500-position legality/clock suite, special moves and draw tests, deterministic
search checks, Bayesian/lifecycle tests and 93 adaptive safe-set cases pass.

The provided round-30 prefix and four rounded-clock calls are preserved in
`tournament/fixtures/rated.json`; `round30.pgn` contains the legal known fragment.
One fresh instance per engine handles all four calls. The submitted baseline
reproduced Nd4, Bb5, Bg5, Bf4 on both initial replay runs. The candidate matched
the first three moves, then completed depth four and chose Qh5 on both runs.
The final candidate query took 2.878 and 2.759 seconds. This is a timed search
result, not a new tactical rule or proof that Qh5 wins. A cold isolated FEN at a
full clock still chose Bf4 at depth three; preserving game state matters.

Clock-free fixed-depth analysis through `get_move` confirms that both engines
choose Bf4 at depth three (+31 cp, 15,315 nodes) and Qh5 at depth four (-23 cp,
37,994 nodes). At depth four Bf4 receives -338 cp (score or upper bound).
Moves, every root score and node counts are identical. The submitted/candidate
depth-four analysis took 3.449/2.939 seconds in this run. The test substitutes
a stopped clock solely to complete fixed-depth analysis; timed replay and
games use the actual clock.

## Arena and decision

Retention requires exact evaluation/search equivalence, a meaningful end-to-end
speed gain, no safety/timing failures, and nonnegative paired score differences
at both tested clocks. Confidence intervals remain necessary to describe
uncertainty; a nonnegative point estimate is not proof of an Elo gain.

The completed 20-game smoke scored submitted +3 =1 -6 (35%) and candidate
+6 =2 -2 (70%), against the frozen submitted rival. The paired difference was
+35 percentage points, colour-pair bootstrap interval [+15, +50], with just
five clusters. There were no failures. Candidate initialization peaked at
4.758 seconds and RSS at 172.55 MB. This small screen justified further testing,
not a strength claim.

The primary screen contains 60 matched cases / 120 games. Six independent
containers run disjoint 10-case shards, pinned to logical CPUs 0, 2, 4, 6, 8,
10: one thread from each of the host's six physical Intel i7-8700 cores. Each
container retains the one-CPU, 2-GB and filesystem/network restrictions.
The merge validates source hashes, conditions, cases and colour reversal.
It reports both colour-pair and opening-cluster intervals, since the 30 pairs
repeat five openings. Seeds identify matching trials; the submitted agent
itself has no random policy. Results remain conditional on this small corpus.

A conversation interruption ended an earlier foreground smoke before completion.
An initial serial screen was stopped to distribute the fixed primary cases
across physical cores. Their partial logs are preserved and excluded from the
primary analysis; neither run is presented as a completed acceptance test.

The primary screen completed all 120 games with zero failures and verified all
30 colour pairs. Submitted +21 =23 -16 scored 54.17%; candidate +29 =11 -20
scored 57.50%. The paired difference was +3.33 percentage points, with 95%
colour-pair bootstrap interval [-9.17, +15.00]. Clustering by the five openings
gave [-17.50, +24.17]. This is positive but statistically inconclusive.

Screen median/P95/worst move times were 191.85/288.67/317.40 ms for the submitted
control and 204.64/290.14/305.80 ms for the candidate. Candidate initialization
peaked at 4.377 s and RSS at 170.89 MB. Candidate modelling overhead was
0.510% median / 1.470% P95, versus 0.492% / 1.508% for the submitted control.
Both exceeded the earlier passive-model 1% P95 target under the parallel screen;
the model is unchanged and adaptive selection remains disabled. Evidence
coverage averaged 46.51% versus 51.19%. These statistics describe different
game paths, so the paired-FEN benchmark governs depth/throughput comparisons.
Raw shard logs and the merged report are in `%TEMP%/numba-screen-shard*.log`
and `%TEMP%/numba-screen-merged.log`.

The 120+0.5 confirmation completed eight games over two verified colour pairs,
with zero failures. Submitted +1 =0 -3 scored 25%; candidate +3 =0 -1 scored
75%. The paired difference was +50 percentage points, with a very wide
bootstrap interval [0, +100] from only two clusters. This is a small timing
and nonregression confirmation, not an Elo estimate.

Full-clock median/P95/worst move times were 1,585.30/2,881.32/2,883.38 ms for
the submitted control and 1,673.91/2,881.02/2,910.13 ms for the candidate.
Candidate modelling overhead was 0.117% median / 0.293% P95, versus
0.125% / 0.333% for control. Coverage averaged 89.77% versus 92.86%.
Candidate initialization peaked at 2.847 s and RSS at 195.90 MB. These are
game-path statistics; fixed-position comparisons above isolate efficiency.
Complete raw results are in `%TEMP%/numba-confirm-shard0.log`,
`%TEMP%/numba-confirm-shard1.log` and `%TEMP%/numba-confirm-merged.log`.

**Decision: retain Numba.** Exact numerical/search equivalence, import warm-up,
signature stability, legality/special-move/timing/determinism checks, passive
invariants and the official Ruff/Mypy/two-game gate passed. Both staged paired
point estimates were nonnegative, and the independent fixed-position benchmark
showed a repeatable end-to-end gain. Playing-strength uncertainty remains large.
Across the completed smoke, primary screen and full-clock confirmation there
were 148 games and no engine failures. No archive was created or modified.

## Files and remaining work

Changed in this experiment: `agent.py`, `docker-test.ps1`,
`tests/numba_validation.py`, `tests/numba_arena_summary.py`, `tests/selection.py`,
`tests/README.md`, `tests/NUMBA.md`, `tests/tournament/fixtures/rated.json`, and
`tests/tournament/fixtures/round30.pgn`. Existing `tests/timing.py`,
`tests/tournament/build_engines.py` and `tests/tournament/uci_agent.py` received
formatting/lint cleanup. The native tournament build remains parked; all
measurements here use the existing Python image and official referee.

Legal move generation now dominates the profile. Further evaluator-only work
has limited headroom. The depth-three quiet-check horizon remains in the
search; this optimization reaches the correcting depth more often rather than
changing that rule. The next isolated search experiment should test a bounded
quiet-check quiescence layer against this preserved reference, with the same
clock and paired-game gates. A wider held-out opening suite is also needed
before making tournament-strength claims.
