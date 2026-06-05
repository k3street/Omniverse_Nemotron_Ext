"""Phase 89 — GPU vendor detection tests (SPEC/DETECTION layer).

Gate: all tests must pass with no hardware present.
Covers: metadata, RUNTIME_CAPABILITIES, vendor detection,
        runtime selection, compatibility matrix, and dry-run smoke runner.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.l0

# ---------------------------------------------------------------------------
# Import under test
# ---------------------------------------------------------------------------

from service.isaac_assist_service.multimodal.gpu_vendor_detection import (
    RUNTIME_CAPABILITIES,
    GPUInfo,
    GPUSmokeRunner,
    GPUVendor,
    MLRuntime,
    compatibility_matrix,
    detect_vendor_from_string,
    get_phase_metadata,
    select_runtime,
)


# ---------------------------------------------------------------------------
# 1. Metadata
# ---------------------------------------------------------------------------


def test_phase_metadata_fields() -> None:
    md = get_phase_metadata()
    assert md["phase"] == 89
    assert md["status"] == "landed"
    assert "spec_ref" in md
    assert "gate" in md


# ---------------------------------------------------------------------------
# 2. RUNTIME_CAPABILITIES
# ---------------------------------------------------------------------------


def test_runtime_capabilities_has_six_entries() -> None:
    assert len(RUNTIME_CAPABILITIES) == 6


def test_runtime_capabilities_covers_all_runtimes() -> None:
    expected: set[MLRuntime] = {"cuda", "rocm", "directml", "oneapi", "mps", "cpu"}
    actual = {cap.runtime for cap in RUNTIME_CAPABILITIES}
    assert actual == expected


def test_cuda_supports_isaac_and_groot() -> None:
    cuda = next(c for c in RUNTIME_CAPABILITIES if c.runtime == "cuda")
    assert cuda.supports_isaac_sim is True
    assert cuda.supports_gr00t is True
    assert "nvidia" in cuda.supported_vendors


def test_rocm_does_not_support_isaac_or_groot() -> None:
    rocm = next(c for c in RUNTIME_CAPABILITIES if c.runtime == "rocm")
    assert rocm.supports_isaac_sim is False
    assert rocm.supports_gr00t is False
    assert "amd" in rocm.supported_vendors


def test_cpu_fallback_supports_all_vendors() -> None:
    cpu = next(c for c in RUNTIME_CAPABILITIES if c.runtime == "cpu")
    all_vendors: list[GPUVendor] = ["nvidia", "amd", "intel", "apple", "unknown"]
    for vendor in all_vendors:
        assert vendor in cpu.supported_vendors


# ---------------------------------------------------------------------------
# 3. detect_vendor_from_string
# ---------------------------------------------------------------------------


def test_detect_vendor_nvidia_rtx() -> None:
    assert detect_vendor_from_string("NVIDIA GeForce RTX 5070") == "nvidia"


def test_detect_vendor_amd_radeon() -> None:
    assert detect_vendor_from_string("AMD Radeon RX 9070") == "amd"


def test_detect_vendor_intel_arc() -> None:
    assert detect_vendor_from_string("Intel Arc A770") == "intel"


def test_detect_vendor_apple_m3() -> None:
    assert detect_vendor_from_string("Apple M3 Max") == "apple"


def test_detect_vendor_unknown() -> None:
    assert detect_vendor_from_string("Unknown GPU XQ-999") == "unknown"


def test_detect_vendor_nvidia_quadro() -> None:
    assert detect_vendor_from_string("NVIDIA Quadro RTX 6000") == "nvidia"


def test_detect_vendor_amd_instinct() -> None:
    assert detect_vendor_from_string("AMD Instinct MI300X") == "amd"


def test_detect_vendor_intel_iris() -> None:
    assert detect_vendor_from_string("Intel Iris Xe Graphics") == "intel"


def test_detect_vendor_apple_m1() -> None:
    assert detect_vendor_from_string("Apple M1 Pro") == "apple"


# ---------------------------------------------------------------------------
# 4. select_runtime
# ---------------------------------------------------------------------------


def test_select_runtime_nvidia_no_requirements() -> None:
    assert select_runtime("nvidia") == "cuda"


def test_select_runtime_nvidia_require_isaac() -> None:
    result = select_runtime("nvidia", require_isaac_sim=True)
    assert result == "cuda"


def test_select_runtime_amd_require_isaac_falls_back_to_cpu() -> None:
    # No AMD runtime supports Isaac Sim → must fall back to cpu
    result = select_runtime("amd", require_isaac_sim=True)
    assert result == "cpu"


def test_select_runtime_amd_no_requirements_prefers_rocm() -> None:
    result = select_runtime("amd")
    assert result == "rocm"


def test_select_runtime_intel_no_requirements_prefers_oneapi_or_directml() -> None:
    result = select_runtime("intel")
    # directml (index 2) comes before oneapi (index 3) in preference order
    assert result in ("directml", "oneapi")


def test_select_runtime_intel_prefers_directml_over_oneapi() -> None:
    # Per _RUNTIME_PREFERENCE: cuda > rocm > directml > oneapi > mps > cpu
    result = select_runtime("intel")
    assert result == "directml"


def test_select_runtime_apple_no_requirements() -> None:
    result = select_runtime("apple")
    assert result == "mps"


def test_select_runtime_unknown_always_cpu() -> None:
    assert select_runtime("unknown") == "cpu"


def test_select_runtime_unknown_require_isaac_cpu() -> None:
    assert select_runtime("unknown", require_isaac_sim=True) == "cpu"


# ---------------------------------------------------------------------------
# 5. compatibility_matrix
# ---------------------------------------------------------------------------


def test_compatibility_matrix_has_all_vendors() -> None:
    matrix = compatibility_matrix()
    all_vendors: list[GPUVendor] = ["nvidia", "amd", "intel", "apple", "unknown"]
    for vendor in all_vendors:
        assert vendor in matrix, f"Missing vendor: {vendor}"


def test_compatibility_matrix_nvidia_cuda_true() -> None:
    matrix = compatibility_matrix()
    assert matrix["nvidia"]["cuda"] is True


def test_compatibility_matrix_amd_cuda_false() -> None:
    matrix = compatibility_matrix()
    assert matrix["amd"]["cuda"] is False


def test_compatibility_matrix_nvidia_isaac_sim_via_cuda() -> None:
    matrix = compatibility_matrix()
    assert matrix["nvidia"]["cuda_isaac_sim"] is True


def test_compatibility_matrix_all_non_nvidia_isaac_sim_false() -> None:
    matrix = compatibility_matrix()
    for vendor in ("amd", "intel", "apple", "unknown"):
        for key, value in matrix[vendor].items():
            if key.endswith("_isaac_sim"):
                assert value is False, (
                    f"{vendor}/{key} should be False but got {value}"
                )


# ---------------------------------------------------------------------------
# 6. GPUSmokeRunner — dry-run
# ---------------------------------------------------------------------------


def _make_gpu(vendor: GPUVendor, name: str, runtime: MLRuntime) -> GPUInfo:
    return GPUInfo(
        vendor=vendor,
        device_name=name,
        vram_gb=8.0,
        compute_capability="8.9" if vendor == "nvidia" else None,
        driver_version="550.0" if vendor == "nvidia" else None,
        recommended_runtime=runtime,
    )


def test_smoke_runner_dry_run_flag() -> None:
    runner = GPUSmokeRunner(dry_run=True)
    assert runner._dry_run is True


def test_smoke_runner_hardware_mode_raises() -> None:
    with pytest.raises(NotImplementedError):
        GPUSmokeRunner(dry_run=False)


def test_smoke_runner_nvidia_ok() -> None:
    runner = GPUSmokeRunner(dry_run=True)
    gpu = _make_gpu("nvidia", "NVIDIA GeForce RTX 5070", "cuda")
    result = runner.run_smoke_check(gpu)
    assert result["status"] == "ok"
    assert result["vendor"] == "nvidia"
    assert result["runtime"] == "cuda"
    assert result["isaac_compatible"] is True
    assert result["groot_compatible"] is True
    assert result["fallback_to_cpu"] is False
    assert result["dry_run"] is True


def test_smoke_runner_amd_degraded() -> None:
    runner = GPUSmokeRunner(dry_run=True)
    gpu = _make_gpu("amd", "AMD Radeon RX 9070", "rocm")
    result = runner.run_smoke_check(gpu)
    assert result["status"] == "degraded"
    assert result["isaac_compatible"] is False
    assert result["fallback_to_cpu"] is False


def test_smoke_runner_intel_directml_degraded() -> None:
    runner = GPUSmokeRunner(dry_run=True)
    gpu = _make_gpu("intel", "Intel Arc A770", "directml")
    result = runner.run_smoke_check(gpu)
    assert result["status"] == "degraded"
    assert result["isaac_compatible"] is False


def test_smoke_runner_unknown_unsupported() -> None:
    runner = GPUSmokeRunner(dry_run=True)
    gpu = _make_gpu("unknown", "Unknown GPU XQ-999", "cpu")
    result = runner.run_smoke_check(gpu)
    assert result["status"] == "unsupported"
    assert result["fallback_to_cpu"] is True


def test_smoke_runner_apple_mps_degraded() -> None:
    runner = GPUSmokeRunner(dry_run=True)
    gpu = _make_gpu("apple", "Apple M3 Max", "mps")
    result = runner.run_smoke_check(gpu)
    assert result["status"] == "degraded"
    assert result["isaac_compatible"] is False
