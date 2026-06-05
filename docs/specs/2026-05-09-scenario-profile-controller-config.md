# Scenario-Profile-Based Controller Configuration

**Date:** 2026-05-09
**Status:** spec, deferred
**Origin:** function-gate session 2026-05-09 — sensor-gate, scene-collision, belt-pause and FJ-tolerance tuning all needed PER-CANONICAL adjustments. One global config either underfits high-speed cases (CP-22) or overfits multi-robot relays (CP-59/65). Each "fix" causes regressions in some other scenario.

## Problem

`_gen_pick_place_curobo`, `_gen_pick_place_builtin`, and `_gen_pick_place_spline` each accept a flat parameter set and apply uniform logic. But canonicals span at least five distinct flavors with conflicting needs:

- **single-belt-pick** (CP-01-style): cube enters sensor zone, claim, pick, place. Standard sensor radius (3× bbox) works.
- **high-speed-belt** (CP-22, 0.5 m/s nominal): cube transits sensor zone in 2-3 ticks. Needs upstream lookahead claim + wider settle ticks.
- **multi-robot-relay** (CP-51, CP-53, CP-65, CP-68, CP-76): robot B picks from handoff/staging position, NOT from its own sensor zone. Needs sensor-gate disabled or widened. Plus mutex coordination.
- **obstacle-rich** (CP-37 pillar, CP-46 packed palletizer): planning needs scene-collision AROUND obstacle while EXCLUDING scene-floor (Table/Belt/Bin) the robot expects to touch.
- **heap** (CP-57, CP-59): cubes randomly distributed in radius around heap center; sensor centered on heap; gate = heap_radius + margin.

A single sensor-gate radius can't satisfy all five. v13 set radius * 3 — fixed CP-37 (Mode A spiral) but broke CP-22 (high-speed transit) and CP-59/65 (multi-robot relays where cube isn't near sensor at install time).

## Approach: Rule-based scenario profiler at install time

`setup_pick_place_controller` would inspect the scene + its own args, classify into a *scenario profile*, and load matching controller config. No LLM call — the features are all observable at install time and the profile branches are few.

### Scenario profile features (extracted at install time)

```
profile = {
    "belt_speed":         abs(belt.physxSurfaceVelocity:surfaceVelocity[0]),  # 0 if no belt
    "belt_path":          BELT_PATH or None,
    "n_cubes":            len(SOURCE_PATHS),
    "cube_initial_xy":    [(world_xy(c)) for c in SOURCE_PATHS],
    "sensor_xy":          world_xy(SENSOR_PATH) or None,
    "robot_count":        count of Franka*/UR10*/etc prims with controllers installed,
    "has_mutex":          MUTEX_PATH is not None,
    "obstacles":          PLANNING_OBSTACLES (excluding scene-floor names),
    "destination_kind":   "bin" if Cube prim with collision, "marker" if Xform, "stack" if BaseCube/HoldPedestal,
}
```

### Profile branches → controller config

```python
def select_profile(p):
    if p["robot_count"] >= 2 and p["has_mutex"]:
        return "multi_robot_relay"
    if p["n_cubes"] > 3 and max_cube_spread_xy(p) < 0.30:
        return "heap"
    if p["belt_speed"] > 0.25:
        return "high_speed_belt"
    if any(real_obstacle(o) for o in p["obstacles"]):
        return "obstacle_rich"
    return "single_belt_pick"

PROFILE_CONFIGS = {
    "single_belt_pick": {
        "sensor_gate_factor": 3.0,         # 3 × sensor_radius
        "settle_ticks": 8,
        "scene_collision": False,
        "lookahead_x": 0.0,
    },
    "high_speed_belt": {
        "sensor_gate_factor": 5.0,         # generous catch
        "settle_ticks": 16,
        "scene_collision": False,
        "lookahead_x": 0.30,
    },
    "multi_robot_relay": {
        "sensor_gate_factor": 8.0,         # robot B sees cube far from its sensor
        "settle_ticks": 8,
        "scene_collision": False,
        "lookahead_x": 0.0,
        "mutex_required": True,
    },
    "obstacle_rich": {
        "sensor_gate_factor": 3.0,
        "settle_ticks": 8,
        "scene_collision": True,           # update_world per plan
        "scene_exclude_floor": True,       # skip Table/Belt/Bin
        "lookahead_x": 0.0,
    },
    "heap": {
        "sensor_gate_factor": 1.0,         # gate = heap_radius
        "sensor_anchor": "heap_centroid",  # sensor not always at heap
        "settle_ticks": 8,
        "scene_collision": False,
    },
}
```

### Where to apply

Inside the f-string template emitted by `_gen_pick_place_curobo` (and similar), replace hardcoded constants like `_sensor_radius * 3.0` with `_PROFILE["sensor_gate_factor"] * _sensor_radius`. The PROFILE dict is computed at install time (Python side, before f-string emit) and embedded into the template as a literal.

## Why a future-task, not now

Refactoring all three handlers to load PROFILE-keyed configs is ~200 lines of targeted but invasive edits. Risk of further regressions while migrating. Better to:

1. Finish current session in v10 baseline (51 ✓) plus the diagnostic counters (`ctrl:plan_calls`, `ctrl:plan_fails`, `ctrl:last_fail_goal`).
2. Lock the current state.
3. Spec → implement scenario-profile branch as a separate work item with its own diff.
4. Validate one profile at a time (e.g. "obstacle_rich" alone → unlock CP-37/35/46/48 without touching CP-22/59/65 settings).

## Validation criteria

