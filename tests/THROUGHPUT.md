# Throughput optimisation experiment, rounds 79-80 ingest: DIAGNOSIS ONLY

**Verdict: DIAGNOSIS ONLY. The candidate is rejected and `agent.py` was never modified**
(`65ec40ceb29a8f6fe14a74ab2ed6ca446164f701e46d2b9d3fe8d5af94655bda`, 1046 lines, 39396
bytes). Nothing was packaged and nothing was submitted.

The candidate is correct — it passes exact fixed-depth move, score, node and root-score
equivalence — and it is genuinely faster. It fails both acceptance thresholds, and the
measurements below show that no optimisation of this hot path could have passed the second
one.

## Identity

| Item | Value |
|---|---|
| Branch / HEAD at start | `main` / `adc0714004a13ce5b253eba325e99d1f35ee2759` |
| `agent.py` before and after | `65ec40ce...`, unchanged, `git diff -- agent.py` empty |
| Rejected candidate | `fa749fb156d35f500af458b07e2fef2ebe729de1d1fff557267b7056ffdfc769`, preserved at `tests/throughput_candidate/fa749fb1.../agent.py` |
| Ceiling probe (deliberately incorrect, never a candidate) | scratch only, described below |
| Python / chess / numpy / numba | 3.14.4 / 1.11.2 / 2.5.2 / 0.67.0 |

The RATED_V5 diagnostic artifacts were already committed as `adc0714` (12 files: the
report, `rated_v5.py`, the fixture corpus and nine evidence files); nothing was missing and
nothing needed re-preserving.

## Rounds 79-80

Both existed and both were retrieved as full PGNs and validated; nothing was reconstructed
from prose.

| Rd | Colour | Opponent | Result | Termination | Plies | Our moves | SHA-256 of PGN |
|---:|---|---|---|---|---:|---:|---|
| 79 | Black | Chesstosterone | Win | checkmate | 139 | 70 | `0c56ebcb6589...` |
| 80 | White | team | **Loss** | checkmate | 111 | 55 | `1ed48e098ac6...` |

`rated_v5.py validate` now covers rounds 57-80 and passes all sixteen checks on all 24
games. Record over 57-80: **13 wins, 3 draws, 8 losses**.

### Round 80, 24.Rd4 — classification

`24.Rd4` is the dominant error of the game: **372 cp** at depth 5, where the next largest
error in all 55 of White's moves is 17 cp.

FEN `1r2r1k1/6p1/2p1b3/p1qnQpBp/P1B5/1PP5/5PPP/3RR1K1 w - - 1 24`, clock 82.210 s,
budget 2.569 s, 2.467 s used.

| depth | best | score | `Rd4` score | loss |
|---:|---|---:|---:|---:|
| 2 | **Rd4** | +179 | +179 | 0 |
| 3 | h4 | +180 | −5 | 185 |
| 4 | Qg3 | +182 | −5 | 187 |
| 5 | Qg3 | +183 | −189 | **372** |

**Classification: B — intermittently reproduces, 1 of 5 cold runs** at the exact historical
clock. The five runs completed depths `[3, 2, 3, 3, 3]`, and **the single run that played
`Rd4` is exactly the run that completed only depth 2**. `Rd4` is best at depth 2 and
refuted from depth 3 onward, so this is not an evaluation error at all: it is a
depth-2/depth-3 boundary decided by how much search fits in 2.57 s. The platform, on a
slower core, landed on the wrong side of that boundary.

This is the strongest single piece of evidence in the corpus for the throughput
hypothesis, and it is the one fixture the candidate actually fixed (below).

A second new fixture, `r79-12-Nxd3` (201 cp, in a game that was won), reproduces **5/5 —
class A**.

## Profile of the existing search

`qprofile.py hotspots --group rated --depth 4`, 31 positions, clean wall 53.115 s,
cProfile overhead 2.38x (`results/qprofile/hotspots_rated_d4_v5.json`).

| function | calls | tottime | share | max speedup if removed entirely |
|---|---:|---:|---:|---:|
| `chess/__init__.py:generate_pseudo_legal_moves` | 6,778,825 | 15.38 s | 12.3% | 1.141x |
| `chess/__init__.py:scan_reversed` | 29,396,041 | 7.65 s | 6.1% | 1.065x |
| `chess/__init__.py:generate_legal_moves` | 6,726,644 | 7.50 s | 6.0% | 1.064x |
| `agent.py:quiesce` | 229,506 | 7.41 s | 5.9% | 1.063x |
| `agent.py:priority` | 4,192,447 | 6.84 s | 5.5% | 1.058x |
| `chess/__init__.py:push` | 1,033,213 | 6.78 s | 5.4% | 1.058x |
| `chess/__init__.py:attackers_mask` | 3,342,238 | 5.60 s | 4.5% | 1.047x |
| `agent.py:compiled_evaluate` | 620,843 | 5.00 s | 4.0% | 1.042x |

