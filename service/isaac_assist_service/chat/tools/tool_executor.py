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
from typing import Any, Awaitable, Callable, Dict, List, Optional
from . import kit_tools
from .patch_validator import validate_patch, format_issues_for_llm, has_blocking_issues
from ...config import config

logger = logging.getLogger(__name__)

# ── Paths to knowledge files ─────────────────────────────────────────────────
_WORKSPACE = Path(__file__).resolve().parents[4] / "workspace"
# _SENSOR_SPECS_PATH migrated to handlers/scene_blueprints.py (Phase 8 wave 7, 2026-05-13).
# _DEFORMABLE_PRESETS_PATH migrated to handlers/physics.py (Phase 8 wave 6).

# Cache loaded once
# _sensor_specs migrated to handlers/scene_blueprints.py (Phase 8 wave 7, 2026-05-13).
# _deformable_presets migrated to handlers/physics.py (Phase 8 wave 6).

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
# _ARENA_SCENE_MAP migrated to handlers/arena.py (Phase 8 wave 1, 2026-05-13).

# from: feat/addendum-community-remote-v2
_ASYNC_TASKS: Dict[str, Dict[str, Any]] = {}

# from: feat/addendum-community-remote-v2
_ASYNC_TASKS_LOCK = _threading.Lock()

# from: feat/addendum-phase5-pedagogy-uncertainty-v2
# _BROKEN_SCENE_FAULTS migrated to handlers/diagnostics.py (Phase 8 wave 10, 2026-05-13).

# from: feat/7H-cloud-deployment
_cloud_jobs: Dict[str, Dict] = {}

# from: feat/7H-cloud-deployment
# _CLOUD_PRICING migrated to handlers/training.py (Phase 8 wave 12, 2026-05-13).

# from: feat/7H-cloud-deployment
# _CLOUD_SCRIPT_ALLOWLIST migrated to handlers/training.py (Phase 8 wave 12, 2026-05-13).

# from: feat/new-physics-calibration
# _DEFAULT_CALIBRATE_PARAMS migrated to handlers/robot.py (Phase 8 wave 11, 2026-05-13).

# from: feat/new-onboarding
# _DEFAULT_SUGGESTIONS migrated to handlers/workflow.py (Phase 8 wave 13, 2026-05-13).

# from: feat/addendum-enterprise-scale
# _DELTA_ROOT migrated to handlers/scene_authoring.py (Phase 8 wave 12, 2026-05-13).

# from: feat/7C-xr-teleoperation
# _DEVICE_AXIS_DEFAULTS migrated to handlers/teleop.py (Phase 8 wave 4, 2026-05-13).

# from: feat/addendum-phase7A-rl-debugging
# _DOMINANT_TERM_THRESHOLD migrated to handlers/training.py (Phase 8 wave 12, 2026-05-13).

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
# _DR_RANGE_HINTS migrated to handlers/robot.py (Phase 8 wave 15, 2026-05-13).

# from: feat/addendum-dr-advanced
# _DR_ROBOT_HINTS migrated to handlers/training.py (Phase 8 wave 15, 2026-05-13).

# from: feat/addendum-dr-advanced
# _DR_TASK_DEFAULTS migrated to handlers/training.py (Phase 8 wave 15, 2026-05-13).

# from: feat/7E-eureka-rewards
_eureka_runs: Dict[str, Dict] = {}

# from: feat/addendum-phase7G-groot-tooling-v2
# _EXPORT_TARGETS migrated to handlers/training.py (Phase 8 wave 12, 2026-05-13).

# from: feat/addendum-phase7G-groot-tooling-v2
# _FINETUNE_FREEZE_PROFILES migrated to handlers/training.py (Phase 8 wave 13, 2026-05-13).

# from: feat/addendum-phase3-urdf-postprocessor
# _FIX_PROFILE_PATTERNS migrated to handlers/robot.py (Phase 8 wave 13, 2026-05-13).

# from: feat/7G-groot-n1
# _GROOT_EMBODIMENTS migrated to handlers/training.py (Phase 8 wave 13, 2026-05-13).

# from: feat/addendum-community-remote-v2
# _ISAA_MANIFEST_VERSION migrated to handlers/scene_blueprints.py (Phase 8 wave 7, 2026-05-13).

# from: feat/atomic-tier6-lighting
# _LIGHT_TYPE_NAMES migrated to handlers/vision.py (Phase 8 wave 4, 2026-05-13).

# from: feat/new-onboarding
# _MOBILE_ROBOT_KEYWORDS migrated to handlers/workflow.py (Phase 8 wave 13, 2026-05-13).

# from: feat/addendum-ros2-nav2
# _NAV2_BRIDGE_PROFILES migrated to handlers/ros2.py (Phase 8 wave 4, 2026-05-13).

# from: feat/new-omnigraph-assistant
# _OG_TEMPLATES migrated to handlers/scene_authoring.py (Phase 8 wave 12, 2026-05-13).

# _PHYSICS_MATERIALS_PATH + _physics_materials migrated to handlers/physics.py (Phase 8 wave 6).

# from: feat/new-auto-simplification
# _PHYSICS_SETTINGS_PRESETS migrated to handlers/physics.py (Phase 8 wave 6, 2026-05-13).

# from: feat/addendum-phase2-smart-debugging
# _PHYSX_ERROR_PATTERNS migrated to handlers/diagnostics.py (Phase 8 wave 10, 2026-05-13).

# from: feat/6A-physx-validation
# _PHYSX_ERROR_RE migrated to handlers/physics.py (Phase 8 wave 6, 2026-05-13).

# from: feat/addendum-collision-mesh-quality-v2
# _PHYSX_HULL_MAX_POLYS migrated to handlers/physics.py (Phase 8 wave 6, 2026-05-13).

# from: feat/addendum-collision-mesh-quality-v2
# _PHYSX_HULL_MAX_VERTS migrated to handlers/physics.py (Phase 8 wave 6, 2026-05-13).

# from: feat/atomic-tier8-render
# _POST_PROCESS_PATHS migrated to handlers/rendering.py (Phase 8 wave 2, 2026-05-13).

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
# _QUICK_CALIBRATE_PARAMS migrated to handlers/robot.py (Phase 8 wave 11, 2026-05-13).

# from: feat/new-quick-demo-builder-v2
# _QUICK_DEMO_TEMPLATES migrated to handlers/vision.py (Phase 8 wave 4, 2026-05-13).

# from: feat/addendum-community-remote-v2
# _RENDER_QUALITY_PRESETS migrated to handlers/vision.py (Phase 8 wave 4, 2026-05-13).

# from: feat/addendum-phase7A-rl-debugging
# _REWARD_HACK_PATTERNS migrated to handlers/training.py (Phase 8 wave 12, 2026-05-13).

# from: feat/addendum-phase3-urdf-postprocessor
# _ROBOT_FIX_PROFILES migrated to handlers/robot.py (Phase 8 wave 13, 2026-05-13).

# from: feat/addendum-phase3-urdf-postprocessor
_ROBOT_NAME_PATTERNS = {
    "franka": ["franka", "panda"],
    "ur10": ["ur10"],
    "ur5": ["ur5"],
    "ur5e": ["ur5e"],
    "cobotta": ["cobotta"],
}

# from: feat/8D-robot-setup
# _ROBOT_TYPE_DEFAULTS migrated to handlers/robot.py (Phase 8 wave 13, 2026-05-13).

# Named-robot registry for robot_wizard — maps a known name to the
# canonical RELATIVE path under the Isaac asset root (5.x layout).
# robot_wizard resolves to a local disk path when ASSETS_ROOT_PATH is
# set and the file exists (faster, offline-capable), otherwise falls
# back to the cloud HTTPS URL.
#
# Relationship to _CATALOG_ROBOTS (module-level, used by catalog_search):
# _CATALOG_ROBOTS is a flat filename map assuming Collected_Robots/*.usd
# layout. That layout is WRONG for 5.x — Franka actually lives at
# Isaac/Robots/FrankaRobotics/FrankaPanda/franka.usd. This registry is
# the authoritative import source; _CATALOG_ROBOTS just drives search.
# _ROBOT_WIZARD_REGISTRY migrated to handlers/_shared.py (Phase 8 wave 8, 2026-05-13).


# _resolve_robot_asset migrated to handlers/_shared.py (Phase 8 wave 8, 2026-05-13).
# _SLASH_COMMANDS migrated to handlers/workflow.py (Phase 8 wave 13, 2026-05-13).

# from: feat/addendum-enterprise-scale
_STAGE_INDEX: Dict[str, Dict[str, Any]] = {}

# from: feat/addendum-enterprise-scale
_STAGE_INDEX_META: Dict[str, Any] = {"prim_scope": None, "prim_count": 0}

# from: feat/new-onboarding
# _STARTER_PROMPTS migrated to handlers/workflow.py (Phase 8 wave 13, 2026-05-13).

# from: feat/7C-xr-teleoperation
# _STREAM_QUALITY_PRESETS migrated to handlers/teleop.py (Phase 8 wave 4, 2026-05-13).

# from: feat/new-onboarding
# _SUGGESTION_MAP migrated to handlers/workflow.py (Phase 8 wave 13, 2026-05-13).

# from: feat/8B-motion-planning-complete
# _SUPPORTED_MOTION_ROBOTS migrated to handlers/robot.py (Phase 8 wave 11, 2026-05-13).

# from: feat/addendum-phase7C-teleop-quality
# _TELEOP_DEVICES migrated to handlers/diagnostics.py (Phase 8 wave 10, 2026-05-13).

# from: feat/addendum-community-remote-v2
# _TEMPLATE_EXPORT_DIR migrated to handlers/scene_blueprints.py (Phase 8 wave 7, 2026-05-13).

# from: feat/new-omnigraph-assistant
# _TEMPLATE_KEYWORDS migrated to handlers/scene_authoring.py (Phase 8 wave 14, 2026-05-13).

# from: feat/addendum-community-remote-v2
# _TEMPLATE_LIBRARY_DIR migrated to handlers/scene_blueprints.py (Phase 8 wave 7, 2026-05-13).

# from: feat/atomic-tier12-asset-mgmt
# _TIER12_HELPERS migrated to handlers/scene_authoring.py (Phase 8 wave 12, 2026-05-13).

# from: feat/atomic-tier14-bulk
# _TIER14_SCHEMA_MAP migrated to handlers/scene_authoring.py (Phase 8 wave 12, 2026-05-13).

# from: feat/new-physics-calibration
# _VALID_CALIBRATE_PARAMS migrated to handlers/robot.py (Phase 8 wave 11, 2026-05-13).

# from: feat/addendum-community-remote-v2
# _VRAM_PER_ENV_MB migrated to handlers/diagnostics.py (Phase 8 wave 10, 2026-05-13).

# from: feat/addendum-humanoid-advanced
# _WHOLE_BODY_PROFILES migrated to handlers/robot.py (Phase 8 wave 11, 2026-05-13).

# from: feat/phase10-autonomous-workflows
# _WORKFLOW_RETRY_HARD_CAP migrated to handlers/workflow.py (Phase 8 wave 13, 2026-05-13).

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


# _load_sensor_specs migrated to handlers/scene_blueprints.py (Phase 8 wave 7, 2026-05-13).


# ── Safe xform helper (inlined into generated code) ─────────────────────────
# Referenced USD assets (e.g. robots) often already have xform ops.
# Calling AddTranslateOp() again crashes with "Error in AddXformOp".
# _SAFE_XFORM_SNIPPET migrated to handlers/_shared.py (Phase 8 wave 3, 2026-05-13).
# Cross-handler constant; 9 import sites across 5 themes now use:
#   from ._shared import _SAFE_XFORM_SNIPPET


# ── Code generation helpers ──────────────────────────────────────────────────

