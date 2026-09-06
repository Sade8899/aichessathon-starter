# Selective quiescence capture generation

See [QCAP.md](QCAP.md). `qcap_experiment.json` pins the Numba control and the
candidate `8e7001995c76c1d3a3ad31b7054351b9436d2e64be4078ba53a5d24c9c7a33b3`;
raw measurements are in `results/qcap/`. `qgen_checks.py` now takes
`--experiment`, defaulting to `qgen_experiment.json`, so every command in the
section below still runs unchanged and selects the earlier, rejected candidate.
Pass the manifest explicitly to exercise this one:

```powershell
.\docker-test.ps1 -LogName qcap-lint ruff check .
.\docker-test.ps1 -LogName qcap-types mypy --strict agent.py tests/qgen_checks.py tests/numba_validation.py tests/numba_arena_summary.py tests/quiet_checks.py tests/quiet_report.py tests/quiet_openings.py tests/quiet_suite.py
.\docker-test.ps1 -LogName qcap-targeted python tests/qgen_checks.py targeted --experiment tests/qcap_experiment.json
.\docker-test.ps1 -LogName qcap-equality python tests/qgen_checks.py equality --experiment tests/qcap_experiment.json
.\docker-test.ps1 -LogName qcap-benchmark python tests/qgen_checks.py bench --repeats 3 --experiment tests/qcap_experiment.json
.\docker-test.ps1 -LogName qcap-timing python tests/qgen_checks.py timing --repeats 2 --experiment tests/qcap_experiment.json
.\docker-test.ps1 -LogName qcap-legality python tests/verify.py
.\docker-test.ps1 -LogName qcap-determinism python tests/determinism.py
.\docker-test.ps1 -LogName qcap-passive python tests/qgen_checks.py passive --experiment tests/qcap_experiment.json
.\docker-test.ps1 -LogName qcap-gate make gate
.\docker-test.ps1 -Detached -CpuSet 0 -LogName qcap-smoke python tests/qgen_checks.py arena --cases 10 --seed 51000 --experiment tests/qcap_experiment.json
```

## Source identity

`agent.py` and every frozen source are stored and checked out with LF; the
`.gitattributes` rules pin that so raw-byte SHA-256 assertions reproduce from a
fresh clone on any platform. The retained candidate is
`be5da8696f01924e2f752b1686e1d6a94b38dc72fff86a1f7fedb05f006c56e0`. The earlier
qcap measurements loaded the same program written with CRLF,
`8e7001995c76c1d3a3ad31b7054351b9436d2e64be4078ba53a5d24c9c7a33b3`, which
`qcap_experiment.json` still pins; the two hashes are different byte strings and
are never treated as equal. `qcap_validation.json` pins the LF pair used by the
validation campaign.

```powershell
.\docker-test.ps1 -LogName qcap-identity python tests/qcap_identity.py
.\docker-test.ps1 -LogName qcap-loader python tests/qcap_identity.py loader
git add --renormalize .   # must report no change
```

`qcap_identity.py` proves the newline conversion is the only difference, by byte
conversion in both directions and by identical AST and marshalled code objects.
`loader` confirms the harness accepts the exact validation bytes.

## Validation campaign

Both stages use the thirty openings in `quiet_openings.json` in file order; none
was selected or replaced using a result. Fast screen: 60 matched colour pairs,
120 cases, 240 games at 10,000 ms + 100 ms, seed 57000, six shards of 20 cases
pinned to logical CPUs 0, 2, 4, 6, 8, 10, one thread per physical core.

```powershell
for ($s=0; $s -lt 6; $s++) {
    $cpu = 2*$s; $offset = 20*$s
    .\docker-test.ps1 -Detached -CpuSet "$cpu" -LogName "qcap-screen-$s" python tests/qgen_checks.py arena --cases 20 --case-offset $offset --seed 57000 --experiment tests/qcap_validation.json
}
for ($s=0; $s -lt 6; $s++) { docker inspect "qcap-screen-$s" --format '{{.State.Status}} {{.State.ExitCode}}' }
$logs = for ($s=0; $s -lt 6; $s++) { $p="tests/results/qcap/qcap-screen-$s.log"; docker logs "qcap-screen-$s" *> $p; $p }
Get-Content -LiteralPath $logs | docker run --rm -i --network none --cpus 1 --memory 2g --pids-limit 128 --read-only --tmpfs /tmp:rw,size=256m --mount "type=bind,source=$PWD,target=/workspace,readonly" chessathon-scope:test python tests/numba_arena_summary.py --cases 120 --control control --candidate candidate *> "tests/results/qcap/qcap-screen-merged.json"
```

