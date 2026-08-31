from pathlib import Path

import pytest

from scripts.world_effect_guarded_dispatch import (
    assess_fused_target_geometry,
    classify_expected_post_release_geometry_change,
    DispatchInvalidationEvent,
    GuardedWorldEffectDispatcher,
    RuntimeWorldEffectHandlerRegistry,
    WorldEffectGuardedDispatchError,
    build_fresh_dispatch_evidence,
    interaction_obstacle_geometry,
)
from tests.test_world_effect_runtime_lease import FakeClock, issued_fixture


def dispatcher_fixture(*, handler=None):
    *_, clock, runtime_lease = issued_fixture()
    handlers = RuntimeWorldEffectHandlerRegistry()
    calls = []

    def default_handler(arguments, configuration, lease):
        calls.append((arguments, configuration, lease.lease.issued_lease_id))
        return {"converged": True, "iterations": 4}

    handlers.register(
        runtime_lease.lease.tool_id,
        default_handler if handler is None else handler,
    )
    dispatcher = GuardedWorldEffectDispatcher(
        runtime_lease=runtime_lease,
        handlers=handlers,
        maximum_evidence_age_s=0.5,
        maximum_permit_lifetime_s=0.5,
        clock_ns=clock,
    )
    evidence = build_fresh_dispatch_evidence(
        runtime_lease=runtime_lease,
        source="fresh_test_runtime",
        observation={"rgbd_available": True, "state_sequence": 2},
        observed_at_monotonic_ns=clock(),
    )
    return clock, runtime_lease, dispatcher, evidence, calls


def _geometry(center, extent=(0.04, 0.04, 0.04)):
    return {
        "center_base_m": list(center),
        "visible_extent_base_m": list(extent),
    }


def test_fused_geometry_does_not_treat_visible_centroid_noise_as_translation():
    assessment = assess_fused_target_geometry(
        baseline_geometry=_geometry((0.49, 0.22, 0.048)),
        current_geometry=_geometry(
            (0.49, 0.231, 0.048),
            extent=(0.04, 0.0436, 0.04),
        ),
        maximum_center_shift_m=0.01,
        maximum_extent_change_fraction=0.1,
        baseline_tracked_position_m=(0.5, 0.22, 0.025),
        current_tracked_position_m=(0.5, 0.22, 0.025),
    )

    assert assessment["rgbd_visible_center_shift_m"] > 0.01
    assert assessment["center_translation_source"] == "tracked_entity_pose"
    assert assessment["center_shift_m"] == 0.0
    assert not assessment["invalidated"]


def test_fused_geometry_invalidates_real_translation_or_visible_shape_change():
    translated = assess_fused_target_geometry(
        baseline_geometry=_geometry((0.49, 0.22, 0.048)),
        current_geometry=_geometry((0.505, 0.22, 0.048)),
        maximum_center_shift_m=0.01,
        maximum_extent_change_fraction=0.1,
        baseline_tracked_position_m=(0.5, 0.22, 0.025),
        current_tracked_position_m=(0.515, 0.22, 0.025),
    )
    reshaped = assess_fused_target_geometry(
        baseline_geometry=_geometry((0.49, 0.22, 0.048)),
        current_geometry=_geometry((0.49, 0.22, 0.048), extent=(0.046, 0.04, 0.04)),
        maximum_center_shift_m=0.01,
        maximum_extent_change_fraction=0.1,
        baseline_tracked_position_m=(0.5, 0.22, 0.025),
        current_tracked_position_m=(0.5, 0.22, 0.025),
    )

    assert translated["center_shift_exceeded"]
    assert translated["invalidated"]
    assert reshaped["extent_change_exceeded"]
    assert reshaped["extent_measurement_reliable"]
    assert reshaped["extent_change_invalidating"]
    assert reshaped["invalidated"]


def test_fused_geometry_treats_extent_only_change_as_occlusion_when_centroid_is_inconsistent():
    assessment = assess_fused_target_geometry(
        baseline_geometry=_geometry(
            (0.4953, 0.2336, 0.0479),
            extent=(0.0453, 0.0458, 0.0428),
        ),
        current_geometry=_geometry(
            (0.4953, 0.2414, 0.0479),
            extent=(0.0453, 0.0350, 0.0428),
        ),
        maximum_center_shift_m=0.02,
        maximum_extent_change_fraction=0.2,
        baseline_tracked_position_m=(0.5, 0.22, 0.025),
        current_tracked_position_m=(0.5, 0.22, 0.025),
    )

    assert assessment["extent_change_exceeded"]
    assert assessment["extent_centroid_residual_fraction"] > 0.1
    assert not assessment["extent_measurement_reliable"]
    assert not assessment["extent_change_invalidating"]
    assert not assessment["invalidated"]


