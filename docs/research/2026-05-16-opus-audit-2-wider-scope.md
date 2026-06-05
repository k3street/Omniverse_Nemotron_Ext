# Opus Audit #2 — Wider Scope on A1-A8 + R-A-fix

**Date:** 2026-05-16
**Audit target:** A1-A8 canonical drafts (post R-A-fix commit `7563e52`)
**Prior audit:** Audit #1 (commit `9af3da7`, doc `2026-05-16-opus-audit-A1-A8.md`) found 4 BLOCKERs in A4-A7; patched by R-A-fix
**Scope:** angles missed in Audit #1 — regressions from R-A-fix, code/template equivalence, R-series consistency, post-R12-schema gaps, semantic-realism walkthrough

## §1 Methodology

| Angle | Depth | Method |
|-------|-------|--------|
| A — R-A-fix regressions | DEEP | Read `git show 7563e52 -- workspace/templates/`; map every `[+ -]` to schema |
| B — code vs code_template equivalence | MEDIUM | Mental substitution for A1/A4/A6 templates against `role_defaults`; spot-check `_format_for_code` output shape |
| C — A-series vs R-series consistency | MEDIUM | Compared A1 vs CP-09 (palletizer parallel), A6 vs CP-52 (mutex parallel), checked role-key conventions + motion_controllers shape |
| D — post-R12 schema regressions | LIGHT | Inspected CP-09, CP-58, CP-NEW-inspect-reject, CP-NEW-opcua-12conveyors verified_status + intent honesty |
| E — semantic realism | MEDIUM | Walked through A1 (palletizer geometry/reach) and A6 (4-robot chain reach/handoff z-heights) |

No tests executed (per Bun-mitigation constraint). No commits/patches.

## §2 Findings

### Angle A — R-A-fix regressions: **NEW BLOCKERS FOUND**

R-A-fix correctly patched `create_articulated_joint`, `set_joint_limits` kwargs in A4; correctly schema-aligned A5's `setup_ros2_bridge`/`emit_ros2_control_yaml`; correctly fixed A6 mutex/handoff prim creators; correctly schema-aligned A7's Eureka tool calls per their generate/iterate/evaluate signatures; correctly fixed A8's `create_heap_zone` schema.

But four other tool-call kwarg violations remain unfixed in the same files:

#### A4 turn-faucet — **BLOCKER**
File: `workspace/templates/CP-NEW-turn-faucet.json`
- Line ~115 in `code`: `get_joint_positions(robot_path="/World/Franka")` — schema (`tool_schemas.py:6714-6723`) requires single param `articulation` (string). `robot_path` not accepted.
- Line ~127 in `code`: `get_joint_positions(articulation_prim="/World/FaucetBody")` — same problem; `articulation_prim` not accepted.
- `code_template` line ~62 has the same `robot_path={{primary_robot.path}}` typo.
- Severity: BLOCKER. Handler models (`_models.py:32`) use `extra='allow'`, so the call won't raise; but the required positional `articulation` is missing, so handler will receive empty / KeyError on read.

#### A5 ros2-bridge-franka — **BLOCKER (×2)**
File: `workspace/templates/CP-NEW-ros2-bridge-franka.json`
1. `ros2_subscribe_once(topic="/joint_states", timeout_s=5.0)` — schema (`tool_schemas.py:1391-1402`) **requires** `topic` AND `msg_type`. `msg_type` is missing. Also `timeout_s` should be `timeout` per schema. Severity: BLOCKER (required field missing → handler will fail or no-op).
2. `diagnose_ros2(robot_path=..., expected_topics=..., check_qos=True)` — schema (`tool_schemas.py:4177-4185`) declares **no params** (`properties: {}, required: []`). All three kwargs are silently dropped under `extra='allow'`. The user's intent (check specific topics + QoS for the Franka robot) is not honored. Severity: BLOCKER on intent (call succeeds but does nothing about the topics/QoS).

#### A6 assembly-line-4robot-handoff — **BLOCKER (semantic; ×3 stations × 3 kwargs)**
File: `workspace/templates/CP-NEW-assembly-line-4robot-handoff.json`
- All 4 `setup_pick_place_controller` calls use `handoff_signal=...`, `claim_mutex=...`, and (for B/C/D) `activation_signal=...`. None of these are in `setup_pick_place_controller` schema (`tool_schemas.py:3767-3829`). Grep against `service/isaac_assist_service/chat/tools/handlers/` confirms handler does NOT read them either. Severity: BLOCKER on intent — controllers will install without any of the gating that the goal text and `structural_features.has_handoff_signals: true` advertise. The 4 robots will race, not pipeline.
- Cross-cutting: CP-52 uses `mutex_path=` (also not in schema, but at least the naming convention is established). A6 uses `claim_mutex=` — different name. Inconsistent. Severity: MED.

