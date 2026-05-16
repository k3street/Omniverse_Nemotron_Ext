# Opus Round-2 audit — A1-A8 sonnet-drafted canonicals (2026-05-16)

Audit of 8 CP-NEW templates drafted 2026-05-16. Each currently labelled
`drafted-2026-05-16; form-gate ⏳; function-gate ⏳`. Lint passed; no
function-gate runs yet.

## §1 Methodology

| Template | Depth | Angles covered |
|---|---|---|
| A1 palletizer-layer-stack | full | 1, 2, 3, 4 |
| A4 turn-faucet            | full | 1, 2, 3, 4 |
| A5 ros2-bridge-franka     | full | 1, 2, 3, 4 |
| A7 eureka-pick-place-reward | full | 1, 2, 3, 4 |
| A2 kit-prep-operator       | sampled | 2 (honesty/schema) |
| A3 barcode-scanner-divert  | sampled | 1 (reachability) |
| A6 assembly-line-4robot    | sampled | 2 (honesty/schema) |
| A8 heap-zone-unstack       | sampled | 2 (handler-schema) |

All eight files exist on disk
(`/home/anton/projects/Omniverse_Nemotron_Ext/workspace/templates/CP-NEW-*.json`);
ChromaDB `tool_index/` is present (chroma.sqlite3 + 6 collection UUIDs).
Auto-reindex is via `_rehydrate_cache` →
`template_retriever.py:70-103`.

Schema verification was done against
`service/isaac_assist_service/chat/tools/handlers/_models.py`. Pattern-hint
routing was tested against
`service/isaac_assist_service/multimodal/text_modality.py:56-83`.

## §2 Findings per Angle

### Angle 1 — Reachability

- **A1-A8 all picked up by `_rehydrate_cache`**: it reads every
  `workspace/templates/*.json` on first `_get_collection()` call
  (`template_retriever.py:90`). ChromaDB sqlite is present, but
  cache is only populated when service starts.
- **Pattern-hint routing (HIGH issue)**:
  - A7 (`pattern_hint=train`): user prompts likely to match this
    canonical ("Eureka reward", "shape a reward function") do NOT
    trigger the `train` regex at
    `text_modality.py:65`. The `train` rule keys on
    `rl train`, `train.*rl`, `rl.games`, `rsl.rl`,
    `isaac.?lab env`, `sdg pipeline`, `sim.to.real`, `sim2real`,
    `clone_envs`, `parallel.*env`. None of "eureka", "reward
    shaping", "reward function" trigger it. So user prompts for A7
    fall through to the `pick_place` default
    (`text_modality.py:83`) — filter-by-intent on
    `pattern_hint=train` would never surface A7. **Severity: HIGH**.
    The R12 claim that train-rules cover A7 is technically true
    (rules exist) but they don't cover A7's actual lexicon.
  - A3 (`pattern_hint=sort`): user prompt "barcode sorter" does NOT
    match `\bsort\b` (word-boundary blocks "sorter").
    "lane divert" / "SKU divert" likewise miss every rule. Only
    explicit "sort" / "routing" / "classify" / "by label" trigger
    the rule. **Severity: MED** — fragile reachability for
    barcode-driven flows.
  - A8 (`pattern_hint=reorient`): "heap zone", "destack", "unstack",
    "pile" do NOT match the reorient rule (which keys on
    `reorient`, `flip`, `upright`, `tip over`, `stand up`,
    `rotate.*correct`). User prompts for destacking fall to
    `pick_place` default. **Severity: MED**.
  - A4 (`pattern_hint=other`): "turn faucet", "rotate handle",
    "open valve" → fall to `pick_place` default. Since `other`
    pattern has no positive routing rule, this can only be
    reached by tag-based / vector retrieval, not by intent
    filter. **Severity: LOW** (acceptable for `other`).
- **structural_tags**: A1-A8 use the
  `isaac:segment.subsegment` convention consistently. No `cad:` or
  `user:` tags appear in this set. Tags are correctly formed.

### Angle 2 — Honesty (verified_status vs reality)

- **A1 BLOCKER**: `tools_used` lists `compute_stack_placement` and
  the goal claims `compute_stack_placement(pattern='grid_2x3')
  computes the 6 grid drop-positions in advance`
  (`CP-NEW-palletizer-layer-stack.json:3`), but the `code` block
  (lines 89-117 in `drop_targets` dict) hand-codes the 6 positions —
  `compute_stack_placement(...)` is never called.
  **Severity: HIGH** — misleading provenance.

