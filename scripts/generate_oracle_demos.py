#!/usr/bin/env python3
"""Generate privileged scripted pick-and-place demonstrations in RoboLab.

The controller reads object poses straight out of the simulator, so it needs no
model API and costs nothing to run. Each task is described by a TaskProfile;
multi-object tasks are driven one object at a time, re-reading poses between
objects because earlier placements disturb the scene.
"""
from __future__ import annotations

import argparse
import math
import sys
import traceback
from dataclasses import dataclass
from pathlib import Path

import cv2  # Must precede Isaac Lab imports.
import h5py
import torch
from isaaclab.app import AppLauncher


parser = argparse.ArgumentParser()
parser.add_argument("--task", default="BananaOnPlate", choices=("BananaOnPlate", "BlocksInBin"))
parser.add_argument("--episodes", type=int, default=1)
# Each episode seeds its jitter from its index, so a second run with the same
# range reproduces the same scenes. Offset the index to get new ones.
parser.add_argument("--seed-offset", type=int, default=0)
parser.add_argument("--hold-steps", type=int, default=35)
parser.add_argument("--output", type=Path, default=Path("output/banana_on_plate_oracle"))
parser.add_argument("--xy-jitter", type=float, default=0.03)
# Cluttered scenes park small objects centimetres apart. Descending onto one of
# those closes the gripper on its neighbour instead, which is worse than not
# demonstrating it: it teaches exactly the failure we are trying to train out.
parser.add_argument("--min-clearance", type=float, default=0.0)
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
from franka_sensor_schema import (  # noqa: E402
    SIGNAL_SPECS,
    SensorCaptureBuffer,
    empty_sensor_frame,
    sensor_frame_from_isaac_env,
    write_sensor_group,
)


# Base-link grasp pose transferred from RoboLab's bundled successful banana
# demonstration, expressed relative to the banana centroid.
BANANA_GRASP_OFFSET = torch.tensor([-0.010, -0.023, 0.147], dtype=torch.float32)
BANANA_GRASP_QUAT = torch.tensor([0.555, 0.385, 0.616, -0.406], dtype=torch.float32)
BANANA_GRASP_QUAT /= torch.linalg.norm(BANANA_GRASP_QUAT)

# The blocks are small cubes, so the same downward-facing wrist works; only the
# lateral bias (tuned for the banana's curve) drops out.
BLOCK_GRASP_OFFSET = torch.tensor([0.0, 0.0, 0.147], dtype=torch.float32)


@dataclass(frozen=True)
class TaskProfile:
    """Everything the scripted controller needs to drive one task."""

    task: str
    pick_objects: tuple[str, ...]
    place_target: str
    grasp_offset: torch.Tensor
    grasp_quaternion: torch.Tensor
    # Heights are relative to the picked object and the place target centroids.
    approach_m: float = 0.10
    lift_m: float = 0.14
    place_clearance_m: float = 0.27
    place_release_m: float = 0.16
    retreat_m: float = 0.28
    jitter_objects: tuple[str, ...] = ()


TASK_PROFILES = {
    "BananaOnPlate": TaskProfile(
        task="BananaOnPlateTask",
        pick_objects=("banana",),
        place_target="plate_large",
        grasp_offset=BANANA_GRASP_OFFSET,
        grasp_quaternion=BANANA_GRASP_QUAT,
        jitter_objects=("banana", "plate_large"),
    ),
    # Four blocks into a walled bin: one episode yields four grasp cycles, and
    # the release has to clear the bin wall rather than hovering over a flat
    # plate, so it is staged higher than the banana's.
    "BlocksInBin": TaskProfile(
        task="BlocksInBinTask",
        pick_objects=("red_block", "blue_block", "green_block", "yellow_block"),
        place_target="grey_bin",
        grasp_offset=BLOCK_GRASP_OFFSET,
        grasp_quaternion=BANANA_GRASP_QUAT,
        place_clearance_m=0.34,
        place_release_m=0.26,
        retreat_m=0.34,
        jitter_objects=("red_block", "blue_block", "green_block", "yellow_block"),
    ),
}

PROFILE = TASK_PROFILES[args.task]


def scene_object_names(env) -> tuple[str, ...]:
    objects = getattr(getattr(env, "scene", None), "rigid_objects", None)
    return tuple(objects.keys()) if objects else ()


def graspable_objects(env, profile: "TaskProfile", min_clearance_m: float) -> tuple[str, ...]:
    """Keep only pick targets with room around them for a top-down descent."""
    if min_clearance_m <= 0:
        return profile.pick_objects
    others = [
        name
        for name in scene_object_names(env)
        # The table is the support surface and the bin is where things go; a
        # block sitting near either is still perfectly graspable.
        if name not in {"table", profile.place_target}
    ]
    keep = []
    for name in profile.pick_objects:
        here = object_position(env, name)[:2]
        clearance = min(
            (
                float(torch.linalg.norm(object_position(env, other)[:2] - here))
                for other in others
                if other != name
            ),
            default=float("inf"),
        )
        if clearance >= min_clearance_m:
            keep.append(name)
        else:
            print(f"[oracle] skipping {name}: nearest object {clearance:.3f} m < {min_clearance_m:.3f} m")
    return tuple(keep)


def _up(height_m: float) -> torch.Tensor:
    return torch.tensor([0.0, 0.0, height_m], dtype=torch.float32)


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


