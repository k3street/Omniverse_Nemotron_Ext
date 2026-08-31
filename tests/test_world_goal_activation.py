from copy import deepcopy
from pathlib import Path

import pytest

from scripts.world_goal_activation import (
    WORLD_GOAL_ACTIVATION_SCHEMA_VERSION,
    WorldGoalActivationError,
    WorldGoalActivationGate,
    build_goal_activation_candidates,
    build_world_goal_activation_prompt,
    shadow_world_capability_registry,
)
from scripts.world_entity_physical_evidence import build_entity_physical_evidence
from scripts.world_effect_provider_registry import (
    RuntimeToolCapability,
    default_world_effect_provider_registry,
)
from scripts.world_goal_graph_contract import (
    WORLD_GOAL_GRAPH_SCHEMA_VERSION,
    WorldGoalGraph,
)
from scripts.world_goal_graph_membership import SceneMembershipLease
from scripts.world_predicate_evaluator_registry import (
    rgbd_world_predicate_evaluator_registry,
)
from scripts.world_scene_inventory_memory import TemporalSceneInventoryMemory


def inventory(*, red_inside=False):
    red_min = [0.20, 0.20, 0.02] if red_inside else [0.50, 0.20, 0.02]
    red_max = [0.25, 0.25, 0.07] if red_inside else [0.55, 0.25, 0.07]
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
                    "visible_aabb_min_base_m": red_min,
                    "visible_aabb_max_base_m": red_max,
                },
            },
            {
                "entity_id": "green_block",
                "label": "green block",
                "observation_status": "visible_rgbd",
                "geometry": {
                    "visible_aabb_min_base_m": [0.60, 0.20, 0.02],
                    "visible_aabb_max_base_m": [0.65, 0.25, 0.07],
                },
            },
        ],
        "role_bindings": [],
        "limitations": [],
    }


def relation(subject_id):
    return {
        "subject_id": subject_id,
        "attribute": "inside",
        "operator": "==",
        "value": True,
        "reference_id": "grey_bin",
    }


