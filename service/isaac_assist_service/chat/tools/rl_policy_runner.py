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

# ── Inspire Hand joint names on the Unitree G1 ──────────────────────────────
# These DOFs are not controlled by the locomotion policy (12 DOF legs only).
# Leaving them free causes the extra mass to destabilize the robot at spawn.
# Freeze strategy: high stiffness (500 Nm/rad) + high damping (50 Nms/rad) at 0.
_INSPIRE_HAND_JOINTS = [
    # Left hand
    "left_hand_j1", "left_hand_j2", "left_hand_j3", "left_hand_j4",
    "left_hand_j5", "left_hand_j6", "left_hand_j7", "left_hand_j8",
    "left_hand_j9", "left_hand_j10", "left_hand_j11", "left_hand_j12",
    # Right hand
    "right_hand_j1", "right_hand_j2", "right_hand_j3", "right_hand_j4",
    "right_hand_j5", "right_hand_j6", "right_hand_j7", "right_hand_j8",
    "right_hand_j9", "right_hand_j10", "right_hand_j11", "right_hand_j12",
]

_FREEZE_HAND_STIFFNESS = 500.0   # Nm/rad — stiff enough to hold position
_FREEZE_HAND_DAMPING   = 50.0    # Nms/rad

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


def _build_freeze_hand_script(robot_prim_path: str) -> str:
    """
    Generate a USD/PhysX script that freezes Inspire Hand joints in place.
    Sets high stiffness + damping on all hand joints so the locomotion policy
    (which only controls the 12 leg DOFs) doesn't have to fight free-floating
    hand mass.
    """
    joints_repr = repr(_INSPIRE_HAND_JOINTS)
    return f"""\
import omni.usd
from pxr import UsdPhysics

stage = omni.usd.get_context().get_stage()
robot_path = "{robot_prim_path}"
hand_joints = {joints_repr}
stiffness = {_FREEZE_HAND_STIFFNESS}
damping   = {_FREEZE_HAND_DAMPING}

frozen = []
for prim in stage.Traverse():
    prim_name = prim.GetPath().name
    if prim_name in hand_joints and prim.IsA(UsdPhysics.RevoluteJoint):
        drive = UsdPhysics.DriveAPI.Apply(prim, "angular")
        drive.GetStiffnessAttr().Set(stiffness)
        drive.GetDampingAttr().Set(damping)
        drive.GetTargetPositionAttr().Set(0.0)
        frozen.append(str(prim.GetPath()))

print(f"Froze {{len(frozen)}} Inspire Hand joints at neutral position.")
"""


async def _freeze_inspire_hand(robot_prim_path: str) -> Dict[str, Any]:
    """Run the hand-freeze script via Kit RPC before launching the policy."""
    try:
        from . import kit_tools
        script = _build_freeze_hand_script(robot_prim_path)
        result = await kit_tools.exec_sync(script)
        if result and result.get("success"):
            output = result.get("output", "").strip()
            logger.info(f"[RLRunner] Hand freeze: {output}")
            return {"frozen": True, "output": output}
        else:
            logger.warning(f"[RLRunner] Hand freeze failed: {result}")
            return {"frozen": False, "error": str(result)}
    except Exception as e:
        logger.warning(f"[RLRunner] Hand freeze exception: {e}")
        return {"frozen": False, "error": str(e)}


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
    robot_prim_path = args.get("robot_prim_path", "/World/G1")
    freeze_hand = args.get("freeze_hand", True)  # default True for G1+Inspire stability

    # Kill any existing policy
    await _stop_policy()

    # Freeze Inspire Hand joints before starting the locomotion policy.
    # The motion.pt checkpoint only controls 12 leg DOFs; uncontrolled hand
    # joints destabilize the robot at spawn due to free-floating mass.
    freeze_result = {}
    if freeze_hand:
        freeze_result = await _freeze_inspire_hand(robot_prim_path)
        if not freeze_result.get("frozen"):
            logger.warning("[RLRunner] Could not freeze hand joints — robot may be unstable")

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
        "hand_joints_frozen": freeze_result.get("frozen", False),
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