def test_fused_geometry_keeps_extent_change_fail_closed_without_independent_tracker():
    assessment = assess_fused_target_geometry(
        baseline_geometry=_geometry((0.49, 0.22, 0.048)),
        current_geometry=_geometry(
            (0.49, 0.231, 0.048),
            extent=(0.046, 0.04, 0.04),
        ),
        maximum_center_shift_m=0.02,
        maximum_extent_change_fraction=0.1,
    )

    assert assessment["extent_measurement_reliable"]
    assert assessment["extent_change_invalidating"]
    assert assessment["invalidated"]


def test_path_obstacles_exclude_only_the_selected_interaction_target():
    geometries = {
        "red_block": {"visible_aabb_min_base_m": [0.0, 0.0, 0.0]},
        "grey_bin": {"visible_aabb_min_base_m": [0.2, 0.0, 0.0]},
        "nearby_block": {"visible_aabb_min_base_m": [0.1, 0.0, 0.0]},
    }

    obstacles = interaction_obstacle_geometry(
        geometries,
        interaction_target_entity_id="red_block",
    )

    assert set(obstacles) == {"grey_bin", "nearby_block"}
    assert obstacles["grey_bin"] is geometries["grey_bin"]
    assert "red_block" in geometries


def _release_geometry_event(entity_id="red_block"):
    return DispatchInvalidationEvent(
        condition_id="scene.target_geometry_drift",
        evidence_source_id="scene.geometry.rgbd",
        evidence={"entity_id": entity_id, "center_shift_m": 0.06},
        reason="target geometry changed",
    )


def _confirmed_release_report():
    return {
        "requested_state": "disengage",
        "engaged_after": False,
        "state_before": {
            "gripper_closed_fraction": 0.47,
            "current_contact": {"touch": True, "retained_force_n": 1.08},
        },
        "state_after": {
            "gripper_closed_fraction": 0.0,
            "current_contact": {"touch": False, "retained_force_n": 0.0},
        },
    }


def test_confirmed_release_classifies_released_target_drift_as_expected_effect():
    assessment = classify_expected_post_release_geometry_change(
        _release_geometry_event(),
        invocation_arguments={"state": "disengage"},
        actuator_report=_confirmed_release_report(),
        target_entity_ids=("red_block",),
    )

    assert assessment is not None
    assert assessment["entity_id"] == "red_block"
    assert assessment["reason"] == (
        "released_entity_settled_after_confirmed_disengagement"
    )
    assert not assessment["lease_revocation_authority"]


@pytest.mark.parametrize(
    ("event", "arguments", "report"),
    [
        (
            _release_geometry_event("grey_bin"),
            {"state": "disengage"},
            _confirmed_release_report(),
        ),
        (
            _release_geometry_event(),
            {"state": "engage"},
            _confirmed_release_report(),
        ),
        (
            _release_geometry_event(),
            {"state": "disengage"},
            {
                **_confirmed_release_report(),
                "state_before": {
                    "gripper_closed_fraction": 0.47,
                    "current_contact": {
                        "touch": False,
                        "retained_force_n": 0.0,
                    },
                },
            },
        ),
        (
            DispatchInvalidationEvent(
                condition_id="scene.target_visibility_lost",
                evidence_source_id="scene.geometry.rgbd",
                evidence={"entity_id": "red_block"},
                reason="target lost",
            ),
            {"state": "disengage"},
            _confirmed_release_report(),
        ),
    ],
)
def test_post_release_exception_fails_closed(event, arguments, report):
    assert (
        classify_expected_post_release_geometry_change(
            event,
            invocation_arguments=arguments,
            actuator_report=report,
            target_entity_ids=("red_block",),
        )
        is None
    )


def test_fresh_evidence_mints_exact_permit_and_dispatches_once():
    _, runtime_lease, dispatcher, evidence, calls = dispatcher_fixture()

    permit = dispatcher.mint_permit(evidence)
    assert permit.issued_lease_id == runtime_lease.lease.issued_lease_id
    assert permit.invocation_digest == runtime_lease.lease.invocation_digest
    assert permit.tool_id == runtime_lease.lease.tool_id
    assert permit.to_dict()["execution_authority"]

    outcome = dispatcher.dispatch(permit)
    serialized = outcome.to_dict()
    assert len(calls) == 1
    assert serialized["handler_bound"]
    assert serialized["tool_called"]
    assert serialized["dispatch_performed"]
    assert serialized["dispatch_permit_consumed"]
    assert serialized["final_lease_state"] == "consumed"
    assert serialized["requires_fresh_observation"]
    assert not serialized["execution_authority"]
    assert runtime_lease.state == "consumed"

    with pytest.raises(WorldEffectGuardedDispatchError, match="already been consumed"):
        dispatcher.dispatch(permit)