- **A5 BLOCKER**: `setup_ros2_bridge` schema requires `profile`
  (`_models.py:2338`, e.g. `franka_moveit2`). A5's call
  (`CP-NEW-ros2-bridge-franka.json:18`) passes
  `robot_path/publish_joint_states/joint_states_topic/joint_states_rate_hz/
  subscribe_joint_commands/joint_commands_topic/frame_id` — no
  `profile`. Pydantic validation fails with `field required`.
  **Severity: BLOCKER**.

- **A5 BLOCKER**: `configure_ros2_time` schema requires `mode`
  (`_models.py:1916`); A5 passes `use_sim_time/clock_topic/
  publish_rate_hz`. No `mode` → validation error. **Severity:
  BLOCKER**.

- **A5 HIGH**: `emit_ros2_control_yaml` schema has only
  `robot_path/controller_type/output_path/joint_states_topic/
  joint_commands_topic/update_rate_hz` (`_models.py:399-408`). A5
  passes `hardware_plugin` + `controller_names=[...]` —
  `extra='allow'` accepts but the handler ignores them, so the
  generated YAML is wrong / partial. **Severity: HIGH**.

- **A7 BLOCKER**: All four Eureka tool signatures are wrong:
  - `generate_reward` requires `task_description` + `env_source_path`
    (path to a `.py` file on disk, `_models.py:1332-1333`). A7
    passes `env_name/obs_keys/output_path` and never produces an
    env source file. Validation fails.
  - `iterate_reward` requires `prev_reward_code` + `metrics`
    (`_models.py:1351-1352`). A7 passes `reward_id/n_generations/
    n_rollout_steps/feedback_mode/output_dir`. Validation fails.
  - `evaluate_reward` requires `reward_code` + `env_id`
    (`_models.py:1342-1343`). A7 passes `reward_id/n_eval_episodes/
    success_threshold`. Validation fails.
  - `create_isaaclab_env` requires `task_name/robot_path/task_type`
    (`_models.py:764-766`). A7 passes
    `env_name/template_path/n_envs/obs_keys/action_keys/reward_func`.
    Validation fails.
  All four are required-field violations → `verified_status`
  "drafted; lint passed" is misleading; this won't even validate.
  **Severity: BLOCKER**. A7 success criterion
  (`evaluate_reward score >= 0.50`) cannot be measured if no Eureka
  call validates.

