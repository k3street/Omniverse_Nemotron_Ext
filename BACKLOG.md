# Sim-Ready Asset Pipeline — Backlog

> This file is the detailed engineering journal for the asset pipeline. See
> `ROADMAP.md` for the current release gates and cross-product priorities.
> New general product work belongs in the roadmap; keep this file focused on
> measured asset, material, and simulation findings.

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
Newton 1.5 migration 2026-08-14: standalone probes now pin Newton 1.5.0 +
Warp 1.16.0 in `requirements-newton.txt`, use explicit reusable
`CollisionPipeline`/`Contacts`, preserve cable body-frame and Dahl defaults,
disable Newton 1.5's new visual-only USD import in headless rigid batches, and
record both package versions in new evidence. Version-suffixed
`*_newton_1_5` fields preserve the Newton 0.2 baselines rather than relabeling
or overwriting them. Measured on the GB10: rigid cross-engine gate 30/32
(frying pan still moving at 4 s; planter collision hull does not make the
expected 10 cm drop); cable load/slack PASS; cable grasp PASS; foam-brick
soft-body compression PASS; and particle-contact cloth grasp 5/5 PASS with
13-37 mm slip. Newton 1.5 full-surface VBD contact is NOT ready here: the
washcloth is ejected by tens of metres, so the option remains an explicit A/B
flag and is disabled by default. The Franka probe executes and carries the
washcloth 0.23 m, but FAILS release/landing because the cloth rides up with the
retreating hand. The Isaac Lab environment stays separate: 3.0 Beta 2 has
experimental VBD cloth, coupled solvers and `Isaac-Lift-Cloth-Franka-v0`, but
its release dependency is Newton 1.2.1. Revisit the task port when Isaac Lab
officially consumes 1.5.
Psyonic hydroelastic hands SHIPPED 2026-08-14: the non-destructive
`make_newton_hydro_hands.py` wrapper generator keeps the canonical robot and
Isaac runtime untouched, selects generic `Physics=physics`, replaces 68
overlapping/open hand colliders with 22 watertight Newton SDF hulls, and adds
20 adjacent-link filters. CPU topology/import validation preserves the
52-body/52-joint articulation with zero superseded shapes still colliding.
GB10 validation builds all 22 SDFs; an isolated distal-index/probe pair emits
64 reduced hydroelastic contacts at 2 mm penetration. Reproduce the CUDA gate
with `smoke_newton_hydro_hands.py`; grasped objects must also be hydroelastic.
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
Metropolis People packs DOWNLOADED 2026-08-09 (~/Desktop/assets/
Metropolis_People, 9.4 GB, 3085 files, 0 failures, from the Isaac 5.1
content S3 — 6.0 content not yet published there): 232 extended DH
character variants + 22 DH + 23 stylized = 277 characters across THREE
rig families (DHGen 78-joint, stylized 101-joint, Biped 81-joint clips).
Animations folder = the same 24 clips as Collected_People. Spot-check:
9/9 sampled rigs verify (many are RIG-ONLY — no clip authored, all
motion from the anim graph at scene time; verify_character now accepts
that as valid by design). Excluded from the hub watcher
(ASSET_SCAN_EXCLUDE=Metropolis_People) to avoid flooding the human
queue; batch verify_character when registering. These are the crowd
population for IRA scene testing.
Physically accurate cords SHIPPED 2026-08-10 (user downloaded plugs/
cords/electronics incl. 4 soldering irons): `scripts/make_cable.py` —
capsule-chain articulated cables (spherical joints, coincident local
frames, gravity-torque-scaled joint friction, rubber material, linear-
density link masses) + `compose` (iron + cord + plug as ONE articulation
rooted at the plug, physics on clean UNSCALED proxy boxes, scan meshes
as physics-free visuals). Live-validated: iron DANGLES from its cord
(slack cord crumples into a realistic bundle, iron hangs tip-down,
stable). Hard-won PhysX facts: an external maximal FixedJoint against a
floating-base articulation explodes past ~4 links — the fixed-base
convention is ArticulationRootAPI ON the world FixedJoint prim; every
joint needs coincident frames at spawn (default zero frames slam
origins together); joint frames through scaled scan wrappers are
treacherous (proxy bodies); friction above link gravity torque makes
the cord rigid. Electronics priors added (soldering_iron, power_plug,
power_strip, charger; 'soldering' removed from hand_tool). HONEST STATUS after live iteration: assembly is STABLE (no explosion,
iron carried by cord, plug anchored, wire-baked scans cleaned by
deactivating the 'wire' mesh + rescaling to the true body) but the cord
CRUMPLES instead of draping — omni.physx does NOT enforce
UsdPhysics.SphericalJoint cone limits inside articulations (observed
90-deg+ folds at 15-deg authored cones), and free ball joints + gram
links crumple under solver noise with slow energy climb.
RESOLVED 2026-08-10: the cord now DRAPES — straight vertical hang,
plug->iron 1.045 m of a 1.0 m cord, gentle swing, iron dangling
tip-down. The cure was CONVERGENCE, not limits: gram-scale links
carrying a 100 g tool through 24 constraints cannot converge at 60 Hz
default iterations (limits were being swamped, not ignored... though
SphericalJoint cone limits ARE also unenforced in articulations). The
baked-in recipe: D6 joints (locked translation, +/-20-25 deg rotation
limits, stiffness/damping drives toward straight), link mass FLOORED at
15 g (physical 2.5 g recorded in customLayerData), 64 position
iterations per link + articulation, physxScene 240 Hz. Scan cleanup:
Soldering_Iron(1)'s baked cord is a separable mesh named 'wire' —
deactivate + rescale to true body.
Second donor (desk lamp, 2026-08-10) — fixed 3 real bugs, 1 open:
FIXED: upright mode (a lamp must KEEP its pose and stand; the hanging-
tool convention rotated it onto its side), weld pairing (weld_a/weld_b
swap ends between modes — crossed welds launched the assembly), and
collision filtering on welded pairs (the cord's end links start INSIDE
the tool's box proxy; unfiltered, PhysX ejects them and they tunnel
through the floor — lowest cord z went -0.083 -> -0.017). Also a
classification bug: "Desk_lamp" matched the `table` prior on "desk" and
scaled to a 1.5 m lamp; new desk_lamp prior (0.3-0.65 m, articulable).
OPEN — the honest one: a SLACK cord still coils. Under tension (tool
hanging) the chain hangs straight and correct; lying loose it collapses
to 0.19 m of 0.7 m even with bend drives scaled to link weight. D6
rotational drives are not holding shape at this scale.
SOLVED 2026-08-10 via (c): `route_cord` + `cord_mode='routed'` is now
the DEFAULT. A static Bezier cord leaves the tool's real exit along its
local axis, arrives at the plug from the correct side (the end tangent
must point BACK toward the tool — the +h sign bug made the curve
overshoot and thread through the plug), is arc-length matched to the
requested cord length so slack shows as droop, meanders laterally so it
does not read as a rod, and clamps to the surface it lies on. Sampled
into capsule colliders: collidable scene dressing, zero solver cost,
always looks right. Validated on the desk lamp: 0.75 m cord leaves the
base, S-curves across the table, terminates at the plug. Dynamics stay
opt-in (`cord_mode='dynamic'`) for cords the robot actually grasps —
that path still needs tension to hold shape.
Routed-mode fixes 2026-08-11 (all found by the human looking at the
sim, then confirmed numerically):
- The routed branch RETURNED before the joint code, so the plug was a
  free rigid body attached to nothing — the static cord's capsules shoved
  it away on play. A routed assembly is now STATIC throughout (colliders,
  no rigid bodies): verified zero movement over 8 s of play.
