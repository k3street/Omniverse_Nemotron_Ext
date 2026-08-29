#!/usr/bin/env python3
"""Visible Gemini Robotics ER 2 coach test on RoboLab BananaOnPlateTask.

The model is the stage-level embodied-reasoning coach. A proven demonstration
seeds the downward grasp orientation, then bounded local Jacobian IK targets
the live banana and plate poses. A fresh camera/state observation is sent after
every semantic phase instead of calling the model every simulator step.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import math
import os
import random
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Callable, Sequence

import cv2  # Must precede Isaac Lab imports.
import h5py
import numpy as np
import requests
import torch
from dotenv import load_dotenv
from isaaclab.app import AppLauncher


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
load_dotenv(REPO_ROOT / ".env")

parser = argparse.ArgumentParser(
    description="Run all visible Gemini Robotics ER 2 tests on RoboLab's current DROID robot."
)
parser.add_argument("--task", default="BananaOnPlateTask")
parser.add_argument(
    "--instruction",
    default="Pick up the yellow banana and put it on the white plate",
    help=(
        "Natural-language instruction supplied to the model at every fresh "
        "observation-bound motion decision."
    ),
)
parser.add_argument("--model", default="gemini-robotics-er-2-preview")
parser.add_argument("--retry-steps", type=int, default=20)
parser.add_argument(
    "--motion-checkpoint-replans",
    type=int,
    default=3,
    help=(
        "Maximum fresh, observation-bound model attempts after a motion "
        "checkpoint tool call is rejected by the local safety gate."
    ),
)
parser.add_argument(
    "--disable-adaptive-ik",
    action="store_true",
    help="Replay the fixed demonstration instead of targeting live object poses.",
)
parser.add_argument("--adaptive-tolerance", type=float, default=0.012)
parser.add_argument("--adaptive-max-step", type=float, default=0.020)
parser.add_argument("--adaptive-max-iterations", type=int, default=24)
parser.add_argument("--adaptive-settle-steps", type=int, default=12)
parser.add_argument("--adaptive-max-joint-step", type=float, default=0.07)
parser.add_argument("--adaptive-damping", type=float, default=0.05)
parser.add_argument("--adaptive-orientation-tolerance-deg", type=float, default=4.0)
parser.add_argument("--adaptive-max-angle-step-deg", type=float, default=8.0)
parser.add_argument(
    "--coach-interval-iterations",
    type=int,
    default=10,
    help="Send a fresh mid-motion observation to Gemini every N local IK chunks.",
)
parser.add_argument(
    "--periodic-motion-observations",
    action=argparse.BooleanOptionalAction,
    default=False,
    help=(
        "Also poll Gemini at --coach-interval-iterations; disabled by default "
        "so a motion lease runs until completion or local invalidation."
    ),
)
parser.add_argument(
    "--maximum-model-target-correction",
    type=float,
    default=0.10,
    help=(
        "Safety envelope for one model-issued XYZ target correction in meters."
    ),
)
parser.add_argument("--maximum-grasp-drift", type=float, default=0.025)
parser.add_argument("--minimum-transport-lift", type=float, default=0.030)
parser.add_argument("--max-transport-recoveries", type=int, default=8)
parser.add_argument("--recovery-hold-steps", type=int, default=24)
parser.add_argument("--recovery-stability-drift", type=float, default=0.008)
parser.add_argument("--recovery-set-down-clearance", type=float, default=0.006)
parser.add_argument(
    "--rgbd-safety",
    action=argparse.BooleanOptionalAction,
    default=True,
    help="Render depth and include a depth visualization/metrics at motion checkpoints.",
)
parser.add_argument(
    "--ros2-sensor-ingress",
    action=argparse.BooleanOptionalAction,
    default=False,
    help=(
        "Subscribe to normalized ROS 2 touch, force, RGB-D, tracked-object, "
        "collision-stop, and motion-feedback topics when ROS 2 is available; "
        "disabled by default while simulator-native sensing is used."
    ),
)
parser.add_argument(
    "--ros2-touch-topic", default="/isaac_assist/gripper_touch"
)
parser.add_argument(
    "--ros2-contact-wrench-topic",
    default="/isaac_assist/gripper_contact_wrench",
)
parser.add_argument(
    "--ros2-contact-status-topic",
    default="/isaac_assist/gripper_contact_status",
)
parser.add_argument(
    "--ros2-rgbd-status-topic",
    default="/isaac_assist/rgbd_collision_status",
)
parser.add_argument(
    "--ros2-safety-stop-topic", default="/isaac_assist/safety_stop"
)
parser.add_argument(
    "--ros2-tracked-object-status-topic",
    default="/isaac_assist/tracked_object_status",
)
parser.add_argument(
    "--ros2-motion-status-topic", default="/isaac_assist/motion_status"
)
parser.add_argument("--approach-clearance", type=float, default=0.10)
parser.add_argument("--lift-clearance", type=float, default=0.14)
parser.add_argument("--plate-hover-height", type=float, default=0.27)
parser.add_argument(
    "--banana-offset",
    nargs=2,
    type=float,
    default=(0.0, 0.0),
    metavar=("DX", "DY"),
    help="Relocate the banana in robot-root XY meters after reset.",
)
parser.add_argument(
    "--plate-offset",
    nargs=2,
    type=float,
    default=(0.0, 0.0),
    metavar=("DX", "DY"),
    help="Relocate the plate in robot-root XY meters after reset.",
)
parser.add_argument(
    "--banana-yaw-deg",
    type=float,
    default=0.0,
    help="Rotate the banana around world Z after reset and rotate the grasp with it.",
)
parser.add_argument(
    "--randomize-background",
    action="store_true",
    help="Choose a deterministic non-default RoboLab HDRI using --appearance-seed.",
)
parser.add_argument("--appearance-seed", type=int, default=0)
parser.add_argument(
    "--light-intensity",
    type=float,
    help="Override the RoboLab sphere-light intensity for appearance diversity.",
)
parser.add_argument(
    "--disable-residual-centering",
    action="store_true",
    help="Skip the fresh model-governed placement operation after transport.",
)
parser.add_argument("--center-tolerance", type=float, default=0.040)
parser.add_argument("--center-max-step", type=float, default=0.008)
parser.add_argument("--center-max-z-step", type=float, default=0.008)
parser.add_argument("--release-height", type=float, default=0.040)
parser.add_argument("--release-height-tolerance", type=float, default=0.012)
parser.add_argument("--plate-contact-height", type=float, default=0.025)
parser.add_argument("--center-max-iterations", type=int, default=48)
parser.add_argument("--center-settle-steps", type=int, default=16)
parser.add_argument("--center-max-joint-step", type=float, default=0.04)
parser.add_argument("--center-damping", type=float, default=0.05)
parser.add_argument(
    "--disable-release-retreat",
    action="store_true",
    help="Leave the open gripper at the release pose instead of verifying detachment.",
)
parser.add_argument("--retreat-distance", type=float, default=0.080)
parser.add_argument("--retreat-max-step", type=float, default=0.020)
parser.add_argument("--retreat-max-iterations", type=int, default=6)
parser.add_argument("--retreat-settle-steps", type=int, default=12)
parser.add_argument(
    "--demo",
    type=Path,
    default=REPO_ROOT / "artifacts" / "banana_on_plate_demos_v2" / "data.hdf5",
    help="Successful RoboLab demonstration supplying the local joint trajectory.",
)
parser.add_argument(
    "--linger-steps",
    type=int,
    default=90,
    help="Keep the final robot pose visible for this many physics steps.",
)
parser.add_argument("--timeout", type=float, default=120.0)
parser.add_argument("--model-max-retries", type=int, default=2)
parser.add_argument("--model-retry-backoff", type=float, default=2.0)
parser.add_argument(
    "--artifact-dir",
    type=Path,
    default=REPO_ROOT / "artifacts" / "gemini_robotics_er2_robolab",
)
parser.add_argument(
    "--training-episode-dir",
    type=Path,
    help="Successful Gemini completions are published here as HDF5 + two-camera video.",
)
parser.add_argument(
    "--episode-index",
    type=int,
    default=-1,
    help="Training episode index; -1 chooses the next unused index.",
)
parser.add_argument("--record-video-scale", type=float, default=0.5)
parser.add_argument(
    "--disable-training-recording",
    action="store_true",
    help="Run evaluation only; no successful completion is admitted as training data.",
)
parser.add_argument(
    "--disable-contact-telemetry",
    action="store_true",
    help="Disable the Sim 6 two-finger sensor and its success-admission gate.",
)
parser.add_argument("--minimum-contact-coverage", type=float, default=0.95)
parser.add_argument("--minimum-touch-samples", type=int, default=1)
parser.add_argument(
    "--actuator-feedback-position-change",
    type=float,
    default=0.05,
    help=(
        "Minimum normalized actuator-position change which, together with a "
        "tactile change, immediately returns control to the model."
    ),
)
parser.add_argument(
    "--actuator-feedback-force-change",
    type=float,
    default=0.25,
    help=(
        "Minimum tactile-force delta in newtons that counts as a significant "
        "post-actuation observation change."
    ),
)
parser.add_argument(
    "--disable-critic-guidance",
    action="store_true",
    help="Ignore phase-scoped lessons from the previous passive local critique.",
)
parser.add_argument(
    "--disable-world-intent-shadow",
    action="store_true",
    help="Skip the non-authoritative embodiment-neutral Gemini intent probe.",
)
AppLauncher.add_app_launcher_args(parser)
args_cli, _ = parser.parse_known_args()
args_cli.enable_cameras = True
app_launcher = AppLauncher(args_cli)
simulation_app = app_launcher.app


import robolab.constants  # noqa: E402
from robolab.core.environments.runtime import create_env, end_episode  # noqa: E402
from robolab.core.environments.config import parse_env_cfg  # noqa: E402
from robolab.core.observations.observation_utils import unpack_image_obs  # noqa: E402
from robolab.core.utils.vis_utils import visualize_axes  # noqa: E402
from robolab.core.utils.video_utils import VideoWriter  # noqa: E402
from robolab.registrations.droid.auto_env_registrations_abs_ik import (  # noqa: E402
    auto_register_droid_abs_ik_envs,
)
from robolab.robots.droid import DroidJointPositionActionCfg  # noqa: E402
from adaptive_pick_place import (  # noqa: E402
    apply_object_relative_grasp,
    derive_object_relative_grasp,
    derive_manipulation_feedback,
    live_phase_target,
    quaternion_error_axis_angle_wxyz,
    quaternion_multiply_wxyz,
    yaw_quaternion_wxyz,
)
from residual_centering import (  # noqa: E402
    bounded_scalar_step,
    bounded_vector_step,
    bounded_xy_step,
    damped_least_squares_delta,
)
from franka_sensor_schema import (  # noqa: E402
    SENSOR_DIM,
    SIGNAL_SLICES,
    VALIDITY_DIM,
    SensorCaptureBuffer,
    empty_sensor_frame,
    sensor_frame_from_isaac_env,
    summarize_contact_telemetry,
)
from gemini_episode_dataset import GeminiEpisodeDatasetRecorder  # noqa: E402
from robolab_contact_telemetry import (  # noqa: E402
    contact_sensor_runtime_info,
    install_sim6_gripper_contact_sensor,
)
from observation_bound_motion_tools import (  # noqa: E402
    ActuatorFeedbackEventPolicy,
    ActuatorExecutorRegistry,
    ActuatorExecutorSpec,
    MotionExecutorRegistry,
    MotionExecutorSpec,
    MotionLeaseConditions,
    MotionToolValidationError,
    ObservationBoundActuatorGate,
    ObservationBoundMotionGate,
    ObservationBoundOperationGate,
    OperationCandidate,
    assess_actuator_feedback_event,
    actuator_tool_schemas,
    motion_report_yields_to_scheduler,
    motion_lease_source_errors,
    motion_tool_schemas,
    operation_scheduler_tool_schemas,
)
from rgbd_object_axis_tracking import (  # noqa: E402
    estimate_masked_object_axis,
    instance_mask_for_prim_label,
    sign_invariant_axis_error_deg,
)
from sensor_invalidation_registry import (  # noqa: E402
    PredicateResult,
    SensorObservation,
    SensorObservationSnapshot,
    SensorPredicateLease,
    SensorPredicateRegistry,
    SensorPredicateSpec,
)
from sensor_invalidation_ros2 import (  # noqa: E402
    ROS2SensorIngress,
    ROS2SensorIngressConfig,
    overlay_sensor_observations,
    start_ros2_sensor_ingress,
)
from transport_recovery import (  # noqa: E402
    SupportContactMonitor,
    assess_release_detachment,
    assess_recovery_hold,
    placement_completion_event,
    support_aligned_object_quaternion_wxyz,
)
from world_intent_contract import (  # noqa: E402
    WORLD_INTENT_SCHEMA_VERSION,
    WorldIntent,
    build_world_intent_prompt,
)
from service.isaac_assist_service.chat.llm_gemini import GeminiProvider  # noqa: E402


MODEL_ID = args_cli.model
GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    f"{MODEL_ID}:generateContent"
)

# Calibrated base-link grasp transform transferred from RoboLab's successful
# demonstration. ER 2 is the semantic/visual coach; local IK retargets this
# transform to live object poses while preserving the proven orientation.
BANANA_GRASP_OFFSET = torch.tensor([-0.010, -0.023, 0.147], dtype=torch.float32)
BANANA_GRASP_QUAT = torch.tensor([0.555, 0.385, 0.616, -0.406], dtype=torch.float32)
BANANA_GRASP_QUAT /= torch.linalg.norm(BANANA_GRASP_QUAT)
GRIPPER_BASE_TO_FINGERTIP_M = 0.149
TOTAL_TESTS = 10
VALID_CRITIC_PHASES = {
    "global", "approach_banana", "descend", "grasp", "lift", "above_plate",
    "place", "release"
}
ACTIVE_EPISODE_RECORDER: GeminiEpisodeDatasetRecorder | None = None
ACTIVE_SENSOR_MONITOR: SensorCaptureBuffer | None = None
ACTIVE_SENSOR_SAMPLE_INDEX = 0
ACTIVE_ROS2_SENSOR_INGRESS: ROS2SensorIngress | None = None


def _json_from_text(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1]
        cleaned = cleaned.rsplit("```", 1)[0].strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end < start:
        raise ValueError(f"ER 2 did not return a JSON object: {text[:300]}")
    return json.loads(cleaned[start : end + 1])


def _encode_frame(frame: np.ndarray) -> tuple[str, str]:
    ok, encoded = cv2.imencode(".jpg", cv2.cvtColor(frame, cv2.COLOR_RGB2BGR))
    if not ok:
        raise RuntimeError("Failed to JPEG-encode RoboLab camera observation")
    raw = encoded.tobytes()
    return base64.b64encode(raw).decode("ascii"), hashlib.sha256(raw).hexdigest()[:12]


def _load_critic_guidance(path: Path, task: str) -> dict[str, Any]:
    """Load only bounded semantic lessons created by the passive critic."""
    if not path.is_file():
        return {"source_model": None, "lessons": []}
    try:
        memory = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        print(f"[critic-memory] ignored unreadable memory: {error}", flush=True)
        return {"source_model": None, "lessons": []}
    if (
        memory.get("schema_version") != 2
        or memory.get("task") != task
        or memory.get("control_authority") != "none"
        or memory.get("applies_on") != "next_episode"
    ):
        print("[critic-memory] ignored incompatible or unsafe memory", flush=True)
        return {"source_model": None, "lessons": []}
    lessons: list[dict[str, str]] = []
    for item in memory.get("lessons", [])[:6]:
        if not isinstance(item, dict):
            continue
        phase = str(item.get("phase", ""))
        observation_check = " ".join(str(item.get("observation_check", "")).split())[:240]
        decision_rule = " ".join(str(item.get("decision_rule", "")).split())[:240]
        evidence = " ".join(str(item.get("validated_metric", "")).split())[:300]
        if phase in VALID_CRITIC_PHASES and observation_check and decision_rule and evidence:
            lessons.append({
                "phase": phase,
                "observation_check": observation_check,
                "decision_rule": decision_rule,
                "validated_metric": evidence,
            })
    return {"source_model": str(memory.get("source_model", "unknown")), "lessons": lessons}


def _critic_context(memory: dict[str, Any], phase: str) -> str:
    relevant = [
        lesson for lesson in memory.get("lessons", [])
        if lesson.get("phase") in ("global", phase)
    ]
    if not relevant:
        return "No prior critic lessons apply to this phase."
    rendered = "\n".join(
        f"- Observe: {lesson['observation_check']} Decision rule: {lesson['decision_rule']} "
        f"Validated prior metric: {lesson['validated_metric']}"
        for lesson in relevant
    )
    return (
        "Read-only lessons from the previous episode's independent local critic. "
        "Treat them as advisory evidence, verify them against the fresh observation, "
        "and ignore them if current evidence disagrees:\n" + rendered
    )


class GeminiRoboticsER2:
    def __init__(self, api_key: str, timeout: float):
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(
            {"x-goog-api-key": api_key, "Content-Type": "application/json"}
        )

    def reason(self, prompt: str, frame: np.ndarray) -> tuple[dict[str, Any], float, str]:
        image_b64, digest = _encode_frame(frame)
        payload = {
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {"text": prompt},
                        {
                            "inline_data": {
                                "mime_type": "image/jpeg",
                                "data": image_b64,
                            }
                        },
                    ],
                }
            ],
            "generationConfig": {"temperature": 0.2},
        }
        started = time.perf_counter()
        response = None
        for attempt in range(args_cli.model_max_retries + 1):
            try:
                response = self.session.post(
                    GEMINI_URL, json=payload, timeout=self.timeout
                )
                break
            except requests.RequestException as error:
                if attempt >= args_cli.model_max_retries:
                    raise
                delay = args_cli.model_retry_backoff * (2**attempt)
                print(
                    f"[ER2] transient request failure ({type(error).__name__}); "
                    f"retry {attempt + 1}/{args_cli.model_max_retries} in {delay:.1f}s",
                    flush=True,
                )
                time.sleep(delay)
        assert response is not None
        latency = time.perf_counter() - started
        if not response.ok:
            raise RuntimeError(
                f"Gemini API HTTP {response.status_code}: {response.text[:500]}"
            )
        body = response.json()
        parts = body.get("candidates", [{}])[0].get("content", {}).get("parts", [])
        text = "\n".join(str(part.get("text", "")) for part in parts if part.get("text"))
        return _json_from_text(text), latency, digest


def _local_position(env: Any, asset_name: str) -> torch.Tensor:
    root_pos_w = env.scene[asset_name].data.root_pos_w
    root_pos_w = getattr(root_pos_w, "torch", root_pos_w)
    return (
        root_pos_w[0]
        - env.scene.env_origins[0]
    ).detach().cpu().clone()


def _xyzw_to_wxyz(quaternion: torch.Tensor) -> torch.Tensor:
    return torch.cat((quaternion[-1:], quaternion[:3]))


def _wxyz_to_xyzw(quaternion: torch.Tensor) -> torch.Tensor:
    return torch.cat((quaternion[1:], quaternion[:1]))


def _local_quaternion(env: Any, asset_name: str) -> torch.Tensor:
    """Read an Isaac Sim 6 runtime quaternion in RoboLab recording order."""
    quaternion = env.scene[asset_name].data.root_quat_w
    quaternion = getattr(quaternion, "torch", quaternion)[0].detach().cpu().clone()
    return _xyzw_to_wxyz(quaternion)


def _eef_position(env: Any) -> torch.Tensor:
    # Isaac Sim 6 exposes articulation buffers as ProxyArray; always take its
    # explicit torch view. The older FrameTransformer path also flips the nested
    # Robotiq transform in this source-build combination.
    robot = env.scene["robot"]
    index = robot.data.body_names.index("base_link")
    body_pos_w = robot.data.body_pos_w
    body_pos_w = getattr(body_pos_w, "torch", body_pos_w)
    root_pos_w = robot.data.root_pos_w
    root_pos_w = getattr(root_pos_w, "torch", root_pos_w)
    return (
        body_pos_w[0, index]
        - root_pos_w[0]
    ).detach().cpu().clone()


def _eef_quaternion(env: Any) -> torch.Tensor:
    robot = env.scene["robot"]
    index = robot.data.body_names.index("base_link")
    body_quat_w = robot.data.body_quat_w
    body_quat_w = getattr(body_quat_w, "torch", body_quat_w)
    return _xyzw_to_wxyz(body_quat_w[0, index].detach().cpu().clone())


def _step_env(env: Any, action: torch.Tensor):
    """Step once and mirror the executed transition into the active collector."""
    global ACTIVE_SENSOR_SAMPLE_INDEX
    result = env.step(action)
    sensor_frame = None
    if ACTIVE_SENSOR_MONITOR is not None:
        try:
            sensor_frame = sensor_frame_from_isaac_env(env)
        except Exception:
            sensor_frame = empty_sensor_frame()
        ACTIVE_SENSOR_MONITOR.append(sensor_frame, ACTIVE_SENSOR_SAMPLE_INDEX / 15.0)
        ACTIVE_SENSOR_SAMPLE_INDEX += 1
    if ACTIVE_EPISODE_RECORDER is not None:
        ACTIVE_EPISODE_RECORDER.append(
            env,
            action,
            result[0],
            eef_position=_eef_position(env).numpy(),
            eef_quaternion_wxyz=_eef_quaternion(env).numpy(),
            sensor_frame=sensor_frame,
        )
    return result


def _active_contact_summary() -> dict[str, Any]:
    if ACTIVE_SENSOR_MONITOR is None:
        values = np.zeros((0, SENSOR_DIM), dtype=np.float32)
        validity = np.zeros((0, VALIDITY_DIM), dtype=np.float32)
    else:
        values, validity, _ = ACTIVE_SENSOR_MONITOR.arrays()
    return summarize_contact_telemetry(
        values,
        validity,
        minimum_coverage=args_cli.minimum_contact_coverage,
        minimum_touch_samples=args_cli.minimum_touch_samples,
    )


def _current_contact_observation(env: Any) -> dict[str, Any]:
    """Expose the fresh contact sample to the same model observation."""
    try:
        frame = sensor_frame_from_isaac_env(env)
        force_valid = bool(frame.validity[5] > 0.5)
        touch_valid = bool(frame.validity[6] > 0.5)
        force = np.asarray(
            frame.values[SIGNAL_SLICES["gripper_contact_force"]],
            dtype=np.float32,
        )
        touch = bool(
            frame.values[SIGNAL_SLICES["gripper_touch"]][0] >= 0.5
        )
        return {
            "available": force_valid and touch_valid,
            "touch": touch if touch_valid else None,
            "net_force_xyz_n": force.tolist() if force_valid else None,
            "net_force_n": (
                float(np.linalg.vector_norm(force)) if force_valid else None
            ),
        }
    except Exception as error:
        return {
            "available": False,
            "touch": None,
            "net_force_xyz_n": None,
            "net_force_n": None,
            "error": f"{type(error).__name__}: {error}",
        }


def _set_sim6_camera_views(env: Any) -> None:
    """Use look-at poses instead of legacy Sim 5 camera quaternions."""
    origins = env.scene.env_origins
    views = {
        "over_shoulder_left_camera": ((0.05, 0.57, 0.66), (0.48, -0.05, 0.05)),
        "egocentric_mirrored_camera": ((1.50, 0.00, 1.00), (0.42, 0.00, 0.10)),
    }
    for name, (eye, target) in views.items():
        camera = env.scene.sensors[name]
        eye_offset = torch.tensor(eye, dtype=torch.float32, device=camera.device)
        target_offset = torch.tensor(target, dtype=torch.float32, device=camera.device)
        camera.set_world_poses_from_view(origins.to(camera.device) + eye_offset,
                                         origins.to(camera.device) + target_offset)
        camera._update_poses(None)


def _transform_asset_pose(
    env: Any,
    asset_name: str,
    offset_xy: tuple[float, float],
    *,
    yaw_degrees: float = 0.0,
) -> None:
    """Apply deterministic post-reset translation and world-Z rotation."""
    if offset_xy == (0.0, 0.0) and yaw_degrees == 0.0:
        return
    asset = env.scene[asset_name]
    root_pose_w = asset.data.root_pose_w
    root_pose_w = getattr(root_pose_w, "torch", root_pose_w).clone()
    root_pose_w[0, :2] += torch.tensor(
        offset_xy, dtype=root_pose_w.dtype, device=root_pose_w.device
    )
    if yaw_degrees != 0.0:
        current_wxyz = _xyzw_to_wxyz(root_pose_w[0, 3:7])
        yaw_wxyz = yaw_quaternion_wxyz(
            np.deg2rad(yaw_degrees), like=current_wxyz
        )
        root_pose_w[0, 3:7] = _wxyz_to_xyzw(
            quaternion_multiply_wxyz(yaw_wxyz, current_wxyz)
        )
    asset.write_root_pose_to_sim(root_pose_w)
    root_vel_w = asset.data.root_vel_w
    root_vel_w = getattr(root_vel_w, "torch", root_vel_w)
    asset.write_root_velocity_to_sim(torch.zeros_like(root_vel_w))


def _single_exterior_frame(obs: dict[str, Any]) -> np.ndarray:
    frame = obs["image_obs"]["over_shoulder_left_camera"][0]
    if isinstance(frame, torch.Tensor):
        frame = frame.detach().cpu().numpy()
    frame = np.asarray(frame)
    return np.ascontiguousarray(frame[..., :3].astype(np.uint8, copy=False))


def _rgbd_checkpoint_frame(
    env: Any, rgb_frame: np.ndarray
) -> tuple[np.ndarray, dict[str, Any] | None]:
    """Render a human/model-readable depth panel beside the checkpoint RGB."""
    if not args_cli.rgbd_safety:
        return rgb_frame, None
    sensor = env.scene.sensors["over_shoulder_left_camera"]
    depth = sensor.data.output.get("depth")
    if depth is None:
        return rgb_frame, {"available": False, "reason": "depth_output_missing"}
    depth = getattr(depth, "torch", depth)
    if isinstance(depth, torch.Tensor):
        depth = depth[0].detach().cpu().numpy()
    depth = np.asarray(depth).squeeze()
    valid = np.isfinite(depth) & (depth > 0.05) & (depth < 5.0)
    summary: dict[str, Any] = {
        "available": True,
        "shape": list(depth.shape),
        "valid_fraction": float(valid.mean()),
    }
    if bool(valid.any()):
        samples = depth[valid]
        summary.update(
            {
                "minimum_m": float(samples.min()),
                "q05_m": float(np.quantile(samples, 0.05)),
                "median_m": float(np.median(samples)),
            }
        )
    clipped = np.where(valid, np.clip(depth, 0.10, 2.0), 2.0)
    normalized = ((2.0 - clipped) / 1.9 * 255.0).astype(np.uint8)
    colored = cv2.applyColorMap(normalized, cv2.COLORMAP_TURBO)
    colored = cv2.cvtColor(colored, cv2.COLOR_BGR2RGB)
    if colored.shape[:2] != rgb_frame.shape[:2]:
        colored = cv2.resize(
            colored,
            (rgb_frame.shape[1], rgb_frame.shape[0]),
            interpolation=cv2.INTER_NEAREST,
        )
    composite = np.concatenate((rgb_frame, colored), axis=1)
    return np.ascontiguousarray(composite), summary


def _rgbd_object_axis_observation(
    env: Any,
    *,
    prim_label_fragment: str,
    reference_axis: np.ndarray | None = None,
) -> dict[str, Any]:
    """Read a masked RGB-D principal axis without using rigid-body pose state."""
    sensor = env.scene.sensors["over_shoulder_left_camera"]
    depth_value = sensor.data.output.get("depth")
    instance_value = sensor.data.output.get("instance_id_segmentation_fast")
    if depth_value is None or instance_value is None:
        raise ValueError("RGB-D depth or instance-id output is unavailable")

    def _camera_numpy(value: Any) -> np.ndarray:
        value = getattr(value, "torch", value)
        if isinstance(value, torch.Tensor):
            value = value[0].detach().cpu().numpy()
        return np.asarray(value).squeeze()

    depth = _camera_numpy(depth_value)
    instance_ids = _camera_numpy(instance_value)
    info = (sensor.data.info or {}).get("instance_id_segmentation_fast")
    mask, identity = instance_mask_for_prim_label(
        instance_ids,
        info,
        prim_label_fragment,
    )
    intrinsics = getattr(sensor.data.intrinsic_matrices, "torch", None)
    if intrinsics is None:
        intrinsics = sensor.data.intrinsic_matrices
    if isinstance(intrinsics, torch.Tensor):
        intrinsics = intrinsics[0].detach().cpu().numpy()
    observation = estimate_masked_object_axis(
        depth,
        mask,
        np.asarray(intrinsics),
    )
    result = {
        "available": True,
        "source": "rgbd_instance_depth_major_axis",
        "prim_label_fragment": prim_label_fragment,
        **identity,
        **observation.to_dict(),
    }
    if reference_axis is not None:
        result["orientation_error_deg"] = sign_invariant_axis_error_deg(
            reference_axis,
            observation.major_axis_camera,
        )
    return result


def _state(env: Any, initial_banana_z: float) -> dict[str, Any]:
    banana = _local_position(env, "banana")
    plate = _local_position(env, "plate_large")
    eef = _eef_position(env)
    fingertip = eef + torch.tensor([0.0, 0.0, -GRIPPER_BASE_TO_FINGERTIP_M])
    robot = env.scene["robot"]
    finger_index = robot.data.joint_names.index("finger_joint")
    joint_pos = robot.data.joint_pos
    joint_pos = getattr(joint_pos, "torch", joint_pos)
    finger_joint_rad = float(joint_pos[0, finger_index].detach().cpu())
    closed_fraction = max(0.0, min(1.0, finger_joint_rad / (np.pi / 4)))
    fingertip_distance = float(torch.linalg.vector_norm(fingertip - banana))
    banana_lift = float(banana[2]) - initial_banana_z
    plate_xy_error = float(torch.linalg.vector_norm(plate[:2] - banana[:2]))
    height_above_plate = float(banana[2] - plate[2])
    feedback = derive_manipulation_feedback(
        gripper_closed_fraction=closed_fraction,
        fingertip_object_distance_m=fingertip_distance,
        object_lift_m=banana_lift,
        object_target_xy_error_m=plate_xy_error,
        object_height_above_target_m=height_above_plate,
        contact_height_m=args_cli.plate_contact_height,
    )
    return {
        "banana_xyz": banana.tolist(),
        "banana_quaternion_wxyz": _local_quaternion(env, "banana").tolist(),
        "plate_xyz": plate.tolist(),
        "eef_gripper_base_xyz": eef.tolist(),
        "eef_gripper_base_quaternion_wxyz": _eef_quaternion(env).tolist(),
        "gripper_fingertip_center_xyz": fingertip.tolist(),
        "gripper_base_to_fingertip_m": GRIPPER_BASE_TO_FINGERTIP_M,
        "fingertip_banana_distance_m": fingertip_distance,
        "finger_joint_rad": finger_joint_rad,
        "gripper_closed_fraction": closed_fraction,
        "current_contact": _current_contact_observation(env),
        "banana_lift_m": banana_lift,
        "banana_plate_xy_error_m": plate_xy_error,
        "banana_height_above_plate_m": height_above_plate,
        "gripper_closure_obstructed": feedback["gripper_closure_obstructed"],
        "grasp_candidate": feedback["grasp_candidate"],
        "grasp_confirmed": feedback["grasp_confirmed"],
        "banana_plate_contact_proxy": feedback["object_target_contact_proxy"],
    }


def _scene_prompt(state: dict[str, Any], critic_context: str) -> str:
    return f"""You are Gemini Robotics ER 2 supervising a Franka robot with a Robotiq gripper.
