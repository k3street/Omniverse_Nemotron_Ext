# Role-Based Schema Migration — Round 8b Scale Report

**Date:** 2026-05-15
**Agent:** Sonnet 4.6 (Round 8b)
**Cohort:** CP-74..87 (Tier A verified only)
**Reference:** `docs/research/2026-05-15-round8a-migration-scale.md` (Round 8a)

---

## §1 Survey Table

| Template | Classify | Reason |
|----------|----------|--------|
| CP-74 | SKIP | function-gate ✗ (belt-pause-from-callback bug) |
| CP-75 | MIGRATE | UR10 builtin static-table pickup, fg ✓ |
| CP-76 | SKIP | stable_fail (explicit skip-list) |
| CP-77 | MIGRATE | Franka cuRobo conveyor nested-box packer, fg ✓ |
| CP-78 | MIGRATE | UR10 builtin pedestal pickup, fg ✓ |
| CP-79 | MIGRATE | UR10 builtin pedestal +X+Y, fg ✓ |
| CP-80 | SKIP | function-gate ✗ (belt-pause-from-callback bug, same as CP-74) |
| CP-81 | MIGRATE | UR10 cuRobo 2-cube pedestal, fg ✓ |
| CP-82 | MIGRATE | UR10 cuRobo 2-cube color-routing 2-bin, fg ✓ |
| CP-83 | MIGRATE | UR10 cuRobo 2-cube pedestal (different Y), fg ✓ |
| CP-84 | SKIP | function-gate ✗ (drop precision, z=0.775 short) |
| CP-85 | SKIP | function-gate ✗ (descent issue, smaller bins) |
| CP-86 | MIGRATE | UR10 builtin single-cube + color_routing, fg ✓ |
| CP-87 | SKIP | draft |

**MIGRATE: 8 | SKIP: 6 (2 fg-fail belt-pause, 1 stable_fail, 2 fg-fail precision/descent, 1 draft)**

---

## §2 Migration Details

### UR10 Static-Pedestal Family (CP-75, CP-78, CP-79)

Pattern: UR10 via `create_prim` + `add_reference` + `teleport_prim` (not `import_robot`),
`target_source="builtin"`, no conveyor, single cube. Role structure:
`primary_robot / primary_destination / workpieces[1]`. Pedestal is a hardcoded static
prim in `code_template` (not parameterised — it is scene geometry, not a role).

- CP-75: cube at table level z=0.825. `uses_conveyor_transport=false`.
- CP-78: cube on 15cm pedestal at z=0.975. Same bin position.
- CP-79: cube at +X+Y pedestal z=0.975 (different xy). Bin at +X-Y.

### CP-86 (color-routing single-cube)

Extends CP-78 scene + `set_semantic_label` + `color_routing`. `pattern_hint="sort"`,
`routing_axis="color"`, `destination_kind="single_bin"` (routing resolves to same bin).
`workpieces[0].color_label="red"` sub-field for documentation only (not substituted
into code — `set_semantic_label class_name` is hardcoded string `"red"` in code_template
for equivalence). Verified equivalence holds because both legacy and role code emit same
`set_semantic_label` call.

### CP-81 / CP-83 (UR10 cuRobo 2-cube no conveyor)

`target_source="curobo"`, two pedestals, two cubes, single bin. `workpieces[2]`.
CP-81: cubes at (-0.5, 0.55) and (-0.5, 0.25). CP-83: cubes at (-0.5, 0.4) and (-0.5, 0.0).
No `drop_targets` dict — both cubes go to same destination. Single `primary_destination`.

### CP-82 (UR10 cuRobo 2-cube color-routing 2-bin)

`destination_kind="n_bins_routed"`, `routing_axis="color"`. Added `destinations` list role
(2 bins: Bin_red + Bin_blue). `primary_destination` = Bin_red (matches `destination_path`
in legacy code). `code_template` uses `{{destinations[0].path}}` and `{{destinations[1].path}}`
for `create_bin` calls and `color_routing` dict values.

### CP-77 (Franka cuRobo conveyor nested-box packer)

