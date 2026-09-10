## identities

control agent.py sha256 65ec40ceb29a8f6fe14a74ab2ed6ca446164f701e46d2b9d3fe8d5af94655bda
control agent.py bytes  39396
control agent.py lines  1046
pre-neural package sha256 d9392c6b9c572790c838cc91e957c6eeaceecb706175d99cdfbf928a5e3b86a5
branch: experiment/nnue-v3-final
commit: 6ffe714330f88e55b1af66c084675638749ab8fb
control tag commit: 78c03b0d266a027c5e47ffa3a1b5af0c435da1b6

## control-only calibrations

| clock ms | budget ms | mean depth | median | nodes |
| ---: | ---: | ---: | ---: | ---: |
| 1,000 | 31.2 | 0.8 | 1.0 | 398 |
| 2,000 | 62.5 | 1.3 | 1.0 | 767 |
| 4,000 | 125.0 | 2.15 | 2.0 | 2,212 |
| 8,000 | 250.0 | 2.8 | 3.0 | 4,266 |
| 16,000 | 500.0 | 3.35 | 3.0 | 9,015 |
| 30,000 | 937.5 | 3.9 | 4.0 | 16,428 |
| 60,000 | 1875.0 | 4.45 | 4.0 | 35,099 |
| 120,000 | 3750.0 | 4.85 | 5.0 | 74,945 |

worker calibration at 8000 ms + 500 ms, 8 games each

| workers | games/h | mean depth | vs 1 worker | flags |
| ---: | ---: | ---: | ---: | ---: |
| 1 | 102 | 3.128 | +0.00% | 0 |
| 2 | 210 | 3.137 | +0.28% | 0 |
| 4 | 401 | 3.009 | -3.79% | 0 |
| 6 | 614 | 2.482 | -20.66% | 0 |

root-tie headroom for the ORDER mode:
  positions_scored: 200
  clock_ms: 8000
  mean_root_tie_size: 1.17
  tied_root_rate: 0.115
  tie_holds_better_move_rate: 0.045
  mean_recoverable_cp_per_position: 0.77
  mean_played_regret_cp: 38.52
  played_equals_control_static_best_rate: 0.305

## data

V2 sibling groups: 28,640 written of 40,000 planned, 11,360 dropped for no ordering signal, 32,413 groups/h on 8 workers
V3 targeted groups: 11,328 written of 14,000 planned, 2,672 dropped for no ordering signal, 23,676 groups/h on 6 workers

split overlap check:
  groups: 39968
  directories: ['tests\\results\\nnue\\groups', 'tests\\results\\nnue\\v3\\groups']
  parents_per_split: {'train': 28117, 'validation': 6175, 'test': 5676}
  children_per_split: {'train': 220455, 'validation': 47955, 'test': 44989}
  parent_key_overlap: {'train|validation': 0, 'train|test': 0, 'validation|test': 0}
  child_key_overlap: {'train|validation': 3, 'train|test': 18, 'validation|test': 1}
  parent_child_cross_split_overlap: {'train_parents|validation_children': 10, 'train_parents|test_children': 6, 'validation_parents|train_children': 6, 'validation_parents|test_children': 3, 'test_parents|train_children': 10, 'test_parents|validation_children': 2}
  games_straddling_a_split: 0
  games_straddling_examples: []
  families_straddling_a_split: {'shallowblue': ['test', 'train', 'validation'], 'rustic': ['test', 'train', 'validation']}
  loki_family_groups: 0
  rated_v5_ball_hits: 0
  rated_v5_ball_examples: []
  mirror_sample_size: 4441
  mirror_cross_split_conflicts: 0
  leaked_keys: 53
  quarantined_groups: 55
  groups_after_quarantine: 39913
  parent_key_overlap_after_quarantine: {'train|validation': 0, 'train|test': 0, 'validation|test': 0}
  child_key_overlap_after_quarantine: {'train|validation': 0, 'train|test': 0, 'validation|test': 0}
  parent_child_cross_overlap_after_quarantine: {'train_parents|validation_children': 0, 'train_parents|test_children': 0, 'validation_parents|train_children': 0, 'validation_parents|test_children': 0, 'test_parents|train_children': 0, 'test_parents|validation_children': 0}
  clean_after_quarantine: True
  clean: False

## every checkpoint trained

