# Selective quiescence capture generation

The control is the retained Numba engine, SHA-256
`59f99079f1db99221683dd3f06391f4fc502c1dae11fb712b08170242649830a`, frozen in
`numba_checkpoint/<sha>/agent.py`. The candidate is
`8e7001995c76c1d3a3ad31b7054351b9436d2e64be4078ba53a5d24c9c7a33b3`, frozen in
`qcap_candidate/<sha>/agent.py`. `qcap_experiment.json` pins both identities.

This is a separate experiment from [QGEN.md](QGEN.md). That candidate stays
rejected under its own screening rule and its report is unchanged; only the
default-preserving `--experiment` flag was added to the shared harness.

## Change

The control built its quiescence move set by generating every legal move and
discarding the quiet ones. At a non-check node where the quiet-check condition
`ply < 3 and (tactical or attack)` is false, the discarded moves are the majority,
and each one still cost a `Move` object and a `_is_safe` call inside python-chess.

`captures_and_promotions` generates that set instead of filtering it. Two masked
calls to `generate_legal_moves` reproduce python-chess's own yield order:

1. `to_mask = occupied_co[not turn]` — every move landing on an enemy piece.
   This is every capture and every capture promotion, in the generator's order:
   non-pawn pieces first, then pawn captures with the four promotion pieces in
   `Q, R, B, N` order. Castling cannot appear, because `generate_castling_moves`
   masks our own rook squares, which are never enemy-occupied.
2. `from_mask = our pawns`, `to_mask = empty promotion squares | ep square` — the
   quiet promotions and the en passant captures, which the control's predicate
   accepts through `m.promotion` and `is_capture`'s en passant clause. An
   occupancy mask alone reaches neither: both land on empty squares.

The passes cannot overlap, because the first only yields moves onto occupied
squares and the second only onto empty ones, so there are no duplicates. The
second pass runs only when its mask is non-empty, so the ordinary node pays for
one masked generation. It is restricted to pawns, so a rook that attacks the en
passant square or an empty promotion square cannot leak in; both cases have
dedicated fixtures. Within the second pass the generator yields pawn advances
before en passant, which is the order the control's filter saw them in, so
concatenating pass one and pass two reproduces the control list exactly,
including equal-priority ties under the stable sort in `Engine.order`.

Nodes in check keep the original full generation, and so do nodes where quiet
checks are eligible, because `gives_check` has to see the quiet moves. The legal
move existence probe, the terminal, draw, stand pat and `MAX_PLY` exits and their
order are untouched, and the list is materialized before the search loop pushes.
Evaluation, Numba, the caches, the clocks, repetition, the transposition table,
`PASSIVE = True` and `ADAPTIVE = False` are unchanged, and no pruning, quiet
check extension or evaluation change was added. The only new definition in the
module is `captures_and_promotions`, which the equality gate asserts.

## Method

All compute uses the existing `chessathon-scope:test` image, one CPU, 2 GB memory,
128 processes, a read-only filesystem and workspace, 256 MB writable `/tmp` and no
network. `qgen_checks.py` validates both source hashes before loading either
module. The benchmark reuses the 24-position suite with three repetitions that
alternate control/candidate order per position, each query starting with empty
engine and evaluation caches. The host's idle `ai-development-orchestrator` app,
Postgres and Redis containers were the only other containers; no other benchmark
job ran.

## Equivalence