Every shard must reach `exited 0` before merging; the merge validates source
hashes, identical conditions, complete cases, colour reversal and duplicates.
The fast screen finished: candidate 62.50% (+73 =4 -43) against control 45.83%
(+51 =8 -61), a paired difference of +16.67 points, 95% colour-pair interval
[+6.67, +26.25] and opening interval [+5.42, +27.50], zero failures over 240
games. The full-clock confirmation then ran, and disagreed: candidate 42.5%
against control 45.0%, a paired difference of -2.5 points with a 95% interval of
[-20.0, +17.5] over ten clusters, zero failures over 40 games. Retention stays
provisional; see [QCAP.md](QCAP.md). Do not rerun either stage to move the
estimate.

The full-clock confirmation runs only if the screen's paired point estimate is
nonnegative with zero failures. Its ten openings were declared before the screen
launched: the first ten of `quiet_openings.json` in file order, one four-game
matched colour pair each, run serially in one container.

```powershell
.\docker-test.ps1 -Detached -CpuSet 0 -LogName qcap-confirm python tests/qgen_checks.py arena --cases 20 --seed 58000 --base-ms 120000 --increment-ms 500 --experiment tests/qcap_validation.json
docker logs qcap-confirm *> "tests/results/qcap/qcap-confirm.log"
Get-Content "tests/results/qcap/qcap-confirm.log" | docker run --rm -i --network none --cpus 1 --memory 2g --pids-limit 128 --read-only --tmpfs /tmp:rw,size=256m --mount "type=bind,source=$PWD,target=/workspace,readonly" chessathon-scope:test python tests/numba_arena_summary.py --cases 20 --control control --candidate candidate *> "tests/results/qcap/qcap-confirm-merged.json"
```

`verify.py`, `determinism.py` and `make gate` import the working `agent.py`, so
run them while the candidate is in place. Merge a benchmark log into the paired
per-position report, and a detached arena into the paired score:

```powershell
Get-Content "$env:TEMP/qcap-benchmark.log" | docker run --rm -i --network none --cpus 1 --memory 2g --pids-limit 128 --read-only --tmpfs /tmp:rw,size=256m --mount "type=bind,source=$PWD,target=/workspace,readonly" chessathon-scope:test python tests/qgen_checks.py report *> "tests/results/qcap/qcap-report.json"
docker logs qcap-smoke *> "$env:TEMP/qcap-smoke.log"
Get-Content "$env:TEMP/qcap-smoke.log" | docker run --rm -i --network none --cpus 1 --memory 2g --pids-limit 128 --read-only --tmpfs /tmp:rw,size=256m --mount "type=bind,source=$PWD,target=/workspace,readonly" chessathon-scope:test python tests/numba_arena_summary.py --cases 10 --control control --candidate candidate *> "tests/results/qcap/qcap-smoke-merged.json"
```

The `targeted` mode gained a direct comparison of `captures_and_promotions`
against the control's filter over a corpus that actually contains en passant and
promotions, thirteen hand-authored special-move fixtures, and recording of the
list after `Engine.order`'s stable sort so equal-priority ties are compared too.
The 20-game paired smoke finished with zero failures: control 45% (+4 =1 -5),
candidate 70% (+7 =0 -3), a paired difference of +25 points with a 95% colour-pair
interval of [-20, +60] over five clusters. That interval includes zero, so it is a
screen, not a strength result. The candidate is provisionally retained; do not run
a larger arena without authorization.

`passive_units.unchanged_search` and `numba_validation.py check` are historical
AST identity checks for the original search; this experiment changes
`Engine.quiesce`, so they cannot pass and were not run. They are unmodified.

# Quiescence move-generation experiment

See [QGEN.md](QGEN.md) for the single-generator experiment and its rejection.
`qgen_experiment.json` pins the Numba control and the candidate; `qgen_checks.py`
loads those frozen sources, so every command below still runs after `agent.py`
was restored. Raw logs, the per-position report, the decision record and the
applied diff are in `results/qgen/`. The candidate is
`bb6ab82b95319283e6cdef6bf8a7b20ae3b185125942e041f9ca5352301a3fee`.

