# Role-Based Schema Migration — Round 6 Scale Report

**Date:** 2026-05-15
**Agent:** Sonnet 4.6 (Round 6)
**Cohort:** CP-29 through CP-38
**Reference:** `docs/research/2026-05-15-round5-migration-scale.md` (Round 5)

---

## §1 Cohort Survey

| Template | Classify | Reason |
|----------|----------|--------|
| CP-29 | MIGRATE | 1-cube precision-experiment (bias compensation), build-verified, single_bin(fixture) |
| CP-30 | MIGRATE | 4-cube 2×2 pallet with per-cube drop_targets, function-gate ✓ |
| CP-31 | MIGRATE | 3-cube vertical stack destack (tabletop), function-gate ✓ |
| CP-32 | MIGRATE | 2-color sort (overhead camera), function-gate ✓ |
| CP-33 | MIGRATE | 2-color sort (side-angle camera), function-gate ✓ |
| CP-34 | MIGRATE | 3-color sort (red/green/blue), function-gate ✓ |
| CP-35 | SKIP | `draft` status (wilson_lower=0.0137) — 8-cube color_specs loop, unstable |
| CP-36 | MIGRATE | 4-cube 2-tier shelf with z-varying drop_targets, function-gate ✓ |
| CP-37 | SKIP | `draft` status (wilson_lower=0.1884) — 4-cube + pillar obstacle, unstable |
| CP-38 | MIGRATE | 12-cube high-volume single bin (≤12 limit boundary), function-gate ✓ |

**MIGRATE: 8, SKIP: 2**

---

## §2 Per-Migrated Template Shape Summary

### CP-29 — Precision-experiment bias compensation (1 cube)

- **pattern_hint:** `pick_place`
- **destination_kind:** `fixture` — TargetZone is a 10×10cm precision marker, not a generic bin
- **structural_tags:** `isaac:topology.precision_benchmark`, `isaac:experiment.bias_compensation` (new sub-tag)
- **roles:** `primary_robot`, `input_conveyor`, `primary_destination` (fixture), `pick_sensor`, `workpieces` (1)
- **`primary_destination.drop_target`:** Stored on destination role (single-cube, target is destination property)
- **File:** `workspace/templates/CP-29.json`

### CP-30 — Generous-margin palletizer (4 cubes, 2×2 grid)

- **pattern_hint:** `pick_place`
- **destination_kind:** `single_bin` — 50×50cm pallet with large margin; modeled as flat Cube with CollisionAPI
- **structural_tags:** `isaac:destination.generous_margin_pallet` (new sub-tag)
- **roles:** Standard 5-role structure; `primary_destination` is a Cube (not `create_bin`)
- **`workpieces[N].drop_target`:** Per-cube 2×2 grid targets at 0.16m spacing
- **File:** `workspace/templates/CP-30.json`

### CP-31 — Vertical-stack destack (3 cubes, tabletop)

- **pattern_hint:** `pick_place`
- **destination_kind:** `single_bin`
- **structural_tags:** `isaac:topology.tabletop_rearrangement`, `isaac:feed.vertical_stack` (new sub-tag)
- **Key:** `workpieces` ordered bottom-to-top ([0]=z=0.835, [1]=z=0.885, [2]=z=0.935); `source_paths` in code_template uses **reverse index** `[workpieces[2], workpieces[1], workpieces[0]]` to pick top-first without destabilizing pile
- **`uses_conveyor_transport: false`** — `work_surface` is stationary (velocity=0.001)
- **File:** `workspace/templates/CP-31.json`

### CP-32 — 2-color vision sorter (overhead camera)

- **pattern_hint:** `sort`
- **destination_kind:** `n_bins_routed`; `routing_axis: "color"`
- **structural_tags:** `isaac:routing.color_sort`, `isaac:vision.overhead_camera` (new sub-tag)
- **roles:** `red_destination`, `blue_destination` — separate named bin roles instead of generic primary_destination
- **`routing.color_routing`:** Dict field stored on a `routing` role_defaults entry: `{"red": "/World/RedBin", "blue": "/World/BlueBin"}` — uses `{{routing.color_routing}}` which formats via `_format_for_code` as a valid Python dict literal
- **workpieces:** Each has `material_path` and `label` sub-fields for `assign_material` + `set_semantic_label` calls
- **File:** `workspace/templates/CP-32.json`

