#!/usr/bin/env python3
"""Collect many admitted Gemini-supervised episodes for GR00T fine-tuning.

Each attempt launches the live Gemini/Isaac Sim runner with a deterministic
scene variation.  The runner, not this orchestrator, owns the success gate and
publishes an episode only after the physical task completes.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = REPO_ROOT / "launch_gemini_robotics_robolab.sh"


def radical_inverse(index: int, base: int) -> float:
    """Deterministic low-discrepancy scalar in [0, 1)."""
    if index < 0 or base < 2:
        raise ValueError("index must be non-negative and base must be >= 2")
    value = 0.0
    factor = 1.0 / base
    while index:
        index, digit = divmod(index, base)
        value += digit * factor
        factor /= base
    return value


def _range(unit: float, low: float, high: float) -> float:
    return low + unit * (high - low)


def plan_variations(
    attempts: int,
    *,
    seed: int,
    object_xy: float,
    plate_xy: float,
    yaw_degrees: float,
    light_min: float,
    light_max: float,
    start_index: int = 0,
) -> list[dict[str, Any]]:
    if attempts <= 0:
        raise ValueError("attempts must be positive")
    if min(object_xy, plate_xy, yaw_degrees) < 0:
        raise ValueError("pose ranges must be non-negative")
    if light_min <= 0 or light_max < light_min:
        raise ValueError("light range must be positive and ordered")
    primes = (2, 3, 5, 7, 11, 13)
    variations = []
    offset = max(0, seed) * 131 + 1
    for attempt in range(attempts):
        sample = [radical_inverse(offset + attempt, base) for base in primes]
        light_log = _range(sample[5], math.log(light_min), math.log(light_max))
        variations.append(
            {
                "attempt": attempt,
                "episode_index": start_index + attempt,
                "movable_object_offset_xy_m": [
                    _range(sample[0], -object_xy, object_xy),
                    _range(sample[1], -object_xy, object_xy),
                ],
                "plate_offset_xy_m": [
                    _range(sample[2], -plate_xy, plate_xy),
                    _range(sample[3], -plate_xy, plate_xy),
                ],
                "movable_object_yaw_deg": _range(
                    sample[4], -yaw_degrees, yaw_degrees
                ),
                "sphere_light_intensity": math.exp(light_log),
                "appearance_seed": seed * 100_000 + attempt,
            }
        )
    return variations


def command_for_variation(
    variation: dict[str, Any],
    *,
    episode_dir: Path,
    artifact_dir: Path,
    headless: bool,
    randomize_background: bool,
    movable_object_asset: str = "banana",
    movable_object_label: str | None = None,
    instruction: str | None = None,
) -> list[str]:
    command = [
        str(LAUNCHER),
        "--movable-object-asset",
        movable_object_asset,
        "--movable-object-offset",
        *(f"{value:.8f}" for value in variation["movable_object_offset_xy_m"]),
        "--plate-offset",
        *(f"{value:.8f}" for value in variation["plate_offset_xy_m"]),
        "--movable-object-yaw-deg",
        f"{variation['movable_object_yaw_deg']:.8f}",
        "--light-intensity",
        f"{variation['sphere_light_intensity']:.8f}",
        "--appearance-seed",
        str(variation["appearance_seed"]),
        "--training-episode-dir",
        str(episode_dir),
        "--episode-index",
        str(variation["episode_index"]),
        "--artifact-dir",
        str(artifact_dir),
        "--no-periodic-motion-observations",
        "--no-ros2-sensor-ingress",
        "--linger-steps",
        "0",
    ]
    if movable_object_label:
        command.extend(("--movable-object-label", movable_object_label))
    if instruction:
        command.extend(("--instruction", instruction))
    if headless:
        command.append("--headless")
    if randomize_background:
        command.append("--randomize-background")
    return command


def contact_admission_for_episode(
    episode_dir: Path, episode_index: int
) -> dict[str, Any] | None:
    """Read the recorder's append-only contact gate evidence for an episode."""
    manifest = episode_dir / "collection_manifest.jsonl"
    if not manifest.is_file():
        return None
    match = None
    for line in manifest.read_text().splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("episode_index") == episode_index:
            match = row.get("contact_telemetry")
    if not isinstance(match, dict):
        return None
    return match


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target-successes", type=int, default=100)
    parser.add_argument("--max-attempt-multiplier", type=float, default=2.0)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--start-index", type=int, default=0)
    parser.add_argument(
        "--output", type=Path, default=REPO_ROOT / "artifacts/gemini_groot_campaign"
    )
    parser.add_argument(
        "--object-xy-range",
        "--banana-xy-range",
        dest="object_xy_range",
        type=float,
        default=0.06,
    )
    parser.add_argument("--plate-xy-range", type=float, default=0.05)
    parser.add_argument(
        "--object-yaw-range-deg",
        "--banana-yaw-range-deg",
        dest="object_yaw_range_deg",
        type=float,
        default=90.0,
    )
    parser.add_argument("--movable-object-asset", default="banana")
    parser.add_argument("--movable-object-label")
    parser.add_argument(
        "--instruction",
        help=(
            "Natural-language task and grasp guidance forwarded unchanged to "
            "every fresh observation-bound model decision."
        ),
    )
    parser.add_argument("--light-min", type=float, default=1800.0)
    parser.add_argument("--light-max", type=float, default=8500.0)
    parser.add_argument("--visible-first", action="store_true")
    parser.add_argument("--fixed-background", action="store_true")
    parser.add_argument("--enable-passive-critic", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.target_successes <= 0 or args.max_attempt_multiplier < 1.0:
        parser.error("target-successes must be positive and multiplier must be >= 1")
    output = args.output.expanduser().resolve()
    episode_dir = output / "episodes"
    attempts_dir = output / "attempts"
    output.mkdir(parents=True, exist_ok=True)
    episode_dir.mkdir(parents=True, exist_ok=True)
    attempts = int(math.ceil(args.target_successes * args.max_attempt_multiplier))
    variations = plan_variations(
        attempts,
        seed=args.seed,
        object_xy=args.object_xy_range,
        plate_xy=args.plate_xy_range,
        yaw_degrees=args.object_yaw_range_deg,
        light_min=args.light_min,
        light_max=args.light_max,
        start_index=args.start_index,
    )
    plan = {
        "schema_version": 4,
        "scene_roles": {
            "movable_object": {
                "asset": args.movable_object_asset,
                "label": args.movable_object_label,
            },
            "target_receptacle": {"asset": "plate_large", "label": "white plate"},
        },
        "teacher": "gemini-robotics-er-2-preview semantic supervision",
        "instruction": args.instruction,
        "executor": (
            "runtime-registered, model-configurable bounded motion and actuator tools"
        ),
        "admission": (
            "measured RGB-D/contact outcome predicates, clean release, and valid "
            "nonzero gripper-contact telemetry; failures are not episodes"
        ),
        "sensor_ingress": "simulator-native; ROS 2 deferred",
        "replanning": "event-driven local invalidation; periodic polling disabled",
        "target_successes": args.target_successes,
        "maximum_attempts": attempts,
        "implemented_variations": [
            "movable_object_identity",
            "movable_object_xy",
            "movable_object_yaw",
            "plate_xy",
            "sphere_light_intensity",
            "HDRI_background",
        ],
        "not_yet_implemented": [
            "receptacle_identity",
            "table_material",
            "receptacle_material",
        ],
        "variations": variations,
    }
    (output / "campaign_plan.json").write_text(json.dumps(plan, indent=2) + "\n")
    environment = os.environ.copy()
    environment["ROBOT_SEQUENCE_CRITIC"] = "1" if args.enable_passive_critic else "0"
    results = []
    success_count = 0
    for variation in variations:
        if success_count >= args.target_successes:
            break
        episode_index = variation["episode_index"]
        hdf5_path = episode_dir / f"run_{episode_index}.hdf5"
        if hdf5_path.is_file():
            contact_admission = contact_admission_for_episode(
                episode_dir, episode_index
            )
            if contact_admission and contact_admission.get("passed") is True:
                success_count += 1
                results.append(
                    {
                        **variation,
                        "status": "already_complete",
                        "contact_telemetry": contact_admission,
                    }
                )
                continue
            raise RuntimeError(
                f"Existing episode {hdf5_path} has no passing contact admission evidence"
            )
        artifact_dir = attempts_dir / f"attempt_{variation['attempt']:06d}"
        command = command_for_variation(
            variation,
            episode_dir=episode_dir,
            artifact_dir=artifact_dir,
            headless=not (args.visible_first and variation["attempt"] == 0),
            randomize_background=not args.fixed_background,
            movable_object_asset=args.movable_object_asset,
            movable_object_label=args.movable_object_label,
            instruction=args.instruction,
        )
        if args.dry_run:
            results.append({**variation, "status": "planned", "command": command})
            continue
        artifact_dir.mkdir(parents=True, exist_ok=True)
        completed = subprocess.run(command, cwd=REPO_ROOT, env=environment, check=False)
        contact_admission = contact_admission_for_episode(episode_dir, episode_index)
        admitted = (
            completed.returncode == 0
            and hdf5_path.is_file()
            and contact_admission is not None
            and contact_admission.get("passed") is True
        )
        success_count += int(admitted)
        results.append(
            {
                **variation,
                "status": "success" if admitted else "failed_not_admitted",
                "returncode": completed.returncode,
                "contact_telemetry": contact_admission,
            }
        )
        (output / "campaign_results.json").write_text(
            json.dumps(
                {
                    "target_successes": args.target_successes,
                    "successful_episodes": success_count,
                    "results": results,
                },
                indent=2,
            )
            + "\n"
        )
    if args.dry_run:
        (output / "campaign_results.json").write_text(
            json.dumps({"successful_episodes": 0, "results": results}, indent=2) + "\n"
        )
        print(f"Planned {len(results)} attempts in {output}")
        return 0
    print(
        f"Collected {success_count}/{args.target_successes} admitted Gemini episodes "
        f"after {len(results)} attempts in {episode_dir}"
    )
    return 0 if success_count >= args.target_successes else 2


if __name__ == "__main__":
    raise SystemExit(main())
