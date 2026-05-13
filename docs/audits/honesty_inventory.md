# Honesty Inventory — Phase 47b scanner

Generated: `2026-05-13T11:53:27.214507+00:00`
Handler directory: `service/isaac_assist_service/chat/tools/handlers`

## Summary

- **Critical** findings: 440
- **Warn** findings: 3
- **Info** findings: 0
- **Clean modules**: 0 of 17

Severity definitions:
- **critical**: function has no `success` key in any returned dict, or returns a bare string. Likely silent failure surface.
- **warn**: function returns `None` or has bare `return` paths — caller cannot distinguish from intentional empty result.
- **info**: low-confidence heuristic hit; review recommended.

## Per-module findings

### `animation.py` — 5 findings

**CRITICAL** (5):
- `_gen_set_timeline_range` — tags: no_success_key
- `_gen_set_keyframe` — tags: no_success_key
- `_gen_play_animation` — tags: no_success_key
- `_gen_create_audio_prim` — tags: no_success_key
- `_gen_set_audio_property` — tags: no_success_key

### `arena.py` — 5 findings

**CRITICAL** (5):
- `_arena_env_id` — tags: no_success_key
- `_gen_create_arena` — tags: no_success_key
- `_gen_create_arena_variant` — tags: no_success_key
- `_gen_run_arena_benchmark` — tags: no_success_key
- `_handle_arena_leaderboard` — tags: no_success_key

### `diagnostics.py` — 54 findings

**CRITICAL** (53):
- `_lazy_execute_tool_call` — tags: no_success_key
- `_per_joint_rmse` — tags: no_success_key
- `_load_trajectory_for_gap` — tags: returns_none_literal, no_success_key
- `_augment_verify_with_feasibility` — tags: no_success_key
- `_analyze_performance` — tags: no_success_key
- `_detect_used_vram_gb` — tags: returns_none_literal, no_success_key
- `_gen_debug_draw` — tags: no_success_key
- `_gen_check_physics_health` — tags: no_success_key
- `_gen_check_singularity` — tags: no_success_key
- `_gen_monitor_joint_effort` — tags: no_success_key
- `_gen_debug_graph` — tags: no_success_key
- `_gen_preflight_check` — tags: no_success_key
- `_gen_visualize_clearance` — tags: no_success_key
- `_gen_check_path_clearance` — tags: no_success_key
- `_gen_visualize_collision_mesh` — tags: no_success_key
- `_gen_visualize_forces` — tags: no_success_key
- `_gen_highlight_prim` — tags: no_success_key
- `_gen_sim_control` — tags: string_return, no_success_key
- `_gen_show_workspace` — tags: no_success_key
- `_gen_build_stage_index` — tags: no_success_key
- `_gen_enable_extension` — tags: no_success_key
- `_gen_create_broken_scene` — tags: no_success_key
- `_gen_enable_deterministic_mode` — tags: no_success_key
- `_gen_set_clearance_monitor` — tags: no_success_key
- `_gen_configure_zmq_stream` — tags: no_success_key
- `_handle_check_collision_mesh` — tags: no_success_key
- `_handle_check_collisions` — tags: no_success_key
- `_handle_check_teleop_hardware` — tags: no_success_key
- `_handle_check_tf_health` — tags: no_success_key
- `_handle_check_vram_headroom` — tags: no_success_key
- `_handle_compare_sim_real_video` — tags: no_success_key
- `_handle_console_error_autodetect` — tags: no_success_key
- `_handle_diagnose_domain_gap` — tags: no_success_key
- `_handle_diagnose_performance` — tags: no_success_key
- `_handle_diagnose_physics_error` — tags: no_success_key
- `_handle_diagnose_whole_body` — tags: no_success_key
- `_handle_get_active_state` — tags: no_success_key
- `_handle_get_console_errors` — tags: no_success_key
- `_handle_get_debug_info` — tags: no_success_key
- `_handle_hardware_compatibility_check` — tags: no_success_key
- `_handle_measure_distance` — tags: no_success_key
- `_handle_measure_sim_real_gap` — tags: no_success_key
- `_handle_proactive_check` — tags: no_success_key
- `_handle_simulate_traversal_check` — tags: no_success_key
- `_handle_trace_config` — tags: bare_return, no_success_key
- `_handle_validate_annotations` — tags: no_success_key
- `_handle_validate_calibration` — tags: no_success_key
- `_handle_validate_scene_blueprint` — tags: no_success_key
- `_handle_validate_semantic_labels` — tags: no_success_key
- `_handle_validate_teleop_demo` — tags: no_success_key
- `_handle_verify_pickplace_pipeline` — tags: no_success_key
- `_handle_list_extensions` — tags: no_success_key
- `_handle_fix_error` — tags: no_success_key

