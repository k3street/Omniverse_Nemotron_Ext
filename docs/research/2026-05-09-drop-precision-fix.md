# cuRobo Drop-Precision Fix — UR10 Multi-Cube Canonicals (CP-81/82/84/85)

Date: 2026-05-09  
Scope: `service/isaac_assist_service/chat/tools/tool_executor.py`, `_gen_pick_place_curobo` code block (~lines 32347–33368)

---

## 1. Trajectory Accuracy Diagnosis

### 1.1 cuRobo Pipeline Recap

`_build_segments` (line 33153) generates 7 cuRobo segments per cube cycle:

| Seg | Goal world-XYZ | `action_after` | Notes |
|-----|---------------|----------------|-------|
| S1  | `[cube_x, cube_y, h1]` | None | approach above cube |
| S1.5 | `[cube_x, cube_y, cube_z + 0.18]` | None | mid-height pick |
| S2  | `[cube_x, cube_y, pz]` | close | descend + grip |
| S3  | `[cube_x, cube_y, h1]` | None | lift |
| S4  | `[drop_x, drop_y, h1]` | None | transit |
| S4.5 | `[drop_x, drop_y, drop_z + 0.18]` | None | mid-height drop |
| S5  | `[drop_x, drop_y, drop_z]` | open | descend + release |

Each segment is solved independently via `_plan_to_world_point` (line 32977), which calls `_planner.plan_pose(goal, start, max_attempts=3)`. The world target is first converted to robot-base frame via `_world_to_base` (line 32840), then sent as a `GoalToolPose(tool_frames=[_TOOL_FRAME], ...)`.

### 1.2 The IK Tolerance Gap

The planner is configured with (lines 32741–32744):

```python
_pcfg = MotionPlannerCfg.create(
    ...
    position_tolerance=0.003,  # 3 mm allowed residual
    orientation_tolerance=0.05,
)
```

cuRobo considers a plan **successful** when the final EE position is within `position_tolerance` = 3 mm of the target. However, the success check at line 32977–32991 only tests `res.success[0, 0].item()` — a boolean. If cuRobo declares success at a joint config that puts `tool0` at `(drop_x + 0.003, drop_y, drop_z)` that is technically within tolerance, the trajectory endpoint is accepted without any FK verification against the actual USD world position of `tool0`.

**The critical gap**: cuRobo plans for the `tool0` frame. For UR10, `tool0` is **not** the suction contact point. The suction cup hangs off `ee_link/suction_cup`, and `_grip_close` anchors the FixedJoint at `ee_link` (line 33041), not `tool0`. The distance from `tool0` to `ee_link` in the UR10 USD is approximately 0–5 mm (they share the same pose in the standard UR10 USD from Isaac assets). However, the **suction_cup** child prim sits approximately 0.158 m along the local +X axis of the `wrist_3_link`/`tool0` frame — which, during a top-down approach (end-effector pointing -Z in world), becomes a lateral XY offset, not a vertical Z offset.

This is explicitly acknowledged in the code comment (lines 33161–33166):

```python
# UR10 tool0 → suction_cup tip: +0.158m in local +X (NOT +Z); during a
# top-down grasp this becomes a lateral world-XY offset, not vertical.
# The 0.158 here is wrong-but-trying — proper UR10 grasp wiring needs
# the GoalToolPose offset transformed by the planner's solved EE quat,
# which the current pipeline doesn't compute. Tracked for follow-up.
FL = 0.105 if ROBOT_FAMILY == "franka" else 0.0
```

For UR10, `FL = 0.0`, meaning the S5 goal is exactly `drop_pos` in world space, and cuRobo is asked to put `tool0` there. But cube attachment is at `ee_link`, which in the UR10 kinematic chain shares the same Z height as `tool0` — so at least vertically the target is correct. The 0.20–0.30 m XY shortfall is therefore **not** primarily caused by the suction_cup frame offset.

### 1.3 The Real Culprit: segment trajectory indexing vs PhysX PD convergence

In `_on_step` for the cuRobo path (line 33302–33320):

```python
cur_seg = segs[S["seg_idx"]]
elapsed = time.monotonic() - S["seg_start_t"]
traj = cur_seg["traj"]
mt = cur_seg["motion_time"]
T = traj.shape[0]

# Sample trajectory at elapsed/mt * (T-1)
idx = int(round(min(elapsed / mt, 1.0) * (T - 1)))
q7 = traj[idx]
_apply_arm_joints(q7)
```

The trajectory is sampled linearly in time at `~60 Hz` physics ticks. `_apply_arm_joints` (line 33124) issues `ArticulationAction(joint_positions=q7_arr, joint_indices=np.arange(_ARM_DOF))`. This is a **position setpoint** command to PhysX drive joints, not a direct position write.