Use the existing `chessathon-scope:test` image and the same runner restrictions.

```powershell
.\docker-test.ps1 -LogName qgen-lint ruff check .
.\docker-test.ps1 -LogName qgen-types mypy --strict agent.py tests/qgen_checks.py tests/numba_validation.py tests/numba_arena_summary.py tests/quiet_checks.py tests/quiet_report.py tests/quiet_openings.py tests/quiet_suite.py
.\docker-test.ps1 -LogName qgen-targeted python tests/qgen_checks.py targeted
.\docker-test.ps1 -LogName qgen-equality python tests/qgen_checks.py equality
.\docker-test.ps1 -LogName qgen-benchmark python tests/qgen_checks.py bench --repeats 3
```

`targeted` compares 552 whole quiescence trees, the ordered move list at every
quiescence node, stand pat cutoffs that build no list, first-move preservation,
terminal/draw/ply-cap precedence and board and repetition restoration, including
after a deadline. `equality` covers the AST scope, 2,400 evaluation comparisons,
Numba signature stability and exact fixed-depth moves, root scores and nodes on
the 24 positions. Merge the benchmark into the paired per-position report with:

```powershell
Get-Content "$env:TEMP/qgen-benchmark.log" | docker run --rm -i --network none --cpus 1 --memory 2g --pids-limit 128 --read-only --tmpfs /tmp:rw,size=256m --mount "type=bind,source=$PWD,target=/workspace,readonly" chessathon-scope:test python tests/qgen_checks.py report *> "$env:TEMP/qgen-report.json"
```

The paired median throughput gain was +4.90% at fixed depth and +4.37% timed,
below the 5% screening threshold, so the experiment is rejected and the remaining
correctness suite and the 20-game smoke were never started. `qgen_checks.py` also
has `passive`, `timing` and `arena` modes for the gates that were not reached.
Do not run them to revisit the decision. Search equivalence was exact:
identical moves, root scores and node counts on all 24 fixed-depth positions.

`agent.py` is the retained Numba baseline
`59f99079f1db99221683dd3f06391f4fc502c1dae11fb712b08170242649830a`, restored
byte for byte. Verify with:

```powershell
(Get-FileHash agent.py -Algorithm SHA256).Hash.ToLower()
$sha='59f99079f1db99221683dd3f06391f4fc502c1dae11fb712b08170242649830a'
Compare-Object (Get-Content agent.py) (Get-Content "tests/numba_checkpoint/$sha/agent.py")
```

# Quiet-check experiment

See [QUIET_CHECKS.md](QUIET_CHECKS.md) for the bounded-check experiment and its
retention decision. `quiet_experiment.json` pins the Numba control and experimental
candidate; these commands load those frozen sources even after `agent.py` is
restored. No existing baseline is overwritten. The candidate's timed-depth
regression is a failed gate, not a reason to loosen the original assertion.

Use the existing `chessathon-scope:test` image and the same runner restrictions.
The following commands exercise the experiment independently of the working agent:

```powershell
.\docker-test.ps1 -LogName qcheck-targeted python tests/quiet_checks.py targeted
.\docker-test.ps1 -LogName qcheck-equality python tests/quiet_checks.py equality
.\docker-test.ps1 -LogName qcheck-benchmark python tests/quiet_checks.py bench --repeats 3
.\docker-test.ps1 -LogName qcheck-counts python tests/quiet_checks.py counters
.\docker-test.ps1 -LogName qcheck-init python tests/quiet_checks.py init --repeats 3
.\docker-test.ps1 -LogName qcheck-replay1 python tests/quiet_checks.py replay
.\docker-test.ps1 -LogName qcheck-replay2 python tests/quiet_checks.py replay
.\docker-test.ps1 -LogName qcheck-cold1 python tests/quiet_checks.py cold
.\docker-test.ps1 -LogName qcheck-cold2 python tests/quiet_checks.py cold
.\docker-test.ps1 -LogName qcheck-timing-gate python tests/quiet_checks.py timing --repeats 2
.\docker-test.ps1 -LogName qcheck-candidate-suite python tests/quiet_suite.py candidate
.\docker-test.ps1 -LogName qcheck-control-suite python tests/quiet_suite.py control
```

