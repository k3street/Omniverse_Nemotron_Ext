# Vision-Gated and Special Target-Source Canonical Failures
**Date:** 2026-05-09
**Scope:** CP-40, CP-48, CP-59, CP-60, CP-62

---

## Summary

Five canonicals are pending form-gate/function-gate clearance due to four distinct failure
categories: (1) spline controller missing scene-collision detection, (2) vision pipeline
failing on overlapping/heap geometry, (3) no-robot scene using the robot-centric verifier,
(4) gantry slider created as a physically independent prim — the robot is not parented to
it. Estimated unlock: 3.5 of 5 canonicals with targeted fixes.

---

## CP-40 — Spline Target-Source

### Controller path

`setup_pick_place_controller(target_source='spline')` dispatches to `_gen_pick_place_spline`
at line 31628 of `tool_executor.py`. This is a code-gen handler that produces a self-contained
Kit-side script. The spline script uses Lula IK for warm-start, `scipy.CubicSpline` across
6 pre-planned Cartesian waypoints, and a `physics-step` callback subscription stored in
`builtins._spline_pp_sub`.

The verifier detects the subscription via `_controller_installed` (line 3673): it looks for
`_spline_pp_sub` in builtins when the pipeline has exactly one robot (single-robot untagged
path). For CP-40 this is satisfied — one Franka, so the form-gate's controller_installed
flag resolves correctly.

### Unique failure mode

The spline planner does **no collision-awareness** (stated explicitly in its docstring at
line 31650: "No collision-awareness — uses Cartesian lift-and-transit to avoid obstacles").
It relies on the 6 hand-tuned waypoints being collision-free by construction. The CP-40
scene places the Franka at `[0, 0, 0.75]` with a table at `z=0.375` and a conveyor at
`z=0.78`. The spline `[2] lift` waypoint brings the EE to `EE_INITIAL_HEIGHT` above pick
xy before transit. If `EE_INITIAL_HEIGHT` is not large enough relative to the table surface,
the spline trajectory brushes the table on the `[3] transit_over_drop` segment.

CP-40's `failure_modes` field acknowledges this: "scipy.CubicSpline interpolation can
produce trajectories that brush table — without scene-collision check (cuRobo has it,
spline doesn't)."

A secondary issue is cycle time: `spline_waypoint_dt` defaults to 1.5s × 6 segments = 9s
per cube. The `simulate_args.duration_s` is set to 60s, which gives room for roughly 6
cycles. Per CP-40's `extension_notes`, spline historically delivers 3/4 cubes vs cuRobo's
4/4. The 60s window may not be sufficient for all 4 cubes if cycle slips occur.

### Fix proposal

1. **Increase `EE_INITIAL_HEIGHT`** in the spline generator from the default to at least
   0.25m above the tallest obstacle in the scene (table top at z=0.75). Set `lift_h=0.25`
   and `approach_h=0.25` in the `_gen_pick_place_spline` call at line 28795, or add a
   `min_lift_height` clamp inside the code-gen string using:
   ```python
   EE_INIT_H = max(EE_INIT_H_OVERRIDE or 0.0, 0.25)
   ```
   This prevents the transit waypoints from dipping below the obstacle clearance plane.

2. **Extend `simulate_args.duration_s`** from 60 to 90 in CP-40's template to give the
   slower spline cycle enough wall-time to complete 4 picks.

3. No spline-specific form-gate fix needed — the `_spline_pp_sub` detection path is already
   correct for single-robot scenes.

---

## CP-48 — Vision Inspect-and-Reject

### Controller path

`setup_pick_place_with_vision` (line 6068) is a composite handler that:
1. Calls `add_vision_classifier_gate` (line 5910) — captures viewport, runs Gemini VLM
   detection, matches detections to cube world positions by left-to-right x-sort heuristic.
2. Applies Semantics_color on each cube via Kit RPC.
3. Calls `setup_pick_place_controller(target_source='curobo', color_routing=destination_map)`
   — standard cuRobo controller with vision-derived routing.

The classification-to-cube assignment at lines 6025–6038 is positional: cubes sorted by
world-x, detections sorted by image-x `point[1]`, paired in order. No geometric projection
— it is explicitly noted as "v1 heuristic" in the docstring (line 5948).

### Unique failure mode

The failure mode is **not a code bug but a geometric constraint**: the 5 cubes (4 green + 1
red) are placed at x = [-1.0, -0.7, -0.4, -0.1, +0.2] on the conveyor at y=0.4. The camera
is at `[0, 1.5, 1.5]` looking at `[0, 0, 0.8]`. From this camera angle, the cubes are
separated well in image space by x-position. The VLM should return 5 detections for distinct
colored objects.

The primary failure risks:

1. **Gemini API 0-detection edge case**: if the viewport hasn't settled (no `app.update()`
   calls after the conveyor starts), the scene may render partially. The `flush_code` block
   at line 5971 runs 60 `app.update()` calls before capture, mitigating this — but only if
   Kit RPC exec_sync completes in under 15s timeout.