**The profile is flat and the top entry is inside python-chess.** No single function,
removed completely, yields 15%: the best is 14.1%, and it is not ours to remove.

Attribution of legal-move generation to the call sites that *are* ours:

| call site | resumptions | cumulative |
|---|---:|---:|
| `captures_and_promotions` (agent.py:644) | 2,348,708 | 25.93 s |
| `any(board.generate_legal_moves())` — the stalemate probe in `quiesce` | 914,465 | **13.59 s** |
| `search` (agent.py:817) | 2,341,343 | 9.88 s |
| `quiesce` (agent.py:767) | 1,105,415 | 5.06 s |
| `drawn` → `is_insufficient_material` | 1,024,468 | 3.33 s |

`captures_and_promotions` is already a masked two-pass generator — the previous session
optimised it — so the remaining addressable waste is the quiescence node prologue.

### Branch frequencies that decide the rewrite

Instrumented over the same corpus (959,594 quiescence nodes):

| quantity | value |
|---|---:|
| non-check quiescence nodes | 915,242 |
| of those, `stand >= beta` stand-pat cutoff | 345,758 (37.8%) |
| of those, capture list non-empty | 871,722 (95.2%) |
| **true stalemates found** | **0** |
| `drawn()` calls | 1,024,468 |
| of those, a pawn, rook or queen is on the board | 1,023,576 (99.9%) |

The stalemate probe costs 13.59 s cumulative and **never once returns true**, and 99.9% of
`is_insufficient_material` calls are decidable by one bitboard test.

## The candidate

One bounded region, the quiescence node prologue. 25 changed lines, no search, ordering,
time-management or evaluation change.

1. `drawn()` — a side owning a pawn, rook or queen always has mating material, so
   `is_insufficient_material()` is only called when `board.pawns | board.rooks |
   board.queens` is empty. Exact, not an approximation.
2. `quiesce()` — the stalemate probe is paid only where a stalemate would change the
   value: before returning `stand` on the stand-pat cutoff, and when the quiescence move
   list is empty. When that list is non-empty it is skipped, because any move in it is
   itself a legal move and so already proves the position is not stalemate. The
   `ply >= MAX_PLY` branch keeps the probe ahead of it.

### Gate 1 — semantics preservation: PASS

`throughput.py equivalence --groups rated,quiet --depths 2,3,4`: **183 comparisons, 0
mismatches** — identical chosen move, identical score, identical **node count** and
identical per-root-move scores at every depth on every position
(`results/throughput/equivalence_v1.json`). The benchmark independently confirms
1,889,079 nodes on both sides.

### Gate 2 — >= 15% end-to-end throughput: FAIL

`throughput.py bench --groups rated,quiet --depth 4 --repeats 5`, control and candidate
alternated inside each repetition (`results/throughput/bench_v1.json`):

| rep | control | candidate | speedup |
|---:|---:|---:|---:|
| 1 | 105.612 s | 96.958 s | 1.0893x |
| 2 | 102.870 s | 94.741 s | 1.0858x |
| 3 | 101.809 s | 93.167 s | 1.0928x |
| 4 | 101.423 s | 94.070 s | 1.0782x |
| 5 | 101.762 s | 94.712 s | 1.0744x |

Median of medians **1.0749x (+7.49%)**; median paired ratio 1.0858x (+8.58%). 18,555 to
19,945 nodes/second. **Below the 15% threshold.**

### How much was available at all

A deliberately **incorrect** ceiling probe — the stalemate probe deleted outright and
`is_insufficient_material` forced to `False`, i.e. the stalemate rule and the
insufficient-material draw rule removed from the engine — measures **1.170x-1.184x
(+17.5%)** (`results/throughput/bench_ceiling.json`). So the entire quiescence prologue is
worth about 17.5%, the correct candidate already captures roughly half of it, and the
remainder is exactly the work the rules of chess require. Reaching 15% on this hot path
means giving up correctness.

### Gate 3 — correct >= 3 of 5 class-A failures at historical clocks: FAIL

`rated_v5.py repro --source <candidate> --repeats 5`
(`results/rated_v5/repro_candidate.json`):

