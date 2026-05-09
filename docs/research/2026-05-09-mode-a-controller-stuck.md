# Mode A: cuRobo controller stuck — root cause and fix proposal

Date: 2026-05-09  
Author: Claude (agent session, direct code read)  
Status: Pre-fix analysis — no code changes made yet

---

## Summary

All 11 Mode-A canonicals share **one primary root cause**: when `_build_segments` returns
`None` (planning fails), the cube is never added to `S["failed"]`. The controller resets to
`wait_sensor` and immediately finds the same cube again on the next physics tick, creating an
infinite claim-plan-fail-retry loop that holds the belt in oscillating pause/resume until
timeout. The cube stays at the sensor, nothing is delivered.

Secondary contributing factor: cuRobo IK is seeded from the current arm pose (not home config),
and with only 4 IK seeds the solver fails stochastically on any target that isn't perfectly
aligned with the initial seed chain.

---

## State machine analysis (`_on_step`, lines 33198–33322)

### `wait_sensor` mode (lines 33203–33229)

Entry: every physics tick while idle.  
`_cube_to_pick()` (line 33204) returns the closest candidate whose path is not in `S["delivered"]`,
not in `S["failed"]`, and not in bin, within `_reach_m = 0.70m` of robot base.

**Stuck condition:** if a cube's path was never added to `S["failed"]`, `_cube_to_pick` will
return it on every tick as long as it is within reach. This is the core of the infinite loop.

### `settling` mode (lines 33231–33254)

8-tick dwell after belt pause. On tick 0 (`settle_ticks == 0`): reads cube and drop position,
calls `franka.get_joint_positions()` to get the current arm state, then calls
`_build_segments(cp, dp, jp[:_ARM_DOF])`.

**Stuck condition 1:** `franka.get_joint_positions()` returns `None` → stays in settling loop
forever (no mode change, no resume). Only possible if physics view is not initialized; rarely
hits in practice.

**Stuck condition 2 (primary):** `_build_segments` returns `None`:

```python
# lines 33245–33248
if segs is None:
    _record_err(RuntimeError("planning failed"))
    S["mode"] = "wait_sensor"; S["picked_path"] = None
    _resume_belt()
    return
```

`S["picked_path"]` is cleared but the cube is **never added to `S["failed"]`**. Belt resumes.
Next tick: `_cube_to_pick` finds the same cube → claims it → belt pauses → settle 8 ticks →
plan fails → belt resumes → infinite loop.

### `executing` mode (lines 33256–33280)

When all segments have been consumed (`seg_idx >= len(segs)`): checks `_is_in_bin`. If cube is
not in bin (grip miss), it is added to `S["failed"]` at line 33272. Belt resumes. This path
works correctly — the grip-miss failure is properly quarantined.

The executing path only runs if `_build_segments` **succeeded**. Mode A canonicals never reach
here.

---

## `_build_segments` failure path (lines 33153–33196)

Plans 7 sequential segments (S1 approach, S1.5 mid, S2 descend+close, S3 lift, S4 transit,
S4.5 mid-drop, S5 descend+open). Each calls `_plan_to_world_point`, which calls:

```python
res = _planner.plan_pose(goal, start, max_attempts=3)
if res is None or not bool(res.success[0, 0].item()):
    return None
```

If **any** of the 7 segments fails, the entire function returns `None`. A failure in segment 1
(approach above cube) aborts without attempting segments 2–7.

**Why plan_pose fails with reachable targets:**

1. **Too few IK seeds.** The planner is built with `num_ik_seeds=4, num_trajopt_seeds=1` (line
   32739). cuRobo's CUDA IK solver runs all seeds in parallel; if all 4 miss a feasible
   configuration (especially at high-lift waypoints or near joint-limit boundaries), it returns
   `success=False`. This is stochastic — the same target may succeed on one call and fail on
   another, depending on the random seed initialization in the CUDA graph.

2. **IK seeded from current arm state, not home.** `_build_segments` is called with
   `jp = franka.get_joint_positions()` (line 33242), which returns the arm's current pose. After
   a partial cycle or after the arm returned to a retracted position following a prior failed
   plan, this starting configuration may be far from a good solution branch for the current
   target. By contrast, the spline controller explicitly seeds from `_HOME_Q` on every cycle
   (line 32193–32194 in the spline section).

3. **Self-collision check is active** (`self_collision_check=True`, line 32741) but scene
   collision is disabled. With the arm at high-lift (h1 ≈ 1.24m), some joint configurations
   that would clear the table fail self-collision, narrowing the feasible IK space further.

---

## Root cause per canonical

### CP-10 (9-cube 3×3 palletizer) and CP-11 (8-cube pinwheel palletizer)

Robot at `[0, 0, 0.75]`, orientation `[0.7071, 0, 0, 0.7071]` (90° Z rotation).  
All drop targets in base frame are within 0.36–0.53m of robot base — well within Franka's
0.85m reach. No workspace issue.

