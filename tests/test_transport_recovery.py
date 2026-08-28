import numpy as np
import pytest

from scripts.transport_recovery import (
    SupportContactMonitor,
    assess_release_detachment,
    assess_recovery_hold,
    object_support_contact_event,
    placement_completion_event,
    support_aligned_object_quaternion_wxyz,
)


def test_stable_held_object_relatches_and_resumes():
    result = assess_recovery_hold(
        offset_before=np.array([0.0, 0.0, 0.19]),
        offset_after=np.array([0.002, -0.001, 0.191]),
        object_z_after=0.09,
        object_initial_z=0.02,
    )
    assert result.safe_to_resume is True
    assert result.strategy == "relatch_and_resume"
    assert result.reasons == ()


@pytest.mark.parametrize(
    ("offset_after", "object_z_after", "reason"),
    [
        ([0.02, 0.0, 0.19], 0.09, "continued_slip_during_hold"),
        ([0.0, 0.0, 0.19], 0.03, "object_not_securely_lifted"),
    ],
)
def test_unstable_or_dropped_object_requires_set_down_regrasp(
    offset_after, object_z_after, reason
):
    result = assess_recovery_hold(
        offset_before=np.array([0.0, 0.0, 0.19]),
        offset_after=np.asarray(offset_after),
        object_z_after=object_z_after,
        object_initial_z=0.02,
    )
    assert result.safe_to_resume is False
    assert result.strategy == "set_down_and_regrasp"
    assert reason in result.reasons


def test_recovery_contract_rejects_invalid_inputs():
    with pytest.raises(ValueError):
        assess_recovery_hold(
            offset_before=np.zeros(2),
            offset_after=np.zeros(3),
            object_z_after=0.1,
            object_initial_z=0.02,
        )


def test_set_down_stops_on_object_support_instead_of_pressing_target():
    assert object_support_contact_event(
        object_z=0.08,
        object_initial_z=0.02,
        set_down_clearance_m=0.006,
    ) is None
    event = object_support_contact_event(
        object_z=0.031,
        object_initial_z=0.02,
        set_down_clearance_m=0.006,
    )
    assert event is not None
    assert event["converged"] is True
    assert event["reason"] == "object_support_contact"


def test_rotated_object_support_is_detected_from_stalled_downward_motion():
    monitor = SupportContactMonitor(
        object_initial_z=0.02,
        set_down_clearance_m=0.006,
        consecutive_stall_samples=3,
    )
    samples = [
        (0.09, 0.28),
        (0.08, 0.265),
        (0.0798, 0.2648),
        (0.0797, 0.2647),
        (0.0796, 0.2646),
    ]
    event = None
    for object_z, eef_z in samples:
        event = monitor.update(
            object_z=object_z,
            eef_z=eef_z,
            target_eef_z=0.22,
            target_tolerance_m=0.012,
        )
    assert event is not None
    assert event["converged"] is True
    assert event["reason"] == "set_down_motion_stalled_at_support_envelope"
    assert event["consecutive_stall_samples"] == 3


def test_stall_above_support_envelope_does_not_claim_contact():
    monitor = SupportContactMonitor(
        object_initial_z=0.02,
        set_down_clearance_m=0.006,
        consecutive_stall_samples=2,
    )
    for _ in range(4):
        event = monitor.update(
            object_z=0.18,
            eef_z=0.35,
            target_eef_z=0.22,
            target_tolerance_m=0.012,
        )
    assert event is None


def test_aligned_set_down_detects_object_support_while_tool_continues_down():
    monitor = SupportContactMonitor(
        object_initial_z=0.02,
        set_down_clearance_m=0.006,
        consecutive_stall_samples=3,
        require_eef_stall=False,
    )
    event = None
    for object_z, eef_z in (
        (0.0900, 0.28),
        (0.0895, 0.27),
        (0.0890, 0.26),
        (0.0885, 0.25),
    ):
        event = monitor.update(
            object_z=object_z,
            eef_z=eef_z,
            target_eef_z=0.20,
            target_tolerance_m=0.012,
        )

    assert event is not None
    assert event["reason"] == "set_down_motion_stalled_at_support_envelope"
    assert event["require_eef_stall"] is False


def test_detachment_allows_subject_to_settle_away_from_retreat_direction():
    result = assess_release_detachment(
        controlled_start_xyz=np.array([0.0, 0.0, 0.25]),
        controlled_final_xyz=np.array([0.0, 0.0, 0.33]),
        subject_start_xyz=np.array([0.02, 0.0, 0.10]),
        subject_final_xyz=np.array([0.10, 0.02, 0.03]),
        released=True,
        goal_relation_holds=True,
        terminal=False,
    )

    assert result["converged"] is True
    assert result["subject_motion_along_retreat_m"] < 0.0


def test_detachment_rejects_subject_following_the_retreat():
    result = assess_release_detachment(
        controlled_start_xyz=np.array([0.0, 0.0, 0.25]),
        controlled_final_xyz=np.array([0.0, 0.0, 0.33]),
        subject_start_xyz=np.array([0.0, 0.0, 0.10]),
        subject_final_xyz=np.array([0.0, 0.0, 0.18]),
        released=True,
        goal_relation_holds=True,
        terminal=False,
    )

    assert result["converged"] is False
    assert result["subject_motion_along_retreat_m"] > 0.02


def test_recovery_orientation_discards_roll_pitch_but_preserves_yaw():
    roll = np.deg2rad(70.0)
    yaw = np.deg2rad(35.0)
    # ZYX yaw/roll composition in wxyz form.
    quaternion = np.array(
        [
            np.cos(yaw / 2) * np.cos(roll / 2),
            np.cos(yaw / 2) * np.sin(roll / 2),
            np.sin(yaw / 2) * np.sin(roll / 2),
            np.sin(yaw / 2) * np.cos(roll / 2),
        ]
    )
    aligned = support_aligned_object_quaternion_wxyz(quaternion)
    expected = np.array([np.cos(yaw / 2), 0.0, 0.0, np.sin(yaw / 2)])
    np.testing.assert_allclose(aligned, expected, atol=1.0e-6)


def test_recovery_orientation_rejects_invalid_quaternion():
    with pytest.raises(ValueError):
        support_aligned_object_quaternion_wxyz(np.zeros(4))


def test_recovery_set_down_can_complete_task_without_regrasp():
    event = placement_completion_event(
        object_xyz=np.array([0.53, 0.23, 0.022]),
        target_xyz=np.array([0.55, 0.25, 0.0]),
    )
    assert event is not None
    assert event["completed"] is True
    assert event["reason"] == "object_target_contact"


def test_recovery_set_down_off_target_requires_regrasp():
    assert (
        placement_completion_event(
            object_xyz=np.array([0.35, 0.05, 0.022]),
            target_xyz=np.array([0.55, 0.25, 0.0]),
        )
        is None
    )


def test_visually_overlapping_object_must_also_be_stable():
    assert (
        placement_completion_event(
            object_xyz=np.array([0.53, 0.23, 0.034]),
            target_xyz=np.array([0.55, 0.25, 0.0]),
            settled_displacement_m=0.04,
        )
        is None
    )
