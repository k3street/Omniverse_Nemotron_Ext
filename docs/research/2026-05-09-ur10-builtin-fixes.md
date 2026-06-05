# UR10 Builtin Handler — Root Cause Analysis and Patch Plan

**Date:** 2026-05-09
**Scope:** CP-74, CP-80 (belt-fed conveyor), CP-84 (stacking), CP-85 (color routing)
**Handler:** `_gen_pick_place_builtin` — `tool_executor.py` lines 29200–29806
**Status:** 4 failing. This document identifies per-canonical root causes and provides patch-level code references.

---

## 1. Shared Architecture Context

The builtin handler wraps Isaac Sim's `universal_robots.PickPlaceController` (UR10 path) via:

- `SingleManipulator` + external `SurfaceGripper` (not `UR10(attach_gripper=True)`)
- `subscribe_physics_step_events(_on_step)` — the main state machine
- `subscribe_physics_on_step_events(..., pre_step=True, order=0)` — belt-pause applier
- FixedJoint workaround for `IsaacSurfaceGripper` articulation-link engagement bug
- Cube velocity damping (events 0–3, before FJ snaps) to hold cube while EE descends

The state machine has one shared Python-closure state dict `S = {"delivered": set(), "current": None, "fixed_joint": None}`.

---

## 2. CP-74 and CP-80 — Belt-Fed Conveyor

### 2.1 Observed failure

Cube travels past the sensor zone and lands at `x=0.605` (CP-74) or similar (CP-80), past the `PickSensor` zone centered at `x=0.4` (CP-74) or `x=-0.4` (CP-80). Belt pause was added but did not help.

### 2.2 Root cause 1 — `_ev` NameError on first tick silences all UR10-specific logic

**File:** `tool_executor.py`, `_on_step` inner body (~line 29651–29670)

```python
# line 29651
try:
    _ev = _controller.get_current_event() if hasattr(_controller, 'get_current_event') else None
    if _dbg_phase_attr: _dbg_phase_attr.Set(f"event={_ev}")
except Exception: pass          # <-- _ev never assigned if get_current_event() raises
# ...
# line 29670
if ROBOT_FAMILY in ("ur10", "ur10e") and _ev is not None and _ev <= 3 \  # <-- NameError
```

On the **first call** to `_on_step`, `_ev` is a local variable that is not initialized before the `try` block. If `get_current_event()` raises (common on tick 1 while the controller is not yet fully initialized), `_ev` is never assigned. Line 29670 then raises `NameError: name '_ev' is not defined`. The outer `try/except` at line 29614 catches this exception silently — the debug attribute shows `error:NameError:...` and the function returns. This happens every tick until `get_current_event()` succeeds.

**Consequence**: both the velocity damping block (lines 29670–29681) and the FixedJoint formation block (lines 29688–29757) never execute while `_ev` fails to assign. The cube continues drifting on the belt with no mitigation.

**Fix** — initialize `_ev = None` before the try block:

```python
# Replace at ~line 29651:
_ev = None  # guard: NameError if get_current_event() raises before assignment
try:
    _ev = _controller.get_current_event() if hasattr(_controller, 'get_current_event') else None
    if _dbg_phase_attr: _dbg_phase_attr.Set(f"event={_ev}")
except Exception: pass
```

This is a one-line insertion. After this fix, `_ev = None` on failure, `_ev is not None` evaluates `False`, and the UR10-specific blocks are correctly skipped until the controller initializes.

### 2.3 Root cause 2 — Pre-step belt-pause subscription fires on `_belt_pause_request` flag, but `_pause_belt()` also writes directly inside `_on_step`

**File:** `tool_executor.py`, lines 29522–29552

