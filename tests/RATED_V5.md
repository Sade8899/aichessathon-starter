# Rated rounds 57-78: predeclared method, reproduction, and verdict

This document is written in two passes. Everything under
[Predeclared method](#predeclared-method) was committed to disk **before** any measurement
of a candidate and before `agent.py` was touched. Everything after it is the result.

## Identity

| Item | Value |
|---|---|
| Branch | `main` |
| HEAD at start | `a9612f11d05cc9be80420749fad2e25407e64296` |
| Working tree at start | clean |
| `agent.py` SHA-256 | `65ec40ceb29a8f6fe14a74ab2ed6ca446164f701e46d2b9d3fe8d5af94655bda` |
| `agent.py` size | 39396 bytes, 1046 lines |
| Python | 3.14.4, Windows-10-10.0.19045-SP0 |
| python-chess | 1.11.2 |
| numpy | 2.5.2 |
| numba | 0.67.0 |
| torch | 2.13.0+cpu |
| onnxruntime | 1.29.0 |

Protected artefacts, hashed and never written to:

| Path | SHA-256 |
|---|---|
| `submission 0509v3/agent_05_09.zip` | `4cf5c8885c49360f6a60328cdd062aa5a45e697e8ee5122d243639c675640dfd` |
| `submission 0509v3/agent_05_09_v3.zip` | `020340a3afcab4c41b2f0a1e2ab526a1bdd5e88b79338283d8af87a8b9136490` |
| `submission 0609v4/agent.zip` | `267400857f19b0c5ca9d5a4d6640f42201db1f42be9ba8cafae5e8bd63c6a74b` |
| `tests/submitted/e5f63625.../agent.py` | `e5f63625a30f23ef7f1d625fbb5830f2bdbbed1b5480142a6e31b83cf731325b` |
| `submission 0609v4/games/*.pgn` (rounds 44-56) | per `submission 0609v4/manifest.json` |

`agent.py` is the engine that actually played rounds 57-78, so unlike the rounds 44-56
study a rated error here is direct evidence about the file on disk.

## Existing tooling reused

Nothing in this study reimplements a probe that already exists.

| Need | Existing tool |
|---|---|
| PGN reading, clocks, fixed-depth `root`/`ladder`/`think` | `tests/rated_v4.py`, imported by path |
| Rounds 44-56 enforced fixtures | `tests/rated_v4.py fixtures`, `tests/rated_v4_positions.json` |
| Reference/numba evaluation equality, determinism | `tests/capgen_checks.py` (`equality`, `identity`, `fixed`, `timing`) |
| Colour symmetry and eval invariants | `tests/delta_invariants.py` |
| Tactics corpus, mate suppression | `tests/tactics.py`, `tests/tactic_checks.py` |
| Paired arena against a control | `tests/selection.py`, `harness/arena.py` |
| Packaging and archive limits | `harness/package.py`, `tests/smoke_archive.py` |
| Rounds 57-78 corpus, reproduction | `tests/rated_v5.py` (new; validation, scan, repro, fixtures) |

## Predeclared method

### Phase 1 - corpus

All 22 rated games of rounds 57-78 are retrieved as full PGNs and asserted against the
team page before any of them may become a fixture. `rated_v5.py validate` checks, per
game: the ten required headers; the start FEN legal, flagged `SetUp`, and not the standard
start; every move legal from it; colour, outcome and termination agreeing with the team
page; the final position actually satisfying the claimed termination and the mated side
matching the result; clocks present, never negative, and never above the 3.75 s ceiling;
the round header matching the filename. A game that fails any check is not eligible to
become a fixture. A PGN that cannot be retrieved is recorded as unavailable, never
reconstructed from prose.

### Phase 2 - reproduction at the historical clock

A rated move becomes a fixture candidate only if the control's own deeper fixed-depth
search rejects it. Each candidate is then replayed through `get_move` at **the exact clock
the platform recorded before that move**, from a cold engine, five independent times.
"Cold" means the source is re-imported from bytes and the previous module object dropped,
so no transposition table, evaluation cache, killer table or opponent model survives a
repetition.

Classification, decided per fixture and never by averaging:

| Class | Condition |
|---|---|
| A reliably reproduces | the unacceptable move is chosen in **at least 4 of 5** cold runs |
| B intermittently reproduces | chosen in 1-3 of 5 runs |
| C platform-speed artefact | chosen in 0 of 5, and the five runs agree with each other |
| D fixture/oracle unstable | chosen in 0 of 5, and the five runs disagree |
| E solved control | an acceptable move in all 5 runs, for a fixture drawn from a game the engine won |

Only class A counts as a reproducible failure. Round 78 is a solved control and must stay
class E throughout. Round 77 was an accurate threefold draw and is **not** treated as a
failure; it enters the corpus only as a control.

### Phase 3 - root cause

For each class A failure the committed move is compared against stable deeper searches and
the acceptable-move oracle, and assigned exactly one primary cause: passed-pawn urgency,
king safety, tactical exchange/material, repetition/conversion, mobility/positional,
remaining search horizon, or other-with-evidence. Before anything is proposed, the terms
already in `evaluate` are measured on the failing positions to establish whether the
required signal is absent, present but too weak, activated too late, duplicated, or
colour-asymmetric.

### Phase 4 - candidate gate

No candidate is written unless **all** hold:

1. at least two class A failures share the same measurable evaluation deficiency;
2. the proposed signal separates those failures from the solved controls;
3. the change fits one bounded evaluation section;
4. no search, time-management, move-ordering or legality change is needed;
5. the term is exactly symmetric for White and Black;
6. the term does not reward a pawn for being advanced as such - it must measure promotion
   urgency and whether the pawn can actually be stopped.

Failing any of these, the study stops at **DIAGNOSIS ONLY**.

### Phase 5 - candidate shape

At most one candidate, addressing urgent passed-pawn danger, implemented in both
`evaluate` and `numeric_evaluate` and required to stay exactly equal. It may weigh
promotion distance, whether the defending king or a piece reaches the promotion path in
time, connected and protected passers, rook or queen support behind the passer, whether
stopping the pawn costs material, and side-to-move tempo. It may not add a general large
passer bonus, use tablebases or the network, special-case any fixture FEN, round, move or
colour, alter search depth or time allocation, or add nondeterminism. The smallest
plausible weight is tried first; no uncontrolled sweep.

### Phase 6 - acceptance gates

All twelve must pass, each with a recorded command and evidence path.

| # | Gate | Threshold |
|---:|---|---|
| 1 | import, `ruff check .`, `mypy` | clean |
| 2 | existing verification and determinism suites | pass |
| 3 | exact colour symmetry | pass |
| 4 | legality, timeout, exception, protocol regressions | zero |
| 5 | class A failures fixed | at least 50% |
| 6 | solved target fixtures worsened | zero |
| 7 | forced mates suppressed | zero |
| 8 | fixed-depth enforced corpus (v4 + v5) | no consequential regression >= 40 cp, no outcome-class regression |
| 9 | paired games vs untouched control | at least 80, colour- and opening-balanced |
| 10 | candidate score | >= 55%, or a positive result whose interval justifies promotion under the repository's existing policy |
| 11 | throughput regression | no worse than 5% |
| 12 | critical targeted checks repeated after a cold restart | pass |

Any failed gate rejects the candidate and `agent.py` is restored to
`65ec40ce...` byte-for-byte.

### Phase 7 - release

Only on a clean sweep: full smoke and submission verification, package via
`harness/package.py`, inspect archive members and limits, record SHA-256 of `agent.py` and
of the archive, produce the diff against the control, and commit the candidate with its
evidence. Nothing is uploaded without explicit approval.

The default verdict is to retain the control.

---

# RESULTS

**Verdict: DIAGNOSIS ONLY. No candidate was built and `agent.py` was never modified**
(`65ec40ce...`, 1046 lines, 39396 bytes, unchanged at the end of the session). The
candidate gate fails at condition 2, on measurement, and the failure is reported below with
the numbers that produced it.

Note on HEAD: the session began at `a9612f1`. During it the working tree advanced to
`f80341c` — three of the owner's own commits (null-move, LMR, root-order and
platform-reproduction deliverables) dated 2026-09-08.
`git diff a9612f1..f80341c -- agent.py` is empty, and every protected artefact keeps the
hash recorded above, so none of the baseline changed.