Task: Pick up the yellow banana and put it on the white plate.

Fresh simulator state in robot-root coordinates (meters):
{json.dumps(state, indent=2)}

Inspect the attached current multi-camera image. Return ONLY JSON:
{{
  "scene_ok": true,
  "banana_visible": true,
  "plate_visible": true,
  "recommended_sequence": ["approach_banana", "descend", "grasp", "lift", "above_plate", "lower_to_plate", "release", "retreat"],
  "assessment": "brief visual assessment"
}}
Do not output joint angles. Bounded local IK targets the current object poses.

{critic_context}"""


def _stage_prompt(
    phase: str,
    state: dict[str, Any],
    nominal_target: torch.Tensor,
    nominal_quaternion_wxyz: torch.Tensor,
    gripper_closed: bool,
    critic_context: str,
) -> str:
    distance = float(
        torch.linalg.norm(torch.tensor(state["eef_gripper_base_xyz"]) - nominal_target)
    )
    orientation_error_degrees = float(
        torch.rad2deg(
            torch.linalg.vector_norm(
                quaternion_error_axis_angle_wxyz(
                    nominal_quaternion_wxyz,
                    torch.tensor(state["eef_gripper_base_quaternion_wxyz"]),
                )
            )
        )
    )
    return f"""You are Gemini Robotics ER 2 acting as a closed-loop robot coach.
Task: Pick up the yellow banana and put it on the white plate.
Current phase: {phase}
This is a FRESH observation captured after the previous phase completed.

Privileged simulator state in robot-root coordinates (meters):
{json.dumps(state, indent=2)}
Calibrated nominal Cartesian target: {nominal_target.tolist()}
Current EEF-to-target distance: {distance:.4f} m
Object-relative target quaternion (wxyz): {nominal_quaternion_wxyz.tolist()}
Current orientation error: {orientation_error_degrees:.2f} degrees
Requested gripper state: {"closed" if gripper_closed else "open"}

Important tool geometry: eef_gripper_base_xyz is the Robotiq mounting flange,
not the jaws. For this fixed downward grasp orientation, the provided
gripper_fingertip_center_xyz is 0.149 m lower and is the point that must align
with the banana. Do not reject a grasp because the mounting flange is above it.
The measured gripper_closed_fraction is authoritative: values near 1.0 mean
fully closed and values near 0.0 mean fully open. After the grasp phase, the
close command has already been issued; a fraction around 0.10-0.50 can mean the
banana is physically blocking further finger travel and is positive contact
evidence, not an open-gripper command. At phase "lift", execute the lift test
when the fingers visibly surround the banana; the next observation verifies
whether the grasp is physically attached.
The fused grasp_candidate field means closure was obstructed near the banana;
grasp_confirmed means the banana measurably followed the lift. If
banana_plate_contact_proxy is true, the object has reached the plate envelope:
do not request more lowering, and execute release when XY placement is valid.

Inspect the attached current multi-camera image. The calibrated executor will
move toward this live-pose Cartesian target with bounded local Jacobian IK; you
are not controlling individual joints or individual simulator frames.

For phase "grasp", set grasp_ready=true only when the image and measured distance
support closing around the banana.

{critic_context}

Return ONLY JSON:
{{
  "decision": "execute" or "retry" or "abort",
  "grasp_ready": true or false,
  "confidence": 0.0,
  "assessment": "brief visual/state reasoning"
}}"""


def _motion_checkpoint_prompt(
    phase: str,
    state: dict[str, Any],
    checkpoint: dict[str, Any],
) -> str:
    return f"""You are Gemini Robotics ER 2 supervising a robot during motion.
Task: Pick up the yellow banana and put it on the white plate.
Current phase: {phase}

This is a FRESH mid-motion observation, not a phase-boundary image.
Current simulator state:
{json.dumps(state, indent=2)}

Local safety/checkpoint metrics:
{json.dumps(checkpoint, indent=2)}

The local monitor has immediate stop authority. Inspect the image for gripper
slip, dropped object, unexpected contact, or an obstructed route. Return ONLY:
{{
  "decision": "execute" or "pause_regrasp" or "complete" or "abort",
  "confidence": 0.0,
  "assessment": "brief fresh visual/state reasoning"
}}
When RGB-D safety is enabled, the attached image is RGB on the left and a
near-to-far TURBO depth visualization on the right; numeric depth summaries
are included above. Use execute only when it is safe to continue the current
bounded motion. Use complete when the image and state show that the banana is
already on the plate and no more grasp/transport motion is needed."""


def _local_dls_executor_registry(
    trackable_object_ids: Sequence[str] = (),
) -> MotionExecutorRegistry:
    """Register the currently available executor and its configurable surface."""
    normalized_object_ids = sorted(
        {
            str(object_id)
            for object_id in trackable_object_ids
            if isinstance(object_id, str) and object_id
        }
    )
    configuration_properties: dict[str, Any] = {
        "position_tolerance_m": {
            "type": "number", "minimum": 0.001, "maximum": 0.05,
        },
        "translation_step_limit_m": {
            "type": "number", "minimum": 0.001, "maximum": 0.05,
        },
        "maximum_iterations": {
            "type": "integer", "minimum": 1, "maximum": 400,
        },
        "settle_steps": {
            "type": "integer", "minimum": 1, "maximum": 60,
        },
        "joint_step_limit_rad": {
            "type": "number", "minimum": 0.001, "maximum": 0.20,
        },
        "damping": {
            "type": "number", "minimum": 0.001, "maximum": 1.0,
        },
        "orientation_tolerance_deg": {
            "type": "number", "minimum": 0.1, "maximum": 30.0,
        },
        "rotation_step_limit_deg": {
            "type": "number", "minimum": 0.1, "maximum": 30.0,
        },
        "minimum_progress_m": {
            "type": "number", "minimum": 0.0001, "maximum": 0.01,
        },
        "maximum_stalled_observations": {
            "type": "integer", "minimum": 2, "maximum": 20,
        },
        "require_contact": {"type": "boolean"},
        "minimum_contact_force_n": {
            "type": "number", "minimum": 0.0, "maximum": 100.0,
        },
        "maximum_tracked_pose_error_m": {
            "type": "number", "minimum": 0.001, "maximum": 0.30,
        },
        "maximum_tracked_orientation_error_deg": {
            "type": "number", "minimum": 1.0, "maximum": 90.0,
        },
        "minimum_observed_clearance_m": {
            "type": "number", "minimum": 0.0, "maximum": 0.50,
        },
    }
    if normalized_object_ids:
        configuration_properties["tracked_object_id"] = {
            "type": "string",
            "enum": normalized_object_ids,
            "description": (
                "Runtime-advertised object whose RGB-D orientation lease is "
                "being configured."
            ),
        }
    registry = MotionExecutorRegistry()
    registry.register(
        MotionExecutorSpec(
            executor_id="bounded_dls_ik",
            tool_name="execute_bounded_dls_ik",
            description=(
                "Execute the current world-space target with bounded damped "
                "least-squares inverse kinematics. The target controls the pose "
                "reported as eef_gripper_base_xyz in observed state; it is not "
                "the fingertip or object position. Account for the observed "
                "gripper_base_to_fingertip_m offset before correcting it. "
                "Optionally correct the target and configure this invocation "
                "from the fresh observation."
            ),
            configuration_schema={
                "type": "object",
                "additionalProperties": False,
                "properties": configuration_properties,
            },
        )
    )
    return registry


def _local_binary_actuator_registry() -> ActuatorExecutorRegistry:
    """Register the current runtime actuator without changing the protocol."""
    registry = ActuatorExecutorRegistry()
    registry.register(
        ActuatorExecutorSpec(
            executor_id="binary_end_effector_clamp",
            tool_name="execute_binary_end_effector_clamp",
            description=(
                "Command the current binary end-effector clamp from fresh "
                "visual, actuator, and contact evidence. Engage closes the "
                "clamp, disengage opens it, and maintain preserves its current "
                "command. Configure how long the command settles before the "
                "next observation."
            ),
            command_schema={
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "state": {
                        "type": "string",
                        "enum": ["engage", "disengage", "maintain"],
                    }
                },
                "required": ["state"],
            },
            configuration_schema={
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "settle_steps": {
                        "type": "integer",
                        "minimum": 8,
                        "maximum": 120,
                    }
                },
            },
        )
    )
    return registry


def _motion_lease_conditions_from_config(
    config: dict[str, Any],
) -> MotionLeaseConditions:
    """Build the generic lease contract from one executor's optional settings."""
    return MotionLeaseConditions(
        require_contact=bool(config.get("require_contact", False)),
        minimum_contact_force_n=float(config.get("minimum_contact_force_n", 0.0)),
        maximum_tracked_pose_error_m=config.get(
            "maximum_tracked_pose_error_m"
        ),
        minimum_observed_clearance_m=config.get(
            "minimum_observed_clearance_m"
        ),
    )


def _contact_retained_predicate(
    values: dict[str, Any], parameters: dict[str, Any]
) -> PredicateResult:
    touch = values["gripper.touch"]
    force = values["gripper.contact_force_n"]
    minimum = float(parameters.get("minimum_force_n", 0.0))
    if not isinstance(touch, bool):
        raise ValueError("gripper.touch must be boolean")
    if touch is not True:
        return PredicateResult(False, "contact_lost", {"touch": touch})
    if isinstance(force, bool) or not isinstance(force, (int, float)):
        raise ValueError("gripper.contact_force_n must be numeric")
    if float(force) < minimum:
        return PredicateResult(
            False,
            "contact_force_below_lease_minimum",
            {"contact_force_n": float(force), "minimum_force_n": minimum},
        )
    return PredicateResult(
        True,
        "contact_retained",
        {"touch": touch, "contact_force_n": float(force)},
    )


def _numeric_bound_predicate(
    values: dict[str, Any],
    parameters: dict[str, Any],
    *,
    channel: str,
    bound_name: str,
    valid_reason: str,
    invalid_reason: str,
    maximum: bool,
) -> PredicateResult:
    observed = values[channel]
    bound = parameters[bound_name]
    if (
        isinstance(observed, bool)
        or not isinstance(observed, (int, float))
        or isinstance(bound, bool)
        or not isinstance(bound, (int, float))
    ):
        raise ValueError("numeric predicate values must be numbers")
    observed = float(observed)
    bound = float(bound)
    if not math.isfinite(observed) or not math.isfinite(bound):
        raise ValueError("numeric predicate values must be finite")
    valid = observed <= bound if maximum else observed >= bound
    return PredicateResult(
        valid,
        valid_reason if valid else invalid_reason,
        {channel: observed, bound_name: bound},
    )


def _motion_progress_predicate(
    values: dict[str, Any], parameters: dict[str, Any]
) -> PredicateResult:
    stalled = values["motion.stalled_observation_count"]
    maximum = parameters["maximum_stalled_observations"]
    if (
        isinstance(stalled, bool)
        or not isinstance(stalled, int)
        or isinstance(maximum, bool)
        or not isinstance(maximum, int)
    ):
        raise ValueError("motion stall counts must be integers")
    valid = stalled < maximum
    return PredicateResult(
        valid,
        "motion_progress_observed" if valid else "motion_progress_stalled",
        {
            "stalled_observation_count": stalled,
            "maximum_stalled_observations": maximum,
        },
    )


def _collision_stop_clear_predicate(
    values: dict[str, Any], _parameters: dict[str, Any]
) -> PredicateResult:
    stopped = values["scene.collision_stop"]
    if not isinstance(stopped, bool):
        raise ValueError("scene collision-stop observation must be boolean")
    return PredicateResult(
        not stopped,
        "collision_path_clear" if not stopped else "rgbd_collision_stop_requested",
        {"scene.collision_stop": stopped},
    )


def _motion_sensor_predicate_registry() -> SensorPredicateRegistry:
    """Register available local predicate plugins; the lease core stays generic."""
    registry = SensorPredicateRegistry()
    registry.register(
        SensorPredicateSpec(
            predicate_id="scene.no_collision_stop",
            description=(
                "The ROS 2 RGB-D collision monitor has not requested a stop."
            ),
            required_channels=("scene.collision_stop",),
            maximum_age_s=0.5,
            evaluator=_collision_stop_clear_predicate,
        )
    )
    registry.register(
        SensorPredicateSpec(
            predicate_id="motion.progress_not_stalled",
            description=(
                "Fresh robot kinematic feedback continues to reduce target error."
            ),
            required_channels=("motion.stalled_observation_count",),
            maximum_age_s=0.5,
            evaluator=_motion_progress_predicate,
        )
    )
    registry.register(
        SensorPredicateSpec(
            predicate_id="gripper.contact_retained",
            description="Touch and minimum contact force remain observed.",
            required_channels=("gripper.touch", "gripper.contact_force_n"),
            maximum_age_s=0.5,
            evaluator=_contact_retained_predicate,
        )
    )
    registry.register(
        SensorPredicateSpec(
            predicate_id="object.translation_within_error",
            description="Tracked object translation remains inside its lease.",
            required_channels=("object.tracked_translation_error_m",),
            maximum_age_s=0.5,
            evaluator=lambda values, parameters: _numeric_bound_predicate(
                values,
                parameters,
                channel="object.tracked_translation_error_m",
                bound_name="maximum_error_m",
                valid_reason="tracked_translation_within_tolerance",
                invalid_reason="tracked_pose_error_exceeded",
                maximum=True,
            ),
        )
    )
    registry.register(
        SensorPredicateSpec(
            predicate_id="object.orientation_within_error",
            description=(
                "RGB-D tracked object major-axis orientation remains inside "
                "its model-issued tolerance."
            ),
            required_channels=("rgbd.object_orientation_error_deg",),
            maximum_age_s=0.5,
            evaluator=lambda values, parameters: _numeric_bound_predicate(
                values,
                parameters,
                channel="rgbd.object_orientation_error_deg",
                bound_name="maximum_error_deg",
                valid_reason="tracked_orientation_within_tolerance",
                invalid_reason="rgbd_object_orientation_error_exceeded",
                maximum=True,
            ),
        )
    )
    registry.register(
        SensorPredicateSpec(
            predicate_id="scene.clearance_above_minimum",
            description="Observed object-to-support clearance remains sufficient.",
            required_channels=("scene.observed_clearance_m",),
            maximum_age_s=0.5,
            evaluator=lambda values, parameters: _numeric_bound_predicate(
                values,
                parameters,
                channel="scene.observed_clearance_m",
                bound_name="minimum_clearance_m",
                valid_reason="observed_clearance_sufficient",
                invalid_reason="observed_clearance_below_lease_minimum",
                maximum=False,
            ),
        )
    )
    return registry


def _motion_sensor_predicate_leases(
    config: dict[str, Any],
) -> tuple[SensorPredicateLease, ...]:
    leases: list[SensorPredicateLease] = []
    leases.append(
        SensorPredicateLease(
            "motion.progress_not_stalled",
            {
                "maximum_stalled_observations": int(
                    config.get("maximum_stalled_observations", 3)
                )
            },
        )
    )
    if bool(config.get("require_contact", False)):
        leases.append(
            SensorPredicateLease(
                "gripper.contact_retained",
                {"minimum_force_n": float(config.get("minimum_contact_force_n", 0.0))},
            )
        )
    if config.get("maximum_tracked_pose_error_m") is not None:
        leases.append(
            SensorPredicateLease(
                "object.translation_within_error",
                {"maximum_error_m": float(config["maximum_tracked_pose_error_m"])},
            )
        )
    if config.get("maximum_tracked_orientation_error_deg") is not None:
        leases.append(
            SensorPredicateLease(
                "object.orientation_within_error",
                {
                    "maximum_error_deg": float(
                        config["maximum_tracked_orientation_error_deg"]
                    ),
                    "tracked_object_id": config.get("tracked_object_id"),
                },
            )
        )
    if config.get("minimum_observed_clearance_m") is not None:
        leases.append(
            SensorPredicateLease(
                "scene.clearance_above_minimum",
                {
                    "minimum_clearance_m": float(
                        config["minimum_observed_clearance_m"]
                    )
                },
            )
        )
    return tuple(leases)


def _motion_governor_prompt(
    *,
    instruction: str,
    observation_id: str,
    state: dict[str, Any],
    motion_context: dict[str, Any],
    rgbd_summary: dict[str, Any] | None,
    critic_context: str,
) -> str:
    """Build a tool-only motion decision request without task-specific rules."""
    return f"""Govern the next bounded world-space movement using the attached
fresh observation and exactly one advertised tool.

Human instruction:
{instruction}

Fresh observation token: {observation_id}
Observed world state:
{json.dumps(state, indent=2)}
Current motion context:
{json.dumps(motion_context, indent=2)}
RGB-D summary:
{json.dumps(rgbd_summary, indent=2)}

The image contains RGB on the left and depth on the right when depth is
available. Select a registered executor tool to continue or correct the current
world-space target. Select hold_motion when a new observation is required before
movement, or abort_motion when movement is unsafe. Executor settings are
optional and must stay inside their advertised schema. Ground any target
correction and configuration change in current evidence. Measured contact and
touch in observed world state are current physical evidence; interpret them
together with the requested actuator state instead of requiring an unloaded
actuator to reach its full travel. Configure a sufficiently long
maximum_iterations horizon for the target. When the observed state indicates
that an object is being carried, advertise only the invariants needed for this
motion through require_contact, minimum_contact_force_n,
maximum_tracked_pose_error_m, and minimum_observed_clearance_m. These are lease
conditions evaluated locally while the model is not being polled. When an
RGB-D tracked-orientation source is available for a carried object, also set a
maximum_tracked_orientation_error_deg appropriate to the observed geometry so
object rotation or slip interrupts the lease, and identify that object with
tracked_object_id from the runtime-advertised enum. The
lease_condition_sources map in motion context is authoritative: never include a
condition whose corresponding source is null or unavailable. Do not emit prose
or JSON outside the single native tool call.

An invalidated lease is a stopped recovery checkpoint, not automatically a
terminal task failure. Inspect the fresh evidence and choose the next safe
solution. If contact, actuator feedback, and visual tracking show that a carried
object remains stably grasped while the current route or clearance is invalid,
correct the target to restore safe clearance and continue. If the grasp is
unstable, detached, or cannot be established from current evidence, hold for a
fresh operation decision instead of blindly continuing. When
previous_motion_tool_outcome is present, correct that rejected proposal while
preserving the local safety bounds.

When scheduler_decision explicitly dispatches continue.runtime_motion after a
previous hold, the fresh operation decision has already resolved whether to
wait for another operation. Do not repeat hold_motion solely because the same
state is still visible. Use the current evidence to select a bounded movement
that can safely change that state and advance the instruction, or select
abort_motion when no such movement is safe. The recovery movement itself is
your decision; it is not supplied by a phase-specific controller.

{critic_context}"""


def _motion_registry_for_observation_sources(
    registry: MotionExecutorRegistry,
    source_context: dict[str, Any],
) -> MotionExecutorRegistry:
    """Advertise only lease settings backed by this observation's sensors."""
    unavailable_fields: set[str] = set()
    if source_context.get("contact") is None:
        unavailable_fields.update(("require_contact", "minimum_contact_force_n"))
    if source_context.get("tracked_pose") is None:
        unavailable_fields.add("maximum_tracked_pose_error_m")
    orientation_sources = source_context.get("tracked_orientation")
    if not isinstance(orientation_sources, dict) or not orientation_sources:
        unavailable_fields.update(
            ("maximum_tracked_orientation_error_deg", "tracked_object_id")
        )
    if source_context.get("observed_clearance") is None:
        unavailable_fields.add("minimum_observed_clearance_m")
    filtered = MotionExecutorRegistry()
    for spec in registry.specs():
        schema = json.loads(json.dumps(spec.configuration_schema))
        properties = schema.get("properties", {})
        for field in unavailable_fields:
            properties.pop(field, None)
        filtered.register(
            MotionExecutorSpec(
                executor_id=spec.executor_id,
                tool_name=spec.tool_name,
                description=spec.description,
                configuration_schema=schema,
            )
        )
    return filtered


def _choose_observation_bound_motion_tool(
    provider: GeminiProvider,
    registry: MotionExecutorRegistry,
    *,
    instruction: str,
    observation_prefix: str,
    frame: np.ndarray,
    state: dict[str, Any],
    current_target: torch.Tensor,
    motion_context: dict[str, Any],
    rgbd_summary: dict[str, Any] | None,
    critic_context: str,
) -> tuple[dict[str, Any], float, str]:
    """Ask the selected model to invoke one fresh-observation motion tool."""
    encoded, digest = _encode_frame(frame)
    observation_id = f"{observation_prefix}:{digest}"
    source_context = motion_context.get("lease_condition_sources", {})
    if not isinstance(source_context, dict):
        source_context = {}
    observation_registry = _motion_registry_for_observation_sources(
        registry,
        source_context,
    )
    gate = ObservationBoundMotionGate(
        observation_id=observation_id,
        current_target_m=current_target.tolist(),
        maximum_correction_m=args_cli.maximum_model_target_correction,
        registry=observation_registry,
    )
    messages = [
        {
            "role": "system",
            "content": (
                "You are a visual motion governor. Every movement decision must "
                "be expressed as exactly one of the runtime-advertised tools."
            ),
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/jpeg;base64,{encoded}",
                        "detail": "high",
                    },
                },
                {
                    "type": "text",
                    "text": _motion_governor_prompt(
                        instruction=instruction,
                        observation_id=observation_id,
                        state=state,
                        motion_context=motion_context,
                        rgbd_summary=rgbd_summary,
                        critic_context=critic_context,
                    ),
                },
            ],
        },
    ]
    started = time.perf_counter()
    response = asyncio.run(
        asyncio.wait_for(
            provider.complete(
                messages,
                {
                    "tools": motion_tool_schemas(
                        observation_id, observation_registry
                    ),
                    "tool_choice": "required",
                },
            ),
            timeout=args_cli.timeout,
        )
    )
    latency = time.perf_counter() - started
    tool_calls = response.tool_calls or []
    if len(tool_calls) != 1:
        error = (
            "model must issue exactly one motion tool call; "
            f"received {len(tool_calls)}"
        )
        return (
            {
                "decision": "retry",
                "grasp_ready": False,
                "confidence": 0.0,
                "assessment": f"Motion tool rejected by safety gate: {error}",
                "motion_tool": {
                    "status": "rejected",
                    "observation_id": observation_id,
                    "tool_name": None,
                    "arguments": None,
                    "error": error,
                    "model_text": str(response.text or "")[:500],
                },
                "target_xyz_m": current_target.tolist(),
                "executor_id": None,
                "executor_config": {},
            },
            latency,
            digest,
        )
    try:
        outcome = gate.dispatch(tool_calls[0])
    except MotionToolValidationError as error:
        function = tool_calls[0].get("function", {})
        return (
            {
                "decision": "retry",
                "grasp_ready": False,
                "confidence": 0.0,
                "assessment": f"Motion tool rejected by safety gate: {error}",
                "motion_tool": {
                    "status": "rejected",
                    "observation_id": observation_id,
                    "tool_name": function.get("name"),
                    "arguments": function.get("arguments"),
                    "error": str(error),
                },
                "target_xyz_m": current_target.tolist(),
                "executor_id": None,
                "executor_config": {},
            },
            latency,
            digest,
        )
    if outcome.action == "execute":
        lease_conditions = _motion_lease_conditions_from_config(
            dict(outcome.executor_config)
        )
        source_errors = motion_lease_source_errors(
            lease_conditions,
            contact_available=source_context.get("contact") is not None,
            tracked_pose_available=source_context.get("tracked_pose") is not None,
            observed_clearance_available=(
                source_context.get("observed_clearance") is not None
            ),
        )
        if (
            outcome.executor_config.get(
                "maximum_tracked_orientation_error_deg"
            )
            is not None
        ):
            tracked_object_id = outcome.executor_config.get("tracked_object_id")
            orientation_sources = source_context.get("tracked_orientation")
            if not isinstance(tracked_object_id, str) or not tracked_object_id:
                source_errors = (
                    *source_errors,
                    "tracked_object_id is required for an RGB-D orientation lease",
                )
            elif not isinstance(orientation_sources, dict) or (
                orientation_sources.get(tracked_object_id) is None
            ):
                source_errors = (
                    *source_errors,
                    "RGB-D tracked-orientation source is unavailable for "
                    f"{tracked_object_id!r}",
                )
        if source_errors:
            error = "lease references unavailable observations: " + "; ".join(
                source_errors
            )
            rejected_tool = outcome.to_dict()
            rejected_tool.update({"status": "rejected", "error": error})
            return (
                {
                    "decision": "retry",
                    "grasp_ready": False,
                    "confidence": 0.0,
                    "assessment": f"Motion tool rejected by safety gate: {error}",
                    "motion_tool": rejected_tool,
                    "target_xyz_m": current_target.tolist(),
                    "executor_id": None,
                    "executor_config": {},
                },
                latency,
                digest,
            )
    decision = {
        "decision": (
            "execute"
            if outcome.action == "execute"
            else "retry" if outcome.action == "hold" else "abort"
        ),
        "grasp_ready": outcome.action == "execute",
        "confidence": outcome.confidence,
        "assessment": outcome.reason,
        "motion_tool": outcome.to_dict(),
        "target_xyz_m": list(outcome.target_after_m),
        "executor_id": outcome.executor_id,
        "executor_config": dict(outcome.executor_config),
    }
    return decision, latency, digest


