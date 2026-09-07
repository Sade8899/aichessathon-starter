# Tactical search: a check extension capped at one ply per line

One isolated experiment against the promoted delta-pruning engine
`65ec40ceb29a8f6fe14a74ab2ed6ca446164f701e46d2b9d3fe8d5af94655bda`, which Phase 1
promoted into the working `agent.py`. The candidate is
`896ac0b24e5e7961ea5927e40f188163af4136e3b20faebf1f0cfc8e66fcc44d`, frozen at
`tactic_candidate/896ac0b2.../agent.py`. `tactic_experiment.json` pins both identities
and was written before any candidate code existed. The diff is in
`results/tactics/check-extension.patch`.

Delta pruning, time allocation, evaluation, opening handling, tablebase handling,
opponent adaptation, repetition and draw logic, transposition table size and the
dependency set are all unchanged. `agent.py` was never modified.

## Verdict

**Rejected.** Two predeclared rejection criteria fire, and either alone is
disqualifying:

1. **`verify.py` fails, deterministically.** It asserts `Engine.search(depth)` equals a
   plain minimax reference that recurses `depth - 1` unconditionally. A check
   extension necessarily breaks that equality. Reproduced twice, identical both times:
   `('promotion', 991, 981)`.
2. **Mean completed depth at a realistic clock falls 0.114 ply** on ordinary
   positions, 39 measurements shallower against 2 deeper, versus a directly measured
   noise floor of +0.003 ply.

The tactical gain is large and real — the holdout requirement was exceeded more than
fourfold — and it is recorded in full below. It does not rescue the candidate, because
the acceptance rule is that every condition passes.

## The corpus, and two corpora that failed first

No trusted reference engine is installed, the platform image ships none, and
downloading one is out of scope, so the labels had to come from somewhere else.

**Attempt one, engine-scored.** 243 positions where the control at fixed depth 6 had a
unique best move by more than 150 cp. Every one of the 243 was solved at depth 2. A
hanging queen is a clear margin the shallowest search already sees. Retained as the
previously-solved regression set, `tactics_solved.json`.

**Attempt two, shallow-versus-deep disagreement.** Keep positions where the control's
depth-3 move loses 150 cp or more by its own depth-6 assessment. This found nothing in
ten minutes of mining, and the reason is the useful measurement:

| over 14 random reachable positions | value |
|---|---:|
| depth-3 move identical to the depth-6 move | 11 of 14 |
| worst loss of the depth-3 move at depth 6 | 105 cp |
| positions losing 151 cp or more | 0 |

An engine labelled with itself agrees with itself. That is a limit of self-oracle
labelling, not a property of the engine's strength, and it is why the third attempt
abandons engine scores entirely.

**The corpus used: 251 forced mates in three.** `tactics.mating_moves` proves the line
from the rules, so a label owes nothing to the evaluation under test. A position is
kept only if a mate in three exists and no mate in two exists, and the mate-in-two
rejection is exact — it searches every first move and every reply. Every one of the
251 labels was independently re-proved after the fact: `mate_in: [3]`,
`shorter_mate_exists: 0`, `labels_reproved: 251`.

Split into 133 development and 118 holdout positions by a hash of the FEN, sealed
before the candidate existed and checked on every load. No constant was tuned against
the holdout.

Motif coverage, mechanically detected: mate threat 251, trapped piece 182, overloaded
defender 90, fork 68, sacrifice 58, skewer 49, deflection 29, discovered attack 16,
promotion 16, zwischenzug 12, recapture 11, pin 1. Check evasion and defensive-only do
not appear, because a position with the side to move already in check rarely has a
forced mate for that side. Deflection, overload and zwischenzug are detected by
geometric proxies and are the least reliable labels here.

### A defect found and fixed in the solver

The first mate solver iterated `board.legal_moves` lazily while pushing and popping
inside the loop, so python-chess generated moves for the wrong position. It was fixed
to materialise the list. **Every one of the 251 labels re-proves unchanged under the
corrected solver and the corpus seal is unchanged**, so no label moved. What did move
was the reported line shapes, and they moved in the direction that supports the
technique more strongly, not less: mean plies 4.56 to 5.00, pure check share 92.0% to
92.8%. The corrected figures are the ones used below and in the manifest.

