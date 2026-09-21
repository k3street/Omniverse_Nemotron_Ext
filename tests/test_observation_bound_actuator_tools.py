from __future__ import annotations

from pathlib import Path

import pytest

from scripts.observation_bound_motion_tools import (
    ActuatorFeedbackEventPolicy,
    ActuatorExecutorRegistry,
    ActuatorExecutorSpec,
    MotionToolValidationError,
    ObservationBoundActuatorGate,
    assess_actuator_feedback_event,
    actuator_command_outcome_invalidation_reason,
    actuator_tool_schemas,
    motion_report_yields_to_actuator,
)


def _registry() -> ActuatorExecutorRegistry:
    registry = ActuatorExecutorRegistry()
    registry.register(
        ActuatorExecutorSpec(
            executor_id="binary_clamp",
            tool_name="execute_binary_clamp",
            description="Execute a bounded binary clamp transition.",
            command_schema={
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "state": {
                        "type": "string",
                        "enum": ["engage", "disengage", "maintain"],
                    }
                },
                "required": ["state"],
            },
            configuration_schema={
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "settle_steps": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 120,
                    }
                },
            },
            capability_tags=(
                "entity_attachment.acquire",
                "entity_attachment.release",
                "actuation.observation_bound",
            ),
        )
    )
    return registry


def _call(name: str, observation_id: str, **extra):
    return {
        "function": {
            "name": name,
            "arguments": {
                "observation_id": observation_id,
                "confidence": 0.9,
                "reason": "Grounded in the fresh observation and measured state.",
                **extra,
            },
        }
    }


def test_actuator_registry_advertises_task_neutral_capability_tags():
    advertisement = _registry().advertisement()

    assert advertisement[0]["tool_family"] == "actuator"
    assert advertisement[0]["capability_tags"] == [
        "entity_attachment.acquire",
        "entity_attachment.release",
        "actuation.observation_bound",
    ]
    assert advertisement[0]["invocation_schema"] == advertisement[0][
        "command_schema"
    ]


def _gate(observation_id: str = "actuator-7") -> ObservationBoundActuatorGate:
    return ObservationBoundActuatorGate(
        observation_id=observation_id,
        registry=_registry(),
    )


def test_actuator_tools_are_runtime_discovered_and_protocol_is_neutral():
    schemas = actuator_tool_schemas("fresh-3", _registry())
    assert [item["function"]["name"] for item in schemas] == [
        "execute_binary_clamp",
        "hold_actuation",
        "abort_actuation",
    ]
    serialized = str(schemas).lower()
    for forbidden in ("banana", "plate", "franka", "joint", "gripper", "phase"):
        assert forbidden not in serialized


def test_model_selects_actuator_command_and_configuration():
    outcome = _gate().dispatch(
        _call(
            "execute_binary_clamp",
            "actuator-7",
            command={"state": "engage"},
            executor_config={"settle_steps": 48},
        )
    )
    assert outcome.action == "execute"
    assert outcome.executor_id == "binary_clamp"
    assert outcome.command == {"state": "engage"}
    assert outcome.executor_config == {"settle_steps": 48}


@pytest.mark.parametrize(
    ("tool_name", "expected_action"),
    [("hold_actuation", "hold"), ("abort_actuation", "abort")],
)
def test_control_tools_do_not_emit_an_actuator_command(
    tool_name: str, expected_action: str
):
    outcome = _gate().dispatch(_call(tool_name, "actuator-7"))
    assert outcome.action == expected_action
    assert outcome.command == {}


def test_stale_actuator_observation_is_rejected():
    with pytest.raises(MotionToolValidationError, match="stale observation"):
        _gate().dispatch(
            _call(
                "execute_binary_clamp",
                "old-token",
                command={"state": "engage"},
            )
        )


def test_actuator_observation_is_single_use():
    gate = _gate()
    gate.dispatch(
        _call(
            "execute_binary_clamp",
            "actuator-7",
            command={"state": "maintain"},
        )
    )
    with pytest.raises(MotionToolValidationError, match="already authorized"):
        gate.dispatch(_call("hold_actuation", "actuator-7"))


@pytest.mark.parametrize(
    ("command", "config"),
    [
        ({"state": "invalid"}, {}),
        ({"state": "engage", "unknown": True}, {}),
        ({"state": "engage"}, {"settle_steps": 0}),
        ({"state": "engage"}, {"settle_steps": 121}),
    ],
)
def test_invalid_actuator_command_or_configuration_is_rejected(command, config):
    with pytest.raises(MotionToolValidationError):
        _gate().dispatch(
            _call(
                "execute_binary_clamp",
                "actuator-7",
                command=command,
                executor_config=config,
            )
        )


