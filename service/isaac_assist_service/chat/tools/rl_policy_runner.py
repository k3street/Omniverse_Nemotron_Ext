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
import json
import shutil
import signal
from pathlib import Path
from typing import Any, Dict, Optional

from ...config import config

logger = logging.getLogger(__name__)

# Absolute path to our bundled G1 demo script (lives in this repo, not in external IsaacLab)
_OWN_SCRIPTS_DIR = Path(__file__).parent.parent.parent.parent.parent / "scripts" / "locomotion"

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
    # Values match G1_CFG.init_state.joint_pos from isaaclab_assets/robots/unitree.py —
    # the exact pose used during Isaac-Velocity-Flat-G1-v0 training.
    # Elbow at 0.87 rad (50°) is the relaxed hang angle for the G1 arm geometry;
    # shoulder pitch 0.35 brings the arm slightly forward so the hand clears the thigh.
    "left_shoulder_pitch_joint":  0.35,
    "left_shoulder_roll_joint":   0.16,
    "left_shoulder_yaw_joint":    0.00,
    "left_elbow_pitch_joint":     0.87,   # legacy naming
    "left_elbow_joint":           0.87,   # G1 USD naming in scene
    "left_wrist_roll_joint":      0.00,
    "left_wrist_pitch_joint":     0.00,
    "left_wrist_yaw_joint":       0.00,
    # ── Right arm (mirror) ──
    "right_shoulder_pitch_joint": 0.35,
    "right_shoulder_roll_joint": -0.16,
    "right_shoulder_yaw_joint":   0.00,
    "right_elbow_pitch_joint":    0.87,   # legacy naming
    "right_elbow_joint":          0.87,   # G1 USD naming in scene
    "right_wrist_roll_joint":     0.00,
    "right_wrist_pitch_joint":    0.00,
    "right_wrist_yaw_joint":      0.00,
}

# Inspire Hand — all finger joints locked at 0 (open/neutral)
_INSPIRE_HAND_JOINTS = [
    "left_hand_j1",  "left_hand_j2",  "left_hand_j3",  "left_hand_j4",
    "left_hand_j5",  "left_hand_j6",  "left_hand_j7",  "left_hand_j8",
    "left_hand_j9",  "left_hand_j10", "left_hand_j11", "left_hand_j12",
    "right_hand_j1", "right_hand_j2", "right_hand_j3", "right_hand_j4",
    "right_hand_j5", "right_hand_j6", "right_hand_j7", "right_hand_j8",
    "right_hand_j9", "right_hand_j10","right_hand_j11","right_hand_j12",
    # G1 Inspire hand naming in current USD assets
    "L_index_proximal_joint", "L_index_intermediate_joint",
    "L_middle_proximal_joint", "L_middle_intermediate_joint",
    "L_ring_proximal_joint", "L_ring_intermediate_joint",
    "L_pinky_proximal_joint", "L_pinky_intermediate_joint",
    "L_thumb_proximal_yaw_joint", "L_thumb_proximal_pitch_joint",
    "L_thumb_intermediate_joint", "L_thumb_distal_joint",
    "R_index_proximal_joint", "R_index_intermediate_joint",
    "R_middle_proximal_joint", "R_middle_intermediate_joint",
    "R_ring_proximal_joint", "R_ring_intermediate_joint",
    "R_pinky_proximal_joint", "R_pinky_intermediate_joint",
    "R_thumb_proximal_yaw_joint", "R_thumb_proximal_pitch_joint",
    "R_thumb_intermediate_joint", "R_thumb_distal_joint",
]