## The measured bottleneck

The control, at fixed depth, over all 251 positions:

| depth | solved | rate | gain over previous depth |
|---:|---:|---:|---:|
| 2 | 163 | 64.9% | — |
| 3 | 172 | 68.5% | +9 |
| 4 | 173 | 68.9% | **+1** |
| 5 | 241 | 96.0% | **+68** |

Earliest solving depth: 163 at depth 2, 11 at 3, 5 at 4, **63 at 5**, and 9 never
within depth 5.

**Depth 3 to depth 4 gains one position out of 251. Depth 4 to depth 5 gains 68.** A
mate in three is five plies; the engine completes about four at a realistic clock
(ORDER.md measured a mean of 4.13 at 120,000 ms). The failure is a pure horizon
failure, and it is one ply wide.

The shape of the lines says which kind of ply is missing:

| line shape, 251 proved mating lines | value |
|---|---:|
| plies per line | **5, every line** |
| mean checks per line | 2.93 |
| lines that are pure check sequences | **233 (92.8%)** |
| lines containing a forced single reply | 186 (74.1%) |
| lines containing any recapture | **11 (4.4%)** |

### Why not the alternatives

- **Forcing move ordering** — rejected. Ordering cannot change a fixed-depth result: a
  mate five plies deep is absent from a four-ply tree whatever order moves are tried
  in. ORDER.md already measured main-search ordering at 85.6% first-move cutoffs.
- **Recapture extension** — rejected on measurement. 11 of 251 lines contain a
  recapture at all, mean 0.05 per line. The missed tactics do not cross the horizon
  during exchange sequences.
- **SEE for tactical ordering** — rejected, same objection as ordering, plus a per-move
  loop of new code. It reorders; it does not deepen.
- **Checks in quiescence** — rejected. This is the design QUIET_CHECKS.md already
  rejected, which collapsed completed depth from 2.438 to 1.667 ply and lost 27.5
  points over 240 games.

## The change

One constant and two methods.

```python
CHECK_EXTENSION_CAP = 1
...
        self.extensions = 0          # Engine.__init__
...
            extend = int(self.extensions < CHECK_EXTENSION_CAP and board.is_check())
            self.extensions += extend
            child = self.enter(board)
            try:
                deeper = depth - 1 + extend
                ...
            finally:
                self.extensions -= extend
```

The counter is incremented before descending and restored on the way back up, so it
counts extensions on the current root-to-leaf path only. Once the cap is reached depth
strictly decreases again, and `MAX_PLY` remains the outer guard, so no extension chain
is unbounded. The cap is one because the measurement demands one additional ply and
nothing in it demands two; a larger cap was not tested.

`tactic_checks.py identity` asserts the scope mechanically against the manifest, which
declared it in advance: no definition added or removed, `CHECK_EXTENSION_CAP` the only
constant added, `__init__` and `search` the only `Engine` methods whose AST differs,
every other definition identical. `DELTA_MARGIN` is still 200, `ROOT_WINDOW_MARGIN` 60,
`TT_SIZE` 65536, `MAX_PLY` 96, `VALUES`, `MATE` and `INF` unchanged; the four Numba
signatures match; the `get_move` docstring matches; evaluation is identical over 108
comparisons; and the per-move budget is identical at 120,000 / 113,600 / 10,000 ms
(3.75 / 3.55 / 0.3125 s).

## What the candidate achieves

### Fixed depth, 251 proved mates

| | depth 2 | depth 3 | depth 4 | depth 5 |
|---|---:|---:|---:|---:|
| control solved | 163 | 172 | 173 | 241 |
| candidate solved | 163 | 170 | **249** | **249** |
| change | 0 | **-2** | **+76** | +8 |
| node ratio | 1.000 | 1.014 | 1.913 | 0.264 |

Holdout alone at depth 4: 78 to 117 of 118, **+39**. Never-solved within depth 5 falls
from 9 to 1. **Earliest solving depth improved on 71 positions and worsened on none**,
and there is no position the control solves at some depth and the candidate never
does.

The two depth-3 losses are both holdout and neither is a defect that survives:
in one, the control abandons the mate itself at depths 4 and 5 and both engines play
the same non-mating move there; in the other the candidate solves it again at depths 4
and 5. The depth-5 node ratio of 0.264 is not a saving in ordinary search — it is
`choose` stopping its iterative deepening as soon as it holds a mate score, which the
candidate reaches sooner.