**WARN** (1):
- `_trace_in_source` — tags: bare_return

### `physics.py` — 41 findings

**CRITICAL** (41):
- `_load_deformable_presets` — tags: no_success_key
- `_load_physics_materials` — tags: no_success_key
- `_normalize_material_name` — tags: no_success_key
- `_gen_set_physics_params` — tags: no_success_key
- `_gen_set_joint_targets` — tags: no_success_key
- `_gen_set_drive_gains` — tags: no_success_key
- `_gen_set_joint_limits` — tags: no_success_key
- `_gen_apply_physics_material` — tags: no_success_key
- `_gen_set_physics_scene_config` — tags: no_success_key
- `_gen_apply_force` — tags: no_success_key
- `_gen_set_joint_velocity_limit` — tags: no_success_key
- `_gen_deformable` — tags: no_success_key
- `_gen_deformable_body` — tags: no_success_key
- `_gen_deformable_surface` — tags: no_success_key
- `_gen_configure_self_collision` — tags: no_success_key
- `_gen_optimize_collision` — tags: no_success_key
- `_gen_simplify_collision` — tags: no_success_key
- `_gen_setup_contact_sensors` — tags: no_success_key
- `_gen_check_collision_mesh_code` — tags: no_success_key
- `_gen_fix_collision_mesh` — tags: no_success_key
- `_gen_set_linear_velocity` — tags: no_success_key
- `_gen_compute_convex_hull` — tags: no_success_key
- `_handle_get_articulation_state` — tags: no_success_key
- `_handle_get_physics_errors` — tags: no_success_key
- `_handle_get_joint_limits` — tags: no_success_key
- `_handle_get_contact_report` — tags: no_success_key
- `_handle_get_joint_targets` — tags: no_success_key
- `_handle_get_linear_velocity` — tags: no_success_key
- `_handle_get_angular_velocity` — tags: no_success_key
- `_handle_get_mass` — tags: no_success_key
- `_handle_get_inertia` — tags: no_success_key
- `_handle_get_physics_scene_config` — tags: no_success_key
- `_handle_get_kinematic_state` — tags: no_success_key
- `_handle_get_joint_positions` — tags: no_success_key
- `_handle_get_joint_velocities` — tags: no_success_key
- `_handle_get_joint_torques` — tags: no_success_key
- `_handle_get_drive_gains` — tags: no_success_key
- `_handle_get_articulation_mass` — tags: no_success_key
- `_handle_get_center_of_mass` — tags: no_success_key
- `_handle_lookup_material` — tags: no_success_key
- `_handle_suggest_physics_settings` — tags: no_success_key

### `pick_place.py` — 12 findings

**CRITICAL** (12):
- `_resolve_auto_target_source` — tags: no_success_key
- `_gen_setup_pick_place_controller` — tags: no_success_key
- `_gen_pick_place_builtin` — tags: no_success_key
- `_gen_setup_pick_place_ros2_bridge` — tags: no_success_key
- `_gen_pick_place_sensor_gated` — tags: no_success_key
- `_gen_pick_place_native` — tags: no_success_key
- `_gen_pick_place_spline` — tags: no_success_key
- `_gen_pick_place_curobo` — tags: no_success_key
- `_gen_pick_place_diffik` — tags: no_success_key
- `_gen_pick_place_osc` — tags: no_success_key
- `_gen_pick_place_fixed_poses` — tags: no_success_key
- `_gen_pick_place_ros2_cmd` — tags: no_success_key

### `rendering.py` — 8 findings

