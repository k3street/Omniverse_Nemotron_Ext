# R-A-enum-fix — TC_BAD_ENUM_VALUE patch for 8 A-series templates
Date: 2026-05-16

## Summary
Lint enum-validation pass surfaced 8 `TC_BAD_ENUM_VALUE` ERRORs across 7 template files
(the task description attributed one error to CP-58.json but it was actually in
CP-NEW-peg-bushing-impedance.json; CP-58 was already clean at 0 ERROR).

---

## Per-template fixes

### 1. CP-NEW-peg-bushing-impedance.json
- **Tool**: `validate_assembly_constraint.type`
- **Before**: `insertion_axis`
- **After**: `coincident_axes`
- **Semantic equivalence**: Both describe axis-aligned assembly relationships. `insertion_axis`
  is not a valid enum value; `coincident_axes` expresses the same geometric constraint (peg
  axis must coincide with bushing axis) and is the best match from
  `['coincident_axes', 'concentric', 'tangent', 'parallel_planes', 'fixed_offset',
  'angle_between', 'distance_between']`. The handler docs confirm `coincident_axes` is the
  correct type for coaxial peg-in-hole alignment.
- **Note**: `concentric` was considered but describes radial co-centering; `coincident_axes`
  captures both radial and axial alignment needed for insertion.

### 2. CP-NEW-rl-clone-env.json
- **Tool**: `launch_training.algo`
- **Before**: `'PPO'` (uppercase)
- **After**: `'ppo'` (lowercase)
- **Semantic equivalence**: Exact same algorithm; enum values are lowercase per schema
  `['ppo', 'sac', 'td3', 'rsl_rl']`. Pure case normalisation.
- **Note**: Template also has pre-existing `TC_REQUIRED_MISSING` errors on `clone_envs` and
  `create_isaaclab_env` (missing required args) — these are legacy backlog, not in scope.

### 3. CP-NEW-g1-bimanual-tabletop.json
- **Tool**: `setup_whole_body_control.robot_profile`
- **Before**: `unitree_g1`
- **After**: `g1`
- **Semantic equivalence**: Same robot (Unitree G1). The profile registry drops the vendor
  prefix; valid values are `['g1', 'h1', 'figure02', 'generic']`. The template continues to
  target the G1 via `robot_wizard(robot_name="g1", ...)` — profile and robot_wizard are
  consistent after fix.

### 4. CP-NEW-dr-curriculum-trainer.json
- **Tool**: `create_isaaclab_env.task_type`
- **Before**: `pick_and_place`
- **After**: `manipulation`
- **Semantic equivalence**: Pick-and-place is a manipulation task; `manipulation` is the
  broadest matching value in `['manipulation', 'locomotion', 'navigation', 'custom']`.
  `suggest_dr_ranges(task_type="pick_and_place", ...)` retains its original value because
  that field has no enum constraint (free-form).

### 5. CP-NEW-parallel-env-scaling-32.json (2 fixes)

#### 5a. check_vram_headroom.operation
- **Before**: `rl_training`
- **After**: `train`
- **Semantic equivalence**: RL training IS training. Valid enum `['clone', 'train', 'sdg',
  'render', 'custom']`. The handler maps `train` to the RL-training VRAM profile which is
  exactly the scenario the template exercises (32-env PPO).

#### 5b. create_isaaclab_env.task_type
- **Before**: `reach`
- **After**: `manipulation`
- **Semantic equivalence**: A reach task is an arm-manipulation task; `manipulation` is
  correct in `['manipulation', 'locomotion', 'navigation', 'custom']`. The task is Franka
  reaching a target — manipulation is semantically sound.

### 6. CP-NEW-groot-finetune-n10-demos.json
- **Tool**: `suggest_finetune_config.task_type`
- **Before**: `tabletop pick-and-place`
- **After**: `similar_to_pretrain`
- **Semantic equivalence**: GR00T N1 was pretrained on tabletop manipulation (pick-and-place)
  tasks. `similar_to_pretrain` selects "freeze vision+language, tune DiT+connectors" — the
  correct strategy when fine-tuning on data similar to pretraining. Valid enum:
  `['similar_to_pretrain', 'new_visual_domain', 'new_embodiment']`. `new_visual_domain` was
  considered (for industrial assets) but the template explicitly uses sim-rendered tabletop
  data matching the pretraining distribution, making `similar_to_pretrain` the right choice.
- **Note**: `suggest_data_mix(task_type="tabletop pick-and-place", ...)` retains original
  value — that field is free-form.

### 7. CP-NEW-groot-load-eval-live.json
- **Tool**: `check_vram_headroom.operation`
- **Before**: `groot_inference`
- **After**: `custom`
- **Semantic equivalence**: `groot_inference` is not in `['clone', 'train', 'sdg', 'render',
  'custom']`. No single value semantically captures "GR00T inference"; `custom` is the
  appropriate escape-hatch value per schema design. `render` was considered (GPU rendering
  similarity) but is misleading — the operation is model inference, not image rendering.

---

## Reverts
None. All 8 enum errors had a valid semantic mapping.

---

## Lint status post-fix
```
8 templates scanned — 0 TC_BAD_ENUM_VALUE ERRORs
Remaining ERRORs: TC_REQUIRED_MISSING on CP-NEW-rl-clone-env.json (3 errors, pre-existing backlog)
CP-58.json: 0 ERROR (was already clean; insertion_axis was in CP-NEW-peg-bushing-impedance.json)
```

All `verified_status` fields appended with `; patched-r-a-enum-fix`.
