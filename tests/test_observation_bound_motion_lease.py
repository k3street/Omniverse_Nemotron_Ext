from __future__ import annotations

from pathlib import Path

import pytest

from scripts.observation_bound_motion_tools import (
    MotionLeaseConditions,
    MotionToolValidationError,
    assess_motion_lease,
    motion_lease_source_errors,
)


def _assess(conditions: MotionLeaseConditions, **overrides):
    evidence = {
        "contact_available": True,
        "touch": True,
        "contact_force_n": 1.2,
        "tracked_pose_error_m": 0.01,
        "observed_clearance_m": 0.08,
    }
    evidence.update(overrides)
    return assess_motion_lease(conditions, **evidence)


def test_motion_lease_accepts_all_observed_invariants():
    conditions = MotionLeaseConditions(
        require_contact=True,
        minimum_contact_force_n=0.5,
        maximum_tracked_pose_error_m=0.03,
        minimum_observed_clearance_m=0.05,
    )
    assessment = _assess(conditions)
    assert assessment.valid
    assert assessment.invalidation_reasons == ()


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"touch": False}, "contact_lost"),
        (
            {"contact_force_n": 0.1},
            "contact_force_below_lease_minimum",
        ),
        (
            {"tracked_pose_error_m": 0.04},
            "tracked_pose_error_exceeded",
        ),
        (
            {"observed_clearance_m": 0.02},
            "observed_clearance_below_lease_minimum",
        ),
    ],
)
def test_motion_lease_invalidates_only_on_advertised_conditions(overrides, reason):
    conditions = MotionLeaseConditions(
        require_contact=True,
        minimum_contact_force_n=0.5,
        maximum_tracked_pose_error_m=0.03,
        minimum_observed_clearance_m=0.05,
    )
    assessment = _assess(conditions, **overrides)
    assert not assessment.valid
    assert reason in assessment.invalidation_reasons


def test_unadvertised_evidence_never_invalidates_a_lease():
    assessment = _assess(
        MotionLeaseConditions(),
        contact_available=False,
        touch=False,
        contact_force_n=None,
        tracked_pose_error_m=None,
        observed_clearance_m=None,
    )
    assert assessment.valid


def test_required_but_unavailable_observations_fail_closed():
    assessment = _assess(
        MotionLeaseConditions(
            require_contact=True,
            maximum_tracked_pose_error_m=0.03,
            minimum_observed_clearance_m=0.05,
        ),
        contact_available=False,
        touch=None,
        contact_force_n=None,
        tracked_pose_error_m=None,
        observed_clearance_m=None,
    )
    assert set(assessment.invalidation_reasons) == {
        "contact_observation_unavailable",
        "tracked_pose_unavailable",
        "observed_clearance_unavailable",
    }


@pytest.mark.parametrize(
    "kwargs",
    [
        {"minimum_contact_force_n": -1.0},
        {"maximum_tracked_pose_error_m": -0.1},
        {"minimum_observed_clearance_m": float("nan")},
    ],
)
def test_motion_lease_rejects_invalid_condition_thresholds(kwargs):
    with pytest.raises(MotionToolValidationError):
        MotionLeaseConditions(**kwargs)


def test_runner_defaults_to_event_only_motion_observation_leases():
    source = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "run_gemini_robotics_robolab.py"
    ).read_text()
    option = source.index('"--periodic-motion-observations"')
    assert "default=False" in source[option : option + 500]
    checkpoint = source.index("periodic_checkpoint = (")
    assert "args_cli.periodic_motion_observations" in source[
        checkpoint : checkpoint + 300
    ]


def test_runner_executor_advertises_configurable_lease_conditions():
    source = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "run_gemini_robotics_robolab.py"
    ).read_text()
    for field in (
        '"require_contact"',
        '"minimum_contact_force_n"',
        '"maximum_tracked_pose_error_m"',
        '"maximum_tracked_orientation_error_deg"',
        '"minimum_observed_clearance_m"',
        '"minimum_progress_m"',
        '"maximum_stalled_observations"',
    ):
        assert field in source


def test_lease_source_admission_rejects_only_requested_unavailable_sources():
    conditions = MotionLeaseConditions(
        require_contact=False,
        maximum_tracked_pose_error_m=0.03,
        minimum_observed_clearance_m=0.05,
    )
    assert motion_lease_source_errors(
        conditions,
        contact_available=True,
        tracked_pose_available=False,
        observed_clearance_available=False,
    ) == (
        "tracked-pose source is unavailable",
        "observed-clearance source is unavailable",
    )


def test_lease_source_admission_accepts_available_sources():
    conditions = MotionLeaseConditions(
        require_contact=True,
        maximum_tracked_pose_error_m=0.03,
        minimum_observed_clearance_m=0.05,
    )
    assert motion_lease_source_errors(
        conditions,
        contact_available=True,
        tracked_pose_available=True,
        observed_clearance_available=True,
    ) == ()


def test_runner_checks_lease_sources_before_returning_execute_decision():
    source = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "run_gemini_robotics_robolab.py"
    ).read_text()
    chooser_start = source.index("def _choose_observation_bound_motion_tool(")
    chooser_end = source.index("def _actuator_governor_prompt(", chooser_start)
    chooser = source[chooser_start:chooser_end]
    source_check = chooser.index("motion_lease_source_errors(")
    execute_decision = chooser.index('"decision": (', source_check)
    assert source_check < execute_decision