def test_fresh_invalidation_revokes_before_handler_binding():
    _, runtime_lease, dispatcher, _, calls = dispatcher_fixture()
    binding = runtime_lease.lease.invalidation_bindings[0]
    evidence = build_fresh_dispatch_evidence(
        runtime_lease=runtime_lease,
        source="fresh_test_runtime",
        observation={"rgbd_available": False},
        invalidation_events=(
            DispatchInvalidationEvent(
                condition_id=binding.condition_id,
                evidence_source_id=binding.evidence_source_id,
                evidence={"available": False},
                reason="required evidence changed",
            ),
        ),
        observed_at_monotonic_ns=10_000_000_000,
    )

    with pytest.raises(WorldEffectGuardedDispatchError, match="invalidated"):
        dispatcher.mint_permit(evidence)

    assert runtime_lease.state == "revoked"
    assert not calls


def test_stale_evidence_and_expired_permit_fail_closed():
    clock, runtime_lease, dispatcher, _, calls = dispatcher_fixture()
    stale = build_fresh_dispatch_evidence(
        runtime_lease=runtime_lease,
        source="fresh_test_runtime",
        observation={"sequence": 1},
        observed_at_monotonic_ns=clock() - 500_000_001,
    )
    with pytest.raises(WorldEffectGuardedDispatchError, match="stale"):
        dispatcher.mint_permit(stale)
    assert not calls

    _, runtime_lease, dispatcher, evidence, calls = dispatcher_fixture()
    permit = dispatcher.mint_permit(evidence)
    dispatcher._clock_ns.nanoseconds += 500_000_000
    with pytest.raises(WorldEffectGuardedDispatchError, match="expired"):
        dispatcher.dispatch(permit)
    assert runtime_lease.active
    assert not calls


def test_handler_failure_revokes_lease_and_consumes_authority():
    def broken_handler(arguments, configuration, lease):
        raise ValueError("simulated executor failure")

    _, runtime_lease, dispatcher, evidence, _ = dispatcher_fixture(
        handler=broken_handler
    )
    permit = dispatcher.mint_permit(evidence)

    with pytest.raises(WorldEffectGuardedDispatchError, match="handler failed"):
        dispatcher.dispatch(permit)

    assert runtime_lease.state == "revoked"
    assert runtime_lease.to_dict()["revocation_reason"] == "dispatch.handler_error"


def test_runner_guarded_mode_orders_permit_handler_dispatch_and_fresh_outcome():
    source = (
        Path(__file__).parents[1]
        / "scripts"
        / "run_gemini_robotics_robolab.py"
    ).read_text()

    assert "--guarded-world-effect-execution" in source
    assert "--world-effect-preflight-settle-steps" in source
    assert "world_effect_preflight_settle" in source
    assert "assess_fused_target_geometry(" in source
    assert "tracked_position_references_m=(" in source
    assert "carry_reference_offset=carry_reference_offset" in source
    assert "tracked_object_id in set(retained_attachment_entity_ids)" in source
    assert "retained_contact_supports_loaded_actuator(" in source
    assert "classify_expected_post_release_geometry_change(" in source
    assert '"expected_post_effect_events"' in source
    tracking_block = source[source.index("tracked_pose_error_m = None") :]
    assert tracking_block.index("if carry_reference_offset is not None:") < (
        tracking_block.index("elif tracked_position_reference is not None:")
    )
    assert "rgbd_axis_references=guarded_rgbd_axis_references" in source
    assert source.count(
        "fresh_rgbd_scene_geometry="
    ) >= 2
    assert "it is never rebased mid-lease" in source
    assert "tracked_orientation_observer=(" in source
    assert "rgbd.oriented_footprint_axis_set_robot_root" in source
    assert "observed_clearance_observer=(" in source
    assert "sim6.live_interaction_frame_plus_fresh_full_scene_rgbd" in source
    assert "_runtime_geometry_by_id(_state(env, initial_object_z))" in source
    assert "build_fresh_dispatch_evidence(" in source
    assert "GuardedWorldEffectDispatcher(" in source
    assert "RuntimeWorldEffectHandlerRegistry(" in source
    assert '"dispatch_permit_issued": True' in source
    assert '"requires_model_replan"' in source