| checkpoint | form | ep | composite | preserved | top-move gain | regret cp | p95 harm | p99 harm | zero flips | draw pres | fire | \|corr\| | eligible |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | :-: |
| `P_h32_s20260910_rel040` | relative | 13 | 3.822 | 0.9891 | +0.0047 | +4.91 | 0 | 0 | 0.0000 | 0.9963 | 0.295 | 3.68 | yes |
| `P_h16_s20260910_rel015` | relative | 12 | 3.238 | 0.9848 | +0.0029 | +5.44 | 0 | 6 | 0.0000 | 0.9963 | 0.656 | 12.72 | yes |
| `P_h16_s20260910_rel005` | relative | 12 | 3.149 | 0.9836 | +0.0052 | +6.51 | 0 | 15 | 0.0000 | 0.9814 | 0.856 | 24.30 | yes |
| `P_h16_s20260910_f005` | relative | 10 | 2.868 | 0.9876 | +0.0047 | +4.75 | 0 | 1 | 0.0000 | 0.9816 | 0.915 | 34.80 | yes |
| `P_h32_s20260910_f015` | relative | 10 | 2.847 | 0.9829 | +0.0094 | +6.44 | 0 | 10 | 0.0000 | 0.9484 | 0.841 | 22.61 | yes |
| `P_h32_s20260913_f040` | relative | 14 | 2.593 | 0.9885 | +0.0026 | +4.03 | 0 | 0 | 0.0000 | 0.9907 | 0.468 | 6.61 | yes |
| `P_h16_s20260910_f015` | relative | 15 | 2.426 | 0.9876 | +0.0036 | +4.23 | 0 | 4 | 0.0000 | 0.9916 | 0.615 | 17.98 | yes |
| `P_h16_s20260910_f002` | relative | 9 | 2.229 | 0.9862 | +0.0042 | +4.10 | 0 | 0 | 0.0000 | 0.9878 | 0.929 | 37.96 | yes |
| `RAP_h16_s20260910_rel005` | relative | 12 | 1.570 | 0.9727 | +0.0079 | +8.77 | 0 | 43 | 0.0000 | 0.9915 | 0.988 | 87.53 | yes |
| `RA_h16_s20260910_rel005` | relative | 8 | 1.566 | 0.9770 | +0.0077 | +7.21 | 0 | 32 | 0.0000 | 0.9986 | 0.990 | 87.77 | yes |
| `P_h32_s20260910_rel100` | relative | 1 | 1.439 | 0.9939 | +0.0009 | +2.18 | 0 | 0 | 0.0000 | 0.9975 | 0.282 | 0.66 | yes |
| `P_h32_s20260910_rel015` | relative | 13 | 1.386 | 0.9745 | +0.0086 | +8.25 | 0 | 37 | 0.0000 | 0.9440 | 0.774 | 16.85 | yes |
| `RAP_h32_s20260910_rel015` | relative | 1 | 1.179 | 0.9982 | +0.0018 | +2.21 | 0 | 0 | 0.0000 | 1.0000 | 0.992 | 100.57 | yes |
| `P_h16_s20260910_rel040` | relative | 11 | 1.176 | 0.9921 | +0.0016 | +2.01 | 0 | 0 | 0.0000 | 1.0000 | 0.163 | 2.07 | yes |
| `RAP_h16_s20260910_rel015` | relative | 7 | 1.115 | 0.9703 | +0.0084 | +8.34 | 0 | 43 | 0.0000 | 0.9969 | 0.988 | 80.02 | yes |
| `RAP_h16_s20260910_f002` | relative | 12 | 1.111 | 0.9839 | +0.0066 | +4.21 | 0 | 9 | 0.0000 | 0.9929 | 0.973 | 78.55 | yes |
| `RAP_h48_s20260910_f48` | relative | 0 | 1.105 | 1.0000 | +0.0023 | +1.71 | 0 | 0 | 0.0000 | 1.0000 | 0.975 | 84.45 | yes |
| `RA_h16_s20260910_f002` | relative | 9 | 1.067 | 0.9806 | +0.0060 | +5.23 | 0 | 18 | 0.0000 | 0.9939 | 0.973 | 77.39 | yes |
| `RAP_h32_s20260910_f002` | relative | 1 | 1.052 | 1.0000 | +0.0028 | +1.63 | 0 | 0 | 0.0000 | 1.0000 | 0.975 | 84.41 | yes |
| `RA_h32_s20260910_f005` | relative | 1 | 1.052 | 1.0000 | +0.0028 | +1.63 | 0 | 0 | 0.0000 | 1.0000 | 0.975 | 84.41 | yes |
| `RA_h16_s20260910_f015` | relative | 13 | 1.006 | 0.9700 | +0.0113 | +8.65 | 0 | 44 | 0.0000 | 0.9708 | 0.964 | 62.94 | yes |
| `RAP_h32_s20260910_f005` | relative | 1 | 0.996 | 0.9995 | +0.0028 | +1.63 | 0 | 0 | 0.0000 | 1.0000 | 0.975 | 84.41 | yes |
| `RA_h32_s20260910_f002` | relative | 1 | 0.908 | 1.0000 | +0.0023 | +1.51 | 0 | 0 | 0.0000 | 1.0000 | 0.975 | 84.41 | yes |
| `RA_h32_s20260910_rel015` | relative | 1 | 0.899 | 0.9976 | +0.0016 | +2.02 | 0 | 0 | 0.0000 | 1.0000 | 0.992 | 100.55 | yes |
| `RAP_h16_s20260910_rel040` | relative | 1 | 0.898 | 0.9891 | +0.0034 | +2.50 | 0 | 0 | 0.0000 | 1.0000 | 0.988 | 67.90 | yes |
| `P_h32_s20260910_f005` | relative | 7 | 0.862 | 0.9848 | +0.0040 | +3.96 | 0 | 4 | 0.0000 | 0.9573 | 0.915 | 36.14 | yes |
| `RAP_h16_s20260910_f005` | relative | 5 | 0.819 | 0.9862 | +0.0045 | +3.59 | 0 | 9 | 0.0000 | 1.0000 | 0.974 | 78.85 | yes |
| `RA_h32_s20260910_rel005` | relative | 1 | 0.774 | 0.9976 | +0.0014 | +1.91 | 0 | 0 | 0.0000 | 1.0000 | 0.992 | 100.59 | yes |
| `RA_h32_s20260910_f015` | relative | 1 | 0.751 | 0.9995 | +0.0026 | +1.39 | 0 | 0 | 0.0000 | 1.0000 | 0.975 | 84.41 | yes |
| `P_h16_s20260910_q150` | additive | 9 | 0.704 | 0.9994 | -0.0002 | +0.88 | 0 | 0 | 0.0000 | 0.9963 | 0.036 | 0.04 | yes |
| `RAP_h32_s20260910_f015` | relative | 1 | 0.681 | 0.9991 | +0.0028 | +1.37 | 0 | 0 | 0.0000 | 1.0000 | 0.975 | 84.40 | yes |
| `P_h16_s20260910_rel100` | relative | 0 | 0.545 | 0.9939 | +0.0007 | +1.31 | 0 | 0 | 0.0000 | 0.9969 | 0.140 | 0.29 | yes |
| `P_h32_s20260910_f002` | relative | 5 | 0.508 | 0.9843 | +0.0034 | +4.10 | 0 | 4 | 0.0000 | 0.9426 | 0.922 | 38.08 | yes |
| `RA_h16_s20260910_rel015` | relative | 2 | 0.472 | 0.9867 | +0.0018 | +3.13 | 0 | 6 | 0.0000 | 1.0000 | 0.991 | 81.80 | yes |
| `RA_h16_s20260910_f005` | relative | 2 | 0.467 | 0.9954 | +0.0015 | +1.57 | 0 | 0 | 0.0000 | 1.0000 | 0.975 | 77.52 | yes |
| `RA_h16_s20260910_rel100` | relative | 0 | 0.434 | 0.9891 | +0.0014 | +2.32 | 0 | 4 | 0.0000 | 0.9926 | 0.895 | 28.49 | yes |
| `RAP_h32_s20260910_rel005` | relative | 1 | 0.376 | 0.9976 | +0.0007 | +1.55 | 0 | 0 | 0.0000 | 1.0000 | 0.992 | 100.60 | yes |
| `RAP_h16_s20260910_f015` | relative | 1 | 0.324 | 0.9940 | +0.0021 | +1.52 | 0 | 0 | 0.0000 | 1.0000 | 0.975 | 75.20 | yes |
| `P_h32_s20260910_rel005` | relative | 4 | 0.245 | 0.9812 | +0.0036 | +5.90 | 0 | 25 | 0.0000 | 0.9405 | 0.930 | 35.13 | yes |
| `P_h16_s20260910_q080` | additive | 11 | 0.115 | 0.9958 | -0.0002 | +1.39 | 0 | 0 | 0.0002 | 0.9703 | 0.362 | 0.42 | yes |
| `RA_h16_s20260910_rel040` | relative | 0 | 0.082 | 0.9873 | +0.0043 | +2.54 | 0 | 8 | 0.0000 | 1.0000 | 0.990 | 73.79 | yes |
| `RAP_h16_s20260910_t92` | additive | 0 | -0.000 | 1.0000 | +0.0000 | +0.00 | 0 | 0 | 0.0000 | 1.0000 | 0.000 | 0.00 | yes |
| `RAP_h32_s20260910_t97` | additive | 0 | -0.000 | 1.0000 | +0.0000 | +0.00 | 0 | 0 | 0.0000 | 1.0000 | 0.000 | 0.00 | yes |
| `RAP_h32_s20260910_rel040` | relative | 0 | -0.189 | 0.9939 | +0.0009 | +1.36 | 0 | 0 | 0.0000 | 1.0000 | 0.992 | 97.36 | yes |
| `P_h16_s20260910_matrix` | additive | 5 | -0.214 | 0.9958 | +0.0000 | +1.76 | 0 | 0 | 0.0000 | 0.9415 | 0.165 | 0.26 | yes |
| `RAP_h16_s20260910_rel100` | relative | 6 | -0.233 | 0.9715 | +0.0072 | +7.47 | 0 | 42 | 0.0000 | 0.9471 | 0.884 | 24.11 | yes |
| `RAP_h32_s20260910_relsmoke` | relative | 0 | -0.790 | 0.9745 | +0.0063 | +5.21 | 0 | 37 | 0.0000 | 0.9996 | 0.977 | 54.57 | yes |
| `RAP_h32_s20260910_rel100` | relative | 1 | -0.996 | 0.9745 | +0.0041 | +6.32 | 0 | 37 | 0.0000 | 0.9436 | 0.912 | 31.78 | yes |
| `P_h48_s20260910_f48` | relative | 1 | -1.051 | 0.9843 | +0.0015 | +1.45 | 0 | 5 | 0.0000 | 0.9970 | 0.942 | 40.29 | yes |
| `RA_h32_s20260910_rel100` | relative | 1 | -1.255 | 0.9733 | +0.0047 | +6.25 | 0 | 37 | 0.0000 | 0.9393 | 0.907 | 29.39 | yes |
| `RA_h32_s20260910_rel040` | relative | 1 | -1.942 | 0.9703 | +0.0027 | +5.44 | 0 | 43 | 0.0000 | 0.9917 | 0.959 | 51.56 | yes |
| `L_h16_s20260910_matrix` | additive | 2 | -3.551 | 0.9879 | +0.0002 | +2.43 | 0 | 1 | 0.0008 | 0.8226 | 0.501 | 1.29 | yes |
| `R_h16_s20260910_matrix` | additive | 1 | -4.191 | 0.9885 | +0.0005 | +3.04 | 0 | 1 | 0.0021 | 0.7715 | 0.684 | 1.78 | yes |
| `RAP_h16_s20260910_t85` | additive | 0 | -5.768 | 0.9867 | +0.0027 | +3.17 | 0 | 16 | 0.0058 | 0.7602 | 0.810 | 4.34 | yes |
| `RAP_h16_s20260910_q150` | additive | 1 | -5.917 | 0.9885 | -0.0020 | +2.02 | 0 | 8 | 0.0016 | 0.7703 | 0.746 | 2.13 | yes |
| `RAP_h16_s20260910_matrix` | additive | 0 | -6.327 | 0.9903 | +0.0025 | +3.93 | 0 | 15 | 0.0066 | 0.6887 | 0.862 | 4.27 | yes |
| `RA_h16_s20260910_matrix` | additive | 0 | -6.327 | 0.9867 | +0.0018 | +3.97 | 0 | 9 | 0.0043 | 0.6833 | 0.829 | 3.52 | yes |
| `RAP_h16_s20260910_q010` | additive | 2 | -6.533 | 0.9758 | +0.0066 | +9.06 | 0 | 37 | 0.0161 | 0.6201 | 0.938 | 10.42 | yes |
| `RAP_h16_s20260910_q080` | additive | 0 | -6.927 | 0.9885 | -0.0002 | +3.33 | 0 | 15 | 0.0062 | 0.7027 | 0.850 | 3.95 | yes |
| `P_h32_s20260910_matrix` | additive | 11 | -7.386 | 0.9824 | +0.0005 | +3.57 | 0 | 19 | 0.0064 | 0.7153 | 0.802 | 3.37 | yes |
| `L_h48_s20260910_matrix` | additive | 6 | -8.886 | 0.9667 | +0.0111 | +12.60 | 0 | 80 | 0.0315 | 0.5782 | 0.966 | 24.97 | no |
| `P_h48_s20260910_matrix` | additive | 11 | -9.421 | 0.9782 | -0.0020 | +1.77 | 0 | 24 | 0.0049 | 0.7461 | 0.857 | 4.66 | yes |
| `RAP_h32_s20260910_t92` | additive | 0 | -11.018 | 0.9600 | +0.0066 | +8.41 | 0 | 90 | 0.0186 | 0.7149 | 0.818 | 12.35 | no |
| `RAP_h48_s20260910_matrix` | additive | 1 | -11.033 | 0.9558 | +0.0093 | +14.56 | 0 | 93 | 0.0379 | 0.5243 | 0.968 | 22.73 | no |
| `R_h32_s20260910_matrix` | additive | 0 | -11.773 | 0.9661 | +0.0070 | +8.86 | 0 | 52 | 0.0181 | 0.5152 | 0.945 | 11.63 | no |
| `RAP_h32_s20260910_q010` | additive | 0 | -13.408 | 0.9606 | +0.0075 | +9.29 | 0 | 77 | 0.0237 | 0.5464 | 0.954 | 15.20 | no |
| `RA_h32_s20260910_matrix` | additive | 0 | -13.872 | 0.9642 | +0.0070 | +8.57 | 0 | 68 | 0.0241 | 0.5125 | 0.953 | 13.33 | no |
| `RAP_h32_s20260910_t85` | additive | 0 | -14.561 | 0.9606 | +0.0063 | +8.89 | 0 | 89 | 0.0235 | 0.5581 | 0.955 | 14.69 | no |
| `R_h48_s20260910_matrix` | additive | 1 | -15.414 | 0.9552 | +0.0057 | +11.11 | 0 | 101 | 0.0353 | 0.5203 | 0.970 | 21.49 | no |
| `RAP_h32_s20260910_matrix` | additive | 0 | -15.851 | 0.9612 | +0.0054 | +7.79 | 0 | 89 | 0.0206 | 0.5455 | 0.950 | 12.82 | no |
| `RAP_h32_s20260910_smoke` | additive | 0 | -15.851 | 0.9612 | +0.0054 | +7.79 | 0 | 89 | 0.0206 | 0.5455 | 0.950 | 12.82 | no |
| `RA_h48_s20260910_matrix` | additive | 0 | -15.996 | 0.9515 | +0.0061 | +10.21 | 0 | 99 | 0.0348 | 0.5404 | 0.965 | 18.83 | no |
| `L_h32_s20260910_matrix` | additive | 0 | -17.135 | 0.9642 | +0.0029 | +5.83 | 0 | 76 | 0.0177 | 0.5166 | 0.938 | 10.31 | no |

