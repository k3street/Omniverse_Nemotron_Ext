from __future__ import annotations

import ast
import json
from pathlib import Path
import sys

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from observation_bound_motion_tools import (  # noqa: E402
    MotionToolValidationError,
    ObservationBoundOperationGate,
    OperationCandidate,
    actuator_transition_is_admissible,
    compare_grasp_pose_to_failed_attempts,
    compare_target_to_stalled_recovery,
    failed_grasp_pose_lease_released,
    motion_report_yields_to_scheduler,
    motion_checkpoint_scheduler_handoff_reason,
    operation_scheduler_tool_schemas,
    recovery_motion_handoff_from_report,
    retained_contact_supports_loaded_actuator,
    runtime_transition_admission,
    runtime_transition_motion_handoff,
    stalled_motion_checkpoint_yields_to_scheduler,
)


def test_failed_grasp_pose_comparison_uses_object_relative_pose_deltas():
    comparisons = compare_grasp_pose_to_failed_attempts(
        failed_attempts=[
            {
                "attempt_id": 7,
                "eef_minus_object_m": [0.1, 0.0, 0.2],
                "eef_quaternion_wxyz": [1.0, 0.0, 0.0, 0.0],
            }
        ],
        current_eef_xyz_m=[1.2, 2.04, 3.2],
        current_object_xyz_m=[1.1, 2.0, 3.0],
        current_eef_quaternion_wxyz=[
            0.9396926208,
            0.0,
            0.0,
            0.3420201433,
        ],
    )
    assert comparisons[0]["attempt_id"] == 7
    assert comparisons[0]["comparison_frame"] == (
        "object_relative_end_effector_pose"
    )
    assert comparisons[0]["translation_delta_m"] == pytest.approx(0.04)
    assert comparisons[0]["orientation_delta_deg"] == pytest.approx(40.0)


def test_failed_grasp_pose_comparison_treats_quaternion_sign_as_same_pose():
    comparisons = compare_grasp_pose_to_failed_attempts(
        failed_attempts=[
            {
                "eef_minus_object_m": [0.0, 0.0, 0.1],
                "eef_quaternion_wxyz": [-1.0, 0.0, 0.0, 0.0],
            }
        ],
        current_eef_xyz_m=[0.0, 0.0, 0.1],
        current_object_xyz_m=[0.0, 0.0, 0.0],
        current_eef_quaternion_wxyz=[1.0, 0.0, 0.0, 0.0],
    )
    assert comparisons[0]["translation_delta_m"] == pytest.approx(0.0)
    assert comparisons[0]["orientation_delta_deg"] == pytest.approx(0.0)


def test_failed_grasp_pose_lease_requires_new_position_or_orientation():
    assert not failed_grasp_pose_lease_released(
        pose_comparisons=[
            {"translation_delta_m": 0.014, "orientation_delta_deg": 9.9}
        ],
    )
    assert failed_grasp_pose_lease_released(
        pose_comparisons=[
            {"translation_delta_m": 0.015, "orientation_delta_deg": 0.0}
        ],
    )
    assert failed_grasp_pose_lease_released(
        pose_comparisons=[
            {"translation_delta_m": 0.0, "orientation_delta_deg": 10.0}
        ],
    )
    assert failed_grasp_pose_lease_released(pose_comparisons=[])


def test_failed_grasp_pose_lease_blocks_only_reengagement():
    assert not actuator_transition_is_admissible(
        actuator_engaged=False,
        goal_contact_observed=False,
        retained_contact_observed=True,
        failed_grasp_pose_lease_released=False,
        interaction_distance_m=0.0,
    )
    assert actuator_transition_is_admissible(
        actuator_engaged=True,
        goal_contact_observed=False,
        retained_contact_observed=True,
        measured_actuator_outcome_invalidated=True,
        failed_grasp_pose_lease_released=False,
        interaction_distance_m=0.0,
    )


def test_disengaged_actuator_requires_touch_or_interaction_proximity():
    assert not actuator_transition_is_admissible(
        actuator_engaged=False,
        goal_contact_observed=False,
        retained_contact_observed=False,
        interaction_distance_m=0.039,
    )
    assert actuator_transition_is_admissible(
        actuator_engaged=False,
        goal_contact_observed=False,
        retained_contact_observed=True,
        interaction_distance_m=0.039,
    )
    assert actuator_transition_is_admissible(
        actuator_engaged=False,
        goal_contact_observed=False,
        retained_contact_observed=False,
        interaction_distance_m=0.008,
    )


def test_loaded_actuator_stays_engaged_until_goal_or_contact_loss():
    assert not actuator_transition_is_admissible(
        actuator_engaged=True,
        goal_contact_observed=False,
        retained_contact_observed=True,
        interaction_distance_m=0.0,
    )
    assert actuator_transition_is_admissible(
        actuator_engaged=True,
        goal_contact_observed=False,
        retained_contact_observed=False,
        interaction_distance_m=0.2,
    )


def test_measured_failed_actuator_outcome_readmits_release_with_retained_touch():
    assert actuator_transition_is_admissible(
        actuator_engaged=True,
        goal_contact_observed=False,
        retained_contact_observed=True,
        measured_actuator_outcome_invalidated=True,
        interaction_distance_m=0.0,
    )


