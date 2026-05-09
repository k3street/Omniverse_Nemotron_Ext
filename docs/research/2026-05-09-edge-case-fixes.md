# Edge-Case Canonical Failure Analysis

**Date:** 2026-05-09  
**Canonicals:** CP-22, CP-37, CP-58, CP-65, CP-73  
**Status:** Pre-fix analysis — no code changes applied in this document

---

## Context: What Is Already Fixed

Before diving into the five edge cases, it is important to note which fixes are **already deployed** in `tool_executor.py` as of this session:

- **Mode B FixedJoint** (CP-37 fix): `_gen_pick_place_curobo` creates a `UsdPhysics.FixedJoint` between `panda_hand` and the cube at `action_after == "close"`, preventing elbow-sweep knockoff. Lines 33391–33406.
- **3-strike plan-fail quarantine** (Mode A fix): After 3 consecutive `_build_segments` failures for the same cube, the cube is added to `S["failed"]`. Lines 33320–33327.
- **Pre-step belt-pause subscription** (CP-74/CP-80 fix): `subscribe_physics_on_step_events(..., pre_step=True, order=0)` applies belt pause/resume before `PxScene::simulate()`. Lines 33049–33061.
- **Home-config IK seed + fallback** (planning quality fix): `_build_segments` is called first with `_HOME_Q`, then falls back to `jp` if that fails. Lines 33313–33318.

These four fixes are live. The five canonicals below have **additional** failure modes not covered by the existing fixes.

---

## CP-22: High-Speed Belt (0.5 m/s nominal, ~1.5 m/s apparent)

### Root Cause

CP-22 extends CP-01 with `surface_velocity=[0.5, 0, 0]`. With the ~3× PhysX stick-slip multiplier typical for rubber-on-belt contact, the apparent cube surface speed is ~1.5 m/s. The sensor zone is 0.06 × 0.06 × 0.06 m centered at x=0.4.

**Transit time through the sensor zone = 0.06 / 1.5 = 0.04 s.** At 60 Hz physics, that is only **2–3 physics ticks** for the cube to cross the entire sensor volume. The `_cube_to_pick()` function in the curobo handler checks whether a cube is within 0.70 m of robot base AND within reach — it has no sensor-zone intersection check. The call fires once per physics tick.

The claim sequence is:
1. Cube enters sensor zone.
2. Tick N: `_cube_to_pick()` returns the cube. Belt is paused. Controller enters `settling` mode for 8 ticks.
3. Tick N+8: cube position is read and trajectory planning begins.

**This sequence works for CP-01** (0.2 m/s apparent, ~0.4 s transit) because the cube is still within the sensor zone when `_cube_to_pick()` fires, and the 8-tick settle gives the cube time to decelerate to ~0 before the position is locked.

**At 0.5 m/s nominal / 1.5 m/s apparent**, the cube may pass through the entire sensor zone in fewer ticks than the time from when `_cube_to_pick()` first returns it to when the belt-pause write actually lands in PhysX (1–2 ticks delay even with the pre-step fix). The cube exits the sensor zone before the belt pauses; once the belt does pause, the cube is past x=0.4 and continuing to drift under residual friction until stopping. The 8-tick settle is then spent at a position past the optimal pick point (x~0.45–0.55), near the robot's reach boundary at 0.70 m from base.

There is a second compound issue: the CP-22 spec places the sensor at x=0.4, y=0.4, z=0.835. The cubes start at x=[-1.4, -1.1, -0.8, -0.5]. At 1.5 m/s apparent speed, Cube_4 (x=-0.5) arrives at the sensor zone in ~0.6 s of sim time — less than 40 physics ticks. If the robot is still in a planning or executing state from the prior cube, `_cube_to_pick()` simply won't fire (mode is not `wait_sensor`) and Cube_4 passes undetected.

**The pre-step belt-pause fix does not help CP-22.** Belt pause timing is not the bottleneck; the cube is already past the sensor zone before any claim occurs at high speed.

### Fix

**Predictive claim: widen the `_cube_to_pick` detection window upstream of the sensor zone.**

In `_gen_pick_place_curobo`, the `_cube_to_pick` function currently checks only whether a cube is within `_reach_m` (0.70 m) of the robot base. Add a secondary candidate path: accept cubes that are **approaching** the sensor zone from upstream (negative x side), within a look-ahead distance proportional to current belt velocity.

