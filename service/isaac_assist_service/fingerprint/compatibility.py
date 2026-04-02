import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)

def resolve_compatibility(fingerprint: Dict[str, Any]) -> Dict[str, Any]:
    """
    Checks the collected runtime fingerprint against known hardware constraints.
    Prevents hallucinating unsupported patches to the Omniverse runtime.
    """
    
    status = "supported"
    blocking = []
    warnings = []
    
    # 1. Isaac Sim Version Check
    iv = fingerprint.get("isaac_sim_version", "unknown")
    if "4." in iv:
        status = "deprecated"
        warnings.append("Isaac Sim 4.x is deprecated for LLM automated repairs.")
        
    # 2. VRAM Gate
    gpus = fingerprint.get("gpu_devices", [])
    if gpus:
        total_vram = sum(g.get("vram_mb", 0) for g in gpus)
        if total_vram < 8000:
            status = "unsupported"
            blocking.append(f"Insufficient VRAM: {total_vram}MB. Omniverse requires > 8GB.")
    else:
        status = "unknown"
        warnings.append("No NVIDIA GPUs detected. Omniverse may crash.")
        
    return {
        "status": status,
        "mode": "ga" if status == "supported" else "experimental",
        "blocking": blocking,
        "warnings": warnings,
        "informational": []
    }
