"""
L0 tests for every CODE_GEN_HANDLER in tool_executor.py.
Each test:
  1. Passes valid arguments
  2. Verifies the returned code compiles (compile())
  3. Checks for expected imports / API calls
"""
import pytest

pytestmark = pytest.mark.l0

from service.isaac_assist_service.chat.tools.tool_executor import CODE_GEN_HANDLERS


# ---------------------------------------------------------------------------
# Helper: compile check
# ---------------------------------------------------------------------------

def _assert_valid_python(code: str, handler_name: str):
    """Verify the generated code is syntactically valid Python."""
    try:
        compile(code, f"<generated:{handler_name}>", "exec")
    except SyntaxError as e:
        pytest.fail(f"{handler_name} generated invalid Python:\n{e}\n\nCode:\n{code}")


# ---------------------------------------------------------------------------
# Test vectors — one dict per CODE_GEN_HANDLER
# Each entry: (handler_name, args_dict, expected_substrings)
# ---------------------------------------------------------------------------

_TEST_VECTORS = [
    (
        "create_prim",
        {"prim_path": "/World/Cube", "prim_type": "Cube"},
        ["import omni.usd", "DefinePrim", "Cube"],
    ),
    (
        "create_prim",
        {
            "prim_path": "/World/Sphere",
            "prim_type": "Sphere",
            "position": [1.0, 2.0, 3.0],
            "scale": [0.5, 0.5, 0.5],
            "rotation_euler": [0, 90, 0],
        },
        ["_safe_set_translate", "_safe_set_scale", "_safe_set_rotate_xyz"],
    ),
    (
        "delete_prim",
        {"prim_path": "/World/Old"},
        ["RemovePrim", "/World/Old"],
    ),
    (
        "set_attribute",
        {"prim_path": "/World/Cube", "attr_name": "radius", "value": 0.5},
        ["GetAttribute", "Set(", "radius"],
    ),
    (
        "add_reference",
        {"prim_path": "/World/Robot", "reference_path": "/assets/franka.usd"},
        ["AddReference", "franka.usd"],
    ),
    (
        "apply_api_schema",
        {"prim_path": "/World/Cube", "schema_name": "PhysicsRigidBodyAPI"},
        ["RigidBodyAPI", "Apply"],
    ),
    (
        "apply_api_schema",
        {"prim_path": "/World/Cube", "schema_name": "UnknownSchema"},
        ["ApplyAPISchemaCommand"],
    ),
    (
        "clone_prim",
        {"source_path": "/World/Src", "target_path": "/World/Dst"},
        ["CopySpec"],
    ),
    (
        "clone_prim",
        {"source_path": "/World/Src", "target_path": "/World/Dst", "count": 2, "spacing": 1.5},
        ["CopySpec", "for i in range(2)"],
    ),
    (
        "clone_prim",
        {"source_path": "/World/Src", "target_path": "/World/Dst", "count": 10, "spacing": 2.0},
        ["GridCloner", "clone("],
    ),
    (
        "clone_prim",
        {"source_path": "/World/Src", "target_path": "/World/Dst", "count": 10, "spacing": 2.0, "collision_filter": True},
        ["GridCloner", "filter_collisions"],
    ),
    (
        "create_deformable_mesh",
        {"prim_path": "/World/Cloth", "soft_body_type": "cloth"},
        ["PhysxSchema", "Deformable"],
    ),
    (
        "create_deformable_mesh",
        {"prim_path": "/World/Sponge", "soft_body_type": "sponge"},
        ["PhysxSchema", "Deformable"],
    ),
    (
        "create_omnigraph",
        {
            "graph_path": "/World/OG",
            "graph_type": "action_graph",
            "nodes": [{"name": "tick", "type": "omni.graph.action.OnPlaybackTick"}],
            "connections": [],
        },
        ["og.Controller.edit", "CREATE_NODES", "OnPlaybackTick"],
    ),
    (
        "create_material",
        {"material_path": "/World/Mat", "shader_type": "OmniPBR"},
        ["UsdShade.Material", "OmniPBR.mdl", "diffuse_color"],
    ),
    (
        "create_material",
        {
            "material_path": "/World/GlassMat",
            "shader_type": "OmniGlass",
            "diffuse_color": [0.9, 0.9, 1.0],
            "metallic": 0.0,
            "roughness": 0.1,
        },
        ["OmniGlass.mdl"],
    ),
    (
        "assign_material",
        {"prim_path": "/World/Cube", "material_path": "/World/Mat"},
        ["MaterialBindingAPI", "Bind"],
    ),
    (
        "sim_control",
        {"action": "play"},
        ["omni.timeline", "play()"],
    ),
    (
        "sim_control",
        {"action": "pause"},
        ["pause()"],
    ),
    (
        "sim_control",
        {"action": "stop"},
        ["stop()"],
    ),
    (
        "sim_control",
        {"action": "step", "step_count": 5},
        ["forward_one_frame", "range(5)"],
    ),
    (
        "sim_control",
        {"action": "reset"},
        ["stop()", "set_current_time(0)"],
    ),
    (
        "set_physics_params",
        {"gravity_magnitude": 9.81, "gravity_direction": [0, 0, -1]},
        ["UsdPhysics.Scene", "GravityMagnitude"],
    ),
    (
        "teleport_prim",
        {"prim_path": "/World/Robot", "position": [1, 2, 3]},
        ["_safe_set_translate", "1, 2, 3"],
    ),
    (
        "teleport_prim",
        {"prim_path": "/World/Robot", "position": [1, 2, 3], "rotation_euler": [0, 45, 0]},
        ["_safe_set_rotate_xyz"],
    ),
    (
        "set_joint_targets",
        {"articulation_path": "/World/Robot", "joint_name": "panda_joint1", "target_position": 0.5},
        ["DriveAPI", "TargetPosition"],
    ),
    (
        "import_robot",
        {"file_path": "/path/to/robot.usd", "format": "usd", "dest_path": "/World/Robot"},
        ["AddReference", "robot.usd"],
    ),
    (
        "import_robot",
        {"file_path": "robot.urdf", "format": "urdf", "dest_path": "/World/Robot"},
        ["URDFParseAndImportFile"],
    ),
    (
        "anchor_robot",
        {"robot_path": "/World/Franka"},
        ["fixedBase", "RemovePrim", "rootJoint"],
    ),
    (
        "anchor_robot",
        {
            "robot_path": "/World/Franka",
            "anchor_surface_path": "/World/Table",
            "base_link_name": "panda_link0",
        },
        ["FixedJoint", "Table", "body0", "body1"],
    ),
    (
        "set_viewport_camera",
        {"camera_path": "/World/Camera"},
        ["viewport", "camera_path"],
    ),
    (
        "configure_sdg",
        {"num_frames": 10, "output_dir": "/tmp/sdg"},
        ["replicator", "WriterRegistry", "run_until_complete"],
    ),
    (
        "add_sensor_to_prim",
        {"prim_path": "/World/Robot", "sensor_type": "camera"},
        ["UsdGeom.Camera", "FocalLength"],
    ),
    (
        "add_sensor_to_prim",
        {"prim_path": "/World/Robot", "sensor_type": "rtx_lidar"},
        ["LidarRtx"],
    ),
    (
        "add_sensor_to_prim",
        {"prim_path": "/World/Robot", "sensor_type": "imu"},
        ["IMUSensor"],
    ),
    (
        "add_sensor_to_prim",
        {"prim_path": "/World/Robot", "sensor_type": "contact_sensor"},
        ["ContactSensor"],
    ),
    (
        "move_to_pose",
        {
            "articulation_path": "/World/Franka",
            "target_position": [0.4, 0.0, 0.3],
            "robot_type": "franka",
        },
        # Default planner is RMPflow (reactive). Generated code uses
        # load_supported_motion_gen_config (the modern name; the older
        # load_supported_motion_policy_config no longer exists).
        ["RmpFlow", "set_end_effector_target", "apply_action",
         "load_supported_motion_gen_config", "SingleArticulation"],
    ),
    (
        "move_to_pose",
        {
            "articulation_path": "/World/Franka",
            "target_position": [0.4, 0.0, 0.3],
            "planner": "lula_rrt",
            "robot_type": "franka",
        },
        # Handler migrated from LulaRRTMotionPolicy to
        # LulaTaskSpaceTrajectoryGenerator (single-shot global planner)
        ["LulaTaskSpaceTrajectoryGenerator", "load_supported_lula_rrt_config"],
    ),
    (
        "plan_trajectory",
        {
            "articulation_path": "/World/Franka",
            "waypoints": [
                {"position": [0.4, 0.0, 0.3]},
                {"position": [0.4, 0.2, 0.3]},
            ],
            "robot_type": "franka",
        },
        # plan_trajectory uses the same Lula task-space generator and emits
        # compute_task_space_trajectory_from_points
        ["LulaTaskSpaceTrajectoryGenerator", "compute_task_space_trajectory_from_points"],
    ),
    (
        "build_scene_from_blueprint",
        {
            "blueprint": {
                "objects": [
                    {
                        "name": "Table",
                        "prim_type": "Cube",
                        "position": [0, 0, 0.5],
                        "rotation": [0, 0, 0],
                        "scale": [1, 1, 0.05],
                    }
                ]
            }
        },
        ["DefinePrim", "Cube", "_safe_set_translate"],
    ),
    (
        "fix_error",
        {"error_text": "CollisionAPI not applied, objects pass through each other"},
        ["import omni.usd", "CollisionAPI"],
    ),
    (
        "fix_error",
        {"error_text": "solver diverged and simulation is unstable"},
        ["import omni.usd", "Iteration"],
    ),
    (
        "batch_apply_operation",
        {
            "target_path": "/World/Robots",
            "operation": "apply_collision",
        },
        ["import omni.usd", "PrimRange"],
    ),
    (
        "launch_training",
        {"task": "Isaac-Reach-Franka-v0", "algo": "ppo", "num_steps": 100000},
        ["IsaacLab", "train"],
    ),
    # ----- qa-20-followup: drain _KNOWN_UNTESTED ratchet (batch 1) -----
    (
        "add_default_light",
        {},
        ["UsdLux.DomeLight.Define", "omni.usd.get_context().get_stage()"],
    ),
    (
        "enable_extension",
        {"ext_id": "omni.foo"},
        ["get_extension_manager", "omni.foo"],
    ),
    (
        "save_stage",
        {"path": "/tmp/scene_out.usd"},
        ["omni.usd.get_context()", "/tmp/scene_out.usd"],
    ),
    (
        "open_stage",
        {"path": "/tmp/scene_in.usd"},
        ["omni.usd.get_context()", "/tmp/scene_in.usd"],
    ),
    (
        "export_stage",
        {"path": "/tmp/x.usda", "format": "usda"},
        ["/tmp/x.usda", "usda"],
    ),
    (
        "focus_viewport_on",
        {"prim_path": "/World/Cube"},
        ["import omni.usd", "/World/Cube"],
    ),
    (
        "highlight_prim",
        {"prim_path": "/World/Hot"},
        ["/World/Hot"],
    ),
    (
        "group_prims",
        {"prim_paths": ["/World/A", "/World/B"], "group_name": "Grp"},
        ["import omni.usd", "Grp"],
    ),
    (
        "duplicate_prims",
        {"prim_paths": ["/World/A"], "offset": [1, 0, 0]},
        ["import omni.usd", "/World/A"],
    ),
    (
        "delete_node",
        {"graph_path": "/World/G", "node_name": "N1"},
        ["import omni.graph.core", "/World/G"],
    ),
    (
        "debug_graph",
        {"graph_path": "/World/G"},
        ["import omni.graph.core", "/World/G"],
    ),
    (
        "explain_graph",
        {"graph_path": "/World/G"},
        ["import omni.graph.core", "/World/G"],
    ),
    (
        "preflight_check",
        {},
        ["omni.usd.get_context().get_stage()", "issues"],
    ),
    (
        "flatten_layers",
        {"output_path": "/tmp/flat.usd"},
        ["omni.usd.get_context().get_stage()", "/tmp/flat.usd"],
    ),
    (
        "show_workspace",
        {"articulation_path": "/World/Robot"},
        ["debug_draw", "/World/Robot"],
    ),
    (
        "show_tf_tree",
        {},
        ["ROS_DISTRO"],
    ),
    (
        "teach_robot_pose",
        {"robot_path": "/World/R", "pose_name": "home"},
        ["/World/R", "home"],
    ),
    (
        "start_teleop_session",
        {"robot_path": "/World/R"},
        ["import omni.usd", "/World/R"],
    ),
    (
        "stop_teleop_session",
        {},
        ["Stop Teleop Session", "import omni.usd"],
    ),
    # ----- qa-20-followup: drain _KNOWN_UNTESTED ratchet (batch 2) -----
    (
        "set_keyframe",
        {"prim_path": "/W/Cube", "attr": "translate", "value": [1.0, 2.0, 3.0], "time": 10},
        ["omni.usd.get_context()", "/W/Cube"],
    ),
    (
        "set_camera_params",
        {"camera_path": "/W/Cam", "focal_length": 50},
        ["omni.usd.get_context()", "/W/Cam"],
    ),
    (
        "set_camera_look_at",
        {"camera_path": "/W/Cam", "target": [0.0, 0.0, 0.0]},
        ["import omni.usd", "/W/Cam"],
    ),
    (
        "set_render_resolution",
        {"width": 1920, "height": 1080},
        ["omni.kit.viewport.utility", "1920", "1080"],
    ),
    (
        "set_render_mode",
        {"mode": "path_traced"},
        ["carb.settings", "PathTracing"],
    ),
    (
        "set_light_intensity",
        {"light_path": "/W/L", "intensity": 1000},
        ["import omni.usd", "/W/L"],
    ),
    (
        "set_light_color",
        {"light_path": "/W/L", "rgb": [1.0, 0.5, 0.2]},
        ["import omni.usd", "/W/L"],
    ),
    (
        "set_drive_gains",
        {"joint_path": "/W/J", "kp": 500, "kd": 50},
        ["UsdPhysics", "/W/J"],
    ),
    (
        "set_joint_limits",
        {"joint_path": "/W/J", "lower": -1.57, "upper": 1.57},
        ["UsdPhysics", "/W/J"],
    ),
    (
        "set_linear_velocity",
        {"prim_path": "/W/B", "velocity": [0.0, 0.0, 0.5]},
        ["import omni.usd", "/W/B"],
    ),
    (
        "set_semantic_label",
        {"prim_path": "/W/C", "class_name": "cube"},
        ["Semantics", "/W/C"],
    ),
    (
        "remove_semantic_label",
        {"prim_path": "/W/C"},
        ["import omni.usd", "/W/C"],
    ),
    (
        "set_variant",
        {"prim_path": "/W/Robot", "variant_set": "Gripper", "variant": "Default"},
        ["omni.usd", "/W/Robot"],
    ),
    (
        "set_timeline_range",
        {"start": 0, "end": 100},
        ["omni.timeline", "100"],
    ),
    (
        "set_edit_target",
        {"layer_path": "/W/Layer.usda"},
        ["import omni.usd", "/W/Layer.usda"],
    ),
    (
        "set_environment_background",
        {"preset": "sky"},
        ["import omni.usd"],
    ),
    (
        "set_graph_variable",
        {"graph_path": "/W/G", "name": "x", "value": 1.0},
        ["import omni.graph.core", "/W/G"],
    ),
    (
        "set_audio_property",
        {"prim_path": "/W/A", "prop": "volume", "value": 0.5},
        ["UsdMedia", "/W/A"],
    ),
    (
        "set_prim_metadata",
        {"prim_path": "/W/C", "key": "mykey", "value": "myval"},
        ["import omni.usd", "/W/C"],
    ),
    (
        "set_render_config",
        {"renderer": "rtx", "samples_per_pixel": 8},
        ["import omni.usd", "/Render/Vars"],
    ),
    (
        "set_clearance_monitor",
        {"articulation_path": "/W/R", "min_distance": 0.1},
        ["import omni.usd", "/W/R"],
    ),
    (
        "visualize_clearance",
        {"articulation_path": "/W/R"},
        ["debug_draw", "/W/R"],
    ),
    (
        "visualize_collision_mesh",
        {"prim_path": "/W/B"},
        ["UsdPhysics", "/W/B"],
    ),
    (
        "visualize_forces",
        {"articulation_path": "/W/R"},
        ["import omni.usd", "/W/R"],
    ),
    (
        "teleop_safety_config",
        {"robot_path": "/W/R", "max_velocity": 1.0},
        ["Teleop Safety", "/W/R"],
    ),
    (
        "record_demo_video",
        {"output_path": "/tmp/v.mp4"},
        ["/tmp/v.mp4"],
    ),
    # ----- qa-20-followup: drain _KNOWN_UNTESTED ratchet (batch 3) -----
    (
        "activate_area",
        {"area_id": "A1", "prim_scope": "/W/Areas"},
        ["omni.usd.get_context()", "/W/Areas"],
    ),
    (
        "add_latency_randomization",
        {"mean_ms": 50, "std_ms": 10},
        ["latency randomization"],
    ),
    (
        "add_node",
        {"graph_path": "/W/G", "node_type": "omni.graph.nodes.Add", "name": "N1"},
        ["omni.graph.core", "/W/G"],
    ),
    (
        "add_sublayer",
        {"layer_path": "/tmp/sub.usda"},
        ["omni.usd", "/tmp/sub.usda"],
    ),
    (
        "add_usd_reference",
        {"prim_path": "/W/Ref", "usd_url": "/tmp/robot.usd"},
        ["omni.usd", "/W/Ref"],
    ),
    (
        "apply_force",
        {"prim_path": "/W/B", "force": [0.0, 0.0, 100.0]},
        ["UsdPhysics", "/W/B"],
    ),
    (
        "apply_physics_material",
        {"prim_path": "/W/B", "material_name": "Steel"},
        ["UsdPhysics", "/W/B"],
    ),
    (
        "assemble_robot",
        {"base_path": "/W/Base", "attachment_path": "/W/EE"},
        ["Phase 70", "RobotAssembler"],
    ),
    (
        "assign_class_to_children",
        {"prim_path": "/W/Parent", "class_name": "cube"},
        ["Semantics", "/W/Parent"],
    ),
    (
        "batch_delete_prims",
        {"prim_paths": ["/W/A", "/W/B"]},
        ["omni.usd", "/W/A"],
    ),
    (
        "batch_set_attributes",
        {"updates": [{"prim_path": "/W/A", "attr": "visibility", "value": "invisible"}]},
        ["batch_set_attributes"],
    ),
    (
        "bulk_apply_schema",
        {"prim_paths": ["/W/A"], "schema": "PhysicsCollisionAPI"},
        ["CollisionAPI"],
    ),
    (
        "bulk_set_attribute",
        {"prim_paths": ["/W/A"], "attr": "visibility", "value": "invisible"},
        ["omni.usd", "/W/A"],
    ),
    (
        "check_physics_health",
        {},
        ["UsdPhysics", "import omni.usd"],
    ),
    (
        "check_singularity",
        {"articulation_path": "/W/R", "target_position": [1, 0, 0.5]},
        ["isaacsim", "/W/R"],
    ),
    (
        "compute_convex_hull",
        {"prim_path": "/W/Mesh"},
        ["UsdGeom", "/W/Mesh"],
    ),
    (
        "configure_camera",
        {"camera_path": "/W/Cam", "fov": 60},
        ["UsdGeom", "/W/Cam"],
    ),
    (
        "configure_coco_yolo_writer",
        {"output_dir": "/tmp/coco", "format": "coco"},
        ["omni.replicator.core", "/tmp/coco"],
    ),
    (
        "configure_correlated_dr",
        {
            "parameter_groups": [
                {"params": ["a", "b"], "ranges": {"a": [0, 1]}, "correlation": 0.5}
            ]
        },
        ["Correlated", "import numpy"],
    ),
    (
        "configure_differential_sdg",
        {"baseline_pipeline_id": "p1", "candidate_pipeline_id": "p2"},
        ["omni.replicator.core"],
    ),
    (
        "configure_ros2_bridge",
        {},
        ["configure_ros2_bridge"],
    ),
    (
        "configure_ros2_time",
        {"mode": "system"},
        ["omni.graph.core"],
    ),
    (
        "configure_self_collision",
        {"articulation_path": "/W/R", "mode": "enable"},
        ["UsdPhysics", "/W/R"],
    ),
    (
        "configure_teleop_mapping",
        {"robot_path": "/W/R", "mapping_file": "/tmp/m.json"},
        ["UsdPhysics", "Teleop"],
    ),
    (
        "configure_zmq_stream",
        {"camera_prim": "/W/Cam", "endpoint": "tcp://localhost:5555"},
        ["omni.graph.core"],
    ),
    (
        "connect_nodes",
        {"graph_path": "/W/G", "src": "A.out", "dst": "B.in"},
        ["omni.graph.core", "/W/G"],
    ),
    (
        "create_audio_prim",
        {"prim_path": "/W/Audio", "audio_file": "/tmp/x.wav", "position": [0, 0, 0]},
        ["UsdMedia", "/W/Audio"],
    ),
    (
        "create_behavior",
        {
            "robot_path": "/W/R",
            "behavior_name": "pp",
            "behavior_type": "pick_place",
            "pick_pose": {"x": 0, "y": 0, "z": 0.5, "roll": 0, "pitch": 0, "yaw": 0},
            "place_pose": {"x": 1, "y": 0, "z": 0.5, "roll": 0, "pitch": 0, "yaw": 0},
        },
        ["isaacsim.cortex.framework"],
    ),
    (
        "create_bin",
        {"prim_path": "/W/Bin"},
        ["UsdGeom", "/W/Bin"],
    ),
    (
        "create_broken_scene",
        {"scenario": "cube_clipping"},
        ["Broken scene"],
    ),
    (
        "create_calibration_experiment",
        {"experiment_name": "cam_intrinsics"},
        ["Calibration experiment", "import numpy"],
    ),
    (
        "create_conveyor",
        {"prim_path": "/W/Conv", "length": 2.0},
        ["UsdPhysics", "/W/Conv"],
    ),
    (
        "create_graph",
        {"graph_path": "/W/G"},
        ["template"],
    ),
    (
        "create_hdri_skydome",
        {"hdri_path": "/tmp/sky.hdr"},
        ["UsdLux", "/tmp/sky.hdr"],
    ),
    (
        "create_sdg_pipeline",
        {"output_dir": "/tmp/sdg"},
        ["omni.replicator.core"],
    ),
    (
        "enable_deterministic_mode",
        {"seed": 42},
        ["deterministic", "import os"],
    ),
    (
        "enable_post_process",
        {"effect": "bloom"},
        ["omni.usd"],
    ),
    (
        "enforce_class_balance",
        {
            "pipeline_id": "p1",
            "target_distribution": {"cube": 0.5, "sphere": 0.5},
        },
        ["omni.replicator.core"],
    ),
    (
        "export_template",
        {"name": "pick_place", "output_path": "/tmp/t.json"},
        ["import json"],
    ),
    (
        "fix_collision_mesh",
        {"prim_path": "/W/M"},
        ["UsdPhysics", "/W/M"],
    ),
    (
        "fix_ros2_qos",
        {"topic": "/img"},
        ["omni.graph.core", "/img"],
    ),
    (
        "generate_occupancy_map",
        {"output_path": "/tmp/map.png"},
        ["MapGenerator"],
    ),
    (
        "grasp_object",
        {"robot_path": "/W/R", "target_prim": "/W/Cube"},
        ["UsdGeom", "/W/R"],
    ),
    (
        "import_template",
        {"file_path": "/tmp/t.json"},
        ["import json"],
    ),
    (
        "load_robot_pose",
        {"robot_path": "/W/R", "pose_name": "home"},
        ["import os", "home"],
    ),
    (
        "optimize_scene",
        {},
        ["UsdPhysics", "import json"],
    ),
    (
        "preview_dr",
        {"preset": "lighting_jitter"},
        ["preview"],
    ),
    (
        "quick_demo",
        {"scenario": "pick_place"},
        ["Quick Demo Builder", "franka"],
    ),
    (
        "record_waypoints",
        {"articulation_path": "/W/R", "output_path": "/tmp/wp.json"},
        ["isaacsim", "/W/R"],
    ),
    (
        "replay_rosbag",
        {"bag_path": "/tmp/bag.db3"},
        ["import subprocess", "/tmp/bag.db3"],
    ),
    (
        "replay_trajectory",
        {"articulation_path": "/W/R", "trajectory_path": "/tmp/t.json"},
        ["isaacsim", "/W/R"],
    ),
    (
        "render_video",
        {"output_path": "/tmp/v.mp4", "duration": 5},
        ["/tmp/v.mp4"],
    ),
    (
        "robot_wizard",
        {"robot_class": "franka_panda"},
        ["robot_wizard"],
    ),
    (
        "scatter_on_surface",
        {"surface_path": "/W/Table", "count": 10},
        ["UsdGeom", "random"],
    ),
    (
        "set_joint_velocity_limit",
        {"joint_path": "/W/J", "vel_limit": 3.14},
        ["UsdPhysics", "/W/J"],
    ),
    (
        "set_motion_policy",
        {"articulation_path": "/W/R", "policy_type": "rmpflow"},
        ["set_motion_policy"],
    ),
    (
        "set_physics_scene_config",
        {"gravity": [0, 0, -9.81]},
        ["UsdPhysics"],
    ),
    (
        "setup_multi_rate",
        {"physics_dt": 0.005, "render_dt": 0.033},
        ["Dual-rate", "VecEnv"],
    ),
    (
        "start_teaching_mode",
        {"articulation_path": "/W/R", "mode": "free_drive"},
        ["start_teaching_mode"],
    ),
    (
        "tune_gains",
        {"articulation_path": "/W/R"},
        ["UsdPhysics", "/W/R"],
    ),
]


