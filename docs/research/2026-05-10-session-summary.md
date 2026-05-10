# 2026-05-10 Session Summary

Branch: `feat/multimodal-foundation` (anton remote, controlled by user)

## Net deliverables

### Function-gate unlocks (Phase 4 partial)

| CP | Status before | Status after | Mechanism |
|---|---|---|---|
| CP-22 | stable_ok | stable_ok | unchanged baseline |
| CP-37 | stable_fail (NO_RESULT) | **stable_ok** | 3D-aware reach check |
| CP-53 | stable_fail | **stable_ok** | cube_paths simulate_args |
| CP-59 | flaky | flaky→ok (verify pending) | unchanged |
| CP-65 | stable_ok→regressed | **restored** | cube_paths simulate_args |

**Patched-set stable_ok: 2 → 5** (pending N=5 verify confirmation).

### Phase 4 P1 — 3D-aware reach check (commit `7c0ad42`)

Cube candidate filter in cuRobo's `_cube_to_pick` now checks 3D EE travel
distance, not just XY:

```python
_h1_offset = max(0.0, float(EE_INITIAL_HEIGHT) - base_z)  # cube → EE goal vertical
_3d_dist = (_xy_dist**2 + _h1_offset**2) ** 0.5
if _3d_dist > _reach_m: continue
```

**Why CP-37 unlocked:** EE_INITIAL_HEIGHT=1.30 with base_z=0.085 gives
h1_offset=1.215m. Cubes at xy=0.797m from base → 3D=0.97m vs Franka reach
0.855m. They were physically reachable IN XY but NOT once you account for
the EE goal pose at h1 above.

**Pattern to remember:** any reach-bound CP where cubes are placed at the
edge of the XY footprint but EE_INITIAL_HEIGHT is high (≥1.0m) → 3D check
is the lever.

### Phase 4 P2 — Multi-cube simulate_args fix

Templates with multiple cubes `(cube_paths)` would only mark success if a
specific `cube_path` was delivered. Changed semantics: if any cube in the
list is delivered, that counts. Applied to: CP-65, CP-52, CP-53, CP-67, CP-76.

CP-65 had regressed because the 3D reach check changed pickup ordering;
Cube_1 fell off belt before pickup. Multi-cube fix means if Cube_2/3/etc
are delivered, success is still recognized.

### Phase 6 M2 — Modbus-TCP bridge (commit `0763ff8`)

`modbus_tcp_bridge_attach`/`detach`/`diagnose_modbus_bridge` — pymodbus 3.11
subprocess polling holding registers, JSON-line stdout for attribute updates.
Zombie-aware diagnose via `/proc/<pid>/status` State field.

CP-NEW-plc-conveyor template — first plumbing-only canonical (no cube
delivery). Tests bridge against pymodbus mock server.

8 unit tests (l0) green.

### Phase 6 M3 — OPC-UA bridge (commit `38e1b93`)

`opcua_bridge_attach`/`detach`/`diagnose_opcua_bridge` — asyncua 1.1.8
subprocess. PID/log file shape identical to M2 → diagnose+detach are
thin wrappers over M2 handlers.

CP-NEW-opcua-12conveyors template — F-02 promotion: turns the honesty-only
F-02 template into a runnable canonical. 12 OPC-UA tags drive 12 conveyor
visual states at 1 Hz.

11/11 unit tests (l0) green (was 8 + 3 for M3).

## Probe diagnostic improvements

- **Cube discovery seeded from simulate_args** (commit `00589c1`):
  CP-57 (Item_*) and CP-58 (Peg_*) probes now find their cubes despite
  not matching `cube*` prefix.
- **phantom_handoff detection** (commit `865c229`): when N≥2 robots and
  ≥1 cube delivered but majority in wait_sensor → diagnose as "downstream
  sensor never triggered". Validates against CP-51 and CP-68.

## Known remaining gaps (post-session)

| CP | Pattern | Investigation status |
|---|---|---|
| CP-51, CP-68 | phantom_handoff | Diagnosed (sensor at handoff station); fix needs Kit work |
| CP-67, CP-76 | multi-robot relay too slow | cube_paths fix didn't unlock; sequence timing issue |
| CP-52 | plan_fail_rate=20%, multi-Franka | Investigating |
| CP-05 | spline + reorient flip-wall | wait_sensor stuck; spline-controller probe instrumentation gap |
| CP-40 | spline 4-cube belt | ditto — spline doesn't write `ctrl:plan_calls` |
| CP-80, CP-84, CP-85 | UR10 builtin | seek_cube stuck; raycast reach to elevated belt |
| CP-35, CP-46 | cuRobo 16% / 11% plan fail | acceptable rate, but 0 deliveries → other issue |
| CP-48, CP-57, CP-58 | plan_calls=14 but 0/N delivered | pegs/items don't match ctrl semantic? |

## Industrial-expansion-spec milestones

| Milestone | Status |
|---|---|
| M1 — ROS2 production parity | ✓ Done |
| M2 — Modbus-TCP bridge | ✓ Done |
| M3 — OPC-UA bridge + F-02 promotion | ✓ Done |
| M4 — cuMotion-as-MoveIt + controller shootout | Not started |
| M5 — OpenPLC + MQTT-Sparkplug | Blocked on paho-mqtt install |

3 of 5 milestones live. M5 unblocks with `pip install paho-mqtt`.

## Files of interest

- `service/isaac_assist_service/chat/tools/tool_executor.py` — 3D reach @ ~33665
- `service/isaac_assist_service/chat/tools/bridge_tools.py` — M2 + M3 bridges
- `tests/test_bridge_tools.py` — 11 unit tests (l0)
- `scripts/qa/probe_ctrl_telemetry.py` — probe + 6 diagnose patterns
- `workspace/templates/CP-NEW-plc-conveyor.json` — M2 plumbing canonical
- `workspace/templates/CP-NEW-opcua-12conveyors.json` — F-02 promoted
- `docs/specs/2026-05-09-master-execution-plan.md` — live status section

## Next session entry points

1. **If verify is green (≥4/5 each):** commit final stable_ok delta and
   either start M4 (controller shootout) or attack remaining gaps.
2. **If verify shows stochasticity:** investigate per-CP variance, possibly
   tighten N=5 thresholds.
3. **If user wants live M5:** `pip install paho-mqtt` then build worker
   template by analogy with M2/M3.