- **A6 BLOCKER**: `setup_robot_claim_mutex` schema
  (`_models.py:1063-1065`) has fields
  `mutex_path/resource_path/robots`. A6 passes
  `mutex_path/robots/tray_path` (`tray_path` → `extra='allow'`,
  so it's accepted but the handler reads `resource_path`).
  The mutex never binds to the tray. **Severity: HIGH**.

- **A6 BLOCKER**: `setup_robot_handoff_signal` schema
  (`_models.py:1072-1075`) requires `handoff_path` and uses
  `robot_a/robot_b`. A6 passes
  `upstream_robot/downstream_robot/signal_path/trigger_on` — none
  match the schema; `handoff_path` is required → validation
  error. **Severity: BLOCKER**.

- **A4 HIGH**: `create_articulated_joint` schema
  (`_models.py:1037-1044`) requires `joint_path` + `body1_path`,
  with `body0_path/joint_type/axis(List[float])/limit_lower/
  limit_upper/drive_type` as optionals. A4 passes
  `joint_path/joint_type/parent_prim/child_prim/axis="Z"(string)/
  position/damping/stiffness/friction`. Required `body1_path` is
  missing; `axis` is a string not `List[float]`; six other fields
  are `extra='allow'`-absorbed and silently dropped. The joint
  will not be created correctly. **Severity: BLOCKER**.

- **A4 HIGH**: `set_joint_limits` schema (`_models.py:3175-3177`)
  requires `joint_path/lower/upper`. A4 passes
  `joint_path/lower_deg/upper_deg`. Required fields missing →
  validation error. **Severity: HIGH**.

- **A8 HIGH**: `create_heap_zone` schema
  (`_models.py:998-1002`) requires `heap_path` and uses
  `center/radius/n_items/item_size`. A8 passes
  `zone_path/position/size/n_items/item_size/item_prim_prefix`.
  Required `heap_path` missing; `zone_path/position/size/
  item_prim_prefix` not in schema. **Severity: BLOCKER**.

- **A1 MED**: `robot_wizard(robot_name="ur10")` — the schema
  description (`_models.py:1589`) lists only
  `franka_panda` (with aliases `franka`, `panda`) as registered.
  A1's own failure_modes #1 acknowledges this ("UR10 URDF not
  found in robot_wizard registry"). UR10 will likely fail at
  runtime. **Severity: MED**.

- **A2 MED**: `create_kit_tray` schema (`_models.py:1085`) says
  `slot_layout='grid_RxC' or 'row_N'`. A2 passes `slot_layout=
  "linear_5"` — not in documented enum. Handler may default to
  grid_5x1 or reject. **Severity: MED**.

- **A3 MED**: `barcode_reader_sensor` schema (`_models.py:1017-
  1019`) takes `sensor_path/position/scan_radius`. A3 passes
  `sensor_path/position/scan_volume/read_attribute` —
  `scan_volume` and `read_attribute` are `extra='allow'` absorbed
  but handler reads `scan_radius`. Scan-volume semantics get
  dropped. **Severity: MED**.

- **`motion_controllers`**: A1 declares `untested: [curobo, rmpflow]`
  but the failure-mode #4 references CP-09 handler fix for cuRobo
  drop-target computation. Per memory note
  `project_isaac_assist_motion_controller_tag.md`, `verified` is
  empty until function-gate confirms. Honest. **Severity: LOW**.
  Same logic across A1-A8: all `verified: []`, which matches the
  drafted status — honest.

### Angle 3 — Schema & code quality

- **LOC**: A1=152, A2=146, A3=180, A4=128, A5=83, A6=187, A7=117,
  A8=180. All ≥83. A5 is shortest (plumbing-only) — acceptable
  given it has no `code_template`/`role_defaults` outside the
  primary_robot. A5 lacks a `role_defaults.primary_robot.class`
  alias used in template, but it's there in role_defaults
  (`primary_robot.class=franka_panda`). **Severity: LOW**.

- **`code_template` parameterization**: A1, A2, A4, A5, A7
  use `{{role.field}}`; A6 has no `code_template` (single-instance
  scene). A8 uses `{{workpieces[i].path}} for i in range(...)`
  which is Python list-comprehension syntax interpolated, not a
  template substitution. **Severity: MED — A8 template will not
  re-instantiate cleanly**.

- **structural_tags**: All A1-A8 use the
  `isaac:segment.subsegment` form. No malformed tags.

- **Internal-numeric consistency**:
  - A1 settle_state lists 6 boxes at `[*, 0.45, 0.855]` — matches
    `code`'s `box_x_positions` and `box_paths` ✓.
  - A6 has 4 Franka stations at x=±1.5,±0.5 — matches
    settle_state ✓.
  - A4 settle_state references `joint_path`
    `/World/FaucetJoint` — matches `code` ✓.
  - A7 `simulate_args: null` plus `verify_args.stages: []` — the
    function-gate evaluator must handle the null case
    explicitly. **Severity: LOW** (probably OK; needs handler
    check).

### Angle 4 — Inter-template consistency

- **Franka prim path convention**: A2, A3, A4, A5, A7, A8 all use
  `/World/Franka`. A6 uses `/World/FrankaA, /World/FrankaB, …`.
  Consistent. ✓
- **Franka mount height**: All Franka templates mount the robot at
  `[0, 0, 0.75]` with `[0.7071068, 0, 0, 0.7071068]` orientation
  EXCEPT A8 which uses orientation `[1, 0, 0, 0]`. Inconsistency
  hint, but for top-down destacking the identity orientation is
  intentional. **Severity: LOW**.
- **A8 `robot_wizard(robot_name="franka")`** uses bare alias vs
  A2/A3/A4/A5/A6/A7 using `franka_panda`. Schema docs say
  `franka_panda` aliases include `franka`/`panda` — both work.
  **Severity: LOW** (style only).
- **Workpiece naming convention**: A1 `Box_*`, A2 `Part_*`,
  A3 `Item_*`, A6 `WorkpieceA`, A7 `Cube_1`, A8 `Cube_*`. No
  shared registry but each is internally consistent.
- **Differentiation**: A1 (palletizer grid), A2 (5-bin kit tray),
  A3 (3-lane barcode sort), A4 (faucet rotate), A5 (ROS2 bridge),
  A6 (4-robot serial handoff), A7 (Eureka RL loop), A8 (heap
  destack). These are MEANINGFULLY different scenarios — no
  superficial copies. ✓

## §3 Highest-priority fix recommendations

1. **A7 (eureka) is unsalvageable as drafted** — every Eureka tool
   call is schema-wrong. Either:
   (a) rewrite to match real signatures
   (`generate_reward(task_description, env_source_path)`,
    `iterate_reward(prev_reward_code, metrics)`,
    `evaluate_reward(reward_code, env_id)`); requires writing a
   real env .py file on disk first, OR
   (b) downgrade A7 to a "Eureka pipeline scaffold" canonical
   where the deliverable is a workflow doc, not a runnable
   reward score.
2. **A5 (ROS2 bridge)** — switch `setup_ros2_bridge` to use
   `profile="franka_moveit2"` + `robot_path` (the documented
   single-call API). Replace `configure_ros2_time(use_sim_time=...,
   ...)` with `configure_ros2_time(mode="sim_time")`. Drop the
   `hardware_plugin/controller_names` args from
   `emit_ros2_control_yaml` (use `controller_type=` instead).
3. **A6 (assembly-line) handoff signal + mutex** — rewrite calls
   to use schema-correct kwargs:
   `setup_robot_handoff_signal(handoff_path=..., robot_a=...,
   robot_b=..., position=...)` (the schema's `robot_a` is for
   the placing robot). Similarly
   `setup_robot_claim_mutex(mutex_path, resource_path=tray_path,
   robots=[...])`.
4. **A4 (turn-faucet)** — rewrite `create_articulated_joint` to
   schema (`body1_path` required, `axis` as `List[float]` like
   `[0, 0, 1]` not `"Z"`, drop `damping/stiffness/friction/
   position` from the call). Rewrite `set_joint_limits` to use
   `lower/upper` (not `lower_deg/upper_deg`); the schema
   docstring already states degrees-for-revolute.
5. **A8 (heap-zone)** — switch to `heap_path/center/radius` for
   `create_heap_zone`. Remove `item_prim_prefix` arg (handler
   doesn't accept it; the prim-naming convention is fixed by the
   handler).

## §4 Round-3 patch backlog

- Land §3 fixes 1-5 as separate commits; verify each via
  `live_smoke` against a single live Kit RPC after each fix.
- Add a **schema-check** lint pass to `lint_template.py` that
  validates each `tools_used` call site against
  `handlers/_models.py` arg schemas (Pydantic). This audit found
  10 schema mismatches across 6 templates that lint failed to
  catch.
- Add **pattern-hint reachability check** to lint: for each
  template, run `_detect_pattern_hint` on the template's
  `goal` text; if the detected hint differs from declared
  `intent.pattern_hint`, warn. (Caveat: rule-based routing keys
  on USER prompts not template goals; the check still surfaces
  the lexicon-mismatch class of bug.)
- Extend `_PATTERN_RULES` train rule to include `eureka`,
  `reward\s+(function|shaping|design)`, `reward.*tune` so A7's
  topic becomes routable.
- Extend `_PATTERN_RULES` reorient rule to include `destack`,
  `unstack`, `pile`, `heap` for A8.
- Audit fixes-pass: rerun this 4-angle audit on the patched
  templates before promoting `verified_status` past `drafted`.
- For A1's `compute_stack_placement` claim — either actually call
  the tool in `code` and consume its return, or remove the
  goal-text claim that it's used.

## §5 Honest verdict

**RED — do not promote A1-A8 past `drafted` until at least the
BLOCKER-level fixes (A4, A5, A6, A7) land.** A1/A2/A3/A8 are
MED-level and YELLOW-shippable after their respective fixes; A4,
A5, A6, A7 will fail Pydantic argument validation before any
Kit RPC runs, which means their function-gate cannot be
attempted. Continuing canonical creation on top of broken
schema patterns risks compounding errors. Pause and fix this
batch first.