73 checkpoints trained

## search-level probes (played move, real clock)

### f040_eval at 8000 ms on the validation split

  paired_positions: 221
  moves_changed: 21
  moves_changed_rate: 0.095
  paired_mean_regret_delta_cp: -0.348
  paired_improved: 9
  paired_worsened: 9
  paired_worst_regression_cp: 80.0
  paired_best_improvement_cp: -156.0

  control: mean regret 32.916 cp, top-move rate 0.5044, depth 3.332, nodes 4,473
  candidate_metrics: mean regret 31.753 cp, top-move rate 0.5202, depth 3.278, nodes 4,180

### f040_order at 8000 ms on the validation split

  paired_positions: 226
  moves_changed: 6
  moves_changed_rate: 0.0265
  paired_mean_regret_delta_cp: -0.363
  paired_improved: 5
  paired_worsened: 1
  paired_worst_regression_cp: 28.0
  paired_best_improvement_cp: -37.0

  control: mean regret 33.159 cp, top-move rate 0.4956, depth 3.332, nodes 4,433
  candidate_metrics: mean regret 33.084 cp, top-move rate 0.5022, depth 3.361, nodes 4,406

### smoke_rel005 at 4000 ms on the validation split

  paired_positions: 53
  moves_changed: 11
  moves_changed_rate: 0.2075
  paired_mean_regret_delta_cp: -5.113
  paired_improved: 5
  paired_worsened: 6
  paired_worst_regression_cp: 137.0
  paired_best_improvement_cp: -309.0

  control: mean regret 55.926 cp, top-move rate 0.463, depth 2.315, nodes 1,112
  candidate_metrics: mean regret 50.164 cp, top-move rate 0.5091, depth 2.127, nodes 1,075