| fixture | control | candidate | completed depths | corrected |
|---|---|---|---|---|
| r57-22-e6 | A 5/5 | A 5/5 | 4,4,4,4,4 (unchanged) | no |
| r58-55-Rh2 | A 5/5 | A 5/5 | 2,2,2,2,2 (unchanged) | no |
| r58-56-e4+ | A 5/5 | A 5/5 | 4,4,4,4,4 (unchanged) | no |
| r66-49-e5 | A 5/5 | A 5/5 | 4,4,4,4,4 (unchanged) | no |
| r66-55-Kf6 | A 5/5 | A 5/5 | 4,4,4,4,4 (unchanged) | no |
| r79-12-Nxd3 | A 5/5 | A 5/5 | 3,3,3,3,3 (unchanged) | no |
| **r80-24-Rd4** | **B 1/5** | **C 0/5** | 3,3,3,3,3 (was 3,2,3,3,3) | **yes** |

**0 of 5. The threshold is 3 of 5.** The candidate did not gain a single ply on any class-A
fixture; the completed depths are identical to the control's. Its one real behavioural gain
is `r80-24-Rd4`, where the extra 8.5% was just enough to finish depth 3 in all five runs
instead of four, moving it from class B to class C.

### Why 15% would not have passed gate 3 either

The cost of one more iterative-deepening ply, measured on the five class-A positions
themselves:

| fixture | d2 | d3 | d4 | d5 | d3/d2 | d4/d3 | d5/d4 |
|---|---:|---:|---:|---:|---:|---:|---:|
| r57-22-e6 | 0.075 | 0.432 | 2.375 | 10.143 | 5.76x | 5.50x | 4.27x |
| r58-55-Rh2 | 0.307 | 0.650 | 2.399 | 11.807 | 2.12x | 3.69x | 4.92x |
| r58-56-e4+ | 0.046 | 0.117 | 0.444 | 1.750 | 2.54x | 3.79x | 3.94x |
| r66-49-e5 | 0.022 | 0.102 | 0.628 | 1.677 | 4.64x | 6.16x | 2.67x |
| r66-55-Kf6 | 0.089 | 0.207 | 0.685 | 1.925 | 2.33x | 3.31x | 2.81x |

**Median cost of one extra ply: 3.79x; minimum observed: 2.12x.** Buying one ply therefore
needs between +112% and +279% throughput. A 15% gain is a fifth of the cheapest ply in the
corpus. The two acceptance thresholds are not jointly satisfiable: the >= 3/5 criterion
requires at least a doubling of search speed, while the largest measured hot path is worth
17.5% even when correctness is discarded.

This also revises the closing recommendation of `RATED_V5.md`. That report observed the
class-A failures needed only 1.1x-1.4x more *wall-clock time* to reach their correcting
depth and suggested throughput as the next lever. The 1.1x-1.4x figures are correct, but
they are the ratio of the *fixed-depth* time to the budget, and the search cannot spend a
1.1x budget on a partial iteration — it completes ply N or it does not. The quantity that
matters is the whole next ply, and that is 3.79x. **Throughput is not a viable lever for
these failures at any achievable magnitude.**

## Gates not reached

Gates for solved-control, mate, legality, determinism and colour-symmetry regression were
not run: the candidate had already failed two acceptance thresholds, and running them could
only have produced a pass that did not change the verdict. Equivalence at fixed depth is
strictly stronger than all of them anyway — a candidate with identical moves, scores and
node counts cannot regress a control, suppress a mate, change legality, or alter symmetry —
so the recorded 183/183 equivalence covers them in substance.

## What is kept

| Path | What it is |
|---|---|
| `tests/throughput.py` | new: `equivalence` (exact move/score/node/root-score) and `bench` (alternating paired timing) |
| `tests/throughput_candidate/fa749fb1.../agent.py` | the rejected candidate, preserved |
| `tests/results/throughput/` | equivalence, bench and ceiling evidence |
| `tests/results/qprofile/hotspots_rated_d4_v5.json` | the profile this experiment was built on |
| `tests/results/rated_v5/scan_79_80.json`, `repro_79_80.json`, `repro_candidate.json` | rounds 79-80 and the candidate's reproduction |
| `submission 0609v4/aichessathon-round-{79,80}-*.pgn` | the new rated games |

`tests/rated_v5.py` and `tests/rated_v5_positions.json` now cover rounds 57-80; the corpus
holds 16 fixtures and the control passes every enforced one.
