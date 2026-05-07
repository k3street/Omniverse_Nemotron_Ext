# Canonical-task gap analysis — 2026-05-08

For each of the 5 task-spectrum entries (VR-19 → MULTIMODAL-01),
document what tools exist today vs. what's needed to execute the
canonical deterministically. Identifies the work required before
agent-eval makes sense per task.

Companion to:
- `docs/specs/2026-05-08-next-session-autonomous-plan.md` (roadmap)
- `docs/specs/2026-05-08-harness-layers-and-failure-modes.md` (architecture)
- `docs/qa/tasks/{VR-19, SORT-01, CONSTRAINT-01, REORIENT-01, MULTIMODAL-01}.md`

---

## VR-19 — assembly line (cube traverses A → C)

**Status:** ✅ Canonical exists (CP-02), tools sufficient, task spec
v2 written, smoke-tested.

**Tools used:** create_prim, set_attribute, apply_api_schema,
robot_wizard, create_conveyor, create_bin, add_proximity_sensor,
set_physics_scene_config, apply_physics_material, bulk_set_attribute,
setup_pick_place_controller, verify_pickplace_pipeline,
simulate_traversal_check.

**Gap:** None. Hard-instantiate path now triggers correctly when the
prompt strongly matches CP-02 (calibration in
`harness-layers-and-failure-modes.md`).

---

## SORT-01 — color-routed sorting

**Status:** ⚠️ Tool gap. Canonical CP-03 not yet built. Task spec
written (`docs/qa/tasks/SORT-01.md`).

**What's needed for the canonical:**

1. Two cubes with distinct visual + physics materials
   - red cube → red OmniPBR + rubber friction
   - blue cube → blue OmniPBR + rubber friction
2. Two bins with corresponding visual material tinting
3. ONE robot that routes by color (red → red bin, blue → blue bin)

**Existing tools that cover the build:**
- `create_material(material_path, shader_type, diffuse_color, ...)`
  — creates OmniPBR materials with arbitrary RGB
- `assign_material(prim_path, material_path)` — binding
- `apply_physics_material(prim_path, material_name)` — physics friction
- `create_bin`, `create_conveyor`, `robot_wizard` — geometry
- `setup_pick_place_controller` — controller (single destination per
  install)

**The gap — color routing:**

Today's `setup_pick_place_controller` accepts ONE `destination_path`
per install. Two color → bin routes need either:

- **Option A:** Add `color_routing` arg to `setup_pick_place_controller`:
  ```json
  "color_routing": {"red": "/World/RedBin", "blue": "/World/BlueBin"}
  ```
  Controller logic: when picking, sniff the cube's diffuse_color (or a
  semantic_label) and choose destination accordingly. **Substantial
  change to controller closure code (~100 LoC across all target_source
  variants).**

- **Option B:** New `setup_color_routed_pick_place` tool that wraps
  `setup_pick_place_controller` and installs a per-color dispatcher
  that calls `_pause_belt`/`_plan_pick_place` with the matching
  destination. Same code complexity as A, separate name.

- **Option C:** New typed-resolver `resolve_color_routing(prompt) →
  {"red": "/World/RedBin", "blue": "/World/BlueBin"}` plus
  multi-controller-per-robot support. Currently each install on the
  same robot path overrides the previous. Would need per-color
  subscription tags (e.g., `_curobo_pp_sub_<TAG>_red`). Substantial
  too, but generalizes to "routes by any property" not just color.

**Recommended:** Option A. Simplest concrete API for SORT-01;
generalizes to other binary-routing tasks. Resolver (Option C) is a
different layer.

**Effort estimate:** ~3-5 hours of careful work in
`setup_pick_place_controller` handler + tests + CP-03 template build
+ verify_args/simulate_args.

---

## CONSTRAINT-01 — bounded footprint

**Status:** ⚠️ Verifier extension needed. Canonical CP-04 not yet
built. Task spec written.

**What's needed:**
- A pick-place layout that fits within 2×2 m
- Verifier check that catches out-of-bounds prims

**Existing tools:**
- All build tools (create_prim, robot_wizard, etc.) work
- `get_bounding_box(prim_path)` returns world bbox
- `verify_pickplace_pipeline` has reach + bridge + controller checks

**The gap — footprint validation:**

Add `footprint_within_bounds` check to verify_pickplace_pipeline:
- Accept optional `footprint_bounds` arg (`[[xmin, ymin], [xmax, ymax]]`)
- For every authored prim under `/World/`, compute world bbox
- If any prim's bbox extends outside bounds → issue
  `[footprint_bounds] prim X xy=({xmin, ymin}, {xmax, ymax}) exceeds {bound}`

**Effort estimate:** ~1 hour to extend verify handler + add to
schema + smoke test. Then ~2-3 hours to design CP-04 layout that
genuinely fits (probably half-scale CP-01 — smaller table, shorter
conveyor, robot at center, bin compressed).

**Note on motion:** CP-01's 4/4 delivery rate is partly due to the
3 m conveyor giving cubes spacing. Compact layout may reduce
delivery rate; CONSTRAINT-01 spec accepts ≥3/4 to allow this.

---

## REORIENT-01 — pose transformation (flip-station)

**Status:** ⚠️ Hardest. Canonical CP-05 not yet built. Tool gap
moderate (depends on flip-station design). Task spec written.

