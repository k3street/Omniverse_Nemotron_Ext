from pathlib import Path

import pytest

from scripts.world_effect_provider_registry import (
    RuntimeToolCapability,
    default_world_effect_provider_registry,
)
from scripts.world_effect_session import (
    WORLD_EFFECT_SESSION_SCHEMA_VERSION,
    WorldEffectSessionError,
    WorldEffectSessionGate,
    build_world_effect_session_candidates,
    build_world_effect_session_prompt,
)
from scripts.world_goal_activation import (
    WORLD_GOAL_ACTIVATION_SCHEMA_VERSION,
    WorldGoalActivationGate,
    build_goal_activation_candidates,
    shadow_world_capability_registry,
)
from scripts.world_goal_graph_contract import (
    WORLD_GOAL_GRAPH_SCHEMA_VERSION,
    WorldGoalGraph,
)
from scripts.world_goal_graph_membership import SceneMembershipLease
from scripts.world_predicate_evaluator_registry import (
    rgbd_world_predicate_evaluator_registry,
)


def graph():
    return WorldGoalGraph.from_mapping(
        {
            "schema_version": WORLD_GOAL_GRAPH_SCHEMA_VERSION,
            "graph_id": "clean-table",
            "status": "ready",
            "root_goal_ids": ["red-in-bin"],
            "goals": [
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
                    "rationale": "Move the red block off the table.",
                }
            ],
            "entity_scope": [
                {
                    "entity_id": "observed_scene",
                    "status": "context",
                    "reason": "Inventory scope.",
                },
                {
                    "entity_id": "red_block",
                    "status": "included",
                    "reason": "Goal subject.",
                },
                {
                    "entity_id": "grey_bin",
                    "status": "included",
                    "reason": "Goal reference.",
                },
            ],
            "constraints": [],
            "required_observations": [],
            "confidence": 0.9,
            "reason": "One measurable outcome.",
        }
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
                "entity_id": "red_block",
                "label": "red block",
                "observation_status": "visible_rgbd",
                "geometry": {
                    "visible_aabb_min_base_m": [0.50, 0.20, 0.02],
                    "visible_aabb_max_base_m": [0.55, 0.25, 0.07],
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
        ],
        "role_bindings": [],
        "limitations": [],
    }


def tools(*, active=False, motion_id="factory.spatial", actuator_id="factory.attach"):
    activation = "active" if active else "factory_available"
    return [
        RuntimeToolCapability(
            tool_id="sensor.rgbd",
            tool_family="sensor",
            capability_tags=("scene.geometry.rgbd",),
            activation_status="active",
            source="test",
        ),
        RuntimeToolCapability(
            tool_id=motion_id,
            tool_family="motion",
            capability_tags=(
                "spatial.pose_target",
                "motion.observation_bound",
                "motion.invalidation_feedback",
            ),
            activation_status=activation,
            source="test",
        ),
        RuntimeToolCapability(
            tool_id=actuator_id,
            tool_family="actuator",
            capability_tags=(
                "entity_attachment.acquire",
                "entity_attachment.release",
                "actuation.observation_bound",
            ),
            activation_status=activation,
            source="test",
        ),
    ]


def session_candidates(*, active=False, motion_id="factory.spatial", actuator_id="factory.attach"):
    task_graph = graph()
    scene = inventory()
    lease = SceneMembershipLease.issue(task_graph, scene)
    provider_assessment = default_world_effect_provider_registry().assess(
        "world_relation.realize_inside",
        tools(active=active, motion_id=motion_id, actuator_id=actuator_id),
    )
    capability_registry = shadow_world_capability_registry(
        effect_provider_assessment=provider_assessment.to_dict()
    )
    activation_candidates = build_goal_activation_candidates(
        task_graph,
        lease,
        rgbd_world_predicate_evaluator_registry(),
        capability_registry,
        scene,
    )
    activation = WorldGoalActivationGate(
        "goal-activation:test",
        activation_candidates,
    ).dispatch(
        {
            "schema_version": WORLD_GOAL_ACTIVATION_SCHEMA_VERSION,
            "observation_id": "goal-activation:test",
            "decision": "select_goal",
            "goal_id": "red-in-bin",
            "capability_id": "world_relation.realize_inside",
            "confidence": 0.9,
            "reason": "The outcome is dependency-ready.",
        }
    )
    result = build_world_effect_session_candidates(
        task_graph,
        lease,
        activation_candidates,
        activation,
        provider_assessment,
    )
    return task_graph, lease, activation, result


