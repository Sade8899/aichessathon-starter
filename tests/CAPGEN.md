# Direct capture generation in quiescence

Predeclared before implementation. Nothing below was edited after the candidate ran.

The control is the promoted working agent, SHA-256
`65ec40ceb29a8f6fe14a74ab2ed6ca446164f701e46d2b9d3fe8d5af94655bda`. The working
`agent.py` is not modified by this experiment; the candidate lives only in
`tests/capgen_candidate/<sha>/agent.py`.

## 1. Measured bottleneck

From `tests/results/qprofile/` on the promoted control, rated corpus at fixed depth 4
(31 positions, 1,024,885 nodes, 57.17 s clean wall):

| Quantity | Value |
|---|---:|
| Quiescence share of nodes | 93.6% (median 92.7%) |
| `captures_and_promotions` calls | 568,925 |
| `captures_and_promotions` cost per call | 17.2-17.8 us |
| Implied share of total wall time | ~17.7% |
| `generate_pseudo_legal_moves` tottime share (cProfile) | 12.4% |
| `generate_legal_moves` tottime share | 6.0% |
| `scan_reversed` tottime share | 6.2% |
| `attackers_mask` tottime share | 4.5% |

Move generation and the python-chess machinery beneath it is the largest cost in the
engine, and `captures_and_promotions` is the single largest identified consumer of it.

Two competing candidates were measured and rejected as too small:

| Rejected candidate | Measured cost | Share of wall |
|---|---:|---:|
| Duplicate `position_key` per node (`enter` then `drawn`) | 1.787 us x 1.06M | ~3.3% |
| Stalemate probe `any(generate_legal_moves())` | 4.17 us x 914,465 | ~6.7% |

The stalemate probe found **zero** stalemates in 914,465 rated-corpus calls and 707,268
quiet-corpus calls, but it cannot be removed exactly: control returns 0 from a stalemate
node on every path, including the stand-pat cutoff path, so the probe has to precede the
cutoff. Cheaper exact spellings were measured and gained little (king-moves-first: 6.03
us to 5.64 us, 6.5%).

## 2. Selected technique

Replace the body of `captures_and_promotions` with a direct generator that produces the
same list without going through `board.generate_legal_moves`.

## 3. Exact expected mechanism

`captures_and_promotions` is called from exactly one place, the non-check branch of
`Engine.quiesce`, guarded by `if not check:` where `check = board.is_check()`. Two facts
follow that python-chess cannot assume and therefore re-derives on every call:

1. **The side to move is not in check.** `generate_legal_moves` still calls
   `attackers_mask(not turn, king)` to find checkers, then takes the not-in-check branch.
2. **Castling can never appear.** `generate_pseudo_legal_moves` calls
   `generate_castling_moves` whenever `from_mask & self.kings`, and that routine calls
   `clean_castling_rights()` (measured at 1.215 us) before discovering that its candidate
   set is empty. Castling lands on our own rook squares, which are never enemy-occupied
   and never an empty promotion square, so both masked passes provably yield nothing.

The candidate iterates our pieces directly with `scan_reversed`, in python-chess's own
descending-square order, and filters with `board._is_safe(king, blockers, move)` -- the
same predicate `generate_legal_moves` applies -- with `king` and `blockers` computed once
instead of once per masked pass.

Measured on 6,000 real non-check quiescence positions sampled from a depth-3 search over
the round 55 rated positions: **17.4 us to 10.5 us, a 1.66x speedup, 39.9% faster**, with
**0 list-equality mismatches out of 6,000**.

An inlined-safety variant was also measured and was not faster (10.22 us against 10.48
us, inside noise), so the simpler version that reuses `_is_safe` was chosen; it is the
one a judge can check against python-chess line by line.

## 4. Exact source scope

One function, `captures_and_promotions`, in the candidate copy only. No other definition
is added, removed or changed. `Engine.quiesce` is untouched, including the stand-pat
test, the delta-pruning block and its margin, the quiet-check branch and the ordering
call. Evaluation, piece values, time allocation, main-search depth semantics, opening
handling, repetition and draw rules, opponent modelling, transposition-table size and the
public API are all untouched.

## 5. Correctness invariants

1. For every reachable non-check position, the returned list equals the control's list
   exactly, element for element and in the same order, including duplicates being absent.
2. Order equality matters because `Engine.order` sorts with a stable sort, so ties keep
   generation order and any reordering would change the searched move order.
3. Every returned move is legal.
4. Underpromotions appear in `Q, R, B, N` order, matching python-chess.
5. En passant is produced by python-chess's own `generate_pseudo_legal_ep`, filtered by
   `_is_safe`, so pinned en passant stays excluded.
6. Fixed-depth searches return identical moves, identical scores and identical node
   counts to the control.

## 6. Expected node and time effect

Node counts must be **identical**; this is an exactness-preserving change, so any node
difference is a defect, not a gain. Expected wall-time effect is a reduction of roughly
7% of total fixed-depth time (17.7% share x 39.9% saved).

## 7. Minimum accepted improvement

The standing bar is quiescence wall time down at least 20%, or total fixed-depth wall
time down at least 15%.

**This is predeclared as likely to fail.** The profile does not contain a single
conservative exact change of that size; the largest available is the ~7% above. The
experiment is run anyway because the measurement is the deliverable and because the
alternative -- weakening the bar to fit the result -- would be dishonest.

## 8. Immediate rejection criteria

