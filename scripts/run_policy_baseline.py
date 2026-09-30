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
  pi05    Physical Intelligence's pi0.5, DROID joint-position checkpoint,
          through RoboLab's own OpenPI client: over-shoulder and wrist images
          plus joint state in, 15-step chunks of joint targets and a binary
          gripper out. Needs an OpenPI policy server (see launch_openpi_server.sh).

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
parser.add_argument("--policy", choices=("oracle", "pi05"), default="oracle")
parser.add_argument("--instruction", default="Put the red block in the grey bin")
parser.add_argument("--policy-host", default="localhost")
parser.add_argument("--policy-port", type=int, default=8000)
parser.add_argument("--max-policy-steps", type=int, default=600,
                    help="pi05 episode length in control steps (40 s at 15 Hz)")
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
# Like the planner's executor: hold each IK command for a few physics steps so
# the arm's joint drives catch up before the next Jacobian is taken. One step
# per command left the arm 3 cm short of the grasp after 90 steps.
parser.add_argument("--settle-steps", type=int, default=12)
parser.add_argument("--max-iterations-per-waypoint", type=int, default=40)
parser.add_argument("--hold-steps", type=int, default=20)
AppLauncher.add_app_launcher_args(parser)
args = parser.parse_args()
args.enable_cameras = True
simulation_app = AppLauncher(args).app

sys.path.insert(0, str(Path(__file__).resolve().parent))
from robolab.core.environments.runtime import create_env, end_episode  # noqa: E402
from robolab.core.observations.observation_utils import unpack_image_obs  # noqa: E402
from robolab.core.utils.video_utils import VideoWriter  # noqa: E402

from adaptive_pick_place import (  # noqa: E402
    choose_grasp_yaw,
    quaternion_error_axis_angle_wxyz,
    quaternion_multiply_wxyz,
    yaw_quaternion_wxyz,
)
from rgbd_collision_safety import grasp_axis_finger_clearance  # noqa: E402
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
# Robotiq 2F-85 finger bodies in the gripper-base frame, as the planner's
# runtime publishes them: the jaws close along local y, fingers are 2.7 cm wide.
FINGER_BOUNDS_LOCAL = {
    "left_inner_finger": {"min_m": [0.093, 0.0417, -0.0135], "max_m": [0.150, 0.0730, 0.0135]},
    "right_inner_finger": {"min_m": [0.093, -0.0730, -0.0135], "max_m": [0.150, -0.0417, 0.0135]},
}
CLOSING_AXIS_LOCAL = torch.tensor([0.0, -1.0, 0.0])


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