def test_loaded_contact_quality_rejects_one_sided_multibody_contact():
    assert not retained_contact_supports_loaded_actuator(
        {
            "available": True,
            "touch": True,
            "contact_bodies": {
                "available": True,
                "active_body_count": 1,
                "pairwise_force_direction_cosine": None,
                "force_magnitude_ratio_min_over_max": None,
                "channels": [
                    {"body": "contact_a", "touch": False},
                    {"body": "contact_b", "touch": True},
                ],
            },
        }
    )


def test_loaded_contact_quality_accepts_opposed_balanced_multibody_contact():
    assert retained_contact_supports_loaded_actuator(
        {
            "available": True,
            "touch": True,
            "contact_bodies": {
                "available": True,
                "active_body_count": 2,
                "pairwise_force_direction_cosine": -0.8,
                "force_magnitude_ratio_min_over_max": 0.7,
                "channels": [
                    {"body": "contact_a", "touch": True},
                    {"body": "contact_b", "touch": True},
                ],
            },
        }
    )


def test_loaded_contact_quality_preserves_single_channel_actuator_contact():
    assert retained_contact_supports_loaded_actuator(
        {
            "available": True,
            "touch": True,
            "contact_bodies": {
                "available": True,
                "active_body_count": 1,
                "pairwise_force_direction_cosine": None,
                "force_magnitude_ratio_min_over_max": None,
                "channels": [{"body": "contact", "touch": True}],
            },
        }
    )


def test_loaded_motion_transition_requires_command_contact_and_geometry():
    missing_contact = runtime_transition_admission(
        "supported_loaded_interaction",
        actuator_engaged=True,
        retained_contact_observed=False,
        interaction_candidate_observed=True,
        interaction_confirmed_observed=False,
        actuator_disengaged_observed=False,
    )
    assert missing_contact["admitted"] is False
    assert missing_contact["missing_evidence"] == [
        "retained_contact_observed"
    ]

    admitted = runtime_transition_admission(
        "supported_loaded_interaction",
        actuator_engaged=True,
        retained_contact_observed=True,
        interaction_candidate_observed=True,
        interaction_confirmed_observed=False,
        actuator_disengaged_observed=False,
    )
    assert admitted["admitted"] is True
    assert admitted["authority"] == "fresh_runtime_capability_evidence"


def test_release_transition_requires_observed_disengagement_and_no_load():
    commanded_open_but_not_observed = runtime_transition_admission(
        "released_interaction",
        actuator_engaged=False,
        retained_contact_observed=True,
        interaction_candidate_observed=True,
        interaction_confirmed_observed=True,
        actuator_disengaged_observed=False,
    )
    assert commanded_open_but_not_observed["admitted"] is False
    assert set(commanded_open_but_not_observed["missing_evidence"]) == {
        "actuator_disengaged_observed",
        "loaded_contact_absent",
    }

    admitted = runtime_transition_admission(
        "released_interaction",
        actuator_engaged=False,
        retained_contact_observed=False,
        interaction_candidate_observed=False,
        interaction_confirmed_observed=False,
        actuator_disengaged_observed=True,
    )
    assert admitted["admitted"] is True


def test_converged_noop_with_unchanged_capability_is_a_stalled_handoff():
    handoff = runtime_transition_motion_handoff(
        {
            "phase": "runtime-transition",
            "target_xyz": [0.0, 0.0, 0.005],
            "target_quaternion_wxyz": [1.0, 0.0, 0.0, 0.0],
            "eef_start_xyz": [0.0, 0.0, 0.0],
            "eef_final_xyz": [0.0, 0.0, 0.0],
            "target_error_before_m": 0.005,
            "target_error_after_m": 0.005,
            "orientation_error_after_deg": 0.0,
            "executor_config": {
                "position_tolerance_m": 0.01,
                "orientation_tolerance_deg": 4.0,
            },
            "recovery_request": None,
        },
        admission_before={
            "admitted": False,
            "missing_evidence": ["retained_contact_observed"],
        },
        admission_after={
            "admitted": False,
            "missing_evidence": ["retained_contact_observed"],
        },
    )
    assert handoff is not None
    assert handoff["stopped_reason"] == (
        "runtime_capability_unchanged_after_converged_noop"
    )
    assert handoff["lease_invalidation_reason"] == (
        "lease_invalidated:motion_progress_stalled"
    )
    assert handoff["measured_eef_displacement_m"] == pytest.approx(0.0)


def test_transition_motion_that_changes_pose_is_not_labeled_noop():
    handoff = runtime_transition_motion_handoff(
        {
            "phase": "runtime-transition",
            "target_xyz": [0.0, 0.0, 0.05],
            "target_quaternion_wxyz": [1.0, 0.0, 0.0, 0.0],
            "eef_start_xyz": [0.0, 0.0, 0.0],
            "eef_final_xyz": [0.0, 0.0, 0.03],
            "target_error_before_m": 0.05,
            "target_error_after_m": 0.02,
            "orientation_error_after_deg": 0.0,
            "executor_config": {
                "position_tolerance_m": 0.01,
                "orientation_tolerance_deg": 4.0,
            },
            "recovery_request": None,
        },
        admission_before={
            "admitted": False,
            "missing_evidence": ["retained_contact_observed"],
        },
        admission_after={
            "admitted": False,
            "missing_evidence": ["retained_contact_observed"],
        },
    )
    assert handoff is None