def _actuator_governor_prompt(
    *,
    instruction: str,
    observation_id: str,
    state: dict[str, Any],
    actuator_context: dict[str, Any],
    rgbd_summary: dict[str, Any] | None,
    critic_context: str,
) -> str:
    """Build a task-neutral actuator decision request."""
    return f"""Govern the next bounded actuator transition using the attached
fresh observation and exactly one advertised tool.

Human instruction:
{instruction}

Fresh observation token: {observation_id}
Observed world state:
{json.dumps(state, indent=2)}
Current actuator context:
{json.dumps(actuator_context, indent=2)}
RGB-D summary:
{json.dumps(rgbd_summary, indent=2)}

The image contains RGB on the left and depth on the right when depth is
available. Choose a registered actuator executor only when its command advances
the human instruction and is safe in the fresh observed state. Select
hold_actuation when another observation is required before changing the
actuator. abort_actuation is a terminal task abort, not a request to stop this
transition and replan. If the current actuator operation should stop but the
human instruction remains recoverable, select hold_actuation so the fresh
operation scheduler can choose another capability. Select abort_actuation only
when no safe recovery can continue the overall instruction. Measured touch and
force are physical evidence: incomplete travel with touch can indicate an
object is obstructing closure, while disengagement should remove retained
contact. Executor settings are optional and must remain inside their advertised
schema. Do not emit prose or JSON outside the single native tool call.

{critic_context}"""


def _choose_observation_bound_actuator_tool(
    provider: GeminiProvider,
    registry: ActuatorExecutorRegistry,
    *,
    instruction: str,
    observation_prefix: str,
    frame: np.ndarray,
    state: dict[str, Any],
    actuator_context: dict[str, Any],
    rgbd_summary: dict[str, Any] | None,
    critic_context: str,
) -> tuple[dict[str, Any], float, str]:
    """Ask the selected model for one fresh-observation actuator call."""
    encoded, digest = _encode_frame(frame)
    observation_id = f"{observation_prefix}:{digest}"
    gate = ObservationBoundActuatorGate(
        observation_id=observation_id,
        registry=registry,
    )
    messages = [
        {
            "role": "system",
            "content": (
                "You are a visual actuator governor. Every actuator decision "
                "must be exactly one runtime-advertised native tool call."
            ),
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/jpeg;base64,{encoded}",
                        "detail": "high",
                    },
                },
                {
                    "type": "text",
                    "text": _actuator_governor_prompt(
                        instruction=instruction,
                        observation_id=observation_id,
                        state=state,
                        actuator_context=actuator_context,
                        rgbd_summary=rgbd_summary,
                        critic_context=critic_context,
                    ),
                },
            ],
        },
    ]
    started = time.perf_counter()
    response = asyncio.run(
        asyncio.wait_for(
            provider.complete(
                messages,
                {
                    "tools": actuator_tool_schemas(observation_id, registry),
                    "tool_choice": "required",
                },
            ),
            timeout=args_cli.timeout,
        )
    )
    latency = time.perf_counter() - started
    tool_calls = response.tool_calls or []
    if len(tool_calls) != 1:
        error = (
            "model must issue exactly one actuator tool call; "
            f"received {len(tool_calls)}"
        )
        return (
            {
                "decision": "retry",
                "confidence": 0.0,
                "assessment": f"Actuator tool rejected by safety gate: {error}",
                "actuator_tool": {
                    "status": "rejected",
                    "observation_id": observation_id,
                    "tool_name": None,
                    "arguments": None,
                    "error": error,
                    "model_text": str(response.text or "")[:500],
                },
                "executor_id": None,
                "command": {},
                "executor_config": {},
            },
            latency,
            digest,
        )
    try:
        outcome = gate.dispatch(tool_calls[0])
    except MotionToolValidationError as error:
        function = tool_calls[0].get("function", {})
        return (
            {
                "decision": "retry",
                "confidence": 0.0,
                "assessment": f"Actuator tool rejected by safety gate: {error}",
                "actuator_tool": {
                    "status": "rejected",
                    "observation_id": observation_id,
                    "tool_name": function.get("name"),
                    "arguments": function.get("arguments"),
                    "error": str(error),
                },
                "executor_id": None,
                "command": {},
                "executor_config": {},
            },
            latency,
            digest,
        )
    return (
        {
            "decision": (
                "execute"
                if outcome.action == "execute"
                else "retry" if outcome.action == "hold" else "abort"
            ),
            "confidence": outcome.confidence,
            "assessment": outcome.reason,
            "actuator_tool": outcome.to_dict(),
            "executor_id": outcome.executor_id,
            "command": dict(outcome.command),
            "executor_config": dict(outcome.executor_config),
        },
        latency,
        digest,
    )


def _post_motion_operation_candidates(
    *, actuator_transition_available: bool = True
) -> tuple[OperationCandidate, ...]:
    """Advertise only operations admitted by current runtime preconditions."""
    candidates = [
        OperationCandidate(
            operation_id="continue.runtime_motion",
            kind="motion",
            description=(
                "Preserve all current actuator commands and continue to the "
                "next runtime-proposed motion operation."
            ),
        ),
    ]
    if actuator_transition_available:
        candidates.append(OperationCandidate(
            operation_id="evaluate.runtime_actuator",
            kind="actuation",
            description=(
                "Request a fresh model-governed actuator command before any "
                "later runtime-proposed motion."
            ),
        ))
    return tuple(candidates)


def _operation_scheduler_prompt(
    *,
    instruction: str,
    observation_id: str,
    state: dict[str, Any],
    operation_context: dict[str, Any],
    candidates: tuple[OperationCandidate, ...],
    rgbd_summary: dict[str, Any] | None,
    critic_context: str,
) -> str:
    """Build a task-neutral next-operation request from runtime candidates."""
    return f"""Select the next operation using the attached fresh observation
and exactly one advertised scheduler tool.

Human instruction:
{instruction}

Fresh observation token: {observation_id}
Observed world state:
{json.dumps(state, indent=2)}
Operation context:
{json.dumps(operation_context, indent=2)}
Runtime-advertised candidates:
{json.dumps([candidate.to_dict() for candidate in candidates], indent=2)}
RGB-D summary:
{json.dumps(rgbd_summary, indent=2)}

A bounded runtime operation has just completed or yielded on measured evidence.
Dispatch evaluate.runtime_actuator when changing or confirming an actuator
command is the next physical operation needed to advance the human instruction.
Dispatch continue.runtime_motion only when current actuator commands should be
preserved while the runtime proposes the next movement. Use observe_again when
the evidence is insufficient, complete_task only when the physical instruction
is already achieved, or abort_task when neither advertised operation is safe.
Do not infer a transition from a prerecorded action or phase schedule. Do not
emit prose or JSON outside the single native tool call.

{critic_context}"""


def _choose_observation_bound_operation(
    provider: GeminiProvider,
    *,
    instruction: str,
    observation_prefix: str,
    frame: np.ndarray,
    state: dict[str, Any],
    operation_context: dict[str, Any],
    candidates: tuple[OperationCandidate, ...],
    rgbd_summary: dict[str, Any] | None,
    critic_context: str,
) -> tuple[dict[str, Any], float, str]:
    """Ask the selected model to route one fresh-observation operation."""
    encoded, digest = _encode_frame(frame)
    observation_id = f"{observation_prefix}:{digest}"
    gate = ObservationBoundOperationGate(
        observation_id=observation_id,
        candidates=candidates,
    )
    messages = [
        {
            "role": "system",
            "content": (
                "You are a visual operation scheduler. Every next-operation "
                "decision must be exactly one runtime-advertised native tool call."
            ),
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/jpeg;base64,{encoded}",
                        "detail": "high",
                    },
                },
                {
                    "type": "text",
                    "text": _operation_scheduler_prompt(
                        instruction=instruction,
                        observation_id=observation_id,
                        state=state,
                        operation_context=operation_context,
                        candidates=candidates,
                        rgbd_summary=rgbd_summary,
                        critic_context=critic_context,
                    ),
                },
            ],
        },
    ]
    started = time.perf_counter()
    response = asyncio.run(
        asyncio.wait_for(
            provider.complete(
                messages,
                {
                    "tools": operation_scheduler_tool_schemas(
                        observation_id, candidates
                    ),
                    "tool_choice": "required",
                },
            ),
            timeout=args_cli.timeout,
        )
    )
    latency = time.perf_counter() - started
    tool_calls = response.tool_calls or []
    if len(tool_calls) != 1:
        error = (
            "model must issue exactly one scheduler tool call; "
            f"received {len(tool_calls)}"
        )
        return (
            {
                "decision": "observe",
                "confidence": 0.0,
                "assessment": f"Scheduler tool rejected by safety gate: {error}",
                "scheduler_tool": {
                    "status": "rejected",
                    "observation_id": observation_id,
                    "tool_name": None,
                    "arguments": None,
                    "error": error,
                    "model_text": str(response.text or "")[:500],
                },
                "operation_id": None,
                "operation_kind": None,
            },
            latency,
            digest,
        )
    try:
        outcome = gate.dispatch(tool_calls[0])
    except MotionToolValidationError as error:
        function = tool_calls[0].get("function", {})
        return (
            {
                "decision": "observe",
                "confidence": 0.0,
                "assessment": f"Scheduler tool rejected by safety gate: {error}",
                "scheduler_tool": {
                    "status": "rejected",
                    "observation_id": observation_id,
                    "tool_name": function.get("name"),
                    "arguments": function.get("arguments"),
                    "error": str(error),
                },
                "operation_id": None,
                "operation_kind": None,
            },
            latency,
            digest,
        )
    return (
        {
            "decision": outcome.action,
            "confidence": outcome.confidence,
            "assessment": outcome.reason,
            "scheduler_tool": outcome.to_dict(),
            "operation_id": outcome.operation_id,
            "operation_kind": outcome.operation_kind,
        },
        latency,
        digest,
    )


def _test_line(index: int, name: str, passed: bool, detail: str) -> None:
    status = "PASS" if passed else "FAIL"
    print(f"[TEST {index}/{TOTAL_TESTS}] {status} | {name} | {detail}", flush=True)


def _write_trace(path: Path, trace: dict[str, Any]) -> None:
    """Atomically publish the latest episode evidence, including partial runs."""
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(trace, indent=2) + "\n")
    temporary.replace(path)


def _next_episode_index(output_dir: Path) -> int:
    indices = []
    for path in output_dir.glob("run_*.hdf5"):
        try:
            indices.append(int(path.stem.split("_")[-1]))
        except ValueError:
            continue
    return max(indices, default=-1) + 1


def _run_joint_segment(
    env: Any,
    obs: dict[str, Any],
    joint_states: np.ndarray,
    recorded_actions: np.ndarray,
    start: int,
    end: int,
) -> tuple[dict[str, Any], bool, torch.Tensor]:
    action = torch.zeros((1, 8), dtype=torch.float32, device=env.device)
    terminal = False
    for step in range(start, end):
        state_index = min(step + 1, len(joint_states) - 1)
        action[0, :7] = torch.as_tensor(
            joint_states[state_index, :7], dtype=torch.float32, device=env.device
        )
        action[0, 7] = float(recorded_actions[min(step, len(recorded_actions) - 1), 7])
        obs, _, terminated, truncated, _ = _step_env(env, action)
        terminal = bool(torch.as_tensor(terminated).any()) or bool(
            torch.as_tensor(truncated).any()
        )
        if terminal:
            break
    return obs, terminal, action


def _hold_joint_action(
    env: Any,
    obs: dict[str, Any],
    action: torch.Tensor,
    steps: int,
    gripper_closed: bool | None = None,
) -> tuple[dict[str, Any], bool]:
    command = action.clone()
    if gripper_closed is not None:
        command[0, 7] = 1.0 if gripper_closed else 0.0
    terminal = False
    for _ in range(steps):
        obs, _, terminated, truncated, _ = _step_env(env, command)
        terminal = bool(torch.as_tensor(terminated).any()) or bool(
            torch.as_tensor(truncated).any()
        )
        if terminal:
            break
    return obs, terminal


def _execute_binary_actuator_tool(
    env: Any,
    obs: dict[str, Any],
    last_action: torch.Tensor,
    decision: dict[str, Any],
    *,
    initial_banana_z: float,
) -> tuple[dict[str, Any], bool, torch.Tensor, dict[str, Any]]:
    """Adapt one admitted runtime actuator call to RoboLab's binary action."""
    if decision.get("executor_id") != "binary_end_effector_clamp":
        raise RuntimeError(
            f"Unsupported actuator executor: {decision.get('executor_id')!r}"
        )
    requested_state = decision.get("command", {}).get("state")
    if requested_state not in {"engage", "disengage", "maintain"}:
        raise RuntimeError(f"Invalid admitted actuator state: {requested_state!r}")
    command = last_action.clone()
    engaged_before = bool(float(command[0, 7].detach().cpu()) > 0.5)
    engaged_after = (
        True
        if requested_state == "engage"
        else False if requested_state == "disengage" else engaged_before
    )
    settle_steps = int(decision.get("executor_config", {}).get("settle_steps", 35))
    state_before = _state(env, initial_banana_z)
    obs, terminal = _hold_joint_action(
        env,
        obs,
        command,
        settle_steps,
        gripper_closed=engaged_after,
    )
    command[0, 7] = 1.0 if engaged_after else 0.0
    state_after = _state(env, initial_banana_z)
    return (
        obs,
        terminal,
        command,
        {
            "executor_id": decision["executor_id"],
            "requested_state": requested_state,
            "engaged_before": engaged_before,
            "engaged_after": engaged_after,
            "settle_steps": settle_steps,
            "state_before": {
                "finger_joint_rad": state_before["finger_joint_rad"],
                "gripper_closed_fraction": state_before[
                    "gripper_closed_fraction"
                ],
                "current_contact": state_before["current_contact"],
            },
            "state_after": {
                "finger_joint_rad": state_after["finger_joint_rad"],
                "gripper_closed_fraction": state_after[
                    "gripper_closed_fraction"
                ],
                "current_contact": state_after["current_contact"],
            },
            "terminal": terminal,
        },
    )


def _actuator_feedback_event_from_execution(
    execution: dict[str, Any],
    policy: ActuatorFeedbackEventPolicy,
) -> dict[str, Any]:
    """Translate adapter telemetry into the task-neutral feedback-event contract."""
    before = execution["state_before"]
    after = execution["state_after"]
    event = assess_actuator_feedback_event(
        position_before=float(before["gripper_closed_fraction"]),
        position_after=float(after["gripper_closed_fraction"]),
        force_before_n=float(before["current_contact"]["net_force_n"]),
        force_after_n=float(after["current_contact"]["net_force_n"]),
        touch_before=bool(before["current_contact"]["touch"]),
        touch_after=bool(after["current_contact"]["touch"]),
        policy=policy,
    )
    return event.to_dict()


def _move_eef_to_target(
    env: Any,
    obs: dict[str, Any],
    last_action: torch.Tensor,
    target: torch.Tensor,
    target_quaternion_wxyz: torch.Tensor,
    phase: str,
    *,
    gripper_closed: bool,
    initial_banana_z: float,
    executor_config: dict[str, Any] | None = None,
    carry_reference_offset: torch.Tensor | None = None,
    rgbd_axis_references: dict[str, np.ndarray] | None = None,
    checkpoint_callback: Callable[
        [dict[str, Any], dict[str, Any]], dict[str, Any]
    ]
    | None = None,
    early_stop_callback: Callable[[], dict[str, Any] | None] | None = None,
) -> tuple[dict[str, Any], bool, torch.Tensor, dict[str, Any]]:
    """Reach a live SE(3) target with bounded local DLS IK."""
    if target.shape != (3,) or not bool(torch.isfinite(target).all()):
        raise ValueError(f"Invalid adaptive target for {phase}: {target}")
    if target_quaternion_wxyz.shape != (4,) or not bool(
        torch.isfinite(target_quaternion_wxyz).all()
    ):
        raise ValueError(
            f"Invalid adaptive orientation target for {phase}: {target_quaternion_wxyz}"
        )
    effective_config = {
        "position_tolerance_m": args_cli.adaptive_tolerance,
        "translation_step_limit_m": args_cli.adaptive_max_step,
        "maximum_iterations": args_cli.adaptive_max_iterations,
        "settle_steps": args_cli.adaptive_settle_steps,
        "joint_step_limit_rad": args_cli.adaptive_max_joint_step,
        "damping": args_cli.adaptive_damping,
        "orientation_tolerance_deg": args_cli.adaptive_orientation_tolerance_deg,
        "rotation_step_limit_deg": args_cli.adaptive_max_angle_step_deg,
        "minimum_progress_m": 0.00025,
        "maximum_stalled_observations": 3,
        "require_contact": False,
        "minimum_contact_force_n": 0.0,
        "maximum_tracked_pose_error_m": None,
        "maximum_tracked_orientation_error_deg": None,
        "minimum_observed_clearance_m": None,
    }
    effective_config.update(executor_config or {})
    lease_conditions = _motion_lease_conditions_from_config(effective_config)
    sensor_predicate_registry = _motion_sensor_predicate_registry()
    sensor_predicate_leases = _motion_sensor_predicate_leases(effective_config)
    robot = env.scene["robot"]
    arm_joint_ids = [robot.data.joint_names.index(f"panda_joint{i}") for i in range(1, 8)]
    body_idx = robot.data.body_names.index("base_link")
    jacobi_body_idx = body_idx - 1 if robot.is_fixed_base else body_idx
    jacobi_joint_ids = [index + robot.num_base_dofs for index in arm_joint_ids]
    command = last_action.clone()
    command[0, 7] = 1.0 if gripper_closed else 0.0
    target_cpu = target.detach().cpu().to(dtype=torch.float32)
    target_quat_cpu = target_quaternion_wxyz.detach().cpu().to(dtype=torch.float32)
    orientation_tolerance = np.deg2rad(
        effective_config["orientation_tolerance_deg"]
    )
    maximum_angle_step = np.deg2rad(effective_config["rotation_step_limit_deg"])
    terminal = False
    iterations: list[dict[str, Any]] = []
    eef_start = _eef_position(env)
    error_start = float(torch.linalg.vector_norm(target_cpu - eef_start))
    orientation_error_start = float(
        torch.linalg.vector_norm(
            quaternion_error_axis_angle_wxyz(target_quat_cpu, _eef_quaternion(env))
        )
    )
    previous_error = error_start
    previous_orientation_error = orientation_error_start
    recovery_request: dict[str, Any] | None = None
    early_stop: dict[str, Any] | None = None
    stalled_observations = 0

    for iteration in range(int(effective_config["maximum_iterations"])):
        eef_before = _eef_position(env)
        error = target_cpu - eef_before
        error_norm = float(torch.linalg.vector_norm(error))
        orientation_error = quaternion_error_axis_angle_wxyz(
            target_quat_cpu, _eef_quaternion(env)
        )
        orientation_error_norm = float(torch.linalg.vector_norm(orientation_error))
        if (
            error_norm <= effective_config["position_tolerance_m"]
            and orientation_error_norm <= orientation_tolerance
        ):
            break
        xyz_step = bounded_vector_step(
            error.to(device=env.device), effective_config["translation_step_limit_m"]
        )
        desired_twist_w = torch.zeros(6, dtype=torch.float32, device=env.device)
        desired_twist_w[:3] = xyz_step
        desired_twist_w[3:] = bounded_vector_step(
            orientation_error.to(device=env.device), maximum_angle_step
        )
        jacobian_w = robot.data.body_link_jacobian_w.torch[
            0, jacobi_body_idx
        ][:, jacobi_joint_ids]
        delta_joint = damped_least_squares_delta(
            jacobian_w,
            desired_twist_w,
            effective_config["damping"],
            effective_config["joint_step_limit_rad"],
        )
        joint_pos = robot.data.joint_pos.torch[0, arm_joint_ids]
        joint_limits = robot.data.soft_joint_pos_limits.torch[0, arm_joint_ids]
        command[0, :7] = torch.clamp(
            joint_pos + delta_joint,
            min=joint_limits[:, 0] + 1.0e-3,
            max=joint_limits[:, 1] - 1.0e-3,
        )
        obs, terminal = _hold_joint_action(
            env,
            obs,
            command,
            int(effective_config["settle_steps"]),
            gripper_closed=gripper_closed,
        )
        eef_after = _eef_position(env)
        error_after = float(torch.linalg.vector_norm(target_cpu - eef_after))
        orientation_error_after = float(
            torch.linalg.vector_norm(
                quaternion_error_axis_angle_wxyz(
                    target_quat_cpu, _eef_quaternion(env)
                )
            )
        )
        record = {
            "iteration": iteration + 1,
            "eef_before_xyz": eef_before.tolist(),
            "requested_xyz_step_m": xyz_step.detach().cpu().tolist(),
            "max_abs_joint_step_rad": float(torch.max(torch.abs(delta_joint))),
            "eef_after_xyz": eef_after.tolist(),
            "target_error_after_m": error_after,
            "orientation_error_before_deg": float(np.rad2deg(orientation_error_norm)),
            "orientation_error_after_deg": float(np.rad2deg(orientation_error_after)),
            "terminal": terminal,
        }
        checkpoint_reason: str | None = None
        tracked_pose_error_m = None
        if carry_reference_offset is not None:
            tracked_object = _local_position(env, "banana")
            tracked_pose_error_m = float(
                torch.linalg.vector_norm(
                    (eef_after - tracked_object) - carry_reference_offset
                )
            )
        observed_clearance_m = None
        if lease_conditions.minimum_observed_clearance_m is not None:
            observed_clearance_m = float(
                _local_position(env, "banana")[2]
                - _local_position(env, "plate_large")[2]
            )
        current_contact = _current_contact_observation(env)
        observed_at_s = time.monotonic()
        sensor_observations: list[SensorObservation] = []
        progress_m = previous_error - error_after
        if progress_m < float(effective_config["minimum_progress_m"]):
            stalled_observations += 1
        else:
            stalled_observations = 0
        sensor_observations.append(
            SensorObservation(
                channel_id="motion.stalled_observation_count",
                source_id="sim6.robot_kinematic_state_adapter",
                sequence=iteration + 1,
                timestamp_s=observed_at_s,
                value=stalled_observations,
            )
        )
        record["measured_target_progress_m"] = progress_m
        record["stalled_observation_count"] = stalled_observations
        if bool(current_contact.get("available")):
            sensor_observations.extend(
                (
                    SensorObservation(
                        channel_id="gripper.touch",
                        source_id="sim6.gripper_contact_sensor",
                        sequence=iteration + 1,
                        timestamp_s=observed_at_s,
                        value=bool(current_contact["touch"]),
                    ),
                    SensorObservation(
                        channel_id="gripper.contact_force_n",
                        source_id="sim6.gripper_contact_sensor",
                        sequence=iteration + 1,
                        timestamp_s=observed_at_s,
                        value=float(current_contact["net_force_n"]),
                    ),
                )
            )
        if tracked_pose_error_m is not None:
            sensor_observations.append(
                SensorObservation(
                    channel_id="object.tracked_translation_error_m",
                    source_id="sim6.privileged_relative_pose_adapter",
                    sequence=iteration + 1,
                    timestamp_s=observed_at_s,
                    value=tracked_pose_error_m,
                )
            )
        rgbd_axis_observation = None
        rgbd_axis_error = None
        rgbd_axis_error_message = None
        if (
            effective_config.get("maximum_tracked_orientation_error_deg")
            is not None
        ):
            try:
                tracked_object_id = effective_config.get("tracked_object_id")
                if not isinstance(tracked_object_id, str) or not tracked_object_id:
                    raise ValueError(
                        "tracked_object_id is unavailable for the RGB-D lease"
                    )
                reference_axis = (rgbd_axis_references or {}).get(
                    tracked_object_id
                )
                if reference_axis is None:
                    raise ValueError(
                        "RGB-D tracked-orientation reference is unavailable for "
                        f"{tracked_object_id!r}"
                    )
                rgbd_axis_observation = _rgbd_object_axis_observation(
                    env,
                    prim_label_fragment=f"/scene/{tracked_object_id}",
                    reference_axis=reference_axis,
                )
                rgbd_axis_error = float(
                    rgbd_axis_observation["orientation_error_deg"]
                )
                sensor_observations.append(
                    SensorObservation(
                        channel_id="rgbd.object_orientation_error_deg",
                        source_id="rgbd.instance_depth_major_axis",
                        sequence=iteration + 1,
                        timestamp_s=observed_at_s,
                        value=rgbd_axis_error,
                        frame_id="over_shoulder_left_camera",
                    )
                )
            except ValueError as exc:
                rgbd_axis_error_message = str(exc)
        if observed_clearance_m is not None:
            sensor_observations.append(
                SensorObservation(
                    channel_id="scene.observed_clearance_m",
                    source_id="sim6.privileged_object_to_support_height_adapter",
                    sequence=iteration + 1,
                    timestamp_s=observed_at_s,
                    value=observed_clearance_m,
                )
            )
        ros2_overlay_channels: list[str] = []
        if (
            ACTIVE_ROS2_SENSOR_INGRESS is not None
            and ACTIVE_ROS2_SENSOR_INGRESS.available
        ):
            ros2_snapshot = ACTIVE_ROS2_SENSOR_INGRESS.buffer.snapshot()
            ros2_overlay_channels = list(ros2_snapshot.channels())
            sensor_observations = list(
                overlay_sensor_observations(sensor_observations, ros2_snapshot)
            )
        observation_snapshot = SensorObservationSnapshot(sensor_observations)
        iteration_predicate_leases = sensor_predicate_leases
        if observation_snapshot.get("scene.collision_stop") is not None:
            iteration_predicate_leases = (
                *iteration_predicate_leases,
                SensorPredicateLease("scene.no_collision_stop", {}),
            )
        lease_assessment = sensor_predicate_registry.assess(
            iteration_predicate_leases,
            observation_snapshot,
            evaluated_at_s=observed_at_s,
        )
        channel_sources = {
            item.channel_id: item.source_id for item in sensor_observations
        }
        record["motion_lease"] = {
            **lease_assessment.to_dict(),
            "active_predicates": [
                item.to_dict() for item in iteration_predicate_leases
            ],
            "observation_channels": [
                item.to_dict() for item in sensor_observations
            ],
            "ros2_overlay_channels": ros2_overlay_channels,
            "contact_source": (
                channel_sources.get("gripper.touch")
                if channel_sources.get("gripper.touch")
                == channel_sources.get("gripper.contact_force_n")
                else "+".join(
                    sorted(
                        {
                            source
                            for source in (
                                channel_sources.get("gripper.touch"),
                                channel_sources.get("gripper.contact_force_n"),
                            )
                            if source is not None
                        }
                    )
                )
            ),
            "tracked_pose_source": channel_sources.get(
                "object.tracked_translation_error_m"
            ),
            "observed_clearance_source": channel_sources.get(
                "scene.observed_clearance_m"
            ),
            "tracked_orientation_source": channel_sources.get(
                "rgbd.object_orientation_error_deg"
            ),
            "collision_stop_source": channel_sources.get("scene.collision_stop"),
            "tracked_orientation_object_id": effective_config.get(
                "tracked_object_id"
            ),
            "rgbd_tracked_axis": rgbd_axis_observation,
            "rgbd_tracking_error": rgbd_axis_error_message,
        }
        if not lease_assessment.valid:
            checkpoint_reason = "lease_invalidated:" + ",".join(
                lease_assessment.invalidation_reasons
            )
        if early_stop_callback is not None:
            early_stop = early_stop_callback()
            if early_stop is not None:
                record["early_stop"] = early_stop
        # Preserve the last executed transition even if a local stop or coach
        # pause raises below. This is audit evidence, never success admission.
        iterations.append(record)
        periodic_checkpoint = (
            args_cli.periodic_motion_observations
            and (iteration + 1) % args_cli.coach_interval_iterations == 0
        )
        target_changed = False
        if early_stop is None and checkpoint_callback is not None and (
            periodic_checkpoint or checkpoint_reason is not None
        ):
            checkpoint = {
                "reason": checkpoint_reason or "periodic",
                "phase": phase,
                "iteration": iteration + 1,
                "target_error_m": error_after,
                "orientation_error_deg": float(np.rad2deg(orientation_error_after)),
                "motion_lease": record.get("motion_lease"),
                "lease_condition_sources": {
                    "contact": record["motion_lease"].get("contact_source"),
                    "tracked_pose": record["motion_lease"].get(
                        "tracked_pose_source"
                    ),
                    "tracked_orientation": record["motion_lease"].get(
                        "tracked_orientation_source"
                    ),
                    "observed_clearance": record["motion_lease"].get(
                        "observed_clearance_source"
                    ),
                    "collision_stop": record["motion_lease"].get(
                        "collision_stop_source"
                    ),
                    "model_polling": (
                        "periodic_or_event"
                        if args_cli.periodic_motion_observations
                        else "event_or_completion_only"
                    ),
                },
                "current_target_xyz_m": target_cpu.tolist(),
                "executor_id": "bounded_dls_ik",
                "executor_config": dict(effective_config),
            }
            tracked_orientation_object_id = record["motion_lease"].get(
                "tracked_orientation_object_id"
            )
            tracked_orientation_source = checkpoint[
                "lease_condition_sources"
            ].get("tracked_orientation")
            checkpoint["lease_condition_sources"]["tracked_orientation"] = (
                {
                    tracked_orientation_object_id: tracked_orientation_source
                }
                if tracked_orientation_object_id
                and tracked_orientation_source is not None
                else {}
            )
            checkpoint_decision = checkpoint_callback(obs, checkpoint)
            record["coach_checkpoint"] = checkpoint_decision
            if checkpoint_decision.get("decision") == "execute":
                updated_target = torch.tensor(
                    checkpoint_decision.get("target_xyz_m", target_cpu.tolist()),
                    dtype=torch.float32,
                )
                if updated_target.shape != (3,) or not bool(
                    torch.isfinite(updated_target).all()
                ):
                    raise RuntimeError(
                        f"Model returned an invalid world target: {updated_target}"
                    )
                target_changed = not bool(torch.allclose(updated_target, target_cpu))
                if target_changed:
                    record["target_before_model_correction_m"] = target_cpu.tolist()
                    target_cpu = updated_target
                    record["target_after_model_correction_m"] = target_cpu.tolist()
                    error_after = float(torch.linalg.vector_norm(target_cpu - eef_after))
                    record["target_error_after_model_correction_m"] = error_after
                updated_config = checkpoint_decision.get("executor_config") or {}
                if updated_config:
                    effective_config.update(updated_config)
                    lease_conditions = _motion_lease_conditions_from_config(
                        effective_config
                    )
                    sensor_predicate_leases = _motion_sensor_predicate_leases(
                        effective_config
                    )
                    orientation_tolerance = np.deg2rad(
                        effective_config["orientation_tolerance_deg"]
                    )
                    maximum_angle_step = np.deg2rad(
                        effective_config["rotation_step_limit_deg"]
                    )
                    record["executor_config_after_model_call"] = dict(
                        effective_config
                    )
            if checkpoint_reason is not None:
                if checkpoint_decision.get("decision") == "abort":
                    raise RuntimeError(
                        f"Local safety stopped {phase} and Gemini aborted at "
                        f"iteration {iteration + 1}: {checkpoint_reason}; "
                        f"coach={checkpoint_decision}"
                    )
                if checkpoint_decision.get("decision") == "execute":
                    record["lease_reauthorized_by_model"] = True
                    stalled_observations = 0
                else:
                    recovery_request = {
                        **checkpoint,
                        "reason": "model_requested_hold",
                        "lease_invalidation_reason": checkpoint_reason,
                        "coach_decision": checkpoint_decision,
                    }
            elif checkpoint_decision.get("decision") == "retry":
                recovery_request = {
                    **checkpoint,
                    "reason": "model_requested_hold",
                    "coach_decision": checkpoint_decision,
                }
            elif checkpoint_decision.get("decision") != "execute":
                raise RuntimeError(
                    f"Gemini paused {phase} at mid-motion checkpoint: "
                    f"{checkpoint_decision}"
                )
        print(
            f"[adaptive-ik] phase={phase} iteration={iteration + 1} "
            f"error={error_norm:.4f}→{error_after:.4f}m "
            f"angle={np.rad2deg(orientation_error_norm):.1f}→"
            f"{np.rad2deg(orientation_error_after):.1f}deg "
            f"step={record['requested_xyz_step_m']} "
            f"max_dq={record['max_abs_joint_step_rad']:.4f}rad",
            flush=True,
        )
        if recovery_request is not None:
            print(
                f"[adaptive-ik] paused {phase} for bounded recovery: "
                f"{recovery_request['reason']}",
                flush=True,
            )
            break
        if early_stop is not None:
            print(
                f"[adaptive-ik] stopped {phase} on measured condition: "
                f"{early_stop}",
                flush=True,
            )
            break
        if terminal:
            break
        if not target_changed and error_after > previous_error + 0.008:
            raise RuntimeError(
                f"Adaptive IK diverged in {phase}: "
                f"target error {previous_error:.4f}→{error_after:.4f} m"
            )
        if orientation_error_after > previous_orientation_error + np.deg2rad(8.0):
            raise RuntimeError(
                f"Adaptive rotational IK diverged in {phase}: orientation error "
                f"{np.rad2deg(previous_orientation_error):.1f}→"
                f"{np.rad2deg(orientation_error_after):.1f} degrees"
            )
        previous_error = error_after
        previous_orientation_error = orientation_error_after

    eef_final = _eef_position(env)
    error_final = float(torch.linalg.vector_norm(target_cpu - eef_final))
    orientation_error_final = float(
        torch.linalg.vector_norm(
            quaternion_error_axis_angle_wxyz(target_quat_cpu, _eef_quaternion(env))
        )
    )
    report = {
        "enabled": True,
        "phase": phase,
        "target_source": "live_object_pose",
        "target_xyz": target_cpu.tolist(),
        "target_quaternion_wxyz": target_quat_cpu.tolist(),
        "eef_start_xyz": eef_start.tolist(),
        "eef_final_xyz": eef_final.tolist(),
        "target_error_before_m": error_start,
        "target_error_after_m": error_final,
        "orientation_error_before_deg": float(np.rad2deg(orientation_error_start)),
        "orientation_error_after_deg": float(np.rad2deg(orientation_error_final)),
        "executor_id": "bounded_dls_ik",
        "executor_config": effective_config,
        "motion_lease": {
            "conditions": lease_conditions.to_dict(),
            "active_predicates": [
                item.to_dict() for item in sensor_predicate_leases
            ],
            "maximum_iterations": int(effective_config["maximum_iterations"]),
            "model_observation_mode": (
                "periodic_or_event"
                if args_cli.periodic_motion_observations
                else "event_or_completion_only"
            ),
            "tracking_source": (
                "sim_privileged_relative_pose"
                if carry_reference_offset is not None
                else None
            ),
            "tracked_orientation_source": (
                "rgbd_instance_depth_major_axis"
                if effective_config.get("tracked_object_id")
                in (rgbd_axis_references or {})
                else None
            ),
            "tracked_orientation_object_id": effective_config.get(
                "tracked_object_id"
            ),
            "clearance_source": (
                "sim_privileged_object_to_support_height"
                if lease_conditions.minimum_observed_clearance_m is not None
                else None
            ),
        },
        "orientation_tolerance_deg": effective_config[
            "orientation_tolerance_deg"
        ],
        "target_tolerance_m": effective_config["position_tolerance_m"],
        "recovery_requested": recovery_request is not None,
        "recovery_request": recovery_request,
        "early_stop": early_stop,
        "converged": bool(
            not terminal
            and (
            (
                error_final <= effective_config["position_tolerance_m"]
                and orientation_error_final <= orientation_tolerance
                and recovery_request is None
            )
            or bool(early_stop is not None and early_stop.get("converged"))
            )
        ),
        "iterations": iterations,
    }
    return obs, terminal, command, report


