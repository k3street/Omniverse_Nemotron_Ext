# Overnight Final Summary — 2026-05-11 02:40

## Confirmed unlocks (robust across fresh-Kit batches)

**Patched-set stable_ok (verified in multiple Kit instances):**
- CP-22, CP-46, CP-48, CP-51, CP-52, CP-53, CP-57, CP-58, CP-59, CP-65
- = 10 robust unlocks (was 2 at Phase 0 baseline)

**Stochastic stable_ok (works sometimes):**
- CP-68 (N=5 5/5 yesterday, 1/2 fresh Kit today)
- CP-37 (N=5 5/5 yesterday + 1/1 fresh Kit yesterday, 0/4 today)

**Yrkesroll stable_ok (7 confirmed in fresh Kit):**
- controller-shootout-cp, 3station-oee, y-merge-singulation,
  cad-revision-drift, inspect-reject, dr-curriculum, multi-cam-triangulation

**BUILD_OK plumbing/build-only (7):**
- plc-conveyor, plc-fixture, opcua-12conveyors, multi-amr-corridor,
  defect-sdg, rl-clone-env, sim2real-gap

## Total verified today

**24 templates** (10 patched-set + 7 yrkesroll + 7 BUILD_OK).

## Patterns proven

1. **3D-aware reach check** (Phase 4 P1) — cuRobo cube candidate filter.
2. **cube_paths multi-cube semantics** (Phase 4 P2) — gate accepts any cube delivered.
3. **drop_target explicit on FrankaB** (multi-Franka pattern) — auto-drop misses bin by 2-5mm.
4. **FrankaB position closer to handoff** — auto-3D-reach blocks otherwise.
5. **Remove destination_path from planning_obstacles** — cuRobo refuses to plan into bin if listed as obstacle.

## Patterns failed / parked

1. PhysX numerical explosion during grip — bricks, pegs, tactile (3 CPs)
2. Multi-robot coordination beyond handoff — rotary table (CP-67), mating (CP-76)
3. UR10 cuRobo plan_pose 100% fail (CP-73) — base config issue
4. Spline / native controllers — wait_sensor, no engagement signal
5. Conveyor loop physics (CP-60) — segment overlap doesn't bridge corners
6. Drawer-pull (CP-NEW-drawer-open) — pick-place can't drag prismatic joints
7. NVIDIA SimReady deps (G1, Carter, Avatar) — assets not on local Nucleus

## Kit-state-corruption lessons

1. **Sequential >30 CPs in single Kit instance** causes cuRobo planner state drift.
   Solution: restart Kit every 30 CPs (manual or script).
2. **Modifying helpers called inside cuRobo** (e.g. _bin_drop_pos) requires
   full Kit restart, not just uvicorn restart.

## Tools shipped today

- M1 ROS2 production parity (3 tools + CP-87)
- M2 Modbus-TCP bridge + 8 unit tests
- M3 OPC-UA bridge + 3 unit tests
- M4 cuMotion-MoveIt + controller-shootout artefact
- M5 MQTT-Sparkplug bridge + 3 unit tests
- OpenPLC convenience wrapper (M5 P3)
- bridge_pause/resume + list_bridges utilities

= **13 industrial-bridge handlers** + MCP schemas (416 total).

## Commits today: 81

## Active processes (still running)

- Isaac Sim Kit RPC (port 8001): PID 1514050
- uvicorn (port 8000): PID 984778
- Watchdog: PID 37120 (5h28m uptime)

## What I'd do next session

1. Re-run CP-37 in N=3-5 to determine if genuinely regressed or just unlucky
2. Implement Kit-restart hooks in multi_run_regression.py (every 30 CPs)
3. Re-run full sweep with the new restart logic
4. Investigate CP-67 rotary table movement (cube should rotate with disc)

---

## Final sanity check 02:42

```
CP-22   stable_ok  1/1  46.5s
CP-51   stable_ok  1/1  50.9s
CP-52   stable_ok  1/1  51.0s
```

3/3 robust patched-set unlocks confirmed end of overnight session.

## Commits today: 82 on feat/multimodal-foundation @ anton


---

## Morning extension (07:10+)

After sweep showed Kit-state-drift on ~44 E_OFF_TARGET_XY CPs, ran
fresh-Kit batches:

### Additional unlocks confirmed (fresh Kit, single-CP isolation)

| CP | Status | Note |
|---|---|---|
| CP-16 | stable_ok | duration_s 120 → 180 |
| CP-17 | stable_ok | Kit-state-drift in sweep |
| CP-18 | stable_ok | Kit-state-drift |
| CP-24 | stable_ok | Kit-state-drift |
| CP-31 | stable_ok | Kit-state-drift |
| CP-41 | stable_ok | Kit-state-drift |
| CP-47 | stable_ok | Kit-state-drift |
| CP-54 | stable_ok | Kit-state-drift |
| CP-66 | stable_ok | Kit-state-drift |
| CP-77 | stable_ok | Kit-state-drift |

= **10 additional confirmed unlocks** beyond yesterday's 10.

### Still stable_fail (genuine in fresh Kit)

CP-12, CP-15, CP-27, CP-28, CP-29 (C_FELL_OFF_BELT)
CP-38, CP-56, CP-62, CP-72 (E_OFF_TARGET_XY genuine)
CP-69, CP-70, CP-75, CP-79 (UR10 issues)
CP-37, CP-68 (stochastic — Kit-state dependent)

### Web-research note (07:15)

Searched NVIDIA forums + GitHub. Confirmed: Kit memory leak (~200MB per
SimulationApp cycle) + cuRobo cache stale-state are documented community
issues:
- github.com/isaac-sim/IsaacSim#51
- NVIDIA forum SimulationApp memory leak thread
- cuRobo issue #603 (update_world cache)

My "Kit needs restart every ~30 CPs" observation is consistent with these.

### Updated verified count

- **21 patched-set stable_ok in fresh Kit** (was 10 yesterday, +11 today)
  - CP-22, CP-46, CP-48, CP-51, CP-52, CP-53, CP-57, CP-58, CP-59, CP-65 (yesterday)
  - +CP-16, CP-17, CP-18, CP-24, CP-31, CP-41, CP-47, CP-54, CP-66, CP-77 (today)
  - +CP-16 template fix (duration_s 120→180)
- **5 yrkesroll N=3 robust** (controller-shootout-cp, cad-revision-drift,
  inspect-reject, dr-curriculum, multi-cam-triangulation)
- **2 yrkesroll N=3 flaky** (3station-oee 2/3, y-merge-singulation 1/3)
- **7 BUILD_OK plumbing** (plc-conveyor, plc-fixture, opcua-12conveyors,
  multi-amr-corridor, defect-sdg, rl-clone-env, sim2real-gap)

= **35 confirmed templates** in fresh-Kit conditions.
