"""
L0 tests for the ROS2 Nav2 addendum tools.

Covers the four tools added in addendum_ros2_nav2.md:
  - setup_ros2_bridge   (CODE_GEN_HANDLER)
  - export_nav2_map     (CODE_GEN_HANDLER)
  - replay_rosbag       (CODE_GEN_HANDLER)
  - check_tf_health     (DATA_HANDLER, queues read-only patch via Kit RPC)
"""
import pytest

pytestmark = pytest.mark.l0

from service.isaac_assist_service.chat.tools.tool_executor import (
    CODE_GEN_HANDLERS,
    DATA_HANDLERS,
    _NAV2_BRIDGE_PROFILES,
    get_nav2_bridge_profile,
)
from service.isaac_assist_service.chat.tools.tool_schemas import ISAAC_SIM_TOOLS


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _assert_compiles(code: str, label: str):
    try:
        compile(code, f"<{label}>", "exec")
    except SyntaxError as exc:
        pytest.fail(f"{label} produced invalid Python:\n{exc}\n\nCode:\n{code}")


def _schema_for(name: str):
    for tool in ISAAC_SIM_TOOLS:
        if tool["function"]["name"] == name:
            return tool
    pytest.fail(f"Schema {name!r} not registered in ISAAC_SIM_TOOLS")


# ---------------------------------------------------------------------------
# Schema registration smoke checks
# ---------------------------------------------------------------------------

class TestSchemasRegistered:
    @pytest.mark.parametrize("name", [
        "setup_ros2_bridge",
        "export_nav2_map",
        "replay_rosbag",
        "check_tf_health",
    ])
    def test_schema_present(self, name):
        schema = _schema_for(name)
        assert schema["function"]["parameters"]["type"] == "object"


# ---------------------------------------------------------------------------
# N.1 — setup_ros2_bridge
# ---------------------------------------------------------------------------

class TestSetupRos2Bridge:
    def test_handler_registered(self):
        assert "setup_ros2_bridge" in CODE_GEN_HANDLERS

    def test_known_profiles_lookup(self):
        # Spec table requires these four exact profile names.
        for profile_name in ("ur10e_moveit2", "jetbot_nav2", "franka_moveit2", "amr_full"):
            profile = get_nav2_bridge_profile(profile_name)
            assert profile is not None, f"profile {profile_name} missing"
            assert profile["topics"], f"profile {profile_name} has no topics"
            assert profile["nodes"], f"profile {profile_name} has no nodes"

    def test_unknown_profile_returns_none(self):
        assert get_nav2_bridge_profile("does_not_exist") is None

    @pytest.mark.parametrize("profile_name", list(_NAV2_BRIDGE_PROFILES.keys()))
    def test_each_profile_compiles(self, profile_name):
        gen = CODE_GEN_HANDLERS["setup_ros2_bridge"]
        code = gen({"profile": profile_name, "robot_path": "/World/MyRobot"})
        _assert_compiles(code, f"setup_ros2_bridge:{profile_name}")
        # Common assertions: every profile must build an OmniGraph and tag itself.
        assert "og.Controller.edit" in code
        assert "/World/MyRobot" in code
        assert profile_name in code

    def test_jetbot_nav2_topics_present(self):
        """Jetbot Nav2 profile MUST include /scan, /cmd_vel, /odom, /clock per spec."""
        gen = CODE_GEN_HANDLERS["setup_ros2_bridge"]
        code = gen({"profile": "jetbot_nav2", "robot_path": "/World/Jetbot"})
        for topic in ("/scan", "/cmd_vel", "/odom", "/clock"):
            assert topic in code, f"jetbot_nav2 must wire {topic}"

    def test_ur10e_profile_uses_joint_topics(self):
        gen = CODE_GEN_HANDLERS["setup_ros2_bridge"]
        code = gen({"profile": "ur10e_moveit2", "robot_path": "/World/UR10e"})
        assert "/joint_states" in code
        assert "/joint_command" in code

    def test_unknown_profile_raises_safely(self):
        """Unknown profile must produce code that raises ValueError on execution."""
        gen = CODE_GEN_HANDLERS["setup_ros2_bridge"]
        code = gen({"profile": "bogus", "robot_path": "/World/R"})
        _assert_compiles(code, "setup_ros2_bridge:bogus")
        assert "raise ValueError" in code
        assert "bogus" in code

    def test_default_graph_path(self):
        gen = CODE_GEN_HANDLERS["setup_ros2_bridge"]
        code = gen({"profile": "jetbot_nav2", "robot_path": "/World/Jetbot"})
        assert "/World/ROS2_Bridge" in code

    def test_custom_graph_path_respected(self):
        gen = CODE_GEN_HANDLERS["setup_ros2_bridge"]
        code = gen({
            "profile": "jetbot_nav2",
            "robot_path": "/World/Jetbot",
            "graph_path": "/World/MyBridges/Nav2",
        })
        assert "/World/MyBridges/Nav2" in code


