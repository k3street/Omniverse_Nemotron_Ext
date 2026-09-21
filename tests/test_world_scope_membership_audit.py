from copy import deepcopy
from pathlib import Path

import pytest

from scripts.world_goal_graph_contract import (
    WORLD_GOAL_GRAPH_SCHEMA_VERSION,
    WorldGoalGraph,
)
from scripts.world_scope_membership_audit import (
    WORLD_SCOPE_MEMBERSHIP_AUDIT_SCHEMA_VERSION,
    WorldScopeMembershipAuditError,
    WorldScopeMembershipAuditGate,
    assess_world_goal_graph_membership_audit,
    build_world_scope_membership_audit_prompt,
    world_scope_membership_observation_id,
)


def inventory():
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
                "geometry": {"visible_extent_base_m": [1.0, 0.8, 0.05]},
            },
            {
                "entity_id": "grey_bin",
                "label": "grey bin",
                "observation_status": "visible_rgbd",
                "geometry": {"visible_extent_base_m": [0.3, 0.3, 0.2]},
            },
            {
                "entity_id": "red_block",
                "label": "red block",
                "observation_status": "visible_rgbd",
                "geometry": {"visible_extent_base_m": [0.05, 0.05, 0.05]},
                "physical_evidence": {
                    "mobility": {"available": True, "status": "dynamic"},
                    "mass": {"available": True, "mass_kg": 0.02},
                },
            },
            {
                "entity_id": "large_object",
                "label": "large object",
                "observation_status": "visible_rgbd",
                "geometry": {"visible_extent_base_m": [0.4, 0.4, 0.4]},
                "physical_evidence": {
                    "mobility": {"available": True, "status": "dynamic"},
                    "mass": {"available": True, "mass_kg": 1.5},
                },
            },
        ],
        "role_bindings": [],
        "limitations": [],
    }


def graph(*, large_status="context"):
    goals = [
        {
            "goal_id": "red-in-bin",
            "desired_state": [
                {
                    "subject_id": "red_block",
                    "attribute": "inside",
                    "operator": "==",
                    "value": True,
                    "reference_id": "grey_bin",
                }
            ],
            "depends_on": [],
            "valid_while": [],
            "completion_policy": "all",
            "reobserve_after": "state_change",
            "rationale": "Observable red block outcome.",
        }
    ]
    roots = ["red-in-bin"]
    if large_status == "included":
        goals.append(
            {
                "goal_id": "large-in-bin",
                "desired_state": [
                    {
                        "subject_id": "large_object",
                        "attribute": "inside",
                        "operator": "==",
                        "value": True,
                        "reference_id": "grey_bin",
                    }
                ],
                "depends_on": [],
                "valid_while": [],
                "completion_policy": "all",
                "reobserve_after": "state_change",
                "rationale": "Observable large object outcome.",
            }
        )
        roots.append("large-in-bin")
    return WorldGoalGraph.from_mapping(
        {
            "schema_version": WORLD_GOAL_GRAPH_SCHEMA_VERSION,
            "graph_id": f"collective-{large_status}",
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
                    "status": "context",
                    "reason": "Referenced surface.",
                },
                {
                    "entity_id": "grey_bin",
                    "status": "context",
                    "reason": "Destination.",
                },
                {
                    "entity_id": "red_block",
                    "status": "included",
                    "reason": "Covered object.",
                },
                {
                    "entity_id": "large_object",
                    "status": large_status,
                    "reason": "Proposed scope decision.",
                },
            ],
            "constraints": [],
            "required_observations": [],
            "confidence": 0.9,
            "reason": "Collective observed outcome.",
        }
    )


def audit_payload(observation_id):
    return {
        "schema_version": WORLD_SCOPE_MEMBERSHIP_AUDIT_SCHEMA_VERSION,
        "observation_id": observation_id,
        "instruction_scope": "collective",
        "feasibility_independent": True,
        "decisions": [
            {
                "entity_id": "observed_scene",
                "status": "context",
                "reason": "Observation scope.",
            },
            {
                "entity_id": "table",
                "status": "context",
                "reason": "The referenced surface.",
            },
            {
                "entity_id": "grey_bin",
                "status": "context",
                "reason": "The destination.",
            },
            {
                "entity_id": "red_block",
                "status": "included",
                "reason": "Covered by the collective location outcome.",
            },
            {
                "entity_id": "large_object",
                "status": "included",
                "reason": "Covered regardless of expected difficulty.",
            },
        ],
        "confidence": 0.94,
        "reason": "All observed objects on the referenced surface are covered.",
    }


def audit_for(task_graph):
    scene = inventory()
    observation_id = world_scope_membership_observation_id(
        "Restore all objects from the observed surface to the destination",
        scene,
        task_graph,
    )
    audit = WorldScopeMembershipAuditGate(observation_id, scene).dispatch(
        audit_payload(observation_id)
    )
    return observation_id, audit


