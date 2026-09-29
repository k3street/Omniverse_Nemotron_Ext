"""RoboLab environments on the Isaac Sim 6 source build, set up one way for every runner.

RoboLab's task configs were authored for Isaac Sim 5, and three things break on
this Sim 6 build: spawn quaternions are read as (x, y, z, w) instead of
(w, x, y, z); the absolute-IK action resolves the Robotiq control body wrongly;
and RoboLab's episode recorder uses a tensor API Sim 6 removed. The planner
worked around all three inline. Anything else that runs a policy in the same
scenes (a scripted oracle, a VLA baseline) must make the same corrections, or
it is graded on a different scene than the planner, so they live here.

Import only after the Isaac app has launched (AppLauncher).
"""
from __future__ import annotations

import random
from typing import Any, Mapping, Sequence

import numpy as np
import torch


def _xyzw_to_wxyz(q: torch.Tensor) -> torch.Tensor:
    return q[[3, 0, 1, 2]]


def _wxyz_to_xyzw(q: torch.Tensor) -> torch.Tensor:
    return q[[1, 2, 3, 0]]


def _yaw_quaternion_wxyz(yaw_rad: float, like: torch.Tensor) -> torch.Tensor:
    half = yaw_rad / 2.0
    return like.new_tensor([np.cos(half), 0.0, 0.0, np.sin(half)])


def _multiply_wxyz(a: torch.Tensor, b: torch.Tensor) -> torch.Tensor:
    aw, ax, ay, az = a
    bw, bx, by, bz = b
    return torch.stack((
        aw * bw - ax * bx - ay * by - az * bz,
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
    ))


def build_env_cfg(
    task: str,
    *,
    instruction: str | None = None,
    cameras: Any = None,
    convert_camera_offsets: bool = False,
    contact_telemetry: bool = True,
    randomize_background: bool = False,
    appearance_seed: int = 0,
    light_intensity: float | None = None,
    rgbd: bool = False,
) -> Any:
    """RoboLab env config for `task`, corrected for Isaac Sim 6.

    Joint-position control replaces absolute IK, terminations/subtasks/recorders
    are off (the caller records with GeminiEpisodeDatasetRecorder), and the
    background and light follow the harness scene's appearance fields.
    """
    from robolab.core.environments.config import parse_env_cfg
    from robolab.registrations.droid.auto_env_registrations_abs_ik import (
        auto_register_droid_abs_ik_envs,
    )
    from robolab.robots.droid import DroidJointPositionActionCfg

    try:
        from .robolab_contact_telemetry import install_sim6_gripper_contact_sensor
        from .sim6_camera_offsets import convert_sim5_camera_offsets
    except ImportError:  # Script execution adds this directory to sys.path.
        from robolab_contact_telemetry import install_sim6_gripper_contact_sensor  # type: ignore[no-redef]
        from sim6_camera_offsets import convert_sim5_camera_offsets  # type: ignore[no-redef]

    auto_register_droid_abs_ik_envs(
        task=task, contact_sensors=False, **({"cameras": cameras} if cameras is not None else {})
    )
    env_cfg = parse_env_cfg(task, device="cuda:0", seed=0, num_envs=1, use_fabric=True)
    # RoboLab's explicit robot/object poses came from the Sim 5 (w, x, y, z)
    # configuration contract. This Sim 6 source build consumes spawn poses as
    # (x, y, z, w); convert only the legacy-authored fields at the boundary.
    env_cfg.scene.robot.init_state.rot = (0.0, 0.0, 0.0, 1.0)
    fixture_rot = env_cfg.scene.table_fixture.init_state.rot
    env_cfg.scene.table_fixture.init_state.rot = (fixture_rot[1], fixture_rot[2], fixture_rot[3], fixture_rot[0])
    for asset_name in env_cfg.contact_object_list:
        asset_cfg = getattr(env_cfg.scene, asset_name)
        w, x, y, z = asset_cfg.init_state.rot
        asset_cfg.init_state.rot = (x, y, z, w)
    if convert_camera_offsets:
        convert_sim5_camera_offsets(env_cfg)
    if contact_telemetry:
        install_sim6_gripper_contact_sensor(env_cfg)
    if randomize_background:
        from robolab.variations.backgrounds import find_background_files

        backgrounds = find_background_files()
        current = str(env_cfg.scene.dome_light.spawn.texture_file)
        backgrounds = [path for path in backgrounds if str(path) != current]
        if not backgrounds:
            raise FileNotFoundError("No non-default RoboLab HDRI backgrounds are available")
        env_cfg.scene.dome_light.spawn.texture_file = random.Random(appearance_seed).choice(backgrounds)
    if light_intensity is not None:
        sphere_light = getattr(env_cfg.scene, "sphere_light", None)
        if sphere_light is None:
            raise RuntimeError("Requested light variation but scene has no sphere_light")
        sphere_light.spawn.intensity = light_intensity
    if rgbd:
        for camera_name in ("over_shoulder_left_camera", "wrist_cam"):
            camera_cfg = getattr(env_cfg.scene, camera_name)
            if "depth" not in camera_cfg.data_types:
                camera_cfg.data_types = [*camera_cfg.data_types, "depth"]
        exterior = env_cfg.scene.over_shoulder_left_camera
        if "instance_id_segmentation_fast" not in exterior.data_types:
            exterior.data_types = [*exterior.data_types, "instance_id_segmentation_fast"]
        exterior.renderer_cfg.colorize_instance_id_segmentation = False
        env_cfg.scene.lazy_sensor_update = False
    # Sim 6's absolute-IK bridge resolves the Robotiq control body incorrectly;
    # every runner commands joint positions and does its own IK if it needs it.
    env_cfg.actions = DroidJointPositionActionCfg()
    env_cfg.terminations = None
    env_cfg.subtasks = None
    if instruction is not None:
        env_cfg.instruction = instruction
    # RoboLab's recorder consumes the removed Sim 5 tensor API.
    env_cfg.recorders = None
    return env_cfg