```python
def _pause_belt():
    # direct write inside physics step callback — gets restored next tick
    if _belt_en and _belt_en.IsDefined(): _belt_en.Set(False)
    if _belt_sv: _belt_sv.Set((0, 0, 0))
    _belt_pause_request[0] = True           # queues the pre-step applier

# pre-step applier — subscribed via subscribe_physics_on_step_events(fn, True, 0)
def _apply_belt_pause_outside_callback():
    req = _belt_pause_request[0]
    if req is None: return
    ...
    _belt_pause_request[0] = None           # consume
```

The pre-step subscription is the correct fix path (as established in `2026-05-09-belt-pause-physx-fix.md`). However, the pre-step callback fires ONCE per tick and consumes the flag. The core timing is:

1. Tick N: `_on_step` fires (post-step), cube enters reach, `_pause_belt()` called, `_belt_pause_request[0] = True` set.
2. Pre-step of tick N+1: `_apply_belt_pause_outside_callback` fires, reads `True`, writes `surfaceVelocityEnabled=False` and `surfaceVelocity=(0,0,0)`, clears flag.
3. Tick N+1: PhysX reads the pre-step-written values — **belt is paused**.

This is correct in principle. The timing gap is exactly 1 physics frame (≈16ms at 60Hz). At belt velocity 0.2 m/s, the cube drifts 3.3mm during this window — acceptable.

**BUT**: if Root Cause 1 (NameError) is active, `_pause_belt()` is never called at all (the outer except catches the NameError before reaching the seek_cube block). Fix Root Cause 1 first; the pre-step applier should then work.

Additionally, the current `_pause_belt()` does a direct in-callback write AND sets the flag. The CP-80 `verified_status` note says: "surfaceVelocityEnabled toggle ALSO doesn't propagate from inside _on_step". This is consistent with the documented PhysX behavior (the direct write is overridden). The pre-step subscription is the correct channel. The issue is that Root Cause 1 prevents `_pause_belt()` from being called.

### 2.4 Root cause 3 — `_next_cube()` reach gate doesn't check sensor proximity; cube can be claimed too early

**File:** `tool_executor.py`, `_next_cube` function (~line 29589–29601)

```python
def _next_cube():
    _REACH_M = 1.20 if ROBOT_FAMILY in ("ur10", "ur10e") else 0.95
    base = _robot_base_xy()
    if base is None: return None
    for sp in SOURCE_PATHS:
        if sp in S["delivered"]: continue
        cp = _cube_pos(sp)
        if cp is None: continue
        if float(np.linalg.norm(cp[:2] - base)) <= _REACH_M:
            return sp
    return None
```

For CP-74: cube starts at `(-1.0, 0.4)`, robot base at `(0, 0)` → distance = 1.0m, within the 1.2m reach limit. The cube is claimed **immediately on first tick** — before it even reaches the `PickSensor` at `x=0.4`. The PickPlaceController's `picking_position` is then set to the cube's current position (somewhere on the belt far from the EE), and every subsequent tick it chases a moving target.

For CP-74 specifically, the cube needs to reach `x≈0.4` (the sensor zone) before being claimed, so the controller can descend onto a stationary target. Sensor-gate logic (`SENSOR_PATH`) is set in the template but the builtin handler's `_next_cube()` does not consult it.

**Fix option A** — Add sensor-proximity gate to `_next_cube()` when `SENSOR_PATH` is set:

