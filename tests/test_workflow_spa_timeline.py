"""Phase 38 — SPA-side timeline UI hooks."""
import pytest
pytestmark = pytest.mark.l0


def test_phase_38_module_importable():
    import importlib
    mod = importlib.import_module("service.isaac_assist_service.multimodal.workflow_spa_timeline")
    assert mod is not None