2. **Matching order fragility**: the x-sort heuristic assumes cubes project strictly
   left-to-right in image space. With the camera at `[0, 1.5, 1.5]` (positive y, high z)
   looking toward the origin, the image x of a cube at world-x=-1.0 is the leftmost
   detection. This holds as long as no two cubes have nearly identical projected x — at
   y=0.4 they are all at the same world-y, so projected x differences are proportional to
   world-x differences. The 0.3m spacing should give sufficient separation.

3. **destination_map key matching**: CP-48 calls `setup_pick_place_with_vision` with
   `destination_map={"green": "/World/GoodBin", "red": "/World/RejectBin"}`. The detected
   labels from Gemini are expected to be "green cube" and "red cube". The stripping logic
   at line 6127 (`short = label.lower().replace(" cube", "").strip()`) converts these
   correctly. The fuzzy matching at lines 6047–6052 provides a second fallback. This path
   looks robust.

4. **False root cause (form-gate)**: CP-48's `verified_status` says "form-gate verification
   pending". The most likely form-gate failure is that **Gemini is called at canonical
   build time** (when the scene exists in Isaac Sim but the Timeline is stopped), so the
   rendered image shows the cubes in their initial authored positions before any physics
   settling. This is the correct behavior — that is by design. The actual risk is whether
   the green/red materials are rendered before capture. `assign_material` sets the USD
   material binding; the 60-frame flush should push the renderer to apply it.

### Fix proposal

1. **Increase flush frames** from 60 to 120 in the `flush_code` block (line 5972) to ensure
   materials and mesh geometry are fully evaluated before the Gemini API capture. The cost
   is negligible (sub-second).

