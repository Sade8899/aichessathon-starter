# Concurrency calibration

How many arena containers this host can run at once without the two configurations
being served unequally. Nothing here changes the engine or any existing result, and
no main arena was run.

There are two versions. **Version one** is preserved below exactly as recorded, at
commit `86d493cc7660969015a78383c1a4d75419fc3be4`, with its driver
`concurrency_calibration.py` and its records in `results/concurrency/`. Its
throughput, initialization, memory and failure measurements stand. Its **fairness
conclusion does not**: version one rejected 24 workers conservatively, but it did
not isolate fairness. **Version two**, at the end of this file, is the calibration
to rely on for fairness. Both reach the same operational answer, for different
reasons.

---

# Version one

## Design

Every container at every level runs the identical workload: `qgen_checks.py arena
--cases 2 --seed 59000 --base-ms 10000 --increment-ms 100 --experiment
tests/qcap_validation.json`. That is one matched colour pair, four games — control
and candidate each as White and as Black — on opening 0 of `quiet_openings.json`
with one seed and one clock. Sources are the pinned validation pair, control
`59f99079...` and candidate `be5da869...`. Because the workload is identical
everywhere, the only variable is how many containers run simultaneously.

Both configurations play inside every container, alternating execution order per
case, so configuration and CPU are balanced by construction: no core ever serves
only the control or only the candidate. Block B additionally reverses the container
to CPU mapping, so worker index and core index are not confounded either.

Placement, one logical CPU pinned per container:

| Level | Block A cpuset | Block B cpuset | Sharing |
|---|---|---|---|
| 6 | 0, 2, 4, 6, 8, 10 | reversed | one thread of each physical core |
| 12 | 0-11 | reversed | both threads of each physical core |
| 24 | 0-11 twice | reversed | two containers per logical CPU |

Each container keeps the standard limits: one CPU, 2 GB, 128 processes, read-only
filesystem and workspace, 256 MB `/tmp`, no network. Two blocks per level, so 336
games in total: 48 at level 6, 96 at 12, 192 at 24.

## Declared before the run

These constants are in `concurrency_calibration.py` and were written before any
container started, so the thresholds cannot have followed the numbers.

- **Throughput** is completed games per hour, measured as the makespan of a block:
  from launching the first container to the last one exiting, so queueing and
  initialization are included, not just search time.
- **Contention** is measured per configuration as the fraction of its own
  six-worker median nodes per second that it retains at a higher level. Median move
  time is the wrong metric here, because the engine spends its time allocation
  regardless of how much CPU it actually receives; nodes per second is what moves.
- **Imbalance** is `candidate_retained / control_retained - 1`, required to be
  within ±3%.
- **Material** gain is at least 10% more games per hour than the better of levels
  6 and 12.
- Approval requires a material gain, imbalance within ±3%, and zero failures.

## Results

Zero engine failures, zero non-zero container exits and zero flag falls at every
level: 336 of 336 games completed.

| Level | Games/hour | Block makespans, s | Total container memory, MB | Failures |
|---:|---:|---|---:|---:|
| 6 | 727.1 | 115.4 / 122.2 | 1,064 / 1,071 | 0 |
| 12 | 1,298.0 | 126.7 / 139.5 | 2,058 / 2,080 | 0 |
| 24 | 1,730.6 | 198.0 / 201.4 | 4,048 / 4,059 | 0 |

| Level | Config | Median NPS | Mean depth | Median move ms | Worst move ms | Median init ms | Max init ms | Max RSS MB |
|---:|---|---:|---:|---:|---:|---:|---:|---:|
| 6 | control | 10,420 | 1.842 | 205.6 | 305.4 | 3,908 | 5,462 | 172.4 |
| 6 | candidate | 14,008 | 1.966 | 208.6 | 304.1 | 3,646 | 4,627 | 173.1 |
| 12 | control | 6,667 | 1.435 | 216.2 | 379.4 | 6,021 | 7,458 | 172.7 |
| 12 | candidate | 8,181 | 1.602 | 213.3 | 342.5 | 5,991 | 7,535 | 169.7 |
| 24 | control | 2,891 | 1.199 | 220.9 | 405.6 | 11,956 | 18,587 | 171.2 |
| 24 | candidate | 3,707 | 1.315 | 216.4 | 334.2 | 12,858 | 15,577 | 167.4 |

