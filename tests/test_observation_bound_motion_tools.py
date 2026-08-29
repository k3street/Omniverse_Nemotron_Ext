from __future__ import annotations

import pytest

from scripts.observation_bound_motion_tools import (
    MotionExecutorRegistry,
    MotionExecutorSpec,
    MotionToolValidationError,
    ObservationBoundMotionGate,
    motion_tool_schemas,
)


def _registry() -> MotionExecutorRegistry:
    registry = MotionExecutorRegistry()
    registry.register(
        MotionExecutorSpec(
            executor_id="workspace_controller",
            tool_name="execute_workspace_motion",
            description="Execute the current world-space target.",
            configuration_schema={
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "step_limit_m": {
                        "type": "number",
                        "minimum": 0.001,
                        "maximum": 0.1,
                    },
                    "iterations": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 100,
                    },
                },
            },
        )
    )
    return registry


def _call(name: str, observation_id: str, **extra):
    arguments = {
        "observation_id": observation_id,
        "confidence": 0.9,
        "reason": "Grounded in the fresh RGB-D observation.",
        **extra,
    }
    return {"function": {"name": name, "arguments": arguments}}


def _gate(observation_id: str = "obs-7") -> ObservationBoundMotionGate:
    return ObservationBoundMotionGate(
        observation_id=observation_id,
        current_target_m=[0.4, -0.1, 0.2],
        maximum_correction_m=0.1,
        registry=_registry(),
    )


def test_schemas_are_runtime_discovered_and_protocol_is_neutral():
    schemas = motion_tool_schemas("fresh-42", _registry())
    names = [item["function"]["name"] for item in schemas]
    assert names == [
        "execute_workspace_motion",
        "hold_motion",
        "abort_motion",
    ]
    serialized = str(schemas).lower()
    for forbidden in ("banana", "plate", "franka", "joint", "gripper", "phase"):
        assert forbidden not in serialized
    for item in schemas:
        observation = item["function"]["parameters"]["properties"][
            "observation_id"
        ]
        assert observation["const"] == "fresh-42"


def test_model_selects_executor_config_and_corrects_world_target():
    outcome = _gate().dispatch(
        _call(
            "execute_workspace_motion",
            "obs-7",
            translation_delta_m=[0.0, 0.02, 0.08],
            executor_config={"step_limit_m": 0.015, "iterations": 30},
        )
    )
    assert outcome.action == "execute"
    assert outcome.executor_id == "workspace_controller"
    assert outcome.executor_config == {
        "step_limit_m": 0.015,
        "iterations": 30,
    }
    assert outcome.target_before_m == pytest.approx((0.4, -0.1, 0.2))
    assert outcome.target_after_m == pytest.approx((0.4, -0.08, 0.28))


def test_executor_can_run_with_runtime_defaults_and_no_correction():
    outcome = _gate().dispatch(_call("execute_workspace_motion", "obs-7"))
    assert outcome.action == "execute"
    assert outcome.executor_config == {}
    assert outcome.target_after_m == outcome.target_before_m


@pytest.mark.parametrize(
    ("tool_name", "expected_action"),
    [("hold_motion", "hold"), ("abort_motion", "abort")],
)
def test_control_tools_preserve_target(tool_name: str, expected_action: str):
    outcome = _gate().dispatch(_call(tool_name, "obs-7"))
    assert outcome.action == expected_action
    assert outcome.executor_id is None
    assert outcome.target_after_m == outcome.target_before_m


def test_second_executor_is_discovered_without_protocol_change():
    registry = _registry()
    registry.register(
        MotionExecutorSpec(
            executor_id="learned_policy",
            tool_name="execute_learned_policy",
            description="Execute using an available learned policy.",
            configuration_schema={
                "type": "object",
                "additionalProperties": False,
                "properties": {"horizon": {"type": "integer", "minimum": 1}},
            },
        )
    )
    assert [item["function"]["name"] for item in motion_tool_schemas("obs", registry)] == [
        "execute_learned_policy",
        "execute_workspace_motion",
        "hold_motion",
        "abort_motion",
    ]


def test_stale_observation_is_rejected():
    with pytest.raises(MotionToolValidationError, match="stale observation"):
        _gate().dispatch(_call("execute_workspace_motion", "obs-6"))


def test_observation_can_authorize_only_one_call():
    gate = _gate()
    gate.dispatch(_call("execute_workspace_motion", "obs-7"))
    with pytest.raises(MotionToolValidationError, match="already authorized"):
        gate.dispatch(_call("execute_workspace_motion", "obs-7"))


def test_unregistered_executor_tool_is_rejected():
    with pytest.raises(MotionToolValidationError, match="unregistered"):
        _gate().dispatch(_call("execute_unknown", "obs-7"))


def test_correction_over_safety_limit_is_rejected():
    with pytest.raises(MotionToolValidationError, match="exceeds"):
        _gate().dispatch(
            _call(
                "execute_workspace_motion",
                "obs-7",
                translation_delta_m=[0.0, 0.0, 0.11],
            )
        )


@pytest.mark.parametrize(
    "config",
    (
        {"step_limit_m": 0.2},
        {"iterations": 0},
        {"unknown": True},
    ),
)
def test_invalid_executor_configuration_is_rejected(config):
    with pytest.raises(MotionToolValidationError):
        _gate().dispatch(
            _call(
                "execute_workspace_motion",
                "obs-7",
                executor_config=config,
            )
        )
