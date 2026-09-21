import copy
import json
from pathlib import Path

from scripts.world_effect_episode_acceptance import assess_world_effect_episode


def _operation(index, family="motion", requested_state=None):
    handler = {
        "final_action": [[0.0] * 8],
        "post_dispatch_observation": {
            "rgbd_scene_geometry": {"geometries": [{"runtime_id": "object"}]},
            "tracked_entity_positions_m": {"object": [0.1, 0.2, 0.3]},
        },
    }
    operation = {
        "operation_index": index,
        "planning_source": "initial_world_effect_pipeline",
        "tool_family": family,
        "tool_id": "tool",
        "dispatch": {
            "fresh_evidence": {
                "observation": {
                    "rgbd_scene_geometry": {
                        "geometries": [{"runtime_id": "object"}]
                    },
                    "tracked_entity_positions_m": {"object": [0.1, 0.2, 0.3]},
                }
            },
            "permit": {"permit_id": f"permit:{index}"},
            "outcome": {"handler_result": handler},
            "runtime_lease_after": {"state": "consumed"},
        },
    }
    if requested_state is not None:
        operation["tool_family"] = "actuator"
        touch = requested_state == "engage"
        handler["actuator_report"] = {
            "requested_state": requested_state,
            "engaged_after": touch,
            "state_after": {
                "gripper_closed_fraction": 0.5 if touch else 0.0,
                "current_contact": {
                    "touch": touch,
                    "retained_force_n": 1.0 if touch else 0.0,
                },
            },
        }
        operation["attachment_state_after"] = {
            "entity_ids": ["object"] if touch else [],
            "gripper_engaged": touch,
        }
    return operation


def _accepted_trace():
    operations = [
        _operation(1),
        _operation(2, requested_state="engage"),
        _operation(3),
        _operation(4, requested_state="disengage"),
    ]
    return {
        "status": "guarded_world_effect_sequence_stopped",
        "world_intent_shadow": {"status": "valid"},
        "world_goal_graph_shadow": {"status": "valid"},
        "world_scope_membership_audit_shadow": {"status": "valid"},
        "world_goal_activation_shadow": {"status": "valid"},
        "guarded_world_effect_result": {"selected_goal_completed": True},
        "world_effect_sequence": {
            "status": "stopped",
            "stop_reason": "selected_goal_completed",
            "operations": operations,
            "progress_observations": [
                {
                    "selected_goal_satisfied": index == 4,
                    "selected_goal_evaluations": [
                        {
                            "evaluator_id": "rgbd.visible_geometry_inside",
                            "satisfied": index == 4,
                        }
                    ],
                    "completion_blocking_attachment_entity_ids": [],
                }
                for index in range(1, 5)
            ],
        },
    }


def test_accepts_complete_released_sensor_grounded_episode():
    result = assess_world_effect_episode(_accepted_trace())

    assert result.accepted
    assert not result.rejection_reasons
    assert result.metrics["executed_operation_count"] == 4


def test_accepts_multi_goal_task_only_with_fresh_complete_graph_transition():
    trace = _accepted_trace()
    trace["world_effect_sequence"].update(
        {
            "stop_reason": "task_completed",
            "task_completion_claimed": True,
            "completed_goal_ids": ["object_a_inside", "object_b_inside"],
            "goal_transitions": [
                {
                    "status": "next_goal_selected",
                    "completed_goal_id": "object_a_inside",
                    "fresh_graph": {"status": "ready"},
                    "task_completion_assessment": {"valid": False},
                },
                {
                    "status": "task_complete",
                    "completed_goal_id": "object_b_inside",
                    "fresh_graph": {"status": "complete"},
                    "task_completion_assessment": {"valid": True},
                },
            ],
        }
    )
    trace["guarded_world_effect_result"].update(
        {
            "selected_goal_completed": True,
            "task_completion_claimed": True,
        }
    )

    result = assess_world_effect_episode(trace)

    assert result.accepted
    assert result.metrics["goal_transition_count"] == 2
    assert result.metrics["task_completed"] is True

    incomplete = copy.deepcopy(trace)
    incomplete["world_effect_sequence"]["goal_transitions"][-1][
        "task_completion_assessment"
    ]["valid"] = False
    rejected = assess_world_effect_episode(incomplete)
    assert not rejected.accepted
    assert (
        "task_completion_fresh_graph_admitted"
        in rejected.rejection_reasons
    )


def test_rejects_goal_completion_while_attachment_is_still_engaged():
    trace = _accepted_trace()
    trace["world_effect_sequence"]["operations"].pop()
    trace["world_effect_sequence"]["progress_observations"].pop()

    result = assess_world_effect_episode(trace)

    assert not result.accepted
    assert "attachment_released_and_contact_cleared" in result.rejection_reasons


def test_rejects_incomplete_sensor_action_trace():
    trace = _accepted_trace()
    del trace["world_effect_sequence"]["operations"][1]["dispatch"]["outcome"][
        "handler_result"
    ]["final_action"]

    result = assess_world_effect_episode(trace)

    assert not result.accepted
    assert "sensor_action_model_trace_complete" in result.rejection_reasons


def test_rejects_failed_contact_telemetry_when_episode_summary_is_present():
    trace = _accepted_trace()
    trace["guarded_episode_contact_telemetry"] = {
        "passed": False,
        "coverage": 0.4,
        "touch_samples": 0,
    }

    result = assess_world_effect_episode(trace)

    assert not result.accepted
    assert "contact_telemetry_admitted" in result.rejection_reasons


def test_accepts_explained_recovered_revocation_but_rejects_unexplained_one():
    trace = _accepted_trace()
    lease = trace["world_effect_sequence"]["operations"][0]["dispatch"][
        "runtime_lease_after"
    ]
    lease.update(
        {
            "state": "revoked",
            "revocation_reason": "invalidation:scene.target_geometry_drift",
            "revocation_condition_id": "scene.target_geometry_drift",
            "revocation_evidence": {"entity_id": "object"},
        }
    )
    recovered = assess_world_effect_episode(trace)
    assert recovered.accepted
    assert recovered.metrics["explained_revocation_count"] == 1

    unexplained = copy.deepcopy(trace)
    unexplained["world_effect_sequence"]["operations"][0]["dispatch"][
        "runtime_lease_after"
    ]["revocation_evidence"] = {}
    result = assess_world_effect_episode(unexplained)
    assert not result.accepted
    assert "no_unexplained_lease_revocations" in result.rejection_reasons


def test_known_successful_live_trace_passes_when_available():
    path = Path("artifacts/world_effect_expected_release_live_v110/sequence_trace.json")
    if not path.is_file():
        return

    result = assess_world_effect_episode(json.loads(path.read_text()))

    assert result.accepted, result.to_dict()
