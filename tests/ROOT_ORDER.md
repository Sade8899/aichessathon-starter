# Completed-iteration move quality & root ordering: predeclared diagnosis

Sections 0-9 are predeclared, written before `tests/root_order_probe.py` was built.
Results follow `# RESULTS`; nothing above it is edited afterward. Companion to
[NULLMOVE.md](NULLMOVE.md) and [LMR.md](LMR.md); same isolation method.

**Mode: DIAGNOSIS ONLY.** No candidate `agent.py` is produced by this task.

## 0. Repository & control identity

| Item | Value |
|---|---|
| Branch / HEAD | `main` / `2094e652bbf4d5a0dcdb1b1d143d76c9585dfccb` ("Add per_fixture.csv to store detailed chess engine analysis results" — the owner committed the LMR deliverables since last session) |
| `git diff a9612f1..HEAD` | only the null-move + LMR deliverable files; **`agent.py` and every protected artifact untouched** (`git diff --stat a9612f1..HEAD -- agent.py harness "submission 0609v4" "submission 0509v3" tests/submitted` is empty) |
| Working `agent.py` | SHA-256 `65ec40ceb29a8f6fe14a74ab2ed6ca446164f701e46d2b9d3fe8d5af94655bda`, **1046 lines**, 39396 bytes |
| Python | `.venv` CPython 3.13.5, native PowerShell (Docker/WSL unavailable) |
| Deps | `chess==1.11.2`, `numba==0.67.0`, `numpy 2.5.3`, `ruff 0.16.6`, `mypy 2.3.1` — unchanged; nothing new installed |
| Prior deliverables present | `tests/NULLMOVE.md`, `tests/nullmove_probe.py`, `tests/results/nullmove/`; `tests/LMR.md`, `tests/lmr_probe.py`, `tests/results/lmr/` |
| Rounds 57-66 PGNs | **not retrievable** — every TCP connection to `aichessathon.com` is reset (`WinError 10054` / `ECONNRESET`) from WebFetch and from a direct `urllib` probe on this workstation, though `pypi.org:443` was reachable in session 1. The domain is filtered here. No fixture is fabricated from the R66 prose summary. |

## 1. Audit of the real root search (line-level, `agent.py` `65ec40ce`)

`Engine.choose` lines 933-1026; `Engine.search` lines 817-915; `Engine.order`
lines 732-765.