## gates

### `P_h32_s20260911_f040` mode=eval

| gate | measured | requirement | result |
| --- | --- | --- | :-: |
| protected control agent.py unchanged | 65ec40ceb29a8f6fe14a74ab2ed6ca446164f701e46d2b9d3fe8d5af94655bda | 65ec40ceb29a8f6fe14a74ab2ed6ca446164f701e46d2b9d3fe8d5af94655bda | PASS |
| protected pre-neural package unchanged | d9392c6b9c572790c838cc91e957c6eeaceecb706175d99cdfbf928a5e3b86a5 | d9392c6b9c572790c838cc91e957c6eeaceecb706175d99cdfbf928a5e3b86a5 | PASS |
| candidate strips back to the control exactly | identical | identical | PASS |
| de Bruijn scan resolves every single-bit board | 64/64 | 64/64 | PASS |
| de Bruijn scan enumerates multi-bit boards exactly | 200/200 | 200/200 | PASS |
| agent integer path equals the reference integer path | 1000/1000 exact, max |diff| 0 cp | every position exact | PASS |
| correction never exceeds the clamp | max |correction| 250 cp, clamp 250 | <= 250 | PASS |
| colour-swap feature identity | 1000/1000 | all identical | PASS |
| inference is deterministic across calls | identical | identical | PASS |
| inference depends only on the position, not on board history | identical | identical | PASS |
| always returns a legal move | 40/40 legal | all legal | PASS |
| finds mate in one | 3/3 | 3/3 | PASS |
| paired NPS loss (median of repeated interleaved runs) | 6.33% median of [8.98, -9.11, 6.33]; control 19,928 nps, candidate 18,666 nps | <= 5.0% | FAIL |
| package uncompressed size | 87,999 bytes (agent 60,729 + weights 27,270) | < 50,000,000 | PASS |
| no network or subprocess import in the candidate | none | none | PASS |
| fixture harness loads the candidate's own weights | ready=True status=loaded 638b63ebbfbfb8e0 | ready, with the candidate's weight hash | PASS |
| RATED_V5 enforced fixtures | control 16/16, candidate 16/16 | candidate matches the control's baseline | PASS |
| solved-control regressions (RATED_V5) | 0 [] | 0 | PASS |
| draw and passed-pawn defence fixtures preserved | broken: none | none broken | PASS |
| r80-24-Rd4 still rejected | h2h4 | not the rated move | PASS |
| RATED_V4 enforced fixtures | control 19/19, candidate 16/19 | candidate matches the control's baseline | FAIL |