5 workpieces (Cube_1..4 + Cube_lid). Legacy code uses `for i, x in enumerate(...)` loop
for Cube_1..4 — unrolled to 4 explicit blocks in code_template. Cube_lid uses `scale`
not `size` parameter — preserved verbatim. `drop_targets` dict uses
`{{workpieces[N].path}}: {{workpieces[N].drop_target}}` pattern (same as CP-09..11).
`structural_tags` includes `isaac:topology.nested_box_packer` + `isaac:stack.grid_2x2`.

---

## §3 Equivalence Test Results

Added CP-75, CP-77, CP-78, CP-79, CP-81, CP-82, CP-83, CP-86 to parametrize list (line 70).

| Template | Legacy calls | Role calls | Status |
|---|---|---|---|
| CP-75 | — | — | PASS |
| CP-77 | — | — | PASS |
| CP-78 | — | — | PASS |
| CP-79 | — | — | PASS |
| CP-81 | — | — | PASS |
| CP-82 | — | — | PASS |
| CP-83 | — | — | PASS |
| CP-86 | — | — | PASS |

Full suite: **58/58 pass** (was 50/50 before Round 8b).

```
python -m pytest tests/test_role_template_equivalence.py -v
58 passed in 0.18s
```

---

## §4 Reverts

**None.** All 8 migrations passed equivalence on first attempt.

---

## §5 Lint State After Round 8b

- `R1_MISSING_INTENT` INFO items: **52** (was 60 before Round 8a; was 52 after Round 8a → 8b adds -8 more)
  - Correction: Round 8a brought it to 60, this round reduced to 52. Delta = -8, matching 8 migrations.
- **0 new ERRORs** introduced.
- **55 WARNs** (unchanged — no new templates with motion-planning tools without mc field).
- **121 INFO** total (down from 137 after Round 8a).
- Summary: `321 templates scanned: 263 OK, 0 ERROR, 55 WARN, 121 INFO`

---

## §6 Schema Notes

1. **`add_reference` + `teleport_prim` UR10 pattern** — CP-75/78/79/86 use `create_prim` +
   `add_reference` (not `import_robot`). This is kept verbatim in code_template; the UR10 path
   is a hardcoded asset reference path, same as CP-69..72 convention.

2. **Pedestal as hardcoded scene geometry** — Pedestal prims are NOT parameterised as roles.
   They are fixed scene geometry for the specific ablation/test purpose of each template.
   This is correct; pedestals are not substitutable infrastructure roles.

3. **`uses_conveyor_transport=false`** — First use of this value in migrations. Valid for
   static-pickup UR10 family. The schema does not enforce conveyor presence when false.

4. **`destinations` role for 2-bin routing (CP-82)** — Follows the CP-66 pattern
   (`destinations` list role for multi-bin). `primary_destination` is kept as the
   `destination_path` nominal (Bin_red). This preserves the legacy code's `destination_path`
   argument to `setup_pick_place_controller` exactly.

5. **Cube_lid `scale` vs `size`** — CP-77's lid uses `scale=[0.10, 0.10, 0.01]` not `size=`.
   This is preserved in code_template; no role sub-field added for `scale` (it is a fixed
   design parameter of the lid, not a substitutable role attribute).

---

## §7 Round 8c Recommendation

**CP-74..87 range exhausted for Tier A.** Remaining unmigrated in range:
- CP-74, CP-80: belt-pause bug — blocked
- CP-84, CP-85: drop-precision/descent fg-fail — blocked
- CP-76: stable_fail — explicit skip
- CP-87: draft — skip

**Round 8c target: yrkesroll (Y-01..Y-20) or T-series templates.**
- Yrkesroll (Y-01..Y-20): 20 drafted 2026-05-10. Check verified_status for any fg ✓.
- T-series (T-01..T-14): industrial scenario templates, check verified_status.
- Alternative: fix CP-84 drop-precision (cuRobo + planning_obstacles=[BaseCube]) and
  migrate after — but that is function-gate work, not migration work.

**Lint target after Round 8c:** R1_MISSING_INTENT ≤ 44 (if 8 more yrkesroll/T-series migrate).