`quiet_suite.py` creates a temporary development copy under `/tmp`, uses the
selected frozen agent, and executes existing legality, determinism, Bayesian,
safety, lifecycle and official gates. It preserves the official harness and
every assertion. Historical unchanged-search checks additionally run on control.
The candidate intentionally changes search results; `quiet_checks.py equality`
checks evaluation equality and exact disabled-toggle equivalence separately.

The 30 opening prefixes in `quiet_openings.py` deterministically reproduce
`quiet_openings.json`. Each matched colour pair has four games: each configuration
faces the same frozen control with both colours. Sixty pairs are 120 matched
cases / 240 games. After the 20-game smoke finishes cleanly:

```powershell
.\docker-test.ps1 -Detached -CpuSet 0 -LogName qcheck-smoke python tests/quiet_checks.py arena --cases 10 --seed 51000
for ($s=0; $s -lt 6; $s++) {
    $cpu=2*$s; $offset=20*$s
    .\docker-test.ps1 -Detached -CpuSet "$cpu" -LogName "qcheck-screen-$s" python tests/quiet_checks.py arena --cases 20 --case-offset $offset --seed 53000
}
```

Wait for `docker inspect <name> --format '{{.State.Status}} {{.State.ExitCode}}'`
to show `exited 0` for every shard. Repeated runs need unique container names.
The six affinity targets match this host's six physical cores, not six arbitrary
hyperthreads. No more than six games run concurrently. Capture and merge:

```powershell
$logs = for ($s=0; $s -lt 6; $s++) {
    $path=Join-Path $env:TEMP "qcheck-screen-$s.log"
    docker logs "qcheck-screen-$s" *> $path
    $path
}
Get-Content -LiteralPath $logs | docker run --rm -i --network none --cpus 1 --memory 2g --pids-limit 128 --read-only --tmpfs /tmp:rw,size=256m --mount "type=bind,source=$PWD,target=/workspace,readonly" chessathon-scope:test python tests/numba_arena_summary.py --cases 120 --control control --candidate candidate *> "$env:TEMP/qcheck-screen-merged.json"
Get-Content "$env:TEMP/qcheck-benchmark.log","$env:TEMP/qcheck-counts.log" | docker run --rm -i --network none --cpus 1 --memory 2g --pids-limit 128 --read-only --tmpfs /tmp:rw,size=256m --mount "type=bind,source=$PWD,target=/workspace,readonly" chessathon-scope:test python tests/quiet_report.py *> "$env:TEMP/qcheck-performance.json"
Get-Content "$env:TEMP/qcheck-benchmark.log" | docker run --rm -i --network none --cpus 1 --memory 2g --pids-limit 128 --read-only --tmpfs /tmp:rw,size=256m --mount "type=bind,source=$PWD,target=/workspace,readonly" chessathon-scope:test python tests/quiet_checks.py timed-counters *> "$env:TEMP/qcheck-timed-counts.log"
```

Timed counters replay each measured search to its recorded node/depth cutoff
with development instrumentation. Move, completed depth and total nodes must
match the original timed call. Instrumentation never contributes to production
latency/NPS measurements. Raw output remains in host temporary files.

The screen is complete. All six shards exited 0; the merge accepted 120 cases /
240 games, 60 colour reversals, 30 openings and zero failures. Control scored
45.83% (+45 =20 -55) and the candidate 18.33% (+16 =12 -92), a paired difference
of -27.5 points, 95% colour-pair interval [-36.25, -18.33] and 95% opening
interval [-37.08, -17.50]. The extension is rejected on the earlier smoke and
timed-depth gates; see [QUIET_CHECKS.md](QUIET_CHECKS.md). Do not start another
arena to revisit that decision.

`agent.py` is the retained Numba baseline
`59f99079f1db99221683dd3f06391f4fc502c1dae11fb712b08170242649830a`. Verify the
restoration and re-run the existing gates against it with:

```powershell
(Get-FileHash agent.py -Algorithm SHA256).Hash.ToLower()
$sha='59f99079f1db99221683dd3f06391f4fc502c1dae11fb712b08170242649830a'
Compare-Object (Get-Content agent.py) (Get-Content "tests/numba_checkpoint/$sha/agent.py")

.\docker-test.ps1 -LogName restore-lint ruff check .
.\docker-test.ps1 -LogName restore-types mypy --strict agent.py tests/numba_validation.py tests/numba_arena_summary.py tests/quiet_checks.py tests/quiet_report.py tests/quiet_openings.py tests/quiet_suite.py
.\docker-test.ps1 -LogName restore-check python tests/numba_validation.py check
.\docker-test.ps1 -LogName restore-legal python tests/verify.py
.\docker-test.ps1 -LogName restore-determ python tests/determinism.py
.\docker-test.ps1 -LogName restore-passive python tests/passive_units.py
.\docker-test.ps1 -LogName restore-gate make gate
```

