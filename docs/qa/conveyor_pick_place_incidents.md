# conveyor_pick_place — incident log

Living record of every anomalous behavior observed during the
Isaac Sim pick-and-place integration work. The goal: build up a
corpus so a reasoning agent can apply Bayesian / hypothesis-elimination
analysis across the whole sequence rather than us chasing one issue
at a time.

**Format per entry:** ID, date/time (session-relative or UTC),
observed symptom, measured values, candidate causes (pre-diagnosis),
actual root cause (post-diagnosis, if known), fix, residuals.

---

## I-01 — `ghost object` in viewport after scene rebuild

- **Symptom:** A stray geometry appeared in viewport that had no
  corresponding prim in the stage (traverse showed normal 18 prims).
- **Measured:** prim-count snapshot vs visual render mismatch.
- **Candidates:** stale render cache; Kit session-layer leftover; prim
  hidden via purpose flag.
- **Root cause:** Kit session-state artifact (not in USD layer).
- **Fix:** Save scene + reload cleared it.
- **Residuals:** Happens after multiple `new_stage()` calls without save.

## I-02 — Franka default Gripper variant renders invisible fingers

- **Symptom:** Gripper body visible but finger meshes invisible.
- **Measured:** finger prims valid, mesh count via `Usd.PrimRange` = 0,
  but `GetFilteredChildren(TraverseInstanceProxies())` = 4 meshes.
- **Candidates:** missing geometry; wrong variant; LOD issue.
- **Root cause:** `Gripper` variant "Default" points to geometry that
  doesn't render in this Kit build. "AlternateFinger" works.
- **Fix:** `xf.GetVariantSets().GetVariantSet("Gripper").SetVariantSelection("AlternateFinger")`.
- **Residuals:** None with AlternateFinger.

## I-03 — `SingleArticulation.dof_names` empty after timeline.play()

- **Symptom:** `franka.initialize()` raised `'NoneType' object has no attribute 'link_names'`, OR silently succeeded with empty dof_names.
- **Measured:** `franka.dof_names` = [] directly after `initialize()`.
- **Candidates:** articulation prim structure broken; variant switch left
  articulation disconnected; physics not yet ticking.
- **Root cause:** In Kit RPC exec_sync, `timeline.play()` does not
  auto-pump `app.update()`; PhysX hasn't registered the articulation
  yet when `initialize()` runs. Standalone path avoids this via
  `world.reset()` → `SimulationContext.reset()` → play + initialize +
  finalize chain.
- **Fix:** Pump `omni.kit.app.get_app().update()` at least 6 times
  after `tl.play()` before calling `franka.initialize()`. Also call
  `SimulationManager.initialize_physics()` to ensure `physics_sim_view`
  exists.
- **Residuals:** None after pump.

## I-04 — `panda_finger_joint2` position drive writes silently ignored

- **Symptom:** Writing position targets to finger joint 2 had no effect.
- **Measured:** `UsdPhysics.DriveAPI.Get(fj2_prim, "linear")` returned
  null; drive target attribute never changed joint state.
- **Candidates:** drive not applied; wrong joint axis.
- **Root cause:** `panda_finger_joint2` is a **mimic joint** with no
  DriveAPI. It mirrors `panda_finger_joint1` via a PhysX mimic
  constraint. Position drives must only be applied to fj1.
- **Fix:** `apply_action(ArticulationAction(joint_positions=[...], joint_indices=[fj1_idx]))` — single joint only.
- **Residuals:** None once writing only to fj1.

## I-05 — `world.reset()` fails in exec_sync with `NoneType._physics_context`

- **Symptom:** `'NoneType' object has no attribute '_physics_context'`.
- **Measured:** `SimulationContext.instance()._physics_context` is None.
- **Candidates:** physics not initialized; wrong SimulationContext instance.
- **Root cause:** `world.reset()` requires `SimulationContext` to have
  been async-initialized. Kit RPC exec_sync doesn't set this up.
- **Fix:** Skip `world.reset()`; call `franka.initialize()` + `franka.post_reset()` directly. (But see I-03 for prerequisites.)
- **Residuals:** Many operations that rely on `world.reset()` are unavailable in exec_sync — must be emulated manually.

## I-06 — `physics_callback` via `World.add_physics_callback` unreliable

- **Symptom:** Added callback didn't fire, or fired briefly then stopped.
- **Candidates:** Callback lifetime tied to Scene; scene never fully
  initialized in exec_sync.
- **Root cause:** `World.add_physics_callback` hooks into the
  World/Scene lifecycle, which isn't fully wired in exec_sync.
- **Fix:** Use `omni.physx.get_physx_interface().subscribe_physics_step_events(cb)` directly — engine-level subscription, no World dependency.
- **Residuals:** None.

## I-07 — Subscriptions leak across Kit sessions

- **Symptom:** Previous run's callbacks still fired after stage reset,
  spamming expired-prim tracebacks into logs.
- **Measured:** `builtins._xxx_sub` attributes from prior runs not cleaned.
- **Candidates:** Normal Python object lifecycle; Kit doesn't auto-unsub.
- **Root cause:** Subscription handles stored on `builtins` persist
  across exec_sync calls (same Python process).
- **Fix:** At controller install, iterate `vars(builtins)` matching
  known sub-name prefixes and call `.unsubscribe()` on each.
