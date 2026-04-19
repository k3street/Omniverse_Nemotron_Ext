"""
rl_policy_runner.py
-------------------
Launch and manage Isaac Lab RL locomotion policy subprocesses.

Wraps the Isaac Lab teleop agent (scripts/demos/teleoperation/teleop_se3_agent.py)
for keyboard-driven velocity-tracked locomotion (G1, H1, etc.).

Keyboard controls (Isaac Lab default):
  W / S   — forward / backward
  A / D   — turn left / right
  Q / E   — strafe left / right
  Space   — stop
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import signal
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# ── Singleton process registry ──────────────────────────────────────────────
_policy_proc: Optional[asyncio.subprocess.Process] = None
_policy_task_name: Optional[str] = None

# Default Isaac Lab teleop script path relative to isaaclab root
_TELEOP_SCRIPT = "scripts/demos/teleoperation/teleop_se3_agent.py"
# Fallback play script (autonomous, no keyboard input)
_PLAY_SCRIPT = "scripts/demos/policy_runner.py"


def _find_isaaclab(hint: Optional[str] = None) -> Optional[str]:
    """Locate the isaaclab.sh launcher."""
    candidates = []
    if hint:
        candidates.append(Path(hint) / "isaaclab.sh")
    candidates += [
        Path.home() / "IsaacLab" / "isaaclab.sh",
        Path.home() / "isaac-lab" / "isaaclab.sh",
        Path("/opt/IsaacLab/isaaclab.sh"),
    ]
    for p in candidates:
        if p.exists():
            return str(p)
    # Also check PATH
    found = shutil.which("isaaclab.sh")
    return found


async def handle_deploy_rl_policy(args: Dict[str, Any]) -> Dict[str, Any]:
    """
    Tool handler for deploy_rl_policy.

    1. Locate isaaclab.sh
    2. Build the subprocess command
    3. Launch as background process
    4. Return PID + keyboard cheat-sheet
    """
    global _policy_proc, _policy_task_name

    task = args.get("task", "Isaac-Velocity-Flat-G1-v0")
    checkpoint = args.get("checkpoint", "")
    teleop_device = args.get("teleop_device", "keyboard")
    num_envs = int(args.get("num_envs", 1))
    isaaclab_path = args.get("isaaclab_path", "")

    # Kill any existing policy
    await _stop_policy()

    isaaclab_sh = _find_isaaclab(isaaclab_path or None)
    if not isaaclab_sh:
        return {
            "error": (
                "isaaclab.sh not found. Set isaaclab_path or ensure IsaacLab is at ~/IsaacLab. "
                "Install: git clone https://github.com/isaac-sim/IsaacLab && cd IsaacLab && ./isaaclab.sh --install"
            )
        }

    isaaclab_root = str(Path(isaaclab_sh).parent)
    teleop_script = str(Path(isaaclab_root) / _TELEOP_SCRIPT)

    if not os.path.exists(teleop_script):
        # Fall back to play script (autonomous)
        play_script = str(Path(isaaclab_root) / _PLAY_SCRIPT)
        if not os.path.exists(play_script):
            return {
                "error": (
                    f"Neither teleop script nor play script found under {isaaclab_root}. "
                    "Ensure Isaac Lab 2.3+ is installed."
                ),
                "looked_for": teleop_script,
            }
        teleop_script = play_script
        logger.warning("[RLRunner] Teleop script not found, falling back to play script (no keyboard input)")
        teleop_device = None

    cmd = [isaaclab_sh, "-p", teleop_script, "--task", task, "--num_envs", str(num_envs)]
    if teleop_device:
        cmd += ["--teleop_device", teleop_device]
    if checkpoint:
        cmd += ["--checkpoint", checkpoint]

    logger.info(f"[RLRunner] Launching: {' '.join(cmd)}")

    try:
        _policy_proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _policy_task_name = task
    except FileNotFoundError as e:
        return {"error": f"Failed to launch isaaclab.sh: {e}"}
    except Exception as e:
        return {"error": f"Unexpected error launching policy: {e}"}

    result: Dict[str, Any] = {
        "status": "policy_launched",
        "pid": _policy_proc.pid,
        "task": task,
        "teleop_device": teleop_device or "none (autonomous)",
        "checkpoint": checkpoint or "latest in task log dir",
        "num_envs": num_envs,
        "isaaclab_root": isaaclab_root,
    }

    if teleop_device == "keyboard":
        result["keyboard_controls"] = {
            "W / S": "forward / backward",
            "A / D": "turn left / right",
            "Q / E": "strafe left / right",
            "Space": "stop",
        }
        result["note"] = (
            "Isaac Sim window must have focus for keyboard input. "
            "Use stop_rl_policy to terminate the subprocess."
        )

    return result


async def handle_stop_rl_policy(_args: Dict[str, Any]) -> Dict[str, Any]:
    """Tool handler for stop_rl_policy."""
    old_pid = await _stop_policy()
    if old_pid is None:
        return {"status": "no_policy_running", "message": "No managed RL policy process is running."}
    return {"status": "stopped", "pid": old_pid, "task": _policy_task_name}


async def _stop_policy() -> Optional[int]:
    """Terminate the managed policy subprocess. Returns old PID or None."""
    global _policy_proc, _policy_task_name
    if _policy_proc is None:
        return None
    old_pid = _policy_proc.pid
    try:
        _policy_proc.send_signal(signal.SIGTERM)
        try:
            await asyncio.wait_for(_policy_proc.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            _policy_proc.kill()
            await _policy_proc.wait()
        logger.info(f"[RLRunner] Stopped PID {old_pid} ({_policy_task_name})")
    except ProcessLookupError:
        logger.info(f"[RLRunner] PID {old_pid} already exited")
    _policy_proc = None
    _policy_task_name = None
    return old_pid
