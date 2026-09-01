from __future__ import annotations

from pathlib import Path

import pytest

from scripts.observation_bound_motion_tools import (
    MotionExecutorRegistry,
    MotionExecutorSpec,
    MotionToolValidationError,
    ObservationBoundMotionGate,
    ObservationBoundTaskFeasibilityGate,
    compare_motion_invocation_to_recent_failures,
    motion_tool_schemas,
    opposing_contact_force_capacity,
    task_feasibility_tool_schema,
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
            capability_tags=(
                "spatial.pose_target",
                "motion.observation_bound",
            ),
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


def test_motion_registry_advertises_task_neutral_capability_tags():
    advertisement = _registry().advertisement()

    assert advertisement[0]["tool_family"] == "motion"
    assert advertisement[0]["capability_tags"] == [
        "spatial.pose_target",
        "motion.observation_bound",
    ]
    assert "invocation_schema" not in advertisement[0]


def _gate(observation_id: str = "obs-7") -> ObservationBoundMotionGate:
    return ObservationBoundMotionGate(
        observation_id=observation_id,
        current_target_m=[0.4, -0.1, 0.2],
        current_target_quaternion_wxyz=[1.0, 0.0, 0.0, 0.0],
        maximum_correction_m=0.1,
        maximum_rotation_correction_deg=45.0,
        registry=_registry(),
    )


def _feasibility_call(observation_id: str = "preflight-1", **updates):
    arguments = {
        "observation_id": observation_id,
        "confidence": 0.9,
        "reason": "The published physical evidence supports the assessment.",
        "movable_object_visible": True,
        "target_receptacle_visible": True,
        "reachability": "feasible",
        "grasp_feasibility": "feasible",
        "payload_feasibility": "feasible",
        "task_feasibility": "feasible",
        "motion_authorized": True,
        "blocking_reasons": [],
        "required_runtime_evidence": [],
        "recommended_operations": ["approach", "interact", "transport"],
    }
    arguments.update(updates)
    return {
        "function": {
            "name": "assess_task_feasibility",
            "arguments": arguments,
        }
    }


def test_task_feasibility_schema_is_observation_bound_and_neutral():
    schemas = task_feasibility_tool_schema("preflight-1")
    assert [item["function"]["name"] for item in schemas] == [
        "assess_task_feasibility"
    ]
    parameters = schemas[0]["function"]["parameters"]
    assert parameters["properties"]["observation_id"]["const"] == "preflight-1"
    serialized = str(schemas).lower()
    for forbidden in ("mustard", "franka", "robotiq", "banana", "plate"):
        assert forbidden not in serialized


def test_task_feasibility_gate_admits_only_complete_physical_feasibility():
    outcome = ObservationBoundTaskFeasibilityGate(
        observation_id="preflight-1"
    ).dispatch(_feasibility_call())
    assert outcome.motion_authorized is True
    assert outcome.task_feasibility == "feasible"
    assert outcome.recommended_operations == (
        "approach",
        "interact",
        "transport",
    )


def test_task_feasibility_gate_rejects_authorized_unknown_payload():
    with pytest.raises(MotionToolValidationError, match="motion_authorized"):
        ObservationBoundTaskFeasibilityGate(
            observation_id="preflight-1"
        ).dispatch(
            _feasibility_call(
                payload_feasibility="unknown",
                task_feasibility="unknown",
                motion_authorized=True,
                required_runtime_evidence=["continuous grip-force capacity"],
            )
        )


def test_task_feasibility_gate_preserves_unknown_as_no_motion_authority():
    outcome = ObservationBoundTaskFeasibilityGate(
        observation_id="preflight-1"
    ).dispatch(
        _feasibility_call(
            payload_feasibility="unknown",
            task_feasibility="unknown",
            motion_authorized=False,
            required_runtime_evidence=["continuous grip-force capacity"],
        )
    )
    assert outcome.motion_authorized is False
    assert outcome.required_runtime_evidence == (
        "continuous grip-force capacity",
    )


def test_opposing_contact_capacity_uses_live_jacobian_virtual_work():
    capacity = opposing_contact_force_capacity(
        joint_effort_limit=10.0,
        contact_point_linear_jacobian_columns=[
            [0.05, 0.0, 0.0],
            [-0.05, 0.0, 0.0],
        ],
        closing_axis=[1.0, 0.0, 0.0],
        effective_dynamic_friction=0.5,
        gravity_m_s2=10.0,
    )
    assert capacity["generalized_effort_per_unit_contact_force_m"] == pytest.approx(
        0.1
    )
    assert capacity["normal_force_per_contact_n"] == pytest.approx(100.0)
    assert capacity["total_opposing_normal_force_n"] == pytest.approx(200.0)
    assert capacity["friction_supported_tangential_load_n"] == pytest.approx(100.0)
    assert capacity["physics_derived_payload_capacity_kg"] == pytest.approx(10.0)


def test_opposing_contact_capacity_rejects_unobservable_mechanical_advantage():
    with pytest.raises(
        MotionToolValidationError,
        match="does not expose closing-axis mechanical advantage",
    ):
        opposing_contact_force_capacity(
            joint_effort_limit=10.0,
            contact_point_linear_jacobian_columns=[
                [0.05, 0.0, 0.0],
                [0.05, 0.0, 0.0],
            ],
            closing_axis=[1.0, 0.0, 0.0],
        )


def test_recent_failed_motion_target_requires_materially_distinct_recovery():
    history = {
        "entries": [
            {
                "operation_index": 6,
                "tool_family": "motion",
                "result": {
                    "converged": False,
                    "revocation_reason": "dispatch.motion_not_converged",
                    "terminal_target_position_m": [0.459, -0.003, 0.211],
                    "terminal_target_quaternion_wxyz": [
                        0.7071,
                        -0.0236,
                        0.7063,
                        0.0258,
                    ],
                },
            }
        ]
    }
    assessment = compare_motion_invocation_to_recent_failures(
        recent_operation_history=history,
        proposed_checkpoints=[
            {
                "target_position_m": [0.456, -0.007, 0.212],
                "target_quaternion_wxyz": [
                    0.7187,
                    -0.0082,
                    0.6950,
                    0.0191,
                ],
            }
        ],
    )

    assert assessment["admitted"] is False
    assert assessment["reason"] == "repeated_recent_failed_motion_target"
    assert assessment["blocking_comparison"]["operation_index"] == 6


def test_successful_physical_transition_clears_failed_motion_retry_history():
    history = {
        "entries": [
            {
                "operation_index": 6,
                "tool_family": "motion",
                "result": {
                    "converged": False,
                    "terminal_target_position_m": [0.45, 0.0, 0.21],
                    "terminal_target_quaternion_wxyz": [1.0, 0.0, 0.0, 0.0],
                },
            },
            {
                "operation_index": 7,
                "tool_family": "actuator",
                "result": {
                    "final_lease_state": "consumed",
                    "requested_state": "disengage",
                },
            },
        ]
    }
    assessment = compare_motion_invocation_to_recent_failures(
        recent_operation_history=history,
        proposed_checkpoints=[
            {
                "target_position_m": [0.45, 0.0, 0.21],
                "target_quaternion_wxyz": [1.0, 0.0, 0.0, 0.0],
            }
        ],
    )

    assert assessment["admitted"] is True
    assert assessment["comparisons"] == []


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
            rotation_delta_axis_angle_deg=[0.0, 0.0, 30.0],
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
    assert outcome.target_before_quaternion_wxyz == pytest.approx(
        (1.0, 0.0, 0.0, 0.0)
    )
    assert outcome.target_after_quaternion_wxyz == pytest.approx(
        (0.965925826, 0.0, 0.0, 0.258819045)
    )


def test_executor_can_run_with_runtime_defaults_and_no_correction():
    outcome = _gate().dispatch(_call("execute_workspace_motion", "obs-7"))
    assert outcome.action == "execute"
    assert outcome.executor_config == {}
    assert outcome.target_after_m == outcome.target_before_m
    assert (
        outcome.target_after_quaternion_wxyz
        == outcome.target_before_quaternion_wxyz
    )


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


def test_rotation_correction_over_safety_limit_is_rejected():
    with pytest.raises(MotionToolValidationError, match="rotation correction"):
        _gate().dispatch(
            _call(
                "execute_workspace_motion",
                "obs-7",
                rotation_delta_axis_angle_deg=[0.0, 0.0, 45.1],
            )
        )


def test_rotation_correction_is_applied_in_world_frame():
    gate = ObservationBoundMotionGate(
        observation_id="obs-7",
        current_target_m=[0.4, -0.1, 0.2],
        current_target_quaternion_wxyz=[0.70710678, 0.70710678, 0.0, 0.0],
        maximum_correction_m=0.1,
        maximum_rotation_correction_deg=90.0,
        registry=_registry(),
    )
    outcome = gate.dispatch(
        _call(
            "execute_workspace_motion",
            "obs-7",
            rotation_delta_axis_angle_deg=[0.0, 0.0, 90.0],
        )
    )
    assert outcome.target_after_quaternion_wxyz == pytest.approx(
        (0.5, 0.5, 0.5, 0.5)
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


def test_all_motion_coach_prompts_use_per_body_contact_evidence():
    source = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "run_gemini_robotics_robolab.py"
    ).read_text()
    stage = source[
        source.index("def _stage_prompt(") : source.index(
            "def _motion_checkpoint_prompt("
        )
    ]
    checkpoint = source[
        source.index("def _motion_checkpoint_prompt(") : source.index(
            "def _motion_lease_conditions_from_config("
        )
    ]
    governor = source[
        source.index("def _motion_governor_prompt(") : source.index(
            "def _motion_registry_for_observation_sources("
        )
    ]
    for prompt in (stage, checkpoint, governor):
        assert "contact_bodies" in prompt
        assert "per-body" in prompt
        assert "pairwise_force_direction_cosine" in prompt
        assert "force_magnitude_ratio_min_over_max" in prompt
    assert "touch alone does not prove a secure grasp" in governor
    assert "initial orientation is the only valid grasp orientation" in stage
    assert "fixed downward grasp orientation" not in stage
