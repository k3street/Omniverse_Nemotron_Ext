# Multi-Robot Canonical Failure Analysis and Fix Plan

Date: 2026-05-09
Canonicals: CP-51, CP-52, CP-53, CP-67, CP-68, CP-76

---

## Executive Summary

Six multi-robot canonicals fail to deliver cubes. Investigation reveals three distinct root-cause
categories, not one. The mutex infrastructure (commit be37d23) is correctly wired in the spline
generator at line 32168-32187 and at the mutex release point (line 32303). However:

1. All six canonicals call `setup_pick_place_controller(target_source="curobo", ...)` — the curobo
   generator at line 32381 receives `mutex_path` via the dispatch at line 28817, but the code
   string it emits sets `MUTEX_PATH = {mutex_path!r}` and then performs **the old hardcoded
   z-filter `[0.83, 0.95]`** in its `_cube_to_pick` (line 32089) rather than the base-relative
   z-window used by the newer curobo template (line 33141). This spline-vs-curobo template split
   means the mutex reads fire on the spline runtime path (lines 32168-32187) but the actual
   multi-robot canonicals run on the curobo path at lines 33124-33157 — where the mutex attr IS
   read (MUTEX_PATH variable is set), but the `_cube_to_pick` used by the curobo path at line
   33124 does NOT check the mutex.

   Wait — re-reading more carefully: the curobo generator at line 32381 emits its own `_on_step`
   logic. Searching shows the curobo `_on_step` does NOT include a mutex wait block (no code like
   `if MUTEX_PATH: ...` before `_cube_to_pick()` in the curobo path at lines 33273 ff). The mutex
   guard at lines 32168-32187 belongs to the **spline** `_on_step` (confirmed by `_a_mode.Set("spline")`
   at line 32100 and `_a_mode.Set("curobo")` at line 33176 — two separate templates).

2. The handoff-signal prim (`handoff:state`, `handoff:current_cube`) is never read by either
   controller. Robot B has no mechanism to wait for robot A to set `handoff:state = "placed"`.

3. The curobo `_is_in_bin` check at cycle-end (line 33346) marks a cube as "failed" (not
   delivered) when the drop target is a non-bin prim (Handoff marker, StagingRack, HoldPedestal)
   because those prims have a degenerate world bbox — the cube never registers as "in bin" and
   gets stuck in the `failed` set, so the robot never tries to pick again.

---

## Root Causes By Category

### Category A — Mutex missing from curobo `_on_step`

The mutex wait block (lines 32168-32187) lives inside the **spline** generator's `_on_step`. The
curobo generator (`_gen_pick_place_curobo`, line 32381) emits its own `_on_step` at approximately
line 33268. The curobo `_on_step` starts with:

```python
if S["mode"] == "wait_sensor":
    picked = _cube_to_pick()
    if picked:
        ...
        S["mode"] = "settling"
```

There is no `if MUTEX_PATH:` guard before `_cube_to_pick()`. Both robots reach `wait_sensor`,
both call `_cube_to_pick()` simultaneously, both find the same cube, and both attempt planning on
the same tick. Because the curobo planner is cached in `builtins._curobo_pp_planner`, two
concurrent planning calls from two `_on_step` callbacks can corrupt the planner state or cause the
second robot to plan with stale kinematics.

Affected: CP-51, CP-52, CP-53, CP-67, CP-68, CP-76 (all use target_source="curobo").

### Category B — Robot B never knows the handoff station is occupied by a cube

CP-51 and CP-68 both depend on robot A dropping Cube_1 at the handoff marker (`/World/Handoff`)
and robot B picking it from there. Robot B's controller is configured with:

```python
sensor_path="/World/SensorB"   # at [0, -0.3, 0.85]
belt_path="/World/ConveyorBelt"  # the original belt, NOT the handoff
source_paths=["/World/Cube_1"]
```

The belt_path causes the controller to set `_belt_sv` pointing to the ConveyorBelt's velocity
attribute. The `_pause_belt()` / `_resume_belt()` functions toggle that attr. Cube_1 starts on the
ConveyorBelt, gets picked by robot A, and is dropped at `[0, -0.3, 0.825]`. At that point Cube_1
is no longer on the ConveyorBelt — it is at the handoff station.

