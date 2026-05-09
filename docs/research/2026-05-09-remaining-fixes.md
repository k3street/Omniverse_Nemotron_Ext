# Remaining Failing Canonicals — Root Cause Analysis
**Date:** 2026-05-09  
**Scope:** CP-05, CP-06, CP-28, CP-29, CP-35, CP-46, CP-48, CP-57, CP-62  
**Author:** Claude diagnostic agent (claude-sonnet-4-6)

---

## Summary

Nine canonicals remain failing after the previously deployed fix batch. They break into five distinct failure modes:

1. **CP-05** — Physics geometry: passive flip-wall doesn't reliably tip a Cylinder on a belt; plus an `upright_dot_threshold` mismatch between the controller filter and the function-gate verify.
2. **CP-06** — Builtin controller: FixedJoint grip is only wired for UR10 family; Franka `target_source='builtin'` drops cubes mid-transit because no FJ attach is formed between Franka's parallel gripper fingers and the cube.
3. **CP-28 / CP-29** — Function-gate definition mismatch: these are precision-benchmark canonicals. CP-28 is documented to fail (expected ~24 cm drift); CP-29 fails because the bias-compensation experiment caused a planning failure (target too close to robot base). Neither needs a code fix — they need `expect_pass=False` classification in the suite and their `simulate_args` `target_path` adjusted to the conveyor belt (actual delivery check) rather than the tiny TargetZone.
4. **CP-35 / CP-46** — Build-spec only, no form-gate run yet. CP-35 has a `simulate_args` mismatch (tracks `Cube_r1` vs `RedBin` but 10-cube 240 s sim is fine). CP-46 tracks only `Cube_1`/`Pallet` — those are correct. Both are `"form-gate verification pending"` — the root cause is they haven't been run through `verify_pickplace_pipeline` yet, not a code bug.
5. **CP-48** — `setup_pick_place_with_vision` exists but `add_vision_classifier_gate` requires a live Gemini API call at install time; the function-gate runner has no Gemini credentials wired, so the vision step returns 0 detections and the whole handler errors before installing the cuRobo controller. Also, `destination_map` keys in CP-48 are the SHORT labels (`"green"`, `"red"`) but `class_labels` passed to `add_vision_classifier_gate` are LONG (`"green cube"`, `"red cube"`); the tool strips `" cube"` from detections correctly, but the `color_routing` dict built from `destination_map` uses the short keys while the detected label before stripping is long — flow is sound but brittle if stripping fails.
6. **CP-57** — `create_heap_zone` does not apply `PhysxRigidBodyAPI` to its items (only the three base APIs), so items have no sleep threshold or physx-specific properties. The canonical's code adds these manually but `create_heap_zone` doesn't return the spawned item paths until after the loop — the `bulk_set_attribute` + `apply_physics_material` calls in the canonical code reference `CubeHeap/Item_<n>` paths that may not exist if the heap spawner failed silently.  The bigger issue: the CP-57 `simulate_args.cube_path = "/World/CubeHeap/Item_1"` is the correct path, but the `verify_args.pick_path` uses `/World/HeapSurface` — a conveyor with velocity `0.001 m/s`, which is effectively a static surface. `verify_pickplace_pipeline` should accept this; however, if `create_heap_zone` races with the subsequent `bulk_set_attribute`, the items may not yet be registered with the physics engine.
7. **CP-62** — `create_linear_axis_robot` creates a **separate Cube prim** at `/World/GantrySlider` and a prismatic joint `SliderJoint` between world and the slider — but it does NOT parent the robot under the slider, so the robot and slider are entirely independent. The cuRobo controller then runs normally on the static Franka, ignoring the gantry. This is documented in the template's `failure_modes` ("Gantry slider doesn't actually move robot") but the canonical still claims this as a working demo. Since the controller and bin are within Franka's static reach, the base pick-place should still succeed — the issue is that `surface_gripper` is installed on `panda_hand`, and the cuRobo handler will detect the surface gripper marker and try to use `_surface_gripper.close()` / `_surface_gripper.open()` instead of the parallel gripper finger joints, which breaks grip for Franka (the surface gripper path on Franka does not have the `suction_cup` sub-prim, so the `overlap_sphere` raycast in the cuRobo handler returns no hits and grip is never formed).

