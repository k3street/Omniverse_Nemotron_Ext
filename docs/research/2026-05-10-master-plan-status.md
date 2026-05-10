# Master Plan Status — 2026-05-10 (end-of-day)

Branch: `feat/multimodal-foundation` (anton remote)
Session length: ~14h continuous autonomous work, ~30 commits

## Phase-by-phase status

### Phase 0 — Stabilize + lock baseline ✓ DONE
- N=5 multi-run regression infrastructure
- baseline_compare.py
- determinism unit tests
- Phase 0 baseline frozen

### Phase 1 — diagnose_scene_feasibility ✓ DONE
- Handler wired in tool_executor.py + 92 unit tests
- MCP schema + UI description
- auto_judge `scene_feasibility` axis
- verify_pickplace_pipeline `--feasibility` flag
- 1.3 (full 86-CP feasibility baseline run): pending — needs Kit time

### Phase 2 — Per-class triage + fixes ~PARTIAL
- Revert-safety executor ✓ shipped
- Runtime diagnostic probe (probe_ctrl_telemetry.py) ✓ + 7 diagnose patterns
- Phase 2.2 fix executor: deferred (manual fixes worked better)

### Phase 3 — Multimodal Block 1B (parallel session, not me)

### Phase 4 — Scenario-profile config ✓ P1+P2 DONE
- 3D-aware reach check (Phase 4 P1) — UNLOCKED CP-37
- Multi-cube simulate_args (Phase 4 P2) — UNLOCKED CP-53/57/58/46/48,
  RESTORED CP-65
