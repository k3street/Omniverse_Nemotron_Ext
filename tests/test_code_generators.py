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
    """Safety net: ensure every CODE_GEN_HANDLER appears in at least one test vector."""

    def test_all_handlers_tested(self):
        tested = {v[0] for v in _TEST_VECTORS}
        untested = set(CODE_GEN_HANDLERS.keys()) - tested
        assert untested == set(), (
            f"CODE_GEN_HANDLERS not covered by test vectors: {untested}"
        )
