# Role-Based Schema Migration — Round 5 Scale Report

**Date:** 2026-05-15
**Agent:** Sonnet 4.6 (Round 5)
**Cohort:** CP-19 through CP-28
**Reference:** `docs/research/2026-05-15-round4-migration-scale.md` (Round 4)

---

## §1 Cohort Survey

| Template | Classify | Reason |
|----------|----------|--------|
| CP-19 | MIGRATE | 6-cube twin-pallet feeder, verified ✓, drop_targets to two pallets |
| CP-20 | SKIP | 18 cubes (>12 loop limit) — loop_substitution needed first |
| CP-21 | MIGRATE | 4-cube gravity-feed (z=1.0 spawn), verified ✓, single_bin |
| CP-22 | SKIP | `draft` status (wilson_lower=0.43) — not stable, demoted |
| CP-23 | MIGRATE | 4-cube mirror-orientation (robot facing -Y), verified ✓ |
| CP-24 | MIGRATE | 4-cube narrow-slot insertion, explicit drop_targets, verified ✓ |
| CP-25 | SKIP | 16 cubes (>12 loop limit) — loop_substitution needed first |
| CP-26 | MIGRATE | 4-cube belt-to-belt handoff (Conv1→Conv2), verified ✓ |
| CP-27 | MIGRATE | 4-cube tabletop rearrangement (stationary WorkSurface), verified ✓ |
| CP-28 | MIGRATE | 1-cube precision benchmark, build-verified 2026-05-08 |

**MIGRATE: 7, SKIP: 3**

---

## §2 Per-Migrated Template Shape Summary

### CP-19 — Twin-pallet feeder (6 cubes)

- **pattern_hint:** `pick_place`
- **destination_kind:** `n_bins_routed` — destination_path=PalletA (fallthrough), but drop_targets explicitly map cubes 1-3 to PalletA and 4-6 to PalletB
- **structural_tags:** `isaac:topology.twin_pallet` (new sub-tag for multi-destination unrouted split)
- **roles:** 6 roles — `primary_robot`, `input_conveyor`, `pallet_a`, `pallet_b`, `workpieces` (6), `pick_sensor`
- **workpieces[N].drop_target:** Each of the 6 cubes carries its explicit drop_target coordinate. Cubes 1-3 target PalletA grid (x≈-0.42..-0.28), cubes 4-6 target PalletB grid (x≈0.28..0.42)
- **verify_args_template:** 2 stages (pallet_a + pallet_b), matching the legacy `verify_args` structure
- **File:** `workspace/templates/CP-19.json`

### CP-21 — Gravity-feed station (4 cubes, z=1.0 spawn)

- **pattern_hint:** `pick_place`
- **destination_kind:** `single_bin`
- **structural_tags:** `isaac:feed.gravity_drop` (new sub-tag for above-belt spawn → fall → pick pattern)
- **roles:** Standard 5-role structure; `workpieces` positions are z=1.0 (not 0.835 — the cubes start 17cm above belt)
- **Key:** role_defaults correctly captures z=1.0 in workpiece positions, matching settle_state
- **File:** `workspace/templates/CP-21.json`

### CP-23 — Mirror-orientation cell (robot facing -Y)

