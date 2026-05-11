# Contact-Rich Manipulation Stack — Successor Spec

**Date:** 2026-05-11
**Status:** first draft — applies *after* IA Full Spec lands
**Owner:** TBD
**Estimated LOC:** ~2500-3500 (across 3 layers, infra + integrations + tests)

**Dependencies:** IA Full Spec Phase 80b (grip-stability defaults) must land first.

---

## 0. Reading Guide

§1: Problem (what Phase 80b alone doesn't solve)
§2: Architecture — three orthogonal layers
§3: Layer 1 — Compliance (admittance/impedance via ros2_control)
§4: Layer 2 — Foundation-model policy inference (Pi0, GR00T, OpenVLA)
§5: Layer 3 — RL training pipeline (IndustReal) — opt-in, later phase
§6: Integration with Isaac Assist
§7: Tool/API surface
§8: State machine for contact-rich pick-place lifecycle
§9: Telemetry events
§10: Test plan with coverage targets
§11: Performance SLAs
§12: Phased roll-out
§13: Open questions
§14: References + citations

---

## 1. Problem statement

After IA Full Spec Phase 80b lands, PhysX simulation is stable enough that
peg-in-hole/tactile-insertion scenes don't explode. But three structural
gaps remain:

### 1.1 Phase 80b is necessary, not sufficient

| Layer | Phase 80b covers? | Gap |
|---|---|---|
| Stable physics (no explosion) | ✅ yes | none |
| Compliance under contact | ❌ no | rigid `set_joint_targets` slams through hole edges |
| Skill / policy for insertion | ❌ no | cuRobo plans free-space, not contact dynamics |
| Sim-to-real fidelity | ❌ no | no F/T sensor model, no contact reduction tuning |

Empirical: even with Phase 80b applied, classical `setup_pick_place_controller`
on a Franka cannot reliably **insert** a peg into a hole — only **place it
nearby**. Our verifier accepts "near-bin xy" as success, so we score
stable_ok, but the simulated motion would not transfer to a real robot.

### 1.2 Why VLA models alone don't fix it

GR00T N1.7-DROID, Pi0-FAST DROID, and OpenVLA-7B are open VLAs trained on
Franka-heavy DROID data. They output **joint targets or EE poses**, not
torques. Without a compliance layer beneath them, their commands hit the
same rigid-control problem: the policy says "move to xyz" and PhysX
faithfully accelerates the gripper into the contact edge.

VLAs ARE the right zero-shot layer for *what* to do; they are NOT a
substitute for *how* to do it compliantly.

### 1.3 What this spec ships

Three orthogonal layers, each one a self-contained piece of infrastructure
that can be picked up independently:

1. **Compliance layer** (Layer 1, §3): `setup_admittance_controller` /
   `setup_impedance_controller` tools, ros2_control bridge for Franka
2. **Foundation-model policy layer** (Layer 2, §4): zero-shot VLA inference
   wrappers (GR00T, Pi0) with consistent `execute_vla_policy` tool API
3. **RL training pipeline layer** (Layer 3, §5): IndustReal-style trainer
   for last-mile sim2real (opt-in, deferred)

After all three land, contact-rich CPs (peg-in-hole, tactile-insertion,
drawer-pull, gear-mate, etc.) become **achievable with documented
trade-offs**: classical 50%, classical+admittance 70%, VLA+admittance 75%,
RL+admittance 95%.

---

## 2. Architecture

```
┌────────────────────────────────────────────────────────────────────┐
│ User input: text prompt OR canvas placement OR voice               │
└─────────────────────────────┬──────────────────────────────────────┘
                              ▼
┌────────────────────────────────────────────────────────────────────┐
│ LayoutSpec (multimodal §3) → canonical template match              │
└─────────────────────────────┬──────────────────────────────────────┘
                              ▼
┌────────────────────────────────────────────────────────────────────┐
│  POLICY LAYER (Layer 2)                                            │
│   ├─ cuRobo (classical, free-space)                                │
│   ├─ GR00T N1.7-DROID (VLA, zero-shot, ~30Hz)                     │
│   ├─ Pi0-FAST DROID (VLA, zero-shot, ~50Hz)                       │
│   └─ IndustReal RL (Layer 3, trained, ~60Hz)                      │
└─────────────────────────────┬──────────────────────────────────────┘
                              │  joint targets or EE pose, 30-60 Hz
                              ▼
┌────────────────────────────────────────────────────────────────────┐
│  COMPLIANCE LAYER (Layer 1)                                        │
│   ├─ admittance_controller (ros2_controllers official)             │
│   ├─ cartesian_compliance_controller (fzi, FDCC)                   │
│   └─ cartesian_impedance_controller (matthias-mayr, torque-domain) │
│                                                                    │
│   F = K·(x_des - x_actual) - D·v_actual + F_ext_feedback           │
└─────────────────────────────┬──────────────────────────────────────┘
                              │  joint torques OR position w/ feedback
                              ▼
┌────────────────────────────────────────────────────────────────────┐
│  STABILITY LAYER (already in IA Full Spec Phase 80b)               │
│   PhysX 5.x + per-prim defaults + CCD + maxContactImpulse +        │
│   velocity-sanity guard rail                                       │
└────────────────────────────────────────────────────────────────────┘
```

**Layers are orthogonal.** A canonical template's `controller_stack` field
declares which layer-implementation combination to use:

```jsonc
{
  "task_id": "CP-NEW-peg-in-hole-single",
  "controller_stack": {
    "policy": "groot_n17_droid",
    "compliance": "cartesian_admittance",
    "stability": "phase_80b_defaults"
  }
}
```

The supervisor (Kit Supervisor spec) wraps the whole stack for restart
recovery. The verifier (multimodal verifier_registry §6) classifies
success at the simulate_traversal_check level, independent of layer
choices.

---

## 3. Layer 1 — Compliance (admittance / impedance via ros2_control)

### 3.1 Goal

Add `setup_admittance_controller` and `setup_impedance_controller` as
first-class tools. They install compliant-control under any existing
robot, replacing the rigid `set_joint_targets` path with a spring-damper
behavior at the joint or task-space level.

### 3.2 Why ros2_control mainline?

[ros2_control admittance_controller](https://control.ros.org/rolling/doc/ros2_controllers/admittance_controller/doc/userdoc.html)
is the **official** controller in ros2_controllers. It implements
`ChainedControllerInterface`, so it stacks in front of position
controllers without rewiring. Mainline = long-term-supported = our
default. fzi's `cartesian_compliance_controller` is the heavier hybrid
motion+force variant (FDCC); we ship it as opt-in for tasks needing
explicit wrench tracking.

### 3.3 Tool API surface

```python
async def setup_admittance_controller(
    robot_path: str,                    # "/World/Franka"
    target_frame: str = "tool0",        # which link to compliance-control
    mass_xyz: list[float] = [1.0]*3,    # virtual inertia (kg)
    stiffness_xyz: list[float] = [500.0]*3,  # K (N/m)
    damping_xyz: list[float] = [50.0]*3,     # D (N·s/m)
    mass_rot: list[float] = [0.1]*3,    # virtual rot. inertia (kg·m²)
    stiffness_rot: list[float] = [50.0]*3,
    damping_rot: list[float] = [5.0]*3,
    ft_sensor_path: str | None = None,  # optional explicit F/T sensor
    chain_after: str = "joint_trajectory_controller",  # what we wrap
) -> dict:
    """Install ros2_control admittance_controller in front of robot's
    existing position controller. After this, any tool that sets joint
    targets goes through the admittance layer.

    Returns:
        { controller_name, chain_root, ft_topic, ok, errors }
    """
```

```python
async def setup_impedance_controller(
    robot_path: str,
    target_frame: str = "tool0",
    Kx: list[float] = [400.0]*3,        # cartesian stiffness (N/m)
    Kr: list[float] = [40.0]*3,         # rotational stiffness (N·m/rad)
    Dx: list[float] = [40.0]*3,         # cartesian damping
    Dr: list[float] = [4.0]*3,
    null_space_stiffness: float = 0.5,  # joint-space null space
    null_space_damping: float = 0.5,
    torque_mode: bool = False,          # True = need torque-mode robot
) -> dict:
    """Install Cartesian impedance control (matthias-mayr style).
    Requires torque-mode robot (Franka FCI in libfranka, etc.).

    For non-torque-mode robots, falls back to admittance impl with
    structured warning.
    """
```

### 3.4 F/T sensor model

For sim, we use Isaac Sim's built-in `force_torque_sensor` tool to attach
a 6-axis F/T sensor at the wrist. The admittance controller reads the
sensor's `Wrench` topic. Spec adds:

```python
async def attach_ft_sensor(
    robot_path: str,
    link_name: str = "tool0",
    sensor_path: str = "/World/Franka/FT_Sensor",
    noise_std: float = 0.0,
) -> dict:
    """Mount a 6-axis F/T sensor at link_name. Publishes to
    /World/Franka/FT_Sensor/Wrench topic for ros2_control consumption.
    """
```

### 3.5 ROS2-bridge wiring

ros2_control runs in a ROS2 graph; Isaac Sim 5.1 has `isaacsim.ros2.bridge`
already. The compliance-controller's joint outputs map to Isaac's
articulation drive via the existing FollowJointTrajectory action. We add
one new conduit:

```python
# in isaac_assist/extension.py — new lifecycle hook
def install_admittance_bridge(controller_yaml: Path) -> bool:
    """Load ros2_control configuration with admittance_controller,
    wire its FT subscription to our F/T sensor, wire its joint output
    to Isaac's ArticulationController."""
```

### 3.6 Test fixtures

- `tests/test_admittance_controller.py`: spawn Franka, attach FT sensor,
  apply external wrench via tool API, verify EE displaces according to
  spring law within tolerance.
- `tests/test_impedance_controller.py`: torque-mode Franka, peg held in
  gripper, push peg against vertical wall, verify peg stays at wall
  (no overshoot), force-equilibrium reached.

---

## 4. Layer 2 — Foundation-Model Policy Inference

### 4.1 Goal

Wrap zero-shot VLA models (no training) behind a uniform tool API. After
this lands, any canonical can opt into VLA control by declaring
`policy: "groot_n17_droid"` or similar in its template.

### 4.2 Supported models (initial set)

| Model | Source | Embodiment | Zero-shot success (DROID) |
|---|---|---|---|
| **GR00T N1.7-DROID** | [nvidia/GR00T-N1.7-DROID](https://huggingface.co/nvidia/GR00T-N1.7-DROID) | Franka (3B params) | 60-80% on related tasks |
| **Pi0-FAST DROID** | [Pi0 LeRobot docs](https://huggingface.co/docs/lerobot/pi0) | Franka + 7 others | 80-95% trained, 40-60% zero-shot |
| **OpenVLA-7B** | [openvla/openvla-7b](https://huggingface.co/openvla/openvla-7b) | WidowX zero-shot, Franka requires fine-tune | n/a for Franka zero-shot |

OpenVLA is shipped behind a flag because Franka requires fine-tune; we
support it for completeness and future fine-tuning work.

### 4.3 Tool API surface

```python
async def execute_vla_policy(
    robot_path: str,
    policy: Literal["groot_n17_droid", "pi0_fast_droid", "openvla_7b"],
    text_instruction: str,           # "insert the peg into the round hole"
    camera_paths: list[str],         # ["/World/Cam_wrist", "/World/Cam_overhead"]
    target_paths: list[str] | None = None,   # optional scene context
    duration_s: float = 30.0,        # max execution time
    success_predicate: dict | None = None,   # e.g. {"cube_in_bbox": ...}
    inference_hz: float = 30.0,
    chain_with_compliance: bool = True,  # require Layer 1 controller installed
) -> dict:
    """Run VLA inference loop: query model with (image, text) → joint
    targets → fed to compliance controller (if installed) or directly to
    articulation.

    Returns dict with:
        { ok, n_inferences, mean_inference_ms, last_pose, success,
          telemetry_session_id }
    """
```

### 4.4 Inference infrastructure

A new sidecar service hosts the VLA model:

```
┌─────────────────────────────────────┐
│ vla_inference_service                │
│   - GPU resident model               │
│   - HTTP endpoint /infer (POST)      │
│   - Body: { rgb_b64, instruction }   │
│   - Resp: { action_tokens, ee_delta }│
└──────────┬──────────────────────────┘
           │ HTTP
           ▼
┌─────────────────────────────────────┐
│ Isaac Assist tool_executor           │
│   - execute_vla_policy handler       │
│   - Captures camera, polls /infer,   │
│     applies actions via ros2_control │
└─────────────────────────────────────┘
```

**Files (new):**
- `service/isaac_assist_service/vla/groot_inference_server.py`
- `service/isaac_assist_service/vla/pi0_inference_server.py`
- `service/isaac_assist_service/vla/types.py` — common Action/Observation models
- `service/isaac_assist_service/chat/tools/vla_handlers.py`

### 4.5 Model checkpoint management

Models are pulled lazily from HuggingFace into `~/.cache/isaac-assist/vla/`
on first use. Manifest in `config/vla_models.yaml`:

```yaml
groot_n17_droid:
  hf_repo: "nvidia/GR00T-N1.7-DROID"
  embodiment: "franka_panda"
  vram_gb: 12
  inference_hz_max: 30
  
pi0_fast_droid:
  hf_repo: "lerobot/pi0-fast-droid"
  embodiment: "franka_panda"
  vram_gb: 8
  inference_hz_max: 50
```

### 4.6 Action-space adaptation

VLA outputs are model-specific:
- GR00T: 7-DoF joint deltas (Franka) per timestep
- Pi0-FAST: action chunks of length 32 (EE deltas, gripper state)
- OpenVLA: 7-DoF EE deltas (xyz + rpy) + gripper open/close

The handler normalizes to a canonical `Action` object:

```python
@dataclass
class Action:
    kind: Literal["joint_delta", "joint_abs", "ee_delta", "ee_abs"]
    values: list[float]
    gripper: float | None      # 0=open, 1=closed
    timestamp: float
```

Action-space → compliance-controller integration:
- `joint_delta` / `joint_abs` → `set_joint_targets` (under admittance wrap)
- `ee_delta` / `ee_abs` → `move_to_pose` (under admittance wrap)

---

## 5. Layer 3 — RL Training Pipeline (IndustReal-style)

### 5.1 Goal

Provide a turnkey IndustReal training pipeline for users who need
last-mile sim2real fidelity. **Opt-in, deferred to Phase X of this spec's
roll-out — not in initial MVP.**

### 5.2 Tool API surface

```python
async def train_industreal_policy(
    task: Literal["peg_insert", "gear_assembly", "nut_bolt"],
    env_name: str = "Isaac-Factory-PegInsert-Direct-v0",
    n_envs: int = 8192,              # parallel environments
    total_steps: int = 100_000_000,  # ~8-10h on RTX 5090
    algo: Literal["sapu", "sdf_reward", "sbc"] = "sapu",
    output_checkpoint: str = "/workspace/policies/peg_insert.pt",
) -> dict:
    """Train IndustReal policy. Spawns IsaacLab in headless mode, runs
    PPO/SAC variant with the three IndustReal algorithms (SAPU/SDF/SBC).

    Returns:
        { checkpoint_path, final_success_rate, training_hours, telemetry_run_id }
    """
```

### 5.3 What we ship for Layer 3

- `service/isaac_assist_service/rl/industreal_runner.py` — wraps IsaacLab
- `config/industreal_tasks.yaml` — task → env mappings
- `tests/test_industreal_smoke.py` — 1000-step smoke (no real training)
- Documentation only for full-training workflow (we don't auto-train in CI)

### 5.4 What we DON'T ship in Layer 3 v1

- Custom reward shaping (IndustReal defaults stand)
- Domain randomization curriculum builder (IsaacLab has it)
- Sim2real deployment scripts (Isaac ROS cuMotion-bridge handles it)

---

## 6. Integration with Isaac Assist

### 6.1 New template field `controller_stack`

Templates declare their preferred stack:

```jsonc
{
  "task_id": "CP-NEW-peg-in-hole-single",
  "controller_stack": {
    "policy": "groot_n17_droid",
    "compliance": "cartesian_admittance",
    "stability": "phase_80b_defaults",
    "fallback_chain": [
      {"policy": "pi0_fast_droid"},
      {"policy": "curobo", "compliance": "cartesian_admittance"},
      {"policy": "curobo", "compliance": null}
    ]
  }
}
```

The canonical instantiator reads this field; if a controller in the chain
is unavailable (model not downloaded, hardware mismatch), it falls back
to the next.

### 6.2 Backward compatibility

Existing templates without `controller_stack` continue using
`setup_pick_place_controller` exactly as today. New tools are additive;
old verifier path unaffected.

### 6.3 Direct-eval task harness extension

`scripts/direct_eval.py` adds:
```bash
--policy-override groot_n17_droid    # force VLA on a CP
--compliance-override null           # force no compliance (rigid baseline)
```

For comparing policy/compliance combinations on the same CP.

---

## 7. Tool registry additions

12 new tools across the three layers:

| Tool | Layer | New file |
|---|---|---|
| `setup_admittance_controller` | 1 | `chat/tools/compliance_handlers.py` |
| `setup_impedance_controller` | 1 | same |
| `attach_ft_sensor` | 1 | `chat/tools/sensor_handlers.py` (extend) |
| `set_compliance_params` | 1 | `chat/tools/compliance_handlers.py` |
| `release_compliance` | 1 | same |
| `execute_vla_policy` | 2 | `chat/tools/vla_handlers.py` |
| `list_vla_models` | 2 | same |
| `download_vla_model` | 2 | same |
| `cancel_vla_inference` | 2 | same |
| `vla_inference_diagnostics` | 2 | same |
| `train_industreal_policy` | 3 | `chat/tools/rl_handlers.py` |
| `deploy_industreal_policy` | 3 | same |

All registered into `DATA_HANDLERS` or `CODE_GEN_HANDLERS` per existing
pattern. Each gets `tools_used` declaration in templates that use them.

---

## 8. State machine for contact-rich pick-place

```
IDLE
  │ user_invokes_canonical
  ▼
INSTANTIATE_SCENE (build phase, unchanged from today)
  │ scene_built
  ▼
INSTALL_STABILITY (Phase 80b defaults applied)
  │ stability_ok
  ▼
INSTALL_COMPLIANCE (Layer 1, optional per controller_stack)
  │ compliance_ok
  ▼
LOAD_POLICY (Layer 2 or 3)
  │ policy_loaded
  ▼
APPROACH (free-space; policy commands joint targets)
  │ near_contact_threshold (e.g., 5cm)
  ▼
CONTACT (compliance now driving; F/T feedback active)
  │ insertion_complete OR timeout
  ├─ inserted ────────────────────► RELEASE
  └─ timeout ────────────────────► RECOVERY (back to APPROACH or abort)
  ▼
RELEASE (gripper open, retract)
  │ retracted
  ▼
DONE → emit telemetry, classify via verifier_registry
```

State transitions emit telemetry events per §9. Each phase has a watchdog
timer; on timeout, the supervisor (Kit Supervisor spec) may restart Kit
and replay from INSTANTIATE_SCENE.

---

## 9. Telemetry events

New event types added to multimodal/telemetry.py:

```python
EVENT_COMPLIANCE_INSTALLED = "compliance_installed"
EVENT_COMPLIANCE_PARAMS_UPDATED = "compliance_params_updated"
EVENT_FT_SENSOR_ATTACHED = "ft_sensor_attached"
EVENT_VLA_INFERENCE_STARTED = "vla_inference_started"
EVENT_VLA_INFERENCE_STEP = "vla_inference_step"
EVENT_VLA_INFERENCE_COMPLETED = "vla_inference_completed"
EVENT_CONTACT_PHASE_ENTERED = "contact_phase_entered"
EVENT_CONTACT_PHASE_EXITED = "contact_phase_exited"
EVENT_INSERTION_SUCCEEDED = "insertion_succeeded"
EVENT_INSERTION_FAILED = "insertion_failed"
EVENT_POLICY_FALLBACK = "policy_fallback"
EVENT_RL_TRAINING_STARTED = "rl_training_started"
EVENT_RL_TRAINING_STEP = "rl_training_step"     # at logging interval
EVENT_RL_TRAINING_COMPLETED = "rl_training_completed"
```

Aggregator additions (`scripts/qa/analyze_multimodal_usage.py`):
- `compliance_usage_breakdown(events)` — which controllers active
- `vla_inference_latency(events)` — p50/p95 inference ms
- `contact_phase_success_rate(events)` — insertion success per policy/compliance combo
- `policy_fallback_chain(events)` — how often fallbacks fire and why

---

## 10. Test plan

### 10.1 L0 unit (pure functions)

- `tests/test_compliance_handlers.py` — ≥20 tests
  - Spring-law math (admittance F = K·Δx - D·v)
  - Param validation (K > 0, D > 0, mass > 0)
  - chain_after wiring resolves to valid controller name
- `tests/test_vla_action_normalization.py` — ≥15 tests
  - Action shape conversion (delta ↔ abs)
  - Gripper-state mapping (model-specific → canonical)
  - Action-space → tool-call dispatcher routing
- `tests/test_controller_stack_resolver.py` — ≥10 tests
  - Fallback chain when policy unavailable
  - Stability layer auto-applied
  - Conflict detection (impedance + non-torque robot → warn)

### 10.2 L1 integration (mocked Kit, real model inference)

- `tests/test_vla_inference_e2e.py` (slow, opt-in `-m vla`)
  - Real GR00T inference on synthetic scene → action emitted
  - Real Pi0 inference on synthetic scene → action emitted
  - Latency budget assertions (<200ms p95 per inference)
- `tests/test_compliance_under_load.py`
  - Apply 10N step input to mock F/T → admittance EE displaces correctly
  - Step response time within design budget

### 10.3 E2E (live Kit, opt-in)

- `tests/test_peg_insert_e2e.py` (slow, requires Kit + GPU + GR00T weights)
  - Spawn peg-in-hole scene per CP-NEW-peg-in-hole-single template
  - Run with `controller_stack: groot+admittance`
  - Measure insertion success rate over 20 trials
  - Compare against `curobo+rigid` baseline (today's classical path)
  - Goal: GR00T+admittance ≥50% better than curobo+rigid

### 10.4 Manual validation

- Side-by-side video: GR00T+admittance vs curobo+rigid on CP-peg-insert
- Operator review of contact event traces in telemetry dashboard

---

## 11. Performance SLAs

| Operation | p50 | p95 | Hard limit |
|---|---|---|---|
| admittance step update | 1ms | 2ms | 5ms (500Hz budget) |
| FT sensor read | 0.5ms | 1ms | 2ms |
| GR00T single inference | 30ms | 100ms | 200ms (30Hz budget) |
| Pi0 single inference | 15ms | 50ms | 100ms (50Hz budget) |
| VLA inference loop overhead | 5ms | 15ms | 30ms |
| Policy fallback decision | 1ms | 5ms | 50ms |

VRAM budget per inference instance:
- GR00T N1.7-DROID: 12 GB (single GPU)
- Pi0-FAST DROID: 8 GB
- OpenVLA-7B: 16 GB
- IndustReal training: 24 GB (parallel envs)

Total system requirement for "full stack with Layer 2+3 simultaneously":
RTX 5070 (16 GB) supports GR00T inference only. RTX 4090/5090 (24 GB)
supports GR00T inference + IndustReal training-in-the-background.

---

## 12. Phased roll-out

Each phase is shippable independently. Earlier phases unblock subsequent
ones; non-dependent phases (e.g., Pi0 alongside GR00T) can land in
parallel.

### Phase A — Compliance layer (Layer 1)
- A.1: ros2_control bridge + admittance_controller wiring
- A.2: `setup_admittance_controller` tool + tests
- A.3: F/T sensor model + `attach_ft_sensor` tool
- A.4: `setup_impedance_controller` tool (Cartesian impedance variant)
- A.5: Direct-eval `--compliance-override` flag

**Exit criterion:** existing CP-01 runs identically (no behavior change)
with admittance installed. CP-NEW-peg-in-hole-single shows reduced
PhysX velocity spikes under contact (telemetry).

### Phase B — VLA inference layer (Layer 2)
- B.1: `vla_inference_service` sidecar w/ HuggingFace pull
- B.2: GR00T N1.7-DROID adapter
- B.3: `execute_vla_policy` tool
- B.4: Pi0-FAST DROID adapter
- B.5: Action-space adaptation layer (joint/EE/gripper normalization)
- B.6: Inference latency + memory monitoring

**Exit criterion:** GR00T inference completes one cycle on CP-01
scene (rgb capture → joint targets), staying within p95 latency budget.

### Phase C — Template integration
- C.1: `controller_stack` template field + canonical_instantiator
  resolver
- C.2: Migration of CP-NEW-peg-in-hole-single + CP-NEW-tactile-insertion
  to declare VLA+admittance stack
- C.3: Verifier extension for contact-phase success classification

**Exit criterion:** at least one previously stable_fail CP becomes
stable_ok with the new stack.

### Phase D — Aggregator + dashboards
- D.1: 14 new telemetry event types
- D.2: 4 new aggregator functions
- D.3: Documentation updates

### Phase E — RL training pipeline (Layer 3)
- E.1: IsaacLab IndustReal runner wrapper
- E.2: `train_industreal_policy` tool
- E.3: Checkpoint deployment via cuMotion-bridge

**Exit criterion:** 1k-step smoke training completes; full training
documented but not auto-run in CI.

### Phase F — Hardening
- F.1: VRAM accounting + admission control (refuse VLA load if
  insufficient memory)
- F.2: Inference error recovery (model crash → fallback chain)
- F.3: Cross-policy success-rate dashboard
- F.4: Real-robot deployment guide

---

## 13. Open questions

1. **Compliance layer in Isaac Sim — exact bridge mechanism**
   - Option A: Isaac runs PhysX, ros2_control runs externally, FT topic
     bridges, joint cmd topic bridges back. Standard but adds ROS2
     latency hop.
   - Option B: Port admittance math to Isaac's internal articulation
     controller (in-Kit). Lower latency but custom code, drifts from
     upstream ros2_control.
   - **Default: Option A** for v1; Option B if latency budget breached.

2. **VLA model VRAM contention with cuRobo**
   - cuRobo holds ~2-4 GB GPU memory persistently. GR00T adds 12 GB.
     Total ~16 GB approaches RTX 5070 ceiling.
   - Solution: lazy-unload cuRobo when VLA active; reload on policy
     switch. Implies extra ~10s when fallback chain trips.

3. **Wrench sensor noise model**
   - Real F/T sensors have ~0.1N noise floor. Default `noise_std=0`
     gives unrealistic-clean signal. Calibration data needed.

4. **Pi0 vs GR00T choice for first canonical**
   - Pi0 has wider embodiment coverage; GR00T has NVIDIA-side Isaac
     integration. Empirical comparison needed on CP-peg-insert.

5. **When does RL Layer 3 matter?**
   - VLAs are good enough zero-shot for "reasonable" assemblies. Real
     industrial precision (μm tolerances) likely needs IndustReal
     fine-tune. Defer until empirical demand.

6. **ROS2 distro target**
   - ros2_control mainline supports Humble + Jazzy + Rolling. We pin
     against IsaacLab's choice (currently Humble per Isaac ROS 3.x).

7. **Tactile feedback for Touch2Insert-style methods**
   - Isaac Sim has contact-sensor support; GelSight-style tactile
     simulation requires soft-body + camera-image rendering. Out of
     scope for v1; reserved for tactile-modality work.

---

## 14. References

### Open papers + code (no own training required)
- [NVIDIA Isaac GR00T N1.7](https://github.com/NVIDIA/Isaac-GR00T) — foundation VLA
- [GR00T-N1.7-DROID weights](https://huggingface.co/nvidia/GR00T-N1.7-DROID)
- [Physical Intelligence Pi0](https://huggingface.co/docs/lerobot/pi0)
- [OpenVLA](https://openvla.github.io/) — open VLA baseline
- [VLA Models 2026 Guide](https://www.roboticscenter.ai/guides/vla-models-comparison)

### Compliance control (ros2_control + community)
- [ros2_controllers admittance_controller](https://control.ros.org/rolling/doc/ros2_controllers/admittance_controller/doc/userdoc.html)
- [fzi cartesian_controllers (ros2)](https://github.com/fzi-forschungszentrum-informatik/cartesian_controllers)
- [matthias-mayr Cartesian-Impedance-Controller](https://github.com/matthias-mayr/Cartesian-Impedance-Controller)
- [Variable impedance + imitation learning paper](https://www.sciencedirect.com/science/article/pii/S0736584524001832)

### Contact-rich RL (Isaac Lab IndustReal)
- [IndustReal arxiv](https://ar5iv.labs.arxiv.org/html/2305.17110)
- [IndustReal docs](https://github.com/isaac-sim/IsaacGymEnvs/blob/main/docs/industreal.md)
- [Factory Fast Contact paper](https://arxiv.org/pdf/2205.03532)
- [Isaac Lab Gear-Assembly Sim2Real](https://isaac-sim.github.io/IsaacLab/main/source/policy_deployment/02_gear_assembly/gear_assembly_policy.html)
- [DR peg-in-hole policy paper 2504.04148](https://arxiv.org/html/2504.04148v1)

### Tactile alternatives (deferred to later spec)
- [Touch2Insert zero-shot tactile](https://arxiv.org/abs/2603.03627)
- [TacEx GelSight in Isaac Sim](https://www.researchgate.net/publication/385629897_TacEx_GelSight_Tactile_Simulation_in_Isaac_Sim)

### Prerequisites in IA Full Spec
- IA Full Spec Phase 80b — grip_safe_mode + per-prim physics defaults
- IA Full Spec Phase 70c — articulated pull controller (drawer/door)
- IA Full Spec Phase 70d — drop-target catalog-aware

### This repo's prior specs
- `docs/specs/2026-05-08-multimodal-foundation-spec.md` — LayoutSpec + telemetry
- `docs/specs/2026-05-11-kit-supervisor-spec.md` — drift recovery for verify
- `docs/specs/2026-05-09-master-execution-plan.md` — phase ordering

---

## 15. Implementation checklist

### Layer 1 (Compliance)
- [ ] `service/isaac_assist_service/chat/tools/compliance_handlers.py`
- [ ] `setup_admittance_controller` tool registered in DATA_HANDLERS
- [ ] `setup_impedance_controller` tool registered
- [ ] `attach_ft_sensor` tool extending sensor_handlers
- [ ] `set_compliance_params` runtime mutation tool
- [ ] `release_compliance` cleanup tool
- [ ] ROS2-bridge wiring in `exts/.../assist/extension.py`
- [ ] `config/admittance_controller_defaults.yaml`
- [ ] `tests/test_admittance_handlers.py` (≥20 L0)
- [ ] `tests/test_compliance_e2e.py` (Kit-required, L1)

### Layer 2 (VLA Inference)
- [ ] `service/isaac_assist_service/vla/__init__.py`
- [ ] `service/isaac_assist_service/vla/types.py` (Action, Observation, ModelSpec)
- [ ] `service/isaac_assist_service/vla/groot_inference_server.py`
- [ ] `service/isaac_assist_service/vla/pi0_inference_server.py`
- [ ] `service/isaac_assist_service/vla/checkpoint_manager.py`
- [ ] `config/vla_models.yaml`
- [ ] `service/isaac_assist_service/chat/tools/vla_handlers.py`
- [ ] `execute_vla_policy` + 4 supporting tools
- [ ] `tests/test_vla_action_normalization.py` (≥15 L0)
- [ ] `tests/test_vla_inference_e2e.py` (-m vla, opt-in)

### Layer 3 (RL Training)
- [ ] `service/isaac_assist_service/rl/__init__.py`
- [ ] `service/isaac_assist_service/rl/industreal_runner.py`
- [ ] `config/industreal_tasks.yaml`
- [ ] `service/isaac_assist_service/chat/tools/rl_handlers.py`
- [ ] `train_industreal_policy` + `deploy_industreal_policy`
- [ ] `tests/test_industreal_smoke.py` (1k-step smoke only)
- [ ] Documentation in `docs/guides/training_industreal_policy.md`

### Templates + Integration
- [ ] `controller_stack` field added to template JSON schema
- [ ] canonical_instantiator resolver for stack + fallback chain
- [ ] CP-NEW-peg-in-hole-single migrated to use VLA+admittance stack
- [ ] CP-NEW-tactile-insertion migrated similarly
- [ ] Direct-eval `--policy-override` + `--compliance-override` flags
- [ ] Verifier extension for contact-phase classification

### Telemetry + Aggregator
- [ ] 14 new event types in `multimodal/telemetry.py`
- [ ] 4 new aggregator functions in `analyze_multimodal_usage.py`
- [ ] Per-policy success-rate dashboard
- [ ] VLA inference latency dashboard

### Documentation
- [ ] `docs/guides/installing_vla_models.md`
- [ ] `docs/guides/contact_rich_template_authoring.md`
- [ ] `docs/guides/compliance_tuning.md`
- [ ] Update master execution plan with Phase ordering

---

## 16. Risks and mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| ros2_control latency too high (Option A bridge) | Medium | Compliance step misses 500Hz | Profile early; switch to Option B if needed |
| VLA VRAM exceeds RTX 5070 | High | Cannot run GR00T + cuRobo simultaneously | Lazy-unload cuRobo when VLA active |
| GR00T zero-shot < 50% on our scenes | Medium | Path C/D ineffective | Fall back to Pi0; document need for fine-tune |
| Isaac Lab IndustReal moves API | Medium | Layer 3 breaks | Pin IsaacLab version; vendor critical paths |
| ros2_control admittance_controller upstream regression | Low | F/T-based control unreliable | Pin to mainline tagged release |
| F/T sensor noise model uncalibrated | Low | sim2real degraded | Punt to real-deployment guide; doc-only fix |
| Phase 80b lands later than expected | High | Whole spec blocked | Build Layer 1 in branch; integrate after Phase 80b |

---

## 17. Glossary

- **VLA**: Vision-Language-Action model. Maps (image, text) → robot actions.
- **Compliance**: Robot behavior that yields to external force, like a
  spring-damper.
- **Admittance control**: Force-in → motion-out. Common in position-mode
  robots with external F/T sensor.
- **Impedance control**: Motion-in → force-out. Common in torque-mode
  robots (Franka FCI).
- **FDCC**: Forward Dynamics Compliance Control. fzi's hybrid that
  simulates virtual robot dynamics in real-time to compute compliant
  joint commands.
- **DROID**: Distributed Robot Interaction Dataset, ~25k Franka demos.
  Pi0 and GR00T have variants finetuned on DROID.
- **IndustReal**: NVIDIA RL-for-assembly framework. Includes SAPU,
  SDF-Based Reward, Sampling-Based Curriculum algorithms.
- **CCD**: Continuous Collision Detection. PhysX feature; prevents fast
  objects from tunneling through colliders.
- **EE**: End-effector. The robot's gripper / tool tip.