### CP-33 — 2-color vision sorter (side-angle camera)

- **pattern_hint:** `sort`; same schema shape as CP-32
- **structural_tags:** `isaac:routing.color_sort`, `isaac:vision.side_angle_camera` (new sub-tag; vs overhead)
- **Differences from CP-32:** Camera path `/World/Cam` at `[0, 1.5, 1.5]` (not overhead); Cube positions x=[-0.6, 0.0] (vs [-1.0, -0.6])
- Camera creation is hardcoded in code_template (non-parameterized) — position not parameterized since this is a verified-exact setup
- **File:** `workspace/templates/CP-33.json`

### CP-34 — 3-color vision sorter (red/green/blue)

- **pattern_hint:** `sort`; extends CP-33 to 3 colors
- **destination_kind:** `n_bins_routed`; `routing_axis: "color"`
- **structural_tags:** `isaac:routing.color_sort`, `isaac:vision.side_angle_camera`
- **roles:** `red_destination`, `blue_destination`, `green_destination`; `workpieces` (3)
- **Workpiece ordering:** `workpieces[0]=Cube_red`, `workpieces[1]=Cube_blue`, `workpieces[2]=Cube_green` — matches legacy `bulk_set_attribute` prim_paths order [red, blue, green]
- **`source_paths` in template:** `[workpieces[0], workpieces[2], workpieces[1]]` = [red, green, blue] — matches legacy `source_paths=[Cube_red, Cube_green, Cube_blue]`
- **`routing.color_routing`:** `{"red": "/World/RedBin", "green": "/World/GreenBin", "blue": "/World/BlueBin"}`
- **File:** `workspace/templates/CP-34.json`

### CP-36 — Two-tier shelf (4 cubes, z-varying drop_targets)

- **pattern_hint:** `pick_place`
- **destination_kind:** `shelf` — first use of `shelf` destination kind in cohort
- **structural_tags:** `isaac:destination.two_tier_shelf` (new sub-tag)
- **roles:** `primary_robot`, `input_conveyor`, `shelf_bottom` (fixture), `shelf_top` (fixture), `pick_sensor`, `workpieces` (4)
- **`workpieces[N].drop_target`:** Cubes 1-2 go to z=0.825 (bottom tier), Cubes 3-4 go to z=1.000 (top tier). This is the first template where `drop_target.z` varies across workpieces — all prior examples had uniform z
- **File:** `workspace/templates/CP-36.json`

### CP-38 — High-volume single bin (12 cubes)

- **pattern_hint:** `pick_place`
- **destination_kind:** `single_bin` — large 50×50×20cm bin
- **structural_tags:** `isaac:throughput.high_volume` (new sub-tag)
- **workpieces:** 12 items — at the `≤12` boundary. Expanded inline (no loop) in code_template
- **Float precision note:** Legacy code uses `x = -2.4 + i * 0.30` which produces non-round floats (e.g. -1.7999999999999998). role_defaults stores these exact IEEE-754 values — JSON preserves them. This was caught by equivalence test on first attempt; fixed by using `repr(x)` values.
- **File:** `workspace/templates/CP-38.json`

---

## §3 Equivalence Test Results

Test: `tests/test_role_template_equivalence.py`

| Template | Legacy calls | Role calls | Status |
|----------|-------------|-----------|--------|
| CP-29 | 23 | 23 | PASS |
| CP-30 | 34 | 34 | PASS |
| CP-31 | 30 | 30 | PASS |
| CP-32 | 40 | 40 | PASS |
| CP-33 | 39 | 39 | PASS |
| CP-34 | 51 | 51 | PASS |
| CP-36 | 34 | 34 | PASS |
| CP-38 | 86 | 86 | PASS (after float fix) |

Full suite: **31/31 equivalence tests pass** (was 23/23 before Round 6).

Production dispatch test: **7/7 pass** (`tests/test_role_based_code_dispatch.py`).

```
python -m pytest tests/test_role_template_equivalence.py tests/test_role_based_code_dispatch.py -x --tb=short
38 passed in 0.16s
```

---

## §4 Reverts and Failures

**CP-38 — float precision mismatch (fixed, not reverted).**
First attempt failed at index 20: legacy `position=[-1.7999999999999998, ...]` (Python loop arithmetic `x = -2.4 + i * 0.30`) vs role_defaults `[-1.8, ...]` (manually rounded). Fixed by storing exact float repr values in role_defaults. This is a pattern to watch on any template using computed float positions.