```python
def _next_cube():
    _REACH_M = 1.20 if ROBOT_FAMILY in ("ur10", "ur10e") else 0.95
    base = _robot_base_xy()
    if base is None: return None
    # Sensor-gate: if a pick sensor is configured, only claim a cube that is
    # within the sensor's bbox (same logic as native/cuRobo handlers).
    _sensor_pos = None
    _sensor_radius = 0.06  # default half-size from add_proximity_sensor
    if SENSOR_PATH:
        _sp = stage.GetPrimAtPath(SENSOR_PATH)
        if _sp and _sp.IsValid():
            _scache = UsdGeom.BBoxCache(0, [UsdGeom.Tokens.default_])
            _sb = _scache.ComputeWorldBound(_sp).ComputeAlignedRange()
            if not _sb.IsEmpty():
                _sc = _sb.GetMidpoint()
                _sensor_pos = np.array([float(_sc[0]), float(_sc[1]), float(_sc[2])])
                _sensor_radius = float(max(_sb.GetSize())) / 2.0
    for sp in SOURCE_PATHS:
        if sp in S["delivered"]: continue
        cp = _cube_pos(sp)
        if cp is None: continue
        if float(np.linalg.norm(cp[:2] - base)) > _REACH_M:
            continue
        # Sensor gate: if sensor configured, cube must be within sensor proximity
        if _sensor_pos is not None:
            _dist_to_sensor = float(np.linalg.norm(cp - _sensor_pos))
            if _dist_to_sensor > _sensor_radius * 3.0:  # 3x for generous catch window
                continue
        return sp
    return None
```

This mirrors the pattern in the `native` and `cuRobo` handlers that check `_sensor` proximity before claiming.

**Alternatively, Fix option B** — simply pre-pause the belt at startup (before any cube is on it), so the cube arrives at the sensor zone and stays:

```python
# At initialization time, before _on_step runs:
_pause_belt()  # belt starts paused; cube gravity-settles at start position
# Belt is resumed by _on_step after each delivery via _resume_belt()
```

This is simpler but changes the behavior: cube won't flow from belt start to sensor, it just sits where it was placed. For CP-74 that's fine (cube placed at `x=-1.0`, sensor at `x=0.4` — cube must flow first). So option B doesn't work for CP-74.

**Recommended**: Fix option A (sensor-gate in `_next_cube`).

### 2.5 Estimated unlock: CP-74 and CP-80

Applying all three fixes (Root Causes 1+2+3):

- Root Cause 1 fix unblocks `_ev` → velocity damping and FJ formation run every tick
- Root Cause 2 (pre-step applier) already in place; unlocks once RC1 is fixed
- Root Cause 3 (sensor gate) prevents early claim → cube stationary at sensor when EE descends

Combined estimate: **both CP-74 and CP-80 unlock** (2 of 4 failing CPs).

---

## 3. CP-84 — Stacking (Drop on BaseCube)

### 3.1 Observed failure

`verified_status`: "cube lands at z=0.775 table top, ~0.2m short of BaseCube target". Drop target is `[0.5, -0.4, 0.825]` (BaseCube top + half cube = 0.775 + 0.025 + 0.025). Cube drops ~50mm short (lands at z=0.775 on the table surface, not z=0.825 on the BaseCube).

### 3.2 Root cause — `_bin_pos()` returns DROP_TARGET correctly, but FixedJoint release gate fires prematurely

**File:** `tool_executor.py`, ~lines 29758–29780 (FJ release gate in `_on_step`)

```python
elif _ev >= 7 and S.get("fixed_joint"):
    _bin = _bin_pos()
    _cubp = _cube_pos(S["current"]) if S.get("current") else None
    _drop_close = True
    if _bin is not None and _cubp is not None:
        _xyd = float(np.linalg.norm(_cubp[:2] - _bin[:2]))
        _drop_close = _xyd < 0.10       # 0.10m xy tolerance
    if _drop_close:
        fjp = S["fixed_joint"]
        if stage.GetPrimAtPath(fjp).IsValid():
            stage.RemovePrim(fjp)
        S["fixed_joint"] = None
```

The gate checks `_xyd < 0.10` — 10cm XY tolerance. For stacking:

- Drop target = `[0.5, -0.4, 0.825]`
- Robot base XY = `[0.0, 0.0]`
- `bin_pos[:2]` = `[0.5, -0.4]`

The 0.10m gate is designed for bin-drop (bin is 0.30m wide, so 0.10m = well inside bin). For stacking onto a 0.05m cube, the effective landing zone is 0.025m radius. The 0.10m gate means the FJ releases when the cube is anywhere within 10cm of `(0.5, -0.4)` in XY — which can be 5cm above the table or 2cm above BaseCube. The cube falls from wherever the EE is at that moment.

