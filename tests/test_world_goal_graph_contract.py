import json
from pathlib import Path

import pytest

from scripts.world_goal_graph_contract import (
    SEMANTIC_SCENE_INVENTORY_SCHEMA_VERSION,
    WORLD_GOAL_GRAPH_SCHEMA_VERSION,
    WorldGoalGraph,
    build_world_goal_graph_prompt,
    parse_world_goal_graph_json,
    semantic_scene_inventory_entity_ids,
    semantic_scene_inventory_from_state,
    validate_world_goal_graph_entity_references,
    validate_world_goal_graph_revision,
    world_goal_graph_json_schema,
)
from scripts.world_intent_contract import WorldIntentValidationError


def scene_inventory():
    return {
        "schema_version": SEMANTIC_SCENE_INVENTORY_SCHEMA_VERSION,
        "available": True,
        "source": "test_rgbd",
        "frame": "robot_root",
        "entities": [
            {
                "entity_id": "observed_scene",
                "label": "observed scene",
                "observation_status": "scope",
                "geometry": {},
            },
            {
                "entity_id": "table",
                "label": "table",
                "observation_status": "visible_rgbd",
                "geometry": {},
            },
            {
                "entity_id": "grey_bin",
                "label": "grey bin",
                "observation_status": "visible_rgbd",
                "geometry": {},
            },
            {
                "entity_id": "red_block",
                "label": "red block",
                "observation_status": "visible_rgbd",
                "geometry": {},
            },
            {
                "entity_id": "green_block",
                "label": "green block",
                "observation_status": "visible_rgbd",
                "geometry": {},
            },
        ],
        "role_bindings": [],
        "limitations": [],
    }


def predicate(subject_id, attribute, operator, value, reference_id=None):
    result = {
        "subject_id": subject_id,
        "attribute": attribute,
        "operator": operator,
        "value": value,
    }
    if reference_id is not None:
        result["reference_id"] = reference_id
    return result


def goal(goal_id, desired_state, *, depends_on=(), valid_while=()):
    return {
        "goal_id": goal_id,
        "desired_state": desired_state,
        "depends_on": list(depends_on),
        "valid_while": list(valid_while),
        "completion_policy": "all",
        "reobserve_after": "state_change",
        "rationale": f"Observable outcome for {goal_id}",
    }


def clean_table_graph():
    bin_capacity = predicate(
        "grey_bin", "remaining_capacity", "greater_than", 0
    )
    return {
        "schema_version": WORLD_GOAL_GRAPH_SCHEMA_VERSION,
        "graph_id": "clean-table-0001",
        "status": "ready",
        "root_goal_ids": ["table-clear"],
        "goals": [
            goal(
                "red-in-bin",
                [
                    predicate(
                        "red_block",
                        "spatial_relation",
                        "equals",
                        "inside",
                        "grey_bin",
                    )
                ],
                valid_while=[bin_capacity],
            ),
            goal(
                "green-in-bin",
                [
                    predicate(
                        "green_block",
                        "spatial_relation",
                        "equals",
                        "inside",
                        "grey_bin",
                    )
                ],
                depends_on=["red-in-bin"],
                valid_while=[bin_capacity],
            ),
            goal(
                "table-clear",
                [
                    predicate(
                        "table",
                        "eligible_movable_contents",
                        "all_have_relation",
                        "inside",
                        "grey_bin",
                    )
                ],
                depends_on=["red-in-bin", "green-in-bin"],
            ),
        ],
        "entity_scope": [
            {
                "entity_id": "observed_scene",
                "status": "context",
                "reason": "This is the inventory observation scope.",
            },
            {
                "entity_id": "table",
                "status": "included",
                "reason": "The root outcome is defined on the table.",
            },
            {
                "entity_id": "grey_bin",
                "status": "included",
                "reason": "The bin is the goal relation reference.",
            },
            {
                "entity_id": "red_block",
                "status": "included",
                "reason": "The red block is a goal subject.",
            },
            {
                "entity_id": "green_block",
                "status": "included",
                "reason": "The green block is a goal subject.",
            },
        ],
        "constraints": [
            predicate("table", "structural_pose", "maintain", "observed")
        ],
        "required_observations": [],
        "confidence": 0.86,
        "reason": "The root remains evaluable if the table membership changes.",
    }


def test_clean_table_goal_graph_round_trips_and_is_inventory_grounded():
    payload = clean_table_graph()
    graph = WorldGoalGraph.from_mapping(payload)

    assert graph.to_dict() == payload
    assert parse_world_goal_graph_json(json.dumps(payload)) == graph
    validate_world_goal_graph_entity_references(graph, scene_inventory())


