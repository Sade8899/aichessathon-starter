# Rated loss diagnosis

No engine was changed. No arena was run. Nothing was downloaded, packaged or
submitted. All measurement ran serially in the existing `chessathon-scope:test`
image with one CPU, 2 GB, no network and a read-only workspace. Raw logs are in
`results/rated/`.

## Source identity

The engine that plays rated games is the submitted archive, and it is the primary
subject here. It is **not** the repository's working `agent.py`.

| Role | Identity | Verified |
|---|---|---|
| Submitted archive | `4cf5c8885c49360f6a60328cdd062aa5a45e697e8ee5122d243639c675640dfd` | yes |
| Archive file holding it | **`agent_05_09.zip`** | yes |
| `agent.py` inside that archive | `4551f4e4f2fc09fa56801e52a9ad38f0485ebb32d3c84aa4adbb4166fbb598c0` | yes, byte identical to the frozen copy |
| Working `agent.py` (development candidate) | `be5da8696f01924e2f752b1686e1d6a94b38dc72fff86a1f7fedb05f006c56e0` | not submitted |
| Root `agent.zip` | `020340a3afcab4c41b2f0a1e2ab526a1bdd5e88b79338283d8af87a8b9136490` | contains `59f99079...`, an unsubmitted local build |

**Correction worth acting on:** `tests/tournament/submitted.json` records
`"source_archive": "agent.zip"`, but the archive whose hash matches the submitted
identity is `agent_05_09.zip`. The root `agent.zip` was later rebuilt and now holds
the Numba engine. The recorded hashes are right; only the filename is stale. It was
not changed here, and no archive was rebuilt.

No rated result is attributed to the candidate anywhere in this report. The
separately supplied coursework agent uses a different environment and is excluded.

## Evidence inventory

**Exactly one rated loss is available.** `tests/tournament/fixtures/rated.json` holds
the round-30 fragment; `second_rated_start` is explicitly `null`; the only PGN in the
repository is `round30.pgn`, the same fragment. There are no platform logs and no
other rated games. Every conclusion below rests on n = 1, and the asset feasibility
sections say plainly where that makes a decision impossible rather than merely
uncertain.

| Field | Value |
|---|---|
| Rated games available | 1 |
| Start FEN | `rnbqk1nr/pp3ppp/8/2bp4/8/1N6/PPP2PPP/R1BQKBNR b KQkq - 1 6` (28 pieces, move 6) |
| Pre-blunder FEN | `r1bqk2r/pp2n1pp/1bn2p2/1B1p2B1/3N4/8/PPP2PPP/R2QK1NR w KQkq - 0 10` (28 pieces, move 10) |
| Own moves recorded | 4 |

## The loss

Played **10. Bf4 (`g5f4`)** with **113,600 ms** remaining. Refutation
`Bxd4 Qxd4 Qa5+ Qc3 Qxb5`: the knight and bishop trade off evenly, then `Qa5+` forks
and wins the bishop on b5. Measured material swing for White **-320 cp**, from level
to a piece down. The critical move `Qa5+` is a *quiet* check, so it is not a
capture the quiescence search would examine on its own.

### State reconstruction

The three preceding rated calls were replayed into one fresh instance at the rated
clocks, so the transposition table, history, killers, repetition counters and
opponent model match the rated position. The submitted engine reproduced all three
rated moves — `b3d4`, `f1b5`, `c1g5` — and `reconstruct` correctly observed the
opponent replies `b8c6` and `g8e7`, confirming state continuity. At the blunder the
instance held 2,000 transposition entries, 25 history entries and age 3.

### Submitted engine `4551f4e...`, the engine that actually played

Fixed depths use a stopped development clock; thinking limits use the real clock with
the per-move budget overridden in a loaded development copy. `agent.py` is untouched.

