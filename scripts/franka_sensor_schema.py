#!/usr/bin/env python3
"""Versioned force/contact observations shared by DROID and GR00T tooling.

The schema is deliberately additive: legacy episodes receive zero-filled sensor
vectors and zero validity masks, while newly collected episodes mark only the
signals that were actually observed.  Missing data is never presented as a
real zero-force measurement.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import numpy as np


SENSOR_SCHEMA_VERSION = "2.0"
SENSOR_COLUMN = "observation.sensors"
VALIDITY_COLUMN = "observation.sensor_validity"
CANONICAL_GROUP = "sensors/franka"


@dataclass(frozen=True)
class SignalSpec:
    name: str
    width: int
    aliases: tuple[str, ...]


SIGNAL_SPECS: tuple[SignalSpec, ...] = (
    SignalSpec(
        "joint_torque_measured",
        7,
        (
            f"{CANONICAL_GROUP}/joint_torque_measured",
            "observation/robot_state/motor_torques_measured",
            "observations/robot_state/motor_torques_measured",
            "robot_state/motor_torques_measured",
        ),
    ),
    SignalSpec(
        "joint_torque_commanded",
        7,
        (
            f"{CANONICAL_GROUP}/joint_torque_commanded",
            "observation/robot_state/joint_torques_computed",
            "observations/robot_state/joint_torques_computed",
            "robot_state/joint_torques_computed",
            "states/articulation/robot/applied_torque",
            "states/articulation/robot/computed_torque",
        ),
    ),
    SignalSpec(
        "joint_torque_external",
        7,
        (
            f"{CANONICAL_GROUP}/joint_torque_external",
            "observation/robot_state/motor_torques_external",
            "observations/robot_state/motor_torques_external",
            "robot_state/motor_torques_external",
        ),
    ),
    SignalSpec(
        "eef_wrench",
        6,
        (
            f"{CANONICAL_GROUP}/eef_wrench",
            "observation/robot_state/eef_wrench",
            "observation/robot_state/external_wrench",
            "robot_state/eef_wrench",
            "robot_state/external_wrench",
        ),
    ),
    SignalSpec(
        "joint_contact",
        7,
        (
            f"{CANONICAL_GROUP}/joint_contact",
            "observation/robot_state/joint_contact",
            "robot_state/joint_contact",
        ),
    ),
    SignalSpec(
        "gripper_contact_force",
        3,
        (
            f"{CANONICAL_GROUP}/gripper_contact_force",
            "observation/robot_state/gripper_contact_force",
            "robot_state/gripper_contact_force",
        ),
    ),
    SignalSpec(
        "gripper_touch",
        1,
        (
            f"{CANONICAL_GROUP}/gripper_touch",
            "observation/robot_state/gripper_touch",
            "robot_state/gripper_touch",
        ),
    ),
)

SIGNAL_SLICES: dict[str, slice] = {}
_offset = 0
for _spec in SIGNAL_SPECS:
    SIGNAL_SLICES[_spec.name] = slice(_offset, _offset + _spec.width)
    _offset += _spec.width
SENSOR_DIM = _offset
VALIDITY_DIM = len(SIGNAL_SPECS)


@dataclass(frozen=True)
class SensorFrame:
    values: np.ndarray
    validity: np.ndarray


@dataclass(frozen=True)
class SensorBlock:
    values: np.ndarray
    validity: np.ndarray
    coverage: dict[str, float]
    source_paths: dict[str, str | None]


def empty_sensor_frame() -> SensorFrame:
    return SensorFrame(
        values=np.zeros(SENSOR_DIM, dtype=np.float32),
        validity=np.zeros(VALIDITY_DIM, dtype=np.float32),
    )


def _numpy(value: Any) -> np.ndarray:
    value = getattr(value, "torch", value)
    if hasattr(value, "detach"):
        value = value.detach()
    if hasattr(value, "cpu"):
        value = value.cpu()
    if hasattr(value, "numpy"):
        value = value.numpy()
    return np.asarray(value)


def _member(source: Any, names: Sequence[str]) -> Any | None:
    for name in names:
        if isinstance(source, Mapping) and name in source:
            return source[name]
        if hasattr(source, name):
            return getattr(source, name)
    return None


_STATE_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "joint_torque_measured": (
        "motor_torques_measured",
        "tau_J",
        "joint_torque_measured",
    ),
    "joint_torque_commanded": (
        "joint_torques_computed",
        "tau_J_d",
        "joint_torque_commanded",
    ),
    "joint_torque_external": (
        "motor_torques_external",
        "tau_ext_hat_filtered",
        "joint_torque_external",
    ),
    "eef_wrench": ("eef_wrench", "external_wrench", "O_F_ext_hat_K", "K_F_ext_hat_K"),
    "joint_contact": ("joint_contact",),
    "gripper_contact_force": ("gripper_contact_force",),
    "gripper_touch": ("gripper_touch",),
}


def sensor_frame_from_robot_state(robot_state: Any) -> SensorFrame:
    """Extract available Polymetis/libfranka-style fields by duck typing."""
    frame = empty_sensor_frame()
    values = frame.values.copy()
    validity = frame.validity.copy()
    for index, spec in enumerate(SIGNAL_SPECS):
        raw = _member(robot_state, _STATE_FIELD_ALIASES[spec.name])
        if raw is None:
            continue
        array = _numpy(raw).astype(np.float32, copy=False).reshape(-1)
        if array.size != spec.width:
            raise ValueError(
                f"{spec.name} must contain {spec.width} values, got shape {array.shape}"
            )
        if not np.isfinite(array).all():
            continue
        values[SIGNAL_SLICES[spec.name]] = array
        validity[index] = 1.0
    return SensorFrame(values=values, validity=validity)


def sensor_frame_from_isaac_env(env: Any, *, touch_threshold_n: float = 0.1) -> SensorFrame:
    """Capture honest Isaac signals without inventing a measured/external torque.

    Isaac's implicit actuator exposes commanded/computed torque, which is not a
    physical torque-sensor measurement.  Therefore only the commanded channel
    is marked valid.  Contact force/touch are marked valid only when an actual
    ContactSensor is present in the scene.
    """
    frame = empty_sensor_frame()
    values = frame.values.copy()
    validity = frame.validity.copy()
    robot = env.scene["robot"]
    joint_names = list(robot.data.joint_names)
    arm_ids = [joint_names.index(f"panda_joint{i}") for i in range(1, 8)]
    torque = _numpy(robot.data.applied_torque)[0, arm_ids].astype(np.float32, copy=False)
    if torque.size == 7 and np.isfinite(torque).all():
        values[SIGNAL_SLICES["joint_torque_commanded"]] = torque
        validity[1] = 1.0

    sensors = getattr(env.scene, "sensors", {})
    batch_sensor = sensors.get("gripper__all_objs") if hasattr(sensors, "get") else None
    candidates = (
        [batch_sensor]
        if batch_sensor is not None
        else [
            sensor
            for name, sensor in sensors.items()
            if name.startswith("gripper__")
            and hasattr(getattr(sensor, "data", None), "net_forces_w")
        ]
    )
    if candidates:
        forces = []
        for sensor in candidates:
            raw = sensor.data.net_forces_w
            if raw is None:
                continue
            array = _numpy(raw)
            if array.size:
                forces.append(array.reshape(-1, 3).sum(axis=0))
        if forces:
            force = np.sum(forces, axis=0, dtype=np.float32)
            values[SIGNAL_SLICES["gripper_contact_force"]] = force
            values[SIGNAL_SLICES["gripper_touch"]] = float(
                np.linalg.norm(force) >= touch_threshold_n
            )
            validity[5:7] = 1.0
    return SensorFrame(values=values, validity=validity)


class SensorCaptureBuffer:
    def __init__(self) -> None:
        self._values: list[np.ndarray] = []
        self._validity: list[np.ndarray] = []
        self._timestamps: list[float] = []

    def append(self, frame: SensorFrame, timestamp_s: float) -> None:
        if frame.values.shape != (SENSOR_DIM,) or frame.validity.shape != (
            VALIDITY_DIM,
        ):
            raise ValueError("sensor frame has the wrong shape")
        self._values.append(frame.values.copy())
        self._validity.append(frame.validity.copy())
        self._timestamps.append(float(timestamp_s))

    def arrays(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        if not self._values:
            return (
                np.zeros((0, SENSOR_DIM), dtype=np.float32),
                np.zeros((0, VALIDITY_DIM), dtype=np.float32),
                np.zeros((0,), dtype=np.float64),
            )
        return (
            np.stack(self._values).astype(np.float32),
            np.stack(self._validity).astype(np.float32),
            np.asarray(self._timestamps, dtype=np.float64),
        )


def _dataset(group: Any, path: str) -> Any | None:
    try:
        return group[path] if path in group else None
    except (KeyError, TypeError):
        return None


def load_sensor_block(group: Any, length: int) -> SensorBlock:
    """Load canonical, raw-DROID, or RoboLab signals and mask missing rows."""
    if length < 0:
        raise ValueError("length must be non-negative")
    values = np.zeros((length, SENSOR_DIM), dtype=np.float32)
    validity = np.zeros((length, VALIDITY_DIM), dtype=np.float32)
    source_paths: dict[str, str | None] = {}
    explicit_validity = _dataset(group, f"{CANONICAL_GROUP}/validity")
    explicit = None if explicit_validity is None else _numpy(explicit_validity)
    if explicit is not None and (explicit.ndim != 2 or explicit.shape[1] != VALIDITY_DIM):
        raise ValueError(
            f"canonical sensor validity must have shape (T, {VALIDITY_DIM}), got {explicit.shape}"
        )

    for index, spec in enumerate(SIGNAL_SPECS):
        selected_path = next(
            (path for path in spec.aliases if _dataset(group, path) is not None),
            None,
        )
        source_paths[spec.name] = selected_path
        if selected_path is None:
            continue
        array = _numpy(_dataset(group, selected_path)).astype(np.float32, copy=False)
        if array.ndim == 1 and spec.width == 1:
            array = array[:, None]
        if array.ndim != 2 or array.shape[1] < spec.width:
            raise ValueError(
                f"{selected_path} must have shape (T, {spec.width}), got {array.shape}"
            )
        rows = min(length, array.shape[0])
        signal = array[:rows, : spec.width]
        finite = np.isfinite(signal).all(axis=1)
        target = values[:rows, SIGNAL_SLICES[spec.name]]
        target[finite] = signal[finite]
        validity[:rows, index] = finite.astype(np.float32)
        if explicit is not None:
            explicit_rows = min(rows, explicit.shape[0])
            validity[:explicit_rows, index] *= (explicit[:explicit_rows, index] > 0.5)
            validity[explicit_rows:rows, index] = 0.0
            invalid = validity[:rows, index] == 0.0
            target[invalid] = 0.0

    coverage = {
        spec.name: (float(validity[:, index].mean()) if length else 0.0)
        for index, spec in enumerate(SIGNAL_SPECS)
    }
    return SensorBlock(values, validity, coverage, source_paths)


def resize_sensor_arrays(
    values: np.ndarray, validity: np.ndarray, timestamps: np.ndarray, length: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Trim or invalid-pad captured samples to the recorder's authoritative length."""
    out_values = np.zeros((length, SENSOR_DIM), dtype=np.float32)
    out_validity = np.zeros((length, VALIDITY_DIM), dtype=np.float32)
    out_timestamps = np.full((length,), np.nan, dtype=np.float64)
    rows = min(length, len(values), len(validity), len(timestamps))
    out_values[:rows] = values[:rows]
    out_validity[:rows] = validity[:rows]
    out_timestamps[:rows] = timestamps[:rows]
    return out_values, out_validity, out_timestamps


