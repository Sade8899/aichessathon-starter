# Null-move pruning: predeclared experiment and diagnosis

Written before any candidate `agent.py` exists. Sections 1-6 are predeclared. Results
are appended below the `# RESULTS` marker and nothing above it is edited afterward.

## 0. Environment and repository identity

| Item | Value |
|---|---|
| Machine | Intel Core i7-12700, 12 physical / 20 logical cores, 32 GB RAM, Windows 11 Enterprise 26200 |
| Branch / HEAD | `main` / `a9612f11d05cc9be80420749fad2e25407e64296` (clone of `github.com/Sade8899/aichessathon-starter`) |
| `git status` | clean; no stashes; no other local branches |
| Docker / WSL | **unavailable** — Docker Desktop installed but its Linux engine is not running and cannot be started without the WSL2 backend / admin; `wsl` has no distro. All work is native Windows PowerShell per the revised brief. |
| Python | `.venv` created with `C:\Apps\Python315\python.exe` = **CPython 3.13.5** (no 3.12 present anywhere on the workstation; `pyproject.toml` requires `>=3.12`, satisfied) |
| Deps installed into `.venv` | `chess==1.11.2` (exact pin), `numpy==2.5.3` (pin is 2.5.2), `numba==0.67.0` (exact pin), `llvmlite==0.49.0`, `ruff==0.16.6`, `mypy==2.3.1` |
| Deviations from platform | Python 3.13.5 vs platform 3.12; numpy 2.5.3 vs 2.5.2. python-chess move generation (the thing the node-count profile depends on) is identical across these. |

### SHA-256 identities (recorded, not modified)

| File | SHA-256 |
|---|---|
| Working `agent.py` (control) | `65ec40ceb29a8f6fe14a74ab2ed6ca446164f701e46d2b9d3fe8d5af94655bda` |
| `tests/submitted/4551f4e…/agent.py` | `4551f4e4f2fc09fa56801e52a9ad38f0485ebb32d3c84aa4adbb4166fbb598c0` |
| `tests/submitted/59f99079…/agent.py` (Numba control) | `59f99079f1db99221683dd3f06391f4fc502c1dae11fb712b08170242649830a` |
| `tests/submitted/e5f63625…/agent.py` (v4 submitted) | `e5f63625a30f23ef7f1d625fbb5830f2bdbbed1b5480142a6e31b83cf731325b` |
| `submission 0609v4/agent.zip` | `267400857f19b0c5ca9d5a4d6640f42201db1f42be9ba8cafae5e8bd63c6a74b` |

## 1. The brief's premise vs. the actual code

The revised brief states that `agent.py` "already contains a section labelled
`Null-Move Pruning`" and refers to `is_max_turn`, a `max/min` search, and helpers
`_is_simple_endgame` / `_is_zugzwang_risk`. **None of this is in the working control.**
`agent.py` `65ec40ce…` is a clean **negamax** (`-self.search(board, depth-1, -beta,
-alpha, ply+1)`); the only occurrence of "null" is `chess.Move.null()` used as a
sentinel for `Engine.root_move`. There is no null-move pruning, no verification
search, no `is_max_turn`, and neither named helper exists. There is also no
`extension/` directory (the repo has `harness/`).

The task is therefore treated as the original standing plan: **profile the
opportunity for adding conservative null-move pruning to the main negamax search**,
and only build a candidate if the profile clears a predeclared bar. "Audit the
existing implementation" becomes "there is nothing to audit — measure whether adding
one is justified."

## 2. Hypothesis

The measured weakness (RATED_V4.md, CAPGEN.md, ORDER.md) is a 1-3 ply tactical
horizon deficit: mean completed depth ≈ 3.83, corrections live at depth 4-7,
quiescence is 89.5-93.6 % of all nodes, and CAPGEN measured that reaching the
correcting ply on r55 `19…g5` costs roughly a **4× speed-up**. Conservative
null-move pruning is the one eligible search-shape technique that can, in principle,
remove a large fraction of the *main-search* tree and buy a whole ply.

**H1:** A conservative null-move policy (not in check; `depth ≥ 3`; side-to-move has
non-pawn material; no mate-window; R = 2) fires often enough on the control's tree
to matter.

**H0 (expected to win, on the existing evidence):** null-move pruning acts only on
the ≈ 6-10 % of nodes that are main-search, and those near-leaf nodes carry small
quiescence subtrees, so the projected **total-tree** reduction is well below 20 %
and no additional completed ply is bought under the real clock.

## 3. Exact permitted code scope (only if a candidate is justified)