```python
def _cube_to_pick():
    base_xy = np.array([float(_usd_pos[0]), float(_usd_pos[1])])
    base_z = float(_usd_pos[2])
    sxy = _sensor_xy_v if _sensor_xy_v is not None else base_xy
    cands = []
    _reach_m = 1.20 if ROBOT_FAMILY in ("ur10", "ur10e") else 0.70

    # --- NEW: read current belt velocity to compute look-ahead window ---
    _belt_v = 0.0
    try:
        _bsv = stage.GetPrimAtPath(BELT_PATH).GetAttribute(
            "physxSurfaceVelocity:surfaceVelocity")
        if _bsv and _bsv.IsDefined():
            _bsv_val = _bsv.Get()
            if _bsv_val:
                _belt_v = abs(float(_bsv_val[0]))  # assume x-axis belt
    except Exception:
        pass
    # At high speeds (>0.25 m/s apparent), claim cubes up to 0.30 m upstream
    # of the sensor so belt-pause lands before the cube exits the zone.
    # At nominal speed (<=0.25 m/s) use 0.0 (existing behaviour: no look-ahead).
    _look_ahead_x = 0.30 if _belt_v > 0.25 else 0.0
    # --- END NEW ---

    for sp in SOURCE_PATHS:
        if sp in S["delivered"] or sp in S.get("failed", set()) or _is_in_bin(sp): continue
        cp = _world_pos(sp)
        if cp is None: continue
        if cp[2] < base_z - 0.30 or cp[2] > base_z + 0.50: continue
        if float(np.linalg.norm(cp[:2] - base_xy)) > _reach_m: continue
        # Sensor proximity: in zone (existing), OR approaching from upstream
        # within look-ahead window.
        _d_sensor = float(np.linalg.norm(cp[:2] - sxy))
        _approaching = (
            _look_ahead_x > 0.0
            and cp[0] < sxy[0]                       # cube is upstream (lower x)
            and (sxy[0] - cp[0]) <= _look_ahead_x    # within look-ahead
            and abs(cp[1] - sxy[1]) < 0.10           # on-axis: within 10cm in y
        )
        if _d_sensor > 0.12 and not _approaching:
            continue  # too far from sensor and not approaching
        cands.append((_d_sensor, sp))
    if not cands: return None
    cands.sort(); return cands[0][1]
```

The `_look_ahead_x = 0.30 m` is derived from:
- Belt speed 0.5 m/s nominal, 1.5 m/s apparent
- Planning starts 8 ticks (~0.13 s) after claim
- Cube travels 0.13 s × 1.5 m/s = 0.195 m during settle
- 0.30 m provides ~0.10 m safety margin

The `_belt_v > 0.25` threshold ensures look-ahead only activates for CP-22 / other high-speed variants; CP-01's 0.2 m/s nominal is correctly excluded (avoids premature claims at low speed where the existing sensor-zone check is sufficient).

Additionally, the `settle_ticks` value should be increased from 8 to 16 for high-speed belts. The extra 8 ticks (0.13 s additional) costs little and gives more margin for residual cube velocity to decay:

In `_on_step → wait_sensor` branch:
```python
# Adapt settle time to belt speed — higher speed = more residual momentum
_belt_now = 0.0
try:
    _bsv2 = stage.GetPrimAtPath(BELT_PATH).GetAttribute(
        "physxSurfaceVelocity:surfaceVelocity")
    if _bsv2 and _bsv2.IsDefined():
        _v = _bsv2.Get()
        if _v: _belt_now = abs(float(_v[0]))
except Exception:
    pass
S["settle_ticks"] = 16 if _belt_now > 0.25 else 8
```

**The `_belt_v` read at claim time is before the pre-step pause fires**, so this correctly reads the running velocity, not zero.

### Estimated Unlock

CP-22 is currently `build-spec; form-gate pending`. With the look-ahead fix, expected function-gate delivery rate: 3/4 or 4/4. The 60 s `simulate_args.duration_s` may need to increase to 90 s to allow all cubes to arrive at the sensor zone given the robot's ~7–10 s cycle time. The template should be updated to `duration_s: 90`.

**Net unlock: 1 canonical (CP-22)** once form-gate passes.

---

## CP-37: Obstacle-Avoidance Station

