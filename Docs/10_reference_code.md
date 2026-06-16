# 10 — Reference Code

End-to-end skeleton. This is illustrative — it compiles in shape but elides ROS2 boilerplate, IK details, and Isaac Lab environment definitions. Use it as the canonical structure to fill out, not as drop-in production code.

## Layout

```
manipulation_stack/
├── pi05_service/
│   ├── server.py
│   ├── prompts.py
│   ├── schema.py
│   └── vla_runtime.py
├── policy_bank/
│   ├── server.py
│   ├── policy_loader.py
│   ├── observation_builder.py
│   └── policies/
│       └── pick_rigid/
│           └── tenthings_v1_open_arm_bimanual/
│               └── v0.3.1/
│                   ├── policy.onnx
│                   ├── normalizer.npz
│                   ├── config.yaml
│                   └── card.md
├── continuity_manager/
│   ├── manager.py
│   ├── scene_tracker.py
│   ├── predicates.py
│   └── escalation.py
├── ros_nodes/
│   ├── observation_pipeline_node.py
│   └── action_arbitration_node.py
├── isaac_lab_envs/
│   └── pick_rigid_env.py
├── config/
│   ├── retract_poses.yaml
│   ├── workspace_bounds.yaml
│   └── calibration/
└── tests/
```

## continuity_manager/manager.py — the orchestrator

