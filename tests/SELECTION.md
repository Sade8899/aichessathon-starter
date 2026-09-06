# Submission selection

All execution uses the local `chessathon-scope:test` image and `docker-test.ps1`.
Matches have one CPU, 2 GB RAM, 128 processes, a read-only workspace, a 256 MB
`/tmp`, and no network. Full output is retained in host temporary logs.

The original frozen baseline remains `tests/checkpoint/agent.py`, SHA-256
`9a77d2a1473ead6349da8a8aa7481125f4fdb2e579d7bb4f2e8dd3ee4ada6c1c`.
The passive checkpoint before this selection is `tests/passive_checkpoint/agent.py`,
SHA-256 `61f7cfc6c831a7958b266389df5449ab686ce5bd2b7264111abdd1b15d4c8ec1`.
Neither snapshot should be updated when the runtime changes.

## Reproduction and independent experiments

The starting gate, Bayesian/safety/lifecycle tests and 500 reachable-position
checks reproduced. All 24 fixed-depth moves and objective node counts matched;
the paired timed depth difference was zero. Canonical contract and rules were
fetched from the two official URLs in AGENTS.md. The older 60-second initialization
remarks in IDEAS.md and the Numba baseline are superseded by the current 90-second
contract. Harness code is unchanged.

Evaluation accounted for about half of profiled search time. The experiments
preserve its exact formula: precompute placement, pawn-front masks and distances,
iterate piece bitboards directly, and cache only static evaluation in a bounded
32,768-slot table. The cache key includes all pieces, ownership and side to move;
rule-dependent search results are never stored in this evaluation cache.

Three fixed-depth benchmark repetitions on 24 positions measured:

| Variant | Nodes/second | Median query, ms |
|---|---:|---:|
| Passive checkpoint | 8,290 | 118.71 |
| Precomputed evaluation | 10,148 | 95.38 |
| Evaluation cache | 9,667 | 101.84 |
| Both | 11,573 | 85.94 |
| Both, profiling disabled | 11,553 | 85.79 |

All moves, completed depths and node counts matched. Evaluations matched on
1,994 colour-paired reachable positions. These are speed results, not playing
strength claims. A separate cold-cache timed repeat is required for final depth
selection; the first timed ablation allowed module caches to persist.

Selective pruning, dynamic clocks and Numba were deferred: the measured first
bottleneck could be reduced without changing minimax values or draw handling.

## Opponent-model experiment

An independent toggle retains a shallow frame from an earlier fully completed
iteration alongside the deepest frame. Aborted iterations do not survive. The
model uses this shallow score interval instead of treating deeper scores as a
shallow proxy. This adds no search and keeps at most three roots with one earlier
frame per root.

In 29 held-out observations, current reply coverage was 100%, earlier-depth
coverage 86.2%, and 9.4% of scores were exact. Successive-depth versus single-depth
log loss was 2.996 versus 2.979 for random, 2.086 versus 2.093 for shallow search,
and 1.877 versus 1.887 for deeper search. Random and switching predictions got
worse, and policy separation remained poor. This experiment did not qualify for
adaptation. Full Brier, calibration-error and posterior diagnostics are in
`select-depth-calibration.log`; no model was trained or downloaded.

## Paired method

Each configuration uses the same opening, colour, opponent and seed in each case.
Execution order rotates across cases to balance host drift. Five standard opening
sequences and six policies cover random, material, forcing, shallow, deeper and
switching play. The first three appropriate policies use repository baselines.
Games run in fresh official runner processes with persistent state during play.

Intervals use 4,000 paired bootstrap samples of opening/opponent clusters, keeping
the colour pair together. An exact zero-discordance bound replaces a degenerate
zero-width interval when every cluster difference is zero. Results apply only to
this portfolio. A stronger sparring option uses the team's passive checkpoint;
no external engine is installed or packaged.

The 20-game smoke screen had no failures. Profiling with the faster search had
1.061% P95 overhead, exceeding the 1% limit. The profiling-disabled configuration
advanced to the 60-case screen. A larger screen is conditional on a positive signal.

The first 60-case paired screen (120 games, seed block 17000) measured 55/5/0
for faster evaluation plus caching without profiling, versus 55/3/2 for the
passive checkpoint. The difference was +1.67 percentage points, 95% interval
[-1.67, +5.00]. There were no failures or low-piece endings. This qualified the
candidate for a repeat with independent seeds, not a confident strength claim.

An additional independent experiment tightens the root window only when
adaptation is disabled. It avoids spending time on the previous 60 cp candidate
band. Every completed best value and chosen-move value matched over 24 positions
and three repetitions. Mean nodes fell from 1,480 to 983; median time fell from
84.09 to 34.56 ms. Special rules and another 500 reachable positions passed. Its
20-game smoke sample scored 19/1/0 without failures. Exact variant copies of both
candidates passed the official gate.

A cold-cache, 24-position timing repeat gave mean depths 1.750 (checkpoint),
2.021 (evaluation/cache), and 2.375 (tight root). Throughput was 8,656, 11,823 and
12,810 nodes/s respectively. Median move time was about 150.3 ms for each. The
second 60-case screen compares all three using seed block 27000.

The experiment implementation is preserved separately in
`tests/experiments/agent.py`, SHA-256
`4551f4e4f2fc09fa56801e52a9ad38f0485ebb32d3c84aa4adbb4166fbb598c0`.
Selection tools derive toggle configurations from that source so cleanup of the
submission cannot silently change an experiment. Subsequent arena logs record
the exact source hash for every configuration.

## Assets

No model, book or Syzygy assets existed in this workspace. There are no certified
book candidates for the five known test openings; eligible book coverage is zero.
Competition starting-position coverage is unknown because that set is unpublished.
No book is proposed. Arena instrumentation counts low-piece material signatures
before deciding whether an endgame asset is justified. Any future downloaded
asset requires source, version, licence and checksum verification.

## Reusable commands

```powershell
.\docker-test.ps1 -LogName select-profile-detail python tests/selection.py profile
.\docker-test.ps1 -LogName select-eval-ablations python tests/selection.py bench
.\docker-test.ps1 -LogName select-candidate-checks python tests/selection_units.py
.\docker-test.ps1 -LogName select-depth-calibration python tests/depth_calibration.py
.\docker-test.ps1 -LogName select-smoke20 python tests/selection.py arena --configs 'reference,combined,objective' --games 20
.\docker-test.ps1 -LogName select-screen60 python tests/selection.py arena --configs 'reference,objective' --cases 60
```

Packaging is authorized only after final configuration selection and revalidation.
The extracted archive test runs the unchanged official gate and two full-clock
games, with only `agent.py` in the archive.
