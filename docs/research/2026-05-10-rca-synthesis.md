# RCA Synthesis — 2026-05-10

Probed 24 CPs from patched-set + control samples.

## Pattern Distribution
- **P-PLAN_FAIL**: 10 CPs
  - CP-37 (delivered=0)
  - CP-46 (delivered=0)
  - CP-51 (delivered=0)
- **P-OTHER**: 7 CPs
  - CP-05 (delivered=0)
  - CP-22 (delivered=0)
  - CP-57 (delivered=0)
- **P-SENSOR_NEVER_FIRES**: 2 CPs
  - CP-06 (delivered=0)
  - CP-40 (delivered=0)
- **P-PLAN_FAIL | P-WALL_STUCK(8 cubes)**: 1 CPs
  - CP-35 (delivered=0)
- **P-WALL_STUCK(5 cubes)**: 1 CPs
  - CP-48 (delivered=0)
- **P-BUILD_FAIL**: 1 CPs
  - CP-60 (delivered=0)
- **P-MULTI_ROBOT_PARTIAL**: 1 CPs
  - CP-65 (delivered=1)
- **P-PLAN_FAIL | P-MULTI_ROBOT_PARTIAL**: 1 CPs
  - CP-67 (delivered=1)

## Detail per CP
### CP-05 — `P-OTHER`
  - last_phase: `{'/World/Franka': 'wait_sensor'}`
  - plan_calls=0 cubes_delivered=0

### CP-06 — `P-SENSOR_NEVER_FIRES`
  - last_phase: `{'/World/Franka': 'event=6'}`
  - plan_calls=0 cubes_delivered=0
  - cube features: stuck=0 fallen=0 passed_no_trigger=4

### CP-22 — `P-OTHER`
  - last_phase: `{'/World/Franka': 'executing'}`
  - plan_calls=0 cubes_delivered=0

### CP-35 — `P-PLAN_FAIL | P-WALL_STUCK(8 cubes)`
  - last_phase: `{'/World/Franka': 'executing'}`
  - plan_calls=0 cubes_delivered=0
  - cube features: stuck=8 fallen=0 passed_no_trigger=0
  - error: `RuntimeError: planning failed for /World/Cube_y2`
  - error: `RuntimeError: planning failed for /World/Cube_d2`

### CP-37 — `P-PLAN_FAIL`
  - last_phase: `{'/World/Franka': 'wait_sensor'}`
  - plan_calls=0 cubes_delivered=0
  - error: `RuntimeError: planning failed for /World/Cube_3`
  - error: `RuntimeError: planning failed for /World/Cube_2`

### CP-40 — `P-SENSOR_NEVER_FIRES`
  - last_phase: `{'/World/Franka': 'wait_sensor'}`
  - plan_calls=0 cubes_delivered=0
  - cube features: stuck=0 fallen=0 passed_no_trigger=4

### CP-46 — `P-PLAN_FAIL`
  - last_phase: `{'/World/Franka': 'wait_sensor'}`
  - plan_calls=0 cubes_delivered=0
  - cube features: stuck=0 fallen=0 passed_no_trigger=6
  - error: `RuntimeError: planning failed for /World/Cube_4`
  - error: `RuntimeError: planning failed for /World/Cube_3`

### CP-48 — `P-WALL_STUCK(5 cubes)`
  - last_phase: `{'/World/Franka': 'executing'}`
  - plan_calls=0 cubes_delivered=0
  - cube features: stuck=5 fallen=0 passed_no_trigger=0

### CP-51 — `P-PLAN_FAIL`
  - last_phase: `{'/World/FrankaA': 'wait_sensor', '/World/FrankaB': 'wait_sensor'}`
  - plan_calls=0 cubes_delivered=0
  - cube features: stuck=0 fallen=0 passed_no_trigger=1
  - error: `RuntimeError: planning failed for /World/Cube_1`

### CP-52 — `P-PLAN_FAIL`
  - last_phase: `{'/World/FrankaA': 'wait_sensor', '/World/FrankaB': 'wait_sensor'}`
  - plan_calls=0 cubes_delivered=0
  - cube features: stuck=0 fallen=0 passed_no_trigger=4
  - error: `RuntimeError: planning failed for /World/Cube_1`