def set_camera_views(env: Any) -> None:
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


def transform_asset_pose(
    env: Any, asset_name: str, offset_xy: Sequence[float], *, yaw_degrees: float = 0.0
) -> None:
    """Apply a deterministic post-reset translation and world-Z rotation."""
    if tuple(offset_xy) == (0.0, 0.0) and yaw_degrees == 0.0:
        return
    asset = env.scene[asset_name]
    root_pose_w = asset.data.root_pose_w
    root_pose_w = getattr(root_pose_w, "torch", root_pose_w).clone()
    root_pose_w[0, :2] += torch.tensor(tuple(offset_xy), dtype=root_pose_w.dtype, device=root_pose_w.device)
    if yaw_degrees != 0.0:
        current = _xyzw_to_wxyz(root_pose_w[0, 3:7])
        turn = _yaw_quaternion_wxyz(float(np.deg2rad(yaw_degrees)), like=current)
        root_pose_w[0, 3:7] = _wxyz_to_xyzw(_multiply_wxyz(turn, current))
    asset.write_root_pose_to_sim(root_pose_w)
    root_vel_w = asset.data.root_vel_w
    root_vel_w = getattr(root_vel_w, "torch", root_vel_w)
    asset.write_root_velocity_to_sim(torch.zeros_like(root_vel_w))


def apply_harness_scene(env: Any, movable: str, receptacle: str, scene: Mapping[str, Any]) -> None:
    """Place the roles as one harness scene says (see evaluate_planner plan)."""
    transform_asset_pose(
        env, movable, scene.get("movable_object_offset_xy_m", (0.0, 0.0)),
        yaw_degrees=float(scene.get("movable_object_yaw_deg", 0.0)),
    )
    transform_asset_pose(env, receptacle, scene.get("plate_offset_xy_m", (0.0, 0.0)))
