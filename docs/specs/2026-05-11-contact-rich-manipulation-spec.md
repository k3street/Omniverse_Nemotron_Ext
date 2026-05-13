# Compliance & Force-Feedback Successor Spec

**Date:** 2026-05-11 (v1) — 2026-05-13 (v2 narrow rewrite)
**Status:** v2 — narrowed scope. Compliance layer only. VLA + RL
deferred to Phase 62b/62c extensions.
**Owner:** TBD
**Estimated LOC:** 600-900 (compliance handlers + ros2_control bridge
+ FT sensor harmonization + tests)

**Dependencies (must land first):**
- IA Full Spec **Phase 80b** — grip_safe_mode + per-prim physics
  defaults (LANDED 2026-05-13). Layer 0 stability.
- IA Full Spec **Phase 63b** — cuRoboV2 constrained planning (math
  layer LANDED 2026-05-13). Provides constrained trajectories.
- IA Full Spec **Phase 20** — role-based template refactor (LANDED
  2026-05-13). Hosts `compliance_mode` auto-pick.
- IA Full Spec **Phase 19** — Kit RPC execution path. Runtime hand-off.

**Spec history:** v1 (2026-05-11) was a 4-layer architecture covering
stability/compliance/planning/policy + RL. v2 review (2026-05-13)
identified that 3 of 4 layers were already covered by IA Full Spec
phases that landed:
- Layer 0 stability → Phase 80b
- Layer 2 planning → Phase 63b
- Layer 3 VLA policy → Phase 62b (`load_groot_policy`,
  `evaluate_groot`, `finetune_groot`, `compare_policies` exist)
- Layer 4 RL training → Phase 79/79b WBC + IsaacLab G1 locomanip

This rewrite narrows Spec 2 to **Layer 1 compliance only** — the
genuinely missing piece.

---

## 0. TL;DR

After IA Full Spec Phase 80b lands, classical pick-place is stable but
cannot reliably insert pegs. Contact-rich tasks need compliant
control — the robot yields to contact force instead of slamming
through. ros2_control mainline `admittance_controller` and related
controllers exist upstream; we need a thin Isaac-side bridge + tools.

**This spec adds:**
- 5 new tools: `setup_admittance_controller`, `setup_impedance_controller`,
  `set_compliance_params`, `release_compliance`, `follow_trajectory_with_compliance`
- F/T sensor wrapper (harmonize with existing `add_force_torque_sensor`)
- ros2_control bridge wiring in extension
- `compliance_mode` template field (single string, auto-picked by
  Phase 20 role-binder)

**This spec does NOT add:**
- ❌ VLA inference infrastructure (Phase 62b owns it)
- ❌ IndustReal RL training pipeline (Phase 62c / 79b owns it)
- ❌ `controller_stack` super-schema (the variant matrix was over-
  engineering; replaced with single `compliance_mode` field)
- ❌ Stack evaluation / compatibility framework (was Spec 3;
  reverted — auto-pick handles it)

**Default user experience: "it just works."** User says "build a
peg-in-hole" → system auto-picks `compliance_mode=admittance` from
intent (peg-in-hole → has_contact_phase → admittance). User never
sees the choice unless they explicitly override.

---

## 1. Problem statement

### 1.1 What Phase 80b alone doesn't solve

After Phase 80b's `grip_safe_mode` lands, PhysX is stable enough that
contact-rich scenes don't explode. But classical `set_joint_targets`
commands the robot to hit a position regardless of contact — the
gripper still slams through hole edges. PhysX-stable ≠ contact-
compliant.

Empirical observation (from 2026-05-11 verify batches): even with
Phase 80b applied, `setup_pick_place_controller(target_source="curobo")`
on a Franka cannot reliably **insert** a peg into a hole — it can
**place it nearby**. Our verifier accepts "near-bin xy" as success,
so we score stable_ok, but the motion would not transfer to a real
robot.

### 1.2 The genuinely-missing piece: compliance

The robot must be **compliant** under contact: yield to external force,
behave like a virtual spring-damper. The math:

```
F = K · (x_desired - x_actual) - D · v_actual + F_external_feedback
```

ros2_control mainline ships `admittance_controller` that implements
this. fzi-forschungszentrum-informatik ships
`cartesian_compliance_controller` (FDCC). matthias-mayr ships
Cartesian impedance for torque-mode. We need an Isaac-side bridge
+ tool wrappers.

