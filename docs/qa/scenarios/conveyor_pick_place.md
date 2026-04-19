# Scenario: Conveyor + Franka Pick-and-Place

**Scenario ID**: `conveyor_pick_place`
**Tier**: 1 (5-7 steps, 3-4 subsystems)
**Date authored**: 2026-04-19
**Status**: active — first industrial-style scenario

## Goal (human-readable)

Set up a pick-and-place cell: a conveyor belt on a table delivers small cubes to a Franka arm, which picks each cube and drops it into a bin positioned on the side of the table. The setup must use realistic industrial dimensions.

## Prompt the user should send

```
Set up a pick-and-place cell on a table: a conveyor belt moves small cubes
to a Franka Panda arm, which picks each cube and drops it into a bin on
the side of the table. Use realistic industrial dimensions — the Franka
must actually reach both the belt pick point and the bin.
```

Keep the prompt **free of specific numbers** (belt length, cube size, table height). The agent should derive dimensions from the Franka's reach envelope, which is a tool-discovery test (`lookup_product_spec("franka_panda")` is the expected call).

## Target layout

All coordinates in meters, Z-up, Y perpendicular to belt travel.

| Component | Position | Dimensions | Notes |
|---|---|---|---|
| Table | (0, 0, 0.375) | 2.0 × 1.0 × 0.75 | Top at Z=0.75 |
| Franka base | (0, 0, 0.75) | — | Mounted on table top, workspace sphere r=0.855 |
| Belt | (-0.55, 0, 0.80) center | 1.9 × 0.3 × 0.1 | Top at Z=0.85, runs along -X to +X |
| Cubes (4) | X = -1.3, -1.0, -0.7, -0.4 | 0.05 cube, mass 0.1 kg | Initial Z=0.875 (on belt) |
| Bin | (0, 0.5, 0.80) | 0.3 × 0.3 × 0.15 | On table, open top, 4 walls + floor |
| Dome light | (0, 0, 2.0) | — | For visibility |

**Reach sanity**: pick point at (0.3, 0, 0.875) is 0.335m from Franka base → well inside 0.855m envelope. Bin drop-point at (0, 0.5, 0.95) is 0.54m away → also inside.

**Belt direction**: +X (cubes travel toward robot). Speed: 0.2 m/s (slow enough for robot reaction).

## Required subsystems

The agent must correctly orchestrate:

1. **USD basics** — prim creation, xform-ops, hierarchy
2. **PhysX collision** — table, belt, cubes, bin walls all need CollisionAPI
3. **PhysX rigid bodies** — cubes need RigidBodyAPI (dynamic), belt needs RigidBodyAPI+kinematicEnabled (required for surface-velocity)
4. **PhysxSurfaceVelocityAPI** — belt motion (see `/cite conveyor surface velocity`)
5. **Robot import** — Franka loaded with ArticulationRootAPI, joints addressable
6. **Grasp / motion control** — the `grasp_object` tool OR a custom pick-place sequence
7. **Physics simulation lifecycle** — scene must have PhysicsScene, timeline must start

## Verify-checks (8 total)

Run by `scripts/qa/check_conveyor_pick_place.py` against the live Kit stage. Each check is binary pass/fail. Total score = sum(pass) / 8.

### Structural checks (run pre-simulation)

1. **C1 — Table exists**
   A prim under /World/ with type in {Cube, Mesh, Xform} whose bounding-box top is between Z=0.7 and Z=0.8. (Relaxed so agent can name it /World/Table or /World/Workbench.)

2. **C2 — Belt has surface-velocity combo**
   At least one prim under /World/ has ALL THREE applied: PhysicsCollisionAPI, PhysicsRigidBodyAPI with kinematicEnabled=True, PhysxSurfaceVelocityAPI with surfaceVelocity non-zero.

3. **C3 — 4 cubes on belt**
   At least 4 prims of type Cube with world bbox bottom between Z=0.80 and Z=0.90 (on belt top), mass ≤ 0.2 kg, size ≤ 0.08m.

