import platform
import sys
import os
import subprocess
import datetime
from typing import Dict, Any, List

def run_shell(cmd: str) -> str:
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=2)
        return result.stdout.strip()
    except Exception:
        return ""

def get_gpu_info() -> List[Dict[str, Any]]:
    """
    Parses nvidia-smi explicitly. 
    Prevents deploying a pynvml dependency requirement.
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
    """
    Implements 02_ENVIRONMENT_FINGERPRINT data schema.
    """
    isaac_path = os.environ.get("ISAAC_SIM_PATH", "")
    
    # Generic OS info
    fingerprint = {
        "fingerprint_id": "auto_gen",
        "collected_at": datetime.datetime.utcnow().isoformat(),
        
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