```python
from __future__ import annotations
import time
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import requests

from .scene_tracker import SceneTracker
from .predicates import evaluate_predicate, PredicateResult
from .escalation import triage_failure, EscalationDecision

log = logging.getLogger(__name__)


class State(Enum):
    IDLE = "idle"
    PLANNING = "planning"
    EXECUTING = "executing"
    ESCALATING = "escalating"
    SAFE_HOLD = "safe_hold"
    COMPLETE = "complete"


@dataclass
class PhaseResult:
    success: bool
    failure_reason: Optional[str] = None
    evidence: dict = field(default_factory=dict)


@dataclass
class TaskResult:
    success: bool
    completed_phases: int
    final_state: State
    reason: Optional[str] = None


class ContinuityManager:
    def __init__(
        self,
        embodiment_id: str,
        pi05_url: str = "http://localhost:7100",
        policy_bank_url: str = "http://localhost:7101",
        obs_pipeline=None,         # injected ROS2 subscriber
        action_publisher=None,     # injected ROS2 publisher
        max_escalations: int = 2,
    ):
        self.embodiment_id = embodiment_id
        self.pi05_url = pi05_url
        self.policy_bank_url = policy_bank_url
        self.obs_pipeline = obs_pipeline
        self.action_publisher = action_publisher
        self.max_escalations = max_escalations

        self.state = State.IDLE
        self.scene = SceneTracker()
        self.task_spec: Optional[dict] = None
        self.current_phase_idx = 0
        self.escalations_used = 0

    # ----- top-level driver -----

    def run_task(self, goal_text: str) -> TaskResult:
        self.state = State.PLANNING
        try:
            scene_snapshot = self._build_scene_snapshot()
            self.task_spec = self._call_planner(goal_text, scene_snapshot)
        except Exception as e:
            log.exception("planning failed")
            return TaskResult(False, 0, State.IDLE, reason=f"plan_failed: {e}")

        self.scene.bind_semantic_ids(self.task_spec["scene_snapshot"]["objects"])
        self.state = State.EXECUTING
        self.current_phase_idx = 0
        self.escalations_used = 0

        while self.current_phase_idx < len(self.task_spec["phases"]):
            phase = self.task_spec["phases"][self.current_phase_idx]
            log.info("entering phase %d: %s", phase["phase_index"], phase["skill_name"])
            result = self._execute_phase(phase)

            if result.success:
                self.current_phase_idx += 1
                continue

            decision = triage_failure(result.failure_reason)
            if decision == EscalationDecision.FAIL_TO_OPERATOR:
                self.state = State.SAFE_HOLD
                self._engage_safe_hold()
                return TaskResult(False, self.current_phase_idx, self.state, reason=result.failure_reason)

            if decision == EscalationDecision.RETRY_ONCE:
                log.warning("retrying phase %d", self.current_phase_idx)
                retry_result = self._execute_phase(phase)
                if retry_result.success:
                    self.current_phase_idx += 1
                    continue
                # fall through to escalate

            if self.escalations_used >= self.max_escalations:
                self.state = State.SAFE_HOLD
                self._engage_safe_hold()
                return TaskResult(False, self.current_phase_idx, self.state,
                                  reason=f"escalation_budget_exhausted:{result.failure_reason}")

            self._escalate(result)
            # after escalation, task_spec is refreshed and current_phase_idx adjusted

        self.state = State.COMPLETE
        return TaskResult(True, self.current_phase_idx, self.state)

    # ----- phase execution -----

    def _execute_phase(self, phase: dict) -> PhaseResult:
        self._reset_policy(phase["skill_name"])
        phase_start = time.time()
        consecutive_clamps = 0

        while True:
            obs = self.obs_pipeline.latest(timeout=0.05)
            if obs is None:
                return PhaseResult(False, "obs_timeout")
            self.scene.update(obs)

            fail = evaluate_predicate(
                phase.get("failure_predicate"), obs, self.scene, phase_start
            )
            if fail.matched:
                return PhaseResult(False, fail.clause_name, evidence=fail.evidence)

            success = evaluate_predicate(
                phase["success_predicate"], obs, self.scene, phase_start
            )
            if success.matched:
                return PhaseResult(True)

            phase_context = self._build_phase_context(phase, phase_start)
            try:
                act_resp = self._call_policy(phase["skill_name"], obs, phase_context)
            except Exception as e:
                log.exception("policy call failed")
                return PhaseResult(False, "policy_error")

            if act_resp.get("info", {}).get("clamped"):
                consecutive_clamps += 1
                if consecutive_clamps >= 3:
                    return PhaseResult(False, "policy_persistent_clamp")
            else:
                consecutive_clamps = 0

            self._publish_action(phase, act_resp["action"], obs)

    def _escalate(self, prior_result: PhaseResult) -> None:
        self.state = State.ESCALATING
        self.escalations_used += 1
        scene_snapshot = self._build_scene_snapshot()
        new_spec = self._call_replanner(
            task_id=self.task_spec["task_id"],
            current_phase=self.current_phase_idx,
            failure_reason=prior_result.failure_reason,
            scene_snapshot=scene_snapshot,
        )
        # Pi0.5 returns phases starting from current_phase; splice in.
        self.task_spec["phases"] = (
            self.task_spec["phases"][: self.current_phase_idx] + new_spec["phases"]
        )
        self.state = State.EXECUTING

    # ----- service calls -----

    def _call_planner(self, goal_text: str, scene_snapshot: dict) -> dict:
        r = requests.post(
            f"{self.pi05_url}/plan",
            json={
                "goal_text": goal_text,
                "scene_snapshot": scene_snapshot,
                "embodiment_id": self.embodiment_id,
            },
            timeout=10.0,
        )
        r.raise_for_status()
        return r.json()

    def _call_replanner(self, **kwargs) -> dict:
        r = requests.post(f"{self.pi05_url}/replan", json=kwargs, timeout=10.0)
        r.raise_for_status()
        return r.json()

    def _call_policy(self, skill_name: str, obs, phase_context: dict) -> dict:
        r = requests.post(
            f"{self.policy_bank_url}/act",
            json={
                "skill_name": skill_name,
                "embodiment_id": self.embodiment_id,
                "observation": obs.to_policy_input(),
                "phase_context": phase_context,
            },
            timeout=0.05,
        )
        r.raise_for_status()
        return r.json()

    def _reset_policy(self, skill_name: str) -> None:
        requests.post(
            f"{self.policy_bank_url}/reset",
            json={"skill_name": skill_name, "embodiment_id": self.embodiment_id},
            timeout=0.5,
        )

    # ----- helpers -----

    def _build_phase_context(self, phase: dict, phase_start: float) -> dict:
        target = phase["semantic_target"]
        resolved_target = self.scene.resolve_target(target)
        elapsed = time.time() - phase_start
        max_dur = phase["constraints"].get("max_duration_s", 10.0)
        return {
            "skill_name": phase["skill_name"],
            "semantic_target": resolved_target,
            "approach_offset_m": target.get("approach_offset_m"),
            "max_force_n": phase["constraints"].get("max_force_n", 15.0),
            "phase_progress": min(elapsed / max_dur, 1.0),
            "carry_object_pose": self.scene.carry_pose_or_none(),
        }

    def _build_scene_snapshot(self) -> dict:
        obs = self.obs_pipeline.latest(timeout=0.5)
        return {
            "timestamp": obs.timestamp,
            "frame": "base_link",
            "rgb": obs.rgb_scene_b64,
            "objects": [o.to_dict() for o in obs.detected_objects],
        }

    def _publish_action(self, phase: dict, action, obs) -> None:
        lead_arm = phase["hand_assignment"].get("lead_arm")  # "left" or "right"
        self.action_publisher.publish(
            mode=phase["hand_assignment"].get("mode", "SINGLE"),
            lead_arm=lead_arm,
            action=action,
        )

    def _engage_safe_hold(self) -> None:
        self.action_publisher.engage_safe_hold()
```