from .handlers.arena import (  # noqa: E402
    _gen_create_arena,
    _gen_create_arena_variant,
    _gen_run_arena_benchmark,
    _handle_arena_leaderboard,          # Phase 7 wave 16
)
from .handlers.animation import (  # noqa: E402
    _gen_create_audio_prim,
    _gen_play_animation,
    _gen_set_audio_property,
    _gen_set_keyframe,
    _gen_set_timeline_range,
)
# Phase 3 wave 1 — these three code generators have moved to
# handlers/scene_authoring.py. Names are re-imported here so the
# existing CODE_GEN_HANDLERS dispatch lines (e.g.
# `CODE_GEN_HANDLERS["create_prim"] = _gen_create_prim` further down
# in this file) keep working unchanged. Phase 9 swaps the dispatch
# pattern to a `register()`-based registration and the legacy inline
# assignments go away.
from .handlers.scene_authoring import (  # noqa: E402
    _gen_add_node,                # Phase 6 wave 18
    _gen_add_reference,
    _gen_add_sublayer,            # Phase 6 wave 16
    _gen_add_usd_reference,       # Phase 6 wave 16
    _gen_activate_area,           # Phase 6 wave 23
    _gen_apply_api_schema,
    _gen_assign_class_to_children,  # Phase 6 wave 21
    _gen_assign_material,
    _gen_batch_apply_operation,
    _gen_batch_delete_prims,
    _gen_batch_set_attributes,
    _gen_bulk_apply_schema,       # Phase 6 wave 18
    _gen_bulk_set_attribute,      # Phase 6 wave 18
    _gen_clone_prim,
    _gen_connect_nodes,           # Phase 6 wave 18
    _gen_create_graph,            # Phase 6 wave 23
    _gen_create_material,
    _gen_create_omnigraph,
    _gen_create_prim,
    _gen_delete_node,             # Phase 6 wave 18
    _gen_delete_prim,
    _gen_duplicate_prims,         # Phase 6 wave 18
    _gen_explain_graph,           # Phase 6 wave 23
    _gen_export_stage,            # Phase 6 wave 16
    _gen_flatten_layers,          # Phase 6 wave 16
    _gen_group_prims,             # Phase 6 wave 18
    _gen_load_payload,            # Phase 6 wave 16
    _gen_merge_meshes,            # Phase 6 wave 23
    _gen_open_stage,              # Phase 6 wave 16
    _gen_optimize_scene,
    _gen_remove_semantic_label,     # Phase 6 wave 21
    _gen_restore_delta_snapshot,
    _gen_save_delta_snapshot,
    _gen_save_stage,              # Phase 6 wave 16
    _gen_scatter_on_surface,
    _gen_set_attribute,
    _gen_set_edit_target,         # Phase 6 wave 16
    _gen_set_graph_variable,      # Phase 6 wave 18
    _gen_set_prim_metadata,       # Phase 6 wave 21
    _gen_set_variant,             # Phase 6 wave 21
    _gen_teleport_prim,
    _handle_build_stage_index,    # Phase 7 wave 4
    _handle_compute_stack_placement,  # Phase 7 wave 15
    _handle_compute_surface_area,  # Phase 7 wave 15
    _handle_compute_volume,       # Phase 7 wave 15
    _handle_count_prims_under_path,  # Phase 7 wave 4
    _handle_find_heavy_prims,     # Phase 7 wave 15
    _handle_find_prims_by_name,   # Phase 7 wave 3
    _handle_find_prims_by_schema,  # Phase 7 wave 3
    _handle_get_asset_info,       # Phase 7 wave 3
    _handle_get_attribute,        # Phase 7 wave 3
    _handle_get_bounding_box,     # Phase 7 wave 3
    _handle_get_kind,             # Phase 7 wave 3
    _handle_get_prim_metadata,    # Phase 7 wave 3
    _handle_get_prim_type,        # Phase 7 wave 3
    _handle_get_selected_prims,   # Phase 7 wave 3
    _handle_get_semantic_label,   # Phase 7 wave 3
    _handle_get_world_transform,  # Phase 7 wave 3
    _handle_inspect_graph,        # Phase 7 wave 15
    _handle_list_all_prims,       # Phase 7 wave 3
    _handle_list_applied_schemas,  # Phase 7 wave 3
    _handle_list_attributes,      # Phase 7 wave 3
    _handle_list_graphs,          # Phase 7 wave 15
    _handle_list_layers,          # Phase 7 wave 4
    _handle_list_opened_stages,   # Phase 7 wave 4
    _handle_list_payloads,        # Phase 7 wave 4
    _handle_list_references,      # Phase 7 wave 4
    _handle_list_relationships,   # Phase 7 wave 4
    _handle_list_semantic_classes,  # Phase 7 wave 4
    _handle_list_variant_sets,    # Phase 7 wave 4
    _handle_list_variants,        # Phase 7 wave 4
    _handle_prim_exists,          # Phase 7 wave 3
    _handle_query_stage_index,    # Phase 7 wave 4
    _handle_restore_delta_snapshot,  # Phase 7 wave 15
    _handle_run_stage_analysis,   # Phase 7 wave 4
    _handle_save_delta_snapshot,  # Phase 7 wave 15
    _handle_scene_diff,           # Phase 7 wave 4
    _handle_scene_summary,        # Phase 7 wave 4
    _handle_select_by_criteria,   # Phase 7 wave 4
)
from .handlers.scene_blueprints import (  # noqa: E402
    _gen_build_scene_from_blueprint,
    _gen_export_template,
    _gen_import_template,
    _gen_load_scene_template,
    _handle_catalog_search,
    _handle_download_asset,
    _handle_export_scene_package,
    _handle_filter_templates_by_hardware,
    _handle_generate_scene_blueprint,
    _handle_list_local_files,
    _handle_list_scene_templates,
    _handle_load_scene_template,
    _handle_lookup_api_deprecation,
    _handle_lookup_knowledge,
    _handle_lookup_product_spec,
    _handle_nucleus_browse,
)
from .handlers.sensors import (  # noqa: E402
    _gen_add_proximity_sensor,
    _gen_add_sensor,
    _gen_configure_camera,
    _gen_inspect_camera,
    _gen_set_camera_look_at,
    _gen_set_camera_params,
    _handle_add_force_torque_sensor,       # Phase 7 wave 9
    _handle_add_vision_classifier_gate,    # Phase 7 wave 9
    _handle_barcode_reader_sensor,         # Phase 7 wave 9
    _handle_list_contacts,                 # Phase 7 wave 9
    _handle_nir_material_sensor,           # Phase 7 wave 9
    _handle_overlap_box,                   # Phase 7 wave 9
    _handle_overlap_sphere,                # Phase 7 wave 9
    _handle_raycast,                       # Phase 7 wave 9
    _handle_sweep_sphere,                  # Phase 7 wave 9
)
from .handlers.physics import (  # noqa: E402
    _gen_apply_force,
    _gen_apply_physics_material,
    _gen_check_collision_mesh_code,
    _gen_compute_convex_hull,       # Phase 6 wave 22
    _gen_configure_self_collision,
    _gen_deformable,
    _gen_deformable_body,
    _gen_deformable_surface,
    _gen_fix_collision_mesh,
    _gen_optimize_collision,
    _gen_set_drive_gains,
    _gen_set_joint_limits,
    _gen_set_joint_targets,
    _gen_set_joint_velocity_limit,
    _gen_set_linear_velocity,       # Phase 6 wave 22
    _gen_set_physics_params,
    _gen_set_physics_scene_config,
    _gen_setup_contact_sensors,
    _gen_simplify_collision,
    _handle_get_angular_velocity,            # Phase 7 wave 2
    _handle_get_articulation_mass,           # Phase 7 wave 2
    _handle_get_articulation_state,          # Phase 7 wave 2
    _handle_get_center_of_mass,              # Phase 7 wave 2
    _handle_get_contact_report,              # Phase 7 wave 2
    _handle_get_drive_gains,                 # Phase 7 wave 2
    _handle_get_inertia,                     # Phase 7 wave 2
    _handle_get_joint_limits,                # Phase 7 wave 2
    _handle_get_joint_positions,             # Phase 7 wave 2
    _handle_get_joint_targets,               # Phase 7 wave 2
    _handle_get_joint_torques,               # Phase 7 wave 2
    _handle_get_joint_velocities,            # Phase 7 wave 2
    _handle_get_kinematic_state,             # Phase 7 wave 2
    _handle_get_linear_velocity,             # Phase 7 wave 2
    _handle_get_mass,                        # Phase 7 wave 2
    _handle_get_physics_errors,              # Phase 7 wave 2
    _handle_get_physics_scene_config,        # Phase 7 wave 2
    _handle_lookup_material,                 # Phase 7 wave 16
    _handle_suggest_physics_settings,        # Phase 7 wave 16
)
from .handlers.pick_place import (  # noqa: E402
    _gen_setup_pick_place_controller,
    _gen_setup_pick_place_ros2_bridge,
)
from .handlers.diagnostics import (  # noqa: E402
    _gen_build_stage_index,         # Phase 6 wave 22
    _gen_check_path_clearance,
    _gen_check_physics_health,
    _gen_check_singularity,
    _gen_configure_zmq_stream,      # Phase 6 wave 24
    _gen_create_broken_scene,         # Phase 6 wave 23
    _gen_debug_draw,
    _gen_debug_graph,
    _gen_enable_deterministic_mode,   # Phase 6 wave 23
    _gen_enable_extension,          # Phase 6 wave 22
    _gen_highlight_prim,
    _gen_monitor_joint_effort,
    _gen_preflight_check,
    _gen_set_clearance_monitor,       # Phase 6 wave 23
    _gen_show_workspace,            # Phase 6 wave 22
    _gen_sim_control,               # Phase 6 wave 22
    _gen_visualize_clearance,
    _gen_visualize_collision_mesh,
    _gen_visualize_forces,
    _handle_check_collision_mesh,   # Phase 7 wave 10
    _handle_check_collisions,       # Phase 7 wave 10
    _handle_check_teleop_hardware,  # Phase 7 wave 10
    _handle_check_tf_health,        # Phase 7 wave 10
    _handle_check_vram_headroom,    # Phase 7 wave 10
    _handle_compare_sim_real_video, # Phase 7 wave 10
    _handle_console_error_autodetect,  # Phase 7 wave 10
    _handle_diagnose_domain_gap,    # Phase 7 wave 10
    _handle_diagnose_performance,   # Phase 7 wave 10
    _handle_diagnose_physics_error, # Phase 7 wave 10
    _handle_diagnose_whole_body,    # Phase 7 wave 10
    _handle_get_active_state,       # Phase 7 wave 10
    _handle_get_console_errors,     # Phase 7 wave 10
    _handle_get_debug_info,         # Phase 7 wave 10
    _handle_hardware_compatibility_check,  # Phase 7 wave 10
    _handle_list_extensions,            # Phase 7 wave 16
    _handle_measure_distance,       # Phase 7 wave 14
    _handle_measure_sim_real_gap,   # Phase 7 wave 14
    _handle_proactive_check,        # Phase 7 wave 14
    _handle_simulate_traversal_check,  # Phase 7 wave 14
    _handle_trace_config,           # Phase 7 wave 14
    _handle_validate_annotations,   # Phase 7 wave 14
    _handle_validate_calibration,   # Phase 7 wave 14
    _handle_validate_scene_blueprint,  # Phase 7 wave 14
    _handle_validate_semantic_labels,  # Phase 7 wave 14
    _handle_validate_teleop_demo,   # Phase 7 wave 14
    _handle_verify_pickplace_pipeline,  # Phase 7 wave 14
)
from .handlers.rendering import (  # noqa: E402
    _gen_add_default_light,
    _gen_create_hdri_skydome,
    _gen_enable_post_process,
    _gen_set_environment_background,
    _gen_set_light_color,
    _gen_set_light_intensity,
    _gen_set_render_config,
    _gen_set_render_resolution,
)
from .handlers.resolve import (  # noqa: E402
    _handle_resolve_constraint_phrase,
    _handle_resolve_context_reference,
    _handle_resolve_coordinate_reference,
    _handle_resolve_count_vagueness,
    _handle_resolve_material_properties,
    _handle_resolve_prim_reference,
    _handle_resolve_relational_property,
    _handle_resolve_robot_class,
    _handle_resolve_sequence_phrase,
    _handle_resolve_size_adjective,
    _handle_resolve_skill_composition,
    _handle_resolve_success_condition,
)
from .handlers.robot import (  # noqa: E402
    _gen_anchor_robot,
    _gen_assemble_robot,
    _gen_create_behavior,           # Phase 6 wave 24
    _gen_create_bin,
    _gen_create_conveyor,
    _gen_create_conveyor_track,
    _gen_create_gripper,
    _gen_create_wheeled_robot,
    _gen_define_grasp_pose,
    _gen_export_nav2_map,           # Phase 6 wave 24
    _gen_generate_occupancy_map,    # Phase 6 wave 24
    _gen_grasp_object,
    _gen_import_robot,
    _gen_interpolate_trajectory,
    _gen_load_robot_pose,
    _gen_move_to_pose,
    _gen_navigate_to,
    _gen_plan_trajectory,
    _gen_publish_robot_description,
    _gen_record_trajectory,
    _gen_record_waypoints,
    _gen_replay_trajectory,
    _gen_robot_wizard,
    _gen_set_motion_policy,
    _gen_setup_multi_rate,
    _gen_setup_rsi_from_demos,
    _gen_setup_whole_body_control,
    _gen_solve_ik,
    _gen_start_teaching_mode,
    _gen_teach_robot_pose,
    _gen_tune_gains,
    _gen_verify_import,
    _handle_apply_robot_fix_profile,   # Phase 7 wave 7
    _handle_calibrate_physics,         # Phase 7 wave 7
    _handle_create_articulated_joint,  # Phase 7 wave 7
    _handle_create_gravity_dispenser,  # Phase 7 wave 7
    _handle_create_heap_zone,          # Phase 7 wave 7
    _handle_create_kit_tray,           # Phase 7 wave 7
    _handle_create_linear_axis_robot,  # Phase 7 wave 7
    _handle_create_recirculation_loop, # Phase 7 wave 7
    _handle_create_rotary_table,       # Phase 7 wave 7
    _handle_generate_robot_description, # Phase 7 wave 7
    _handle_get_gripper_state,         # Phase 7 wave 7
    _handle_list_available_controllers, # Phase 7 wave 16
    _handle_place_on_top_of,            # Phase 7 wave 16
    _handle_quick_calibrate,           # Phase 7 wave 7
    _handle_register_moving_obstacle,  # Phase 7 wave 7
    _handle_setup_assembly_constraint, # Phase 7 wave 7
    _handle_setup_cortex_behavior,     # Phase 7 wave 7
    _handle_setup_grasp_pose_sampler,  # Phase 7 wave 7
    _handle_setup_isaac_ros_cumotion_moveit, # Phase 7 wave 7
    _handle_setup_nav_robot,           # Phase 7 wave 8
    _handle_setup_pick_place_with_vision,  # Phase 7 wave 8
    _handle_setup_robot_claim_mutex,   # Phase 7 wave 8
    _handle_setup_robot_handoff_signal, # Phase 7 wave 8
    _handle_setup_ros2_control_compat, # Phase 7 wave 8
    _handle_setup_zone_partition,      # Phase 7 wave 8
    _handle_surface_gripper,           # Phase 7 wave 8
    _handle_track_slot_occupancy,      # Phase 7 wave 8
    _handle_visualize_behavior_tree,   # Phase 7 wave 8
)
from .handlers.ros2 import (  # noqa: E402
    _gen_configure_ros2_bridge,
    _gen_configure_ros2_time,
    _gen_fix_ros2_qos,
    _gen_replay_rosbag,
    _gen_setup_ros2_bridge,
    _gen_show_tf_tree,
    _handle_diagnose_ros2,          # Phase 7 wave 14
    _handle_emit_ros2_control_yaml, # Phase 7 wave 14
    _handle_precheck_ros2_environment,  # Phase 7 wave 14
)
from .handlers.sdg import (  # noqa: E402
    _gen_add_domain_randomizer,
    _gen_add_latency_randomization,
    _gen_configure_coco_yolo_writer,
    _gen_configure_correlated_dr,
    _gen_configure_differential_sdg,
    _gen_configure_sdg,
    _gen_create_sdg_pipeline,
    _gen_enforce_class_balance,
    _gen_export_dataset,
    _gen_preview_dr,
    _handle_benchmark_sdg,              # Phase 7 wave 16
    _handle_preview_sdg,                # Phase 7 wave 16
)
from .handlers.teleop import (  # noqa: E402
    _gen_configure_teleop_mapping,
    _gen_export_teleop_mapping,
    _gen_generate_teleop_watchdog_script,
    _gen_record_teleop_demo,
    _gen_start_teleop_session,
    _gen_stop_teleop_session,
    _gen_teleop_safety_config,
    _handle_summarize_teleop_session,   # Phase 7 wave 16
)
from .handlers.training import (  # noqa: E402
    _gen_clone_envs,
    _gen_cloud_download_results,    # Phase 6 wave 24
    _gen_create_calibration_experiment,  # Phase 6 wave 24
    _gen_eval_harness,              # Phase 6 wave 24
    _gen_evaluate_groot,
    _gen_evaluate_reward,
    _gen_export_policy,
    _gen_finetune_groot,
    _gen_launch_training,
    _gen_setup_loco_manipulation_training,
    _handle_analyze_checkpoint,     # Phase 7 wave 5
    _handle_analyze_randomization,  # Phase 7 wave 5
    _handle_apply_dr_preset,        # Phase 7 wave 5
    _handle_checkpoint_training,    # Phase 7 wave 5
    _handle_cloud_estimate_cost,    # Phase 7 wave 6
    _handle_cloud_launch,           # Phase 7 wave 6
    _handle_cloud_status,           # Phase 7 wave 6
    _handle_cloud_teardown,         # Phase 7 wave 6
    _handle_compare_policies,       # Phase 7 wave 5
    _handle_create_isaaclab_env,    # Phase 7 wave 5
    _handle_detect_ood,             # Phase 7 wave 5
    _handle_diagnose_training,      # Phase 7 wave 6
    _handle_eureka_status,          # Phase 7 wave 5
    _handle_export_finetune_data,   # Phase 7 wave 5
    _handle_finetune_stats,         # Phase 7 wave 5
    _handle_generate_reward,        # Phase 7 wave 5
    _handle_get_env_observations,   # Phase 7 wave 5
    _handle_get_env_rewards,        # Phase 7 wave 5
    _handle_get_env_termination_state,  # Phase 7 wave 5
    _handle_get_training_status,    # Phase 7 wave 5
    _handle_iterate_reward,         # Phase 7 wave 5
    _handle_load_groot_policy,      # Phase 7 wave 5
    _handle_load_rl_policy,         # Phase 7 wave 6
    _handle_monitor_forgetting,     # Phase 7 wave 6
    _handle_pause_training,         # Phase 7 wave 6
    _handle_profile_training_throughput,  # Phase 7 wave 6
    _handle_redact_finetune_data,   # Phase 7 wave 6
    _handle_review_reward,          # Phase 7 wave 6
    _handle_suggest_data_mix,       # Phase 7 wave 6
    _handle_suggest_dr_ranges,      # Phase 7 wave 6
    _handle_suggest_finetune_config,  # Phase 7 wave 6
    _handle_suggest_parameter_adjustment,  # Phase 7 wave 6
    _handle_train_actuator_net,     # Phase 7 wave 6
)
from .handlers.vision import (  # noqa: E402
    _gen_extract_attention_maps,
    _gen_focus_viewport_on,         # Phase 6 wave 22
    _gen_quick_demo,
    _gen_record_demo_video,
    _gen_render_video,
    _gen_set_render_mode,
    _gen_set_semantic_label,
    _gen_set_viewport_camera,
    _handle_capture_camera_image,   # Phase 7 wave 11
    _handle_capture_viewport,       # Phase 7 wave 11
    _handle_get_camera_params,      # Phase 7 wave 11
    _handle_get_light_properties,   # Phase 7 wave 11
    _handle_get_render_config,      # Phase 7 wave 11
    _handle_get_timeline_state,     # Phase 7 wave 11
    _handle_get_viewport_camera,    # Phase 7 wave 11
    _handle_inspect_camera,         # Phase 7 wave 11
    _handle_list_cameras,           # Phase 7 wave 11
    _handle_list_keyframes,         # Phase 7 wave 11
    _handle_list_lights,            # Phase 7 wave 11
    _handle_pixel_to_world,         # Phase 7 wave 11
    _handle_vision_analyze_scene,   # Phase 7 wave 11
    _handle_vision_bounding_boxes,  # Phase 7 wave 11
    _handle_vision_detect_objects,  # Phase 7 wave 11
    _handle_vision_plan_trajectory, # Phase 7 wave 11
)
from .handlers.workflow import (  # noqa: E402
    _handle_approve_workflow_checkpoint,
    _handle_cancel_workflow,
    _handle_dispatch_async_task,
    _handle_edit_workflow_plan,
    _handle_execute_with_retry,
    _handle_get_workflow_status,
    _handle_list_workflows,
    _handle_post_action_suggestions,
    _handle_query_async_task,
    _handle_queue_write_locked_patch,
    _handle_record_feedback,
    _handle_scene_aware_starter_prompts,
    _handle_slash_command_discovery,
    _handle_start_workflow,
    _handle_watch_changes,
)


