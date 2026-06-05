# Role-Based Schema Migration — Round 7 Scale Report

**Date:** 2026-05-15
**Agent:** Sonnet 4.6 (Round 7)
**Cohort:** CP-39 through CP-58
**Reference:** `docs/research/2026-05-15-round6-migration-scale.md` (Round 6)

---

## §1 Cohort Survey

| Template | Classify | Reason |
|----------|----------|--------|
| CP-39 | MIGRATE | drop_targets LIST form (4 cubes, Pallet), function-gate ✓ |
| CP-40 | SKIP | draft (wilson_lower=0.0127) |
| CP-41 | MIGRATE | mixed-mass (varying density), function-gate ✓ |
| CP-42 | MIGRATE | rectangular bricks 2×2 grid, function-gate ✓ |
| CP-43 | MIGRATE | sphere-pick (4 spheres), function-gate ✓ |
| CP-44 | MIGRATE | mixed geometry (2 cube+2 sphere), function-gate ✓ |
| CP-45 | MIGRATE | side-mounted robot (+X offset), function-gate ✓ |
| CP-46 | SKIP | stable_fail per prior audit |
| CP-47 | MIGRATE | runtime-vision sort 2-color (setup_pick_place_with_vision), function-gate ✓ |
| CP-48 | SKIP | draft (wilson_lower=0.0385) |
| CP-49 | MIGRATE | kitting station (create_kit_tray + drop_targets), function-gate ✓ |
| CP-50 | SKIP | vision-kitting with dual trays — no drop_target param in setup_pick_place_with_vision; novel pattern, needs dedicated design |
| CP-51 | SKIP | draft (wilson_lower=0.2759) |
| CP-52 | SKIP | draft (wilson_lower=0.3866) |
| CP-53 | SKIP | draft (wilson_lower=0.2447) |
| CP-54 | MIGRATE | surface_gripper (suction), 4 cubes, function-gate ✓ |
| CP-55 | SKIP | drawer-open: no pick_place controller, uses create_articulated_joint only — novel manipulation pattern; no equivalence basis |
| CP-56 | MIGRATE | rotary table (create_rotary_table), 4 cubes, function-gate ✓ |
| CP-57 | SKIP | draft (wilson_lower=0.1018) |
| CP-58 | SKIP | function-gate ✗ (peg-in-hole false-positive) |

**MIGRATE: 10, SKIP: 10**

---

## §2 Float-Precision Fixes

Both were caught by equivalence test on first run — no reverts needed, both fixed and passed.

**CP-45 — side-mounted robot float positions.**
Legacy code computes `0.7 + rel_x` for rel_x in `[-1.15, -0.9, -0.65]`, producing
`-0.44999999999999996`, `-0.20000000000000007`, `0.04999999999999993`.
role_defaults initially stored rounded values; fixed to exact IEEE-754 repr.

**CP-56 — rotary table cube positions int vs float.**
Legacy loop uses `(0.62, 0)` and `(0.38, 0)` — y is an int `0`, not `float 0.0`.
JSON round-trips `0` as int and `0.0` as float; these produce different repr in
`_normalize`. Fixed by storing `0` (not `0.0`) in role_defaults workpiece positions.

**No templates reverted.**

---

## §3 New Patterns Observed

### `drop_targets` LIST form (CP-39)
CP-39 is the first template using `drop_targets=[list]` rather than `drop_targets={dict}`.
The list is parallel to `source_paths` — positional not path-keyed. The code_template
uses `{{workpieces[N].drop_target}}` in list syntax identically to the dict form;
only the outer brackets differ. Equivalence confirmed.

### `isaac:workpiece.*` structural_tag namespace (CP-41, CP-42, CP-43, CP-44)
Four templates introduce workpiece-material sub-tags:
- `isaac:workpiece.varying_mass` — per-cube density set via `physics:density`
- `isaac:workpiece.brick_shaped` — non-cube Cube primitive with non-uniform scale
- `isaac:workpiece.sphere` — Sphere primitive, radius instead of size
- `isaac:workpiece.mixed_geometry` — mixed Cube+Sphere in same source_paths

