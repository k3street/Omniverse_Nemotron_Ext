import pytest

from scripts.world_constraint_governor import governed_vertical_target


def test_vertical_governor_raises_nominal_target_to_required_clearance():
    target = governed_vertical_target(
        nominal_target_z=0.30,
        controlled_frame_z=0.20,
        subject_z=0.05,
        reference_z=0.02,
        minimum_clearance_m=0.20,
    )

    assert target == pytest.approx(0.37)


def test_vertical_governor_preserves_higher_nominal_target():
    target = governed_vertical_target(
        nominal_target_z=0.45,
        controlled_frame_z=0.20,
        subject_z=0.05,
        reference_z=0.02,
        minimum_clearance_m=0.20,
    )

    assert target == pytest.approx(0.45)


def test_vertical_governor_rejects_negative_clearance():
    with pytest.raises(ValueError, match="non-negative"):
        governed_vertical_target(
            nominal_target_z=0.30,
            controlled_frame_z=0.20,
            subject_z=0.05,
            reference_z=0.02,
            minimum_clearance_m=-0.01,
        )
