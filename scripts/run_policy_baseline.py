#!/usr/bin/env python3
"""Run a non-planner policy in the planner's exact scenes, recorded and graded the same way.

The comparison is only fair if every policy gets the same scene and the same
grade. This runner takes the planner's scene flags (so the evaluation harness
can launch it on the same seeded scenes), builds the environment through
robolab_sim6_env (the same Sim 6 corrections the planner uses), records with
GeminiEpisodeDatasetRecorder and grades with planner_eval_gate's final-state
grade, which is the one the scorecard uses for every policy.

Policies:
  oracle  scripted expert: reads the true object and receptacle poses and
          follows fixed waypoints with damped-least-squares IK. An upper
          reference: it knows what no camera-driven policy can.

Launch through launch_policy_baseline.sh, which waits for the machine's single
Isaac slot like the planner launcher does.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
import traceback
from pathlib import Path

import cv2  # noqa: F401  Must precede Isaac Lab imports.
import numpy as np
import torch
from isaaclab.app import AppLauncher

parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
parser.add_argument("--policy", choices=("oracle",), default="oracle")
parser.add_argument("--task", default="BlocksInBinTask")
parser.add_argument("--movable-object-asset", default="red_block")
parser.add_argument("--target-receptacle-asset", default="grey_bin")
parser.add_argument("--movable-object-offset", nargs=2, type=float, default=(0.0, 0.0))
parser.add_argument("--plate-offset", nargs=2, type=float, default=(0.0, 0.0))
parser.add_argument("--movable-object-yaw-deg", type=float, default=0.0)
parser.add_argument("--light-intensity", type=float)
parser.add_argument("--appearance-seed", type=int, default=0)
parser.add_argument("--randomize-background", action="store_true")
parser.add_argument("--artifact-dir", type=Path, required=True)
parser.add_argument("--acceptance", type=Path,
                    default=Path(__file__).resolve().parents[1] / "config/planner_eval/blocks_in_bin_astra_v1.json")
parser.add_argument("--max-steps-per-waypoint", type=int, default=90)
parser.add_argument("--hold-steps", type=int, default=20)
AppLauncher.add_app_launcher_args(parser)
args = parser.parse_args()
args.enable_cameras = True
simulation_app = AppLauncher(args).app

sys.path.insert(0, str(Path(__file__).resolve().parent))
from robolab.core.environments.runtime import create_env, end_episode  # noqa: E402
from robolab.core.observations.observation_utils import unpack_image_obs  # noqa: E402
from robolab.core.utils.video_utils import VideoWriter  # noqa: E402

from adaptive_pick_place import quaternion_error_axis_angle_wxyz, quaternion_multiply_wxyz, yaw_quaternion_wxyz  # noqa: E402
from gemini_episode_dataset import GeminiEpisodeDatasetRecorder  # noqa: E402
from planner_eval_gate import check_final_state, outcome_from_arrays  # noqa: E402
from residual_centering import bounded_vector_step, damped_least_squares_delta  # noqa: E402
from robolab_sim6_env import apply_harness_scene, build_env_cfg, set_camera_views  # noqa: E402

# The planner's calibrated top-down grasp: base_link 14.9 cm above the object's
# root, wrist in the orientation RoboLab's successful demonstration used.
GRASP_HEIGHT_M = 0.149
DOWNWARD_GRASP_WXYZ = torch.tensor([0.555, 0.385, 0.616, -0.406])
DOWNWARD_GRASP_WXYZ = DOWNWARD_GRASP_WXYZ / torch.linalg.norm(DOWNWARD_GRASP_WXYZ)
# Bin heights for base_link above the receptacle root: clear the 10.5 cm rim.
APPROACH_M, LIFT_M, ABOVE_BIN_M, RELEASE_M, RETREAT_M = 0.10, 0.14, 0.34, 0.26, 0.34


def torch_view(value):
    return getattr(value, "torch", value)


def eef_pose(env) -> tuple[torch.Tensor, torch.Tensor]:
    robot = env.scene["robot"]
    index = robot.data.body_names.index("base_link")
    pos = torch_view(robot.data.body_pos_w)[0, index] - torch_view(robot.data.root_pos_w)[0]
    q = torch_view(robot.data.body_quat_w)[0, index]
    return pos.detach().cpu().float(), q[[3, 0, 1, 2]].detach().cpu().float()


def object_pose(env, name) -> tuple[torch.Tensor, torch.Tensor]:
    data = env.scene[name].data
    pos = torch_view(data.root_pos_w)[0] - env.scene.env_origins[0]
    q = torch_view(data.root_quat_w)[0]
    return pos.detach().cpu().float(), q[[3, 0, 1, 2]].detach().cpu().float()


def yaw_of(q_wxyz: torch.Tensor) -> float:
    w, x, y, z = (float(v) for v in q_wxyz)
    return math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z))


class OraclePolicy:
    """Waypoints from privileged poses, reached with bounded DLS IK."""

    def __init__(self, env, movable: str, receptacle: str, initial_object_yaw: float):
        self.env = env
        obj, obj_q = object_pose(env, movable)
        bin_xyz, _ = object_pose(env, receptacle)
        # A square footprint grips the same every quarter turn: follow the
        # object's yaw by the smallest turn that squares the jaws with a face.
        turn = yaw_of(obj_q) - initial_object_yaw
        turn = ((turn + math.pi / 4) % (math.pi / 2)) - math.pi / 4
        self.grasp_q = quaternion_multiply_wxyz(yaw_quaternion_wxyz(turn, like=DOWNWARD_GRASP_WXYZ), DOWNWARD_GRASP_WXYZ)
        up = lambda h: torch.tensor([0.0, 0.0, h])  # noqa: E731
        grasp = obj + up(GRASP_HEIGHT_M)
        self.waypoints = [
            ("approach", grasp + up(APPROACH_M), 0.0),
            ("descend", grasp, 0.0),
            ("grasp", grasp, 1.0),
            ("lift", grasp + up(LIFT_M), 1.0),
            ("above receptacle", bin_xyz + up(ABOVE_BIN_M), 1.0),
            ("lower", bin_xyz + up(RELEASE_M), 1.0),
            ("release", bin_xyz + up(RELEASE_M), 0.0),
            ("retreat", bin_xyz + up(RETREAT_M), 0.0),
        ]
        robot = env.scene["robot"]
        self.arm_ids = [robot.data.joint_names.index(f"panda_joint{i}") for i in range(1, 8)]
        body = robot.data.body_names.index("base_link")
        self.jac_body = body - 1 if robot.is_fixed_base else body
        self.jac_joints = [i + robot.num_base_dofs for i in self.arm_ids]

    def step_toward(self, target: torch.Tensor, gripper: float) -> tuple[torch.Tensor, float, float]:
        robot = self.env.scene["robot"]
        pos, q = eef_pose(self.env)
        error = target - pos
        rot_error = quaternion_error_axis_angle_wxyz(self.grasp_q, q)
        twist = torch.cat((bounded_vector_step(error, 0.02), bounded_vector_step(rot_error, math.radians(8))))
        jac = torch_view(robot.data.body_link_jacobian_w)[0, self.jac_body][:, self.jac_joints].detach().cpu().float()
        delta = damped_least_squares_delta(jac, twist, 0.05, 0.07)
        joints = torch_view(robot.data.joint_pos)[0, self.arm_ids].detach().cpu().float()
        limits = torch_view(robot.data.soft_joint_pos_limits)[0, self.arm_ids].detach().cpu().float()
        action = torch.zeros((1, 8), dtype=torch.float32)
        action[0, :7] = torch.clamp(joints + delta, limits[:, 0] + 1e-3, limits[:, 1] - 1e-3)
        action[0, 7] = gripper
        return action, float(torch.linalg.vector_norm(error)), math.degrees(float(torch.linalg.vector_norm(rot_error)))


def main() -> int:
    acceptance = json.loads(args.acceptance.read_text())
    args.artifact_dir.mkdir(parents=True, exist_ok=True)
    trace: dict = {"policy": args.policy, "task": args.task, "status": "running", "waypoints": []}
    trace_path = args.artifact_dir / "baseline_trace.json"
    env_cfg = build_env_cfg(
        args.task, randomize_background=args.randomize_background,
        appearance_seed=args.appearance_seed, light_intensity=args.light_intensity,
    )
    env, _ = create_env(env_cfg, use_fabric=True, policy=f"baseline-{args.policy}")
    recorder = None
    try:
        obs, _ = env.reset()
        _, initial_q = object_pose(env, args.movable_object_asset)
        apply_harness_scene(env, args.movable_object_asset, args.target_receptacle_asset, {
            "movable_object_offset_xy_m": args.movable_object_offset,
            "plate_offset_xy_m": args.plate_offset,
            "movable_object_yaw_deg": args.movable_object_yaw_deg,
        })
        set_camera_views(env)
        robot = env.scene["robot"]
        hold = torch.zeros((1, 8), dtype=torch.float32)
        hold[0, :7] = torch_view(robot.data.joint_pos)[0, :7].detach().cpu()
        for _ in range(15):  # moved objects report new poses only after stepping
            obs, *_ = env.step(hold.to(env.device))
        recorder = GeminiEpisodeDatasetRecorder(
            output_dir=args.artifact_dir / "training_episodes", episode_index=0,
            movable_object_asset=args.movable_object_asset, target_receptacle_asset=args.target_receptacle_asset,
            metadata={"policy": args.policy, "task": args.task,
                      "movable_object_offset_xy_m": list(args.movable_object_offset),
                      "target_receptacle_offset_xy_m": list(args.plate_offset),
                      "movable_object_yaw_deg": args.movable_object_yaw_deg},
            video_writer_factory=VideoWriter, unpack_images=unpack_image_obs, fps=15,
        )
        policy = OraclePolicy(env, args.movable_object_asset, args.target_receptacle_asset, yaw_of(initial_q))
        started = time.time()
        for label, target, gripper in policy.waypoints:
            steps, reached = 0, False
            limit = args.hold_steps if label in ("grasp", "release") else args.max_steps_per_waypoint
            while steps < limit:
                action, err_m, err_deg = policy.step_toward(target, gripper)
                obs, *_ = env.step(action.to(env.device))
                pos, q = eef_pose(env)
                recorder.append(env, action, obs, eef_position=pos.numpy(), eef_quaternion_wxyz=q.numpy())
                steps += 1
                if label not in ("grasp", "release") and err_m < 0.006 and err_deg < 3.0:
                    reached = True
                    break
            trace["waypoints"].append({"label": label, "steps": steps, "reached": reached or label in ("grasp", "release"),
                                       "position_error_m": err_m, "orientation_error_deg": err_deg})
            print(f"[baseline] {label}: steps={steps} error={err_m:.4f} m {err_deg:.1f} deg", flush=True)
        for _ in range(15):  # let the object settle before grading
            obs, *_ = env.step(action.to(env.device))
            pos, q = eef_pose(env)
            recorder.append(env, action, obs, eef_position=pos.numpy(), eef_quaternion_wxyz=q.numpy())
        trace["wall_s"] = time.time() - started
        trace["status"] = "complete"
    except BaseException as error:
        trace["status"] = "failed"
        trace["failure"] = {"type": type(error).__name__, "message": str(error)}
        traceback.print_exc()
    finally:
        spec = acceptance["final_state"]
        if recorder is not None and recorder.sample_count:
            # Grade with the same final-state grade as every policy, from the
            # recorder's own buffers, then file the recording truthfully.
            passed, details = check_final_state(outcome_from_arrays(
                recorder._movable_object_poses, recorder._target_receptacle_poses, recorder._actions,
            ), acceptance)
            trace["final_state"] = {"passed": passed, **details}
            if passed and recorder.sample_count >= 41:
                trace["recording"] = recorder.publish_success(trace_path=trace_path)
            else:
                trace["recording"] = recorder.preserve_failure(
                    reason=f"final-state grade failed: {details.get('failed')}", trace_path=trace_path)
        else:
            trace["final_state"] = {"passed": False, "failed": ["outcome_missing"]}
        (trace_path).write_text(json.dumps(trace, indent=1, default=float) + "\n")
        print(f"[baseline] FINAL: {'PASS' if trace['final_state']['passed'] else 'FAIL'} {trace['final_state'].get('failed')}",
              flush=True)
        end_episode(env)
        env.close()
    return 0 if trace["final_state"]["passed"] else 2


if __name__ == "__main__":
    code = 1
    try:
        code = main()
    finally:
        simulation_app.close(exit_code=code)
    sys.exit(code)
