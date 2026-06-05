# `diagnose_scene_feasibility` — Pre-Flight Constraint Validator

**Date:** 2026-05-09
**Status:** spec, deferred
**Origin:** function-gate session 2026-05-09. Multiple canonicals fail simulate_traversal_check for reasons unrelated to controller logic — drop_target inside an obstacle bbox, pick pose at 95%+ reach causing IK-edge planning failures, transit corridor blocked. These are *scene* problems, not *controller* problems. We need to detect them BEFORE running simulation. Pattern recognized from `robotics_lab` (factory-layout-generator with WFC contradiction-detection + ABOM Constraint/Violation schema + NSGA-II pareto-front objectives).

## Problem

Today the harness runs `simulate_traversal_check` on every canonical. Each run is 60-180s of GPU sim. Many failures are scene-side and could be detected in <2 seconds at install time:

- Goal pose has no IK solution (out of workspace)
- Goal IK config is in self-collision or scene-collision
- Drop_target inside an obstacle bounding box
- Pick pose ≥ 95% of robot's max reach (edge-of-workspace, IK fragile)
- Transit corridor obstructed by registered planning_obstacle
- Cube at install time is outside sensor zone (controller never claims it)

These are *scene-design* mistakes. The controller can't recover from them. Running 60s sims to discover them wastes lab time AND obscures the actual controller bugs we DO want to fix.

In production (Gemini agent generates novel scenes from natural-language prompts), the same pre-flight check protects against agent-induced scene errors before expensive simulation.

## Approach

Add a new MCP tool `diagnose_scene_feasibility(args)` that runs deterministic geometric checks on a built scene and returns a structured constraint-check report. Mirror the schema from `lib/constraint_handler.py` in robotics_lab (`Constraint` / `Violation` / `severity` enum) — proven shape, doesn't reinvent.

### Tool signature

```python
async def diagnose_scene_feasibility(args: Dict) -> Dict:
    """
    Args:
      robot_path: USD path of the robot prim
      pick_pose: world [x, y, z] OR cube_paths (auto-pick first reachable cube)
      drop_pose: world [x, y, z] OR destination_path (use bbox center)
      obstacles: list of USD prim paths for collision context (optional)
      ee_offset: tool-tip offset relative to ee_link (optional)

    Returns: see Output Schema below.
    """
```

### Metrics computed (per pose: pick + drop)

| Metric | How | Failure threshold |
|---|---|---|
| `ik_feasible` | Call `solve_ik(robot, pose)` — succeeds or not | `false` → ERROR |
| `collision_distance` | At IK solution config: distance from robot spheres to nearest obstacle | `< 0` → CRITICAL (in collision); `< 0.005m` → ERROR |
| `manipulability` | `\|J·J^T\|^0.5` at IK config | `< 0.05` → WARNING (singular) |
| `reach_utilization` | `\|pose - robot_base\| / max_reach` | `> 0.95` → WARNING; `> 1.0` → CRITICAL |
| `inside_obstacle_bbox` | Test `pose ∈ obstacle.bbox` for each registered obstacle | `true` → CRITICAL |

### Path-clearance metric (transit corridor)

Sample N=20 points along straight-line interpolation from pick_pose's IK config to drop_pose's IK config in joint space. For each, run `check_collisions` against scene. Report:

| Metric | Computed | Failure threshold |
|---|---|---|
| `clearance_pct` | (#non-colliding samples) / N × 100 | `< 60%` → ERROR; `< 90%` → WARNING |

(This is approximate — real planner can find non-straight paths — but cheap and a reasonable lower-bound. WFC-style: if straight-line is clear, the planner almost certainly finds a path; if straight-line is blocked, the planner *may* still find one but failure-rate climbs.)

### Sensor-zone metric (only if SENSOR_PATH set)

| Metric | Computed | Failure threshold |
|---|---|---|
| `cube_in_sensor_zone_at_settle` | for each cube: `\|cube_xy - sensor_xy\| <= sensor_radius * K` after settle ticks accounting for belt motion | `false` for ALL cubes → ERROR (controller will never claim) |

### Output schema

```json
{
  "verdict": "feasible | tightly_feasible | overconstrained | infeasible",
  "metrics": { …all metrics computed… },
  "violations": [
    {
      "axis": "reach_utilization",
      "severity": "WARNING|ERROR|CRITICAL",
      "value": 0.94,
      "threshold": 0.95,
      "message": "Pick pose at 94% of robot max reach; IK may be fragile near edge"
    }
  ],
  "alternatives": [
    {
      "axis": "reach_utilization",
      "suggestion": "Move pick_pose 0.05m closer to robot_base",
      "expected_value": 0.88
    }
  ]
}
```

### Verdict taxonomy

- **`feasible`** — all metrics pass thresholds; expect simulate_traversal_check to succeed barring controller bugs.
- **`tightly_feasible`** — passes CRITICAL + ERROR but has WARNINGs; controller may need tuned params (auto-tune candidate).
- **`overconstrained`** — at least one ERROR (e.g., transit clearance 50%, IK fragile, sensor never sees cube). Plan-rate will be low; canonical author should reposition.
- **`infeasible`** — at least one CRITICAL (pose has no IK, drop inside obstacle, robot starts in collision). Controller will fail 100%; scene must be rewritten.

## Use cases

1. **Canonical authoring/QA** — run on every CP template before commit. Filter `verdict in ("overconstrained", "infeasible")` → reject template; require fix.

2. **Novel-task pre-flight** — Gemini agent calls before `simulate_traversal_check`. If `infeasible`, agent surfaces violations to user with `alternatives[]` ("drop position inside Bin's wall — move 5cm inward?"). Cuts user-time-to-meaningful-feedback from 90s sim to <2s analysis.

3. **Auto-tune trigger** — `tightly_feasible` verdict triggers NSGA-II controller-param sweep (per scenario-profile spec). Skip auto-tune for `feasible` (waste of compute) and `infeasible` (won't help).

4. **Layout-gen integration** (robotics_lab + Isaac Assist coupling, future) — embed feasibility-check as one of the NSGA-II objectives in factory-layout optimization. Layouts that don't admit feasible pick-place are dominated; never reach the pareto-front.

## Implementation reuses existing tools

Already in Isaac Assist:
- `solve_ik` — IK feasibility
- `check_singularity` — manipulability
- `check_path_clearance` — collision sampling
- `raycast` / `overlap_sphere` — point-in-bbox / sphere-in-volume
- `get_bounding_box` — obstacle bbox

The tool is mostly orchestration + scoring rubric over these primitives. ~150-200 LOC.

## Out of scope (for first implementation)

- **Multi-step task feasibility** (relay through handoff station, peg-in-hole insertion). Future profile-aware extension.
- **Time-budget feasibility** (will controller deliver in `duration_s`?). Requires throughput modeling.
- **Force-feedback feasibility** (assembly insertion forces). Requires runtime force gates that don't exist yet.
- **Energy/wear cost** (relevant for industrial deployments, not function-gate).

## Validation criteria

A successful first implementation:
- Catches the 6 failing-canonical types this session uncovered: pillar-at-robot-center (CP-37), drop-target-inside-bbox, cube-out-of-sensor-zone, drop-too-far-for-FrankaB (CP-51), cube-on-belt-already-past-pick-zone, multi-robot relay 2nd-stage cube position outside reach.
- Returns `infeasible` for ≥4 of the above; controller-fix categories continue to use simulate_traversal_check.
- Output schema parsable by both LLM (json) AND human (markdown via `format_diagnose_for_chat()`).
- Run-time per scene: <2s on RTX 5070 (target — IK + collision sampling are sub-second).

## Notes

- Pattern reference: `robotics_lab/lib/constraint_handler.py:90-98` for Constraint/Violation/severity. Reimplement (don't import — robotics_lab is private, separate venv).
- Pattern reference: `robotics_lab/lib/optimization/wfc/engine.py:116-157` for contradiction-detection (BFS propagate). Conceptually our IK-out-of-workspace is the contradiction; we don't need full BFS.
- Diagnostic counters (`ctrl:plan_calls`, `ctrl:plan_fails`) added 2026-05-09 are RUNTIME signals; this tool is INSTALL-TIME. Both stay; they answer different questions.
- Companion to `2026-05-09-scenario-profile-controller-config.md` — feasibility check + profile selector are two halves of "smart pre-flight before sim".

## Opus review (new angles)

The spec is clean on the "what" but silent on a half-dozen interaction surfaces. Concrete additions below; each one is a real seam in the codebase, not a hand-wave.

### A. LayoutSpec validator role (pre-instantiation)

The multimodal-foundation spec (`2026-05-08-multimodal-foundation-spec.md` §3.7) defines six rejection rules for a LayoutSpec but **all six are static schema checks** (enum membership, regex shape, cross-feature consistency). None of them can answer "will Franka actually reach this drop_position?" — that is a geometric query.

`diagnose_scene_feasibility` is the natural Stage-7 LayoutSpec-validator IF it can run on a LayoutSpec instance *without* needing the scene built. Today the spec requires `robot_path` (a USD prim path); on a freshly-emitted LayoutSpec there are no prim paths yet. **Add a second entrypoint: `diagnose_layout_spec(layout_spec) -> Dict`** that materializes a hypothetical scene from the canonical's `code_template` substituted with the LayoutSpec's positions, runs the same checks, then tears down. This closes the gap between `validate_layout_spec.py` (formal) and `simulate_traversal_check` (full sim) — currently a ~90-second cliff.

The ratifier (§5.1) returns `needs_choice` on ambiguity; it has no way to return `geometrically_unbindable`. The current binding-stage `constraint_fail` (§5.4) is documented but no implementation exists. `diagnose_layout_spec` is the implementation: ratify proposes bindings → diagnose validates them → if `infeasible`, ratifier promotes the failure to `constraint_fail` with the violation list as the diagnostic.

### B. Differential / regression role across deploys

The spec frames diagnose as install-time and use-cases as authoring/agent-pre-flight (§Use cases 1-4). Missing: **drift detection across the canonical lifecycle.**

Each canonical has a v-bumped definition. When `setup_pick_place_controller` adds a kwarg, when `robot_wizard` updates Franka's URDF, when `create_bin` changes default dimensions, a previously-feasible scene can silently become tightly_feasible or overconstrained. We do not detect this until the next `function_gate_suite` run flips an ✓ to ✗ — and even then we struggle to distinguish "controller bug" from "scene drifted past threshold."

Concrete addition: **add `scripts/qa/feasibility_baseline.py`** that runs diagnose on every CP-NN canonical and persists `workspace/baselines/feasibility/{cp_id}.json` with metrics + verdict. CI compares current diagnose output against baseline; surfaces `delta(reach_utilization) > 0.02` or verdict downgrade as regression. This is cheap (<2s × 86 canonicals = ~3 min) and catches the kind of slow-bleed regressions the no-regression baseline file in the companion spec (`2026-05-09-scenario-profile-controller-config.md` Opus review item 4) acknowledges but does not address geometrically.

### C. Caching by scene-graph hash — invalidation rules

The spec says <2s per call but never asks whether re-runs on the same scene are needed. `function_gate_consistency.py` runs `function_gate_suite` 3-5 times per session for the same canonicals. `simulate_traversal_check` is non-deterministic in physics outcome but `diagnose_scene_feasibility` is **deterministic geometric math** — same scene graph → same output (assuming the determinism fixes from §D below). Cache hit ratio should be ~80% in CI.

Cache key: hash of `(robot_path_prim_geom, robot_world_xform, all obstacles' bbox + xform, pick_pose, drop_pose, ee_offset, sensor_xform_if_set)`. Invalidate on:
- Stage close / `open_stage` call (orchestrator-side hook)
- Any tool in `MUTATE_GEOMETRY_TOOLS` set (translate, set_attribute on xformOp:*, apply_api_schema with PhysicsCollisionAPI, etc.)
- TTL fallback: 60s (catches USD-layer-edit cases where mutation tracking is incomplete)

Cache lives in `service/isaac_assist_service/cache/feasibility_cache.py`, in-memory dict keyed by hash, sweep on `process_exit`. Skip persistence — Kit RPC is single-tenant per `feedback_isaac_assist_kit_concurrency`, no cross-process replay value.

### D. Determinism — IK seed, sample seed

Current spec does not address determinism. `solve_ik` in cuRobo uses a random seed for batch IK; `check_path_clearance` samples N=20 points along a straight line (deterministic ordering, but joint-space interpolation has multi-IK ambiguity at branch points). Without `seed` parameter, two consecutive diagnose calls on the same scene can produce different `manipulability` (different IK config selected), different `clearance_pct` (different sample-cube collision near edge), and different `verdict` near the WARNING/ERROR boundary.

**Add `seed: int` arg (default 42)** that propagates to `solve_ik(seed=)` and the path-clearance sampler. Also: **return `seed_used` in the output** so consumers can replay. This is what makes the differential role (§B) actually work — a flake in IK seed should not register as feasibility drift.

### E. Multi-robot scope — explicit per-robot reports

Spec is silent on CP-51/53/65/68/76 (multi-robot relays). The signature accepts a single `robot_path` and a single pick/drop pair. Multi-robot canonicals have N pick-place cycles where each cycle has its own (robot, pick, drop). Three options:

1. **Per-cycle calls.** Caller invokes diagnose once per (robot, pick, drop) tuple. Spec extends `Use cases #1` to instruct CP authors to call N times. Aggregator function `diagnose_multi_robot(cycles: List) -> {per_cycle: [...], aggregate: {worst_severity, worst_axis, mutex_conflicts: []}}`.
2. **One call with cycles list.** `diagnose_scene_feasibility(args)` accepts `cycles: [{robot, pick, drop}, ...]`. Internally loops, returns per-cycle and aggregate. Same compute cost. Better ergonomics for canonical authors who are already authoring `code_template` with N cycles.
3. **Add mutex-conflict check** as a multi-robot-only metric: if cycle A's transit corridor bbox intersects cycle B's transit corridor bbox AND `has_mutex == false` in the scenario profile → severity ERROR. CP-65 fails today partly because the two robots' workspaces overlap and no mutex was declared.

Option 2 is recommended; option 3 is the addition that justifies it.

### F. Verifier-tool short-circuit integration

`verify_pickplace_pipeline` (form-gate, `tool_executor.py:3527`) currently runs reach + bridge + controller checks but does NOT check IK-feasibility-at-pose or in-bbox-obstacle. Today an authored template with a typoed drop_target inside a wall passes form-gate (its `pipeline_ok=true`) and then fails simulate-traversal 60s later with `cube_final ≈ drop_target_pre_collision`.

**Add `--feasibility` flag to `verify_pickplace_pipeline` that delegates to diagnose.** When set, verify's stage list each runs through diagnose; verdicts `infeasible` / `overconstrained` propagate as form-gate `issues[]`. Default off (preserves current contract). Canonical_instantiator turns it on for hard-instantiate (preempts 60s wasted sim on a malformed scene). Cost: +2s × N stages on hard-instantiate path; net win because typical hard-instantiate failure today burns 60s.

This is the cleanest place to mechanically prevent the "drop_target inside obstacle" CP-author bug class — it converts a runtime failure into a build-time failure.

### G. Plain-language summary — Swedish + English

`format_diagnose_for_chat()` is referenced in §Validation criteria but unspecified. Spec the shape now to avoid the "and-now-the-LLM-writes-it" anti-pattern (which by `feedback_diligence_no_false_positives` we should avoid).

```python
def format_for_user(report: Dict, lang: str = "sv") -> str:
    """Returns 1-3 line summary for chat reply.
    Uses canonical violation message templates, never LLM-paraphrased."""
```

Templates (Swedish, since user prefers it per memory):
- `infeasible / inside_obstacle_bbox` → "Drop-positionen ligger inuti '{path}'. Flytta dropp-punkten {delta_m:.2f} m i +{axis}."
- `overconstrained / clearance_pct < 60%` → "Transitkorridoren är blockerad ({clearance_pct:.0f}% fri). Robot kommer att stoppa mid-trajectory."
- `tightly_feasible / reach_utilization > 0.95` → "Pick-pose är nära robotens räckvidd ({reach:.0%}). IK kan misslyckas vid edge-cases."

English fallback for non-Swedish sessions. **Templates live in `service/.../diagnose/messages.py`; LLM never paraphrases.** This is the multimodal-foundation §1.3 P1 principle applied to the report surface.

### H. Concrete test plan — 10 cases

Spec says "validation criteria" but enumerates 6 scene-types, no unit tests. Required tests:

1. **T-FEAS-1** (synthetic): pick=[0.5, 0, 0.5], drop=[0.4, 0.1, 0.3], no obstacles, Franka at origin → `verdict=feasible`, all metrics pass.
2. **T-FEAS-2** (CP-01 known-good): real scene, expects `feasible`. Regression baseline.
3. **T-OVERC-1**: pick at 96% reach → `verdict=tightly_feasible`, `violations[0].axis=reach_utilization`.
4. **T-INFEAS-1**: drop inside Bin's wall (CP-author bug class) → `verdict=infeasible`, `violations[0].axis=inside_obstacle_bbox`.
5. **T-INFEAS-2**: pick at 1.05× reach → `verdict=infeasible`, `violations[0].severity=CRITICAL`.
6. **T-PATH-1**: pillar between pick and drop, straight-line blocked → `clearance_pct < 60%` → ERROR.
7. **T-PATH-2**: pillar offset, straight-line clear → `clearance_pct = 100%` → no violation.
8. **T-DETERM-1**: same scene + same `seed` → byte-identical metrics dict on two calls.
9. **T-CACHE-1**: same scene called twice within 60s → second call <0.1s (cache hit); after `set_attribute` on robot xform → cache invalidated, full recompute.
10. **T-MULTI-1**: CP-65 cycles list, robot A reaches, robot B drop outside reach → `aggregate.worst_severity=CRITICAL`, `per_cycle[1].verdict=infeasible`.

Tests live in `tests/test_diagnose_scene_feasibility.py`. Synthetic scenes (T-FEAS-1, T-OVERC-1, T-INFEAS-2, T-DETERM-1, T-CACHE-1) run unit-test fast (<5s total via mock-Kit-RPC fixtures from `tests/conftest.py`). Real-scene tests gated by `RUN_KIT_TESTS=1`.

### I. auto_judge integration

`scripts/qa/auto_judge.py:106-151` heuristic verdict scores 5 axes (engagement, tool_execution, expected_tool_overlap, hallucination_flags, response_discipline), max 25. None of them measure "did the agent build a feasible scene." A run where the agent built `infeasible` scene + ran simulate_traversal_check + reported "delivery failed" today scores higher than the same run that NEVER hit simulate (because tool_execution counts simulate_traversal_check as a successful tool run regardless of result).

**Add 6th axis `scene_feasibility` (0-5)** scored from `diagnose_scene_feasibility` output if found in tool_calls log:
- `feasible` → 5
- `tightly_feasible` → 3
- `overconstrained` → 1
- `infeasible` → 0
- not called → 3 (neutral; preserves backward compat)

Max becomes 30; recalibrate baseline thresholds. This rewards agents that pre-flight-check before simulate, penalizes agents that build broken scenes regardless of whether they correctly report breakage.

### J. Compute-budget honesty

Spec claims <2s. Components:
- `solve_ik` × 2 (pick + drop): ~150-300ms each on RTX 5070 cuRobo (warm). Cold first call: 2-5s (cuRobo MotionGen init).
- `check_path_clearance` × 20 samples: ~50-100ms each = 1-2s.
- `get_bounding_box` × N obstacles: ~10ms each.
- Sensor-zone tick simulation: 0 (math only) or ~100ms if requires running settle ticks.

**Worst-case is ~5s on cold start; ~1.5-3s warm.** Either:
1. Document the warm/cold distinction; require pre-warm at service startup (`MotionGen.warmup()` exists in cuRobo, ~3s one-time).
2. Drop path-clearance to N=10 samples for warm <1.5s budget.

The spec also implies "compute reachability" but only for 2 poses. **Be explicit: this tool does NOT compute a reachability map (full workspace voxel sweep is ~30s).** That belongs in a different tool (`compute_reachability_map`) gated behind a feature flag — useful for visualization but not for pre-flight.

### K. Synergy with scenario-profile spec

`2026-05-09-scenario-profile-controller-config.md:99-100` claims auto-tune trigger should fire on `tightly_feasible`. But scenario-profile is install-time selection; diagnose is install-time validation. Sequencing matters:

1. Profile selection (`select_profile(p)` → `profile_name`) must run **before** diagnose, because `multi_robot_relay` profile expects different sensor anchoring (heap_centroid, lookahead_x, etc.) and diagnose's sensor-zone metric depends on those.
2. Diagnose runs **after** profile selection; verdict feeds back to profile-tuning loop.
3. If diagnose returns `infeasible` for a profile-X scene that should fit profile-Y, the profile selector chose wrong → fall through to next-best profile, re-diagnose. Bounded by ~5 profiles, so worst-case 5 × 2s = 10s.

Add §Notes line: "Profile selector runs before this tool. Diagnose verdict on `infeasible` should trigger profile-fallback before scene-redesign."