| # | Question | Finding | Evidence |
|---|---|---|---|
| 1 | How are root moves ordered each iteration? | Iteration 1: `order()` with `preferred=None`, `ply=0` — MVV-LVA for captures/promotions, then history score (halved and carried across game moves, l.961), killers `+80000` (cleared each move, l.962), closed/endgame pattern bonuses, and a `+300` check bonus at `ply==0` when `pattern.tactical or attack`. Iteration `d>1`: `moves` is re-sorted by **this iteration's completed root scores `completed[m]`, descending** (swindle tiebreak = non-captures first). No `order()` re-call at the root after iteration 1. | l.967; l.997-1003; l.737-763 |
| 2 | Does the previous iteration's PV / scores / TT move seed the next? | **Yes, via the re-sort.** Previous completed iteration's scores order the next; the previous best move is `moves[0]` and is searched first. No *separate* root TT probe or explicit PV-move injection in `choose`. Child nodes (`ply>=1`) do get TT-move-first ordering inside `search`. | l.997-1004; l.843-844 |
| 3 | Does it already do root internal iterative deepening? | **Effectively yes.** Iterative deepening itself is the shallow-informs-deep mechanism: iteration `d`'s completed scores order iteration `d+1`. A separate depth-`(d-2)` root pass duplicates this for `d>=3`. Only **iterations 1 and 2** have weak seeding (history/captures for iter 1; noisy depth-1 eval+quiescence scores for iter 2). | l.970-1003 |
| 4 | Do root scores survive an interrupted iteration? | **No.** `except Deadline: break` (l.992-993) exits the `for depth` loop *before* `completed = current` (l.994). The interrupted iteration's partial `current` dict, and any re-sort of `moves`, are discarded. | l.976-994 |
| 5 | Can an unfinished deeper iteration influence the committed move? | **No.** Follows from #4: `completed`, the `moves` order, and `chosen = moves[0]` all retain the last *fully completed* iteration's state. | l.992-1004 |
| 6 | Do aspiration / PVS / TT bounds / stale scores distort root ordering? | The root uses a **reduced window**: `floor = max(-INF, root_best - ROOT_WINDOW_MARGIN(60) - 1)`, then `current[move] = -search(board, depth-1, -INF, -floor, 1)` (l.986-987). Move 0 gets a full-window **exact** score and anchors `root_best`. Any later move with true value `> root_best - 61` also gets an **exact** score. Moves with true value `<= root_best - 61` get an **upper bound** `<= root_best - 61` and their mutual order falls back to the stable sort's prior order. **Consequence: the argmax (committed move) at any completed iteration is reliable; only the tail ordering is not.** So root ordering cannot cause a *wrong commit at a fixed completed depth* — it can only help by making iterations cheaper so a deeper one completes within the clock. There is no aspiration re-search at the root and no PVS re-search at the root (the `alpha < score < beta` re-search is inside `search`, ply>=1). | l.985-991; l.878-883 |
| 7 | Time outside the main search | `evaluate` once (l.966), `order()` once (l.967), `reconstruct()` = one ply of legal-move generation over `self.pending` (l.917-931), `moves.sort` per iteration (`board.is_capture` only under `pattern.swindle`). Opponent-model frame building in `search` at `ply==1` is guarded and timed into `stats["model_seconds"]`: `verify.py` measures median **0.05 %**, worst **7.4 %** of move time. History is halved and retained across moves; killers cleared per move. | l.961-967; l.828-835; l.917-931 |