| Level | Control NPS retained | Candidate NPS retained | Imbalance | Within ±3% |
|---:|---:|---:|---:|---|
| 6 | 1.000 | 1.000 | 0.00% | yes, by definition |
| 12 | 0.640 | 0.584 | **-8.73%** | no |
| 24 | 0.277 | 0.265 | **-4.62%** | no |

Memory was never a constraint. Peak container memory stayed near 180 MB at every
level, and 24 containers together held about 4.06 GB inside an 8.29 GB Docker VM.

## Decision

**24 workers are not approved, not even for exploratory screens.**

The throughput gain is real and material: 1,730.6 games per hour against 1,298.0
at twelve workers, +33.3% and comfortably past the declared 10%. There were no
failures. But the declared fairness condition fails: the candidate retains 26.5%
of its six-worker node rate while the control retains 27.7%, an imbalance of
-4.62% against a ±3% limit. Twelve workers are worse on the same measure, -8.73%.
Only the six-worker level satisfies the condition, and it does so trivially,
because it is the baseline the others are compared against.

Approving 24 workers would mean accepting that the configuration under test is
systematically served slightly less CPU than its control. That is exactly the bias
a paired screen is supposed to exclude, and a screen is the thing whose result
would be used to decide the experiment. The throughput is not worth it.

**Six workers remain the setting for authoritative 120-second testing**, unchanged
by this calibration, and remain the only calibrated setting for screens as well.

## What this does and does not establish

The direction is consistent — the candidate loses slightly more node rate than the
control at both oversubscribed levels — and it has a plausible mechanism: the
candidate runs at a higher node rate, so it makes heavier demands on the shared
per-core resources that a second thread on the same core competes for. Consistency
across two levels and two reversed blocks is suggestive, but two blocks per level
is a small sample and no interval was computed for the imbalance itself.

The metric has a real confound that should be stated plainly. Contention lowers
completed depth sharply, from 1.84 to 1.20 ply for the control and 1.97 to 1.32 for
the candidate, so the engines choose different moves and the games diverge. Nodes
per second is position-dependent, so part of the measured imbalance may be a
different mix of positions rather than unequal CPU service. That is a weakness of
the measurement, not a reason to set the declared threshold aside: the honest
conclusion is that this calibration cannot demonstrate the two configurations are
served equally at 12 or 24 workers, which is what approval required.

Two further observations, independent of the decision. Initialization degrades
badly under oversubscription, from about 3.8 s median at six workers to 12 s median
and 18.6 s worst at 24; that is still far inside the platform's 90 s import budget
but it consumes a growing share of each block. And at 24 workers the engines
complete barely more than one ply, so games played there are far from tournament
conditions and their outcomes would say little about strength even if the fairness
condition had passed.

## Commands

```powershell
python tests/concurrency_calibration.py run --levels 6 --blocks A,B
python tests/concurrency_calibration.py run --levels 12 --blocks A,B
python tests/concurrency_calibration.py run --levels 24 --blocks A,B
python tests/concurrency_calibration.py report --levels 6,12,24
```

The driver runs on the host and calls Docker directly; it is the only thing in
`tests/` that does. Raw per-container arena logs, per-block records and the merged
`calibration.json` are in `results/concurrency/`.

---

# Version two

Version one's fairness statistic was confounded, so this calibration re-measures
fairness properly. Throughput, initialization, memory and failures are re-measured
too, under synchronized starts. Version one's files and records are untouched;
version two lives in `concurrency_v2.py`, `concurrency_fairness.py` and
`results/concurrency_v2/`.

## Why version one could not isolate fairness

Every version one container ran the same four games in the same order:
**control, candidate, candidate, control**. Block B reversed the container-to-CPU
mapping but not that order, so the two configurations never swapped execution slots.
The colour slots then disagree sharply about which configuration is disadvantaged.
Recomputing version one's own statistic separately by colour, from its own logs:

| Version one | Aggregate | White | Black |
|---|---:|---:|---:|
| 12 workers | -8.73% | -12.47% | -0.75% |
| 24 workers | **-4.62%** | **+6.49%** | **-24.73%** |