**CRITICAL** (8):
- `_gen_set_light_intensity` — tags: no_success_key
- `_gen_set_light_color` — tags: no_success_key
- `_gen_create_hdri_skydome` — tags: no_success_key
- `_gen_add_default_light` — tags: no_success_key
- `_gen_set_render_config` — tags: no_success_key
- `_gen_set_render_resolution` — tags: no_success_key
- `_gen_enable_post_process` — tags: no_success_key
- `_gen_set_environment_background` — tags: string_return, no_success_key

### `resolve.py` — 12 findings

**CRITICAL** (12):
- `_handle_resolve_count_vagueness` — tags: no_success_key
- `_handle_resolve_robot_class` — tags: no_success_key
- `_handle_resolve_material_properties` — tags: no_success_key
- `_handle_resolve_constraint_phrase` — tags: no_success_key
- `_handle_resolve_sequence_phrase` — tags: no_success_key
- `_handle_resolve_context_reference` — tags: no_success_key
- `_handle_resolve_coordinate_reference` — tags: no_success_key
- `_handle_resolve_relational_property` — tags: no_success_key
- `_handle_resolve_success_condition` — tags: no_success_key
- `_handle_resolve_skill_composition` — tags: no_success_key
- `_handle_resolve_size_adjective` — tags: no_success_key
- `_handle_resolve_prim_reference` — tags: no_success_key

### `robot.py` — 63 findings

**CRITICAL** (63):
- `_generate_calibration_script` — tags: no_success_key
- `_suggested_dr_ranges` — tags: no_success_key
- `_detect_robot_for_fix` — tags: returns_none_literal, no_success_key
- `_gen_anchor_robot` — tags: no_success_key
- `_gen_verify_import` — tags: no_success_key
- `_gen_robot_wizard` — tags: no_success_key
- `_gen_tune_gains` — tags: no_success_key
- `_gen_assemble_robot` — tags: no_success_key
- `_gen_create_gripper` — tags: no_success_key
- `_gen_create_wheeled_robot` — tags: no_success_key
- `_gen_navigate_to` — tags: no_success_key
- `_gen_create_conveyor` — tags: no_success_key
- `_gen_create_conveyor_track` — tags: no_success_key
- `_gen_create_bin` — tags: no_success_key
- `_gen_publish_robot_description` — tags: no_success_key
- `_gen_move_to_pose` — tags: no_success_key
- `_gen_plan_trajectory` — tags: no_success_key
- `_gen_set_motion_policy` — tags: no_success_key
- `_gen_solve_ik` — tags: no_success_key
- `_gen_grasp_object` — tags: no_success_key
- `_gen_define_grasp_pose` — tags: no_success_key
- `_gen_record_waypoints` — tags: no_success_key
- `_gen_start_teaching_mode` — tags: no_success_key
- `_gen_replay_trajectory` — tags: no_success_key
- `_gen_interpolate_trajectory` — tags: no_success_key
- `_gen_setup_whole_body_control` — tags: no_success_key
- `_gen_setup_rsi_from_demos` — tags: no_success_key
- `_gen_setup_multi_rate` — tags: no_success_key
- `_gen_record_trajectory` — tags: no_success_key
- `_gen_import_robot` — tags: string_return, no_success_key
- `_gen_teach_robot_pose` — tags: no_success_key
- `_gen_load_robot_pose` — tags: no_success_key
- `_gen_generate_occupancy_map` — tags: no_success_key
- `_gen_create_behavior` — tags: no_success_key
- `_gen_export_nav2_map` — tags: no_success_key
- `_handle_create_kit_tray` — tags: no_success_key
- `_handle_create_articulated_joint` — tags: no_success_key
- `_handle_create_rotary_table` — tags: no_success_key
- `_handle_register_moving_obstacle` — tags: no_success_key
- `_handle_create_gravity_dispenser` — tags: no_success_key
- `_handle_create_heap_zone` — tags: no_success_key
- `_handle_setup_cortex_behavior` — tags: no_success_key
- `_handle_setup_assembly_constraint` — tags: no_success_key
- `_handle_create_recirculation_loop` — tags: no_success_key
- `_handle_create_linear_axis_robot` — tags: no_success_key
- `_handle_setup_grasp_pose_sampler` — tags: no_success_key
- `_handle_generate_robot_description` — tags: no_success_key
- `_handle_apply_robot_fix_profile` — tags: no_success_key
- `_handle_calibrate_physics` — tags: no_success_key
- `_handle_quick_calibrate` — tags: no_success_key
- `_handle_get_gripper_state` — tags: no_success_key
- `_handle_setup_isaac_ros_cumotion_moveit` — tags: no_success_key
- `_handle_setup_pick_place_with_vision` — tags: no_success_key
- `_handle_track_slot_occupancy` — tags: no_success_key
- `_handle_setup_robot_handoff_signal` — tags: no_success_key
- `_handle_setup_robot_claim_mutex` — tags: no_success_key
- `_handle_surface_gripper` — tags: no_success_key
- `_handle_setup_zone_partition` — tags: no_success_key
- `_handle_setup_nav_robot` — tags: no_success_key
- `_handle_visualize_behavior_tree` — tags: no_success_key
- `_handle_setup_ros2_control_compat` — tags: no_success_key
- `_handle_place_on_top_of` — tags: no_success_key
- `_handle_list_available_controllers` — tags: no_success_key

