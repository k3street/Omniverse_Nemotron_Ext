"""Hardware and software fingerprint collector.

Gathers OS, Python, Isaac Sim path, and GPU info in one call so the
compatibility resolver and audit log can embed a machine snapshot.
All shell calls are wrapped with a 2-second timeout and fail silently
to avoid hanging the service at startup.
"""
import platform
import sys
import os
import subprocess
import datetime
from datetime import timezone
from typing import Dict, Any, List


def run_shell(cmd: str) -> str:
    """Execute a shell command and return its stdout, stripped.

    Returns an empty string on any error (timeout, non-zero exit, etc.)
    so callers never need to handle exceptions from optional probes.

    Args:
        cmd (str): Shell command to run.

    Returns:
        str: Stripped stdout, or ``""`` on failure.
    """
    try:
        # shell=True is intentional: all 4 in-tree callers pass hard-coded
        # strings containing pipes (`nvidia-smi ... | head -n 1`,
        # `nvcc --version | grep release | awk ...`). No user input reaches
        # this function — `cmd` is always a literal in the calling module.
        result = subprocess.run(  # noqa: audit-Q10
            cmd, shell=True, capture_output=True, text=True, timeout=2
        )
        return result.stdout.strip()
    except Exception:
        return ""


def get_gpu_info() -> List[Dict[str, Any]]:
    """Collect GPU info via ``nvidia-smi``, avoiding a pynvml dependency.

    Returns:
        list[dict]: One entry per detected GPU with keys
        ``device_index`` (int), ``name`` (str), ``vram_mb`` (int).
        Empty list when no NVIDIA GPU is detected or ``nvidia-smi`` is absent.
    """
    gpus = []
    out = run_shell("nvidia-smi --query-gpu=index,name,memory.total --format=csv,noheader")
    if out:
        for line in out.splitlines():
            parts = [p.strip() for p in line.split(',')]
            if len(parts) == 3:
                # "0", "NVIDIA GeForce RTX 4090", "24564 MiB"
                vram_mb = ''.join(filter(str.isdigit, parts[2]))
                gpus.append({
                    "device_index": int(parts[0]),
                    "name": parts[1],
                    "vram_mb": int(vram_mb) if vram_mb else 0
                })
    return gpus

def collect_fingerprint() -> Dict[str, Any]:
    """Collect a full environment fingerprint snapshot.

    Implements the ``02_ENVIRONMENT_FINGERPRINT`` schema: OS, Python,
    Isaac Sim version inferred from ``ISAAC_SIM_PATH``, GPU devices,
    driver version, and CUDA version.

    Returns:
        dict: Fingerprint with keys ``fingerprint_id``, ``collected_at``,
        ``os_distribution``, ``os_version``, ``kernel_version``,
        ``architecture``, ``python_version``, ``python_executable``,
        ``isaac_sim_install_path``, ``isaac_sim_version``,
        ``gpu_devices``, ``driver_version``, ``cuda_version``.
    """
    isaac_path = os.environ.get("ISAAC_SIM_PATH", "")
    
    # Generic OS info
    fingerprint = {
        "fingerprint_id": "auto_gen",
        "collected_at": datetime.datetime.now(timezone.utc).isoformat(),
        
        "os_distribution": platform.system(),
        "os_version": platform.release(),
        "kernel_version": platform.version(),
        "architecture": platform.machine(),
        
        "python_version": platform.python_version(),
        "python_executable": sys.executable,
        
        # Isaac Specifics
        "isaac_sim_install_path": isaac_path,
        "isaac_sim_version": "6.0.0" if "6.0" in isaac_path else "5.1.0",
        
        "gpu_devices": get_gpu_info(),
        "driver_version": run_shell("nvidia-smi --query-gpu=driver_version --format=csv,noheader | head -n 1"),
        "cuda_version": run_shell("nvcc --version | grep release | awk '{print $5}' | sed 's/,//'")
    }
    return fingerprint
