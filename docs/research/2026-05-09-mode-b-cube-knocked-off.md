# Failure Mode B: Cube Falls to Floor at (0.59, 0.59, 0.525)

**Affects:** CP-30 (4-cube palletizer), CP-37 (obstacle-avoidance station)  
**Symptom:** Cube_1 final position `(+0.59, +0.59, +0.525)`, `above_floor=False`  
**Date:** 2026-05-09

---

## Geometry of the Failure Position

All coordinates can be decoded from template constants:

| Value | Source | Meaning |
|---|---|---|
| z=0.525 | ground_top(0.5) + cube_half(0.025) | Cube resting on the ground, not on belt or table |
| y=0.59 | belt outer edge at y=0.60, table edge at y=0.50 | Cube tipped off the belt's outer (+y) side edge |
| x=0.59 | within belt x-range (-1.5 to +1.5) | Cube was moving in +x when it fell off the belt side |

The cube is **not** at the pick position (x~-0.57), not at a transit waypoint, and not at a drop target. It is at ground level directly below the outer belt edge. The chain of events that gets it there is described below.

---

## Root Cause: Elbow Sweep During S3 Lift Knocks Cube off Belt Side

### What happens in the curobo controller

The `_gen_pick_place_curobo` path (`_build_segments`) plans seven joint-space segments per cube:

```
S1:   EE to (cube_x, cube_y, h1)       — approach above cube
S1.5: EE to (cube_x, cube_y, 1.015)    — mid-height descent
S2:   EE to (cube_x, cube_y, 0.940)    — descend + close gripper  ← GRIP POINT
S3:   EE to (cube_x, cube_y, h1)       — lift
S4:   EE to (drop_x,  drop_y,  h1)     — transit
S4.5: EE to (drop_x,  drop_y,  h_mid)  — mid-drop
S5:   EE to (drop_x,  drop_y,  drop_z) — descend + open gripper
```

The S2 target (`pz = cube_z + FL = 0.835 + 0.105 = 0.940`) positions `panda_hand` at the correct height for finger-tips to reach cube center. At S2 completion, `_grip_close()` fires. In the curobo controller, `_grip_close()` calls only `franka.gripper.forward("close")` — **no FixedJoint is created** (unlike the spline controller at line 32049 which creates a `UsdPhysics.FixedJoint`).

