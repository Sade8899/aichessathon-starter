# Late-move reduction: predeclared experiment and diagnosis

Sections 0-6 are predeclared, written before any candidate `agent.py` existed.
Results are appended below `# RESULTS` and nothing above it is edited afterward.
Companion to [NULLMOVE.md](NULLMOVE.md); same isolation method.

## 0. Repository and control identity

| Item | Value |
|---|---|
| Branch | `main` |
| HEAD | `04ba28a69e4fa9702800510fac666f7e901ebbaa` — "Add per_fixture.csv to track null move test results" |
| HEAD note | the previous session's HEAD was `a9612f1`; between sessions the owner committed the null-move deliverables (`tests/NULLMOVE.md`, `tests/nullmove_probe.py`, `tests/results/nullmove/*`) as `04ba28a`. `git diff a9612f1..04ba28a` is **exactly those five files**; `agent.py` and every protected artifact (`submission 0609v4/`, `submission 0509v3/`, `tests/submitted/`, `tests/tournament/`) are untouched. |
| Working `agent.py` SHA-256 | `65ec40ceb29a8f6fe14a74ab2ed6ca446164f701e46d2b9d3fe8d5af94655bda` (protected control, intact) |
| Python | `.venv` CPython 3.13.5 (native Windows; Docker/WSL unavailable) |
| Deps | `chess==1.11.2`, `numba==0.67.0`, `numpy 2.5.3`, `ruff 0.16.6`, `mypy 2.3.1` |
| Newer PGNs (rounds 57-65) | **none present**; newest local PGN is round 56. No download. |
| External engine binaries | `tests/external_engines/` absent. |

**No LMR exists in the control.** `agent.py` is negamax + PVS: the first move is
searched full-window `(-beta, -alpha)`, every later move gets a zero-window scout
`(-alpha-1, -alpha)` and a re-search only when `alpha < score < beta`. There is no
reduction, no `_is_simple_endgame` / `_is_zugzwang_risk`, no verification search.

## 1. Hypothesis

The weakness is a 1-3 ply horizon deficit (mean completed depth ≈ 3.8; corrections
at depth 4-7; quiescence 88-95% of nodes; the round-55/56 failures complete depth
2-3; null-move pruning found no eligible node there). LMR reduces the **count of
full-depth sibling searches** rather than needing the search already deep, so it
might bite at the remaining depths this engine reaches and buy a completed ply.

**H1:** a conservative one-ply LMR on late quiet moves removes ≥ 20% of the whole
tree at the depths the engine actually completes, without breaking an enforced
fixture. **H0:** the reduced (depth-2) scout is barely above quiescence, its
fail-lows are unreliable at exactly the tactical positions that matter, and the
`remaining depth ≥ 3` requirement still excludes the shallow failures.

## 2. Exact permitted code scope (only if the opportunity gate passes)

Only `Engine.search`, in a frozen copy `tests/lmr_candidate/<sha>/agent.py`. Add a
one-ply reduction on the scout search of eligible late quiet moves, plus a
full-depth verification re-search when the reduced scout raises alpha, plus the
minimum constants. **No** change to `evaluate` / `numeric_evaluate`, `order`,
`quiesce`, `captures_and_promotions`, time allocation, TT size, repetition/50-move
logic, killers/history update, opponent model, or the public API. No new
dependency. No null-move, aspiration, futility, razoring, extensions, book, TB.

## 3. Proposed eligibility (adapted to this negamax/PVS engine)