def true_scene_geometry(env, movable: str) -> dict:
    """Every rigid object's true box, from its USD shape at its live pose.

    The planner estimates these from RGB-D; the oracle may read them. Poses
    come from the simulator tensors (physics does not write moved poses back
    to USD), shapes from each prim's untransformed USD bound.
    """
    import omni.usd
    from pxr import Usd, UsdGeom

    stage = omni.usd.get_context().get_stage()
    cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_])
    geometries = []
    for name in env.scene.rigid_objects.keys():
        prim = stage.GetPrimAtPath(f"/World/envs/env_0/scene/{name}")
        if not prim.IsValid():
            continue
        local = cache.ComputeUntransformedBound(prim).ComputeAlignedRange()
        lo, hi = np.array(local.GetMin()), np.array(local.GetMax())
        corners = np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])])
        pos, q = object_pose(env, name)
        w, x, y, z = (float(v) for v in q)
        rotation = np.array([
            [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
        ])
        world = corners @ rotation.T + pos.numpy()
        item = {"runtime_id": name, "visible_aabb_min_base_m": world.min(0).tolist(),
                "visible_aabb_max_base_m": world.max(0).tolist()}
        if name == movable:
            yaw = yaw_of(q)
            item["center_base_m"] = world.mean(0).tolist()
            item["oriented_footprint_axes_base"] = [[math.cos(yaw), math.sin(yaw), 0.0],
                                                    [-math.sin(yaw), math.cos(yaw), 0.0]]
            extents = hi - lo
            item["oriented_footprint_extents_m"] = sorted([float(extents[0]), float(extents[1])], reverse=True)
        geometries.append(item)
    # A box that contains the object's own centre is what it stands on or in
    # (the table, a fixture spanning the scene), not a neighbour a finger can
    # hit. Left in, one such box read as a 19.7 m finger overlap on every axis.
    target = next(g for g in geometries if g["runtime_id"] == movable)
    cx, cy = target["center_base_m"][:2]
    return {"geometries": [
        g for g in geometries
        if g is target or not (g["visible_aabb_min_base_m"][0] <= cx <= g["visible_aabb_max_base_m"][0]
                               and g["visible_aabb_min_base_m"][1] <= cy <= g["visible_aabb_max_base_m"][1])
    ]}


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
        seed = quaternion_multiply_wxyz(yaw_quaternion_wxyz(turn, like=DOWNWARD_GRASP_WXYZ), DOWNWARD_GRASP_WXYZ)
        # Of the grasps a quarter turn apart, take the one whose fingers have
        # the most room beside the neighbours (then the smallest wrist turn),
        # with the same code the planner uses, fed true geometry instead of
        # RGB-D estimates. Choosing by wrist turn alone put a finger on a
        # neighbour in development scene 2.
        geometry = true_scene_geometry(env, movable)
        target = next(g for g in geometry["geometries"] if g["runtime_id"] == movable)
        clearances = grasp_axis_finger_clearance(
            scene_geometry=geometry,
            actuator_geometry={"contact_body_bounds_local_m": FINGER_BOUNDS_LOCAL},
            object_runtime_id=movable,
        )
        extents = target["oriented_footprint_extents_m"]
        _, current_q = eef_pose(env)
        self.grasp_q, self.grasp_choice = choose_grasp_yaw(
            seed, current_q, CLOSING_AXIS_LOCAL,
            [axis[:2] for axis in target["oriented_footprint_axes_base"]],
            [c["finger_clearance_m"] for c in clearances],
            quarter_turn_symmetric=max(extents) / max(min(extents), 1e-6) <= 1.25,
        )
        self.grasp_choice["clearances"] = clearances
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


def run_oracle(env, recorder, trace: dict, initial_object_yaw: float) -> torch.Tensor:
    policy = OraclePolicy(env, args.movable_object_asset, args.target_receptacle_asset, initial_object_yaw)
    trace["grasp_choice"] = policy.grasp_choice
    print(f"[baseline] grasp axis {policy.grasp_choice['chosen_object_axis_index']}, "
          f"quarter turns {policy.grasp_choice['chosen_quarter_turns']}, clearances "
          f"{[(round(c['finger_clearance_m'] or 0, 4), c['nearest_obstruction']) for c in policy.grasp_choice['clearances']]}",
          flush=True)
    action = None
    for label, target, gripper in policy.waypoints:
        steps, reached = 0, False
        dwell = label in ("grasp", "release")
        iterations = 1 if dwell else args.max_iterations_per_waypoint
        for _ in range(iterations):
            action, err_m, err_deg = policy.step_toward(target, gripper)
            for _ in range(args.hold_steps if dwell else args.settle_steps):
                obs, *_ = env.step(action.to(env.device))
                pos, q = eef_pose(env)
                recorder.append(env, action, obs, eef_position=pos.numpy(), eef_quaternion_wxyz=q.numpy())
                steps += 1
            if not dwell:
                _, err_m, err_deg = policy.step_toward(target, gripper)
                if err_m < 0.006 and err_deg < 3.0:
                    reached = True
                    break
        trace["waypoints"].append({"label": label, "steps": steps, "reached": reached or dwell,
                                   "position_error_m": err_m, "orientation_error_deg": err_deg})
        print(f"[baseline] {label}: steps={steps} error={err_m:.4f} m {err_deg:.1f} deg", flush=True)
    return action


def run_pi05(env, obs, recorder, trace: dict) -> torch.Tensor:
    """Closed-loop pi0.5 through RoboLab's client, which handles action chunking."""
    from policies.pi0_family.client import Pi0DroidJointposClient

    client = Pi0DroidJointposClient(remote_host=args.policy_host, remote_port=args.policy_port, policy_variant="pi05")
    client.reset()
    trace["instruction"] = args.instruction
    trace["policy_server"] = f"{args.policy_host}:{args.policy_port}"
    action = None
    for step in range(args.max_policy_steps):
        chunk_action = np.asarray(client.infer(obs, args.instruction)["action"], dtype=np.float32).reshape(-1)
        action = torch.from_numpy(chunk_action[:8]).reshape(1, 8)
        obs, *_ = env.step(action.to(env.device))
        pos, q = eef_pose(env)
        recorder.append(env, action, obs, eef_position=pos.numpy(), eef_quaternion_wxyz=q.numpy())
        if step % 75 == 0:
            print(f"[baseline] pi05 step {step}: gripper={float(action[0, 7]):.0f} eef={pos.numpy().round(3).tolist()}", flush=True)
    trace["policy_steps"] = args.max_policy_steps
    return action


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
        started = time.time()
        if args.policy == "pi05":
            action = run_pi05(env, obs, recorder, trace)
        else:
            action = run_oracle(env, recorder, trace, yaw_of(initial_q))
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