2. **Add viewport camera switch before flush**: the `set_viewport_camera` call is attempted
   at line 5962 but is wrapped in a non-fatal `try/except`. If it silently fails (e.g., the
   viewport manager isn't initialized), the capture uses the default viewport which may not
   show the cubes. Make the camera switch explicit with a retry loop or a post-switch
   app.update() confirmation before the flush.

3. No controller-side change needed — the cuRobo + color_routing path is verified working
   from CP-18/CP-47.

---

## CP-59 — Vision Dual-Robot

### Controller path

Same as CP-48: `setup_pick_place_with_vision` → `add_vision_classifier_gate` → cuRobo +
color_routing. Only robot A (`/World/FrankaA`) gets `setup_pick_place_with_vision` in the
template. Robot B is intentionally omitted to avoid a second Gemini API call.

### Unique failure mode

CP-59's `verified_status` is the most informative of the five: "Gemini returned 0 detections
(heap has overlapping cubes; vision doesn't separate them well)."

The heap layout in CP-59 places 4 cubes in a 0.10m-radius spread:
- Cube_r1: `[-0.05, 0.30, 0.86]`
- Cube_b1: `[ 0.05, 0.30, 0.86]`
- Cube_r2: `[-0.04, 0.36, 0.86]`
- Cube_b2: `[ 0.04, 0.24, 0.86]`

The x-spread is only 0.10m (−0.05 to +0.05). With the camera at `[0, 0.3, 1.5]` looking
straight down at `[0, 0.3, 0.85]` (overhead), the 4 cubes project into a 0.10m × 0.12m
cluster. In image pixels (assuming ~500px height FOV ~0.6m wide at that z), the cluster
spans roughly 80px × 100px. At 5cm cube size, individual cubes occupy about 40px each with
10px gaps. VLMs (Gemini) treat this as one colored mass, not 4 distinct objects, and return
0 bounding box detections because no single contiguous region matches "red cube" or "blue
cube" as a free-standing object.

The `add_vision_classifier_gate` error at line 6120 fires:
```python
return {"type": "error",
        "error": "Vision returned 0 detections — check camera placement and scene visibility."}
```
This kills the composite tool before any controller install, so CP-59 fails at form-gate.

### Fix proposal

**Option A — Spread cubes**: Replace the 0.10m-radius heap with a 0.30m-radius spread that
matches the camera's FOV at `[0, 0.3, 1.5]`. Place cubes at:
- Cube_r1: `[-0.15, 0.15, 0.86]`
- Cube_b1: `[ 0.15, 0.15, 0.86]`
- Cube_r2: `[-0.15, 0.45, 0.86]`
- Cube_b2: `[ 0.15, 0.45, 0.86]`

This gives 0.30m separation in both x and y, matching the CP-47/48 spacing that is confirmed
working. The semantic requirement (#14 "shared heap") is still satisfied — the cubes are on
the shared surface, just with sufficient visual separation.

**Option B — Camera elevation**: Raise camera from `z=1.5` to `z=2.5` and widen the FOV
or zoom out, so the same 0.10m spread covers more pixels. However, this changes scene geometry
and may break reach calculations for both robots.

Option A is lower risk and aligns with the CP-59 template's own `failure_modes` note:
"Could replace heap with spread cubes for reliable vision."

Additionally, the template only installs robot A's controller. Robot B needs a second
`setup_pick_place_with_vision` call (second Gemini API call) for the form-gate to pass the
`controller_installed` check for `/World/FrankaB`. The `verify_args.stages` only checks
FrankaA, so form-gate technically passes on that point — but the scene is incomplete.

---

## CP-60 — Recirculation Loop (No Robot)

### Controller path

CP-60 has **no robot**. `create_recirculation_loop` (line 7311) creates an Xform parent at
`/World/Loop` with 4 child conveyor segments (Top/Right/Bottom/Left). No
`setup_pick_place_controller` is called. No physics subscription is registered in builtins.

### Failure mode

The `verify_args` in CP-60's template is:
```json
{
  "stages": [
    {"robot_path": "/World/Loop", "pick_path": "/World/Loop/Top",
     "place_path": "/World/Loop/Right", "robot_kind": "n/a"}
  ],
  "cube_path": "/World/Cube_1"
}
```

`verify_pickplace_pipeline` processes this as if `/World/Loop` is a robot. The handler at
line 3704 resolves reach:
```python
reach = ROBOT_REACH.get(rk) if rk in ROBOT_REACH else ROBOT_REACH.get('default', 0.8)
```
`rk` = `"n/a"` is not in `_ROBOT_REACH_M` (line 3241), so it falls back to
`ROBOT_REACH.get('default', 0.8)` = **0.8m**. The `/World/Loop` Xform's bbox center is
at `[0, 0, 0.78]` — its "robot base" is computed by `_robot_base_pos` as `[0, 0, 0.78]`.
The pick path `/World/Loop/Top` at `[0, 0.3, 0.78]` is 0.30m away — within 0.8m reach, so
the reach check passes.

The **fatal check is `controller_installed`**. At line 3728:
```python
if rp:
    ci = _controller_installed(rp, _n_robots)
    stage_result['controller_installed'] = ci is not None
    if ci is None:
        issues.append(f'[controller_installed] ...')
```

`_controller_installed("/World/Loop", 1)` looks for:
- `_curobo_pp_sub__World_Loop` — not present
- `_builtin_pp_sub__World_Loop` — not present
- (single-robot fallback) `_spline_pp_sub`, `_native_pp_sub`, `_diffik_pp_sub`, `_osc_pp_sub` — none present

Result: `controller_installed=False` → `[controller_installed]` issue added → `pipeline_ok=False`.

The `ok` computation at line 3828 includes:
```python
and not any(i.startswith('[controller_installed]') for i in issues)
```

CP-60 has no robot and no controller. The verifier cannot pass because it was designed for
robot pick-place pipelines, not pure-conveyor loops.

For `simulate_traversal_check`, the function-gate check in CP-60 asks whether `/World/Cube_1`
reaches `/World/Loop/Right` in 30s. This could actually succeed — the cube starts on the Top
segment and should be carried by friction toward the Right segment. But the form-gate fails
first, blocking the function-gate from even running.

### Fix proposal

**Short-term (template-level)**: Remove the `robot_path` from CP-60's `verify_args.stages`
and instead use an empty string or omit the `stages` key entirely. Let the form-gate skip the
pipeline check for no-robot scenes. Alternatively, add a `skip_controller_check: true` field
to the stage spec:

```json
"verify_args": {
  "stages": [
    {"robot_path": "", "pick_path": "/World/Loop/Top",
     "place_path": "/World/Loop/Right", "robot_kind": "conveyor_only",
     "skip_controller_check": true}
  ],
  "cube_path": "/World/Cube_1"
}
```

**Medium-term (verifier-level)**: Add a guard in `_handle_verify_pickplace_pipeline` at line
3727: if `rp` is empty **or** `robot_kind` is in `{"n/a", "conveyor_only", "none"}`, skip
the `controller_installed` check for that stage. The `if rp:` guard already handles the empty
string case — just update the template to use `robot_path: ""`.

The simplest working fix is: change `verify_args.stages[0].robot_path` from `"/World/Loop"`
to `""` in the CP-60 template. The reach check is skipped when `rpos is None` (line 3712
short-circuits), the controller check is skipped by the `if rp:` guard at line 3728, and
`pipeline_ok` becomes true if no other issues exist.

---

## CP-62 — Linear-Axis Gantry with Surface Gripper

### Controller path

CP-62 calls three tools:
1. `create_linear_axis_robot` (line 7359) — creates `/World/GantrySlider` Cube + prismatic
   joint to world.
2. `surface_gripper` (line 6463) — adds `IsaacSurfaceGripper` schema under
   `/World/Franka/panda_hand`. Falls back to `omni.kit.commands.execute("CreateSurfaceGripper")`
   if the Franka USD has no Gripper variant set.
3. `setup_pick_place_controller(target_source='curobo')` — standard cuRobo controller with
   `planning_obstacles=[..., "/World/GantrySlider"]`.

### Failure modes — three independent bugs

**Bug 1: Gantry slider is not kinematically connected to the robot.**

`create_linear_axis_robot` (line 7380) creates `/World/GantrySlider` as a standalone Cube
with a `SliderJoint` under it (`/World/GantrySlider/SliderJoint`) that connects the slider
to the world (`body0_path=""`, `body1_path=slider_path`). The robot (`/World/Franka`) is
**never parented or fixed-jointed to the slider**. The slider prim moves independently of
the robot.

The CP-62 `failure_modes` field acknowledges this: "Gantry slider doesn't actually move
robot — slider prim is independent. Real gantry would parent robot to slider."

For the canonical demo, the gantry travel is not exercised at runtime (the bin is at +0.6m,
within normal Franka reach from the conveyor). The CP-62 `failure_modes` also notes: "Bin
at +0.6m — within Franka reach without gantry travel needed for canonical demo." So the
canonical can pass function-gate without the gantry actually moving.

**Bug 2: Surface gripper + cuRobo coexistence.**

The `surface_gripper` tool marks the robot prim with `isaac_assist:surface_gripper_path` at
line 6554. The cuRobo handler checks for this attribute at line 29354 and switches to
`SurfaceGripper` class instead of `ParallelGripper`. However, the Franka USD imported by
`robot_wizard` includes a parallel gripper by default (finger joints). With the
`SurfaceGripper` schema added on top, both gripper implementations may conflict at runtime:
the cuRobo controller's SurfaceGripper class (line 29361) tries to initialize
`isaacsim.robot.manipulators.grippers.SurfaceGripper` with the schema prim path, but the
articulation still has finger joints defined. The `SurfaceGripper.initialize()` call may
succeed while the finger-joint `gripper_open`/`gripper_close` commands from the cuRobo
callback have no effect, or conversely the finger joints open/close while the suction
gripper doesn't activate — depending on which code path fires first.

From the CP-62 `failure_modes`: "Surface gripper + parallel gripper coexist on same hand.
Pickup mechanism depends on which activates first."

**Bug 3: form-gate controller_installed may fail for multi-robot confusion.**

`setup_pick_place_controller(target_source='curobo')` with `/World/Franka` installs
`_curobo_pp_sub__World_Franka` (per-robot tagged). The `verify_pickplace_pipeline` with
`robot_kind="franka_panda"` and `robot_path="/World/Franka"` should find this tag. No
multi-robot confusion since CP-62 has one Franka. This check should pass.

### Fix proposal

**Bug 1 (gantry parenting)**: Add a `FixedJoint` between `/World/Franka` base link and
`/World/GantrySlider` after `create_linear_axis_robot`:
```python
create_articulated_joint(
    joint_path="/World/GantrySlider/FrankaMount",
    body0_path="/World/GantrySlider",
    body1_path="/World/Franka",
    joint_type="fixed",
)
```
This makes the robot ride the slider. Without this, the canonical is structurally incomplete
as a gantry demo, but the function-gate can still pass since the bin is within normal reach.
Accept Bug 1 as a Sprint 4+ limitation and document it; do not block form-gate on it.

**Bug 2 (gripper conflict)**: The cuRobo handler's detection of `isaac_assist:surface_gripper_path`
at line 29354 means it automatically switches gripper class. The key question is whether
`SurfaceGripper.initialize()` succeeds on a Franka without a proper suction joint. If it
fails, the cuRobo callback crashes and no picks happen. Fix: add a `use_surface_gripper=True`
explicit flag to `setup_pick_place_controller` that bypasses the attribute check fallback
and forces the SurfaceGripper branch consistently. Additionally, set the Franka's `Gripper`
variant to a suction variant (if available) before calling `surface_gripper`, or accept that
this canonical tests infrastructure only (Sprint 4+ for suction-on-pickup control).

---

## Estimated Unlock Count

| Canonical | Root Cause | Fix Complexity | Unlock Probability |
|-----------|-----------|----------------|-------------------|
| CP-40 (spline) | Lift height too low → table collision | Low: 1-line clamp + duration increase | **High (form-gate likely already passes; function-gate with lift fix)** |
| CP-48 (vision inspect) | Flush timing + camera switch reliability | Low: increase flush frames | **Medium-High (Gemini call is stochastic; 2/3 runs expected to pass)** |
| CP-59 (vision dual) | Heap geometry → 0 detections | Medium: cube positions in template | **High after cube spread fix** |
| CP-60 (recirculation) | controller_installed check on no-robot stage | Trivial: `robot_path: ""` in verify_args | **High (1-line template change)** |
| CP-62 (gantry surface gripper) | Gripper coexistence + gantry not parented | Medium: explicit gripper selection; gantry parenting is Sprint 4+ | **Medium (function-gate may pass if suction branch initializes; gantry non-functional but scene delivers cubes via normal Franka reach)** |

**Net unlock estimate: 3.5 of 5 canonicals** with the fixes described above. CP-48 and CP-62
remain partially stochastic (vision API reliability, gripper branch selection) and are
counted as 0.5 each.

---

## Files Referenced

- Template files: `workspace/templates/CP-{40,48,59,60,62}.json`
- Spline handler: `tool_executor.py` lines 31628–31634 (`_gen_pick_place_spline`)
- Vision gate: `tool_executor.py` lines 5910–6065 (`_handle_add_vision_classifier_gate`)
- Vision composite: `tool_executor.py` lines 6068–6178 (`_handle_setup_pick_place_with_vision`)
- Surface gripper: `tool_executor.py` lines 6463–6592 (`_handle_surface_gripper`)
- Recirculation loop: `tool_executor.py` lines 7311–7357 (`_handle_create_recirculation_loop`)
- Linear axis: `tool_executor.py` lines 7359–7409 (`_handle_create_linear_axis_robot`)
- Form-gate verifier: `tool_executor.py` lines 3527–3849 (`_handle_verify_pickplace_pipeline`)
- controller_installed logic: `tool_executor.py` lines 3673–3693
- ROBOT_REACH_M dict: `tool_executor.py` line 3241
- Function-gate: `tool_executor.py` lines 3852–4088 (`_handle_simulate_traversal_check`)
