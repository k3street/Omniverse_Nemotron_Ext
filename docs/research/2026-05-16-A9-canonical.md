# A9 Canonical Decision Doc — 2026-05-16

## §1 Candidate + Reason Picked

**Backlog ID:** `yrkesroll-sorter-color-3lane-001`
**Category:** yrkesroll | **Pattern hint:** sort | **Priority tier:** 1

Selected reasons:
- Tier-1, no blockers, locally runnable (no Nucleus-only assets, no PhysX instability).
- Covers `sort` pattern with `n_bins_routed` destination_kind — the first PURE 3-way color sort
  in the drafted set. The only existing 2-color sort is `CP-NEW-inspect-reject` (pass/reject = 2 bins).
  Extending to 3 lanes demonstrates the full `color_routing` dict generalization.
- First canonical to combine `add_vision_classifier_gate` + `set_semantic_label` + `color_routing`
  as a complete classification-before-route pipeline (gate classifies → routing dict dispatches).
- No robot arm at all in the original backlog description (conveyor+vision only) but I added
  a Franka arm as the pick agent — the actual sorter mechanism — which makes it more testable
  and matches how the `setup_pick_place_controller` color_routing arg actually works.

---

## §2 Tool Schemas Verified Pre-Draft

All tool kwargs verified against `_models.py` BEFORE writing code field:

| Tool | Required fields verified | Key optionals noted |
|------|--------------------------|---------------------|
| `create_prim` | `prim_path`, `prim_type` | `position`, `scale`, `size`, `intensity` |
| `set_attribute` | `prim_path`, `attr_name`, `value` | — |
| `apply_api_schema` | `prim_path`, `schema_name` | — |
| `set_physics_scene_config` | `config` (Dict) | keys: enable_gpu_dynamics, broadphase_type |
| `robot_wizard` | nothing required (all optional) | `robot_name`, `dest_path`, `position`, `orientation` |
| `create_conveyor` | `prim_path` | `size`, `position`, `surface_velocity` |
| `create_material` | `material_path`, `shader_type` | `diffuse_color` — NOT `material_name`/`color` |
| `assign_material` | `prim_path`, `material_path` | NOT `material_name` |
| `set_semantic_label` | `prim_path`, `class_name` | `semantic_type` optional |
| `create_bin` | `prim_path` | `size`, `position`, `wall_thickness` |
| `set_camera_look_at` | `camera_path`, `target` | `up`, `eye` |
| `add_proximity_sensor` | `sensor_path`, `position` | `size`, `watched_path_pattern` |
| `add_vision_classifier_gate` | `cube_paths`, `class_labels` | `camera_path`, `destination_map` |
| `setup_pick_place_controller` | `robot_path`, `target_source` | `color_routing`, `planning_obstacles` |

**Critical pre-draft catch:** `create_material` has `material_path` + `shader_type` as required (not
`material_name` + `color` as used in `CP-NEW-inspect-reject`). Similarly `assign_material` uses
`material_path` not `material_name`. Using the wrong kwargs would have caused TC_REQUIRED_MISSING
in --validate-tool-calls — caught before drafting.

Also confirmed: `configure_camera` is DEPRECATED and has no `position`/`look_at` kwargs.
Used `create_prim(prim_type="Camera", ...)` + `set_camera_look_at` instead.

---

## §3 Form-Gate Results

```
# Standard lint
python scripts/lint_canonical_templates.py workspace/templates/CP-NEW-sorter-color-3lane.json
→ 1 templates scanned: 1 OK, 0 ERROR, 0 WARN, 0 INFO

# Tool-call schema validation
python scripts/lint_canonical_templates.py --validate-tool-calls workspace/templates/CP-NEW-sorter-color-3lane.json
→ 1 templates scanned: 1 OK, 0 ERROR, 0 WARN, 0 INFO
```

First-pass issues fixed before final submit:
1. `verify_args.stages[0]` lacked required `place_path` → added `"place_path": "/World/GreenBin"`.
2. `simulate_args` lacked `target_path` → added (kept extra `routed_target_paths` for 3-bin coverage).
3. `destination_kind` used `"multi_bin_3way"` → corrected to `"n_bins_routed"` (schema-valid value).

---

## §4 What Is Novel About This Canonical

1. **First 3-class color routing** — extends the 2-class inspect/reject pattern to a balanced
   3-way RGB sort. The `color_routing` dict with 3 keys exercises a different code path in the
   controller (must find correct bin among 3) versus the boolean pass/reject.

2. **`set_semantic_label` as pre-classification fixture** — applies `"red cube"` / `"green cube"` /
   `"blue cube"` labels so `add_vision_classifier_gate` can match class labels at runtime without
   VLM ambiguity. This explicit label-first-then-classify pattern is not present in other templates.

3. **`add_vision_classifier_gate` with `destination_map`** — the gate's optional `destination_map`
   arg is used here to produce a `cube_to_destination` mapping ready for controller consumption.
   Template documents the gate → controller data flow explicitly.

4. **Geometry constraint for N=3 bins** — three 0.28m bins at ±0.40m X offsets behind the
   Franka, demonstrating that all three fit inside 0.85m reach (worst-case: 0.60m diagonal).
   Future N=4 sort templates can reference this geometry constraint analysis.

5. **`OmniPBR` + `diffuse_color` materials** — uses `create_material` with correct `material_path`
   + `shader_type` API (not the legacy `material_name`/`color` shorthand used in earlier drafts),
   making this template the reference example for correct material authoring.