def _candidates() -> tuple[OperationCandidate, ...]:
    return (
        OperationCandidate(
            operation_id="continue.runtime_motion",
            kind="motion",
            description="Preserve current actuator commands and continue motion.",
        ),
        OperationCandidate(
            operation_id="evaluate.runtime_actuator",
            kind="actuation",
            description="Request a fresh actuator decision before continuing.",
        ),
    )


def _call(name: str, observation_id: str, **arguments):
    return {
        "function": {
            "name": name,
            "arguments": {
                "observation_id": observation_id,
                "confidence": 0.8,
                "reason": "Grounded in the current observation.",
                **arguments,
            },
        }
    }


def _gate(observation_id: str = "schedule-1") -> ObservationBoundOperationGate:
    return ObservationBoundOperationGate(
        observation_id=observation_id,
        candidates=_candidates(),
    )


def test_scheduler_advertises_only_runtime_candidate_ids():
    schemas = operation_scheduler_tool_schemas("schedule-1", _candidates())
    by_name = {item["function"]["name"]: item for item in schemas}
    operation_property = by_name["dispatch_operation"]["function"]["parameters"][
        "properties"
    ]["operation_id"]
    assert operation_property["enum"] == [
        "continue.runtime_motion",
        "evaluate.runtime_actuator",
    ]
    assert set(by_name) == {
        "dispatch_operation",
        "observe_again",
        "complete_task",
        "abort_task",
    }


def test_scheduler_contract_is_task_and_embodiment_neutral():
    serialized = json.dumps(
        operation_scheduler_tool_schemas("schedule-1", _candidates())
    ).lower()
    for forbidden in (
        "banana",
        "plate",
        "franka",
        "gripper",
        "joint",
        "approach",
        "grasp",
        "release",
    ):
        assert forbidden not in serialized


def test_dispatch_resolves_runtime_operation_kind():
    outcome = _gate().dispatch(
        _call(
            "dispatch_operation",
            "schedule-1",
            operation_id="evaluate.runtime_actuator",
        )
    )
    assert outcome.action == "dispatch"
    assert outcome.operation_id == "evaluate.runtime_actuator"
    assert outcome.operation_kind == "actuation"


@pytest.mark.parametrize(
    ("tool_name", "action"),
    [
        ("observe_again", "observe"),
        ("complete_task", "complete"),
        ("abort_task", "abort"),
    ],
)
def test_scheduler_control_tools(tool_name: str, action: str):
    outcome = _gate().dispatch(_call(tool_name, "schedule-1"))
    assert outcome.action == action
    assert outcome.operation_id is None
    assert outcome.operation_kind is None


def test_scheduler_rejects_stale_and_unadvertised_operations():
    with pytest.raises(MotionToolValidationError, match="stale"):
        _gate().dispatch(
            _call(
                "dispatch_operation",
                "old-observation",
                operation_id="continue.runtime_motion",
            )
        )
    with pytest.raises(MotionToolValidationError, match="not advertised"):
        _gate().dispatch(
            _call(
                "dispatch_operation",
                "schedule-1",
                operation_id="invented.operation",
            )
        )


def test_scheduler_observation_is_single_use():
    gate = _gate()
    gate.dispatch(
        _call(
            "dispatch_operation",
            "schedule-1",
            operation_id="continue.runtime_motion",
        )
    )
    with pytest.raises(MotionToolValidationError, match="already authorized"):
        gate.dispatch(_call("observe_again", "schedule-1"))


def test_scheduler_rejects_duplicate_candidates_and_unknown_arguments():
    duplicate = _candidates()[0]
    with pytest.raises(MotionToolValidationError, match="unique"):
        ObservationBoundOperationGate(
            observation_id="schedule-1",
            candidates=(duplicate, duplicate),
        )
    with pytest.raises(MotionToolValidationError, match="unknown fields"):
        _gate().dispatch(
            _call(
                "dispatch_operation",
                "schedule-1",
                operation_id="continue.runtime_motion",
                implementation="not scheduler data",
            )
        )


def test_model_motion_hold_yields_to_scheduler_without_actuator_hint():
    report = {
        "recovery_request": {
            "reason": "model_requested_hold",
            "coach_decision": {"motion_tool": {"action": "hold"}},
        }
    }
    assert motion_report_yields_to_scheduler(report)


def test_stalled_motion_checkpoint_yields_to_fresh_operation_scheduler():
    assert stalled_motion_checkpoint_yields_to_scheduler(
        {"reason": "lease_invalidated:motion_progress_stalled"}
    )
    assert stalled_motion_checkpoint_yields_to_scheduler(
        {
            "reason": (
                "lease_invalidated:contact_force_below_lease_minimum,"
                "motion_progress_stalled"
            )
        }
    )
    assert not stalled_motion_checkpoint_yields_to_scheduler(
        {"reason": "lease_invalidated:contact_force_below_lease_minimum"}
    )
    assert not stalled_motion_checkpoint_yields_to_scheduler(
        {"reason": "periodic"}
    )