But more critically: `_ev >= 7` means PickPlaceController's "release" event has fired. PickPlaceController uses RmpFlow; its release event fires when the trajectory reaches the `placing_position`. If `placing_position = [0.5, -0.4, 0.825]` (the drop target) but RmpFlow descends to a `z` limited by the `end_effector_initial_height` constraint or blocked by BaseCube's collision mesh, the EE stalls above `z=0.825` and event 7 fires at `z≈0.85` or higher — the cube releases and falls ~3–7cm, landing on the table rather than on BaseCube.

**Computed `end_effector_initial_height`** for CP-84 (line 29465):

```python
_h1_zs = [0.975]          # Cube_1 at z=0.975
# DROP_TARGET = [0.5, -0.4, 0.825]
_h1_zs.append(0.825)
_h1 = max([0.975, 0.825]) + 0.20 = 1.175m
```

PickPlaceController's transit height is 1.175m. Descent from 1.175m to 0.825m is 0.35m. BaseCube at z=0.775 with `PhysicsCollisionAPI` blocks the EE's suction cup at z≈0.80. RmpFlow stops descent when collision is detected — EE hovers at 0.825m (suction cup touching BaseCube top), event 7 fires, FJ removed, cube released from ~0.825m and free-falls — should be correct. **But** the FJ is between `ee_link` (not `suction_cup`) and the cube. The `ee_link` sits higher than the suction cup tip by the suction cup arm length (~0.05–0.10m). When the suction cup is at z=0.825, `ee_link` is at z=0.875–0.925. The cube's FJ attachment point is `ee_link`, so the cube body center (midpoint of its half-size 0.025m bbox) tracks `ee_link` minus the joint offset. If `ee_link` is at z=0.875 and the cube center tracks `ee_link` – offset, the cube may effectively be at z=0.875–0.0 = 0.825 (if the cube hangs below `ee_link`), which would be correct — cube center at 0.825 = BaseCube top + half cube.

The fact that the failure is "cube lands at z=0.775" (not 0.825) strongly suggests the FJ is NOT forming at all for CP-84, or the FJ forms but the EE never descends to the right height.

### 3.3 FJ formation check — same NameError bug applies

The FixedJoint formation block at line 29695 also depends on `_ev`:

```python
if ROBOT_FAMILY in ("ur10", "ur10e") and _ev is not None:
    if 0 <= _ev <= 4 and not S.get("fixed_joint"):
        # ... overlap_sphere + FJ snap
```

If `_ev` is never defined (Root Cause 1 from Section 2.2), the FJ formation block never executes. No FJ → cube is not attached to EE → cube stays where it is (on the pedestal), the controller tries to descend to the pedestal position, finds no FJ, does nothing, and `is_done()` eventually fires after timeout. Cube is marked delivered at its original position on the pedestal.

