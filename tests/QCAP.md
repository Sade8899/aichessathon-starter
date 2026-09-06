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

1. `to_mask = occupied_co[not turn]` â€” every move landing on an enemy piece.
   This is every capture and every capture promotion, in the generator's order:
   non-pawn pieces first, then pawn captures with the four promotion pieces in
   `Q, R, B, N` order. Castling cannot appear, because `generate_castling_moves`
   masks our own rook squares, which are never enemy-occupied.
2. `from_mask = our pawns`, `to_mask = empty promotion squares | ep square` â€” the
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
| Median paired fixed-depth NPS gain | â€” | +34.54% |
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
| Benchmark, predeclared median paired timed NPS gain â‰¥ 5% | passed, +33.54% |
| `timing.py` completed-depth gate, 0.05-ply tolerance | passed, 2.5625 â†’ 2.7500 |
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

**Provisionally retained.** `agent.py` is the candidate, which this section
recorded as `8e700199...` because the tested working tree used CRLF; the same
program with the repository's LF endings is `be5da869...`. See **Source identity
and reproducibility** below, and **Campaign decision** for the later validation.
The diff is preserved in `results/qcap/qcap-quiesce.patch`.

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


## Source identity and reproducibility

The candidate was written on Windows, so the bytes that every earlier qcap
measurement loaded used CRLF and hash
`8e7001995c76c1d3a3ad31b7054351b9436d2e64be4078ba53a5d24c9c7a33b3`. The same
source stored with LF hashes
`be5da8696f01924e2f752b1686e1d6a94b38dc72fff86a1f7fedb05f006c56e0`, which is what
the repository holds and what a clone produces. Two different raw hashes are two
different byte strings; they are not treated as equal anywhere.

`qcap_identity.py` proves the relationship rather than asserting it. It checks
both stored files against their hashes, then that `crlf.replace(b"

", b"
")`
is byte-for-byte the LF file and the reverse conversion returns the CRLF file,
that no bare carriage return or bare newline survives in either, that exactly
1,034 carriage returns separate 39,712 bytes from 38,678, and that the two files
split into identical line lists. Equal bytes under conversion is not by itself
proof that the programs are equal, so it also compares `ast.dump(ast.parse(...))`
and the SHA-256 of the marshalled compiled code object. Both are identical,
`e757629d88b2075b408cb6dfc738dc27ecbde30b8543334afeb501b78941f432`, while the
control's code object is `5cd8e15433b6fdb76b8521cc40d77a437e9849366379b1de36e8f41cb7d3a81b`,
confirming the pair is still two different programs.

Because the compiled code is identical, the evidence gathered against the CRLF
bytes describes the LF bytes as well, and was not re-run. That scoping matters: the
equivalence, benchmark, timing, correctness and 20-game smoke results reported
above were measured on the CRLF source `8e700199...`; the validation campaign
below was played on the LF source `be5da869...`. What carries across is the
program, established by the identical AST and code object, not the measurements
themselves. No result is relabelled as having been taken on the other file.

A fresh clone previously depended on the client's `core.autocrlf`. `.gitattributes`
now pins `agent.py`, the test modules, the test manifests, every frozen
`agent.py` under `tests/`, and the harness and baseline sources to `eol=lf`, so
raw-byte hashes reproduce on any platform. Raw records under `tests/results/`
carry no rule and keep the bytes their run produced. The working `agent.py` is the
LF source, `be5da869...`.

### The historical CRLF source is an explicit exception

A blanket `tests/**/agent.py text eol=lf` is wrong for one file. The frozen
candidate at `tests/qcap_candidate/8e700199.../agent.py` *is* the CRLF bytes: its
directory name is its raw-byte hash, and `qcap_identity.py` asserts that hash. Under
the broad rule Git stored it as the LF blob and would hand a clone LF bytes, so the
assertion would fail on a fresh checkout even though `git status` looked clean.
A clean `git add --renormalize` result only shows the working tree agrees with the
normalization rules; it says nothing about whether the historical bytes survived.

`.gitattributes` therefore carries an exact-path exception, placed **after** the
broad rule so it wins, that disables conversion in both directions:

```
tests/qcap_candidate/8e7001995c76c1d3a3ad31b7054351b9436d2e64be4078ba53a5d24c9c7a33b3/agent.py -text !eol
```

`-text` turns the newline filter off and `!eol` removes the inherited `eol`
setting that would otherwise still apply. `git check-attr text eol` reports
`text: unset` and `eol: unspecified` for that path, and `text: set`, `eol: lf` for
the LF checkpoint and for `agent.py`.

