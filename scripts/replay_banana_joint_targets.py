#!/usr/bin/env python3
"""Replay measured arm joint targets from an absolute-IK BananaOnPlate demo.

This is a conversion diagnostic: it restores the recorded scene state, swaps
the environment to RoboLab's joint-position controller, and commands the next
recorded arm state plus the original binary gripper command.  A successful
episode proves that the joint-space representation used for GR00T training is
physically capable of reproducing the demonstration.
"""
from __future__ import annotations

import argparse
import sys
import traceback
from pathlib import Path

import cv2  # Must precede Isaac Lab imports.
import h5py
import numpy as np
import torch
from isaaclab.app import AppLauncher


parser = argparse.ArgumentParser()
parser.add_argument("--hdf5", required=True, type=Path)
parser.add_argument("--episode", type=int, default=0)
parser.add_argument("--state-offset", type=int, default=1, choices=(0, 1))
parser.add_argument("--tail-steps", type=int, default=20)
AppLauncher.add_app_launcher_args(parser)
args, _ = parser.parse_known_args()
args.enable_cameras = True
launcher = AppLauncher(args)
simulation_app = launcher.app

import robolab.constants  # noqa: E402
from robolab.core.environments.runtime import create_env, end_episode  # noqa: E402
from robolab.core.replay import restore_recorded_initial_state  # noqa: E402
from robolab.registrations.droid.auto_env_registrations_jointpos import (  # noqa: E402
    auto_register_droid_envs,
)


def main() -> None:
    source_path = args.hdf5.expanduser().resolve()
    demo_key = f"data/demo_{args.episode}"
    with h5py.File(source_path, "r") as source:
        demo = source[demo_key]
        states = np.asarray(
            demo["states/articulation/robot/joint_position"], dtype=np.float32
        )[:, :7]
        recorded_actions = np.asarray(demo["actions"], dtype=np.float32)

    robolab.constants.ENABLE_SUBTASK_PROGRESS_CHECKING = True
    robolab.constants.RECORD_IMAGE_DATA = False
    robolab.constants.VERBOSE = False
    auto_register_droid_envs(task="BananaOnPlateTask")
    env, _ = create_env(
        "BananaOnPlateTask", num_envs=1, use_fabric=True, policy="joint-target-replay"
    )
    env.reset()
    restore_recorded_initial_state(env, str(source_path), args.episode)

    last_action = None
    total_steps = len(states) + args.tail_steps
    for step in range(total_steps):
        source_index = min(step + args.state_offset, len(states) - 1)
        gripper_index = min(step, len(recorded_actions) - 1)
        action_np = np.concatenate(
            [states[source_index], recorded_actions[gripper_index, 7:8]], axis=0
        )
        last_action = torch.from_numpy(action_np).to(env.device).unsqueeze(0)
        _, _, terminated, truncated, _ = env.step(last_action)
        if bool(torch.as_tensor(terminated).any()) or bool(torch.as_tensor(truncated).any()):
            break

    results = env.get_env_results()
    print(
        f"[joint-target-replay] offset={args.state_offset} steps={step + 1} "
        f"results={results}"
    )
    end_episode(env)
    env.close()
    simulation_app.close()
    if not results or not bool(results[0].get("success", False)):
        raise SystemExit(2)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"[joint-target-replay] error: {error}")
        traceback.print_exc()
        simulation_app.close()
        sys.exit(1)
