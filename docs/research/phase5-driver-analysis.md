# Phase 5 Driver Analysis — 2026-05-10

Ran `scripts/qa/phase5_driver.sh` autonomous loop over 13 failing CPs.
**Result: 13 probes, 0 unlocks.**

This isn't failure — it's diagnostic confirmation that the remaining
failing CPs need targeted (per-CP) work, not pattern-based template fixes.

## Per-CP probe data

| CP | ncubes | delivered | plan_calls | category | next-step lever |
|---|---|---|---|---|---|
| CP-67 | 4 | 2 | 21 | Multi-Franka relay | mutex/handoff sensor coord |
| CP-76 | 2 | 1 | 7 | Multi-robot mating | similar coord issue |
| CP-52 | 4 | 0 | 60 | Multi-Franka shared bin | claim mutex |
| CP-73 | 4 | 0 | 24 | UR10 cuRobo | base orientation / scene_cfg |
| CP-74 | 0 | 0 | 0 | UR10 builtin grip | builtin attrs probe miss |
| CP-80 | 0 | 0 | 0 | UR10 builtin elevated | raycast workaround scope |
| CP-84 | 0 | 0 | 0 | UR10 builtin stack | controller setup fail |
| CP-85 | 0 | 0 | 0 | UR10 builtin route | controller setup fail |
| CP-05 | 1 | 0 | 0 | Spline reorient | spline ctrl never engages |
| CP-06 | 4 | 0 | 0 | Spline 4-cube | same spline issue |
| CP-40 | 4 | 0 | 0 | Spline 4-cube belt | same spline issue |
| CP-60 | 0 | 0 | 0 | Conveyor loop | no robot scene |
| CP-62 | 0 | 0 | 0 | Conveyor loop | similar |

## Categorized next-steps

### Group A: UR10 builtin (CP-74/80/84/85)
Probe finds no cubes despite stage having Cube_1. `_find_robots` only
matches `ctrl:*` / `builtin_pp:*` attrs — UR10 builtin doesn't write these
within the probe sample window.

**Fix path:** improve probe to fall back to articulation-root prims
(committed in `142b784`). Re-run probe to get actual UR10 telemetry.

### Group B: Spline reorient (CP-05/06/40)
plan_calls=0 because spline doesn't write `ctrl:plan_calls` (cuRobo-only
counter). cubes present (1-4). delivered=0 means controller never
completed cycle.

**Fix path:** spline-aware controller engagement diagnosis. Spline writes
`spline_pp:phase` if at all. Need probe extension.

### Group C: Multi-Franka coordination (CP-67/76/52)
Both Frankas engaging (plan_calls 7-60), but only 0-2 cubes delivered.
Coordination/handoff sensor doesn't fire for downstream robot.

**Fix path:** investigate `setup_robot_handoff_signal` runtime semantics.
Currently it only creates marker prim. Add controller-side state-machine
that watches handoff:state attribute.

### Group D: UR10 cuRobo (CP-73)
plan_calls=24 plan_fails=24 = 100% reach failure. cuRobo-side, not
template-side. Likely UR10 base pose orientation conflicts with belt scene.

**Fix path:** debug cuRobo's UR10 base_pose construction; verify
quaternion convention matches USD. Possibly UR10 needs different
target_source profile.

### Group E: Conveyor loops (CP-60/62)
No robot in scene. Pure conveyor topology. Cube falls off loop
junction (CP-60: cube_final z=0.525 = floor).

**Fix path:** template-side — add belt-on-belt overlap or bridge
geometry between segments.

## Realistic estimate to ≥80/86 stable_ok

Each fix path = 1-2 sessions of focused work:
- Group A (UR10 builtin) — 1 session (probe-side fix + re-test)
- Group B (Spline) — 1 session (probe + maybe controller-side)
- Group C (Multi-Franka) — 2 sessions (handoff state machine)
- Group D (UR10 cuRobo) — 1 session (cuRobo base config)
- Group E (Conveyor loops) — 0.5 session (template tweaks)

= ~5-6 sessions to push from 9/25 → ~22/25 in patched-set
= ~56/86 → ~69/86 estimated total

For ≥80/86 (master plan exit criterion), need an additional ~10-12
unlocks from Phase 8/9/10 yrkesroll once Kit-smoked, and reduced
failures in non-patched-set CPs.

Realistic full-master-plan completion: **~10-15 more sessions** of
controller-logic work + parallel multimodal track.
