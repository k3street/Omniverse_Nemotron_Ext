# A1 — First F.0 Canonical Draft Decision Document
Date: 2026-05-16

## §1 Candidate Selection

**Backlog ID:** `yrkesroll-palletizer-layer-stack-001`  
**Title:** Palletizer — 2×3 layer stack on pallet (ONET SOC 51-2091)

**Why this candidate:**
- Priority tier 1, no blockers, medium complexity — safest first draft
- `pick_place` pattern is the best-tested pathway through the cuRobo handler
- UR10 provides meaningful differentiation from the Franka-heavy existing library
- No existing `CP-NEW-palletizer-*` template — zero overlap risk
- Backlog top-3 instruction says avoid `inspector-reject-divert` (overlaps CP-NEW-inspect-reject) and `multi-cam-triangulation` (non-pick_place, different plumbing). Palletizer is the cleanest next pick.

## §2 Template Summary

| Field | Value |
|---|---|
| task_id | `CP-NEW-palletizer-layer-stack` |
| File | `workspace/templates/CP-NEW-palletizer-layer-stack.json` |
| pattern_hint | `pick_place` |
| Roles count | 4 (primary_robot, input_conveyor, primary_destination, workpieces) |
| tools_used count | 11 |
| Code lines | ~80 lines (concrete code block) |
| Code template lines | ~80 lines (parameterised) |
| Motion controller | `curobo` (untested — no Kit RPC available for function-gate) |

**Pattern:** Single-station UR10 picks 6 uniform 0.10m boxes from a 3m infeed conveyor and places them in a 2×3 grid on a 0.70×0.50m pallet. Grid positions pre-computed via `compute_stack_placement(pattern='grid_2x3', ...)` and baked into `drop_targets` dict. Proximity sensor at pick point triggers each cycle.

**Key design decisions:**
- UR10 (reach ~1.3m) chosen to keep all 6 grid slots within reach without robot repositioning
- Belt velocity reduced to 0.15 m/s to give reliable pick window timing
- Single-layer only (all drop z = 0.875m) — two-layer variant deferred as a future extension
- Grid spacing 0.12m between 0.10m boxes = 0.02m clearance — tight but within PhysX broadphase tolerance

## §3 Form-Gate Result

```
workspace/templates/CP-NEW-palletizer-layer-stack.json: OK
1 templates scanned: 1 OK, 0 ERROR, 0 WARN, 0 INFO
```

**Result: PASS — 0 ERROR, 0 WARN.**

All schema requirements met:
- Core-6 fields present and non-empty
- T1 fields present: `verify_args`, `simulate_args`, `diagnose_args`, `verified_status`
- `verify_args.stages[0]` has `robot_path`, `pick_path`, `place_path`
- `simulate_args` has `target_path`, `duration_s`, `cube_paths` (multi-cube variant)
- `intent.pattern_hint` = `"pick_place"` (valid enum value)
- `structural_tags` all match `isaac:segment.subsegment` pattern
- `roles` / `role_defaults` / `code_template` all present (trio satisfied)
- Every role in `roles` has a matching entry in `role_defaults`
- `motion_controllers` declared with valid structure
- No deprecated fields present

## §4 Backlog Status Update

`config/canonical_backlog.yaml` updated:
- `yrkesroll-palletizer-layer-stack-001`: `status: queued` → `status: drafted`
- Added `template_file` and `drafted_date` fields for traceability

## §5 What's Pending

1. **Function-gate (Kit RPC):** Requires a live Isaac Sim + Kit session to execute the code block and confirm UR10 picks all 6 boxes and delivers to pallet grid slots within `xy_tolerance=0.08`. Must be run sequentially (no concurrent direct_eval).

2. **UR10 asset verification:** `robot_wizard(robot_name="ur10", ...)` — confirm asset key string matches the registered UR10 USD. May need capitalisation adjustment (`"UR10"`).

3. **Grid slot clearance tuning:** 0.02m clearance between 0.10m boxes at 0.12m spacing is tight. If PhysX contact solver reports persistent overlaps at settle, increase to `spacing=0.14` and recompute drop targets.

4. **Human review:** Anton or QC agent should validate the reach geometry calculation (UR10 base at [0,0,0.75], far pallet corner at ~0.76m horizontal distance from base) against actual UR10 URDF kinematics before promoting to `form-gate ✓`.

5. **Two-layer extension:** Once single-layer is function-gated, a `CP-NEW-palletizer-2layer` can raise `drop_z` by 0.10m for Layer 2 boxes using the same drop_targets pattern.