At 24 workers the candidate looks favoured on White and heavily penalised on Black.
A genuine difference in CPU service would not reverse sign by colour. The -4.62%
aggregate is a weighted mixture of two contradictory numbers, so it does not
demonstrate unequal CPU service. The underlying cause is that version one measured
nodes per second over *arena games*, and contention lowers completed depth, so the
games diverge and the two configurations end up searching different positions.

## What Docker actually exposes

Recorded before any CPU set was chosen, in `results/concurrency_v2/topology.json`.
The guest reports an Intel i7-8700, 12 logical CPUs, 6 core ids, with thread
siblings in adjacent pairs `(0,1) (2,3) (4,5) (6,7) (8,9) (10,11)`. `--cpuset-cpus N`
does restrict a container to guest CPU `N`, confirmed with `sched_getaffinity`.

But the `hypervisor` flag is set and the kernel is `6.6.87.2-microsoft-standard-WSL2`.
This is a Hyper-V utility VM, and guest vCPU to host logical processor placement is
the hypervisor's to decide, not ours. **No physical core isolation is claimed here.**
Version one's description of cpuset `0,2,4,6,8,10` as "one thread from each physical
core" was an overstatement: it is one CPU from each *guest-reported* core id, which
may or may not correspond to distinct host cores. CPU sets are used only to spread
workers deterministically and to keep placement identical between the two versions.

## Design

Two phases per level, each with its own containers, both with synchronized starts.

**Fairness phase, the primary statistic.** Each container runs
`concurrency_fairness.py`: both configurations search the existing 24-position
fixed-depth suite to depth two, three times each. Node counts at fixed depth are
identical between the configurations and across levels, so the work is the same
everywhere and wall time can only measure how much CPU the container received. This
was verified rather than assumed: across all 6,048 measurements every position had a
single node count and a single chosen move, totalling 36,369 nodes per pass, matching
the figure recorded in `NUMBA.md`.

**Arena phase, throughput only.** The version one workload, unchanged: two cases,
four games, opening 0, seed 59000, 10 s + 0.1 s. Used for games per hour,
initialization, memory and failures. Its depth and node rate are *not* used for
fairness, which is the version one mistake.

**Counterbalanced order.** Each container is assigned an execution order, and
`qgen_checks.py arena` gained a `--configs` argument, defaulting to the previous
`control,candidate`, so the arena order can be reversed too. A worker on guest CPU
`c`, replica `r`, runs control first when `(c // 2 + c % 2 + r)` is even. That
balances order within every core id, across both sibling positions, and between the
two containers sharing a CPU at 24 workers. Verified from the logs: at every level
each configuration appears in slot 0 exactly as often as in slot 1 (216, 432 and 864
measurements each).

**Synchronized start.** Docker's clock is compared with the host's, then every
container is launched with a shell barrier that waits for one shared future epoch
second before executing. Makespan is measured from that instant, not from the first
`docker run`, so container creation is excluded from throughput. All containers were
created before the barrier at every level, and the measured clock offset was 0.09 to
0.11 s.

**Fairness statistic.** For each configuration, level and position, take the median
seconds. Speed retained is the six-worker time divided by the time at this level, on
the same position. A configuration's retention is the median of that ratio over the
24 positions. Imbalance is `candidate_retention / control_retention - 1`. It is
computed three times: aggregate, control-first stratum, candidate-first stratum.

## Declared thresholds

Unchanged from version one, plus the strata requirement: a material gain is at least
10% more games per hour than the better of levels 6 and 12; imbalance must be within
plus or minus 3%; failures must be zero; and **the aggregate and both execution-order
strata must all pass**.

## Results

Every level ran once. Zero engine failures, zero non-zero container exits, zero flag
falls, across 168 arena games and 6,048 fairness measurements.

| Level | Games/hour | Arena makespan, s | Fairness makespan, s | Arena memory, MB | Failures |
|---:|---:|---:|---:|---:|---:|
| 6 | 771.9 | 111.9 | 27.4 | 1,069 | 0 |
| 12 | 1,359.0 | 127.2 | 36.3 | 2,102 | 0 |
| 24 | 1,892.5 | 182.6 | 79.1 | 4,066 | 0 |

