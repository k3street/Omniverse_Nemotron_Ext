# Controller Shootout Report

Comparison of controller modes (target_source × robot_family) across
available baseline runs from `workspace/baselines/`.

## Summary

| controller / family | n_canonicals | n_runs | stable_ok | stable_fail | flaky | mean cycle (s) | plan_fail rate |
|---|---|---|---|---|---|---|---|
| curobo/franka_panda | 18 | 93 | 18 | 72 | 1 | — | — |
| builtin/ur10 | 4 | 8 | 0 | 8 | 0 | — | — |
| spline/franka_panda | 1 | 3 | 0 | 3 | 0 | — | — |
| curobo/ur10 | 1 | 3 | 0 | 3 | 0 | — | — |
| builtin/franka | 1 | 2 | 0 | 2 | 0 | — | — |

## Per-bucket canonical lists

### curobo/franka_panda (18 CPs)
- CP-05
- CP-22
- CP-35
- CP-37
- CP-46
- CP-48
- CP-51
- CP-52
- CP-53
- CP-57
- CP-58
- CP-59
- CP-60
- CP-62
- CP-65
- CP-67
- CP-68
- CP-76

### builtin/ur10 (4 CPs)
- CP-74
- CP-80
- CP-84
- CP-85

### spline/franka_panda (1 CPs)
- CP-40

### curobo/ur10 (1 CPs)
- CP-73

### builtin/franka (1 CPs)
- CP-06

## Notes

- This is a snapshot. As more controllers are exercised across the
  canonical set, the table fills out and ranking becomes meaningful.
- For Phase 9 (M4 cuMotion-as-MoveIt), the `ros2_cmd` row will appear
  once CP-87 (and any successors) run with the cumotion_moveit pipeline.
- Cycle-time is per-run mean; meaningful only when canonicals deliver
  cubes. For plumbing-only canonicals (CP-NEW-plc-conveyor etc) cycle
  time is N/A.