@pytest.mark.parametrize(
    "invalidation",
    [
        "motion_execution_diverged",
        "motion_orientation_diverged",
        "motion_progress_stalled",
    ],
)
def test_local_kinematic_invalidations_report_scheduler_handoff_reason(
    invalidation,
):
    checkpoint = {"reason": f"lease_invalidated:{invalidation}"}
    assert motion_checkpoint_scheduler_handoff_reason(checkpoint) == invalidation
    assert stalled_motion_checkpoint_yields_to_scheduler(checkpoint)


def test_stalled_motion_report_becomes_compact_next_call_handoff():
    report = {
        "phase": "runtime_recovery",
        "target_xyz": [0.6, 0.0, 0.2],
        "target_quaternion_wxyz": [1.0, 0.0, 0.0, 0.0],
        "eef_start_xyz": [0.6, 0.0, 0.3],
        "eef_final_xyz": [0.6, 0.0, 0.27],
        "target_error_before_m": 0.1,
        "target_error_after_m": 0.07,
        "orientation_error_after_deg": 2.0,
        "executor_config": {
            "position_tolerance_m": 0.01,
            "orientation_tolerance_deg": 4.0,
        },
        "recovery_request": {
            "reason": "model_requested_hold",
            "lease_invalidation_reason": (
                "lease_invalidated:motion_progress_stalled"
            ),
        },
        "iterations": [
            {
                "measured_target_progress_m": 0.0,
                "stalled_observation_count": 3,
                "large_payload_that_should_not_be_copied": [1, 2, 3],
            }
        ],
    }
    handoff = recovery_motion_handoff_from_report(report)
    assert handoff == {
        "phase": "runtime_recovery",
        "attempted_target_xyz_m": [0.6, 0.0, 0.2],
        "attempted_target_quaternion_wxyz": [1.0, 0.0, 0.0, 0.0],
        "eef_start_xyz_m": [0.6, 0.0, 0.3],
        "eef_final_xyz_m": [0.6, 0.0, 0.27],
        "target_error_before_m": 0.1,
        "target_error_after_m": 0.07,
        "orientation_error_after_deg": 2.0,
        "position_tolerance_m": 0.01,
        "orientation_tolerance_deg": 4.0,
        "stopped_reason": "model_requested_hold",
        "lease_invalidation_reason": (
            "lease_invalidated:motion_progress_stalled"
        ),
        "last_measured_progress_m": 0.0,
        "last_stalled_observation_count": 3,
    }
    assert "iterations" not in handoff


def test_stalled_target_comparison_uses_executor_configured_tolerances():
    previous = {
        "attempted_target_xyz_m": [0.6, 0.0, 0.2],
        "attempted_target_quaternion_wxyz": [1.0, 0.0, 0.0, 0.0],
        "position_tolerance_m": 0.01,
        "orientation_tolerance_deg": 4.0,
        "lease_invalidation_reason": "lease_invalidated:motion_progress_stalled",
    }
    repeated = compare_target_to_stalled_recovery(
        previous_recovery_outcome=previous,
        proposed_target_xyz_m=[0.605, 0.0, 0.2],
        proposed_target_quaternion_wxyz=[
            0.9996573249755573,
            0.0,
            0.0,
            0.026176948307873153,
        ],
    )
    assert repeated is not None
    assert repeated["comparison_frame"] == "world_space_motion_target"
    assert repeated["translation_delta_m"] == pytest.approx(0.005)
    assert repeated["orientation_delta_deg"] == pytest.approx(3.0)
    assert repeated["previous_position_tolerance_m"] == pytest.approx(0.01)
    assert repeated["previous_orientation_tolerance_deg"] == pytest.approx(4.0)
    assert repeated["effectively_identical"] is True

    distinct = compare_target_to_stalled_recovery(
        previous_recovery_outcome=previous,
        proposed_target_xyz_m=[0.605, 0.0, 0.2],
        proposed_target_quaternion_wxyz=[
            0.9981347984218669,
            0.0,
            0.0,
            0.06104853953485687,
        ],
    )
    assert distinct is not None
    assert distinct["orientation_delta_deg"] == pytest.approx(7.0)
    assert distinct["effectively_identical"] is False


def test_nonstalled_target_has_no_repetition_comparison():
    assert compare_target_to_stalled_recovery(
        previous_recovery_outcome={
            "lease_invalidation_reason": "lease_invalidated:contact_lost"
        },
        proposed_target_xyz_m=[0.0, 0.0, 0.0],
        proposed_target_quaternion_wxyz=[1.0, 0.0, 0.0, 0.0],
    ) is None


def test_successful_motion_report_has_no_recovery_handoff():
    assert recovery_motion_handoff_from_report(
        {"converged": True, "recovery_request": None}
    ) is None