A move is LMR-eligible only when all hold: remaining `depth ≥ 3`; `index ≥ 1` (not
the PVS first move); `index ≥ 3` (sufficiently late); `quiet` (not a capture, not a
promotion — the engine's own `quiet` flag); `not board.gives_check(move)`; the node
is not in check; `move != preferred` (TT move); `move` is not a killer for this
ply; not a genuine mate window (`MATE - MAX_PLY ≤ abs(alpha or beta) ≤ MATE`; the
±INF sentinel, 32000 > MATE, is *not* a mate window); not a repetition boundary
(`seen[key] ≥ 2` or `halfmove_clock ≥ 90`); `depth - 1 - 1 ≥ 1` so the reduced
search is still a real search, not a jump to quiescence.

## 4. Reduction amount, verification, re-search

Reduction is **exactly one ply** (`R = 1`); no other value tested in this
candidate. For an eligible late move: scout at `depth - 2` with the same
zero-window `(-alpha-1, -alpha)`. If that reduced scout **raises alpha**
(`score > alpha`), redo the scout at full `depth - 1` (still zero-window). Then the
normal PVS re-search on `alpha < score < beta` applies unchanged. Only a
full-depth result may set an exact score, update the PV, or be trusted in the TT.
A reduced result alone may never claim a forced mate; mate-distance packing
(`pack_mate` / `unpack_mate`) is unchanged.

## 5. Safety risks

1. **Missed alpha-raiser** — a reduced scout fails low on a move that a full search
   would have raised alpha with; the verification re-search only fires on a reduced
   *alpha-raise*, so a reduced *fail-low* is trusted and the move is under-searched.
2. **Mate suppression** — the reduced fail-low hides a forced mate that lives one
   ply past the reduced horizon.
3. Zugzwang / low-material endings where a "quiet" move is actually the only move
   holding the game.
4. Repetition: reducing a move that is forced to avoid a draw.
5. Determinism / board state after the shadow or verification search.

## 6. Measurements, opportunity gate, acceptance, rejection

**Probe (`tests/lmr_probe.py`).** A shadow reduced scout at every move the control
searches, with the TT, killers, history, repetition counters and node counter
swapped out and restored; compared against the control's own full-depth result for
that move. `equivalence` proves identical root move, completed depth, every root
score and exact node count vs the untouched control (probe disabled and enabled),
≥ 64 positions.

**Opportunity gate — build a candidate only if ALL pass:**
1. LMR eligibility ≥ 15% of full-depth main-search child searches at remaining
   depth 3 **and** at remaining depth 4.
2. ≥ 4 of the 6 target fixtures contain eligible LMR moves *before their completed
   iteration ends* (i.e. in the timed run at the historical clock).
3. Projected **total-tree** node reduction ≥ 20% (net of re-search overhead).
4. Zero suppressed forced mates in the shadow comparison.
5. Zero *consequential* missed alpha-raisers in the enforced corpus (a missed
   alpha-raiser whose real score would have become the node's new best).
6. The projected saving occurs at remaining depths the engine normally completes
   (≤ 4), not only at depth ≥ 5.

If any fails: **DIAGNOSIS ONLY**, `agent.py` byte-identical, one next action.

**If a candidate is built**, gates in order: Gate 1 static (3× import, ruff,
mypy --strict, `verify.py`, `determinism.py`); Gate 2 fixed-depth control-vs-
candidate (no illegal move, no false mate, no enforced-fixture or negative-control
regression); Gate 3 six target fixtures at historical clocks (≥ 1 of: +1 ply in
≥ 3 targets / correct ≥ 2 proven horizon failures without breaking another / ≥ 25%
node reduction with every verified result preserved); Gate 4 20-game paired smoke
vs control; Gate 5 ≥ 80-game paired tournament vs control (non-negative); Public
ladder ≥ 40 games each vs Shallow Blue 2.0 **iff the binary is obtainable from the
official upstream release with version/SHA/licence recorded** (currently absent).

**Rejection** on the first of: opportunity gate fails; any Gate 1/2 failure; Gate 3
satisfying none of its branches; a clearly negative Gate 4/5 paired score or a
strong colour regression; any new illegal move, crash or timeout.

# RESULTS

## 7. Method and equivalence

`tests/lmr_probe.py`, 5 unique textual splices into a dev copy of the control:
(A) `_lmr_probe(...)` per move before it is pushed; (B) node-counter mark before
the real full-depth search; (C) `_lmr_record(...)` after the move's score is
final; (D)/(E) TT read/write bypass while inside a shadow search. The probe runs a
real shadow reduced scout (`search(depth-2, -alpha-1, -alpha)`, `R = 1`) with
`killers, history, seen, context, duplicates, nodes` all swapped out and restored,
`Deadline` inside the shadow caught and discarded.

`python tests/lmr_probe.py equivalence` — **256 comparisons** (64 random positions
× depths 2,3 × {probe disabled, probe enabled}): identical root move, completed
depth, every root score and exact node count vs the untouched control. Every number
below describes the control's real tree.

Two probe bugs were found and fixed *before* any results were recorded, both
verified against equivalence: the mate-window guard must exclude a genuine mate
score (`|x| ≤ MATE`) not the `±INF` sentinel (`32000 > MATE`); the repetition guard
is `seen[key] ≥ 2` (next occurrence is the third), not `≥ 1` (an ordinary
transposition on the search stack).

Runs: `fixed --depths 2,3,4,5` (25 fixtures × 4 = 100 rows), `timed --clock-ms
90000` (25 fixtures, real wall clock; historical budgets for r46-30/r55-19/r56-17).
Raw: `tests/results/lmr/opportunity_{fixed,timed}.json`, `per_fixture.csv`.

## 8. Opportunity profile

### 8.1 Aggregate

| Quantity | fixed d2-5 | timed, real clocks |
|---|---:|---:|
| engine nodes | 7,564,913 | 828,557 |
| full-depth child searches | 2,068,806 | 400,104 |
| LMR-eligible / all child searches | 1.61 % | 2.30 % |
| **eligible / child at remaining depth 3** | **64.7 %** | **52.7 %** |
| **eligible / child at remaining depth 4** | **77.2 %** | **48.0 %** |
| reduced scouts | 33,370 | 9,213 |
| reduced fail-low | 33,007 | 9,135 |
| reduced alpha-raise (→ re-search) | 363 | 78 |
| correct saves | 26,238 | 7,915 |
| net saved nodes | 2,213,273 | 223,934 |
| **projected total-tree reduction** | **29.3 %** | **27.0 %** |
| net saved at remaining depth ≤ 4 | 2,469,787 (100 %) | 185,915 (81 %) |
| **missed alpha-raisers (all consequential)** | **209** | **15** |
| — of those, in the enforced corpus | **137** | **9** |
| **forced-mate suppressions** | 0 | **1** |

### 8.2 Opportunity gate

| # | condition | fixed | timed | verdict |
|---|---|---|---|:--:|
| g1 | eligible ≥ 15 % of child at remaining d3 **and** d4 | 64.7 / 77.2 % | 52.7 / 48.0 % | **PASS** |
| g2 | ≥ 4 of 6 targets with eligible moves before completed iteration | see 8.3 | **3 / 6** | **FAIL** |
| g3 | projected total-tree reduction ≥ 20 % | 29.3 % | 27.0 % | **PASS** |
| g4 | zero suppressed forced mates | 0 | **1** | **FAIL** |
| g5 | zero consequential missed alpha-raisers in the enforced corpus | **137** | **9** | **FAIL** |
| g6 | saving at remaining depths the engine completes (≤ 4) | 100 % | 81 % | **PASS** |

**3 of 6 conditions fail. No candidate is built.**

### 8.3 The six target horizon positions

Eligibility only appears once `Engine.search`'s `depth` argument reaches 3 — which,
because `Engine.choose` runs the root ply itself and calls `search(depth-1)`, means
the engine must **complete iteration 4 or deeper**. Under the real clock:

| Fixture | completed depth (timed) | eligible (timed) | eligible at fixed d4 / d5 | consequential missed at d5 |
|---|---:|---:|---|---:|
| r45-54-Rc7 | 5 | 564 | 159 / 564 | 2 |
| r46-30-Ka4 | 3 | 111 | 111 / 957 | 0 |
| r53-35-Rf7 | 4 | 285 | 85 / 1013 | 2 |
| **r55-19-g5** | **3** (timed 2) | **0** | 695 / 2161 | **11 / 36** |
| **r55-20-Nxd4** | **2** | **0** | 673 / 2422 | 0 / 3 |
| **r56-17-f4** | **3** (timed 2) | **0** | 606 / 2814 | **30 / 42** |

The three round-55/56 positions — the whole reason for the experiment — complete
depth 2-3 under their real 2.7-2.9 s budgets, so `search` never sees `depth ≥ 3`
and **LMR is inert there**, exactly as null-move pruning was. Worse: at the fixed
d4/d5 iterations they never reach, LMR at R = 1 produces **11, 0 and 30**
consequential missed alpha-raisers on r55-19, r55-20 and r56-17 (and 36 / 3 / 42 at
d5). It would degrade these positions, not correct them, if they ever searched that
deep.

### 8.4 Correctness cost is real, not boundary noise

Unlike the null-move probe (false cutoffs were all 1-14 cp), LMR's misses are
substantive. Typical: `reduced = alpha, real_score = alpha + 18…40` — the reduced
depth-2 scout, barely above quiescence, fails low on a move a full search ranks
20-40 cp higher, and the standard verification re-search does **not** fire on a
reduced fail-low. The one mate suppression (timed, r45-55-a4, `depth 6, ply 1,
index 11`): reduced depth-4 scout returns `alpha = 1494`; the full depth-5 search
finds **mate in 4 (29992)**. A trusted reduced fail-low hides it — rejection
criterion "never allow a reduced result to hide a forced mate."

The saves are genuine (27-29 % of the tree, ~80-100 % of it at remaining depth
≤ 4, re-search overhead only 5-10 % of the gross saving), but they are dominated by
the deep endgames that were never failures (r51-62, r51-66, r49-53, r52-40, r54-53:
40-85 % projected reduction each) and come with a correctness bill inside the
enforced corpus.

## 9. Verdict

**DIAGNOSIS ONLY.** `agent.py` unchanged, SHA-256
`65ec40ceb29a8f6fe14a74ab2ed6ca446164f701e46d2b9d3fe8d5af94655bda` — byte-identical.
No candidate created (the brief and section 6 forbid inventing one when the
opportunity gate fails).

**Rejection criterion: opportunity gate — conditions g2, g4 and g5 fail.**
- g2: only 3 of 6 target fixtures have any eligible LMR move before their completed
  iteration ends; the three shallow round-55/56 failures have zero, hitting the
  same `remaining depth ≥ 3` wall that stopped null-move pruning.
- g4: one forced-mate suppression (r45-55-a4).
- g5: 137 (fixed) / 9 (timed) consequential missed alpha-raisers in the enforced
  corpus, concentrated in the round-55/56 target positions themselves.

LMR has more raw opportunity than null-move (27-29 % projected reduction, mostly at
completed depths) but it buys nothing on the positions that define the weakness and
introduces a tactical-miss cost there. A blunt one-ply reduction with a trusted
reduced fail-low is unsafe for an engine whose reduced search is one ply above
quiescence.

## 10. One recommended next action

**Measure the internal-iterative-deepening / TT-move-quality gap instead.** Sections
8.3 shows the round-55/56 failures die at completed depth 2-3 with `search` never
reaching `depth ≥ 3`; ORDER.md already measured 85.6 % first-move cutoffs, so the
lever is not pruning the tree further but reaching the correcting *iteration* at
all. Profile — with the same shadow-equivalence method — how often the depth-2/3
search's chosen root move disagrees with the depth-4/5 move on the six targets and
the enforced corpus, and whether seeding the root move ordering from a cheap
depth-`d-2` search (internal iterative deepening at the root only, no new pruning)
changes the completed-iteration move. It is a single ordering-adjacent change,
touches no evaluation, and is falsifiable on the evidence already here.

## 11. Exact commands

```powershell
.\.venv\Scripts\ruff.exe check tests/lmr_probe.py
.\.venv\Scripts\mypy.exe --strict tests/lmr_probe.py
$env:PYTHONPATH = $PWD; .\.venv\Scripts\python.exe tests/verify.py
$env:PYTHONPATH = $PWD; .\.venv\Scripts\python.exe tests/determinism.py
.\.venv\Scripts\python.exe tests/lmr_probe.py equivalence
.\.venv\Scripts\python.exe tests/lmr_probe.py fixed --depths 2,3,4,5
.\.venv\Scripts\python.exe tests/lmr_probe.py timed --clock-ms 90000
```

## 12. Changed / added files

| Path | State |
|---|---|
| `agent.py` | **unchanged**, `65ec40ceb29a8f6fe14a74ab2ed6ca446164f701e46d2b9d3fe8d5af94655bda` |
| `tests/lmr_probe.py` | **new** — dev-only profiler, ruff + mypy --strict clean |
| `tests/LMR.md` | **new** — this file |
| `tests/results/lmr/opportunity_fixed.json` | **new** |
| `tests/results/lmr/opportunity_timed.json` | **new** |
| `tests/results/lmr/per_fixture.csv` | **new** |

No existing file, PGN, ZIP, frozen agent, manifest or historical result was
modified. Nothing was committed, pushed, packaged or submitted.
