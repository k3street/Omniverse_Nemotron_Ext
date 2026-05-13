"""Phase 33 — Generalize start_workflow / approve / reject / revise."""
import pytest
pytestmark = pytest.mark.l0


def test_phase_33_module_importable():
    import importlib
    mod = importlib.import_module("service.isaac_assist_service.multimodal.workflow_engine")
    assert mod is not None
