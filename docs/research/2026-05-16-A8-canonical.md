# A8 Canonical Decision — 2026-05-16

## Selected candidate

**`industrial-heap-zone-unstack-001`** — industrial category, `reorient` pattern_hint, Tier 2, zero blockers.

## Selection rationale

### Pattern coverage
A1-A7 exhausted `pick_place` (×3), `sort` (×1), `other` (×2), `rl-train` (×1).
Unused pattern_hints entering A8: **reorient**, navigate, insert.

- **navigate**: all queued navigate entries carry `nucleus_only_asset` blocker (Carter USD required).
- **insert**: `CP-NEW-tactile-insertion` already exists in workspace/templates (drafted earlier in the session, not in A1-A7 set but present). Drafting a second insert would duplicate the pattern before the first is function-gated.
- **reorient**: two unblocked queued entries existed — `industrial-heap-zone-unstack-001` (industrial, Tier 2) and `research-maniskill-stack-cube-001` (research, Tier 2). The industrial entry was chosen for category depth (industrial now has a 2nd drafted canonical alongside `CP-NEW-assembly-line-4robot-handoff`).

### What is new in this canonical

| Dimension | This canonical |
|---|---|
| Pattern | `reorient` — first use in A-series |
| Key tool | `create_heap_zone` — not used in any A1-A7 |
| Pick strategy | `bounding_box_height` mode: per-cycle Z-rank of remaining cubes via `get_bounding_box`; no fixed pick pose |
| Robot | Franka (same as several canonicals, but the task is structurally distinct) |
| Topology | Unstructured source (heap) → single conveyor outfeed |
| Motion | cuRobo with `top_down_approach=True`, transit height 1.18 m |

## Template summary

- File: `workspace/templates/CP-NEW-heap-zone-unstack.json`
- LOC (code field): 68 lines
- Roles: 5 (`primary_robot`, `heap_source`, `output_conveyor`, `workpieces`, `arrival_sensor`)
- Tools used: 12
- Form-gate: **1 OK / 0 ERROR**

## Key design decisions

1. **Heap at [0, 0.30, 0.80], conveyor at [0, -0.50, 0.78]**: both within ~0.80 m horizontal from Franka base; no reach-boundary violations.
2. **settle_ticks=180**: 3 s sim time (60 Hz) before first bounding_box call — ensures pile has settled and cubes are motionless before grasp height query.
3. **xy_tolerance=0.22 in simulate_args**: wider than pick_place canonicals (0.08) because cubes land anywhere on the conveyor belt surface, not a fixed slot.
4. **failure_modes (6 entries)** cover: cube naming index offset, wall-wedge occlusion, extreme pile height beyond Franka reach, missing `top_down_approach` kwarg fallback, conveyor drop geometry, overlap_sphere false-positive from zone walls.

## Backlog update

`industrial-heap-zone-unstack-001`: `queued` → `drafted`, `drafted_by: A8`.