# _gen_add_reference moved to handlers/scene_authoring.py (Phase 3 wave 2).
# Imported back at the top of this file (see Phase 3 wave 1 import block).


# _gen_apply_api_schema moved to handlers/scene_authoring.py (Phase 3 wave 3).


# _gen_clone_prim moved to handlers/scene_authoring.py (Phase 3 wave 3).


# _gen_deformable moved to handlers/physics.py (Phase 5 wave 4).


# _gen_deformable_body moved to handlers/physics.py (Phase 5 wave 4).


# _gen_deformable_surface moved to handlers/physics.py (Phase 5 wave 4).


# _OG_NODE_TYPE_MAP migrated to handlers/_shared.py (Phase 8 wave 5, 2026-05-13).


# _gen_create_omnigraph moved to handlers/scene_authoring.py (Phase 3 wave 4).


# _gen_create_material moved to handlers/scene_authoring.py (Phase 3 wave 3).


# _gen_assign_material moved to handlers/scene_authoring.py (Phase 3 wave 2).


# _gen_sim_control moved to handlers/diagnostics.py (Phase 6 wave 22).


# _gen_set_physics_params moved to handlers/physics.py (Phase 5 wave 1).
# _gen_teleport_prim moved to handlers/scene_authoring.py (Phase 3 wave 2).
# _gen_set_joint_targets moved to handlers/physics.py (Phase 5 wave 1).


# _gen_import_robot moved to handlers/robot.py (Phase 6 wave 20).


# ── Robot anchoring ──────────────────────────────────────────────────────────
# Isaac Sim robot USD assets contain a "rootJoint" (6-DOF free joint) that
# allows them to float freely. To anchor a robot:
# 1. Set PhysxArticulationAPI.fixedBase = True (keeps ArticulationRootAPI on root)
# 2. Delete the rootJoint (free joint)
# 3. Optionally create a FixedJoint to attach to a specific surface
# CRITICAL: Do NOT move ArticulationRootAPI — it must stay on the root prim
# or the tensor API pattern '/World/Robot' will fail with
# "Pattern did not match any articulations".

# _gen_anchor_robot moved to handlers/robot.py (Phase 6 wave 1).


# _gen_set_viewport_camera moved to handlers/vision.py (Phase 6 wave 15).


# _gen_configure_sdg moved to handlers/sdg.py (Phase 6 wave 5).


# ── Code generation dispatch ─────────────────────────────────────────────────

# Phase 9 (2026-05-13): both dispatch dicts populated by
# handlers/_dispatch.py:register_handlers() — sole entry point.
# Replaces 2 dict literals + ~340 inline assignments + 3 external
# registrator calls + ROS2 try/except block (all migrated).
CODE_GEN_HANDLERS: Dict[str, Callable[..., Any]] = {}
DATA_HANDLERS: Dict[str, Callable[..., Awaitable[Any]]] = {}

from .handlers._dispatch import register_handlers
register_handlers(DATA_HANDLERS, CODE_GEN_HANDLERS)


# ── Spec / data lookup handlers (no code gen, just return data) ──────────────

# _handle_lookup_product_spec moved to handlers/scene_blueprints.py (Phase 7 wave 12+13 redirect-stub stripped).


# _handle_scene_summary moved to handlers/scene_authoring.py (Phase 7 wave 4).

# _handle_capture_viewport moved to handlers/vision.py (Phase 7 wave 11).


# _handle_get_console_errors moved to handlers/diagnostics.py (Phase 7 wave 12+13 redirect-stub stripped).


# _handle_get_articulation_state moved to handlers/physics.py (Phase 7 wave 2).

    # _handle_list_all_prims moved to handlers/scene_authoring.py (Phase 7 wave 3).


# _SIZE_BUCKET_ALIASES migrated to handlers/resolve.py (Phase 8 wave 8, 2026-05-13).

# Per-object-class size buckets in meters. The "default" row handles
# unknown classes with sensible cube-like defaults. Tuned to match
# common Isaac Sim / industrial-robotics conventions: small cubes are
# 5cm (manipulation benchmark size), tables are 1.2m (workbench).
# _SIZE_BUCKETS migrated to handlers/resolve.py (Phase 8 wave 8, 2026-05-13).


# _COUNT_BUCKETS migrated to handlers/resolve.py (Phase 8 wave 8, 2026-05-13).


    # _handle_resolve_count_vagueness moved to handlers/resolve.py (Phase 7 wave 1).


# robot-class → registry key. Anchors generic class language ('a manipulator',
# 'a humanoid', 'a wheeled robot') to the same name resolution that
# robot_wizard / import_robot already understand. Avoids the agent inventing
# random asset paths when it should be selecting a known-good default.
# _ROBOT_CLASS_DEFAULTS migrated to handlers/resolve.py (Phase 8 wave 8, 2026-05-13).


    # _handle_resolve_robot_class moved to handlers/resolve.py (Phase 7 wave 1).


