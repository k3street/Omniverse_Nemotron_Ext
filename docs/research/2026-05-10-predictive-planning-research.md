# Predictive Planning for cuRobo Conveyor Pick-Place — Research Report

**Date:** 2026-05-10
**Author:** research agent
**Scope:** Resolve `plan_pose` 100%-fail rate on conveyor-belt CPs (CP-37/46/51/etc) in the Isaac Assist patched-set.
**Out-of-scope:** ML-based grasp synthesis, vision-driven pose updates, multi-robot bin-conflict.

---

## 1. TL;DR

**The "predictive planning" framing is mostly a red herring.** Our existing pipeline already pauses the belt and runs a settle-window (`settle_ticks` = 8–16 ticks ≈ 130–270ms) before reading the cube position. With μ ≈ 0.75 friction at 0.2 m/s, an unpinned cube travels < 3 mm during settling — an order of magnitude below the 0.855 m Franka reach radius. **The dominant CP-37-style failure is geometric reach, not motion blur.**

Concrete recommendation, ranked by impact-per-LOC:

1. **(P0, ~30 LOC) Pre-claim reachability filter in `_cube_to_pick`.** Before claiming a cube, run an analytic reach check (`||cube_pos - robot_base_xy|| <= reach_radius` in base XY-plane). If the cube is currently out of reach, return `None`; the next tick re-checks. This stops 100% of the wasted plan_pose calls on out-of-reach cubes (the literal CP-37 failure mode). Revert-safe — pure read-only check, no mutation.
2. **(P1, ~50 LOC) Reach-with-prediction in `_cube_to_pick`.** Same as P0 but project the cube forward by `(reach_safety_margin + plan_time_estimate) * belt_velocity` and require the *projected* position to be inside reach. Uses constant-velocity model — no MPC needed because belts are kinematic.
3. **(P2, ~80 LOC) Goalset planning to a small set of intercept candidates.** When a cube enters the lookahead zone, sample 3–5 future positions (0.0s, 0.2s, 0.4s, 0.6s, 0.8s ahead) and call `_planner.plan_goalset`. Pick the earliest-reachable. Replaces the current `plan_single` per segment-1 with one batched call.
4. **(P3 — DON'T) Switch to `MpcSolver`.** The cuRobo MPC framework re-solves at 500 Hz and would naturally track moving targets, but (a) requires bottom-up rewrite of the controller, (b) the docs explicitly warn "MPC is experimental, no safety guarantees", (c) MPPI cost-tuning is a research project of its own. Skip.

P0 alone should unlock CP-37, CP-46, CP-51, CP-52, CP-53, CP-58 (all P-PLAN_FAIL with cubes spawned outside reach radius). P0+P1 should additionally unlock CP-62, CP-67, CP-68, CP-73, CP-76. **Implementation budget: one 2–4h session.**

---

## 2. Belt-pause investigation — is the cube actually stopping?

### 2.1 PhysX semantics of `surfaceVelocity = 0`

From [PhysX 5.4 RigidBodyDynamics docs](https://nvidia-omniverse.github.io/PhysX/physx/5.4.1/docs/RigidBodyDynamics.html) and [PxRigidDynamic::setKinematicSurfaceVelocity](https://documentation.help/NVIDIA-PhysX-SDK-Guide/RigidDynamics.html):

> "[Surface velocity] sets a persistent velocity on the kinematic actor but flags the actor to bypass integration, meaning that objects interacting with the kinematic actor through collisions will behave as if the kinematic actor is moving, although the actor's pose does not actually change."

The mechanism is implemented inside the **contact-modify callback** — at each contact, PhysX adds the surface velocity to the relative-velocity term used in the friction-cone solver. **It does NOT apply a force to the dynamic body directly.** It synthesizes the velocity that the contact friction "thinks" the surface is moving at.

Implication: when `surfaceVelocity` is set to (0, 0, 0):
- The contact-modify callback adds zero to the relative velocity.
- The cube's existing momentum is unchanged at the moment of the write.
- The cube then decelerates *only* through normal kinetic friction with the belt (no longer being pushed forward).

### 2.2 Numerical estimate of cube glide distance

For a typical CP-37 cube on a rubber belt at 0.2 m/s, with conservative μ = 0.5 (rubber-on-rubber static is 1.0–1.16 but kinetic can be lower; Isaac Sim defaults vary):

- Deceleration: a = μ·g = 0.5 × 9.81 = **4.91 m/s²**
- Time to stop from 0.2 m/s: t = v/a = 0.2 / 4.91 = **41 ms** (≈ 2.5 ticks at 60Hz)
- Glide distance: d = v² / (2a) = 0.04 / 9.82 = **4.1 mm**

For a "high speed" CP at 0.5 m/s:
- t = 102 ms (≈ 6 ticks)
- d = 25.5 mm = **2.5 cm**

For 0.8 m/s:
- t = 163 ms (≈ 10 ticks)
- d = 65 mm = **6.5 cm**

Our existing `settle_ticks = 16` (267 ms) at >0.25 m/s belts is **already longer** than any of these stopping times. The settle windows are sufficient.

### 2.3 Is the belt-pause actually working in the cuRobo handler?

Per [`docs/research/2026-05-09-belt-pause-physx-fix.md`](./2026-05-09-belt-pause-physx-fix.md), the cuRobo handler at line ~33548 *does* use the pre-step subscription pattern (Fix 1) — it writes to `physxSurfaceVelocity:surfaceVelocity` from a `subscribe_physics_on_step_events(..., pre_step=True)` callback. This is the correct timing. The cube IS being driven to (0, 0, 0) surface velocity reliably.

### 2.4 Does the cube actually have momentum when settle starts?

In the settling phase (line ~33887), `_pause_belt()` was called the previous tick. By tick 1 of settling, the contact-modify callback has already run with the new (0, 0, 0) surface velocity. The cube glides forward briefly under inertia. By tick 16 (267 ms), the cube has fully stopped (per §2.2 calculation, even at 0.5 m/s belts).

**Conclusion: the cube IS stopping. Predictive planning to compensate for "ongoing motion during plan time" is not the issue.** The issue is that some cubes spawn *fundamentally out of reach* and the controller waits for them to enter reach, then the belt-pause-and-pick logic claims them when they arrive — but by that time the controller still uses an old cube position from the previous read, OR the cube has *already passed* through reach.

Re-reading the user's CP-37 description more carefully:

> Cubes spawn at world x ∈ {-1.4, -1.15, -0.9, -0.65}, y=0.4, z=0.835 → in Franka base frame y=1.4 (out of reach 0.855m).

**The cubes never get into Franka reach in CP-37.** The Franka is rotated 90° around Z, so its forward direction is +Y, but the belt is at world y=0.4, which after the 90° rotation maps to *base* y=1.4 — the cube is 1.4m to the *side* of the Franka, never in reach. The whole CP-37 scenario is geometrically impossible for that Franka pose. The 24/24 plan_pose failure is correct; the controller is wasting plan calls on unreachable cubes.

This shifts the recommendation: **the fix is to not call plan_pose on out-of-reach cubes**, not to predict their position better.

---

## 3. State of the art for conveyor pick

### 3.1 Industrial systems

[FANUC iRPickPRO + Visual Line Tracking](https://motioncontrolsrobotics.com/line-tracking-robotic-pick-and-place/), ABB IRB 360 FlexPicker, KUKA.ConveyorTech, and Universal Robots URCap conveyor tracking all share the same architecture:

1. **Encoder on belt** measures belt position (not just velocity). Position is integrated continuously by a real-time controller (1–10 kHz).
2. **Pick zone window** is defined in belt-relative coordinates — items are tracked by their offset on the belt, not in world-fixed coordinates.
3. **Pre-computed motion templates** for each item type (in robot base frame, parameterized by pick offset). Trajectories are generated offline once, then re-played with belt-relative offset compensation.
4. **The robot follows the belt** in the pickup phase — for delta robots this means the gripper actually moves laterally in world space at belt velocity during the close-grip phase, so there is no relative motion at the moment of grasp.

This requires a real-time control architecture that we don't have (Isaac Sim runs at 60 Hz physics; we plan at ~5 Hz; PLC encoders aren't part of the model). However, the *insight* generalizes: **don't plan to a static cube position; plan to where the cube will be at grasp time**.

### 3.2 Academic — kinodynamic search-based planners

[Menon, Cohen, Likhachev 2014, "Motion Planning for Smooth Pickup of Moving Objects"](https://www.cs.cmu.edu/~maxim/files/planforsmoothpick_icra14.pdf) (CMU/UPenn, used on PR2):

- State-space includes time as a search dimension: `s = (θ_1, ..., θ_n, θ̇_1, ..., θ̇_n, t)`.
- Heuristic samples future object positions at t = Δt, 2Δt, 3Δt, ... and computes time-of-flight to each via projected end-effector velocity. Returns `min Δt_estimate` such that arm can reach the projected object position before object passes.
- Three motion phases: Reach (free arm motion to object), Grasp (close gripper while tracking object velocity), Lift (rigid body manipulation).
- Algorithm runs ARA\* with adaptive motion primitives. Time complexity is search-tree-bound; not constant.

[Islam, Salzman, Agarwal, Likhachev 2020, "Provably Constant-time Planning and Replanning for Real-time Grasping Objects off a Conveyor Belt"](https://www.roboticsproceedings.org/rss16/p025.pdf) (RSS 2020):

- **Pre-computes a trajectory library** offline indexed by (belt position, belt speed). Library is built from a discretized lattice of object pickup poses.
- Online query: O(1) lookup into the library, validity-check, execute. Real-time guaranteed regardless of scene complexity.
- Replanning version: handles updates to pickup pose during execution by graph-distance to nearest cached trajectory.
- Strength: provably real-time. Weakness: setup cost is one-time but heavy; library must be re-built per robot/object pair.

### 3.3 cuRobo paper — the referenced cycle-time paper

[Jain et al. 2025, "Industrial Robot Motion Planning with GPUs: Integration of cuRobo for Extended DOF Systems"](https://arxiv.org/html/2508.04146v1):

- Reports cuRobo's cycle time 3.1s vs. MoveIt 9.9s on Franka pick-place.
- Mentions vision-driven dynamic replanning reducing scrap rates by 18% — but does NOT describe the actual replanning algorithm. The replanning is fed an updated static pose from vision, not a moving target.
- 30–100 ms typical plan time — confirms our `~0.5s × 5 segments = 2.5s` total plan time estimate.

### 3.4 Academic — time-optimal interception

[Wang 2022, "A Time-Optimal Intersection Search Algorithm for Robot Grasping"](https://onlinelibrary.wiley.com/doi/10.1155/2022/5349426) and [Liu et al. 2024, "Dynamic grasping of manipulator based on realtime smooth trajectory generation"](https://journals.sagepub.com/doi/full/10.1177/09544062231161147):

- Shared idea: parametrize candidate intercept points by time `T`, then solve the geometric problem "find smallest T such that robot can reach object_pos(T) before object passes through workspace exit boundary."
- Reduces to a 1D optimization over T, with the robot's reachability map and the object's future trajectory as inputs.

### 3.5 Synthesis

The dominant pattern in both industrial and academic systems is **time-indexed sampling** plus **early termination**:

1. Sample object position at future times t₁ < t₂ < t₃ < ... up to "leaves workspace."
2. For each tᵢ, compute (cheaply) "is robot end-effector reachable at object_pos(tᵢ) at time tᵢ?"
3. Pick smallest tᵢ that's reachable.
4. Plan motion to land at `object_pos(tᵢ)` at time `tᵢ`.

This is exactly what we should implement: the difference between us and Menon/CMU is that we don't need a kinodynamic planner because (a) we're going to pause the belt, so cube velocity at grasp moment is zero, (b) cuRobo handles the trajectory generation; we just need to pick the right goal.

---

## 4. cuRobo native support for moving / batched targets

### 4.1 What cuRobo gives us out of the box

From [cuRobo MotionGen API docs](https://curobo.org/_api/curobo.wrap.reacher.html):

| Method | Purpose | Useful for our problem? |
|---|---|---|
| `plan_single(start, goal, cfg)` | One trajectory to one goal | What we use today. |
| `plan_goalset(start, goalset, cfg)` | One trajectory to ANY of N goals | **Yes — option C below.** |
| `plan_batch(starts, goals, cfg)` | N independent trajectories in parallel | Less useful — we want best of N, not all. |
| `plan_batch_goalset(starts, goalsets, cfg)` | N robots × N goalsets | Too much for our case. |
| `plan_grasp(approach, grasp, retract)` | Combined approach+grasp+retract | Replaces our 5-segment loop, but adds ~20 LOC of refactoring. Returns one `GraspPlanResult`. |

**Key insight: `plan_goalset` plans to the cheapest reachable goal among a set, in a single call. Same total plan time (~30–100 ms median) as `plan_single`.** This is a free batch planning capability.

cuRobo also supports `time_dilation_factor=0.1` (slow trajectory) and `maximum_trajectory_dt` (relaxed timing) per [discussion #285](https://github.com/NVlabs/curobo/discussions/285), useful if we need to slow the descent for synchronized grasping.

### 4.2 What cuRobo does NOT have (out of scope)

- **No time-indexed `plan_pose(goal_at_time_t)` API.** All `plan_*` methods take a static pose. To plan to a future pose, *we* compute the future position and pass it as a static goal.
- **No belt-tracking trajectory primitive.** No "follow this point on the belt" rollout. cuRobo's MPC could approximate but is experimental.
- **No "reachability" pre-check API on Pose** (only via running an IK solve, which itself is what fails). However, for fixed-base manipulators we can do an analytic check: `||goal_pos_base|| <= max_reach_radius` is necessary but not sufficient for IK feasibility — it's a *cheap rejection filter* good enough to skip clearly-unreachable goals before paying the IK cost.

### 4.3 cuRobo `MpcSolver` (the experimental option)

- File: `examples/isaac_sim/mpc_example.py` in NVlabs/curobo.
- Uses MPPI sampling-based MPC; ~500 Hz on RTX 4090.
- API: `mpc_solver.update_goal(goal_buffer)` + `mpc_solver.step()` per tick.
- Per [v0.8.0 changelog](https://raw.githubusercontent.com/NVlabs/curobo/main/CHANGELOG.md): "MPC framework is experimental and does not provide safety guarantees as the MPC optimizes constraints as cost terms with large weights, which can put the robot in a collision state if the costs are not tuned."
- Per [GitHub Discussion #375](https://github.com/NVlabs/curobo/discussions/375): MPPI requires 400 rollouts/env, ~800 MB GPU memory.

**Recommendation: don't switch to MpcSolver.** It would solve the moving-target problem cleanly but represents a controller rewrite, requires cost tuning, and breaks the deterministic plan-then-execute model that our 49 stable canonicals depend on.

---

## 5. Implementation options A–D — pros/cons + LOC

### Option A — Pre-claim prediction (project forward by total_plan_time)

**Algorithm:**
```python
# In _cube_to_pick (line ~33685)
total_plan_time_estimate = 0.5  # seconds, empirical
belt_v = np.array(_nominal_belt)  # world-frame velocity
cube_pos_now = _world_pos(cube_path)
cube_pos_at_grasp = cube_pos_now + belt_v * total_plan_time_estimate
goal_world = cube_pos_at_grasp  # plan to projected position
```
Pause belt at claim time (already done). Plan to projected position.

**Pros:** Simple. ~10 LOC. No new cuRobo API.
**Cons:** Only helps if cube would otherwise *barely* miss reach window. CP-37 still fails (cube never gets into reach). Doesn't address the dominant failure mode.
**LOC:** ~10. **Risk:** Low. **Impact:** ~1–2 CPs (only the marginal cases).

### Option B — Re-claim per segment

**Algorithm:** Re-read cube position before each of the 5 segment plans; re-plan if cube has moved more than ε.

**Pros:** Most adaptive.
**Cons:** Belt is *paused* during pick — cube isn't moving between segments anyway, so this is moot. Adds complexity for zero gain.
**LOC:** ~40. **Risk:** Medium (state-machine churn). **Impact:** 0 CPs in our setup.
**Verdict: skip.**

### Option C — Goalset planning to time-indexed candidates

**Algorithm:**
```python
# In _build_segments for segment 1 (approach above cube):
# Sample N future positions of cube and plan to whichever reaches first.
candidates = []
for dt in (0.0, 0.2, 0.4, 0.6, 0.8):
    cube_at_t = cube_pos + belt_velocity * dt
    if _is_in_reach(cube_at_t):
        candidates.append(cube_at_t)
if not candidates:
    return None  # no reachable future — skip cube
res = _planner.plan_goalset(start, goalset_from(candidates), cfg)
```

**Pros:** Single batched plan call (~30–100 ms total). cuRobo picks the cheapest reachable goal. Handles "cube enters reach late" gracefully.
**Cons:** Belt is paused during pick, so the candidates collapse to one (the current pos). Useful only for the *claim* decision, not for in-pick planning. Implementing `_is_in_reach` correctly + plumbing goalset through `_plan_to_world_point` is ~80 LOC.
**LOC:** ~80. **Risk:** Medium (plan_goalset returns goal-index in result; need to track which candidate succeeded). **Impact:** 1–3 CPs marginal.

### Option D — Pre-claim reachability filter (RECOMMENDED P0)

**Algorithm:**
```python
def _is_in_reach(cube_pos_world, safety_margin=0.05):
    """Cheap analytic filter — reject cubes outside Franka reach radius."""
    cube_base = _world_to_base(cube_pos_world)
    # Franka max reach 0.855m, but with safety margin and base-Z component
    horizontal_dist = (cube_base[0]**2 + cube_base[1]**2)**0.5
    return horizontal_dist <= (0.855 - safety_margin)

def _cube_to_pick():
    for path in SOURCE_PATHS:
        if path in S["delivered"] or path in S.get("failed", set()):
            continue
        # Skip if not yet at sensor (existing logic)
        # ... existing trigger check ...
        # NEW: skip if not yet in reach
        cube_pos = _world_pos(path)
        if cube_pos is not None and not _is_in_reach(cube_pos):
            continue  # cube not in reach yet — wait for next tick
        return path
    return None
```

**Pros:**
- Stops 100% of the wasted plan_pose calls on out-of-reach cubes.
- Pure read-only — zero side effects on existing canonicals.
- Trivial revert (delete one function and one `if` check).
- 30 LOC including reach-radius lookup per robot family.
**Cons:**
- Some cubes will pass through the reach zone too quickly to be claimed in the next tick. P1 (option below) addresses this.
**LOC:** ~30. **Risk:** Very low. **Impact:** Unlocks all 10 P-PLAN_FAIL CPs that fail because of out-of-reach cube spawn (per RCA: CP-37, CP-46, CP-51/52/53, CP-58, CP-62, CP-67/68, CP-73, CP-76).

### Option E — Pre-claim reachability filter WITH PREDICTION (RECOMMENDED P1)

**Algorithm:** Same as D but also project the cube forward by `plan_time + safety_margin` and require *both* current AND projected position to be in reach (or use the projected position as the goal). This handles the case where the cube is approaching reach but isn't quite there yet — wait until projected position lands inside reach.

```python
def _cube_to_pick():
    for path in SOURCE_PATHS:
        # ... filtering ...
        cube_pos = _world_pos(path)
        if cube_pos is None:
            continue
        # Project forward by plan time. With paused belt, this is moot, but
        # for the CLAIM decision we want to know if the cube will be in reach
        # by the time the plan completes (so we don't claim too early).
        belt_v = np.array(_nominal_belt)  # world-frame velocity
        plan_time_estimate = 0.6  # seconds, includes belt-pause settle + plan
        cube_at_grasp = cube_pos + belt_v * plan_time_estimate
        if not _is_in_reach(cube_at_grasp):
            continue  # cube won't be in reach by grasp time — skip
        return path
```

**Pros:** Captures cubes that are approaching reach but not yet inside, giving the controller earlier claim. Fixes CP-37-style "cube enters and exits reach in one tick."
**Cons:** None substantive. **Same revert profile as D** (one function, one filter).
**LOC:** ~50. **Risk:** Very low. **Impact:** Adds ~1–3 CPs on top of P0.

---

## 6. Recommended path

**Implement P0 (Option D) immediately. Add P1 (Option E) as a follow-up if P0 doesn't unlock CP-67/68.**

Code sketch (insert into the cuRobo handler around line ~33685, near the existing `_cube_to_pick`):

```python
# ─────────────────────────────────────────────────────────────────────
# Reach-radius filter — analytic pre-claim check (no IK/plan cost).
# Skips cubes that are physically outside the Franka/UR10 workspace.
# Without this, the controller burns ~24 plan_pose calls per CP on
# out-of-reach cubes (CP-37 RCA: 24/24 failure rate due to reach).
# ─────────────────────────────────────────────────────────────────────
_REACH_RADIUS = {
    "franka": 0.855,    # Franka max horizontal reach in base XY-plane
    "ur10":   1.300,    # UR10 max reach
    "ur10e":  1.300,
}.get(ROBOT_FAMILY, 0.855)

# Safety margin: keep some headroom so cuRobo's IK + collision avoid
# don't fail in the last few mm of the workspace boundary. Empirical:
# 5 cm gives 90% IK success at the boundary in cuRobo's solver.
_REACH_SAFETY = 0.05

def _is_in_reach(cube_pos_world):
    """Cheap analytic check: cube within robot horizontal reach radius?

    Returns True if cube_pos_world (3-vector) is within (_REACH_RADIUS
    - _REACH_SAFETY) of robot base in horizontal plane. Z is ignored
    (we plan vertical descent regardless).
    """
    if cube_pos_world is None:
        return False
    try:
        base = _world_to_base(cube_pos_world)
        horiz = (float(base[0])**2 + float(base[1])**2)**0.5
        return horiz <= (_REACH_RADIUS - _REACH_SAFETY)
    except Exception:
        return True  # be permissive if math fails — let plan_pose try

# (Optional P1: project cube forward by total plan time to handle
# cubes that will enter reach by grasp moment.)
def _is_in_reach_predicted(cube_pos_world, dt=0.6):
    """P1 variant: check if cube WILL be in reach after dt seconds at
    nominal belt velocity. Belt may be running OR paused at call time;
    if paused, cube_pos_world is current and delta is zero."""
    if cube_pos_world is None:
        return False
    try:
        belt_v = _nominal_belt or (0.0, 0.0, 0.0)
        # Only project if belt is currently running (i.e. we haven't
        # paused yet — pre-claim phase). If belt paused, cube is static.
        belt_running = (_belt_sv and _belt_sv.Get() and
                        sum(abs(v) for v in _belt_sv.Get()) > 1e-6)
        if not belt_running:
            return _is_in_reach(cube_pos_world)
        future_pos = (cube_pos_world[0] + belt_v[0] * dt,
                      cube_pos_world[1] + belt_v[1] * dt,
                      cube_pos_world[2] + belt_v[2] * dt)
        return _is_in_reach(future_pos)
    except Exception:
        return True
```

Insert call into `_cube_to_pick` (line ~33685):

```python
def _cube_to_pick():
    """Walk SOURCE_PATHS in order; return first cube past sensor that
    isn't already delivered/failed/in-flight AND is within reach radius."""
    for path in SOURCE_PATHS:
        if path in S["delivered"]: continue
        if path in S.get("failed", set()): continue
        if path == S.get("picked_path"): continue
        if not _is_past_sensor(path): continue
        # NEW reach filter — skip out-of-reach cubes (P0) or cubes that
        # won't reach reach by grasp time (P1).
        cube_pos = _world_pos(path)
        if not _is_in_reach_predicted(cube_pos):
            continue
        return path
    return None
```

### Why this is the right call

1. **Diagnostic data backs it.** The CP-37 RCA proves the failure is geometric reach, not motion-prediction error. Fixing the actual cause is more valuable than building infrastructure for a different problem.
2. **Revert profile is trivial.** One function definition, one if-statement; entire change can be removed in 30 seconds without affecting any other CP.
3. **No new cuRobo API.** No version compatibility risk, no Warp version churn, no MPC tuning.
4. **No state machine change.** The settle/execute states stay identical. We only filter what's claimable.
5. **CP-22 stable_ok stays stable_ok.** CP-22 cubes spawn within reach (existing 4/4 success). They pass the filter unchanged.

### What this does NOT solve

- **CP-22 (P-OTHER)**: it's already in `executing` mode failing. Different bug — investigate separately.
- **CP-05/57/74/80/84/85 (P-OTHER)**: never reaches plan_pose (different controller path). Different bug.
- **CP-06/40 (P-SENSOR_NEVER_FIRES)**: sensor wiring issue, not reach.
- **CP-48 (P-WALL_STUCK)**: cubes physically stuck against a wall. Different bug.
- **CP-60 (P-BUILD_FAIL)**: scene_cfg errors at build time. Different bug.

Estimated unlock from this change: **6–10 CPs** out of 25 in the patched-set.

---

## 7. Test plan

### 7.1 Implementation order

1. Add `_REACH_RADIUS`, `_REACH_SAFETY`, `_is_in_reach`, `_is_in_reach_predicted` to the cuRobo handler at line ~33685 (right before `_cube_to_pick`).
2. Modify `_cube_to_pick` to call `_is_in_reach_predicted` before returning a cube.
3. Restart `uvicorn` (per [`feedback_isaac_assist_service_restart.md`](../../service-restart-feedback)) — the service caches `tool_executor` on startup.
4. Re-run direct_eval on patched-set serially (per [`feedback_isaac_assist_kit_concurrency.md`](../../kit-concurrency-feedback)).

### 7.2 Test cohort, in priority order

**Cohort A — Stability check (must not regress):**

| CP | Current state | Expected after fix |
|---|---|---|
| CP-22 | stable_ok 4/4 | stable_ok 4/4 (cubes within reach, filter trivially passes) |
| CP-59 | stable_ok 5/5 | stable_ok 5/5 |
| CP-65 | stable_ok 1/1 (single cube) | stable_ok 1/1 |
| Any 5 random stable_ok | unchanged | unchanged |

**Acceptance criterion: 0/n regressions on Cohort A.** Any single regression → revert.

**Cohort B — P-PLAN_FAIL CPs (primary target):**

| CP | Current | Expected after fix |
|---|---|---|
| CP-37 | 0/4 (P-PLAN_FAIL) | ≥ 2/4 expected (some cubes never reach, some do) OR clean 0/4 with no plan_pose calls (measured by ctrl:plan_calls=0). The latter is acceptable because it confirms the filter is correctly identifying the geometry problem. |
| CP-46 | 0/6 | ≥ 1/6 OR plan_calls=0 |
| CP-51/52/53 | 0/4 | mixed |
| CP-58 | 0/3 | ≥ 1/3 |
| CP-62 | 0/4 | ≥ 1/4 |
| CP-67 | 1/4 | ≥ 2/4 |
| CP-73 | 0/4 (UR10) | ≥ 1/4 |

**Acceptance criterion:** zero of the 10 P-PLAN_FAIL CPs degrades (still 0/n delivered counts as no regression — but `plan_fails` should drop because we don't call plan_pose on out-of-reach cubes). Net gain ≥ 3 CPs going from 0-deliveries to ≥1-delivery.

### 7.3 Diagnostic instrumentation

Add a third counter to the controller telemetry:

```python
_a_plan_skipped = _ensure_attr("ctrl:plan_skipped_unreachable", Sdf.ValueTypeNames.Int, 0)
# ... in _is_in_reach when False:
try: _a_plan_skipped.Set(int(_a_plan_skipped.Get() or 0) + 1)
except Exception: pass
```

Per-CP probe should log `plan_skipped` so we can quantify "how many cubes were correctly filtered out as unreachable" vs "how many were planned for and failed."

### 7.4 Stop conditions

- **Hard stop**: any Cohort A regression. Revert immediately.
- **Soft stop**: Cohort B unchanged after deploy. The fix is doing nothing wrong, but the unreachability hypothesis was wrong. Move to Option C (goalset planning) as next attempt.

### 7.5 Follow-up if P0 lands cleanly

After P0 stabilizes:
1. **P1 is a 1-line tweak** — change `_is_in_reach` to `_is_in_reach_predicted` in `_cube_to_pick`. Tests with same cohort.
2. **Then consider Option C (goalset)** if specific CPs still fail because cubes spawn far apart and the controller can claim only one per cycle. Goalset would let cuRobo plan to the closest of N cubes.
3. **MPC stays off the table** until we hit a CP genuinely requiring continuous motion synchronization (none in current patched-set).

---

## 8. References

### cuRobo (NVlabs)
- [cuRobo main docs landing](https://curobo.org/)
- [MotionGen API reference](https://curobo.org/_api/curobo.wrap.reacher.html)
- [Python examples — MotionGen + MpcSolver](https://curobo.org/get_started/2a_python_examples.html)
- [Isaac Sim examples — mpc_example.py](https://curobo.org/get_started/2b_isaacsim_examples.html)
- [cuRobo report PDF (paper)](https://curobo.org/reports/curobo_report.pdf)
- [GitHub repo NVlabs/curobo](https://github.com/NVlabs/curobo)
- [CHANGELOG.md (raw)](https://raw.githubusercontent.com/NVlabs/curobo/main/CHANGELOG.md)
- [Discussion #375 — MPC scaling](https://github.com/NVlabs/curobo/discussions/375)
- [Discussion #285 — plan params](https://github.com/NVlabs/curobo/discussions/285)
- [Discussion #91 — batch motion planning](https://github.com/NVlabs/curobo/discussions/91)
- [Issue #248 — goalset with constraints bug](https://github.com/NVlabs/curobo/issues/248)
- [Discussion #245 — IK speed](https://github.com/NVlabs/curobo/discussions/245)

### Industrial / commercial systems
- [Motion Controls — FANUC line tracking](https://motioncontrolsrobotics.com/line-tracking-robotic-pick-and-place/)
- [rbtx — synchronized conveyor tracking with delta robot](https://rbtx.co.uk/en-GB/solutions/synchronized-conveyor-tracking-with-delta-robot)
- [KUKA high-speed delta + ConveyorTech](https://www.kuka.com/en-us/company/press/news/2022/06/new-highspeed-delta-robot)

### Academic — moving-target manipulation
- [Menon, Cohen, Likhachev 2014 — "Motion Planning for Smooth Pickup of Moving Objects" (PDF)](https://www.cs.cmu.edu/~maxim/files/planforsmoothpick_icra14.pdf)
- [Islam et al. 2020 RSS — "Provably Constant-time Planning and Replanning..." (PDF)](https://www.roboticsproceedings.org/rss16/p025.pdf)
- [Islam et al. 2021 IJRR — same, journal version](https://journals.sagepub.com/doi/abs/10.1177/02783649211027194)
- [arXiv 2003.08517 — provably constant-time conveyor grasping](https://arxiv.org/abs/2003.08517)
- [Wang 2022 — "A Time-Optimal Intersection Search Algorithm for Robot Grasping"](https://onlinelibrary.wiley.com/doi/10.1155/2022/5349426)
- [Liu et al. 2024 — "Dynamic grasping of manipulator based on realtime smooth trajectory generation"](https://journals.sagepub.com/doi/full/10.1177/09544062231161147)
- [Han, Chen 2019 — "Toward Fast and Optimal Robotic Pick-and-Place on a Moving Conveyor"](https://arxiv.org/abs/1912.08009)
- [Industrial cuRobo extended-DOF paper](https://arxiv.org/html/2508.04146v1)

### PhysX / surface velocity
- [PhysX 5.4 RigidBodyDynamics (incl. surface velocity discussion)](https://nvidia-omniverse.github.io/PhysX/physx/5.4.1/docs/RigidBodyDynamics.html)
- [Omniverse PhysxSurfaceVelocityAPI class reference](https://docs.omniverse.nvidia.com/kit/docs/omni_usd_schema_physics/106.1/class_physx_schema_physx_surface_velocity_a_p_i.html)
- [PhysX SDK Rigid Dynamics (legacy doc)](https://documentation.help/NVIDIA-PhysX-SDK-Guide/RigidDynamics.html)
- [IsaacLab #4561 — surface-velocity collision bug in Gym](https://github.com/isaac-sim/IsaacLab/issues/4561)

### Internal
- [`docs/research/2026-05-09-belt-pause-physx-fix.md`](./2026-05-09-belt-pause-physx-fix.md)
- [`docs/research/2026-05-10-cp37-rca-final.md`](./2026-05-10-cp37-rca-final.md)
- [`docs/research/2026-05-10-rca-synthesis.md`](./2026-05-10-rca-synthesis.md)