### `ros2.py` — 9 findings

**CRITICAL** (9):
- `_gen_show_tf_tree` — tags: no_success_key
- `_gen_configure_ros2_bridge` — tags: string_return, no_success_key
- `_gen_fix_ros2_qos` — tags: no_success_key
- `_gen_configure_ros2_time` — tags: string_return, no_success_key
- `_gen_setup_ros2_bridge` — tags: no_success_key
- `_gen_replay_rosbag` — tags: no_success_key
- `_handle_diagnose_ros2` — tags: no_success_key
- `_handle_emit_ros2_control_yaml` — tags: no_success_key
- `_handle_precheck_ros2_environment` — tags: no_success_key

### `scene_authoring.py` — 86 findings

**CRITICAL** (85):
- `_parse_unified_diff_to_changes` — tags: bare_return, no_success_key
- `_summarize_changes` — tags: string_return, no_success_key
- `_score_prim_for_query` — tags: no_success_key
- `_neighbour_paths` — tags: no_success_key
- `_build_select_by_criteria_code` — tags: no_success_key
- `_detect_template` — tags: no_success_key
- `_gen_create_prim` — tags: no_success_key
- `_gen_delete_prim` — tags: no_success_key
- `_gen_set_attribute` — tags: no_success_key
- `_gen_add_reference` — tags: no_success_key
- `_gen_assign_material` — tags: no_success_key
- `_gen_teleport_prim` — tags: no_success_key
- `_gen_apply_api_schema` — tags: no_success_key
- `_gen_clone_prim` — tags: no_success_key
- `_gen_create_material` — tags: no_success_key
- `_gen_create_omnigraph` — tags: no_success_key
- `_gen_batch_apply_operation` — tags: string_return, no_success_key
- `_gen_optimize_scene` — tags: no_success_key
- `_gen_scatter_on_surface` — tags: no_success_key
- `_gen_save_delta_snapshot` — tags: no_success_key
- `_gen_restore_delta_snapshot` — tags: no_success_key
- `_gen_batch_delete_prims` — tags: string_return, no_success_key
- `_gen_batch_set_attributes` — tags: string_return, no_success_key
- `_gen_add_sublayer` — tags: no_success_key
- `_gen_set_edit_target` — tags: no_success_key
- `_gen_flatten_layers` — tags: no_success_key
- `_gen_add_usd_reference` — tags: no_success_key
- `_gen_load_payload` — tags: no_success_key
- `_gen_save_stage` — tags: no_success_key
- `_gen_open_stage` — tags: no_success_key
- `_gen_export_stage` — tags: no_success_key
- `_gen_add_node` — tags: no_success_key
- `_gen_connect_nodes` — tags: no_success_key
- `_gen_set_graph_variable` — tags: no_success_key
- `_gen_delete_node` — tags: no_success_key
- `_gen_bulk_set_attribute` — tags: no_success_key
- `_gen_bulk_apply_schema` — tags: no_success_key
- `_gen_group_prims` — tags: no_success_key
- `_gen_duplicate_prims` — tags: no_success_key
- `_gen_set_variant` — tags: no_success_key
- `_gen_set_prim_metadata` — tags: no_success_key
- `_gen_remove_semantic_label` — tags: no_success_key
- `_gen_assign_class_to_children` — tags: no_success_key
- `_gen_merge_meshes` — tags: no_success_key
- `_gen_create_graph` — tags: no_success_key
- `_gen_explain_graph` — tags: no_success_key
- `_gen_activate_area` — tags: no_success_key
- `_handle_list_all_prims` — tags: no_success_key
- `_handle_get_attribute` — tags: no_success_key
- `_handle_get_world_transform` — tags: no_success_key
- `_handle_get_bounding_box` — tags: no_success_key
- `_handle_prim_exists` — tags: no_success_key
- `_handle_list_attributes` — tags: no_success_key
- `_handle_list_applied_schemas` — tags: no_success_key
- `_handle_get_prim_metadata` — tags: no_success_key
- `_handle_get_prim_type` — tags: no_success_key
- `_handle_find_prims_by_schema` — tags: no_success_key
- `_handle_find_prims_by_name` — tags: no_success_key
- `_handle_get_kind` — tags: no_success_key
- `_handle_get_semantic_label` — tags: no_success_key
- `_handle_get_asset_info` — tags: no_success_key
- `_handle_get_selected_prims` — tags: no_success_key
- `_handle_scene_summary` — tags: no_success_key
- `_handle_run_stage_analysis` — tags: no_success_key
- `_handle_scene_diff` — tags: no_success_key
- `_handle_build_stage_index` — tags: no_success_key
- `_handle_query_stage_index` — tags: no_success_key
- `_handle_count_prims_under_path` — tags: no_success_key
- `_handle_list_relationships` — tags: no_success_key
- `_handle_list_layers` — tags: no_success_key
- `_handle_list_variant_sets` — tags: no_success_key
- `_handle_list_variants` — tags: no_success_key
- `_handle_list_semantic_classes` — tags: no_success_key
- `_handle_list_references` — tags: no_success_key
- `_handle_list_payloads` — tags: no_success_key
- `_handle_select_by_criteria` — tags: no_success_key
- `_handle_list_opened_stages` — tags: no_success_key
- `_handle_compute_stack_placement` — tags: no_success_key
- `_handle_compute_surface_area` — tags: no_success_key
- `_handle_compute_volume` — tags: no_success_key
- `_handle_find_heavy_prims` — tags: no_success_key
- `_handle_inspect_graph` — tags: no_success_key
- `_handle_list_graphs` — tags: no_success_key
- `_handle_save_delta_snapshot` — tags: no_success_key
- `_handle_restore_delta_snapshot` — tags: no_success_key