- Plug orientation: aligning one axis leaves ROLL free, and a scanned
  plug has no semantic "up" (its local +Z IS the cord axis). Replaced the
  assumption with a geometric rule — roll about the cord axis to whichever
  angle lies FLATTEST — then lift to rest and re-route the cord to the
  moved entry.
- Plug was 4.8 deg nose-up because it followed the cord's raw end
  tangent; a plug on a surface is level, so the orientation now uses the
  tangent's horizontal projection.
- Cord endpoints are no longer ground-clamped (that opened a 3 cm gap at
  the join); interior points still clamp. Exit point lifted by one radius
  so the cord rests ON the surface instead of half-sunk.
- Kit CACHES USD layers: a file rebuilt on disk keeps serving the old
  content until Sdf.Layer.Find(path).Reload(force=True).
Human corrections are now CAPTURABLE (2026-08-11): the user dragged the
plug to the right join point in the viewport and the correction was lost
— a forced Sdf.Layer.Reload discards viewport edits (the root-layer
override spec survives but with no authored properties, which is how it
was diagnosed). `scripts/capture_attachment.py --asset X --prim ... --cord
...` reads the corrected pose out of the live stage, converts it into the
ASSET'S OWN space, and stores it in workspace/knowledge/
cord_attachments.json; `attach_frame` prefers a stored attachment over
its geometric guess, so the correction is made once and reused forever.
CAPTURE BEFORE RELOADING.
Dynamic cords ANSWERED 2026-08-11 — the user pointed at Vertex Block
Descent (ankachan.github.io/Projects/VertexBlockDescent; Chen, Liu, Yang
& Yuksel, SIGGRAPH 2024), which is what Newton's SolverVBD implements —
the solver we were already using for cloth without connecting it to the
cable problem. Its stated edge over XPBD at extreme mass ratios is
exactly our failure. Measured by `scripts/vbd_cable_probe.py` (Newton
rod + VBD, PHYSICAL masses, no 15 g floor):
  HANG  100 g tool on 3.02 g segments -> arc 0.954 m, dead straight,
        hanging 1.200 -> 0.246 m
  SLACK 1.0 m of cord across a 0.55 m span, both ends pinned -> HOLDS
        THE BOW (span 0.548 m, z 0.004-0.006, lying flat)
