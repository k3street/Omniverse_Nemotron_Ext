from copy import deepcopy
from pathlib import Path

import pytest

from scripts.world_goal_graph_contract import (
    WORLD_GOAL_GRAPH_SCHEMA_VERSION,
    WorldGoalGraph,
)
from scripts.world_goal_graph_membership import (
    SceneMembershipLease,
    SceneMembershipLeaseError,
    assess_world_goal_graph_scene_scope,
    scene_membership_fingerprint,
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
                "geometry": {"center_base_m": [0.4, 0.1, 0.0]},
            },
            {
                "entity_id": "grey_bin",
                "label": "grey bin",
                "observation_status": "visible_rgbd",
                "geometry": {"center_base_m": [0.4, -0.2, 0.1]},
            },
            {
                "entity_id": "red_block",
                "label": "red block",
                "observation_status": "visible_rgbd",
                "geometry": {"center_base_m": [0.5, 0.2, 0.05]},
            },
            {
                "entity_id": "birdhouse",
                "label": "birdhouse",
                "observation_status": "visible_rgbd",
                "geometry": {"center_base_m": [0.7, 0.3, 0.12]},
            },
        ],
        "role_bindings": [
            {"role_id": "movable_object", "entity_id": "red_block"},
            {"role_id": "target_receptacle", "entity_id": "grey_bin"},
        ],
        "limitations": [],
    }


def graph_payload(*, status="ready"):
    return {
        "schema_version": WORLD_GOAL_GRAPH_SCHEMA_VERSION,
        "graph_id": "clean-table-shadow",
        "status": status,
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
                "rationale": "The red block is inside the bin.",
            }
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
                "reason": "Task surface that should remain.",
            },
            {
                "entity_id": "grey_bin",
                "status": "included",
                "reason": "Goal relation reference.",
            },
            {
                "entity_id": "red_block",
                "status": "included",
                "reason": "Goal relation subject.",
            },
            {
                "entity_id": "birdhouse",
                "status": "excluded",
                "reason": "Observed contextual fixture, not clutter for this graph.",
            },
        ],
        "constraints": [],
        "required_observations": [],
        "confidence": 0.9,
        "reason": "Every observed entity has an explicit task-scope decision.",
    }


def graph(*, status="ready"):
    return WorldGoalGraph.from_mapping(graph_payload(status=status))


def test_complete_scene_scope_is_admitted_and_exclusions_are_explicit():
    admission = assess_world_goal_graph_scene_scope(graph(), inventory())

    assert admission.admitted
    assert admission.included_entity_ids == ("grey_bin", "red_block")
    assert admission.context_entity_ids == ("observed_scene", "table")
    assert admission.excluded_entity_ids == ("birdhouse",)
    assert admission.unknown_entity_ids == ()
    assert admission.missing_scope_entity_ids == ()
    assert admission.extra_scope_entity_ids == ()


@pytest.mark.parametrize(
    "mutate,field,expected",
    [
        (
            lambda payload: payload["entity_scope"].pop(),
            "missing_scope_entity_ids",
            ("birdhouse",),
        ),
        (
            lambda payload: payload["entity_scope"].append(
                {
                    "entity_id": "invented_entity",
                    "status": "excluded",
                    "reason": "Not observed.",
                }
            ),
            "extra_scope_entity_ids",
            ("invented_entity",),
        ),
        (
            lambda payload: payload["entity_scope"][4].update(status="unknown"),
            "unknown_entity_ids",
            ("birdhouse",),
        ),
        (
            lambda payload: payload["entity_scope"][3].update(status="excluded"),
            "predicate_scope_conflicts",
            ("red_block",),
        ),
        (
            lambda payload: payload["entity_scope"][4].update(status="included"),
            "included_without_graph_relation",
            ("birdhouse",),
        ),
    ],
)
def test_missing_unknown_extra_and_contradictory_scope_fail_closed(
    mutate, field, expected
):
    payload = graph_payload()
    mutate(payload)
    admission = assess_world_goal_graph_scene_scope(
        WorldGoalGraph.from_mapping(payload), inventory()
    )

    assert not admission.admitted
    assert getattr(admission, field) == expected