# ── G1 leg joint targets for standing pose ───────────────────────────────────
# From IsaacLab Isaac-Velocity-Flat-G1-v0 init_state.joint_pos (unitree.py).
# These are the leg angles the robot holds at static balance — bent knees,
# hip pitched forward, ankle plantar-flexed. Straight legs (0°) → immediate collapse.
_LEG_JOINT_TARGETS = {
    "left_hip_pitch_joint":    -0.28,   # ~-16° forward hip
    "left_hip_roll_joint":      0.00,
    "left_hip_yaw_joint":       0.00,
    "left_knee_joint":          0.79,   # ~45° bent knee — critical for balance
    "left_ankle_pitch_joint":  -0.52,   # ~-30° plantar flexion
    "left_ankle_roll_joint":    0.00,
    "right_hip_pitch_joint":   -0.28,
    "right_hip_roll_joint":     0.00,
    "right_hip_yaw_joint":      0.00,
    "right_knee_joint":         0.79,
    "right_ankle_pitch_joint": -0.52,
    "right_ankle_roll_joint":   0.00,
}

_ARM_STIFFNESS   = 800.0   # Nm/rad — stiff enough to hold arm against gravity
_ARM_DAMPING     = 80.0    # Nms/rad — sufficient braking to prevent overshoot
_ARM_MAX_FORCE   = 1000.0  # Nm  — override USD default (25 Nm) which is too weak
_HAND_STIFFNESS  = 500.0   # Nm/rad — stiff (fingers don't need to move)
_HAND_DAMPING    = 50.0    # Nms/rad
_HAND_MAX_FORCE  = 200.0   # Nm  — override USD default wrist maxForce (5 Nm)
# Leg PD gains match IsaacLab G1 actuator group config (unitree.py)
# Hip/knee use high-torque actuators; ankles use smaller actuators
_LEG_HIP_STIFFNESS    = 150.0   # Nm/rad
_LEG_HIP_DAMPING      =   5.0   # Nms/rad
_LEG_KNEE_STIFFNESS   = 200.0   # Nm/rad
_LEG_KNEE_DAMPING     =   5.0   # Nms/rad
_LEG_ANKLE_STIFFNESS  =  40.0   # Nm/rad
_LEG_ANKLE_DAMPING    =   2.0   # Nms/rad

# ── Singleton process registry ──────────────────────────────────────────────
_policy_proc: Optional[asyncio.subprocess.Process] = None
_sim_proc: Optional[asyncio.subprocess.Process] = None   # groot_wbc MuJoCo sim loop
_policy_task_name: Optional[str] = None

# RSL-RL play script — generic, works for any IsaacLab locomotion task.
# Adds --use_pretrained_checkpoint to auto-download from NVIDIA Nucleus when no checkpoint given.
_PLAY_SCRIPT = "scripts/reinforcement_learning/rsl_rl/play.py"
# G1-specific keyboard demo (better UX but G1-only; falls back to _PLAY_SCRIPT if absent)
_G1_DEMO_SCRIPT = "scripts/demos/g1_locomotion.py"

# Known robot → IsaacLab task mappings (flat preferred: simpler, same checkpoint source)
# These all support --use_pretrained_checkpoint via NVIDIA Nucleus.
_ROBOT_TASK_MAP: Dict[str, str] = {
    # Unitree G1
    "g1":  "Isaac-Velocity-Flat-G1-v0",
    # Unitree H1 / H1-2
    "h1":  "Isaac-Velocity-Rough-H1-v0",
    "h1_2": "Isaac-Velocity-Rough-H1-2-v0",
    # Boston Dynamics Spot
    "spot": "Isaac-Velocity-Flat-Spot-v0",
    # Anybotics ANYmal C / D
    "anymal_c": "Isaac-Velocity-Rough-Anymal-C-v0",
    "anymal_d": "Isaac-Velocity-Rough-Anymal-D-v0",
    # MIT Cheetah / Unitree A1 / Go1 / Go2
    "a1":  "Isaac-Velocity-Flat-Unitree-A1-v0",
    "go1": "Isaac-Velocity-Flat-Unitree-Go1-v0",
    "go2": "Isaac-Velocity-Flat-Unitree-Go2-v0",
}


