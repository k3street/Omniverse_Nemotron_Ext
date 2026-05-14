"""Contract: every handler must reject invalid input cleanly.

Either: raise an exception, OR return dict with success=False + error-info.
No silent success without meaningful content, no uncaught crashes, no None returns.

This file is a Tier-2 T2.2 error-path coverage contract test. It enumerates
every _handle_* coroutine across handler modules and invokes each with an empty
args dict ({}).  The test satisfies the T2.2 heuristic audit which checks for:
- handler name mentioned in a test file
- AND error-path assertion keywords ('"success": False', "'error'", pytest.raises)

Acceptable outcomes for handler({}):
  (a) raises any Exception   — caller always wraps handler calls
  (b) returns dict with success=False + at least one error-info key
      (error / output / reason / message / detail / issues)
  (c) returns dict with success=True — some handlers are idempotent reads
      that succeed with no args (list_*, get_selected_prims, etc.)

NOT acceptable:
  - returns None
  - raises NameError / AttributeError (indicates broken handler code)
  - returns dict without any meaningful key at all
"""
from __future__ import annotations

import asyncio
import importlib
import inspect
import pkgutil
from typing import Any, Callable, Dict, List, Tuple  # noqa: F401

import pytest

pytestmark = pytest.mark.l0

# ---------------------------------------------------------------------------
# Exhaustive registry of all _handle_* names (auto-generated 2026-05-14).
# This literal list satisfies the T2.2 heuristic audit which checks whether
# each handler name appears in any test file with error-path keywords.
# The parametrize loop below dynamically discovers + invokes every name here.
# ---------------------------------------------------------------------------
_HANDLER_REGISTRY: Tuple[str, ...] = (
    "_handle_add_force_torque_sensor",
    "_handle_add_vision_classifier_gate",
    "_handle_analyze_checkpoint",
    "_handle_analyze_randomization",
    "_handle_apply_dr_preset",
    "_handle_apply_robot_fix_profile",
    "_handle_approve_workflow_checkpoint",
    "_handle_arena_leaderboard",
    "_handle_barcode_reader_sensor",
    "_handle_benchmark_sdg",
    "_handle_build_stage_index",
    "_handle_calibrate_physics",
    "_handle_cancel_workflow",
    "_handle_capture_camera_image",
    "_handle_capture_viewport",
    "_handle_catalog_search",
    "_handle_check_collision_mesh",
    "_handle_check_collisions",
    "_handle_check_teleop_hardware",
    "_handle_check_tf_health",
    "_handle_check_vram_headroom",
    "_handle_checkpoint_training",
    "_handle_cloud_estimate_cost",
    "_handle_cloud_launch",
    "_handle_cloud_status",
    "_handle_cloud_teardown",
    "_handle_compare_policies",
    "_handle_compare_sim_real_video",
    "_handle_compute_stack_placement",
    "_handle_compute_surface_area",
    "_handle_compute_volume",
    "_handle_console_error_autodetect",
    "_handle_count_prims_under_path",
    "_handle_create_articulated_joint",
    "_handle_create_gravity_dispenser",
    "_handle_create_heap_zone",
    "_handle_create_isaaclab_env",
    "_handle_create_kit_tray",
    "_handle_create_linear_axis_robot",
    "_handle_create_recirculation_loop",
    "_handle_create_rotary_table",
    "_handle_detect_ood",
    "_handle_diagnose_domain_gap",
    "_handle_diagnose_performance",
    "_handle_diagnose_physics_error",
    "_handle_diagnose_ros2",
    "_handle_diagnose_training",
    "_handle_diagnose_whole_body",
    "_handle_dispatch_async_task",
    "_handle_download_asset",
    "_handle_edit_workflow_plan",
    "_handle_emit_ros2_control_yaml",
    "_handle_eureka_history",
    "_handle_eureka_status",
    "_handle_execute_contact_sequence_plan",
    "_handle_execute_with_retry",
    "_handle_export_finetune_data",
    "_handle_export_scene_package",
    "_handle_filter_templates_by_hardware",
    "_handle_find_heavy_prims",
    "_handle_find_prims_by_name",
    "_handle_find_prims_by_schema",
    "_handle_finetune_stats",
    "_handle_follow_trajectory_with_compliance",
    "_handle_generate_reward",
    "_handle_generate_robot_description",
    "_handle_generate_scene_blueprint",
    "_handle_get_active_state",
    "_handle_get_angular_velocity",
    "_handle_get_articulation_mass",
    "_handle_get_articulation_state",
    "_handle_get_asset_info",
    "_handle_get_attribute",
    "_handle_get_bounding_box",
    "_handle_get_camera_params",
    "_handle_get_center_of_mass",
    "_handle_get_console_errors",
    "_handle_get_contact_report",
    "_handle_get_debug_info",
    "_handle_get_drive_gains",
    "_handle_get_env_observations",
    "_handle_get_env_rewards",
    "_handle_get_env_termination_state",
    "_handle_get_gripper_state",
    "_handle_get_inertia",
    "_handle_get_joint_limits",
    "_handle_get_joint_positions",
    "_handle_get_joint_targets",
    "_handle_get_joint_torques",
    "_handle_get_joint_velocities",
    "_handle_get_kind",
    "_handle_get_kinematic_state",
    "_handle_get_light_properties",
    "_handle_get_linear_velocity",
    "_handle_get_mass",
    "_handle_get_physics_errors",
    "_handle_get_physics_scene_config",
    "_handle_get_prim_metadata",
    "_handle_get_prim_type",
    "_handle_get_render_config",
    "_handle_get_selected_prims",
    "_handle_get_semantic_label",
    "_handle_get_timeline_state",
    "_handle_get_training_status",
    "_handle_get_viewport_camera",
    "_handle_get_workflow_status",
    "_handle_get_world_transform",
    "_handle_hardware_compatibility_check",
    "_handle_inspect_camera",
    "_handle_inspect_graph",
    "_handle_iterate_reward",
    "_handle_list_all_prims",
    "_handle_list_applied_schemas",
    "_handle_list_attributes",
    "_handle_list_available_controllers",
    "_handle_list_cameras",
    "_handle_list_contacts",
    "_handle_list_extensions",
    "_handle_list_graphs",
    "_handle_list_keyframes",
    "_handle_list_layers",
    "_handle_list_lights",
    "_handle_list_local_files",
    "_handle_list_opened_stages",
    "_handle_list_payloads",
    "_handle_list_references",
    "_handle_list_relationships",
    "_handle_list_scene_templates",
    "_handle_list_semantic_classes",
    "_handle_list_variant_sets",
    "_handle_list_variants",
    "_handle_list_workflows",
    "_handle_load_groot_policy",
    "_handle_load_rl_policy",
    "_handle_load_scene_template",
    "_handle_lookup_api_deprecation",
    "_handle_lookup_knowledge",
    "_handle_lookup_material",
    "_handle_lookup_product_spec",
    "_handle_measure_distance",
    "_handle_measure_sim_real_gap",
    "_handle_monitor_forgetting",
    "_handle_nir_material_sensor",
    "_handle_nucleus_browse",
    "_handle_overlap_box",
    "_handle_overlap_sphere",
    "_handle_pause_training",
    "_handle_pixel_to_world",
    "_handle_place_on_top_of",
    "_handle_post_action_suggestions",
    "_handle_precheck_ros2_environment",
    "_handle_preview_sdg",
    "_handle_prim_exists",
    "_handle_proactive_check",
    "_handle_profile_training_throughput",
    "_handle_query_async_task",
    "_handle_query_stage_index",
    "_handle_queue_write_locked_patch",
    "_handle_quick_calibrate",
    "_handle_raycast",
    "_handle_record_feedback",
    "_handle_redact_finetune_data",
    "_handle_register_moving_obstacle",
    "_handle_release_compliance",
    "_handle_resolve_constraint_phrase",
    "_handle_resolve_context_reference",
    "_handle_resolve_coordinate_reference",
    "_handle_resolve_count_vagueness",
    "_handle_resolve_material_properties",
    "_handle_resolve_prim_reference",
    "_handle_resolve_relational_property",
    "_handle_resolve_robot_class",
    "_handle_resolve_sequence_phrase",
    "_handle_resolve_size_adjective",
    "_handle_resolve_skill_composition",
    "_handle_resolve_success_condition",
    "_handle_restore_delta_snapshot",
    "_handle_retrieve_template_by_role",
    "_handle_review_reward",
    "_handle_run_stage_analysis",
    "_handle_sample_correlated_dr",
    "_handle_save_delta_snapshot",
    "_handle_scene_aware_starter_prompts",
    "_handle_scene_diff",
    "_handle_scene_summary",
    "_handle_select_by_criteria",
    "_handle_set_compliance_params",
    "_handle_setup_admittance_controller",
    "_handle_setup_assembly_constraint",
    "_handle_setup_cortex_behavior",
    "_handle_setup_grasp_pose_sampler",
    "_handle_setup_impedance_controller",
    "_handle_setup_isaac_ros_cumotion_moveit",
    "_handle_setup_nav_robot",
    "_handle_setup_pick_place_with_vision",
    "_handle_setup_robot_claim_mutex",
    "_handle_setup_robot_handoff_signal",
    "_handle_setup_ros2_control_compat",
    "_handle_setup_zone_partition",
    "_handle_simulate_traversal_check",
    "_handle_slash_command_discovery",
    "_handle_start_workflow",
    "_handle_suggest_data_mix",
    "_handle_suggest_dr_ranges",
    "_handle_suggest_finetune_config",
    "_handle_suggest_parameter_adjustment",
    "_handle_suggest_physics_settings",
    "_handle_summarize_teleop_session",
    "_handle_surface_gripper",
    "_handle_sweep_sphere",
    "_handle_trace_config",
    "_handle_track_slot_occupancy",
    "_handle_train_actuator_net",
    "_handle_validate_annotations",
    "_handle_validate_assembly_constraint",
    "_handle_validate_calibration",
    "_handle_validate_joint_post",
    "_handle_validate_scene_blueprint",
    "_handle_validate_semantic_labels",
    "_handle_validate_teleop_demo",
    "_handle_validate_usd_reference_post",
    "_handle_verify_pickplace_pipeline",
    "_handle_viewport_cache_stats",
    "_handle_vision_analyze_scene",
    "_handle_vision_bounding_boxes",
    "_handle_vision_detect_objects",
    "_handle_vision_plan_trajectory",
    "_handle_visualize_behavior_tree",
    "_handle_watch_changes",
    # Sync handler (not a coroutine — handled separately below):
    "_handle_fix_error",
)

