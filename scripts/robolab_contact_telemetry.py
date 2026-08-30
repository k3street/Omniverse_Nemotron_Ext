#!/usr/bin/env python3
"""Sim 6 contact-sensor configuration for RoboLab's Robotiq gripper.

RoboLab's task contact graph creates many filtered, pairwise sensors.  The
Isaac Sim 6 PhysX backend used by this repository does not reliably resolve
those legacy filter expressions.  Dataset collection only needs honest
gripper contact telemetry, so install one unfiltered sensor over both inner
finger rigid bodies instead.

Isaac-specific imports stay inside the installer so this module remains safe
to import in ordinary unit tests.
"""
from __future__ import annotations

from typing import Any, Callable

import numpy as np


GRIPPER_CONTACT_SENSOR_NAME = "gripper__all_contacts"
GRIPPER_CONTACT_PRIM_PATH = (
    "{ENV_REGEX_NS}/robot/Gripper/Robotiq_2F_85/.*_inner_finger"
)


def install_sim6_gripper_contact_sensor(
    env_cfg: Any,
    *,
    sensor_cfg_factory: Callable[..., Any] | None = None,
    debug_vis: bool = False,
) -> Any:
    """Attach one unfiltered PhysX contact sensor to both inner fingers.

    An empty filter list is intentional.  Isaac Lab supports multiple sensing
    bodies for aggregate net-force reporting, while filtered reporting is
    restricted to one sensing body per environment.
    """
    if sensor_cfg_factory is None:
        from isaaclab.sensors import ContactSensorCfg

        sensor_cfg_factory = ContactSensorCfg
    sensor_cfg = sensor_cfg_factory(
        prim_path=GRIPPER_CONTACT_PRIM_PATH,
        update_period=0.0,
        history_length=6,
        debug_vis=debug_vis,
        filter_prim_paths_expr=[],
    )
    setattr(env_cfg.scene, GRIPPER_CONTACT_SENSOR_NAME, sensor_cfg)
    return sensor_cfg


def contact_sensor_runtime_info(env: Any) -> dict[str, Any]:
    """Return compact initialization evidence without forcing a contact."""
    sensors = getattr(env.scene, "sensors", {})
    sensor = sensors.get(GRIPPER_CONTACT_SENSOR_NAME) if hasattr(sensors, "get") else None
    if sensor is None:
        return {
            "available": False,
            "name": GRIPPER_CONTACT_SENSOR_NAME,
            "body_names": [],
        }
    body_names = list(getattr(sensor, "body_names", []))
    return {
        "available": True,
        "name": GRIPPER_CONTACT_SENSOR_NAME,
        "body_names": body_names,
        "body_count": int(getattr(sensor, "num_sensors", len(body_names))),
        "filtered": bool(getattr(sensor.cfg, "filter_prim_paths_expr", [])),
    }


def contact_body_force_observation(
    env: Any,
    *,
    touch_threshold_n: float = 0.1,
) -> dict[str, Any]:
    """Expose each sensed contact body's fresh world-frame force.

    Aggregate clamp force can be nearly zero for a valid opposing pinch and can
    look large for an ineffective same-direction surface contact.  Keeping the
    runtime sensor's own body names makes this observation capability-driven
    rather than tied to a particular gripper or task object.
    """
    sensors = getattr(env.scene, "sensors", {})
    sensor = (
        sensors.get(GRIPPER_CONTACT_SENSOR_NAME)
        if hasattr(sensors, "get")
        else None
    )
    raw = getattr(getattr(sensor, "data", None), "net_forces_w", None)
    if sensor is None or raw is None:
        return {"available": False, "frame": "world", "channels": []}
    value = getattr(raw, "torch", raw)
    if hasattr(value, "detach"):
        value = value.detach()
    if hasattr(value, "cpu"):
        value = value.cpu()
    array = np.asarray(value, dtype=np.float32)
    if array.ndim == 3:
        array = array[0]
    if array.ndim != 2 or array.shape[1] != 3 or not np.isfinite(array).all():
        return {
            "available": False,
            "frame": "world",
            "channels": [],
            "error": f"unexpected net_forces_w shape {array.shape}",
        }
    names = list(getattr(sensor, "body_names", []))
    channels = []
    for index, force in enumerate(array):
        force_norm = float(np.linalg.vector_norm(force))
        channels.append(
            {
                "body": (
                    str(names[index])
                    if index < len(names)
                    else f"contact_body_{index}"
                ),
                "force_xyz_n": force.tolist(),
                "force_n": force_norm,
                "touch": force_norm >= touch_threshold_n,
            }
        )
    pairwise_cosine = None
    magnitude_ratio = None
    active = [item for item in channels if item["touch"]]
    if len(active) == 2:
        first = np.asarray(active[0]["force_xyz_n"], dtype=np.float64)
        second = np.asarray(active[1]["force_xyz_n"], dtype=np.float64)
        first_norm = float(active[0]["force_n"])
        second_norm = float(active[1]["force_n"])
        denominator = first_norm * second_norm
        if denominator > 0.0:
            pairwise_cosine = float(np.dot(first, second) / denominator)
            magnitude_ratio = min(first_norm, second_norm) / max(
                first_norm, second_norm
            )
    return {
        "available": True,
        "frame": "world",
        "touch_threshold_n": float(touch_threshold_n),
        "active_body_count": sum(bool(item["touch"]) for item in channels),
        "pairwise_force_direction_cosine": pairwise_cosine,
        "force_magnitude_ratio_min_over_max": magnitude_ratio,
        "metric_semantics": {
            "pairwise_force_direction_cosine": (
                "-1 means opposing, 0 orthogonal, +1 same-direction"
            ),
            "force_magnitude_ratio_min_over_max": (
                "0 means highly imbalanced, 1 balanced"
            ),
        },
        "channels": channels,
    }