@pytest.mark.parametrize(
    "report",
    [
        {},
        {"recovery_request": {"reason": "local_anomaly"}},
        {
            "recovery_request": {
                "reason": "model_requested_hold",
                "coach_decision": {"motion_tool": {"action": "abort"}},
            }
        },
    ],
)
def test_safety_or_abort_never_yields_to_scheduler(report):
    assert not motion_report_yields_to_scheduler(report)


def test_adaptive_runner_routes_actuation_from_scheduler_not_recorded_hint():
    runner = SCRIPTS / "run_gemini_robotics_robolab.py"
    source = runner.read_text()
    tree = ast.parse(source, filename=str(runner))
    stage_loop = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.For, ast.AsyncFor))
        and "stage_index" in ast.unparse(node.target)
    )
    loop_source = ast.get_source_segment(source, stage_loop)
    assert loop_source is not None
    assert "operation_scheduler_handler(" in loop_source
    assert 'scheduler_decision.get("operation_kind") == "actuation"' in loop_source
    assert "bool(legacy_recorded_gripper_closed) !=" not in loop_source
    assert "bool(recorded_actions[start, 7]" not in loop_source


def test_runner_trace_explicitly_disclaims_recorded_actuator_hints():
    source = (SCRIPTS / "run_gemini_robotics_robolab.py").read_text()
    assert '"recorded_actuator_hints": False' in source
    assert '"recorded_actuator_hint_used": bool(' in source


def test_runner_immediately_reschedules_on_post_actuation_feedback_event():
    source = (SCRIPTS / "run_gemini_robotics_robolab.py").read_text()
    assessment = source.index(
        "feedback_event = _actuator_feedback_event_from_execution("
    )
    scheduler_call = source.index(
        "operation_scheduler_handler(",
        assessment,
    )
    next_workspace_feedback = source.index(
        'eef = _eef_position(env)',
        scheduler_call,
    )
    assert assessment < scheduler_call < next_workspace_feedback
    assert '"type": "actuator_physical_outcome_observed"' in source[
        assessment:next_workspace_feedback
    ]
    assert "**feedback_event" in source[
        assessment:next_workspace_feedback
    ]
    assert "record_unsupported_grasp_attempt(" in source[
        assessment:next_workspace_feedback
    ]


def test_runner_withholds_reengagement_until_failed_grasp_pose_changes():
    source = (SCRIPTS / "run_gemini_robotics_robolab.py").read_text()
    handler_start = source.index("def operation_scheduler_handler(")
    handler_end = source.index("def actuator_transition_handler(", handler_start)
    handler = source[handler_start:handler_end]
    comparison = handler.index("compare_grasp_pose_to_failed_attempts(")
    lease = handler.index("failed_grasp_pose_lease_released(", comparison)
    admission = handler.index("actuator_transition_is_admissible(", lease)
    candidates = handler.index("_post_motion_operation_candidates(", admission)
    assert comparison < lease < admission < candidates
    assert "failed_attempts=failed_grasp_attempts" in handler
    assert "failed_grasp_pose_lease_released=(" in handler
    assert '"failed_grasp_pose_comparisons": (' in handler


def test_runner_records_unsupported_engagement_before_fresh_scheduler_call():
    source = (SCRIPTS / "run_gemini_robotics_robolab.py").read_text()
    helper = source.index("def record_unsupported_grasp_attempt(")
    boundary_feedback = source.index(
        "boundary_feedback_event = (", helper
    )
    boundary_record = source.index(
        "record_unsupported_grasp_attempt(", boundary_feedback
    )
    boundary_scheduler = source.index(
        "operation_scheduler_handler(", boundary_record
    )
    assert boundary_feedback < boundary_record < boundary_scheduler
    helper_source = source[helper:boundary_feedback]
    assert 'execution.get("requested_state") != "engage"' in helper_source
    assert 'feedback_event.get("loaded_contact_supported_after") is True' in (
        helper_source
    )
    assert "failed_grasp_attempts.append(failure)" in helper_source


def test_runner_preserves_loaded_actuator_but_readmits_it_after_contact_loss():
    source = (SCRIPTS / "run_gemini_robotics_robolab.py").read_text()
    handler_start = source.index("def operation_scheduler_handler(")
    handler_end = source.index("def actuator_transition_handler(", handler_start)
    handler = source[handler_start:handler_end]
    predicate = handler.index('schedule_state.get("goal_relation", {})')
    touch = handler.index(
        "touch_observed = bool(", predicate
    )
    retained_contact = handler.index(
        "retained_contact_supports_loaded_actuator(", touch
    )
    recovery = handler.index(
        "actuator_recovery_observed = bool(", retained_contact
    )
    measured_failure = handler.index(
        "measured_actuator_outcome_invalidated = bool(", recovery
    )
    proximity = handler.index("interaction_distance_m = float(", measured_failure)
    alignment = handler.index(
        'schedule_state.get(\n                    "pregrasp_axis_alignment", {}',
        proximity,
    )
    admission = handler.index(
        "actuator_transition_available = (",
        alignment,
    )
    actuator_predicate = handler.index(
        "actuator_transition_is_admissible(", admission
    )
    candidates = handler.index(
        "_post_motion_operation_candidates(", actuator_predicate
    )
    model_call = handler.index("_choose_observation_bound_operation(", candidates)
    assert (
        predicate
        < touch
        < retained_contact
        < recovery
        < measured_failure
        < proximity
        < alignment
        < admission
        < actuator_predicate
        < candidates
        < model_call
    )
    assert "current_engaged and not retained_contact_observed" in handler
    retained_argument = handler[
        handler.index("retained_contact_observed=(", admission) : candidates
    ]
    assert "if current_engaged" in retained_argument
    assert "else touch_observed" in retained_argument
    assert '"loaded_contact_quality_source"' in handler
    assert 'trigger_event.get("actuator_outcome_invalidated") is True' in handler
    assert '"source": "measured_runtime_actuator_preconditions"' in handler
    assert "pregrasp_axis_alignment_ready" in handler
    assert "and actuator_transition_is_admissible(" in handler