## continuity_manager/predicates.py

```python
from dataclasses import dataclass, field
import time
from typing import Optional


@dataclass
class PredicateResult:
    matched: bool
    clause_name: Optional[str] = None
    evidence: dict = field(default_factory=dict)


def evaluate_predicate(spec, obs, scene, phase_start) -> PredicateResult:
    if spec is None:
        return PredicateResult(False)
    t = spec["type"]
    if t == "AND":
        results = [evaluate_predicate(c, obs, scene, phase_start) for c in spec["clauses"]]
        if all(r.matched for r in results):
            return PredicateResult(True)
        return PredicateResult(False)
    if t == "OR":
        for c in spec["clauses"]:
            r = evaluate_predicate(c, obs, scene, phase_start)
            if r.matched:
                return r
        return PredicateResult(False)
    handler = LEAF_PREDICATES.get(t)
    if handler is None:
        raise ValueError(f"unknown predicate type: {t}")
    return handler(spec, obs, scene, phase_start)


def _gripper_closed(spec, obs, scene, phase_start) -> PredicateResult:
    arm = spec["arm"]
    width = obs.gripper_width(arm)
    if spec["min_width_m"] <= width <= spec["max_width_m"]:
        return PredicateResult(True, "gripper_closed", {"width": width})
    return PredicateResult(False)


def _gripper_open(spec, obs, scene, phase_start) -> PredicateResult:
    arm = spec["arm"]
    width = obs.gripper_width(arm)
    if width >= spec["min_width_m"]:
        return PredicateResult(True, "gripper_open", {"width": width})
    return PredicateResult(False)


def _object_attached(spec, obs, scene, phase_start) -> PredicateResult:
    tracked = scene.get(spec["object_id"])
    if tracked is None:
        return PredicateResult(False)
    arm = spec["arm"]
    tcp = obs.tcp_pose(arm)
    dist = scene.distance(tracked.pose, tcp)
    if dist < 0.02 and tracked.tracked_for_frames(arm, n=5):
        scene.mark_attached(spec["object_id"], arm)
        return PredicateResult(True, "object_attached", {"distance": dist})
    return PredicateResult(False)


def _lift_clearance(spec, obs, scene, phase_start) -> PredicateResult:
    tracked = scene.get(spec["object_id"])
    if tracked is None:
        return PredicateResult(False)
    z_initial = tracked.initial_z
    z_now = tracked.pose.position[2]
    if z_now - z_initial >= spec["min_clearance_m"]:
        return PredicateResult(True, "lift_clearance", {"clearance": z_now - z_initial})
    return PredicateResult(False)


def _force_exceeded(spec, obs, scene, phase_start) -> PredicateResult:
    f_mag = max(obs.ft_magnitude("left"), obs.ft_magnitude("right"))
    if f_mag > spec["threshold_n"]:
        return PredicateResult(True, "force_exceeded", {"force": f_mag})
    return PredicateResult(False)


def _duration_exceeded(spec, obs, scene, phase_start) -> PredicateResult:
    elapsed = time.time() - phase_start
    if elapsed > spec["threshold_s"]:
        return PredicateResult(True, "duration_exceeded", {"elapsed": elapsed})
    return PredicateResult(False)


def _object_lost(spec, obs, scene, phase_start) -> PredicateResult:
    tracked = scene.get(spec["object_id"])
    if tracked is None or tracked.frames_missing >= spec["missing_frames"]:
        return PredicateResult(True, "object_lost", {"object_id": spec["object_id"]})
    return PredicateResult(False)


def _pose_reached(spec, obs, scene, phase_start) -> PredicateResult:
    target = scene.current_phase_target_pose()
    lead_tcp = obs.tcp_pose(scene.current_lead_arm())
    dpos = scene.distance(lead_tcp, target)
    drot = scene.angular_distance(lead_tcp, target)
    if dpos <= spec["tolerance_m"] and drot <= spec["tolerance_rad"]:
        return PredicateResult(True, "pose_reached", {"dpos": dpos, "drot": drot})
    return PredicateResult(False)


def _object_resting_on(spec, obs, scene, phase_start) -> PredicateResult:
    obj = scene.get(spec["object_id"])
    sup = scene.get(spec["support_id"])
    if obj is None or sup is None:
        return PredicateResult(False)
    if obj.is_stable(window_frames=10) and scene.is_above_xy(obj, sup):
        return PredicateResult(True, "object_resting_on")
    return PredicateResult(False)


LEAF_PREDICATES = {
    "gripper_closed": _gripper_closed,
    "gripper_open": _gripper_open,
    "object_attached": _object_attached,
    "lift_clearance": _lift_clearance,
    "force_exceeded": _force_exceeded,
    "duration_exceeded": _duration_exceeded,
    "object_lost": _object_lost,
    "pose_reached": _pose_reached,
    "object_resting_on": _object_resting_on,
}
```

