# Silent-success audit — 7 _gen_pick_place_* handlers
**Date**: 2026-05-07
**Method**: live Kit RPC `/exec_sync` calls with intentionally-bad path
arguments. Verdict per handler: did `success=False` (correct — error
propagated) or `success=True` despite Traceback in output (silent-success bug).

## Results

| Handler | Verdict | Notes |
|---------|---------|-------|
| `_gen_setup_pick_place_ros2_bridge` | ✅ RAISES | `rmw_init_options_init` AMENT_PREFIX_PATH unset → loud failure |
| `_gen_pick_place_sensor_gated` | ✅ RAISES | `ParallelGripper.initialize()` raises on invalid `/World/NonExistentRobot` |
| `_gen_pick_place_native` | ✅ RAISES | `Stage.GetPrimAtPath(None)` Boost.Python.ArgumentError propagates |
| `_gen_pick_place_spline` | ✅ RAISES | same propagation as native |
| `_gen_pick_place_curobo` | ❌ SILENT | Traceback in output but `success=True` |
| `_gen_pick_place_diffik` | ❌ SILENT | Traceback in output but `success=True` |
| `_gen_pick_place_osc` | ❌ SILENT | Traceback in output but `success=True` |

## Action taken (initial)

- 4 verified-clean handlers added to `AUDITED_CLEAN` in
  `tests/test_tool_honesty_scan.py`
- Test now flags exactly the 3 buggy handlers (down from 7) — flags
  are now signal, not noise

## FIX SHIPPED 2026-05-07 (same-day update)

Top-of-handler pre-flight prim-existence check added to all 3 buggy
handlers. Verified via live Kit RPC: all three now return
`success=False` on bad inputs (was: `success=True` despite Traceback
in output).

Verified via `hard_instantiate_smoke_tests.py`: 6/6 fixtures pass —
CP-03 (which uses cuRobo) builds correctly with valid paths, no
regression on production canonicals.

All 7 handlers now in AUDITED_CLEAN. Honesty test passes.

## The bug

All 5 pick-place handlers (native + spline + curobo + diffik + osc)
share an `on_step` callback registered via
`omni.physx.subscribe_physics_step_events`. When the callback fires
on a step and accesses a bad prim path, it produces:

```
File "<string>", line 52, in on_step
File "<string>", line 47, in get_pos
Boost.Python.ArgumentError: Python argument types in
    Stage.GetPrimAtPath(Stage, str)
```

For native + spline, this error somehow propagates back to
`/exec_sync`'s success flag. For curobo + diffik + osc, it doesn't —
the handler reports `success=True` despite the Traceback being
emitted to stdout. Net effect: the LLM sees a "successful" tool
execution and reports the scene as built, but the controller is
silently broken.

## Hypothesis on the differential propagation

Native + spline likely have a synchronous post-setup step that
triggers `on_step` once before the handler returns (so the error
hits the handler's exception path), while curobo + diffik + osc only
register the subscription and return — meaning the first physics step
fires AFTER `/exec_sync` has captured `success=True`.

This was NOT verified directly — would require comparing the
synchronous regions of all 5 handlers (each is 600+ lines).

## Recommended fix

Add a top-of-handler pre-flight prim-existence check that runs
synchronously at the start of the generated code, BEFORE any
subscription registration:

```python
# Pre-flight: reject before subscribing on_step, so error propagates
# to /exec_sync return value not just stdout
for _p, _label in [
    (ROBOT_PATH, 'robot_path'),
    (BELT_PATH,  'belt_path'),
    (DEST_PATH,  'destination_path'),
]:
    if not stage.GetPrimAtPath(_p).IsValid():
        raise RuntimeError(
            f"setup_pick_place: {_label}={_p!r} not found in stage"
        )
for _src in SOURCE_PATHS:
    if not stage.GetPrimAtPath(_src).IsValid():
        raise RuntimeError(
            f"setup_pick_place: source path {_src!r} not found"
        )
```

This makes the error fail-fast and PROPAGATE to success=False,
without touching the subscription or step machinery.

## Risk for fix

Touching `_gen_pick_place_curobo` is non-trivial: it's the controller
used by CP-03 (color-routed sort), which is verified working via
hard-instantiate smoke tests. The pre-flight check must:
1. Not reject paths CP-03 uses (they're real and valid)
2. Run BEFORE any code that mutates state
3. Be tested via verifier_smoke_tests.py to confirm CP-03 still passes

Same for diffik + osc handlers — they may or may not be exercised by
existing canonicals; need to check before mutating.

## Why deferred

The fix touches production handlers and would require:
- uvicorn restart (per `feedback_isaac_assist_service_restart` memory)
- re-run of all hard_instantiate fixtures
- visual delivery test on CP-03 to confirm color-routing still works

Conservative approach: ship the audit findings, let the user OK the
fix scope before mutating production handlers. The honesty test
correctly flagging 3 real bugs is itself progress.

## Repro commands

```bash
# Generate + run each handler with bad paths (full smoke):
python3 << 'PYEOF'
import sys, json, asyncio, aiohttp
sys.path.insert(0, '.')
from service.isaac_assist_service.chat.tools.tool_executor import (
    _gen_pick_place_curobo,
)
async def go():
    code = _gen_pick_place_curobo(
        "/World/NonExistentRobot", "/World/NonExistentSensor",
        "/World/NonExistentBelt", ["/World/NonExistentCube"],
        "/World/NonExistentBin", [0.5, 0.0, 0.5], [0.0, 0.0, 0.1],
    )
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout) as s:
        async with s.post("http://127.0.0.1:8001/exec_sync",
                          json={"code": code}) as r:
            data = await r.json()
            print("success:", data["success"])
            print("output:", data["output"][:300])
asyncio.run(go())
PYEOF
# Expected (CORRECT): success=False
# Actual (BUG):        success=True with Traceback in output
```