def test_failed_lift_marks_actuator_outcome_invalid_for_operation_scheduler():
    source = (SCRIPTS / "run_gemini_robotics_robolab.py").read_text()
    lift_gate = source.index('if phase == "lift":')
    failure_event = source.index(
        '"type": "measured_stage_outcome_not_met"', lift_gate
    )
    scheduler = source.index("operation_scheduler_handler(", failure_event)
    event_source = source[failure_event:scheduler]
    assert '"actuator_outcome_invalidated": bool(' in event_source
    assert "failed_grasp_attempts\n                            and actuator_command_engaged" in (
        event_source
    )
    assert '"prior_failed_actuator_outcome_observed": bool(' in event_source
    assert '"failed_grasp_pose_comparisons": (' in event_source
    assert "compare_grasp_pose_to_failed_attempts(" in source[
        lift_gate:scheduler
    ]
    assert "failed_grasp_attempts" in event_source


def test_task_feasibility_preflight_gates_all_motion_with_runtime_evidence():
    source = (SCRIPTS / "run_gemini_robotics_robolab.py").read_text()
    evidence = source.index("def _runtime_task_capability_evidence(")
    chooser = source.index("def _choose_observation_bound_task_feasibility(")
    live_call = source.index(
        "scene, latency, digest = _choose_observation_bound_task_feasibility("
    )
    authority_gate = source.index(
        'if not bool(scene.get("motion_authorized")):', live_call
    )
    checkpoint_handler = source.index("def motion_checkpoint_handler(", live_call)
    assert evidence < chooser < live_call < authority_gate < checkpoint_handler
    preflight = source[evidence:checkpoint_handler]
    assert "body_mass" in preflight
    assert "joint_effort_limits" in preflight
    assert "continuous_normal_force_capacity_n" in preflight
    assert "rated_workspace_envelope" in preflight
    assert "ObservationBoundTaskFeasibilityGate" in preflight
    assert "Task feasibility preflight withheld motion authority" in preflight


def test_failed_lift_preserves_stalled_motion_evidence_for_next_model_call():
    source = (SCRIPTS / "run_gemini_robotics_robolab.py").read_text()
    lift_gate = source.index('if phase == "lift":')
    initialization = source.index(
        "previous_lift_recovery_motion_outcome = (", lift_gate
    )
    trigger = source.index(
        '"previous_recovery_motion_outcome": (', initialization
    )
    motion_context = source.index(
        '"previous_recovery_motion_outcome": (', trigger + 1
    )
    report_update = source.index(
        "previous_lift_recovery_motion_outcome = (", motion_context
    )
    assert initialization < trigger < motion_context < report_update
    prompt_start = source.index("def _motion_governor_prompt(")
    prompt_end = source.index(
        "def _motion_registry_for_observation_sources(", prompt_start
    )
    prompt = source[prompt_start:prompt_end]
    assert "previous_recovery_motion_outcome" in prompt
    assert "do not repeat an effectively identical target" in prompt


def test_phase_boundary_actuation_reschedules_before_motion_replan():
    source = (SCRIPTS / "run_gemini_robotics_robolab.py").read_text()
    hold = source.index(
        'decision["motion_tool"].get("action") == "hold"'
    )
    scheduler = source.index("operation_scheduler_handler(", hold)
    actuator_branch = source.index(
        'boundary_schedule.get("operation_kind") != "actuation"', scheduler
    )
    actuator = source.index("actuator_transition_handler(", actuator_branch)
    executor = source.index("_execute_binary_actuator_tool(", actuator)
    fresh_scheduler = source.index(
        "boundary_post_scheduler_latency", executor
    )
    replan = source.index("decision = motion_checkpoint_handler(", fresh_scheduler)
    terminal = source.index(
        'if decision.get("decision") != "execute"', replan
    )
    assert (
        hold
        < scheduler
        < actuator_branch
        < actuator
        < executor
        < fresh_scheduler
        < replan
        < terminal
    )
    assert '"type": "phase_boundary_model_hold"' in source[hold:actuator]
    assert '"phase_boundary_actuator_transition_completed"' in source[
        executor:fresh_scheduler
    ]
    assert 'phase_label=f"{phase}:boundary_actuation_completed"' in source[
        executor:replan
    ]
    assert '"reason": "scheduler_requested_boundary_replan"' in source[
        fresh_scheduler:terminal
    ]


