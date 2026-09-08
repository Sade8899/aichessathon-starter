# Platform reproduction & failure-mechanism attribution: predeclared diagnosis

Sections 0-11 are predeclared, written before `tests/platform_repro.py` was built.
Results follow `# RESULTS`; nothing above it is edited afterward. Companion to
[NULLMOVE.md](NULLMOVE.md), [LMR.md](LMR.md), [ROOT_ORDER.md](ROOT_ORDER.md).

**Mode: DIAGNOSIS ONLY.** No candidate `agent.py` is produced.

## 0. Control identity

| Item | Value |
|---|---|
| Branch / HEAD | `main` / `304b9830c58e195074526d362aabec72f4be2486` ("Add root order probe…" — the owner committed the root-order deliverables since last session) |
| `git diff a9612f1..HEAD -- agent.py harness "submission 0609v4" "submission 0509v3" tests/submitted` | **empty** — `agent.py` and every protected artifact untouched; the only changes since `a9612f1` are the null-move / LMR / root-order deliverable files |
| Working `agent.py` | SHA-256 `65ec40ceb29a8f6fe14a74ab2ed6ca446164f701e46d2b9d3fe8d5af94655bda`, **1046 lines**, 39396 bytes |
| Python | `.venv` CPython 3.13.5 (native PowerShell; Docker/WSL unavailable) |
| Deps | `chess==1.11.2`, `numba==0.67.0`, `numpy 2.5.3`, `ruff 0.16.6`, `mypy 2.3.1` |
| CPU | 12th Gen Intel Core i7-12700, 12 physical / 20 logical cores |
| Platform reference | one core of an AMD EPYC 9V74 @ 2.60 GHz, 2 GB (per AGENTS.md) |

## 1. Corpus & completeness

**Local rated PGNs: rounds 44-56 only** (13 files, `submission 0609v4/games/`).
**Rounds 57-66 are still absent** — no new PGN anywhere in the repo, and
`aichessathon.com` still resets every TCP connection on this workstation. The
corpus is therefore **INCOMPLETE**: it does not cover rounds 57-66.

**Missing-fixture list (not analysed here; no position reconstructed from prose):**