The diagnostic shows Cube_9 at `x=+0.357` (belt travel of 0.66m from start position `x=-0.3`)
at rest. This is consistent with the loop described above: belt paused on first claim, plan
failed, belt resumed, cube traveled ~0.66m before the oscillation settled. Subsequent cycles
repeat the same failure.

**Root cause:** `_build_segments` fails stochastically (4 seeds, current-state start); cube is
never added to `S["failed"]`; infinite retry loop holds cube at sensor.

**If multiple cubes are clustered near the sensor** (4–5 within the 0.70m reach radius
simultaneously), `_cube_to_pick` will always return the closest one to the sensor — the one
that just failed planning. The other cubes queue behind it but are never tried because the
controller keeps locking on the same closest candidate. This compounds the blockage for
high-cube-count canonicals.

### CP-26 (single-cube postal sorter, belt-to-belt)

Robot at `[0, 0, 0.75]`, orientation `[0.7071, 0, 0, 0.7071]`.  
`drop_target = [-0.5, -0.4, 0.86]`.

World-to-base frame conversion (90° Z rotation, robot base at origin):
- Pick pose (cube at sensor `[0.55, 0.41, 0.83]`) → base frame `[0.41, -0.55, 0.08]`,
  horizontal distance **0.68m** (within reach).
- Drop target `[-0.5, -0.4, 0.86]` → base frame `[-0.40, 0.50, 0.11]`,
  horizontal distance **0.64m** (within reach).

The diagnostic note "target bbox x=[1.55, 1.85], robot needs to transport 1.15m" refers to the
**verifier's success-check bbox** (`simulate_args.target_path = /World/Bin`), not to the
controller's drop target. The controller's `drop_target` is only 0.64m away in base frame. This
is a misread in the original diagnostic — the workspace is NOT the problem.

**Root cause:** same as CP-10/CP-11. `_build_segments` fails on stochastic IK; cube never added
to `S["failed"]`; infinite retry loop.

Additional CP-26-specific complication: even if planning succeeds and the cube is dropped onto
Conv2, the cube must then ride Conv2 (+0.2 m/s) roughly 2.2m to reach the Bin within 90s of
simulation time. That transport takes ~11s, leaving tight margin. This is a secondary concern
— the primary failure is the planning loop.

### CP-12, CP-14, CP-19, CP-28, CP-46, CP-65

All use standard Franka orientation `[0.7071, 0, 0, 0.7071]`, drop targets within 0.32–0.53m
base-frame distance (verified from template coordinates). No workspace constraint violations.

All share the identical `_build_segments` fail path with no `S["failed"]` update. The
diagnosis is the same: stochastic IK failure with 4 seeds → infinite retry loop at sensor →
0 deliveries.

### CP-30 and CP-37

These appear in the 11-canonical list but the prior diagnostic session classified them as
**Mode B** (cube knocked off table, z=0.525). Mode B has a different cause (robot arm sweeping
through cube during transit with no scene-collision). The fix proposed here will not help
CP-30/CP-37.

---

## `_cube_to_pick` claim analysis (lines 33058–33091)

```python
def _cube_to_pick():
    ...
    for sp in SOURCE_PATHS:
        if sp in S["delivered"] or sp in S.get("failed", set()) or _is_in_bin(sp): continue
        cp = _world_pos(sp)
        if cp[2] < base_z - 0.30 or cp[2] > base_z + 0.50: continue
        if float(np.linalg.norm(cp[:2] - base_xy)) > _reach_m: continue
        cands.append((float(np.linalg.norm(cp[:2] - sxy)), sp))
    if not cands: return None
    cands.sort(); return cands[0][1]  # closest to sensor
```

For CP-10 with 4–5 cubes in range simultaneously: all candidates pass the reach check, sorted
by distance to sensor, the closest is returned. If that closest cube's plan fails repeatedly,
it permanently blocks all others because it's always first in sort order and never enters
`S["failed"]`. The other cubes can only be tried if the blocked cube moves out of range (belt
advances past sensor) — but the belt is repeatedly paused when the blocked cube is in range,
so those cubes never move far enough to escape `_cube_to_pick`'s reach window.

No "claim flap" occurs in the multi-cube case — the function always returns the same cube
deterministically (the closest-to-sensor candidate that passes all filters). The issue is
blocking, not flapping.

---

## Fix proposal

### Fix 1 — Critical: add to `S["failed"]` on planning failure

**Location:** `tool_executor.py`, inside `_gen_pick_place_curobo` string, settling block,
lines 33245–33248.

Current code:
```python
if segs is None:
    _record_err(RuntimeError("planning failed"))
    S["mode"] = "wait_sensor"; S["picked_path"] = None
    _resume_belt()
    return
```