def test_invalidated_motion_scheduler_dispatch_is_executed_before_phase_advance():
    source = (SCRIPTS / "run_gemini_robotics_robolab.py").read_text()
    initial_scheduler = source.index(
        "scheduler_motion_handoffs: list[dict[str, Any]] = []"
    )
    yielded_gate = source.index(
        "motion_report_yields_to_scheduler(motion_report)", initial_scheduler
    )
    replan = source.index(
        '"reason": "scheduler_requested_runtime_motion"', yielded_gate
    )
    executor = source.index("_move_eef_to_target(", replan)
    reschedule = source.index("operation_scheduler_handler(", executor)
    budget_gate = source.index(
        'scheduler_decision.get("operation_kind") == "motion"', reschedule
    )
    actuator_branch = source.index(
        'scheduler_decision.get("operation_kind") == "actuation"', budget_gate
    )
    assert (
        initial_scheduler
        < yielded_gate
        < replan
        < executor
        < reschedule
        < budget_gate
        < actuator_branch
    )
    handoff_source = source[initial_scheduler:actuator_branch]
    assert '"type": "scheduler_motion_handoff_completed"' in handoff_source
    assert 'motion_report["scheduler_motion_handoffs"]' in handoff_source
    target = handoff_source.index("handoff_target = torch.tensor(")
    active_target = handoff_source.index("nominal = handoff_target", target)
    executor = handoff_source.index("_move_eef_to_target(", active_target)
    assert target < active_target < executor


def test_stage_actuator_hold_reschedules_and_executes_motion_before_actuation():
    source = (SCRIPTS / "run_gemini_robotics_robolab.py").read_text()
    stage_branch = source.index(
        'scheduler_decision.get("operation_kind") == "actuation"'
    )
    branch_end = source.index(
        "post_feedback_decisions: list[dict[str, Any]] = []", stage_branch
    )
    branch = source[stage_branch:branch_end]
    actuator = branch.index("actuator_transition_handler(")
    yielded = branch.index(
        '"type": "actuator_governor_yielded_to_scheduler"', actuator
    )
    scheduler = branch.index("operation_scheduler_handler(", yielded)
    motion = branch.index("motion_checkpoint_handler(", actuator)
    executor = branch.index("_move_eef_to_target(", motion)
    admitted_actuation = branch.index("_execute_binary_actuator_tool(", executor)
    assert actuator < yielded < motion < executor < scheduler < admitted_actuation
    assert "yield_on_hold=True" in branch[actuator:yielded]
    assert '"reason": (' in branch[motion:executor]
    assert '"scheduler_requested_motion_after_"' in branch[motion:executor]
    assert '"actuator_hold_scheduler_handoffs"' in branch


def test_pregrasp_gate_runs_at_every_actual_disengaged_to_engaged_transition():
    source = (SCRIPTS / "run_gemini_robotics_robolab.py").read_text()
    helper = source.index("def admit_pregrasp_transition(")
    stage_loop = source.index("for stage_index, (", helper)
    helper_source = source[helper:stage_loop]
    assert 'requested_state != "engage"' in helper_source
    assert "or currently_engaged" in helper_source
    assert "or requested_state" not in helper_source.split(
        'requested_state != "engage"', 1
    )[0]
    assert "pregrasp_passed" in helper_source
    assert "pregrasp_passed\n                or" not in helper_source
    assert 'pregrasp_state = _state(env, initial_object_z)' in helper_source
    assert 'pregrasp_state.get(\n                "pregrasp_axis_alignment", {}' in (
        helper_source
    )
    assert "jaw_axis_aligned=jaw_axis_aligned" in helper_source

    actuator_branch = source.index(
        'scheduler_decision.get("operation_kind") == "actuation"', stage_loop
    )
    admitted = source.index(
        "admit_pregrasp_transition(", actuator_branch
    )
    executor = source.index(
        "_execute_binary_actuator_tool(", admitted
    )
    assert actuator_branch < admitted < executor


def test_disengaged_pregrasp_orientation_persists_from_fresh_wrist_pose():
    source = (SCRIPTS / "run_gemini_robotics_robolab.py").read_text()
    stage_loop = source.index("for stage_index, (")
    stage_model_call = source.index(
        "_choose_observation_bound_motion_tool(", stage_loop
    )
    stage_seed = source.index(
        "not actuator_engaged_at_stage_start", stage_loop
    )
    measured_quaternion = source.index(
        'current["eef_gripper_base_quaternion_wxyz"]', stage_seed
    )
    assert stage_seed < measured_quaternion < stage_model_call
    stage_source = source[stage_seed:stage_model_call]
    assert 'phase != "release"' in stage_source
    assert '"fresh_measured_disengaged_wrist_pose"' in stage_source

    retry = source.index("if needs_retry:", stage_model_call)
    retry_model_call = source.index(
        "_choose_observation_bound_motion_tool(", retry
    )
    retry_source = source[retry:retry_model_call]
    assert 'current[\n                                    "eef_gripper_base_quaternion_wxyz"' in (
        retry_source
    )
    assert '"fresh_measured_disengaged_wrist_pose"' in retry_source