A successful migration would:
- Keep existing ✓ count (CP-01-04, CP-08, CP-10-13, CP-22, CP-59, CP-65, etc).
- Promote at least 4 of the current floor-drops to ✓ via "obstacle_rich" profile.
- Promote at least 3 of the current above-floor partials to ✓ via "multi_robot_relay" profile.
- Each profile has a DEDICATED test canonical exercising it.

## Notes

- The existing `setup_pick_place_controller` accepts kwargs: those should not change. Profile is INTERNAL; user-facing args stay the same. Profile selector reads them + scene to pick branch.
- Don't use an LLM for selection — features are deterministic and finite. Regex/dict lookup is sufficient.
- IF a 6th profile emerges (e.g. CP-67 rotary disc transport requires its own behavior), branch tree extends; controller-shape stays.
- Diagnostic counters (`ctrl:plan_calls`, `ctrl:plan_fails`, `ctrl:last_fail_goal`) added 2026-05-09 are foundational for verifying profile changes during migration. Keep them.

---

## Opus review (2026-05-09)

**Verdict.** Spec fits the existing harness philosophy (rule-based discretization of an LLM decision; aligns with P1/P2 in `2026-05-08-multimodal-foundation-spec.md`) and resolves a real, repeatedly-observed regression class. Punch list below is mostly missing branches and verification scaffolding, not architectural conflicts. Ship after the listed additions; do NOT ship as-is — branch set is incomplete and a couple of profile parameters don't actually exist as knobs in the curobo handler yet.

### Concrete issues

1. **CP-67 rotary-table is unrepresented.** Notes line 114 acknowledges this but doesn't add a branch. CP-67 is `multi_robot + mutex + rotary` — `select_profile` would route it to `multi_robot_relay`, which sets `sensor_gate_factor=8.0` and ignores the rotary disc dynamics. Add `rotary_handoff` branch (or feature flag `dest_is_rotating: true`).

2. **CP-58 peg-in-hole is unrepresented.** It's an obstacle-rich + assembly-constraint task; would route to `obstacle_rich`, but it needs `setup_assembly_constraint` semantics, not just `update_world`. Add `assembly_insertion` or explicitly out-of-scope it.

3. **CP-05 reorient is unrepresented.** Profile selector has nothing for `require_upright=True` — already a kwarg threaded through `_gen_pick_place_curobo` (tool_executor.py:28829). Currently CP-05 would fall to `single_belt_pick`. Either add `reorient` branch or document REORIENT remains separate.

4. **`belt_speed` feature reads only `surfaceVelocity[0]`.** cuRobo handler at tool_executor.py:33285 reads same attr, so the read is correct, but CP-23 has the robot rotated 180° — belt may move on −x. Use `np.linalg.norm` of the [0:2] components, not just `[0]`.

5. **`lookahead_x` is the only `high_speed_belt`-specific knob today.** tool_executor.py:33290 already does `_look_ahead_x = 0.30 if _belt_v > 0.25 else 0.0` — already a hardcoded 2-branch scenario classifier. Spec should explicitly note it is REPLACING this inline classifier, not adding a parallel one.

6. **`scene_collision: True` and `scene_exclude_floor: True` are not arguments to `_gen_pick_place_curobo` today.** Lines 28814-28832 show the actual signature. Spec needs to add these as new kwargs OR explicitly say "new code path inside the f-string emit." As written, "replace hardcoded constants" understates the change.

7. **Diagnostic counter scope is curobo-only.** `ctrl:plan_calls`/`plan_fails`/`last_fail_goal` defined and incremented only inside `_gen_pick_place_curobo`. Builtin handler has its own `builtin_pp:phase`/`builtin_pp:tick_count` but no plan-fail counter. Spec's validation-criteria assume per-profile pass/fail measurement, but builtin path can't expose a `plan_fails` signal — needs either builtin equivalent or scope clarification.

8. **`heap` profile feature `max_cube_spread_xy < 0.30` is undefined.** No such helper exists. Define `max_cube_spread_xy(p) = max pairwise xy distance among cube_initial_xy`.

9. **Branch precedence ambiguity.** CP-22 hybrid (obstacle + high-speed) would be classified `obstacle_rich`, losing lookahead. Document precedence intent (specificity over generality) explicitly.

10. **Coordination doc conflict.** Block 1B (multimodal session) plans to refactor CP-01..CP-05 to role-based templates. Profile inference reads `BELT_PATH`, `SOURCE_PATHS`, `SENSOR_PATH` literals — those become `{{role.path}}` placeholders post-refactor. Spec must commit to running BEFORE Block 1B, or specify it post-substitute.

### Concrete additions wanted before implementation

1. **Per-profile fixture canonical map** — explicit `{profile → CP-NN}` table (e.g. `single_belt_pick: CP-01`, `high_speed_belt: CP-22`, `multi_robot_relay: CP-65`, `obstacle_rich: CP-37`, `heap: CP-57`).

2. **A `ctrl:profile` attribute** written at install. Lets `simulate_traversal_check` / function_gate_suite report which profile was chosen, distinct from why-it-failed.

3. **Builtin-handler scope decision**: either add `plan_calls`-equivalent to builtin, or restrict spec to cuRobo + spline only. Currently silently leaves CP-69-86 builtin scenarios untreated.

4. **No-regression baseline file** (`workspace/baselines/pre-profile-rollout/`): cube_final + ctrl:* state for the currently-✓ canonicals before profile rollout.

5. **Selector unit tests** — `tests/test_scenario_profile.py` taking synthetic `profile` dicts → asserting branch. Cheaper than full canonical reruns and catches branch-precedence bugs early.

6. **Explicit non-goals**: Cortex (CP-49/52/61/72/73), peg-in-hole (CP-58), drawer (CP-55), reorient (CP-05) — call them out as future profiles, not today's scope.