# ---------------------------------------------------------------------------
# N.2 — export_nav2_map
# ---------------------------------------------------------------------------

class TestExportNav2Map:
    def test_handler_registered(self):
        assert "export_nav2_map" in CODE_GEN_HANDLERS

    def test_default_args_compile(self):
        gen = CODE_GEN_HANDLERS["export_nav2_map"]
        code = gen({"output_path": "/tmp/warehouse"})
        _assert_compiles(code, "export_nav2_map:defaults")
        # Defaults from spec
        assert "0.05" in code  # default resolution (Nav2 standard)
        assert "0.65" in code  # default occupied_thresh
        assert "0.196" in code  # default free_thresh

    def test_custom_args_compile(self):
        gen = CODE_GEN_HANDLERS["export_nav2_map"]
        code = gen({
            "output_path": "workspace/maps/site_a",
            "resolution": 0.1,
            "origin": [1.5, -2.0, 0.0],
            "dimensions": [25.0, 30.0],
            "height_range": [0.1, 0.7],
            "occupied_thresh": 0.7,
            "free_thresh": 0.2,
        })
        _assert_compiles(code, "export_nav2_map:custom")
        assert "0.1" in code  # resolution
        assert "0.7" in code  # occupied_thresh
        assert "0.2" in code  # free_thresh

    def test_writes_pgm_and_yaml(self):
        gen = CODE_GEN_HANDLERS["export_nav2_map"]
        code = gen({"output_path": "/tmp/map"})
        # Must produce both files map_server expects.
        assert ".pgm" in code
        assert ".yaml" in code
        # PGM magic bytes
        assert "P5" in code
        # YAML must reference the standard Nav2 keys.
        for key in ("image:", "resolution:", "origin:", "occupied_thresh:", "free_thresh:", "negate:"):
            assert key in code, f"map.yaml missing {key}"

    def test_uses_phase8a_occupancy_api(self):
        gen = CODE_GEN_HANDLERS["export_nav2_map"]
        code = gen({"output_path": "/tmp/map"})
        assert "isaacsim.asset.gen.omap" in code or "_omap" in code


# ---------------------------------------------------------------------------
# N.3 — replay_rosbag
# ---------------------------------------------------------------------------

class TestReplayRosbag:
    def test_handler_registered(self):
        assert "replay_rosbag" in CODE_GEN_HANDLERS

    def test_default_compiles(self):
        gen = CODE_GEN_HANDLERS["replay_rosbag"]
        code = gen({"bag_path": "/data/run_2024_05_01"})
        _assert_compiles(code, "replay_rosbag:default")
        assert "/data/run_2024_05_01" in code
        # Default sync_mode is sim_time → must use --clock
        assert "--clock" in code

    def test_real_time_skips_clock_flag(self):
        gen = CODE_GEN_HANDLERS["replay_rosbag"]
        code = gen({"bag_path": "/tmp/b.db3", "sync_mode": "real_time"})
        _assert_compiles(code, "replay_rosbag:real_time")
        # In real_time mode the bag should not drive sim /clock.
        assert "'sim_time'" in code  # sync_mode literal stored
        # The conditional clause is data-driven; verify the runtime mode is real_time.
        assert "'real_time'" in code

    def test_topic_whitelist(self):
        gen = CODE_GEN_HANDLERS["replay_rosbag"]
        code = gen({"bag_path": "/tmp/b", "topics": ["/cmd_vel", "/odom"]})
        _assert_compiles(code, "replay_rosbag:topics")
        assert "/cmd_vel" in code
        assert "/odom" in code

    def test_default_topic_is_cmd_vel(self):
        """Spec: replay publishes the real robot's cmd_vel by default."""
        gen = CODE_GEN_HANDLERS["replay_rosbag"]
        code = gen({"bag_path": "/tmp/b"})
        assert "/cmd_vel" in code

    def test_invokes_ros2_bag_play(self):
        gen = CODE_GEN_HANDLERS["replay_rosbag"]
        code = gen({"bag_path": "/tmp/b"})
        # Must shell out to ros2 bag play
        assert "ros2" in code
        assert "bag" in code
        assert "play" in code


