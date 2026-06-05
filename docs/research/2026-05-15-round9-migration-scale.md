# Round 9 — CP-NEW-* Role-Based Schema Migration

**Date:** 2026-05-15
**Agent:** Sonnet 4.6 (Round 9)
**Recipe:** docs/research/2026-05-15-role-migration-pilot.md

---

## Result Summary

**Migrated:** 8 templates
**Skipped:** 14 templates
**Equivalence:** 66/66 pass (prior 58 + 8 new)
**R1_MISSING_INTENT delta:** 22 → 14 CP-NEW templates (-8)

---

## Migrated Templates

| Template | pattern_hint | destination_kind | Notes |
|---|---|---|---|
| CP-NEW-inspect-reject | sort | n_bins_routed | routing_axis=semantic_class; 5 cubes; float-precision fix in role_defaults |
| CP-NEW-dr-curriculum | pick_place | single_bin | 1 cube; DR Stage 2; belt_path=None |
| CP-NEW-multi-cam-triangulation | pick_place | single_bin | 1 cube; 3 cameras (not in roles — static); belt_path=None |
| CP-NEW-cad-revision-drift | pick_place | fixture | 1 cube; conveyor; arena_variant in code |
| CP-NEW-controller-shootout-cp | pick_place | single_bin | 4 cubes; conveyor; benchmark structural_tag |
| CP-NEW-y-merge-singulation | pick_place | single_bin | 6 cubes (L1-3 + R1-3); 3-conveyor scene; legacy bulk_set_attribute has unevaluated f-string — reproduced exactly |
| CP-NEW-3station-oee | pick_place | n_bins_routed | 3 robots; 9 cubes; 3 sensors; 3 bins; float-precision fix for positions |
| CP-NEW-plc-fixture | pick_place | fixture | 1 cube; modbus bridge + pick_place; BUILD_OK only |

---

## Skipped Templates

### stable_fail (5)
- CP-NEW-peg-in-hole-single — PhysX explosion (200km/s)
- CP-NEW-brick-stacking — PhysX explosion (200km/s)
- CP-NEW-tactile-insertion — PhysX explosion (200km/s)
- CP-NEW-drawer-open — pick_place cannot drive PrismaticJoint
- CP-NEW-cross-belt-sorter — no robot; cubes fall through belt junctions

### Nucleus-blocked (2) — already on skip list
- CP-NEW-g1-bimanual-tabletop — G1 SimReady asset missing
- CP-NEW-operator-ergonomics — OperatorAvatar SimReady missing

### Parked (1) — already on skip list
- CP-NEW-brick-stacking — also stable_fail

### BUILD_OK but novel pattern / no simulate_args (6)
- CP-NEW-defect-sdg — SDG pipeline only; no simulate_args; no pick_place role fit
- CP-NEW-rl-clone-env — clone_envs + launch_training; no simulate_args; novel RL pattern
- CP-NEW-amr-pickup-handoff — gate stable_fail (controller plans but cube never picked)
- CP-NEW-opcua-12conveyors — bridge plumbing only; no simulate_args; no robot role
- CP-NEW-plc-conveyor — bridge plumbing only; no simulate_args; no robot role
- CP-NEW-multi-amr-corridor — fleet AMR nav; no simulate_args; novel navigate pattern

### BUILD_OK partial (1)
- CP-NEW-sim2real-gap — needs real rosbag asset; no simulate_args; novel rosbag-replay pattern

---

## Equivalence Notes

Three first-pass failures — all fixed before final commit:

1. **Float precision (inspect-reject, 3station-oee):** Legacy positions computed via
   `x + i*step` accumulate IEEE-754 rounding. role_defaults must store the exact
   float (e.g. `-0.3999999999999999` not `-0.4`) to match `_normalize(repr())`.

2. **Unevaluated f-string (y-merge bulk_set_attribute):** Legacy code has a bug:
   `bulk_set_attribute(prim_paths=["/World/Cube_L{i+1}"], ...)` inside a list,
   not an f-string — the literal string `"/World/Cube_L{i+1}"` is captured.
   code_template must reproduce this verbatim to pass equivalence.

---

## Lint Status (migrated templates only)

All 8 migrated templates: 0 ERROR. Warnings:
- `T1_MISSING_SETTLE_STATE` on all 8 — expected (settle_state is a follow-on task)
- `T1_MC_MISSING` on CP-NEW-plc-fixture — needs `motion_controllers` field (has setup_pick_place_controller)

---

## Files Modified

- `workspace/templates/CP-NEW-inspect-reject.json`
- `workspace/templates/CP-NEW-dr-curriculum.json`
- `workspace/templates/CP-NEW-multi-cam-triangulation.json`
- `workspace/templates/CP-NEW-cad-revision-drift.json`
- `workspace/templates/CP-NEW-controller-shootout-cp.json`
- `workspace/templates/CP-NEW-y-merge-singulation.json`
- `workspace/templates/CP-NEW-3station-oee.json`
- `workspace/templates/CP-NEW-plc-fixture.json`
- `tests/test_role_template_equivalence.py` — added 8 IDs to parametrize list (58→66)
