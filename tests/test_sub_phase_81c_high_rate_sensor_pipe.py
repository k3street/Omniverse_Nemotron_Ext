"""Phase 81c contract test."""
import pytest
pytestmark = pytest.mark.l0


def test_phase_81c_metadata():
    from service.isaac_assist_service.multimodal.sub_phase_81c_high_rate_sensor_pipe import get_phase_metadata
    md = get_phase_metadata()
    assert md["phase"] == "81c"
    assert md["status"] == "landed"


def test_sensor_pipe_bounds_memory_and_reports_drops():
    from service.isaac_assist_service.multimodal.sub_phase_81c_high_rate_sensor_pipe import HighRateSensorPipe

    pipe = HighRateSensorPipe[int](capacity=2)
    pipe.publish(1.0, 10)
    pipe.publish(2.0, 20)
    pipe.publish(3.0, 30)
    assert [sample.payload for sample in pipe.read_since(-1)] == [20, 30]
    assert pipe.stats() == {"buffered": 2, "capacity": 2, "published": 3, "dropped": 1}
