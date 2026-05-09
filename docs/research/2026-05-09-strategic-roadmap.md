# Strategic roadmap — Isaac Assist 2026-05-09

Living strategy doc. Authored after CP-86 ships, the function-gate sweep stabilizes, and the multimodal-foundation spec lands as a parallel architectural axis. Two satellite agents are scoping yrkesroll-based scenario expansion and CP canonical quality/format audit; this doc is the META layer that decides what those efforts compose into.

---

## Current state snapshot

86 CP canonicals (CP-01..CP-86), 37 function-gate verified, ~13 untested, 1 hard-blocked (CP-06 builtin-handler FixedJoint integration). 213 tool handlers in `tool_executor.py` — Tier A/B/C all built including the Sprint-2/3/4/5 tool burst. Hard-instantiate path is live: when retrieval similarity ≥ 0.45 and margin ≥ 0.20, the canonical executes deterministically and the LLM is reduced to a 23-tool verify/inspect surface. 298 templates total across 19 prefixes (CP=86, AD=23, M=17, T=14, D=14, K=12, S=12, P=12). Verified primitives: cuRobo and builtin pick-place handlers, conveyor pipeline, multi-cube `drop_targets`, surface gripper (raycast workaround for IsaacSurfaceGripper articulation bug), Cortex behavior trees, vision classifier gate, kit tray, mutex coordination, multi-robot handoff. Open frontiers: belt-pause-from-callback bug (4 canonicals), cuRobo drop precision (~17cm not 5cm), single-tenant Kit RPC concurrency, ROS2 bridge handlers stubbed, IsaacLab RL env scaffolding absent, vision viewport-capture is mock (real Replicator capture pending).

---

## Strongest unique value proposition right now

Off-the-shelf Isaac Sim gives you a Python REPL inside Kit and 40 example scenes. Isaac Lab gives you batched RL envs. Cortex gives you behavior trees. None of them give you **deterministic natural-language scene authoring with form+function dual verification and a 86-template hard-instantiate retrieval cache**.

Concretely: a user types "build a 4-color sorting station with overhead vision and a reject lane" and within 10 seconds the orchestrator hard-instantiates CP-35 (or the closest match), executes 106 tool calls in Kit, runs `verify_pickplace_pipeline` to confirm the build, and runs `simulate_traversal_check` for 180 s to confirm cubes actually arrive. The LLM never authors USD code at any step that mutates the stage. That deterministic substrate is the moat — and nothing in NVIDIA's stack ships with it.

The substrate composes three things that exist in isolation but not together: (a) a **typed canonical library** with `settle_state` + `verify_args` + `simulate_args` machine-readable success criteria, (b) a **hard-instantiate retrieval pipeline** with margin-gated routing into a sandboxed exec, and (c) a **dual gate** (form check on the stage shape, function check on simulated outcome). Anyone can build (a) alone. The (b)+(c) substrate is what makes (a) operational and bullshit-resistant.

The secondary value is the **70+-tool action surface** that makes pick-place the most-explored corner of an agent that can also do SDG, articulated joints, sensors, ROS2 (when handlers fill in), Cortex, mutex, vision-gate, and surface-grip. The pick-place dominance is a coverage skew, not a capability cap — every tool that ships unlocks scenarios across the whole canonical lattice.

---

## Top 3 strategic options

### Option A — Deepen the canonical lattice (more CP, more verification)
Push CP-87..CP-150, add multi-cube function-gate to every canonical that builds, fix the belt-pause-from-callback bug, get drop precision under 8 cm, fix CP-06's FixedJoint integration. Verification infrastructure becomes the product: an industrial QA bench for "does this canonical actually deliver cubes."

**Strengths**: linear, measurable, regression-safe. Each new CP raises retrieval recall. The current pipeline (settle_state, verify_args, simulate_args, hard-instantiate, 180s function-gate) is proven and just needs more inputs. Lowest risk per unit value delivered.

**Weaknesses**: pick-place is already the saturated axis. CP-87..150 is diminishing returns unless the categories diversify (welding, assembly, mobile-manipulation, force-control). Doesn't address the parallel "Kimate values G1 + Nav2 + ROS2" finding from the Sonnet priorities research.

### Option B — Punch through to multimodal foundation + new pattern hints
Ship the multimodal foundation (`LayoutSpec` IR, role bindings, ratifier, verifier registry per `2026-05-08-multimodal-foundation-spec.md` §20). Add `pattern_hint = "navigate"` and `pattern_hint = "assemble"` to the closed enum; build the first canonicals in those families. Move the canvas modality and sketch modality off the wishlist into a real producer. Make Isaac Assist multimodal-by-default rather than text-only-with-canvas-someday.

**Strengths**: addresses the strategic gap that text-prompt is currently the *only* working production input. Drag-drop canvas + photo-of-floor-plan + sketch-of-cell would step-change the user demos. The IR cleans up the regex-family that currently lives at every NL→tool-call boundary. Aligned with Anton's "type a prompt and it builds" north star.

**Weaknesses**: multimodal-foundation spec is 22 sections of architecture that isn't shipped yet. Land-all-or-land-none is real — half the IR is worse than none. Risk of 6 months of foundation work before any canonical user sees a benefit.