19/21 pass

FAILING: paired NPS loss (median of repeated interleaved runs), RATED_V4 enforced fixtures

### `P_h32_s20260912_f040` mode=eval

| gate | measured | requirement | result |
| --- | --- | --- | :-: |
| protected control agent.py unchanged | 65ec40ceb29a8f6fe14a74ab2ed6ca446164f701e46d2b9d3fe8d5af94655bda | 65ec40ceb29a8f6fe14a74ab2ed6ca446164f701e46d2b9d3fe8d5af94655bda | PASS |
| protected pre-neural package unchanged | d9392c6b9c572790c838cc91e957c6eeaceecb706175d99cdfbf928a5e3b86a5 | d9392c6b9c572790c838cc91e957c6eeaceecb706175d99cdfbf928a5e3b86a5 | PASS |
| candidate strips back to the control exactly | identical | identical | PASS |
| de Bruijn scan resolves every single-bit board | 64/64 | 64/64 | PASS |
| de Bruijn scan enumerates multi-bit boards exactly | 200/200 | 200/200 | PASS |
| agent integer path equals the reference integer path | 1000/1000 exact, max |diff| 0 cp | every position exact | PASS |
| correction never exceeds the clamp | max |correction| 250 cp, clamp 250 | <= 250 | PASS |
| colour-swap feature identity | 1000/1000 | all identical | PASS |
| inference is deterministic across calls | identical | identical | PASS |
| inference depends only on the position, not on board history | identical | identical | PASS |
| always returns a legal move | 40/40 legal | all legal | PASS |
| finds mate in one | 3/3 | 3/3 | PASS |
| paired NPS loss (median of repeated interleaved runs) | 4.70% median of [-3.41, 4.7, 1.19, 6.08, 8.44, 11.49, 2.48]; control 18,842 nps, candidate 17,933 nps | <= 5.0% | PASS |
| package uncompressed size | 87,999 bytes (agent 60,729 + weights 27,270) | < 50,000,000 | PASS |
| no network or subprocess import in the candidate | none | none | PASS |
| fixture harness loads the candidate's own weights | ready=True status=loaded 08da2c0764de3ad4 | ready, with the candidate's weight hash | PASS |
| RATED_V5 enforced fixtures | control 16/16, candidate 16/16 | candidate matches the control's baseline | PASS |
| solved-control regressions (RATED_V5) | 0 [] | 0 | PASS |
| draw and passed-pawn defence fixtures preserved | broken: none | none broken | PASS |
| r80-24-Rd4 still rejected | g5f4 | not the rated move | PASS |
| RATED_V4 enforced fixtures | control 19/19, candidate 19/19 | candidate matches the control's baseline | PASS |