**Audit conclusion.** Policies **A** (previous PV move first) and **B** (previous
root scores descending) are **already implemented** by the l.997-1003 re-sort.
Policy **D** (depth-`(d-2)` root pass) is **redundant** with iteration-to-iteration
reordering for `d>=3` and can only matter for iterations 1-2. Policy **C** (TT move
first when trustworthy) is *not* done at the root explicitly, but a completed root
score is a strictly stronger signal than a TT bound. Policy **E** (use a completed,
comparable partial ordering from an interrupted iteration) is **genuinely not done**
(#4) and is the one non-redundant mechanism — but #6 limits its value, because the
interrupted iteration uses the same reduced window, so its partial scores are
either exact (`> root_best-61`) or clamped.

Because #6 shows the commit at a fixed completed depth is already the true best of
that iteration's search, this experiment's real question is narrow:

> **Can any bounded root-ordering change make the live engine COMPLETE a deeper
> iteration within the historical clock on the target positions, and does that
> deeper iteration commit a better move?**

## 2. Hypothesis

**H1:** a cheaper root ordering (fewer main-search nodes per iteration via more
first-move cutoffs) lets the engine complete iteration `d+1` instead of `d` on
>=4 of the 6 targets under their historical clocks, and that iteration commits a
move >=40 cp better (or fixes a result class) with no new mate suppression.

**H0 (expected, on the prior evidence):** ORDER.md already measures **85.6 %**
first-move cutoffs; the residual is ~14 % and a deeper iteration costs roughly the
branching factor (CAPGEN: reaching the r55 correcting ply costs ~4x), which no
reordering delivers. Policies A/B are already in place; C/D are redundant or
near-redundant; E is limited by the reduced window.

## 3. Target fixtures & enforced corpus

**Six established targets:** `r45-54-Rc7`, `r46-30-Ka4`, `r53-35-Rf7`,
`r55-19-g5`, `r55-20-Nxd4`, `r56-17-f4`.

**Enforced corpus:** the 19 `enforced: true` positions in
`tests/rated_v4_positions.json` (the six targets plus `r45-55-a4`, `r51-53-Qc5`,
`r51-62-Ke3`, `r51-66-Qc4`, `r53-52-Bc6`, `round30-Bf4`, `r54-53-Ke5`, and the
winning negative controls `r47-63-e2`, `r48-26-Qxf6`, `r49-53-h3`, `r50-35-d3`,
`r52-40-c7`, `r52-55-Re4`). Context rows are also profiled but never gate.

**Rounds 57/58/65/66:** not added — PGNs unreachable (section 0). The R66 motif
(Black failing to blockade connected White a+g passers, `61...Nb6 62.Rxb6`,
`63.a8=Q`, `65.Rb8#`) is recorded as narrative only; no FEN is inferred.

## 4. Historical time budgets

From `tests/RATED_V4.md` / `tests/CAPGEN.md`: `r46-30` had ~68.7 s clock (≈2.15 s
budget); `r55-19` ≈2.708 s budget; `r56-17` ≈2.919 s budget. Others default to a
90 000 ms clock (≈2.8 s budget) — the same convention the null-move and LMR timed
runs used. Budget = `min(clock/32, clock-0.025)`; hard deadline `0.96×`, soft stop
`0.65×`.

## 5. Oracle definition

The reference ("deep") move for a position is the argmax of a **fixed-depth**
search at the deepest depth `D` completed within a 60 s cap (`D <= 8`), **required
to agree with the argmax at `D-1`** ("stable"). Where `D` and `D-1` disagree, the
position is marked `unstable` and its disagreements are reported but excluded from
gate 3's numerator/denominator. Mate claims are trusted only when the score is a
mate score at both `D` and `D-1` (the repo's accepted method: a mate out of this
alpha-beta is sound because the defender generates every legal move). A deeper move
is **never** treated as better merely for being deeper (protocol Phase 4).

## 6. Move-quality metrics (per fixture, per depth d in 1..5)

Committed move `m_d`; its score under the oracle's depth-`D` scores `s_D(m_d)`;
`cp_loss_d = max_m s_D(m) - s_D(m_d)`; agreement `m_d == oracle_best`; whether
`m_d` or `oracle_best` is a mate; result-class of `s_D(m_d)` vs `s_D(oracle_best)`
(win `>150`, draw `[-150,150]`, loss `<-150`). For `oracle_best` at iteration `d`:
its index in the search order, the score iteration `d` assigned it, whether that
score was **clamped** (`<= root_best_d - 61`), nodes and seconds elapsed before it
was searched, and whether a PVS re-search occurred for it inside `search`.

Disagreement buckets: **harmless** `|cp_loss| < 15`; **material** `>= 15`;
**consequential** `>= 40` or a result-class change; **mate** (one side is a mate,
the other is not).

## 7. Shadow root-ordering policies (measured independently, agent.py unchanged)

| | policy |
|---|---|
| A | previous completed PV move first (rest unchanged) |
| B | stable previous-iteration root scores, descending (rest by `order()`) |
| C | TT root move first **iff** its stored `depth >= d-1` and `bound == 0` (exact); else `order()` |
| D | a cheap root-only depth-`(d-2)` ordering pass before iteration `d` (`d >= 3`) |
| E | seed iteration `d` from the *interrupted* iteration `d`'s completed, comparable partial scores where present, else from iteration `d-1` |

Per policy, over >=3 timed repeats (report median and spread): preparation nodes &
ms; main-search nodes saved; net nodes; net wall ms; completed depth under the
historical clock; committed-move change; change in oracle cp-loss of the committed
move; PVS re-search count; first-move cutoff rate; regressions where the control
already committed the stable deeper move; white/black split; tactical / quiet /
endgame / in-check split.

## 8. Side-effect isolation

`tests/root_order_probe.py` splices a dev copy of the control. A shadow / policy run
must save and restore: `table` (TT), `killers`, `history`, `seen`, `context`,
`duplicates`, `nodes`, `deadline`, `age`, `pending`, `evidence`, `model`,
`iteration_evidence`, `completed_evidence`, `completed_scores`, `root_move`,
`pattern`, `collect`, `stats`. **Equivalence mode**: policy = control must yield
identical committed move, completed depth, every root score, node count, timeout
behaviour and persistent heuristic state (`history`, `killers`, `seen`) vs the
untouched control, over >=64 enforced positions at fixed depths 2 and 3 with >=2
repeats each. Stop and repair if it fails.

## 9. Predeclared opportunity gate

A policy may be recommended for a future candidate only if **all** pass:

1. the audit proves it is not already equivalent to existing root ordering;
2. >= 4 of the 6 targets are reached at the actual completed iteration (i.e. the
   policy lets the engine complete the iteration whose commit matches the stable
   deeper move on >= 4 targets);
3. it improves >= 50 % of consequential shallow/deep disagreements;
4. zero new mate suppressions or result-class regressions;
5. it never worsens a target where the control already commits the stable deeper
   move;
6. median preparation overhead <= 8 % of the historical move budget;
7. net search cost <= 0, **or** completed depth increases on >= 4 of 6 targets;
8. determinism and colour symmetry intact;
9. the improvement survives >= 3 repeated timed runs, not one favourable sample.

If no policy passes every gate: **DIAGNOSIS ONLY**, `agent.py` byte-identical, one
next action.

# RESULTS

## 10. Method & equivalence

`tests/root_order_probe.py` loads a dev copy of the control and **re-implements the
`Engine.choose` root loop** (`root_search`) so a policy can inject the per-iteration
root-move order, while `Engine.search` / `order` / `enter` / `leave` / `drawn` /
`reconstruct` are the untouched control code.

`python tests/root_order_probe.py equivalence --reps 2` — **256 comparisons**
(64 positions incl. the 25 fixtures × depths 2,3 × 2 reps): `root_search("control")`
reproduces the untouched `Engine.choose` with **0 mismatches** on committed move,
completed depth, every root score and exact node count.

**Oracle** = full-window exact score of every legal root move at the deepest
fixed depth `D` completed within budget (`D<=8`, ~4 M-node cap), **required to
agree with `D-1`** ("stable"); mates trusted only when scored at both `D` and
`D-1`. 20 of 25 fixtures gave a stable oracle; 5 unstable (`r44-14`, `r44-21`,
`r53-52`, `round30-Bf4`, `r54-53`) are reported but excluded from the gate maths.

## 11. Phase 4 — the completed-iteration gap (`results/root_order/phase4_gap.json`)

On this workstation, with no probe overhead, the live control reaches deeper than
the platform did (`r45-54` d6 vs platform d5; `r53-35` d5 vs platform d4;
`r55-19`/`r56-17` d3 vs platform d2).

| metric (20 stable-oracle fixtures) | value |
|---|---:|
| live committed move == stable oracle best | **16 / 20 (80 %)** |
| disagreements | 4 |
| — harmless (`<15 cp`) | 2 (`r55-19` 9 cp, `r56-17` 7 cp) |
| — material (`>=15 cp`) | 1 (`r55-18-h6` 31 cp, context row) |
| — **consequential (`>=40 cp` / class change)** | **1 (`r55-20-Nxd4`, 126 cp)** |
| — mate disagreements | **0** |

**The six targets:**

| target | live depth / move | oracle depth / best | match | cp loss | oracle-best index / clamped |
|---|---|---|:--:|---:|---|
| r45-54-Rc7 | d6 `a5a4` | d7 `a5a4` | ✅ | 0 | 1/17, no |
| r46-30-Ka4 | d4 `b4a5` | d6 `b4a5` | ✅ | 0 | 2/42, no |
| r53-35-Rf7 | d5 `e7e8` | d6 `e7e8` | ✅ | 0 | 1/36, no |
| r55-19-g5 | d3 `f8f6` (Rf6) | d5 `f8f7` (Rf7) | ✖ harmless | **9** | 5/37, no |
| r56-17-f4 | d3 `c3c4` (c4) | d5 `g1h1` (Kh1) | ✖ harmless | **7** | 4/37, no |
| **r55-20-Nxd4** | **d2** `c6d4` | **d5** `f8g8` (Rg8) | ✖ consequential | **126** | 6/38, no |

Findings for Phase-4 items 7-11: in **every** disagreement the oracle-best move
was **not clamped** by the reduced root window and was searched at index 4-16 of
~37 — i.e. **not mis-ordered and not scored by a distorted window**. The live
iteration genuinely values it lower because of the horizon. `r55-19` and `r56-17`
already commit an *acceptable* move locally (Rf6, c4 — the fixtures'
`acceptable_uci`); only `r55-20` commits a losing move, and it does so because it
completes **depth 2** while the correction lives at **depth 5** — a
branching-factor gap (~3 completed plies), not an ordering gap.

## 12. Phase 5 — shadow root-ordering policies (`results/root_order/phase5_policies.json`, 3 timed reps, historical clocks)

`dd` = median completed-depth change vs control; `dn` = median node change; prep =
preparation time as % of the move budget.

| policy | mean Δ completed depth | depth regressions | committed-move changes | max / median prep % of budget | audit: redundant? |
|---|---:|---:|---|---:|---|
| A — previous PV first | **−0.05** | 1 (`r45-54`: d6→d5) | 1 — **`r45-54` `a5a4`→`c3c7`** (the fixture's `unacceptable_uci`, a result-class regression) | 0 / 0 | **yes** — already done by the l.997-1003 re-sort |
| B — previous scores desc | +0.00 | 0 | 1 (`round30` `d1h5`→`d4c6`, still acceptable) | 0 / 0 | **yes** — this *is* the l.997-1003 re-sort |
| C — TT root move first | +0.00 | 0 | 1 (`round30`→`d4c6`) | 0 / 0 | **n/a** — `choose` never writes a root TT entry, so C ≡ `order()` fallback |
| D — depth-`(d-2)` prep pass | **−0.05** | 1 (`r51-66`: d7→d6) | 0 | **30.8 / 13.5** | **yes** — iteration `d-1`'s completed scores already order iteration `d`; the pass repeats work and its overhead is 3-31 % of the clock, with net nodes *worse* on ~6 fixtures (`r53-35` −20 %, `r45-55` −21 %, `round30` −18 %) |
| E — partial interrupted-iteration scores | +0.00 | 0 | 3 — `r53-52` `d7c6`→`d7g4` (→ the fixture's `acceptable` move), `r54-53` `g8h7`→`d6e5` (→ acceptable), `round30`→`g5e3` (neutral) | 0 / 0 | **no** — genuinely absent (audit #4) |

**No policy increases completed depth on any fixture. No policy changes the
committed move on any of the six targets.** Policy A and Policy D each *regress* a
completed depth. Policy E is the only non-redundant mechanism and it does have
three benign/positive move flips — but all three are on **non-target** fixtures,
two of them **oracle-unstable**, and E's trigger (an interrupted iteration leaving
usable exact partial scores) is timing-dependent, so gate 9 (survives repeated
timed runs on other schedules) is not safely met.

## 13. Opportunity gate

| # | condition | A | B | C | D | E |
|---|---|:--:|:--:|:--:|:--:|:--:|
| 1 | not already equivalent to existing root ordering | ✖ | ✖ | ✖ | ✖ | ✅ |
| 2 | >= 4 of 6 targets reached at the completed iteration by the policy | ✖ | ✖ | ✖ | ✖ | ✖ |
| 3 | improves >= 50 % of consequential shallow/deep disagreements (1 exists: `r55-20`) | ✖ (0/1) | ✖ | ✖ | ✖ | ✖ (0/1) |
| 4 | zero new mate suppressions / result-class regressions | ✖ (`r45-54`) | ✅ | ✅ | ✅ | ✅ |
| 5 | never worsens a target the control already gets right | ✖ (`r45-54`) | ✅ | ✅ | ✅ | ✅ |
| 6 | median prep overhead <= 8 % of budget | ✅ | ✅ | ✅ | ✖ (13.5 %) | ✅ |
| 7 | net search cost <= 0, or +depth on >= 4 targets | ~ | ~ | ~ | ✖ | ~ |
| 8 | determinism / colour symmetry intact | ✅ | ✅ | ✅ | ✅ | ✖ (move is clock-cut-point dependent) |
| 9 | improvement survives >= 3 repeated timed runs | ✖ | ✖ | ✖ | ✖ | ✖ (trigger is schedule-sensitive) |

**No policy passes every gate.**

## 14. Verdict

**DIAGNOSIS ONLY.** `agent.py` unchanged, SHA-256
`65ec40ceb29a8f6fe14a74ab2ed6ca446164f701e46d2b9d3fe8d5af94655bda`,
1046 lines — **byte-identical, before == after**.

- **The control's root ordering is already near-optimal.** It re-sorts root moves
  by the previous completed iteration's exact scores every iteration (agent.py
  l.997-1003), which subsumes policies A, B and the practical effect of root
  internal iterative deepening (D). The audit predicted this and Phase 5 measured
  it: A/B/C/D produce ~0 net benefit and two of them regress a completed depth.
- **The reduced root window does not corrupt the commit.** The argmax at every
  completed iteration is reliable; Phase 4 confirms the oracle-best move is never
  clamped and never pathologically mis-ordered in the disagreement cases.
- **The completed-iteration gap is tiny and it is a horizon gap, not an ordering
  gap.** 1 consequential disagreement in the enforced corpus (`r55-20-Nxd4`),
  0 mate disagreements; `r55-20` needs ~3 more completed plies, which no reordering
  buys. On this workstation the control already commits an acceptable move on
  5 of the 6 targets.
- Policy E is the only non-redundant idea and it is too small and too
  schedule-sensitive to justify a candidate.

## 15. One recommended next action

**Stop the search-shape line and quantify how much of the six rated losses is
platform-hardware rather than engine.** Phase 4 shows this i7-12700 completes
1-2 plies deeper than the EPYC 9V74 did on the targets and already commits the
correcting move on `r45-54`, `r46-30`, `r53-35` and an acceptable move on `r55-19`,
`r56-17`; only `r55-20-Nxd4` still fails locally. Null-move, LMR and root-ordering
have each now been measured and rejected against the same horizon. The evidence-
based next step is a **controlled re-run of the full enforced rated fixture set on
this workstation at the exact historical clocks (>= 5 repeats), classifying each
loss as "reproduces locally" vs "platform-speed artifact"**, so the next
experiment (if any) targets only losses that are real on reachable hardware — and
if `r55-20` is the sole survivor, the honest conclusion is that the engine is at
its single-technique search ceiling and further gains require an evaluation change,
which is out of scope here.

## 16. Exact commands

```powershell
.\.venv\Scripts\ruff.exe check agent.py tests/root_order_probe.py
.\.venv\Scripts\mypy.exe --strict tests/root_order_probe.py
$env:PYTHONPATH = $PWD; .\.venv\Scripts\python.exe tests/verify.py
$env:PYTHONPATH = $PWD; .\.venv\Scripts\python.exe tests/determinism.py
.\.venv\Scripts\python.exe tests/root_order_probe.py equivalence --reps 2
.\.venv\Scripts\python.exe tests/root_order_probe.py phase4 --max-depth 6 --oracle-depth 7
.\.venv\Scripts\python.exe tests/root_order_probe.py phase5 --reps 3
```

## 17. Changed / added files

| Path | State |
|---|---|
| `agent.py` | **unchanged**, `65ec40ceb29a8f6fe14a74ab2ed6ca446164f701e46d2b9d3fe8d5af94655bda`, 1046 lines |
| `tests/root_order_probe.py` | **new** — dev-only probe, ruff + mypy --strict clean |
| `tests/ROOT_ORDER.md` | **new** — this file |
| `tests/results/root_order/phase4_gap.json` | **new** |
| `tests/results/root_order/phase5_policies.json` | **new** |
| `tests/results/root_order/phase5.log` | **new** |

No existing file, PGN, ZIP, frozen agent, manifest or historical result was
modified. Rounds 57-66 PGNs were **not** retrieved (domain filtered — section 0)
and no fixture was fabricated. Nothing was committed, pushed, packaged or
submitted.