### Option C — Pivot to G1 + ROS2 + Nav2 + RL (the Kimate priorities)
The Sonnet research on Kimate's merge fingerprint found **whole-body loco-manipulation on Unitree G1, Nova Carter Nav2 warehouse runs, and IsaacLab RL training** as the actual P0 north star Kimate funded Phase 9 to reach. Ship CP-87 (G1 bimanual), CP-88 (Carter warehouse Nav2), CP-89 (IsaacLab RL with 64 envs via cloner), CP-90 (Gemini Robotics ER + cuMotion vision-guided pick), CP-91 (mobile-robot adversarial honesty pre-flight).

**Strengths**: aligns with the merge-gatekeeper's actual priorities (not Anton's expressed preferences in the working branch). Validates the Phase 9 ROS2/SLAM/Nav2 stack that's already merged. Opens the sim-to-real bridge that GR00T fine-tuning + teleop-data-capture pipeline depends on. Strategic positioning: become the canonical bench for NVIDIA's whole-body humanoid stack.

**Weaknesses**: requires real ROS2 bridge handlers (currently stubbed), real Nav2 plumbing, real GR00T inference hookup, real IsaacLab scaffolding. Each of these is a multi-week investment without guaranteed success — the components exist in NVIDIA's stack but binding them through Isaac Assist's tool surface is uncharted. Also: the G1 robot itself isn't a verified asset on the local install (Cobotta absence is the analog — when the example module isn't shipped, the canonical can't ship either).

---

## Recommended path forward

**Combine A + B in sequence; defer C to Phase 9b under Kimate's direct authorship.**

The argument:

1. **A is the proven mode and pays the bills.** Every CP canonical that ships raises retrieval recall, gives the orchestrator more grist, and makes the demo-tape stronger. CP-87..CP-100 in pick-place + assembly + insertion + welding categories is a ~2-week production run that doesn't need new architecture. The canonical-quality audit (parallel agent) and the yrkesroll-based scenario expansion (parallel agent) both feed directly into A.

2. **B unblocks the next 10× of users.** Once the canonical lattice is dense, the limiting reagent stops being "do we have a CP-N for this?" and becomes "can the user *describe* what they want?" Multimodal modalities (drag-drop canvas + photo-of-cell + sketch-of-station) are the reach extension. The foundation work in `2026-05-08-multimodal-foundation-spec.md` §20 sequences `LayoutSpec` IR → ratifier → verifier registry — land that and the modalities slot in cheaply.

3. **C is Kimate's axis, not Anton's.** The Sonnet research is unambiguous: Kimate funded Phase 9 (ROS2/SLAM/Nav2/cuMotion/Gemini Robotics ER) and merged Phase 12 honesty harness. He values the *direction* of CP-N work without driving it. The right move is to **let Kimate drive C** while Anton continues A→B. When Kimate's Phase 9 work needs canonical scaffolding (e.g., a Nova Carter Nav2 canonical), Anton ships the CP-N for it on demand. This is the right division of labor for the merge dynamics; trying to lead C from the working branch produces PRs Kimate may not merge.

**One concrete commitment for the recommended path**: stop building tools speculatively. Every new tool from CP-87 onward must have a documented multi-canonical use case (≥2) before the handler ships. Tier C single-use tools were a Sprint 5 specific pattern — that mode is over now that the lattice is dense.

---

## Specific next 5 milestones

### Milestone 1 — Function-gate ALL of CP-01..CP-86 (close the verification gap)
Bring the 37/86 verified count to 70+/86. Loop through the `simulate_traversal_check` results, raise duration_s on the false-failures, build `function_gate_multi_cube` runs for every canonical with ≥3 cubes, fix or probe-mark the residual real-failure clusters (belt-pause cluster, drop-precision cluster, multi-cube-cuRobo cluster, Cortex cluster). Output: a deterministic pass/probe/fail tag on every CP-N row in `2026-05-07-cp06-onwards-master-plan.md`. Estimated: 4-6 sessions of QA running.

### Milestone 2 — Fix the belt-pause-from-callback bug (unblock 4 canonicals + CP-06)
CP-22, CP-73, CP-74, CP-80 all fail because pause-belt invoked from the post-physics-step callback doesn't propagate to PhysxSurfaceVelocityAPI before the next pick window opens. CP-06 is hard-blocked on FixedJoint integration in the builtin handler. Both are bounded engineering problems with a clear test fixture (the canonicals themselves). One root-cause investigation + one fix (probably the same or adjacent code path) unblocks 5 canonicals and erases the largest "form-gate ✓ function-gate ✗" cluster.

### Milestone 3 — Land Tier-1 of the multimodal foundation (LayoutSpec IR + ratifier)
From `2026-05-08-multimodal-foundation-spec.md` §20: ship the IR schema, the ratifier (pure function — template + spec → role bindings), and the verifier registry. **Don't ship modalities yet** — just the foundation. This is roughly §3, §4, §5, §6, §7, §8 of the spec. Once it's in, every existing text-prompt path migrates to produce LayoutSpec instead of going straight to retrieval, and the next milestone (M4) plugs the canvas modality in for free.

