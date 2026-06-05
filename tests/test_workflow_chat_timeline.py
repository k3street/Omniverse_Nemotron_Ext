"""Phase 37 — Workflow lifecycle: chat-side timeline UI."""
import pytest
pytestmark = pytest.mark.l0


def test_phase_37_module_importable():
    import importlib
    mod = importlib.import_module("service.isaac_assist_service.multimodal.workflow_chat_timeline")
    assert mod is not None