PhysX PD drives track the setpoint with finite gains. When `mt` is short (cuRobo often returns `motion_time` of 1.5–3 s for a 0.3 m drop descent), the joint positions lag the setpoint by a finite amount determined by drive stiffness and damping. Boosted finger gains are set for Franka (line 32713), but **no explicit stiffness/damping boost is applied to UR10 arm joints**. With factory default PhysX drive gains on the UR10 USD (typically stiffness ~400 Nm/rad, damping ~40 Nm·s/rad), a 1.5 s descent of 0.18–0.30 m in joint space incurs tracking lag.

More critically: at the **end** of segment S5 (`elapsed >= mt`), the code does:

```python
_apply_arm_joints(traj[-1])  # line 33324 — pin to final config
pre_grip_settle = 0.8        # line 33326 — wait 0.8s
```

Then it fires `_grip_open()` (line 33311). The 0.8 s settle is for the arm to converge from wherever it physically is to `traj[-1]`. If `traj[-1]` corresponds to `tool0` at `drop_pos` in world, and the PD drive takes 0.8 s or longer to converge, then `_grip_open()` fires when EE is still short of the target.

**Measured discrepancy from CP-82 failure note**: `"Cube_1 picked but drops at y=-0.49 between Bin_red (-0.30) and Bin_blue (-0.55)"`. Bin_red center y = -0.30, cube lands at y = -0.49. Error = 0.19 m in Y. The transit from cube at y = +0.55 to bin_red at y = -0.30 is 0.85 m. An EE that is 0.8 s into a converge from mid-trajectory (say y = -0.10) toward y = -0.30 at default UR10 gains would plausibly stall at -0.49. This is consistent with the PD convergence hypothesis.

### 1.4 Additional Factor: `_world_to_base` transform when base is non-identity

UR10 is teleported to `[0, 0, 0.75]` (world Z = 0.75). `_usd_pos = [0, 0, 0.75]`, `_usd_quat = [1, 0, 0, 0]` (no yaw rotation). `_world_to_base(xyz_world)` computes `p_rel = xyz_world - [0, 0, 0.75]`. For `drop_pos = [0.5, -0.30, bin_top]` where `bin_top = 0.75 + 0.075 + 0.05 ≈ 0.875`, `p_base = [0.5, -0.30, 0.125]`. This transformation looks correct for a straight-up mounted robot. **No transform bug here**.

### 1.5 Segment advancement timing: S4.5 → S5 advance is too fast

S4.5 is a mid-height transit (`action_after=None`). Per the logic at line 33325:

```python
_is_grip_seg = cur_seg["action_after"] in ("close", "open")
pre_grip_settle = 0.8 if _is_grip_seg else 0.0
```

For S4.5 (`action_after=None`), `pre_grip_settle = 0.0`. The segment advances to S5 immediately when `elapsed >= mt + 0.0 + 0.0`. This means S5 starts when the arm has `motion_time(S4.5)` elapsed from S4.5 start — but if S4.5 trajectory is short (0.5–1.0 s), the arm may be physically mid-air, driven toward `h_mid_drop`, and S5 immediately commands descent to `drop_pos`. The cuRobo planner chains segments: the start joint config for S5 is the *planned* end of S4.5 (`traj[-1]` from S4.5), not the *actual* physical joint positions at S4.5 end. If the arm hasn't converged to S4.5's final config, S5's initial trajectory already diverges from the arm's real state at tick 0.

---

## 2. Three Candidate Fixes, Ranked by Complexity

### Fix A — Extend pre_grip_settle for S5 + extend post_grip dwell (LOW complexity)

**Root cause addressed**: PD convergence lag at drop.  
**Change**: Increase `pre_grip_settle` from 0.8 s to 2.5 s for the S5 open segment, and increase `post_grip` dwell from 1.0 s to 1.5 s.  
**Lines affected**: ~33325–33316  
**Risk**: Makes each cycle longer (~3.5 s slower), but all 4 canonicals share the same timing. Cycle time for 2-cube canonicals goes from ~30 s to ~37 s per cycle, well within 180 s budget.  
**Estimated unlock**: 2–3 / 4 canonicals. CP-84/85 (single-cube) should unlock with high confidence. CP-81/82 (two-cube) depend also on FixedJoint propagation not degrading.

### Fix B — EE arrival gate on S5: defer grip_open until tool0 XY is within tolerance (MEDIUM complexity)