def graph(*, green_depends_on_red=False):
    return WorldGoalGraph.from_mapping(
        {
            "schema_version": WORLD_GOAL_GRAPH_SCHEMA_VERSION,
            "graph_id": "clean-table",
            "status": "ready",
            "root_goal_ids": ["red-in-bin", "green-in-bin"],
            "goals": [
                {
                    "goal_id": "red-in-bin",
                    "desired_state": [relation("red_block")],
                    "depends_on": [],
                    "valid_while": [],
                    "completion_policy": "all",
                    "reobserve_after": "state_change",
                    "rationale": "Red block outcome.",
                },
                {
                    "goal_id": "green-in-bin",
                    "desired_state": [relation("green_block")],
                    "depends_on": ["red-in-bin"] if green_depends_on_red else [],
                    "valid_while": [],
                    "completion_policy": "all",
                    "reobserve_after": "state_change",
                    "rationale": "Green block outcome.",
                },
            ],
            "entity_scope": [
                {
                    "entity_id": "observed_scene",
                    "status": "context",
                    "reason": "Inventory scope.",
                },
                {
                    "entity_id": "table",
                    "status": "context",
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
                {
                    "entity_id": "green_block",
                    "status": "included",
                    "reason": "Goal subject.",
                },
            ],
            "constraints": [],
            "required_observations": [],
            "confidence": 0.9,
            "reason": "Two measurable outcomes.",
        }
    )


def candidates(scene=None, task_graph=None, capability_registry=None):
    scene = scene or inventory()
    task_graph = task_graph or graph()
    return build_goal_activation_candidates(
        task_graph,
        SceneMembershipLease.issue(task_graph, scene),
        rgbd_world_predicate_evaluator_registry(),
        capability_registry or shadow_world_capability_registry(),
        scene,
    )


def test_capability_registry_matches_world_effect_without_execution_authority():
    result = candidates()

    assert [item.goal_id for item in result.candidates] == [
        "red-in-bin",
        "green-in-bin",
    ]
    assessment = result.candidates[0].capability_assessments[0]
    assert assessment.capability_id == "world_relation.realize_inside"
    assert assessment.planning_ready
    assert not assessment.execution_ready
    assert assessment.missing_evidence == (
        "subject_mobility",
        "subject_mass",
        "destination_interior_clearance",
        "runtime_effect_provider_binding",
    )
    capacity = assessment.evidence["destination_capacity_estimates"][0]
    assert capacity["available"]
    assert capacity["subject_fits_observed_envelope"]
    assert capacity["authority"] == "planning_only_upper_bound"


def _with_physical_evidence(scene, entity_id, *, mobility, mass_kg=0.1):
    enriched = deepcopy(scene)
    entity = next(
        item for item in enriched["entities"] if item["entity_id"] == entity_id
    )
    rigid_bodies = (
        [
            {
                "prim_path": f"/World/envs/env_0/scene/{entity_id}",
                "enabled": True,
                "kinematic": mobility == "kinematic",
            }
        ]
        if mobility in {"dynamic", "kinematic"}
        else []
    )
    entity["physical_evidence"] = build_entity_physical_evidence(
        entity_id=entity_id,
        prim_path=f"/World/envs/env_0/scene/{entity_id}",
        rigid_body_records=rigid_bodies,
        prim_observed=True,
        mass_kg=mass_kg,
        mass_source="test_live_mass",
    )
    return enriched


def test_dynamic_subject_mass_resolves_physical_planning_evidence():
    scene = _with_physical_evidence(
        inventory(), "red_block", mobility="dynamic", mass_kg=0.12
    )
    result = candidates(scene=scene)
    assessment = next(
        item for item in result.candidates if item.goal_id == "red-in-bin"
    ).capability_assessments[0]

    assert assessment.planning_ready
    assert assessment.missing_evidence == (
        "destination_interior_clearance",
        "runtime_effect_provider_binding",
    )
    assert assessment.evidence["subject_physical_evidence"]["red_block"] == {
        "mobility_status": "dynamic",
        "mobility_available": True,
        "mass_available": True,
        "mass_kg": 0.12,
        "source": "active_simulator_physics",
    }


def test_compatible_provider_factories_resolve_binding_but_not_activation():
    scene = _with_physical_evidence(
        inventory(), "red_block", mobility="dynamic", mass_kg=0.12
    )
    tools = [
        RuntimeToolCapability(
            tool_id="sensor.geometry",
            tool_family="sensor",
            capability_tags=("scene.geometry.rgbd",),
            activation_status="active",
            source="test",
        ),
        RuntimeToolCapability(
            tool_id="factory.motion",
            tool_family="motion",
            capability_tags=(
                "spatial.pose_target",
                "motion.observation_bound",
                "motion.invalidation_feedback",
            ),
            activation_status="factory_available",
            source="test",
        ),
        RuntimeToolCapability(
            tool_id="factory.attachment",
            tool_family="actuator",
            capability_tags=(
                "entity_attachment.acquire",
                "entity_attachment.release",
                "actuation.observation_bound",
            ),
            activation_status="factory_available",
            source="test",
        ),
    ]
    provider_assessment = default_world_effect_provider_registry().assess(
        "world_relation.realize_inside", tools
    )
    result = candidates(
        scene=scene,
        capability_registry=shadow_world_capability_registry(
            effect_provider_assessment=provider_assessment.to_dict()
        ),
    )
    assessment = next(
        item for item in result.candidates if item.goal_id == "red-in-bin"
    ).capability_assessments[0]

    assert assessment.missing_evidence == (
        "destination_interior_clearance",
        "runtime_effect_provider_activation",
    )
    provider = assessment.evidence["runtime_effect_provider_assessment"]
    assert provider["binding_ready"]
    assert not provider["active_binding_ready"]
    assert not assessment.execution_ready


def test_proven_fixed_subject_is_not_an_activation_candidate():
    scene = _with_physical_evidence(
        inventory(), "red_block", mobility="fixed", mass_kg=None
    )
    result = candidates(scene=scene)

    assert [item.goal_id for item in result.candidates] == ["green-in-bin"]
    assert result.evidence_blocked_goal_ids == ("red-in-bin",)
    blocker = result.evidence_blockers[0]
    assert blocker.goal_id == "red-in-bin"
    assert blocker.reason_codes == ("no_planning_ready_capability",)
    assessment = blocker.capability_assessments[0]
    assert assessment.evidence["planning_blockers"] == [
        "red_block.mobility_status=fixed"
    ]
    assert assessment.evidence["subject_physical_evidence"]["red_block"][
        "mobility_status"
    ] == "fixed"


def test_visible_non_fit_is_not_an_activation_candidate():
    scene = inventory()
    red = next(item for item in scene["entities"] if item["entity_id"] == "red_block")
    red["geometry"] = {
        "visible_aabb_min_base_m": [0.0, 0.0, 0.0],
        "visible_aabb_max_base_m": [0.50, 0.50, 0.50],
    }
    result = candidates(scene=scene)

    assert [item.goal_id for item in result.candidates] == ["green-in-bin"]
    assert result.evidence_blocked_goal_ids == ("red-in-bin",)
    serialized = result.evidence_blockers[0].to_dict()
    assessment = serialized["capability_assessments"][0]
    assert assessment["evidence"]["planning_blockers"] == [
        "red_block.does_not_fit_observed_envelope_of.grey_bin"
    ]
    capacity = assessment["evidence"]["destination_capacity_estimates"][0]
    assert capacity["subject_id"] == "red_block"
    assert capacity["reference_id"] == "grey_bin"
    assert capacity["subject_fits_observed_envelope"] is False
    assert "runtime_effect_provider_assessment" in assessment["evidence"]
    assert serialized["desired_state_evaluations"][0]["predicate"] == relation(
        "red_block"
    )
    assert serialized["execution_authority"] is False


def test_satisfied_and_dependency_blocked_goals_are_not_activation_candidates():
    satisfied = candidates(scene=inventory(red_inside=True))
    blocked = candidates(task_graph=graph(green_depends_on_red=True))

    assert satisfied.satisfied_goal_ids == ("red-in-bin",)
    assert [item.goal_id for item in satisfied.candidates] == ["green-in-bin"]
    assert [item.goal_id for item in blocked.candidates] == ["red-in-bin"]
    assert blocked.dependency_blocked_goal_ids == ("green-in-bin",)


def test_satisfied_goal_remains_selectable_until_its_subject_is_released():
    scene = inventory(red_inside=True)
    scene["world_effect_continuation_evidence"] = {
        "selected_goal_id": "red-in-bin",
        "attachment_entity_ids": ["red_block"],
        "gripper_engaged": True,
        "task_completion_allowed": False,
    }

    result = candidates(scene=scene)

    assert result.completion_blocked_goal_ids == ("red-in-bin",)
    assert "red-in-bin" not in result.satisfied_goal_ids
    assert "red-in-bin" in {item.goal_id for item in result.candidates}


def test_retained_attachment_identity_keeps_occluded_goal_planning_ready():
    baseline = inventory()
    changed = deepcopy(baseline)
    changed["entities"] = [
        item for item in changed["entities"] if item["entity_id"] != "red_block"
    ]
    scene = TemporalSceneInventoryMemory(
        baseline,
        maximum_missed_observations=0,
    ).update(
        changed,
        independently_present_entity_ids=("red_block",),
    ).inventory
    scene = dict(scene)
    scene["world_effect_continuation_evidence"] = {
        "schema_version": "world-effect-continuation-evidence.v1",
        "selected_goal_id": "red-in-bin",
        "source_operation_index": 4,
        "attachment_entity_ids": ["red_block"],
        "tracked_present_entity_ids": ["red_block"],
        "tracked_entity_positions_m": {"red_block": [0.5, 0.2, 0.08]},
        "temporarily_occluded_entity_ids": ["red_block"],
        "gripper_engaged": True,
        "retained_contact_supported": True,
        "recovery_actuator_only": False,
        "planning_continuation_allowed": True,
        "reason": "fresh_contact_and_tracking_support_continuation",
        "completion_evidence": False,
        "task_completion_allowed": False,
        "dispatch_enabled": False,
        "motion_authority": False,
        "execution_authority": False,
        "authority_scope": [],
    }
    task_graph = graph(green_depends_on_red=True)
    result = build_goal_activation_candidates(
        task_graph,
        SceneMembershipLease.issue(task_graph, baseline),
        rgbd_world_predicate_evaluator_registry(),
        shadow_world_capability_registry(),
        scene,
    )

    assert [item.goal_id for item in result.candidates] == ["red-in-bin"]
    assessment = result.candidates[0].capability_assessments[0]
    assert assessment.planning_ready
    assert assessment.evidence["retained_attachment_subject_ids"] == [
        "red_block"
    ]
    assert assessment.evidence["related_entity_visibility"]["red_block"] is False
    assert assessment.evidence["related_entity_planning_ready"]["red_block"]


def test_spoofed_retained_attachment_authority_is_not_planning_evidence():
    scene = inventory()
    red = next(item for item in scene["entities"] if item["entity_id"] == "red_block")
    red["observation_status"] = "temporarily_occluded_rgbd"
    red["geometry"] = {}
    red["temporal_presence_evidence"] = {
        "independently_present": True,
        "cached_geometry_exposed": False,
        "completion_evidence": False,
        "execution_authority": False,
    }
    scene["world_effect_continuation_evidence"] = {
        "schema_version": "world-effect-continuation-evidence.v1",
        "selected_goal_id": "red-in-bin",
        "attachment_entity_ids": ["red_block"],
        "tracked_present_entity_ids": ["red_block"],
        "gripper_engaged": True,
        "retained_contact_supported": True,
        "recovery_actuator_only": False,
        "planning_continuation_allowed": True,
        "completion_evidence": False,
        "dispatch_enabled": False,
        "motion_authority": False,
        "execution_authority": True,
    }

    result = candidates(scene=scene, task_graph=graph(green_depends_on_red=True))

    assert not result.candidates
    assert result.evidence_blocked_goal_ids == ("red-in-bin",)


def test_membership_change_prevents_candidate_generation():
    task_graph = graph()
    baseline = inventory()
    lease = SceneMembershipLease.issue(task_graph, baseline)
    changed = deepcopy(baseline)
    changed["entities"].append(
        {
            "entity_id": "blue_block",
            "label": "blue block",
            "observation_status": "visible_rgbd",
            "geometry": {},
        }
    )

    with pytest.raises(WorldGoalActivationError, match="scene membership lease"):
        build_goal_activation_candidates(
            task_graph,
            lease,
            rgbd_world_predicate_evaluator_registry(),
            shadow_world_capability_registry(),
            changed,
        )


def selection_payload(observation_id="activation-test"):
    return {
        "schema_version": WORLD_GOAL_ACTIVATION_SCHEMA_VERSION,
        "observation_id": observation_id,
        "decision": "select_goal",
        "goal_id": "red-in-bin",
        "capability_id": "world_relation.realize_inside",
        "confidence": 0.8,
        "reason": "The red block is visible and appears unobstructed.",
    }


def test_activation_gate_accepts_only_fresh_advertised_goal_capability_pairs():
    gate = WorldGoalActivationGate("activation-test", candidates())
    accepted = gate.dispatch(selection_payload())

    assert accepted.goal_id == "red-in-bin"
    assert accepted.capability_id == "world_relation.realize_inside"
    assert accepted.to_dict()["execution_authority"] is False

    stale = selection_payload("old-observation")
    with pytest.raises(WorldGoalActivationError, match="stale"):
        gate.dispatch(stale)

    invented = selection_payload()
    invented["capability_id"] = "invented.capability"
    with pytest.raises(WorldGoalActivationError, match="not advertised"):
        gate.dispatch(invented)


def test_activation_gate_rejects_false_completion_while_goals_remain():
    gate = WorldGoalActivationGate("activation-test", candidates())
    payload = selection_payload()
    payload.update(
        decision="complete",
        goal_id=None,
        capability_id=None,
    )

    with pytest.raises(WorldGoalActivationError, match="unresolved goals"):
        gate.dispatch(payload)


def test_activation_prompt_exposes_candidates_and_missing_execution_evidence():
    task_graph = graph()
    scene = inventory()
    lease = SceneMembershipLease.issue(task_graph, scene)
    candidate_set = candidates(scene=scene, task_graph=task_graph)
    prompt = build_world_goal_activation_prompt(
        instruction="Clean the table",
        observation_id="activation-test",
        graph=task_graph,
        membership_lease=lease,
        inventory=scene,
        capability_advertisement=shadow_world_capability_registry().advertisement(),
        candidate_set=candidate_set,
    )

    assert "red-in-bin" in prompt
    assert "green-in-bin" in prompt
    assert "world_relation.realize_inside" in prompt
    assert "runtime_effect_provider_binding" in prompt
    assert "does not dispatch" in prompt
    assert '"execution_authority": false' in prompt
    for forbidden in ("joint target", "inverse kinematics", "franka", "droid"):
        assert forbidden not in prompt.lower()


def test_live_runner_records_shadow_selection_without_dispatch_authority():
    source = (
        Path(__file__).parents[1]
        / "scripts"
        / "run_gemini_robotics_robolab.py"
    ).read_text()
    graph_admission = source.index("goal_graph_shadow_admitted = bool(")
    candidates = source.index("build_goal_activation_candidates(", graph_admission)
    model_prompt = source.index("build_world_goal_activation_prompt(", candidates)
    gate = source.index("WorldGoalActivationGate(", model_prompt)
    trace = source.index('episode_trace["world_goal_activation_shadow"]', gate)
    feasibility = source.index(
        "_choose_observation_bound_task_feasibility(", trace
    )

    shadow = source[graph_admission:feasibility]
    assert graph_admission < candidates < model_prompt < gate < trace < feasibility
    assert '"motion_authority": False' in shadow
    assert '"execution_authority": False' in shadow
    assert '"authority_scope": []' in shadow
    assert "_execute_adaptive_stage(" not in shadow
    assert "actuator_transition_handler(" not in shadow


def test_shadow_plan_only_is_a_hard_boundary_before_legacy_runtime():
    root = Path(__file__).parents[1]
    source = (root / "scripts" / "run_gemini_robotics_robolab.py").read_text()
    launcher = (root / "launch_gemini_robotics_robolab.sh").read_text()

    argument = source.index('"--shadow-plan-only"')
    mode_guard = source.index("world_effect_only_mode = bool(", argument)
    demo_guard = source.index("if not world_effect_only_mode:", mode_guard)
    demo_load = source.index('with h5py.File(demo_path, "r")', demo_guard)
    provider_guard = source.index(
        "if not args_cli.shadow_plan_only or args_cli.guarded_world_effect_execution:",
        demo_load,
    )
    provider = source.index(
        "motion_tool_provider = GeminiProvider", provider_guard
    )
    activation_trace = source.index(
        'episode_trace["world_goal_activation_shadow"] = {', provider
    )
    hard_boundary = source.index(
        "if args_cli.shadow_plan_only:", activation_trace
    )
    boundary_return = source.index(
        "return 0 if shadow_complete else 2", hard_boundary
    )
    feasibility = source.index(
        "_choose_observation_bound_task_feasibility(", boundary_return
    )

    assert argument < mode_guard < demo_guard < demo_load
    assert provider_guard < provider < activation_trace
    assert activation_trace < hard_boundary < boundary_return < feasibility
    boundary = source[hard_boundary:feasibility]
    assert '"feasibility_called": False' in boundary
    assert '"demonstration_loaded": False' in boundary
    assert '"execution_provider_created": False' in boundary
    assert '"motion_stage_count": len(episode_trace["stages"])' in boundary
    assert '"motion_authority": False' in boundary
    assert '"execution_authority": False' in boundary
    assert "--shadow-plan-only" in launcher
    assert "--guarded-world-effect-execution" in launcher
    assert 'shadow_plan_only=1' in launcher
    assert '[[ "$shadow_plan_only" == 0' in launcher


def test_live_runner_discovers_effect_provider_before_goal_activation():
    source = (
        Path(__file__).parents[1]
        / "scripts"
        / "run_gemini_robotics_robolab.py"
    ).read_text()
    provider_registry = source.index(
        "default_world_effect_provider_registry()"
    )
    provider_assessment = source.index(
        "world_effect_provider_registry.assess(", provider_registry
    )
    capability_registry = source.index(
        "shadow_world_capability_registry(", provider_assessment
    )
    candidates_call = source.index(
        "build_goal_activation_candidates(", capability_registry
    )

    assert (
        provider_registry
        < provider_assessment
        < capability_registry
        < candidates_call
    )
    block = source[provider_registry:candidates_call]
    assert '"world_relation.realize_inside"' in block
    assert "inside_effect_provider_assessment.to_dict()" in block


def test_live_runner_revises_only_blocked_graph_before_final_activation():
    source = (
        Path(__file__).parents[1]
        / "scripts"
        / "run_gemini_robotics_robolab.py"
    ).read_text()
    initial_candidates = source.index("goal_activation_candidates = (")
    empty_candidate_trigger = source.index(
        "or not goal_activation_candidates.evidence_blocked_goal_ids",
        initial_candidates,
    )
    revision_prompt = source.index(
        "revision_context=revision_context", empty_candidate_trigger
    )
    continuity_validation = source.index(
        "validate_world_goal_graph_revision(", revision_prompt
    )
    replacement_candidates = source.index(
        "revised_activation_candidates = (", continuity_validation
    )
    graph_digest = source.index(
        "goal_graph_digest = hashlib.sha256(", replacement_candidates
    )
    final_activation = source.index(
        "build_world_goal_activation_prompt(", graph_digest
    )

    assert (
        initial_candidates
        < empty_candidate_trigger
        < revision_prompt
        < continuity_validation
        < replacement_candidates
        < graph_digest
        < final_activation
    )
    revision_block = source[empty_candidate_trigger:final_activation]
    assert '"complete_replacement_graph": True' in revision_block
    assert '"preserve_scene_membership": True' in revision_block
    assert '"preserve_unresolved_blocked_outcomes": True' in revision_block
    assert '"execution_authority": False' in revision_block
    assert "_execute_adaptive_stage(" not in revision_block
    assert "actuator_transition_handler(" not in revision_block
