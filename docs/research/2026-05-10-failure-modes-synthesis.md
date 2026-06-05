# Failure Modes Classification — 2026-05-10

> **NOTE (afternoon update):** This is a snapshot from morning probe runs.
> Post-snapshot fixes applied:
> - CP-37 → stable_ok (via 3D reach check)
> - CP-53 → stable_ok (via cube_paths simulate_args)
> - CP-65 → restored from regression (via cube_paths)
> - CP-67/CP-76 also got cube_paths fix (still failing, sequence-bound)
>
> **Z-OTHER bucket re-analysis:**
> - CP-51, CP-68 — phantom_handoff (Robot A delivered to handoff but
>   Robot B sensor never triggered). New diagnostic detects this.
> - CP-57, CP-58 — probe found 0 cubes because `_find_cubes` only
>   matched `cube*` prefix. Probe now seeds from simulate_args.
> - CP-48, CP-46, CP-35 — controllers running with low plan_fail_rate
>   (0–16%) but 0 cubes delivered. Investigation parked.
>
> **D-NO_PLAN_NO_PICK bucket re-analysis:**
> - CP-40 (spline), CP-80/CP-84/CP-85 (UR10 builtin) — these don't
>   write `ctrl:plan_calls` because they're not cuRobo. Probe instrumentation
>   gap, not a real failure pattern.

Probed 22 failing canonicals (post 3D reach + plan_calls counters).

## Distribution
- **Z-OTHER**: 9 CP(s)
- **D-NO_PLAN_NO_PICK**: 5 CP(s)
- **F-BUILD_FAILED**: 3 CP(s)
- **B-PARTIAL_DELIVERY**: 3 CP(s)
- **D-NO_PLAN_NO_PICK | E-EVENT_CYCLE**: 1 CP(s)
- **A-PLAN_FAILS_HIGH**: 1 CP(s)

## A-PLAN_FAILS_HIGH (1)
### CP-73
  - plan_calls=24 plan_fails=24 rate=1.0
  - delivered=0/4 cycles=0
  - last_phase={'/World/UR10': 'wait_sensor'}
  - plan_fail_rate=1.00 (24/24)
  - error: `RuntimeError: planning failed for /World/Cube_4`
  - error: `RuntimeError: planning failed for /World/Cube_3`

## B-PARTIAL_DELIVERY (3)
### CP-65
  - plan_calls=14 plan_fails=0 rate=0.0
  - delivered=1/4 cycles=1
  - last_phase={'/World/FrankaA': 'wait_sensor', '/World/FrankaB': 'wait_sensor'}
  - delivered=1/4

### CP-67
  - plan_calls=21 plan_fails=2 rate=0.1
  - delivered=2/4 cycles=2
  - last_phase={'/World/FrankaA': 'wait_sensor', '/World/FrankaB': 'settling'}
  - delivered=2/4
  - error: `RuntimeError: planning failed for /World/Cube_4`

### CP-76
  - plan_calls=7 plan_fails=0 rate=0.0
  - delivered=1/2 cycles=1
  - last_phase={'/World/FixtureHolder': 'wait_sensor', '/World/Inserter': 'wait_sensor'}
  - delivered=1/2

## D-NO_PLAN_NO_PICK (5)
### CP-05
  - plan_calls=0 plan_fails=0 rate=None
  - delivered=0/1 cycles=0
  - last_phase={'/World/Franka': 'wait_sensor'}

### CP-40
  - plan_calls=0 plan_fails=0 rate=None
  - delivered=0/4 cycles=0
  - last_phase={'/World/Franka': 'wait_sensor'}

### CP-80
  - plan_calls=0 plan_fails=0 rate=None
  - delivered=0/1 cycles=0
  - last_phase={'/World/UR10': 'seek_cube'}

### CP-84
  - plan_calls=0 plan_fails=0 rate=None
  - delivered=0/1 cycles=0
  - last_phase={'/World/UR10': 'seek_cube'}

