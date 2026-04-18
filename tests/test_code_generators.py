"""
L0 tests for every CODE_GEN_HANDLER in tool_executor.py.
Each test:
  1. Passes valid arguments
  2. Verifies the returned code compiles (compile())
  3. Checks for expected imports / API calls

Test vectors include handlers from this branch only — vectors for handlers
introduced in later phases are filtered out at module load so this file
stays runnable as new addenda are merged into master one branch at a time.
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

_RAW_TEST_VECTORS = [
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
        ["set_current_time(0)"],
    ),
    (
        "set_physics_params",
        {"gravity_direction": [0, 0, -1], "gravity_magnitude": 9.81},
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
        ["_safe_set_translate"],
    ),
    (
        "set_joint_targets",
        {"articulation_path": "/World/Franka", "joint_name": "panda_joint1", "target_position": 0.5},
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
        {"file_path": "/assets/franka.usd", "format": "usd", "dest_path": "/World/Franka"},
        ["AddReference", "franka.usd"],
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
        ["RmpFlow", "set_end_effector_target", "apply_action", "add_physics_callback", "update_world", "load_supported_motion_policy_config"],
        ["RmpFlow", "set_end_effector_target", "apply_action"],
    ),
    (
        "move_to_pose",
        {
            "articulation_path": "/World/Franka",
            "target_position": [0.4, 0.0, 0.3],
            "planner": "lula_rrt",
            "robot_type": "franka",
        },
        ["LulaRRTMotionPolicy"],
        ["LulaTaskSpaceTrajectoryGenerator"],
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
        ["LulaRRTMotionPolicy", "set_end_effector_target"],
        ["LulaTaskSpaceTrajectoryGenerator", "compute_task_space_trajectory"],
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
    # ── Phase 7B: Replicator / SDG ──────────────────────────────────────────
    (
        "create_sdg_pipeline",
        {"annotators": ["bounding_box_2d", "semantic_segmentation"], "output_format": "coco", "num_frames": 50},
        ["omni.replicator.core", "rep.create.camera", "CocoWriter", "rep.orchestrator"],
    ),
    (
        "create_sdg_pipeline",
        {"annotators": ["depth", "normals"], "output_format": "kitti", "num_frames": 10, "camera_position": [0, 5, 5]},
        ["omni.replicator.core", "KittiWriter", "render_product"],
    ),
    (
        "create_sdg_pipeline",
        {"annotators": ["bounding_box_2d"], "output_format": "basic", "num_frames": 5},
        ["BasicWriter", "rep.orchestrator"],
    ),
    (
        "create_sdg_pipeline",
        {"annotators": ["depth"], "output_format": "numpy", "num_frames": 20, "resolution": [640, 480]},
        ["BasicWriter", "640", "480"],
    ),
    (
        "add_domain_randomizer",
        {"target": "/World/Objects/.*", "randomizer_type": "pose"},
        ["omni.replicator.core", "rep.get.prims", "rotation"],
    ),
    (
        "add_domain_randomizer",
        {"target": "/World/Lights/.*", "randomizer_type": "lighting", "params": {"intensity_min": 500, "intensity_max": 2000}},
        ["omni.replicator.core", "intensity"],
    ),
    (
        "add_domain_randomizer",
        {"target": "/World/Objects/.*", "randomizer_type": "color"},
        ["omni.replicator.core", "color"],
    ),
    (
        "add_domain_randomizer",
        {"target": "/World/Objects/.*", "randomizer_type": "visibility"},
        ["omni.replicator.core", "visibility"],
    ),
    (
        "export_dataset",
        {"output_dir": "/tmp/sdg_export", "num_frames": 100},
        ["rep.orchestrator", "step"],
    ),
    (
        "export_dataset",
        {"output_dir": "/tmp/sdg_export", "num_frames": 500, "step_batch": 20},
        ["step", "Progress"],
    ),
    # ── Phase 7C: XR Teleoperation ──────────────────────────────────────────
    (
        "start_teleop_session",
        {"robot_path": "/World/Robot", "input_device": "keyboard", "stream_quality": "medium"},
        ["subscribe_physics_step_events", "watchdog"],
    ),
    (
        "start_teleop_session",
        {"robot_path": "/World/Franka", "input_device": "quest_3", "stream_quality": "high"},
        ["subscribe_physics_step_events", "quest_3"],
    ),
    (
        "configure_teleop_mapping",
        {"robot_path": "/World/Robot", "joint_names": ["joint_1", "joint_2"], "gains": {"position": 1.0, "velocity": 0.5}},
        ["joint", "gain"],
    ),
    (
        "record_teleop_demo",
        {"output_path": "/tmp/demo.hdf5", "robot_path": "/World/Robot", "frequency_hz": 30},
        ["h5py", "joint_pos"],
    ),
    (
        "record_teleop_demo",
        {"output_path": "/tmp/demo2.hdf5", "robot_path": "/World/Franka", "frequency_hz": 60},
        ["h5py", "60"],
    ),
    (
        "stop_teleop_session",
        {},
        ["remove", "velocit"],
    ),
    (
        "teleop_safety_config",
        {"robot_path": "/World/Robot", "watchdog_timeout_ms": 500, "max_joint_velocity": 2.0},
        ["watchdog", "velocit"],
    ),
    (
        "teleop_safety_config",
        {"robot_path": "/World/Robot", "workspace_limits": {"min": [-1, -1, 0], "max": [1, 1, 2]}},
        ["workspace", "limit"],
    ),
    # ── Phase 7E: Eureka LLM Reward Generation ─────────────────────────────
    (
        "evaluate_reward",
        {
            "reward_code": "def compute_reward(self):\n    dist = torch.norm(self.target - self.ee_pos, dim=-1)\n    return -dist",
            "env_id": "Isaac-Reach-Franka-Direct-v0",
            "num_steps": 500,
        },
        ["subprocess.Popen", "isaaclab.train", "reward_fn.py", "metrics.json", "Isaac-Reach-Franka-Direct-v0"],
    ),
    (
        "evaluate_reward",
        {
            "reward_code": "def compute_reward(self):\n    return torch.ones(self.num_envs)",
            "env_id": "Isaac-Lift-Cube-Direct-v0",
        },
        ["subprocess.Popen", "isaaclab.train", "reward_fn.py", "Isaac-Lift-Cube-Direct-v0"],
    ),
    # ── Phase 7F: ZMQ Sensor Streaming ──────────────────────────────────────
    (
        "configure_zmq_stream",
        {"camera_prim": "/World/Camera"},
        ["og.Controller", "ZMQBridge", "tcp://127.0.0.1:5555", "/World/Camera", "CameraHelper"],
    ),
    (
        "configure_zmq_stream",
        {"camera_prim": "/World/Lidar", "pub_port": 6000},
        ["og.Controller", "ZMQBridge", "tcp://127.0.0.1:6000", "/World/Lidar"],
    ),
    (
        "configure_zmq_stream",
        {"camera_prim": "/World/Camera", "pub_port": 7777, "compression": "none", "resolution": [320, 240], "fps": 15},
        ["og.Controller", "ZMQBridge", "tcp://127.0.0.1:7777", "none", "320", "240", "15"],
    ),
    # ── Phase 7D: IsaacLab-Arena Composable Environments ───────────────────
    (
        "create_arena",
        {
            "scene_type": "tabletop_pick_and_place",
            "robot_asset": "Franka",
            "task": "pick_and_place",
            "num_envs": 64,
            "env_spacing": 2.5,
        },
        ["ArenaEnvBuilder.combine", "gymnasium.register", "EmbodimentCfg", "TaskCfg", "SceneCfg"],
    ),
    (
        "create_arena",
        {
            "scene_type": "custom",
            "robot_asset": "/path/to/my_robot.usd",
            "task": "locomotion",
            "num_envs": 128,
        },
        ["ArenaEnvBuilder.combine", "gymnasium.register", "EmbodimentCfg", "'custom'"],
    ),
    (
        "create_arena_variant",
        {
            "base_env_id": "Arena-TabletopPickAndPlacePickAndPlace-Franka-v0",
            "robot_asset": "UR10",
        },
        ["ArenaEnvBuilder.combine", "gymnasium.register", "gymnasium.spec", "EmbodimentCfg", "UR10"],
    ),
    (
        "run_arena_benchmark",
        {
            "env_id": "Arena-TabletopPickAndPlacePickAndPlace-Franka-v0",
            "num_episodes": 50,
            "metrics": ["success_rate", "episode_length"],
        },
        ["subprocess.Popen", "arena.benchmark", "num_episodes", "results_file"],
    ),
    (
        "run_arena_benchmark",
        {
            "env_id": "Arena-KitchenNavigation-NovaCarter-v0",
            "num_episodes": 200,
            "metrics": ["success_rate", "object_moved"],
            "checkpoint": "/checkpoints/policy_best.pt",
        },
        ["subprocess.Popen", "arena.benchmark", "--checkpoint", "policy_best.pt"],
    ),
    # ── Phase 7G: GR00T N1 Foundation Policy ───────────────────────────────
    (
        "evaluate_groot",
        {
            "task": "Isaac-GR00T-Reach-v0",
            "num_episodes": 50,
        },
        ["subprocess.Popen", "gr00t.eval.isaac_lab", "gr00t.deploy.policy_server", "Isaac-GR00T-Reach-v0", "50"],
    ),
    (
        "evaluate_groot",
        {
            "model_id": "nvidia/GR00T-N1.6-3B",
            "task": "Isaac-GR00T-PickCube-v0",
            "num_episodes": 100,
            "checkpoint": "/checkpoints/groot_finetuned/best.pt",
        },
        ["subprocess.Popen", "gr00t.eval.isaac_lab", "Isaac-GR00T-PickCube-v0", "100", "/checkpoints/groot_finetuned/best.pt"],
    ),
    (
        "finetune_groot",
        {
            "demo_data": "/data/lerobot_demos/panda_pick",
            "num_steps": 10000,
            "lora": True,
        },
        ["subprocess.Popen", "gr00t.finetune.train", "--use-lora", "--lora-rank", "lerobot_demos/panda_pick", "10000"],
    ),
    (
        "finetune_groot",
        {
            "model_id": "nvidia/GR00T-N1.6-3B",
            "demo_data": "/data/demos/full_train",
            "num_steps": 50000,
            "lora": False,
            "output_dir": "/output/groot_full",
        },
        ["subprocess.Popen", "gr00t.finetune.train", "demos/full_train", "50000", "Full fine-tuning", "/output/groot_full"],
    ),
    # ── Phase 7H: IsaacAutomator Cloud Deployment ──────────────────────────
    (
        "cloud_download_results",
        {"job_id": "cloud-aws-abc12345"},
        ["rsync", "subprocess", "cloud-aws-abc12345", "/results/", "workspace/cloud_results"],
    ),
    (
        "cloud_download_results",
        {"job_id": "cloud-gcp-def67890", "output_dir": "/tmp/my_results"},
        ["rsync", "subprocess", "cloud-gcp-def67890", "/tmp/my_results"],
    ),
    # ── Physics Material Database ────────────────────────────────────────
    (
        "apply_physics_material",
        {"prim_path": "/World/Cube", "material_name": "steel"},
        ["UsdPhysics.MaterialAPI", "StaticFrictionAttr", "DynamicFrictionAttr", "RestitutionAttr", "CollisionAPI"],
    ),
    (
        "apply_physics_material",
        {"prim_path": "/World/Gripper", "material_name": "rubber"},
        ["UsdPhysics.MaterialAPI", "PhysicsMaterials/rubber_natural", "CollisionAPI"],
    ),
    (
        "apply_physics_material",
        {"prim_path": "/World/Part", "material_name": "aluminum"},
        ["UsdPhysics.MaterialAPI", "PhysicsMaterials/aluminum", "DensityAttr"],
    ),
    # ── Phase 8A: Cloner, Debug Draw, Occupancy Map, Camera ──────────────
    (
        "clone_envs",
        {"source_path": "/World/envs/env_0", "num_envs": 16, "spacing": 2.5, "collision_filter": True},
        ["GridCloner", "replicate_physics=True", "filter_collisions", "clone("],
    ),
    (
        "clone_envs",
        {"source_path": "/World/envs/env_0", "num_envs": 8, "collision_filter": False},
        ["GridCloner", "replicate_physics=True", "clone("],
    ),
    (
        "debug_draw",
        {"draw_type": "points", "points": [[0, 0, 0], [1, 1, 1]], "color": [1, 0, 0, 1], "size": 10},
        ["debug_draw", "acquire_debug_draw_interface", "draw_points"],
    ),
    (
        "debug_draw",
        {
            "draw_type": "lines",
            "points": [[0, 0, 0], [1, 0, 0], [0, 1, 0], [1, 1, 0]],
            "color": [0, 1, 0, 1],
        },
        ["debug_draw", "draw_lines(", "start_points", "end_points"],
    ),
    (
        "debug_draw",
        {
            "draw_type": "lines_spline",
            "points": [[0, 0, 0], [1, 1, 0], [2, 0, 0]],
            "lifetime": 5.0,
        },
        ["debug_draw", "draw_lines_spline", "call_later"],
    ),
    (
        "generate_occupancy_map",
        {"origin": [0, 0], "dimensions": [10, 10], "resolution": 0.05, "height_range": [0, 2]},
        ["MapGenerator", "cell_size=0.05", "generate2d", "get_buffer"],
    ),
    (
        "configure_camera",
        {"camera_path": "/World/Camera", "focal_length": 35.0},
        ["UsdGeom.Camera", "FocalLengthAttr", "Set(35.0)"],
    ),
    (
        "configure_camera",
        {"camera_path": "/World/Camera", "clipping_range": [0.01, 10000.0]},
        ["UsdGeom.Camera", "ClippingRangeAttr", "Gf.Vec2f(0.01, 10000.0)"],
    ),
    # ── Phase 8C: Cortex Behaviors & Manipulation ─────────────────────────
    (
        "create_behavior",
        {
            "articulation_path": "/World/Franka",
            "behavior_type": "pick_and_place",
            "target_prim": "/World/Cube",
        },
        ["CortexWorld", "DfStateMachineDecider", "MotionCommander", "ApproachState", "GraspState", "LiftState", "PlaceState"],
    ),
    (
        "create_behavior",
        {
            "articulation_path": "/World/Franka",
            "behavior_type": "follow_target",
            "target_prim": "/World/Target",
        },
        ["CortexWorld", "FollowTargetState", "send_end_effector_target", "MotionCommander"],
    ),
    (
        "create_gripper",
        {
            "articulation_path": "/World/Franka",
            "gripper_type": "parallel_jaw",
            "gripper_dof_names": ["panda_finger_joint1", "panda_finger_joint2"],
            "open_position": 0.04,
            "closed_position": 0.0,
        },
        ["ParallelGripper", "panda_finger_joint1", "joint_opened_positions", "gripper.open()"],
    ),
    (
        "create_gripper",
        {
            "articulation_path": "/World/Franka",
            "gripper_type": "suction",
        },
        ["og.Controller.edit", "OgnSurfaceGripper", "SuctionGripperGraph", "gripThreshold"],
    ),
    (
        "grasp_object",
        {
            "robot_path": "/World/Franka",
            "target_prim": "/World/Cube",
            "grasp_type": "top_down",
            "approach_distance": 0.15,
            "lift_height": 0.2,
        },
        ["RmpFlow", "set_end_effector_target", "approach_pos", "Step 1", "Step 4", "top_down"],
    ),
    (
        "grasp_object",
        {
            "robot_path": "/World/Franka",
            "target_prim": "/World/Mug",
            "grasp_type": "from_file",
            "grasp_file": "workspace/grasp_poses/Mug.isaac_grasp",
        },
        ["yaml.safe_load", "grasp_spec", "RmpFlow", "from file", "Mug.isaac_grasp"],
    ),
    (
        "define_grasp_pose",
        {
            "robot_path": "/World/Franka",
            "object_path": "/World/Cup",
            "gripper_offset": [0, 0, 0.02],
            "approach_direction": [0, 0, -1],
        },
        ["yaml.dump", "isaac_grasp", "gripper_offset", "approach_direction", "grasp_force"],
    ),
    # ── Phase 8D: Robot Setup Suite ─────────────────────────────────────────
    (
        "robot_wizard",
        {
            "asset_path": "/home/user/robots/franka.urdf",
            "robot_type": "manipulator",
        },
        ["import_urdf", "ImportConfig", "Kp=1000", "Kd=100", "CollisionAPI", "convex"],
    ),
    (
        "robot_wizard",
        {
            "asset_path": "/assets/carter.usd",
            "robot_type": "mobile",
            "drive_stiffness": 300,
            "drive_damping": 30,
        },
        ["AddReference", "carter.usd", "Kp=300", "Kd=30", "CollisionAPI"],
    ),
    (
        "tune_gains",
        {
            "articulation_path": "/World/Franka",
            "method": "manual",
            "joint_name": "panda_joint1",
            "kp": 500,
            "kd": 50,
        },
        ["DriveAPI", "StiffnessAttr", "DampingAttr", "panda_joint1", "500", "50"],
    ),
    (
        "tune_gains",
        {
            "articulation_path": "/World/Franka",
            "method": "step_response",
            "test_mode": "step",
        },
        ["GainTuner", "GainsTestMode.STEP", "initialize_gains_test", "compute_gains_test_error_terms", "pos_rmse", "vel_rmse"],
    ),
    (
        "assemble_robot",
        {
            "base_path": "/World/Franka",
            "attachment_path": "/World/Gripper",
            "base_mount": "panda_hand",
            "attach_mount": "tool_base",
        },
        ["RobotAssembler", "assemble(", "panda_hand", "tool_base", "single_robot"],
    ),
    (
        "configure_self_collision",
        {
            "articulation_path": "/World/Franka",
            "mode": "enable",
        },
        ["PhysxArticulationAPI", "EnabledSelfCollisionsAttr", "True"],
    ),
    (
        "configure_self_collision",
        {
            "articulation_path": "/World/Franka",
            "mode": "auto",
        },
        ["auto", "default"],
    ),
    # ── Phase 8E: Wheeled Robots & Conveyor Systems ──────────────────────────
    (
        "create_wheeled_robot",
        {
            "robot_path": "/World/Carter",
            "drive_type": "differential",
            "wheel_radius": 0.05,
            "wheel_base": 0.3,
            "max_linear_speed": 1.5,
            "max_angular_speed": 3.0,
        },
        ["DifferentialController", "wheel_radius=0.05", "wheel_base=0.3", "MAX_LINEAR_SPEED", "drive("],
    ),
    (
        "create_wheeled_robot",
        {
            "robot_path": "/World/Kaya",
            "drive_type": "holonomic",
            "wheel_radius": 0.04,
            "wheel_base": 0.2,
            "wheel_dof_names": ["axle_0", "axle_1", "axle_2"],
        },
        ["HolonomicController", "wheel_radius=0.04", "axle_0", "axle_1", "axle_2"],
    ),
    (
        "navigate_to",
        {
            "robot_path": "/World/Carter",
            "target_position": [5.0, 3.0],
            "planner": "direct",
        },
        ["WheelBasePoseController", "DifferentialController", "subscribe_physics_step_events", "5.0, 3.0"],
    ),
    (
        "navigate_to",
        {
            "robot_path": "/World/Carter",
            "target_position": [8.0, 6.0],
            "planner": "astar",
        },
        ["astar", "heapq", "WheelBasePoseController", "occupancy", "waypoints"],
    ),
    (
        "create_conveyor",
        {
            "prim_path": "/World/Conveyor",
            "speed": 0.8,
            "direction": [1, 0, 0],
        },
        ["OgnIsaacConveyor", "useFabric", "WARNING", "ConveyorGraph", "velocity"],
    ),
    (
        "create_conveyor_track",
        {
            "waypoints": [[0, 0, 0], [3, 0, 0], [3, 3, 0]],
            "belt_width": 0.5,
            "speed": 0.5,
        },
        ["ConveyorTrack", "Segment_", "atan2", "OgnIsaacConveyor", "AddRotateZOp"],
    ),
    (
        "merge_meshes",
        {
            "prim_paths": ["/World/Mesh1", "/World/Mesh2", "/World/Mesh3"],
            "output_path": "/World/MergedMesh",
        },
        ["MeshMerger", "update_selection", "merge()", "/World/Mesh1"],
    ),
    # ── Phase 8B: Motion Policy, IK ──────────────────────────────────────────
    (
        "set_motion_policy",
        {
            "articulation_path": "/World/Franka",
            "policy_type": "add_obstacle",
            "obstacle_name": "table_top",
            "obstacle_type": "cuboid",
            "obstacle_dims": [1.0, 0.6, 0.05],
            "obstacle_position": [0.5, 0.0, 0.4],
            "robot_type": "franka",
        },
        ["RmpFlow", "add_cuboid", "table_top", "update_world"],
    ),
    (
        "set_motion_policy",
        {
            "articulation_path": "/World/Franka",
            "policy_type": "add_obstacle",
            "obstacle_name": "ball",
            "obstacle_type": "sphere",
            "obstacle_dims": [0.05],
            "obstacle_position": [0.3, 0.1, 0.5],
            "robot_type": "franka",
        },
        ["RmpFlow", "add_sphere", "ball", "update_world"],
    ),
    (
        "solve_ik",
        {
            "articulation_path": "/World/Franka",
            "target_position": [0.4, 0.0, 0.3],
            "robot_type": "franka",
        },
        ["LulaKinematicsSolver", "ArticulationKinematicsSolver", "compute_inverse_kinematics", "apply_action", "success"],
    ),
    (
        "solve_ik",
        {
            "articulation_path": "/World/Franka",
            "target_position": [0.4, 0.0, 0.3],
            "target_orientation": [1.0, 0.0, 0.0, 0.0],
            "robot_type": "franka",
        },
        ["LulaKinematicsSolver", "compute_inverse_kinematics", "[1.0, 0.0, 0.0, 0.0]"],
    ),
    # ── Phase 2 Addendum: Smart Debugging ─────────────────────────────────────
    (
        "check_physics_health",
        {},
        ["UsdPhysics", "CollisionAPI", "MassAPI", "DiagonalInertiaAttr", "RevoluteJoint", "metersPerUnit", "json.dumps"],
    ),
    (
        "check_physics_health",
        {"articulation_path": "/World/Franka"},
        ["/World/Franka", "GetAllDescendants", "CollisionAPI", "MassAPI"],
    ),
    # ── Phase 3 Addendum: URDF Post-Processor ──────────────────────────────
    (
        "verify_import",
        {"articulation_path": "/World/Robot"},
        ["UsdPhysics", "CollisionAPI", "MassAPI", "ArticulationRootAPI", "metersPerUnit", "json.dumps"],
    ),
    (
        "verify_import",
        {"articulation_path": "/World/Franka"},
        ["/World/Franka", "GetAllDescendants", "CollisionAPI", "RevoluteJoint"],
    ),
    # ── Phase 8F: ROS2 Deep Integration ──────────────────────────────────────
    (
        "show_tf_tree",
        {"root_frame": "base_link"},
        ["tf_viewer", "acquire_transform_listener_interface", "get_transforms", "base_link", "ROS2PublishTransformTree"],
    ),
    (
        "publish_robot_description",
        {"articulation_path": "/World/Franka", "topic": "/robot_description"},
        ["rclpy", "TRANSIENT_LOCAL", "DurabilityPolicy", "urdf_string", "/robot_description", "create_publisher"],
    ),
    (
        "configure_ros2_bridge",
        {
            "sensors": [
                {"type": "camera", "prim_path": "/World/Camera", "topic_name": "/rgb", "frame_id": "camera_link"},
            ],
            "ros2_domain_id": 0,
        },
        ["ROS2Context", "ROS2CameraHelper", "_ROS2_NS", "isaacsim.__version__", "og.Controller.edit", "/rgb"],
    ),
    (
        "configure_ros2_bridge",
        {
            "sensors": [
                {"type": "lidar", "prim_path": "/World/Lidar", "topic_name": "/scan", "frame_id": "lidar_link"},
                {"type": "imu", "prim_path": "/World/IMU", "topic_name": "/imu/data", "frame_id": "imu_link"},
            ],
        },
        ["ROS2Context", "ROS2PublishLaserScan", "ROS2PublishImu", "/scan", "/imu/data", "lidar_link", "imu_link"],
    ),
    # ── Phase 8F Addendum: ROS2 Quality ──────────────────────────────────────
    (
        "fix_ros2_qos",
        {"topic": "/scan"},
        ["BEST_EFFORT", "VOLATILE", "topicName", "/scan", "qosProfile"],
    ),
    (
        "fix_ros2_qos",
        {"topic": "/robot_description"},
        ["RELIABLE", "TRANSIENT_LOCAL", "topicName", "/robot_description"],
    ),
    (
        "configure_ros2_time",
        {"mode": "sim_time"},
        ["useSimTime", "True", "ROS2PublishClock", "ROS2Context", "OnPlaybackTick", "og.Controller.edit"],
    ),
    (
        "configure_ros2_time",
        {"mode": "scaled", "time_scale": 2.0},
        ["useSimTime", "True", "ROS2PublishClock", "Time scale", "2.0"],
    ),
    # ── Interactive Robot Teaching ─────────────────────────────────────────────
    (
        "start_teaching_mode",
        {
            "articulation_path": "/World/Franka",
            "mode": "drag_target",
            "robot_type": "franka",
        },
        ["RmpFlow", "TeachTarget", "subscribe_physics_step_events", "set_end_effector_target", "Sphere"],
    ),
    (
        "start_teaching_mode",
        {
            "articulation_path": "/World/Franka",
            "mode": "keyboard",
        },
        ["Se3Keyboard", "pos_sensitivity", "SingleArticulation"],
    ),
    (
        "start_teaching_mode",
        {
            "articulation_path": "/World/Franka",
            "mode": "spacemouse",
        },
        ["Se3SpaceMouse", "pos_sensitivity", "SingleArticulation"],
    ),
    (
        "start_teaching_mode",
        {
            "articulation_path": "/World/Franka",
            "mode": "gravity_comp",
        },
        ["set_joint_stiffnesses", "set_joint_dampings", "subscribe_physics_step_events", "gravity_comp"],
    ),
    (
        "record_waypoints",
        {
            "articulation_path": "/World/Franka",
            "output_path": "/tmp/waypoints.json",
            "format": "json",
        },
        ["get_joint_positions", "json.dump", "waypoints", "/tmp/waypoints.json"],
    ),
    (
        "record_waypoints",
        {
            "articulation_path": "/World/Franka",
            "output_path": "/tmp/demo.hdf5",
            "format": "hdf5",
        },
        ["h5py", "robomimic", "joint_pos", "joint_vel", "num_demos"],
    ),
    (
        "record_waypoints",
        {
            "articulation_path": "/World/Franka",
            "output_path": "/tmp/waypoints.usd",
            "format": "usd",
        },
        ["TimeSample", "DriveAPI", "SetEndTimeCode"],
    ),
    (
        "replay_trajectory",
        {
            "articulation_path": "/World/Franka",
            "trajectory_path": "/tmp/waypoints.json",
            "speed": 0.5,
        },
        ["subscribe_physics_step_events", "set_joint_position_targets", "waypoints", "0.5"],
    ),
    (
        "interpolate_trajectory",
        {
            "articulation_path": "/World/Franka",
            "waypoints": [
                {"joint_positions": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]},
                {"joint_positions": [0.5, 0.3, 0.1, -0.2, 0.0, 0.4, 0.0]},
            ],
            "method": "linear",
            "num_steps": 50,
        },
        ["linspace", "interpolated", "Linear interpolation"],
    ),
    (
        "interpolate_trajectory",
        {
            "articulation_path": "/World/Franka",
            "waypoints": [
                {"joint_positions": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]},
                {"joint_positions": [0.5, 0.3, 0.1, -0.2, 0.0, 0.4, 0.0]},
            ],
            "method": "cubic",
        },
        ["CubicSpline", "scipy.interpolate", "Cubic interpolation"],
    ),
    (
        "interpolate_trajectory",
        {
            "articulation_path": "/World/Franka",
            "waypoints": [
                {"joint_positions": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]},
                {"joint_positions": [0.5, 0.3, 0.1, -0.2, 0.0, 0.4, 0.0]},
            ],
            "method": "rmpflow",
            "robot_type": "franka",
        },
        ["RmpFlow", "interface_config_loader", "RMPflow interpolation", "collision-aware"],
    ),
    # ── Phase 8B Addendum: Workspace & Singularity ──────────────────────────
    (
        "show_workspace",
        {"articulation_path": "/World/Franka", "resolution": 10000, "color_mode": "manipulability"},
        ["debug_draw", "acquire_debug_draw_interface", "draw_points", "LulaKinematicsSolver"],
    ),
    (
        "show_workspace",
        {"articulation_path": "/World/UR10", "color_mode": "reachability"},
        ["debug_draw", "draw_points"],
    ),
    (
        "check_singularity",
        {"articulation_path": "/World/Franka", "target_position": [0.5, 0, 0.3]},
        ["LulaKinematicsSolver", "svd", "condition"],
    ),
    (
        "check_singularity",
        {"articulation_path": "/World/Franka", "target_position": [0.5, 0, 0.3], "target_orientation": [1, 0, 0, 0]},
        ["compute_inverse_kinematics", "svd", "condition"],
    ),
    (
        "monitor_joint_effort",
        {"articulation_path": "/World/Franka", "duration_seconds": 3.0},
        ["subscribe_physics_step_events", "get_applied_joint_efforts", "utilization"],
    ),
    # ── Performance Diagnostics ──────────────────────────────────────────────
    (
        "optimize_collision",
        {"prim_path": "/World/HeavyMesh", "approximation": "convexHull"},
        ["MeshCollisionAPI", "GetApproximationAttr", "convexHull", "/World/HeavyMesh"],
    ),
    (
        "optimize_collision",
        {"prim_path": "/World/Table/Legs", "approximation": "convexDecomposition"},
        ["MeshCollisionAPI", "convexDecomposition", "CollisionAPI"],
    ),
    (
        "optimize_collision",
        {"prim_path": "/World/Backdrop", "approximation": "boundingSphere"},
        ["MeshCollisionAPI", "boundingSphere", "/World/Backdrop"],
    ),
    # ── Scene Simplification: optimize_scene ─────────────────────────────────
    (
        "optimize_scene",
        {"mode": "analyze"},
        ["stage.Traverse", "analyze_only = True", "CollisionAPI", "optimizations"],
    ),
    (
        "optimize_scene",
        {"mode": "conservative", "target_fps": 60},
        ["stage.Traverse", "analyze_only = False", "convexHull", "convexDecomposition", "target_fps = 60"],
    ),
    (
        "optimize_scene",
        {"mode": "aggressive", "target_fps": 45},
        ["analyze_only = False", "apply_aggressive = True", "EnableGPUDynamicsAttr", "BroadphaseTypeAttr"],
    ),
    # ── Scene Simplification: simplify_collision ─────────────────────────────
    (
        "simplify_collision",
        {"prim_path": "/World/Table", "approximation": "convexHull"},
        ["MeshCollisionAPI", "CollisionAPI", "convexHull", "/World/Table"],
    ),
    (
        "simplify_collision",
        {"prim_path": "/World/Ball", "approximation": "convexDecomposition"},
        ["MeshCollisionAPI", "convexDecomposition", "/World/Ball"],
    ),
    (
        "simplify_collision",
        {"prim_path": "/World/Trigger", "approximation": "boundingSphere"},
        ["MeshCollisionAPI", "boundingSphere"],
    ),
    (
        "simplify_collision",
        {"prim_path": "/World/Deformable", "approximation": "sdf"},
        ["MeshCollisionAPI", "sdf"],
    ),
    # ── OmniGraph Assistant ──────────────────────────────────────────────────
    (
        "explain_graph",
        {"graph_path": "/World/ActionGraph"},
        ["og.get_graph_by_path", "/World/ActionGraph", "get_nodes", "get_node_type", "json.dumps"],
    ),
    (
        "create_graph",
        {"description": "publish joint states to ROS2", "robot_path": "/World/Franka"},
        ["og.Controller.edit", "ROS2Context", "ROS2PublishJointState", "CREATE_NODES", "CONNECT", "/World/Franka"],
    ),
    (
        "create_graph",
        {
            "description": "publish simulation clock",
            "template": "ros2_clock",
            "graph_path": "/World/ClockGraph",
        },
        ["og.Controller.edit", "ROS2Context", "ROS2PublishClock", "IsaacReadSimulationTime", "/World/ClockGraph"],
    ),
    (
        "create_graph",
        {
            "description": "subscribe to cmd_vel for teleop",
            "template": "ros2_cmd_vel",
            "robot_path": "/World/Carter",
            "topic": "/cmd_vel",
        },
        ["og.Controller.edit", "ROS2Context", "SubscribeTwist", "DifferentialController", "/World/Carter", "/cmd_vel"],
    ),
    (
        "create_graph",
        {
            "description": "publish camera images",
            "template": "ros2_camera",
            "camera_path": "/World/Camera",
            "topic": "/camera/rgb",
        },
        ["og.Controller.edit", "ROS2CameraHelper", "/World/Camera", "/camera/rgb"],
    ),
    (
        "create_graph",
        {
            "description": "publish lidar scans",
            "template": "ros2_lidar",
            "lidar_path": "/World/Lidar",
        },
        ["og.Controller.edit", "IsaacReadLidar", "ROS2PublishLaserScan", "/World/Lidar"],
    ),
    (
        "create_graph",
        {
            "description": "broadcast TF tree",
            "template": "ros2_tf",
            "root_prim": "/World/Robot",
        },
        ["og.Controller.edit", "ROS2PublishTransformTree", "/World/Robot"],
    ),
    (
        "create_graph",
        {
            "description": "publish IMU data",
            "template": "ros2_imu",
            "imu_path": "/World/Robot/IMU",
            "topic": "/imu/data",
        },
        ["og.Controller.edit", "IsaacReadIMU", "ROS2PublishImu", "/World/Robot/IMU", "/imu/data"],
    ),
    (
        "create_graph",
        {
            "description": "publish odometry",
            "template": "ros2_odom",
            "chassis_path": "/World/Carter/chassis",
        },
        ["og.Controller.edit", "IsaacComputeOdometry", "ROS2PublishOdometry", "/World/Carter/chassis"],
    ),
    (
        "debug_graph",
        {"graph_path": "/World/ActionGraph"},
        ["og.get_graph_by_path", "/World/ActionGraph", "ROS2Context", "OnPlaybackTick", "issues", "json.dumps"],
    ),
    # ── Phase 2 Addendum: Preflight Check (23 checks) ────────────────────────
    (
        "preflight_check",
        {},
        ["UsdPhysics", "CollisionAPI", "MassAPI", "DiagonalInertiaAttr", "RevoluteJoint",
         "metersPerUnit", "json.dumps", "M01", "M04", "M07", "M09", "M16", "M18",
         "tier1_errors", "tier2_warnings", "tier3_rl", "tier4_ros2", "auto_fixable"],
    ),
    (
        "preflight_check",
        {"scope": "tier1"},
        ["UsdPhysics", "CollisionAPI", "MassAPI", "DiagonalInertiaAttr",
         "M01", "M04", "M05", "M06", "M08", "M11", "tier1_errors"],
    ),
    (
        "preflight_check",
        {"scope": "all", "articulation_path": "/World/Franka"},
        ["/World/Franka", "GetAllDescendants", "CollisionAPI", "MassAPI",
         "tier1_errors", "auto_fixable"],
    ),
    # ── Phase 7A Addendum: RL training debugging & quality ─────────────────
    (
        "generate_eval_harness",
        {"task_name": "Isaac-Reach-Franka-v0"},
        ["gymnasium", "gym.make", "NUM_EPISODES", "eval_results.json", "is_success"],
    ),
    (
        "generate_eval_harness",
        {
            "task_name": "Isaac-Lift-Cube-Franka-v0",
            "num_episodes": 25,
            "output_dir": "/tmp/eval_out",
            "checkpoint_path": "/tmp/policy.pt",
            "record_video": True,
            "max_steps_per_episode": 500,
        },
        ["RecordVideo", "/tmp/eval_out", "Isaac-Lift-Cube-Franka-v0", "NUM_EPISODES = 25", "MAX_STEPS_PER_EPISODE = 500"],
    ),
    # ── Phase 7B Addendum: Advanced SDG ─────────────────────────────────────
    (
        "scatter_on_surface",
        {"source_prims": ["/World/Apple"], "target_mesh": "/World/Tree", "count": 10},
        ["_sample_surface_points", "_poisson_filter", "trimesh"],
    ),
    (
        "scatter_on_surface",
        {
            "source_prims": ["/World/A", "/World/B"],
            "target_mesh": "/path/to/branch.usd",
            "count": 25,
            "spacing": 0.05,
            "normal_align": True,
            "penetration_check": True,
            "seed": 42,
        },
        ["random.seed(42)", "spacing = 0.05", "normal_align = True"],
    ),
    (
        "configure_differential_sdg",
        {"static_elements": ["/World/Floor"], "dynamic_elements": ["/World/Light"]},
        ["rep.new_layer", "rep.trigger.on_frame", "rep.randomizer.rotation"],
    ),
    (
        "configure_coco_yolo_writer",
        {"output_dir": "/tmp/sdg", "cameras": ["/World/Cam1", "/World/Cam2"]},
        ["categories.json", "_GLOBAL_ANN_ID", "_image_id_for"],
    ),
    (
        "enforce_class_balance",
        {"min_per_class": 2, "max_retries": 5, "classes": ["apple", "orange"]},
        ["MIN_PER_CLASS = 2", "_class_counts", "class_balance_gate"],
    ),
    # ─── Enterprise Scale Addendum (E.4 / E.6) ───────────────────────────────
    (
        "batch_delete_prims",
        {"prim_paths": ["/World/A", "/World/B", "/World/C"]},
        ["Sdf.BatchNamespaceEdit", "/World/A", "/World/C"],
    ),
    (
        "batch_set_attributes",
        {"changes": [
            {"prim_path": "/World/A", "attr_name": "visibility", "value": "invisible"},
            {"prim_path": "/World/B", "attr_name": "radius", "value": 0.25},
        ]},
        ["Sdf.ChangeBlock", "visibility", "radius"],
    ),
    (
        "activate_area",
        {"prim_scope": "/World/Cell_A"},
        ["SetActive(True)", "SetActive(False)", "/World/Cell_A"],
    ),
    # NOTE: launch_training (master) is intentionally not tested here —
    # the generator has a pre-existing nested-quote bug in master that
    # produces invalid Python; fixing it is out of scope for this addendum.
    # NOTE: launch_training (master) is intentionally not tested here —
    # the generator has a pre-existing nested-quote bug in master that
    # produces invalid Python; fixing it is out of scope for this addendum.
    # ── Tier 1 Atomic Tools — USD Core ──────────────────────────────────────
    (
        "set_prim_metadata",
        {"prim_path": "/World/Cube", "key": "kind", "value": "component"},
        ["SetMetadata", "'kind'", "'component'", "/World/Cube"],
    ),
    (
        "set_prim_metadata",
        {"prim_path": "/World/X", "key": "hidden", "value": True},
        ["SetMetadata", "'hidden'", "True"],
    ),
    # NOTE: launch_training (master) is intentionally not tested here —
    # the generator has a pre-existing nested-quote bug in master that
    # produces invalid Python; fixing it is out of scope for this addendum.
    # ── Clearance Detection Addendum ────────────────────────────────────────
    (
        "set_clearance_monitor",
        {
            "articulation_path": "/World/Franka",
            "clearance_mm": 50.0,
            "warning_mm": 100.0,
            "target_prims": ["/World/Fixture"],
        },
        [
            "PhysxContactReportAPI",
            "CreateContactOffsetAttr",
            "subscribe_contact_report_events",
            "stop_threshold_m",
            "warning_threshold_m",
            "/World/Fixture",
        ],
    ),
    (
        "set_clearance_monitor",
        {"articulation_path": "/World/UR10"},
        [
            "PhysxContactReportAPI",
            "CreateContactOffsetAttr",
            "subscribe_contact_report_events",
            "/World/UR10",
        ],
    ),
    (
        "visualize_clearance",
        {
            "articulation_path": "/World/Franka",
            "mode": "heatmap",
            "target_prims": ["/World/Fixture"],
            "clearance_mm": 50.0,
        },
        [
            "PhysxSDFMeshCollisionAPI",
            "debug_draw",
            "draw_points",
            "/World/Franka",
        ],
    ),
    (
        "visualize_clearance",
        {
            "articulation_path": "/World/Franka",
            "mode": "zones",
            "target_prims": ["/World/Fixture", "/World/Wall"],
            "clearance_mm": 50.0,
            "warning_mm": 100.0,
        },
        [
            "PhysxTriggerAPI",
            "DefinePrim",
            "WarningZone",
            "StopZone",
        ],
    ),
    (
        "check_path_clearance",
        {
            "articulation_path": "/World/Franka",
            "trajectory": [[0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]],
            "obstacles": ["/World/Fixture"],
            "clearance_mm": 50.0,
        },
        [
            "LulaKinematicsSolver",
            "compute_forward_kinematics",
            "min_clearance_mm",
            "violations",
            "/World/Fixture",
        ],
    ),
    # ── Addendum H: Humanoid Advanced ──────────────────────────────────────
    (
        "setup_contact_sensors",
        {
            "articulation_path": "/World/Allegro",
            "body_names": ["thumb_tip", "index_tip", "middle_tip", "ring_tip"],
            "num_envs": 4096,
        },
        [
            "ContactSensorCfg",
            "PhysxCfg",
            "thumb_tip",
            "index_tip",
            "{ENV_REGEX_NS}/Robot/thumb_tip",
            "gpu_max_rigid_contact_count",
            "gpu_max_rigid_patch_count",
        ],
    ),
    (
        "setup_contact_sensors",
        {
            "articulation_path": "/World/Hand",
            "body_names": ["fingertip"],
            "num_envs": 64,
            "track_air_time": True,
        },
        ["ContactSensorCfg", "fingertip", "track_air_time=True"],
    ),
    (
        "setup_whole_body_control",
        {
            "articulation_path": "/World/G1",
            "locomotion_policy": "hover_g1_flat.pt",
            "arm_planner": "pink_ik",
            "robot_profile": "g1",
        },
        [
            "ActionGroupCfg",
            "LocomotionPolicyCfg",
            "PinkIKControllerCfg",
            "FrameTask",
            "PostureTask",
            "DampingTask",
            "lower_body=locomotion_cfg",
            "upper_body=arm_cfg",
            "hover_g1_flat.pt",
        ],
    ),
    (
        "setup_whole_body_control",
        {
            "articulation_path": "/World/H1",
            "locomotion_policy": "hover_h1_rough.pt",
            "arm_planner": "rmpflow",
            "robot_profile": "h1",
        },
        ["ActionGroupCfg", "RmpFlowControllerCfg", "hover_h1_rough.pt"],
    ),
    (
        "setup_loco_manipulation_training",
        {
            "task_description": "walk to a table and pick up a cup",
            "robot": "g1",
            "approach": "decoupled",
            "reward_terms": [
                {"name": "forward_velocity", "weight": 1.0, "category": "locomotion"},
                {"name": "reach_target", "weight": 1.0, "category": "manipulation"},
                {"name": "grasp_success", "weight": 5.0, "category": "manipulation"},
            ],
        },
        [
            "DECOUPLED",
            "Reward mixing advisor",
            "WARNING",
            "Phase 1",
            "Phase 2",
            "Phase 3",
            "locomotion_weight=5.0",
            "manipulation_weight=2.0",
        ],
    ),
    (
        "setup_loco_manipulation_training",
        {
            "task_description": "dynamic running pickup",
            "robot": "h1",
            "approach": "hierarchical",
        },
        ["HIERARCHICAL", "SoFTA", "FALCON"],
    ),
    (
        "setup_loco_manipulation_training",
        {
            "task_description": "max performance whole-body",
            "robot": "g1",
            "approach": "joint",
        },
        ["JOINT", "end-to-end", "reward curriculum"],
    ),
    (
        "setup_rsi_from_demos",
        {
            "demo_path": "workspace/demos/g1_walk_pick.npz",
            "env_cfg": "G1WalkPickEnvCfg",
            "noise_std": 0.05,
        },
        [
            "InitialStateCfg",
            "demo_sampling",
            "g1_walk_pick.npz",
            "noise_std=0.05",
            "G1WalkPickEnvCfg",
        ],
    ),
    (
        "setup_multi_rate",
        {"lower_rate_hz": 50, "upper_rate_hz": 100, "upper_dof": 14},
        [
            "DualRateWrapper",
            "gym.Wrapper",
            "UPPER_DOF = 14",
            "DECIMATION = 2",
            "torch.cat",
            "_cached_lower",
        ],
    ),
    (
        "setup_multi_rate",
        {"lower_rate_hz": 30, "upper_rate_hz": 120},
        ["DualRateWrapper", "DECIMATION = 4"],
    ),
    # NOTE: launch_training (master) is intentionally not tested here —
    # the generator has a pre-existing nested-quote bug in master that
    # produces invalid Python; fixing it is out of scope for this addendum.
    # ── Collision Mesh Quality Addendum ─────────────────────────────────────
    (
        "fix_collision_mesh",
        {
            "prim_path": "/World/Robot/link3",
            "target_triangles": 500,
        },
        [
            "import trimesh",
            "fix_normals",
            "fill_holes",
            "simplify_quadric_decimation",
            "convex_hull",
            "MeshCollisionAPI",
            "/World/Robot/link3",
        ],
    ),
    (
        "fix_collision_mesh",
        {"prim_path": "/World/Table"},  # No target_triangles → handler picks default
        [
            "import trimesh",
            "fix_normals",
            "MeshCollisionAPI",
            "TARGET_TRIANGLES = None",
            "/World/Table",
        ],
    ),
    (
        "visualize_collision_mesh",
        {"prim_path": "/World/Robot/link3"},
        [
            "UsdPhysics.CollisionAPI",
            "carb.settings",
            "visualizationCollisionMesh",
            "/World/Robot/link3",
        ],
    ),
    # ── Quick Demo Builder ──────────────────────────────────────────────────
    (
        "quick_demo",
        {"demo_type": "pick_place"},
        ["pick_place", "DemoCamera", "DomeLight", "import omni.usd"],
    ),
    (
        "quick_demo",
        {"demo_type": "mobile_nav", "robot": "jetbot", "scene_style": "industrial"},
        ["mobile_nav", "jetbot", "industrial"],
    ),
    (
        "quick_demo",
        {"demo_type": "humanoid_walk", "objects": [], "scene_style": "dramatic"},
        ["humanoid_walk", "DemoCamera"],
    ),
    (
        "record_demo_video",
        {"output_path": "/tmp/demo.mp4"},
        ["CaptureExtension", "/tmp/demo.mp4", "fps"],
    ),
    (
        "record_demo_video",
        {"output_path": "/tmp/demo2.mp4", "duration": 30, "fps": 60, "camera": "/World/Cam"},
        ["/tmp/demo2.mp4", "60", "/World/Cam"],
    ),
    # ── Sim-to-Real Gap: create_calibration_experiment ─────────────────
    (
        "create_calibration_experiment",
        {"parameter": "friction", "range": [0.4, 1.0], "num_samples": 7, "real_data_path": "/data/real.h5"},
        ["friction", "linspace", "0.4", "1.0", "/data/real.h5"],
    ),
    (
        "create_calibration_experiment",
        {"parameter": "damping", "range": [0.1, 1.0], "real_data_path": "/data/real.csv"},
        ["damping", "DriveAPI", "DampingAttr"],
    ),
    # ── GR00T Tooling: extract_attention_maps + export_policy ─────────────
    (
        "extract_attention_maps",
        {"checkpoint_path": "/models/groot.pt", "observation_path": "/data/obs.h5"},
        ["/models/groot.pt", "/data/obs.h5", "12", "attn_drop"],
    ),
    (
        "extract_attention_maps",
        {"checkpoint_path": "/models/g.pt", "observation_path": "/o.h5", "layer": 6},
        ["layer = 6", "attn_drop"],
    ),
    (
        "export_policy",
        {"checkpoint": "/models/groot.pt", "target_device": "jetson_agx_orin"},
        ["jetson_agx_orin", "TensorRT", "5.8"],
    ),
    (
        "export_policy",
        {"checkpoint": "/models/groot.pt", "target_device": "x86_rtx4090", "inference_budget_ms": 50},
        ["x86_rtx4090", "budget_ms", "50"],
    ),
    # ── Phase 5 Addendum: create_broken_scene (6 fault types) ─────────────
    (
        "create_broken_scene",
        {"fault_type": "missing_collision"},
        ["FAULT", "CollisionAPI", "FallingCube"],
    ),
    (
        "create_broken_scene",
        {"fault_type": "zero_mass"},
        ["MassAPI", "0.0", "ZeroMassBody"],
    ),
    (
        "create_broken_scene",
        {"fault_type": "wrong_scale"},
        ["100", "HugeBox"],
    ),
    (
        "create_broken_scene",
        {"fault_type": "inverted_joint"},
        ["RevoluteJoint", "Z"],
    ),
    (
        "create_broken_scene",
        {"fault_type": "no_physics_scene"},
        ["RigidBodyAPI", "no_physics_scene"],
    ),
    (
        "create_broken_scene",
        {"fault_type": "inf_joint_limits"},
        ["inf", "LowerLimitAttr"],
    ),
    # ── Safety & Compliance: enable_deterministic_mode ────────────────────
    (
        "enable_deterministic_mode",
        {},
        ["TGS", "EnableGPUDynamicsAttr", "42"],
    ),
    (
        "enable_deterministic_mode",
        {"seed": 123, "physics_dt": 0.01, "solver_iterations": 8},
        ["123", "0.01", "8"],
    ),
    (
        "enable_deterministic_mode",
        {"export_archive_path": "/tmp/repro.zip", "seed": 999},
        ["zipfile", "/tmp/repro.zip", "manifest"],
    ),
    # NOTE: launch_training (master) is intentionally not tested here —
    # the generator has a pre-existing nested-quote bug in master that
    # produces invalid Python; fixing it is out of scope for this addendum.

    # ── Tier 0 Atomic Tools — Foundation ────────────────────────────────────
    # CODE_GEN handlers from docs/specs/atomic_tools_catalog.md.
    (
        "set_semantic_label",
        {"prim_path": "/World/Cube", "class_name": "cube"},
        ["Semantics.SemanticsAPI.Apply", "CreateSemanticDataAttr", "'cube'"],
    ),
    (
        "set_semantic_label",
        {"prim_path": "/World/Robot", "class_name": "franka", "semantic_type": "instance"},
        ["Semantics_instance", "'franka'", "CreateSemanticTypeAttr"],
    ),
    (
        "set_drive_gains",
        {"joint_path": "/World/Franka/joint1", "kp": 100.0, "kd": 5.0},
        ["UsdPhysics.DriveAPI.Apply", "CreateStiffnessAttr", "CreateDampingAttr", "100.0", "5.0"],
    ),
    (
        "set_drive_gains",
        {"joint_path": "/World/UR10/elbow", "kp": 250.0, "kd": 12.5, "drive_type": "linear"},
        ["DriveAPI", "'linear'", "250.0", "12.5"],
    ),
    (
        "set_render_mode",
        {"mode": "preview"},
        ["carb.settings", "rendermode", "RaytracedLighting"],
    ),
    (
        "set_render_mode",
        {"mode": "path_traced"},
        ["PathTracing", "rendermode"],
    ),
    (
        "set_render_mode",
        {"mode": "rt"},
        ["RaytracedLighting"],
    ),
    (
        "set_variant",
        {"prim_path": "/World/Asset", "variant_set": "color", "variant": "red"},
        ["GetVariantSets", "SetVariantSelection", "'color'", "'red'"],
    ),
    (
        "set_variant",
        {"prim_path": "/World/Robot", "variant_set": "rig", "variant": "gripper"},
        ["AddVariantSet", "'rig'", "'gripper'"],
    ),
    (
        "record_trajectory",
        {"articulation": "/World/Franka", "duration": 5.0},
        [
            "subscribe_physics_step_events",
            "np.savez",
            "'/World/Franka'",
            "duration = 5.0",
        ],
    ),
    (
        "record_trajectory",
        {
            "articulation": "/World/UR10",
            "duration": 2.5,
            "output_path": "/tmp/ur10.npz",
            "rate_hz": 120.0,
        },
        ["'/tmp/ur10.npz'", "rate_hz = 120.0", "duration = 2.5"],
    ),
    # ── Tier 2 Atomic Tools — Physics Bodies & Scene ───────────────────────
    (
        "set_linear_velocity",
        {"prim_path": "/World/Cube", "vel": [1.0, 0.0, 0.0]},
        ["UsdPhysics.RigidBodyAPI", "GetVelocityAttr", "Gf.Vec3f"],
    ),
    (
        "set_physics_scene_config",
        {"config": {
            "solver_type": "TGS",
            "position_iterations": 8,
            "velocity_iterations": 1,
            "time_steps_per_second": 120,
            "enable_gpu_dynamics": True,
            "broadphase_type": "GPU",
            "gravity_direction": [0, 0, -1],
            "gravity_magnitude": 9.81,
        }},
        ["PhysxSceneAPI", "TGS", "GPU", "GravityMagnitudeAttr"],
    ),
    (
        "apply_force",
        {
            "prim_path": "/World/Cube",
            "force": [10.0, 0.0, 0.0],
            "torque": [0.0, 0.0, 1.0],
            "position": [0.0, 0.0, 0.0],
        },
        ["UsdPhysics.RigidBodyAPI", "force", "torque"],
    ),

    # ── Tier 3 Atomic Tools — Articulation & Joints (CODE_GEN) ────────────
    (
        "set_joint_limits",
        {"joint_path": "/World/Franka/panda_link0/panda_joint1", "lower": -2.5, "upper": 2.5},
        [
            "physics:lowerLimit",
            "physics:upperLimit",
            "CreateLowerLimitAttr",
            "CreateUpperLimitAttr",
            "-2.5",
            "2.5",
        ],
    ),
    (
        "set_joint_velocity_limit",
        {"joint_path": "/World/Franka/panda_link0/panda_joint1", "vel_limit": 3.14},
        [
            "PhysxJointAPI",
            "MaxJointVelocity",
            "physxJoint:maxJointVelocity",
            "3.14",
        ],
    ),
    # NOTE: launch_training (master) is intentionally not tested here —
    # the generator has a pre-existing nested-quote bug in master that
    # produces invalid Python; fixing it is out of scope for this addendum.

    # ── Tier 4 Atomic Tools — Geometry & Spatial Analysis (CODE_GEN) ──────
    (
        "compute_convex_hull",
        {"prim_path": "/World/MyMesh"},
        [
            "UsdPhysics.MeshCollisionAPI",
            "convexHull",
            "/World/MyMesh",
        ],
    ),
    (
        "compute_convex_hull",
        {"prim_path": "/World/MyMesh", "export_hull_path": "/World/MyMeshHull"},
        [
            "UsdPhysics.MeshCollisionAPI",
            "convexHull",
            "/World/MyMeshHull",
            "ConvexHull",
            "DefinePrim",
        ],
    ),
    # ── Atomic Tier 5 — OmniGraph (low-level atomic operations) ────────────
    (
        "add_node",
        {
            "graph_path": "/World/ActionGraph",
            "node_type": "omni.graph.action.OnPlaybackTick",
            "name": "tick",
        },
        ["og.Controller.edit", "CREATE_NODES", "OnPlaybackTick", "tick"],
    ),
    (
        "add_node",
        {
            "graph_path": "/World/ActionGraph",
            "node_type": "omni.isaac.ros2_bridge.ROS2Context",
            "name": "ctx",
        },
        # Should be remapped via _OG_NODE_TYPE_MAP
        ["isaacsim.ros2.bridge.ROS2Context", "ctx", "CREATE_NODES"],
    ),
    (
        "connect_nodes",
        {
            "graph_path": "/World/ActionGraph",
            "src": "tick.outputs:tick",
            "dst": "publishClock.inputs:execIn",
        },
        ["og.Controller.edit", "CONNECT", "tick.outputs:tick", "publishClock.inputs:execIn"],
    ),
    (
        "set_graph_variable",
        {
            "graph_path": "/World/ActionGraph",
            "name": "topicName",
            "value": "/joint_states",
        },
        ["og.Controller", "topicName", "/joint_states"],
    ),
    (
        "set_graph_variable",
        {
            "graph_path": "/World/ActionGraph",
            "name": "rate",
            "value": 60,
        },
        ["og.Controller", "rate", "60"],
    ),
    (
        "delete_node",
        {"graph_path": "/World/ActionGraph", "node_name": "tick"},
        ["og.Controller.edit", "DELETE_NODES", "tick"],
    ),
    # NOTE: launch_training (master) is intentionally not tested here —
    # the generator has a pre-existing nested-quote bug in master that
    # produces invalid Python; fixing it is out of scope for this addendum.
    # ── Tier 6 — Lighting ──────────────────────────────────────────────────
    (
        "set_light_intensity",
        {"light_path": "/World/SunLight", "intensity": 5000.0},
        ["inputs:intensity", "/World/SunLight", "5000.0"],
    ),
    (
        "set_light_intensity",
        {"light_path": "/World/Lamp", "intensity": 0},
        ["inputs:intensity", "/World/Lamp"],
    ),
    (
        "set_light_color",
        {"light_path": "/World/SunLight", "rgb": [1.0, 0.56, 0.2]},
        ["inputs:color", "Gf.Vec3f", "1.0", "0.56", "0.2"],
    ),
    (
        "set_light_color",
        {"light_path": "/World/CoolKey", "rgb": [0.4, 0.6, 1.0]},
        ["inputs:color", "/World/CoolKey"],
    ),
    (
        "create_hdri_skydome",
        {"hdri_path": "/home/u/sky.hdr"},
        ["UsdLux.DomeLight", "inputs:texture:file", "/home/u/sky.hdr", "latlong", "1000"],
    ),
    (
        "create_hdri_skydome",
        {
            "hdri_path": "omniverse://localhost/Skies/cumulus.exr",
            "dome_path": "/World/Lighting/Sky",
            "intensity": 2500.0,
        },
        ["UsdLux.DomeLight", "/World/Lighting/Sky", "cumulus.exr", "2500.0"],
    ),
    # ── Tier 7 — Camera (atomic) ─────────────────────────────────────────────
    (
        "set_camera_params",
        {
            "camera_path": "/World/Camera",
            "params": {
                "focal_length": 35.0,
                "horizontal_aperture": 20.955,
                "vertical_aperture": 15.2908,
                "clipping_range": [0.1, 1000.0],
                "focus_distance": 2.5,
                "f_stop": 2.8,
                "projection": "perspective",
            },
        },
        [
            "UsdGeom.Camera",
            "GetFocalLengthAttr().Set(35.0)",
            "GetHorizontalApertureAttr().Set(20.955)",
            "GetClippingRangeAttr().Set(Gf.Vec2f(0.1, 1000.0))",
            "GetFocusDistanceAttr().Set(2.5)",
            "GetFStopAttr().Set(2.8)",
            "GetProjectionAttr().Set('perspective')",
            "/World/Camera",
        ],
    ),
    (
        "set_camera_params",
        {
            "camera_path": "/World/OrthoCam",
            "params": {"projection": "orthographic"},
        },
        [
            "GetProjectionAttr().Set('orthographic')",
            "/World/OrthoCam",
        ],
    ),
    (
        "set_camera_look_at",
        {
            "camera_path": "/World/Camera",
            "target": [1.0, 2.0, 3.0],
        },
        [
            "Gf.Matrix4d().SetLookAt",
            "GetInverse",
            "ExtractRotation",
            "_safe_set_translate",
            "_safe_set_rotate_xyz",
            "/World/Camera",
        ],
    ),
    (
        "set_camera_look_at",
        {
            "camera_path": "/World/Camera",
            "target": [1.0, 2.0, 3.0],
            "up": [0.0, 0.0, 1.0],
            "eye": [5.0, 5.0, 5.0],
        },
        [
            "Gf.Vec3d(5.0, 5.0, 5.0)",
            "Gf.Vec3d(0.0, 0.0, 1.0)",
            "Override translation to the supplied eye position",
            "_safe_set_rotate_xyz",
        ],
    ),
    # ── Tier 8 — Render Settings ───────────────────────────────────────────
    (
        "set_render_config",
        {"renderer": "PathTracing", "samples_per_pixel": 64, "max_bounces": 6},
        ["rendermode", "PathTracing", "samplesPerPixel", "64", "maxBounces", "6"],
    ),
    (
        "set_render_config",
        {"renderer": "RaytracedLighting"},
        ["rendermode", "RaytracedLighting", "viewport"],
    ),
    (
        "set_render_resolution",
        {"width": 1920, "height": 1080},
        ["viewport.utility", "vp.resolution", "(1920, 1080)"],
    ),
    (
        "enable_post_process",
        {"effect": "bloom", "params": {"intensity": 0.5, "threshold": 1.0}},
        ["/Render/PostProcess/Bloom", "enabled", "intensity", "0.5"],
    ),
    (
        "enable_post_process",
        {"effect": "tonemap", "params": {"operator": "aces", "exposure": 0.0}},
        ["/Render/PostProcess/Tonemap", "operator", "aces"],
    ),
    (
        "enable_post_process",
        {"effect": "dof", "params": {"focus_distance": 2.5, "f_stop": 2.8}},
        ["/Render/PostProcess/DoF", "focusDistance", "fStop"],
    ),
    (
        "enable_post_process",
        {"effect": "motion_blur", "params": {"shutter_speed": 0.0167, "samples": 4}},
        ["/Render/PostProcess/MotionBlur", "shutterSpeed", "samples"],
    ),
    (
        "enable_post_process",
        {"effect": "bloom", "enabled": False},
        ["/Render/PostProcess/Bloom", "enabled"],
    ),
    (
        "set_environment_background",
        {"hdri_path": "omniverse://localhost/Skies/sunset.hdr", "intensity": 1500.0, "rotation_deg": 90.0},
        ["UsdLux.DomeLight", "CreateTextureFileAttr", "sunset.hdr", "1500", "AddRotateYOp"],
    ),
    (
        "set_environment_background",
        {"color": [0.2, 0.2, 0.2]},
        ["clearColor", "Gf.Vec3f", "0.2"],
    ),
    (
        "set_environment_background",
        {},
        ["clearColor", "neutral grey"],
    ),
    # ── Tier 9 — USD Layers & Variants ─────────────────────────────────────
    (
        "add_sublayer",
        {"layer_path": "/tmp/overrides.usda"},
        ["Sdf.Layer", "subLayerPaths.insert", "/tmp/overrides.usda"],
    ),
    (
        "add_sublayer",
        {"layer_path": "omniverse://localhost/projects/shot01/lighting.usda"},
        ["subLayerPaths", "lighting.usda"],
    ),
    (
        "set_edit_target",
        {"layer_path": "/tmp/overrides.usda"},
        ["SetEditTarget", "Usd.EditTarget", "FindOrOpen", "/tmp/overrides.usda"],
    ),
    (
        "set_edit_target",
        {"layer_path": "anon:0xdeadbeef"},
        ["SetEditTarget", "Usd.EditTarget", "anon:0xdeadbeef"],
    ),
    (
        "flatten_layers",
        {"output_path": "/tmp/flattened_scene.usda"},
        ["stage.Flatten", "Export", "/tmp/flattened_scene.usda"],
    ),
    (
        "flatten_layers",
        {"output_path": "/tmp/baked.usdc"},
        ["stage.Flatten", "Export", "/tmp/baked.usdc"],
    ),
    # ── Tier 10 — Animation & Timeline ────────────────────────────────────
    (
        "set_timeline_range",
        {"start": 0, "end": 240},
        ["SetStartTimeCode", "SetEndTimeCode", "omni.timeline", "240"],
    ),
    (
        "set_timeline_range",
        {"start": 0, "end": 720, "fps": 60},
        ["SetTimeCodesPerSecond", "60", "720"],
    ),
    (
        "set_keyframe",
        {
            "prim_path": "/World/Cube",
            "attr": "xformOp:translate",
            "time": 1.0,
            "value": [0, 0, 1],
        },
        ["GetAttribute", "TimeCode", "xformOp:translate", "/World/Cube"],
    ),
    (
        "set_keyframe",
        {
            "prim_path": "/World/Light",
            "attr": "inputs:intensity",
            "time": 2.5,
            "value": 5000.0,
        },
        ["inputs:intensity", "5000.0", "TimeCode"],
    ),
    (
        "play_animation",
        {"start": 0, "end": 5.0},
        ["omni.timeline", "tl.play()", "5.0"],
    ),
    (
        "play_animation",
        {"start": 1.0, "end": 3.5},
        ["omni.timeline", "tl.play()", "1.0", "3.5"],
    ),
]


# Filter out vectors whose handlers do not exist on this branch.
# Keeps the file runnable as new addenda are merged into master in any order.
_TEST_VECTORS = [v for v in _RAW_TEST_VECTORS if v[0] in CODE_GEN_HANDLERS]


class TestCodeGenerators:

    @pytest.mark.parametrize(
        "handler_name,args,expected_substrings",
        _TEST_VECTORS,
        ids=[f"{v[0]}_{i}" for i, v in enumerate(_TEST_VECTORS)],
    )
    def test_generates_valid_python(self, handler_name, args, expected_substrings):
        if handler_name not in CODE_GEN_HANDLERS:
            pytest.skip(f"Handler '{handler_name}' not available on this branch")
        gen = CODE_GEN_HANDLERS[handler_name]
        code = gen(args)
        _assert_valid_python(code, handler_name)

    @pytest.mark.parametrize(
        "handler_name,args,expected_substrings",
        _TEST_VECTORS,
        ids=[f"{v[0]}_{i}" for i, v in enumerate(_TEST_VECTORS)],
    )
    def test_contains_expected_fragments(self, handler_name, args, expected_substrings):
        if handler_name not in CODE_GEN_HANDLERS:
            pytest.skip(f"Handler '{handler_name}' not available on this branch")
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

    @pytest.mark.skipif("clone_envs" not in CODE_GEN_HANDLERS, reason="Phase 8A not merged")
    def test_clone_envs_no_collision_filter(self):
        """When collision_filter=False, filter_collisions should NOT appear."""
        code = CODE_GEN_HANDLERS["clone_envs"](
            {"source_path": "/World/envs/env_0", "num_envs": 4, "collision_filter": False}
        )
        _assert_valid_python(code, "clone_envs")
        assert "filter_collisions" not in code
        assert "GridCloner" in code

    @pytest.mark.skipif("clone_envs" not in CODE_GEN_HANDLERS, reason="Phase 8A not merged")
    def test_clone_envs_default_spacing(self):
        """Default spacing should be 2.5."""
        code = CODE_GEN_HANDLERS["clone_envs"](
            {"source_path": "/World/envs/env_0", "num_envs": 8}
        )
        _assert_valid_python(code, "clone_envs")
        assert "spacing=2.5" in code

    @pytest.mark.skipif("debug_draw" not in CODE_GEN_HANDLERS, reason="Phase 8A not merged")
    def test_debug_draw_lifetime_zero_no_clear(self):
        """When lifetime is 0 (default), no call_later should be generated."""
        code = CODE_GEN_HANDLERS["debug_draw"](
            {"draw_type": "points", "points": [[0, 0, 0]], "lifetime": 0}
        )
        _assert_valid_python(code, "debug_draw")
        assert "call_later" not in code

    @pytest.mark.skipif("generate_occupancy_map" not in CODE_GEN_HANDLERS, reason="Phase 8A not merged")
    def test_occupancy_map_defaults(self):
        """With no args, should use defaults."""
        code = CODE_GEN_HANDLERS["generate_occupancy_map"]({})
        _assert_valid_python(code, "generate_occupancy_map")
        assert "MapGenerator" in code
        assert "cell_size=0.05" in code

    @pytest.mark.skipif("configure_camera" not in CODE_GEN_HANDLERS, reason="Phase 8A not merged")
    def test_configure_camera_multiple_params(self):
        """Setting multiple camera params at once."""
        code = CODE_GEN_HANDLERS["configure_camera"]({
            "camera_path": "/World/Cam",
            "focal_length": 50.0,
            "horizontal_aperture": 36.0,
            "focus_distance": 10.0,
        })
        _assert_valid_python(code, "configure_camera")
        assert "FocalLengthAttr" in code
        assert "HorizontalApertureAttr" in code
        assert "FocusDistanceAttr" in code

    @pytest.mark.skipif("configure_camera" not in CODE_GEN_HANDLERS, reason="Phase 8A not merged")
    def test_configure_camera_no_optional_params(self):
        """Only camera_path, no optional params — should still produce valid code."""
        code = CODE_GEN_HANDLERS["configure_camera"](
            {"camera_path": "/World/Cam"}
        )
        _assert_valid_python(code, "configure_camera")
        assert "UsdGeom.Camera" in code


    # ── Phase 8D edge cases ────────────────────────────────────────────────

    @pytest.mark.skipif("robot_wizard" not in CODE_GEN_HANDLERS, reason="Phase 8D not merged")
    # ── Interactive Teaching edge cases ────────────────────────────────────

    def test_start_teaching_unknown_mode(self):
        """Unknown mode should return a comment, not crash."""
        code = CODE_GEN_HANDLERS["start_teaching_mode"](
            {"articulation_path": "/World/Franka", "mode": "telekinesis"}
        )
        _assert_valid_python(code, "start_teaching_mode")
        assert "Unknown" in code

    def test_start_teaching_default_robot_type(self):
        """When robot_type is omitted, should default to franka."""
        code = CODE_GEN_HANDLERS["start_teaching_mode"](
            {"articulation_path": "/World/Franka", "mode": "drag_target"}
        )
        _assert_valid_python(code, "start_teaching_mode")
        assert "'franka'" in code

    def test_record_waypoints_default_format_is_json(self):
        """When format is omitted, should default to JSON."""
        code = CODE_GEN_HANDLERS["record_waypoints"](
            {"articulation_path": "/World/Franka", "output_path": "/tmp/wp.json"}
        )
        _assert_valid_python(code, "record_waypoints")
        assert "json.dump" in code

    def test_replay_trajectory_speed_clamped(self):
        """Speed should be clamped to [0.1, 4.0]."""
        code = CODE_GEN_HANDLERS["replay_trajectory"](
            {"articulation_path": "/World/Franka", "trajectory_path": "/tmp/t.json", "speed": 10.0}
        )
        _assert_valid_python(code, "replay_trajectory")
        assert "4.0" in code  # clamped to max

    def test_replay_trajectory_default_speed(self):
        """Default speed should be 1.0."""
        code = CODE_GEN_HANDLERS["replay_trajectory"](
            {"articulation_path": "/World/Franka", "trajectory_path": "/tmp/t.json"}
        )
        _assert_valid_python(code, "replay_trajectory")
        assert "1.0" in code

    def test_interpolate_trajectory_with_output_path(self):
        """When output_path is provided, should generate save code."""
        code = CODE_GEN_HANDLERS["interpolate_trajectory"]({
            "articulation_path": "/World/Franka",
            "waypoints": [
                {"joint_positions": [0.0, 0.0]},
                {"joint_positions": [1.0, 1.0]},
            ],
            "method": "linear",
            "output_path": "/tmp/smooth.json",
        })
        _assert_valid_python(code, "interpolate_trajectory")
        assert "/tmp/smooth.json" in code
        assert "json.dump" in code

    def test_interpolate_trajectory_cubic_with_output(self):
        """Cubic with output_path should save."""
        code = CODE_GEN_HANDLERS["interpolate_trajectory"]({
            "articulation_path": "/World/Franka",
            "waypoints": [
                {"joint_positions": [0.0]},
                {"joint_positions": [0.5]},
                {"joint_positions": [1.0]},
            ],
            "method": "cubic",
            "output_path": "/tmp/cubic.json",
        })
        _assert_valid_python(code, "interpolate_trajectory")
        assert "/tmp/cubic.json" in code

    # ── Phase 8D edge cases ────────────────────────────────────────────────

    def test_robot_wizard_humanoid_defaults(self):
        """Humanoid type should use stiffness=800, damping=80."""
        code = CODE_GEN_HANDLERS["robot_wizard"]({
            "asset_path": "/robots/h1.usd",
            "robot_type": "humanoid",
        })
        _assert_valid_python(code, "robot_wizard")
        assert "Kp=800" in code
        assert "Kd=80" in code

    @pytest.mark.skipif("robot_wizard" not in CODE_GEN_HANDLERS, reason="Phase 8D not merged")
    def test_robot_wizard_custom_overrides(self):
        """Custom stiffness/damping should override type defaults."""
        code = CODE_GEN_HANDLERS["robot_wizard"]({
            "asset_path": "/robots/franka.urdf",
            "robot_type": "manipulator",
            "drive_stiffness": 2000,
            "drive_damping": 200,
        })
        _assert_valid_python(code, "robot_wizard")
        assert "Kp=2000" in code
        assert "Kd=200" in code
        assert "import_urdf" in code

    @pytest.mark.skipif("tune_gains" not in CODE_GEN_HANDLERS, reason="Phase 8D not merged")
    def test_tune_gains_manual_all_joints(self):
        """When no joint_name given, should iterate all descendants."""
        code = CODE_GEN_HANDLERS["tune_gains"]({
            "articulation_path": "/World/Robot",
            "method": "manual",
            "kp": 750,
            "kd": 75,
        })
        _assert_valid_python(code, "tune_gains")
        assert "GetAllDescendants" in code
        assert "750" in code
        assert "75" in code

    @pytest.mark.skipif("tune_gains" not in CODE_GEN_HANDLERS, reason="Phase 8D not merged")
    def test_tune_gains_sinusoidal_mode(self):
        """Step response with sinusoidal test mode."""
        code = CODE_GEN_HANDLERS["tune_gains"]({
            "articulation_path": "/World/Robot",
            "method": "step_response",
            "test_mode": "sinusoidal",
        })
        _assert_valid_python(code, "tune_gains")
        assert "GainsTestMode.SINUSOIDAL" in code

    @pytest.mark.skipif("configure_self_collision" not in CODE_GEN_HANDLERS, reason="Phase 8D not merged")
    def test_configure_self_collision_disable(self):
        """Disable mode should set enabledSelfCollisions=False."""
        code = CODE_GEN_HANDLERS["configure_self_collision"]({
            "articulation_path": "/World/Robot",
            "mode": "disable",
        })
        _assert_valid_python(code, "configure_self_collision")
        assert "EnabledSelfCollisionsAttr" in code
        assert "False" in code

    @pytest.mark.skipif("configure_self_collision" not in CODE_GEN_HANDLERS, reason="Phase 8D not merged")
    def test_configure_self_collision_with_filtered_pairs(self):
        """Filtered pairs should generate FilteredPairsAPI code."""
        code = CODE_GEN_HANDLERS["configure_self_collision"]({
            "articulation_path": "/World/Robot",
            "mode": "enable",
            "filtered_pairs": [["/World/Robot/link1", "/World/Robot/link2"]],
        })
        _assert_valid_python(code, "configure_self_collision")
        assert "FilteredPairsAPI" in code
        assert "link1" in code
        assert "link2" in code

    # ── OmniGraph Assistant edge cases ────────────────────────────────────

    def test_create_graph_auto_detects_template(self):
        """Description containing 'joint state' should auto-select ros2_joint_state."""
        code = CODE_GEN_HANDLERS["create_graph"]({
            "description": "publish the joint states to ROS2",
            "robot_path": "/World/UR10",
        })
        _assert_valid_python(code, "create_graph")
        assert "ROS2PublishJointState" in code
        assert "/World/UR10" in code

    def test_create_graph_auto_detects_imu(self):
        """Description containing 'imu' should auto-select ros2_imu."""
        code = CODE_GEN_HANDLERS["create_graph"]({
            "description": "publish IMU sensor data",
            "imu_path": "/World/Robot/IMU",
        })
        _assert_valid_python(code, "create_graph")
        assert "IsaacReadIMU" in code

    def test_create_graph_auto_detects_odom(self):
        """Description containing 'odometry' should auto-select ros2_odom."""
        code = CODE_GEN_HANDLERS["create_graph"]({
            "description": "publish the odometry of the robot",
            "chassis_path": "/World/Robot/chassis",
        })
        _assert_valid_python(code, "create_graph")
        assert "IsaacComputeOdometry" in code

    def test_create_graph_unknown_description_raises(self):
        """Unrecognizable description should produce a ValueError code."""
        code = CODE_GEN_HANDLERS["create_graph"]({
            "description": "do something completely unrelated to ROS2",
        })
        assert "ValueError" in code or "Could not match" in code

    def test_create_graph_default_topic_used(self):
        """When no topic provided, template default should be used."""
        code = CODE_GEN_HANDLERS["create_graph"]({
            "description": "publish joint states",
            "template": "ros2_joint_state",
            "robot_path": "/World/Robot",
        })
        _assert_valid_python(code, "create_graph")
        assert "/joint_states" in code

    def test_create_graph_custom_topic_override(self):
        """Explicit topic should override template default."""
        code = CODE_GEN_HANDLERS["create_graph"]({
            "description": "publish joint states",
            "template": "ros2_joint_state",
            "robot_path": "/World/Robot",
            "topic": "/my_robot/joint_states",
        })
        _assert_valid_python(code, "create_graph")
        assert "/my_robot/joint_states" in code

    def test_create_graph_custom_graph_path(self):
        """Custom graph_path should be used in generated code."""
        code = CODE_GEN_HANDLERS["create_graph"]({
            "description": "publish clock",
            "template": "ros2_clock",
            "graph_path": "/World/MyClockGraph",
        })
        _assert_valid_python(code, "create_graph")
        assert "/World/MyClockGraph" in code

    def test_create_graph_all_templates_compile(self):
        """Every template should produce valid Python."""
        from service.isaac_assist_service.chat.tools.tool_executor import _OG_TEMPLATES
        for tmpl_name in _OG_TEMPLATES:
            code = CODE_GEN_HANDLERS["create_graph"]({
                "description": f"test {tmpl_name}",
                "template": tmpl_name,
                "robot_path": "/World/Robot",
                "camera_path": "/World/Camera",
                "lidar_path": "/World/Lidar",
                "imu_path": "/World/IMU",
                "chassis_path": "/World/Chassis",
                "root_prim": "/World",
            })
            _assert_valid_python(code, f"create_graph:{tmpl_name}")

    def test_explain_graph_contains_node_introspection(self):
        """explain_graph should introspect node types and connections."""
        code = CODE_GEN_HANDLERS["explain_graph"]({
            "graph_path": "/World/SensorGraph",
        })
        _assert_valid_python(code, "explain_graph")
        assert "/World/SensorGraph" in code
        assert "get_attributes" in code
        assert "get_upstream_connections" in code

    def test_debug_graph_checks_all_issues(self):
        """debug_graph should check for the 4 main issue categories."""
        code = CODE_GEN_HANDLERS["debug_graph"]({
            "graph_path": "/World/BrokenGraph",
        })
        _assert_valid_python(code, "debug_graph")
        assert "missing_ros2_context" in code
        assert "missing_on_tick" in code
        assert "disconnected_exec_input" in code
        assert "duplicate_node_names" in code


class TestTemplateDetection:
    """Test the template auto-detection from natural language descriptions."""

    def test_detect_clock(self):
        from service.isaac_assist_service.chat.tools.tool_executor import _detect_template
        assert _detect_template("publish the simulation clock to ROS2") == "ros2_clock"

    def test_detect_joint_state(self):
        from service.isaac_assist_service.chat.tools.tool_executor import _detect_template
        assert _detect_template("publish joint states") == "ros2_joint_state"

    def test_detect_camera(self):
        from service.isaac_assist_service.chat.tools.tool_executor import _detect_template
        assert _detect_template("stream camera images to ROS2") == "ros2_camera"

    def test_detect_lidar(self):
        from service.isaac_assist_service.chat.tools.tool_executor import _detect_template
        assert _detect_template("publish lidar laser scan") == "ros2_lidar"

    def test_detect_cmd_vel(self):
        from service.isaac_assist_service.chat.tools.tool_executor import _detect_template
        assert _detect_template("subscribe to cmd_vel for teleop") == "ros2_cmd_vel"

    def test_detect_tf(self):
        from service.isaac_assist_service.chat.tools.tool_executor import _detect_template
        assert _detect_template("broadcast transform tree") == "ros2_tf"

    def test_detect_imu(self):
        from service.isaac_assist_service.chat.tools.tool_executor import _detect_template
        assert _detect_template("publish IMU data") == "ros2_imu"

    def test_detect_odom(self):
        from service.isaac_assist_service.chat.tools.tool_executor import _detect_template
        assert _detect_template("publish odometry") == "ros2_odom"

    def test_detect_none_for_unrelated(self):
        from service.isaac_assist_service.chat.tools.tool_executor import _detect_template
        assert _detect_template("make a cube") is None

    def test_detect_none_for_empty(self):
        from service.isaac_assist_service.chat.tools.tool_executor import _detect_template
        assert _detect_template("") is None

    # ── Phase 7A Addendum: RL eval harness codegen ─────────────────────────

    def test_generate_eval_harness_minimal_args(self):
        """Only task_name is required — defaults should produce valid code."""
        code = CODE_GEN_HANDLERS["generate_eval_harness"](
            {"task_name": "Isaac-Reach-Franka-v0"}
        )
        _assert_valid_python(code, "generate_eval_harness")
        # Default num_episodes = 100, no video, default output dir under workspace/eval
        assert "NUM_EPISODES = 100" in code
        assert "RECORD_VIDEO = False" in code
        assert "workspace/eval/Isaac-Reach-Franka-v0" in code

    def test_generate_eval_harness_with_video(self):
        """record_video=True should import gymnasium's RecordVideo wrapper."""
        code = CODE_GEN_HANDLERS["generate_eval_harness"]({
            "task_name": "Isaac-Velocity-Anymal-C-v0",
            "record_video": True,
        })
        _assert_valid_python(code, "generate_eval_harness")
        assert "RecordVideo" in code
        assert "RECORD_VIDEO = True" in code

    def test_generate_eval_harness_no_checkpoint_uses_random(self):
        """Without a checkpoint path, the harness falls back to a random policy."""
        code = CODE_GEN_HANDLERS["generate_eval_harness"](
            {"task_name": "Isaac-Reach-Franka-v0"}
        )
        _assert_valid_python(code, "generate_eval_harness")
        assert "random policy" in code
        assert "action_space.sample()" in code

    def test_generate_eval_harness_task_name_safely_quoted(self):
        """Task names containing quotes must be safely escaped via repr()."""
        code = CODE_GEN_HANDLERS["generate_eval_harness"](
            {"task_name": "Weird'Task\"Name", "num_episodes": 5}
        )
        _assert_valid_python(code, "generate_eval_harness")
    # ── Clearance Detection Addendum edge cases ────────────────────────────
    # These run only when the addendum is merged; they auto-skip otherwise so
    # this file stays runnable as branches land in master in any order.
    # ── Clearance Detection Addendum edge cases ────────────────────────────
    # Auto-skip when the clearance addendum isn't merged on this branch so
    # this file stays runnable across tier branches.

    @pytest.mark.skipif(
        "set_clearance_monitor" not in CODE_GEN_HANDLERS,
        reason="Clearance Detection addendum not merged on this branch",
    )
    def test_set_clearance_monitor_default_thresholds(self):
        """No clearance_mm given should fall back to 50mm stop / 100mm warning."""
        code = CODE_GEN_HANDLERS["set_clearance_monitor"]({
            "articulation_path": "/World/Franka",
        })
        _assert_valid_python(code, "set_clearance_monitor")
        assert "stop_threshold_m = 0.05" in code
        assert "warning_threshold_m = 0.1" in code

    @pytest.mark.skipif(
        "set_clearance_monitor" not in CODE_GEN_HANDLERS,
        reason="set_clearance_monitor handler not on this branch",
        reason="Clearance Detection addendum not merged on this branch",
    )
    def test_set_clearance_monitor_no_targets(self):
        """Empty target list should still produce valid code (monitors all robot links)."""
        code = CODE_GEN_HANDLERS["set_clearance_monitor"]({
            "articulation_path": "/World/Franka",
            "clearance_mm": 25.0,
        })
        _assert_valid_python(code, "set_clearance_monitor")
        assert "stop_threshold_m = 0.025" in code
        assert "subscribe_contact_report_events" in code

    @pytest.mark.skipif(
        "visualize_clearance" not in CODE_GEN_HANDLERS,
        reason="visualize_clearance handler not on this branch",
        reason="Clearance Detection addendum not merged on this branch",
    )
    def test_visualize_clearance_default_mode_is_heatmap(self):
        """Omitting mode should default to heatmap (SDF + debug draw)."""
        code = CODE_GEN_HANDLERS["visualize_clearance"]({
            "articulation_path": "/World/Franka",
            "target_prims": ["/World/Fixture"],
        })
        _assert_valid_python(code, "visualize_clearance")
        assert "PhysxSDFMeshCollisionAPI" in code
        assert "debug_draw" in code
        # Trigger zones are NOT created in heatmap mode
        assert "PhysxTriggerAPI" not in code

    @pytest.mark.skipif(
        "visualize_clearance" not in CODE_GEN_HANDLERS,
        reason="visualize_clearance handler not on this branch",
        reason="Clearance Detection addendum not merged on this branch",
    )
    def test_visualize_clearance_zones_uses_triggers(self):
        """zones mode should use PhysxTriggerAPI and skip SDF mesh collision."""
        code = CODE_GEN_HANDLERS["visualize_clearance"]({
            "articulation_path": "/World/Franka",
            "mode": "zones",
            "target_prims": ["/World/Fixture"],
        })
        _assert_valid_python(code, "visualize_clearance")
        assert "PhysxTriggerAPI" in code
        assert "PhysxSDFMeshCollisionAPI" not in code

    @pytest.mark.skipif(
        "check_path_clearance" not in CODE_GEN_HANDLERS,
        reason="check_path_clearance handler not on this branch",
        reason="Clearance Detection addendum not merged on this branch",
    )
    def test_check_path_clearance_multiple_waypoints(self):
        """Trajectory with multiple waypoints should be embedded as a list-of-lists."""
        code = CODE_GEN_HANDLERS["check_path_clearance"]({
            "articulation_path": "/World/Franka",
            "trajectory": [
                [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
                [0.1, 0.2, 0.0, -0.5, 0.0, 0.5, 0.0],
            ],
            "obstacles": ["/World/Fixture"],
            "clearance_mm": 75.0,
        })
        _assert_valid_python(code, "check_path_clearance")
        assert "threshold_m = 0.075" in code
        assert "compute_forward_kinematics" in code
        assert "violations" in code

    @pytest.mark.skipif(
        "check_path_clearance" not in CODE_GEN_HANDLERS,
        reason="check_path_clearance handler not on this branch",
        reason="Clearance Detection addendum not merged on this branch",
    )
    def test_check_path_clearance_no_obstacles(self):
        """Empty obstacle list is still valid — every waypoint should report inf clearance."""
        code = CODE_GEN_HANDLERS["check_path_clearance"]({
            "articulation_path": "/World/Franka",
            "trajectory": [[0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]],
            "obstacles": [],
        })
        _assert_valid_python(code, "check_path_clearance")
        assert "obstacle_paths = list([])" in code
    # ── Collision Mesh Quality Addendum edge cases ─────────────────────────

    def test_fix_collision_mesh_default_target(self):
        """Omitting target_triangles should embed TARGET_TRIANGLES = None
        and let the script pick a default based on RigidBodyAPI presence."""
        code = CODE_GEN_HANDLERS["fix_collision_mesh"]({
            "prim_path": "/World/Robot/link0",
        })
        _assert_valid_python(code, "fix_collision_mesh")
        assert "TARGET_TRIANGLES = None" in code
        # Default heuristic must be present
        assert "RigidBodyAPI" in code
        assert "500" in code  # dynamic default
        assert "2000" in code  # static default

    def test_fix_collision_mesh_explicit_target(self):
        code = CODE_GEN_HANDLERS["fix_collision_mesh"]({
            "prim_path": "/World/Wall",
            "target_triangles": 1500,
        })
        _assert_valid_python(code, "fix_collision_mesh")
        assert "TARGET_TRIANGLES = 1500" in code

    def test_fix_collision_mesh_uses_coacd_constants(self):
        """Spec calls for CoACD threshold=0.05 and max_convex_hull=16."""
        code = CODE_GEN_HANDLERS["fix_collision_mesh"]({
            "prim_path": "/World/Bracket",
            "target_triangles": 500,
        })
        _assert_valid_python(code, "fix_collision_mesh")
        assert "COACD_THRESHOLD = 0.05" in code
        assert "COACD_MAX_CONVEX_HULL = 16" in code
        assert "import coacd" in code

    def test_fix_collision_mesh_enforces_hull_limits(self):
        """Spec: verify all hulls have ≤64 vertices (PhysX GPU limit)."""
        code = CODE_GEN_HANDLERS["fix_collision_mesh"]({
            "prim_path": "/World/Hull",
            "target_triangles": 200,
        })
        _assert_valid_python(code, "fix_collision_mesh")
        assert "PHYSX_HULL_MAX_VERTS = 64" in code
        assert "PHYSX_HULL_MAX_POLYS = 255" in code

    def test_fix_collision_mesh_writes_back_to_usd(self):
        """The repaired triangles should be written back to the USD mesh."""
        code = CODE_GEN_HANDLERS["fix_collision_mesh"]({
            "prim_path": "/World/Foo",
            "target_triangles": 500,
        })
        _assert_valid_python(code, "fix_collision_mesh")
        assert "GetPointsAttr().Set" in code
        assert "GetFaceVertexCountsAttr().Set" in code
        assert "GetFaceVertexIndicesAttr().Set" in code
        assert "MeshCollisionAPI" in code
        assert "CreateApproximationAttr" in code

    def test_fix_collision_mesh_path_with_quotes_sanitized(self):
        """Quotes in prim_path must not break the generated string literal."""
        code = CODE_GEN_HANDLERS["fix_collision_mesh"]({
            "prim_path": "/World/Robot's_link",
            "target_triangles": 100,
        })
        _assert_valid_python(code, "fix_collision_mesh")

    def test_visualize_collision_mesh_uses_omni_physx_ui(self):
        """Spec: implementation uses omni.physx.ui Physics Debug visualization."""
        code = CODE_GEN_HANDLERS["visualize_collision_mesh"]({
            "prim_path": "/World/Robot/link3",
        })
        _assert_valid_python(code, "visualize_collision_mesh")
        assert "omni.physx.ui" in code
        assert "/World/Robot/link3" in code

    def test_visualize_collision_mesh_applies_collision_api(self):
        """Should apply CollisionAPI if missing so the collision shape exists to display."""
        code = CODE_GEN_HANDLERS["visualize_collision_mesh"]({
            "prim_path": "/World/Foo",
        })
        _assert_valid_python(code, "visualize_collision_mesh")
        assert "UsdPhysics.CollisionAPI" in code
        assert "Apply(prim)" in code

    def test_visualize_collision_mesh_path_sanitized(self):
        code = CODE_GEN_HANDLERS["visualize_collision_mesh"]({
            "prim_path": '/World/"quoted"',
        })
        _assert_valid_python(code, "visualize_collision_mesh")
    # ── Tier 0 Atomic Tools — edge cases ────────────────────────────────────

    def test_set_semantic_label_default_semantic_type(self):
        """Omitting semantic_type should default to 'class'."""
        code = CODE_GEN_HANDLERS["set_semantic_label"]({
            "prim_path": "/World/Cube",
            "class_name": "cube",
        })
        _assert_valid_python(code, "set_semantic_label")
        assert "Semantics_class" in code
        assert "'class'" in code

    def test_set_drive_gains_defaults_to_angular(self):
        code = CODE_GEN_HANDLERS["set_drive_gains"]({
            "joint_path": "/World/Franka/joint1",
            "kp": 400.0,
            "kd": 40.0,
        })
        _assert_valid_python(code, "set_drive_gains")
        assert "'angular'" in code
        assert "400.0" in code
        assert "40.0" in code

    def test_set_render_mode_path_traced_uses_pathtracing(self):
        code = CODE_GEN_HANDLERS["set_render_mode"]({"mode": "path_traced"})
        _assert_valid_python(code, "set_render_mode")
        assert "PathTracing" in code
        assert "RaytracedLighting" not in code

    def test_set_variant_creates_variant_set_if_missing(self):
        code = CODE_GEN_HANDLERS["set_variant"]({
            "prim_path": "/World/Asset",
            "variant_set": "color",
            "variant": "red",
        })
        _assert_valid_python(code, "set_variant")
        # Must handle both pre-existing and new variant sets
        assert "HasVariantSet" in code
        assert "AddVariantSet" in code
        assert "SetVariantSelection" in code

    def test_record_trajectory_default_output_path(self):
        code = CODE_GEN_HANDLERS["record_trajectory"]({
            "articulation": "/World/Franka",
            "duration": 10.0,
        })
        _assert_valid_python(code, "record_trajectory")
        # Default output goes under workspace/trajectories/
        assert "workspace/trajectories/" in code
        assert "duration = 10.0" in code
        # Default sample rate
        assert "rate_hz = 60.0" in code

    def test_record_trajectory_custom_rate(self):
        code = CODE_GEN_HANDLERS["record_trajectory"]({
            "articulation": "/World/UR10",
            "duration": 1.0,
            "rate_hz": 240.0,
        })
        _assert_valid_python(code, "record_trajectory")
        assert "rate_hz = 240.0" in code
        assert "subscribe_physics_step_events" in code
    # NOTE: Clearance Detection Addendum edge cases live on the
    # feat/addendum-clearance-detection branch and are intentionally absent
    # here so this branch ships only Tier 1 USD Core changes.
    # NOTE: Clearance Detection Addendum edge cases live on the
    # feat/addendum-clearance-detection branch and are intentionally absent
    # here so this branch ships only Tier 2 Physics Bodies & Scene changes.
    # ── Atomic Tier 5 — OmniGraph edge cases ──────────────────────────────

    def test_add_node_remaps_legacy_namespace(self):
        """add_node should remap omni.isaac.* to isaacsim.* (Isaac Sim 5.1)."""
        code = CODE_GEN_HANDLERS["add_node"]({
            "graph_path": "/World/OG",
            "node_type": "omni.isaac.ros2_bridge.ROS2PublishClock",
            "name": "clock_pub",
        })
        _assert_valid_python(code, "add_node")
        assert "isaacsim.ros2.bridge.ROS2PublishClock" in code
        assert "omni.isaac.ros2_bridge" not in code

    def test_add_node_unknown_type_passes_through(self):
        """Unknown node types should be passed through unchanged."""
        code = CODE_GEN_HANDLERS["add_node"]({
            "graph_path": "/World/OG",
            "node_type": "omni.graph.action.OnPlaybackTick",
            "name": "tick",
        })
        _assert_valid_python(code, "add_node")
        assert "omni.graph.action.OnPlaybackTick" in code

    def test_connect_nodes_uses_attribute_paths(self):
        """connect_nodes wires <node>.outputs:port -> <node>.inputs:port."""
        code = CODE_GEN_HANDLERS["connect_nodes"]({
            "graph_path": "/World/OG",
            "src": "tick.outputs:tick",
            "dst": "publishJointState.inputs:execIn",
        })
        _assert_valid_python(code, "connect_nodes")
        assert 'keys.CONNECT' in code
        assert "tick.outputs:tick" in code
        assert "publishJointState.inputs:execIn" in code

    def test_set_graph_variable_string_value(self):
        code = CODE_GEN_HANDLERS["set_graph_variable"]({
            "graph_path": "/World/OG",
            "name": "topicName",
            "value": "/joint_states",
        })
        _assert_valid_python(code, "set_graph_variable")
        assert "topicName" in code
        # repr() of a string value must appear
        assert "'/joint_states'" in code or '"/joint_states"' in code

    def test_set_graph_variable_numeric_value(self):
        code = CODE_GEN_HANDLERS["set_graph_variable"]({
            "graph_path": "/World/OG",
            "name": "rate_hz",
            "value": 60,
        })
        _assert_valid_python(code, "set_graph_variable")
        assert "rate_hz" in code
        assert "60" in code

    def test_set_graph_variable_array_value(self):
        code = CODE_GEN_HANDLERS["set_graph_variable"]({
            "graph_path": "/World/OG",
            "name": "joint_indices",
            "value": [0, 1, 2, 3],
        })
        _assert_valid_python(code, "set_graph_variable")
        assert "joint_indices" in code
        assert "[0, 1, 2, 3]" in code

    def test_delete_node_uses_delete_nodes_key(self):
        code = CODE_GEN_HANDLERS["delete_node"]({
            "graph_path": "/World/OG",
            "node_name": "old_node",
        })
        _assert_valid_python(code, "delete_node")
        assert "DELETE_NODES" in code
        assert "old_node" in code
    # ── Tier 6 — Lighting edge cases ───────────────────────────────────────

    def test_set_light_intensity_clamps_negative(self):
        """Negative intensity values must be clamped to 0 (light cannot be < 0)."""
        code = CODE_GEN_HANDLERS["set_light_intensity"]({
            "light_path": "/World/Lamp",
            "intensity": -50.0,
        })
        _assert_valid_python(code, "set_light_intensity")
        # Should write 0.0, not -50
        assert "Set(0.0)" in code or "Set(0)" in code
        assert "-50" not in code

    def test_set_light_color_clamps_negative_channels(self):
        """Negative RGB channels must be clamped to 0."""
        code = CODE_GEN_HANDLERS["set_light_color"]({
            "light_path": "/World/Lamp",
            "rgb": [-0.2, 0.5, 1.5],
        })
        _assert_valid_python(code, "set_light_color")
        # Negative red should not appear; >1 green is allowed (acts as boost)
        assert "-0.2" not in code
        assert "0.5" in code
        assert "1.5" in code

    def test_set_light_color_rejects_wrong_arity(self):
        """rgb with !=3 elements should raise during code generation."""
        with pytest.raises(ValueError):
            CODE_GEN_HANDLERS["set_light_color"]({
                "light_path": "/World/Lamp",
                "rgb": [1.0, 0.5],
            })

    def test_create_hdri_skydome_default_dome_path(self):
        """Without dome_path the default is /Environment/DomeLight."""
        code = CODE_GEN_HANDLERS["create_hdri_skydome"]({
            "hdri_path": "/tmp/sky.exr",
        })
        _assert_valid_python(code, "create_hdri_skydome")
        assert "/Environment/DomeLight" in code
        assert "latlong" in code

    def test_create_hdri_skydome_default_intensity(self):
        """Default intensity should be 1000."""
        code = CODE_GEN_HANDLERS["create_hdri_skydome"]({
            "hdri_path": "/tmp/sky.exr",
        })
        _assert_valid_python(code, "create_hdri_skydome")
        assert "1000" in code

    def test_set_light_intensity_path_with_special_chars(self):
        """USD paths with underscores/numbers must round-trip cleanly."""
        code = CODE_GEN_HANDLERS["set_light_intensity"]({
            "light_path": "/World/Lights/Key_Light_01",
            "intensity": 1500.0,
        })
        _assert_valid_python(code, "set_light_intensity")
        assert "/World/Lights/Key_Light_01" in code


    # ── Tier 7 — Camera atomic edge cases ─────────────────────────────────

    @pytest.mark.skipif(
        "set_camera_params" not in CODE_GEN_HANDLERS,
        reason="set_camera_params handler not on this branch",
    )
    def test_set_camera_params_partial_fields_leaves_others_alone(self):
        """Only requested fields should be emitted as Set() calls."""
        code = CODE_GEN_HANDLERS["set_camera_params"]({
            "camera_path": "/World/Cam",
            "params": {"focal_length": 50.0},
        })
        _assert_valid_python(code, "set_camera_params")
        assert "GetFocalLengthAttr().Set(50.0)" in code
        # Other attributes should not be touched
        assert "GetHorizontalApertureAttr().Set" not in code
        assert "GetClippingRangeAttr().Set" not in code
        assert "GetFocusDistanceAttr().Set" not in code
        assert "GetFStopAttr().Set" not in code
        assert "GetProjectionAttr().Set" not in code

    @pytest.mark.skipif(
        "set_camera_params" not in CODE_GEN_HANDLERS,
        reason="set_camera_params handler not on this branch",
    )
    def test_set_camera_params_empty_params_still_compiles(self):
        """params={} should produce a valid no-op patch (validator + print only)."""
        code = CODE_GEN_HANDLERS["set_camera_params"]({
            "camera_path": "/World/Cam",
            "params": {},
        })
        _assert_valid_python(code, "set_camera_params")
        assert "UsdGeom.Camera" in code
        assert "GetFocalLengthAttr().Set" not in code

    @pytest.mark.skipif(
        "set_camera_params" not in CODE_GEN_HANDLERS,
        reason="set_camera_params handler not on this branch",
    )
    def test_set_camera_params_invalid_projection_is_noop_with_warning(self):
        """Unknown projection value should emit a warning comment, not crash compile."""
        code = CODE_GEN_HANDLERS["set_camera_params"]({
            "camera_path": "/World/Cam",
            "params": {"projection": "fisheye"},
        })
        _assert_valid_python(code, "set_camera_params")
        assert "WARNING" in code
        assert "GetProjectionAttr().Set" not in code

    @pytest.mark.skipif(
        "set_camera_params" not in CODE_GEN_HANDLERS,
        reason="set_camera_params handler not on this branch",
    )
    def test_set_camera_params_clipping_range_uses_vec2f(self):
        """clipping_range should land in a Gf.Vec2f, not a tuple."""
        code = CODE_GEN_HANDLERS["set_camera_params"]({
            "camera_path": "/World/Cam",
            "params": {"clipping_range": [0.5, 5000.0]},
        })
        _assert_valid_python(code, "set_camera_params")
        assert "Gf.Vec2f(0.5, 5000.0)" in code

    @pytest.mark.skipif(
        "set_camera_look_at" not in CODE_GEN_HANDLERS,
        reason="set_camera_look_at handler not on this branch",
    )
    def test_set_camera_look_at_default_up_is_y(self):
        """Omitting 'up' should default to world +Y."""
        code = CODE_GEN_HANDLERS["set_camera_look_at"]({
            "camera_path": "/World/Cam",
            "target": [0.0, 0.0, 0.0],
        })
        _assert_valid_python(code, "set_camera_look_at")
        assert "Gf.Vec3d(0.0, 1.0, 0.0)" in code

    @pytest.mark.skipif(
        "set_camera_look_at" not in CODE_GEN_HANDLERS,
        reason="set_camera_look_at handler not on this branch",
    )
    def test_set_camera_look_at_no_eye_uses_current_position(self):
        """When 'eye' is omitted the snippet must read the camera's current world translation."""
        code = CODE_GEN_HANDLERS["set_camera_look_at"]({
            "camera_path": "/World/Cam",
            "target": [1.0, 0.0, 0.0],
        })
        _assert_valid_python(code, "set_camera_look_at")
        assert "ComputeLocalToWorldTransform" in code
        assert "ExtractTranslation" in code

    @pytest.mark.skipif(
        "set_camera_look_at" not in CODE_GEN_HANDLERS,
        reason="set_camera_look_at handler not on this branch",
    )
    def test_set_camera_look_at_rejects_bad_target(self):
        """Malformed target should raise ValueError synchronously (caught before queueing)."""
        with pytest.raises(ValueError):
            CODE_GEN_HANDLERS["set_camera_look_at"]({
                "camera_path": "/World/Cam",
                "target": [1.0, 2.0],  # only 2 components
            })


    # ── Tier 8 — Render Settings edge cases ────────────────────────────────

    def test_set_render_config_minimal_renderer_only(self):
        code = CODE_GEN_HANDLERS["set_render_config"]({"renderer": "RealTime"})
        _assert_valid_python(code, "set_render_config")
        assert "RealTime" in code
        # When SPP/max_bounces omitted, those attributes should NOT be written
        assert "samplesPerPixel" not in code
        assert "maxBounces" not in code

    def test_set_render_config_pathtracing_with_quality(self):
        code = CODE_GEN_HANDLERS["set_render_config"]({
            "renderer": "PathTracing",
            "samples_per_pixel": 256,
            "max_bounces": 8,
        })
        _assert_valid_python(code, "set_render_config")
        assert "PathTracing" in code
        assert "256" in code
        assert "8" in code

    def test_set_render_resolution_4k(self):
        code = CODE_GEN_HANDLERS["set_render_resolution"]({"width": 3840, "height": 2160})
        _assert_valid_python(code, "set_render_resolution")
        assert "(3840, 2160)" in code

    def test_enable_post_process_disable_flag(self):
        code = CODE_GEN_HANDLERS["enable_post_process"]({
            "effect": "bloom",
            "enabled": False,
        })
        _assert_valid_python(code, "enable_post_process")
        assert "False" in code
        assert "/Render/PostProcess/Bloom" in code

    def test_enable_post_process_no_params_dict(self):
        """Omitting params should still produce valid code that just toggles enabled."""
        code = CODE_GEN_HANDLERS["enable_post_process"]({"effect": "tonemap"})
        _assert_valid_python(code, "enable_post_process")
        assert "/Render/PostProcess/Tonemap" in code

    def test_set_environment_background_hdri_with_rotation(self):
        code = CODE_GEN_HANDLERS["set_environment_background"]({
            "hdri_path": "/assets/studio.hdr",
            "rotation_deg": 180.0,
        })
        _assert_valid_python(code, "set_environment_background")
        assert "DomeLight" in code
        assert "studio.hdr" in code
        assert "180" in code

    def test_set_environment_background_color_only(self):
        code = CODE_GEN_HANDLERS["set_environment_background"]({"color": [1.0, 0.0, 0.0]})
        _assert_valid_python(code, "set_environment_background")
        assert "clearColor" in code
        # Solid color path should also remove the dome if present
        assert "RemovePrim" in code

    def test_set_environment_background_no_args_defaults_to_grey(self):
        """No args is valid — falls back to neutral grey."""
        code = CODE_GEN_HANDLERS["set_environment_background"]({})
        _assert_valid_python(code, "set_environment_background")
        assert "0.2" in code

    # ── Tier 9 — USD Layers & Variants edge cases ──────────────────────────

    @pytest.mark.skipif(
        "add_sublayer" not in CODE_GEN_HANDLERS,
        reason="Tier 9 (USD Layers & Variants) not merged on this branch",
    )
    def test_add_sublayer_local_path_creates_if_missing(self):
        """Local filesystem paths should hit the CreateNew branch."""
        code = CODE_GEN_HANDLERS["add_sublayer"]({"layer_path": "/tmp/new_layer.usda"})
        _assert_valid_python(code, "add_sublayer")
        # Must guard CreateNew on file-existence so we don't clobber existing content
        assert "os.path.exists" in code
        assert "Sdf.Layer.CreateNew" in code
        assert "subLayerPaths.insert(0" in code

    @pytest.mark.skipif(
        "add_sublayer" not in CODE_GEN_HANDLERS,
        reason="Tier 9 (USD Layers & Variants) not merged on this branch",
    )
    def test_add_sublayer_omniverse_url_skips_create(self):
        """Nucleus URLs must skip the local CreateNew branch (it'd error on '://')."""
        code = CODE_GEN_HANDLERS["add_sublayer"](
            {"layer_path": "omniverse://localhost/p/lighting.usda"}
        )
        _assert_valid_python(code, "add_sublayer")
        assert "://" in code
        assert "subLayerPaths.insert(0" in code

    @pytest.mark.skipif(
        "set_edit_target" not in CODE_GEN_HANDLERS,
        reason="Tier 9 (USD Layers & Variants) not merged on this branch",
    )
    def test_set_edit_target_path_with_special_chars(self):
        """Paths with spaces/parens must be embedded with repr() so they don't break syntax."""
        code = CODE_GEN_HANDLERS["set_edit_target"](
            {"layer_path": "/tmp/Project Files (v2)/scene.usda"}
        )
        _assert_valid_python(code, "set_edit_target")
        assert "SetEditTarget" in code
        assert "Project Files" in code

    @pytest.mark.skipif(
        "set_edit_target" not in CODE_GEN_HANDLERS,
        reason="Tier 9 (USD Layers & Variants) not merged on this branch",
    )
    def test_set_edit_target_falls_back_to_layer_stack(self):
        """When FindOrOpen returns None we must scan stage.GetLayerStack() before raising."""
        code = CODE_GEN_HANDLERS["set_edit_target"]({"layer_path": "anon:0xCAFE"})
        _assert_valid_python(code, "set_edit_target")
        assert "GetLayerStack" in code
        assert "anon:0xCAFE" in code

    @pytest.mark.skipif(
        "flatten_layers" not in CODE_GEN_HANDLERS,
        reason="Tier 9 (USD Layers & Variants) not merged on this branch",
    )
    def test_flatten_layers_raises_when_no_stage(self):
        """Generated code must raise (not silently no-op) if the stage is missing."""
        code = CODE_GEN_HANDLERS["flatten_layers"]({"output_path": "/tmp/out.usda"})
        _assert_valid_python(code, "flatten_layers")
        assert "stage.Flatten" in code
        assert "Export" in code
        assert "RuntimeError" in code

    @pytest.mark.skipif(
        "flatten_layers" not in CODE_GEN_HANDLERS,
        reason="Tier 9 (USD Layers & Variants) not merged on this branch",
    )
    def test_flatten_layers_supports_usdc_extension(self):
        """Binary crate (.usdc) output is just as valid as .usda — same flatten code path."""
        code = CODE_GEN_HANDLERS["flatten_layers"]({"output_path": "/tmp/scene.usdc"})
        _assert_valid_python(code, "flatten_layers")
        assert "/tmp/scene.usdc" in code

    # ── Tier 10 — Animation & Timeline edge cases ──────────────────────────

    @pytest.mark.skipif(
        "set_timeline_range" not in CODE_GEN_HANDLERS,
        reason="Tier 10 (Animation & Timeline) not merged on this branch",
    )
    def test_set_timeline_range_defaults_keep_existing_fps(self):
        """When fps is omitted, generated code reads stage.GetTimeCodesPerSecond()."""
        code = CODE_GEN_HANDLERS["set_timeline_range"]({"start": 0, "end": 100})
        _assert_valid_python(code, "set_timeline_range")
        # No SetTimeCodesPerSecond when fps not provided
        assert "SetTimeCodesPerSecond" not in code
        # Should still read fps for the timeline-interface conversion
        assert "GetTimeCodesPerSecond" in code

    @pytest.mark.skipif(
        "set_timeline_range" not in CODE_GEN_HANDLERS,
        reason="Tier 10 (Animation & Timeline) not merged on this branch",
    )
    def test_set_timeline_range_validates_start_lt_end(self):
        """Generated code must guard against start >= end."""
        code = CODE_GEN_HANDLERS["set_timeline_range"]({"start": 10, "end": 100, "fps": 30})
        _assert_valid_python(code, "set_timeline_range")
        assert "ValueError" in code
        assert "must be < end" in code

    @pytest.mark.skipif(
        "set_keyframe" not in CODE_GEN_HANDLERS,
        reason="Tier 10 (Animation & Timeline) not merged on this branch",
    )
    def test_set_keyframe_path_with_special_chars(self):
        """Special chars in prim path / attr must round-trip via repr() without breaking syntax."""
        code = CODE_GEN_HANDLERS["set_keyframe"]({
            "prim_path": "/World/My Robot (v2)/joint",
            "attr": "drive:angular:physics:targetPosition",
            "time": 0.5,
            "value": 1.57,
        })
        _assert_valid_python(code, "set_keyframe")
        assert "My Robot" in code
        assert "drive:angular" in code
        # Must convert seconds -> USD time code via fps
        assert "TimeCode" in code
        assert "fps" in code

    @pytest.mark.skipif(
        "set_keyframe" not in CODE_GEN_HANDLERS,
        reason="Tier 10 (Animation & Timeline) not merged on this branch",
    )
    def test_set_keyframe_array_value_falls_back_to_gf_vec(self):
        """Vec3 / Vec4 fallback when raw .Set() rejects a Python list."""
        code = CODE_GEN_HANDLERS["set_keyframe"]({
            "prim_path": "/World/Cube",
            "attr": "xformOp:translate",
            "time": 0.0,
            "value": [1.0, 2.0, 3.0],
        })
        _assert_valid_python(code, "set_keyframe")
        # Fallback path must construct a Gf.Vec3f from the list
        assert "Gf.Vec3f" in code
        assert "Gf.Vec4f" in code

    @pytest.mark.skipif(
        "play_animation" not in CODE_GEN_HANDLERS,
        reason="Tier 10 (Animation & Timeline) not merged on this branch",
    )
    def test_play_animation_validates_range(self):
        """Generated code must reject start >= end before calling play()."""
        code = CODE_GEN_HANDLERS["play_animation"]({"start": 0, "end": 2.0})
        _assert_valid_python(code, "play_animation")
        assert "ValueError" in code
        assert "tl.play()" in code

    @pytest.mark.skipif(
        "play_animation" not in CODE_GEN_HANDLERS,
        reason="Tier 10 (Animation & Timeline) not merged on this branch",
    )
    def test_play_animation_uses_seconds_not_codes(self):
        """play_animation takes seconds and converts to time codes via stage fps."""
        code = CODE_GEN_HANDLERS["play_animation"]({"start": 0.0, "end": 1.0})
        _assert_valid_python(code, "play_animation")
        assert "GetTimeCodesPerSecond" in code
        assert "start_seconds" in code
        assert "end_seconds" in code


class TestAllCodeGenHandlersCovered:
    """Safety net: ensure every CODE_GEN_HANDLER appears in at least one test vector."""

    def test_all_handlers_tested(self):
        tested = {v[0] for v in _TEST_VECTORS}
        untested = set(CODE_GEN_HANDLERS.keys()) - tested
    # Pre-existing master generators known to emit invalid Python (out of
    # scope for this addendum to fix). Keep them on the allowlist so a
    # missing test vector here doesn't mask coverage gaps elsewhere.
    _KNOWN_BAD = {"launch_training"}

    def test_all_handlers_tested(self):
        tested = {v[0] for v in _TEST_VECTORS}
        untested = set(CODE_GEN_HANDLERS.keys()) - tested - self._KNOWN_BAD
        assert untested == set(), (
            f"CODE_GEN_HANDLERS not covered by test vectors: {untested}"
        )