# _MATERIAL_PROPERTIES migrated to handlers/resolve.py (Phase 8 wave 8, 2026-05-13).


    # _handle_resolve_material_properties moved to handlers/resolve.py (Phase 7 wave 1).


# _CONSTRAINT_RE_NUMERIC migrated to handlers/resolve.py (Phase 8 wave 8, 2026-05-13).
# _UNIT_TO_SI migrated to handlers/resolve.py (Phase 8 wave 8, 2026-05-13).


    # _handle_resolve_constraint_phrase moved to handlers/resolve.py (Phase 7 wave 1).

    # _handle_resolve_sequence_phrase moved to handlers/resolve.py (Phase 7 wave 1).

    # _handle_resolve_context_reference moved to handlers/resolve.py (Phase 7 wave 1).

# _SKILL_RECIPES migrated to handlers/resolve.py (Phase 8 wave 8, 2026-05-13).


# Default reach radius (meters) per robot type. Used by
# verify_pickplace_pipeline when no explicit reach is supplied.
# These are conservative envelope estimates from the manufacturer specs;
# actual cuRobo / Lula IK can refine but the envelope is what matters
# for pipeline-feasibility-without-running-IK.
_ROBOT_REACH_M = {
    "franka_panda": 0.855,  # Franka Panda — 855mm reach
    "ur5e":         0.850,
    "ur10":         1.300,
    "ur10e":        1.300,
    "kinova":       0.902,
    "h1":           0.580,  # H1 humanoid arm reach (one arm)
    "g1":           0.450,
    "default":      0.800,
}


# _SUCCESS_CONDITION_TEMPLATES migrated to handlers/resolve.py (Phase 8 wave 8, 2026-05-13).


_COORD_LANDMARKS = {
    # Named anchor points — return position relative to a reference prim
    # (or world origin when no reference). Ordered most-specific first.
    "origin": "world",
    "world origin": "world",
    "center of stage": "world",
    "stage center": "world",
}


    # _handle_resolve_coordinate_reference moved to handlers/resolve.py (Phase 7 wave 1).


_RELATIONAL_PATTERN_RE = __import__("re").compile(
    r"(?P<factor>\d+(?:\.\d+)?)\s*[xX×]?\s*(?P<rel>times|x|×|the size of|larger than|smaller than|bigger than)?",
    __import__("re").IGNORECASE,
)


    # _handle_resolve_relational_property moved to handlers/resolve.py (Phase 7 wave 1).

    # _handle_resolve_success_condition moved to handlers/resolve.py (Phase 7 wave 1).

# _handle_verify_pickplace_pipeline moved to handlers/diagnostics.py (Phase 7 wave 14).


async def _augment_verify_with_feasibility(verify_result: Dict, stages: list) -> Dict:
    """Phase 1.5 — Opus §F. Run diagnose_scene_feasibility for each stage's
    pick+drop pose, merge any infeasible/overconstrained violations into the
    verify_result['issues'] list. Sets pipeline_ok=False if any CRITICAL
    violations found.

    Reads stage pick_pos / place_pos from verify_result['results']
    (already computed by the kit-side script) — avoids second Kit RPC trip
    to compute them.
    """
    import json as _j
    out_text = (verify_result.get("output") or "").strip()
    parsed = None
    for line in out_text.splitlines()[::-1]:
        line = line.strip()
        if line.startswith("{"):
            try:
                parsed = _j.loads(line); break
            except Exception:
                continue
    if not parsed:
        return verify_result  # non-parseable; leave alone

    stage_results = parsed.get("results") or []
    issues = list(parsed.get("issues") or [])
    pipeline_ok = bool(parsed.get("pipeline_ok"))
    feasibility_reports = []

    for i, sr in enumerate(stage_results):
        rp = sr.get("robot_path")
        pick_pos = sr.get("pick_pos")
        place_pos = sr.get("place_pos")
        if not rp or not pick_pos or not place_pos:
            continue
        try:
            diag_res = await execute_tool_call("diagnose_scene_feasibility", {
                "robot_path": rp,
                "pick_pose": pick_pos,
                "drop_pose": place_pos,
                "robot_base": sr.get("robot_pos") or [0, 0, 0],
                "max_reach": sr.get("reach_m") or 0.855,
                "use_cache": True,
            })
        except Exception as e:
            issues.append(f"[feasibility] stage {i}: diagnose call failed: {type(e).__name__}: {str(e)[:80]}")
            continue
        if isinstance(diag_res, dict) and "verdict" in diag_res:
            d = diag_res
        else:
            d = None
            for line in (diag_res.get("output") or "").splitlines()[::-1]:
                line = line.strip()
                if line.startswith("{"):
                    try:
                        d = _j.loads(line); break
                    except Exception:
                        continue
        if not d:
            continue
        feasibility_reports.append({"stage_index": i, "verdict": d.get("verdict"),
                                     "n_violations": len(d.get("violations") or [])})
        verdict = d.get("verdict")
        if verdict in ("infeasible", "overconstrained"):
            for v in (d.get("violations") or []):
                if v.get("severity") in ("ERROR", "CRITICAL"):
                    issues.append(f"[feasibility] stage {i}: {v.get('message')}")
            if verdict == "infeasible":
                pipeline_ok = False

    parsed["issues"] = issues
    parsed["pipeline_ok"] = pipeline_ok
    parsed["feasibility_reports"] = feasibility_reports

    # Re-serialize the augmented payload onto the result for the caller
    new_output = _j.dumps(parsed)
    return {**verify_result, "output": new_output}


# _handle_simulate_traversal_check moved to handlers/diagnostics.py (Phase 7 wave 14).


    # _handle_resolve_skill_composition moved to handlers/resolve.py (Phase 7 wave 1).

    # _handle_resolve_size_adjective moved to handlers/resolve.py (Phase 7 wave 1).

    # _handle_resolve_prim_reference moved to handlers/resolve.py (Phase 7 wave 1).

    # _handle_place_on_top_of moved to handlers/robot.py (Phase 7 wave 16).


# _handle_measure_distance moved to handlers/diagnostics.py (Phase 7 wave 14).


# _handle_get_debug_info moved to handlers/diagnostics.py (Phase 7 wave 12+13 redirect-stub stripped).


# _handle_lookup_api_deprecation moved to handlers/scene_blueprints.py (Phase 7 wave 12+13 redirect-stub stripped).


# _handle_lookup_knowledge moved to handlers/scene_blueprints.py (Phase 7 wave 12+13 redirect-stub stripped).


# Data-only handlers (no code gen → return data directly to LLM)



# ── Main dispatch ────────────────────────────────────────────────────────────

# ── P1: per-tool result-size cap (kcode-spec sec 6.2) ──────────────────
# Bounds the size of any single tool_result before it enters the
# orchestrator's messages history. Justified by Track C 9.4 measurement:
# chars/token ratio is 2.25 (vs. chars/4 heuristic), so token cost is 2x
# what we naively estimate. Capping single tool outputs at 50KB ensures
# no single call burns ~22k tokens of context budget.
#
# Config: per-tool overrides for tools that need MORE headroom, plus
# tools that should NEVER be capped (capture_viewport's image data).
# Env flag RESULT_CAP=off disables capping entirely.

# Default cap in bytes of json-stringified result. Tools above this
# threshold get their `output` field truncated with a marker.
_RESULT_CAP_DEFAULT_CHARS = int(os.environ.get("RESULT_CAP_DEFAULT", "50000"))
# Tools that should never be capped (semantic loss > token saving)
_RESULT_CAP_EXEMPT = frozenset({
    "capture_viewport",       # image bytes — VLM needs intact data
    "vision_detect_objects",  # detection coordinates — small but every entry matters
    # Function/form gates emit the informative result as a JSON line at
    # END of output. Truncating from the beginning loses it. The output
    # may include long preceding noise (controller reset prints, stale-
    # sub Tracebacks) but those don't affect parsing as long as the
    # final JSON line survives. Exempt rather than build a tail-aware
    # truncator (simpler, lower risk of off-by-one).
    "simulate_traversal_check",
    "verify_pickplace_pipeline",
})
# Per-tool overrides (in chars). Smaller = aggressive cap.
_RESULT_CAP_OVERRIDES = {
    "run_usd_script": 12000,           # 9.2 max 205KB — tail outputs blow the budget
    "setup_pick_place_controller": 18000,  # 9.2 max 44KB — controller code is heavy
    "scene_summary": 8000,             # path-heavy, tokenizes 2.0 chars/token
    "list_all_prims": 6000,
    "find_prims_by_schema": 6000,
    "preflight_check": 16000,
}


