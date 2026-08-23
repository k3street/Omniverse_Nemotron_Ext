"""Phase 70b contract test."""
import pytest
pytestmark = pytest.mark.l0


def test_phase_70b_metadata():
    from service.isaac_assist_service.multimodal.sub_phase_70b_robot_subassembly_library import get_phase_metadata
    md = get_phase_metadata()
    assert md["phase"] == "70b"
    assert md["status"] == "landed"


def test_subassembly_library_filters_and_checks_compatibility():
    from service.isaac_assist_service.multimodal.sub_phase_70b_robot_subassembly_library import RobotSubassembly, RobotSubassemblyLibrary

    arm = RobotSubassembly("arm", "arm", "asset://arm.usd", "iso-9409-1-50-4-m6", 18)
    gripper = RobotSubassembly("gripper", "gripper", "asset://gripper.usd", "iso-9409-1-50-4-m6", 1.2)
    incompatible = RobotSubassembly("tool", "tool", "asset://tool.usd", "custom", 0.3)
    library = RobotSubassemblyLibrary([arm, gripper, incompatible])
    assert [entry.subassembly_id for entry in library.query(kind="gripper")] == ["gripper"]
    assert library.compatible("arm", "gripper", "6.0") is True
    assert library.compatible("arm", "tool", "6.0") is False
