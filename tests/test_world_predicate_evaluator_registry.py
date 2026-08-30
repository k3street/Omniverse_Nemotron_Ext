from pathlib import Path

import pytest

from scripts.world_goal_graph_contract import (
    WORLD_GOAL_GRAPH_SCHEMA_VERSION,
    WorldGoalGraph,
    build_world_goal_graph_prompt,
)
from scripts.world_predicate_evaluator_registry import (
    WorldPredicateEvaluation,
    WorldPredicateEvaluatorError,
    WorldPredicateEvaluatorRegistry,
    WorldPredicateEvaluatorSpec,
    rgbd_world_predicate_evaluator_registry,
)
from scripts.world_intent_contract import WorldPredicate


def predicate(subject_id, attribute, operator, value, reference_id=None):
    payload = {
        "subject_id": subject_id,
        "attribute": attribute,
        "operator": operator,
        "value": value,
    }
    if reference_id is not None:
        payload["reference_id"] = reference_id
    return payload


def inventory(*, block_inside=False):
    block_min = [0.20, 0.20, 0.02] if block_inside else [0.60, 0.20, 0.02]
    block_max = [0.25, 0.25, 0.07] if block_inside else [0.65, 0.25, 0.07]
    return {
        "schema_version": "semantic-scene-inventory.v1",
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
                "geometry": {
                    "visible_aabb_min_base_m": [0.0, 0.0, -0.02],
                    "visible_aabb_max_base_m": [1.0, 1.0, 0.02],
                },
            },
            {
                "entity_id": "grey_bin",
                "label": "grey bin",
                "observation_status": "visible_rgbd",
                "geometry": {
                    "visible_aabb_min_base_m": [0.10, 0.10, 0.0],
                    "visible_aabb_max_base_m": [0.40, 0.40, 0.20],
                },
            },
            {
                "entity_id": "red_block",
                "label": "red block",
                "observation_status": "visible_rgbd",
                "geometry": {
                    "visible_aabb_min_base_m": block_min,
                    "visible_aabb_max_base_m": block_max,
                },
            },
        ],
        "role_bindings": [],
        "limitations": [],
    }


def clean_graph(*, include_unverifiable_root=True):
    goals = [
        {
            "goal_id": "red-in-bin",
            "desired_state": [
                predicate("red_block", "inside", "==", True, "grey_bin")
            ],
            "depends_on": [],
            "valid_while": [],
            "completion_policy": "all",
            "reobserve_after": "state_change",
            "rationale": "The red block is observably inside the bin.",
        }
    ]
    roots = ["red-in-bin"]
    if include_unverifiable_root:
        goals.append(
            {
                "goal_id": "table-clean",
                "desired_state": [
                    predicate("table", "clean", "==", True)
                ],
                "depends_on": ["red-in-bin"],
                "valid_while": [],
                "completion_policy": "all",
                "reobserve_after": "always",
                "rationale": "The table is observably clean.",
            }
        )
        roots = ["table-clean"]
    return WorldGoalGraph.from_mapping(
        {
            "schema_version": WORLD_GOAL_GRAPH_SCHEMA_VERSION,
            "graph_id": "clean-test",
            "status": "ready",
            "root_goal_ids": roots,
            "goals": goals,
            "entity_scope": [
                {
                    "entity_id": "observed_scene",
                    "status": "context",
                    "reason": "Inventory scope.",
                },
                {
                    "entity_id": "table",
                    "status": "included" if include_unverifiable_root else "context",
                    "reason": "Task surface.",
                },
                {
                    "entity_id": "grey_bin",
                    "status": "included",
                    "reason": "Goal reference.",
                },
                {
                    "entity_id": "red_block",
                    "status": "included",
                    "reason": "Goal subject.",
                },
            ],
            "constraints": [],
            "required_observations": [],
            "confidence": 0.9,
            "reason": "Test graph",
        }
    )


def test_rgbd_registry_advertises_only_measurable_containment_forms():
    advertisement = rgbd_world_predicate_evaluator_registry().advertisement()

    assert advertisement["completion_requires_advertised_evaluator"] is True
    assert [item["evaluator_id"] for item in advertisement["evaluators"]] == [
        "rgbd.visible_geometry_inside"
    ]
    assert advertisement["evaluators"][0]["authority"] == "completion"
    assert advertisement["evaluators"][0]["supported_predicate_forms"][0] == {
        "attribute": "inside",
        "operator": "==",
        "value": True,
        "reference_id": "required_inventory_entity_id",
    }


@pytest.mark.parametrize(
    "block_inside,expected_status,expected_satisfied",
    [(False, "unsatisfied", False), (True, "satisfied", True)],
)
def test_rgbd_containment_evaluator_returns_fresh_geometric_evidence(
    block_inside, expected_status, expected_satisfied
):
    relation = WorldPredicate.from_mapping(
        predicate("red_block", "inside", "==", True, "grey_bin"),
        "predicate",
    )
    result = rgbd_world_predicate_evaluator_registry().evaluate(
        relation, inventory(block_inside=block_inside)
    )

    assert result.status == expected_status
    assert result.satisfied is expected_satisfied
    assert result.evidence["axis_contained"] == (
        [True, True, True] if block_inside else [False, True, True]
    )


