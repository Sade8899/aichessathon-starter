# Quiescence move-generation experiment

The control is the retained Numba engine, SHA-256
`59f99079f1db99221683dd3f06391f4fc502c1dae11fb712b08170242649830a`, frozen in
`numba_checkpoint/<sha>/agent.py`. The candidate is
`bb6ab82b95319283e6cdef6bf8a7b20ae3b185125942e041f9ca5352301a3fee`, frozen in
`qgen_candidate/<sha>/agent.py`. `qgen_experiment.json` pins both identities.
The original submitted `4551f4e...` remains only the historical evaluation oracle.

## Change

`Engine.quiesce` probed for a legal move with `any(board.generate_legal_moves())`
and then restarted generation with `board.legal_moves` to collect forcing moves.
Every non-check quiescence node that survived stand pat therefore paid twice for
`_slider_blockers`, the checker mask and pseudo-legal generation.

The candidate keeps one generator. `next(pending, None)` answers the stalemate
probe; the consumed move is put back at the head of the list with
`[first, *pending]`, which is materialized before the forcing filter runs,
because `board.gives_check` pushes and pops and the search loop pushes for real.
A stand pat cutoff returns before that list is built, so a cutting node still
generates exactly one move. The early-exit chain was split into a check branch
and a non-check branch to hold the generator without an optional-typed local;
the order of terminal, draw, stalemate, ply-cap and stand pat exits is unchanged.

No pruning, evaluation weight, opening asset, dependency or flag changed.
`PASSIVE = True`, `ADAPTIVE = False`, Numba evaluation, the import warm-up, both
caches, the clocks, transposition-table behaviour and passive SCOPE are untouched.
The rejected quiet-check extension was not revived; the depth-three quiet-check
rule in `order` and the `ply < 3` forcing rule are byte identical.

## Method

All compute uses the existing `chessathon-scope:test` image
(`sha256:d06cdcb427cdf1853de87e69c41dd3797b49070d3dec070f5929d36e64a42b5a`),
one CPU, 2 GB memory, 128 processes, a read-only filesystem and workspace,
256 MB writable `/tmp` and no network. `qgen_checks.py` validates both source
hashes before loading either module. The benchmark reuses the 24-position suite
from `numba_validation.suite()` with three repetitions that alternate
control/candidate order per position; every query starts with an empty engine,
transposition table and evaluation cache. Fixed-depth queries stop the
development clock at depth two; timed queries use the real `get_move(fen, 10000)`
clock. The host's `ai-development-orchestrator` app, Postgres and Redis
containers were idle during the run; no other benchmark container ran.

## Equivalence

`qgen_checks.py targeted` compares whole quiescence trees, not just root results.
Both engines run the same 184 positions (two reachable corpora plus the fixed
suite) at ply 0, 2 and 5, with and without a tactical pattern, giving 552 matched
trees; the recorded score, quiescence node count and the ordered move list at
every quiescence node are identical. Narrow windows over 60 positions add 116
stand pat cutoffs where the candidate produced no move list at all, which is the
direct evidence that a cutting node never generates the full set.

First-move preservation has dedicated coverage: over the corpus, 32 non-check
positions whose first generated legal move is a capture or promotion assert that
this exact move appears in the quiescence move list, and in 3 of them it is the
only forcing move, so dropping it would empty the list.

Terminal and draw precedence are asserted case by case, including checkmate at a
100-halfmove clock (mate wins over the draw exit), stalemate and fifty-move at
`MAX_PLY` (both return zero rather than falling through to `evaluate`, whose
value for the stalemate fixture is non-zero), insufficient material, threefold
repetition through the engine's own `seen` counter, a check node that keeps the
full legal move list, and a quiet node at the ply cap that returns the static
score. Board FEN, move stack, `seen`, `context` and `duplicates` are compared
before and after every probe, including one that a zero deadline interrupts.

`qgen_checks.py equality` adds AST identity for every module-level definition and
every `Engine` method except `quiesce`, 2,400 exact evaluation comparisons
against the historical oracle including colour mirrors, the four Numba signatures
before and after gameplay calls, and exact fixed-depth-two moves, root scores and
node counts on all 24 positions. Fixed-depth node counts are identical
position by position (mean 1,515.375, unchanged from the Numba experiment).