21/21 pass


## arenas

| run | tag | games | clock | workers | W-D-L | score % | 95% CI % | Elo | Elo CI | draws % | cand depth | ctl depth | flags | illegal |
| --- | --- | ---: | --- | ---: | --- | ---: | --- | ---: | --- | ---: | ---: | ---: | ---: | ---: |
| screen_eval | `P_h32_s20260912_f040` | 300 | 8000+500 | 4 | 122-47-131 | 48.5 | [43.33, 53.67] | -10.4 | [-46.6, 25.5] | 15.67 | 2.53 | 2.56 | 0 | 0 |
| regime8s | `regime` | 300 | 8000+500 | 4 | 104-63-133 | 45.17 | [40.33, 50.17] | -33.7 | [-68.0, 1.2] | 21.0 | 2.72 | 2.66 | 0 | 0 |
| h2h | `F_h32_s20260909_q005` | 2000 | 1000+100 | 6 | 780-378-842 | 48.45 | [46.50, 50.45] | -10.8 | [-24.4, 3.1] | 18.9 | 1.24 | 1.23 | 0 | 0 |
| h2h_null | `F_h32_s20260909_q005` | 1000 | 1000+100 | 6 | 416-175-409 | 50.35 | [47.55, 53.25] | 2.4 | [-17.0, 22.6] | 17.5 | 1.2 | 1.2 | 0 | 0 |
| h2h_pilot | `F_h32_s20260909_q005` | 300 | 1000+100 | 6 | 127-44-129 | 49.67 | [44.50, 54.83] | -2.3 | [-38.4, 33.7] | 14.67 | 1.31 | 1.31 | 0 | 0 |
| regime8s | `F_h32_s20260909_q005` | 300 | 8000+500 | 4 | 104-63-133 | 45.17 | [40.33, 50.17] | -33.7 | [-68.0, 1.2] | 21.0 | 2.72 | 2.66 | 0 | 0 |