def test_unregistered_actuator_tool_is_rejected():
    with pytest.raises(MotionToolValidationError, match="unregistered"):
        _gate().dispatch(_call("execute_unknown", "actuator-7"))


def test_model_motion_hold_yields_when_actuator_transition_is_pending():
    report = {
        "recovery_request": {
            "reason": "model_requested_hold",
            "coach_decision": {"motion_tool": {"action": "hold"}},
        }
    }
    assert motion_report_yields_to_actuator(
        report, actuator_transition_pending=True
    )
    assert not motion_report_yields_to_actuator(
        report, actuator_transition_pending=False
    )


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
def test_non_hold_or_safety_recovery_never_yields_to_actuator(report):
    assert not motion_report_yields_to_actuator(
        report, actuator_transition_pending=True
    )


def _feedback_event(**overrides):
    values = {
        "position_before": 0.0,
        "position_after": 0.20,
        "force_before_n": 0.0,
        "force_after_n": 1.2,
        "touch_before": False,
        "touch_after": True,
        "policy": ActuatorFeedbackEventPolicy(
            minimum_position_change=0.05,
            minimum_force_change_n=0.25,
        ),
    }
    values.update(overrides)
    return assess_actuator_feedback_event(**values)


def test_position_and_tactile_change_trigger_immediate_feedback():
    event = _feedback_event()
    assert event.triggered
    assert event.actuator_position_changed
    assert event.tactile_changed
    assert event.touch_changed


def test_position_change_without_tactile_change_does_not_trigger():
    event = _feedback_event(
        force_after_n=0.1,
        touch_after=False,
    )
    assert event.actuator_position_changed
    assert not event.tactile_changed
    assert not event.triggered


def test_tactile_change_without_position_change_does_not_trigger():
    event = _feedback_event(position_after=0.01)
    assert not event.actuator_position_changed
    assert event.tactile_changed
    assert not event.triggered


def test_ineffective_engagement_invalidates_command_outcome():
    assert actuator_command_outcome_invalidation_reason(
        requested_state="engage",
        actuator_position_changed=False,
        loaded_contact_supported=False,
    ) == "engagement_produced_no_motion_or_supported_loaded_contact"


@pytest.mark.parametrize(
    ("requested_state", "position_changed", "contact_supported"),
    [
        ("engage", True, False),
        ("engage", False, True),
        ("disengage", False, False),
        ("maintain", False, False),
    ],
)
def test_effective_or_nonengagement_command_is_not_invalidated(
    requested_state, position_changed, contact_supported
):
    assert actuator_command_outcome_invalidation_reason(
        requested_state=requested_state,
        actuator_position_changed=position_changed,
        loaded_contact_supported=contact_supported,
    ) is None


def test_force_delta_can_trigger_tactile_change_without_touch_transition():
    event = _feedback_event(touch_before=True, touch_after=True)
    assert not event.touch_changed
    assert event.tactile_changed
    assert event.triggered