### 1.3 Why this is its own spec (not a phase in IA Full Spec)

The compliance layer doesn't fit cleanly into Phase 63b (cuRoboV2 is
about motion planning, not control), Phase 80b (about physics
defaults), or Phase 70b (`create_behavior` rewrite). It's a separate
control-layer concern that wraps ros2_control upstream and exposes it
as Isaac tools.

---

## 2. Architecture (3 layers, not 4)

```
┌──────────────────────────────────────────────────────────────┐
│ Phase 20 role-based template + auto-pick                     │
│ "compliance_mode": auto-selected from intent.structural_features│
│   uses_conveyor_transport + no contact → null (rigid)        │
│   has_contact_phase + Franka → admittance                    │
│   has_contact_phase + UR10e position-mode → admittance       │
│   has_contact_phase + Franka FCI torque-mode → impedance     │
└─────────────────────────────┬────────────────────────────────┘
                              ▼
┌──────────────────────────────────────────────────────────────┐
│ Phase 63b plan_constrained_trajectory                        │
│   axis-locked trajectory + handoff_at fraction               │
└─────────────────────────────┬────────────────────────────────┘
                              │ trajectory + handoff_at
                              ▼
┌──────────────────────────────────────────────────────────────┐
│ THIS SPEC: Compliance layer                                  │
│ follow_trajectory_with_compliance(trajectory, handoff_at,    │
│                                    compliance_mode)          │
│   - Rigid above handoff_at fraction (trajectory targets)     │
│   - Compliant below handoff_at (admittance yields to F/T)    │
│   - F/T sensor model integrated                              │
│   - ros2_control bridge under the hood                       │
└─────────────────────────────┬────────────────────────────────┘
                              ▼
┌──────────────────────────────────────────────────────────────┐
│ Phase 80b stability layer                                    │
│   PhysX 5.x + per-prim defaults + CCD + maxContactImpulse    │
│   + velocity-sanity guard rail                               │
└──────────────────────────────────────────────────────────────┘
```

Compliance handoff is the bridge: Phase 63b's constrained trajectory
includes `lock_orientation_from=0.5`. THIS spec's
`follow_trajectory_with_compliance` consumes that trajectory and the
handoff fraction, runs rigid control above the line, compliant
control below. Same fraction for both → seamless transition.

---

## 3. Compliance variant matrix

All variants are first-class — selectable via `compliance_mode`:

