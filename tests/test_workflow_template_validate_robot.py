"""Phase 35 — Workflow template: validate_robot_import."""
import pytest
pytestmark = pytest.mark.l0


def test_phase_35_module_importable():
    import importlib
    mod = importlib.import_module("service.isaac_assist_service.multimodal.workflow_template_validate_robot")
    assert mod is not None