| Condition | Completed depth | Move | Bf4 score | Nodes | Seconds | Blunder played |
|---|---:|---|---:|---:|---:|---|
| depth 1 | 1 | `g5f4` | +39 | 756 | 0.07 | yes |
| depth 2 | 2 | `d1h5` | +3 | 2,938 | 0.26 | no |
| depth 3 | 3 | **`g5f4`** | **+31** | 14,485 | 1.26 | **yes** |
| depth 4 | 4 | `d1h5` | **-338** | 36,911 | 3.13 | no |
| think 3.0 s (**the rated condition**) | 3 | **`g5f4`** | +31 | 33,632 | 2.88 | **yes** |
| think 3.2 s | 3 | `g5f4` | +31 | 34,112 | 3.07 | yes |
| think 3.55 s | 4 | `d1h5` | -338 | 36,911 | 3.15 | no |
| think 4.5 s | 4 | `d1h5` | -338 | 36,902 | 3.05 | no |
| think 6.0 s | 4 | `d1h5` | -338 | 74,000 | 5.76 | no |
| cold, think 3.0 s | 3 | `g5f4` | +31 | 36,224 | 2.88 | yes |

**The rated loss reproduces exactly**, both with the replayed state and cold.

**Earliest depth that rejects the blunder: 4.** Depth 3 actively prefers it at
+31 cp; depth 4 scores it -338 cp. Depth 2 happens not to select it, but depth 3
does, so 4 is the first depth from which the rejection holds.

### Candidate `be5da869...`, comparison only, never submitted

| Condition | Completed depth | Move | Bf4 score | Seconds | Blunder played |
|---|---:|---|---:|---:|---|
| think 3.0 s | 4 | `d1h5` | -338 | 2.11 | no |
| think 4.5 s | 4 | `d1h5` | -338 | 4.32 | no |
| think 6.0 s | 4 | `d1h5` | -338 | 5.76 | no |
| depth 3 | 3 | `g5f4` | +31 | 0.74 | yes |
| depth 4 | 4 | `d1h5` | -338 | 1.90 | no |

The candidate is search-equivalent at fixed depth — it still prefers Bf4 at depth 3
— but it is fast enough to finish depth 4 inside the same 3.0 s budget, in 2.11 s
against the submitted engine's 3.13 s. The Numba and QCAP throughput work fixes this
particular loss incidentally, by reaching the correcting depth in time.

## Classification

**Cause 2: the three-second allocation stopped before the correcting depth.**

The other five causes are ruled out by measurement, not by argument:

1. *Move ordering delayed the line* — no. Depth 4 costs 36,911 nodes and 3.13 s
   whether reached by iteration or directly; the correcting depth was simply not
   affordable, not mis-ordered.
3. *Quiescence or tactical horizon* — no. The unchanged quiescence sees the whole
   refutation once the search reaches depth 4, scoring it -338 cp.
4. *Evaluation failure at deeper search* — no. Deeper search rejects the move
   decisively and increasingly.
5. *Persistent state or transposition effect* — no. The cold run and the fully
   replayed run agree at every budget: both blunder at 3.0 s and both avoid it at
   3.55 s and above.
6. *Genuine strategic weakness* — no. There is a concrete five-ply tactical
   refutation worth a piece.

### Root cause, precisely

```python
budget = min(3.0, available / 32, max(0.001, available - 0.025))
```

With 113,600 ms remaining, `available / 32` is **3.55 s**, but the constant `3.0`
binds, and the hard deadline is `0.96 x 3.0 = 2.88 s`. Depth 4 needed 3.13 s. The
engine held 113.6 s of clock and spent 2.6% of it on the move that lost the game.

The measured threshold is tight and sits directly on that constant: **3.2 s still
blunders, 3.55 s does not.** Removing the cap alone — leaving `available / 32` to
govern, exactly as it already does later in a game — would have avoided this loss
with the submitted engine, unchanged in every other respect.

## Opening book feasibility