def test_membership_fingerprint_ignores_pose_but_tracks_visibility_membership():
    baseline = inventory()
    moved = deepcopy(baseline)
    moved["entities"][3]["geometry"]["center_base_m"] = [0.2, -0.1, 0.08]
    hidden = deepcopy(baseline)
    hidden["entities"][3]["observation_status"] = "role_bound_not_visible"

    assert scene_membership_fingerprint(moved) == scene_membership_fingerprint(
        baseline
    )
    assert scene_membership_fingerprint(hidden) != scene_membership_fingerprint(
        baseline
    )


def test_lease_invalidates_on_added_removed_or_visibility_changed_entity():
    baseline = inventory()
    lease = SceneMembershipLease.issue(graph(), baseline)

    added = deepcopy(baseline)
    added["entities"].append(
        {
            "entity_id": "blue_block",
            "label": "blue block",
            "observation_status": "visible_rgbd",
            "geometry": {},
        }
    )
    removed = deepcopy(baseline)
    removed["entities"] = [
        item for item in removed["entities"] if item["entity_id"] != "birdhouse"
    ]
    hidden = deepcopy(baseline)
    hidden["entities"][3]["observation_status"] = "role_bound_not_visible"

    assert lease.assess(added).reasons == ("scene_entity_added",)
    assert lease.assess(removed).reasons == ("scene_entity_removed",)
    hidden_result = lease.assess(hidden)
    assert hidden_result.reasons == ("scene_entity_observation_status_changed",)
    assert hidden_result.observation_status_changes == (
        {
            "entity_id": "red_block",
            "before": "visible_rgbd",
            "after": "role_bound_not_visible",
        },
    )


def test_every_completed_goal_expires_lease_and_requires_fresh_graph():
    lease = SceneMembershipLease.issue(graph(), inventory())

    result = lease.assess(inventory(), completed_goal_id="red-in-bin")

    assert not result.valid
    assert result.reasons == ("goal_completion_requires_fresh_graph",)


def test_task_completion_requires_a_fresh_graph_that_declares_complete():
    ready_lease = SceneMembershipLease.issue(graph(), inventory())
    complete_lease = SceneMembershipLease.issue(
        graph(status="complete"), inventory()
    )

    ready = ready_lease.assess(inventory(), task_completion_requested=True)
    complete = complete_lease.assess(inventory(), task_completion_requested=True)

    assert not ready.valid
    assert ready.reasons == ("task_completion_requires_fresh_complete_graph",)
    assert complete.valid


def test_unadmitted_scope_cannot_issue_a_membership_lease():
    payload = graph_payload()
    payload["entity_scope"][4]["status"] = "unknown"
    uncertain = WorldGoalGraph.from_mapping(payload)

    with pytest.raises(SceneMembershipLeaseError, match="unadmitted scene scope"):
        SceneMembershipLease.issue(uncertain, inventory())


def test_resolved_subset_lease_defers_unknown_and_blocks_task_completion():
    payload = graph_payload(status="needs_observation")
    payload["entity_scope"][4].update(
        status="unknown", reason="Fresh evidence cannot decide membership."
    )
    payload["required_observations"] = [
        {
            "subject_id": "birdhouse",
            "attribute": "covered_by_instruction",
            "operator": "==",
            "value": True,
        }
    ]
    partial = WorldGoalGraph.from_mapping(payload)
    admission = assess_world_goal_graph_scene_scope(partial, inventory())

    assert not admission.admitted
    assert admission.resolved_subset_admitted
    lease = SceneMembershipLease.issue(partial, inventory(), admission)
    assert lease.deferred_unknown_entity_ids == ("birdhouse",)
    assert not lease.to_dict()["task_completion_allowed"]
    completion = lease.assess(inventory(), task_completion_requested=True)
    assert not completion.valid
    assert "task_completion_requires_resolved_scope" in completion.reasons


def test_live_shadow_records_scope_admission_and_non_authoritative_lease():
    source = (
        Path(__file__).parents[1]
        / "scripts"
        / "run_gemini_robotics_robolab.py"
    ).read_text()
    planner = source.index("build_world_goal_graph_prompt(")
    scope = source.index("assess_world_goal_graph_scene_scope(", planner)
    lease = source.index("SceneMembershipLease.issue(", scope)
    trace = source.index('episode_trace["world_goal_graph_shadow"]', lease)
    scheduler = source.index("def operation_scheduler_handler(", trace)

    shadow = source[scope:scheduler]
    assert planner < scope < lease < trace < scheduler
    assert '"scene_scope_admission"' in shadow
    assert '"scene_membership_lease"' in shadow
    assert '"combined_shadow_admission"' in shadow
    assert '"motion_authority": False' in shadow