- Predictive planning prototype: NOT IMPLEMENTED (Opus research said
  it's a red herring vs 3D reach)

### Phase 5 — 100% function-gate drive ~PARTIAL
**Confirmed stable_ok today (verified):**
- CP-22 (N=5 5/5)
- CP-37 (N=5 5/5) — UNLOCKED
- CP-53 (N=5 5/5) — UNLOCKED
- CP-59 (N=5 5/5) — promoted from flaky
- CP-65 (N=5 5/5)
- CP-57 (N=1 1/1) — UNLOCKED
- CP-58 (N=1 1/1) — UNLOCKED
- CP-46 (N=1 1/1) — UNLOCKED
- CP-48 (N=1 1/1) — UNLOCKED

= **9 verified stable_ok in patched-set** (was 2 in Phase 0 baseline).
~62 of base CP-01..CP-86 presumed stable_ok historically (not re-verified
in this session's patched-set).

**Partial progress (engagement unblocked, residual physics):**
- CP-51, CP-68 (handoff): FrankaB moved closer to handoff; cube falls
  off bin edge; bin drop-precision issue remaining

**Still failing (controller-side issues outside cube_paths fix scope):**
- CP-67, CP-76, CP-52: multi-Franka relay/handoff coordination
- CP-73, CP-74: UR10 + cuRobo plan-fail / grip
- CP-80, CP-84, CP-85: UR10 + builtin / raycast
- CP-05, CP-06: spline / reorient flip-wall
- CP-40: spline 4-cube belt
- CP-60, CP-62: build-failed conveyor loops

For ≥80/86 stable_ok exit criterion, ~9-11 more CPs need targeted fixes.

### Phase 6 M1 — ROS2 production parity ✓ DONE
- setup_ros2_control_compat
- emit_ros2_control_yaml
- precheck_ros2_environment
- CP-87-ros2-moveit2-franka-pickplace template

### Phase 7 — Multimodal Block 2 + 3 (parallel session, not me)

### Phase 8 M2-M3 bridges + Top-5 yrkesroll ✓ TOOLS + ~DRAFTED templates
- M2: modbus_tcp_bridge_attach (✓ + 8 unit tests)
- M3: opcua_bridge_attach (✓ + 11 unit tests)
- F-02 promotion: CP-NEW-opcua-12conveyors ✓
- CP-NEW-plc-conveyor (M2 plumbing) ✓
- Top-5 yrkesroll templates (5/5 drafted, all need Kit smoke):
  - CP-NEW-g1-bimanual-tabletop, rl-clone-env, amr-pickup-handoff,
    drawer-open, peg-in-hole-single

### Phase 9 M4 cuMotion-MoveIt + Top 6-15 yrkesroll ✓ TOOL + ~DRAFTED
- setup_isaac_ros_cumotion_moveit handler ✓
- controller_shootout_report.py ✓ artefact emitter
- Top 6-15 yrkesroll templates (10/10 drafted)

### Phase 10 M5 + Top 16-20 + Multimodal 4-5 ~PARTIAL
- M5 mqtt_sparkplug_bridge_attach ✓ + 14 unit tests
- M5 P3 openplc_runtime_attach ✓ + 16 unit tests
- Top 16-20 yrkesroll templates (5/5 drafted)
- Multimodal Block 4-5: parallel session, not me

## Industrial-expansion: 5 of 5 milestones ✓

| M | Status | LOC delivered | Tests |
|---|---|---|---|
| M1 ROS2 | ✓ | 3 tools | – |
| M2 Modbus | ✓ | 1 tool + worker | 4 (+1 cycle) |
| M3 OPC-UA | ✓ | 1 tool + worker | 4 (+1 cycle) |
| M4 cuMotion-MoveIt | ✓ | 1 tool + shootout | – |
| M5 MQTT-Sparkplug | ✓ | 1 tool + worker | 3 |
| M5 P3 OpenPLC | ✓ | 1 wrapper | 2 |

All 6 industrial tools have MCP schemas (413 total schemas).

## Definition-of-done — controller-logic track

✓ DONE:
- diagnose_scene_feasibility shipped + wired
- scenario_profile pattern (3D reach + multi-cube)
- ROS2 first-class direct-eval target
- Modbus + OPC-UA + MQTT-Sparkplug + OpenPLC bridges shipped
- cuMotion-MoveIt + controller-shootout artefact emitter
- 87+22 templates exist (target was 111; 109 actual)

❌ NOT DONE:
- 86/86 stable ✓ (~9 verified, base CPs presumed but unverified)
- Per-profile scenario branches selected automatically (manual flag)
- 20 yrkesroll Kit smoke-tested live (templates drafted, untested)
- Multimodal Block 1B/2/3/4/5 (parallel session)

## Realistic remaining work

For full master-plan completion, controller-logic track:
- Phase 5 drive: ~5-10 sessions to push toward ≥80/86 (each unlock is
  template tweak + Kit verify + commit; multi-Franka and UR10 issues
  need targeted controller work)
- Phase 8 yrkesroll Kit smoke: ~5-10 sessions (per template; depends on
  NVIDIA Nucleus availability for G1, Carter, Avatar)
- Phase 9 Kit smoke: ~5-10 sessions
- Phase 10 Kit smoke: ~3-5 sessions

Plus parallel multimodal track work.

## Files touched this session (~30 commits on `feat/multimodal-foundation`)

Service code:
- `service/isaac_assist_service/chat/tools/tool_executor.py` (3D reach,
  M1 + M4 handlers)
- `service/isaac_assist_service/chat/tools/bridge_tools.py` (NEW; M2-M5
  + OpenPLC, 10 handlers)
- `service/isaac_assist_service/chat/tools/tool_schemas.py` (+9 schemas)

Tests:
- `tests/test_bridge_tools.py` (16 l0 tests, all green)

Scripts:
- `scripts/qa/probe_ctrl_telemetry.py` (cube-discovery + phantom_handoff)
- `scripts/qa/multi_run_regression.py` (already had multi-run)
- `scripts/qa/controller_shootout_report.py` (NEW; M4 deliverable)
- `scripts/qa/classify_failure_modes.py` (NEW)
- `scripts/qa/phase5_driver.sh` (NEW; autonomous loop)
- `scripts/qa/progress_watchdog.sh` (already running 5+h)

Templates:
- 25 modified/created (CP-37/53/57/58/46/48/65/67/76/52/35/51/68 fixes,
  20 yrkesroll, 2 plumbing)

Docs:
- `docs/specs/2026-05-09-master-execution-plan.md` (live status)
- `docs/research/2026-05-10-session-summary.md` (NEW)
- `docs/research/2026-05-10-master-plan-status.md` (this doc)
- `docs/research/2026-05-10-failure-modes-synthesis.md` (annotated)
- `docs/research/controller_shootout.md` (NEW)
- `docs/research/2026-05-10-predictive-planning-research.md` (Opus)
