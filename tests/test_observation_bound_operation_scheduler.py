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
    motion_report_yields_to_scheduler,
    operation_scheduler_tool_schemas,
)


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
    assert '"type": "actuator_and_tactile_state_changed"' in source[
        assessment:next_workspace_feedback
    ]


def test_runner_preserves_loaded_actuator_but_readmits_it_after_contact_loss():
    source = (SCRIPTS / "run_gemini_robotics_robolab.py").read_text()
    handler_start = source.index("def operation_scheduler_handler(")
    handler_end = source.index("def actuator_transition_handler(", handler_start)
    handler = source[handler_start:handler_end]
    predicate = handler.index(
        'schedule_state.get("banana_plate_contact_proxy", False)'
    )
    retained_contact = handler.index(
        "retained_contact_observed = bool(", predicate
    )
    recovery = handler.index(
        "actuator_recovery_observed = bool(", retained_contact
    )
    admission = handler.index("actuator_transition_available = bool(", recovery)
    candidates = handler.index("_post_motion_operation_candidates(", admission)
    model_call = handler.index("_choose_observation_bound_operation(", candidates)
    assert predicate < retained_contact < recovery < admission < candidates < model_call
    assert "current_engaged and not retained_contact_observed" in handler
    assert '"source": "measured_runtime_actuator_preconditions"' in handler


def test_phase_boundary_hold_yields_to_scheduler_then_motion_replan():
    source = (SCRIPTS / "run_gemini_robotics_robolab.py").read_text()
    hold = source.index(
        'decision["motion_tool"].get("action") == "hold"'
    )
    scheduler = source.index("operation_scheduler_handler(", hold)
    actuator_branch = source.index(
        'boundary_schedule.get("operation_kind") == "actuation"', scheduler
    )
    replan = source.index("decision = motion_checkpoint_handler(", actuator_branch)
    terminal = source.index(
        'if decision.get("decision") != "execute"', replan
    )
    assert hold < scheduler < actuator_branch < replan < terminal
    assert '"type": "phase_boundary_model_hold"' in source[hold:replan]
    assert '"reason": "scheduler_requested_boundary_replan"' in source[
        actuator_branch:terminal
    ]


def test_failed_measured_lift_routes_through_fresh_operation_recovery():
    source = (SCRIPTS / "run_gemini_robotics_robolab.py").read_text()
    lift_gate = source.index('if phase == "lift":')
    outcome_loop = source.index(
        "for recovery_index in range(args_cli.max_transport_recoveries)",
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