**Root cause addressed**: Release fires before EE physically arrives at drop_pos.  
**Change**: Mirror the spline-handler's proximity gate (lines 32227–32234) in the cuRobo S5 segment. Hold `grip_done=True` / delay `_grip_open()` until `_world_pos(S["picked_path"])[:2]` is within 0.08 m of `drop_pos[:2]`. Cap the hold at `mt + 4.0 s` to prevent infinite hang.  
**Lines affected**: `_on_step` S5 grip trigger block (~line 33327–33312).  
**Risk**: If cuRobo's final config genuinely can't place `tool0` within 0.08 m (IK near-miss), the grip hold fires the cap and cube drops short — same behavior as today but with a longer window. Cap prevents stuck cycle.  
**Estimated unlock**: 3 / 4 canonicals. CP-81/82/84/85 all show 0.19–0.30 m error, so the cube is within cap range.

### Fix C — FK verification + re-plan delta correction (HIGH complexity)

**Root cause addressed**: cuRobo IK residual + PD tracking, together.  
**Change**: After executing each segment, read actual arm joint positions from `franka.get_joint_positions()`, compute FK for `tool0` frame using USD xform chain (`ComputeLocalToWorldTransform`), compare to intended goal, issue a correction plan if error > 0.015 m.  
**Lines affected**: `_on_step` segment-done block, `_build_segments`, new `_correct_to_goal` helper (~40 new lines).  
**Risk**: Adds one cuRobo plan_pose call per segment correction (0.5 s CPU on warm CUDA graph). May cause oscillation if correction threshold is too tight. FK via USD xform is already used in `_world_pos` — same mechanism, reliable.  
**Estimated unlock**: 4 / 4 canonicals. Directly addresses both IK residual and PD tracking lag.

---

## 3. Highest-ROI Patch: Fix B — EE Arrival Gate on S5

Fix B has the best effort-to-unlock ratio. It requires ~25 lines of change in `_on_step` and directly mirrors the proven spline-path logic. Fix A is 5 lines but only covers settle time; Fix C takes 40+ lines and introduces a new planning call.

Patch targets `_gen_pick_place_curobo` inside `tool_executor.py`. The relevant section is the `_on_step` executing block, segment S5 handling, approximately lines 33302–33320:

```python
# ── PATCH: Fix B — EE arrival gate for S5 (drop segment) ──────────────
# Replace the existing segment-done block inside `if S["mode"] == "executing":`
# The change is scoped to the "open" action_after trigger only.

            if elapsed >= mt:
                _apply_arm_joints(traj[-1])
                _is_grip_seg = cur_seg["action_after"] in ("close", "open")
                pre_grip_settle = 0.8 if _is_grip_seg else 0.0
                if not cur_seg["grip_done"] and elapsed >= mt + pre_grip_settle:
                    if cur_seg["action_after"] == "close":
                        _grip_close()
                        cur_seg["grip_done"] = True
                    elif cur_seg["action_after"] == "open":
                        # --- BEGIN FIX B ---
                        # Gate release on EE being physically close to drop_pos.
                        # cuRobo trajectory time ends when the planner thinks EE
                        # has arrived, but PD drive convergence lags 0.3-1.5s on
                        # UR10 default gains. Releasing early drops cube on table.
                        _can_open = True
                        _dp = S["segments"] and S["seg_idx"] < len(S["segments"]) and \
                              S.get("picked_path") and S["picked_path"]
                        if ROBOT_FAMILY in ("ur10", "ur10e") and S.get("picked_path"):
                            try:
                                _cube_p = _world_pos(S["picked_path"])
                                # Use the S5 goal world-xyz from goals list index 6
                                # which equals the last segment's planned target.
                                # We reconstruct it from the stored segments via
                                # FK of traj[-1]: use UsdGeom xform on ROBOT_PATH/tool0.
                                _tool0 = stage.GetPrimAtPath(f"{ROBOT_PATH}/tool0")
                                if _tool0 and _tool0.IsValid():
                                    _t0w = UsdGeom.Xformable(_tool0).ComputeLocalToWorldTransform(0)
                                    _t0_pos = np.array([float(_t0w.ExtractTranslation()[i]) for i in range(3)])
                                    # drop_pos stored in _build_segments goal[6] = (drop_x, drop_y, drop_z)
                                    # Retrieve from the segment's first tick target (not stored directly).
                                    # Use cube world pos as proxy: if cube attached via FixedJoint,
                                    # its XY tracks EE. Compare cube XY vs planned drop_pos XY.
                                    if _cube_p is not None:
                                        # Recover drop_pos from S5 goal: stored as last element of
                                        # goals[] which is the last segs entry's world target.
                                        # We compute it from _bin_drop_pos() since it's deterministic.
                                        _target_dp = _bin_drop_pos(S["picked_path"])
                                        if _target_dp is not None:
                                            _xy_err = float(np.linalg.norm(
                                                np.array(_cube_p[:2]) - np.array(_target_dp[:2])
                                            ))
                                            if _xy_err > 0.08:
                                                _can_open = False  # hold — EE not yet at drop
                                            # Safety cap: force open after 4s past mt to prevent infinite hold
                                            if elapsed > mt + pre_grip_settle + 4.0:
                                                _can_open = True
                            except Exception:
                                pass  # on any error, allow open (fail-safe)
                        # --- END FIX B ---
                        if _can_open:
                            _grip_open()
                            cur_seg["grip_done"] = True
                # Post-grip dwell so finger drives reach final position
                post_grip = 2.5 if cur_seg["action_after"] == "close" else \
                            (1.0 if cur_seg["action_after"] == "open" else 0.0)
                if elapsed >= mt + pre_grip_settle + post_grip:
                    if cur_seg["grip_done"]:  # only advance once grip fired
                        S["seg_idx"] += 1
                        S["seg_start_t"] = time.monotonic()
```

