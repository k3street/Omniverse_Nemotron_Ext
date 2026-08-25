#!/usr/bin/env python3
"""Visible Gemini Robotics ER 2 coach test on RoboLab BananaOnPlateTask.

The model is the stage-level embodied-reasoning coach. RoboLab's demonstrated
joint trajectory remains the fast motion executor, so a fresh camera/state
observation is sent after every semantic phase instead of calling the model
every simulator step.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sys
import time
import traceback
from pathlib import Path
from typing import Any

import cv2  # Must precede Isaac Lab imports.
import h5py
import numpy as np
import requests
import torch
from dotenv import load_dotenv
from isaaclab.app import AppLauncher


REPO_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(REPO_ROOT / ".env")

parser = argparse.ArgumentParser(
    description="Run all visible Gemini Robotics ER 2 tests on RoboLab's current DROID robot."
)
parser.add_argument("--task", default="BananaOnPlateTask")
parser.add_argument("--model", default="gemini-robotics-er-2-preview")
parser.add_argument("--retry-steps", type=int, default=20)
parser.add_argument(
    "--disable-residual-centering",
    action="store_true",
    help="Run the fixed demonstrated transport without the bounded Cartesian correction.",
)
parser.add_argument("--center-tolerance", type=float, default=0.025)
parser.add_argument("--center-max-step", type=float, default=0.020)
parser.add_argument("--center-max-z-step", type=float, default=0.015)
parser.add_argument("--release-height", type=float, default=0.040)
parser.add_argument("--release-height-tolerance", type=float, default=0.012)
parser.add_argument("--center-max-iterations", type=int, default=8)
parser.add_argument("--center-settle-steps", type=int, default=12)
parser.add_argument("--center-max-joint-step", type=float, default=0.08)
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
parser.add_argument(
    "--artifact-dir",
    type=Path,
    default=REPO_ROOT / "artifacts" / "gemini_robotics_er2_robolab",
)
parser.add_argument(
    "--disable-critic-guidance",
    action="store_true",
    help="Ignore phase-scoped lessons from the previous passive local critique.",
)
AppLauncher.add_app_launcher_args(parser)
args_cli, _ = parser.parse_known_args()
args_cli.enable_cameras = True
app_launcher = AppLauncher(args_cli)
simulation_app = app_launcher.app


import robolab.constants  # noqa: E402
from robolab.core.environments.runtime import create_env, end_episode  # noqa: E402
from robolab.core.environments.config import parse_env_cfg  # noqa: E402
from robolab.core.utils.vis_utils import visualize_axes  # noqa: E402
from robolab.registrations.droid.auto_env_registrations_abs_ik import (  # noqa: E402
    auto_register_droid_abs_ik_envs,
)
from robolab.robots.droid import DroidJointPositionActionCfg  # noqa: E402
from residual_centering import (  # noqa: E402
    bounded_scalar_step,
    bounded_xy_step,
    damped_least_squares_delta,
)


MODEL_ID = args_cli.model
GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    f"{MODEL_ID}:generateContent"
)

# Calibrated base-link pose transferred from RoboLab's successful demonstration.
# ER 2 is the semantic/visual coach; the recorded trajectory is the local motion
# primitive, matching the architecture that made the older Gemini coach work.
BANANA_GRASP_OFFSET = torch.tensor([-0.010, -0.023, 0.147], dtype=torch.float32)
BANANA_GRASP_QUAT = torch.tensor([0.555, 0.385, 0.616, -0.406], dtype=torch.float32)
BANANA_GRASP_QUAT /= torch.linalg.norm(BANANA_GRASP_QUAT)
GRIPPER_BASE_TO_FINGERTIP_M = 0.149
TOTAL_TESTS = 9
VALID_CRITIC_PHASES = {
    "global", "approach_banana", "descend", "grasp", "lift", "above_plate", "release"
}


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
        response = self.session.post(GEMINI_URL, json=payload, timeout=self.timeout)
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


def _single_exterior_frame(obs: dict[str, Any]) -> np.ndarray:
    frame = obs["image_obs"]["over_shoulder_left_camera"][0]
    if isinstance(frame, torch.Tensor):
        frame = frame.detach().cpu().numpy()
    frame = np.asarray(frame)
    return np.ascontiguousarray(frame[..., :3].astype(np.uint8, copy=False))


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
    return {
        "banana_xyz": banana.tolist(),
        "plate_xyz": plate.tolist(),
        "eef_gripper_base_xyz": eef.tolist(),
        "gripper_fingertip_center_xyz": fingertip.tolist(),
        "gripper_base_to_fingertip_m": GRIPPER_BASE_TO_FINGERTIP_M,
        "finger_joint_rad": finger_joint_rad,
        "gripper_closed_fraction": max(0.0, min(1.0, finger_joint_rad / (np.pi / 4))),
        "banana_lift_m": float(banana[2]) - initial_banana_z,
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
Do not output joint angles. The calibrated local motion primitive handles motion.

{critic_context}"""