The PhysX D6 chain collapsed that same slack cord to a 0.19 m hairpin.
Newton API facts: add_rod builds RIGID BODIES + cable joints (not
particles); one quaternion per SEGMENT mapping local +Z to the segment
direction; given positions are the REST shape; VBD needs
ModelBuilder.color() before finalize; pin by zero inverse mass (a world
FixedJoint let the rod fall 78 m).
PORTED 2026-08-11: `build_cable(..., physx_floor=False)` authors the
PHYSICAL linear-density mass (3.02 g segments, no 15 g fudge) and records
length/radius/links/both masses in customLayerData; `verify_asset_newton.py
cable <path|id>` rebuilds that cord as a Newton rod and verifies it under
VBD. First run PASSES: hang straightness 1.000 under a 100 g load, slack
span 0.548 m of 0.550 intended (ratio 1.00). USD stays the interchange,
Newton is the verification engine — same split as rigid/drape/fold/squish.
CABLE ACTUATION CLOSED 2026-08-11: `verify_asset_newton.py grasp` — a
KINEMATIC gripper body (zero inverse mass set BEFORE finalize, pose
driven each substep) holds one end while the far end stays anchored.
Criteria are measured, not smoke: gripper follow error, arc stretch
ratio (a cable that stretches to reach is not a cable), anchor drift.
PASSES on a bare cord and on the composed soldering iron: 0.335 m move,
0.0 mm follow error, arc stretch 1.000-1.001, anchor drift 0.00 mm, at
3.77 g physical segments. The same pattern that failed for CLOTH
particles works for rigid rods — mass is baked at finalize either way,
but VBD integrates driven rigid bodies correctly.
- [x] CLOTH actuation SHIPPED 2026-08-12 (`verify_asset_newton.py
      cloth_grasp <garment>`): two kinematic finger boxes pinch a garment
      corner (friction mu=1.0, no attachment constraint) and lift 35 cm.
      5/5 garments PASS on CUDA in 90 s: slip 4 mm, corner rises the full
      35 cm, the garment hangs 117-146 cm straight down and SETTLES
      (residual swing <= 0.7 cm).
      Four findings, each of which silently produced wrong physics:
      1. `SolverVBD(..., integrate_with_external_rigid_solver=True)` is
         what makes cloth see rigid shapes at all. Without it the cloth
         passes through the fingers and falls 25 m — no error, no warning.
         Newton's own example_cloth_franka sets it; nothing else does.
      2. `wp.array.numpy()` is a VIEW on CPU but a COPY on CUDA. Every
         pose-driven gripper in this file was mutating a copy. It worked
         only because we were pinned to CPU; moving to GPU exposed it.
         Now `.numpy().copy()` -> mutate -> `.assign()`.
      3. "Centroid rose" is not a grasp test — it fails a PERFECT grasp.
         A flat sheet lifted by one corner necessarily LOSES centroid
         height as it becomes a hanging sheet. The real test is that the
         garment stays within its own reach of the fingers (`suspended`),
         drapes below them, and nothing floats above the grip.
      4. Single-frame metrics lie. The t-shirt "passed" at drape 7 cm
         while swinging like a pendulum with cloth flung ABOVE the hand.
         Fixed with a 3 s post-lift hold plus a measured `settled` check
         (mean height varies < 5 cm over the final half second).
      Damping note: `tri_kd` is the only internal cloth damping Newton
      exposes and it is EXPLICIT — stable only while dt < 2m/kd, and a
      cloth particle weighs ~0.4 g. Raising it to 0.5-2.0 detonated the
      solve at 20, 60 and 120 substeps alike. Viscous per-frame velocity
      damping (CLOTH_DAMP_HZ=2.0) settles the swing instead, and is
      unconditionally stable.
