"""Phase 50 — Diagnose dimension: sensor coverage score."""
import pytest
pytestmark = pytest.mark.l0


def test_phase_50_module_importable():
    import importlib
    mod = importlib.import_module("service.isaac_assist_service.multimodal.diagnose_sensor_coverage")
    assert mod is not None
