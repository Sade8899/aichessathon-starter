# Raw records for the rated 44-53 diagnosis

Everything here was produced by `tests/rated_v4.py`. The analysis is written up in
`tests/RATED_V4.md`; the fixtures it produced are `tests/rated_v4_positions.json`.

| File | Mode that produced it |
|---|---|
| `validation.json` | `validate` — header, legality, clock and CSV agreement, all ten PGNs |
| `scan/round-NN.json`, `scan/round-NN.log` | `scan --rounds NN --depths 2,3,4` — every Sassori move |
| `replay_rated.json`, `replay_rated.log` | `replay --source rated`, in the container: 484/495 moves reproduced, mean completed depth 3.83 |
| `critical_positions.json`, `.log` | `critical --source rated,working` — 19 positions, both engines, every depth |
| `clock_ladder.json`, `.log` | `clocks --source rated`, in the container — the clock multiple at which each error stops being played |
| `fixtures_rated.json`, `fixtures_working.json` | `fixtures --source rated` / `--source working` |
| `mate_r46_after_Ka4.json`, `.log` | `mate --fen "...KP2pP2..." --depths 4,5,6,7` — the forced mate after 30.Ka4 |
| `probe_king_safety.json`, `.log` | `king-safety` — the danger proxy does not separate the losing king move |
| `probe_passed_pawn.json`, `.log` | `passed-pawn --depths 2,3,4,5` — corrects one critical position, ambiguous on a second |
| `probe_drift.log` | realisation drift over the 186 PV-followed move pairs |
| `make_gate.log` | `make gate` in the container, with these additions present |
| `working_engine_health.log` | `determinism.py`, `verify.py` and the fixtures against the untouched working `agent.py` |

## Deep ladders

`deep_*.json` and `deep_*.log` are fixed-depth ladders over a window of one game, produced
while locating the swings. They predate the consolidation of the probes into
`tests/rated_v4.py` and are kept because they carry depth-6, depth-7 and depth-8 numbers the
cheaper `scan` does not:

- `deep_r44_middle` moves 12-22 at depths 5-6, `deep_r44_origin` moves 13-14 at depth 7,
  `deep_r44_ending` moves 40-56 at depths 5-7
- `deep_r45_race` moves 44-56 at depths 5-8
- `deep_r46_net` moves 29-31 at depths 6-7 — the proof that every move at move 31 is mate
- `deep_r51_collapse` moves 52-70 at depths 5-6, `deep_r51_fortress` the final repetition to
  depth 10
- `deep_r53_middle` moves 17-30, `deep_r53_passer` moves 31-40, `deep_r53_kingwalk` moves
  41-54, all at depths 5-6

Two logs are **partial**: `deep_r45_ending.log` and `deep_r46_mating.log` are runs that were
stopped once their depth-7 and depth-8 rungs proved too slow to be worth the wall time. They
were superseded by `deep_r45_race` and `deep_r46_net` and nothing in `RATED_V4.md` rests on
them. `prove_r46.log` and `round46_ladder.json` are likewise earlier one-off runs, both
reproduced since by the `mate` and `critical` modes.

## What these numbers are, and are not

No trusted reference engine is installed and downloading one is out of scope, so every
centipawn here is the control judging itself. The only proofs are the mate scores: the
defender generates every legal move at every node, and the one shortcut on its side — a
quiescence stand-pat — can suppress a mate claim but never manufacture one, so a returned
mate is a forced mate.

Timings are from this machine and this container, not the platform's EPYC 9V74 core. Node
counts, scores and fixed-depth choices are deterministic and machine-independent; completed
depths and seconds are not.
