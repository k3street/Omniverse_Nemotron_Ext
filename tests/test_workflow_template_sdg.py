"""Phase 36 — Workflow template: generate_sdg_dataset."""
import pytest
pytestmark = pytest.mark.l0


def test_phase_36_module_importable():
    import importlib
    mod = importlib.import_module("service.isaac_assist_service.multimodal.workflow_template_sdg")
    assert mod is not None