### P_h32_s20260912_f040/screen_eval by colour

| candidate colour | draws | games | losses | score_pct | wins |
| --- | ---: | ---: | ---: | ---: | ---: |
| candidate_white | 24 | 150 | 58 | 53.33 | 68 |
| candidate_black | 23 | 150 | 73 | 43.67 | 54 |

### regime/regime8s by colour

| candidate colour | draws | games | losses | score_pct | wins |
| --- | ---: | ---: | ---: | ---: | ---: |
| candidate_white | 37 | 150 | 63 | 45.67 | 50 |
| candidate_black | 26 | 150 | 70 | 44.67 | 54 |

### C_h32_s20260909_smoke/arena_smoke by colour

| candidate colour | candidate | ci95 | control | mean_diff | pairs |
| --- | ---: | ---: | ---: | ---: | ---: |
| white | {'games': 4, 'W-D-L': '1-0-3', 'wins': 1, 'draws': 0, 'losses': 3, 'draw_pct': 0.0, 'score_pct': 25.0, 'elo': -190.8, 'time_losses': 0, 'illegal_moves': 0, 'infrastructure_failures': 0, 'nps_mean': 1167.1, 'mean_depth': 2.54, 'move_ms_p95_mean': 406.0} | [-0.75, 0.625] | {'games': 4, 'W-D-L': '1-1-2', 'wins': 1, 'draws': 1, 'losses': 2, 'draw_pct': 25.0, 'score_pct': 37.5, 'elo': -88.7, 'time_losses': 0, 'illegal_moves': 0, 'infrastructure_failures': 0, 'nps_mean': 1304.6, 'mean_depth': 1.96, 'move_ms_p95_mean': 338.7} | -0.125 | 4 |
| black | {'games': 4, 'W-D-L': '0-0-4', 'wins': 0, 'draws': 0, 'losses': 4, 'draw_pct': 0.0, 'score_pct': 0.0, 'elo': -2400.0, 'time_losses': 0, 'illegal_moves': 0, 'infrastructure_failures': 0, 'nps_mean': 1227.1, 'mean_depth': 2.69, 'move_ms_p95_mean': 384.8} | [0.0, 0.0] | {'games': 4, 'W-D-L': '0-0-4', 'wins': 0, 'draws': 0, 'losses': 4, 'draw_pct': 0.0, 'score_pct': 0.0, 'elo': -2400.0, 'time_losses': 0, 'illegal_moves': 0, 'infrastructure_failures': 0, 'nps_mean': 1267.5, 'mean_depth': 2.02, 'move_ms_p95_mean': 297.0} | 0.0 | 4 |

### F_h32_s20260909_q005/arena_screen by colour

| candidate colour | candidate | ci95 | control | mean_diff | pairs |
| --- | ---: | ---: | ---: | ---: | ---: |
| white | {'games': 50, 'W-D-L': '2-4-44', 'wins': 2, 'draws': 4, 'losses': 44, 'draw_pct': 8.0, 'score_pct': 8.0, 'elo': -424.3, 'time_losses': 0, 'illegal_moves': 0, 'infrastructure_failures': 10, 'nps_mean': 2120.1, 'mean_depth': 2.59, 'move_ms_p95_mean': 364.8} | [-0.11, 0.04] | {'games': 50, 'W-D-L': '4-3-43', 'wins': 4, 'draws': 3, 'losses': 43, 'draw_pct': 6.0, 'score_pct': 11.0, 'elo': -363.2, 'time_losses': 0, 'illegal_moves': 0, 'infrastructure_failures': 10, 'nps_mean': 2171.3, 'mean_depth': 2.52, 'move_ms_p95_mean': 362.6} | -0.03 | 50 |
| black | {'games': 60, 'W-D-L': '1-1-58', 'wins': 1, 'draws': 1, 'losses': 58, 'draw_pct': 1.67, 'score_pct': 2.5, 'elo': -636.4, 'time_losses': 0, 'illegal_moves': 0, 'infrastructure_failures': 0, 'nps_mean': 1784.8, 'mean_depth': 2.57, 'move_ms_p95_mean': 346.5} | [-0.0833, 0.0417] | {'games': 60, 'W-D-L': '2-1-57', 'wins': 2, 'draws': 1, 'losses': 57, 'draw_pct': 1.67, 'score_pct': 4.17, 'elo': -544.7, 'time_losses': 0, 'illegal_moves': 0, 'infrastructure_failures': 0, 'nps_mean': 1868.1, 'mean_depth': 2.41, 'move_ms_p95_mean': 359.6} | -0.0167 | 60 |

### F_h32_s20260909_q005/h2h by colour

