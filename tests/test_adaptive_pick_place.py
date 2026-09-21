import pytest
import torch

from scripts.adaptive_pick_place import (
    apply_object_relative_grasp,
    apply_lift_test_contract,
    derive_object_relative_grasp,
    derive_manipulation_feedback,
    live_phase_target,
    pregrasp_evidence_ready,
    quaternion_error_axis_angle_wxyz,
    quaternion_multiply_wxyz,
    rotate_vector_wxyz,
    yaw_quaternion_wxyz,
)


GRASP_OFFSET = torch.tensor([-0.010, -0.023, 0.147])


def target(
    phase: str,
    banana: torch.Tensor,
    plate: torch.Tensor,
    eef: torch.Tensor | None = None,
) -> torch.Tensor:
    return live_phase_target(
        phase,
        banana,
        plate,
        GRASP_OFFSET,
        eef_xyz=eef,
        approach_clearance=0.10,
        lift_clearance=0.14,
        plate_hover_height=0.27,
    )


def test_banana_relocation_moves_approach_and_grasp_targets_equally():
    banana = torch.tensor([0.46, -0.11, 0.02])
    plate = torch.tensor([0.59, 0.23, 0.0])
    relocation = torch.tensor([0.06, 0.04, 0.0])

    for phase in ("approach_banana", "descend", "grasp", "lift"):
        assert torch.allclose(
            target(phase, banana + relocation, plate) - target(phase, banana, plate),
            relocation,
        )


def test_generic_approach_phase_matches_legacy_alias():
    movable_object = torch.tensor([0.32, -0.06, 0.02])
    target_receptacle = torch.tensor([0.58, 0.20, 0.0])
    assert torch.allclose(
        target("approach_object", movable_object, target_receptacle),
        target("approach_banana", movable_object, target_receptacle),
    )


def test_plate_relocation_moves_transport_target_but_not_grasp_target():
    banana = torch.tensor([0.46, -0.11, 0.02])
    plate = torch.tensor([0.59, 0.23, 0.0])
    eef = torch.tensor([0.50, -0.10, 0.31])
    relocation = torch.tensor([-0.04, 0.05, 0.0])

    assert torch.allclose(
        target("above_plate", banana, plate + relocation, eef)
        - target("above_plate", banana, plate, eef),
        relocation,
    )
    assert torch.equal(
        target("grasp", banana, plate + relocation),
        target("grasp", banana, plate),
    )


def test_live_targets_use_expected_vertical_clearances():
    banana = torch.tensor([0.46, -0.11, 0.02])
    plate = torch.tensor([0.59, 0.23, 0.0])
    eef = torch.tensor([0.50, -0.10, 0.31])
    grasp = banana + GRASP_OFFSET

    assert target("approach_banana", banana, plate)[2].item() == pytest.approx(
        grasp[2].item() + 0.10
    )
    assert target("lift", banana, plate)[2].item() == pytest.approx(
        grasp[2].item() + 0.14
    )
    assert target("above_plate", banana, plate, eef)[2].item() == pytest.approx(0.27)


def test_transport_target_compensates_measured_carry_offset():
    banana = torch.tensor([0.51, -0.07, 0.12])
    plate = torch.tensor([0.55, 0.24, 0.0])
    eef = torch.tensor([0.50, -0.10, 0.31])

    transport = target("above_plate", banana, plate, eef)
    carried_offset_xy = banana[:2] - eef[:2]
    predicted_banana_xy = transport[:2] + carried_offset_xy
    assert torch.allclose(predicted_banana_xy, plate[:2])


def test_live_target_rejects_unsupported_phase_and_non_finite_pose():
    banana = torch.tensor([0.46, -0.11, 0.02])
    plate = torch.tensor([0.59, 0.23, 0.0])
    with pytest.raises(ValueError, match="unsupported"):
        target("release", banana, plate)
    with pytest.raises(ValueError, match="non-finite"):
        target("grasp", torch.tensor([float("nan"), 0.0, 0.0]), plate)


def test_contact_blocked_closure_converts_lift_retry_to_measured_test():
    decision = {"decision": "retry", "assessment": "fingers look partly open"}
    normalized = apply_lift_test_contract("lift", decision, 0.16)
    assert normalized["decision"] == "execute"
    assert normalized["model_decision"] == "retry"
    assert normalized["supervisor_contract"] == "contact_blocked_closure_lift_test"
    assert decision["decision"] == "retry"


def test_lift_contract_never_overrides_abort_or_nearly_open_gripper():
    assert apply_lift_test_contract("lift", {"decision": "abort"}, 0.20)[
        "decision"
    ] == "abort"
    assert apply_lift_test_contract("lift", {"decision": "retry"}, 0.05)[
        "decision"
    ] == "retry"