## continuity_manager/escalation.py

```python
from enum import Enum


class EscalationDecision(Enum):
    REPLAN = "replan"
    RETRY_ONCE = "retry_once"
    FAIL_TO_OPERATOR = "fail"


_TRIAGE = {
    "force_exceeded": EscalationDecision.FAIL_TO_OPERATOR,
    "duration_exceeded": EscalationDecision.REPLAN,
    "object_lost": EscalationDecision.REPLAN,
    "pose_unreachable": EscalationDecision.REPLAN,
    "policy_persistent_clamp": EscalationDecision.FAIL_TO_OPERATOR,
    "policy_error": EscalationDecision.FAIL_TO_OPERATOR,
    "obs_timeout": EscalationDecision.FAIL_TO_OPERATOR,
}


def triage_failure(reason: str) -> EscalationDecision:
    return _TRIAGE.get(reason, EscalationDecision.RETRY_ONCE)
```

## policy_bank/server.py (sketch)

```python
from fastapi import FastAPI, HTTPException
import numpy as np
import onnxruntime as ort
from .policy_loader import PolicyHandle, load_all_from_manifest
from .observation_builder import build_obs_vector
from .safety_wrapper import safe_action

app = FastAPI()
HANDLES: dict[tuple[str, str], PolicyHandle] = load_all_from_manifest("policies/manifest.yaml")


@app.post("/act")
def act(req: ActRequest) -> dict:
    key = (req.skill_name, req.embodiment_id)
    h = HANDLES.get(key)
    if h is None:
        raise HTTPException(404, f"no policy for {key}")

    obs_vec = build_obs_vector(req.observation, req.phase_context, h.config)
    obs_vec = (obs_vec - h.normalizer.mean) / (h.normalizer.std + 1e-6)
    raw_action = h.session.run(None, {"obs": obs_vec[None].astype(np.float32)})[0][0]

    action, clamped = safe_action(raw_action, h.config, req.observation)

    return {
        "action": action.tolist(),
        "value_estimate": None,
        "info": {"clamped": clamped, "policy_version": h.version},
    }


@app.post("/reset")
def reset(req: ResetRequest) -> dict:
    # stateless policies — no-op; recurrent ones would clear hidden state here
    return {"ok": True}


@app.get("/policies")
def list_policies() -> list[dict]:
    return [h.describe() for h in HANDLES.values()]
```

## isaac_lab_envs/pick_rigid_env.py (skeleton — fill with actual Isaac Lab APIs)

```python
import torch
from isaaclab.envs import ManagerBasedRLEnv, ManagerBasedRLEnvCfg
from isaaclab.assets import ArticulationCfg, RigidObjectCfg
from isaaclab.scene import InteractiveSceneCfg
from isaaclab.managers import ObservationTermCfg, RewardTermCfg, TerminationTermCfg


class PickRigidEnvCfg(ManagerBasedRLEnvCfg):
    # populate with:
    # - scene: bimanual robot articulation matching the embodiment
    # - rigid_object: target object with randomized pose/scale/mass
    # - actions: delta_tcp + gripper command on lead arm only
    # - observations: tcp_pose, tcp_vel, gripper, ft, wrist_rgb_features, target_pose, phase_progress
    # - rewards: approach + alignment + grasp + lift_clearance - action_norm - drop
    # - terminations: success (lift achieved), failure (force, drop, time)
    # - randomization events: see 08
    pass


class PickRigidEnv(ManagerBasedRLEnv):
    """Single-arm pick of a rigid object. Lead arm trains; idle arm fixed at retract."""
    cfg: PickRigidEnvCfg
    # standard RL env interface; populate with reward shaping from 08
```