**WARN** (1):
- `_flush_pending` — tags: bare_return

### `scene_blueprints.py` — 19 findings

**CRITICAL** (19):
- `_build_asset_index` — tags: no_success_key
- `_load_template_manifests` — tags: no_success_key
- `_load_sensor_specs` — tags: no_success_key
- `_gen_build_scene_from_blueprint` — tags: string_return, no_success_key
- `_gen_load_scene_template` — tags: string_return, no_success_key
- `_gen_export_template` — tags: no_success_key
- `_gen_import_template` — tags: no_success_key
- `_handle_lookup_api_deprecation` — tags: no_success_key
- `_handle_lookup_knowledge` — tags: no_success_key
- `_handle_lookup_product_spec` — tags: no_success_key
- `_handle_catalog_search` — tags: no_success_key
- `_handle_nucleus_browse` — tags: no_success_key
- `_handle_download_asset` — tags: no_success_key
- `_handle_list_local_files` — tags: no_success_key
- `_handle_filter_templates_by_hardware` — tags: no_success_key
- `_handle_list_scene_templates` — tags: no_success_key
- `_handle_load_scene_template` — tags: no_success_key
- `_handle_generate_scene_blueprint` — tags: no_success_key
- `_handle_export_scene_package` — tags: no_success_key

### `sdg.py` — 12 findings

