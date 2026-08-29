from __future__ import annotations

import pytest

from scripts.observation_bound_motion_tools import (
    ActuatorExecutorRegistry,
    ActuatorExecutorSpec,
    MotionToolValidationError,
    ObservationBoundActuatorGate,
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