### Realistic clocks, 1,506 paired counterbalanced measurements

251 positions x 3 clocks (100.0 / 113.6 / 120.0 s) x 2 repeats x 2 configurations, the
two engines measured back to back on each position with the order alternating. A
position counts as solved only if every repeat solved it.

| | control | candidate | change |
|---|---:|---:|---:|
| all 251, stably solved | 189 | **241** | **+52 (+20.7 points)** |
| **holdout 118, stably solved** | **86** | **113** | **+27 (+22.9 points)** |
| development 133, stably solved | 103 | 128 | +25 |
| newly unsolved | — | **0** | — |

The predeclared holdout bar was the larger of 2 positions and 5 percentage points,
which for 118 positions is 6 positions. The candidate gained 27. Every clock level
agrees: solved rate 75.7% to 97.2% at 100 s, 77.9% to 98.0% at 113.6 s, 78.9% to 97.8%
at 120 s.

## Why it is rejected anyway

### 1. `verify.py` fails

```
AssertionError: ('promotion', 991, 981)
```

`verify.py` builds a `reference` minimax that recurses `depth - 1` unconditionally and
asserts `Engine.search(board, 2, ...)` returns the same value. The candidate's search
is no longer depth-2 minimax on a checking line — that is the entire point of the
change — so it returns 991 where the reference returns 981. Reproduced twice with
identical values; the control passes the same assertion.

The candidate is not unsound. It searches an existing legal line more deeply, adds no
pruning, no speculative cutoff and no evaluation term. What it violates is an
equivalence the gate encodes, and **no extension-based technique can pass that gate as
written**. Making it pass would mean editing `verify.py`, which the experiment rules
forbid, so the gate stands and the candidate fails it.

### 2. Completed depth at realistic clocks

Measured on the quiet positional corpus — 30 held-out opening prefixes plus the
24-position suite — because on the mate corpus the candidate's completed depth falls
for a benign reason: it finds the mate and `choose` stops iterating, which is also why
its node ratio there is 0.43 while it solves more.

| quiet corpus, 324 paired measurements | mean completed depth | deeper | shallower | move agreement | node ratio |
|---|---:|---:|---:|---:|---:|
| **noise floor**, control under both names | 3.9105 → 3.9136, **+0.0031** | 8 | 7 | 0.9877 | 1.0032 |
| **candidate vs control** | 3.9074 → 3.7932, **-0.1142** | **2** | **39** | 0.8951 | 0.9800 |

The regression is 37 times the noise floor's magnitude, opposite in sign to it, and
one-sided: 39 shallower against 2 deeper, where the noise run produced 8 and 7. The
predeclared tolerance was 0.05 ply. This is the same failure mode that rejected
QUIET_CHECKS, an order of magnitude smaller (-0.114 against -0.847) but past the bar.

## Everything else that was run

| # | Gate | Control | Candidate |
|---|---|---|---|
| 1 | Mechanical identity against the predeclared scope | `65ec40ce...` | `896ac0b2...`, exact |
| 2 | Cold import, 3 fresh runner processes | 4.16 s median | 3.33 s median |
| 3 | `ruff check .` | pass | pass |
| 4 | `mypy --strict` | pass | pass |
| 5 | Official `make gate` | pass, +2 =0 -0 | pass, +2 =0 -0 |
| 6 | `verify.py` | pass | **FAIL** |
| 7 | `determinism.py` | pass | not reached |
| 8 | Passive units, invariants, lifecycle, 93 safe-set cases | pass | not reached |
| 9 | Round 30 rated regression | `d1h5`, depth 4, -338 cp | `d1h5`, depth 4, -338 cp |
| 10 | 600-ply clock safety, 300 budgets + 16 recurrences | 18,894 ms min, 0 flags | 18,405 ms min, **0 flags** |
| 11 | Previously solved fixtures, 243 positions, depths 2/3/4 | 243/243 | **243/243, 0 regressions** |
| 12 | Development tactical corpus | 103 stable | 128 stable |
| 13 | Sealed holdout tactical corpus | 86 stable | **113 stable** |
| 14 | Quiet positional corpus | — | see below |
| 15 | Repeated counterbalanced realistic clocks | — | 1,506 + 648 measurements |
| 16 | 20-game smoke | **not run** | **not run** |
| 17 | 240-game paired screen | **not run** | **not run** |

