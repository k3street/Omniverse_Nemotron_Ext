"""Phase 56 — Recalibration loop."""
import pytest
pytestmark = pytest.mark.l0


def test_phase_56_module_importable():
    import importlib
    mod = importlib.import_module("service.isaac_assist_service.multimodal.recalibrate_loop")
    assert mod is not None