`numba_validation.py check` covers import, Numba signatures, the 2,400
evaluation comparisons and exact fixed-depth equality. `verify.py` covers the
500-position legality/clock suite and the castling, en passant, promotion,
underpromotion, stalemate, fifty-move and threefold fixtures. `passive_units.py`
covers the Bayesian units, the 93 safe-set cases and the lifecycle checks.
`make gate` runs the unchanged official two-game referee.

# Retained Numba evaluation checkpoint

The current experiment changes only evaluation execution. Search, evaluation
weights, clocks, caches, repetition and passive SCOPE remain unchanged;
`ADAPTIVE=False`. See [NUMBA.md](NUMBA.md) for measurements and the decision.
The exact submitted source is pinned by `tournament/submitted.json` and is never
rewritten. Older passive-model results below are historical, not results for
this optimization.

Use the existing `chessathon-scope:test` image. All commands run with one CPU,
2 GB memory, 128 processes, a read-only workspace/filesystem, 256 MB `/tmp`,
and no network. The optional build targets `python-test`, using Python 3.12
and the frozen `pyproject.toml`/`uv.lock`; it does not build the parked native
tournament stage. No runtime dependency was added.

```powershell
.\docker-test.ps1 -Build -LogName numba-build-check ruff check .
.\docker-test.ps1 -LogName numba-final-exact python tests/numba_validation.py check
.\docker-test.ps1 -LogName numba-benchmark python tests/numba_validation.py bench --repeats 3
.\docker-test.ps1 -LogName numba-profile python tests/numba_validation.py profile
.\docker-test.ps1 -LogName numba-round30-stateful python tests/numba_validation.py round30
.\docker-test.ps1 -LogName numba-horizon python tests/numba_validation.py horizon
.\docker-test.ps1 -LogName numba-legality python tests/verify.py
.\docker-test.ps1 -LogName numba-determinism python tests/determinism.py
.\docker-test.ps1 -LogName numba-passive python tests/passive_units.py
.\docker-test.ps1 -LogName numba-development-types mypy --strict agent.py tests/numba_validation.py tests/numba_arena_summary.py
.\docker-test.ps1 -LogName numba-gate make gate
```

Paired arena cases each run the submitted and candidate agents against the exact
submitted rival. Consecutive cases reverse colours with the same opening and
seed. Ten cases are 20 games; 60 cases are 120 games. Five test-only openings
come from `passive_arena.OPENINGS`, never from agent gameplay. Both sides use
fresh official runner processes per game and persistent state within each game.
The unchanged official referee charges wall time and awards increments.

Long arenas can run detached so a conversation interruption cannot terminate
the Docker client and its game. Use unique names when repeating a run:

```powershell
.\docker-test.ps1 -Detached -LogName numba-smoke-final python tests/numba_validation.py arena --cases 10 --seed 39000
# Run after the smoke passes:
.\docker-test.ps1 -Detached -LogName numba-screen60 python tests/numba_validation.py arena --cases 60 --seed 41000
# Run only after a nonnegative fast screen:
.\docker-test.ps1 -Detached -LogName numba-confirmation python tests/numba_validation.py arena --cases 4 --seed 43000 --base-ms 120000 --increment-ms 500

docker inspect numba-smoke-final --format '{{.State.Status}} {{.State.ExitCode}}'
docker logs numba-smoke-final *> "$env:TEMP/numba-smoke-final.log"
Get-Content "$env:TEMP/numba-smoke-final.log" -Tail 1
```

Replace the name in the last three commands to inspect other runs. Results and
verbose output stay in host temporary logs. Detached containers remain available
for log inspection; the Python Docker image is reusable. No packaging commands
are part of this checkpoint.

The primary 60-case screen uses six disjoint shards. Docker reports six physical
cores (logical siblings 0/1, 2/3, ..., 10/11); these commands pin one game to each
physical core. Each container retains the same one-CPU/2-GB restrictions.
Use different container/log names when repeating an existing run.

