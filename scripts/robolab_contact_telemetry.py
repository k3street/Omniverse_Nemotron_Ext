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
