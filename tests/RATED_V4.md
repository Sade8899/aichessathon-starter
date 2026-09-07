# Rated rounds 44-53: diagnosis of the v4 submission

Ten rated games, all played by the submitted source
`e5f63625a30f23ef7f1d625fbb5830f2bdbbed1b5480142a6e31b83cf731325b`, shipped as
`submission 0609v4/agent.zip`
(`267400857f19b0c5ca9d5a4d6640f42201db1f42be9ba8cafae5e8bd63c6a74b`). Five wins, one
draw, four losses.

The working `agent.py` is `65ec40ceb29a8f6fe14a74ab2ed6ca446164f701e46d2b9d3fe8d5af94655bda`,
which is that source plus the accepted quiescence delta pruning. **It played none of these
games**, and nothing here is evidence about it except where it is explicitly measured as a
comparison. `agent.py` was not modified at any point; its digest is unchanged.

**Verdict: diagnosis and regression only. No engine experiment was justified, and no
candidate was created.** A shared root cause is established and is shared by six critical
positions, but every technique eligible under the experiment rules was measured and none
of them addresses it. The measurements that close each option are in
[Why no experiment follows](#why-no-experiment-follows).

## Submission evidence

| Item | Value |
|---|---|
| Archive | `submission 0609v4/agent.zip`, one member, `agent.py` |
| Archive SHA-256 | `267400857f19b0c5ca9d5a4d6640f42201db1f42be9ba8cafae5e8bd63c6a74b` |
| Agent SHA-256 inside it | `e5f63625a30f23ef7f1d625fbb5830f2bdbbed1b5480142a6e31b83cf731325b` |
| Frozen copy | `tests/submitted/e5f63625.../agent.py`, byte-identical |
| Working `agent.py` | `65ec40ce...`, unchanged throughout |
| Rejected check extension | `896ac0b2...`, not revived |

The archive was extracted into a temporary directory outside the repository and was never
modified. The ten PGNs are preserved byte-for-byte at `submission 0609v4/games/` and their
digests are recorded in `submission 0609v4/manifest.json`.

`*.pgn` is ignored repository-wide by `.gitignore:6`. One narrow exception was added,
`!submission*/games/*.pgn`, so the evidence directory is visible while every other PGN —
including `tests/results/rated/platform/` and `tests/tournament/fixtures/round30.pgn` —
stays ignored exactly as before. `.gitattributes` marks the same paths `-text !eol`,
because the files arrive LF and `core.autocrlf=true` would otherwise rewrite them on
checkout and invalidate every digest above.

The results CSV in that directory is the platform's **cumulative** export and holds rounds
29 through 53. Only 44 through 53 belong to this submission.

## PGN validation

`rated_v4.py validate`, all ten files, every check passing
(`results/rated_v4/validation.json`).

| Rd | Colour | Opponent | Result | Termination | Plies | Moves | Slowest | Clock left |
|---:|---|---|---|---|---:|---:|---:|---:|
| 44 | White | Blitz | Loss | checkmate | 111 | 55 | 3.541 | 39.75 |
| 45 | Black | corundum.ai | Loss | checkmate | 130 | 65 | 3.601 | 35.23 |
| 46 | White | The W in Woxbridge | Loss | checkmate | 57 | 28 | 3.602 | 67.25 |
| 47 | Black | The Italian | Win | checkmate | 119 | 60 | 3.602 | 37.44 |
| 48 | White | Queens of Queens' | Win | checkmate | 44 | 22 | 3.602 | 75.15 |
| 49 | Black | Knightmare | Win | checkmate | 115 | 58 | 3.602 | 38.89 |
| 50 | Black | Zugzwang Rickwar | Win | checkmate | 67 | 34 | 3.602 | 57.90 |
| 51 | White | Left Right | Draw | threefold repetition | 146 | 73 | 3.602 | 30.32 |
| 52 | White | Team2 | Win | checkmate | 100 | 50 | 3.602 | 44.02 |
| 53 | Black | e=mc^2 | Loss | checkmate | 100 | 50 | 3.601 | 43.94 |

Checked per game: the ten required headers; the starting FEN legal and flagged `SetUp`,
and never the standard start; every move legal from that FEN; result, termination, colour
and move count agreeing with the CSV row; the final position actually satisfying the
claimed termination, with the mated side matching the result; clocks present, never
negative, and never above the 3.75 s ceiling the budget expression allows; the round header
matching the file name and lying inside 44-53. **Totals: 5 wins, 1 draw, 4 losses**, which
is the CSV's own account of those ten rows.

The slowest move of each game is the hard deadline `0.96 x clock/32` at whichever early
move first had a deeper iteration started and aborted: 3.602 s at a 120.0 s clock in nine
games, and 3.541 s at a 118.0 s clock in round 44. Ordinary moves stop earlier, at the soft
break of `0.65 x clock/32`, once an iteration completes. No move exceeded its budget, and no
game came near the 600-ply limit.

## The replay: what the engine actually saw

`rated_v4.py replay` feeds one persistent engine the same positions and the same clocks the
platform did, inside the 1-CPU, 2 GB, read-only container.

- **484 of 495 moves reproduced exactly (97.8%).** That is the identity check: the frozen
  `e5f63625...` source is the engine that played these games.
- **Mean completed depth 3.83.** Distribution over 495 moves:
  depth 1: 10, 2: 30, 3: 143, 4: 191, 5: 99, 6: 17, 7: 4, 8: 1.
- Round 46, the sharpest loss, has the **lowest mean completed depth of the ten, 3.07**.

The eleven divergences are all timing jitter at a depth boundary: this container is not the
platform's core, so a move that sat on the edge of completing one more iteration sometimes
completes it here. Two of them matter and are reported below, because in both the extra ply
produced the correcting move.

## First critical error in each loss, and the draw

"Correcting depth" is the shallowest fixed depth at which the control itself rejects the
move it played. "Completed" is what the replay actually finished at that move.

| Round | First critical move | Completed | Correcting depth | Margin | Classification |
|---|---|---:|---:|---:|---|
| 44 | 14.b4 (advisory) | 3 | 5 | 35 cp | 5 - static evaluation |
| 45 | 54...Rc7 | 5 | 7 | 785 cp | 8 - exchange or promotion race |
| 46 | 30.Ka4 | 3 | 4 (985 cp at 6) | 95 cp | 1 - search horizon |
| 51 | 53.Qc5 | 2 | 4 | 161 cp | 1 - search horizon |
| 53 | 35...Rf7 | 4 | 5 | 87 cp | 1 - search horizon |

### Round 44 — the error is before the ending, and it is an evaluation, not a tactic

The question was whether the decisive error is before the `Nxb4 / cxb4 / Bxa1` sequence or
later in the rook-and-pawn ending. **It is before it, and the ending was entered already
lost.**

14.b4 allows 16...Nxb4 17.cxb4, and `cxb4` opens the a1-h8 diagonal onto the undefended
`Ra1`, so 17...Bxa1 wins the exchange. What the measurements add is that the engine never
regarded any of it as a mistake:

- 15.Nxc4, 16.dxe4 and 17.cxb4 all show **loss 0 at depth 5 and depth 6**. By its own
  deepest search the engine had nothing better once move 15 was reached.
- 14.b4 is the earliest move with a better alternative, and even at **depth 7** it is only
  45 cp behind `Nb3` (146 s for that one search). At depth 5 it is 35 cp behind `Bxd5+`.
- 13.Re1 is best at depth 7, loss 0.
- The largest single depth-visible slip in the whole game is **21.Bf4, 67 cp at depth 5**.
- Material after 17...Bxa1 is -195 for White, and the engine scores that position **-47 at
  depth 5 and -90 at depth 6**. It is over-rating its compensation for the exchange by
  roughly a pawn.
- The ending is not thrown away. By move 40 the engine is at -380, and from move 55 every
  legal move scores -799: the position is dead lost before the rook-and-pawn phase begins.

So the earliest avoidable material loss is 14.b4, and it is avoidable only in the weak sense
of 35-45 cp at depths the engine cannot reach. The honest classification is **static
evaluation**, and it is the one finding here with no reproducible tactical refutation behind
it.

### Round 45 — the rook exchange is what let the g pawn queen

**54...Rc7 is the critical move.** The measured ladder:

| depth | best | best score | 54...Rc7 | loss |
|---:|---|---:|---:|---:|
| 4 | Rc7 | -165 | -165 | 0 |
| 5 | Rc7 | -135 | -135 | 0 |
| 6 | a4 | -133 | -161 | 28 |
| **7** | **a4** | **-133** | **-918** | **785** |

The engine completed **depth 5**, where `Rc7` is still its own first choice. `54...a4`
queens as well — the depth-7 line is `a4 g6 a3 Re7 a2 g7 a1=Q` — and holds at -133. Trading
the last rook removes the only piece that could stop the g pawn, and after `55.Rxc7` White
queens with tempo.

`53...a5` is **not** an error: loss 0 at depths 5, 6, 7 and 8.

`55...a4`, the move that declined a free rook, is a **proven forced mate against** at depth
7 (-29992) while `Kxc7` is -899. It is a genuine blunder but it is classified **12 - already
lost position**: both moves lose, and at depths 5 and 6 the two score identically at -918,
so the engine's stable sort simply kept the move that was ordered first among equals.

**No drawing move is demonstrable.** Between moves 44 and 53 no move loses more than 22 cp
at depth 8 and the score sits between -117 and -171 throughout. The last position the engine
rated as roughly balanced is move 37 (+36 at depths 3 and 4). Round 45 is a slow decline
with one sharp exchange error at the end of it, not a thrown-away draw.

### Round 46 — the highest-priority loss, and a proven mate

The preliminary checkpoint reproduced exactly:

- Moves 20 through 28 match the control's depth-3 choices.
- FEN before move 29 confirmed: `r5k1/5rq1/1pp1p2p/3bPp2/PP2pP2/K1Q3P1/4P1B1/3R3R w - - 1 29`.
  At depth 3 the control prefers `Rh3` by **41 cp**, as reported.
- FEN before move 30 confirmed: `r5k1/5rq1/2p1p2p/p2bPp2/1P2pP2/K1Q3P1/4P1B1/3R3R w - - 0 30`.
  At depth 3 `Ka4` is the control's own choice; at depth 4 `bxa5` is preferred and `Ka4`
  scores **95 cp** worse, as reported.

Answers to the five questions:

1. **The first move that makes the mating net unavoidable is 30.Ka4.** 29.a5 is not an
   error: loss 0 at depths 4, 5 and 6, and 22 cp at depth 7. After 30.Ka4 the control finds
   a **forced mate for Black at depth 7** (+29991, line `axb4+ Kxb4 Rb7+ Kc5 Rb5+ Kd4`), and
   at move 31, after 30...axb4+, **every** legal White move scores -29992 at depth 6. The
   alternative 30.bxa5 holds the game to -177 at depth 6 and -199 at depth 7. This is the
   one wholly proven result in the diagnosis: a mate score out of this alpha-beta is sound,
   because the defender generates every legal move and the only shortcut on its side is a
   quiescence stand-pat, which can suppress a mate claim but never manufacture one.
2. **It is not a one-ply horizon failure.** Depth 4 and depth 5 both see only 95 and 92 cp;
   depth 6 sees 985. The engine completed **depth 3**. Avoiding the move needs one more ply;
   understanding why needs three.
3. **King safety did not encourage the king walk.** The static evaluation already prefers
   `bxa5` (+41) to `Ka4` (-64), and `Ka4` has the most enemy-attacked squares around the
   king of any move in the position (4, against 2 for `bxa5`). The depth-3 search overrode
   its own evaluation. A king-danger term cannot fix a case where the evaluation was already
   right.
4. **Forcing checks were not searched too late at the root.** `recognise` classifies this
   position as `tactical`, so checks receive the +300 root-ordering bonus and quiescence
   admits quiet checks. The bonus applies only at ply 0 and quiet checks only at ply < 3, so
   the refutation's third and fourth checks (`Rb5+`, `Qa7+`, at plies 5 and 7) are outside
   quiescence's reach whatever the ordering — but ordering is not what hides them, depth is.
5. **Minimum depth 4 to avoid it, depth 6 to understand it.** In time: the engine had
   68.7 s on the clock, so a 2.15 s budget. `rated_v4.py clocks` shows it needs **2x that
   clock — 137 s, a 4.29 s budget — to complete depth 4 and play `bxa5`.** A 120 s game never
   offers that under `available / 32`. Cold full-window depth 6 at this position costs
   295,714 nodes and 16.5 s, roughly eight times the entire per-move budget.

### Round 51 — the draw saved half a point; it is not defective repetition handling

**The repetition was correct.** At the final repetition,
`7r/PK1P1k2/5p2/2n3p1/6P1/8/8/8 w - - 11 80`, White is a rook and a knight down for two
connected passers on a7 and d7, -720 by material. The control searched it to **depth 10**
and never found progress for either side: -670 at depth 4 through -731 at depth 10, with the
principal variation shuffling `Kc6 Ne4 Kb7 Nd6+ Kc6`. Repeating scores 0 against roughly
-700 for anything else, so taking the draw is the engine's best available result and it took
it deliberately.

**There was a winning continuation, but it was gone long before the repetition.** White
peaked around +630 at move 39. The measured collapse:

| Move | Best | Best score | Played | Loss | Depth |
|---|---|---:|---:|---:|---:|
| 53.Qc5 | a6 | +529 | +368 | **161** | 4 |
| 56.Qa6 | Qd1 | +425 | +296 | **129** | 6 |
| 58.Rc8 | Qb6 | +246 | +87 | **159** | 6 |
| 62.Ke3 | Kd3 | +238 | **-230** | **468** | 6 |
| 66.Qc4 | Kb6 | -220 | -615 | **395** | 4 |

**62.Ke3 is the last move at which White was still winning.** It scores +261 at depths 2
through 5 and -230 at depth 6, and the container replay — which completed depth 6 here —
played `Kd3` instead. By move 63 every move is -230. 66.Qc4 blocks a check by offering the
queen trade into a lost pawn ending and loses 395 cp at depth 4. The platform must have
completed depth 3 or less there, because depth 4 rejects the move; the container replay
completed depth 5 and played `Kb6`.

So: not a conversion failure at the repetition, and not defective repetition handling. It is
the same horizon deficit, applied to a won position.

### Round 53 — connected passers and a king march, but the cause is horizon

The eval trajectory: +301 at move 22, +44 at move 24, -58 at 27, -134 at 30, -182 at 34,
-307 at 36, and roughly -330 for the next fifteen moves. Through the entire liquidation
20...axb4 to 30...Kg7 the control shows **loss 0 at depths 5 and 6** — it never had a better
move by its own reckoning, and the drop from +301 to +44 is the leaf estimate at move 22
being wrong about a position six plies away.

The nominated moments:

- **35.d6 / 35...Rf7** — this is the first depth-visible error of the phase. `Re8` is
  preferred by **87 cp at depths 5 and 6**; the engine completed depth 4, where `Rf7` is its
  own choice. At depth 2 `cxd6` looks best by 152 cp, but that is shallow noise: depths 3
  and 4 both prefer `Rf7`.
- **36.dxc7 / 36...Ra8** — loss 0 at depths 5 and 6. Once `d6` was allowed to reach c7 there
  was nothing better.
- **the c8 blockade (42...Rc8)** — 36 cp at depth 4. Not decisive.
- **the king walk g6 to d1 (43...Kf5 onward)** — forced by checks: `Bd3+`, `Ba3+`, `Bb2+`,
  `Rf6+`, `Bc1+`, `Rf2+`. `46...h4` loses 122 cp at depths 2 and 3 and was played anyway.
- **52...Bc6** — a **proven forced mate against at depth 6** (-29994) where `g4` is -748.
  The engine completed depth 5. `rated_v4.py clocks` shows it needs **8x the clock** —
  357 s, an 11.2 s budget — to reach depth 6 here.
- **53...Rxc7** — also mate at depth 6; by then there was nothing to save.

**Root cause: tactical horizon, not passed-pawn evaluation.** The direct test is in
`results/rated_v4/probe_passed_pawn.log`: an aggressively strengthened advanced-passer term
leaves the choice at 35...Rf7 unchanged at every depth from 2 to 5, and at 52...Bc6 it
changes only at depth 3, to a move that is not the correcting one. King safety is likewise
not implicated — see the probe below.

## The shared cause

Every critical error in the four losses and the draw is a position where **the control picks
the rated move at the depth it actually completed, and rejects it one to three plies deeper.**

Completed depth is the replay's, except where the replay diverged, in which case the
platform's is bounded by the shallowest depth that rejects the rated move.

| Position | Completed | Correcting | Gap | Margin at correcting depth |
|---|---:|---:|---:|---:|
| r51-53-Qc5 | 2 | 4 | 2 | 161 cp |
| r46-30-Ka4 | 3 | 4 | 1 | 95 cp (985 at depth 6) |
| r51-66-Qc4 | <=3 (inferred) | 4 | 1 | 395 cp |
| r53-35-Rf7 | 4 | 5 | 1 | 87 cp |
| r53-52-Bc6 | 5 | 6 | 1 | mate |
| r45-54-Rc7 | 5 | 7 | 2 | 785 cp |
| r51-62-Ke3 | 5 (6 in replay) | 6 | 1 | 468 cp |

Six independent critical positions, one cause. The sub-motif in rounds 46, 51 and 53 is the
same shape: **the engine's own king moves into a forcing check sequence** whose payoff lands
two to four plies beyond the leaf. That is consistent with what TACTICS.md already measured
from the 251-mate corpus — "the failure is a pure horizon failure, and it is one ply wide" —
and this round of games says the same thing from real play.

Two supporting measurements:

- **Quiescence is 76-98% of all nodes** at every critical position, matching ORDER.md's
  89.5% over its own suite. The tree is dominated by leaf work.
- **Realisation drift**: restricted to the 186 move pairs where the game actually followed
  the control's own depth-4 principal variation, the depth-4 score fell by a median of 4 to
  12 cp per own move in the four losses and rose by 1 to 9 cp in the five wins
  (`results/rated_v4/probe_drift.log`). The leaf estimate leaks in the direction of the
  result. This is a symptom of the same horizon, and it is confounded with the result, so it
  is reported as description rather than as an independent finding.

## Delta pruning changed nothing here

The working engine `65ec40ce...` was measured on all nineteen critical positions at every
depth, alongside the control:

- **Identical best move at every depth in every position.** `delta_pruning_changes_choice`
  is false throughout (`results/rated_v4/critical_positions.json`).
- It passes all 15 enforced fixtures with the same choices as the control.
- It is faster — for example 13.2 s against 16.5 s for depth 6 at r46-30 — which is what it
  was accepted for.

**No rated result in rounds 44-53 is attributable to delta pruning, in either direction.**

## Why no experiment follows

The rule is that production code changes only when at least two independent critical
positions share one reproducible cause. They do. But every technique eligible under the
experiment rules was then measured against that cause, and none of them clears the bar.

**Passed-pawn danger term.** This is the one option with any measured traction, and it is
reported with its ambiguity intact. Measured over every position at depths 2-5, with the
pure-Python evaluation on both sides so only the one edited expression differs
(`rated_v4.py passed-pawn`):

- **r45-54-Rc7 — corrected.** The term flips the choice to `a4` at depth 4 *and* depth 5,
  and the replay completed depth 5 in the game. This one is unambiguous.
- **r51-53-Qc5 — ambiguous, and it decides whether the count reaches two.** The term flips
  to the correcting move `a6` at depth 3 but not at depth 2. The in-game replay, with the
  engine's real persistent state, completed **depth 2** there; a cold engine given the same
  44.9 s clock completes **depth 3**. Which of those the platform actually did is not
  resolvable from the record, so this position may or may not have been corrected.
- **Not corrected anywhere else.** r53-52 flips only at depth 3 and to a move that is not
  the correcting one; r46-30, r51-62, r51-66, r53-35 and both round 44 positions do not move
  at any depth. No negative control breaks, and r49-53 improves at depth 2.

So the term corrects one critical position for certain and a second only under the more
generous of two depth readings — and it leaves round 46, the sharpest loss and the one
proven mate, entirely untouched. It also does not address the established cause: it changes
which move a fixed depth prefers in two passed-pawn endings, not the fact that the engine
completes 3.83 ply against corrections that live at 4 to 7. Acting on it would mean editing
both `evaluate` and its Numba mirror `numeric_evaluate`, keeping the 2,400-comparison
equality check, and then clearing the quiet corpus, the 251-mate corpus and a 240-game
paired estimate — on a term whose entire rated support is one endgame, which is close to
what rejection criterion 5 describes. The measurement is recorded so the decision can be
revisited; it is not acted on here.

**Colour-symmetric king safety term.** The separability probe (`rated_v4.py king-safety`)
takes every legal move in each king-move position and ranks it by static evaluation next to
three cheap danger proxies. It does not separate. At r46-30 the static evaluation already
preferred the safe move by 105 cp and the search overrode it. At r51-66 the danger proxy
actively **favours** the losing move: `Qc4` leaves one attacked square around the king,
`Kb6` leaves three. A term built on this signal would have made round 51 worse.

**Exact forcing move ordering, check and evasion ordering, SEE used only for ordering.**
Ordering cannot change a fixed-depth result — and the evidence here is precisely that the
correct move appears only at a deeper fixed depth. Ordering can only help by buying depth,
and the headroom is small: ORDER.md measured main-search first-move cutoffs at 85.6%. The
node factors required are not small. At r46-30 the control needs 7,605 nodes for depth 3,
22,786 for depth 4 (3.0x) and 295,714 for depth 6 (38.9x). Nothing in this class delivers a
3x tree reduction on top of an 85.6% cutoff rate.

**Tactical delta-pruning exemption, including protecting capturing checks.** No rated
evidence supports it. The refutations in rounds 46, 51 and 53 are *quiet* checks, which
quiescence never generates at ply >= 3 and which delta pruning never touches — it only skips
captures. And the working engine, which is the only source with delta pruning, chooses
identically to the control at every depth in every critical position.

**Check extensions and recapture extensions.** Excluded by the experiment rules, and already
measured and rejected here: TACTICS.md records `verify.py` failing deterministically
(`('promotion', 991, 981)`) because a check extension breaks the fixed-depth minimax
equality the harness asserts, plus a 0.114-ply loss of completed depth against a +0.003-ply
noise floor.

**Wider quiescence check coverage.** Already measured and rejected: QUIET_CHECKS.md records
completed depth collapsing from 2.438 to 1.667 mean ply and -27.5 points over 240 games.

That exhausts the eligible list. The cause is real, it is measured, and the remedies that
would touch it are either excluded by the rules or already refuted by this repository's own
measurements. The one option with a measured effect, the passed-pawn term, does not touch
that cause and carries one clear rated position behind it. Running a third experiment against the same cause with a technique already
known to fail would burn an upload slot to reproduce a known result.

## The regression set

`tests/rated_v4_positions.json`, 19 positions, run by `rated_v4.py fixtures`. Fifteen are
enforced; four are recorded context and never gate a candidate.

A fixture asserts that at its `minimum_correcting_depth` the engine chooses a move in
`acceptable_uci`, or — where several moves keep the result and the list is deliberately
empty — anything other than `unacceptable_uci`. Each fixture also carries its full depth
ladder, the clock the move was played on, the depth the replay completed, the motif, the
classification and the proof type.

| id | Rd | Kind | Depth | Must not play | Acceptable | Margin | Proof |
|---|---:|---|---:|---|---|---:|---|
| r45-54-Rc7 | 45 | critical | 7 | c3c7 | a5a4 | 785 | search |
| r45-55-a4 | 45 | critical | 7 | a5a4 | b8c7 | mate | **mate** |
| r46-30-Ka4 | 46 | critical | 4 | a3a4 | b4a5 | 95 | search (mate at 7) |
| r51-53-Qc5 | 51 | critical | 4 | a3c5 | a5a6 | 161 | search |
| r51-62-Ke3 | 51 | critical | 6 | d2e3 | d2d3 | 468 | search |
| r51-66-Qc4 | 51 | critical | 4 | a6c4 | c5b6 | 395 | search |
| r53-35-Rf7 | 53 | critical | 5 | e7f7 | e7e8 | 87 | search |
| r53-52-Bc6 | 53 | critical | 6 | d7c6 | g5g4 | mate | **mate** |
| round30-Bf4 | 30 | critical | 4 | g5f4 | any other | 315 | search |
| r47-63-e2 | 47 | control (win) | 4 | — | e3e2 | mate | **mate** |
| r48-26-Qxf6 | 48 | control (win) | 2 | — | e5f6 | +1961 | search |
| r49-53-h3 | 49 | control (win) | 3 | — | h4h3 | +572 | search |
| r50-35-d3 | 50 | control (win) | 2 | — | d4d3 | +954 | search |
| r52-40-c7 | 52 | control (win) | 2 | — | c6c7 | +409 | search |
| r52-55-Re4 | 52 | control (win) | 2 | — | e6e4 | mate | **mate** |
| r51-79-repetition | 51 | context | 6 | — | b7c6 | 0 | search (to depth 10) |
| r44-14-b4 | 44 | context | 5 | — | c4d5 | 35 | search |
| r44-21-Bf4 | 44 | context | 5 | — | c1b2 | 67 | search |
| r46-29-a5 | 46 | context | 5 | — | a4a5 | 0 | search |

`r46-29-a5` is in the set specifically so a future engine cannot be credited with fixing a
move that was never wrong. `r51-79-repetition` fixes the objective state at the threefold
rather than asserting a move.

Both the control and the working engine pass all 15 enforced fixtures, with identical
choices (`results/rated_v4/fixtures_rated.json`, `fixtures_working.json`).

## Limitations

- **No independent oracle.** No trusted reference engine is installed, the platform image
  ships none, and downloading one is out of scope. Every centipawn here is the control
  judging itself, and a self-oracle agrees with itself — TACTICS.md measured exactly that.
  The only proofs are the mate scores, which are sound because the defender generates every
  legal move and the one shortcut on its side, a quiescence stand-pat, can suppress a mate
  claim but never invent one. Every other number is labelled "search" above.
- **Completed depth is hardware-dependent.** The replay container is not the platform's
  EPYC 9V74 core. It reproduced 97.8% of the moves, and the 11 divergences are all one-ply
  boundary cases, so the depth figures are the right order but not the platform's exact
  ones.
- **Round 44 has no reproducible tactical error.** Its classification rests on the gap
  between material and the engine's own score, which is the weakest evidence in this
  document.
- **Realisation drift is confounded with the result.** A losing game has a falling score by
  construction; the measurement is reported as description, not as an independent cause.
- The `rated_v4.py clocks` ladder walks powers of two, so a "needs 2x" reading means the
  correcting depth arrives somewhere in (1x, 2x], not exactly at 2x.

## Reproducing

```
python tests/rated_v4.py validate     --out tests/results/rated_v4/validation.json
python tests/rated_v4.py scan   --rounds 46 --depths 2,3,4
python tests/rated_v4.py replay --source rated
python tests/rated_v4.py critical --source rated,working
python tests/rated_v4.py clocks   --source rated
python tests/rated_v4.py mate --fen "r5k1/5rq1/2p1p2p/p2bPp2/KP2pP2/2Q3P1/4P1B1/3R3R b - - 1 30" --depths 4,5,6,7
python tests/rated_v4.py king-safety
python tests/rated_v4.py passed-pawn --depths 2,3,4,5
python tests/rated_v4.py fixtures --source rated
python tests/rated_v4.py fixtures --source working
```

Timing-sensitive modes (`replay`, `clocks`) belong in the container:

```powershell
.\docker-test.ps1 python tests/rated_v4.py replay --source rated
```

Raw records are in `tests/results/rated_v4/`. `make gate` passes with these additions
(ruff, mypy over 9 source files, +2 =0 -0 against the random baseline), and `agent.py` is
byte-identical to `65ec40ce...`.

## Next

**Obtain the platform PGNs for the earlier rounds that still have no move record, and treat
completed depth as the next thing to measure rather than the next thing to change.** Six
critical positions in this batch share one cause, the engine completes a mean of 3.83 ply,
and every technique the rules allow has now been measured against that cause and found not
to touch it. The productive next step is a search-efficiency measurement that establishes
where the remaining node budget actually goes below the quiescence boundary — not another
one-technique experiment aimed at the same ply.