#### A7 eureka-pick-place-reward — **BLOCKER (×2)**
File: `workspace/templates/CP-NEW-eureka-pick-place-reward.json`
1. `review_reward(reward_id=..., output_path=...)` — schema (`tool_schemas.py:4720-4731`) **requires** `reward_code` (the code string). The call passes a run-id instead. Severity: BLOCKER (required field missing).
2. `checkpoint_training(reward_id=..., checkpoint_dir=...)` — schema (`tool_schemas.py:8809-8847`) accepts `run_id`, `include_replay_buffer`, `tag`. `reward_id` and `checkpoint_dir` are silently dropped under `extra='allow'`. The checkpoint goes to default location, not `/tmp/eureka_pp_checkpoint`. Severity: BLOCKER on intent.
3. `create_isaaclab_env(task_type="pick_place", ...)` — schema enum is `["manipulation", "locomotion", "navigation", "custom"]`. `pick_place` is not in enum. Pydantic `_models.py:766` has `task_type: str` (no enum constraint at validation), so handler receives the value as-is; but downstream handler logic likely doesn't recognise the type. Severity: MED-HIGH (silent drift, handler may default).

#### A8 heap-zone-unstack — **BLOCKER on intent**
File: `workspace/templates/CP-NEW-heap-zone-unstack.json`
- `setup_pick_place_controller(target_source="bounding_box_height", ...)` — `target_source` enum is `["auto","native","spline","curobo","diffik","osc","sensor_gated","fixed_poses","cube_tracking","ros2_cmd"]`. `bounding_box_height` is NOT in enum.
- Adjacent kwargs not in schema: `source_zone_path`, `drop_point`, `transit_height`, `top_down_approach`, `grasp_z_offset`, `arrival_sensor_path`, `settle_ticks`.
- Severity: BLOCKER — the height-ranked unstacking mode the canonical advertises does not exist in the controller. Closest existing kwarg is `drop_target` (singular) for sensor_gated mode. The whole canonical pivots on a non-existent target_source.

### Angle B — code vs code_template equivalence: **CONCERNS**

Substitution engine (`canonical_instantiator.py:480-506`) `_format_for_code` correctly quotes strings via `repr` and renders lists/dicts as Python literals, so syntactic validity is preserved.

But semantic equivalence has drift:

#### A4 — code vs code_template diverge by 0.020m
- `code` 2nd `plan_trajectory` position: `[0.05, 0.35, 0.925]` (descend to grasp z = handle top + small offset)
- `code_template` substitutes `{{articulated_fixture.handle_position}}` = `[0.05, 0.35, 0.905]` (handle centre)
- Severity: MED. A scaled or LLM-instantiated variant lands the EE 2 cm too low and may collide with handle below grasp axis.

#### A4 — code_template is missing the verification loop
- `code` has a `for step in range(MAX_POLL_STEPS)` block that polls `get_joint_positions` until faucet ≥ 85°.
- `code_template` ends at `set_joint_targets(...)` — no polling, no success criterion.
- Severity: MED. Instantiated scenes won't self-verify the success condition that the `success_deg: 85.0` field in `role_defaults` declares.

#### A1 — `tools_used` lists `compute_stack_placement` but code never calls it
- The goal text says drop positions are pre-computed and baked into `drop_targets` dict. The thoughts comment-block describes what `compute_stack_placement(...)` would return.
- Severity: YELLOW. Hurts retrieval ranking (false-positive on hybrid search for `compute_stack_placement` use cases) and tool-coverage metrics. Either drop from `tools_used` or invoke once (with `expected_count: 6, n_items=6, pattern='grid_2x3'`) and reference its return.

#### A1 — `motion_controllers.untested` lists `rmpflow` but `rmpflow` is not a `target_source` enum value
- Code uses `target_source="curobo"`. Schema enum has no `rmpflow` standalone. (Native mode is RmpFlow under the hood.)
- Severity: LOW. Cosmetic but inconsistent with R-series; CP-09 lists `motion_controllers.untested = ['rmpflow', 'moveit2']` too, so this is a pre-existing convention.