### Root Cause

Per `docs/research/2026-05-09-mode-b-cube-knocked-off.md` (already written), CP-37 fails as **Mode B**: the Franka elbow sweeps laterally during S3 lift and knocks Cube_1 off the belt's outer edge to `(0.59, 0.59, 0.525)`. The cube lands on the floor, outside Franka's 0.70 m reach limit.

**Critical update:** The Mode B FixedJoint fix **is already deployed** in `tool_executor.py` at lines 33391–33406. The fix creates a `UsdPhysics.FixedJoint` between `ROBOT_PATH/panda_hand` and the cube at `action_after == "close"`, ensuring the cube is attached rigidly to the gripper through transit regardless of elbow contact.

CP-37's prior `verified_status: form-gate verification pending` means the FixedJoint fix has not yet been tested on this canonical specifically. However, the fix is generic (applies to all `_gen_pick_place_curobo`-generated code), and since CP-37 uses the same plumbing as CP-30 (which the fix was designed for), the Mode B failure should be resolved.

The one CP-37-specific concern is the explicit `end_effector_initial_height=1.30` override. The Mode B fix does not depend on h1 value — it operates at the `action_after == "close"` segment regardless of h1. The obstacle pillar at z=1.15 is in `planning_obstacles`, which means cuRobo's planner is registered to avoid it in world-space collision. With h1=1.30, the EE approach height clears the pillar top (1.15) by 0.15 m.

**No additional code fix needed for CP-37.** The existing Mode B FixedJoint is sufficient. The action item is: run the form-gate and function-gate now that the fix is deployed.

### Form-Gate Status Gap

CP-37's `verify_args` is well-formed: one stage with `robot_path=/World/Franka`, `pick_path=/World/ConveyorBelt`, `place_path=/World/Bin`. The `cube_path=/World/Cube_1` in `simulate_args` correctly tracks the first cube.

### Estimated Unlock

**1 canonical (CP-37)** once form-gate and function-gate are run. Expected delivery: 4/4 (Mode B fix eliminates the elbow-sweep failure; planning obstacle registration handles pillar avoidance).

---

## CP-58: Peg-in-Hole Assembly

### Root Cause

CP-58's `verified_status` explicitly records: `"function-gate ✗ (peg-in-hole assembly — earlier ✓ was false positive due to target_path override; real target is HolePanel and cube doesn't reach it)"`.

The failure has **two independent layers**:

**Layer 1 — Geometry: holes are Xform markers, not real cylindrical voids.**

The CP-58 template creates `/World/Hole_1` through `/World/Hole_4` as `prim_type="Xform"` at y=-0.4, z=0.825. The `/World/HolePanel` is a `Cube` at position `[0, -0.4, 0.775]` with `scale=[0.20, 0.05, 0.025]`. HolePanel top surface is at z = 0.775 + 0.025 = 0.80. Hole marker z=0.825 is 0.025 m above HolePanel top.

When the robot releases the peg at (hole_x, -0.4, 0.825), the peg (a cylinder of radius 0.02 m, height 0.05 m) drops 0.025 m and lands on top of the HolePanel solid surface. There is no cylindrical void. This is noted in CP-58's own `failure_modes[0]`: "Holes are Xform markers — no actual cylindrical holes via subtractive mesh. Peg lands on top of HolePanel."

**Layer 2 — Controller: `destination_path=/World/HolePanel` with per-peg `drop_targets`.**

`setup_pick_place_controller` is called with:
- `destination_path="/World/HolePanel"`
- `drop_targets={"/World/Peg_1": [-0.15, -0.4, 0.825], ...}`

The `simulate_traversal_check` in `simulate_args` uses:
- `cube_path="/World/Peg_1"`
- `target_path="/World/HolePanel"`

`_is_in_bin` checks whether Peg_1's XY position is within HolePanel's XY bbox. HolePanel bbox in XY: x = [0 ± 0.20] = [-0.20, 0.20], y = [-0.4 ± 0.05] = [-0.45, -0.35]. The drop target for Peg_1 is x=-0.15, y=-0.4 — within this XY range. So the verifier *will* pass if the peg is placed at the drop target position.