The smoke and the screen were not started. Two mandatory conditions had already failed,
so the stopping rule applies; QGEN.md set the same precedent when its throughput gate
failed first. Skipping them means **no playing-strength evidence exists for this
candidate at all**, and none is claimed.

`determinism.py` and the passive suites ran and passed for the control but were not
reached for the candidate, because `official_gate` stops a configuration at its first
failing command and `verify` precedes them.

### Quiet positional corpus, fixed depth

| depth | move agreement | disagreements | control nodes | candidate nodes | node ratio |
|---:|---:|---:|---:|---:|---:|
| 2 | 1.000 | 0 | 89,643 | 89,225 | 0.995 |
| 3 | 0.963 | 2 | 453,338 | 492,986 | 1.088 |
| 4 | 0.870 | 7 | 1,976,104 | 2,239,201 | **1.133** |

Node growth is well inside the predeclared 1.35x limit. Move agreement at depth 4 is
0.870, below the 0.95 the manifest predeclared — **that proxy was crossed**. It was the
wrong proxy: an extension is supposed to change some moves. Refereeing every
disagreement against a control search one ply deeper, the rule
`order_checks.adjudicate` already uses, shows the disagreements are harmless:

| quiet adjudication, depths 2-4 | value |
|---|---:|
| disagreements refereed | 9 |
| **tactical regressions** | **0** |
| improvements | 0 |
| worst candidate loss | **27 cp** |
| worst control loss | 31 cp |

So the governing criterion, "no serious regression in ordinary search", holds: the
candidate's worst disagreement costs 27 cp where the control's own costs 31 cp. The
predeclared numeric proxy failed and is reported as failed; the thing it was standing
in for did not.

## Limitations

- **No strength evidence.** No game was played with this candidate. The tactical gain
  is a solving-rate measurement on proved mates, not Elo.
- **The corpus is mates in three from random play at 40 to 160 plies.** Forced mates
  are almost absent from opening positions, so the population is late-middlegame and
  endgame material, and often lopsided. Finding the fastest mate rather than another
  winning move frequently does not change a game's result, so a +20 point solving gain
  should not be read as a comparable playing gain.
- **The oracle proves mates only through checking continuations** after the first ply.
  That can miss a mate, never invent one, so labels are sound but the set of positions
  is biased toward check-driven mates — which is also the bias that most favours the
  technique being tested.
- **Motif labels are geometric proxies.** Deflection, overloaded defender and
  zwischenzug in particular are approximations, and check evasion and defensive-only
  are unrepresented.
- **The cap was not tuned.** `CHECK_EXTENSION_CAP = 1` was fixed from the measured
  one-ply shortfall before the candidate existed. A cap of 2 was never tested.
- **`verify.py`'s reference is the binding constraint on this whole family.** Any
  extension, reduction or any other technique that makes `Engine.search(depth)` differ
  from depth-`depth` minimax fails it. That is worth knowing before a future experiment
  is designed, and it is a decision about the gate, not about this candidate.
- The candidate is **not promoted, not packaged, not committed and not submitted**, and
  no rated result is attributed to it.

## Preserved material

`agent.py` is the promoted control, verified byte for byte as
`65ec40ceb29a8f6fe14a74ab2ed6ca446164f701e46d2b9d3fe8d5af94655bda`.

- `tactic_experiment.json` — the predeclaration, both identities, the measured
  bottleneck, the alternatives and their reasons, and the acceptance and rejection
  rules, all written before the candidate existed.
- `tactic_candidate/896ac0b2.../agent.py` — the candidate source.
- `results/tactics/check-extension.patch` — the exact diff.
- `tactics_corpus.json` — 251 proved mates, sealed split.
- `tactics_solved.json` — 243 previously-solved fixtures.
- `tactics_quiet.json` — the 54-position quiet corpus.
- `tactics.py`, `tactic_checks.py` — the harnesses.
- `results/tactics/` — every raw log.

No test was weakened, no official harness file was touched, and no package, archive,
commit or push was produced.