**CRITICAL** (12):
- `_gen_configure_sdg` — tags: no_success_key
- `_gen_create_sdg_pipeline` — tags: no_success_key
- `_gen_add_domain_randomizer` — tags: no_success_key
- `_gen_export_dataset` — tags: no_success_key
- `_gen_configure_differential_sdg` — tags: no_success_key
- `_gen_configure_coco_yolo_writer` — tags: no_success_key
- `_gen_enforce_class_balance` — tags: no_success_key
- `_gen_configure_correlated_dr` — tags: no_success_key
- `_gen_add_latency_randomization` — tags: no_success_key
- `_gen_preview_dr` — tags: no_success_key
- `_handle_preview_sdg` — tags: no_success_key
- `_handle_benchmark_sdg` — tags: no_success_key

### `sensors.py` — 15 findings

**CRITICAL** (15):
- `_gen_add_sensor` — tags: no_success_key
- `_gen_inspect_camera` — tags: no_success_key
- `_gen_configure_camera` — tags: no_success_key
- `_gen_set_camera_params` — tags: no_success_key
- `_gen_set_camera_look_at` — tags: no_success_key
- `_gen_add_proximity_sensor` — tags: no_success_key
- `_handle_add_force_torque_sensor` — tags: no_success_key
- `_handle_add_vision_classifier_gate` — tags: no_success_key
- `_handle_barcode_reader_sensor` — tags: no_success_key
- `_handle_list_contacts` — tags: no_success_key
- `_handle_nir_material_sensor` — tags: no_success_key
- `_handle_overlap_box` — tags: no_success_key
- `_handle_overlap_sphere` — tags: no_success_key
- `_handle_raycast` — tags: no_success_key
- `_handle_sweep_sphere` — tags: no_success_key

### `teleop.py` — 8 findings

**CRITICAL** (8):
- `_gen_start_teleop_session` — tags: no_success_key
- `_gen_configure_teleop_mapping` — tags: no_success_key
- `_gen_record_teleop_demo` — tags: no_success_key
- `_gen_stop_teleop_session` — tags: string_return, no_success_key
- `_gen_teleop_safety_config` — tags: no_success_key
- `_gen_export_teleop_mapping` — tags: no_success_key
- `_gen_generate_teleop_watchdog_script` — tags: no_success_key
- `_handle_summarize_teleop_session` — tags: no_success_key

### `training.py` — 48 findings

**CRITICAL** (48):
- `_format_component_metrics` — tags: string_return, no_success_key
- `_generate_isaaclab_env_code` — tags: no_success_key
- `_build_mutation_prompt` — tags: no_success_key
- `_read_tb_scalars` — tags: no_success_key
- `_read_checkpoint_action_std` — tags: returns_none_literal, no_success_key
- `_generate_actuator_net_script` — tags: no_success_key
- `_gen_launch_training` — tags: no_success_key
- `_gen_evaluate_reward` — tags: no_success_key
- `_gen_evaluate_groot` — tags: no_success_key
- `_gen_finetune_groot` — tags: no_success_key
- `_gen_clone_envs` — tags: no_success_key
- `_gen_setup_loco_manipulation_training` — tags: no_success_key
- `_gen_export_policy` — tags: no_success_key
- `_gen_cloud_download_results` — tags: no_success_key
- `_gen_create_calibration_experiment` — tags: no_success_key
- `_gen_eval_harness` — tags: no_success_key
- `_handle_create_isaaclab_env` — tags: no_success_key
- `_handle_generate_reward` — tags: no_success_key
- `_handle_iterate_reward` — tags: no_success_key
- `_handle_eureka_status` — tags: no_success_key
- `_handle_load_groot_policy` — tags: no_success_key
- `_handle_compare_policies` — tags: no_success_key
- `_handle_export_finetune_data` — tags: no_success_key
- `_handle_finetune_stats` — tags: no_success_key
- `_handle_analyze_randomization` — tags: no_success_key
- `_handle_apply_dr_preset` — tags: no_success_key
- `_handle_detect_ood` — tags: no_success_key
- `_handle_analyze_checkpoint` — tags: no_success_key
- `_handle_get_training_status` — tags: no_success_key
- `_handle_get_env_observations` — tags: no_success_key
- `_handle_get_env_rewards` — tags: no_success_key
- `_handle_checkpoint_training` — tags: no_success_key
- `_handle_cloud_estimate_cost` — tags: no_success_key
- `_handle_cloud_launch` — tags: no_success_key
- `_handle_cloud_status` — tags: no_success_key
- `_handle_cloud_teardown` — tags: no_success_key
- `_handle_diagnose_training` — tags: no_success_key
- `_handle_load_rl_policy` — tags: no_success_key
- `_handle_monitor_forgetting` — tags: no_success_key
- `_handle_pause_training` — tags: no_success_key
- `_handle_profile_training_throughput` — tags: no_success_key
- `_handle_redact_finetune_data` — tags: no_success_key
- `_handle_review_reward` — tags: no_success_key
- `_handle_suggest_data_mix` — tags: no_success_key
- `_handle_suggest_dr_ranges` — tags: no_success_key
- `_handle_suggest_finetune_config` — tags: no_success_key
- `_handle_suggest_parameter_adjustment` — tags: no_success_key
- `_handle_train_actuator_net` — tags: no_success_key

