"""Phase 80 contract test — canonical surface_gripper_suction module."""
import pytest
pytestmark = pytest.mark.l0


def test_phase_80_metadata():
    from service.isaac_assist_service.multimodal.surface_gripper_suction import get_phase_metadata
    md = get_phase_metadata()
    assert md["phase"] == 80
    assert md["status"] == "landed"


def test_phase_80_suction_symbols_importable():
    """Verify all public symbols are accessible from the canonical module."""
    from service.isaac_assist_service.multimodal import surface_gripper_suction as m
    assert hasattr(m, "SuctionGripperModel")
    assert hasattr(m, "SuctionCupSpec")
    assert hasattr(m, "GripForceResult")
    assert hasattr(m, "GRIPPER_TYPE_REGISTRY")
    assert hasattr(m, "get_gripper")
    assert hasattr(m, "list_grippers")