### CP-85
  - plan_calls=0 plan_fails=0 rate=None
  - delivered=0/1 cycles=0
  - last_phase={'/World/UR10': 'seek_cube'}

## D-NO_PLAN_NO_PICK | E-EVENT_CYCLE (1)
### CP-06
  - plan_calls=0 plan_fails=0 rate=None
  - delivered=0/4 cycles=0
  - last_phase={'/World/Franka': 'event=6'}

## F-BUILD_FAILED (3)
### CP-60
  - plan_calls=0 plan_fails=0 rate=None
  - delivered=0/0 cycles=0
  - last_phase={}
  - no probe data

### CP-62
  - plan_calls=0 plan_fails=0 rate=None
  - delivered=0/0 cycles=0
  - last_phase={}
  - no probe data

### CP-74
  - plan_calls=0 plan_fails=0 rate=None
  - delivered=0/0 cycles=0
  - last_phase={}
  - no probe data

## Z-OTHER (9)
### CP-35
  - plan_calls=74 plan_fails=12 rate=0.16
  - delivered=0/8 cycles=1
  - last_phase={'/World/Franka': 'executing'}
  - error: `RuntimeError: planning failed for /World/Cube_y2`
  - error: `RuntimeError: planning failed for /World/Cube_d2`

### CP-46
  - plan_calls=56 plan_fails=6 rate=0.11
  - delivered=0/6 cycles=1
  - last_phase={'/World/Franka': 'executing'}
  - error: `RuntimeError: planning failed for /World/Cube_5`

### CP-48
  - plan_calls=14 plan_fails=0 rate=0.0
  - delivered=0/5 cycles=1
  - last_phase={'/World/Franka': 'executing'}

### CP-51
  - plan_calls=7 plan_fails=0 rate=0.0
  - delivered=1/1 cycles=1
  - last_phase={'/World/FrankaA': 'wait_sensor', '/World/FrankaB': 'wait_sensor'}

### CP-52
  - plan_calls=60 plan_fails=12 rate=0.2
  - delivered=0/4 cycles=0
  - last_phase={'/World/FrankaA': 'wait_sensor', '/World/FrankaB': 'wait_sensor'}
  - error: `RuntimeError: planning failed for /World/Cube_2`
  - error: `RuntimeError: planning failed for /World/Cube_1`

### CP-53
  - plan_calls=54 plan_fails=8 rate=0.15
  - delivered=0/3 cycles=1
  - last_phase={'/World/FrankaA': 'executing', '/World/FrankaB': 'wait_sensor'}
  - error: `RuntimeError: planning failed for /World/Cube_3`

### CP-57
  - plan_calls=14 plan_fails=0 rate=0.0
  - delivered=0/0 cycles=1
  - last_phase={'/World/Franka': 'executing'}

### CP-58
  - plan_calls=14 plan_fails=0 rate=0.0
  - delivered=0/0 cycles=1
  - last_phase={'/World/Franka': 'executing'}

### CP-68
  - plan_calls=7 plan_fails=0 rate=0.0
  - delivered=1/1 cycles=1
  - last_phase={'/World/FrankaA': 'wait_sensor', '/World/FrankaB': 'wait_sensor'}


## Actionable next steps per category

- **A-PLAN_FAILS_HIGH**: predictive planning (project cube_pos by belt_v × plan_horizon). Or per-CP scenario_profile with looser reach margin if 3D reach was too aggressive.
- **B-PARTIAL_DELIVERY**: investigate gripper-release (Mode B FJ) and drop precision. Often timing-related between segments.
- **C-CUBE_FELL_OFF**: template-side fix. Belt too short OR sensor too far from robot.
- **D-NO_PLAN_NO_PICK**: 3D reach check might be too aggressive. Reduce safety margin or implement P1 predictive.
- **E-EVENT_CYCLE**: builtin handler-specific. Investigate event-state machine for stuck transitions.
- **F-BUILD_FAILED**: rebuild canonical, check for tool-call errors during install.