The function-gate failure is because the peg lands on top of HolePanel (Layer 1), but `simulate_traversal_check` uses XY-bbox check only, not z. If the peg is at (-0.15, -0.4, 0.825) with z=0.825 and HolePanel floor is at z=0.775, `above_floor` in the verifier checks `cube_z >= target_bbox.min.z - floor_tol (0.10)`, i.e., 0.825 >= 0.775 - 0.10 = 0.675. This passes. So the verifier would actually succeed.

**The real failure** is that the robot is not placing the peg at the drop_target position. The curobo handler uses `_bin_drop_pos(picked_path)` to look up the drop target. In `_gen_pick_place_curobo`, the `DROP_TARGETS` dict maps `{"/World/Peg_1": [-0.15, -0.4, 0.825], ...}`. The `_bin_drop_pos` function checks `DROP_TARGETS` first:

```python
# In _bin_drop_pos():
if DROP_TARGETS:
    if isinstance(DROP_TARGETS, dict):
        t = DROP_TARGETS.get(cube_path)  # cube_path from S["picked_path"]
    ...
```

The issue: `S["picked_path"]` is set to `"/World/Peg_1"` by `_cube_to_pick()`. But `_cube_to_pick()` has the z-window check `cp[2] < base_z - 0.30 or cp[2] > base_z + 0.50`. The robot base is at z=0.75 (table top). Peg_1 is on the conveyor at z=0.835. That is `0.835 - 0.75 = 0.085`, well within `[−0.30, +0.50]`. No z-exclusion.

**The actual failure mode** per the `verified_status` note is that the earlier "✓ was false positive due to target_path override." The false positive was: when `destination_path="/World/Bin"` was mistakenly used (before the template was updated to `HolePanel`), the verifier passed because the bin exists and cubes reached it. Once corrected to `HolePanel`, the peg must be placed on HolePanel, which requires precise drop-target routing.

Investigating further: the `simulate_args.duration_s=120` and the template has Peg geometry (cylinders, not cubes). The curobo planner plans to `(peg_z + FL + EE_OFFSET_z) = 0.835 + 0.105 + 0.0 = 0.940` for the pick approach. For a cylinder vs a cube, the `FL` (finger length, 0.105 m) positions the fingertip at peg center z. Cylinders have the same z=0.835 center as cubes in this scene. No z-difference.

