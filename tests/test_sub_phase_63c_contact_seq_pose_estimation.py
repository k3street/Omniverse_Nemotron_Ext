"""Phase 63c contract test."""
import pytest
pytestmark = pytest.mark.l0


def test_phase_63c_metadata():
    from service.isaac_assist_service.multimodal.sub_phase_63c_contact_seq_pose_estimation import get_phase_metadata
    md = get_phase_metadata()
    assert md["phase"] == "63c"
    assert md["status"] == "landed"


def test_contact_pose_fuses_weighted_candidates():
    from service.isaac_assist_service.multimodal.sub_phase_63c_contact_seq_pose_estimation import ContactPoseCandidate, estimate_contact_pose

    estimate = estimate_contact_pose([
        ContactPoseCandidate((0, 0, 0), (0, 0, 2), 0.75, "depth"),
        ContactPoseCandidate((1, 0, 0), (0, 0, 1), 0.25, "vision"),
    ])
    assert estimate.position_m == pytest.approx((0.25, 0, 0))
    assert estimate.surface_normal == pytest.approx((0, 0, 1))