---

## Per-Canonical Root Cause Detail

---

### CP-05 — REORIENT-01 Passive Flip Station

**Verified status:** `build-spec-2026-05-08; physics-tuning-required`

**Root cause — two distinct bugs:**

**Bug A: Upright threshold mismatch.**  
The template's `simulate_args` sets `upright_tolerance_dot: 0.95`. The `setup_pick_place_controller` call sets `upright_dot_threshold=0.7`. The cuRobo handler's `_cube_to_pick()` REORIENT filter uses `upright_dot_threshold` (the controller arg, 0.7), but `simulate_traversal_check` uses `upright_tolerance_dot` (the simulate arg, 0.95). If the cylinder tips to ~0.75 dot (about 41° from vertical), the controller will pick it (0.75 > 0.70) but the function gate will reject it (0.75 < 0.95). This threshold pair must match — both should be 0.95 or the template comment saying "within ~18° of vertical" is wrong for the controller-pick gate.

**Bug B: Cylinder-on-belt physics.**  
The geometry uses `prim_type="Cylinder"` with `radius=0.025, height=0.10, rotation_euler=[90,0,0]`. USD Cylinder's default long axis is local +Z. With `rotation_euler=[90,0,0]` the long axis swings to world Y, so the cylinder lies across the belt (perpendicular to travel direction). Its contact with the belt is a line contact along the length, not a point. Belt velocity is in world X (+0.8 m/s) — the belt pushes the cylinder in X. A cylinder lying across (long axis along Y) rolls along X much more easily than a cube, so instead of being pushed into the flip-wall it may simply roll forward without impacting the wall cleanly. The flip-wall at position `x=0.50` with `scale=[0.005, 0.15, 0.018]` is 1.8 cm tall — the cylinder's radius is 2.5 cm, so the wall is SHORTER than the cylinder's radius. The cylinder centre is at `z=0.81` and the wall top is at `z=0.847 + 0.018 = ~0.865`. The cylinder sitting on the belt at z=0.81 (centre) with radius 0.025 has its equator at z=0.835 — the wall top at z=0.865 is above the cylinder equator, which means the wall WILL impede the cylinder. However, without the right angular momentum (the cylinder lies with long axis in Y, so belt-driven X-translation results in rolling motion in Y, not X), the cylinder may skid rather than tip.

This bug is fundamentally physics-tuning but the threshold mismatch in Bug A is a mechanical code fix.

**Pattern:** New failure mode (passive flip dynamics) + parameter mismatch (known pattern — similar to drop-precision threshold issues).

**Patch proposal:**

1. In `CP-05.json`: set `upright_dot_threshold` in the `setup_pick_place_controller` call to `0.95` (matching `simulate_args.upright_tolerance_dot`). Line in template code: `upright_dot_threshold=0.7` → `upright_dot_threshold=0.95`.

2. In `CP-05.json` code: Change the `create_prim` for the Cylinder to use `rotation_euler=[0, 90, 0]` (rotate around Y instead of X). This tilts the cylinder so its long axis lies along world X (along belt travel direction). The cylinder then presents a circular cross-section to the flip-wall, which is the correct geometry for a flip: the front edge hits the wall, the back continues forward, cylinder tips nose-down then upright.