def selection_payload(candidate_set, *, observation_id=None):
    candidate = candidate_set.candidates[0]
    return {
        "schema_version": WORLD_EFFECT_SESSION_SCHEMA_VERSION,
        "observation_id": observation_id or candidate_set.observation_id,
        "decision": "select_provider",
        "candidate_id": candidate.candidate_id,
        "provider_id": candidate.provider_id,
        "confidence": 0.88,
        "reason": "All semantic requirements have compatible runtime bindings.",
    }


def test_factory_binding_creates_shadow_candidate_without_instantiation():
    _, _, activation, candidate_set = session_candidates()
    candidate = candidate_set.candidates[0]

    assert candidate.activation_observation_id == activation.observation_id
    assert candidate.goal_id == "red-in-bin"
    assert candidate.world_capability_id == "world_relation.realize_inside"
    assert candidate.provider_id == "transport.reversible_attachment"
    assert candidate.inactive_requirement_ids == (
        "observation_bound_spatial_motion",
        "reversible_entity_attachment",
    )
    serialized = candidate.to_dict()
    assert not serialized["provider_instantiated"]
    assert not serialized["execution_ready"]
    assert not serialized["motion_authority"]
    assert not serialized["execution_authority"]


def test_runtime_tool_ids_are_discovered_without_changing_goal_contract():
    _, _, _, candidate_set = session_candidates(
        active=True,
        motion_id="whole_body.reach",
        actuator_id="vacuum.acquire_release",
    )
    candidate = candidate_set.candidates[0]
    bindings = {
        item["requirement_id"]: item["tool_id"]
        for item in candidate.requirement_bindings
    }

    assert candidate.tool_binding_active
    assert candidate.inactive_requirement_ids == ()
    assert bindings["observation_bound_spatial_motion"] == "whole_body.reach"
    assert bindings["reversible_entity_attachment"] == "vacuum.acquire_release"
    assert candidate.to_dict()["execution_ready"] is False


def test_session_gate_accepts_only_fresh_advertised_provider_pair():
    _, _, _, candidate_set = session_candidates()
    gate = WorldEffectSessionGate(candidate_set)

    accepted = gate.dispatch(selection_payload(candidate_set))
    assert accepted.provider_id == "transport.reversible_attachment"
    assert not accepted.to_dict()["provider_instantiated"]

    with pytest.raises(WorldEffectSessionError, match="stale"):
        gate.dispatch(selection_payload(candidate_set, observation_id="stale:test"))

    invented = selection_payload(candidate_set)
    invented["provider_id"] = "invented.provider"
    with pytest.raises(WorldEffectSessionError, match="not advertised"):
        gate.dispatch(invented)


def test_session_prompt_is_goal_bound_and_explicitly_non_authoritative():
    task_graph, lease, activation, candidate_set = session_candidates()
    prompt = build_world_effect_session_prompt(
        instruction="Clean the table",
        graph=task_graph,
        membership_lease=lease,
        activation_decision=activation,
        candidate_set=candidate_set,
    )
    lowered = prompt.lower()

    assert "red-in-bin" in prompt
    assert "red_block" in prompt
    assert "grey_bin" in prompt
    assert "transport.reversible_attachment" in prompt
    assert "does not\ninstantiate a provider" in lowered
    assert '"execution_authority": false' in lowered
    for forbidden in (
        "joint target",
        "inverse kinematics",
        "franka",
        "droid",
        "parallel gripper",
        "suction cup",
    ):
        assert forbidden not in lowered


def test_runner_wires_session_after_activation_and_before_shadow_boundary():
    source = (
        Path(__file__).parents[1]
        / "scripts"
        / "run_gemini_robotics_robolab.py"
    ).read_text()
    activation_gate = source.index("WorldGoalActivationGate(")
    session_candidates_call = source.index(
        "build_world_effect_session_candidates(", activation_gate
    )
    session_prompt = source.index(
        "build_world_effect_session_prompt(", session_candidates_call
    )
    session_gate = source.index("WorldEffectSessionGate(", session_prompt)
    session_trace = source.index(
        'episode_trace["world_effect_session_shadow"]', session_gate
    )
    hard_boundary = source.index("if args_cli.shadow_plan_only:", session_trace)

    assert (
        activation_gate
        < session_candidates_call
        < session_prompt
        < session_gate
        < session_trace
        < hard_boundary
    )
    block = source[session_candidates_call:hard_boundary]
    assert '"provider_instantiated": False' in block
    assert '"motion_authority": False' in block
    assert '"execution_authority": False' in block
    assert "_execute_adaptive_stage(" not in block
    assert "actuator_transition_handler(" not in block
