#!/usr/bin/env python3
"""Strict admission gate for model-governed world-effect episode traces."""
from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence


WORLD_EFFECT_EPISODE_ACCEPTANCE_SCHEMA_VERSION = (
    "world-effect-episode-acceptance.v1"
)


@dataclass(frozen=True)
class EpisodeAcceptance:
    accepted: bool
    checks: Mapping[str, Mapping[str, Any]]
    rejection_reasons: tuple[str, ...]
    warnings: tuple[str, ...]
    metrics: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": WORLD_EFFECT_EPISODE_ACCEPTANCE_SCHEMA_VERSION,
            "accepted": self.accepted,
            "checks": {key: dict(value) for key, value in self.checks.items()},
            "rejection_reasons": list(self.rejection_reasons),
            "warnings": list(self.warnings),
            "metrics": dict(self.metrics),
            "execution_authority": False,
        }


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sequence(value: Any) -> Sequence[Any]:
    return value if isinstance(value, (list, tuple)) else ()


def _check(passed: bool, reason: str, **evidence: Any) -> dict[str, Any]:
    return {"passed": bool(passed), "reason": reason, **evidence}


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    numeric = float(value)
    return numeric if math.isfinite(numeric) else None


def _executed_operations(trace: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    sequence = _mapping(trace.get("world_effect_sequence"))
    return [
        operation
        for operation in _sequence(sequence.get("operations"))
        if isinstance(operation, Mapping)
        and isinstance(operation.get("dispatch"), Mapping)
        and isinstance(_mapping(operation.get("dispatch")).get("outcome"), Mapping)
    ]


def _actuator_reports(
    operations: Sequence[Mapping[str, Any]],
) -> list[tuple[int, Mapping[str, Any], Mapping[str, Any]]]:
    reports = []
    for operation in operations:
        dispatch = _mapping(operation.get("dispatch"))
        handler = _mapping(_mapping(dispatch.get("outcome")).get("handler_result"))
        report = _mapping(handler.get("actuator_report"))
        if report:
            reports.append(
                (
                    int(operation.get("operation_index", -1)),
                    operation,
                    report,
                )
            )
    return reports


def assess_world_effect_episode(
    trace: Mapping[str, Any],
) -> EpisodeAcceptance:
    """Decide whether a trace is safe to publish as a training episode.

    The gate uses only embodiment-neutral trace contracts: observed goal
    predicates, reversible-attachment state, sensor/action/model evidence, and
    lease lifecycle.  It does not encode object names, robot joints, or a task
    routine.
    """
    sequence = _mapping(trace.get("world_effect_sequence"))
    result = _mapping(trace.get("guarded_world_effect_result"))
    progress = [
        item
        for item in _sequence(sequence.get("progress_observations"))
        if isinstance(item, Mapping)
    ]
    final_progress = progress[-1] if progress else {}
    operations = _executed_operations(trace)
    checks: dict[str, Mapping[str, Any]] = {}
    warnings: list[str] = []

    selected_goal_completed = bool(
        trace.get("status") == "guarded_world_effect_sequence_stopped"
        and sequence.get("status") == "stopped"
        and sequence.get("stop_reason") == "selected_goal_completed"
        and result.get("selected_goal_completed") is True
    )
    goal_transitions = [
        item
        for item in _sequence(sequence.get("goal_transitions"))
        if isinstance(item, Mapping)
    ]
    final_goal_transition = goal_transitions[-1] if goal_transitions else {}
    task_completed = bool(
        trace.get("status") == "guarded_world_effect_sequence_stopped"
        and sequence.get("status") == "stopped"
        and sequence.get("stop_reason") == "task_completed"
        and sequence.get("task_completion_claimed") is True
        and result.get("task_completion_claimed") is True
        and final_goal_transition.get("status") == "task_complete"
        and _mapping(final_goal_transition.get("fresh_graph")).get("status")
        == "complete"
        and _mapping(
            final_goal_transition.get("task_completion_assessment")
        ).get("valid")
        is True
    )
    completed = selected_goal_completed or task_completed
    checks["sequence_completed_selected_goal"] = _check(
        completed,
        (
            "task_completed_with_fresh_graph"
            if task_completed
            else (
                "selected_goal_completed"
                if selected_goal_completed
                else "sequence_not_completed"
            )
        ),
        trace_status=trace.get("status"),
        stop_reason=sequence.get("stop_reason"),
    )
    checks["task_completion_fresh_graph_admitted"] = _check(
        not bool(sequence.get("task_completion_claimed")) or task_completed,
        (
            "fresh_complete_graph_and_membership_lease_admitted"
            if task_completed
            else (
                "single_goal_episode_did_not_claim_task_completion"
                if not sequence.get("task_completion_claimed")
                else "task_completion_claim_lacks_fresh_graph_evidence"
            )
        ),
        goal_transition_count=len(goal_transitions),
        final_goal_transition_status=final_goal_transition.get("status"),
    )

    evaluations = [
        item
        for item in _sequence(final_progress.get("selected_goal_evaluations"))
        if isinstance(item, Mapping)
    ]
    goal_evidence = bool(
        final_progress.get("selected_goal_satisfied") is True
        and evaluations
        and all(item.get("satisfied") is True for item in evaluations)
        and not _sequence(
            final_progress.get("completion_blocking_attachment_entity_ids")
        )
    )
    checks["fresh_goal_predicates_satisfied"] = _check(
        goal_evidence,
        (
            "all_final_goal_predicates_satisfied"
            if goal_evidence
            else "fresh_final_goal_evidence_missing_or_unsatisfied"
        ),
        evaluator_ids=[item.get("evaluator_id") for item in evaluations],
        evaluation_count=len(evaluations),
    )

    actuator_reports = _actuator_reports(operations)
    engage_indices = [
        index
        for index, _, report in actuator_reports
        if report.get("requested_state") == "engage"
    ]
    disengage_reports = [
        item
        for item in actuator_reports
        if item[2].get("requested_state") == "disengage"
    ]
    release_required = bool(engage_indices)
    final_release = disengage_reports[-1] if disengage_reports else None
    release_report = {} if final_release is None else final_release[2]
    release_state = _mapping(release_report.get("state_after"))
    release_contact = _mapping(release_state.get("current_contact"))
    final_attachment = next(
        (
            _mapping(operation.get("attachment_state_after"))
            for operation in reversed(operations)
            if isinstance(operation.get("attachment_state_after"), Mapping)
        ),
        {},
    )
    final_retained_force = _finite_number(
        release_contact.get("retained_force_n")
    )
    final_gripper_fraction = _finite_number(
        release_state.get("gripper_closed_fraction")
    )
    release_after_engage = bool(
        not release_required
        or (
            final_release is not None
            and final_release[0] > max(engage_indices)
            and release_report.get("engaged_after") is False
            and release_contact.get("touch") is False
            and final_retained_force is not None
            and final_retained_force <= 0.0
            and final_gripper_fraction is not None
            and final_gripper_fraction <= 0.1
            and final_attachment.get("gripper_engaged") is False
            and not _sequence(final_attachment.get("entity_ids"))
        )
    )
    checks["attachment_released_and_contact_cleared"] = _check(
        release_after_engage,
        (
            "confirmed_disengagement_after_attachment"
            if release_after_engage and release_required
            else (
                "no_attachment_acquired"
                if release_after_engage
                else "attachment_or_contact_not_cleared"
            )
        ),
        release_required=release_required,
        engage_operation_indices=engage_indices,
        final_disengage_operation_index=(
            None if final_release is None else final_release[0]
        ),
        final_touch=release_contact.get("touch"),
        final_retained_force_n=final_retained_force,
    )

    contact_summary_present = "guarded_episode_contact_telemetry" in trace
    contact_summary = _mapping(trace.get("guarded_episode_contact_telemetry"))
    contact_telemetry_ok = bool(
        not contact_summary_present or contact_summary.get("passed") is True
    )
    checks["contact_telemetry_admitted"] = _check(
        contact_telemetry_ok,
        (
            "contact_telemetry_passed"
            if contact_summary_present and contact_telemetry_ok
            else (
                "legacy_trace_without_episode_contact_summary"
                if contact_telemetry_ok
                else "contact_telemetry_failed"
            )
        ),
        summary=(dict(contact_summary) if contact_summary_present else None),
    )

    lease_failures: list[dict[str, Any]] = []
    explained_revocations: list[dict[str, Any]] = []
    for operation in operations:
        dispatch = _mapping(operation.get("dispatch"))
        lease = _mapping(dispatch.get("runtime_lease_after"))
        state = lease.get("state")
        if state == "consumed":
            continue
        record = {
            "operation_index": operation.get("operation_index"),
            "state": state,
            "reason": lease.get("revocation_reason"),
            "condition_id": lease.get("revocation_condition_id"),
        }
        if (
            state == "revoked"
            and isinstance(lease.get("revocation_reason"), str)
            and bool(_mapping(lease.get("revocation_evidence")))
        ):
            explained_revocations.append(record)
        else:
            lease_failures.append(record)
    if explained_revocations:
        warnings.append(
            f"{len(explained_revocations)} lease revocation(s) include explicit "
            "reason and evidence"
        )
    leases_ok = bool(operations and not lease_failures)
    checks["no_unexplained_lease_revocations"] = _check(
        leases_ok,
        "all_leases_consumed_or_explained" if leases_ok else "lease_audit_failed",
        executed_operation_count=len(operations),
        explained_revocations=explained_revocations,
        failures=lease_failures,
    )

    incomplete_operations: list[dict[str, Any]] = []
    for operation in operations:
        dispatch = _mapping(operation.get("dispatch"))
        evidence = _mapping(dispatch.get("fresh_evidence"))
        observation = _mapping(evidence.get("observation"))
        outcome = _mapping(dispatch.get("outcome"))
        handler = _mapping(outcome.get("handler_result"))
        post = _mapping(handler.get("post_dispatch_observation"))
        missing = []
        if not _mapping(observation.get("rgbd_scene_geometry")):
            missing.append("fresh_rgbd_scene_geometry")
        if not _mapping(observation.get("tracked_entity_positions_m")):
            missing.append("fresh_tracked_entity_positions")
        if not dispatch.get("permit"):
            missing.append("dispatch_permit")
        planning_source = operation.get("planning_source")
        if not isinstance(planning_source, str) or not planning_source:
            missing.append("model_planning_source")
        if (
            planning_source == "fresh_post_effect_model_replan"
            and not _mapping(operation.get("planning"))
        ):
            missing.append("fresh_model_replan_trace")
        if handler.get("final_action") is None:
            missing.append("executed_action")
        if not _mapping(post.get("rgbd_scene_geometry")):
            missing.append("post_effect_rgbd_scene_geometry")
        if not _mapping(post.get("tracked_entity_positions_m")):
            missing.append("post_effect_tracked_entity_positions")
        if missing:
            incomplete_operations.append(
                {
                    "operation_index": operation.get("operation_index"),
                    "missing": missing,
                }
            )
    model_components = {
        name: _mapping(trace.get(name)).get("status")
        for name in (
            "world_intent_shadow",
            "world_goal_graph_shadow",
            "world_scope_membership_audit_shadow",
            "world_goal_activation_shadow",
        )
    }
    model_trace_complete = all(
        status == "valid" for status in model_components.values()
    )
    trace_complete = bool(
        operations
        and not incomplete_operations
        and model_trace_complete
        and len(progress) >= len(operations)
    )
    checks["sensor_action_model_trace_complete"] = _check(
        trace_complete,
        "trace_complete" if trace_complete else "trace_incomplete",
        model_component_statuses=model_components,
        progress_observation_count=len(progress),
        incomplete_operations=incomplete_operations,
    )

    rejection_reasons = tuple(
        name for name, check in checks.items() if not check.get("passed")
    )
    return EpisodeAcceptance(
        accepted=not rejection_reasons,
        checks=checks,
        rejection_reasons=rejection_reasons,
        warnings=tuple(warnings),
        metrics={
            "executed_operation_count": len(operations),
            "progress_observation_count": len(progress),
            "goal_predicate_count": len(evaluations),
            "attachment_acquisition_count": len(engage_indices),
            "attachment_release_count": len(disengage_reports),
            "explained_revocation_count": len(explained_revocations),
            "goal_transition_count": len(goal_transitions),
            "task_completed": task_completed,
        },
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trace", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    trace = json.loads(args.trace.read_text())
    result = assess_world_effect_episode(trace).to_dict()
    output = args.output or args.trace.with_name("episode_acceptance.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    return 0 if result["accepted"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