The candidate may change **only** `Engine.search` in a frozen copy under
`tests/nullmove_candidate/<sha>/agent.py`, adding a null-move block plus the minimum
constants. It must **not** touch: `evaluate` / `numeric_evaluate`, time allocation
(`choose` budget lines), `order`, `quiesce`, `captures_and_promotions`, the TT
size/shape, repetition/50-move logic, opponent model, or the public API. No new
dependency. No LMR, aspiration, futility, razoring, extensions, book or tablebase.

Required safeguards for the candidate: never null in check; never two null moves in
a row; disabled when side-to-move has no non-pawn piece and in low-material /
zugzwang-risk endings; disabled when `abs(beta) ≥ MATE - MAX_PLY`; the real board,
castling, en-passant and repetition state restored after the null search; a null
fail-high is **never** stored as an exact TT value; deterministic; no production
instrumentation.

## 4. Target fixtures

Six known horizon failures (historical clocks used where recorded):
`r45-54-Rc7`, `r46-30-Ka4`, `r53-35-Rf7`, `r55-19-g5`, `r55-20-Nxd4`, `r56-17-f4`.

Plus, for regression and negative-control coverage: all 19 enforced fixtures in
`tests/rated_v4_positions.json` (including the winning-game negative controls
`r47-63-e2`, `r48-26-Qxf6`, `r49-53-h3`, `r50-35-d3`, `r52-40-c7`, `r52-55-Re4` and
`r54-53-Ke5`), the repetition objective-state `r51-79-repetition`, the round-30
`Qa5+` regression `round30-Bf4`, and the pawn-only / low-material endings among the
above (`r45-*`, `r51-*`, `r53-52`). Both agent colours are represented
(white-to-move: r46, r51, r56, r52; black-to-move: r45, r53, r55, r47, r49, r50).

## 5. Correctness risks

1. **Zugzwang** — null pruning in pawn endings and low-material positions returns a
   false fail-high where passing is actually bad. r45, r51, r53-52 are exactly this
   shape.
2. **Mate blindness** — a null cutoff near a mate boundary can suppress a real mate
   (the losses in r46/r51/r53 turn on *quiet* checks two-plus plies past the leaf).
3. **Repetition contamination** — the null "position" must not be entered into
   `seen` / `context` / `duplicates`; a leak corrupts threefold detection.
4. **TT poisoning** — storing an unverified null bound as exact would hand later
   iterations a wrong score.
5. **Determinism / state** — board, castling rights, ep square and halfmove clock
   must be byte-identical after every null search.

## 6. Measurements, acceptance, rejection

**Profile (this stage).** For the target corpus + all enforced fixtures, at fixed
depths 2-5 and at the historical clocks, the instrumented development copy
(`tests/nullmove_probe.py`, side-effect-free shadow search, proven equivalent to the
control) reports: main-search nodes, eligible nodes and their share of main-search
nodes, `stand ≥ beta` count, null attempts, null fail-highs, verified vs false
cutoffs (verification = the node's own full-width search result `best ≥ beta`),
false cutoffs that are actually fail-low or suppress a mate, distribution by depth /
phase / pawn-only / in-check / near-repetition, side-and-colour split, and the
projected node saving (pruned subtree cost − null-search cost, attributed once per
path).

**Go / no-go for building a candidate (predeclared):**
- proceed only if **≥ 10 % of main-search nodes** are eligible **and** the projected
  **total-tree** reduction is **≥ 20 %**, with **zero** false cutoffs that suppress a
  mate and a false-cutoff rate that a zugzwang guard removes.
- otherwise: **DIAGNOSIS ONLY**, `agent.py` left byte-identical, one next action
  recommended.

**If a candidate is built**, it must additionally pass, in order: Gate 1 static +
3× import + ruff + mypy + `verify.py` + `determinism.py`; Gate 2 zero false forced
mate claims and zero illegal state transitions over the target corpus; Gate 3 no
enforced-fixture and no winning-negative-control regression vs control; Gate 4
either **≥ 20 % fewer total nodes** at equal depth with verified tactics unchanged,
**or ≥ +1 completed ply in ≥ 3 target horizon positions** at the historical clock;
Gate 5 a 20-game colour-counterbalanced smoke vs control with no illegal move /
crash / init failure / timeout and a non-negative paired score; Gate 6 ≥ 80
colour-counterbalanced games vs control; Gate 7 ≥ 40 games each vs Shallow Blue 2.0
(control and candidate separately) — **only if the Shallow Blue binary is present**;
it is not currently in `tests/external_engines/` and downloading is out of scope.

**Rejection** fires on the first of: any Gate 1/2/3 failure; Gate 4 satisfying
neither branch; a clearly negative Gate 5/6 paired score or a strong colour
regression; any new illegal move, crash or timeout.

# RESULTS

## 7. Method

`tests/nullmove_probe.py` (native Windows, `.venv` CPython 3.13.5). The control
source is read, its SHA-256 asserted, and a development copy produced by three
unique textual splices: (1) a TT-read bypass while inside a shadow search, (2) a
`_nm_probe(...)` call immediately before the move loop of `Engine.search`, (3) a
`_nm_record(...)` call after the loop plus a TT-write bypass while inside a shadow
search. `_nm_probe`, at every main-search node that reaches the move loop, evaluates
conservative eligibility (not in check; remaining `depth >= 3`; `abs(beta) <
MATE - MAX_PLY`; side-to-move has a non-pawn, non-king piece) and, when
`evaluate(board) >= beta`, performs a real reduced null search
(`-search(depth-1-R, -beta, -beta+1)`, `R = 2`) with the transposition table,
killers, history, repetition counters and node counter **all swapped out and
restored**, so the control's own tree is never perturbed. `_nm_record` compares the
shadow cutoff against the node's own full-width result (`best >= beta` =
verified; otherwise a false cutoff) and attributes the pruned subtree cost
(`subtree_nodes - null_search_nodes`) once per root-to-node path.

