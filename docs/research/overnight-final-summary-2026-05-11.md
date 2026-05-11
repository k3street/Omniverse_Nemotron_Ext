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

