# Sim-Ready Asset Pipeline — Backlog

Ordered by leverage. Top item first.

## 1. Finish line: finalize/promote step (TOP)
"Approved" must mean "finished and sim-usable". Today 5 of 7 registered
assets contain zero physics, and one registry entry points at the raw
`.usdz` instead of its physics derivative.
- Promote step (hub action + CLI): make_sim_ready if physics absent →
  re-audit → stamp customData → registry `file` points at the derivative →
  copy/land in ONE canonical library dir (`workspace/assets_fixed/` today;
  decide final library location).
- Approval gate: rigid categories require physics authored; block or
  auto-promote otherwise.
- Cleanups: steel_frying_pan registry pointer (raw usdz → derivative),
  11-byte corrupt aston wrapper, black__decker/black_decker id-collision
  dedupe.

## 2. Automated live verification (verify_asset_live)
Per-joint drive sweep + rigid drop test in a live/headless Isaac session →
measured evidence written to the registry → auto-flip `*_unverified` →
`*_verified` when within schema tolerances. Hub button per asset. The
overbed-table/office-chair evidence was gathered with one-off scripts;
this makes verified categories scale.

## 3. Articulation authoring at scale
Draft-spec proposal from part structure (hub editor shipped 2026-08-06);
next: AI/VLM joint suggestion (discovery hub `suggest_joints_ai` prior
art), axis/limit inference from geometry, per-link masses in
articulate_asset.

## 4. Mesh segmentation for baked assets
Connected-component split (office chair, wheelchair wheels are clean
disjoint islands inside fused meshes). Unlocks articulation for the
largest class of scan assets.

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