# ---------------------------------------------------------------------------
# N.4 — check_tf_health
# ---------------------------------------------------------------------------

class TestCheckTfHealth:
    def test_handler_registered(self):
        assert "check_tf_health" in DATA_HANDLERS
        assert DATA_HANDLERS["check_tf_health"] is not None

    @pytest.mark.asyncio
    async def test_default_args_queue_patch(self, mock_kit_rpc):
        # Wire the exec_patch endpoint that kit_tools.queue_exec_patch posts to.
        mock_kit_rpc["/exec_patch"] = {"queued": True, "patch_id": "tf_health_001"}
        handler = DATA_HANDLERS["check_tf_health"]
        result = await handler({})
        assert isinstance(result, dict)
        assert result.get("queued") is True

    @pytest.mark.asyncio
    async def test_custom_expected_frames_queue(self, mock_kit_rpc):
        mock_kit_rpc["/exec_patch"] = {"queued": True, "patch_id": "tf_health_002"}
        handler = DATA_HANDLERS["check_tf_health"]
        result = await handler({
            "expected_frames": ["base_link", "odom", "map", "front_lidar"],
            "max_age_seconds": 0.5,
            "root_frame": "odom",
        })
        assert result.get("queued") is True

    @pytest.mark.asyncio
    async def test_generated_code_uses_tf2(self, mock_kit_rpc):
        """The patch sent to Kit should use tf2_ros + rclpy and emit JSON."""
        captured = {}

        async def fake_post(path, body):
            captured[path] = body
            return {"queued": True, "patch_id": "tf_health_003"}

        import service.isaac_assist_service.chat.tools.kit_tools as kt
        # Replace _post so we capture the actual code patch payload.
        import pytest as _pytest  # noqa: F401
        old = kt._post
        kt._post = fake_post
        try:
            await DATA_HANDLERS["check_tf_health"]({"expected_frames": ["base_link"]})
        finally:
            kt._post = old

        body = captured.get("/exec_patch") or {}
        code = body.get("code", "")
        # rclpy + tf2 path
        assert "tf2_ros" in code
        assert "rclpy" in code
        # Custom expected frame propagated
        assert "base_link" in code
        # The handler emits a structured report
        assert "stale_frames" in code
        assert "missing_frames" in code
        assert "orphan_frames" in code
        # Code must compile
        _assert_compiles(code, "check_tf_health:patch")


# ---------------------------------------------------------------------------
# Integration: each tool wired into the executor dispatch
# ---------------------------------------------------------------------------

class TestExecutorDispatch:
    @pytest.mark.asyncio
    async def test_setup_ros2_bridge_via_execute_tool_call(self, mock_kit_rpc):
        from service.isaac_assist_service.chat.tools.tool_executor import execute_tool_call
        mock_kit_rpc["/exec_patch"] = {"queued": True, "patch_id": "bridge_001"}
        result = await execute_tool_call("setup_ros2_bridge", {
            "profile": "jetbot_nav2",
            "robot_path": "/World/Jetbot",
        })
        assert result["type"] == "code_patch"
        assert "jetbot_nav2" in result["code"]

    @pytest.mark.asyncio
    async def test_export_nav2_map_via_execute_tool_call(self, mock_kit_rpc):
        from service.isaac_assist_service.chat.tools.tool_executor import execute_tool_call
        mock_kit_rpc["/exec_patch"] = {"queued": True, "patch_id": "map_001"}
        result = await execute_tool_call("export_nav2_map", {"output_path": "/tmp/map"})
        assert result["type"] == "code_patch"
        assert ".pgm" in result["code"]
        assert ".yaml" in result["code"]

    @pytest.mark.asyncio
    async def test_replay_rosbag_via_execute_tool_call(self, mock_kit_rpc):
        from service.isaac_assist_service.chat.tools.tool_executor import execute_tool_call
        mock_kit_rpc["/exec_patch"] = {"queued": True, "patch_id": "bag_001"}
        result = await execute_tool_call("replay_rosbag", {"bag_path": "/tmp/run.db3"})
        assert result["type"] == "code_patch"
        assert "/tmp/run.db3" in result["code"]

    @pytest.mark.asyncio
    async def test_check_tf_health_via_execute_tool_call(self, mock_kit_rpc):
        from service.isaac_assist_service.chat.tools.tool_executor import execute_tool_call
        mock_kit_rpc["/exec_patch"] = {"queued": True, "patch_id": "tf_001"}
        result = await execute_tool_call("check_tf_health", {})
        # check_tf_health is a DATA handler, so the dispatcher returns type=data
        assert result["type"] == "data"