### `vision.py` — 27 findings

**CRITICAL** (27):
- `_gen_set_viewport_camera` — tags: no_success_key
- `_gen_render_video` — tags: no_success_key
- `_gen_quick_demo` — tags: no_success_key
- `_gen_record_demo_video` — tags: no_success_key
- `_gen_extract_attention_maps` — tags: no_success_key
- `_gen_set_semantic_label` — tags: no_success_key
- `_gen_set_render_mode` — tags: no_success_key
- `_gen_focus_viewport_on` — tags: no_success_key
- `_get_viewport_bytes` — tags: no_success_key
- `_get_vision_provider` — tags: no_success_key
- `_parse_last_json_line` — tags: returns_none_literal, no_success_key
- `_handle_capture_viewport` — tags: no_success_key
- `_handle_capture_camera_image` — tags: no_success_key
- `_handle_inspect_camera` — tags: no_success_key
- `_handle_pixel_to_world` — tags: no_success_key
- `_handle_list_lights` — tags: no_success_key
- `_handle_get_light_properties` — tags: no_success_key
- `_handle_list_cameras` — tags: no_success_key
- `_handle_get_camera_params` — tags: no_success_key
- `_handle_get_render_config` — tags: no_success_key
- `_handle_get_timeline_state` — tags: no_success_key
- `_handle_list_keyframes` — tags: no_success_key
- `_handle_get_viewport_camera` — tags: no_success_key
- `_handle_vision_detect_objects` — tags: no_success_key
- `_handle_vision_bounding_boxes` — tags: no_success_key
- `_handle_vision_plan_trajectory` — tags: no_success_key
- `_handle_vision_analyze_scene` — tags: no_success_key

### `workflow.py` — 19 findings

**CRITICAL** (18):
- `_wf_make_initial_plan` — tags: no_success_key
- `_wf_advance_phase` — tags: returns_none_literal, no_success_key
- `_handle_record_feedback` — tags: no_success_key
- `_handle_watch_changes` — tags: no_success_key
- `_handle_scene_aware_starter_prompts` — tags: no_success_key
- `_handle_slash_command_discovery` — tags: no_success_key
- `_handle_post_action_suggestions` — tags: no_success_key
- `_handle_queue_write_locked_patch` — tags: no_success_key
- `_handle_start_workflow` — tags: no_success_key
- `_handle_edit_workflow_plan` — tags: no_success_key
- `_handle_approve_workflow_checkpoint` — tags: no_success_key
- `_handle_cancel_workflow` — tags: no_success_key
- `_handle_get_workflow_status` — tags: no_success_key
- `_handle_list_workflows` — tags: no_success_key
- `_handle_execute_with_retry` — tags: no_success_key
- `_handle_dispatch_async_task` — tags: no_success_key
- `_handle_query_async_task` — tags: no_success_key
- `_wf_now_iso` — tags: no_success_key

**WARN** (1):
- `_async_task_runner` — tags: bare_return

## Clean modules

