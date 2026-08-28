#!/usr/bin/env python3
"""Runtime bridge from RoboLab force/contact terms to GR00T state keys.

The module is import-safe outside Isaac Sim. Isaac-specific imports happen only
inside :func:`install_sensor_observations`, after AppLauncher has started.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import numpy as np

try:
    from franka_sensor_schema import SIGNAL_SPECS, VALIDITY_DIM
except ModuleNotFoundError:  # Support imports as scripts.robolab_groot_sensor_bridge.
    from scripts.franka_sensor_schema import SIGNAL_SPECS, VALIDITY_DIM


SENSOR_WIDTHS = {spec.name: spec.width for spec in SIGNAL_SPECS}
STATE_FIELD_ALIASES = {
    "joint_torque_measured": ("joint_torque_measured", "motor_torques_measured"),
    "joint_torque_commanded": ("joint_torque_commanded", "joint_torques_computed"),
    "joint_torque_external": ("joint_torque_external", "motor_torques_external"),
    "eef_wrench": ("eef_wrench", "external_wrench"),
    "joint_contact": ("joint_contact",),
    "gripper_contact_force": ("gripper_contact_force",),
    "gripper_touch": ("gripper_touch",),
}


def _torch(value: Any):
    import torch

    if isinstance(value, torch.Tensor):
        return value
    try:
        import warp as wp

        return wp.to_torch(value)
    except (ImportError, TypeError):
        return torch.as_tensor(value)


def _robot_and_arm_ids(env: Any):
    robot = env.scene["robot"]
    joint_names = list(robot.data.joint_names)
    arm_ids = [joint_names.index(f"panda_joint{i}") for i in range(1, 8)]
    return robot, arm_ids


def _zeros(env: Any, width: int):
    robot, _ = _robot_and_arm_ids(env)
    reference = _torch(robot.data.joint_pos)
    return reference.new_zeros((reference.shape[0], width))


def joint_torque_measured(env: Any):
    return _zeros(env, 7)


def joint_torque_commanded(env: Any):
    robot, arm_ids = _robot_and_arm_ids(env)
    return _torch(robot.data.applied_torque)[:, arm_ids]


def joint_torque_external(env: Any):
    return _zeros(env, 7)


def eef_wrench(env: Any):
    return _zeros(env, 6)


def joint_contact(env: Any):
    return _zeros(env, 7)


def _contact_force_and_validity(env: Any):
    import torch

    zeros = _zeros(env, 3)
    sensors = getattr(env.scene, "sensors", {})
    sensor = sensors.get("gripper__all_objs") if hasattr(sensors, "get") else None
    if sensor is None or getattr(getattr(sensor, "data", None), "net_forces_w", None) is None:
        return zeros, zeros.new_zeros((zeros.shape[0], 1))
    force = _torch(sensor.data.net_forces_w)
    while force.ndim > 2:
        force = force.sum(dim=1)
    if force.ndim != 2 or force.shape[-1] != 3:
        return zeros, zeros.new_zeros((zeros.shape[0], 1))
    finite = torch.isfinite(force).all(dim=1, keepdim=True)
    force = torch.where(finite, force, torch.zeros_like(force))
    return force.to(dtype=zeros.dtype, device=zeros.device), finite.to(dtype=zeros.dtype)


def gripper_contact_force(env: Any):
    return _contact_force_and_validity(env)[0]


def gripper_touch(env: Any, threshold_n: float = 0.1):
    import torch

    force, validity = _contact_force_and_validity(env)
    touch = (torch.linalg.vector_norm(force, dim=1, keepdim=True) >= threshold_n).to(
        dtype=force.dtype
    )
    return touch * validity


def sensor_validity(env: Any):
    import torch

    commanded = joint_torque_commanded(env)
    _, contact_validity = _contact_force_and_validity(env)
    validity = commanded.new_zeros((commanded.shape[0], VALIDITY_DIM))
    validity[:, 1] = torch.isfinite(commanded).all(dim=1).to(commanded.dtype)
    validity[:, 5] = contact_validity[:, 0]
    validity[:, 6] = contact_validity[:, 0]
    return validity


def install_sensor_observations() -> type:
    """Replace RoboLab's DROID proprio config before task registration."""
    from isaaclab.managers import ObservationTermCfg as ObsTerm
    from isaaclab.utils import configclass
    import robolab.robots.droid as droid

    @configclass
    class SensorAwareProprioceptionObservationCfg(droid.ProprioceptionObservationCfg):
        joint_torque_measured = ObsTerm(func=joint_torque_measured)
        joint_torque_commanded = ObsTerm(func=joint_torque_commanded)
        joint_torque_external = ObsTerm(func=joint_torque_external)
        eef_wrench = ObsTerm(func=eef_wrench)
        joint_contact = ObsTerm(func=joint_contact)
        gripper_contact_force = ObsTerm(func=gripper_contact_force)
        gripper_touch = ObsTerm(func=gripper_touch)
        sensor_validity = ObsTerm(func=sensor_validity)

    droid.ProprioceptionObservationCfg = SensorAwareProprioceptionObservationCfg
    return SensorAwareProprioceptionObservationCfg


