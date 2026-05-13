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

# Cache loaded once

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

# Phase 8 wave 29 (2026-05-13): _LockedPatch + _StageWriteLockQueue
# canonical home moved to handlers/_state.py. Aliased here for any
# remaining `_te._LockedPatch` / `_te._StageWriteLockQueue` callsites.
from .handlers._state import LockedPatch as _LockedPatch  # noqa: E402, F401
from .handlers._state import StageWriteLockQueue as _StageWriteLockQueue  # noqa: E402, F401

# ── Recovered module-level state from PR branches ───────────────────────

# from: feat/7D-arena

# Phase 8 wave 28 (2026-05-13): _ASYNC_TASKS + _ASYNC_TASKS_LOCK
# canonical home moved to handlers/_state.py. Aliased here so any
# remaining `_te._ASYNC_TASKS*` callsites see the same instances.
from .handlers._state import ASYNC_TASKS as _ASYNC_TASKS  # noqa: E402, F401
from .handlers._state import ASYNC_TASKS_LOCK as _ASYNC_TASKS_LOCK  # noqa: E402, F401

# from: feat/addendum-phase5-pedagogy-uncertainty-v2

# from: feat/7H-cloud-deployment
# from: feat/7H-cloud-deployment

# from: feat/7H-cloud-deployment

# from: feat/new-physics-calibration

# from: feat/new-onboarding

# from: feat/addendum-enterprise-scale

# from: feat/7C-xr-teleoperation

# from: feat/addendum-phase7A-rl-debugging

# from: feat/addendum-dr-advanced

# from: feat/new-physics-calibration

# from: feat/addendum-dr-advanced

# from: feat/addendum-dr-advanced

# from: feat/7E-eureka-rewards
_eureka_runs: Dict[str, Dict] = {}

# from: feat/addendum-phase7G-groot-tooling-v2

# from: feat/addendum-phase7G-groot-tooling-v2

# from: feat/addendum-phase3-urdf-postprocessor

# from: feat/7G-groot-n1

# from: feat/addendum-community-remote-v2

# from: feat/atomic-tier6-lighting

# from: feat/new-onboarding

# from: feat/addendum-ros2-nav2

# from: feat/new-omnigraph-assistant

# _PHYSICS_MATERIALS_PATH + _physics_materials migrated to handlers/physics.py (Phase 8 wave 6).

# from: feat/new-auto-simplification

# from: feat/addendum-phase2-smart-debugging

# from: feat/6A-physx-validation

# from: feat/addendum-collision-mesh-quality-v2

# from: feat/addendum-collision-mesh-quality-v2

# from: feat/atomic-tier8-render

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

# from: feat/new-quick-demo-builder-v2

# from: feat/addendum-community-remote-v2

# from: feat/addendum-phase7A-rl-debugging

# from: feat/addendum-phase3-urdf-postprocessor

# _ROBOT_NAME_PATTERNS + _detect_robot_type deleted as dead code (2026-05-13).
# Pattern dict was used only by _detect_robot_type below; _detect_robot_type
# had zero callers (confirmed via grep). Removed in Phase 8 cleanup.

# from: feat/8D-robot-setup

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

# from: feat/addendum-enterprise-scale

# from: feat/addendum-enterprise-scale

# from: feat/new-onboarding

# from: feat/7C-xr-teleoperation

# from: feat/new-onboarding

# from: feat/8B-motion-planning-complete

# from: feat/addendum-phase7C-teleop-quality

# from: feat/addendum-community-remote-v2

# from: feat/new-omnigraph-assistant

# from: feat/addendum-community-remote-v2

# from: feat/atomic-tier12-asset-mgmt

# from: feat/atomic-tier14-bulk

# from: feat/new-physics-calibration

# from: feat/addendum-community-remote-v2

# from: feat/addendum-humanoid-advanced

# from: feat/phase10-autonomous-workflows

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
# Phase 8 wave 29 (2026-05-13): singleton lives in handlers/_state.py.
from .handlers._state import WRITE_LOCK_QUEUE as _WRITE_LOCK_QUEUE  # noqa: E402, F401

# from: feat/9-finetune-flywheel
_turn_recorder = TurnRecorder()

# ═══════════════════════════════════════════════════════════════════════════

# ── Safe xform helper (inlined into generated code) ─────────────────────────
# Referenced USD assets (e.g. robots) often already have xform ops.
# Calling AddTranslateOp() again crashes with "Error in AddXformOp".
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

