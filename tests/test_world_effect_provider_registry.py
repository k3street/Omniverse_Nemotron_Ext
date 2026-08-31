import json

import pytest

from scripts.world_effect_provider_registry import (
    RuntimeToolCapability,
    WorldEffectProviderError,
    default_world_effect_provider_registry,
)


def rgbd_tool():
    return RuntimeToolCapability(
        tool_id="sensor.scene_geometry",
        tool_family="sensor",
        capability_tags=("scene.geometry.rgbd",),
        activation_status="active",
        source="test_sensor_registry",
    )


def motion_tool(*, active=False, tool_id="motion.spatial_target"):
    return RuntimeToolCapability(
        tool_id=tool_id,
        tool_family="motion",
        capability_tags=(
            "spatial.pose_target",
            "motion.observation_bound",
            "motion.invalidation_feedback",
        ),
        activation_status="active" if active else "factory_available",
        source="test_motion_registry",
    )


def attachment_tool(*, active=False, tool_id="attachment.reversible"):
    return RuntimeToolCapability(
        tool_id=tool_id,
        tool_family="actuator",
        capability_tags=(
            "entity_attachment.acquire",
            "entity_attachment.release",
            "actuation.observation_bound",
        ),
        activation_status="active" if active else "factory_available",
        source="test_actuator_registry",
    )


def test_available_factories_form_a_compatible_but_inactive_binding():
    assessment = default_world_effect_provider_registry().assess(
        "world_relation.realize_inside",
        [rgbd_tool(), motion_tool(), attachment_tool()],
    )
    result = assessment.to_dict()

    assert result["binding_ready"]
    assert not result["active_binding_ready"]
    assert result["preferred_provider_id"] == (
        "transport.reversible_attachment"
    )
    binding = result["bindings"][0]
    assert binding["compatible"]
    assert not binding["active"]
    assert binding["missing_requirement_ids"] == []
    assert result["execution_authority"] is False


def test_active_runtime_tools_form_an_active_binding_without_naming_mechanism():
    assessment = default_world_effect_provider_registry().assess(
        "world_relation.realize_inside",
        [
            rgbd_tool(),
            motion_tool(active=True, tool_id="whole_body.reach"),
            attachment_tool(active=True, tool_id="vacuum.acquire_release"),
        ],
    )
    binding = assessment.preferred_binding()

    assert assessment.binding_ready
    assert assessment.active_binding_ready
    assert binding is not None and binding.active
    selected = {
        item["requirement_id"]: item["tool_id"]
        for item in binding.requirement_bindings
    }
    assert selected["observation_bound_spatial_motion"] == "whole_body.reach"
    assert selected["reversible_entity_attachment"] == (
        "vacuum.acquire_release"
    )


def test_all_compatible_runtime_motion_tools_remain_available_to_planning():
    assessment = default_world_effect_provider_registry().assess(
        "world_relation.realize_inside",
        [
            rgbd_tool(),
            motion_tool(active=True, tool_id="bounded_dls_ik"),
            motion_tool(active=True, tool_id="bounded_dls_waypoint_path"),
            attachment_tool(active=True),
        ],
    )
    binding = assessment.preferred_binding()
    assert binding is not None
    motion_binding = next(
        item
        for item in binding.requirement_bindings
        if item["requirement_id"] == "observation_bound_spatial_motion"
    )

    assert motion_binding["tool_id"] == "bounded_dls_ik"
    assert motion_binding["compatible_tools"] == [
        {"tool_id": "bounded_dls_ik", "activation_status": "active"},
        {
            "tool_id": "bounded_dls_waypoint_path",
            "activation_status": "active",
        },
    ]


def test_missing_semantic_tool_requirement_fails_closed():
    assessment = default_world_effect_provider_registry().assess(
        "world_relation.realize_inside",
        [rgbd_tool(), motion_tool(active=True)],
    )
    binding = assessment.bindings[0]

    assert not assessment.binding_ready
    assert not assessment.active_binding_ready
    assert binding.missing_requirement_ids == (
        "reversible_entity_attachment",
    )


def test_provider_recipe_is_embodiment_neutral():
    advertisement = json.dumps(
        default_world_effect_provider_registry().advertisement()
    ).lower()

    for forbidden in (
        "franka",
        "droid",
        "joint",
        "inverse kinematics",
        "gripper",
        "suction",
        "dual arm",
    ):
        assert forbidden not in advertisement


def test_duplicate_runtime_tool_ids_are_rejected():
    duplicate = rgbd_tool()
    with pytest.raises(WorldEffectProviderError, match="duplicate runtime tool"):
        default_world_effect_provider_registry().assess(
            "world_relation.realize_inside",
            [duplicate, duplicate],
        )


def test_active_tool_can_carry_its_runtime_owned_typed_advertisement():
    tool = RuntimeToolCapability(
        tool_id="bounded_runtime_motion",
        tool_family="motion",
        capability_tags=(
            "spatial.pose_target",
            "motion.observation_bound",
            "motion.invalidation_feedback",
        ),
        activation_status="active",
        source="active_test_registry",
        tool_advertisement={
            "executor_id": "bounded_runtime_motion",
            "tool_name": "execute_bounded_runtime_motion",
            "tool_family": "motion",
            "invocation_schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {"target": {"type": "number"}},
                "required": ["target"],
            },
        },
    )

    advertisement = tool.to_dict()
    assert advertisement["executor_id"] == "bounded_runtime_motion"
    assert advertisement["invocation_schema"]["required"] == ["target"]
    assert advertisement["tool_family"] == "motion"
    assert advertisement["execution_authority"] is False

    with pytest.raises(WorldEffectProviderError, match="family must match"):
        RuntimeToolCapability(
            tool_id="bounded_runtime_motion",
            tool_family="motion",
            capability_tags=("spatial.pose_target",),
            activation_status="active",
            source="active_test_registry",
            tool_advertisement={
                "executor_id": "bounded_runtime_motion",
                "tool_family": "actuator",
            },
        )
