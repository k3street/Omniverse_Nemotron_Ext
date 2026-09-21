#!/usr/bin/env python3
"""Validate and execute embodiment-independent Gemini training campaigns.

The matrix keeps task intent separate from scene bindings and embodiments.  A
cell is executable only when a registered runtime adapter supports its world
effect, completion evaluator, arm count, scene, and dataset adapter.  Missing
support is reported while planning instead of silently falling back to the
Franka runner.
"""
from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SPEC = REPO_ROOT / "config/gemini_training_matrix.example.json"
SCHEMA_VERSION = "gemini-training-matrix.v1"


class SpecError(ValueError):
    """Raised when a training-matrix spec violates its contract."""


# This is an executable-support registry, not a robot description.  A new
# embodiment becomes runnable only after its real scene/runtime and recorder
# adapters are registered here and covered by tests.
RUNTIME_ADAPTERS: dict[str, dict[str, Any]] = {
    "robolab_franka_guarded_world_effect_v1": {
        "executor": "gemini_groot_campaign_v1",
        "arm_counts": {1},
        "world_capability_ids": {
            "world_relation.realize_inside",
            "world_relation.realize_left_of",
        },
        "completion_evaluator_ids": {
            "rgbd.visible_geometry_inside",
            "rgbd.visible_geometry_left_of",
        },
        "dataset_adapter_ids": {"robolab_droid_hdf5_v1"},
    }
}


_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_.-]*$")
_TASK_KEYS = {
    "task_id",
    "instruction",
    "required_world_capability_id",
    "completion_predicate",
}
_FORBIDDEN_TASK_KEYS = {
    "arm",
    "arm_count",
    "controller",
    "embodiment",
    "embodiment_id",
    "end_effector",
    "gripper",
    "joint_names",
    "robot",
    "runtime_adapter_id",
}


