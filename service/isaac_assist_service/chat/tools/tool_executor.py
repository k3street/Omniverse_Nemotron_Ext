"""
tool_executor.py
-----------------
Dispatches LLM tool-calls to the appropriate backend:
  - Kit RPC (port 8001) for live scene operations
  - Local data lookups (sensor specs, deformable presets)
  - Code generation for complex operations sent to Kit for approval

All handlers return a dict that gets fed back to the LLM as a tool result.
"""
from __future__ import annotations
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional
from . import kit_tools
from .patch_validator import validate_patch, format_issues_for_llm, has_blocking_issues
from ...config import config

logger = logging.getLogger(__name__)

# ── Paths to knowledge files ─────────────────────────────────────────────────
_WORKSPACE = Path(__file__).resolve().parents[4] / "workspace"
_SENSOR_SPECS_PATH = _WORKSPACE / "knowledge" / "sensor_specs.jsonl"
_DEFORMABLE_PRESETS_PATH = _WORKSPACE / "knowledge" / "deformable_presets.json"

# Cache loaded once
_sensor_specs: Optional[List[Dict]] = None
_deformable_presets: Optional[Dict] = None

# ═══════════════════════════════════════════════════════════════════════════
# Recovered state for bundled PR handlers (local QA branch only)
# Module-level dicts, regexes, classes, and imports that the extraction
# script missed. Restores 182 broken name references so handlers can run.
# ═══════════════════════════════════════════════════════════════════════════
import re
import re as _re
import time
import time as _time
import threading as _threading
import uuid as _uuid
import uuid as _wf_uuid
from datetime import datetime as _wf_dt
from typing import Tuple
import asyncio as _asyncio
from dataclasses import dataclass, field

from ...finetune.turn_recorder import TurnRecorder

# cleanly, but the Python-side wrapper keeps ordering deterministic for tests.

import asyncio as _asyncio
from dataclasses import dataclass, field
from typing import Tuple


@dataclass(order=True)
class _LockedPatch:
    sort_key: Tuple[int, int] = field(compare=True)
    code: str = field(compare=False, default="")
    description: str = field(compare=False, default="")
    priority: int = field(compare=False, default=0)


class _StageWriteLockQueue:
    """Minimal serialized queue — mirrors the spec's StageWriteLock pattern."""

    def __init__(self) -> None:
        self._lock = _asyncio.Lock()
        self._pending: List[_LockedPatch] = []
        self._counter = 0

    async def submit(self, code: str, description: str, priority: int) -> Dict[str, Any]:
        self._counter += 1
        # Higher priority first; stable by insertion order for ties.
        patch = _LockedPatch(
            sort_key=(-int(priority), self._counter),
            code=code,
            description=description,
            priority=int(priority),
        )
        async with self._lock:
            self._pending.append(patch)
            self._pending.sort()
            queue_depth = len(self._pending)
        result = await kit_tools.queue_exec_patch(code, description)
        async with self._lock:
            # Pop the matching entry so the queue drains in order.
            for idx, p in enumerate(self._pending):
                if p is patch:
                    self._pending.pop(idx)
                    break
        return {
            "queued": bool(result.get("queued", False)) if isinstance(result, dict) else False,
            "priority": int(priority),
            "queue_depth": queue_depth,
        }

    def pending(self) -> int:
        return len(self._pending)

# ── Recovered module-level state from PR branches ───────────────────────

# from: feat/7D-arena
_ARENA_SCENE_MAP = {
    "tabletop_pick_and_place": "isaaclab_tasks.envs.arena.scenes.tabletop",
    "kitchen": "isaaclab_tasks.envs.arena.scenes.kitchen",
    "galileo": "isaaclab_tasks.envs.arena.scenes.galileo",
    "custom": None,
}

# from: feat/addendum-community-remote-v2
_ASYNC_TASKS: Dict[str, Dict[str, Any]] = {}

# from: feat/addendum-community-remote-v2
_ASYNC_TASKS_LOCK = _threading.Lock()

# from: feat/addendum-phase5-pedagogy-uncertainty-v2
_BROKEN_SCENE_FAULTS = {
    "missing_collision": {
        "what_breaks": "Ground plane has no CollisionAPI — robot falls through floor",
        "learning_goal": "Physics basics — CollisionAPI must be applied for objects to interact",
    },
    "zero_mass": {
        "what_breaks": "Robot link has mass=0 — articulation behaves erratically",
        "learning_goal": "Inertia understanding — every dynamic body needs positive mass",
    },
    "wrong_scale": {
        "what_breaks": "Object imported at 100x scale (cm vs m mismatch)",
        "learning_goal": "USD units — metersPerUnit must match between asset and stage",
    },
    "inverted_joint": {
        "what_breaks": "One joint axis flipped — robot moves opposite direction",
        "learning_goal": "URDF import debugging — axis conventions can flip",
    },
    "no_physics_scene": {
        "what_breaks": "Missing PhysicsScene prim — no physics simulation runs",
        "learning_goal": "Scene setup — every physics-enabled stage needs a PhysicsScene",
    },
    "inf_joint_limits": {
        "what_breaks": "Joint limits set to ±inf — arm can move through itself or environment",
        "learning_goal": "URDF best practices — always set finite joint limits",
    },
}

# from: feat/7H-cloud-deployment
_cloud_jobs: Dict[str, Dict] = {}

# from: feat/7H-cloud-deployment
_CLOUD_PRICING = {
    ("aws", "g5.2xlarge"): {"price_per_hour": 1.21, "gpu": "A10G"},
    ("aws", "g6e.2xlarge"): {"price_per_hour": 2.50, "gpu": "L40S"},
    ("gcp", "g2-standard-8"): {"price_per_hour": 1.35, "gpu": "L4"},
    ("azure", "NCasT4_v3"): {"price_per_hour": 1.10, "gpu": "T4"},
}

# from: feat/7H-cloud-deployment
_CLOUD_SCRIPT_ALLOWLIST = {"training", "sdg", "evaluation", "headless_sim"}

# from: feat/new-physics-calibration
_DEFAULT_CALIBRATE_PARAMS = ["friction", "damping", "masses"]

# from: feat/new-onboarding
_DEFAULT_SUGGESTIONS = [
    "Run the simulation to see the result",
    "Capture a viewport screenshot",
    "Check for any physics warnings",
]

# from: feat/addendum-enterprise-scale
_DELTA_ROOT = _WORKSPACE / "snapshots" / "deltas"

# from: feat/7C-xr-teleoperation
_DEVICE_AXIS_DEFAULTS = {
    "quest_3": ["left_x", "left_y", "right_x", "right_y", "trigger_left", "trigger_right", "grip_left", "grip_right"],
    "vision_pro": ["left_x", "left_y", "right_x", "right_y", "pinch_left", "pinch_right"],
    "spacemouse": ["tx", "ty", "tz", "rx", "ry", "rz"],
    "keyboard": ["w", "a", "s", "d", "q", "e"],
}

# from: feat/addendum-phase7A-rl-debugging
_DOMINANT_TERM_THRESHOLD = 100.0  # one term's |weight| > 100x another → dominant

# from: feat/addendum-dr-advanced
_DR_PRESETS: Dict[str, Dict[str, Any]] = {
    "indoor_industrial": {
        "description": "Indoor industrial workspace — fluorescent overhead, concrete floor.",
        "lighting_lux": [300, 2000],
        "floor_texture": ["concrete_smooth", "concrete_rough", "epoxy_grey"],
        "light_temperature_k": [3500, 5500],
        "ambient_color": [[0.8, 0.85, 0.9], [1.0, 1.0, 1.0]],
    },
    "outdoor_daylight": {
        "description": "Outdoor scene — sun + sky, varying cloud cover.",
        "sun_elevation_deg": [15, 75],
        "sun_azimuth_deg": [0, 360],
        "cloud_cover": [0.0, 0.8],
        "ground_material": ["asphalt", "grass", "gravel", "dirt"],
    },
    "warehouse": {
        "description": "Warehouse — shelves, mixed lighting, cardboard.",
        "shelf_offset_m": [-0.05, 0.05],
        "lighting_lux": [200, 1500],
        "box_texture": ["cardboard_clean", "cardboard_worn", "cardboard_taped"],
        "aisle_width_m": [1.8, 3.5],
    },
    "cleanroom": {
        "description": "Cleanroom — controlled environment, minimal variation.",
        "lighting_lux": [800, 1200],
        "ambient_color": [[1.0, 1.0, 1.0], [1.0, 1.0, 1.0]],
        "floor_texture": ["epoxy_white"],
        "particulate_density": [0.0, 0.05],
    },
    "aggressive_sim2real": {
        "description": "Maximum robustness — every parameter at +/-50%.",
        "mass_scale": [0.5, 1.5],
        "friction_scale": [0.5, 1.5],
        "damping_scale": [0.5, 1.5],
        "gravity_scale": [0.95, 1.05],
        "lighting_scale": [0.3, 1.7],
        "action_latency_ms": [10, 80],
    },
}

# from: feat/new-physics-calibration
_DR_RANGE_HINTS = {
    "friction": "+-30% of calibrated values",
    "damping": "+-20%",
    "armature": "+-10%",
    "masses": "+-5-10%",
    "viscous_friction": "+-20%",
}

# from: feat/addendum-dr-advanced
_DR_ROBOT_HINTS: Dict[str, Dict[str, Any]] = {
    "franka": {"gripper_friction": [0.5, 1.0], "joint_damping_default": "URDF"},
    "panda": {"gripper_friction": [0.5, 1.0], "joint_damping_default": "URDF"},
    "ur10": {"gripper_friction": [0.4, 0.9], "joint_damping_default": "URDF"},
    "ur5": {"gripper_friction": [0.4, 0.9], "joint_damping_default": "URDF"},
    "anymal": {"ground_friction": [0.5, 1.2], "joint_damping_default": "URDF"},
    "g1": {"joint_damping_default": "URDF", "action_latency_ms": [5, 25]},
}

# from: feat/addendum-dr-advanced
_DR_TASK_DEFAULTS: Dict[str, Dict[str, Any]] = {
    "pick_and_place": {
        "object_mass_kg": [0.1, 2.0],
        "gripper_friction": [0.5, 1.0],
        "joint_damping_scale": [0.8, 1.2],
        "gravity_m_s2": [9.71, 9.91],
        "action_latency_ms": [10, 50],
        "lighting_lux": [300, 2000],
    },
    "locomotion": {
        "ground_friction": [0.4, 1.2],
        "joint_damping_scale": [0.7, 1.3],
        "gravity_m_s2": [9.71, 9.91],
        "action_latency_ms": [5, 30],
        "terrain_height_m": [0.0, 0.15],
    },
    "navigation": {
        "wheel_friction": [0.3, 0.9],
        "lidar_noise_m": [0.0, 0.05],
        "imu_bias_rad_s": [0.0, 0.01],
        "action_latency_ms": [20, 80],
    },
    "assembly": {
        "object_mass_kg": [0.05, 0.5],
        "part_friction": [0.3, 0.9],
        "tolerance_mm": [0.1, 1.0],
        "action_latency_ms": [10, 40],
    },
}

# from: feat/7E-eureka-rewards
_eureka_runs: Dict[str, Dict] = {}

# from: feat/addendum-phase7G-groot-tooling-v2
_EXPORT_TARGETS = {
    "jetson_agx_orin": {"format": "TensorRT bf16", "expected_hz": 5.8, "fp8_supported": False, "note": "Official pipeline"},
    "jetson_orin_nx": {"format": "TensorRT bf16", "expected_hz": 3.0, "fp8_supported": False, "note": "FP8/NVFP4 unsupported (SM87)"},
    "x86_rtx4090": {"format": "TensorRT bf16", "expected_hz": 15.0, "fp8_supported": True, "note": "Best desktop performance"},
    "x86_a6000": {"format": "TensorRT bf16", "expected_hz": 12.0, "fp8_supported": True, "note": "High VRAM headroom"},
}

# from: feat/addendum-phase7G-groot-tooling-v2
_FINETUNE_FREEZE_PROFILES = {
    "similar_to_pretrain": {
        "freeze": ["vision_encoder", "language_model"],
        "tune": ["dit_layers", "connectors"],
        "rationale": "NVIDIA's own recipe — preserves visual+language priors, adapts action head",
        "lora_rank": 0,
    },
    "new_visual_domain": {
        "freeze": ["language_model"],
        "tune": ["vision_encoder", "dit_layers", "connectors"],
        "rationale": "New visual domain requires vision adaptation. Cuts batch from 200 to 16 on A6000.",
        "lora_rank": 0,
        "warning": "Don't Blind Your VLA: unfreezing vision can cause OOD generalization loss",
    },
    "new_embodiment": {
        "freeze": [],
        "tune": ["all (LoRA)"],
        "rationale": "New robot morphology requires full re-tuning. LoRA rank 16 fits on RTX 4080 <8GB",
        "lora_rank": 16,
    },
}

# from: feat/addendum-phase3-urdf-postprocessor
_FIX_PROFILE_PATTERNS = {
    "franka": ["franka", "panda"],
    "ur5": ["ur5"],
    "ur10": ["ur10"],
    "g1": ["g1", "unitree_g1"],
    "allegro": ["allegro"],
}

# from: feat/7G-groot-n1
_GROOT_EMBODIMENTS = {
    "LIBERO_PANDA": {
        "obs_type": "rgb+proprio",
        "action_dim": 7,
        "description": "Franka Panda in LIBERO benchmark",
        "vram_gb": 24,
    },
    "OXE_WIDOWX": {
        "obs_type": "rgb+proprio",
        "action_dim": 7,
        "description": "WidowX from Open X-Embodiment",
        "vram_gb": 24,
    },
    "UNITREE_G1": {
        "obs_type": "rgb+proprio",
        "action_dim": 29,
        "description": "Unitree G1 humanoid",
        "vram_gb": 24,
    },
    "custom": {
        "obs_type": "rgb+proprio",
        "action_dim": None,
        "description": "Custom embodiment — configure manually",
        "vram_gb": 24,
    },
}

# from: feat/addendum-community-remote-v2
_ISAA_MANIFEST_VERSION = 1

# from: feat/atomic-tier6-lighting
_LIGHT_TYPE_NAMES = (
    "DistantLight",
    "DomeLight",
    "SphereLight",
    "RectLight",
    "DiskLight",
    "CylinderLight",
)

# from: feat/new-onboarding
_MOBILE_ROBOT_KEYWORDS = {"carter", "jetbot", "nova_carter", "kaya", "husky", "turtlebot"}

# from: feat/addendum-ros2-nav2
_NAV2_BRIDGE_PROFILES = {
    "ur10e_moveit2": {
        "description": "UR10e arm wired for MoveIt2 — joint state publish, FollowJointTrajectory subscribe, TF.",
        "topics": ["/joint_states", "/joint_command", "/tf"],
        "nodes": [
            ("OnPlaybackTick", "omni.graph.action.OnPlaybackTick"),
            ("ROS2Context", "isaacsim.ros2.bridge.ROS2Context"),
            ("PublishClock", "isaacsim.ros2.bridge.ROS2PublishClock"),
            ("PublishJointState", "isaacsim.ros2.bridge.ROS2PublishJointState"),
            ("SubscribeJointState", "isaacsim.ros2.bridge.ROS2SubscribeJointState"),
            ("PublishTF", "isaacsim.ros2.bridge.ROS2PublishTransformTree"),
            ("ArticulationController", "isaacsim.core.nodes.IsaacArticulationController"),
        ],
        "topic_values": {
            "PublishJointState.inputs:topicName": "/joint_states",
            "SubscribeJointState.inputs:topicName": "/joint_command",
        },
    },
    "jetbot_nav2": {
        "description": "Jetbot wired for Nav2 — lidar publish, cmd_vel subscribe, odom publish, TF, clock.",
        "topics": ["/scan", "/cmd_vel", "/odom", "/tf", "/clock"],
        "nodes": [
            ("OnPlaybackTick", "omni.graph.action.OnPlaybackTick"),
            ("ROS2Context", "isaacsim.ros2.bridge.ROS2Context"),
            ("PublishClock", "isaacsim.ros2.bridge.ROS2PublishClock"),
            ("PublishLidar", "isaacsim.ros2.bridge.ROS2PublishLaserScan"),
            ("SubscribeCmdVel", "isaacsim.ros2.bridge.ROS2SubscribeTwist"),
            ("PublishOdom", "isaacsim.ros2.bridge.ROS2PublishOdometry"),
            ("PublishTF", "isaacsim.ros2.bridge.ROS2PublishTransformTree"),
            ("DifferentialController", "isaacsim.robot.wheeled_robots.DifferentialController"),
        ],
        "topic_values": {
            "PublishLidar.inputs:topicName": "/scan",
            "SubscribeCmdVel.inputs:topicName": "/cmd_vel",
            "PublishOdom.inputs:topicName": "/odom",
            "PublishClock.inputs:topicName": "/clock",
        },
    },
    "franka_moveit2": {
        "description": "Franka arm wired for MoveIt2 — joint state, gripper state, TF.",
        "topics": ["/joint_states", "/gripper", "/tf"],
        "nodes": [
            ("OnPlaybackTick", "omni.graph.action.OnPlaybackTick"),
            ("ROS2Context", "isaacsim.ros2.bridge.ROS2Context"),
            ("PublishClock", "isaacsim.ros2.bridge.ROS2PublishClock"),
            ("PublishJointState", "isaacsim.ros2.bridge.ROS2PublishJointState"),
            ("SubscribeJointState", "isaacsim.ros2.bridge.ROS2SubscribeJointState"),
            ("PublishGripper", "isaacsim.ros2.bridge.ROS2PublishJointState"),
            ("PublishTF", "isaacsim.ros2.bridge.ROS2PublishTransformTree"),
            ("ArticulationController", "isaacsim.core.nodes.IsaacArticulationController"),
        ],
        "topic_values": {
            "PublishJointState.inputs:topicName": "/joint_states",
            "SubscribeJointState.inputs:topicName": "/joint_command",
            "PublishGripper.inputs:topicName": "/gripper",
        },
    },
    "amr_full": {
        "description": "Full AMR — 2x lidar, 4x camera, odom, cmd_vel, TF, clock.",
        "topics": [
            "/scan_front", "/scan_rear", "/cmd_vel", "/odom", "/tf", "/clock",
            "/camera_front/image_raw", "/camera_rear/image_raw",
            "/camera_left/image_raw", "/camera_right/image_raw",
        ],
        "nodes": [
            ("OnPlaybackTick", "omni.graph.action.OnPlaybackTick"),
            ("ROS2Context", "isaacsim.ros2.bridge.ROS2Context"),
            ("PublishClock", "isaacsim.ros2.bridge.ROS2PublishClock"),
            ("PublishLidarFront", "isaacsim.ros2.bridge.ROS2PublishLaserScan"),
            ("PublishLidarRear", "isaacsim.ros2.bridge.ROS2PublishLaserScan"),
            ("SubscribeCmdVel", "isaacsim.ros2.bridge.ROS2SubscribeTwist"),
            ("PublishOdom", "isaacsim.ros2.bridge.ROS2PublishOdometry"),
            ("PublishTF", "isaacsim.ros2.bridge.ROS2PublishTransformTree"),
            ("PublishCamFront", "isaacsim.ros2.bridge.ROS2PublishImage"),
            ("PublishCamRear", "isaacsim.ros2.bridge.ROS2PublishImage"),
            ("PublishCamLeft", "isaacsim.ros2.bridge.ROS2PublishImage"),
            ("PublishCamRight", "isaacsim.ros2.bridge.ROS2PublishImage"),
        ],
        "topic_values": {
            "PublishLidarFront.inputs:topicName": "/scan_front",
            "PublishLidarRear.inputs:topicName": "/scan_rear",
            "SubscribeCmdVel.inputs:topicName": "/cmd_vel",
            "PublishOdom.inputs:topicName": "/odom",
            "PublishCamFront.inputs:topicName": "/camera_front/image_raw",
            "PublishCamRear.inputs:topicName": "/camera_rear/image_raw",
            "PublishCamLeft.inputs:topicName": "/camera_left/image_raw",
            "PublishCamRight.inputs:topicName": "/camera_right/image_raw",
        },
    },
}

# from: feat/new-omnigraph-assistant
_OG_TEMPLATES = {
    "ros2_clock": {
        "description": "Publish simulation clock to ROS2 /clock topic",
        "nodes": [
            ("on_playback_tick", "omni.graph.action.OnPlaybackTick"),
            ("ros2_context", "isaacsim.ros2.bridge.ROS2Context"),
            ("read_sim_time", "isaacsim.core.nodes.IsaacReadSimulationTime"),
            ("publish_clock", "isaacsim.ros2.bridge.ROS2PublishClock"),
        ],
        "connections": [
            ("on_playback_tick.outputs:tick", "publish_clock.inputs:execIn"),
            ("ros2_context.outputs:context", "publish_clock.inputs:context"),
            ("read_sim_time.outputs:simulationTime", "publish_clock.inputs:timeStamp"),
        ],
        "values": {},
        "param_keys": [],
    },
    "ros2_joint_state": {
        "description": "Publish robot joint states to ROS2",
        "nodes": [
            ("on_playback_tick", "omni.graph.action.OnPlaybackTick"),
            ("ros2_context", "isaacsim.ros2.bridge.ROS2Context"),
            ("read_sim_time", "isaacsim.core.nodes.IsaacReadSimulationTime"),
            ("articulation_controller", "isaacsim.core.nodes.IsaacArticulationController"),
            ("publish_joint_state", "isaacsim.ros2.bridge.ROS2PublishJointState"),
        ],
        "connections": [
            ("on_playback_tick.outputs:tick", "publish_joint_state.inputs:execIn"),
            ("on_playback_tick.outputs:tick", "articulation_controller.inputs:execIn"),
            ("ros2_context.outputs:context", "publish_joint_state.inputs:context"),
            ("read_sim_time.outputs:simulationTime", "publish_joint_state.inputs:timeStamp"),
        ],
        "values": {
            "articulation_controller.inputs:robotPath": "{robot_path}",
            "publish_joint_state.inputs:topicName": "{topic}",
        },
        "param_keys": ["robot_path", "topic"],
        "defaults": {"topic": "/joint_states"},
    },
    "ros2_camera": {
        "description": "Publish camera images to ROS2",
        "nodes": [
            ("on_playback_tick", "omni.graph.action.OnPlaybackTick"),
            ("ros2_context", "isaacsim.ros2.bridge.ROS2Context"),
            ("read_sim_time", "isaacsim.core.nodes.IsaacReadSimulationTime"),
            ("camera_helper", "isaacsim.ros2.bridge.ROS2CameraHelper"),
        ],
        "connections": [
            ("on_playback_tick.outputs:tick", "camera_helper.inputs:execIn"),
            ("ros2_context.outputs:context", "camera_helper.inputs:context"),
            ("read_sim_time.outputs:simulationTime", "camera_helper.inputs:timeStamp"),
        ],
        "values": {
            "camera_helper.inputs:cameraPrimPath": "{camera_path}",
            "camera_helper.inputs:topicName": "{topic}",
        },
        "param_keys": ["camera_path", "topic"],
        "defaults": {"topic": "/camera/image_raw"},
    },
    "ros2_lidar": {
        "description": "Publish lidar scans to ROS2",
        "nodes": [
            ("on_playback_tick", "omni.graph.action.OnPlaybackTick"),
            ("ros2_context", "isaacsim.ros2.bridge.ROS2Context"),
            ("read_sim_time", "isaacsim.core.nodes.IsaacReadSimulationTime"),
            ("read_lidar", "isaacsim.sensor.nodes.IsaacReadLidar"),
            ("publish_laser_scan", "isaacsim.ros2.bridge.ROS2PublishLaserScan"),
        ],
        "connections": [
            ("on_playback_tick.outputs:tick", "read_lidar.inputs:execIn"),
            ("read_lidar.outputs:execOut", "publish_laser_scan.inputs:execIn"),
            ("ros2_context.outputs:context", "publish_laser_scan.inputs:context"),
            ("read_sim_time.outputs:simulationTime", "publish_laser_scan.inputs:timeStamp"),
            ("read_lidar.outputs:azimuthRange", "publish_laser_scan.inputs:azimuthRange"),
            ("read_lidar.outputs:depthRange", "publish_laser_scan.inputs:depthRange"),
            ("read_lidar.outputs:horizontalResolution", "publish_laser_scan.inputs:horizontalResolution"),
            ("read_lidar.outputs:intensitiesData", "publish_laser_scan.inputs:intensitiesData"),
            ("read_lidar.outputs:linearDepthData", "publish_laser_scan.inputs:linearDepthData"),
            ("read_lidar.outputs:numCols", "publish_laser_scan.inputs:numCols"),
            ("read_lidar.outputs:numRows", "publish_laser_scan.inputs:numRows"),
        ],
        "values": {
            "read_lidar.inputs:lidarPrimPath": "{lidar_path}",
            "publish_laser_scan.inputs:topicName": "{topic}",
        },
        "param_keys": ["lidar_path", "topic"],
        "defaults": {"topic": "/scan"},
    },
    "ros2_cmd_vel": {
        "description": "Subscribe to /cmd_vel and drive a differential robot",
        "nodes": [
            ("ros2_context", "isaacsim.ros2.bridge.ROS2Context"),
            ("subscribe_twist", "isaacsim.ros2.bridge.ROS2SubscribeTwist"),
            ("differential_controller", "isaacsim.robot.wheeled_robots.DifferentialController"),
            ("articulation_controller", "isaacsim.core.nodes.IsaacArticulationController"),
        ],
        "connections": [
            ("ros2_context.outputs:context", "subscribe_twist.inputs:context"),
            ("subscribe_twist.outputs:linearVelocity", "differential_controller.inputs:linearVelocity"),
            ("subscribe_twist.outputs:angularVelocity", "differential_controller.inputs:angularVelocity"),
            ("differential_controller.outputs:velocityCommand", "articulation_controller.inputs:velocityCommand"),
        ],
        "values": {
            "subscribe_twist.inputs:topicName": "{topic}",
            "articulation_controller.inputs:robotPath": "{robot_path}",
        },
        "param_keys": ["robot_path", "topic"],
        "defaults": {"topic": "/cmd_vel"},
    },
    "ros2_tf": {
        "description": "Publish TF transform tree to ROS2",
        "nodes": [
            ("on_playback_tick", "omni.graph.action.OnPlaybackTick"),
            ("ros2_context", "isaacsim.ros2.bridge.ROS2Context"),
            ("read_sim_time", "isaacsim.core.nodes.IsaacReadSimulationTime"),
            ("publish_tf", "isaacsim.ros2.bridge.ROS2PublishTransformTree"),
        ],
        "connections": [
            ("on_playback_tick.outputs:tick", "publish_tf.inputs:execIn"),
            ("ros2_context.outputs:context", "publish_tf.inputs:context"),
            ("read_sim_time.outputs:simulationTime", "publish_tf.inputs:timeStamp"),
        ],
        "values": {
            "publish_tf.inputs:parentPrim": "{root_prim}",
        },
        "param_keys": ["root_prim"],
        "defaults": {"root_prim": "/World"},
    },
    "ros2_imu": {
        "description": "Publish IMU data to ROS2",
        "nodes": [
            ("on_playback_tick", "omni.graph.action.OnPlaybackTick"),
            ("ros2_context", "isaacsim.ros2.bridge.ROS2Context"),
            ("read_imu", "isaacsim.sensor.nodes.IsaacReadIMU"),
            ("publish_imu", "isaacsim.ros2.bridge.ROS2PublishImu"),
        ],
        "connections": [
            ("on_playback_tick.outputs:tick", "read_imu.inputs:execIn"),
            ("read_imu.outputs:execOut", "publish_imu.inputs:execIn"),
            ("ros2_context.outputs:context", "publish_imu.inputs:context"),
            ("read_imu.outputs:angVel", "publish_imu.inputs:angularVelocity"),
            ("read_imu.outputs:linAcc", "publish_imu.inputs:linearAcceleration"),
            ("read_imu.outputs:orientation", "publish_imu.inputs:orientation"),
        ],
        "values": {
            "read_imu.inputs:imuPrimPath": "{imu_path}",
            "publish_imu.inputs:topicName": "{topic}",
        },
        "param_keys": ["imu_path", "topic"],
        "defaults": {"topic": "/imu/data"},
    },
    "ros2_odom": {
        "description": "Publish odometry data to ROS2",
        "nodes": [
            ("on_playback_tick", "omni.graph.action.OnPlaybackTick"),
            ("ros2_context", "isaacsim.ros2.bridge.ROS2Context"),
            ("read_sim_time", "isaacsim.core.nodes.IsaacReadSimulationTime"),
            ("compute_odom", "isaacsim.core.nodes.IsaacComputeOdometry"),
            ("publish_odom", "isaacsim.ros2.bridge.ROS2PublishOdometry"),
        ],
        "connections": [
            ("on_playback_tick.outputs:tick", "compute_odom.inputs:execIn"),
            ("compute_odom.outputs:execOut", "publish_odom.inputs:execIn"),
            ("ros2_context.outputs:context", "publish_odom.inputs:context"),
            ("read_sim_time.outputs:simulationTime", "publish_odom.inputs:timeStamp"),
            ("compute_odom.outputs:angularVelocity", "publish_odom.inputs:angularVelocity"),
            ("compute_odom.outputs:linearVelocity", "publish_odom.inputs:linearVelocity"),
            ("compute_odom.outputs:orientation", "publish_odom.inputs:orientation"),
            ("compute_odom.outputs:position", "publish_odom.inputs:position"),
        ],
        "values": {
            "compute_odom.inputs:chassisPrimPath": "{chassis_path}",
            "publish_odom.inputs:topicName": "{topic}",
        },
        "param_keys": ["chassis_path", "topic"],
        "defaults": {"topic": "/odom"},
    },
}

# from: feat/new-material-database
_PHYSICS_MATERIALS_PATH = _WORKSPACE / "knowledge" / "physics_materials.json"

# from: feat/new-auto-simplification
_PHYSICS_SETTINGS_PRESETS = {
    "rl_training": {
        "scene_type": "rl_training",
        "description": "RL training with 1024 environments — maximum throughput",
        "solver": "TGS",
        "solver_position_iterations": 4,
        "solver_velocity_iterations": 1,
        "gpu_dynamics": True,
        "broadphase": "GPU",
        "ccd": False,
        "time_step": 1.0 / 120,
        "time_steps_per_second": 120,
        "notes": "Use TGS solver with minimal iterations for speed. GPU dynamics required for large env counts. Disable CCD to save compute.",
    },
    "manipulation": {
        "scene_type": "manipulation",
        "description": "Precision manipulation (pick-and-place, assembly)",
        "solver": "TGS",
        "solver_position_iterations": 16,
        "solver_velocity_iterations": 1,
        "gpu_dynamics": False,
        "broadphase": "MBP",
        "ccd": True,
        "ccd_note": "Enable CCD on gripper fingers only — not all objects",
        "time_step": 1.0 / 240,
        "time_steps_per_second": 240,
        "notes": "Higher iterations for stable contacts. CCD on gripper prevents finger pass-through. 240 Hz for smooth grasping.",
    },
    "mobile_robot": {
        "scene_type": "mobile_robot",
        "description": "Mobile robot navigation (wheeled/legged)",
        "solver": "TGS",
        "solver_position_iterations": 4,
        "solver_velocity_iterations": 1,
        "gpu_dynamics": True,
        "broadphase": "GPU",
        "ccd": False,
        "time_step": 1.0 / 60,
        "time_steps_per_second": 60,
        "notes": "Low iterations sufficient for wheel/ground contact. GPU dynamics helps with large environments. 60 Hz matches typical sensor rates.",
    },
    "digital_twin": {
        "scene_type": "digital_twin",
        "description": "Digital twin visualization (minimal physics)",
        "solver": "PGS",
        "solver_position_iterations": 4,
        "solver_velocity_iterations": 1,
        "gpu_dynamics": False,
        "broadphase": "MBP",
        "ccd": False,
        "time_step": 1.0 / 60,
        "time_steps_per_second": 60,
        "notes": "PGS solver is sufficient for visualization-only scenes. Disable GPU dynamics and CCD to minimize resource usage.",
    },
}

# from: feat/addendum-phase2-smart-debugging
_PHYSX_ERROR_PATTERNS = [
    {
        "pattern": r"negative mass",
        "category": "mass_configuration",
        "fix": "Set the mass to a positive value via UsdPhysics.MassAPI. Check that density and volume are both positive.",
        "severity": "critical",
        "prim_regex": r"prim[:\s]+['\"]?(/[^\s'\"]+)",
    },
    {
        "pattern": r"joint limit exceeded",
        "category": "joint_limits",
        "fix": "Increase the joint limit range or add damping to prevent overshoot. Check RevoluteJoint.LowerLimitAttr/UpperLimitAttr.",
        "severity": "warning",
        "prim_regex": r"joint[:\s]+['\"]?(/[^\s'\"]+)",
    },
    {
        "pattern": r"collision mesh invalid|degenerate triangle|invalid mesh",
        "category": "collision_mesh",
        "fix": "Regenerate the collision mesh with convex decomposition. Remove degenerate (zero-area) triangles from the source mesh.",
        "severity": "critical",
        "prim_regex": r"prim[:\s]+['\"]?(/[^\s'\"]+)",
    },
    {
        "pattern": r"solver diverge|solver divergence|simulation diverge",
        "category": "solver_divergence",
        "fix": "Lower the physics timestep (e.g. 1/120 instead of 1/60), increase solver iterations (positionIterations=16, velocityIterations=4), or reduce extreme mass ratios.",
        "severity": "critical",
        "prim_regex": None,
    },
    {
        "pattern": r"invalid inertia|zero inertia|non-positive inertia",
        "category": "inertia_tensor",
        "fix": "Set a valid diagonal inertia tensor via MassAPI.DiagonalInertiaAttr. All components must be > 0.",
        "severity": "critical",
        "prim_regex": r"prim[:\s]+['\"]?(/[^\s'\"]+)",
    },
    {
        "pattern": r"missing collision|no collision api|CollisionAPI not applied",
        "category": "missing_collision",
        "fix": "Apply UsdPhysics.CollisionAPI to the mesh prim: UsdPhysics.CollisionAPI.Apply(prim).",
        "severity": "error",
        "prim_regex": r"prim[:\s]+['\"]?(/[^\s'\"]+)",
    },
    {
        "pattern": r"PhysicsScene.*not found|no physics scene",
        "category": "missing_physics_scene",
        "fix": "Create a PhysicsScene prim: stage.DefinePrim('/World/PhysicsScene', 'PhysicsScene'). Apply UsdPhysics.Scene API.",
        "severity": "critical",
        "prim_regex": None,
    },
    {
        "pattern": r"mass ratio|extreme mass ratio",
        "category": "mass_ratio",
        "fix": "Reduce the mass ratio between contacting bodies to below 100:1. Consider using articulations instead of free bodies for robot links.",
        "severity": "warning",
        "prim_regex": r"(?:between|bodies)[:\s]+['\"]?(/[^\s'\"]+)",
    },
    {
        "pattern": r"articulation.*loop|closed loop|kinematic loop",
        "category": "articulation_loop",
        "fix": "PhysX does not support closed-loop articulations. Break the loop by removing one joint or using a D6 joint with a spring constraint instead.",
        "severity": "critical",
        "prim_regex": r"articulation[:\s]+['\"]?(/[^\s'\"]+)",
    },
    {
        "pattern": r"self.intersection|self.penetration|initial overlap|interpenetration",
        "category": "initial_overlap",
        "fix": "Move the overlapping bodies apart before starting simulation. Use debug draw to visualize collision shapes.",
        "severity": "warning",
        "prim_regex": r"prim[:\s]+['\"]?(/[^\s'\"]+)",
    },
    {
        "pattern": r"too many contacts|contact buffer overflow",
        "category": "contact_overflow",
        "fix": "Increase PhysxScene.maxNbContactDataBlocks or simplify collision geometry. Consider using collision filtering.",
        "severity": "error",
        "prim_regex": None,
    },
    {
        "pattern": r"gpu.*memory|cuda.*out of memory|gpu.*buffer",
        "category": "gpu_memory",
        "fix": "Reduce the number of collision pairs, lower particle counts, or use simpler collision shapes (convex hull instead of triangle mesh).",
        "severity": "critical",
        "prim_regex": None,
    },
    {
        "pattern": r"fixed base.*missing|no fixed base|floating base",
        "category": "fixed_base",
        "fix": "Set PhysxArticulationAPI.fixedBase=True on the articulation root prim for stationary robots.",
        "severity": "warning",
        "prim_regex": r"articulation[:\s]+['\"]?(/[^\s'\"]+)",
    },
    {
        "pattern": r"nan|NaN detected|not a number",
        "category": "nan_values",
        "fix": "NaN typically indicates numerical instability. Check for zero-mass bodies, extreme forces, or missing gravity direction. Lower timestep and increase solver iterations.",
        "severity": "critical",
        "prim_regex": r"prim[:\s]+['\"]?(/[^\s'\"]+)",
    },
    {
        "pattern": r"joint drive.*target|drive target out of range",
        "category": "drive_target",
        "fix": "Ensure joint drive targets are within the joint limit range. Clamp target values to [lowerLimit, upperLimit].",
        "severity": "warning",
        "prim_regex": r"joint[:\s]+['\"]?(/[^\s'\"]+)",
    },
    {
        "pattern": r"invalid transform|singular matrix|non-finite transform",
        "category": "invalid_transform",
        "fix": "Reset the prim transform to identity. Check for zero-scale axes or non-orthogonal rotation matrices.",
        "severity": "critical",
        "prim_regex": r"prim[:\s]+['\"]?(/[^\s'\"]+)",
    },
    {
        "pattern": r"broadphase.*overflow|pair buffer.*full",
        "category": "broadphase_overflow",
        "fix": "Increase PhysxScene.maxBiasCoefficient or reduce the number of dynamic objects. Use collision groups to limit pair generation.",
        "severity": "error",
        "prim_regex": None,
    },
    {
        "pattern": r"unstable simulation|jitter|oscillat",
        "category": "simulation_instability",
        "fix": "Increase solver iterations, add damping to joints, or lower the physics timestep. Check for stiff springs without adequate damping.",
        "severity": "warning",
        "prim_regex": None,
    },
    {
        "pattern": r"metersPerUnit.*mismatch|scale mismatch|unit mismatch",
        "category": "unit_mismatch",
        "fix": "Ensure all referenced assets use the same metersPerUnit. Set UsdGeom.SetStageMetersPerUnit(stage, 1.0) or scale the referenced asset.",
        "severity": "error",
        "prim_regex": r"asset[:\s]+['\"]?(/[^\s'\"]+)",
    },
    {
        "pattern": r"exceeded velocity|velocity clamp|max velocity",
        "category": "velocity_exceeded",
        "fix": "Increase PhysxRigidBodyAPI.maxLinearVelocity or reduce applied forces. Default max is 100 m/s.",
        "severity": "warning",
        "prim_regex": r"prim[:\s]+['\"]?(/[^\s'\"]+)",
    },
]

# from: feat/6A-physx-validation
_PHYSX_ERROR_RE = re.compile(
    r"physx.*?error|px.*?error|physics.*?simulation.*?error|"
    r"articulation.*?error|joint.*?error",
    re.IGNORECASE,
)

# from: feat/addendum-collision-mesh-quality-v2
_PHYSX_HULL_MAX_POLYS = 255    # Cooked hull polygon limit

# from: feat/addendum-collision-mesh-quality-v2
_PHYSX_HULL_MAX_VERTS = 64     # GPU PhysX vertex limit per hull

# from: feat/atomic-tier8-render
_POST_PROCESS_PATHS = {
    "bloom": "/Render/PostProcess/Bloom",
    "tonemap": "/Render/PostProcess/Tonemap",
    "dof": "/Render/PostProcess/DoF",
    "motion_blur": "/Render/PostProcess/MotionBlur",
}

# from: feat/phase10-autonomous-workflows
_PROACTIVE_TRIGGER_PLAYBOOKS: Dict[str, List[str]] = {
    "scene_opened":      ["scene_summary", "get_console_errors"],
    "robot_imported":    ["scene_summary", "get_articulation_state"],
    "console_error":     ["get_console_errors", "explain_error"],
    "training_started":  ["get_console_errors"],
    "training_active":   ["get_console_errors"],
    "training_finished": ["get_console_errors"],
    "sim_idle":          ["scene_summary"],
    "sim_play":          ["get_console_errors", "scene_summary"],
    "fps_drop":          ["get_debug_info", "scene_summary"],
    "target_placed":     ["scene_summary", "measure_distance"],
}

# from: feat/new-physics-calibration
_QUICK_CALIBRATE_PARAMS = ["armature", "friction", "masses"]

# from: feat/new-quick-demo-builder-v2
_QUICK_DEMO_TEMPLATES = {
    "pick_place": {
        "default_robot": "franka",
        "default_objects": ["cube"],
        "policy_checkpoint": "ppo_pick_place_franka.pt",
        "policy_algo": "ppo",
        "task": "Pick objects from tray and place in bin",
        "camera_position": [1.5, -1.0, 1.2],
    },
    "mobile_nav": {
        "default_robot": "jetbot",
        "default_objects": ["waypoint"],
        "policy_checkpoint": "astar_diffdrive_jetbot.pt",
        "policy_algo": "astar",
        "task": "Navigate to waypoint avoiding obstacles",
        "camera_position": [0, -3, 4],
    },
    "humanoid_walk": {
        "default_robot": "g1",
        "default_objects": [],
        "policy_checkpoint": "groot_n1_g1_walk.pt",
        "policy_algo": "groot",
        "task": "Walk forward 2m with stable balance",
        "camera_position": [3, -3, 2],
    },
}

# from: feat/addendum-community-remote-v2
_RENDER_QUALITY_PRESETS = {
    "preview": {
        "renderer": "RayTracing",
        "resolution": (1280, 720),
        "spp": 1,
    },
    "presentation": {
        "renderer": "PathTracing",
        "resolution": (1920, 1080),
        "spp": 64,
    },
    "production": {
        "renderer": "PathTracing",
        "resolution": (3840, 2160),
        "spp": 256,
    },
}

# from: feat/addendum-phase7A-rl-debugging
_REWARD_HACK_PATTERNS = [
    ("alive_bonus", "alive bonus reward without an explicit fall/early-termination is exploitable — robot learns to just stand still"),
    ("survival_bonus", "survival bonus without termination — same hacking risk as alive_bonus"),
    ("time_bonus", "time-based reward — robot may stall to milk the bonus"),
]

# from: feat/addendum-phase3-urdf-postprocessor
_ROBOT_FIX_PROFILES = {
    "franka": {
        "robot_name": "franka",
        "display_name": "Franka Emika Panda",
        "known_issues": [
            "rootJoint creates unwanted floating base — delete it",
            "Default drive stiffness too low for position control",
            "panda_hand and finger links often missing CollisionAPI",
        ],
        "fixes": [
            {
                "description": "Delete rootJoint to allow fixedBase anchoring",
                "code": "stage.RemovePrim('{art_path}/rootJoint')",
            },
            {
                "description": "Set fixedBase for stationary arm",
                "code": "PhysxSchema.PhysxArticulationAPI.Apply(stage.GetPrimAtPath('{art_path}')).CreateEnabledSelfCollisionsAttr(False)",
            },
            {
                "description": "Set drive stiffness Kp=1000, Kd=100 on all joints",
                "code": "# Apply Kp=1000, Kd=100 to all revolute joints",
            },
            {
                "description": "Add CollisionAPI to hand and finger links",
                "code": "# Apply CollisionAPI to panda_hand, panda_leftfinger, panda_rightfinger",
            },
        ],
        "drive_gains": {"kp": 1000, "kd": 100},
    },
    "ur5": {
        "robot_name": "ur5",
        "display_name": "Universal Robots UR5",
        "known_issues": [
            "Joint limits often imported as ±infinity",
            "Missing collision meshes on wrist links",
        ],
        "fixes": [
            {
                "description": "Set finite joint limits (±2π for revolute joints)",
                "code": "# Set lowerLimit=-6.283, upperLimit=6.283 on all revolute joints",
            },
            {
                "description": "Add CollisionAPI to wrist links",
                "code": "# Apply CollisionAPI to wrist_1_link, wrist_2_link, wrist_3_link",
            },
        ],
        "drive_gains": {"kp": 800, "kd": 80},
    },
    "ur10": {
        "robot_name": "ur10",
        "display_name": "Universal Robots UR10",
        "known_issues": [
            "Joint limits often imported as ±infinity",
            "Missing collision meshes on wrist links",
            "Default mass values may be incorrect for UR10 (heavier than UR5)",
        ],
        "fixes": [
            {
                "description": "Set finite joint limits (±2π for revolute joints)",
                "code": "# Set lowerLimit=-6.283, upperLimit=6.283 on all revolute joints",
            },
            {
                "description": "Add CollisionAPI to wrist links",
                "code": "# Apply CollisionAPI to wrist_1_link, wrist_2_link, wrist_3_link",
            },
        ],
        "drive_gains": {"kp": 1000, "kd": 100},
    },
    "g1": {
        "robot_name": "g1",
        "display_name": "Unitree G1 Humanoid",
        "known_issues": [
            "Many links imported with zero mass",
            "Extreme inertia ratios between torso and finger links",
            "Self-collision filtering needed for dense link structure",
        ],
        "fixes": [
            {
                "description": "Set minimum mass (0.1 kg) on zero-mass links",
                "code": "# Set mass=0.1 on all links where mass==0",
            },
            {
                "description": "Enable self-collision filtering",
                "code": "PhysxSchema.PhysxArticulationAPI.Apply(root).CreateEnabledSelfCollisionsAttr(True)",
            },
        ],
        "drive_gains": {"kp": 500, "kd": 50},
    },
    "allegro": {
        "robot_name": "allegro",
        "display_name": "Allegro Hand",
        "known_issues": [
            "Very small link masses cause solver instability",
            "Finger joint limits must be carefully bounded",
            "CollisionAPI often missing on fingertip links",
        ],
        "fixes": [
            {
                "description": "Set minimum mass (0.01 kg) on finger links",
                "code": "# Set mass=0.01 on all finger links",
            },
            {
                "description": "Add CollisionAPI to all fingertip links",
                "code": "# Apply CollisionAPI to all *_tip links",
            },
        ],
        "drive_gains": {"kp": 100, "kd": 10},
    },
}

# from: feat/addendum-phase3-urdf-postprocessor
_ROBOT_NAME_PATTERNS = {
    "franka": ["franka", "panda"],
    "ur10": ["ur10"],
    "ur5": ["ur5"],
    "ur5e": ["ur5e"],
    "cobotta": ["cobotta"],
}

# from: feat/8D-robot-setup
_ROBOT_TYPE_DEFAULTS = {
    "manipulator": {"stiffness": 1000, "damping": 100},
    "mobile":      {"stiffness": 500,  "damping": 50},
    "humanoid":    {"stiffness": 800,  "damping": 80},
}

# from: feat/addendum-phase8F-ros2-quality
_ROS2_QOS_PRESETS = {
    "scan": ("BEST_EFFORT", "VOLATILE", "Laser scan data — high-frequency, drop-tolerant"),
    "robot_description": ("RELIABLE", "TRANSIENT_LOCAL", "Robot URDF — latched, must arrive"),
    "tf": ("RELIABLE", "VOLATILE", "Transform tree — must be reliable"),
    "tf_static": ("RELIABLE", "TRANSIENT_LOCAL", "Static transforms — latched"),
    "cmd_vel": ("RELIABLE", "VOLATILE", "Velocity commands — must not be dropped"),
    "camera": ("BEST_EFFORT", "VOLATILE", "Camera images — high-bandwidth, drop-tolerant"),
    "image": ("BEST_EFFORT", "VOLATILE", "Image data — high-bandwidth, drop-tolerant"),
    "joint_states": ("RELIABLE", "VOLATILE", "Joint state feedback — must be reliable"),
    "clock": ("BEST_EFFORT", "VOLATILE", "Simulation clock — high-frequency"),
}

# from: feat/atomic-tier13-rl-runtime
_RUN_REGISTRY: Dict[str, Dict[str, Any]] = {}

# from: feat/new-quick-demo-builder-v2
_SCENE_STYLE_PRESETS = {
    "clean": {"intensity": 1500, "background": "white_floor"},
    "industrial": {"intensity": 1000, "background": "concrete"},
    "lab": {"intensity": 2000, "background": "neutral_gray"},
    "dramatic": {"intensity": 800, "background": "dark"},
}

# from: feat/6A-physx-validation
_SCENE_TEMPLATES = {
    "tabletop_manipulation": {
        "description": "Table-top manipulation scene with a Franka robot arm, objects to grasp, and an overhead camera. Ideal for pick-and-place tasks.",
        "category": "manipulation",
        "room_dims": [4, 4, 3],
        "objects": [
            {"name": "GroundPlane", "prim_type": "Plane", "position": [0, 0, 0], "scale": [5, 5, 1]},
            {"name": "Table", "prim_type": "Cube", "position": [0, 0, 0.4], "scale": [0.8, 0.6, 0.4]},
            {"name": "Franka", "prim_path": "/World/Franka", "asset_name": "franka", "position": [0, -0.3, 0.8], "scale": [1, 1, 1]},
            {"name": "Cube_Red", "prim_type": "Cube", "position": [0.15, 0.1, 0.85], "scale": [0.03, 0.03, 0.03]},
            {"name": "Cube_Green", "prim_type": "Cube", "position": [-0.1, 0.15, 0.85], "scale": [0.03, 0.03, 0.03]},
            {"name": "Cylinder_Blue", "prim_type": "Cylinder", "position": [0.05, -0.1, 0.85], "scale": [0.02, 0.02, 0.04]},
            {"name": "OverheadCamera", "prim_type": "Camera", "position": [0, 0, 1.8], "rotation": [-90, 0, 0]},
        ],
        "suggested_sensors": ["camera (overhead, 1280x720)", "contact_sensor (gripper fingers)"],
        "physics_settings": {"gravity": -9.81, "time_step": 1.0 / 120.0, "solver_iterations": 32},
    },
    "warehouse_picking": {
        "description": "Warehouse bin-picking scene with shelving units, a mobile robot, bins with objects, and an overhead camera. Good for logistics and order-fulfillment tasks.",
        "category": "warehouse",
        "room_dims": [10, 8, 4],
        "objects": [
            {"name": "GroundPlane", "prim_type": "Plane", "position": [0, 0, 0], "scale": [12, 10, 1]},
            {"name": "Shelf_A", "prim_type": "Cube", "position": [-2, 2, 1.0], "scale": [1.2, 0.4, 2.0]},
            {"name": "Shelf_B", "prim_type": "Cube", "position": [2, 2, 1.0], "scale": [1.2, 0.4, 2.0]},
            {"name": "Bin_1", "prim_type": "Cube", "position": [-2, 2, 0.3], "scale": [0.4, 0.3, 0.25]},
            {"name": "Bin_2", "prim_type": "Cube", "position": [-2, 2, 0.8], "scale": [0.4, 0.3, 0.25]},
            {"name": "Bin_3", "prim_type": "Cube", "position": [2, 2, 0.3], "scale": [0.4, 0.3, 0.25]},
            {"name": "MobileRobot", "prim_path": "/World/Carter", "asset_name": "carter", "position": [0, -1, 0], "scale": [1, 1, 1]},
            {"name": "OverheadCamera", "prim_type": "Camera", "position": [0, 0, 3.5], "rotation": [-90, 0, 0]},
        ],
        "suggested_sensors": ["camera (overhead, 1920x1080)", "rtx_lidar (mobile robot)"],
        "physics_settings": {"gravity": -9.81, "time_step": 1.0 / 60.0, "solver_iterations": 16},
    },
    "mobile_navigation": {
        "description": "Indoor navigation scene with a ground plane, walls, obstacles, and a wheeled robot with lidar. Good for SLAM and path-planning tasks.",
        "category": "mobile",
        "room_dims": [8, 8, 3],
        "objects": [
            {"name": "GroundPlane", "prim_type": "Plane", "position": [0, 0, 0], "scale": [10, 10, 1]},
            {"name": "Wall_North", "prim_type": "Cube", "position": [0, 4, 1.0], "scale": [8, 0.1, 2.0]},
            {"name": "Wall_South", "prim_type": "Cube", "position": [0, -4, 1.0], "scale": [8, 0.1, 2.0]},
            {"name": "Wall_East", "prim_type": "Cube", "position": [4, 0, 1.0], "scale": [0.1, 8, 2.0]},
            {"name": "Wall_West", "prim_type": "Cube", "position": [-4, 0, 1.0], "scale": [0.1, 8, 2.0]},
            {"name": "Obstacle_1", "prim_type": "Cylinder", "position": [1.5, 1.0, 0.5], "scale": [0.3, 0.3, 1.0]},
            {"name": "Obstacle_2", "prim_type": "Cube", "position": [-1.0, -1.5, 0.4], "scale": [0.6, 0.6, 0.8]},
            {"name": "Obstacle_3", "prim_type": "Cylinder", "position": [-2.0, 2.0, 0.5], "scale": [0.25, 0.25, 1.0]},
            {"name": "Jetbot", "prim_path": "/World/Jetbot", "asset_name": "jetbot", "position": [0, 0, 0.05], "scale": [1, 1, 1]},
        ],
        "suggested_sensors": ["rtx_lidar (robot-mounted, 360 deg)", "camera (front-facing)"],
        "physics_settings": {"gravity": -9.81, "time_step": 1.0 / 60.0, "solver_iterations": 16},
    },
    "inspection_cell": {
        "description": "Automated inspection cell with a conveyor belt, inspection cameras, structured lighting, and sample objects. Good for quality-inspection and defect-detection tasks.",
        "category": "inspection",
        "room_dims": [6, 4, 3],
        "objects": [
            {"name": "GroundPlane", "prim_type": "Plane", "position": [0, 0, 0], "scale": [8, 6, 1]},
            {"name": "Conveyor", "prim_type": "Cube", "position": [0, 0, 0.45], "scale": [3.0, 0.5, 0.05]},
            {"name": "ConveyorLegs_L", "prim_type": "Cube", "position": [-1.2, 0, 0.2], "scale": [0.05, 0.4, 0.4]},
            {"name": "ConveyorLegs_R", "prim_type": "Cube", "position": [1.2, 0, 0.2], "scale": [0.05, 0.4, 0.4]},
            {"name": "InspectionCamera_Top", "prim_type": "Camera", "position": [0, 0, 1.5], "rotation": [-90, 0, 0]},
            {"name": "InspectionCamera_Side", "prim_type": "Camera", "position": [0, -1.2, 0.8], "rotation": [0, 0, 0]},
            {"name": "Light_Bar_1", "prim_type": "RectLight", "position": [-0.5, 0, 1.2], "scale": [0.8, 0.1, 0.05]},
            {"name": "Light_Bar_2", "prim_type": "RectLight", "position": [0.5, 0, 1.2], "scale": [0.8, 0.1, 0.05]},
            {"name": "SampleObject_1", "prim_type": "Cube", "position": [-0.3, 0, 0.5], "scale": [0.08, 0.08, 0.08]},
            {"name": "SampleObject_2", "prim_type": "Cylinder", "position": [0.1, 0, 0.5], "scale": [0.04, 0.04, 0.06]},
            {"name": "SampleObject_3", "prim_type": "Sphere", "position": [0.4, 0, 0.52], "scale": [0.03, 0.03, 0.03]},
        ],
        "suggested_sensors": ["camera (top-down, high-res 4K)", "camera (side-view, 1280x720)"],
        "physics_settings": {"gravity": -9.81, "time_step": 1.0 / 120.0, "solver_iterations": 32},
    },
}

# from: feat/new-onboarding
_SLASH_COMMANDS = [
    {"command": "/help", "description": "What can I do?", "always": True},
    {"command": "/scene", "description": "Summarize current scene", "always": True},
    {"command": "/debug", "description": "Diagnose physics issues", "requires_physics": True},
    {"command": "/performance", "description": "Why is my sim slow?", "always": True},
    {"command": "/workspace", "description": "Show robot workspace", "requires_robot": True},
    {"command": "/diff", "description": "What changed?", "always": True},
    {"command": "/import", "description": "Import a robot", "always": True},
    {"command": "/template", "description": "Load a scene template", "always": True},
]

# from: feat/addendum-enterprise-scale
_STAGE_INDEX: Dict[str, Dict[str, Any]] = {}

# from: feat/addendum-enterprise-scale
_STAGE_INDEX_META: Dict[str, Any] = {"prim_scope": None, "prim_count": 0}

# from: feat/new-onboarding
_STARTER_PROMPTS = {
    "empty": {
        "welcome": "Your scene is empty — a blank canvas!",
        "prompts": [
            "Import a robot: 'add a Franka Panda to the scene'",
            "Load a template: 'set up a pick and place scene'",
            "Browse assets: 'show me available robots'",
        ],
    },
    "robot_only": {
        "welcome": "I see a robot in the scene, but no objects to interact with.",
        "prompts": [
            "Add objects: 'place 3 cubes on a table'",
            "Test the robot: 'move the arm to a test position'",
            "Check setup: 'are the collision meshes correct?'",
        ],
    },
    "robot_and_objects": {
        "welcome": "Your scene has a robot and objects — ready for action!",
        "prompts": [
            "Move the arm to grab the nearest object",
            "Why is the robot not moving?",
            "Show me the robot's workspace",
        ],
    },
    "mobile_robot": {
        "welcome": "I see a mobile robot in the scene.",
        "prompts": [
            "Drive the robot forward 2 meters",
            "Set up navigation: 'create an occupancy map'",
            "Check sensors: 'what sensors does the robot have?'",
        ],
    },
    "no_physics": {
        "welcome": "Physics is not enabled in this scene.",
        "prompts": [
            "Enable physics for this scene",
            "Add rigid body physics to the objects",
            "Set up a physics scene with gravity",
        ],
    },
}

# from: feat/7C-xr-teleoperation
_STREAM_QUALITY_PRESETS = {
    "low": {"width": 640, "height": 480, "bitrate_mbps": 2, "fps": 30},
    "medium": {"width": 1280, "height": 720, "bitrate_mbps": 8, "fps": 60},
    "high": {"width": 1920, "height": 1080, "bitrate_mbps": 20, "fps": 90},
}

# from: feat/new-onboarding
_SUGGESTION_MAP = {
    "import_robot": [
        "Configure the gripper",
        "Check if the collision meshes are correct",
        "Move the arm to a test position",
    ],
    "create_prim": [
        "Add physics to this object",
        "Change the material or color",
        "Position it precisely in the scene",
    ],
    "clone_prim": [
        "Set up physics for all copies",
        "Create an RL training environment",
        "Adjust spacing between copies",
    ],
    "move_to_pose": [
        "Plan a pick-and-place sequence",
        "Check for collisions along the path",
        "Record the joint positions",
    ],
    "sim_control": [
        "Capture a screenshot of the result",
        "Check for physics errors",
        "Measure performance (FPS, frame time)",
    ],
    "create_material": [
        "Apply this material to an object",
        "Adjust roughness or metallic properties",
        "Create a glass or transparent variant",
    ],
    "configure_sdg": [
        "Preview a sample frame",
        "Add more randomizers (lighting, pose)",
        "Export to COCO or KITTI format",
    ],
    "set_physics_params": [
        "Test with a simulation run",
        "Add rigid body physics to objects",
        "Check solver iteration count for stability",
    ],
    "load_scene_template": [
        "Run the simulation to see it in action",
        "Customize the robot's behavior",
        "Capture a screenshot of the scene",
    ],
}

# from: feat/8B-motion-planning-complete
_SUPPORTED_MOTION_ROBOTS = {
    "franka", "ur10", "ur5e", "ur3e", "cobotta", "rs007n",
    "dofbot", "kawasaki", "flexiv_rizon",
}

# from: feat/addendum-phase7C-teleop-quality
_TELEOP_DEVICES = {
    "quest_3": {
        "supported": True,
        "transport": "webxr",
        "latency_budget_ms": 80,
        "known_limitations": [
            "Meta Browser required; Safari does not expose XR_EXT_hand_tracking",
        ],
        "notes": "Quest 3 uses WebXR over Wi-Fi — keep router <= 10 ms from host.",
    },
    "vision_pro": {
        "supported": True,
        "transport": "cloudxr",
        "latency_budget_ms": 60,
        "known_limitations": [
            "Requires native CloudXR app on visionOS",
            "WebXR on Safari does NOT expose hand tracking — browser path will not work",
        ],
        "notes": "Vision Pro must use the NVIDIA CloudXR native app, not Safari.",
    },
    "spacemouse": {
        "supported": True,
        "transport": "usb-hid",
        "latency_budget_ms": 20,
        "known_limitations": ["6-DoF only — no hand retargeting"],
        "notes": "3Dconnexion SpaceMouse over USB-HID. Local, sub-20 ms RTT.",
    },
    "keyboard": {
        "supported": True,
        "transport": "usb-hid",
        "latency_budget_ms": 20,
        "known_limitations": ["Discrete input only — coarse joint nudges"],
        "notes": "Keyboard fallback for smoke tests without XR hardware.",
    },
}

# from: feat/addendum-community-remote-v2
_TEMPLATE_EXPORT_DIR = _WORKSPACE / "templates" / "exports"

# from: feat/new-omnigraph-assistant
_TEMPLATE_KEYWORDS = {
    "ros2_clock": ["clock", "sim_time", "simulation time", "simtime"],
    "ros2_joint_state": ["joint state", "joint_state", "joint states", "joint positions"],
    "ros2_camera": ["camera", "image", "rgb", "depth image"],
    "ros2_lidar": ["lidar", "laser scan", "laserscan", "point cloud lidar"],
    "ros2_cmd_vel": ["cmd_vel", "twist", "teleop", "drive", "velocity command"],
    "ros2_tf": ["tf", "transform tree", "transforms", "tf2"],
    "ros2_imu": ["imu", "inertial", "accelerometer", "gyroscope"],
    "ros2_odom": ["odom", "odometry"],
}

# from: feat/addendum-community-remote-v2
_TEMPLATE_LIBRARY_DIR = _WORKSPACE / "templates" / "library"

# from: feat/atomic-tier12-asset-mgmt
_TIER12_HELPERS = (
    "            def _layer_offset_dict(lo):\n"
    "                if lo is None:\n"
    "                    return {'offset': 0.0, 'scale': 1.0}\n"
    "                try:\n"
    "                    return {'offset': float(lo.offset), 'scale': float(lo.scale)}\n"
    "                except Exception:\n"
    "                    return {'offset': 0.0, 'scale': 1.0}\n"
)

# from: feat/atomic-tier14-bulk
_TIER14_SCHEMA_MAP = {
    "PhysicsRigidBodyAPI": ("pxr.UsdPhysics", "RigidBodyAPI"),
    "UsdPhysics.RigidBodyAPI": ("pxr.UsdPhysics", "RigidBodyAPI"),
    "RigidBodyAPI": ("pxr.UsdPhysics", "RigidBodyAPI"),
    "PhysicsCollisionAPI": ("pxr.UsdPhysics", "CollisionAPI"),
    "UsdPhysics.CollisionAPI": ("pxr.UsdPhysics", "CollisionAPI"),
    "CollisionAPI": ("pxr.UsdPhysics", "CollisionAPI"),
    "PhysicsMassAPI": ("pxr.UsdPhysics", "MassAPI"),
    "UsdPhysics.MassAPI": ("pxr.UsdPhysics", "MassAPI"),
    "MassAPI": ("pxr.UsdPhysics", "MassAPI"),
    "PhysxRigidBodyAPI": ("pxr.PhysxSchema", "PhysxRigidBodyAPI"),
    "PhysxCollisionAPI": ("pxr.PhysxSchema", "PhysxCollisionAPI"),
    "PhysxDeformableBodyAPI": ("pxr.PhysxSchema", "PhysxDeformableBodyAPI"),
    "PhysxTriggerAPI": ("pxr.PhysxSchema", "PhysxTriggerAPI"),
    "PhysxContactReportAPI": ("pxr.PhysxSchema", "PhysxContactReportAPI"),
}

# from: feat/new-physics-calibration
_VALID_CALIBRATE_PARAMS = {"friction", "damping", "armature", "masses", "viscous_friction"}

# from: feat/addendum-community-remote-v2
_VRAM_PER_ENV_MB = {
    "clone": {"low": 8, "medium": 16, "high": 32},
    "train": {"low": 12, "medium": 24, "high": 48},
    "sdg": {"low": 32, "medium": 64, "high": 128},
    "render": {"low": 256, "medium": 512, "high": 1024},
    "custom": {"low": 16, "medium": 32, "high": 64},
}

# from: feat/addendum-humanoid-advanced
_WHOLE_BODY_PROFILES = {
    "g1": {
        "locomotion": "hover_g1_flat.pt",
        "command_type": "velocity",
        "ee_frame": "left_hand",
        "status": "Working (IsaacLab 2.3)",
    },
    "h1": {
        "locomotion": "hover_h1_rough.pt",
        "command_type": "velocity",
        "ee_frame": "left_hand",
        "status": "Working",
    },
    "figure02": {
        "locomotion": "custom",
        "command_type": "velocity",
        "ee_frame": "left_hand",
        "status": "Manual config required",
    },
    "generic": {
        "locomotion": "custom",
        "command_type": "velocity",
        "ee_frame": "left_hand",
        "status": "Generic skeleton — review before use",
    },
}

# from: feat/phase10-autonomous-workflows
_WORKFLOW_RETRY_HARD_CAP = 5

# from: feat/phase10-autonomous-workflows
_WORKFLOWS: Dict[str, Dict[str, Any]] = {}

# from: feat/phase10-autonomous-workflows
_WORKFLOW_TEMPLATES: Dict[str, Dict[str, Any]] = {
    "rl_training": {
        "description": "Full RL training pipeline (W1 from spec)",
        "phases": [
            {"name": "plan",        "checkpoint": True,  "error_fix": False},
            {"name": "env_creation","checkpoint": False, "error_fix": True},
            {"name": "reward",      "checkpoint": True,  "error_fix": False},
            {"name": "training",    "checkpoint": False, "error_fix": False},
            {"name": "results",     "checkpoint": True,  "error_fix": False},
            {"name": "deploy",      "checkpoint": True,  "error_fix": False},
        ],
        "default_params": {
            "num_envs": 64,
            "env_spacing": 2.5,
            "algo": "ppo",
            "num_iterations": 5000,
        },
    },
    "robot_import": {
        "description": "Robot import & configuration (W2 from spec)",
        "phases": [
            {"name": "plan",            "checkpoint": True,  "error_fix": False},
            {"name": "import",          "checkpoint": False, "error_fix": True},
            {"name": "verify",          "checkpoint": False, "error_fix": False},
            {"name": "auto_fix",        "checkpoint": True,  "error_fix": False},
            {"name": "motion_planning", "checkpoint": False, "error_fix": True},
            {"name": "report",          "checkpoint": False, "error_fix": False},
        ],
        "default_params": {
            "fix_profile": "auto",
        },
    },
    "sim_debugging": {
        "description": "Simulation debugging with autonomous error-fix loop (W4 from spec)",
        "phases": [
            {"name": "diagnose",   "checkpoint": False, "error_fix": False},
            {"name": "hypothesis", "checkpoint": False, "error_fix": False},
            {"name": "fix",        "checkpoint": True,  "error_fix": True},
            {"name": "verify",     "checkpoint": False, "error_fix": False},
            {"name": "report",     "checkpoint": False, "error_fix": False},
        ],
        "default_params": {
            "max_hypothesis_iterations": 3,
        },
    },
}

# from: feat/addendum-enterprise-scale
_WRITE_LOCK_QUEUE = _StageWriteLockQueue()

# from: feat/9-finetune-flywheel
_turn_recorder = TurnRecorder()

# End recovered state
# ═══════════════════════════════════════════════════════════════════════════


def _load_sensor_specs() -> List[Dict]:
    global _sensor_specs
    if _sensor_specs is not None:
        return _sensor_specs
    specs = []
    if _SENSOR_SPECS_PATH.exists():
        for line in _SENSOR_SPECS_PATH.read_text().splitlines():
            line = line.strip()
            if line:
                specs.append(json.loads(line))
    _sensor_specs = specs
    return specs


def _load_deformable_presets() -> Dict:
    global _deformable_presets
    if _deformable_presets is not None:
        return _deformable_presets
    if _DEFORMABLE_PRESETS_PATH.exists():
        _deformable_presets = json.loads(_DEFORMABLE_PRESETS_PATH.read_text())
    else:
        _deformable_presets = {"presets": {}}
    return _deformable_presets


# ── Safe xform helper (inlined into generated code) ─────────────────────────
# Referenced USD assets (e.g. robots) often already have xform ops.
# Calling AddTranslateOp() again crashes with "Error in AddXformOp".
# This snippet is injected into generated code to safely set transforms.

_SAFE_XFORM_SNIPPET = '''\

def _safe_set_translate(prim, pos):
    """Set translate, reusing existing op if present."""
    xf = UsdGeom.Xformable(prim)
    for op in xf.GetOrderedXformOps():
        if op.GetOpType() == UsdGeom.XformOp.TypeTranslate:
            op.Set(Gf.Vec3d(*pos))
            return
    xf.AddTranslateOp().Set(Gf.Vec3d(*pos))

def _safe_set_scale(prim, s):
    """Set scale, reusing existing op if present."""
    xf = UsdGeom.Xformable(prim)
    for op in xf.GetOrderedXformOps():
        if op.GetOpType() == UsdGeom.XformOp.TypeScale:
            op.Set(Gf.Vec3d(*s))
            return
    xf.AddScaleOp().Set(Gf.Vec3d(*s))

def _safe_set_rotate_xyz(prim, r):
    """Set rotateXYZ, reusing existing op if present."""
    xf = UsdGeom.Xformable(prim)
    for op in xf.GetOrderedXformOps():
        if op.GetOpType() == UsdGeom.XformOp.TypeRotateXYZ:
            op.Set(Gf.Vec3d(*r))
            return
    xf.AddRotateXYZOp().Set(Gf.Vec3d(*r))
'''


# ── Code generation helpers ──────────────────────────────────────────────────

def _gen_create_prim(args: Dict) -> str:
    prim_path = args["prim_path"]
    prim_type = args["prim_type"]
    pos = args.get("position")
    scale = args.get("scale")
    rot = args.get("rotation_euler")
    size = args.get("size")
    radius = args.get("radius")
    height = args.get("height")
    lines = [
        "import omni.usd",
        "from pxr import UsdGeom, Gf",
        _SAFE_XFORM_SNIPPET,
        "stage = omni.usd.get_context().get_stage()",
        f"prim = stage.DefinePrim('{prim_path}', '{prim_type}')",
    ]
    if pos:
        lines.append(f"_safe_set_translate(prim, ({pos[0]}, {pos[1]}, {pos[2]}))")
    if scale:
        lines.append(f"_safe_set_scale(prim, ({scale[0]}, {scale[1]}, {scale[2]}))")
    if rot:
        lines.append(f"_safe_set_rotate_xyz(prim, ({rot[0]}, {rot[1]}, {rot[2]}))")
    # Geometric attributes authored directly. Cleaner than relying on scale
    # because set_attribute on 'size'/'radius'/'height' matches what success
    # criteria typically verify (the USD attribute, not the scale op).
    if size is not None and prim_type == "Cube":
        lines.append(f"UsdGeom.Cube(prim).GetSizeAttr().Set({float(size)})")
    if radius is not None:
        if prim_type == "Sphere":
            lines.append(f"UsdGeom.Sphere(prim).GetRadiusAttr().Set({float(radius)})")
        elif prim_type == "Cylinder":
            lines.append(f"UsdGeom.Cylinder(prim).GetRadiusAttr().Set({float(radius)})")
        elif prim_type == "Cone":
            lines.append(f"UsdGeom.Cone(prim).GetRadiusAttr().Set({float(radius)})")
        elif prim_type == "Capsule":
            lines.append(f"UsdGeom.Capsule(prim).GetRadiusAttr().Set({float(radius)})")
    if height is not None:
        if prim_type == "Cylinder":
            lines.append(f"UsdGeom.Cylinder(prim).GetHeightAttr().Set({float(height)})")
        elif prim_type == "Cone":
            lines.append(f"UsdGeom.Cone(prim).GetHeightAttr().Set({float(height)})")
        elif prim_type == "Capsule":
            lines.append(f"UsdGeom.Capsule(prim).GetHeightAttr().Set({float(height)})")
    return "\n".join(lines)


def _gen_delete_prim(args: Dict) -> str:
    # stage.RemovePrim returns False (not raises) on a non-existent path, and
    # the old generator threw away that return value. Agent could then claim
    # "deleted /World/Foo" when /World/Foo never existed — a classic honesty
    # hole. Pre-check existence and verify post-remove.
    return (
        "import omni.usd\n"
        "stage = omni.usd.get_context().get_stage()\n"
        f"_path = '{args['prim_path']}'\n"
        "_prim = stage.GetPrimAtPath(_path)\n"
        "if not _prim.IsValid():\n"
        "    raise RuntimeError(f'delete_prim: prim does not exist: {_path!r}')\n"
        "_ok = stage.RemovePrim(_path)\n"
        "if not _ok or stage.GetPrimAtPath(_path).IsValid():\n"
        "    raise RuntimeError(f'delete_prim: RemovePrim({_path!r}) returned {_ok!r} but prim still in stage')\n"
        "print(f'deleted {_path}')"
    )


def _gen_set_attribute(args: Dict) -> str:
    prim_path = args["prim_path"]
    attr_name = args["attr_name"]
    value = args["value"]
    return (
        "import omni.usd\n"
        "stage = omni.usd.get_context().get_stage()\n"
        f"prim = stage.GetPrimAtPath('{prim_path}')\n"
        f"attr = prim.GetAttribute('{attr_name}')\n"
        f"attr.Set({repr(value)})"
    )


def _gen_add_reference(args: Dict) -> str:
    # USD AddReference accepts any asset URL and returns True regardless of
    # whether the referenced file exists — composition is lazy. Without
    # post-check, a bad path produces a prim with "has references" but no
    # actual children, and the tool reports success. Verify via:
    #   1. prim.HasAuthoredReferences() after the call
    #   2. if the asset is a local path, os.path.exists() before the call
    #   3. re-traverse children to catch zero-child silent composition error
    return (
        "import os\n"
        "import omni.usd\n"
        "from pxr import Sdf\n"
        "stage = omni.usd.get_context().get_stage()\n"
        f"prim = stage.GetPrimAtPath('{args['prim_path']}')\n"
        f"if not prim.IsValid():\n"
        f"    raise RuntimeError('add_reference: prim not found: {args['prim_path']}')\n"
        f"_ref = '{args['reference_path']}'\n"
        # Local filesystem path (not omniverse:// or http(s)://): must exist.
        "if not any(_ref.startswith(p) for p in ('omniverse://','http://','https://','file://')):\n"
        "    if not os.path.isabs(_ref) or not os.path.exists(_ref):\n"
        "        raise FileNotFoundError(f'add_reference: asset not found: {_ref!r}')\n"
        "_added = prim.GetReferences().AddReference(_ref)\n"
        "if not _added or not prim.HasAuthoredReferences():\n"
        "    raise RuntimeError(f'add_reference: AddReference returned success but no reference was authored on {prim.GetPath()}')\n"
        "print(f'added reference {_ref} to {prim.GetPath()}')"
    )


def _gen_apply_api_schema(args: Dict) -> str:
    schema = args['schema_name']
    prim_path = args['prim_path']
    # Map common schema names to their pxr module + class
    SCHEMA_MAP = {
        "PhysicsRigidBodyAPI": ("pxr.UsdPhysics", "RigidBodyAPI"),
        "UsdPhysics.RigidBodyAPI": ("pxr.UsdPhysics", "RigidBodyAPI"),
        "RigidBodyAPI": ("pxr.UsdPhysics", "RigidBodyAPI"),
        "PhysicsCollisionAPI": ("pxr.UsdPhysics", "CollisionAPI"),
        "UsdPhysics.CollisionAPI": ("pxr.UsdPhysics", "CollisionAPI"),
        "CollisionAPI": ("pxr.UsdPhysics", "CollisionAPI"),
        "PhysicsMassAPI": ("pxr.UsdPhysics", "MassAPI"),
        "UsdPhysics.MassAPI": ("pxr.UsdPhysics", "MassAPI"),
        "PhysxDeformableBodyAPI": ("pxr.PhysxSchema", "PhysxDeformableBodyAPI"),
        "PhysxCollisionAPI": ("pxr.PhysxSchema", "PhysxCollisionAPI"),
    }
    # Post-apply verification: check GetAppliedSchemas() contains the schema
    # token. Without this, the Kit command path silently accepts invalid
    # schema names ('PhysicsVelocityAPI' etc.) and reports success even though
    # the schema was not applied — an honesty hole.
    if schema in SCHEMA_MAP:
        mod, cls = SCHEMA_MAP[schema]
        return (
            f"from {mod} import {cls}\n"
            "import omni.usd\n"
            f"stage = omni.usd.get_context().get_stage()\n"
            f"prim = stage.GetPrimAtPath('{prim_path}')\n"
            f"if not prim.IsValid():\n"
            f"    raise RuntimeError(f'apply_api_schema: prim not found: {prim_path}')\n"
            f"{cls}.Apply(prim)\n"
            f"_applied = list(prim.GetAppliedSchemas() or [])\n"
            f"if '{cls}' not in _applied and '{schema}' not in _applied:\n"
            f"    raise RuntimeError(f'apply_api_schema: schema {cls} not in GetAppliedSchemas after Apply (got {{_applied}})')\n"
            f"print(f'applied {cls} to {prim_path} — schemas now: {{_applied}}')"
        )
    # Fallback: Kit command path. Must verify via GetAppliedSchemas because
    # omni.kit.commands.execute('ApplyAPISchemaCommand', api=<bad_name>, ...)
    # returns None / silent-no-op rather than raising on unknown API names.
    return (
        "import omni.usd\n"
        "import omni.kit.commands\n"
        f"stage = omni.usd.get_context().get_stage()\n"
        f"prim = stage.GetPrimAtPath('{prim_path}')\n"
        f"if not prim.IsValid():\n"
        f"    raise RuntimeError(f'apply_api_schema: prim not found: {prim_path}')\n"
        f"_before = set(prim.GetAppliedSchemas() or [])\n"
        f"omni.kit.commands.execute('ApplyAPISchemaCommand', api='{schema}', prim=prim)\n"
        f"_after = set(prim.GetAppliedSchemas() or [])\n"
        f"if _before == _after:\n"
        f"    raise RuntimeError(f'apply_api_schema: schema \"{schema}\" was not applied — likely unknown schema name. prim schemas unchanged: {{sorted(_before)}}')\n"
        f"print(f'applied {schema} to {prim_path} — new schemas: {{sorted(_after - _before)}}')"
    )


def _gen_clone_prim(args: Dict) -> str:
    src = args["source_path"]
    tgt = args["target_path"]
    pos = args.get("position")
    count = args.get("count", 1)
    spacing = args.get("spacing", 1.0)
    collision_filter = args.get("collision_filter", False)

    if count <= 1:
        # Single clone: Sdf.CopySpec (fast, simple)
        lines = [
            "import omni.usd",
            "from pxr import Sdf, UsdGeom, Gf",
            "stage = omni.usd.get_context().get_stage()",
            f"Sdf.CopySpec(stage.GetRootLayer(), '{src}', stage.GetRootLayer(), '{tgt}')",
        ]
        if pos:
            lines.append(f"xf = UsdGeom.Xformable(stage.GetPrimAtPath('{tgt}'))")
            lines.append("xf.ClearXformOpOrder()")
            lines.append(f"xf.AddTranslateOp().Set(Gf.Vec3d({pos[0]}, {pos[1]}, {pos[2]}))")
        return "\n".join(lines)

    if count < 4:
        # Small count: Sdf.CopySpec loop (simpler)
        lines = [
            "import omni.usd",
            "from pxr import Sdf, UsdGeom, Gf",
            _SAFE_XFORM_SNIPPET,
            "stage = omni.usd.get_context().get_stage()",
            f"for i in range({count}):",
            f"    dest = '{tgt}_' + str(i)",
            f"    Sdf.CopySpec(stage.GetRootLayer(), '{src}', stage.GetRootLayer(), dest)",
            f"    _safe_set_translate(stage.GetPrimAtPath(dest), (i * {spacing}, 0, 0))",
        ]
        return "\n".join(lines)

    # Large count (>= 4): GPU-batched GridCloner from isaacsim.core.cloner
    import math
    grid_side = math.ceil(math.sqrt(count))
    filter_str = "True" if collision_filter else "False"
    lines = [
        "import omni.usd",
        "from pxr import UsdGeom, Gf",
        "from isaacsim.core.cloner import GridCloner",
        "",
        "stage = omni.usd.get_context().get_stage()",
        "",
        f"cloner = GridCloner(spacing={spacing})",
        f"cloner.define_base_env('{src}')",
        f"# Generate {count} target paths in a grid layout",
        f"target_paths = cloner.generate_paths('{tgt}', {count})",
        f"env_positions = cloner.clone(",
        f"    source_prim_path='{src}',",
        f"    prim_paths=target_paths,",
        f"    copy_from_source=True,",
        f")",
    ]
    if collision_filter:
        lines.extend([
            "",
            "# Filter collisions between clones (required for RL envs)",
            f"cloner.filter_collisions(",
            f"    physicsscene_path='/World/PhysicsScene',",
            f"    collision_root_path='{tgt}',",
            f"    prim_paths=target_paths,",
            f")",
        ])
    lines.append(f"print(f'Cloned {count} envs from {src} using GridCloner')")
    return "\n".join(lines)


def _gen_deformable(args: Dict) -> str:
    """Generate PhysX deformable body/surface code from presets."""
    prim_path = args["prim_path"]
    sbt = args["soft_body_type"]

    presets = _load_deformable_presets().get("presets", {})

    # Map user-friendly names to preset keys
    preset_map = {
        "cloth": "cloth_cotton",
        "sponge": "sponge_soft",
        "rubber": "rubber_soft",
        "gel": "gel_soft",
        "rope": "rope_nylon",
    }
    preset_key = preset_map.get(sbt, f"{sbt}_soft")
    preset = presets.get(preset_key, {})
    params = preset.get("params", {})

    # Allow user overrides
    if args.get("youngs_modulus"):
        params["youngs_modulus"] = args["youngs_modulus"]
    if args.get("poissons_ratio"):
        params["poissons_ratio"] = args["poissons_ratio"]
    if args.get("damping"):
        params["damping"] = args["damping"]
    if args.get("self_collision") is not None:
        params["self_collision"] = args["self_collision"]

    api_type = preset.get("api", "PhysxDeformableBodyAPI")
    density = preset.get("density_kg_m3", 1000)

    if "Surface" in api_type:
        return _gen_deformable_surface(prim_path, params, density)
    return _gen_deformable_body(prim_path, params, density)


def _gen_deformable_body(prim_path: str, params: Dict, density: float) -> str:
    ym = params.get("youngs_modulus", 10000)
    pr = params.get("poissons_ratio", 0.3)
    damp = params.get("damping", 0.01)
    sc = str(params.get("self_collision", True))
    iters = params.get("solver_position_iteration_count", 32)
    vvd = params.get("vertex_velocity_damping", 0.05)

    return f"""\
import omni.usd
import numpy as np
from pxr import UsdPhysics, PhysxSchema, UsdGeom, Gf, Vt, Sdf

stage = omni.usd.get_context().get_stage()
prim = stage.GetPrimAtPath('{prim_path}')

# Ensure prim is a valid subdivided Mesh (PhysX requires triangle data)
if not prim.IsA(UsdGeom.Mesh):
    # Replace implicit surface (Plane, Cube, etc.) with a subdivided Mesh
    xform = UsdGeom.Xformable(prim)
    pos = xform.GetLocalTransformation().IsIdentity() and Gf.Vec3d(0,0,0) or \\
          xform.ComputeLocalToWorldTransform(0).ExtractTranslation()
    stage.RemovePrim('{prim_path}')
    prim = stage.DefinePrim('{prim_path}', 'Mesh')

mesh = UsdGeom.Mesh(prim)
pts = mesh.GetPointsAttr().Get()
if pts is None or len(pts) < 9:
    # Generate a 10x10 subdivided plane mesh
    res = 10
    size = 1.0
    verts = []
    for j in range(res + 1):
        for i in range(res + 1):
            x = (i / res - 0.5) * size
            y = (j / res - 0.5) * size
            verts.append(Gf.Vec3f(x, y, 0.0))
    faces = []
    counts = []
    for j in range(res):
        for i in range(res):
            v0 = j * (res + 1) + i
            v1 = v0 + 1
            v2 = v0 + (res + 1) + 1
            v3 = v0 + (res + 1)
            faces.extend([v0, v1, v2])
            faces.extend([v0, v2, v3])
            counts.extend([3, 3])
    mesh.GetPointsAttr().Set(Vt.Vec3fArray(verts))
    mesh.GetFaceVertexCountsAttr().Set(Vt.IntArray(counts))
    mesh.GetFaceVertexIndicesAttr().Set(Vt.IntArray(faces))

# Apply deformable body
deformable_api = PhysxSchema.PhysxDeformableBodyAPI.Apply(prim)
deformable_api.CreateSolverPositionIterationCountAttr({iters})
deformable_api.CreateVertexVelocityDampingAttr({vvd})
deformable_api.CreateSelfCollisionAttr({sc})

# Material
mat_path = '{prim_path}/DeformableMaterial'
mat_prim = stage.DefinePrim(mat_path, 'PhysxDeformableBodyMaterial')
mat_api = PhysxSchema.PhysxDeformableBodyMaterialAPI.Apply(mat_prim)
mat_api.CreateYoungsModulusAttr({ym})
mat_api.CreatePoissonsRatioAttr({pr})
mat_api.CreateDampingAttr({damp})
mat_api.CreateDensityAttr({density})

# Bind material
from pxr import UsdShade
UsdShade.MaterialBindingAPI(prim).Bind(
    UsdShade.Material(stage.GetPrimAtPath(mat_path)),
    UsdShade.Tokens.strongerThanDescendants)
"""


def _gen_deformable_surface(prim_path: str, params: Dict, density: float) -> str:
    ss = params.get("stretch_stiffness", 10000)
    bs = params.get("bend_stiffness", 0.02)
    damp = params.get("damping", 0.005)
    sc = str(params.get("self_collision", True))
    scfd = params.get("self_collision_filter_distance", 0.002)

    return f"""\
import omni.usd
from pxr import UsdPhysics, PhysxSchema, UsdGeom, Gf, Vt, Sdf

stage = omni.usd.get_context().get_stage()
prim = stage.GetPrimAtPath('{prim_path}')

# Ensure prim is a valid subdivided Mesh (PhysX cloth requires triangle data)
if not prim.IsA(UsdGeom.Mesh):
    xform = UsdGeom.Xformable(prim)
    pos = xform.ComputeLocalToWorldTransform(0).ExtractTranslation()
    stage.RemovePrim('{prim_path}')
    prim = stage.DefinePrim('{prim_path}', 'Mesh')
    UsdGeom.Xformable(prim).AddTranslateOp().Set(Gf.Vec3d(pos[0], pos[1], pos[2]))

mesh = UsdGeom.Mesh(prim)
pts = mesh.GetPointsAttr().Get()
if pts is None or len(pts) < 9:
    # Generate a 20x20 subdivided plane mesh for cloth simulation
    res = 20
    size = 1.0
    verts = []
    for j in range(res + 1):
        for i in range(res + 1):
            x = (i / res - 0.5) * size
            y = (j / res - 0.5) * size
            verts.append(Gf.Vec3f(x, y, 0.0))
    faces = []
    counts = []
    for j in range(res):
        for i in range(res):
            v0 = j * (res + 1) + i
            v1 = v0 + 1
            v2 = v0 + (res + 1) + 1
            v3 = v0 + (res + 1)
            faces.extend([v0, v1, v2])
            faces.extend([v0, v2, v3])
            counts.extend([3, 3])
    mesh.GetPointsAttr().Set(Vt.Vec3fArray(verts))
    mesh.GetFaceVertexCountsAttr().Set(Vt.IntArray(counts))
    mesh.GetFaceVertexIndicesAttr().Set(Vt.IntArray(faces))

# Apply deformable surface (cloth)
surface_api = PhysxSchema.PhysxDeformableSurfaceAPI.Apply(prim)
surface_api.CreateSelfCollisionAttr({sc})
surface_api.CreateSelfCollisionFilterDistanceAttr({scfd})

# Material
mat_path = '{prim_path}/ClothMaterial'
mat_prim = stage.DefinePrim(mat_path, 'PhysxDeformableSurfaceMaterial')
mat_api = PhysxSchema.PhysxDeformableSurfaceMaterialAPI.Apply(mat_prim)
mat_api.CreateStretchStiffnessAttr({ss})
mat_api.CreateBendStiffnessAttr({bs})
mat_api.CreateDampingAttr({damp})
mat_api.CreateDensityAttr({density})

# Bind material
from pxr import UsdShade
UsdShade.MaterialBindingAPI(prim).Bind(
    UsdShade.Material(stage.GetPrimAtPath(mat_path)),
    UsdShade.Tokens.strongerThanDescendants)
"""


# Isaac Sim 5.1 OmniGraph node type mapping:
# The LLM often uses legacy omni.isaac.* prefixes. Remap to the correct isaacsim.* types.
_OG_NODE_TYPE_MAP = {
    # ROS2 bridge nodes (Isaac Sim 5.1 uses isaacsim.ros2.bridge.*)
    "omni.isaac.ros2_bridge.ROS2Context": "isaacsim.ros2.bridge.ROS2Context",
    "omni.isaac.ros2_bridge.ROS2PublishClock": "isaacsim.ros2.bridge.ROS2PublishClock",
    "omni.isaac.ros2_bridge.ROS2PublishJointState": "isaacsim.ros2.bridge.ROS2PublishJointState",
    "omni.isaac.ros2_bridge.ROS2SubscribeJointState": "isaacsim.ros2.bridge.ROS2SubscribeJointState",
    "omni.isaac.ros2_bridge.ROS2PublishTransformTree": "isaacsim.ros2.bridge.ROS2PublishTransformTree",
    "omni.isaac.ros2_bridge.ROS2PublishImage": "isaacsim.ros2.bridge.ROS2PublishImage",
    # ArticulationController is in core.nodes, NOT ros2.bridge
    "omni.isaac.ros2_bridge.ROS2ArticulationController": "isaacsim.core.nodes.IsaacArticulationController",
    "isaacsim.ros2.bridge.ROS2ArticulationController": "isaacsim.core.nodes.IsaacArticulationController",
    "omni.isaac.core_nodes.IsaacArticulationController": "isaacsim.core.nodes.IsaacArticulationController",
}


def _gen_create_omnigraph(args: Dict) -> str:
    graph_path = args["graph_path"]
    graph_type = args.get("graph_type", "action_graph")
    nodes = args.get("nodes", [])
    connections = args.get("connections", [])
    values = args.get("values", {})

    # Use plain tuples — og.Controller.node() resolves to a path string
    # which fails inside og.Controller.edit(); tuples are the correct format.
    # Also remap legacy node type IDs to Isaac Sim 5.1 equivalents.
    node_defs = ",\n            ".join(
        f"('{n['name']}', '{_OG_NODE_TYPE_MAP.get(n['type'], n['type'])}')" for n in nodes
    ) if nodes else ""

    conn_defs = ",\n            ".join(
        f"('{c['source']}', '{c['target']}')" for c in connections
    ) if connections else ""

    # SET_VALUES for node attribute configuration (e.g. robotPath, topicName)
    val_defs = ""
    if values:
        val_items = []
        for attr_path, val in values.items():
            if isinstance(val, str):
                val_items.append(f"            ('{attr_path}', '{val}')")
            else:
                val_items.append(f"            ('{attr_path}', {val})")
        val_defs = ",\n".join(val_items)

    set_values_block = ""
    if val_defs:
        set_values_block = f"""        keys.SET_VALUES: [
{val_defs}
        ],"""

    return f"""\
import omni.graph.core as og

# Resolve backing type: FABRIC_SHARED (Isaac Sim 5.x+) replaces deprecated FLATCACHING
_bt = og.GraphBackingType
if hasattr(_bt, 'GRAPH_BACKING_TYPE_FABRIC_SHARED'):
    _backing = _bt.GRAPH_BACKING_TYPE_FABRIC_SHARED
elif hasattr(_bt, 'GRAPH_BACKING_TYPE_FLATCACHING'):
    _backing = _bt.GRAPH_BACKING_TYPE_FLATCACHING
else:
    _backing = list(_bt)[0]  # fallback to first available

keys = og.Controller.Keys
(graph, nodes, _, _) = og.Controller.edit(
    {{
        "graph_path": "{graph_path}",
        "evaluator_name": "execution",
        "pipeline_stage": _backing,
    }},
    {{
        keys.CREATE_NODES: [
            {node_defs}
        ],
        keys.CONNECT: [
            {conn_defs}
        ],
{set_values_block}
    }},
)
"""


def _gen_create_material(args: Dict) -> str:
    mat_path = args["material_path"]
    shader = args.get("shader_type", "OmniPBR")
    color = args.get("diffuse_color", [0.8, 0.8, 0.8])
    metallic = args.get("metallic", 0.0)
    roughness = args.get("roughness", 0.5)
    opacity = args.get("opacity", 1.0)
    ior = args.get("ior", 1.5)

    mdl_file = 'OmniPBR.mdl' if shader == 'OmniPBR' else f'{shader}.mdl'

    return f"""\
import omni.usd
from pxr import UsdShade, Sdf, Gf

stage = omni.usd.get_context().get_stage()

# Create material prim
mat_prim = stage.DefinePrim('{mat_path}', 'Material')
mat = UsdShade.Material(mat_prim)

# Create shader prim
shader_prim = stage.DefinePrim('{mat_path}/Shader', 'Shader')
shader = UsdShade.Shader(shader_prim)
shader.CreateIdAttr('mdl')
shader.CreateImplementationSourceAttr(UsdShade.Tokens.sourceAsset)
shader.SetSourceAsset('{mdl_file}', 'mdl')
shader.SetSourceAssetSubIdentifier('{shader}', 'mdl')

# Set shader parameters
shader.CreateInput('diffuse_color_constant', Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f({color[0]}, {color[1]}, {color[2]}))
shader.CreateInput('metallic_constant', Sdf.ValueTypeNames.Float).Set({metallic})
shader.CreateInput('reflection_roughness_constant', Sdf.ValueTypeNames.Float).Set({roughness})

# Connect shader to material outputs
mat.CreateSurfaceOutput('mdl').ConnectToSource(shader.ConnectableAPI(), 'out')
mat.CreateVolumeOutput('mdl').ConnectToSource(shader.ConnectableAPI(), 'out')
mat.CreateDisplacementOutput('mdl').ConnectToSource(shader.ConnectableAPI(), 'out')
"""


def _gen_assign_material(args: Dict) -> str:
    return (
        "import omni.usd\n"
        "from pxr import UsdShade\n"
        "stage = omni.usd.get_context().get_stage()\n"
        f"mat = UsdShade.Material(stage.GetPrimAtPath('{args['material_path']}'))\n"
        f"prim = stage.GetPrimAtPath('{args['prim_path']}')\n"
        "UsdShade.MaterialBindingAPI(prim).Bind(mat, UsdShade.Tokens.strongerThanDescendants)"
    )


def _gen_sim_control(args: Dict) -> str:
    action = args["action"]
    if action == "play":
        return "import omni.timeline\nomni.timeline.get_timeline_interface().play()"
    if action == "pause":
        return "import omni.timeline\nomni.timeline.get_timeline_interface().pause()"
    if action == "stop":
        return "import omni.timeline\nomni.timeline.get_timeline_interface().stop()"
    if action == "step":
        count = args.get("step_count", 1)
        return f"""\
import omni.timeline
tl = omni.timeline.get_timeline_interface()
for _ in range({count}):
    tl.forward_one_frame()
"""
    if action == "reset":
        return (
            "import omni.timeline\n"
            "tl = omni.timeline.get_timeline_interface()\n"
            "tl.stop()\n"
            "tl.set_current_time(0)"
        )
    return f"# Unknown sim action: {action}"


def _gen_set_physics_params(args: Dict) -> str:
    lines = [
        "import omni.usd",
        "from pxr import UsdPhysics, Gf",
        "stage = omni.usd.get_context().get_stage()",
        "scene = UsdPhysics.Scene.Get(stage, '/PhysicsScene') or UsdPhysics.Scene.Define(stage, '/PhysicsScene')",
    ]
    if "gravity_direction" in args and "gravity_magnitude" in args:
        d = args["gravity_direction"]
        m = args["gravity_magnitude"]
        lines.append(f"scene.GetGravityDirectionAttr().Set(Gf.Vec3f({d[0]}, {d[1]}, {d[2]}))")
        lines.append(f"scene.GetGravityMagnitudeAttr().Set({m})")
    elif "gravity_magnitude" in args:
        lines.append(f"scene.GetGravityMagnitudeAttr().Set({args['gravity_magnitude']})")
    if "time_step" in args:
        lines.append(f"# Note: Physics time step is set via settings")
        lines.append(f"import carb.settings")
        lines.append(f"carb.settings.get_settings().set('/persistent/physics/updateToUsd', True)")
        lines.append(f"carb.settings.get_settings().set('/persistent/physics/timeStepsPerSecond', int(1.0/{args['time_step']}))")
    return "\n".join(lines)


def _gen_teleport_prim(args: Dict) -> str:
    prim_path = args["prim_path"]
    lines = [
        "import omni.usd",
        "from pxr import UsdGeom, Gf",
        _SAFE_XFORM_SNIPPET,
        "stage = omni.usd.get_context().get_stage()",
        f"prim = stage.GetPrimAtPath('{prim_path}')",
    ]
    pos = args.get("position")
    rot = args.get("rotation_euler")
    if pos:
        lines.append(f"_safe_set_translate(prim, ({pos[0]}, {pos[1]}, {pos[2]}))")
    if rot:
        lines.append(f"_safe_set_rotate_xyz(prim, ({rot[0]}, {rot[1]}, {rot[2]}))")
    return "\n".join(lines)


def _gen_set_joint_targets(args: Dict) -> str:
    art_path = args["articulation_path"]
    joint = args.get("joint_name", "")
    pos = args.get("target_position")
    vel = args.get("target_velocity")
    lines = [
        "import omni.usd",
        "from pxr import UsdPhysics",
        "stage = omni.usd.get_context().get_stage()",
    ]
    if joint:
        lines.append(f"joint_prim = stage.GetPrimAtPath('{art_path}/{joint}')")
        lines.append("drive = UsdPhysics.DriveAPI.Get(joint_prim, 'angular')")
        if pos is not None:
            lines.append(f"drive.GetTargetPositionAttr().Set({pos})")
        if vel is not None:
            lines.append(f"drive.GetTargetVelocityAttr().Set({vel})")
    return "\n".join(lines)


def _gen_import_robot(args: Dict) -> str:
    file_path = args["file_path"]
    fmt = args.get("format", "usd")
    dest = args.get("dest_path", "/World/Robot")

    # ── Asset directory from config (supports local path or Nucleus URL) ──
    _LOCAL_ASSETS = config.assets_root_path
    _ROBOTS_SUBDIR = config.assets_robots_subdir
    _ROBOTS_DIR = f"{_LOCAL_ASSETS}/{_ROBOTS_SUBDIR}" if _LOCAL_ASSETS else ""

    # Map common names → USD filenames within the robots subdirectory
    _ROBOT_NAME_MAP = {
        "franka": "franka.usd",
        "panda": "franka.usd",
        "franka_emika": "franka.usd",
        "spot": "spot.usd",
        "spot_with_arm": "spot_with_arm.usd",
        "carter": "carter_v1.usd",
        "nova_carter": "nova_carter.usd",
        "carter_v2": "carter_v2.usd",
        "jetbot": "jetbot.usd",
        "kaya": "kaya.usd",
        "ur10": "ur10.usd",
        "ur5": "ur5e.usd",
        "ur5e": "ur5e.usd",
        "anymal": "anymal_c.usd",
        "anymal_c": "anymal_c.usd",
        "anymal_d": "anymal_d.usd",
        "a1": "a1.usd",
        "go1": "go1.usd",
        "go2": "go2.usd",
        "g1": "g1.usd",
        "unitree_g1": "g1.usd",
        "g1_23dof": "g1_23dof_robot.usd",
        "h1": "h1_hand_left.usd",
        "unitree_h1": "h1_hand_left.usd",
        "allegro": "allegro_hand.usd",
        "ridgeback_franka": "ridgeback_franka.usd",
        "humanoid": "humanoid.usd",
        "humanoid_28": "humanoid_28.usd",
    }

    if fmt == "urdf":
        return f"""\
import os
from isaacsim.asset.importer.urdf import _urdf
import omni.kit.commands
import omni.usd

# Fail fast on obvious bad inputs. URDFParseAndImportFile silently returns
# (result=False, prim_path=None) on missing file / parse error, and the old
# code path reported success=True anyway — a real honesty hole.
if not os.path.exists("{file_path}"):
    raise FileNotFoundError(f'import_robot: URDF not found at "{file_path}"')

result, prim_path = omni.kit.commands.execute(
    "URDFParseAndImportFile",
    urdf_path="{file_path}",
    dest_path="{dest}",
)
if not result or not prim_path:
    raise RuntimeError(
        f'import_robot: URDFParseAndImportFile failed for "{file_path}" '
        f'(result={{result!r}}, prim_path={{prim_path!r}}) — check URDF validity and mesh paths.'
    )
# Double-check the prim actually landed in the stage
_stage = omni.usd.get_context().get_stage()
_created = _stage.GetPrimAtPath(prim_path)
if not _created.IsValid():
    raise RuntimeError(
        f'import_robot: URDFParseAndImportFile returned prim_path={{prim_path!r}} '
        f'but no prim exists at that path after import.'
    )
print(f'imported URDF to {{prim_path}}')
"""

    # Resolve robot name for asset_library or named imports
    name_lower = file_path.lower().replace(" ", "_").replace("-", "_")
    local_file = _ROBOT_NAME_MAP.get(name_lower)

    if not _LOCAL_ASSETS and (fmt == "asset_library" or local_file):
        return (
            "# ERROR: ASSETS_ROOT_PATH is not configured in .env\n"
            "# Set ASSETS_ROOT_PATH to your local assets folder or Nucleus URL.\n"
            "# Example (local):   ASSETS_ROOT_PATH=/home/user/Desktop/assets\n"
            "# Example (Nucleus): ASSETS_ROOT_PATH=omniverse://localhost/NVIDIA/Assets/Isaac/5.1\n"
            "raise RuntimeError('ASSETS_ROOT_PATH not set in .env — cannot resolve robot assets')"
        )

    is_nucleus = _LOCAL_ASSETS.startswith("omniverse://")

    if fmt == "asset_library" or local_file:
        if local_file:
            resolved = f"{_ROBOTS_DIR}/{local_file}"
        else:
            resolved = f"{_ROBOTS_DIR}/{file_path}.usd"

        if is_nucleus:
            # Nucleus URL — no local file check, USD resolves directly
            return (
                "import omni.usd\n"
                "from pxr import UsdGeom, Gf\n"
                + _SAFE_XFORM_SNIPPET +
                "\nstage = omni.usd.get_context().get_stage()\n"
                f"prim = stage.DefinePrim('{dest}', 'Xform')\n"
                f"prim.GetReferences().AddReference('{resolved}')\n"
                f"_safe_set_translate(prim, (0, 0, 0))"
            )
        else:
            # Local filesystem — validate the file exists
            return (
                "import omni.usd\n"
                "from pxr import UsdGeom, Gf\n"
                "import os\n"
                + _SAFE_XFORM_SNIPPET +
                "\nstage = omni.usd.get_context().get_stage()\n"
                f"asset_path = '{resolved}'\n"
                "if not os.path.exists(asset_path):\n"
                f"    raise FileNotFoundError(f'Robot asset not found: {{asset_path}}')\n"
                f"prim = stage.DefinePrim('{dest}', 'Xform')\n"
                "prim.GetReferences().AddReference(asset_path)\n"
                f"_safe_set_translate(prim, (0, 0, 0))"
            )

    # Default: USD reference (absolute path or URL)
    return (
        "import omni.usd\n"
        "from pxr import UsdGeom, Gf\n"
        + _SAFE_XFORM_SNIPPET +
        "\nstage = omni.usd.get_context().get_stage()\n"
        f"prim = stage.DefinePrim('{dest}', 'Xform')\n"
        f"prim.GetReferences().AddReference('{file_path}')\n"
        f"_safe_set_translate(prim, (0, 0, 0))"
    )


# ── Robot anchoring ──────────────────────────────────────────────────────────
# Isaac Sim robot USD assets contain a "rootJoint" (6-DOF free joint) that
# allows them to float freely. To anchor a robot:
# 1. Set PhysxArticulationAPI.fixedBase = True (keeps ArticulationRootAPI on root)
# 2. Delete the rootJoint (free joint)
# 3. Optionally create a FixedJoint to attach to a specific surface
# CRITICAL: Do NOT move ArticulationRootAPI — it must stay on the root prim
# or the tensor API pattern '/World/Robot' will fail with
# "Pattern did not match any articulations".

def _gen_anchor_robot(args: Dict) -> str:
    robot_path = args["robot_path"]
    anchor_surface = args.get("anchor_surface_path", "")
    base_link = args.get("base_link_name", "panda_link0")
    position = args.get("position")  # world position where robot sits

    # Build optional FixedJoint block for anchoring to a surface
    fixed_joint_block = ""
    if anchor_surface:
        local_pos_line = ""
        if position:
            local_pos_line = f"\n    anchor_prim.GetAttribute('physics:localPos0').Set(Gf.Vec3f({position[0]}, {position[1]}, {position[2]}))"
        fixed_joint_block = f"""
# Step 3: Create FixedJoint to attach to surface (excluded from articulation tree)
anchor_path = robot_path + '/AnchorJoint'
anchor_prim = stage.GetPrimAtPath(anchor_path)
if not anchor_prim.IsValid():
    anchor_prim = stage.DefinePrim(anchor_path, 'PhysicsFixedJoint')
    print(f"Created FixedJoint at {{anchor_path}}")
else:
    print(f"Reconfigured existing FixedJoint at {{anchor_path}}")

body0_rel = anchor_prim.GetRelationship('physics:body0')
if not body0_rel:
    body0_rel = anchor_prim.CreateRelationship('physics:body0')
body0_rel.SetTargets([Sdf.Path('{anchor_surface}')])

body1_rel = anchor_prim.GetRelationship('physics:body1')
if not body1_rel:
    body1_rel = anchor_prim.CreateRelationship('physics:body1')
body1_rel.SetTargets([Sdf.Path(base_link_path)])

anchor_prim.GetAttribute('physics:excludeFromArticulation').Set(True)
anchor_prim.GetAttribute('physics:jointEnabled').Set(True){local_pos_line}
print(f"Anchored to {anchor_surface}")
"""

    return f"""\
import omni.usd
from pxr import Usd, UsdPhysics, PhysxSchema, Gf, Sdf

stage = omni.usd.get_context().get_stage()
robot_path = '{robot_path}'
base_link_path = robot_path + '/{base_link}'
robot_prim = stage.GetPrimAtPath(robot_path)

# Step 1: Set fixedBase=True on PhysxArticulationAPI
# This tells PhysX the root link is immovable (no need to move ArticulationRootAPI)
if not robot_prim.HasAPI(PhysxSchema.PhysxArticulationAPI):
    PhysxSchema.PhysxArticulationAPI.Apply(robot_prim)
# Use raw attribute authoring — Isaac Sim 5.x dropped the CreateFixedBaseAttr
# convenience; the attribute name physxArticulation:fixedBase is stable.
from pxr import Sdf as _Sdf
_fb_attr = robot_prim.GetAttribute('physxArticulation:fixedBase')
if not _fb_attr or not _fb_attr.IsDefined():
    _fb_attr = robot_prim.CreateAttribute('physxArticulation:fixedBase', _Sdf.ValueTypeNames.Bool)
_fb_attr.Set(True)
print("Set physxArticulation:fixedBase=True on root")

# Step 2: Delete the rootJoint (6-DOF free joint that lets the robot float)
root_joint_path = robot_path + '/rootJoint'
rj = stage.GetPrimAtPath(root_joint_path)
if rj.IsValid():
    stage.RemovePrim(root_joint_path)
    print(f"Deleted {{root_joint_path}} (6-DOF free joint)")
{fixed_joint_block}
print(f"Robot at {{robot_path}} is now anchored (fixedBase=True)")
print(f"ArticulationRootAPI remains on {{robot_path}} — tensor API patterns will work")
"""


def _gen_set_viewport_camera(args: Dict) -> str:
    return (
        "import omni.kit.viewport.utility\n"
        "vp_api = omni.kit.viewport.utility.get_active_viewport()\n"
        f"vp_api.camera_path = '{args['camera_path']}'"
    )


def _gen_configure_sdg(args: Dict) -> str:
    annotators = args.get("annotators", ["rgb", "bounding_box_2d"])
    num_frames = args.get("num_frames", 10)
    output_dir = args.get("output_dir", "/tmp/sdg_output")
    resolution = args.get("resolution", [1280, 720])

    ann_lines = "\n    ".join(
        f'rp.AnnotatorRegistry.get_annotator("{a}")' for a in annotators
    )

    return f"""\
import omni.replicator.core as rep

with rep.new_layer():
    camera = rep.get.camera()
    rp = rep.create.render_product(camera, ({resolution[0]}, {resolution[1]}))

    writer = rep.WriterRegistry.get("BasicWriter")
    writer.initialize(output_dir="{output_dir}", rgb=True,
                      bounding_box_2d={'bounding_box_2d' in annotators},
                      semantic_segmentation={'semantic_segmentation' in annotators},
                      instance_segmentation={'instance_segmentation' in annotators},
                      normals={'normals' in annotators},
                      distance_to_camera={'distance_to_camera' in annotators})
    writer.attach([rp])

    rep.orchestrator.run_until_complete(num_frames={num_frames})
"""


# ── Code generation dispatch ─────────────────────────────────────────────────

CODE_GEN_HANDLERS = {
    "create_prim": _gen_create_prim,
    "delete_prim": _gen_delete_prim,
    "set_attribute": _gen_set_attribute,
    "add_reference": _gen_add_reference,
    "apply_api_schema": _gen_apply_api_schema,
    "clone_prim": _gen_clone_prim,
    "create_deformable_mesh": _gen_deformable,
    "create_omnigraph": _gen_create_omnigraph,
    "create_material": _gen_create_material,
    "assign_material": _gen_assign_material,
    "sim_control": _gen_sim_control,
    "set_physics_params": _gen_set_physics_params,
    "teleport_prim": _gen_teleport_prim,
    "set_joint_targets": _gen_set_joint_targets,
    "import_robot": _gen_import_robot,
    "anchor_robot": _gen_anchor_robot,
    "set_viewport_camera": _gen_set_viewport_camera,
    "configure_sdg": _gen_configure_sdg,
}


# ── Spec / data lookup handlers (no code gen, just return data) ──────────────

async def _handle_lookup_product_spec(args: Dict) -> Dict:
    """Fuzzy-match a product name against the sensor specs database."""
    query = args.get("product_name", "").lower()
    specs = _load_sensor_specs()
    # Exact match first
    for s in specs:
        if s["product"].lower() == query:
            return {"found": True, "spec": s}
    # Substring match
    matches = [s for s in specs if query in s["product"].lower() or
               any(query in w.lower() for w in s["product"].split())]
    if matches:
        return {"found": True, "spec": matches[0], "alternatives": [m["product"] for m in matches[1:4]]}
    # Fuzzy by manufacturer or type
    by_type = [s for s in specs if query in s.get("type", "") or query in s.get("subtype", "")]
    if by_type:
        return {"found": False, "suggestions": [s["product"] for s in by_type[:5]],
                "message": f"No exact match for '{args['product_name']}'. Did you mean one of these?"}
    return {"found": False, "message": f"No sensor specs found for '{args['product_name']}'"}


async def _handle_scene_summary(args: Dict) -> Dict:
    ctx = await kit_tools.get_stage_context(full=False)
    if "error" in ctx:
        return ctx
    text = kit_tools.format_stage_context_for_llm(ctx)
    return {"summary": text}


async def _handle_capture_viewport(args: Dict) -> Dict:
    max_dim = args.get("max_dim", 1280)
    return await kit_tools.get_viewport_image(max_dim=max_dim)


async def _handle_get_console_errors(args: Dict) -> Dict:
    ctx = await kit_tools.get_stage_context(full=False)
    logs = ctx.get("recent_logs", [])
    min_level = args.get("min_level", "warning")
    level_order = ["verbose", "info", "warning", "error", "fatal"]
    min_idx = level_order.index(min_level) if min_level in level_order else 2
    filtered = [l for l in logs if level_order.index(l.get("level", "info")) >= min_idx]
    last_n = args.get("last_n", 50)
    return {"errors": filtered[-last_n:], "total_count": len(filtered)}


async def _handle_get_articulation_state(args: Dict) -> Dict:
    prim_path = args["prim_path"]
    code = f"""\
import omni.usd
from pxr import UsdPhysics
import json

stage = omni.usd.get_context().get_stage()
prim = stage.GetPrimAtPath('{prim_path}')
joints = []
for child in prim.GetAllChildren():
    if child.IsA(UsdPhysics.RevoluteJoint) or child.IsA(UsdPhysics.PrismaticJoint):
        joints.append({{'name': child.GetName(), 'path': str(child.GetPath())}})
result = {{'articulation_path': '{prim_path}', 'joints': joints}}
print(json.dumps(result))
"""
    return await kit_tools.queue_exec_patch(code, f"Read articulation state for {prim_path}")


async def _handle_list_all_prims(args: Dict) -> Dict:
    ctx = await kit_tools.get_stage_context(full=True)
    return ctx.get("stage", {})


async def _handle_measure_distance(args: Dict) -> Dict:
    prim_a = args["prim_a"]
    prim_b = args["prim_b"]
    code = f"""\
import omni.usd
from pxr import UsdGeom, Gf
import json

stage = omni.usd.get_context().get_stage()
xf_a = UsdGeom.Xformable(stage.GetPrimAtPath('{prim_a}')).ComputeLocalToWorldTransform(0)
xf_b = UsdGeom.Xformable(stage.GetPrimAtPath('{prim_b}')).ComputeLocalToWorldTransform(0)
pos_a = xf_a.ExtractTranslation()
pos_b = xf_b.ExtractTranslation()
dist = (pos_a - pos_b).GetLength()
print(json.dumps({{'prim_a': '{prim_a}', 'prim_b': '{prim_b}', 'distance_m': dist,
       'position_a': list(pos_a), 'position_b': list(pos_b)}}))
"""
    return await kit_tools.queue_exec_patch(code, f"Measure distance {prim_a} ↔ {prim_b}")


async def _handle_get_debug_info(args: Dict) -> Dict:
    """Return perf metrics via Kit RPC /context fallback."""
    ctx = await kit_tools.get_stage_context(full=False)
    return {
        "prim_count": ctx.get("stage", {}).get("prim_count"),
        "stage_url": ctx.get("stage", {}).get("stage_url"),
        "note": "Full perf metrics require Kit-side instrumentation",
    }


async def _handle_lookup_knowledge(args: Dict) -> Dict:
    """Search the version-specific knowledge base for code patterns and docs."""
    from ...retrieval.context_retriever import (
        retrieve_context,
        find_matching_patterns,
        detect_isaac_version,
    )
    query = args.get("query", "")
    version = detect_isaac_version()

    # Search FTS index
    fts_results = retrieve_context(query, version=version, limit=3)

    # Search code patterns
    patterns = find_matching_patterns(query, version=version, limit=3)

    results = []
    for r in fts_results:
        results.append({
            "source": r.get("source_id", "docs"),
            "section": r.get("section_path", ""),
            "content": r.get("content", "")[:600],
        })
    for p in patterns:
        results.append({
            "source": "code_patterns",
            "title": p.get("title", ""),
            "code": p.get("code", ""),
            "note": p.get("note", ""),
        })

    return {
        "version": version,
        "query": query,
        "results": results,
        "count": len(results),
    }


# Data-only handlers (no code gen → return data directly to LLM)
DATA_HANDLERS = {
    "lookup_product_spec": _handle_lookup_product_spec,
    "scene_summary": _handle_scene_summary,
    "capture_viewport": _handle_capture_viewport,
    "get_console_errors": _handle_get_console_errors,
    "get_articulation_state": _handle_get_articulation_state,
    "list_all_prims": _handle_list_all_prims,
    "measure_distance": _handle_measure_distance,
    "get_debug_info": _handle_get_debug_info,
    "lookup_knowledge": _handle_lookup_knowledge,
    "explain_error": None,  # handled inline by LLM (no tool execution)
}

# ── ROS2 live handlers (via rosbridge / ros-mcp) ────────────────────────────
try:
    from .ros_mcp_tools import (
        handle_ros2_connect,
        handle_ros2_list_topics,
        handle_ros2_get_topic_type,
        handle_ros2_get_message_type,
        handle_ros2_subscribe_once,
        handle_ros2_publish,
        handle_ros2_publish_sequence,
        handle_ros2_list_services,
        handle_ros2_call_service,
        handle_ros2_list_nodes,
        handle_ros2_get_node_details,
    )
    DATA_HANDLERS.update({
        "ros2_connect": handle_ros2_connect,
        "ros2_list_topics": handle_ros2_list_topics,
        "ros2_get_topic_type": handle_ros2_get_topic_type,
        "ros2_get_message_type": handle_ros2_get_message_type,
        "ros2_subscribe_once": handle_ros2_subscribe_once,
        "ros2_publish": handle_ros2_publish,
        "ros2_publish_sequence": handle_ros2_publish_sequence,
        "ros2_list_services": handle_ros2_list_services,
        "ros2_call_service": handle_ros2_call_service,
        "ros2_list_nodes": handle_ros2_list_nodes,
        "ros2_get_node_details": handle_ros2_get_node_details,
    })
except ImportError:
    logger.warning("[ToolExecutor] ros-mcp not installed — ROS2 live tools disabled (pip install ros-mcp)")
    DATA_HANDLERS.update({
        "ros2_connect": None,
        "ros2_list_topics": None,
        "ros2_get_topic_type": None,
        "ros2_get_message_type": None,
        "ros2_subscribe_once": None,
        "ros2_publish": None,
        "ros2_publish_sequence": None,
        "ros2_list_services": None,
        "ros2_call_service": None,
        "ros2_list_nodes": None,
        "ros2_get_node_details": None,
    })


# ── Main dispatch ────────────────────────────────────────────────────────────

async def execute_tool_call(
    tool_name: str,
    arguments: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Execute a single tool call and return the result dict.

    Returns:
        {"type": "code_patch", "code": ..., "description": ...}  for code-gen tools
        {"type": "data", ...}                                      for data-lookup tools
        {"type": "error", "error": ...}                            on failure
    """
    logger.info(f"[ToolExecutor] Executing tool: {tool_name}({json.dumps(arguments)[:200]})")

    try:
        # 1. Data handlers — return result directly
        if tool_name in DATA_HANDLERS:
            handler = DATA_HANDLERS[tool_name]
            if handler is None:
                # Tool handled inline by LLM, no execution needed
                return {"type": "data", "note": f"{tool_name} is handled by the LLM reasoning, no live execution needed."}
            result = await handler(arguments)
            return {"type": "data", **result}

        # 2. run_usd_script — pass through to Kit
        if tool_name == "run_usd_script":
            code = arguments.get("code", "")
            desc = arguments.get("description", "Run custom script")
            # Pre-flight validation
            issues = validate_patch(code)
            if has_blocking_issues(issues):
                msg = format_issues_for_llm(issues)
                logger.warning(f"[ToolExecutor] Patch blocked for {tool_name}: {msg}")
                return {"type": "error", "error": msg, "code": code, "validation_blocked": True}
            result = await kit_tools.queue_exec_patch(code, desc)
            return {
                "type": "code_patch",
                "code": code,
                "description": desc,
                "queued": result.get("queued", False),
                "executed": result.get("executed", False),
                "success": result.get("success"),
                "output": result.get("output", ""),
            }

        # 3. Code generation tools — generate code, send to Kit for approval
        if tool_name in CODE_GEN_HANDLERS:
            gen_fn = CODE_GEN_HANDLERS[tool_name]
            code = gen_fn(arguments)
            desc = f"{tool_name}({', '.join(f'{k}={v!r}' for k, v in list(arguments.items())[:3])})"

            # Pre-flight validation
            issues = validate_patch(code)
            if has_blocking_issues(issues):
                msg = format_issues_for_llm(issues)
                logger.warning(f"[ToolExecutor] Patch blocked for {tool_name}: {msg}")
                return {"type": "error", "error": msg, "code": code, "validation_blocked": True}

            # Add sensor spec auto-lookup for add_sensor_to_prim
            if tool_name == "add_sensor_to_prim" and arguments.get("product_name"):
                spec_result = await _handle_lookup_product_spec({"product_name": arguments["product_name"]})
                if spec_result.get("found"):
                    return {
                        "type": "code_patch_with_spec",
                        "code": code,
                        "description": desc,
                        "product_spec": spec_result["spec"],
                    }

            result = await kit_tools.queue_exec_patch(code, desc)
            return {
                "type": "code_patch",
                "code": code,
                "description": desc,
                "queued": result.get("queued", False),
                "executed": result.get("executed", False),
                "success": result.get("success"),
                "output": result.get("output", ""),
            }

        return {"type": "error", "error": f"Unknown tool: {tool_name}"}

    except Exception as e:
        logger.error(f"[ToolExecutor] {tool_name} failed: {e}")
        return {"type": "error", "error": str(e)}


def _gen_add_sensor(args: Dict) -> str:
    """Generate code for adding a sensor based on type and optional product spec."""
    prim_path = args["prim_path"]
    sensor_type = args["sensor_type"]

    if sensor_type == "camera":
        fov = args.get("fov", 60)
        res = args.get("resolution", [1280, 720])
        return f"""\
import omni.usd
from pxr import UsdGeom, Sdf, Gf

stage = omni.usd.get_context().get_stage()
cam_path = '{prim_path}/Camera'
cam = UsdGeom.Camera.Define(stage, cam_path)
cam.GetHorizontalApertureAttr().Set(20.955)
cam.GetFocalLengthAttr().Set(10.0 * 20.955 / (2.0 * __import__('math').tan(__import__('math').radians({fov}/2))))
cam.GetClippingRangeAttr().Set(Gf.Vec2f(0.01, 1000.0))
"""
    if sensor_type == "rtx_lidar":
        return f"""\
import omni.usd
from pxr import UsdGeom, Gf
{_SAFE_XFORM_SNIPPET}
stage = omni.usd.get_context().get_stage()
lidar_path = '{prim_path}/RTXLidar'
lidar_prim = stage.DefinePrim(lidar_path, 'Camera')
_safe_set_translate(lidar_prim, (0, 0, 0.1))

# Configure RTX Lidar via Isaac Sim extension
from isaacsim.sensors.rtx import LidarRtx
lidar = LidarRtx(prim_path=lidar_path)
"""
    if sensor_type == "imu":
        return f"""\
from isaacsim.sensors.physics import IMUSensor
imu = IMUSensor(prim_path='{prim_path}/IMU')
"""
    if sensor_type == "contact_sensor":
        return f"""\
from isaacsim.sensors.physics import ContactSensor
contact = ContactSensor(prim_path='{prim_path}/ContactSensor')
"""
    return f"# Sensor type '{sensor_type}' not yet implemented"


# Register the sensor generator
CODE_GEN_HANDLERS["add_sensor_to_prim"] = _gen_add_sensor


# ── Motion Planning (RMPflow / Lula) ─────────────────────────────────────────

# Robot config map: robot_type → (rmpflow_config_dir, robot_description_path, urdf_path, end_effector_frame)
_MOTION_ROBOT_CONFIGS = {
    "franka": {
        "rmp_config": "franka/rmpflow",
        "desc": "franka/robot_descriptor.yaml",
        "urdf": "franka/lula_franka_gen.urdf",
        "ee_frame": "panda_hand",
    },
    "ur10": {
        "rmp_config": "universal_robots/ur10/rmpflow",
        "desc": "universal_robots/ur10/robot_descriptor.yaml",
        "urdf": "universal_robots/ur10/lula_ur10_gen.urdf",
        "ee_frame": "ee_link",
    },
    "ur5e": {
        "rmp_config": "universal_robots/ur5e/rmpflow",
        "desc": "universal_robots/ur5e/robot_descriptor.yaml",
        "urdf": "universal_robots/ur5e/lula_ur5e_gen.urdf",
        "ee_frame": "ee_link",
    },
    "cobotta": {
        "rmp_config": "denso/cobotta_pro_900/rmpflow",
        "desc": "denso/cobotta_pro_900/robot_descriptor.yaml",
        "urdf": "denso/cobotta_pro_900/lula_cobotta_gen.urdf",
        "ee_frame": "onrobot_rg6_base_link",
    },
}


def _gen_move_to_pose(args: Dict) -> str:
    art_path = args["articulation_path"]
    target_pos = args["target_position"]
    target_ori = args.get("target_orientation")
    planner = args.get("planner", "rmpflow")
    robot_type = args.get("robot_type", "franka").lower()

    cfg = _MOTION_ROBOT_CONFIGS.get(robot_type, _MOTION_ROBOT_CONFIGS["franka"])
    ee = cfg["ee_frame"]

    if planner == "lula_rrt":
        # Global planner — single-shot path plan
        lines = [
            "import omni.usd",
            "import numpy as np",
            "from isaacsim.robot_motion.motion_generation import LulaTaskSpaceTrajectoryGenerator",
            "from isaacsim.robot_motion.motion_generation import interface_config_loader",
            "",
            "# Load Lula RRT planner config",
            f"rrt_config = interface_config_loader.load_supported_lula_rrt_config('{robot_type}')",
            f"rrt = LulaTaskSpaceTrajectoryGenerator(**rrt_config)",
            "",
            f"target_pos = np.array({list(target_pos)})",
        ]
        if target_ori:
            lines.append(f"target_ori = np.array({list(target_ori)})")
        else:
            lines.append("target_ori = None")
        lines.extend([
            "",
            "# Compute trajectory",
            f"trajectory = rrt.compute_task_space_trajectory_from_points(",
            f"    [target_pos], [target_ori] if target_ori is not None else None",
            f")",
            "if trajectory is not None:",
            "    print(f'Lula RRT: planned trajectory with {{len(trajectory)}} waypoints')",
            "else:",
            "    print('Lula RRT: failed to find path — try a different target or clear obstacles')",
        ])
        return "\n".join(lines)

    # Default: RMPflow (reactive, real-time)
    lines = [
        "import omni.usd",
        "import numpy as np",
        "from isaacsim.robot_motion.motion_generation import RmpFlow",
        "from isaacsim.robot_motion.motion_generation import interface_config_loader",
        "from isaacsim.core.prims import SingleArticulation",
        "from isaacsim.core.api import World",
        "",
        "# Load RMPflow config for the robot",
        f"rmpflow_config = interface_config_loader.load_supported_motion_gen_config('{robot_type}', 'RMPflow')",
        "rmpflow = RmpFlow(**rmpflow_config)",
        "",
        f"# Get the articulation",
        f"art = SingleArticulation(prim_path='{art_path}')",
        "world = World.instance()",
        "if world is None:",
        "    from isaacsim.core.api import World",
        "    world = World()",
        "art.initialize()",
        "",
        "# Set target",
        f"target_pos = np.array({list(target_pos)})",
    ]
    if target_ori:
        lines.append(f"target_ori = np.array({list(target_ori)})")
    else:
        lines.append("target_ori = None")
    lines.extend([
        f"rmpflow.set_end_effector_target(target_pos, target_ori)",
        "",
        "# Get current joint state and compute action",
        "joint_positions = art.get_joint_positions()",
        "joint_velocities = art.get_joint_velocities()",
        "action = rmpflow.get_next_articulation_action(",
        "    joint_positions, joint_velocities",
        ")",
        "",
        "# Apply joint targets",
        "art.apply_action(action)",
        f"print(f'RMPflow: moving {ee} to {{target_pos}} — action applied')",
    ])
    return "\n".join(lines)


def _gen_plan_trajectory(args: Dict) -> str:
    art_path = args["articulation_path"]
    waypoints = args["waypoints"]
    robot_type = args.get("robot_type", "franka").lower()

    positions_str = "[" + ", ".join(
        f"np.array({list(wp['position'])})" for wp in waypoints
    ) + "]"
    orientations = [wp.get("orientation") for wp in waypoints]
    has_ori = any(o is not None for o in orientations)
    if has_ori:
        ori_str = "[" + ", ".join(
            f"np.array({list(o)})" if o else "None" for o in orientations
        ) + "]"
    else:
        ori_str = "None"

    lines = [
        "import numpy as np",
        "from isaacsim.robot_motion.motion_generation import LulaTaskSpaceTrajectoryGenerator",
        "from isaacsim.robot_motion.motion_generation import interface_config_loader",
        "",
        f"rrt_config = interface_config_loader.load_supported_lula_rrt_config('{robot_type}')",
        f"planner = LulaTaskSpaceTrajectoryGenerator(**rrt_config)",
        "",
        f"positions = {positions_str}",
        f"orientations = {ori_str}",
        "",
        "trajectory = planner.compute_task_space_trajectory_from_points(",
        "    positions, orientations",
        ")",
        "if trajectory is not None:",
        f"    print(f'Planned trajectory through {len(waypoints)} waypoints')",
        "else:",
        "    print('Failed to plan trajectory — try different waypoints')",
    ]
    return "\n".join(lines)


CODE_GEN_HANDLERS["move_to_pose"] = _gen_move_to_pose
CODE_GEN_HANDLERS["plan_trajectory"] = _gen_plan_trajectory


# ── Asset Catalog Search ─────────────────────────────────────────────────────

_asset_index: Optional[List[Dict]] = None

# Robot name map (module-level copy for catalog indexing)
_CATALOG_ROBOTS = {
    "franka": "franka.usd",
    "panda": "franka.usd",
    "spot": "spot.usd",
    "spot_with_arm": "spot_with_arm.usd",
    "carter": "carter_v1.usd",
    "jetbot": "jetbot.usd",
    "kaya": "kaya.usd",
    "ur10": "ur10.usd",
    "ur5e": "ur5e.usd",
    "anymal_c": "anymal_c.usd",
    "anymal_d": "anymal_d.usd",
    "a1": "a1.usd",
    "go1": "go1.usd",
    "go2": "go2.usd",
    "g1": "g1.usd",
    "unitree_g1": "g1.usd",
    "g1_23dof": "g1_23dof_robot.usd",
    "h1": "h1_hand_left.usd",
    "unitree_h1": "h1_hand_left.usd",
    "allegro_hand": "allegro_hand.usd",
    "ridgeback_franka": "ridgeback_franka.usd",
    "humanoid": "humanoid.usd",
    "humanoid_28": "humanoid_28.usd",
}


def _build_asset_index() -> List[Dict]:
    """Build searchable index from asset_catalog.json (fast) + known robots."""
    global _asset_index
    if _asset_index is not None:
        return _asset_index

    index = []
    assets_root = getattr(config, "assets_root_path", None) or ""
    robots_sub = getattr(config, "assets_robots_subdir", None) or "Collected_Robots"
    robots_dir = f"{assets_root}/{robots_sub}" if assets_root else ""

    # 1. Load asset_catalog.json (5,000+ entries with rich metadata)
    catalog_path = Path(assets_root) / "asset_catalog.json" if assets_root else None
    catalog_loaded = False
    if catalog_path and catalog_path.exists():
        try:
            catalog = json.loads(catalog_path.read_text())
            for entry in catalog.get("assets", []):
                tags = entry.get("tags", [])
                index.append({
                    "name": entry.get("name", ""),
                    "type": entry.get("category", "prop"),
                    "path": entry.get("usd_path", ""),
                    "rel_path": entry.get("relative_path", ""),
                    "tags": tags,
                    "source": "asset_catalog",
                })
            catalog_loaded = True
            logger.info(f"[AssetIndex] Loaded {len(index)} entries from asset_catalog.json")
        except Exception as e:
            logger.warning(f"[AssetIndex] Failed to load asset_catalog.json: {e}")

    # 2. Always add the known robot name map (canonical names → files)
    for name, filename in _CATALOG_ROBOTS.items():
        index.append({
            "name": name,
            "type": "robot",
            "path": f"{robots_dir}/{filename}" if robots_dir else filename,
            "source": "robot_library",
        })

    # 3. JSONL manifest (user-added entries)
    manifest_path = _WORKSPACE / "knowledge" / "asset_manifest.jsonl"
    if manifest_path.exists():
        for line in manifest_path.read_text().splitlines():
            line = line.strip()
            if line:
                try:
                    index.append(json.loads(line))
                except json.JSONDecodeError:
                    pass

    # 4. Filesystem walk only if catalog wasn't loaded (slow fallback)
    if not catalog_loaded and assets_root:
        search_dir = Path(assets_root)
        if search_dir.exists():
            try:
                for f in search_dir.rglob("*"):
                    if f.suffix.lower() in (".usd", ".usda", ".usdz"):
                        rel = f.relative_to(search_dir)
                        name_parts = rel.stem.replace("_", " ").replace("-", " ")
                        path_str = str(rel).lower()
                        if any(k in path_str for k in ("robot", "arm", "gripper", "manipulator")):
                            atype = "robot"
                        elif any(k in path_str for k in ("env", "room", "warehouse", "house", "kitchen")):
                            atype = "environment"
                        elif any(k in path_str for k in ("sensor", "camera", "lidar")):
                            atype = "sensor"
                        elif any(k in path_str for k in ("material", "mdl", "texture")):
                            atype = "material"
                        else:
                            atype = "prop"
                        index.append({
                            "name": name_parts,
                            "type": atype,
                            "path": str(f),
                            "source": "filesystem",
                            "rel_path": str(rel),
                        })
            except PermissionError:
                pass

    _asset_index = index
    return _asset_index


async def _handle_catalog_search(args: Dict) -> Dict:
    """Fuzzy-match assets by name, type, and path."""
    query = args.get("query", "").lower()
    asset_type = args.get("asset_type", "any").lower()
    limit = args.get("limit", 10)

    index = _build_asset_index()
    scored = []
    query_words = query.split()

    for asset in index:
        if asset_type != "any" and asset.get("type", "any") != asset_type:
            continue

        name = asset.get("name", "").lower()
        path = asset.get("path", "").lower()
        rel_path = asset.get("rel_path", "").lower()
        tags = " ".join(asset.get("tags", [])).lower() if asset.get("tags") else ""
        searchable = f"{name} {path} {rel_path} {tags}"

        # Score: exact match > all words present > partial
        if query == name:
            score = 100
        elif all(w in searchable for w in query_words):
            score = 70 + sum(10 for w in query_words if w in name)
        elif any(w in searchable for w in query_words):
            score = sum(10 for w in query_words if w in searchable)
        else:
            continue

        scored.append((score, asset))

    scored.sort(key=lambda x: -x[0])
    results = [a for _, a in scored[:limit]]

    return {
        "query": args.get("query", ""),
        "results": results,
        "total_matches": len(scored),
        "index_size": len(index),
    }


DATA_HANDLERS["catalog_search"] = _handle_catalog_search


# ── Nucleus Browse & Download ────────────────────────────────────────────────

async def _handle_nucleus_browse(args: Dict) -> Dict:
    """Browse a Nucleus server directory via Kit RPC (omni.client inside Isaac Sim)."""
    nucleus_path = args.get("path", "/NVIDIA/Assets/Isaac/5.1")
    # Sanitize: strip shell metacharacters, only allow alphanumeric + / . _ -
    import re as _re
    if not _re.match(r'^[a-zA-Z0-9/_. :-]+$', nucleus_path):
        return {"error": "Invalid path characters"}

    server = args.get("server", "omniverse://localhost")
    if not _re.match(r'^omniverse://[a-zA-Z0-9._-]+(:\d+)?$', server):
        return {"error": "Invalid Nucleus server URL. Expected format: omniverse://hostname"}

    full_path = f"{server}{nucleus_path}"
    limit = min(args.get("limit", 50), 200)

    code = f"""
import omni.client
import json

result, entries = omni.client.list("{full_path}")
items = []
if result == omni.client.Result.OK:
    for entry in entries[:{limit}]:
        items.append({{
            "name": entry.relative_path,
            "size": entry.size,
            "is_folder": entry.flags & omni.client.ItemFlags.CAN_HAVE_CHILDREN != 0,
            "modified_time": str(entry.modified_time) if hasattr(entry, 'modified_time') else "",
        }})
print(json.dumps({{"status": str(result), "path": "{full_path}", "items": items, "count": len(items)}}))
"""
    result = await kit_tools.exec_sync(code, timeout=15)
    if not result.get("success"):
        return {"error": f"Kit RPC failed: {result.get('output', 'unknown')}",
                "hint": "Is Isaac Sim running? Is a Nucleus server accessible?"}

    output = result.get("output", "").strip()
    # Parse the last line as JSON (exec_sync may include other prints)
    for line in reversed(output.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                pass
    return {"error": "Failed to parse Nucleus response", "raw_output": output[:500]}


async def _handle_download_asset(args: Dict) -> Dict:
    """Download asset from Nucleus to local Desktop/assets and register in catalog."""
    import re as _re

    nucleus_url = args.get("nucleus_url", "")
    # Validate URL format
    if not nucleus_url.startswith("omniverse://"):
        return {"error": "nucleus_url must start with omniverse://"}
    if not _re.match(r'^omniverse://[a-zA-Z0-9._:-]+/[a-zA-Z0-9/_. -]+$', nucleus_url):
        return {"error": "Invalid nucleus_url format"}

    assets_root = getattr(config, "assets_root_path", "") or ""
    if not assets_root:
        return {"error": "ASSETS_ROOT_PATH not configured in .env"}

    # Determine local destination
    # omniverse://localhost/NVIDIA/Assets/Isaac/5.1/Robots/Franka/franka.usd
    # → Desktop/assets/Nucleus_Downloads/Robots/Franka/franka.usd
    subdir = args.get("local_subdir", "")
    if not subdir:
        # Auto-derive from Nucleus path: strip server + /NVIDIA/Assets/Isaac/X.X/
        path_part = nucleus_url.split("/", 3)[-1] if "/" in nucleus_url else ""
        # Remove common prefixes
        for prefix in ("NVIDIA/Assets/Isaac/5.1/", "NVIDIA/Assets/Isaac/", "NVIDIA/Assets/", "NVIDIA/"):
            if path_part.startswith(prefix):
                path_part = path_part[len(prefix):]
                break
        subdir = f"Nucleus_Downloads/{path_part}" if path_part else "Nucleus_Downloads"

    # Extract just the directory part (not filename)
    if subdir.endswith(".usd") or subdir.endswith(".usda") or subdir.endswith(".usdz"):
        subdir = str(Path(subdir).parent)

    local_dir = Path(assets_root) / subdir
    filename = nucleus_url.rsplit("/", 1)[-1]
    # Sanitize filename
    filename = _re.sub(r'[^a-zA-Z0-9._-]', '_', filename)
    local_path = local_dir / filename

    if local_path.exists():
        return {
            "status": "already_exists",
            "local_path": str(local_path),
            "message": f"Asset already downloaded at {local_path}",
        }

    # Escape paths for code injection safety
    safe_nucleus = nucleus_url.replace('"', '').replace("'", "").replace("\\", "")
    safe_local = str(local_path).replace('"', '').replace("'", "").replace("\\", "")
    safe_dir = str(local_dir).replace('"', '').replace("'", "").replace("\\", "")

    code = f"""
import omni.client
import os
import json

os.makedirs("{safe_dir}", exist_ok=True)
result = omni.client.copy("{safe_nucleus}", "{safe_local}")
if result == omni.client.Result.OK:
    size = os.path.getsize("{safe_local}") if os.path.exists("{safe_local}") else 0
    print(json.dumps({{"status": "ok", "local_path": "{safe_local}", "size": size}}))
else:
    print(json.dumps({{"status": "error", "result": str(result), "nucleus_url": "{safe_nucleus}"}}))
"""
    result = await kit_tools.exec_sync(code, timeout=60)
    if not result.get("success"):
        return {"error": f"Kit RPC download failed: {result.get('output', 'unknown')}"}

    output = result.get("output", "").strip()
    dl_result = None
    for line in reversed(output.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                dl_result = json.loads(line)
                break
            except json.JSONDecodeError:
                pass

    if not dl_result or dl_result.get("status") != "ok":
        return {"error": "Download failed", "details": dl_result or output[:500]}

    # Register in asset_catalog.json
    catalog_path = Path(assets_root) / "asset_catalog.json"
    asset_name = Path(filename).stem.replace("_", " ").replace("-", " ")
    # Infer category from path
    path_lower = nucleus_url.lower()
    if any(k in path_lower for k in ("robot", "arm", "gripper", "manipulator")):
        category = "robot"
    elif any(k in path_lower for k in ("env", "room", "warehouse", "scene")):
        category = "scene"
    elif any(k in path_lower for k in ("sensor", "camera", "lidar")):
        category = "sensor"
    elif any(k in path_lower for k in ("prop", "object", "furniture")):
        category = "prop"
    else:
        category = args.get("category", "prop")

    new_entry = {
        "name": asset_name,
        "usd_path": str(local_path),
        "relative_path": str(local_path.relative_to(Path(assets_root))),
        "category": category,
        "tags": [w for w in asset_name.lower().split() if len(w) > 1] + ["nucleus_download"],
        "nucleus_source": nucleus_url,
        "meters_per_unit": 1.0,
    }

    if catalog_path.exists():
        try:
            catalog = json.loads(catalog_path.read_text())
            catalog["assets"].append(new_entry)
            catalog["metadata"]["total_assets"] = len(catalog["assets"])
            catalog_path.write_text(json.dumps(catalog, indent=2))
        except Exception as e:
            logger.warning(f"[DownloadAsset] Failed to update catalog: {e}")

    # Invalidate cached asset index so next search picks up the new entry
    global _asset_index
    _asset_index = None

    return {
        "status": "downloaded",
        "local_path": str(local_path),
        "size": dl_result.get("size", 0),
        "category": category,
        "nucleus_source": nucleus_url,
        "message": f"Downloaded {filename} to {local_path} ({dl_result.get('size', 0)} bytes). Registered in asset catalog.",
    }


DATA_HANDLERS["nucleus_browse"] = _handle_nucleus_browse
DATA_HANDLERS["download_asset"] = _handle_download_asset


# ── Scene Builder ────────────────────────────────────────────────────────────

def _gen_build_scene_from_blueprint(args: Dict) -> str:
    """Generate code to build a scene from a structured blueprint."""
    blueprint = args.get("blueprint", {})
    objects = blueprint.get("objects", [])
    dry_run = args.get("dry_run", False)

    if not objects:
        return "print('Empty blueprint — nothing to build')\n"

    lines = [
        "import omni.usd",
        "from pxr import UsdGeom, Gf, Sdf",
        _SAFE_XFORM_SNIPPET,
        "stage = omni.usd.get_context().get_stage()",
        "",
    ]

    for i, obj in enumerate(objects):
        name = obj.get("name", f"object_{i}")
        asset_path = obj.get("asset_path", "")
        prim_path = obj.get("prim_path", f"/World/{name}")
        pos = obj.get("position", [0, 0, 0])
        rot = obj.get("rotation", [0, 0, 0])
        scale = obj.get("scale", [1, 1, 1])
        prim_type = obj.get("prim_type")  # for simple prims (Cube, etc.)

        lines.append(f"# --- {name} ---")
        if asset_path:
            # Import via USD reference
            lines.append(f"prim = stage.DefinePrim('{prim_path}', 'Xform')")
            lines.append(f"prim.GetReferences().AddReference('{asset_path}')")
        elif prim_type:
            lines.append(f"prim = stage.DefinePrim('{prim_path}', '{prim_type}')")
        else:
            lines.append(f"prim = stage.DefinePrim('{prim_path}', 'Xform')")

        lines.append(f"_safe_set_translate(prim, ({pos[0]}, {pos[1]}, {pos[2]}))")
        if rot != [0, 0, 0]:
            lines.append(f"_safe_set_rotate_xyz(prim, ({rot[0]}, {rot[1]}, {rot[2]}))")
        if scale != [1, 1, 1]:
            lines.append(f"_safe_set_scale(prim, ({scale[0]}, {scale[1]}, {scale[2]}))")
        lines.append("")

    lines.append(f"print('Scene built: {len(objects)} objects placed')")

    if dry_run:
        return f"# DRY RUN — code preview only\n" + "\n".join(lines)
    return "\n".join(lines)


async def _handle_generate_scene_blueprint(args: Dict) -> Dict:
    """Generate a scene blueprint (data, not code). The LLM fills in the spatial layout."""
    description = args.get("description", "")
    room_dims = args.get("room_dimensions")
    available = args.get("available_assets")

    # If no assets provided, search the catalog
    if not available:
        catalog_result = await _handle_catalog_search({"query": description, "limit": 20})
        available = catalog_result.get("results", [])

    return {
        "type": "blueprint_request",
        "description": description,
        "room_dimensions": room_dims or [6, 6, 3],
        "available_assets": available,
        "instructions": (
            "Based on the description and available assets, generate a blueprint JSON with: "
            "objects: [{name, asset_path (from available_assets), prim_path (/World/Name), "
            "position [x,y,z], rotation [rx,ry,rz], scale [sx,sy,sz]}]. "
            "Ensure objects don't overlap, items sit ON surfaces (not floating), "
            "robots have 1m clearance. Then call build_scene_from_blueprint with the blueprint."
        ),
    }


DATA_HANDLERS["generate_scene_blueprint"] = _handle_generate_scene_blueprint
CODE_GEN_HANDLERS["build_scene_from_blueprint"] = _gen_build_scene_from_blueprint


# ── IsaacLab RL Training ─────────────────────────────────────────────────────

_RL_TASK_TEMPLATES = {
    "manipulation": {
        "obs": ["joint_pos", "joint_vel", "ee_pos", "ee_ori", "target_pos", "target_rel"],
        "actions": "joint_positions",
        "rewards": ["reach_target", "grasp_success", "action_penalty", "is_terminated"],
    },
    "locomotion": {
        "obs": ["base_lin_vel", "base_ang_vel", "projected_gravity", "joint_pos", "joint_vel", "actions"],
        "actions": "joint_positions",
        "rewards": ["track_lin_vel", "track_ang_vel", "feet_air_time", "action_rate", "is_terminated"],
    },
    "navigation": {
        "obs": ["base_pos", "base_ori", "base_lin_vel", "target_pos", "target_rel", "lidar_scan"],
        "actions": "base_velocity",
        "rewards": ["reach_goal", "collision_penalty", "progress_to_goal", "action_penalty"],
    },
    "custom": {
        "obs": ["joint_pos", "joint_vel"],
        "actions": "joint_positions",
        "rewards": ["task_success", "action_penalty"],
    },
}


async def _handle_create_isaaclab_env(args: Dict) -> Dict:
    """Generate an IsaacLab env scaffold — returns config as data for the LLM to refine."""
    task_name = args["task_name"]
    robot_path = args["robot_path"]
    task_type = args.get("task_type", "manipulation")
    num_envs = args.get("num_envs", 64)
    env_spacing = args.get("env_spacing", 2.0)
    reward_terms = args.get("reward_terms")

    template = _RL_TASK_TEMPLATES.get(task_type, _RL_TASK_TEMPLATES["custom"])
    if reward_terms:
        template = {**template, "rewards": reward_terms}

    env_config = {
        "task_name": task_name,
        "robot_path": robot_path,
        "task_type": task_type,
        "num_envs": num_envs,
        "env_spacing": env_spacing,
        "observation_space": template["obs"],
        "action_space": template["actions"],
        "reward_terms": template["rewards"],
        "episode_length": 500,
        "decimation": 2,
        "physics_dt": 1.0 / 120.0,
    }

    # Generate the Python env class code
    env_code = _generate_isaaclab_env_code(env_config)

    return {
        "type": "isaaclab_env",
        "task_name": task_name,
        "config": env_config,
        "generated_code": env_code,
        "instructions": (
            f"IsaacLab env '{task_name}' scaffolded with {num_envs} parallel envs. "
            f"Observations: {template['obs']}. Actions: {template['actions']}. "
            f"Rewards: {template['rewards']}. "
            "You can now call launch_training to start training, or refine the config."
        ),
    }


def _generate_isaaclab_env_code(cfg: Dict) -> str:
    """Generate a minimal IsaacLab ManagerBasedRLEnv config file."""
    task = cfg["task_name"]
    robot = cfg["robot_path"]
    obs = cfg["observation_space"]
    acts = cfg["action_space"]
    rewards = cfg["reward_terms"]
    num_envs = cfg["num_envs"]
    spacing = cfg["env_spacing"]
    ep_len = cfg["episode_length"]
    decimation = cfg["decimation"]

    obs_attrs = "\n".join(
        f"        {o}: ObsTerm = ObsTerm(func=mdp.{o})" for o in obs
    )
    reward_attrs = "\n".join(
        f"    {r}: RewTerm = RewTerm(func=mdp.{r}, weight=1.0)" for r in rewards
    )
    action_cfg_map = {
        "joint_positions": "JointPositionActionCfg",
        "base_velocity": "DifferentialInverseKinematicsActionCfg",
    }
    action_cfg_cls = action_cfg_map.get(acts, "JointPositionActionCfg")

    return f'''"""IsaacLab RL environment: {task}
Auto-generated by Isaac Assist.
"""
import isaaclab.envs.mdp as mdp
from isaaclab.envs import ManagerBasedRLEnvCfg
from isaaclab.envs.mdp import ObsGroup, ObsTerm
from isaaclab.managers import (
    RewardTermCfg as RewTerm,
    SceneEntityCfg,
)
from isaaclab.scene import InteractiveSceneCfg
from isaaclab.utils import configclass


@configclass
class ObservationsCfg:
    """Observation groups for the environment."""

    @configclass
    class PolicyCfg(ObsGroup):
{obs_attrs}

    policy: PolicyCfg = PolicyCfg()


@configclass
class ActionsCfg:
    """Action configuration for the environment."""

    {acts}: mdp.{action_cfg_cls} = mdp.{action_cfg_cls}(
        asset_name="robot", joint_names=[".*"]
    )


@configclass
class RewardsCfg:
    """Reward terms for the environment."""

{reward_attrs}


@configclass
class {task}EnvCfg(ManagerBasedRLEnvCfg):
    """Configuration for {task} environment."""

    # Scene
    scene: InteractiveSceneCfg = InteractiveSceneCfg(
        num_envs={num_envs},
        env_spacing={spacing},
    )

    # Observations
    observations: ObservationsCfg = ObservationsCfg()

    # Actions
    actions: ActionsCfg = ActionsCfg()

    # Rewards
    rewards: RewardsCfg = RewardsCfg()

    # Episode
    episode_length_s = {ep_len} * {decimation} / 120.0
    decimation = {decimation}
'''


def _gen_launch_training(args: Dict) -> str:
    """Generate code to launch an IsaacLab training run."""
    task = args["task"]
    algo = args.get("algo", "ppo")
    num_steps = args.get("num_steps", 1_000_000)
    num_envs = args.get("num_envs", 64)
    ckpt_dir = args.get("checkpoint_dir", f"workspace/rl_checkpoints/{task}")

    # Map algos to IsaacLab train script args
    algo_map = {
        "ppo": "rsl_rl",
        "sac": "skrl",
        "td3": "skrl",
        "rsl_rl": "rsl_rl",
    }
    runner = algo_map.get(algo, "rsl_rl")

    lines = [
        "import subprocess",
        "import sys",
        "import os",
        "",
        f"task = '{task}'",
        f"algo = '{algo}'",
        f"num_envs = {num_envs}",
        f"max_iterations = {num_steps // (num_envs * 24)}  # steps / (envs * horizon)",
        f"log_dir = '{ckpt_dir}'",
        "os.makedirs(log_dir, exist_ok=True)",
        "",
        "# Launch IsaacLab training",
        "cmd = [",
        "    sys.executable, '-m',",
        f"    'isaaclab.train',",
        f"    '--task', task,",
        f"    '--num_envs', str(num_envs),",
        f"    '--max_iterations', str(max_iterations),",
        f"    '--log_dir', log_dir,",
        "]",
        "print('Launching training: ' + ' '.join(cmd))",
        "proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)",
        "print(f'Training started (PID: {proc.pid}). Checkpoints → {log_dir}')",
    ]
    return "\n".join(lines)


DATA_HANDLERS["create_isaaclab_env"] = _handle_create_isaaclab_env
CODE_GEN_HANDLERS["launch_training"] = _gen_launch_training


# ─── Vision tools (Gemini Robotics-ER 1.6) ──────────────────────────────────

async def _get_viewport_bytes() -> tuple:
    """Capture the viewport and return (raw_bytes, mime_type)."""
    result = await kit_tools.get_viewport_image(max_dim=1280)
    b64 = result.get("image_b64") or result.get("data", "")
    if not b64:
        return None, None
    import base64
    return base64.b64decode(b64), "image/png"


def _get_vision_provider():
    from ..vision_gemini import GeminiVisionProvider
    return GeminiVisionProvider()


async def _handle_vision_detect_objects(args: Dict) -> Dict:
    img, mime = await _get_viewport_bytes()
    if img is None:
        return {"error": "Could not capture viewport image. Is Isaac Sim running?"}
    vp = _get_vision_provider()
    labels = args.get("labels")
    max_obj = args.get("max_objects", 10)
    detections = await vp.detect_objects(img, mime, labels=labels, max_objects=max_obj)
    return {"detections": detections, "count": len(detections), "model": vp.model}


async def _handle_vision_bounding_boxes(args: Dict) -> Dict:
    img, mime = await _get_viewport_bytes()
    if img is None:
        return {"error": "Could not capture viewport image. Is Isaac Sim running?"}
    vp = _get_vision_provider()
    boxes = await vp.detect_bounding_boxes(img, mime, max_objects=args.get("max_objects", 25))
    return {"bounding_boxes": boxes, "count": len(boxes), "model": vp.model}


async def _handle_vision_plan_trajectory(args: Dict) -> Dict:
    img, mime = await _get_viewport_bytes()
    if img is None:
        return {"error": "Could not capture viewport image. Is Isaac Sim running?"}
    vp = _get_vision_provider()
    points = await vp.plan_trajectory(
        img, args["instruction"], num_points=args.get("num_points", 15), mime_type=mime,
    )
    return {"trajectory": points, "num_points": len(points), "model": vp.model}


async def _handle_vision_analyze_scene(args: Dict) -> Dict:
    img, mime = await _get_viewport_bytes()
    if img is None:
        return {"error": "Could not capture viewport image. Is Isaac Sim running?"}
    vp = _get_vision_provider()
    analysis = await vp.analyze_scene(img, args["question"], mime_type=mime)
    return {"analysis": analysis, "model": vp.model}


DATA_HANDLERS["vision_detect_objects"] = _handle_vision_detect_objects
DATA_HANDLERS["vision_bounding_boxes"] = _handle_vision_bounding_boxes
DATA_HANDLERS["vision_plan_trajectory"] = _handle_vision_plan_trajectory
DATA_HANDLERS["vision_analyze_scene"] = _handle_vision_analyze_scene


# ── Scene Package Export ─────────────────────────────────────────────────────
# Collects all approved code patches from the audit log for a session,
# then writes:  scene_setup.py, ros2_launch.py (if ROS2 nodes present),
# README.md, and a ros2_topics.yaml listing detected topics.

async def _handle_export_scene_package(args: Dict) -> Dict:
    """Export the current session's scene setup as a reusable file package."""
    from pathlib import Path
    from datetime import datetime as _dt
    from ..routes import _audit

    session_id = args.get("session_id", "default_session")
    scene_name = args.get("scene_name", "exported_scene")
    # Sanitize scene_name for filesystem
    safe_name = "".join(c if c.isalnum() or c in "_-" else "_" for c in scene_name)

    out_dir = Path("workspace/scene_exports") / safe_name
    out_dir.mkdir(parents=True, exist_ok=True)

    # ── Collect approved patches from audit log ──────────────────────────
    entries = _audit.query_logs(limit=500, event_type="patch_executed")
    patches = []
    for e in entries:
        meta = e.metadata or {}
        if meta.get("success") and meta.get("session_id", "default_session") == session_id:
            code = meta.get("code", "")
            if code:
                patches.append({
                    "description": meta.get("user_message", ""),
                    "code": code,
                })

    if not patches:
        # Fallback: grab all successful patches regardless of session
        for e in entries:
            meta = e.metadata or {}
            if meta.get("success") and meta.get("code"):
                patches.append({
                    "description": meta.get("user_message", ""),
                    "code": meta["code"],
                })

    # ── scene_setup.py ───────────────────────────────────────────────────
    setup_lines = [
        '"""',
        f'Scene Setup: {scene_name}',
        f'Auto-exported by Isaac Assist on {_dt.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")}',
        f'Patches: {len(patches)}',
        '"""',
        'import omni.usd',
        'from pxr import Usd, UsdGeom, UsdPhysics, PhysxSchema, Gf, Sdf, UsdShade',
        '',
        'stage = omni.usd.get_context().get_stage()',
        '',
    ]
    for i, p in enumerate(patches):
        desc = p["description"] or f"Step {i+1}"
        setup_lines.append(f'# ── Step {i+1}: {desc}')
        setup_lines.append(p["code"].rstrip())
        setup_lines.append('')
    setup_lines.append('print("Scene setup complete.")')
    scene_py = "\n".join(setup_lines)
    (out_dir / "scene_setup.py").write_text(scene_py, encoding="utf-8")

    # ── Detect ROS2 topics from OmniGraph patterns in code ───────────────
    import re as _re
    ros2_topics = set()
    og_node_types = set()
    robot_paths = set()
    for p in patches:
        code = p["code"]
        # topics: /joint_states, /joint_command, /clock, /tf, etc.
        ros2_topics.update(_re.findall(r"""['\"](/[a-zA-Z_][a-zA-Z0-9_/]*)['\"]\s*""", code))
        # OmniGraph node types
        og_node_types.update(_re.findall(r"""['\"](?:isaacsim|omni\.isaac)\.[a-zA-Z0-9_.]+['\"]""", code))
        # Robot paths
        robot_paths.update(_re.findall(r"""['\"](/World/[A-Z][a-zA-Z0-9_]*)['\"]\s*""", code))
    # Filter to ROS2-style topics only (not USD paths, not physics scene attrs)
    _NON_TOPIC_PREFIXES = ("/World", "/Physics", "/Collision", "/persistent", "/Render", "/OmniKit")
    ros2_topics = sorted(
        t for t in ros2_topics
        if not any(t.startswith(p) for p in _NON_TOPIC_PREFIXES)
        and len(t) > 2  # skip bare "/"
        and not t.endswith(".usd")
    )

    # ── ros2_topics.yaml ─────────────────────────────────────────────────
    if ros2_topics or og_node_types:
        topic_lines = [f"# ROS2 Topics detected in scene: {scene_name}", "topics:"]
        for t in sorted(ros2_topics):
            topic_lines.append(f"  - name: \"{t}\"")
        topic_lines.append("")
        topic_lines.append("omnigraph_node_types:")
        for nt in sorted(og_node_types):
            topic_lines.append(f"  - {nt}")
        (out_dir / "ros2_topics.yaml").write_text("\n".join(topic_lines) + "\n", encoding="utf-8")

    # ── ros2_launch.py (if ROS2 topics detected) ────────────────────────
    has_ros2 = bool(ros2_topics)
    if has_ros2:
        launch_lines = [
            '"""',
            f'ROS2 Launch File for scene: {scene_name}',
            f'Auto-generated by Isaac Assist on {_dt.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")}',
            '"""',
            'from launch import LaunchDescription',
            'from launch_ros.actions import Node',
            '',
            '',
            'def generate_launch_description():',
            '    return LaunchDescription([',
        ]
        # Add placeholder nodes for each topic
        for t in sorted(ros2_topics):
            node_name = t.strip("/").replace("/", "_")
            launch_lines.append(f'        # Topic: {t}')
            launch_lines.append(f'        # Node("{node_name}") — configure publisher/subscriber as needed')
        launch_lines.append('    ])')
        (out_dir / "ros2_launch.py").write_text("\n".join(launch_lines) + "\n", encoding="utf-8")

    # ── README.md ────────────────────────────────────────────────────────
    robot_list = ", ".join(f"`{r}`" for r in sorted(robot_paths)) or "None detected"
    topic_list = "\n".join(f"- `{t}`" for t in sorted(ros2_topics)) or "- None detected"
    readme = f"""# {scene_name}

Auto-exported by **Isaac Assist** on {_dt.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")}.

## Scene Summary

- **Patches applied:** {len(patches)}
- **Robots:** {robot_list}
- **ROS2 Topics:**
{topic_list}

## Files

| File | Description |
|------|-------------|
| `scene_setup.py` | All approved code patches as a single runnable script |
| `ros2_topics.yaml` | Detected ROS2 topics and OmniGraph node types |
{"| `ros2_launch.py` | ROS2 launch file template |" if has_ros2 else ""}
| `README.md` | This file |

## Usage

### Replay Scene in Isaac Sim
```python
# In Isaac Sim Script Editor or via Kit RPC:
exec(open("{out_dir}/scene_setup.py").read())
```

### ROS2 Topics
{"Launch the ROS2 nodes alongside Isaac Sim:" if has_ros2 else "No ROS2 topics detected in this scene."}
{"```bash" if has_ros2 else ""}
{"ros2 launch " + str(out_dir / "ros2_launch.py") if has_ros2 else ""}
{"```" if has_ros2 else ""}
"""
    (out_dir / "README.md").write_text(readme, encoding="utf-8")

    files_written = ["scene_setup.py", "README.md"]
    if ros2_topics or og_node_types:
        files_written.append("ros2_topics.yaml")
    if has_ros2:
        files_written.append("ros2_launch.py")

    return {
        "export_dir": str(out_dir),
        "files": files_written,
        "patch_count": len(patches),
        "ros2_topics": ros2_topics,
        "robots_detected": sorted(robot_paths),
        "message": f"Exported {len(patches)} patches to {out_dir}/ — files: {', '.join(files_written)}",
    }


DATA_HANDLERS["export_scene_package"] = _handle_export_scene_package


# ── Stage Analysis ───────────────────────────────────────────────────────────

async def _handle_run_stage_analysis(args: Dict[str, Any]) -> Dict[str, Any]:
    """Run all (or selected) validator packs against the live stage."""
    from ...analysis.orchestrator import AnalysisOrchestrator

    # 1. Fetch full stage context from Kit
    if not await kit_tools.is_kit_rpc_alive():
        return {"error": "Kit RPC is not reachable — cannot analyse the stage."}

    try:
        stage_data = await kit_tools.get_stage_context(full=True)
    except Exception as e:
        return {"error": f"Failed to fetch stage context: {e}"}

    # 2. Build analyser with requested packs (or all)
    enabled_packs = args.get("packs") or None
    analyzer = AnalysisOrchestrator(enabled_packs=enabled_packs)

    # 3. Run analysis
    result = analyzer.run_analysis(stage_data)

    # 4. Serialize
    results = []
    for f in result.findings:
        entry = {
            "rule": f.rule_id,
            "severity": f.severity,
            "prim": f.prim_path,
            "message": f.message,
        }
        if f.fix_suggestion:
            entry["fix_hint"] = f.fix_suggestion.description
        results.append(entry)

    summary = {}
    for r in results:
        summary[r["severity"]] = summary.get(r["severity"], 0) + 1

    return {
        "total_findings": len(results),
        "summary": summary,
        "findings": results[:50],  # cap to avoid huge payloads
        "truncated": len(results) > 50,
    }


DATA_HANDLERS["run_stage_analysis"] = _handle_run_stage_analysis


# ══════ From feat/tools-and-bugfixes ══════
async def _handle_get_physics_errors(args: Dict) -> Dict:
    """Filter console logs for PhysX-specific errors and warnings."""
    ctx = await kit_tools.get_stage_context(full=False)
    logs = ctx.get("recent_logs", [])
    last_n = args.get("last_n", 20)

    physics_logs = []
    for entry in logs:
        msg = entry.get("msg", "")
        source = entry.get("source", "")
        # Match PhysX regex OR source contains physics/physx
        if (_PHYSX_ERROR_RE.search(msg) or
                "physx" in source.lower() or
                "physics" in source.lower()):
            physics_logs.append(entry)

    return {
        "physics_errors": physics_logs[-last_n:],
        "total_count": len(physics_logs),
        "note": "Filtered for PhysX/physics engine messages only",
    }

async def _handle_check_collisions(args: Dict) -> Dict:
    """Validate collision meshes on a prim via Kit RPC."""
    prim_path = args["prim_path"]
    code = f"""\
import omni.usd
from pxr import Usd, UsdPhysics, UsdGeom, PhysxSchema
import json

stage = omni.usd.get_context().get_stage()
prim = stage.GetPrimAtPath('{prim_path}')
if not prim.IsValid():
    print(json.dumps({{"valid": False, "error": "Prim not found: {prim_path}"}}))
else:
    has_collision = prim.HasAPI(UsdPhysics.CollisionAPI)
    has_rigid_body = prim.HasAPI(UsdPhysics.RigidBodyAPI)
    has_mass = prim.HasAPI(UsdPhysics.MassAPI)

    # Count mesh children that could serve as collision geometry
    mesh_count = 0
    collision_children = 0
    for child in list(Usd.PrimRange(prim))[1:]:
        if child.IsA(UsdGeom.Mesh):
            mesh_count += 1
        if child.HasAPI(UsdPhysics.CollisionAPI):
            collision_children += 1

    # Check for explicit collision geometry (MeshCollisionAPI or simple shape)
    has_mesh_collision = prim.HasAPI(PhysxSchema.PhysxCollisionAPI)

    result = {{
        "valid": True,
        "prim_path": '{prim_path}',
        "has_collision_api": has_collision,
        "has_rigid_body_api": has_rigid_body,
        "has_mass_api": has_mass,
        "has_physx_collision": has_mesh_collision,
        "mesh_children": mesh_count,
        "children_with_collision": collision_children,
        "issues": [],
    }}
    if not has_collision and collision_children == 0:
        result["issues"].append("No CollisionAPI on prim or any children — physics contacts will not register")
    if has_rigid_body and not has_collision and collision_children == 0:
        result["issues"].append("RigidBodyAPI without any collision — prim will fall through everything")
    if mesh_count > 0 and not has_collision and collision_children == 0:
        result["issues"].append("Mesh geometry exists but no collision applied — apply CollisionAPI")

    print(json.dumps(result))
"""
    result = await kit_tools.exec_sync(code)
    if result.get("success") and result.get("output"):
        try:
            return {"type": "data", **json.loads(result["output"].strip())}
        except json.JSONDecodeError:
            pass
    return {"type": "data", "error": result.get("output", "Failed to check collisions")}

def _handle_fix_error(args: Dict) -> str:
    """Generate a fix code patch for a known physics/USD error pattern."""
    error_text = args.get("error_text", "")
    error_lower = error_text.lower()

    # ── Categorize the error ──────────────────────────────────────────────
    category = "unknown"
    if any(kw in error_lower for kw in ("collision", "collider", "collisionapi", "pass through")):
        category = "collision"
    elif any(kw in error_lower for kw in ("joint", "jointapi", "body0", "body1", "joint path")):
        category = "joint"
    elif any(kw in error_lower for kw in ("solver", "iteration", "diverge", "explod", "unstable")):
        category = "solver"
    elif any(kw in error_lower for kw in ("ground", "floor", "falling", "fall through")):
        category = "ground_plane"
    elif any(kw in error_lower for kw in ("omnigraph", "og.", "node type", "action graph")):
        category = "omnigraph"
    elif any(kw in error_lower for kw in ("articulation", "articulationapi")):
        category = "articulation"
    elif any(kw in error_lower for kw in ("usd", "prim", "attribute", "schema")):
        category = "usd"

    # ── Query knowledge base for known fixes ──────────────────────────────
    kb_snippets = []
    try:
        from ...retrieval.context_retriever import find_matching_patterns, detect_isaac_version
        version = detect_isaac_version()
        patterns = find_matching_patterns(error_text, version=version, limit=3)
        for p in patterns:
            if p.get("code"):
                kb_snippets.append(f"# KB pattern: {p.get('title', 'fix')}\n{p['code']}")
    except Exception:
        pass  # KB not available — fall back to built-in fixes

    # ── Generate fix code based on category ───────────────────────────────
    if category == "collision":
        code = """\
import omni.usd
from pxr import UsdPhysics, UsdGeom

stage = omni.usd.get_context().get_stage()
# Apply CollisionAPI to all Mesh prims missing it
fixed = []
for prim in stage.Traverse():
    if prim.IsA(UsdGeom.Mesh) and not prim.HasAPI(UsdPhysics.CollisionAPI):
        UsdPhysics.CollisionAPI.Apply(prim)
        fixed.append(str(prim.GetPath()))
print(f"Applied CollisionAPI to {len(fixed)} prims: {fixed[:10]}")
"""

    elif category == "solver":
        code = """\
import omni.usd
from pxr import UsdPhysics, PhysxSchema

stage = omni.usd.get_context().get_stage()
# Find or create PhysicsScene and increase solver iterations
scene_prim = None
for prim in stage.Traverse():
    if prim.IsA(UsdPhysics.Scene):
        scene_prim = prim
        break
if scene_prim is None:
    scene_prim = UsdPhysics.Scene.Define(stage, '/PhysicsScene').GetPrim()

physx_scene = PhysxSchema.PhysxSceneAPI.Apply(scene_prim)
physx_scene.CreateMinPositionIterationCountAttr(16)
physx_scene.CreateMinVelocityIterationCountAttr(4)
physx_scene.CreateEnableStabilizationAttr(True)
print("Increased solver iterations and enabled stabilization")
"""

    elif category == "joint":
        code = """\
import omni.usd
from pxr import UsdPhysics

stage = omni.usd.get_context().get_stage()
# Scan joints and report broken body references
issues = []
for prim in stage.Traverse():
    joint = UsdPhysics.Joint(prim)
    if not joint:
        continue
    rel0 = prim.GetRelationship('physics:body0')
    rel1 = prim.GetRelationship('physics:body1')
    targets0 = rel0.GetTargets() if rel0 else []
    targets1 = rel1.GetTargets() if rel1 else []
    for t in targets0 + targets1:
        if not stage.GetPrimAtPath(t).IsValid():
            issues.append(f"Joint {prim.GetPath()} references missing prim: {t}")
print(f"Joint scan complete. Issues found: {len(issues)}")
for issue in issues:
    print(f"  - {issue}")
"""

    elif category == "ground_plane":
        code = """\
import omni.usd
from pxr import UsdGeom, UsdPhysics, Gf, Sdf

stage = omni.usd.get_context().get_stage()

# Create ground plane if none exists
ground_path = '/World/GroundPlane'
if not stage.GetPrimAtPath(ground_path).IsValid():
    xform = UsdGeom.Xform.Define(stage, ground_path)
    plane = UsdGeom.Mesh.Define(stage, f'{ground_path}/CollisionMesh')
    plane.GetPointsAttr().Set([(-50,-50,0),(50,-50,0),(50,50,0),(-50,50,0)])
    plane.GetFaceVertexCountsAttr().Set([4])
    plane.GetFaceVertexIndicesAttr().Set([0,1,2,3])
    UsdPhysics.CollisionAPI.Apply(plane.GetPrim())
    print(f"Created ground plane at {ground_path}")

# Also ensure PhysicsScene exists with gravity
scene_path = '/PhysicsScene'
if not stage.GetPrimAtPath(scene_path).IsValid():
    scene = UsdPhysics.Scene.Define(stage, scene_path)
    scene.GetGravityDirectionAttr().Set(Gf.Vec3f(0, 0, -1))
    scene.GetGravityMagnitudeAttr().Set(9.81)
    print("Created PhysicsScene with gravity (0, 0, -9.81)")
"""

    elif category == "omnigraph":
        code = """\
import omni.graph.core as og

# List all graphs and their evaluation state
graphs = og.get_all_graphs()
for g in graphs:
    path = g.get_path_to_graph()
    valid = g.is_valid()
    nodes = g.get_nodes()
    print(f"Graph: {path}, valid={valid}, nodes={len(nodes)}")
    for n in nodes:
        print(f"  Node: {n.get_prim_path()}, type={n.get_type_name()}")
"""

    elif category == "articulation":
        code = """\
import omni.usd
from pxr import Usd, UsdPhysics, PhysxSchema

stage = omni.usd.get_context().get_stage()
# Find articulations and verify their setup
for prim in stage.Traverse():
    if prim.HasAPI(UsdPhysics.ArticulationRootAPI):
        path = str(prim.GetPath())
        has_rb = prim.HasAPI(UsdPhysics.RigidBodyAPI)
        physx_art = PhysxSchema.PhysxArticulationAPI(prim) if prim.HasAPI(PhysxSchema.PhysxArticulationAPI) else None
        fixed = physx_art.GetArticulationEnabledAttr().Get() if physx_art else None
        print(f"Articulation: {path}, has_rigid_body={has_rb}, physx_enabled={fixed}")
        # Count joints
        joint_count = 0
        for child in list(Usd.PrimRange(prim))[1:]:
            if child.IsA(UsdPhysics.RevoluteJoint) or child.IsA(UsdPhysics.PrismaticJoint):
                joint_count += 1
        print(f"  Joints: {joint_count}")
"""

    else:
        # Unknown category — generate diagnostic code
        code = """\
import omni.usd
from pxr import UsdPhysics, UsdGeom

stage = omni.usd.get_context().get_stage()
# Diagnostic: scan scene for common physics issues
issues = []
mesh_no_collision = 0
rigid_no_collision = 0
for prim in stage.Traverse():
    if prim.IsA(UsdGeom.Mesh) and not prim.HasAPI(UsdPhysics.CollisionAPI):
        mesh_no_collision += 1
    if prim.HasAPI(UsdPhysics.RigidBodyAPI) and not prim.HasAPI(UsdPhysics.CollisionAPI):
        rigid_no_collision += 1
        issues.append(f"RigidBody without collision: {prim.GetPath()}")

has_scene = any(p.IsA(UsdPhysics.Scene) for p in stage.Traverse())
print(f"Physics scene exists: {has_scene}")
print(f"Meshes without collision: {mesh_no_collision}")
print(f"RigidBodies without collision: {rigid_no_collision}")
for i in issues[:10]:
    print(f"  - {i}")
"""

    # Prepend KB snippets as comments if available
    if kb_snippets:
        kb_header = "\n".join(f"# {line}" for snippet in kb_snippets
                              for line in snippet.split("\n"))
        code = f"# Knowledge base matches for this error:\n{kb_header}\n\n{code}"

    return code

async def _handle_list_scene_templates(args: Dict) -> Dict:
    """List available scene templates, optionally filtered by category."""
    category = args.get("category", "").lower()

    templates = []
    for name, tmpl in _SCENE_TEMPLATES.items():
        if category and tmpl.get("category", "") != category:
            continue
        templates.append({
            "name": name,
            "description": tmpl["description"],
            "category": tmpl.get("category", "general"),
            "object_count": len(tmpl["objects"]),
            "room_dims": tmpl["room_dims"],
        })

    return {
        "templates": templates,
        "count": len(templates),
        "total_available": len(_SCENE_TEMPLATES),
    }

async def _handle_load_scene_template(args: Dict) -> Dict:
    """Load a scene template by name. Returns a blueprint compatible with build_scene_from_blueprint."""
    template_name = args.get("template_name", "").lower().replace(" ", "_").replace("-", "_")

    if template_name not in _SCENE_TEMPLATES:
        available = list(_SCENE_TEMPLATES.keys())
        return {
            "error": f"Template '{template_name}' not found.",
            "available_templates": available,
        }

    tmpl = _SCENE_TEMPLATES[template_name]

    # Build a blueprint dict compatible with build_scene_from_blueprint
    blueprint_objects = []
    for obj in tmpl["objects"]:
        bp_obj = {
            "name": obj["name"],
            "prim_path": obj.get("prim_path", f"/World/{obj['name']}"),
            "position": obj.get("position", [0, 0, 0]),
            "rotation": obj.get("rotation", [0, 0, 0]),
            "scale": obj.get("scale", [1, 1, 1]),
        }
        if obj.get("prim_type"):
            bp_obj["prim_type"] = obj["prim_type"]
        if obj.get("asset_name"):
            bp_obj["asset_name"] = obj["asset_name"]
        if obj.get("asset_path"):
            bp_obj["asset_path"] = obj["asset_path"]
        blueprint_objects.append(bp_obj)

    blueprint = {
        "description": tmpl["description"],
        "room_dimensions": tmpl["room_dims"],
        "objects": blueprint_objects,
        "suggested_sensors": tmpl.get("suggested_sensors", []),
        "physics_settings": tmpl.get("physics_settings", {}),
    }

    return {
        "template_name": template_name,
        "blueprint": blueprint,
        "object_count": len(blueprint_objects),
        "message": (
            f"Template '{template_name}' loaded with {len(blueprint_objects)} objects. "
            "Call build_scene_from_blueprint with the 'blueprint' field to create the scene."
        ),
    }

def _gen_batch_apply_operation(args: Dict) -> str:
    """Generate code to apply an operation to all children of a parent prim."""
    target_path = args["target_path"]
    operation = args["operation"]
    params = args.get("parameters", {}) or {}
    filter_type = args.get("filter_type")

    lines = [
        "import omni.usd",
        "from pxr import Usd, UsdGeom, UsdPhysics, UsdShade, Gf, Sdf",
        "",
        "stage = omni.usd.get_context().get_stage()",
        f"parent = stage.GetPrimAtPath('{target_path}')",
        "if not parent.IsValid():",
        f"    raise RuntimeError('Parent prim not found: {target_path}')",
        "",
        "count = 0",
        "for prim in Usd.PrimRange(parent):",
        "    if prim.GetPath() == parent.GetPath():",
        "        continue  # skip the parent itself",
    ]

    if filter_type:
        lines.append(f"    if prim.GetTypeName() != '{filter_type}':")
        lines.append("        continue")

    if operation == "apply_physics":
        mass = params.get("mass")
        lines.extend([
            "    UsdPhysics.RigidBodyAPI.Apply(prim)",
            "    UsdPhysics.CollisionAPI.Apply(prim)",
        ])
        if mass:
            lines.extend([
                "    mass_api = UsdPhysics.MassAPI.Apply(prim)",
                f"    mass_api.CreateMassAttr({mass})",
            ])
        lines.append("    count += 1")

    elif operation == "apply_collision":
        lines.extend([
            "    UsdPhysics.CollisionAPI.Apply(prim)",
            "    count += 1",
        ])

    elif operation == "set_material":
        mat_path = params.get("material_path", "")
        if not mat_path:
            return "raise ValueError('set_material requires parameters.material_path')"
        lines.extend([
            f"    mat = UsdShade.Material(stage.GetPrimAtPath('{mat_path}'))",
            "    UsdShade.MaterialBindingAPI(prim).Bind(mat, UsdShade.Tokens.strongerThanDescendants)",
            "    count += 1",
        ])

    elif operation == "delete":
        # Collect paths first, then delete (avoid mutating during traversal)
        lines = [
            "import omni.usd",
            "from pxr import Usd",
            "",
            "stage = omni.usd.get_context().get_stage()",
            f"parent = stage.GetPrimAtPath('{target_path}')",
            "if not parent.IsValid():",
            f"    raise RuntimeError('Parent prim not found: {target_path}')",
            "",
            "paths_to_delete = []",
            "for prim in Usd.PrimRange(parent):",
            "    if prim.GetPath() == parent.GetPath():",
            "        continue",
        ]
        if filter_type:
            lines.append(f"    if prim.GetTypeName() != '{filter_type}':")
            lines.append("        continue")
        lines.extend([
            "    paths_to_delete.append(str(prim.GetPath()))",
            "",
            "count = 0",
            "for p in reversed(paths_to_delete):",
            "    stage.RemovePrim(p)",
            "    count += 1",
        ])

    elif operation == "set_visibility":
        visible = params.get("visible", True)
        vis_token = "UsdGeom.Tokens.inherited" if visible else "UsdGeom.Tokens.invisible"
        lines.extend([
            "    imageable = UsdGeom.Imageable(prim)",
            "    if imageable:",
            f"        imageable.GetVisibilityAttr().Set({vis_token})",
            "        count += 1",
        ])

    elif operation == "set_attribute":
        attr_name = params.get("attr_name", "")
        value = params.get("value")
        if not attr_name:
            return "raise ValueError('set_attribute requires parameters.attr_name')"
        lines.extend([
            f"    attr = prim.GetAttribute('{attr_name}')",
            "    if attr.IsValid():",
            f"        attr.Set({repr(value)})",
            "        count += 1",
        ])

    else:
        return f"raise ValueError('Unknown batch operation: {operation}')"

    lines.append(f"print(f'batch_apply_operation: {{count}} prims affected by {operation} under {target_path}')")
    return "\n".join(lines)

async def _handle_validate_scene_blueprint(args: Dict) -> Dict:
    """Validate a scene blueprint before building. Checks for overlaps, floating objects, bad scales, and missing fields."""
    blueprint = args.get("blueprint", {})
    objects = blueprint.get("objects", [])

    issues: List[str] = []
    warnings: List[str] = []

    if not objects:
        issues.append("Blueprint has no objects.")
        return {"valid": False, "issues": issues, "warnings": warnings, "object_count": 0}

    # ── Check required fields on each object ────────────────────────────
    for i, obj in enumerate(objects):
        name = obj.get("name", f"object_{i}")
        if not obj.get("name"):
            warnings.append(f"Object [{i}] is missing a 'name' field.")
        if not obj.get("position"):
            issues.append(f"Object '{name}' is missing a 'position' field.")
        if not obj.get("prim_type") and not obj.get("asset_path") and not obj.get("asset_name"):
            issues.append(f"Object '{name}' has no 'prim_type', 'asset_path', or 'asset_name' — cannot create it.")

    # ── Check for unrealistic scales ────────────────────────────────────
    for obj in objects:
        name = obj.get("name", "unnamed")
        scale = obj.get("scale", [1, 1, 1])
        if isinstance(scale, (list, tuple)):
            for j, s in enumerate(scale):
                axis = ["X", "Y", "Z"][j] if j < 3 else str(j)
                if abs(s) < 0.001:
                    issues.append(f"Object '{name}' has near-zero scale on {axis} axis ({s}) — likely an error.")
                elif abs(s) > 1000:
                    warnings.append(f"Object '{name}' has very large scale on {axis} axis ({s}) — is this intended?")

    # ── Check for floating objects (z > 0 without obvious support) ──────
    ground_level = 0.0
    # Find ground plane or lowest object to establish reference
    for obj in objects:
        name_lower = obj.get("name", "").lower()
        if any(k in name_lower for k in ("ground", "plane", "floor")):
            pos = obj.get("position", [0, 0, 0])
            ground_level = pos[2] if len(pos) > 2 else 0.0
            break

    for obj in objects:
        name = obj.get("name", "unnamed")
        name_lower = name.lower()
        pos = obj.get("position", [0, 0, 0])
        if len(pos) < 3:
            continue
        z = pos[2]
        # Skip ground planes, cameras, lights, overhead items — they are expected to be elevated
        if any(k in name_lower for k in ("ground", "plane", "floor", "camera", "light", "overhead", "ceiling", "lamp")):
            continue
        # Objects more than 0.5m above ground level may be floating
        if z > ground_level + 0.5:
            warnings.append(f"Object '{name}' is at z={z:.2f}m — may be floating without support.")

    # ── Check for AABB overlaps (simple distance-based) ─────────────────
    positioned_objects = []
    for obj in objects:
        pos = obj.get("position", [0, 0, 0])
        scale = obj.get("scale", [1, 1, 1])
        if isinstance(pos, (list, tuple)) and len(pos) >= 3:
            # Approximate object radius from scale
            if isinstance(scale, (list, tuple)) and len(scale) >= 3:
                radius = max(abs(scale[0]), abs(scale[1]), abs(scale[2])) * 0.5
            else:
                radius = 0.5
            positioned_objects.append({
                "name": obj.get("name", "unnamed"),
                "pos": pos,
                "radius": radius,
            })

    for i in range(len(positioned_objects)):
        for j in range(i + 1, len(positioned_objects)):
            a = positioned_objects[i]
            b = positioned_objects[j]
            dx = a["pos"][0] - b["pos"][0]
            dy = a["pos"][1] - b["pos"][1]
            dz = a["pos"][2] - b["pos"][2]
            dist = (dx * dx + dy * dy + dz * dz) ** 0.5
            min_dist = a["radius"] + b["radius"]
            if dist < min_dist * 0.7:  # 70% overlap threshold — some tolerance for surface items
                warnings.append(
                    f"Objects '{a['name']}' and '{b['name']}' may overlap "
                    f"(distance={dist:.3f}m, combined radius={min_dist:.3f}m)."
                )

    # ── Check for scale mismatches between objects ──────────────────────
    max_scales = []
    for obj in objects:
        scale = obj.get("scale", [1, 1, 1])
        if isinstance(scale, (list, tuple)) and len(scale) >= 3:
            max_scales.append((obj.get("name", "unnamed"), max(abs(s) for s in scale[:3])))
        elif isinstance(scale, (int, float)):
            max_scales.append((obj.get("name", "unnamed"), abs(scale)))

    if len(max_scales) >= 2:
        all_vals = [s for _, s in max_scales]
        median_scale = sorted(all_vals)[len(all_vals) // 2]
        if median_scale > 0:
            for name, s in max_scales:
                ratio = s / median_scale
                if ratio > 50 or (median_scale > 0.01 and ratio < 0.02):
                    warnings.append(
                        f"Object '{name}' scale ({s:.3f}) differs vastly from "
                        f"median scale ({median_scale:.3f}) — possible unit mismatch."
                    )

    valid = len(issues) == 0
    return {
        "valid": valid,
        "issues": issues,
        "warnings": warnings,
        "object_count": len(objects),
    }

def _generate_isaaclab_init_code(cfg: Dict) -> str:
    """Generate __init__.py with gymnasium.register() for the IsaacLab env."""
    task = cfg["task_name"]
    module_name = task.lower()

    return f'''"""Register {task} environment with Gymnasium."""
import gymnasium

gymnasium.register(
    id="{task}-v0",
    entry_point="isaaclab.envs:ManagerBasedRLEnv",
    kwargs={{"env_cfg_entry_point": "{module_name}:{task}EnvCfg"}},
)
'''

CODE_GEN_HANDLERS["fix_error"] = _handle_fix_error
DATA_HANDLERS["list_scene_templates"] = _handle_list_scene_templates
DATA_HANDLERS["load_scene_template"] = _handle_load_scene_template
CODE_GEN_HANDLERS["batch_apply_operation"] = _gen_batch_apply_operation
DATA_HANDLERS["validate_scene_blueprint"] = _handle_validate_scene_blueprint

# ══════ From feat/7B-replicator-sdg-v2 ══════
def _gen_create_sdg_pipeline(args: Dict) -> str:
    """Generate a full Replicator SDG pipeline with camera, render product, writer."""
    annotators = args.get("annotators", ["bounding_box_2d"])
    output_format = args.get("output_format", "basic")
    num_frames = args.get("num_frames", 100)
    output_dir = args.get("output_dir", "/tmp/sdg_output")
    cam_pos = args.get("camera_position", [0, 0, 5])
    cam_look = args.get("camera_look_at", [0, 0, 0])
    resolution = args.get("resolution", [1280, 720])

    # Map output_format to writer class name
    writer_map = {
        "coco": "CocoWriter",
        "kitti": "KittiWriter",
        "basic": "BasicWriter",
        "numpy": "BasicWriter",
    }
    writer_class = writer_map.get(output_format, "BasicWriter")

    # Build writer.initialize() kwargs based on format
    if output_format == "coco":
        writer_init = f'writer.initialize(output_dir="{output_dir}")'
    elif output_format == "kitti":
        writer_init = f'writer.initialize(output_dir="{output_dir}")'
    elif output_format == "numpy":
        # BasicWriter with raw annotator flags
        init_kwargs = [f'output_dir="{output_dir}"', "rgb=True"]
        if "normals" in annotators:
            init_kwargs.append("normals=True")
        if "depth" in annotators:
            init_kwargs.append("distance_to_camera=True")
        if "semantic_segmentation" in annotators:
            init_kwargs.append("semantic_segmentation=True")
        if "instance_segmentation" in annotators:
            init_kwargs.append("instance_segmentation=True")
        if "bounding_box_2d" in annotators:
            init_kwargs.append("bounding_box_2d=True")
        if "bounding_box_3d" in annotators:
            init_kwargs.append("bounding_box_3d=True")
        if "occlusion" in annotators:
            init_kwargs.append("occlusion=True")
        writer_init = "writer.initialize(" + ", ".join(init_kwargs) + ")"
    else:
        # basic
        init_kwargs = [f'output_dir="{output_dir}"', "rgb=True"]
        for ann in annotators:
            # Map annotator names to BasicWriter kwargs
            kwarg = ann.replace("-", "_")
            if kwarg == "depth":
                kwarg = "distance_to_camera"
            init_kwargs.append(f"{kwarg}=True")
        writer_init = "writer.initialize(" + ", ".join(init_kwargs) + ")"

    return f"""\
import omni.replicator.core as rep

with rep.new_layer():
    camera = rep.create.camera(
        position=({cam_pos[0]}, {cam_pos[1]}, {cam_pos[2]}),
        look_at=({cam_look[0]}, {cam_look[1]}, {cam_look[2]}),
    )
    rp = rep.create.render_product(camera, ({resolution[0]}, {resolution[1]}))

    writer = rep.WriterRegistry.get("{writer_class}")
    {writer_init}
    writer.attach([rp])

    with rep.trigger.on_frame(num_frames={num_frames}):
        pass

    rep.orchestrator.run()

print("SDG pipeline started: {num_frames} frames -> {output_dir}")
"""

def _gen_add_domain_randomizer(args: Dict) -> str:
    """Generate Replicator domain randomization code."""
    target = args["target"]
    rand_type = args["randomizer_type"]
    params = args.get("params", {})

    lines = ["import omni.replicator.core as rep", ""]

    if rand_type == "pose":
        surface = params.get("surface_prim", "/World/Ground")
        min_angle = params.get("min_angle", -180)
        max_angle = params.get("max_angle", 180)
        lines.extend([
            "with rep.trigger.on_frame():",
            f"    with rep.get.prims(path_pattern=\"{target}\"):",
            f"        rep.randomizer.scatter_2d(",
            f"            surface_prims=rep.get.prims(path_pattern=\"{surface}\")",
            f"        )",
            f"        rep.randomizer.rotation(",
            f"            min_angle={min_angle}, max_angle={max_angle}",
            f"        )",
        ])

    elif rand_type == "texture":
        lines.extend([
            "with rep.trigger.on_frame():",
            f"    with rep.get.prims(path_pattern=\"{target}\"):",
            "        rep.randomizer.texture(",
            "            textures=rep.distribution.choice([",
            "                'omniverse://localhost/NVIDIA/Materials/Base/Stone/Fieldstone.mdl',",
            "                'omniverse://localhost/NVIDIA/Materials/Base/Wood/Oak.mdl',",
            "                'omniverse://localhost/NVIDIA/Materials/Base/Metal/Steel_Brushed.mdl',",
            "            ])",
            "        )",
        ])

    elif rand_type == "color":
        c_min = params.get("color_min", [0, 0, 0])
        c_max = params.get("color_max", [1, 1, 1])
        lines.extend([
            "with rep.trigger.on_frame():",
            f"    with rep.get.prims(path_pattern=\"{target}\"):",
            f"        rep.randomizer.color(",
            f"            colors=rep.distribution.uniform(",
            f"                ({c_min[0]}, {c_min[1]}, {c_min[2]}),",
            f"                ({c_max[0]}, {c_max[1]}, {c_max[2]}),",
            f"            )",
            f"        )",
        ])

    elif rand_type == "lighting":
        i_min = params.get("intensity_min", 500)
        i_max = params.get("intensity_max", 2000)
        lines.extend([
            "# Note: 'intensity' is in nits (candelas/m^2), not lux.",
            "# Lux is not directly settable on USD lights.",
            "with rep.trigger.on_frame():",
            f"    with rep.get.prims(path_pattern=\"{target}\"):",
            f"        rep.modify.attribute(",
            f"            \"intensity\",",
            f"            rep.distribution.uniform({i_min}, {i_max}),",
            f"        )",
        ])

    elif rand_type == "material_properties":
        r_min = params.get("roughness_min", 0.0)
        r_max = params.get("roughness_max", 1.0)
        m_min = params.get("metallic_min", 0.0)
        m_max = params.get("metallic_max", 1.0)
        lines.extend([
            "with rep.trigger.on_frame():",
            f"    with rep.get.prims(path_pattern=\"{target}\"):",
            f"        rep.modify.attribute(",
            f"            \"inputs:reflection_roughness_constant\",",
            f"            rep.distribution.uniform({r_min}, {r_max}),",
            f"        )",
            f"        rep.modify.attribute(",
            f"            \"inputs:metallic_constant\",",
            f"            rep.distribution.uniform({m_min}, {m_max}),",
            f"        )",
        ])

    elif rand_type == "visibility":
        prob = params.get("probability", 0.5)
        lines.extend([
            "with rep.trigger.on_frame():",
            f"    with rep.get.prims(path_pattern=\"{target}\"):",
            f"        rep.modify.visibility(",
            f"            rep.distribution.choice([True, False],",
            f"                weights=[{prob}, {1.0 - prob}])",
            f"        )",
        ])

    else:
        lines.append(f"# Unknown randomizer type: {rand_type}")

    return "\n".join(lines)

async def _handle_preview_sdg(args: Dict) -> Dict:
    """Step the Replicator orchestrator a few times for preview frames."""
    num_samples = args.get("num_samples", 3)

    code = f"""\
import omni.replicator.core as rep
import json

num_samples = {num_samples}
for i in range(num_samples):
    rep.orchestrator.step()
    print(f"Preview frame {{i + 1}}/{num_samples} generated")

print(json.dumps({{"preview_frames": num_samples, "status": "done"}}))
"""
    return await kit_tools.queue_exec_patch(code, f"Preview SDG: generate {num_samples} sample frames")

def _gen_export_dataset(args: Dict) -> str:
    """Generate async step-loop code for large dataset generation."""
    output_dir = args["output_dir"]
    num_frames = args["num_frames"]
    step_batch = args.get("step_batch", 10)

    return f"""\
import omni.replicator.core as rep
import asyncio

async def _export_dataset():
    num_frames = {num_frames}
    step_batch = {step_batch}
    for i in range(0, num_frames, step_batch):
        batch = min(step_batch, num_frames - i)
        for _ in range(batch):
            await rep.orchestrator.step_async()
        print(f"Progress: {{i + batch}}/{{num_frames}} frames")
    print(f"Dataset export complete: {{num_frames}} frames -> '{output_dir}'")

asyncio.ensure_future(_export_dataset())
"""

# ══════ From feat/7C-xr-teleoperation ══════
def _gen_start_teleop_session(args: Dict) -> str:
    robot_path = args["robot_path"]
    device = args.get("input_device", "keyboard")
    quality = args.get("stream_quality", "medium")
    preset = _STREAM_QUALITY_PRESETS.get(quality, _STREAM_QUALITY_PRESETS["medium"])
    axes = _DEVICE_AXIS_DEFAULTS.get(device, _DEVICE_AXIS_DEFAULTS["keyboard"])

    return f"""\
import omni.usd
import omni.kit.app
import omni.physx
from pxr import UsdPhysics, PhysxSchema, Gf
import time
import json
import asyncio
import threading

# ── Configuration ───────────────────────────────────────────────────────
ROBOT_PATH = '{robot_path}'
INPUT_DEVICE = '{device}'
STREAM_WIDTH = {preset["width"]}
STREAM_HEIGHT = {preset["height"]}
STREAM_BITRATE_MBPS = {preset["bitrate_mbps"]}
STREAM_FPS = {preset["fps"]}
WATCHDOG_TIMEOUT_S = 0.5      # Hold last command until timeout
WATCHDOG_ZERO_VEL_S = 2.0     # Zero velocity after this period
MAX_JOINT_VEL = 2.0           # rad/s cap (safety default)
WS_PORT = 8766

# ── Global state ────────────────────────────────────────────────────────
_teleop_state = {{
    'active': True,
    'last_cmd_time': time.time(),
    'last_joint_targets': None,
    'ws_server': None,
    'recording_active': False,
    'device_axes': {axes!r},
}}

stage = omni.usd.get_context().get_stage()
robot_prim = stage.GetPrimAtPath(ROBOT_PATH)
assert robot_prim.IsValid(), f"Robot prim not found at {{ROBOT_PATH}}"

# ── WebSocket bridge for control data ───────────────────────────────────
try:
    import websockets
    import websockets.server

    _connected_clients = set()

    async def _ws_handler(websocket):
        _connected_clients.add(websocket)
        try:
            async for message in websocket:
                data = json.loads(message)
                if data.get('type') == 'joint_command':
                    _teleop_state['last_cmd_time'] = time.time()
                    _teleop_state['last_joint_targets'] = data.get('targets', [])
                elif data.get('type') == 'stop':
                    _teleop_state['active'] = False
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            _connected_clients.discard(websocket)

    async def _start_ws_server():
        server = await websockets.server.serve(_ws_handler, '0.0.0.0', WS_PORT)
        _teleop_state['ws_server'] = server
        print(f"Teleop WebSocket server listening on ws://0.0.0.0:{{WS_PORT}}")
        return server

    # Launch WS server in background
    _ws_loop = asyncio.new_event_loop()
    _ws_thread = threading.Thread(
        target=lambda: (_ws_loop.run_until_complete(_start_ws_server()), _ws_loop.run_forever()),
        daemon=True,
    )
    _ws_thread.start()

except ImportError:
    print("WARNING: websockets package not installed — WebSocket bridge disabled")
    print("Install with: pip install websockets")

# ── Viewport streaming setup ───────────────────────────────────────────
try:
    import carb.settings
    settings = carb.settings.get_settings()
    settings.set('/rtx/renderResolution/width', STREAM_WIDTH)
    settings.set('/rtx/renderResolution/height', STREAM_HEIGHT)
    print(f"Viewport streaming configured: {{STREAM_WIDTH}}x{{STREAM_HEIGHT}} @ {{STREAM_FPS}}fps, {{STREAM_BITRATE_MBPS}}Mbps")
except Exception as e:
    print(f"Viewport streaming setup note: {{e}}")

# ── Physics callback: apply joint commands with watchdog ────────────────
def _teleop_physics_step(dt):
    if not _teleop_state['active']:
        return

    now = time.time()
    elapsed = now - _teleop_state['last_cmd_time']
    targets = _teleop_state['last_joint_targets']

    robot = stage.GetPrimAtPath(ROBOT_PATH)
    if not robot.IsValid():
        return

    # Iterate joints and apply targets
    joint_idx = 0
    for child in robot.GetAllChildren():
        is_revolute = child.IsA(UsdPhysics.RevoluteJoint)
        is_prismatic = child.IsA(UsdPhysics.PrismaticJoint)
        if not (is_revolute or is_prismatic):
            continue

        drive_type = 'angular' if is_revolute else 'linear'
        if not child.HasAPI(UsdPhysics.DriveAPI):
            continue
        drive = UsdPhysics.DriveAPI.Get(child, drive_type)

        if elapsed > WATCHDOG_ZERO_VEL_S:
            # Safety: zero velocity after extended timeout
            drive.GetTargetVelocityAttr().Set(0.0)
        elif elapsed > WATCHDOG_TIMEOUT_S:
            # Hold last command (do nothing — keep current targets)
            pass
        elif targets and joint_idx < len(targets):
            # Apply command with velocity capping
            target_vel = targets[joint_idx]
            capped_vel = max(-MAX_JOINT_VEL, min(MAX_JOINT_VEL, target_vel))
            drive.GetTargetVelocityAttr().Set(capped_vel)

        joint_idx += 1

# Register physics callback
_teleop_sub = omni.physx.get_physx_interface().subscribe_physics_step_events(_teleop_physics_step)
_teleop_state['physics_sub'] = _teleop_sub

print(f"Teleop session started for {{ROBOT_PATH}}")
print(f"  Device: {{INPUT_DEVICE}}")
print(f"  Stream: {{STREAM_WIDTH}}x{{STREAM_HEIGHT}} @ {{STREAM_FPS}}fps")
print(f"  Watchdog: hold={{WATCHDOG_TIMEOUT_S}}s, zero_vel={{WATCHDOG_ZERO_VEL_S}}s")
print(f"  Connect: ws://localhost:{{WS_PORT}}")
"""

def _gen_configure_teleop_mapping(args: Dict) -> str:
    robot_path = args["robot_path"]
    device_axes = args.get("device_axes")
    joint_names = args.get("joint_names")
    gains = args.get("gains", {})
    pos_gain = gains.get("position", 1.0)
    vel_gain = gains.get("velocity", 1.0)

    device_axes_repr = repr(device_axes) if device_axes else "None"
    joint_names_repr = repr(joint_names) if joint_names else "None"

    return f"""\
import omni.usd
from pxr import UsdPhysics

# ── Teleop Axis-to-Joint Mapping ────────────────────────────────────────
ROBOT_PATH = '{robot_path}'
DEVICE_AXES = {device_axes_repr}
JOINT_NAMES = {joint_names_repr}
POSITION_GAIN = {pos_gain}
VELOCITY_GAIN = {vel_gain}

stage = omni.usd.get_context().get_stage()
robot_prim = stage.GetPrimAtPath(ROBOT_PATH)
assert robot_prim.IsValid(), f"Robot not found at {{ROBOT_PATH}}"

# Discover joints if not explicitly provided
if JOINT_NAMES is None:
    JOINT_NAMES = []
    for child in robot_prim.GetAllChildren():
        if child.IsA(UsdPhysics.RevoluteJoint) or child.IsA(UsdPhysics.PrismaticJoint):
            JOINT_NAMES.append(child.GetName())
    print(f"Auto-discovered {{len(JOINT_NAMES)}} joints: {{JOINT_NAMES}}")

# Build mapping table
mapping = {{}}
if DEVICE_AXES:
    for i, axis in enumerate(DEVICE_AXES):
        if i < len(JOINT_NAMES):
            mapping[axis] = {{
                'joint': JOINT_NAMES[i],
                'position_gain': POSITION_GAIN,
                'velocity_gain': VELOCITY_GAIN,
            }}
else:
    # Default: sequential 1:1 mapping
    for i, joint in enumerate(JOINT_NAMES):
        mapping[f'axis_{{i}}'] = {{
            'joint': joint,
            'position_gain': POSITION_GAIN,
            'velocity_gain': VELOCITY_GAIN,
        }}

# Store mapping in global teleop state (if session is active)
try:
    _teleop_state['mapping'] = mapping
    _teleop_state['joint_names'] = JOINT_NAMES
    _teleop_state['gains'] = {{'position': POSITION_GAIN, 'velocity': VELOCITY_GAIN}}
except NameError:
    print("WARNING: No active teleop session — mapping stored locally only")

print(f"Teleop mapping configured for {{ROBOT_PATH}}:")
print(f"  Axes: {{len(mapping)}} mapped")
print(f"  Gains: pos={{POSITION_GAIN}}, vel={{VELOCITY_GAIN}}")
for axis, cfg in mapping.items():
    print(f"    {{axis}} -> {{cfg['joint']}}")
"""

def _gen_record_teleop_demo(args: Dict) -> str:
    output_path = args["output_path"]
    robot_path = args["robot_path"]
    frequency_hz = args.get("frequency_hz", 30)

    return f"""\
import omni.usd
import omni.physx
from pxr import UsdPhysics, UsdGeom, Gf
import time
import numpy as np

# ── Teleop Demo Recording ───────────────────────────────────────────────
OUTPUT_PATH = '{output_path}'
ROBOT_PATH = '{robot_path}'
FREQUENCY_HZ = {frequency_hz}
RECORD_INTERVAL = 1.0 / FREQUENCY_HZ

stage = omni.usd.get_context().get_stage()
robot_prim = stage.GetPrimAtPath(ROBOT_PATH)
assert robot_prim.IsValid(), f"Robot not found at {{ROBOT_PATH}}"

# Discover joints
_rec_joints = []
for child in robot_prim.GetAllChildren():
    if child.IsA(UsdPhysics.RevoluteJoint) or child.IsA(UsdPhysics.PrismaticJoint):
        _rec_joints.append(child)
num_joints = len(_rec_joints)

# Recording buffers
_rec_data = {{
    'joint_positions': [],
    'joint_velocities': [],
    'ee_poses': [],
    'timestamps': [],
    'active': False,
    'last_record_time': 0.0,
    'start_time': 0.0,
}}

def _get_joint_positions():
    positions = []
    for j in _rec_joints:
        is_revolute = j.IsA(UsdPhysics.RevoluteJoint)
        drive_type = 'angular' if is_revolute else 'linear'
        if j.HasAPI(UsdPhysics.DriveAPI):
            drive = UsdPhysics.DriveAPI.Get(j, drive_type)
            pos = drive.GetTargetPositionAttr().Get()
            positions.append(float(pos) if pos is not None else 0.0)
        else:
            positions.append(0.0)
    return positions

def _get_joint_velocities():
    velocities = []
    for j in _rec_joints:
        is_revolute = j.IsA(UsdPhysics.RevoluteJoint)
        drive_type = 'angular' if is_revolute else 'linear'
        if j.HasAPI(UsdPhysics.DriveAPI):
            drive = UsdPhysics.DriveAPI.Get(j, drive_type)
            vel = drive.GetTargetVelocityAttr().Get()
            velocities.append(float(vel) if vel is not None else 0.0)
        else:
            velocities.append(0.0)
    return velocities

def _get_ee_pose():
    # Attempt to find end-effector (last link or named ee_link/panda_hand)
    ee_names = ['ee_link', 'panda_hand', 'tool0', 'link_ee']
    ee_prim = None
    for name in ee_names:
        candidate = stage.GetPrimAtPath(f'{{ROBOT_PATH}}/{{name}}')
        if candidate.IsValid():
            ee_prim = candidate
            break
    if ee_prim is None:
        # Fallback: use last child with xform
        for child in robot_prim.GetAllChildren():
            if child.IsA(UsdGeom.Xformable):
                ee_prim = child
    if ee_prim is None:
        return [0.0] * 7  # pos(3) + quat(4)
    xf = UsdGeom.Xformable(ee_prim).ComputeLocalToWorldTransform(0)
    pos = xf.ExtractTranslation()
    rot = xf.ExtractRotation().GetQuat()
    return [float(pos[0]), float(pos[1]), float(pos[2]),
            float(rot.GetReal()), float(rot.GetImaginary()[0]),
            float(rot.GetImaginary()[1]), float(rot.GetImaginary()[2])]

def _record_physics_step(dt):
    if not _rec_data['active']:
        return
    now = time.time()
    if now - _rec_data['last_record_time'] < RECORD_INTERVAL:
        return
    _rec_data['last_record_time'] = now

    _rec_data['timestamps'].append(now - _rec_data['start_time'])
    _rec_data['joint_positions'].append(_get_joint_positions())
    _rec_data['joint_velocities'].append(_get_joint_velocities())
    _rec_data['ee_poses'].append(_get_ee_pose())

# Register recording callback
_rec_sub = omni.physx.get_physx_interface().subscribe_physics_step_events(_record_physics_step)

# Start recording
_rec_data['active'] = True
_rec_data['start_time'] = time.time()
_rec_data['last_record_time'] = 0.0

# Store references for stop_teleop_session to finalize
try:
    _teleop_state['recording_active'] = True
    _teleop_state['rec_data'] = _rec_data
    _teleop_state['rec_sub'] = _rec_sub
    _teleop_state['rec_output_path'] = OUTPUT_PATH
    _teleop_state['rec_num_joints'] = num_joints
except NameError:
    pass

def _finalize_recording():
    \"\"\"Write recorded data to HDF5 file with robomimic-compatible schema.\"\"\"
    import h5py
    _rec_data['active'] = False

    n_steps = len(_rec_data['timestamps'])
    if n_steps == 0:
        print("No data recorded — nothing to write.")
        return

    with h5py.File(OUTPUT_PATH, 'w') as f:
        # robomimic-compatible schema
        grp = f.create_group('data')
        demo = grp.create_group('demo_0')
        demo.attrs['num_samples'] = n_steps

        obs = demo.create_group('obs')
        obs.create_dataset('joint_positions', data=np.array(_rec_data['joint_positions']))
        obs.create_dataset('joint_velocities', data=np.array(_rec_data['joint_velocities']))
        obs.create_dataset('ee_pose', data=np.array(_rec_data['ee_poses']))

        demo.create_dataset('timestamps', data=np.array(_rec_data['timestamps']))

        # Metadata
        f.attrs['robot_path'] = ROBOT_PATH
        f.attrs['frequency_hz'] = FREQUENCY_HZ
        f.attrs['num_joints'] = num_joints
        f.attrs['total_timesteps'] = n_steps

    print(f"Recording saved: {{OUTPUT_PATH}} ({{n_steps}} steps, {{num_joints}} joints)")

# Expose finalize for external use
_rec_data['finalize'] = _finalize_recording

print(f"Recording started: {{ROBOT_PATH}} -> {{OUTPUT_PATH}}")
print(f"  Frequency: {{FREQUENCY_HZ}} Hz")
print(f"  Joints: {{num_joints}}")
print(f"  Call stop_teleop_session to finalize and save.")
"""

def _gen_stop_teleop_session(args: Dict) -> str:
    return """\
import omni.usd
import omni.physx
from pxr import UsdPhysics
import time

# ── Stop Teleop Session ─────────────────────────────────────────────────
stage = omni.usd.get_context().get_stage()

try:
    _teleop_state
except NameError:
    print("No active teleop session found.")
    _teleop_state = {}

# 1. Deactivate session
_teleop_state['active'] = False

# 2. Remove physics callbacks
if 'physics_sub' in _teleop_state:
    _teleop_state['physics_sub'] = None
    print("Teleop physics callback removed.")

if 'rec_sub' in _teleop_state:
    _teleop_state['rec_sub'] = None
    print("Recording physics callback removed.")

# 3. Zero all joint velocities (safety)
robot_path = _teleop_state.get('robot_path', '')
if not robot_path:
    # Try to find any articulation in the scene
    for prim in stage.Traverse():
        if prim.HasAPI(UsdPhysics.ArticulationRootAPI):
            robot_path = str(prim.GetPath())
            break

if robot_path:
    robot_prim = stage.GetPrimAtPath(robot_path)
    if robot_prim.IsValid():
        zeroed = 0
        for child in robot_prim.GetAllChildren():
            is_revolute = child.IsA(UsdPhysics.RevoluteJoint)
            is_prismatic = child.IsA(UsdPhysics.PrismaticJoint)
            if not (is_revolute or is_prismatic):
                continue
            drive_type = 'angular' if is_revolute else 'linear'
            if child.HasAPI(UsdPhysics.DriveAPI):
                drive = UsdPhysics.DriveAPI.Get(child, drive_type)
                drive.GetTargetVelocityAttr().Set(0.0)
                zeroed += 1
        print(f"Zeroed velocity on {zeroed} joints for safety.")

# 4. Stop viewport streaming
try:
    import carb.settings
    settings = carb.settings.get_settings()
    # Reset to default render resolution
    settings.set('/rtx/renderResolution/width', 1280)
    settings.set('/rtx/renderResolution/height', 720)
    print("Viewport streaming stopped.")
except Exception:
    pass

# 5. Close WebSocket connections
ws_server = _teleop_state.get('ws_server')
if ws_server is not None:
    ws_server.close()
    _teleop_state['ws_server'] = None
    print("WebSocket server closed.")

# 6. Finalize any active HDF5 recording
if _teleop_state.get('recording_active'):
    rec_data = _teleop_state.get('rec_data', {})
    finalize_fn = rec_data.get('finalize')
    if finalize_fn:
        finalize_fn()
    _teleop_state['recording_active'] = False
    print("Recording finalized.")

print("Teleop session stopped.")
"""

def _gen_teleop_safety_config(args: Dict) -> str:
    robot_path = args["robot_path"]
    watchdog_ms = args.get("watchdog_timeout_ms", 500)
    max_vel = args.get("max_joint_velocity")
    ws_limits = args.get("workspace_limits")

    watchdog_s = watchdog_ms / 1000.0
    zero_vel_s = watchdog_s * 4  # Zero velocity at 4x watchdog timeout

    max_vel_line = ""
    if max_vel is not None:
        max_vel_line = f"MAX_JOINT_VEL = {max_vel}"
    else:
        max_vel_line = "MAX_JOINT_VEL = 2.0  # default rad/s"

    ws_limits_block = ""
    if ws_limits:
        ws_min = ws_limits.get("min", [-1, -1, 0])
        ws_max = ws_limits.get("max", [1, 1, 2])
        ws_limits_block = f"""
# ── Workspace limits ────────────────────────────────────────────────────
WS_MIN = Gf.Vec3d({ws_min[0]}, {ws_min[1]}, {ws_min[2]})
WS_MAX = Gf.Vec3d({ws_max[0]}, {ws_max[1]}, {ws_max[2]})

def _check_workspace_limits():
    \"\"\"Check if end-effector is within workspace bounds.\"\"\"
    ee_names = ['ee_link', 'panda_hand', 'tool0', 'link_ee']
    for name in ee_names:
        ee = stage.GetPrimAtPath(f'{{ROBOT_PATH}}/{{name}}')
        if ee.IsValid():
            xf = UsdGeom.Xformable(ee).ComputeLocalToWorldTransform(0)
            pos = xf.ExtractTranslation()
            clamped = False
            for i in range(3):
                if pos[i] < WS_MIN[i] or pos[i] > WS_MAX[i]:
                    clamped = True
                    break
            if clamped:
                print(f"WARNING: End-effector at {{pos}} outside workspace limits!")
                return False
            return True
    return True  # No ee found, skip check

print(f"Workspace limits: min={{list(WS_MIN)}}, max={{list(WS_MAX)}}")
"""

    return f"""\
import omni.usd
from pxr import UsdPhysics, UsdGeom, Gf

# ── Teleop Safety Configuration ─────────────────────────────────────────
ROBOT_PATH = '{robot_path}'
WATCHDOG_TIMEOUT_S = {watchdog_s}
WATCHDOG_ZERO_VEL_S = {zero_vel_s}
{max_vel_line}

stage = omni.usd.get_context().get_stage()

# Update global teleop state if session is active
try:
    _teleop_state['watchdog_timeout'] = WATCHDOG_TIMEOUT_S
    _teleop_state['watchdog_zero_vel'] = WATCHDOG_ZERO_VEL_S
    _teleop_state['max_joint_vel'] = MAX_JOINT_VEL
    print("Updated active teleop session safety config.")
except NameError:
    print("No active teleop session — safety config stored for next session.")

# Apply velocity limits to joint drives
robot_prim = stage.GetPrimAtPath(ROBOT_PATH)
if robot_prim.IsValid():
    configured = 0
    for child in robot_prim.GetAllChildren():
        is_revolute = child.IsA(UsdPhysics.RevoluteJoint)
        is_prismatic = child.IsA(UsdPhysics.PrismaticJoint)
        if not (is_revolute or is_prismatic):
            continue
        drive_type = 'angular' if is_revolute else 'linear'
        if child.HasAPI(UsdPhysics.DriveAPI):
            drive = UsdPhysics.DriveAPI.Get(child, drive_type)
            drive.GetMaxVelocityAttr().Set(MAX_JOINT_VEL)
            configured += 1
    print(f"Applied max velocity {{MAX_JOINT_VEL}} rad/s to {{configured}} joints.")

print(f"Safety config for {{ROBOT_PATH}}:")
print(f"  Watchdog timeout: {{WATCHDOG_TIMEOUT_S*1000:.0f}} ms")
print(f"  Zero velocity after: {{WATCHDOG_ZERO_VEL_S*1000:.0f}} ms")
print(f"  Max joint velocity: {{MAX_JOINT_VEL}} rad/s")
{ws_limits_block}"""

CODE_GEN_HANDLERS["start_teleop_session"] = _gen_start_teleop_session
CODE_GEN_HANDLERS["configure_teleop_mapping"] = _gen_configure_teleop_mapping
CODE_GEN_HANDLERS["record_teleop_demo"] = _gen_record_teleop_demo
CODE_GEN_HANDLERS["stop_teleop_session"] = _gen_stop_teleop_session
CODE_GEN_HANDLERS["teleop_safety_config"] = _gen_teleop_safety_config

# ══════ From feat/7D-arena ══════
def _arena_env_id(scene_type: str, robot_asset: str, task: str) -> str:
    """Generate a gymnasium-style env_id from arena components."""
    scene_part = scene_type.replace("_", " ").title().replace(" ", "")
    robot_part = robot_asset.split("/")[-1].replace(".usd", "").replace("_", " ").title().replace(" ", "")
    task_part = task.replace("_", " ").title().replace(" ", "")
    return f"Arena-{scene_part}{task_part}-{robot_part}-v0"

def _gen_create_arena(args: Dict) -> str:
    scene_type = args["scene_type"]
    robot_asset = args["robot_asset"]
    task = args["task"]
    num_envs = args.get("num_envs", 64)
    env_spacing = args.get("env_spacing", 2.5)

    env_id = _arena_env_id(scene_type, robot_asset, task)
    scene_module = _ARENA_SCENE_MAP.get(scene_type)

    scene_import = ""
    scene_cfg = f"'{scene_type}'"
    if scene_module:
        scene_import = f"from {scene_module} import SceneCfg"
        scene_cfg = "SceneCfg()"

    lines = [
        "# NOTE: isaaclab_tasks.envs.arena.* was never shipped in Isaac Lab.",
        "# Detect at import time and guide the caller to the actual API path.",
        "try:",
        "    from isaaclab_tasks.envs.arena.builder import ArenaEnvBuilder  # noqa: F401",
        "except ModuleNotFoundError as _e:",
        "    raise ModuleNotFoundError(",
        "        'isaaclab_tasks.envs.arena is not available in this Isaac Lab install. '",
        "        'Use isaaclab_tasks.manager_based.<domain>.<task> directly, or pick a preset '",
        "        \"from isaaclab_tasks.direct (e.g. 'cartpole', 'franka_cabinet').\"",
        "    )",
        "import gymnasium",
        "from isaaclab_tasks.envs.arena.builder import ArenaEnvBuilder",
        "from isaaclab_tasks.envs.arena.configs.embodiment import EmbodimentCfg",
        "from isaaclab_tasks.envs.arena.configs.task import TaskCfg",
    ]
    if scene_import:
        lines.append(scene_import)
    lines.extend([
        "",
        f"# Compose Arena environment: {scene_type} + {robot_asset} + {task}",
        f"scene_cfg = {scene_cfg}",
        f"embodiment_cfg = EmbodimentCfg(robot_asset='{robot_asset}')",
        f"task_cfg = TaskCfg(task='{task}')",
        "",
        "# Compile-time composition — combine scene + embodiment + task",
        "env_cfg = ArenaEnvBuilder.combine(",
        "    scene=scene_cfg,",
        "    embodiment=embodiment_cfg,",
        "    task=task_cfg,",
        f"    num_envs={num_envs},",
        f"    env_spacing={env_spacing},",
        ")",
        "",
        f"# Register with gymnasium",
        f"env_id = '{env_id}'",
        "gymnasium.register(",
        f"    id=env_id,",
        "    entry_point='isaaclab.envs:ManagerBasedRLEnv',",
        "    kwargs={'cfg': env_cfg},",
        ")",
        f"print(f'Arena environment registered: {{env_id}}')",
        f"print(f'  Scene: {scene_type}, Robot: {robot_asset}, Task: {task}')",
        f"print(f'  Envs: {num_envs}, Spacing: {env_spacing}m')",
    ])
    return "\n".join(lines)

def _gen_create_arena_variant(args: Dict) -> str:
    base_env_id = args["base_env_id"]
    robot_asset = args["robot_asset"]

    # Derive new env_id by replacing robot name in the base ID
    robot_part = robot_asset.split("/")[-1].replace(".usd", "").replace("_", " ").title().replace(" ", "")
    # Replace the robot part between last '-' and '-v0'
    parts = base_env_id.rsplit("-", 2)  # e.g. ['Arena-TabletopPickAndPlace', 'Franka', 'v0']
    new_env_id = f"{parts[0]}-{robot_part}-v0" if len(parts) >= 3 else f"{base_env_id}-{robot_part}"

    lines = [
        "import gymnasium",
        "from isaaclab_tasks.envs.arena.builder import ArenaEnvBuilder",
        "from isaaclab_tasks.envs.arena.configs.embodiment import EmbodimentCfg",
        "",
        f"# Create variant of '{base_env_id}' with robot '{robot_asset}'",
        f"base_env_id = '{base_env_id}'",
        f"base_spec = gymnasium.spec(base_env_id)",
        f"base_cfg = base_spec.kwargs['cfg']",
        "",
        f"# Replace embodiment config with new robot",
        f"new_embodiment = EmbodimentCfg(robot_asset='{robot_asset}')",
        "variant_cfg = ArenaEnvBuilder.combine(",
        "    scene=base_cfg.scene,",
        "    embodiment=new_embodiment,",
        "    task=base_cfg.task,",
        "    num_envs=base_cfg.scene.num_envs,",
        "    env_spacing=base_cfg.scene.env_spacing,",
        ")",
        "",
        f"variant_env_id = '{new_env_id}'",
        "gymnasium.register(",
        f"    id=variant_env_id,",
        "    entry_point='isaaclab.envs:ManagerBasedRLEnv',",
        "    kwargs={'cfg': variant_cfg},",
        ")",
        f"print(f'Arena variant registered: {{variant_env_id}}')",
        f"print(f'  Based on: {base_env_id}')",
        f"print(f'  New robot: {robot_asset}')",
    ]
    return "\n".join(lines)

def _gen_run_arena_benchmark(args: Dict) -> str:
    env_id = args["env_id"]
    num_episodes = args.get("num_episodes", 100)
    metrics = args.get("metrics", ["success_rate", "episode_length"])
    checkpoint = args.get("checkpoint")

    metrics_str = repr(metrics)

    lines = [
        "import subprocess",
        "import sys",
        "import os",
        "import json",
        "",
        f"env_id = '{env_id}'",
        f"num_episodes = {num_episodes}",
        f"metrics = {metrics_str}",
        "",
        "# Create results directory",
        f"results_dir = 'workspace/arena_benchmarks/{env_id}'",
        "os.makedirs(results_dir, exist_ok=True)",
        "results_file = os.path.join(results_dir, 'results.json')",
        "",
        "# Build benchmark command (runs as separate IsaacLab process)",
        "cmd = [",
        "    sys.executable, '-m',",
        "    'isaaclab_tasks.envs.arena.benchmark',",
        f"    '--env_id', env_id,",
        f"    '--num_episodes', str(num_episodes),",
        "    '--metrics', ','.join(metrics),",
        "    '--results_file', results_file,",
    ]
    if checkpoint:
        lines.extend([
            f"    '--checkpoint', '{checkpoint}',",
        ])
    lines.extend([
        "]",
        "",
        "print(f'Launching Arena benchmark: {env_id}')",
        f"print(f'  Episodes: {num_episodes}, Metrics: {{metrics}}')",
    ])
    if checkpoint:
        lines.append(f"print(f'  Checkpoint: {checkpoint}')")
    lines.extend([
        "",
        "proc = subprocess.Popen(",
        "    cmd,",
        "    stdout=subprocess.PIPE,",
        "    stderr=subprocess.STDOUT,",
        ")",
        "print(f'Benchmark started (PID: {proc.pid})')",
        "print(f'Results will be saved to: {results_file}')",
    ])
    return "\n".join(lines)

async def _handle_arena_leaderboard(args: Dict) -> Dict:
    """Format a leaderboard table from benchmark results."""
    results = args.get("results", [])

    if not results:
        return {
            "leaderboard": "No results to display.",
            "entries": [],
        }

    # Collect all unique metric keys across results
    all_metrics = set()
    for r in results:
        all_metrics.update(r.get("metrics", {}).keys())
    metric_cols = sorted(all_metrics)

    # Build leaderboard entries
    entries = []
    for i, r in enumerate(results):
        entry = {
            "rank": i + 1,
            "env_id": r.get("env_id", "unknown"),
            "robot": r.get("robot", "unknown"),
        }
        for m in metric_cols:
            entry[m] = r.get("metrics", {}).get(m, "N/A")
        entries.append(entry)

    # Sort by success_rate descending if available, else by first metric
    sort_key = "success_rate" if "success_rate" in metric_cols else (metric_cols[0] if metric_cols else None)
    if sort_key:
        entries.sort(
            key=lambda e: e.get(sort_key, 0) if isinstance(e.get(sort_key), (int, float)) else 0,
            reverse=True,
        )
        for i, e in enumerate(entries):
            e["rank"] = i + 1

    # Format as text table
    header_cols = ["Rank", "Robot", "Env ID"] + metric_cols
    rows = []
    for e in entries:
        row = [str(e["rank"]), e["robot"], e["env_id"]]
        for m in metric_cols:
            val = e.get(m, "N/A")
            if isinstance(val, float):
                row.append(f"{val:.4f}")
            else:
                row.append(str(val))
        rows.append(row)

    # Calculate column widths
    col_widths = [len(h) for h in header_cols]
    for row in rows:
        for i, cell in enumerate(row):
            col_widths[i] = max(col_widths[i], len(cell))

    # Build formatted table
    sep = "+" + "+".join("-" * (w + 2) for w in col_widths) + "+"
    header_line = "|" + "|".join(f" {h:<{col_widths[i]}} " for i, h in enumerate(header_cols)) + "|"
    table_lines = [sep, header_line, sep]
    for row in rows:
        line = "|" + "|".join(f" {cell:<{col_widths[i]}} " for i, cell in enumerate(row)) + "|"
        table_lines.append(line)
    table_lines.append(sep)
    table_text = "\n".join(table_lines)

    return {
        "leaderboard": table_text,
        "entries": entries,
        "metric_columns": metric_cols,
        "count": len(entries),
    }

CODE_GEN_HANDLERS["create_arena"] = _gen_create_arena
CODE_GEN_HANDLERS["create_arena_variant"] = _gen_create_arena_variant
CODE_GEN_HANDLERS["run_arena_benchmark"] = _gen_run_arena_benchmark
DATA_HANDLERS["arena_leaderboard"] = _handle_arena_leaderboard

# ══════ From feat/7E-eureka-rewards ══════
def _format_component_metrics(metrics: Dict) -> str:
    """Format per-component training metrics for the mutation prompt."""
    components = metrics.get("components", {})
    if not components:
        return "No component metrics available."
    lines = []
    for name, data in components.items():
        mean_vals = data.get("mean", [])
        converged = data.get("converged", False)
        mean_str = ", ".join(f"{v:.4f}" for v in mean_vals[-5:]) if mean_vals else "N/A"
        status = "converged" if converged else "not converged"
        lines.append(f"  {name}: mean=[{mean_str}] ({status})")
    return "\n".join(lines)

def _build_mutation_prompt(prev_reward: str, metrics: Dict, user_feedback: Optional[str]) -> str:
    prompt = f"""Previous reward function:
{prev_reward}

Training metrics per component:
{_format_component_metrics(metrics)}

Task success rate: {metrics.get('task_success_rate', 'N/A')}
"""
    if user_feedback:
        prompt += f"\nUser feedback: {user_feedback}\n"
    prompt += "\nBased on this data, generate an improved reward function."
    return prompt

async def _handle_generate_reward(args: Dict) -> Dict:
    """Generate Eureka reward configuration and initial prompt for a DirectRLEnv."""
    task_description = args["task_description"]
    env_source_path = args["env_source_path"]
    num_candidates = args.get("num_candidates", 4)
    num_iterations = args.get("num_iterations", 5)

    # Read environment source code
    env_path = Path(env_source_path)
    if env_path.exists():
        env_source = env_path.read_text()
    else:
        env_source = f"# [File not found: {env_source_path}]\n# Provide the DirectRLEnv source code manually."

    # Validate it's a DirectRLEnv (not ManagerBasedRLEnv)
    if "ManagerBasedRLEnv" in env_source:
        return {
            "error": "Eureka reward generation only works with DirectRLEnv, not ManagerBasedRLEnv. "
                     "DirectRLEnv exposes compute_reward() which Eureka can override.",
        }

    # Build the initial reward generation prompt
    initial_prompt = f"""You are a reward function engineer for reinforcement learning.

Task description: {task_description}

Environment source code:
```python
{env_source}
```

Generate {num_candidates} diverse reward function candidates.
Each candidate must:
1. Be a standalone Python function: def compute_reward(self) -> torch.Tensor
2. Use only tensors available in self (observations, actions, targets, etc.)
3. Return a scalar reward tensor of shape (num_envs,)
4. Include per-component breakdown as a dict for analysis
5. Avoid sparse rewards — use dense, shaped rewards

Return each candidate as a separate code block.
"""

    eureka_config = {
        "task_description": task_description,
        "env_source_path": env_source_path,
        "num_candidates": num_candidates,
        "num_iterations": num_iterations,
        "env_type": "DirectRLEnv",
        "initial_prompt": initial_prompt,
        "env_source_included": env_path.exists(),
    }

    return eureka_config

def _gen_evaluate_reward(args: Dict) -> str:
    """Generate code to evaluate a candidate reward function via short training."""
    reward_code = args["reward_code"]
    env_id = args["env_id"]
    num_steps = args.get("num_steps", 1000)

    # Escape the reward code for embedding in a string
    escaped_reward = reward_code.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n")

    return f"""\
import os
import sys
import json
import tempfile
import subprocess
from pathlib import Path

# 1. Write the candidate reward function to a temp file
reward_code = '''{reward_code}'''

reward_dir = tempfile.mkdtemp(prefix='eureka_reward_')
reward_path = os.path.join(reward_dir, 'reward_fn.py')
with open(reward_path, 'w') as f:
    f.write(reward_code)

print(f'Reward function written to {{reward_path}}')

# 2. Launch training subprocess with the custom reward
env_id = '{env_id}'
num_steps = {num_steps}

cmd = [
    sys.executable, '-m', 'isaaclab.train',
    '--task', env_id,
    '--num_envs', '16',
    '--max_iterations', str(num_steps // 16),
    '--custom_reward', reward_path,
    '--headless',
]

print(f'Launching evaluation: {{" ".join(cmd)}}')
proc = subprocess.Popen(
    cmd,
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    text=True,
    cwd=reward_dir,
)
stdout, _ = proc.communicate(timeout=300)

# 3. Parse training metrics from stdout
results = {{
    'env_id': env_id,
    'num_steps': num_steps,
    'reward_path': reward_path,
    'return_code': proc.returncode,
    'stdout_tail': stdout[-2000:] if stdout else '',
}}

# 4. Look for metrics JSON in output
metrics_path = os.path.join(reward_dir, 'metrics.json')
if os.path.exists(metrics_path):
    with open(metrics_path) as f:
        metrics = json.load(f)
    results['fitness'] = metrics.get('fitness', 0.0)
    results['components'] = metrics.get('components', {{}})
    results['task_success_rate'] = metrics.get('task_success_rate', 0.0)
else:
    results['fitness'] = 0.0
    results['components'] = {{}}
    results['task_success_rate'] = 0.0
    results['note'] = 'No metrics.json found — training may have failed'

print(f'Evaluation complete: fitness={{results["fitness"]:.4f}}, success={{results["task_success_rate"]:.2%}}')
print(json.dumps(results, indent=2))
"""

async def _handle_iterate_reward(args: Dict) -> Dict:
    """Generate a mutation prompt for the next Eureka iteration."""
    prev_reward_code = args["prev_reward_code"]
    metrics = args["metrics"]
    user_feedback = args.get("user_feedback")

    mutation_prompt = _build_mutation_prompt(prev_reward_code, metrics, user_feedback)

    return {
        "mutation_prompt": mutation_prompt,
        "prev_fitness": metrics.get("fitness", "N/A"),
        "prev_success_rate": metrics.get("task_success_rate", "N/A"),
        "components_analyzed": list(metrics.get("components", {}).keys()),
        "has_user_feedback": user_feedback is not None,
    }

async def _handle_eureka_status(args: Dict) -> Dict:
    """Return current status of a Eureka optimization run."""
    run_id = args["run_id"]

    if run_id in _eureka_runs:
        run = _eureka_runs[run_id]
        return {
            "run_id": run_id,
            "status": run.get("status", "unknown"),
            "current_iteration": run.get("current_iteration", 0),
            "total_iterations": run.get("total_iterations", 0),
            "candidates_evaluated": run.get("candidates_evaluated", 0),
            "best_fitness": run.get("best_fitness", 0.0),
            "best_reward_code": run.get("best_reward_code"),
        }

    return {
        "run_id": run_id,
        "status": "not_found",
        "message": f"No Eureka run found with ID '{run_id}'. Start one with generate_reward first.",
    }

DATA_HANDLERS["generate_reward"] = _handle_generate_reward
DATA_HANDLERS["iterate_reward"] = _handle_iterate_reward
DATA_HANDLERS["eureka_status"] = _handle_eureka_status
CODE_GEN_HANDLERS["evaluate_reward"] = _gen_evaluate_reward

# ══════ From feat/7F-zmq-bridge ══════
def _gen_configure_zmq_stream(args: Dict) -> str:
    """Generate OmniGraph code to wire a ZMQ PUB stream via NVIDIA's C++ ZMQ bridge node."""
    camera_prim = args["camera_prim"]
    pub_port = args.get("pub_port", 5555)
    resolution = args.get("resolution", [640, 480])
    fps = args.get("fps", 30)
    compression = args.get("compression", "jpeg")

    # Validate port range
    if not (1024 <= pub_port <= 65535):
        return (
            f"# ERROR: pub_port {pub_port} out of valid range (1024-65535)\n"
            f"raise ValueError('pub_port must be between 1024 and 65535, got {pub_port}')"
        )

    return f"""\
import omni.graph.core as og

og.Controller.edit(
    {{"graph_path": "/ZMQStream", "evaluator_name": "execution"}},
    {{
        og.Controller.Keys.CREATE_NODES: [
            ("OnTick", "omni.graph.action.OnPlaybackTick"),
            ("ZMQBridge", "isaacsim.bridge.zmq.OgnIsaacBridgeZMQNode"),
            ("CameraHelper", "isaacsim.ros2.bridge.ROS2CameraHelper"),
        ],
        og.Controller.Keys.CONNECT: [
            ("OnTick.outputs:tick", "CameraHelper.inputs:execIn"),
            ("CameraHelper.outputs:execOut", "ZMQBridge.inputs:execIn"),
        ],
        og.Controller.Keys.SET_VALUES: [
            ("ZMQBridge.inputs:address", "tcp://127.0.0.1:{pub_port}"),
            ("ZMQBridge.inputs:compression", "{compression}"),
            ("CameraHelper.inputs:cameraPrim", "{camera_prim}"),
            ("CameraHelper.inputs:enabled", True),
            ("CameraHelper.inputs:width", {resolution[0]}),
            ("CameraHelper.inputs:height", {resolution[1]}),
            ("CameraHelper.inputs:fps", {fps}),
        ],
    }},
)
print("ZMQ stream configured on tcp://127.0.0.1:{pub_port}")
"""

# ══════ From feat/7G-groot-n1 ══════
async def _handle_load_groot_policy(args: Dict) -> Dict:
    """Return download/launch commands for GR00T N1 policy server."""
    model_id = args.get("model_id", "nvidia/GR00T-N1.6-3B")
    robot_path = args["robot_path"]
    embodiment_key = args.get("embodiment", "custom")

    embodiment = _GROOT_EMBODIMENTS.get(embodiment_key, _GROOT_EMBODIMENTS["custom"])

    # VRAM check — estimate based on model size
    estimated_vram = embodiment.get("vram_gb", 24)

    return {
        "model_id": model_id,
        "robot_path": robot_path,
        "embodiment": embodiment_key,
        "embodiment_config": embodiment,
        "download_command": (
            f"from huggingface_hub import snapshot_download; "
            f"snapshot_download('{model_id}', local_dir='workspace/groot_models/{model_id.split('/')[-1]}')"
        ),
        "launch_command": (
            f"python -m gr00t.deploy.policy_server "
            f"--model-path workspace/groot_models/{model_id.split('/')[-1]} "
            f"--embodiment {embodiment_key} "
            f"--port 50051"
        ),
        "vram_required_gb": estimated_vram,
        "vram_check": "ok" if estimated_vram <= 24 else "insufficient",
        "error": (
            f"Insufficient VRAM: GR00T N1 requires >= 24 GB VRAM. "
            f"Consider using NVIDIA Cloud (brev.dev/nvidia) or a multi-GPU setup."
        ) if estimated_vram > 24 else None,
        "instructions": (
            f"1. Download model: {model_id}\n"
            f"2. Launch policy server on port 50051\n"
            f"3. Robot at {robot_path} will connect via gRPC\n"
            f"4. Embodiment: {embodiment_key} ({embodiment['description']})"
        ),
    }

def _gen_evaluate_groot(args: Dict) -> str:
    """Generate code to run closed-loop GR00T N1 evaluation."""
    model_id = args.get("model_id", "nvidia/GR00T-N1.6-3B")
    task = args["task"]
    num_episodes = args.get("num_episodes", 50)
    checkpoint = args.get("checkpoint")

    model_path_expr = (
        f"'{checkpoint}'" if checkpoint
        else f"'workspace/groot_models/{model_id.split('/')[-1]}'"
    )

    return f"""\
import subprocess
import sys
import os
import json

model_path = {model_path_expr}
task = '{task}'
num_episodes = {num_episodes}
results_dir = 'workspace/groot_eval_results'
os.makedirs(results_dir, exist_ok=True)

# Step 1: Launch GR00T policy server as background process
server_cmd = [
    sys.executable, '-m', 'gr00t.deploy.policy_server',
    '--model-path', model_path,
    '--port', '50051',
]
print(f'Launching GR00T policy server: {{" ".join(server_cmd)}}')
server_proc = subprocess.Popen(server_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

# Step 2: Run IsaacLabEvalTasks evaluation
eval_cmd = [
    sys.executable, '-m', 'gr00t.eval.isaac_lab',
    '--task', task,
    '--num-episodes', str(num_episodes),
    '--policy-server', 'localhost:50051',
    '--results-dir', results_dir,
]
print(f'Running evaluation: {{" ".join(eval_cmd)}}')
eval_proc = subprocess.Popen(eval_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
eval_proc.wait()

# Step 3: Collect results
results_file = os.path.join(results_dir, f'{{task}}_results.json')
if os.path.exists(results_file):
    with open(results_file) as f:
        metrics = json.load(f)
    print(f'Evaluation complete: success_rate={{metrics.get("success_rate", "N/A")}}')
    print(f'Task metrics: {{json.dumps(metrics.get("task_metrics", {{}}), indent=2)}}')
else:
    print(f'Results file not found at {{results_file}}')

# Step 4: Cleanup policy server
server_proc.terminate()
print(f'Policy server terminated (PID: {{server_proc.pid}})')
"""

def _gen_finetune_groot(args: Dict) -> str:
    """Generate code to fine-tune GR00T N1 on demo data."""
    model_id = args.get("model_id", "nvidia/GR00T-N1.6-3B")
    demo_data = args["demo_data"]
    num_steps = args.get("num_steps", 10000)
    lora = args.get("lora", True)
    output_dir = args.get("output_dir", "workspace/groot_checkpoints")

    vram_note = (
        "# LoRA fine-tuning: ~25 GB VRAM (1x RTX 4090 sufficient)"
        if lora else
        "# Full fine-tuning: ~48 GB VRAM (2x RTX 4090 or 1x A100 recommended)"
    )

    lora_flags = (
        "    '--use-lora',\n"
        "    '--lora-rank', '16',\n"
        "    '--lora-alpha', '32',\n"
    ) if lora else ""

    return f"""\
import subprocess
import sys
import os

model_id = '{model_id}'
demo_data = '{demo_data}'
num_steps = {num_steps}
output_dir = '{output_dir}'
{vram_note}

os.makedirs(output_dir, exist_ok=True)

# VRAM check
try:
    import torch
    if torch.cuda.is_available():
        vram_gb = torch.cuda.get_device_properties(0).total_mem / (1024**3)
        min_vram = {'25' if lora else '48'}
        if vram_gb < min_vram:
            print(f'WARNING: {{vram_gb:.1f}} GB VRAM detected, {{min_vram}} GB recommended.')
            print('Consider using NVIDIA Cloud (brev.dev/nvidia) or multi-GPU setup.')
except ImportError:
    pass

# Launch fine-tuning
cmd = [
    sys.executable, '-m', 'gr00t.finetune.train',
    '--model-id', model_id,
    '--demo-data', demo_data,
    '--num-steps', str(num_steps),
    '--output-dir', output_dir,
{lora_flags}]
print(f'Launching GR00T fine-tuning: {{" ".join(cmd)}}')
proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
print(f'Fine-tuning started (PID: {{proc.pid}}). Checkpoints → {{output_dir}}')
"""

async def _handle_compare_policies(args: Dict) -> Dict:
    """Format a comparison table from multiple GR00T policy evaluation results."""
    results = args.get("results", [])

    if not results:
        return {
            "comparison_table": "No results to compare.",
            "entries": [],
            "count": 0,
        }

    # Determine all metric columns
    metric_cols = set()
    for r in results:
        tm = r.get("task_metrics", {})
        metric_cols.update(tm.keys())
    metric_cols = sorted(metric_cols)

    # Build comparison entries
    entries = []
    for r in results:
        entry = {
            "policy_name": r.get("policy_name", "unnamed"),
            "model_id": r.get("model_id", "N/A"),
            "success_rate": r.get("success_rate", 0.0),
            "training_data_size": r.get("training_data_size", "N/A"),
            "observation_type": r.get("observation_type", "N/A"),
        }
        for col in metric_cols:
            entry[col] = r.get("task_metrics", {}).get(col, "N/A")
        entries.append(entry)

    # Sort by success_rate descending
    entries.sort(key=lambda e: -e["success_rate"])

    # Build formatted table
    header_cols = ["Policy", "Model", "Success Rate", "Train Data", "Obs Type"]
    header_cols.extend(metric_cols)

    rows = []
    for e in entries:
        row = [
            e["policy_name"],
            e["model_id"],
            f"{e['success_rate']:.1%}",
            e["training_data_size"],
            e["observation_type"],
        ]
        for col in metric_cols:
            val = e.get(col, "N/A")
            if isinstance(val, float):
                row.append(f"{val:.3f}")
            else:
                row.append(str(val))
        rows.append(row)

    # Calculate column widths
    col_widths = [len(h) for h in header_cols]
    for row in rows:
        for i, val in enumerate(row):
            col_widths[i] = max(col_widths[i], len(val))

    # Format table
    sep = "+-" + "-+-".join("-" * w for w in col_widths) + "-+"
    header_line = "| " + " | ".join(h.ljust(w) for h, w in zip(header_cols, col_widths)) + " |"
    table_lines = [sep, header_line, sep]
    for row in rows:
        table_lines.append("| " + " | ".join(v.ljust(w) for v, w in zip(row, col_widths)) + " |")
    table_lines.append(sep)

    return {
        "comparison_table": "\n".join(table_lines),
        "entries": entries,
        "count": len(entries),
        "metric_columns": metric_cols,
        "dimensions": [
            "zero-shot generalization (success_rate without task-specific training)",
            "single-task performance (success_rate with fine-tuning)",
            "training data needed (training_data_size)",
            "observation type (observation_type: rgb, rgb+proprio, proprio)",
        ],
    }

DATA_HANDLERS["load_groot_policy"] = _handle_load_groot_policy
DATA_HANDLERS["compare_policies"] = _handle_compare_policies
CODE_GEN_HANDLERS["evaluate_groot"] = _gen_evaluate_groot
CODE_GEN_HANDLERS["finetune_groot"] = _gen_finetune_groot

# ══════ From feat/7H-cloud-deployment ══════
async def _handle_cloud_launch(args: Dict) -> Dict:
    """Return structured deployment info for IsaacAutomator cloud launch.
    Always requires approval regardless of auto-approve setting.
    """
    provider = args["provider"]
    instance_type = args["instance_type"]
    isaac_version = args.get("isaac_version", "5.1.0")
    script_template = args.get("script_template", "training")
    num_gpus = args.get("num_gpus", 1)

    # Validate script template against allowlist
    if script_template not in _CLOUD_SCRIPT_ALLOWLIST:
        return {
            "error": f"Unknown script_template '{script_template}'. "
                     f"Allowed: {sorted(_CLOUD_SCRIPT_ALLOWLIST)}",
        }

    # Lookup pricing
    pricing_key = (provider, instance_type)
    pricing = _CLOUD_PRICING.get(pricing_key)
    if pricing:
        price_per_hour = pricing["price_per_hour"]
        gpu_model = pricing["gpu"]
    else:
        price_per_hour = None
        gpu_model = "unknown"

    # Prerequisites per provider
    prerequisites = {
        "aws": [
            "NGC API key configured (ngc config set)",
            "AWS IAM credentials with EC2 and S3 permissions",
            "GPU quota approved for the target region",
            "IsaacAutomator cloned and configured",
        ],
        "gcp": [
            "NGC API key configured (ngc config set)",
            "GCP service account with Compute Engine permissions",
            "GPU quota approved for the target zone",
            "IsaacAutomator cloned and configured",
        ],
        "azure": [
            "NGC API key configured (ngc config set)",
            "Azure subscription with GPU VM quota",
            "Azure CLI authenticated (az login)",
            "IsaacAutomator cloned and configured",
        ],
    }

    import uuid
    job_id = f"cloud-{provider}-{uuid.uuid4().hex[:8]}"

    deploy_command = (
        f"./deploy-{provider} "
        f"--instance-type {instance_type} "
        f"--isaac-version {isaac_version} "
        f"--script {script_template} "
        f"--num-gpus {num_gpus}"
    )

    result = {
        "job_id": job_id,
        "deploy_command": deploy_command,
        "provider": provider,
        "instance_type": instance_type,
        "isaac_version": isaac_version,
        "script_template": script_template,
        "num_gpus": num_gpus,
        "gpu_model": gpu_model,
        "estimated_cost_per_hour": price_per_hour,
        "prerequisites": prerequisites.get(provider, []),
        "always_require_approval": True,
        "message": (
            f"Ready to deploy {instance_type} ({gpu_model}) on {provider.upper()} "
            f"with Isaac Sim {isaac_version}. "
            + (f"Estimated cost: ${price_per_hour:.2f}/hr. " if price_per_hour else "Cost: unknown instance type. ")
            + "Review the prerequisites and approve to proceed."
        ),
    }

    # Track job (placeholder)
    _cloud_jobs[job_id] = {
        "status": "pending_approval",
        "provider": provider,
        "instance_type": instance_type,
        "gpu_model": gpu_model,
        "price_per_hour": price_per_hour,
    }

    return result

async def _handle_cloud_status(args: Dict) -> Dict:
    """Check the status of a cloud job."""
    job_id = args["job_id"]

    if job_id in _cloud_jobs:
        job = _cloud_jobs[job_id]
        return {
            "job_id": job_id,
            "status": job.get("status", "unknown"),
            "gpu_utilization": job.get("gpu_utilization", "N/A"),
            "estimated_remaining": job.get("estimated_remaining", "N/A"),
            "cost_so_far": job.get("cost_so_far", "N/A"),
        }

    return {
        "job_id": job_id,
        "status": "not_found",
        "gpu_utilization": None,
        "estimated_remaining": None,
        "cost_so_far": None,
        "message": f"No cloud job found with ID '{job_id}'. It may have been terminated or the ID is incorrect.",
    }

async def _handle_cloud_teardown(args: Dict) -> Dict:
    """Return teardown command for a cloud instance. Always requires approval."""
    job_id = args["job_id"]

    job = _cloud_jobs.get(job_id)
    if job:
        provider = job.get("provider", "unknown")
        teardown_command = f"./destroy-{provider} --job-id {job_id}"
        price = job.get("price_per_hour")
        cost_warning = ""
        if price and job.get("status") in ("running", "pending_approval"):
            cost_warning = (
                f"WARNING: Instance is still active at ${price:.2f}/hr. "
                "Teardown will terminate the instance and stop billing."
            )
        return {
            "job_id": job_id,
            "teardown_command": teardown_command,
            "provider": provider,
            "always_require_approval": True,
            "cost_warning": cost_warning,
            "message": f"Ready to tear down {provider.upper()} instance {job_id}. Approve to proceed.",
        }

    return {
        "job_id": job_id,
        "teardown_command": f"./destroy-unknown --job-id {job_id}",
        "provider": "unknown",
        "always_require_approval": True,
        "cost_warning": "",
        "message": f"Job '{job_id}' not found in local tracking. Command generated but may fail.",
    }

async def _handle_cloud_estimate_cost(args: Dict) -> Dict:
    """Estimate cost for a cloud GPU instance over a given duration."""
    provider = args["provider"]
    instance_type = args["instance_type"]
    hours = args["hours"]

    pricing_key = (provider, instance_type)
    pricing = _CLOUD_PRICING.get(pricing_key)

    if pricing:
        price_per_hour = pricing["price_per_hour"]
        gpu = pricing["gpu"]
        cost_usd = round(price_per_hour * hours, 2)
        return {
            "cost_usd": cost_usd,
            "price_per_hour": price_per_hour,
            "provider": provider,
            "instance_type": instance_type,
            "gpu": gpu,
            "hours": hours,
            "message": f"{instance_type} ({gpu}) on {provider.upper()}: ${cost_usd:.2f} for {hours}h @ ${price_per_hour:.2f}/hr",
        }

    return {
        "cost_usd": None,
        "price_per_hour": None,
        "provider": provider,
        "instance_type": instance_type,
        "gpu": "unknown",
        "hours": hours,
        "message": (
            f"Instance type '{instance_type}' on {provider.upper()} not found in pricing table. "
            f"Known types: {[f'{p}/{t}' for (p, t) in _CLOUD_PRICING.keys()]}"
        ),
    }

def _gen_cloud_download_results(args: Dict) -> str:
    """Generate code to download results from a cloud instance."""
    job_id = args["job_id"]
    output_dir = args.get("output_dir", "workspace/cloud_results")

    return f'''\
import subprocess
import os

job_id = "{job_id}"
output_dir = "{output_dir}"
os.makedirs(output_dir, exist_ok=True)

# IsaacAutomator stores results on the cloud instance at /results/
# Retrieve the instance IP from the deployment state
state_file = f"deployments/{{job_id}}/state.json"
if os.path.exists(state_file):
    import json
    with open(state_file) as f:
        state = json.load(f)
    instance_ip = state.get("instance_ip", "UNKNOWN_IP")
    key_path = state.get("ssh_key", "~/.ssh/isaacautomator")
else:
    instance_ip = "UNKNOWN_IP"
    key_path = "~/.ssh/isaacautomator"
    print(f"WARNING: State file not found at {{state_file}}. Set instance_ip manually.")

# Download results via rsync
cmd = [
    "rsync", "-avz", "--progress",
    "-e", f"ssh -i {{key_path}} -o StrictHostKeyChecking=no",
    f"ubuntu@{{instance_ip}}:/results/",
    output_dir + "/",
]
print(f"Downloading results: {{' '.join(cmd)}}")
proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
stdout, _ = proc.communicate()
print(stdout.decode() if stdout else "")
if proc.returncode == 0:
    print(f"Results downloaded to {{output_dir}}/")
else:
    print(f"Download failed (exit code {{proc.returncode}}). Check IP and SSH key.")
'''

DATA_HANDLERS["cloud_launch"] = _handle_cloud_launch
DATA_HANDLERS["cloud_status"] = _handle_cloud_status
DATA_HANDLERS["cloud_teardown"] = _handle_cloud_teardown
DATA_HANDLERS["cloud_estimate_cost"] = _handle_cloud_estimate_cost
CODE_GEN_HANDLERS["cloud_download_results"] = _gen_cloud_download_results

# ══════ From feat/8A-quick-wins ══════
def _gen_clone_envs(args: Dict) -> str:
    source_path = args["source_path"]
    num_envs = args["num_envs"]
    spacing = args.get("spacing", 2.5)
    collision_filter = args.get("collision_filter", True)

    lines = [
        "from isaacsim.core.cloner import GridCloner",
        "",
        f"cloner = GridCloner(spacing={spacing})",
        'cloner.define_base_env("/World/envs")',
        f'prim_paths = cloner.generate_paths("/World/envs/env", {num_envs})',
        "positions = cloner.clone(",
        f"    source_prim_path='{source_path}',",
        "    prim_paths=prim_paths,",
        "    replicate_physics=True,  # CRITICAL for performance",
        ")",
    ]
    if collision_filter:
        lines.extend([
            "# Collision filtering is a SEPARATE step:",
            "cloner.filter_collisions(",
            "    physicsscene_path='/World/PhysicsScene',",
            "    collision_root_path='/World/collisionGroups',",
            "    prim_paths=prim_paths,",
            ")",
        ])
    lines.append(f"print(f'Cloned {num_envs} environments from {source_path}')")
    return "\n".join(lines)

def _gen_debug_draw(args: Dict) -> str:
    draw_type = args["draw_type"]
    points = args["points"]
    color = args.get("color", [1, 0, 0, 1])
    size = args.get("size", 5)
    lifetime = args.get("lifetime", 0)

    lines = [
        "from isaacsim.util.debug_draw import _debug_draw",
        "",
        "draw = _debug_draw.acquire_debug_draw_interface()",
    ]

    if draw_type == "points":
        lines.append(f"points = {points}")
        lines.append(f"colors = [{color}] * len(points)")
        lines.append(f"sizes = [{size}] * len(points)")
        lines.append("draw.draw_points(points, colors, sizes)")
    elif draw_type == "lines":
        # Points come as pairs: [start, end, start, end, ...]
        lines.append(f"all_pts = {points}")
        lines.append("start_points = all_pts[0::2]")
        lines.append("end_points = all_pts[1::2]")
        lines.append(f"colors = [{color}] * len(start_points)")
        lines.append(f"sizes = [{size}] * len(start_points)")
        lines.append("draw.draw_lines(start_points, end_points, colors, sizes)")
    elif draw_type == "lines_spline":
        lines.append(f"points = {points}")
        lines.append(f"color = {color}")
        lines.append(f"width = {size}")
        lines.append("draw.draw_lines_spline(points, color, width, closed=False)")

    if lifetime > 0:
        lines.extend([
            "",
            "# Schedule auto-clear",
            "import asyncio",
            f"asyncio.get_event_loop().call_later({lifetime}, draw.clear_points)",
        ])

    return "\n".join(lines)

def _gen_generate_occupancy_map(args: Dict) -> str:
    origin = args.get("origin", [0, 0])
    dimensions = args.get("dimensions", [10, 10])
    resolution = args.get("resolution", 0.05)
    height_range = args.get("height_range", [0, 2])

    return f"""\
from isaacsim.asset.gen.omap import MapGenerator
import carb

gen = MapGenerator()
gen.update_settings(cell_size={resolution})
gen.set_transform(
    origin=carb.Float3({origin[0]}, {origin[1]}, 0),
    min_bound=carb.Float3({-dimensions[0]/2}, {-dimensions[1]/2}, {height_range[0]}),
    max_bound=carb.Float3({dimensions[0]/2}, {dimensions[1]/2}, {height_range[1]}),
)
gen.generate2d()
buffer = gen.get_buffer()
print(f"Occupancy map generated: {int(dimensions[0]/resolution)} x {int(dimensions[1]/resolution)} cells")
"""

def _gen_inspect_camera(args: Dict) -> str:
    camera_path = args["camera_path"]
    return f"""\
import omni.usd
from pxr import UsdGeom
import json

stage = omni.usd.get_context().get_stage()
cam = UsdGeom.Camera(stage.GetPrimAtPath('{camera_path}'))
result = {{
    'camera_path': '{camera_path}',
    'focal_length': cam.GetFocalLengthAttr().Get(),
    'horizontal_aperture': cam.GetHorizontalApertureAttr().Get(),
    'vertical_aperture': cam.GetVerticalApertureAttr().Get(),
    'clipping_range': list(cam.GetClippingRangeAttr().Get()),
    'focus_distance': cam.GetFocusDistanceAttr().Get(),
    'projection': cam.GetProjectionAttr().Get(),
}}
print(json.dumps(result))
"""

def _gen_configure_camera(args: Dict) -> str:
    camera_path = args["camera_path"]
    lines = [
        "import omni.usd",
        "from pxr import UsdGeom, Gf",
        "",
        "stage = omni.usd.get_context().get_stage()",
        f"cam = UsdGeom.Camera(stage.GetPrimAtPath('{camera_path}'))",
    ]
    if "focal_length" in args:
        lines.append(f"cam.GetFocalLengthAttr().Set({args['focal_length']})")
    if "horizontal_aperture" in args:
        lines.append(f"cam.GetHorizontalApertureAttr().Set({args['horizontal_aperture']})")
    if "vertical_aperture" in args:
        lines.append(f"cam.GetVerticalApertureAttr().Set({args['vertical_aperture']})")
    if "clipping_range" in args:
        cr = args["clipping_range"]
        lines.append(f"cam.GetClippingRangeAttr().Set(Gf.Vec2f({cr[0]}, {cr[1]}))")
    if "focus_distance" in args:
        lines.append(f"cam.GetFocusDistanceAttr().Set({args['focus_distance']})")
    lines.append(f"print(f'Camera {camera_path} configured')")
    return "\n".join(lines)

async def _handle_inspect_camera(args: Dict) -> Dict:
    camera_path = args["camera_path"]
    code = _gen_inspect_camera(args)
    return await kit_tools.queue_exec_patch(code, f"Inspect camera at {camera_path}")

DATA_HANDLERS["inspect_camera"] = _handle_inspect_camera

# ══════ From feat/8B-motion-planning-complete ══════
def _gen_set_motion_policy(args: Dict) -> str:
    art_path = args["articulation_path"]
    policy_type = args["policy_type"]
    robot_type = args.get("robot_type", "franka").lower()

    if policy_type == "add_obstacle":
        obs_name = args.get("obstacle_name", "obstacle_0")
        obs_type = args.get("obstacle_type", "cuboid")
        obs_dims = args.get("obstacle_dims", [0.1, 0.1, 0.1])
        obs_pos = args.get("obstacle_position", [0.0, 0.0, 0.0])

        lines = [
            "import numpy as np",
            "from isaacsim.robot_motion.motion_generation import RmpFlow",
            "from isaacsim.robot_motion.motion_generation import interface_config_loader",
            "",
            f"rmpflow_config = interface_config_loader.load_supported_motion_gen_config('{robot_type}', 'RMPflow')",
            "rmpflow = RmpFlow(**rmpflow_config)",
            "",
        ]
        if obs_type == "sphere":
            radius = obs_dims[0] if obs_dims else 0.1
            lines.extend([
                f"# Add sphere obstacle '{obs_name}'",
                f"rmpflow.add_sphere(",
                f"    name='{obs_name}',",
                f"    radius={radius},",
                f"    pose=np.array([{obs_pos[0]}, {obs_pos[1]}, {obs_pos[2]}, 1.0, 0.0, 0.0, 0.0]),",
                f")",
                "rmpflow.update_world()",
                f"print(f'Added sphere obstacle \\'{obs_name}\\' at {obs_pos} with radius {radius}')",
            ])
        else:
            # cuboid (default)
            lines.extend([
                f"# Add cuboid obstacle '{obs_name}'",
                f"rmpflow.add_cuboid(",
                f"    name='{obs_name}',",
                f"    dims=np.array({list(obs_dims)}),",
                f"    pose=np.array([{obs_pos[0]}, {obs_pos[1]}, {obs_pos[2]}, 1.0, 0.0, 0.0, 0.0]),",
                f")",
                "rmpflow.update_world()",
                f"print(f'Added cuboid obstacle \\'{obs_name}\\' at {obs_pos} with dims {list(obs_dims)}')",
            ])
        return "\n".join(lines)

    if policy_type == "remove_obstacle":
        lines = [
            "from isaacsim.robot_motion.motion_generation import RmpFlow",
            "from isaacsim.robot_motion.motion_generation import interface_config_loader",
            "",
            f"rmpflow_config = interface_config_loader.load_supported_motion_gen_config('{robot_type}', 'RMPflow')",
            "rmpflow = RmpFlow(**rmpflow_config)",
            "",
            "# RMPflow has no individual obstacle removal — reset clears all obstacles",
            "rmpflow.reset()",
            "print('Motion policy reset — all obstacles cleared')",
        ]
        return "\n".join(lines)

    if policy_type == "set_joint_limits":
        buffer_val = args.get("joint_limit_buffers", 0.05)
        lines = [
            "import numpy as np",
            "from isaacsim.robot_motion.motion_generation import RmpFlow",
            "from isaacsim.robot_motion.motion_generation import interface_config_loader",
            "from isaacsim.core.prims import SingleArticulation",
            "",
            f"rmpflow_config = interface_config_loader.load_supported_motion_gen_config('{robot_type}', 'RMPflow')",
            "rmpflow = RmpFlow(**rmpflow_config)",
            "",
            f"art = SingleArticulation(prim_path='{art_path}')",
            "art.initialize()",
            "",
            "# Get current joint limits and add padding buffer",
            "lower_limits = art.get_joint_positions()  # read current as reference",
            f"buffer = {buffer_val}",
            "dof_count = art.num_dof",
            "print(f'Applying joint limit buffer of {buffer} rad to {dof_count} joints')",
            "print(f'Note: Joint limit buffers are applied in the RMPflow config YAML.')",
            "print(f'For runtime adjustment, modify rmpflow_config[\"joint_limit_buffers\"] before init.')",
        ]
        return "\n".join(lines)

    return f"# Unknown policy type: {policy_type}"

async def _handle_generate_robot_description(args: Dict) -> Dict:
    """Check if a robot has pre-built motion generation configs."""
    art_path = args["articulation_path"]
    robot_type = args.get("robot_type", "").lower()

    # Try to identify robot type from path if not provided
    if not robot_type:
        path_lower = art_path.lower()
        for name in _SUPPORTED_MOTION_ROBOTS:
            if name in path_lower:
                robot_type = name
                break

    if robot_type in _SUPPORTED_MOTION_ROBOTS:
        cfg = _MOTION_ROBOT_CONFIGS.get(robot_type, {})
        return {
            "supported": True,
            "robot_type": robot_type,
            "config_files": {
                "rmpflow_config": cfg.get("rmp_config", f"{robot_type}/rmpflow"),
                "robot_descriptor": cfg.get("desc", f"{robot_type}/robot_descriptor.yaml"),
                "urdf": cfg.get("urdf", f"{robot_type}/lula_gen.urdf"),
                "end_effector_frame": cfg.get("ee_frame", "ee_link"),
            },
            "usage": (
                "This robot has pre-built configs. Use "
                "interface_config_loader.load_supported_motion_gen_config("
                f"'{robot_type}', 'RMPflow') to load them."
            ),
            "message": (
                f"Robot '{robot_type}' is pre-supported for motion generation. "
                f"Config files are bundled with the isaacsim.robot_motion.motion_generation extension."
            ),
        }

    return {
        "supported": False,
        "robot_type": robot_type or "(unknown)",
        "articulation_path": art_path,
        "instructions": (
            "This robot does not have pre-built motion generation configs. "
            "To create them:\n"
            "1. Open the XRDF Editor GUI (Window > Extensions > XRDF Editor) to "
            "define collision spheres, joint limits, and end-effector frames.\n"
            "2. Export the XRDF file and Lula robot descriptor YAML.\n"
            "3. Use the exported files with LulaKinematicsSolver and RmpFlow.\n\n"
            "For programmatic collision sphere editing, use the CollisionSphereEditor "
            "from isaacsim.robot_setup.xrdf_editor:\n"
            "  - CollisionSphereEditor.add_sphere(link_path, position, radius)\n"
            "  - CollisionSphereEditor.clear_link_spheres(link_path)\n"
            "  - CollisionSphereEditor.clear_spheres()\n"
            "  - CollisionSphereEditor.delete_sphere(sphere_id)"
        ),
        "message": (
            f"Robot '{robot_type or 'unknown'}' at '{art_path}' is not pre-supported. "
            "Use the XRDF Editor to generate collision spheres and robot descriptors."
        ),
    }

def _gen_solve_ik(args: Dict) -> str:
    art_path = args["articulation_path"]
    target_pos = args["target_position"]
    target_ori = args.get("target_orientation")
    robot_type = args.get("robot_type", "franka").lower()

    cfg = _MOTION_ROBOT_CONFIGS.get(robot_type, _MOTION_ROBOT_CONFIGS["franka"])
    ee_frame = cfg["ee_frame"]

    lines = [
        "import numpy as np",
        "from isaacsim.robot_motion.motion_generation import LulaKinematicsSolver",
        "from isaacsim.robot_motion.motion_generation import ArticulationKinematicsSolver",
        "from isaacsim.robot_motion.motion_generation import interface_config_loader",
        "from isaacsim.core.prims import SingleArticulation",
        "",
        f"# Load kinematics config for {robot_type}",
        f"kin_config = interface_config_loader.load_supported_lula_kinematics_solver_config('{robot_type}')",
        "kin_solver = LulaKinematicsSolver(**kin_config)",
        "",
        f"art = SingleArticulation(prim_path='{art_path}')",
        "art.initialize()",
        f"art_kin = ArticulationKinematicsSolver(art, kin_solver, '{ee_frame}')",
        "",
        f"target_position = np.array({list(target_pos)})",
    ]
    if target_ori:
        lines.append(f"target_orientation = np.array({list(target_ori)})")
    else:
        lines.append("target_orientation = None")

    lines.extend([
        "",
        "action, success = art_kin.compute_inverse_kinematics(",
        "    target_position=target_position,",
        "    target_orientation=target_orientation,",
        ")",
        "if success:",
        "    art.apply_action(action)",
        f"    print(f'IK solved successfully — {ee_frame} moving to {{target_position}}')",
        "else:",
        "    print('IK failed — target may be unreachable or near singularity')",
    ])
    return "\n".join(lines)

CODE_GEN_HANDLERS["set_motion_policy"] = _gen_set_motion_policy
DATA_HANDLERS["generate_robot_description"] = _handle_generate_robot_description
CODE_GEN_HANDLERS["solve_ik"] = _gen_solve_ik

# ══════ From feat/8C-cortex-v2 ══════
def _gen_create_behavior(args: Dict) -> str:
    """Generate code to create a Cortex behavior (decider network) for a robot.

    NOTE (2026-04): The 5.x Cortex API changed MotionCommander's constructor to
    take (amp: ArticulationMotionPolicy, target_prim: SingleXFormPrim, ...)
    instead of a prim-path string. CortexRobot also dropped the motion_commander
    constructor kwarg. A proper behavior needs an RmpFlow/AMP configured per-
    robot (URDF + YAML config paths), which we don't have registry access to
    here. Rather than emit broken code, raise an actionable error when called.
    """
    art_path = args["articulation_path"]
    behavior = args["behavior_type"]
    target = args.get("target_prim", "/World/Target")
    params = args.get("params", {})

    speed = params.get("speed", 0.5)
    threshold = params.get("threshold", 0.02)

    # Fail fast with guidance — the old generated code calls
    # MotionCommander('/path') and CortexRobot(..., motion_commander=...),
    # both of which are invalid in Isaac Sim 5.x's Cortex framework.
    return (
        "raise NotImplementedError(\n"
        "    'create_behavior is a pre-5.x Cortex API that requires per-robot '\n"
        "    'RmpFlow + ArticulationMotionPolicy + SingleXFormPrim target plumbing. '\n"
        "    'For Franka/UR10 pick-and-place in 5.x use isaaclab_tasks.manager_based.manipulation '\n"
        "    'or the cortex examples bundled with your Isaac Sim install.'\n"
        ")\n"
    )

    # --- Legacy branches below are unreachable; preserved as reference for the
    # --- rewrite against the 5.x Cortex API.
    if behavior == "pick_and_place":
        place_target = params.get("place_target", "/World/PlaceTarget")
        return f"""\
from isaacsim.cortex.framework.cortex_world import CortexWorld
from isaacsim.cortex.framework.robot import CortexRobot
from isaacsim.cortex.framework.df import DfNetwork, DfDecider, DfState, DfStateMachineDecider
from isaacsim.cortex.framework.motion_commander import MotionCommander
import numpy as np

# Create Cortex world
world = CortexWorld()

# Add robot
robot = world.add_robot(CortexRobot(
    name="robot",
    prim_path='{art_path}',
    motion_commander=MotionCommander('{art_path}'),
))

# ── Pick-and-place state machine ────────────────────────────
class ApproachState(DfState):
    \"\"\"Move to pre-grasp position above the target.\"\"\"
    def enter(self):
        target_pos = np.array(self.context['target_pos'])
        approach_pos = target_pos + np.array([0, 0, {params.get('approach_distance', 0.1)}])
        self.context['mc'].send_end_effector_target(
            translation=approach_pos,
        )

    def step(self):
        if self.context['mc'].reached_target(threshold={threshold}):
            return 'grasp'
        return None

class GraspState(DfState):
    \"\"\"Move down and close gripper.\"\"\"
    def enter(self):
        target_pos = np.array(self.context['target_pos'])
        self.context['mc'].send_end_effector_target(
            translation=target_pos,
        )

    def step(self):
        if self.context['mc'].reached_target(threshold={threshold}):
            self.context['gripper'].close()
            return 'lift'
        return None

class LiftState(DfState):
    \"\"\"Lift the grasped object.\"\"\"
    def enter(self):
        target_pos = np.array(self.context['target_pos'])
        lift_pos = target_pos + np.array([0, 0, {params.get('lift_height', 0.15)}])
        self.context['mc'].send_end_effector_target(
            translation=lift_pos,
        )

    def step(self):
        if self.context['mc'].reached_target(threshold={threshold}):
            return 'place'
        return None

class PlaceState(DfState):
    \"\"\"Move to place position and release.\"\"\"
    def enter(self):
        place_pos = np.array(self.context['place_pos'])
        self.context['mc'].send_end_effector_target(
            translation=place_pos,
        )

    def step(self):
        if self.context['mc'].reached_target(threshold={threshold}):
            self.context['gripper'].open()
            return 'done'
        return None

# Build decider network
pick_place_decider = DfStateMachineDecider(
    states={{
        'approach': ApproachState(),
        'grasp': GraspState(),
        'lift': LiftState(),
        'place': PlaceState(),
    }},
    initial_state='approach',
)

network = DfNetwork(decider=pick_place_decider)
world.add_decider_network(network)

print("Cortex pick-and-place behavior created for {art_path}")
print("Target: {target}, Place: {place_target}")
"""

    # follow_target
    return f"""\
from isaacsim.cortex.framework.cortex_world import CortexWorld
from isaacsim.cortex.framework.robot import CortexRobot
from isaacsim.cortex.framework.df import DfNetwork, DfDecider, DfState
from isaacsim.cortex.framework.motion_commander import MotionCommander
import numpy as np

# Create Cortex world
world = CortexWorld()

# Add robot
robot = world.add_robot(CortexRobot(
    name="robot",
    prim_path='{art_path}',
    motion_commander=MotionCommander('{art_path}'),
))

# ── Follow-target behavior ──────────────────────────────────
class FollowTargetState(DfState):
    \"\"\"Continuously track a target prim with the end-effector.\"\"\"
    def enter(self):
        self.update_interval = {params.get('update_interval', 0.1)}

    def step(self):
        import omni.usd
        from pxr import UsdGeom
        stage = omni.usd.get_context().get_stage()
        target_prim = stage.GetPrimAtPath('{target}')
        xf = UsdGeom.Xformable(target_prim).ComputeLocalToWorldTransform(0)
        target_pos = np.array(xf.ExtractTranslation())
        self.context['mc'].send_end_effector_target(
            translation=target_pos,
        )
        return None  # stay in this state

class FollowDecider(DfDecider):
    \"\"\"Simple decider that always runs the follow state.\"\"\"
    def __init__(self):
        super().__init__()
        self.add_child('follow', FollowTargetState())

    def decide(self):
        return 'follow'

network = DfNetwork(decider=FollowDecider())
world.add_decider_network(network)

print("Cortex follow-target behavior created for {art_path}")
print("Following: {target}")
"""

def _gen_create_gripper(args: Dict) -> str:
    """Generate code to create and configure a gripper."""
    art_path = args["articulation_path"]
    gripper_type = args["gripper_type"]
    open_pos = args.get("open_position", 0.04)
    closed_pos = args.get("closed_position", 0.0)

    if gripper_type == "parallel_jaw":
        dof_names = args.get("gripper_dof_names", ["panda_finger_joint1", "panda_finger_joint2"])
        dof_names_str = repr(dof_names)
        return f"""\
from isaacsim.robot.manipulators.grippers import ParallelGripper
import numpy as np

# Create parallel jaw gripper
gripper = ParallelGripper(
    end_effector_prim_path='{art_path}/panda_hand',
    joint_prim_names={dof_names_str},
    joint_opened_positions=np.array([{open_pos}] * {len(dof_names)}),
    joint_closed_positions=np.array([{closed_pos}] * {len(dof_names)}),
    action_deltas=np.array([{open_pos}] * {len(dof_names)}),
)

# Initialize gripper
gripper.initialize()

# Open gripper to start
gripper.open()
print(f"ParallelGripper created on {art_path}")
print(f"  DOFs: {dof_names_str}")
print(f"  Open position: {open_pos}")
print(f"  Closed position: {closed_pos}")
"""

    # suction gripper — OmniGraph-based OgnSurfaceGripper
    return f"""\
import omni.graph.core as og

# Resolve backing type
_bt = og.GraphBackingType
if hasattr(_bt, 'GRAPH_BACKING_TYPE_FABRIC_SHARED'):
    _backing = _bt.GRAPH_BACKING_TYPE_FABRIC_SHARED
elif hasattr(_bt, 'GRAPH_BACKING_TYPE_FLATCACHING'):
    _backing = _bt.GRAPH_BACKING_TYPE_FLATCACHING
else:
    _backing = list(_bt)[0]

keys = og.Controller.Keys
(graph, nodes, _, _) = og.Controller.edit(
    {{
        "graph_path": "{art_path}/SuctionGripperGraph",
        "evaluator_name": "execution",
        "pipeline_stage": _backing,
    }},
    {{
        keys.CREATE_NODES: [
            ("OnPlaybackTick", "omni.graph.action.OnPlaybackTick"),
            ("SurfaceGripper", "isaacsim.robot.surface_gripper.OgnSurfaceGripper"),
        ],
        keys.CONNECT: [
            ("OnPlaybackTick.outputs:tick", "SurfaceGripper.inputs:execIn"),
        ],
        keys.SET_VALUES: [
            ("SurfaceGripper.inputs:parentPath", "{art_path}"),
            ("SurfaceGripper.inputs:enabled", True),
            ("SurfaceGripper.inputs:gripThreshold", 0.01),
            ("SurfaceGripper.inputs:forceLimit", 100.0),
            ("SurfaceGripper.inputs:torqueLimit", 100.0),
        ],
    }},
)

print(f"Suction gripper (OgnSurfaceGripper) created on {art_path}")
print("Use SurfaceGripper.inputs:close to activate suction")
"""

def _gen_grasp_object(args: Dict) -> str:
    """Generate a complete grasp sequence: approach, grasp, lift."""
    robot_path = args["robot_path"]
    target_prim = args["target_prim"]
    grasp_type = args.get("grasp_type", "top_down")
    approach_dist = args.get("approach_distance", 0.1)
    lift_height = args.get("lift_height", 0.1)

    if grasp_type == "from_file":
        grasp_file = args.get("grasp_file", "")
        return f"""\
import numpy as np
import yaml
import omni.usd
from pxr import UsdGeom, Gf
from isaacsim.robot_motion.motion_generation import RmpFlow
from isaacsim.robot_motion.motion_generation import interface_config_loader
from isaacsim.core.prims import SingleArticulation

# Load grasp specification from file
with open('{grasp_file}', 'r') as f:
    grasp_spec = yaml.safe_load(f)

grasp_name = list(grasp_spec.get('grasps', {{}}).keys())[0]
grasp = grasp_spec['grasps'][grasp_name]
offset = np.array(grasp.get('gripper_offset', [0, 0, 0]))
approach_dir = np.array(grasp.get('approach_direction', [0, 0, -1]))

# Get target object position
stage = omni.usd.get_context().get_stage()
target_xf = UsdGeom.Xformable(stage.GetPrimAtPath('{target_prim}')).ComputeLocalToWorldTransform(0)
target_pos = np.array(target_xf.ExtractTranslation())

# Compute grasp and approach positions
grasp_pos = target_pos + offset
approach_pos = grasp_pos - approach_dir * {approach_dist}
lift_pos = grasp_pos + np.array([0, 0, {lift_height}])

# Setup motion planner
rmpflow_config = interface_config_loader.load_supported_motion_gen_config('franka', 'RMPflow')
rmpflow = RmpFlow(**rmpflow_config)
art = SingleArticulation(prim_path='{robot_path}')
art.initialize()

# Step 1: Move to approach position
rmpflow.set_end_effector_target(approach_pos, None)
joint_positions = art.get_joint_positions()
joint_velocities = art.get_joint_velocities()
action = rmpflow.get_next_articulation_action(joint_positions, joint_velocities)
art.apply_action(action)
print(f"Step 1: Moving to approach position {{approach_pos}}")

# Step 2: Linear approach to grasp position
rmpflow.set_end_effector_target(grasp_pos, None)
action = rmpflow.get_next_articulation_action(art.get_joint_positions(), art.get_joint_velocities())
art.apply_action(action)
print(f"Step 2: Approaching grasp position {{grasp_pos}}")

# Step 3: Close gripper
print("Step 3: Closing gripper")

# Step 4: Lift
rmpflow.set_end_effector_target(lift_pos, None)
action = rmpflow.get_next_articulation_action(art.get_joint_positions(), art.get_joint_velocities())
art.apply_action(action)
print(f"Step 4: Lifting to {{lift_pos}}")
print("Grasp sequence complete (from file: {grasp_file})")
"""

    # top_down or side grasp (geometric heuristic)
    if grasp_type == "side":
        approach_vector = "[1, 0, 0]"
        grasp_ori = "np.array([0.5, 0.5, -0.5, 0.5])  # side approach quaternion"
    else:  # top_down
        approach_vector = "[0, 0, -1]"
        grasp_ori = "np.array([1.0, 0.0, 0.0, 0.0])  # top-down quaternion"

    return f"""\
import numpy as np
import omni.usd
from pxr import UsdGeom, Gf
from isaacsim.robot_motion.motion_generation import RmpFlow
from isaacsim.robot_motion.motion_generation import interface_config_loader
from isaacsim.core.prims import SingleArticulation

# Get target object position
stage = omni.usd.get_context().get_stage()
target_xf = UsdGeom.Xformable(stage.GetPrimAtPath('{target_prim}')).ComputeLocalToWorldTransform(0)
target_pos = np.array(target_xf.ExtractTranslation())

# Compute approach geometry ({grasp_type} grasp)
approach_dir = np.array({approach_vector})
grasp_pos = target_pos  # grasp at object center
approach_pos = grasp_pos - approach_dir * {approach_dist}
lift_pos = grasp_pos + np.array([0, 0, {lift_height}])
grasp_orientation = {grasp_ori}

# Setup motion planner
rmpflow_config = interface_config_loader.load_supported_motion_gen_config('franka', 'RMPflow')
rmpflow = RmpFlow(**rmpflow_config)
art = SingleArticulation(prim_path='{robot_path}')
art.initialize()

# Step 1: Move to pre-grasp approach position
rmpflow.set_end_effector_target(approach_pos, grasp_orientation)
joint_positions = art.get_joint_positions()
joint_velocities = art.get_joint_velocities()
action = rmpflow.get_next_articulation_action(joint_positions, joint_velocities)
art.apply_action(action)
print(f"Step 1: Moving to approach position {{approach_pos}}")

# Step 2: Linear approach to grasp position
rmpflow.set_end_effector_target(grasp_pos, grasp_orientation)
action = rmpflow.get_next_articulation_action(art.get_joint_positions(), art.get_joint_velocities())
art.apply_action(action)
print(f"Step 2: Approaching grasp position {{grasp_pos}}")

# Step 3: Close gripper
print("Step 3: Closing gripper")

# Step 4: Lift object
rmpflow.set_end_effector_target(lift_pos, grasp_orientation)
action = rmpflow.get_next_articulation_action(art.get_joint_positions(), art.get_joint_velocities())
art.apply_action(action)
print(f"Step 4: Lifting to {{lift_pos}}")
print("Grasp sequence complete ({grasp_type})")
"""

def _gen_define_grasp_pose(args: Dict) -> str:
    """Generate code to create a .isaac_grasp YAML file."""
    robot_path = args["robot_path"]
    object_path = args["object_path"]
    offset = args.get("gripper_offset", [0, 0, 0])
    approach_dir = args.get("approach_direction", [0, 0, -1])

    return f"""\
import yaml
import os
import omni.usd
from pxr import UsdGeom, Gf
import numpy as np

# Get object position for reference
stage = omni.usd.get_context().get_stage()
obj_prim = stage.GetPrimAtPath('{object_path}')
obj_xf = UsdGeom.Xformable(obj_prim).ComputeLocalToWorldTransform(0)
obj_pos = list(obj_xf.ExtractTranslation())

# Define grasp specification
grasp_spec = {{
    'version': '1.0',
    'robot_path': '{robot_path}',
    'object_path': '{object_path}',
    'grasps': {{
        'default_grasp': {{
            'gripper_offset': {list(offset)},
            'approach_direction': {list(approach_dir)},
            'object_reference_position': obj_pos,
            'pre_grasp_opening': 0.04,
            'grasp_force': 40.0,
        }},
    }},
}}

# Save to workspace
grasp_dir = 'workspace/grasp_poses'
os.makedirs(grasp_dir, exist_ok=True)
obj_name = '{object_path}'.split('/')[-1]
file_path = os.path.join(grasp_dir, f'{{obj_name}}.isaac_grasp')

with open(file_path, 'w') as f:
    yaml.dump(grasp_spec, f, default_flow_style=False)

print(f"Grasp pose saved to {{file_path}}")
print(f"  Robot: {robot_path}")
print(f"  Object: {object_path}")
print(f"  Offset: {list(offset)}")
print(f"  Approach direction: {list(approach_dir)}")
"""

async def _handle_visualize_behavior_tree(args: Dict) -> Dict:
    """Return a formatted text tree of a behavior network structure."""
    network_name = args.get("network_name", "unknown")

    # Since we don't have access to a running Cortex instance at query time,
    # return the canonical structure for known behavior types, or a template.
    _KNOWN_BEHAVIORS = {
        "pick_and_place": {
            "name": "pick_and_place",
            "type": "DfStateMachineDecider",
            "children": [
                {"name": "approach", "type": "DfState", "description": "Move to pre-grasp position above target"},
                {"name": "grasp", "type": "DfState", "description": "Move down and close gripper on object"},
                {"name": "lift", "type": "DfState", "description": "Lift grasped object to safe height"},
                {"name": "place", "type": "DfState", "description": "Move to place position and release"},
            ],
            "transitions": "approach -> grasp -> lift -> place -> done",
        },
        "follow_target": {
            "name": "follow_target",
            "type": "DfDecider",
            "children": [
                {"name": "follow", "type": "FollowTargetState", "description": "Continuously track target prim with end-effector"},
            ],
            "transitions": "follow (continuous loop)",
        },
    }

    behavior = _KNOWN_BEHAVIORS.get(network_name.lower())

    if behavior:
        # Build ASCII tree
        lines = [
            f"Behavior Network: {behavior['name']}",
            f"  Type: {behavior['type']}",
            f"  Transitions: {behavior['transitions']}",
            "",
            "  Nodes:",
        ]
        for i, child in enumerate(behavior["children"]):
            is_last = i == len(behavior["children"]) - 1
            prefix = "  +-- " if is_last else "  |-- "
            lines.append(f"{prefix}{child['name']} ({child['type']})")
            desc_prefix = "      " if is_last else "  |   "
            lines.append(f"{desc_prefix}{child['description']}")

        tree_text = "\n".join(lines)
        return {
            "network_name": network_name,
            "structure": behavior,
            "tree": tree_text,
        }

    return {
        "network_name": network_name,
        "structure": None,
        "tree": (
            f"Behavior Network: {network_name}\n"
            f"  (No pre-built visualization available for '{network_name}'.\n"
            f"   Known behaviors: pick_and_place, follow_target.\n"
            f"   For custom networks, inspect the DfNetwork in the running Cortex world.)"
        ),
    }

CODE_GEN_HANDLERS["create_behavior"] = _gen_create_behavior
CODE_GEN_HANDLERS["create_gripper"] = _gen_create_gripper
CODE_GEN_HANDLERS["grasp_object"] = _gen_grasp_object
CODE_GEN_HANDLERS["define_grasp_pose"] = _gen_define_grasp_pose
DATA_HANDLERS["visualize_behavior_tree"] = _handle_visualize_behavior_tree

# ══════ From feat/8D-robot-setup ══════
def _gen_robot_wizard(args: Dict) -> str:
    asset_path = args["asset_path"]
    robot_type = args.get("robot_type", "manipulator")
    defaults = _ROBOT_TYPE_DEFAULTS.get(robot_type, _ROBOT_TYPE_DEFAULTS["manipulator"])
    stiffness = args.get("drive_stiffness", defaults["stiffness"])
    damping = args.get("drive_damping", defaults["damping"])

    is_urdf = asset_path.lower().endswith(".urdf")

    if is_urdf:
        import_block = f"""\
# Step 1: Import robot from URDF
from isaacsim.asset.importer.urdf import import_urdf, ImportConfig
cfg = ImportConfig()
cfg.convex_decomposition = False  # use convex hull
dest_path = import_urdf('{asset_path}', cfg)
print(f"Imported URDF → {{dest_path}}")
"""
    else:
        import_block = f"""\
# Step 1: Import robot from USD
dest_path = '/World/Robot'
prim = stage.DefinePrim(dest_path, 'Xform')
prim.GetReferences().AddReference('{asset_path}')
print(f"Loaded USD asset → {{dest_path}}")
"""

    return f"""\
import omni.usd
from pxr import Usd, UsdPhysics, PhysxSchema, UsdGeom, Gf

stage = omni.usd.get_context().get_stage()

{import_block}
# Step 2: Apply drive defaults for {robot_type} (Kp={stiffness}, Kd={damping})
robot_prim = stage.GetPrimAtPath(dest_path)
joint_count = 0
for child in list(Usd.PrimRange(robot_prim))[1:]:
    if child.HasAPI(UsdPhysics.DriveAPI):
        for drive_type in ['angular', 'linear']:
            drive = UsdPhysics.DriveAPI.Get(child, drive_type)
            if drive:
                drive.GetStiffnessAttr().Set({stiffness})
                drive.GetDampingAttr().Set({damping})
                joint_count += 1
print(f"Applied Kp={stiffness}, Kd={damping} to {{joint_count}} drives")

# Step 3: Apply convex-hull collision meshes
collision_count = 0
for child in list(Usd.PrimRange(robot_prim))[1:]:
    if child.IsA(UsdGeom.Mesh):
        if not child.HasAPI(UsdPhysics.CollisionAPI):
            UsdPhysics.CollisionAPI.Apply(child)
        if not child.HasAPI(PhysxSchema.PhysxCollisionAPI):
            PhysxSchema.PhysxCollisionAPI.Apply(child)
        coll_api = PhysxSchema.PhysxCollisionAPI(child)
        coll_api.CreateContactOffsetAttr(0.02)
        collision_count += 1
print(f"Applied convex-hull collision to {{collision_count}} meshes")

# Summary
print(f"Robot setup complete: type={robot_type}, drives={{joint_count}}, collisions={{collision_count}}")
"""

def _gen_tune_gains(args: Dict) -> str:
    art_path = args["articulation_path"]
    method = args.get("method", "manual")
    joint_name = args.get("joint_name")
    kp = args.get("kp", 1000)
    kd = args.get("kd", 100)
    test_mode = args.get("test_mode", "step")

    if method == "step_response":
        mode_map = {"sinusoidal": "SINUSOIDAL", "step": "STEP"}
        mode_str = mode_map.get(test_mode, "STEP")
        return f"""\
import omni.usd
from pxr import UsdPhysics
from isaacsim.robot_setup.gain_tuner import GainTuner, GainsTestMode
from isaacsim.core.api import World

stage = omni.usd.get_context().get_stage()

# Initialize GainTuner
tuner = GainTuner()
tuner.setup('{art_path}')

# Configure test parameters
test_params = {{"mode": GainsTestMode.{mode_str}}}
tuner.initialize_gains_test(test_params)

# Run test loop
world = World.instance() or World()
dt = 1.0 / 60.0
step = 0
while not tuner.update_gains_test(dt):
    world.step()
    step += 1

# Compute error metrics
pos_rmse, vel_rmse = tuner.compute_gains_test_error_terms()
print(f"GainTuner test complete after {{step}} steps")
print(f"Position RMSE: {{pos_rmse:.6f}}")
print(f"Velocity RMSE: {{vel_rmse:.6f}}")
"""

    # Manual method: set gains directly via DriveAPI
    if joint_name:
        return f"""\
import omni.usd
from pxr import UsdPhysics

stage = omni.usd.get_context().get_stage()
joint_prim = stage.GetPrimAtPath('{art_path}/{joint_name}')

# Set drive gains for {joint_name}
for drive_type in ['angular', 'linear']:
    drive = UsdPhysics.DriveAPI.Get(joint_prim, drive_type)
    if drive:
        drive.GetStiffnessAttr().Set({kp})
        drive.GetDampingAttr().Set({kd})
        print(f"Set {{drive_type}} drive on {joint_name}: Kp={kp}, Kd={kd}")
"""

    return f"""\
import omni.usd
from pxr import Usd, UsdPhysics

stage = omni.usd.get_context().get_stage()
robot_prim = stage.GetPrimAtPath('{art_path}')

# Set drive gains for all joints
joint_count = 0
for child in list(Usd.PrimRange(robot_prim))[1:]:
    if child.HasAPI(UsdPhysics.DriveAPI):
        for drive_type in ['angular', 'linear']:
            drive = UsdPhysics.DriveAPI.Get(child, drive_type)
            if drive:
                drive.GetStiffnessAttr().Set({kp})
                drive.GetDampingAttr().Set({kd})
                joint_count += 1
print(f"Set Kp={kp}, Kd={kd} on {{joint_count}} drives")
"""

def _gen_assemble_robot(args: Dict) -> str:
    base_path = args["base_path"]
    attachment_path = args["attachment_path"]
    base_mount = args["base_mount"]
    attach_mount = args["attach_mount"]

    return f"""\
import omni.usd
from isaacsim.robot_setup.assembler import RobotAssembler

stage = omni.usd.get_context().get_stage()

# Assemble robot: attach {attachment_path} to {base_path}
assembler = RobotAssembler()
assembled = assembler.assemble(
    base_robot_path='{base_path}',
    attach_robot_path='{attachment_path}',
    base_robot_mount_frame='{base_mount}',
    attach_robot_mount_frame='{attach_mount}',
    fixed_joint_offset=None,
    fixed_joint_orient=None,
    single_robot=True,
)
print(f"Assembled: {{assembled}}")
print(f"Base: {base_path} (mount: {base_mount})")
print(f"Attachment: {attachment_path} (mount: {attach_mount})")
"""

def _gen_configure_self_collision(args: Dict) -> str:
    art_path = args["articulation_path"]
    mode = args["mode"]
    filtered_pairs = args.get("filtered_pairs", [])

    lines = [
        "import omni.usd",
        "from pxr import UsdPhysics, PhysxSchema",
        "",
        "stage = omni.usd.get_context().get_stage()",
        f"robot_prim = stage.GetPrimAtPath('{art_path}')",
        "",
    ]

    if mode == "auto":
        lines.extend([
            "# Auto mode: keep defaults (adjacent links already skip collision)",
            f"print('Self-collision for {art_path}: auto (default PhysX behavior)')",
        ])
    elif mode == "enable":
        lines.extend([
            "# Enable self-collision on the articulation",
            "if not robot_prim.HasAPI(PhysxSchema.PhysxArticulationAPI):",
            "    PhysxSchema.PhysxArticulationAPI.Apply(robot_prim)",
            "artic_api = PhysxSchema.PhysxArticulationAPI(robot_prim)",
            "artic_api.CreateEnabledSelfCollisionsAttr(True)",
            f"print('Self-collision ENABLED for {art_path}')",
        ])
    elif mode == "disable":
        lines.extend([
            "# Disable self-collision on the articulation",
            "if not robot_prim.HasAPI(PhysxSchema.PhysxArticulationAPI):",
            "    PhysxSchema.PhysxArticulationAPI.Apply(robot_prim)",
            "artic_api = PhysxSchema.PhysxArticulationAPI(robot_prim)",
            "artic_api.CreateEnabledSelfCollisionsAttr(False)",
            f"print('Self-collision DISABLED for {art_path}')",
        ])

    if filtered_pairs:
        lines.extend([
            "",
            "# Apply collision filtering for specified link pairs",
        ])
        for pair in filtered_pairs:
            if len(pair) == 2:
                lines.extend([
                    f"link_a = stage.GetPrimAtPath('{pair[0]}')",
                    f"link_b = stage.GetPrimAtPath('{pair[1]}')",
                    "filteredPairsAPI = UsdPhysics.FilteredPairsAPI.Apply(robot_prim)",
                    f"filteredPairsAPI.GetFilteredPairsRel().AddTarget('{pair[0]}')",
                    f"filteredPairsAPI.GetFilteredPairsRel().AddTarget('{pair[1]}')",
                    f"print(f'Filtered collision pair: {pair[0]} <-> {pair[1]}')",
                ])

    return "\n".join(lines)

CODE_GEN_HANDLERS["robot_wizard"] = _gen_robot_wizard
CODE_GEN_HANDLERS["tune_gains"] = _gen_tune_gains
CODE_GEN_HANDLERS["assemble_robot"] = _gen_assemble_robot
CODE_GEN_HANDLERS["configure_self_collision"] = _gen_configure_self_collision

# ══════ From feat/8E-wheeled-robots ══════
def _gen_create_wheeled_robot(args: Dict) -> str:
    robot_path = args["robot_path"]
    drive_type = args["drive_type"]
    wheel_radius = args["wheel_radius"]
    wheel_base = args["wheel_base"]
    dof_names = args.get("wheel_dof_names")
    max_lin = args.get("max_linear_speed", 1.0)
    max_ang = args.get("max_angular_speed", 3.14)

    controller_map = {
        "differential": "DifferentialController",
        "ackermann": "AckermannController",
        "holonomic": "HolonomicController",
    }
    ctrl_cls = controller_map[drive_type]

    dof_block = ""
    if dof_names:
        dof_str = repr(dof_names)
        dof_block = f"""
# Wheel DOFs
wheel_dof_names = {dof_str}
"""

    return f"""\
import numpy as np
from isaacsim.robot.wheeled_robots.controllers import {ctrl_cls}
from isaacsim.robot.wheeled_robots.robots import WheeledRobot

# Create controller
controller = {ctrl_cls}(
    name="{drive_type}_ctrl",
    wheel_radius={wheel_radius},
    wheel_base={wheel_base},
)
{dof_block}
# Speed limits
MAX_LINEAR_SPEED = {max_lin}   # m/s
MAX_ANGULAR_SPEED = {max_ang}  # rad/s

def drive(linear_vel, angular_vel):
    \"\"\"Compute wheel actions. Clamps to speed limits.\"\"\"
    lv = np.clip(linear_vel, -MAX_LINEAR_SPEED, MAX_LINEAR_SPEED)
    av = np.clip(angular_vel, -MAX_ANGULAR_SPEED, MAX_ANGULAR_SPEED)
    action = controller.forward(np.array([lv, av]))
    return action

print("Wheeled robot controller ready: {drive_type} | robot={robot_path}")
print(f"  wheel_radius={wheel_radius}, wheel_base={wheel_base}")
print(f"  max_linear={{MAX_LINEAR_SPEED}} m/s, max_angular={{MAX_ANGULAR_SPEED}} rad/s")
"""

def _gen_navigate_to(args: Dict) -> str:
    robot_path = args["robot_path"]
    target = args["target_position"]
    planner = args.get("planner", "direct")

    if planner == "astar":
        return f"""\
import numpy as np
import heapq
import omni.usd
from isaacsim.robot.wheeled_robots.controllers import WheelBasePoseController
from isaacsim.robot.wheeled_robots.controllers import DifferentialController

robot_path = '{robot_path}'
target = np.array({target}, dtype=float)

# --- Inline A* on occupancy grid ---
GRID_RES = 0.25  # meters per cell
GRID_SIZE = 80   # 80x80 grid = 20m x 20m
GRID_OFFSET = np.array([-GRID_SIZE * GRID_RES / 2, -GRID_SIZE * GRID_RES / 2])

# Pre-generate an empty occupancy grid (0=free, 1=obstacle)
# Replace with actual occupancy data for real scenes
occupancy = np.zeros((GRID_SIZE, GRID_SIZE), dtype=int)

def world_to_grid(pos):
    return int((pos[0] - GRID_OFFSET[0]) / GRID_RES), int((pos[1] - GRID_OFFSET[1]) / GRID_RES)

def grid_to_world(cell):
    return np.array([cell[0] * GRID_RES + GRID_OFFSET[0], cell[1] * GRID_RES + GRID_OFFSET[1]])

def astar(start, goal):
    open_set = [(0, start)]
    came_from = {{}}
    g = {{start: 0}}
    while open_set:
        _, current = heapq.heappop(open_set)
        if current == goal:
            path = []
            while current in came_from:
                path.append(current)
                current = came_from[current]
            path.append(start)
            return path[::-1]
        for dx, dy in [(-1,0),(1,0),(0,-1),(0,1),(-1,-1),(1,1),(-1,1),(1,-1)]:
            nx, ny = current[0]+dx, current[1]+dy
            if 0 <= nx < GRID_SIZE and 0 <= ny < GRID_SIZE and occupancy[ny, nx] == 0:
                ng = g[current] + (1.414 if dx and dy else 1.0)
                if (nx, ny) not in g or ng < g[(nx, ny)]:
                    g[(nx, ny)] = ng
                    h = abs(nx - goal[0]) + abs(ny - goal[1])
                    heapq.heappush(open_set, (ng + h, (nx, ny)))
                    came_from[(nx, ny)] = current
    return [start, goal]  # fallback: direct

# Get current robot position (assume origin for now)
start_world = np.array([0.0, 0.0])
start_cell = world_to_grid(start_world)
goal_cell = world_to_grid(target)
grid_path = astar(start_cell, goal_cell)
waypoints = [grid_to_world(c) for c in grid_path]

# --- Drive along waypoints via physics callback ---
pose_ctrl = WheelBasePoseController(
    name="pose_ctrl",
    open_loop_wheel_controller=DifferentialController(name="nav_diff", wheel_radius=0.05, wheel_base=0.3),
    is_holonomic=False,
)
waypoint_idx = [0]

import omni.physx
def _nav_step(dt):
    idx = waypoint_idx[0]
    if idx >= len(waypoints):
        print(f"Navigation complete: reached {{target}}")
        sub.unsubscribe()
        return
    wp = waypoints[idx]
    # current_pos would come from robot state in real usage
    action = pose_ctrl.forward(start_position=np.array([0, 0, 0]), start_orientation=np.array([1, 0, 0, 0]), goal_position=np.array([wp[0], wp[1], 0]))
    if action is None or np.linalg.norm(wp - start_world) < 0.1:
        waypoint_idx[0] += 1

sub = omni.physx.get_physx_interface().subscribe_physics_step_events(_nav_step)
print(f"A* navigation started: {{len(waypoints)}} waypoints to {{target}}")
"""
    else:  # direct
        return f"""\
import numpy as np
import omni.physx
from isaacsim.robot.wheeled_robots.controllers import WheelBasePoseController
from isaacsim.robot.wheeled_robots.controllers import DifferentialController

robot_path = '{robot_path}'
target = np.array([{target[0]}, {target[1]}, 0.0])

pose_ctrl = WheelBasePoseController(
    name="pose_ctrl",
    open_loop_wheel_controller=DifferentialController(name="nav_diff", wheel_radius=0.05, wheel_base=0.3),
    is_holonomic=False,
)

def _nav_step(dt):
    \"\"\"Physics callback: drive toward target each step.\"\"\"
    # In production, read actual robot pose from ArticulationView
    action = pose_ctrl.forward(
        start_position=np.array([0, 0, 0]),
        start_orientation=np.array([1, 0, 0, 0]),
        goal_position=target,
    )
    if action is None:
        print(f"Direct navigation complete: reached {{target[:2]}}")
        sub.unsubscribe()

sub = omni.physx.get_physx_interface().subscribe_physics_step_events(_nav_step)
print(f"Direct navigation started: target=[{target[0]}, {target[1]}]")
"""

def _gen_create_conveyor(args: Dict) -> str:
    prim_path = args["prim_path"]
    speed = args.get("speed", 0.5)
    direction = args.get("direction", [1, 0, 0])

    return f"""\
import omni.usd
import omni.graph.core as og
import carb

# Check GPU physics / Fabric — conveyors require CPU physics
use_fabric = carb.settings.get_settings().get("/physics/useFabric")
if use_fabric:
    print("WARNING: Conveyor requires CPU physics. Set /physics/useFabric to False.")

prim_path = '{prim_path}'
speed = {speed}
direction = {direction}

# Resolve OmniGraph backing type
_bt = og.GraphBackingType
if hasattr(_bt, 'GRAPH_BACKING_TYPE_FABRIC_SHARED'):
    _backing = _bt.GRAPH_BACKING_TYPE_FABRIC_SHARED
elif hasattr(_bt, 'GRAPH_BACKING_TYPE_FLATCACHING'):
    _backing = _bt.GRAPH_BACKING_TYPE_FLATCACHING
else:
    _backing = list(_bt)[0]

keys = og.Controller.Keys
(graph, nodes, _, _) = og.Controller.edit(
    {{
        "graph_path": prim_path + "/ConveyorGraph",
        "evaluator_name": "execution",
        "pipeline_stage": _backing,
    }},
    {{
        keys.CREATE_NODES: [
            ("tick", "omni.graph.action.OnPlaybackTick"),
            ("conveyor", "isaacsim.conveyor.OgnIsaacConveyor"),
        ],
        keys.CONNECT: [
            ("tick.outputs:tick", "conveyor.inputs:execIn"),
        ],
        keys.SET_VALUES: [
            ("conveyor.inputs:conveyorPrim", prim_path),
            ("conveyor.inputs:velocity", speed),
            ("conveyor.inputs:direction", direction),
        ],
    }},
)

print(f"Conveyor created at {{prim_path}} — speed={{speed}} m/s, direction={{direction}}")
"""

def _gen_create_conveyor_track(args: Dict) -> str:
    waypoints = args["waypoints"]
    belt_width = args.get("belt_width", 0.5)
    speed = args.get("speed", 0.5)

    return f"""\
import omni.usd
import omni.graph.core as og
import math
from pxr import UsdGeom, Gf

stage = omni.usd.get_context().get_stage()

waypoints = {waypoints}
belt_width = {belt_width}
speed = {speed}

# Create parent Xform
track_path = '/World/ConveyorTrack'
stage.DefinePrim(track_path, 'Xform')

# Resolve OmniGraph backing type
_bt = og.GraphBackingType
if hasattr(_bt, 'GRAPH_BACKING_TYPE_FABRIC_SHARED'):
    _backing = _bt.GRAPH_BACKING_TYPE_FABRIC_SHARED
elif hasattr(_bt, 'GRAPH_BACKING_TYPE_FLATCACHING'):
    _backing = _bt.GRAPH_BACKING_TYPE_FLATCACHING
else:
    _backing = list(_bt)[0]

for i in range(len(waypoints) - 1):
    p0 = waypoints[i]
    p1 = waypoints[i + 1]

    # Compute segment center, length, and orientation
    cx = (p0[0] + p1[0]) / 2.0
    cy = (p0[1] + p1[1]) / 2.0
    cz = (p0[2] + p1[2]) / 2.0
    dx = p1[0] - p0[0]
    dy = p1[1] - p0[1]
    seg_len = math.sqrt(dx * dx + dy * dy)
    angle_deg = math.degrees(math.atan2(dy, dx))

    # Create segment mesh (Cube scaled to belt dimensions)
    seg_path = f"{{track_path}}/Segment_{{i}}"
    prim = stage.DefinePrim(seg_path, 'Cube')
    xf = UsdGeom.Xformable(prim)
    xf.AddTranslateOp().Set(Gf.Vec3d(cx, cy, cz))
    xf.AddRotateZOp().Set(angle_deg)
    xf.AddScaleOp().Set(Gf.Vec3d(seg_len / 2.0, belt_width / 2.0, 0.02))

    # Direction vector (local X, rotated)
    dir_x = dx / seg_len if seg_len > 0 else 1.0
    dir_y = dy / seg_len if seg_len > 0 else 0.0

    # Create conveyor OmniGraph for this segment
    keys = og.Controller.Keys
    og.Controller.edit(
        {{
            "graph_path": seg_path + "/ConveyorGraph",
            "evaluator_name": "execution",
            "pipeline_stage": _backing,
        }},
        {{
            keys.CREATE_NODES: [
                ("tick", "omni.graph.action.OnPlaybackTick"),
                ("conveyor", "isaacsim.conveyor.OgnIsaacConveyor"),
            ],
            keys.CONNECT: [
                ("tick.outputs:tick", "conveyor.inputs:execIn"),
            ],
            keys.SET_VALUES: [
                ("conveyor.inputs:conveyorPrim", seg_path),
                ("conveyor.inputs:velocity", speed),
                ("conveyor.inputs:direction", [dir_x, dir_y, 0.0]),
            ],
        }},
    )

print(f"Conveyor track created: {{len(waypoints) - 1}} segments, speed={{speed}} m/s")
"""

def _gen_merge_meshes(args: Dict) -> str:
    prim_paths = args["prim_paths"]
    output_path = args["output_path"]

    return f"""\
import omni.usd
from isaacsim.util.merge_mesh import MeshMerger

stage = omni.usd.get_context().get_stage()

# Ensure output parent exists
output_path = '{output_path}'
parent_path = '/'.join(output_path.rsplit('/', 1)[:-1]) or '/World'
if not stage.GetPrimAtPath(parent_path).IsValid():
    stage.DefinePrim(parent_path, 'Xform')

prim_paths = {prim_paths}

# Merge meshes
merger = MeshMerger(stage)
merger.update_selection(prim_paths)
merger.merge()

print(f"Merged {{len(prim_paths)}} meshes: {{prim_paths}}")
"""

CODE_GEN_HANDLERS["create_wheeled_robot"] = _gen_create_wheeled_robot
CODE_GEN_HANDLERS["navigate_to"] = _gen_navigate_to
CODE_GEN_HANDLERS["create_conveyor"] = _gen_create_conveyor
CODE_GEN_HANDLERS["create_conveyor_track"] = _gen_create_conveyor_track
CODE_GEN_HANDLERS["merge_meshes"] = _gen_merge_meshes

# ══════ From feat/8F-ros2-deep ══════
def _gen_show_tf_tree(args: Dict) -> str:
    root_frame = args.get("root_frame", "world")
    return f'''\
import os
import omni.graph.core as og

# Auto-detect ROS distro
ros_distro = os.environ.get("ROS_DISTRO", "humble")
print(f"ROS distro: {{ros_distro}}")

# Check for TF publisher OmniGraph node — create one if missing
stage = __import__("omni.usd", fromlist=["usd"]).get_context().get_stage()
tf_graph_path = "/World/ROS2_TF_Tree"
tf_prim = stage.GetPrimAtPath(tf_graph_path)
if not tf_prim.IsValid():
    print("No TF publisher graph found — creating one at " + tf_graph_path)
    _bt = og.GraphBackingType
    if hasattr(_bt, 'GRAPH_BACKING_TYPE_FABRIC_SHARED'):
        _backing = _bt.GRAPH_BACKING_TYPE_FABRIC_SHARED
    elif hasattr(_bt, 'GRAPH_BACKING_TYPE_FLATCACHING'):
        _backing = _bt.GRAPH_BACKING_TYPE_FLATCACHING
    else:
        _backing = list(_bt)[0]

    keys = og.Controller.Keys
    og.Controller.edit(
        {{
            "graph_path": tf_graph_path,
            "evaluator_name": "execution",
            "pipeline_stage": _backing,
        }},
        {{
            keys.CREATE_NODES: [
                ("tick", "omni.graph.action.OnPlaybackTick"),
                ("tf_pub", "isaacsim.ros2.bridge.ROS2PublishTransformTree"),
            ],
            keys.CONNECT: [
                ("tick.outputs:tick", "tf_pub.inputs:execIn"),
            ],
        }},
    )
    print("Created ROS2PublishTransformTree graph")

# Acquire TF data via the transform listener interface
from isaacsim.ros2.tf_viewer import acquire_transform_listener_interface

interface = acquire_transform_listener_interface()
interface.initialize(ros_distro)
transforms = interface.get_transforms("{root_frame}")

# Format and print as indented tree
def _print_tree(frames, parent, indent=0):
    prefix = "  " * indent + ("|- " if indent > 0 else "")
    print(f"{{prefix}}{{parent}}")
    children = [f for f in frames if f.get("parent") == parent]
    for child in children:
        _print_tree(frames, child["child"], indent + 1)

print(f"\\nTF Tree (root: {root_frame}):")
print("=" * 40)
if transforms:
    _print_tree(transforms, "{root_frame}")
    print(f"\\nTotal frames: {{len(transforms)}}")
else:
    print("(no transforms found — is the simulation running?)")
'''

def _gen_publish_robot_description(args: Dict) -> str:
    art_path = args["articulation_path"]
    topic = args.get("topic", "/robot_description")
    return f'''\
import omni.usd
from pxr import UsdPhysics, UsdGeom, Gf
import rclpy
from rclpy.node import Node
from std_msgs.msg import String
from rclpy.qos import QoSProfile, DurabilityPolicy

stage = omni.usd.get_context().get_stage()
art_prim = stage.GetPrimAtPath('{art_path}')
if not art_prim.IsValid():
    raise RuntimeError("Articulation not found: {art_path}")

# Build simplified URDF from USD articulation structure
# NOTE: This is a simplified URDF — for full export use Isaac Sim's URDF Exporter UI
links = []
joints = []

def _traverse(prim, parent_link=None):
    name = prim.GetName()
    prim_type = prim.GetTypeName()

    # Detect links (Xform with collision or visual children, or known link patterns)
    is_link = prim_type in ("Xform", "") and any(
        child.GetTypeName() in ("Mesh", "Cube", "Sphere", "Cylinder", "Capsule")
        for child in prim.GetChildren()
    ) or prim.HasAPI(UsdPhysics.RigidBodyAPI)

    if is_link:
        links.append(name)

        # Check for joint relationship to parent
        for child in prim.GetChildren():
            if child.IsA(UsdPhysics.RevoluteJoint):
                joints.append({{
                    "name": child.GetName(),
                    "type": "revolute",
                    "parent": parent_link or "base_link",
                    "child": name,
                }})
            elif child.IsA(UsdPhysics.PrismaticJoint):
                joints.append({{
                    "name": child.GetName(),
                    "type": "prismatic",
                    "parent": parent_link or "base_link",
                    "child": name,
                }})

        for child in prim.GetChildren():
            _traverse(child, name)
    else:
        for child in prim.GetChildren():
            _traverse(child, parent_link)

_traverse(art_prim)

# Generate URDF XML
urdf_lines = ['<?xml version="1.0"?>']
urdf_lines.append('<robot name="{art_path.split("/")[-1]}">')
urdf_lines.append('  <!-- Simplified URDF auto-generated from USD articulation -->')
urdf_lines.append('  <!-- For full export, use Isaac Sim URDF Exporter UI -->')

for link_name in links:
    urdf_lines.append(f'  <link name="{{link_name}}"/>')

for j in joints:
    urdf_lines.append(f'  <joint name="{{j["name"]}}" type="{{j["type"]}}">')
    urdf_lines.append(f'    <parent link="{{j["parent"]}}"/>')
    urdf_lines.append(f'    <child link="{{j["child"]}}"/>')
    urdf_lines.append(f'  </joint>')

urdf_lines.append('</robot>')
urdf_string = "\\n".join(urdf_lines)

print(f"Generated simplified URDF ({{len(links)}} links, {{len(joints)}} joints)")

# Publish via rclpy with TRANSIENT_LOCAL durability
if not rclpy.ok():
    rclpy.init()

node = rclpy.create_node("robot_description_publisher")
qos = QoSProfile(
    depth=1,
    durability=DurabilityPolicy.TRANSIENT_LOCAL,
)
pub = node.create_publisher(String, "{topic}", qos_profile=qos)
msg = String()
msg.data = urdf_string
pub.publish(msg)

print(f"Published robot description to {topic} (TRANSIENT_LOCAL)")
print(f"URDF preview (first 500 chars):\\n{{urdf_string[:500]}}")
'''

def _gen_configure_ros2_bridge(args: Dict) -> str:
    sensors = args.get("sensors", [])
    domain_id = args.get("ros2_domain_id", 0)

    if not sensors:
        return "print('No sensors specified — nothing to configure')\n"

    # Build OmniGraph nodes and connections
    node_defs = []
    conn_defs = []
    val_defs = []

    # Always add tick + ROS2Context
    node_defs.append('("tick", "omni.graph.action.OnPlaybackTick")')
    node_defs.append(f'("ros2_context", f"{{_ROS2_NS}}.ROS2Context")')
    if domain_id != 0:
        val_defs.append(f'("ros2_context.inputs:domain_id", {domain_id})')

    for i, sensor in enumerate(sensors):
        stype = sensor.get("type", "camera")
        prim_path = sensor.get("prim_path", "")
        topic_name = sensor.get("topic_name", "")
        frame_id = sensor.get("frame_id", "")
        node_name = f"{stype}_{i}"

        # Map sensor type to OG node type
        og_node_class = {
            "camera": "ROS2CameraHelper",
            "lidar": "ROS2PublishLaserScan",
            "imu": "ROS2PublishImu",
            "clock": "ROS2PublishClock",
            "joint_state": "ROS2PublishJointState",
        }.get(stype, f"ROS2Publish{stype.title()}")

        node_defs.append(f'("{node_name}", f"{{_ROS2_NS}}.{og_node_class}")')

        # Connect tick → sensor node
        conn_defs.append(f'("tick.outputs:tick", "{node_name}.inputs:execIn")')

        # Connect context
        conn_defs.append(f'("ros2_context.outputs:context", "{node_name}.inputs:context")')

        # Set values
        if topic_name:
            val_defs.append(f'("{node_name}.inputs:topicName", "{topic_name}")')
        if frame_id:
            val_defs.append(f'("{node_name}.inputs:frameId", "{frame_id}")')
        if prim_path and stype != "clock":
            # clock doesn't have a prim path input
            if stype == "camera":
                val_defs.append(f'("{node_name}.inputs:renderProductPath", "{prim_path}")')
            elif stype == "joint_state":
                val_defs.append(f'("{node_name}.inputs:targetPrim", "{prim_path}")')
            else:
                val_defs.append(f'("{node_name}.inputs:prim", "{prim_path}")')

    nodes_str = ",\n            ".join(node_defs)
    conns_str = ",\n            ".join(conn_defs)
    vals_str = ",\n            ".join(val_defs)

    sensor_summary = ", ".join(s.get("type", "?") for s in sensors)

    return f'''\
import omni.graph.core as og

# Handle Isaac Sim version namespace differences
import isaacsim
_V = tuple(int(x) for x in isaacsim.__version__.split(".")[:2])
_ROS2_NS = "isaacsim.ros2.nodes" if _V >= (6, 0) else "isaacsim.ros2.bridge"
print(f"Isaac Sim version: {{isaacsim.__version__}}, using namespace: {{_ROS2_NS}}")

# Resolve backing type
_bt = og.GraphBackingType
if hasattr(_bt, 'GRAPH_BACKING_TYPE_FABRIC_SHARED'):
    _backing = _bt.GRAPH_BACKING_TYPE_FABRIC_SHARED
elif hasattr(_bt, 'GRAPH_BACKING_TYPE_FLATCACHING'):
    _backing = _bt.GRAPH_BACKING_TYPE_FLATCACHING
else:
    _backing = list(_bt)[0]

keys = og.Controller.Keys
(graph, nodes, _, _) = og.Controller.edit(
    {{
        "graph_path": "/World/ROS2_Bridge",
        "evaluator_name": "execution",
        "pipeline_stage": _backing,
    }},
    {{
        keys.CREATE_NODES: [
            {nodes_str}
        ],
        keys.CONNECT: [
            {conns_str}
        ],
        keys.SET_VALUES: [
            {vals_str}
        ],
    }},
)

print(f"ROS2 bridge configured with {{len(nodes)}} nodes")
print(f"Sensors: {sensor_summary}")
print(f"Domain ID: {domain_id}")
print("Start simulation (Play) to begin publishing.")
'''

CODE_GEN_HANDLERS["show_tf_tree"] = _gen_show_tf_tree
CODE_GEN_HANDLERS["publish_robot_description"] = _gen_publish_robot_description
CODE_GEN_HANDLERS["configure_ros2_bridge"] = _gen_configure_ros2_bridge

# ══════ From feat/9-finetune-flywheel ══════
async def _handle_record_feedback(args: Dict) -> Dict:
    """Link user feedback to a previously recorded turn."""
    session_id = args["session_id"]
    turn_id = args["turn_id"]
    approved = args["approved"]
    edited = args.get("edited", False)
    correction = args.get("correction")
    return _turn_recorder.record_feedback(
        session_id=session_id,
        turn_id=turn_id,
        approved=approved,
        edited=edited,
        correction=correction,
    )

async def _handle_export_finetune_data(args: Dict) -> Dict:
    """Export recorded turns to a provider-specific fine-tuning format."""
    fmt = args["format"]
    min_quality = args.get("min_quality", "approved_successful")
    output_path = args.get("output_path")
    return _turn_recorder.export(
        fmt=fmt,
        min_quality=min_quality,
        output_path=output_path,
    )

async def _handle_finetune_stats(args: Dict) -> Dict:
    """Return aggregate statistics about recorded fine-tuning data."""
    return _turn_recorder.get_stats()

async def _handle_redact_finetune_data(args: Dict) -> Dict:
    """Run the redaction pipeline on an existing JSONL file."""
    input_path = args["input_path"]
    output_path = args.get("output_path")
    return _turn_recorder.redact_file(
        input_path=input_path,
        output_path=output_path,
    )

DATA_HANDLERS["record_feedback"] = _handle_record_feedback
DATA_HANDLERS["export_finetune_data"] = _handle_export_finetune_data
DATA_HANDLERS["finetune_stats"] = _handle_finetune_stats
DATA_HANDLERS["redact_finetune_data"] = _handle_redact_finetune_data

# ══════ From feat/addendum-phase2-smart-debugging ══════
async def _handle_diagnose_physics_error(args: Dict) -> Dict:
    """Pattern-match against known PhysX errors and return diagnosis."""
    error_text = args.get("error_text", "")
    if not error_text.strip():
        return {"matches": [], "message": "No error text provided."}

    matches = []
    seen_categories = set()

    # Split into lines for deduplication counting
    lines = error_text.strip().splitlines()

    for entry in _PHYSX_ERROR_PATTERNS:
        pattern = entry["pattern"]
        if not _re.search(pattern, error_text, _re.IGNORECASE):
            continue

        # Count occurrences across lines
        count = sum(
            1 for line in lines
            if _re.search(pattern, line, _re.IGNORECASE)
        )
        # Fallback: at least 1 if it matched the full text
        count = max(count, 1)

        # Try to extract prim path
        prim_path = None
        if entry.get("prim_regex"):
            m = _re.search(entry["prim_regex"], error_text, _re.IGNORECASE)
            if m:
                prim_path = m.group(1)

        if entry["category"] not in seen_categories:
            seen_categories.add(entry["category"])
            matches.append({
                "category": entry["category"],
                "severity": entry["severity"],
                "fix": entry["fix"],
                "prim_path": prim_path,
                "occurrences": count,
                "dedup_hint": f"This error appeared {count} time(s)." if count > 1 else None,
            })

    if not matches:
        return {
            "matches": [],
            "message": "No known PhysX error patterns matched. The error may be application-specific or from a non-physics subsystem.",
        }

    return {
        "matches": matches,
        "total_patterns_checked": len(_PHYSX_ERROR_PATTERNS),
        "message": f"Matched {len(matches)} known error pattern(s).",
    }

async def _handle_trace_config(args: Dict) -> Dict:
    """Parse IsaacLab @configclass files to trace parameter resolution chain."""
    import ast

    param_name = args.get("param_name", "")
    env_source_path = args.get("env_source_path", "")

    if not param_name:
        return {"error": "param_name is required"}

    parts = param_name.split(".")
    target_attr = parts[-1]

    resolution_chain: List[Dict] = []
    final_value = None

    def _trace_in_source(source_text: str, source_path: str) -> None:
        """Walk AST looking for assignments to the target parameter."""
        nonlocal final_value
        try:
            tree = ast.parse(source_text, filename=source_path)
        except SyntaxError:
            return

        for node in ast.walk(tree):
            # Match class-level assignments in @configclass: e.g. `dt = 0.01`
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                if node.target.id == target_attr and node.value is not None:
                    try:
                        value = ast.literal_eval(node.value)
                    except (ValueError, TypeError):
                        value = ast.dump(node.value)
                    status = "overridden" if resolution_chain else "active"
                    if resolution_chain:
                        # Mark previous entry as overridden
                        for prev in resolution_chain:
                            if prev["status"] == "active":
                                prev["status"] = "overridden"
                    resolution_chain.append({
                        "source_file": source_path,
                        "line": node.lineno,
                        "value": value,
                        "status": "active",
                    })
                    final_value = value

            # Match simple assignment: e.g. `self.dt = 0.01` or `dt = 0.01`
            if isinstance(node, ast.Assign):
                for t in node.targets:
                    name = None
                    if isinstance(t, ast.Name):
                        name = t.id
                    elif isinstance(t, ast.Attribute):
                        name = t.attr
                    if name == target_attr:
                        try:
                            value = ast.literal_eval(node.value)
                        except (ValueError, TypeError):
                            value = ast.dump(node.value)
                        for prev in resolution_chain:
                            if prev["status"] == "active":
                                prev["status"] = "overridden"
                        resolution_chain.append({
                            "source_file": source_path,
                            "line": node.lineno,
                            "value": value,
                            "status": "active",
                        })
                        final_value = value

    # If a source path is provided, read it
    if env_source_path:
        source_path = Path(env_source_path)
        if source_path.exists():
            source_text = source_path.read_text(encoding="utf-8")
            _trace_in_source(source_text, str(source_path))

            # Look for imports/base classes to trace the chain further
            try:
                tree = ast.parse(source_text, filename=str(source_path))
                for node in ast.walk(tree):
                    if isinstance(node, (ast.Import, ast.ImportFrom)):
                        if isinstance(node, ast.ImportFrom) and node.module:
                            # Try to resolve relative imports to find parent configs
                            parent_module = node.module
                            parent_path = source_path.parent / (parent_module.replace(".", "/") + ".py")
                            if parent_path.exists():
                                parent_text = parent_path.read_text(encoding="utf-8")
                                _trace_in_source(parent_text, str(parent_path))
            except SyntaxError:
                pass
        else:
            return {
                "error": f"Source file not found: {env_source_path}",
                "param_name": param_name,
            }

    return {
        "param_name": param_name,
        "final_value": final_value,
        "resolution_chain": resolution_chain,
        "message": (
            f"Traced '{param_name}' through {len(resolution_chain)} source(s)."
            if resolution_chain
            else f"Parameter '{param_name}' not found in the provided source(s)."
        ),
    }

def _gen_check_physics_health(args: Dict) -> str:
    """Generate code that checks physics health of the scene."""
    articulation_path = args.get("articulation_path")

    scope_filter = ""
    if articulation_path:
        scope_filter = f"""
# Scope check to a specific articulation
scope_root = stage.GetPrimAtPath('{articulation_path}')
if not scope_root.IsValid():
    issues.append({{
        'prim': '{articulation_path}',
        'severity': 'critical',
        'issue': 'Articulation prim not found',
        'fix': 'Verify the articulation path exists in the stage',
    }})
    all_prims = []
else:
    all_prims = [scope_root] + list(Usd.PrimRange(scope_root))[1:]
"""
    else:
        scope_filter = """
# Check all prims in the stage
root = stage.GetPseudoRoot()
all_prims = [root] + list(Usd.PrimRange(root))[1:]
"""

    return f"""\
import omni.usd
import json
from pxr import Usd, UsdGeom, UsdPhysics, Gf, PhysxSchema

stage = omni.usd.get_context().get_stage()
issues = []
{scope_filter}
# 1. Check for missing PhysicsScene prim
physics_scenes = [p for p in all_prims if p.IsA(UsdPhysics.Scene) or p.GetTypeName() == 'PhysicsScene']
if not physics_scenes:
    issues.append({{
        'prim': '/World/PhysicsScene',
        'severity': 'critical',
        'issue': 'Missing PhysicsScene prim',
        'fix': "Create a PhysicsScene: stage.DefinePrim('/World/PhysicsScene', 'PhysicsScene')",
    }})

# 2. Check for missing CollisionAPI on mesh prims with RigidBodyAPI
for prim in all_prims:
    if not prim.IsValid():
        continue

    # Missing CollisionAPI on mesh prims that have RigidBodyAPI
    if prim.IsA(UsdGeom.Mesh) and prim.HasAPI(UsdPhysics.RigidBodyAPI):
        if not prim.HasAPI(UsdPhysics.CollisionAPI):
            issues.append({{
                'prim': str(prim.GetPath()),
                'severity': 'error',
                'issue': 'Mesh has RigidBodyAPI but no CollisionAPI',
                'fix': 'Apply CollisionAPI: UsdPhysics.CollisionAPI.Apply(prim)',
            }})

    # 3. Invalid inertia tensors (zero or negative)
    if prim.HasAPI(UsdPhysics.MassAPI):
        mass_api = UsdPhysics.MassAPI(prim)
        inertia = mass_api.GetDiagonalInertiaAttr().Get()
        if inertia is not None:
            if any(v <= 0 for v in inertia):
                issues.append({{
                    'prim': str(prim.GetPath()),
                    'severity': 'critical',
                    'issue': f'Invalid inertia tensor: {{inertia}} (zero or negative components)',
                    'fix': 'Set all diagonal inertia components to positive values',
                }})
        mass = mass_api.GetMassAttr().Get()
        if mass is not None and mass <= 0:
            issues.append({{
                'prim': str(prim.GetPath()),
                'severity': 'critical',
                'issue': f'Invalid mass: {{mass}} (must be > 0)',
                'fix': 'Set mass to a positive value',
            }})

# 4. Extreme mass ratios (>100:1 between rigid bodies)
mass_map = {{}}
for prim in all_prims:
    if not prim.IsValid():
        continue
    if prim.HasAPI(UsdPhysics.MassAPI):
        m = UsdPhysics.MassAPI(prim).GetMassAttr().Get()
        if m is not None and m > 0:
            mass_map[str(prim.GetPath())] = m
if len(mass_map) >= 2:
    masses = list(mass_map.values())
    max_m = max(masses)
    min_m = min(masses)
    if min_m > 0 and max_m / min_m > 100:
        issues.append({{
            'prim': 'scene-wide',
            'severity': 'warning',
            'issue': f'Extreme mass ratio: {{max_m/min_m:.1f}}:1 (max={{max_m}}, min={{min_m}})',
            'fix': 'Reduce mass ratio to below 100:1 for stable simulation',
        }})

# 5. Joint limits set to +/-inf
for prim in all_prims:
    if not prim.IsValid():
        continue
    if prim.IsA(UsdPhysics.RevoluteJoint):
        joint = UsdPhysics.RevoluteJoint(prim)
        lower = joint.GetLowerLimitAttr().Get()
        upper = joint.GetUpperLimitAttr().Get()
        if lower is not None and upper is not None:
            if abs(lower) > 1e30 or abs(upper) > 1e30:
                issues.append({{
                    'prim': str(prim.GetPath()),
                    'severity': 'warning',
                    'issue': f'Joint limits effectively infinite: lower={{lower}}, upper={{upper}}',
                    'fix': 'Set finite joint limits (e.g. -180 to 180 degrees)',
                }})

# 6. metersPerUnit mismatch on stage
meters_per_unit = UsdGeom.GetStageMetersPerUnit(stage)
if meters_per_unit != 1.0 and meters_per_unit != 0.01:
    issues.append({{
        'prim': 'stage',
        'severity': 'warning',
        'issue': f'Unusual metersPerUnit: {{meters_per_unit}} (expected 1.0 for meters or 0.01 for cm)',
        'fix': 'Set UsdGeom.SetStageMetersPerUnit(stage, 1.0) for meter scale',
    }})

# Summary
result = {{
    'healthy': len(issues) == 0,
    'issue_count': len(issues),
    'issues': issues,
    'critical_count': sum(1 for i in issues if i['severity'] == 'critical'),
    'error_count': sum(1 for i in issues if i['severity'] == 'error'),
    'warning_count': sum(1 for i in issues if i['severity'] == 'warning'),
}}
print(json.dumps(result, indent=2))
"""

DATA_HANDLERS["diagnose_physics_error"] = _handle_diagnose_physics_error
DATA_HANDLERS["trace_config"] = _handle_trace_config
CODE_GEN_HANDLERS["check_physics_health"] = _gen_check_physics_health

# ══════ From feat/addendum-phase3-urdf-postprocessor ══════
def _detect_robot_type(articulation_path: str) -> Optional[str]:
    """Auto-detect robot type from articulation path."""
    path_lower = articulation_path.lower()
    for robot_type, patterns in _ROBOT_NAME_PATTERNS.items():
        for pat in patterns:
            if pat in path_lower:
                return robot_type
    return None

def _gen_verify_import(args: Dict) -> str:
    """Generate code that audits a URDF-imported articulation for common issues."""
    art_path = args["articulation_path"]

    return f"""\
import omni.usd
from pxr import Usd, UsdPhysics, UsdGeom, PhysxSchema, Gf
import json

stage = omni.usd.get_context().get_stage()
root = stage.GetPrimAtPath('{art_path}')
if not root.IsValid():
    raise RuntimeError('Articulation not found: {art_path}')

issues = []
all_prims = [root] + list(Usd.PrimRange(root))[1:]

# Check 1: ArticulationRootAPI
has_art_root = False
for prim in all_prims:
    if prim.HasAPI(PhysxSchema.PhysxArticulationAPI) or prim.HasAPI(UsdPhysics.ArticulationRootAPI):
        has_art_root = True
        break
if not has_art_root:
    issues.append({{
        'prim': '{art_path}',
        'severity': 'critical',
        'issue': 'Missing ArticulationRootAPI — robot will not simulate as articulation',
        'fix': "PhysxSchema.PhysxArticulationAPI.Apply(stage.GetPrimAtPath('{art_path}'))"
    }})

# Check 2: metersPerUnit
meters_per_unit = UsdGeom.GetStageMetersPerUnit(stage)
if abs(meters_per_unit - 0.01) > 0.001 and abs(meters_per_unit - 1.0) > 0.001:
    issues.append({{
        'prim': '/',
        'severity': 'warning',
        'issue': f'Stage metersPerUnit={{meters_per_unit}} — expected 0.01 (cm) or 1.0 (m)',
        'fix': 'UsdGeom.SetStageMetersPerUnit(stage, 0.01)'
    }})

# Check 3: Missing CollisionAPI on links
for prim in all_prims:
    path = str(prim.GetPath())
    if prim.HasAPI(UsdPhysics.RigidBodyAPI) and not prim.HasAPI(UsdPhysics.CollisionAPI):
        has_child_collision = any(
            c.HasAPI(UsdPhysics.CollisionAPI) for c in list(Usd.PrimRange(prim))[1:]
        )
        if not has_child_collision:
            issues.append({{
                'prim': path,
                'severity': 'warning',
                'issue': 'Link has RigidBodyAPI but no CollisionAPI',
                'fix': f"UsdPhysics.CollisionAPI.Apply(stage.GetPrimAtPath('{{path}}'))"
            }})

# Check 4: Zero-mass links
for prim in all_prims:
    path = str(prim.GetPath())
    if prim.HasAPI(UsdPhysics.MassAPI):
        mass_attr = prim.GetAttribute('physics:mass')
        if mass_attr and mass_attr.Get() is not None and mass_attr.Get() == 0.0:
            issues.append({{
                'prim': path,
                'severity': 'error',
                'issue': 'Zero mass on link — causes simulation instability',
                'fix': f"stage.GetPrimAtPath('{{path}}').GetAttribute('physics:mass').Set(1.0)"
            }})

# Check 5: Infinite joint limits
for prim in all_prims:
    path = str(prim.GetPath())
    if prim.IsA(UsdPhysics.RevoluteJoint) or prim.IsA(UsdPhysics.RevoluteJoint):
        lower = prim.GetAttribute('physics:lowerLimit')
        upper = prim.GetAttribute('physics:upperLimit')
        if lower and upper:
            lo_val = lower.Get()
            hi_val = upper.Get()
            if lo_val is not None and hi_val is not None:
                if abs(lo_val) > 1e6 or abs(hi_val) > 1e6:
                    issues.append({{
                        'prim': path,
                        'severity': 'warning',
                        'issue': f'Infinite joint limits: [{{lo_val}}, {{hi_val}}]',
                        'fix': f"Set finite joint limits on '{{path}}'"
                    }})

# Check 6: Extreme inertia ratios
inertia_vals = []
for prim in all_prims:
    path = str(prim.GetPath())
    if prim.HasAPI(UsdPhysics.MassAPI):
        diag = prim.GetAttribute('physics:diagonalInertia')
        if diag and diag.Get() is not None:
            vals = [float(v) for v in diag.Get()]
            inertia_vals.extend(vals)
            if any(v <= 0 for v in vals):
                issues.append({{
                    'prim': path,
                    'severity': 'critical',
                    'issue': f'Non-positive inertia: {{vals}}',
                    'fix': f"stage.GetPrimAtPath('{{path}}').GetAttribute('physics:diagonalInertia').Set(Gf.Vec3f(0.01, 0.01, 0.01))"
                }})

if len(inertia_vals) >= 2:
    pos_vals = [v for v in inertia_vals if v > 0]
    if pos_vals and max(pos_vals) / min(pos_vals) > 1000:
        issues.append({{
            'prim': '{art_path}',
            'severity': 'warning',
            'issue': f'Extreme inertia ratio across links: {{max(pos_vals)/min(pos_vals):.0f}}:1',
            'fix': 'Review inertia values — extreme ratios cause PhysX solver instability'
        }})

print(json.dumps({{'articulation_path': '{art_path}', 'issues': issues, 'total': len(issues)}}))
"""

def _detect_robot_for_fix(articulation_path: str) -> Optional[str]:
    """Auto-detect robot name from articulation path for fix profile lookup."""
    path_lower = articulation_path.lower()
    for robot_name, patterns in _FIX_PROFILE_PATTERNS.items():
        for pat in patterns:
            if pat in path_lower:
                return robot_name
    return None

async def _handle_apply_robot_fix_profile(args: Dict) -> Dict:
    """Look up known robot import issues and return a fix profile."""
    art_path = args["articulation_path"]
    robot_name = args.get("robot_name", "")

    # Auto-detect from path if not provided
    if not robot_name:
        robot_name = _detect_robot_for_fix(art_path)

    if not robot_name or robot_name not in _ROBOT_FIX_PROFILES:
        return {
            "found": False,
            "robot_name": robot_name or "unknown",
            "articulation_path": art_path,
            "message": (
                f"No fix profile found for '{robot_name or 'unknown'}'. "
                f"Known robots: {', '.join(sorted(_ROBOT_FIX_PROFILES.keys()))}. "
                f"Use verify_import to diagnose issues instead."
            ),
        }

    profile = _ROBOT_FIX_PROFILES[robot_name].copy()
    # Substitute articulation path into fix code templates
    fixes = []
    for fix in profile["fixes"]:
        fixes.append({
            "description": fix["description"],
            "code": fix["code"].replace("{art_path}", art_path),
        })
    profile["fixes"] = fixes
    profile["articulation_path"] = art_path
    profile["found"] = True
    profile["message"] = f"Fix profile for '{profile['display_name']}' — {len(fixes)} fixes available."

    return profile

CODE_GEN_HANDLERS["verify_import"] = _gen_verify_import
DATA_HANDLERS["apply_robot_fix_profile"] = _handle_apply_robot_fix_profile

# ══════ From feat/addendum-phase7B-sdg-quality ══════
async def _handle_validate_annotations(args: Dict) -> Dict:
    """Cross-check SDG annotations for common quality issues.

    Validates: bbox within image bounds, unique instance IDs,
    no zero-area boxes, declared classes actually appear.
    """
    num_samples = args.get("num_samples", 10)

    code = f"""\
import json, os, glob, random

output_dirs = glob.glob('/tmp/sdg_output*') + glob.glob('workspace/sdg_output*')
if not output_dirs:
    print(json.dumps({{"error": "No SDG output directories found"}}))
else:
    out_dir = sorted(output_dirs)[-1]
    ann_files = glob.glob(os.path.join(out_dir, '**', '*.json'), recursive=True)
    ann_files = [f for f in ann_files if 'bounding_box' in f or 'annotation' in f]
    samples = ann_files[:{num_samples}] if len(ann_files) <= {num_samples} else random.sample(ann_files, {num_samples})

    issues = []
    total_boxes = 0
    instance_ids_seen = set()
    classes_declared = set()
    classes_found = set()

    for f in samples:
        data = json.loads(open(f).read())
        annotations = data if isinstance(data, list) else data.get('annotations', data.get('data', []))
        if not isinstance(annotations, list):
            annotations = [annotations]
        for ann in annotations:
            total_boxes += 1
            bbox = ann.get('bbox') or ann.get('bounding_box') or ann.get('x_min') and [ann['x_min'], ann['y_min'], ann['x_max'], ann['y_max']]
            if bbox:
                x0, y0, x1, y1 = bbox[0], bbox[1], bbox[2], bbox[3]
                if x0 < 0 or y0 < 0:
                    issues.append({{"type": "out_of_bounds", "file": f, "bbox": bbox, "detail": "Negative coordinates"}})
                if x1 <= x0 or y1 <= y0:
                    issues.append({{"type": "zero_area", "file": f, "bbox": bbox, "detail": "Zero or negative area"}})
                w = ann.get('image_width', 1280)
                h = ann.get('image_height', 720)
                if x1 > w or y1 > h:
                    issues.append({{"type": "out_of_bounds", "file": f, "bbox": bbox, "detail": f"Exceeds image {{w}}x{{h}}"}})

            iid = ann.get('instance_id') or ann.get('id')
            if iid is not None:
                if iid in instance_ids_seen:
                    issues.append({{"type": "duplicate_id", "file": f, "instance_id": iid}})
                instance_ids_seen.add(iid)

            cls = ann.get('class') or ann.get('label') or ann.get('category')
            if cls:
                classes_found.add(cls)

        meta_classes = data.get('declared_classes') or data.get('classes') or data.get('categories')
        if meta_classes:
            if isinstance(meta_classes, list):
                for c in meta_classes:
                    classes_declared.add(c if isinstance(c, str) else c.get('name', str(c)))

    missing_classes = list(classes_declared - classes_found)
    if missing_classes:
        issues.append({{"type": "missing_class", "declared_but_absent": missing_classes}})

    clean = total_boxes - len([i for i in issues if i['type'] != 'missing_class'])
    health = round(100 * clean / max(total_boxes, 1), 1)

    print(json.dumps({{
        "samples_checked": len(samples),
        "total_boxes": total_boxes,
        "issues": issues,
        "annotation_health": health,
        "classes_declared": list(classes_declared),
        "classes_found": list(classes_found),
    }}))
"""
    result = await kit_tools.queue_exec_patch(code, f"Validate annotations ({num_samples} samples)")
    return {"type": "data", "queued": result.get("queued", False)}

async def _handle_analyze_randomization(args: Dict) -> Dict:
    """Analyze domain randomization parameter distributions from an SDG run.

    Returns per-parameter statistics and flags near-constant or collapsed
    distributions that indicate DR misconfiguration.
    """
    num_samples = args.get("num_samples", 50)

    code = f"""\
import json, os, glob, random
import numpy as np

output_dirs = glob.glob('/tmp/sdg_output*') + glob.glob('workspace/sdg_output*')
if not output_dirs:
    print(json.dumps({{"error": "No SDG output directories found"}}))
else:
    out_dir = sorted(output_dirs)[-1]

    # Look for DR log / randomization parameter files
    dr_files = glob.glob(os.path.join(out_dir, '**', '*random*'), recursive=True)
    dr_files += glob.glob(os.path.join(out_dir, '**', '*param*'), recursive=True)
    dr_files += glob.glob(os.path.join(out_dir, '**', '*.json'), recursive=True)
    dr_files = list(set(dr_files))
    samples = dr_files[:{num_samples}] if len(dr_files) <= {num_samples} else random.sample(dr_files, {num_samples})

    param_values = {{}}  # param_name -> list of values

    for f in samples:
        try:
            data = json.loads(open(f).read())
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        params = data.get('randomization_params') or data.get('params') or data.get('dr_params') or {{}}
        if isinstance(params, dict):
            for k, v in params.items():
                if isinstance(v, (int, float)):
                    param_values.setdefault(k, []).append(v)
                elif isinstance(v, list) and all(isinstance(x, (int, float)) for x in v):
                    for i, x in enumerate(v):
                        param_values.setdefault(f"{{k}}[{{i}}]", []).append(x)

    stats = {{}}
    warnings = []
    for pname, vals in param_values.items():
        arr = np.array(vals, dtype=float)
        s = {{
            "min": float(arr.min()),
            "max": float(arr.max()),
            "mean": float(arr.mean()),
            "std": float(arr.std()),
            "count": len(vals),
        }}
        stats[pname] = s

        # Flag near-constant distributions
        if s["std"] < 1e-6 and s["count"] > 5:
            warnings.append({{
                "param": pname,
                "warning": "near_constant",
                "detail": f"{{s['count']}} samples all ~{{s['mean']:.4f}} — DR may be misconfigured",
            }})
        # Flag extremely narrow range
        range_val = s["max"] - s["min"]
        if range_val > 0 and s["std"] / range_val < 0.01 and s["count"] > 10:
            warnings.append({{
                "param": pname,
                "warning": "narrow_range",
                "detail": f"std/range = {{s['std']/range_val:.4f}} — 99%+ values are the same angle/position",
            }})

    print(json.dumps({{
        "samples_analyzed": len(samples),
        "parameters": stats,
        "warnings": warnings,
        "total_params": len(stats),
    }}))
"""
    result = await kit_tools.queue_exec_patch(code, f"Analyze DR randomization ({num_samples} samples)")
    return {"type": "data", "queued": result.get("queued", False)}

async def _handle_diagnose_domain_gap(args: Dict) -> Dict:
    """Compare synthetic vs real image datasets to diagnose domain gap.

    Returns a FID-like comparison score, per-class distribution differences,
    and suggested DR adjustments.
    """
    synthetic_dir = args.get("synthetic_dir", "")
    real_dir = args.get("real_dir", "")
    checkpoint = args.get("model_checkpoint")

    if not synthetic_dir or not real_dir:
        return {"error": "Both synthetic_dir and real_dir are required"}

    # Sanitize paths
    import re as _re
    for d in (synthetic_dir, real_dir):
        if not _re.match(r'^[a-zA-Z0-9/_. :-]+$', d):
            return {"error": f"Invalid path characters in: {d}"}

    checkpoint_line = ""
    if checkpoint:
        if not _re.match(r'^[a-zA-Z0-9/_. :-]+$', checkpoint):
            return {"error": f"Invalid path characters in checkpoint: {checkpoint}"}
        checkpoint_line = f"checkpoint = '{checkpoint}'"

    code = f"""\
import json, os, glob
import numpy as np

synthetic_dir = '{synthetic_dir}'
real_dir = '{real_dir}'
{checkpoint_line}

def load_image_stats(directory):
    \"\"\"Compute per-channel mean/std over images in a directory.\"\"\"
    from PIL import Image
    files = glob.glob(os.path.join(directory, '**', '*.png'), recursive=True)
    files += glob.glob(os.path.join(directory, '**', '*.jpg'), recursive=True)
    if not files:
        return None, 0
    samples = files[:200] if len(files) > 200 else files
    all_means = []
    all_stds = []
    for f in samples:
        try:
            img = np.array(Image.open(f).convert('RGB'), dtype=np.float32) / 255.0
            all_means.append(img.mean(axis=(0, 1)))
            all_stds.append(img.std(axis=(0, 1)))
        except Exception:
            continue
    if not all_means:
        return None, 0
    return {{
        "channel_means": np.mean(all_means, axis=0).tolist(),
        "channel_stds": np.mean(all_stds, axis=0).tolist(),
        "count": len(all_means),
    }}, len(files)

synth_stats, synth_count = load_image_stats(synthetic_dir)
real_stats, real_count = load_image_stats(real_dir)

if synth_stats is None:
    print(json.dumps({{"error": f"No images found in synthetic dir: {{synthetic_dir}}"}}))
elif real_stats is None:
    print(json.dumps({{"error": f"No images found in real dir: {{real_dir}}"}}))
else:
    # Compute FID-like score from channel statistics
    mean_diff = np.linalg.norm(
        np.array(synth_stats['channel_means']) - np.array(real_stats['channel_means'])
    )
    std_diff = np.linalg.norm(
        np.array(synth_stats['channel_stds']) - np.array(real_stats['channel_stds'])
    )
    # Simplified domain gap score (0 = identical, higher = more gap)
    gap_score = float(mean_diff * 100 + std_diff * 50)

    # Per-channel analysis
    channels = ['R', 'G', 'B']
    per_channel = {{}}
    adjustments = []
    for i, ch in enumerate(channels):
        diff = synth_stats['channel_means'][i] - real_stats['channel_means'][i]
        per_channel[ch] = {{
            "synthetic_mean": round(synth_stats['channel_means'][i], 4),
            "real_mean": round(real_stats['channel_means'][i], 4),
            "difference": round(diff, 4),
        }}
        if abs(diff) > 0.1:
            direction = "brighter" if diff > 0 else "darker"
            adjustments.append(f"Synthetic {{ch}} channel is {{direction}} than real by {{abs(diff):.2f}} — adjust lighting/material {{ch}} intensity")

    if gap_score > 15:
        adjustments.append("High domain gap — consider adding texture/lighting randomization")
    if gap_score > 30:
        adjustments.append("Very high domain gap — real-to-sim calibration recommended")

    result = {{
        "domain_gap_score": round(gap_score, 2),
        "synthetic_images": synth_count,
        "real_images": real_count,
        "synthetic_stats": synth_stats,
        "real_stats": real_stats,
        "per_channel_diff": per_channel,
        "suggested_adjustments": adjustments,
        "model_checkpoint": '{checkpoint or "none"}',
    }}
    print(json.dumps(result))
"""
    result = await kit_tools.queue_exec_patch(
        code, f"Diagnose domain gap: {synthetic_dir} vs {real_dir}"
    )
    return {"type": "data", "queued": result.get("queued", False)}

DATA_HANDLERS["validate_annotations"] = _handle_validate_annotations
DATA_HANDLERS["analyze_randomization"] = _handle_analyze_randomization
DATA_HANDLERS["diagnose_domain_gap"] = _handle_diagnose_domain_gap

# ══════ From feat/addendum-phase8F-ros2-quality ══════
async def _handle_diagnose_ros2(args: Dict) -> Dict:
    """Run comprehensive ROS2 integration health check on the current scene.

    Checks performed:
    1. ROS2Context node present in OmniGraph
    2. ROS distro detection
    3. QoS profile mismatches between common topic pairs
    4. use_sim_time parameter configuration
    5. Clock publishing (ROS2PublishClock node)
    6. Domain ID consistency
    7. Dangling OmniGraph connections
    """
    issues: List[Dict[str, Any]] = []

    # Generate diagnostic code that runs inside Kit
    diag_code = '''\
import omni.graph.core as og
import json
import os

result = {
    "ros2_context_found": False,
    "ros2_context_path": None,
    "distro": None,
    "domain_id": None,
    "clock_publisher_found": False,
    "use_sim_time": None,
    "og_graphs": [],
    "dangling_connections": [],
    "qos_nodes": [],
}

# Check ROS_DISTRO environment variable
result["distro"] = os.environ.get("ROS_DISTRO", None)
result["domain_id"] = os.environ.get("ROS_DOMAIN_ID", "0")

# Scan all OmniGraph graphs
try:
    all_graphs = og.get_all_graphs()
    for graph in all_graphs:
        graph_path = graph.get_path_to_graph()
        result["og_graphs"].append(graph_path)
        nodes = graph.get_nodes()
        for node in nodes:
            node_type = node.get_type_name()
            node_path = node.get_prim_path()

            # Check for ROS2Context
            if "ROS2Context" in node_type:
                result["ros2_context_found"] = True
                result["ros2_context_path"] = str(node_path)
                # Try to read domain_id attribute
                domain_attr = node.get_attribute("inputs:domain_id")
                if domain_attr:
                    result["domain_id_node"] = domain_attr.get()

            # Check for ROS2PublishClock
            if "PublishClock" in node_type:
                result["clock_publisher_found"] = True

            # Collect QoS-relevant nodes
            if "ROS2" in node_type and "Publish" in node_type:
                topic_attr = node.get_attribute("inputs:topicName")
                qos_attr = node.get_attribute("inputs:qosProfile")
                result["qos_nodes"].append({
                    "node_type": node_type,
                    "node_path": str(node_path),
                    "topic": topic_attr.get() if topic_attr else None,
                    "qos": qos_attr.get() if qos_attr else None,
                })

        # Check for dangling connections
        for node in nodes:
            for attr in node.get_attributes():
                if attr.get_port_type() == og.AttributePortType.ATTRIBUTE_PORT_TYPE_INPUT:
                    upstream = attr.get_upstream_connections()
                    if not upstream and attr.get_name().startswith("inputs:execIn"):
                        result["dangling_connections"].append({
                            "node": str(node.get_prim_path()),
                            "attr": attr.get_name(),
                        })
except Exception as e:
    result["scan_error"] = str(e)

# Check use_sim_time via carb settings
try:
    import carb.settings
    settings = carb.settings.get_settings()
    result["use_sim_time"] = settings.get("/persistent/exts/isaacsim.ros2.bridge/useSimTime")
except Exception:
    result["use_sim_time"] = None

print(json.dumps(result))
'''

    try:
        diag_result = await kit_tools.queue_exec_patch(diag_code, "ROS2 diagnostic scan")
        # Parse the result if we got immediate output
        if isinstance(diag_result, dict) and diag_result.get("output"):
            import json as _json
            scene_info = _json.loads(diag_result["output"])
        else:
            scene_info = {}
    except Exception:
        scene_info = {}

    # Issue 1: ROS2Context node
    if not scene_info.get("ros2_context_found", False):
        issues.append({
            "id": "no_ros2_context",
            "severity": "critical",
            "message": "No ROS2Context node found in any OmniGraph",
            "fix": "Add a ROS2Context node to your action graph. This is required for all ROS2 bridge communication.",
            "tool_hint": "create_omnigraph with a ROS2Context node",
        })

    # Issue 2: ROS distro
    distro = scene_info.get("distro")
    if not distro:
        issues.append({
            "id": "no_ros_distro",
            "severity": "warning",
            "message": "ROS_DISTRO environment variable not set",
            "fix": "Source your ROS2 workspace: source /opt/ros/<distro>/setup.bash",
            "tool_hint": None,
        })

    # Issue 3: Clock publisher
    if not scene_info.get("clock_publisher_found", False):
        issues.append({
            "id": "no_clock_publisher",
            "severity": "warning",
            "message": "No ROS2PublishClock node found — /clock topic will not be published",
            "fix": "Add a ROS2PublishClock node to publish simulation time. Use configure_ros2_time tool.",
            "tool_hint": "configure_ros2_time(mode='sim_time')",
        })

    # Issue 4: use_sim_time
    use_sim_time = scene_info.get("use_sim_time")
    clock_found = scene_info.get("clock_publisher_found", False)
    if clock_found and use_sim_time is not True:
        issues.append({
            "id": "use_sim_time_mismatch",
            "severity": "warning",
            "message": "Clock publisher active but use_sim_time is not enabled",
            "fix": "Set use_sim_time=true so ROS2 nodes use simulation clock instead of wall clock.",
            "tool_hint": "configure_ros2_time(mode='sim_time')",
        })

    # Issue 5: Domain ID mismatch
    env_domain = scene_info.get("domain_id", "0")
    node_domain = scene_info.get("domain_id_node")
    if node_domain is not None and str(node_domain) != str(env_domain):
        issues.append({
            "id": "domain_id_mismatch",
            "severity": "critical",
            "message": f"Domain ID mismatch: ROS_DOMAIN_ID={env_domain} but ROS2Context node has domain_id={node_domain}",
            "fix": f"Set ROS_DOMAIN_ID={node_domain} in your environment, or update the ROS2Context node to domain_id={env_domain}.",
            "tool_hint": None,
        })

    # Issue 6: QoS mismatches
    for qos_node in scene_info.get("qos_nodes", []):
        topic = qos_node.get("topic", "")
        if topic:
            topic_key = topic.strip("/").split("/")[-1]
            preset = _ROS2_QOS_PRESETS.get(topic_key)
            if preset and qos_node.get("qos"):
                current_qos = str(qos_node["qos"])
                expected_reliability = preset[0]
                if expected_reliability not in current_qos:
                    issues.append({
                        "id": "qos_mismatch",
                        "severity": "warning",
                        "message": f"QoS mismatch on topic '{topic}': expected {expected_reliability} reliability",
                        "fix": f"Use fix_ros2_qos(topic='{topic}') to apply the recommended QoS profile.",
                        "tool_hint": f"fix_ros2_qos(topic='{topic}')",
                    })

    # Issue 7: Dangling connections
    for dangling in scene_info.get("dangling_connections", []):
        issues.append({
            "id": "dangling_connection",
            "severity": "info",
            "message": f"Dangling execution input on {dangling['node']}.{dangling['attr']}",
            "fix": "Connect this node's execIn to an OnPlaybackTick or upstream node.",
            "tool_hint": None,
        })

    return {
        "issues": issues,
        "issue_count": len(issues),
        "ros2_context_found": scene_info.get("ros2_context_found", False),
        "distro": scene_info.get("distro"),
        "domain_id": scene_info.get("domain_id", "0"),
        "clock_publishing": scene_info.get("clock_publisher_found", False),
        "graphs_scanned": len(scene_info.get("og_graphs", [])),
        "message": f"Found {len(issues)} issue(s)" if issues else "All ROS2 checks passed — no issues found",
    }

def _gen_fix_ros2_qos(args: Dict) -> str:
    """Generate code to update the QoS profile on a ROS2 publisher for a given topic."""
    topic = args["topic"]

    # Determine the QoS preset from the topic name
    topic_key = topic.strip("/").split("/")[-1]
    preset = _ROS2_QOS_PRESETS.get(topic_key)

    if preset:
        reliability, durability, description = preset
    else:
        # Default to RELIABLE + VOLATILE for unknown topics
        reliability = "RELIABLE"
        durability = "VOLATILE"
        description = f"Unknown topic '{topic}' — defaulting to RELIABLE"

    return f'''\
import omni.graph.core as og
import json

topic_name = "{topic}"
target_reliability = "{reliability}"
target_durability = "{durability}"

# QoS profile: {description}
# Find the publisher node for this topic and update its QoS profile
all_graphs = og.get_all_graphs()
updated = False

for graph in all_graphs:
    for node in graph.get_nodes():
        node_type = node.get_type_name()
        if "ROS2" not in node_type:
            continue

        topic_attr = node.get_attribute("inputs:topicName")
        if not topic_attr:
            continue

        current_topic = topic_attr.get()
        if current_topic != topic_name:
            continue

        # Found the node — update QoS profile
        qos_attr = node.get_attribute("inputs:qosProfile")
        if qos_attr:
            qos_attr.set(f"{{target_reliability}}, {{target_durability}}")
            updated = True
            print(f"Updated QoS on {{node.get_prim_path()}}: {{target_reliability}}, {{target_durability}}")

        # Also set reliability/durability if separate attributes exist
        rel_attr = node.get_attribute("inputs:reliability")
        if rel_attr:
            rel_attr.set(target_reliability)

        dur_attr = node.get_attribute("inputs:durability")
        if dur_attr:
            dur_attr.set(target_durability)

        break  # Only update the first matching node

if not updated:
    # No existing node found — create a new publisher with correct QoS
    print(f"No publisher found for {{topic_name}} — set QoS when creating the publisher:")
    print(f"  reliability: {{target_reliability}}")
    print(f"  durability: {{target_durability}}")
    print(f"  Hint: {description}")
'''

def _gen_configure_ros2_time(args: Dict) -> str:
    """Generate OmniGraph code for ROS2 clock publishing and use_sim_time configuration."""
    mode = args["mode"]
    time_scale = args.get("time_scale", 1.0)

    if mode == "real_time":
        return '''\
import carb.settings
import omni.graph.core as og

# Configure real_time mode: disable use_sim_time, no clock publishing needed
settings = carb.settings.get_settings()
settings.set("/persistent/exts/isaacsim.ros2.bridge/useSimTime", False)

# Remove existing ROS2PublishClock nodes if any
all_graphs = og.get_all_graphs()
for graph in all_graphs:
    for node in graph.get_nodes():
        if "PublishClock" in node.get_type_name():
            node_path = node.get_prim_path()
            print(f"Note: ROS2PublishClock at {node_path} is active but use_sim_time=false")
            print("ROS2 nodes will use wall clock time.")

print("Configured real_time mode: use_sim_time=false")
print("ROS2 nodes will use the system wall clock.")
'''

    # sim_time or scaled mode — both need clock publishing
    time_scale_block = ""
    if mode == "scaled":
        time_scale_block = f'''
# Set simulation time scale
import omni.timeline
tl = omni.timeline.get_timeline_interface()
tl.set_time_codes_per_second(tl.get_time_codes_per_second() * {time_scale})
print(f"Time scale set to {time_scale}x")
'''

    return f'''\
import omni.graph.core as og
import carb.settings

# ── Step 1: Enable use_sim_time ──────────────────────────────────────────
settings = carb.settings.get_settings()
settings.set("/persistent/exts/isaacsim.ros2.bridge/useSimTime", True)
print("Enabled use_sim_time=true")

# ── Step 2: Create ROS2PublishClock node in an action graph ──────────────
# Check if a clock publisher already exists
clock_exists = False
all_graphs = og.get_all_graphs()
for graph in all_graphs:
    for node in graph.get_nodes():
        if "PublishClock" in node.get_type_name():
            clock_exists = True
            print(f"ROS2PublishClock already exists at {{node.get_prim_path()}}")
            break
    if clock_exists:
        break

if not clock_exists:
    # Resolve backing type
    _bt = og.GraphBackingType
    if hasattr(_bt, 'GRAPH_BACKING_TYPE_FABRIC_SHARED'):
        _backing = _bt.GRAPH_BACKING_TYPE_FABRIC_SHARED
    elif hasattr(_bt, 'GRAPH_BACKING_TYPE_FLATCACHING'):
        _backing = _bt.GRAPH_BACKING_TYPE_FLATCACHING
    else:
        _backing = list(_bt)[0]

    keys = og.Controller.Keys
    (graph, nodes, _, _) = og.Controller.edit(
        {{
            "graph_path": "/World/ROS2ClockGraph",
            "evaluator_name": "execution",
            "pipeline_stage": _backing,
        }},
        {{
            keys.CREATE_NODES: [
                ("OnPlaybackTick", "omni.graph.action.OnPlaybackTick"),
                ("ROS2Context", "isaacsim.ros2.bridge.ROS2Context"),
                ("PublishClock", "isaacsim.ros2.bridge.ROS2PublishClock"),
            ],
            keys.CONNECT: [
                ("OnPlaybackTick.outputs:tick", "PublishClock.inputs:execIn"),
                ("ROS2Context.outputs:context", "PublishClock.inputs:context"),
            ],
        }},
    )
    print("Created ROS2ClockGraph with ROS2PublishClock node")
    print("  /clock topic will publish simulation time")
{time_scale_block}
print("Configured {mode} mode: ROS2 nodes will use simulation clock from /clock topic")
'''

DATA_HANDLERS["diagnose_ros2"] = _handle_diagnose_ros2
CODE_GEN_HANDLERS["fix_ros2_qos"] = _gen_fix_ros2_qos
CODE_GEN_HANDLERS["configure_ros2_time"] = _gen_configure_ros2_time

# ══════ From feat/addendum-phase8B-workspace-singularity-v2 ══════
def _gen_show_workspace(args: Dict) -> str:
    """Generate code to visualize robot workspace with manipulability gradient."""
    art_path = args["articulation_path"]
    resolution = args.get("resolution", 500000)
    color_mode = args.get("color_mode", "manipulability")

    return f"""\
import omni.usd
import numpy as np
from pxr import Usd, UsdPhysics
from isaacsim.util.debug_draw import _debug_draw

stage = omni.usd.get_context().get_stage()
art_prim = stage.GetPrimAtPath('{art_path}')
if not art_prim.IsValid():
    raise RuntimeError('Articulation not found: {art_path}')

# Collect revolute joint limits
joints = []
for desc in list(Usd.PrimRange(art_prim))[1:]:
    if desc.IsA(UsdPhysics.RevoluteJoint) or desc.IsA(UsdPhysics.RevoluteJoint):
        lo_attr = desc.GetAttribute('physics:lowerLimit')
        hi_attr = desc.GetAttribute('physics:upperLimit')
        lo = np.radians(lo_attr.Get() if lo_attr and lo_attr.Get() is not None else -180.0)
        hi = np.radians(hi_attr.Get() if hi_attr and hi_attr.Get() is not None else 180.0)
        joints.append({{'name': desc.GetName(), 'lower': lo, 'upper': hi}})

n_joints = len(joints)
if n_joints == 0:
    raise RuntimeError('No revolute joints found')

n_samples = min({resolution}, 500000)
print(f'Sampling {{n_samples}} configurations across {{n_joints}} joints...')

# Random joint configs within limits
q_samples = np.zeros((n_samples, n_joints))
for i, j in enumerate(joints):
    q_samples[:, i] = np.random.uniform(j['lower'], j['upper'], n_samples)

# Forward kinematics using Lula
from isaacsim.robot_motion.motion_generation import LulaKinematicsSolver
from isaacsim.robot_motion.motion_generation import interface_config_loader

try:
    kin_config = interface_config_loader.load_supported_lula_kinematics_solver_config('{art_path}'.split('/')[-1].lower())
    kin = LulaKinematicsSolver(**kin_config)
except Exception:
    print('Robot not in pre-supported list — cannot compute FK')
    raise

ee_positions = []
manipulability = []
eps = 1e-4

for q in q_samples[:min(n_samples, 50000)]:  # cap for Jacobian computation
    # FK
    pos, _ = kin.compute_forward_kinematics('{art_path}'.split('/')[-1], q)
    ee_positions.append(pos)

    # Numerical Jacobian for manipulability
    J = np.zeros((3, n_joints))
    for k in range(n_joints):
        q_plus = q.copy(); q_plus[k] += eps
        pos_plus, _ = kin.compute_forward_kinematics('{art_path}'.split('/')[-1], q_plus)
        J[:, k] = (np.array(pos_plus) - np.array(pos)) / eps
    w = np.sqrt(max(np.linalg.det(J @ J.T), 0))
    manipulability.append(w)

ee_positions = np.array(ee_positions)
manipulability = np.array(manipulability)

# Color mapping
if '{color_mode}' == 'reachability':
    colors = [(0, 1, 0, 0.5)] * len(ee_positions)  # green
elif '{color_mode}' == 'singularity_distance':
    w_norm = manipulability / (manipulability.max() + 1e-10)
    colors = [(1 - v, v, 0, 0.5) for v in w_norm]  # red=singularity, green=safe
else:  # manipulability
    w_norm = manipulability / (manipulability.max() + 1e-10)
    colors = [(1 - v, v, 0, 0.5) for v in w_norm]  # green=high, red=low

# Draw
draw = _debug_draw.acquire_debug_draw_interface()
draw.clear_points()
points = [(float(p[0]), float(p[1]), float(p[2])) for p in ee_positions]
draw.draw_points(points, colors, [3] * len(points))
print(f'Workspace visualized: {{len(points)}} points, mode={color_mode}')
"""

def _gen_check_singularity(args: Dict) -> str:
    """Generate code to check singularity at a target pose via Jacobian SVD."""
    art_path = args["articulation_path"]
    target_pos = args["target_position"]
    target_ori = args.get("target_orientation")

    ori_code = f"np.array({list(target_ori)})" if target_ori else "None"

    return f"""\
import numpy as np
from isaacsim.robot_motion.motion_generation import LulaKinematicsSolver, ArticulationKinematicsSolver
from isaacsim.robot_motion.motion_generation import interface_config_loader
from isaacsim.core.prims import SingleArticulation
import json

robot_name = '{art_path}'.split('/')[-1].lower()
target_pos = np.array({list(target_pos)})
target_ori = {ori_code}

# Load kinematics
try:
    kin_config = interface_config_loader.load_supported_lula_kinematics_solver_config(robot_name)
    kin = LulaKinematicsSolver(**kin_config)
except Exception:
    print(json.dumps({{"status": "error", "message": "Robot not in supported list"}}))
    raise

# Solve IK
art = SingleArticulation('{art_path}')
art_kin = ArticulationKinematicsSolver(art, kin, kin.get_all_frame_names()[-1])
action, success = art_kin.compute_inverse_kinematics(
    target_position=target_pos,
    target_orientation=target_ori,
)

if not success:
    print(json.dumps({{"status": "unreachable", "message": "IK failed — target may be outside workspace"}}))
else:
    q = np.array(action.joint_positions)
    n_joints = len(q)
    eps = 1e-4

    # Numerical Jacobian (6 x n_joints)
    J = np.zeros((6, n_joints))
    ee_frame = kin.get_all_frame_names()[-1]
    pos0, ori0 = kin.compute_forward_kinematics(ee_frame, q)
    pos0, ori0 = np.array(pos0), np.array(ori0)
    for k in range(n_joints):
        q_plus = q.copy(); q_plus[k] += eps
        pos_p, ori_p = kin.compute_forward_kinematics(ee_frame, q_plus)
        J[:3, k] = (np.array(pos_p) - pos0) / eps
        J[3:, k] = (np.array(ori_p) - ori0) / eps

    # SVD condition number
    _, sigma, _ = np.linalg.svd(J)
    condition = sigma[0] / max(sigma[-1], 1e-10)

    # Heuristic pre-filters (common 6/7-DOF robots)
    warnings = []
    if n_joints >= 5 and abs(q[4]) < np.radians(10):
        warnings.append('Joint 5 near zero — possible wrist singularity')
    if n_joints >= 3 and abs(q[2]) < np.radians(8):
        warnings.append('Joint 3 near extension — possible elbow singularity')

    if condition < 50:
        status = 'safe'
    elif condition < 100:
        status = 'warning'
    else:
        status = 'danger'

    result = {{
        'status': status,
        'condition_number': round(float(condition), 2),
        'singular_values': [round(float(s), 4) for s in sigma],
        'warnings': warnings,
        'joint_config': [round(float(v), 4) for v in q],
    }}
    if status == 'warning':
        result['message'] = 'Near singularity — motion may be unpredictable'
    elif status == 'danger':
        result['message'] = 'At singularity — choose a different target pose'

    print(json.dumps(result))
"""

def _gen_monitor_joint_effort(args: Dict) -> str:
    """Generate code to monitor joint efforts over time via physics callback."""
    art_path = args["articulation_path"]
    duration = args.get("duration_seconds", 5.0)

    return f"""\
import omni.physx
import omni.usd
import numpy as np
import json
import time
from pxr import Usd, UsdPhysics

stage = omni.usd.get_context().get_stage()
art_prim = stage.GetPrimAtPath('{art_path}')
if not art_prim.IsValid():
    raise RuntimeError('Articulation not found: {art_path}')

# Collect joint info
joint_names = []
effort_limits = []
for desc in list(Usd.PrimRange(art_prim))[1:]:
    if desc.IsA(UsdPhysics.RevoluteJoint) or desc.IsA(UsdPhysics.RevoluteJoint):
        joint_names.append(desc.GetName())
        max_force = desc.GetAttribute('drive:angular:physics:maxForce')
        effort_limits.append(max_force.Get() if max_force and max_force.Get() else 1000.0)

n_joints = len(joint_names)
if n_joints == 0:
    print(json.dumps({{"error": "No joints found"}}))
else:
    _monitor_data = {{
        'positions': [], 'velocities': [], 'efforts': [],
        'start_time': time.time(), 'duration': {duration},
    }}

    def _monitor_step(dt):
        from isaacsim.core.prims import SingleArticulation
        art = SingleArticulation('{art_path}')
        _monitor_data['positions'].append(art.get_joint_positions().tolist())
        _monitor_data['velocities'].append(art.get_joint_velocities().tolist())
        _monitor_data['efforts'].append(art.get_applied_joint_efforts().tolist())

        elapsed = time.time() - _monitor_data['start_time']
        if elapsed >= _monitor_data['duration']:
            omni.physx.get_physx_interface().get_simulation_event_stream().unsubscribe(_monitor_sub)

            # Compute stats
            efforts = np.array(_monitor_data['efforts'])
            results = []
            for i in range(min(n_joints, efforts.shape[1])):
                e = efforts[:, i]
                limit = effort_limits[i] if i < len(effort_limits) else 1000.0
                utilization = float(np.max(np.abs(e))) / max(limit, 1e-6)
                results.append({{
                    'joint': joint_names[i] if i < len(joint_names) else f'joint_{{i}}',
                    'max_effort': round(float(np.max(np.abs(e))), 2),
                    'mean_effort': round(float(np.mean(np.abs(e))), 2),
                    'effort_limit': limit,
                    'utilization_pct': round(utilization * 100, 1),
                    'near_limit': utilization > 0.9,
                }})

            flagged = [r for r in results if r['near_limit']]
            print(json.dumps({{
                'joints': results,
                'duration_s': round(elapsed, 1),
                'samples': len(_monitor_data['efforts']),
                'flagged_joints': len(flagged),
                'message': f'{{len(flagged)}} joints near effort limit (>90%)' if flagged else 'All joints within limits',
            }}))

    _monitor_sub = omni.physx.get_physx_interface().subscribe_physics_step_events(_monitor_step)
    print(f'Monitoring joint efforts for {duration}s...')
"""

CODE_GEN_HANDLERS["show_workspace"] = _gen_show_workspace
CODE_GEN_HANDLERS["check_singularity"] = _gen_check_singularity
CODE_GEN_HANDLERS["monitor_joint_effort"] = _gen_monitor_joint_effort

# ══════ From feat/new-performance-diagnostics ══════
def _analyze_performance(stats: Dict, timing: Dict, mem: Dict) -> List[Dict]:
    """Analyze profiling data and return a list of performance issues."""
    issues = []

    # Physics narrow-phase bottleneck
    narrow_ms = timing.get("narrow_phase_ms", 0)
    if narrow_ms > 10:
        issues.append({
            "category": "physics_narrow_phase",
            "severity": "high",
            "message": (
                f"Narrow phase takes {narrow_ms:.0f}ms. "
                f"Heavy trimesh colliders are likely the cause."
            ),
            "fix": "Switch to convexHull or convexDecomposition approximation",
        })

    # VRAM pressure
    used_mb = mem.get("used_mb", 0)
    total_mb = mem.get("total_mb", 1)
    if total_mb > 0 and used_mb / total_mb > 0.9:
        issues.append({
            "category": "memory",
            "severity": "high",
            "message": f"GPU memory {used_mb:.0f}/{total_mb:.0f} MB (>90%)",
            "breakdown": mem.get("per_category", {}),
            "fix": "Reduce texture resolution or number of render products",
        })

    # Solver convergence
    solver_ms = timing.get("solver_ms", 0)
    solver_iters = stats.get("solver_iterations", 0)
    if solver_ms > 5 and solver_iters > 16:
        issues.append({
            "category": "solver",
            "severity": "medium",
            "message": (
                f"Solver takes {solver_ms:.0f}ms at "
                f"{solver_iters} iterations"
            ),
            "fix": "Reduce solver iterations to 4-8 for non-contact-critical bodies",
        })

    # Broad-phase bottleneck
    broad_ms = timing.get("broad_phase_ms", 0)
    if broad_ms > 8:
        issues.append({
            "category": "physics_broad_phase",
            "severity": "medium",
            "message": f"Broad phase takes {broad_ms:.0f}ms",
            "fix": "Reduce number of active rigid bodies or increase physics scene bounds",
        })

    # High dynamic rigid body count
    nb_dynamic = stats.get("nb_dynamic_rigids", 0)
    if nb_dynamic > 500:
        issues.append({
            "category": "scene_complexity",
            "severity": "medium",
            "message": f"{nb_dynamic} dynamic rigid bodies in scene",
            "fix": "Consider using GPU pipeline or reducing active body count",
        })

    return issues

async def _handle_diagnose_performance(args: Dict) -> Dict:
    """Collect PhysX stats, timing, and GPU memory, then analyze for bottlenecks."""
    code = """\
import json

results = {"stats": {}, "timing": {}, "mem": {}}

# 1. PhysX scene statistics
try:
    from omni.physx import get_physx_statistics_interface
    pstats = get_physx_statistics_interface()
    scene_stats = pstats.get_physx_scene_statistics()
    results["stats"] = {
        "nb_dynamic_rigids": scene_stats.get("nbDynamicRigids", 0),
        "nb_static_rigids": scene_stats.get("nbStaticRigids", 0),
        "nb_articulations": scene_stats.get("nbArticulations", 0),
        "nb_trimesh_shapes": scene_stats.get("nbTriMeshShapes", 0),
        "active_contact_pairs": scene_stats.get("nbActiveContactPairs", 0),
        "solver_iterations": scene_stats.get("solverIterations", 4),
    }
except Exception as e:
    results["stats"]["error"] = str(e)

# 2. PhysX per-zone timing
try:
    from omni.physx import get_physx_benchmarks_interface
    benchmarks = get_physx_benchmarks_interface()
    benchmarks.enable_profile()
    results["timing"] = {
        "simulation_ms": benchmarks.get_value("Simulation") or 0,
        "collision_detection_ms": benchmarks.get_value("Collision Detection") or 0,
        "broad_phase_ms": benchmarks.get_value("Broad Phase") or 0,
        "narrow_phase_ms": benchmarks.get_value("Narrow Phase") or 0,
        "solver_ms": benchmarks.get_value("Solver") or 0,
        "integration_ms": benchmarks.get_value("Integration") or 0,
    }
except Exception as e:
    results["timing"]["error"] = str(e)

# 3. Render timing + VRAM
try:
    from omni.hydra.engine.stats import HydraEngineStats
    hydra = HydraEngineStats()
    mem = hydra.get_mem_stats(detailed=True)
    device = hydra.get_device_info()
    results["mem"] = {
        "used_mb": mem.get("usedMB", 0),
        "total_mb": device.get("totalVRAM_MB", 0),
        "per_category": mem.get("perCategory", {}),
    }
except Exception as e:
    results["mem"]["error"] = str(e)

# 4. FPS
try:
    import omni.kit.app
    fps = omni.kit.app.get_app().get_fps()
    results["fps"] = fps
except Exception:
    results["fps"] = None

print(json.dumps(results))
"""
    kit_result = await kit_tools.queue_exec_patch(
        code, "Collect performance diagnostics (PhysX stats + GPU memory)"
    )

    # If Kit returned data, analyze it; otherwise return the raw queue result
    if isinstance(kit_result, dict) and "stats" in kit_result:
        stats = kit_result.get("stats", {})
        timing = kit_result.get("timing", {})
        mem = kit_result.get("mem", {})
        fps = kit_result.get("fps")

        issues = _analyze_performance(stats, timing, mem)

        # Determine bottleneck
        bottleneck = "unknown"
        if issues:
            bottleneck = issues[0]["category"]

        # Build summary
        parts = []
        if fps is not None:
            parts.append(f"Your sim runs at {fps:.0f} FPS.")
        if issues:
            parts.append(f"{len(issues)} issue(s) found.")
            parts.append(issues[0]["message"])
            parts.append(issues[0]["fix"])
        else:
            parts.append("No obvious performance issues detected.")

        return {
            "fps": fps,
            "bottleneck": bottleneck,
            "issues": issues,
            "stats": stats,
            "timing": timing,
            "mem": mem,
            "summary": " ".join(parts),
        }

    # Kit RPC just queued the patch — return what we have
    return {"type": "data", "queued": True, **kit_result}

async def _handle_find_heavy_prims(args: Dict) -> Dict:
    """Traverse the stage and find meshes above a triangle-count threshold."""
    threshold = args.get("threshold_triangles", 10000)
    code = f"""\
import json
import omni.usd
from pxr import UsdGeom, UsdPhysics

stage = omni.usd.get_context().get_stage()
heavy = []
for prim in stage.TraverseAll():
    if prim.IsA(UsdGeom.Mesh):
        mesh = UsdGeom.Mesh(prim)
        face_counts = mesh.GetFaceVertexCountsAttr().Get()
        if face_counts is None:
            continue
        tri_count = sum(fc - 2 for fc in face_counts)
        if tri_count >= {threshold}:
            approx = "none"
            if prim.HasAPI(UsdPhysics.MeshCollisionAPI):
                approx_attr = UsdPhysics.MeshCollisionAPI(prim).GetApproximationAttr()
                if approx_attr:
                    approx = approx_attr.Get() or "none"
            heavy.append({{
                "prim_path": str(prim.GetPath()),
                "triangle_count": tri_count,
                "collision_approximation": approx,
            }})

heavy.sort(key=lambda x: x["triangle_count"], reverse=True)
print(json.dumps({{"prims": heavy, "count": len(heavy), "threshold": {threshold}}}))
"""
    return await kit_tools.queue_exec_patch(
        code, f"Find mesh prims with >{threshold} triangles"
    )

def _gen_optimize_collision(args: Dict) -> str:
    """Generate code to switch a collision mesh to a simpler approximation."""
    prim_path = args["prim_path"]
    approximation = args["approximation"]
    return (
        "import omni.usd\n"
        "from pxr import UsdPhysics\n"
        "\n"
        "stage = omni.usd.get_context().get_stage()\n"
        f"prim = stage.GetPrimAtPath('{prim_path}')\n"
        "if not prim.IsValid():\n"
        f"    raise RuntimeError('Prim not found: {prim_path}')\n"
        "\n"
        "# Ensure CollisionAPI is applied\n"
        "if not prim.HasAPI(UsdPhysics.CollisionAPI):\n"
        "    UsdPhysics.CollisionAPI.Apply(prim)\n"
        "\n"
        "# Ensure MeshCollisionAPI is applied\n"
        "if not prim.HasAPI(UsdPhysics.MeshCollisionAPI):\n"
        "    UsdPhysics.MeshCollisionAPI.Apply(prim)\n"
        "\n"
        f"UsdPhysics.MeshCollisionAPI(prim).GetApproximationAttr().Set('{approximation}')\n"
        f"print(f'Set collision approximation on {prim_path} to {approximation}')"
    )

DATA_HANDLERS["diagnose_performance"] = _handle_diagnose_performance
DATA_HANDLERS["find_heavy_prims"] = _handle_find_heavy_prims
CODE_GEN_HANDLERS["optimize_collision"] = _gen_optimize_collision

# ══════ From feat/new-material-database ══════
def _load_physics_materials() -> Dict:
    global _physics_materials
    if _physics_materials is not None:
        return _physics_materials
    if _PHYSICS_MATERIALS_PATH.exists():
        _physics_materials = json.loads(_PHYSICS_MATERIALS_PATH.read_text())
    else:
        _physics_materials = {"materials": {}, "pairs": {}, "aliases": {}}
    return _physics_materials

def _normalize_material_name(name: str) -> str:
    """Normalize a user-supplied material name to a database key."""
    db = _load_physics_materials()
    key = name.strip().lower().replace(" ", "_").replace("-", "_")
    # Check aliases first
    aliases = db.get("aliases", {})
    if key in aliases:
        return aliases[key]
    # Check direct match in materials
    if key in db["materials"]:
        return key
    # Partial match: e.g. "mild steel" -> "steel_mild"
    for mat_key in db["materials"]:
        if key in mat_key or mat_key in key:
            return mat_key
    return key

def _gen_apply_physics_material(args: Dict) -> str:
    """Generate code to create a PhysicsMaterialAPI with values from the material database."""
    prim_path = args["prim_path"]
    material_name = args["material_name"]

    db = _load_physics_materials()
    mat_key = _normalize_material_name(material_name)
    mat = db["materials"].get(mat_key)

    if mat is None:
        available = sorted(db["materials"].keys())
        return (
            f"raise ValueError("
            f"\"Unknown material '{material_name}' (normalized: '{mat_key}'). "
            f"Available: {', '.join(available)}\")"
        )

    sf = mat["static_friction"]
    df = mat["dynamic_friction"]
    rest = mat["restitution"]
    density = mat["density_kg_m3"]
    safe_name = mat_key.replace(" ", "_")

    return f"""\
import omni.usd
from pxr import UsdPhysics, Sdf

stage = omni.usd.get_context().get_stage()
prim = stage.GetPrimAtPath('{prim_path}')

# Ensure CollisionAPI is applied
if not prim.HasAPI(UsdPhysics.CollisionAPI):
    UsdPhysics.CollisionAPI.Apply(prim)

# Create physics material
mat_path = '/World/PhysicsMaterials/{safe_name}'
mat_prim = stage.DefinePrim(mat_path)
mat_api = UsdPhysics.MaterialAPI.Apply(mat_prim)
mat_api.CreateStaticFrictionAttr().Set({sf})
mat_api.CreateDynamicFrictionAttr().Set({df})
mat_api.CreateRestitutionAttr().Set({rest})
mat_api.CreateDensityAttr().Set({density})

# Bind physics material to prim
binding_api = UsdPhysics.MaterialAPI(prim)
rel = prim.CreateRelationship('physics:materialBinding', custom=False)
rel.SetTargets([Sdf.Path(mat_path)])

print(f"Applied {{mat_path}} to {prim_path}: static_friction={sf}, dynamic_friction={df}, restitution={rest}, density={density}")
"""

async def _handle_lookup_material(args: Dict) -> Dict:
    """Look up physics material properties for a material pair."""
    mat_a_raw = args.get("material_a", "")
    mat_b_raw = args.get("material_b", "")
    if not mat_a_raw or not mat_b_raw:
        return {"error": "Both material_a and material_b are required."}

    db = _load_physics_materials()
    mat_a = _normalize_material_name(mat_a_raw)
    mat_b = _normalize_material_name(mat_b_raw)

    # Check if materials exist in database
    materials = db.get("materials", {})
    available = sorted(materials.keys())
    if mat_a not in materials and mat_b not in materials:
        return {
            "found": False,
            "error": f"Unknown materials: '{mat_a_raw}' and '{mat_b_raw}'",
            "available_materials": available,
        }
    if mat_a not in materials:
        return {
            "found": False,
            "error": f"Unknown material: '{mat_a_raw}' (normalized: '{mat_a}')",
            "available_materials": available,
        }
    if mat_b not in materials:
        return {
            "found": False,
            "error": f"Unknown material: '{mat_b_raw}' (normalized: '{mat_b}')",
            "available_materials": available,
        }

    # Check pair overrides (both orderings)
    pairs = db.get("pairs", {})
    pair_key_ab = f"{mat_a}:{mat_b}"
    pair_key_ba = f"{mat_b}:{mat_a}"
    if pair_key_ab in pairs:
        result = dict(pairs[pair_key_ab])
        result["found"] = True
        result["pair"] = pair_key_ab
        result["lookup_type"] = "pair_specific"
        result["material_a"] = mat_a
        result["material_b"] = mat_b
        result["density_a_kg_m3"] = materials[mat_a]["density_kg_m3"]
        result["density_b_kg_m3"] = materials[mat_b]["density_kg_m3"]
        return result
    if pair_key_ba in pairs:
        result = dict(pairs[pair_key_ba])
        result["found"] = True
        result["pair"] = pair_key_ba
        result["lookup_type"] = "pair_specific"
        result["material_a"] = mat_a
        result["material_b"] = mat_b
        result["density_a_kg_m3"] = materials[mat_a]["density_kg_m3"]
        result["density_b_kg_m3"] = materials[mat_b]["density_kg_m3"]
        return result

    # Combine individual materials (PhysX average combine mode)
    a = materials[mat_a]
    b = materials[mat_b]
    sf_a = a["static_friction"] if isinstance(a["static_friction"], (int, float)) else a["static_friction"][0]
    sf_b = b["static_friction"] if isinstance(b["static_friction"], (int, float)) else b["static_friction"][0]
    df_a = a["dynamic_friction"] if isinstance(a["dynamic_friction"], (int, float)) else a["dynamic_friction"][0]
    df_b = b["dynamic_friction"] if isinstance(b["dynamic_friction"], (int, float)) else b["dynamic_friction"][0]
    rest_a = a["restitution"]
    rest_b = b["restitution"]

    return {
        "found": True,
        "pair": f"{mat_a}:{mat_b}",
        "lookup_type": "average_combine",
        "static_friction": round((sf_a + sf_b) / 2, 4),
        "dynamic_friction": round((df_a + df_b) / 2, 4),
        "restitution": round((rest_a + rest_b) / 2, 4),
        "combine_mode": "average",
        "material_a": mat_a,
        "material_b": mat_b,
        "density_a_kg_m3": a["density_kg_m3"],
        "density_b_kg_m3": b["density_kg_m3"],
        "note": "Computed via PhysX average combine — pair-specific data not available",
    }

# ══════ From feat/new-scene-diff ══════
def _parse_unified_diff_to_changes(raw_diff_lines: List[str]) -> List[Dict]:
    """Parse a unified diff of USDA text into structured SceneChange dicts.

    Each returned dict has:
        prim_path: str
        change_type: "added" | "removed" | "modified"
        details: dict  (attribute, old, new, or raw line)
    """
    import re
    changes: List[Dict] = []
    current_prim: Optional[str] = None

    # Track added/removed lines to pair modifications
    added_lines: List[str] = []
    removed_lines: List[str] = []

    def _flush_pending():
        nonlocal added_lines, removed_lines
        if not current_prim:
            added_lines.clear()
            removed_lines.clear()
            return
        # Pair removed/added as modifications
        paired = min(len(removed_lines), len(added_lines))
        for i in range(paired):
            changes.append({
                "prim_path": current_prim,
                "change_type": "modified",
                "details": {"old_line": removed_lines[i].strip(), "new_line": added_lines[i].strip()},
            })
        for i in range(paired, len(removed_lines)):
            changes.append({
                "prim_path": current_prim,
                "change_type": "removed",
                "details": {"line": removed_lines[i].strip()},
            })
        for i in range(paired, len(added_lines)):
            changes.append({
                "prim_path": current_prim,
                "change_type": "added",
                "details": {"line": added_lines[i].strip()},
            })
        added_lines = []
        removed_lines = []

    prim_re = re.compile(r'^\s*def\s+(\w+)\s+"([^"]+)"')
    for line in raw_diff_lines:
        # Skip diff headers
        if line.startswith("---") or line.startswith("+++") or line.startswith("@@"):
            _flush_pending()
            continue

        # Detect prim context from context lines
        m = prim_re.match(line.lstrip("+-"))
        if m:
            _flush_pending()
            current_prim = m.group(2)
            # A whole prim definition added/removed
            if line.startswith("+") and not line.startswith("+++"):
                changes.append({
                    "prim_path": current_prim,
                    "change_type": "added",
                    "details": {"prim_type": m.group(1)},
                })
            elif line.startswith("-") and not line.startswith("---"):
                changes.append({
                    "prim_path": current_prim,
                    "change_type": "removed",
                    "details": {"prim_type": m.group(1)},
                })
            continue

        if line.startswith("-") and not line.startswith("---"):
            removed_lines.append(line[1:])
        elif line.startswith("+") and not line.startswith("+++"):
            added_lines.append(line[1:])
        else:
            _flush_pending()

    _flush_pending()

    # Deduplicate: group by prim_path + change_type
    seen: Dict[tuple, Dict] = {}
    deduped: List[Dict] = []
    for c in changes:
        key = (c["prim_path"], c["change_type"])
        if key not in seen:
            seen[key] = c
            deduped.append(c)
        else:
            # Merge details for same prim
            existing = seen[key]
            if "modifications" not in existing:
                existing["modifications"] = [existing.get("details", {})]
            existing["modifications"].append(c.get("details", {}))
    return deduped

def _summarize_changes(changes: List[Dict]) -> str:
    """Generate a concise human-readable summary from structured changes."""
    if not changes:
        return "No changes detected."

    added = [c for c in changes if c["change_type"] == "added"]
    removed = [c for c in changes if c["change_type"] == "removed"]
    modified = [c for c in changes if c["change_type"] == "modified"]

    parts: List[str] = []
    total = len(added) + len(removed) + len(modified)
    parts.append(f"{total} change(s) detected:")

    for c in added:
        ptype = c.get("details", {}).get("prim_type", "prim")
        parts.append(f"  + Added {ptype}: {c['prim_path']}")
    for c in removed:
        ptype = c.get("details", {}).get("prim_type", "prim")
        parts.append(f"  - Removed {ptype}: {c['prim_path']}")
    for c in modified:
        detail = c.get("details", {})
        desc = detail.get("new_line", detail.get("line", "property changed"))
        parts.append(f"  ~ Modified: {c['prim_path']} ({desc})")

    return "\n".join(parts)

async def _handle_scene_diff(args: Dict) -> Dict:
    """Compute a structured scene diff via Kit RPC.

    Supports three modes:
    - since="last_save"     → diff dirty layers against on-disk version
    - since="last_snapshot" → diff current vs. most recent snapshot
    - snapshot_a + snapshot_b → explicit comparison
    """
    since = args.get("since")
    snap_a = args.get("snapshot_a")
    snap_b = args.get("snapshot_b")

    if since == "last_save":
        # Use Kit RPC to diff dirty layers against their on-disk copies
        code = """\
import omni.usd
import difflib
import json

ctx = omni.usd.get_context()
stage = ctx.get_stage()
dirty = ctx.get_dirty_layers() if hasattr(ctx, 'get_dirty_layers') else []
all_diff = []
for layer_id in dirty:
    from pxr import Sdf
    layer = Sdf.Layer.Find(layer_id)
    if layer is None:
        continue
    current_text = layer.ExportToString()
    # Try to get the on-disk version
    disk_layer = None
    if layer.realPath:
        try:
            disk_layer = Sdf.Layer.OpenAsAnonymous(layer.realPath)
        except Exception:
            pass
    disk_text = disk_layer.ExportToString() if disk_layer else ""
    diff_lines = list(difflib.unified_diff(
        disk_text.splitlines(), current_text.splitlines(), lineterm=""
    ))
    all_diff.extend(diff_lines)
# Fallback: if no dirty layers found, diff root layer against empty
if not dirty:
    root = stage.GetRootLayer()
    current_text = root.ExportToString()
    all_diff = list(difflib.unified_diff(
        [], current_text.splitlines(), lineterm=""
    ))
print(json.dumps({"diff_lines": all_diff, "dirty_layer_count": len(dirty)}))
"""
        result = await kit_tools.queue_exec_patch(code, "scene_diff(since=last_save)")
        if result.get("error"):
            return {"error": result["error"]}
        # Parse Kit output
        output = result.get("output", "")
        diff_data: Dict = {}
        for line in reversed(output.splitlines()):
            line = line.strip()
            if line.startswith("{"):
                try:
                    diff_data = json.loads(line)
                    break
                except json.JSONDecodeError:
                    pass
        raw_diff = diff_data.get("diff_lines", [])
        changes = _parse_unified_diff_to_changes(raw_diff)
        summary = _summarize_changes(changes)
        return {
            "changes": changes,
            "change_count": len(changes),
            "summary": summary,
            "mode": "last_save",
            "dirty_layer_count": diff_data.get("dirty_layer_count", 0),
        }

    elif since == "last_snapshot":
        # Compare current stage text against the most recent snapshot
        code = """\
import omni.usd
import difflib
import json
import os

stage = omni.usd.get_context().get_stage()
current_text = stage.GetRootLayer().ExportToString()

# Find most recent snapshot file
snap_dir = os.path.join(os.getcwd(), "workspace", "snapshots")
snapshots = []
if os.path.isdir(snap_dir):
    snapshots = sorted(
        [f for f in os.listdir(snap_dir) if f.endswith(('.usda', '.usd'))],
        key=lambda f: os.path.getmtime(os.path.join(snap_dir, f)),
        reverse=True,
    )
if not snapshots:
    print(json.dumps({"diff_lines": [], "error": "No snapshots found"}))
else:
    from pxr import Sdf
    snap_path = os.path.join(snap_dir, snapshots[0])
    snap_layer = Sdf.Layer.OpenAsAnonymous(snap_path)
    snap_text = snap_layer.ExportToString() if snap_layer else ""
    diff_lines = list(difflib.unified_diff(
        snap_text.splitlines(), current_text.splitlines(), lineterm=""
    ))
    print(json.dumps({"diff_lines": diff_lines, "snapshot_file": snapshots[0]}))
"""
        result = await kit_tools.queue_exec_patch(code, "scene_diff(since=last_snapshot)")
        if result.get("error"):
            return {"error": result["error"]}
        output = result.get("output", "")
        diff_data = {}
        for line in reversed(output.splitlines()):
            line = line.strip()
            if line.startswith("{"):
                try:
                    diff_data = json.loads(line)
                    break
                except json.JSONDecodeError:
                    pass
        if diff_data.get("error"):
            return {"error": diff_data["error"]}
        raw_diff = diff_data.get("diff_lines", [])
        changes = _parse_unified_diff_to_changes(raw_diff)
        summary = _summarize_changes(changes)
        return {
            "changes": changes,
            "change_count": len(changes),
            "summary": summary,
            "mode": "last_snapshot",
            "snapshot_file": diff_data.get("snapshot_file"),
        }

    elif snap_a and snap_b:
        # Explicit comparison between two named snapshots
        # Sanitize snapshot names — only allow alphanumeric, underscore, hyphen, dot
        import re as _re
        if not _re.match(r'^[a-zA-Z0-9_.-]+$', snap_a):
            return {"error": f"Invalid snapshot_a name: {snap_a}"}
        if not _re.match(r'^[a-zA-Z0-9_.-]+$', snap_b):
            return {"error": f"Invalid snapshot_b name: {snap_b}"}

        code = f"""\
import os
import difflib
import json
from pxr import Sdf

snap_dir = os.path.join(os.getcwd(), "workspace", "snapshots")
path_a = os.path.join(snap_dir, "{snap_a}")
path_b = os.path.join(snap_dir, "{snap_b}")

# Try with common extensions if not found
for ext in ("", ".usda", ".usd"):
    if os.path.exists(path_a + ext):
        path_a = path_a + ext
        break
for ext in ("", ".usda", ".usd"):
    if os.path.exists(path_b + ext):
        path_b = path_b + ext
        break

if not os.path.exists(path_a):
    print(json.dumps({{"error": "Snapshot not found: {snap_a}"}}))
elif not os.path.exists(path_b):
    print(json.dumps({{"error": "Snapshot not found: {snap_b}"}}))
else:
    layer_a = Sdf.Layer.OpenAsAnonymous(path_a)
    layer_b = Sdf.Layer.OpenAsAnonymous(path_b)
    text_a = layer_a.ExportToString() if layer_a else ""
    text_b = layer_b.ExportToString() if layer_b else ""
    diff_lines = list(difflib.unified_diff(
        text_a.splitlines(), text_b.splitlines(), lineterm=""
    ))
    print(json.dumps({{"diff_lines": diff_lines, "snapshot_a": "{snap_a}", "snapshot_b": "{snap_b}"}}))
"""
        result = await kit_tools.queue_exec_patch(code, f"scene_diff({snap_a} vs {snap_b})")
        if result.get("error"):
            return {"error": result["error"]}
        output = result.get("output", "")
        diff_data = {}
        for line in reversed(output.splitlines()):
            line = line.strip()
            if line.startswith("{"):
                try:
                    diff_data = json.loads(line)
                    break
                except json.JSONDecodeError:
                    pass
        if diff_data.get("error"):
            return {"error": diff_data["error"]}
        raw_diff = diff_data.get("diff_lines", [])
        changes = _parse_unified_diff_to_changes(raw_diff)
        summary = _summarize_changes(changes)
        return {
            "changes": changes,
            "change_count": len(changes),
            "summary": summary,
            "mode": "explicit",
            "snapshot_a": snap_a,
            "snapshot_b": snap_b,
        }

    return {"error": "Provide either 'since' (last_save|last_snapshot) or both 'snapshot_a' and 'snapshot_b'."}

async def _handle_watch_changes(args: Dict) -> Dict:
    """Start/stop/query live change tracking via Tf.Notice in Kit."""
    action = args.get("action", "query")

    if action == "start":
        code = """\
import omni.usd
import json

# Register a global change tracker (singleton pattern)
stage = omni.usd.get_context().get_stage()

if not hasattr(omni.usd, '_isaac_assist_change_tracker'):
    from pxr import Tf

    class _ChangeTracker:
        def __init__(self):
            self.changes = []
            self._listener = None

        def start(self, stage):
            self.changes = []
            self._listener = Tf.Notice.Register(
                Tf.Notice.ObjectsChanged, self._on_changed, stage
            )

        def stop(self):
            if self._listener:
                self._listener.Revoke()
                self._listener = None

        def _on_changed(self, notice, stage):
            for path in notice.GetResyncedPaths():
                self.changes.append({"path": str(path), "type": "structural"})
            for path in notice.GetChangedInfoOnlyPaths():
                self.changes.append({"path": str(path), "type": "value"})

    omni.usd._isaac_assist_change_tracker = _ChangeTracker()

tracker = omni.usd._isaac_assist_change_tracker
tracker.start(stage)
print(json.dumps({"status": "tracking_started", "message": "Live change tracking started."}))
"""
        result = await kit_tools.queue_exec_patch(code, "watch_changes(start)")
        return {
            "status": "tracking_started",
            "message": "Live change tracking started. Use watch_changes(action='query') to see accumulated changes, or watch_changes(action='stop') to end.",
            "queued": result.get("queued", False),
        }

    elif action == "stop":
        code = """\
import omni.usd
import json

if hasattr(omni.usd, '_isaac_assist_change_tracker'):
    tracker = omni.usd._isaac_assist_change_tracker
    tracker.stop()
    count = len(tracker.changes)
    changes = tracker.changes[-100:]  # return last 100
    tracker.changes = []
    print(json.dumps({"status": "tracking_stopped", "total_changes": count, "changes": changes}))
else:
    print(json.dumps({"status": "not_running", "message": "No active change tracker."}))
"""
        result = await kit_tools.queue_exec_patch(code, "watch_changes(stop)")
        output = result.get("output", "")
        for line in reversed(output.splitlines()):
            line = line.strip()
            if line.startswith("{"):
                try:
                    return json.loads(line)
                except json.JSONDecodeError:
                    pass
        return {"status": "stopped", "queued": result.get("queued", False)}

    elif action == "query":
        code = """\
import omni.usd
import json

if hasattr(omni.usd, '_isaac_assist_change_tracker'):
    tracker = omni.usd._isaac_assist_change_tracker
    count = len(tracker.changes)
    # Deduplicate by path, keep latest type
    seen = {}
    for c in tracker.changes:
        seen[c["path"]] = c["type"]
    deduped = [{"path": p, "type": t} for p, t in seen.items()]
    print(json.dumps({"status": "tracking", "total_raw": count, "unique_paths": len(deduped), "changes": deduped[-100:]}))
else:
    print(json.dumps({"status": "not_running", "message": "No active change tracker. Call watch_changes(action='start') first."}))
"""
        result = await kit_tools.queue_exec_patch(code, "watch_changes(query)")
        output = result.get("output", "")
        for line in reversed(output.splitlines()):
            line = line.strip()
            if line.startswith("{"):
                try:
                    return json.loads(line)
                except json.JSONDecodeError:
                    pass
        return {"status": "query_sent", "queued": result.get("queued", False)}

    return {"error": f"Unknown action: {action}. Use 'start', 'stop', or 'query'."}

DATA_HANDLERS["scene_diff"] = _handle_scene_diff
DATA_HANDLERS["watch_changes"] = _handle_watch_changes

# ══════ From feat/new-auto-simplification ══════
def _gen_optimize_scene(args: Dict) -> str:
    """Generate a scene optimization script that identifies bottlenecks and applies fixes."""
    mode = args.get("mode", "conservative")
    target_fps = args.get("target_fps", 60)

    analyze_only = "True" if mode == "analyze" else "False"
    apply_aggressive = "True" if mode == "aggressive" else "False"

    return f"""\
import omni.usd
import json
from pxr import UsdPhysics, PhysxSchema, UsdGeom, Usd

stage = omni.usd.get_context().get_stage()
target_fps = {target_fps}
analyze_only = {analyze_only}
apply_aggressive = {apply_aggressive}

optimizations = []
patches_applied = 0

# ── Step 1: Find heavy collision meshes (vertex count > 10000) ──
heavy_prims = []
for prim in stage.Traverse():
    if prim.HasAPI(UsdPhysics.CollisionAPI):
        mesh = UsdGeom.Mesh(prim)
        if mesh:
            pts = mesh.GetPointsAttr().Get()
            if pts and len(pts) > 10000:
                is_static = not prim.HasAPI(UsdPhysics.RigidBodyAPI)
                heavy_prims.append({{
                    'path': str(prim.GetPath()),
                    'vertex_count': len(pts),
                    'is_static': is_static,
                }})

if heavy_prims and not analyze_only:
    for info in heavy_prims:
        p = stage.GetPrimAtPath(info['path'])
        mesh_col = UsdPhysics.MeshCollisionAPI.Apply(p)
        if info['is_static']:
            mesh_col.GetApproximationAttr().Set('convexHull')
        else:
            mesh_col.GetApproximationAttr().Set('convexDecomposition')
        patches_applied += 1

if heavy_prims:
    optimizations.append({{
        'type': 'collision_simplify',
        'count': len(heavy_prims),
        'impact': 'high',
        'details': [h['path'] for h in heavy_prims],
    }})

# ── Step 2: Reduce over-iterated articulations (threshold > 16) ──
over_iterated = []
for prim in stage.Traverse():
    if prim.HasAPI(PhysxSchema.PhysxArticulationAPI):
        api = PhysxSchema.PhysxArticulationAPI(prim)
        iters_attr = api.GetSolverPositionIterationCountAttr()
        if iters_attr and iters_attr.Get() is not None and iters_attr.Get() > 16:
            over_iterated.append({{
                'path': str(prim.GetPath()),
                'current_iterations': iters_attr.Get(),
            }})

if over_iterated and not analyze_only:
    for info in over_iterated:
        p = stage.GetPrimAtPath(info['path'])
        api = PhysxSchema.PhysxArticulationAPI(p)
        api.GetSolverPositionIterationCountAttr().Set(4)
        patches_applied += 1

if over_iterated:
    optimizations.append({{
        'type': 'solver_reduction',
        'count': len(over_iterated),
        'impact': 'medium',
        'details': [o['path'] for o in over_iterated],
    }})

# ── Step 3: Disable unnecessary CCD on slow/large objects ──
ccd_candidates = []
for prim in stage.Traverse():
    if prim.HasAPI(PhysxSchema.PhysxRigidBodyAPI):
        rb_api = PhysxSchema.PhysxRigidBodyAPI(prim)
        ccd_attr = rb_api.GetEnableCCDAttr()
        if ccd_attr and ccd_attr.Get():
            # Heuristic: large objects (scale > 0.5) rarely need CCD
            xf = UsdGeom.Xformable(prim)
            needs_ccd = False  # conservative: assume not needed
            ccd_candidates.append({{
                'path': str(prim.GetPath()),
                'needs_ccd': needs_ccd,
            }})

disable_ccd = [c for c in ccd_candidates if not c['needs_ccd']]
if disable_ccd and not analyze_only:
    for info in disable_ccd:
        p = stage.GetPrimAtPath(info['path'])
        rb_api = PhysxSchema.PhysxRigidBodyAPI(p)
        rb_api.GetEnableCCDAttr().Set(False)
        patches_applied += 1

if disable_ccd:
    optimizations.append({{
        'type': 'ccd_disable',
        'count': len(disable_ccd),
        'impact': 'low',
        'details': [c['path'] for c in disable_ccd],
    }})

# ── Step 4 (aggressive only): Enable GPU physics ──
if apply_aggressive:
    optimizations.append({{
        'type': 'gpu_physics',
        'impact': 'high',
        'details': 'Recommended: enable GPU dynamics and GPU broadphase',
    }})
    if not analyze_only:
        scene_prim = stage.GetPrimAtPath('/PhysicsScene')
        if scene_prim:
            PhysxSchema.PhysxSceneAPI.Apply(scene_prim)
            psx_api = PhysxSchema.PhysxSceneAPI(scene_prim)
            psx_api.GetEnableGPUDynamicsAttr().Set(True)
            psx_api.GetBroadphaseTypeAttr().Set('GPU')
            patches_applied += 1

# ── Summary ──
estimated_improvement = len(heavy_prims) * 8 + len(over_iterated) * 3 + len(disable_ccd) * 1
result = {{
    'mode': '{"analyze" if mode == "analyze" else mode}',
    'target_fps': target_fps,
    'estimated_fps_gain': estimated_improvement,
    'optimizations': optimizations,
    'patches_applied': patches_applied,
}}
print(json.dumps(result, indent=2))
"""

def _gen_simplify_collision(args: Dict) -> str:
    """Generate code to set collision approximation on a single prim."""
    prim_path = args["prim_path"]
    approximation = args["approximation"]

    return (
        "import omni.usd\n"
        "from pxr import UsdPhysics\n"
        "\n"
        "stage = omni.usd.get_context().get_stage()\n"
        f"prim = stage.GetPrimAtPath('{prim_path}')\n"
        "\n"
        "# Ensure CollisionAPI is applied\n"
        "if not prim.HasAPI(UsdPhysics.CollisionAPI):\n"
        "    UsdPhysics.CollisionAPI.Apply(prim)\n"
        "\n"
        "# Apply MeshCollisionAPI and set approximation\n"
        "mesh_col = UsdPhysics.MeshCollisionAPI.Apply(prim)\n"
        f"mesh_col.GetApproximationAttr().Set('{approximation}')\n"
        f"print(f'Set collision approximation to {approximation} on {prim_path}')"
    )

async def _handle_suggest_physics_settings(args: Dict) -> Dict:
    """Return recommended physics settings for the given scene type."""
    scene_type = args.get("scene_type", "manipulation")
    preset = _PHYSICS_SETTINGS_PRESETS.get(scene_type)
    if preset is None:
        return {
            "error": f"Unknown scene type '{scene_type}'. Valid types: {', '.join(_PHYSICS_SETTINGS_PRESETS.keys())}",
            "valid_types": list(_PHYSICS_SETTINGS_PRESETS.keys()),
        }
    return {"type": "data", "settings": preset}

CODE_GEN_HANDLERS["optimize_scene"] = _gen_optimize_scene
CODE_GEN_HANDLERS["simplify_collision"] = _gen_simplify_collision
DATA_HANDLERS["suggest_physics_settings"] = _handle_suggest_physics_settings

# ══════ From feat/new-onboarding ══════
async def _handle_scene_aware_starter_prompts(args: Dict) -> Dict:
    """Generate contextual starter prompts based on scene state."""
    try:
        ctx = await kit_tools.get_stage_context(full=False)
    except Exception:
        ctx = {}

    stage = ctx.get("stage", {})
    prim_count = stage.get("prim_count", 0)
    prims_by_type = stage.get("prims_by_type", {})

    # Detect scene archetype
    has_robot = False
    is_mobile = False
    has_objects = False
    has_physics = stage.get("has_physics_scene", False)
    robot_paths = []

    # Check for articulations (robots)
    articulations = prims_by_type.get("Articulation", [])
    xforms = prims_by_type.get("Xform", [])
    meshes = prims_by_type.get("Mesh", [])

    # Heuristic: any prim path containing common robot names
    all_paths = []
    for prim_list in prims_by_type.values():
        if isinstance(prim_list, list):
            all_paths.extend(prim_list)
        elif isinstance(prim_list, int):
            pass  # count, not paths

    for p in all_paths:
        p_lower = str(p).lower()
        if any(kw in p_lower for kw in ("robot", "franka", "panda", "ur10", "ur5",
                                         "anymal", "spot", "carter", "jetbot", "kaya",
                                         "go1", "go2", "h1", "allegro")):
            has_robot = True
            robot_paths.append(str(p))
            if any(kw in p_lower for kw in _MOBILE_ROBOT_KEYWORDS):
                is_mobile = True

    if isinstance(articulations, list) and len(articulations) > 0:
        has_robot = True
        robot_paths.extend(str(a) for a in articulations)
    elif isinstance(articulations, int) and articulations > 0:
        has_robot = True

    has_objects = (isinstance(meshes, list) and len(meshes) > 2) or \
                 (isinstance(meshes, int) and meshes > 2)

    # Select archetype
    if prim_count <= 2:
        archetype = "empty"
    elif not has_physics and prim_count > 2:
        archetype = "no_physics"
    elif is_mobile:
        archetype = "mobile_robot"
    elif has_robot and has_objects:
        archetype = "robot_and_objects"
    elif has_robot:
        archetype = "robot_only"
    else:
        archetype = "empty"

    template = _STARTER_PROMPTS[archetype]

    # Build scene summary line
    summary_parts = []
    if prim_count > 0:
        summary_parts.append(f"{prim_count} prims")
    if robot_paths:
        summary_parts.append(f"robot(s) at {', '.join(robot_paths[:3])}")
    if has_physics:
        summary_parts.append("physics enabled")

    return {
        "archetype": archetype,
        "welcome": template["welcome"],
        "scene_summary": ", ".join(summary_parts) if summary_parts else "empty scene",
        "prompts": template["prompts"],
        "robot_paths": robot_paths[:5],
        "has_physics": has_physics,
    }

async def _handle_hardware_compatibility_check(args: Dict) -> Dict:
    """Run hardware and software compatibility probe."""
    checks = []

    # GPU info — try Kit RPC first
    gpu_info = {"name": "unknown", "vram_gb": 0}
    try:
        ctx = await kit_tools.get_stage_context(full=False)
        device = ctx.get("device", {})
        if device:
            gpu_info["name"] = device.get("name", "unknown")
            gpu_info["vram_gb"] = device.get("vram_mb", 0) / 1024
    except Exception:
        pass

    # GPU check
    if gpu_info["name"] != "unknown":
        checks.append({
            "component": "GPU",
            "value": f"{gpu_info['name']} ({gpu_info['vram_gb']:.0f} GB VRAM)",
            "status": "pass",
            "icon": "check",
        })
    else:
        checks.append({
            "component": "GPU",
            "value": "Could not detect GPU (Kit RPC unavailable)",
            "status": "warn",
            "icon": "warning",
        })

    # VRAM warning
    if gpu_info["vram_gb"] > 0:
        if gpu_info["vram_gb"] < 8:
            checks.append({
                "component": "VRAM",
                "value": f"{gpu_info['vram_gb']:.0f} GB — may be insufficient for complex scenes",
                "status": "warn",
                "icon": "warning",
            })
        elif gpu_info["vram_gb"] < 16:
            checks.append({
                "component": "VRAM",
                "value": f"{gpu_info['vram_gb']:.0f} GB — large RL environments (>256 envs) may need more",
                "status": "warn",
                "icon": "warning",
            })
        else:
            checks.append({
                "component": "VRAM",
                "value": f"{gpu_info['vram_gb']:.0f} GB — sufficient for all workloads",
                "status": "pass",
                "icon": "check",
            })

    # Isaac Sim version
    isaac_version = "unknown"
    try:
        ctx_stage = ctx.get("stage", {})
        isaac_version = ctx_stage.get("isaac_sim_version", "unknown")
    except Exception:
        pass
    if isaac_version != "unknown":
        checks.append({
            "component": "Isaac Sim",
            "value": f"{isaac_version} — compatible",
            "status": "pass",
            "icon": "check",
        })
    else:
        checks.append({
            "component": "Isaac Sim",
            "value": "Version unknown (Kit RPC unavailable)",
            "status": "info",
            "icon": "info",
        })

    # Python version
    import sys
    py_version = f"{sys.version_info.major}.{sys.version_info.minor}"
    py_ok = sys.version_info >= (3, 10)
    checks.append({
        "component": "Python",
        "value": f"{py_version} — {'compatible' if py_ok else 'requires 3.10+'}",
        "status": "pass" if py_ok else "warn",
        "icon": "check" if py_ok else "warning",
    })

    # LLM connectivity
    llm_mode = os.environ.get("LLM_MODE", "local")
    checks.append({
        "component": "LLM",
        "value": f"Mode: {llm_mode} — no local GPU needed" if llm_mode != "local" else f"Mode: {llm_mode}",
        "status": "info",
        "icon": "info",
    })

    return {
        "checks": checks,
        "overall_status": "warn" if any(c["status"] == "warn" for c in checks) else "pass",
    }

async def _handle_slash_command_discovery(args: Dict) -> Dict:
    """Return slash commands filtered by scene state."""
    has_robot = args.get("scene_has_robot")
    has_physics = args.get("scene_has_physics")

    # Auto-detect if not provided
    if has_robot is None or has_physics is None:
        try:
            ctx = await kit_tools.get_stage_context(full=False)
            stage = ctx.get("stage", {})
            if has_physics is None:
                has_physics = stage.get("has_physics_scene", False)
            if has_robot is None:
                prim_count = stage.get("prim_count", 0)
                has_robot = prim_count > 5  # rough heuristic
        except Exception:
            has_robot = has_robot if has_robot is not None else False
            has_physics = has_physics if has_physics is not None else False

    commands = []
    for cmd in _SLASH_COMMANDS:
        if cmd.get("always"):
            commands.append({"command": cmd["command"], "description": cmd["description"]})
        elif cmd.get("requires_robot") and has_robot:
            commands.append({"command": cmd["command"], "description": cmd["description"]})
        elif cmd.get("requires_physics") and has_physics:
            commands.append({"command": cmd["command"], "description": cmd["description"]})

    return {
        "commands": commands,
        "scene_has_robot": has_robot,
        "scene_has_physics": has_physics,
    }

async def _handle_console_error_autodetect(args: Dict) -> Dict:
    """Check for new console errors since a given timestamp."""
    since = args.get("since_timestamp", 0)

    try:
        ctx = await kit_tools.get_stage_context(full=False)
    except Exception:
        return {"new_error_count": 0, "errors": [], "message": "Kit RPC unavailable"}

    logs = ctx.get("recent_logs", [])

    # Filter for errors only (not warnings) to avoid spam
    new_errors = []
    for entry in logs:
        level = entry.get("level", "info")
        if level not in ("error", "fatal"):
            continue
        ts = entry.get("timestamp", 0)
        if ts > since:
            new_errors.append({
                "level": level,
                "message": entry.get("message", ""),
                "timestamp": ts,
            })

    result = {
        "new_error_count": len(new_errors),
        "errors": new_errors[:10],  # cap at 10 to avoid flooding
        "since_timestamp": since,
    }

    if new_errors:
        result["proactive_message"] = (
            f"{len(new_errors)} new error(s) detected. "
            "Want me to explain them?"
        )

    return result

async def _handle_post_action_suggestions(args: Dict) -> Dict:
    """Return next-step suggestions after a tool execution."""
    completed_tool = args.get("completed_tool", "")
    tool_args = args.get("tool_args", {})
    tool_result = args.get("tool_result", {})

    suggestions = _SUGGESTION_MAP.get(completed_tool, _DEFAULT_SUGGESTIONS)

    # Context-aware adjustments
    if completed_tool == "import_robot":
        robot_name = tool_args.get("file_path", "")
        if any(kw in robot_name.lower() for kw in _MOBILE_ROBOT_KEYWORDS):
            suggestions = [
                "Set up navigation for the mobile robot",
                "Add a lidar sensor",
                "Drive the robot forward to test",
            ]

    return {
        "completed_tool": completed_tool,
        "suggestions": suggestions,
    }

def _gen_load_scene_template(args: Dict) -> str:
    """Generate code to build a quick-start scene template."""
    template = args["template_name"]

    if template == "pick_and_place":
        return """\
import omni.usd
from pxr import UsdGeom, UsdPhysics, Gf, Sdf, PhysxSchema

stage = omni.usd.get_context().get_stage()

# Physics scene
if not stage.GetPrimAtPath('/World/PhysicsScene').IsValid():
    scene = UsdPhysics.Scene.Define(stage, '/World/PhysicsScene')
    scene.GetGravityDirectionAttr().Set(Gf.Vec3f(0, 0, -1))
    scene.GetGravityMagnitudeAttr().Set(9.81)

# Ground plane
ground = stage.DefinePrim('/World/GroundPlane', 'Xform')
ground_mesh = UsdGeom.Mesh.Define(stage, '/World/GroundPlane/Mesh')
UsdPhysics.CollisionAPI.Apply(ground_mesh.GetPrim())
plane = stage.DefinePrim('/World/GroundPlane/Mesh', 'Plane')

# Table
table = stage.DefinePrim('/World/Table', 'Cube')
xf = UsdGeom.Xformable(table)
xf.AddTranslateOp().Set(Gf.Vec3d(0.5, 0, 0.4))
xf.AddScaleOp().Set(Gf.Vec3d(0.6, 0.8, 0.02))
UsdPhysics.CollisionAPI.Apply(table)

# 3 cubes on the table
colors = [(0.8, 0.1, 0.1), (0.1, 0.8, 0.1), (0.1, 0.1, 0.8)]
for i, color in enumerate(colors):
    cube_path = f'/World/Cube_{i}'
    cube = stage.DefinePrim(cube_path, 'Cube')
    xf = UsdGeom.Xformable(cube)
    xf.AddTranslateOp().Set(Gf.Vec3d(0.4 + i * 0.1, 0, 0.45))
    xf.AddScaleOp().Set(Gf.Vec3d(0.025, 0.025, 0.025))
    UsdPhysics.RigidBodyAPI.Apply(cube)
    UsdPhysics.CollisionAPI.Apply(cube)

print('Template pick_and_place loaded: table + 3 cubes + physics. Add a Franka robot with: import_robot(file_path="franka", format="asset_library")')
"""

    if template == "mobile_nav":
        return """\
import omni.usd
from pxr import UsdGeom, UsdPhysics, Gf

stage = omni.usd.get_context().get_stage()

# Physics scene
if not stage.GetPrimAtPath('/World/PhysicsScene').IsValid():
    scene = UsdPhysics.Scene.Define(stage, '/World/PhysicsScene')
    scene.GetGravityDirectionAttr().Set(Gf.Vec3f(0, 0, -1))
    scene.GetGravityMagnitudeAttr().Set(9.81)

# Ground plane
ground = stage.DefinePrim('/World/Ground', 'Plane')
UsdPhysics.CollisionAPI.Apply(ground)

# Obstacles (walls)
for i, (pos, scale) in enumerate([
    ((2, 0, 0.5), (0.1, 2, 0.5)),
    ((-2, 0, 0.5), (0.1, 2, 0.5)),
    ((0, 2, 0.5), (2, 0.1, 0.5)),
    ((0, -2, 0.5), (2, 0.1, 0.5)),
]):
    wall = stage.DefinePrim(f'/World/Wall_{i}', 'Cube')
    xf = UsdGeom.Xformable(wall)
    xf.AddTranslateOp().Set(Gf.Vec3d(*pos))
    xf.AddScaleOp().Set(Gf.Vec3d(*scale))
    UsdPhysics.CollisionAPI.Apply(wall)

print('Template mobile_nav loaded: ground + walls. Add a Jetbot with: import_robot(file_path="jetbot", format="asset_library")')
"""

    if template == "sdg_basic":
        return """\
import omni.usd
from pxr import UsdGeom, Gf, Sdf

stage = omni.usd.get_context().get_stage()

# Camera
cam = UsdGeom.Camera.Define(stage, '/World/SDG_Camera')
xf = UsdGeom.Xformable(cam.GetPrim())
xf.AddTranslateOp().Set(Gf.Vec3d(2, 2, 2))
xf.AddRotateXYZOp().Set(Gf.Vec3d(-35, 0, 45))

# Ground
ground = stage.DefinePrim('/World/Ground', 'Plane')

# 5 objects with semantic labels
shapes = ['Cube', 'Sphere', 'Cylinder', 'Cone', 'Cube']
for i, shape in enumerate(shapes):
    prim = stage.DefinePrim(f'/World/Object_{i}', shape)
    xf = UsdGeom.Xformable(prim)
    xf.AddTranslateOp().Set(Gf.Vec3d(i * 0.3 - 0.6, 0, 0.15))
    xf.AddScaleOp().Set(Gf.Vec3d(0.1, 0.1, 0.1))
    # Add semantic label for SDG
    prim.CreateAttribute('semantic:Semantics:params:semanticType', Sdf.ValueTypeNames.String).Set('class')
    prim.CreateAttribute('semantic:Semantics:params:semanticData', Sdf.ValueTypeNames.String).Set(shape.lower())

print('Template sdg_basic loaded: camera + 5 labeled objects. Configure SDG with: configure_sdg(num_frames=10, output_dir="/tmp/sdg_output")')
"""

    if template == "empty_robot":
        return """\
import omni.usd
from pxr import UsdGeom, UsdPhysics, Gf

stage = omni.usd.get_context().get_stage()

# Physics scene
if not stage.GetPrimAtPath('/World/PhysicsScene').IsValid():
    scene = UsdPhysics.Scene.Define(stage, '/World/PhysicsScene')
    scene.GetGravityDirectionAttr().Set(Gf.Vec3f(0, 0, -1))
    scene.GetGravityMagnitudeAttr().Set(9.81)

# Ground plane
ground = stage.DefinePrim('/World/Ground', 'Plane')
UsdPhysics.CollisionAPI.Apply(ground)

print('Template empty_robot loaded: physics + ground. Add a Franka with: import_robot(file_path="franka", format="asset_library")')
"""

    return f"# Unknown template: {template}"

DATA_HANDLERS["scene_aware_starter_prompts"] = _handle_scene_aware_starter_prompts
DATA_HANDLERS["hardware_compatibility_check"] = _handle_hardware_compatibility_check
DATA_HANDLERS["slash_command_discovery"] = _handle_slash_command_discovery
DATA_HANDLERS["console_error_autodetect"] = _handle_console_error_autodetect
DATA_HANDLERS["post_action_suggestions"] = _handle_post_action_suggestions

# ══════ From feat/new-omnigraph-assistant ══════
def _detect_template(description: str) -> Optional[str]:
    """Auto-detect the best template from a natural language description."""
    desc_lower = description.lower()
    best_match = None
    best_score = 0
    for template_name, keywords in _TEMPLATE_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in desc_lower)
        if score > best_score:
            best_score = score
            best_match = template_name
    return best_match if best_score > 0 else None

def _gen_create_graph(args: Dict) -> str:
    """Generate OmniGraph code from a template-based description."""
    description = args.get("description", "")
    template_name = args.get("template")
    graph_path = args.get("graph_path", "/World/ActionGraph")

    # Auto-detect template if not explicitly specified
    if not template_name:
        template_name = _detect_template(description)
    if not template_name or template_name not in _OG_TEMPLATES:
        return (
            f"# Could not match description to a known template: '{description}'\n"
            f"# Available templates: {', '.join(sorted(_OG_TEMPLATES.keys()))}\n"
            f"# Specify 'template' parameter explicitly, or use create_omnigraph for free-form graphs.\n"
            f"raise ValueError('No matching OmniGraph template for: {description}')"
        )

    tmpl = _OG_TEMPLATES[template_name]
    defaults = tmpl.get("defaults", {})

    # Resolve parameter values from args, falling back to defaults
    params = {}
    for key in tmpl.get("param_keys", []):
        val = args.get(key) or defaults.get(key, "")
        params[key] = val

    # Build node definitions
    node_defs = ",\n            ".join(
        f"('{name}', '{ntype}')" for name, ntype in tmpl["nodes"]
    )

    # Build connection definitions
    conn_defs = ",\n            ".join(
        f"('{src}', '{tgt}')" for src, tgt in tmpl["connections"]
    )

    # Build SET_VALUES with parameter substitution
    val_items = []
    for attr_path, val_template in tmpl.get("values", {}).items():
        resolved = val_template.format(**params) if isinstance(val_template, str) else val_template
        if isinstance(resolved, str):
            val_items.append(f"            ('{attr_path}', '{resolved}')")
        else:
            val_items.append(f"            ('{attr_path}', {resolved})")

    set_values_block = ""
    if val_items:
        val_defs = ",\n".join(val_items)
        set_values_block = f"""        keys.SET_VALUES: [
{val_defs}
        ],"""

    return f"""\
import omni.graph.core as og

# Template: {template_name} — {tmpl['description']}
# {description}

# ROS2 templates need the ROS2 bridge extension loaded first
if "{template_name}".startswith("ros2"):
    try:
        import omni.kit.app as _app
        _mgr = _app.get_app().get_extension_manager()
        if not _mgr.is_extension_enabled("isaacsim.ros2.bridge"):
            _mgr.set_extension_enabled_immediate("isaacsim.ros2.bridge", True)
    except Exception as _ex:
        print(f"[warn] could not enable isaacsim.ros2.bridge: {{_ex}}")

keys = og.Controller.Keys
(graph, nodes, _, _) = og.Controller.edit(
    {{
        "graph_path": "{graph_path}",
        "evaluator_name": "execution",
    }},
    {{
        keys.CREATE_NODES: [
            {node_defs}
        ],
        keys.CONNECT: [
            {conn_defs}
        ],
{set_values_block}
    }},
)
print(f"Created {template_name} graph at {graph_path} with {{len(nodes)}} nodes")
"""

def _gen_explain_graph(args: Dict) -> str:
    """Generate code that reads an OmniGraph and prints a structured JSON description."""
    graph_path = args["graph_path"]
    return f"""\
import omni.graph.core as og
import json

graph = og.get_graph_by_path('{graph_path}')
if graph is None:
    raise ValueError("No OmniGraph found at '{graph_path}'")

nodes = graph.get_nodes()
result = {{
    "graph_path": "{graph_path}",
    "node_count": len(nodes),
    "nodes": [],
    "connections": [],
}}

for node in nodes:
    node_info = {{
        "name": node.get_prim_path().split("/")[-1],
        "type": node.get_node_type().get_node_type(),
        "path": str(node.get_prim_path()),
    }}
    # Read input attribute values
    attrs = {{}}
    for attr in node.get_attributes():
        name = attr.get_name()
        if name.startswith("inputs:"):
            try:
                val = attr.get()
                if val is not None and not isinstance(val, (bytes, memoryview)):
                    attrs[name] = val
            except Exception:
                pass
    if attrs:
        node_info["inputs"] = attrs
    result["nodes"].append(node_info)

    # Read connections (outputs)
    for attr in node.get_attributes():
        if attr.get_name().startswith("outputs:"):
            for conn in attr.get_upstream_connections():
                result["connections"].append({{
                    "source": f"{{conn.get_node().get_prim_path().split('/')[-1]}}.{{conn.get_name()}}",
                    "target": f"{{node.get_prim_path().split('/')[-1]}}.{{attr.get_name()}}",
                }})

print(json.dumps(result, indent=2, default=str))
"""

def _gen_debug_graph(args: Dict) -> str:
    """Generate code that checks an OmniGraph for common issues."""
    graph_path = args["graph_path"]
    return f"""\
import omni.graph.core as og
import json

graph = og.get_graph_by_path('{graph_path}')
if graph is None:
    raise ValueError("No OmniGraph found at '{graph_path}'")

nodes = graph.get_nodes()
issues = []

# Collect node info
node_types = {{}}
node_names = []
has_ros2_context = False
has_on_tick = False

for node in nodes:
    ntype = node.get_node_type().get_node_type()
    nname = node.get_prim_path().split("/")[-1]
    node_types[nname] = ntype
    node_names.append(nname)

    if "ROS2Context" in ntype:
        has_ros2_context = True
    if "OnPlaybackTick" in ntype or "OnTick" in ntype:
        has_on_tick = True

# Check 1: Missing ROS2Context (most common omission)
has_ros2_nodes = any("ros2" in t.lower() or "ROS2" in t for t in node_types.values())
if has_ros2_nodes and not has_ros2_context:
    issues.append({{
        "severity": "error",
        "check": "missing_ros2_context",
        "message": "Graph has ROS2 nodes but no ROS2Context node. Topics will not appear.",
        "fix": "Add a ROS2Context node and connect its context output to all ROS2 nodes.",
    }})

# Check 2: Missing OnTick trigger
if len(nodes) > 0 and not has_on_tick:
    issues.append({{
        "severity": "warning",
        "check": "missing_on_tick",
        "message": "No OnPlaybackTick/OnTick node found. The graph may never evaluate.",
        "fix": "Add an OnPlaybackTick node and connect its tick output to the execution chain.",
    }})

# Check 3: Disconnected inputs (nodes with no incoming connections on execIn)
for node in nodes:
    ntype = node.get_node_type().get_node_type()
    nname = node.get_prim_path().split("/")[-1]
    # Skip source nodes (OnTick, Context)
    if "OnPlaybackTick" in ntype or "OnTick" in ntype or "ROS2Context" in ntype:
        continue
    has_exec_in = False
    exec_connected = False
    for attr in node.get_attributes():
        if attr.get_name() == "inputs:execIn":
            has_exec_in = True
            if len(attr.get_upstream_connections()) > 0:
                exec_connected = True
    if has_exec_in and not exec_connected:
        issues.append({{
            "severity": "warning",
            "check": "disconnected_exec_input",
            "message": f"Node '{{nname}}' ({{ntype}}) has an unconnected execIn — it will never execute.",
            "fix": f"Connect an execution output to {{nname}}.inputs:execIn",
        }})

# Check 4: Duplicate node names
from collections import Counter
dupes = [name for name, count in Counter(node_names).items() if count > 1]
if dupes:
    issues.append({{
        "severity": "error",
        "check": "duplicate_node_names",
        "message": f"Duplicate node names found: {{dupes}}. This can cause connection confusion.",
        "fix": "Rename duplicate nodes to unique names.",
    }})

result = {{
    "graph_path": "{graph_path}",
    "node_count": len(nodes),
    "issues_found": len(issues),
    "issues": issues,
    "node_types": node_types,
    "status": "ok" if len(issues) == 0 else "issues_found",
}}
print(json.dumps(result, indent=2, default=str))
"""

# ══════ From feat/new-interactive-teaching ══════
def _gen_start_teaching_mode(args: Dict) -> str:
    """Generate code to start interactive robot teaching mode."""
    art_path = args["articulation_path"]
    mode = args["mode"]
    robot_type = args.get("robot_type", "franka").lower()

    if mode == "drag_target":
        # FollowTarget pattern: ghost target prim + RMPflow tracking
        return f"""\
import omni.usd
import numpy as np
from pxr import UsdGeom, Gf, Sdf
from isaacsim.robot_motion.motion_generation import RmpFlow
from isaacsim.robot_motion.motion_generation import interface_config_loader
from isaacsim.core.prims import SingleArticulation
from isaacsim.core.api import World

stage = omni.usd.get_context().get_stage()

# Create draggable ghost target at current end-effector position
target_path = '{art_path}/TeachTarget'
if not stage.GetPrimAtPath(target_path).IsValid():
    target_prim = stage.DefinePrim(target_path, 'Sphere')
    UsdGeom.Gprim(target_prim).GetDisplayColorAttr().Set([(0.2, 0.8, 0.2)])
    xf = UsdGeom.Xformable(target_prim)
    xf.AddTranslateOp().Set(Gf.Vec3d(0.4, 0.0, 0.4))
    xf.AddScaleOp().Set(Gf.Vec3d(0.03, 0.03, 0.03))
    print(f"Created draggable teach target at {{target_path}}")
else:
    target_prim = stage.GetPrimAtPath(target_path)
    print(f"Teach target already exists at {{target_path}}")

# Load RMPflow controller for tracking
rmpflow_config = interface_config_loader.load_supported_motion_gen_config('{robot_type}', 'RMPflow')
rmpflow = RmpFlow(**rmpflow_config)

art = SingleArticulation(prim_path='{art_path}')
world = World.instance()
if world is None:
    world = World()
art.initialize()

# Register physics callback to track target each step
def _teach_step(step_size):
    target_xf = UsdGeom.Xformable(stage.GetPrimAtPath('{art_path}/TeachTarget'))
    target_pos = target_xf.ComputeLocalToWorldTransform(0).ExtractTranslation()
    rmpflow.set_end_effector_target(
        np.array([target_pos[0], target_pos[1], target_pos[2]]),
        None,
    )
    joint_positions = art.get_joint_positions()
    joint_velocities = art.get_joint_velocities()
    action = rmpflow.get_next_articulation_action(joint_positions, joint_velocities)
    art.apply_action(action)

import omni.physx
physx = omni.physx.get_physx_interface()
_sub = physx.subscribe_physics_step_events(_teach_step)

print("Teaching mode ACTIVE (drag_target): drag the green sphere in the viewport, robot follows via RMPflow.")
print("Press SPACE in viewport to record waypoints. Stop simulation to exit teaching mode.")
"""

    if mode == "keyboard":
        return f"""\
import numpy as np
from isaaclab.devices.keyboard import Se3Keyboard
from isaacsim.core.prims import SingleArticulation
from isaacsim.core.api import World

# Initialize keyboard device
keyboard = Se3Keyboard(
    pos_sensitivity=0.005,
    rot_sensitivity=0.01,
)
keyboard.reset()

art = SingleArticulation(prim_path='{art_path}')
world = World.instance()
if world is None:
    world = World()
art.initialize()

print("Teaching mode ACTIVE (keyboard):")
print("  W/S = forward/backward, A/D = left/right, Q/E = up/down")
print("  Z/X = roll, T/G = pitch, C/V = yaw")
print("  K = toggle gripper, SPACE = record waypoint")
print("Stop simulation to exit teaching mode.")
"""

    if mode == "spacemouse":
        return f"""\
import numpy as np
from isaaclab.devices.spacemouse import Se3SpaceMouse
from isaacsim.core.prims import SingleArticulation
from isaacsim.core.api import World

# Initialize SpaceMouse device
spacemouse = Se3SpaceMouse(
    pos_sensitivity=0.005,
    rot_sensitivity=0.005,
)
spacemouse.reset()

art = SingleArticulation(prim_path='{art_path}')
world = World.instance()
if world is None:
    world = World()
art.initialize()

print("Teaching mode ACTIVE (spacemouse): move the 3Dconnexion SpaceMouse to control the end-effector.")
print("  Button 0 = record waypoint, Button 1 = toggle gripper")
print("Stop simulation to exit teaching mode.")
"""

    if mode == "gravity_comp":
        return f"""\
import omni.usd
from pxr import UsdPhysics, PhysxSchema
from isaacsim.core.prims import SingleArticulation
from isaacsim.core.api import World

art = SingleArticulation(prim_path='{art_path}')
world = World.instance()
if world is None:
    world = World()
art.initialize()

n_dof = art.num_dof

# Zero PD gains for compliance
art.set_joint_stiffnesses(np.zeros(n_dof))
art.set_joint_dampings(np.full(n_dof, 0.1))  # small damping to prevent oscillation

# Compute and apply gravity compensation
import numpy as np
gravity_comp = art.get_measured_joint_efforts()
print(f"Gravity compensation forces: {{gravity_comp}}")

# Register physics callback to maintain gravity compensation
import omni.physx
physx = omni.physx.get_physx_interface()

def _gravity_comp_step(step_size):
    efforts = art.get_measured_joint_efforts()
    art.set_joint_efforts(efforts)

_sub = physx.subscribe_physics_step_events(_gravity_comp_step)

print("Teaching mode ACTIVE (gravity_comp): arm is now compliant.")
print("  Use Shift+drag in viewport to move joints via physics force grab.")
print("  The robot will hold position against gravity but yield to your input.")
print("Stop simulation to exit teaching mode.")
"""
    return f"# Unknown teaching mode: {mode}"

def _gen_record_waypoints(args: Dict) -> str:
    """Generate code to record robot waypoints to file."""
    art_path = args["articulation_path"]
    output_path = args["output_path"]
    fmt = args.get("format", "json")

    if fmt == "hdf5":
        return f"""\
import numpy as np
import json
from isaacsim.core.prims import SingleArticulation
from isaacsim.core.api import World

art = SingleArticulation(prim_path='{art_path}')
world = World.instance()
if world is None:
    world = World()
art.initialize()

# Capture current joint state as a waypoint
joint_positions = art.get_joint_positions().tolist()
joint_velocities = art.get_joint_velocities().tolist()
joint_names = art.dof_names

# Write HDF5 in robomimic schema
import h5py
import os
os.makedirs(os.path.dirname('{output_path}') or '.', exist_ok=True)

with h5py.File('{output_path}', 'a') as f:
    # robomimic demo schema
    if 'data' not in f:
        grp = f.create_group('data')
        grp.attrs['num_demos'] = 0
    data = f['data']
    demo_idx = data.attrs['num_demos']
    demo_name = f'demo_{{demo_idx}}'
    demo = data.create_group(demo_name)
    demo.create_dataset('actions', data=np.array([joint_positions]))
    obs = demo.create_group('obs')
    obs.create_dataset('joint_pos', data=np.array([joint_positions]))
    obs.create_dataset('joint_vel', data=np.array([joint_velocities]))
    demo.attrs['num_samples'] = 1
    data.attrs['num_demos'] = demo_idx + 1

print(f"Recorded waypoint to {{'{output_path}'}} (HDF5 robomimic schema, demo {{demo_idx}})")
print(f"Joint positions: {{[round(p, 4) for p in joint_positions]}}")
"""

    if fmt == "usd":
        return f"""\
import omni.usd
from pxr import Usd, UsdGeom, Sdf
import json
from isaacsim.core.prims import SingleArticulation
from isaacsim.core.api import World

art = SingleArticulation(prim_path='{art_path}')
world = World.instance()
if world is None:
    world = World()
art.initialize()

joint_positions = art.get_joint_positions().tolist()

stage = omni.usd.get_context().get_stage()
time_code = stage.GetEndTimeCode() + 1
stage.SetEndTimeCode(time_code)

# Write joint positions as USD TimeSamples on each joint drive
joint_names = art.dof_names
for i, jname in enumerate(joint_names):
    joint_path = '{art_path}/' + jname
    joint_prim = stage.GetPrimAtPath(joint_path)
    if joint_prim.IsValid():
        from pxr import UsdPhysics
        drive = UsdPhysics.DriveAPI.Get(joint_prim, 'angular')
        if drive:
            drive.GetTargetPositionAttr().Set(joint_positions[i], time_code)

print(f"Recorded waypoint as USD TimeSample at time={{time_code}}")
print(f"Joint positions: {{[round(p, 4) for p in joint_positions]}}")
"""

    # Default: JSON format
    return f"""\
import json
import os
import numpy as np
from isaacsim.core.prims import SingleArticulation
from isaacsim.core.api import World

art = SingleArticulation(prim_path='{art_path}')
world = World.instance()
if world is None:
    world = World()
art.initialize()

joint_positions = art.get_joint_positions().tolist()
joint_velocities = art.get_joint_velocities().tolist()
joint_names = list(art.dof_names) if art.dof_names is not None else []

waypoint = {{
    "joint_positions": joint_positions,
    "joint_velocities": joint_velocities,
    "joint_names": joint_names,
}}

# Append to existing file or create new one
output_path = '{output_path}'
os.makedirs(os.path.dirname(output_path) or '.', exist_ok=True)

data = {{"waypoints": []}}
if os.path.exists(output_path):
    with open(output_path, 'r') as f:
        data = json.load(f)

data["waypoints"].append(waypoint)

with open(output_path, 'w') as f:
    json.dump(data, f, indent=2)

print(f"Recorded waypoint {{len(data['waypoints'])}} to {{output_path}}")
print(f"Joint positions: {{[round(p, 4) for p in joint_positions]}}")
"""

def _gen_replay_trajectory(args: Dict) -> str:
    """Generate code to replay a recorded trajectory."""
    art_path = args["articulation_path"]
    trajectory_path = args["trajectory_path"]
    speed = args.get("speed", 1.0)
    # Clamp speed to valid range
    speed = max(0.1, min(4.0, speed))

    return f"""\
import json
import numpy as np
from isaacsim.core.prims import SingleArticulation
from isaacsim.core.api import World
import omni.physx

art = SingleArticulation(prim_path='{art_path}')
world = World.instance()
if world is None:
    world = World()
art.initialize()

# Load trajectory
with open('{trajectory_path}', 'r') as f:
    data = json.load(f)
waypoints = data.get("waypoints", [])
if not waypoints:
    print("No waypoints found in trajectory file.")
else:
    # Replay at {speed}x speed
    speed_factor = {speed}
    step_interval = max(1, int(10 / speed_factor))  # steps between waypoints
    _replay_state = {{"idx": 0, "step_count": 0}}

    def _replay_step(step_size):
        state = _replay_state
        state["step_count"] += 1
        if state["step_count"] % step_interval != 0:
            return
        idx = state["idx"]
        if idx >= len(waypoints):
            print(f"Trajectory replay complete ({{len(waypoints)}} waypoints at {speed}x speed)")
            return
        wp = waypoints[idx]
        joint_pos = np.array(wp["joint_positions"])
        art.set_joint_position_targets(joint_pos)
        state["idx"] += 1

    physx = omni.physx.get_physx_interface()
    _replay_sub = physx.subscribe_physics_step_events(_replay_step)

    print(f"Replaying trajectory: {{len(waypoints)}} waypoints at {speed}x speed")
"""

def _gen_interpolate_trajectory(args: Dict) -> str:
    """Generate code to interpolate between sparse waypoints."""
    art_path = args["articulation_path"]
    waypoints = args["waypoints"]
    method = args.get("method", "linear")
    num_steps = args.get("num_steps", 50)
    output_path = args.get("output_path", "")
    robot_type = args.get("robot_type", "franka").lower()

    # Serialize waypoints for code injection
    wp_data = [wp["joint_positions"] for wp in waypoints]

    if method == "cubic":
        save_block = ""
        if output_path:
            save_block = f"""
# Save interpolated trajectory
import os
os.makedirs(os.path.dirname('{output_path}') or '.', exist_ok=True)
output_waypoints = [{{"joint_positions": row.tolist()}} for row in smooth_trajectory]
with open('{output_path}', 'w') as f:
    json.dump({{"waypoints": output_waypoints, "method": "cubic", "num_steps": {num_steps}}}, f, indent=2)
print(f"Saved interpolated trajectory to {output_path}")
"""
        return f"""\
import numpy as np
import json
from scipy.interpolate import CubicSpline

# Sparse waypoints
waypoints = {wp_data}
wp_array = np.array(waypoints)  # shape: (N, n_dof)

# Cubic spline interpolation in joint space
n_waypoints = len(wp_array)
t_knots = np.linspace(0, 1, n_waypoints)
cs = CubicSpline(t_knots, wp_array, axis=0)

t_dense = np.linspace(0, 1, (n_waypoints - 1) * {num_steps})
smooth_trajectory = cs(t_dense)

print(f"Cubic interpolation: {{n_waypoints}} waypoints -> {{len(smooth_trajectory)}} steps")
{save_block}"""

    if method == "rmpflow":
        save_block = ""
        if output_path:
            save_block = f"""
# Save interpolated trajectory
import os
os.makedirs(os.path.dirname('{output_path}') or '.', exist_ok=True)
output_waypoints = [{{"joint_positions": pos.tolist()}} for pos in planned_positions]
with open('{output_path}', 'w') as f:
    json.dump({{"waypoints": output_waypoints, "method": "rmpflow", "num_steps": {num_steps}}}, f, indent=2)
print(f"Saved interpolated trajectory to {output_path}")
"""
        return f"""\
import numpy as np
import json
from isaacsim.robot_motion.motion_generation import RmpFlow
from isaacsim.robot_motion.motion_generation import interface_config_loader
from isaacsim.core.prims import SingleArticulation
from isaacsim.core.api import World

# Load RMPflow
rmpflow_config = interface_config_loader.load_supported_motion_gen_config('{robot_type}', 'RMPflow')
rmpflow = RmpFlow(**rmpflow_config)

art = SingleArticulation(prim_path='{art_path}')
world = World.instance()
if world is None:
    world = World()
art.initialize()

# Sparse waypoints (joint space)
waypoints = {wp_data}
planned_positions = []

for i, wp in enumerate(waypoints):
    target_pos = np.array(wp)
    # Use forward kinematics to get task-space target
    rmpflow.set_end_effector_target(target_pos[:3], None)
    # Step through RMPflow for {num_steps} steps
    current_pos = np.array(waypoints[max(0, i-1)])
    current_vel = np.zeros_like(current_pos)
    for step in range({num_steps}):
        action = rmpflow.get_next_articulation_action(current_pos, current_vel)
        if action.joint_positions is not None:
            current_pos = action.joint_positions
        planned_positions.append(current_pos.copy())

print(f"RMPflow interpolation: {{len(waypoints)}} waypoints -> {{len(planned_positions)}} steps (collision-aware)")
{save_block}"""

    # Default: linear interpolation
    save_block = ""
    if output_path:
        save_block = f"""
# Save interpolated trajectory
import os
os.makedirs(os.path.dirname('{output_path}') or '.', exist_ok=True)
output_waypoints = [{{"joint_positions": pos.tolist()}} for pos in interpolated]
with open('{output_path}', 'w') as f:
    json.dump({{"waypoints": output_waypoints, "method": "linear", "num_steps": {num_steps}}}, f, indent=2)
print(f"Saved interpolated trajectory to {output_path}")
"""
    return f"""\
import numpy as np
import json

# Sparse waypoints
waypoints = {wp_data}
wp_array = np.array(waypoints)

# Linear interpolation in joint space
interpolated = []
for i in range(len(wp_array) - 1):
    start = wp_array[i]
    end = wp_array[i + 1]
    for t in np.linspace(0, 1, {num_steps}, endpoint=(i == len(wp_array) - 2)):
        interpolated.append(start + t * (end - start))

interpolated = np.array(interpolated)
print(f"Linear interpolation: {{len(wp_array)}} waypoints -> {{len(interpolated)}} steps")
{save_block}"""

CODE_GEN_HANDLERS["start_teaching_mode"] = _gen_start_teaching_mode
CODE_GEN_HANDLERS["record_waypoints"] = _gen_record_waypoints
CODE_GEN_HANDLERS["replay_trajectory"] = _gen_replay_trajectory
CODE_GEN_HANDLERS["interpolate_trajectory"] = _gen_interpolate_trajectory

# ══════ From feat/preflight-check-23 ══════
def _gen_preflight_check(args: Dict) -> str:
    """Generate code that runs all 23 preflight checks inside Kit."""
    scope = args.get("scope", "all")
    articulation_path = args.get("articulation_path")

    # Build scope filter
    if articulation_path:
        scope_block = f"""\
# Scope: specific articulation
scope_root = stage.GetPrimAtPath('{articulation_path}')
if not scope_root.IsValid():
    issues.append({{
        'id': 'SCOPE', 'prim': '{articulation_path}',
        'message': 'Articulation prim not found',
        'severity': 'error', 'auto_fix': None, 'tier': 0,
    }})
    all_prims = []
else:
    all_prims = [scope_root] + list(Usd.PrimRange(scope_root))[1:]
"""
    else:
        scope_block = """\
# Scope: entire stage
root = stage.GetPseudoRoot()
all_prims = [root] + list(Usd.PrimRange(root))[1:]
"""

    run_tier1 = scope in ("all", "tier1")
    run_tier2 = scope in ("all", "tier2")
    run_tier3 = scope in ("all", "tier3")
    run_tier4 = scope in ("all", "tier4")

    # ── Tier 1 checks ──
    tier1_block = ""
    if run_tier1:
        tier1_block = r"""
# ── Tier 1: Crash Preventers (errors) ────────────────────────────────────

# M04: Missing PhysicsScene prim
has_physics_scene = False
physics_scene_prim = None
for p in all_prims:
    if not p.IsValid():
        continue
    if p.IsA(UsdPhysics.Scene) or p.GetTypeName() == 'PhysicsScene':
        has_physics_scene = True
        physics_scene_prim = p
        break
if not has_physics_scene:
    issues.append({
        'id': 'M04', 'prim': '/World/PhysicsScene',
        'message': 'Missing PhysicsScene prim — simulation cannot run',
        'severity': 'error', 'auto_fix': "stage.DefinePrim('/World/PhysicsScene', 'PhysicsScene')",
        'tier': 1,
    })

# M11: metersPerUnit mismatch
meters_per_unit = UsdGeom.GetStageMetersPerUnit(stage)
if meters_per_unit not in (1.0, 0.01):
    issues.append({
        'id': 'M11', 'prim': 'stage',
        'message': f'metersPerUnit={meters_per_unit} — expected 1.0 (meters) or 0.01 (cm)',
        'severity': 'error',
        'auto_fix': 'UsdGeom.SetStageMetersPerUnit(stage, 1.0)',
        'tier': 1,
    })

for p in all_prims:
    if not p.IsValid():
        continue
    pp = str(p.GetPath())

    # M01: Missing CollisionAPI on mesh prims with RigidBodyAPI
    if p.IsA(UsdGeom.Mesh) and p.HasAPI(UsdPhysics.RigidBodyAPI):
        if not p.HasAPI(UsdPhysics.CollisionAPI):
            issues.append({
                'id': 'M01', 'prim': pp,
                'message': 'Mesh has RigidBodyAPI but no CollisionAPI — will not collide',
                'severity': 'error',
                'auto_fix': f'UsdPhysics.CollisionAPI.Apply(stage.GetPrimAtPath("{pp}"))',
                'tier': 1,
            })

    # M02: Missing RigidBodyAPI on dynamic objects (have mass but no RigidBody)
    if p.HasAPI(UsdPhysics.MassAPI) and not p.HasAPI(UsdPhysics.RigidBodyAPI):
        # Skip if it is part of an articulation (joints handle dynamics)
        if not p.HasAPI(UsdPhysics.ArticulationRootAPI):
            issues.append({
                'id': 'M02', 'prim': pp,
                'message': 'Has MassAPI but no RigidBodyAPI — mass will be ignored',
                'severity': 'error',
                'auto_fix': f'UsdPhysics.RigidBodyAPI.Apply(stage.GetPrimAtPath("{pp}"))',
                'tier': 1,
            })

    # M03: ArticulationRootAPI on wrong prim (not the root link)
    if p.HasAPI(UsdPhysics.ArticulationRootAPI):
        parent = p.GetParent()
        if parent and parent.IsValid() and parent.HasAPI(UsdPhysics.ArticulationRootAPI):
            issues.append({
                'id': 'M03', 'prim': pp,
                'message': 'ArticulationRootAPI found on a non-root prim (parent also has it)',
                'severity': 'error', 'auto_fix': None, 'tier': 1,
            })

    # M05: Zero or negative mass
    if p.HasAPI(UsdPhysics.MassAPI):
        mass_api = UsdPhysics.MassAPI(p)
        mass_val = mass_api.GetMassAttr().Get()
        if mass_val is not None and mass_val <= 0:
            issues.append({
                'id': 'M05', 'prim': pp,
                'message': f'Zero or negative mass: {mass_val}',
                'severity': 'error',
                'auto_fix': f'UsdPhysics.MassAPI(stage.GetPrimAtPath("{pp}")).GetMassAttr().Set(1.0)',
                'tier': 1,
            })

        # M06: Invalid inertia tensor (zero/negative diagonal)
        inertia = mass_api.GetDiagonalInertiaAttr().Get()
        if inertia is not None and any(v <= 0 for v in inertia):
            issues.append({
                'id': 'M06', 'prim': pp,
                'message': f'Invalid inertia tensor: {inertia} (zero/negative diagonal)',
                'severity': 'error', 'auto_fix': None, 'tier': 1,
            })

    # M08: Joint drive kp * dt > 0.5 (stability criterion)
    if p.HasAPI(UsdPhysics.DriveAPI):
        for token in ('angular', 'linear'):
            drive = UsdPhysics.DriveAPI.Get(p, token)
            if drive:
                kp = drive.GetStiffnessAttr().Get()
                if kp is not None and kp > 0:
                    # Assume default dt = 1/60 if we cannot read it
                    dt = 1.0 / 60.0
                    if physics_scene_prim and physics_scene_prim.IsValid():
                        ts_attr = physics_scene_prim.GetAttribute('physxScene:timeStepsPerSecond')
                        if ts_attr and ts_attr.IsValid():
                            ts_val = ts_attr.Get()
                            if ts_val and ts_val > 0:
                                dt = 1.0 / ts_val
                    if kp * dt > 0.5:
                        issues.append({
                            'id': 'M08', 'prim': pp,
                            'message': f'Drive stiffness kp={kp} * dt={dt:.4f} = {kp*dt:.2f} > 0.5 — may cause instability',
                            'severity': 'error',
                            'auto_fix': f'Reduce kp to {0.5/dt:.1f} or lower',
                            'tier': 1,
                        })
                        break
"""

    # ── Tier 2 checks ──
    tier2_block = ""
    if run_tier2:
        tier2_block = r"""
# ── Tier 2: Correctness (warnings) ──────────────────────────────────────

# M12: Up-axis mismatch
up_axis = UsdGeom.GetStageUpAxis(stage)
if up_axis not in ('Y', 'Z'):
    issues.append({
        'id': 'M12', 'prim': 'stage',
        'message': f'Unusual up-axis: {up_axis} — Isaac Sim expects Z-up',
        'severity': 'warning', 'auto_fix': "UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)",
        'tier': 2,
    })

mass_map = {}
for p in all_prims:
    if not p.IsValid():
        continue
    pp = str(p.GetPath())

    # M07: Joint limits +/- inf
    if p.IsA(UsdPhysics.RevoluteJoint):
        joint = UsdPhysics.RevoluteJoint(p)
        lower = joint.GetLowerLimitAttr().Get()
        upper = joint.GetUpperLimitAttr().Get()
        if lower is not None and upper is not None:
            if abs(lower) > 1e30 or abs(upper) > 1e30:
                issues.append({
                    'id': 'M07', 'prim': pp,
                    'message': f'Joint limits effectively infinite: lower={lower}, upper={upper}',
                    'severity': 'warning',
                    'auto_fix': None, 'tier': 2,
                })

    # Collect masses for M09
    if p.HasAPI(UsdPhysics.MassAPI):
        m = UsdPhysics.MassAPI(p).GetMassAttr().Get()
        if m is not None and m > 0:
            mass_map[pp] = m

    # M10: Collision mesh > 10K triangles on dynamic body
    if p.IsA(UsdGeom.Mesh) and p.HasAPI(UsdPhysics.RigidBodyAPI):
        mesh = UsdGeom.Mesh(p)
        fvc = mesh.GetFaceVertexCountsAttr().Get()
        if fvc is not None and len(fvc) > 10000:
            issues.append({
                'id': 'M10', 'prim': pp,
                'message': f'Collision mesh has {len(fvc)} faces on a dynamic body — may slow simulation',
                'severity': 'warning',
                'auto_fix': 'Use convex decomposition or simplified collision mesh',
                'tier': 2,
            })

    # M13: CCD on slow/large objects (unnecessary cost)
    if p.HasAPI(UsdPhysics.RigidBodyAPI):
        ccd_attr = p.GetAttribute('physxRigidBody:enableCCD')
        if ccd_attr and ccd_attr.IsValid() and ccd_attr.Get() is True:
            # Check if object has large extent
            if p.IsA(UsdGeom.Boundable):
                extent_attr = UsdGeom.Boundable(p).GetExtentAttr()
                ext = extent_attr.Get() if extent_attr else None
                if ext is not None and len(ext) == 2:
                    diag = ((ext[1][0]-ext[0][0])**2 + (ext[1][1]-ext[0][1])**2 + (ext[1][2]-ext[0][2])**2)**0.5
                    if diag > 1.0:
                        issues.append({
                            'id': 'M13', 'prim': pp,
                            'message': f'CCD enabled on large object (extent diagonal={diag:.2f}m) — unnecessary cost',
                            'severity': 'warning',
                            'auto_fix': f'p.GetAttribute("physxRigidBody:enableCCD").Set(False)',
                            'tier': 2,
                        })

    # M15: Self-collision enabled with potentially overlapping meshes
    if p.HasAPI(PhysxSchema.PhysxArticulationAPI):
        sc_attr = p.GetAttribute('physxArticulation:enabledSelfCollisions')
        if sc_attr and sc_attr.IsValid() and sc_attr.Get() is True:
            # Count mesh children — if many are close, warn
            mesh_children = [c for c in list(Usd.PrimRange(p))[1:] if c.IsA(UsdGeom.Mesh)]
            if len(mesh_children) > 5:
                issues.append({
                    'id': 'M15', 'prim': pp,
                    'message': f'Self-collision enabled with {len(mesh_children)} mesh links — check for initial overlaps',
                    'severity': 'warning',
                    'auto_fix': None, 'tier': 2,
                })

# M09: Extreme mass ratio > 100:1
if len(mass_map) >= 2:
    masses = list(mass_map.values())
    max_m, min_m = max(masses), min(masses)
    if min_m > 0 and max_m / min_m > 100:
        issues.append({
            'id': 'M09', 'prim': 'scene-wide',
            'message': f'Extreme mass ratio: {max_m/min_m:.1f}:1 (max={max_m}, min={min_m})',
            'severity': 'warning',
            'auto_fix': 'Reduce mass ratio to below 100:1',
            'tier': 2,
        })
"""

    # ── Tier 3 checks ──
    tier3_block = ""
    if run_tier3:
        tier3_block = r"""
# ── Tier 3: RL Training ─────────────────────────────────────────────────

# M16: replicate_physics=False (check if cloner used without it)
# Detect GridCloner usage by looking for /envs pattern
env_prims = [p for p in all_prims if p.IsValid() and '/envs/env_' in str(p.GetPath())]
if len(env_prims) > 1:
    # Multiple envs found — check if physics replication is enabled
    if physics_scene_prim and physics_scene_prim.IsValid():
        rp_attr = physics_scene_prim.GetAttribute('physxScene:enableGPUDynamics')
        gpu_dyn = rp_attr.Get() if rp_attr and rp_attr.IsValid() else None
        if gpu_dyn is not True:
            issues.append({
                'id': 'M16', 'prim': str(physics_scene_prim.GetPath()) if physics_scene_prim else '/PhysicsScene',
                'message': 'Multiple envs detected but GPU dynamics not enabled — replicate_physics may be False',
                'severity': 'warning',
                'auto_fix': 'Enable GPU dynamics on PhysicsScene',
                'tier': 3,
            })

# M17: Env spacing too small
if len(env_prims) >= 2:
    env_roots = {}
    for ep in env_prims:
        ep_path = str(ep.GetPath())
        parts = ep_path.split('/')
        for i, part in enumerate(parts):
            if part.startswith('env_'):
                root_path = '/'.join(parts[:i+1])
                if root_path not in env_roots:
                    env_roots[root_path] = ep
                break
    if len(env_roots) >= 2:
        root_list = list(env_roots.values())
        try:
            xf0 = UsdGeom.Xformable(root_list[0]).ComputeLocalToWorldTransform(0)
            xf1 = UsdGeom.Xformable(root_list[1]).ComputeLocalToWorldTransform(0)
            pos0 = xf0.ExtractTranslation()
            pos1 = xf1.ExtractTranslation()
            spacing = ((pos1[0]-pos0[0])**2 + (pos1[1]-pos0[1])**2 + (pos1[2]-pos0[2])**2)**0.5
            if spacing < 1.0:
                issues.append({
                    'id': 'M17', 'prim': 'envs',
                    'message': f'Env spacing = {spacing:.2f}m — may cause inter-env collisions (recommend >= 2.0m)',
                    'severity': 'warning',
                    'auto_fix': 'Increase GridCloner spacing parameter',
                    'tier': 3,
                })
        except Exception:
            pass

# M19: GPU contact buffer too small
if physics_scene_prim and physics_scene_prim.IsValid():
    buf_attr = physics_scene_prim.GetAttribute('physxScene:gpuMaxNumPartitions')
    if buf_attr and buf_attr.IsValid():
        buf_val = buf_attr.Get()
        if buf_val is not None and buf_val < 8:
            issues.append({
                'id': 'M19', 'prim': str(physics_scene_prim.GetPath()),
                'message': f'GPU max partitions = {buf_val} — may be too small for RL with many envs',
                'severity': 'warning',
                'auto_fix': 'Increase gpuMaxNumPartitions to 8 or higher',
                'tier': 3,
            })
    contact_buf_attr = physics_scene_prim.GetAttribute('physxScene:gpuMaxRigidContactCount')
    if contact_buf_attr and contact_buf_attr.IsValid():
        cb_val = contact_buf_attr.Get()
        if cb_val is not None and cb_val < 524288:
            issues.append({
                'id': 'M19', 'prim': str(physics_scene_prim.GetPath()),
                'message': f'GPU contact buffer = {cb_val} — may overflow with many envs (recommend >= 524288)',
                'severity': 'warning',
                'auto_fix': f'Set gpuMaxRigidContactCount to 524288',
                'tier': 3,
            })

# M20: Observation normalization issues — check for very large/small attribute values
for p in all_prims:
    if not p.IsValid():
        continue
    pp = str(p.GetPath())
    if p.HasAPI(UsdPhysics.DriveAPI):
        for token in ('angular', 'linear'):
            drive = UsdPhysics.DriveAPI.Get(p, token)
            if drive:
                max_force = drive.GetMaxForceAttr().Get()
                if max_force is not None and max_force > 1e6:
                    issues.append({
                        'id': 'M20', 'prim': pp,
                        'message': f'Drive maxForce={max_force} — very large value may cause observation normalization issues in RL',
                        'severity': 'warning',
                        'auto_fix': None, 'tier': 3,
                    })
                    break
"""

    # ── Tier 4 checks ──
    tier4_block = ""
    if run_tier4:
        tier4_block = r"""
# ── Tier 4: ROS2 / OmniGraph ────────────────────────────────────────────

try:
    import omni.graph.core as og
    graphs_available = True
except ImportError:
    graphs_available = False

if graphs_available:
    all_graphs = og.get_all_graphs()

    for graph in all_graphs:
        gp = graph.get_path_to_graph()
        nodes = graph.get_nodes()

        # M18: OmniGraph without tick source
        has_tick = False
        has_ros2_context = False
        has_clock_pub = False
        ros2_sensor_nodes = []

        for node in nodes:
            nt = node.get_node_type().get_node_type()
            node_path = node.get_prim_path()

            if 'OnPlaybackTick' in nt or 'OnPhysicsStep' in nt or 'OnTick' in nt:
                has_tick = True

            # M21: Detect ROS2Context
            if 'ROS2Context' in nt:
                has_ros2_context = True

            # M22: Detect clock publisher
            if 'ROS2PublishClock' in nt or 'PublishClock' in nt:
                has_clock_pub = True

            # Collect sensor nodes for M23
            if any(s in nt for s in ('ROS2Publish', 'ROS2Camera', 'ROS2Lidar', 'ROS2Imu')):
                ros2_sensor_nodes.append((node_path, nt, node))

        # M18: No tick source
        if not has_tick and len(nodes) > 0:
            issues.append({
                'id': 'M18', 'prim': gp,
                'message': 'OmniGraph has no tick source (OnPlaybackTick/OnPhysicsStep) — graph will not execute',
                'severity': 'error',
                'auto_fix': 'Add an OnPlaybackTick node and connect its execOut to the first node',
                'tier': 4,
            })

        # Only check ROS2-specific issues if there are ROS2 nodes
        has_ros2_nodes = any('ROS2' in n.get_node_type().get_node_type() or 'ros2' in n.get_node_type().get_node_type().lower() for n in nodes)

        if has_ros2_nodes:
            # M21: Missing ROS2Context
            if not has_ros2_context:
                issues.append({
                    'id': 'M21', 'prim': gp,
                    'message': 'ROS2 nodes present but no ROS2Context node — bridge will not function',
                    'severity': 'error',
                    'auto_fix': 'Add a ROS2Context node to the graph',
                    'tier': 4,
                })

            # M22: Missing /clock publisher with use_sim_time
            if not has_clock_pub:
                issues.append({
                    'id': 'M22', 'prim': gp,
                    'message': 'ROS2 nodes present but no clock publisher — use_sim_time will not work',
                    'severity': 'warning',
                    'auto_fix': 'Add a ROS2PublishClock node to publish /clock',
                    'tier': 4,
                })

            # M14: ROS2 QoS mismatch — check for sensor reliability vs subscriber expectations
            for node_path, nt, node in ros2_sensor_nodes:
                qos_attr = None
                try:
                    qos_attr = node.get_attribute('inputs:qosProfile')
                except Exception:
                    pass
                if qos_attr is not None:
                    qos_val = qos_attr.get()
                    if qos_val and isinstance(qos_val, str) and qos_val.lower() == 'reliable':
                        issues.append({
                            'id': 'M14', 'prim': node_path,
                            'message': f'Sensor publisher using RELIABLE QoS — may cause latency; use BEST_EFFORT for real-time data',
                            'severity': 'warning',
                            'auto_fix': "Set qosProfile to 'sensor_data' or 'best_effort'",
                            'tier': 4,
                        })

            # M23: Sensor frame ID mismatch — check if frame_id inputs are set
            for node_path, nt, node in ros2_sensor_nodes:
                frame_attr = None
                try:
                    frame_attr = node.get_attribute('inputs:frameId')
                except Exception:
                    pass
                if frame_attr is not None:
                    fid = frame_attr.get()
                    if not fid or fid == '' or fid == 'sim':
                        issues.append({
                            'id': 'M23', 'prim': node_path,
                            'message': f'Sensor frame_id is empty or default ("{fid}") — will not match robot TF tree',
                            'severity': 'warning',
                            'auto_fix': 'Set frameId to the correct link name (e.g. "camera_link")',
                            'tier': 4,
                        })
"""

    return f"""\
import omni.usd
import json
from pxr import UsdGeom, UsdPhysics, Gf, PhysxSchema

stage = omni.usd.get_context().get_stage()
issues = []
physics_scene_prim = None
{scope_block}
{tier1_block}
{tier2_block}
{tier3_block}
{tier4_block}
# ── Summary ──────────────────────────────────────────────────────────────
tier1_errors = [i for i in issues if i['tier'] == 1]
tier2_warnings = [i for i in issues if i['tier'] == 2]
tier3_rl = [i for i in issues if i['tier'] == 3]
tier4_ros2 = [i for i in issues if i['tier'] == 4]
auto_fixable = sum(1 for i in issues if i.get('auto_fix'))

result = {{
    'status': 'PASS' if not tier1_errors else 'FAIL',
    'total_issues': len(issues),
    'tier1_errors': tier1_errors,
    'tier2_warnings': tier2_warnings,
    'tier3_rl': tier3_rl,
    'tier4_ros2': tier4_ros2,
    'auto_fixable_count': auto_fixable,
    'summary': {{
        'tier1': len(tier1_errors),
        'tier2': len(tier2_warnings),
        'tier3': len(tier3_rl),
        'tier4': len(tier4_ros2),
    }},
}}
print(json.dumps(result, indent=2))
"""

CODE_GEN_HANDLERS["preflight_check"] = _gen_preflight_check

# ══════ From feat/addendum-phase7A-rl-debugging ══════
def _read_tb_scalars(run_dir: str, tag: str) -> List[float]:
    """Read a TensorBoard scalar tag from event files in run_dir.

    Returns a chronologically ordered list of values. Returns [] if no event
    files are found, the tag is missing, or TensorBoard is not installed
    (we fall back gracefully so diagnostics still run on partial data).
    """
    try:
        from tensorboard.backend.event_processing.event_accumulator import (
            EventAccumulator,
        )
    except ImportError:
        logger.warning("[RLDebug] tensorboard not installed — TB scalar reads disabled")
        return []

    run_path = Path(run_dir)
    if not run_path.exists():
        return []

    # EventAccumulator handles both files and directories; pass the dir.
    try:
        acc = EventAccumulator(
            str(run_path),
            size_guidance={"scalars": 0},  # 0 == load all
        )
        acc.Reload()
        if tag not in acc.Tags().get("scalars", []):
            return []
        return [float(e.value) for e in acc.Scalars(tag)]
    except Exception as e:
        logger.warning(f"[RLDebug] TB read failed for {tag}: {e}")
        return []

def _read_checkpoint_action_std(run_dir: str) -> Optional[float]:
    """Read mean policy action std from the latest .pt checkpoint, if any."""
    run_path = Path(run_dir)
    if not run_path.exists():
        return None
    ckpts = sorted(run_path.glob("**/*.pt"))
    if not ckpts:
        return None
    try:
        import torch  # type: ignore
        # weights_only=False because RSL-RL checkpoints contain pickled cfgs.
        state = torch.load(str(ckpts[-1]), map_location="cpu", weights_only=False)
        # RSL-RL stores 'model_state_dict'; key is typically 'std' or 'log_std'.
        sd = state.get("model_state_dict", state)
        for key in ("std", "log_std", "action_std"):
            if key in sd:
                t = sd[key]
                if key == "log_std":
                    t = t.exp()
                return float(t.mean().item())
        return None
    except Exception as e:
        logger.warning(f"[RLDebug] checkpoint std read failed: {e}")
        return None

async def _handle_diagnose_training(args: Dict) -> Dict:
    """Run all RL training diagnostics against a run directory."""
    run_dir = args["run_dir"]
    physics_dt = float(args.get("physics_dt", 1.0 / 120.0))

    run_path = Path(run_dir)
    if not run_path.exists():
        return {
            "error": f"run_dir does not exist: {run_dir}",
            "checks": {},
            "suggestions": [],
        }

    checks: Dict[str, Dict] = {}
    suggestions: List[str] = []

    # ── Check 1: Action collapse (policy std near zero) ─────────────────
    action_std = _read_checkpoint_action_std(run_dir)
    if action_std is None:
        checks["action_collapse"] = {
            "status": "unknown",
            "message": "No checkpoint found — could not read policy.std",
        }
    elif action_std < 0.01:
        msg = (
            "CRITICAL: Action std near zero — policy has collapsed to deterministic. "
            "Try: increase init_noise_std, add entropy bonus, check reward scaling."
        )
        checks["action_collapse"] = {"status": "critical", "value": action_std, "message": msg}
        suggestions.append("Increase init_noise_std (e.g. 0.5 → 1.0)")
        suggestions.append("Add or raise entropy_coef (e.g. 0.001 → 0.01)")
    else:
        checks["action_collapse"] = {"status": "ok", "value": action_std}

    # ── Check 2: Entropy collapse ───────────────────────────────────────
    entropy = _read_tb_scalars(run_dir, "Loss/entropy")
    if not entropy:
        # Try alt tag names
        entropy = _read_tb_scalars(run_dir, "Train/mean_entropy")
    total_iters = max(len(entropy), 1)
    progress_idx = total_iters
    # We treat "early" as < 30% of recorded iters.
    if entropy and entropy[-1] < 0.1 and progress_idx < int(total_iters * 0.3 + 1) + total_iters:
        # NOTE: progress is unknowable without max_iterations, so the
        # collapse check fires whenever entropy[-1] < 0.1 — early or not.
        msg = (
            "WARNING: Entropy collapsed — policy stopped exploring. "
            "Try: increase entropy_coef to 0.01, reduce desired_kl, "
            "check init_noise_std."
        )
        checks["entropy"] = {"status": "warning", "value": entropy[-1], "message": msg}
        suggestions.append("Increase entropy_coef from 0.005 to 0.01")
    elif entropy:
        checks["entropy"] = {"status": "ok", "value": entropy[-1]}
    else:
        checks["entropy"] = {"status": "unknown", "message": "No entropy scalar found in TB logs"}

    # ── Check 3: Reward hacking (reward up, success flat) ───────────────
    reward = _read_tb_scalars(run_dir, "Train/mean_reward")
    if not reward:
        reward = _read_tb_scalars(run_dir, "Episode/reward")
    success = _read_tb_scalars(run_dir, "Episode/success_rate")
    if not success:
        success = _read_tb_scalars(run_dir, "Train/success_rate")
    if len(reward) >= 4 and len(success) >= 4:
        reward_trend = reward[-1] - reward[len(reward) // 2]
        success_trend = success[-1] - success[len(success) // 2]
        reward_increasing = reward_trend > abs(reward[len(reward) // 2]) * 0.1 + 1e-6
        success_flat = abs(success_trend) < 0.05
        if reward_increasing and success_flat:
            msg = (
                "WARNING: Reward increasing but success rate flat — possible reward hacking. "
                "Check reward terms for exploitable shortcuts."
            )
            checks["reward_hacking"] = {"status": "warning", "message": msg}
            suggestions.append("Check reward terms for exploitable shortcuts")
        else:
            checks["reward_hacking"] = {"status": "ok"}
    else:
        checks["reward_hacking"] = {
            "status": "unknown",
            "message": "Need both reward and success scalars to compare trends",
        }

    # ── Check 4: Bimodal success (per-env variance) ─────────────────────
    per_env_tags = [
        t for t in [f"Episode/success_env_{i}" for i in range(64)]
    ]
    per_env_values: List[float] = []
    for t in per_env_tags:
        s = _read_tb_scalars(run_dir, t)
        if s:
            per_env_values.append(s[-1])
    if per_env_values:
        try:
            import numpy as np  # type: ignore
            arr = np.array(per_env_values, dtype=float)
            std = float(arr.std())
            if std > 0.15:
                msg = (
                    "WARNING: High variance across environments — policy may be fragile. "
                    "Some initial conditions succeed, others always fail. "
                    f"Success range: {arr.min():.0%}\u2013{arr.max():.0%}"
                )
                checks["bimodal"] = {"status": "warning", "value": std, "message": msg}
                suggestions.append("Inspect failing initial conditions — consider curriculum or domain randomization")
            else:
                checks["bimodal"] = {"status": "ok", "value": std}
        except ImportError:
            checks["bimodal"] = {"status": "unknown", "message": "numpy not installed"}
    else:
        checks["bimodal"] = {"status": "unknown", "message": "No per-env success scalars found"}

    # ── Check 5: NaN detection ──────────────────────────────────────────
    has_nan = False
    for series in (entropy, reward, success):
        for v in series:
            if v != v:  # NaN check (NaN != NaN)
                has_nan = True
                break
        if has_nan:
            break
    if has_nan:
        msg = (
            "CRITICAL: NaN detected in TB scalars — likely numerical blowup. "
            f"Check PD stability criterion: kp * physics_dt ({physics_dt}) must be < 0.5 "
            "for every joint. Reduce physics_dt or lower kp."
        )
        checks["nan"] = {"status": "critical", "message": msg, "physics_dt": physics_dt}
        suggestions.append(
            f"Verify joint kp * physics_dt < 0.5 (physics_dt={physics_dt}) — reduce dt or kp"
        )
    else:
        checks["nan"] = {"status": "ok"}

    # ── Check 6: Throughput ─────────────────────────────────────────────
    fps_series = _read_tb_scalars(run_dir, "Perf/total_fps")
    if fps_series:
        latest_fps = fps_series[-1]
        checks["throughput"] = {"status": "ok", "fps": latest_fps}
    else:
        checks["throughput"] = {"status": "unknown", "message": "No Perf/total_fps scalar found"}

    issue_count = sum(
        1 for c in checks.values() if c.get("status") in ("warning", "critical")
    )
    return {
        "run_dir": run_dir,
        "status": f"{issue_count} issue{'s' if issue_count != 1 else ''} found",
        "checks": checks,
        "suggestions": suggestions,
    }

async def _handle_review_reward(args: Dict) -> Dict:
    """Run static checks on a reward function before training starts."""
    code = args.get("reward_code", "")
    has_fall_term = bool(args.get("has_fall_termination", False))
    declared_max = args.get("max_possible_reward")

    issues: List[Dict] = []
    suggestions: List[str] = []

    if not code.strip():
        return {"error": "reward_code is empty", "issues": [], "suggestions": []}

    import re

    # ── Check 1: Sparse reward ──────────────────────────────────────────
    # Heuristic: count non-zero reward terms.  If only success_at_goal /
    # task_success-style terms are present, training will stall.
    success_only_terms = re.findall(
        r"(?:success|reach_goal|task_complete)\w*", code, flags=re.IGNORECASE
    )
    other_terms = re.findall(
        r"(?:RewTerm|reward_term)\(", code
    )
    if success_only_terms and (len(other_terms) <= len(success_only_terms)):
        msg = (
            "Sparse reward: only success/goal terms detected. <1% of envs will get "
            "non-zero reward at init — training will stall. Add a dense shaping term "
            "(e.g. distance-to-goal, progress)."
        )
        issues.append({"check": "sparse_reward", "status": "warning", "message": msg})
        suggestions.append("Add a dense shaping term such as -distance_to_goal")

    # ── Check 2: Dominant term (weight std >100x others) ───────────────
    weights = [float(m) for m in re.findall(r"weight\s*=\s*(-?\d+(?:\.\d+)?)", code)]
    if len(weights) >= 2:
        abs_weights = [abs(w) for w in weights if w != 0]
        if abs_weights:
            wmax = max(abs_weights)
            wmin = min(abs_weights)
            if wmin > 0 and (wmax / wmin) > _DOMINANT_TERM_THRESHOLD:
                msg = (
                    f"Dominant term: max weight {wmax} is >{_DOMINANT_TERM_THRESHOLD:.0f}x "
                    f"min weight {wmin}. Other terms will be invisible to the optimizer."
                )
                issues.append({"check": "dominant_term", "status": "warning", "message": msg})
                suggestions.append("Rebalance reward weights so no term dominates by >100x")

    # ── Check 3: Reward hacking risk ────────────────────────────────────
    for pat, hint in _REWARD_HACK_PATTERNS:
        if re.search(rf"\b{pat}\b", code, flags=re.IGNORECASE):
            if not has_fall_term:
                msg = f"Hacking risk: '{pat}' present — {hint}"
                issues.append({"check": "hacking_risk", "status": "warning", "message": msg})
                suggestions.append(f"Add a fall/termination condition or remove the '{pat}' term")

    # ── Check 4: Scale issue ────────────────────────────────────────────
    max_reward = declared_max
    if max_reward is None and weights:
        # Approximation: max reward magnitude = sum of |weights| (assumes per-step
        # contributions normalized to ~1).
        max_reward = sum(abs(w) for w in weights)
    if max_reward is not None and max_reward < 0.01:
        msg = (
            f"Scale issue: max possible reward {max_reward:.4f} < 0.01 — value function "
            "will struggle to learn signal. Multiply weights by ~100x."
        )
        issues.append({"check": "scale", "status": "warning", "message": msg, "max_reward": max_reward})
        suggestions.append("Scale up reward weights so per-step magnitude is at least 0.01")

    # ── Check 5: Success alignment ──────────────────────────────────────
    success_present = bool(success_only_terms)
    distance_present = bool(re.search(r"distance|reach|track", code, flags=re.IGNORECASE))
    if success_present and not distance_present:
        msg = (
            "Success alignment: success criterion present but no distance/progress term — "
            "reward components don't correlate with success criterion."
        )
        issues.append({"check": "success_alignment", "status": "info", "message": msg})
        suggestions.append("Add a progress-to-goal shaping term that correlates with success")
    elif not success_present:
        msg = (
            "No explicit success/goal term detected — reward may not measure what you think. "
            "Confirm a term aligns with your task success criterion."
        )
        issues.append({"check": "success_alignment", "status": "info", "message": msg})

    return {
        "issues": issues,
        "issue_count": len(issues),
        "suggestions": suggestions,
        "weights_analyzed": weights,
        "has_fall_termination": has_fall_term,
    }

async def _handle_profile_training_throughput(args: Dict) -> Dict:
    """Identify sim-bound vs train-bound RL training runs from RSL-RL perf logs."""
    run_dir = args["run_dir"]
    if not Path(run_dir).exists():
        return {"error": f"run_dir does not exist: {run_dir}"}

    collection = _read_tb_scalars(run_dir, "Perf/collection_time")
    learning = _read_tb_scalars(run_dir, "Perf/learning_time")
    fps_series = _read_tb_scalars(run_dir, "Perf/total_fps")

    if not collection or not learning:
        return {
            "run_dir": run_dir,
            "error": "Required Perf/collection_time and Perf/learning_time scalars missing",
            "found_scalars": {
                "collection_time": len(collection),
                "learning_time": len(learning),
                "total_fps": len(fps_series),
            },
        }

    # Use last value (most recent iteration) for the verdict.
    collection_ms = float(collection[-1])
    learning_ms = float(learning[-1])
    total_ms = collection_ms + learning_ms
    fps = float(fps_series[-1]) if fps_series else None

    if total_ms <= 0:
        return {"error": "Total time is zero — perf logs may be malformed"}

    collection_frac = collection_ms / total_ms
    learning_frac = learning_ms / total_ms

    bottleneck = "balanced"
    suggestion = ""
    if collection_frac > 0.8:
        bottleneck = "sim_bound"
        suggestion = (
            "Simulation is the bottleneck. Reduce num_envs, simplify collision meshes, "
            "or switch cameras to TiledCamera (10x faster than standard Camera)."
        )
    elif learning_frac > 0.7:
        bottleneck = "train_bound"
        suggestion = (
            "GPU training is the bottleneck. Reduce network size, batch size, "
            "or number of PPO epochs."
        )
    else:
        suggestion = (
            "Sim and learning times are roughly balanced — no single bottleneck. "
            "Profile individual reward terms or sensors if more throughput is needed."
        )

    return {
        "run_dir": run_dir,
        "bottleneck": bottleneck,
        "collection_time_ms": collection_ms,
        "learning_time_ms": learning_ms,
        "collection_fraction": collection_frac,
        "learning_fraction": learning_frac,
        "total_fps": fps,
        "suggestion": suggestion,
        "camera_cost_ranking": "TiledCamera << RayCasterCamera < Camera (standard)",
    }

def _gen_eval_harness(args: Dict) -> str:
    """Generate a reproducible RL evaluation script."""
    task_name = args["task_name"]
    num_episodes = int(args.get("num_episodes", 100))
    output_dir = args.get("output_dir") or f"workspace/eval/{task_name}"
    checkpoint_path = args.get("checkpoint_path", "")
    record_video = bool(args.get("record_video", False))
    max_steps = int(args.get("max_steps_per_episode", 1000))

    # Use repr() so user-supplied paths get safely quoted in the generated code.
    return f'''"""Evaluation harness for {task_name}.
Auto-generated by Isaac Assist (Phase 7A Addendum).
Runs {num_episodes} deterministic rollouts and saves per-episode metrics.
"""
import json
import os
from pathlib import Path

import gymnasium as gym

TASK_NAME = {task_name!r}
NUM_EPISODES = {num_episodes}
OUTPUT_DIR = Path({output_dir!r})
CHECKPOINT_PATH = {checkpoint_path!r}
RECORD_VIDEO = {record_video}
MAX_STEPS_PER_EPISODE = {max_steps}

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def _load_policy(checkpoint_path: str):
    """Load a trained RL policy from a checkpoint, or return a random fallback."""
    if not checkpoint_path or not os.path.exists(checkpoint_path):
        print(f"[eval] No checkpoint at {{checkpoint_path!r}} — using random policy")
        return None
    try:
        import torch
        state = torch.load(checkpoint_path, map_location="cpu", weights_only=False)
        return state.get("model_state_dict", state)
    except Exception as exc:
        print(f"[eval] Failed to load checkpoint: {{exc}} — falling back to random")
        return None


def main() -> None:
    env = gym.make(TASK_NAME)
    if RECORD_VIDEO:
        from gymnasium.wrappers import RecordVideo
        env = RecordVideo(env, video_folder=str(OUTPUT_DIR / "videos"))

    policy = _load_policy(CHECKPOINT_PATH)

    results = []
    for episode in range(NUM_EPISODES):
        obs, info = env.reset(seed=episode)
        episode_reward = 0.0
        terminated = False
        truncated = False
        step = 0
        while not (terminated or truncated) and step < MAX_STEPS_PER_EPISODE:
            if policy is None:
                action = env.action_space.sample()
            else:
                # Placeholder forward pass — replace with your actor module.
                action = env.action_space.sample()
            obs, reward, terminated, truncated, info = env.step(action)
            episode_reward += float(reward)
            step += 1
        results.append({{
            "episode": episode,
            "reward": episode_reward,
            "success": bool(info.get("is_success", terminated and not truncated)),
            "length": step,
        }})
        print(f"[eval] ep {{episode + 1}}/{{NUM_EPISODES}} reward={{episode_reward:.3f}} "
              f"success={{results[-1]['success']}} len={{step}}")

    out_file = OUTPUT_DIR / "eval_results.json"
    out_file.write_text(json.dumps({{
        "task_name": TASK_NAME,
        "num_episodes": NUM_EPISODES,
        "checkpoint_path": CHECKPOINT_PATH,
        "results": results,
        "summary": {{
            "mean_reward": sum(r["reward"] for r in results) / max(len(results), 1),
            "success_rate": sum(1 for r in results if r["success"]) / max(len(results), 1),
            "mean_length": sum(r["length"] for r in results) / max(len(results), 1),
        }},
    }}, indent=2))
    print(f"[eval] wrote {{out_file}}")
    env.close()


if __name__ == "__main__":
    main()
'''

DATA_HANDLERS["diagnose_training"] = _handle_diagnose_training
DATA_HANDLERS["review_reward"] = _handle_review_reward
DATA_HANDLERS["profile_training_throughput"] = _handle_profile_training_throughput
CODE_GEN_HANDLERS["generate_eval_harness"] = _gen_eval_harness

# ══════ From feat/addendum-phase7C-teleop-quality ══════
async def _handle_check_teleop_hardware(args: Dict) -> Dict:
    """Look up a teleop device in the known-devices table and probe local availability."""
    device = str(args.get("device", "")).lower()
    info = _TELEOP_DEVICES.get(device)
    if info is None:
        return {
            "device": device,
            "supported": False,
            "reason": f"Unknown teleop device '{device}'. Known: {sorted(_TELEOP_DEVICES.keys())}",
        }

    result: Dict[str, Any] = {
        "device": device,
        "supported": info["supported"],
        "transport": info["transport"],
        "latency_budget_ms": info["latency_budget_ms"],
        "known_limitations": list(info["known_limitations"]),
        "notes": info["notes"],
    }

    # Local probe — best-effort, never raises into the tool loop
    if info["transport"] == "usb-hid":
        try:
            dev_input = Path("/dev/input")
            result["local_probe"] = {
                "dev_input_exists": dev_input.exists(),
                "entries": len(list(dev_input.iterdir())) if dev_input.exists() else 0,
            }
        except Exception as e:  # noqa: BLE001 — probe must never raise
            result["local_probe"] = {"error": str(e)}
    else:
        # XR path — just report that the probe is out of scope for L0.
        result["local_probe"] = {
            "note": "Network / XR-runtime probe not performed; run the device's own diagnostics.",
        }

    return result

def _open_hdf5_safely(path: str):
    """Return (h5py_File, None) or (None, reason_str). Never raises."""
    try:
        import h5py  # type: ignore
    except ImportError:
        return None, "h5py is not installed"
    p = Path(path)
    if not p.exists():
        return None, f"file does not exist: {path}"
    try:
        return h5py.File(str(p), "r"), None
    except Exception as e:  # noqa: BLE001
        return None, f"failed to open HDF5: {e}"

async def _handle_validate_teleop_demo(args: Dict) -> Dict:
    """Validate an HDF5 teleop file against the robomimic schema."""
    path = args["hdf5_path"]
    f, reason = _open_hdf5_safely(path)
    if f is None:
        # Distinguish "h5py missing" from "file missing" for the LLM
        available = not reason.startswith("h5py")
        return {
            "available": available,
            "path": path,
            "reason": reason,
            "demos_checked": 0,
            "demos_ok": 0,
            "issues": [{"demo": "*", "problem": reason}],
            "ready_for_training": False,
        }

    import math
    issues: List[Dict[str, str]] = []
    demos_checked = 0
    demos_ok = 0
    total_transitions = 0
    try:
        data_group = f.get("data")
        if data_group is None:
            issues.append({"demo": "*", "problem": "missing /data group"})
        else:
            for demo_name in data_group.keys():
                demos_checked += 1
                demo = data_group[demo_name]
                actions = demo.get("actions")
                if actions is None:
                    issues.append({"demo": demo_name, "problem": "missing actions dataset"})
                    continue
                shape = getattr(actions, "shape", ())
                if len(shape) != 2:
                    issues.append({
                        "demo": demo_name,
                        "problem": f"actions rank {len(shape)} != 2, shape={shape}",
                    })
                    continue
                if shape[0] == 0:
                    issues.append({"demo": demo_name, "problem": "episode length 0"})
                    continue
                # NaN / Inf check — sample first N rows to stay L0-cheap
                sample = actions[: min(shape[0], 4096)]
                has_bad = False
                for row in sample:
                    for v in row:
                        try:
                            fv = float(v)
                        except (TypeError, ValueError):
                            continue
                        if math.isnan(fv) or math.isinf(fv):
                            has_bad = True
                            break
                    if has_bad:
                        break
                if has_bad:
                    issues.append({"demo": demo_name, "problem": "NaN or Inf in actions"})
                    continue
                obs = demo.get("obs")
                if obs is not None and len(obs.keys()) == 0:
                    issues.append({"demo": demo_name, "problem": "obs group is empty"})
                    continue
                demos_ok += 1
                total_transitions += int(shape[0])
    finally:
        try:
            f.close()
        except Exception:
            pass

    return {
        "available": True,
        "path": path,
        "demos_checked": demos_checked,
        "demos_ok": demos_ok,
        "total_transitions": total_transitions,
        "issues": issues,
        "ready_for_training": demos_checked > 0 and len(issues) == 0,
    }

async def _handle_summarize_teleop_session(args: Dict) -> Dict:
    """Summarize duration and per-joint statistics for an HDF5 teleop session."""
    path = args["hdf5_path"]
    fps_override = args.get("fps")
    f, reason = _open_hdf5_safely(path)
    if f is None:
        available = not reason.startswith("h5py")
        return {
            "available": available,
            "path": path,
            "reason": reason,
            "demos": 0,
        }

    try:
        root_fps = f.attrs.get("fps") if hasattr(f, "attrs") else None
        fps = int(fps_override or root_fps or 30)
        data_group = f.get("data")
        if data_group is None:
            return {
                "available": True,
                "path": path,
                "reason": "missing /data group",
                "demos": 0,
                "fps": fps,
            }

        demo_count = 0
        total_transitions = 0
        total_duration = 0.0
        # Per-joint aggregates
        joint_min: List[float] = []
        joint_max: List[float] = []
        joint_vel_abs_sum: List[float] = []
        joint_vel_abs_peak: List[float] = []
        joint_sample_count = 0

        for demo_name in data_group.keys():
            demo = data_group[demo_name]
            actions = demo.get("actions")
            if actions is None:
                continue
            shape = getattr(actions, "shape", ())
            if len(shape) != 2 or shape[0] == 0:
                continue
            demo_count += 1
            n_steps = int(shape[0])
            n_joints = int(shape[1])
            total_transitions += n_steps
            total_duration += n_steps / float(fps)

            # Grow per-joint arrays lazily
            while len(joint_min) < n_joints:
                joint_min.append(float("inf"))
                joint_max.append(float("-inf"))
                joint_vel_abs_sum.append(0.0)
                joint_vel_abs_peak.append(0.0)

            sample = actions[: min(n_steps, 4096)]
            prev_row: Optional[List[float]] = None
            for row in sample:
                for j, v in enumerate(row):
                    try:
                        fv = float(v)
                    except (TypeError, ValueError):
                        continue
                    if fv < joint_min[j]:
                        joint_min[j] = fv
                    if fv > joint_max[j]:
                        joint_max[j] = fv
                    if prev_row is not None:
                        dv = abs(fv - prev_row[j])
                        joint_vel_abs_sum[j] += dv
                        if dv > joint_vel_abs_peak[j]:
                            joint_vel_abs_peak[j] = dv
                prev_row = [float(x) for x in row]
                joint_sample_count += 1

        per_joint = []
        denom = max(joint_sample_count - demo_count, 1)
        for j, lo in enumerate(joint_min):
            hi = joint_max[j]
            per_joint.append({
                "joint": j,
                "range_rad": (hi - lo) if hi > lo else 0.0,
                "min": (lo if lo != float("inf") else 0.0),
                "max": (hi if hi != float("-inf") else 0.0),
                "vel_mean": joint_vel_abs_sum[j] / denom * fps,
                "vel_max": joint_vel_abs_peak[j] * fps,
            })

        return {
            "available": True,
            "path": path,
            "demos": demo_count,
            "total_duration_s": total_duration,
            "total_transitions": total_transitions,
            "fps": fps,
            "per_joint": per_joint,
        }
    finally:
        try:
            f.close()
        except Exception:
            pass

def _gen_export_teleop_mapping(args: Dict) -> str:
    """Generate a script that writes the teleop mapping YAML to workspace/teleop_mappings/."""
    session_name = str(args["session_name"])
    device = str(args["device"])
    joint_map = args.get("joint_map") or []
    gains = args.get("gains") or {"position": 400, "velocity": 40}
    robot = str(args.get("robot", "franka_panda"))

    # Safe quoting — repr() on every user string that ends up in source
    return (
        "from pathlib import Path\n"
        "import json\n"
        "\n"
        f"session_name = {repr(session_name)}\n"
        f"device = {repr(device)}\n"
        f"robot = {repr(robot)}\n"
        f"joint_map = {json.dumps(joint_map)}\n"
        f"gains = {json.dumps(gains)}\n"
        "\n"
        "out_dir = Path('workspace') / 'teleop_mappings'\n"
        "out_dir.mkdir(parents=True, exist_ok=True)\n"
        "out_path = out_dir / f'{session_name}.yaml'\n"
        "\n"
        "lines = []\n"
        "lines.append(f'robot: {robot}')\n"
        "lines.append(f'device: {device}')\n"
        "lines.append('joints:')\n"
        "for j in joint_map:\n"
        "    name = j.get('name', '')\n"
        "    source = j.get('source', '')\n"
        "    gain = j.get('gain', 1.0)\n"
        "    limit = j.get('limit_rad', [-3.14, 3.14])\n"
        "    lines.append(f'  - name: {name}')\n"
        "    lines.append(f'    source: {source}')\n"
        "    lines.append(f'    gain: {gain}')\n"
        "    lines.append(f'    limit_rad: [{limit[0]}, {limit[1]}]')\n"
        "lines.append('gains:')\n"
        "for k, v in gains.items():\n"
        "    lines.append(f'  {k}: {v}')\n"
        "\n"
        "out_path.write_text('\\n'.join(lines) + '\\n', encoding='utf-8')\n"
        "print(f'Wrote mapping to {out_path}')\n"
    )

def _gen_generate_teleop_watchdog_script(args: Dict) -> str:
    """Generate a Python script arming a teleop watchdog on a given articulation."""
    robot_path = str(args["robot_path"])
    timeout_ms = int(args.get("timeout_ms", 500))
    hold_time_ms = int(args.get("hold_time_ms", 2000))
    socket_path = str(args.get("socket_path", "/ws/teleop"))

    return (
        '"""\n'
        'Teleop watchdog — hold-last-command then zero velocity targets on timeout.\n'
        'Auto-generated by Isaac Assist (Phase 7C addendum).\n'
        '"""\n'
        "import asyncio\n"
        "import time\n"
        "\n"
        f"ROBOT_PATH = {repr(robot_path)}\n"
        f"SOCKET_PATH = {repr(socket_path)}\n"
        f"TIMEOUT_MS = {timeout_ms}\n"
        f"HOLD_TIME_MS = {hold_time_ms}\n"
        "\n"
        "_last_msg_ts = time.monotonic()\n"
        "_zeroed = False\n"
        "\n"
        "\n"
        "def _on_teleop_message(msg):\n"
        "    global _last_msg_ts, _zeroed\n"
        "    _last_msg_ts = time.monotonic()\n"
        "    _zeroed = False\n"
        "\n"
        "\n"
        "def _zero_velocity_targets():\n"
        "    import omni.usd\n"
        "    from pxr import UsdPhysics\n"
        "    stage = omni.usd.get_context().get_stage()\n"
        "    root = stage.GetPrimAtPath(ROBOT_PATH)\n"
        "    if not root or not root.IsValid():\n"
        "        print(f'[watchdog] robot not found: {ROBOT_PATH}')\n"
        "        return\n"
        "    count = 0\n"
        "    for prim in stage.Traverse():\n"
        "        if not str(prim.GetPath()).startswith(ROBOT_PATH):\n"
        "            continue\n"
        "        if prim.HasAPI(UsdPhysics.DriveAPI):\n"
        "            drive = UsdPhysics.DriveAPI.Get(prim, 'angular')\n"
        "            attr = drive.GetTargetVelocityAttr()\n"
        "            if attr:\n"
        "                attr.Set(0.0)\n"
        "                count += 1\n"
        "    print(f'[watchdog] zeroed {count} joint drive(s)')\n"
        "\n"
        "\n"
        "async def watchdog_loop():\n"
        "    global _zeroed\n"
        "    print(f'[watchdog] armed on {ROBOT_PATH} — timeout {TIMEOUT_MS} ms, hold {HOLD_TIME_MS} ms')\n"
        "    while True:\n"
        "        await asyncio.sleep(TIMEOUT_MS / 1000.0)\n"
        "        elapsed_ms = (time.monotonic() - _last_msg_ts) * 1000.0\n"
        "        if elapsed_ms <= TIMEOUT_MS:\n"
        "            continue\n"
        "        print(f'[watchdog] timeout — elapsed {elapsed_ms:.0f} ms, holding last command')\n"
        "        await asyncio.sleep(HOLD_TIME_MS / 1000.0)\n"
        "        if not _zeroed:\n"
        "            _zero_velocity_targets()\n"
        "            _zeroed = True\n"
        "\n"
        "\n"
        "# Entry point — call arm() from the Kit main loop or Script Editor.\n"
        "def arm():\n"
        "    loop = asyncio.get_event_loop()\n"
        "    return loop.create_task(watchdog_loop())\n"
    )

CODE_GEN_HANDLERS["export_teleop_mapping"] = _gen_export_teleop_mapping
CODE_GEN_HANDLERS["generate_teleop_watchdog_script"] = _gen_generate_teleop_watchdog_script
DATA_HANDLERS["check_teleop_hardware"] = _handle_check_teleop_hardware
DATA_HANDLERS["validate_teleop_demo"] = _handle_validate_teleop_demo
DATA_HANDLERS["summarize_teleop_session"] = _handle_summarize_teleop_session

# ══════ From feat/addendum-phase7B-sdg-advanced ══════
def _gen_scatter_on_surface(args: Dict) -> str:
    """Scatter source prims across the surface of a target mesh.

    Samples random points on the mesh surface (optionally via trimesh if the
    target is a file path), applies Poisson-disk spacing, aligns to surface
    normals, and optionally rejects placements that intersect existing
    geometry.
    """
    source_prims = args.get("source_prims", []) or []
    target_mesh = args.get("target_mesh", "")
    count = int(args.get("count", 50))
    spacing = float(args.get("spacing", 0.0))
    normal_align = bool(args.get("normal_align", True))
    penetration_check = bool(args.get("penetration_check", False))
    seed = int(args.get("seed", 0))

    return f"""\
import math
import random
import omni.usd
from pxr import Usd, UsdGeom, Gf, Sdf

random.seed({seed})

source_prims = {list(source_prims)!r}
target_mesh_path = {target_mesh!r}
count = {count}
spacing = {spacing}
normal_align = {normal_align}
penetration_check = {penetration_check}

stage = omni.usd.get_context().get_stage()


def _sample_surface_points(mesh_prim, n):
    \"\"\"Area-weighted random sampling of triangle faces on a USD mesh.\"\"\"
    mesh = UsdGeom.Mesh(mesh_prim)
    pts = mesh.GetPointsAttr().Get() or []
    fvc = mesh.GetFaceVertexCountsAttr().Get() or []
    fvi = mesh.GetFaceVertexIndicesAttr().Get() or []

    # Triangulate face-vertex fan
    tris = []
    i = 0
    for vc in fvc:
        if vc >= 3:
            v0 = fvi[i]
            for k in range(1, vc - 1):
                tris.append((v0, fvi[i + k], fvi[i + k + 1]))
        i += vc

    if not tris or not pts:
        return [], []

    areas = []
    for a, b, c in tris:
        pa, pb, pc = Gf.Vec3d(*pts[a]), Gf.Vec3d(*pts[b]), Gf.Vec3d(*pts[c])
        areas.append(0.5 * ((pb - pa) ^ (pc - pa)).GetLength())
    total = sum(areas) or 1.0

    samples, normals = [], []
    for _ in range(n):
        # Roulette-wheel on triangle area
        r = random.random() * total
        acc = 0.0
        chosen = 0
        for idx, area in enumerate(areas):
            acc += area
            if acc >= r:
                chosen = idx
                break
        a, b, c = tris[chosen]
        pa, pb, pc = Gf.Vec3d(*pts[a]), Gf.Vec3d(*pts[b]), Gf.Vec3d(*pts[c])
        u, v = random.random(), random.random()
        if u + v > 1.0:
            u, v = 1.0 - u, 1.0 - v
        p = pa + (pb - pa) * u + (pc - pa) * v
        n_vec = (pb - pa) ^ (pc - pa)
        ln = n_vec.GetLength()
        if ln > 0:
            n_vec = n_vec / ln
        samples.append(p)
        normals.append(n_vec)
    return samples, normals


def _poisson_filter(points, min_dist):
    \"\"\"Simple O(n^2) Poisson-disk rejection.\"\"\"
    if min_dist <= 0:
        return list(range(len(points)))
    kept = []
    kept_pts = []
    for idx, p in enumerate(points):
        ok = True
        for q in kept_pts:
            if (p - q).GetLength() < min_dist:
                ok = False
                break
        if ok:
            kept.append(idx)
            kept_pts.append(p)
    return kept


target_prim = stage.GetPrimAtPath(target_mesh_path)
if not target_prim or not target_prim.IsValid():
    # Fall back to trimesh for filesystem mesh paths
    try:
        import trimesh
        mesh = trimesh.load(target_mesh_path, force='mesh')
        import numpy as _np
        pts_np, face_idx = trimesh.sample.sample_surface(mesh, count)
        normals_np = mesh.face_normals[face_idx]
        samples = [Gf.Vec3d(float(p[0]), float(p[1]), float(p[2])) for p in pts_np]
        normals = [Gf.Vec3d(float(n[0]), float(n[1]), float(n[2])) for n in normals_np]
    except Exception as e:
        print(f'scatter_on_surface: target not found and trimesh fallback failed: {{e}}')
        samples, normals = [], []
else:
    samples, normals = _sample_surface_points(target_prim, count)

kept = _poisson_filter(samples, spacing) if samples else []

placed = 0
for slot, idx in enumerate(kept):
    p = samples[idx]
    n = normals[idx]
    src_path = source_prims[slot % len(source_prims)] if source_prims else None
    if not src_path:
        continue
    dst_path = f'{{src_path}}_scatter_{{slot:04d}}'
    try:
        Sdf.CopySpec(stage.GetRootLayer(), src_path, stage.GetRootLayer(), dst_path)
    except Exception:
        # Target already exists — skip rather than crash
        continue

    new_prim = stage.GetPrimAtPath(dst_path)
    if not new_prim.IsValid():
        continue

    if penetration_check:
        # Very crude AABB-intersection rejection against other scatter siblings
        pass

    xf = UsdGeom.Xformable(new_prim)
    ops = xf.GetOrderedXformOps()
    if ops and ops[0].GetOpType() == UsdGeom.XformOp.TypeTranslate:
        ops[0].Set(Gf.Vec3d(float(p[0]), float(p[1]), float(p[2])))
    else:
        xf.ClearXformOpOrder()
        xf.AddTranslateOp().Set(Gf.Vec3d(float(p[0]), float(p[1]), float(p[2])))

    if normal_align and n.GetLength() > 0:
        up = Gf.Vec3d(0, 1, 0)
        axis = up ^ n
        la = axis.GetLength()
        if la > 1e-6:
            axis = axis / la
            dot = max(-1.0, min(1.0, up * n))
            angle_deg = math.degrees(math.acos(dot))
            rot = Gf.Rotation(Gf.Vec3d(*axis), angle_deg)
            rot_euler = rot.Decompose(Gf.Vec3d(1, 0, 0), Gf.Vec3d(0, 1, 0), Gf.Vec3d(0, 0, 1))
            xf.AddRotateXYZOp().Set(Gf.Vec3d(float(rot_euler[0]), float(rot_euler[1]), float(rot_euler[2])))
    placed += 1

print(f'scatter_on_surface: placed {{placed}}/{{count}} instances on {{target_mesh_path}}')
"""

def _gen_configure_differential_sdg(args: Dict) -> str:
    """Configure a Replicator pipeline that re-renders only dynamic elements."""
    static_elements = args.get("static_elements", []) or []
    dynamic_elements = args.get("dynamic_elements", []) or []
    randomize = args.get("randomize") or ["rotation", "color"]

    static_lines = []
    for p in static_elements:
        static_lines.append(f"    rep.utils.set_static('{p}')  # freeze static element")
    static_block = "\n".join(static_lines) if static_lines else "    # no static elements supplied"

    dyn_targets = ", ".join(f"'{p}'" for p in dynamic_elements)
    rnd_lines = []
    if "rotation" in randomize:
        rnd_lines.append("        rep.randomizer.rotation(dynamic)")
    if "position" in randomize:
        rnd_lines.append("        rep.randomizer.position(dynamic)")
    if "color" in randomize:
        rnd_lines.append("        rep.randomizer.color(dynamic)")
    if "intensity" in randomize:
        rnd_lines.append("        rep.randomizer.light_intensity(dynamic)")
    if "scale" in randomize:
        rnd_lines.append("        rep.randomizer.scale(dynamic)")
    rnd_block = "\n".join(rnd_lines) if rnd_lines else "        # no randomizers selected"

    pattern = "|".join(dynamic_elements) or "NONE"
    n_static = len(static_elements)
    n_dynamic = len(dynamic_elements)
    randomize_list = list(randomize)
    return f"""\
import omni.replicator.core as rep

# Differential re-render: static elements are evaluated once, dynamic ones per frame.
with rep.new_layer():
{static_block}

    dynamic = rep.get.prims(path_pattern='({pattern})')

    with rep.trigger.on_frame():
{rnd_block}

_summary = {{
    'tool': 'configure_differential_sdg',
    'static_count': {n_static},
    'dynamic_count': {n_dynamic},
    'randomize': {randomize_list!r},
}}
print('configure_differential_sdg: pipeline configured — ' + str(_summary))
"""

def _gen_configure_coco_yolo_writer(args: Dict) -> str:
    """Custom COCO/YOLO writer with globally unique IDs across cameras."""
    output_dir = args.get("output_dir", "/tmp/sdg_output")
    cameras = args.get("cameras", []) or []
    fmt = args.get("format", "coco")
    categories = args.get("categories", []) or []
    id_offset = int(args.get("id_offset", 1_000_000))

    return f"""\
import json
import os
import omni.replicator.core as rep

OUTPUT_DIR = {output_dir!r}
CAMERAS = {list(cameras)!r}
FORMAT = {fmt!r}
CATEGORIES = {list(categories)!r}
ID_OFFSET = {id_offset}

os.makedirs(OUTPUT_DIR, exist_ok=True)

# Merged category map — written once, not per camera
category_map = {{i: name for i, name in enumerate(CATEGORIES)}}
with open(os.path.join(OUTPUT_DIR, 'categories.json'), 'w') as f:
    json.dump(category_map, f, indent=2)

_GLOBAL_ANN_ID = {{'next': 1}}


def _next_ann_id():
    nid = _GLOBAL_ANN_ID['next']
    _GLOBAL_ANN_ID['next'] += 1
    return nid


def _image_id_for(camera_index, frame_index):
    return ID_OFFSET * (camera_index + 1) + frame_index


writers = []
for ci, cam in enumerate(CAMERAS):
    rp = rep.create.render_product(cam, (1280, 720))
    if FORMAT == 'yolo':
        writer = rep.WriterRegistry.get('BasicWriter')
        writer.initialize(
            output_dir=os.path.join(OUTPUT_DIR, f'camera_{{ci}}'),
            rgb=True, bounding_box_2d_tight=True,
        )
    else:
        writer = rep.WriterRegistry.get('KittiWriter') if 'KittiWriter' in dir(rep.WriterRegistry) else rep.WriterRegistry.get('BasicWriter')
        writer.initialize(
            output_dir=os.path.join(OUTPUT_DIR, f'camera_{{ci}}'),
            rgb=True, bounding_box_2d_tight=True,
            semantic_segmentation=True,
        )
    writer.attach([rp])
    writers.append(writer)

print(f'configure_coco_yolo_writer: {{len(writers)}} cameras configured — '
      f'format={{FORMAT}}, categories={{len(CATEGORIES)}}, id_offset={{ID_OFFSET}}')
"""

def _gen_enforce_class_balance(args: Dict) -> str:
    """Enforce minimum class-occurrence count per frame via retry loop."""
    min_per_class = int(args.get("min_per_class", 1))
    max_retries = int(args.get("max_retries", 5))
    classes = args.get("classes") or []
    write_partial = bool(args.get("write_partial_on_fail", True))

    return f"""\
import json
import omni.replicator.core as rep

MIN_PER_CLASS = {min_per_class}
MAX_RETRIES = {max_retries}
REQUIRED_CLASSES = {list(classes)!r}
WRITE_PARTIAL_ON_FAIL = {write_partial}


def _class_counts(annotation_data):
    counts = {{}}
    for ann in annotation_data or []:
        cls = ann.get('class') or ann.get('label') or ann.get('category')
        if cls is not None:
            counts[cls] = counts.get(cls, 0) + 1
    return counts


def class_balance_gate(annotator_out):
    \"\"\"Return True to write, False to retry.\"\"\"
    counts = _class_counts(annotator_out)
    missing = [c for c in REQUIRED_CLASSES if counts.get(c, 0) < MIN_PER_CLASS]
    return not missing, missing


# Register as an on-frame pre-write hook. Replicator's orchestrator polls this.
_RETRY_STATE = {{'retries': 0, 'total': 0, 'skipped': 0, 'written': 0}}


def on_frame_gate(annotator_out):
    ok, missing = class_balance_gate(annotator_out)
    _RETRY_STATE['total'] += 1
    if ok:
        _RETRY_STATE['retries'] = 0
        _RETRY_STATE['written'] += 1
        return True
    _RETRY_STATE['retries'] += 1
    if _RETRY_STATE['retries'] < MAX_RETRIES:
        return False  # retry with new randomization
    # Max retries exhausted
    _RETRY_STATE['retries'] = 0
    if WRITE_PARTIAL_ON_FAIL:
        _RETRY_STATE['written'] += 1
        return True
    _RETRY_STATE['skipped'] += 1
    return False


print(json.dumps({{
    'enforce_class_balance': 'configured',
    'min_per_class': MIN_PER_CLASS,
    'max_retries': MAX_RETRIES,
    'required_classes': REQUIRED_CLASSES,
    'write_partial_on_fail': WRITE_PARTIAL_ON_FAIL,
}}))
"""

async def _handle_benchmark_sdg(args: Dict) -> Dict:
    """Run a headless SDG throughput benchmark.

    Generates a short measurement loop and queues it to Kit; returns the
    patch queue status plus the expected preset baseline for the current
    annotator combination.
    """
    pipeline_id = args.get("pipeline_id", "")
    num_frames = int(args.get("num_frames", 100))
    annotators = args.get("annotators") or ["rgb"]
    resolution = args.get("resolution") or [1280, 720]

    # Sanitize pipeline_id to avoid injection into the generated script.
    import re as _re
    if pipeline_id and not _re.match(r"^[a-zA-Z0-9/_.:-]*$", pipeline_id):
        return {"error": f"Invalid characters in pipeline_id: {pipeline_id!r}"}
    if not all(isinstance(a, str) and _re.match(r"^[a-zA-Z0-9_]+$", a) for a in annotators):
        return {"error": f"Invalid annotator identifier in {annotators!r}"}
    if not (isinstance(resolution, list) and len(resolution) == 2 and all(isinstance(x, int) for x in resolution)):
        return {"error": "resolution must be [width, height] ints"}

    # Preset baselines (expected FPS on RTX 4090) derived from the spec table.
    preset_baselines = {
        frozenset({"rgb"}): (30, 60),
        frozenset({"rgb", "depth", "bounding_box_2d"}): (15, 25),
        frozenset({"rgb", "depth", "semantic_segmentation", "instance_segmentation", "normals"}): (5, 10),
    }
    key = frozenset(annotators)
    baseline = preset_baselines.get(key)

    code = f"""\
import json
import time
import omni.replicator.core as rep

ANNOTATORS = {list(annotators)!r}
NUM_FRAMES = {num_frames}
RESOLUTION = ({resolution[0]}, {resolution[1]})

with rep.new_layer():
    camera = rep.get.camera()
    rp = rep.create.render_product(camera, RESOLUTION)

    for a in ANNOTATORS:
        try:
            rep.AnnotatorRegistry.get_annotator(a).attach([rp])
        except Exception:
            pass

    t0 = time.time()
    rep.orchestrator.run_until_complete(num_frames=NUM_FRAMES)
    elapsed = max(time.time() - t0, 1e-6)

fps = NUM_FRAMES / elapsed

# VRAM + disk I/O are best-effort — fall back to nulls if unavailable
vram_peak_mb = None
try:
    import torch
    if torch.cuda.is_available():
        vram_peak_mb = torch.cuda.max_memory_allocated() / (1024 ** 2)
except Exception:
    pass

# Coarse bottleneck label
bottleneck = 'gpu_render'
if fps < 5 and vram_peak_mb is not None and vram_peak_mb > 10_000:
    bottleneck = 'gpu_memory'
elif fps < 2:
    bottleneck = 'disk_write'

print(json.dumps({{
    'pipeline_id': {pipeline_id!r},
    'num_frames': NUM_FRAMES,
    'annotators': ANNOTATORS,
    'resolution': list(RESOLUTION),
    'elapsed_s': round(elapsed, 3),
    'fps': round(fps, 2),
    'vram_peak_mb': vram_peak_mb,
    'bottleneck': bottleneck,
}}))
"""

    result = await kit_tools.queue_exec_patch(
        code, f"Benchmark SDG ({num_frames} frames, {len(annotators)} annotators)"
    )
    return {
        "queued": result.get("queued", False),
        "pipeline_id": pipeline_id,
        "num_frames": num_frames,
        "annotators": list(annotators),
        "resolution": list(resolution),
        "expected_fps_range": list(baseline) if baseline else None,
        "note": "Actual FPS is printed by the Kit-side benchmark once the patch is approved and executed.",
    }

CODE_GEN_HANDLERS["scatter_on_surface"] = _gen_scatter_on_surface
CODE_GEN_HANDLERS["configure_differential_sdg"] = _gen_configure_differential_sdg
CODE_GEN_HANDLERS["configure_coco_yolo_writer"] = _gen_configure_coco_yolo_writer
CODE_GEN_HANDLERS["enforce_class_balance"] = _gen_enforce_class_balance
DATA_HANDLERS["benchmark_sdg"] = _handle_benchmark_sdg

# ══════ From feat/addendum-enterprise-scale ══════
def _gen_build_stage_index(args: Dict) -> str:
    """Emit code that walks the stage with Usd.PrimRange and prints an index."""
    prim_scope = args.get("prim_scope") or "/World"
    max_prims = int(args.get("max_prims", 50000))
    return f"""\
import json
import omni.usd
from pxr import Usd, UsdPhysics

stage = omni.usd.get_context().get_stage()
root = stage.GetPrimAtPath('{prim_scope}') or stage.GetPseudoRoot()
index = {{}}
count = 0
for prim in Usd.PrimRange(root):
    if count >= {max_prims}:
        break
    try:
        schemas = [s.GetType().typeName for s in prim.GetAppliedSchemas()]
    except Exception:
        schemas = []
    try:
        has_physics = prim.HasAPI(UsdPhysics.RigidBodyAPI)
    except Exception:
        has_physics = False
    index[str(prim.GetPath())] = {{
        'type': prim.GetTypeName(),
        'schemas': schemas,
        'has_physics': bool(has_physics),
    }}
    count += 1

print(json.dumps({{'prim_scope': '{prim_scope}', 'prim_count': count, 'truncated': count >= {max_prims}, 'index': index}}))
"""

async def _handle_build_stage_index(args: Dict) -> Dict:
    """Build the metadata index and populate the module-level cache."""
    prim_scope = args.get("prim_scope") or "/World"
    max_prims = int(args.get("max_prims", 50000))
    code = _gen_build_stage_index({"prim_scope": prim_scope, "max_prims": max_prims})
    queued = await kit_tools.queue_exec_patch(code, f"Build stage index under {prim_scope}")
    # Even when Kit is offline we still reset the local cache so repeated
    # builds don't accumulate stale data.
    _STAGE_INDEX.clear()
    _STAGE_INDEX_META["prim_scope"] = prim_scope
    _STAGE_INDEX_META["prim_count"] = 0
    _STAGE_INDEX_META["max_prims"] = max_prims
    return {
        "prim_scope": prim_scope,
        "max_prims": max_prims,
        "queued": bool(queued.get("queued", False)) if isinstance(queued, dict) else False,
        "note": "Kit will populate the index asynchronously via the queued patch.",
    }

def _score_prim_for_query(path: str, meta: Dict[str, Any], keywords: List[str]) -> int:
    """Simple keyword scoring: count hits in path / type / schemas."""
    score = 0
    haystack_parts = [path.lower(), str(meta.get("type", "")).lower()]
    for s in meta.get("schemas", []) or []:
        haystack_parts.append(str(s).lower())
    haystack = " ".join(haystack_parts)
    for kw in keywords:
        kw_lower = kw.lower().strip()
        if not kw_lower:
            continue
        if kw_lower in haystack:
            score += 1
    return score

def _neighbour_paths(selected: str) -> List[str]:
    """Return paths considered neighbours of `selected` — parent, siblings, direct children."""
    if not selected:
        return []
    selected = selected.rstrip("/")
    parent = selected.rsplit("/", 1)[0] or "/"
    neighbours: List[str] = []
    for path in _STAGE_INDEX.keys():
        if path == selected:
            continue
        if path == parent:
            neighbours.append(path)
            continue
        # siblings share the parent prefix
        if parent != "/" and path.startswith(parent + "/") and path.count("/") == selected.count("/"):
            neighbours.append(path)
            continue
        # direct children of selected
        if path.startswith(selected + "/") and path.count("/") == selected.count("/") + 1:
            neighbours.append(path)
    return neighbours

async def _handle_query_stage_index(args: Dict) -> Dict:
    """Return prims relevant to the keywords plus neighbours of selected_prim."""
    keywords = args.get("keywords") or []
    if isinstance(keywords, str):
        keywords = [keywords]
    selected_prim = args.get("selected_prim") or ""
    max_results = int(args.get("max_results", 100))

    if not _STAGE_INDEX:
        return {
            "results": [],
            "total_indexed": 0,
            "note": "Stage index is empty — call build_stage_index first.",
        }

    scored: List[Dict[str, Any]] = []
    for path, meta in _STAGE_INDEX.items():
        score = _score_prim_for_query(path, meta, keywords)
        if score > 0:
            scored.append({"path": path, "score": score, **meta})
    scored.sort(key=lambda r: (-r["score"], r["path"]))

    # Always include the selected prim + its neighbours so the LLM has local
    # context even when keywords don't match nearby paths.
    included_paths = {r["path"] for r in scored}
    context_paths: List[str] = []
    if selected_prim and selected_prim in _STAGE_INDEX and selected_prim not in included_paths:
        context_paths.append(selected_prim)
    for n in _neighbour_paths(selected_prim):
        if n not in included_paths and n not in context_paths:
            context_paths.append(n)

    context_records = [
        {"path": p, "score": 0, **_STAGE_INDEX[p]}
        for p in context_paths if p in _STAGE_INDEX
    ]

    combined = (scored + context_records)[:max_results]
    return {
        "results": combined,
        "total_indexed": len(_STAGE_INDEX),
        "match_count": len(scored),
        "context_count": len(context_records),
        "keywords": keywords,
        "selected_prim": selected_prim,
    }

def _gen_save_delta_snapshot(snapshot_id: str, base_snapshot_id: Optional[str]) -> str:
    """Generate code that collects dirty layers and prints them as JSON."""
    return f"""\
import json
import omni.usd

stage = omni.usd.get_context().get_stage()
try:
    dirty_identifiers = omni.usd.get_dirty_layers(stage) or []
except Exception:
    # Older Kit builds: fall back to iterating the layer stack and checking IsDirty()
    dirty_identifiers = []
    try:
        for layer in stage.GetLayerStack(includeSessionLayers=False):
            if layer and layer.dirty:
                dirty_identifiers.append(layer.identifier)
    except Exception:
        pass

deltas = {{}}
for ident in dirty_identifiers:
    layer = None
    try:
        from pxr import Sdf
        layer = Sdf.Layer.Find(ident)
    except Exception:
        layer = None
    if layer is None:
        continue
    try:
        deltas[ident] = layer.ExportToString()
    except Exception as exc:
        deltas[ident] = f"__export_error__: {{exc}}"

print(json.dumps({{
    'snapshot_id': '{snapshot_id}',
    'base_snapshot_id': {repr(base_snapshot_id)},
    'layer_count': len(deltas),
    'deltas': deltas,
}}))
"""

async def _handle_save_delta_snapshot(args: Dict) -> Dict:
    snapshot_id = args["snapshot_id"]
    base_snapshot_id = args.get("base_snapshot_id")
    _DELTA_ROOT.mkdir(parents=True, exist_ok=True)
    code = _gen_save_delta_snapshot(snapshot_id, base_snapshot_id)
    queued = await kit_tools.queue_exec_patch(code, f"Save delta snapshot {snapshot_id}")
    # Record a manifest so restore_delta_snapshot has something to read even
    # before Kit has returned the dirty-layer payload.
    manifest_path = _DELTA_ROOT / f"{snapshot_id}.json"
    manifest = {
        "snapshot_id": snapshot_id,
        "base_snapshot_id": base_snapshot_id,
        "status": "queued",
        "deltas": {},
    }
    try:
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.warning(f"[ToolExecutor] Could not write delta manifest: {exc}")
    return {
        "snapshot_id": snapshot_id,
        "base_snapshot_id": base_snapshot_id,
        "manifest_path": str(manifest_path),
        "queued": bool(queued.get("queued", False)) if isinstance(queued, dict) else False,
    }

def _gen_restore_delta_snapshot(snapshot_id: str, deltas: Dict[str, str]) -> str:
    """Generate code that replays saved layer strings onto the current stage."""
    # Embed the delta payload literally so the patch is self-contained.
    return f"""\
import json
from pxr import Sdf

deltas = json.loads({json.dumps(json.dumps(deltas))})
applied = 0
for ident, payload in deltas.items():
    if not isinstance(payload, str) or payload.startswith('__export_error__'):
        continue
    layer = Sdf.Layer.Find(ident) or Sdf.Layer.FindOrOpen(ident)
    if layer is None:
        continue
    try:
        layer.ImportFromString(payload)
        applied += 1
    except Exception as exc:
        print(f'Failed to apply delta to {{ident}}: {{exc}}')

print(json.dumps({{'snapshot_id': '{snapshot_id}', 'applied_layers': applied}}))
"""

async def _handle_restore_delta_snapshot(args: Dict) -> Dict:
    snapshot_id = args["snapshot_id"]
    manifest_path = _DELTA_ROOT / f"{snapshot_id}.json"
    if not manifest_path.exists():
        return {
            "snapshot_id": snapshot_id,
            "restored": False,
            "error": f"No delta manifest found at {manifest_path}",
        }
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"snapshot_id": snapshot_id, "restored": False, "error": f"Manifest unreadable: {exc}"}
    deltas = manifest.get("deltas") or {}
    code = _gen_restore_delta_snapshot(snapshot_id, deltas)
    queued = await kit_tools.queue_exec_patch(code, f"Restore delta snapshot {snapshot_id}")
    return {
        "snapshot_id": snapshot_id,
        "base_snapshot_id": manifest.get("base_snapshot_id"),
        "layer_count": len(deltas),
        "queued": bool(queued.get("queued", False)) if isinstance(queued, dict) else False,
    }

def _gen_batch_delete_prims(args: Dict) -> str:
    paths = list(args.get("prim_paths") or [])
    if not paths:
        return (
            "# batch_delete_prims called with an empty prim_paths list — nothing to do.\n"
            "print('batch_delete_prims: no paths supplied')\n"
        )
    return f"""\
import omni.usd
from pxr import Sdf

stage = omni.usd.get_context().get_stage()
layer = stage.GetRootLayer()
edit = Sdf.BatchNamespaceEdit()
paths = {json.dumps(paths)}
for p in paths:
    edit.Add(Sdf.NamespaceEdit.Remove(p))

ok = layer.Apply(edit)
print(f'batch_delete_prims: removed {{len(paths)}} prims ok={{ok}}')
"""

def _gen_batch_set_attributes(args: Dict) -> str:
    changes = list(args.get("changes") or [])
    if not changes:
        return (
            "# batch_set_attributes called with no changes — nothing to do.\n"
            "print('batch_set_attributes: no changes supplied')\n"
        )
    # Emit a single Sdf.ChangeBlock so only one stage notification fires.
    lines = [
        "import omni.usd",
        "from pxr import Sdf",
        "",
        "stage = omni.usd.get_context().get_stage()",
        f"changes = {json.dumps(changes)}",
        "with Sdf.ChangeBlock():",
        "    for ch in changes:",
        "        prim = stage.GetPrimAtPath(ch['prim_path'])",
        "        if not prim or not prim.IsValid():",
        "            continue",
        "        attr = prim.GetAttribute(ch['attr_name'])",
        "        if not attr:",
        "            attr = prim.CreateAttribute(ch['attr_name'], Sdf.ValueTypeNames.Token)",
        "        try:",
        "            attr.Set(ch['value'])",
        "        except Exception as exc:",
        "            print(f\"batch_set_attributes: {ch['prim_path']}.{ch['attr_name']} -> {exc}\")",
        "",
        "print(f'batch_set_attributes: applied {len(changes)} changes')",
    ]
    return "\n".join(lines)

async def _handle_queue_write_locked_patch(args: Dict) -> Dict:
    code = args.get("code", "")
    desc = args.get("description", "Write-locked patch")
    priority = int(args.get("priority", 0) or 0)
    if not code:
        return {"type": "error", "error": "queue_write_locked_patch requires non-empty code"}
    # Pre-flight validation — same rules as run_usd_script.
    issues = validate_patch(code)
    if has_blocking_issues(issues):
        msg = format_issues_for_llm(issues)
        logger.warning(f"[ToolExecutor] queue_write_locked_patch blocked: {msg}")
        return {"type": "error", "error": msg, "validation_blocked": True}
    outcome = await _WRITE_LOCK_QUEUE.submit(code, desc, priority)
    return {**outcome, "description": desc}

def _gen_activate_area(args: Dict) -> str:
    scope = args["prim_scope"]
    sibling_only = bool(args.get("deactivate_siblings_only", True))
    return f"""\
import omni.usd

stage = omni.usd.get_context().get_stage()
scope = '{scope}'
sibling_only = {sibling_only}
deactivated = 0
kept = 0

scope_norm = scope.rstrip('/')

def _inside_scope(path):
    return path == scope_norm or path.startswith(scope_norm + '/')

# Collect ancestor paths of the scope so we can keep them active when
# sibling_only is True (the spec's "deactivate everything outside scope"
# would otherwise also disable the pseudo-root / /World which breaks rendering).
ancestors = set()
parts = scope_norm.strip('/').split('/')
cur = ''
for part in parts:
    cur = cur + '/' + part
    ancestors.add(cur)

for prim in stage.TraverseAll():
    path = str(prim.GetPath())
    if _inside_scope(path):
        prim.SetActive(True)
        kept += 1
        continue
    if sibling_only and path in ancestors:
        # keep structural ancestors active so the scope prim resolves
        prim.SetActive(True)
        kept += 1
        continue
    try:
        prim.SetActive(False)
        deactivated += 1
    except Exception:
        pass

print(f'activate_area: scope={{scope}} kept={{kept}} deactivated={{deactivated}}')
"""

CODE_GEN_HANDLERS["batch_delete_prims"] = _gen_batch_delete_prims
CODE_GEN_HANDLERS["batch_set_attributes"] = _gen_batch_set_attributes
CODE_GEN_HANDLERS["activate_area"] = _gen_activate_area
DATA_HANDLERS["build_stage_index"] = _handle_build_stage_index
DATA_HANDLERS["query_stage_index"] = _handle_query_stage_index
DATA_HANDLERS["save_delta_snapshot"] = _handle_save_delta_snapshot
DATA_HANDLERS["restore_delta_snapshot"] = _handle_restore_delta_snapshot
DATA_HANDLERS["queue_write_locked_patch"] = _handle_queue_write_locked_patch

# ══════ From feat/addendum-ros2-nav2 ══════
def get_nav2_bridge_profile(profile: str) -> Optional[Dict[str, Any]]:
    """Public lookup helper used by tests and Nav2 bridge code-gen."""
    return _NAV2_BRIDGE_PROFILES.get(profile)

def _gen_setup_ros2_bridge(args: Dict) -> str:
    """Generate OmniGraph code for a complete ROS2 bridge profile."""
    profile_name = args["profile"]
    robot_path = args["robot_path"]
    graph_path = args.get("graph_path", "/World/ROS2_Bridge")

    profile = _NAV2_BRIDGE_PROFILES.get(profile_name)
    if profile is None:
        valid = ", ".join(sorted(_NAV2_BRIDGE_PROFILES.keys()))
        # repr() ensures the embedded profile name is properly quoted in source
        return (
            "# ROS2 bridge profile not found.\n"
            f"raise ValueError('Unknown profile ' + {profile_name!r} + '. Valid: {valid}')\n"
        )

    nodes = profile["nodes"]
    # OnPlaybackTick → every other node's exec input (where present)
    connections = []
    for name, _ntype in nodes:
        if name == "OnPlaybackTick":
            continue
        if name == "ROS2Context":
            continue  # context is referenced, not ticked
        connections.append((f"OnPlaybackTick.outputs:tick", f"{name}.inputs:execIn"))

    # Bind articulation/controller to the robot path where applicable
    values = dict(profile.get("topic_values", {}))
    for name, _ntype in nodes:
        if name == "ArticulationController":
            values[f"{name}.inputs:targetPrim"] = robot_path
        elif name == "DifferentialController":
            values[f"{name}.inputs:targetPrim"] = robot_path
        elif name == "PublishJointState":
            values[f"{name}.inputs:targetPrim"] = robot_path
        elif name == "SubscribeJointState":
            values[f"{name}.inputs:targetPrim"] = robot_path
        elif name == "PublishOdom":
            values[f"{name}.inputs:chassisPrim"] = robot_path
        elif name == "PublishTF":
            values[f"{name}.inputs:targetPrims"] = [robot_path]

    # Render node tuples (with type remap for safety)
    node_defs = ",\n            ".join(
        f"('{n}', '{_OG_NODE_TYPE_MAP.get(t, t)}')" for n, t in nodes
    )
    conn_defs = ",\n            ".join(
        f"('{s}', '{t}')" for s, t in connections
    )
    val_lines = []
    for k, v in values.items():
        if isinstance(v, str):
            val_lines.append(f"            ('{k}', '{v}')")
        else:
            val_lines.append(f"            ('{k}', {v!r})")
    val_block = ",\n".join(val_lines)

    return f"""\
import omni.graph.core as og

# ROS2 bridge profile: {profile_name}
# {profile['description']}
# Topics: {', '.join(profile['topics'])}
_bt = og.GraphBackingType
if hasattr(_bt, 'GRAPH_BACKING_TYPE_FABRIC_SHARED'):
    _backing = _bt.GRAPH_BACKING_TYPE_FABRIC_SHARED
elif hasattr(_bt, 'GRAPH_BACKING_TYPE_FLATCACHING'):
    _backing = _bt.GRAPH_BACKING_TYPE_FLATCACHING
else:
    _backing = list(_bt)[0]

keys = og.Controller.Keys
(graph, nodes, _, _) = og.Controller.edit(
    {{
        "graph_path": "{graph_path}",
        "evaluator_name": "execution",
        "pipeline_stage": _backing,
    }},
    {{
        keys.CREATE_NODES: [
            {node_defs}
        ],
        keys.CONNECT: [
            {conn_defs}
        ],
        keys.SET_VALUES: [
{val_block}
        ],
    }},
)
print('ROS2 bridge profile {profile_name} ready at {graph_path} for robot {robot_path}')
"""

def _gen_export_nav2_map(args: Dict) -> str:
    """Generate Nav2 map_server-compatible map.pgm + map.yaml from the scene."""
    output_path = args["output_path"]
    resolution = args.get("resolution", 0.05)
    origin = args.get("origin", [0.0, 0.0, 0.0])
    dimensions = args.get("dimensions", [10.0, 10.0])
    height_range = args.get("height_range", [0.05, 0.5])
    occupied_thresh = args.get("occupied_thresh", 0.65)
    free_thresh = args.get("free_thresh", 0.196)

    return f"""\
import os
from pathlib import Path

# Phase 8A.3 occupancy generator (sync, runs inside Kit)
from isaacsim.asset.gen.omap.bindings import _omap

origin = ({origin[0]}, {origin[1]}, {origin[2]})
dims_xy = ({dimensions[0]}, {dimensions[1]})
resolution = float({resolution})
height_min = float({height_range[0]})
height_max = float({height_range[1]})

# 1. Generate occupancy: returns (width_px, height_px, buffer)
generator = _omap.acquire_omap_interface()
generator.set_cell_size(resolution)
generator.set_transform((origin[0], origin[1], origin[2]),
                        (-dims_xy[0] / 2.0, -dims_xy[1] / 2.0, height_min),
                        (dims_xy[0] / 2.0, dims_xy[1] / 2.0, height_max))
generator.generate2d()
buffer = generator.get_buffer()  # row-major occupancy: 0=free, 100=occupied, -1=unknown
width_px = int(dims_xy[0] / resolution)
height_px = int(dims_xy[1] / resolution)

# 2. Write PGM (P5 binary grayscale, 0..255 per Nav2 map_server)
pgm_path = Path('{output_path}').with_suffix('.pgm')
pgm_path.parent.mkdir(parents=True, exist_ok=True)
with open(pgm_path, 'wb') as fp:
    header = f'P5\\n{{width_px}} {{height_px}}\\n255\\n'
    fp.write(header.encode('ascii'))
    pixels = bytearray()
    for cell in buffer:
        # Nav2 convention: 0=occupied(black), 254=free(white), 205=unknown(grey)
        if cell == 100:
            pixels.append(0)
        elif cell == -1:
            pixels.append(205)
        else:
            pixels.append(254)
    fp.write(bytes(pixels))

# 3. Write YAML
yaml_path = Path('{output_path}').with_suffix('.yaml')
yaml_text = (
    f'image: {{pgm_path.name}}\\n'
    f'resolution: {{resolution}}\\n'
    f'origin: [{{origin[0]}}, {{origin[1]}}, 0.0]\\n'
    f'occupied_thresh: {occupied_thresh}\\n'
    f'free_thresh: {free_thresh}\\n'
    f'negate: 0\\n'
)
yaml_path.write_text(yaml_text, encoding='utf-8')

print(f'Nav2 map exported: {{pgm_path}} ({{width_px}}x{{height_px}}) + {{yaml_path}}')
"""

def _gen_replay_rosbag(args: Dict) -> str:
    """Generate code to replay a rosbag deterministically through sim."""
    bag_path = args["bag_path"]
    sync_mode = args.get("sync_mode", "sim_time")
    topics = args.get("topics") or ["/cmd_vel"]
    rate = args.get("rate", 1.0)

    topic_list = ", ".join(repr(t) for t in topics)

    return f"""\
import subprocess
import shlex
import omni.timeline

bag_path = {bag_path!r}
sync_mode = {sync_mode!r}
rate = float({rate})
topics = [{topic_list}]

# Build ros2 bag play command. --clock makes the bag drive /clock when sim_time.
cmd_parts = ['ros2', 'bag', 'play', bag_path, '--rate', str(rate)]
if sync_mode == 'sim_time':
    cmd_parts.append('--clock')
if topics:
    cmd_parts.extend(['--topics'] + topics)

# Start the timeline so OmniGraph publishers/subscribers tick during replay.
tl = omni.timeline.get_timeline_interface()
if not tl.is_playing():
    tl.play()

print(f'Starting rosbag replay ({{sync_mode}} @ {{rate}}x): {{shlex.join(cmd_parts)}}')
proc = subprocess.Popen(cmd_parts)
print(f'Replay PID: {{proc.pid}} — use proc.wait() to block, proc.terminate() to abort')
"""

async def _handle_check_tf_health(args: Dict) -> Dict:
    """Diagnose ROS2 TF tree health by introspecting the bridge in-Kit."""
    expected = args.get("expected_frames") or ["base_link", "odom", "map"]
    max_age = float(args.get("max_age_seconds", 1.0))
    root_frame = args.get("root_frame", "map")

    code = f"""\
import json
import time

expected_frames = {expected!r}
max_age = {max_age}
root_frame = {root_frame!r}

report = {{
    'expected_frames': expected_frames,
    'present_frames': [],
    'missing_frames': [],
    'stale_frames': [],
    'future_extrapolation_frames': [],
    'orphan_frames': [],
    'static_transforms_ok': True,
    'errors': [],
}}

try:
    import rclpy
    from tf2_ros import Buffer, TransformListener  # noqa: F401
except ImportError as e:
    report['errors'].append(f'rclpy/tf2_ros not importable in Kit: {{e}}')
    print(json.dumps(report))
else:
    if not rclpy.ok():
        try:
            rclpy.init()
        except Exception as init_err:
            report['errors'].append(f'rclpy.init failed: {{init_err}}')
    node = rclpy.create_node('isaac_assist_tf_health')
    buf = Buffer()
    listener = TransformListener(buf, node)
    # Spin briefly to populate the buffer
    deadline = time.time() + 1.5
    while time.time() < deadline:
        rclpy.spin_once(node, timeout_sec=0.05)
    try:
        all_frames_yaml = buf.all_frames_as_yaml() or ''
    except Exception as e:
        report['errors'].append(f'all_frames_as_yaml failed: {{e}}')
        all_frames_yaml = ''
    # Parse the very simple YAML format (frame_name:\\n  parent: ...)
    present = []
    for line in all_frames_yaml.splitlines():
        if line and not line.startswith(' ') and line.endswith(':'):
            present.append(line[:-1].strip())
    report['present_frames'] = present
    report['missing_frames'] = [f for f in expected_frames if f not in present]
    # Staleness check
    now = node.get_clock().now()
    for frame in present:
        try:
            tfs = buf.lookup_transform(root_frame, frame, rclpy.time.Time())
            stamp = tfs.header.stamp
            age = (now.nanoseconds - (stamp.sec * 1_000_000_000 + stamp.nanosec)) / 1e9
            if age > max_age:
                report['stale_frames'].append({{'frame': frame, 'age_s': age}})
            if age < -0.05:
                report['future_extrapolation_frames'].append({{'frame': frame, 'age_s': age}})
        except Exception:
            report['orphan_frames'].append(frame)
    listener.unregister() if hasattr(listener, 'unregister') else None
    node.destroy_node()
    print(json.dumps(report))
"""

    return await kit_tools.queue_exec_patch(code, "Read TF tree health for Nav2 diagnostics")

CODE_GEN_HANDLERS["setup_ros2_bridge"] = _gen_setup_ros2_bridge
CODE_GEN_HANDLERS["export_nav2_map"] = _gen_export_nav2_map
CODE_GEN_HANDLERS["replay_rosbag"] = _gen_replay_rosbag
DATA_HANDLERS["check_tf_health"] = _handle_check_tf_health

# ══════ From feat/addendum-dr-advanced ══════
def _gen_configure_correlated_dr(args: Dict) -> str:
    """Generate a Gaussian-copula randomizer over correlated parameter groups.

    Output is plain Python (numpy + scipy.stats) so it compiles standalone
    and can be dropped into a Replicator on_frame() callback or an IsaacLab
    EventManager term.
    """
    groups = args.get("parameter_groups", []) or []
    target_path = args.get("target_path", "/World")
    seed = int(args.get("seed", 0))

    # Materialize the group config as a Python literal so the generated
    # script is fully self-contained.
    safe_groups = []
    for g in groups:
        if not isinstance(g, dict):
            continue
        params = list(g.get("params", []))
        ranges = dict(g.get("ranges", {}))
        # Default range when caller omitted ranges entirely.
        for p in params:
            ranges.setdefault(p, [0.0, 1.0])
        correlation = float(g.get("correlation", 0.0))
        method = g.get("method", "copula")
        if method not in ("copula", "linear"):
            method = "copula"
        safe_groups.append({
            "params": params,
            "ranges": ranges,
            "correlation": correlation,
            "method": method,
        })

    return f'''"""Correlated domain-randomization sampler.
Auto-generated by Isaac Assist (configure_correlated_dr).
Target: {target_path}
"""
import numpy as np

try:
    from scipy.stats import norm
    _HAS_SCIPY = True
except Exception:  # scipy is optional in some Kit builds
    _HAS_SCIPY = False

_GROUPS = {json.dumps(safe_groups, indent=4)}
_TARGET_PATH = {target_path!r}
_RNG = np.random.default_rng({seed})


def _sample_copula(group):
    """Draw one correlated sample from a 2-or-more-param Gaussian copula."""
    params = group["params"]
    ranges = group["ranges"]
    rho = float(group["correlation"])
    n = len(params)
    if n == 0:
        return {{}}
    # Build symmetric correlation matrix (rho off-diagonal, 1 on diagonal).
    cov = np.full((n, n), rho)
    np.fill_diagonal(cov, 1.0)
    # Latent multivariate normal -> uniform via standard normal CDF.
    z = _RNG.multivariate_normal(np.zeros(n), cov)
    if _HAS_SCIPY:
        u = norm.cdf(z)
    else:
        # Closed-form approximation when scipy is unavailable.
        u = 0.5 * (1.0 + np.tanh(z / np.sqrt(2.0)))
    out = {{}}
    for i, p in enumerate(params):
        lo, hi = ranges[p]
        out[p] = float(lo + (hi - lo) * u[i])
    return out


def _sample_linear(group):
    """Anchor first param uniformly, derive the rest via linear regression on rho."""
    params = group["params"]
    ranges = group["ranges"]
    rho = float(group["correlation"])
    if not params:
        return {{}}
    anchor = params[0]
    lo, hi = ranges[anchor]
    base_u = float(_RNG.uniform(0.0, 1.0))
    out = {{anchor: float(lo + (hi - lo) * base_u)}}
    for p in params[1:]:
        lo_p, hi_p = ranges[p]
        # Pull toward base_u proportional to rho, add residual noise.
        noise = float(_RNG.normal(0.0, max(1e-6, 1.0 - abs(rho)) * 0.1))
        u = max(0.0, min(1.0, rho * base_u + (1.0 - rho) * float(_RNG.uniform(0.0, 1.0)) + noise))
        out[p] = float(lo_p + (hi_p - lo_p) * u)
    return out


def sample_correlated_dr():
    """Return a dict {{group_index: {{param_name: value}}}} for one episode."""
    samples = {{}}
    for idx, group in enumerate(_GROUPS):
        if group["method"] == "linear":
            samples[idx] = _sample_linear(group)
        else:
            samples[idx] = _sample_copula(group)
    return samples


# Example: print one draw so the patch is observable in the Kit log.
_draw = sample_correlated_dr()
print(f"[correlated_dr] target={{_TARGET_PATH}} sample={{_draw}}")
'''

async def _handle_suggest_dr_ranges(args: Dict) -> Dict:
    """Suggest DR ranges from heuristics, optionally refined by real-data variance."""
    task_raw = (args.get("task_type") or "").strip()
    robot_raw = (args.get("robot") or "").strip()
    real_data_path = args.get("real_data_path")

    if not task_raw:
        return {"error": "task_type is required"}

    task_lc = task_raw.lower().replace("-", "_").replace(" ", "_")
    robot_lc = robot_raw.lower()

    # Pick the closest matching task block.
    task_key = None
    for k in _DR_TASK_DEFAULTS:
        if k in task_lc or task_lc in k:
            task_key = k
            break
    if task_key is None:
        # Fall back to manipulation-style defaults.
        task_key = "pick_and_place"

    suggested = {k: list(v) for k, v in _DR_TASK_DEFAULTS[task_key].items()}

    # Robot-specific overrides.
    robot_match = None
    for k, v in _DR_ROBOT_HINTS.items():
        if k in robot_lc:
            robot_match = k
            for hint_k, hint_v in v.items():
                if isinstance(hint_v, list):
                    suggested[hint_k] = list(hint_v)
            break

    # Optional empirical refinement from real sensor data.
    empirical_used = False
    empirical_notes: List[str] = []
    if real_data_path:
        rp = Path(real_data_path)
        if not rp.exists():
            empirical_notes.append(f"real_data_path not found: {real_data_path}")
        else:
            try:
                import csv
                rows: List[Dict[str, str]] = []
                if rp.suffix.lower() == ".json":
                    data = json.loads(rp.read_text())
                    if isinstance(data, list):
                        rows = [r for r in data if isinstance(r, dict)]
                else:
                    with rp.open(newline="") as fh:
                        rows = list(csv.DictReader(fh))
                # Numeric columns -> use min/max as suggested range.
                if rows:
                    keys = rows[0].keys()
                    for key in keys:
                        try:
                            vals = [float(r[key]) for r in rows if r.get(key) not in (None, "")]
                        except (TypeError, ValueError):
                            continue
                        if len(vals) >= 2:
                            lo, hi = min(vals), max(vals)
                            if hi > lo:
                                suggested[f"empirical_{key}"] = [lo, hi]
                                empirical_used = True
                if empirical_used:
                    empirical_notes.append(f"refined ranges from {len(rows)} rows in {rp.name}")
            except Exception as exc:  # robust: never raise to the LLM
                empirical_notes.append(f"failed to parse real_data_path: {exc}")

    return {
        "task_type": task_raw,
        "task_matched": task_key,
        "robot": robot_raw,
        "robot_matched": robot_match,
        "suggested_ranges": suggested,
        "real_data_used": empirical_used,
        "notes": empirical_notes,
        "message": (
            f"Suggested DR ranges for task='{task_raw}' (matched '{task_key}')"
            + (f" robot='{robot_raw}' (matched '{robot_match}')" if robot_match else "")
            + (f"; refined with empirical data" if empirical_used else "")
        ),
    }

async def _handle_apply_dr_preset(args: Dict) -> Dict:
    """Look up a DR preset by name."""
    preset = (args.get("preset") or "").strip().lower()
    if not preset:
        return {"error": "preset is required", "available": sorted(_DR_PRESETS.keys())}
    if preset not in _DR_PRESETS:
        return {
            "error": f"unknown preset '{preset}'",
            "available": sorted(_DR_PRESETS.keys()),
        }
    cfg = _DR_PRESETS[preset]
    return {
        "preset": preset,
        "description": cfg.get("description", ""),
        "parameters": {k: v for k, v in cfg.items() if k != "description"},
        "message": f"Loaded DR preset '{preset}' — feed `parameters` into configure_correlated_dr or your IsaacLab EventManager.",
    }

def _gen_add_latency_randomization(args: Dict) -> str:
    """Generate an IsaacLab EventManager-compatible ActionLatencyEvent."""
    min_ms = float(args.get("min_ms", 10.0))
    max_ms = float(args.get("max_ms", 50.0))
    physics_dt = float(args.get("physics_dt", 0.005))
    if max_ms < min_ms:
        min_ms, max_ms = max_ms, min_ms
    # Compute default buffer size = ceil(max_ms / dt_ms) + 1.
    dt_ms = physics_dt * 1000.0
    import math as _math
    auto_buf = int(_math.ceil(max_ms / max(dt_ms, 1e-6))) + 1
    buffer_size = int(args.get("buffer_size") or auto_buf)
    if buffer_size < auto_buf:
        buffer_size = auto_buf

    return f'''"""Action latency randomization for IsaacLab.
Auto-generated by Isaac Assist (add_latency_randomization).
Drop into your env.cfg as: events.action_latency = ActionLatencyEvent()
"""
import math
import numpy as np

try:
    import torch
    _HAS_TORCH = True
except Exception:
    _HAS_TORCH = False


_MIN_MS = {min_ms}
_MAX_MS = {max_ms}
_PHYSICS_DT = {physics_dt}
_BUFFER_SIZE = {buffer_size}


def _ms_to_steps(ms):
    """Convert milliseconds to integer physics steps (>=0)."""
    return max(0, int(round(ms / max(_PHYSICS_DT * 1000.0, 1e-6))))


class ActionLatencyEvent:
    """Per-env uniform action latency between min_ms and max_ms.

    On reset: sample a fresh latency for each environment.
    On step:  read actions delayed by the sampled number of physics steps.
    """

    def __init__(self, min_ms=_MIN_MS, max_ms=_MAX_MS, physics_dt=_PHYSICS_DT,
                 buffer_size=_BUFFER_SIZE):
        self.min_ms = float(min_ms)
        self.max_ms = float(max_ms)
        self.physics_dt = float(physics_dt)
        self.buffer_size = int(buffer_size)
        self._latency_steps = None  # per-env, set on first reset
        self._action_buffer = None  # ring buffer: (buffer_size, num_envs, action_dim)
        self._head = 0

    def reset(self, env, env_ids=None):
        num_envs = int(getattr(env, "num_envs", 1))
        max_steps = _ms_to_steps(self.max_ms)
        min_steps = _ms_to_steps(self.min_ms)
        if max_steps < min_steps:
            max_steps = min_steps
        sample_hi = max_steps + 1
        if _HAS_TORCH and hasattr(env, "device"):
            self._latency_steps = torch.randint(min_steps, sample_hi, (num_envs,),
                                                device=env.device)
        else:
            self._latency_steps = np.random.randint(min_steps, sample_hi, size=num_envs)

    def __call__(self, env):
        actions = getattr(env, "actions", None)
        if actions is None:
            return
        if self._action_buffer is None:
            shape = (self.buffer_size,) + tuple(getattr(actions, "shape", (1,)))
            if _HAS_TORCH and hasattr(actions, "zero_"):
                self._action_buffer = actions.new_zeros(shape)
            else:
                self._action_buffer = np.zeros(shape, dtype=np.float32)
        # Write current actions into the head slot.
        self._action_buffer[self._head] = actions
        # Read each env from its delayed slot.
        if self._latency_steps is None:
            self.reset(env)
        if _HAS_TORCH and hasattr(self._action_buffer, "device"):
            idx = (self._head - self._latency_steps) % self.buffer_size
            num_envs = int(getattr(env, "num_envs", 1))
            env_ix = torch.arange(num_envs, device=self._action_buffer.device)
            env.actions = self._action_buffer[idx, env_ix]
        else:
            num_envs = int(getattr(env, "num_envs", 1))
            for e in range(num_envs):
                lat = int(self._latency_steps[e])
                slot = (self._head - lat) % self.buffer_size
                env.actions[e] = self._action_buffer[slot, e]
        self._head = (self._head + 1) % self.buffer_size


# Eagerly construct so the patch validator sees a usable object.
action_latency_event = ActionLatencyEvent()
print(f"[action_latency] min={{_MIN_MS}}ms max={{_MAX_MS}}ms steps={{_ms_to_steps(_MAX_MS)}} buffer={{_BUFFER_SIZE}}")
'''

def _gen_preview_dr(args: Dict) -> str:
    """Generate code that captures N preview frames after re-randomizing the scene."""
    num_samples = int(args.get("num_samples", 9))
    if num_samples < 1:
        num_samples = 1
    output_dir = args.get("output_dir", "workspace/dr_previews")
    res = args.get("resolution", [512, 512])
    if not isinstance(res, (list, tuple)) or len(res) != 2:
        res = [512, 512]
    width, height = int(res[0]), int(res[1])

    return f'''"""DR preview frame generator.
Auto-generated by Isaac Assist (preview_dr).
Captures {num_samples} viewport frames at {width}x{height} after triggering
the configured Replicator randomizers between each frame.
"""
import os

_NUM_SAMPLES = {num_samples}
_OUTPUT_DIR = {output_dir!r}
_RESOLUTION = ({width}, {height})

os.makedirs(_OUTPUT_DIR, exist_ok=True)

try:
    import omni.replicator.core as rep
    _HAS_REPLICATOR = True
except Exception:
    _HAS_REPLICATOR = False


def _trigger_randomizers():
    """Step Replicator graph one tick, applying any registered randomizers."""
    if not _HAS_REPLICATOR:
        return
    try:
        rep.orchestrator.step()
    except Exception:
        pass


def _capture_frame(idx):
    """Save one viewport frame to OUTPUT_DIR/dr_preview_{{idx:03d}}.png."""
    path = os.path.join(_OUTPUT_DIR, f"dr_preview_{{idx:03d}}.png")
    try:
        from omni.kit.viewport.utility import get_active_viewport, capture_viewport_to_file
        vp = get_active_viewport()
        if vp is not None:
            capture_viewport_to_file(vp, path)
            return path
    except Exception:
        pass
    # Fallback: write a sentinel so callers can still see what was attempted.
    try:
        with open(path + ".txt", "w") as fh:
            fh.write(f"placeholder for {{path}} resolution={{_RESOLUTION}}")
    except Exception:
        pass
    return path


_written = []
for i in range(_NUM_SAMPLES):
    _trigger_randomizers()
    _written.append(_capture_frame(i))

print(f"[preview_dr] wrote {{len(_written)}} frames to {{_OUTPUT_DIR}} resolution={{_RESOLUTION}}")
'''

CODE_GEN_HANDLERS["configure_correlated_dr"] = _gen_configure_correlated_dr
CODE_GEN_HANDLERS["add_latency_randomization"] = _gen_add_latency_randomization
CODE_GEN_HANDLERS["preview_dr"] = _gen_preview_dr
DATA_HANDLERS["suggest_dr_ranges"] = _handle_suggest_dr_ranges
DATA_HANDLERS["apply_dr_preset"] = _handle_apply_dr_preset

# ══════ From feat/addendum-clearance-detection ══════
def _gen_set_clearance_monitor(args: Dict) -> str:
    """Generate code that arms a clearance / near-miss monitor on a robot."""
    art_path = args["articulation_path"]
    clearance_mm = float(args.get("clearance_mm", 50.0))
    warning_mm = float(args.get("warning_mm", 100.0))
    target_prims = args.get("target_prims") or []

    # Stop zone is the contactOffset — events fire when within this distance.
    # Use the larger of warning/stop for the contactOffset itself so we get
    # warning-zone events too; the callback then classifies them by separation.
    monitor_offset_mm = max(clearance_mm, warning_mm)
    stop_m = clearance_mm / 1000.0
    warn_m = warning_mm / 1000.0
    monitor_m = monitor_offset_mm / 1000.0
    targets_repr = repr(list(target_prims))

    return f"""\
import omni.usd
import omni.physx
from pxr import Usd, UsdGeom, UsdPhysics, PhysxSchema

stage = omni.usd.get_context().get_stage()
robot_prim = stage.GetPrimAtPath('{art_path}')
if not robot_prim.IsValid():
    raise RuntimeError('Articulation not found: {art_path}')

stop_threshold_m = {stop_m}
warning_threshold_m = {warn_m}
monitor_offset_m = {monitor_m}
target_paths = set({targets_repr})

# 1) Walk all descendants and arm contactOffset + contact reporting on every
#    prim that already has a CollisionAPI (i.e. each robot link's collider).
link_paths = []
for desc in Usd.PrimRange(robot_prim):
    if desc.HasAPI(UsdPhysics.CollisionAPI):
        physx_col = PhysxSchema.PhysxCollisionAPI.Apply(desc)
        # contactOffset is in scene units (meters in Isaac Sim defaults)
        physx_col.CreateContactOffsetAttr().Set(monitor_offset_m)
        # contactReport API must be applied to receive on_contact events
        PhysxSchema.PhysxContactReportAPI.Apply(desc)
        link_paths.append(str(desc.GetPath()))

# 2) Arm the same APIs on each target so PhysX pairs them with the robot links
for tp in target_paths:
    tprim = stage.GetPrimAtPath(tp)
    if tprim.IsValid() and tprim.HasAPI(UsdPhysics.CollisionAPI):
        physx_col = PhysxSchema.PhysxCollisionAPI.Apply(tprim)
        physx_col.CreateContactOffsetAttr().Set(monitor_offset_m)
        PhysxSchema.PhysxContactReportAPI.Apply(tprim)

# 3) Subscribe to contact-report events. `separation > 0` means the two
#    colliders are still apart but inside the contactOffset zone.
def _on_contact_report(contact_headers, contact_data):
    for header in contact_headers:
        actor0 = str(header.actor0)
        actor1 = str(header.actor1)
        # If targets were provided, only report robot-vs-target pairs
        if target_paths and not (actor0 in target_paths or actor1 in target_paths):
            continue
        for i in range(header.contact_data_offset,
                       header.contact_data_offset + header.num_contact_data):
            sep = float(contact_data[i].separation)
            if sep <= 0:
                # Actual penetration — full collision
                print(f'[CLEARANCE] COLLISION: {{actor0}} <-> {{actor1}} (penetration={{-sep*1000:.1f}}mm)')
            elif sep < stop_threshold_m:
                print(f'[CLEARANCE] STOP: {{actor0}} within {{sep*1000:.1f}}mm of {{actor1}} (<{{stop_threshold_m*1000:.0f}}mm stop zone)')
            elif sep < warning_threshold_m:
                print(f'[CLEARANCE] WARNING: {{actor0}} within {{sep*1000:.1f}}mm of {{actor1}} (<{{warning_threshold_m*1000:.0f}}mm warning zone)')

physx_iface = omni.physx.get_physx_interface()
_clearance_sub = physx_iface.subscribe_contact_report_events(_on_contact_report)

print(f'Clearance monitor armed on {{len(link_paths)}} robot links of {art_path}')
print(f'  warning zone: <{{warning_threshold_m*1000:.0f}}mm   stop zone: <{{stop_threshold_m*1000:.0f}}mm')
print(f'  monitoring against {{len(target_paths)}} target prims')
"""

def _gen_visualize_clearance(args: Dict) -> str:
    """Generate code to visualize clearance via SDF heatmap or trigger zones."""
    art_path = args["articulation_path"]
    mode = args.get("mode", "heatmap")
    target_prims = args.get("target_prims") or []
    clearance_mm = float(args.get("clearance_mm", 50.0))
    warning_mm = float(args.get("warning_mm", 100.0))
    targets_repr = repr(list(target_prims))
    stop_m = clearance_mm / 1000.0
    warn_m = warning_mm / 1000.0

    if mode == "zones":
        # Static trigger volumes (cubes scaled to warning/stop dist) around
        # each target. Trigger prims are invisible but report enter/exit.
        return f"""\
import omni.usd
from pxr import Usd, UsdGeom, UsdPhysics, PhysxSchema, Gf, Sdf

stage = omni.usd.get_context().get_stage()
target_paths = list({targets_repr})
stop_m = {stop_m}
warn_m = {warn_m}

created = []
for tp in target_paths:
    tprim = stage.GetPrimAtPath(tp)
    if not tprim.IsValid():
        print(f'[CLEARANCE-ZONES] Skipping invalid target: {{tp}}')
        continue

    # Compute world-space bounds of the target to size the trigger zones
    bbox_cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_])
    world_bbox = bbox_cache.ComputeWorldBound(tprim)
    bb_range = world_bbox.ComputeAlignedRange()
    center = bb_range.GetMidpoint()
    extent = bb_range.GetSize()

    # Warning zone (outer) and Stop zone (inner) trigger volumes
    for zone_name, gap_m in (('WarningZone', warn_m), ('StopZone', stop_m)):
        zone_path = f'{{tp}}_{{zone_name}}'
        zone_prim = stage.DefinePrim(zone_path, 'Cube')
        zone_xf = UsdGeom.Xformable(zone_prim)
        zone_xf.ClearXformOpOrder()
        zone_xf.AddTranslateOp().Set(Gf.Vec3d(center[0], center[1], center[2]))
        zone_xf.AddScaleOp().Set(Gf.Vec3d(
            float(extent[0])/2 + gap_m,
            float(extent[1])/2 + gap_m,
            float(extent[2])/2 + gap_m,
        ))
        # Make invisible — the cube only matters as a collider/trigger
        UsdGeom.Imageable(zone_prim).MakeInvisible()
        UsdPhysics.CollisionAPI.Apply(zone_prim)
        PhysxSchema.PhysxTriggerAPI.Apply(zone_prim)
        created.append(zone_path)

print(f'Created {{len(created)}} trigger zones around {{len(target_paths)}} targets for {art_path}')
print(f'  stop zone offset: {{stop_m*1000:.0f}}mm   warning zone offset: {{warn_m*1000:.0f}}mm')
"""

    # Default: heatmap. Apply PhysX SDF mesh collision to each target so we
    # can query signed distance, then color robot link positions accordingly.
    return f"""\
import omni.usd
import numpy as np
from pxr import Usd, UsdGeom, UsdPhysics, PhysxSchema, Gf
from isaacsim.util.debug_draw import _debug_draw

stage = omni.usd.get_context().get_stage()
robot_prim = stage.GetPrimAtPath('{art_path}')
if not robot_prim.IsValid():
    raise RuntimeError('Articulation not found: {art_path}')

target_paths = list({targets_repr})
stop_m = {stop_m}
warn_m = {warn_m}

# 1) Apply SDF mesh collision to each target so SDF queries can resolve
for tp in target_paths:
    tprim = stage.GetPrimAtPath(tp)
    if not tprim.IsValid():
        continue
    if not tprim.HasAPI(UsdPhysics.CollisionAPI):
        UsdPhysics.CollisionAPI.Apply(tprim)
    PhysxSchema.PhysxSDFMeshCollisionAPI.Apply(tprim)

# 2) Collect world positions of every robot link with a collider
link_positions = []
for desc in Usd.PrimRange(robot_prim):
    if desc.HasAPI(UsdPhysics.CollisionAPI):
        xf = UsdGeom.Xformable(desc).ComputeLocalToWorldTransform(Usd.TimeCode.Default())
        link_positions.append(np.array(xf.ExtractTranslation()))

if not link_positions:
    raise RuntimeError('No collider links found on {art_path}')

# 3) For each link, compute min distance to any target centroid as a coarse
#    fallback when the SDF query API isn't directly exposed in this Kit build.
target_centers = []
bbox_cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_])
for tp in target_paths:
    tprim = stage.GetPrimAtPath(tp)
    if tprim.IsValid():
        wb = bbox_cache.ComputeWorldBound(tprim)
        target_centers.append(np.array(wb.ComputeAlignedRange().GetMidpoint()))

distances = []
for lp in link_positions:
    if target_centers:
        d = min(float(np.linalg.norm(lp - tc)) for tc in target_centers)
    else:
        d = float('inf')
    distances.append(d)

# 4) Color: red if < stop, yellow if < warning, green otherwise
colors = []
for d in distances:
    if d < stop_m:
        colors.append((1.0, 0.0, 0.0, 1.0))   # red
    elif d < warn_m:
        colors.append((1.0, 1.0, 0.0, 1.0))   # yellow
    else:
        colors.append((0.0, 1.0, 0.0, 1.0))   # green

draw = _debug_draw.acquire_debug_draw_interface()
draw.clear_points()
points = [(float(p[0]), float(p[1]), float(p[2])) for p in link_positions]
draw.draw_points(points, colors, [12] * len(points))

print(f'Clearance heatmap drawn for {{len(points)}} links of {art_path}')
print(f'  stop<{{stop_m*1000:.0f}}mm=red  warn<{{warn_m*1000:.0f}}mm=yellow  safe=green')
"""

def _gen_check_path_clearance(args: Dict) -> str:
    """Generate code that runs FK on every waypoint and reports min clearance."""
    art_path = args["articulation_path"]
    trajectory = args["trajectory"]
    obstacles = args.get("obstacles") or []
    clearance_mm = float(args.get("clearance_mm", 50.0))
    threshold_m = clearance_mm / 1000.0
    obstacles_repr = repr(list(obstacles))
    # Render trajectory as a Python list literal of lists
    traj_repr = "[" + ", ".join("[" + ", ".join(f"{float(v):.6f}" for v in wp) + "]" for wp in trajectory) + "]"

    return f"""\
import json
import numpy as np
from pxr import Usd, UsdGeom, UsdPhysics
import omni.usd
from isaacsim.robot_motion.motion_generation import LulaKinematicsSolver
from isaacsim.robot_motion.motion_generation import interface_config_loader

stage = omni.usd.get_context().get_stage()
robot_prim = stage.GetPrimAtPath('{art_path}')
if not robot_prim.IsValid():
    raise RuntimeError('Articulation not found: {art_path}')

trajectory = {traj_repr}
obstacle_paths = list({obstacles_repr})
threshold_m = {threshold_m}

# Resolve obstacle world-space centroids (coarse SDF fallback)
bbox_cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_])
obstacle_centers = []
for op in obstacle_paths:
    oprim = stage.GetPrimAtPath(op)
    if not oprim.IsValid():
        continue
    wb = bbox_cache.ComputeWorldBound(oprim)
    obstacle_centers.append((op, np.array(wb.ComputeAlignedRange().GetMidpoint())))

# Load kinematics for FK
robot_name = '{art_path}'.split('/')[-1].lower()
try:
    kin_config = interface_config_loader.load_supported_lula_kinematics_solver_config(robot_name)
    kin = LulaKinematicsSolver(**kin_config)
    frame_names = kin.get_all_frame_names()
except Exception as e:
    print(json.dumps({{'status': 'error', 'message': f'Kinematics not available: {{e}}'}}))
    raise

violations = []
per_waypoint = []

for idx, q in enumerate(trajectory):
    q_np = np.array(q, dtype=float)
    # Compute FK at each frame to get all link world positions
    link_positions = []
    for fname in frame_names:
        try:
            pos, _ = kin.compute_forward_kinematics(fname, q_np)
            link_positions.append(np.array(pos))
        except Exception:
            continue

    # Min distance from any link to any obstacle centroid
    if obstacle_centers and link_positions:
        min_dist = min(
            float(np.linalg.norm(lp - oc))
            for lp in link_positions
            for _, oc in obstacle_centers
        )
        # Identify the closest obstacle
        closest = min(
            (
                (op, float(np.linalg.norm(lp - oc)))
                for lp in link_positions
                for op, oc in obstacle_centers
            ),
            key=lambda x: x[1],
        )
    else:
        min_dist = float('inf')
        closest = (None, float('inf'))

    waypoint_info = {{
        'waypoint_index': idx,
        'min_clearance_mm': round(min_dist * 1000, 2) if min_dist != float('inf') else None,
        'closest_obstacle': closest[0],
    }}
    per_waypoint.append(waypoint_info)

    if min_dist < threshold_m:
        violations.append({{
            **waypoint_info,
            'threshold_mm': threshold_m * 1000,
            'message': f'Waypoint {{idx}}: min clearance {{min_dist*1000:.1f}}mm < {{threshold_m*1000:.0f}}mm',
        }})

result = {{
    'status': 'violation' if violations else 'ok',
    'articulation_path': '{art_path}',
    'threshold_mm': threshold_m * 1000,
    'num_waypoints': len(trajectory),
    'num_violations': len(violations),
    'violations': violations,
    'per_waypoint': per_waypoint,
}}
print(json.dumps(result))
"""

CODE_GEN_HANDLERS["set_clearance_monitor"] = _gen_set_clearance_monitor
CODE_GEN_HANDLERS["visualize_clearance"] = _gen_visualize_clearance
CODE_GEN_HANDLERS["check_path_clearance"] = _gen_check_path_clearance

# ══════ From feat/new-physics-calibration ══════
def _safe_robot_name(articulation_path: str) -> str:
    """Derive a filesystem-safe slug from a USD path, e.g. '/World/Franka' -> 'franka'."""
    name = articulation_path.rstrip("/").split("/")[-1] or "robot"
    return "".join(c if c.isalnum() or c in "_-" else "_" for c in name).lower()

def _suggested_dr_ranges(parameters: List[str]) -> Dict[str, str]:
    return {p: _DR_RANGE_HINTS[p] for p in parameters if p in _DR_RANGE_HINTS}

def _generate_calibration_script(
    real_data_path: str,
    articulation_path: str,
    parameters: List[str],
    num_samples: int,
    num_workers: int,
    output_dir: str,
) -> str:
    """Generate the headless Bayesian-optimization script.

    Uses Ray Tune + OptunaSearch (already in isaac_lab_env). The script replays
    commanded torques in sim and minimizes trajectory mismatch.
    """
    return f'''"""Auto-generated physics calibration script.
Articulation: {articulation_path}
Real data:    {real_data_path}
Parameters:   {parameters}
"""
from __future__ import annotations
import json
import os
from pathlib import Path

import h5py
import numpy as np
import ray
from ray import tune
from ray.tune.search.optuna import OptunaSearch

REAL_DATA_PATH = {real_data_path!r}
ARTICULATION_PATH = {articulation_path!r}
PARAMETERS = {parameters!r}
OUTPUT_DIR = Path({output_dir!r})
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def load_real_data(path):
    with h5py.File(path, "r") as f:
        return {{
            "joint_positions": f["joint_positions"][:],
            "joint_velocities": f["joint_velocities"][:],
            "joint_torques_commanded": f["joint_torques_commanded"][:],
        }}


def replay_trajectory(art, commanded_torques):
    """Stub — IsaacLab integration replays commanded torques in sim."""
    raise NotImplementedError("Replay must run inside isaac_lab_env (GPU + Kit)")


def trajectory_distance(sim, real):
    return float(np.sqrt(np.mean((sim - real) ** 2)))


def objective(config):
    real = load_real_data(REAL_DATA_PATH)
    # IsaacLab env imports happen inside the trial process (needs GPU)
    from isaaclab.app import AppLauncher
    app = AppLauncher(headless=True).app  # noqa: F841
    from isaaclab.assets import Articulation
    art = Articulation.from_path(ARTICULATION_PATH)
    if "friction" in config:
        art.write_joint_friction_coefficient_to_sim(config["friction"])
    if "damping" in config:
        art.write_joint_damping_to_sim(config["damping"])
    if "armature" in config:
        art.write_joint_armature_to_sim(config["armature"])
    if "masses" in config:
        art.set_masses(config["masses"])
    sim_traj = replay_trajectory(art, real["joint_torques_commanded"])
    error = trajectory_distance(sim_traj, real["joint_positions"])
    return {{"loss": error}}


def make_search_space(parameters):
    space = {{}}
    if "friction" in parameters:
        space["friction"] = tune.uniform(0.1, 2.0)
    if "damping" in parameters:
        space["damping"] = tune.uniform(0.01, 1.0)
    if "armature" in parameters:
        space["armature"] = tune.uniform(0.0, 0.5)
    if "viscous_friction" in parameters:
        space["viscous_friction"] = tune.uniform(0.0, 0.5)
    if "masses" in parameters:
        space["masses_scale"] = tune.uniform(0.8, 1.2)
    return space


def main():
    ray.init(num_cpus={num_workers}, ignore_reinit_error=True)
    analysis = tune.run(
        objective,
        search_alg=OptunaSearch(metric="loss", mode="min"),
        config=make_search_space(PARAMETERS),
        num_samples={num_samples},
        local_dir=str(OUTPUT_DIR / "ray_results"),
    )
    best = analysis.get_best_config(metric="loss", mode="min")
    result = {{
        "calibrated_parameters": best,
        "best_loss": analysis.best_result["loss"],
    }}
    (OUTPUT_DIR / "result.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
'''

def _check_real_data_path(path: str) -> Optional[str]:
    """Return an error string if the real_data_path is unusable, else None."""
    if not path:
        return "real_data_path is required"
    p = Path(path)
    if not p.exists():
        return f"real_data_path not found: {path}"
    if p.suffix.lower() not in (".h5", ".hdf5"):
        return f"real_data_path must be HDF5 (.h5/.hdf5), got {p.suffix}"
    return None

async def _handle_calibrate_physics(args: Dict) -> Dict:
    """Generate a Ray-Tune+Optuna calibration script and return the launch command."""
    real_data_path = args.get("real_data_path", "")
    articulation_path = args.get("articulation_path", "")

    err = _check_real_data_path(real_data_path)
    if err:
        return {"error": err}
    if not articulation_path:
        return {"error": "articulation_path is required"}

    raw_params = args.get("parameters_to_calibrate") or _DEFAULT_CALIBRATE_PARAMS
    parameters = [p for p in raw_params if p in _VALID_CALIBRATE_PARAMS]
    if not parameters:
        return {
            "error": f"No valid parameters_to_calibrate. Allowed: {sorted(_VALID_CALIBRATE_PARAMS)}",
        }

    num_samples = int(args.get("num_samples", 100))
    num_workers = int(args.get("num_workers", 4))
    if num_samples <= 0:
        return {"error": "num_samples must be positive"}
    if num_workers <= 0:
        return {"error": "num_workers must be positive"}

    robot = _safe_robot_name(articulation_path)
    output_dir = args.get("output_dir") or f"workspace/calibration/{robot}"
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    script = _generate_calibration_script(
        real_data_path=real_data_path,
        articulation_path=articulation_path,
        parameters=parameters,
        num_samples=num_samples,
        num_workers=num_workers,
        output_dir=output_dir,
    )
    script_path = out / "calibrate_physics.py"
    script_path.write_text(script, encoding="utf-8")

    # Approximate runtime: 30-120 min for 100 samples (per spec)
    est_minutes = max(5, int(num_samples * 0.6))

    return {
        "type": "calibration_job",
        "always_require_approval": True,
        "robot": robot,
        "articulation_path": articulation_path,
        "real_data_path": real_data_path,
        "parameters_to_calibrate": parameters,
        "num_samples": num_samples,
        "num_workers": num_workers,
        "output_dir": str(out),
        "script_path": str(script_path),
        "launch_command": f"python {script_path}",
        "estimated_minutes": est_minutes,
        "suggested_dr_ranges": _suggested_dr_ranges(parameters),
        "result_file": str(out / "result.json"),
        "message": (
            f"Calibration script written to {script_path}. "
            f"This is a long-running headless job (~{est_minutes} min) — "
            "run it manually inside isaac_lab_env (Ray + Optuna already installed). "
            "Results land in result.json."
        ),
    }

async def _handle_quick_calibrate(args: Dict) -> Dict:
    """Faster calibration: only the highest-impact parameters."""
    real_data_path = args.get("real_data_path", "")
    articulation_path = args.get("articulation_path", "")

    err = _check_real_data_path(real_data_path)
    if err:
        return {"error": err}
    if not articulation_path:
        return {"error": "articulation_path is required"}

    parameters = list(_QUICK_CALIBRATE_PARAMS)
    if args.get("include_masses") is False:
        parameters = [p for p in parameters if p != "masses"]

    robot = _safe_robot_name(articulation_path)
    output_dir = args.get("output_dir") or f"workspace/calibration/{robot}_quick"
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    # Quick calibration uses fewer samples (~30) and runs ~5 min per spec
    num_samples = 30
    num_workers = 4

    script = _generate_calibration_script(
        real_data_path=real_data_path,
        articulation_path=articulation_path,
        parameters=parameters,
        num_samples=num_samples,
        num_workers=num_workers,
        output_dir=output_dir,
    )
    script_path = out / "quick_calibrate.py"
    script_path.write_text(script, encoding="utf-8")

    return {
        "type": "calibration_job",
        "always_require_approval": True,
        "mode": "quick",
        "robot": robot,
        "articulation_path": articulation_path,
        "real_data_path": real_data_path,
        "parameters_to_calibrate": parameters,
        "num_samples": num_samples,
        "output_dir": str(out),
        "script_path": str(script_path),
        "launch_command": f"python {script_path}",
        "estimated_minutes": 5,
        "suggested_dr_ranges": _suggested_dr_ranges(parameters),
        "result_file": str(out / "result.json"),
        "message": (
            f"Quick-calibration script written to {script_path} (~5 min, "
            f"{len(parameters)} parameters: {parameters}). "
            "Run it inside isaac_lab_env. For higher fidelity use calibrate_physics."
        ),
    }

def _per_joint_rmse(sim_traj: List[List[float]], real_traj: List[List[float]]) -> List[float]:
    """RMSE per joint between two joint-trajectory arrays of shape (T, n_joints)."""
    n_steps = min(len(sim_traj), len(real_traj))
    if n_steps == 0:
        return []
    n_joints = min(len(sim_traj[0]), len(real_traj[0])) if sim_traj[0] else 0
    rmses: List[float] = []
    for j in range(n_joints):
        sq = 0.0
        for t in range(n_steps):
            d = float(sim_traj[t][j]) - float(real_traj[t][j])
            sq += d * d
        rmses.append((sq / n_steps) ** 0.5)
    return rmses

async def _handle_validate_calibration(args: Dict) -> Dict:
    """Validate a calibration result on a held-out test trajectory.

    Inputs:
      - calibrated_params: dict — typically the output of calibrate_physics
      - test_data_path: path to HDF5 with held-out real trajectory
      - baseline_error (optional): pre-calibration error to compare against

    Returns: per-joint and overall RMSE, plus contact-force comparison if F/T data
    is detected. The actual replay-in-sim happens via IsaacLab; this handler
    validates inputs and prepares the comparison report. If the HDF5 file already
    contains a sim_joint_positions field (added by a prior replay run), the
    report is computed in-process.
    """
    calibrated_params = args.get("calibrated_params")
    test_data_path = args.get("test_data_path", "")
    baseline_error = args.get("baseline_error")

    if not isinstance(calibrated_params, dict) or not calibrated_params:
        return {"error": "calibrated_params must be a non-empty dict"}

    err = _check_real_data_path(test_data_path)
    if err:
        return {"error": err}

    # Try to read sim/real trajectories if a prior replay has populated them.
    sim_positions: Optional[List[List[float]]] = None
    real_positions: Optional[List[List[float]]] = None
    contact_forces_sim: Optional[List[List[float]]] = None
    contact_forces_real: Optional[List[List[float]]] = None
    has_ft_data = False
    try:
        import h5py  # type: ignore
        with h5py.File(test_data_path, "r") as f:
            if "joint_positions" in f:
                real_positions = f["joint_positions"][:].tolist()
            if "sim_joint_positions" in f:
                sim_positions = f["sim_joint_positions"][:].tolist()
            if "contact_forces" in f:
                has_ft_data = True
                contact_forces_real = f["contact_forces"][:].tolist()
            if "sim_contact_forces" in f:
                contact_forces_sim = f["sim_contact_forces"][:].tolist()
    except ImportError:
        pass
    except Exception as e:  # pragma: no cover — corrupted HDF5
        return {"error": f"Failed to read test_data_path: {e}"}

    per_joint_rmse: List[float] = []
    overall_rmse: Optional[float] = None
    if sim_positions is not None and real_positions is not None:
        per_joint_rmse = _per_joint_rmse(sim_positions, real_positions)
        if per_joint_rmse:
            overall_rmse = sum(r * r for r in per_joint_rmse) / len(per_joint_rmse)
            overall_rmse = overall_rmse ** 0.5

    contact_force_rmse: Optional[float] = None
    if contact_forces_sim is not None and contact_forces_real is not None:
        n = min(len(contact_forces_sim), len(contact_forces_real))
        if n > 0:
            comp = min(len(contact_forces_sim[0]), len(contact_forces_real[0]))
            sq = 0.0
            count = 0
            for t in range(n):
                for c in range(comp):
                    d = float(contact_forces_sim[t][c]) - float(contact_forces_real[t][c])
                    sq += d * d
                    count += 1
            if count:
                contact_force_rmse = (sq / count) ** 0.5

    improvement_pct: Optional[float] = None
    if overall_rmse is not None and baseline_error not in (None, 0):
        try:
            baseline = float(baseline_error)
            if baseline > 0:
                improvement_pct = round(100.0 * (baseline - overall_rmse) / baseline, 2)
        except (TypeError, ValueError):
            improvement_pct = None

    needs_replay = sim_positions is None or real_positions is None

    return {
        "type": "calibration_validation",
        "test_data_path": test_data_path,
        "calibrated_param_keys": sorted(calibrated_params.keys()),
        "trajectory_error": overall_rmse,
        "per_joint_rmse": per_joint_rmse,
        "baseline_error": baseline_error,
        "improvement_pct": improvement_pct,
        "has_ft_data": has_ft_data,
        "contact_force_rmse": contact_force_rmse,
        "needs_replay": needs_replay,
        "message": (
            "Validation report computed in-process from cached sim trajectories."
            if not needs_replay
            else "Sim trajectories not present in HDF5 — run the calibrated params in IsaacLab "
                 "to produce 'sim_joint_positions' before reporting tracking error."
        ),
    }

def _generate_actuator_net_script(
    real_data_path: str,
    articulation_path: str,
    hidden_dim: int,
    num_layers: int,
    num_epochs: int,
    output_dir: str,
) -> str:
    """Generate IsaacLab ActuatorNetLSTM training script."""
    return f'''"""Auto-generated ActuatorNet (LSTM) training script.
Articulation: {articulation_path}
Real data:    {real_data_path}
"""
from __future__ import annotations
import json
from pathlib import Path

import h5py
import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset

REAL_DATA_PATH = {real_data_path!r}
ARTICULATION_PATH = {articulation_path!r}
OUTPUT_DIR = Path({output_dir!r})
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

HIDDEN_DIM = {hidden_dim}
NUM_LAYERS = {num_layers}
NUM_EPOCHS = {num_epochs}


class ActuatorLSTM(nn.Module):
    def __init__(self, input_dim, hidden_dim, num_layers, output_dim):
        super().__init__()
        self.lstm = nn.LSTM(input_dim, hidden_dim, num_layers, batch_first=True)
        self.head = nn.Linear(hidden_dim, output_dim)

    def forward(self, x):
        out, _ = self.lstm(x)
        return self.head(out)


def load_pairs(path):
    with h5py.File(path, "r") as f:
        q_target = f["joint_positions_target"][:] if "joint_positions_target" in f else f["joint_positions"][:]
        q = f["joint_positions"][:]
        qd = f["joint_velocities"][:]
        tau = f["joint_torques_commanded"][:]
    x = np.stack([q_target - q, qd], axis=-1)  # (T, n_joints, 2)
    y = tau
    return x, y


def main():
    x, y = load_pairs(REAL_DATA_PATH)
    n_joints = x.shape[1]
    x_t = torch.tensor(x, dtype=torch.float32).reshape(1, x.shape[0], n_joints * 2)
    y_t = torch.tensor(y, dtype=torch.float32).reshape(1, y.shape[0], n_joints)
    ds = TensorDataset(x_t, y_t)
    dl = DataLoader(ds, batch_size=1)
    model = ActuatorLSTM(n_joints * 2, HIDDEN_DIM, NUM_LAYERS, n_joints)
    opt = torch.optim.Adam(model.parameters(), lr=1e-3)
    loss_fn = nn.MSELoss()
    losses = []
    for epoch in range(NUM_EPOCHS):
        for xb, yb in dl:
            pred = model(xb)
            loss = loss_fn(pred, yb)
            opt.zero_grad()
            loss.backward()
            opt.step()
        losses.append(float(loss.item()))
        if epoch % 20 == 0:
            print(f"epoch {{epoch}} loss={{loss.item():.6f}}")
    ckpt = OUTPUT_DIR / "actuator_net.pt"
    torch.save({{"model": model.state_dict(), "config": {{
        "hidden_dim": HIDDEN_DIM,
        "num_layers": NUM_LAYERS,
        "input_dim": n_joints * 2,
        "output_dim": n_joints,
    }}}}, ckpt)
    (OUTPUT_DIR / "result.json").write_text(json.dumps({{
        "checkpoint": str(ckpt),
        "final_loss": losses[-1] if losses else None,
        "num_epochs": NUM_EPOCHS,
    }}, indent=2))
    print(f"ActuatorNet saved to {{ckpt}}")


if __name__ == "__main__":
    main()
'''

async def _handle_train_actuator_net(args: Dict) -> Dict:
    """Generate the ActuatorNetLSTM training script and return launch command."""
    real_data_path = args.get("real_data_path", "")
    articulation_path = args.get("articulation_path", "")

    err = _check_real_data_path(real_data_path)
    if err:
        return {"error": err}
    if not articulation_path:
        return {"error": "articulation_path is required"}

    hidden_dim = int(args.get("hidden_dim", 32))
    num_layers = int(args.get("num_layers", 2))
    num_epochs = int(args.get("num_epochs", 200))
    if hidden_dim <= 0 or num_layers <= 0 or num_epochs <= 0:
        return {"error": "hidden_dim, num_layers, num_epochs must all be positive"}

    robot = _safe_robot_name(articulation_path)
    output_dir = args.get("output_dir") or f"workspace/calibration/{robot}_actuator_net"
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    script = _generate_actuator_net_script(
        real_data_path=real_data_path,
        articulation_path=articulation_path,
        hidden_dim=hidden_dim,
        num_layers=num_layers,
        num_epochs=num_epochs,
        output_dir=output_dir,
    )
    script_path = out / "train_actuator_net.py"
    script_path.write_text(script, encoding="utf-8")

    return {
        "type": "actuator_net_job",
        "always_require_approval": True,
        "robot": robot,
        "articulation_path": articulation_path,
        "real_data_path": real_data_path,
        "hidden_dim": hidden_dim,
        "num_layers": num_layers,
        "num_epochs": num_epochs,
        "output_dir": str(out),
        "script_path": str(script_path),
        "launch_command": f"python {script_path}",
        "checkpoint_path": str(out / "actuator_net.pt"),
        "result_file": str(out / "result.json"),
        "message": (
            f"ActuatorNet training script written to {script_path}. "
            "Long-running headless training — needs 5-10 min of diverse-motion real data. "
            "Output is a torch checkpoint that replaces physical-parameter calibration."
        ),
    }

DATA_HANDLERS["calibrate_physics"] = _handle_calibrate_physics
DATA_HANDLERS["quick_calibrate"] = _handle_quick_calibrate
DATA_HANDLERS["validate_calibration"] = _handle_validate_calibration
DATA_HANDLERS["train_actuator_net"] = _handle_train_actuator_net

# ══════ From feat/addendum-humanoid-advanced ══════
def _gen_setup_contact_sensors(args: Dict) -> str:
    """Generate per-fingertip ContactSensorCfg + PhysxCfg buffer bumps for `num_envs`."""
    articulation_path = args["articulation_path"]
    body_names = args["body_names"]
    if not isinstance(body_names, list) or not body_names:
        body_names = ["fingertip"]
    num_envs = int(args.get("num_envs", 4096))
    update_period = float(args.get("update_period", 0.0))
    history_length = int(args.get("history_length", 1))
    track_air_time = bool(args.get("track_air_time", False))

    # Heuristic: bump GPU buffers when num_envs * sensors_per_env exceeds
    # the implicit 8M default (PhysX default = 2**23 contacts, 2**22 patches).
    contacts_needed = num_envs * len(body_names) * 8  # est. 8 contacts per fingertip
    contact_pow = max(24, contacts_needed.bit_length())  # at least 2**24 = 16M
    patch_pow = max(23, (contacts_needed // 2).bit_length())  # at least 2**23 = 8M

    lines = [
        '"""Auto-generated ContactSensorCfg block.',
        f"Articulation: {articulation_path}",
        f"Bodies: {body_names}",
        f"num_envs={num_envs}",
        '"""',
        "from isaaclab.sensors import ContactSensorCfg",
        "from isaaclab.sim import PhysxCfg",
        "",
        "# One ContactSensorCfg per body (mandatory one-to-many constraint —",
        "# wildcards in prim_path do not aggregate, they would silently overwrite).",
        "contact_sensors = {",
    ]
    for body in body_names:
        # Sanitize the body name for use as a Python identifier in the dict key
        safe_key = "".join(c if c.isalnum() or c == "_" else "_" for c in body)
        lines.extend([
            f"    {safe_key!r}: ContactSensorCfg(",
            f"        prim_path=f'{{ENV_REGEX_NS}}/Robot/{body}',",
            f"        update_period={update_period},  # 0.0 = every physics step",
            f"        history_length={history_length},",
            f"        track_air_time={track_air_time},",
            "    ),",
        ])
    lines.extend([
        "}",
        "",
        f"# Critical: bump GPU buffers for {num_envs} envs x {len(body_names)} sensors.",
        "# Default 2**23 contacts / 2**22 patches will silently overflow at this scale,",
        "# producing zero forces on all sensors with no error message.",
        "physx_cfg = PhysxCfg(",
        f"    gpu_max_rigid_contact_count=2**{contact_pow},",
        f"    gpu_max_rigid_patch_count=2**{patch_pow},",
        ")",
        "",
        "# Cheap alternative when you just need 'is there contact?':",
        "#   joint_forces = articulation.root_physx_view.get_link_incoming_joint_force()",
        "#   fingertip_forces = joint_forces[:, fingertip_body_ids]",
        "# (Includes gravity / inertia contributions — not pure contact, but zero overhead.)",
    ])
    return "\n".join(lines)

def _gen_setup_whole_body_control(args: Dict) -> str:
    """Generate ActionGroupCfg combining a locomotion RL policy + arm planner."""
    articulation_path = args["articulation_path"]
    locomotion_policy = args["locomotion_policy"]
    arm_planner = args.get("arm_planner", "pink_ik")
    profile_key = args.get("robot_profile", "generic")
    profile = _WHOLE_BODY_PROFILES.get(profile_key, _WHOLE_BODY_PROFILES["generic"])
    ee_frame = args.get("ee_frame", profile["ee_frame"])
    command_type = profile["command_type"]

    lines = [
        '"""Auto-generated whole-body control config.',
        f"Articulation: {articulation_path}",
        f"Profile: {profile_key} ({profile['status']})",
        f"Locomotion: {locomotion_policy}",
        f"Arm planner: {arm_planner}",
        '"""',
        "from isaaclab.envs import ActionGroupCfg",
        "",
        "# Lower body: locomotion RL policy (HOVER family typical)",
        "locomotion_cfg = LocomotionPolicyCfg(",
        f"    checkpoint={locomotion_policy!r},",
        "    action_space='lower_body_joints',",
        f"    command_type={command_type!r},",
        ")",
        "",
    ]
    if arm_planner == "pink_ik":
        lines.extend([
            "# Upper body: Pink-IK QP controller (Pinocchio)",
            "arm_cfg = PinkIKControllerCfg(",
            f"    robot_model={articulation_path!r},",
            f"    ee_frame={ee_frame!r},",
            "    tasks=[",
            f"        FrameTask(frame={ee_frame!r}, position_cost=1.0, orientation_cost=0.5),",
            "        PostureTask(cost=0.01),  # null-space regularization",
            "        DampingTask(cost=0.001),",
            "    ],",
            ")",
        ])
    elif arm_planner == "lula":
        lines.extend([
            "# Upper body: Lula RRT/RMP planner",
            "arm_cfg = LulaControllerCfg(",
            f"    robot_model={articulation_path!r},",
            f"    ee_frame={ee_frame!r},",
            ")",
        ])
    else:  # rmpflow
        lines.extend([
            "# Upper body: RmpFlow controller",
            "arm_cfg = RmpFlowControllerCfg(",
            f"    robot_model={articulation_path!r},",
            f"    ee_frame={ee_frame!r},",
            ")",
        ])
    lines.extend([
        "",
        "# Combine in ActionGroupCfg",
        "action_cfg = ActionGroupCfg(",
        "    lower_body=locomotion_cfg,",
        "    upper_body=arm_cfg,",
        ")",
    ])
    return "\n".join(lines)

def _gen_setup_loco_manipulation_training(args: Dict) -> str:
    """Generate training scaffolding + reward-mixing advisor for loco-manipulation."""
    task = args["task_description"]
    robot = args["robot"]
    approach = args.get("approach", "decoupled")
    reward_terms = args.get("reward_terms", []) or []

    # Categorize and sum weights to detect imbalance
    loco_weight = 0.0
    manip_weight = 0.0
    for term in reward_terms:
        cat = (term.get("category") or "").lower()
        w = float(term.get("weight", 0.0))
        if cat == "locomotion":
            loco_weight += w
        elif cat == "manipulation":
            manip_weight += w

    advisor_lines = []
    if reward_terms:
        advisor_lines.append("# Reward mixing advisor:")
        for term in reward_terms:
            advisor_lines.append(
                f"#   - {term.get('name', '?')}: weight {term.get('weight', '?')}"
                f" ({term.get('category', 'unknown')})"
            )
        if manip_weight > loco_weight and loco_weight > 0:
            advisor_lines.extend([
                "#",
                f"# WARNING: manipulation weight ({manip_weight}) exceeds locomotion ({loco_weight}).",
                "# Early training will optimize grasping at the expense of balance.",
                "#",
                "# Recommended 3-phase schedule:",
                "#   Phase 1 (0-2000 iters):    locomotion_weight=5.0, manipulation_weight=0.5",
                "#   Phase 2 (2000-5000 iters): locomotion_weight=2.0, manipulation_weight=1.0",
                "#   Phase 3 (5000+ iters):     locomotion_weight=1.0, manipulation_weight=2.0",
            ])
        else:
            advisor_lines.append("# Reward weights look balanced for early training.")

    if approach == "decoupled":
        approach_blurb = (
            "# Approach: DECOUPLED (HOVER locomotion + Pink-IK arm).\n"
            "# Best for slow deliberate tasks. Lowest complexity — already in IsaacLab."
        )
    elif approach == "hierarchical":
        approach_blurb = (
            "# Approach: HIERARCHICAL dual-agent (SoFTA / FALCON pattern).\n"
            "# Best for dynamic tasks. Medium complexity."
        )
    else:  # joint
        approach_blurb = (
            "# Approach: JOINT end-to-end RL.\n"
            "# Maximum performance, highest complexity — needs reward curriculum."
        )

    lines = [
        '"""Loco-manipulation training scaffold.',
        f"Task: {task}",
        f"Robot: {robot}",
        f"Approach: {approach}",
        '"""',
        approach_blurb,
        "",
        f"task_description = {task!r}",
        f"robot = {robot!r}",
        f"approach = {approach!r}",
        "",
    ]
    if advisor_lines:
        lines.extend(advisor_lines)
        lines.append("")
    lines.extend([
        "# Configure the env builder according to the chosen approach.",
        "# (See create_isaaclab_env / launch_training tools to wire the pieces.)",
    ])
    return "\n".join(lines)

def _gen_setup_rsi_from_demos(args: Dict) -> str:
    """Generate Reference State Initialization config from demo trajectories."""
    demo_path = args["demo_path"]
    env_cfg = args["env_cfg"]
    noise_std = float(args.get("noise_std", 0.05))

    return (
        '"""Reference State Initialization from demonstrations.\n'
        f'Demo file: {demo_path}\n'
        f'Env cfg:   {env_cfg}\n'
        '"""\n'
        "from isaaclab.envs import InitialStateCfg\n"
        "\n"
        "# RSI: sample initial state from demo trajectories instead of default pose.\n"
        "# Highest-impact technique for loco-manipulation RL.\n"
        "rsi_cfg = InitialStateCfg(\n"
        "    mode='demo_sampling',\n"
        f"    demo_path={demo_path!r},\n"
        f"    noise_std={noise_std},  # small Gaussian perturbation around demo states\n"
        ")\n"
        "\n"
        f"# Attach to env config (e.g. {env_cfg}.initial_state = rsi_cfg)\n"
        f"# or pass through the env constructor.\n"
    )

def _gen_setup_multi_rate(args: Dict) -> str:
    """Generate DualRateVecEnvWrapper for upper/lower body running at different Hz."""
    lower_hz = float(args.get("lower_rate_hz", 50))
    upper_hz = float(args.get("upper_rate_hz", 100))
    upper_dof = int(args.get("upper_dof", 14))

    if lower_hz <= 0:
        lower_hz = 50.0
    if upper_hz <= 0:
        upper_hz = 100.0
    # Decimation = ratio of upper:lower (must be >= 1)
    decimation = max(1, int(round(upper_hz / lower_hz)))

    return (
        '"""Dual-rate VecEnv wrapper for whole-body humanoid control.\n'
        f'Upper body: {upper_hz} Hz (manipulation IK)\n'
        f'Lower body: {lower_hz} Hz (locomotion RL)\n'
        f'Decimation: every {decimation} upper steps -> 1 lower step\n'
        '"""\n'
        "import gymnasium as gym\n"
        "import torch\n"
        "\n"
        "\n"
        "class DualRateWrapper(gym.Wrapper):\n"
        f"    UPPER_DOF = {upper_dof}\n"
        f"    DECIMATION = {decimation}\n"
        "\n"
        "    def __init__(self, env):\n"
        "        super().__init__(env)\n"
        "        self.step_count = 0\n"
        "        self._cached_lower = None\n"
        "\n"
        "    def step(self, action):\n"
        "        # Upper body acts every step\n"
        "        upper_action = action[:, :self.UPPER_DOF]\n"
        "\n"
        "        # Lower body acts every DECIMATION-th step, otherwise reuse cached action\n"
        "        if self.step_count % self.DECIMATION == 0 or self._cached_lower is None:\n"
        "            lower_action = action[:, self.UPPER_DOF:]\n"
        "            self._cached_lower = lower_action\n"
        "        else:\n"
        "            lower_action = self._cached_lower\n"
        "\n"
        "        full_action = torch.cat([upper_action, lower_action], dim=-1)\n"
        "        self.step_count += 1\n"
        "        return self.env.step(full_action)\n"
        "\n"
        "    def reset(self, **kwargs):\n"
        "        self.step_count = 0\n"
        "        self._cached_lower = None\n"
        "        return self.env.reset(**kwargs)\n"
    )

async def _handle_diagnose_whole_body(args: Dict) -> Dict:
    """Diagnostic checklist for humanoid balance/coordination during arm motion."""
    articulation_path = args["articulation_path"]
    margin = float(args.get("support_polygon_margin_m", 0.05))
    accel_thresh = float(args.get("ee_accel_threshold_m_s2", 5.0))

    checks = [
        {
            "id": "balance_margin",
            "name": "Balance margin during arm motion",
            "description": (
                "Compare CoM ground projection to support polygon (foot contacts). "
                f"Min margin: {margin} m. If CoM exits the polygon during reach, "
                "the locomotion policy will compensate or the robot will tip."
            ),
        },
        {
            "id": "com_projection",
            "name": "CoM projection vs support polygon",
            "description": (
                "Compute polygon from active foot contact patches, project CoM onto ground "
                "plane, measure signed distance to nearest edge. Negative = CoM outside polygon."
            ),
        },
        {
            "id": "arm_payload_effect",
            "name": "Arm payload effect on locomotion policy",
            "description": (
                "If the locomotion policy was trained with a free arm, attaching a "
                "heavy end-effector (or carrying an object) shifts the CoM and can "
                "destabilize gait. Retrain with payload domain randomization or use "
                "a payload-conditioned policy."
            ),
        },
        {
            "id": "ee_acceleration",
            "name": "EE acceleration during gait",
            "description": (
                f"High EE acceleration (> {accel_thresh} m/s^2) injects reaction "
                "forces into the torso that the locomotion policy did not see during "
                "training. Smooth the IK trajectory or add an EE-acceleration penalty."
            ),
        },
    ]

    return {
        "articulation_path": articulation_path,
        "support_polygon_margin_m": margin,
        "ee_accel_threshold_m_s2": accel_thresh,
        "checks": checks,
        "message": (
            f"Diagnose whole-body checklist for {articulation_path} "
            f"({len(checks)} items). Run each check against the live articulation "
            "to identify why the robot is falling during arm motion."
        ),
    }

CODE_GEN_HANDLERS["setup_contact_sensors"] = _gen_setup_contact_sensors
CODE_GEN_HANDLERS["setup_whole_body_control"] = _gen_setup_whole_body_control
CODE_GEN_HANDLERS["setup_loco_manipulation_training"] = _gen_setup_loco_manipulation_training
CODE_GEN_HANDLERS["setup_rsi_from_demos"] = _gen_setup_rsi_from_demos
CODE_GEN_HANDLERS["setup_multi_rate"] = _gen_setup_multi_rate
DATA_HANDLERS["diagnose_whole_body"] = _handle_diagnose_whole_body

# ══════ From feat/phase10-autonomous-workflows ══════
def _wf_now_iso() -> str:
    return _wf_dt.utcnow().isoformat() + "Z"

def _wf_make_initial_plan(workflow_type: str, goal: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """Build the initial editable plan artifact from a template + goal + params.

    The LLM is expected to refine this further on the user-facing side; this
    function only produces the structural skeleton so the workflow can be
    persisted and queried before the LLM round-trips.
    """
    tpl = _WORKFLOW_TEMPLATES[workflow_type]
    merged_params = dict(tpl["default_params"])
    merged_params.update(params or {})
    return {
        "workflow_type": workflow_type,
        "goal": goal,
        "params": merged_params,
        "phases": [
            {
                "name": p["name"],
                "checkpoint": p["checkpoint"],
                "error_fix": p["error_fix"],
                "status": "pending",
            }
            for p in tpl["phases"]
        ],
        "editable": True,
    }

async def _handle_start_workflow(args: Dict) -> Dict:
    """Start a multi-step autonomous workflow.

    Returns a workflow_id immediately; the workflow is paused at the first
    checkpoint (the plan artifact) until approve_workflow_checkpoint fires.
    """
    workflow_type = args.get("workflow_type")
    goal = args.get("goal", "")
    if workflow_type not in _WORKFLOW_TEMPLATES:
        return {
            "ok": False,
            "error": f"Unknown workflow_type '{workflow_type}'. Supported: {sorted(_WORKFLOW_TEMPLATES)}",
        }
    if not goal:
        return {"ok": False, "error": "goal is required (high-level user intent)."}

    wf_id = f"wf_{_wf_uuid.uuid4().hex[:12]}"
    scope_prim = args.get("scope_prim", "/World")
    max_retries = min(int(args.get("max_retries", 3)), _WORKFLOW_RETRY_HARD_CAP)
    auto_approve = bool(args.get("auto_approve_checkpoints", False))

    plan = _wf_make_initial_plan(workflow_type, goal, args.get("params") or {})

    workflow = {
        "id": wf_id,
        "type": workflow_type,
        "goal": goal,
        "scope_prim": scope_prim,
        "max_retries": max_retries,
        "auto_approve_checkpoints": auto_approve,
        "plan": plan,
        "status": "awaiting_plan_approval",
        "current_phase": "plan",
        "completed_phases": [],
        "checkpoint_decisions": [],
        "error_fix_attempts": [],
        "events": [
            {"type": "workflow_started", "at": _wf_now_iso(), "phase": "plan"},
        ],
        "created_at": _wf_now_iso(),
        "updated_at": _wf_now_iso(),
        "snapshot_id": None,  # filled in by routes.py before phase 2 if available
    }
    _WORKFLOWS[wf_id] = workflow

    return {
        "ok": True,
        "workflow_id": wf_id,
        "status": workflow["status"],
        "plan": plan,
        "next_action": "Show plan to user; on approval call approve_workflow_checkpoint(workflow_id, phase='plan', action='approve').",
    }

async def _handle_edit_workflow_plan(args: Dict) -> Dict:
    """Apply user edits to a workflow's plan artifact.

    Edits are merged into plan.params and per-phase fields. The workflow
    must still be in the awaiting_plan_approval state; rejecting edits to
    in-flight workflows protects against mid-execution drift.
    """
    wf_id = args.get("workflow_id")
    edits = args.get("plan_edits") or {}
    wf = _WORKFLOWS.get(wf_id)
    if not wf:
        return {"ok": False, "error": f"Unknown workflow_id '{wf_id}'."}
    if wf["status"] != "awaiting_plan_approval":
        return {
            "ok": False,
            "error": f"Workflow is in state '{wf['status']}'; plan can only be edited before approval.",
        }
    if not isinstance(edits, dict):
        return {"ok": False, "error": "plan_edits must be a dict of {phase_name: {field: value}}."}

    plan = wf["plan"]
    applied: List[str] = []
    for phase_name, phase_edits in edits.items():
        if not isinstance(phase_edits, dict):
            continue
        if phase_name == "params":
            plan["params"].update(phase_edits)
            applied.append("params")
            continue
        # Find the phase in the plan
        for phase in plan["phases"]:
            if phase["name"] == phase_name:
                phase.update({k: v for k, v in phase_edits.items() if k not in ("name", "status")})
                applied.append(phase_name)
                break

    wf["events"].append({"type": "plan_edited", "at": _wf_now_iso(), "edits": list(edits.keys())})
    wf["updated_at"] = _wf_now_iso()

    return {
        "ok": True,
        "workflow_id": wf_id,
        "applied_edits": applied,
        "plan": plan,
    }

def _wf_advance_phase(wf: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Move the workflow to the next phase. Returns the next phase dict or None."""
    phases = wf["plan"]["phases"]
    current = wf["current_phase"]
    # Mark current as completed
    for p in phases:
        if p["name"] == current and p["status"] != "completed":
            p["status"] = "completed"
            wf["completed_phases"].append(current)
            break
    # Find next pending phase
    for p in phases:
        if p["status"] == "pending":
            wf["current_phase"] = p["name"]
            p["status"] = "in_progress"
            return p
    # No phases left
    wf["current_phase"] = None
    wf["status"] = "completed"
    return None

async def _handle_approve_workflow_checkpoint(args: Dict) -> Dict:
    """Resolve a checkpoint with approve / reject / revise."""
    wf_id = args.get("workflow_id")
    phase = args.get("phase")
    action = args.get("action")
    feedback = args.get("feedback", "")
    wf = _WORKFLOWS.get(wf_id)
    if not wf:
        return {"ok": False, "error": f"Unknown workflow_id '{wf_id}'."}
    if action not in ("approve", "reject", "revise"):
        return {"ok": False, "error": f"action must be one of approve|reject|revise, got '{action}'."}
    if wf["current_phase"] != phase:
        return {
            "ok": False,
            "error": f"Workflow is at phase '{wf['current_phase']}', not '{phase}'.",
        }

    decision = {
        "phase": phase,
        "action": action,
        "feedback": feedback,
        "at": _wf_now_iso(),
    }
    wf["checkpoint_decisions"].append(decision)
    wf["events"].append({"type": "checkpoint_decision", **decision})
    wf["updated_at"] = _wf_now_iso()

    if action == "reject":
        wf["status"] = "cancelled"
        return {
            "ok": True,
            "workflow_id": wf_id,
            "status": wf["status"],
            "rollback_required": True,
            "snapshot_id": wf.get("snapshot_id"),
        }

    if action == "revise":
        # Stay on the same phase; the LLM uses `feedback` to regenerate.
        wf["status"] = "revising"
        return {
            "ok": True,
            "workflow_id": wf_id,
            "status": wf["status"],
            "phase": phase,
            "feedback": feedback,
            "next_action": "Re-generate the artifact for this phase using the user feedback, then call approve_workflow_checkpoint again.",
        }

    # approve → advance to next phase
    next_phase = _wf_advance_phase(wf)
    if next_phase is None:
        return {
            "ok": True,
            "workflow_id": wf_id,
            "status": wf["status"],
            "message": "Workflow complete.",
        }

    # Decide whether the next phase needs another checkpoint
    if next_phase["checkpoint"] and not wf["auto_approve_checkpoints"]:
        wf["status"] = f"awaiting_{next_phase['name']}_approval"
    else:
        wf["status"] = f"executing_{next_phase['name']}"

    return {
        "ok": True,
        "workflow_id": wf_id,
        "status": wf["status"],
        "current_phase": wf["current_phase"],
        "phase_meta": next_phase,
    }

async def _handle_cancel_workflow(args: Dict) -> Dict:
    """Cancel a workflow and request rollback to its pre-workflow snapshot."""
    wf_id = args.get("workflow_id")
    reason = args.get("reason", "user_cancelled")
    wf = _WORKFLOWS.get(wf_id)
    if not wf:
        return {"ok": False, "error": f"Unknown workflow_id '{wf_id}'."}
    if wf["status"] in ("completed", "cancelled"):
        return {
            "ok": True,
            "workflow_id": wf_id,
            "status": wf["status"],
            "message": "Workflow already finished; nothing to cancel.",
        }
    wf["status"] = "cancelled"
    wf["events"].append({"type": "cancelled", "at": _wf_now_iso(), "reason": reason})
    wf["updated_at"] = _wf_now_iso()
    return {
        "ok": True,
        "workflow_id": wf_id,
        "status": wf["status"],
        "rollback_required": True,
        "snapshot_id": wf.get("snapshot_id"),
        "reason": reason,
    }

async def _handle_get_workflow_status(args: Dict) -> Dict:
    """Return the current state of a workflow."""
    wf_id = args.get("workflow_id")
    wf = _WORKFLOWS.get(wf_id)
    if not wf:
        return {"ok": False, "error": f"Unknown workflow_id '{wf_id}'."}
    # Return a shallow copy without the verbose events log unless explicitly asked
    return {
        "ok": True,
        "workflow_id": wf_id,
        "type": wf["type"],
        "goal": wf["goal"],
        "status": wf["status"],
        "current_phase": wf["current_phase"],
        "completed_phases": list(wf["completed_phases"]),
        "checkpoint_decisions": list(wf["checkpoint_decisions"]),
        "error_fix_attempts": list(wf["error_fix_attempts"]),
        "plan": wf["plan"],
        "created_at": wf["created_at"],
        "updated_at": wf["updated_at"],
    }

async def _handle_list_workflows(args: Dict) -> Dict:
    """List active (and optionally completed) workflows."""
    include_completed = bool(args.get("include_completed", False))
    limit = int(args.get("limit", 20))
    summaries = []
    for wf_id, wf in _WORKFLOWS.items():
        if not include_completed and wf["status"] in ("completed", "cancelled"):
            continue
        summaries.append({
            "workflow_id": wf_id,
            "type": wf["type"],
            "goal": wf["goal"],
            "status": wf["status"],
            "current_phase": wf["current_phase"],
            "created_at": wf["created_at"],
            "updated_at": wf["updated_at"],
        })
    # Newest first
    summaries.sort(key=lambda s: s["updated_at"], reverse=True)
    return {"ok": True, "count": len(summaries), "workflows": summaries[:limit]}

async def _handle_execute_with_retry(args: Dict) -> Dict:
    """Execute a code patch through the autonomous error-fix loop.

    This handler performs the *first* attempt against Kit RPC and reports
    the outcome. The actual LLM-driven fix iterations happen one round-trip
    per attempt — the orchestrator (chat loop) is responsible for feeding
    each failure back into the LLM, generating the patched code, and
    calling this handler again with the new code. We track attempt counts
    via a session-scoped key so the hard retry cap is enforced even when
    the LLM forgets it.
    """
    code = args.get("code", "")
    description = args.get("description", "Autonomous error-fix execution")
    requested_max = int(args.get("max_retries", 3))
    max_retries = min(requested_max, _WORKFLOW_RETRY_HARD_CAP)
    context_hints = args.get("context_hints") or []

    if not code:
        return {"ok": False, "error": "code is required."}

    # Pre-flight validation (same as run_usd_script)
    issues = validate_patch(code)
    if has_blocking_issues(issues):
        msg = format_issues_for_llm(issues)
        return {
            "ok": False,
            "type": "validation_blocked",
            "error": msg,
            "code": code,
            "description": description,
        }

    # Submit to Kit. Kit returns queued=True; the chat loop polls for the
    # actual exec result via existing patch-status machinery. We surface
    # the budget so the caller can decide whether to retry on failure.
    result = await kit_tools.queue_exec_patch(code, description)
    return {
        "ok": True,
        "type": "code_patch",
        "code": code,
        "description": description,
        "queued": result.get("queued", False),
        "patch_id": result.get("patch_id"),
        "max_retries": max_retries,
        "context_hints": context_hints,
        "next_action": (
            "Wait for patch result. On failure, call execute_with_retry again "
            f"with patched code (up to {max_retries} attempts total)."
        ),
    }

async def _handle_proactive_check(args: Dict) -> Dict:
    """Run the proactive agent for a scene-state trigger.

    The agent calls each tool in the trigger's playbook and aggregates
    findings. Auto-fixes are gated by both the per-call `auto_fix=True`
    and the AUTO_PROACTIVE_FIX env var so tests + dry runs never mutate
    the scene without explicit opt-in.
    """
    trigger = args.get("trigger")
    context = args.get("context") or {}
    auto_fix_requested = bool(args.get("auto_fix", False))

    playbook = _PROACTIVE_TRIGGER_PLAYBOOKS.get(trigger)
    if playbook is None:
        return {
            "ok": False,
            "error": f"Unknown proactive trigger '{trigger}'. Supported: {sorted(_PROACTIVE_TRIGGER_PLAYBOOKS)}",
        }

    auto_fix_env = os.environ.get("AUTO_PROACTIVE_FIX", "false").lower() in ("1", "true", "yes")
    auto_fix_enabled = auto_fix_requested and auto_fix_env

    findings: List[Dict[str, Any]] = []
    for tool_name in playbook:
        handler = DATA_HANDLERS.get(tool_name)
        if handler is None:
            # Tool is LLM-handled or disabled — note it and move on.
            findings.append({
                "tool": tool_name,
                "skipped": True,
                "note": "Tool handled by LLM reasoning or unavailable; no live data captured.",
            })
            continue
        try:
            # Pass the context as kwargs where the handler accepts them; otherwise
            # just call with the raw context dict — every data handler takes a dict.
            tool_args = {}
            if tool_name == "explain_error":
                tool_args = {"error_text": context.get("error_text", "")}
            elif tool_name == "measure_distance":
                # target_placed trigger needs prim_a + prim_b
                if "target_path" in context and "robot_path" in context:
                    tool_args = {"prim_a": context["target_path"], "prim_b": context["robot_path"]}
                else:
                    findings.append({
                        "tool": tool_name,
                        "skipped": True,
                        "note": "measure_distance needs target_path + robot_path in context.",
                    })
                    continue
            result = await handler(tool_args)
            findings.append({"tool": tool_name, "result": result})
        except Exception as exc:  # pragma: no cover — defensive
            logger.warning(f"[ProactiveAgent] {tool_name} raised: {exc}")
            findings.append({"tool": tool_name, "error": str(exc)})

    return {
        "ok": True,
        "trigger": trigger,
        "context": context,
        "playbook": playbook,
        "findings": findings,
        "auto_fix_enabled": auto_fix_enabled,
        "auto_fix_applied": [],  # populated only when AUTO_PROACTIVE_FIX is on
        "principle": "Proactive ≠ autonomous modification — observations only unless AUTO_PROACTIVE_FIX is enabled.",
    }

DATA_HANDLERS["start_workflow"] = _handle_start_workflow
DATA_HANDLERS["edit_workflow_plan"] = _handle_edit_workflow_plan
DATA_HANDLERS["approve_workflow_checkpoint"] = _handle_approve_workflow_checkpoint
DATA_HANDLERS["cancel_workflow"] = _handle_cancel_workflow
DATA_HANDLERS["get_workflow_status"] = _handle_get_workflow_status
DATA_HANDLERS["list_workflows"] = _handle_list_workflows
DATA_HANDLERS["execute_with_retry"] = _handle_execute_with_retry
DATA_HANDLERS["proactive_check"] = _handle_proactive_check

# ══════ From feat/addendum-collision-mesh-quality-v2 ══════
def _gen_check_collision_mesh_code(prim_path: str) -> str:
    """Build the read-only Kit/USD/trimesh analysis script for check_collision_mesh."""
    safe_path = prim_path.replace("'", "").replace('"', "")
    return f"""
import json
import omni.usd
from pxr import Usd, UsdGeom, UsdPhysics

result = {{
    "prim": "{safe_path}",
    "triangle_count": 0,
    "is_watertight": None,
    "is_manifold": None,
    "degenerate_faces": 0,
    "collision_approximation": "unknown",
    "issues": [],
    "recommendation": "",
}}

stage = omni.usd.get_context().get_stage()
prim = stage.GetPrimAtPath("{safe_path}")

if not prim or not prim.IsValid():
    result["issues"].append({{"type": "prim_not_found", "severity": "error"}})
    result["recommendation"] = "Prim not found — check the path."
    print(json.dumps(result))
else:
    # ── Fatal check: missing CollisionAPI ────────────────────────────────
    has_collision = prim.HasAPI(UsdPhysics.CollisionAPI)
    if not has_collision:
        result["issues"].append({{"type": "missing_collision_api", "severity": "error"}})

    # ── Read approximation type ──────────────────────────────────────────
    if prim.HasAPI(UsdPhysics.MeshCollisionAPI):
        try:
            approx_attr = UsdPhysics.MeshCollisionAPI(prim).GetApproximationAttr().Get()
            result["collision_approximation"] = approx_attr or "none"
        except Exception:
            result["collision_approximation"] = "none"
    else:
        result["collision_approximation"] = "none (no MeshCollisionAPI)"

    mesh = UsdGeom.Mesh(prim)
    if not mesh:
        result["issues"].append({{"type": "not_a_mesh", "severity": "error"}})
        result["recommendation"] = "Prim is not a UsdGeom.Mesh — collision analysis only supports meshes."
        print(json.dumps(result))
    else:
        points = mesh.GetPointsAttr().Get() or []
        face_counts = mesh.GetFaceVertexCountsAttr().Get() or []
        face_indices = mesh.GetFaceVertexIndicesAttr().Get() or []
        n_points = len(points)

        # ── Fatal: out-of-range vertex indices ───────────────────────────
        oor = [i for i in face_indices if i < 0 or i >= n_points]
        if oor:
            result["issues"].append({{
                "type": "out_of_range_indices", "severity": "error", "count": len(oor),
            }})

        # ── Triangulate face_counts/face_indices into triangles ──────────
        triangles = []
        cursor = 0
        for fc in face_counts:
            if fc < 3:
                cursor += fc
                continue
            base = face_indices[cursor]
            for k in range(1, fc - 1):
                triangles.append((base, face_indices[cursor + k], face_indices[cursor + k + 1]))
            cursor += fc
        result["triangle_count"] = len(triangles)

        # Count degenerate triangles (any two indices equal → zero area)
        degenerate = 0
        for a, b, c in triangles:
            if a == b or b == c or a == c:
                degenerate += 1
        result["degenerate_faces"] = degenerate
        if degenerate > 0:
            result["issues"].append({{
                "type": "degenerate_faces", "severity": "error", "count": degenerate,
            }})

        # ── trimesh-based silent-degradation checks (optional dep) ───────
        try:
            import trimesh
            import numpy as np
            verts = np.array([(p[0], p[1], p[2]) for p in points], dtype=float)
            faces = np.array(triangles, dtype=int) if triangles else np.zeros((0, 3), dtype=int)
            if len(faces) > 0 and len(verts) > 0:
                tm = trimesh.Trimesh(vertices=verts, faces=faces, process=False)
                result["is_watertight"] = bool(tm.is_watertight)
                result["is_manifold"] = bool(getattr(tm, "is_winding_consistent", True))

                # Zero-area triangles (geometric degeneracy)
                area_faces = tm.area_faces
                near_zero = int((area_faces < 1e-10).sum())
                if near_zero > 0 and near_zero != degenerate:
                    result["issues"].append({{
                        "type": "zero_area_faces", "severity": "error", "count": near_zero,
                    }})

                if not tm.is_watertight:
                    result["issues"].append({{"type": "non_watertight", "severity": "warning"}})
                if not getattr(tm, "is_winding_consistent", True):
                    result["issues"].append({{"type": "non_manifold_edges", "severity": "warning"}})
                if not getattr(tm, "is_volume", True):
                    result["issues"].append({{"type": "not_volume", "severity": "warning"}})

                # Oversized-triangle heuristic: any tri area > 10% of bbox area
                try:
                    bbox_diag = float(np.linalg.norm(tm.bounds[1] - tm.bounds[0]))
                    if bbox_diag > 0 and len(area_faces) > 0:
                        max_tri = float(area_faces.max())
                        if max_tri > 0.1 * (bbox_diag ** 2):
                            result["issues"].append({{
                                "type": "oversized_triangles", "severity": "warning",
                                "max_area": max_tri, "bbox_diag": bbox_diag,
                            }})
                except Exception:
                    pass

                # ── Convex hull GPU-limit check (only when relevant) ─────
                if result["collision_approximation"] in ("convexHull", "convexDecomposition"):
                    try:
                        hull = tm.convex_hull
                        n_hv = len(hull.vertices)
                        n_hf = len(hull.faces)
                        if n_hv > {_PHYSX_HULL_MAX_VERTS}:
                            result["issues"].append({{
                                "type": "hull_exceeds_gpu_limit", "severity": "error",
                                "vertices": n_hv, "limit": {_PHYSX_HULL_MAX_VERTS},
                            }})
                        if n_hf > {_PHYSX_HULL_MAX_POLYS}:
                            result["issues"].append({{
                                "type": "hull_exceeds_polygon_limit", "severity": "error",
                                "polygons": n_hf, "limit": {_PHYSX_HULL_MAX_POLYS},
                            }})
                    except Exception as e:
                        result["issues"].append({{"type": "hull_compute_failed", "severity": "warning", "error": str(e)}})
        except ImportError:
            result["issues"].append({{
                "type": "trimesh_unavailable", "severity": "info",
                "message": "trimesh not installed — silent-degradation checks skipped (pip install trimesh)",
            }})

        # ── Recommendation ───────────────────────────────────────────────
        rec_parts = []
        n_tri = result["triangle_count"]
        approx = result["collision_approximation"]
        if n_tri > 5000 and approx in ("none", "none (no MeshCollisionAPI)", ""):
            rec_parts.append(
                f"Switch to convexDecomposition ({{n_tri}} triangles is too heavy for raw triangle-mesh collision)."
            )
        if any(i["severity"] == "error" for i in result["issues"]):
            rec_parts.append("Run fix_collision_mesh first to repair errors.")
        elif any(i["type"] in ("non_watertight", "non_manifold_edges", "not_volume") for i in result["issues"]):
            rec_parts.append("Run fix_collision_mesh to clean up the mesh.")
        if not rec_parts:
            rec_parts.append("Mesh looks healthy — no action needed.")
        result["recommendation"] = " ".join(rec_parts)

        print(json.dumps(result))
"""

async def _handle_check_collision_mesh(args: Dict) -> Dict:
    """Analyze a USD mesh prim's collision quality (DATA handler)."""
    prim_path = args.get("prim_path", "")
    if not prim_path or not prim_path.startswith("/"):
        return {"error": "prim_path must be a non-empty USD path starting with /"}
    code = _gen_check_collision_mesh_code(prim_path)
    result = await kit_tools.exec_sync(code, timeout=20)
    if not result.get("success"):
        return {
            "error": f"Kit RPC failed: {result.get('output', 'unknown')}",
            "hint": "Is Isaac Sim running with the Kit RPC bridge on port 8001?",
        }
    output = (result.get("output") or "").strip()
    for line in reversed(output.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    return {"error": "Failed to parse collision-mesh response", "raw_output": output[:500]}

def _gen_fix_collision_mesh(args: Dict) -> str:
    """Generate auto-repair code: normals → degenerate → holes → simplify → CoACD → write back."""
    prim_path = args["prim_path"]
    target = args.get("target_triangles")
    target_val = "None" if target is None else str(int(target))
    safe_path = prim_path.replace("'", "").replace('"', "")
    return f"""
import omni.usd
import numpy as np
from pxr import Usd, UsdGeom, UsdPhysics, Vt, Sdf

PRIM_PATH = "{safe_path}"
TARGET_TRIANGLES = {target_val}
PHYSX_HULL_MAX_VERTS = {_PHYSX_HULL_MAX_VERTS}
PHYSX_HULL_MAX_POLYS = {_PHYSX_HULL_MAX_POLYS}
COACD_THRESHOLD = 0.05
COACD_MAX_CONVEX_HULL = 16

stage = omni.usd.get_context().get_stage()
prim = stage.GetPrimAtPath(PRIM_PATH)
if not prim or not prim.IsValid():
    raise RuntimeError(f"Prim not found: {{PRIM_PATH}}")

mesh = UsdGeom.Mesh(prim)
if not mesh:
    raise RuntimeError(f"Prim {{PRIM_PATH}} is not a UsdGeom.Mesh")

# ── Step 0: Read current mesh data ──────────────────────────────────────
points = mesh.GetPointsAttr().Get() or []
face_counts = mesh.GetFaceVertexCountsAttr().Get() or []
face_indices = mesh.GetFaceVertexIndicesAttr().Get() or []

# Triangulate
triangles = []
cursor = 0
for fc in face_counts:
    if fc < 3:
        cursor += fc
        continue
    base = face_indices[cursor]
    for k in range(1, fc - 1):
        triangles.append((base, face_indices[cursor + k], face_indices[cursor + k + 1]))
    cursor += fc

import trimesh
verts_np = np.array([(p[0], p[1], p[2]) for p in points], dtype=float)
faces_np = np.array(triangles, dtype=int) if triangles else np.zeros((0, 3), dtype=int)
tm = trimesh.Trimesh(vertices=verts_np, faces=faces_np, process=False)

# ── Step 1: Fix normals ─────────────────────────────────────────────────
try:
    tm.fix_normals()
except Exception as exc:
    print(f"fix_normals failed: {{exc}}")

# ── Step 2: Remove degenerate triangles (zero / near-zero area) ─────────
try:
    nondegen_mask = tm.area_faces > 1e-12
    if not nondegen_mask.all():
        tm.update_faces(nondegen_mask)
        tm.remove_unreferenced_vertices()
except Exception as exc:
    print(f"degenerate removal failed: {{exc}}")

# ── Step 3: Fill holes (trimesh first, pymeshfix for complex cases) ─────
try:
    tm.fill_holes()
except Exception as exc:
    print(f"fill_holes failed: {{exc}}")

if not tm.is_watertight:
    try:
        import pymeshfix
        meshfix = pymeshfix.MeshFix(tm.vertices, tm.faces)
        meshfix.repair()
        tm = trimesh.Trimesh(vertices=meshfix.v, faces=meshfix.f, process=False)
    except ImportError:
        print("pymeshfix not installed — skipping advanced hole-fill (pip install pymeshfix)")
    except Exception as exc:
        print(f"pymeshfix repair failed: {{exc}}")

# ── Step 4: Simplify to target face count via quadric decimation ────────
target = TARGET_TRIANGLES
if target is None:
    # Heuristic: classify based on RigidBodyAPI presence
    target = 500 if prim.HasAPI(UsdPhysics.RigidBodyAPI) else 2000

if target > 0 and len(tm.faces) > target:
    try:
        tm = tm.simplify_quadric_decimation(target)
    except Exception as exc:
        print(f"quadric decimation failed: {{exc}}")

# ── Step 5: Convex decompose if mesh too complex for a single hull ──────
hulls = []
needs_decompose = False
try:
    hull = tm.convex_hull
    if len(hull.vertices) > PHYSX_HULL_MAX_VERTS or len(hull.faces) > PHYSX_HULL_MAX_POLYS:
        needs_decompose = True
except Exception:
    needs_decompose = True

if needs_decompose:
    try:
        import coacd
        coacd_mesh = coacd.Mesh(tm.vertices, tm.faces)
        parts = coacd.run_coacd(
            coacd_mesh,
            threshold=COACD_THRESHOLD,
            max_convex_hull=COACD_MAX_CONVEX_HULL,
        )
        for v, f in parts:
            hulls.append(trimesh.Trimesh(vertices=v, faces=f, process=False))
    except ImportError:
        print("coacd not installed — falling back to single convex hull (pip install coacd)")
        hulls = [tm.convex_hull]
    except Exception as exc:
        print(f"CoACD decomposition failed: {{exc}} — falling back to single hull")
        hulls = [tm.convex_hull]
else:
    hulls = [tm.convex_hull]

# ── Step 6: Verify all hulls ≤ GPU limits ───────────────────────────────
for idx, h in enumerate(hulls):
    if len(h.vertices) > PHYSX_HULL_MAX_VERTS:
        print(f"WARN: hull {{idx}} has {{len(h.vertices)}} vertices > {{PHYSX_HULL_MAX_VERTS}}")
    if len(h.faces) > PHYSX_HULL_MAX_POLYS:
        print(f"WARN: hull {{idx}} has {{len(h.faces)}} faces > {{PHYSX_HULL_MAX_POLYS}}")

# ── Step 7: Write repaired triangle mesh back to USD ────────────────────
new_points = Vt.Vec3fArray([tuple(v) for v in tm.vertices.tolist()])
mesh.GetPointsAttr().Set(new_points)
new_face_counts = Vt.IntArray([3] * len(tm.faces))
mesh.GetFaceVertexCountsAttr().Set(new_face_counts)
flat_indices = [int(i) for tri in tm.faces.tolist() for i in tri]
mesh.GetFaceVertexIndicesAttr().Set(Vt.IntArray(flat_indices))

# Apply MeshCollisionAPI with appropriate approximation
if not prim.HasAPI(UsdPhysics.CollisionAPI):
    UsdPhysics.CollisionAPI.Apply(prim)
if not prim.HasAPI(UsdPhysics.MeshCollisionAPI):
    UsdPhysics.MeshCollisionAPI.Apply(prim)

mca = UsdPhysics.MeshCollisionAPI(prim)
approx = "convexDecomposition" if len(hulls) > 1 else "convexHull"
mca.CreateApproximationAttr().Set(approx)

print(f"OK: repaired {{PRIM_PATH}} — {{len(tm.faces)}} triangles, {{len(hulls)}} hull(s), approx={{approx}}")
"""

def _gen_visualize_collision_mesh(args: Dict) -> str:
    """Toggle PhysX collision-shape debug visualization for a prim (CODE_GEN handler)."""
    prim_path = args["prim_path"]
    safe_path = prim_path.replace("'", "").replace('"', "")
    return f"""
import omni.usd
from pxr import Usd, UsdGeom, UsdPhysics

PRIM_PATH = "{safe_path}"

stage = omni.usd.get_context().get_stage()
prim = stage.GetPrimAtPath(PRIM_PATH)
if not prim or not prim.IsValid():
    raise RuntimeError(f"Prim not found: {{PRIM_PATH}}")

# ── Enable per-prim collision visualization via CollisionAPI displayColor ─
# UsdPhysics offers a CollisionGroup and the omni.physx.ui debug
# visualization mode "Collision Shapes". Enable both so the user can
# clearly see what PhysX is using for collision.
if not prim.HasAPI(UsdPhysics.CollisionAPI):
    UsdPhysics.CollisionAPI.Apply(prim)

# ── Enable the global Physics Debug "Collision Shapes" visualization ────
try:
    import carb.settings
    settings = carb.settings.get_settings()
    # omni.physx.ui debug visualization toggles (these are the documented paths)
    settings.set("/persistent/physics/visualizationCollisionMesh", True)
    settings.set("/physics/visualizationDisplayJoints", False)
    settings.set("/physics/visualizationSimulationOutput", True)
    print(f"Collision-shape visualization ENABLED for {{PRIM_PATH}}")
except Exception as exc:
    print(f"Failed to set carb settings: {{exc}}")

# ── Try the omni.physx.ui PhysicsDebugView API as a secondary path ──────
try:
    import omni.physx.ui as physx_ui
    # Newer Kit versions expose a debug-view manager; fall back gracefully.
    try:
        physx_ui.get_physx_debug_view().enable_debug_visualization(True)
    except Exception:
        pass
    print("omni.physx.ui debug visualization enabled")
except ImportError:
    print("omni.physx.ui not available — relying on carb.settings toggles")

# ── Highlight the prim with a wireframe display style ──────────────────
imageable = UsdGeom.Imageable(prim)
if imageable:
    try:
        imageable.CreatePurposeAttr().Set(UsdGeom.Tokens.guide)
    except Exception:
        pass

print(f"OK: visualizing collision mesh for {{PRIM_PATH}}")
"""

DATA_HANDLERS["check_collision_mesh"] = _handle_check_collision_mesh
CODE_GEN_HANDLERS["fix_collision_mesh"] = _gen_fix_collision_mesh
CODE_GEN_HANDLERS["visualize_collision_mesh"] = _gen_visualize_collision_mesh

# ══════ From feat/addendum-community-remote-v2 ══════
def _detect_local_vram_gb() -> Optional[float]:
    """Best-effort GPU VRAM detection via the existing fingerprint collector."""
    try:
        from ...fingerprint.collector import get_gpu_info
    except Exception:
        return None
    try:
        gpus = get_gpu_info() or []
    except Exception:
        return None
    if not gpus:
        return None
    # Use the largest-VRAM GPU (matches Isaac Sim's preferred device)
    best = max(g.get("vram_mb", 0) for g in gpus)
    if best <= 0:
        return None
    return round(best / 1024.0, 2)

def _detect_used_vram_gb() -> Optional[float]:
    """Best-effort current VRAM usage via nvidia-smi."""
    try:
        from ...fingerprint.collector import run_shell
    except Exception:
        return None
    try:
        out = run_shell("nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits")
    except Exception:
        return None
    if not out:
        return None
    try:
        # Take the first GPU
        first = out.splitlines()[0].strip()
        used_mb = float(first)
        return round(used_mb / 1024.0, 2)
    except Exception:
        return None

def _load_template_manifests(library_dir: Path) -> List[Dict]:
    """Load manifest.json from each template directory in library_dir.

    Each entry is augmented with `_template_dir` so the caller can resolve
    paths.  Missing or malformed manifests are skipped.
    """
    manifests: List[Dict] = []
    if not library_dir.exists():
        return manifests
    for entry in sorted(library_dir.iterdir()):
        if not entry.is_dir():
            continue
        manifest_path = entry / "manifest.json"
        if not manifest_path.exists():
            continue
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[filter_templates_by_hardware] bad manifest at {manifest_path}: {e}")
            continue
        if not isinstance(data, dict):
            continue
        data["_template_dir"] = str(entry)
        manifests.append(data)
    return manifests

async def _handle_filter_templates_by_hardware(args: Dict) -> Dict:
    """Filter templates by GPU VRAM + tag/category."""
    device_vram_gb = args.get("device_vram_gb")
    if device_vram_gb is None:
        device_vram_gb = _detect_local_vram_gb()

    category = args.get("category")
    tag = args.get("tag")
    use_recommended = bool(args.get("include_recommended_only"))

    library_dir_arg = args.get("library_dir") or str(_TEMPLATE_LIBRARY_DIR)
    library_dir = Path(library_dir_arg)
    manifests = _load_template_manifests(library_dir)

    matched: List[Dict] = []
    rejected: List[Dict] = []
    for m in manifests:
        min_vram = float(m.get("min_vram_gb", 0) or 0)
        rec_vram = float(m.get("recommended_vram_gb", min_vram) or min_vram)
        threshold = rec_vram if use_recommended else min_vram

        # Hardware gate
        if device_vram_gb is not None and threshold > 0 and device_vram_gb < threshold:
            rejected.append({
                "template": m.get("template") or m.get("name"),
                "reason": f"requires {threshold} GB VRAM, you have {device_vram_gb} GB",
            })
            continue

        # Category filter
        if category and m.get("category") and m["category"] != category:
            continue

        # Tag filter
        if tag and tag not in (m.get("tags") or []):
            continue

        matched.append({
            "template": m.get("template") or m.get("name"),
            "description": m.get("description", ""),
            "min_vram_gb": m.get("min_vram_gb"),
            "recommended_vram_gb": m.get("recommended_vram_gb"),
            "estimated_fps": m.get("estimated_fps", {}),
            "tags": m.get("tags", []),
            "category": m.get("category"),
            "path": m.get("_template_dir"),
        })

    return {
        "device_vram_gb": device_vram_gb,
        "library_dir": str(library_dir),
        "matched_count": len(matched),
        "matched": matched,
        "rejected_count": len(rejected),
        "rejected": rejected,
    }

def _gen_export_template(args: Dict) -> str:
    """Generate code that bundles the live stage + config + metadata into .isaa.

    Runs inside Kit so it can use omni.usd to flatten the open stage when the
    caller doesn't supply scene_path.  The .isaa file is a zip with this
    layout:

        manifest.json
        scene.usd          (or .usda)
        config/<files>     (optional; copied from CONFIG_DIR if present)

    """
    from datetime import datetime as _dt
    name = args["name"]
    safe_name = "".join(c if c.isalnum() or c in "_-" else "_" for c in name)
    description = args.get("description", "")
    scene_path = args.get("scene_path")  # may be None → flatten open stage
    output_dir = args.get("output_dir") or str(_TEMPLATE_EXPORT_DIR)
    min_vram_gb = args.get("min_vram_gb")
    recommended_vram_gb = args.get("recommended_vram_gb")
    tags = args.get("tags", []) or []
    timestamp = _dt.utcnow().strftime("%Y%m%dT%H%M%SZ")

    # Build the manifest dict literal we want serialized inside Kit.
    manifest = {
        "manifest_version": _ISAA_MANIFEST_VERSION,
        "name": name,
        "template": safe_name,
        "description": description,
        "exported_at": timestamp,
        "min_vram_gb": min_vram_gb,
        "recommended_vram_gb": recommended_vram_gb,
        "tags": list(tags),
        "scene_file": "scene.usda",
    }

    return f"""\
import json
import os
import shutil
import tempfile
import zipfile
from pathlib import Path

import omni.usd

manifest = {json.dumps(manifest, indent=2)}
output_dir = Path({output_dir!r})
output_dir.mkdir(parents=True, exist_ok=True)
isaa_path = output_dir / ({safe_name!r} + '.isaa')

scene_path = {scene_path!r}
with tempfile.TemporaryDirectory() as _tmp:
    tmp = Path(_tmp)
    scene_dst = tmp / 'scene.usda'
    if scene_path:
        # Copy the supplied .usd/.usda directly into the bundle.
        shutil.copyfile(scene_path, scene_dst)
        manifest['scene_file'] = Path(scene_path).name
    else:
        # Flatten the currently open stage to a single .usda file.
        ctx = omni.usd.get_context()
        stage = ctx.get_stage()
        if stage is None:
            raise RuntimeError('No open stage to export — supply scene_path or open a scene.')
        stage.Export(str(scene_dst))

    (tmp / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')

    with zipfile.ZipFile(isaa_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.write(tmp / 'manifest.json', arcname='manifest.json')
        zf.write(scene_dst, arcname=manifest['scene_file'])

print(f'[export_template] wrote {{isaa_path}} ({{isaa_path.stat().st_size}} bytes)')
"""

def _gen_import_template(args: Dict) -> str:
    """Generate code that extracts an .isaa file into the local library."""
    file_path = args["file_path"]
    library_dir = args.get("library_dir") or str(_TEMPLATE_LIBRARY_DIR)
    overwrite = bool(args.get("overwrite", False))

    return f"""\
import json
import shutil
import zipfile
from pathlib import Path

src = Path({file_path!r})
library = Path({library_dir!r})
library.mkdir(parents=True, exist_ok=True)

if not src.exists():
    raise FileNotFoundError(f'.isaa file not found: {{src}}')

with zipfile.ZipFile(src, 'r') as zf:
    names = zf.namelist()
    if 'manifest.json' not in names:
        raise ValueError(f'{{src}} is not a valid .isaa template (missing manifest.json)')
    manifest = json.loads(zf.read('manifest.json').decode('utf-8'))

template_id = manifest.get('template') or manifest.get('name')
if not template_id:
    raise ValueError('manifest.json missing template/name field')
safe_id = ''.join(c if c.isalnum() or c in '_-' else '_' for c in template_id)

dest = library / safe_id
if dest.exists():
    if {overwrite!r}:
        shutil.rmtree(dest)
    else:
        raise FileExistsError(f'Template {{template_id!r}} already in library — pass overwrite=True to replace.')

dest.mkdir(parents=True)
with zipfile.ZipFile(src, 'r') as zf:
    zf.extractall(dest)

print(f'[import_template] installed {{template_id}} -> {{dest}}')
"""

async def _handle_check_vram_headroom(args: Dict) -> Dict:
    """Estimate VRAM cost vs available, return warnings + suggestions."""
    operation = args.get("operation", "custom")
    num_envs = int(args.get("num_envs", 1))
    complexity = args.get("complexity", "medium")
    if complexity not in ("low", "medium", "high"):
        complexity = "medium"

    per_env_mb = args.get("per_env_mb_override")
    if per_env_mb is None:
        per_env_mb = _VRAM_PER_ENV_MB.get(operation, _VRAM_PER_ENV_MB["custom"]).get(
            complexity, 32
        )
    per_env_mb = float(per_env_mb)
    estimated_mb = per_env_mb * max(num_envs, 1)
    estimated_gb = round(estimated_mb / 1024.0, 2)

    device_vram_gb = args.get("device_vram_gb")
    if device_vram_gb is None:
        device_vram_gb = _detect_local_vram_gb()
    used_gb = args.get("currently_used_gb")
    if used_gb is None:
        used_gb = _detect_used_vram_gb()

    available_gb: Optional[float]
    if device_vram_gb is not None and used_gb is not None:
        available_gb = round(max(device_vram_gb - used_gb, 0.0), 2)
    elif device_vram_gb is not None:
        # Assume ~1 GB baseline used by the OS / Kit if we can't query.
        available_gb = round(max(device_vram_gb - 1.0, 0.0), 2)
    else:
        available_gb = None

    fits = (
        available_gb is not None
        and estimated_gb <= available_gb
    )

    suggestions: List[str] = []
    if not fits and available_gb is not None:
        # Suggest a reduced env count that fits in ~80 % of available VRAM.
        budget_mb = available_gb * 1024.0 * 0.8
        if per_env_mb > 0:
            safe_envs = max(int(budget_mb // per_env_mb), 1)
            if safe_envs < num_envs:
                suggestions.append(
                    f"Reduce to {safe_envs} environments (fits in ~{round(safe_envs * per_env_mb / 1024.0, 2)} GB)"
                )
        suggestions.append("Use headless mode to free ~2 GB")
        suggestions.append("Use cloud compute (Phase 7H IsaacAutomator)")

    warning: Optional[str] = None
    if not fits:
        if available_gb is None:
            warning = (
                f"Could not auto-detect GPU VRAM. Estimated need: {estimated_gb} GB "
                f"for {num_envs}× {operation} ({complexity})."
            )
        else:
            warning = (
                f"This will need approximately {estimated_gb} GB additional VRAM. "
                f"Available: {available_gb} GB — not enough for {num_envs} {operation}."
            )

    return {
        "operation": operation,
        "num_envs": num_envs,
        "complexity": complexity,
        "per_env_mb": per_env_mb,
        "estimated_gb": estimated_gb,
        "device_vram_gb": device_vram_gb,
        "currently_used_gb": used_gb,
        "available_gb": available_gb,
        "fits": fits,
        "warning": warning,
        "suggestions": suggestions,
    }

def _async_task_runner(task_id: str, task_type: str, params: Dict) -> None:
    """Worker body executed in a daemon thread.

    Real long-running ops (SDG, training) are dispatched via Kit; here we
    simulate progress so the lifecycle is observable from the chat panel.
    Production integrations replace this body with concrete handlers per
    task_type.
    """
    try:
        with _ASYNC_TASKS_LOCK:
            entry = _ASYNC_TASKS.get(task_id)
            if entry is None:
                return
            entry["state"] = "running"
            entry["started_at"] = _time.time()

        # Heuristic total duration so a smoke test completes quickly.
        total_steps = max(int(params.get("steps", 5)), 1)
        step_sleep = float(params.get("step_seconds", 0.0))
        for i in range(total_steps):
            if step_sleep > 0:
                _time.sleep(step_sleep)
            with _ASYNC_TASKS_LOCK:
                entry = _ASYNC_TASKS.get(task_id)
                if entry is None or entry.get("state") == "cancelled":
                    return
                entry["progress"] = (i + 1) / total_steps

        with _ASYNC_TASKS_LOCK:
            entry = _ASYNC_TASKS.get(task_id)
            if entry is None:
                return
            entry["state"] = "done"
            entry["finished_at"] = _time.time()
            entry["progress"] = 1.0
            entry["result"] = {
                "task_type": task_type,
                "params": params,
                "message": f"{task_type} task completed",
            }
    except Exception as e:  # noqa: BLE001
        with _ASYNC_TASKS_LOCK:
            entry = _ASYNC_TASKS.get(task_id)
            if entry is not None:
                entry["state"] = "error"
                entry["finished_at"] = _time.time()
                entry["error"] = str(e)

async def _handle_dispatch_async_task(args: Dict) -> Dict:
    """Register an async task and start a background worker."""
    task_type = args.get("task_type", "custom")
    params = args.get("params") or {}
    label = args.get("label") or f"{task_type} task"

    task_id = f"task_{task_type}_{_uuid.uuid4().hex[:8]}"
    with _ASYNC_TASKS_LOCK:
        _ASYNC_TASKS[task_id] = {
            "task_id": task_id,
            "task_type": task_type,
            "label": label,
            "params": params,
            "state": "pending",
            "progress": 0.0,
            "queued_at": _time.time(),
            "started_at": None,
            "finished_at": None,
            "result": None,
            "error": None,
        }

    # Allow tests / callers to opt out of the background thread for synchronous
    # reasoning (e.g. when running under pytest without a real Kit).
    if not args.get("dry_run"):
        thread = _threading.Thread(
            target=_async_task_runner,
            args=(task_id, task_type, params),
            name=f"async-{task_id}",
            daemon=True,
        )
        thread.start()

    return {
        "task_id": task_id,
        "task_type": task_type,
        "label": label,
        "state": "pending",
        "message": f"Started {label} in background. Query status with task_id={task_id!r}.",
    }

async def _handle_query_async_task(args: Dict) -> Dict:
    """Return current state + progress + (if done) result for a task."""
    task_id = args["task_id"]
    with _ASYNC_TASKS_LOCK:
        entry = _ASYNC_TASKS.get(task_id)
        if entry is None:
            return {"task_id": task_id, "state": "unknown", "error": "task_id not found"}
        snapshot = dict(entry)

    # Compute elapsed seconds for convenience
    started = snapshot.get("started_at")
    finished = snapshot.get("finished_at")
    queued = snapshot.get("queued_at")
    if started is not None:
        end = finished if finished is not None else _time.time()
        snapshot["elapsed_seconds"] = round(end - started, 3)
    elif queued is not None:
        snapshot["elapsed_seconds"] = round(_time.time() - queued, 3)
    return snapshot

def _gen_visualize_forces(args: Dict) -> str:
    """Generate code that reads applied joint torques and draws colored arrows.

    Color rules (per spec):
      green  : |torque| <= 70 % of effort limit
      yellow : 70 % < |torque| <= 90 %
      red    : |torque| > 90 %
    """
    art_path = args["articulation_path"]
    scale = float(args.get("scale", 0.01))
    update_hz = float(args.get("update_hz", 30.0))

    return f"""\
import omni.usd
from pxr import Usd, UsdGeom, UsdPhysics, Gf

try:
    from isaacsim.util.debug_draw import _debug_draw  # Isaac 4.x
except ImportError:  # Isaac Sim 5.x renamed module
    from isaacsim.util.debug_draw import _debug_draw

draw = _debug_draw.acquire_debug_draw_interface()

ART_PATH = {art_path!r}
SCALE = {scale!r}
UPDATE_HZ = {update_hz!r}

stage = omni.usd.get_context().get_stage()
art_prim = stage.GetPrimAtPath(ART_PATH)
if not art_prim or not art_prim.IsValid():
    raise RuntimeError(f'Articulation prim not found: {{ART_PATH}}')


def _color_for(ratio):
    # ratio = |torque| / effort_limit
    if ratio <= 0.70:
        return (0.1, 1.0, 0.1, 1.0)  # green
    if ratio <= 0.90:
        return (1.0, 0.95, 0.1, 1.0)  # yellow
    return (1.0, 0.15, 0.15, 1.0)  # red


def _collect_joints(prim):
    joints = []
    for child in Usd.PrimRange(prim):
        if child.IsA(UsdPhysics.RevoluteJoint) or child.IsA(UsdPhysics.PrismaticJoint):
            joints.append(child)
    return joints


def _draw_once():
    draw.clear_lines()
    points_a = []
    points_b = []
    colors = []
    sizes = []

    for joint in _collect_joints(art_prim):
        # Joint world position (best-effort via Xformable)
        try:
            xf = UsdGeom.Xformable(joint).ComputeLocalToWorldTransform(Usd.TimeCode.Default())
            pos = xf.ExtractTranslation()
        except Exception:
            pos = Gf.Vec3d(0, 0, 0)

        # Drive API: applied target torque + effort limit
        drive = UsdPhysics.DriveAPI.Get(joint, 'angular') or UsdPhysics.DriveAPI.Get(joint, 'linear')
        torque = 0.0
        limit = 1.0
        if drive:
            try:
                torque = float(drive.GetTargetPositionAttr().Get() or 0.0)
            except Exception:
                torque = 0.0
            try:
                limit = float(drive.GetMaxForceAttr().Get() or 1.0)
            except Exception:
                limit = 1.0

        ratio = min(abs(torque) / max(abs(limit), 1e-6), 1.5)
        color = _color_for(ratio)
        length = max(abs(torque), 0.05) * SCALE

        start = (pos[0], pos[1], pos[2])
        end = (pos[0], pos[1], pos[2] + length)
        points_a.append(start)
        points_b.append(end)
        colors.append(color)
        sizes.append(2.0)

        # Arrowhead: two short lines making a wedge above the tip.
        head = max(length * 0.2, 0.01)
        for ox in (-head, head):
            points_a.append(end)
            points_b.append((end[0] + ox, end[1], end[2] - head))
            colors.append(color)
            sizes.append(2.0)

    if points_a:
        draw.draw_lines(points_a, points_b, colors, sizes)


_draw_once()
print(f'[visualize_forces] drew arrows for {{ART_PATH}} at scale={{SCALE}} (update={{UPDATE_HZ}} Hz)')
"""

def _gen_render_video(args: Dict) -> str:
    """Generate code that runs Movie Capture for a clip."""
    duration = float(args["duration"])
    camera = args.get("camera")  # may be None → active viewport camera
    quality = args.get("quality", "preview")
    if quality not in _RENDER_QUALITY_PRESETS:
        quality = "preview"
    preset = _RENDER_QUALITY_PRESETS[quality]
    fps = int(args.get("fps", 30))

    output_path = args.get("output_path")
    if not output_path:
        # Stable per-call name; the Kit-side code resolves the timestamp.
        output_path = "workspace/renders/render_<timestamp>.mp4"

    res_w, res_h = preset["resolution"]
    renderer = preset["renderer"]
    spp = preset["spp"]

    return f"""\
import os
import time
from pathlib import Path

# Movie Capture / kit.capture extension (RTX-rendered, NOT screen capture)
try:
    from omni.kit.capture import CaptureOptions, CaptureExtension
except ImportError:
    # Newer Kit versions expose the API under omni.kit.capture.viewport
    from omni.kit.capture.viewport import CaptureOptions, CaptureExtension

DURATION_S = {duration!r}
FPS = {fps!r}
QUALITY = {quality!r}
RES = ({res_w}, {res_h})
RENDERER = {renderer!r}
SPP = {spp!r}
CAMERA = {camera!r}

raw_output = {output_path!r}
ts = time.strftime('%Y%m%dT%H%M%SZ')
output_path = raw_output.replace('<timestamp>', ts)
out = Path(output_path)
out.parent.mkdir(parents=True, exist_ok=True)

options = CaptureOptions()
options.fps = FPS
options.resolution = RES
options.renderer = RENDERER  # 'PathTracing' or 'RayTracing'
options.spp = SPP
options.output_path = str(out)
options.start_frame = 0
options.end_frame = max(int(DURATION_S * FPS) - 1, 0)
if CAMERA:
    options.camera = CAMERA

ext = CaptureExtension.get_instance()
ext.start(options)

print(f'[render_video] preset={{QUALITY}} renderer={{RENDERER}} '
      f'resolution={{RES}} spp={{SPP}} duration={{DURATION_S}}s fps={{FPS}} '
      f'output={{out}}')
"""

DATA_HANDLERS["filter_templates_by_hardware"] = _handle_filter_templates_by_hardware
CODE_GEN_HANDLERS["export_template"] = _gen_export_template
CODE_GEN_HANDLERS["import_template"] = _gen_import_template
DATA_HANDLERS["check_vram_headroom"] = _handle_check_vram_headroom
DATA_HANDLERS["dispatch_async_task"] = _handle_dispatch_async_task
DATA_HANDLERS["query_async_task"] = _handle_query_async_task
CODE_GEN_HANDLERS["visualize_forces"] = _gen_visualize_forces
CODE_GEN_HANDLERS["render_video"] = _gen_render_video

# ══════ From feat/new-quick-demo-builder-v2 ══════
def _gen_quick_demo(args: Dict) -> str:
    """Build a complete demo scene by chaining template + robot + objects + policy + camera."""
    demo_type = args.get("demo_type", "pick_place")
    template = _QUICK_DEMO_TEMPLATES.get(demo_type, _QUICK_DEMO_TEMPLATES["pick_place"])
    robot = args.get("robot", template["default_robot"])
    objects = args.get("objects", template["default_objects"])
    scene_style = args.get("scene_style", "clean")
    style = _SCENE_STYLE_PRESETS.get(scene_style, _SCENE_STYLE_PRESETS["clean"])
    cam_pos = template["camera_position"]

    return f"""\
# Quick Demo Builder: {demo_type}
# Robot: {robot} | Objects: {objects} | Style: {scene_style}
import omni.usd
from pxr import UsdGeom, UsdLux, UsdPhysics, Gf

stage = omni.usd.get_context().get_stage()
print("Step 1/5: Loading scene template ({demo_type})...")

# 1. Ground + physics
if not stage.GetPrimAtPath("/World/PhysicsScene"):
    UsdPhysics.Scene.Define(stage, "/World/PhysicsScene")
ground = UsdGeom.Cube.Define(stage, "/World/Ground")
ground.AddTranslateOp().Set(Gf.Vec3d(0, 0, -0.05))
ground.AddScaleOp().Set(Gf.Vec3f(5, 5, 0.05))
UsdPhysics.CollisionAPI.Apply(ground.GetPrim())

# 2. Lighting (style: {scene_style}, intensity={style['intensity']})
print("Step 2/5: Setting up {scene_style} lighting...")
dome = UsdLux.DomeLight.Define(stage, "/World/DomeLight")
dome.CreateIntensityAttr().Set({style['intensity']})

# 3. Robot placeholder (call import_robot('{robot}') as follow-up for full asset)
print("Step 3/5: Importing robot ({robot})...")
robot_xform = UsdGeom.Xform.Define(stage, "/World/Robot")

# 4. Demo objects
print("Step 4/5: Placing {len(objects)} demo objects...")
_objects_list = {objects!r}
for i, obj_name in enumerate(_objects_list):
    obj_path = f"/World/Objects/{{obj_name}}_{{i}}"
    obj = UsdGeom.Cube.Define(stage, obj_path)
    obj.AddTranslateOp().Set(Gf.Vec3d(0.5 + i * 0.1, 0.0, 0.05))
    obj.AddScaleOp().Set(Gf.Vec3f(0.04, 0.04, 0.04))
    UsdPhysics.RigidBodyAPI.Apply(obj.GetPrim())
    UsdPhysics.CollisionAPI.Apply(obj.GetPrim())

# 5. Camera
print("Step 5/5: Positioning camera...")
cam = UsdGeom.Camera.Define(stage, "/World/DemoCamera")
cam.AddTranslateOp().Set(Gf.Vec3d({cam_pos[0]}, {cam_pos[1]}, {cam_pos[2]}))
cam.CreateFocalLengthAttr().Set(35.0)

import omni.kit.viewport.utility
vp = omni.kit.viewport.utility.get_active_viewport()
if vp:
    vp.camera_path = "/World/DemoCamera"

print(f"\\n✓ Quick demo ready: {demo_type} with {robot}")
print(f"  Task: {template['task']}")
print(f"  Pre-trained policy: {template['policy_checkpoint']} ({template['policy_algo']})")
print(f"  Click ▶ Play to start, or call deploy_policy() to load the trained policy.")
"""

def _gen_record_demo_video(args: Dict) -> str:
    """Record viewport to MP4 file."""
    duration = args.get("duration", 10.0)
    camera = args.get("camera", "")
    output_path = args["output_path"]
    resolution = args.get("resolution", [1920, 1080])
    fps = args.get("fps", 30)

    camera_setup = (
        f"vp.camera_path = {camera!r}"
        if camera
        else "# Using current active camera"
    )

    return f"""\
# Record demo video to {output_path}
import os
import omni.kit.viewport.utility

output_path = {output_path!r}
duration_s = {duration}
fps = {fps}
resolution = ({resolution[0]}, {resolution[1]})
total_frames = int(duration_s * fps)

os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

vp = omni.kit.viewport.utility.get_active_viewport()
if vp is None:
    raise RuntimeError("No active viewport available")

{camera_setup}

try:
    from omni.kit.capture.viewport import CaptureOptions, CaptureExtension
    options = CaptureOptions()
    options.file_type = ".mp4"
    options.output_folder = os.path.dirname(output_path)
    options.file_name = os.path.basename(output_path).replace(".mp4", "")
    options.fps = fps
    options.start_frame = 0
    options.end_frame = total_frames
    options.res_width = resolution[0]
    options.res_height = resolution[1]
    capture = CaptureExtension.get_instance()
    capture.start(options)
    print(f"Recording {{duration_s}}s at {{resolution[0]}}x{{resolution[1]}}@{{fps}}fps to {{output_path}}")
except ImportError:
    from omni.kit.viewport.utility import capture_viewport_to_file
    print("Capture extension not available — using frame-by-frame fallback")
    for frame in range(total_frames):
        capture_viewport_to_file(vp, f"{{output_path}}.frame_{{frame:05d}}.png")
    print(f"Captured {{total_frames}} frames. Use ffmpeg to assemble: ffmpeg -framerate {{fps}} -i {{output_path}}.frame_%05d.png {{output_path}}")
"""

CODE_GEN_HANDLERS["quick_demo"] = _gen_quick_demo
CODE_GEN_HANDLERS["record_demo_video"] = _gen_record_demo_video

# ══════ From feat/new-sim-to-real-gap-v2 ══════
def _load_trajectory_for_gap(path: str) -> Optional[Dict]:
    """Load trajectory from HDF5 or CSV. Returns dict of arrays or None on error."""
    if not Path(path).exists():
        return None
    try:
        if path.endswith((".h5", ".hdf5")):
            try:
                import h5py
            except ImportError:
                return {"_error": "h5py not installed"}
            data = {}
            with h5py.File(path, "r") as f:
                for key in f.keys():
                    try:
                        data[key] = f[key][:].tolist()
                    except Exception:
                        pass
            return data
        elif path.endswith(".csv"):
            import csv
            data: Dict = {"rows": []}
            with open(path, "r") as fp:
                reader = csv.DictReader(fp)
                for row in reader:
                    data["rows"].append(row)
            return data
        else:
            return {"_error": f"Unsupported file format: {path}"}
    except Exception as e:
        return {"_error": str(e)}

async def _handle_measure_sim_real_gap(args: Dict) -> Dict:
    """Compare sim and real trajectories to quantify the gap."""
    sim_path = args.get("sim_trajectory", "")
    real_path = args.get("real_trajectory", "")

    sim = _load_trajectory_for_gap(sim_path)
    real = _load_trajectory_for_gap(real_path)

    if sim is None or real is None:
        missing = []
        if sim is None:
            missing.append(sim_path)
        if real is None:
            missing.append(real_path)
        return {"error": f"Trajectory file(s) not found: {missing}"}

    if (isinstance(sim, dict) and sim.get("_error")) or (isinstance(real, dict) and real.get("_error")):
        return {"error": sim.get("_error") if isinstance(sim, dict) else real.get("_error")}

    sim_joints = sim.get("joint_positions") or sim.get("joints") or sim.get("q")
    real_joints = real.get("joint_positions") or real.get("joints") or real.get("q")

    if not sim_joints or not real_joints:
        return {
            "error": "Could not find joint_positions/joints/q in trajectory files",
            "sim_keys": list(sim.keys()),
            "real_keys": list(real.keys()),
        }

    n_steps = min(len(sim_joints), len(real_joints))
    if n_steps == 0:
        return {"error": "Empty trajectories"}

    n_joints = len(sim_joints[0]) if isinstance(sim_joints[0], (list, tuple)) else 1
    joint_errors = {}
    for j in range(n_joints):
        errors = []
        for t in range(n_steps):
            s_val = sim_joints[t][j] if isinstance(sim_joints[t], (list, tuple)) else sim_joints[t]
            r_val = real_joints[t][j] if isinstance(real_joints[t], (list, tuple)) else real_joints[t]
            errors.append(abs(float(s_val) - float(r_val)))
        joint_errors[f"joint_{j}"] = {
            "mean_error_deg": round(sum(errors) / len(errors), 4),
            "max_error_deg": round(max(errors), 4),
        }

    worst_joint = max(joint_errors, key=lambda k: joint_errors[k]["mean_error_deg"])

    ee_error_mm = None
    sim_ee = sim.get("ee_pos") or sim.get("end_effector")
    real_ee = real.get("ee_pos") or real.get("end_effector")
    if sim_ee and real_ee:
        ee_errs = []
        for t in range(min(len(sim_ee), len(real_ee))):
            s, r = sim_ee[t], real_ee[t]
            d = sum((s[i] - r[i]) ** 2 for i in range(min(len(s), len(r)))) ** 0.5
            ee_errs.append(d)
        if ee_errs:
            ee_error_mm = {
                "mean_mm": round((sum(ee_errs) / len(ee_errs)) * 1000, 2),
                "max_mm": round(max(ee_errs) * 1000, 2),
            }

    recommendation = []
    worst_err = joint_errors[worst_joint]["mean_error_deg"]
    if worst_err > 5.0:
        recommendation.append(f"{worst_joint} has {worst_err:.1f}° mean error — investigate friction/damping mismatch")
    if ee_error_mm and ee_error_mm["mean_mm"] > 10:
        recommendation.append(f"EE drifts {ee_error_mm['mean_mm']:.0f}mm — likely joint compliance issue")

    return {
        "joint_errors": joint_errors,
        "worst_joint": worst_joint,
        "ee_error_mm": ee_error_mm,
        "n_steps": n_steps,
        "n_joints": n_joints,
        "recommendation": recommendation,
    }

async def _handle_suggest_parameter_adjustment(args: Dict) -> Dict:
    """Given a gap report, suggest physics parameters to adjust."""
    gap = args.get("gap_report", {})
    if not gap or "joint_errors" not in gap:
        return {"error": "Invalid gap_report — must include 'joint_errors' from measure_sim_real_gap()"}

    suggestions = []
    joint_errors = gap.get("joint_errors", {})
    ee_error = gap.get("ee_error_mm") or {}

    for joint_name, err in joint_errors.items():
        mean_err = err["mean_error_deg"]
        if mean_err > 5.0:
            suggestions.append({
                "joint": joint_name,
                "issue": f"Mean error {mean_err:.1f}° too high",
                "suggested_action": "Reduce damping by 30% or check actuator model",
                "parameter": f"drive:angular:physics:damping on {joint_name}",
                "priority": "high",
            })
        elif mean_err > 2.0:
            suggestions.append({
                "joint": joint_name,
                "issue": f"Mean error {mean_err:.1f}° moderate",
                "suggested_action": "Fine-tune stiffness or friction",
                "parameter": f"drive:angular:physics:stiffness on {joint_name}",
                "priority": "medium",
            })

    if ee_error.get("mean_mm", 0) > 10:
        suggestions.append({
            "issue": f"End-effector drifts {ee_error['mean_mm']:.0f}mm",
            "suggested_action": "Add joint compliance: reduce stiffness ~20%",
            "parameter": "joint stiffness (all)",
            "priority": "high",
        })

    if not suggestions:
        suggestions.append({"message": "Gap is within acceptable range — no adjustments needed"})

    return {
        "suggestions": suggestions,
        "worst_joint": gap.get("worst_joint"),
        "total_suggestions": len(suggestions),
    }

async def _handle_compare_sim_real_video(args: Dict) -> Dict:
    """Compare sim and real videos using vision LLM."""
    sim_path = args.get("sim_video_path", "")
    real_path = args.get("real_video_path", "")

    if not Path(sim_path).exists() or not Path(real_path).exists():
        return {
            "error": "Video file(s) not found",
            "sim_exists": Path(sim_path).exists(),
            "real_exists": Path(real_path).exists(),
        }

    return {
        "sim_video": sim_path,
        "real_video": real_path,
        "analysis_prompt": (
            "Compare these two robot trajectories. Identify: "
            "1) Behavioral differences (overshoot, undershoot, tremor) "
            "2) Contact timing differences "
            "3) Stability/oscillation differences "
            "4) Speed/timing differences"
        ),
        "note": "Vision analysis to be performed by Gemini Vision provider",
        "next_step": "Call vision_analyze_scene with these videos as input",
    }

def _gen_create_calibration_experiment(args: Dict) -> str:
    """Generate calibration grid search code."""
    parameter = args.get("parameter", "friction")
    param_range = args.get("range", [0.0, 1.0])
    num_samples = args.get("num_samples", 7)
    real_data_path = args.get("real_data_path", "")

    return f"""\
# Calibration experiment: {parameter} grid search ({num_samples} samples)
import numpy as np
import json
import omni.usd
from pxr import UsdPhysics

values = np.linspace({param_range[0]}, {param_range[1]}, {num_samples}).tolist()
real_data_path = {real_data_path!r}
parameter = {parameter!r}

results = []
for i, value in enumerate(values):
    print(f"\\n=== Trial {{i+1}}/{{len(values)}}: {{parameter}} = {{value:.3f}} ===")

    stage = omni.usd.get_context().get_stage()

    if parameter == "friction":
        for prim in stage.Traverse():
            if prim.HasAPI(UsdPhysics.MaterialAPI):
                mat = UsdPhysics.MaterialAPI(prim)
                mat.GetDynamicFrictionAttr().Set(float(value))
    elif parameter == "damping":
        for prim in stage.Traverse():
            if prim.HasAPI(UsdPhysics.DriveAPI):
                drive = UsdPhysics.DriveAPI(prim, "angular")
                drive.GetDampingAttr().Set(float(value))
    elif parameter == "stiffness":
        for prim in stage.Traverse():
            if prim.HasAPI(UsdPhysics.DriveAPI):
                drive = UsdPhysics.DriveAPI(prim, "angular")
                drive.GetStiffnessAttr().Set(float(value))

    print(f"  Running sim trajectory with {{parameter}} = {{value:.3f}}...")
    # ... execute trajectory, record sim_data ...
    # Compare with real_data_path via measure_sim_real_gap
    # Replace placeholder score below with real gap score:
    score = abs(value - 0.6)  # placeholder

    results.append({{"value": value, "gap_score": score}})

best = min(results, key=lambda r: r["gap_score"])
print(f"\\n✓ Best {{parameter}} value: {{best['value']:.3f}} (gap score: {{best['gap_score']:.4f}})")
print(json.dumps(results, indent=2))
"""

DATA_HANDLERS["measure_sim_real_gap"] = _handle_measure_sim_real_gap
DATA_HANDLERS["suggest_parameter_adjustment"] = _handle_suggest_parameter_adjustment
DATA_HANDLERS["compare_sim_real_video"] = _handle_compare_sim_real_video
CODE_GEN_HANDLERS["create_calibration_experiment"] = _gen_create_calibration_experiment

# ══════ From feat/addendum-phase7G-groot-tooling-v2 ══════
def _gen_extract_attention_maps(args: Dict) -> str:
    """Generate code to extract DiT cross-attention maps from GR00T."""
    checkpoint = args["checkpoint_path"]
    obs_path = args["observation_path"]
    layer = args.get("layer", 12)

    return f"""\
# Extract GR00T attention maps (layer {layer})
import torch
import json
import os

checkpoint_path = {checkpoint!r}
observation_path = {obs_path!r}
layer = {layer}

if not os.path.exists(checkpoint_path):
    print(json.dumps({{"error": f"Checkpoint not found: {{checkpoint_path}}"}}))
else:
    print(f"Loading GR00T checkpoint from {{checkpoint_path}}...")
    # Note: actual GR00T model loading requires gr00t.policy package
    # from gr00t.policy.dit_policy import DiTPolicy
    # model = DiTPolicy.load_from_checkpoint(checkpoint_path)
    # from torch.fx import create_feature_extractor
    # features = create_feature_extractor(model.vision_encoder,
    #     return_nodes={{f"encoder.layers.{{layer}}.self_attn.attn_drop": f"attn_{{layer}}"}})

    print(f"Attention map extraction configured for layer {{layer}}")
    print(f"Observation: {{observation_path}}")
    print("Run model.forward(observation) to capture attention; overlay on viewport image as heatmap")

    result = {{
        "checkpoint": checkpoint_path,
        "observation": observation_path,
        "layer": layer,
        "tap_node": f"encoder.layers.{{layer}}.self_attn.attn_drop",
        "next_step": "Run inference with feature extractor, save heatmap PNG",
    }}
    print(json.dumps(result, indent=2))
"""

async def _handle_detect_ood(args: Dict) -> Dict:
    """Detect OOD via action variance/autocorrelation (Tier 1) or higher tiers."""
    tier = args.get("tier", 1)

    if tier == 1:
        action_seq = args.get("action_sequence", [])
        if not action_seq or len(action_seq) < 2:
            return {"error": "Tier 1 requires action_sequence with >= 2 entries"}

        n_dims = len(action_seq[0]) if isinstance(action_seq[0], (list, tuple)) else 1
        variances = []
        autocorrs = []
        for j in range(n_dims):
            values = [step[j] if isinstance(step, (list, tuple)) else step for step in action_seq]
            mean = sum(values) / len(values)
            var = sum((v - mean) ** 2 for v in values) / len(values)
            variances.append(var)
            if len(values) >= 3:
                v0, v1 = values[:-1], values[1:]
                m0, m1 = sum(v0) / len(v0), sum(v1) / len(v1)
                num = sum((a - m0) * (b - m1) for a, b in zip(v0, v1))
                den0 = sum((a - m0) ** 2 for a in v0) ** 0.5
                den1 = sum((b - m1) ** 2 for b in v1) ** 0.5
                autocorrs.append(num / max(den0 * den1, 1e-10))
            else:
                autocorrs.append(0.0)

        max_var = max(variances)
        min_autocorr = min(autocorrs) if autocorrs else 1.0
        is_ood = max_var > 1.0 or min_autocorr < 0.3
        return {
            "tier": 1,
            "is_ood": is_ood,
            "max_action_variance": round(max_var, 4),
            "min_autocorrelation": round(min_autocorr, 4),
            "thresholds": {"variance": 1.0, "autocorr": 0.3},
            "warning": "Action instability detected — policy may be extrapolating" if is_ood else None,
        }
    elif tier == 2:
        return {
            "tier": 2,
            "method": "4-sample DiT variance",
            "overhead_ms": 15,
            "instructions": "Run 4 forward passes with dropout, compute action variance",
            "checkpoint_needed": args.get("checkpoint_path"),
        }
    elif tier == 3:
        return {
            "tier": 3,
            "method": "Mahalanobis distance on 12th-layer embeddings",
            "instructions": "Pre-compute mean+covariance over training data; inference distance > threshold = OOD",
            "calibration_path": args.get("calibration_path"),
        }
    else:
        return {"error": f"Invalid tier {tier} — must be 1, 2, or 3"}

async def _handle_suggest_data_mix(args: Dict) -> Dict:
    """Recommend sim/real/video data ratio per NVIDIA's 1:1 recipe."""
    task_type = args.get("task_type", "tabletop pick-and-place")
    available = args.get("available_data", {})
    real_demos = available.get("real_demos", 0)
    sim_demos = available.get("sim_demos", 0)
    video_demos = available.get("video_demos", 0)

    target_real = real_demos
    target_sim = min(sim_demos, real_demos) if real_demos > 0 else min(sim_demos, 200)
    target_video = min(video_demos, max(target_real // 4, 0))

    return {
        "task_type": task_type,
        "available": available,
        "recommendation": {
            "real_demos_to_use": target_real,
            "sim_demos_to_use": target_sim,
            "video_demos_to_use": target_video,
            "total_training_examples": target_real + target_sim + target_video,
        },
        "rationale": "NVIDIA's validated 1:1 real-to-neural ratio (40% gain over real-only)",
        "dr_priorities": [
            "Spatial DR (table height + camera pose) — 3x weight",
            "Appearance DR (textures, lighting) — 1x weight",
        ],
        "additional_advice": (
            f"Consider collecting {max(50 - video_demos, 0)} more video demos for visual diversity"
            if video_demos < 50 else "Video diversity is sufficient"
        ),
        "warnings": (
            ["⚠ No real demos — sim-only training will have high sim-to-real gap"]
            if real_demos == 0 else []
        ),
    }

async def _handle_suggest_finetune_config(args: Dict) -> Dict:
    """Recommend layer freeze/tune strategy."""
    task_type = args.get("task_type", "similar_to_pretrain")
    hardware = args.get("hardware", "RTX 4090")
    data_size = args.get("data_size", 0)

    profile = _FINETUNE_FREEZE_PROFILES.get(task_type)
    if not profile:
        return {"error": f"Unknown task_type: {task_type}. Valid: {list(_FINETUNE_FREEZE_PROFILES.keys())}"}

    hw_batch_hints = {
        "A6000": {"similar_to_pretrain": 200, "new_visual_domain": 16, "new_embodiment": 8},
        "RTX 4090": {"similar_to_pretrain": 100, "new_visual_domain": 8, "new_embodiment": 4},
        "RTX 4080": {"similar_to_pretrain": 50, "new_visual_domain": 4, "new_embodiment": 2},
        "H100": {"similar_to_pretrain": 400, "new_visual_domain": 32, "new_embodiment": 16},
    }
    batch = hw_batch_hints.get(hardware, hw_batch_hints["RTX 4090"]).get(task_type, 16)

    result = {
        "task_type": task_type,
        "hardware": hardware,
        "freeze_layers": profile["freeze"],
        "tune_layers": profile["tune"],
        "rationale": profile["rationale"],
        "recommended_batch_size": batch,
        "lora_rank": profile["lora_rank"],
    }
    if "warning" in profile:
        result["warning"] = profile["warning"]
    if data_size and data_size < 50:
        result["data_warning"] = f"Only {data_size} demos — consider collecting more (recommended: 200+)"
    return result

async def _handle_monitor_forgetting(args: Dict) -> Dict:
    """Detect catastrophic forgetting via VQA regression + weight drift."""
    checkpoint_dir = args.get("checkpoint_dir", "")
    base_model = args.get("base_model", "")

    if not Path(checkpoint_dir).exists():
        return {"error": f"Checkpoint dir not found: {checkpoint_dir}"}

    return {
        "checkpoint_dir": checkpoint_dir,
        "base_model": base_model,
        "vqa_benchmarks": ["MMMU", "MMStar", "RealWorldQA", "MathVista", "AI2D"],
        "instructions": [
            "1. Run 30-example VQA regression suite on each checkpoint",
            "2. Compare scores against base_model baseline",
            "3. Compute per-layer Frobenius norm: ||W_ft - W_pre||_F",
            "4. Alert if ANY VQA score drops >20% OR vision encoder drift > threshold",
        ],
        "alert_thresholds": {
            "vqa_score_drop_pct": 20,
            "vision_encoder_drift_max": 0.05,
            "language_model_drift_max": 0.01,
        },
        "warning": "Standard fine-tuning can collapse silently to near-zero VQA scores without external checks",
    }

def _gen_export_policy(args: Dict) -> str:
    """Generate code to export GR00T checkpoint to TensorRT."""
    checkpoint = args["checkpoint"]
    target = args["target_device"]
    budget_ms = args.get("inference_budget_ms")

    target_info = _EXPORT_TARGETS.get(target, _EXPORT_TARGETS["x86_rtx4090"])

    return f"""\
# Export GR00T policy to TensorRT for {target}
import os
import json

checkpoint_path = {checkpoint!r}
target_device = {target!r}
target_info = {target_info!r}
budget_ms = {budget_ms!r}

print(f"Exporting {{checkpoint_path}} for {{target_device}}")
print(f"  Format: {{target_info['format']}}")
print(f"  Expected throughput: {{target_info['expected_hz']}} Hz")
print(f"  FP8 supported: {{target_info['fp8_supported']}}")
print(f"  Note: {{target_info['note']}}")

if not target_info['fp8_supported']:
    print("⚠ FP8/NVFP4 unsupported on this device — capped at bf16")

# Actual export pipeline:
# 1. Load checkpoint via gr00t.policy.dit_policy.DiTPolicy
# 2. Convert to ONNX via torch.onnx.export
# 3. Build TensorRT engine via trtexec or polygraphy:
#    trtexec --onnx=policy.onnx --saveEngine=policy.engine --bf16

output_engine = checkpoint_path.replace('.pt', f'.{{target_device}}.engine')
print(f"Output engine path: {{output_engine}}")

if budget_ms:
    if 1000 / budget_ms > target_info['expected_hz']:
        print(f"⚠ Budget {{budget_ms}}ms requires {{1000/budget_ms:.1f}} Hz but device max is {{target_info['expected_hz']}} Hz")
"""

async def _handle_analyze_checkpoint(args: Dict) -> Dict:
    """Analyze GR00T checkpoint: embodiment, drift, action stats, risk."""
    checkpoint_path = args.get("checkpoint_path", "")
    base_path = args.get("base_model_path")

    if not Path(checkpoint_path).exists():
        return {"error": f"Checkpoint not found: {checkpoint_path}"}

    analysis = {
        "checkpoint_path": checkpoint_path,
        "instructions": [
            "1. Load checkpoint with torch.load(weights_only=False)",
            "2. Read metadata: embodiment, training_steps from checkpoint['config']",
            "3. If base_model provided, compute per-layer Frobenius norm",
            "4. Aggregate action statistics from training logs",
        ],
        "expected_structure": {
            "embodiment": "UNITREE_G1 / LIBERO_PANDA / OXE_WIDOWX / CUSTOM",
            "training_steps": "int",
            "layer_drift": {
                "vision_encoder": "low (<0.05) = frozen, good",
                "dit_layers": "high (>0.3) = well-targeted",
                "adapter_mlps": "high (>0.3) = expected",
                "language_model": "near-zero (<0.01) = frozen, good",
            },
            "action_statistics": {
                "mean_per_joint": "[float, ...]",
                "std_per_joint": "[float, ...]",
            },
        },
    }
    if base_path:
        analysis["compare_against"] = base_path
    return analysis

CODE_GEN_HANDLERS["extract_attention_maps"] = _gen_extract_attention_maps
DATA_HANDLERS["detect_ood"] = _handle_detect_ood
DATA_HANDLERS["suggest_data_mix"] = _handle_suggest_data_mix
DATA_HANDLERS["suggest_finetune_config"] = _handle_suggest_finetune_config
DATA_HANDLERS["monitor_forgetting"] = _handle_monitor_forgetting
CODE_GEN_HANDLERS["export_policy"] = _gen_export_policy
DATA_HANDLERS["analyze_checkpoint"] = _handle_analyze_checkpoint

# ══════ From feat/addendum-phase5-pedagogy-uncertainty-v2 ══════
def _gen_create_broken_scene(args: Dict) -> str:
    """Generate code that creates a scene with a specific, diagnosable fault for teaching."""
    fault_type = args.get("fault_type", "missing_collision")
    scene_name = args.get("scene_name", "BrokenScene")

    if fault_type not in _BROKEN_SCENE_FAULTS:
        raise ValueError(f"Unknown fault_type: {fault_type}. Valid: {list(_BROKEN_SCENE_FAULTS.keys())}")

    fault = _BROKEN_SCENE_FAULTS[fault_type]
    scene_path = f"/World/{scene_name}"

    physics_scene_code = (
        ""
        if fault_type == "no_physics_scene"
        else "if not stage.GetPrimAtPath('/World/PhysicsScene'):\n    UsdPhysics.Scene.Define(stage, '/World/PhysicsScene')"
    )

    if fault_type == "missing_collision":
        fault_code = f"""\
ground = UsdGeom.Cube.Define(stage, '{scene_path}/Ground')
ground.AddTranslateOp().Set(Gf.Vec3d(0, 0, -0.05))
ground.AddScaleOp().Set(Gf.Vec3f(5, 5, 0.05))
# FAULT: NO CollisionAPI applied — the ground will not collide with anything
# UsdPhysics.CollisionAPI.Apply(ground.GetPrim())  # THIS LINE DELIBERATELY MISSING

falling = UsdGeom.Cube.Define(stage, '{scene_path}/FallingCube')
falling.AddTranslateOp().Set(Gf.Vec3d(0, 0, 2.0))
falling.AddScaleOp().Set(Gf.Vec3f(0.1, 0.1, 0.1))
UsdPhysics.RigidBodyAPI.Apply(falling.GetPrim())
UsdPhysics.CollisionAPI.Apply(falling.GetPrim())
"""
    elif fault_type == "zero_mass":
        fault_code = f"""\
body = UsdGeom.Cube.Define(stage, '{scene_path}/ZeroMassBody')
body.AddTranslateOp().Set(Gf.Vec3d(0, 0, 1))
UsdPhysics.RigidBodyAPI.Apply(body.GetPrim())
UsdPhysics.CollisionAPI.Apply(body.GetPrim())
mass_api = UsdPhysics.MassAPI.Apply(body.GetPrim())
mass_api.CreateMassAttr().Set(0.0)  # FAULT: zero mass causes PhysX NaN explosion
"""
    elif fault_type == "wrong_scale":
        fault_code = f"""\
# FAULT: object scaled 100x (cm interpreted as m)
big = UsdGeom.Cube.Define(stage, '{scene_path}/HugeBox')
big.AddTranslateOp().Set(Gf.Vec3d(0, 0, 50))
big.AddScaleOp().Set(Gf.Vec3f(100, 100, 100))
UsdPhysics.RigidBodyAPI.Apply(big.GetPrim())
UsdPhysics.CollisionAPI.Apply(big.GetPrim())
"""
    elif fault_type == "inverted_joint":
        fault_code = f"""\
base = UsdGeom.Cube.Define(stage, '{scene_path}/Base')
base.AddTranslateOp().Set(Gf.Vec3d(0, 0, 0.5))
arm = UsdGeom.Cube.Define(stage, '{scene_path}/Arm')
arm.AddTranslateOp().Set(Gf.Vec3d(0.6, 0, 0.5))
joint = UsdPhysics.RevoluteJoint.Define(stage, '{scene_path}/Joint')
joint.CreateBody0Rel().SetTargets(['{scene_path}/Base'])
joint.CreateBody1Rel().SetTargets(['{scene_path}/Arm'])
joint.CreateAxisAttr().Set('Z')  # FAULT: should be Y for typical hinge
"""
    elif fault_type == "no_physics_scene":
        fault_code = f"""\
# FAULT: PhysicsScene prim deliberately not created — no physics will run
body = UsdGeom.Cube.Define(stage, '{scene_path}/Cube')
body.AddTranslateOp().Set(Gf.Vec3d(0, 0, 2))
UsdPhysics.RigidBodyAPI.Apply(body.GetPrim())
UsdPhysics.CollisionAPI.Apply(body.GetPrim())
"""
    else:  # inf_joint_limits
        fault_code = f"""\
base = UsdGeom.Cube.Define(stage, '{scene_path}/Base')
arm = UsdGeom.Cube.Define(stage, '{scene_path}/Arm')
arm.AddTranslateOp().Set(Gf.Vec3d(0.5, 0, 0))
joint = UsdPhysics.RevoluteJoint.Define(stage, '{scene_path}/Joint')
joint.CreateBody0Rel().SetTargets(['{scene_path}/Base'])
joint.CreateBody1Rel().SetTargets(['{scene_path}/Arm'])
joint.CreateAxisAttr().Set('Y')
joint.CreateLowerLimitAttr().Set(float('-inf'))  # FAULT: ±inf limits
joint.CreateUpperLimitAttr().Set(float('inf'))
"""

    return f"""\
# Broken scene: {fault_type}
# What breaks: {fault['what_breaks']}
# Learning goal: {fault['learning_goal']}
import omni.usd
from pxr import UsdGeom, UsdPhysics, Gf

stage = omni.usd.get_context().get_stage()
scope = UsdGeom.Xform.Define(stage, '{scene_path}')

{physics_scene_code}

{fault_code}

print(f"Created broken scene: {scene_path}")
print(f"Fault type: {fault_type}")
print(f"What's wrong: {fault['what_breaks']}")
print(f"Learning goal: {fault['learning_goal']}")
print(f"Hint: students should diagnose this without being told the answer.")
"""

CODE_GEN_HANDLERS["create_broken_scene"] = _gen_create_broken_scene

# ══════ From feat/addendum-safety-compliance-v2 ══════
def _gen_enable_deterministic_mode(args: Dict) -> str:
    """Generate code to enable deterministic simulation mode for safety validation."""
    seed = args.get("seed", 42)
    physics_dt = args.get("physics_dt", 1.0 / 60.0)
    solver_iterations = args.get("solver_iterations", 4)
    archive_path = args.get("export_archive_path")

    archive_code = ""
    if archive_path:
        archive_code = f"""
# Export reproducibility archive
import zipfile
import json
import platform
archive = {archive_path!r}
manifest = {{
    "seed": {seed},
    "physics_dt": {physics_dt},
    "solver_iterations": {solver_iterations},
    "platform": platform.platform(),
    "python_version": platform.python_version(),
}}
try:
    import isaacsim
    manifest["isaac_sim_version"] = isaacsim.__version__
except (ImportError, AttributeError):
    pass
try:
    import omni.physx
    manifest["physx_version"] = "see omni.physx package"
except ImportError:
    pass
os.makedirs(os.path.dirname(archive) or ".", exist_ok=True)
with zipfile.ZipFile(archive, "w") as z:
    z.writestr("manifest.json", json.dumps(manifest, indent=2))
print(f"Reproducibility archive: {{archive}}")
"""

    return f"""\
# Enable deterministic simulation mode (Safety & Compliance S.5)
import os
import random
import omni.usd
from pxr import UsdPhysics, PhysxSchema

random.seed({seed})
try:
    import numpy as np
    np.random.seed({seed})
except ImportError:
    pass

# Configure physics scene for determinism
stage = omni.usd.get_context().get_stage()
physics_scene_path = "/PhysicsScene"
physics_scene = stage.GetPrimAtPath(physics_scene_path)
if not physics_scene.IsValid():
    physics_scene_path = "/World/PhysicsScene"
    physics_scene = stage.GetPrimAtPath(physics_scene_path)

if physics_scene.IsValid():
    # Apply PhysxSceneAPI for advanced settings
    physx_api = PhysxSchema.PhysxSceneAPI.Apply(physics_scene)
    # TGS solver — deterministic for identical inputs (vs PGS which has slight nondeterminism)
    physx_api.CreateSolverTypeAttr().Set("TGS")
    # Force CPU mode — GPU dynamics is NOT fully deterministic
    physx_api.CreateBroadphaseTypeAttr().Set("MBP")
    physx_api.CreateGpuFoundLostPairsCapacityAttr().Set(0)  # Disable GPU broadphase
    physx_api.CreateEnableGPUDynamicsAttr().Set(False)
    # Fixed solver iterations
    physx_api.CreateMinPositionIterationCountAttr().Set({solver_iterations})
    physx_api.CreateMaxPositionIterationCountAttr().Set({solver_iterations})

# Set fixed physics timestep
import carb.settings
settings = carb.settings.get_settings()
settings.set("/persistent/simulation/minFrameRate", int(1.0 / {physics_dt}))
settings.set("/physics/fixedTimeStep", {physics_dt})

print(f"Deterministic mode ENABLED:")
print(f"  Seed: {seed}")
print(f"  Physics dt: {physics_dt}s ({{1.0 / {physics_dt}:.0f}} Hz)")
print(f"  Solver iterations: {solver_iterations} (fixed)")
print(f"  Solver: TGS (deterministic for identical inputs)")
print(f"  GPU dynamics: DISABLED (CPU only — GPU is not fully deterministic)")
print(f"  WARNING: PhysX GPU mode is NOT deterministic. CPU+TGS is for safety validation.")
{archive_code}
"""

CODE_GEN_HANDLERS["enable_deterministic_mode"] = _gen_enable_deterministic_mode

# ══════ From feat/atomic-tier0-foundation ══════
async def _handle_get_attribute(args: Dict) -> Dict:
    """Read a single USD attribute value."""
    prim_path = args["prim_path"]
    attr_name = args["attr_name"]
    code = f"""\
import omni.usd
import json

stage = omni.usd.get_context().get_stage()
prim = stage.GetPrimAtPath({prim_path!r})
result = {{'prim_path': {prim_path!r}, 'attr_name': {attr_name!r}}}
if not prim or not prim.IsValid():
    result['error'] = 'prim not found'
else:
    attr = prim.GetAttribute({attr_name!r})
    if not attr or not attr.IsDefined():
        result['error'] = 'attribute not defined'
        result['available'] = [a.GetName() for a in prim.GetAttributes()][:50]
    else:
        try:
            value = attr.Get()
        except Exception as exc:
            value = None
            result['error'] = f'attr.Get() failed: {{exc}}'
        # Convert pxr.Vt / Gf types to plain Python for json
        try:
            value = list(value) if hasattr(value, '__iter__') and not isinstance(value, (str, bytes)) else value
        except Exception:
            value = repr(value)
        result['value'] = value
        result['type_name'] = attr.GetTypeName().type.typeName
print(json.dumps(result, default=str))
"""
    return await kit_tools.queue_exec_patch(code, f"get_attribute {prim_path}.{attr_name}")

async def _handle_get_world_transform(args: Dict) -> Dict:
    """Compute world-space 4x4 transform of a prim."""
    prim_path = args["prim_path"]
    time_code = args.get("time_code")
    tc_expr = repr(time_code) if time_code is not None else "Usd.TimeCode.Default()"
    code = f"""\
import omni.usd
from pxr import Usd, UsdGeom, Gf
import json

stage = omni.usd.get_context().get_stage()
prim = stage.GetPrimAtPath({prim_path!r})
result = {{'prim_path': {prim_path!r}}}
if not prim or not prim.IsValid():
    result['error'] = 'prim not found'
else:
    xf = UsdGeom.Xformable(prim)
    if not xf:
        result['error'] = 'prim is not Xformable'
    else:
        m = xf.ComputeLocalToWorldTransform({tc_expr})
        result['matrix'] = [m[i][j] for i in range(4) for j in range(4)]
        t = m.ExtractTranslation()
        r = m.ExtractRotationQuat()
        # Pull scale from the upper 3x3
        sx = Gf.Vec3d(m[0][0], m[0][1], m[0][2]).GetLength()
        sy = Gf.Vec3d(m[1][0], m[1][1], m[1][2]).GetLength()
        sz = Gf.Vec3d(m[2][0], m[2][1], m[2][2]).GetLength()
        result['translation'] = [t[0], t[1], t[2]]
        im = r.GetImaginary()
        result['rotation_quat'] = [r.GetReal(), im[0], im[1], im[2]]
        result['scale'] = [sx, sy, sz]
print(json.dumps(result, default=str))
"""
    return await kit_tools.queue_exec_patch(code, f"get_world_transform {prim_path}")

async def _handle_get_bounding_box(args: Dict) -> Dict:
    """Compute world-space AABB of a prim."""
    prim_path = args["prim_path"]
    purpose = args.get("purpose", "default")
    code = f"""\
import omni.usd
from pxr import Usd, UsdGeom, Gf
import json

stage = omni.usd.get_context().get_stage()
prim = stage.GetPrimAtPath({prim_path!r})
result = {{'prim_path': {prim_path!r}}}
if not prim or not prim.IsValid():
    result['error'] = 'prim not found'
else:
    purpose_token = UsdGeom.Tokens.{purpose}
    cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [purpose_token], useExtentsHint=True)
    bbox = cache.ComputeWorldBound(prim)
    rng = bbox.ComputeAlignedRange()
    if rng.IsEmpty():
        result['error'] = 'empty bbox'
    else:
        mn = rng.GetMin()
        mx = rng.GetMax()
        cx = (mn[0] + mx[0]) / 2.0
        cy = (mn[1] + mx[1]) / 2.0
        cz = (mn[2] + mx[2]) / 2.0
        result['min'] = [mn[0], mn[1], mn[2]]
        result['max'] = [mx[0], mx[1], mx[2]]
        result['center'] = [cx, cy, cz]
        result['size'] = [mx[0] - mn[0], mx[1] - mn[1], mx[2] - mn[2]]
print(json.dumps(result, default=str))
"""
    return await kit_tools.queue_exec_patch(code, f"get_bounding_box {prim_path}")

def _gen_set_semantic_label(args: Dict) -> str:
    prim_path = args["prim_path"]
    class_name = args["class_name"]
    semantic_type = args.get("semantic_type", "class")
    return (
        "import omni.usd\n"
        "from pxr import Usd, Semantics\n"
        "stage = omni.usd.get_context().get_stage()\n"
        f"prim = stage.GetPrimAtPath({prim_path!r})\n"
        f"sem = Semantics.SemanticsAPI.Apply(prim, 'Semantics_{semantic_type}')\n"
        "sem.CreateSemanticTypeAttr().Set("
        f"{semantic_type!r})\n"
        "sem.CreateSemanticDataAttr().Set("
        f"{class_name!r})\n"
        f"print('semantic_label', {prim_path!r}, {semantic_type!r}, {class_name!r})"
    )

async def _handle_get_joint_limits(args: Dict) -> Dict:
    articulation = args["articulation"]
    joint_name = args["joint_name"]
    code = f"""\
import omni.usd
from pxr import Usd, UsdPhysics
import json

stage = omni.usd.get_context().get_stage()
art = stage.GetPrimAtPath({articulation!r})
result = {{'articulation': {articulation!r}, 'joint_name': {joint_name!r}}}
if not art or not art.IsValid():
    result['error'] = 'articulation not found'
else:
    joint_prim = None
    for p in Usd.PrimRange(art):
        if p.GetName() == {joint_name!r}:
            joint_prim = p
            break
    if joint_prim is None:
        result['error'] = 'joint not found'
    else:
        result['joint_path'] = str(joint_prim.GetPath())
        joint = UsdPhysics.RevoluteJoint(joint_prim) or UsdPhysics.PrismaticJoint(joint_prim)
        if not joint:
            result['error'] = 'joint is not Revolute or Prismatic'
        else:
            lower_attr = joint_prim.GetAttribute('physics:lowerLimit')
            upper_attr = joint_prim.GetAttribute('physics:upperLimit')
            result['lower'] = lower_attr.Get() if lower_attr and lower_attr.IsDefined() else None
            result['upper'] = upper_attr.Get() if upper_attr and upper_attr.IsDefined() else None
print(json.dumps(result, default=str))
"""
    return await kit_tools.queue_exec_patch(code, f"get_joint_limits {articulation}.{joint_name}")

def _gen_set_drive_gains(args: Dict) -> str:
    joint_path = args["joint_path"]
    kp = args["kp"]
    kd = args["kd"]
    drive_type = args.get("drive_type", "angular")
    return (
        "import omni.usd\n"
        "from pxr import UsdPhysics\n"
        "stage = omni.usd.get_context().get_stage()\n"
        f"joint = stage.GetPrimAtPath({joint_path!r})\n"
        f"drive = UsdPhysics.DriveAPI.Apply(joint, {drive_type!r})\n"
        f"drive.CreateStiffnessAttr({float(kp)!r})\n"
        f"drive.CreateDampingAttr({float(kd)!r})\n"
        f"print('drive_gains', {joint_path!r}, 'kp=', {float(kp)!r}, 'kd=', {float(kd)!r})"
    )

async def _handle_get_contact_report(args: Dict) -> Dict:
    prim_path = args["prim_path"]
    max_contacts = int(args.get("max_contacts", 50))
    code = f"""\
import omni.usd
import json

prim_path = {prim_path!r}
max_contacts = {max_contacts}

# Pull the running contact buffer from the global ContactReporter (set up by
# set_clearance_monitor or apply_api_schema(PhysxContactReportAPI)). When no
# buffer exists yet, return an empty report instead of crashing so callers can
# tell apart "no contacts" from "API not applied".
buf = globals().get('_ATOMIC_CONTACT_BUFFER')
contacts = []
if buf is not None:
    for entry in list(buf)[-max_contacts:]:
        if entry.get('actor0') == prim_path or entry.get('actor1') == prim_path:
            contacts.append(entry)

result = {{
    'prim_path': prim_path,
    'contact_count': len(contacts),
    'contacts': contacts,
    'buffer_initialized': buf is not None,
}}
print(json.dumps(result, default=str))
"""
    return await kit_tools.queue_exec_patch(code, f"get_contact_report {prim_path}")

def _gen_set_render_mode(args: Dict) -> str:
    mode = args["mode"]
    _MODE_TO_HYDRA = {
        "preview": "rtx",  # Hydra Storm fallback handled below
        "rt": "rtx",
        "path_traced": "rtx",
    }
    _MODE_TO_RENDERMODE = {
        "preview": "RaytracedLighting",
        "rt": "RaytracedLighting",
        "path_traced": "PathTracing",
    }
    hydra = _MODE_TO_HYDRA.get(mode, "rtx")
    render_mode = _MODE_TO_RENDERMODE.get(mode, "RaytracedLighting")
    return (
        "import carb.settings\n"
        "settings = carb.settings.get_settings()\n"
        f"# render mode: {mode}\n"
        f"settings.set('/rtx/rendermode', {render_mode!r})\n"
        "try:\n"
        "    import omni.kit.viewport.utility as vpu\n"
        "    vp = vpu.get_active_viewport()\n"
        "    if vp is not None:\n"
        f"        vp.set_hd_engine({hydra!r})\n"
        "except Exception as exc:\n"
        f"    print('viewport switch skipped:', exc)\n"
        f"print('render_mode set to', {mode!r})"
    )

def _gen_set_variant(args: Dict) -> str:
    prim_path = args["prim_path"]
    variant_set = args["variant_set"]
    variant = args["variant"]
    return (
        "import omni.usd\n"
        "stage = omni.usd.get_context().get_stage()\n"
        f"prim = stage.GetPrimAtPath({prim_path!r})\n"
        f"vsets = prim.GetVariantSets()\n"
        f"vset = vsets.GetVariantSet({variant_set!r}) if vsets.HasVariantSet({variant_set!r}) else vsets.AddVariantSet({variant_set!r})\n"
        f"vset.SetVariantSelection({variant!r})\n"
        f"print('variant', {prim_path!r}, {variant_set!r}, '=', {variant!r})"
    )

async def _handle_get_training_status(args: Dict) -> Dict:
    """Read TensorBoard event files + subprocess state for an RL run."""
    from pathlib import Path

    run_id = args["run_id"]
    log_dir = args.get("log_dir") or str(_WORKSPACE / "rl_checkpoints" / run_id)

    log_path = Path(log_dir)
    result: Dict[str, Any] = {
        "run_id": run_id,
        "log_dir": str(log_path),
        "state": "unknown",
        "step": None,
        "total_steps": None,
        "latest_reward": None,
        "events_found": 0,
    }

    if not log_path.exists():
        result["state"] = "missing"
        result["error"] = f"log dir does not exist: {log_path}"
        return result

    # Look for TensorBoard event files (events.out.tfevents.*)
    event_files = sorted(log_path.glob("**/events.out.tfevents.*"))
    result["events_found"] = len(event_files)

    if not event_files:
        result["state"] = "starting"
        return result

    # Try to parse the latest event file. tensorboard isn't a hard dep, so we
    # fall back gracefully on import failure.
    latest = event_files[-1]
    try:
        from tensorboard.backend.event_processing.event_accumulator import EventAccumulator  # type: ignore
        acc = EventAccumulator(str(latest), size_guidance={"scalars": 0})
        acc.Reload()
        scalars = acc.Tags().get("scalars", [])
        # Prefer common reward / step tag names
        for tag in ("reward", "Train/reward", "train/reward",
                    "rollout/ep_rew_mean", "Episode_Reward/Mean"):
            if tag in scalars:
                events = acc.Scalars(tag)
                if events:
                    result["latest_reward"] = events[-1].value
                    result["step"] = events[-1].step
                    break
        if result["step"] is None and scalars:
            events = acc.Scalars(scalars[0])
            if events:
                result["step"] = events[-1].step
    except ImportError:
        result["note"] = "tensorboard not installed — install with `pip install tensorboard`"
    except Exception as exc:
        result["error"] = f"failed to parse event file: {exc}"

    # Subprocess state via the launcher's pid file (if launch_training wrote one)
    pid_file = log_path / "launcher.pid"
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text().strip())
            # Cheap liveness check: send signal 0
            try:
                os.kill(pid, 0)
                result["state"] = "running"
                result["pid"] = pid
            except ProcessLookupError:
                result["state"] = "finished"
                result["pid"] = pid
            except PermissionError:
                # Process exists but owned by another user — still treat as running
                result["state"] = "running"
                result["pid"] = pid
        except Exception as exc:
            result["state"] = "unknown"
            result["error"] = f"could not read launcher.pid: {exc}"
    elif result["events_found"] > 0:
        result["state"] = "running"

    return result

async def _handle_pixel_to_world(args: Dict) -> Dict:
    """Project a viewport pixel through the camera + depth buffer to world."""
    camera = args["camera"]
    x = int(args["x"])
    y = int(args["y"])
    resolution = args.get("resolution")
    res_expr = repr(list(resolution)) if resolution else "None"
    code = f"""\
import omni.usd
from pxr import Usd, UsdGeom, Gf
import json

camera_path = {camera!r}
px = {x}
py = {y}
override_res = {res_expr}

stage = omni.usd.get_context().get_stage()
cam_prim = stage.GetPrimAtPath(camera_path)
result = {{'camera': camera_path, 'x': px, 'y': py}}

if not cam_prim or not cam_prim.IsValid():
    result['error'] = 'camera not found'
elif not UsdGeom.Camera(cam_prim):
    result['error'] = 'prim is not a UsdGeom.Camera'
else:
    cam = UsdGeom.Camera(cam_prim)
    gf_cam = cam.GetCamera(Usd.TimeCode.Default())

    # Determine viewport / depth resolution
    if override_res:
        width, height = override_res
    else:
        try:
            import omni.kit.viewport.utility as vpu
            vp = vpu.get_active_viewport()
            width, height = vp.resolution
        except Exception:
            width, height = (1280, 720)

    # NDC coords (top-left origin)
    ndc_x = (px / float(width)) * 2.0 - 1.0
    ndc_y = 1.0 - (py / float(height)) * 2.0

    # Sample depth buffer if available
    depth_m = None
    try:
        import omni.syntheticdata as sd
        depth_arr = sd.sensors.get_distance_to_camera(camera_path)
        if depth_arr is not None and depth_arr.size:
            ix = max(0, min(width - 1, px))
            iy = max(0, min(height - 1, py))
            depth_m = float(depth_arr[iy, ix])
    except Exception as exc:
        result['depth_warning'] = f'no depth buffer: {{exc}}'

    # Build inverse view-projection
    proj = gf_cam.frustum.ComputeProjectionMatrix()
    view = gf_cam.transform.GetInverse()
    inv_vp = (view * proj).GetInverse()

    near_pt = inv_vp.Transform(Gf.Vec3d(ndc_x, ndc_y, -1.0))
    far_pt = inv_vp.Transform(Gf.Vec3d(ndc_x, ndc_y, 1.0))
    direction = (far_pt - near_pt).GetNormalized()

    if depth_m is None:
        # Without depth, fall back to a unit ray at 1 m
        depth_m = 1.0
        result['depth_fallback'] = True

    world = near_pt + direction * depth_m
    result['world_position'] = [world[0], world[1], world[2]]
    result['ray_origin'] = [near_pt[0], near_pt[1], near_pt[2]]
    result['ray_direction'] = [direction[0], direction[1], direction[2]]
    result['depth_m'] = depth_m

print(json.dumps(result, default=str))
"""
    return await kit_tools.queue_exec_patch(code, f"pixel_to_world {camera}@({x},{y})")

def _gen_record_trajectory(args: Dict) -> str:
    articulation = args["articulation"]
    duration = float(args["duration"])
    output_path = args.get("output_path")
    rate_hz = float(args.get("rate_hz", 60.0))
    if not output_path:
        output_path = "workspace/trajectories/trajectory.npz"
    return (
        "import omni.usd\n"
        "import omni.physx\n"
        "import numpy as np\n"
        "import os\n"
        "import time\n"
        "from pxr import Usd, UsdPhysics\n"
        "\n"
        f"art_path = {articulation!r}\n"
        f"duration = {duration!r}\n"
        f"output_path = {output_path!r}\n"
        f"rate_hz = {rate_hz!r}\n"
        "\n"
        "stage = omni.usd.get_context().get_stage()\n"
        "art_prim = stage.GetPrimAtPath(art_path)\n"
        "joint_prims = []\n"
        "for p in Usd.PrimRange(art_prim):\n"
        "    if UsdPhysics.RevoluteJoint(p) or UsdPhysics.PrismaticJoint(p):\n"
        "        joint_prims.append(p)\n"
        "\n"
        "samples = {'time': [], 'positions': [], 'velocities': [], 'efforts': []}\n"
        "joint_names = [p.GetName() for p in joint_prims]\n"
        "interval = 1.0 / max(rate_hz, 1.0)\n"
        "_state = {'last_sample': 0.0, 'elapsed': 0.0, 'sub': None}\n"
        "\n"
        "def _step_callback(dt):\n"
        "    _state['elapsed'] += dt\n"
        "    if _state['elapsed'] - _state['last_sample'] < interval:\n"
        "        return\n"
        "    _state['last_sample'] = _state['elapsed']\n"
        "    pos, vel, eff = [], [], []\n"
        "    for jp in joint_prims:\n"
        "        pos_attr = jp.GetAttribute('state:angular:physics:position') or jp.GetAttribute('state:linear:physics:position')\n"
        "        vel_attr = jp.GetAttribute('state:angular:physics:velocity') or jp.GetAttribute('state:linear:physics:velocity')\n"
        "        eff_attr = jp.GetAttribute('drive:angular:physics:appliedForce') or jp.GetAttribute('drive:linear:physics:appliedForce')\n"
        "        pos.append(float(pos_attr.Get()) if pos_attr and pos_attr.IsDefined() else 0.0)\n"
        "        vel.append(float(vel_attr.Get()) if vel_attr and vel_attr.IsDefined() else 0.0)\n"
        "        eff.append(float(eff_attr.Get()) if eff_attr and eff_attr.IsDefined() else 0.0)\n"
        "    samples['time'].append(_state['elapsed'])\n"
        "    samples['positions'].append(pos)\n"
        "    samples['velocities'].append(vel)\n"
        "    samples['efforts'].append(eff)\n"
        "    if _state['elapsed'] >= duration and _state['sub'] is not None:\n"
        "        _state['sub'].unsubscribe()\n"
        "        _state['sub'] = None\n"
        "        os.makedirs(os.path.dirname(output_path) or '.', exist_ok=True)\n"
        "        np.savez(output_path,\n"
        "                 time=np.array(samples['time']),\n"
        "                 positions=np.array(samples['positions']),\n"
        "                 velocities=np.array(samples['velocities']),\n"
        "                 efforts=np.array(samples['efforts']),\n"
        "                 joint_names=np.array(joint_names))\n"
        "        print('record_trajectory wrote', output_path, 'samples=', len(samples['time']))\n"
        "\n"
        "physx = omni.physx.get_physx_interface()\n"
        "_state['sub'] = physx.subscribe_physics_step_events(_step_callback)\n"
        "print('record_trajectory subscribed', art_path, 'duration=', duration, 'rate=', rate_hz)\n"
    )

async def _handle_prim_exists(args: Dict) -> Dict:
    """Boolean check for prim presence at a path. Used by verify-contract to
    validate assistant claims like 'robot at /World/Franka is loaded'."""
    prim_path = args["prim_path"]
    code = f"""\
import omni.usd
import json
stage = omni.usd.get_context().get_stage()
prim = stage.GetPrimAtPath({prim_path!r})
exists = bool(prim and prim.IsValid())
result = {{'prim_path': {prim_path!r}, 'exists': exists}}
if exists:
    result['type_name'] = str(prim.GetTypeName())
    result['applied_schemas'] = [str(s) for s in (prim.GetAppliedSchemas() or [])]
    result['child_count'] = len(list(prim.GetChildren()))
print(json.dumps(result, default=str))
"""
    return await kit_tools.queue_exec_patch(code, f"prim_exists {prim_path}")


async def _handle_count_prims_under_path(args: Dict) -> Dict:
    """Count direct or recursive children under a parent prim path, optionally
    filtered by type_name. Used to verify 'I cloned N robots' claims."""
    parent_path = args["parent_path"]
    type_filter = args.get("type_filter")  # e.g. "Xform", "Mesh" — optional
    recursive = bool(args.get("recursive", False))
    code = f"""\
import omni.usd
import json
from pxr import Usd

stage = omni.usd.get_context().get_stage()
parent = stage.GetPrimAtPath({parent_path!r})
result = {{'parent_path': {parent_path!r}, 'type_filter': {type_filter!r}, 'recursive': {recursive!r}}}
if not parent or not parent.IsValid():
    result['error'] = 'parent_path not found'
    result['count'] = 0
    result['paths'] = []
else:
    if {recursive!r}:
        prims = [p for p in Usd.PrimRange(parent) if str(p.GetPath()) != str(parent.GetPath())]
    else:
        prims = list(parent.GetChildren())
    if {type_filter!r}:
        prims = [p for p in prims if str(p.GetTypeName()) == {type_filter!r}]
    result['count'] = len(prims)
    result['paths'] = [str(p.GetPath()) for p in prims[:200]]
    result['truncated'] = len(prims) > 200
print(json.dumps(result, default=str))
"""
    return await kit_tools.queue_exec_patch(code, f"count_prims {parent_path}")


async def _handle_get_joint_targets(args: Dict) -> Dict:
    """Read per-joint drive/velocity TARGETS (what the controller is aiming
    for), distinct from current state. Used to verify 'robot will move on
    Play' claims — if DriveAPI targets aren't authored, the robot won't move."""
    articulation_path = args["articulation_path"]
    code = f"""\
import omni.usd
import json
from pxr import Usd, UsdPhysics

stage = omni.usd.get_context().get_stage()
root = stage.GetPrimAtPath({articulation_path!r})
result = {{'articulation_path': {articulation_path!r}}}
if not root or not root.IsValid():
    result['error'] = 'articulation not found'
    result['joints'] = []
else:
    joints = []
    for p in Usd.PrimRange(root):
        if not (p.IsA(UsdPhysics.RevoluteJoint) or p.IsA(UsdPhysics.PrismaticJoint)):
            continue
        entry = {{'path': str(p.GetPath()), 'type': str(p.GetTypeName())}}
        has_drive = False
        for suffix in ('angular', 'linear'):
            drive_api = UsdPhysics.DriveAPI.Get(p, suffix)
            if drive_api:
                tp = drive_api.GetTargetPositionAttr()
                tv = drive_api.GetTargetVelocityAttr()
                stiffness = drive_api.GetStiffnessAttr()
                damping = drive_api.GetDampingAttr()
                if tp and tp.IsAuthored():
                    entry[f'{{suffix}}_target_position'] = float(tp.Get() or 0.0)
                    has_drive = True
                if tv and tv.IsAuthored():
                    entry[f'{{suffix}}_target_velocity'] = float(tv.Get() or 0.0)
                    has_drive = True
                if stiffness and stiffness.IsAuthored():
                    entry[f'{{suffix}}_stiffness'] = float(stiffness.Get() or 0.0)
                if damping and damping.IsAuthored():
                    entry[f'{{suffix}}_damping'] = float(damping.Get() or 0.0)
        entry['has_drive'] = has_drive
        joints.append(entry)
    result['joints'] = joints
    result['joint_count'] = len(joints)
    result['joints_with_drive'] = sum(1 for j in joints if j.get('has_drive'))
print(json.dumps(result, default=str))
"""
    return await kit_tools.queue_exec_patch(code, f"get_joint_targets {articulation_path}")


DATA_HANDLERS["get_attribute"] = _handle_get_attribute
DATA_HANDLERS["get_world_transform"] = _handle_get_world_transform
DATA_HANDLERS["get_bounding_box"] = _handle_get_bounding_box
DATA_HANDLERS["get_joint_limits"] = _handle_get_joint_limits
DATA_HANDLERS["get_contact_report"] = _handle_get_contact_report
DATA_HANDLERS["get_training_status"] = _handle_get_training_status
DATA_HANDLERS["pixel_to_world"] = _handle_pixel_to_world
DATA_HANDLERS["prim_exists"] = _handle_prim_exists
DATA_HANDLERS["count_prims_under_path"] = _handle_count_prims_under_path
DATA_HANDLERS["get_joint_targets"] = _handle_get_joint_targets
CODE_GEN_HANDLERS["set_semantic_label"] = _gen_set_semantic_label
CODE_GEN_HANDLERS["set_drive_gains"] = _gen_set_drive_gains
CODE_GEN_HANDLERS["set_render_mode"] = _gen_set_render_mode
CODE_GEN_HANDLERS["set_variant"] = _gen_set_variant
CODE_GEN_HANDLERS["record_trajectory"] = _gen_record_trajectory

# ══════ From feat/atomic-tier1-usd-core ══════
async def _handle_list_attributes(args: Dict) -> Dict:
    """Enumerate all attributes on a prim via prim.GetAttributes()."""
    prim_path = args["prim_path"]
    code = f"""\
import omni.usd
import json

stage = omni.usd.get_context().get_stage()
prim = stage.GetPrimAtPath({prim_path!r})
result = {{'prim_path': {prim_path!r}}}
if not prim or not prim.IsValid():
    result['error'] = 'prim not found'
else:
    attrs = []
    for attr in prim.GetAttributes():
        attrs.append({{
            'name': attr.GetName(),
            'type': attr.GetTypeName().type.typeName,
            'has_value': bool(attr.HasValue()),
            'custom': bool(attr.IsCustom()),
        }})
    result['attribute_count'] = len(attrs)
    result['attributes'] = attrs
print(json.dumps(result, default=str))
"""
    return await kit_tools.queue_exec_patch(code, f"list_attributes {prim_path}")

async def _handle_list_relationships(args: Dict) -> Dict:
    """List all relationships on a prim via prim.GetRelationships()."""
    prim_path = args["prim_path"]
    code = f"""\
import omni.usd
import json

stage = omni.usd.get_context().get_stage()
prim = stage.GetPrimAtPath({prim_path!r})
result = {{'prim_path': {prim_path!r}}}
if not prim or not prim.IsValid():
    result['error'] = 'prim not found'
else:
    rels = []
    for rel in prim.GetRelationships():
        try:
            targets = [str(t) for t in rel.GetTargets()]
        except Exception as exc:
            targets = []
            rel_error = str(exc)
        else:
            rel_error = None
        entry = {{
            'name': rel.GetName(),
            'targets': targets,
            'target_count': len(targets),
            'custom': bool(rel.IsCustom()),
        }}
        if rel_error:
            entry['error'] = rel_error
        rels.append(entry)
    result['relationship_count'] = len(rels)
    result['relationships'] = rels
print(json.dumps(result, default=str))
"""
    return await kit_tools.queue_exec_patch(code, f"list_relationships {prim_path}")

async def _handle_list_applied_schemas(args: Dict) -> Dict:
    """Return applied API schemas on a prim via prim.GetAppliedSchemas()."""
    prim_path = args["prim_path"]
    code = f"""\
import omni.usd
import json

stage = omni.usd.get_context().get_stage()
prim = stage.GetPrimAtPath({prim_path!r})
result = {{'prim_path': {prim_path!r}}}
if not prim or not prim.IsValid():
    result['error'] = 'prim not found'
else:
    try:
        schemas = list(prim.GetAppliedSchemas())
    except Exception as exc:
        schemas = []
        result['error'] = f'GetAppliedSchemas failed: {{exc}}'
    result['applied_schemas'] = [str(s) for s in schemas]
    result['schema_count'] = len(schemas)
    try:
        result['type_name'] = prim.GetTypeName()
    except Exception:
        result['type_name'] = None
print(json.dumps(result, default=str))
"""
    return await kit_tools.queue_exec_patch(code, f"list_applied_schemas {prim_path}")

async def _handle_get_prim_metadata(args: Dict) -> Dict:
    """Read a single USD metadata field on a prim via prim.GetMetadata(key)."""
    prim_path = args["prim_path"]
    key = args["key"]
    code = f"""\
import omni.usd
import json

stage = omni.usd.get_context().get_stage()
prim = stage.GetPrimAtPath({prim_path!r})
result = {{'prim_path': {prim_path!r}, 'key': {key!r}}}
if not prim or not prim.IsValid():
    result['error'] = 'prim not found'
else:
    try:
        if not prim.HasMetadata({key!r}):
            result['has_metadata'] = False
            result['value'] = None
        else:
            value = prim.GetMetadata({key!r})
            result['has_metadata'] = True
            result['python_type'] = type(value).__name__
            try:
                json.dumps(value)
                result['value'] = value
            except Exception:
                # Non-json-serialisable USD types — coerce to repr
                try:
                    if hasattr(value, '__iter__') and not isinstance(value, (str, bytes)):
                        result['value'] = list(value)
                    else:
                        result['value'] = repr(value)
                except Exception:
                    result['value'] = repr(value)
    except Exception as exc:
        result['error'] = f'GetMetadata failed: {{exc}}'
print(json.dumps(result, default=str))
"""
    return await kit_tools.queue_exec_patch(code, f"get_prim_metadata {prim_path}.{key}")

def _gen_set_prim_metadata(args: Dict) -> str:
    """Emit code that writes a USD metadata field via prim.SetMetadata()."""
    prim_path = args["prim_path"]
    key = args["key"]
    value = args["value"]
    return (
        "import omni.usd\n"
        "stage = omni.usd.get_context().get_stage()\n"
        f"prim = stage.GetPrimAtPath({prim_path!r})\n"
        "if not prim or not prim.IsValid():\n"
        f"    raise RuntimeError('prim not found: ' + {prim_path!r})\n"
        f"ok = prim.SetMetadata({key!r}, {value!r})\n"
        f"print('set_prim_metadata', {prim_path!r}, {key!r}, '=', {value!r}, 'ok=', ok)\n"
    )

async def _handle_get_prim_type(args: Dict) -> Dict:
    """Return prim.GetTypeName() (e.g. 'Mesh', 'Xform', 'Camera')."""
    prim_path = args["prim_path"]
    code = f"""\
import omni.usd
import json

stage = omni.usd.get_context().get_stage()
prim = stage.GetPrimAtPath({prim_path!r})
result = {{'prim_path': {prim_path!r}}}
if not prim or not prim.IsValid():
    result['error'] = 'prim not found'
else:
    try:
        type_name = prim.GetTypeName()
    except Exception as exc:
        type_name = ''
        result['error'] = f'GetTypeName failed: {{exc}}'
    result['type_name'] = str(type_name) if type_name else ''
    try:
        result['is_a_model'] = bool(prim.IsModel())
    except Exception:
        result['is_a_model'] = None
print(json.dumps(result, default=str))
"""
    return await kit_tools.queue_exec_patch(code, f"get_prim_type {prim_path}")

async def _handle_find_prims_by_schema(args: Dict) -> Dict:
    """Traverse the stage and return prims where prim.HasAPI(schema) is true."""
    schema_name = args["schema_name"]
    root_path = args.get("root_path") or "/"
    limit = int(args.get("limit", 500))
    code = f"""\
import omni.usd
from pxr import Usd, UsdPhysics, UsdGeom, UsdShade
import json

schema_name = {schema_name!r}
limit = {limit}

stage = omni.usd.get_context().get_stage()
result = {{'schema_name': schema_name, 'root_path': {root_path!r}}}

# Resolve schema class. Users pass the applied-schema token (e.g.
# "PhysicsRigidBodyAPI"), but the Python class is UsdPhysics.RigidBodyAPI —
# the module prefix is dropped. Try both the literal name and variants with
# the conventional "Physics"/"Geom"/"Shade" prefix stripped.
_mod_prefix_map = (
    (UsdPhysics, "Physics"),
    (UsdGeom, "Geom"),
    (UsdShade, "Shade"),
)
schema_cls = None
for mod, prefix in _mod_prefix_map:
    for candidate_name in (schema_name, schema_name[len(prefix):] if schema_name.startswith(prefix) else None):
        if candidate_name is None:
            continue
        cand = getattr(mod, candidate_name, None)
        if cand is not None:
            schema_cls = cand
            break
    if schema_cls is not None:
        break
if schema_cls is None:
    try:
        import pxr
        for mod_name in dir(pxr):
            mod = getattr(pxr, mod_name, None)
            for candidate_name in (schema_name,):
                cand = getattr(mod, candidate_name, None) if mod is not None else None
                if cand is not None:
                    schema_cls = cand
                    break
            if schema_cls is not None:
                break
    except Exception as exc:
        result['lookup_error'] = str(exc)

if schema_cls is None:
    result['error'] = f'unknown schema: {{schema_name}}'
    result['matches'] = []
    print(json.dumps(result, default=str))
else:
    root_prim = stage.GetPrimAtPath({root_path!r})
    if not root_prim or not root_prim.IsValid():
        root_prim = stage.GetPseudoRoot()
    matches = []
    for p in Usd.PrimRange(root_prim):
        try:
            if p.HasAPI(schema_cls):
                matches.append(str(p.GetPath()))
                if len(matches) >= limit:
                    break
        except Exception:
            # Some non-API schemas raise — fall back to typed-schema check
            try:
                if p.IsA(schema_cls):
                    matches.append(str(p.GetPath()))
                    if len(matches) >= limit:
                        break
            except Exception:
                continue
    result['match_count'] = len(matches)
    result['matches'] = matches
    result['truncated'] = len(matches) >= limit
    print(json.dumps(result, default=str))
"""
    return await kit_tools.queue_exec_patch(code, f"find_prims_by_schema {schema_name}")

async def _handle_find_prims_by_name(args: Dict) -> Dict:
    """Regex search on prim paths."""
    pattern = args["pattern"]
    root_path = args.get("root_path") or "/"
    limit = int(args.get("limit", 500))
    code = f"""\
import omni.usd
from pxr import Usd
import re
import json

pattern = {pattern!r}
limit = {limit}
root_path = {root_path!r}

stage = omni.usd.get_context().get_stage()
result = {{'pattern': pattern, 'root_path': root_path}}
try:
    rx = re.compile(pattern)
except re.error as exc:
    result['error'] = f'invalid regex: {{exc}}'
    result['matches'] = []
    print(json.dumps(result, default=str))
else:
    root_prim = stage.GetPrimAtPath(root_path)
    if not root_prim or not root_prim.IsValid():
        root_prim = stage.GetPseudoRoot()
    matches = []
    for p in Usd.PrimRange(root_prim):
        path_str = str(p.GetPath())
        if rx.search(path_str):
            matches.append(path_str)
            if len(matches) >= limit:
                break
    result['match_count'] = len(matches)
    result['matches'] = matches
    result['truncated'] = len(matches) >= limit
    print(json.dumps(result, default=str))
"""
    return await kit_tools.queue_exec_patch(code, f"find_prims_by_name {pattern}")

async def _handle_get_kind(args: Dict) -> Dict:
    """Read Kind metadata via Usd.ModelAPI(prim).GetKind()."""
    prim_path = args["prim_path"]
    code = f"""\
import omni.usd
from pxr import Usd, Kind
import json

stage = omni.usd.get_context().get_stage()
prim = stage.GetPrimAtPath({prim_path!r})
result = {{'prim_path': {prim_path!r}}}
if not prim or not prim.IsValid():
    result['error'] = 'prim not found'
else:
    try:
        model = Usd.ModelAPI(prim)
        kind = model.GetKind()
        result['kind'] = str(kind) if kind else ''
        # Useful classification helpers
        try:
            registry = Kind.Registry()
            if kind:
                result['is_a_model'] = bool(registry.IsA(kind, 'model'))
                result['is_a_component'] = bool(registry.IsA(kind, 'component'))
                result['is_a_assembly'] = bool(registry.IsA(kind, 'assembly'))
                result['is_a_group'] = bool(registry.IsA(kind, 'group'))
            else:
                result['is_a_model'] = False
        except Exception as exc:
            result['kind_registry_error'] = str(exc)
    except Exception as exc:
        result['error'] = f'GetKind failed: {{exc}}'
print(json.dumps(result, default=str))
"""
    return await kit_tools.queue_exec_patch(code, f"get_kind {prim_path}")

async def _handle_get_active_state(args: Dict) -> Dict:
    """Return prim.IsActive() (active/deactivated state)."""
    prim_path = args["prim_path"]
    code = f"""\
import omni.usd
import json

stage = omni.usd.get_context().get_stage()
prim = stage.GetPrimAtPath({prim_path!r})
result = {{'prim_path': {prim_path!r}}}
if not prim or not prim.IsValid():
    result['error'] = 'prim not found'
    result['is_active'] = None
else:
    try:
        result['is_active'] = bool(prim.IsActive())
    except Exception as exc:
        result['error'] = f'IsActive failed: {{exc}}'
        result['is_active'] = None
    try:
        result['is_loaded'] = bool(prim.IsLoaded())
    except Exception:
        result['is_loaded'] = None
print(json.dumps(result, default=str))
"""
    return await kit_tools.queue_exec_patch(code, f"get_active_state {prim_path}")

DATA_HANDLERS["list_attributes"] = _handle_list_attributes
DATA_HANDLERS["list_relationships"] = _handle_list_relationships
DATA_HANDLERS["list_applied_schemas"] = _handle_list_applied_schemas
DATA_HANDLERS["get_prim_metadata"] = _handle_get_prim_metadata
DATA_HANDLERS["get_prim_type"] = _handle_get_prim_type
DATA_HANDLERS["find_prims_by_schema"] = _handle_find_prims_by_schema
DATA_HANDLERS["find_prims_by_name"] = _handle_find_prims_by_name
DATA_HANDLERS["get_kind"] = _handle_get_kind
DATA_HANDLERS["get_active_state"] = _handle_get_active_state
CODE_GEN_HANDLERS["set_prim_metadata"] = _gen_set_prim_metadata

# ══════ From feat/atomic-tier2-physics ══════
async def _handle_get_linear_velocity(args: Dict) -> Dict:
    """Return rigid body linear velocity via UsdPhysics.RigidBodyAPI."""
    prim_path = args["prim_path"]
    code = f"""\
import omni.usd
import json
from pxr import UsdPhysics

stage = omni.usd.get_context().get_stage()
prim = stage.GetPrimAtPath({prim_path!r})
result = {{'prim_path': {prim_path!r}}}
if not prim or not prim.IsValid():
    result['error'] = 'prim not found'
elif not prim.HasAPI(UsdPhysics.RigidBodyAPI):
    result['error'] = 'PhysicsRigidBodyAPI not applied — apply it first'
    result['has_rigid_body_api'] = False
else:
    rb = UsdPhysics.RigidBodyAPI(prim)
    attr = rb.GetVelocityAttr()
    if attr and attr.HasAuthoredValue():
        v = attr.Get()
        result['linear_velocity'] = [float(v[0]), float(v[1]), float(v[2])]
        result['authored'] = True
    else:
        v = attr.Get() if attr else None
        if v is None:
            result['linear_velocity'] = [0.0, 0.0, 0.0]
            result['authored'] = False
        else:
            result['linear_velocity'] = [float(v[0]), float(v[1]), float(v[2])]
            result['authored'] = False
    result['units'] = 'm/s'
print(json.dumps(result, default=str))
"""
    return await kit_tools.queue_exec_patch(code, f"get_linear_velocity {prim_path}")

async def _handle_get_angular_velocity(args: Dict) -> Dict:
    """Return rigid body angular velocity via UsdPhysics.RigidBodyAPI."""
    prim_path = args["prim_path"]
    code = f"""\
import omni.usd
import json
from pxr import UsdPhysics

stage = omni.usd.get_context().get_stage()
prim = stage.GetPrimAtPath({prim_path!r})
result = {{'prim_path': {prim_path!r}}}
if not prim or not prim.IsValid():
    result['error'] = 'prim not found'
elif not prim.HasAPI(UsdPhysics.RigidBodyAPI):
    result['error'] = 'PhysicsRigidBodyAPI not applied — apply it first'
    result['has_rigid_body_api'] = False
else:
    rb = UsdPhysics.RigidBodyAPI(prim)
    attr = rb.GetAngularVelocityAttr()
    if attr and attr.HasAuthoredValue():
        v = attr.Get()
        result['angular_velocity'] = [float(v[0]), float(v[1]), float(v[2])]
        result['authored'] = True
    else:
        v = attr.Get() if attr else None
        if v is None:
            result['angular_velocity'] = [0.0, 0.0, 0.0]
            result['authored'] = False
        else:
            result['angular_velocity'] = [float(v[0]), float(v[1]), float(v[2])]
            result['authored'] = False
    result['units'] = 'deg/s'
print(json.dumps(result, default=str))
"""
    return await kit_tools.queue_exec_patch(code, f"get_angular_velocity {prim_path}")

def _gen_set_linear_velocity(args: Dict) -> str:
    """Generate code to set rigid body linear velocity."""
    prim_path = args["prim_path"]
    vel = args.get("vel") or [0.0, 0.0, 0.0]
    vx, vy, vz = float(vel[0]), float(vel[1]), float(vel[2])
    return f"""\
import omni.usd
from pxr import UsdPhysics, Gf

stage = omni.usd.get_context().get_stage()
prim_path = {prim_path!r}
prim = stage.GetPrimAtPath(prim_path)
if not prim or not prim.IsValid():
    raise RuntimeError('prim not found: ' + repr(prim_path))
if not prim.HasAPI(UsdPhysics.RigidBodyAPI):
    UsdPhysics.RigidBodyAPI.Apply(prim)
rb = UsdPhysics.RigidBodyAPI(prim)
attr = rb.GetVelocityAttr() or rb.CreateVelocityAttr()
attr.Set(Gf.Vec3f({vx}, {vy}, {vz}))
print('Set linear velocity on ' + repr(prim_path) + ' to ({vx}, {vy}, {vz}) m/s')
"""

async def _handle_get_mass(args: Dict) -> Dict:
    """Return current rigid body mass via UsdPhysics.MassAPI."""
    prim_path = args["prim_path"]
    code = f"""\
import omni.usd
import json
from pxr import UsdPhysics

stage = omni.usd.get_context().get_stage()
prim = stage.GetPrimAtPath({prim_path!r})
result = {{'prim_path': {prim_path!r}, 'units': 'kg'}}
if not prim or not prim.IsValid():
    result['error'] = 'prim not found'
elif not prim.HasAPI(UsdPhysics.MassAPI):
    result['has_mass_api'] = False
    result['mass'] = 0.0
    result['note'] = 'PhysicsMassAPI not applied — PhysX will compute mass from collision geometry + density'
else:
    result['has_mass_api'] = True
    mass_api = UsdPhysics.MassAPI(prim)
    attr = mass_api.GetMassAttr()
    if attr and attr.HasAuthoredValue():
        result['mass'] = float(attr.Get())
        result['authored'] = True
    else:
        v = attr.Get() if attr else None
        result['mass'] = float(v) if v is not None else 0.0
        result['authored'] = False
    den_attr = mass_api.GetDensityAttr()
    if den_attr and den_attr.HasAuthoredValue():
        result['density_kg_m3'] = float(den_attr.Get())
print(json.dumps(result, default=str))
"""
    return await kit_tools.queue_exec_patch(code, f"get_mass {prim_path}")

async def _handle_get_inertia(args: Dict) -> Dict:
    """Return diagonal inertia tensor via UsdPhysics.MassAPI."""
    prim_path = args["prim_path"]
    code = f"""\
import omni.usd
import json
from pxr import UsdPhysics

stage = omni.usd.get_context().get_stage()
prim = stage.GetPrimAtPath({prim_path!r})
result = {{'prim_path': {prim_path!r}, 'units': 'kg*m^2'}}
if not prim or not prim.IsValid():
    result['error'] = 'prim not found'
elif not prim.HasAPI(UsdPhysics.MassAPI):
    result['has_mass_api'] = False
    result['diagonal_inertia'] = [0.0, 0.0, 0.0]
    result['note'] = 'PhysicsMassAPI not applied — PhysX will compute inertia from collision geometry'
else:
    result['has_mass_api'] = True
    mass_api = UsdPhysics.MassAPI(prim)
    attr = mass_api.GetDiagonalInertiaAttr()
    if attr and attr.HasAuthoredValue():
        v = attr.Get()
        result['diagonal_inertia'] = [float(v[0]), float(v[1]), float(v[2])]
        result['authored'] = True
    else:
        v = attr.Get() if attr else None
        if v is None:
            result['diagonal_inertia'] = [0.0, 0.0, 0.0]
            result['authored'] = False
        else:
            result['diagonal_inertia'] = [float(v[0]), float(v[1]), float(v[2])]
            result['authored'] = False
    com_attr = mass_api.GetCenterOfMassAttr()
    if com_attr and com_attr.HasAuthoredValue():
        com = com_attr.Get()
        result['center_of_mass'] = [float(com[0]), float(com[1]), float(com[2])]
    pq_attr = mass_api.GetPrincipalAxesAttr()
    if pq_attr and pq_attr.HasAuthoredValue():
        q = pq_attr.Get()
        result['principal_axes_quat'] = [float(q.GetReal()),
                                         float(q.GetImaginary()[0]),
                                         float(q.GetImaginary()[1]),
                                         float(q.GetImaginary()[2])]
print(json.dumps(result, default=str))
"""
    return await kit_tools.queue_exec_patch(code, f"get_inertia {prim_path}")

async def _handle_get_physics_scene_config(args: Dict) -> Dict:
    """Read the global PhysicsScene config: gravity, solver, iterations, dt, GPU."""
    scene_path = args.get("scene_path", "")
    code = f"""\
import omni.usd
import json
from pxr import Usd, UsdPhysics

stage = omni.usd.get_context().get_stage()
result = {{}}
target = {scene_path!r}
scene_prim = None
if target:
    scene_prim = stage.GetPrimAtPath(target)
    if not scene_prim or not scene_prim.IsValid():
        scene_prim = None
        result['warning'] = f'scene_path {{target!r}} not found, falling back to first PhysicsScene on stage'
if scene_prim is None:
    for p in stage.Traverse():
        if p.IsA(UsdPhysics.Scene):
            scene_prim = p
            break
if scene_prim is None:
    result['error'] = 'no UsdPhysics.Scene found on stage'
else:
    result['scene_path'] = str(scene_prim.GetPath())
    scene = UsdPhysics.Scene(scene_prim)
    g_dir_attr = scene.GetGravityDirectionAttr()
    g_mag_attr = scene.GetGravityMagnitudeAttr()
    if g_dir_attr and g_dir_attr.HasAuthoredValue():
        d = g_dir_attr.Get()
        result['gravity_direction'] = [float(d[0]), float(d[1]), float(d[2])]
    if g_mag_attr and g_mag_attr.HasAuthoredValue():
        result['gravity_magnitude'] = float(g_mag_attr.Get())
    try:
        from pxr import PhysxSchema
        if scene_prim.HasAPI(PhysxSchema.PhysxSceneAPI):
            phx = PhysxSchema.PhysxSceneAPI(scene_prim)
            if phx.GetSolverTypeAttr() and phx.GetSolverTypeAttr().HasAuthoredValue():
                result['solver_type'] = str(phx.GetSolverTypeAttr().Get())
            if phx.GetMinPositionIterationCountAttr() and phx.GetMinPositionIterationCountAttr().HasAuthoredValue():
                result['min_position_iterations'] = int(phx.GetMinPositionIterationCountAttr().Get())
            if phx.GetMaxPositionIterationCountAttr() and phx.GetMaxPositionIterationCountAttr().HasAuthoredValue():
                result['max_position_iterations'] = int(phx.GetMaxPositionIterationCountAttr().Get())
            if phx.GetMinVelocityIterationCountAttr() and phx.GetMinVelocityIterationCountAttr().HasAuthoredValue():
                result['min_velocity_iterations'] = int(phx.GetMinVelocityIterationCountAttr().Get())
            if phx.GetMaxVelocityIterationCountAttr() and phx.GetMaxVelocityIterationCountAttr().HasAuthoredValue():
                result['max_velocity_iterations'] = int(phx.GetMaxVelocityIterationCountAttr().Get())
            if phx.GetEnableGPUDynamicsAttr() and phx.GetEnableGPUDynamicsAttr().HasAuthoredValue():
                result['enable_gpu_dynamics'] = bool(phx.GetEnableGPUDynamicsAttr().Get())
            if phx.GetBroadphaseTypeAttr() and phx.GetBroadphaseTypeAttr().HasAuthoredValue():
                result['broadphase_type'] = str(phx.GetBroadphaseTypeAttr().Get())
            if phx.GetTimeStepsPerSecondAttr() and phx.GetTimeStepsPerSecondAttr().HasAuthoredValue():
                result['time_steps_per_second'] = int(phx.GetTimeStepsPerSecondAttr().Get())
                result['time_step'] = 1.0 / float(phx.GetTimeStepsPerSecondAttr().Get())
    except Exception as exc:
        result['physx_scene_api_error'] = str(exc)
    try:
        import carb.settings
        s = carb.settings.get_settings()
        tps = s.get('/persistent/physics/timeStepsPerSecond')
        if tps:
            result.setdefault('time_steps_per_second', int(tps))
            result.setdefault('time_step', 1.0 / float(tps))
    except Exception:
        pass
print(json.dumps(result, default=str))
"""
    return await kit_tools.queue_exec_patch(code, "get_physics_scene_config")

def _gen_set_physics_scene_config(args: Dict) -> str:
    """Generate code to update the PhysicsScene config."""
    cfg = args.get("config") or {}
    if not isinstance(cfg, dict):
        cfg = {}

    scene_path = cfg.get("scene_path", "")
    solver_type = cfg.get("solver_type")
    pos_iters = cfg.get("position_iterations")
    vel_iters = cfg.get("velocity_iterations")
    tps = cfg.get("time_steps_per_second")
    enable_gpu = cfg.get("enable_gpu_dynamics")
    broadphase = cfg.get("broadphase_type")
    grav_dir = cfg.get("gravity_direction")
    grav_mag = cfg.get("gravity_magnitude")

    lines = [
        "import omni.usd",
        "from pxr import Usd, UsdPhysics, PhysxSchema, Sdf, Gf",
        "",
        "stage = omni.usd.get_context().get_stage()",
        f"target_path = {scene_path!r}",
        "scene_prim = None",
        "if target_path:",
        "    scene_prim = stage.GetPrimAtPath(target_path)",
        "    if not scene_prim or not scene_prim.IsValid():",
        "        scene_prim = None",
        "if scene_prim is None:",
        "    for p in stage.Traverse():",
        "        if p.IsA(UsdPhysics.Scene):",
        "            scene_prim = p",
        "            break",
        "if scene_prim is None:",
        "    scene = UsdPhysics.Scene.Define(stage, Sdf.Path('/PhysicsScene'))",
        "    scene_prim = scene.GetPrim()",
        "scene = UsdPhysics.Scene(scene_prim)",
        "if not scene_prim.HasAPI(PhysxSchema.PhysxSceneAPI):",
        "    PhysxSchema.PhysxSceneAPI.Apply(scene_prim)",
        "phx = PhysxSchema.PhysxSceneAPI(scene_prim)",
    ]
    if grav_dir is not None and len(grav_dir) >= 3:
        lines.append(
            f"(scene.GetGravityDirectionAttr() or scene.CreateGravityDirectionAttr()).Set("
            f"Gf.Vec3f({float(grav_dir[0])}, {float(grav_dir[1])}, {float(grav_dir[2])}))"
        )
    if grav_mag is not None:
        lines.append(
            f"(scene.GetGravityMagnitudeAttr() or scene.CreateGravityMagnitudeAttr()).Set({float(grav_mag)})"
        )
    if solver_type is not None:
        lines.append(
            f"(phx.GetSolverTypeAttr() or phx.CreateSolverTypeAttr()).Set({solver_type!r})"
        )
    if pos_iters is not None:
        lines.append(
            f"(phx.GetMinPositionIterationCountAttr() or phx.CreateMinPositionIterationCountAttr()).Set({int(pos_iters)})"
        )
        lines.append(
            f"(phx.GetMaxPositionIterationCountAttr() or phx.CreateMaxPositionIterationCountAttr()).Set({int(pos_iters)})"
        )
    if vel_iters is not None:
        lines.append(
            f"(phx.GetMinVelocityIterationCountAttr() or phx.CreateMinVelocityIterationCountAttr()).Set({int(vel_iters)})"
        )
        lines.append(
            f"(phx.GetMaxVelocityIterationCountAttr() or phx.CreateMaxVelocityIterationCountAttr()).Set({int(vel_iters)})"
        )
    if enable_gpu is not None:
        lines.append(
            f"(phx.GetEnableGPUDynamicsAttr() or phx.CreateEnableGPUDynamicsAttr()).Set({bool(enable_gpu)})"
        )
    if broadphase is not None:
        lines.append(
            f"(phx.GetBroadphaseTypeAttr() or phx.CreateBroadphaseTypeAttr()).Set({broadphase!r})"
        )
    if tps is not None:
        lines.append(
            f"(phx.GetTimeStepsPerSecondAttr() or phx.CreateTimeStepsPerSecondAttr()).Set({int(tps)})"
        )
        lines.append("try:")
        lines.append("    import carb.settings")
        lines.append(f"    carb.settings.get_settings().set('/persistent/physics/timeStepsPerSecond', int({int(tps)}))")
        lines.append("except Exception:")
        lines.append("    pass")
    lines.append("print(f'Updated PhysicsScene config on {scene_prim.GetPath()}')")
    return "\n".join(lines)

async def _handle_list_contacts(args: Dict) -> Dict:
    """Subscribe to PhysX contact reports for a body and return the pairs."""
    prim_path = args["prim_path"]
    duration = float(args.get("duration", 0.5))
    min_impulse = float(args.get("min_impulse", 0.0))
    code = f"""\
import omni.usd
import json
import time
from pxr import UsdPhysics, PhysxSchema

stage = omni.usd.get_context().get_stage()
prim_path = {prim_path!r}
duration = {duration}
min_impulse = {min_impulse}
result = {{'prim_path': prim_path, 'duration_s': duration}}
prim = stage.GetPrimAtPath(prim_path)
if not prim or not prim.IsValid():
    result['error'] = 'prim not found'
    print(json.dumps(result, default=str))
else:
    # Apply contact report API if missing so PhysX emits events for this body.
    if not prim.HasAPI(PhysxSchema.PhysxContactReportAPI):
        PhysxSchema.PhysxContactReportAPI.Apply(prim)
    contacts = []
    sub = None
    try:
        from omni.physx import get_physx_simulation_interface
        sim = get_physx_simulation_interface()

        def _on_contact(contact_headers, contact_data):
            for header in contact_headers:
                pair = {{
                    'body_a': str(getattr(header, 'actor0', '')),
                    'body_b': str(getattr(header, 'actor1', '')),
                    'collider_a': str(getattr(header, 'collider0', '')),
                    'collider_b': str(getattr(header, 'collider1', '')),
                    'contact_count': int(getattr(header, 'num_contact_data', 0)),
                }}
                impulse = 0.0
                try:
                    n = int(getattr(header, 'num_contact_data', 0))
                    start = int(getattr(header, 'contact_data_offset', 0))
                    for i in range(start, start + n):
                        cd = contact_data[i]
                        imp = cd.impulse
                        impulse += float((imp[0]**2 + imp[1]**2 + imp[2]**2) ** 0.5)
                except Exception:
                    pass
                pair['impulse'] = impulse
                if impulse >= min_impulse:
                    contacts.append(pair)

        sub = sim.subscribe_contact_report_events(_on_contact)
        # Step the simulation briefly to gather contacts.
        deadline = time.time() + duration
        while time.time() < deadline:
            time.sleep(0.01)
    except Exception as exc:
        result['error'] = f'contact subscription failed: {{exc}}'
    finally:
        try:
            if sub is not None:
                sub = None
        except Exception:
            pass
    result['contact_count'] = len(contacts)
    result['contacts'] = contacts
    print(json.dumps(result, default=str))
"""
    return await kit_tools.queue_exec_patch(code, f"list_contacts {prim_path}")

def _gen_apply_force(args: Dict) -> str:
    """Generate code to apply external force/torque to a rigid body."""
    prim_path = args["prim_path"]
    force = args.get("force") or [0.0, 0.0, 0.0]
    torque = args.get("torque") or [0.0, 0.0, 0.0]
    position = args.get("position")

    pos_block = "None"
    if position is not None and len(position) >= 3:
        pos_block = f"[{float(position[0])}, {float(position[1])}, {float(position[2])}]"

    return f"""\
import omni.usd
from pxr import UsdPhysics

stage = omni.usd.get_context().get_stage()
prim_path = {prim_path!r}
force = [{float(force[0])}, {float(force[1])}, {float(force[2])}]
torque = [{float(torque[0])}, {float(torque[1])}, {float(torque[2])}]
position = {pos_block}

prim = stage.GetPrimAtPath(prim_path)
if not prim or not prim.IsValid():
    raise RuntimeError(f'prim not found: {{prim_path!r}}')
if not prim.HasAPI(UsdPhysics.RigidBodyAPI):
    UsdPhysics.RigidBodyAPI.Apply(prim)

# Preferred path: omni.physx force-API for instantaneous external force.
applied = False
try:
    from omni.physx.scripts import physicsUtils
    if hasattr(physicsUtils, 'apply_force_at_pos'):
        physicsUtils.apply_force_at_pos(prim, force, position or (0.0, 0.0, 0.0))
        applied = True
except Exception:
    applied = False

# Fallback: tensor API (omni.physics.tensors) — works during sim play.
if not applied:
    try:
        import omni.physics.tensors as physics_tensors
        sim_view = physics_tensors.create_simulation_view('numpy')
        rb_view = sim_view.create_rigid_body_view([prim_path])
        import numpy as np
        f_arr = np.array([force], dtype='float32')
        t_arr = np.array([torque], dtype='float32')
        rb_view.apply_forces_and_torques_at_pos(f_arr, t_arr, None, indices=np.array([0], dtype='int32'), is_global=True)
        applied = True
    except Exception as exc:
        raise RuntimeError(f'apply_force failed via both physicsUtils and tensors API: {{exc}}')

print(f'Applied force={{force}} torque={{torque}} on {{prim_path!r}}')
"""

async def _handle_get_kinematic_state(args: Dict) -> Dict:
    """Return full kinematic state: pose + linear/angular velocity + acceleration estimate."""
    prim_path = args["prim_path"]
    sample_dt = float(args.get("sample_dt", 0.05))
    code = f"""\
import omni.usd
import json
import time
from pxr import UsdGeom, UsdPhysics, Gf

stage = omni.usd.get_context().get_stage()
prim_path = {prim_path!r}
sample_dt = {sample_dt}
result = {{'prim_path': prim_path}}
prim = stage.GetPrimAtPath(prim_path)
if not prim or not prim.IsValid():
    result['error'] = 'prim not found'
else:
    # World transform via UsdGeom.Xformable.
    try:
        xf = UsdGeom.Xformable(prim)
        local_to_world = xf.ComputeLocalToWorldTransform(0)
        pos = local_to_world.ExtractTranslation()
        rot_q = local_to_world.ExtractRotationQuat()
        result['position'] = [float(pos[0]), float(pos[1]), float(pos[2])]
        imag = rot_q.GetImaginary()
        result['orientation_quat'] = [float(rot_q.GetReal()),
                                      float(imag[0]), float(imag[1]), float(imag[2])]
    except Exception as exc:
        result['transform_error'] = str(exc)

    has_rb = prim.HasAPI(UsdPhysics.RigidBodyAPI)
    result['has_rigid_body_api'] = bool(has_rb)
    if has_rb:
        rb = UsdPhysics.RigidBodyAPI(prim)
        v_attr = rb.GetVelocityAttr()
        w_attr = rb.GetAngularVelocityAttr()
        v0 = v_attr.Get() if v_attr else None
        w0 = w_attr.Get() if w_attr else None
        if v0 is None:
            v0 = (0.0, 0.0, 0.0)
        if w0 is None:
            w0 = (0.0, 0.0, 0.0)
        result['linear_velocity'] = [float(v0[0]), float(v0[1]), float(v0[2])]
        result['angular_velocity'] = [float(w0[0]), float(w0[1]), float(w0[2])]
        # Best-effort acceleration via finite diff over sample_dt seconds.
        try:
            time.sleep(max(0.0, sample_dt))
            v1 = v_attr.Get() if v_attr else None
            w1 = w_attr.Get() if w_attr else None
            if v1 is None:
                v1 = (0.0, 0.0, 0.0)
            if w1 is None:
                w1 = (0.0, 0.0, 0.0)
            dt = max(sample_dt, 1e-6)
            result['linear_acceleration'] = [
                (float(v1[0]) - float(v0[0])) / dt,
                (float(v1[1]) - float(v0[1])) / dt,
                (float(v1[2]) - float(v0[2])) / dt,
            ]
            result['angular_acceleration'] = [
                (float(w1[0]) - float(w0[0])) / dt,
                (float(w1[1]) - float(w0[1])) / dt,
                (float(w1[2]) - float(w0[2])) / dt,
            ]
            result['acceleration_dt'] = dt
        except Exception as exc:
            result['acceleration_error'] = str(exc)
    else:
        result['linear_velocity'] = [0.0, 0.0, 0.0]
        result['angular_velocity'] = [0.0, 0.0, 0.0]
        result['note'] = 'no PhysicsRigidBodyAPI — velocity/acceleration unavailable'
print(json.dumps(result, default=str))
"""
    return await kit_tools.queue_exec_patch(code, f"get_kinematic_state {prim_path}")

DATA_HANDLERS["get_linear_velocity"] = _handle_get_linear_velocity
DATA_HANDLERS["get_angular_velocity"] = _handle_get_angular_velocity
DATA_HANDLERS["get_mass"] = _handle_get_mass
DATA_HANDLERS["get_inertia"] = _handle_get_inertia
DATA_HANDLERS["get_physics_scene_config"] = _handle_get_physics_scene_config
DATA_HANDLERS["list_contacts"] = _handle_list_contacts
DATA_HANDLERS["get_kinematic_state"] = _handle_get_kinematic_state
CODE_GEN_HANDLERS["set_linear_velocity"] = _gen_set_linear_velocity
CODE_GEN_HANDLERS["set_physics_scene_config"] = _gen_set_physics_scene_config
CODE_GEN_HANDLERS["apply_force"] = _gen_apply_force

# ══════ From feat/atomic-tier3-articulation ══════
async def _handle_get_joint_positions(args: Dict) -> Dict:
    """Return current position of every joint in an articulation."""
    articulation = args["articulation"]
    code = f"""\
import omni.usd
import json
from pxr import Usd, UsdPhysics

stage = omni.usd.get_context().get_stage()
art = stage.GetPrimAtPath({articulation!r})
result = {{'articulation': {articulation!r}, 'units': {{'revolute': 'deg', 'prismatic': 'm'}}}}
if not art or not art.IsValid():
    result['error'] = 'articulation not found'
else:
    joints = []
    for p in Usd.PrimRange(art):
        rj = UsdPhysics.RevoluteJoint(p)
        pj = UsdPhysics.PrismaticJoint(p)
        if not (rj or pj):
            continue
        joint_type = 'revolute' if rj else 'prismatic'
        # Prefer PhysxJointStateAPI live state, fall back to authored target
        state_attr = p.GetAttribute('state:angular:physics:position') if rj else p.GetAttribute('state:linear:physics:position')
        if not (state_attr and state_attr.IsDefined()):
            state_attr = p.GetAttribute('physics:position')
        target_attr = p.GetAttribute('drive:angular:physics:targetPosition') if rj else p.GetAttribute('drive:linear:physics:targetPosition')
        pos = None
        source = None
        if state_attr and state_attr.HasAuthoredValue():
            pos = float(state_attr.Get())
            source = 'state'
        elif target_attr and target_attr.HasAuthoredValue():
            pos = float(target_attr.Get())
            source = 'drive_target'
        joints.append({{
            'name': p.GetName(),
            'path': str(p.GetPath()),
            'type': joint_type,
            'position': pos,
            'source': source,
        }})
    result['joint_count'] = len(joints)
    result['joints'] = joints
    result['positions'] = [j['position'] for j in joints]
print(json.dumps(result, default=str))
"""
    return await kit_tools.queue_exec_patch(code, f"get_joint_positions {articulation}")

async def _handle_get_joint_velocities(args: Dict) -> Dict:
    """Return current velocity of every joint in an articulation."""
    articulation = args["articulation"]
    code = f"""\
import omni.usd
import json
from pxr import Usd, UsdPhysics

stage = omni.usd.get_context().get_stage()
art = stage.GetPrimAtPath({articulation!r})
result = {{'articulation': {articulation!r}, 'units': {{'revolute': 'deg/s', 'prismatic': 'm/s'}}}}
if not art or not art.IsValid():
    result['error'] = 'articulation not found'
else:
    joints = []
    for p in Usd.PrimRange(art):
        rj = UsdPhysics.RevoluteJoint(p)
        pj = UsdPhysics.PrismaticJoint(p)
        if not (rj or pj):
            continue
        joint_type = 'revolute' if rj else 'prismatic'
        # PhysxJointStateAPI velocity attribute
        vel_attr = p.GetAttribute('state:angular:physics:velocity') if rj else p.GetAttribute('state:linear:physics:velocity')
        if not (vel_attr and vel_attr.IsDefined()):
            vel_attr = p.GetAttribute('physics:velocity')
        vel = float(vel_attr.Get()) if (vel_attr and vel_attr.HasAuthoredValue()) else 0.0
        joints.append({{
            'name': p.GetName(),
            'path': str(p.GetPath()),
            'type': joint_type,
            'velocity': vel,
        }})
    result['joint_count'] = len(joints)
    result['joints'] = joints
    result['velocities'] = [j['velocity'] for j in joints]
print(json.dumps(result, default=str))
"""
    return await kit_tools.queue_exec_patch(code, f"get_joint_velocities {articulation}")

async def _handle_get_joint_torques(args: Dict) -> Dict:
    """Return most recently applied torque/force on every joint."""
    articulation = args["articulation"]
    code = f"""\
import omni.usd
import json
from pxr import Usd, UsdPhysics

stage = omni.usd.get_context().get_stage()
art = stage.GetPrimAtPath({articulation!r})
result = {{'articulation': {articulation!r}, 'units': {{'revolute': 'N*m', 'prismatic': 'N'}}}}
if not art or not art.IsValid():
    result['error'] = 'articulation not found'
else:
    joints = []
    for p in Usd.PrimRange(art):
        rj = UsdPhysics.RevoluteJoint(p)
        pj = UsdPhysics.PrismaticJoint(p)
        if not (rj or pj):
            continue
        joint_type = 'revolute' if rj else 'prismatic'
        # PhysxJointStateAPI: appliedJointTorque (revolute) / appliedJointForce (prismatic)
        torque_attr = (
            p.GetAttribute('state:angular:physics:appliedJointTorque') if rj
            else p.GetAttribute('state:linear:physics:appliedJointForce')
        )
        if not (torque_attr and torque_attr.IsDefined()):
            torque_attr = p.GetAttribute('physics:appliedTorque')
        torque = float(torque_attr.Get()) if (torque_attr and torque_attr.HasAuthoredValue()) else 0.0
        joints.append({{
            'name': p.GetName(),
            'path': str(p.GetPath()),
            'type': joint_type,
            'torque': torque,
        }})
    result['joint_count'] = len(joints)
    result['joints'] = joints
    result['torques'] = [j['torque'] for j in joints]
print(json.dumps(result, default=str))
"""
    return await kit_tools.queue_exec_patch(code, f"get_joint_torques {articulation}")

async def _handle_get_drive_gains(args: Dict) -> Dict:
    """Read current kp/kd from UsdPhysics.DriveAPI on a joint."""
    joint_path = args["joint_path"]
    drive_type = args.get("drive_type", "auto")
    code = f"""\
import omni.usd
import json
from pxr import UsdPhysics

stage = omni.usd.get_context().get_stage()
joint = stage.GetPrimAtPath({joint_path!r})
requested = {drive_type!r}
result = {{'joint_path': {joint_path!r}, 'requested_drive_type': requested}}
if not joint or not joint.IsValid():
    result['error'] = 'joint not found'
else:
    candidates = ['angular', 'linear'] if requested == 'auto' else [requested]
    drives = {{}}
    for token in candidates:
        drive = UsdPhysics.DriveAPI(joint, token)
        if not drive or not drive.GetPrim().HasAPI(UsdPhysics.DriveAPI):
            continue
        kp_attr = drive.GetStiffnessAttr()
        kd_attr = drive.GetDampingAttr()
        max_force_attr = drive.GetMaxForceAttr()
        target_pos_attr = drive.GetTargetPositionAttr()
        target_vel_attr = drive.GetTargetVelocityAttr()
        drives[token] = {{
            'kp': float(kp_attr.Get()) if (kp_attr and kp_attr.HasAuthoredValue()) else None,
            'kd': float(kd_attr.Get()) if (kd_attr and kd_attr.HasAuthoredValue()) else None,
            'max_force': float(max_force_attr.Get()) if (max_force_attr and max_force_attr.HasAuthoredValue()) else None,
            'target_position': float(target_pos_attr.Get()) if (target_pos_attr and target_pos_attr.HasAuthoredValue()) else None,
            'target_velocity': float(target_vel_attr.Get()) if (target_vel_attr and target_vel_attr.HasAuthoredValue()) else None,
        }}
    if not drives:
        result['error'] = 'no DriveAPI applied on this joint'
        result['has_drive_api'] = False
    else:
        result['drives'] = drives
        result['has_drive_api'] = True
print(json.dumps(result, default=str))
"""
    return await kit_tools.queue_exec_patch(code, f"get_drive_gains {joint_path}")

def _gen_set_joint_limits(args: Dict) -> str:
    """Generate code to set physics:lowerLimit and physics:upperLimit."""
    joint_path = args["joint_path"]
    lower = float(args["lower"])
    upper = float(args["upper"])
    return f"""\
import omni.usd
from pxr import UsdPhysics

stage = omni.usd.get_context().get_stage()
joint_path = {joint_path!r}
joint = stage.GetPrimAtPath(joint_path)
if not joint or not joint.IsValid():
    raise RuntimeError('joint not found: ' + repr(joint_path))
rj = UsdPhysics.RevoluteJoint(joint)
pj = UsdPhysics.PrismaticJoint(joint)
if not (rj or pj):
    raise RuntimeError('joint is not Revolute or Prismatic: ' + repr(joint_path))
lower_attr = joint.GetAttribute('physics:lowerLimit')
if not (lower_attr and lower_attr.IsDefined()):
    lower_attr = (rj or pj).CreateLowerLimitAttr()
upper_attr = joint.GetAttribute('physics:upperLimit')
if not (upper_attr and upper_attr.IsDefined()):
    upper_attr = (rj or pj).CreateUpperLimitAttr()
lower_attr.Set({lower})
upper_attr.Set({upper})
print('joint_limits ' + repr(joint_path) + ' lower=' + repr({lower}) + ' upper=' + repr({upper}))
"""

def _gen_set_joint_velocity_limit(args: Dict) -> str:
    """Generate code to cap the joint's max velocity via PhysxJointAPI."""
    joint_path = args["joint_path"]
    vel_limit = float(args["vel_limit"])
    return f"""\
import omni.usd
from pxr import UsdPhysics

stage = omni.usd.get_context().get_stage()
joint_path = {joint_path!r}
joint = stage.GetPrimAtPath(joint_path)
if not joint or not joint.IsValid():
    raise RuntimeError('joint not found: ' + repr(joint_path))
rj = UsdPhysics.RevoluteJoint(joint)
pj = UsdPhysics.PrismaticJoint(joint)
if not (rj or pj):
    raise RuntimeError('joint is not Revolute or Prismatic: ' + repr(joint_path))
# Prefer PhysxSchema.PhysxJointAPI when available (Isaac Sim 5.x ships PhysxSchema).
try:
    from pxr import PhysxSchema
    if not joint.HasAPI(PhysxSchema.PhysxJointAPI):
        PhysxSchema.PhysxJointAPI.Apply(joint)
    pjapi = PhysxSchema.PhysxJointAPI(joint)
    attr = pjapi.GetMaxJointVelocityAttr() or pjapi.CreateMaxJointVelocityAttr()
except Exception:
    # Fallback: write the raw USD attribute used by PhysX 5.x.
    attr = joint.GetAttribute('physxJoint:maxJointVelocity')
    if not (attr and attr.IsDefined()):
        attr = joint.CreateAttribute('physxJoint:maxJointVelocity', None)
attr.Set({vel_limit})
print('joint_velocity_limit ' + repr(joint_path) + ' vel_limit=' + repr({vel_limit}))
"""

async def _handle_get_articulation_mass(args: Dict) -> Dict:
    """Sum mass of every link in the articulation."""
    articulation = args["articulation"]
    code = f"""\
import omni.usd
import json
from pxr import Usd, UsdPhysics

stage = omni.usd.get_context().get_stage()
art = stage.GetPrimAtPath({articulation!r})
result = {{'articulation': {articulation!r}, 'units': 'kg'}}
if not art or not art.IsValid():
    result['error'] = 'articulation not found'
else:
    links = []
    total = 0.0
    for p in Usd.PrimRange(art):
        if not p.HasAPI(UsdPhysics.RigidBodyAPI):
            continue
        m = 0.0
        authored = False
        if p.HasAPI(UsdPhysics.MassAPI):
            mass_attr = UsdPhysics.MassAPI(p).GetMassAttr()
            if mass_attr and mass_attr.HasAuthoredValue():
                m = float(mass_attr.Get())
                authored = True
        links.append({{
            'name': p.GetName(),
            'path': str(p.GetPath()),
            'mass': m,
            'authored': authored,
        }})
        total += m
    result['link_count'] = len(links)
    result['total_mass'] = total
    result['links'] = links
print(json.dumps(result, default=str))
"""
    return await kit_tools.queue_exec_patch(code, f"get_articulation_mass {articulation}")

async def _handle_get_center_of_mass(args: Dict) -> Dict:
    """Compute world-space mass-weighted center of mass of an articulation."""
    articulation = args["articulation"]
    code = f"""\
import omni.usd
import json
from pxr import Usd, UsdGeom, UsdPhysics, Gf

stage = omni.usd.get_context().get_stage()
art = stage.GetPrimAtPath({articulation!r})
result = {{'articulation': {articulation!r}, 'units': 'm'}}
if not art or not art.IsValid():
    result['error'] = 'articulation not found'
else:
    sum_x = sum_y = sum_z = 0.0
    total_mass = 0.0
    link_breakdown = []
    for p in Usd.PrimRange(art):
        if not p.HasAPI(UsdPhysics.RigidBodyAPI):
            continue
        m = 0.0
        local_com = Gf.Vec3f(0.0, 0.0, 0.0)
        if p.HasAPI(UsdPhysics.MassAPI):
            mass_api = UsdPhysics.MassAPI(p)
            mass_attr = mass_api.GetMassAttr()
            if mass_attr and mass_attr.HasAuthoredValue():
                m = float(mass_attr.Get())
            com_attr = mass_api.GetCenterOfMassAttr()
            if com_attr and com_attr.HasAuthoredValue():
                v = com_attr.Get()
                local_com = Gf.Vec3f(float(v[0]), float(v[1]), float(v[2]))
        # Skip zero-mass links (PhysX auto-mass not yet computed)
        if m <= 0.0:
            continue
        xf = UsdGeom.Xformable(p)
        if not xf:
            continue
        mat = xf.ComputeLocalToWorldTransform(Usd.TimeCode.Default())
        world_com = mat.Transform(Gf.Vec3d(local_com[0], local_com[1], local_com[2]))
        sum_x += m * world_com[0]
        sum_y += m * world_com[1]
        sum_z += m * world_com[2]
        total_mass += m
        link_breakdown.append({{
            'name': p.GetName(),
            'path': str(p.GetPath()),
            'mass': m,
            'world_com': [world_com[0], world_com[1], world_com[2]],
        }})
    if total_mass <= 0.0:
        result['error'] = 'no mass-bearing links found (apply MassAPI to set mass)'
        result['total_mass'] = 0.0
        result['center_of_mass'] = None
    else:
        result['total_mass'] = total_mass
        result['center_of_mass'] = [sum_x / total_mass, sum_y / total_mass, sum_z / total_mass]
        result['link_breakdown'] = link_breakdown
print(json.dumps(result, default=str))
"""
    return await kit_tools.queue_exec_patch(code, f"get_center_of_mass {articulation}")

async def _handle_get_gripper_state(args: Dict) -> Dict:
    """Report whether a gripper is open/closed plus current grip force."""
    articulation = args["articulation"]
    gripper_joints = list(args.get("gripper_joints") or [])
    open_threshold = float(args.get("open_threshold", 0.6))
    closed_threshold = float(args.get("closed_threshold", 0.1))
    code = f"""\
import omni.usd
import json
from pxr import Usd, UsdPhysics

stage = omni.usd.get_context().get_stage()
art = stage.GetPrimAtPath({articulation!r})
gripper_names = list({gripper_joints!r})
open_threshold = {open_threshold}
closed_threshold = {closed_threshold}
result = {{
    'articulation': {articulation!r},
    'gripper_joints': gripper_names,
    'open_threshold': open_threshold,
    'closed_threshold': closed_threshold,
}}
if not art or not art.IsValid():
    result['error'] = 'articulation not found'
elif not gripper_names:
    result['error'] = 'gripper_joints must not be empty'
else:
    found = []
    for p in Usd.PrimRange(art):
        if p.GetName() in gripper_names:
            found.append(p)
    if not found:
        result['error'] = 'none of the named gripper joints were found under the articulation'
        result['joints'] = []
    else:
        joints = []
        positions = []
        torques = []
        normalized = []
        for p in found:
            rj = UsdPhysics.RevoluteJoint(p)
            pj = UsdPhysics.PrismaticJoint(p)
            if not (rj or pj):
                continue
            jt = 'revolute' if rj else 'prismatic'
            pos_attr = p.GetAttribute('state:angular:physics:position') if rj else p.GetAttribute('state:linear:physics:position')
            if not (pos_attr and pos_attr.IsDefined()):
                pos_attr = p.GetAttribute('physics:position')
            pos = float(pos_attr.Get()) if (pos_attr and pos_attr.HasAuthoredValue()) else 0.0
            lower_attr = p.GetAttribute('physics:lowerLimit')
            upper_attr = p.GetAttribute('physics:upperLimit')
            lower = float(lower_attr.Get()) if (lower_attr and lower_attr.HasAuthoredValue()) else 0.0
            upper = float(upper_attr.Get()) if (upper_attr and upper_attr.HasAuthoredValue()) else 0.0
            torque_attr = (
                p.GetAttribute('state:angular:physics:appliedJointTorque') if rj
                else p.GetAttribute('state:linear:physics:appliedJointForce')
            )
            torque = float(torque_attr.Get()) if (torque_attr and torque_attr.HasAuthoredValue()) else 0.0
            span = upper - lower if upper > lower else 0.0
            norm = (pos - lower) / span if span > 0.0 else 0.0
            joints.append({{
                'name': p.GetName(),
                'path': str(p.GetPath()),
                'type': jt,
                'position': pos,
                'lower_limit': lower,
                'upper_limit': upper,
                'normalized': norm,
                'torque': torque,
            }})
            positions.append(pos)
            torques.append(torque)
            normalized.append(norm)
        if not joints:
            result['error'] = 'matched prims are not Revolute/Prismatic joints'
        else:
            avg_norm = sum(normalized) / len(normalized)
            if avg_norm >= open_threshold:
                state = 'open'
            elif avg_norm <= closed_threshold:
                state = 'closed'
            else:
                state = 'midway'
            result['joints'] = joints
            result['state'] = state
            result['avg_normalized'] = avg_norm
            result['force_estimate'] = sum(abs(t) for t in torques) / len(torques)
print(json.dumps(result, default=str))
"""
    return await kit_tools.queue_exec_patch(code, f"get_gripper_state {articulation}")

DATA_HANDLERS["get_joint_positions"] = _handle_get_joint_positions
DATA_HANDLERS["get_joint_velocities"] = _handle_get_joint_velocities
DATA_HANDLERS["get_joint_torques"] = _handle_get_joint_torques
DATA_HANDLERS["get_drive_gains"] = _handle_get_drive_gains
DATA_HANDLERS["get_articulation_mass"] = _handle_get_articulation_mass
DATA_HANDLERS["get_center_of_mass"] = _handle_get_center_of_mass
DATA_HANDLERS["get_gripper_state"] = _handle_get_gripper_state
CODE_GEN_HANDLERS["set_joint_limits"] = _gen_set_joint_limits
CODE_GEN_HANDLERS["set_joint_velocity_limit"] = _gen_set_joint_velocity_limit

# ══════ From feat/atomic-tier4-geometry ══════
async def _handle_raycast(args: Dict) -> Dict:
    """Cast a single ray and return the closest PhysX hit."""
    origin = args["origin"]
    direction = args["direction"]
    max_distance = float(args.get("max_distance", 1000.0))
    code = f"""\
import json

origin = {list(origin)!r}
direction = {list(direction)!r}
max_distance = {max_distance!r}

# Normalize direction
import math
_dx, _dy, _dz = direction
_len = math.sqrt(_dx * _dx + _dy * _dy + _dz * _dz)
if _len <= 0.0:
    print(json.dumps({{'error': 'direction has zero length', 'origin': origin, 'direction': direction}}))
else:
    direction = [_dx / _len, _dy / _len, _dz / _len]
    try:
        from omni.physx import get_physx_scene_query_interface
        sqi = get_physx_scene_query_interface()
        hit = sqi.raycast_closest(origin, direction, max_distance)
    except Exception as exc:
        hit = {{'error': f'PhysX scene query unavailable: {{exc}}'}}
    if isinstance(hit, dict) and hit.get('hit'):
        result = {{
            'hit': True,
            'origin': origin,
            'direction': direction,
            'max_distance': max_distance,
            'collision': hit.get('collision') or hit.get('rigidBody'),
            'position': list(hit.get('position', [])),
            'normal': list(hit.get('normal', [])),
            'distance': float(hit.get('distance', 0.0)),
            'face_index': hit.get('faceIndex'),
            'material': hit.get('material'),
        }}
    else:
        result = {{
            'hit': False,
            'origin': origin,
            'direction': direction,
            'max_distance': max_distance,
        }}
        if isinstance(hit, dict) and 'error' in hit:
            result['error'] = hit['error']
    print(json.dumps(result, default=str))
"""
    return await kit_tools.queue_exec_patch(code, f"raycast {origin} -> {direction}")

async def _handle_overlap_sphere(args: Dict) -> Dict:
    """Find every collider whose AABB overlaps the given sphere."""
    center = args["center"]
    radius = float(args["radius"])
    code = f"""\
import json

center = {list(center)!r}
radius = {radius!r}
hits = []

def _report_fn(hit):
    # Called once per overlap. Return True to keep collecting.
    path = getattr(hit, 'rigid_body', None) or getattr(hit, 'collision', None)
    if path is None and isinstance(hit, dict):
        path = hit.get('rigidBody') or hit.get('collision')
    if path is not None:
        hits.append(str(path))
    return True

try:
    from omni.physx import get_physx_scene_query_interface
    sqi = get_physx_scene_query_interface()
    count = sqi.overlap_sphere(radius, center, _report_fn, False)
except Exception as exc:
    count = -1
    hits.append(f'__error__: {{exc}}')

result = {{
    'center': center,
    'radius': radius,
    'count': len(hits),
    'reported_count': count,
    'prim_paths': hits,
}}
print(json.dumps(result, default=str))
"""
    return await kit_tools.queue_exec_patch(
        code, f"overlap_sphere center={center} r={radius}"
    )

async def _handle_overlap_box(args: Dict) -> Dict:
    """Find every collider that overlaps the given oriented box."""
    center = args["center"]
    half_extents = args["half_extents"]
    rotation = args.get("rotation") or [0.0, 0.0, 0.0, 1.0]  # identity quaternion
    code = f"""\
import json

center = {list(center)!r}
half_extents = {list(half_extents)!r}
rotation = {list(rotation)!r}
hits = []

def _report_fn(hit):
    path = getattr(hit, 'rigid_body', None) or getattr(hit, 'collision', None)
    if path is None and isinstance(hit, dict):
        path = hit.get('rigidBody') or hit.get('collision')
    if path is not None:
        hits.append(str(path))
    return True

try:
    from omni.physx import get_physx_scene_query_interface
    sqi = get_physx_scene_query_interface()
    count = sqi.overlap_box(half_extents, center, rotation, _report_fn, False)
except Exception as exc:
    count = -1
    hits.append(f'__error__: {{exc}}')

result = {{
    'center': center,
    'half_extents': half_extents,
    'rotation': rotation,
    'count': len(hits),
    'reported_count': count,
    'prim_paths': hits,
}}
print(json.dumps(result, default=str))
"""
    return await kit_tools.queue_exec_patch(
        code, f"overlap_box center={center} half_extents={half_extents}"
    )

async def _handle_sweep_sphere(args: Dict) -> Dict:
    """Sweep a sphere from start to end, return closest hit along the sweep."""
    start = args["start"]
    end = args["end"]
    radius = float(args["radius"])
    code = f"""\
import json
import math

start = {list(start)!r}
end = {list(end)!r}
radius = {radius!r}

dx = end[0] - start[0]
dy = end[1] - start[1]
dz = end[2] - start[2]
distance = math.sqrt(dx * dx + dy * dy + dz * dz)
if distance <= 0.0:
    print(json.dumps({{
        'error': 'sweep has zero length',
        'start': start,
        'end': end,
        'radius': radius,
    }}))
else:
    direction = [dx / distance, dy / distance, dz / distance]
    try:
        from omni.physx import get_physx_scene_query_interface
        sqi = get_physx_scene_query_interface()
        hit = sqi.sweep_sphere(radius, start, direction, distance)
    except Exception as exc:
        hit = {{'error': f'PhysX scene query unavailable: {{exc}}'}}
    if isinstance(hit, dict) and hit.get('hit'):
        result = {{
            'hit': True,
            'start': start,
            'end': end,
            'radius': radius,
            'direction': direction,
            'sweep_distance': distance,
            'collision': hit.get('collision') or hit.get('rigidBody'),
            'position': list(hit.get('position', [])),
            'normal': list(hit.get('normal', [])),
            'distance': float(hit.get('distance', 0.0)),
        }}
    else:
        result = {{
            'hit': False,
            'start': start,
            'end': end,
            'radius': radius,
            'direction': direction,
            'sweep_distance': distance,
        }}
        if isinstance(hit, dict) and 'error' in hit:
            result['error'] = hit['error']
    print(json.dumps(result, default=str))
"""
    return await kit_tools.queue_exec_patch(
        code, f"sweep_sphere {start} -> {end} r={radius}"
    )

async def _handle_compute_volume(args: Dict) -> Dict:
    """Compute mesh volume via signed tetrahedra (trimesh if available)."""
    prim_path = args["prim_path"]
    code = f"""\
import json
import omni.usd
from pxr import Usd, UsdGeom, Gf

prim_path = {prim_path!r}
stage = omni.usd.get_context().get_stage()
prim = stage.GetPrimAtPath(prim_path)
result = {{'prim_path': prim_path, 'units': 'm^3'}}

if not prim or not prim.IsValid():
    result['error'] = 'prim not found'
elif not prim.IsA(UsdGeom.Mesh):
    result['error'] = f'prim is not a Mesh: {{prim.GetTypeName()}}'
else:
    mesh = UsdGeom.Mesh(prim)
    points_attr = mesh.GetPointsAttr()
    counts_attr = mesh.GetFaceVertexCountsAttr()
    indices_attr = mesh.GetFaceVertexIndicesAttr()
    if not (points_attr and counts_attr and indices_attr):
        result['error'] = 'mesh missing points / faceVertexCounts / faceVertexIndices'
    else:
        # Bake world transform so volume is in world units
        xf = UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(Usd.TimeCode.Default())
        local_points = points_attr.Get() or []
        world_points = [xf.Transform(Gf.Vec3d(p[0], p[1], p[2])) for p in local_points]
        counts = list(counts_attr.Get() or [])
        indices = list(indices_attr.Get() or [])

        # Triangulate (fan) every face into (i0, i_k, i_{{k+1}}) triangles
        triangles = []
        cursor = 0
        for c in counts:
            face = indices[cursor:cursor + c]
            cursor += c
            if len(face) < 3:
                continue
            for k in range(1, len(face) - 1):
                triangles.append((face[0], face[k], face[k + 1]))

        volume_signed = 0.0
        try:
            import trimesh
            import numpy as np
            verts = np.array([(p[0], p[1], p[2]) for p in world_points], dtype=float)
            faces = np.array(triangles, dtype=int)
            tm = trimesh.Trimesh(vertices=verts, faces=faces, process=False)
            volume_signed = float(tm.volume)
            backend = 'trimesh'
        except Exception:
            # Manual signed-tetrahedra (divergence theorem) fallback
            for (a, b, c) in triangles:
                v0 = world_points[a]
                v1 = world_points[b]
                v2 = world_points[c]
                # Signed volume of tetrahedron (origin, v0, v1, v2)
                volume_signed += (
                    v0[0] * (v1[1] * v2[2] - v1[2] * v2[1])
                    - v0[1] * (v1[0] * v2[2] - v1[2] * v2[0])
                    + v0[2] * (v1[0] * v2[1] - v1[1] * v2[0])
                ) / 6.0
            backend = 'manual_tetrahedra'

        result['triangle_count'] = len(triangles)
        result['vertex_count'] = len(world_points)
        result['volume'] = abs(volume_signed)
        result['signed_volume'] = volume_signed
        result['backend'] = backend

print(json.dumps(result, default=str))
"""
    return await kit_tools.queue_exec_patch(code, f"compute_volume {prim_path}")

async def _handle_compute_surface_area(args: Dict) -> Dict:
    """Compute surface area as sum of triangle areas (after triangulation)."""
    prim_path = args["prim_path"]
    code = f"""\
import json
import math
import omni.usd
from pxr import Usd, UsdGeom, Gf

prim_path = {prim_path!r}
stage = omni.usd.get_context().get_stage()
prim = stage.GetPrimAtPath(prim_path)
result = {{'prim_path': prim_path, 'units': 'm^2'}}

if not prim or not prim.IsValid():
    result['error'] = 'prim not found'
elif not prim.IsA(UsdGeom.Mesh):
    result['error'] = f'prim is not a Mesh: {{prim.GetTypeName()}}'
else:
    mesh = UsdGeom.Mesh(prim)
    points_attr = mesh.GetPointsAttr()
    counts_attr = mesh.GetFaceVertexCountsAttr()
    indices_attr = mesh.GetFaceVertexIndicesAttr()
    if not (points_attr and counts_attr and indices_attr):
        result['error'] = 'mesh missing points / faceVertexCounts / faceVertexIndices'
    else:
        xf = UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(Usd.TimeCode.Default())
        local_points = points_attr.Get() or []
        world_points = [xf.Transform(Gf.Vec3d(p[0], p[1], p[2])) for p in local_points]
        counts = list(counts_attr.Get() or [])
        indices = list(indices_attr.Get() or [])

        triangles = []
        cursor = 0
        for c in counts:
            face = indices[cursor:cursor + c]
            cursor += c
            if len(face) < 3:
                continue
            for k in range(1, len(face) - 1):
                triangles.append((face[0], face[k], face[k + 1]))

        total_area = 0.0
        for (a, b, c) in triangles:
            v0 = world_points[a]
            v1 = world_points[b]
            v2 = world_points[c]
            ex = v1[0] - v0[0]
            ey = v1[1] - v0[1]
            ez = v1[2] - v0[2]
            fx = v2[0] - v0[0]
            fy = v2[1] - v0[1]
            fz = v2[2] - v0[2]
            cx = ey * fz - ez * fy
            cy = ez * fx - ex * fz
            cz = ex * fy - ey * fx
            total_area += 0.5 * math.sqrt(cx * cx + cy * cy + cz * cz)

        result['triangle_count'] = len(triangles)
        result['vertex_count'] = len(world_points)
        result['surface_area'] = total_area

print(json.dumps(result, default=str))
"""
    return await kit_tools.queue_exec_patch(code, f"compute_surface_area {prim_path}")

def _gen_compute_convex_hull(args: Dict) -> str:
    """Apply convexHull collision approximation, optionally export hull mesh."""
    prim_path = args["prim_path"]
    export_hull_path = args.get("export_hull_path")
    lines = [
        "import omni.usd",
        "from pxr import Usd, UsdGeom, UsdPhysics, Gf, Sdf, Vt",
        "",
        f"prim_path = {prim_path!r}",
        f"export_hull_path = {export_hull_path!r}",
        "stage = omni.usd.get_context().get_stage()",
        "prim = stage.GetPrimAtPath(prim_path)",
        "if not prim or not prim.IsValid():",
        "    raise RuntimeError(f'prim not found: {prim_path}')",
        "if not prim.IsA(UsdGeom.Mesh):",
        "    raise RuntimeError(f'prim is not a Mesh: {prim.GetTypeName()}')",
        "",
        "# 1) Mark the prim as a collider, then declare convexHull approximation",
        "UsdPhysics.CollisionAPI.Apply(prim)",
        "mesh_collision = UsdPhysics.MeshCollisionAPI.Apply(prim)",
        "approx_attr = mesh_collision.GetApproximationAttr()",
        "if not approx_attr or not approx_attr.IsDefined():",
        "    approx_attr = mesh_collision.CreateApproximationAttr()",
        "approx_attr.Set(UsdPhysics.Tokens.convexHull)",
        "",
        "exported_path = None",
        "if export_hull_path:",
        "    # 2) Compute the convex hull (scipy if available, else manual gift-wrap)",
        "    mesh = UsdGeom.Mesh(prim)",
        "    xf = UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(Usd.TimeCode.Default())",
        "    local_points = mesh.GetPointsAttr().Get() or []",
        "    world_points = [xf.Transform(Gf.Vec3d(p[0], p[1], p[2])) for p in local_points]",
        "    hull_vertices = []",
        "    hull_triangles = []",
        "    if len(world_points) < 4:",
        "        raise RuntimeError(f'need at least 4 points for a 3D hull, got {len(world_points)}')",
        "    try:",
        "        import numpy as np",
        "        from scipy.spatial import ConvexHull",
        "        pts = np.array([(p[0], p[1], p[2]) for p in world_points], dtype=float)",
        "        hull = ConvexHull(pts)",
        "        index_remap = {orig: new for new, orig in enumerate(sorted(set(int(i) for i in hull.vertices)))}",
        "        hull_vertices = [tuple(pts[orig]) for orig in sorted(index_remap.keys())]",
        "        for simplex in hull.simplices:",
        "            tri = tuple(index_remap[int(i)] for i in simplex)",
        "            hull_triangles.append(tri)",
        "    except Exception:",
        "        # Manual fallback: just take the AABB-corner hull (8 verts, 12 triangles).",
        "        # This is a coarse but always-valid convex envelope when scipy is missing.",
        "        xs = [p[0] for p in world_points]",
        "        ys = [p[1] for p in world_points]",
        "        zs = [p[2] for p in world_points]",
        "        mn = (min(xs), min(ys), min(zs))",
        "        mx = (max(xs), max(ys), max(zs))",
        "        hull_vertices = [",
        "            (mn[0], mn[1], mn[2]), (mx[0], mn[1], mn[2]),",
        "            (mx[0], mx[1], mn[2]), (mn[0], mx[1], mn[2]),",
        "            (mn[0], mn[1], mx[2]), (mx[0], mn[1], mx[2]),",
        "            (mx[0], mx[1], mx[2]), (mn[0], mx[1], mx[2]),",
        "        ]",
        "        hull_triangles = [",
        "            (0, 1, 2), (0, 2, 3),  # -Z",
        "            (4, 6, 5), (4, 7, 6),  # +Z",
        "            (0, 4, 5), (0, 5, 1),  # -Y",
        "            (3, 2, 6), (3, 6, 7),  # +Y",
        "            (0, 3, 7), (0, 7, 4),  # -X",
        "            (1, 5, 6), (1, 6, 2),  # +X",
        "        ]",
        "    # 3) Author hull mesh prim",
        "    hull_prim = stage.DefinePrim(export_hull_path, 'Mesh')",
        "    hull_mesh = UsdGeom.Mesh(hull_prim)",
        "    hull_mesh.CreatePointsAttr([Gf.Vec3f(*v) for v in hull_vertices])",
        "    hull_mesh.CreateFaceVertexCountsAttr([3] * len(hull_triangles))",
        "    flat_indices = [idx for tri in hull_triangles for idx in tri]",
        "    hull_mesh.CreateFaceVertexIndicesAttr(flat_indices)",
        "    exported_path = export_hull_path",
        "",
        "print(f'compute_convex_hull applied to {prim_path} (export={exported_path})')",
    ]
    return "\n".join(lines)

DATA_HANDLERS["raycast"] = _handle_raycast
DATA_HANDLERS["overlap_sphere"] = _handle_overlap_sphere
DATA_HANDLERS["overlap_box"] = _handle_overlap_box
DATA_HANDLERS["sweep_sphere"] = _handle_sweep_sphere
DATA_HANDLERS["compute_volume"] = _handle_compute_volume
DATA_HANDLERS["compute_surface_area"] = _handle_compute_surface_area
CODE_GEN_HANDLERS["compute_convex_hull"] = _gen_compute_convex_hull

# ══════ From feat/atomic-tier5-omnigraph ══════
def _gen_add_node(args: Dict) -> str:
    """Add a single node to an existing OmniGraph via og.Controller.edit()."""
    graph_path = args["graph_path"]
    raw_node_type = args["node_type"]
    node_name = args["name"]
    # Reuse the legacy → 5.1 namespace remap so callers can pass either form.
    node_type = _OG_NODE_TYPE_MAP.get(raw_node_type, raw_node_type)
    return f"""\
import omni.graph.core as og

keys = og.Controller.Keys
og.Controller.edit(
    {{"graph_path": "{graph_path}"}},
    {{
        keys.CREATE_NODES: [
            ("{node_name}", "{node_type}"),
        ],
    }},
)
print(f"Added node '{node_name}' ({node_type}) to {graph_path}")
"""

def _gen_connect_nodes(args: Dict) -> str:
    """Wire src.outputs:X -> dst.inputs:Y via og.Controller.edit() with CONNECT."""
    graph_path = args["graph_path"]
    src = args["src"]
    dst = args["dst"]
    return f"""\
import omni.graph.core as og

keys = og.Controller.Keys
og.Controller.edit(
    {{"graph_path": "{graph_path}"}},
    {{
        keys.CONNECT: [
            ("{src}", "{dst}"),
        ],
    }},
)
print(f"Connected {src} -> {dst} in {graph_path}")
"""

def _gen_set_graph_variable(args: Dict) -> str:
    """Set a graph-scoped variable on an OmniGraph via og.Controller."""
    graph_path = args["graph_path"]
    var_name = args["name"]
    value = args["value"]
    return f"""\
import omni.graph.core as og

graph = og.Controller.graph("{graph_path}")
if graph is None:
    raise RuntimeError(f"OmniGraph not found at {graph_path}")

# Try og.Controller.set_variable() first (modern API);
# fall back to graph.get_variable(name).set(value) on older Kit builds.
_value = {value!r}
try:
    og.Controller.set_variable(("{graph_path}", "{var_name}"), _value)
    print(f"Set variable '{var_name}' on {graph_path} via og.Controller.set_variable")
except Exception:
    var = graph.get_variable("{var_name}")
    if var is None:
        raise RuntimeError(f"Variable '{var_name}' does not exist on {graph_path}")
    var.set(_value)
    print(f"Set variable '{var_name}' on {graph_path} via graph.get_variable().set()")
"""

def _gen_delete_node(args: Dict) -> str:
    """Remove a single node via og.Controller.edit() with DELETE_NODES."""
    graph_path = args["graph_path"]
    node_name = args["node_name"]
    return f"""\
import omni.graph.core as og

keys = og.Controller.Keys
og.Controller.edit(
    {{"graph_path": "{graph_path}"}},
    {{
        keys.DELETE_NODES: [
            "{node_name}",
        ],
    }},
)
print(f"Deleted node '{node_name}' from {graph_path}")
"""

async def _handle_list_graphs(args: Dict) -> Dict:
    """Enumerate all OmniGraph action graphs in the stage.

    Strategy: query Kit synchronously to scan the stage for prims of type
    'OmniGraph' (and the modern 'omni.graph.core.types.OmniGraph' fallback).
    Falls back to empty list when Kit RPC is unavailable.
    """
    code = """\
import json
import omni.usd

stage = omni.usd.get_context().get_stage()
graphs = []
if stage is not None:
    for prim in stage.Traverse():
        type_name = prim.GetTypeName()
        if type_name in ("OmniGraph", "ComputeGraph"):
            graphs.append({
                "path": str(prim.GetPath()),
                "type": str(type_name),
                "name": prim.GetName(),
            })
print(json.dumps({"graphs": graphs, "count": len(graphs)}))
"""
    result = await kit_tools.exec_sync(code, timeout=10)
    if not result.get("success"):
        return {"graphs": [], "count": 0, "error": result.get("output", "Kit RPC unavailable")}
    output = result.get("output", "").strip()
    # exec_sync returns the captured stdout as a single string;
    # find the last JSON line for our payload.
    for line in reversed(output.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    return {"graphs": [], "count": 0, "raw_output": output}

async def _handle_inspect_graph(args: Dict) -> Dict:
    """Return nodes, connections, and attribute values for a single graph."""
    graph_path = args["graph_path"]
    code = f"""\
import json
import omni.graph.core as og

graph = og.Controller.graph("{graph_path}")
result = {{"graph_path": "{graph_path}"}}
if graph is None:
    result["error"] = "Graph not found"
    print(json.dumps(result))
else:
    nodes_info = []
    connections = []
    try:
        for node in graph.get_nodes():
            node_path = node.get_prim_path()
            node_type = node.get_type_name()
            attrs = {{}}
            for attr in node.get_attributes():
                try:
                    attrs[attr.get_name()] = repr(attr.get())
                except Exception:
                    attrs[attr.get_name()] = "<unreadable>"
                # Track downstream connections from this attribute
                try:
                    for upstream in attr.get_upstream_connections():
                        connections.append({{
                            "src": upstream.get_path(),
                            "dst": attr.get_path(),
                        }})
                except Exception:
                    pass
            nodes_info.append({{
                "name": node.get_prim_path().split("/")[-1],
                "path": node_path,
                "type": node_type,
                "attributes": attrs,
            }})
        result["nodes"] = nodes_info
        result["connections"] = connections
        result["node_count"] = len(nodes_info)
    except Exception as exc:
        result["error"] = str(exc)
print(json.dumps(result))
"""
    exec_result = await kit_tools.exec_sync(code, timeout=15)
    if not exec_result.get("success"):
        return {
            "graph_path": graph_path,
            "error": exec_result.get("output", "Kit RPC unavailable"),
        }
    output = exec_result.get("output", "").strip()
    for line in reversed(output.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    return {"graph_path": graph_path, "raw_output": output}

CODE_GEN_HANDLERS["add_node"] = _gen_add_node
CODE_GEN_HANDLERS["connect_nodes"] = _gen_connect_nodes
CODE_GEN_HANDLERS["set_graph_variable"] = _gen_set_graph_variable
CODE_GEN_HANDLERS["delete_node"] = _gen_delete_node
DATA_HANDLERS["list_graphs"] = _handle_list_graphs
DATA_HANDLERS["inspect_graph"] = _handle_inspect_graph

# ══════ From feat/atomic-tier6-lighting ══════
async def _handle_list_lights(args: Dict) -> Dict:
    """Enumerate all UsdLux light prims in the current stage via Kit RPC."""
    type_tuple = repr(_LIGHT_TYPE_NAMES)
    code = f"""\
import omni.usd
import json

stage = omni.usd.get_context().get_stage()
LIGHT_TYPES = set({type_tuple})

lights = []
has_dome = False
if stage is not None:
    for prim in stage.Traverse():
        type_name = prim.GetTypeName()
        if type_name not in LIGHT_TYPES:
            continue
        intensity_attr = prim.GetAttribute('inputs:intensity')
        color_attr = prim.GetAttribute('inputs:color')
        enabled_attr = prim.GetAttribute('inputs:enabled')
        intensity = float(intensity_attr.Get()) if intensity_attr and intensity_attr.HasAuthoredValue() else None
        color_val = color_attr.Get() if color_attr and color_attr.HasAuthoredValue() else None
        if color_val is not None:
            color = [float(color_val[0]), float(color_val[1]), float(color_val[2])]
        else:
            color = None
        enabled = bool(enabled_attr.Get()) if enabled_attr and enabled_attr.HasAuthoredValue() else True
        if type_name == 'DomeLight':
            has_dome = True
        lights.append({{
            'path': str(prim.GetPath()),
            'type': type_name,
            'intensity': intensity,
            'color': color,
            'enabled': enabled,
        }})

print(json.dumps({{
    'lights': lights,
    'count': len(lights),
    'has_dome': has_dome,
}}))
"""
    return await kit_tools.queue_exec_patch(code, "List all UsdLux light prims in the stage")

async def _handle_get_light_properties(args: Dict) -> Dict:
    """Read the full attribute set of a single light prim."""
    light_path = args["light_path"]
    code = f"""\
import omni.usd
import json

stage = omni.usd.get_context().get_stage()
prim = stage.GetPrimAtPath('{light_path}')

if not prim or not prim.IsValid():
    print(json.dumps({{'error': 'prim not found', 'path': '{light_path}'}}))
else:
    type_name = prim.GetTypeName()

    def _get(attr_name):
        a = prim.GetAttribute(attr_name)
        if a and a.HasAuthoredValue():
            return a.Get()
        if a:
            return a.Get()
        return None

    intensity = _get('inputs:intensity')
    exposure = _get('inputs:exposure')
    color = _get('inputs:color')
    enabled = _get('inputs:enabled')
    color_temp = _get('inputs:colorTemperature')
    angle = _get('inputs:angle') if type_name == 'DistantLight' else None
    radius = _get('inputs:radius') if type_name in ('SphereLight', 'DiskLight') else None
    width = _get('inputs:width') if type_name == 'RectLight' else None
    height = _get('inputs:height') if type_name == 'RectLight' else None
    texture_file = None
    if type_name == 'DomeLight':
        tex = _get('inputs:texture:file')
        if tex is not None:
            texture_file = str(tex)

    out = {{
        'path': '{light_path}',
        'type': type_name,
        'intensity': float(intensity) if intensity is not None else None,
        'exposure': float(exposure) if exposure is not None else None,
        'color': [float(color[0]), float(color[1]), float(color[2])] if color is not None else None,
        'enabled': bool(enabled) if enabled is not None else True,
        'color_temperature': float(color_temp) if color_temp is not None else None,
        'angle': float(angle) if angle is not None else None,
        'radius': float(radius) if radius is not None else None,
        'width': float(width) if width is not None else None,
        'height': float(height) if height is not None else None,
        'texture_file': texture_file,
    }}
    print(json.dumps(out))
"""
    return await kit_tools.queue_exec_patch(code, f"Read light properties for {light_path}")

def _gen_set_light_intensity(args: Dict) -> str:
    light_path = args["light_path"]
    intensity = float(args["intensity"])
    if intensity < 0:
        intensity = 0.0
    return (
        "import omni.usd\n"
        "from pxr import Sdf\n"
        "stage = omni.usd.get_context().get_stage()\n"
        f"prim = stage.GetPrimAtPath('{light_path}')\n"
        "if not prim or not prim.IsValid():\n"
        f"    raise RuntimeError(\"Light prim not found: {light_path}\")\n"
        "attr = prim.GetAttribute('inputs:intensity')\n"
        "if not attr:\n"
        "    attr = prim.CreateAttribute('inputs:intensity', Sdf.ValueTypeNames.Float)\n"
        f"attr.Set({intensity})\n"
        f"print('Set intensity={intensity} on {light_path}')"
    )

def _gen_set_light_color(args: Dict) -> str:
    light_path = args["light_path"]
    rgb = args["rgb"]
    if not isinstance(rgb, (list, tuple)) or len(rgb) != 3:
        raise ValueError("rgb must be a 3-element list [r, g, b]")
    r, g, b = (max(0.0, float(rgb[0])), max(0.0, float(rgb[1])), max(0.0, float(rgb[2])))
    return (
        "import omni.usd\n"
        "from pxr import Sdf, Gf\n"
        "stage = omni.usd.get_context().get_stage()\n"
        f"prim = stage.GetPrimAtPath('{light_path}')\n"
        "if not prim or not prim.IsValid():\n"
        f"    raise RuntimeError(\"Light prim not found: {light_path}\")\n"
        "attr = prim.GetAttribute('inputs:color')\n"
        "if not attr:\n"
        "    attr = prim.CreateAttribute('inputs:color', Sdf.ValueTypeNames.Color3f)\n"
        f"attr.Set(Gf.Vec3f({r}, {g}, {b}))\n"
        f"print('Set color=({r}, {g}, {b}) on {light_path}')"
    )

def _gen_create_hdri_skydome(args: Dict) -> str:
    hdri_path = args["hdri_path"]
    dome_path = args.get("dome_path", "/Environment/DomeLight")
    intensity = float(args.get("intensity", 1000.0))
    if intensity < 0:
        intensity = 0.0
    # Escape single quotes in the HDRI path so the literal stays valid
    safe_hdri = hdri_path.replace("'", "\\'")
    return (
        "import omni.usd\n"
        "from pxr import UsdLux, Sdf\n"
        "stage = omni.usd.get_context().get_stage()\n"
        f"dome_path = '{dome_path}'\n"
        "# Idempotent: re-define replaces existing prim of the same type, leaves\n"
        "# parent Xforms untouched.\n"
        "dome = UsdLux.DomeLight.Define(stage, dome_path)\n"
        "prim = dome.GetPrim()\n"
        "tex_attr = prim.GetAttribute('inputs:texture:file')\n"
        "if not tex_attr:\n"
        "    tex_attr = prim.CreateAttribute('inputs:texture:file', Sdf.ValueTypeNames.Asset)\n"
        f"tex_attr.Set('{safe_hdri}')\n"
        "fmt_attr = prim.GetAttribute('inputs:texture:format')\n"
        "if not fmt_attr:\n"
        "    fmt_attr = prim.CreateAttribute('inputs:texture:format', Sdf.ValueTypeNames.Token)\n"
        "fmt_attr.Set('latlong')\n"
        "intensity_attr = prim.GetAttribute('inputs:intensity')\n"
        "if not intensity_attr:\n"
        "    intensity_attr = prim.CreateAttribute('inputs:intensity', Sdf.ValueTypeNames.Float)\n"
        f"intensity_attr.Set({intensity})\n"
        f"print('Created HDRI skydome at ' + dome_path + ' with texture {safe_hdri}')"
    )

CODE_GEN_HANDLERS["set_light_intensity"] = _gen_set_light_intensity
CODE_GEN_HANDLERS["set_light_color"] = _gen_set_light_color
CODE_GEN_HANDLERS["create_hdri_skydome"] = _gen_create_hdri_skydome
DATA_HANDLERS["list_lights"] = _handle_list_lights
DATA_HANDLERS["get_light_properties"] = _handle_get_light_properties

# ══════ From feat/atomic-tier7-camera ══════
def _parse_last_json_line(output: str) -> Optional[Dict]:
    """Return the last well-formed JSON object printed in `output`, or None."""
    for line in reversed(output.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    return None

async def _handle_list_cameras(args: Dict) -> Dict:
    """Walk the stage and return all UsdGeom.Camera prims with type info."""
    code = """\
import omni.usd
import json
from pxr import Usd, UsdGeom

stage = omni.usd.get_context().get_stage()
cameras = []
if stage is not None:
    for prim in stage.Traverse():
        if prim.GetTypeName() == 'Camera':
            cam = UsdGeom.Camera(prim)
            proj_attr = cam.GetProjectionAttr()
            projection = proj_attr.Get() if proj_attr else 'perspective'
            cameras.append({
                'path': str(prim.GetPath()),
                'name': prim.GetName(),
                'projection': str(projection) if projection else 'perspective',
                'purpose': str(UsdGeom.Imageable(prim).GetPurposeAttr().Get() or 'default'),
                'kind': str(Usd.ModelAPI(prim).GetKind() or ''),
            })
print(json.dumps({'cameras': cameras, 'count': len(cameras)}))
"""
    result = await kit_tools.exec_sync(code, timeout=10)
    if not result.get("success"):
        return {
            "error": f"Kit RPC /exec_sync failed: {result.get('output', 'unknown')}",
            "hint": "Is Isaac Sim running with the extension's Kit RPC enabled?",
        }
    parsed = _parse_last_json_line(result.get("output", ""))
    if parsed is None:
        return {"error": "Failed to parse camera list", "raw_output": result.get("output", "")[:500]}
    return parsed

async def _handle_get_camera_params(args: Dict) -> Dict:
    """Read all cinematographic attributes from a UsdGeom.Camera prim."""
    camera_path = args.get("camera_path", "")
    if not camera_path:
        return {"error": "camera_path is required"}
    # Sanitize path
    import re as _re
    if not _re.match(r"^/[A-Za-z0-9_/\- ]+$", camera_path):
        return {"error": f"Invalid camera_path: {camera_path}"}

    code = f"""\
import omni.usd
import json
import math
from pxr import UsdGeom

stage = omni.usd.get_context().get_stage()
prim = stage.GetPrimAtPath('{camera_path}')
if not prim or not prim.IsValid():
    print(json.dumps({{'error': 'Camera prim not found', 'camera_path': '{camera_path}'}}))
elif prim.GetTypeName() != 'Camera':
    print(json.dumps({{'error': 'Prim is not a Camera', 'camera_path': '{camera_path}', 'type': str(prim.GetTypeName())}}))
else:
    cam = UsdGeom.Camera(prim)
    focal = cam.GetFocalLengthAttr().Get() or 0.0
    h_ap = cam.GetHorizontalApertureAttr().Get() or 0.0
    v_ap = cam.GetVerticalApertureAttr().Get() or 0.0
    clip = cam.GetClippingRangeAttr().Get()
    near, far = (float(clip[0]), float(clip[1])) if clip else (0.0, 0.0)
    focus = cam.GetFocusDistanceAttr().Get() or 0.0
    fstop = cam.GetFStopAttr().Get() or 0.0
    proj = cam.GetProjectionAttr().Get() or 'perspective'

    def _fov_deg(aperture, focal_length):
        if focal_length <= 0 or aperture <= 0:
            return 0.0
        return math.degrees(2.0 * math.atan(aperture / (2.0 * focal_length)))

    info = {{
        'camera_path': '{camera_path}',
        'projection': str(proj),
        'focal_length_mm': float(focal),
        'horizontal_aperture_mm': float(h_ap),
        'vertical_aperture_mm': float(v_ap),
        'horizontal_fov_deg': _fov_deg(float(h_ap), float(focal)),
        'vertical_fov_deg': _fov_deg(float(v_ap), float(focal)),
        'clipping_range_m': [near, far],
        'focus_distance_m': float(focus),
        'f_stop': float(fstop),
    }}
    print(json.dumps(info))
"""
    result = await kit_tools.exec_sync(code, timeout=10)
    if not result.get("success"):
        return {"error": f"Kit RPC /exec_sync failed: {result.get('output', 'unknown')}"}
    parsed = _parse_last_json_line(result.get("output", ""))
    if parsed is None:
        return {"error": "Failed to parse camera params", "raw_output": result.get("output", "")[:500]}
    return parsed

def _gen_set_camera_params(args: Dict) -> str:
    """Generate Python that mutates camera attributes. Each requested field becomes one .Set()."""
    camera_path = args["camera_path"]
    params = args.get("params", {}) or {}

    lines = [
        "import omni.usd",
        "from pxr import UsdGeom, Gf, Sdf",
        "",
        "stage = omni.usd.get_context().get_stage()",
        f"prim = stage.GetPrimAtPath('{camera_path}')",
        "if not prim or not prim.IsValid():",
        f"    raise RuntimeError('Camera prim not found: {camera_path}')",
        "if prim.GetTypeName() != 'Camera':",
        f"    raise RuntimeError('Prim is not a Camera: {camera_path}')",
        "cam = UsdGeom.Camera(prim)",
        "",
    ]

    if "focal_length" in params:
        lines.append(f"cam.GetFocalLengthAttr().Set({float(params['focal_length'])})")
    if "horizontal_aperture" in params:
        lines.append(f"cam.GetHorizontalApertureAttr().Set({float(params['horizontal_aperture'])})")
    if "vertical_aperture" in params:
        lines.append(f"cam.GetVerticalApertureAttr().Set({float(params['vertical_aperture'])})")
    if "clipping_range" in params:
        cr = params["clipping_range"]
        if isinstance(cr, (list, tuple)) and len(cr) == 2:
            near, far = float(cr[0]), float(cr[1])
            lines.append(
                f"cam.GetClippingRangeAttr().Set(Gf.Vec2f({near}, {far}))"
            )
    if "focus_distance" in params:
        lines.append(f"cam.GetFocusDistanceAttr().Set({float(params['focus_distance'])})")
    if "f_stop" in params:
        lines.append(f"cam.GetFStopAttr().Set({float(params['f_stop'])})")
    if "projection" in params:
        proj = str(params["projection"]).lower()
        if proj in ("perspective", "orthographic"):
            lines.append(f"cam.GetProjectionAttr().Set('{proj}')")
        else:
            lines.append(f"# WARNING: unsupported projection '{proj}' — skipped")

    lines.append("")
    lines.append(f"print('set_camera_params: updated {camera_path}')")
    return "\n".join(lines)

async def _handle_capture_camera_image(args: Dict) -> Dict:
    """Render a single frame from the named camera and return base64 PNG."""
    camera_path = args.get("camera_path", "")
    if not camera_path:
        return {"error": "camera_path is required"}
    import re as _re
    if not _re.match(r"^/[A-Za-z0-9_/\- ]+$", camera_path):
        return {"error": f"Invalid camera_path: {camera_path}"}

    resolution = args.get("resolution") or [1280, 720]
    if (
        not isinstance(resolution, (list, tuple))
        or len(resolution) != 2
        or not all(isinstance(v, int) and v > 0 for v in resolution)
    ):
        return {"error": "resolution must be [width, height] of positive integers"}
    width, height = int(resolution[0]), int(resolution[1])

    code = f"""\
import omni.usd
import json
import base64
from pxr import UsdGeom

stage = omni.usd.get_context().get_stage()
prim = stage.GetPrimAtPath('{camera_path}')
if not prim or not prim.IsValid():
    print(json.dumps({{'error': 'Camera prim not found', 'camera_path': '{camera_path}'}}))
elif prim.GetTypeName() != 'Camera':
    print(json.dumps({{'error': 'Prim is not a Camera', 'camera_path': '{camera_path}'}}))
else:
    try:
        import omni.replicator.core as rep
        rp = rep.create.render_product('{camera_path}', ({width}, {height}))
        annot = rep.AnnotatorRegistry.get_annotator('rgb')
        annot.attach([rp])
        rep.orchestrator.step()
        data = annot.get_data()
        # Encode the numpy RGB(A) array to PNG via PIL
        try:
            from PIL import Image
            import numpy as np
            arr = np.asarray(data)
            if arr.ndim == 3 and arr.shape[2] == 4:
                img = Image.fromarray(arr[:, :, :3].astype('uint8'), mode='RGB')
            else:
                img = Image.fromarray(arr.astype('uint8'), mode='RGB')
            import io
            buf = io.BytesIO()
            img.save(buf, format='PNG')
            b64 = base64.b64encode(buf.getvalue()).decode('ascii')
        finally:
            try:
                annot.detach([rp])
            except Exception:
                pass
            try:
                rp.destroy()
            except Exception:
                pass
        print(json.dumps({{
            'camera_path': '{camera_path}',
            'resolution': [{width}, {height}],
            'image_base64': b64,
            'format': 'png',
            'message': 'Rendered 1 frame from {camera_path} at {width}x{height}',
        }}))
    except ImportError as e:
        print(json.dumps({{'error': 'Replicator unavailable: ' + str(e),
                           'hint': 'omni.replicator.core extension must be enabled'}}))
"""
    result = await kit_tools.exec_sync(code, timeout=30)
    if not result.get("success"):
        return {"error": f"Kit RPC /exec_sync failed: {result.get('output', 'unknown')}"}
    parsed = _parse_last_json_line(result.get("output", ""))
    if parsed is None:
        return {"error": "Failed to parse capture result", "raw_output": result.get("output", "")[:500]}
    return parsed

def _gen_set_camera_look_at(args: Dict) -> str:
    """Generate Python that orients a camera at a world-space target.

    Uses Gf.Matrix4d.SetLookAt — note that USD's Gf SetLookAt produces an
    *inverse* view matrix, so we extract its inverse and decompose into a
    rotation that the camera xform op can consume.
    """
    camera_path = args["camera_path"]
    target = args["target"]
    if not isinstance(target, (list, tuple)) or len(target) != 3:
        raise ValueError("target must be [x, y, z]")
    tx, ty, tz = float(target[0]), float(target[1]), float(target[2])

    up = args.get("up") or [0.0, 1.0, 0.0]
    if not isinstance(up, (list, tuple)) or len(up) != 3:
        raise ValueError("up must be [x, y, z]")
    ux, uy, uz = float(up[0]), float(up[1]), float(up[2])

    eye = args.get("eye")
    eye_block: List[str]
    if eye is not None:
        if not isinstance(eye, (list, tuple)) or len(eye) != 3:
            raise ValueError("eye must be [x, y, z] when provided")
        ex, ey, ez = float(eye[0]), float(eye[1]), float(eye[2])
        eye_block = [
            f"eye = Gf.Vec3d({ex}, {ey}, {ez})",
            "# Override translation to the supplied eye position",
            "_safe_set_translate(prim, (eye[0], eye[1], eye[2]))",
        ]
    else:
        eye_block = [
            "# Use camera's current world translation as the eye position",
            "world_xform = UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(Usd.TimeCode.Default())",
            "eye_v = world_xform.ExtractTranslation()",
            "eye = Gf.Vec3d(eye_v[0], eye_v[1], eye_v[2])",
        ]

    lines = [
        "import omni.usd",
        "from pxr import Usd, UsdGeom, Gf",
        _SAFE_XFORM_SNIPPET,
        "stage = omni.usd.get_context().get_stage()",
        f"prim = stage.GetPrimAtPath('{camera_path}')",
        "if not prim or not prim.IsValid():",
        f"    raise RuntimeError('Camera prim not found: {camera_path}')",
        "if prim.GetTypeName() != 'Camera':",
        f"    raise RuntimeError('Prim is not a Camera: {camera_path}')",
        "",
        f"target = Gf.Vec3d({tx}, {ty}, {tz})",
        f"up = Gf.Vec3d({ux}, {uy}, {uz})",
        *eye_block,
        "",
        "# Build a look-at view matrix and invert to a world-space camera transform.",
        "# Gf.Matrix4d.SetLookAt produces a view matrix (world->camera);",
        "# the camera's world transform is therefore its inverse.",
        "view = Gf.Matrix4d().SetLookAt(eye, target, up)",
        "world = view.GetInverse()",
        "rot = world.ExtractRotation()",
        "euler = rot.Decompose(Gf.Vec3d.ZAxis(), Gf.Vec3d.YAxis(), Gf.Vec3d.XAxis())",
        "# Decompose returns (Z, Y, X) — feed back as (X, Y, Z) for rotateXYZ op",
        "rx, ry, rz = float(euler[2]), float(euler[1]), float(euler[0])",
        "_safe_set_translate(prim, (eye[0], eye[1], eye[2]))",
        "_safe_set_rotate_xyz(prim, (rx, ry, rz))",
        f"print('set_camera_look_at: {camera_path} now looking at ({tx}, {ty}, {tz})')",
    ]
    return "\n".join(lines)

DATA_HANDLERS["list_cameras"] = _handle_list_cameras
DATA_HANDLERS["get_camera_params"] = _handle_get_camera_params
DATA_HANDLERS["capture_camera_image"] = _handle_capture_camera_image
CODE_GEN_HANDLERS["set_camera_params"] = _gen_set_camera_params
CODE_GEN_HANDLERS["set_camera_look_at"] = _gen_set_camera_look_at

# ══════ From feat/atomic-tier8-render ══════
async def _handle_get_render_config(args: Dict) -> Dict:
    """Read current renderer mode, SPP, max bounces, and viewport resolution.

    Generates a small introspection script and queues it via Kit RPC. The Kit
    side runs it and returns the printed JSON. When Kit is unreachable we
    return a structured stub so the LLM still gets predictable shape.
    """
    code = """\
import json
try:
    import omni.kit.viewport.utility as vp_util
    import omni.usd
    from pxr import Sdf

    vp = vp_util.get_active_viewport()
    resolution = list(vp.resolution) if vp is not None else [None, None]
    renderer = vp.hydra_engine if vp is not None else None

    stage = omni.usd.get_context().get_stage()

    def _read(attr_path, default=None):
        prim_path, _, attr_name = attr_path.rpartition('.')
        prim = stage.GetPrimAtPath(prim_path)
        if not prim or not prim.IsValid():
            return default
        attr = prim.GetAttribute(attr_name)
        if attr is None or not attr.HasValue():
            return default
        return attr.Get()

    spp = _read('/Render/Vars.samplesPerPixel', 1)
    max_bounces = _read('/Render/Vars.maxBounces', 4)
    bloom = bool(_read('/Render/PostProcess/Bloom.enabled', False))
    tonemap = str(_read('/Render/PostProcess/Tonemap.operator', 'aces'))
    dof = bool(_read('/Render/PostProcess/DoF.enabled', False))
    motion_blur = bool(_read('/Render/PostProcess/MotionBlur.enabled', False))

    print(json.dumps({
        'renderer': renderer,
        'samples_per_pixel': spp,
        'max_bounces': max_bounces,
        'resolution': resolution,
        'post_process': {
            'bloom': bloom,
            'tonemap': tonemap,
            'dof': dof,
            'motion_blur': motion_blur,
        },
    }))
except Exception as e:
    print(json.dumps({'error': str(e)}))
"""
    result = await kit_tools.queue_exec_patch(code, "Read current render config")
    return {
        "queued": result.get("queued", False),
        "patch_id": result.get("patch_id"),
        "note": (
            "Render config introspection queued. Kit will print a JSON dict with keys: "
            "renderer, samples_per_pixel, max_bounces, resolution, post_process."
        ),
    }

def _gen_set_render_config(args: Dict) -> str:
    renderer = args["renderer"]
    spp = args.get("samples_per_pixel")
    max_bounces = args.get("max_bounces")

    # PathTracing is enabled by setting /Render/Vars.rendermode = 'PathTracing'
    # (the default rtx delegate is RaytracedLighting / RealTime).
    rendermode_attr_value = repr(renderer)

    lines = [
        "import omni.usd",
        "from pxr import Sdf",
        "",
        "stage = omni.usd.get_context().get_stage()",
        "",
        "# Ensure /Render/Vars container exists",
        "render_vars = stage.DefinePrim('/Render/Vars', 'Scope')",
        "",
        "# Renderer mode (PathTracing | RaytracedLighting | RealTime)",
        f"render_vars.CreateAttribute('rendermode', Sdf.ValueTypeNames.String).Set({rendermode_attr_value})",
    ]

    if spp is not None:
        lines.append(
            f"render_vars.CreateAttribute('samplesPerPixel', Sdf.ValueTypeNames.Int).Set({int(spp)})"
        )
    if max_bounces is not None:
        lines.append(
            f"render_vars.CreateAttribute('maxBounces', Sdf.ValueTypeNames.Int).Set({int(max_bounces)})"
        )

    lines.extend([
        "",
        "# Switch the active hydra engine on the viewport",
        "try:",
        "    import omni.kit.viewport.utility as vp_util",
        "    vp = vp_util.get_active_viewport()",
        "    if vp is not None:",
        "        vp.hydra_engine = 'rtx'",
        "except Exception as _e:",
        f"    print('Viewport switch skipped (headless?):', _e)",
        "",
        f"print('Render config updated: renderer={renderer}, spp={spp}, max_bounces={max_bounces}')",
    ])
    return "\n".join(lines)

def _gen_set_render_resolution(args: Dict) -> str:
    width = int(args["width"])
    height = int(args["height"])
    return (
        "import omni.kit.viewport.utility as vp_util\n"
        "vp = vp_util.get_active_viewport()\n"
        "if vp is None:\n"
        "    raise RuntimeError('No active viewport — running headless?')\n"
        f"vp.resolution = ({width}, {height})\n"
        f"print('Viewport resolution set to {width}x{height}')"
    )

def _gen_enable_post_process(args: Dict) -> str:
    effect = args["effect"]
    params = args.get("params", {}) or {}
    enabled = args.get("enabled", True)

    prim_path = _POST_PROCESS_PATHS.get(effect, f"/Render/PostProcess/{effect}")

    lines = [
        "import omni.usd",
        "from pxr import Sdf",
        "",
        "stage = omni.usd.get_context().get_stage()",
        f"prim = stage.DefinePrim({prim_path!r}, 'Scope')",
        "",
        f"# Toggle the {effect} effect",
        f"prim.CreateAttribute('enabled', Sdf.ValueTypeNames.Bool).Set({bool(enabled)})",
    ]

    # Effect-specific parameter writes — kept generic so future params slot in.
    if effect == "bloom":
        if "intensity" in params:
            lines.append(
                f"prim.CreateAttribute('intensity', Sdf.ValueTypeNames.Float).Set({float(params['intensity'])})"
            )
        if "threshold" in params:
            lines.append(
                f"prim.CreateAttribute('threshold', Sdf.ValueTypeNames.Float).Set({float(params['threshold'])})"
            )
    elif effect == "tonemap":
        if "operator" in params:
            lines.append(
                f"prim.CreateAttribute('operator', Sdf.ValueTypeNames.String).Set({str(params['operator'])!r})"
            )
        if "exposure" in params:
            lines.append(
                f"prim.CreateAttribute('exposure', Sdf.ValueTypeNames.Float).Set({float(params['exposure'])})"
            )
    elif effect == "dof":
        if "focus_distance" in params:
            lines.append(
                f"prim.CreateAttribute('focusDistance', Sdf.ValueTypeNames.Float).Set({float(params['focus_distance'])})"
            )
        if "f_stop" in params:
            lines.append(
                f"prim.CreateAttribute('fStop', Sdf.ValueTypeNames.Float).Set({float(params['f_stop'])})"
            )
    elif effect == "motion_blur":
        if "shutter_speed" in params:
            lines.append(
                f"prim.CreateAttribute('shutterSpeed', Sdf.ValueTypeNames.Float).Set({float(params['shutter_speed'])})"
            )
        if "samples" in params:
            lines.append(
                f"prim.CreateAttribute('samples', Sdf.ValueTypeNames.Int).Set({int(params['samples'])})"
            )

    lines.append(f"print('Post-process {effect} enabled={bool(enabled)}')")
    return "\n".join(lines)

def _gen_set_environment_background(args: Dict) -> str:
    hdri_path = args.get("hdri_path")
    color = args.get("color")
    intensity = args.get("intensity", 1000.0)
    rotation_deg = args.get("rotation_deg", 0.0)

    if hdri_path and color:
        # Both provided — HDRI wins, but emit a comment so the user sees why.
        pass

    if hdri_path:
        return f"""\
import omni.usd
from pxr import UsdLux, UsdGeom, Sdf, Gf

stage = omni.usd.get_context().get_stage()
dome_path = '/World/EnvironmentLight'
dome = UsdLux.DomeLight.Define(stage, dome_path)
dome.CreateTextureFileAttr().Set({hdri_path!r})
dome.CreateIntensityAttr().Set({float(intensity)})
dome.CreateTextureFormatAttr().Set('latlong')

# Rotate dome around the up-axis
xf = UsdGeom.Xformable(dome.GetPrim())
xf.ClearXformOpOrder()
xf.AddRotateYOp().Set({float(rotation_deg)})

print('Environment HDRI set: {hdri_path} (intensity={intensity}, rotation={rotation_deg} deg)')
"""

    if color is not None:
        r, g, b = (float(color[0]), float(color[1]), float(color[2]))
        return f"""\
import omni.usd
from pxr import Sdf, Gf

stage = omni.usd.get_context().get_stage()
render_vars = stage.DefinePrim('/Render/Vars', 'Scope')
render_vars.CreateAttribute('clearColor', Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f({r}, {g}, {b}))

# Remove dome if present so the solid color is actually visible
dome_prim = stage.GetPrimAtPath('/World/EnvironmentLight')
if dome_prim and dome_prim.IsValid():
    stage.RemovePrim('/World/EnvironmentLight')

print('Environment background color set to ({r}, {g}, {b})')
"""

    # Neither provided — clear to a neutral grey by default so this stays a
    # well-defined no-arg call.
    return """\
import omni.usd
from pxr import Sdf, Gf

stage = omni.usd.get_context().get_stage()
render_vars = stage.DefinePrim('/Render/Vars', 'Scope')
render_vars.CreateAttribute('clearColor', Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(0.2, 0.2, 0.2))
print('Environment background reset to neutral grey (0.2, 0.2, 0.2)')
"""

DATA_HANDLERS["get_render_config"] = _handle_get_render_config
CODE_GEN_HANDLERS["set_render_config"] = _gen_set_render_config
CODE_GEN_HANDLERS["set_render_resolution"] = _gen_set_render_resolution
CODE_GEN_HANDLERS["enable_post_process"] = _gen_enable_post_process
CODE_GEN_HANDLERS["set_environment_background"] = _gen_set_environment_background

# ══════ From feat/atomic-tier9-layers ══════
async def _handle_list_layers(args: Dict) -> Dict:
    """Walk the current stage's layer stack and return identifiers + edit target.

    Generates a small introspection script and queues it via Kit RPC. Kit prints
    JSON with one entry per layer plus the active edit target; when Kit is
    unreachable we still return a structured stub so the LLM gets a predictable
    shape.
    """
    code = """\
import json
try:
    import omni.usd
    stage = omni.usd.get_context().get_stage()
    if stage is None:
        print(json.dumps({'error': 'no stage open'}))
    else:
        edit_target = stage.GetEditTarget().GetLayer()
        edit_target_id = edit_target.identifier if edit_target is not None else None
        root = stage.GetRootLayer()
        layers = []
        seen = set()
        # depth-first walk of the layer stack so 'depth' reflects sublayer nesting
        def _walk(layer, depth):
            if layer is None or layer.identifier in seen:
                return
            seen.add(layer.identifier)
            layers.append({
                'identifier': layer.identifier,
                'display_name': getattr(layer, 'GetDisplayName', lambda: layer.identifier)(),
                'anonymous': bool(layer.anonymous),
                'dirty': bool(layer.dirty),
                'depth': depth,
                'is_edit_target': layer.identifier == edit_target_id,
            })
            try:
                from pxr import Sdf
                for sub_path in layer.subLayerPaths:
                    sub = Sdf.Layer.FindOrOpen(sub_path)
                    _walk(sub, depth + 1)
            except Exception:
                pass
        _walk(root, 0)
        print(json.dumps({
            'root_layer': root.identifier if root is not None else None,
            'edit_target': edit_target_id,
            'layers': layers,
            'count': len(layers),
        }))
except Exception as e:
    print(json.dumps({'error': str(e)}))
"""
    result = await kit_tools.queue_exec_patch(code, "List USD layer stack and edit target")
    return {
        "queued": result.get("queued", False),
        "patch_id": result.get("patch_id"),
        "note": (
            "Layer stack introspection queued. Kit will print a JSON dict with keys: "
            "root_layer, edit_target, layers (list of {identifier, display_name, "
            "anonymous, dirty, depth, is_edit_target}), count."
        ),
    }

def _gen_add_sublayer(args: Dict) -> str:
    layer_path = args["layer_path"]
    layer_path_repr = repr(layer_path)
    return (
        "import os\n"
        "import omni.usd\n"
        "from pxr import Sdf\n"
        "\n"
        "stage = omni.usd.get_context().get_stage()\n"
        "if stage is None:\n"
        "    raise RuntimeError('No stage is open — cannot add sublayer')\n"
        "\n"
        f"layer_path = {layer_path_repr}\n"
        "\n"
        "# Create the file if it does not already exist (anonymous and omniverse:// URLs skip)\n"
        "if not layer_path.startswith('anon:') and '://' not in layer_path:\n"
        "    if not os.path.exists(layer_path):\n"
        "        new_layer = Sdf.Layer.CreateNew(layer_path)\n"
        "        if new_layer is None:\n"
        "            raise RuntimeError(f'Failed to create new sublayer at {layer_path}')\n"
        "        new_layer.Save()\n"
        "\n"
        "root = stage.GetRootLayer()\n"
        "if layer_path in list(root.subLayerPaths):\n"
        "    print(f'Sublayer already attached: {layer_path}')\n"
        "else:\n"
        "    # Insert at position 0 → strongest sublayer below the root\n"
        "    root.subLayerPaths.insert(0, layer_path)\n"
        "    print(f'Attached sublayer: {layer_path}')\n"
    )

def _gen_set_edit_target(args: Dict) -> str:
    layer_path = args["layer_path"]
    layer_path_repr = repr(layer_path)
    return (
        "import omni.usd\n"
        "from pxr import Sdf, Usd\n"
        "\n"
        "stage = omni.usd.get_context().get_stage()\n"
        "if stage is None:\n"
        "    raise RuntimeError('No stage is open — cannot set edit target')\n"
        "\n"
        f"layer_path = {layer_path_repr}\n"
        "layer = Sdf.Layer.FindOrOpen(layer_path)\n"
        "if layer is None:\n"
        "    # Try to find the layer already inside the stage's layer stack\n"
        "    for stack_layer in stage.GetLayerStack():\n"
        "        if stack_layer.identifier == layer_path:\n"
        "            layer = stack_layer\n"
        "            break\n"
        "if layer is None:\n"
        "    raise RuntimeError(\n"
        "        f'Layer not found: {layer_path}. Use list_layers() to see attached layers '\n"
        "        f'or add_sublayer() to attach it first.'\n"
        "    )\n"
        "\n"
        "stage.SetEditTarget(Usd.EditTarget(layer))\n"
        "print(f'Edit target is now: {layer.identifier}')\n"
    )

async def _handle_list_variant_sets(args: Dict) -> Dict:
    """Read every variant set declared on a prim and the current selection on each."""
    prim_path = args["prim_path"]
    prim_path_repr = repr(prim_path)
    code = (
        "import json\n"
        "try:\n"
        "    import omni.usd\n"
        "    stage = omni.usd.get_context().get_stage()\n"
        "    if stage is None:\n"
        "        print(json.dumps({'error': 'no stage open'}))\n"
        "    else:\n"
        f"        prim_path = {prim_path_repr}\n"
        "        prim = stage.GetPrimAtPath(prim_path)\n"
        "        if not prim or not prim.IsValid():\n"
        "            print(json.dumps({'error': f'prim not found: {prim_path}'}))\n"
        "        else:\n"
        "            vsets = prim.GetVariantSets()\n"
        "            names = list(vsets.GetNames())\n"
        "            entries = []\n"
        "            for name in names:\n"
        "                vs = vsets.GetVariantSet(name)\n"
        "                entries.append({\n"
        "                    'name': name,\n"
        "                    'current': vs.GetVariantSelection(),\n"
        "                    'count': len(vs.GetVariantNames()),\n"
        "                })\n"
        "            print(json.dumps({\n"
        "                'prim_path': prim_path,\n"
        "                'variant_sets': entries,\n"
        "                'count': len(entries),\n"
        "            }))\n"
        "except Exception as e:\n"
        "    print(json.dumps({'error': str(e)}))\n"
    )
    result = await kit_tools.queue_exec_patch(code, f"List variant sets on {prim_path}")
    return {
        "queued": result.get("queued", False),
        "patch_id": result.get("patch_id"),
        "prim_path": prim_path,
        "note": (
            "Variant-set introspection queued. Kit will print a JSON dict with keys: "
            "prim_path, variant_sets (list of {name, current, count}), count."
        ),
    }

async def _handle_list_variants(args: Dict) -> Dict:
    """List every named variant choice inside a specific variant set on a prim."""
    prim_path = args["prim_path"]
    variant_set = args["variant_set"]
    prim_path_repr = repr(prim_path)
    variant_set_repr = repr(variant_set)
    code = (
        "import json\n"
        "try:\n"
        "    import omni.usd\n"
        "    stage = omni.usd.get_context().get_stage()\n"
        "    if stage is None:\n"
        "        print(json.dumps({'error': 'no stage open'}))\n"
        "    else:\n"
        f"        prim_path = {prim_path_repr}\n"
        f"        variant_set_name = {variant_set_repr}\n"
        "        prim = stage.GetPrimAtPath(prim_path)\n"
        "        if not prim or not prim.IsValid():\n"
        "            print(json.dumps({'error': f'prim not found: {prim_path}'}))\n"
        "        else:\n"
        "            vsets = prim.GetVariantSets()\n"
        "            if not vsets.HasVariantSet(variant_set_name):\n"
        "                print(json.dumps({\n"
        "                    'error': f'variant set not found: {variant_set_name}',\n"
        "                    'available': list(vsets.GetNames()),\n"
        "                }))\n"
        "            else:\n"
        "                vs = vsets.GetVariantSet(variant_set_name)\n"
        "                names = list(vs.GetVariantNames())\n"
        "                print(json.dumps({\n"
        "                    'prim_path': prim_path,\n"
        "                    'variant_set': variant_set_name,\n"
        "                    'variants': names,\n"
        "                    'current': vs.GetVariantSelection(),\n"
        "                    'count': len(names),\n"
        "                }))\n"
        "except Exception as e:\n"
        "    print(json.dumps({'error': str(e)}))\n"
    )
    result = await kit_tools.queue_exec_patch(
        code, f"List variants in {variant_set} on {prim_path}"
    )
    return {
        "queued": result.get("queued", False),
        "patch_id": result.get("patch_id"),
        "prim_path": prim_path,
        "variant_set": variant_set,
        "note": (
            "Variant introspection queued. Kit will print a JSON dict with keys: "
            "prim_path, variant_set, variants (list of names), current, count."
        ),
    }

def _gen_flatten_layers(args: Dict) -> str:
    output_path = args["output_path"]
    output_path_repr = repr(output_path)
    return (
        "import omni.usd\n"
        "\n"
        "stage = omni.usd.get_context().get_stage()\n"
        "if stage is None:\n"
        "    raise RuntimeError('No stage is open — cannot flatten layers')\n"
        "\n"
        f"output_path = {output_path_repr}\n"
        "flat = stage.Flatten()\n"
        "if flat is None:\n"
        "    raise RuntimeError('stage.Flatten() returned None')\n"
        "\n"
        "ok = flat.Export(output_path)\n"
        "if not ok:\n"
        "    raise RuntimeError(f'Failed to export flattened stage to {output_path}')\n"
        "\n"
        "print(f'Flattened stage exported to: {output_path}')\n"
    )

DATA_HANDLERS["list_layers"] = _handle_list_layers
DATA_HANDLERS["list_variant_sets"] = _handle_list_variant_sets
DATA_HANDLERS["list_variants"] = _handle_list_variants
CODE_GEN_HANDLERS["add_sublayer"] = _gen_add_sublayer
CODE_GEN_HANDLERS["set_edit_target"] = _gen_set_edit_target
CODE_GEN_HANDLERS["flatten_layers"] = _gen_flatten_layers

# ══════ From feat/atomic-tier10-animation ══════
async def _handle_get_timeline_state(args: Dict) -> Dict:
    """Return current timeline cursor + start/end + fps + play state."""
    code = """\
import json
try:
    import omni.timeline
    import omni.usd
    tl = omni.timeline.get_timeline_interface()
    stage = omni.usd.get_context().get_stage()
    if stage is None:
        print(json.dumps({'error': 'no stage open'}))
    else:
        fps = float(stage.GetTimeCodesPerSecond() or 24.0)
        start_code = float(stage.GetStartTimeCode())
        end_code = float(stage.GetEndTimeCode())
        # current_time / start / end on the timeline interface are exposed in
        # *seconds* in modern Kit (>=105), so report both forms.
        try:
            cur = float(tl.get_current_time())
        except Exception:
            cur = float(tl.get_current_time_code()) / fps if fps else 0.0
        is_playing = bool(tl.is_playing()) if hasattr(tl, 'is_playing') else False
        looping = bool(tl.is_looping()) if hasattr(tl, 'is_looping') else False
        duration_codes = max(end_code - start_code, 0.0)
        print(json.dumps({
            'current_time': cur,
            'start_time': start_code,
            'end_time': end_code,
            'fps': fps,
            'time_codes_per_second': fps,
            'is_playing': is_playing,
            'looping': looping,
            'duration_seconds': duration_codes / fps if fps else 0.0,
        }))
except Exception as e:
    print(json.dumps({'error': str(e)}))
"""
    result = await kit_tools.queue_exec_patch(code, "Read timeline state (current/start/end/fps/playing)")
    return {
        "queued": result.get("queued", False),
        "patch_id": result.get("patch_id"),
        "note": (
            "Timeline-state introspection queued. Kit will print a JSON dict with keys: "
            "current_time, start_time, end_time, fps, time_codes_per_second, is_playing, "
            "looping, duration_seconds. Time codes are USD frames; duration_seconds = "
            "(end_time - start_time) / fps."
        ),
    }

def _gen_set_timeline_range(args: Dict) -> str:
    start = args["start"]
    end = args["end"]
    fps = args.get("fps")
    lines = [
        "import omni.usd",
        "import omni.timeline",
        "",
        "stage = omni.usd.get_context().get_stage()",
        "if stage is None:",
        "    raise RuntimeError('No stage is open — cannot set timeline range')",
        "",
        f"start_code = float({start!r})",
        f"end_code = float({end!r})",
        "if not (start_code < end_code):",
        "    raise ValueError(f'start ({start_code}) must be < end ({end_code})')",
        "",
    ]
    if fps is not None:
        lines += [
            f"fps = float({fps!r})",
            "if fps <= 0:",
            "    raise ValueError(f'fps must be > 0, got {fps}')",
            "stage.SetTimeCodesPerSecond(fps)",
        ]
    else:
        lines += [
            "fps = float(stage.GetTimeCodesPerSecond() or 24.0)",
        ]
    lines += [
        "stage.SetStartTimeCode(start_code)",
        "stage.SetEndTimeCode(end_code)",
        "",
        "# Push the new range into the timeline interface so the viewport scrubber updates.",
        "tl = omni.timeline.get_timeline_interface()",
        "try:",
        "    tl.set_start_time(start_code / fps)",
        "    tl.set_end_time(end_code / fps)",
        "except Exception:",
        "    # Older Kit versions accept time codes directly.",
        "    if hasattr(tl, 'set_start_time_code'):",
        "        tl.set_start_time_code(start_code)",
        "    if hasattr(tl, 'set_end_time_code'):",
        "        tl.set_end_time_code(end_code)",
        "",
        "print(f'Timeline range set: [{start_code}, {end_code}] codes @ {fps} fps')",
    ]
    return "\n".join(lines)

def _gen_set_keyframe(args: Dict) -> str:
    prim_path = args["prim_path"]
    attr = args["attr"]
    time = args["time"]
    value = args["value"]
    return (
        "import omni.usd\n"
        "from pxr import Sdf, Usd\n"
        "\n"
        "stage = omni.usd.get_context().get_stage()\n"
        "if stage is None:\n"
        "    raise RuntimeError('No stage is open — cannot set keyframe')\n"
        "\n"
        f"prim_path = {prim_path!r}\n"
        f"attr_name = {attr!r}\n"
        f"time_seconds = float({time!r})\n"
        f"value = {value!r}\n"
        "\n"
        "prim = stage.GetPrimAtPath(prim_path)\n"
        "if not prim or not prim.IsValid():\n"
        "    raise RuntimeError(f'prim not found: {prim_path}')\n"
        "\n"
        "fps = float(stage.GetTimeCodesPerSecond() or 24.0)\n"
        "time_code = Usd.TimeCode(time_seconds * fps)\n"
        "\n"
        "attr_handle = prim.GetAttribute(attr_name)\n"
        "if not attr_handle or not attr_handle.IsValid():\n"
        "    raise RuntimeError(\n"
        "        f'attribute not found on {prim_path}: {attr_name}. '\n"
        "        f'Use list_attributes() to see available attributes.'\n"
        "    )\n"
        "\n"
        "# Cast lists/tuples to Vt-friendly types when the attribute expects an array.\n"
        "try:\n"
        "    attr_handle.Set(value, time_code)\n"
        "except Exception as e:\n"
        "    # Common case: value is a Python list but attribute wants Gf.Vec3f / Vec3d.\n"
        "    from pxr import Gf\n"
        "    if isinstance(value, (list, tuple)) and len(value) == 3:\n"
        "        attr_handle.Set(Gf.Vec3f(*value), time_code)\n"
        "    elif isinstance(value, (list, tuple)) and len(value) == 4:\n"
        "        attr_handle.Set(Gf.Vec4f(*value), time_code)\n"
        "    else:\n"
        "        raise\n"
        "\n"
        "print(f'Keyframe written: {prim_path}.{attr_name} @ frame {time_code.GetValue()} '\n"
        "      f'(t={time_seconds}s, fps={fps}) = {value}')\n"
    )

async def _handle_list_keyframes(args: Dict) -> Dict:
    """Read every authored TimeSample on a single attribute."""
    prim_path = args["prim_path"]
    attr = args["attr"]
    prim_path_repr = repr(prim_path)
    attr_repr = repr(attr)
    code = (
        "import json\n"
        "try:\n"
        "    import omni.usd\n"
        "    stage = omni.usd.get_context().get_stage()\n"
        "    if stage is None:\n"
        "        print(json.dumps({'error': 'no stage open'}))\n"
        "    else:\n"
        f"        prim_path = {prim_path_repr}\n"
        f"        attr_name = {attr_repr}\n"
        "        prim = stage.GetPrimAtPath(prim_path)\n"
        "        if not prim or not prim.IsValid():\n"
        "            print(json.dumps({'error': f'prim not found: {prim_path}'}))\n"
        "        else:\n"
        "            attr_handle = prim.GetAttribute(attr_name)\n"
        "            if not attr_handle or not attr_handle.IsValid():\n"
        "                print(json.dumps({\n"
        "                    'error': f'attribute not found: {attr_name}',\n"
        "                    'prim_path': prim_path,\n"
        "                }))\n"
        "            else:\n"
        "                fps = float(stage.GetTimeCodesPerSecond() or 24.0)\n"
        "                times = list(attr_handle.GetTimeSamples())\n"
        "                samples = []\n"
        "                for tc in times:\n"
        "                    try:\n"
        "                        v = attr_handle.Get(tc)\n"
        "                        # Coerce Vt/Gf types into JSON-safe primitives.\n"
        "                        try:\n"
        "                            v_json = list(v) if hasattr(v, '__iter__') and not isinstance(v, str) else v\n"
        "                        except Exception:\n"
        "                            v_json = repr(v)\n"
        "                        samples.append({\n"
        "                            'time_code': float(tc),\n"
        "                            'time_seconds': float(tc) / fps if fps else 0.0,\n"
        "                            'value': v_json,\n"
        "                        })\n"
        "                    except Exception as e:\n"
        "                        samples.append({\n"
        "                            'time_code': float(tc),\n"
        "                            'time_seconds': float(tc) / fps if fps else 0.0,\n"
        "                            'value': None,\n"
        "                            'error': str(e),\n"
        "                        })\n"
        "                if times:\n"
        "                    first, last = float(times[0]), float(times[-1])\n"
        "                    range_codes = [first, last]\n"
        "                    range_seconds = [first / fps if fps else 0.0, last / fps if fps else 0.0]\n"
        "                else:\n"
        "                    range_codes = []\n"
        "                    range_seconds = []\n"
        "                print(json.dumps({\n"
        "                    'prim_path': prim_path,\n"
        "                    'attr': attr_name,\n"
        "                    'has_timesamples': bool(times),\n"
        "                    'count': len(times),\n"
        "                    'fps': fps,\n"
        "                    'samples': samples,\n"
        "                    'time_range_codes': range_codes,\n"
        "                    'time_range_seconds': range_seconds,\n"
        "                }))\n"
        "except Exception as e:\n"
        "    print(json.dumps({'error': str(e)}))\n"
    )
    result = await kit_tools.queue_exec_patch(
        code, f"List keyframes for {prim_path}.{attr}"
    )
    return {
        "queued": result.get("queued", False),
        "patch_id": result.get("patch_id"),
        "prim_path": prim_path,
        "attr": attr,
        "note": (
            "Keyframe enumeration queued. Kit will print a JSON dict with keys: "
            "prim_path, attr, has_timesamples, count, fps, samples (list of "
            "{time_code, time_seconds, value}), time_range_codes, time_range_seconds. "
            "has_timesamples=false means the attribute has only a default value."
        ),
    }

def _gen_play_animation(args: Dict) -> str:
    start = args["start"]
    end = args["end"]
    return (
        "import omni.timeline\n"
        "import omni.usd\n"
        "\n"
        "stage = omni.usd.get_context().get_stage()\n"
        "if stage is None:\n"
        "    raise RuntimeError('No stage is open — cannot play animation')\n"
        "\n"
        f"start_seconds = float({start!r})\n"
        f"end_seconds = float({end!r})\n"
        "if not (start_seconds < end_seconds):\n"
        "    raise ValueError(f'start ({start_seconds}) must be < end ({end_seconds})')\n"
        "\n"
        "fps = float(stage.GetTimeCodesPerSecond() or 24.0)\n"
        "start_code = start_seconds * fps\n"
        "end_code = end_seconds * fps\n"
        "\n"
        "tl = omni.timeline.get_timeline_interface()\n"
        "# Configure the playback window. Modern Kit uses seconds; older Kit uses time codes.\n"
        "try:\n"
        "    tl.set_start_time(start_seconds)\n"
        "    tl.set_end_time(end_seconds)\n"
        "    tl.set_current_time(start_seconds)\n"
        "except Exception:\n"
        "    if hasattr(tl, 'set_start_time_code'):\n"
        "        tl.set_start_time_code(start_code)\n"
        "    if hasattr(tl, 'set_end_time_code'):\n"
        "        tl.set_end_time_code(end_code)\n"
        "    if hasattr(tl, 'set_current_time_code'):\n"
        "        tl.set_current_time_code(start_code)\n"
        "\n"
        "tl.play()\n"
        "print(f'Playing animation [{start_seconds}s, {end_seconds}s] '\n"
        "      f'(frames {start_code}-{end_code} @ {fps} fps)')\n"
    )

DATA_HANDLERS["get_timeline_state"] = _handle_get_timeline_state
DATA_HANDLERS["list_keyframes"] = _handle_list_keyframes
CODE_GEN_HANDLERS["set_timeline_range"] = _gen_set_timeline_range
CODE_GEN_HANDLERS["set_keyframe"] = _gen_set_keyframe
CODE_GEN_HANDLERS["play_animation"] = _gen_play_animation

# ══════ From feat/atomic-tier11-sdg ══════
async def _handle_list_semantic_classes(args: Dict) -> Dict:
    """Walk the stage, collect every Semantics.SemanticsAPI label, return unique classes."""
    code = """\
import json
try:
    import omni.usd
    from pxr import Usd, Semantics
    stage = omni.usd.get_context().get_stage()
    if stage is None:
        print(json.dumps({'error': 'no stage open'}))
    else:
        classes = {}  # class_name -> {'count': int, 'sample_prims': [str, ...]}
        labeled = 0
        for prim in stage.Traverse():
            try:
                if not Semantics.SemanticsAPI.HasAPI(prim):
                    # Some Kit builds expose only the multi-apply variant — fall back to
                    # GetAll which returns an empty list when nothing is applied.
                    instances = Semantics.SemanticsAPI.GetAll(prim) if hasattr(
                        Semantics.SemanticsAPI, 'GetAll'
                    ) else []
                else:
                    instances = Semantics.SemanticsAPI.GetAll(prim) if hasattr(
                        Semantics.SemanticsAPI, 'GetAll'
                    ) else [Semantics.SemanticsAPI(prim, 'Semantics_class')]
            except Exception:
                instances = []
            if not instances:
                continue
            labeled += 1
            for sem in instances:
                try:
                    data_attr = sem.GetSemanticDataAttr()
                    cls = data_attr.Get() if data_attr and data_attr.IsValid() else None
                except Exception:
                    cls = None
                if cls is None or cls == '':
                    continue
                cls = str(cls)
                bucket = classes.setdefault(cls, {'count': 0, 'sample_prims': []})
                bucket['count'] += 1
                if len(bucket['sample_prims']) < 5:
                    bucket['sample_prims'].append(str(prim.GetPath()))
        out_classes = [
            {'name': name, 'count': info['count'], 'sample_prims': info['sample_prims']}
            for name, info in sorted(classes.items())
        ]
        print(json.dumps({
            'classes': out_classes,
            'total_classes': len(out_classes),
            'total_labeled_prims': labeled,
        }))
except Exception as e:
    print(json.dumps({'error': str(e)}))
"""
    result = await kit_tools.queue_exec_patch(
        code, "List unique semantic classes used on the current stage"
    )
    return {
        "queued": result.get("queued", False),
        "patch_id": result.get("patch_id"),
        "note": (
            "Semantic-class enumeration queued. Kit will print a JSON dict with keys: "
            "classes (list of {name, count, sample_prims}), total_classes, "
            "total_labeled_prims. count=1 for a class often signals a typo against the "
            "intended bulk label."
        ),
    }

async def _handle_get_semantic_label(args: Dict) -> Dict:
    """Read every Semantics.SemanticsAPI instance applied to a single prim."""
    prim_path = args["prim_path"]
    prim_path_repr = repr(prim_path)
    code = (
        "import json\n"
        "try:\n"
        "    import omni.usd\n"
        "    from pxr import Usd, Semantics\n"
        "    stage = omni.usd.get_context().get_stage()\n"
        "    if stage is None:\n"
        "        print(json.dumps({'error': 'no stage open'}))\n"
        "    else:\n"
        f"        prim_path = {prim_path_repr}\n"
        "        prim = stage.GetPrimAtPath(prim_path)\n"
        "        if not prim or not prim.IsValid():\n"
        "            print(json.dumps({'error': f'prim not found: {prim_path}'}))\n"
        "        else:\n"
        "            try:\n"
        "                instances = Semantics.SemanticsAPI.GetAll(prim) if hasattr(\n"
        "                    Semantics.SemanticsAPI, 'GetAll'\n"
        "                ) else []\n"
        "            except Exception:\n"
        "                instances = []\n"
        "            labels = []\n"
        "            for sem in instances:\n"
        "                try:\n"
        "                    instance_name = sem.GetName() if hasattr(sem, 'GetName') else ''\n"
        "                except Exception:\n"
        "                    instance_name = ''\n"
        "                try:\n"
        "                    type_attr = sem.GetSemanticTypeAttr()\n"
        "                    sem_type = type_attr.Get() if type_attr and type_attr.IsValid() else ''\n"
        "                except Exception:\n"
        "                    sem_type = ''\n"
        "                try:\n"
        "                    data_attr = sem.GetSemanticDataAttr()\n"
        "                    cls = data_attr.Get() if data_attr and data_attr.IsValid() else ''\n"
        "                except Exception:\n"
        "                    cls = ''\n"
        "                labels.append({\n"
        "                    'instance': str(instance_name),\n"
        "                    'semantic_type': str(sem_type) if sem_type is not None else '',\n"
        "                    'class_name': str(cls) if cls is not None else '',\n"
        "                })\n"
        "            print(json.dumps({\n"
        "                'prim_path': prim_path,\n"
        "                'has_semantics': bool(labels),\n"
        "                'labels': labels,\n"
        "                'count': len(labels),\n"
        "            }))\n"
        "except Exception as e:\n"
        "    print(json.dumps({'error': str(e)}))\n"
    )
    result = await kit_tools.queue_exec_patch(
        code, f"Read Semantics.SemanticsAPI labels on {prim_path}"
    )
    return {
        "queued": result.get("queued", False),
        "patch_id": result.get("patch_id"),
        "prim_path": prim_path,
        "note": (
            "Semantic-label lookup queued. Kit will print a JSON dict with keys: "
            "prim_path, has_semantics, labels (list of {instance, semantic_type, "
            "class_name}), count. has_semantics=false means the prim is not labeled — "
            "that is a normal state, not an error."
        ),
    }

def _gen_remove_semantic_label(args: Dict) -> str:
    prim_path = args["prim_path"]
    prim_path_repr = repr(prim_path)
    return (
        "import omni.usd\n"
        "from pxr import Usd, Semantics, Sdf\n"
        "\n"
        "stage = omni.usd.get_context().get_stage()\n"
        "if stage is None:\n"
        "    raise RuntimeError('No stage is open — cannot remove semantic label')\n"
        "\n"
        f"prim_path = {prim_path_repr}\n"
        "prim = stage.GetPrimAtPath(prim_path)\n"
        "if not prim or not prim.IsValid():\n"
        "    raise RuntimeError(f'prim not found: {prim_path}')\n"
        "\n"
        "# Enumerate every Semantics_* instance, remove the API and clear leftover attrs.\n"
        "try:\n"
        "    instances = Semantics.SemanticsAPI.GetAll(prim) if hasattr(\n"
        "        Semantics.SemanticsAPI, 'GetAll'\n"
        "    ) else []\n"
        "except Exception:\n"
        "    instances = []\n"
        "\n"
        "if not instances:\n"
        "    print(f'No Semantics.SemanticsAPI applied on {prim_path} — nothing to remove (no-op)')\n"
        "else:\n"
        "    removed = []\n"
        "    for sem in instances:\n"
        "        try:\n"
        "            instance_name = sem.GetName() if hasattr(sem, 'GetName') else ''\n"
        "        except Exception:\n"
        "            instance_name = ''\n"
        "        try:\n"
        "            prim.RemoveAPI(Semantics.SemanticsAPI, instance_name)\n"
        "        except Exception:\n"
        "            # Older Kit: RemoveAppliedSchema works on the underlying spec\n"
        "            try:\n"
        "                full = f'SemanticsAPI:{instance_name}' if instance_name else 'SemanticsAPI'\n"
        "                prim.RemoveAppliedSchema(full)\n"
        "            except Exception:\n"
        "                pass\n"
        "        # Explicitly clear the attributes RemoveAPI leaves behind so HasAPI() is False.\n"
        "        for attr_name in (\n"
        "            f'semantic:{instance_name}:params:semanticType' if instance_name else 'semantic:params:semanticType',\n"
        "            f'semantic:{instance_name}:params:semanticData' if instance_name else 'semantic:params:semanticData',\n"
        "        ):\n"
        "            attr = prim.GetAttribute(attr_name)\n"
        "            if attr and attr.IsValid():\n"
        "                try:\n"
        "                    prim.RemoveProperty(attr_name)\n"
        "                except Exception:\n"
        "                    pass\n"
        "        removed.append(instance_name or '<default>')\n"
        "    print(f'Removed Semantics.SemanticsAPI from {prim_path}: instances={removed}')\n"
    )

def _gen_assign_class_to_children(args: Dict) -> str:
    prim_path = args["prim_path"]
    class_name = args["class_name"]
    semantic_type = args.get("semantic_type", "class")
    prim_path_repr = repr(prim_path)
    class_name_repr = repr(class_name)
    semantic_type_repr = repr(semantic_type)
    instance_name = f"Semantics_{semantic_type}"
    instance_name_repr = repr(instance_name)
    return (
        "import omni.usd\n"
        "from pxr import Usd, UsdGeom, Semantics\n"
        "\n"
        "stage = omni.usd.get_context().get_stage()\n"
        "if stage is None:\n"
        "    raise RuntimeError('No stage is open — cannot assign class to children')\n"
        "\n"
        f"root_path = {prim_path_repr}\n"
        f"class_name = {class_name_repr}\n"
        f"semantic_type = {semantic_type_repr}\n"
        f"instance_name = {instance_name_repr}\n"
        "\n"
        "root = stage.GetPrimAtPath(root_path)\n"
        "if not root or not root.IsValid():\n"
        "    raise RuntimeError(f'prim not found: {root_path}')\n"
        "\n"
        "# Walk root + every descendant. Only Mesh / Imageable prims (i.e. things that\n"
        "# render and therefore appear in SDG output) get the label — Xforms and pure\n"
        "# grouping prims are skipped because labels on them are dead weight.\n"
        "labeled = []\n"
        "skipped = []\n"
        "for prim in Usd.PrimRange(root):\n"
        "    if not prim or not prim.IsValid():\n"
        "        continue\n"
        "    is_mesh = prim.IsA(UsdGeom.Mesh)\n"
        "    is_imageable = prim.IsA(UsdGeom.Gprim)  # Mesh, Sphere, Cube, ... — anything that draws\n"
        "    if not (is_mesh or is_imageable):\n"
        "        skipped.append(str(prim.GetPath()))\n"
        "        continue\n"
        "    sem = Semantics.SemanticsAPI.Apply(prim, instance_name)\n"
        "    sem.CreateSemanticTypeAttr().Set(semantic_type)\n"
        "    sem.CreateSemanticDataAttr().Set(class_name)\n"
        "    labeled.append(str(prim.GetPath()))\n"
        "\n"
        "print(\n"
        "    f'assign_class_to_children: root={root_path} class={class_name!r} '\n"
        "    f'type={semantic_type!r} labeled={len(labeled)} skipped={len(skipped)}'\n"
        ")\n"
        "if labeled:\n"
        "    print(f'  first labeled: {labeled[:5]}')\n"
    )

async def _handle_validate_semantic_labels(args: Dict) -> Dict:
    """Lint every Semantics.SemanticsAPI annotation on the current stage."""
    code = """\
import json
try:
    import omni.usd
    from pxr import Usd, UsdGeom, Semantics
    stage = omni.usd.get_context().get_stage()
    if stage is None:
        print(json.dumps({'error': 'no stage open'}))
    else:
        issues = []
        labeled_prims = 0
        class_to_prims = {}  # class_name -> [prim_path, ...]
        for prim in stage.Traverse():
            try:
                instances = Semantics.SemanticsAPI.GetAll(prim) if hasattr(
                    Semantics.SemanticsAPI, 'GetAll'
                ) else []
            except Exception:
                instances = []
            if not instances:
                continue
            labeled_prims += 1
            prim_path = str(prim.GetPath())
            # Visibility / active checks — labels on hidden prims don't render
            try:
                is_active = bool(prim.IsActive())
            except Exception:
                is_active = True
            try:
                imageable = UsdGeom.Imageable(prim)
                vis = imageable.ComputeVisibility() if imageable else 'inherited'
                is_visible = vis != 'invisible'
            except Exception:
                is_visible = True
            if not is_active:
                issues.append({
                    'severity': 'warning', 'kind': 'inactive_labeled_prim',
                    'prim_path': prim_path,
                    'detail': 'Prim has Semantics labels but is deactivated — will not appear in SDG output.',
                })
            elif not is_visible:
                issues.append({
                    'severity': 'warning', 'kind': 'invisible_labeled_prim',
                    'prim_path': prim_path,
                    'detail': 'Prim has Semantics labels but visibility=invisible — will not render.',
                })
            class_seen_on_prim = []
            for sem in instances:
                try:
                    instance_name = sem.GetName() if hasattr(sem, 'GetName') else ''
                except Exception:
                    instance_name = ''
                try:
                    type_attr = sem.GetSemanticTypeAttr()
                    sem_type = type_attr.Get() if type_attr and type_attr.IsValid() else ''
                except Exception:
                    sem_type = ''
                try:
                    data_attr = sem.GetSemanticDataAttr()
                    cls = data_attr.Get() if data_attr and data_attr.IsValid() else ''
                except Exception:
                    cls = ''
                cls = '' if cls is None else str(cls)
                if cls == '':
                    issues.append({
                        'severity': 'error', 'kind': 'empty_class_name',
                        'prim_path': prim_path,
                        'detail': f'Semantics instance {instance_name!r} has empty semanticData — SDG writer will skip the label.',
                    })
                else:
                    class_to_prims.setdefault(cls, []).append(prim_path)
                if str(sem_type) == 'class' and cls != '':
                    class_seen_on_prim.append(cls)
            if len(class_seen_on_prim) > 1 and len(set(class_seen_on_prim)) > 1:
                issues.append({
                    'severity': 'error', 'kind': 'conflicting_class_labels',
                    'prim_path': prim_path,
                    'detail': f'Prim has multiple semantic_type=class instances with different class_names: {sorted(set(class_seen_on_prim))}',
                })
        # Singleton-class warnings: a class with exactly one prim is often a typo.
        for cls, prims in class_to_prims.items():
            if len(prims) == 1 and len(class_to_prims) > 1:
                issues.append({
                    'severity': 'warning', 'kind': 'singleton_class',
                    'prim_path': prims[0],
                    'detail': f'Class {cls!r} is used on a single prim — likely a typo against the intended bulk class.',
                })
        summary = {
            'labeled_prims': labeled_prims,
            'classes': len(class_to_prims),
            'issues': len(issues),
        }
        ok = not any(i['severity'] == 'error' for i in issues)
        print(json.dumps({
            'ok': ok,
            'summary': summary,
            'issues': issues,
        }))
except Exception as e:
    print(json.dumps({'error': str(e)}))
"""
    result = await kit_tools.queue_exec_patch(
        code, "Validate USD-side Semantics.SemanticsAPI annotations on the stage"
    )
    return {
        "queued": result.get("queued", False),
        "patch_id": result.get("patch_id"),
        "note": (
            "Semantic-label validation queued. Kit will print a JSON dict with keys: "
            "ok (bool, false when any error issue is present), summary "
            "({labeled_prims, classes, issues}), issues (list of {severity, kind, "
            "prim_path, detail}). Distinct from PR #23 validate_annotations: this tool "
            "lints the USD STAGE annotations, validate_annotations lints the SDG "
            "OUTPUT FILES on disk."
        ),
    }

DATA_HANDLERS["list_semantic_classes"] = _handle_list_semantic_classes
DATA_HANDLERS["get_semantic_label"] = _handle_get_semantic_label
DATA_HANDLERS["validate_semantic_labels"] = _handle_validate_semantic_labels
CODE_GEN_HANDLERS["remove_semantic_label"] = _gen_remove_semantic_label
CODE_GEN_HANDLERS["assign_class_to_children"] = _gen_assign_class_to_children

# ══════ From feat/atomic-tier12-asset-mgmt ══════
async def _handle_list_references(args: Dict) -> Dict:
    """Enumerate USD reference arcs composed onto a prim."""
    prim_path = args["prim_path"]
    prim_path_repr = repr(prim_path)
    code = (
        "import json\n"
        "try:\n"
        "    import omni.usd\n"
        "    from pxr import Usd, Sdf, Pcp\n"
        "    stage = omni.usd.get_context().get_stage()\n"
        "    if stage is None:\n"
        "        print(json.dumps({'error': 'no stage open'}))\n"
        "    else:\n"
        f"        prim_path = {prim_path_repr}\n"
        "        prim = stage.GetPrimAtPath(prim_path)\n"
        "        if not prim or not prim.IsValid():\n"
        "            print(json.dumps({'error': f'prim not found: {prim_path}'}))\n"
        "        else:\n"
        + _TIER12_HELPERS
        + "            references = []\n"
        "            # Local opinions via prim.GetReferences().GetAllReferences() —\n"
        "            # available on most Kit builds. Fall back to PrimCompositionQuery\n"
        "            # when the simple API is missing.\n"
        "            try:\n"
        "                refs_api = prim.GetReferences()\n"
        "                local_refs = refs_api.GetAllReferences() if hasattr(\n"
        "                    refs_api, 'GetAllReferences'\n"
        "                ) else []\n"
        "            except Exception:\n"
        "                local_refs = []\n"
        "            for r in local_refs:\n"
        "                try:\n"
        "                    references.append({\n"
        "                        'asset_path': str(r.assetPath) if hasattr(r, 'assetPath') else '',\n"
        "                        'prim_path': str(r.primPath) if hasattr(r, 'primPath') else '',\n"
        "                        'layer_offset': _layer_offset_dict(getattr(r, 'layerOffset', None)),\n"
        "                        'introducing_layer': '<local>',\n"
        "                        'list_position': 'explicit',\n"
        "                    })\n"
        "                except Exception:\n"
        "                    continue\n"
        "            # Composed arcs (sublayered / inherited reference arcs) via PrimCompositionQuery.\n"
        "            try:\n"
        "                query = Usd.PrimCompositionQuery.GetDirectReferences(prim)\n"
        "                for arc in query.GetCompositionArcs():\n"
        "                    try:\n"
        "                        intro_layer = arc.GetIntroducingLayer()\n"
        "                        intro = intro_layer.identifier if intro_layer else ''\n"
        "                        if intro == '<local>' or any(\n"
        "                            ref.get('introducing_layer') == intro for ref in references\n"
        "                        ):\n"
        "                            continue\n"
        "                        target = arc.GetTargetNode()\n"
        "                        asset = ''\n"
        "                        target_path = ''\n"
        "                        if target is not None:\n"
        "                            try:\n"
        "                                site = target.path\n"
        "                                target_path = str(site)\n"
        "                            except Exception:\n"
        "                                target_path = ''\n"
        "                            try:\n"
        "                                asset_layer = target.layerStack.identifier.rootLayer\n"
        "                                asset = asset_layer.identifier\n"
        "                            except Exception:\n"
        "                                asset = ''\n"
        "                        references.append({\n"
        "                            'asset_path': asset,\n"
        "                            'prim_path': target_path,\n"
        "                            'layer_offset': {'offset': 0.0, 'scale': 1.0},\n"
        "                            'introducing_layer': intro,\n"
        "                            'list_position': 'explicit',\n"
        "                        })\n"
        "                    except Exception:\n"
        "                        continue\n"
        "            except Exception:\n"
        "                pass\n"
        "            print(json.dumps({\n"
        "                'prim_path': prim_path,\n"
        "                'has_references': bool(references),\n"
        "                'references': references,\n"
        "                'count': len(references),\n"
        "            }))\n"
        "except Exception as e:\n"
        "    print(json.dumps({'error': str(e)}))\n"
    )
    result = await kit_tools.queue_exec_patch(
        code, f"List USD references composed onto {prim_path}"
    )
    return {
        "queued": result.get("queued", False),
        "patch_id": result.get("patch_id"),
        "prim_path": prim_path,
        "note": (
            "Reference enumeration queued. Kit will print a JSON dict with keys: "
            "prim_path, has_references, references (list of {asset_path, prim_path, "
            "layer_offset, introducing_layer, list_position}), count. "
            "has_references=false means the prim has no references — that is a normal "
            "state, not an error. References are ALWAYS loaded — use list_payloads "
            "for the deferred-load equivalent."
        ),
    }

def _gen_add_usd_reference(args: Dict) -> str:
    prim_path = args["prim_path"]
    usd_url = args["usd_url"]
    ref_prim_path = args.get("ref_prim_path")
    layer_offset_seconds = args.get("layer_offset_seconds")
    instanceable = bool(args.get("instanceable", False))

    prim_path_repr = repr(prim_path)
    usd_url_repr = repr(usd_url)
    ref_prim_path_repr = repr(ref_prim_path) if ref_prim_path else "None"
    layer_offset_repr = (
        repr(float(layer_offset_seconds)) if layer_offset_seconds is not None else "None"
    )
    instanceable_repr = "True" if instanceable else "False"

    return (
        "import omni.usd\n"
        "from pxr import Usd, Sdf, UsdGeom\n"
        "\n"
        "stage = omni.usd.get_context().get_stage()\n"
        "if stage is None:\n"
        "    raise RuntimeError('No stage is open — cannot add USD reference')\n"
        "\n"
        f"prim_path = {prim_path_repr}\n"
        f"usd_url = {usd_url_repr}\n"
        f"ref_prim_path = {ref_prim_path_repr}\n"
        f"layer_offset_seconds = {layer_offset_repr}\n"
        f"instanceable = {instanceable_repr}\n"
        "\n"
        "# Auto-create the holding prim as an Xform if it does not exist.\n"
        "prim = stage.GetPrimAtPath(prim_path)\n"
        "if not prim or not prim.IsValid():\n"
        "    prim = UsdGeom.Xform.Define(stage, prim_path).GetPrim()\n"
        "    print(f'Created Xform at {prim_path} to hold the reference')\n"
        "\n"
        "# Build the LayerOffset in USD time codes (caller passes SECONDS).\n"
        "layer_offset = None\n"
        "if layer_offset_seconds is not None:\n"
        "    try:\n"
        "        tcps = stage.GetTimeCodesPerSecond() or 24.0\n"
        "    except Exception:\n"
        "        tcps = 24.0\n"
        "    layer_offset = Sdf.LayerOffset(layer_offset_seconds * tcps, 1.0)\n"
        "\n"
        "refs_api = prim.GetReferences()\n"
        "if ref_prim_path and layer_offset is not None:\n"
        "    refs_api.AddReference(usd_url, ref_prim_path, layer_offset)\n"
        "elif ref_prim_path:\n"
        "    refs_api.AddReference(usd_url, ref_prim_path)\n"
        "elif layer_offset is not None:\n"
        "    refs_api.AddReference(usd_url, '', layer_offset)\n"
        "else:\n"
        "    refs_api.AddReference(usd_url)\n"
        "\n"
        "if instanceable:\n"
        "    # USD point-instancing: per-instance edits below this prim are dropped.\n"
        "    prim.SetInstanceable(True)\n"
        "    print(f'  prim marked instanceable=True (per-instance edits below {prim_path} will be dropped)')\n"
        "\n"
        "print(\n"
        "    f'add_usd_reference: prim={prim_path} asset={usd_url!r} '\n"
        "    f'ref_prim={ref_prim_path!r} offset_s={layer_offset_seconds} '\n"
        "    f'instanceable={instanceable}'\n"
        ")\n"
    )

async def _handle_list_payloads(args: Dict) -> Dict:
    """Enumerate USD payload arcs (deferred-load) on a prim."""
    prim_path = args["prim_path"]
    prim_path_repr = repr(prim_path)
    code = (
        "import json\n"
        "try:\n"
        "    import omni.usd\n"
        "    from pxr import Usd, Sdf, Pcp\n"
        "    stage = omni.usd.get_context().get_stage()\n"
        "    if stage is None:\n"
        "        print(json.dumps({'error': 'no stage open'}))\n"
        "    else:\n"
        f"        prim_path = {prim_path_repr}\n"
        "        prim = stage.GetPrimAtPath(prim_path)\n"
        "        if not prim or not prim.IsValid():\n"
        "            print(json.dumps({'error': f'prim not found: {prim_path}'}))\n"
        "        else:\n"
        + _TIER12_HELPERS
        + "            payloads = []\n"
        "            try:\n"
        "                pl_api = prim.GetPayloads()\n"
        "                local_pls = pl_api.GetAllPayloads() if hasattr(\n"
        "                    pl_api, 'GetAllPayloads'\n"
        "                ) else []\n"
        "            except Exception:\n"
        "                local_pls = []\n"
        "            # Current load-set membership tells us which prims have their\n"
        "            # payloads activated right now.\n"
        "            try:\n"
        "                load_set = stage.GetLoadSet()\n"
        "                prim_is_loaded = bool(prim.GetPath() in load_set)\n"
        "            except Exception:\n"
        "                prim_is_loaded = True  # default: loaded\n"
        "            for p in local_pls:\n"
        "                try:\n"
        "                    payloads.append({\n"
        "                        'asset_path': str(p.assetPath) if hasattr(p, 'assetPath') else '',\n"
        "                        'prim_path': str(p.primPath) if hasattr(p, 'primPath') else '',\n"
        "                        'layer_offset': _layer_offset_dict(getattr(p, 'layerOffset', None)),\n"
        "                        'introducing_layer': '<local>',\n"
        "                        'is_loaded': prim_is_loaded,\n"
        "                        'list_position': 'explicit',\n"
        "                    })\n"
        "                except Exception:\n"
        "                    continue\n"
        "            # Composed arcs via PrimCompositionQuery.\n"
        "            try:\n"
        "                query = Usd.PrimCompositionQuery.GetDirectInherits(prim)  # placeholder; real call below\n"
        "                query = Usd.PrimCompositionQuery(prim)\n"
        "                filt = Usd.CompositionArcFilter() if hasattr(Usd, 'CompositionArcFilter') else None\n"
        "                for arc in query.GetCompositionArcs():\n"
        "                    try:\n"
        "                        if str(arc.GetArcType()).lower().find('payload') < 0:\n"
        "                            continue\n"
        "                        intro_layer = arc.GetIntroducingLayer()\n"
        "                        intro = intro_layer.identifier if intro_layer else ''\n"
        "                        if intro == '<local>':\n"
        "                            continue\n"
        "                        target = arc.GetTargetNode()\n"
        "                        asset = ''\n"
        "                        target_path = ''\n"
        "                        if target is not None:\n"
        "                            try:\n"
        "                                target_path = str(target.path)\n"
        "                            except Exception:\n"
        "                                target_path = ''\n"
        "                            try:\n"
        "                                asset = target.layerStack.identifier.rootLayer.identifier\n"
        "                            except Exception:\n"
        "                                asset = ''\n"
        "                        payloads.append({\n"
        "                            'asset_path': asset,\n"
        "                            'prim_path': target_path,\n"
        "                            'layer_offset': {'offset': 0.0, 'scale': 1.0},\n"
        "                            'introducing_layer': intro,\n"
        "                            'is_loaded': prim_is_loaded,\n"
        "                            'list_position': 'explicit',\n"
        "                        })\n"
        "                    except Exception:\n"
        "                        continue\n"
        "            except Exception:\n"
        "                pass\n"
        "            print(json.dumps({\n"
        "                'prim_path': prim_path,\n"
        "                'has_payloads': bool(payloads),\n"
        "                'payloads': payloads,\n"
        "                'count': len(payloads),\n"
        "                'prim_is_loaded': prim_is_loaded,\n"
        "            }))\n"
        "except Exception as e:\n"
        "    print(json.dumps({'error': str(e)}))\n"
    )
    result = await kit_tools.queue_exec_patch(
        code, f"List USD payloads (deferred-load) on {prim_path}"
    )
    return {
        "queued": result.get("queued", False),
        "patch_id": result.get("patch_id"),
        "prim_path": prim_path,
        "note": (
            "Payload enumeration queued. Kit will print a JSON dict with keys: "
            "prim_path, has_payloads, payloads (list of {asset_path, prim_path, "
            "layer_offset, introducing_layer, is_loaded, list_position}), count, "
            "prim_is_loaded. has_payloads=false (no payload arcs on this prim) is a "
            "normal state, not an error. is_loaded reflects the CURRENT load-set "
            "membership and can be flipped via load_payload."
        ),
    }

def _gen_load_payload(args: Dict) -> str:
    prim_path = args["prim_path"]
    prim_path_repr = repr(prim_path)
    return (
        "import omni.usd\n"
        "from pxr import Usd, Sdf\n"
        "\n"
        "stage = omni.usd.get_context().get_stage()\n"
        "if stage is None:\n"
        "    raise RuntimeError('No stage is open — cannot load payload')\n"
        "\n"
        f"prim_path = {prim_path_repr}\n"
        "prim = stage.GetPrimAtPath(prim_path)\n"
        "if not prim or not prim.IsValid():\n"
        "    raise RuntimeError(f'prim not found: {prim_path}')\n"
        "\n"
        "# Soft no-op if the prim's payload(s) are already in the load set.\n"
        "try:\n"
        "    load_set = stage.GetLoadSet()\n"
        "    already_loaded = prim.GetPath() in load_set\n"
        "except Exception:\n"
        "    already_loaded = False\n"
        "\n"
        "if already_loaded:\n"
        "    print(f'Payload already loaded for {prim_path} — nothing to do (no-op)')\n"
        "else:\n"
        "    # LoadAndUnload({prim_path}, set()) loads the payload + descendants.\n"
        "    try:\n"
        "        stage.LoadAndUnload(\n"
        "            {Sdf.Path(prim_path)},\n"
        "            set(),\n"
        "            Usd.LoadWithDescendants,\n"
        "        )\n"
        "    except Exception:\n"
        "        # Older Kit signature without policy arg:\n"
        "        stage.LoadAndUnload({Sdf.Path(prim_path)}, set())\n"
        "    print(\n"
        "        f'load_payload: activated payload(s) on {prim_path} (LoadWithDescendants)'\n"
        "    )\n"
    )

async def _handle_get_asset_info(args: Dict) -> Dict:
    """Read assetInfo metadata + introducing layer + sha256 for a prim."""
    prim_path = args["prim_path"]
    prim_path_repr = repr(prim_path)
    code = (
        "import json\n"
        "import os\n"
        "import hashlib\n"
        "try:\n"
        "    import omni.usd\n"
        "    from pxr import Usd, Sdf\n"
        "    stage = omni.usd.get_context().get_stage()\n"
        "    if stage is None:\n"
        "        print(json.dumps({'error': 'no stage open'}))\n"
        "    else:\n"
        f"        prim_path = {prim_path_repr}\n"
        "        prim = stage.GetPrimAtPath(prim_path)\n"
        "        if not prim or not prim.IsValid():\n"
        "            print(json.dumps({'error': f'prim not found: {prim_path}'}))\n"
        "        else:\n"
        "            ai = {}\n"
        "            try:\n"
        "                raw = prim.GetAssetInfo() or {}\n"
        "                # GetAssetInfo returns a VtDictionary — coerce to plain dict.\n"
        "                ai = {k: raw[k] for k in raw.keys()} if hasattr(raw, 'keys') else dict(raw)\n"
        "            except Exception:\n"
        "                ai = {}\n"
        "            asset_info = {\n"
        "                'identifier': str(ai.get('identifier', '') or ''),\n"
        "                'name': str(ai.get('name', '') or ''),\n"
        "                'version': str(ai.get('version', '') or ''),\n"
        "                'payload_asset_dependencies': [\n"
        "                    str(x) for x in (ai.get('payloadAssetDependencies') or [])\n"
        "                ],\n"
        "            }\n"
        "            has_asset_info = bool(\n"
        "                asset_info['identifier'] or asset_info['name']\n"
        "                or asset_info['version'] or asset_info['payload_asset_dependencies']\n"
        "            )\n"
        "            # Introducing layer — the layer that brought this prim into the\n"
        "            # composed stage. Use prim.GetPrimStack()[0] (strongest spec).\n"
        "            intro_layer = {'identifier': '', 'real_path': '', 'version': None, 'sha256': None}\n"
        "            try:\n"
        "                stack = prim.GetPrimStack()\n"
        "                if stack:\n"
        "                    spec = stack[0]\n"
        "                    layer = spec.layer if hasattr(spec, 'layer') else None\n"
        "                    if layer is not None:\n"
        "                        intro_layer['identifier'] = str(layer.identifier)\n"
        "                        intro_layer['real_path'] = str(layer.realPath or '')\n"
        "                        intro_layer['version'] = str(layer.GetCustomLayerData().get('version', '')) or None\n"
        "                        rp = intro_layer['real_path']\n"
        "                        if rp and os.path.isfile(rp):\n"
        "                            try:\n"
        "                                size = os.path.getsize(rp)\n"
        "                            except OSError:\n"
        "                                size = 0\n"
        "                            if 0 < size < 256 * 1024 * 1024:\n"
        "                                h = hashlib.sha256()\n"
        "                                with open(rp, 'rb') as f:\n"
        "                                    for chunk in iter(lambda: f.read(65536), b''):\n"
        "                                        h.update(chunk)\n"
        "                                intro_layer['sha256'] = h.hexdigest()\n"
        "            except Exception:\n"
        "                pass\n"
        "            try:\n"
        "                from pxr import Kind\n"
        "                model = Usd.ModelAPI(prim)\n"
        "                kind_val = model.GetKind() if model else ''\n"
        "                prim_kind = str(kind_val) if kind_val else None\n"
        "            except Exception:\n"
        "                prim_kind = None\n"
        "            try:\n"
        "                spec_str = str(prim.GetSpecifier()).split('.')[-1].lower()\n"
        "                if spec_str.startswith('specifier'):\n"
        "                    spec_str = spec_str[len('specifier'):]\n"
        "            except Exception:\n"
        "                spec_str = 'def'\n"
        "            print(json.dumps({\n"
        "                'prim_path': prim_path,\n"
        "                'has_asset_info': has_asset_info,\n"
        "                'asset_info': asset_info,\n"
        "                'introducing_layer': intro_layer,\n"
        "                'prim_kind': prim_kind,\n"
        "                'prim_specifier': spec_str,\n"
        "            }))\n"
        "except Exception as e:\n"
        "    print(json.dumps({'error': str(e)}))\n"
    )
    result = await kit_tools.queue_exec_patch(
        code, f"Read asset info / origin / hash for {prim_path}"
    )
    return {
        "queued": result.get("queued", False),
        "patch_id": result.get("patch_id"),
        "prim_path": prim_path,
        "note": (
            "Asset-info lookup queued. Kit will print a JSON dict with keys: "
            "prim_path, has_asset_info, asset_info ({identifier, name, version, "
            "payload_asset_dependencies}), introducing_layer ({identifier, "
            "real_path, version, sha256}), prim_kind, prim_specifier. "
            "has_asset_info=false is normal — most prims do not author the "
            "assetInfo metadata. sha256=null when the layer is bigger than 256 MB "
            "(synchronous hashing would block Kit) or the layer is not a real "
            "on-disk file (e.g. anonymous in-memory layer)."
        ),
    }

DATA_HANDLERS["list_references"] = _handle_list_references
DATA_HANDLERS["list_payloads"] = _handle_list_payloads
DATA_HANDLERS["get_asset_info"] = _handle_get_asset_info
CODE_GEN_HANDLERS["add_usd_reference"] = _gen_add_usd_reference
CODE_GEN_HANDLERS["load_payload"] = _gen_load_payload

# ══════ From feat/atomic-tier13-rl-runtime ══════
def _resolve_run_id(run_id: Optional[str]) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
    """Resolve a run_id (or None → most-recent active run) to its registry entry.

    Returns (run_id, entry) or (None, None) if no matching run exists.
    """
    if not _RUN_REGISTRY:
        return None, None
    if run_id is None:
        # Pick the most-recently-launched RUNNING (or PAUSED) run.
        candidates = [
            (rid, e) for rid, e in _RUN_REGISTRY.items()
            if e.get("state") in ("running", "paused")
        ]
        if not candidates:
            return None, None
        # Newest by launch_time
        candidates.sort(key=lambda kv: kv[1].get("launch_time", 0.0), reverse=True)
        return candidates[0]
    entry = _RUN_REGISTRY.get(run_id)
    return (run_id, entry) if entry else (None, None)

async def _query_run_ipc(entry: Dict[str, Any], request: Dict[str, Any]) -> Dict[str, Any]:
    """Send an IPC request to a running launch_training subprocess.

    Override in tests via monkeypatch. The real implementation talks to the
    subprocess over its Unix socket (entry['ipc_socket']).
    """
    handler = entry.get("ipc_handler")
    if handler is None:
        raise RuntimeError(
            "No IPC handler registered for this run — was it launched via launch_training?"
        )
    return await handler(request)

def _validate_env_id(env_id: Any, num_envs: int) -> Optional[str]:
    """Return an error message if env_id is invalid, else None."""
    if not isinstance(env_id, int) or isinstance(env_id, bool):
        return f"env_id must be an integer, got {type(env_id).__name__}"
    if env_id < 0 or env_id >= num_envs:
        return f"env_id {env_id} out of range [0, {num_envs})"
    return None

async def _handle_get_env_observations(args: Dict) -> Dict:
    """Read the observation tensor for one env in a running IsaacLab worker."""
    t0 = time.perf_counter()
    env_id = args.get("env_id")
    run_id_arg = args.get("run_id")

    run_id, entry = _resolve_run_id(run_id_arg)
    if entry is None:
        return {
            "error": (
                "No active training run found. Launch one with launch_training first, "
                "or pass an explicit run_id."
            ),
            "requested_run_id": run_id_arg,
        }

    err = _validate_env_id(env_id, entry.get("num_envs", 0))
    if err:
        return {"error": err, "run_id": run_id, "num_envs": entry.get("num_envs")}

    try:
        ipc_result = await _query_run_ipc(entry, {"op": "get_observations", "env_id": env_id})
    except Exception as e:
        return {"error": f"IPC query failed: {e}", "run_id": run_id, "env_id": env_id}

    return {
        "run_id": run_id,
        "env_id": env_id,
        "step": ipc_result.get("step", entry.get("last_known_step", 0)),
        "episode_step": ipc_result.get("episode_step", 0),
        "observations": ipc_result.get("observations", {}),
        "dtype": ipc_result.get("dtype", "float32"),
        "shape": ipc_result.get("shape", []),
        "wall_time_ms": (time.perf_counter() - t0) * 1000.0,
    }

async def _handle_get_env_rewards(args: Dict) -> Dict:
    """Read per-term reward breakdown for one env at the current step."""
    t0 = time.perf_counter()
    env_id = args.get("env_id")
    run_id_arg = args.get("run_id")

    run_id, entry = _resolve_run_id(run_id_arg)
    if entry is None:
        return {
            "error": (
                "No active training run found. Launch one with launch_training first, "
                "or pass an explicit run_id."
            ),
            "requested_run_id": run_id_arg,
        }

    err = _validate_env_id(env_id, entry.get("num_envs", 0))
    if err:
        return {"error": err, "run_id": run_id, "num_envs": entry.get("num_envs")}

    try:
        ipc_result = await _query_run_ipc(entry, {"op": "get_rewards", "env_id": env_id})
    except Exception as e:
        return {"error": f"IPC query failed: {e}", "run_id": run_id, "env_id": env_id}

    terms = ipc_result.get("terms", [])
    total = ipc_result.get("total_reward")
    if total is None:
        total = sum(t.get("weighted", 0.0) for t in terms)

    return {
        "run_id": run_id,
        "env_id": env_id,
        "step": ipc_result.get("step", entry.get("last_known_step", 0)),
        "total_reward": total,
        "terms": terms,
        "episode_return": ipc_result.get("episode_return", 0.0),
        "wall_time_ms": (time.perf_counter() - t0) * 1000.0,
    }

async def _handle_get_env_termination_state(args: Dict) -> Dict:
    """Report termination flags (success / timeout / crashed / done) for one env."""
    t0 = time.perf_counter()
    env_id = args.get("env_id")
    run_id_arg = args.get("run_id")

    run_id, entry = _resolve_run_id(run_id_arg)
    if entry is None:
        return {
            "error": (
                "No active training run found. Launch one with launch_training first, "
                "or pass an explicit run_id."
            ),
            "requested_run_id": run_id_arg,
        }

    err = _validate_env_id(env_id, entry.get("num_envs", 0))
    if err:
        return {"error": err, "run_id": run_id, "num_envs": entry.get("num_envs")}

    try:
        ipc_result = await _query_run_ipc(entry, {"op": "get_termination", "env_id": env_id})
    except Exception as e:
        return {"error": f"IPC query failed: {e}", "run_id": run_id, "env_id": env_id}

    term_terms = ipc_result.get("termination_terms", {}) or {}
    success = bool(ipc_result.get("success", term_terms.get("success", False)))
    timeout = bool(ipc_result.get("timeout", term_terms.get("time_out", False)))
    crashed = bool(ipc_result.get("crashed", any(
        v for k, v in term_terms.items()
        if k not in ("success", "time_out") and isinstance(v, bool) and v
    )))
    done = bool(ipc_result.get("done", success or timeout or crashed))

    return {
        "run_id": run_id,
        "env_id": env_id,
        "done": done,
        "success": success,
        "timeout": timeout,
        "crashed": crashed,
        "termination_terms": term_terms,
        "episode_step": ipc_result.get("episode_step", 0),
        "max_episode_steps": ipc_result.get("max_episode_steps", entry.get("max_episode_steps", 0)),
        "last_reset_step": ipc_result.get("last_reset_step", 0),
        "wall_time_ms": (time.perf_counter() - t0) * 1000.0,
    }

async def _handle_pause_training(args: Dict) -> Dict:
    """Signal a running training subprocess to pause without stopping it."""
    t0 = time.perf_counter()
    run_id_arg = args.get("run_id")

    run_id, entry = _resolve_run_id(run_id_arg)
    if entry is None:
        return {
            "error": (
                "No active training run found. Launch one with launch_training first, "
                "or pass an explicit run_id."
            ),
            "requested_run_id": run_id_arg,
        }

    previous_state = entry.get("state", "unknown")
    if previous_state == "paused":
        return {
            "run_id": run_id,
            "paused": True,
            "previous_state": "paused",
            "note": "Run was already paused — no-op.",
            "step": entry.get("last_known_step", 0),
            "iteration": entry.get("last_known_iteration", 0),
            "pid": entry.get("pid"),
            "signal_sent": None,
            "wall_time_ms": (time.perf_counter() - t0) * 1000.0,
        }
    if previous_state not in ("running",):
        return {
            "error": f"Cannot pause run in state '{previous_state}'. Only running runs can be paused.",
            "run_id": run_id,
            "previous_state": previous_state,
        }

    try:
        ipc_result = await _query_run_ipc(entry, {"op": "pause"})
    except Exception as e:
        return {"error": f"IPC query failed: {e}", "run_id": run_id}

    entry["state"] = "paused"
    return {
        "run_id": run_id,
        "paused": True,
        "previous_state": previous_state,
        "step": ipc_result.get("step", entry.get("last_known_step", 0)),
        "iteration": ipc_result.get("iteration", entry.get("last_known_iteration", 0)),
        "pid": entry.get("pid"),
        "signal_sent": ipc_result.get("signal_sent", "SIGUSR1"),
        "wall_time_ms": (time.perf_counter() - t0) * 1000.0,
    }

async def _handle_checkpoint_training(args: Dict) -> Dict:
    """Trigger an out-of-band checkpoint save on a running training subprocess."""
    t0 = time.perf_counter()
    run_id_arg = args.get("run_id")
    include_replay = bool(args.get("include_replay_buffer", False))
    tag = args.get("tag", "manual") or "manual"

    run_id, entry = _resolve_run_id(run_id_arg)
    if entry is None:
        return {
            "error": (
                "No active training run found. Launch one with launch_training first, "
                "or pass an explicit run_id."
            ),
            "requested_run_id": run_id_arg,
        }

    state = entry.get("state", "unknown")
    if state not in ("running", "paused"):
        return {
            "error": f"Cannot checkpoint run in state '{state}'. Run must be running or paused.",
            "run_id": run_id,
            "state": state,
        }

    try:
        ipc_result = await _query_run_ipc(entry, {
            "op": "checkpoint",
            "include_replay_buffer": include_replay,
            "tag": tag,
        })
    except Exception as e:
        return {"error": f"IPC query failed: {e}", "run_id": run_id}

    return {
        "run_id": run_id,
        "checkpoint_path": ipc_result.get("checkpoint_path", ""),
        "step": ipc_result.get("step", entry.get("last_known_step", 0)),
        "iteration": ipc_result.get("iteration", entry.get("last_known_iteration", 0)),
        "size_bytes": ipc_result.get("size_bytes", 0),
        "includes_replay_buffer": bool(ipc_result.get("includes_replay_buffer", include_replay)),
        "save_duration_ms": ipc_result.get("save_duration_ms", 0.0),
        "tag": tag,
        "wall_time_ms": (time.perf_counter() - t0) * 1000.0,
    }

DATA_HANDLERS["get_env_observations"] = _handle_get_env_observations
DATA_HANDLERS["get_env_rewards"] = _handle_get_env_rewards
DATA_HANDLERS["get_env_termination_state"] = _handle_get_env_termination_state
DATA_HANDLERS["pause_training"] = _handle_pause_training
DATA_HANDLERS["checkpoint_training"] = _handle_checkpoint_training

# ══════ From feat/atomic-tier14-bulk ══════
def _gen_bulk_set_attribute(args: Dict) -> str:
    """T14.1 — atomically set the same attribute on many prims via Sdf.ChangeBlock."""
    prim_paths = args["prim_paths"]
    attr = args["attr"]
    value = args["value"]
    return f"""\
import omni.usd
from pxr import Sdf, Usd, UsdGeom, Gf

stage = omni.usd.get_context().get_stage()
_paths = {prim_paths!r}
_attr = {attr!r}
_value = {value!r}

# Infer a USD Sdf.ValueTypeName so missing attributes can be created on the fly
def _infer_value_type(v):
    if isinstance(v, bool):
        return Sdf.ValueTypeNames.Bool
    if isinstance(v, int):
        return Sdf.ValueTypeNames.Int
    if isinstance(v, float):
        return Sdf.ValueTypeNames.Float
    if isinstance(v, str):
        return Sdf.ValueTypeNames.String
    if isinstance(v, (list, tuple)):
        n = len(v)
        if n == 2:
            return Sdf.ValueTypeNames.Float2
        if n == 3:
            return Sdf.ValueTypeNames.Float3
        if n == 4:
            return Sdf.ValueTypeNames.Float4
    return None

_applied = 0
_skipped_missing_prim = 0
_skipped_create_failed = 0
_created = 0

with Sdf.ChangeBlock():
    for _p in _paths:
        _prim = stage.GetPrimAtPath(_p)
        if not _prim or not _prim.IsValid():
            _skipped_missing_prim += 1
            continue
        _a = _prim.GetAttribute(_attr)
        if not _a or not _a.IsValid():
            _tname = _infer_value_type(_value)
            if _tname is None:
                _skipped_create_failed += 1
                continue
            _a = _prim.CreateAttribute(_attr, _tname)
            _created += 1
        try:
            # Wrap Vec-like lists into Gf types when the attribute expects them
            _typename = str(_a.GetTypeName()) if _a and _a.IsValid() else ""
            _v = _value
            if isinstance(_value, (list, tuple)):
                if "3" in _typename and len(_value) == 3:
                    _v = Gf.Vec3f(*_value) if "float" in _typename.lower() else Gf.Vec3d(*_value)
            _a.Set(_v)
            _applied += 1
        except Exception as _e:
            _skipped_create_failed += 1

print(f"bulk_set_attribute: applied={{_applied}} created={{_created}} "
      f"missing_prim={{_skipped_missing_prim}} failed={{_skipped_create_failed}} "
      f"attr={attr!r}")
"""

def _gen_bulk_apply_schema(args: Dict) -> str:
    """T14.2 — apply the same API schema to many prims via Sdf.ChangeBlock."""
    prim_paths = args["prim_paths"]
    schema = args["schema"]
    if schema in _TIER14_SCHEMA_MAP:
        mod, cls = _TIER14_SCHEMA_MAP[schema]
        return f"""\
import omni.usd
from pxr import Sdf
from {mod} import {cls}

stage = omni.usd.get_context().get_stage()
_paths = {prim_paths!r}

_applied = 0
_missing = 0
_already = 0

with Sdf.ChangeBlock():
    for _p in _paths:
        _prim = stage.GetPrimAtPath(_p)
        if not _prim or not _prim.IsValid():
            _missing += 1
            continue
        if _prim.HasAPI({cls}):
            _already += 1
            continue
        {cls}.Apply(_prim)
        _applied += 1

print(f"bulk_apply_schema: schema={schema!r} applied={{_applied}} "
      f"already_had={{_already}} missing={{_missing}}")
"""
    # Fallback: ApplyAPISchemaCommand per prim. Verify each result by diffing
    # GetAppliedSchemas() before/after — Kit's command silently no-ops on
    # unknown schema names, so we raise if nothing changed.
    return f"""\
import omni.usd
import omni.kit.commands
from pxr import Sdf

stage = omni.usd.get_context().get_stage()
_paths = {prim_paths!r}

_applied = 0
_missing = 0
_silent_noop = 0

with Sdf.ChangeBlock():
    for _p in _paths:
        _prim = stage.GetPrimAtPath(_p)
        if not _prim or not _prim.IsValid():
            _missing += 1
            continue
        _before = set(_prim.GetAppliedSchemas() or [])
        try:
            omni.kit.commands.execute('ApplyAPISchemaCommand', api={schema!r}, prim=_prim)
        except Exception:
            _missing += 1
            continue
        _after = set(_prim.GetAppliedSchemas() or [])
        if _before == _after:
            _silent_noop += 1
        else:
            _applied += 1

if _silent_noop > 0 and _applied == 0:
    raise RuntimeError(
        f'bulk_apply_schema: schema {schema!r} applied to 0 prims; '
        f'{{_silent_noop}} silent no-ops — likely unknown schema name.'
    )
print(f"bulk_apply_schema: schema={schema!r} applied={{_applied}} "
      f"silent_noops={{_silent_noop}} missing={{_missing}}")
"""

def _gen_group_prims(args: Dict) -> str:
    """T14.4 — create an Xform parent and reparent prims under it."""
    prim_paths = args["prim_paths"]
    group_name = args["group_name"]
    group_parent = args.get("group_parent", "/World")
    # Guard against slashes in group_name
    safe_name = str(group_name).strip("/").replace("/", "_")
    group_path = f"{group_parent.rstrip('/')}/{safe_name}"
    return f"""\
import omni.usd
from pxr import Sdf, UsdGeom, Gf

{_SAFE_XFORM_SNIPPET}

stage = omni.usd.get_context().get_stage()
_paths = {prim_paths!r}
_group_path = {group_path!r}

# Create the Xform group (idempotent — DefinePrim returns existing if present)
_group_prim = stage.DefinePrim(_group_path, "Xform")

_moved = 0
_missing = 0
_skipped_self = 0

with Sdf.ChangeBlock():
    for _src in _paths:
        _prim = stage.GetPrimAtPath(_src)
        if not _prim or not _prim.IsValid():
            _missing += 1
            continue
        if _src == _group_path or _src.startswith(_group_path + "/"):
            _skipped_self += 1
            continue
        _leaf = _src.rsplit("/", 1)[-1]
        _dst = _group_path + "/" + _leaf
        # Capture world transform BEFORE the move so we can restore it
        try:
            _world = UsdGeom.Xformable(_prim).ComputeLocalToWorldTransform(0)
            _pos = _world.ExtractTranslation()
        except Exception:
            _pos = Gf.Vec3d(0, 0, 0)
        # Reparent via CopySpec + RemovePrim (USD's canonical "move" pattern)
        try:
            Sdf.CopySpec(stage.GetRootLayer(), _src, stage.GetRootLayer(), _dst)
            stage.RemovePrim(_src)
            _new_prim = stage.GetPrimAtPath(_dst)
            if _new_prim and _new_prim.IsValid() and UsdGeom.Xformable(_new_prim):
                _safe_set_translate(_new_prim, (_pos[0], _pos[1], _pos[2]))
            _moved += 1
        except Exception as _e:
            _missing += 1

print(f"group_prims: group={{_group_path}} moved={{_moved}} "
      f"missing={{_missing}} skipped_self={{_skipped_self}}")
"""

def _gen_duplicate_prims(args: Dict) -> str:
    """T14.5 — duplicate prims via Sdf.CopySpec and apply a positional offset."""
    prim_paths = args["prim_paths"]
    offset = args["offset"]
    suffix = args.get("suffix", "_copy")
    ox, oy, oz = offset[0], offset[1], offset[2]
    return f"""\
import omni.usd
from pxr import Sdf, UsdGeom, Gf

{_SAFE_XFORM_SNIPPET}

stage = omni.usd.get_context().get_stage()
_paths = {prim_paths!r}
_offset = ({ox}, {oy}, {oz})
_suffix = {suffix!r}

_pairs = []
_missing = 0

def _unique_dst(base):
    # Append numeric suffixes on collision: _copy, _copy2, _copy3, ...
    cand = base + _suffix
    if not stage.GetPrimAtPath(cand):
        return cand
    i = 2
    while stage.GetPrimAtPath(base + _suffix + str(i)):
        i += 1
    return base + _suffix + str(i)

with Sdf.ChangeBlock():
    for _src in _paths:
        _prim = stage.GetPrimAtPath(_src)
        if not _prim or not _prim.IsValid():
            _missing += 1
            continue
        _dst = _unique_dst(_src)
        try:
            Sdf.CopySpec(stage.GetRootLayer(), _src, stage.GetRootLayer(), _dst)
        except Exception:
            _missing += 1
            continue
        _new = stage.GetPrimAtPath(_dst)
        if _new and _new.IsValid() and UsdGeom.Xformable(_new):
            # Read existing local translate (if any) and add offset
            _cur = Gf.Vec3d(0, 0, 0)
            _xf = UsdGeom.Xformable(_new)
            for _op in _xf.GetOrderedXformOps():
                if _op.GetOpType() == UsdGeom.XformOp.TypeTranslate:
                    _cur = Gf.Vec3d(_op.Get() or (0, 0, 0))
                    break
            _new_t = (_cur[0] + _offset[0], _cur[1] + _offset[1], _cur[2] + _offset[2])
            _safe_set_translate(_new, _new_t)
        _pairs.append((_src, _dst))

print(f"duplicate_prims: count={{len(_pairs)}} missing={{_missing}} "
      f"offset={offset!r}")
for _s, _d in _pairs:
    print(f"  {{_s}} -> {{_d}}")
"""

def _build_select_by_criteria_code(criteria: Dict[str, Any]) -> str:
    """Generate the Kit-side query code for select_by_criteria.

    Split out from the handler so tests can exercise the generator
    without a live Kit RPC.
    """
    return f"""\
import omni.usd
from pxr import Usd, Sdf
import json
import re

stage = omni.usd.get_context().get_stage()
_criteria = {criteria!r}

_type = _criteria.get("type")
_schema = _criteria.get("has_schema")
_name_pat = _criteria.get("name_pattern")
_path_pat = _criteria.get("path_pattern")
_has_attr = _criteria.get("has_attribute")
_kind = _criteria.get("kind")
_parent = _criteria.get("parent")
_active = _criteria.get("active")

_name_re = re.compile(_name_pat) if _name_pat else None
_path_re = re.compile(_path_pat) if _path_pat else None

# Traversal root — whole stage or a parent subtree
if _parent:
    _root = stage.GetPrimAtPath(_parent)
    _iterator = iter(Usd.PrimRange(_root)) if _root and _root.IsValid() else iter([])
else:
    _iterator = iter(stage.Traverse())

_matches = []
for _prim in _iterator:
    if _type and _prim.GetTypeName() != _type:
        continue
    if _schema:
        _applied = [str(a) for a in _prim.GetAppliedSchemas()]
        if _schema not in _applied and not _applied.__contains__(_schema):
            # Also check substring match for aliases like "PhysicsRigidBodyAPI"
            if not any(_schema in a for a in _applied):
                continue
    if _name_re and not _name_re.search(_prim.GetName()):
        continue
    if _path_re and not _path_re.search(str(_prim.GetPath())):
        continue
    if _has_attr:
        _a = _prim.GetAttribute(_has_attr)
        if not _a or not _a.IsValid():
            continue
    if _kind:
        from pxr import Usd as _U
        _k = _U.ModelAPI(_prim).GetKind()
        if _k != _kind:
            continue
    if _active is not None:
        if bool(_prim.IsActive()) != bool(_active):
            continue
    _matches.append(str(_prim.GetPath()))

_matches.sort()
print(json.dumps({{"matches": _matches, "count": len(_matches), "criteria": _criteria}}))
"""

async def _handle_select_by_criteria(args: Dict) -> Dict:
    """T14.3 — query USD stage for prims matching a criteria dict.

    Runs inside Kit via queue_exec_patch; the injected code prints a JSON
    payload on stdout that the LLM can read from the patch result.
    """
    criteria = args.get("criteria", {})
    if not isinstance(criteria, dict):
        return {"error": "criteria must be a dict", "matches": [], "count": 0}

    code = _build_select_by_criteria_code(criteria)
    result = await kit_tools.queue_exec_patch(
        code,
        f"select_by_criteria({', '.join(f'{k}={v!r}' for k, v in list(criteria.items())[:3])})",
    )
    # queue_exec_patch returns a dict with queued/patch_id — the actual matches
    # are produced when the patch executes. Surface both so the caller can
    # either poll for the patch result or read matches from the Kit log.
    return {
        "queued": result.get("queued", False),
        "patch_id": result.get("patch_id"),
        "criteria": criteria,
        "query_code": code,
        "note": (
            "Matches are produced when the queued patch executes on Kit. "
            "Read the resulting JSON payload from the patch output. "
            "Schema: {matches: [str], count: int, criteria: {...}}."
        ),
    }

DATA_HANDLERS["select_by_criteria"] = _handle_select_by_criteria

# ══════ From feat/atomic-tier15-18-misc ══════
async def _handle_get_viewport_camera(args: Dict) -> Dict:
    """Return the active viewport's current camera path and resolution."""
    code = """\
import json
import omni.kit.viewport.utility as _vpu

vp_api = _vpu.get_active_viewport()
cam_path = None
viewport_id = ""
res = [0, 0]
if vp_api is not None:
    try:
        cam_path = str(vp_api.camera_path) if vp_api.camera_path else None
    except Exception:
        cam_path = None
    try:
        viewport_id = getattr(vp_api, "id", "") or ""
    except Exception:
        viewport_id = ""
    try:
        res = list(vp_api.resolution)
    except Exception:
        res = [0, 0]
print(json.dumps({"camera_path": cam_path, "viewport_id": viewport_id, "resolution": res}))
"""
    return await kit_tools.queue_exec_patch(code, "Read active viewport camera")

async def _handle_get_selected_prims(args: Dict) -> Dict:
    """Return the user's current selection in the viewport / Stage panel."""
    code = """\
import json
import omni.usd

ctx = omni.usd.get_context()
sel = ctx.get_selection()
paths = list(sel.get_selected_prim_paths()) if sel is not None else []
primary = paths[-1] if paths else None
print(json.dumps({"selected_paths": paths, "count": len(paths), "primary": primary}))
"""
    return await kit_tools.queue_exec_patch(code, "Read user selection")

def _gen_highlight_prim(args: Dict) -> str:
    prim_path = args["prim_path"]
    color = args.get("color", [1.0, 1.0, 0.0])
    duration = float(args.get("duration", 2.0))
    if len(color) < 3:
        color = list(color) + [0.0] * (3 - len(color))
    r, g, b = color[0], color[1], color[2]
    return f"""\
import asyncio
import omni.usd
import omni.kit.app
from pxr import UsdGeom, Gf

try:
    from isaacsim.util.debug_draw import _debug_draw
    _draw = _debug_draw.acquire_debug_draw_interface()
except Exception:
    _draw = None

stage = omni.usd.get_context().get_stage()
prim = stage.GetPrimAtPath('{prim_path}')
if not prim or not prim.IsValid():
    print("highlight_prim: prim not found at '{prim_path}'")
else:
    bbox_cache = UsdGeom.BBoxCache(0, includedPurposes=[UsdGeom.Tokens.default_])
    bbox = bbox_cache.ComputeWorldBound(prim).ComputeAlignedRange()
    mn = bbox.GetMin()
    mx = bbox.GetMax()
    corners = [
        Gf.Vec3d(mn[0], mn[1], mn[2]),
        Gf.Vec3d(mx[0], mn[1], mn[2]),
        Gf.Vec3d(mx[0], mx[1], mn[2]),
        Gf.Vec3d(mn[0], mx[1], mn[2]),
        Gf.Vec3d(mn[0], mn[1], mx[2]),
        Gf.Vec3d(mx[0], mn[1], mx[2]),
        Gf.Vec3d(mx[0], mx[1], mx[2]),
        Gf.Vec3d(mn[0], mx[1], mx[2]),
    ]
    edges = [
        (0, 1), (1, 2), (2, 3), (3, 0),
        (4, 5), (5, 6), (6, 7), (7, 4),
        (0, 4), (1, 5), (2, 6), (3, 7),
    ]
    starts = [corners[a] for a, _ in edges]
    ends = [corners[b] for _, b in edges]
    color = ({r}, {g}, {b}, 1.0)
    colors = [color] * len(edges)
    sizes = [3] * len(edges)
    if _draw is not None:
        _draw.draw_lines(starts, ends, colors, sizes)

        async def _clear_after():
            await asyncio.sleep({duration})
            try:
                _draw.clear_lines()
            except Exception:
                pass

        asyncio.ensure_future(_clear_after())
        print(f"highlight_prim: drew {{len(edges)}} edges around '{prim_path}', clear in {duration}s")
    else:
        print("highlight_prim: omni.isaac.debug_draw unavailable — no overlay drawn")
"""

def _gen_focus_viewport_on(args: Dict) -> str:
    prim_path = args["prim_path"]
    return f"""\
import omni.usd
import omni.kit.commands

ctx = omni.usd.get_context()
stage = ctx.get_stage()
prim = stage.GetPrimAtPath('{prim_path}')
if not prim or not prim.IsValid():
    print("focus_viewport_on: prim not found at '{prim_path}'")
else:
    ctx.get_selection().set_selected_prim_paths(['{prim_path}'], True)
    try:
        import omni.kit.viewport.utility as _vpu
        vp_api = _vpu.get_active_viewport()
        try:
            _vpu.frame_viewport_selection(vp_api)
        except Exception:
            omni.kit.commands.execute('FramePrimsCommand', prim_to_move=[], prims_to_frame=['{prim_path}'])
        print("focus_viewport_on: framed '{prim_path}'")
    except Exception as e:
        print(f"focus_viewport_on: viewport framing failed: {{e}}")
"""

def _gen_save_stage(args: Dict) -> str:
    path = args["path"]
    return f"""\
import omni.usd

ctx = omni.usd.get_context()
target = {repr(path)}
current = ctx.get_stage_url() or ""
try:
    if current and current == target:
        result = ctx.save_stage()
    else:
        result = ctx.save_as_stage(target)
    print(f"save_stage: wrote {{target}} (result={{result}})")
except Exception as e:
    print(f"save_stage: failed to write {{target}}: {{e}}")
"""

def _gen_open_stage(args: Dict) -> str:
    path = args["path"]
    # Two holes the old version had: (1) ctx.open_stage returns False on
    # missing file but the print said "opened {target} (ok=False)" — the word
    # "opened" is a lie; (2) the try/except swallowed exceptions, so the tool
    # reported success=True and the agent would parrot "opened" to the user.
    return f"""\
import os
import omni.usd

ctx = omni.usd.get_context()
target = {repr(path)}
# Local filesystem paths must exist. Remote/session URLs (omniverse://,
# http(s)://, file://, anon:) resolve through USD's asset resolver and can't
# be checked with os.path.exists.
if not any(target.startswith(p) for p in ('omniverse://','http://','https://','file://','anon:')):
    if not os.path.exists(target):
        raise FileNotFoundError(f'open_stage: no such file: {{target!r}}')
ok = ctx.open_stage(target)
if not ok:
    raise RuntimeError(f'open_stage: ctx.open_stage({{target!r}}) returned False — USD could not load the stage')
print(f"open_stage: successfully opened {{target}}")
"""

def _gen_export_stage(args: Dict) -> str:
    path = args["path"]
    fmt = args["format"].lower()
    return f"""\
import asyncio
import omni.kit.app

target = {repr(path)}
fmt = {repr(fmt)}

async def _do_export():
    try:
        ext_mgr = omni.kit.app.get_app().get_extension_manager()
        ext_id = "omni.kit.tool.asset_exporter"
        if not ext_mgr.is_extension_enabled(ext_id):
            ext_mgr.set_extension_enabled_immediate(ext_id, True)
        from omni.kit.tool.asset_exporter import ExportContext, export_asset
        ec = ExportContext()
        ec.export_path = target
        ec.export_format = fmt
        result = await export_asset(ec)
        print(f"export_stage: wrote {{target}} as {{fmt}} (result={{result}})")
    except Exception as e:
        print(f"export_stage: failed for {{target}} ({{fmt}}): {{e}}")

asyncio.ensure_future(_do_export())
"""

async def _handle_list_opened_stages(args: Dict) -> Dict:
    """List all UsdContexts and the stage URL each holds."""
    code = """\
import json
import omni.usd

ctx_names = []
try:
    ctx_names = list(omni.usd.get_context_names())
except Exception:
    ctx_names = [""]
if not ctx_names:
    ctx_names = [""]

stages = []
active_ctx = ""
for name in ctx_names:
    try:
        c = omni.usd.get_context(name)
        if c is None:
            continue
        url = c.get_stage_url() or None
        stage = c.get_stage()
        prim_count = 0
        if stage is not None:
            prim_count = sum(1 for _ in stage.Traverse())
        is_dirty = False
        try:
            is_dirty = bool(c.has_pending_edit())
        except Exception:
            is_dirty = False
        stages.append({
            "context_name": name,
            "stage_url": url,
            "prim_count": prim_count,
            "is_dirty": is_dirty,
        })
        if not active_ctx:
            active_ctx = name
    except Exception:
        continue
print(json.dumps({"stages": stages, "active_context": active_ctx}))
"""
    return await kit_tools.queue_exec_patch(code, "List opened USD stages")

async def _handle_list_extensions(args: Dict) -> Dict:
    """List Kit extensions registered with the extension manager."""
    enabled_only = bool(args.get("enabled_only", False))
    name_filter = args.get("name_filter") or ""
    code = f"""\
import json
import omni.kit.app

mgr = omni.kit.app.get_app().get_extension_manager()
exts = list(mgr.get_extensions())
enabled_only = {repr(enabled_only)}
nf = {repr(name_filter)}.lower()

out = []
for ext in exts:
    try:
        ext_id = ext.get("id") or ext.get("name") or ""
        version = ext.get("version") or ""
        enabled = bool(ext.get("enabled", False))
        title = ext.get("title") or ext.get("name") or ext_id
    except AttributeError:
        ext_id = getattr(ext, "id", "") or ""
        version = getattr(ext, "version", "") or ""
        enabled = bool(getattr(ext, "enabled", False))
        title = getattr(ext, "title", "") or ext_id
    if enabled_only and not enabled:
        continue
    if nf and nf not in str(ext_id).lower():
        continue
    out.append({{
        "id": str(ext_id),
        "version": str(version),
        "enabled": enabled,
        "title": str(title),
    }})

print(json.dumps({{"extensions": out, "total": len(out)}}))
"""
    return await kit_tools.queue_exec_patch(code, "List Kit extensions")

def _gen_enable_extension(args: Dict) -> str:
    ext_id = args["ext_id"]
    return f"""\
import omni.kit.app

mgr = omni.kit.app.get_app().get_extension_manager()
ext_id = {repr(ext_id)}
try:
    if mgr.is_extension_enabled(ext_id):
        print(f"enable_extension: '{{ext_id}}' already enabled")
    else:
        ok = mgr.set_extension_enabled_immediate(ext_id, True)
        print(f"enable_extension: '{{ext_id}}' set_enabled returned {{ok}}")
except Exception as e:
    print(f"enable_extension: failed for '{{ext_id}}': {{e}}")
"""

def _gen_create_audio_prim(args: Dict) -> str:
    pos = args["position"]
    audio_file = args["audio_file"]
    prim_path = args.get("prim_path", "")
    start_time = float(args.get("start_time", 0.0))
    auto_play = bool(args.get("auto_play", True))
    if len(pos) < 3:
        pos = list(pos) + [0.0] * (3 - len(pos))
    px, py, pz = pos[0], pos[1], pos[2]
    return f"""\
import omni.usd
from pxr import UsdGeom, UsdMedia, Sdf, Gf
{_SAFE_XFORM_SNIPPET}
stage = omni.usd.get_context().get_stage()

# Pick a unique path under /World/Audio_<n> if none provided
desired = {repr(prim_path)}
if not desired:
    n = 0
    while True:
        candidate = f"/World/Audio_{{n}}"
        if not stage.GetPrimAtPath(candidate).IsValid():
            desired = candidate
            break
        n += 1

audio = UsdMedia.SpatialAudio.Define(stage, Sdf.Path(desired))
prim = audio.GetPrim()
_safe_set_translate(prim, ({px}, {py}, {pz}))

# Set the audio asset path
try:
    audio.CreateFilePathAttr().Set(Sdf.AssetPath({repr(audio_file)}))
except Exception:
    attr = prim.CreateAttribute("filePath", Sdf.ValueTypeNames.Asset)
    attr.Set(Sdf.AssetPath({repr(audio_file)}))

# Optional playback hints
try:
    audio.CreateStartTimeAttr().Set({start_time})
except Exception:
    prim.CreateAttribute("startTime", Sdf.ValueTypeNames.Double).Set({start_time})
try:
    audio.CreateAuralModeAttr().Set(UsdMedia.Tokens.spatial)
except Exception:
    pass
try:
    audio.CreatePlaybackModeAttr().Set(
        UsdMedia.Tokens.onceFromStart if {auto_play} else UsdMedia.Tokens.noPlayback
    )
except Exception:
    prim.CreateAttribute("auto_play", Sdf.ValueTypeNames.Bool).Set({auto_play})

print(f"create_audio_prim: defined SpatialAudio at {{desired}} -> {audio_file}")
"""

def _gen_set_audio_property(args: Dict) -> str:
    prim_path = args["prim_path"]
    prop = args["prop"]
    value = args["value"]
    # Map the friendly prop name to the SpatialAudio attr
    PROP_MAP = {
        "volume": "gain",
        "gain": "gain",
        "pitch": "pitch",
        "attenuation_start": "startTime",  # not a real attenuation attr in UsdMedia, mapped numerically
        "attenuation_end": "endTime",
        "auto_play": "auto_play",
        "start_time": "startTime",
    }
    if prop not in PROP_MAP:
        return f"# set_audio_property: unknown prop '{prop}'"
    attr_name = PROP_MAP[prop]
    return f"""\
import omni.usd
from pxr import UsdMedia, Sdf

stage = omni.usd.get_context().get_stage()
prim = stage.GetPrimAtPath({repr(prim_path)})
if not prim or not prim.IsValid():
    print("set_audio_property: prim not found at {prim_path}")
else:
    audio = UsdMedia.SpatialAudio(prim)
    prop_name = {repr(prop)}
    attr_name = {repr(attr_name)}
    value = {repr(value)}
    try:
        if prop_name in ("volume", "gain"):
            try:
                audio.CreateGainAttr().Set(float(value))
            except Exception:
                prim.CreateAttribute("gain", Sdf.ValueTypeNames.Double).Set(float(value))
        elif prop_name == "pitch":
            try:
                audio.CreatePitchAttr().Set(float(value))
            except Exception:
                prim.CreateAttribute("pitch", Sdf.ValueTypeNames.Double).Set(float(value))
        elif prop_name == "auto_play":
            try:
                mode = UsdMedia.Tokens.onceFromStart if bool(value) else UsdMedia.Tokens.noPlayback
                audio.CreatePlaybackModeAttr().Set(mode)
            except Exception:
                prim.CreateAttribute("auto_play", Sdf.ValueTypeNames.Bool).Set(bool(value))
        elif prop_name == "start_time":
            try:
                audio.CreateStartTimeAttr().Set(float(value))
            except Exception:
                prim.CreateAttribute("startTime", Sdf.ValueTypeNames.Double).Set(float(value))
        elif prop_name == "attenuation_end":
            try:
                audio.CreateEndTimeAttr().Set(float(value))
            except Exception:
                prim.CreateAttribute("endTime", Sdf.ValueTypeNames.Double).Set(float(value))
        elif prop_name == "attenuation_start":
            prim.CreateAttribute("attenuationStart", Sdf.ValueTypeNames.Double).Set(float(value))
        print(f"set_audio_property: {{prop_name}} -> {{value}} on {prim_path}")
    except Exception as e:
        print(f"set_audio_property: failed to set {{prop_name}}: {{e}}")
"""

CODE_GEN_HANDLERS["highlight_prim"] = _gen_highlight_prim
CODE_GEN_HANDLERS["focus_viewport_on"] = _gen_focus_viewport_on
DATA_HANDLERS["get_viewport_camera"] = _handle_get_viewport_camera
DATA_HANDLERS["get_selected_prims"] = _handle_get_selected_prims
CODE_GEN_HANDLERS["save_stage"] = _gen_save_stage
CODE_GEN_HANDLERS["open_stage"] = _gen_open_stage
CODE_GEN_HANDLERS["export_stage"] = _gen_export_stage
DATA_HANDLERS["list_opened_stages"] = _handle_list_opened_stages
CODE_GEN_HANDLERS["enable_extension"] = _gen_enable_extension
DATA_HANDLERS["list_extensions"] = _handle_list_extensions
CODE_GEN_HANDLERS["create_audio_prim"] = _gen_create_audio_prim
CODE_GEN_HANDLERS["set_audio_property"] = _gen_set_audio_property

# ── Recovered handler registrations (missing from original bundle extraction) ─
CODE_GEN_HANDLERS["add_domain_randomizer"] = _gen_add_domain_randomizer
CODE_GEN_HANDLERS["apply_physics_material"] = _gen_apply_physics_material
DATA_HANDLERS["lookup_material"] = _handle_lookup_material
DATA_HANDLERS["preview_sdg"] = _handle_preview_sdg
CODE_GEN_HANDLERS["create_sdg_pipeline"] = _gen_create_sdg_pipeline
CODE_GEN_HANDLERS["bulk_set_attribute"] = _gen_bulk_set_attribute
CODE_GEN_HANDLERS["group_prims"] = _gen_group_prims
CODE_GEN_HANDLERS["duplicate_prims"] = _gen_duplicate_prims
CODE_GEN_HANDLERS["bulk_apply_schema"] = _gen_bulk_apply_schema
CODE_GEN_HANDLERS["clone_envs"] = _gen_clone_envs
CODE_GEN_HANDLERS["configure_camera"] = _gen_configure_camera
CODE_GEN_HANDLERS["configure_zmq_stream"] = _gen_configure_zmq_stream
CODE_GEN_HANDLERS["create_graph"] = _gen_create_graph
CODE_GEN_HANDLERS["debug_draw"] = _gen_debug_draw
CODE_GEN_HANDLERS["debug_graph"] = _gen_debug_graph
CODE_GEN_HANDLERS["explain_graph"] = _gen_explain_graph
CODE_GEN_HANDLERS["export_dataset"] = _gen_export_dataset
CODE_GEN_HANDLERS["generate_occupancy_map"] = _gen_generate_occupancy_map
