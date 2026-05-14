"""Phase 89 — ROCm + Intel Arc + DirectML smoke tests (SPEC/DETECTION layer).

Pure-Python GPU vendor detection, runtime compatibility matrix, and
dry-run smoke checker.  No actual GPU hardware or driver calls are made
here — the goal is a clean, testable specification layer that the
runtime-smoke layer (opus-runtime gate) can build on top of.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 89.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional

# ---------------------------------------------------------------------------
# Phase metadata
# ---------------------------------------------------------------------------

PHASE_ID = 89
PHASE_TITLE = "ROCm + Intel Arc + DirectML smoke tests"
PHASE_STATUS = "landed"


def get_phase_metadata() -> Dict[str, Any]:
    """Return phase metadata for spec-coverage audits."""
    return {
        "phase": PHASE_ID,
        "title": PHASE_TITLE,
        "status": PHASE_STATUS,
        "spec_ref": "specs/IA_FULL_SPEC_2026-05-10.md Phase 89",
        "agent": "sonnet-bounded",
        "gate": "pytest",
        "loc": 250,
    }


# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

GPUVendor = Literal["nvidia", "amd", "intel", "apple", "unknown"]
MLRuntime = Literal["cuda", "rocm", "directml", "oneapi", "mps", "cpu"]

# Ordered preference list used by select_runtime()
_RUNTIME_PREFERENCE: List[MLRuntime] = [
    "cuda",
    "rocm",
    "directml",
    "oneapi",
    "mps",
    "cpu",
]

# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class GPUInfo:
    """Detected GPU information."""

    vendor: GPUVendor
    device_name: str
    vram_gb: float
    compute_capability: Optional[str]
    driver_version: Optional[str]
    recommended_runtime: MLRuntime


@dataclass
class RuntimeCapability:
    """Describes a single ML runtime's capabilities and constraints."""

    runtime: MLRuntime
    supported_vendors: List[GPUVendor]
    min_driver_version: Optional[str]
    supports_isaac_sim: bool
    supports_gr00t: bool
    notes: str


# ---------------------------------------------------------------------------
# Compatibility matrix (static data)
# ---------------------------------------------------------------------------

RUNTIME_CAPABILITIES: List[RuntimeCapability] = [
    RuntimeCapability(
        runtime="cuda",
        supported_vendors=["nvidia"],
        min_driver_version="525.0",
        supports_isaac_sim=True,
        supports_gr00t=True,
        notes="NVIDIA CUDA; primary Isaac Sim and GR00T runtime.",
    ),
    RuntimeCapability(
        runtime="rocm",
        supported_vendors=["amd"],
        min_driver_version="5.4",
        supports_isaac_sim=False,
        supports_gr00t=False,
        notes="AMD ROCm; Isaac Sim not supported; GR00T support limited / experimental.",
    ),
    RuntimeCapability(
        runtime="directml",
        supported_vendors=["nvidia", "amd", "intel"],
        min_driver_version=None,
        supports_isaac_sim=False,
        supports_gr00t=False,
        notes=(
            "Microsoft DirectML; cross-vendor but lacks PhysX/CUDA path for Isaac Sim."
        ),
    ),
    RuntimeCapability(
        runtime="oneapi",
        supported_vendors=["intel"],
        min_driver_version=None,
        supports_isaac_sim=False,
        supports_gr00t=False,
        notes="Intel oneAPI (SYCL); Intel Arc and Xe targets; no Isaac Sim support.",
    ),
    RuntimeCapability(
        runtime="mps",
        supported_vendors=["apple"],
        min_driver_version=None,
        supports_isaac_sim=False,
        supports_gr00t=False,
        notes="Apple Metal Performance Shaders; macOS/Apple Silicon only.",
    ),
    RuntimeCapability(
        runtime="cpu",
        supported_vendors=["nvidia", "amd", "intel", "apple", "unknown"],
        min_driver_version=None,
        supports_isaac_sim=False,
        supports_gr00t=False,
        notes="CPU fallback; works everywhere but not suitable for simulation.",
    ),
]


# ---------------------------------------------------------------------------
# Vendor string detection
# ---------------------------------------------------------------------------

# Keyword mapping: each tuple is (keyword_list, vendor)
_VENDOR_KEYWORDS: List[tuple[List[str], GPUVendor]] = [
    (["NVIDIA", "GeForce", "RTX", "GTX", "Quadro", "Tesla", "A100", "H100", "H200"], "nvidia"),
    (["AMD", "Radeon", "Instinct", "RX ", "Vega", "Navi"], "amd"),
    (["Intel", "Arc", "Iris", "UHD", "Xe"], "intel"),
    (["Apple", "M1", "M2", "M3", "M4"], "apple"),
]


def detect_vendor_from_string(device_string: str) -> GPUVendor:
    """Infer GPU vendor from a device name string.

    Case-sensitive keyword scan; first match wins following the priority
    order nvidia → amd → intel → apple → unknown.

    Args:
        device_string: Human-readable GPU device name, e.g.
            ``"NVIDIA GeForce RTX 5070"`` or ``"AMD Radeon RX 9070"``.

    Returns:
        A :data:`GPUVendor` literal.
    """
    for keywords, vendor in _VENDOR_KEYWORDS:
        for kw in keywords:
            if kw in device_string:
                return vendor
    return "unknown"


# ---------------------------------------------------------------------------
# Runtime selection
# ---------------------------------------------------------------------------


