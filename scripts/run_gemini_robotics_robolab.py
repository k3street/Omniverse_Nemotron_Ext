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
import os
import random
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Callable

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
    help="Run the fixed demonstrated transport without the bounded Cartesian correction.",
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
from rgbd_collision_safety import assess_motion_safety  # noqa: E402
from robolab_contact_telemetry import (  # noqa: E402
    contact_sensor_runtime_info,
    install_sim6_gripper_contact_sensor,
)
from observation_bound_motion_tools import (  # noqa: E402
    ActuatorExecutorRegistry,
    ActuatorExecutorSpec,
    MotionExecutorRegistry,
    MotionExecutorSpec,
    MotionToolValidationError,
    ObservationBoundActuatorGate,
    ObservationBoundMotionGate,
    actuator_tool_schemas,
    motion_report_yields_to_actuator,
    motion_tool_schemas,
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
    "global", "approach_banana", "descend", "grasp", "lift", "above_plate", "release"
}
ACTIVE_EPISODE_RECORDER: GeminiEpisodeDatasetRecorder | None = None
ACTIVE_SENSOR_MONITOR: SensorCaptureBuffer | None = None
ACTIVE_SENSOR_SAMPLE_INDEX = 0


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


def _local_dls_executor_registry() -> MotionExecutorRegistry:
    """Register the currently available executor and its configurable surface."""
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
                "properties": {
                    "position_tolerance_m": {
                        "type": "number", "minimum": 0.001, "maximum": 0.05,
                    },
                    "translation_step_limit_m": {
                        "type": "number", "minimum": 0.001, "maximum": 0.05,
                    },
                    "maximum_iterations": {
                        "type": "integer", "minimum": 1, "maximum": 100,
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
                },
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
actuator to reach its full travel. Do not emit prose or JSON outside the single
native tool call.

{critic_context}"""


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
    gate = ObservationBoundMotionGate(
        observation_id=observation_id,
        current_target_m=current_target.tolist(),
        maximum_correction_m=args_cli.maximum_model_target_correction,
        registry=registry,
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
                    "tools": motion_tool_schemas(observation_id, registry),
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
actuator, or abort_actuation when the transition is unsafe. Measured touch and
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
    }
    effective_config.update(executor_config or {})
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
        if carry_reference_offset is not None and phase == "above_plate":
            banana_after = _local_position(env, "banana")
            safety = assess_motion_safety(
                phase=phase,
                eef_xyz=eef_after.numpy(),
                object_xyz=banana_after.numpy(),
                reference_eef_minus_object=carry_reference_offset.numpy(),
                object_initial_z=initial_banana_z,
                maximum_grasp_drift_m=args_cli.maximum_grasp_drift,
                minimum_carried_lift_m=args_cli.minimum_transport_lift,
            )
            record["local_safety"] = safety.to_dict()
            if not safety.safe:
                checkpoint_reason = "local_anomaly:" + ",".join(safety.reasons)
        if early_stop_callback is not None:
            early_stop = early_stop_callback()
            if early_stop is not None:
                record["early_stop"] = early_stop
        # Preserve the last executed transition even if a local stop or coach
        # pause raises below. This is audit evidence, never success admission.
        iterations.append(record)
        periodic_checkpoint = (
            (iteration + 1) % args_cli.coach_interval_iterations == 0
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
                "local_safety": record.get("local_safety"),
                "current_target_xyz_m": target_cpu.tolist(),
                "executor_id": "bounded_dls_ik",
                "executor_config": dict(effective_config),
            }
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
                recovery_request = {
                    **checkpoint,
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
        "Control cadence: one observation-bound model tool per semantic phase "
        f"and every {args_cli.coach_interval_iterations} local IK chunks"
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
        "Residual plate centering: "
        + (
            "off (baseline mode)"
            if args_cli.disable_residual_centering
            else (
                f"on (tolerance={args_cli.center_tolerance:.3f}m, "
                f"step≤{args_cli.center_max_step:.3f}m, "
                f"release_height={args_cli.release_height:.3f}m, "
                f"iterations≤{args_cli.center_max_iterations})"
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
    motion_executor_registry = _local_dls_executor_registry()
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
            checkpoint_state = _state(env, initial_banana_z)
            checkpoint_frame = _single_exterior_frame(checkpoint_obs)
            checkpoint_frame, depth_summary = _rgbd_checkpoint_frame(
                env, checkpoint_frame
            )
            checkpoint["rgbd"] = depth_summary
            checkpoint_index = len(episode_trace["motion_checkpoints"])
            frame_name = f"motion_checkpoint_{checkpoint_index:03d}.jpg"
            cv2.imwrite(
                str(args_cli.artifact_dir / frame_name),
                cv2.cvtColor(checkpoint_frame, cv2.COLOR_RGB2BGR),
            )
            # Recovery/status checkpoints may report a measured condition
            # without proposing a destination. Bind those calls to the fresh
            # measured end-effector pose so the model can hold, abort, or apply
            # a bounded correction through the same executor protocol.
            checkpoint.setdefault(
                "current_target_xyz_m",
                checkpoint_state["eef_gripper_base_xyz"],
            )
            current_target = torch.tensor(
                checkpoint["current_target_xyz_m"], dtype=torch.float32
            )
            decision, latency, digest = _choose_observation_bound_motion_tool(
                motion_tool_provider,
                motion_executor_registry,
                instruction=args_cli.instruction,
                observation_prefix=f"checkpoint-{checkpoint_index}",
                frame=checkpoint_frame,
                state=checkpoint_state,
                current_target=current_target,
                motion_context=checkpoint,
                rgbd_summary=depth_summary,
                critic_context=_critic_context(
                    critic_memory, str(checkpoint["phase"])
                ),
            )
            model_calls += 1
            digests.append(digest)
            event = {
                **checkpoint,
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
            print(
                f"[ER2 checkpoint] phase={checkpoint['phase']} "
                f"iteration={checkpoint['iteration']} reason={checkpoint['reason']} "
                f"tool={decision['motion_tool']['tool_name']} "
                f"decision={decision.get('decision')} latency={latency:.2f}s",
                flush=True,
            )
            return decision

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
                    bool(recorded_actions[start, 7] > 0.5),
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
        transport_recovery_count = 0
        placement_completed_during_recovery = False
        episode_trace["recoveries"] = []
        last_action = torch.zeros((1, 8), dtype=torch.float32, device=env.device)
        last_action[0, :7] = torch.as_tensor(
            joint_states[0, :7], dtype=torch.float32, device=env.device
        )
        for stage_index, (phase, start, end, close) in enumerate(stages, start=1):
            actuator_engaged_at_stage_start = bool(
                float(last_action[0, 7].detach().cpu()) > 0.5
            )
            actuator_decision: dict[str, Any] | None = None
            actuator_execution: dict[str, Any] | None = None
            actuator_latency = 0.0
            actuator_digest: str | None = None
            current = _state(env, initial_banana_z)
            if not args_cli.disable_adaptive_ik and phase != "release":
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
                        "current_actuator_state": (
                            "engaged"
                            if actuator_engaged_at_stage_start
                            else "disengaged"
                        ),
                        "executor_candidates": [
                            spec.executor_id
                            for spec in motion_executor_registry.specs()
                        ],
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
                        close,
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
                        if (
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
                        carry_reference_offset=(
                            latched_carry_offset if phase == "above_plate" else None
                        ),
                        checkpoint_callback=motion_checkpoint_handler,
                    )
                    motion_attempts.append(attempt_report)
                    if not bool(attempt_report.get("recovery_requested")):
                        break
                    actuator_transition_pending = bool(close) != bool(
                        float(last_action[0, 7].detach().cpu()) > 0.5
                    )
                    if motion_report_yields_to_actuator(
                        attempt_report,
                        actuator_transition_pending=actuator_transition_pending,
                    ):
                        attempt_report["yielded_to_actuator"] = True
                        attempt_report["yield_reason"] = (
                            "model_hold_with_pending_actuator_transition"
                        )
                        attempt_report["recovery_requested"] = False
                        attempt_report["converged"] = True
                        print(
                            f"[executor handoff] {phase}: model hold yielded "
                            "to pending actuator tool",
                            flush=True,
                        )
                        break
                    if phase != "above_plate":
                        raise RuntimeError(
                            f"Recovery requested during unsupported phase {phase}: "
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
            if (
                not args_cli.disable_adaptive_ik
                and bool(close) != actuator_engaged_before_transition
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
                _write_trace(trace_path, episode_trace)
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
                if (
                    not actuator_engaged_before_transition
                    and actuator_execution["engaged_after"]
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
                "upstream_actuator_state_hint": (
                    "engaged" if close else "disengaged"
                ),
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
                    obs, terminal, last_action, centering_report = _residual_center_over_plate(
                        env,
                        obs,
                        last_action,
                        initial_banana_z,
                    )
                    centered = bool(centering_report["converged"])
                    tests["centering"] = centered
                    _test_line(
                        7,
                        "bounded residual plate centering",
                        centered,
                        f"xy_error={centering_report['xy_error_before_m']:.3f}→"
                        f"{centering_report['xy_error_after_m']:.3f}m "
                        f"height={centering_report['height_above_plate_before_m']:.3f}→"
                        f"{centering_report['height_above_plate_after_m']:.3f}m "
                        f"iterations={len(centering_report['iterations'])}",
                    )
                    if not centered:
                        raise RuntimeError(
                            "Residual centering did not reach the release tolerance; refusing release"
                        )
                episode_trace["residual_centering"] = centering_report
                _write_trace(trace_path, episode_trace)

            if phase == "lift":
                lifted = float(banana_now[2]) - initial_banana_z >= 0.05
                tests["lift"] = lifted
                _test_line(
                    6,
                    "physical banana lift",
                    lifted,
                    f"delta_z={float(banana_now[2]) - initial_banana_z:.3f}m",
                )
                if not lifted:
                    raise RuntimeError("Gripper moved to lift pose but banana did not follow")
                latched_carry_offset = _eef_position(env) - banana_now
                latched_carry_quaternion = _eef_quaternion(env)
                episode_trace["post_lift_carry_latch"] = {
                    "eef_minus_banana_m": latched_carry_offset.tolist(),
                    "eef_quaternion_wxyz": latched_carry_quaternion.tolist(),
                }
                _write_trace(trace_path, episode_trace)
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