The file's bytes were recovered and accepted only against the required hash. The
working copy still held them, and reconstructing them independently from the
verified LF source with `lf.replace(b"
", b"
")` produced a byte-identical
result; both routes hash to `8e700199...`.

Preservation was then verified by round trip in two throwaway repositories under a
short temporary path, never touching this repository's index: the same
`.gitattributes` and the same three files, added, committed, then deleted from the
worktree and checked out again, once with `core.autocrlf=false` and once with
`core.autocrlf=true`.

| Path | Stored blob | Checkout, autocrlf=false | Checkout, autocrlf=true |
|---|---|---|---|
| historical CRLF | `8e700199...` | `8e700199...`, 1,034 CRLF, 39,712 B | `8e700199...`, 1,034 CRLF, 39,712 B |
| LF checkpoint | `be5da869...` | `be5da869...`, 0 CRLF, 38,678 B | `be5da869...`, 0 CRLF, 38,678 B |
| `agent.py` | `be5da869...` | `be5da869...`, 0 CRLF, 38,678 B | `be5da869...`, 0 CRLF, 38,678 B |

All six checkouts are byte-exact, so the object database now holds the CRLF bytes
and hands them back unconverted regardless of the client's `core.autocrlf`.

Historical pins are untouched: `qcap_experiment.json` still names the CRLF bytes
that the benchmark, equality and smoke runs actually loaded, and the CRLF file
stays frozen under its own hash. The validation campaign uses a separate manifest,
`qcap_validation.json`, which pins the LF candidate, the LF control, both file
paths, the CRLF hash it supersedes and the line-ending policy. `qcap_identity.py
loader` confirms the harness accepts those exact bytes, that only the candidate
defines `captures_and_promotions`, and that suite positions 0 and 2 reproduce the
recorded moves and node counts, 1,238 and 1,388.

## Validation campaign

Both stages use the thirty held-out opening prefixes in `quiet_openings.json` in
file order. No position was selected, replaced or reordered using any result.
Each matched colour pair is four official-referee games: the candidate and the
control each face the frozen Numba control as White and as Black, on the same
opening, seed, rival source and clock.

The fast screen is 60 pairs, 120 cases, 240 games at 10,000 ms + 100 ms. Pair `p`
plays opening `p % 30` with seed `57000 + p`, so pairs 0-29 and 30-59 are the two
trials of each opening under different seeds. Six shards of 20 cases run on
logical CPUs 0, 2, 4, 6, 8 and 10, one thread from each of the host's six physical
cores, each container limited to one CPU and 2 GB.

**Declared before the screen was launched:** the full-clock confirmation, which
runs only if the screen's paired point estimate is nonnegative with zero failures,
uses the **first ten openings of `quiet_openings.json` in file order**, one
four-game matched colour pair each: pairs 0-9, cases 0-19, 40 games at 120,000 ms
+ 500 ms, seed 58000, run serially in a single one-CPU container.

### Fast screen result

Six shards, all `exited 0`. The merge accepted one identical `RUN` record across
every shard (both source hashes, seed 57000, 10,000 ms base, 100 ms increment),
120 complete case pairs with no duplicate or missing case, 60 verified colour
reversals, 30 distinct openings and 60 distinct seeds. Every case matched on
opening, seed, colour and rival between the two configurations.

| Fast screen, 10 s + 0.1 s | Control | Candidate |
|---|---:|---:|
| Games | 120 | 120 |
| W / D / L | 51 / 8 / 61 | 73 / 4 / 43 |
| Score, percent | 45.83 | 62.50 |
| Move milliseconds, median / P95 / worst | 205.54 / 290.74 / 305.48 | 207.54 / 290.20 / 309.05 |
| Median nodes/second | 8,473 | 11,541 |
| Mean completed depth, game paths | 1.824 | 1.903 |
| Passive overhead, median / P95 percent | 0.558 / 1.652 | 0.572 / 1.557 |
| Maximum / median initialization, ms | 5,560.80 / 4,117.68 | 5,070.66 / 4,136.31 |
| Maximum RSS, MB | 171.79 | 173.43 |

The paired difference is **+16.67 percentage points**. The colour-pair bootstrap
over 60 clusters gives a 95% interval of **[+6.67, +26.25]**; the opening
bootstrap over 30 clusters gives **[+5.42, +27.50]**. Both exclude zero.

There were **zero failures** across 240 games and 8,972 recorded moves: no flag
fall, no illegal or malformed move, no crash and no ply-cap draw. Every game ended
by rule — 228 checkmates, 6 threefold repetitions, 5 fifty-move draws and one
insufficient-material draw. The slowest single move was 309.05 ms against a
10,000 ms clock, and peak initialization was 5.56 s against the 90 s budget.