1. Any fixed-depth move, score or node count differs from the control.
2. Any list-equality mismatch on any corpus, including special-move fixtures.
3. Any illegal move, crash, flag or malformed output.
4. Any previously solved critical rated tactic becomes unsolved.
5. Total fixed-depth wall time falls by less than 15% and quiescence wall time falls by
   less than 20%.

---

# Results

Everything above was written before the candidate was built. Nothing above was edited
afterwards. Candidate SHA-256
`12907f5441c507d2b27a83eaf8b8146aed8811321c157e073bf2ae118bfb7566`, frozen at
`tests/capgen_candidate/<sha>/agent.py`. The working `agent.py` is still
`65ec40ceb29a8f6fe14a74ab2ed6ca446164f701e46d2b9d3fe8d5af94655bda`.

## Correctness

| Gate | Result | Evidence |
|---|---|---|
| AST identity, only `captures_and_promotions` differs | passed | `results/capgen/identity.json` |
| Ruff over the candidate | passed | |
| `mypy --strict` over the candidate | passed | |
| List equality, 40,000 reachable non-check positions | 0 mismatches | `results/capgen/equality.json` |
| Special moves, 414 cases (14 hand-authored + 400 reached) | 0 mismatches, 0 illegal | `results/capgen/special.json` |
| Fixed depth 4, moves / scores / node counts, 61 positions | 0 mismatches | `results/capgen/fixed_d4.json` |
| Three cold imports, worst 1.465 s against a 90 s budget | passed | `results/capgen/imports.json` |
| `determinism.py` | passed | repeatability, colour symmetry, opening repeats |
| `verify.py`, 500 reachable positions | targeted passed | `results/capgen/verify_candidate.json` |
| Rated fixtures through round 56, enforced | 19/19 on control **and** 19/19 on candidate | `results/capgen/fixtures_*.json` |
| Control and candidate fixture choices | identical move and score on every fixture | |

The change is exact, as designed. Node counts are identical position by position, so no
part of any timing result can come from searching less.

## Speed, the predeclared criterion

Paired, counterbalanced, three repetitions, minimum of three per side, rated corpus at
fixed depth 4, 31 positions. `results/capgen/timing_rated_d4.json`.

| Metric | Value |
|---|---:|
| Total control wall time | 54.070 s |
| Total candidate wall time | 50.834 s |
| **Total wall-time reduction** | **5.99%** |
| Median paired gain | 5.81% |
| Mean paired gain | 4.42% |
| Positions regressed | 5 of 31 |
| Positions regressed among those over 1.0 s | 1 of 14 |
| Median paired gain among those over 1.0 s | 6.38% |

Every regression except one sits below 0.4 s of control time, where timer noise is the
larger effect; the worst, `r55 22...Bxa4` at -20.6%, is a 0.37 s position. On the
fourteen positions above one second the picture is consistent: median +6.38%, one
regression.

The isolated operation did speed up as predicted, 17.4 us to 10.5 us, a 1.66x. It simply
is not a large enough slice of the whole: `captures_and_promotions` is about 17.7% of
wall time, and 39.9% of 17.7% is about 7%, which is what the end-to-end measurement found.

## Verdict: REJECTED

Rejection criterion 3 fires. Total fixed-depth wall time fell 5.99%, against a
predeclared bar of 15%; quiescence wall time did not fall 20% either. No correctness or
safety gate failed, no fixture regressed, and no rated correction was newly reached,
because an exact change cannot reach one except by completing another ply, and a 6%
saving does not.

This was predeclared as the likely outcome in section 7. The profile is the deliverable:
it establishes that the missing ply is not one quiescence micro-optimisation away.

Later gates were not run, by the protocol's own instruction to reject immediately: the
240-game paired screen and the candidate-versus-baseline comparison would have measured a
change already disqualified on its predeclared criterion.

The candidate, its patch, its profile and these results are preserved. The working
`agent.py` is untouched and still hashes to `65ec40ce...`.

## Gates that were run after the speed criterion failed

The speed criterion decides the verdict, but the remaining cheap gates were run anyway so
the record is complete and so a future candidate can be compared against a full row.

| Gate | Control | Candidate |
|---|---|---|
| Ruff over the repository | passed | passed |
| `mypy` over the configured files (agent + harness) | passed, 9 files | n/a |
| `mypy --strict` over each touched development module | passed | passed |
| `make gate` games, 2 vs the random baseline at 5,000 ms | +2 =0 -0, no failed termination | +2 =0 -0, no failed termination |
| 20-game smoke, candidate against control, 10,000 ms + 100 ms | - | **+11 =1 -8, 57.5%** |

The smoke score is nonnegative, which satisfies acceptance condition 11. It is not
evidence of strength: 20 games carry an interval of roughly plus or minus 22 points, and
the change cannot alter any fixed-depth decision, so the only channel by which it can win
a game is occasionally finishing one more iterative-deepening ply inside the same clock.
A 6% saving buys that rarely. The 240-game paired screen was not run, because rejection
criterion 3 had already fired.

## What the profile says about the real gap

The clock ladder on the round 55 position after `19.Qc1` (`ladder_r55_g5.json`) is the
number that matters most. The engine completed depth 2 in the 2.708 s it had and played
`g5`. It still plays `g5` at twice the clock. At four times the clock, depth 3 completes
and it plays `Rf6` instead.

So the correction costs about a **4x** speedup, and the largest exact quiescence
optimisation the profile contains is worth about **1.06x**. That gap is structural, not
something a faster move generator closes.
