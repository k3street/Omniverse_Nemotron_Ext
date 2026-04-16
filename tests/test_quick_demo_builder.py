"""
L0 tests for the Quick Demo Builder tools.
Tests create_demo_scene (CODE_GEN), list_demo_types (DATA), and
add_demo_objects (CODE_GEN).
"""
import pytest

pytestmark = pytest.mark.l0

from service.isaac_assist_service.chat.tools.tool_executor import (
    CODE_GEN_HANDLERS,
    DATA_HANDLERS,
    _DEMO_TEMPLATES,
)


# ---------------------------------------------------------------------------
# Helper: compile check (same pattern as test_code_generators.py)
# ---------------------------------------------------------------------------

def _assert_valid_python(code: str, label: str):
    """Verify the generated code is syntactically valid Python."""
    try:
        compile(code, f"<generated:{label}>", "exec")
    except SyntaxError as e:
        pytest.fail(f"{label} generated invalid Python:\n{e}\n\nCode:\n{code}")


# ---------------------------------------------------------------------------
# Tests — create_demo_scene (CODE_GEN handler)
# ---------------------------------------------------------------------------

class TestCreateDemoScene:
    """Test the create_demo_scene code generator."""

    def test_handler_registered(self):
        assert "create_demo_scene" in CODE_GEN_HANDLERS

    @pytest.mark.parametrize("demo_type", [
        "pick_and_place", "navigation", "conveyor", "stacking", "inspection",
    ])
    def test_all_demo_types_compile(self, demo_type):
        gen = CODE_GEN_HANDLERS["create_demo_scene"]
        code = gen({"demo_type": demo_type})
        _assert_valid_python(code, f"create_demo_scene({demo_type})")

    @pytest.mark.parametrize("demo_type", [
        "pick_and_place", "navigation", "conveyor", "stacking", "inspection",
    ])
    def test_all_demo_types_have_physics_scene(self, demo_type):
        gen = CODE_GEN_HANDLERS["create_demo_scene"]
        code = gen({"demo_type": demo_type})
        assert "PhysicsScene" in code

    def test_pick_and_place_has_table(self):
        gen = CODE_GEN_HANDLERS["create_demo_scene"]
        code = gen({"demo_type": "pick_and_place"})
        assert "/World/Table" in code
        assert "DefinePrim" in code

    def test_pick_and_place_default_robot_is_franka(self):
        gen = CODE_GEN_HANDLERS["create_demo_scene"]
        code = gen({"demo_type": "pick_and_place"})
        assert "franka" in code.lower()

    def test_navigation_default_robot_is_nova_carter(self):
        gen = CODE_GEN_HANDLERS["create_demo_scene"]
        code = gen({"demo_type": "navigation"})
        assert "nova_carter" in code.lower()

    def test_navigation_has_goal_marker(self):
        gen = CODE_GEN_HANDLERS["create_demo_scene"]
        code = gen({"demo_type": "navigation"})
        assert "GoalMarker" in code

    def test_navigation_has_obstacles(self):
        gen = CODE_GEN_HANDLERS["create_demo_scene"]
        code = gen({"demo_type": "navigation"})
        assert "Obstacle" in code

    def test_conveyor_has_conveyor_belt(self):
        gen = CODE_GEN_HANDLERS["create_demo_scene"]
        code = gen({"demo_type": "conveyor"})
        assert "/World/Conveyor" in code

    def test_conveyor_has_bins(self):
        gen = CODE_GEN_HANDLERS["create_demo_scene"]
        code = gen({"demo_type": "conveyor"})
        assert "Bin_" in code

    def test_stacking_uses_cubes(self):
        gen = CODE_GEN_HANDLERS["create_demo_scene"]
        code = gen({"demo_type": "stacking"})
        assert "Block_" in code

    def test_inspection_has_turntable(self):
        gen = CODE_GEN_HANDLERS["create_demo_scene"]
        code = gen({"demo_type": "inspection"})
        assert "Turntable" in code

    def test_inspection_has_camera(self):
        gen = CODE_GEN_HANDLERS["create_demo_scene"]
        code = gen({"demo_type": "inspection"})
        assert "InspectionCamera" in code

    def test_custom_robot(self):
        gen = CODE_GEN_HANDLERS["create_demo_scene"]
        code = gen({"demo_type": "pick_and_place", "robot": "ur10"})
        assert "ur10" in code.lower()

    def test_custom_num_objects(self):
        gen = CODE_GEN_HANDLERS["create_demo_scene"]
        code = gen({"demo_type": "pick_and_place", "num_objects": 7})
        assert "range(7)" in code

    def test_no_ground_plane(self):
        gen = CODE_GEN_HANDLERS["create_demo_scene"]
        code = gen({"demo_type": "pick_and_place", "ground_plane": False})
        assert "GroundPlane" not in code

    def test_with_ground_plane(self):
        gen = CODE_GEN_HANDLERS["create_demo_scene"]
        code = gen({"demo_type": "pick_and_place", "ground_plane": True})
        assert "GroundPlane" in code

    def test_physics_disabled(self):
        gen = CODE_GEN_HANDLERS["create_demo_scene"]
        code = gen({"demo_type": "pick_and_place", "physics": False})
        # Should not apply rigid body to objects
        assert "RigidBodyAPI.Apply(obj)" not in code

    def test_physics_enabled(self):
        gen = CODE_GEN_HANDLERS["create_demo_scene"]
        code = gen({"demo_type": "pick_and_place", "physics": True})
        assert "RigidBodyAPI.Apply(obj)" in code

    @pytest.mark.parametrize("lighting", ["default", "studio", "warehouse"])
    def test_lighting_presets(self, lighting):
        gen = CODE_GEN_HANDLERS["create_demo_scene"]
        code = gen({"demo_type": "pick_and_place", "lighting": lighting})
        _assert_valid_python(code, f"create_demo_scene(lighting={lighting})")
        assert "Light" in code

    def test_studio_lighting_has_key_light(self):
        gen = CODE_GEN_HANDLERS["create_demo_scene"]
        code = gen({"demo_type": "pick_and_place", "lighting": "studio"})
        assert "KeyLight" in code

    def test_warehouse_lighting_has_ceiling_lights(self):
        gen = CODE_GEN_HANDLERS["create_demo_scene"]
        code = gen({"demo_type": "pick_and_place", "lighting": "warehouse"})
        assert "CeilingLight" in code

    def test_imports_present(self):
        gen = CODE_GEN_HANDLERS["create_demo_scene"]
        code = gen({"demo_type": "pick_and_place"})
        assert "import omni.usd" in code
        assert "from pxr import" in code