The mean completed depth in this table is a game-path statistic: the two
configurations played different games, so the positions differ. It is not
comparable with the identical-position measurements above, where the candidate
gained 2.5625 to 2.7500 mean ply in the `timing.py` gate and 2.639 to 2.764 in the
paired benchmark. Median nodes per second is likewise measured over different
positions here.

The point estimate is nonnegative and there were no failures, so the declared
full-clock confirmation was run.

### Full-clock confirmation result

Forty games at 120,000 ms + 500 ms, seed 58000, run serially in one container on
one CPU with 2 GB and no network. The openings are the ten declared before the
screen launched: `quiet_openings.json` entries 0-9 in file order, one four-game
matched colour pair each. The merge accepted 20 complete case pairs, 10 verified
colour reversals, 10 distinct openings, 10 distinct seeds and one `RUN` record
carrying both source hashes.

| Confirmation, 120 s + 0.5 s | Control | Candidate |
|---|---:|---:|
| Games | 20 | 20 |
| W / D / L | 3 / 12 / 5 | 5 / 7 / 8 |
| Score, percent | 45.0 | 42.5 |
| Move milliseconds, median / P95 / worst | 1,526.11 / 2,880.95 / 2,892.46 | 1,702.53 / 2,881.00 / 2,887.88 |
| Median nodes/second | 15,271 | 19,601 |
| Mean completed depth, game paths | 4.510 | 4.554 |
| Passive overhead, median / P95 percent | 0.116 / 0.256 | 0.121 / 0.253 |
| Maximum initialization, ms | 3,098.46 | 2,981.39 |
| Maximum RSS, MB | 198.90 | 199.64 |

The paired difference is **-2.5 percentage points**, with a 95% interval of
**[-20.0, +17.5]** from both the colour-pair and the opening bootstrap, since ten
pairs and ten openings are the same ten clusters. The interval spans zero widely.
Per-pair paired differences were -0.5, -0.5, -0.5, +1.0, 0, -0.5, 0, -0.5, 0, +1.0.

There were **zero failures** across 40 games and 2,773 recorded moves. All games
ended by rule: 21 checkmates, 15 threefold repetitions, 3 fifty-move draws and one
insufficient-material draw. The slowest move was 2,892.46 ms against a 120,000 ms
clock, and peak initialization was 3.10 s against the 90 s budget.

The two stages disagree, and the disagreement is the main finding. The candidate's
throughput advantage is present at both clocks — median nodes per second 8,473 to
11,541 at 10 s and 15,271 to 19,601 at 120 s — but at the full clock it buys almost
no extra depth, 4.510 to 4.554 mean ply, because each additional ply costs a large
branching factor. At the short clock the same advantage moves the search from 1.824
to 1.903 mean ply, where a fraction of a ply still changes the move chosen. That is
a consistent mechanism, not a contradiction, but it is a post-hoc reading of two
samples and is not established by them.

Draws also behave differently: at the full clock both configurations draw far more
often, 12 and 7 of 20 games, against the same rival, which shrinks the score
separation available to either side.

## Campaign decision

**Retention remains provisional, pending orchestrator review.** Nothing in this
campaign changes the engine, and no gate regressed.

What is now established: the source identity is reproducible from a fresh clone,
the executable code is unchanged by the newline fix, and 280 further games ran
with zero failures, no flag fall, no illegal move and no crash, at both a short
and a full tournament clock.

What is not established is a strength gain. The 240-game fast screen at 10 s gives
+16.67 points with both 95% intervals above zero, which is a strong result at that
clock. The 40-game confirmation at 120 s gives -2.5 points with an interval of
[-20.0, +17.5], which is uninformative on its own and certainly not evidence of a
gain at the clock the competition actually uses. The two samples are not pooled:
they use different clocks, different seeds and different opening subsets, and the
confirmation is one sixth the size by game count, 40 against 240. No Elo figure is inferred from either, and none
is inferred from the throughput measurements, which describe node rate rather than
playing strength.

The honest summary is that the optimization is exactly equivalent in search
behaviour, materially faster, clearly better at a fast clock on this rival, and
unproven at the full clock. The obvious next question, which this campaign was not
authorized to answer, is whether a full-clock screen with the sample size the fast
screen had would separate the configurations at all.


## Files and reuse

Changed: `agent.py`, `tests/qgen_checks.py` (a default-preserving `--experiment`
flag, the sorted-order recording, the special corpus and the generation-equality
coverage), `tests/README.md`, and this report. Added: `tests/qcap_experiment.json`,
`tests/qcap_candidate/<sha>/agent.py` and `tests/results/qcap/`. No frozen source,
no official harness file and no unrelated file was touched, and no package,
archive, commit, push or submission was produced.
