# Sim-Ready Asset Pipeline — Backlog

Ordered by leverage. Top item first.

## 1. Finish line: finalize/promote step — ✅ SHIPPED 2026-08-07
"Approved" means "finished and sim-usable".
- `scripts/promote_asset.py` (CLI + called by hub Approve): ensures physics
  (rigid: make_sim_ready with class material/mass at INGEST already;
  articulated: joints required, materials + volume-split per-link masses at
  promote) → re-audit → stamp → lands in the canonical, self-contained
  library `workspace/asset_library/<asset_id>/` (source copied in,
  references rewritten relative — the folder is portable) → registry
  `file` points at the library copy.
- Approve refuses articulated categories without joints; promotion is part
  of approval and rolls back the registry entry on failure.
- Cleanups done: frying-pan pointer, corrupt aston wrapper, drill dupe
  (slug now collapses underscore runs). All 8 registered assets verified:
  physics present, materials correct, relative refs.

## 2. Automated live verification — ✅ SHIPPED 2026-08-07
`scripts/verify_asset_live.py [<id> ... | --all-unverified]` against a live
Isaac session: rigid drop test (rest-on-ground criterion tolerating
settle-tipping) and per-joint drive tests for articulated assets
(prismatic in meters, revolute in degrees, measured base drift, ground
under floating bases). Evidence written to the registry; categories
auto-flip `*_unverified` → `*_verified` inside schema tolerances and the
library stamp updates. Validated: 7/8 registry assets verified with
measured evidence (bedside lift: commanded 0.15 m, measured 0.15 m,
error 0.0000). Remaining: hub button per asset; headless-Isaac CI mode.

## 3. Articulation authoring at scale — ◐ geometric tier SHIPPED 2026-08-07
`scripts/articulation_draft.py`: symmetry-axis detection (wheel-pair-first,
bbox-center midpoint — centroid means skew off the symmetry plane),
disc/mirror-pair wheel detection with a ground-contact prior (side guards
float, wheels touch the floor), containment collapse of co-axial rings,
and link grouping by same-side bbox containment. Hub draft flow uses it
with naive fallback. Validated on the segmented wheelchair: both drive
wheels found with 20/12 grouped members plus both casters; reviewer edits
4 noise candidates to fixed and applies — 110 bodies, 109 joints, 4
spinning wheels.
Remaining: VLM tier for non-wheel mechanisms (lids, doors, drawers —
geometry alone cannot name them), limit inference, and ONE OPEN PHYSICS
INVESTIGATION — continuous-wheel drive convergence: verify_asset_live now
has the spin-test harness (--file standalone mode, base pinned kinematic,
no ground, articulation self-collision off, wheel joints anchored at the
hub centroid — each step validated and each ruled out as the cause), but
the wheelchair's wheel crawls continuously under a position drive with
the target interpreted as neither degrees (90 -> endless spin) nor
radians (pi/2 -> passes 90 deg and keeps going, ~4 deg/s). Next probe:
read the joint state through omni.physx directly instead of Fabric
relative rotation, and check drive semantics (position vs velocity,
stiffness units) for reduced-coordinate articulations in Isaac 6.0.
Limited-joint verification (prismatic/revolute with limits) is solid —
that path verified 7/8 registry assets.

## 4. Mesh segmentation for baked assets — ✅ SHIPPED 2026-08-07
`scripts/segment_mesh.py <asset_id>` + hub "Segment baked mesh" button:
connected-component split with exact-position vertex welding (scan exports
duplicate vertices per strip — the wheelchair tire was 8193 false islands
until welded, 5 after), small islands merged into the nearest part by
centroid, part count capped at 32, primvars/normals/material bindings and
local transforms carried, original mesh deactivated in the derivative.
Validated: wheelchair — every fused left/right pair split cleanly (wheels,
casters, handrims, axle); office chair — 26 parts from one fused mesh;
segmented assets render correctly with materials intact. Remaining
follow-on (now part of #3): cross-mesh LINK GROUPING — cluster the split
parts into articulation links by side/proximity (left wheel = tire + rim +
spokes) so the draft-spec proposes wheel joints directly.

## 5. VLM visual classification
`class_source` seam exists (`filename_guess`/`hint`/`human_visual`); wire
thumbnail → VLM → class so the visual step stops being manual.

## 6. Deformables path
Cloth/soft assets currently get rigid treatment. Widen
`create_deformable_mesh` enum (10 of 15 presets unreachable); route
make_sim_ready by material class.

## 7. Mass/inertia fidelity
Per-part masses, inertia tensor + COM validation (SPD check — cad_creator
`inertia_is_spd` prior art), density-from-volume per link.

## 8. Scene-level physics
`build_scene_from_blueprint` authors zero physics; consume the sim-ready
registry in scene building; per-object physics profiles in blueprints and
the floor-plan instantiator.

## 9. Format breadth
MJCF/URDF/glTF ingest (MolmoSpaces raw is MJCF); conversion step before
the gate.

## 10. Ops hygiene
Hub multi-reviewer auth, id-collision dedupe, corrupt-file guard,
priors coverage (~25 classes; some ranges too broad — split
electronics_handheld), thumbnail re-render after corrective actions.
