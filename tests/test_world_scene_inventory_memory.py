from copy import deepcopy

import pytest

from scripts.world_goal_graph_membership import SceneMembershipLease
from scripts.world_predicate_evaluator_registry import (
    rgbd_world_predicate_evaluator_registry,
)
from scripts.world_scene_inventory_memory import (
    TEMPORARILY_OCCLUDED_STATUS,
    TemporalSceneInventoryError,
    TemporalSceneInventoryMemory,
)
from tests.test_world_goal_graph_membership import graph, inventory


def without_entity(scene, entity_id):
    changed = deepcopy(scene)
    changed["entities"] = [
        item for item in changed["entities"] if item["entity_id"] != entity_id
    ]
    return changed


def entity(scene, entity_id):
    return next(
        item for item in scene["entities"] if item["entity_id"] == entity_id
    )


def test_brief_occlusion_preserves_identity_but_never_cached_geometry():
    baseline = inventory()
    memory = TemporalSceneInventoryMemory(
        baseline,
        maximum_missed_observations=2,
    )

    update = memory.update(without_entity(baseline, "birdhouse"))

    retained = entity(update.inventory, "birdhouse")
    assert retained["observation_status"] == TEMPORARILY_OCCLUDED_STATUS
    assert retained["geometry"] == {}
    assert retained["temporal_presence_evidence"]["presence_source"] == (
        "bounded_rgbd_miss_window"
    )
    assert update.grace_retained_entity_ids == ("birdhouse",)
    assert not update.to_dict()["stale_geometry_exposed"]


def test_runtime_tracker_preserves_occluded_identity_beyond_camera_grace():
    baseline = inventory()
    missing = without_entity(baseline, "birdhouse")
    memory = TemporalSceneInventoryMemory(
        baseline,
        maximum_missed_observations=1,
    )

    first = memory.update(
        missing,
        independently_present_entity_ids=("birdhouse",),
    )
    second = memory.update(
        missing,
        independently_present_entity_ids=("birdhouse",),
    )

    assert first.tracker_confirmed_entity_ids == ("birdhouse",)
    assert second.tracker_confirmed_entity_ids == ("birdhouse",)
    retained = entity(second.inventory, "birdhouse")
    assert retained["temporal_presence_evidence"]["missed_observations"] == 2
    assert retained["temporal_presence_evidence"]["independently_present"]


def test_unconfirmed_identity_expires_fail_closed_after_bounded_grace():
    baseline = inventory()
    missing = without_entity(baseline, "birdhouse")
    memory = TemporalSceneInventoryMemory(
        baseline,
        maximum_missed_observations=1,
    )

    first = memory.update(missing)
    second = memory.update(missing)

    assert "birdhouse" in {
        item["entity_id"] for item in first.inventory["entities"]
    }
    assert "birdhouse" not in {
        item["entity_id"] for item in second.inventory["entities"]
    }
    assert second.expired_entity_ids == ("birdhouse",)
    lease = SceneMembershipLease.issue(graph(), baseline)
    assessment = lease.assess(second.inventory)
    assert not assessment.valid
    assert assessment.reasons == ("scene_entity_removed",)


def test_reacquired_rgbd_geometry_replaces_occlusion_evidence():
    baseline = inventory()
    memory = TemporalSceneInventoryMemory(baseline)
    memory.update(without_entity(baseline, "birdhouse"))

    reacquired = memory.update(baseline)

    restored = entity(reacquired.inventory, "birdhouse")
    assert restored["observation_status"] == "visible_rgbd"
    assert restored["geometry"] == entity(baseline, "birdhouse")["geometry"]
    assert "temporal_presence_evidence" not in restored
    assert reacquired.temporarily_occluded_entity_ids == ()


def test_temporal_identity_does_not_make_pose_predicates_fresh():
    baseline = inventory()
    memory = TemporalSceneInventoryMemory(baseline)
    occluded = memory.update(
        without_entity(baseline, "red_block"),
        independently_present_entity_ids=("red_block",),
    )
    predicate = graph().goals[0].desired_state[0]

    evaluation = rgbd_world_predicate_evaluator_registry().evaluate(
        predicate,
        occluded.inventory,
    )

    assert evaluation.status == "unknown"
    assert evaluation.reason == "predicate_geometry_not_fresh_visible"
    assert evaluation.evidence["stale_geometry_accepted"] is False


def test_new_entity_is_exposed_immediately_instead_of_filtered():
    baseline = inventory()
    current = deepcopy(baseline)
    current["entities"].append(
        {
            "entity_id": "blue_block",
            "label": "blue block",
            "observation_status": "visible_rgbd",
            "geometry": {},
        }
    )
    memory = TemporalSceneInventoryMemory(baseline)

    update = memory.update(current)

    assert update.newly_observed_entity_ids == ("blue_block",)
    assert "blue_block" in {
        item["entity_id"] for item in update.inventory["entities"]
    }


def test_invalid_memory_configuration_fails_closed():
    with pytest.raises(TemporalSceneInventoryError):
        TemporalSceneInventoryMemory(inventory(), maximum_missed_observations=-1)
    with pytest.raises(TemporalSceneInventoryError):
        TemporalSceneInventoryMemory(inventory(), maximum_missed_observations=True)
