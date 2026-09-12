"""Sim 6 boundary fix for RoboLab camera offsets authored in the Sim 5 contract.

RoboLab authors ``TiledCameraCfg.OffsetCfg.rot`` as (w, x, y, z). This Isaac
Sim 6 source build consumes the tuple as (x, y, z, w) — the same boundary
mismatch the composed-execution runner already corrects for robot, fixture,
and object spawn poses. Read the wrong way, the over-shoulder-left quaternion
becomes a 180-degree roll about the optical axis (an upside-down image), the
right camera ends up pointing at the ceiling, and the wrist camera loses the
gripper from its frame — so a policy trained on RoboLab views sees nothing it
recognises. Convert once, before the environment is created.
"""

from __future__ import annotations

from typing import Any, Sequence

POLICY_CAMERA_NAMES = (
    "wrist_cam",
    "over_shoulder_left_camera",
    "over_shoulder_right_camera",
)


def convert_sim5_camera_offsets(
    env_cfg: Any, camera_names: Sequence[str] = POLICY_CAMERA_NAMES
) -> list[str]:
    """Reorder each present camera's offset.rot from (w,x,y,z) to (x,y,z,w).

    Not idempotent by design: apply exactly once per environment config,
    at the same boundary where spawn poses are converted.
    """
    converted: list[str] = []
    for name in camera_names:
        camera_cfg = getattr(env_cfg.scene, name, None)
        offset = getattr(camera_cfg, "offset", None)
        rot = getattr(offset, "rot", None)
        if rot is None or len(rot) != 4:
            continue
        w, x, y, z = rot
        offset.rot = (x, y, z, w)
        converted.append(name)
    return converted