def run_episode(
    env, hold_steps: int, episode: int, output: Path
) -> tuple[bool, SensorCaptureBuffer, tuple[str, ...]]:
    obs, _ = env.reset()
    # Arm recording only after reset. Arming before reset causes the recorder to
    # finalize an empty episode and leaves subsequent simulator steps unrecorded.
    if hasattr(env.recorder_manager, "set_hdf5_file"):
        env.recorder_manager.set_hdf5_file(f"run_{episode}.hdf5")
        env.recorder_manager.set_episode_index(0, env_ids=[0])
    generator = torch.Generator(device="cpu").manual_seed(episode + args.seed_offset)
    for name in PROFILE.jitter_objects:
        jitter_object(env, name, args.xy_jitter, generator)
    video = None
    if not args.no_save_videos:
        video = VideoWriter(str(output / f"episode_{episode:06d}_policy.mp4"), fps=15)
    frames = env.scene["frames"]
    eef_index = frames.data.target_frame_names.index("eef_frame")
    target_xyz = object_position(env, PROFILE.place_target)

    # Keep the gripper's known reachable downward-facing orientation and move
    # through conservative vertical waypoints derived from privileged object poses.
    # Pick poses are read before the first approach: nothing moves an object
    # until the gripper reaches it, and re-reading mid-episode would pick up a
    # block already being carried.
    waypoints = []
    attempted = graspable_objects(env, PROFILE, args.min_clearance)
    print(f"[oracle] attempting {len(attempted)}/{len(PROFILE.pick_objects)} objects: {list(attempted)}")
    for name in attempted:
        grasp = object_position(env, name) + PROFILE.grasp_offset
        place = PROFILE.place_target
        waypoints += [
            (f"approach {name}", grasp + _up(PROFILE.approach_m), 0.0, hold_steps),
            (f"descend {name}", grasp, 0.0, hold_steps),
            (f"grasp {name}", grasp, 1.0, hold_steps),
            (f"lift {name}", grasp + _up(PROFILE.lift_m), 1.0, hold_steps),
            (f"above {place}", target_xyz + _up(PROFILE.place_clearance_m), 1.0, hold_steps + 10),
            (f"lower {name}", target_xyz + _up(PROFILE.place_release_m), 1.0, hold_steps),
            (f"release {name}", target_xyz + _up(PROFILE.place_release_m), 0.0, hold_steps),
            (f"retreat {name}", target_xyz + _up(PROFILE.retreat_m), 0.0, hold_steps),
        ]

    action = torch.zeros((1, 8), dtype=torch.float32, device=env.device)
    action_quat = PROFILE.grasp_quaternion.to(env.device)
    terminated = False
    sensor_buffer = SensorCaptureBuffer()
    sensor_warning_printed = False
    sample_index = 0
    step_dt = float(getattr(env, "step_dt", 1.0 / 15.0))
    for label, target, gripper, steps in waypoints:
        action[0, :3] = target.to(env.device)
        action[0, 3:7] = action_quat
        action[0, 7] = gripper
        print(f"[oracle] {label}: target={target.tolist()} gripper={gripper}")
        for _ in range(steps):
            obs, _, term, trunc, _ = env.step(action)
            try:
                sensor_frame = sensor_frame_from_isaac_env(env)
            except Exception as error:
                # Sensor capture is additive. Keep the episode, but make the
                # missing sample explicit through an all-zero validity mask.
                sensor_frame = empty_sensor_frame()
                if not sensor_warning_printed:
                    print(f"[oracle] optional sensor capture unavailable: {error}")
                    sensor_warning_printed = True
            sensor_buffer.append(sensor_frame, sample_index * step_dt)
            sample_index += 1
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
    return success, sensor_buffer, attempted


def attach_sensor_recording(
    hdf5_path: Path, sensor_buffer: SensorCaptureBuffer, demo_key: str = "demo_0"
) -> None:
    values, validity, timestamps = sensor_buffer.arrays()
    with h5py.File(hdf5_path, "r+") as target:
        demo = target[f"data/{demo_key}"]
        write_sensor_group(
            demo,
            values,
            validity,
            timestamps,
            source="isaac_sim_contact_and_actuator_telemetry",
        )
        sample_count = int(demo.attrs["num_samples"])
    coverage = {
        spec.name: (float(validity[:, index].mean()) if len(validity) else 0.0)
        for index, spec in enumerate(SIGNAL_SPECS)
    }
    print(
        f"[oracle] sensor schema attached to {hdf5_path.name}: "
        f"samples={sample_count} captured={len(values)} coverage={coverage}"
    )


def main() -> None:
    output = args.output.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    robolab.constants.set_output_dir(str(output))
    robolab.constants.ENABLE_SUBTASK_PROGRESS_CHECKING = True
    # Camera data is written as compressed MP4; raw 720p frames in HDF5 would
    # consume roughly 1.6 GB per episode.
    robolab.constants.RECORD_IMAGE_DATA = False
    robolab.constants.VERBOSE = False
    auto_register_droid_abs_ik_envs(task=PROFILE.task)
    successes = 0
    for episode in range(args.episodes):
        # RoboLab's streaming recorder is single-shot after a terminal episode;
        # use a fresh manager per demo so all state/action streams are re-armed.
        env, _ = create_env(PROFILE.task, num_envs=1, use_fabric=True)
        success, sensor_buffer, attempted = run_episode(env, args.hold_steps, episode, output)
        successes += int(success)
        end_episode(env)
        env.close()
        hdf5_path = output / f"run_{episode}.hdf5"
        if not hdf5_path.is_file():
            raise FileNotFoundError(f"RoboLab recorder did not create {hdf5_path}")
        attach_sensor_recording(hdf5_path, sensor_buffer)
        # Record what the controller set out to move. A multi-object task only
        # marks itself successful when every object is placed, so selecting
        # episodes later means comparing outcome against intent -- and intent
        # cannot be recovered from the trajectories alone.
        with h5py.File(hdf5_path, "r+") as target:
            target["data/demo_0"].attrs["attempted_objects"] = ",".join(attempted)
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
