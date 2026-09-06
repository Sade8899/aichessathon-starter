# Time allocation: removing the constant 3.0 s ceiling

One isolated experiment, run against the working development candidate
`be5da869...`. It is the single change [RATED_LOSSES.md](RATED_LOSSES.md)
recommended, and nothing else was attempted alongside it. No opening book, no
tablebase, no search, evaluation, ordering, quiescence or opponent-model change.

## The change

`tests/time_experiment.json` pins both sources. The candidate is
`e5f63625a30f23ef7f1d625fbb5830f2bdbbed1b5480142a6e31b83cf731325b`, stored at
`tests/time_candidate/e5f63625.../agent.py`.

```python
# control   agent.py:933
budget = min(3.0, available / 32, max(0.001, available - 0.025))
# candidate
budget = min(available / 32, max(0.001, available - 0.025))
```

`available / 32` stays. The final clock reserve `max(0.001, available - 0.025)`
stays. The 0.96 hard-deadline and 0.65 soft-stop fractions of the budget are
untouched. `tests/time_checks.py identity` proves the scope mechanically: the two
files are the same length, **exactly one line differs**, that line is one statement
inside `Engine.choose`, and every other module-level constant, function, class and
`Engine` method is identical at the AST level. Flags and the four Numba signatures
match.

## What the cap was costing

The budget expression is measured, not re-read, by freezing the clock and reading
back `Engine.deadline`:

| clock | control budget | candidate budget | extra |
|---|---|---|---|
| 10.0 s | 0.313 s | 0.313 s | 0 |
| 48.0 s | 1.500 s | 1.500 s | 0 |
| 96.0 s | 3.000 s | 3.000 s | 0 |
| 100.0 s | 3.000 s | 3.125 s | +0.125 s |
| 113.6 s | 3.000 s | **3.550 s** | +0.550 s |
| 120.0 s | 3.000 s | **3.750 s** | +0.750 s |

The cap binds only above **96 s**, so the two engines are provably identical for
all but the opening moves of a game. 113.6 s is the round 30 clock, and 3.55 s is
the value `rated_regression.json` records as the one that stops the blunder.

## Results

| Check | Control `be5da869` | Candidate `e5f63625` |
|---|---|---|
| Cold import, 3 fresh runner processes | 2.60 s median | 2.57 s median |
| `ruff check .` | pass | pass |
| `mypy --strict` (12 modules) | pass | pass |
| `make gate` | pass | pass |
| `tests/verify.py` | pass | pass |
| `tests/determinism.py` | pass | pass |
| Rated round 30 regression | `d1h5` depth 4 | `d1h5` depth 4 |
| 600-ply clock walk | 18.50 s left | 18.40 s left |
| 24-game arena flags/crashes/illegal | 0 / 0 / 0 | 0 / 0 / 0 |

### Fixed-depth identity

At a fixed depth the two are the same program. Over the 24-position suite at
depths 2 and 3, 48 comparisons, the chosen move, every root score and the exact
node count match. Node counts reproduce the values `qcap_identity.py` already
records (position 0 = 1238, position 2 = 1388).

### The rated round 30 regression

The reusable fixture still discriminates, and it reproduces exactly:

| Engine | Move | Completed depth | Seconds | Passes |
|---|---|---|---|---|
| Submitted `4551f4e` | `g5f4` | 3 | 2.88 | no |
| Control `be5da869` | `d1h5` | 4 | 1.96 | yes |
| Candidate `e5f63625` | `d1h5` | 4 | 3.41 | yes |

The candidate spends its larger budget here on an unfinished depth-5 iteration
rather than on a fifth completed depth, so at this one position the extra time
buys nothing. Whether it buys depth in general is the next section.

`rated_losses.py` was generalized to accept either budget expression so the same
fixture can diagnose a time candidate. The override it splices in is byte-identical
to the previous hard-coded one for the capped source, and the recorded submitted
depth ladder reproduces exactly (`g5f4` 39, `d1h5` 3, `g5f4` 31, `d1h5` -338), as
do the 3.0, 3.55, 4.5 and 6.0 s think rows. **One recorded row did not reproduce**:
at a 3.2 s think the record says depth 3 and `g5f4`, this run reached depth 4 and
`d1h5` in 3.067 s against a 3.072 s hard deadline. Depth 4 costs almost exactly the
3.2 s deadline, so that row is a knife edge that a few milliseconds of host
variation flips. It is not caused by the generalization and does not touch the
regression rule, which uses the real budget rather than an override.

### Does extra time become extra depth

The fixed tactical corpus, 24 positions × 6 clocks × 2 engines × 6 repeats,
counterbalanced execution order, 864 paired measurements. Clocks at or below 96 s
give the two engines a **provably identical** budget, so the differences there are
the wall-clock noise floor, and the clocks above 96 s carry the signal.

