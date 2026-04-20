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

# ── G1 upper-body joint targets for stable locomotion CG ────────────────────
# The motion.pt checkpoint controls only the 12 leg DOFs. Arms and hands must
# be held in a natural down position — arms at the sides keeps the CG centred
# over the feet. Raised or outstretched arms shift the CG forward/sideways and
# cause falls even if the hand joints are individually locked.
#
# Target positions are in radians; values match the G1's neutral standing pose
# used during unitree_rl_gym training.
#
# PhysX reads DriveAPI attributes at simulation start (not mid-step), so the
# simulation MUST be stopped before applying these — the freeze script calls
# omni.timeline.get_timeline_interface().stop() first, then the user presses Play.

_ARM_JOINT_TARGETS = {
    # ── Left arm (natural hang, elbow slightly bent) ──
    "left_shoulder_pitch_joint":  0.20,   # slight forward lean keeps arm away from torso
    "left_shoulder_roll_joint":   0.15,   # slight abduction
    "left_shoulder_yaw_joint":    0.00,
    "left_elbow_pitch_joint":     0.40,   # 23° bend — natural hang
    "left_wrist_roll_joint":      0.00,
    "left_wrist_pitch_joint":     0.00,
    "left_wrist_yaw_joint":       0.00,
    # ── Right arm (mirror of left) ──
    "right_shoulder_pitch_joint":  0.20,
    "right_shoulder_roll_joint":  -0.15,
    "right_shoulder_yaw_joint":    0.00,
    "right_elbow_pitch_joint":     0.40,
    "right_wrist_roll_joint":      0.00,
    "right_wrist_pitch_joint":     0.00,
    "right_wrist_yaw_joint":       0.00,
}

# Inspire Hand — all finger joints locked at 0 (open/neutral)
_INSPIRE_HAND_JOINTS = [
    "left_hand_j1",  "left_hand_j2",  "left_hand_j3",  "left_hand_j4",
    "left_hand_j5",  "left_hand_j6",  "left_hand_j7",  "left_hand_j8",
    "left_hand_j9",  "left_hand_j10", "left_hand_j11", "left_hand_j12",
    "right_hand_j1", "right_hand_j2", "right_hand_j3", "right_hand_j4",
    "right_hand_j5", "right_hand_j6", "right_hand_j7", "right_hand_j8",
    "right_hand_j9", "right_hand_j10","right_hand_j11","right_hand_j12",
]

_ARM_STIFFNESS  = 300.0   # Nm/rad — firm but not rigid (allows small sway)
_ARM_DAMPING    = 30.0    # Nms/rad
_HAND_STIFFNESS = 500.0   # Nm/rad — stiff (fingers don't need to move)
_HAND_DAMPING   = 50.0    # Nms/rad

# ── Singleton process registry ──────────────────────────────────────────────
_policy_proc: Optional[asyncio.subprocess.Process] = None
_policy_task_name: Optional[str] = None

# G1 locomotion demo script (keyboard-controlled, downloads pretrained checkpoint from Nucleus)
_G1_DEMO_SCRIPT = "scripts/demos/g1_locomotion.py"
# RSL-RL play script — used when the demo script isn't present.
# Pass --use_pretrained_checkpoint to auto-download from NVIDIA Nucleus on first run.
_PLAY_SCRIPT = "scripts/reinforcement_learning/rsl_rl/play.py"


def _find_isaaclab(hint: Optional[str] = None) -> Optional[str]:
    """Locate the isaaclab.sh launcher."""
    candidates = []
    # 1. Explicit hint (from tool arg or ISAACLAB_PATH env var)
    env_hint = os.environ.get("ISAACLAB_PATH", "")
    for h in [hint, env_hint]:
        if h:
            p = Path(h)
            candidates.append(p / "isaaclab.sh" if p.suffix != ".sh" else p)
    # 2. Known project location
    candidates += [
        Path.home() / "Documents/Github/open_arm_10Things/IsaacLab/isaaclab.sh",
        Path.home() / "IsaacLab" / "isaaclab.sh",
        Path.home() / "isaac-lab" / "isaaclab.sh",
        Path("/opt/IsaacLab/isaaclab.sh"),
    ]
    for p in candidates:
        if p.exists():
            return str(p)
    return shutil.which("isaaclab.sh")