Robot B's `_cube_to_pick()` checks:
- `base_z - 0.30 <= cube_z <= base_z + 0.50` — satisfied (0.825 is within range)
- `|xy_cube - xy_robot_b| <= 0.70m` — robot B is at [0.7, 0, 0.75], cube at [0, -0.3, 0.825];
  distance = sqrt(0.49 + 0.09) = 0.76m — **FAILS reach check**

The handoff position [0, -0.3, 0.825] is 0.76m from robot B base at [0.7, 0, 0.75], exceeding
the hardcoded 0.70m reach limit in `_cube_to_pick`. Robot B never picks the cube.

Additionally, there is no handoff-state gate: robot B does not wait for `handoff:state == "placed"`
before trying to pick. If the reach bug were fixed, both robots could simultaneously try to pick
Cube_1 from the handoff if robot A's trajectory is slow.

### Category C — `_is_in_bin` returns False for non-bin prims, locks cube in `failed` set

The curobo controller's post-cycle check (line 33346) is:

```python
if _is_in_bin(S["picked_path"]):
    S["delivered"].add(S["picked_path"])
else:
    S["failed"].add(S["picked_path"])
```

`_is_in_bin` (line 33167) calls `ComputeWorldBound(...).ComputeAlignedRange()` on `DEST_PATH`.
For a proper bin (a large Cube with collision), this returns a real XY bbox. For the prims used in
multi-robot canonicals, the behavior is:

- `/World/Handoff` (CP-51, CP-68): Xform prim with custom attrs only, no mesh. World bbox is a
  degenerate point at the prim origin. Cube dropped at [0, -0.3, 0.825] is NOT "in bbox" → goes
  to `failed`.