def test_runner_lease_core_consumes_registered_sensor_predicates():
    source = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "run_gemini_robotics_robolab.py"
    ).read_text()
    mover_start = source.index("def _move_eef_to_target(")
    mover_end = source.index("def _recover_transport_grasp(", mover_start)
    mover = source[mover_start:mover_end]
    assert "sensor_predicate_registry.assess(" in mover
    assert 'channel_id="rgbd.object_orientation_error_deg"' in mover
    assert '"tracked_orientation_object_id"' in mover
    assert "assess_motion_lease(" not in mover
    assert '"rgbd_object_orientation_error_exceeded"' in source


def test_runner_enables_raw_instance_ids_for_rgbd_axis_tracking():
    source = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "run_gemini_robotics_robolab.py"
    ).read_text()
    assert '"instance_id_segmentation_fast"' in source
    assert "colorize_instance_id_segmentation = False" in source
    assert "latched_rgbd_axis_reference" in source


def test_runner_native_tool_schema_hides_unavailable_sensor_conditions():
    source = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "run_gemini_robotics_robolab.py"
    ).read_text()
    helper_start = source.index("def _motion_registry_for_observation_sources(")
    chooser_start = source.index(
        "def _choose_observation_bound_motion_tool(", helper_start
    )
    helper = source[helper_start:chooser_start]
    for field in (
        '"maximum_tracked_pose_error_m"',
        '"maximum_tracked_orientation_error_deg"',
        '"tracked_object_id"',
        '"minimum_observed_clearance_m"',
    ):
        assert field in helper
    chooser_end = source.index("def _actuator_governor_prompt(", chooser_start)
    chooser = source[chooser_start:chooser_end]
    assert "observation_registry = _motion_registry_for_observation_sources(" in chooser
    assert "motion_tool_schemas(" in chooser
    assert "observation_registry" in chooser


def test_runner_stalled_kinematic_feedback_invalidates_motion_lease():
    source = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "run_gemini_robotics_robolab.py"
    ).read_text()
    assert 'predicate_id="motion.progress_not_stalled"' in source
    assert 'channel_id="motion.stalled_observation_count"' in source
    assert 'source_id="sim6.robot_kinematic_state_adapter"' in source
    assert '"motion_progress_stalled"' in source


def test_runner_replans_rejected_checkpoint_tools_before_stopping():
    source = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "run_gemini_robotics_robolab.py"
    ).read_text()
    handler_start = source.index("def motion_checkpoint_handler(")
    handler_end = source.index("def operation_scheduler_handler(", handler_start)
    handler = source[handler_start:handler_end]
    assert "motion_checkpoint_replans" in handler
    assert '"previous_motion_tool_outcome": previous_outcome' in handler
    assert 'tool.get("status") != "rejected"' in handler
    assert 'decision["replan_attempts_exhausted"] = True' in handler
    assert "Recovery requested during unsupported phase" not in source


def test_motion_governor_prompt_treats_invalidation_as_recovery_checkpoint():
    source = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "run_gemini_robotics_robolab.py"
    ).read_text()
    prompt_start = source.index("def _motion_governor_prompt(")
    prompt_end = source.index(
        "def _motion_registry_for_observation_sources(", prompt_start
    )
    prompt = source[prompt_start:prompt_end]
    assert "stopped recovery checkpoint" in prompt
    assert "remains stably grasped" in prompt
    assert "restore safe clearance" in prompt
    assert "previous_motion_tool_outcome" in prompt


def test_scheduler_selected_motion_rejects_repeated_passive_hold():
    source = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "run_gemini_robotics_robolab.py"
    ).read_text()
    prompt_start = source.index("def _motion_governor_prompt(")
    prompt_end = source.index(
        "def _motion_registry_for_observation_sources(", prompt_start
    )
    prompt = source[prompt_start:prompt_end]
    assert "scheduler_decision explicitly dispatches continue.runtime_motion" in prompt
    assert "Do not repeat hold_motion" in prompt

    handler_start = source.index("def motion_checkpoint_handler(")
    handler_end = source.index("def operation_scheduler_handler(", handler_start)
    handler = source[handler_start:handler_end]
    scheduler_contract = handler.index("scheduler_selected_motion = bool(")
    repeated_hold = handler.index('tool.get("action") == "hold"', scheduler_contract)
    rejection = handler.index('tool["status"] = "rejected"', repeated_hold)
    retry_gate = handler.index('tool.get("status") != "rejected"', rejection)
    assert scheduler_contract < repeated_hold < rejection < retry_gate


def test_runner_defaults_to_simulator_sensing_and_defers_ros2_ingress():
    source = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "run_gemini_robotics_robolab.py"
    ).read_text()
    option = source.index('"--ros2-sensor-ingress"')
    assert "default=False" in source[option : option + 500]


def test_placement_is_a_model_issued_motion_stage_not_legacy_xy_z_control():
    source = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "run_gemini_robotics_robolab.py"
    ).read_text()
    assert source.count("_residual_center_over_plate(") == 1
    assert '"place",\n                    len(recorded_actions)' in source
    assert 'target_source = "current_observation_model_seed"' in source
    assert '"controller": "observation_bound_model_motion_tool"' in source
    assert '"legacy_local_xy_z_controller_used": False' in source


def test_rejected_phase_boundary_tool_uses_checkpoint_replan_loop():
    source = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "run_gemini_robotics_robolab.py"
    ).read_text()
    rejection = source.index(
        'decision["motion_tool"].get("status") == "rejected"'
    )
    replan = source.index("decision = motion_checkpoint_handler(", rejection)
    terminal_check = source.index(
        'if decision.get("decision") != "execute"', replan
    )
    assert rejection < replan < terminal_check
    assert '"reason": "phase_boundary_motion_tool_rejected"' in source[
        replan:terminal_check
    ]