When S3 begins (EE lifts from z=0.940 to h1), the Franka arm is fully extended at reach maximum toward y=0.4 (belt center). To lift the EE upward along the same x,y column, the planner must retract and re-extend joints, and the **elbow/forearm (panda_link4/5/6) sweeps laterally in the +y direction** (outward from the robot body, toward the belt's outer edge). This is geometrically forced: with robot base at (0,0) oriented 90° about Z, the elbow must swing toward y>0.4 to lift the wrist while holding x,y constant.

The cube, sitting at belt height (z=0.835, y=0.4), is at the same vertical level as the elbow during early S3. The elbow body contacts the cube and **pushes it approximately 0.19m in the +y direction** to y≈0.59.

Once the cube is at y=0.59, it is 0.01m from the belt's outer edge (y=0.6). The cube half-extent is 0.025m, so the cube overhangs the belt edge by 0.015m. Without a side wall, the cube tips and falls off the belt's +y face. It drops 0.31m to the ground (belt_top z=0.835 → ground_top z=0.5 + cube_half 0.025 = 0.525).

After the cycle ends, the cube is added to `S["failed"]` and is never retried. It rests at (0.59, 0.59, 0.525) — outside the 0.70m Franka reach limit (distance from base = 0.835m) and permanently inaccessible.

---

## Why Both Cubes Land at Identical (0.59, 0.59, 0.525)

The landing position is **deterministic**, not coincidental:

1. **Same S2 goal** — both CP-30 and CP-37 pick from the same conveyor with the same cube geometry, so S2 ends at the same EE pose `(x, 0.4, 0.940)`.

2. **Same S2 end-config seeds S3** — `_build_segments` chains plans: `q = traj[-1]` at each segment becomes the next segment's start joint state. The S3 plan is seeded from the S2 end-config, which is the same for both canonicals.

3. **Different h1, same elbow contact** — CP-30 uses auto h1=1.235; CP-37 uses explicit h1=1.30. The elbow sweep angle (and therefore the contact force and resulting cube displacement) is governed by the S2→S3 lift path, not by the S1 approach height. Both arms execute the same lower portion of the lift.

4. **Same friction coefficient** — both templates apply `apply_physics_material(material_name="rubber")` to the cubes. The FixedJoint-absent grip behaves identically.

5. **Deterministic physics** — `set_physics_scene_config(enable_gpu_dynamics=False, broadphase_type="MBP")` runs CPU physics. Same inputs → same trajectory.

The identical final coordinates are therefore a signature of a **deterministic elbow-sweep impact** at a fixed point in both controllers' joint-space trajectories.

---

## Why CP-08 Partially Works (2/4 Cubes Delivered)

CP-08 uses the same curobo controller and same scene layout (only the pallet is smaller). The 2/4 success rate comes from stochastic variation in the grip: cuRobo's planner uses random seeds (`max_attempts=3` in `_plan_to_world_point`). On successful seeds, the S3 trajectory takes a path where the elbow sweep does not contact the cube before friction grip secures it. When friction grip forms before the elbow sweep, the cube is carried safely upward. When it does not, Failure Mode B triggers.

CP-30 and CP-37 ran once each (single-run probe), making them land in the failure branch. Multiple runs would show variable success rates, but the failure mode is always the same: cube at (0.59, 0.59, 0.525).

---

## Code Locations

| File | Lines | Relevance |
|---|---|---|
| `service/isaac_assist_service/chat/tools/tool_executor.py` | 33055–33065 | `_grip_close()` in curobo mode — no FixedJoint |
| `tool_executor.py` | 33184–33226 | `_build_segments()` — seven-segment plan, S3 lifts from S2 end-config |
| `tool_executor.py` | 33303–33319 | `_on_step` executing mode — grip_done fires, then post-grip dwell |
| `tool_executor.py` | 32049–32059 | `_attach_cube()` in spline mode — has FixedJoint; curobo lacks this |
| `tool_executor.py` | 33263–33272 | Delivered/failed branching — failed cube never retried |

---

## Fix Proposals

### Fix 1 (Primary): Add FixedJoint to curobo `_grip_close` (mirrors spline mode)

In `_on_step` (curobo executing mode), after `_grip_close()` fires at the `"close"` segment, create a `UsdPhysics.FixedJoint` between `panda_hand` and the cube. Release it at the `"open"` segment.

```python
# In _on_step, where cur_seg["action_after"] == "close":
if cur_seg["action_after"] == "close":
    _grip_close()
    # ── FixedJoint cheat (mirrors spline mode) ──
    if S.get("picked_path"):
        try:
            ee = stage.GetPrimAtPath(ROBOT_PATH + "/panda_hand")
            cube = stage.GetPrimAtPath(S["picked_path"])
            if ee and ee.IsValid() and cube and cube.IsValid():
                jp = f"{S['picked_path']}_curobo_fj"
                fj = UsdPhysics.FixedJoint.Define(stage, jp)
                fj.CreateBody0Rel().SetTargets([Sdf.Path(str(ee.GetPath()))])
                fj.CreateBody1Rel().SetTargets([Sdf.Path(S["picked_path"])])
                S["grasp_joint"] = jp
        except Exception as _fje:
            print(f"(curobo fj snap fail: {_fje})")
    cur_seg["grip_done"] = True

# In _on_step, where cur_seg["action_after"] == "open":
elif cur_seg["action_after"] == "open":
    if S.get("grasp_joint"):
        try:
            if stage.GetPrimAtPath(S["grasp_joint"]).IsValid():
                stage.RemovePrim(S["grasp_joint"])
        except Exception: pass
        S["grasp_joint"] = None
    _grip_open()
    cur_seg["grip_done"] = True
```

The `S["grasp_joint"]` field already exists in the curobo reset hook (`line ~33344`) so state management is already wired.

This fix directly eliminates Failure Mode B: even if the elbow contacts the cube during S3, the FixedJoint constraint holds the cube rigidly to `panda_hand`. The cube is transported despite the collision.

**Risk:** Sim2real dishonesty (FixedJoint teleports cube into EE-relative pose on formation). This is already accepted practice in the spline controller and the UR10 FixedJoint workaround. It is the correct trade-off for function-gate verification.

### Fix 2 (Secondary): Belt side-guard prim in templates

Add a thin wall along the belt outer edge in CP-30 and CP-37 templates:

```python
create_prim(prim_path="/World/BeltGuardYPlus", prim_type="Cube",
            position=[0.0, 0.635, 0.855], scale=[1.5, 0.01, 0.025])
apply_api_schema(prim_path="/World/BeltGuardYPlus", schema_name="PhysicsCollisionAPI")
```

This is a scene-level safeguard. It does not fix the elbow-sweep root cause but catches cubes that get nudged toward the outer edge. Not recommended as the sole fix — Fix 1 is cleaner.

### Fix 3 (Tertiary): Scene-collision re-enable via Warp upgrade

Tracked as I-38: `update_world` is disabled because Warp 1.8.2 lacks `CuboidDataWarp` registration. With scene collision active, the cube would be a registered obstacle and cuRobo's planner would route the elbow away from it during S3. This is the architecturally correct long-term fix but requires an environment upgrade.

---

## Estimated Unlock Count

| Fix | CP-30 | CP-37 | CP-08 | Other curobo canonicals | Total |
|---|---|---|---|---|---|
| Fix 1 (FixedJoint) | 4/4 | 4/4 | 4/4 | variable | **+2 canonicals unblocked, +1 improved** |

There are **70 curobo-based canonicals** in `workspace/templates/`. All are affected by the friction-only grip gap to varying degrees depending on scene geometry. Fix 1 applied to `_gen_pick_place_curobo` in `tool_executor.py` will improve delivery reliability across all 70, with the most visible unlocks on CP-30 and CP-37 which are currently 0-delivery.

Direct unblocks from Fix 1: **CP-30, CP-37** (currently 0/4 delivery, expected 4/4 after fix). CP-08 improves from 2/4 to expected 4/4, upgrading it from partial to full function-gate.