### Angle C — A-series vs R-series consistency: **MOSTLY GREEN, ONE NAMING DRIFT**

Cross-comparison:

| Aspect | A-series | R-series sample | Verdict |
|--------|---------|-----------------|---------|
| `roles` keys (palletizer/conveyor task) | `primary_robot`, `input_conveyor`, `primary_destination`, `pick_sensor`, `workpieces` (A1) | Same keys in CP-09 | PASS |
| `intent.structural_features` shape | dict of bool/int/str | dict of bool/int/str | PASS |
| `intent.structural_tags` namespacing | `isaac:topology.*`, `isaac:robot.*`, `isaac:industry.*` | Same | PASS |
| `motion_controllers` shape | `{verified, untested, failed}` | `{verified, untested}` mostly | PASS (A-series adds `failed` empty dict — acceptable) |
| `verified_status` honesty marker | "function-gate ⏳" | "function-gate ✓" or "✗ (reason)" | PASS |
| Mutex kwarg on `setup_pick_place_controller` | A6 uses `claim_mutex=...` | CP-52 uses `mutex_path=...` | **NAMING DRIFT** (MED) |
| destination_kind value | A6 has `"single_bin"` for 4-robot serial chain | CP-09 has `"single_bin"`; multi-robot CP-52 uses `"single_bin"` | PASS (consistent) |

### Angle D — Post-R12 schema regressions: **GREEN**

- CP-09: `verified_status` contains real qualifier with xy_tolerance slack. Honest.
- CP-58 (R12b insert): `verified_status: ... function-gate ✗ (peg-in-hole assembly — earlier ✓ was false positive due to target_path override...)`. Honestly downgraded after audit found false-positive. Exemplary.
- CP-NEW-inspect-reject (R18 other): `verified_status: build-spec-2026-05-10; smoke-test ✓ 1/1 (52s); vision-gated routing`. Live-tested, status reflects reality.
- CP-NEW-opcua-12conveyors (R18 industrial): `BUILD_OK; plumbing-only (no cube delivery, no simulate_args)`. Plumbing scope clearly stated; no overclaiming. Exemplary.

No stale or contradictory verified_status found in the R-migrated sample.

### Angle E — Semantic realism walk-through

#### A1 palletizer — REALISTIC ✓
- UR10 base z=0.75 on 0.75m table — geometry consistent.
- Pallet at y=-0.55, scale [0.35, 0.25, 0.025] → bottom z=0.75, top z=0.80. Pallet has `PhysicsCollisionAPI` only (no RigidBodyAPI), so it's a static collider and won't fall. ✓
- Drop targets z=0.875 = pallet_top(0.80) + box_size/2(0.05) + small offset = consistent.
- 6 box pickups within UR10 1.3 m reach: max diagonal distance to far corner ≈ 0.76 m ≪ 1.3 m. ✓
- Pick sensor at conveyor x=0.35 — box at x=-0.1 (`Box_6`) will reach sensor first, in 1.7 s at 0.15 m/s, then continue downstream while picked. Believable single-box-at-a-time semantics.
- One minor inconsistency: `failure_modes` mentions `_compute_h1` from CP-09 handler fix → references a code path, fine for documentation.

#### A6 4-robot assembly chain — MOSTLY REALISTIC, semantic gating broken
- 4 Frankas at x=±1.5, ±0.5 on a 4m table (table scale [2.0, 0.25, 0.375]) — reach envelopes overlap correctly at trays.
- Tray top z=0.80; workpiece A starts at z=0.85 above InfeedStation top z=0.82 → bottom at z=0.82 = supported. ✓
- Each tray is within both adjacent robots' ~0.85 m reach (diagonal 0.74 m). ✓
- **However**: no `pick_sensor` for any station's `setup_pick_place_controller` — under cuRobo the controller free-runs. Combined with the missing `handoff_signal`/`claim_mutex` enforcement (Angle A finding), all 4 robots will start picking simultaneously. The "serial pipeline" promised by `is_serial_pipeline: true` does not materialise. Reflects in: function-gate ⏳.

## §3 Top priority fixes