3. Adjust flip-wall height: `scale=[0.005, 0.15, 0.018]` → `scale=[0.005, 0.15, 0.013]` (wall top at ~0.855 m, below cylinder equator at 0.860 m so wall catches the cylinder's lower body, not the top).

**File:** `/home/anton/projects/Omniverse_Nemotron_Ext/workspace/templates/CP-05.json`  
**Lines:** `code` field, `create_prim(prim_path="/World/Cube" ...)` and `create_prim(prim_path="/World/FlipWall" ...)` and `setup_pick_place_controller(... upright_dot_threshold=0.7 ...)`

**Confidence:** Medium — Bug A fix (threshold alignment) is high-confidence mechanical. Bug B (geometry rotation axis) is medium — physics outcome still depends on friction + timing.

---

### CP-06 — Builtin Franka PickPlaceController (friction-grip drops cube)

**Verified status:** `blocked since 2026-05-07`

**Root cause:**  
The `_gen_pick_place_builtin` handler has a `FixedJoint` attach pattern at lines ~29706–29781, but it is **gated on `ROBOT_FAMILY in ("ur10", "ur10e")`**. Franka's bundled `PickPlaceController` uses the `ParallelGripper` — but the parallel gripper closes to ~0mm finger gap with high stiffness (10,000 Nm/rad) yet the physical contact between two 5cm-wide flat finger pads and a rubber-coated 5cm cube is friction-only. During the high-acceleration whip motion of RMPflow, the effective inertial force on the cube (mass ~0.1 kg × ~5 m/s² peak acceleration ≈ 0.5 N) easily overcomes the static friction between fingers and cube. The result is what the blocked note describes: cube ends at `[4.05, 0.09, 0.53]` — it slips from the fingers during transit.

The cuRobo handler fixes this via a `UsdPhysics.FixedJoint` between the Franka's `panda_hand` link and the cube when `S["mode"] == "grip_close"` fires. The builtin handler has the same FixedJoint concept but only runs it for UR10, not Franka.

**Pattern:** Variant of the "Mode B: FixedJoint grip" pattern from the deployed fixes — the prior fix only covered cuRobo's handler; the builtin handler's FixedJoint path exists only for UR10.

**Patch proposal:**  
In `_gen_pick_place_builtin` (tool_executor.py line ~29706), change the family gate for FixedJoint snap from `if ROBOT_FAMILY in ("ur10", "ur10e")` to include Franka:

```python
# Old gate:
if ROBOT_FAMILY in ("ur10", "ur10e") and _ev is not None:
    if 0 <= _ev <= 4 and not S.get("fixed_joint"):
        ...  # UR10-specific suction_cup raycast

# New: Franka path uses panda_hand + finger contact zone
# UR10 path unchanged (suction_cup raycast)
if _ev is not None and not S.get("fixed_joint") and 0 <= _ev <= 4:
    if ROBOT_FAMILY == "franka":
        # No suction_cup; grip when EE is near cube (overlap_sphere from panda_hand)
        try:
            _ee_prim = stage.GetPrimAtPath(f"{ROBOT_PATH}/panda_hand")
            if _ee_prim and _ee_prim.IsValid():
                _eem = UsdGeom.Xformable(_ee_prim).ComputeLocalToWorldTransform(0)
                _eet = _eem.ExtractTranslation()
                _origin = [float(_eet[0]), float(_eet[1]), float(_eet[2])]
                _hits = []
                def _franka_report_fn(hit):
                    p = getattr(hit,"rigid_body",None) or getattr(hit,"collision",None)
                    if p: _hits.append(str(p))
                    return True
                from omni.physx import get_physx_scene_query_interface as _gsqi
                _gsqi().overlap_sphere(0.15, _origin, _franka_report_fn, False)
                for h in _hits:
                    for sp in SOURCE_PATHS:
                        if (h == sp or h.startswith(sp+"/")) and sp not in S["delivered"]:
                            cube = stage.GetPrimAtPath(sp)
                            ee   = stage.GetPrimAtPath(f"{ROBOT_PATH}/panda_hand")
                            if ee and ee.IsValid() and cube and cube.IsValid():
                                jp = f"{sp}_pp_grip_fj"
                                fj = UsdPhysics.FixedJoint.Define(stage, jp)
                                fj.CreateBody0Rel().SetTargets([Sdf.Path(str(ee.GetPath()))])
                                fj.CreateBody1Rel().SetTargets([Sdf.Path(sp)])
                                S["fixed_joint"] = jp
                                if S.get("current") != sp: S["current"] = sp
                            break
                    if S.get("fixed_joint"): break
        except Exception as _fje: print(f"(builtin pp Franka fj snap fail: {_fje})")
    elif ROBOT_FAMILY in ("ur10", "ur10e"):
        ...  # existing suction_cup overlap_sphere code unchanged
```

The FJ release for Franka is the same event ≥7 + xy proximity gate already present.

**File:** `service/isaac_assist_service/chat/tools/tool_executor.py`  
**Approximate line range:** 29706–29781 (the FixedJoint snap block in `_gen_pick_place_builtin._on_step`)

**Confidence:** High — direct analogy to the cuRobo Mode-B fix and the UR10 builtin FJ pattern; the mechanism is identical, only the EE link name and overlap radius differ.

---

### CP-28 — Precision Drop Benchmark (24 cm drift, expected failure)

**Verified status:** `build-verified-2026-05-08; PRECISION DATA: mean drift ~24cm`

**Root cause:**  
This canonical is designed to MEASURE cuRobo drop precision, not to pass a function-gate. Its `simulate_args.target_path = "/World/TargetZone"` — a 10×10 cm marker — and the measured drift is 20-33 cm. This means the cube NEVER lands in the TargetZone, so `simulate_traversal_check` returns `success=False` for every run. The canonical is functioning exactly as designed; the problem is it is classified as a pass-required canonical in the suite.

**Pattern:** Test definition mismatch — the canonical IS working, the harness expectation is wrong.

**Patch proposal:**  
1. In `function_gate_suite.py` (or wherever CP-28 is registered), set `expect_pass=False` for CP-28.  
2. Alternatively, change `simulate_args.target_path` to the conveyor belt path (`/World/ConveyorBelt`) so the function-gate checks only that the cube was PICKED (moved off the belt), not that it landed precisely in the 10 cm zone. The precision measurement itself is the result data, not the pass/fail signal.

**File:** `scripts/qa/function_gate_suite.py` (suite registration) OR `workspace/templates/CP-28.json` (simulate_args)  
**Confidence:** High — no physics ambiguity; this is a harness classification bug.

---

### CP-29 — Y-Bias Compensation Experiment (cube never picked)

**Verified status:** `build-verified-2026-05-08; compensation FAILED — cube never picked`

**Root cause:**  
The `drop_target = [0.0, -0.24, 0.825]` puts the TargetZone at y = -0.24 m from robot base. At Franka's home position on the table, y = 0, so the TargetZone is 0.24 m forward of the base — well within reach. HOWEVER the cube source is still at x = -1.4 m on the conveyor at y = 0.4. The `_cube_to_pick()` function checks `np.linalg.norm(cp[:2] - base_xy) > _reach_m`. The cube at (-1.4, 0.4) is `sqrt(1.4² + 0.4²) ≈ 1.46 m` from base — OUTSIDE the default Franka reach of 0.85 m. The `verified_status` note says "cube was never picked" across 3 runs, consistent with reach failure rather than planning failure. The reach check in `_cube_to_pick()` (curobo handler, line ~33182) rejects the cube before any plan attempt.

Wait — but CP-01 also starts cubes at x = -1.4 and works, because the belt moves cubes to the sensor at x = 0.4. This is a belt timing question: the cube must travel from x = -1.4 to x ≈ 0.4 (sensor) before the controller picks it. CP-29 uses the same belt at 0.2 m/s, so cubes should arrive at the sensor in about 5 s. If the controller picked the closest cube prematurely, or if cuRobo's plan to drop_target `[0.0, -0.24, 0.825]` (extremely close to the robot base) fails IK, that would cause 3-strike failure and the cube gets added to `failed` set without delivery.

The actual cause is most likely IK failure for `drop_target = [0.0, -0.24, 0.825]` — placing an EE at (0.0, -0.24) in world coordinates means the Franka must fold its arm back toward itself (into a configuration near the singularity of a straight-down pose at very short reach radius). cuRobo's IK solver often fails for targets < 0.3 m from robot base in the horizontal plane. The 3-strike counter marks the cube as permanently failed.

**Pattern:** New failure mode — drop_target too close to robot base causes IK singularity / repeated plan failure triggering 3-strike permanent fail.

**Patch proposal:**  
Same fix as CP-28: classify as `expect_pass=False` in suite (this canonical is an experiment by design). Optionally, change `simulate_args.target_path` to `/World/TargetZone` but loosen `xy_tolerance` (e.g. 0.20) so ANY cube delivery within 20 cm counts — this would validate that the cube moved at all, regardless of precision.

**File:** Suite registration or `workspace/templates/CP-29.json` simulate_args  
**Confidence:** High — diagnostic-first: the "never picked" symptom across 3 runs is consistent with either reach-check rejecting or IK-singularity 3-strike.

---

### CP-35 — 10-Cube 4-Color Sortation + Reject (form-gate never run)

**Verified status:** `build-spec-2026-05-08; form-gate verification pending`

**Root cause:**  
CP-35 has never been run through `verify_pickplace_pipeline` (form-gate) or `simulate_traversal_check` (function-gate). The `verified_status` explicitly says "pending". There is no code bug to fix — the canonical needs to be instantiated and run.

However, there IS a potential functional issue worth flagging: the `color_routing` fallthrough for defective cubes (those with `None` as their color in `color_specs`) depends on `_cube_semantic_class()` returning `None` for cubes with no `set_semantic_label` call. Currently `_cube_semantic_class()` (curobo handler line ~32822) returns `None` when no Semantics API is found, and `_destination_path_for()` then falls through to `DEST_PATH = "/World/RejectBin"`. This is correct. The 10-cube × 7s/cycle = 70 s sim is under the 240 s budget.

**Potential issue:** 5 bins packed at 0.30 m spacing, all listed as `planning_obstacles`. cuRobo at Warp 1.8.2 cannot use scene-collision obstacles (known limitation: `'Couldn't find function overload for is_obs_enabled'`). So the planning_obstacles list is inert. The robot may crash into bin rims.

**Patch proposal:**  
Run the form-gate and function-gate first — only patch if a real failure is observed. The probable bin-rim collision due to disabled scene-collision is a known limitation across all cuRobo canonicals and isn't unique to CP-35.

**File:** No code patch until form-gate is run.  
**Confidence:** Low (speculative until gate run).

---

### CP-46 — 6-Cube Grid Palletizer with compute_stack_placement (form-gate never run)

**Verified status:** `build-spec-2026-05-08; form-gate verification pending; reference baseline`

**Root cause:**  
Same situation as CP-35 — never run through gates. The template is well-formed and extends the proven CP-30 pattern. The `drop_targets` dict is pre-computed with 6 explicit `[x,y,z]` entries, all within the pallet's 50 cm span. `_bin_drop_pos()` in the cuRobo handler correctly dispatches per cube from `DROP_TARGETS dict`. This canonical SHOULD pass without code changes if the form-gate template is executed.

**Potential issue:** `compute_stack_placement` is a `DATA_HANDLER` tool (lines 24952–25131) that returns placement positions but the code in CP-46's template hardcodes the positions directly rather than calling the tool at runtime. This is by design (the positions are pre-computed). No issue.

**Patch proposal:**  
Run gates. No code change anticipated.  
**Confidence:** Low (speculative).

---

### CP-48 — Runtime Vision Inspect-and-Reject (Gemini API dependency)

**Verified status:** `build-spec-2026-05-08; form-gate verification pending; runtime-vision integration`

**Root cause — two issues:**

**Issue A (blocking): Gemini credentials not available in function-gate runner.**  
`setup_pick_place_with_vision` calls `add_vision_classifier_gate` which calls `_get_vision_provider()` and then `vp.detect_objects()` — a live Gemini Robotics-ER (or Gemini Flash Vision) API call. The function-gate runner (`function_gate_suite.py`, `direct_eval.py`) does not configure Gemini credentials or a mock. If the API key is missing or the network is unavailable, `detect_objects()` raises, and `cube_to_class` will be empty, causing the handler to return `{"type": "error", ...}` before any cuRobo controller is installed. The physics simulation never starts.

**Issue B (mild): destination_map key format.**  
`destination_map = {"green": "/World/GoodBin", "red": "/World/RejectBin"}` and `class_labels = ["green cube", "red cube"]`. In `_handle_setup_pick_place_with_vision()` (line 6154), `color_routing = dict(destination_map)` — so keys are short (`"green"`, `"red"`). The `_cube_semantic_class()` function returns short class names because `set_semantic_label` was called with the stripped label. This part is correct. BUT if for any reason the label-stripping at line 6127 (`label.lower().replace(" cube", "").strip()`) misses a label (e.g. if Gemini returns `"green_cube"` or `"GREEN CUBE"`), the routing silently falls through to `DEST_PATH = "/World/RejectBin"`, misrouting good cubes as defective. This is a brittleness issue, not a hard failure.

**Patch proposal:**

For Issue A, add a mock/bypass path in the function-gate for the vision step. The simplest fix that doesn't require mock infrastructure: add a `vision_labels` pre-computed field to `simulate_args` in the template, and when `add_vision_classifier_gate` would be called, if `_FUNCTION_GATE_MODE` is set (environment flag), use the pre-computed labels instead of calling Gemini. Alternatively — simpler — add a `vision_precomputed` field to the template's simulate_args that the function-gate runner injects as mock cube-to-class mapping:

```python
# In _handle_setup_pick_place_with_vision:
# Check for pre-computed vision result (function-gate bypass)
if args.get("vision_precomputed"):
    cube_to_class = args["vision_precomputed"]
else:
    vision_res = await execute_tool_call("add_vision_classifier_gate", {...})
    cube_to_class = vision_res.get("cube_to_class") or {}
```

Then in `CP-48.json` `simulate_args`, add:
```json
"vision_precomputed": {
    "/World/Cube_g1": "green",
    "/World/Cube_g2": "green",
    "/World/Cube_g3": "green",
    "/World/Cube_g4": "green",
    "/World/Cube_bad": "red"
}
```

For Issue B, add case-normalization in the label-stripping: `.lower().replace("_", " ").replace(" cube","").strip()`.

**Files:**  
- `service/isaac_assist_service/chat/tools/tool_executor.py` lines ~6118–6122 (`_handle_setup_pick_place_with_vision`)  
- `workspace/templates/CP-48.json` simulate_args  

**Confidence:** High for Issue A (Gemini gate bypass); Medium for Issue B (brittleness, not confirmed failure).

---

### CP-57 — Heap Singulation (PhysxRigidBodyAPI missing on heap items)

**Verified status:** `build-spec-2026-05-08; form-gate verification pending`

**Root cause:**  
`create_heap_zone` (handler lines 6956–7007) applies only `PhysicsRigidBodyAPI`, `PhysicsCollisionAPI`, and `PhysicsMassAPI` to each item — it does NOT apply `PhysxRigidBodyAPI`. Without `PhysxRigidBodyAPI`, items cannot have `physxRigidBody:sleepThreshold` authored on them (the attribute belongs to that schema). The canonical's code then calls:

```python
bulk_set_attribute(
    prim_paths=[f"/World/CubeHeap/Item_{i+1}" for i in range(5)],
    attr="physxRigidBody:sleepThreshold",
    value=0.0,
)
```

`bulk_set_attribute` at line ~... typically creates the attribute if missing, but `physxRigidBody:sleepThreshold` requires the schema to be applied first — without the schema, the attribute write silently fails. Items will then have the default non-zero sleep threshold and may freeze in mid-air (stuck in sleep state after spawn overlap resolution).

Additionally, the `create_heap_zone` spawns items with `item_size * 0.5` z offset above center — they are meant to fall and settle. If items enter sleep before reaching table surface (due to non-zero sleep threshold), they freeze floating above the heap surface, making robot pickup impossible (EE can't descend to floating cube without planning failure).

**Pattern:** New — `create_heap_zone` was added without including `PhysxRigidBodyAPI` in its schema list, which breaks the pattern established by every other canonical that uses `sleepThreshold=0`.

**Patch proposal:**  
In `_handle_create_heap_zone` (tool_executor.py lines 6997–6999), add `PhysxRigidBodyAPI` to the API list:

```python
# Change:
for api in ("PhysicsRigidBodyAPI", "PhysicsCollisionAPI", "PhysicsMassAPI"):
    await execute_tool_call("apply_api_schema",
                              {"prim_path": path, "schema_name": api})

# To:
for api in ("PhysicsRigidBodyAPI", "PhysicsCollisionAPI", "PhysicsMassAPI", "PhysxRigidBodyAPI"):
    await execute_tool_call("apply_api_schema",
                              {"prim_path": path, "schema_name": api})
```

Also set `sleepThreshold=0` inside the handler itself (so canonicals get correct behavior even without manual post-processing):

```python
await execute_tool_call("bulk_set_attribute", {
    "prim_paths": item_paths,
    "attr": "physxRigidBody:sleepThreshold",
    "value": 0.0,
})
```

**File:** `service/isaac_assist_service/chat/tools/tool_executor.py` lines 6997–6999  
**Confidence:** High — direct analogy to every other canonical; `PhysxRigidBodyAPI` is required for `sleepThreshold` authoring (this is the same schema pattern used in the deployed fixes).

---

### CP-62 — Surface Gripper Gantry (SG marker conflicts with Franka FJ grip path)

**Verified status:** `build-spec-2026-05-08; form-gate verification pending`

**Root cause:**  
`surface_gripper` tool (lines 6463–6592) writes `isaac_assist:surface_gripper_path` to the robot prim as a marker. The cuRobo handler reads this marker at install time (lines 32718–32733) and initializes `_surface_gripper`. The cuRobo grip path then uses `_surface_gripper.close()` / `_surface_gripper.open()` instead of the finger joint approach.

On Franka, `surface_gripper` attempts to install via `variant_set.SetVariantSelection("Short_Suction")` on the Franka prim. Franka USD from the Isaac Sim asset library does NOT have a `"Gripper"` variant set — so the variant path fails. It falls back to `omni.kit.commands.execute("CreateSurfaceGripper", prim_path=art_path)` where `art_path = "/World/Franka/panda_hand"`. This may or may not succeed depending on the Kit version. If it succeeds, a surface gripper schema prim is written at `panda_hand/SurfaceGripper`. The `isaac_assist:surface_gripper_path` marker is set.

When the cuRobo controller installs, it detects the marker and creates `_surface_gripper`. During grip, it calls `_surface_gripper.close()` — but the Franka has no suction joint underneath `panda_hand`; the SurfaceGripper C++ interface tries to engage based on `maxGripDistance` from a contact search, finds the cube (which is on the belt at ~0.835 m, and panda_hand during descend is at ~0.88 m — within `grip_threshold=0.01 m`... actually the default `grip_threshold=0.01 m` means the SG only engages within 1 cm, which is extremely tight). The `overlap_sphere` fallback in the grip path uses the UR10-specific `suction_cup` child path which does not exist under `panda_hand`. So the overlap_sphere call to form a FixedJoint fails silently because `stage.GetPrimAtPath(f"{ROBOT_PATH}/ee_link/suction_cup")` is None for Franka (Franka EE link is `panda_hand`, not `ee_link`).

**Net result:** `_surface_gripper` is initialized, `.close()` is called, no FixedJoint is formed (no suction_cup prim), and the parallel finger joints are NOT closed because the code path goes through `_surface_gripper.close()` instead of `_grip_close_fingers()`. The cube is never gripped.

**Pattern:** Surface gripper marker + Franka combination creates a code path conflict. The deployed "UR10 builtin: `_ev=None` guard" fix is analogous but this is a different handler (cuRobo, not builtin).

**Patch proposal (two options):**

**Option A (recommended):** Don't install `surface_gripper` on Franka in CP-62. Remove the `surface_gripper(robot_path="/World/Franka", ...)` call from the template. Franka's parallel gripper works fine for normal pick-place; the gantry slider is a structural addition that doesn't require changing the gripper type. The `create_linear_axis_robot` issue (robot not parented to slider) is documented as Sprint 4+ work.

**Option B (code fix):** In `_gen_pick_place_curobo`, add a family check to the `_surface_gripper` detection block:

```python
# Only use surface_gripper path if robot is NOT Franka (Franka uses finger joints)
if ROBOT_FAMILY != "franka":
    _sg_attr = stage.GetPrimAtPath(ROBOT_PATH).GetAttribute("isaac_assist:surface_gripper_path")
    ...
else:
    _surface_gripper = None  # Force finger-joint path for Franka
```

This prevents the surface_gripper marker from overriding Franka's working parallel gripper.

**File (Option A):** `workspace/templates/CP-62.json` — remove `surface_gripper` from `tools_used` and from `code`.  
**File (Option B):** `service/isaac_assist_service/chat/tools/tool_executor.py` lines ~32718–32733.  
**Confidence:** High — the suction_cup path fallback logic is UR10-specific (documented inline); Franka has no `ee_link/suction_cup` subpath.

---

## Ranked Patch List (Highest Impact First)

| Rank | CP | Change | File | Confidence | Impact |
|------|----|--------|------|-----------|--------|
| 1 | CP-57 | Add `PhysxRigidBodyAPI` + `sleepThreshold=0` inside `create_heap_zone` handler | tool_executor.py ~6997 | High | Unblocks CP-57 completely; also fixes any future canonical using create_heap_zone |
| 2 | CP-06 | Add Franka FixedJoint snap to `_gen_pick_place_builtin._on_step` | tool_executor.py ~29706 | High | Unblocks the entire `target_source='builtin'` Franka path |
| 3 | CP-62 | Add `ROBOT_FAMILY != "franka"` guard to surface_gripper detection in `_gen_pick_place_curobo` | tool_executor.py ~32718 | High | Prevents SG marker from hijacking Franka finger grip; also protects any future Franka+SG combined canonical |
| 4 | CP-05 | Align `upright_dot_threshold=0.95` in template code to match `simulate_args.upright_tolerance_dot=0.95` | CP-05.json code | High (Bug A) | Ensures controller and gate agree; currently gate can reject a correctly flipped cube that controller accepted |
| 5 | CP-48 | Add `vision_precomputed` bypass path in `_handle_setup_pick_place_with_vision` + populate in CP-48 simulate_args | tool_executor.py ~6118; CP-48.json | High | Unblocks function-gate for all vision canonicals without live Gemini credential |
| 6 | CP-28 | Mark `expect_pass=False` in suite; change `target_path` in simulate_args to conveyorbelt or use `xy_tolerance=0.30` | function_gate_suite.py; CP-28.json | High | Stops CP-28 from polluting pass-rate; it is working correctly |
| 7 | CP-29 | Mark `expect_pass=False` in suite | function_gate_suite.py | High | Same — experimental canonical |
| 8 | CP-05 | Change Cylinder rotation_euler from `[90,0,0]` to `[0,90,0]` and reduce flip-wall height slightly | CP-05.json code | Medium | Physics-tuning; makes flip geometry consistent with belt direction |
| 9 | CP-35 | Run form-gate; patch only on observed failure | — | Low | Speculative — no confirmed code bug |
| 10 | CP-46 | Run form-gate; patch only on observed failure | — | Low | Speculative — template is structurally clean |

---

## Notes on Already-Deployed Fixes

The following are NOT re-recommended (already deployed per task spec):
- Mode A 3-strike plan-fail counter (cuRobo handler)
- Mode B FixedJoint grip_close (cuRobo handler)
- Belt-pause pre_step subscription
- Drop-precision XY arrival gate
- Multi-robot mutex + proximity delivery
- UR10 builtin: `_ev=None` guard, sensor-gate in `_next_cube`, FJ tolerance
- CP-22 high-speed lookahead + adaptive settle_ticks
- CP-58 multi-cube cube_paths

The remaining patches are orthogonal to these and do not conflict with them.