| Question | Measurement |
|---|---|
| Rated starting positions available | 1 |
| Is the rated start the standard initial position? | **No** |
| Start structure | White pawns a2 b2 c2 f2 g2 h2 (d- and e-pawns captured); Black pawns a7 b7 d5 f7 g7 h7 (c- and e-pawns captured); all 16 pieces present at move 6 |
| First objectively unsafe own move | **move 10**, `g5f4`, -315 cp against the depth-4 best |
| Earlier own moves | `b3d4` -5 cp, `f1b5` 0 cp, `c1g5` 0 cp — all sound |
| Positions a legitimate book could have covered | **0 of 1** |
| Positions requiring tactical search | **1 of 1** |

Four captures by move 6 with every piece still on the board is not a mainline
opening; it is a constructed position, consistent with the rules, which state that
rated games start from curated positions and that **the set is not published**. A
book keyed to the standard initial position would not have contained this start, and
a book for the curated set cannot be built in advance because the set is secret.

The engine also does not leave theory early in any damaging way: its first three
rated moves lose 5, 0 and 0 centipawns at depth 4. The failure is a tactic at move
10, four plies past a curated start, which is exactly where a book would already have
run out even if one existed.

Cost, if it were ever justified: a Polyglot `.bin` probe is a Zobrist hash and a
binary search, microseconds per move, and would save the full ~3 s allocation on each
covered ply. One file, a few megabytes, comfortably inside the 50 MB unzipped limit.
Provenance would have to be human opening theory; a table of this or any engine's
moves or evaluations is explicitly out of scope under the project rules.

**Not justified.** Measured coverage is zero, and the curated start set is
unpublished, so coverage cannot be improved by effort.

## Endgame tablebase feasibility

| Question | Measurement |
|---|---|
| Endgames in the rated evidence | **none** |
| Rated piece count, start and pre-blunder | 28 and 28 |
| Rated fragment final piece count | 25 |
| Rated positions eligible for 3, 4 or 5-piece Syzygy | **0** |
| Was WDL enough, or was DTZ needed? | **Not determinable** — no rated endgame exists |

Because the rated evidence contains no endgame at all, the material signature "when
every endgame begins and when its result becomes compromised" cannot be recorded for
rated play. The only endgame evidence available is **local sparring against the
frozen control, which is not rated play** and is labelled as such throughout: 300
games, 40 distinct material classes of five or fewer pieces, 76 occurrences.

| Pieces | Classes observed locally |
|---:|---|
| 2 | KvK |
| 3 | KQvK (5), KRvK (3), KPvK (3) |
| 4 | KRvKR (5), KPvKQ (3), KPvKP (2), KBPvK (2), KQBvK (2), KNPvK, KQNvK, KQQvK, KQRvK, KRNvK |
| 5 | KRPvKR (6), KQRvKN (3), KRPvKN (3), KQRPvK (3), KQRBvK (3), and 21 others |

The smallest useful set from that evidence is the 3-piece classes plus `KRvKR` and
`KPvKP`, but the frequent ones — `KQvK`, `KRvK` — are already trivially won by the
existing search, and the genuinely hard ones are five-piece.

| Set | Approximate uncompressed size | Fits 50 MB limit |
|---|---:|---|
| 3-piece | ~0.1 MB | yes |
| 3+4-piece | ~7.4 MB | yes |
| 3+4+5-piece | ~945 MB | **no, by a factor of ~19** |

Those are the published Syzygy figures used for planning; nothing was downloaded, so
they are not measured here.

`python-chess` support was verified in the sandbox with no network:
`chess.syzygy.open_tablebase()` opens a directory read only, an empty directory
loads zero tables, and `probe_wdl` and `probe_dtz` raise `MissingTableError`, which
subclasses `KeyError` and is therefore catchable — so an agent can fall back to
normal search safely on any position the shipped set does not cover.

**Not justified.** Zero rated positions were eligible. The five-piece set that would
matter cannot fit the submission limit, and the 3-4 piece set that fits covers
positions the engine already converts.

### Separating tablebase-eligible failures from the rest