**What's needed:**
- Cube starts on its side (orientation tilted)
- Intermediate flip-station that rotates cube upright
- Function-gate check on cube's final orientation

**Two implementation paths:**

**Path 1 — Passive (recommended for first canonical):**
- A tilted ramp with appropriate friction
- Cube placed at top of ramp on its side, gravity rolls it down
- At the ramp's bottom, the cube is upright due to ramp geometry
- All buildable with existing tools (`create_prim` + scale + rotate
  + `apply_api_schema(PhysicsCollisionAPI)`)
- No new build tools needed
- Tuning: ramp angle, friction, end-stop angle

**Path 2 — Active:**
- A robotic flipper (mini-arm or rotational joint)
- Joint-controlled actuator that grasps + rotates
- Substantial complexity; would need `add_revolute_joint` or similar
  joint setup + controller integration

**Existing function-gate has limitation:**
`simulate_traversal_check` checks position only. Need to extend with
optional orientation check:
```json
"require_upright": true,
"upright_tolerance_dot": 0.95   // cube up_vector · world_up
```

**Effort estimate:**
- Path 1 (passive): ~4-6 hours including ramp tuning + simulate
  extension + CP-05 template + smoke test.
- Path 2 (active): ~8-12 hours; new joint setup tools needed.

**Recommendation:** ship Path 1 first as CP-05. Path 2 is a future
expansion.

---

## MULTIMODAL-01 — sketch input

**Status:** Deferred. Major infrastructure gap. Task spec written
(placeholder).

**Required infrastructure (none exists):**

1. **Sketch parser** — vision pipeline that detects robot/conveyor/bin
   shapes + reads coordinate annotations. Could leverage existing
   `vision_detect_objects`, `vision_bounding_boxes`, but those are
   generic detection — sketch-specific OCR + shape semantics absent.

2. **Spec generator** — convert parsed shapes → structured JSON spec
   matching the canonical template format (goal, tools_used, code).

3. **Validation tool** — sanity-check the parsed spec.

**Effort estimate:** large project, multi-day. Out of scope until
canonicals 1-4 are solid.

---

## Cross-cutting gaps

These cut across multiple tasks and are worth implementing once:

### A. Material visual integration

Currently `apply_physics_material` is just physics. A composite
`apply_visual_and_physics(prim, color, friction)` that does
`create_material` + `assign_material` + `apply_physics_material` in
one call would cleanly handle SORT-01 + future color-aware tasks.

### B. Verifier extensions

- `footprint_within_bounds` (CONSTRAINT-01)
- `cube_orientation_check` for `simulate_traversal_check` (REORIENT-01)
- `color_routing_check` — verify cube colors match destination tags
  for SORT-01

These could be added incrementally to `verify_pickplace_pipeline` as
new form-checks (the conveyor_active / controller_installed /
cube_source_bridged pattern from Phase 1.1) without changing the
tool's external contract.

### C. Template `params` schema (T2 from harness-layers doc)

For SORT-01-like tasks where the same canonical can serve multiple
parameterizations (red+blue, red+green+blue, 2-class vs 3-class
sorting), the template should declare its parameters and the
hard-instantiate path should substitute them before sandbox-exec.

---

## Recommended sequence (in order of leverage)

1. ✅ **Add cross-cutting B.1** (`footprint_within_bounds` to verify) —
   commit `b93baa4`. CONSTRAINT-01 enabled.
2. ✅ **Build CP-04 (compact 2×2 m pick-place)** — commit `d0915fe`.
   Form-gate verified; visual delivery test pending user.
3. ✅ **Add SORT-01 controller extension (`color_routing` arg) +
   build CP-03** — commits `46fafe1` (cuRobo), `81459a5` (spline),
   CP-03 in `46fafe1`. cuRobo + spline target_sources support color
   routing.
4. ✅ **Extend `simulate_traversal_check` with orientation check** —
   commit `2196d4f`. `require_upright` + `upright_tolerance_dot` args.
5. ✅ **Build CP-05 (passive flip-station)** — commit `9742374`.
   Form-gate verified; function-gate orientation test requires user
   visual + physics tuning.
6. **MULTIMODAL-01 deferred indefinitely.**

All five sequence items complete. Status: all four roadmap canonicals
have build + form-gate coverage. Agent-eval performance is the next
frontier — depends on model + harness work (complexity routing,
payload mitigation, hard-instantiate ranking quality, and the
remaining cross-cutting concerns C below).

## Cross-cutting status

- A. **Composite visual + physics material tool** — NOT IMPLEMENTED.
  Skipped on review: explicit `create_material` + `assign_material` +
  `apply_physics_material` calls in templates make the dependencies
  visible to agents reading the canonical patterns. A composite would
  hide the dependency surface. Reconsider if SORT-01 + future
  color-aware tasks accumulate enough boilerplate to warrant it.

- B. **Verifier extensions** — DONE for footprint_bounds (CONSTRAINT-01)
  and orientation (REORIENT-01). The pattern (incremental form-checks
  on `verify_pickplace_pipeline` + flag-based opt-in args on
  `simulate_traversal_check`) is now established and can be applied
  to future task types without a tool-redesign.

- C. **Template `params` schema (T2)** — NOT IMPLEMENTED. Template
  format additions to support parameter substitution before
  hard-instantiate sandbox-exec. Would let CP-N templates serve a
  family of variants (e.g., CP-01 with N-cubes parameter) without
  duplicating templates per variant. Future work — bigger
  architectural change.