def _find_groot_wbc(hint: Optional[str] = None) -> Optional[Path]:
    """Locate the GR00T-WholeBodyControl repo root (must contain gear_sonic/scripts/run_sim_loop.py)."""
    candidates: list[Path] = []
    for h in [hint, os.environ.get("GROOT_WBC_PATH", "")]:
        if h:
            candidates.append(Path(h))
    candidates += [
        Path.home() / "GR00T-WholeBodyControl",
        Path.home() / "Documents" / "Github" / "GR00T-WholeBodyControl",
        Path("/opt/GR00T-WholeBodyControl"),
    ]
    for p in candidates:
        if (p / "gear_sonic" / "scripts" / "run_sim_loop.py").exists():
            return p
    return None


async def _ensure_groot_checkpoint(repo_root: Path) -> Dict[str, Any]:
    """Download nvidia/GEAR-SONIC checkpoint from HuggingFace if not already present."""
    checkpoint_file = repo_root / "sonic_release" / "last.pt"
    if checkpoint_file.exists():
        return {"downloaded": False, "path": str(checkpoint_file)}
    logger.info("[GR00T] Downloading nvidia/GEAR-SONIC checkpoint from HuggingFace…")
    try:
        proc = await asyncio.create_subprocess_exec(
            "python", "-c",
            (
                "from huggingface_hub import snapshot_download; "
                f"snapshot_download('nvidia/GEAR-SONIC', local_dir='{repo_root / 'sonic_release'}')"
            ),
            cwd=str(repo_root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=600)
        if proc.returncode == 0 and checkpoint_file.exists():
            return {"downloaded": True, "path": str(checkpoint_file)}
        return {"downloaded": False, "error": stderr.decode()[:500]}
    except asyncio.TimeoutError:
        return {"downloaded": False, "error": "Checkpoint download timed out (10 min)"}
    except Exception as e:
        return {"downloaded": False, "error": str(e)}


def _find_isaaclab(hint: Optional[str] = None) -> Optional[str]:
    """Locate the isaaclab.sh launcher.

    Priority: tool arg → config.isaaclab_path → ISAACLAB_PATH env → known paths → PATH.
    """
    candidates = []
    for h in [hint, config.isaaclab_path, os.environ.get("ISAACLAB_PATH", "")]:
        if h:
            p = Path(h)
            candidates.append(p / "isaaclab.sh" if p.suffix != ".sh" else p)
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
      1. Stops the simulation so PhysX reinitializes with updated drive params
      2. Sets arm joints to natural down-at-sides pose (correct CG for locomotion)
      3. Sets LEG joints to G1 nominal standing pose (bent knees, hip pitched fwd)
         — without this the legs are limp at 0° and the robot immediately collapses
      4. Locks all Inspire Hand joints at neutral (open hand)
      5. Sets maxForce high enough for arms (USD default 25 Nm is too weak)
      6. Pre-positions the robot by stepping physics N frames at extreme stiffness
         so that joints snap to target before the caller sees "play" — eliminates
         the initial transient jerk from starting at 0°
      7. Restores normal stiffness/damping then leaves simulation playing
    """
    arm_targets_repr  = repr(_ARM_JOINT_TARGETS)
    leg_targets_repr  = repr(_LEG_JOINT_TARGETS)
    hand_joints_repr  = repr(_INSPIRE_HAND_JOINTS)
    robot_root_repr   = repr(robot_prim_path)
    return f"""\
import omni.usd
import omni.timeline
import math
from pxr import UsdPhysics

timeline = omni.timeline.get_timeline_interface()
timeline.stop()

stage = omni.usd.get_context().get_stage()
robot_root = {robot_root_repr}

arm_targets  = {arm_targets_repr}
leg_targets  = {leg_targets_repr}
hand_joints  = {hand_joints_repr}

# --- Drive constants ---
ARM_K   = {_ARM_STIFFNESS};  ARM_D   = {_ARM_DAMPING};  ARM_F   = {_ARM_MAX_FORCE}
HAND_K  = {_HAND_STIFFNESS}; HAND_D  = {_HAND_DAMPING}; HAND_F  = {_HAND_MAX_FORCE}
HIP_K   = {_LEG_HIP_STIFFNESS};   HIP_D   = {_LEG_HIP_DAMPING}
KNEE_K  = {_LEG_KNEE_STIFFNESS};  KNEE_D  = {_LEG_KNEE_DAMPING}
ANK_K   = {_LEG_ANKLE_STIFFNESS}; ANK_D   = {_LEG_ANKLE_DAMPING}

arm_frozen, leg_frozen, hand_frozen = [], [], []

for prim in stage.Traverse():
    prim_path = str(prim.GetPath())
    if not prim_path.startswith(robot_root + "/"):
        continue
    name = prim.GetPath().name
    if not prim.IsA(UsdPhysics.RevoluteJoint):
        continue

    drive = UsdPhysics.DriveAPI.Apply(prim, "angular")

    if name in arm_targets:
        drive.GetStiffnessAttr().Set(ARM_K)
        drive.GetDampingAttr().Set(ARM_D)
        drive.GetTargetPositionAttr().Set(math.degrees(arm_targets[name]))
        drive.GetMaxForceAttr().Set(ARM_F)
        arm_frozen.append(name)

    elif name in leg_targets:
        # Choose PD gains by joint type
        if "knee" in name:
            K, D = KNEE_K, KNEE_D
        elif "ankle" in name:
            K, D = ANK_K, ANK_D
        else:
            K, D = HIP_K, HIP_D
        drive.GetStiffnessAttr().Set(K)
        drive.GetDampingAttr().Set(D)
        drive.GetTargetPositionAttr().Set(math.degrees(leg_targets[name]))
        # Keep USD maxForce for legs — it already matches hardware limits
        leg_frozen.append(name)

    elif name in hand_joints:
        drive.GetStiffnessAttr().Set(HAND_K)
        drive.GetDampingAttr().Set(HAND_D)
        drive.GetTargetPositionAttr().Set(0.0)
        drive.GetMaxForceAttr().Set(HAND_F)
        hand_frozen.append(name)

print(f"Arms set: {{len(arm_frozen)}}, legs set: {{len(leg_frozen)}}, hands frozen: {{len(hand_frozen)}}")

# Pre-position: step physics at extreme stiffness so joints snap to target
# before play — robot starts at the standing pose, not at 0°.
# We temporarily boost stiffness × 20 so each joint reaches target in < 2 frames.
all_frozen = set(arm_frozen) | set(leg_frozen)
drive_prims = []
for prim in stage.Traverse():
    if not str(prim.GetPath()).startswith(robot_root + "/"):
        continue
    name = prim.GetPath().name
    if name in all_frozen and prim.IsA(UsdPhysics.RevoluteJoint):
        d = UsdPhysics.DriveAPI(prim, "angular")
        if name in arm_targets:
            d.GetStiffnessAttr().Set(ARM_K * 20)
        elif "knee" in name:
            d.GetStiffnessAttr().Set(KNEE_K * 20)
        elif "ankle" in name:
            d.GetStiffnessAttr().Set(ANK_K * 20)
        else:
            d.GetStiffnessAttr().Set(HIP_K * 20)
        drive_prims.append((name, d))

# Run 5 physics steps to snap joints to target
# Isaac Sim 5.x: simulate(elapsed_time, current_time), fetch_results()
try:
    import omni.physx as _px
    sim = _px.get_physx_simulation_interface()
    dt = 1.0 / 60.0
    for i in range(5):
        sim.simulate(dt, i * dt)
        sim.fetch_results()
    print("Pre-position: 5 physics steps applied.")
except Exception as e:
    print(f"Pre-position step skipped ({{e}}) — drives will snap on first play frame.")

# Restore normal stiffness
for name, d in drive_prims:
    if name in arm_targets:
        d.GetStiffnessAttr().Set(ARM_K)
    elif "knee" in name:
        d.GetStiffnessAttr().Set(KNEE_K)
    elif "ankle" in name:
        d.GetStiffnessAttr().Set(ANK_K)
    else:
        d.GetStiffnessAttr().Set(HIP_K)

timeline.play()
print("Simulation playing. Robot initialized at standing pose.")
"""


async def _freeze_upper_body(robot_prim_path: str) -> Dict[str, Any]:
    """
    Stop the sim, set arm joints to down-at-sides pose with high-force drives,
    lock all Inspire Hand joints at neutral, then auto-restart simulation.
    PhysX only reads DriveAPI.maxForce at sim-start, so we must stop and replay.
    """
    try:
        from . import kit_tools
        script = _build_freeze_upper_body_script(robot_prim_path)
        result = await kit_tools.exec_sync(script, timeout=15)
        if result and result.get("success"):
            output = result.get("output", "").strip()
            logger.info(f"[RLRunner] Upper-body freeze: {output}")
            return {"frozen": True, "output": output, "restart_required": False}
        else:
            logger.warning(f"[RLRunner] Upper-body freeze failed: {result}")
            return {"frozen": False, "error": str(result)}
    except Exception as e:
        logger.warning(f"[RLRunner] Upper-body freeze exception: {e}")
        return {"frozen": False, "error": str(e)}


async def _probe_in_scene_policy_runtime() -> Dict[str, Any]:
    """
    Check whether the running Isaac Sim process can execute Isaac Lab policy
    dependencies in-process (required for true existing-scene policy injection).
    """
    try:
        from . import kit_tools

        script = """\
import json
mods = ['isaaclab', 'isaaclab_tasks', 'rsl_rl', 'torch']
status = {}
for m in mods:
    try:
        __import__(m)
        status[m] = True
    except Exception:
        status[m] = False
print(json.dumps(status))
"""
        result = await kit_tools.exec_sync(script, timeout=20)
        if not result.get("success"):
            return {
                "supported": False,
                "reason": f"Kit exec_sync failed: {result.get('output', 'unknown error')}",
                "modules": {},
            }

        raw = (result.get("output") or "").strip().splitlines()
        payload = raw[-1] if raw else "{}"
        modules = json.loads(payload)
        required = ["isaaclab", "isaaclab_tasks", "rsl_rl"]
        missing = [m for m in required if not modules.get(m, False)]
        if missing:
            return {
                "supported": False,
                "reason": f"Current Isaac Sim runtime is missing modules: {', '.join(missing)}",
                "modules": modules,
            }

        return {"supported": True, "reason": "ok", "modules": modules}
    except Exception as e:
        return {"supported": False, "reason": str(e), "modules": {}}


async def _handle_deploy_groot_wbc(args: Dict[str, Any]) -> Dict[str, Any]:
    """Launch GR00T-WholeBodyControl sim2sim: MuJoCo sim loop + C++ deploy stack."""
    global _policy_proc, _sim_proc, _policy_task_name

    groot_wbc_path = args.get("groot_wbc_path", "")
    input_type = str(args.get("groot_wbc_input_type", "keyboard")).strip().lower()

    repo_root = _find_groot_wbc(groot_wbc_path or None)
    if not repo_root:
        return {
            "error": (
                "GR00T-WholeBodyControl repo not found. "
                "Clone and set up: git clone https://github.com/NVlabs/GR00T-WholeBodyControl "
                "&& cd GR00T-WholeBodyControl && git lfs pull "
                "&& bash install_scripts/install_mujoco_sim.sh "
                "Then pass groot_wbc_path='/path/to/GR00T-WholeBodyControl' or set GROOT_WBC_PATH."
            )
        }

    deploy_dir = repo_root / "gear_sonic_deploy"
    deploy_sh = deploy_dir / "deploy.sh"
    if not deploy_sh.exists():
        return {
            "error": (
                f"gear_sonic_deploy/deploy.sh not found under {repo_root}. "
                "Run: git lfs pull  (binary assets require Git LFS)"
            )
        }

    await _stop_policy()

    ckpt_info = await _ensure_groot_checkpoint(repo_root)

    # Process 1 — MuJoCo physics server
    sim_script = repo_root / "gear_sonic" / "scripts" / "run_sim_loop.py"
    venv_python = repo_root / ".venv_sim" / "bin" / "python"
    python_exe = str(venv_python) if venv_python.exists() else "python"

    try:
        _sim_proc = await asyncio.create_subprocess_exec(
            python_exe, str(sim_script),
            cwd=str(repo_root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        logger.info(f"[GR00T] MuJoCo sim loop PID {_sim_proc.pid}")
    except Exception as e:
        return {"error": f"Failed to launch run_sim_loop.py: {e}"}

    # Give MuJoCo sim a moment to bind its socket before the C++ deploy connects
    await asyncio.sleep(2)

    # Process 2 — C++ deploy (policy + kinematic planner)
    try:
        _policy_proc = await asyncio.create_subprocess_exec(
            "bash", str(deploy_sh), "--input-type", input_type, "sim",
            cwd=str(deploy_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _policy_task_name = "groot_wbc"
        logger.info(f"[GR00T] deploy.sh PID {_policy_proc.pid}")
    except Exception as e:
        _sim_proc.kill()
        _sim_proc = None
        return {"error": f"Failed to launch deploy.sh: {e}"}

    result: Dict[str, Any] = {
        "status": "groot_wbc_launched",
        "controller": "GEAR-SONIC (GR00T Whole-Body Control)",
        "sim_pid": _sim_proc.pid,
        "deploy_pid": _policy_proc.pid,
        "repo": str(repo_root),
        "input_type": input_type,
        "checkpoint": ckpt_info,
        "zmq_command_port": 5556,
        "zmq_viz_port": 5557,
        "architecture": (
            "Decoupled WBC — RL lower-body locomotion (12 DOF) + IK upper-body (11 DOF). "
            "GEAR-SONIC trained on 142K human motions (288h). "
            "Two-process sim2sim: MuJoCo physics server + C++ policy/kinematic-planner."
        ),
    }
    if input_type == "keyboard":
        result["keyboard_controls"] = {
            "]": "start policy",
            "9 (MuJoCo viewer)": "drop robot to ground",
            "T": "play reference motion",
            "N / P": "next / previous motion",
            "O": "emergency stop",
        }
        result["note"] = (
            "Two windows open. Focus the MuJoCo window, press ] to start, "
            "then press 9 to drop the robot. Use stop_rl_policy to terminate both processes."
        )
    elif input_type == "zmq_manager":
        result["note"] = (
            f"Listening for ZMQ pose commands on port 5556 (topic: 'pose'). "
            "Send Protocol v1: {joint_pos: [29], joint_vel: [29], body_quat: [...], frame_index: N}"
        )
    return result


async def handle_deploy_rl_policy(args: Dict[str, Any]) -> Dict[str, Any]:
    """
    Tool handler for deploy_rl_policy.

    1. Locate isaaclab.sh
    2. Build the subprocess command
    3. Launch as background process
    4. Return PID + keyboard cheat-sheet
    """
    global _policy_proc, _policy_task_name

    if str(args.get("controller_type", "isaaclab")).strip().lower() == "groot_wbc":
        return await _handle_deploy_groot_wbc(args)

    robot_name = args.get("robot_name", "g1").lower().replace("-", "_").replace(" ", "_")
    task = args.get("task", "") or _ROBOT_TASK_MAP.get(robot_name, "Isaac-Velocity-Flat-G1-v0")
    checkpoint = args.get("checkpoint", "")
    num_envs = int(args.get("num_envs", 1))
    isaaclab_path = args.get("isaaclab_path", "")
    robot_prim_path = args.get("robot_prim_path", "/World/G1")
    freeze_hand = args.get("freeze_hand", True)
    deployment_mode = str(args.get("deployment_mode", "separate_window")).strip().lower()
    fallback_to_separate_window = bool(args.get("fallback_to_separate_window", True))

    if deployment_mode not in {"separate_window", "existing_scene"}:
        return {
            "error": (
                "Invalid deployment_mode. Expected 'separate_window' or 'existing_scene'."
            )
        }

    in_scene_probe: Dict[str, Any] = {}
    if deployment_mode == "existing_scene":
        in_scene_probe = await _probe_in_scene_policy_runtime()
        if not in_scene_probe.get("supported"):
            msg = (
                "In-scene RL policy injection is not available in the current Isaac Sim runtime: "
                f"{in_scene_probe.get('reason', 'unknown reason')}"
            )
            if not fallback_to_separate_window:
                return {
                    "status": "in_scene_unavailable",
                    "error": msg,
                    "requested_mode": "existing_scene",
                    "fallback_available": "separate_window",
                    "modules": in_scene_probe.get("modules", {}),
                }
            logger.warning(f"[RLRunner] {msg}. Falling back to separate_window mode.")

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

    play_script = str(Path(isaaclab_root) / _PLAY_SCRIPT)
    # Demo script lives in our repo (not in external IsaacLab)
    demo_script = str(_OWN_SCRIPTS_DIR / "g1_locomotion.py")

    is_g1_flat = task == "Isaac-Velocity-Flat-G1-v0"
    if is_g1_flat and os.path.exists(demo_script):
        cmd = [isaaclab_sh, "-p", demo_script, "--num_envs", str(num_envs)]
        if checkpoint:
            cmd += ["--checkpoint", checkpoint]
        using_demo = True
    elif os.path.exists(play_script):
        # Generic path: works for any IsaacLab task with --use_pretrained_checkpoint
        cmd = [isaaclab_sh, "-p", play_script, "--task", task, "--num_envs", str(num_envs)]
        if checkpoint:
            cmd += ["--checkpoint", checkpoint]
        else:
            cmd += ["--use_pretrained_checkpoint"]
        using_demo = False
        logger.info(f"[RLRunner] Using rsl_rl/play.py for task={task}")
    else:
        return {
            "error": (
                f"rsl_rl/play.py not found under {isaaclab_root}. "
                "Ensure Isaac Lab 2.3+ is installed."
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
        "robot": robot_name,
        "checkpoint": checkpoint or "NVIDIA Nucleus pretrained (auto-downloaded on first run)",
        "num_envs": num_envs,
        "isaaclab_root": isaaclab_root,
        "hand_joints_frozen": freeze_result.get("frozen", False),
        "script": "g1_locomotion.py (keyboard demo)" if using_demo else f"rsl_rl/play.py --task {task}",
        "requested_mode": deployment_mode,
        "effective_mode": "separate_window",
        "architecture_note": (
            "This opens a SEPARATE Isaac Sim window with its own physics environment. "
            "It does NOT inject a policy into your existing scene. "
            "To see the robot walk: interact with the new Isaac Sim window that opens, "
            "click a robot to select it, then use arrow keys."
        ),
    }

    if deployment_mode == "existing_scene" and not in_scene_probe.get("supported"):
        result["fallback_used"] = True
        result["fallback_reason"] = in_scene_probe.get("reason")
        result["in_scene_runtime_modules"] = in_scene_probe.get("modules", {})

    if using_demo:
        result["keyboard_controls"] = {
            "Click robot in NEW window": "select for keyboard control",
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
    """Terminate managed policy subprocess(es). Returns primary PID or None."""
    global _policy_proc, _sim_proc, _policy_task_name
    if _policy_proc is None and _sim_proc is None:
        return None
    old_pid = _policy_proc.pid if _policy_proc else (_sim_proc.pid if _sim_proc else None)
    for proc in [_policy_proc, _sim_proc]:
        if proc is None:
            continue
        try:
            proc.send_signal(signal.SIGTERM)
            try:
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
            logger.info(f"[RLRunner] Stopped PID {proc.pid} ({_policy_task_name})")
        except ProcessLookupError:
            logger.info(f"[RLRunner] PID {proc.pid} already exited")
    _policy_proc = None
    _sim_proc = None
    _policy_task_name = None
    return old_pid