| round | described failure (owner's note) | local data |
|---|---|---|
| 57 | tactical knight fork and rook loss | none |
| 58 | rook loss and failed endgame conversion | none |
| 59 | — | none |
| 60 | — | none |
| 61 | repetition in a favourable ending | none |
| 62 | — | none |
| 63 | — | none |
| 64 | insufficient-material draw | none |
| 65 | unstable tactical win with eight recorded blunders | none |
| 66 | failure to contain connected passed pawns (`61…Nb6 62.Rxb6`, `63.a8=Q`, `65.Rb8#`) | none |

**Analysed corpus = the 25 positions in `tests/rated_v4_positions.json`** (rounds
30, 44-56): 13 critical/enforced failure positions, 6 winning negative controls,
1 repetition objective-state, 5 context positions. It contains losses, wins and a
draw, so a future change can be tested for damage to already-correct decisions.

**Six established targets:** `r45-54-Rc7`, `r46-30-Ka4`, `r53-35-Rf7`,
`r55-19-g5`, `r55-20-Nxd4`, `r56-17-f4`.

## 2. Historical-clock source

`tests/rated_v4_positions.json` → each fixture's `rated_game`:
`clock_before_s`, `seconds_used`, `completed_depth_in_replay`,
`replay_reproduced_rated_move`. Cross-checked against
`tests/results/rated_v4/clock_ladder.json`, `ladder_r55_g5.json`,
`replay_rated.json`, `replay_r54_r56.json` and `RATED_V4.md`. The budget the engine
allocated is `min(clock_before_s/32, clock_before_s-0.025)`; hard deadline `0.96×`,
soft stop `0.65×`.

## 3. Platform-capacity calibration method

Strongest available calibration per fixture, in priority order:

1. **Historical node count** — available only for `r55-19-g5`
   (`ladder_r55_g5.json`: 54 720 nodes at an 87 000 ms clock, completed depth 2).
   Not recorded for any other fixture; **none is invented**.
2. **Historical completed depth + partial progress** — `completed_depth_in_replay`
   for all 25, valid where `replay_reproduced_rated_move == true` (23/25; **not**
   `r51-62`, `r51-66`, where the local replay diverged and the platform completed
   *fewer* plies, so their cap is treated as `< replayD`).
3. **Historical completed depth alone** — the fallback for `r51-62`/`r51-66`
   (cap at `replayD − 1`, flagged approximate).
4. **Wall-clock budget only** — measured but reported as the *local* i7 result,
   explicitly **not** a platform simulation.

The platform-capacity run caps the search at the calibrated completed depth
(a deterministic fixed-depth search) and, where a node count exists, additionally
caps at that node budget. The cap is applied only in the isolated probe
(`Engine` subclass / monkey-patch with full state restore); `agent.py` is
byte-identical throughout.

## 4. Deterministic reference-search method

Repo-accepted method: full-window exact score of every legal root move at the
deepest fixed depth `D` completed within a 60 s / ~4 M-node cap (`D ≤ 8`),
**required to agree with `D−1`** ("stable"). A mate is trusted only when scored as
a mate at both `D` and `D−1`. Successive-depth disagreements are recorded; an
unstable position is labelled **UNRESOLVED**, not forced to a conclusion. A deeper
move is never treated as better merely for being deeper.

## 5. Repeat count & timing discipline

Timed measurements run **sequentially**, one process, no background workers. Per
timed fixture: ≥1 untimed warm-up, then **5 measured repetitions**, every one
recorded. Report median / min / max / IQR of: completed depth, selected move,
score, nodes, elapsed seconds, whether the next iteration started and was
interrupted, and the root move being searched when the deadline fired.
Fixed-depth and fixed-node jobs are deterministic and may run in parallel but are
never used as timing evidence.

## 6. The four conditions per fixture

1. **A — local wall-clock**: `clock_before_s` on the i7, 5 timed reps.
2. **B — platform-calibrated**: fixed search capped at the calibrated completed
   depth; plus a node cap where a historical node count exists.
3. **C — fixed depth 1 … deepest feasible stable depth**: deterministic ladder.
4. **D — generous diagnostic budget**: the stable reference (section 4).

Recorded for every condition: chosen move, completed depth, partial next-iteration
progress, root-move index of the chosen move, score, nodes, elapsed, reference
score, centipawn loss, result-class delta (win `>150` / draw `[−150,150]` /
loss `<−150`), mate creation or suppression, repeatability.

## 7. Failure classification rules

Exactly one **primary** category per fixture (secondary categories optional):

1. **PLATFORM-SPEED ARTIFACT** — bad move under B, but A reliably (≥4/5) reaches
   deeper and corrects it.
2. **REPRODUCES LOCALLY** — the rated (or an equivalently losing) move in ≥3/5
   platform-calibrated repetitions / the deterministic B run.
3. **HORIZON FAILURE** — a *stable* correction appears only after ≥1 additional
   full ply (`correcting_depth > platform_depth`, correction stable over 2 depths).
4. **QUIESCENCE-BOUNDARY FAILURE** — the correction becomes visible when a
   forcing capture / check / promotion / recapture crosses from the main search
   into quiescence (i.e. correcting_depth = platform_depth + 1 **and** the
   refutation's first decisive move is a capture/check/promotion at the leaf).
5. **EVALUATION MISRANKING** — both candidates searched to comparable sufficient
   depth, but the leaf/backed-up evaluation systematically prefers the worse
   position. **Must be proven** by comparing leaf evals, backed-up scores and
   deeper continuations — never a catch-all.
6. **ENDGAME / PASSED-PAWN POLICY FAILURE** — promotion race, blockade, king
   activity, conversion, insufficient-material or repetition handling.
7. **REPETITION POLICY FAILURE** — engine knowingly repeats while a stable
   superior non-repeating move is inside its completed search.
8. **TACTICAL SEARCH FAILURE** — a forcing check/capture/fork/pin/mate/legality
   consequence missed *within* the nominal searched horizon.
9. **CLOCK-ALLOCATION FAILURE** — the game clock could safely support more search
   but the allocator stops early / distributes time poorly.
10. **UNRESOLVED** — unstable / incomplete / conflicting evidence.

## 8. Measurement thresholds

- "reproduces": rated-or-equivalent-loss move in ≥3/5 timed reps of B, or in the
  deterministic B fixed-depth run.
- "corrected by speed": A commits the reference (or an `acceptable_uci`) move in
  ≥4/5 reps while B does not.
- "consequential": cp loss ≥ 40 vs the stable reference, or a result-class change,
  or a mate suppression.
- "shared mechanism": ≥ 60 % of reproduced consequential failures in one primary
  category.
- reference "stable": identical best move at two successive feasible depths.

## 9. R55-20-Nxd4 special analysis

Measure and report: the depth at which `Nxd4` first becomes inferior and the depth
at which the correction (`Rg8`) becomes stable; the exact tactical/strategic event
entering the horizon; whether the refuting line begins with a check / capture /
threat / quiet move; whether quiescence searches the decisive continuation; the
shallow static evaluation of the `…Nxd4 Nxd4` branch vs the `…Rg8` branch; the node
cost to expose the correction; and whether a *narrowly defined* extension could
expose it at the platform-completed iteration. **Diagnostic only — no extension is
implemented.**

## 10. Decision gate (a future direction is justified only if ALL pass)

1. ≥ 3 complete failure fixtures reproduce under platform-calibrated capacity.
2. ≥ 60 % of reproduced consequential failures share one primary mechanism.
3. the proposed mechanism can affect the iteration the platform actually completes.
4. the evidence includes already-correct control positions the change must preserve.
5. the mechanism is not null-move, generic LMR or root reordering (already rejected).
6. the change is narrower than a broad evaluation rewrite.
7. expected benefit and principal regression risk can be stated quantitatively.

If any fails → **DIAGNOSIS ONLY**. If `r55-20` is the sole reproducible
consequential failure, classify it as an isolated quiescence boundary, a
selective-extension opportunity, a time-allocation issue, or an evaluation feature
gap — do not jump to "needs a full evaluation rewrite".

## 11. Evidence required before proposing any code change

A written mechanism proposal naming: the exact `agent.py` region it touches; the
fixtures it is predicted to correct and the depth/iteration at which; the
already-correct fixtures (incl. the 6 negative controls) it must not regress; a
quantified expected node/depth cost; the single most likely regression and its
magnitude; and a predeclared shadow-equivalence + acceptance/rejection gate of the
kind used for null-move, LMR and root-ordering.

# RESULTS

## 12. Method & equivalence

`tests/platform_repro.py` loads a dev copy of the control and re-implements the
`Engine.choose` root loop (`root_search`) with pluggable wall-clock / depth / node
caps; every node primitive is the untouched control. `python
tests/platform_repro.py equivalence` — **256 comparisons** (64 positions incl. all
25 fixtures × depths 2,3 × 2 reps): identical committed move, completed depth,
every root score, exact node count vs `Engine.choose`, **0 mismatches**.

The node cap is an `Engine` subclass overriding `tick()`; all other state is
per-call fresh. Timed runs (condition A) ran **sequentially, one process, no
background workers**, ≥1 untimed warm-up + 5 measured reps; IQR of elapsed time was
0.00–0.05 s across the six targets.

**Reference-search limitation.** The generous-budget reference (§4) uses a
full-window score of *every* root move at each depth, capped at ~4 M nodes / 60 s.
On wide 30-40-move positions this cap truncates the deepest ladder rung and the
"best move at `D`" can disagree with `D-1` for a mechanical reason rather than a
real instability — so `r44-14`, `r44-21`, `r53-35`, `r53-52`, `round30`, `r54-53`,
`r51-66` came back `UNSTABLE`. Where the fixture's own `depth_ladder` (from the
repo's `rated_v4.py scan`) is unambiguous, that ladder is used as the reference in
the narrative below and the row is re-classified; `r53-52`, `round30`, `r54-53`
stay `UNRESOLVED` (they are proven-mate-against / already-solved positions and are
not counted as reproducible failures either way).

## 13. Wall-clock (A) vs platform-capacity (B) — the six targets

Historical clocks from `rated_game`. B = fixed search capped at
`completed_depth_in_replay`. A = 5 timed reps at the exact historical clock.

| target | hist. clock | plat. depth (B) | B move / cp-loss | A median depth | A move (5 reps) | verdict |
|---|---:|---:|---|---:|---|---|
| **r45-54-Rc7** | 42.6 s | 5 | `Rc7` / **785** | **5** | `Rc7` **5/5** | **REPRODUCES LOCALLY** |
| r46-30-Ka4 | 68.7 s | 3 | `Ka4` / 985 | 4 | `bxa5` 4/5 (`Ka4` 1/5) | PLATFORM-SPEED ARTIFACT (borderline) |
| r53-35-Rf7 | 62.2 s | 4 | `Rf7` / 87 | 5 | `Re8` 5/5 | PLATFORM-SPEED ARTIFACT |
| r55-19-g5 | 90.2 s | 2 | `g5` / 102 | 3 | `Rf6` 5/5 (acceptable) | PLATFORM-SPEED ARTIFACT |
| **r55-20-Nxd4** | 88.0 s | 2 | `Nxd4` / **126** | **2** | `Nxd4` **5/5** | **REPRODUCES LOCALLY** |
| r56-17-f4 | 97.2 s | 2 | `f4` / 62 | 3 | `c4` 5/5 (acceptable) | PLATFORM-SPEED ARTIFACT |

The distinction the task demands is real: with a **flat 90 s** clock (last
session's root-order Phase 4) the i7 corrected 5/6 targets; at the **true, shorter
historical clocks** it corrects only 4/6. `r45-54` (42.6 s → 1.24 s budget) and
`r55-20` (88 s → 2.64 s) reproduce **deterministically, 5/5**. `r55-20` never even
finishes iteration 3 — every rep completes depth 2 and is interrupted in depth 3
searching root move `Bd7-e8`.

## 14. Reproduction status — every enforced failure fixture

| fixture | plat. depth | correcting depth | B reproduces bad move | A corrects (of 5) | primary category | secondary |
|---|---:|---:|:--:|:--:|---|---|
| r45-54-Rc7 | 5 | 6-7 | ✅ `Rc7` (785 cp) | 0/5 | **REPRODUCES LOCALLY** | HORIZON+1, ENDGAME/PASSED-PAWN |
| r45-55-a4 | 6 | — | both moves lose | 0/5 | *already-lost* (class 12) — not a fixable failure | — |
| r46-30-Ka4 | 3 | 4 | ✅ `Ka4` (985 cp) | 4/5 `bxa5` | PLATFORM-SPEED ARTIFACT | quiescence-boundary?, HORIZON+1 |
| r51-53-Qc5 | 2 | 4 | ✅ `Qc5` (150 cp) | 0/5 | **REPRODUCES LOCALLY** | HORIZON+2 |
| r51-62-Ke3 | ≤5 | 6 | ✅ `Ke3` (468 cp) | 5/5 `Kd3` | PLATFORM-SPEED ARTIFACT | HORIZON+1 |
| r51-66-Qc4 | ≤5 | 4 | ✖ (`Kb6` at d5) | 5/5 `Kb6` | PLATFORM-SPEED ARTIFACT | — |
| r53-35-Rf7 | 4 | 5 | ✅ `Rf7` (87 cp) | 5/5 `Re8` | PLATFORM-SPEED ARTIFACT | HORIZON+1 |
| r53-52-Bc6 | 5 | 6 (mate) | ✅ `Bc6` | 0/5 | UNRESOLVED (proven mate-against at d6; reference unstable) | — |
| round30-Bf4 | — | 4 | — | 0/5 | UNRESOLVED (reference unstable) | — |
| r54-53-Ke5 | 4 | 4 | ✖ | 0/5 (`Kh7`) | UNRESOLVED (already-solved fixture; reference unstable) | — |
| r55-19-g5 | 2 | 3 | ✅ `g5` (102 cp) | 5/5 `Rf6` | PLATFORM-SPEED ARTIFACT | HORIZON+1 |
| r55-20-Nxd4 | 2 | 4-5 | ✅ `Nxd4` (126 cp) | 0/5 | **REPRODUCES LOCALLY** | HORIZON+2 |
| r56-17-f4 | 2 | 3 | ✅ `f4` (62 cp) | 5/5 `c4` | PLATFORM-SPEED ARTIFACT | HORIZON+1 |

**6 negative controls** (`r47-63-e2`, `r48-26-Qxf6`, `r49-53-h3`, `r50-35-d3`,
`r52-40-c7`, `r52-55-Re4`): **6/6 commit the correct move under A and B.**

## 15. Aggregate mechanism distribution

| quantity | value |
|---|---:|
| enforced failure fixtures analysed | 13 (2 already-lost / mate-against, 3 unresolved reference) |
| **reproduce under platform capacity (B)** | **8** — of which **3 consequential** (≥40 cp): `r45-54-Rc7` (785), `r51-53-Qc5` (150), `r55-20-Nxd4` (126) |
| corrected by the faster workstation at the true clock (A) | 4 of the 6 targets; 6 of 8 reproduced-in-B failures |
| **reproduced consequential failures sharing one mechanism** | **3 / 3 = 100 % HORIZON** (correction 1-2 full plies past the platform-completed iteration, pivotal move quiet) |
| colour split of the 3 reproduced consequential | 2 black (`r45-54`, `r55-20`), 1 white (`r51-53`) |
| phase split | all 3 are endgame / late-middlegame |
| forcing vs quiet correction | **3 / 3 quiet** — `a4` (pawn push), `a6` (pawn push), `Rg8` (prophylaxis) |
| completed-depth distribution under A (targets) | d2 ×1, d3 ×3 (2 corrected), d5 ×2 (1 corrected) |
| clock headroom | none reproduced failure had spare clock — budgets were 1.2-2.9 s of a 1.3-3.0 s allowance (`available/32`), fully used; **no CLOCK-ALLOCATION failure** |
| mechanism explaining a meaningful proportion | **HORIZON** — 8/8 reproduced failures, 3/3 consequential |

## 16. R55-20-Nxd4 horizon trace (`results/platform_repro/r5520_trace.json`)

FEN `r4r1k/p2bn3/qpnBp2p/1R1p2p1/P1pP3P/2P2N2/2P1BPP1/R1Q3K1 b - - 0 20`.

| depth | overall best | `…Nxd4` branch score / PV | `…Rg8` branch score / PV | `Nxd4 − Rg8` |
|---:|---|---|---|---:|
| 2 | `Nxd4` | −106 · `Nxd4 cxd4` | −135 · `Rg8 hxg5` | **+29** |
| 3 | `Nxd4` | −106 · `Nxd4 cxd4 Bb5` | −113 · `Rg8 hxg5 Nf5` | +7 |
| 4 | `Rg8` | −106 · `… Bb5 axb5` | −102 · `… Nf5 Bf4` | −4 |
| **5** | `Rg8` | **−230** · `… Bb5 axb5 axb5` | −104 · `… Bf4 h5` | **−126** |
| 6 | `Rf3` | −232 | −112 | −120 |
| 7 | `Rf3` | −253 | −109 | −144 |

- **`Nxd4` first inferior at depth 4** (by 4 cp — noise); **stably inferior at
  depth 5** (126 cp), and it stays −230…−253 through depth 7.
- **Decisive event entering the horizon:** after `…Nxd4 c3xd4`, Black has traded
  off the knight that was the only piece able to reach the **f5 blockade square**.
  Black's try `…Bd7-b5` (a **quiet** move) is met by `a4xb5 a6xb5` and Black is
  **a bishop down for a pawn** with no compensation. That capture sequence sits
  4 plies past the platform's depth-2 leaf.
- **First move of the refuting line:** `c3xd4` (**capture** — the forced
  recapture). The move that *loses* the game, `…Bb5`, is **quiet**; the material
  swing `a4xb5 a6xb5` is behind it.
- **Does quiescence search the decisive continuation?** **No.** At depth 2,
  quiescence after `…Nxd4 c3xd4` sees the recapture but stops at the quiet `…Bb5`;
  the `a4xb5 a6xb5` swing is gated behind a quiet move and never enters
  quiescence. This is **not** a quiescence-boundary failure.
- **Shallow evaluations:** static after `…Nxd4 c3xd4` = **−214** (quiescence
  −92); static after `…Rg8` = **−33**. The leaf eval *already* rates the
  `Nxd4` branch worse — but the depth-2 *search* over-rates the `…Rg8 h4xg5`
  line at −135 (it counts White's pawn grab but not Black's `…Nf5` regain) and so
  prefers `Nxd4`. A **minor secondary EVALUATION-MISRANKING** component: the leaf
  eval does not value the f5 knight-outpost / blockade highly enough to flip the
  depth-2 ranking. `probe_passed_pawn.log` already showed eval tweaks do not move
  this position.
- **Node cost to expose:** depth 2 full-root ≈ 8.8 k nodes; depth 5 ≈ 388 k
  (~**44×**); the correction needs +2–3 completed plies.
- **Could a narrowly defined extension expose it at the platform iteration?**
  **No.** The pivotal move `…Bb5` is quiet, so no check / capture / recapture
  extension fires on it; a recapture extension on `c3xd4` adds one ply (reaches
  ~d3 on that line), still short of the depth-5 revelation. Check extensions were
  already measured and rejected (`verify.py` breakage, −0.114 ply).

**R55-20 is an isolated HORIZON failure**, secondary minor evaluation misranking;
**not** a quiescence boundary, **not** a clean selective-extension opportunity,
**not** a time-allocation issue.

## 17. Decision gate

| # | condition | result |
|---|---|:--:|
| 1 | ≥ 3 complete failure fixtures reproduce under platform-calibrated capacity | **PASS** — `r45-54`, `r51-53`, `r55-20` (consequential); 8 total in B |
| 2 | ≥ 60 % of reproduced consequential failures share one primary mechanism | **PASS** — 3/3 = 100 % HORIZON |
| 3 | the proposed mechanism can affect the iteration the platform actually completes | **FAIL** — all 3 are pure horizon with a **quiet** pivotal move; adding effective plies at the completed iteration is exactly what null-move, LMR and root-ordering were each rejected for, and no conservative selective extension fires on a quiet move (check extensions already rejected) |
| 4 | evidence includes already-correct control positions the change must preserve | **PASS** — 6/6 negative controls correct under A and B |
| 5 | mechanism is not null-move / generic LMR / root reordering | n/a — no mechanism proposed |
| 6 | change is narrower than a broad evaluation rewrite | n/a |
| 7 | expected benefit & principal regression risk stated quantitatively | n/a — no candidate mechanism survives gate 3 |

**Gate 3 fails → DIAGNOSIS ONLY.**

## 18. Verdict

**DIAGNOSIS ONLY.** `agent.py` unchanged, SHA-256
`65ec40ceb29a8f6fe14a74ab2ed6ca446164f701e46d2b9d3fe8d5af94655bda`, **1046 lines,
39396 bytes — byte-identical, before == after**.

- Under **faithful platform capacity** (fixed search capped at the historical
  completed depth), **all six targets and 8 of 13 enforced failure fixtures
  reproduce the losing move**; 3 are consequential (`r45-54`, `r51-53`, `r55-20`).
- At the **true historical wall clock on the i7**, the faster machine corrects
  4 of 6 targets — `r53-35`, `r55-19`, `r56-17` fully (5/5) and `r46-30` mostly
  (4/5). **`r45-54-Rc7` and `r55-20-Nxd4` reproduce deterministically (5/5)**;
  both are pure **HORIZON** failures whose correction lives 1-2 full plies past
  what the engine completes, behind a **quiet** move.
- The mechanism is unambiguous and it is the same one null-move, LMR and
  root-ordering were rejected against: **the engine does not complete a deep
  enough iteration, and the corrections are not reachable by any conservative
  search-shape change that fits inside the completed iteration.** R55-20 in
  particular is not a quiescence boundary, not a time-allocation issue, and not a
  clean selective-extension target; it carries only a minor, already-probed
  evaluation-misranking component.
- No new mechanism is justified. The two remaining local failures are horizon
  losses that this repo's own prior experiments have already shown are not
  reachable by in-scope search techniques.

## 19. One narrowly defined next action

**Before any code change, obtain rounds 57-66 and re-run this exact harness.**
The analysable corpus stops at round 56 and the four completed search-shape
experiments plus this reproduction study have converged on the same two
irreducible local failures (`r45-54`, `r55-20`), both horizon. Rounds 57-66 —
especially R66 (connected passed pawns) and R65 (eight recorded blunders) — are
the only source of *new* failure mechanisms; the described R66 loss (blockade /
passed-pawn containment) and R58 (endgame conversion) may be an
**endgame/passed-pawn evaluation** cluster distinct from the horizon cluster, and
if ≥ 3 of them reproduce under platform capacity with a shared non-horizon
mechanism, that — not another search-shape probe against `r55-20` — is the
justified next experiment. Retrieval currently fails (`aichessathon.com` resets
every connection here); the action is to retrieve those PGNs by whatever route the
owner can (another network, a manual export) and add them as byte-exact fixtures.

## 20. Exact commands

```powershell
.\.venv\Scripts\ruff.exe check agent.py tests/platform_repro.py
.\.venv\Scripts\mypy.exe --strict tests/platform_repro.py
$env:PYTHONPATH = $PWD; .\.venv\Scripts\python.exe tests/verify.py
$env:PYTHONPATH = $PWD; .\.venv\Scripts\python.exe tests/determinism.py
.\.venv\Scripts\python.exe tests/platform_repro.py equivalence
.\.venv\Scripts\python.exe tests/platform_repro.py measure --reps 5
.\.venv\Scripts\python.exe tests/platform_repro.py r5520
```

## 21. Changed / added files

| Path | State |
|---|---|
| `agent.py` | **unchanged**, `65ec40ceb29a8f6fe14a74ab2ed6ca446164f701e46d2b9d3fe8d5af94655bda`, 1046 lines, 39396 bytes |
| `tests/platform_repro.py` | **new** — dev-only harness, ruff + mypy --strict clean |
| `tests/PLATFORM_REPRO.md` | **new** — this file |
| `tests/results/platform_repro/measure.json` · `measure.log` · `r5520_trace.json` | **new** |

No existing file, PGN, ZIP, frozen agent, manifest or historical result was
modified. Rounds 57-66 PGNs were **not** retrieved and **no position was
reconstructed from prose**. Nothing was committed, packaged or submitted.
