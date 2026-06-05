# Opus Audit #3 — Convergence pass on A1–A8 canonicals

Date: 2026-05-16
Auditor: Opus 4.7 (no prior context)
State audited: Post `R-A-fix-2` (round 2 patches landed)
Method: schema cross-check against
`service/isaac_assist_service/chat/tools/handlers/_models.py`,
handler argument inspection (`physics.py`, `robot.py`, `vision.py`,
`sensors.py`), prim-path semantic verification.

---

## §1 Methodology

For each of A1–A8 I:

1. Extracted every tool call in `code` + `code_template` with full kwargs
   (script `/tmp/extract_full.py` — regex-balanced paren walk).
2. For each call, matched against its Pydantic model in `_models.py`
   (registry table at line ~3940+ maps tool-name → ArgsModel).
3. Required fields = bare annotations OR `Field(...)`; optional =
   `Optional[...] = Field(None, ...)`. `model_config` allows extras
   (so unknown kwargs are silently absorbed — but missing REQUIRED
   fields will still raise `ValidationError`).
4. Cross-checked verify_args / role_defaults prim paths against what
   the code field actually creates (or what the tool handler emits).
5. Spot-checked predictions from R-A-fix-2: emit_ros2_control_yaml (A5),
   iterate_reward (A7), create_heap_zone (A8), get_bounding_box (A8),
   setup_robot_claim_mutex / setup_robot_handoff_signal (A6).

Severity definitions (per task brief):

- **BLOCKER** — Pydantic validation fails OR semantic mismatch makes
  the canonical impossible to run (e.g. tool ops on nonexistent prims).
- **HIGH** — Validates and dispatches, but runtime behaviour deviates
  enough that the canonical cannot succeed.
- **MED** — Runs to completion but with a subtle correctness issue.
- **LOW** — Cosmetic / informational.

---

## §2 Findings per template

### A1 — `CP-NEW-palletizer-layer-stack.json`
- All tool kwargs match schemas (`robot_wizard`, `create_conveyor`,
  `add_proximity_sensor`, `bulk_set_attribute`, `apply_physics_material`,
  `setup_pick_place_controller(drop_targets={...})`).
- `compute_stack_placement` appears only in a comment (line 16 thoughts /
  inline code comment), not as a call — no validation concern.
- verify_args / role_defaults paths match `code` exactly. **GREEN**.

### A2 — `CP-NEW-kit-prep-operator.json`
- `set_semantic_label(prim_path=path, label=label, class_name="kit_part")`
  — **MED**. `label` is not in `SetSemanticLabelArgs` (_models.py:2909),
  silently absorbed by `extra='allow'`. Handler `_gen_set_semantic_label`
  (vision.py:387) reads only `class_name` + `semantic_type`. All five
  parts get the same semantic class `"kit_part"` — the per-part PCB /
  screw / housing / label / bracket distinction (intended by the author
  via `label=label`) is LOST.
  - Fix: switch to `class_name=label, semantic_type="kit_part"` (or
    similar) so the per-item identity actually lands in
    `Semantics_<type>.semanticData`.
- Rest of A2 is schema-clean.

### A3 — `CP-NEW-barcode-scanner-divert.json`
- `barcode_reader_sensor(sensor_path=..., position=..., scan_volume=...,
  read_attribute=...)` — **MED**. `BarcodeReaderSensorArgs`
  (_models.py:1013) only has `sensor_path`, `position`, `scan_radius`.
  `scan_volume` + `read_attribute` are absorbed and ignored by
  `_handle_barcode_reader_sensor` (sensors.py:686). The canonical's
  modelled scan-volume / label-attr binding is silently dropped — the
  sensor lands with a default scan radius.
  - Fix: either extend the schema/handler with `scan_volume` +
    `read_attribute` (preferred, since the canonical clearly wants
    them), or downgrade the call to `scan_radius=<largest of
    scan_volume>/2`.
- `set_semantic_label(prim_path=..., class_name=sku, semantic_type=
  "label")` — schema-clean (uses canonical kwargs correctly here,
  unlike A2).