def select_runtime(
    vendor: GPUVendor,
    require_isaac_sim: bool = False,
    require_gr00t: bool = False,
) -> MLRuntime:
    """Select the best available ML runtime for the given vendor.

    Filters :data:`RUNTIME_CAPABILITIES` by:
    1. ``vendor`` present in ``supported_vendors``
    2. ``supports_isaac_sim`` if *require_isaac_sim* is True
    3. ``supports_gr00t`` if *require_gr00t* is True

    Among remaining candidates the preference order
    ``cuda > rocm > directml > oneapi > mps > cpu`` is applied.

    Args:
        vendor: Detected :data:`GPUVendor`.
        require_isaac_sim: Set True if the workload needs Isaac Sim.
        require_gr00t: Set True if the workload needs GR00T.

    Returns:
        Best :data:`MLRuntime` for the constraints; always returns
        ``"cpu"`` when no better match exists.
    """
    candidates: List[RuntimeCapability] = []
    for cap in RUNTIME_CAPABILITIES:
        if vendor not in cap.supported_vendors:
            continue
        if require_isaac_sim and not cap.supports_isaac_sim:
            continue
        if require_gr00t and not cap.supports_gr00t:
            continue
        candidates.append(cap)

    if not candidates:
        return "cpu"

    # Sort by preference order; lower index = higher preference
    def _pref(cap: RuntimeCapability) -> int:
        """Return the preference rank of *cap* (lower = more preferred).

        Args:
            cap (RuntimeCapability): Capability to rank.

        Returns:
            int: Index into ``_RUNTIME_PREFERENCE``; ``len`` of the list when unknown.
        """
        try:
            return _RUNTIME_PREFERENCE.index(cap.runtime)
        except ValueError:
            return len(_RUNTIME_PREFERENCE)

    candidates.sort(key=_pref)
    return candidates[0].runtime


# ---------------------------------------------------------------------------
# Compatibility matrix export
# ---------------------------------------------------------------------------

_ALL_VENDORS: List[GPUVendor] = ["nvidia", "amd", "intel", "apple", "unknown"]


def compatibility_matrix() -> Dict[GPUVendor, Dict[str, bool]]:
    """Return a nested dict summarising runtime support per vendor.

    Structure::

        {
            "nvidia": {
                "cuda": True,
                "rocm": False,
                ...
            },
            ...
        }

    The ``"<runtime>_isaac_sim"`` and ``"<runtime>_gr00t"`` keys are also
    included to expose per-capability flags.
    """
    matrix: Dict[GPUVendor, Dict[str, bool]] = {}
    for vendor in _ALL_VENDORS:
        row: Dict[str, bool] = {}
        for cap in RUNTIME_CAPABILITIES:
            supported = vendor in cap.supported_vendors
            row[cap.runtime] = supported
            row[f"{cap.runtime}_isaac_sim"] = supported and cap.supports_isaac_sim
            row[f"{cap.runtime}_gr00t"] = supported and cap.supports_gr00t
        matrix[vendor] = row
    return matrix


# ---------------------------------------------------------------------------
# Smoke runner
# ---------------------------------------------------------------------------


class GPUSmokeRunner:
    """Dry-run GPU smoke checker.

    In *dry_run* mode (the default and only supported mode for this
    detection layer) no hardware is accessed.  The runner uses the
    compatibility matrix to determine expected outcomes.

    Args:
        dry_run: Must be True; hardware invocation is not implemented in
            this layer.
    """

    def __init__(self, dry_run: bool = True) -> None:
        """Initialise the smoke runner in dry-run (hardware-free) mode.

        Args:
            dry_run (bool, optional): Must be ``True``; hardware invocation is not
                implemented in this detection layer. Defaults to ``True``.

        Raises:
            NotImplementedError: If ``dry_run=False`` is passed.
        """
        if not dry_run:
            raise NotImplementedError(
                "Hardware smoke testing is not implemented in the detection layer. "
                "Set dry_run=True or use the opus-runtime layer."
            )
        self._dry_run = dry_run

    def run_smoke_check(self, gpu: GPUInfo) -> Dict[str, Any]:
        """Run a dry-run smoke check for the given GPU.

        Returns a result dict with the following keys:

        * ``vendor`` — detected vendor string
        * ``runtime`` — recommended runtime
        * ``isaac_compatible`` — bool, True only for CUDA/NVIDIA
        * ``groot_compatible`` — bool, True only for CUDA/NVIDIA
        * ``fallback_to_cpu`` — True when recommended_runtime is "cpu"
        * ``status`` — ``"ok"`` | ``"degraded"`` | ``"unsupported"``
        * ``dry_run`` — always True in this layer

        Status rules:
        - ``"ok"`` — vendor is nvidia and cuda runtime available
        - ``"degraded"`` — runtime available but not cuda (e.g. rocm, directml)
        - ``"unsupported"`` — unknown vendor or no non-cpu runtime available
        """
        vendor = gpu.vendor
        runtime = gpu.recommended_runtime

        # Determine capability flags from RUNTIME_CAPABILITIES
        isaac_compatible = False
        groot_compatible = False
        for cap in RUNTIME_CAPABILITIES:
            if cap.runtime == runtime and vendor in cap.supported_vendors:
                isaac_compatible = cap.supports_isaac_sim
                groot_compatible = cap.supports_gr00t
                break

        fallback_to_cpu = runtime == "cpu"

        # Determine status
        if vendor == "unknown" or fallback_to_cpu:
            status = "unsupported"
        elif runtime == "cuda":
            status = "ok"
        else:
            status = "degraded"

        return {
            "vendor": vendor,
            "runtime": runtime,
            "isaac_compatible": isaac_compatible,
            "groot_compatible": groot_compatible,
            "fallback_to_cpu": fallback_to_cpu,
            "status": status,
            "dry_run": True,
        }
