"""Phase 39 — SQLite checkpoint store."""
import pytest
pytestmark = pytest.mark.l0


def test_phase_39_module_importable():
    import importlib
    mod = importlib.import_module("service.isaac_assist_service.multimodal.workflow_checkpoint_store")
    assert mod is not None
