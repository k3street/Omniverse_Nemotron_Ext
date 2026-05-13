"""Cross-handler shared utilities.

Phase 8 contract per spec — this module is the *future* home for the
twelve cross-handler utilities currently living at module-level inside
`tool_executor.py`. As each theme moves out of the monolith (Phases
3-7), the utilities its handlers depended on move here.

The Phase 2b cross-reference audit identified ten high-fan-in
utilities (each called by ≥3 handlers — `docs/audits/handler_cross_refs.md`
for the live list):

  execute_tool_call          — dispatcher entry, stays in tool_executor
                                (special case — not migrated here)
  _get_viewport_bytes        — viewport capture helper (vision theme)
  _get_vision_provider       — vision-LLM provider selector
  _query_run_ipc             — training IPC helper
  _resolve_run_id            — training run-id resolver
  _check_real_data_path      — finetune / sim data validator
  _wf_now_iso                — workflow timestamp helper
  _parse_last_json_line      — subprocess output parser
  _safe_robot_name           — USD-path sanitiser
  _validate_env_id           — IsaacLab env-id validator

Phase 8's "Files (changes)" — the import-swap from `tool_executor`
globals to `handlers._shared` — requires the themed modules to actually
contain handler code first. That's Phases 3-7. Until then, this module
is a documented re-export façade: themed modules can already
`from ._shared import _safe_robot_name` and the import resolves to the
existing implementation in `tool_executor.py`, so no behaviour change.

Once Phase 3-7 land, the re-exports in this file are replaced by the
moved function bodies; tool_executor.py loses those module-level
definitions; nothing in the consumer themed modules has to change
(their imports already point here).

Per `specs/IA_FULL_SPEC_2026-05-10.md` Phase 8.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Type-only re-exports for theme handlers that want signatures
    # without circular-import risk.
    pass


# ---------------------------------------------------------------------------
# Read-only cross-handler constants (Phase 8 §Risk mitigation: keep
# read-only constants here, NOT in `_state.py`'s mutable dataclasses).
# Populated as Phase 3-7 lift them out of tool_executor.py.

# Phase 8 wave 3 (2026-05-13): _SAFE_XFORM_SNIPPET migrated from
# tool_executor.py:1596. Used by animation/sensors/scene_blueprints/
# scene_authoring code generators to inject safe transform setters.
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

# Phase 8 wave 5 (2026-05-13): _OG_NODE_TYPE_MAP migrated from
# tool_executor.py:1900. Used by scene_authoring + ros2 OmniGraph
# code generators to remap legacy omni.isaac.* → isaacsim.* node types.
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


# Phase 8 wave 5: _open_hdf5_safely migrated from tool_executor.py:3808.
# Used by teleop (validate demo) + diagnostics (check teleop hardware).
def _open_hdf5_safely(path: str):
    """Return (h5py_File, None) or (None, reason_str). Never raises."""
    from pathlib import Path
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



# Phase 8 wave 8 (2026-05-13): cross-theme symbols used by resolve + robot.

_ROBOT_WIZARD_REGISTRY = {
    "franka_panda": {
        "rel_path": "Isaac/Robots/FrankaRobotics/FrankaPanda/franka.usd",
        "cloud_url": "https://omniverse-content-production.s3-us-west-2.amazonaws.com/Assets/Isaac/5.1/Isaac/Robots/FrankaRobotics/FrankaPanda/franka.usd",
        "robot_type": "manipulator",
        # Franka-specific profile — overrides generic `manipulator` defaults.
        # Added after 2026-04-21 conveyor_pick_place debugging; generic
        # kp=1000/kd=100 is too weak for Franka position control, and the
        # default Gripper variant doesn't render fingers in some Kit
        # builds. Full profile lets `robot_wizard` configure an
        # out-of-the-box working Franka without downstream patches.
        "drive_stiffness": 6000,
        "drive_damping": 500,
        "variants": {"Gripper": "AlternateFinger"},
        "home_joints": [0.0, -0.785, 0.0, -2.356, 0.0, 1.571, 0.785, 0.04, 0.04],
        "ee_link": "panda_hand",
        "gripper_joints": ["panda_finger_joint1", "panda_finger_joint2"],
        "gripper_open": 0.04,
        "gripper_close": 0.0,
        "rmpflow_config_rel": "motion_policy_configs/franka/rmpflow/franka_rmpflow_common.yaml",
    },
    # Aliases
    "franka": "franka_panda",
    "panda": "franka_panda",
    "franka_emika_panda": "franka_panda",

    # Additional robots — minimal entries (cloud URL only, no fine-grained
    # profile). Sufficient for robot_wizard / resolve_robot_class pipelines:
    # the wizard's `if registry_hit:` branch fills in defaults from the
    # generic robot_type table when a profile isn't specified. Cloud URLs
    # follow Isaac Sim 5.1 conventions; if a specific path drifts, edit
    # here once rather than every caller.
    # Paths verified against the user's local 5.0 asset bundle on
    # /mnt/shared_data/isaac-sim-assets-complete-5.0.0; cloud_url uses
    # the same relative path against the public S3 prefix. _resolve_robot_asset
    # prefers local-disk lookup when ASSETS_ROOT_PATH is set, falling back
    # to the cloud URL when it isn't.
    "h1": {
        "rel_path": "Isaac/Robots/Unitree/H1/h1.usd",
        "cloud_url": "https://omniverse-content-production.s3-us-west-2.amazonaws.com/Assets/Isaac/5.1/Isaac/Robots/Unitree/H1/h1.usd",
        "robot_type": "humanoid",
    },
    "g1": {
        "rel_path": "Isaac/Robots/Unitree/G1/g1.usd",
        "cloud_url": "https://omniverse-content-production.s3-us-west-2.amazonaws.com/Assets/Isaac/5.1/Isaac/Robots/Unitree/G1/g1.usd",
        "robot_type": "humanoid",
    },
    "spot": {
        "rel_path": "Isaac/Robots/BostonDynamics/spot/spot.usd",
        "cloud_url": "https://omniverse-content-production.s3-us-west-2.amazonaws.com/Assets/Isaac/5.1/Isaac/Robots/BostonDynamics/spot/spot.usd",
        "robot_type": "mobile",
    },
    "anymal_c": {
        "rel_path": "Isaac/Robots/ANYbotics/anymal_c/anymal_c.usd",
        "cloud_url": "https://omniverse-content-production.s3-us-west-2.amazonaws.com/Assets/Isaac/5.1/Isaac/Robots/ANYbotics/anymal_c/anymal_c.usd",
        "robot_type": "mobile",
    },
    "nova_carter": {
        "rel_path": "Isaac/Robots/NVIDIA/NovaCarter/nova_carter.usd",
        "cloud_url": "https://omniverse-content-production.s3-us-west-2.amazonaws.com/Assets/Isaac/5.1/Isaac/Robots/NVIDIA/NovaCarter/nova_carter.usd",
        "robot_type": "mobile",
    },
    "ur10e": {
        "rel_path": "Isaac/Robots/UniversalRobots/ur10e/ur10e.usd",
        "cloud_url": "https://omniverse-content-production.s3-us-west-2.amazonaws.com/Assets/Isaac/5.1/Isaac/Robots/UniversalRobots/ur10e/ur10e.usd",
        "robot_type": "manipulator",
    },
    "allegro": {
        "rel_path": "Isaac/Robots/WonikRobotics/AllegroHand/allegro_hand.usd",
        "cloud_url": "https://omniverse-content-production.s3-us-west-2.amazonaws.com/Assets/Isaac/5.1/Isaac/Robots/WonikRobotics/AllegroHand/allegro_hand.usd",
        "robot_type": "manipulator",
    },
    # Aliases for the robots above
    "unitree_h1": "h1",
    "unitree_g1": "g1",
    "carter": "nova_carter",
    "ur10": "ur10e",
}

def _resolve_robot_asset(entry: Dict) -> str:
    """Return the best asset path for a robot registry entry.

    Prefers local disk (ASSETS_ROOT_PATH + rel_path) when present, since
    USD's asset resolver is ~50-100× faster off disk than over HTTPS and
    doesn't depend on internet. Falls back to the cloud URL otherwise.
    """
    import os as _os
    assets_root = _os.environ.get("ASSETS_ROOT_PATH", "").rstrip("/")
    if assets_root and entry.get("rel_path"):
        local = f"{assets_root}/{entry['rel_path']}"
        if _os.path.exists(local):
            return local
    return entry.get("cloud_url", "")

# from: feat/addendum-phase8F-ros2-quality
# _ROS2_QOS_PRESETS migrated to handlers/ros2.py (Phase 8 wave 4, 2026-05-13).

# from: feat/atomic-tier13-rl-runtime
_RUN_REGISTRY: Dict[str, Dict[str, Any]] = {}

# from: feat/new-quick-demo-builder-v2
# _SCENE_STYLE_PRESETS migrated to handlers/vision.py (Phase 8 wave 4, 2026-05-13).

# from: feat/6A-physx-validation
# _SCENE_TEMPLATES migrated to handlers/scene_blueprints.py (Phase 8 wave 7, 2026-05-13).

# from: feat/new-onboarding

CONSTANTS: dict[str, object] = {
    "SAFE_XFORM_SNIPPET": _SAFE_XFORM_SNIPPET,
    "OG_NODE_TYPE_MAP": _OG_NODE_TYPE_MAP,
}


# ---------------------------------------------------------------------------
# Utility surface contract — names that consumer themed modules expect
# to import. Until Phase 3-7 actually move the implementations,
# `_resolve_from_legacy` is the bridge: it pulls the name out of
# `tool_executor.py`'s module namespace and re-exports it under
# `_shared.<name>`.
#
# This is a deliberate bridge — when a theme like handlers/training.py
# does `from ._shared import _resolve_run_id`, the import resolves
# transparently regardless of whether the function has moved yet.

_LEGACY_REEXPORT_NAMES: tuple[str, ...] = (
    "_get_viewport_bytes",
    "_get_vision_provider",
    "_query_run_ipc",
    "_resolve_run_id",
    "_check_real_data_path",
    "_wf_now_iso",
    "_parse_last_json_line",
    "_safe_robot_name",
    "_validate_env_id",
)


def _resolve_from_legacy(name: str):
    """Pull `name` from `tool_executor.py`'s module namespace.

    Used by __getattr__ below to provide transparent re-exports for any
    high-fan-in utility that hasn't been physically moved out of the
    monolith yet.
    """
    # Lazy import — avoid pulling tool_executor at module load (it's
    # 35k lines and loading it eagerly would change import-time cost
    # for any _shared consumer that doesn't actually use the legacy
    # utilities).
    from .. import tool_executor as _te  # type: ignore[no-redef]

    return getattr(_te, name)


def __getattr__(name: str):  # PEP 562 module-level __getattr__
    """Lazy re-export for legacy-named utilities.

    Phase 3-7 will replace this with direct function definitions in
    this module body. Until then, `from ._shared import <name>` for any
    name in `_LEGACY_REEXPORT_NAMES` resolves to the corresponding
    function in `tool_executor.py`.
    """
    if name in _LEGACY_REEXPORT_NAMES:
        return _resolve_from_legacy(name)
    raise AttributeError(
        f"module 'service.isaac_assist_service.chat.tools.handlers._shared' "
        f"has no attribute {name!r}. If this is a future-Phase-3-7 utility "
        f"that should be re-exported, add it to _LEGACY_REEXPORT_NAMES."
    )


__all__ = [
    "CONSTANTS",
    "_SAFE_XFORM_SNIPPET",
    "_OG_NODE_TYPE_MAP",
    "_open_hdf5_safely",
    "_ROBOT_WIZARD_REGISTRY",
    "_resolve_robot_asset",
    # Plus the lazy-imported names; importers see them via __getattr__.
    *_LEGACY_REEXPORT_NAMES,
]