**Critical note on segment advancement**: The existing code at line 33317–33319 advances `seg_idx` based purely on `elapsed >= mt + pre_grip_settle + post_grip`. This must be conditioned on `grip_done = True` to prevent advancing past S5 before the gate fires. The patch above adds that condition.

### Concrete line-level edit in tool_executor.py

The patch replaces lines 33327–33319 (the `action_after == "open"` branch and segment advancement) in `_gen_pick_place_curobo`. The actual text is inside a Python f-string template, so braces are doubled. The net addition is approximately 25 lines inside `_on_step`.

```
Lines to replace: 33307-33319 (current "open" branch + seg advance)

Old (3 lines for the open branch):
    elif cur_seg["action_after"] == "open":
        _grip_open()
    cur_seg["grip_done"] = True

New: replace with the gated version above (25 lines),
     and add `if cur_seg["grip_done"]:` guard on seg_idx advance.
```

---

## 4. Estimated Unlock Count

| CP | Controller | Failure mode | Fix B impact |
|----|-----------|--------------|-------------|
| CP-81 | cuRobo, UR10, 2 cubes | Drops ~0.20 m short; z=0.775 table not bin | Unlocks: gate holds until cube XY within 0.08 m of bin center |
| CP-82 | cuRobo, UR10, 2 cubes, color-routing | Drops at y=-0.49, between two bins | Unlocks: cube reaches Bin_red y=-0.30 before release |
| CP-84 | builtin, UR10, stacking (drop_target=[0.5,-0.4,0.825]) | Drops at z=0.775 table top, 0.05 m below BaseCube top | Partial: CP-84 uses builtin handler, not cuRobo. Fix B targets curobo code path only. |
| CP-85 | builtin, UR10, single cube, color-routing | Similar to CP-84; "smaller Bin_red + extra Bin_blue caused descent issue" | Partial: builtin handler; Fix B doesn't apply. |

**CP-84 and CP-85 use `target_source="builtin"`**, not cuRobo. Their drop-precision issue is in the `_gen_pick_place_builtin` / native RmpFlow PickPlaceController path, which doesn't use `_build_segments` or `_on_step` from the cuRobo template.

**Revised estimate**:
- Fix B directly unlocks **CP-81 and CP-82** (cuRobo path): **2 of 4**.
- CP-84 / CP-85 need a parallel fix in the builtin handler or conversion to `target_source="curobo"`.

**For CP-84/85**: The builtin PickPlaceController uses RmpFlow internally and calls its own placing_position descent. Converting them to `target_source="curobo"` + applying Fix B would likely unlock all 4. Alternatively, the existing position-gate in `_grip_open()` at line 32227–32234 in the spline handler template already has the right logic — the same pattern needs to be applied to the builtin handler's FixedJoint release path at line ~29679.

### Summary

| Fix | Lines changed | Unlocks (direct) | Unlocks (with builtin parallel) |
|-----|--------------|-----------------|--------------------------------|
| A (longer settle) | ~5 | 1–2 | 2–3 |
| B (XY arrival gate, cuRobo) | ~25 | 2 | 4 |
| C (FK re-plan) | ~45 | 3–4 | 4 |

Fix B + parallel application to builtin handler's FixedJoint release = **4 / 4** with ~35 lines total.

---

## 5. Key Code Locations (for implementation)

- `_gen_pick_place_curobo`: lines 32347–33368
- `_build_segments`: lines 33153–33196
- `_on_step` cuRobo executing block: lines 33256–33320
- `_plan_to_world_point`: lines 32977–33000
- MotionPlannerCfg construction: lines 32736–32744
- UR10 builtin FixedJoint release gate (existing, spline handler): lines 32225–32241
- `_apply_arm_joints`: lines 33124–33151
