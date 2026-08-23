"""Phase 63d contract test."""
import pytest
pytestmark = pytest.mark.l0


def test_phase_63d_metadata():
    from service.isaac_assist_service.multimodal.sub_phase_63d_contact_seq_telemetry import get_phase_metadata
    md = get_phase_metadata()
    assert md["phase"] == "63d"
    assert md["status"] == "landed"


def test_contact_telemetry_aggregates_results():
    from service.isaac_assist_service.multimodal.execute_contact_sequence_runtime import ContactObservation, ContactStepResult
    from service.isaac_assist_service.multimodal.sub_phase_63d_contact_seq_telemetry import aggregate_contact_telemetry

    results = [ContactStepResult(0, "make_contact", True, ContactObservation(0, 5, 1, True, 0), 0.2)]
    report = aggregate_contact_telemetry(results)
    assert report.success_rate == 1.0
    assert report.peak_force_n == 5
    assert report.total_duration_s == pytest.approx(0.2)