**No templates reverted.**

---

## §5 New Patterns Observed

### `routing.color_routing` dict pattern for color sorters

CP-32/33/34 introduce a `routing` role_defaults entry that holds the `color_routing` dict. This is distinct from the `n_bins_routed` pattern in CP-18 (which uses routing_key on each destination). The color_routing dict encodes a complete label→path mapping and is substituted as a single dict literal via `{{routing.color_routing}}`. Cleaner than per-destination routing_key for compact 2-3 color sorters.

### Named bin roles for color sorters (`red_destination`, `blue_destination`)

CP-32/33/34 use semantically named destination roles rather than generic `primary_destination`. This makes the template intent clearer and enables variant generation (swap bin positions, change color mapping). Pattern: when destination roles are qualitatively different (not just positionally different), use distinct named roles.

### `shelf_bottom` / `shelf_top` role naming for multi-tier destinations

CP-36 introduces `shelf_bottom` and `shelf_top` as separate fixture roles. Both have `destination_kind: "shelf"`. This enables per-tier constraints (e.g., only place heavy items on bottom tier) in future LayoutSpec ratification.

### z-varying `drop_target` in workpieces

CP-36 is the first template where `workpieces[N].drop_target.z` varies by cube identity (bottom tier: z=0.825, top tier: z=1.000). Prior templates had uniform drop_target z across all cubes. The schema supports this naturally via per-entry sub-fields.

### `isaac:experiment.*` structural_tag namespace

CP-29 introduces `isaac:experiment.bias_compensation` — a new sub-namespace for templates that are controlled experiments rather than production scenes. This could grow into `isaac:experiment.precision_benchmark`, `isaac:experiment.controller_shootout`, etc.

### Float-exact positions required for computed-loop templates

CP-38 demonstrates that when legacy code uses iterative float arithmetic (`x = -2.4 + i * 0.30`), role_defaults must store the exact IEEE-754 float values (not rounded decimals) to pass equivalence. Detection: if legacy code has a `for i in range(N): x = start + i * step` pattern with non-integer floats, always compute exact values via Python and store them in role_defaults.

---

## §6 Schema Gaps Surfaced

1. **`isaac:experiment.*` namespace not in STRUCTURAL_TAG_PATTERN** — `STRUCTURAL_TAG_PATTERN` already allows arbitrary namespaces matching `^(isaac|cad|user):[a-z0-9_]+(\.[a-z0-9_]+)*$`, so `isaac:experiment.bias_compensation` is valid. No schema change needed, but this namespace is new.

2. **`routing_axis: "color"` vs `routing_axis: "semantic_class"`** — CP-32/33/34 sort by color label (set via `set_semantic_label(..., semantic_type="color")`). CP-18 sorts by semantic class (good/defective) via a vision classifier gate. Both could be `routing_axis: "color"` but the dispatch mechanism differs: color sorters use hardcoded `color_routing` dict, class sorters use a runtime classifier. No schema change needed today, but a future `routing_mechanism: "static"` vs `routing_mechanism: "classifier"` field would clarify.

3. **`shelf` destination_kind now used (CP-36)** — Previously only `single_bin`, `n_bins_routed`, `fixture` appeared in migrated templates. `shelf` enters the live set with CP-36.

4. **`routing` role_defaults entry** — The `routing` key in role_defaults is not enumerated anywhere in the schema. It's used as a named dict holder for `color_routing`. Low risk (no validation of role_defaults keys), but documents a new convention: utility roles (not geometric objects) can live in role_defaults as named dicts.

---

## §7 Recommendation for Round 7 Cohort

**Candidate cohort: CP-39..48**

Pre-survey notes (from Round 6 observations):
- CP-40 appears in failure-mode docs — check verified_status before migrating
- CP-46 is `stable_fail` — SKIP
- CP-35 and CP-37 remain `draft` — SKIP

Priority patterns to look for:
- Multi-robot scenes (would introduce new roles)
- AMR/mobile robot patterns (different from fixed-base arm)
- ROS2-bridge templates (may not fit pick_place pattern_hint)

The `routing.color_routing` and named destination roles (§5) established in Round 6 should be reused for any additional color sorter variants in CP-39..48.