### Per-workpiece `density` field in role_defaults (CP-41)
CP-41 is the first template where workpieces carry a physics property (`density`) in
role_defaults, used in `set_attribute(attr_name="physics:density", value={{...}})`.
Pattern: workpiece sub-fields are not limited to position/path/drop_target — any
per-object parameter that varies across workpieces should live here.

### `isaac:robot.side_mounted` tag (CP-45)
CP-45 is the first template where robot/conveyor/bin/sensor are all offset to the +X
axis. The role_defaults positions encode the absolute world positions directly (no
"base + offset" arithmetic). The Table prim is hardcoded at `[0.7, 0, 0.375]` in
code_template (not parameterized) since it's a structural detail not a role.

### `setup_pick_place_with_vision` pattern (CP-47)
CP-47 uses `setup_pick_place_with_vision` instead of `setup_pick_place_controller`.
The schema is `pattern_hint=sort`, `routing_axis=color`. Named roles `red_destination`
and `blue_destination` (reusing Round 6 CP-32/33 pattern). The `routing.color_routing`
dict is stored in role_defaults and substituted as a Python dict literal in
`destination_map={...}`. Equivalence passes because the dict substitution renders
`{"red": "/World/RedBin", "blue": "/World/BlueBin"}` identically to the legacy literal.

### `create_kit_tray` destination (CP-49)
CP-49 introduces `destination_kind="fixture"` with `create_kit_tray` (not `create_bin`).
The kit-tray call has extra fields: `tray_size`, `slot_layout`, `slot_size`, `slot_spacing`.
These are stored on `primary_destination` role_defaults and substituted individually.
`drop_targets` uses the dict form keyed by workpiece path.

### `surface_gripper` extra call (CP-54)
CP-54 introduces `surface_gripper(robot_path, ee_link, grip_threshold, force_limit, torque_limit)`
as an extra call after robot_wizard and before setup_pick_place_controller. `ee_link` is
stored on `primary_robot` role_defaults as `"ee_link": "/World/Franka/panda_hand"`.
New structural_tag: `isaac:gripper.surface_suction`.

### `create_rotary_table` as feed mechanism (CP-56)
CP-56 introduces a `rotary_table` role with `disc_path` sub-field. The `belt_path` in
`setup_pick_place_controller` points to `{{rotary_table.disc_path}}` not a conveyor.
`uses_conveyor_transport: false` — first template with this combination. New tag:
`isaac:transport.rotary_table`.

---

## §4 Schema Gaps Surfaced

1. **`destination_kind: "kit_tray"` not in VALID_DESTINATION_KINDS** — CP-49 uses `fixture`
   as destination_kind (kit trays ARE fixtures). `kit_tray` is expressed via structural_tag
   `isaac:destination.kit_tray`. No schema change needed.

2. **`create_kit_tray` slot parameters** — `tray_size`, `slot_layout`, `slot_size`,
   `slot_spacing` are novel fields on role_defaults. No validation of role_defaults keys,
   so this works. Document as convention: extra-call tools can store their params on the
   nearest matching role.

3. **`physics:density` as workpiece sub-field** — CP-41 extends the workpiece sub-field
   vocabulary beyond `path/position/drop_target/material_path/label`. Physics properties
   that vary per-workpiece can live here. No constraint in schema; works naturally.

4. **`bulk_set_attribute` with literal string `["/World/Sphere_{i+1}"]` (CP-43)** —
   The legacy code has an apparent bug: `prim_paths=["/World/Sphere_{i+1}"]` (literal
   f-string syntax in a regular string, NOT an f-string). The code_template faithfully
   reproduces this literal. Equivalence passes because the captured call is identical
   in both legacy and role paths.

---

## §5 Round 8 Recommendation

**Candidate cohort: CP-59..79 (or focused subset)**

Pre-observations:
- CP-60, CP-62, CP-76 are `stable_fail` — SKIP
- CP-55 deferred: drawer-open needs `pattern_hint="manipulate"` or similar new enum before migration
- CP-50 deferred: vision-kitting with dual trays needs `setup_pick_place_with_vision` + per-tray routing — complex; may need a new `destination_kind: "n_trays_routed"` or `routing_axis: "color"` + kit_tray combination
- Draft templates (wilson_lower < 0.5) in CP-59..79 range: check before batch migration
- Focus on: multi-robot, AMR/mobile, ROS2-bridge, and additional pick_place variants