- Rest schema-clean.

### A4 — `CP-NEW-turn-faucet.json`
Two **BLOCKERS** (both appear in `code` AND `code_template`):

- **BLOCKER #1** — `plan_trajectory(robot_path="/World/Franka",
  target_pose={"position": [...], "orientation": [...]},
  planning_obstacles=[...], close_gripper=True)`.
  `PlanTrajectoryArgs` (_models.py:714) requires `articulation_path:
  str` (NOT `robot_path`) and `waypoints: List[Dict[str, Any]]` (NOT
  `target_pose: Dict`). Both required fields absent → Pydantic
  `ValidationError`. `planning_obstacles` + `close_gripper` are absorbed
  but irrelevant — call never reaches handler.
  - Handler `_gen_plan_trajectory` (robot.py:2186) reads
    `args["articulation_path"]` and `args["waypoints"]` — `KeyError`
    even if validation were bypassed.
  - Two call sites in `code` (lines after `sim_control`), two in
    `code_template`.

- **BLOCKER #2** — `set_joint_targets(robot_path="/World/Franka",
  positions={6: wrist_baseline + TARGET_ANGLE_RAD}, stiffness=500,
  damping=50)`.
  `SetJointTargetsArgs` (_models.py:197) requires `articulation_path:
  str` (NOT `robot_path`) and has `target_position: Optional[float]`
  (NOT `positions: Dict[int, float]`). Missing required
  `articulation_path` → `ValidationError`. `positions`, `stiffness`,
  `damping` are absorbed and dropped.
  - Even if validation passed, handler `_gen_set_joint_targets`
    (physics.py:174) reads `args["articulation_path"]` (KeyError) and
    `args.get("target_position")` (None — handler would produce
    code-gen that sets nothing).
  - To express "drive joint #6 from `wrist_baseline` to
    `wrist_baseline + 1.5708 rad`" the canonical needs either
    `set_joint_targets(articulation_path=..., joint_name="panda_joint7",
    target_position=<new_angle>)` OR a different tool that supports
    multi-joint dict targets (none exists in the current registry).

Also note (MED, not blocker): `get_joint_positions(articulation=
"/World/FaucetBody")` — but `/World/FaucetBody` only carries
`PhysicsArticulationRootAPI` with a single child revolute joint at
`/World/FaucetJoint`. `_handle_get_joint_positions` walks revolute/
prismatic children; should work, but the indexing comment "joint
index 6 = Franka flange joint" mixes the Franka joint vector with
the faucet joint vector. Semantic risk, not validation. **MED**.

### A5 — `CP-NEW-ros2-bridge-franka.json`
- `emit_ros2_control_yaml` — kwargs `robot_path, output_path,
  controller_type, joint_states_topic, joint_commands_topic,
  update_rate_hz` all present in `EmitRos2ControlYamlArgs`
  (_models.py:399). **GREEN**.
- `configure_ros2_time(mode="sim_time")` — schema-clean.
- `setup_ros2_bridge(profile="franka_moveit2", robot_path=...)` —
  schema-clean.
- `ros2_subscribe_once(topic, msg_type, timeout)` — schema-clean.
- `diagnose_ros2(robot_path=..., expected_topics=[...],
  check_qos=True)` in `code_template` — schema is empty (`pass`).
  Extras absorbed and dropped. Handler runs the generic check.
  **LOW** (intent lost but no error).

### A6 — `CP-NEW-assembly-line-4robot-handoff.json`
- `setup_robot_claim_mutex(mutex_path, resource_path, robots)` ×3 —
  matches `SetupRobotClaimMutexArgs` (_models.py:1059). **GREEN**.
- `setup_robot_handoff_signal(handoff_path, robot_a, robot_b,
  position)` ×3 — matches `SetupRobotHandoffSignalArgs`
  (_models.py:1068). **GREEN**. (R-A-fix-2 claim "was removed" is
  incorrect — the tool calls are present and schema-conformant.)
- Four `setup_pick_place_controller(robot_path, target_source,
  source_paths, destination_path, planning_obstacles)` calls — all
  schema-clean.

### A7 — `CP-NEW-eureka-pick-place-reward.json`
- `create_isaaclab_env(task_name, robot_path, task_type, num_envs,
  reward_terms)` — schema-clean (`CreateIsaaclabEnvArgs` line 760).
- `generate_reward(task_description, env_source_path, num_candidates,
  num_iterations)` — schema-clean (line 1328).
- `evaluate_reward(reward_code, env_id, num_steps)` ×2 — schema-clean.
- `iterate_reward(prev_reward_code, metrics, user_feedback)` —
  schema-clean (`IterateRewardArgs` line 1347; `metrics: Dict[str,
  Any]` accepts the placeholder dict).
- `review_reward(reward_code)` — schema-clean.
- `eureka_history(run_id=...)` — schema-clean.
- `checkpoint_training(run_id, tag)` — schema-clean.
**GREEN**.

### A8 — `CP-NEW-heap-zone-unstack.json`

One **BLOCKER**:

- **BLOCKER** — Prim-path semantic mismatch.
  `create_heap_zone(heap_path="/World/HeapZone", ..., n_items=8)`
  emits items at `/World/HeapZone/Item_1..8` (per
  `_handle_create_heap_zone` in robot.py:4381, line 4418 builds
  `f"{heap_path}/Item_{i+1}"`).
  The code then defines `cube_paths = [f"/World/Cube_{i+1}" for i in
  range(8)]` and applies:
  - `bulk_set_attribute(prim_paths=cube_paths, ...)` — silently skips
    missing prims (per `BulkSetAttributeArgs` docstring line 3667).
  - `apply_physics_material(prim_path=path, ...)` — Pydantic clean but
    handler will fail on nonexistent prim (or no-op depending on
    impl).
  - `get_bounding_box(prim_path=_path)` — returns empty / errors on
    missing prim; max_z falls to default 0.0 for all eight.
  - `setup_pick_place_controller(source_paths=sorted_cube_paths,
    drop_targets={p: ...}, ...)` — controller cannot find any cubes
    to pick.
  This is a **functional dead-end**, not a Pydantic violation — but
  the canonical cannot succeed. `verify_args.cube_paths` AND
  `role_defaults.workpieces[*].path` ALSO reference `/World/Cube_N`,
  so the contract is consistently wrong everywhere.
  - **Note:** failure_modes[0] in the template explicitly anticipates
    naming-mismatch risk but suggests "probe with list_all_prims and
    adjust" — i.e., the canonical author was aware. The fix is to
    either (a) rewrite the code to use `/World/HeapZone/Item_N` paths
    (and align verify_args + role_defaults), or (b) extend
    `create_heap_zone` schema/handler to accept a `cube_path_template`
    kwarg.

- `get_bounding_box(prim_path=...)` kwarg — confirmed in
  `GetBoundingBoxArgs` (_models.py:2901). The kwarg name itself is
  correct (sonnet's prediction held). Only the prim-paths being passed
  to it are wrong.
- Otherwise schema-clean.

---

## §3 Verdict

**RED** — Audit #3 finds **3 BLOCKERs** (2 in A4, 1 in A8) plus
**2 MEDs** (A2 semantic label, A3 barcode kwargs) and **2 LOWs**
(A5 diagnose_ros2, A4 wrist-index comment).

Sonnet R-A-fix-2's prediction of "3–6 MED items" **did NOT hold**:
- emit_ros2_control_yaml (A5) — was GREEN, no issue.
- iterate_reward (A7) — was GREEN, no issue.
- create_heap_zone (A8) — issue is not schema, it's semantic
  prim-path mismatch (handler emits HeapZone/Item_N, canonical uses
  /World/Cube_N). **BLOCKER**.
- get_bounding_box (A8) kwarg — GREEN.
- setup_robot_claim_mutex (A6) — GREEN.
- setup_robot_handoff_signal (A6) — GREEN (and present, not removed).

The real new finding the patcher missed is the A4 `plan_trajectory` /
`set_joint_targets` schema mismatch (`robot_path` vs
`articulation_path`, `target_pose`/`positions` vs `waypoints`/
`target_position`). A4 turn-faucet **will not validate** under
Pydantic. Audit #1 and #2 fixed surrounding A4 kwargs but did not
touch the motion-control invocations.

---

## §4 Patch backlog

### BLOCKERs (must fix before canonical-creation resumes)

1. **A4 `plan_trajectory` × 4 call sites** (code: 2 calls, code_template:
   2 calls). Rewrite as:
   ```
   plan_trajectory(
       articulation_path="/World/Franka",
       waypoints=[
           {"position": [0.05, 0.35, 1.05],
            "orientation": [1.0, 0.0, 0.0, 0.0]}
       ],
       robot_type="franka",
   )
   ```
   Drop `planning_obstacles` and `close_gripper` (no schema field; if
   collision-aware planning is required, switch to `move_to_pose` or
   `setup_pick_place_controller`-based approach which DOES support
   obstacles).

2. **A4 `set_joint_targets` × 2 call sites** (code + code_template).
   Rewrite as either:
   - One-joint form (matches current handler):
     `set_joint_targets(articulation_path="/World/Franka",
        joint_name="panda_joint7", target_position=<radians>)`
   - Or extend the schema/handler to accept `joint_targets: Dict[int,
     float]` + drive-gain overrides if the canonical truly needs
     multi-joint dict semantics. Recommendation: drop stiffness/
     damping (use `set_drive_gains` on the specific joint separately
     if needed; current handler ignores them anyway).

3. **A8 prim-path semantic mismatch** (BLOCKER): pick ONE of
   - Replace `cube_paths = [...Cube_N...]` with
     `cube_paths = [f"/World/HeapZone/Item_{i+1}" for i in range(8)]`
     AND update `verify_args.cube_paths`,
     `role_defaults.workpieces[*].path`, and `code_template` accordingly.
   - OR extend `_handle_create_heap_zone` to accept
     `item_path_template: str = "{heap_path}/Item_{i}"` so the canonical
     can request `/World/Cube_{i+1}`.

### MED (recommended)

4. **A2 `set_semantic_label`** — change call signature to use schema
   fields. Suggestion: `set_semantic_label(prim_path=path,
   class_name=label, semantic_type="kit_part")` (so the part-class is
   actually emitted in the Semantics_<type> data attr).

5. **A3 `barcode_reader_sensor`** — either trim canonical to
   `scan_radius` (= longest scan_volume axis / 2) OR extend schema +
   handler to accept `scan_volume: List[float]` and `read_attribute:
   str`. Current behaviour silently drops these.

### LOW

6. **A4 wrist-index** — fix comment "joint index 6 = Franka flange
   joint" if you intend the 7th DOF (panda_joint7 is index 6 in
   zero-indexed array but is the wrist not the flange).
7. **A5 `diagnose_ros2`** in `code_template` — extras are absorbed.
   Either trim them or extend `DiagnoseRos2Args` to accept
   `robot_path / expected_topics / check_qos` so the agent's intent is
   not silently lost.

---

## §5 Recommended standing defense

Once A4 + A8 patches land (R-A-fix-3), add a `tool_call_schema_lint`
stage to the template-creation pipeline:

```
for tool_name, kwargs in extract_tool_calls(template["code"]):
    model = TOOL_ARG_MODELS.get(tool_name)
    if not model:
        warn(f"unknown tool: {tool_name}")
        continue
    try:
        model(**kwargs)
    except ValidationError as e:
        error(f"{tool_name}: {e}")
```

This would have caught all three rounds of BLOCKER drift at template-
authoring time, not at audit time. Place under
`scripts/qa/lint_canonical_tool_calls.py` and wire into the canonical
acceptance gate next to the existing form-gate.

Additionally, a `prim_path_reachability_check` stage would catch the
A8-class issue: parse all `prim_path=` / `source_paths=...` usages and
verify each path is created by some `create_prim` / `create_X` call
upstream OR by a known-emitter handler (with its emission template).

---

End of audit #3.