## Measurements

Twenty-four positions, three repetitions, 144 timed and 144 fixed-depth queries.
Gains are paired per position, then summarized across the 24 positions.

| Metric | Control | Candidate |
|---|---:|---:|
| Fixed-depth median / P95 latency, ms | 65.71 / 289.16 | 63.28 / 273.64 |
| Fixed-depth mean nodes/second | 15,734 | 16,332 |
| Timed median / worst move, ms | 300.35 / 301.74 | 300.32 / 303.51 |
| Timed median nodes/second | 15,819 | 16,667 |
| Mean completed timed depth | 2.653 | 2.708 |
| Maximum RSS, MB | 180.41 | 180.41 |

| Paired throughput gain | Median | Mean |
|---|---:|---:|
| Fixed depth, nodes/second | **+4.90%** | +3.94% |
| Timed, nodes/second | **+4.37%** | +4.47% |

Per-position regressions: fixed-depth throughput regressed at suite indices 14
(-3.11%) and 20 (-5.94%, the 68-node endgame, the smallest sample in the suite);
timed throughput regressed at index 4 (-1.54%). No position lost mean completed
depth. The worst candidate move time was 303.51 ms against 301.74 ms for the
control, both within the 10,000 ms budget; the smallest hard-deadline slack was
-3.5 ms for the candidate and -1.7 ms for the control, the usual periodic-polling
overshoot rather than a flag risk.

Reporting the same data unpaired, as the ratio of the two overall NPS medians,
gives +5.36% timed and +2.65% fixed depth. The paired per-position median is the
statistic that matches the design of the benchmark, and it is the one used below.

## Decision

The experiment is **rejected** on the screening threshold. The paired median
throughput gain is 4.90% at fixed depth and 4.37% timed, both below the 5%
screening threshold, so the run stopped there. The remaining correctness suite
and the 20-game smoke were not started, as the stopping rules require.

This is a screening decision, not a claim that the change is harmful or
worthless. The measured effect is real and consistent: 21 of 24 positions gained
fixed-depth throughput, mean completed timed depth rose slightly, and search
equivalence is exact. It is simply too small to clear the bar set for this
experiment, and the mean gain is smaller still than the median.

Ruff, `mypy --strict` over the agent and eight development modules, the targeted
invariant suite and the equivalence suite all passed on the candidate. The
official `make gate`, `verify.py`, `determinism.py` and the arena were not run,
because the throughput gate failed first.

One gate would have needed handling had the experiment continued:
`passive_units.unchanged_search` asserts `Engine.quiesce` is AST-identical to
`checkpoint/agent.py`. That is a historical identity check for the original
search, and the quiet-check experiment already established the precedent of
running `units`, `invariants` and `lifecycle` for a search-code candidate while
leaving the AST assertion untouched. No gate was weakened or edited.

## Restoration and preserved material

`agent.py` was restored from `numba_checkpoint/<control sha>/agent.py`, verified
by SHA-256 and by a byte comparison, and `git status` reports it unmodified.
`PASSIVE = True` and `ADAPTIVE = False` are unchanged. No unrelated file was
touched; no package, archive, commit or push was produced.

The experiment stays reproducible after the restoration, because `qgen_checks.py`
loads both frozen sources rather than the working `agent.py`:
`qgen_experiment.json` pins both identities, `qgen_candidate/<sha>/agent.py`
keeps the candidate source, `qgen_checks.py` keeps the harness, and
`results/qgen/` keeps the raw logs, the per-position report, the decision record
and `qgen-quiesce.patch`, the exact diff that was applied.

## Remaining work

Legal move generation still dominates the profile, and this experiment removed
only the cheapest of its duplicated work. A larger share is the forcing filter
itself, which generates every legal move and then discards the quiet ones; a
capture and promotion generator driven by `generate_legal_moves(from_mask,
to_mask)` over the opponent's occupancy would avoid producing them at all, and
would subsume this change. That is the next experiment worth screening, against
this same control and with the same paired gates.
