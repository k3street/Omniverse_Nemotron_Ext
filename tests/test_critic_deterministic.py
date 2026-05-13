"""Phase 45 — Critic deterministic scoring + LLM feature extractor."""
import pytest
pytestmark = pytest.mark.l0


def test_phase_45_module_importable():
    import importlib
    mod = importlib.import_module("service.isaac_assist_service.multimodal.critic_deterministic")
    assert mod is not None