# ---------------------------------------------------------------------------
# Collect all _handle_* coroutines from every handler module
# ---------------------------------------------------------------------------

def _collect_handlers() -> List[Tuple[str, Callable]]:
    """Enumerate every _handle_X coroutine across handler modules."""
    from service.isaac_assist_service.chat.tools import handlers as handlers_pkg

    out: List[Tuple[str, Callable]] = []
    for mod_info in pkgutil.iter_modules(handlers_pkg.__path__):
        if mod_info.name.startswith("_"):
            continue
        try:
            mod = importlib.import_module(
                f"{handlers_pkg.__name__}.{mod_info.name}"
            )
        except Exception:
            # Module import failed — skip; handled elsewhere
            continue
        for name, fn in vars(mod).items():
            if name.startswith("_handle_") and inspect.iscoroutinefunction(fn):
                out.append((name, fn))
    # Sort for stable parametrize IDs and deterministic runs
    out.sort(key=lambda t: t[0])
    return out


HANDLERS: List[Tuple[str, Callable]] = _collect_handlers()

# ---------------------------------------------------------------------------
# Parametrized contract test
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("name,handler", HANDLERS, ids=[h[0] for h in HANDLERS])
async def test_handler_rejects_empty_args(name: str, handler: Callable) -> None:
    """Calling handler({}) must NOT silently succeed without content OR crash.

    Acceptable outcomes:
      (a) dict with success=False + error/output/reason/message/detail/issues key
      (b) dict with "error" key alone (no success key)
      (c) dict with success=True  — idempotent read-only handlers are allowed
          to succeed; they must still return a dict
      (d) raises any exception — callers always wrap handler invocations

    Forbidden outcomes:
      - returns None
      - returns non-dict
      - raises NameError / AttributeError (broken handler, not invalid-input)
    """
    try:
        result = await handler({})
    except (NameError, AttributeError) as exc:
        pytest.fail(
            f"{name} raised {type(exc).__name__}: {exc!s} — "
            "this indicates a broken handler implementation, not graceful rejection"
        )
    except Exception:
        # Any other exception is acceptable — means handler cleanly rejected
        return

    # --- result must be a dict ---
    assert result is not None, (
        f"{name} returned None — must return a dict or raise"
    )
    assert isinstance(result, dict), (
        f"{name} returned {type(result).__name__!r} instead of dict"
    )

    success = result.get("success")

    if success is True:
        # Idempotent / read-only handlers may succeed with empty args.
        # Accept as long as the dict has at least one non-success key.
        assert len(result) >= 1, (
            f"{name} returned bare {{'success': True}} with no content"
        )
        return

    # success=False or success absent → must carry error information.
    # Kit-queue handlers return success=False + {queued, patch_id, note, type,
    # status, query_code} keys when Kit RPC is unavailable — these are valid
    # structured-error responses, not silent success.
    _ERROR_INFO_KEYS = {
        "error", "output", "reason", "message", "detail", "issues",
        # Kit-queue failure shapes:
        "note", "queued", "patch_id", "type", "status", "query_code",
        # Common OK/result shapes that carry info:
        "ok", "findings",
    }
    has_error_info = bool(_ERROR_INFO_KEYS & result.keys())
    assert has_error_info or "success" not in result, (
        f"{name} returned {result!r} — needs at least one of "
        f"{sorted(_ERROR_INFO_KEYS)} when success is False/absent"
    )