### Milestone 4 — Ship the canvas modality (drag-drop layout producer)
The first non-text modality that produces LayoutSpec. Most user value per unit code: a 2D top-down drag-drop where users place robots, conveyors, bins, sources, sinks, and the canvas emits a `LayoutSpec` that hard-instantiates a CP-N (or composes T3 from multiple). This is the demo-tape moment: "I drew it, the agent built it, the cube arrived." The Sprint scope is in `2026-05-08-multimodal-foundation-spec.md` §11.

### Milestone 5 — Add pattern_hint = "assemble" + first force-control canonical
CP-58 (peg-in-hole) is the only force-controlled canonical and it's form-gate-only. Promote `assemble` from a missing pattern_hint to a first-class one in the IR enum. Build CP-87 (3-part snap-fit assembly with force gate) as the first verified force-controlled canonical. This is the bridge from pick-place dominance to "Isaac Assist also does assembly," which is the next yrkesroll the parallel agent's research will surface.

(Numbering CP-87 forward; if the parallel agents come back with sharper category recommendations from the yrkesroll-based research, accept their reordering — the milestone is "ship the first force-controlled canonical," not specifically CP-87.)

---

## Anti-investments — STOP doing

### Stop building Tier C single-use tools
Tier C (one-canonical-only tools like `nir_material_sensor`, `setup_grasp_pose_sampler`, `setup_nav_robot`) was correct in Sprint 5 to round out research-coverage. From CP-87 onward, every new tool requires a documented ≥2-canonical use case. Single-use tools rot — they ship and then never get exercised again, and the next breaking change to the underlying Isaac API silently ruins them. The handler audit (parallel agent) will probably confirm this on Tier C survivors.

### Stop investing in persona/canary harness for CP-tier work
The MEMORY notes already flag this: persona-harness inflates success via user-rescue loops; T4 canary is too stochastic for triple-perfect criteria. CP canonicals validate via deterministic build + form-gate + function-gate. That trinity *is* the production measurement. Persona/canary is useful for **off-template** prompts (T5 novel) where there is no canonical to instantiate against — but for T1 hard-instantiate, the harness is just noise. Move the persona-canary maintenance budget to function-gate sweeps and direct_eval honesty audits.

### Stop adding new pattern_hints without IR foundation
Currently every new "category" of scenario implicitly extends the canonical retriever's prompt-matching surface. Until the multimodal IR ships with a proper `PatternHint` closed enum (M3 above), adding `pattern_hint = "navigate"` or `pattern_hint = "weld"` informally just creates more retrieval drift. Hold the line: build canonicals in `pick_place | sort | reorient` (the proven enum) until M3 lands, then formally extend the enum once.

### Stop optimizing template ranking before fixing function-gate gaps
Template ranking improvements (gte-large, e5-mistral, structural re-rank) are seductive — every iteration makes T1 retrieval slightly better on adversarial prompts. But the bottleneck is **function-gate coverage**, not retrieval quality. A canonical that retrieves perfectly but doesn't actually deliver the cube is worse than one that retrieves at margin 0.21 and does. M1 (function-gate everything) before any further retrieval optimization. The all-MiniLM-L6-v2 + goal+thoughts+tools_used embed is good enough for now.

### Stop chasing 100% function-gate on stochastic canonicals
CP-09, CP-12, CP-14, CP-76, CP-77 fail function-gate stochastically (cuRobo seed × narrow placement margin × dual-robot timing × sub-cm precision). The right answer is **probe-mark + N-of-M acceptance** in the success criteria, not endless tuning. The MEMORY note on T4 canary stochasticity applies here: 5-of-7 acceptance is the realistic measurement; 3-run triple-perfect is over-spec. Move these to probe status, document the variance, ship the new canonical.

### Stop investing in Phase 9 ROS2 bridge from this branch
Per the Kimate-priorities research, Phase 9 (ROS2 bridge handlers, SLAM, Nav2, cuMotion) is Kimate's axis. The 11 ROS2 tool *schemas* exist in the chat tool surface; the *handlers* are stubs. Filling them in from `feat/live-progress-ui` produces PRs that compete with Kimate's own bot-driven Phase 9 work and may not get merged. Better: when Anton needs a Nav2-specific canonical (M5+), file an issue on k3street:master and let Kimate's bot fill it in, then ship the canonical against the merged handler.

---

## Closing frame

The tool is already most-powerful at one thing: deterministic NL pick-place authoring with dual verification. The next axis of power isn't doubling down on pick-place (that's the saturated direction) — it's extending the substrate (multimodal IR, force-control pattern, navigate pattern) so the same dual-verification trinity covers more of robotics. The 86 CP canonicals are not the product; the substrate that turns them into deterministic outputs is. M1+M2 finishes the pick-place chapter cleanly. M3+M4 opens the next chapter (multimodal). M5 is the trial run for the chapter after that (force-control). Hold the line on anti-investments and the project compounds. Drift on them and the canonical lattice rots while the demo-tape stays text-only.