def test_manipulation_feedback_distinguishes_candidate_confirmed_and_contact():
    candidate = derive_manipulation_feedback(
        gripper_closed_fraction=0.16,
        fingertip_object_distance_m=0.025,
        object_lift_m=0.0,
        object_target_xy_error_m=0.30,
        object_height_above_target_m=0.02,
    )
    assert candidate["grasp_candidate"] is True
    assert candidate["grasp_confirmed"] is False
    assert candidate["object_target_contact_proxy"] is False

    placed = derive_manipulation_feedback(
        gripper_closed_fraction=0.61,
        fingertip_object_distance_m=0.03,
        object_lift_m=0.08,
        object_target_xy_error_m=0.019,
        object_height_above_target_m=0.020,
    )
    assert placed["grasp_confirmed"] is True
    assert placed["object_target_contact_proxy"] is True


def test_pregrasp_uses_retained_touch_after_scheduler_already_closed_gripper():
    assert pregrasp_evidence_ready(
        model_ready=True,
        confidence=0.9,
        base_target_distance_m=0.094,
        fingertip_object_distance_m=0.008,
        actuator_engaged=True,
        touch_observed=True,
    )
    assert not pregrasp_evidence_ready(
        model_ready=True,
        confidence=0.9,
        base_target_distance_m=0.010,
        fingertip_object_distance_m=0.008,
        actuator_engaged=True,
        touch_observed=False,
    )


def test_pregrasp_requires_pose_alignment_before_gripper_engagement():
    assert not pregrasp_evidence_ready(
        model_ready=True,
        confidence=0.9,
        base_target_distance_m=0.094,
        fingertip_object_distance_m=0.008,
        actuator_engaged=False,
        touch_observed=False,
    )


def test_pregrasp_requires_fresh_jaw_axis_alignment_before_engagement():
    assert not pregrasp_evidence_ready(
        model_ready=True,
        confidence=0.9,
        base_target_distance_m=0.01,
        fingertip_object_distance_m=0.008,
        actuator_engaged=False,
        touch_observed=False,
        jaw_axis_aligned=False,
    )
    assert pregrasp_evidence_ready(
        model_ready=True,
        confidence=0.9,
        base_target_distance_m=0.01,
        fingertip_object_distance_m=0.008,
        actuator_engaged=False,
        touch_observed=False,
        jaw_axis_aligned=True,
    )


def test_object_relative_grasp_rotates_position_and_orientation_with_banana():
    object_xyz = torch.tensor([0.45, -0.10, 0.02])
    object_quat = torch.tensor([1.0, 0.0, 0.0, 0.0])
    grasp_xyz = object_xyz + GRASP_OFFSET
    grasp_quat = torch.tensor([0.555, 0.385, 0.616, -0.406])
    grasp_quat /= torch.linalg.vector_norm(grasp_quat)
    offset_object, object_to_grasp = derive_object_relative_grasp(
        object_xyz, object_quat, grasp_xyz, grasp_quat
    )

    yaw = yaw_quaternion_wxyz(torch.pi / 2, like=object_xyz)
    rotated_xyz, rotated_quat = apply_object_relative_grasp(
        object_xyz, yaw, offset_object, object_to_grasp
    )

    assert torch.allclose(
        rotated_xyz - object_xyz,
        torch.tensor([0.023, -0.010, 0.147]),
        atol=1.0e-6,
    )
    assert torch.allclose(
        rotated_quat,
        quaternion_multiply_wxyz(yaw, grasp_quat),
        atol=1.0e-6,
    )


def test_relative_grasp_round_trip_handles_nonidentity_object_pose():
    object_xyz = torch.tensor([0.45, -0.10, 0.02])
    object_quat = yaw_quaternion_wxyz(0.61, like=object_xyz)
    grasp_xyz = torch.tensor([0.43, -0.13, 0.18])
    grasp_quat = yaw_quaternion_wxyz(-0.72, like=object_xyz)
    offset, relative_quat = derive_object_relative_grasp(
        object_xyz, object_quat, grasp_xyz, grasp_quat
    )
    recovered_xyz, recovered_quat = apply_object_relative_grasp(
        object_xyz, object_quat, offset, relative_quat
    )
    assert torch.allclose(recovered_xyz, grasp_xyz, atol=1.0e-6)
    assert torch.allclose(recovered_quat, grasp_quat, atol=1.0e-6)


def test_quaternion_error_uses_shortest_axis_angle():
    current = torch.tensor([1.0, 0.0, 0.0, 0.0])
    target_quat = yaw_quaternion_wxyz(torch.pi / 2, like=current)
    error = quaternion_error_axis_angle_wxyz(target_quat, current)
    assert torch.allclose(error, torch.tensor([0.0, 0.0, torch.pi / 2]), atol=1.0e-6)
    assert torch.allclose(
        quaternion_error_axis_angle_wxyz(-target_quat, current), error, atol=1.0e-6
    )


def test_rotate_vector_rejects_invalid_input():
    with pytest.raises(ValueError, match="shape"):
        rotate_vector_wxyz(torch.tensor([1.0, 0.0, 0.0, 0.0]), torch.zeros(2))