4. **C4 — Franka imported**
   A prim under /World/ with ArticulationRootAPI applied, whose name contains "franka" or "panda" (case-insensitive). Base translate Z between 0.7 and 0.85 (sitting on table).

5. **C5 — Bin has 4 walls + floor**
   A prim under /World/ whose name contains "bin" or "box" or "container". Has ≥5 children, all with CollisionAPI.

### Dynamic checks (run after N seconds of simulation)

6. **C6 — Belt moves cubes**
   After 3 seconds simulation, at least one cube's X position has changed by ≥ 0.1m from its initial position. (Tests surface-velocity transfer.)

7. **C7 — Robot reaches pick point**
   After 10 seconds, the Franka end-effector (any prim whose name contains "panda_hand" or "tcp" or "end_effector") has been within 0.1m of any cube's position at least once. (Logged via max cross-frame distance check.)

8. **C8 — ≥1 cube lands in bin**
   After 30 seconds simulation, at least one cube is inside the bin's bbox volume. (Stretch goal — most likely to fail on first attempt.)

## Anti-patterns to flag

Detected by scanning all `run_usd_script` code_previews in the trace.

| Pattern | Severity | Why |
|---|---|---|
| `CreateMeshPrimWithDefaultXform` | error | Authors mesh data on Cube TypeName → warped geometry (validator already blocks) |
| `TransformPrimCommand` | error | Not a real command (validator blocks) |
| `ClearXformOpOrder` | warning | Leaves orphan attrs (validator warns) |
| `IsA(UsdLux.LightAPI)` or similar | error | Applied-API vs prim-type confusion (validator blocks) |
| `pipeline_stage=GraphBackingType.*` | error | Enum mismatch (validator blocks) |
| `omni.kit.commands.execute("CreateConveyor"` or similar fabricated names | error | Not a registered command |
| Multiple `DeletePrims` + recreate on same path within one script | warning | Session-layer ghosts |

## Expected tool usage

These tools should appear in the trace at least once:

- `lookup_product_spec` (query="franka_panda" or similar) — to discover reach envelope
- `import_robot` (Franka) OR `add_reference` (Franka USD)
- `create_conveyor_track` OR explicit PhysxSurfaceVelocityAPI+kinematic RigidBody combo
- `apply_api_schema` (for physics on cubes and bin)
- `sim_control` or timeline.play()
- `get_world_transform` or similar read-back for verification

Tools that SHOULDN'T appear:

- `cloud_launch`, `launch_training` (wrong workflow)
- `record_demo_video`, `record_trajectory` (not needed for this scenario)

## Known gotchas (from 2026-04-19 smoke-tests)

- Agent tends to reach for `run_usd_script` over higher-level tools. If `create_conveyor_track` isn't called, note it — agent missed tool discovery.
- Precision-mismatch trap: existing prims have float3 scale, new AddScaleOp with Vec3d fails USD validation. Use the reuse-existing-op pattern.
- Session-layer ghosts: deleting + recreating the same prim path within a session can compose stale attrs. Agent should verify PrimStack is clean.
- Franka default pose has Joint_1 such that the arm is upright. Reach-check should account for this.

## Scoring

- **8/8 passing**: perfect run. Document what worked so other scenarios reuse the pattern.
- **6/8 passing, all structural + belt motion**: functionally correct setup, robot orchestration is the remaining failure. Tier-1 MVP achieved.
- **4/8 passing, structural only**: agent got geometry right but couldn't connect physics or motion. Common for first attempts.
- **<4/8**: agent did not understand the task. Investigate thoughts + tool choices.

## Iteration

After each run:
1. Copy trace summary to `workspace/scenario_results/conveyor_pick_place_{date}.json`
2. Note which checks failed and any new anti-patterns hit
3. If a failure class keeps repeating: add a validator rule, a cite entry, or a tool docstring nudge
4. Re-run, compare scores over time

The goal is **monotonic improvement** — each intervention should raise the score, not regress other checks.