def test_runner_requires_fresh_confirmation_for_terminal_actuator_abort():
    source = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "run_gemini_robotics_robolab.py"
    ).read_text()
    prompt_start = source.index("def _actuator_governor_prompt(")
    prompt_end = source.index(
        "def _choose_observation_bound_actuator_tool(", prompt_start
    )
    prompt = source[prompt_start:prompt_end]
    assert "abort_actuation is a terminal task abort" in prompt
    assert "select hold_actuation" in prompt
    assert "This restriction applies only to engagement" in prompt
    assert "Never use failed-grasp" in prompt
    assert "dispatch supplies the fresh reason" in prompt
    assert "named per-body forces" in prompt
    assert "aggregate force or touch alone does not prove" in prompt
    assert "pairwise_force_direction_cosine" in prompt
    assert "force_magnitude_ratio_min_over_max" in prompt
    assert "trigger_event.actuator_outcome_invalidated" in prompt
    assert "Do not issue that same requested state again" in prompt
    assert "disengage it to permit a pose correction" in prompt
    assert "failed_grasp_pose_comparisons" in prompt
    assert "translation_delta_m and orientation_delta_deg" in prompt

    scheduler_prompt_start = source.index("def _operation_scheduler_prompt(")
    scheduler_prompt_end = source.index(
        "def _choose_observation_bound_operation(", scheduler_prompt_start
    )
    scheduler_prompt = source[scheduler_prompt_start:scheduler_prompt_end]
    assert "actuator_outcome_invalidated" in scheduler_prompt
    assert "do not repeat" in scheduler_prompt
    assert "failed_grasp_pose_comparisons" in scheduler_prompt

    feedback_start = source.index("def _actuator_feedback_event_from_execution(")
    feedback_end = source.index("def _move_eef_to_target(", feedback_start)
    feedback = source[feedback_start:feedback_end]
    assert "retained_contact_supports_loaded_actuator(" in feedback
    assert "actuator_command_outcome_invalidation_reason(" in feedback
    assert 'result["triggered"] = True' in feedback

    post_feedback = source.index("feedback_trigger_event = {")
    assert '"type": "actuator_physical_outcome_observed"' in source[
        post_feedback : post_feedback + 300
    ]
    repeated_actuator = source.index(
        "repeated_actuator_decision,", post_feedback
    )
    repeated_call_end = source.index(
        ")\n                    actuator_latency", repeated_actuator
    )
    repeated_call = source[repeated_actuator:repeated_call_end]
    assert "trigger_event=feedback_trigger_event" in repeated_call
    assert "scheduler_dispatch=post_feedback_decision" in repeated_call
    assert "yield_on_hold=True" in repeated_call

    post_feedback_loop = source[post_feedback:repeated_call_end + 8000]
    assert "pending_scheduler_trigger_event" in post_feedback_loop
    assert '"type": "actuator_governor_yielded_to_scheduler"' in (
        post_feedback_loop
    )
    assert "post_feedback_motion_decision = motion_checkpoint_handler(" in (
        post_feedback_loop
    )
    assert 'f"{phase}:post_actuation_motion"' in post_feedback_loop
    assert "post_feedback_motion_handoffs" in post_feedback_loop
    assert "post_actuation_feedback_budget_yield" in post_feedback_loop
    assert '"task_success_assumed": False' in post_feedback_loop
    assert "Post-actuation feedback reschedule budget exhausted" not in (
        post_feedback_loop
    )

    handler_start = source.index("def actuator_transition_handler(")
    handler_end = source.index("stages = []", handler_start)
    handler = source[handler_start:handler_end]
    abort = handler.index('decision.get("decision") == "abort"')
    confirmation = handler.index('if attempt == 0:', abort)
    hold = handler.index("_hold_joint_action(", confirmation)
    terminal_abort = handler.index("Actuator governor aborted", hold)
    assert abort < confirmation < hold < terminal_abort
    assert '"status": "confirmation_required"' in handler[confirmation:hold]
    assert "yield_on_hold: bool = False" in handler
    assert "scheduler_dispatch: dict[str, Any] | None = None" in handler
    assert '"scheduler_dispatch": scheduler_dispatch' in handler
    assert "if yield_on_hold:" in handler
    yield_branch = handler.index("if yield_on_hold:")
    yield_return = handler.index("return transition_obs", yield_branch)
    assert "_hold_joint_action(" in handler[yield_branch:yield_return]

    lift_recovery_start = source.index(
        'phase_label="lift:measured_outcome_not_met"', handler_end
    )
    lift_recovery = source[lift_recovery_start : lift_recovery_start + 4000]
    assert "yield_on_hold=True" in lift_recovery
    assert "scheduler_dispatch=recovery_schedule" in lift_recovery
    assert 'recovery_event["yielded_to_scheduler"] = True' in lift_recovery
    assert '"scheduler_handoff"' in lift_recovery

    boundary = source.index(
        'phase_label=f"{phase}:boundary_hold"', handler_end
    )
    boundary_call = source[boundary : boundary + 3000]
    assert "scheduler_dispatch=boundary_schedule" in boundary_call


def test_feedback_event_policy_is_explicit():
    policy = ActuatorFeedbackEventPolicy(0.05, 0.0)
    assert _feedback_event(policy=policy).triggered
    with pytest.raises(MotionToolValidationError, match="policy"):
        assess_actuator_feedback_event(
            position_before=0.0,
            position_after=0.2,
            force_before_n=0.0,
            force_after_n=1.0,
            touch_before=False,
            touch_after=True,
            policy=None,
        )


def test_runner_expires_carried_object_latches_after_disengagement():
    source = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "run_gemini_robotics_robolab.py"
    ).read_text()
    helper_start = source.index("def reconcile_carry_latch_after_actuation(")
    helper_end = source.index("def capture_grasp_attempt(", helper_start)
    helper = source[helper_start:helper_end]
    assert 'execution.get("engaged_after")' in helper
    assert "latched_carry_offset = None" in helper
    assert "latched_carry_quaternion = None" in helper
    assert "latched_rgbd_axis_references = {}" in helper
    assert '"reason": "actuator_disengaged"' in helper
    assert '"carry_latch_expiration"' in helper
    assert source.count("reconcile_carry_latch_after_actuation(") == 7