```powershell
for ($s=0; $s -lt 6; $s++) {
    $cpu=2*$s; $offset=10*$s
    .\docker-test.ps1 -Detached -CpuSet "$cpu" -LogName "numba-screen-shard$s" python tests/numba_validation.py arena --cases 10 --case-offset $offset --seed 41000
}
```

After all six containers report `exited 0`, collect and merge their complete
logs. The merge rejects missing/duplicate cases, unequal conditions, failed
games, incomplete shards and missing colour reversals. It calculates percentiles
from all move records, rather than averaging shard percentiles.

```powershell
$logs = for ($s=0; $s -lt 6; $s++) {
    $path=Join-Path $env:TEMP "numba-screen-shard$s.log"
    docker logs "numba-screen-shard$s" *> $path
    $path
}
Get-Content -LiteralPath $logs | docker run --rm -i --network none --cpus 1 --memory 2g --pids-limit 128 --read-only --tmpfs /tmp:rw,size=256m --mount "type=bind,source=$PWD,target=/workspace,readonly" chessathon-scope:test python tests/numba_arena_summary.py --cases 60 *> "$env:TEMP/numba-screen-merged.log"
Get-Content "$env:TEMP/numba-screen-merged.log"
```

The smaller full-clock confirmation can likewise use two shards with
`--cases 2 --case-offset 0` and `--cases 2 --case-offset 2`, seed 43000,
`--base-ms 120000 --increment-ms 500`, on CPUs 0 and 2. Merge their logs with
`numba_arena_summary.py --cases 4`. The benchmark and timed tactical replay
remain separate one-container measurements.

# Historical passive SCOPE checkpoint

`agent.py` remains a standard-chess engine with `get_move(fen, time_left_ms)`.
`ADAPTIVE = False` and `PASSIVE = True` are the tested development defaults.
The classical evaluation, move ordering, quiescence, repetition handling and
mate-distance helpers are unchanged. Tests compare their ASTs with the frozen
checkpoint and compare completed searches through the real agent interface.

## Frozen baseline

`tests/checkpoint/agent.py` is the exact previous agent, including its disabled
adaptation flag and original profiling path. Its SHA-256 is:

`9a77d2a1473ead6349da8a8aa7481125f4fdb2e579d7bb4f2e8dd3ee4ada6c1c`

Do not update that file when modifying the current agent. Arena configurations
are the frozen baseline, passive profiling with adaptation disabled, and the
current agent with adaptation enabled. The current search keeps the baseline's
60 cp root search window; the narrower exploitation limits apply only to the
final completed-depth candidate set.

## Passive model and safety

`OpponentModel` exposes only `observe`, `predict`, `confidence` and `reset`.
It retains six Bayesian probabilities and bounded confidence/change statistics.
It never calls search, touches a board, controls time or selects a move.

The objective search records reply features, score, depth and bound at ply one
for no more than the three leading root moves. A new iteration has a separate
buffer. Only the deepest fully completed iteration survives; aborted evidence
is discarded. Missing replies remain missing. FEN reconstruction precedes the
Bayesian update, which happens before the next search. Tiny-clock emergency
fallback retains the previous engine's immediate-return behaviour.

Likelihoods use capture/promotion gain, forcing features, score intervals and
available search depth. Coverage, bound quality and informativeness temper the
Bayesian update. Unknown evidence decays beliefs toward the prior; contradictory
observations increase the unknown hypothesis. Predictions cannot convert missing
or bound-only information into an exact score.

Root selection requires six informative observations, confidence above 0.30,
coverage above 70%, sufficient clock, and a nonforcing, nonmate position. Safe
sacrifice limits are 0 cp in tactical positions, 8 cp while winning, 15 cp in
balanced positions and 25 cp while losing. Model influence is capped at 20%,
and the final bonus at 12 cp. Missing replies and opponent lower bounds receive
the objective worst-case value. Root invariants are asserted and unit tested.

## Docker commands

Use the existing image `chessathon-scope:test`; the runner mounts the current
workspace read-only. Each test uses one CPU, 2 GB RAM, a 128-process limit,
256 MB writable `/tmp`, and no network. Complete output goes to host temporary
files. No dependencies were added and no harness files were changed.