def _apply_result_cap(tool_name: str, result: Dict[str, Any]) -> Dict[str, Any]:
    """Truncate large tool_result content. Returns either the original
    result (if under cap or capping disabled) or a copy with truncated
    fields and a `_truncated` marker.

    Truncation strategy:
    1. If `output` field exists and is large, truncate it first.
    2. If still over cap and `code` field exists, drop the `code` field
       (LLM rarely needs to re-read it; reduces noise on repeated calls).
    3. Add `_truncated` marker dict so the LLM sees the cap fired.

    Idempotent: re-capping an already-capped result is a no-op.
    """
    if os.environ.get("RESULT_CAP", "on").lower() in ("off", "0", "false"):
        return result
    if not isinstance(result, dict):
        return result
    if tool_name in _RESULT_CAP_EXEMPT:
        return result
    # Already capped — don't recap (prevents marker doubling)
    if "_truncated" in result:
        return result

    cap = _RESULT_CAP_OVERRIDES.get(tool_name, _RESULT_CAP_DEFAULT_CHARS)
    blob_size = len(json.dumps(result, default=str))
    if blob_size <= cap:
        return result

    out = dict(result)
    original_chars = blob_size
    # Step 1: truncate `output` field
    if "output" in out and isinstance(out["output"], str) and len(out["output"]) > 500:
        keep_chars = max(500, cap - 2000)  # leave room for other fields
        out["output"] = (
            out["output"][:keep_chars]
            + f"...[output truncated; original {len(out['output'])} chars]"
        )
    # Step 2: drop `code` field if still over
    new_size = len(json.dumps(out, default=str))
    if new_size > cap and "code" in out:
        out["code"] = "<dropped: code field — see prior tool_result for source>"
        new_size = len(json.dumps(out, default=str))
    out["_truncated"] = {
        "tool": tool_name,
        "original_chars": original_chars,
        "kept_chars": new_size,
        "cap": cap,
    }
    return out


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

    All returns flow through `_apply_result_cap` (P1 from kcode-spec sec 6.2)
    which truncates oversized result payloads to bound LLM token cost.
    """
    logger.info(f"[ToolExecutor] Executing tool: {tool_name}({json.dumps(arguments)[:200]})")

    async def _inner() -> Dict[str, Any]:
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

    try:
        result = await _inner()
    except Exception as e:
        logger.error(f"[ToolExecutor] {tool_name} failed: {e}")
        result = {"type": "error", "error": str(e)}

    return _apply_result_cap(tool_name, result)



# _gen_add_sensor moved to handlers/sensors.py (Phase 6 wave 4).


# Register the sensor generator


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


# _gen_move_to_pose moved to handlers/robot.py (Phase 6 wave 12).
# _gen_plan_trajectory moved to handlers/robot.py (Phase 6 wave 12).


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
    "h1": "h1.usd",
    "unitree_h1": "h1.usd",
    "h1_hand_left": "h1_hand_left.usd",
    "allegro_hand": "allegro_hand.usd",
    "ridgeback_franka": "ridgeback_franka.usd",
    "humanoid": "humanoid.usd",
    "humanoid_28": "humanoid_28.usd",
}


def _invalidate_asset_index() -> None:
    """Invalidate the cached asset index so the next search rebuilds it."""
    global _asset_index
    _asset_index = None


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


# _handle_catalog_search moved to handlers/scene_blueprints.py (Phase 7 wave 12+13 redirect-stub stripped).




# ── Local Filesystem Search ──────────────────────────────────────────────────
# When the user references "this URDF" / "the STEP file you imported" without
# a path, the agent needs to discover local files. Without this tool the agent
# either asks the user (annoying) or generates ad-hoc glob.glob() code-patches
# (unguarded). This is a guarded discovery primitive scoped to known asset
# roots — not a general filesystem walker.
import os as _os_files
import glob as _glob_files
import fnmatch as _fnmatch_files

_LIST_LOCAL_DEFAULT_ROOTS = [
    "/home/anton/projects/Omniverse_Nemotron_Ext/workspace",
    "/home/anton/projects/Omniverse_Nemotron_Ext/data",
    "/home/anton/Downloads",
    "/home/anton/Documents",
    "/home/anton/robots",
    "/home/anton/projects/myarm",
    "/home/anton/projects/sharp_football",
    "/tmp",
]
# Hard cap to stop the agent from triggering massive filesystem walks.
_LIST_LOCAL_MAX_RESULTS = 200
_LIST_LOCAL_MAX_DEPTH = 6
# Asset-relevant extensions only — refuse to surface secrets / source code.
_LIST_LOCAL_ALLOWED_EXTS = {
    ".urdf", ".usd", ".usda", ".usdc", ".usdz",
    ".step", ".stp", ".iges", ".igs", ".stl", ".obj", ".fbx", ".gltf", ".glb",
    ".ifc", ".ifczip",
    ".yaml", ".yml", ".json",  # config, scene templates
    ".pcd", ".ply",  # point clouds
    ".png", ".jpg", ".jpeg", ".exr", ".hdr",  # textures (filtered by name pattern)
}


# _handle_list_local_files moved to handlers/scene_blueprints.py (Phase 7 wave 12+13 redirect-stub stripped).




# ── Nucleus Browse & Download ────────────────────────────────────────────────

# _handle_nucleus_browse moved to handlers/scene_blueprints.py (Phase 7 wave 13).
# _handle_download_asset moved to handlers/scene_blueprints.py (Phase 7 wave 13).



# ── Scene Builder ────────────────────────────────────────────────────────────

# _gen_build_scene_from_blueprint moved to handlers/scene_blueprints.py (Phase 6 wave 11).


# _handle_generate_scene_blueprint moved to handlers/scene_blueprints.py (Phase 7 wave 12+13 redirect-stub stripped).




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


# _handle_create_isaaclab_env moved to handlers/training.py (Phase 7 wave 5).

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


# _gen_launch_training moved to handlers/training.py (Phase 6 wave 6).




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


# _handle_vision_detect_objects moved to handlers/vision.py (Phase 7 wave 11).
# _handle_vision_bounding_boxes moved to handlers/vision.py (Phase 7 wave 11).
# _handle_vision_plan_trajectory moved to handlers/vision.py (Phase 7 wave 11).
# _handle_vision_analyze_scene moved to handlers/vision.py (Phase 7 wave 11).




# _handle_add_vision_classifier_gate moved to handlers/sensors.py (Phase 7 wave 9).




# _handle_setup_pick_place_with_vision moved to handlers/robot.py (Phase 7 wave 8).




# _handle_create_kit_tray moved to handlers/robot.py (Phase 7 wave 7).


# _handle_track_slot_occupancy moved to handlers/robot.py (Phase 7 wave 8).




# _handle_setup_robot_handoff_signal moved to handlers/robot.py (Phase 7 wave 8).




# _handle_setup_robot_claim_mutex moved to handlers/robot.py (Phase 7 wave 8).




# _handle_surface_gripper moved to handlers/robot.py (Phase 7 wave 8).




# _handle_create_articulated_joint moved to handlers/robot.py (Phase 7 wave 7).




# _handle_barcode_reader_sensor moved to handlers/sensors.py (Phase 7 wave 9).




# _handle_create_rotary_table moved to handlers/robot.py (Phase 7 wave 7).




# _handle_register_moving_obstacle moved to handlers/robot.py (Phase 7 wave 7).




# _handle_create_gravity_dispenser moved to handlers/robot.py (Phase 7 wave 7).


# _handle_create_heap_zone moved to handlers/robot.py (Phase 7 wave 7).




# _handle_setup_cortex_behavior moved to handlers/robot.py (Phase 7 wave 7).




# _handle_setup_zone_partition moved to handlers/robot.py (Phase 7 wave 8).




# _handle_add_force_torque_sensor moved to handlers/sensors.py (Phase 7 wave 9).


# _handle_setup_assembly_constraint moved to handlers/robot.py (Phase 7 wave 7).




# _handle_create_recirculation_loop moved to handlers/robot.py (Phase 7 wave 7).


# _handle_create_linear_axis_robot moved to handlers/robot.py (Phase 7 wave 7).


# _handle_nir_material_sensor moved to handlers/sensors.py (Phase 7 wave 9).




# _handle_load_rl_policy moved to handlers/training.py (Phase 7 wave 6).

# _handle_setup_grasp_pose_sampler moved to handlers/robot.py (Phase 7 wave 7).


# _handle_setup_nav_robot moved to handlers/robot.py (Phase 7 wave 8).




# ── Scene Package Export ─────────────────────────────────────────────────────
# Collects all approved code patches from the audit log for a session,
# then writes:  scene_setup.py, ros2_launch.py (if ROS2 nodes present),
# README.md, and a ros2_topics.yaml listing detected topics.

# _handle_export_scene_package moved to handlers/scene_blueprints.py (Phase 7 wave 12+13 redirect-stub stripped).




# ── Stage Analysis ───────────────────────────────────────────────────────────

# _handle_run_stage_analysis moved to handlers/scene_authoring.py (Phase 7 wave 4).



# ══════ From feat/tools-and-bugfixes ══════
# _handle_get_physics_errors moved to handlers/physics.py (Phase 7 wave 2).
# _handle_check_collisions moved to handlers/diagnostics.py (Phase 7 wave 12+13 redirect-stub stripped).

# _handle_fix_error moved to handlers/diagnostics.py (Phase 9 follow-up cleanup).
# _handle_list_scene_templates moved to handlers/scene_blueprints.py (Phase 7 wave 12+13 redirect-stub stripped).

# _handle_load_scene_template moved to handlers/scene_blueprints.py (Phase 7 wave 12+13 redirect-stub stripped).

# _gen_batch_apply_operation moved to handlers/scene_authoring.py (Phase 6 wave 14).

# _handle_validate_scene_blueprint moved to handlers/diagnostics.py (Phase 7 wave 14).



# ══════ From feat/7B-replicator-sdg-v2 ══════
# _gen_create_sdg_pipeline moved to handlers/sdg.py (Phase 6 wave 5).

# _gen_add_domain_randomizer moved to handlers/sdg.py (Phase 6 wave 5).

    # _handle_preview_sdg moved to handlers/sdg.py (Phase 7 wave 16).

# _gen_export_dataset moved to handlers/sdg.py (Phase 6 wave 5).

# ══════ From feat/7C-xr-teleoperation ══════
# _gen_start_teleop_session moved to handlers/teleop.py (Phase 6 wave 8).


# _gen_configure_teleop_mapping moved to handlers/teleop.py (Phase 6 wave 8).


# _gen_record_teleop_demo moved to handlers/teleop.py (Phase 6 wave 8).


# _gen_stop_teleop_session moved to handlers/teleop.py (Phase 6 wave 8).


# _gen_teleop_safety_config moved to handlers/teleop.py (Phase 6 wave 8).


# ══════ From feat/7D-arena ══════
# _arena_env_id migrated to handlers/arena.py (Phase 8 wave 1, 2026-05-13).

# _gen_create_arena moved to handlers/arena.py (Phase 6 wave 9).






























































# _gen_create_arena_variant moved to handlers/arena.py (Phase 6 wave 9).









































# _gen_run_arena_benchmark moved to handlers/arena.py (Phase 6 wave 9).























































    # _handle_arena_leaderboard moved to handlers/arena.py (Phase 7 wave 16).


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

# _handle_generate_reward moved to handlers/training.py (Phase 7 wave 5).

# _gen_evaluate_reward moved to handlers/training.py (Phase 6 wave 6).
# _handle_iterate_reward moved to handlers/training.py (Phase 7 wave 5).

# _handle_eureka_status moved to handlers/training.py (Phase 7 wave 5).


# ══════ From feat/7F-zmq-bridge ══════
# _gen_configure_zmq_stream moved to handlers/diagnostics.py (Phase 6 wave 24).
# ══════ From feat/7G-groot-n1 ══════
# _handle_load_groot_policy moved to handlers/training.py (Phase 7 wave 5).

# _gen_evaluate_groot moved to handlers/training.py (Phase 6 wave 6).
# _gen_finetune_groot moved to handlers/training.py (Phase 6 wave 6).
# _handle_compare_policies moved to handlers/training.py (Phase 7 wave 5).


# ══════ From feat/7H-cloud-deployment ══════
# _handle_cloud_launch moved to handlers/training.py (Phase 7 wave 6).
# _handle_cloud_status moved to handlers/training.py (Phase 7 wave 6).
# _handle_cloud_teardown moved to handlers/training.py (Phase 7 wave 6).
# _handle_cloud_estimate_cost moved to handlers/training.py (Phase 7 wave 6).
# _gen_cloud_download_results moved to handlers/training.py (Phase 6 wave 24).

# ══════ From feat/8A-quick-wins ══════
# _gen_clone_envs moved to handlers/training.py (Phase 6 wave 6).
# _gen_debug_draw moved to handlers/diagnostics.py (Phase 6 wave 10).

# _gen_generate_occupancy_map moved to handlers/robot.py (Phase 6 wave 24).
# _gen_inspect_camera moved to handlers/sensors.py (Phase 6 wave 4).
# _gen_configure_camera moved to handlers/sensors.py (Phase 6 wave 4).

# _handle_inspect_camera moved to handlers/vision.py (Phase 7 wave 11).


# ══════ From feat/8B-motion-planning-complete ══════
# _gen_set_motion_policy moved to handlers/robot.py (Phase 6 wave 12).
# _handle_generate_robot_description moved to handlers/robot.py (Phase 7 wave 7).

_CUROBO_ROBOT_YML_MAP = {
    "franka": "franka.yml",
    "franka_panda": "franka.yml",
    "panda": "franka.yml",
    "ur10e": "ur10e.yml",
    "ur10": "ur10.yml",
    "ur5e": "ur5e.yml",
    "ur5": "ur5e.yml",
    "iiwa": "iiwa.yml",
    "kinova_gen3": "kinova_gen3.yml",
    "jaco7": "jaco7.yml",
}


# _gen_set_motion_policy moved to handlers/robot.py (Phase 6 wave 12).
# _gen_solve_ik moved to handlers/robot.py (Phase 6 wave 12).
# _gen_create_behavior moved to handlers/robot.py (Phase 6 wave 24).
# _gen_create_gripper moved to handlers/robot.py (Phase 6 wave 3).
# _gen_grasp_object moved to handlers/robot.py (Phase 6 wave 12).
# _handle_visualize_behavior_tree moved to handlers/robot.py (Phase 7 wave 8).

# _gen_define_grasp_pose moved to handlers/robot.py (Phase 6 wave 12).

# ══════ From feat/8D-robot-setup ══════
# _gen_robot_wizard moved to handlers/robot.py (Phase 6 wave 2).

# _gen_tune_gains moved to handlers/robot.py (Phase 6 wave 2).

# _gen_assemble_robot moved to handlers/robot.py (Phase 6 wave 2).

# _gen_configure_self_collision moved to handlers/physics.py (Phase 5 wave 4).


# ══════ From feat/8E-wheeled-robots ══════
# _gen_create_wheeled_robot moved to handlers/robot.py (Phase 6 wave 3).
# _gen_navigate_to moved to handlers/robot.py (Phase 6 wave 3).
# _gen_create_conveyor moved to handlers/robot.py (Phase 6 wave 3).
# _gen_create_conveyor_track moved to handlers/robot.py (Phase 6 wave 3).
# _gen_merge_meshes moved to handlers/scene_authoring.py (Phase 6 wave 23).

# _gen_create_bin moved to handlers/robot.py (Phase 6 wave 3).



# ══════ From feat/8F-ros2-deep ══════
# _gen_show_tf_tree moved to handlers/ros2.py (Phase 6 wave 7).

# _gen_publish_robot_description moved to handlers/robot.py (Phase 6 wave 3).

# _gen_configure_ros2_bridge moved to handlers/ros2.py (Phase 6 wave 7).


# ══════ From feat/9-finetune-flywheel ══════
# _handle_record_feedback moved to handlers/workflow.py (Phase 7 wave 12+13 redirect-stub stripped).

# _handle_export_finetune_data moved to handlers/training.py (Phase 7 wave 5).
# _handle_finetune_stats moved to handlers/training.py (Phase 7 wave 5).

# _handle_redact_finetune_data moved to handlers/training.py (Phase 7 wave 6).

# ══════ From feat/addendum-phase2-smart-debugging ══════
# _handle_diagnose_physics_error moved to handlers/diagnostics.py (Phase 7 wave 12+13 redirect-stub stripped).

# _handle_trace_config moved to handlers/diagnostics.py (Phase 7 wave 14).

# _gen_check_physics_health moved to handlers/diagnostics.py (Phase 6 wave 10).


# ══════ From feat/addendum-phase3-urdf-postprocessor ══════
def _detect_robot_type(articulation_path: str) -> Optional[str]:
    """Auto-detect robot type from articulation path."""
    path_lower = articulation_path.lower()
    for robot_type, patterns in _ROBOT_NAME_PATTERNS.items():
        for pat in patterns:
            if pat in path_lower:
                return robot_type
    return None

# _gen_verify_import moved to handlers/robot.py (Phase 6 wave 1).

# _detect_robot_for_fix migrated to handlers/robot.py (Phase 8 wave 13, 2026-05-13).
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

# _handle_diagnose_performance moved to handlers/diagnostics.py (Phase 7 wave 12+13 redirect-stub stripped).

# _handle_find_heavy_prims moved to handlers/scene_authoring.py (Phase 7 wave 15).

# _gen_optimize_collision moved to handlers/physics.py (Phase 5 wave 5).


# ══════ From feat/new-material-database ══════
# _load_physics_materials migrated to handlers/physics.py (Phase 8 wave 6, 2026-05-13).

# _normalize_material_name migrated to handlers/physics.py (Phase 8 wave 6, 2026-05-13).

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

# _handle_scene_diff moved to handlers/scene_authoring.py (Phase 7 wave 4).
# _handle_watch_changes moved to handlers/workflow.py (Phase 7 wave 12+13 redirect-stub stripped).


# ══════ From feat/new-auto-simplification ══════
# _gen_optimize_scene moved to handlers/scene_authoring.py (Phase 6 wave 14).

# _gen_simplify_collision moved to handlers/physics.py (Phase 5 wave 5).

    # _handle_suggest_physics_settings moved to handlers/physics.py (Phase 7 wave 16).


# ══════ From feat/new-onboarding ══════
# _handle_scene_aware_starter_prompts moved to handlers/workflow.py (Phase 7 wave 12+13 redirect-stub stripped).

# _handle_hardware_compatibility_check moved to handlers/diagnostics.py (Phase 7 wave 12+13 redirect-stub stripped).

# _handle_slash_command_discovery moved to handlers/workflow.py (Phase 7 wave 12+13 redirect-stub stripped).

# _handle_console_error_autodetect moved to handlers/diagnostics.py (Phase 7 wave 12+13 redirect-stub stripped).

# _handle_post_action_suggestions moved to handlers/workflow.py (Phase 7 wave 12+13 redirect-stub stripped).

# _gen_load_scene_template moved to handlers/scene_blueprints.py (Phase 6 wave 11).


# ══════ From feat/new-omnigraph-assistant ══════
# _detect_template migrated to handlers/scene_authoring.py (Phase 8 wave 14, 2026-05-13).

# _gen_create_graph moved to handlers/scene_authoring.py (Phase 6 wave 23).

# _gen_explain_graph moved to handlers/scene_authoring.py (Phase 6 wave 23).

# _gen_debug_graph moved to handlers/diagnostics.py (Phase 6 wave 10).

# ══════ From feat/new-interactive-teaching ══════
# _gen_start_teaching_mode moved to handlers/robot.py (Phase 6 wave 13).

# _gen_record_waypoints moved to handlers/robot.py (Phase 6 wave 12).
# _gen_replay_trajectory moved to handlers/robot.py (Phase 6 wave 13).

# _gen_interpolate_trajectory moved to handlers/robot.py (Phase 6 wave 13).


# ══════ From feat/preflight-check-23 ══════
# _gen_preflight_check moved to handlers/diagnostics.py (Phase 6 wave 10).


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

# _handle_diagnose_training moved to handlers/training.py (Phase 7 wave 6).

# _handle_review_reward moved to handlers/training.py (Phase 7 wave 6).
# _handle_profile_training_throughput moved to handlers/training.py (Phase 7 wave 6).

# _gen_eval_harness moved to handlers/training.py (Phase 6 wave 24).

# ══════ From feat/addendum-phase7C-teleop-quality ══════
# _handle_check_teleop_hardware moved to handlers/diagnostics.py (Phase 7 wave 12+13 redirect-stub stripped).

# _open_hdf5_safely migrated to handlers/_shared.py (Phase 8 wave 5, 2026-05-13).

# _handle_validate_teleop_demo moved to handlers/diagnostics.py (Phase 7 wave 14).

    # _handle_summarize_teleop_session moved to handlers/teleop.py (Phase 7 wave 16).


# _gen_export_teleop_mapping moved to handlers/teleop.py (Phase 6 wave 8).


# _gen_generate_teleop_watchdog_script moved to handlers/teleop.py (Phase 6 wave 8).


# ══════ From feat/addendum-phase7B-sdg-advanced ══════
# _gen_scatter_on_surface moved to handlers/scene_authoring.py (Phase 6 wave 14).

# _gen_configure_differential_sdg moved to handlers/sdg.py (Phase 6 wave 5).

# _gen_configure_coco_yolo_writer moved to handlers/sdg.py (Phase 6 wave 5).

# _gen_enforce_class_balance moved to handlers/sdg.py (Phase 6 wave 5).

    # _handle_benchmark_sdg moved to handlers/sdg.py (Phase 7 wave 16).


# ══════ From feat/addendum-enterprise-scale ══════
# _gen_build_stage_index moved to handlers/diagnostics.py (Phase 6 wave 22).

# _handle_build_stage_index moved to handlers/scene_authoring.py (Phase 7 wave 4).
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

# _handle_query_stage_index moved to handlers/scene_authoring.py (Phase 7 wave 4).
# _gen_save_delta_snapshot moved to handlers/scene_authoring.py (Phase 6 wave 14).

# _handle_save_delta_snapshot moved to handlers/scene_authoring.py (Phase 7 wave 15).

# _gen_restore_delta_snapshot moved to handlers/scene_authoring.py (Phase 6 wave 14).

# _handle_restore_delta_snapshot moved to handlers/scene_authoring.py (Phase 7 wave 15).

# _gen_batch_delete_prims moved to handlers/scene_authoring.py (Phase 6 wave 14).

# _gen_batch_set_attributes moved to handlers/scene_authoring.py (Phase 6 wave 14).

# _handle_queue_write_locked_patch moved to handlers/workflow.py (Phase 7 wave 12+13 redirect-stub stripped).

# _gen_activate_area moved to handlers/scene_authoring.py (Phase 6 wave 23).


# ══════ From feat/addendum-ros2-nav2 ══════
def get_nav2_bridge_profile(profile: str) -> Optional[Dict[str, Any]]:
    """Public lookup helper used by tests and Nav2 bridge code-gen."""
    return _NAV2_BRIDGE_PROFILES.get(profile)

# _gen_setup_ros2_bridge moved to handlers/ros2.py (Phase 6 wave 7).

# _gen_export_nav2_map moved to handlers/robot.py (Phase 6 wave 24).
# _gen_replay_rosbag moved to handlers/ros2.py (Phase 6 wave 7).

# _handle_check_tf_health moved to handlers/diagnostics.py (Phase 7 wave 12+13 redirect-stub stripped).


# ══════ From feat/addendum-dr-advanced ══════
# _gen_configure_correlated_dr moved to handlers/sdg.py (Phase 6 wave 5).

# _handle_suggest_dr_ranges moved to handlers/training.py (Phase 7 wave 6).

# _handle_apply_dr_preset moved to handlers/training.py (Phase 7 wave 5).

# _gen_add_latency_randomization moved to handlers/sdg.py (Phase 6 wave 5).

# _gen_preview_dr moved to handlers/sdg.py (Phase 6 wave 5).


# ══════ From feat/addendum-clearance-detection ══════
# _gen_set_clearance_monitor moved to handlers/diagnostics.py (Phase 6 wave 23).

# _gen_visualize_clearance moved to handlers/diagnostics.py (Phase 6 wave 10).

# _gen_check_path_clearance moved to handlers/diagnostics.py (Phase 6 wave 10).


# ══════ From feat/new-physics-calibration ══════
def _safe_robot_name(articulation_path: str) -> str:
    """Derive a filesystem-safe slug from a USD path, e.g. '/World/Franka' -> 'franka'."""
    name = articulation_path.rstrip("/").split("/")[-1] or "robot"
    return "".join(c if c.isalnum() or c in "_-" else "_" for c in name).lower()

# _suggested_dr_ranges migrated to handlers/robot.py (Phase 8 wave 15, 2026-05-13).

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

# _handle_calibrate_physics moved to handlers/robot.py (Phase 7 wave 7).
# _handle_quick_calibrate moved to handlers/robot.py (Phase 7 wave 7).

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

# _handle_validate_calibration moved to handlers/diagnostics.py (Phase 7 wave 14).

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

# _handle_train_actuator_net moved to handlers/training.py (Phase 7 wave 6).

# ══════ From feat/addendum-humanoid-advanced ══════
# _gen_setup_contact_sensors moved to handlers/physics.py (Phase 5 wave 5).

# _gen_setup_whole_body_control moved to handlers/robot.py (Phase 6 wave 13).

# _gen_setup_loco_manipulation_training moved to handlers/training.py (Phase 6 wave 6).

# _gen_setup_rsi_from_demos moved to handlers/robot.py (Phase 6 wave 13).

# _gen_setup_multi_rate moved to handlers/robot.py (Phase 6 wave 13).

# _handle_diagnose_whole_body moved to handlers/diagnostics.py (Phase 7 wave 12+13 redirect-stub stripped).


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

# _handle_start_workflow moved to handlers/workflow.py (Phase 7 wave 12+13 redirect-stub stripped).

# _handle_edit_workflow_plan moved to handlers/workflow.py (Phase 7 wave 12+13 redirect-stub stripped).

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

# _handle_approve_workflow_checkpoint moved to handlers/workflow.py (Phase 7 wave 12+13 redirect-stub stripped).

# _handle_cancel_workflow moved to handlers/workflow.py (Phase 7 wave 12+13 redirect-stub stripped).

# _handle_get_workflow_status moved to handlers/workflow.py (Phase 7 wave 12+13 redirect-stub stripped).

# _handle_list_workflows moved to handlers/workflow.py (Phase 7 wave 12+13 redirect-stub stripped).

# _handle_execute_with_retry moved to handlers/workflow.py (Phase 7 wave 12+13 redirect-stub stripped).

# _handle_proactive_check moved to handlers/diagnostics.py (Phase 7 wave 14).


# ══════ From feat/addendum-collision-mesh-quality-v2 ══════
# _gen_check_collision_mesh_code moved to handlers/physics.py (Phase 5 wave 5).

# _handle_check_collision_mesh moved to handlers/diagnostics.py (Phase 7 wave 12+13 redirect-stub stripped).

# _gen_fix_collision_mesh moved to handlers/physics.py (Phase 5 wave 5).

# _gen_visualize_collision_mesh moved to handlers/diagnostics.py (Phase 6 wave 10).


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

# _handle_filter_templates_by_hardware moved to handlers/scene_blueprints.py (Phase 7 wave 12+13 redirect-stub stripped).

# _gen_export_template moved to handlers/scene_blueprints.py (Phase 6 wave 11).

# _gen_import_template moved to handlers/scene_blueprints.py (Phase 6 wave 11).


# _handle_check_vram_headroom moved to handlers/diagnostics.py (Phase 7 wave 12+13 redirect-stub stripped).

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

# _handle_dispatch_async_task moved to handlers/workflow.py (Phase 7 wave 12+13 redirect-stub stripped).

# _handle_query_async_task moved to handlers/workflow.py (Phase 7 wave 12+13 redirect-stub stripped).

# _gen_visualize_forces moved to handlers/diagnostics.py (Phase 6 wave 10).

# _gen_render_video moved to handlers/vision.py (Phase 6 wave 15).


# ══════ From feat/new-quick-demo-builder-v2 ══════
# _gen_quick_demo moved to handlers/vision.py (Phase 6 wave 15).

# _gen_record_demo_video moved to handlers/vision.py (Phase 6 wave 15).


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

# _handle_measure_sim_real_gap moved to handlers/diagnostics.py (Phase 7 wave 14).

# _handle_suggest_parameter_adjustment moved to handlers/training.py (Phase 7 wave 6).
# _handle_compare_sim_real_video moved to handlers/diagnostics.py (Phase 7 wave 12+13 redirect-stub stripped).

# _gen_create_calibration_experiment moved to handlers/training.py (Phase 6 wave 24).


# ══════ From feat/addendum-phase7G-groot-tooling-v2 ══════
# _gen_extract_attention_maps moved to handlers/vision.py (Phase 6 wave 15).

# _handle_detect_ood moved to handlers/training.py (Phase 7 wave 5).
# _handle_suggest_data_mix moved to handlers/training.py (Phase 7 wave 6).
# _handle_suggest_finetune_config moved to handlers/training.py (Phase 7 wave 6).
# _handle_monitor_forgetting moved to handlers/training.py (Phase 7 wave 6).

# ══════ From feat/addendum-phase5-pedagogy-uncertainty-v2 ══════
# _gen_create_broken_scene moved to handlers/diagnostics.py (Phase 6 wave 23).


# ══════ From feat/addendum-safety-compliance-v2 ══════
# _gen_enable_deterministic_mode moved to handlers/diagnostics.py (Phase 6 wave 23).


# _handle_pixel_to_world moved to handlers/vision.py (Phase 7 wave 11).

# _gen_record_trajectory moved to handlers/robot.py (Phase 6 wave 13).

    # _handle_prim_exists moved to handlers/scene_authoring.py (Phase 7 wave 3).


# _handle_count_prims_under_path moved to handlers/scene_authoring.py (Phase 7 wave 4).

# _handle_get_joint_targets moved to handlers/physics.py (Phase 7 wave 2).


# ══════ From feat/atomic-tier1-usd-core ══════
    # _handle_list_attributes moved to handlers/scene_authoring.py (Phase 7 wave 3).

# _handle_list_relationships moved to handlers/scene_authoring.py (Phase 7 wave 4).
    # _handle_list_applied_schemas moved to handlers/scene_authoring.py (Phase 7 wave 3).

    # _handle_get_prim_metadata moved to handlers/scene_authoring.py (Phase 7 wave 3).

# _gen_set_prim_metadata moved to handlers/scene_authoring.py (Phase 6 wave 21).

    # _handle_get_prim_type moved to handlers/scene_authoring.py (Phase 7 wave 3).

    # _handle_find_prims_by_schema moved to handlers/scene_authoring.py (Phase 7 wave 3).

    # _handle_find_prims_by_name moved to handlers/scene_authoring.py (Phase 7 wave 3).

    # _handle_get_kind moved to handlers/scene_authoring.py (Phase 7 wave 3).

# _handle_get_active_state moved to handlers/diagnostics.py (Phase 7 wave 12+13 redirect-stub stripped).


# ══════ From feat/atomic-tier2-physics ══════
# _handle_get_linear_velocity moved to handlers/physics.py (Phase 7 wave 2).
# _handle_get_angular_velocity moved to handlers/physics.py (Phase 7 wave 2).
# _gen_set_linear_velocity moved to handlers/physics.py (Phase 6 wave 22).

# _handle_get_mass moved to handlers/physics.py (Phase 7 wave 2).
# _handle_get_inertia moved to handlers/physics.py (Phase 7 wave 2).
# _handle_get_physics_scene_config moved to handlers/physics.py (Phase 7 wave 2).
# _gen_set_physics_scene_config moved to handlers/physics.py (Phase 5 wave 3).

# _handle_list_contacts moved to handlers/sensors.py (Phase 7 wave 9).

# _gen_apply_force moved to handlers/physics.py (Phase 5 wave 3).

# _handle_get_kinematic_state moved to handlers/physics.py (Phase 7 wave 2).

# ══════ From feat/atomic-tier3-articulation ══════
# _handle_get_joint_positions moved to handlers/physics.py (Phase 7 wave 2).
# _handle_get_joint_velocities moved to handlers/physics.py (Phase 7 wave 2).
# _handle_get_joint_torques moved to handlers/physics.py (Phase 7 wave 2).
# _handle_get_drive_gains moved to handlers/physics.py (Phase 7 wave 2).
# _gen_set_joint_limits moved to handlers/physics.py (Phase 5 wave 2).

# _gen_set_joint_velocity_limit moved to handlers/physics.py (Phase 5 wave 3).

# _handle_get_articulation_mass moved to handlers/physics.py (Phase 7 wave 2).
# _handle_get_center_of_mass moved to handlers/physics.py (Phase 7 wave 2).
# _handle_get_gripper_state moved to handlers/robot.py (Phase 7 wave 7).


# ══════ From feat/atomic-tier4-geometry ══════
# _handle_raycast moved to handlers/sensors.py (Phase 7 wave 9).

# _handle_overlap_sphere moved to handlers/sensors.py (Phase 7 wave 9).

# _handle_overlap_box moved to handlers/sensors.py (Phase 7 wave 9).

# _handle_sweep_sphere moved to handlers/sensors.py (Phase 7 wave 9).

# _handle_compute_volume moved to handlers/scene_authoring.py (Phase 7 wave 15).

# _handle_compute_surface_area moved to handlers/scene_authoring.py (Phase 7 wave 15).

# _gen_compute_convex_hull moved to handlers/physics.py (Phase 6 wave 22).



# _handle_compute_stack_placement moved to handlers/scene_authoring.py (Phase 7 wave 15).



# ══════ From feat/atomic-tier5-omnigraph ══════
# _gen_add_node moved to handlers/scene_authoring.py (Phase 6 wave 18).
# _gen_connect_nodes moved to handlers/scene_authoring.py (Phase 6 wave 18).
# _gen_set_graph_variable moved to handlers/scene_authoring.py (Phase 6 wave 18).
# _gen_delete_node moved to handlers/scene_authoring.py (Phase 6 wave 18).

# _handle_list_graphs moved to handlers/scene_authoring.py (Phase 7 wave 15).
# _handle_inspect_graph moved to handlers/scene_authoring.py (Phase 7 wave 15).


# ══════ From feat/atomic-tier6-lighting ══════
# _handle_list_lights moved to handlers/vision.py (Phase 7 wave 11).
# _handle_get_light_properties moved to handlers/vision.py (Phase 7 wave 11).

# _gen_set_light_intensity moved to handlers/rendering.py (Phase 6 wave 17).

# _gen_set_light_color moved to handlers/rendering.py (Phase 6 wave 17).

# _gen_create_hdri_skydome moved to handlers/rendering.py (Phase 6 wave 17).

# _gen_add_default_light moved to handlers/rendering.py (Phase 6 wave 17).



# ══════ From feat/atomic-tier7-camera ══════
# _parse_last_json_line moved to handlers/vision.py (Phase 7 wave 11).
# _handle_list_cameras moved to handlers/vision.py (Phase 7 wave 11).
# _handle_get_camera_params moved to handlers/vision.py (Phase 7 wave 11).

# _gen_set_camera_params moved to handlers/sensors.py (Phase 6 wave 4).

# _handle_capture_camera_image moved to handlers/vision.py (Phase 7 wave 11).

# _gen_set_camera_look_at moved to handlers/sensors.py (Phase 6 wave 4).


# ══════ From feat/atomic-tier8-render ══════
# _handle_get_render_config moved to handlers/vision.py (Phase 7 wave 11).

# _gen_set_render_config moved to handlers/rendering.py (Phase 6 wave 17).

# _gen_set_render_resolution moved to handlers/rendering.py (Phase 6 wave 17).

# _gen_enable_post_process moved to handlers/rendering.py (Phase 6 wave 17).

# _gen_set_environment_background moved to handlers/rendering.py (Phase 6 wave 17).


# ══════ From feat/atomic-tier9-layers ══════
# _handle_list_layers moved to handlers/scene_authoring.py (Phase 7 wave 4).
# _gen_add_sublayer moved to handlers/scene_authoring.py (Phase 6 wave 16).
# _gen_set_edit_target moved to handlers/scene_authoring.py (Phase 6 wave 16).
# _handle_list_variant_sets moved to handlers/scene_authoring.py (Phase 7 wave 4).
# _handle_list_variants moved to handlers/scene_authoring.py (Phase 7 wave 4).
# _gen_flatten_layers moved to handlers/scene_authoring.py (Phase 6 wave 16).

# ══════ From feat/atomic-tier10-animation ══════
# _handle_get_timeline_state moved to handlers/vision.py (Phase 7 wave 11).

# _gen_set_timeline_range moved to handlers/animation.py (Phase 6 wave 19).

# _gen_set_keyframe moved to handlers/animation.py (Phase 6 wave 19).

# _handle_list_keyframes moved to handlers/vision.py (Phase 7 wave 11).

# _gen_play_animation moved to handlers/animation.py (Phase 6 wave 19).


# ══════ From feat/atomic-tier11-sdg ══════
# _handle_list_semantic_classes moved to handlers/scene_authoring.py (Phase 7 wave 4).
    # _handle_get_semantic_label moved to handlers/scene_authoring.py (Phase 7 wave 3).

# _gen_remove_semantic_label moved to handlers/scene_authoring.py (Phase 6 wave 21).


# _gen_assign_class_to_children moved to handlers/scene_authoring.py (Phase 6 wave 21).

# _handle_validate_semantic_labels moved to handlers/diagnostics.py (Phase 7 wave 14).


# ══════ From feat/atomic-tier12-asset-mgmt ══════
# _handle_list_references moved to handlers/scene_authoring.py (Phase 7 wave 4).
# _gen_add_usd_reference moved to handlers/scene_authoring.py (Phase 6 wave 16).
# _handle_list_payloads moved to handlers/scene_authoring.py (Phase 7 wave 4).
# _gen_load_payload moved to handlers/scene_authoring.py (Phase 6 wave 16).
    # _handle_get_asset_info moved to handlers/scene_authoring.py (Phase 7 wave 3).


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

# _handle_get_env_observations moved to handlers/training.py (Phase 7 wave 5).
# _handle_get_env_rewards moved to handlers/training.py (Phase 7 wave 5).
# _handle_get_env_termination_state moved to handlers/training.py (Phase 7 wave 5).
# _handle_pause_training moved to handlers/training.py (Phase 7 wave 6).
# _handle_checkpoint_training moved to handlers/training.py (Phase 7 wave 5).


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

# _handle_select_by_criteria moved to handlers/scene_authoring.py (Phase 7 wave 4).

# ══════ From feat/atomic-tier15-18-misc ══════
# _handle_get_viewport_camera moved to handlers/vision.py (Phase 7 wave 11).

    # _handle_get_selected_prims moved to handlers/scene_authoring.py (Phase 7 wave 3).

# _gen_highlight_prim moved to handlers/diagnostics.py (Phase 6 wave 10).

# _gen_focus_viewport_on moved to handlers/vision.py (Phase 6 wave 22).

# _gen_save_stage moved to handlers/scene_authoring.py (Phase 6 wave 16).
# _gen_open_stage moved to handlers/scene_authoring.py (Phase 6 wave 16).
# _gen_export_stage moved to handlers/scene_authoring.py (Phase 6 wave 16).

# _handle_list_opened_stages moved to handlers/scene_authoring.py (Phase 7 wave 4).
    # _handle_list_extensions moved to handlers/diagnostics.py (Phase 7 wave 16).

# _gen_enable_extension moved to handlers/diagnostics.py (Phase 6 wave 22).

# _gen_create_audio_prim moved to handlers/animation.py (Phase 6 wave 19).

# _gen_set_audio_property moved to handlers/animation.py (Phase 6 wave 19).


# ── Recovered handler registrations (missing from original bundle extraction) ─


# ══════════════════════════════════════════════════════════════════════
# setup_pick_place_controller — composite Tier-1 industrial pick-place
#
# Built 2026-04-19 from the conveyor+Franka smoke-test. The retired
# create_behavior tool pointed callers to isaaclab_tasks or Cortex
# examples; this fills the gap with a direct RmpFlow + state-machine
# integration that runs inside Isaac Sim via a physics-step callback.
#
# Architecture: "python_callback" — Python state machine hooked into
# omni.physx, uses RmpFlow for motion generation, attaches each cube
# to the end-effector via a temporary FixedJoint during transport, and
# releases via FixedJoint deletion over the destination. No OmniGraph
# state machine, no external ROS2 controller — everything runs in-sim
# from a single code patch. The matching ROS2-bridge tool
# (setup_pick_place_ros2_bridge) provides the industrial-realism
# alternative for digital-twin scenarios; see its docstring.
# ══════════════════════════════════════════════════════════════════════

# _gen_setup_pick_place_controller moved to handlers/pick_place.py (Phase 6 wave 25).


# _gen_pick_place_builtin moved to handlers/pick_place.py (Phase 6 wave 25).




# _gen_setup_pick_place_ros2_bridge moved to handlers/pick_place.py (Phase 6 wave 25).




# ══════════════════════════════════════════════════════════════════════
# Phase-12 toolkit — proximity sensor + teach/load pose + mode-driven
# pick-place controller. Built 2026-04-19 after conveyor_pick_place
# template surfaced these gaps across ML-researcher, industrial, and
# vision personas.
# ══════════════════════════════════════════════════════════════════════

# _gen_add_proximity_sensor moved to handlers/sensors.py (Phase 6 wave 4).




# _gen_teach_robot_pose moved to handlers/robot.py (Phase 6 wave 20).




# _gen_load_robot_pose moved to handlers/robot.py (Phase 6 wave 20).




# ══════════════════════════════════════════════════════════════════════
# Mode-specific generators for setup_pick_place_controller
# ══════════════════════════════════════════════════════════════════════

# _PP_RMPFLOW_HEADER migrated to handlers/pick_place.py (Phase 8 wave 9, 2026-05-13).


# _gen_pick_place_sensor_gated moved to handlers/pick_place.py (Phase 6 wave 25).


# ── Shared controller snippets ─────────────────────────────────────────
# Extracted for re-use across pick-place controller generators (native,
# spline, curobo, diffik, osc). Inserted via {var} f-string interpolation
# in each generator — contents must use SINGLE braces (they get emitted
# verbatim into the generated exec_sync script).
#
# Contracts (documented in docs/qa/ctrl_attrs_schema.md):
#   - Scene Reset Manager: idempotent singleton at builtins._scene_reset_manager
#       · register(name, reset_fn) / unregister(name)
#       · reset_fn() → bool (True = done, False = retry next tick)
#   - Observability: every pick-place controller creates ctrl:* attrs on
#       its robot prim. See _PP_CTRL_ATTRS for the canonical list.

_PP_CTRL_ATTRS = [
    # (attr_name, usd_type_name_literal, default_value_literal)
    ("ctrl:mode",            "Sdf.ValueTypeNames.String", '""'),
    ("ctrl:phase",           "Sdf.ValueTypeNames.String", '"wait_sensor"'),
    ("ctrl:cubes_delivered", "Sdf.ValueTypeNames.Int",    "0"),
    ("ctrl:error_count",     "Sdf.ValueTypeNames.Int",    "0"),
    ("ctrl:last_error",      "Sdf.ValueTypeNames.String", '""'),
    ("ctrl:picked_path",     "Sdf.ValueTypeNames.String", '""'),
    ("ctrl:tick_count",      "Sdf.ValueTypeNames.Int",    "0"),
    # Phase 4 diagnostic counters (added 2026-05-10): incremented in
    # cuRobo handler around _planner.plan_pose() calls. Lets probes
    # distinguish "controller never planned" (plan_calls=0) from
    # "controller tried but planner failed" (plan_calls>0, plan_fails>0).
    ("ctrl:plan_calls",      "Sdf.ValueTypeNames.Int",    "0"),
    ("ctrl:plan_fails",      "Sdf.ValueTypeNames.Int",    "0"),
    ("ctrl:last_fail_goal",  "Sdf.ValueTypeNames.String", '""'),
]


# _PP_OBSERVABILITY_SNIPPET migrated to handlers/pick_place.py (Phase 8 wave 9, 2026-05-13).


# _PP_SCENE_RESET_MGR_SNIPPET migrated to handlers/pick_place.py (Phase 8 wave 9, 2026-05-13).


# _gen_pick_place_native moved to handlers/pick_place.py (Phase 6 wave 25).


# _gen_pick_place_spline moved to handlers/pick_place.py (Phase 6 wave 25).


# _gen_pick_place_curobo moved to handlers/pick_place.py (Phase 6 wave 25).


# _gen_pick_place_diffik moved to handlers/pick_place.py (Phase 6 wave 25).


# _gen_pick_place_osc moved to handlers/pick_place.py (Phase 6 wave 25).


# _gen_pick_place_fixed_poses moved to handlers/pick_place.py (Phase 6 wave 25).


# _gen_pick_place_ros2_cmd moved to handlers/pick_place.py (Phase 6 wave 25).


# ══════════════════════════════════════════════════════════════════════
# Controller matrix — availability probe (FAS 4)
# ══════════════════════════════════════════════════════════════════════

_CONTROLLER_METADATA = {
    "native": {
        "hardware_req": "CPU (Franka only)",
        "cycle_class": "medium",          # short / medium / long
        "collision_aware": "partial",      # true / false / partial
        "motion_quality": 2,                # 1-5, 5=best
        "use_case_fit": ["dynamic_targets", "belt_picking", "live_cube_tracking"],
        "summary": "Canonical Isaac Sim franka.PickPlaceController + RmpFlow. Reactive. Good for Franka on moving targets. CPU only.",
        "avoid": ["obstacle-rich scenes", "non-Franka arms"],
    },
    "sensor_gated": {
        "hardware_req": "CPU",
        "cycle_class": "medium",
        "collision_aware": "false",
        "motion_quality": 2,
        "use_case_fit": ["industrial_sim2real", "plc_mimic", "teach_replay"],
        "summary": "Sensor-triggered state machine with pre-taught or coord-based PICK/DROP/HOME. Generic (any arm with RmpFlow config).",
        "avoid": ["complex multi-segment planning", "online re-planning"],
    },
    "fixed_poses": {
        "hardware_req": "CPU",
        "cycle_class": "varies",
        "collision_aware": "false",
        "motion_quality": 1,
        "use_case_fit": ["cycle_time_demos", "validation", "pose_replay"],
        "summary": "Timer-driven pose-list replay. No sensing, no grasping logic.",
        "avoid": ["any real pick-place task"],
    },
    "cube_tracking": {
        "hardware_req": "CPU",
        "cycle_class": "medium",
        "collision_aware": "false",
        "motion_quality": 2,
        "use_case_fit": ["ml_demo_generation"],
        "summary": "Omniscient reactive tracker — cheats using ground-truth cube pose each frame. NOT sim2real honest.",
        "avoid": ["sim2real evaluation", "industrial training"],
    },
    "ros2_cmd": {
        "hardware_req": "External",
        "cycle_class": "varies",
        "collision_aware": "depends",
        "motion_quality": 3,
        "use_case_fit": ["digital_twin", "plc_in_loop", "external_moveit"],
        "summary": "Subscribes to external target-pose / gripper topics. State machine lives outside Isaac Sim.",
        "avoid": ["self-contained Isaac Sim simulations"],
    },
    "spline": {
        "hardware_req": "CPU",
        "cycle_class": "medium",
        "collision_aware": "pre-check only",
        "motion_quality": 4,
        "use_case_fit": ["repetitive_cycles", "sim2real_demos", "cpu_only", "deterministic_motion"],
        "summary": "Pre-planned 6-waypoint Cartesian trajectory with warm-start IK chaining + scipy.CubicSpline interpolation. Smooth, deterministic, CPU-only. Beats native on delivery rate.",
        "avoid": ["obstacle-rich scenes", "highly-dynamic targets"],
    },
    "curobo": {
        "hardware_req": "NVIDIA GPU >= Volta (compute_capability >= 7.0), 4GB VRAM",
        "cycle_class": "short",
        "collision_aware": "true",
        "motion_quality": 5,
        "use_case_fit": ["obstacle_rich_scenes", "precision_picking", "production_cycle_time"],
        "summary": "GPU-accelerated global trajectory optimization with collision checking (Cuboid/SDF/mesh). Industrial quality motion, fastest cycle time when hardware supports.",
        "avoid": ["no GPU / pre-Volta GPU"],
    },
    "diffik": {
        "hardware_req": "CPU, Isaac Lab",
        "cycle_class": "long",
        "collision_aware": "false",
        "motion_quality": 2,
        "use_case_fit": ["teleop", "cartesian_rl_observation", "simple_free_motion"],
        "summary": "Stateless Jacobian-based differential IK (Isaac Lab). No planning or collision awareness. Jittery but fast per-step compute.",
        "avoid": ["singularity-prone trajectories", "obstacle avoidance"],
    },
    "osc": {
        "hardware_req": "CPU, Isaac Lab",
        "cycle_class": "long",
        "collision_aware": "false",
        "motion_quality": 3,
        "use_case_fit": ["contact_rich_tasks", "polishing", "assembly", "compliant_motion"],
        "summary": "Operational-space control with task-space impedance (torque mode). Experimental. Accept 2/4 delivery minimum.",
        "avoid": ["standard pick-place without contact tasks"],
    },
    "auto": {
        "hardware_req": "any",
        "cycle_class": "varies",
        "collision_aware": "varies",
        "motion_quality": None,
        "use_case_fit": ["unknown_hardware", "portable_scripts", "agent_selects"],
        "summary": "Probes runtime env and selects best available (curobo → native → spline → diffik).",
        "avoid": [],
    },
}


def _probe_gpu_capability():
    """Return dict with gpu_available, compute_capability, arch_name, vram_gb."""
    out = {"gpu_available": False, "compute_capability": None,
           "arch_name": None, "vram_gb": None, "cuda_available": False,
           "reason": None}
    try:
        import torch
        out["cuda_available"] = bool(torch.cuda.is_available())
        if not out["cuda_available"]:
            out["reason"] = "torch.cuda.is_available() = False"
            return out
        out["gpu_available"] = True
        cap = torch.cuda.get_device_capability(0)
        out["compute_capability"] = f"{cap[0]}.{cap[1]}"
        arch_map = {
            (6,0): "Pascal", (6,1): "Pascal", (6,2): "Pascal",
            (7,0): "Volta", (7,2): "Volta",
            (7,5): "Turing",
            (8,0): "Ampere", (8,6): "Ampere", (8,7): "Ampere", (8,9): "Ada",
            (9,0): "Hopper",
            (10,0): "Blackwell",
            (12,0): "Blackwell",
        }
        out["arch_name"] = arch_map.get(cap, f"compute_{cap[0]}.{cap[1]}")
        props = torch.cuda.get_device_properties(0)
        out["vram_gb"] = round(props.total_memory / 1024 / 1024 / 1024, 1)
    except ImportError:
        out["reason"] = "torch not importable"
    except Exception as e:
        out["reason"] = f"{type(e).__name__}: {e}"
    return out


def _probe_scipy():
    try:
        import scipy.interpolate  # noqa: F401
        import scipy
        return {"available": True, "version": getattr(scipy, "__version__", "?")}
    except ImportError:
        return {"available": False, "reason": "scipy not importable"}
    except Exception as e:
        return {"available": False, "reason": f"{type(e).__name__}: {e}"}


def _probe_curobo():
    """Probe cuRobo availability. Valid = importable AND content/ present.

    The `isaac_lab_env/site-packages/curobo` is usable ONLY if we also
    monkey-patch wp.func (see I-28) AND have franka.yml + internal
    YAMLs (see I-27). This install lacks content/ so full MotionPlanner
    integration is blocked, but the env-bridge pattern works and core
    modules import.
    """
    import os, glob
    # In-Kit or direct import
    try:
        import curobo  # noqa: F401
        return {"available": True, "note": "curobo imports; content/ may still be absent"}
    except ImportError:
        pass
    # Check isaac_lab_env candidate paths
    for pat in [
        "/home/anton/miniconda3/envs/isaac_lab_env/lib/python3.*/site-packages/curobo",
        os.path.expanduser("~/miniconda3/envs/isaac_lab_env/lib/python*/site-packages/curobo"),
        os.path.expanduser("~/isaac_lab_env/lib/python*/site-packages/curobo"),
    ]:
        hits = glob.glob(pat)
        if hits:
            env_path = hits[0]
            content_dir = os.path.join(env_path, "content")
            has_content = os.path.isdir(content_dir) and any(
                f.endswith((".yml", ".yaml"))
                for f in os.listdir(content_dir) if os.path.isfile(os.path.join(content_dir, f))
            ) if os.path.isdir(content_dir) else False
            return {
                "available": False,
                "reason": "env-bridge required (sys.path.insert + invalidate_caches + wp.func patch); MotionPlanner additionally blocked on missing content/ YAMLs" if not has_content else "env-bridge required",
                "env_bridge_path": env_path,
                "has_content_yamls": has_content,
                "bridgeable": True,
            }
    return {"available": False, "reason": "curobo not found in current env or isaac_lab_env", "bridgeable": False}


def _probe_isaac_lab():
    """Probe Isaac Lab availability. In practice, isaaclab is importable
    inside Kit AFTER sys.path.insert + importlib.invalidate_caches (see I-29).
    So the controller generators are bridgeable even when `import isaaclab`
    fails from the main process.
    """
    import os, glob
    try:
        import isaaclab  # noqa: F401
        return {"available": True}
    except ImportError:
        pass
    for pat in [
        "/home/anton/miniconda3/envs/isaac_lab_env/lib/python3.*/site-packages/isaaclab-*.dist-info",
        os.path.expanduser("~/miniconda3/envs/isaac_lab_env/lib/python*/site-packages/isaaclab-*.dist-info"),
    ]:
        hits = glob.glob(pat)
        if hits:
            return {
                "available": False,
                "reason": "env-bridge required (sys.path.insert + invalidate_caches); controller generators handle this automatically",
                "env_bridge_path": hits[0],
                "bridgeable": True,
            }
    return {"available": False, "reason": "isaaclab not importable and not found in isaac_lab_env", "bridgeable": False}


    # _handle_list_available_controllers moved to handlers/robot.py (Phase 7 wave 16).




# _resolve_auto_target_source migrated to handlers/pick_place.py (Phase 8 wave 9, 2026-05-13).


# === Phase 6 M4 — cuMotion-as-MoveIt2 ===

# _handle_setup_isaac_ros_cumotion_moveit moved to handlers/robot.py (Phase 7 wave 7).