# ---------------------------------------------------------------------------
# Tests — list_demo_types (DATA handler)
# ---------------------------------------------------------------------------

class TestListDemoTypes:
    """Test the list_demo_types data handler."""

    def test_handler_registered(self):
        assert "list_demo_types" in DATA_HANDLERS

    @pytest.mark.asyncio
    async def test_returns_all_demo_types(self):
        handler = DATA_HANDLERS["list_demo_types"]
        result = await handler({})
        assert "demos" in result
        assert result["count"] == 5
        type_names = {d["type"] for d in result["demos"]}
        assert type_names == {"pick_and_place", "navigation", "conveyor", "stacking", "inspection"}

    @pytest.mark.asyncio
    async def test_each_demo_has_required_fields(self):
        handler = DATA_HANDLERS["list_demo_types"]
        result = await handler({})
        for demo in result["demos"]:
            assert "type" in demo
            assert "description" in demo
            assert "default_robot" in demo
            assert len(demo["description"]) > 10

    @pytest.mark.asyncio
    async def test_default_robots_are_valid(self):
        handler = DATA_HANDLERS["list_demo_types"]
        result = await handler({})
        valid_robots = {"franka", "ur10", "nova_carter"}
        for demo in result["demos"]:
            assert demo["default_robot"] in valid_robots


# ---------------------------------------------------------------------------
# Tests — add_demo_objects (CODE_GEN handler)
# ---------------------------------------------------------------------------

class TestAddDemoObjects:
    """Test the add_demo_objects code generator."""

    def test_handler_registered(self):
        assert "add_demo_objects" in CODE_GEN_HANDLERS

    @pytest.mark.parametrize("demo_type", [
        "pick_and_place", "navigation", "conveyor", "stacking", "inspection",
    ])
    def test_all_demo_types_compile(self, demo_type):
        gen = CODE_GEN_HANDLERS["add_demo_objects"]
        code = gen({"demo_type": demo_type})
        _assert_valid_python(code, f"add_demo_objects({demo_type})")

    def test_custom_prim_root(self):
        gen = CODE_GEN_HANDLERS["add_demo_objects"]
        code = gen({"demo_type": "pick_and_place", "prim_root": "/World/MyObjs"})
        assert "/World/MyObjs" in code

    def test_default_prim_root(self):
        gen = CODE_GEN_HANDLERS["add_demo_objects"]
        code = gen({"demo_type": "pick_and_place"})
        assert "/World/Objects" in code

    def test_custom_count(self):
        gen = CODE_GEN_HANDLERS["add_demo_objects"]
        code = gen({"demo_type": "pick_and_place", "count": 10})
        assert "range(10)" in code

    def test_randomize_true(self):
        gen = CODE_GEN_HANDLERS["add_demo_objects"]
        code = gen({"demo_type": "pick_and_place", "randomize": True})
        assert "random.uniform" in code

    def test_randomize_false(self):
        gen = CODE_GEN_HANDLERS["add_demo_objects"]
        code = gen({"demo_type": "pick_and_place", "randomize": False})
        assert "random.uniform" not in code

    def test_navigation_adds_collision(self):
        gen = CODE_GEN_HANDLERS["add_demo_objects"]
        code = gen({"demo_type": "navigation"})
        assert "CollisionAPI.Apply" in code

    def test_pick_and_place_adds_rigid_body(self):
        gen = CODE_GEN_HANDLERS["add_demo_objects"]
        code = gen({"demo_type": "pick_and_place"})
        assert "RigidBodyAPI.Apply" in code
        assert "CollisionAPI.Apply" in code

    def test_creates_parent_xform_if_missing(self):
        gen = CODE_GEN_HANDLERS["add_demo_objects"]
        code = gen({"demo_type": "pick_and_place"})
        assert "if not stage.GetPrimAtPath(prim_root).IsValid():" in code

    def test_imports_present(self):
        gen = CODE_GEN_HANDLERS["add_demo_objects"]
        code = gen({"demo_type": "pick_and_place"})
        assert "import omni.usd" in code
        assert "from pxr import" in code


# ---------------------------------------------------------------------------
# Tests — demo templates consistency
# ---------------------------------------------------------------------------

class TestDemoTemplates:
    """Verify the internal _DEMO_TEMPLATES structure is consistent."""

    def test_all_templates_have_required_keys(self):
        for dtype, tmpl in _DEMO_TEMPLATES.items():
            assert "description" in tmpl, f"{dtype} missing description"
            assert "default_robot" in tmpl, f"{dtype} missing default_robot"
            assert "object_types" in tmpl, f"{dtype} missing object_types"
            assert "table_height" in tmpl, f"{dtype} missing table_height"
            assert len(tmpl["object_types"]) > 0, f"{dtype} has empty object_types"