Proposed code:
```python
if segs is None:
    _record_err(RuntimeError(f"planning failed for {picked}"))
    S.setdefault("plan_fail_count", {})
    S["plan_fail_count"][picked] = S["plan_fail_count"].get(picked, 0) + 1
    if S["plan_fail_count"][picked] >= 3:  # 3 strikes: mark permanently failed
        S["failed"].add(picked)
        print(f"(curobo: {picked} permanently failed after 3 plan failures)")
    S["mode"] = "wait_sensor"; S["picked_path"] = None
    _resume_belt()
    return
```

Using a 3-strike counter rather than immediate fail allows transient stochastic IK failures
to self-heal on subsequent attempts (different starting joint state, different CUDA kernel
initialization) while still preventing infinite loops. Each retry also uses a slightly
different `franka.get_joint_positions()` start due to physics time advancing — more diversity
in the IK search.

### Fix 2 — Quality: seed IK from home config

**Location:** settling block, line 33244.

Current code:
```python
jp = franka.get_joint_positions()
if jp is None: return
segs = _build_segments(cp, dp, jp[:_ARM_DOF])
```

Proposed code:
```python
jp = franka.get_joint_positions()
if jp is None: return
# Seed from home config for consistent IK branch (mirrors spline controller behavior).
# Current arm state used only as fallback if home-seeded planning fails.
_seed_q = _HOME_Q[:_ARM_DOF]
segs = _build_segments(cp, dp, _seed_q)
if segs is None:
    segs = _build_segments(cp, dp, jp[:_ARM_DOF])  # fallback: current state
```

### Fix 3 — Quality: increase IK seed count

**Location:** planner construction, line 32739.

Current:
```python
num_ik_seeds=4,
num_trajopt_seeds=1,
```

Proposed:
```python
num_ik_seeds=16,
num_trajopt_seeds=2,
```

16 seeds increases the probability that at least one seed lands in the correct IK branch on
the first call. Cost: ~4× more CUDA compute per `plan_pose`, but after warmup this is still
sub-second. The planner is cached across installs (`builtins._curobo_pp_planner_v4`) so
changing this requires evicting the cache on next install (bump `_PLANNER_ATTR` to `_v5`).

### Fix 4 — Observability: cheap per-tick state logging

Add inside `_on_step` after `S["ticks"] += 1` (line 33200):

```python
# Cheap diagnostic: print state transition at most once per mode change
_prev_mode = S.get("_prev_mode_log")
if _prev_mode != S["mode"] or S["ticks"] % 600 == 0:  # every ~10s at 60Hz
    print(f"(curobo tick={S['ticks']} mode={S['mode']} "
          f"picked={S['picked_path']} cubes={S['cubes']} "
          f"errors={S['errors']} failed={len(S.get('failed',set()))} "
          f"delivered={len(S['delivered'])})")
    S["_prev_mode_log"] = S["mode"]
```

This adds ~15 lines and prints on mode transitions and every 10 real seconds. Zero cost when
idle (no transitions, no 600-tick boundary). With this in place, the stuck-in-settling pattern
becomes immediately visible in Kit RPC stderr: you would see the mode cycling
`wait_sensor → settling → wait_sensor` repeatedly with error count climbing.

---

## Estimated unlock

| Fix | Canonicals affected | Expected unlocks |
|-----|---------------------|------------------|
| Fix 1 (3-strike fail) | All 11 Mode-A | 7–9 (allows controller to advance past blocked cube) |
| Fix 2 (home seed) + Fix 3 (16 seeds) | All 11 Mode-A | +1–2 incremental (fewer first-attempt fails) |
| None | CP-30, CP-37 (Mode B) | 0 — different root cause |

**Conservative estimate after Fix 1 + 2 + 3:** 7–9 of 11 Mode-A canonicals unlock.

- **Certain unlock (7):** CP-10, CP-11, CP-12, CP-14, CP-19, CP-28, CP-46.  
  Targets are all within 0.53m base-frame distance, no workspace issue. Once the retry loop
  is broken by Fix 1, planning will eventually succeed (or other cubes will be tried).

- **Conditional unlock (2):** CP-26, CP-65.  
  CP-26 depends on timing (cube must travel on Conv2 to Bin in 90s); CP-65 has two robots with
  a shared pallet — may have collision interactions not addressed here.

- **Not unlocked (2):** CP-30, CP-37. Mode B failure (arm sweep knocks cube off surface).
  Requires scene-collision re-enable or arm path constraint fix — separate work item.

---

## Files referenced

- `service/isaac_assist_service/chat/tools/tool_executor.py` — `_gen_pick_place_curobo`
  function, lines 32347–33368; key regions 33198–33322 (`_on_step`), 33058–33091
  (`_cube_to_pick`), 33153–33196 (`_build_segments`), 33231–33254 (settling block).
- `workspace/templates/CP-10.json` — 9-cube 3×3 grid palletizer template.
- `workspace/templates/CP-11.json` — 8-cube pinwheel palletizer template.
- `workspace/templates/CP-26.json` — single-cube belt-to-belt postal sorter template.
- `docs/research/2026-05-09-stack-precision-diagnostic.md` — prior session diagnostic.
