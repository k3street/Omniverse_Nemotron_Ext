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
]


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


class TestAllCodeGenHandlersCovered:
    """Safety net: ensure every CODE_GEN_HANDLER appears in at least one test vector."""

    def test_all_handlers_tested(self):
        tested = {v[0] for v in _TEST_VECTORS}
        untested = set(CODE_GEN_HANDLERS.keys()) - tested
        assert untested == set(), (
            f"CODE_GEN_HANDLERS not covered by test vectors: {untested}"
        )