- `/World/StagingRack` (CP-53): Kit tray prim. Its bbox covers the tray surface but may not
  include the cube resting on top (the cube's center is above the tray top face).
- `/World/HoldPedestal` (CP-76): Small cube prim 4cm × 4cm × 5cm. Cube dropped on top has its
  center at z=0.99; pedestal bbox top is at z=0.875. Cube center is above bbox → fails check.
- `/World/RotaryTable` (CP-67): Disc prim; robot B's `DEST_PATH=/World/OutBin` (correct), but
  robot B's `source_paths` refer to the same cube paths and `belt_path="/World/RotaryTable/Disc"`
  (non-standard). If the Disc prim lacks `physxSurfaceVelocityAPI`, `_belt_sv` is None and
  `_pause_belt()` / `_resume_belt()` are no-ops — belt runs unconstrained during pick attempt.

---

## Per-Canonical Root Cause

### CP-51: Producer/Consumer Handoff

Root causes:
1. **Category A**: Curobo `_on_step` has no mutex guard. Both controllers call `_cube_to_pick()`
   simultaneously on tick 1. Cube_1 starts on ConveyorBelt — robot A claims it correctly. But
   robot B also claims it (same cube in source_paths). Both plan concurrently.
2. **Category B**: Handoff position [0, -0.3, 0.825] is 0.76m from FrankaB at [0.7, 0, 0.75] —
   exceeds 0.70m reach check. FrankaB never detects Cube_1 at handoff.
3. **Category C**: Robot A's DEST_PATH is `/World/Handoff` (Xform with no mesh) → bbox is
   degenerate → `_is_in_bin` returns False → robot A's cycle marks Cube_1 as `failed` after
   placing it at handoff. Robot A then has no further cubes to pick. Cube_1 sits at handoff
   permanently.

Fix: see patches below. Estimated unlock: high — fixes A+B+C together should make this work.

### CP-52: Parallel-Picking Duo

Root causes:
1. **Category A**: No mutex guard in curobo `_on_step`. Both robots scan source_paths on the same
   tick. FrankaA's source_paths are [Cube_1, Cube_2] and FrankaB's are [Cube_3, Cube_4] — the
   source_paths split means they won't grab the same cube, so mutex may not be the primary
   failure. However both controllers share the same curobo planner cache
   (`_curobo_pp_planner_<TAG>` uses `_ROBOT_TAG` which differs per robot — confirmed by line 32505
   `_ROBOT_TAG = "{robot_path}".replace("/", "_")`). This means planners are per-robot and don't
   conflict. CP-52 likely has a simpler failure.
2. **Primary issue**: CP-52's controller for FrankaA uses
   `sensor_path="/World/SensorA"` at position [-0.2, 0.4, 0.835]. Cubes Cube_1 and Cube_2 start
   at x=-1.4 and x=-1.15 — far from sensor at x=-0.2. Belt velocity is 0.2 m/s. Time to reach
   sensor: (1.4 - 0.2) / 0.2 = 6s for Cube_1. Robot A should pick Cube_1 fine. FrankaB's sensor
   is at [+0.2, 0.4, 0.835]. Cube_3 and Cube_4 start at x=-0.9 and x=-0.65. They must pass
   sensor A zone first. If FrankaA's belt is paused during Cube_1 pickup, Cube_3 and Cube_4 are
   frozen before reaching SensorB. **Deadlock**: A pauses belt for Cube_1, B waits forever.
3. Root fix: staggered belt-pause: once A has claimed Cube_1 (gripper closing), belt should resume
   so Cube_3/Cube_4 flow to B. Alternative: per-robot belt segments. Simplest fix: after FrankaA
   enters "settling" state, resume belt so FrankaB's cubes continue flowing.

Fix: moderate complexity. Requires belt-coordination logic or per-robot belt segments.

### CP-53: Producer/Consumer Bounded Buffer

Root causes:
1. **Category A**: No mutex guard in curobo `_on_step`. Robot A (producer) and Robot B (consumer)
   both see Cube_1..Cube_3 in source_paths on tick 1. Without mutex, both may claim Cube_1.
2. **Category C**: Robot A's DEST_PATH is `/World/StagingRack`. After placing Cube_1 at slot
   [-0.10, -0.30, 0.825], `_is_in_bin` checks if cube is inside StagingRack's world bbox. The
   tray is 30cm × 10cm; cube center at slot 1 is at x=-0.10, y=-0.30, z=0.825. Tray top surface
   at z=0.81 (position 0.775 + half-height 0.025). Cube center z=0.825 is 1.5cm above tray top.
   World bbox upper z bound is ~0.80. Cube center is above → `_is_in_bin` returns False → marked
   `failed`.
3. Timing: robot B picks before robot A places (both start simultaneously). B's sensor at
   [0, -0.3, 0.85] is at the staging rack position. Cubes start on belt at x=-1.7 to -1.1 — robot
   B's reach is 0.70m from base [0.6, 0, 0.75] = reach to x=[-0.1, 1.3] at y=0. Robot B's cubes
   are on belt at y=0.4; at start, Cube_1 at [-1.7, 0.4, 0.835] is 2.4m from B's base — out of
   reach. B waits. But once A places Cube_1 at staging slot [-0.10, -0.30, 0.825], Cube_1 is
   0.64m from B's base — within reach. B should detect it. But Category C prevents A from
   finishing (marks Cube_1 failed), so A loops without placing more. B waits indefinitely.

Fix: depends on Category C fix. Also needs mutex guard for Category A.

### CP-67: Leader/Follower Rotary Station

Root causes:
1. **Category A**: No mutex in curobo `_on_step`. Both robots share source_paths for Cube_1..4.
   Robot A and B both scan source_paths simultaneously. Race condition on first available cube.
2. **Disc-as-belt**: Robot B's `belt_path="/World/RotaryTable/Disc"`. The curobo generator tries
   to get `physxSurfaceVelocity:surfaceVelocity` on the Disc prim. Rotary table uses a revolute
   joint drive, not a surface velocity API. `_belt_sv` is None. `_pause_belt()` is a no-op.
   The disc keeps rotating during robot B's pick attempt — cube slides away mid-pick.
3. **Disc-as-sensor source**: Robot B's sensor at [0, -0.2, 0.85] (the disc -Y side) waits for a
   cube to arrive via disc rotation. The cube must rotate 180 degrees (180/30 = 6s). After 6s the
   cube arrives at B's side. But robot A's belt pause from its own pick may hold cube on the
   conveyor — the cube never reaches A's drop point, so nothing gets placed on the disc.
4. **Fundamental**: Rotary disc as pick source requires `belt_path` to be a prim with
   `physxSurfaceVelocityAPI` for belt-pause logic. The disc has none. Also, cube physics on a
   rotating disc is friction-driven: if the disc surface lacks sufficient friction, cube stays
   stationary while disc rotates under it.

Fix: significant code change needed. Requires either treating `belt_path=None` gracefully for
robot B (skip pause/resume on disc) or adding a `disc_path` argument. Also a scene redesign
concern — the disc must have friction set for cube transport. Estimated unlock: requires scene
re-design + code fix.

### CP-68: Handoff with Moving Obstacle Awareness

Root causes: Identical to CP-51 (Categories A, B, C). The `register_moving_obstacle` calls only
set a USD attribute (`curobo:moving_obstacles`) on the robot prim — they are metadata-only at
canonical time. The curobo planner does not read this attr per tick (confirmed: `_plan_pick_place`
at line 31932 takes `PLANNING_OBSTACLES` which is a static list set at install time — no runtime
re-query). So CP-68's dynamic obstacle feature is non-functional, but that is declared Sprint 4+
and is not the cause of delivery failure.

The actual delivery failures are identical to CP-51: reach-check rejection of handoff position,
degenerate bbox for Handoff prim, and no mutex in curobo `_on_step`.

Fix: same patches as CP-51.

### CP-76: Dual-Robot Dynamic Fixture Hold

Root causes:
1. **Category C** (primary): Both robots' DEST_PATH is `/World/HoldPedestal`. The pedestal is a
   tiny Cube prim (4cm × 4cm × 5cm) with only `PhysicsCollisionAPI` (no mesh). Its world bbox
   is very small. Robot R1 drops workpiece at [0, 0, 0.92]; pedestal top is at z=0.875. Cube
   center z=0.92 is above pedestal bbox upper bound → `_is_in_bin` returns False → R1 marks
   Cube_workpiece as `failed`. R1 enters an idle state. R2 similarly fails.
2. **No mutex**: Both robots have DEST_PATH = HoldPedestal with no mutex. They can both try to
   plan a drop at [0, 0, 0.92] and [0, 0, 0.99] simultaneously. No coordination.
3. **Fundamental fixture-hold limitation**: There is no mechanism to hold a cube mid-air in Isaac
   Sim without a runtime FixedJoint between the gripper and cube. R1 places cube on the pedestal
   (not holds it mid-air). R2 stacks on top. The "hold" semantic is simulated by the pedestal
   catch — this is a valid simplification for build-spec form. The actual issue is Category C
   preventing delivery.

Fix: Category C fix unlocks both robots. No fundamental scene redesign needed for build-spec form.

---

## Concrete Code Patches

### Patch 1 — Add mutex guard to curobo `_on_step` (fixes Category A)

Location: `_gen_pick_place_curobo` function at line 32381, in the emitted code string. The
`_on_step` for curobo starts at approximately line 33268 (within the template string, not a
Python function at that outer line). The `wait_sensor` branch currently reads:

```python
# CURRENT (around line 33273 in the emitted string):
if S["mode"] == "wait_sensor":
    picked = _cube_to_pick()
    if picked:
        S["picked_path"] = picked
        ...
        S["mode"] = "settling"
    return
```

Add the same mutex guard block that exists in the spline `_on_step` (lines 32168-32187):

```python
# PATCHED:
if S["mode"] == "wait_sensor":
    # Multi-robot mutex: only claim cube if mutex is free or already
    # held by us. If held by another robot, wait this tick.
    if MUTEX_PATH:
        try:
            _mp = stage.GetPrimAtPath(MUTEX_PATH)
            if _mp and _mp.IsValid():
                _claimed = _mp.GetAttribute("mutex:claimed_by").Get() or ""
                if _claimed and _claimed != ROBOT_PATH:
                    return  # other robot holds mutex; wait
        except Exception: pass
    picked = _cube_to_pick()
    if picked:
        # Acquire mutex before claiming cube
        if MUTEX_PATH:
            try:
                _mp = stage.GetPrimAtPath(MUTEX_PATH)
                if _mp and _mp.IsValid():
                    _mp.GetAttribute("mutex:claimed_by").Set(ROBOT_PATH)
                    _cc = _mp.GetAttribute("mutex:claim_count")
                    if _cc and _cc.IsDefined():
                        _cc.Set(int(_cc.Get() or 0) + 1)
            except Exception: pass
        S["picked_path"] = picked
        ...
        S["mode"] = "settling"
    return
```

Also add mutex release in the cycle-end block (after `S["mode"] = "wait_sensor"`):

```python
# After line 33354 (S["picked_path"] = None):
if MUTEX_PATH:
    try:
        _mp = stage.GetPrimAtPath(MUTEX_PATH)
        if _mp and _mp.IsValid():
            _attr = _mp.GetAttribute("mutex:claimed_by")
            if _attr and (_attr.Get() or "") == ROBOT_PATH:
                _attr.Set("")
    except Exception: pass
```

The template variable `MUTEX_PATH` is already injected at line 32481:
`MUTEX_PATH = {mutex_path!r}`. No new variable needed.

But note: setup_pick_place_controller templates for CP-51, CP-52, CP-53, CP-67, CP-68 do NOT pass
`mutex_path` to the controller. The canonical code calls `setup_robot_claim_mutex` but does not
thread the `mutex_path` through `setup_pick_place_controller`. So `MUTEX_PATH` will be `None` in
all existing canonicals. The template code path needs `mutex_path` wired in the template `code`
string. Two sub-patches needed:

**Sub-patch 1a**: Add `mutex_path=` arg to each `setup_pick_place_controller` call in affected
templates:

```python
# CP-51, CP-68 — no mutex prim, but handoff_signal provides coordination.
# CP-52 — add mutex_path="/World/PickMutex" to BOTH controller installs:
setup_pick_place_controller(
    robot_path="/World/FrankaA",
    ...
    mutex_path="/World/PickMutex",   # <-- add
)
setup_pick_place_controller(
    robot_path="/World/FrankaB",
    ...
    mutex_path="/World/PickMutex",   # <-- add
)

# CP-53 — add mutex_path="/World/RackMutex" to BOTH:
setup_pick_place_controller(robot_path="/World/FrankaA", ..., mutex_path="/World/RackMutex")
setup_pick_place_controller(robot_path="/World/FrankaB", ..., mutex_path="/World/RackMutex")

# CP-67 — add mutex_path="/World/TableMutex" to BOTH:
setup_pick_place_controller(robot_path="/World/FrankaA", ..., mutex_path="/World/TableMutex")
setup_pick_place_controller(robot_path="/World/FrankaB", ..., mutex_path="/World/TableMutex")
```

### Patch 2 — Fix `_is_in_bin` for non-bin destination prims (fixes Category C)

Location: inside the curobo emitted code template, the `_is_in_bin` function (around where
`_a_mode.Set("curobo")` appears at line 33176, just after). Currently:

```python
def _is_in_bin(cube_path):
    b = _bin_bounds()
    if b is None: return False
    cp = _world_pos(cube_path)
    if cp is None: return False
    mn, mx = b
    return (mn[0] <= cp[0] <= mx[0]) and (mn[1] <= cp[1] <= mx[1])
```

The problem: `_bin_bounds()` uses `ComputeWorldBound().ComputeAlignedRange()`, which returns a
degenerate range for Xform-only prims (Handoff, Mutex marker), and a too-small range for small
physical prims (HoldPedestal). Replace with a proximity check when bbox is degenerate:

```python
def _is_near_dest(cube_path, tolerance=0.15):
    """True if cube is within tolerance meters of DROP_TARGET or DEST_PATH center."""
    cp = _world_pos(cube_path)
    if cp is None: return False
    # Try DROP_TARGET first (explicit coordinate)
    if DROP_TARGET is not None:
        dt = np.array(DROP_TARGET, dtype=np.float64)
        if float(np.linalg.norm(cp - dt)) < tolerance:
            return True
    # Try DEST_PATH center via bbox midpoint
    if DEST_PATH:
        p = stage.GetPrimAtPath(DEST_PATH)
        if p and p.IsValid():
            try:
                bb = UsdGeom.Imageable(p).ComputeWorldBound(0, UsdGeom.Tokens.default_).ComputeAlignedRange()
                mid = np.array([bb.GetMidpoint()[0], bb.GetMidpoint()[1], bb.GetMidpoint()[2]])
                if float(np.linalg.norm(cp - mid)) < tolerance:
                    return True
                # Also try XY-only bbox check for proper bins
                mn = np.array([bb.GetMin()[0], bb.GetMin()[1]])
                mx = np.array([bb.GetMax()[0], bb.GetMax()[1]])
                if (mx[0] - mn[0]) > 0.05 and (mx[1] - mn[1]) > 0.05:
                    if (mn[0] <= cp[0] <= mx[0]) and (mn[1] <= cp[1] <= mx[1]):
                        return True
            except Exception: pass
    return False

# Replace _is_in_bin usages with _is_near_dest in the cycle-end delivered check:
# line ~33346:
if _is_near_dest(S["picked_path"]):
    S["delivered"].add(S["picked_path"])
else:
    S["failed"].add(S["picked_path"])
```

The existing `_is_in_bin` in `_cube_to_pick` (line 33138) should remain as-is (prevents
re-picking cubes already in the final bin). For non-bin destinations, the cube moves away from the
source zone after delivery so `_is_in_bin` returning False does no harm for the pick-exclusion
purpose.

### Patch 3 — Fix reach check for handoff picks (fixes Category B for CP-51, CP-68)

Root: handoff at [0, -0.3, 0.825] is 0.76m from FrankaB at [0.7, 0, 0.75]. The reach check in
`_cube_to_pick` is `> 0.70m` — fails by 6cm.

Option A (preferred): Move handoff position closer to robot B. Change in template code:
```
drop_target=[0, -0.3, 0.825]  →  drop_target=[-0.1, -0.25, 0.825]
```
This puts the handoff at sqrt((0.7-(-0.1))^2 + (0.25)^2) = sqrt(0.64 + 0.0625) = 0.79m from
FrankaB — still fails. The geometry is constrained by robot A's reach from [-0.7, 0, 0.75]:
sqrt(0.7^2 + 0.3^2) = 0.76m. Both robots must reach the same point. Solution: widen the reach
threshold in `_cube_to_pick` for handoff scenarios, or use `_reach_m = 0.85` for Franka when a
non-belt source is involved.

Option B (correct): The hardcoded 0.70m reach in the curobo `_cube_to_pick` (at line 33142) is
already correct for the newer curobo template. Check — line 33136: `_reach_m = 1.20 if ROBOT_FAMILY in ("ur10", "ur10e") else 0.70`. And line 33142: `if float(np.linalg.norm(cp[:2] - base_xy)) > _reach_m: continue`.

But the older spline `_cube_to_pick` at line 32089 has a hardcoded `0.70`. The curobo template
(lines 33124-33157) has `_reach_m = 0.70` for Franka — same limit.

The actual Franka reach is ~0.85m. Widening to 0.85m in `_cube_to_pick`:

```python
# line 33136 in the curobo template:
_reach_m = 1.20 if ROBOT_FAMILY in ("ur10", "ur10e") else 0.85  # was 0.70; Franka arm is 0.85m
```

This fixes robot B being unable to detect Cube_1 at the handoff (0.76m).

Also add a handoff-state gate: robot B should only pick Cube_1 from the handoff once robot A has
set `handoff:state = "placed"`. This requires robot A's controller to write to the handoff prim
after dropping, and robot B's `_cube_to_pick` to gate on that attr. This is a Sprint 3+ feature
(declared in CP-51 extension_notes). For now, temporal separation via sensor position is the
intended mechanism: robot B's sensor at [0, -0.3, 0.85] does not fire until a cube is within
8cm radius of that point. Cube_1 won't reach that position until robot A drops it there — natural
sequencing. The mutex guard (Patch 1) ensures only one robot picks at a time.

### Patch 4 — CP-52 Belt Coordination (fixes parallel-pick deadlock)

The belt-pause-deadlock for CP-52: FrankaA pauses the belt during its pick cycle. FrankaB's cubes
(Cube_3, Cube_4) are frozen mid-belt before reaching SensorB.

Fix: each robot should only pause/resume "its" belt segment. Since CP-52 uses a single shared
belt, an alternative approach is: the belt should only be paused during the grip-close segment
(S2 in the curobo plan), not the entire pick cycle. The curobo controller already pauses belt
on `wait_sensor` → `settling` transition. A targeted fix: resume belt immediately after cube is
gripped (during "lift" segment), so other cubes can flow.

Implementation in curobo `_on_step`, after the close segment completes and grasp FJ is formed:
```python
# After S["grasp_joint"] is set (grasp FJ formed), resume belt:
if S.get("grasp_joint") and not S.get("belt_resumed_after_grip"):
    _resume_belt()
    S["belt_resumed_after_grip"] = True
```
Reset `belt_resumed_after_grip` in `wait_sensor` entry.

This allows Cube_3/Cube_4 to flow toward SensorB while FrankaA is transiting to the bin.

### Patch 5 — CP-67 Disc-as-belt handling

The rotary disc at `/World/RotaryTable/Disc` does not have `physxSurfaceVelocityAPI`. Robot B's
controller sets `BELT_PATH = "/World/RotaryTable/Disc"` → `_belt_sv = None` → pause/resume are
no-ops. This is actually acceptable — the disc should keep rotating continuously. The real issue is
robot B waiting for a cube to appear at sensor position [0, -0.2, 0.85].

Required scene redesign: add `physxSurfaceVelocityAPI` to the disc prim with a tangential surface
velocity vector matching the disc's rotation at the cube pickup radius. Alternatively, give robot B
`belt_path=None` (no belt to pause — disc rotation is continuous). The `_gen_pick_place_curobo`
function already handles `belt_path=None` gracefully (line 32025: `_belt_prim = stage.GetPrimAtPath(BELT_PATH) if BELT_PATH else None`). So the template fix is to change the template code to pass `belt_path=None` for robot B.

CP-67 also has the fundamental issue that robot B's `_cube_to_pick` must detect a cube ON THE
ROTATING DISC at its -Y edge. The disc top surface is at z≈0.835. Robot B is at [0, -0.5, 0.75],
so base_z=0.75. Cube at z=0.835 is within z-window [0.45, 1.25] — OK. Distance from B to disc
center at (0,0) is 0.5m, which equals B's arm reach to disc edge at radius 0.20m is 0.5 - 0.20 =
0.30m to center but actual cube position on disc at [-Y, radius=0.20] is [0, -0.20, 0.835], which
is 0.30m from B's base at [0, -0.5] — well within reach. The detection should work once cube
arrives at -Y disc edge.

The CP-67 scene is fundamentally correct architecturally but requires code support for the disc as
a non-belt transport (belt_path=None for robot B).

---

## Estimated Unlock Count

| Canonical | Primary Causes | Patches Needed | Unlock Confidence |
|-----------|----------------|----------------|-------------------|
| CP-51 | A + B + C | 1 (curobo mutex) + 2 (is_near_dest) + 3 (reach 0.85m) + template mutex_path passthrough | High — all patches are code-only |
| CP-52 | Belt deadlock | 4 (resume after grip) | Medium — depends on exact belt timing |
| CP-53 | A + C | 1 + 2 + template mutex_path | High — sequential timing natural once A delivers to rack |
| CP-67 | Disc transport + A | 5 (belt_path=None for B) + 1 | Low — also needs disc friction verification |
| CP-68 | A + B + C (same as CP-51) | 1 + 2 + 3 | High — same fixes as CP-51 |
| CP-76 | C only | 2 | High — both robots work independently; just need delivery check to pass |

Patches 1 + 2 + 3 together should unlock CP-51, CP-53, CP-68, CP-76 (4 of 6).
CP-52 needs Patch 4 (medium risk). CP-67 needs scene-level disc transport fix (lower confidence).

---

## Canonicals Requiring Scene Re-design vs Code Fix

**Code fix only**: CP-51, CP-53, CP-68, CP-76. All failures are in the controller runtime logic.
Templates are architecturally sound; geometry and sensor placement are correct.

**Code fix + scene tweak**: CP-52. The single shared belt with two independent pause/resume
controllers is architecturally fragile. Patch 4 (resume after grip) is a code fix, but a proper
solution would add per-robot belt segments (two conveyors feeding into a shared pickup zone).
This is a scene change but not mandatory if Patch 4 suffices.

**Scene re-design needed**: CP-67. The rotary table as a transport medium between two robots
requires the disc to have a proper surface velocity API or an alternate cube-detection mechanism
(proximity sensor on -Y disc edge, triggered by disc rotation). The current architecture sets
`belt_path="/World/RotaryTable/Disc"` which doesn't have the required physics API. The correct
approach is either (a) add `physxSurfaceVelocityAPI` to the disc with computed tangential velocity,
or (b) treat the disc as a passive transport and use a proximity sensor on robot B's side that
fires when a cube is within pickup range regardless of belt pause state. Option (b) is achievable
with a belt_path=None change to robot B's controller install, which is a template code change only.
With that change, CP-67 becomes a code-fix candidate.

---

## Implementation Priority

1. **Patch 1** (curobo `_on_step` mutex guard) — single location in `_gen_pick_place_curobo`,
   unlock all 6 canonicals partially.
2. **Patch 2** (`_is_near_dest` delivery check) — single location in curobo template, unlocks
   CP-51, CP-53, CP-68, CP-76.
3. **Template updates** (add `mutex_path=` to CP-52, CP-53, CP-67 controller calls) — JSON edits
   to 3 template files.
4. **Patch 3** (Franka reach 0.85m) — single constant in curobo `_cube_to_pick`.
5. **Patch 4** (CP-52 belt-resume-after-grip) — curobo `_on_step` executing branch.
6. **Patch 5** (CP-67 belt_path=None for robot B) — template JSON edit only.