| clock | cap binds | Δ depth | deeper | shallower | agree | control s | candidate s |
|---|---|---|---|---|---|---|---|
| 120.0 s | yes | **+0.222** | 32 | 0 | 0.931 | 2.880 | 3.600 |
| 113.6 s | yes | **+0.153** | 22 | 0 | 0.924 | 2.880 | 3.408 |
| 100.0 s | yes | +0.035 | 6 | 1 | 0.986 | 2.880 | 3.000 |
| 96.0 s | no | -0.007 | 1 | 2 | 0.993 | 2.880 | 2.880 |
| 48.0 s | no | -0.021 | 2 | 5 | 0.979 | 1.440 | 1.440 |
| 10.0 s | no | -0.021 | 1 | 4 | 0.993 | 0.300 | 0.300 |

- **Noise floor**, 432 measurements at identical budgets: mean Δ depth **-0.016**,
  4 deeper against 11 shallower, largest per-clock magnitude **0.021**, median time
  difference 0.07 ms. No systematic effect, as it must be.
- **Signal**, 432 measurements above the cap: mean Δ depth **+0.137**, **60 deeper
  against 1 shallower**, all three clock levels net deeper and none net shallower,
  +0.456 s more thinking per move.

The signal is 6.6× the largest noise magnitude and rises monotonically with the
extra budget (+0.222 at +0.75 s, +0.153 at +0.55 s, +0.035 at +0.125 s, 0 below the
cap). The lone shallower measurement above the cap is at 100 s, where the extra
budget is 0.125 s, well inside noise. Move agreement is 98.8% below the cap, where
it should be 100% and the residual is deadline-boundary jitter, and 94.7% above it,
where the candidate genuinely searches further.

An earlier two-repeat pass reported five "depth regressions" and a `false` verdict.
Four of the five were at identical-budget clocks, so the criterion was wrong, not the
candidate: it demanded zero regressions from a noisy wall-clock measurement. The
criterion now compares the signal against the noise floor the experiment measures
directly.

### Clock safety through 600 plies

The referee stops at 600 plies, so 300 moves is every move one side can ever make.
`clock` mode draws 300 consecutive budgets on one live clock at 120 000 ms + 500 ms,
with a random legal opponent; when a position terminates the board advances to the
next declared opening and the clock carries on, because the clock recurrence is what
is under test, not the positions.

| | control | candidate |
|---|---|---|
| moves / plies | 300 / 600 | 300 / 600 |
| minimum clock | 18 500 ms | **18 395 ms** |
| final clock | 20 610 ms | 20 584 ms |
| worst move | 2 882 ms | 3 690 ms |
| worst spend / budget | 0.975 | 0.984 |
| flags | 0 | 0 |

`available / 32` is self-limiting, so both converge to the same steady state around
18-25 s and the candidate finishes a maximum-length game 105 ms behind the control.
The cap only ever mattered in the opening.

The recurrence was then propagated analytically over 300 and 600 moves at the
measured worst overshoot and at 1.5× and 2× that, drawing each budget from the
engine itself rather than from the source expression. The worst minimum clock over
all sixteen runs is **7 632 ms**, at double the worst overshoot ever observed.
Nothing flags.

### 24-game full clock comparison

Six workers on logical CPUs 0, 2, 4, 6, 8, 10, one thread per physical core, the
level `results/concurrency_v2/calibration_v2.json` approved. Twelve matched cases,
24 games, six colour pairs, the first six openings of `quiet_openings.json` in file
order, seeds 61000-61005, 120 000 ms + 500 ms, sparring against the control.
Configuration execution order is counterbalanced twice: within each shard by case
parity, and across shards by flipping `--configs`.

|  | control | candidate |
|---|---|---|
| score | 0.500 (+3 =6 -3) | 0.500 (+4 =4 -4) |
| mean depth, all moves | 4.383 | 4.490 |
| median clock left | 44.9 s | 43.0 s |
| lowest clock left in any game | 23.7 s | 25.9 s |
| lowest clock reached at any point | 22.2 s | 24.5 s |
| worst move | 2.884 s | 3.604 s |
| median move | 1.777 s | 1.787 s |
| total nodes | 17 327 199 | 18 142 152 |
| max init | 4 130 ms | 3 886 ms |
| max RSS | 195.1 MB | 198.0 MB |
| **flags / illegal / crashes / failures** | **0 / 0 / 0 / 0** | **0 / 0 / 0 / 0** |

Paired difference **0.0**, 95% interval [-0.125, +0.125] on both colour-pair and
opening clustering. Per pair the differences are -1, +1, +0.5, -0.5, -0.5, +0.5 and
six zeros. **This is directional only.** 24 games cannot establish strength and this
result does not claim any; it is here to show the change is safe under a real clock,
not that it is stronger.

Per-game mean depth in the arena is *not* a controlled depth measurement — the games
diverge after the first differing move, so the two engines are searching different
positions. The fixed corpus above is the controlled measurement. Likewise move
agreement is only meaningful on the fixed corpus.

## Verdict

The acceptance rule was zero flags, crashes and illegal moves; every existing
regression still passing; and additional thinking time converted into greater
completed depth on the fixed tactical corpus.