- [x] ROBOT pick-and-place on cloth SHIPPED 2026-08-12
      (`scripts/pick_place_cloth.py`): a Franka FR3 under SolverFeatherstone
      one-way coupled to cloth under SolverVBD — NVIDIA's own current
      reference pairing (`newton.examples cloth_franka`). Isaac Lab 3.0 Beta 2
      now has an experimental cloth-lift equivalent, but pins Newton 1.2.1;
      keep this 1.5 probe isolated until that integration catches up.
      Newton 0.2 baseline — washcloth (18 g): 5/5 runs carried it 0.20-0.26 m of a commanded
      0.30 m, intact, landed on the table, settled.
      The finding that shaped it: A PARALLEL-JAW GRIPPER CANNOT PICK A FLAT
      SHEET OFF A TABLE. The fingers close beside zero-thickness fabric
      pressed against the surface. Dropping it first does not help — a plain
      square sheet lands flat again (measured 8 mm of loft, its own
      thickness); the reference gets away with it only because a shirt has
      sleeves and a collar. Staged instead with a flap over the table edge,
      and the grasp point chosen from the SETTLED geometry the way a
      perception stack would.
      Live path threaded too: 5 cloth workpiece classes in the object
      palette, `_DEFORMABLE_WORKPIECE_CLASSES` + `_apply_cloth`
      (PhysxDeformableSurfaceAPI) in the instantiator, and `_is_deformable`
      routing cloth to a friction grasp in pick_place.py — a
      UsdPhysics.FixedJoint has no deformable body to bind, so on cloth the
      weld-grasp defines cleanly and holds NOTHING.
      Entry point closed 2026-08-12: `create_franka_physics_pick_scene`
      takes a `workpiece` (any pickable palette class) instead of hardcoded
      cube_small. `_workpiece_profile` derives physics + footprint +
      spacing from the palette; `require_rigid_body_api_for_workpieces` is
      conditional; `grip_style` (friction vs fixed_joint) is chosen from
      what is being picked and reaches the generated controller. Count is
      FITTED to the table and the shortfall reported
      (object_count_dropped) — 3 towels would otherwise be placed at
      x=0.38/2.34/4.30 against a table ending at 1.55.
- [ ] Napkin (41 g, 2.3x the washcloth) SLIPS the one-flap friction pinch:
      3 runs carried it 0.11 / 0.09 / 0.09 m of a commanded 0.30 m. A
      friction grasp is mass-limited by contact patch x normal force. Needs
      a bigger bite, a second grasp point, or two hands.
- [ ] Next for laundry: two-hand grasp (fold needs both corners held),
      then grasp -> place -> release against a table.
- [ ] `drape` is VACUOUS for the generated garments — they are authored
      as flat sheets, so initial z-extent is 0 and "collapses to flat"
      cannot fail. It remains meaningful for scanned deformables. Give
      generated garments a crumpled/draped-over-edge start.
- [ ] Mass-floor honesty: reduce toward physical 2.5 g links via TGS
      solver tuning or Newton (cross-check with add_joint_cable).
- [ ] plug-socket insertion affordance; cable in scene blueprints.
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