def _recover_transport_grasp(
    env: Any,
    obs: dict[str, Any],
    last_action: torch.Tensor,
    *,
    initial_banana_z: float,
    grasp_offset_object: torch.Tensor,
    object_to_grasp_quat: torch.Tensor,
    checkpoint_callback: Callable[
        [dict[str, Any], dict[str, Any]], dict[str, Any]
    ],
) -> tuple[
    dict[str, Any],
    bool,
    torch.Tensor,
    torch.Tensor,
    torch.Tensor,
    dict[str, Any],
]:
    """Stabilize a shifted grasp or safely set down and reacquire the object."""
    command = last_action.clone()
    command[0, 7] = 1.0
    terminal = False
    segments: list[dict[str, Any]] = []
    eef_before = _eef_position(env)
    banana_before = _local_position(env, "banana")
    offset_before = eef_before - banana_before

    obs, terminal = _hold_joint_action(
        env,
        obs,
        command,
        args_cli.recovery_hold_steps,
        gripper_closed=True,
    )
    if terminal:
        raise RuntimeError("Environment terminated during recovery stability hold")
    eef_after_hold = _eef_position(env)
    banana_after_hold = _local_position(env, "banana")
    offset_after = eef_after_hold - banana_after_hold
    hold_assessment = assess_recovery_hold(
        offset_before=offset_before.numpy(),
        offset_after=offset_after.numpy(),
        object_z_after=float(banana_after_hold[2]),
        object_initial_z=initial_banana_z,
        maximum_hold_drift_m=args_cli.recovery_stability_drift,
        minimum_carried_lift_m=args_cli.minimum_transport_lift,
    )
    try:
        contact_frame = sensor_frame_from_isaac_env(env)
        contact_valid = bool(
            contact_frame.validity[5] > 0.5 and contact_frame.validity[6] > 0.5
        )
        contact_touch = bool(
            contact_frame.values[SIGNAL_SLICES["gripper_touch"]][0] >= 0.5
        )
        contact_force = contact_frame.values[
            SIGNAL_SLICES["gripper_contact_force"]
        ]
        contact_force_n = float(np.linalg.vector_norm(contact_force))
    except Exception:
        contact_valid = False
        contact_touch = False
        contact_force_n = 0.0
    contact_confirms_grasp = (
        args_cli.disable_contact_telemetry or (contact_valid and contact_touch)
    )
    report: dict[str, Any] = {
        "strategy": hold_assessment.strategy,
        "hold_steps": args_cli.recovery_hold_steps,
        "offset_before_hold_m": offset_before.tolist(),
        "offset_after_hold_m": offset_after.tolist(),
        "hold_assessment": hold_assessment.to_dict(),
        "contact_confirmation": {
            "required": not args_cli.disable_contact_telemetry,
            "valid": contact_valid,
            "touch": contact_touch,
            "net_force_n": contact_force_n,
            "confirms_grasp": contact_confirms_grasp,
        },
        "segments": segments,
        "completed": False,
    }

    if hold_assessment.safe_to_resume and contact_confirms_grasp:
        decision = checkpoint_callback(
            obs,
            {
                "reason": "recovery_hold_stable",
                "phase": "above_plate_recovery",
                "iteration": 0,
                "target_error_m": 0.0,
                "orientation_error_deg": 0.0,
                "local_safety": {
                    "safe": True,
                    "reasons": [],
                    "grasp_translation_drift_m": hold_assessment.hold_grasp_drift_m,
                    "object_lift_m": hold_assessment.object_lift_m,
                    "rgbd_clearance_m": None,
                },
            },
        )
        report["stability_coach_decision"] = decision
        if decision.get("decision") == "execute":
            report["completed"] = True
            report["outcome"] = "stable_grasp_relatched"
            return (
                obs,
                terminal,
                command,
                offset_after,
                _eef_quaternion(env),
                report,
            )
        if decision.get("decision") == "abort":
            raise RuntimeError(
                f"Gemini aborted after recovery stability hold: {decision}"
            )
        report["strategy"] = "set_down_and_regrasp"
        report["stability_override"] = "coach_requested_physical_regrasp"
    elif hold_assessment.safe_to_resume:
        report["strategy"] = "set_down_and_regrasp"
        report["stability_override"] = "contact_sensor_did_not_confirm_grasp"

    def move_segment(
        name: str,
        target: torch.Tensor,
        quaternion: torch.Tensor,
        *,
        closed: bool,
        early_stop_callback: Callable[[], dict[str, Any] | None] | None = None,
    ) -> None:
        nonlocal obs, terminal, command
        obs, terminal, command, segment_report = _move_eef_to_target(
            env,
            obs,
            command,
            target,
            quaternion,
            name,
            gripper_closed=closed,
            initial_banana_z=initial_banana_z,
            checkpoint_callback=None,
            early_stop_callback=early_stop_callback,
        )
        segments.append(segment_report)
        if terminal or not bool(segment_report["converged"]):
            raise RuntimeError(
                f"Recovery segment {name} failed to converge: "
                f"error={segment_report['target_error_after_m']:.4f} m"
            )

    # If the object is still carried, put it down vertically at its current XY
    # before opening. If it has already dropped, opening immediately avoids a
    # blind descent toward an uncertain object pose.
    if hold_assessment.object_lift_m >= args_cli.minimum_transport_lift:
        carry_offset = _eef_position(env) - _local_position(env, "banana")
        set_down = _eef_position(env)
        set_down[2] = (
            initial_banana_z
            + max(float(carry_offset[2]), GRIPPER_BASE_TO_FINGERTIP_M)
            + args_cli.recovery_set_down_clearance
        )
        set_down[2] = min(float(_eef_position(env)[2]), float(set_down[2]))

        support_monitor = SupportContactMonitor(
            object_initial_z=initial_banana_z,
            set_down_clearance_m=args_cli.recovery_set_down_clearance,
        )

        def object_support_contact() -> dict[str, Any] | None:
            banana = _local_position(env, "banana")
            eef = _eef_position(env)
            return support_monitor.update(
                object_z=float(banana[2]),
                eef_z=float(eef[2]),
                target_eef_z=float(set_down[2]),
                target_tolerance_m=args_cli.adaptive_tolerance,
            )

        move_segment(
            "recovery_set_down",
            set_down,
            _eef_quaternion(env),
            closed=True,
            early_stop_callback=object_support_contact,
        )

    obs, terminal = _hold_joint_action(
        env, obs, command, 35, gripper_closed=False
    )
    command[0, 7] = 0.0
    if terminal:
        raise RuntimeError("Environment terminated while opening for recovery")
    release_state = _state(env, initial_banana_z)
    report["state_after_recovery_release"] = release_state

    retreat_target = _eef_position(env) + torch.tensor(
        [0.0, 0.0, args_cli.approach_clearance], dtype=torch.float32
    )
    move_segment(
        "recovery_retreat",
        retreat_target,
        _eef_quaternion(env),
        closed=False,
    )
    obs, terminal = _hold_joint_action(
        env, obs, command, args_cli.recovery_hold_steps, gripper_closed=False
    )
    if terminal:
        raise RuntimeError("Environment terminated while object settled for recovery")

    # Release-to-support motion is expected and may be large. Measure actual
    # stability across a second settled hold instead of comparing against the
    # pre-retreat release pose.
    settle_reference = _local_position(env, "banana")
    settle_verification_steps = max(8, args_cli.recovery_hold_steps // 2)
    obs, terminal = _hold_joint_action(
        env,
        obs,
        command,
        settle_verification_steps,
        gripper_closed=False,
    )
    if terminal:
        raise RuntimeError("Environment terminated during recovery settle verification")

    banana = _local_position(env, "banana")
    plate = _local_position(env, "plate_large")
    settled_displacement = float(
        torch.linalg.vector_norm(banana - settle_reference)
    )
    placement_event = placement_completion_event(
        object_xyz=banana.numpy(),
        target_xyz=plate.numpy(),
        maximum_contact_height_m=max(args_cli.plate_contact_height, 0.100),
        settled_displacement_m=settled_displacement,
    )
    banana_quaternion = _local_quaternion(env, "banana")
    support_aligned_quaternion = torch.as_tensor(
        support_aligned_object_quaternion_wxyz(banana_quaternion.numpy()),
        dtype=torch.float32,
    )
    report["settled_object_pose"] = {
        "xyz": banana.tolist(),
        "quaternion_wxyz": banana_quaternion.tolist(),
        "support_aligned_quaternion_wxyz": support_aligned_quaternion.tolist(),
        "displacement_during_stability_hold_m": settled_displacement,
        "stability_verification_steps": settle_verification_steps,
    }
    report["placement_after_set_down"] = placement_event
    reacquire_decision = checkpoint_callback(
        obs,
        {
            "reason": "recovery_object_settled_reacquire",
            "phase": "recovery_reacquire",
            "iteration": len(segments),
            "target_error_m": 0.0,
            "orientation_error_deg": 0.0,
            "local_safety": {
                "safe": True,
                "reasons": [],
                "grasp_translation_drift_m": None,
                "object_lift_m": float(banana[2]) - initial_banana_z,
                "rgbd_clearance_m": None,
            },
        },
    )
    report["reacquire_coach_decision"] = reacquire_decision
    if placement_event is not None:
        report["completed"] = True
        report["goal_completed"] = True
        report["outcome"] = "goal_completed_during_recovery_set_down"
        return (
            obs,
            terminal,
            command,
            _eef_position(env) - banana,
            _eef_quaternion(env),
            report,
        )
    if reacquire_decision.get("decision") != "execute":
        raise RuntimeError(
            f"Gemini refused recovery reacquisition: {reacquire_decision}"
        )
    grasp_xyz, grasp_quaternion = apply_object_relative_grasp(
        banana,
        support_aligned_quaternion,
        grasp_offset_object,
        object_to_grasp_quat,
    )
    approach = grasp_xyz + torch.tensor(
        [0.0, 0.0, args_cli.approach_clearance], dtype=torch.float32
    )
    move_segment(
        "recovery_approach",
        approach,
        grasp_quaternion,
        closed=False,
    )
    # Re-read the object after approach in case it rolled while settling.
    banana = _local_position(env, "banana")
    support_aligned_quaternion = torch.as_tensor(
        support_aligned_object_quaternion_wxyz(
            _local_quaternion(env, "banana").numpy()
        ),
        dtype=torch.float32,
    )
    grasp_xyz, grasp_quaternion = apply_object_relative_grasp(
        banana,
        support_aligned_quaternion,
        grasp_offset_object,
        object_to_grasp_quat,
    )
    pregrasp_decision = checkpoint_callback(
        obs,
        {
            "reason": "recovery_pregrasp_visual_gate",
            "phase": "recovery_pregrasp",
            "iteration": len(segments),
            "target_error_m": float(
                torch.linalg.vector_norm(grasp_xyz - _eef_position(env))
            ),
            "orientation_error_deg": float(
                torch.linalg.vector_norm(
                    quaternion_error_axis_angle_wxyz(
                        grasp_quaternion, _eef_quaternion(env)
                    )
                )
                * 180.0
                / np.pi
            ),
            "local_safety": {
                "safe": True,
                "reasons": [],
                "grasp_translation_drift_m": None,
                "object_lift_m": float(banana[2]) - initial_banana_z,
                "rgbd_clearance_m": None,
            },
        },
    )
    report["pregrasp_coach_decision"] = pregrasp_decision
    if pregrasp_decision.get("decision") != "execute":
        raise RuntimeError(f"Gemini refused recovery pregrasp: {pregrasp_decision}")
    move_segment(
        "recovery_descend",
        grasp_xyz,
        grasp_quaternion,
        closed=False,
    )
    obs, terminal = _hold_joint_action(
        env, obs, command, 35, gripper_closed=True
    )
    command[0, 7] = 1.0
    if terminal:
        raise RuntimeError("Environment terminated while closing recovery grasp")

    # Lift from the fresh measured pose so object motion during finger closure
    # does not become an open-loop offset.
    recovery_lift_target = _eef_position(env) + torch.tensor(
        [0.0, 0.0, args_cli.lift_clearance], dtype=torch.float32
    )
    move_segment(
        "recovery_lift",
        recovery_lift_target,
        _eef_quaternion(env),
        closed=True,
    )
    banana_regrasped = _local_position(env, "banana")
    lift = float(banana_regrasped[2]) - initial_banana_z
    if lift < max(0.05, args_cli.minimum_transport_lift):
        raise RuntimeError(
            f"Recovery regrasp failed physical lift verification: {lift:.4f} m"
        )
    new_offset = _eef_position(env) - banana_regrasped
    new_quaternion = _eef_quaternion(env)
    decision = checkpoint_callback(
        obs,
        {
            "reason": "recovery_regrasp_verified",
            "phase": "above_plate_recovery",
            "iteration": len(segments),
            "target_error_m": 0.0,
            "orientation_error_deg": 0.0,
            "local_safety": {
                "safe": True,
                "reasons": [],
                "grasp_translation_drift_m": 0.0,
                "object_lift_m": lift,
                "rgbd_clearance_m": None,
            },
        },
    )
    report["regrasp_coach_decision"] = decision
    if decision.get("decision") != "execute":
        raise RuntimeError(
            f"Gemini did not approve transport after verified regrasp: {decision}"
        )
    report["completed"] = True
    report["outcome"] = "set_down_reacquired_regrasped_relatched"
    report["new_carry_offset_m"] = new_offset.tolist()
    report["object_lift_after_regrasp_m"] = lift
    return obs, terminal, command, new_offset, new_quaternion, report


def _residual_center_over_plate(
    env: Any,
    obs: dict[str, Any],
    last_action: torch.Tensor,
    initial_banana_z: float,
) -> tuple[dict[str, Any], bool, torch.Tensor, dict[str, Any]]:
    """Move the grasped banana toward plate center with bounded local DLS IK."""
    robot = env.scene["robot"]
    arm_joint_ids = [robot.data.joint_names.index(f"panda_joint{i}") for i in range(1, 8)]
    body_idx = robot.data.body_names.index("base_link")
    jacobi_body_idx = body_idx - 1 if robot.is_fixed_base else body_idx
    jacobi_joint_ids = [index + robot.num_base_dofs for index in arm_joint_ids]
    command = last_action.clone()
    terminal = False
    iterations: list[dict[str, Any]] = []
    contact_detected = False
    support_contact_event: dict[str, Any] | None = None
    banana_start = _local_position(env, "banana")
    plate_start = _local_position(env, "plate_large")
    error_start = float(torch.linalg.vector_norm(plate_start[:2] - banana_start[:2]))
    height_start = float(banana_start[2] - plate_start[2])
    previous_error = error_start
    previous_height_error = abs(args_cli.release_height - height_start)
    support_monitor = SupportContactMonitor(
        object_initial_z=initial_banana_z,
        set_down_clearance_m=args_cli.recovery_set_down_clearance,
        require_eef_stall=False,
    )

    for iteration in range(args_cli.center_max_iterations):
        banana = _local_position(env, "banana")
        plate = _local_position(env, "plate_large")
        error_xy = (plate[:2] - banana[:2]).to(env.device)
        error_norm = float(torch.linalg.vector_norm(error_xy))
        height_above_plate = float(banana[2] - plate[2])
        height_error = args_cli.release_height - height_above_plate
        eef_z_before = float(_eef_position(env)[2])
        if (
            error_norm <= args_cli.center_tolerance
            and abs(height_error) <= args_cli.release_height_tolerance
        ):
            break
        if (
            error_norm <= 0.12
            and 0.0 <= height_above_plate <= args_cli.plate_contact_height
        ):
            contact_detected = True
            print(
                f"[center] plate contact proxy active at xy={error_norm:.4f}m "
                f"height={height_above_plate:.4f}m; stopping before release",
                flush=True,
            )
            break
        if iteration == 0 and float(banana[2]) - initial_banana_z < 0.05:
            raise RuntimeError("Residual centering refused: banana is no longer securely lifted")
        if height_above_plate < 0.015:
            raise RuntimeError("Residual centering refused: banana is already at plate contact height")

        # Center laterally at the safe hover height before lowering. Combining
        # a near-contact XY sweep with a downward command can lever the banana
        # out of the gripper or collide it with the plate rim.
        if error_norm > args_cli.center_tolerance:
            correction_mode = "xy"
            xy_step = bounded_xy_step(error_xy, args_cli.center_max_step)
            z_step = torch.tensor(0.0, dtype=torch.float32, device=env.device)
        else:
            correction_mode = "z"
            xy_step = torch.zeros(2, dtype=torch.float32, device=env.device)
            z_step = bounded_scalar_step(
                torch.tensor(height_error, dtype=torch.float32, device=env.device),
                args_cli.center_max_z_step,
            )
        desired_twist_w = torch.zeros(6, dtype=torch.float32, device=env.device)
        # The task/robot roots share world orientation in this fixed-base scene,
        # so the measured plate-minus-banana vector is already on Jacobian axes.
        desired_twist_w[:2] = xy_step
        desired_twist_w[2] = z_step
        jacobian_w = robot.data.body_link_jacobian_w.torch[
            0, jacobi_body_idx
        ][:, jacobi_joint_ids]
        delta_joint = damped_least_squares_delta(
            jacobian_w,
            desired_twist_w,
            args_cli.center_damping,
            args_cli.center_max_joint_step,
        )
        joint_pos = robot.data.joint_pos.torch[0, arm_joint_ids]
        joint_limits = robot.data.soft_joint_pos_limits.torch[0, arm_joint_ids]
        joint_target = torch.clamp(
            joint_pos + delta_joint,
            min=joint_limits[:, 0] + 1.0e-3,
            max=joint_limits[:, 1] - 1.0e-3,
        )
        command[0, :7] = joint_target
        command[0, 7] = 1.0
        obs, terminal = _hold_joint_action(
            env,
            obs,
            command,
            args_cli.center_settle_steps,
            gripper_closed=True,
        )
        banana_after = _local_position(env, "banana")
        plate_after = _local_position(env, "plate_large")
        error_after = float(torch.linalg.vector_norm(plate_after[:2] - banana_after[:2]))
        lifted_after = float(banana_after[2]) - initial_banana_z
        height_after = float(banana_after[2] - plate_after[2])
        height_error_after = abs(args_cli.release_height - height_after)
        if correction_mode == "z" and error_after <= args_cli.center_tolerance:
            support_contact_event = support_monitor.update(
                object_z=float(banana_after[2]),
                eef_z=float(_eef_position(env)[2]),
                target_eef_z=eef_z_before + height_error,
                target_tolerance_m=args_cli.release_height_tolerance,
            )
        contact_after = (
            error_after <= 0.12
            and 0.0 <= height_after <= args_cli.plate_contact_height
        )
        iteration_record = {
            "iteration": iteration + 1,
            "correction_mode": correction_mode,
            "xy_error_before_m": error_norm,
            "requested_xy_step_m": xy_step.detach().cpu().tolist(),
            "height_above_plate_before_m": height_above_plate,
            "requested_z_step_m": float(z_step),
            "max_abs_joint_step_rad": float(torch.max(torch.abs(delta_joint))),
            "xy_error_after_m": error_after,
            "height_above_plate_after_m": height_after,
            "banana_lift_after_m": lifted_after,
            "banana_plate_contact_proxy": contact_after,
            "support_contact_event": support_contact_event,
            "terminal": terminal,
        }
        iterations.append(iteration_record)
        print(
            f"[center] mode={correction_mode} iteration={iteration + 1} "
            f"xy={error_norm:.4f}→{error_after:.4f}m "
            f"height={height_above_plate:.4f}→{height_after:.4f}m "
            f"step_xy={xy_step.detach().cpu().tolist()} step_z={float(z_step):.4f}m "
            f"max_dq={iteration_record['max_abs_joint_step_rad']:.4f}rad",
            flush=True,
        )
        if terminal:
            break
        if contact_after:
            contact_detected = True
            print(
                "[center] plate contact proxy became active; "
                "suppressing further Cartesian correction until release",
                flush=True,
            )
            break
        if support_contact_event is not None:
            contact_detected = True
            print(
                "[center] aligned object stopped descending inside the support "
                "envelope; suppressing further correction before release",
                flush=True,
            )
            break
        if height_after < 0.015:
            raise RuntimeError("Residual centering pushed the banana below safe plate clearance")
        if lifted_after < 0.010:
            raise RuntimeError(
                "Residual centering detected grasp loss before release: "
                f"banana lift fell to {lifted_after:.4f} m"
            )
        # Fail closed if a commanded correction materially moves away from the
        # plate. Tiny contact-induced fluctuations are allowed.
        if correction_mode == "xy" and error_after > previous_error + 0.005:
            raise RuntimeError(
                f"Residual centering diverged: XY error {previous_error:.4f}→{error_after:.4f} m"
            )
        if correction_mode == "z" and height_error_after > previous_height_error + 0.005:
            raise RuntimeError(
                "Residual centering diverged vertically: release-height error "
                f"{previous_height_error:.4f}→{height_error_after:.4f} m"
            )
        previous_error = error_after
        previous_height_error = height_error_after

    banana_final = _local_position(env, "banana")
    plate_final = _local_position(env, "plate_large")
    error_final = float(torch.linalg.vector_norm(plate_final[:2] - banana_final[:2]))
    height_final = float(banana_final[2] - plate_final[2])
    report = {
        "enabled": True,
        "xy_error_before_m": error_start,
        "xy_error_after_m": error_final,
        "target_tolerance_m": args_cli.center_tolerance,
        "height_above_plate_before_m": height_start,
        "height_above_plate_after_m": height_final,
        "target_release_height_m": args_cli.release_height,
        "release_height_tolerance_m": args_cli.release_height_tolerance,
        "plate_contact_height_m": args_cli.plate_contact_height,
        "banana_plate_contact_proxy": contact_detected,
        "support_contact_event": support_contact_event,
        "converged": (
            contact_detected
            or (
                error_final <= args_cli.center_tolerance
                and abs(height_final - args_cli.release_height)
                <= args_cli.release_height_tolerance
            )
        ),
        "iterations": iterations,
    }
    return obs, terminal, command, report


def _retreat_after_release(
    env: Any,
    obs: dict[str, Any],
    last_action: torch.Tensor,
) -> tuple[dict[str, Any], bool, torch.Tensor, dict[str, Any]]:
    """Raise the open gripper and verify that the released banana stays behind."""
    robot = env.scene["robot"]
    arm_joint_ids = [robot.data.joint_names.index(f"panda_joint{i}") for i in range(1, 8)]
    body_idx = robot.data.body_names.index("base_link")
    jacobi_body_idx = body_idx - 1 if robot.is_fixed_base else body_idx
    jacobi_joint_ids = [index + robot.num_base_dofs for index in arm_joint_ids]
    command = last_action.clone()
    command[0, 7] = 0.0
    terminal = False
    iterations: list[dict[str, Any]] = []
    eef_start = _eef_position(env)
    banana_start = _local_position(env, "banana")
    start_separation = float(torch.linalg.vector_norm(eef_start - banana_start))
    target_z = float(eef_start[2]) + args_cli.retreat_distance
    previous_remaining = args_cli.retreat_distance

    for iteration in range(args_cli.retreat_max_iterations):
        eef_before = _eef_position(env)
        remaining = target_z - float(eef_before[2])
        if remaining <= 0.010:
            break
        z_step = bounded_scalar_step(
            torch.tensor(remaining, dtype=torch.float32, device=env.device),
            args_cli.retreat_max_step,
        )
        desired_twist_w = torch.zeros(6, dtype=torch.float32, device=env.device)
        desired_twist_w[2] = z_step
        jacobian_w = robot.data.body_link_jacobian_w.torch[
            0, jacobi_body_idx
        ][:, jacobi_joint_ids]
        delta_joint = damped_least_squares_delta(
            jacobian_w,
            desired_twist_w,
            args_cli.center_damping,
            args_cli.center_max_joint_step,
        )
        joint_pos = robot.data.joint_pos.torch[0, arm_joint_ids]
        joint_limits = robot.data.soft_joint_pos_limits.torch[0, arm_joint_ids]
        command[0, :7] = torch.clamp(
            joint_pos + delta_joint,
            min=joint_limits[:, 0] + 1.0e-3,
            max=joint_limits[:, 1] - 1.0e-3,
        )
        obs, terminal = _hold_joint_action(
            env,
            obs,
            command,
            args_cli.retreat_settle_steps,
            gripper_closed=False,
        )
        eef_after = _eef_position(env)
        remaining_after = max(0.0, target_z - float(eef_after[2]))
        record = {
            "iteration": iteration + 1,
            "eef_z_before_m": float(eef_before[2]),
            "requested_z_step_m": float(z_step),
            "max_abs_joint_step_rad": float(torch.max(torch.abs(delta_joint))),
            "eef_z_after_m": float(eef_after[2]),
            "remaining_z_m": remaining_after,
            "terminal": terminal,
        }
        iterations.append(record)
        print(
            f"[retreat] iteration={iteration + 1} "
            f"eef_z={float(eef_before[2]):.4f}→{float(eef_after[2]):.4f}m "
            f"remaining={remaining_after:.4f}m "
            f"max_dq={record['max_abs_joint_step_rad']:.4f}rad",
            flush=True,
        )
        if terminal:
            break
        if remaining_after > previous_remaining + 0.004:
            raise RuntimeError(
                "Release retreat diverged: remaining vertical distance "
                f"{previous_remaining:.4f}→{remaining_after:.4f} m"
            )
        previous_remaining = remaining_after

    # Let the object settle with the gripper open and clear before scoring it.
    obs, settle_terminal = _hold_joint_action(
        env,
        obs,
        command,
        args_cli.retreat_settle_steps * 2,
        gripper_closed=False,
    )
    terminal = terminal or settle_terminal
    eef_final = _eef_position(env)
    banana_final = _local_position(env, "banana")
    plate_final = _local_position(env, "plate_large")
    final_separation = float(torch.linalg.vector_norm(eef_final - banana_final))
    retreat_z = float(eef_final[2] - eef_start[2])
    banana_motion = float(torch.linalg.vector_norm(banana_final - banana_start))
    banana_plate_xy_error = float(
        torch.linalg.vector_norm(banana_final[:2] - plate_final[:2])
    )
    banana_height_above_plate = float(banana_final[2] - plate_final[2])
    on_plate = (
        banana_plate_xy_error <= 0.12
        and 0.0 <= banana_height_above_plate <= 0.20
    )
    state_final = _state(env, float(banana_start[2]))
    gripper_open = state_final["gripper_closed_fraction"] <= 0.10
    detachment = assess_release_detachment(
        controlled_start_xyz=eef_start.numpy(),
        controlled_final_xyz=eef_final.numpy(),
        subject_start_xyz=banana_start.numpy(),
        subject_final_xyz=banana_final.numpy(),
        released=gripper_open,
        goal_relation_holds=on_plate,
        terminal=terminal,
        minimum_retreat_m=max(0.040, args_cli.retreat_distance - 0.020),
    )
    converged = bool(detachment["converged"])
    report = {
        "enabled": True,
        "requested_retreat_m": args_cli.retreat_distance,
        "eef_start_xyz": eef_start.tolist(),
        "eef_final_xyz": eef_final.tolist(),
        "eef_retreat_z_m": retreat_z,
        "banana_start_xyz": banana_start.tolist(),
        "banana_final_xyz": banana_final.tolist(),
        "banana_motion_during_retreat_m": banana_motion,
        "banana_plate_xy_error_after_m": banana_plate_xy_error,
        "banana_height_above_plate_after_m": banana_height_above_plate,
        "banana_remained_on_plate": on_plate,
        "eef_banana_separation_before_m": start_separation,
        "eef_banana_separation_after_m": final_separation,
        "gripper_closed_fraction_after": state_final["gripper_closed_fraction"],
        "detachment_evidence": detachment,
        "converged": converged,
        "iterations": iterations,
    }
    return obs, terminal, command, report


def main() -> int:
    global ACTIVE_EPISODE_RECORDER, ACTIVE_SENSOR_MONITOR, ACTIVE_SENSOR_SAMPLE_INDEX
    global ACTIVE_ROS2_SENSOR_INGRESS
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("Set GEMINI_API_KEY or GOOGLE_API_KEY in the environment")
    if not args_cli.disable_adaptive_ik:
        positive_adaptive_values = {
            "adaptive_tolerance": args_cli.adaptive_tolerance,
            "adaptive_max_step": args_cli.adaptive_max_step,
            "adaptive_max_iterations": args_cli.adaptive_max_iterations,
            "adaptive_settle_steps": args_cli.adaptive_settle_steps,
            "adaptive_max_joint_step": args_cli.adaptive_max_joint_step,
            "adaptive_damping": args_cli.adaptive_damping,
            "adaptive_orientation_tolerance_deg": args_cli.adaptive_orientation_tolerance_deg,
            "adaptive_max_angle_step_deg": args_cli.adaptive_max_angle_step_deg,
            "coach_interval_iterations": args_cli.coach_interval_iterations,
            "motion_checkpoint_replans": args_cli.motion_checkpoint_replans,
            "maximum_model_target_correction": (
                args_cli.maximum_model_target_correction
            ),
            "maximum_grasp_drift": args_cli.maximum_grasp_drift,
            "minimum_transport_lift": args_cli.minimum_transport_lift,
            "recovery_hold_steps": args_cli.recovery_hold_steps,
            "recovery_stability_drift": args_cli.recovery_stability_drift,
            "recovery_set_down_clearance": args_cli.recovery_set_down_clearance,
            "approach_clearance": args_cli.approach_clearance,
            "lift_clearance": args_cli.lift_clearance,
            "plate_hover_height": args_cli.plate_hover_height,
        }
        invalid = {
            name: value for name, value in positive_adaptive_values.items() if value <= 0
        }
        if invalid:
            raise ValueError(f"Adaptive-IK parameters must be positive: {invalid}")
        if args_cli.max_transport_recoveries < 0:
            raise ValueError("max-transport-recoveries must be non-negative")
    relocation_values = (
        *args_cli.banana_offset,
        *args_cli.plate_offset,
        args_cli.banana_yaw_deg,
        *(tuple() if args_cli.light_intensity is None else (args_cli.light_intensity,)),
    )
    if not all(np.isfinite(value) for value in relocation_values):
        raise ValueError("Object relocation offsets must be finite")
    if args_cli.light_intensity is not None and args_cli.light_intensity <= 0:
        raise ValueError("light-intensity must be positive")
    if not args_cli.disable_residual_centering:
        positive_centering_values = {
            "center_tolerance": args_cli.center_tolerance,
            "center_max_step": args_cli.center_max_step,
            "center_max_z_step": args_cli.center_max_z_step,
            "release_height": args_cli.release_height,
            "release_height_tolerance": args_cli.release_height_tolerance,
            "plate_contact_height": args_cli.plate_contact_height,
            "center_max_iterations": args_cli.center_max_iterations,
            "center_settle_steps": args_cli.center_settle_steps,
            "center_max_joint_step": args_cli.center_max_joint_step,
            "center_damping": args_cli.center_damping,
        }
        invalid = {name: value for name, value in positive_centering_values.items() if value <= 0}
        if invalid:
            raise ValueError(f"Residual-centering parameters must be positive: {invalid}")
    if not args_cli.disable_release_retreat:
        positive_retreat_values = {
            "retreat_distance": args_cli.retreat_distance,
            "retreat_max_step": args_cli.retreat_max_step,
            "retreat_max_iterations": args_cli.retreat_max_iterations,
            "retreat_settle_steps": args_cli.retreat_settle_steps,
        }
        invalid = {name: value for name, value in positive_retreat_values.items() if value <= 0}
        if invalid:
            raise ValueError(f"Release-retreat parameters must be positive: {invalid}")
    if args_cli.episode_index < -1:
        raise ValueError("episode-index must be -1 or a non-negative integer")
    if args_cli.record_video_scale <= 0:
        raise ValueError("record-video-scale must be positive")
    if args_cli.model_max_retries < 0 or args_cli.model_retry_backoff < 0:
        raise ValueError("model retry count and backoff must be non-negative")
    if not 0.0 <= args_cli.minimum_contact_coverage <= 1.0:
        raise ValueError("minimum-contact-coverage must be in [0, 1]")
    if args_cli.minimum_touch_samples < 1:
        raise ValueError("minimum-touch-samples must be positive")
    actuator_feedback_policy = ActuatorFeedbackEventPolicy(
        minimum_position_change=args_cli.actuator_feedback_position_change,
        minimum_force_change_n=args_cli.actuator_feedback_force_change,
    )

    demo_path = args_cli.demo.expanduser().resolve()
    if not demo_path.is_file():
        raise FileNotFoundError(f"Successful local motion primitive not found: {demo_path}")
    with h5py.File(demo_path, "r") as source:
        demo = source["data/demo_0"]
        if not bool(demo.attrs.get("success", False)):
            raise RuntimeError(f"Refusing to execute a demonstration not marked successful: {demo_path}")
        recorded_actions = np.asarray(demo["actions"], dtype=np.float32)
        joint_states = np.asarray(
            demo["states/articulation/robot/joint_position"], dtype=np.float32
        )
    change_points = np.flatnonzero(
        np.max(np.abs(np.diff(recorded_actions, axis=0)), axis=1) > 1.0e-5
    ) + 1
    boundaries = np.concatenate(([0], change_points, [len(recorded_actions)]))
    phase_names = ["approach_banana", "descend", "grasp", "lift", "above_plate"]
    if len(boundaries) - 1 != len(phase_names):
        raise RuntimeError(
            f"Expected {len(phase_names)} semantic trajectory segments, got {len(boundaries) - 1}"
        )

    args_cli.artifact_dir.mkdir(parents=True, exist_ok=True)
    training_episode_dir = (
        args_cli.training_episode_dir
        if args_cli.training_episode_dir is not None
        else args_cli.artifact_dir / "training_episodes"
    ).expanduser().resolve()
    critic_memory_path = args_cli.artifact_dir / "critic_guidance.json"
    critic_memory = (
        {"source_model": None, "lessons": []}
        if args_cli.disable_critic_guidance
        else _load_critic_guidance(critic_memory_path, args_cli.task)
    )
    robolab.constants.set_output_dir(str(args_cli.artifact_dir / "robolab_output"))
    # Use an unfiltered two-finger sensor below instead of RoboLab's brittle
    # legacy object-pair filter graph.
    robolab.constants.ENABLE_SUBTASK_PROGRESS_CHECKING = False
    robolab.constants.RECORD_IMAGE_DATA = False
    robolab.constants.VERBOSE = False

    print("=" * 78)
    print("VISIBLE TEST SUITE: Gemini Robotics ER 2 + RoboLab DROID/Franka")
    print(f"Model: {MODEL_ID}")
    print(f"Task:  {args_cli.task}")
    print(f"GUI:   {'off (headless)' if args_cli.headless else 'on'}")
    print(
        "World-intent shadow: "
        + ("disabled" if args_cli.disable_world_intent_shadow else args_cli.instruction)
    )
    print(f"Local motion primitive: {demo_path} ({len(recorded_actions)} steps)")
    print(
        "Control cadence: one observation-bound model tool per runtime operation; "
        + (
            f"periodic + event checkpoints every {args_cli.coach_interval_iterations} "
            "local IK chunks"
            if args_cli.periodic_motion_observations
            else "event/completion checkpoints only during each multi-step IK lease"
        )
    )
    print(
        "Motion executor: runtime-registered bounded DLS IK; model-configurable "
        f"with target correction≤{args_cli.maximum_model_target_correction:.3f}m"
    )
    print(
        "Actuator executor: runtime-registered binary clamp; model-selectable "
        "engage/disengage/maintain with 8–120 settling steps"
    )
    print(
        "Operation scheduler: fresh-observation routing between continued "
        "motion and actuator evaluation; no recorded gripper-state hints"
    )
    print(
        "Post-actuation feedback: immediate Gemini reschedule when position "
        f"change≥{actuator_feedback_policy.minimum_position_change:.3f} and "
        "touch changes or force delta≥"
        f"{actuator_feedback_policy.minimum_force_change_n:.3f}N"
    )
    print(
        "Live-pose adaptive IK: "
        + (
            "off (fixed demonstration replay)"
            if args_cli.disable_adaptive_ik
            else (
                f"on (tolerance={args_cli.adaptive_tolerance:.3f}m, "
                f"step≤{args_cli.adaptive_max_step:.3f}m, "
                f"iterations≤{args_cli.adaptive_max_iterations}, "
                f"banana_offset={list(args_cli.banana_offset)}, "
                f"plate_offset={list(args_cli.plate_offset)}, "
                f"banana_yaw={args_cli.banana_yaw_deg:.1f}deg)"
            )
        )
    )
    print(
        "Model-governed placement: "
        + (
            "off (baseline mode)"
            if args_cli.disable_residual_centering
            else (
                "on (fresh RGB-D/state motion tool; "
                f"measured XY tolerance={args_cli.center_tolerance:.3f}m, "
                f"release height={args_cli.release_height:.3f}m)"
            )
        )
    )
    print(
        "Passive critic memory: "
        + (
            f"{len(critic_memory['lessons'])} lessons from {critic_memory['source_model']}"
            if critic_memory["lessons"]
            else "none"
        )
    )
    print(
        "Post-release detachment check: "
        + (
            "off"
            if args_cli.disable_release_retreat
            else f"on (open-gripper retreat={args_cli.retreat_distance:.3f}m)"
        )
    )
    print(
        "Training capture: "
        + (
            "disabled"
            if args_cli.disable_training_recording
            else f"successful Gemini completions only → {training_episode_dir}"
        )
    )
    print(
        "ROS 2 sensor ingress: "
        + (
            "configured (subscriber availability checked after scene startup)"
            if args_cli.ros2_sensor_ingress
            else "disabled"
        )
    )
    print("=" * 78, flush=True)

    sim_version_path = Path(os.environ.get("ISAAC_SIM_ROOT", "")) / "VERSION"
    sim_version = (
        sim_version_path.read_text().strip()
        if sim_version_path.is_file()
        else "6.x runtime (VERSION path unavailable)"
    )
    sim6_ok = sim_version.startswith("6.")
    _test_line(1, "Isaac Sim 6 runtime", sim6_ok, sim_version)

    auto_register_droid_abs_ik_envs(
        task=args_cli.task,
        contact_sensors=False,
    )
    env_cfg = parse_env_cfg(
        args_cli.task, device="cuda:0", seed=0, num_envs=1, use_fabric=True
    )
    # RoboLab's explicit robot/object poses came from the Sim 5 (w, x, y, z)
    # configuration contract. This Sim 6 source build consumes spawn poses as
    # (x, y, z, w); convert only the legacy-authored fields at the boundary.
    env_cfg.scene.robot.init_state.rot = (0.0, 0.0, 0.0, 1.0)
    fixture_rot = env_cfg.scene.table_fixture.init_state.rot
    env_cfg.scene.table_fixture.init_state.rot = (
        fixture_rot[1], fixture_rot[2], fixture_rot[3], fixture_rot[0]
    )
    for asset_name in env_cfg.contact_object_list:
        asset_cfg = getattr(env_cfg.scene, asset_name)
        w, x, y, z = asset_cfg.init_state.rot
        asset_cfg.init_state.rot = (x, y, z, w)
    if not args_cli.disable_contact_telemetry:
        install_sim6_gripper_contact_sensor(env_cfg)
    if args_cli.randomize_background:
        from robolab.variations.backgrounds import find_background_files

        backgrounds = find_background_files()
        current_background = str(env_cfg.scene.dome_light.spawn.texture_file)
        backgrounds = [path for path in backgrounds if str(path) != current_background]
        if not backgrounds:
            raise FileNotFoundError("No non-default RoboLab HDRI backgrounds are available")
        env_cfg.scene.dome_light.spawn.texture_file = random.Random(
            args_cli.appearance_seed
        ).choice(backgrounds)
    if args_cli.light_intensity is not None:
        sphere_light = getattr(env_cfg.scene, "sphere_light", None)
        if sphere_light is None:
            raise RuntimeError("Requested light variation but scene has no sphere_light")
        sphere_light.spawn.intensity = args_cli.light_intensity
    if args_cli.rgbd_safety:
        for camera_name in ("over_shoulder_left_camera", "wrist_cam"):
            camera_cfg = getattr(env_cfg.scene, camera_name)
            if "depth" not in camera_cfg.data_types:
                camera_cfg.data_types = [*camera_cfg.data_types, "depth"]
        exterior_camera_cfg = env_cfg.scene.over_shoulder_left_camera
        if "instance_id_segmentation_fast" not in exterior_camera_cfg.data_types:
            exterior_camera_cfg.data_types = [
                *exterior_camera_cfg.data_types,
                "instance_id_segmentation_fast",
            ]
        exterior_camera_cfg.renderer_cfg.colorize_instance_id_segmentation = False
        env_cfg.scene.lazy_sensor_update = False
    # Sim 6's absolute-IK bridge currently resolves the Robotiq control body
    # incorrectly. Replay the demonstrated arm states through the stable joint
    # controller while ER 2 performs fresh visual gates at semantic boundaries.
    env_cfg.actions = DroidJointPositionActionCfg()
    env_cfg.terminations = None
    env_cfg.subtasks = None
    # RoboLab's EE-state recorder still consumes the removed Sim 5 tensor API
    # and is not needed for this live control/visualization test.
    env_cfg.recorders = None
    env, _ = create_env(env_cfg, use_fabric=True, policy="gemini-er2")
    obs, _ = env.reset()
    ACTIVE_SENSOR_MONITOR = SensorCaptureBuffer()
    ACTIVE_SENSOR_SAMPLE_INDEX = 0
    contact_sensor_info = contact_sensor_runtime_info(env)
    print(f"[contact-sensor] {contact_sensor_info}", flush=True)
    baseline_banana_xyz = _local_position(env, "banana")
    baseline_banana_quat = _local_quaternion(env, "banana")
    grasp_offset_object, object_to_grasp_quat = derive_object_relative_grasp(
        baseline_banana_xyz,
        baseline_banana_quat,
        baseline_banana_xyz + BANANA_GRASP_OFFSET,
        BANANA_GRASP_QUAT,
    )
    # Do not restore Sim 5 rigid-body snapshots into Sim 6. Their contact state
    # can begin interpenetrating and eject task objects on the first step.
    _transform_asset_pose(
        env,
        "banana",
        tuple(args_cli.banana_offset),
        yaw_degrees=args_cli.banana_yaw_deg,
    )
    _transform_asset_pose(env, "plate_large", tuple(args_cli.plate_offset))
    _set_sim6_camera_views(env)
    env.sim.render()
    obs = env.observation_manager.compute()
    coach = GeminiRoboticsER2(api_key, args_cli.timeout)
    motion_tool_provider = GeminiProvider(api_key, MODEL_ID)
    trackable_object_ids = tuple(
        object_id
        for object_id in env_cfg.contact_object_list
        if isinstance(object_id, str) and object_id
    )
    motion_executor_registry = _local_dls_executor_registry(trackable_object_ids)
    actuator_executor_registry = _local_binary_actuator_registry()
    initial_banana = _local_position(env, "banana")
    initial_banana_z = float(initial_banana[2])
    initial_eef = _eef_position(env)
    robot_root_w = env.scene["robot"].data.root_pos_w
    robot_root_w = getattr(robot_root_w, "torch", robot_root_w)[0].detach().cpu()
    spawn_ok = bool(initial_eef[2] > 0.15 and initial_eef[2] < 1.50)
    print(
        f"[spawn] robot_root={robot_root_w.tolist()} eef_root={initial_eef.tolist()} "
        f"tabletop_z≈0.0 valid={spawn_ok}",
        flush=True,
    )
    if not spawn_ok:
        raise RuntimeError("Robot/table spawn validation failed before policy execution")

    tests: dict[str, bool] = {"runtime": sim6_ok}
    digests: list[str] = []
    eef_trace: list[list[float]] = [_eef_position(env).tolist()]
    model_calls = 0
    terminal = False
    episode_trace: dict[str, Any] = {
        "schema_version": 2,
        "task": args_cli.task,
        "coach_model": MODEL_ID,
        "critic_memory_applied": critic_memory,
        "sim_version": sim_version,
        "physics_steps_are_local": True,
        "motion_executor": (
            "fixed_demonstration_replay"
            if args_cli.disable_adaptive_ik
            else "live_pose_bounded_dls_ik"
        ),
        "requested_relocation_xy_m": {
            "banana": list(args_cli.banana_offset),
            "plate": list(args_cli.plate_offset),
        },
        "requested_banana_yaw_deg": args_cli.banana_yaw_deg,
        "appearance": {
            "randomized_background": args_cli.randomize_background,
            "appearance_seed": args_cli.appearance_seed,
            "sphere_light_intensity": (
                args_cli.light_intensity
                if args_cli.light_intensity is not None
                else float(env_cfg.scene.sphere_light.spawn.intensity)
            ),
            "background_texture": str(env_cfg.scene.dome_light.spawn.texture_file),
        },
        "rgbd_safety": {
            "enabled": args_cli.rgbd_safety,
            "motion_checkpoint_depth_panel": args_cli.rgbd_safety,
            "tracked_object_translation_source": (
                "ros2_tracked_object_status_when_published_else_"
                "sim_privileged_relative_pose"
            ),
            "tracked_object_orientation_source": (
                "ros2_tracked_object_status_when_published_else_"
                "rgbd_instance_depth_major_axis"
                if args_cli.rgbd_safety
                else None
            ),
            "tracked_object_rgbd_axis_adapter_implemented": True,
        },
        "checkpoint_recovery": {
            "rejected_tool_replan_limit": args_cli.motion_checkpoint_replans,
            "preserves_local_correction_limit_m": (
                args_cli.maximum_model_target_correction
            ),
            "stable_grasp_clearance_recovery_is_model_selected": True,
        },
        "ros2_sensor_ingress": {
            "enabled": args_cli.ros2_sensor_ingress,
            "available": None,
            "status": "pending" if args_cli.ros2_sensor_ingress else "disabled",
        },
        "contact_sensor": contact_sensor_info,
        "world_intent_shadow": {
            "status": (
                "disabled" if args_cli.disable_world_intent_shadow else "pending"
            ),
            "contract_version": WORLD_INTENT_SCHEMA_VERSION,
            "motion_authority": False,
            "authority_scope": [],
            "instruction": args_cli.instruction,
        },
        "motion_tool_protocol": {
            "observation_bound": True,
            "lease_model_observation_mode": (
                "periodic_or_event"
                if args_cli.periodic_motion_observations
                else "event_or_completion_only"
            ),
            "tracked_pose_source": (
                "ros2_tracked_object_status_when_published_else_"
                "sim_privileged_relative_pose"
            ),
            "rgbd_tracked_orientation_source": (
                "ros2_tracked_object_status_when_published_else_"
                "rgbd_instance_depth_major_axis"
                if args_cli.rgbd_safety
                else None
            ),
            "rgbd_tracked_axis_adapter_implemented": True,
            "maximum_target_correction_m": (
                args_cli.maximum_model_target_correction
            ),
            "registered_executors": [
                {
                    "executor_id": spec.executor_id,
                    "tool_name": spec.tool_name,
                    "configuration_schema": spec.configuration_schema,
                }
                for spec in motion_executor_registry.specs()
            ],
            "calls": [],
        },
        "actuator_tool_protocol": {
            "observation_bound": True,
            "registered_executors": [
                {
                    "executor_id": spec.executor_id,
                    "tool_name": spec.tool_name,
                    "command_schema": spec.command_schema,
                    "configuration_schema": spec.configuration_schema,
                }
                for spec in actuator_executor_registry.specs()
            ],
            "calls": [],
        },
        "operation_scheduler_protocol": {
            "observation_bound": True,
            "candidate_source": "runtime",
            "recorded_actuator_hints": False,
            "post_actuation_feedback_policy": {
                "minimum_position_change": (
                    actuator_feedback_policy.minimum_position_change
                ),
                "minimum_force_change_n": (
                    actuator_feedback_policy.minimum_force_change_n
                ),
            },
            "calls": [],
        },
        "transport_recovery": {
            "maximum_attempts": args_cli.max_transport_recoveries,
            "stability_hold_steps": args_cli.recovery_hold_steps,
            "maximum_stability_drift_m": args_cli.recovery_stability_drift,
            "set_down_clearance_m": args_cli.recovery_set_down_clearance,
            "strategies": [
                "stable_grasp_relatch",
                "set_down_reacquire_regrasp",
            ],
        },
        "object_relative_grasp": {
            "offset_object_m": grasp_offset_object.tolist(),
            "quaternion_object_to_grasp_wxyz": object_to_grasp_quat.tolist(),
        },
        "initial_state": _state(env, initial_banana_z),
        "stages": [],
        "status": "running",
    }
    trace_path = args_cli.artifact_dir / "sequence_trace.json"
    _write_trace(trace_path, episode_trace)
    episode_recorder: GeminiEpisodeDatasetRecorder | None = None
    ros2_sensor_ingress: ROS2SensorIngress | None = None
    if not args_cli.disable_training_recording:
        episode_index = (
            args_cli.episode_index
            if args_cli.episode_index >= 0
            else _next_episode_index(training_episode_dir)
        )
        episode_recorder = GeminiEpisodeDatasetRecorder(
            output_dir=training_episode_dir,
            episode_index=episode_index,
            metadata={
                "task": args_cli.task,
                "instruction": args_cli.instruction,
                "coach_model": MODEL_ID,
                "sim_version": sim_version,
                "banana_offset_xy_m": list(args_cli.banana_offset),
                "plate_offset_xy_m": list(args_cli.plate_offset),
                "banana_yaw_deg": args_cli.banana_yaw_deg,
                "appearance": episode_trace["appearance"],
                "object_relative_grasp": episode_trace["object_relative_grasp"],
            },
            video_writer_factory=VideoWriter,
            unpack_images=unpack_image_obs,
            fps=15,
            video_scale=args_cli.record_video_scale,
            require_contact_telemetry=not args_cli.disable_contact_telemetry,
            minimum_contact_coverage=args_cli.minimum_contact_coverage,
            minimum_touch_samples=args_cli.minimum_touch_samples,
        )
        ACTIVE_EPISODE_RECORDER = episode_recorder
        episode_trace["training_capture"] = {
            "status": "recording_unpublished",
            "episode_index": episode_index,
            "admission_rule": "all_physical_and_supervision_tests_pass",
        }
        _write_trace(trace_path, episode_trace)

    try:
        if args_cli.ros2_sensor_ingress:
            ros2_sensor_ingress = start_ros2_sensor_ingress(
                ROS2SensorIngressConfig(
                    touch_topic=args_cli.ros2_touch_topic,
                    contact_wrench_topic=args_cli.ros2_contact_wrench_topic,
                    contact_status_topic=args_cli.ros2_contact_status_topic,
                    rgbd_status_topic=args_cli.ros2_rgbd_status_topic,
                    safety_stop_topic=args_cli.ros2_safety_stop_topic,
                    tracked_object_status_topic=(
                        args_cli.ros2_tracked_object_status_topic
                    ),
                    motion_status_topic=args_cli.ros2_motion_status_topic,
                )
            )
            ACTIVE_ROS2_SENSOR_INGRESS = ros2_sensor_ingress
            episode_trace["ros2_sensor_ingress"] = {
                "enabled": True,
                "status": (
                    "subscribed"
                    if ros2_sensor_ingress.available
                    else "unavailable_using_simulator_fallback"
                ),
                **ros2_sensor_ingress.status(),
            }
            print(
                "[ros2-ingress] "
                f"status={episode_trace['ros2_sensor_ingress']['status']} "
                f"error={ros2_sensor_ingress.error}",
                flush=True,
            )
            _write_trace(trace_path, episode_trace)
        frame = _single_exterior_frame(obs)
        cv2.imwrite(
            str(args_cli.artifact_dir / "00_scene.jpg"),
            cv2.cvtColor(frame, cv2.COLOR_RGB2BGR),
        )
        if not args_cli.disable_world_intent_shadow:
            try:
                shadow_payload, shadow_latency, shadow_digest = coach.reason(
                    build_world_intent_prompt(args_cli.instruction),
                    frame,
                )
                shadow_intent = WorldIntent.from_mapping(shadow_payload)
                episode_trace["world_intent_shadow"] = {
                    "status": "valid",
                    "contract_version": WORLD_INTENT_SCHEMA_VERSION,
                    "motion_authority": False,
                    "authority_scope": [],
                    "instruction": args_cli.instruction,
                    "intent": shadow_intent.to_dict(),
                    "latency_s": shadow_latency,
                    "image_digest": shadow_digest,
                }
                print(
                    f"[world-intent] VALID operation={shadow_intent.operation} "
                    f"goals={len(shadow_intent.goals)} "
                    f"constraints={len(shadow_intent.constraints)} "
                    f"latency={shadow_latency:.2f}s authority=none",
                    flush=True,
                )
            except Exception as shadow_error:
                episode_trace["world_intent_shadow"] = {
                    "status": "invalid",
                    "contract_version": WORLD_INTENT_SCHEMA_VERSION,
                    "motion_authority": False,
                    "authority_scope": [],
                    "instruction": args_cli.instruction,
                    "error": {
                        "type": type(shadow_error).__name__,
                        "message": str(shadow_error),
                    },
                }
                print(
                    f"[world-intent] INVALID {type(shadow_error).__name__}: "
                    f"{shadow_error} authority=none",
                    flush=True,
                )
            _write_trace(trace_path, episode_trace)

        scene, latency, digest = coach.reason(
            _scene_prompt(
                _state(env, initial_banana_z),
                _critic_context(critic_memory, "global"),
            ),
            frame,
        )
        model_calls += 1
        digests.append(digest)
        tests["model"] = True
        _test_line(2, "ER 2 API + JSON", True, f"{latency:.2f}s image={digest}")
        scene_ok = bool(scene.get("scene_ok")) and bool(scene.get("banana_visible")) and bool(
            scene.get("plate_visible")
        )
        tests["scene"] = scene_ok
        _test_line(3, "visual scene grounding", scene_ok, str(scene.get("assessment", "")))
        episode_trace["scene_decision"] = scene
        episode_trace["motion_checkpoints"] = []
        _write_trace(trace_path, episode_trace)

        def motion_checkpoint_handler(
            checkpoint_obs: dict[str, Any], checkpoint: dict[str, Any]
        ) -> dict[str, Any]:
            nonlocal model_calls
            # Recovery/status checkpoints may report a measured condition
            # without proposing a destination. Bind those calls to the fresh
            # measured end-effector pose so the model can hold, abort, or apply
            # a bounded correction through the same executor protocol.
            previous_outcome: dict[str, Any] | None = checkpoint.get(
                "previous_motion_tool_outcome"
            )
            max_attempts = max(1, int(args_cli.motion_checkpoint_replans))
            decision: dict[str, Any] = {}
            for attempt in range(max_attempts):
                checkpoint_state = _state(env, initial_banana_z)
                checkpoint_frame = _single_exterior_frame(checkpoint_obs)
                checkpoint_frame, depth_summary = _rgbd_checkpoint_frame(
                    env, checkpoint_frame
                )
                checkpoint_index = len(episode_trace["motion_checkpoints"])
                frame_name = f"motion_checkpoint_{checkpoint_index:03d}.jpg"
                cv2.imwrite(
                    str(args_cli.artifact_dir / frame_name),
                    cv2.cvtColor(checkpoint_frame, cv2.COLOR_RGB2BGR),
                )
                motion_context = {
                    **checkpoint,
                    "rgbd": depth_summary,
                    "replan_attempt": attempt + 1,
                    "maximum_replan_attempts": max_attempts,
                    "previous_motion_tool_outcome": previous_outcome,
                }
                motion_context.setdefault(
                    "current_target_xyz_m",
                    checkpoint_state["eef_gripper_base_xyz"],
                )
                current_target = torch.tensor(
                    motion_context["current_target_xyz_m"], dtype=torch.float32
                )
                decision, latency, digest = _choose_observation_bound_motion_tool(
                    motion_tool_provider,
                    motion_executor_registry,
                    instruction=args_cli.instruction,
                    observation_prefix=f"checkpoint-{checkpoint_index}",
                    frame=checkpoint_frame,
                    state=checkpoint_state,
                    current_target=current_target,
                    motion_context=motion_context,
                    rgbd_summary=depth_summary,
                    critic_context=_critic_context(
                        critic_memory, str(checkpoint["phase"])
                    ),
                )
                model_calls += 1
                digests.append(digest)
                event = {
                    **motion_context,
                    "frame": frame_name,
                    "state": checkpoint_state,
                    "coach_decision": decision,
                    "coach_latency_s": latency,
                    "image_digest": digest,
                }
                episode_trace["motion_checkpoints"].append(event)
                episode_trace["motion_tool_protocol"]["calls"].append(
                    decision["motion_tool"]
                )
                _write_trace(trace_path, episode_trace)
                tool = decision["motion_tool"]
                scheduler_selected_motion = bool(
                    isinstance(checkpoint.get("scheduler_decision"), dict)
                    and checkpoint["scheduler_decision"].get("decision")
                    == "dispatch"
                    and checkpoint["scheduler_decision"].get("operation_id")
                    == "continue.runtime_motion"
                )
                if (
                    scheduler_selected_motion
                    and tool.get("action") == "hold"
                    and tool.get("status") != "rejected"
                ):
                    semantic_error = (
                        "hold_motion cannot repeat after the fresh operation "
                        "scheduler dispatched continue.runtime_motion; issue a "
                        "bounded corrective movement or abort"
                    )
                    tool["status"] = "rejected"
                    tool["error"] = semantic_error
                    decision["assessment"] = (
                        "Motion tool rejected by scheduler-motion contract: "
                        f"{semantic_error}"
                    )
                print(
                    f"[ER2 checkpoint] phase={checkpoint['phase']} "
                    f"iteration={checkpoint['iteration']} reason={checkpoint['reason']} "
                    f"replan={attempt + 1}/{max_attempts} "
                    f"tool={tool['tool_name']} status={tool.get('status')} "
                    f"decision={decision.get('decision')} latency={latency:.2f}s",
                    flush=True,
                )
                # A valid execute, explicit model hold, or explicit abort is a
                # real decision. Only locally rejected calls are coached again.
                if tool.get("status") != "rejected":
                    return decision
                previous_outcome = {
                    "decision": decision.get("decision"),
                    "assessment": decision.get("assessment"),
                    "motion_tool": tool,
                }
            decision["replan_attempts_exhausted"] = True
            decision["replan_attempt_count"] = max_attempts
            return decision

        def operation_scheduler_handler(
            schedule_obs: dict[str, Any],
            current_action: torch.Tensor,
            *,
            phase_label: str,
            observation_prefix: str,
            motion_report: dict[str, Any],
            trigger_event: dict[str, Any] | None = None,
        ) -> tuple[dict[str, Any], bool, dict[str, Any], float, str]:
            """Route the next runtime operation, with one fail-closed retry."""
            nonlocal model_calls
            previous_outcome: dict[str, Any] | None = None
            total_latency = 0.0
            terminal = False
            digest = ""
            for attempt in range(2):
                schedule_state = _state(env, initial_banana_z)
                schedule_frame = _single_exterior_frame(schedule_obs)
                schedule_frame, depth_summary = _rgbd_checkpoint_frame(
                    env, schedule_frame
                )
                frame_name = (
                    f"{observation_prefix}"
                    f"{'_retry' if attempt else ''}.jpg"
                )
                cv2.imwrite(
                    str(args_cli.artifact_dir / frame_name),
                    cv2.cvtColor(schedule_frame, cv2.COLOR_RGB2BGR),
                )
                current_engaged = bool(
                    float(current_action[0, 7].detach().cpu()) > 0.5
                )
                # The task adapter supplies measured goal contact while the
                # contact sensor supplies retained-contact evidence. Preserve
                # a loaded clamp away from the goal, but expose its actuator
                # again after contact loss so the model can recover instead of
                # being forced into an impossible motion-only branch.
                goal_contact_observed = bool(
                    schedule_state.get("banana_plate_contact_proxy", False)
                )
                current_contact = schedule_state.get("current_contact")
                retained_contact_observed = bool(
                    isinstance(current_contact, dict)
                    and current_contact.get("available")
                    and current_contact.get("touch")
                )
                actuator_recovery_observed = bool(
                    current_engaged and not retained_contact_observed
                )
                actuator_transition_available = bool(
                    not current_engaged
                    or goal_contact_observed
                    or actuator_recovery_observed
                )
                candidates = _post_motion_operation_candidates(
                    actuator_transition_available=(
                        actuator_transition_available
                    )
                )
                decision, latency, digest = _choose_observation_bound_operation(
                    motion_tool_provider,
                    instruction=args_cli.instruction,
                    observation_prefix=(
                        f"{observation_prefix}-attempt-{attempt + 1}"
                    ),
                    frame=schedule_frame,
                    state=schedule_state,
                    operation_context={
                        "runtime_label": phase_label,
                        "current_actuator_state": (
                            "engaged" if current_engaged else "disengaged"
                        ),
                        "actuator_transition_admission": {
                            "available": actuator_transition_available,
                            "source": "measured_runtime_actuator_preconditions",
                            "goal_contact_observed": goal_contact_observed,
                            "retained_contact_observed": (
                                retained_contact_observed
                            ),
                            "contact_loss_recovery": actuator_recovery_observed,
                        },
                        "previous_operation_outcome": previous_outcome,
                        "trigger_event": trigger_event,
                        "completed_motion": {
                            "converged": motion_report.get("converged"),
                            "yielded_to_scheduler": motion_report.get(
                                "yielded_to_scheduler", False
                            ),
                            "target_error_after_m": motion_report.get(
                                "target_error_after_m"
                            ),
                            "orientation_error_after_deg": motion_report.get(
                                "orientation_error_after_deg"
                            ),
                            "recovery_request": motion_report.get(
                                "recovery_request"
                            ),
                        },
                    },
                    candidates=candidates,
                    rgbd_summary=depth_summary,
                    critic_context=_critic_context(critic_memory, phase_label),
                )
                total_latency += latency
                model_calls += 1
                digests.append(digest)
                event = {
                    **decision["scheduler_tool"],
                    "runtime_label": phase_label,
                    "attempt": attempt + 1,
                    "frame": frame_name,
                    "state": schedule_state,
                    "advertised_candidates": [
                        candidate.to_dict() for candidate in candidates
                    ],
                    "latency_s": latency,
                }
                episode_trace["operation_scheduler_protocol"]["calls"].append(event)
                _write_trace(trace_path, episode_trace)
                print(
                    f"[ER2 scheduler] label={phase_label} "
                    f"tool={decision['scheduler_tool'].get('tool_name')} "
                    f"operation={decision.get('operation_id')} "
                    f"kind={decision.get('operation_kind')} "
                    f"decision={decision.get('decision')} "
                    f"confidence={decision.get('confidence', 0.0):.2f} "
                    f"latency={latency:.2f}s image={digest}\n"
                    f"      {decision.get('assessment', '')}",
                    flush=True,
                )
                if decision.get("decision") in {"dispatch", "complete"}:
                    return schedule_obs, terminal, decision, total_latency, digest
                if decision.get("decision") == "abort":
                    raise RuntimeError(
                        f"Operation scheduler aborted during {phase_label}: {decision}"
                    )
                previous_outcome = decision
                if attempt == 0:
                    schedule_obs, terminal = _hold_joint_action(
                        env,
                        schedule_obs,
                        current_action,
                        args_cli.retry_steps,
                        gripper_closed=None,
                    )
                    if terminal:
                        raise RuntimeError(
                            "Environment terminated during scheduler retry hold"
                        )
            raise RuntimeError(
                f"Operation scheduler did not dispatch during {phase_label}: "
                f"{previous_outcome}"
            )

        def actuator_transition_handler(
            transition_obs: dict[str, Any],
            current_action: torch.Tensor,
            *,
            phase_label: str,
            observation_prefix: str,
        ) -> tuple[dict[str, Any], bool, dict[str, Any], float, str]:
            """Obtain one admitted actuator call, with one fail-closed retry."""
            nonlocal model_calls
            previous_outcome: dict[str, Any] | None = None
            total_latency = 0.0
            terminal = False
            digest = ""
            for attempt in range(2):
                transition_state = _state(env, initial_banana_z)
                transition_frame = _single_exterior_frame(transition_obs)
                transition_frame, depth_summary = _rgbd_checkpoint_frame(
                    env, transition_frame
                )
                frame_name = (
                    f"{observation_prefix}"
                    f"{'_retry' if attempt else ''}.jpg"
                )
                cv2.imwrite(
                    str(args_cli.artifact_dir / frame_name),
                    cv2.cvtColor(transition_frame, cv2.COLOR_RGB2BGR),
                )
                current_engaged = bool(
                    float(current_action[0, 7].detach().cpu()) > 0.5
                )
                decision, latency, digest = (
                    _choose_observation_bound_actuator_tool(
                        motion_tool_provider,
                        actuator_executor_registry,
                        instruction=args_cli.instruction,
                        observation_prefix=(
                            f"{observation_prefix}-attempt-{attempt + 1}"
                        ),
                        frame=transition_frame,
                        state=transition_state,
                        actuator_context={
                            "phase_label": phase_label,
                            "current_binary_command": (
                                "engaged" if current_engaged else "disengaged"
                            ),
                            "transition_opportunity": True,
                            "previous_tool_outcome": previous_outcome,
                            "executor_candidates": [
                                spec.executor_id
                                for spec in actuator_executor_registry.specs()
                            ],
                        },
                        rgbd_summary=depth_summary,
                        critic_context=_critic_context(
                            critic_memory, phase_label
                        ),
                    )
                )
                total_latency += latency
                model_calls += 1
                digests.append(digest)
                event = {
                    **decision["actuator_tool"],
                    "phase_label": phase_label,
                    "attempt": attempt + 1,
                    "frame": frame_name,
                    "state": transition_state,
                    "latency_s": latency,
                }
                episode_trace["actuator_tool_protocol"]["calls"].append(event)
                _write_trace(trace_path, episode_trace)
                print(
                    f"[ER2 actuator] phase={phase_label} "
                    f"tool={decision['actuator_tool'].get('tool_name')} "
                    f"decision={decision.get('decision')} "
                    f"confidence={decision.get('confidence', 0.0):.2f} "
                    f"latency={latency:.2f}s image={digest}\n"
                    f"      {decision.get('assessment', '')}",
                    flush=True,
                )
                if decision.get("decision") == "execute":
                    return transition_obs, terminal, decision, total_latency, digest
                if decision.get("decision") == "abort":
                    if attempt == 0:
                        previous_outcome = {
                            **decision["actuator_tool"],
                            "status": "confirmation_required",
                            "error": (
                                "abort_actuation terminates the overall task; "
                                "use hold_actuation for recoverable replanning "
                                "or confirm abort on the next fresh observation"
                            ),
                        }
                        transition_obs, terminal = _hold_joint_action(
                            env,
                            transition_obs,
                            current_action,
                            args_cli.retry_steps,
                            gripper_closed=None,
                        )
                        if terminal:
                            raise RuntimeError(
                                "Environment terminated during actuator abort "
                                "confirmation hold"
                            )
                        continue
                    raise RuntimeError(
                        f"Actuator governor aborted during {phase_label}: {decision}"
                    )
                previous_outcome = decision.get("actuator_tool")
                if attempt == 0:
                    transition_obs, terminal = _hold_joint_action(
                        env,
                        transition_obs,
                        current_action,
                        args_cli.retry_steps,
                        gripper_closed=None,
                    )
                    if terminal:
                        raise RuntimeError(
                            "Environment terminated during actuator retry hold"
                        )
            raise RuntimeError(
                f"Actuator governor did not admit a transition during "
                f"{phase_label}: {previous_outcome}"
            )

        stages = []
        for phase, start, end in zip(phase_names, boundaries[:-1], boundaries[1:]):
            stages.append(
                (
                    phase,
                    int(start),
                    int(end),
                    # Retained only for the explicitly requested legacy fixed
                    # replay path. Adaptive execution must not route actuator
                    # opportunities from this recorded value.
                    bool(recorded_actions[start, 7] > 0.5),
                )
            )
        # Placement is a fresh model-issued world-space operation. Its motion
        # seed is the measured current pose, not a locally computed XY/Z target.
        if not args_cli.disable_residual_centering:
            stages.append(
                (
                    "place",
                    len(recorded_actions),
                    len(recorded_actions),
                    True,
                )
            )
        # The source episode reached RoboLab's success predicate while carrying
        # the banana above the plate. Open the gripper visibly to finish the task.
        stages.append(
            (
                "release",
                len(recorded_actions),
                len(recorded_actions),
                False,
            )
        )

        pregrasp_passed = False
        latched_carry_offset: torch.Tensor | None = None
        latched_carry_quaternion: torch.Tensor | None = None
        latched_rgbd_axis_references: dict[str, np.ndarray] = {}
        transport_recovery_count = 0
        placement_completed_during_recovery = False
        episode_trace["recoveries"] = []
        last_action = torch.zeros((1, 8), dtype=torch.float32, device=env.device)
        last_action[0, :7] = torch.as_tensor(
            joint_states[0, :7], dtype=torch.float32, device=env.device
        )
        task_completed_by_scheduler = False
        for stage_index, (
            phase,
            start,
            end,
            legacy_recorded_gripper_closed,
        ) in enumerate(stages, start=1):
            actuator_engaged_at_stage_start = bool(
                float(last_action[0, 7].detach().cpu()) > 0.5
            )
            actuator_decision: dict[str, Any] | None = None
            actuator_execution: dict[str, Any] | None = None
            actuator_latency = 0.0
            actuator_digest: str | None = None
            scheduler_decision: dict[str, Any] | None = None
            scheduler_latency = 0.0
            scheduler_digest: str | None = None
            current = _state(env, initial_banana_z)
            if (
                not args_cli.disable_adaptive_ik
                and phase == "place"
            ):
                nominal = torch.tensor(
                    current["eef_gripper_base_xyz"], dtype=torch.float32
                )
                nominal_quaternion = (
                    latched_carry_quaternion
                    if latched_carry_quaternion is not None
                    else torch.tensor(
                        current["eef_gripper_base_quaternion_wxyz"],
                        dtype=torch.float32,
                    )
                )
                target_source = "current_observation_model_seed"
            elif not args_cli.disable_adaptive_ik and phase != "release":
                banana_xyz = torch.tensor(current["banana_xyz"], dtype=torch.float32)
                if phase in {"lift", "above_plate"} and latched_carry_offset is not None:
                    grasp_xyz = banana_xyz + latched_carry_offset
                    assert latched_carry_quaternion is not None
                    nominal_quaternion = latched_carry_quaternion
                else:
                    grasp_xyz, nominal_quaternion = apply_object_relative_grasp(
                        banana_xyz,
                        torch.tensor(
                            current["banana_quaternion_wxyz"], dtype=torch.float32
                        ),
                        grasp_offset_object,
                        object_to_grasp_quat,
                    )
                nominal = live_phase_target(
                    phase,
                    banana_xyz,
                    torch.tensor(current["plate_xyz"], dtype=torch.float32),
                    grasp_xyz - banana_xyz,
                    eef_xyz=torch.tensor(
                        current["eef_gripper_base_xyz"], dtype=torch.float32
                    ),
                    approach_clearance=args_cli.approach_clearance,
                    lift_clearance=args_cli.lift_clearance,
                    plate_hover_height=args_cli.plate_hover_height,
                )
                target_source = "live_object_pose"
            elif phase == "release":
                nominal = _eef_position(env)
                nominal_quaternion = _eef_quaternion(env)
                target_source = "current_eef_hold"
            else:
                action_index = min(start, len(recorded_actions) - 1)
                nominal = torch.from_numpy(recorded_actions[action_index, :3].copy())
                nominal_quaternion = torch.from_numpy(
                    recorded_actions[action_index, 3:7].copy()
                )
                target_source = "recorded_demonstration"
            frame = _single_exterior_frame(obs)
            stage_depth_summary = None
            if not args_cli.disable_adaptive_ik and phase != "release":
                frame, stage_depth_summary = _rgbd_checkpoint_frame(env, frame)
                if latched_rgbd_axis_references:
                    tracked_axes: dict[str, Any] = {}
                    for object_id, reference_axis in (
                        latched_rgbd_axis_references.items()
                    ):
                        try:
                            tracked_axes[object_id] = _rgbd_object_axis_observation(
                                env,
                                prim_label_fragment=f"/scene/{object_id}",
                                reference_axis=reference_axis,
                            )
                        except ValueError as exc:
                            tracked_axes[object_id] = {
                                "available": False,
                                "error": str(exc),
                            }
                    if stage_depth_summary is None:
                        stage_depth_summary = {}
                    stage_depth_summary["tracked_object_axes"] = tracked_axes
            frame_path = args_cli.artifact_dir / f"{stage_index:02d}_{phase}_before.jpg"
            cv2.imwrite(str(frame_path), cv2.cvtColor(frame, cv2.COLOR_RGB2BGR))
            selected_executor_config: dict[str, Any] = {}
            if not args_cli.disable_adaptive_ik and phase != "release":
                decision, latency, digest = _choose_observation_bound_motion_tool(
                    motion_tool_provider,
                    motion_executor_registry,
                    instruction=args_cli.instruction,
                    observation_prefix=f"stage-{stage_index}",
                    frame=frame,
                    state=current,
                    current_target=nominal,
                    motion_context={
                        "phase_label": phase,
                        "current_target_xyz_m": nominal.tolist(),
                        "current_target_quaternion_wxyz": (
                            nominal_quaternion.tolist()
                        ),
                        "target_seed_semantics": (
                            "The target is the current measured workspace pose; "
                            "select any evidence-grounded correction needed to "
                            "advance the human instruction."
                            if phase == "place"
                            else "runtime-proposed phase target"
                        ),
                        "current_actuator_state": (
                            "engaged"
                            if actuator_engaged_at_stage_start
                            else "disengaged"
                        ),
                        "executor_candidates": [
                            spec.executor_id
                            for spec in motion_executor_registry.specs()
                        ],
                        "lease_condition_sources": {
                            "contact": "sim6_gripper_contact_sensor",
                            "tracked_pose": (
                                "sim_privileged_relative_pose"
                                if latched_carry_offset is not None
                                else None
                            ),
                            "tracked_orientation": (
                                {
                                    object_id: "rgbd_instance_depth_major_axis"
                                    for object_id in latched_rgbd_axis_references
                                }
                                if latched_rgbd_axis_references
                                else {}
                            ),
                            "observed_clearance": (
                                "sim_privileged_object_to_support_height"
                                if phase in {"above_plate", "place"}
                                else None
                            ),
                            "model_polling": (
                                "periodic_or_event"
                                if args_cli.periodic_motion_observations
                                else "event_or_completion_only"
                            ),
                        },
                    },
                    rgbd_summary=stage_depth_summary,
                    critic_context=_critic_context(critic_memory, phase),
                )
                nominal = torch.tensor(decision["target_xyz_m"], dtype=torch.float32)
                selected_executor_config = dict(decision["executor_config"])
                target_source = "model_motion_tool"
                episode_trace["motion_tool_protocol"]["calls"].append(
                    decision["motion_tool"]
                )
                stage_model_called = True
            elif not args_cli.disable_adaptive_ik and phase == "release":
                _, digest = _encode_frame(frame)
                decision = {
                    "decision": "execute",
                    "grasp_ready": True,
                    "confidence": 1.0,
                    "assessment": (
                        "Workspace pose is held; the fresh actuator tool call "
                        "governs the physical transition."
                    ),
                }
                latency = 0.0
                stage_model_called = False
            else:
                decision, latency, digest = coach.reason(
                    _stage_prompt(
                        phase,
                        current,
                        nominal,
                        nominal_quaternion,
                        legacy_recorded_gripper_closed,
                        _critic_context(critic_memory, phase),
                    ),
                        frame,
                    )
                stage_model_called = True
            if stage_model_called:
                model_calls += 1
                digests.append(digest)
            confidence = float(decision.get("confidence", 0.0))
            assessment = str(decision.get("assessment", ""))
            print(
                f"[ER2] phase={phase} decision={decision.get('decision')} "
                f"confidence={confidence:.2f} latency={latency:.2f}s image={digest}\n"
                f"      {assessment}",
                flush=True,
            )

            initial_decision = dict(decision)
            retry_performed = False
            needs_retry = decision.get("decision") != "execute" or (
                phase == "grasp" and not bool(decision.get("grasp_ready"))
            )
            if needs_retry:
                if phase in {"grasp", "lift"} or "motion_tool" in decision:
                    retry_performed = True
                    previous_motion_tool = decision.get("motion_tool")
                    print(
                        f"[coach] {phase} requested retry; holding for a fresh "
                        "observation",
                        flush=True,
                    )
                    obs, terminal = _hold_joint_action(
                        env,
                        obs,
                        last_action,
                        args_cli.retry_steps,
                        gripper_closed=None,
                    )
                    current = _state(env, initial_banana_z)
                    if not args_cli.disable_adaptive_ik:
                        banana_xyz = torch.tensor(
                            current["banana_xyz"], dtype=torch.float32
                        )
                        if phase == "place":
                            nominal = torch.tensor(
                                current["eef_gripper_base_xyz"],
                                dtype=torch.float32,
                            )
                            nominal_quaternion = (
                                latched_carry_quaternion
                                if latched_carry_quaternion is not None
                                else torch.tensor(
                                    current[
                                        "eef_gripper_base_quaternion_wxyz"
                                    ],
                                    dtype=torch.float32,
                                )
                            )
                        elif (
                            phase in {"lift", "above_plate"}
                            and latched_carry_offset is not None
                        ):
                            grasp_xyz = banana_xyz + latched_carry_offset
                            assert latched_carry_quaternion is not None
                            nominal_quaternion = latched_carry_quaternion
                        else:
                            grasp_xyz, nominal_quaternion = apply_object_relative_grasp(
                                banana_xyz,
                                torch.tensor(
                                    current["banana_quaternion_wxyz"], dtype=torch.float32
                                ),
                                grasp_offset_object,
                                object_to_grasp_quat,
                            )
                        if phase != "place":
                            nominal = live_phase_target(
                                phase,
                                banana_xyz,
                                torch.tensor(
                                    current["plate_xyz"], dtype=torch.float32
                                ),
                                grasp_xyz - banana_xyz,
                                eef_xyz=torch.tensor(
                                    current["eef_gripper_base_xyz"],
                                    dtype=torch.float32,
                                ),
                                approach_clearance=args_cli.approach_clearance,
                                lift_clearance=args_cli.lift_clearance,
                                plate_hover_height=args_cli.plate_hover_height,
                            )
                    frame = _single_exterior_frame(obs)
                    retry_depth_summary = None
                    if not args_cli.disable_adaptive_ik:
                        frame, retry_depth_summary = _rgbd_checkpoint_frame(env, frame)
                    decision, latency, digest = (
                        _choose_observation_bound_motion_tool(
                            motion_tool_provider,
                            motion_executor_registry,
                            instruction=args_cli.instruction,
                            observation_prefix=f"stage-{stage_index}-retry",
                            frame=frame,
                            state=current,
                            current_target=nominal,
                            motion_context={
                                "phase_label": phase,
                                "retry": True,
                                "current_target_xyz_m": nominal.tolist(),
                                "current_target_quaternion_wxyz": (
                                    nominal_quaternion.tolist()
                                ),
                                "target_seed_semantics": (
                                    "The target is the current measured workspace "
                                    "pose; correct the rejected proposal using "
                                    "fresh evidence and the human instruction."
                                    if phase == "place"
                                    else "runtime-proposed phase target"
                                ),
                                "current_actuator_state": (
                                    "engaged"
                                    if bool(
                                        float(
                                            last_action[0, 7].detach().cpu()
                                        )
                                        > 0.5
                                    )
                                    else "disengaged"
                                ),
                                "previous_tool_outcome": previous_motion_tool,
                                "lease_condition_sources": {
                                    "contact": "sim6_gripper_contact_sensor",
                                    "tracked_pose": (
                                        "sim_privileged_relative_pose"
                                        if latched_carry_offset is not None
                                        else None
                                    ),
                                    "tracked_orientation": (
                                        {
                                            object_id: "rgbd_instance_depth_major_axis"
                                            for object_id in latched_rgbd_axis_references
                                        }
                                        if latched_rgbd_axis_references
                                        else {}
                                    ),
                                    "observed_clearance": (
                                        "sim_privileged_object_to_support_height"
                                        if phase in {"above_plate", "place"}
                                        else None
                                    ),
                                    "model_polling": (
                                        "periodic_or_event"
                                        if args_cli.periodic_motion_observations
                                        else "event_or_completion_only"
                                    ),
                                },
                            },
                            rgbd_summary=retry_depth_summary,
                            critic_context=_critic_context(critic_memory, phase),
                        )
                    )
                    nominal = torch.tensor(
                        decision["target_xyz_m"], dtype=torch.float32
                    )
                    selected_executor_config = dict(decision["executor_config"])
                    episode_trace["motion_tool_protocol"]["calls"].append(
                        decision["motion_tool"]
                    )
                    model_calls += 1
                    digests.append(digest)
                    confidence = float(decision.get("confidence", 0.0))
                    print(
                        f"[ER2] {phase} retry decision={decision.get('decision')} "
                        f"ready={decision.get('grasp_ready')} confidence={decision.get('confidence')} "
                        f"image={digest}",
                        flush=True,
                    )
                if (
                    decision.get("decision") != "execute"
                    and isinstance(decision.get("motion_tool"), dict)
                    and decision["motion_tool"].get("status") == "rejected"
                ):
                    decision = motion_checkpoint_handler(
                        obs,
                        {
                            "reason": "phase_boundary_motion_tool_rejected",
                            "phase": phase,
                            "iteration": 0,
                            "current_target_xyz_m": nominal.tolist(),
                            "current_target_quaternion_wxyz": (
                                nominal_quaternion.tolist()
                            ),
                            "previous_motion_tool_outcome": decision[
                                "motion_tool"
                            ],
                            "lease_condition_sources": {
                                "contact": "sim6.gripper_contact_sensor",
                                "tracked_pose": (
                                    "sim6.privileged_relative_pose_adapter"
                                    if latched_carry_offset is not None
                                    else None
                                ),
                                "tracked_orientation": (
                                    {
                                        object_id: (
                                            "rgbd.instance_depth_major_axis"
                                        )
                                        for object_id in (
                                            latched_rgbd_axis_references
                                        )
                                    }
                                    if latched_rgbd_axis_references
                                    else {}
                                ),
                                "observed_clearance": (
                                    "sim6.privileged_object_to_support_height_adapter"
                                    if phase in {"above_plate", "place"}
                                    else None
                                ),
                            },
                        },
                    )
                    nominal = torch.tensor(
                        decision["target_xyz_m"], dtype=torch.float32
                    )
                    selected_executor_config = dict(
                        decision.get("executor_config") or {}
                    )
                    confidence = float(decision.get("confidence", 0.0))
                if (
                    decision.get("decision") == "retry"
                    and isinstance(decision.get("motion_tool"), dict)
                    and decision["motion_tool"].get("action") == "hold"
                ):
                    boundary_hold = decision
                    (
                        obs,
                        terminal,
                        boundary_schedule,
                        boundary_scheduler_latency,
                        boundary_scheduler_digest,
                    ) = operation_scheduler_handler(
                        obs,
                        last_action,
                        phase_label=f"{phase}:boundary_hold",
                        observation_prefix=(
                            f"scheduler_boundary_hold_{stage_index:02d}_{phase}"
                        ),
                        motion_report={
                            "converged": False,
                            "yielded_to_scheduler": True,
                            "recovery_request": {
                                "reason": "model_requested_hold",
                                "coach_decision": boundary_hold,
                            },
                        },
                        trigger_event={
                            "type": "phase_boundary_model_hold",
                            "phase_label": phase,
                            "model_decision": boundary_hold,
                        },
                    )
                    scheduler_decision = boundary_schedule
                    scheduler_latency += boundary_scheduler_latency
                    scheduler_digest = boundary_scheduler_digest
                    if boundary_schedule.get("decision") == "complete":
                        task_completed_by_scheduler = True
                    elif boundary_schedule.get("operation_kind") == "actuation":
                        (
                            obs,
                            terminal,
                            boundary_actuator_decision,
                            boundary_actuator_latency,
                            boundary_actuator_digest,
                        ) = actuator_transition_handler(
                            obs,
                            last_action,
                            phase_label=f"{phase}:boundary_hold",
                            observation_prefix=(
                                f"actuator_boundary_hold_{stage_index:02d}_{phase}"
                            ),
                        )
                        (
                            obs,
                            terminal,
                            last_action,
                            boundary_actuator_execution,
                        ) = _execute_binary_actuator_tool(
                            env,
                            obs,
                            last_action,
                            boundary_actuator_decision,
                            initial_banana_z=initial_banana_z,
                        )
                        episode_trace["actuator_tool_protocol"]["calls"][-1][
                            "execution"
                        ] = boundary_actuator_execution
                        actuator_decision = boundary_actuator_decision
                        actuator_execution = boundary_actuator_execution
                        actuator_latency += boundary_actuator_latency
                        actuator_digest = boundary_actuator_digest
                    if not task_completed_by_scheduler:
                        decision = motion_checkpoint_handler(
                            obs,
                            {
                                "reason": "scheduler_requested_boundary_replan",
                                "phase": phase,
                                "iteration": 0,
                                "current_target_xyz_m": (
                                    _eef_position(env).tolist()
                                ),
                                "current_target_quaternion_wxyz": (
                                    _eef_quaternion(env).tolist()
                                ),
                                "previous_motion_tool_outcome": boundary_hold[
                                    "motion_tool"
                                ],
                                "scheduler_decision": boundary_schedule,
                                "lease_condition_sources": {
                                    "contact": "sim6.gripper_contact_sensor",
                                    "tracked_pose": (
                                        "sim6.privileged_relative_pose_adapter"
                                        if latched_carry_offset is not None
                                        else None
                                    ),
                                    "tracked_orientation": (
                                        {
                                            object_id: (
                                                "rgbd.instance_depth_major_axis"
                                            )
                                            for object_id in (
                                                latched_rgbd_axis_references
                                            )
                                        }
                                        if latched_rgbd_axis_references
                                        else {}
                                    ),
                                    "observed_clearance": (
                                        "sim6.privileged_object_to_support_height_adapter"
                                        if phase in {"above_plate", "place"}
                                        else None
                                    ),
                                },
                            },
                        )
                        nominal = torch.tensor(
                            decision["target_xyz_m"], dtype=torch.float32
                        )
                        nominal_quaternion = _eef_quaternion(env)
                        selected_executor_config = dict(
                            decision.get("executor_config") or {}
                        )
                        confidence = float(decision.get("confidence", 0.0))
                if decision.get("decision") != "execute" or (
                    phase == "grasp" and not bool(decision.get("grasp_ready"))
                ):
                    raise RuntimeError(f"ER 2 stopped at phase {phase}: {decision}")

            if phase == "grasp":
                unique_images = len(set(digests))
                eef_motion = float(
                    torch.linalg.norm(
                        torch.tensor(eef_trace[-1]) - torch.tensor(eef_trace[0])
                    )
                )
                feedback_ok = unique_images >= 2 and eef_motion >= 0.02
                tests["feedback"] = feedback_ok
                _test_line(
                    4,
                    "fresh observation after phases",
                    feedback_ok,
                    f"unique_images={unique_images}/{len(digests)} eef_motion={eef_motion:.3f}m",
                )
                base_distance = float(torch.linalg.norm(_eef_position(env) - nominal))
                fingertip_distance = float(
                    torch.linalg.norm(
                        _eef_position(env)
                        + torch.tensor([0.0, 0.0, -GRIPPER_BASE_TO_FINGERTIP_M])
                        - _local_position(env, "banana")
                    )
                )
                pregrasp_passed = (
                    bool(decision.get("grasp_ready"))
                    and confidence >= 0.5
                    and base_distance <= 0.08
                    and fingertip_distance <= 0.05
                )
                tests["pregrasp"] = pregrasp_passed
                _test_line(
                    5,
                    "fresh visual pre-grasp gate",
                    pregrasp_passed,
                    f"ready={decision.get('grasp_ready')} confidence={confidence:.2f} "
                    f"base_target={base_distance:.3f}m fingertip_banana={fingertip_distance:.3f}m",
                )
                if not pregrasp_passed:
                    raise RuntimeError("Visual/metric pre-grasp gate rejected gripper closure")

            visualize_axes(
                nominal + env.scene.env_origins[0].detach().cpu(),
                nominal_quaternion,
                "gemini_er2_target",
                axis_length=0.12,
            )
            print(
                f"[executor] {phase}: source={target_source} "
                f"target={nominal.tolist()} actuator_current="
                f"{'engaged' if bool(float(last_action[0, 7].detach().cpu()) > 0.5) else 'disengaged'}",
                flush=True,
            )
            motion_report: dict[str, Any]
            if phase == "release":
                motion_report = {
                    "enabled": True,
                    "executor": "current_workspace_pose_hold",
                    "target_source": target_source,
                    "converged": not terminal,
                }
            elif not args_cli.disable_adaptive_ik:
                seed_steps = 0
                if phase == "approach_banana":
                    # Establish the demonstrated downward grasp orientation at
                    # a safe hover height before translating to a moved banana.
                    obs, terminal, last_action = _run_joint_segment(
                        env, obs, joint_states, recorded_actions, start, end
                    )
                    seed_steps = end - start
                if terminal:
                    raise RuntimeError(f"Environment terminated during {phase} seed motion")
                motion_attempts: list[dict[str, Any]] = []
                while True:
                    obs, terminal, last_action, attempt_report = _move_eef_to_target(
                        env,
                        obs,
                        last_action,
                        nominal,
                        nominal_quaternion,
                        phase,
                        gripper_closed=bool(
                            float(last_action[0, 7].detach().cpu()) > 0.5
                        ),
                        initial_banana_z=initial_banana_z,
                        executor_config=selected_executor_config,
                        carry_reference_offset=latched_carry_offset,
                        rgbd_axis_references=latched_rgbd_axis_references,
                        checkpoint_callback=motion_checkpoint_handler,
                    )
                    motion_attempts.append(attempt_report)
                    if not bool(attempt_report.get("recovery_requested")):
                        break
                    if motion_report_yields_to_scheduler(attempt_report):
                        attempt_report["yielded_to_scheduler"] = True
                        attempt_report["yield_reason"] = (
                            "model_hold_requires_fresh_operation_selection"
                        )
                        attempt_report["recovery_requested"] = False
                        attempt_report["converged"] = True
                        print(
                            f"[executor handoff] {phase}: model hold yielded "
                            "to the fresh operation scheduler",
                            flush=True,
                        )
                        break
                    if phase != "above_plate":
                        raise RuntimeError(
                            f"Motion recovery replan budget exhausted in {phase}: "
                            f"{attempt_report.get('recovery_request')}"
                        )
                    if transport_recovery_count >= args_cli.max_transport_recoveries:
                        raise RuntimeError(
                            "Transport recovery budget exhausted: "
                            f"{transport_recovery_count}/{args_cli.max_transport_recoveries}"
                        )
                    transport_recovery_count += 1
                    print(
                        f"[recovery] starting bounded transport recovery "
                        f"{transport_recovery_count}/{args_cli.max_transport_recoveries}",
                        flush=True,
                    )
                    (
                        obs,
                        terminal,
                        last_action,
                        latched_carry_offset,
                        latched_carry_quaternion,
                        recovery_report,
                    ) = _recover_transport_grasp(
                        env,
                        obs,
                        last_action,
                        initial_banana_z=initial_banana_z,
                        grasp_offset_object=grasp_offset_object,
                        object_to_grasp_quat=object_to_grasp_quat,
                        checkpoint_callback=motion_checkpoint_handler,
                    )
                    recovery_event = {
                        "index": transport_recovery_count,
                        "trigger": attempt_report.get("recovery_request"),
                        **recovery_report,
                    }
                    episode_trace["recoveries"].append(recovery_event)
                    _write_trace(trace_path, episode_trace)
                    if bool(recovery_report.get("goal_completed")):
                        placement_completed_during_recovery = True
                        print(
                            f"[recovery] completed task with outcome="
                            f"{recovery_report.get('outcome')}; skipping further "
                            "transport/centering",
                            flush=True,
                        )
                        break
                    print(
                        f"[recovery] completed with outcome="
                        f"{recovery_report.get('outcome')}; resuming above_plate",
                        flush=True,
                    )
                    resumed_state = _state(env, initial_banana_z)
                    banana_xyz = torch.tensor(
                        resumed_state["banana_xyz"], dtype=torch.float32
                    )
                    plate_xyz = torch.tensor(
                        resumed_state["plate_xyz"], dtype=torch.float32
                    )
                    nominal = live_phase_target(
                        "above_plate",
                        banana_xyz,
                        plate_xyz,
                        latched_carry_offset,
                        eef_xyz=torch.tensor(
                            resumed_state["eef_gripper_base_xyz"],
                            dtype=torch.float32,
                        ),
                        approach_clearance=args_cli.approach_clearance,
                        lift_clearance=args_cli.lift_clearance,
                        plate_hover_height=args_cli.plate_hover_height,
                    )
                    nominal_quaternion = latched_carry_quaternion
                if placement_completed_during_recovery:
                    orientation_error = torch.linalg.vector_norm(
                        quaternion_error_axis_angle_wxyz(
                            nominal_quaternion, _eef_quaternion(env)
                        )
                    )
                    motion_report = {
                        "enabled": True,
                        "phase": phase,
                        "executor": "bounded_recovery_goal_completion",
                        "attempts": motion_attempts,
                        "recovery_count": transport_recovery_count,
                        "goal_completed_during_recovery": True,
                        "target_xyz": nominal.tolist(),
                        "target_quaternion_wxyz": nominal_quaternion.tolist(),
                        "target_error_after_m": float(
                            torch.linalg.vector_norm(nominal - _eef_position(env))
                        ),
                        "orientation_error_after_deg": float(
                            orientation_error * 180.0 / np.pi
                        ),
                        "converged": True,
                    }
                elif len(motion_attempts) == 1:
                    motion_report = motion_attempts[0]
                else:
                    motion_report = {
                        "enabled": True,
                        "phase": phase,
                        "executor": "bounded_recovery_wrapped_live_pose_ik",
                        "attempts": motion_attempts,
                        "recovery_count": len(motion_attempts) - 1,
                        "target_xyz": nominal.tolist(),
                        "target_quaternion_wxyz": nominal_quaternion.tolist(),
                        "target_error_after_m": motion_attempts[-1][
                            "target_error_after_m"
                        ],
                        "orientation_error_after_deg": motion_attempts[-1][
                            "orientation_error_after_deg"
                        ],
                        "converged": motion_attempts[-1]["converged"],
                    }
                motion_report["demonstration_orientation_seed_steps"] = seed_steps
                if not bool(motion_report["converged"]):
                    raise RuntimeError(
                        f"Adaptive IK did not reach the live {phase} target: "
                        f"error={motion_report['target_error_after_m']:.4f} m"
                    )
            else:
                obs, terminal, last_action = _run_joint_segment(
                    env, obs, joint_states, recorded_actions, start, end
                )
                motion_report = {
                    "enabled": False,
                    "executor": "fixed_demonstration_replay",
                    "target_source": target_source,
                    "demonstrated_steps": end - start,
                    "converged": not terminal,
                }
                if phase == "above_plate" and not terminal:
                    # The successful source episode terminated only eight steps
                    # into transport. Sim 6 needs a short settling hold for the
                    # same final demonstrated joint target to converge before
                    # ER 2 decides whether release is safe.
                    obs, terminal = _hold_joint_action(
                        env,
                        obs,
                        last_action,
                        args_cli.retry_steps,
                        gripper_closed=True,
                    )
            actuator_engaged_before_transition = bool(
                float(last_action[0, 7].detach().cpu()) > 0.5
            )
            if not args_cli.disable_adaptive_ik:
                (
                    obs,
                    terminal,
                    scheduler_decision,
                    scheduler_latency,
                    scheduler_digest,
                ) = operation_scheduler_handler(
                    obs,
                    last_action,
                    phase_label=phase,
                    observation_prefix=(
                        f"scheduler_stage_{stage_index:02d}_{phase}"
                    ),
                    motion_report=motion_report,
                )
                if scheduler_decision.get("decision") == "complete":
                    task_completed_by_scheduler = True
                    motion_report["scheduler_declared_task_complete"] = True
                elif scheduler_decision.get("operation_kind") not in {
                    "motion",
                    "actuation",
                }:
                    raise RuntimeError(
                        f"Scheduler returned unsupported operation during {phase}: "
                        f"{scheduler_decision}"
                    )
            if (
                scheduler_decision is not None
                and scheduler_decision.get("operation_kind") == "actuation"
            ):
                (
                    obs,
                    terminal,
                    actuator_decision,
                    actuator_latency,
                    actuator_digest,
                ) = actuator_transition_handler(
                    obs,
                    last_action,
                    phase_label=phase,
                    observation_prefix=(
                        f"actuator_stage_{stage_index:02d}_{phase}"
                    ),
                )
                obs, terminal, last_action, actuator_execution = (
                    _execute_binary_actuator_tool(
                        env,
                        obs,
                        last_action,
                        actuator_decision,
                        initial_banana_z=initial_banana_z,
                    )
                )
                episode_trace["actuator_tool_protocol"]["calls"][-1][
                    "execution"
                ] = actuator_execution
                motion_report["actuator_execution"] = actuator_execution
                print(
                    f"[actuator executor] phase={phase} "
                    f"state={actuator_execution['requested_state']} "
                    f"settle_steps={actuator_execution['settle_steps']} "
                    f"touch_after="
                    f"{actuator_execution['state_after']['current_contact']['touch']}",
                    flush=True,
                )
                if terminal:
                    raise RuntimeError(
                        f"Environment terminated during {phase} actuator transition"
                    )
                post_feedback_decisions: list[dict[str, Any]] = []
                post_feedback_executions: list[dict[str, Any]] = []
                current_actuator_execution = actuator_execution
                for feedback_index in range(3):
                    feedback_event = _actuator_feedback_event_from_execution(
                        current_actuator_execution,
                        actuator_feedback_policy,
                    )
                    current_actuator_execution["feedback_event"] = feedback_event
                    _write_trace(trace_path, episode_trace)
                    print(
                        f"[post-actuation event] phase={phase} "
                        f"triggered={feedback_event['triggered']} "
                        f"position_delta="
                        f"{feedback_event['actuator_position_change']:.3f} "
                        f"force_delta="
                        f"{feedback_event['tactile_force_change_n']:.3f}N "
                        f"touch_changed={feedback_event['touch_changed']}",
                        flush=True,
                    )
                    if not feedback_event["triggered"]:
                        break
                    (
                        obs,
                        terminal,
                        post_feedback_decision,
                        post_feedback_latency,
                        post_feedback_digest,
                    ) = operation_scheduler_handler(
                        obs,
                        last_action,
                        phase_label=f"{phase}:post_actuation",
                        observation_prefix=(
                            f"scheduler_post_actuator_{stage_index:02d}_{phase}_"
                            f"{feedback_index + 1}"
                        ),
                        motion_report=motion_report,
                        trigger_event={
                            "type": "actuator_and_tactile_state_changed",
                            **feedback_event,
                        },
                    )
                    post_feedback_decisions.append(post_feedback_decision)
                    scheduler_latency += post_feedback_latency
                    scheduler_digest = post_feedback_digest
                    if post_feedback_decision.get("decision") == "complete":
                        task_completed_by_scheduler = True
                        motion_report["scheduler_declared_task_complete"] = True
                        break
                    if post_feedback_decision.get("operation_kind") == "motion":
                        break
                    if post_feedback_decision.get("operation_kind") != "actuation":
                        raise RuntimeError(
                            "Post-actuation scheduler returned unsupported "
                            f"operation: {post_feedback_decision}"
                        )
                    (
                        obs,
                        terminal,
                        repeated_actuator_decision,
                        repeated_actuator_latency,
                        actuator_digest,
                    ) = actuator_transition_handler(
                        obs,
                        last_action,
                        phase_label=f"{phase}:post_actuation",
                        observation_prefix=(
                            f"actuator_post_feedback_{stage_index:02d}_{phase}_"
                            f"{feedback_index + 1}"
                        ),
                    )
                    actuator_latency += repeated_actuator_latency
                    (
                        obs,
                        terminal,
                        last_action,
                        repeated_execution,
                    ) = _execute_binary_actuator_tool(
                        env,
                        obs,
                        last_action,
                        repeated_actuator_decision,
                        initial_banana_z=initial_banana_z,
                    )
                    episode_trace["actuator_tool_protocol"]["calls"][-1][
                        "execution"
                    ] = repeated_execution
                    post_feedback_executions.append(repeated_execution)
                    current_actuator_execution = repeated_execution
                    if terminal:
                        raise RuntimeError(
                            "Environment terminated during post-feedback actuation"
                        )
                else:
                    raise RuntimeError(
                        "Post-actuation feedback reschedule budget exhausted"
                    )
                actuator_execution["post_feedback_scheduler_decisions"] = (
                    post_feedback_decisions
                )
                actuator_execution["post_feedback_executions"] = (
                    post_feedback_executions
                )
                motion_report["post_actuation_scheduler_decisions"] = (
                    post_feedback_decisions
                )
                _write_trace(trace_path, episode_trace)
                if (
                    not actuator_engaged_before_transition
                    and bool(float(last_action[0, 7].detach().cpu()) > 0.5)
                ):
                    # Preserve the fresh measured carry transform after the
                    # admitted actuator command, not before contact.
                    latched_carry_offset = (
                        _eef_position(env) - _local_position(env, "banana")
                    )
                    latched_carry_quaternion = _eef_quaternion(env)
                    motion_report["latched_carry_offset_m"] = (
                        latched_carry_offset.tolist()
                    )
                    motion_report["latched_carry_quaternion_wxyz"] = (
                        latched_carry_quaternion.tolist()
                    )
                    latched_rgbd_axis_references = {}
                    rgbd_reference_observations: dict[str, Any] = {}
                    for object_id in trackable_object_ids:
                        try:
                            rgbd_reference = _rgbd_object_axis_observation(
                                env,
                                prim_label_fragment=f"/scene/{object_id}",
                            )
                            latched_rgbd_axis_references[object_id] = np.asarray(
                                rgbd_reference["major_axis_camera"],
                                dtype=np.float64,
                            )
                            rgbd_reference_observations[object_id] = rgbd_reference
                        except ValueError as exc:
                            rgbd_reference_observations[object_id] = {
                                "available": False,
                                "error": str(exc),
                            }
                    motion_report["latched_rgbd_axis_references"] = (
                        rgbd_reference_observations
                    )
                    print(
                        "[rgbd-tracker] latched observable object axes="
                        f"{sorted(latched_rgbd_axis_references)}",
                        flush=True,
                    )
            eef = _eef_position(env)
            eef_trace.append(eef.tolist())
            pos_error = float(torch.linalg.norm(eef - nominal))
            banana_now = _local_position(env, "banana")
            print(
                f"[feedback] {phase}: eef={eef.tolist()} error={pos_error:.4f}m "
                f"banana={banana_now.tolist()} terminal={terminal}",
                flush=True,
            )
            phase_contact_summary = _active_contact_summary()
            if phase in {"grasp", "lift", "release"}:
                print(
                    f"[contact] after={phase} "
                    f"coverage={phase_contact_summary['coverage']:.3f} "
                    f"touch_samples={phase_contact_summary['touch_samples']} "
                    f"peak_net_force={phase_contact_summary['peak_net_force_n']:.3f}N",
                    flush=True,
                )
            episode_trace["stages"].append({
                "phase": phase,
                "frame": frame_path.name,
                "initial_coach_decision": initial_decision,
                "coach_decision": decision,
                "retry_performed": retry_performed,
                "coach_latency_s": latency,
                "state_before": current,
                "nominal_target_xyz": nominal.tolist(),
                "nominal_target_quaternion_wxyz": nominal_quaternion.tolist(),
                "target_source": target_source,
                "demonstrated_steps": end - start,
                "motion_report": motion_report,
                "recorded_actuator_hint_used": bool(
                    args_cli.disable_adaptive_ik
                ),
                "operation_scheduler_decision": scheduler_decision,
                "operation_scheduler_latency_s": scheduler_latency,
                "operation_scheduler_image_digest": scheduler_digest,
                "actuator_state_at_stage_start": (
                    "engaged"
                    if actuator_engaged_at_stage_start
                    else "disengaged"
                ),
                "actuator_tool_decision": actuator_decision,
                "actuator_tool_latency_s": actuator_latency,
                "actuator_tool_image_digest": actuator_digest,
                "actuator_execution": actuator_execution,
                "eef_after_xyz": eef.tolist(),
                "eef_target_error_m": pos_error,
                "banana_after_xyz": banana_now.tolist(),
                "terminal": terminal,
                "contact_telemetry_after": phase_contact_summary,
            })
            _write_trace(trace_path, episode_trace)

            if phase == "above_plate" and not terminal:
                if placement_completed_during_recovery:
                    completed_state = _state(env, initial_banana_z)
                    centering_report = {
                        "enabled": False,
                        "converged": True,
                        "reason": "goal_completed_during_transport_recovery",
                        "banana_plate_contact_proxy": completed_state[
                            "banana_plate_contact_proxy"
                        ],
                        "xy_error_after_m": completed_state[
                            "banana_plate_xy_error_m"
                        ],
                        "height_above_plate_after_m": completed_state[
                            "banana_height_above_plate_m"
                        ],
                        "iterations": [],
                    }
                    tests["centering"] = True
                    _test_line(
                        7,
                        "bounded residual plate centering",
                        True,
                        "goal completed during supervised recovery set-down",
                    )
                elif args_cli.disable_residual_centering:
                    centering_report = {
                        "enabled": False,
                        "converged": None,
                        "reason": "baseline mode",
                    }
                    tests["centering"] = True
                    _test_line(7, "residual plate centering", True, "disabled (baseline mode)")
                else:
                    centering_report = {
                        "enabled": True,
                        "converged": None,
                        "reason": (
                            "pending_fresh_observation_bound_place_operation"
                        ),
                        "legacy_local_xy_z_controller_used": False,
                    }
                episode_trace["residual_centering"] = centering_report
                _write_trace(trace_path, episode_trace)

            if phase == "place" and not terminal:
                placement_state = _state(env, initial_banana_z)
                centered = bool(
                    placement_state["banana_plate_contact_proxy"]
                    or (
                        placement_state["banana_plate_xy_error_m"]
                        <= args_cli.center_tolerance
                        and abs(
                            placement_state["banana_height_above_plate_m"]
                            - args_cli.release_height
                        )
                        <= args_cli.release_height_tolerance
                    )
                )
                placement_outcome_recoveries: list[dict[str, Any]] = []
                for recovery_index in range(args_cli.max_transport_recoveries):
                    if centered or terminal:
                        break
                    trigger_event = {
                        "type": "measured_stage_outcome_not_met",
                        "predicate_id": (
                            "object.target_contact_or_release_envelope"
                        ),
                        "observed_values": {
                            "target_contact": placement_state[
                                "banana_plate_contact_proxy"
                            ],
                            "target_xy_error_m": placement_state[
                                "banana_plate_xy_error_m"
                            ],
                            "height_above_target_m": placement_state[
                                "banana_height_above_plate_m"
                            ],
                        },
                        "admission_values": {
                            "maximum_target_xy_error_m": (
                                args_cli.center_tolerance
                            ),
                            "target_release_height_m": args_cli.release_height,
                            "release_height_tolerance_m": (
                                args_cli.release_height_tolerance
                            ),
                        },
                        "instruction": args_cli.instruction,
                    }
                    print(
                        "[outcome recovery] measured placement is outside the "
                        f"release envelope (xy="
                        f"{placement_state['banana_plate_xy_error_m']:.3f}m, "
                        f"height="
                        f"{placement_state['banana_height_above_plate_m']:.3f}m); "
                        f"requesting fresh operation {recovery_index + 1}/"
                        f"{args_cli.max_transport_recoveries}",
                        flush=True,
                    )
                    (
                        obs,
                        terminal,
                        recovery_schedule,
                        recovery_scheduler_latency,
                        recovery_scheduler_digest,
                    ) = operation_scheduler_handler(
                        obs,
                        last_action,
                        phase_label="place:measured_outcome_not_met",
                        observation_prefix=(
                            f"scheduler_outcome_{stage_index:02d}_place_"
                            f"{recovery_index + 1}"
                        ),
                        motion_report={
                            "converged": False,
                            "yielded_to_scheduler": True,
                            "recovery_request": trigger_event,
                        },
                        trigger_event=trigger_event,
                    )
                    scheduler_latency += recovery_scheduler_latency
                    scheduler_digest = recovery_scheduler_digest
                    recovery_event: dict[str, Any] = {
                        "kind": "measured_stage_outcome_recovery",
                        "index": recovery_index + 1,
                        "phase": phase,
                        "trigger": trigger_event,
                        "state_before": placement_state,
                        "scheduler_decision": recovery_schedule,
                    }
                    if recovery_schedule.get("decision") == "complete":
                        recovery_event["completion_admitted"] = False
                        recovery_event["completion_rejection_reason"] = (
                            "measured outcome predicate remains false"
                        )
                    elif recovery_schedule.get("operation_kind") == "actuation":
                        (
                            obs,
                            terminal,
                            recovery_actuator_decision,
                            recovery_actuator_latency,
                            recovery_actuator_digest,
                        ) = actuator_transition_handler(
                            obs,
                            last_action,
                            phase_label="place:measured_outcome_not_met",
                            observation_prefix=(
                                f"actuator_outcome_{stage_index:02d}_place_"
                                f"{recovery_index + 1}"
                            ),
                        )
                        (
                            obs,
                            terminal,
                            last_action,
                            recovery_actuator_execution,
                        ) = _execute_binary_actuator_tool(
                            env,
                            obs,
                            last_action,
                            recovery_actuator_decision,
                            initial_banana_z=initial_banana_z,
                        )
                        episode_trace["actuator_tool_protocol"]["calls"][-1][
                            "execution"
                        ] = recovery_actuator_execution
                        actuator_latency += recovery_actuator_latency
                        actuator_digest = recovery_actuator_digest
                        actuator_decision = recovery_actuator_decision
                        actuator_execution = recovery_actuator_execution
                        recovery_event["actuator_decision"] = (
                            recovery_actuator_decision
                        )
                        recovery_event["actuator_execution"] = (
                            recovery_actuator_execution
                        )
                        actuator_state_after = recovery_actuator_execution[
                            "state_after"
                        ]
                        if (
                            recovery_actuator_execution["engaged_after"]
                            and not recovery_actuator_execution["engaged_before"]
                            and actuator_state_after["current_contact"].get(
                                "touch"
                            )
                        ):
                            latched_carry_offset = (
                                _eef_position(env)
                                - _local_position(env, "banana")
                            )
                            latched_carry_quaternion = _eef_quaternion(env)
                            latched_rgbd_axis_references = {}
                            for object_id in trackable_object_ids:
                                try:
                                    axis_observation = (
                                        _rgbd_object_axis_observation(
                                            env,
                                            prim_label_fragment=(
                                                f"/scene/{object_id}"
                                            ),
                                        )
                                    )
                                    latched_rgbd_axis_references[object_id] = (
                                        np.asarray(
                                            axis_observation[
                                                "major_axis_camera"
                                            ],
                                            dtype=np.float64,
                                        )
                                    )
                                except ValueError:
                                    continue
                            recovery_event["carry_latched_after_actuation"] = {
                                "eef_minus_object_m": (
                                    latched_carry_offset.tolist()
                                ),
                                "tracked_rgbd_objects": sorted(
                                    latched_rgbd_axis_references
                                ),
                            }
                    elif recovery_schedule.get("operation_kind") == "motion":
                        recovery_contact = placement_state.get(
                            "current_contact", {}
                        )
                        carry_observed = bool(
                            float(last_action[0, 7].detach().cpu()) > 0.5
                            and isinstance(recovery_contact, dict)
                            and recovery_contact.get("touch")
                            and latched_carry_offset is not None
                        )
                        recovery_motion_decision = motion_checkpoint_handler(
                            obs,
                            {
                                "reason": "measured_stage_outcome_not_met",
                                "phase": "place:outcome_recovery",
                                "iteration": recovery_index + 1,
                                "current_target_xyz_m": (
                                    _eef_position(env).tolist()
                                ),
                                "current_target_quaternion_wxyz": (
                                    _eef_quaternion(env).tolist()
                                ),
                                "scheduler_decision": recovery_schedule,
                                "measured_outcome": trigger_event,
                                "lease_condition_sources": {
                                    "contact": "sim6.gripper_contact_sensor",
                                    "tracked_pose": (
                                        "sim6.privileged_relative_pose_adapter"
                                        if carry_observed
                                        else None
                                    ),
                                    "tracked_orientation": (
                                        {
                                            object_id: (
                                                "rgbd.instance_depth_major_axis"
                                            )
                                            for object_id in (
                                                latched_rgbd_axis_references
                                            )
                                        }
                                        if carry_observed
                                        else {}
                                    ),
                                    "observed_clearance": (
                                        "sim6.privileged_object_to_support_height_adapter"
                                        if carry_observed
                                        else None
                                    ),
                                },
                            },
                        )
                        recovery_event["motion_decision"] = (
                            recovery_motion_decision
                        )
                        if recovery_motion_decision.get("decision") != "execute":
                            raise RuntimeError(
                                "Model did not admit a bounded recovery motion "
                                f"after measured placement failure: "
                                f"{recovery_motion_decision}"
                            )
                        recovery_target = torch.tensor(
                            recovery_motion_decision["target_xyz_m"],
                            dtype=torch.float32,
                        )
                        (
                            obs,
                            terminal,
                            last_action,
                            recovery_motion_report,
                        ) = _move_eef_to_target(
                            env,
                            obs,
                            last_action,
                            recovery_target,
                            _eef_quaternion(env),
                            "place:outcome_recovery",
                            gripper_closed=bool(
                                float(last_action[0, 7].detach().cpu()) > 0.5
                            ),
                            initial_banana_z=initial_banana_z,
                            executor_config=dict(
                                recovery_motion_decision.get(
                                    "executor_config"
                                )
                                or {}
                            ),
                            carry_reference_offset=(
                                latched_carry_offset if carry_observed else None
                            ),
                            rgbd_axis_references=(
                                latched_rgbd_axis_references
                                if carry_observed
                                else {}
                            ),
                            checkpoint_callback=motion_checkpoint_handler,
                        )
                        recovery_event["motion_report"] = recovery_motion_report
                    else:
                        raise RuntimeError(
                            "Placement outcome scheduler returned unsupported "
                            f"operation: {recovery_schedule}"
                        )
                    placement_state = _state(env, initial_banana_z)
                    recovery_event["state_after"] = placement_state
                    placement_outcome_recoveries.append(recovery_event)
                    episode_trace["recoveries"].append(recovery_event)
                    centered = bool(
                        placement_state["banana_plate_contact_proxy"]
                        or (
                            placement_state["banana_plate_xy_error_m"]
                            <= args_cli.center_tolerance
                            and abs(
                                placement_state[
                                    "banana_height_above_plate_m"
                                ]
                                - args_cli.release_height
                            )
                            <= args_cli.release_height_tolerance
                        )
                    )
                    _write_trace(trace_path, episode_trace)
                centering_report = {
                    "enabled": True,
                    "controller": "observation_bound_model_motion_tool",
                    "legacy_local_xy_z_controller_used": False,
                    "converged": centered,
                    "banana_plate_contact_proxy": placement_state[
                        "banana_plate_contact_proxy"
                    ],
                    "xy_error_after_m": placement_state[
                        "banana_plate_xy_error_m"
                    ],
                    "height_above_plate_after_m": placement_state[
                        "banana_height_above_plate_m"
                    ],
                    "motion_report": motion_report,
                    "measured_outcome_recoveries": (
                        placement_outcome_recoveries
                    ),
                }
                episode_trace["stages"][-1]["measured_outcome_recoveries"] = (
                    placement_outcome_recoveries
                )
                episode_trace["stages"][-1]["eef_after_xyz"] = (
                    _eef_position(env).tolist()
                )
                episode_trace["stages"][-1]["banana_after_xyz"] = (
                    _local_position(env, "banana").tolist()
                )
                episode_trace["residual_centering"] = centering_report
                tests["centering"] = centered
                _test_line(
                    7,
                    "model-governed plate placement",
                    centered,
                    f"xy_error={placement_state['banana_plate_xy_error_m']:.3f}m "
                    f"height={placement_state['banana_height_above_plate_m']:.3f}m "
                    f"contact={placement_state['banana_plate_contact_proxy']}",
                )
                _write_trace(trace_path, episode_trace)
                if not centered:
                    raise RuntimeError(
                        "Measured placement recovery budget exhausted without "
                        "satisfying the runtime release predicate"
                    )

            if phase == "lift":
                lift_outcome_recoveries: list[dict[str, Any]] = []
                lifted = (
                    float(banana_now[2]) - initial_banana_z
                    >= args_cli.minimum_transport_lift
                )
                for recovery_index in range(args_cli.max_transport_recoveries):
                    if lifted or terminal:
                        break
                    recovery_state_before = _state(env, initial_banana_z)
                    observed_lift_m = float(
                        recovery_state_before["banana_lift_m"]
                    )
                    trigger_event = {
                        "type": "measured_stage_outcome_not_met",
                        "predicate_id": "object.lift_above_minimum",
                        "observed_value_m": observed_lift_m,
                        "minimum_value_m": args_cli.minimum_transport_lift,
                        "instruction": args_cli.instruction,
                    }
                    print(
                        "[outcome recovery] measured object lift "
                        f"{observed_lift_m:.3f}m is below "
                        f"{args_cli.minimum_transport_lift:.3f}m; requesting "
                        f"fresh operation {recovery_index + 1}/"
                        f"{args_cli.max_transport_recoveries}",
                        flush=True,
                    )
                    (
                        obs,
                        terminal,
                        recovery_schedule,
                        recovery_scheduler_latency,
                        recovery_scheduler_digest,
                    ) = operation_scheduler_handler(
                        obs,
                        last_action,
                        phase_label="lift:measured_outcome_not_met",
                        observation_prefix=(
                            f"scheduler_outcome_{stage_index:02d}_lift_"
                            f"{recovery_index + 1}"
                        ),
                        motion_report={
                            "converged": False,
                            "yielded_to_scheduler": True,
                            "recovery_request": trigger_event,
                        },
                        trigger_event=trigger_event,
                    )
                    scheduler_latency += recovery_scheduler_latency
                    scheduler_digest = recovery_scheduler_digest
                    recovery_event: dict[str, Any] = {
                        "kind": "measured_stage_outcome_recovery",
                        "index": recovery_index + 1,
                        "phase": phase,
                        "trigger": trigger_event,
                        "state_before": recovery_state_before,
                        "scheduler_decision": recovery_schedule,
                    }
                    if recovery_schedule.get("decision") == "complete":
                        recovery_event["completion_admitted"] = False
                        recovery_event["completion_rejection_reason"] = (
                            "measured outcome predicate remains false"
                        )
                    elif recovery_schedule.get("operation_kind") == "actuation":
                        (
                            obs,
                            terminal,
                            recovery_actuator_decision,
                            recovery_actuator_latency,
                            recovery_actuator_digest,
                        ) = actuator_transition_handler(
                            obs,
                            last_action,
                            phase_label="lift:measured_outcome_not_met",
                            observation_prefix=(
                                f"actuator_outcome_{stage_index:02d}_lift_"
                                f"{recovery_index + 1}"
                            ),
                        )
                        (
                            obs,
                            terminal,
                            last_action,
                            recovery_actuator_execution,
                        ) = _execute_binary_actuator_tool(
                            env,
                            obs,
                            last_action,
                            recovery_actuator_decision,
                            initial_banana_z=initial_banana_z,
                        )
                        episode_trace["actuator_tool_protocol"]["calls"][-1][
                            "execution"
                        ] = recovery_actuator_execution
                        actuator_latency += recovery_actuator_latency
                        actuator_digest = recovery_actuator_digest
                        actuator_decision = recovery_actuator_decision
                        actuator_execution = recovery_actuator_execution
                        recovery_event["actuator_decision"] = (
                            recovery_actuator_decision
                        )
                        recovery_event["actuator_execution"] = (
                            recovery_actuator_execution
                        )
                    elif recovery_schedule.get("operation_kind") == "motion":
                        recovery_contact = recovery_state_before.get(
                            "current_contact", {}
                        )
                        carry_observed = bool(
                            float(last_action[0, 7].detach().cpu()) > 0.5
                            and isinstance(recovery_contact, dict)
                            and recovery_contact.get("touch")
                            and latched_carry_offset is not None
                        )
                        recovery_motion_decision = motion_checkpoint_handler(
                            obs,
                            {
                                "reason": "measured_stage_outcome_not_met",
                                "phase": "lift:outcome_recovery",
                                "iteration": recovery_index + 1,
                                "current_target_xyz_m": (
                                    _eef_position(env).tolist()
                                ),
                                "current_target_quaternion_wxyz": (
                                    _eef_quaternion(env).tolist()
                                ),
                                "scheduler_decision": recovery_schedule,
                                "measured_outcome": trigger_event,
                                "lease_condition_sources": {
                                    "contact": "sim6.gripper_contact_sensor",
                                    "tracked_pose": (
                                        "sim6.privileged_relative_pose_adapter"
                                        if carry_observed
                                        else None
                                    ),
                                    "tracked_orientation": (
                                        {
                                            object_id: (
                                                "rgbd.instance_depth_major_axis"
                                            )
                                            for object_id in (
                                                latched_rgbd_axis_references
                                            )
                                        }
                                        if carry_observed
                                        else {}
                                    ),
                                    "observed_clearance": None,
                                },
                            },
                        )
                        recovery_event["motion_decision"] = (
                            recovery_motion_decision
                        )
                        if recovery_motion_decision.get("decision") != "execute":
                            raise RuntimeError(
                                "Model did not admit a bounded recovery motion "
                                f"after measured lift failure: "
                                f"{recovery_motion_decision}"
                            )
                        recovery_target = torch.tensor(
                            recovery_motion_decision["target_xyz_m"],
                            dtype=torch.float32,
                        )
                        (
                            obs,
                            terminal,
                            last_action,
                            recovery_motion_report,
                        ) = _move_eef_to_target(
                            env,
                            obs,
                            last_action,
                            recovery_target,
                            _eef_quaternion(env),
                            "lift:outcome_recovery",
                            gripper_closed=bool(
                                float(last_action[0, 7].detach().cpu()) > 0.5
                            ),
                            initial_banana_z=initial_banana_z,
                            executor_config=dict(
                                recovery_motion_decision.get(
                                    "executor_config"
                                )
                                or {}
                            ),
                            carry_reference_offset=(
                                latched_carry_offset if carry_observed else None
                            ),
                            rgbd_axis_references=(
                                latched_rgbd_axis_references
                                if carry_observed
                                else {}
                            ),
                            checkpoint_callback=motion_checkpoint_handler,
                        )
                        recovery_event["motion_report"] = recovery_motion_report
                    else:
                        raise RuntimeError(
                            "Outcome recovery scheduler returned unsupported "
                            f"operation: {recovery_schedule}"
                        )
                    recovery_state_after = _state(env, initial_banana_z)
                    recovery_event["state_after"] = recovery_state_after
                    lift_outcome_recoveries.append(recovery_event)
                    episode_trace["recoveries"].append(recovery_event)
                    lifted = bool(
                        recovery_state_after["banana_lift_m"]
                        >= args_cli.minimum_transport_lift
                    )
                    _write_trace(trace_path, episode_trace)

                banana_now = _local_position(env, "banana")
                eef = _eef_position(env)
                tests["lift"] = lifted
                _test_line(
                    6,
                    "physical banana lift",
                    lifted,
                    f"delta_z={float(banana_now[2]) - initial_banana_z:.3f}m "
                    f"recoveries={len(lift_outcome_recoveries)}",
                )
                episode_trace["stages"][-1]["measured_outcome_recoveries"] = (
                    lift_outcome_recoveries
                )
                episode_trace["stages"][-1]["eef_after_xyz"] = eef.tolist()
                episode_trace["stages"][-1]["banana_after_xyz"] = (
                    banana_now.tolist()
                )
                if not lifted:
                    _write_trace(trace_path, episode_trace)
                    raise RuntimeError(
                        "Measured lift recovery budget exhausted without "
                        "satisfying the runtime outcome predicate"
                    )
                latched_carry_offset = _eef_position(env) - banana_now
                latched_carry_quaternion = _eef_quaternion(env)
                episode_trace["post_lift_carry_latch"] = {
                    "eef_minus_banana_m": latched_carry_offset.tolist(),
                    "eef_quaternion_wxyz": latched_carry_quaternion.tolist(),
                }
                _write_trace(trace_path, episode_trace)
            if task_completed_by_scheduler:
                print(
                    f"[scheduler] physical task declared complete after {phase}; "
                    "skipping remaining runtime-proposed stages",
                    flush=True,
                )
                break
            if terminal:
                break

        if args_cli.disable_release_retreat:
            retreat_report = {
                "enabled": False,
                "converged": None,
                "reason": "disabled by command line",
            }
            tests["detachment"] = True
            _test_line(8, "open-gripper retreat and detachment", True, "disabled")
        else:
            obs, terminal, last_action, retreat_report = _retreat_after_release(
                env,
                obs,
                last_action,
            )
            detached = bool(retreat_report["converged"])
            tests["detachment"] = detached
            episode_trace["release_retreat"] = retreat_report
            _write_trace(trace_path, episode_trace)
            _test_line(
                8,
                "open-gripper retreat and detachment",
                detached,
                f"eef_up={retreat_report['eef_retreat_z_m']:.3f}m "
                f"banana_motion={retreat_report['banana_motion_during_retreat_m']:.3f}m "
                f"plate_xy={retreat_report['banana_plate_xy_error_after_m']:.3f}m "
                f"separation={retreat_report['eef_banana_separation_before_m']:.3f}→"
                f"{retreat_report['eef_banana_separation_after_m']:.3f}m",
            )
            if not detached:
                raise RuntimeError("Open-gripper retreat did not prove clean object detachment")
        episode_trace["release_retreat"] = retreat_report
        _write_trace(trace_path, episode_trace)

        banana_final = _local_position(env, "banana")
        plate_final = _local_position(env, "plate_large")
        final_frame = _single_exterior_frame(obs)
        cv2.imwrite(
            str(args_cli.artifact_dir / "99_final.jpg"),
            cv2.cvtColor(final_frame, cv2.COLOR_RGB2BGR),
        )
        xy_error = float(torch.linalg.norm(banana_final[:2] - plate_final[:2]))
        height_above_plate = float(banana_final[2] - plate_final[2])
        contact_summary = _active_contact_summary()
        contact_passed = (
            True if args_cli.disable_contact_telemetry else bool(contact_summary["passed"])
        )
        tests["contact_telemetry"] = contact_passed
        _test_line(
            9,
            "real gripper contact telemetry",
            contact_passed,
            "disabled" if args_cli.disable_contact_telemetry else (
                f"coverage={contact_summary['coverage']:.3f} "
                f"touch_samples={contact_summary['touch_samples']} "
                f"peak_net_force={contact_summary['peak_net_force_n']:.3f}N"
            ),
        )
        success = xy_error <= 0.12 and 0.0 <= height_above_plate <= 0.20
        tests["success"] = success
        _test_line(
            10,
            "banana-on-plate geometric outcome",
            success,
            f"xy_error={xy_error:.3f}m height_above_plate={height_above_plate:.3f}m",
        )
        episode_trace["final"] = {
            "frame": "99_final.jpg",
            "banana_xyz": banana_final.tolist(),
            "plate_xyz": plate_final.tolist(),
            "banana_plate_xy_error_m": xy_error,
            "banana_height_above_plate_m": height_above_plate,
            "tests": tests,
            "contact_telemetry": contact_summary,
            "all_tests_passed": all(tests.values()),
            "coach_model_calls": model_calls,
        }
        all_tests_passed = all(tests.values())
        episode_trace["status"] = (
            "complete" if all_tests_passed else "failed_not_admitted"
        )
        if episode_recorder is not None and not all_tests_passed:
            episode_trace["training_capture"] = {
                "status": "rejected",
                "reason": "not_all_supervision_and_physical_tests_passed",
                "samples_discarded": episode_recorder.sample_count,
            }
        _write_trace(trace_path, episode_trace)

        if episode_recorder is not None and all_tests_passed:
            ACTIVE_EPISODE_RECORDER = None
            episode_recorder.metadata["outcome"] = {
                "coach_model_calls": model_calls,
                "transport_recovery_count": len(episode_trace["recoveries"]),
                "transport_recovery_outcomes": [
                    recovery.get("outcome")
                    for recovery in episode_trace["recoveries"]
                ],
                "all_supervision_and_physical_tests_passed": True,
            }
            training_row = episode_recorder.publish_success(trace_path=trace_path)
            episode_trace["training_capture"] = {
                "status": "published_success",
                **training_row,
            }
            _write_trace(trace_path, episode_trace)
            print(
                f"Training episode: {training_row['hdf5']} + {training_row['video']} "
                f"({training_row['samples']} samples)",
                flush=True,
            )

        print("=" * 78)
        print(
            f"FINAL: {'PASS' if all(tests.values()) else 'FAIL'} | "
            f"model_calls={model_calls} physics_steps_are_local=True tests={tests}"
        )
        print(f"Frames: {args_cli.artifact_dir}")
        print(f"Trace:  {trace_path}")
        print("=" * 78, flush=True)

        if args_cli.linger_steps > 0:
            print(
                f"[viewer] Holding the final pose for {args_cli.linger_steps} steps...",
                flush=True,
            )
            obs, _ = _hold_joint_action(
                env, obs, last_action, args_cli.linger_steps, gripper_closed=False
            )
        return 0 if all_tests_passed else 2
    except BaseException as error:
        episode_trace["status"] = "failed_not_admitted"
        episode_trace["failure"] = {
            "type": type(error).__name__,
            "message": str(error),
        }
        if episode_recorder is not None:
            episode_trace["training_capture"] = {
                "status": "rejected",
                "reason": "exception_before_all_success_gates",
                "samples_discarded": episode_recorder.sample_count,
            }
        episode_trace["contact_telemetry_at_failure"] = _active_contact_summary()
        _write_trace(trace_path, episode_trace)
        raise
    finally:
        ACTIVE_EPISODE_RECORDER = None
        ACTIVE_SENSOR_MONITOR = None
        ACTIVE_SENSOR_SAMPLE_INDEX = 0
        if ros2_sensor_ingress is not None:
            episode_trace["ros2_sensor_ingress"] = {
                "enabled": True,
                "status": (
                    "subscribed"
                    if ros2_sensor_ingress.available
                    else "unavailable_using_simulator_fallback"
                ),
                **ros2_sensor_ingress.status(),
            }
            _write_trace(trace_path, episode_trace)
            ros2_sensor_ingress.stop()
        ACTIVE_ROS2_SENSOR_INGRESS = None
        if episode_recorder is not None:
            episode_recorder.discard()
        end_episode(env)
        env.close()


if __name__ == "__main__":
    exit_code = 1
    try:
        exit_code = main()
    except Exception as error:
        print(f"[ER2 TEST SUITE] ERROR: {error}", flush=True)
        traceback.print_exc()
    finally:
        simulation_app.close()
    sys.exit(exit_code)
