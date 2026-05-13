"""Phase 53 — AVM-1: dual analytical+simulation contract."""
import pytest
pytestmark = pytest.mark.l0


def test_phase_53_module_importable():
    import importlib
    mod = importlib.import_module("service.isaac_assist_service.multimodal.avm_dual_contract")
    assert mod is not None