**Equivalence proven.** `python tests/nullmove_probe.py equivalence`: 64
comparisons (16 random positions x depths 2,3 x {probe disabled, probe enabled}),
**identical move, completed depth, every root score and exact node count** vs the
untouched control. Every number below therefore describes the control's real search.

Runs: `python tests/nullmove_probe.py fixed --depths 2,3,4,5` (25 fixtures x 4
depths = 100 rows) and `python tests/nullmove_probe.py timed --clock-ms 90000`
(25 fixtures, real wall clock; historical per-move budgets for `r46-30`, `r55-19`,
`r56-17`). Raw: `tests/results/nullmove/opportunity_fixed.json`,
`opportunity_timed.json`, `per_fixture.csv`.

## 8. Null-move opportunity profile

### 8.1 Aggregate

| Quantity | fixed d2-5 (100 rows) | timed, real clocks (25 rows) |
|---|---:|---:|
| engine nodes | 7,701,040 | 1,821,515 |
| main-search share of tree | **5.2 %** | **12.2 %** |
| quiescence share of tree | 94.8 % | 87.8 % |
| **eligible nodes / main-search nodes** | **1.43 %** | **2.17 %** |
| null attempts | 5,781 | 4,824 |
| null fail-highs (cutoffs) | 3,270 | 2,657 |
| verified cutoffs | 3,261 | 2,634 |
| **false cutoffs** | 9 (0.28 %) | 13 (0.49 %) |
| false cutoffs suppressing a mate | **0** | **0** |
| **projected total-tree node reduction** | **22.2 %** \* | **12.5 %** |

\* The 22.2 % fixed-depth figure is an artefact of *where* the eligibility lives:
**every eligible node occurs only in the depth-5 iteration.** At every fixed depth
2, 3 and 4, across all 25 positions, there are **zero** eligible null-move nodes,
because the near-root nodes with remaining depth >= 3 are answered by a
transposition-table cutoff carried from the previous iteration before the null-move
point is reached. So the "22 %" is 22 % off an iteration the engine, completing a
mean of 3.83 ply in real play, mostly never runs.

### 8.2 Go / no-go gates (predeclared in section 6)

| Gate | Requirement | fixed | timed | Result |
|---|---|---:|---:|:--:|
| eligible nodes >= 10 % of main-search nodes | >= 10 % | 1.43 % | 2.17 % | **FAIL** |
| projected total-tree reduction >= 20 % | >= 20 % | 22.2 %\* | 12.5 % | **FAIL** |
| zero false cutoffs suppressing a mate | 0 | 0 | 0 | pass |
| false-cutoff rate removable by a zugzwang guard | — | 0.28 % | 0.49 % | pass |

Both quantitative gates fail. **No candidate is built.**

### 8.3 The six target horizon positions (timed, historical clocks)

| Fixture | stm | completed depth | eligible (% of main) | null cutoffs | false | projected saving |
|---|---|---:|---:|---:|---:|---:|
| r45-54-Rc7 | black | 5 | 855 (4.5 %) | 258 | 4 | 25.7 % |
| r46-30-Ka4 | white | 4 | 55 (1.1 %) | 42 | 0 | 27.8 % |
| r53-35-Rf7 | black | 5 | 214 (2.1 %) | 103 | 0 | 32.5 % |
| **r55-19-g5** | black | **3** | **0 (0.0 %)** | **0** | 0 | **0.0 %** |
| **r55-20-Nxd4** | black | **2** | **0 (0.0 %)** | **0** | 0 | **0.0 %** |
| **r56-17-f4** | white | **3** | **0 (0.0 %)** | **0** | 0 | **0.0 %** |