def _mapping(value: Any, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SpecError(f"{where} must be an object")
    return value


def _records(value: Any, where: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise SpecError(f"{where} must be a non-empty array")
    return [_mapping(item, f"{where}[{index}]") for index, item in enumerate(value)]


def _exact_keys(
    record: dict[str, Any],
    *,
    required: set[str],
    optional: set[str] = frozenset(),
    where: str,
) -> None:
    missing = required - record.keys()
    unknown = record.keys() - required - optional
    if missing:
        raise SpecError(f"{where} is missing keys: {', '.join(sorted(missing))}")
    if unknown:
        raise SpecError(f"{where} has unknown keys: {', '.join(sorted(unknown))}")


def _identifier(value: Any, where: str) -> str:
    if not isinstance(value, str) or not _ID_PATTERN.fullmatch(value):
        raise SpecError(f"{where} must be a lowercase identifier")
    return value


def _positive_number(value: Any, where: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
        raise SpecError(f"{where} must be a positive number")
    return float(value)


def _unique_by_id(
    records: list[dict[str, Any]], key: str, where: str
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for index, record in enumerate(records):
        record_id = _identifier(record.get(key), f"{where}[{index}].{key}")
        if record_id in result:
            raise SpecError(f"duplicate {key}: {record_id}")
        result[record_id] = record
    return result


def validate_spec(spec: dict[str, Any]) -> dict[str, dict[str, dict[str, Any]]]:
    """Validate a matrix spec and return records indexed by stable IDs."""
    _exact_keys(
        spec,
        required={"schema_version", "tasks", "scenes", "embodiments", "campaigns"},
        optional={"description"},
        where="spec",
    )
    if spec["schema_version"] != SCHEMA_VERSION:
        raise SpecError(
            f"schema_version must be {SCHEMA_VERSION!r}, got {spec['schema_version']!r}"
        )

    tasks = _records(spec["tasks"], "tasks")
    scenes = _records(spec["scenes"], "scenes")
    embodiments = _records(spec["embodiments"], "embodiments")
    campaigns = _records(spec["campaigns"], "campaigns")
    task_by_id = _unique_by_id(tasks, "task_id", "tasks")
    scene_by_id = _unique_by_id(scenes, "scene_id", "scenes")
    embodiment_by_id = _unique_by_id(embodiments, "embodiment_id", "embodiments")
    campaign_by_id = _unique_by_id(campaigns, "campaign_id", "campaigns")

    for task_id, task in task_by_id.items():
        forbidden = task.keys() & _FORBIDDEN_TASK_KEYS
        if forbidden:
            raise SpecError(
                f"task {task_id} contains embodiment fields: "
                f"{', '.join(sorted(forbidden))}"
            )
        _exact_keys(task, required=_TASK_KEYS, where=f"task {task_id}")
        if not isinstance(task["instruction"], str) or not task["instruction"].strip():
            raise SpecError(f"task {task_id}.instruction must be non-empty")
        _identifier(
            task["required_world_capability_id"],
            f"task {task_id}.required_world_capability_id",
        )
        predicate = _mapping(
            task["completion_predicate"], f"task {task_id}.completion_predicate"
        )
        _exact_keys(
            predicate,
            required={
                "relation",
                "subject_role",
                "reference_role",
                "evaluator_id",
            },
            where=f"task {task_id}.completion_predicate",
        )
        for key in ("relation", "subject_role", "reference_role", "evaluator_id"):
            _identifier(predicate[key], f"task {task_id}.completion_predicate.{key}")
        if predicate["subject_role"] == predicate["reference_role"]:
            raise SpecError(f"task {task_id} must use distinct semantic roles")

    for scene_id, scene in scene_by_id.items():
        _exact_keys(
            scene,
            required={"scene_id", "task_bindings", "compatible_runtime_adapter_ids"},
            optional={"description"},
            where=f"scene {scene_id}",
        )
        compatible = scene["compatible_runtime_adapter_ids"]
        if not isinstance(compatible, list) or not all(
            isinstance(item, str) and item for item in compatible
        ):
            raise SpecError(
                f"scene {scene_id}.compatible_runtime_adapter_ids must be a "
                "string array"
            )
        bindings = _mapping(scene["task_bindings"], f"scene {scene_id}.task_bindings")
        if not bindings:
            raise SpecError(f"scene {scene_id}.task_bindings must not be empty")
        for task_id, binding_value in bindings.items():
            if task_id not in task_by_id:
                raise SpecError(f"scene {scene_id} binds unknown task {task_id}")
            binding = _mapping(
                binding_value, f"scene {scene_id}.task_bindings.{task_id}"
            )
            _exact_keys(
                binding,
                required={"robolab_task", "roles"},
                where=f"scene {scene_id}.task_bindings.{task_id}",
            )
            if not isinstance(binding["robolab_task"], str) or not binding[
                "robolab_task"
            ]:
                raise SpecError(f"scene {scene_id} task {task_id} needs robolab_task")
            roles = _mapping(
                binding["roles"], f"scene {scene_id}.task_bindings.{task_id}.roles"
            )
            predicate = task_by_id[task_id]["completion_predicate"]
            for role_id in (predicate["subject_role"], predicate["reference_role"]):
                if role_id not in roles:
                    raise SpecError(
                        f"scene {scene_id} task {task_id} does not bind role {role_id}"
                    )
                role = _mapping(roles[role_id], f"scene {scene_id} role {role_id}")
                _exact_keys(
                    role,
                    required={"asset", "label"},
                    where=f"scene {scene_id} role {role_id}",
                )
                if not all(isinstance(role[key], str) and role[key] for key in role):
                    raise SpecError(
                        f"scene {scene_id} role {role_id} fields must be strings"
                    )

    for embodiment_id, embodiment in embodiment_by_id.items():
        _exact_keys(
            embodiment,
            required={
                "embodiment_id",
                "label",
                "arm_count",
                "end_effectors",
                "runtime_adapter_id",
                "dataset_adapter_id",
            },
            optional={"asset_path"},
            where=f"embodiment {embodiment_id}",
        )
        if not isinstance(embodiment["label"], str) or not embodiment["label"]:
            raise SpecError(f"embodiment {embodiment_id}.label must be non-empty")
        if not isinstance(embodiment["arm_count"], int) or isinstance(
            embodiment["arm_count"], bool
        ) or embodiment["arm_count"] <= 0:
            raise SpecError(f"embodiment {embodiment_id}.arm_count must be positive")
        end_effectors = embodiment["end_effectors"]
        if not isinstance(end_effectors, list) or len(end_effectors) != embodiment[
            "arm_count"
        ]:
            raise SpecError(
                f"embodiment {embodiment_id} needs one end_effector per arm"
            )
        for index, end_effector in enumerate(end_effectors):
            item = _mapping(
                end_effector, f"embodiment {embodiment_id}.end_effectors[{index}]"
            )
            _exact_keys(
                item,
                required={"end_effector_id", "kind"},
                where=f"embodiment {embodiment_id}.end_effectors[{index}]",
            )
            _identifier(item["end_effector_id"], "end_effector_id")
            _identifier(item["kind"], "end_effector kind")
        for key in ("runtime_adapter_id", "dataset_adapter_id"):
            value = embodiment[key]
            if value is not None:
                _identifier(value, f"embodiment {embodiment_id}.{key}")
        if "asset_path" in embodiment and (
            not isinstance(embodiment["asset_path"], str)
            or not embodiment["asset_path"]
        ):
            raise SpecError(f"embodiment {embodiment_id}.asset_path must be non-empty")

    campaign_optional = {
        "enable_passive_critic",
        "fixed_background",
        "light_max",
        "light_min",
        "max_attempt_multiplier",
        "object_xy_range",
        "object_yaw_range_deg",
        "reference_xy_range",
        "seed",
        "visible_first",
    }
    for campaign_id, campaign in campaign_by_id.items():
        _exact_keys(
            campaign,
            required={
                "campaign_id",
                "task_id",
                "scene_id",
                "embodiment_id",
                "target_successes",
            },
            optional=campaign_optional,
            where=f"campaign {campaign_id}",
        )
        for key, index in (
            ("task_id", task_by_id),
            ("scene_id", scene_by_id),
            ("embodiment_id", embodiment_by_id),
        ):
            if campaign[key] not in index:
                raise SpecError(
                    f"campaign {campaign_id} references unknown {key} {campaign[key]!r}"
                )
        if not isinstance(campaign["target_successes"], int) or isinstance(
            campaign["target_successes"], bool
        ) or campaign["target_successes"] <= 0:
            raise SpecError(f"campaign {campaign_id}.target_successes must be positive")
        for key in (
            "max_attempt_multiplier",
            "light_min",
            "light_max",
        ):
            if key in campaign:
                _positive_number(campaign[key], f"campaign {campaign_id}.{key}")
        for key in ("object_xy_range", "object_yaw_range_deg", "reference_xy_range"):
            if key in campaign and (
                not isinstance(campaign[key], (int, float))
                or isinstance(campaign[key], bool)
                or campaign[key] < 0
            ):
                raise SpecError(f"campaign {campaign_id}.{key} must be non-negative")
        for key in ("enable_passive_critic", "fixed_background", "visible_first"):
            if key in campaign and not isinstance(campaign[key], bool):
                raise SpecError(f"campaign {campaign_id}.{key} must be boolean")

    return {
        "tasks": task_by_id,
        "scenes": scene_by_id,
        "embodiments": embodiment_by_id,
        "campaigns": campaign_by_id,
    }


def load_spec(path: Path) -> dict[str, Any]:
    try:
        spec = json.loads(path.read_text())
    except OSError as exc:
        raise SpecError(f"cannot read spec {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise SpecError(f"invalid JSON in {path}: {exc}") from exc
    return _mapping(spec, "spec")


def _blocker(code: str, detail: str) -> dict[str, str]:
    return {"code": code, "detail": detail}


def assess_campaign(
    campaign: dict[str, Any],
    indexes: dict[str, dict[str, dict[str, Any]]],
    *,
    repo_root: Path = REPO_ROOT,
) -> tuple[list[dict[str, str]], dict[str, Any] | None]:
    """Return exact blockers and the scene binding for a matrix cell."""
    task = indexes["tasks"][campaign["task_id"]]
    scene = indexes["scenes"][campaign["scene_id"]]
    embodiment = indexes["embodiments"][campaign["embodiment_id"]]
    binding = scene["task_bindings"].get(campaign["task_id"])
    blockers: list[dict[str, str]] = []
    if binding is None:
        blockers.append(
            _blocker(
                "scene_task_binding_missing",
                f"scene {campaign['scene_id']} does not bind task "
                f"{campaign['task_id']}",
            )
        )

    asset_path = embodiment.get("asset_path")
    if asset_path and not (repo_root / asset_path).is_file():
        blockers.append(
            _blocker(
                "embodiment_asset_missing",
                f"embodiment asset does not exist: {asset_path}",
            )
        )

    adapter_id = embodiment["runtime_adapter_id"]
    adapter = RUNTIME_ADAPTERS.get(adapter_id) if adapter_id else None
    if adapter_id is None:
        blockers.append(
            _blocker(
                "runtime_adapter_missing",
                f"embodiment {campaign['embodiment_id']} has no runtime adapter",
            )
        )
    elif adapter is None:
        blockers.append(
            _blocker(
                "runtime_adapter_unregistered",
                f"runtime adapter {adapter_id} is not executable by this harness",
            )
        )
    else:
        if adapter_id not in scene["compatible_runtime_adapter_ids"]:
            blockers.append(
                _blocker(
                    "scene_runtime_incompatible",
                    f"scene {campaign['scene_id']} does not support {adapter_id}",
                )
            )
        if embodiment["arm_count"] not in adapter["arm_counts"]:
            blockers.append(
                _blocker(
                    "arm_count_unsupported",
                    f"{adapter_id} does not support {embodiment['arm_count']} arms",
                )
            )
        capability_id = task["required_world_capability_id"]
        if capability_id not in adapter["world_capability_ids"]:
            blockers.append(
                _blocker(
                    "world_capability_missing",
                    f"{adapter_id} does not implement {capability_id}",
                )
            )
        evaluator_id = task["completion_predicate"]["evaluator_id"]
        if evaluator_id not in adapter["completion_evaluator_ids"]:
            blockers.append(
                _blocker(
                    "completion_evaluator_missing",
                    f"{adapter_id} does not implement {evaluator_id}",
                )
            )
        dataset_adapter_id = embodiment["dataset_adapter_id"]
        if dataset_adapter_id is None:
            blockers.append(
                _blocker(
                    "dataset_adapter_missing",
                    f"embodiment {campaign['embodiment_id']} has no dataset adapter",
                )
            )
        elif dataset_adapter_id not in adapter["dataset_adapter_ids"]:
            blockers.append(
                _blocker(
                    "dataset_adapter_unsupported",
                    f"{adapter_id} does not support {dataset_adapter_id}",
                )
            )
    return blockers, binding


def command_for_campaign(
    campaign: dict[str, Any],
    task: dict[str, Any],
    binding: dict[str, Any],
    *,
    output_root: Path,
    python_executable: str = sys.executable,
) -> list[str]:
    predicate = task["completion_predicate"]
    subject = binding["roles"][predicate["subject_role"]]
    reference = binding["roles"][predicate["reference_role"]]
    command = [
        python_executable,
        str(REPO_ROOT / "scripts/run_gemini_groot_campaign.py"),
        "--target-successes",
        str(campaign["target_successes"]),
        "--max-attempt-multiplier",
        str(campaign.get("max_attempt_multiplier", 2.0)),
        "--seed",
        str(campaign.get("seed", 0)),
        "--task",
        binding["robolab_task"],
        "--output",
        str(output_root / campaign["campaign_id"]),
        "--object-xy-range",
        str(campaign.get("object_xy_range", 0.06)),
        "--plate-xy-range",
        str(campaign.get("reference_xy_range", 0.05)),
        "--object-yaw-range-deg",
        str(campaign.get("object_yaw_range_deg", 90.0)),
        "--movable-object-asset",
        subject["asset"],
        "--movable-object-label",
        subject["label"],
        "--target-receptacle-asset",
        reference["asset"],
        "--target-receptacle-label",
        reference["label"],
        "--instruction",
        task["instruction"],
        "--light-min",
        str(campaign.get("light_min", 1800.0)),
        "--light-max",
        str(campaign.get("light_max", 8500.0)),
    ]
    if campaign.get("visible_first", False):
        command.append("--visible-first")
    if campaign.get("fixed_background", False):
        command.append("--fixed-background")
    if campaign.get("enable_passive_critic", False):
        command.append("--enable-passive-critic")
    return command


def build_plan(
    spec: dict[str, Any],
    *,
    output_root: Path,
    selected_campaign_ids: set[str] | None = None,
    repo_root: Path = REPO_ROOT,
    python_executable: str = sys.executable,
) -> dict[str, Any]:
    indexes = validate_spec(spec)
    unknown = (selected_campaign_ids or set()) - indexes["campaigns"].keys()
    if unknown:
        raise SpecError(f"unknown selected campaigns: {', '.join(sorted(unknown))}")
    cells = []
    for campaign_id, campaign in indexes["campaigns"].items():
        if selected_campaign_ids and campaign_id not in selected_campaign_ids:
            continue
        blockers, binding = assess_campaign(campaign, indexes, repo_root=repo_root)
        runnable = not blockers
        cell: dict[str, Any] = {
            "campaign_id": campaign_id,
            "task_id": campaign["task_id"],
            "scene_id": campaign["scene_id"],
            "embodiment_id": campaign["embodiment_id"],
            "status": "runnable" if runnable else "blocked",
            "blockers": blockers,
        }
        if runnable and binding is not None:
            cell["command"] = command_for_campaign(
                campaign,
                indexes["tasks"][campaign["task_id"]],
                binding,
                output_root=output_root,
                python_executable=python_executable,
            )
        cells.append(cell)
    return {
        "schema_version": "gemini-training-matrix-plan.v1",
        "source_schema_version": SCHEMA_VERSION,
        "output_root": str(output_root),
        "summary": {
            "total": len(cells),
            "runnable": sum(cell["status"] == "runnable" for cell in cells),
            "blocked": sum(cell["status"] == "blocked" for cell in cells),
        },
        "cells": cells,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Plan or execute validated Gemini-to-training campaign cells."
    )
    parser.add_argument("--spec", type=Path, default=DEFAULT_SPEC)
    parser.add_argument(
        "--output", type=Path, default=REPO_ROOT / "artifacts/gemini_training_matrix"
    )
    parser.add_argument("--campaign", action="append", dest="campaign_ids")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Execute runnable cells. The default only writes and prints the plan.",
    )
    parser.add_argument(
        "--runnable-only",
        action="store_true",
        help="During execution, skip blocked cells instead of refusing the matrix.",
    )
    args = parser.parse_args()
    try:
        spec = load_spec(args.spec.expanduser().resolve())
        output = args.output.expanduser().resolve()
        plan = build_plan(
            spec,
            output_root=output,
            selected_campaign_ids=set(args.campaign_ids or []) or None,
        )
    except SpecError as exc:
        parser.error(str(exc))

    output.mkdir(parents=True, exist_ok=True)
    plan_path = output / "training_matrix_plan.json"
    plan_path.write_text(json.dumps(plan, indent=2) + "\n")
    print(
        f"Matrix: {plan['summary']['runnable']} runnable, "
        f"{plan['summary']['blocked']} blocked; plan={plan_path}"
    )
    for cell in plan["cells"]:
        if cell["status"] == "runnable":
            print(f"RUNNABLE {cell['campaign_id']}: {shlex.join(cell['command'])}")
        else:
            reasons = "; ".join(item["detail"] for item in cell["blockers"])
            print(f"BLOCKED  {cell['campaign_id']}: {reasons}")

    if not args.execute:
        return 0
    blocked = [cell for cell in plan["cells"] if cell["status"] == "blocked"]
    if blocked and not args.runnable_only:
        print(
            "Refusing execution because selected cells are blocked; use "
            "--runnable-only or select a runnable --campaign.",
            file=sys.stderr,
        )
        return 2

    results = []
    for cell in plan["cells"]:
        if cell["status"] != "runnable":
            results.append({"campaign_id": cell["campaign_id"], "status": "skipped"})
            continue
        completed = subprocess.run(cell["command"], cwd=REPO_ROOT, check=False)
        results.append(
            {
                "campaign_id": cell["campaign_id"],
                "status": "completed" if completed.returncode == 0 else "failed",
                "returncode": completed.returncode,
            }
        )
        if completed.returncode != 0:
            break
    results_path = output / "training_matrix_results.json"
    results_path.write_text(json.dumps({"results": results}, indent=2) + "\n")
    return 0 if results and all(
        item["status"] in {"completed", "skipped"} for item in results
    ) else 2


if __name__ == "__main__":
    raise SystemExit(main())