The one rated failure occurs at 28 pieces and is a time-allocation failure, not a
tablebase-eligible one. Separately, and **not from rated play**, the local full-clock
confirmation drew 15 of 40 games by threefold repetition and 3 by the fifty-move
rule. That is a conversion and repetition signal worth its own investigation, but it
is a mirror-match artifact against a near-identical opponent and mostly occurs above
five pieces, so it is not tablebase-eligible either.

## Asset eligibility matrix

One row per available rated loss.

| Loss | Opening book eligible | Tablebase eligible | Tactical search failure | Evaluation failure | Time allocation failure | Repetition or conversion failure |
|---|---|---|---|---|---|---|
| round 30, move 10 `Bf4` | **No** — curated unpublished start, 28 pieces, error 4 plies past book range | **No** — 28 pieces, no endgame in the game | No — depth 4 finds the refutation with the existing quiescence | No — deeper search rejects it, -338 cp | **Yes, primary** — 3.0 s cap stopped at depth 3; 3.55 s reaches depth 4 | No |

## Recommended next experiment

**Exactly one: relax the constant per-move time cap.**

Replace the literal `3.0` in
`budget = min(3.0, available / 32, max(0.001, available - 0.025))` with a larger cap,
or remove that term so `available / 32` governs, and screen it against the existing
gates.

It is the dominant measured cause, and it is the only cause present. The evidence is
direct: at 113.6 s remaining the engine would take 3.55 s instead of 3.00 s, and 3.55 s
is measured to complete depth 4 and reject the blunder while 3.2 s does not. The
`available / 32` term already tapers the budget as the clock drains, so the change
does not risk the endgame; the cap is doing nothing except truncating the opening and
middlegame, where 3.55 s of a 113.6 s reserve is not extravagant.

Two risks the experiment must measure rather than assume: flag safety across a full
600-ply game at 120 s + 0.5 s, since the per-move budget rises everywhere, not only
here; and whether the extra time is actually converted into completed depth rather
than abandoned partial iterations, given the soft deadline at 65% of budget.

This is recommended over an opening book and over a tablebase because both of those
were measured against the actual rated evidence and neither is justified: zero book
coverage of a curated unpublished start, and zero tablebase-eligible rated positions.

## Reusable regression fixture

`rated_regression.json` pins the loss, both engine identities, the depth ladder, the
think ladder and the rule. The rule is deliberately about *reaching* the correcting
depth within the budget the engine will really have, not about the move alone, so it
keeps its meaning if the position's evaluation shifts.

> With the rated persistent state and the per-move budget the engine actually
> receives at 113,600 ms remaining, complete depth 4 or deeper and do not select
> `g5f4`.

```powershell
.\docker-test.ps1 -LogName rated-regress-submitted python tests/rated_losses.py --config submitted --regress
.\docker-test.ps1 -LogName rated-regress-candidate python tests/rated_losses.py --config candidate --regress
.\docker-test.ps1 -LogName rated-submitted python tests/rated_losses.py --config submitted --depths "1,2,3,4" --thinks "3.0,4.5,6.0" --cold
.\docker-test.ps1 -LogName asset-feasibility python tests/asset_feasibility.py
```

It discriminates today, which is what makes it a usable gate:

| Engine | Move | Completed depth | Result |
|---|---|---:|---|
| submitted `4551f4e...` | `g5f4` | 3 | **fails** |
| candidate `be5da869...` | `d1h5` | 4 | passes |

## Defects already improved, and defects still present

**Improved by the unsubmitted candidate.** This loss. The candidate reaches depth 4
in 2.11 s against the submitted engine's 3.13 s and rejects `Bf4` inside the rated
budget. The improvement is a side effect of throughput work, not of any change to
search rules or evaluation.

**Still present in both engines.** The 3.0 s cap itself is byte-identical in the
submitted engine, the Numba control and the current candidate. The candidate clears
this position by roughly 0.9 s of margin, which one harder position will erase, and
the structural fact is unchanged in every engine: with 113.6 s on the clock the
agent thinks for 3.0 s. Depth 5 was not reachable even at 6.0 s in either engine.
The recommended experiment therefore applies to the candidate as much as to the
submitted engine.
