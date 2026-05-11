# Build-spec: Conveyor + Franka Pick-and-Place (rich prompt)

**Scenario ID**: `conveyor_pick_place_build`
**Sibling of**: `conveyor_pick_place.md` (test-spec, vague prompt, measures raw agent capability)
**Purpose**: Deterministic build. Agent gets all dimensions up front → reproducible output → eligible for promotion to `workspace/templates/conveyor_pick_place.json` for retrieval.

## Rationale

The test-spec strips numbers from the prompt to measure what the agent
can derive on its own. This is useful for benchmarking but produces
high run-to-run variance (Run 4 = giant 4×2×1.5m table, Run 5 = correct
2×1×0.75m table, same prompt). That variance blocks template generation
— we can't freeze a "known-good" output while it keeps drifting.

The build-spec flips the contract: hand the agent every number and
API requirement, then check that it actually built exactly that. A
passing run is frozen as a template. At runtime, a vague user request
("set up a pick-place cell") is matched against the template's `goal`
via ChromaDB and the template's `code` replaces derivation.

## Prompt the user should send

```
Build an industrial pick-and-place cell in Isaac Sim 5.x with these exact dimensions:

TABLE: create a Cube at /World/Table, position (0, 0, 0.375),
scaled so final world dimensions are 2.0m × 1.0m × 0.75m (top surface at Z=0.75).
Remember UsdGeom.Cube has default size=2, so scale accordingly. Apply
PhysicsCollisionAPI so cubes and robot can rest on it.

FRANKA PANDA: import at /World/Franka, base on table top at (0, 0, 0.75),
facing +Y. Must have ArticulationRootAPI and ≥10 descendant link prims.
Use robot_wizard(robot_name='franka_panda', dest_path='/World/Franka',
position=[0,0,0.75], orientation=[0.7071068, 0, 0, 0.7071068]).
The orientation quat rotates 90° around Z so the robot's default +X-forward
becomes +Y-forward. Profile auto-applies drive gains (kp=6000, kd=500),
switches to the AlternateFinger variant, and sets the home joint config —
no separate tune_gains / variant / home-pose calls needed.

CONVEYOR BELT: create at /World/ConveyorBelt, center (0, 0.3, 0.80),
dimensions 1.6m × 0.3m × 0.1m (top surface at Z=0.85). Runs along X axis,
placed IN FRONT OF the Franka at Y=0.3 so robot can reach forward to pick.
Apply PhysicsCollisionAPI + kinematic-enabled PhysicsRigidBodyAPI + PhysxSurfaceVelocityAPI
with surfaceVelocity=(0.2, 0, 0). Belt moves cubes toward +X at 0.2 m/s.

FOUR CUBES: /World/Cube_1..4 at X = -0.6, -0.4, -0.2, 0.0, all Y=0.3, Z=0.875.
Size 0.05m, mass 0.1 kg, RigidBodyAPI + CollisionAPI + MassAPI. Cubes are on the belt
moving toward +X — the Franka picks them as they approach X≈0.

BIN: /World/Bin at world (0, -0.4, 0.75), open-top container of 5 Cube children
(floor + 4 walls), each with CollisionAPI. External size 0.3m × 0.3m × 0.15m.
The parent Xform translate z=0.75 puts the bin floor bottom flush on the
table top (also z=0.75); bin walls extend up to z=0.90.
Placed BEHIND the Franka (Y=-0.4), so robot turns around to drop cubes.

DOME LIGHT: /World/DomeLight at (0, 0, 2.0), intensity 1000.

PHYSICS: /World/PhysicsScene must exist.

PROXIMITY SENSOR: add at /World/PickSensor, world position (0.3, 0.3, 0.86),
detection radius 0.04m. Triggers when a cube's bbox overlaps the sensor sphere
— this is the "pick station" where belt should pause.

PICK-PLACE CONTROLLER: install a sensor_gated controller that orchestrates:
(1) belt runs at 0.2 m/s by default.
(2) when /World/PickSensor triggers (cube arrives at pick station), belt
surfaceVelocity is set to (0, 0, 0) to pause transport.
(3) robot moves to pre-taught PICK pose above the triggered cube, closes
gripper, lifts, moves to pre-taught DROP pose above bin, opens gripper to
release cube into bin, returns to HOME pose.
(4) belt surfaceVelocity restores to (0.2, 0, 0), cycle repeats for next cube.

ROBOT POSES: teach three poses on /World/Franka before installing the
controller — PICK (above pick station, fingertips just above belt top),
DROP (above bin, fingertips above bin floor), HOME (clear of belt and bin).

IMPORTANT: the Franka at Y=0 must NOT overlap the belt (Y=0.15..0.45) or the bin
(Y=-0.55..-0.25). Check the math — with the listed dimensions none of these intersect.

Tools to use:
- create_bin for the bin
- create_conveyor for the belt
- robot_wizard(robot_name='franka_panda') for the Franka
- add_proximity_sensor for the sensor
- teach_robot_pose × 3 (PICK, DROP, HOME) or setup via setup_pick_place_controller
- setup_pick_place_controller(target_source='sensor_gated', ...) to wire it all up
```

## Target layout