def write_sensor_group(
    demo_group: Any,
    values: np.ndarray,
    validity: np.ndarray,
    timestamps: np.ndarray,
    *,
    source: str,
) -> None:
    """Write the canonical additive HDF5 group under an episode/demo group."""
    values, validity, timestamps = resize_sensor_arrays(
        np.asarray(values),
        np.asarray(validity),
        np.asarray(timestamps),
        int(demo_group.attrs["num_samples"]),
    )
    parent = demo_group.require_group("sensors").require_group("franka")
    for name in (*[spec.name for spec in SIGNAL_SPECS], "validity", "timestamp_s"):
        if name in parent:
            del parent[name]
    for spec in SIGNAL_SPECS:
        parent.create_dataset(
            spec.name,
            data=values[:, SIGNAL_SLICES[spec.name]],
            compression="gzip",
        )
    parent.create_dataset("validity", data=validity, compression="gzip")
    parent.create_dataset("timestamp_s", data=timestamps, compression="gzip")
    parent.attrs["schema_version"] = SENSOR_SCHEMA_VERSION
    parent.attrs["source"] = source
    parent.attrs["signal_order"] = json.dumps([spec.name for spec in SIGNAL_SPECS])


def sensor_modality_metadata() -> dict[str, dict[str, int | str]]:
    metadata: dict[str, dict[str, int | str]] = {}
    for spec in SIGNAL_SPECS:
        bounds = SIGNAL_SLICES[spec.name]
        metadata[spec.name] = {
            "original_key": SENSOR_COLUMN,
            "start": int(bounds.start),
            "end": int(bounds.stop),
        }
    metadata["sensor_validity"] = {
        "original_key": VALIDITY_COLUMN,
        "start": 0,
        "end": VALIDITY_DIM,
    }
    return metadata