- **Residuals:** New subscription kinds need to be added to the
  cleanup prefix list.

## I-08 — Our custom RmpFlow state-machine: robot "tangles itself"

- **Symptom:** Robot joints contort into impossible-looking configurations during picking.
- **Measured:** `ctrl:error_count` climbs with `RuntimeError: no solution` from Lula.
- **Candidates:** Wrong end-effector orientation constraint; target unreachable; bad cspace target.
- **Root cause (partial):** Orientation constraint `euler_angles_to_quat([π, 0, π/2])` pinned joint6 at 175° limit; RMP couldn't satisfy both position and orientation.
- **Fix (partial):** Removed orientation constraint — joint6 no longer pinned. But tangling persisted in a different form.
- **Residuals:** Even without orientation, "tangling" continued in the canonical `PickPlaceController` → rooted in I-09 and I-10 below.

## I-09 — PickPlaceController `end_effector_initial_height=0.3` is **world-absolute**, not relative

- **Symptom:** Robot approach target appeared to be below the table; joints flailed.
- **Measured:** Target EE position during phase 0 (approach) logged as world z=0.3 when robot base is at world z=0.75 (on table). Cubes at world z=0.875.
- **Candidates:** robot-local vs world frame confusion; stage-units mismatch.
- **Root cause:** Default `end_effector_initial_height=0.3m` is absolute world z (used as `_h1` in controller's height interpolation). Canonical standalone works because Franka sits on ground (base z=0). On elevated bases it's below the table.
- **Fix:** Auto-compute `h1 = max(source_z, destination_z) + 0.2m clearance` from stage geometry; pass to `PickPlaceController(end_effector_initial_height=h1)`.
- **Residuals:** Fixed descent sign but EE still didn't reach picking z (see I-10).

## I-10 — `PickPlaceController.is_done()` cycles with zero actual grasping

- **Symptom:** `cubes_delivered=9` counter climbed but 0 cubes physically moved.
- **Measured:** `controller.is_done()` fired repeatedly, each cycle ~900 physics ticks (~15 s), picking_position=cube pose, placing_position=bin pose. No cube position changed.
- **Candidates:** Missing `controller.reset()` between cycles; events_dt running mechanically without motion; gripper close not actually closing fingers.
- **Root cause:** `_event` auto-advances via `self._t += events_dt[event]`; if controller.forward returns `[None]*n` positions (e.g., pause/done state), apply_action is called but nothing moves, yet the event counter advances anyway. The controller "completes" phases without real motion.
- **Fix:** Defensive skip of `apply_action` when joint_positions is `None` or all-None list (see I-11).
- **Residuals:** Reveals the deeper cause: EE position target never reached (I-12).

## I-11 — `AttributeError: 'NoneType' object has no attribute 'astype'` in apply_action

- **Symptom:** High-frequency error in logs; error_count=964 over 8626 ticks.
- **Measured:** `ArticulationController.apply_action` chain calls `np.asarray([None, None, ...]).astype(np.float32)` → crash.
- **Candidates:** Wrong dtype; None in joint_positions; RmpFlow returning invalid action.
- **Root cause:** `PickPlaceController` phase 2 (inertia-settle) returns `ArticulationAction(joint_positions=[None]*n)` **by design**. `ArticulationController` has guard `if control_actions.joint_positions is not None` but this passes on a list-of-Nones (list is non-None). Downstream `np.asarray([None,None,...]).astype(np.float32)` crashes.
- **Fix:** In our caller, skip `apply_action` if `joint_positions is None` OR `all(p is None for p in joint_positions)`.
- **Residuals:** Upstream Isaac Sim bug — `ArticulationController` should guard this.

## I-12 — EE descends to z=0.99, cube at z=0.875; grip closes on air

- **Symptom:** Gripper closes 11.5cm above the cube.
- **Measured:** Motion log t=6.3 during phase 1 descend: EE at z=1.01, lf/rf at z=0.953, Cube_4 at z=0.875. Phase 1 ended without EE reaching target.
- **Candidates:** RmpFlow collision avoidance with belt/table; IK singular at low z; drive saturation; cspace_target pulling away.
- **Root cause (suspected, pre-verification):** `RMPFlowController.reset()` (rmpflow_controller.py:44-48) RE-APPLIES `set_robot_base_pose` with `self._default_position/_default_orientation` captured at __init__ time. If __init__ ran before physics handles were fully valid, defaults are (0,0,0) with identity quat. Our fix 3 applied the correct (0,0,0.75, 90°Z quat) **after** construction, but every `controller.reset()` on sensor trigger overwrites it back to the stale defaults. EE targets computed in a stale robot frame → descent target maps to world z=0.125 in correct frame, but policy aims at wrong-frame target ≈ world z=1.0.
- **Fix (applied, not yet verified):** Also patch `_cspace_controller._default_position` and `_cspace_controller._default_orientation` to the correct values, so `reset()` preserves our correction.
- **Residuals:** Verify cube gets picked up after this fix.

## I-13 — Belt nominal surface velocity captured as (0,0,0) after controller install

- **Symptom:** After first pick cycle, belt never resumes; subsequent cubes stuck.
- **Measured:** `_nominal_belt` captured at install as (0,0,0) because a prior install had left belt paused.
- **Candidates:** Attribute read at wrong time; scene state persistence issue.
- **Root cause:** `_nominal_belt = tuple(_belt_sv.Get())` captured stale paused value.
- **Fix:** If captured magnitude < 1e-6, fall back to default `(0.2, 0, 0)`. Also force-resume belt at install if paused.
- **Residuals:** None.

## I-14 — `isaac_sensor:triggered` attribute latches on but doesn't unlatch

- **Symptom:** After cube left sensor volume, `isaac_sensor:triggered` stayed True; controller re-picked the same cube.
- **Measured:** Cube_4 at (0.003, -0.39, 0.835) (in bin) but sensor still `triggered=True, last=/World/Cube_4`.
- **Candidates:** Trigger callback fires on ON but not OFF; PhysX trigger event filtering issue.
- **Root cause:** The `add_proximity_sensor` tool's trigger callback only sets `triggered=True` on entry; doesn't reset on exit reliably.
- **Fix:** In native controller, do our own XY-distance proximity check against SOURCE_PATHS each tick (`_cube_at_sensor()`). Independent of the latching attribute. Also track delivered cubes in a set to avoid re-picking.
- **Residuals:** Sensor attribute remains flaky as a diagnostic signal.

## I-15 — `xformOp:translate` writes during play don't teleport rigid bodies

- **Symptom:** Cubes reset via `xf.AddTranslateOp().Set(...)` during simulation had no effect; physics bodies stayed where they were.
- **Measured:** After reset command, cubes still reported old positions after a few physics ticks.
- **Candidates:** Op order; physics overriding USD; transform commit lag.
- **Root cause:** PhysX integrates rigid bodies each step; the simulation body pose overrides any authored USD transform unless the body is kinematic. Standard to teleport: `timeline.stop()` → set xform → `timeline.play()` (physics re-reads initial state).
- **Fix:** Stop timeline before teleport; play after.
- **Residuals:** None.

## I-16 — Franka rotated in USD but agent's `robot_wizard` output identity orientation

- **Symptom:** Spec said "facing +Y"; agent's scene output left Franka with identity orient (facing +X).
- **Measured:** `xformOp:orient = (1, 0, 0, 0)`.
- **Candidates:** Spec parsing; missing rotation parameter.
- **Root cause:** `robot_wizard` supports translate but not rotate; spec's "facing +Y" phrase didn't translate into a tool call. Manual rotate via `AddOrientOp` required.
- **Fix (session-level):** Apply 90° Z rotation manually.
- **Residuals:** Tool should accept `orientation`/`rotation` parameter.

## I-17 — `AddOrientOp()` without precision flag mismatches existing op type

- **Symptom:** `Error in '_SetValueImpl': Type mismatch: expected 'GfQuatd', got 'GfQuatf'`.
- **Measured:** Franka's existing `xformOp:orient` was authored as `Quatd` (double precision).
- **Fix:** Use `Gf.Quatd(...)` matching existing type, or pass `precision=UsdGeom.XformOp.PrecisionDouble` when adding a new op.

## I-18 — Franka USD reference doesn't resolve in exec_sync

- **Symptom:** `add_reference_to_stage(franka.usd)` returned with 0 descendants.
- **Candidates:** Async USD composition not pumped; cloud URL download slow.
- **Root cause:** USD resolver fetches cloud asset asynchronously; exec_sync doesn't pump the app loop to compose the reference.
- **Fix:** Pump `app.update()` in a loop after `add_reference_to_stage`, checking for children.
- **Residuals:** First-time download can take > 1 minute; may timeout exec_sync.

---

## I-19 — `RMPFlowController.reset()` re-applies stale default pose

- **Symptom:** Our `FIX 3` (re-applying correct `set_robot_base_pose` after construction) got undone every time the controller reset on sensor trigger.
- **Measured:** `rmpflow_controller.py:44-48` shows `reset()` re-calls `set_robot_base_pose(self._default_position, self._default_orientation)` — values captured at `__init__` time (which may be stale if physics handles weren't ready).
- **Fix:** Also patch `_cspace_controller._default_position` and `_default_orientation` to the correct values, so reset() preserves the correction.

## I-20 — Physics body orient != USD orient after live USD edit

- **Symptom:** Rotating `/World/Franka` via `xformOp:orient` during a running sim left the physics body still at identity orient, even though USD showed the rotation correctly.
- **Measured:** `franka.get_world_pose()` returned quat=(1,0,0,0) while USD attribute held (0.707, 0, 0, 0.707).
- **Root cause:** PhysX body pose is synced from USD only at sim reset/play. Mid-sim USD edits don't propagate to the physics body unless explicitly pushed.
- **Fix:** After `franka.initialize()`, compute USD-authoritative pose from `ComputeLocalToWorldTransform` and call `franka.set_world_pose(usd_pos, usd_quat)` to sync physics.

## I-21 — Default Franka drive gains too weak (kp=1000)

- **Symptom:** Even with correct IK targets, joints couldn't follow RmpFlow's commanded positions; joint-position error 70+ degrees at drive saturation.
- **Measured:** `drive:angular:physics:targetPosition` and `state:angular:physics:position` differed by 100°+ for joint1 during descent.
- **Root cause:** `robot_wizard` defaulted to `stiffness=1000, damping=100`. Franka position control expects `kp≈6000, kd≈300-500` to track rapid RmpFlow commands. At low gains, drive saturates at `max_force=87 N·m` per joint and can't keep up.
- **Fix:** Set `stiffness=6000, damping=500` on all Franka arm joints before installing controller. Tool-level fix: update `robot_wizard` defaults for Franka specifically.

## I-22 — Franka default "initial" joint config != Franka ready pose

- **Symptom:** At start, EE at (0.39, 0, 1.21) — arm extended UP, far from any reasonable workspace center. Made RmpFlow convergence hard.
- **Measured:** `state:angular:physics:position` on joint1=-15.97°, joint4=-168° at rest. Not the canonical Franka "ready" pose.
- **Root cause:** Franka USD authored different initial joints than the canonical home pose `[0, -0.785, 0, -2.356, 0, 1.571, 0.785]` rad.
- **Fix:** After `franka.initialize()`, call `franka.set_joint_positions(home_q)` + `set_joint_velocities(zeros)` to force ready pose.

## I-23 — PickPlaceController default events_dt too aggressive for elevated scene

- **Symptom:** Phase 1 (descend) only ran 200 physics ticks (3.3s); EE could not converge 32.5cm vertical descent in that window.
- **Measured:** With default `events_dt[1]=0.005`, min EE z was 0.953 vs target 0.875.
- **Fix:** Extend `events_dt[1]` to 0.002 (500 ticks = 8.3s). Enables RmpFlow to converge over wider descent spans on elevated robot bases. Caller may override.

## I-24 — Cubes on belt go to PhysX sleep, belt surface velocity stops moving them

- **Symptom:** After a pick cycle, belt surface velocity was set to nominal (0.2, 0, 0) but remaining cubes didn't move for 90+ sim seconds.
- **Measured:** Cube positions unchanged across 90s of belt-running sim time.
- **Root cause:** PhysX sleeps dynamic bodies at rest. Sleeping bodies don't respond to surface velocity impulses. Isaac Sim default sleep_threshold is aggressive.
- **Fix (not applied):** Set `physxRigidBody:sleepThreshold=0` on cubes, or force-wake cubes via `set_linear_velocity` each physics step.

## I-25 — RmpFlow doesn't converge to target with default `target_rmp.accel_p_gain=30`

- **Symptom:** Even with correct IK-solvable targets, correct base pose, correct initial joint config, RmpFlow settles into a local minimum 10-15cm short of target.
- **Measured:** IK (LulaKinematicsSolver) returned valid joint angles for pick/drop targets; drive targets from RmpFlow did NOT match these IK angles.
- **Root cause (suspected):** RmpFlow's target_rmp accel_p_gain=30 is weak relative to cspace_target_rmp position_gain=100. Without an explicit cspace target, the joint-config attractor pulls toward "nearest home config" rather than IK-correct config for target.
- **Fix (candidate, not applied):** Use `LulaKinematicsSolver.compute_inverse_kinematics` to pre-compute IK for each waypoint; call `rmpflow.set_cspace_target(ik_joints)` before each controller phase to steer RmpFlow toward the correct joint config.

## I-26 — cuRobo v0.7+ flat-module API diverges from IsaacLab mimic's `curobo.wrap.reacher.motion_gen`

**Symptom:** `from curobo.wrap.reacher.motion_gen import MotionGen` (pattern used by `isaaclab_mimic/motion_planners/curobo/curobo_planner.py`) raises `ModuleNotFoundError: No module named 'curobo.wrap'`.
**Measured:** The cuRobo package in `isaac_lab_env/lib/python3.11/site-packages/curobo` exposes `curobo.motion_planner`, `curobo.kinematics`, `curobo.scene`, `curobo.types` directly (flat structure with `_src/` internals). No `wrap/` submodule.
**Candidates:** (a) two cuRobo versions coexist; (b) IsaacLab mimic is stale; (c) our install is a stripped variant.
**Root cause:** cuRobo v0.7+ rewrite moved public API from `wrap.reacher.motion_gen.MotionGen(Config|PlanConfig)` to `motion_planner.MotionPlanner(MotionPlannerCfg)`. IsaacLab mimic code predates this rewrite.
**Fix:** Use new API `from curobo.motion_planner import MotionPlanner, MotionPlannerCfg`. `MotionPlannerCfg.create(robot=...)` is the factory entry.
**Residual:** Onboarding docs that link to IsaacLab mimic mislead new users.

## I-27 — cuRobo install missing `content/` default YAMLs → `MotionPlannerCfg.create` unusable without full robot dict

**Symptom:** `MotionPlannerCfg.create(robot={'urdf_path':..., 'base_link':..., 'tool_frames':...}, self_collision_check=False)` raises `KeyError 'kinematics'`.
**Measured:** `find curobo/content -name '*.yml'` → empty. No `franka.yml`, no `ik/lbfgs_ik.yml`, no `metrics_base.yml`.
**Candidates:** (a) install was content-stripped; (b) configs live in a separate content package; (c) expected to be user-provided.
**Root cause:** Library-only install. The `content/` package that ships with upstream cuRobo is absent. cuRobo factories assume either (i) a fully-populated robot YAML, OR (ii) a dict containing `kinematics`, `cspace`, `collision_spheres`, `self_collision_ignore`.
**Fix options:** (a) clone upstream cuRobo content repo, (b) write minimal franka.yml by hand (needs collision_spheres per link), (c) use only `Kinematics.from_basic_urdf()` for FK/IK without planning. Not fixed in this session — cuRobo generator stays an env-bridge stub.
**Residual:** Blocks full cuRobo integration. Target FAS 6e: franka.yml engineering.

## I-28 — Kit's Warp 1.8.2 rejects `wp.func(fn, module=__name__)` kwarg cuRobo expects

**Symptom:** `from curobo.motion_planner import MotionPlanner` raises `TypeError: func() got an unexpected keyword argument 'module'` at `curobo/_src/geom/collision/wp_collision_kernel.py:58`.
**Measured:** `inspect.signature(wp.func)` → `(f: 'Callable | None' = None, *, name: 'str | None' = None)`. The `module=` kwarg was added in Warp 1.9. Kit 5.1 bundles Warp 1.8.2.
**Root cause:** Warp API evolved; cuRobo v0.7+ kernels compiled against Warp 1.9+ expectations. Three cuRobo files call `wp.func(fn, module=__name__)` at module-load time.
**Fix:** Monkey-patch `wp.func` BEFORE importing any cuRobo module:
```python
import warp as wp
_orig_func = wp.func
def _patched_func(f=None, *, name=None, module=None, **_kw):
    return _orig_func(f, name=name) if f is not None else _orig_func
wp.func = _patched_func
```
Verified: after patch, `MotionPlanner`, `Kinematics`, `Scene`, `JointState` all import cleanly. The `module=` kwarg was diagnostic kernel-namespacing; silently ignoring it produces no observable issue here.
**Residual:** Patch must be re-applied every exec_sync. Put in a shared snippet.

## I-29 — isaaclab/curobo invisible on Kit's sys.path despite being in the site-packages dir — need `importlib.invalidate_caches()`

**Symptom:** `import isaaclab` raises `ModuleNotFoundError` inside Kit, even though the site-packages dir IS in `sys.path` and contains `__editable__.isaaclab-0.53.0.pth`.
**Measured:** `importlib.util.find_spec('isaaclab')` → None. Same for cuRobo. After `importlib.invalidate_caches()`, both imports succeed.
**Root cause:** Python's import cache is built once at startup; later sys.path modifications don't auto-invalidate the finder cache. Kit imports its bundled modules first, caches "not found" for isaaclab, and subsequent calls hit the cache.
**Fix:** Always pair `sys.path.insert(0, <site-packages>)` with `importlib.invalidate_caches()`. Pattern used in `_gen_pick_place_diffik`, `_gen_pick_place_osc`, `_gen_pick_place_curobo`.
**Residual:** Could be factored into a shared snippet `_PP_ISAACLAB_ENV_BRIDGE`.

## I-30 — `Articulation.get_world_poses()` returns shape (1, 3) not per-link

**Symptom:** `_av.get_world_poses()[0, _hand_body_idx + 1]` raises `index 8 is out of bounds for axis 1 with size 3`.
**Measured:** `get_world_poses()` returns `((1, 3), (1, 4))` — that's ROOT pose only, not all 11 link poses.
**Root cause:** The `isaacsim.core.prims.Articulation` wrapper exposes root pose for articulation-level control. Per-link poses are not a first-class tensor getter in 5.x. Use `UsdGeom.Xformable(<link_prim>).ComputeLocalToWorldTransform(0)` instead.
**Fix:** Switched diffik's EE pose access from `av.get_world_poses()[0, idx, :]` to `UsdGeom.Xformable(panda_hand_prim).ComputeLocalToWorldTransform(0)`. Jacobian still comes from `av.get_jacobians()` (per-link).
**Residual:** USD xform read may have a tick latency vs physics sim view. Acceptable for per-tick diffik compute.

## I-31 — `print()` from physics-step callbacks silently dropped — not visible in exec_sync response

**Symptom:** Diagnostic prints inside diffik `_on_step` don't appear in Kit RPC response. `ctrl:last_error` stays empty. State machine appears frozen with no visible reason.
**Measured:** Prints in install-time code (before subscription) appear in exec_sync stdout. Prints from inside the physics callback don't.
**Root cause:** exec_sync captures stdout for the duration of the submitted code's execution. The physics callback subscription survives past that window; subsequent callback invocations run during NORMAL app frames, and their stdout goes to Kit's log (file) not the RPC response.
**Fix:** Use `ctrl:last_error` or a purpose-built `ctrl:debug` USD attr instead of print() for in-callback diagnostics. Attrs are readable via a follow-up exec_sync probe. For diffik investigation, probing `av.get_jacobians()` + `av.get_world_poses()` via a separate exec_sync revealed I-30 directly.
**Residual:** Build a shared `ctrl:debug` attr helper for in-callback state capture.

## I-32 — diffik completes 4 cycles but delivers 0/4 cubes — arm moves but cubes don't follow

**Symptom:** `target_source=diffik` shows `ctrl:cubes_delivered=4` and `ctrl:error_count=0` after 120s, but `n_in_bin=0`. All cubes ended up at x≈0.86-0.93, y≈0.3, z=0.775 — fell off +X edge of belt.
**Measured:** DifferentialIKController's per-tick compute succeeded (Jacobian live, ee_pose live, commands applied). Arm traversed the 8-waypoint schedule. Cycles completed in ~10s each. BUT: cubes didn't follow the arm in transit → same failure mode as native (I-12).
**Candidates:** (a) Jacobian-IK solution reaches wrong EE pose relative to cube; (b) finger grip timing mismatched with diffik's convergence rate (PD commands from diffik don't converge arm to descend_z within dwell time); (c) cube positions drift during belt-paused state because physics still ticks and friction+gravity slide cubes forward a little per tick.
**Root cause (hypothesis, unproven):** (b) — diffik outputs per-tick absolute joint targets; PD drive with kp=6000 has finite settling time; the 1.5s descend segment may not converge hand to within 5mm of cube before dwell grip-close fires. Spline's joint-space CubicSpline over N=8 waypoints guarantees the arm IS at the target joint config at dwell time (trajectory-tracked rather than setpoint-converged). Diffik commands vary per tick as EE pose evolves → "moving target" for PD.
**Fix (not tested, time):** (i) hold-pose during dwell (zero diffik updates), (ii) switch to fixed-joint cheat grip, (iii) longer dwell (2.0s+).
**Residual:** diffik is a FUNCTIONAL motion controller (no crashes, completes cycles), not a reliable pick-place controller under current tuning. Marked 0/4 baseline. Priors: (i) is cheap to test, (ii) is most reliable. Detective agent should prioritize (i) first.

## I-33 — OSC compute needs mass_matrix + gravity which `Articulation` doesn't expose directly

**Symptom:** `OperationalSpaceController.compute(jacobian_b, ..., mass_matrix=..., gravity=...)` expects tensors. `isaacsim.core.prims.Articulation` has `get_body_masses()`, `get_body_inertias()` but no assembled mass matrix M(q) or gravity vector g(q).
**Measured:** `inspect.signature(OSC.compute)` lists `mass_matrix: Optional[torch.Tensor]` and `gravity: Optional[torch.Tensor]` — both defaulted to None.
**Root cause:** With `inertial_dynamics_decoupling=False` and `gravity_compensation=False`, OSC falls back to a Jacobian-transpose impedance law where M≈I and g≈0. Not as precise as full OSC, but doesn't need the hard-to-get tensors.
**Fix:** Configure OSC with `inertial_dynamics_decoupling=False`, `gravity_compensation=False`, `impedance_mode='fixed'`. Feed only jacobian + ee_pose + (optionally) ee_vel.
**Residual:** Full OSC (with M and g) would require either manual CRB dynamics, physx_sim_view direct calls, or waiting for Isaac Sim to expose get_mass_matrices(). Marked experimental.

## I-34 — cuRobo unblocked: cuda-core pip + content/ sync from GitHub

**Symptom:** `MotionPlannerCfg.create(robot='franka.yml')` failed with `ModuleNotFoundError: No module named 'cuda'` AND `KeyError 'kinematics'` AND missing franka.yml.
**Measured:** The installed cuRobo package (curobo-0.2 + nvidia-curobo-0.0.0 dist-info) was a library-only install: `curobo/content/` dir existed but was EMPTY (no configs/, no assets/). And cuRobo's default kernel backend requires `cuda.core` (the `cuda-bindings`/`cuda-core` pip package) which wasn't installed.
**Root cause:** Two-part dependency gap:
- Missing runtime: `cuda-core[cu12]` (installs `cuda-bindings`, `cuda-pathfinder`, `nvidia-cuda-nvcc-cu12`, `nvidia-nvfatbin-cu12`)
- Missing content: cuRobo's bundled robot YAMLs + default task YAMLs + URDF + meshes were stripped from the wheel
**Fix:** Two steps:
1. `pip install 'cuda-core[cu12]'` into isaac_lab_env.
2. Sparse-clone NVlabs/curobo GitHub (main branch), rsync `curobo/content/` into the installed package's content dir.
After both, `MotionPlannerCfg.create(robot='franka.yml')` succeeds, `MotionPlanner(cfg)` builds, `plan_pose` returns trajectories in ~0.5s (with cuda_graph warmed up).
**Residual:** The content sync is per-machine; to make this reproducible, add a `docs/qa/curobo_install.md` with the two commands, or write a post-install script.

## I-35 — cuRobo runs + plans + executes but 0/4 physical delivery — cube knock-over by arm sweep

**Symptom:** `target_source=curobo` installs cleanly. MotionPlanner builds a trajectory per segment, arm executes, state machine runs 4 cycles, `ctrl:error_count=0`, `panda_hand` ends at the planned drop target (0.002, -0.400, 0.950 with 2mm error — tight!). BUT all 4 cubes end up at x≈0.85, z=0.775 (fell off +X belt edge onto table).
**Measured:** Panda_hand reached drop target to within 3mm (tolerance setting). Cubes never followed the arm — same failure mode as native 0/4 and diffik 0/4. Bin empty.
**Candidates:** (a) grip-close timing mismatched with arm descent completion; (b) friction grip needs cube precisely between fingers and cuRobo's tolerance lets hand drift; (c) arm trajectory sweeps through other cubes on belt, knocking them off.
**Root cause (hypothesis, unproven):** Likely (c) combined with (a). `MotionPlannerCfg.create` was called with `scene_model=None` and `planning_obstacles=[]` — only self-collision check active, no scene obstacles. The planned Cartesian paths from home → above_cube → descend etc. can swing the arm body through the cube-row on belt, knocking cubes off and/or pushing them along.
**Fix (not tested):** Add cubes + belt + bin as `Cuboid` obstacles via `MotionPlannerCfg.create(scene_model=<world_config>)`. Build world_config from USD prim bounding boxes at plan time. This is the key value-add of cuRobo over spline; expected to bring delivery to 3-4/4.
**Residual:** cuRobo is FUNCTIONAL (plans, executes, completes cycles) but not DELIVERING until obstacles are wired. Mark FAS 6d as partially complete — obstacle-aware planning is the last step.

## I-36 — Native regression 3/4 → 0-1/4: fyrdelad grundorsak i refactor

**Symptom:** Native-controller levererade historiskt 3/4 (tillfälligt 4/4) via IK+RmpFlow-hybrid. Efter FAS 0-10 refactor: 0-1/4 deterministiskt.

**Diagnos:** Fyra separata regressioner kombinerade:

1. **Stale Scene Reset Manager-hooks**. spline/diffik/osc/curobo fick cleanup-kod (unregister native_pp/spline_pp/diffik_pp/osc_pp/curobo_pp från manager före install). Native fick aldrig samma cleanup. När native körs efter en annan controller i samma Kit-session → manager håller kvar stale hook som refererar till död prim → Stop+Play-reset halvfallerar.

2. **Fel IK-frame i `_guide_via_ik`**. Lula IK beräknade joint-config för att placera `panda_hand` vid target — men RMPFlowController's interna config.json anger `end_effector_frame_name="right_gripper"`. `right_gripper` är definerat i URDF:en 10cm ut från panda_link8 (typ fingertip-plan), medan `panda_hand` är vid handleden. Frame-mismatch → IK-guide puttade armen fel ~5-10cm i z → fingrarna missade kuben.

3. **Belt-resume mellan cykler**. Native's `_on_step` kör `_resume_belt()` vid varje "picking" → "wait_sensor"-transition. Spline ändrades till att hålla bandet fryst tills ALLA kuber levererade. Med belt körande mellan cykler driver kuber 2-3-4 förbi sensor under transit-cykeln för kub 1, och `_cube_at_sensor` (8cm-radie) missar dem.

4. **Finger drive gains default (kp~1000)**. För svagt för att klämma en 0.1kg kub; cube slipper greppet under lift. Spline boostar kp=10000/kd=200. Native saknade boost.

**Fix (alla 4 applicerade):** cleanup-loop, `frame_name="right_gripper"`, belt-freeze-tills-alla-levererade, finger-gains-boost.

**Resultat:** Native 0-1/4 → **3/4 deterministisk** (samma delivery-profil som spline, samma missade kub Cube_3). Next step för 4/4 är gemensam grip-timing-problem med spline — förmodligen kräver separat "hold-during-dwell"-fix eller FixedJoint-cheat för sista kuben.

## I-37 — cuRobo scene-obstacles (Cuboids från USD-bbox) bryter planning till 100% fail

**Symptom:** `_planner.update_world(SceneCfg(cuboid=[Table, Belt, Bin]))` före varje `plan_pose()`-anrop → `RuntimeError: planning failed` på 100% av planeringar (12000+ ticks, 0 success).

**Measured:** Utan obstacles: 4 cykler genomförs OK (även om 0/4 levererade pga arm sweep). Med 3 statiska Cuboids (table 2×1×0.75m, belt 1.6×0.3×0.1m, bin 0.3×0.3×0.15m) i robot-base-frame → cuRobo's trajopt ger upp.

**Candidates:**
(a) Bbox-cuboids täcker robotens egna länkar i hemkonfig → "self-collision" via scene
(b) Bbox-cuboids täcker target descend-zon (panda_hand vid z=0.98 hamnar inom belt-cuboid plus margin)
(c) Quaternion (1,0,0,0) i base frame är fel om base är roterad 90° around Z
(d) Inset-by-1cm är otillräckligt; arm-länkar har bredd 5-10cm

**Root cause (osannolikheter):** Mest sannolikt (b) — belt-cuboid är 10cm hög runt z=0.10 (base), och arm måste descenda igenom det zonen. cuRobo's collision-check rejecterar varje sample som klipper belt-volymen.

**Fix försök (inte applicerade):**
- Substanstest: bara bin som obstacle (inte belt) → kuber ovanför belt borde nås
- Substanstest: gör belt VÄLDIGT tunn (1cm) så arm kan dyka under bin-rim
- Substanstest: använd Mesh istället för Cuboid för tighter envelope
- Alternativ: använd cuRobo's `disable_link_collision(["panda_hand", "panda_leftfinger", "panda_rightfinger"])` + bara stora kropp-länkar mot belt

**Residual:** cuRobo levererar 0/4 utan obstacles (arm sweep). Med obstacles 0/4 (planning fail). Båda lika dåliga. Realistisk fix kräver scene-obstacle-tuning timmar. Annan väg: använd cuRobo's `plan_grasp()` istället för `plan_pose()` — det API'et hanterar approach/lift/place native och gör interna safe-distance-beräkningar. FAS 6e+.

## I-38 — cuRobo collision-aware planning blockerad av Warp 1.8.2 → 1.9 API-break

**Symptom:** `_planner.update_world(SceneCfg(cuboid=[...]))` → exception "Couldn't find function overload for 'is_obs_enabled' that matched inputs with types: [curobo._src.geom.data.data_cuboid.CuboidDataWarp, int32, int32]".

**Measured:** Tomma scener (`SceneCfg.create({})`) fungerar. Så fort en Cuboid läggs in → kollisionsknernen `swept_sphere_obstacle_collision_kernel` kraschar i Warp 1.8.2.

**Root cause:** Warp 1.8.2 (Kit's bundled version) registrerar inte cuRobo-typer som `CuboidDataWarp` korrekt. Warp 1.9+ ändrade typ-registrerings-API så att custom dataclasses kan användas i `wp.func`-signaturer. cuRobo v0.7+ skrev mot 1.9-API.

**Försök:** (a) `sys.path.insert` med `/site-packages/warp/` (1.11) — Kit's `OmniFinder` meta_path-finder routar `import warp` till bundled 1.8.2 oavsett path-prio. Kan inte överrida från Python-nivå. (b) cuda_graph=False — har ingen påverkan; collision-kernel-mismatch är runtime-typ-check.

**Status:** **cuRobo's collision-aware planning är OANVÄNDBAR i Kit 5.1 utan Warp-uppgradering.** plan_pose() utan obstacles fungerar perfekt (3mm precision, 0.5s/plan). Med obstacles → 100% planning-fail. Workaround: hög lift-höjd (`h1 = max(targets) + 0.4m`) så arm sveper högt över belt — armens kropp hamnar över kub-zonen. Men cubes greppas fortfarande inte (separat issue I-39).

**Real fix kräver:** uppgradera Kit's omni.warp.core extension till 1.9+. Risk: kan bryta Isaac Sims warp-kerneller. Alternativ: pinna cuRobo till en pre-v0.7-version som använder gamla Warp-API:t (kanske curobo 0.6.x).

## I-39 — cuRobo: hand når mål inom 3mm men cube greppas inte (mystery)

**Symptom:** cuRobo target_source kör 4 cykler utan fel. `panda_hand` slutar vid drop-target (0, -0.4, 0.95) inom 2-3mm. Men 0/4 kuber i bin. Fingrarnas grip-close fires efter pre-settle 0.8s + post-grip dwell 2.5s. Samma fingrar (kp=10000), samma frame (panda_hand), samma target-z (cube_z + 0.105m) som spline (3/4).

**Hypoteser (oprövade):**
(H1) cuRobo's interpolated_plan slutar vid `traj[-1]` som ÄR IK-perfekt, men `_apply_arm_joints(traj[-1])` skickar joint-positions som PD-drivs mot. PD-residual-velocity vid grip-tid → hand vibrerar 1-2mm vid grip → fingrarna missar.
(H2) cuRobo's `panda_hand` frame DEFINITION i franka.yml skiljer subtle från Lula's panda_hand definition i lula_franka_gen.urdf. 5mm Z-skift kan lägga fingertoppar over/under kub.
(H3) Hand orientation quat ((0, 0.707, 0.707, 0) i base frame) rotaterar fingrarna i fel klang-vinkel relativt kub. Cube är symmetrisk så det borde inte spela roll, men maybe.
(H4) Belt physics: även frusen belt kan generera mikro-rörelse på cubes (PhysX awake threshold). Spline är snabbare, missar mindre. cuRobo's slowmo-cykel ger mer drift-tid.

**Försök:** Längre dwells — ingen påverkan. Höjd lift — ingen påverkan på grip. Same params som spline — fortfarande 0/4.

**Real fix kräver:** djup probning av actual-vs-planned-pose error per tick under PICK-fasen. Eller: byt strategi — använd cuRobo för LIFT/TRANSIT/RETREAT (där collision-avoidance hade mattat ifall det fungerade), och spline-IK för PICK/DROP precision-segment. Hybrid arkitektur. ~3-4h work. FAS 6f framtida.

## Candidate upstream bugs

- `ArticulationController.apply_action` should reject `joint_positions` containing any None element, not just `joint_positions is None`.
- `PickPlaceController` phase-2 inertia-wait returns null action — should be no-op rather than fake action.
- `RMPFlowController.reset()` uses snapshot base pose that may be invalid if called before physics handles exist.
- `add_proximity_sensor` trigger callback unlatching unreliable.

## Recurrence patterns

Several symptoms trace to the same root: **Kit RPC exec_sync doesn't
replicate the full `world.reset()` chain.** Whatever lives behind
`SimulationContext.reset()` → `SimulationManager.initialize_physics()` →
`Scene._finalize()` + `Scene.post_reset()` is assumed by most Isaac Sim
controller code. Embedded integrations must reproduce it piece by
piece or accept silent half-initialization.

## Tooling built during investigation

- `scripts/qa/diagnose_scene.py` — time-series scene state (joints, cubes, EE, controller phase) — 3-sample default, prints motion verdicts.
- `scripts/qa/motion_observer.py` — continuous physics-step subscription logging joint positions/targets, EE trajectory, cube positions, finger gaps, sensor/belt state to JSONL. Start/stop/summarize modes.