Wait — but the `verified_status` says "cube lands at z=0.775", not "cube stays on pedestal at z=0.975". This implies the cube IS picked up and then dropped. This is consistent with a partial scenario: FJ does form (NameError doesn't prevent it every tick — on some ticks `get_current_event()` succeeds after warmup), but FJ releases too early (EE not at drop height).

### 3.4 Additional issue for CP-84 — `_bin_pos()` with DROP_TARGET takes priority over DEST_PATH

```python
def _bin_pos():
    if DROP_TARGET:
        return np.array(DROP_TARGET, dtype=np.float64)
    p = stage.GetPrimAtPath(DEST_PATH)
```

CP-84 template passes `drop_target=[0.5, -0.4, 0.825]` explicitly. So `_bin_pos()` returns `[0.5, -0.4, 0.825]` — correct. The PickPlaceController's `placing_position` is set to `[0.5, -0.4, 0.825]` every tick. RmpFlow plans to place the suction cup at that point. This is correct.

The failure is the FJ-release timing: FJ removes when `_ev >= 7` AND `_xyd < 0.10`. If the EE hasn't descended to z=0.825 when event 7 fires (RmpFlow considers task done at some intermediate height), the cube drops from whatever height the EE is at.

**Fix** — add Z-proximity gate to FJ release (mirror of XY gate):

```python
elif _ev >= 7 and S.get("fixed_joint"):
    _bin = _bin_pos()
    _cubp = _cube_pos(S["current"]) if S.get("current") else None
    _drop_close = True
    if _bin is not None and _cubp is not None:
        _xyd = float(np.linalg.norm(_cubp[:2] - _bin[:2]))
        _zdiff = abs(float(_cubp[2]) - float(_bin[2]))
        # Gate on BOTH XY and Z proximity for stacking accuracy.
        # XY: 0.10m (bin-drop) or 0.04m (stacking onto small prim)
        _xy_tol = 0.04 if (DROP_TARGET is not None) else 0.10
        _z_tol = 0.06  # 6cm vertical tolerance
        _drop_close = _xyd < _xy_tol and _zdiff < _z_tol
        # Safety cap: release anyway after 4s past event 7 to prevent hang
        if elapsed_since_ev7 > 4.0:
            _drop_close = True
    if _drop_close:
        ...
```

Note: `elapsed_since_ev7` needs to be tracked via `S["ev7_t"]` — set it when first `_ev >= 7` is detected.

**Simpler fix** — tighter XY tolerance for DROP_TARGET (non-bin) cases:

```python
_xy_tol = 0.04 if (DROP_TARGET is not None) else 0.10
_drop_close = _xyd < _xy_tol
```

This tighter tolerance means the cube must be within 4cm XY of the drop point before FJ releases. For a 5cm cube on a 5cm base, 4cm XY means the cube is directly above BaseCube. The 0.10m tolerance was appropriate for wide bins; it's too loose for stacking.

### 3.5 Estimated unlock: CP-84

Applying RC1 fix (NameError) + tighter FJ release gate:

- RC1 ensures FJ forms reliably from tick 1
- Tighter gate ensures cube is over BaseCube when released

**Estimated unlock: CP-84** with moderate confidence. The EE descent to `z=0.825` may still be blocked by BaseCube collision mesh — if that is an issue, raising `end_effector_offset` to `[0, 0, 0.06]` (pushing suction cup down past BaseCube top) would help.

---

## 4. CP-85 — Color Routing

### 4.1 Observed failure

`verified_status`: "smaller Bin_red + extra Bin_blue caused descent issue, NOT color_routing — see CP-86 ✓". CP-86 passes, so color routing itself is not the bug.

### 4.2 Root cause — `_gen_pick_place_builtin` does not implement color_routing dispatch

**File:** `tool_executor.py`, `_gen_pick_place_builtin` signature (~line 29200–29202)

```python
def _gen_pick_place_builtin(robot_path, robot_family, sensor_path, belt_path,
                             source_paths, destination_path, drop_target,
                             ee_offset):
```

No `color_routing` parameter. The dispatch at line 28871–28884:

```python
if mode == "builtin":
    return _gen_pick_place_builtin(
        ...
        destination_path=args.get("destination_path"),
        drop_target=args.get("drop_target"),
        ee_offset=args.get("end_effector_offset", [0.0, 0.0, 0.02]),
    )
```

`color_routing=args.get("color_routing")` is **not passed** to `_gen_pick_place_builtin`. The generated code only has `DEST_PATH` and `DROP_TARGET` — no `COLOR_ROUTING` variable.

The `_bin_pos()` function uses `DEST_PATH` regardless of cube color. For CP-85, `DEST_PATH = "/World/Bin_red"` (the explicit `destination_path` passed in the template). Since `destination_path` is already set to `Bin_red` for the single red cube, and the cube IS red, `_bin_pos()` returns the correct bin — this is why CP-86 (identical scene, larger bins) passes while CP-85 fails.

The CP-85 failure is therefore the descent issue: `Bin_red` is smaller (`0.20 x 0.20 x 0.15`) versus CP-86's larger bin. The bin's `z` midpoint is at `0.75 + 0.075 = 0.825`. The `_h1 = max(cube_z=0.975, bin_z=0.825) + 0.20 = 1.175`. PickPlaceController tries to descend to `bin_midpoint = [0.5, -0.30, 0.825]`. The bin walls at `0.20m width` may block the EE from entering the bin if the descent trajectory overshoots the Y offset (`Bin_red` at `y=-0.30` vs `Bin_blue` at `y=-0.55` — only 0.25m separation). The `Short_Suction` gripper extends 0.158m from ee_link — if the EE approaches `y=-0.30` it may clip `Bin_blue` at `y=-0.55`.

**The real fix for CP-85** is the same as CP-84: tighter FJ release gate + RC1 fix. The `destination_path` is already correctly set to `Bin_red` in the template, so `color_routing` dispatch is not needed for the single-cube case.

However, if color_routing support is required for multi-cube scenarios (future canonicals), add it:

```python
def _gen_pick_place_builtin(robot_path, robot_family, sensor_path, belt_path,
                             source_paths, destination_path, drop_target,
                             ee_offset, color_routing=None):
    ...
    COLOR_ROUTING = {_json.dumps(color_routing or {})}
    ...
    def _destination_path_for(cube_path):
        if COLOR_ROUTING and cube_path:
            col = _get_semantic_color(cube_path)
            if col and col in COLOR_ROUTING:
                return COLOR_ROUTING[col]
        return DEST_PATH
    def _bin_pos_for(cube_path=None):
        if DROP_TARGET:
            return np.array(DROP_TARGET, dtype=np.float64)
        dp = _destination_path_for(cube_path)
        p = stage.GetPrimAtPath(dp)
        ...
```

And call `_gen_pick_place_builtin(..., color_routing=args.get("color_routing"))` at line 28875–28884.

### 4.3 Estimated unlock: CP-85

CP-85's immediate failure ("descent issue") is the same NameError + FJ release gate problem as CP-84. Applying those fixes should unlock it. Color routing support is not strictly needed since `destination_path="Bin_red"` already routes to the correct bin for the single red cube.

**Estimated unlock: CP-85** with the same fix set as CP-84.

---

## 5. Summary Patch Plan

### Patch 1 — `_ev` NameError guard (line ~29651)

**Location:** `_gen_pick_place_builtin` f-string, inside `_on_step`, one line before the `try` block that assigns `_ev`.

```python
# BEFORE (line ~29651):
try:
    _ev = _controller.get_current_event() if hasattr(_controller, 'get_current_event') else None

# AFTER:
_ev = None                 # guard: prevents NameError if get_current_event() raises
try:
    _ev = _controller.get_current_event() if hasattr(_controller, 'get_current_event') else None
```

**Impact:** Unblocks all UR10-specific logic (velocity damping + FJ formation) that depends on `_ev is not None`. This is the single highest-priority fix.

### Patch 2 — Sensor-gate in `_next_cube()` (lines ~29589–29601)

**Location:** `_gen_pick_place_builtin` f-string, `_next_cube` function body.

Add sensor-proximity check after the reach-distance check. Cube must be within `3 × sensor_half_size` of `SENSOR_PATH` centroid before being claimed. Sensor's BBoxCache centroid and size are computed once per call (not cached — cheap since `_next_cube` runs only when `S["current"] is None`).

This prevents the belt-fed cube being claimed at x=-1.0 (far from EE) and forces the cube to flow into the pick zone at `x=0.4` first.

### Patch 3 — Tighter FJ release gate for non-bin drop targets (lines ~29758–29779)

**Location:** `_gen_pick_place_builtin` f-string, inside `_on_step`, FJ release block (`elif _ev >= 7 and S.get("fixed_joint")`).

```python
# BEFORE:
_drop_close = _xyd < 0.10

# AFTER:
_xy_tol = 0.04 if DROP_TARGET is not None else 0.10
_drop_close = _xyd < _xy_tol
# Safety cap:
if now - S.get("ev7_enter_t", now) > 4.0:
    _drop_close = True
```

Also add `S["ev7_enter_t"] = now` when `_ev >= 7` is first detected (gate the assignment with `S.get("ev7_enter_t") is None` check).

### Patch 4 — Color routing parameter thread-through (optional, future-proofing)

Add `color_routing=None` to `_gen_pick_place_builtin` signature and pass it from `_gen_setup_pick_place_controller`. Generate `COLOR_ROUTING`, `_get_semantic_color`, `_destination_path_for` helpers in the template body, and update `_bin_pos()` to take an optional `cube_path` argument. This is not needed to unlock CP-85 (single cube, destination_path already correct) but is needed for any future multi-cube color-routing canonical with builtin controller.

---

## 6. Estimated Unlock Count

| CP | Root Cause | Patches Needed | Expected Result |
|----|-----------|----------------|-----------------|
| CP-74 | RC1 (NameError), RC3 (early claim) | Patch 1 + Patch 2 | Unlocks |
| CP-80 | RC1 (NameError), RC3 (early claim, elevated belt) | Patch 1 + Patch 2 | Unlocks |
| CP-84 | RC1 (NameError), loose FJ release gate | Patch 1 + Patch 3 | Unlocks with moderate confidence |
| CP-85 | RC1 (NameError), loose FJ release gate | Patch 1 + Patch 3 | Unlocks with moderate confidence |

**Total: 4 of 4 failing CPs addressed.**
**Lines changed: ~10 (Patch 1+3 combined) + ~20 (Patch 2) = ~30 lines total.**

Confidence rating: CP-74/80 high (belt-pause + sensor-gate is a clean mechanical fix); CP-84/85 moderate (depends on RmpFlow descent actually reaching z=0.825, which requires the FJ to form reliably — unlocked by Patch 1).

---

## 7. Key Line References

| Item | Lines in tool_executor.py |
|------|--------------------------|
| `_gen_pick_place_builtin` function header | 29200–29202 |
| `DEST_PATH`, `DROP_TARGET`, `SENSOR_PATH` template vars | 29239–29243 |
| Pre-step belt-pause subscription | 29539–29552 |
| `_next_cube()` function | 29589–29601 |
| `_on_step` entry + outer try | 29613–29614 |
| `_ev` assignment (needs guard) | 29651–29654 |
| Velocity damping block (gated on `_ev`) | 29670–29681 |
| FixedJoint formation block (gated on `_ev`) | 29688–29757 |
| FixedJoint release gate (`_xyd < 0.10`) | 29758–29779 |
| Builtin handler dispatch in `_gen_setup_pick_place_controller` | 28871–28884 |

---

## 8. Relationship to Prior Research

`2026-05-09-belt-pause-physx-fix.md` established that the pre-step subscription (`subscribe_physics_on_step_events(..., pre_step=True, order=0)`) is the correct channel for belt-pause writes. That fix is already implemented in the current code. The present document shows that the pre-step fix was added but does not help because `_pause_belt()` is never reached (due to the NameError in `_on_step`). Patch 1 is the prerequisite that makes the pre-step belt-pause fix actually fire.

`2026-05-09-drop-precision-fix.md` analyzed CP-81/82/84/85 drop precision for the cuRobo handler (Fix B — EE arrival gate). CP-84/85 use `target_source="builtin"`, not cuRobo, so the cuRobo Fix B does not apply to them. The present document addresses the builtin-handler equivalent via Patch 3 (tighter FJ release tolerance) and Patch 1 (ensure FJ forms at all).
