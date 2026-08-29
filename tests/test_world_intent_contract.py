import json
import math

import pytest

from scripts.world_intent_contract import (
    WORLD_INTENT_SCHEMA_VERSION,
    WorldIntent,
    WorldIntentValidationError,
    build_world_intent_prompt,
    parse_world_intent_json,
    world_intent_json_schema,
)


def relation_intent():
    return {
        "schema_version": WORLD_INTENT_SCHEMA_VERSION,
        "intent_id": "intent-0001",
        "operation": "achieve",
        "goals": [
            {
                "subject_id": "entity-17",
                "attribute": "spatial_relation",
                "operator": "equals",
                "value": "inside",
                "reference_id": "entity-4",
            }
        ],
        "constraints": [
            {
                "subject_id": "entity-17",
                "attribute": "orientation_class",
                "operator": "maintain",
                "value": "upright",
            }
        ],
        "reobserve_after": "state_change",
        "confidence": 0.91,
    }


def test_world_relation_intent_round_trips_without_control_commands():
    payload = relation_intent()
    intent = WorldIntent.from_mapping(payload)

    assert intent.to_dict() == payload
    assert parse_world_intent_json(json.dumps(payload)) == intent


def test_contract_accepts_arbitrary_json_world_state_values():
    payload = relation_intent()
    payload["goals"][0].update(
        attribute="pose_region",
        operator="within",
        value={"frame_id": "region-2", "bounds": [0.1, 0.2, 0.3]},
    )

    assert WorldIntent.from_mapping(payload).goals[0].value["frame_id"] == "region-2"


@pytest.mark.parametrize("operation", ["observe", "achieve", "verify", "complete"])
def test_nonterminal_and_completion_operations_require_a_world_goal(operation):
    payload = relation_intent()
    payload["operation"] = operation
    payload["goals"] = []

    with pytest.raises(WorldIntentValidationError, match="requires at least one"):
        WorldIntent.from_mapping(payload)


def test_unable_operation_may_have_no_goal():
    payload = relation_intent()
    payload.update(operation="unable", goals=[], reobserve_after="never")

    assert WorldIntent.from_mapping(payload).operation == "unable"


@pytest.mark.parametrize(
    "field,value,match",
    [
        ("schema_version", "other.v1", "unsupported schema_version"),
        ("operation", "move_something", "unsupported operation"),
        ("reobserve_after", "later", "unsupported reobserve_after"),
        ("confidence", 1.01, "confidence"),
        ("confidence", math.nan, "confidence"),
    ],
)
def test_invalid_top_level_values_are_rejected(field, value, match):
    payload = relation_intent()
    payload[field] = value

    with pytest.raises(WorldIntentValidationError, match=match):
        WorldIntent.from_mapping(payload)


def test_unknown_fields_cannot_smuggle_control_commands_into_the_contract():
    payload = relation_intent()
    payload["joint_targets"] = [0.0]

    with pytest.raises(WorldIntentValidationError, match="unknown fields"):
        WorldIntent.from_mapping(payload)


def test_predicates_reject_unknown_fields_and_non_json_values():
    unknown = relation_intent()
    unknown["goals"][0]["trajectory"] = []
    with pytest.raises(WorldIntentValidationError, match="unknown fields"):
        WorldIntent.from_mapping(unknown)

    non_json = relation_intent()
    non_json["goals"][0]["value"] = object()
    with pytest.raises(WorldIntentValidationError, match="JSON-compatible"):
        WorldIntent.from_mapping(non_json)


def test_provider_schema_has_no_task_or_embodiment_control_fields():
    schema = world_intent_json_schema()
    property_names = set(schema["properties"])
    property_names.update(schema["properties"]["goals"]["items"]["properties"])
    forbidden = {
        "task_phase",
        "object_name",
        "robot",
        "joint",
        "joint_targets",
        "end_effector",
        "eef_pose",
        "gripper",
        "grasp_pose",
        "controller",
    }

    assert property_names.isdisjoint(forbidden)
    assert schema["additionalProperties"] is False
    assert schema["properties"]["goals"]["items"]["additionalProperties"] is False


def test_parser_rejects_markdown_and_non_json_responses():
    with pytest.raises(WorldIntentValidationError, match="invalid world intent JSON"):
        parse_world_intent_json("```json\n{}\n```")


def test_prompt_requests_world_state_without_embedding_control_vocabulary():
    prompt = build_world_intent_prompt(
        "Make entity A be inside entity B while maintaining clearance from entity C"
    )
    lowered = prompt.lower()

    assert "maintaining clearance from entity c" in lowered
    assert "predicates in `constraints`" in lowered
    for forbidden in (
        "joint target",
        "end effector",
        "gripper",
        "inverse kinematics",
        "franka",
        "droid",
    ):
        assert forbidden not in lowered


def test_constraints_remain_model_defined_world_predicates():
    payload = relation_intent()
    payload["constraints"] = [
        {
            "subject_id": "entity-17",
            "attribute": "model_defined_spatial_relation",
            "operator": "maintain",
            "value": {"distance_m": 0.2, "axis": "observed_up"},
            "reference_id": "entity-4",
        }
    ]
    intent = WorldIntent.from_mapping(payload)
    assert intent.constraints[0].attribute == "model_defined_spatial_relation"
    assert intent.constraints[0].value == {
        "distance_m": 0.2,
        "axis": "observed_up",
    }