### CP-53 — `P-PLAN_FAIL`
  - last_phase: `{'/World/FrankaA': 'wait_sensor', '/World/FrankaB': 'wait_sensor'}`
  - plan_calls=0 cubes_delivered=0
  - cube features: stuck=0 fallen=0 passed_no_trigger=3
  - error: `RuntimeError: planning failed for /World/Cube_2`
  - error: `RuntimeError: planning failed for /World/Cube_1`

### CP-57 — `P-OTHER`
  - last_phase: `{'/World/Franka': 'executing'}`
  - plan_calls=0 cubes_delivered=0

### CP-58 — `P-PLAN_FAIL`
  - last_phase: `{'/World/Franka': 'wait_sensor'}`
  - plan_calls=0 cubes_delivered=0
  - error: `RuntimeError: planning failed for /World/Peg_3`
  - error: `RuntimeError: planning failed for /World/Peg_2`

### CP-60 — `P-BUILD_FAIL`
  - last_phase: `{}`
  - plan_calls=0 cubes_delivered=0

### CP-62 — `P-PLAN_FAIL`
  - last_phase: `{'/World/Franka': 'wait_sensor'}`
  - plan_calls=0 cubes_delivered=0
  - cube features: stuck=0 fallen=0 passed_no_trigger=4
  - error: `RuntimeError: planning failed for /World/Cube_3`
  - error: `RuntimeError: planning failed for /World/Cube_2`

### CP-65 — `P-MULTI_ROBOT_PARTIAL`
  - last_phase: `{'/World/FrankaA': 'wait_sensor', '/World/FrankaB': 'executing'}`
  - plan_calls=0 cubes_delivered=1

### CP-67 — `P-PLAN_FAIL | P-MULTI_ROBOT_PARTIAL`
  - last_phase: `{'/World/FrankaA': 'wait_sensor', '/World/FrankaB': 'wait_sensor'}`
  - plan_calls=0 cubes_delivered=1
  - error: `RuntimeError: planning failed for /World/Cube_4`

### CP-68 — `P-PLAN_FAIL`
  - last_phase: `{'/World/FrankaA': 'wait_sensor', '/World/FrankaB': 'wait_sensor'}`
  - plan_calls=0 cubes_delivered=0
  - cube features: stuck=0 fallen=0 passed_no_trigger=1
  - error: `RuntimeError: planning failed for /World/Cube_1`

### CP-73 — `P-PLAN_FAIL`
  - last_phase: `{'/World/UR10': 'wait_sensor'}`
  - plan_calls=0 cubes_delivered=0
  - cube features: stuck=0 fallen=0 passed_no_trigger=4
  - error: `RuntimeError: planning failed for /World/Cube_3`
  - error: `RuntimeError: planning failed for /World/Cube_2`

### CP-74 — `P-OTHER`
  - last_phase: `{'/World/UR10': 'seek_cube'}`
  - plan_calls=0 cubes_delivered=0

### CP-76 — `P-PLAN_FAIL`
  - last_phase: `{'/World/FixtureHolder': 'wait_sensor', '/World/Inserter': 'wait_sensor'}`
  - plan_calls=0 cubes_delivered=0
  - cube features: stuck=0 fallen=0 passed_no_trigger=2
  - error: `RuntimeError: planning failed for /World/Cube_workpiece`
  - error: `RuntimeError: planning failed for /World/Cube_mating`

### CP-80 — `P-OTHER`
  - last_phase: `{'/World/UR10': 'seek_cube'}`
  - plan_calls=0 cubes_delivered=0

### CP-84 — `P-OTHER`
  - last_phase: `{'/World/UR10': 'seek_cube'}`
  - plan_calls=0 cubes_delivered=0

### CP-85 — `P-OTHER`
  - last_phase: `{'/World/UR10': 'seek_cube'}`
  - plan_calls=0 cubes_delivered=0
