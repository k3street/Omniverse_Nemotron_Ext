#!/usr/bin/env python3
"""Generate privileged scripted BananaOnPlate demonstrations in RoboLab."""
from __future__ import annotations

import argparse
import math
import sys
import traceback
from pathlib import Path

import cv2  # Must precede Isaac Lab imports.
import torch
from isaaclab.app import AppLauncher


parser = argparse.ArgumentParser()
parser.add_argument("--episodes", type=int, default=1)
parser.add_argument("--hold-steps", type=int, default=35)
parser.add_argument("--output", type=Path, default=Path("output/banana_on_plate_oracle"))
parser.add_argument("--xy-jitter", type=float, default=0.03)
parser.add_argument("--no-save-videos", action="store_true")
AppLauncher.add_app_launcher_args(parser)
args, _ = parser.parse_known_args()
args.enable_cameras = True
launcher = AppLauncher(args)
simulation_app = launcher.app

import robolab.constants  # noqa: E402
from robolab.core.environments.runtime import create_env, end_episode  # noqa: E402
from robolab.core.observations.observation_utils import unpack_image_obs  # noqa: E402
from robolab.core.utils.video_utils import VideoWriter  # noqa: E402
from robolab.registrations.droid.auto_env_registrations_abs_ik import (  # noqa: E402
    auto_register_droid_abs_ik_envs,
)


# Base-link grasp pose transferred from RoboLab's bundled successful banana
# demonstration, expressed relative to the banana centroid.
BANANA_GRASP_OFFSET = torch.tensor([-0.010, -0.023, 0.147], dtype=torch.float32)
BANANA_GRASP_QUAT = torch.tensor([0.555, 0.385, 0.616, -0.406], dtype=torch.float32)
BANANA_GRASP_QUAT /= torch.linalg.norm(BANANA_GRASP_QUAT)


def object_position(env, name: str) -> torch.Tensor:
    return env.scene[name].data.root_pos_w[0].detach().cpu().clone()


def jitter_object(env, name: str, amount: float, generator: torch.Generator) -> None:
    if amount <= 0:
        return
    asset = env.scene[name]
    pose = asset.data.root_pose_w.clone()
    delta = (torch.rand((2,), generator=generator) * 2.0 - 1.0) * amount
    pose[0, :2] += delta.to(pose.device)
    asset.write_root_pose_to_sim(pose)
    asset.write_root_velocity_to_sim(torch.zeros_like(asset.data.root_vel_w))


def run_episode(env, hold_steps: int, episode: int, output: Path) -> bool:
    obs, _ = env.reset()
    # Arm recording only after reset. Arming before reset causes the recorder to
    # finalize an empty episode and leaves subsequent simulator steps unrecorded.
    if hasattr(env.recorder_manager, "set_hdf5_file"):
        env.recorder_manager.set_hdf5_file(f"run_{episode}.hdf5")
        env.recorder_manager.set_episode_index(0, env_ids=[0])
    generator = torch.Generator(device="cpu").manual_seed(episode)
    jitter_object(env, "banana", args.xy_jitter, generator)
    jitter_object(env, "plate_large", args.xy_jitter, generator)
    video = None
    if not args.no_save_videos:
        video = VideoWriter(str(output / f"episode_{episode:06d}_policy.mp4"), fps=15)
    frames = env.scene["frames"]
    eef_index = frames.data.target_frame_names.index("eef_frame")
    banana = object_position(env, "banana")
    plate = object_position(env, "plate_large")

    # Keep the gripper's known reachable downward-facing orientation and move
    # through conservative vertical waypoints derived from privileged object poses.
    waypoints = [
        ("approach banana", banana + BANANA_GRASP_OFFSET + torch.tensor([0.0, 0.0, 0.10]), 0.0, hold_steps),
        ("descend", banana + BANANA_GRASP_OFFSET, 0.0, hold_steps),
        ("grasp", banana + BANANA_GRASP_OFFSET, 1.0, hold_steps),
        ("lift", banana + BANANA_GRASP_OFFSET + torch.tensor([0.0, 0.0, 0.14]), 1.0, hold_steps),
        ("above plate", plate + torch.tensor([0.0, 0.0, 0.27]), 1.0, hold_steps + 10),
        ("lower to plate", plate + torch.tensor([0.0, 0.0, 0.16]), 1.0, hold_steps),
        ("release", plate + torch.tensor([0.0, 0.0, 0.16]), 0.0, hold_steps),
        ("retreat", plate + torch.tensor([0.0, 0.0, 0.28]), 0.0, hold_steps),
    ]

    action = torch.zeros((1, 8), dtype=torch.float32, device=env.device)
    action_quat = BANANA_GRASP_QUAT.to(env.device)
    terminated = False
    for label, target, gripper, steps in waypoints:
        action[0, :3] = target.to(env.device)
        action[0, 3:7] = action_quat
        action[0, 7] = gripper
        print(f"[oracle] {label}: target={target.tolist()} gripper={gripper}")
        for _ in range(steps):
            obs, _, term, trunc, _ = env.step(action)
            if video is not None:
                video.write(unpack_image_obs(obs, env_id=0)["combined_image"])
            if bool(torch.as_tensor(term).any()) or bool(torch.as_tensor(trunc).any()):
                terminated = True
                break
        if terminated:
            break

    results = env.get_env_results()
    success = bool(results and results[0].get("success", False))
    if video is not None:
        video.release()
    print(f"[oracle] result: success={success} details={results}")
    return success


def main() -> None:
    output = args.output.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    robolab.constants.set_output_dir(str(output))
    robolab.constants.ENABLE_SUBTASK_PROGRESS_CHECKING = True
    # Camera data is written as compressed MP4; raw 720p frames in HDF5 would
    # consume roughly 1.6 GB per episode.
    robolab.constants.RECORD_IMAGE_DATA = False
    robolab.constants.VERBOSE = False
    auto_register_droid_abs_ik_envs(task="BananaOnPlateTask")
    successes = 0
    for episode in range(args.episodes):
        # RoboLab's streaming recorder is single-shot after a terminal episode;
        # use a fresh manager per demo so all state/action streams are re-armed.
        env, _ = create_env("BananaOnPlateTask", num_envs=1, use_fabric=True)
        successes += int(run_episode(env, args.hold_steps, episode, output))
        end_episode(env)
        env.close()
    simulation_app.close()
    print(f"[oracle] complete: {successes}/{args.episodes} successful")
    if successes != args.episodes:
        raise SystemExit(2)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"[oracle] error: {error}")
        traceback.print_exc()
        simulation_app.close()
        sys.exit(1)