`qgen_checks.py targeted` was extended for this experiment. Selective generation
is compared directly against the control's predicate, `[m for m in
board.legal_moves if board.is_capture(m) or m.promotion]`, on 371 positions: the
reachable corpus plus 193 positions that actually offer en passant or a
promotion, found by seeded random play and supplemented by thirteen hand-authored
fixtures. The assertion is list equality, so ordering and the absence of
duplicates are both covered.

The fixtures cover en passant for both colours, two capturers of the same en
passant square, an en passant that is illegal through a horizontal pin, a rook
that attacks the en passant square, a quiet promotion, all four underpromotions,
a capture promotion combined with a quiet promotion, a pawn on the seventh rank
blocked from promoting, a rook that attacks an empty promotion square, a black
promotion by capture, a position offering promotion and en passant at once, and a
position with no captures at all. In every one the generated list equals the
control list and contains no quiet move.

Whole quiescence trees are compared as well: 552 matched trees over the reachable
corpus and 386 over the special corpus, at several plies with and without a
tactical pattern. At every quiescence node the recorded score, the quiescence node
count, the generated move list and the list after `Engine.order`'s stable sort are
identical, which is what covers equal-priority ties. Board FEN, move stack, `seen`,
`context` and `duplicates` are compared before and after every probe, including one
interrupted by a zero deadline. Terminal and draw precedence keep their case-by-case
assertions: checkmate at a 100-halfmove clock, stalemate and fifty-move at `MAX_PLY`,
insufficient material, threefold repetition, a check node keeping the full legal
move list, and a quiet node at the ply cap returning the static score.

`qgen_checks.py equality` adds AST identity for every module-level definition and
every `Engine` method except `quiesce`, confirms `captures_and_promotions` is the
only added definition and that none was removed, runs 2,400 exact evaluation
comparisons against the historical oracle including colour mirrors, checks the four
Numba signatures before and after gameplay calls, and asserts exact fixed-depth-two
moves, root scores and node counts on all 24 positions. Fixed-depth node counts are
identical position by position (mean 1,515.375, unchanged since the Numba experiment).

## Measurements

Twenty-four positions, three repetitions, 144 timed and 144 fixed-depth queries.
The predeclared criterion is the median across positions of the paired timed
nodes-per-second percentage gain, requiring at least 5%.

| Predeclared criterion | Value |
|---|---:|
| **Median paired timed NPS gain** | **+33.54%** |
| Mean / minimum / maximum | +31.29% / +7.98% / +54.24% |

Fixed depth is reported separately, as latency and as throughput.

| Metric | Control | Candidate |
|---|---:|---:|
| Fixed-depth median / P95 latency, ms | 65.80 / 300.62 | 45.91 / 221.75 |
| Fixed-depth median / mean NPS | 15,146 / 15,613 | 20,508 / 20,230 |
| Median paired fixed-depth NPS gain | — | +34.54% |
| Timed median / worst move, ms | 300.28 / 301.52 | 300.29 / 301.37 |
| Timed median NPS | 15,762 | 21,389 |
| Mean completed timed depth | 2.639 | 2.764 |
| Maximum RSS, MB | 180.73 | 180.71 |

No position regressed on timed throughput and none lost mean completed depth.
One position regressed on fixed-depth throughput, index 9, by 2.01%; it is a
616-node position whose quiescence is dominated by pawn structure rather than
captures, so the second masked pass earns little there. Worst move time was
301.37 ms for the candidate against 301.52 ms for the control, both far inside the
10,000 ms budget, and the smallest hard-deadline slack was -1.4 ms against the
control's -1.5 ms, the usual periodic-polling overshoot.

## Gate results

| Gate | Result |
|---|---|
| Ruff over the repository | passed |
| `mypy --strict`, agent and eight development modules | passed |
| Targeted invariants, generation equality, special moves, restoration | passed |
| Fixed-depth equality, 24 positions, moves / root scores / nodes | passed, exact |
| 2,400 evaluation comparisons and Numba signature stability | passed |
| Benchmark, predeclared median paired timed NPS gain ≥ 5% | passed, +33.54% |
| `timing.py` completed-depth gate, 0.05-ply tolerance | passed, 2.5625 → 2.7500 |
| `verify.py`, 500 reachable positions and special-move fixtures | passed |
| `determinism.py`, 12 positions and colour symmetry | passed |
| Bayesian units, 93 safe-set cases, lifecycle | passed |
| Official `make gate` two-game referee | passed, +2 =0 -0 |

### Historical source identity checks

Two checks assert that the original search code is byte-identical at the AST level.
They are historical identity checks, not behavioural gates, and this experiment
changes `Engine.quiesce` by construction, so they cannot pass and were **not run**:

- `passive_units.unchanged_search`, which compares `Engine.quiesce`, `order`,
  `enter`, `leave`, `drawn`, `reconstruct` and `finish` against `checkpoint/agent.py`.
- `numba_validation.py check`, which compares the whole `Engine` class against the
  submitted `4551f4e...` source.

Neither was edited or weakened, and neither is claimed to have passed. The
quiet-check experiment set the precedent of running `units`, `invariants` and
`lifecycle` for a search-code candidate while leaving these assertions intact.
Their purpose here is served by the behavioural evidence instead: identical moves,
root scores and node counts on the 24 fixed-depth positions, and 938 matched
quiescence trees with identical generated and sorted move lists.

## Paired smoke

Ten matched cases, twenty games, on the thirty held-out opening prefixes in
`quiet_openings.json`, seed 51000, 10,000 ms base and 100 ms increment. Each case
plays the candidate and the control against the same frozen control rival, with
colours reversed between consecutive cases and fresh official runner processes per
game.

| Smoke result | Control | Candidate |
|---|---:|---:|
| Games | 10 | 10 |
| W / D / L | 4 / 1 / 5 | 7 / 0 / 3 |
| Score, percent | 45.0 | 70.0 |
| Move milliseconds, median / P95 / worst | 185.60 / 288.15 / 305.10 | 211.48 / 289.79 / 307.41 |
| Median nodes/second | 14,807 | 20,312 |
| Mean completed depth | 2.519 | 2.210 |
| Passive overhead, median / P95 percent | 0.443 / 1.143 | 0.411 / 0.986 |
| Maximum initialization, ms | 2,823.43 | 2,837.21 |
| Maximum RSS, MB | 172.96 | 173.45 |

The paired difference is **+25 percentage points**, with a 95% colour-pair
bootstrap interval of **[-20, +60]** over five clusters; the opening bootstrap
gives the same, since twenty games span only five openings. The interval includes
zero, so this is a nonnegative screen, not evidence of a strength gain. All five
colour pairs were verified, both source hashes and the conditions matched across
every record, and there were no failures, illegal moves, flag falls or crashes.
The slowest move was 307.41 ms.

Mean completed depth is *lower* for the candidate in this table, which contradicts
every fixed-position measurement above. It is a game-path statistic: the two
configurations played different games, so the positions differ. The paired
comparison on identical positions is the one that governs depth, and there the
candidate gained: 2.5625 to 2.7500 mean ply in the `timing.py` gate and 2.639 to
2.764 in the benchmark. Median nodes per second rose from 14,807 to 20,312 even
across these different game paths.


## Decision

**Provisionally retained.** `agent.py` is the candidate,
`8e7001995c76c1d3a3ad31b7054351b9436d2e64be4078ba53a5d24c9c7a33b3`, and the diff
is preserved in `results/qcap/qcap-quiesce.patch`.

Every mandatory gate passed. Search behaviour is exactly preserved: identical
moves, root scores and node counts on all 24 fixed-depth positions, 938 matched
quiescence trees with identical generated and sorted move lists, and 371 positions
where selective generation equals the control filter move for move. The
predeclared criterion, the median across positions of the paired timed NPS gain,
was +33.54% against a 5% requirement, and the completed-depth gate improved rather
than regressed.

Retention is provisional and the smoke is a screen only. Twenty games over five
openings cannot establish a strength change; the interval [-20, +60] includes
zero, and no Elo claim is made or implied. What the smoke does establish is that
twenty full games ran with zero failures and no timing violation. A wider paired
screen on the thirty held-out openings is the natural next step, but no further
experiment was authorized here.


## Files and reuse

Changed: `agent.py`, `tests/qgen_checks.py` (a default-preserving `--experiment`
flag, the sorted-order recording, the special corpus and the generation-equality
coverage), `tests/README.md`, and this report. Added: `tests/qcap_experiment.json`,
`tests/qcap_candidate/<sha>/agent.py` and `tests/results/qcap/`. No frozen source,
no official harness file and no unrelated file was touched, and no package,
archive, commit, push or submission was produced.