| candidate colour | draws | games | losses | score_pct | wins |
| --- | ---: | ---: | ---: | ---: | ---: |
| candidate_white | 183 | 1000 | 605 | 30.35 | 212 |
| candidate_black | 195 | 1000 | 237 | 66.55 | 568 |

### F_h32_s20260909_q005/h2h_null by colour

| candidate colour | draws | games | losses | score_pct | wins |
| --- | ---: | ---: | ---: | ---: | ---: |
| candidate_white | 80 | 500 | 312 | 29.6 | 108 |
| candidate_black | 95 | 500 | 97 | 71.1 | 308 |

### F_h32_s20260909_q005/h2h_pilot by colour

| candidate colour | draws | games | losses | score_pct | wins |
| --- | ---: | ---: | ---: | ---: | ---: |
| candidate_white | 25 | 150 | 89 | 32.33 | 36 |
| candidate_black | 19 | 150 | 40 | 67.0 | 91 |

### F_h32_s20260909_q005/regime8s by colour

| candidate colour | draws | games | losses | score_pct | wins |
| --- | ---: | ---: | ---: | ---: | ---: |
| candidate_white | 37 | 150 | 63 | 45.67 | 50 |
| candidate_black | 26 | 150 | 70 | 44.67 | 54 |

### F_h32_s20260909_q010/arena_screen by colour

| candidate colour | candidate | ci95 | control | mean_diff | pairs |
| --- | ---: | ---: | ---: | ---: | ---: |
| white | {'games': 50, 'W-D-L': '7-2-41', 'wins': 7, 'draws': 2, 'losses': 41, 'draw_pct': 4.0, 'score_pct': 16.0, 'elo': -288.1, 'time_losses': 0, 'illegal_moves': 0, 'infrastructure_failures': 10, 'nps_mean': 1997.4, 'mean_depth': 2.53, 'move_ms_p95_mean': 366.7} | [-0.07, 0.11] | {'games': 50, 'W-D-L': '6-2-42', 'wins': 6, 'draws': 2, 'losses': 42, 'draw_pct': 4.0, 'score_pct': 14.0, 'elo': -315.3, 'time_losses': 0, 'illegal_moves': 0, 'infrastructure_failures': 10, 'nps_mean': 2160.1, 'mean_depth': 2.56, 'move_ms_p95_mean': 363.4} | 0.02 | 50 |
| black | {'games': 60, 'W-D-L': '0-3-57', 'wins': 0, 'draws': 3, 'losses': 57, 'draw_pct': 5.0, 'score_pct': 2.5, 'elo': -636.4, 'time_losses': 0, 'illegal_moves': 0, 'infrastructure_failures': 0, 'nps_mean': 1744.7, 'mean_depth': 2.45, 'move_ms_p95_mean': 358.2} | [-0.0667, 0.0333] | {'games': 60, 'W-D-L': '1-3-56', 'wins': 1, 'draws': 3, 'losses': 56, 'draw_pct': 5.0, 'score_pct': 4.17, 'elo': -544.7, 'time_losses': 0, 'illegal_moves': 0, 'infrastructure_failures': 0, 'nps_mean': 1923.4, 'mean_depth': 2.46, 'move_ms_p95_mean': 357.6} | -0.0167 | 60 |


## platform container

### `P_h32_s20260912_f040` mode=eval

  image: chessathon-nnue:platform (sha256:8fc6496546d3)
  zip: 36,622 bytes, uncompressed 87,999
  agent at root: True, no folders: True
    agent.py  60,729 bytes  989e6811a4e6a8af
    nnue_v2_weights.npz  27,270 bytes  08da2c0764de3ad4
  import_seconds: 4.0
  import_under_90s: True
  has_get_move: True
  nnue_status: loaded 08da2c0764de3ad4
  nnue_ready: True
  nnue_hidden: 32
  nnue_gate: True
  nnue_phase_gate: False
  numba_warm_at_import: [{'function': 'numeric_evaluate', 'compiled_signatures': 1}, {'function': 'nnue_accumulate', 'compiled_signatures': 1}, {'function': 'numeric_evaluate_v2', 'compiled_signatures': 1}]
  fused_kernel_warm: True
  correction_sample: 0
  eval_differs_from_base: False
  correction_probe_values: [0, -1, -46, 0, -16, -8]
  correction_probe_nonzero: 4
  correction_is_live: True
  smoke_white: {'result': '1-0', 'plies': 21, 'legal': True, 'agent_moves': 11, 'move_ms_mean': 2823.6, 'move_ms_max': 3600.6}
  smoke_black: {'result': '0-1', 'plies': 42, 'legal': True, 'agent_moves': 21, 'move_ms_mean': 2597.3, 'move_ms_max': 3600.7}
  all_legal: True
  no_flags: True
  peak_memory_bytes: 216326144
  peak_memory_mb: 206.3
  peak_under_2gb: True
  files_visible: ['agent.py', 'nnue_v2_weights.npz']