def test_missing_visible_geometry_is_unknown_not_false_completion():
    scene = inventory()
    red = next(item for item in scene["entities"] if item["entity_id"] == "red_block")
    red["geometry"] = {}
    relation = WorldPredicate.from_mapping(
        predicate("red_block", "inside", "==", True, "grey_bin"),
        "predicate",
    )

    result = rgbd_world_predicate_evaluator_registry().evaluate(relation, scene)

    assert result.status == "unknown"
    assert result.satisfied is None
    assert result.reason == "predicate_geometry_unavailable"


def test_temporarily_occluded_geometry_cannot_prove_a_predicate():
    scene = inventory(block_inside=True)
    red_block = next(
        item for item in scene["entities"] if item["entity_id"] == "red_block"
    )
    red_block["observation_status"] = "temporarily_occluded_rgbd"
    relation = WorldPredicate.from_mapping(
        predicate("red_block", "inside", "==", True, "grey_bin"),
        "predicate",
    )

    result = rgbd_world_predicate_evaluator_registry().evaluate(relation, scene)

    assert result.status == "unknown"
    assert result.reason == "predicate_geometry_not_fresh_visible"
    assert result.evidence["stale_geometry_accepted"] is False


def test_graph_admission_fails_closed_on_table_clean_but_admits_inside_relation():
    registry = rgbd_world_predicate_evaluator_registry()

    rejected = registry.assess_graph(clean_graph(), inventory())
    admitted = registry.assess_graph(
        clean_graph(include_unverifiable_root=False), inventory()
    )

    assert rejected.admitted is False
    assert rejected.predicate_count == 2
    assert rejected.admitted_predicate_count == 1
    assert rejected.unsupported_predicates == (
        {
            "path": "goals[1].desired_state[0]",
            "predicate": predicate("table", "clean", "==", True),
        },
    )
    assert admitted.admitted is True
    assert admitted.current_evaluations[0]["evaluation"]["status"] == "unsatisfied"


def test_graph_status_needs_observation_is_never_admitted_for_execution():
    payload = clean_graph(include_unverifiable_root=False).to_dict()
    payload["status"] = "needs_observation"
    payload["required_observations"] = [
        predicate("table", "eligible_movable_contents", "is_known", True)
    ]
    graph = WorldGoalGraph.from_mapping(payload)

    admission = rgbd_world_predicate_evaluator_registry().assess_graph(
        graph, inventory()
    )

    assert admission.admitted is False
    assert admission.resolved_subset_admitted is True
    assert admission.admitted_predicate_count == 1
    assert admission.required_observations == tuple(payload["required_observations"])


def test_registry_rejects_duplicate_plugins():
    registry = WorldPredicateEvaluatorRegistry()

    def supports(_predicate):
        return True

    def evaluates(_predicate, _inventory):
        return WorldPredicateEvaluation(
            evaluator_id="test.evaluator",
            status="unknown",
            reason="test_unknown",
            evidence={},
        )

    spec = WorldPredicateEvaluatorSpec(
        evaluator_id="test.evaluator",
        description="Test evaluator.",
        authority="advisory",
        evidence_source="test.source",
        supported_predicate_forms=({"attribute": "test"},),
        limitations=(),
        matcher=supports,
        evaluator=evaluates,
    )
    registry.register(spec)

    with pytest.raises(WorldPredicateEvaluatorError, match="already registered"):
        registry.register(spec)


def test_goal_prompt_requires_runtime_advertised_completion_predicates():
    prompt = build_world_goal_graph_prompt(
        "Clean the table",
        inventory(),
        rgbd_world_predicate_evaluator_registry().advertisement(),
    )

    assert "rgbd.visible_geometry_inside" in prompt
    assert "completion-authority evaluator" in prompt
    assert "Do not invent a predicate evaluator" in prompt
    assert "status to needs_observation" in prompt


def test_live_shadow_records_runtime_predicate_admission_without_motion_authority():
    source = (
        Path(__file__).parents[1]
        / "scripts"
        / "run_gemini_robotics_robolab.py"
    ).read_text()
    registry = source.index("rgbd_world_predicate_evaluator_registry()")
    planner = source.index("build_world_goal_graph_prompt(", registry)
    admission = source.index(
        "world_predicate_evaluator_registry.assess_graph(", planner
    )
    scheduler = source.index("def operation_scheduler_handler(", admission)

    shadow = source[planner:scheduler]
    assert registry < planner < admission < scheduler
    assert '"predicate_evaluator_admission"' in shadow
    assert '"motion_authority": False' in shadow
    assert "goal_graph_predicate_admission.admitted" in shadow