- **pattern_hint:** `pick_place`
- **destination_kind:** `single_bin`
- **structural_tags:** `isaac:topology.mirror_orientation` (new sub-tag)
- **Key design:** `primary_robot.orientation` is `[0.7071068, 0, 0, -0.7071068]` (negative w vs CP-01's positive), `input_conveyor.position` at y=-0.4 (south), `primary_destination.position` at y=+0.4 (north), `pick_sensor.position` at y=-0.4. This captures the full spatial inversion.
- **File:** `workspace/templates/CP-23.json`

### CP-24 — Narrow-slot insertion (4 cubes, fixture destination)

- **pattern_hint:** `pick_place`
- **destination_kind:** `fixture` — narrow-slot bin (0.30×0.06×0.10m) is a fixture, not a generic bin
- **structural_tags:** `isaac:destination.narrow_slot` (new sub-tag)
- **workpieces[N].drop_target:** Per-cube explicit targets along slot X axis at y=-0.40, spaced 0.06m
- **Legacy code uses `create_bin` for `/World/Slot`** — code_template preserves this
- **File:** `workspace/templates/CP-24.json`

### CP-26 — Belt-to-belt handoff

- **pattern_hint:** `pick_place`
- **destination_kind:** `fixture` — transfer target is Conv2 (a moving belt), not a static bin. `fixture` is closest VALID_DESTINATION_KINDS value
- **structural_tags:** `isaac:topology.belt_to_belt` (new sub-tag)
- **roles:** 5 key roles — `primary_robot`, `input_conveyor` (Conv1), `transfer_conveyor` (Conv2), `exit_bin` (Bin downstream of Conv2), `workpieces`. Transfer conveyor carries `drop_target` as a sub-field
- **`n_handoffs: 1`** — reflects robot transferring cubes from one belt to another
- **File:** `workspace/templates/CP-26.json`

### CP-27 — Tabletop rearrangement (no belt transport)

- **pattern_hint:** `pick_place`
- **destination_kind:** `single_bin` — pallet is the destination, structured as a Cube prim
- **`uses_conveyor_transport: false`** — WorkSurface has surface_velocity=0.001 (near-zero, not a real transport belt). First template in cohort with this flag false.
- **structural_tags:** `isaac:topology.tabletop_rearrangement` (new sub-tag — no active belt)
- **role:** `work_surface` (not `input_conveyor`) — still constrained to `["conveyor"]` since create_conveyor is the tool used, but named to reflect semantic intent
- **`primary_destination` uses create_prim not create_bin** — pallet is a flat Cube with PhysicsCollisionAPI, not a walls bin. code_template preserved this exactly.
- **workpieces positions:** 2D positions on table (x,y vary, not just x) — stored correctly in role_defaults
- **File:** `workspace/templates/CP-27.json`

### CP-28 — Single-cube precision benchmark

- **pattern_hint:** `pick_place`
- **destination_kind:** `fixture` — TargetZone is a 10×10cm marker flat (not a bin)
- **structural_tags:** `isaac:topology.precision_benchmark` (new sub-tag)
- **workpieces:** Single-item list (min=1, max=1)
- **`primary_destination.drop_target`** stored as sub-field since it's a direct scalar target (not per-cube keyed)
- **File:** `workspace/templates/CP-28.json`

---

## §3 Equivalence Test Results

Test: `tests/test_role_template_equivalence.py`

| Template | Legacy calls | Role calls | Status |
|----------|-------------|-----------|--------|
| CP-19 | — | — | PASS |
| CP-21 | — | — | PASS |
| CP-23 | — | — | PASS |
| CP-24 | — | — | PASS |
| CP-26 | — | — | PASS |
| CP-27 | — | — | PASS |
| CP-28 | — | — | PASS |

Full suite: **23/23 pass** (was 16/16 before Round 5).

```
python -m pytest tests/test_role_template_equivalence.py --tb=short
23 passed in 0.12s
```

Production dispatch test: **7/7 pass** (`tests/test_role_based_code_dispatch.py`).

---

## §4 Reverts and Failures

**None.** All 7 migrations passed equivalence on first attempt.

---

## §5 New Patterns Observed

### `uses_conveyor_transport: false` — first occurrence

CP-27 (tabletop rearrangement) uses a stationary WorkSurface (surface_velocity=0.001 ≈ 0). This is the first template where `uses_conveyor_transport: false` makes sense. The `work_surface` role is still constrained to `["conveyor"]` because the tool used is `create_conveyor`, but the semantic is static-surface pickup. This pattern will recur in future tabletop and assembly scenarios.

### Multi-XY cube positions in workpieces

CP-27's cubes have 2D scatter positions on the table surface (`(x=0.2, y=0.3)`, `(x=0.4, y=0.3)`, etc.) rather than varying-X-only positions on a belt. The `workpieces[N].position` field already supports this — no schema extension needed.

### Belt-to-belt adds `n_handoffs` count to structural_features

CP-26 introduced `n_handoffs: 1` to indicate a robot-mediated inter-belt transfer. Prior templates all have `n_handoffs: 0`. This is a natural structural feature but was added without schema validation — VALID values for n_handoffs not enumerated anywhere. Low risk since it's a numeric field.

### `drop_target` as destination sub-field (not per-cube)

CP-28 stores `primary_destination.drop_target` on the destination role itself (since there's only 1 cube, the target is logically a destination property). CP-26 stores `transfer_conveyor.drop_target` similarly. This is distinct from the `workpieces[N].drop_target` pattern used in CP-24/CP-27 where each cube has a different target. Both patterns are valid depending on cardinality.

### Twin-pallet as `n_bins_routed` without routing_axis

CP-19 uses `n_bins_routed` but has no `routing_axis` — routing is by explicit pre-computed drop_targets (spatial assignment), not by real-time sensing of a workpiece property. This is a different dispatch mechanism than CP-18 (which routes by semantic_class from a classifier). The schema doesn't currently distinguish these two variants of `n_bins_routed`. A future `routing_axis: "explicit"` could clarify this.

---

## §6 Schema Gaps Surfaced

1. **No validation of `n_handoffs` field** — added to CP-26 without a VALID enum or range. Numeric field, low risk.

2. **`n_bins_routed` conflates two dispatch mechanisms:**
   - Pre-computed spatial assignment (CP-19: each cube has a fixed drop_target)
   - Runtime-classified routing (CP-18: classifier output decides accept/reject bin)
   A `routing_axis: "explicit"` value in VALID_ROUTING_AXES would disambiguate. Current workaround: CP-19 simply omits `routing_axis` (correctly — there is no runtime axis).

3. **New structural_tags not in any registry:** The following tags were added in this round and are not enumerated anywhere:
   - `isaac:topology.twin_pallet`
   - `isaac:feed.gravity_drop`
   - `isaac:topology.mirror_orientation`
   - `isaac:destination.narrow_slot`
   - `isaac:topology.belt_to_belt`
   - `isaac:topology.tabletop_rearrangement`
   - `isaac:topology.precision_benchmark`
   The STRUCTURAL_TAG_PATTERN regex validates format (namespace:segment.subsegment) but not vocabulary. A tag vocabulary registry would enable completeness checks.

4. **`fixture` destination_kind needs clarification** — used for 3 different situations: narrow-slot bin (CP-24), target zone marker (CP-28), and a moving belt as drop destination (CP-26). The semantic stretch for CP-26 (where the "fixture" is actually a running conveyor) is imprecise. A `conveyor_surface` destination_kind variant may be warranted.

---

## §7 Recommendation for Round 6

**Target cohort: CP-29..38** (next 10 templates after the cohort boundary).

Before starting Round 6:
1. **Survey CP-29..38 loop sizes** — run the enumerate-loop counter script from this round. Skip any with >12 items.
2. **Check pre-migration status** — CP-12..17 were silently pre-migrated; check CP-29..38 the same way.
3. **Consider schema extensions before migrating more `n_bins_routed` templates** — if Round 6 contains more multi-destination templates, add `routing_axis: "explicit"` to VALID_ROUTING_AXES first.

**Estimated remaining R1_MISSING_INTENT count after Round 5:** 87 (was 94, dropped by 7).

**Priority actions before Round 7:**
- Add `loop_substitution` pattern to `substitute_role_placeholders` to unlock CP-20 (18 cubes) and CP-25 (16 cubes) without unwieldy unrolled code_templates.
- Revisit CP-22 when its `verified_status` upgrades from `draft` to `build-spec`.

---

## Files Modified

- `workspace/templates/CP-19.json` — added intent, roles, role_defaults, code_template, verify_args_template, simulate_args_template
- `workspace/templates/CP-21.json` — same additions
- `workspace/templates/CP-23.json` — same additions
- `workspace/templates/CP-24.json` — same additions
- `workspace/templates/CP-26.json` — same additions
- `workspace/templates/CP-27.json` — same additions
- `workspace/templates/CP-28.json` — same additions
- `tests/test_role_template_equivalence.py:70` — added CP-19, CP-21, CP-23, CP-24, CP-26, CP-27, CP-28 to parametrize list