## Phase 1 — the corpus is complete

All 22 rated games of rounds 57-78 are present and validated. **Round 63 (vs LeoMax,
Black, win) was the only one missing** and was retrieved this session from
`https://aichessathon.com/game/e1754156-63f2-464e-af7b-2e3621d211d0`, written to
`submission 0609v4/aichessathon-round-63-leomax.pgn`
(`355658ea09c1aa3b36bdf924044965e84676f6e42fc05992e833e8d3b50877e2`). Nothing was
reconstructed from prose: the transcript was validated as a legal game from its `SetUp`
FEN ending in the checkmate its header claims, with clocks and colour matching the team
page. **No round is unavailable.**

`rated_v5.py validate` passes all sixteen checks on all 22 games
(`results/rated_v5/validation.json`). It also caught a transcription error of mine — round
75 was entered as a loss and is a win — which is what the check exists for.

**Record over rounds 57-78: 12 wins, 3 draws, 7 losses.** Losses 57, 58, 66, 70, 72, 74,
76; draws 61, 64, 77. Round 73 is a win by opponent illegal move, so no terminal board
condition is asserted for it.

## Phase 2 — reproduction at the historical clock

`rated_v5.py scan` laddered every one of the 669 own-moves in the seven losses, three draws
and the round 78 control at depths 2-5. Fourteen positions became fixtures: nine critical
(every move the control's own depth-5 search rejects by at least 100 cp) and five solved
controls from rounds 77 and 78. Each was then replayed five times, cold, at its exact
historical clock (`results/rated_v5/repro_5x.json`, `per_fixture.csv`).

| class | count | fixtures |
|---|---:|---|
| **A reliably reproduces (4/5 or better)** | **5** | `r57-22-e6`, `r58-55-Rh2`, `r58-56-e4+`, `r66-49-e5`, `r66-55-Kf6` — all 5/5 |
| B intermittently reproduces | 2 | `r57-24-Bxc7` (1/5), `r57-33-Rc3` (3/5) |
| C platform-speed artefact | 2 | `r57-59-Kd3`, `r58-84-Bd4` — 0/5; the engine plays the *correct* move here locally |
| D fixture/oracle unstable | 0 | — |
| **E solved control** | **5** | `r77-33-Rc7+`, `r77-53-Rc7+`, `r78-48-Kf6`, `r78-55-Be4`, `r78-60-b1=Q` — 5/5 each |

Round 78 stays solved on all three of its fixtures, including the defence against the
connected `h7`/`g6` passers and the conversion of its own. Round 77's accurate threefold
draw is reproduced 5/5 and is treated as a control throughout, never as a failure.

**Rounds 70, 72, 74 and 76 produced no fixture at all.** Their largest depth-5 swing is 86,
48, 47 and 35 cp respectively — below the 100 cp bar. Four of the seven losses contain no
decision the control's own deeper search rejects. In particular round 76, named in the
brief as a possible passed-pawn failure, has a **maximum error of 35 cp across all 75
moves**; it was not lost to a locatable blunder.

## Phase 3 — measured root cause

**Primary category: remaining search horizon.** Every class A failure is committed at the
depth the engine actually completed and rejected one to two ply deeper, and the rejection
strengthens with depth rather than fading (`results/rated_v5/deeper.json`):

| fixture | budget | used | completed | correcting | time to reach it | factor | loss d5 | d6 | d7 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| r57-22-e6 | 2.60 s | 2.50 s | 4 | 5 | 10.14 s | 3.9x | 124 | 138 | 139 |
| r58-55-Rh2 | 1.34 s | 1.29 s | 2 | 4 | 2.40 s | 1.8x | 128 | 328 | 429 |
| r58-56-e4+ | 1.32 s | 1.27 s | 4 | 5 | 1.75 s | 1.3x | 101 | 101 | 147 |
| r66-49-e5 | 1.53 s | 1.47 s | 4 | 5 | 1.68 s | 1.1x | 146 | 123 | 105 |
| r66-55-Kf6 | 1.37 s | 1.31 s | 4 | 5 | 1.93 s | 1.4x | 221 | 221 | 268 |

Every one used its full hard deadline: the engine was mid-iteration when time ran out, and
in three of the five it needed only **1.1x to 1.4x** more search to complete the iteration
that corrects it. This is the same mechanism `PLATFORM_REPRO.md` established independently
on rounds 44-56 ("3/3 consequential = 100% HORIZON"), now reproduced on a disjoint set of
games.

### The passed-pawn signal already exists, is symmetric, and is not the discriminator

The term is at [`agent.py:100-113`](../agent.py#L100-L113) with its Numba mirror at
[`agent.py:258-275`](../agent.py#L258-L275): `rank*rank*(40-phase)//24` plus a
king-distance term. Measured on the fixtures:

- It is **present once, not duplicated**, and **exactly colour-symmetric**: 1543 positions
  (fixtures plus random legal play), reference vs Numba splits **0**, colour asymmetries
  **0** in both implementations.
- It is **weak**: an enemy pawn on the seventh rank scores only **+38 to +58 cp** in these
  endings — roughly a third of a pawn for a pawn one move from a queen.
- It is **not the discriminator**. Correcting for the rule of the square, blockers, and
  defenders of the promotion square: **not one of the five class A failures contains an
  unstoppable advanced enemy passer.** In every case the pawn is already stopped — `d7` by
  `Ke7` in both round 58 fixtures, `a7` by the rook on the eighth rank and `g7` by the rook
  blockading `g8` in round 66. `r57-22-e6` has no advanced passer at all. The **only**
  genuinely unstoppable seventh-rank passers in the whole corpus belong to `r78-60-b1=Q`,
  a solved control in a game the engine won.

So the hypothesis in the brief — urgent passed-pawn *defence* failure — is measurably not
what these positions are. The pawns are contained statically; what the engine mishandles is
the dynamics of holding the blockade, and that lives past its leaf.

## Phase 4 — the candidate gate fails at condition 2

| # | condition | result |
|---:|---|---|
| 1 | two class A failures share one measurable deficiency | **met** — four of five sit next to a seventh-rank enemy passer scored at +38..+58 |
| 2 | the signal separates those failures from the solved controls | **FAILS** |
| 3-6 | bounded, search-free, symmetric, urgency-not-advancement | not reached |

Condition 2 was tested empirically, not argued. `rated_v5.py probe` builds the strongest
defensible form of the proposed term — scaled by promotion distance and gated on the
promotion path being clear, the promotion square undefended, and the defending king outside
the square of the pawn with the tempo counted — as a source transformation with the
pure-Python evaluation on both sides, so only that one expression differs. Run at weights
20, 40 and 80 over all 14 fixtures at depths 4 and 5, **84 comparisons**
(`results/rated_v5/probe_passer.json`):

- **Fixed: 0.** At no weight and no depth does it correct a single class A failure.
- **Broke: 5** — `r58-84-Bd4` at all three weights, `r57-33-Rc3` and `r66-49-e5` at 80.
- It perturbs the `r78-48-Kf6` solved control at weights 40 and 80.

A term that corrects nothing, damages positions the control gets right, and fires on a won
game's own passers is the inverse of the required signal. Per the predeclared method the
study stops here.

Condition 6 fails on the same evidence for a second reason: since every advanced enemy
passer in the failures is already stoppable, any term large enough to move these positions
would necessarily be rewarding advancement as such, which the method forbids.

## Gates

Gates 5-12 are not applicable: there is no candidate. What was run:

| gate | command | result | evidence |
|---|---|---|---|
| 1 | `ruff check .` | pass | all checks passed |
| 1 | `mypy` | pass | 9 source files, no issues |
| 2 | `rated_v4.py fixtures --source working` | **19/19 enforced** | `results/rated_v5/v4_fixtures_control.json` |
| 2 | `rated_v5.py fixtures --source working` | **14/14 enforced** | `results/rated_v5/v5_fixtures_control.json` |
| 3 | colour symmetry, 1543 positions | 0 asymmetries | see Phase 3 |
| 3 | reference vs Numba equality, 1543 positions | 0 splits | see Phase 3 |
| 12 | five cold restarts per fixture | determinism as tabled | `results/rated_v5/per_fixture.csv` |

`tests/delta_invariants.py` could not be run: it imports `tests/selection.py`, which
imports the POSIX-only `resource` module and fails on native Windows. The direct symmetry
and equality measurement above stands in for it and is recorded as a substitute, not as
that suite.

## What the corpus is worth to the next session

`tests/rated_v5_positions.json` is a regression floor the control already meets, 14/14,
built the same way as the rounds 44-56 corpus. Each fixture is anchored at a depth measured
to satisfy the control, and each critical fixture asserts only "do not play the rated move",
because the preferred alternative genuinely varies with depth. Both corpora together give
33 enforced fixtures across rounds 30 and 44-78.

One defect in the older harness is worth recording: `rated_v4.py fixtures` compares
`outcome["best"] != rejected` where `unacceptable_uci` may be a list, so a list-valued entry
can never fail. Exactly one fixture uses that form, `r56-18-Nxf7`, and it is
`enforced: false`, so no enforced gate is affected — but the control does in fact play the
move that fixture names as unacceptable, and it is silently reported as passing.
`rated_v5.py fixtures` uses set membership and does not have the defect.