class TestCodeGenerators:

    @pytest.mark.parametrize(
        "handler_name,args,expected_substrings",
        _TEST_VECTORS,
        ids=[f"{v[0]}_{i}" for i, v in enumerate(_TEST_VECTORS)],
    )
    def test_generates_valid_python(self, handler_name, args, expected_substrings):
        gen = CODE_GEN_HANDLERS[handler_name]
        code = gen(args)
        _assert_valid_python(code, handler_name)

    @pytest.mark.parametrize(
        "handler_name,args,expected_substrings",
        _TEST_VECTORS,
        ids=[f"{v[0]}_{i}" for i, v in enumerate(_TEST_VECTORS)],
    )
    def test_contains_expected_fragments(self, handler_name, args, expected_substrings):
        gen = CODE_GEN_HANDLERS[handler_name]
        code = gen(args)
        for frag in expected_substrings:
            assert frag in code, (
                f"{handler_name}: expected '{frag}' in generated code.\n"
                f"Code:\n{code[:800]}"
            )


class TestCodeGenEdgeCases:
    """Edge cases: missing optional args, empty strings, special chars."""

    def test_create_prim_minimal(self):
        code = CODE_GEN_HANDLERS["create_prim"](
            {"prim_path": "/World/X", "prim_type": "Xform"}
        )
        _assert_valid_python(code, "create_prim")
        # The snippet helper definitions may be inlined, but there should be
        # no actual translate/scale calls with coordinate arguments
        assert "_safe_set_translate(prim, (" not in code
        assert "_safe_set_scale(prim, (" not in code

    def test_create_prim_special_chars_in_path(self):
        code = CODE_GEN_HANDLERS["create_prim"](
            {"prim_path": "/World/My Robot (v2)", "prim_type": "Xform"}
        )
        _assert_valid_python(code, "create_prim")

    def test_sim_control_unknown_action(self):
        code = CODE_GEN_HANDLERS["sim_control"]({"action": "explode"})
        assert "Unknown" in code or "explode" in code

    def test_clone_prim_single_no_position(self):
        code = CODE_GEN_HANDLERS["clone_prim"](
            {"source_path": "/World/A", "target_path": "/World/B"}
        )
        _assert_valid_python(code, "clone_prim")
        assert "CopySpec" in code

    def test_set_attribute_with_string_value(self):
        code = CODE_GEN_HANDLERS["set_attribute"](
            {"prim_path": "/World/P", "attr_name": "visibility", "value": "invisible"}
        )
        _assert_valid_python(code, "set_attribute")

    def test_set_attribute_with_array_value(self):
        code = CODE_GEN_HANDLERS["set_attribute"](
            {"prim_path": "/World/P", "attr_name": "xformOp:translate", "value": [1, 2, 3]}
        )
        _assert_valid_python(code, "set_attribute")

    def test_omnigraph_legacy_namespace_remapped(self):
        """OmniGraph code gen should remap legacy omni.isaac.* to isaacsim.*"""
        code = CODE_GEN_HANDLERS["create_omnigraph"]({
            "graph_path": "/World/OG",
            "nodes": [
                {"name": "ctx", "type": "omni.isaac.ros2_bridge.ROS2Context"},
            ],
            "connections": [],
        })
        _assert_valid_python(code, "create_omnigraph")
        # Should be remapped
        assert "isaacsim.ros2.bridge.ROS2Context" in code

    def test_deformable_with_overrides(self):
        code = CODE_GEN_HANDLERS["create_deformable_mesh"]({
            "prim_path": "/World/Rubber",
            "soft_body_type": "rubber",
            "youngs_modulus": 50000,
            "poissons_ratio": 0.45,
            "damping": 0.1,
            "self_collision": False,
        })
        _assert_valid_python(code, "create_deformable_mesh")
        assert "50000" in code
        assert "0.45" in code

    def test_import_robot_no_assets_root(self, monkeypatch):
        """When ASSETS_ROOT_PATH is empty, asset_library format should error."""
        from service.isaac_assist_service.config import config
        monkeypatch.setattr(config, "assets_root_path", "")
        code = CODE_GEN_HANDLERS["import_robot"]({
            "file_path": "franka",
            "format": "asset_library",
        })
        assert "ERROR" in code or "RuntimeError" in code

    def test_build_scene_empty_blueprint(self):
        code = CODE_GEN_HANDLERS["build_scene_from_blueprint"](
            {"blueprint": {"objects": []}}
        )
        _assert_valid_python(code, "build_scene_from_blueprint")
        assert "Empty blueprint" in code


