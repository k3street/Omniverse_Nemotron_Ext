"""Phase 40 — Workflow query API."""
import pytest
pytestmark = pytest.mark.l0


def test_phase_40_module_importable():
    import importlib
    mod = importlib.import_module("service.isaac_assist_service.multimodal.workflow_query_api")
    assert mod is not None