def _mapping_candidates(raw_obs: Any) -> list[Mapping]:
    if not isinstance(raw_obs, Mapping):
        return []
    candidates = [raw_obs]
    for value in raw_obs.values():
        if isinstance(value, Mapping):
            candidates.append(value)
            for nested in value.values():
                if isinstance(nested, Mapping):
                    candidates.append(nested)
    return candidates


def _select_env(value: Any, env_id: int) -> np.ndarray:
    if hasattr(value, "detach"):
        value = value.detach().cpu().numpy()
    else:
        value = np.asarray(value)
    if value.ndim >= 2:
        value = value[env_id]
    return np.asarray(value, dtype=np.float32).reshape(-1)


def extract_sensor_state(raw_obs: Any, *, env_id: int = 0) -> dict[str, np.ndarray]:
    """Extract simulator terms or raw-DROID robot-state fields with honest masks."""
    candidates = _mapping_candidates(raw_obs)
    state: dict[str, np.ndarray] = {}
    inferred_validity = np.zeros(VALIDITY_DIM, dtype=np.float32)
    for index, spec in enumerate(SIGNAL_SPECS):
        selected = None
        for alias in STATE_FIELD_ALIASES[spec.name]:
            selected = next((group[alias] for group in candidates if alias in group), None)
            if selected is not None:
                break
        if selected is None:
            state[spec.name] = np.zeros(spec.width, dtype=np.float32)
            continue
        array = _select_env(selected, env_id)
        if array.size != spec.width or not np.isfinite(array).all():
            state[spec.name] = np.zeros(spec.width, dtype=np.float32)
            continue
        state[spec.name] = array
        inferred_validity[index] = 1.0

    explicit = next(
        (group["sensor_validity"] for group in candidates if "sensor_validity" in group),
        None,
    )
    if explicit is not None:
        validity = _select_env(explicit, env_id)
        if validity.size != VALIDITY_DIM or not np.isfinite(validity).all():
            validity = inferred_validity
    else:
        validity = inferred_validity
    state["sensor_validity"] = np.clip(validity, 0.0, 1.0).astype(np.float32)
    for index, spec in enumerate(SIGNAL_SPECS):
        if state["sensor_validity"][index] < 0.5:
            state[spec.name].fill(0.0)
    return state


def pack_sensor_state(sensor_state: Mapping[str, np.ndarray]) -> dict[str, np.ndarray]:
    """Create flat GR00T sim-wrapper state keys with shape ``(B, T, D)``."""
    packed = {
        f"state.{spec.name}": np.asarray(sensor_state[spec.name], dtype=np.float32)[
            None, None, :
        ]
        for spec in SIGNAL_SPECS
    }
    packed["state.sensor_validity"] = np.asarray(
        sensor_state["sensor_validity"], dtype=np.float32
    )[None, None, :]
    return packed


def make_sensor_aware_client(base_client_class: type) -> type:
    """Return a GR00T client subclass that appends sensor state to every replan."""

    class SensorAwareGR00TDroidJointposClient(base_client_class):
        def _extract_observation(self, raw_obs: dict, *, env_id: int = 0) -> dict:
            extracted = super()._extract_observation(raw_obs, env_id=env_id)
            extracted["sensor_state"] = extract_sensor_state(raw_obs, env_id=env_id)
            return extracted

        def _pack_request(self, extracted_obs: dict, instruction: str) -> dict:
            request = super()._pack_request(extracted_obs, instruction)
            request.update(pack_sensor_state(extracted_obs["sensor_state"]))
            return request

    SensorAwareGR00TDroidJointposClient.__name__ = "SensorAwareGR00TDroidJointposClient"
    return SensorAwareGR00TDroidJointposClient