```powershell
.\docker-test.ps1 -LogName passive-gate make gate
.\docker-test.ps1 -LogName passive-units python tests/passive_units.py
.\docker-test.ps1 -LogName passive-legal python tests/verify.py
.\docker-test.ps1 -LogName passive-regression python tests/passive_regression.py
.\docker-test.ps1 -LogName passive-calibration python tests/calibrate.py
.\docker-test.ps1 -LogName passive-benchmark python tests/benchmark.py
.\docker-test.ps1 -LogName passive-smoke20 python tests/passive_arena.py --smoke 20
.\docker-test.ps1 -LogName passive-screen60 python tests/passive_arena.py --cases 60
.\docker-test.ps1 -LogName passive-full-clock python tests/passive_arena.py --cases 1 --base-ms 120000 --increment-ms 500
```

Sixty matched cases mean 180 games: one per configuration in each case. All three
share the opening, seed and colour. Cases cover both colours and six opponents:
the repository's random, greedy and minimax agents, plus new standard-chess
forcing, deeper minimax and switching opponents. The deeper opponent iterates
to depth three within its clock; it may finish at a shallower depth. Switching
changes policy every eight opponent moves. Fast matches use 5 s + 100 ms;
the separate full-clock comparison uses 120 s + 500 ms.

Every game uses a fresh official runner process. State persists across moves in
that process. Separate lifecycle tests verify ages 1 and 2 within each process,
then age 1 again after restarting. Unit-level search tests supplement these
interface and referee tests; they do not replace them.

## Results

The smoke test completed 20 games without failures. The screen completed 180
more, and the full-clock check completed three. There were no crashes, illegal
moves, initialization failures or flags.

| Configuration | Wins | Draws | Losses | Score | Median modelling | P95 modelling |
|---|---:|---:|---:|---:|---:|---:|
| Frozen baseline | 56 | 3 | 1 | 95.83% | 0% at fast clock | 0% |
| Passive | 57 | 2 | 1 | 96.67% | 0.304% | 0.888% |
| Adaptive enabled | 57 | 1 | 2 | 95.83% | 0.311% | 0.811% |

Paired differences use 4,000 bootstrap samples of the 30 matched colour-pair
clusters. They apply to this opponent/opening sample, not the competition ladder.
The summarizer uses an exact zero-discordance bound if every cluster difference
is zero, avoiding a misleading zero-width interval in that case.

| Paired contrast | Difference | 95% interval |
|---|---:|---:|
| Passive minus baseline | +0.83 percentage points | 0.00 to +2.50 |
| Adaptive minus baseline | 0.00 percentage points | -2.50 to +2.50 |
| Adaptive minus passive | -0.83 percentage points | -3.33 to +1.67 |

There were **zero adaptive move overrides**. Differences in game scores therefore
do not establish an exploitation gain; clock-dependent completed depths can
change play. The improvement gate failed, so no larger paired arena was run and
`ADAPTIVE` remains false.

On 24 identical positions, fixed-depth moves and objective node counts matched
exactly. At the fast wall clock, one passive query completed one fewer ply;
mean paired depth change was -0.0417 ply, within the predeclared 0.05-ply tolerance.
Median query time was 150.37 ms for baseline and 150.24 ms for passive.

In the full-clock games, mean depth was identical. Passive modelling cost was
0.052% median and 0.095% at P95, versus 1.171% and 1.177% for the frozen profiler.
Median external move time fell from 2.915 s to 2.881 s. This three-game timing
check is not evidence of a strength improvement.

Selected-move reply coverage averaged 37.5% in the fast screen and 91.7% in the
full-clock check. Depth-one completions cannot supply a complete opponent-reply
search. In 22 held-out calibration positions with evidence, coverage was 100%,
but only 10.9% of scores were exact. Material and forcing posterior mass reached
97.9% and 87.4%, respectively. Random, shallow and deeper policies were not
reliably separated; switching retained 36.1% unknown mass. These are diagnostic
posterior results, not classification accuracy estimates.

Bayesian API/immutability, decay, change detection, missing/bound evidence,
93 adaptive safe-set cases, interrupted-iteration discard, pre-search observation
ordering, legal moves, special moves, draw claims, and process reset tests pass.
The 500-position legal/clock test and the official Ruff/Mypy/referee gate pass.

## Next implementation

Improve policy identifiability using additional shallow information already
computed by objective search, with explicit provenance and bound handling.
Raise useful evidence coverage without adding reply searches or altering search
ordering, then repeat paired tests at realistic clocks. Do not enable adaptation
without actual safe overrides and a repeatable paired improvement.

Packaging and archive tests remain deferred. No archive was created or rebuilt.