def masked_sensor_stats(
    values: np.ndarray, validity: np.ndarray
) -> dict[str, list[float]]:
    """Compute normalization statistics without treating missing data as zero force."""
    values = np.asarray(values, dtype=np.float32)
    validity = np.asarray(validity, dtype=np.float32)
    if values.ndim != 2 or values.shape[1] != SENSOR_DIM:
        raise ValueError(f"sensor values must have shape (T, {SENSOR_DIM})")
    if validity.shape != (values.shape[0], VALIDITY_DIM):
        raise ValueError(f"sensor validity must have shape (T, {VALIDITY_DIM})")
    fields = {name: [] for name in ("mean", "std", "min", "max", "q01", "q99")}
    for spec_index, spec in enumerate(SIGNAL_SPECS):
        signal = values[:, SIGNAL_SLICES[spec.name]]
        valid_rows = validity[:, spec_index] > 0.5
        for column in range(spec.width):
            samples = signal[valid_rows, column]
            samples = samples[np.isfinite(samples)]
            if samples.size:
                numbers = (
                    samples.mean(), samples.std(), samples.min(), samples.max(),
                    np.quantile(samples, 0.01), np.quantile(samples, 0.99),
                )
            else:
                # Neutral normalization for a modality absent from this shard.
                numbers = (0.0, 1.0, 0.0, 0.0, 0.0, 0.0)
            for field, number in zip(fields, numbers):
                fields[field].append(float(number))
    return fields