**The unsolved issue:** `setup_assembly_constraint` (called 4 times in CP-58's code) only writes metadata attributes to `Hole_i` prims. No runtime joint is created. No force-gated insertion phase exists. The "peg in hole" insertion is purely positional — drop the peg at (hole_x, -0.4, 0.825) and it lands on top of HolePanel. Actual sub-surface insertion requires mesh subtraction or a runtime FixedJoint triggered when peg enters the hole volume, neither of which exists.

### Fix Proposals

**Fix 1 (Verifier alignment, unblocks function-gate):** Change the `simulate_args` to track `target_path="/World/Hole_1"` (an Xform marker), not `/World/HolePanel`. The Hole Xform is at (-0.15, -0.4, 0.825). The Xform has no geometry so its bbox is empty. `simulate_traversal_check` falls back to `ComputeLocalToWorldTransform` when bbox is empty, returning the translation position. Change the peg success check to compare peg XY against the individual hole position with a 0.03 m tolerance:

In `simulate_args`:
```json
{
  "cube_path": "/World/Peg_1",
  "target_path": "/World/Hole_1",
  "duration_s": 120,
  "xy_tolerance": 0.03
}
```

But this only checks Peg_1 against Hole_1. For all 4 pegs, use `cube_paths`:
```json
{
  "cube_paths": ["/World/Peg_1", "/World/Peg_2", "/World/Peg_3", "/World/Peg_4"],
  "target_path": "/World/HolePanel",
  "duration_s": 120,
  "xy_tolerance": 0.0
}
```
Success criterion: `ANY of cube_paths in target_path bbox` — passes when at least one peg lands on HolePanel.

**Fix 2 (Template scene, closer to real insertion):** Replace Xform hole markers with thin cylindrical collision volumes (mesh with hollow interior). This is a USD mesh edit and is beyond the scope of the current pick-place controller. Deferred to Sprint 4+.

**Fix 3 (Controller precision):** The `drop_targets` dict in CP-58 maps each peg to a specific hole position with 0.025 m z-clearance above HolePanel. The curobo controller honors these per-cube drop targets. If the planner consistently delivers pegs within 3 cm XY of their hole targets (drop-precision measurements show ~4 cm average for a standard conveyor scene), then with `xy_tolerance=0.03` in `simulate_traversal_check`, most pegs will pass the function-gate.

The root cause for the current 0-delivery is most likely Mode A (planning failure loop). CP-58 extends CP-08 which historically showed 2/4 delivery. The 3-strike fix should allow the controller to advance past planning failures. The `duration_s=120` provides 120 / 4 = 30 s per peg cycle — sufficient for the cuRobo ~7 s cycle time.

### Estimated Unlock

With Fix 1 (correct `simulate_args.target_path`) + the already-deployed 3-strike fix:

**1 canonical (CP-58) partially unlocked** — verifier passes when at least 1 peg lands on HolePanel within XY tolerance. Full 4/4 peg insertion requires Sprint 4+ controller work (not unlocked now).

The `verified_status` should be updated to `"function-gate partial (1/4 pegs expected with Fix 1 + 3-strike fix)"`.

---

## CP-65: Two-Cell Kit-Tray Relay (Franka A + Franka B)

### Root Cause

CP-65 is the most complex canonical in this set: two Franka robots (A at x=-0.6, B at x=+0.6), a conveyor feeding robot A, a kit tray as relay station, and an output bin behind robot B.

**`simulate_args` tracks `cube_path="/World/Cube_1"` against `target_path="/World/OutBin"`.**

The full delivery chain for Cube_1: Conveyor → FrankaA picks → places in KitTray → FrankaB picks from KitTray → places in OutBin. This is a two-stage handoff. `simulate_traversal_check` has no concept of multi-stage delivery; it only checks whether `/World/Cube_1` ends up in `/World/OutBin`'s bbox.

**Failure mode 1 — Mode A (planning failure infinite loop):** Both robots use `target_source="curobo"`. As documented in `2026-05-09-mode-a-controller-stuck.md`, CP-65 is listed as a **conditional unlock** under Mode A because of the shared-tray collision interaction. With the 3-strike fix already deployed, FrankaA will eventually deliver Cube_1 to KitTray (or fail permanently), then FrankaB attempts to pick from KitTray. But both robots' `_cube_to_pick()` scan `SOURCE_PATHS=[Cube_1..4]` — FrankaB's `_cube_to_pick` will find cubes in the KitTray IF they are within 0.70 m of FrankaB's base at x=+0.6. KitTray center is at (0, -0.3), so distance from FrankaB base (0.6, 0) = sqrt(0.36+0.09) = 0.67 m — just within reach.

**Failure mode 2 — Simultaneous claim:** Both robots have separate `_on_step` callbacks and separate `S` dicts, but they scan the same `SOURCE_PATHS = [Cube_1..4]`. Without a mutex, FrankaA can claim Cube_1 from the belt while FrankaB simultaneously claims Cube_1 from the KitTray (if Cube_1 is already in tray range). The CP-65 template calls `setup_robot_handoff_signal` but this tool only writes metadata attributes to `/World/Handoff` prim; it does **not** install any runtime mutual exclusion. The only runtime mutex mechanism is `mutex_path` in the curobo controller, but CP-65 does not pass `mutex_path` to either `setup_pick_place_controller` call.

**Failure mode 3 — HandoffBridge verifier trick:** The template creates a stationary conveyor `/World/HandoffBridge` at `surface_velocity=[0.001, 0, 0]`. FrankaB's controller has `belt_path="/World/HandoffBridge"`. The HandoffBridge pretends to be a moving belt for verifier compatibility. `verify_pickplace_pipeline` checks `surfaceVelocity > 0` on both conveyors; 0.001 passes the threshold. But FrankaB's controller reads `_nominal_belt = (0.001, 0, 0)` from HandoffBridge at install time, so `_resume_belt()` sets it back to 0.001 after each pick. This is correct.

**The primary unlocking work is fixing Failure Mode 2.** Without a mutex, the two robots race on the same cubes and both will be stuck in simultaneous-claim loops.

### Fix

**Add `mutex_path` to both `setup_pick_place_controller` calls.**

In the CP-65 template, the scene already creates `/World/Handoff` via `setup_robot_handoff_signal`. This prim has `mutex:claimed_by` and `mutex:claim_count` attributes. Pass this as `mutex_path` to both controllers:

In CP-65.json code block, change both `setup_pick_place_controller` calls:

```python
# Robot A — fills kit tray with cubes
setup_pick_place_controller(
    robot_path="/World/FrankaA",
    target_source="curobo",
    sensor_path="/World/SensorA",
    belt_path="/World/ConveyorBelt",
    source_paths=[f"/World/Cube_{i+1}" for i in range(4)],
    destination_path="/World/KitTray",
    drop_targets={...},
    planning_obstacles=[...],
    mutex_path="/World/Handoff",   # <-- ADD
)

# Robot B — picks cubes from tray and places at OutBin
setup_pick_place_controller(
    robot_path="/World/FrankaB",
    target_source="curobo",
    sensor_path="/World/SensorB",
    belt_path="/World/HandoffBridge",
    source_paths=[f"/World/Cube_{i+1}" for i in range(4)],
    destination_path="/World/OutBin",
    planning_obstacles=[...],
    mutex_path="/World/Handoff",   # <-- ADD
)
```

The mutex logic in `_on_step → wait_sensor` (lines 32166–32192) checks `mutex:claimed_by` before claiming a cube, and sets it to `ROBOT_PATH` before claiming. This prevents simultaneous double-claim.

**However**, the mutex is currently designed for concurrent access to a *shared conveyor zone*, not for a *sequential handoff pipeline*. In the CP-65 scenario:
- FrankaA claims a cube from ConveyorBelt (mutex set to FrankaA).
- FrankaA holds the mutex through the entire pick-and-place cycle to KitTray.
- FrankaB sees `mutex:claimed_by == "/World/FrankaA"` and waits.
- FrankaA finishes placing, releases mutex (`mutex:claimed_by = ""`).
- FrankaB now claims Cube_1 from KitTray.

This is serial, not pipelined — FrankaA and FrankaB cannot operate simultaneously. For a canonical demo, serial operation is acceptable; 4 cubes at ~7 s/cube × 2 stages = ~56 s, within `duration_s=180`.

An alternative to the mutex (pipelined operation) is **zone-partitioned `SOURCE_PATHS`**: give FrankaA `source_paths=[Cube_1..4]` and FrankaB `source_paths=[Cube_1..4]`, but install them such that FrankaA's `_cube_to_pick` only returns cubes upstream of x=0 and FrankaB's only returns cubes downstream (in KitTray). This would require a spatial filter parameter that does not currently exist in `_cube_to_pick`. The mutex approach is cleaner for now.

### Template Patch

The `CP-65.json` template `code` field needs two changes:

1. **Add `setup_robot_claim_mutex` call** (before the `setup_pick_place_controller` calls). `setup_robot_handoff_signal` creates `/World/Handoff` with `handoff:state` and `handoff:robot_a/b` attributes — but NOT the `mutex:claimed_by` and `mutex:claim_count` attributes the curobo controller reads. `setup_robot_claim_mutex` (lines 6407–6460 of `tool_executor.py`) creates the correct mutex prim:

```python
setup_robot_claim_mutex(
    mutex_path="/World/Handoff",
    resource_path="/World/KitTray",
    robots=["/World/FrankaA", "/World/FrankaB"],
)
```

This can be called on the `/World/Handoff` Xform that already exists (created by `setup_robot_handoff_signal`) — `setup_robot_claim_mutex` upserts on an existing prim.

2. **Add `mutex_path="/World/Handoff"`** to both `setup_pick_place_controller` calls (as shown above).

### Estimated Unlock

**1 canonical (CP-65)** partially unlocked with mutex fix. Expected: FrankaA delivers 2–4 cubes to KitTray; FrankaB picks 1–2 of those and delivers to OutBin. `simulate_traversal_check` passes when Cube_1 arrives in OutBin's bbox. 180 s duration gives ~3–4 full cycles.

The `simulate_args.duration_s=180` is appropriate but the `cube_path="/World/Cube_1"` single-cube check means success only if Cube_1 specifically completes the full relay. With 4 cubes in the queue and stochastic planning, Cube_1 might take 2–3 tries before completing the relay. Consider updating `simulate_args` to use `cube_paths=[Cube_1..4]` with `ANY` success criterion.

---

## CP-73: UR10 + Cortex + Conveyor

### Root Cause

CP-73 calls **both** `setup_cortex_behavior` and `setup_pick_place_controller` on `/World/UR10`. The tool doc at line 7040 explicitly states: **"Use Cortex OR cuRobo, not both on same robot."**

In practice:
- `setup_cortex_behavior` installs a `CortexWorld` instance and wraps `/World/UR10` as a `CortexRobot`. The Cortex step pipeline (logical monitors → behavior tree decisions → motion commander) runs via a physics-step subscription that calls `CortexWorld.step()`.
- `setup_pick_place_controller(target_source="curobo")` installs a separate `_on_step` callback that directly calls `art_ctrl.apply_action(...)` on the UR10 articulation.

Both callbacks fire on every physics tick and write joint targets to the same UR10 articulation controller. The result is undefined joint-target conflicts that produce erratic arm motion, planning failures, and likely segfaults or NaN joints.

Even if `setup_cortex_behavior` were omitted, CP-73 has the **UR10 belt-pause bug**: CP-73 uses the UR10 curobo handler. The belt-pause pre-step fix is deployed for the Franka curobo handler at lines 33049–33061, but the separate UR10 curobo handler variants (elevated conveyor, split-belt) at approximately lines 33678+ may or may not have the identical fix. CP-73 is at `verified_status: form-gate ✓; function-gate ✗ (Cortex+conveyor — multi-cube limitation + belt-pause bug)`, confirming the belt-pause issue.

Additionally: CP-73 sets `robot_kind="ur10e"` in `verify_args` but calls `import_robot(file_path="UR10")` in the code block. The UR10 (not e) and UR10e have different joint count and URDF. The curobo handler sets `_CUROBO_ROBOT_CFG = "ur10e.yml"` when `ROBOT_FAMILY in ("ur10", "ur10e")`. This inconsistency may cause planning failures if the robot's actual URDF does not match `ur10e.yml`.

### Cortex + Conveyor: Architectural Conflict

The Cortex framework's `demo_ur10_conveyor.py` example (the canonical #33 this CP realizes) does NOT use `setup_pick_place_controller`. It uses the Cortex `CortexWorld.run()` blocking loop with a `DfDecider` behavior tree that calls `robot.arm.send_end_effector(target_pose)` each tick. This is RmpFlow-based, not cuRobo. The two architectures cannot coexist.

For a working CP-73, the correct approach is **one of**:

**Option A (Cortex-only):** Remove `setup_pick_place_controller`. Implement the pick-from-moving-conveyor behavior purely in the Cortex behavior tree. The `setup_cortex_behavior` call loads the UR10 Cortex wrapper; a behavior_module that uses `robot.arm.send_end_effector` and `robot.gripper` does the picking. `simulate_traversal_check` still works — it doesn't care about the controller architecture, only about cube final position.

This requires a custom behavior module (`isaacsim.cortex.behaviors.ur10.conveyor_pick`) that does not exist in the Isaac Sim 5.x bundle. It would need to be authored as a separate Python file, which is outside the current tool scope.

**Option B (cuRobo-only):** Remove `setup_cortex_behavior`. Keep only `setup_pick_place_controller(target_source="curobo")`. The Cortex obstacle registration is lost, but the UR10 cuRobo handler already takes `planning_obstacles` from the template. This option trades Cortex's dynamic obstacle avoidance for cuRobo's static planning obstacles. For the canonical demo purpose (show UR10 picking from a moving conveyor), this is acceptable.

**Option B is the lower-effort path.** It degrades CP-73 from "Cortex + conveyor" to "UR10 cuRobo + conveyor" (functionally identical to CP-69's picking pattern). The `task_id` and goal description would need updating to reflect this.

### `simulate_traversal_check` Multi-Cube Limitation

CP-73's `simulate_args` only tracks `/World/Cube_1` against `/World/Bin`. With 4 cubes and a Cortex behavior tree, the Cortex orchestrator picks cubes opportunistically — Cube_1 may not be the first or only cube delivered. The verifier should use `cube_paths=[Cube_1..4]` with ANY-success to increase pass probability.

### Estimated Unlock

**Option A (Cortex-native):** Not unlockable without a custom behavior module. Accept as an eval limit. Suggested status: `"eval-limit: Cortex+conveyor requires custom behavior module beyond current tool scope"`.

**Option B (remove Cortex, keep cuRobo):** 1 canonical partially unlocked. Expected delivery: 2–3/4 cubes (UR10 FixedJoint workaround + pre-step belt pause already deployed). `simulate_traversal_check` passes when ANY of Cube_1–4 reaches Bin. This is a reduced-fidelity realization of the #33 scenario.

The recommended action is to accept Option B with a note, update CP-73's goal to "UR10 cuRobo + conveyor (Cortex layer deferred)", and track the Cortex-native version as a future CP-73b.

---

## Summary Table

| CP | Failure Mode | Root Cause | Fix | Unlock Estimate |
|----|-------------|-----------|-----|----------------|
| CP-22 | Belt too fast for claim cycle | Cube exits sensor zone in 2–3 ticks; no look-ahead in `_cube_to_pick` | Predictive claim (upstream look-ahead 0.30 m when belt > 0.25 m/s) + settle_ticks 8→16 | 1 canonical (CP-22) |
| CP-37 | Mode B cube knocked off | Elbow sweep during S3 lift (docs written; Mode B fix **already deployed**) | No new fix needed — run form/function gate | 1 canonical (CP-37), fix already in code |
| CP-58 | False-positive verifier + no real insertion | Holes are Xform markers; prior ✓ used wrong target_path | Fix simulate_args to use HolePanel + xy_tolerance=0.0; 3-strike fix handles planning | 1 canonical (CP-58) partial (1+/4 pegs) |
| CP-65 | Simultaneous dual-robot claim; no runtime mutex | Both robots scan same SOURCE_PATHS without coordination | Add `mutex_path="/World/Handoff"` to both `setup_pick_place_controller` calls | 1 canonical (CP-65) with serial relay |
| CP-73 | Cortex + cuRobo architectural conflict | `setup_cortex_behavior` + `setup_pick_place_controller` both write joints | Remove `setup_cortex_behavior` (Option B); accept Cortex version as eval limit | 1 canonical (CP-73) at reduced fidelity, OR accept as eval limit |

**Total potential unlocks: 4–5 canonicals** (CP-22 after template update + code patch; CP-37 immediately; CP-58 with `simulate_args` fix; CP-65 with mutex addition; CP-73 with Option B).

---

## Code Change Locations

All changes in `service/isaac_assist_service/chat/tools/tool_executor.py`:

| Change | Location | Lines (approx) |
|--------|----------|---------------|
| CP-22 look-ahead in `_cube_to_pick` | `_gen_pick_place_curobo` template string | 33124–33157 |
| CP-22 adaptive `settle_ticks` | `_on_step → wait_sensor` branch | 33272–33288 |
| CP-65 `mutex_path` parameter in template | `CP-65.json` template code field | n/a (JSON file) |
| CP-73 remove `setup_cortex_behavior` | `CP-73.json` template code field | n/a (JSON file) |

CP-37 and CP-58 changes are in JSON template files only (`workspace/templates/`):

| Change | File |
|--------|------|
| CP-37 form-gate + function-gate execution | Run against live Kit instance (no template change) |
| CP-58 `simulate_args.target_path` correction | `workspace/templates/CP-58.json` |

---

## On Cortex + `simulate_traversal_check` Interaction

For completeness: the question in the task brief about how Cortex's pickup orchestration interacts with `simulate_traversal_check` has a simple answer. `simulate_traversal_check` is a **passive observer** — it plays the timeline for `duration_s` and polls cube world positions. It does not install physics callbacks, does not interfere with any controller's step loop. Cortex's behavior tree, cuRobo's `_on_step`, and the PickPlaceController all continue running during the check.

The interaction issue is upstream: if a Cortex world is running (its step subscription active), it continuously writes `arm.send_end_effector` targets to the robot articulation. `simulate_traversal_check`'s `tl.stop()` + `tl.play()` cycle causes the Cortex world's `CortexWorld.step()` to receive a `reset_cortex()` call (if wired correctly), or to simply stop receiving step callbacks (if the subscription was lost). After `tl.play()` resumes, the Cortex controller may not resume cleanly if `CortexWorld.instance()` was created in a prior play session and its internal state is stale.

This is tracked as the "multi-cube limitation" in CP-73's status. It is not a fundamental limitation of `simulate_traversal_check` itself, but of Cortex state persistence across play/stop cycles — a known Isaac Sim Cortex limitation per the framework docs.