def test_audit_requires_exact_inventory_coverage_and_fresh_observation():
    task_graph = graph()
    observation_id, _ = audit_for(task_graph)
    gate = WorldScopeMembershipAuditGate(observation_id, inventory())

    stale = audit_payload("scope-membership:stale")
    with pytest.raises(WorldScopeMembershipAuditError, match="stale"):
        gate.dispatch(stale)

    incomplete = audit_payload(observation_id)
    incomplete["decisions"].pop()
    with pytest.raises(WorldScopeMembershipAuditError, match="exact inventory"):
        gate.dispatch(incomplete)


def test_audit_rejects_feasibility_as_task_membership_authority():
    task_graph = graph()
    observation_id, _ = audit_for(task_graph)
    payload = audit_payload(observation_id)
    payload["feasibility_independent"] = False

    with pytest.raises(WorldScopeMembershipAuditError, match="independent"):
        WorldScopeMembershipAuditGate(observation_id, inventory()).dispatch(payload)


def test_contextualizing_covered_large_object_fails_scope_admission():
    task_graph = graph(large_status="context")
    _, audit = audit_for(task_graph)
    assessment = assess_world_goal_graph_membership_audit(task_graph, audit)

    assert not assessment.admitted
    assert assessment.mismatches == (
        {
            "entity_id": "large_object",
            "graph_status": "context",
            "audited_status": "included",
            "audit_reason": "Covered regardless of expected difficulty.",
        },
    )
    assert not assessment.to_dict()["execution_authority"]


def test_corrected_graph_matches_same_fresh_membership_audit():
    initial = graph(large_status="context")
    _, audit = audit_for(initial)
    corrected = graph(large_status="included")

    assert assess_world_goal_graph_membership_audit(corrected, audit).admitted


def test_collective_known_subset_can_proceed_while_unknowns_remain_deferred():
    payload = graph(large_status="unknown").to_dict()
    payload["status"] = "needs_observation"
    payload["required_observations"] = [
        {
            "subject_id": "large_object",
            "attribute": "covered_by_instruction",
            "operator": "==",
            "value": True,
        }
    ]
    task_graph = WorldGoalGraph.from_mapping(payload)
    observation_id = world_scope_membership_observation_id(
        "Restore the covered objects", inventory(), task_graph
    )
    audit_data = audit_payload(observation_id)
    next(
        item
        for item in audit_data["decisions"]
        if item["entity_id"] == "large_object"
    ).update(status="unknown", reason="Visual evidence cannot decide membership.")
    audit = WorldScopeMembershipAuditGate(
        observation_id, inventory()
    ).dispatch(audit_data)

    assessment = assess_world_goal_graph_membership_audit(task_graph, audit)

    assert not assessment.admitted
    assert assessment.resolved_subset_admitted
    assert not assessment.scope_resolution_complete
    assert assessment.unknown_entity_ids == ("large_object",)
    assert not assessment.to_dict()["task_completion_allowed"]


def test_prompt_separates_membership_from_physical_difficulty():
    task_graph = graph()
    scene = inventory()
    observation_id = world_scope_membership_observation_id(
        "Restore all objects from the observed surface to the destination",
        scene,
        task_graph,
    )
    prompt = build_world_scope_membership_audit_prompt(
        instruction="Restore all objects from the observed surface to the destination",
        observation_id=observation_id,
        inventory=scene,
        graph=task_graph,
    )
    lowered = prompt.lower()

    assert "every observed entity" in lowered
    assert "not\ntask-membership evidence" in lowered
    assert "must not\nturn a covered entity into context" in lowered
    assert "large_object" in prompt
    assert '"feasibility_independent": {' in prompt
    for forbidden in (
        "joint target",
        "inverse kinematics",
        "franka",
        "droid",
        "parallel gripper",
        "suction cup",
    ):
        assert forbidden not in lowered


def test_runner_audits_and_corrects_scope_before_graph_admission():
    source = (
        Path(__file__).parents[1]
        / "scripts"
        / "run_gemini_robotics_robolab.py"
    ).read_text()
    graph_parse = source.index("goal_graph = WorldGoalGraph.from_mapping(")
    audit_prompt = source.index(
        "build_world_scope_membership_audit_prompt(", graph_parse
    )
    audit_gate = source.index("WorldScopeMembershipAuditGate(", audit_prompt)
    audit_assessment = source.index(
        "assess_world_goal_graph_membership_audit(", audit_gate
    )
    scope_revision = source.index(
        '"task_membership_audit_conflict"', audit_assessment
    )
    predicate_admission = source.index(
        "world_predicate_evaluator_registry.assess_graph(", scope_revision
    )
    activation = source.index("build_goal_activation_candidates(", predicate_admission)

    assert (
        graph_parse
        < audit_prompt
        < audit_gate
        < audit_assessment
        < scope_revision
        < predicate_admission
        < activation
    )
    block = source[audit_prompt:predicate_admission]
    assert '"execution_authority": False' in block
    assert "_execute_adaptive_stage(" not in block
    assert "actuator_transition_handler(" not in block
