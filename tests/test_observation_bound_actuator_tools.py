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

    handler_start = source.index("def actuator_transition_handler(")
    handler_end = source.index("stages = []", handler_start)
    handler = source[handler_start:handler_end]
    abort = handler.index('decision.get("decision") == "abort"')
    confirmation = handler.index('if attempt == 0:', abort)
    hold = handler.index("_hold_joint_action(", confirmation)
    terminal_abort = handler.index("Actuator governor aborted", hold)
    assert abort < confirmation < hold < terminal_abort
    assert '"status": "confirmation_required"' in handler[confirmation:hold]


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