All three hold. **The candidate is accepted for retention as the development
candidate.** It is not submitted, not packaged and not committed, and no rated
result is attributed to it.

What this does *not* establish: that the engine is stronger. The 24-game score is
exactly even with an interval spanning ±0.125, which is what 24 games is worth. The
case for the change rests on the mechanism — the cap was throwing away 0.55-0.75 s
per move for the first handful of moves of every game, `RATED_LOSSES.md` shows
`slowest_s` = 2.9 s in all twelve rated games with a median 45.6 s of clock left
unused, and the corpus shows that time becoming depth — not on the arena score.

## Commands

```powershell
.\docker-test.ps1 -LogName time-lint ruff check .
.\docker-test.ps1 -LogName time-types mypy --strict agent.py tests/time_checks.py tests/time_report.py tests/rated_losses.py tests/rated_games.py tests/qgen_checks.py tests/numba_validation.py tests/numba_arena_summary.py tests/quiet_checks.py tests/quiet_report.py tests/quiet_openings.py tests/quiet_suite.py
.\docker-test.ps1 -LogName time-identity python tests/time_checks.py identity
.\docker-test.ps1 -LogName time-cold python tests/time_checks.py cold --repeats 3
.\docker-test.ps1 -CpuSet 0 -LogName time-budget python tests/time_checks.py budget
.\docker-test.ps1 -CpuSet 0 -LogName time-fixed python tests/time_checks.py fixed
.\docker-test.ps1 -CpuSet 0 -LogName time-gate python tests/time_checks.py gate --configs "control,candidate"
.\docker-test.ps1 -CpuSet 0 -LogName time-rated-submitted python tests/rated_losses.py --config submitted --regress
.\docker-test.ps1 -CpuSet 0 -LogName time-rated-control python tests/rated_losses.py --config candidate --regress
.\docker-test.ps1 -CpuSet 0 -LogName time-rated-timecand python tests/rated_losses.py --config candidate --regress --manifest tests/time_experiment.json
.\docker-test.ps1 -Detached -CpuSet 0 -LogName time-clock python tests/time_checks.py clock --moves 300 --stress-moves 600
```

Ladder, six shards of four positions, two repeats then four more:

```powershell
for ($s=0; $s -lt 6; $s++) {
    $cpu = 2*$s; $offset = 4*$s
    .\docker-test.ps1 -Detached -CpuSet "$cpu" -LogName "time-ladder-$s" python tests/time_checks.py ladder --repeats 2 --offset $offset --count 4
    .\docker-test.ps1 -Detached -CpuSet "$cpu" -LogName "time-ladder-b-$s" python tests/time_checks.py ladder --repeats 4 --repeat-offset 2 --offset $offset --count 4
}
Get-Content -LiteralPath (Get-ChildItem tests/results/time/ladder*.log) | docker run --rm -i --network none --cpus 1 --memory 2g --pids-limit 128 --read-only --tmpfs /tmp:rw,size=256m --mount "type=bind,source=$PWD,target=/workspace,readonly" chessathon-scope:test python tests/time_checks.py ladder-report *> "tests/results/time/ladder-merged.json"
```

Arena, six shards of one colour pair each, counterbalanced order:

```powershell
for ($s=0; $s -lt 6; $s++) {
    $cpu = 2*$s; $offset = 2*$s
    $order = if ($s % 2 -eq 0) { "control,candidate" } else { "candidate,control" }
    .\docker-test.ps1 -Detached -CpuSet "$cpu" -LogName "time-arena-$s" python tests/time_checks.py arena --cases 2 --case-offset $offset --seed 61000 --base-ms 120000 --increment-ms 500 --configs "$order"
}
$logs = for ($s=0; $s -lt 6; $s++) { $p="tests/results/time/arena-$s.log"; docker logs "time-arena-$s" *> $p; $p }
Get-Content -LiteralPath $logs | docker run --rm -i --network none --cpus 1 --memory 2g --pids-limit 128 --read-only --tmpfs /tmp:rw,size=256m --mount "type=bind,source=$PWD,target=/workspace,readonly" chessathon-scope:test python tests/numba_arena_summary.py --cases 12 --control control --candidate candidate *> "tests/results/time/arena-merged.json"
Get-Content -LiteralPath $logs | docker run --rm -i --network none --cpus 1 --memory 2g --pids-limit 128 --read-only --tmpfs /tmp:rw,size=256m --mount "type=bind,source=$PWD,target=/workspace,readonly" chessathon-scope:test python tests/time_report.py --cases 12 --base-ms 120000 --increment-ms 500 *> "tests/results/time/arena-clocks.json"
```

Raw logs and merged reports are in `results/time/`.

## Next

Per the standing instruction, stop here and analyse any further PGNs or private
logs before recommending another engine change. Seven of the eight rated losses
still have no move record, and five of those were played by the deployed
`agent.zip`. Downloading them remains the highest-value next action; a 24-game
even score is not a reason to change anything else.