| Variant | Source | Domain | Robot requirement | Use case |
|---|---|---|---|---|
| **admittance** | [ros2_controllers mainline](https://control.ros.org/rolling/doc/ros2_controllers/admittance_controller/doc/userdoc.html) | force→motion | Position-mode + external F/T sensor | DEFAULT for any robot |
| **cartesian_compliance_fdcc** | [fzi cartesian_controllers (FDCC)](https://github.com/fzi-forschungszentrum-informatik/cartesian_controllers) | hybrid motion+force | Position-mode | Tasks needing explicit wrench tracking (assembly with target force profile) |
| **cartesian_impedance** | [matthias-mayr Cartesian-Impedance-Controller](https://github.com/matthias-mayr/Cartesian-Impedance-Controller) | motion→torque | Torque-mode (Franka FCI) | Lowest-latency compliance, real-Franka deployment |
| **variable_impedance** | [paper-derived; in-house impl](https://www.sciencedirect.com/science/article/pii/S0736584524001832) | K varies w/ phase | Any | K_low during search, K_high during insertion settle |
| **franka_cartesian_impedance** | [frankaemika/franka_ros2 PR #51](https://github.com/frankaemika/franka_ros2/pull/51) | torque (vendor-tuned) | Franka FCI | Best fit when deploying to real Franka |
| **null** | n/a | rigid passthrough | Any | Baseline / rigid mode for A/B comparison |

**Default for new templates:** `admittance` when
`intent.structural_features.has_contact_phase=True`, else `null`.

Why admittance is default: it's mainline ros2_controllers, position-
mode robot agnostic, works with any externally-mounted F/T sensor.
The other variants require either torque-mode hardware OR specific
control properties.

---

## 4. Auto-pick: "it just works"

The user never sees compliance choices unless they explicitly override.

### 4.1 Auto-pick algorithm (lives in Phase 20 role-binder)

```python
def autopick_compliance_mode(layout_spec: LayoutSpec,
                              role_bindings: RoleBindings) -> str | None:
    intent = layout_spec.intent

    # Pure free-space → rigid is fine
    if not intent.structural_features.has_contact_phase:
        return None  # rigid baseline

    # Contact phase present — pick compliance mode by embodiment
    primary_robot = role_bindings.get("primary_robot")
    robot_class = primary_robot.class if primary_robot else "franka_panda"

    if robot_class == "franka_panda":
        if intent.structural_tags.includes("real_robot_deployment"):
            return "franka_cartesian_impedance"   # vendor-tuned for sim2real
        else:
            return "admittance"                   # safe sim default

    elif robot_class in ("ur10e", "ur5e", "ur3e"):
        return "admittance"   # UR robots are position-mode only

    elif robot_class == "kinova_gen3":
        return "admittance"

    # Unknown robot → safe default
    return "admittance"
```

This auto-pick is added to `Phase 20 role-binder` (already LANDED) as
a small extension, NOT a new file.

### 4.2 Override path (rare)

A template author OR end-user can override:

```jsonc
{
  "task_id": "CP-NEW-foo",
  "compliance_mode": "variable_impedance",   // explicit override
  "compliance_params": {                     // explicit tuning
    "stiffness_xyz": [200, 200, 100]
  }
}
```

The override is validated against ~20 hard-incompat rules:

```python
def validate_compliance_override(mode: str, robot_class: str,
                                  has_ft_sensor: bool) -> Result:
    HARD_INCOMPATIBILITIES = [
        # impedance variants require torque-mode
        ("cartesian_impedance", lambda r: r not in TORQUE_MODE_ROBOTS,
         "cartesian_impedance requires torque-mode robot (e.g. Franka FCI)"),
        ("franka_cartesian_impedance", lambda r: r != "franka_panda",
         "franka_cartesian_impedance is Franka-specific"),
        # admittance/FDCC need F/T sensor
        ("admittance", lambda _: not has_ft_sensor,
         "admittance requires F/T sensor; attach via attach_ft_sensor first"),
        # ... ~15 more
    ]
    for mode_match, predicate, message in HARD_INCOMPATIBILITIES:
        if mode == mode_match and predicate(robot_class):
            return Result.error(message)
    return Result.ok()
```

~50 LOC inline validator. Catches the explicit-override case. Most
users never trigger it because auto-pick handles the common path.

---

## 5. Tool API surface

5 new tools (down from 13 in v1):

### 5.1 `setup_admittance_controller`

```python
async def setup_admittance_controller(
    robot_path: str,                    # "/World/Franka"
    target_frame: str = "tool0",        # which link to compliance-control
    mass_xyz: list[float] = [1.0]*3,    # virtual inertia (kg)
    stiffness_xyz: list[float] = [500.0]*3,  # K (N/m)
    damping_xyz: list[float] = [50.0]*3,     # D (N·s/m)
    mass_rot: list[float] = [0.1]*3,
    stiffness_rot: list[float] = [50.0]*3,
    damping_rot: list[float] = [5.0]*3,
    ft_sensor_path: str | None = None,
    chain_after: str = "joint_trajectory_controller",
) -> dict:
    """Install ros2_control admittance_controller in front of robot's
    existing position controller. After this, any tool that sets joint
    targets goes through the admittance layer."""
```

### 5.2 `setup_impedance_controller`

```python
async def setup_impedance_controller(
    robot_path: str,
    target_frame: str = "tool0",
    Kx: list[float] = [400.0]*3,        # cartesian stiffness (N/m)
    Kr: list[float] = [40.0]*3,         # rotational stiffness (N·m/rad)
    Dx: list[float] = [40.0]*3,
    Dr: list[float] = [4.0]*3,
    null_space_stiffness: float = 0.5,
    null_space_damping: float = 0.5,
    torque_mode: bool = True,           # required for impedance
) -> dict:
    """Install Cartesian impedance control. Requires torque-mode robot
    (Franka FCI in libfranka, etc.). Falls back to admittance with
    structured warning if torque-mode unavailable."""
```

### 5.3 `set_compliance_params`

```python
async def set_compliance_params(
    robot_path: str,
    stiffness_xyz: list[float] | None = None,
    damping_xyz: list[float] | None = None,
    mass_xyz: list[float] | None = None,
    stiffness_rot: list[float] | None = None,
    damping_rot: list[float] | None = None,
    mass_rot: list[float] | None = None,
) -> dict:
    """Runtime mutation of an already-installed compliance controller.
    Used by `variable_impedance` to shift K between search-phase
    (low K) and insertion-phase (high K)."""
```

### 5.4 `release_compliance`

```python
async def release_compliance(robot_path: str) -> dict:
    """Remove compliance controller, restore rigid joint-target path.
    Used at task end OR when switching to a different mode."""
```

### 5.5 `follow_trajectory_with_compliance` (Phase 63b ↔ Layer 1 bridge)

```python
async def follow_trajectory_with_compliance(
    trajectory: list[dict],              # waypoints from Phase 63b plan_constrained_trajectory
    robot_path: str = "/World/Franka",
    compliance_handoff_at: float = 0.5,  # 0..1 fraction
    compliance_controller: str = "admittance",
    timeout_s: float = 30.0,
    velocity_scaling: float = 1.0,
) -> dict:
    """Execute a constrained trajectory with rigid-to-compliant handoff.

    From t=0 to t=compliance_handoff_at: rigid joint targets follow
    trajectory exactly.
    From t=compliance_handoff_at to t=1: compliance controller takes
    over; trajectory targets become "desired pose" but yield to F/T.

    Returns:
        { ok, t_handoff_observed, contact_detected_at,
          final_pose, ft_at_handoff }
    """
```

### 5.6 F/T sensor — harmonize with existing

The existing `add_force_torque_sensor` tool
(`tool_schemas.py:1880`) covers wrist-mounted F/T. Extend it (don't
duplicate) with two kwargs:

```python
# Extension to add_force_torque_sensor:
noise_std: float = 0.0          # gaussian noise on wrench (N, default 0 = clean)
publish_topic: str | None = None  # ros2 topic for ros2_control admittance subscription
```

No new `attach_ft_sensor` tool. Harmonization is a small PR on the
existing handler.

---

## 6. Template `compliance_mode` field

A single new field on the canonical-template schema (Phase 20-managed):

```jsonc
{
  "task_id": "CP-NEW-peg-in-hole-single",
  "intent": { ... },
  "roles": { ... },
  "compliance_mode": "admittance",         // optional override; default = auto-pick
  "compliance_params": {                    // optional tuning
    "stiffness_xyz": [400, 400, 200],
    "stiffness_rot": [40, 40, 40]
  },
  "compliance_handoff_at": 0.5             // optional; default 0.5 matches Phase 63b
}
```

If absent → auto-pick (§4.1). If present → validated override (§4.2).

No `controller_stack` super-schema. No nested layer-of-layer. Just one
field with a clear purpose.

---

## 7. State machine for contact-rich pick-place

```
INSTANTIATE_SCENE (Phase 20 role-binder pulls template, fills bindings)
  ↓ scene_built
INSTALL_STABILITY (Phase 80b defaults applied)
  ↓ stability_ok
AUTO-PICK COMPLIANCE (§4.1)
  ↓ compliance_mode resolved
INSTALL_COMPLIANCE (this spec — setup_admittance_controller etc.)
  ↓ compliance_ok
PLAN (Phase 63b plan_constrained_trajectory)
  ↓ trajectory_with_axis_lock
APPROACH (rigid; t < handoff_at)
  ↓ t >= handoff_at
CONTACT (compliance driving; F/T feedback active)
  ↓ insertion_complete OR timeout
  ├─ inserted ──→ RELEASE
  └─ timeout  ──→ RECOVERY (back to PLAN with relaxed params, or abort)
RELEASE (gripper open, retract, release_compliance)
  ↓ retracted
DONE → emit telemetry, classify via verifier_registry
```

The handoff at PLAN→APPROACH→CONTACT is the key: Phase 63b's
`lock_orientation_from` and this spec's `compliance_handoff_at` use
the SAME fraction. Same fraction means seamless transition.

---

## 8. Telemetry events

New events added to `multimodal/telemetry.py`:

```python
EVENT_COMPLIANCE_INSTALLED = "compliance_installed"
EVENT_COMPLIANCE_PARAMS_UPDATED = "compliance_params_updated"
EVENT_COMPLIANCE_RELEASED = "compliance_released"
EVENT_FT_SENSOR_ATTACHED = "ft_sensor_attached"   # only if not already covered
EVENT_CONTACT_PHASE_ENTERED = "contact_phase_entered"
EVENT_CONTACT_PHASE_EXITED = "contact_phase_exited"
EVENT_INSERTION_SUCCEEDED = "insertion_succeeded"
EVENT_INSERTION_FAILED = "insertion_failed"
```

7-8 events total, down from 14 in v1. The VLA/RL telemetry events
moved out with their respective layers.

Aggregator additions in `scripts/qa/analyze_multimodal_usage.py`:
- `compliance_usage_breakdown(events)` — which controllers active
- `contact_phase_success_rate(events)` — insertion success by mode

---

## 9. Test plan

### 9.1 L0 unit (~30 tests)

- `tests/test_compliance_handlers.py`
  - Spring-law math (admittance: F = K·Δx - D·v)
  - Param validation (K > 0, D > 0, mass > 0)
  - `chain_after` wiring resolves to valid controller
  - Mode conversion (admittance ↔ impedance args)
- `tests/test_compliance_autopick.py`
  - Auto-pick correct mode per (intent, robot_class)
  - Override validates correctly
  - Hard-incompat list catches expected cases
- `tests/test_trajectory_compliance_handoff.py`
  - Handoff at correct fraction
  - Rigid/compliant transition continuous in EE position
  - Handoff_at mismatch with Phase 63b lock_orientation_from logs warning

### 9.2 L1 integration (mocked Kit, real model inference)

- `tests/test_compliance_under_load.py`
  - Apply 10N step input to mock F/T → admittance EE displaces
    correctly per spring law
  - Step response time within design budget

### 9.3 E2E (live Kit, opt-in)

- `tests/test_peg_insert_e2e.py` (marked `pytest -m compliance_e2e`)
  - Spawn peg-in-hole scene per CP-NEW-peg-in-hole-single template
  - Auto-pick compliance_mode → admittance
  - Run trajectory + handoff
  - Compare against rigid baseline (compliance_mode=null)
  - Goal: ≥50% better insertion success with admittance vs rigid

---

## 10. Performance SLAs

| Operation | p50 | p95 | Hard limit |
|---|---|---|---|
| admittance step update | 1ms | 2ms | 5ms (500Hz budget) |
| FT sensor read | 0.5ms | 1ms | 2ms |
| compliance install (setup_admittance) | 200ms | 500ms | 1s |
| handoff transition (rigid→compliant) | 10ms | 30ms | 50ms |
| `release_compliance` cleanup | 100ms | 300ms | 500ms |

Compliance step must hit 500Hz to match ros2_control mainline. If
sim physics is at 60Hz, step-update runs at every physics tick.

---

## 11. Phased roll-out

### A — Foundation (1 session)
- A.1: ros2_control bridge wiring in `exts/.../assist/extension.py`
- A.2: `setup_admittance_controller` tool + 8-10 L0 tests
- A.3: F/T sensor harmonization PR on existing `add_force_torque_sensor`

**Exit criterion:** existing CP-01 runs identically with admittance
controller installed but compliance_mode=null (no behavior change).

### B — Variants (1 session)
- B.1: `setup_impedance_controller` tool (torque-mode required)
- B.2: `set_compliance_params` runtime mutation
- B.3: `release_compliance` cleanup
- B.4: 10-12 more L0 tests covering variants

**Exit criterion:** CP-NEW-peg-in-hole-single with explicit
`compliance_mode=admittance` runs and shows reduced PhysX velocity
spikes under contact (telemetry).

### C — Auto-pick + bridge (1 session)
- C.1: `compliance_mode` template field added to schema
- C.2: Auto-pick algorithm added to Phase 20 role-binder
- C.3: Override validator (~50 LOC) with hard-incompat list
- C.4: `follow_trajectory_with_compliance` (Phase 63b ↔ Layer 1 bridge)

**Exit criterion:** at least 1 CP previously stable_fail becomes
stable_ok with admittance auto-picked. Likely CP-NEW-peg-in-hole-single
or CP-NEW-tactile-insertion.

### D — Telemetry + docs (0.5 session)
- D.1: 7-8 new telemetry event types
- D.2: 2 new aggregator functions
- D.3: `docs/guides/compliance_tuning.md`

**Total estimated effort: ~3.5 sessions, 600-900 LOC.**

---

## 12. What this spec does NOT do

Explicit non-goals (clarified after v2 review):

- ❌ **VLA inference infrastructure.** Phase 62b owns this. Existing
  `load_groot_policy`/`evaluate_groot`/`finetune_groot`/`compare_policies`
  tools are the canonical surface. If new VLA models (Pi0, OpenVLA,
  Touch2Insert) are needed, extend Phase 62b — don't fork.
- ❌ **IndustReal RL training pipeline.** Phase 79/79b WBC + IsaacLab
  G1 locomanip cover the simulation/training side. IndustReal-specific
  algos (SAPU/SDF-reward/SBC) are thin wrappers — defer to a
  hypothetical Phase 62c if/when needed.
- ❌ **`controller_stack` super-schema.** Was over-engineering. The
  single `compliance_mode` field is enough. Stack-evaluation framework
  (was Spec 3) is reverted; orthogonality holds by design + ~50 LOC
  hard-incompat validator.
- ❌ **Stack compatibility matrix / orthogonality verification
  framework.** Auto-pick + override validator covers the actual
  failure modes. Empirical compat data comes from Phase 97/97b
  regression sweeps.
- ❌ **Primitive library / composition engine.** Was Spec 4; reverted.
  IA Full Spec Phase 18b/20/21/28 own the role-based template +
  composition pattern. This spec doesn't touch authoring layer.

---

## 13. Open questions

1. **ros2_control bridge: Option A (external graph) vs Option B
   (in-Kit port)?**
   Default: Option A for v1. Standard ROS2 hop adds ~10ms latency but
   reuses upstream maintenance. Option B if 500Hz budget breached.

2. **F/T sensor noise model calibration.**
   Default `noise_std=0` gives unrealistic-clean signal. Phase 97/97b
   regression should reveal whether sim2real-realistic noise is needed.

3. **Auto-pick rule maintenance.**
   §4.1's auto-pick table will grow as new robots join. Should it live
   as code (Python conditionals) or YAML (config)? Default: Python,
   easier to test; migrate to YAML only if the table grows past ~30
   entries.

4. **Override validator semantic vs Phase 11b "Generic constraint-
   violation framework".**
   Our validator is compliance-specific. Phase 11b is generic. We
   should use Phase 11b's framework for the validator implementation
   to avoid duplicate error-shape handling.

5. **Variable-impedance K schedule.**
   Two-phase (low during search, high after first contact) is the v1
   default. Learned schedules deferred — not in scope.

---

## 14. References

### Compliance control (ros2_control + community)
- [ros2_controllers admittance_controller](https://control.ros.org/rolling/doc/ros2_controllers/admittance_controller/doc/userdoc.html)
- [fzi cartesian_controllers (ros2)](https://github.com/fzi-forschungszentrum-informatik/cartesian_controllers)
- [matthias-mayr Cartesian-Impedance-Controller](https://github.com/matthias-mayr/Cartesian-Impedance-Controller)
- [franka_ros2 PR #51 (Cartesian impedance port)](https://github.com/frankaemika/franka_ros2/pull/51)
- [Variable impedance + imitation learning paper](https://www.sciencedirect.com/science/article/pii/S0736584524001832)

### IA Full Spec prerequisites
- Phase 11b — Generic constraint-violation framework (used by override validator)
- Phase 18b — L1/L2/L3 action-level taxonomy (compliance tools = L2)
- Phase 19 — Kit RPC `apply_layout_spec_to_scene` (runtime hand-off)
- Phase 20 — Role-based template refactor (hosts auto-pick)
- Phase 63b — cuRoboV2 constrained planning (trajectory source)
- Phase 80b — grip-stability defaults (Layer 0 dependency)

### Sister specs (reverted)
- ~~`docs/specs/2026-05-11-stack-evaluation-spec.md`~~ → archived 2026-05-13. Stack-orthogonality framework was over-engineered; auto-pick + 50-LOC validator replaces.
- ~~`docs/specs/2026-05-11-composition-spec.md`~~ → archived 2026-05-13. Primitive library duplicated Phase 18b/20/21/28; composition pattern lives in IA Full Spec.

### Prior research
- `docs/research/2026-05-11-composition-research-report.md` — earlier
  Opus audit of Spec 4
- `docs/research/2026-05-13-specs-2-3-4-review.md` — Opus audit
  triggering this v2 narrowing

### Implementation references (existing, to reuse)
- `service/isaac_assist_service/chat/tools/tool_schemas.py:1880` —
  `add_force_torque_sensor` to be extended
- `service/isaac_assist_service/chat/tools/tool_schemas.py:3445-3468` —
  `setup_pick_place_controller.target_source` enum to coordinate with
- `service/isaac_assist_service/multimodal/types.py:87-118` —
  `Intent.structural_features` schema (auto-pick reads from here)
- `service/isaac_assist_service/chat/tools/role_retriever.py` —
  Phase 20 role-binder (auto-pick hooks in here)

---

## 15. Implementation checklist

### Compliance layer (new)
- [ ] `service/isaac_assist_service/chat/tools/compliance_handlers.py`
- [ ] `setup_admittance_controller` tool registered in DATA_HANDLERS
- [ ] `setup_impedance_controller` tool registered
- [ ] `set_compliance_params` runtime mutation tool
- [ ] `release_compliance` cleanup tool
- [ ] `follow_trajectory_with_compliance` (Phase 63b ↔ Layer 1 bridge)
- [ ] ROS2-bridge wiring in `exts/.../assist/extension.py`
- [ ] `config/admittance_controller_defaults.yaml`
- [ ] `config/impedance_controller_defaults.yaml`

### F/T sensor harmonization
- [ ] Extend existing `add_force_torque_sensor` handler with
      `noise_std` + `publish_topic` kwargs (NO new tool)
- [ ] Update tool_schemas.py:1880 signature
- [ ] Backward-compat: existing callers unaffected

### Auto-pick integration
- [ ] Auto-pick algorithm added to Phase 20 role-binder
      (`service/.../chat/tools/role_retriever.py`)
- [ ] Override validator (~50 LOC) using Phase 11b framework
- [ ] `compliance_mode` field accepted in template JSON schema

### Tests
- [ ] `tests/test_compliance_handlers.py` (≥20 L0)
- [ ] `tests/test_compliance_autopick.py` (≥10 L0)
- [ ] `tests/test_trajectory_compliance_handoff.py` (≥10 L0)
- [ ] `tests/test_compliance_under_load.py` (L1, mocked)
- [ ] `tests/test_peg_insert_e2e.py` (E2E, marked compliance_e2e)

### Telemetry
- [ ] 7-8 new event types in `multimodal/telemetry.py`
- [ ] 2 new aggregator functions in `analyze_multimodal_usage.py`

### Documentation
- [ ] `docs/guides/compliance_tuning.md` — user-facing
- [ ] Update IA Full Spec cross-references (mention this spec
      in Phase 80b/63b context)

---

## 16. Anti-overengineering safeguards

Lessons from v1 review (2026-05-13):

1. **Don't duplicate existing tools.** The original v1 proposed
   `attach_ft_sensor` parallel to existing `add_force_torque_sensor`.
   v2 explicitly says: extend the existing, don't add a parallel.

2. **Don't invent layers.** v1 had a 4-layer architecture. v2 has 1
   genuinely new layer (compliance). The other three "layers" are
   already-owned territory.

3. **Auto-pick over user-pick.** v1's `controller_stack` super-schema
   put the burden on the user to choose between variants. v2's
   `compliance_mode` is auto-picked; user-override is the rare case.

4. **Trust orthogonality by design.** v1's Spec 3 sister proposed an
   orthogonality-verification framework with compatibility matrix +
   tiered evaluation. v2 says: orthogonality holds by design; ~20
   hard-incompat rules in a ~50-LOC validator handle exceptions.

5. **Empirical data lives in Phase 97/97b regression**, not a new
   framework. v1's tiered-evaluation strategy is replaced by reading
   the regression sweep results.

6. **Decommission criteria** (if this spec needs to retire):
   - If compliance_mode auto-pick covers >95% of CPs without override,
     and >90% of overrides succeed without manual tuning of
     compliance_params, this spec is fully realized.
   - If the override validator catches less than 1% of attempts after
     6 months, the validator is academic and can be inlined into
     setup_admittance_controller's arg validation.

---

## 17. Glossary

- **Compliance:** robot behavior that yields to external force, like
  a virtual spring-damper.
- **Admittance control:** force-in → motion-out. Common in position-
  mode robots with external F/T sensor.
- **Impedance control:** motion-in → force-out. Common in torque-mode
  robots (Franka FCI).
- **FDCC:** Forward Dynamics Compliance Control. fzi's hybrid that
  simulates virtual robot dynamics in real-time to compute compliant
  joint commands.
- **EE:** End-effector. Robot's gripper/tool tip.
- **Handoff fraction:** the trajectory progress (0..1) at which rigid
  joint-target control hands off to compliance control. Default 0.5.
  Matches Phase 63b's `lock_orientation_from` parameter for seamless
  transition.