## ros_nodes/observation_pipeline_node.py (sketch)

```python
import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Image, JointState
from geometry_msgs.msg import WrenchStamped
import message_filters
from manipulation_msgs.msg import Observation as ObsMsg


class ObservationPipelineNode(Node):
    def __init__(self):
        super().__init__("observation_pipeline")
        cam_scene_rgb = message_filters.Subscriber(self, Image, "/camera_scene/rgb")
        cam_scene_depth = message_filters.Subscriber(self, Image, "/camera_scene/depth")
        joints_left = message_filters.Subscriber(self, JointState, "/arm_left/joint_states")
        joints_right = message_filters.Subscriber(self, JointState, "/arm_right/joint_states")
        ft_left = message_filters.Subscriber(self, WrenchStamped, "/arm_left/ft")
        ft_right = message_filters.Subscriber(self, WrenchStamped, "/arm_right/ft")

        sync = message_filters.ApproximateTimeSynchronizer(
            [cam_scene_rgb, cam_scene_depth, joints_left, joints_right, ft_left, ft_right],
            queue_size=10,
            slop=0.030,
        )
        sync.registerCallback(self.on_synced)

        self.pub = self.create_publisher(ObsMsg, "/manipulation/observation", 10)
        self.detector = ObjectDetector("yolo_world")     # or grounded-sam-2
        self.tracker = ObjectTracker3D()
        self.pose_estimator = PoseEstimator("foundationpose")

    def on_synced(self, rgb, depth, jl, jr, fl, fr):
        detections = self.detector.run(rgb)
        poses = self.pose_estimator.run(rgb, depth, detections)
        tracked = self.tracker.update(poses)
        msg = self.assemble(rgb, depth, jl, jr, fl, fr, tracked)
        self.pub.publish(msg)
```

## Bringup

```bash
# Terminal 1: ROS2 base stack (existing nav + base + telescope) — out of scope here.

# Terminal 2: Pi0.5 service
python -m pi05_service.server  # listens on :7100

# Terminal 3: Policy bank
python -m policy_bank.server   # listens on :7101

# Terminal 4: Observation pipeline (ROS2 node)
ros2 run manipulation_stack observation_pipeline_node

# Terminal 5: Action arbitration (ROS2 node)
ros2 run manipulation_stack action_arbitration_node

# Terminal 6: Continuity Manager — receives goals, drives the loop
python -m continuity_manager.main \
    --embodiment tenthings_v1_open_arm_bimanual \
    --pi05-url http://localhost:7100 \
    --policy-bank-url http://localhost:7101
```

## Test Strategy

| Test | What it covers |
|---|---|
| Unit: predicate evaluator | All leaf predicates, AND/OR composition |
| Unit: escalation triage | Each failure reason maps to expected decision |
| Unit: scene tracker | Object ID continuity, attached/detached transitions |
| Integration: mock Pi0.5 + mock obs → manager runs full task | End-to-end logic w/o hardware or real policy |
| Sim: Isaac Lab env per skill | Policy converges, eval thresholds met |
| Sim: full stack in sim with real Pi0.5 + real policies | Plan + execute on a sim digital twin |
| Hardware: single skill bring-up | Per-skill on-robot eval, n=50 trials |
| Hardware: full task | End-to-end "pick-and-place" task on real |

## What This Skeleton Doesn't Show

- IK solver wiring (cuRobo or TracIK is a multi-file integration).
- ROS2 message definitions (`manipulation_msgs.msg.Observation` is custom; needs an interfaces package).
- Pi0.5 inference internals (depends on which checkpoint and runtime you use — vLLM, transformers, or a custom server).
- Real-robot teleop pipeline for collecting the demo data referenced in `08`.

These are 1–2 weeks each of focused work, not architectural decisions. Fill them after the structure compiles.
