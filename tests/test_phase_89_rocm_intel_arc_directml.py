"""Phase 89 contract test for the back-compat shim."""
import pytest

pytestmark = pytest.mark.l0


def test_phase_89_metadata():
    from service.isaac_assist_service.multimodal.rocm_intel_arc_directml import get_phase_metadata
    md = get_phase_metadata()
    assert md["phase"] == 89
    assert md["status"] == "landed"
    assert "canonical_module" in md
    assert "gpu_vendor_detection" in md["canonical_module"]


def test_phase_89_reexports_match_canonical():
    """Shim re-exports the same symbols as the canonical module."""
    from service.isaac_assist_service.multimodal import rocm_intel_arc_directml as shim
    from service.isaac_assist_service.multimodal import gpu_vendor_detection as canon

    for name in (
        "GPUInfo",
        "GPUSmokeRunner",
        "RUNTIME_CAPABILITIES",
        "RuntimeCapability",
        "compatibility_matrix",
        "detect_vendor_from_string",
        "select_runtime",
    ):
        assert getattr(shim, name) is getattr(canon, name), (
            f"shim re-export of {name} diverged from canonical"
        )