| Component | Position | Dimensions |
|---|---|---|
| Table | (0, 0, 0.375) | 2.0 × 1.0 × 0.75 |
| Franka base | (0, 0, 0.75) | — |
| Belt | (0, 0.3, 0.80) | 1.6 × 0.3 × 0.1 |
| Cubes (4) | X = -0.6, -0.4, -0.2, 0.0, Y = 0.3 | 0.05 |
| Bin | (0, -0.4, 0.80) | 0.3 × 0.3 × 0.15 |
| Dome light | (0, 0, 2.0) | — |

Reach sanity: Franka base (0, 0, 0.75) with 0.855m envelope.
- Pick point (0.3, 0.3, 0.875) is 0.42m away ✓
- Drop point (0, -0.4, 0.95) is 0.45m away ✓
- No component occupies Franka's base footprint (X ±0.15, Y ±0.15) ✓

## Verify-checks (strict tolerance bands)

Checks tighten compared to test-spec. Tolerance policy: ±0.02m on
positions, ±5% on dimensions, exact for APIs and counts.

### Structural (no simulation required)

1. **B1 — Table exact**: /World/Table exists. World bbox size within 5% of (2.0, 1.0, 0.75). Top Z ∈ [0.73, 0.77].
2. **B2 — Belt combo**: /World/ConveyorBelt has all 3 APIs (Collision + kinematic RigidBody + SurfaceVelocity). Velocity within 5% of (0.2, 0, 0). Top Z ∈ [0.83, 0.87].
3. **B3 — Four cubes on belt**: exactly 4 prims matching `/World/Cube_*`. Each size 0.05±0.005m. Bottom Z ∈ [0.85, 0.90]. X-positions within ±0.05m of [-0.6, -0.4, -0.2, 0.0], Y=0.3.
4. **B4 — Franka on table**: /World/Franka with ArticulationRootAPI, ≥10 descendants, base Z ∈ [0.73, 0.77], XY within ±0.05m of (0, 0).
5. **B5 — Bin position + structure**: /World/Bin at (0, -0.4, 0.80) ±0.05m. 5 collision-enabled children. External size within 5% of (0.3, 0.3, 0.15).
6. **B6 — Dome light**: /World/DomeLight with intensity 1000 ±10%.
7. **B7 — PhysicsScene exists**: any prim of type PhysicsScene under /World.
8. **B8 — Proximity sensor**: /World/PickSensor exists at world position (0.3, 0.3, 0.86) ±0.05m. Has PhysxTriggerAPI and `isaac_sensor:triggered` attribute.
9. **B9 — Pick-place controller registered**: physics callback named `pick_place_sensor_gated` (or equivalent) exists. Attribute on /World/Franka or similar indicating sensor_gated mode is active.

### Dynamic (require `--dynamic` flag; run after structural passes)

10. **D1 — Belt moves cubes**: after 2s of physx stepping, at least one cube's X has changed by ≥ 0.1m (belt transfer works).
11. **D2 — Belt pauses on sensor trigger**: after a cube reaches (0.3, 0.3, 0.86) ±0.04m, belt surfaceVelocity reads (0, 0, 0) within 1s (controller responded).
12. **D3 — Robot reaches pick station**: during 30s sim, end-effector (panda_hand) comes within 0.1m of the triggered cube at least once.
13. **D4 — ≥1 cube in bin**: after 60s sim, at least one cube is inside /World/Bin's bbox volume.

All structural (B1-B9) passing = eligible for template promotion. Dynamic checks (D1-D4) additionally validate the controller actually works under simulation.

## Success → template promotion

When a run passes B1-B7:

1. Save `workspace/scenario_results/conveyor_pick_place_build_{ts}.json` (existing check runner output).
2. Extract the verified tool-call sequence from the session trace.
3. Produce `workspace/templates/conveyor_pick_place.json` with:
   ```json
   {
     "task_id": "conveyor_pick_place",
     "goal": "Set up an industrial pick-and-place cell on a table: moving conveyor delivers small cubes to a Franka Panda, robot picks each and drops into an open-top bin.",
     "tools_used": [...from trace...],
     "thoughts": "UsdGeom.Cube default size=2 → halve scale dimensions. Belt 0.02m above table top. Cubes 2.5cm above belt top to settle via physics.",
     "code": "...verbatim tool-call sequence...",
     "failure_modes": [
       "Agent uses scale=(2,1,0.75) → 4×2×1.5m table (ignores Cube size=2)",
       "Forgets PhysxSurfaceVelocityAPI on belt → cubes don't move",
       "Places cubes at Z=0 on ground instead of Z=0.875 on belt"
     ]
   }
   ```
4. `template_retriever.rebuild_index()` picks it up.

## Not changed from test-spec

- Persona-driven architectures (sensor_gated default, cube_tracking alt, etc)
- Required subsystems list
- Anti-patterns to flag
- Dynamic checks C6-C8 (belt motion, robot reach, cube in bin) — still measure behavior, still scenario-specific

## Open questions (decide later)

- Delete the test-spec once build-spec works? Or keep both (one measures capability, one builds templates)?
- Do we auto-generate the template JSON from a passing run, or write it by hand?
- Does the richness in the prompt ACTUALLY produce deterministic output, or does the agent still drift on unspecified details (gripper config, render settings)? Empirical — run N=5 and check variance.