class TestAllCodeGenHandlersCovered:
    """Safety net: ensure every CODE_GEN_HANDLER appears in at least one test vector.

    Ratchet: ``_KNOWN_UNTESTED`` is the pre-existing backlog of handlers
    without test vectors. This set was captured 2026-05-13 (item qa-18)
    after the 144/145 spec landing. New handler additions are not
    permitted to skip vectors — they must be removed from this set as
    they gain coverage.

    The intent is: this test rejects regressions (handler added without
    vector) while documenting the historical backlog. If you must add a
    handler without a vector, add it here explicitly and open a follow-up
    issue.
    """

    _KNOWN_UNTESTED: "frozenset[str]" = frozenset({
        'add_domain_randomizer', 'add_proximity_sensor',
        'check_path_clearance', 'clone_envs', 'cloud_download_results',
        'create_arena', 'create_arena_variant', 'create_conveyor_track',
        'create_gripper', 'create_wheeled_robot', 'debug_draw',
        'define_grasp_pose', 'evaluate_groot', 'evaluate_reward',
        'export_dataset', 'export_nav2_map', 'export_policy',
        'export_teleop_mapping', 'extract_attention_maps', 'finetune_groot',
        'generate_eval_harness', 'generate_teleop_watchdog_script',
        'interpolate_trajectory', 'load_payload',
        'merge_meshes', 'monitor_joint_effort', 'navigate_to',
        'optimize_collision', 'play_animation',
        'publish_robot_description', 'record_teleop_demo',
        'record_trajectory', 'run_arena_benchmark',
        'setup_contact_sensors', 'setup_loco_manipulation_training',
        'setup_pick_place_controller', 'setup_pick_place_ros2_bridge',
        'setup_ros2_bridge', 'setup_rsi_from_demos',
        'setup_whole_body_control', 'simplify_collision', 'solve_ik',
        'verify_import',
    })

    def test_all_handlers_tested(self):
        tested = {v[0] for v in _TEST_VECTORS}
        untested = set(CODE_GEN_HANDLERS.keys()) - tested - self._KNOWN_UNTESTED
        assert untested == set(), (
            f"CODE_GEN_HANDLERS not covered by test vectors and not in "
            f"_KNOWN_UNTESTED ratchet: {untested}. Either add a test vector "
            f"to _TEST_VECTORS or add the handler to _KNOWN_UNTESTED with a "
            f"follow-up issue."
        )

    def test_known_untested_is_pruned(self):
        """Ratchet inverse: anything in _KNOWN_UNTESTED that NOW has a vector
        should be removed from the ratchet so the gap doesn't grow stale."""
        tested = {v[0] for v in _TEST_VECTORS}
        stale = self._KNOWN_UNTESTED & tested
        assert not stale, (
            f"Handlers in _KNOWN_UNTESTED also have test vectors — remove them "
            f"from the ratchet so it tracks the true backlog: {sorted(stale)}"
        )
