import pytest

pytestmark = pytest.mark.l0


def test_phase_77_legacy_name_uses_canonical_implementation():
    from service.isaac_assist_service.multimodal import viewport_hash_cache as canonical
    from service.isaac_assist_service.multimodal import vision_viewport_hash_cache as alias

    assert alias.ViewportHashCache is canonical.ViewportHashCache
    assert alias.get_phase_metadata()["status"] == "landed"


def test_phase_87_legacy_name_uses_canonical_implementation():
    from service.isaac_assist_service.multimodal import stdio_mcp_shim as canonical
    from service.isaac_assist_service.multimodal import stdio_mcp_shim_hardening as alias

    assert alias.StdioMCPShim is canonical.StdioMCPShim
    assert alias.get_phase_metadata()["status"] == "landed"


def test_phase_88_legacy_name_uses_canonical_implementation():
    from service.isaac_assist_service.multimodal import linux_ci_pipeline as canonical
    from service.isaac_assist_service.multimodal import linux_prebuilt_binary_ci as alias

    assert alias.LinuxCIMatrix is canonical.LinuxCIMatrix
    assert alias.get_phase_metadata()["status"] == "landed"


def test_phase_31b_legacy_name_uses_canonical_implementation():
    from service.isaac_assist_service.multimodal import sub_phase_31b_industrial_bridges_full as canonical
    from service.isaac_assist_service.multimodal import sub_phase_31b_industrial_bridge_lifecycle as alias

    assert alias.IndustrialBridge is canonical.IndustrialBridge
    assert alias.get_phase_metadata()["status"] == "landed"
