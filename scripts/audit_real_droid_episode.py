#!/usr/bin/env python3
"""Audit real DROID pilot episodes before conversion or post-training."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable

import cv2
import h5py
import numpy as np

try:
    from franka_sensor_schema import load_sensor_block
except ModuleNotFoundError:  # Support imports as scripts.audit_real_droid_episode.
    from scripts.franka_sensor_schema import load_sensor_block


REQUIRED_STATE = {
    "cartesian_position": ("observation/robot_state/cartesian_position", 6),
    "joint_positions": ("observation/robot_state/joint_positions", 7),
    "gripper_position": ("observation/robot_state/gripper_position", 1),
}
REQUIRED_ACTION = {
    "cartesian_position": ("action/cartesian_position", 6),
    "joint_position": ("action/joint_position", 7),
    "gripper_position": ("action/gripper_position", 1),
}
PANDA_JOINT_LOWER = np.array(
    [-2.8973, -1.7628, -2.8973, -3.0718, -2.8973, -0.0175, -2.8973],
    dtype=np.float64,
)
PANDA_JOINT_UPPER = np.array(
    [2.8973, 1.7628, 2.8973, -0.0698, 2.8973, 3.7525, 2.8973],
    dtype=np.float64,
)

TIMESTAMP_PATHS = (
    "sensors/franka/timestamp_s",
    "observation/timestamp/robot_timestamp_s",
    "observation/robot_state/robot_timestamp_s",
    "timestamp/robot_timestamp_s",
)
TIMESTAMP_SECOND_PATHS = (
    "observation/timestamp/robot_timestamp_seconds",
    "observation/robot_state/robot_timestamp_seconds",
    "timestamp/robot_timestamp_seconds",
)
TIMESTAMP_NANOSECOND_PATHS = (
    "observation/timestamp/robot_timestamp_nanos",
    "observation/robot_state/robot_timestamp_nanos",
    "timestamp/robot_timestamp_nanos",
)


def _dataset(group: Any, *paths: str) -> np.ndarray | None:
    for path in paths:
        if path in group:
            return np.asarray(group[path])
    return None


def video_info(path: Path) -> dict[str, float | int]:
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise RuntimeError(f"cannot open video: {path}")
    result = {
        "frames": int(capture.get(cv2.CAP_PROP_FRAME_COUNT)),
        "fps": float(capture.get(cv2.CAP_PROP_FPS)),
        "width": int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)),
        "height": int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT)),
    }
    capture.release()
    if (
        result["frames"] <= 0
        or not np.isfinite(result["fps"])
        or result["fps"] <= 0.0
    ):
        raise ValueError(f"invalid video metadata for {path}: {result}")
    return result


def _check(
    checks: list[dict[str, Any]],
    name: str,
    status: str,
    message: str,
    **metrics: Any,
) -> None:
    checks.append(
        {
            "name": name,
            "status": status,
            "message": message,
            **({"metrics": metrics} if metrics else {}),
        }
    )


def _timestamps(source: h5py.File) -> tuple[np.ndarray | None, str | None]:
    direct = _dataset(source, *TIMESTAMP_PATHS)
    if direct is not None:
        return np.asarray(direct, dtype=np.float64).reshape(-1), next(
            path for path in TIMESTAMP_PATHS if path in source
        )
    seconds = _dataset(source, *TIMESTAMP_SECOND_PATHS)
    nanos = _dataset(source, *TIMESTAMP_NANOSECOND_PATHS)
    if seconds is None or nanos is None:
        return None, None
    seconds_path = next(path for path in TIMESTAMP_SECOND_PATHS if path in source)
    nanos_path = next(path for path in TIMESTAMP_NANOSECOND_PATHS if path in source)
    timestamp = np.asarray(seconds, dtype=np.float64).reshape(-1)
    timestamp += np.asarray(nanos, dtype=np.float64).reshape(-1) * 1.0e-9
    return timestamp, f"{seconds_path}+{nanos_path}"


def _success_value(source: h5py.File) -> tuple[bool | None, str | None]:
    def parse(value: Any) -> bool:
        if isinstance(value, bytes):
            value = value.decode("utf-8", errors="replace")
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"true", "yes", "1", "success"}:
                return True
            if normalized in {"false", "no", "0", "failure"}:
                return False
            raise ValueError(f"unrecognized success value: {value!r}")
        return bool(value)

    for owner_path in ("", "metadata", "observation"):
        owner = source if not owner_path else source.get(owner_path)
        if owner is None:
            continue
        for key in ("success", "task_success", "episode_success"):
            if key in owner.attrs:
                return parse(owner.attrs[key]), f"{owner_path or '/'}@{key}"
            if key in owner:
                value = np.asarray(owner[key]).reshape(-1)
                if value.size:
                    return parse(value[-1]), f"{owner_path}/{key}".lstrip("/")
    return None, None


def _status(checks: list[dict[str, Any]]) -> str:
    statuses = {item["status"] for item in checks}
    if "fail" in statuses:
        return "fail"
    if "warn" in statuses:
        return "warn"
    return "pass"


def audit_episode(
    spec: dict[str, Any],
    *,
    expected_fps: float = 15.0,
    fps_tolerance: float = 0.2,
    min_torque_coverage: float = 0.95,
    max_joint_step_rad: float = 0.35,
    max_gap_factor: float = 2.0,
    require_external_torque: bool = True,
    require_timestamps: bool = True,
    require_actions: bool = True,
    require_success: bool = False,
    video_probe: Callable[[Path], dict[str, float | int]] = video_info,
) -> dict[str, Any]:
    paths = {
        key: Path(spec[key]).expanduser().resolve()
        for key in ("trajectory", "exterior_video", "wrist_video")
    }
    checks: list[dict[str, Any]] = []
    for key, path in paths.items():
        _check(
            checks,
            f"path.{key}",
            "pass" if path.is_file() else "fail",
            f"found {path}" if path.is_file() else f"missing {path}",
        )
    if any(not path.is_file() for path in paths.values()):
        return {
            "trajectory": str(paths["trajectory"]),
            "status": "fail",
            "ready_for_conversion": False,
            "checks": checks,
        }

    videos: dict[str, dict[str, float | int]] = {}
    for key in ("exterior_video", "wrist_video"):
        try:
            videos[key] = video_probe(paths[key])
            _check(checks, f"video.{key}.readable", "pass", "video metadata readable")
        except Exception as error:
            _check(checks, f"video.{key}.readable", "fail", str(error))

    state_length = 0
    sensor_coverage: dict[str, float] = {}
    try:
        source_file = h5py.File(paths["trajectory"], "r")
    except Exception as error:
        _check(checks, "trajectory.hdf5", "fail", f"cannot open HDF5: {error}")
        return {
            "trajectory": str(paths["trajectory"]),
            "status": "fail",
            "ready_for_conversion": False,
            "videos": videos,
            "checks": checks,
        }

    with source_file as source:
        arrays: dict[str, np.ndarray] = {}
        for name, (path, width) in REQUIRED_STATE.items():
            value = _dataset(source, path)
            if value is None:
                _check(checks, f"state.{name}", "fail", f"missing {path}")
                continue
            array = np.asarray(value)
            if name == "gripper_position" and array.ndim == 1:
                array = array[:, None]
            shape_ok = array.ndim == 2 and array.shape[1] >= width
            finite_fraction = float(np.isfinite(array).mean()) if array.size else 0.0
            status = "pass" if shape_ok and finite_fraction == 1.0 else "fail"
            _check(
                checks,
                f"state.{name}",
                status,
                f"shape={array.shape}, finite={finite_fraction:.6f}",
                shape=list(array.shape),
                finite_fraction=finite_fraction,
            )
            if shape_ok:
                arrays[name] = array[:, :width]

        lengths = {name: len(value) for name, value in arrays.items()}
        if lengths:
            state_length = min(lengths.values())
            aligned = len(set(lengths.values())) == 1
            _check(
                checks,
                "state.length_alignment",
                "pass" if aligned else "fail",
                f"state lengths={lengths}",
                lengths=lengths,
            )
        else:
            _check(checks, "state.length_alignment", "fail", "no usable state arrays")

        action_arrays: dict[str, np.ndarray] = {}
        for name, (path, width) in REQUIRED_ACTION.items():
            value = _dataset(source, path)
            if value is None:
                _check(
                    checks,
                    f"action.{name}",
                    "fail" if require_actions else "warn",
                    f"missing {path}; converter would fall back to observed state",
                )
                continue
            array = np.asarray(value)
            if name == "gripper_position" and array.ndim == 1:
                array = array[:, None]
            shape_ok = array.ndim == 2 and array.shape[1] >= width
            finite_fraction = float(np.isfinite(array).mean()) if array.size else 0.0
            status = "pass" if shape_ok and finite_fraction == 1.0 else "fail"
            _check(
                checks,
                f"action.{name}",
                status,
                f"shape={array.shape}, finite={finite_fraction:.6f}",
                shape=list(array.shape),
                finite_fraction=finite_fraction,
            )
            if shape_ok:
                action_arrays[name] = array[:, :width]

        if action_arrays:
            action_lengths = {name: len(value) for name, value in action_arrays.items()}
            actions_aligned = len(set(action_lengths.values())) == 1 and all(
                length == state_length for length in action_lengths.values()
            )
            _check(
                checks,
                "action.length_alignment",
                "pass" if actions_aligned else "fail",
                f"state={state_length}, action lengths={action_lengths}",
                state_length=state_length,
                action_lengths=action_lengths,
            )

        joints = arrays.get("joint_positions")
        if joints is not None and len(joints) and np.isfinite(joints).all():
            max_abs = float(np.max(np.abs(joints)))
            max_step = float(np.max(np.abs(np.diff(joints, axis=0)))) if len(joints) > 1 else 0.0
            limit_margin = 0.05
            limit_violations = int(
                np.sum(
                    (joints < PANDA_JOINT_LOWER - limit_margin)
                    | (joints > PANDA_JOINT_UPPER + limit_margin)
                )
            )
            plausible = limit_violations == 0 and max_step <= max_joint_step_rad
            _check(
                checks,
                "state.joint_ranges",
                "pass" if plausible else "fail",
                (
                    f"max_abs={max_abs:.4f} rad, max_step={max_step:.4f} rad, "
                    f"limit_violations={limit_violations}"
                ),
                min=np.min(joints, axis=0).tolist(),
                max=np.max(joints, axis=0).tolist(),
                max_abs_rad=max_abs,
                max_step_rad=max_step,
                limit_violations=limit_violations,
            )

        if state_length:
            try:
                sensor_block = load_sensor_block(source, state_length)
                sensor_coverage = sensor_block.coverage
                measured = sensor_coverage["joint_torque_measured"]
                external = sensor_coverage["joint_torque_external"]
                _check(
                    checks,
                    "sensors.measured_torque",
                    "pass" if measured >= min_torque_coverage else "fail",
                    f"coverage={measured:.3f}",
                    coverage=measured,
                    source=sensor_block.source_paths["joint_torque_measured"],
                )
                external_status = (
                    "pass"
                    if external >= min_torque_coverage
                    else ("fail" if require_external_torque else "warn")
                )
                _check(
                    checks,
                    "sensors.external_torque",
                    external_status,
                    f"coverage={external:.3f}",
                    coverage=external,
                    source=sensor_block.source_paths["joint_torque_external"],
                )
            except Exception as error:
                _check(checks, "sensors.schema", "fail", str(error))

        timestamp, timestamp_source = _timestamps(source)
        if timestamp is None:
            _check(
                checks,
                "timestamps.robot",
                "fail" if require_timestamps else "warn",
                "robot timestamps unavailable; dropped control samples cannot be detected",
            )
        else:
            finite = np.isfinite(timestamp)
            deltas = np.diff(timestamp[finite])
            monotonic = bool(len(deltas) and np.all(deltas > 0.0))
            median_dt = float(np.median(deltas)) if len(deltas) else None
            max_dt = float(np.max(deltas)) if len(deltas) else None
            observed_hz = 1.0 / median_dt if median_dt and median_dt > 0.0 else 0.0
            gap_count = (
                int(np.sum(deltas > median_dt * max_gap_factor))
                if median_dt and median_dt > 0.0
                else 0
            )
            timestamp_ok = (
                len(timestamp) == state_length
                and finite.all()
                and monotonic
                and gap_count == 0
                and abs(observed_hz - expected_fps)
                <= max(fps_tolerance, expected_fps * 0.1)
            )
            _check(
                checks,
                "timestamps.robot",
                "pass" if timestamp_ok else "fail",
                (
                    f"source={timestamp_source}, samples={len(timestamp)}, "
                    f"observed_hz={observed_hz:.3f}, gaps={gap_count}"
                ),
                source=timestamp_source,
                samples=len(timestamp),
                observed_hz=observed_hz,
                median_dt_s=median_dt,
                max_dt_s=max_dt,
                gap_count=gap_count,
            )

        recorded_success, success_source = _success_value(source)

    if len(videos) == 2:
        frame_counts = {key: int(value["frames"]) for key, value in videos.items()}
        frame_aligned = state_length > 0 and all(
            frames == state_length for frames in frame_counts.values()
        )
        _check(
            checks,
            "video.frame_alignment",
            "pass" if frame_aligned else "fail",
            f"trajectory={state_length}, frames={frame_counts}",
            trajectory_frames=state_length,
            **frame_counts,
        )
        fps_values = {key: float(value["fps"]) for key, value in videos.items()}
        fps_ok = all(abs(value - expected_fps) <= fps_tolerance for value in fps_values.values())
        fps_ok = fps_ok and abs(fps_values["exterior_video"] - fps_values["wrist_video"]) <= fps_tolerance
        _check(
            checks,
            "video.frame_rate",
            "pass" if fps_ok else "fail",
            f"expected={expected_fps:.3f}, actual={fps_values}",
            expected_fps=expected_fps,
            **fps_values,
        )

    expected_success = spec.get("success")
    success = bool(expected_success) if expected_success is not None else recorded_success
    if success is True:
        _check(
            checks,
            "outcome.success",
            "pass",
            f"successful demonstration ({success_source or 'manifest'})",
        )
    elif success is False:
        _check(
            checks,
            "outcome.success",
            "fail" if require_success else "warn",
            f"episode marked unsuccessful ({success_source or 'manifest'})",
        )
    else:
        _check(
            checks,
            "outcome.success",
            "fail" if require_success else "warn",
            "success outcome is not recorded",
        )

    if spec.get("instruction"):
        _check(checks, "language.instruction", "pass", "instruction provided")
    else:
        _check(
            checks,
            "language.instruction",
            "warn",
            "instruction missing; add it to the manifest before conversion",
        )

    status = _status(checks)
    return {
        "trajectory": str(paths["trajectory"]),
        "instruction": spec.get("instruction"),
        "status": status,
        "ready_for_conversion": status != "fail",
        "state_frames": state_length,
        "sensor_coverage": sensor_coverage,
        "videos": videos,
        "checks": checks,
    }


def _resolve(base: Path, value: str | Path) -> Path:
    path = Path(value).expanduser()
    return (base / path).resolve() if not path.is_absolute() else path.resolve()


def load_specs(args: argparse.Namespace) -> list[dict[str, Any]]:
    if args.manifest:
        manifest = args.manifest.expanduser().resolve()
        records = json.loads(manifest.read_text())
        if not isinstance(records, list) or not records:
            raise ValueError("manifest must be a non-empty JSON list")
        for index, record in enumerate(records):
            if "success" in record and not isinstance(record["success"], bool):
                raise ValueError(f"manifest record {index} success must be true or false")
        return [
            {
                **record,
                "trajectory": _resolve(manifest.parent, record["trajectory"]),
                "exterior_video": _resolve(manifest.parent, record["exterior_video"]),
                "wrist_video": _resolve(manifest.parent, record["wrist_video"]),
            }
            for record in records
        ]
    required = (args.trajectory, args.exterior_video, args.wrist_video)
    if any(value is None for value in required):
        raise ValueError(
            "provide --manifest, or --trajectory, --exterior-video, and --wrist-video"
        )
    return [
        {
            "trajectory": args.trajectory,
            "exterior_video": args.exterior_video,
            "wrist_video": args.wrist_video,
            "instruction": args.instruction,
        }
    ]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--trajectory", type=Path)
    parser.add_argument("--exterior-video", type=Path)
    parser.add_argument("--wrist-video", type=Path)
    parser.add_argument("--instruction")
    parser.add_argument("--output-json", type=Path)
    parser.add_argument("--expected-fps", type=float, default=15.0)
    parser.add_argument("--fps-tolerance", type=float, default=0.2)
    parser.add_argument("--min-torque-coverage", type=float, default=0.95)
    parser.add_argument("--max-joint-step-rad", type=float, default=0.35)
    parser.add_argument("--max-gap-factor", type=float, default=2.0)
    parser.add_argument(
        "--require-external-torque",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    parser.add_argument(
        "--require-timestamps",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    parser.add_argument(
        "--require-actions",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    parser.add_argument("--require-success", action="store_true")
    args = parser.parse_args()

    specs = load_specs(args)
    episodes = [
        audit_episode(
            spec,
            expected_fps=args.expected_fps,
            fps_tolerance=args.fps_tolerance,
            min_torque_coverage=args.min_torque_coverage,
            max_joint_step_rad=args.max_joint_step_rad,
            max_gap_factor=args.max_gap_factor,
            require_external_torque=args.require_external_torque,
            require_timestamps=args.require_timestamps,
            require_actions=args.require_actions,
            require_success=args.require_success,
        )
        for spec in specs
    ]
    counts = {
        status: sum(episode["status"] == status for episode in episodes)
        for status in ("pass", "warn", "fail")
    }
    report = {
        "schema_version": "1.0",
        "ready_for_conversion": counts["fail"] == 0,
        "summary": {"episodes": len(episodes), **counts},
        "episodes": episodes,
    }
    output = json.dumps(report, indent=2, allow_nan=False) + "\n"
    if args.output_json:
        destination = args.output_json.expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(output)
    sys.stdout.write(output)
    raise SystemExit(0 if report["ready_for_conversion"] else 2)


if __name__ == "__main__":
    main()
