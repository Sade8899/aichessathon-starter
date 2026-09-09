# NNUE V2 — move-quality residual with a confidence gate

Predeclared 2026-09-09, before any V2 data was generated, on branch
`experiment/nnue-v2-ranked`. Written first so the acceptance rule cannot be
rewritten after the numbers arrive.

## 1. Why V1 was rejected, stated as a falsifiable claim

V1 optimised static evaluation error and got it: +27.85 cp test MAE, +48.67 cp on the
unseen Loki holdout, for 4.05% NPS. It still lost. RATED_V5 fell from 16/16 to 11/16,
the 800-game paired arena scored -2.88 pp with a 95% CI of [-5.75%, 0.00%], the 30 s
confirmation scored -4.17 pp, and draws collapsed from 15 to 3.

The claim V2 tests is that **the V1 objective was the defect, not the V1 capacity**.
A residual trained on unconditioned static MAE is free to move an evaluation by up to
the clamp in positions where the control's *ordering* was already correct. MAE does not
see orderings, so nothing in the V1 loss ever penalised destroying one. The draw
collapse is the visible signature: a correction with a non-zero mean pushes drawn
positions off equality and turns held draws into decisive losses.

V2 therefore optimises the quantity the engine actually consumes -- the order of sibling
children at a node -- and buys the right to move an evaluation with a confidence head
that must be earned.

## 2. Protected baselines (verified before any work)

| artifact | SHA-256 | status |
| --- | --- | --- |
| `agent.py` (control) | `65ec40ceb29a8f6fe14a74ab2ed6ca446164f701e46d2b9d3fe8d5af94655bda` | verified, never written |
| `submissions/pre_nn_20260909/agent.zip` | `d9392c6b9c572790c838cc91e957c6eeaceecb706175d99cdfbf928a5e3b86a5` | verified, never written |
| tag `pre-nnue-control-20260909` | `78c03b0` | intact |

Every V2 candidate lives in `agent_nnue_v2.py`. `agent.py` is read-only for the whole
session and is only ever packaged by an explicit human decision.

## 3. Data: sibling groups, not more positions

No bulk relabelling. The 128,950 Stockfish-19 labelled positions are reused as-is; the
existing per-game-lineage split assignment (`train` / `validation` / `test` / `holdout`)
is inherited unchanged, so V2 cannot leak across the V1 boundary.

What is new is **hard-negative sibling groups**. For a parent position P drawn from the
existing corpus, one Stockfish MultiPV search returns the top-k moves *and* their scores
in a single search. That is the whole efficiency argument: measured on real corpus
positions, MultiPV=6 yields 43.5 child labels/s/worker against 4.6 for single-PV, a
9.5x saving, because the k lines share one search tree.

For each parent:

- top-k Stockfish moves with scores, from the parent's side-to-move perspective;
- the control's own preferred move at fixed depth, forced into the group when Stockfish
  did not already rank it (this is the hard negative that matters -- the move the
  deployed engine would actually play);
- regret(m) = score(best) - score(m) >= 0 for every candidate;
- each child's Stockfish value, side-to-move relative, is -score(m) by definition of the
  parent search, so children are labelled without a second search;
- each child's `control_static_cp` from the control's own compiled evaluation.

Exclusions, enforced in code and asserted in the provenance report:

- the 16 RATED_V5 fixture FENs and every FEN reachable in one ply from them;
- the entire Loki family, which stays an unseen holdout opponent and unseen data;
- parents whose group has no ordering signal (all regrets equal) are dropped.

Deliberate over-sampling of the position types where V1 did damage: quiet defensive
moves, repetition-adjacent positions, near-equal liquidations, queen exchanges, passed
pawn defence and endgames.

## 4. Model

`correction = calibrated_confidence * bounded_residual`

Two heads become three: the phase-blended mg/eg residual heads of V1, plus a confidence
head `c` in [0,1] sharing the same accumulator. The extra head is one dot product over
HIDDEN units against a ~2,048-add sparse accumulator, so it is nearly free; the NPS gate
decides, not this argument. Widths 32 and 64 are both trained and the narrower is
preferred on ties, because NPS is a real constraint and V1 measured only ~1.3 cp between
them.

### Loss

1. **Value** -- Huber on the Stockfish-minus-control residual.
2. **Pairwise ranking** -- margin hinge on sibling pairs: the lower-regret child must
   receive the lower deployed evaluation, with the margin scaled by the regret gap.
3. **Control anchor** -- pairs the control *already* orders correctly carry a larger
   weight and a larger margin. Breaking one costs more than fixing a broken one gains.
   This is the term V1 did not have and the reason it broke five solved fixtures.
4. **Draw preservation** -- in near-equal, repetition, defensive and fortress-like
   positions, penalise any correction that increases |eval| away from equality.
5. **Confidence calibration** -- BCE of `c` against the detached indicator that applying
   the correction actually reduced error *and* did not damage sibling order.
6. **Symmetry** -- colour-perspective consistency. V1 measured 2,000/2,000 positions
   producing features identical to their colour swap, so this term is provably zero
   under the canonical orientation. It is therefore enforced as a **gate**, not a loss,
   and the measurement is re-run and reported rather than assumed.

### Variants

| tag | contents |
| --- | --- |
| A | anchored residual (value + anchor) |
| B | A + pairwise ranking |
| C | B + confidence gate |
| D | C with stronger anchor and draw penalties |
| E | C restricted to calibrated middlegame/endgame phase regions |

Early stopping on a **composite** validation score, not MAE:
regret reduction, move-order agreement, fraction of already-correct control choices
preserved, draw preservation, residual MAE, and float-vs-quantized agreement.
A checkpoint is never selected because its MAE is best.

## 5. Gates, in order. Any failure rejects before an arena is run.

1. feature-extractor invariants including the corrected de Bruijn scan;
2. reference vs optimised feature equality;
3. colour symmetry;
4. legal-move and mate tests;
5. deterministic inference;
6. float vs quantized agreement;
7. platform-container import and full-game smoke test;
8. package size and dependency constraints;
9. NPS loss <= 5%;
10. **RATED_V5 16/16 -- no solved-control regression, at all**;
11. r80 24.Rd4 still rejected;
12. round-77 repetition defence and round-78 passed-pawn defence still solved.

## 6. Arena

Colour-balanced paired screening, 200-240 games, against the pre-neural control and each
benchmark family (Shallow Blue 2.0, Rustic Alpha 3, Zagreus 5.0), with **Loki 3.0 as a
held-out opponent only**. Immediate rejection on a negative point estimate, a material
draw collapse, one catastrophic colour, or any rise in illegal/timeout/crash rates.

Only the best survivor goes to an 800-1,200-game paired confirmation with identical
openings and reversed colours, reported with a bootstrap 95% CI and an Elo estimate.

## 7. Acceptance

All seven must hold: RATED_V5 16/16; no solved draw or passed-pawn regression; quantized
inference works in the container; NPS and time management acceptable; the large paired
arena positive with a 95% CI excluding zero; the result not dependent on one opponent,
one colour or one time control; draw conversion not deteriorated.

**If no candidate passes, the pre-neural control is retained.** Better MAE, one repaired
fixture, a deeper average search, or a statistically flat arena are not success and will
not be reported as success.