def test_graph_rejects_cycles_unknown_dependencies_and_orphans():
    cyclic = clean_table_graph()
    cyclic["goals"][0]["depends_on"] = ["table-clear"]
    with pytest.raises(WorldIntentValidationError, match="cycle"):
        WorldGoalGraph.from_mapping(cyclic)

    unknown = clean_table_graph()
    unknown["goals"][0]["depends_on"] = ["not-a-goal"]
    with pytest.raises(WorldIntentValidationError, match="unknown goals"):
        WorldGoalGraph.from_mapping(unknown)

    orphaned = clean_table_graph()
    orphaned["goals"].append(
        goal(
            "orphan",
            [predicate("observed_scene", "lighting", "equals", "observed")],
        )
    )
    with pytest.raises(WorldIntentValidationError, match="not causal prerequisites"):
        WorldGoalGraph.from_mapping(orphaned)


def test_graph_rejects_entities_absent_from_fresh_inventory():
    payload = clean_table_graph()
    payload["goals"][0]["desired_state"][0]["subject_id"] = "invented_object"
    graph = WorldGoalGraph.from_mapping(payload)

    with pytest.raises(WorldIntentValidationError, match="absent from inventory"):
        validate_world_goal_graph_entity_references(graph, scene_inventory())


def test_needs_observation_requires_a_world_predicate():
    payload = clean_table_graph()
    payload.update(status="needs_observation", required_observations=[])
    with pytest.raises(WorldIntentValidationError, match="at least one"):
        WorldGoalGraph.from_mapping(payload)

    payload["required_observations"] = [
        predicate("red_block", "movability", "is_known", True)
    ]
    assert WorldGoalGraph.from_mapping(payload).status == "needs_observation"


def test_entity_scope_requires_unique_decisions_and_known_statuses():
    duplicate = clean_table_graph()
    duplicate["entity_scope"].append(dict(duplicate["entity_scope"][0]))
    with pytest.raises(WorldIntentValidationError, match="must be unique"):
        WorldGoalGraph.from_mapping(duplicate)

    unknown_status = clean_table_graph()
    unknown_status["entity_scope"][0]["status"] = "maybe"
    with pytest.raises(WorldIntentValidationError, match="unsupported value"):
        WorldGoalGraph.from_mapping(unknown_status)


def test_scene_inventory_exposes_all_rgbd_entities_and_role_bound_occlusions():
    state = {
        "rgbd_scene_geometry": {
            "available": True,
            "source": "synchronized_rgbd_instance_geometry",
            "frame": "robot_root",
            "geometries": [
                {
                    "runtime_id": "red_block",
                    "center_base_m": [0.4, 0.2, 0.03],
                    "visible_extent_base_m": [0.05, 0.05, 0.05],
                },
                {
                    "runtime_id": "grey_bin",
                    "center_base_m": [0.45, -0.2, 0.07],
                },
                {
                    "runtime_id": "table",
                    "center_base_m": [0.4, 0.1, 0.0],
                },
            ],
        },
        "scene_roles": {
            "movable_object": {"asset": "red_block", "label": "red block"},
            "target_receptacle": {"asset": "hidden_bin", "label": "hidden bin"},
        },
    }

    inventory = semantic_scene_inventory_from_state(state)
    entities = {item["entity_id"]: item for item in inventory["entities"]}

    assert semantic_scene_inventory_entity_ids(inventory) == {
        "observed_scene",
        "red_block",
        "grey_bin",
        "table",
        "hidden_bin",
    }
    assert entities["red_block"]["geometry"]["center_base_m"] == [0.4, 0.2, 0.03]
    assert entities["hidden_bin"]["observation_status"] == "role_bound_not_visible"
    assert inventory["role_bindings"] == [
        {"role_id": "movable_object", "entity_id": "red_block"},
        {"role_id": "target_receptacle", "entity_id": "hidden_bin"},
    ]


def test_scene_inventory_attaches_current_entity_physical_evidence():
    state = {
        "rgbd_scene_geometry": {
            "available": True,
            "geometries": [
                {
                    "runtime_id": "red_block",
                    "visible_extent_base_m": [0.05, 0.05, 0.05],
                }
            ],
        },
        "entity_physical_evidence": {
            "red_block": {
                "schema_version": "world-entity-physical-evidence.v1",
                "entity_id": "red_block",
                "source": "active_simulator_physics",
                "mobility": {"available": True, "status": "dynamic"},
                "mass": {"available": True, "mass_kg": 0.1},
                "execution_authority": False,
            }
        },
    }

    inventory = semantic_scene_inventory_from_state(state)
    red = next(item for item in inventory["entities"] if item["entity_id"] == "red_block")

    assert red["physical_evidence"]["mobility"]["status"] == "dynamic"
    assert red["physical_evidence"]["mass"]["mass_kg"] == 0.1


def test_scene_inventory_rejects_stale_physical_evidence_entity():
    state = {
        "rgbd_scene_geometry": {"available": True, "geometries": []},
        "entity_physical_evidence": {
            "old_block": {"entity_id": "old_block"},
        },
    }

    with pytest.raises(WorldIntentValidationError, match="absent"):
        semantic_scene_inventory_from_state(state)