# Imported back at the top of this file (see Phase 3 wave 1 import block).

# ── Robot anchoring ──────────────────────────────────────────────────────────
# Isaac Sim robot USD assets contain a "rootJoint" (6-DOF free joint) that
# allows them to float freely. To anchor a robot:
# 1. Set PhysxArticulationAPI.fixedBase = True (keeps ArticulationRootAPI on root)
# 2. Delete the rootJoint (free joint)
# 3. Optionally create a FixedJoint to attach to a specific surface
# CRITICAL: Do NOT move ArticulationRootAPI — it must stay on the root prim
# or the tensor API pattern '/World/Robot' will fail with
# "Pattern did not match any articulations".

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

# Per-object-class size buckets in meters. The "default" row handles
# unknown classes with sensible cube-like defaults. Tuned to match
# common Isaac Sim / industrial-robotics conventions: small cubes are
# 5cm (manipulation benchmark size), tables are 1.2m (workbench).

# robot-class → registry key. Anchors generic class language ('a manipulator',
# 'a humanoid', 'a wheeled robot') to the same name resolution that
# robot_wizard / import_robot already understand. Avoids the agent inventing
# random asset paths when it should be selecting a known-good default.

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

_COORD_LANDMARKS = {
    # Named anchor points — return position relative to a reference prim
    # (or world origin when no reference). Ordered most-specific first.
    "origin": "world",
    "world origin": "world",
    "center of stage": "world",
    "stage center": "world",
}

_RELATIONAL_PATTERN_RE = __import__("re").compile(
    r"(?P<factor>\d+(?:\.\d+)?)\s*[xX×]?\s*(?P<rel>times|x|×|the size of|larger than|smaller than|bigger than)?",
    __import__("re").IGNORECASE,
)

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

# Register the sensor generator

# ── Motion Planning (RMPflow / Lula) ─────────────────────────────────────────

# Robot config map: robot_type → (rmpflow_config_dir, robot_description_path, urdf_path, end_effector_frame)

# ── Asset Catalog Search ─────────────────────────────────────────────────────

# Robot name map (module-level copy for catalog indexing)

# ── Local Filesystem Search ──────────────────────────────────────────────────
# When the user references "this URDF" / "the STEP file you imported" without
# a path, the agent needs to discover local files. Without this tool the agent
# either asks the user (annoying) or generates ad-hoc glob.glob() code-patches
# (unguarded). This is a guarded discovery primitive scoped to known asset
# roots — not a general filesystem walker.
import os as _os_files
import glob as _glob_files
import fnmatch as _fnmatch_files

# Hard cap to stop the agent from triggering massive filesystem walks.
# Asset-relevant extensions only — refuse to surface secrets / source code.

# ── Nucleus Browse & Download ────────────────────────────────────────────────

# ── Scene Builder ────────────────────────────────────────────────────────────

# ── IsaacLab RL Training ─────────────────────────────────────────────────────

# ─── Vision tools — _get_viewport_bytes + _get_vision_provider migrated to handlers/_shared.py (Phase 14, 2026-05-13) ───



# ── Scene Package Export ─────────────────────────────────────────────────────
# Collects all approved code patches from the audit log for a session,
# then writes:  scene_setup.py, ros2_launch.py (if ROS2 nodes present),
# README.md, and a ros2_topics.yaml listing detected topics.

# ── Stage Analysis ───────────────────────────────────────────────────────────

# _detect_robot_type deleted as dead code — see line ~271 marker above.

# get_nav2_bridge_profile deleted as dead code (2026-05-13).
# Zero callers across service/, tests/, scripts/ via comprehensive grep.
# _NAV2_BRIDGE_PROFILES was migrated to handlers/ros2.py (Phase 8 wave 4).







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

# ══════════════════════════════════════════════════════════════════════
# Phase-12 toolkit — proximity sensor + teach/load pose + mode-driven
# pick-place controller. Built 2026-04-19 after conveyor_pick_place
# template surfaced these gaps across ML-researcher, industrial, and
# vision personas.
# ══════════════════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════════════════
# Mode-specific generators for setup_pick_place_controller
# ══════════════════════════════════════════════════════════════════════

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

# ══════════════════════════════════════════════════════════════════════
# Controller matrix — availability probe (FAS 4)
# ══════════════════════════════════════════════════════════════════════

# === Phase 6 M4 — cuMotion-as-MoveIt2 ===
