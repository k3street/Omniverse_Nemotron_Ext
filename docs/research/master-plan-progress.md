# Master Plan — Visual Progress Map

```
                 ┌─────────────────────────────────────────────┐
                 │   Master Plan (controller-logic track)     │
                 └─────────────────────────────────────────────┘

PHASE 0 — Stabilize baseline               ████████████ 100% ✓
  ├─ seed/n_runs                            ✓
  ├─ multi_run_regression.py                ✓
  ├─ baseline_compare.py                    ✓
  └─ Phase 0 baseline frozen                ✓

PHASE 1 — diagnose_scene_feasibility       ████████████ 100% ✓
  ├─ Handler + 92 unit tests                ✓
  ├─ MCP schema + UI                        ✓
  └─ auto_judge axis                        ✓

PHASE 2 — Per-class triage                  ███████░░░░░  60% ~
  ├─ Revert-safety executor                 ✓
  ├─ Runtime probe (7 diagnoses)            ✓
  └─ Fix executor (deferred)                ─ (manual fixes worked)

PHASE 3 — Multimodal Block 1B               (parallel session)

PHASE 4 — Scenario-profile config           ██████████░░  85% ~
  ├─ P1 3D reach check                      ✓ (CP-37 unlocked)
  ├─ P2 cube_paths semantics                ✓ (5 unlocks)
  └─ Predictive prototype                   ─ (Opus said red herring)

PHASE 5 — 100% function-gate drive          █████████░░░  75% ~
  ├─ patched-set 25 CPs verify              9/25 stable_ok
  ├─ Estimated total                        ~56/86 stable_ok (65%)
  ├─ ≥80/86 exit criterion                  needs ~24 more unlocks
  └─ Phase 5 driver                         running (autonomous)

PHASE 6 M1 — ROS2 production parity         ████████████ 100% ✓
  ├─ 3 tools                                ✓
  └─ CP-87 template                         ✓

PHASE 7 — Multimodal Block 2 + 3            (parallel session)

PHASE 8 M2-M3 + Top-5 yrkesroll             █████████░░░  75% ~
  ├─ M2 Modbus bridge                       ✓ (8 tests)
  ├─ M3 OPC-UA bridge + F-02 promo          ✓ (11 tests)
  ├─ Top-5 yrkesroll templates              5/5 drafted
  └─ Top-5 Kit smoke                        ─ (need NVIDIA assets)

PHASE 9 M4 + Top 6-15 yrkesroll             █████████░░░  70% ~
  ├─ setup_isaac_ros_cumotion_moveit        ✓
  ├─ controller_shootout_report.py          ✓
  ├─ Top 6-15 templates                     10/10 drafted
  └─ Top 6-15 Kit smoke                     ─

PHASE 10 M5 + Top 16-20                     █████████░░░  75% ~
  ├─ M5 mqtt_sparkplug_bridge_attach        ✓ (14 tests)
  ├─ M5 P3 openplc_runtime_attach           ✓ (16 tests)
  ├─ bridge_pause / bridge_resume           ✓
  ├─ list_bridges                           ✓
  ├─ Top 16-20 templates                    5/5 drafted
  └─ Multimodal Block 4-5                   (parallel session)
```

## Industrial-expansion: 6/6 milestones DONE

```
M1 ROS2          ████████████ ✓
M2 Modbus        ████████████ ✓
M3 OPC-UA        ████████████ ✓
M4 cuMotion-MoveIt ███████████ ✓
M5 MQTT-Sparkplug ████████████ ✓
M5 P3 OpenPLC    ████████████ ✓
```

## Library: 109/111 templates

```
[████████████████████████████░░] 109/111 templates (98%)

Base CP-01..CP-87:                  87
Phase 8 yrkesroll Top-5:             5
Phase 9 yrkesroll Top 6-15:         10
Phase 10 yrkesroll Top 16-20:        5
Plumbing-only canonicals:            2
                                  ─────
Total:                             109

Multimodal Block 4 + Block 5:        2  (parallel session)
                                  ─────
Master plan target:                111
```

## Stable_ok progress

```
Phase 0 baseline:       2/86 ████░░░░░░░░░░░░░░░░░░░░░░░░ (2%)
Yesterday's estimate:  49/86 ████████████░░░░░░░░░░░░░░░░ (57%)
Today (post-unlocks):  56/86 █████████████░░░░░░░░░░░░░░░ (65%)
Phase 5 exit criterion: ≥80/86 ███████████████████░░░░░░░ (93%)
Master plan goal:     86/86 ████████████████████████████ (100%)
```

## Tests added today

- `tests/test_bridge_tools.py` — 20 tests
- `tests/test_controller_shootout.py` — 7 tests
- `tests/test_probe_diagnose.py` — 9 tests
- `tests/test_classify_failure_modes.py` — 7 tests

= 43 new l0 tests, all green.

## Schemas

```
Total MCP-discoverable tools: 416
  + 13 bridge handlers (M2-M5 + OpenPLC + pause/resume/list)
  + setup_isaac_ros_cumotion_moveit
```
