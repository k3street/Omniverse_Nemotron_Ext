"""Live Isaac 6 smoke for the Cosmos 3 Edge executor path, no Gemini needed.

Builds the same DROID env the composed-execution runner uses (joint-position
actions, wrist + both over-shoulder cameras), asks the local Edge policy
server for action chunks, and steps them through the env — validating the
exact camera extraction, observation packing, and chunk application the
``cosmos3_edge_policy`` motion executor performs, without spending any
planning-model budget.

Run with the same launcher environment as the runner:

  ISAAC:  $ISAAC_SIM_ROOT/python.sh scripts/smoke_cosmos3_edge_live.py \
              --headless --task BlocksInBinTask --chunks 2
"""

import argparse

import cv2  # noqa: F401  # Must import before isaaclab. Do not remove.
from isaaclab.app import AppLauncher

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--task", default="BlocksInBinTask")
parser.add_argument("--chunks", type=int, default=2)
parser.add_argument("--settle-steps", type=int, default=30)
parser.add_argument("--host", default="localhost")
parser.add_argument("--port", type=int, default=8000)
parser.add_argument(
    "--instruction",
    default="pick up the object and put it in the bin",
)
AppLauncher.add_app_launcher_args(parser)
args_cli = parser.parse_args()
args_cli.enable_cameras = True
app_launcher = AppLauncher(args_cli)
simulation_app = app_launcher.app

import numpy as np  # noqa: E402
import torch  # noqa: E402
from robolab.core.environments.runtime import create_env  # noqa: E402
from robolab.core.environments.config import parse_env_cfg  # noqa: E402
from robolab.registrations.droid.auto_env_registrations_abs_ik import (  # noqa: E402
    auto_register_droid_abs_ik_envs,
)
from robolab.registrations.droid.camera_presets import (  # noqa: E402
    WRIST_LEFT_RIGHT,
)
from robolab.robots.droid import DroidJointPositionActionCfg  # noqa: E402
from cosmos3_edge_client import Cosmos3EdgeChunkClient  # noqa: E402

CAMERA_NAMES = (
    "wrist_cam",
    "over_shoulder_left_camera",
    "over_shoulder_right_camera",
)


def frame(obs, name):
    tensor = obs["image_obs"][name][0]
    array = (
        tensor.detach().cpu().numpy()
        if hasattr(tensor, "detach")
        else np.asarray(tensor)
    )
    return array[..., :3].astype(np.uint8)


def current_joint_action(env):
    robot = env.scene["robot"]
    joint_positions = getattr(
        robot.data.joint_pos, "torch", robot.data.joint_pos
    )
    action = torch.zeros((1, 8), dtype=torch.float32, device=env.device)
    arm_ids = [
        robot.data.joint_names.index(f"panda_joint{index}")
        for index in range(1, 8)
    ]
    action[0, :7] = joint_positions[0, arm_ids]
    return action


def eef_xyz(env):
    # Same body and ProxyArray handling as the runner's _eef_position.
    robot = env.scene["robot"]
    body_index = robot.data.body_names.index("base_link")
    positions = getattr(robot.data.body_pos_w, "torch", robot.data.body_pos_w)
    root = getattr(robot.data.root_pos_w, "torch", robot.data.root_pos_w)
    return (positions[0, body_index] - root[0]).detach().float().cpu().clone()


def main():
    auto_register_droid_abs_ik_envs(
        task=args_cli.task,
        contact_sensors=False,
        cameras=WRIST_LEFT_RIGHT,
    )
    env_cfg = parse_env_cfg(
        args_cli.task, device="cuda:0", seed=0, num_envs=1, use_fabric=True
    )
    # Same Sim 6 spawn-pose contract fixes as the composed-execution runner.
    env_cfg.scene.robot.init_state.rot = (0.0, 0.0, 0.0, 1.0)
    fixture_rot = env_cfg.scene.table_fixture.init_state.rot
    env_cfg.scene.table_fixture.init_state.rot = (
        fixture_rot[1], fixture_rot[2], fixture_rot[3], fixture_rot[0]
    )
    for asset_name in env_cfg.contact_object_list:
        asset_cfg = getattr(env_cfg.scene, asset_name)
        w, x, y, z = asset_cfg.init_state.rot
        asset_cfg.init_state.rot = (x, y, z, w)
    env_cfg.actions = DroidJointPositionActionCfg()
    env_cfg.terminations = None
    env_cfg.subtasks = None
    env_cfg.recorders = None
    env, _ = create_env(env_cfg, use_fabric=True, policy="cosmos3-edge-smoke")
    obs, _ = env.reset()

    hold = current_joint_action(env)
    for _ in range(args_cli.settle_steps):
        obs, *_ = env.step(hold)

    for name in CAMERA_NAMES:
        assert name in obs["image_obs"], f"camera {name} missing from obs"
        print(f"[smoke] camera {name}: {frame(obs, name).shape}")

    client = Cosmos3EdgeChunkClient(host=args_cli.host, port=args_cli.port)
    start_xyz = eef_xyz(env)
    total_steps = 0
    for chunk_index in range(args_cli.chunks):
        action = current_joint_action(env)
        chunk = client.infer_chunk(
            wrist_rgb=frame(obs, "wrist_cam"),
            left_rgb=frame(obs, "over_shoulder_left_camera"),
            right_rgb=frame(obs, "over_shoulder_right_camera"),
            joint_position_rad=action[0, :7].detach().cpu().numpy(),
            gripper_position=[0.0],
            prompt=args_cli.instruction,
        )
        print(
            f"[smoke] chunk {chunk_index}: inference "
            f"{chunk.inference_seconds:.2f}s, gripper values "
            f"{sorted(set(chunk.gripper_commands.tolist()))}"
        )
        command = action.clone()
        for step in range(len(chunk.actions)):
            command = command.clone()
            command[0, :8] = torch.as_tensor(
                chunk.actions[step], dtype=torch.float32, device=env.device
            )
            obs, *_ = env.step(command)
            total_steps += 1
    moved_m = float(torch.linalg.norm(eef_xyz(env) - start_xyz))
    print(
        f"[smoke] PASS: {args_cli.chunks} chunks / {total_steps} env steps "
        f"executed; eef moved {moved_m:.3f} m from start"
    )
    env.close()
    simulation_app.close()


if __name__ == "__main__":
    main()
