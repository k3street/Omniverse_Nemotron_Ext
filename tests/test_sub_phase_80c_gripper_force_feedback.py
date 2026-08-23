"""Phase 80c contract test."""
import pytest
pytestmark = pytest.mark.l0


def test_phase_80c_metadata():
    from service.isaac_assist_service.multimodal.sub_phase_80c_gripper_force_feedback import get_phase_metadata
    md = get_phase_metadata()
    assert md["phase"] == "80c"
    assert md["status"] == "landed"


def test_force_monitor_detects_contact_imbalance_and_overload():
    from service.isaac_assist_service.multimodal.sub_phase_80c_gripper_force_feedback import ForceSample, GripperForceMonitor

    monitor = GripperForceMonitor(window_size=1)
    assert monitor.observe(ForceSample(1, 0.2, 0.2)).state == "open"
    assert monitor.observe(ForceSample(2, 2, 2)).state == "contact"
    assert monitor.observe(ForceSample(3, 12, 2)).state == "imbalanced"
    overload = monitor.observe(ForceSample(4, 45, 44))
    assert overload.state == "overload" and overload.should_stop
