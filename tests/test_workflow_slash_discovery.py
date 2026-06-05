"""Phase 43 — Workflow templates discoverable via slash_command_discovery."""
import pytest
pytestmark = pytest.mark.l0


def test_phase_43_module_importable():
    import importlib
    mod = importlib.import_module("service.isaac_assist_service.multimodal.workflow_slash_discovery")
    assert mod is not None