Fairness, on the identical fixed-depth corpus:

| Level | Stratum | Control retained | Candidate retained | Imbalance | Within limit |
|---:|---|---:|---:|---:|---|
| 12 | aggregate | 0.713 | 0.717 | +0.65% | yes |
| 12 | control first | 0.712 | 0.738 | **+3.68%** | no |
| 12 | candidate first | 0.731 | 0.729 | -0.26% | yes |
| 24 | aggregate | 0.345 | 0.352 | +1.94% | yes |
| 24 | control first | 0.337 | 0.350 | **+3.80%** | no |
| 24 | candidate first | 0.344 | 0.355 | **+3.29%** | no |

Arena initialization, memory and move time, per configuration:

| Level | Config | Median init ms | Max init ms | Median move ms | Worst move ms | Max RSS MB |
|---:|---|---:|---:|---:|---:|---:|
| 6 | control | 3,617 | 3,704 | 235.9 | 307.1 | 172.3 |
| 6 | candidate | 3,587 | 3,684 | 190.7 | 303.3 | 172.8 |
| 12 | control | 5,316 | 6,053 | 202.5 | 331.2 | 172.4 |
| 12 | candidate | 5,420 | 5,962 | 214.0 | 303.6 | 171.9 |
| 24 | control | 12,308 | 16,057 | 218.9 | 377.2 | 167.8 |
| 24 | candidate | 12,563 | 15,926 | 222.4 | 329.1 | 170.9 |

Fairness containers peaked near 87 MB; arena containers near 180 MB, 4.07 GB in
total at 24 workers inside an 8.29 GB Docker VM. Memory was never the constraint.

## Decision

**24 workers are not approved, including for exploratory screens.** Six workers
remain the calibrated setting for screens and for authoritative 120-second testing.

Throughput at 24 is 1,892.5 games per hour against 1,359.0 at twelve, +39.3% and
well past the declared 10%. Failures were zero. The aggregate imbalance passes at
+1.94%. But both execution-order strata fail: +3.80% control-first and +3.29%
candidate-first, against a 3% limit, and the rule declared before the run requires
the aggregate *and* both strata. Twelve workers also fail, on the control-first
stratum at +3.68%.

## What changed, and what version two establishes

The direction reversed. Version one suggested the candidate was served about 4.6%
*less* CPU at 24 workers; version two, on identical work, finds it retains slightly
*more*, around +2% aggregate and +3.3% to +3.8% within strata. Version one's fairness
conclusion was an artifact of fixed execution slots and diverging game positions, and
its rejection of 24 workers, while operationally correct, rested on a statistic that
could not support it. Version two rejects 24 workers on a measurement that can.

The residual imbalance is small and now favours the candidate, which is a milder
problem than the reverse, but it is still a systematic advantage to the configuration
under test in exactly the comparison a paired screen exists to make unbiased, and it
exceeds the limit declared in advance.

Two honest limitations. First, the three fairness statistics pool measurements
differently, so the aggregate is not algebraically bounded by the two strata, and at
24 workers it does sit below both; the declared rule requires all three, so this does
not change the outcome, but the aggregate should not be read as an average of the
strata. Second, each level was run once, as instructed, so no interval is available
for the imbalance itself, and a 3.3% to 3.8% effect is close enough to the 3% limit
that repeat runs could straddle it. The conservative reading, and the one taken here,
is that 24 workers are not demonstrated to be fair.

Unchanged from version one, and confirmed: initialization degrades badly under
oversubscription, from about 3.6 s median at six workers to 12.3 s median and 16.1 s
worst at 24, and arena completed depth falls to near one ply, so 24-worker games
would be far from tournament conditions regardless of fairness.

## Commands

```powershell
python tests/concurrency_v2.py topology
python tests/concurrency_v2.py run --levels 6
python tests/concurrency_v2.py run --levels 12
python tests/concurrency_v2.py run --levels 24
python tests/concurrency_v2.py report --levels 6,12,24
```

The driver runs on the host and calls Docker directly. Raw per-container logs, the
topology record, per-phase records and the merged `calibration_v2.json` are in
`results/concurrency_v2/`.
