# Sim-Ready Asset Pipeline — Backlog

Ordered by leverage. Top item first.

## 0. Autonomous visual approval (machine sign-off) — ✅ core SHIPPED 2026-08-07
The system visually approves its own downloaded USDZ files; the hub is
the exception surface. `scripts/visual_qa.py`: orbit renders → LOCAL
judge ensemble (Cosmos-Reason2-2B via vLLM :8021, gemma4 via Ollama) +
Claude tiebreak (fires on judge-vs-judge OR judges-vs-entry-class
disagreement) → named rubric checks with evidence (judges_healthy,
identity_agreement with sibling-class keyword credit, integrity,
scale_in_prior, physics_ready incl. mass-in-class-prior, no_error_
callouts, rigid_scope) → unanimous pass = machine sign-off via the same
do_approve+promote path humans use. Schema enforces governance: machine
reviews (reviewer_type=machine, models required) validate ONLY on rigid
categories; every Nth (VISUAL_QA_AUDIT_EVERY=5) approval is
audit_sampled for human spot-check in the hub's audit lane. Hub watcher
auto-QAs new assets when VISUAL_QA_AUTO=1. Multi-view orbit renders
(`render_views`, one usdrecord run, time-sampled camera) at ingest and
re-rendered after every corrective action; judges get measured bbox
dims (renders carry no absolute scale). asset_class is enum-CONSTRAINED
via structured outputs on all three judges. Validated: beer_bottle +
chess_piece_king machine-approved end-to-end on local judges alone
(both promoted to the library); az_vaccine_vial correctly fail-closed
to human review (gray render is honestly ambiguous vial-vs-bottle).
Full-queue sweep 2026-08-07: 135 verdicts — 44 machine-approved, 91
fail-closed to human, 0 errors. Live drop-tests auto-dispatched at
approval flipped the fleet to 45 rigid_verified with measured evidence
(registry: 8 → 56 assets in one day, 46 machine-signed, 10
audit-sampled). Density remediation: the machine revoked 3 of its own
approvals (4198–10090 kg/m³ implied) and re-approved them under
size-aware mass. Product spec lookup (`scripts/product_lookup.py`,
Claude + web search, cached in workspace/knowledge/product_specs.json)
turns named products' class RANGES into published SPECS — Vision Pro
authored at Apple's 0.625 kg. Scan is package-aware across the ENTIRE
assets folder (top-most-USD rule suppresses package components,
.thumb.usd filtered; 7910 files → 3027 assets) and the hub watcher +
judges grind it continuously under VISUAL_QA_AUTO=1.
Shipped since: ✅ auto live-verify at machine approval; ✅ judge
supervision (launch_judges.sh, hub-invoked).
Newton cross-engine verification SHIPPED 2026-08-07:
`scripts/verify_asset_newton.py` (runs in the Newton venv, fully
headless — no Isaac, no Kit, GPU or CPU). `rigid`: re-drops registry
assets in Newton XPBD; 46/46 verified rigids AGREE with PhysX
(evidence in verification.newton; settle-criterion tolerates tipping
AND rolling — a rolling beer can IS at rest). `drape`: cloth drop for
deformables in Newton VBD (welded + dedup double-sided faces + largest
component + unit-normalized scale); clean towel grid PASSES flat on
ground, ball-like 'bandage' honestly FAILS (not cloth), degenerate
scan topology reported as needs-mesh-repair instead of garbage
numbers. Also = the headless CI mode #2 wanted: rigid verification now
needs no live Isaac session.
Laundry-folding mission (2026-08-08 — user: "laundry folding is a big
reason for the robot"; cloth is CORE, not peripheral):
`scripts/make_garment.py` generates clean parametric garments (towel,
hand_towel, washcloth, napkin, tshirt silhouette; quad grids, adaptive
resolution — cells under ~3.5% of the long side leave the solver's
stable regime) and ingests them as deformable_unverified cloth.
`verify_asset_newton.py fold`: fold-PERSISTENCE test — mesh folded in
half geometrically at spawn, dropped, settled; real cloth STAYS folded
(~0.5 length, flat 2-layer stack), springy shells pop open. Validated
5/5 garments: drape PASS + fold PASS (0.49-0.50). Learned: bending
stiffness must be fabric-realistic (edge_ke ~0.05; the 10.0 default is
spring steel — pops folds open, detonates crease energy); moving pinned
particles at runtime is NOT a supported VBD pattern (springs are ignored
by VBD, XPBD explodes at these stiffnesses) — actuated grasp/fold
belongs to the robot-policy workstream, asset verification uses the
zero-actuation persistence formulation.
Material-behavior families (2026-08-08 — user: "many material types,
paper, cloth, jells, liquids, foam, rubber"): presets cover 4 cloth
weights + leather/paper/plastic_film (shells) and 5 volumetric
(sponge x3, rubber x2, silicone, gel) + rope. Verification per family:
shells = drape + fold-persistence (SHIPPED); volumetric = NEW `squish`
test (SolverXPBD FEM soft-grid proxy at the asset's dims with the
preset's Young's/Poisson -> Lame params; foam brick PASSES with dead
landing + compression; explicit SemiImplicit was knife-edged — skin
tri_ke must be ~1e-4 per the diffsim example). Remaining:
- [ ] Squish material DISCRIMINATION: XPBD tet stiffness saturates
      (rubber E=1e5 compresses like foam E=5e3) — calibrate the
      k_mu/k_lambda -> XPBD compliance mapping so presets separate.
- [ ] Liquids + granular: nothing exists (no preset, no authoring, no
      verification). Newton ships MPM examples (granular, multi-material)
      = headless path; PhysX particle fluids = scene-time path. Classes
      needed: liquid containers should model contents (fill-level mass).
- [ ] Rope/cable settle test (1D deformable; the chain asset is waiting).
- [ ] Actuated fold benchmark (grasp-drag-release) when a supported
      moving-attachment path exists (Style3D solver / robot gripper
      contact like example_cloth_franka).
- [ ] Stretch: feed drop/settle video from live verification to Cosmos
      (video-native) for "does it fall like a real chair" judgment.
- [ ] Split over-broad priors (electronics_handheld passed an oversized
      1.26 kg mouse); lean on spec lookup for branded items.
- [ ] Triage policy for NVIDIA stock content (Collected_Environments/
      Robots/Sensors/People ~1550 assets) — much is already sim-ready;
      wholesale re-wrapping may be waste. Decide with the human.

## 0.5 Characters + environment effects (2026-08-09)
Rigged humans SHIPPED: ingest detects UsdSkel (scan_scene_features:
skeletons/joints/animations/skinned meshes + UsdLux light count) →
`character_rigged` category (schema requires the skeleton record;
machines cannot sign characters — humans review humans). Characters are
KINEMATIC animated colliders, never dynamic rigid bodies; rigid-physics
authoring skips them. `scripts/verify_character.py`: headless rig check
— topology (bind/rest transforms match joints), skinning weights
normalized, animation semantics (static pose clip = valid, motion
applies at scene time via omni.anim.people; multi-sample clips must
actually move joints). Validated: 6/6 Collected_People rigs PASS (78
joints, 14-17 skinned meshes each).
Human-like character MOTION (researched 2026-08-09, primary sources =
the installed extensions): Isaac Sim 6.0 does this via
**isaacsim.replicator.agent (IRA) v1.6.8** — installed in our build with
its full stack: omni.anim.graph (locomotion blending), omni.anim.
navigation (navmesh path planning), omni.anim.retarget, omni.anim.
behavior (behavior trees). omni.anim.people is DEPRECATED in favor of
IRA. Our Collected_People folder is the complete kit: characters +
Biped_Setup.usd (shared rig/anim graph) + Animations/ clip library
(4 walk cycles + mirrors, idle, wave, Sit, LookAround, push_button).
How it works: YAML config (environment.base_stage_asset_path; character
groups with num/asset_path/routines — wander/patrol with speed_range +
weighted idle animations; sensor groups; Replicator writers for
RGB/bbox/segmentation GT) → api.load_config_file + setup_simulation +
start_data_generation_async (headless-scriptable) → characters spawn on
a baked NAVMESH, behavior trees (MoveTo + RandomNavMeshPoint) plan
paths, the anim graph blends walk/idle clips = human-like motion.
App kit: isaacsim.exp.action_and_event_data_generation.base.kit (IRA
preloaded). Integration plan for scene testing:
- [ ] Launch path: add IRA app/kit option to launch_isaac.sh (or enable
      isaacsim.replicator.agent.core in the standard app).
- [ ] `make_people_config.py`: generate IRA YAML from a scene blueprint
      + our verified character_rigged assets (asset_path can point at
      Collected_People); bake navmesh over the blueprint scene.
- [ ] Verification: extend verify_character with a LIVE motion check via
      Kit RPC — command a wander routine, sample skeleton root over
      time, assert navmesh-constrained displacement + gait (no sliding:
      root speed within walk clip's speed_range).
- [ ] VLM judge on motion clips (Cosmos is video-native): 'does this
      person walk like a person' — same judge stack as visual QA.
Environment-effects ladder (each is a different sim maturity):
- [ ] Lights: detection SHIPPED (report.lights); next: blueprint light
      placement (UsdLux Sphere/Rect/Dome + intensity/color) for
      perception-domain randomization.
- [ ] Sound: UsdMedia.SpatialAudio exists in USD — detect + place as
      metadata; no acoustic physics in PhysX/Newton (ray-traced audio is
      an Omniverse renderer feature).
- [ ] Wind: cloth-relevant (laundry line!). Newton soft grids expose
      tri_drag/tri_lift aero terms; a lateral-wind drape deflection test
      is feasible headless. PhysX scene wind at scene level.
- [ ] Heat: no thermal solver anywhere in the stack — represent as
      SEMANTIC thermal zones (customData temperature tags on hot
      surfaces, e.g. stove burners) that policies must avoid; define the
      convention in the registry schema when first needed.

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
Wheel-drive investigation RESOLVED 2026-08-07: the drives were always
correct (targets in degrees). The failing harness had pinned the base via
kinematicEnabled — a kinematic link INVALIDATES the whole PhysX
articulation ("did not match any articulations" in the console; no
joints, no drives, bodies drift free), which explained every anomalous
measurement. The legal construct is a FixedJoint from the world to the
base link; with that anchor the wheelchair verifies 4/4 wheels — drive
wheels 89.989 deg for a 90 deg command (0.011 deg error), casters 0.163
deg error, zero base drift. Diagnosis instrument for next time:
PhysxSchema.JointStateAPI (PhysX writes joint pos/vel into USD attrs) and
the omni.physx.tensors "did not match any articulations" console error.
Remaining in #3: VLM-fed joint suggestion for non-wheel mechanisms, limit
inference.

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

## 5. VLM visual classification — ✅ SHIPPED 2026-08-07
`scripts/vlm_classify.py <asset_id>` + hub "Classify visually (VLM)"
button: thumbnail → Claude vision (structured output, constrained to the
prior class keys) → class_hint with `class_source: "vlm"`, checks re-run
under the corrected prior, auto-rescale + physics re-applied when the
class changes the plausible size. Also reports moving parts visible in the
render (feeds future joint suggestion). Validated: a chess rook renamed
`random_object_42.usdz` (filename matching: nothing, 3.0 m passed
unverified) was identified from pixels alone, reclassified chess_piece,
auto-rescaled to 0.08 m with wood_oak + 85 g. The wheelchair classified
"Manual wheelchair, high confidence" with every moving part enumerated.
Requires ANTHROPIC_API_KEY (present in .env). Remaining: auto-VLM at
ingest behind an env flag; use the moving-parts report in the
articulation draft tier (#3).

## 6. Deformables path — ✅ routing SHIPPED 2026-08-07
`create_deformable_mesh` accepts generic types (cloth/sponge/rubber/gel/
rope) or any exact preset key — all 15 presets reachable; unknown types
fail loud. Routing (same-day): 9 soft prior classes (pillow, towel,
blanket, curtain, clothing_garment, face_mask, bandage, sponge,
rope_cable) carry a `deformable` type; ingest SKIPS rigid authoring for
them (a rigid shell on cloth is a physics lie) and proposes the new
`deformable_unverified` category. PhysX deformable APIs are Kit-only, so
authoring happens LIVE: `build_scene_from_blueprint` resolves a registry
asset's `deformable` field and applies the preset (`_soften`: per-mesh
PhysxDeformableBody/SurfaceAPI + preset params + density) at placement.
Schema: deformable_unverified/verified categories require the
`deformable` field; machine sign-off remains rigid-only — deformables
always need a human. Validated: disposable_medical_masks → face_mask/
cloth, bandagem → bandage/cloth, both deformable_unverified with zero
rigid bodies; blueprint codegen emits PhysxDeformableSurfaceAPI with
cloth_cotton params. Remaining: live drape/settle verification to reach
deformable_verified; deformable-aware audit checks (current audit only
speaks rigid/articulated).

## 7. Mass/inertia fidelity
Per-part masses, inertia tensor + COM validation (SPD check — cad_creator
`inertia_is_spd` prior art), density-from-volume per link.

## 8. Scene-level physics — ✅ SHIPPED 2026-08-07
`build_scene_from_blueprint`: per-object `sim_ready_asset` field resolves
to the verified library file (physics + joints arrive through the
reference, no re-authoring); per-object `physics` profile
(manipulable/tool/furniture/static/decoration) + `mass_kg` authors
collision/RB/mass on placement; PhysicsScene ensured whenever physics is
used. New `list_sim_ready_assets` data tool lets the LLM browse the
library (with category filter + verification evidence) before building.
Validated headless: 4-object hospital-corner scene — library table
composed with 3 rigid bodies + 2 verified joints, cup manipulable at
0.3 kg, cabinet static collider, decoration untouched. Remaining: same
profile plumbing in the floor-plan instantiator (multimodal path).

## 9. Format breadth
MJCF/URDF/glTF ingest (MolmoSpaces raw is MJCF); conversion step before
the gate.

## 10. Ops hygiene
Hub multi-reviewer auth, id-collision dedupe, corrupt-file guard,
priors coverage (~25 classes; some ranges too broad — split
electronics_handheld), thumbnail re-render after corrective actions.
