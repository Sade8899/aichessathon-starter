# Concurrency calibration

How many arena containers this host can run at once without the two configurations
being served unequally. Nothing here changes the engine or any existing result, and
no main arena was run.

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