def test_failed_measured_lift_routes_through_fresh_operation_recovery():
    source = (SCRIPTS / "run_gemini_robotics_robolab.py").read_text()
    lift_gate = source.index('if phase == "lift":')
    outcome_loop = source.index(
        "for recovery_index in range(\n"
        "                    args_cli.max_lift_recovery_operations",
        lift_gate,
    )
    scheduler = source.index("operation_scheduler_handler(", outcome_loop)
    actuator = source.index(
        'recovery_schedule.get("operation_kind") == "actuation"', scheduler
    )
    motion = source.index(
        'recovery_schedule.get("operation_kind") == "motion"', actuator
    )
    replan = source.index("motion_checkpoint_handler(", motion)
    executor = source.index("_move_eef_to_target(", replan)
    final_gate = source.index('tests["lift"] = lifted', executor)
    assert outcome_loop < scheduler < actuator < motion < replan < executor < final_gate
    assert '"type": "measured_stage_outcome_not_met"' in source[
        outcome_loop:scheduler
    ]
    assert '"failed_grasp_attempts": list(failed_grasp_attempts)' in source[
        outcome_loop:scheduler
    ]
    assert "args_cli.max_failed_grasp_attempts" in source[
        outcome_loop:scheduler
    ]
    assert "Gripper moved to lift pose but banana did not follow" not in source


def test_failed_measured_placement_routes_through_multi_operation_recovery():
    source = (SCRIPTS / "run_gemini_robotics_robolab.py").read_text()
    place_gate = source.index('if phase == "place" and not terminal:')
    outcome_loop = source.index(
        "for recovery_index in range(args_cli.max_transport_recoveries)",
        place_gate,
    )
    scheduler = source.index("operation_scheduler_handler(", outcome_loop)
    actuator = source.index(
        'recovery_schedule.get("operation_kind") == "actuation"', scheduler
    )
    motion = source.index(
        'recovery_schedule.get("operation_kind") == "motion"', actuator
    )
    replan = source.index("motion_checkpoint_handler(", motion)
    executor = source.index("_move_eef_to_target(", replan)
    final_gate = source.index('tests["centering"] = centered', executor)
    assert outcome_loop < scheduler < actuator < motion < replan < executor < final_gate
    assert '"object.target_contact_or_release_envelope"' in source[
        place_gate:scheduler
    ]
    assert "Model-governed placement did not reach measured release" not in source


def test_runtime_transition_gate_owns_loaded_motion_phase_advancement():
    source = (SCRIPTS / "run_gemini_robotics_robolab.py").read_text()
    stage_loop = source.index("for stage_index, (")
    boundary = source.index("next_runtime_label = (", stage_loop)
    loaded_requirement = source.index(
        'required_capability="supported_loaded_interaction"', boundary
    )
    task_completion = source.index("if task_completed_by_scheduler:", boundary)
    assert boundary < loaded_requirement < task_completion
    boundary_source = source[boundary:task_completion]
    assert 'next_runtime_label == "lift"' in boundary_source
    assert "and not task_completed_by_scheduler" in boundary_source
    assert "resolve_runtime_transition(" in boundary_source
    assert 'episode_trace["stages"][-1][' in boundary_source


def test_goal_relation_exposes_release_and_retreat_requires_observed_open():
    source = (SCRIPTS / "run_gemini_robotics_robolab.py").read_text()
    scheduler = source.index("def operation_scheduler_handler(")
    resolver = source.index(
        'required_capability="released_interaction"', scheduler
    )
    retreat = source.index("_retreat_after_release(", resolver)
    assert resolver < retreat
    scheduler_source = source[scheduler:source.index(
        "def actuator_transition_handler(", scheduler
    )]
    assert 'schedule_state.get("goal_relation", {})' in scheduler_source
    assert "goal_relation_observed" in scheduler_source
    retreat_definition = source.index("def _retreat_after_release(")
    retreat_source = source[retreat_definition:source.index("def main()", retreat_definition)]
    assert "separately executed and observed" in retreat_source
    assert "actuator disengagement" in retreat_source
    assert "command[0, 7] = 0.0" not in retreat_source


def test_runtime_transition_rejects_repeated_stalled_motion_targets():
    source = (SCRIPTS / "run_gemini_robotics_robolab.py").read_text()
    resolver = source.index("def resolve_runtime_transition(")
    resolver_end = source.index(
        'episode_trace["recoveries"] = []', resolver
    )
    resolver_source = source[resolver:resolver_end]
    initialization = resolver_source.index(
        "previous_transition_motion_outcome: dict[str, Any] | None = None"
    )
    context = resolver_source.index(
        '"previous_recovery_motion_outcome": (', initialization
    )
    execution = resolver_source.index("_move_eef_to_target(", context)
    update = resolver_source.index(
        "previous_transition_motion_outcome = (", execution
    )
    assert initialization < context < execution < update