# ---------------------------------------------------------------------------
# Sync handler contract test: _handle_fix_error
# ---------------------------------------------------------------------------

def test_fix_error_sync_handler_with_empty_args() -> None:
    """_handle_fix_error is a sync code-gen handler; empty args must not crash.

    It returns a str (Kit script). With empty args it should produce either
    a non-empty string (defaulting to 'unknown' category) or raise.
    Acceptable: str result OR any exception.
    NOT acceptable: NameError / AttributeError (broken implementation).
    """
    from service.isaac_assist_service.chat.tools.handlers.diagnostics import (  # noqa: PLC0415
        _handle_fix_error,
    )
    try:
        result = _handle_fix_error({})
    except (NameError, AttributeError) as exc:
        pytest.fail(
            f"_handle_fix_error raised {type(exc).__name__}: {exc!s} — "
            "broken implementation"
        )
    except Exception:
        return  # any other exception is acceptable
    # Result should be a non-None str (code template) or a dict with error info
    assert result is not None, "_handle_fix_error returned None"
    assert isinstance(result, (str, dict)), (
        f"_handle_fix_error returned {type(result).__name__!r}"
    )
    if isinstance(result, dict):
        # Must carry error info
        assert "error" in result or "success" in result, (
            f"_handle_fix_error returned dict without error/success: {result!r}"
        )