def _build_freeze_upper_body_script(robot_prim_path: str) -> str:
    """
    Generate a Kit script that:
      1. Stops the simulation (PhysX only reads DriveAPI at sim-start)
      2. Sets arm joints to natural down-at-sides pose (correct CG for locomotion)
      3. Locks all Inspire Hand joints at neutral (open hand)
    The user must press Play after this runs.
    """
    arm_targets_repr  = repr(_ARM_JOINT_TARGETS)
    hand_joints_repr  = repr(_INSPIRE_HAND_JOINTS)
    return f"""\
import omni.usd
import omni.timeline
import math
from pxr import UsdPhysics, Gf

# 1. Stop sim — PhysX reads DriveAPI attributes at simulation start only
timeline = omni.timeline.get_timeline_interface()
was_playing = timeline.is_playing()
if was_playing:
    timeline.stop()

stage = omni.usd.get_context().get_stage()

arm_targets  = {arm_targets_repr}   # joint_name -> target_rad
hand_joints  = {hand_joints_repr}
arm_stiffness  = {_ARM_STIFFNESS}
arm_damping    = {_ARM_DAMPING}
hand_stiffness = {_HAND_STIFFNESS}
hand_damping   = {_HAND_DAMPING}

arm_frozen, hand_frozen, skipped = [], [], []

for prim in stage.Traverse():
    name = prim.GetPath().name
    if not prim.IsA(UsdPhysics.RevoluteJoint):
        skipped.append(name)
        continue

    drive = UsdPhysics.DriveAPI.Apply(prim, "angular")

    if name in arm_targets:
        target_deg = math.degrees(arm_targets[name])
        drive.GetStiffnessAttr().Set(arm_stiffness)
        drive.GetDampingAttr().Set(arm_damping)
        drive.GetTargetPositionAttr().Set(target_deg)
        arm_frozen.append(name)

    elif name in hand_joints:
        drive.GetStiffnessAttr().Set(hand_stiffness)
        drive.GetDampingAttr().Set(hand_damping)
        drive.GetTargetPositionAttr().Set(0.0)
        hand_frozen.append(name)

print(f"Arms set: {{len(arm_frozen)}}, hands frozen: {{len(hand_frozen)}}")
print(f"Sim was playing: {{was_playing}} — press Play to restart with new drives.")
"""


async def _freeze_upper_body(robot_prim_path: str) -> Dict[str, Any]:
    """
    Stop the sim, set arm joints to down-at-sides pose, lock hand joints.
    Kit RPC (exec_sync) runs synchronously inside Isaac Sim Python.
    PhysX only reads DriveAPI on sim-start, so the script stops first.
    """
    try:
        from . import kit_tools
        script = _build_freeze_upper_body_script(robot_prim_path)
        result = await kit_tools.exec_sync(script)
        if result and result.get("success"):
            output = result.get("output", "").strip()
            logger.info(f"[RLRunner] Upper-body freeze: {output}")
            return {"frozen": True, "output": output, "restart_required": True}
        else:
            logger.warning(f"[RLRunner] Upper-body freeze failed: {result}")
            return {"frozen": False, "error": str(result)}
    except Exception as e:
        logger.warning(f"[RLRunner] Upper-body freeze exception: {e}")
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

    # Stop sim + set arms down + lock hands before starting the locomotion policy.
    # PhysX only reads DriveAPI on sim-start, so the sim must be stopped first.
    # Arms-at-sides keeps the CG centred over the feet (critical for stability).
    freeze_result = {}
    if freeze_hand:
        freeze_result = await _freeze_upper_body(robot_prim_path)
        if not freeze_result.get("frozen"):
            logger.warning("[RLRunner] Could not freeze upper body — robot may be unstable")

    isaaclab_sh = _find_isaaclab(isaaclab_path or None)
    if not isaaclab_sh:
        return {
            "error": (
                "isaaclab.sh not found. Set isaaclab_path or ensure IsaacLab is at ~/IsaacLab. "
                "Install: git clone https://github.com/isaac-sim/IsaacLab && cd IsaacLab && ./isaaclab.sh --install"
            )
        }

    isaaclab_root = str(Path(isaaclab_sh).parent)

    # Prefer the G1 keyboard demo; fall back to rsl_rl/play.py + auto-download
    demo_script = str(Path(isaaclab_root) / _G1_DEMO_SCRIPT)
    play_script = str(Path(isaaclab_root) / _PLAY_SCRIPT)

    if os.path.exists(demo_script):
        # Full interactive demo: keyboard control + pretrained checkpoint download
        cmd = [isaaclab_sh, "-p", demo_script, "--num_envs", str(num_envs)]
        if checkpoint:
            cmd += ["--checkpoint", checkpoint]
        using_demo = True
    elif os.path.exists(play_script):
        # Headless play script: --use_pretrained_checkpoint downloads from Nucleus
        cmd = [isaaclab_sh, "-p", play_script, "--task", task, "--num_envs", str(num_envs)]
        if checkpoint:
            cmd += ["--checkpoint", checkpoint]
        else:
            cmd += ["--use_pretrained_checkpoint"]
        using_demo = False
        teleop_device = None
        logger.info("[RLRunner] Using rsl_rl/play.py with --use_pretrained_checkpoint (no keyboard control)")
    else:
        return {
            "error": (
                f"No locomotion script found under {isaaclab_root}. "
                f"Expected: {demo_script} or {play_script}"
            )
        }

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
        "checkpoint": checkpoint or "NVIDIA Nucleus pretrained (auto-downloaded on first run)",
        "num_envs": num_envs,
        "isaaclab_root": isaaclab_root,
        "hand_joints_frozen": freeze_result.get("frozen", False),
        "script": "g1_locomotion.py (keyboard demo)" if using_demo else "rsl_rl/play.py (autonomous)",
    }

    if using_demo:
        result["keyboard_controls"] = {
            "Click robot": "select it for keyboard control",
            "UP / DOWN":   "forward / stop",
            "LEFT / RIGHT": "turn left / right",
            "C": "toggle third-person camera",
        }

    if freeze_result.get("restart_required"):
        result["action_required"] = (
            "Simulation was stopped to apply arm/hand joint drives. "
            "Press Play in Isaac Sim to restart — arms will hold at sides, "
            "hands locked open. Then the policy subprocess will control the legs."
        )
    else:
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