def test_schema_cannot_encode_embodiment_or_control_commands():
    schema = world_goal_graph_json_schema()
    goal_properties = schema["properties"]["goals"]["items"]["properties"]
    predicate_properties = goal_properties["desired_state"]["items"]["properties"]
    all_properties = set(schema["properties"]) | set(goal_properties) | set(
        predicate_properties
    )
    forbidden = {
        "robot",
        "arm",
        "joint",
        "joint_targets",
        "end_effector",
        "actuator",
        "gripper",
        "suction",
        "controller",
        "trajectory",
        "motion_tool",
    }

    assert all_properties.isdisjoint(forbidden)
    assert schema["additionalProperties"] is False
    assert schema["properties"]["goals"]["items"]["additionalProperties"] is False


def test_prompt_contains_complete_inventory_and_only_requests_world_outcomes():
    prompt = build_world_goal_graph_prompt("Clean the table", scene_inventory())
    lowered = prompt.lower()

    assert "clean the table" in lowered
    assert "red_block" in prompt
    assert "green_block" in prompt
    assert "grey_bin" in prompt
    assert "causal prerequisites" in lowered
    assert "collective instruction" in lowered
    assert "not a limit on which entities" in lowered
    assert "no execution\nauthority" in lowered
    for forbidden in (
        "joint target",
        "inverse kinematics",
        "franka",
        "droid",
        "parallel gripper",
        "suction cup",
    ):
        assert forbidden not in lowered


def test_revision_prompt_requests_complete_graph_without_hiding_blockers():
    prompt = build_world_goal_graph_prompt(
        "Clean the table",
        scene_inventory(),
        revision_context={
            "previous_graph": clean_table_graph(),
            "activation_blockers": [
                {
                    "goal_id": "table-clear",
                    "reason_codes": ["no_planning_ready_capability"],
                    "planning_blockers": [
                        "large_object.does_not_fit_observed_envelope_of.grey_bin"
                    ],
                }
            ],
        },
    )
    lowered = prompt.lower()

    assert "complete replacement graph" in lowered
    assert "fresh graph_id" in lowered
    assert "exact runtime blocker evidence" in lowered
    assert "scope-audit evidence" in lowered
    assert "task-membership audit" in lowered
    assert "remains independent of physical feasibility" in lowered
    assert "independent world-state changes" in lowered
    assert "independently activatable goals" in lowered
    assert "do not remove an entity" in lowered
    assert "silently\nexclude it" in lowered
    assert "large_object.does_not_fit_observed_envelope_of.grey_bin" in prompt
    for forbidden in (
        "joint target",
        "inverse kinematics",
        "franka",
        "droid",
        "parallel gripper",
        "suction cup",
    ):
        assert forbidden not in lowered


def test_revision_validation_preserves_blocked_included_subjects():
    previous = WorldGoalGraph.from_mapping(clean_table_graph())
    revised_payload = clean_table_graph()
    revised_payload["graph_id"] = "clean-table-revision-0002"
    revised = WorldGoalGraph.from_mapping(revised_payload)

    validate_world_goal_graph_revision(previous, revised, ["red-in-bin"])

    hidden_payload = clean_table_graph()
    hidden_payload["graph_id"] = "clean-table-revision-hidden"
    red_scope = next(
        item
        for item in hidden_payload["entity_scope"]
        if item["entity_id"] == "red_block"
    )
    red_scope["status"] = "excluded"
    hidden = WorldGoalGraph.from_mapping(hidden_payload)
    with pytest.raises(WorldIntentValidationError, match="cannot hide"):
        validate_world_goal_graph_revision(previous, hidden, ["red-in-bin"])

    complete_payload = clean_table_graph()
    complete_payload["graph_id"] = "clean-table-revision-complete"
    complete_payload["status"] = "complete"
    complete = WorldGoalGraph.from_mapping(complete_payload)
    with pytest.raises(WorldIntentValidationError, match="cannot claim complete"):
        validate_world_goal_graph_revision(previous, complete, ["red-in-bin"])


def test_live_runner_records_goal_graph_as_shadow_without_control_authority():
    source = (
        Path(__file__).parents[1]
        / "scripts"
        / "run_gemini_robotics_robolab.py"
    ).read_text()
    physical_evidence = source.index(
        'preflight_state["entity_physical_evidence"]'
    )
    inventory = source.index(
        "semantic_scene_inventory_from_state(", physical_evidence
    )
    model_call = source.index("build_world_goal_graph_prompt(", inventory)
    validation = source.index(
        "validate_world_goal_graph_entity_references(", model_call
    )
    feasibility = source.index(
        "_choose_observation_bound_task_feasibility(", validation
    )

    assert physical_evidence < inventory < model_call < validation < feasibility
    shadow_block = source[model_call:feasibility]
    assert 'episode_trace["world_goal_graph_shadow"]' in shadow_block
    assert '"motion_authority": False' in shadow_block
    assert '"authority_scope": []' in shadow_block
    assert "goal_graph.to_dict()" in shadow_block