def _stage_prompt(
    phase: str,
    state: dict[str, Any],
    nominal_target: torch.Tensor,
    gripper_closed: bool,
    critic_context: str,
) -> str:
    distance = float(
        torch.linalg.norm(torch.tensor(state["eef_gripper_base_xyz"]) - nominal_target)
    )
    return f"""You are Gemini Robotics ER 2 acting as a closed-loop robot coach.
Task: Pick up the yellow banana and put it on the white plate.
Current phase: {phase}
This is a FRESH observation captured after the previous phase completed.

Privileged simulator state in robot-root coordinates (meters):
{json.dumps(state, indent=2)}
Calibrated nominal Cartesian target: {nominal_target.tolist()}
Current EEF-to-target distance: {distance:.4f} m
Requested gripper state: {"closed" if gripper_closed else "open"}

Important tool geometry: eef_gripper_base_xyz is the Robotiq mounting flange,
not the jaws. For this fixed downward grasp orientation, the provided
gripper_fingertip_center_xyz is 0.149 m lower and is the point that must align
with the banana. Do not reject a grasp because the mounting flange is above it.
The measured gripper_closed_fraction is authoritative: values near 1.0 mean
closed and values near 0.0 mean open. At phase "lift", the banana is expected
to remain at table height until this phase executes; execute the lift to test
whether the grasp is physically attached, then the next observation verifies it.

Inspect the attached current multi-camera image. The calibrated executor will
play the demonstrated local joint-space motion primitive for this semantic
phase; you are not controlling individual joints or individual simulator frames.

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


def _test_line(index: int, name: str, passed: bool, detail: str) -> None:
    status = "PASS" if passed else "FAIL"
    print(f"[TEST {index}/{TOTAL_TESTS}] {status} | {name} | {detail}", flush=True)


def _write_trace(path: Path, trace: dict[str, Any]) -> None:
    """Atomically publish the latest episode evidence, including partial runs."""
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(trace, indent=2) + "\n")
    temporary.replace(path)


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
        obs, _, terminated, truncated, _ = env.step(action)
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
        obs, _, terminated, truncated, _ = env.step(command)
        terminal = bool(torch.as_tensor(terminated).any()) or bool(
            torch.as_tensor(truncated).any()
        )
        if terminal:
            break
    return obs, terminal


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
    banana_start = _local_position(env, "banana")
    plate_start = _local_position(env, "plate_large")
    error_start = float(torch.linalg.vector_norm(plate_start[:2] - banana_start[:2]))
    height_start = float(banana_start[2] - plate_start[2])
    previous_error = error_start
    previous_height_error = abs(args_cli.release_height - height_start)

    for iteration in range(args_cli.center_max_iterations):
        banana = _local_position(env, "banana")
        plate = _local_position(env, "plate_large")
        error_xy = (plate[:2] - banana[:2]).to(env.device)
        error_norm = float(torch.linalg.vector_norm(error_xy))
        height_above_plate = float(banana[2] - plate[2])
        height_error = args_cli.release_height - height_above_plate
        if (
            error_norm <= args_cli.center_tolerance
            and abs(height_error) <= args_cli.release_height_tolerance
        ):
            break
        if iteration == 0 and float(banana[2]) - initial_banana_z < 0.05:
            raise RuntimeError("Residual centering refused: banana is no longer securely lifted")
        if height_above_plate < 0.015:
            raise RuntimeError("Residual centering refused: banana is already at plate contact height")

        xy_step = bounded_xy_step(error_xy, args_cli.center_max_step)
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
        iteration_record = {
            "iteration": iteration + 1,
            "xy_error_before_m": error_norm,
            "requested_xy_step_m": xy_step.detach().cpu().tolist(),
            "height_above_plate_before_m": height_above_plate,
            "requested_z_step_m": float(z_step),
            "max_abs_joint_step_rad": float(torch.max(torch.abs(delta_joint))),
            "xy_error_after_m": error_after,
            "height_above_plate_after_m": height_after,
            "banana_lift_after_m": lifted_after,
            "terminal": terminal,
        }
        iterations.append(iteration_record)
        print(
            f"[center] iteration={iteration + 1} xy={error_norm:.4f}→{error_after:.4f}m "
            f"height={height_above_plate:.4f}→{height_after:.4f}m "
            f"step_xy={xy_step.detach().cpu().tolist()} step_z={float(z_step):.4f}m "
            f"max_dq={iteration_record['max_abs_joint_step_rad']:.4f}rad",
            flush=True,
        )
        if terminal:
            break
        if height_after < 0.015:
            raise RuntimeError("Residual centering pushed the banana below safe plate clearance")
        # Fail closed if a commanded correction materially moves away from the
        # plate. Tiny contact-induced fluctuations are allowed.
        if error_after > previous_error + 0.005:
            raise RuntimeError(
                f"Residual centering diverged: XY error {previous_error:.4f}→{error_after:.4f} m"
            )
        if height_error_after > previous_height_error + 0.005:
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
        "converged": (
            error_final <= args_cli.center_tolerance
            and abs(height_final - args_cli.release_height) <= args_cli.release_height_tolerance
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
    max_detached_object_motion = 0.75 * args_cli.retreat_distance
    state_final = _state(env, float(banana_start[2]))
    gripper_open = state_final["gripper_closed_fraction"] <= 0.10
    converged = (
        retreat_z >= max(0.040, args_cli.retreat_distance - 0.020)
        and banana_motion <= max_detached_object_motion
        and final_separation - start_separation >= 0.040
        and gripper_open
        and on_plate
        and not terminal
    )
    report = {
        "enabled": True,
        "requested_retreat_m": args_cli.retreat_distance,
        "eef_start_xyz": eef_start.tolist(),
        "eef_final_xyz": eef_final.tolist(),
        "eef_retreat_z_m": retreat_z,
        "banana_start_xyz": banana_start.tolist(),
        "banana_final_xyz": banana_final.tolist(),
        "banana_motion_during_retreat_m": banana_motion,
        "maximum_detached_object_motion_m": max_detached_object_motion,
        "banana_plate_xy_error_after_m": banana_plate_xy_error,
        "banana_height_above_plate_after_m": banana_height_above_plate,
        "banana_remained_on_plate": on_plate,
        "eef_banana_separation_before_m": start_separation,
        "eef_banana_separation_after_m": final_separation,
        "gripper_closed_fraction_after": state_final["gripper_closed_fraction"],
        "converged": converged,
        "iterations": iterations,
    }
    return obs, terminal, command, report


def main() -> int:
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("Set GEMINI_API_KEY or GOOGLE_API_KEY in the environment")
    if not args_cli.disable_residual_centering:
        positive_centering_values = {
            "center_tolerance": args_cli.center_tolerance,
            "center_max_step": args_cli.center_max_step,
            "center_max_z_step": args_cli.center_max_z_step,
            "release_height": args_cli.release_height,
            "release_height_tolerance": args_cli.release_height_tolerance,
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
    critic_memory_path = args_cli.artifact_dir / "critic_guidance.json"
    critic_memory = (
        {"source_model": None, "lessons": []}
        if args_cli.disable_critic_guidance
        else _load_critic_guidance(critic_memory_path, args_cli.task)
    )
    robolab.constants.set_output_dir(str(args_cli.artifact_dir / "robolab_output"))
    # RoboLab's 5.x contact-filter expressions do not resolve under the Sim 6
    # tensor API yet. This test scores physical lift/place geometrically.
    robolab.constants.ENABLE_SUBTASK_PROGRESS_CHECKING = False
    robolab.constants.RECORD_IMAGE_DATA = False
    robolab.constants.VERBOSE = False

    print("=" * 78)
    print("VISIBLE TEST SUITE: Gemini Robotics ER 2 + RoboLab DROID/Franka")
    print(f"Model: {MODEL_ID}")
    print(f"Task:  {args_cli.task}")
    print(f"GUI:   {'off (headless)' if args_cli.headless else 'on'}")
    print(f"Local motion primitive: {demo_path} ({len(recorded_actions)} steps)")
    print("Control cadence: one ER 2 call per semantic phase; physics remains local")
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
    print("=" * 78, flush=True)

    sim_version_path = Path(os.environ.get("ISAAC_SIM_ROOT", "")) / "VERSION"
    sim_version = (
        sim_version_path.read_text().strip()
        if sim_version_path.is_file()
        else "6.x runtime (VERSION path unavailable)"
    )
    sim6_ok = sim_version.startswith("6.")
    _test_line(1, "Isaac Sim 6 runtime", sim6_ok, sim_version)

    auto_register_droid_abs_ik_envs(task=args_cli.task, contact_sensors=False)
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
    # Do not restore Sim 5 rigid-body snapshots into Sim 6. Their contact state
    # can begin interpenetrating and eject task objects on the first step.
    _set_sim6_camera_views(env)
    env.sim.render()
    obs = env.observation_manager.compute()
    coach = GeminiRoboticsER2(api_key, args_cli.timeout)
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
        "schema_version": 1,
        "task": args_cli.task,
        "coach_model": MODEL_ID,
        "critic_memory_applied": critic_memory,
        "sim_version": sim_version,
        "physics_steps_are_local": True,
        "initial_state": _state(env, initial_banana_z),
        "stages": [],
        "status": "running",
    }
    trace_path = args_cli.artifact_dir / "sequence_trace.json"
    _write_trace(trace_path, episode_trace)

    try:
        frame = _single_exterior_frame(obs)
        cv2.imwrite(
            str(args_cli.artifact_dir / "00_scene.jpg"),
            cv2.cvtColor(frame, cv2.COLOR_RGB2BGR),
        )
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
        _write_trace(trace_path, episode_trace)

        plate = _local_position(env, "plate_large")
        stages = []
        for phase, start, end in zip(phase_names, boundaries[:-1], boundaries[1:]):
            stages.append(
                (
                    phase,
                    int(start),
                    int(end),
                    torch.from_numpy(recorded_actions[start, :3].copy()),
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
                torch.from_numpy(recorded_actions[-1, :3].copy()),
                False,
            )
        )

        pregrasp_passed = False
        last_action = torch.zeros((1, 8), dtype=torch.float32, device=env.device)
        last_action[0, :7] = torch.as_tensor(
            joint_states[0, :7], dtype=torch.float32, device=env.device
        )
        for stage_index, (phase, start, end, nominal, close) in enumerate(stages, start=1):
            current = _state(env, initial_banana_z)
            frame = _single_exterior_frame(obs)
            frame_path = args_cli.artifact_dir / f"{stage_index:02d}_{phase}_before.jpg"
            cv2.imwrite(str(frame_path), cv2.cvtColor(frame, cv2.COLOR_RGB2BGR))
            decision, latency, digest = coach.reason(
                _stage_prompt(
                    phase,
                    current,
                    nominal,
                    close,
                    _critic_context(critic_memory, phase),
                ),
                frame,
            )
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
                if phase == "grasp":
                    retry_performed = True
                    print(
                        "[coach] pre-grasp requested retry; re-holding grasp pose",
                        flush=True,
                    )
                    obs, terminal = _hold_joint_action(
                        env, obs, last_action, args_cli.retry_steps, gripper_closed=False
                    )
                    current = _state(env, initial_banana_z)
                    frame = _single_exterior_frame(obs)
                    decision, latency, digest = coach.reason(
                        _stage_prompt(
                            phase,
                            current,
                            nominal,
                            close,
                            _critic_context(critic_memory, phase),
                        ),
                        frame,
                    )
                    model_calls += 1
                    digests.append(digest)
                    confidence = float(decision.get("confidence", 0.0))
                    print(
                        f"[ER2] pre-grasp retry decision={decision.get('decision')} "
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
                BANANA_GRASP_QUAT,
                "gemini_er2_target",
                axis_length=0.12,
            )
            print(
                f"[executor] {phase}: demonstrated_steps={end - start} "
                f"target={nominal.tolist()} gripper={'closed' if close else 'open'}",
                flush=True,
            )
            if phase == "release":
                obs, terminal = _hold_joint_action(
                    env, obs, last_action, 35, gripper_closed=False
                )
            else:
                obs, terminal, last_action = _run_joint_segment(
                    env, obs, joint_states, recorded_actions, start, end
                )
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
            eef = _eef_position(env)
            eef_trace.append(eef.tolist())
            pos_error = float(torch.linalg.norm(eef - nominal))
            banana_now = _local_position(env, "banana")
            print(
                f"[feedback] {phase}: eef={eef.tolist()} error={pos_error:.4f}m "
                f"banana={banana_now.tolist()} terminal={terminal}",
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
                "demonstrated_steps": end - start,
                "requested_gripper": "closed" if close else "open",
                "eef_after_xyz": eef.tolist(),
                "eef_target_error_m": pos_error,
                "banana_after_xyz": banana_now.tolist(),
                "terminal": terminal,
            })
            _write_trace(trace_path, episode_trace)

            if phase == "above_plate" and not terminal:
                if args_cli.disable_residual_centering:
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
        success = xy_error <= 0.12 and 0.0 <= height_above_plate <= 0.20
        tests["success"] = success
        _test_line(
            9,
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
            "all_tests_passed": all(tests.values()),
            "coach_model_calls": model_calls,
        }
        episode_trace["status"] = "complete"
        _write_trace(trace_path, episode_trace)

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
        return 0 if all(tests.values()) else 2
    finally:
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