1. **A4** — replace `get_joint_positions(robot_path=...)` and `(articulation_prim=...)` → `(articulation=...)` in both `code` and `code_template`. (BLOCKER)
2. **A5** — fix `ros2_subscribe_once`: add `msg_type="sensor_msgs/msg/JointState"`, rename `timeout_s` → `timeout`. Drop kwargs from `diagnose_ros2` (or extend schema to accept them, if intent is to keep them). (BLOCKER ×2)
3. **A7** — fix `review_reward(reward_code=gen0_code, ...)` instead of `reward_id`; fix `checkpoint_training(run_id=..., tag=...)` and drop `checkpoint_dir`. Optionally also normalise `task_type="manipulation"`. (BLOCKER ×2, MED ×1)
4. **A8** — pivot away from `target_source="bounding_box_height"`. Two options:
   - (a) Land a NEW target_source in the controller (`bounding_box_height`) — bigger change, requires handler work.
   - (b) Reduce A8 scope: use `target_source="curobo"` with manual per-cube `drop_targets` derived from a one-shot `get_bounding_box` call per cube. Smaller change, lands realistic unstacking semantics today.
   (BLOCKER)
5. **A6** — either land `handoff_signal`/`claim_mutex`/`activation_signal` kwargs in `setup_pick_place_controller` schema/handler, OR remove them from A6 and downgrade `structural_features.has_handoff_signals` claim. Aligning naming with CP-52's `mutex_path=` is the smaller move. (BLOCKER on intent, MED naming)

## §4 Patch backlog

| ID | Severity | File | Lines | Effort |
|----|---------|------|-------|--------|
| P1 | BLOCKER | CP-NEW-turn-faucet.json | code + code_template `get_joint_positions` kwargs | 5 min |
| P2 | BLOCKER | CP-NEW-ros2-bridge-franka.json | code + code_template `ros2_subscribe_once` add msg_type, rename timeout | 5 min |
| P3 | BLOCKER (intent) | CP-NEW-ros2-bridge-franka.json | drop unsupported kwargs from `diagnose_ros2` | 2 min |
| P4 | BLOCKER | CP-NEW-eureka-pick-place-reward.json | `review_reward` use reward_code | 5 min |
| P5 | BLOCKER (intent) | CP-NEW-eureka-pick-place-reward.json | `checkpoint_training` use run_id/tag, drop checkpoint_dir | 5 min |
| P6 | MED-HIGH | CP-NEW-eureka-pick-place-reward.json | `create_isaaclab_env` task_type="manipulation" | 1 min |
| P7 | BLOCKER (intent) | CP-NEW-heap-zone-unstack.json | pivot off `target_source="bounding_box_height"` → curobo + drop_targets dict | 20 min (scope decision) |
| P8 | BLOCKER (intent) | CP-NEW-assembly-line-4robot-handoff.json | remove or rename pick_place controller gating kwargs; align with CP-52 | 10 min |
| P9 | MED | CP-NEW-turn-faucet.json | code_template handle_position z-divergence + add polling-loop | 10 min |
| P10 | YELLOW | CP-NEW-palletizer-layer-stack.json | drop `compute_stack_placement` from tools_used OR invoke once | 5 min |

**Total backlog: 10 items.**

R-A-fix follow-up suggestion (already in `2026-05-16-R-A-fix.md` §4): add `--validate-tool-calls` linter that AST-parses `code` field and checks every call site's kwargs against the Pydantic model **including required-field presence**. Audit #1's lint already checks tool **name** existence; this audit shows the gap is the **kwarg-shape** check. P1-P5 would all have been caught.

## §5 Verdict: **YELLOW**

R-A-fix delivered partial correctness — the 4 explicitly-named BLOCKERs from Audit #1 are fixed. But the same-file commit window missed:
- 1 BLOCKER inside the same file as a fixed BLOCKER (A4 get_joint_positions)
- 4 additional BLOCKERs across A5/A7/A8 that exist on tool calls Audit #1 either didn't mention or only fixed one call within the file.

R-A-fix did NOT introduce regressions — every `[+ ]` line in the diff is correct schema-aligned code. The failure is incompleteness, not correctness: the patcher addressed the named violations and missed adjacent ones in the same files.

Cross-template consistency (Angle C) is PASS apart from one MED naming drift (`claim_mutex` vs `mutex_path`). R-series migrations (Angle D) are GREEN — verified_status fields are honest, no stale claims.

**Canonical creation should NOT resume on A4/A5/A6/A7/A8 until P1-P8 land**. A1 + A2 + A3 (untouched by R-A-fix BLOCKER list) are PASS — A1 had only YELLOW finding P10. A new canonical (A9) could start fresh, but P1-P8 patch round is the higher-value next move.

**Recommended next action**: ship one consolidated R-A-fix-2 with P1-P8 (≈45 min total), THEN resume A9 creation.