The three round-55/56 positions — the exact archetypes RATED_V4.md and CAPGEN.md
name as the horizon failure, where the control completes only depth 2-3 under its
real 2.7-2.9 s budget — have **no eligible null-move node at all**. Null-move
pruning compounds with depth: it needs the search already deep enough that
`depth - 1 - R` is a meaningful sub-search. A 3-ply search has no room to give
two plies back. Where the probe *does* fire (r51-66 47.8 %, r52-40 47.6 %,
r51-62 23.9 %) the position was already reaching depth 6-9 — i.e. never a problem.

### 8.4 Correctness picture for a hypothetical candidate

Clean, but moot. Over both runs: 0 cutoffs returned a mate score from a null
search (the `abs(beta) < MATE - MAX_PLY` guard holds by construction); the 22
false cutoffs are all 1-14 cp boundary cases where the zero-window null search
returns exactly `beta` and the full search lands a hair the other side — none
flips a won/lost verdict. 12 of the 22 concentrate in `r54-53-Ke5` (8) and
`r45-54-Rc7` (4), both low-material endings; the `r54-53` cluster is entirely
inside the `zugzw_risk` proxy and a zugzwang exclusion removes it. Distribution:
every eligible node is at remaining depth 3-4 (`R = 2`, `min_depth = 3`); no
eligible node was in check, in a mate window, or pawn-only-to-move (those are
excluded pre-count). Colour split (timed): white eligible 2,671 / black 2,153;
white false 4 / black 9 — no material asymmetry.

## 9. Verdict

**DIAGNOSIS ONLY. `agent.py` unchanged, SHA-256 `65ec40ce…` — byte-identical.**
No candidate was created (the brief and section 6 both forbid inventing one).
Rejection criterion: **go/no-go gate — both quantitative conditions fail**
(2.17 % eligible vs 10 % required; 12.5 % projected total-tree reduction vs 20 %
required), and the technique contributes exactly nothing on the three round-55/56
target positions that define the weakness.

This confirms, from an opportunity measurement rather than an implementation, what
CAPGEN.md concluded from the other direction: "the missing ply is not one
quiescence micro-optimisation away … that gap is structural." It is not one
main-search pruning technique away either, because the main search is only
5-12 % of the tree and, at the depths the engine actually completes, offers no
conservative null-move node.

## 10. One recommended next action

**Profile a bounded late-move reduction (LMR) in the main search the same way this
was profiled** — `depth - 1 - R` on late, quiet, non-forcing moves at shallow
remaining depth, with a re-search on fail-high — against the same six target
fixtures and the same enforced / negative-control corpus, using the identical
shadow-search equivalence proof. LMR reduces the *number of full-depth child
searches* rather than requiring the search to already be deep, so unlike null-move
pruning it can bite at remaining depth 3-4, which is where this engine lives.
ORDER.md's 85.6 % first-move-cutoff rate means the later moves LMR would reduce are
exactly the ones almost never raising alpha. One search-shape change, no
evaluation edit, falsifiable on evidence already in this repository.

## 11. Exact commands

```powershell
C:\Apps\Python315\python.exe -m venv .venv
.\.venv\Scripts\python.exe -m pip install "chess==1.11.2" numpy numba ruff mypy

.\.venv\Scripts\ruff.exe check agent.py tests/nullmove_probe.py
.\.venv\Scripts\mypy.exe --strict tests/nullmove_probe.py
$env:PYTHONPATH = $PWD; .\.venv\Scripts\python.exe tests/verify.py
$env:PYTHONPATH = $PWD; .\.venv\Scripts\python.exe tests/determinism.py

.\.venv\Scripts\python.exe tests/nullmove_probe.py equivalence
.\.venv\Scripts\python.exe tests/nullmove_probe.py fixed --depths 2,3,4,5
.\.venv\Scripts\python.exe tests/nullmove_probe.py timed --clock-ms 90000
```

## 12. Changed / added files

| Path | State |
|---|---|
| `agent.py` | **unchanged**, `65ec40ceb29a8f6fe14a74ab2ed6ca446164f701e46d2b9d3fe8d5af94655bda` |
| `tests/nullmove_probe.py` | **new** — dev-only profiler, ruff + mypy --strict clean |
| `tests/NULLMOVE.md` | **new** — this file |
| `tests/results/nullmove/opportunity_fixed.json` | **new** |
| `tests/results/nullmove/opportunity_timed.json` | **new** |
| `tests/results/nullmove/per_fixture.csv` | **new** |

No existing file, PGN, ZIP, frozen agent, manifest or historical result was
modified. Nothing was committed, pushed, packaged or submitted.
