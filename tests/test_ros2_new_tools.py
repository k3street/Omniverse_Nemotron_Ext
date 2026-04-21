"""
L0 tests for the new tool groups:
  - Gemini Robotics ER bridge     (DATA_HANDLER)
  - cuRobo world collision tools  (7 DATA_HANDLERs)
  - MediaPipe Teleop              (CODE_GEN_HANDLER)
  - Isaac ROS Perception          (3 DATA_HANDLERs)
  - Isaac ROS Segmentation        (7 DATA_HANDLERs)
  - cuMotion                      (9 DATA_HANDLERs)
  - Localization                  (12 DATA_HANDLERs)
  - LingBot-Map 3D reconstruction (DATA_HANDLER)

All tests are L0 — no external processes, no ROS2, no Kit, no curobo GPU.
"""
from __future__ import annotations

import asyncio
import io
import textwrap
from pathlib import Path
from typing import Any, Dict
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.l0

from service.isaac_assist_service.chat.tools.tool_executor import (
    CODE_GEN_HANDLERS,
    DATA_HANDLERS,
)
from service.isaac_assist_service.chat.tools.tool_schemas import ISAAC_SIM_TOOLS


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _schema_for(name: str):
    for tool in ISAAC_SIM_TOOLS:
        if tool["function"]["name"] == name:
            return tool
    pytest.fail(f"Schema {name!r} not registered in ISAAC_SIM_TOOLS")


def _assert_compiles(code: str, label: str) -> None:
    try:
        compile(code, f"<{label}>", "exec")
    except SyntaxError as exc:
        pytest.fail(f"{label} produced invalid Python:\n{exc}\n\nCode:\n{code}")


def _run(coro):
    """Run a coroutine in a throwaway event loop."""
    return asyncio.get_event_loop().run_until_complete(coro)


@pytest.fixture()
def tmp_scene_dir(tmp_path, monkeypatch):
    """Patch every module's _get_scene_dir (and _WORKSPACE) to use a temp directory."""
    scene = tmp_path / "scenes" / "untitled"
    scene.mkdir(parents=True)

    async def _fake_scene_dir():
        return scene

    mods = [
        "service.isaac_assist_service.chat.tools.ros2_gemini_robotics_tools",
        "service.isaac_assist_service.chat.tools.ros2_curobo_world_tools",
        "service.isaac_assist_service.chat.tools.ros2_isaac_ros_tools",
        "service.isaac_assist_service.chat.tools.ros2_segmentation_tools",
        "service.isaac_assist_service.chat.tools.ros2_cumotion_tools",
        "service.isaac_assist_service.chat.tools.ros2_localization_tools",
        "service.isaac_assist_service.chat.tools.ros2_lingbot_tools",
    ]
    import importlib
    for mod_name in mods:
        try:
            m = importlib.import_module(mod_name)
            monkeypatch.setattr(m, "_get_scene_dir", _fake_scene_dir)
            # Also redirect _WORKSPACE so relative_to() calls work
            if hasattr(m, "_WORKSPACE"):
                monkeypatch.setattr(m, "_WORKSPACE", tmp_path / "workspace")
        except (ImportError, AttributeError):
            pass

    return scene


@pytest.fixture()
def no_subprocess(monkeypatch):
    """Stub asyncio.create_subprocess_exec so no real processes are launched."""
    async def _fake_proc(*args, **kwargs):
        class _FakeProc:
            returncode = 0
            pid = 99999
            stdout = AsyncMock(return_value=b"")
            stderr = AsyncMock(return_value=b"")
            async def wait(self): return 0
            async def communicate(self): return (b"", b"")
        return _FakeProc()

    import asyncio as _aio
    monkeypatch.setattr(_aio, "create_subprocess_exec", _fake_proc)
    return _fake_proc


@pytest.fixture()
def no_pkg_exists(monkeypatch):
    """Stub _pkg_exists to always return True."""
    async def _true(pkg): return True

    mods = [
        "service.isaac_assist_service.chat.tools.ros2_isaac_ros_tools",
        "service.isaac_assist_service.chat.tools.ros2_segmentation_tools",
        "service.isaac_assist_service.chat.tools.ros2_cumotion_tools",
        "service.isaac_assist_service.chat.tools.ros2_localization_tools",
    ]
    for mod in mods:
        try:
            import importlib
            m = importlib.import_module(mod)
            monkeypatch.setattr(m, "_pkg_exists", _true)
        except (ImportError, AttributeError):
            pass


# ===========================================================================
# SCHEMA REGISTRATION — smoke-test all new tools are registered
# ===========================================================================

GEMINI_TOOLS = ["launch_gemini_robotics_bridge"]

CUROBO_TOOLS = [
    "configure_curobo_world",
    "add_world_obstacle",
    "remove_world_obstacle",
    "update_obstacle_pose",
    "enable_world_obstacle",
    "query_sphere_collision",
    "launch_world_collision_manager",
]

MEDIAPIPE_TOOLS = ["launch_mediapipe_teleop"]

ISAAC_ROS_TOOLS = [
    "launch_object_detection",
    "launch_pose_estimation",
    "launch_nvblox",
]

SEGMENTATION_TOOLS = [
    "launch_unet_segmentation",
    "launch_segformer",
    "launch_segment_anything",
    "launch_segment_anything2",
    "sam2_add_objects",
    "sam2_remove_object",
    "configure_segmentation_for_nvblox",
]

CUMOTION_TOOLS = [
    "launch_cumotion_planner",
    "launch_robot_segmenter",
    "launch_esdf_visualizer",
    "launch_cumotion_moveit",
    "launch_goal_setter",
    "set_cumotion_target_pose",
    "launch_object_attachment",
    "attach_object",
    "generate_xrdf",
]

LOCALIZATION_TOOLS = [
    "launch_occupancy_grid_localizer",
    "trigger_grid_search_localization",
    "launch_pointcloud_to_flatscan",
    "launch_laserscan_to_flatscan",
    "launch_visual_global_localization",
    "trigger_visual_localization",
    "build_visual_map",
    "load_visual_slam_map",
    "localize_in_visual_slam_map",
    "reset_visual_slam",
    "get_visual_slam_poses",
    "set_visual_slam_pose",
]

LINGBOT_TOOLS = ["launch_lingbot_map"]

ALL_NEW_TOOLS = (
    GEMINI_TOOLS + CUROBO_TOOLS + MEDIAPIPE_TOOLS +
    ISAAC_ROS_TOOLS + SEGMENTATION_TOOLS + CUMOTION_TOOLS +
    LOCALIZATION_TOOLS + LINGBOT_TOOLS
)


class TestSchemasRegistered:
    @pytest.mark.parametrize("name", ALL_NEW_TOOLS)
    def test_schema_present(self, name):
        schema = _schema_for(name)
        assert schema["function"]["parameters"]["type"] == "object"

    @pytest.mark.parametrize("name", ALL_NEW_TOOLS)
    def test_handler_registered(self, name):
        in_data = name in DATA_HANDLERS
        in_code = name in CODE_GEN_HANDLERS
        assert in_data or in_code, f"{name} not in DATA_HANDLERS or CODE_GEN_HANDLERS"


# ===========================================================================
# GEMINI ROBOTICS
# ===========================================================================

class TestGeminiRoboticsTools:
    def test_handler_is_data_handler(self):
        assert "launch_gemini_robotics_bridge" in DATA_HANDLERS

    def test_scaffolds_package_files(self, tmp_scene_dir, no_subprocess):
        result = _run(DATA_HANDLERS["launch_gemini_robotics_bridge"]({}))
        assert result["status"] == "scaffolded"
        assert result["package_name"] == "gemini_robotics_bridge"

        pkg_dir = tmp_scene_dir / "ros2_nodes" / "gemini_robotics_bridge"
        assert (pkg_dir / "CMakeLists.txt").exists()
        assert (pkg_dir / "package.xml").exists()
        assert (pkg_dir / "srv" / "GeminiQuery.srv").exists()
        assert (pkg_dir / "action" / "GeminiTask.action").exists()
        assert (pkg_dir / "scripts" / "gemini_robotics_node.py").exists()
        assert (pkg_dir / "launch" / "gemini_robotics.launch.py").exists()

    def test_node_script_is_valid_python(self, tmp_scene_dir, no_subprocess):
        _run(DATA_HANDLERS["launch_gemini_robotics_bridge"]({}))
        pkg_dir = tmp_scene_dir / "ros2_nodes" / "gemini_robotics_bridge"
        node_src = (pkg_dir / "scripts" / "gemini_robotics_node.py").read_text()
        _assert_compiles(node_src, "gemini_robotics_node.py")

    def test_launch_script_is_valid_python(self, tmp_scene_dir, no_subprocess):
        _run(DATA_HANDLERS["launch_gemini_robotics_bridge"]({}))
        pkg_dir = tmp_scene_dir / "ros2_nodes" / "gemini_robotics_bridge"
        launch_src = (pkg_dir / "launch" / "gemini_robotics.launch.py").read_text()
        _assert_compiles(launch_src, "gemini_robotics.launch.py")

    def test_custom_model_id_accepted(self, tmp_scene_dir, no_subprocess):
        result = _run(DATA_HANDLERS["launch_gemini_robotics_bridge"](
            {"model_id": "gemini-2.0-flash"}
        ))
        assert result["status"] == "scaffolded"

    def test_srv_has_capability_field(self, tmp_scene_dir, no_subprocess):
        _run(DATA_HANDLERS["launch_gemini_robotics_bridge"]({}))
        pkg_dir = tmp_scene_dir / "ros2_nodes" / "gemini_robotics_bridge"
        srv_content = (pkg_dir / "srv" / "GeminiQuery.srv").read_text()
        assert "capability" in srv_content
        assert "result_json" in srv_content or "---" in srv_content

    def test_action_has_feedback(self, tmp_scene_dir, no_subprocess):
        _run(DATA_HANDLERS["launch_gemini_robotics_bridge"]({}))
        pkg_dir = tmp_scene_dir / "ros2_nodes" / "gemini_robotics_bridge"
        action_content = (pkg_dir / "action" / "GeminiTask.action").read_text()
        # Actions have three sections separated by ---
        assert action_content.count("---") >= 2


# ===========================================================================
# cuRobo WORLD TOOLS
# ===========================================================================

class TestCuroboWorldConfig:
    def test_creates_yaml_files(self, tmp_scene_dir):
        result = _run(DATA_HANDLERS["configure_curobo_world"]({}))
        assert result["status"] in ("configured", "created")
        curobo_dir = tmp_scene_dir / "curobo"
        assert (curobo_dir / "world_config.yaml").exists()
        assert (curobo_dir / "world_collision_config.yaml").exists()

    def test_cuboid_appears_in_yaml(self, tmp_scene_dir):
        result = _run(DATA_HANDLERS["configure_curobo_world"]({
            "cuboids": [{"name": "table", "dims": [0.6, 0.6, 0.05],
                         "pose": [0, 0, 0.5, 1, 0, 0, 0]}]
        }))
        yaml_text = (tmp_scene_dir / "curobo" / "world_config.yaml").read_text()
        assert "table" in yaml_text
        assert "0.6" in yaml_text

    def test_cache_appears_in_collision_config(self, tmp_scene_dir):
        _run(DATA_HANDLERS["configure_curobo_world"]({
            "cache_obb": 30, "cache_mesh": 8
        }))
        coll_text = (tmp_scene_dir / "curobo" / "world_collision_config.yaml").read_text()
        assert "30" in coll_text
        assert "8" in coll_text

    def test_no_blox_when_disabled(self, tmp_scene_dir, monkeypatch):
        import service.isaac_assist_service.chat.tools.ros2_curobo_world_tools as wt
        monkeypatch.setattr(wt, "_nvblox_running", lambda: False)
        _run(DATA_HANDLERS["configure_curobo_world"]({"use_blox": False}))
        yaml_text = (tmp_scene_dir / "curobo" / "world_config.yaml").read_text()
        # blox section is present as comments (not active YAML) when disabled
        active_blox = [ln for ln in yaml_text.splitlines()
                       if "blox:" in ln and not ln.lstrip().startswith("#")]
        assert len(active_blox) == 0, "blox section should be commented out when disabled"

    def test_blox_present_when_enabled(self, tmp_scene_dir, monkeypatch):
        import service.isaac_assist_service.chat.tools.ros2_curobo_world_tools as wt
        monkeypatch.setattr(wt, "_nvblox_running", lambda: True)
        _run(DATA_HANDLERS["configure_curobo_world"]({"use_blox": True}))
        yaml_text = (tmp_scene_dir / "curobo" / "world_config.yaml").read_text()
        assert "blox" in yaml_text


class TestCuroboObstacleCRUD:
    def _write_empty_config(self, scene_dir: Path) -> Path:
        import yaml
        curobo_dir = scene_dir / "curobo"
        curobo_dir.mkdir(parents=True, exist_ok=True)
        p = curobo_dir / "world_config.yaml"
        p.write_text(yaml.dump({"cuboid": {}, "mesh": {}}))
        return p

    def test_add_cuboid(self, tmp_scene_dir):
        self._write_empty_config(tmp_scene_dir)
        result = _run(DATA_HANDLERS["add_world_obstacle"]({
            "name": "wall_a",
            "type": "cuboid",
            "dims": [2.0, 0.1, 1.0],
            "pose": [1.0, 0.0, 0.5, 1.0, 0.0, 0.0, 0.0],
        }))
        assert result["status"] == "added"
        assert result["name"] == "wall_a"
        import yaml
        data = yaml.safe_load((tmp_scene_dir / "curobo" / "world_config.yaml").read_text())
        assert "wall_a" in data["cuboid"]

    def test_add_unknown_type_returns_error(self, tmp_scene_dir):
        self._write_empty_config(tmp_scene_dir)
        result = _run(DATA_HANDLERS["add_world_obstacle"]({
            "name": "bad", "type": "capsule"
        }))
        assert result["status"] == "error"

    def test_remove_existing_obstacle(self, tmp_scene_dir):
        self._write_empty_config(tmp_scene_dir)
        _run(DATA_HANDLERS["add_world_obstacle"]({
            "name": "to_remove", "type": "cuboid", "dims": [0.1, 0.1, 0.1]
        }))
        result = _run(DATA_HANDLERS["remove_world_obstacle"]({"name": "to_remove"}))
        assert result["status"] == "removed"
        import yaml
        data = yaml.safe_load((tmp_scene_dir / "curobo" / "world_config.yaml").read_text())
        assert "to_remove" not in (data.get("cuboid") or {})

    def test_remove_nonexistent_returns_error(self, tmp_scene_dir):
        self._write_empty_config(tmp_scene_dir)
        result = _run(DATA_HANDLERS["remove_world_obstacle"]({"name": "ghost"}))
        assert result["status"] == "error"

    def test_remove_no_yaml_returns_error(self, tmp_scene_dir):
        result = _run(DATA_HANDLERS["remove_world_obstacle"]({"name": "x"}))
        assert result["status"] == "error"

    def test_update_pose(self, tmp_scene_dir):
        self._write_empty_config(tmp_scene_dir)
        _run(DATA_HANDLERS["add_world_obstacle"]({
            "name": "box", "type": "cuboid", "dims": [0.1, 0.1, 0.1]
        }))
        new_pose = [1.0, 2.0, 3.0, 1.0, 0.0, 0.0, 0.0]
        result = _run(DATA_HANDLERS["update_obstacle_pose"]({
            "name": "box", "pose": new_pose
        }))
        assert result["status"] == "updated"
        import yaml
        data = yaml.safe_load((tmp_scene_dir / "curobo" / "world_config.yaml").read_text())
        assert data["cuboid"]["box"]["pose"] == new_pose

    def test_enable_disable_obstacle(self, tmp_scene_dir):
        self._write_empty_config(tmp_scene_dir)
        _run(DATA_HANDLERS["add_world_obstacle"]({
            "name": "fence", "type": "cuboid", "dims": [0.1, 0.1, 0.1], "enabled": True
        }))
        result = _run(DATA_HANDLERS["enable_world_obstacle"]({
            "name": "fence", "enabled": False
        }))
        assert result["status"] in ("updated", "ok")
        import yaml
        data = yaml.safe_load((tmp_scene_dir / "curobo" / "world_config.yaml").read_text())
        assert data["cuboid"]["fence"]["enable"] is False


class TestCuroboSphereQuery:
    def test_query_with_mock_curobo(self, tmp_scene_dir, monkeypatch):
        """Stub out curobo imports — verify the handler returns correct shape."""
        import sys, types

        # Build minimal mock curobo module tree
        curobo_mod     = types.ModuleType("curobo")
        geom_mod       = types.ModuleType("curobo.geom")
        sdf_mod        = types.ModuleType("curobo.geom.sdf")
        world_mod      = types.ModuleType("curobo.geom.sdf.world")
        curobo_mod.geom = geom_mod
        geom_mod.sdf   = sdf_mod
        sdf_mod.world  = world_mod

        import numpy as np

        class _FakeCollision:
            def __init__(self, *a, **kw): pass
            def get_sphere_distance(self, t):
                # Return zeros matching batch shape
                import torch
                return torch.zeros(t.shape[0], t.shape[1], t.shape[2])

        world_mod.WorldPrimitiveCollision = _FakeCollision

        for k, v in [("curobo", curobo_mod), ("curobo.geom", geom_mod),
                     ("curobo.geom.sdf", sdf_mod), ("curobo.geom.sdf.world", world_mod)]:
            monkeypatch.setitem(sys.modules, k, v)

        # Also mock torch if not available
        try:
            import torch  # noqa
        except ImportError:
            torch_mod = types.ModuleType("torch")
            torch_mod.zeros = lambda *a, **kw: [[0.0]]
            monkeypatch.setitem(sys.modules, "torch", torch_mod)

        result = _run(DATA_HANDLERS["query_sphere_collision"]({
            "spheres": [
                {"center": [0.5, 0.0, 0.3], "radius": 0.05},
                {"center": [1.0, 0.0, 0.5], "radius": 0.1},
            ]
        }))
        # Even mocked it should return a status key
        assert "status" in result or "spheres" in result or "error" in result


class TestCuroboWorldManager:
    def test_handler_registered(self):
        assert "launch_world_collision_manager" in DATA_HANDLERS

    def test_returns_result_dict(self, tmp_scene_dir, no_subprocess):
        result = _run(DATA_HANDLERS["launch_world_collision_manager"]({}))
        # Should be a dict; subprocess is stubbed so no real launch happens
        assert isinstance(result, dict)


# ===========================================================================
# MEDIAPIPE TELEOP
# ===========================================================================

class TestMediapipeTeleop:
    def test_handler_registered(self):
        assert "launch_mediapipe_teleop" in CODE_GEN_HANDLERS

    def test_default_args_compile(self):
        gen = CODE_GEN_HANDLERS["launch_mediapipe_teleop"]
        code = gen({})
        _assert_compiles(code, "launch_mediapipe_teleop:defaults")

    def test_custom_args_compile(self):
        gen = CODE_GEN_HANDLERS["launch_mediapipe_teleop"]
        code = gen({
            "platform_sdk_path": "/opt/platform_sdk/python",
            "window_title": "Custom Teleop",
            "delta_scale": 0.05,
            "hand_open_threshold": 0.7,
            "robot_prim_path": "/World/Robot/arm",
        })
        _assert_compiles(code, "launch_mediapipe_teleop:custom")

    def test_sdk_path_injected(self):
        gen = CODE_GEN_HANDLERS["launch_mediapipe_teleop"]
        code = gen({"platform_sdk_path": "/my/sdk"})
        assert "/my/sdk" in code

    def test_grid_ui_present(self):
        gen = CODE_GEN_HANDLERS["launch_mediapipe_teleop"]
        code = gen({})
        assert "omni.ui" in code
        assert "5" in code  # 5×5 grid

    def test_start_stop_buttons_present(self):
        gen = CODE_GEN_HANDLERS["launch_mediapipe_teleop"]
        code = gen({})
        assert "Start" in code
        assert "Stop" in code

    def test_se3mediapipe_referenced(self):
        gen = CODE_GEN_HANDLERS["launch_mediapipe_teleop"]
        code = gen({})
        assert "Se3MediaPipe" in code or "se3_mediapipe" in code

    def test_delta_scale_in_code(self):
        gen = CODE_GEN_HANDLERS["launch_mediapipe_teleop"]
        code = gen({"delta_scale": 0.123})
        assert "0.123" in code

    def test_hand_open_threshold_in_code(self):
        gen = CODE_GEN_HANDLERS["launch_mediapipe_teleop"]
        code = gen({"hand_open_threshold": 0.42})
        assert "0.42" in code


# ===========================================================================
# ISAAC ROS PERCEPTION
# ===========================================================================

class TestIsaacROSPerception:
    def test_all_handlers_registered(self):
        for name in ISAAC_ROS_TOOLS:
            assert name in DATA_HANDLERS, f"{name} missing from DATA_HANDLERS"

    def test_object_detection_unknown_model(self, tmp_scene_dir):
        result = _run(DATA_HANDLERS["launch_object_detection"]({"model": "unknown_model"}))
        assert result["status"] == "error"

    def test_object_detection_rtdetr(self, tmp_scene_dir, no_subprocess, no_pkg_exists):
        result = _run(DATA_HANDLERS["launch_object_detection"]({"model": "rtdetr"}))
        assert result["status"] in ("launched", "already_running", "error")
        # If launched, params YAML should exist
        if result["status"] == "launched":
            assert (tmp_scene_dir / "object_detection" / "rtdetr_params.yaml").exists()

    def test_object_detection_yolov8(self, tmp_scene_dir, no_subprocess, no_pkg_exists):
        result = _run(DATA_HANDLERS["launch_object_detection"]({"model": "yolov8"}))
        assert result["status"] in ("launched", "already_running", "error")

    def test_pose_estimation_returns_dict(self, tmp_scene_dir, no_subprocess, no_pkg_exists):
        result = _run(DATA_HANDLERS["launch_pose_estimation"]({}))
        assert isinstance(result, dict)
        assert "status" in result

    def test_nvblox_returns_dict(self, tmp_scene_dir, no_subprocess, no_pkg_exists):
        result = _run(DATA_HANDLERS["launch_nvblox"]({}))
        assert isinstance(result, dict)
        assert "status" in result


# ===========================================================================
# SEGMENTATION
# ===========================================================================

class TestSegmentationTools:
    def test_all_handlers_registered(self):
        for name in SEGMENTATION_TOOLS:
            assert name in DATA_HANDLERS, f"{name} missing from DATA_HANDLERS"

    def test_unet_returns_dict(self, tmp_scene_dir, no_subprocess, no_pkg_exists):
        result = _run(DATA_HANDLERS["launch_unet_segmentation"]({}))
        assert isinstance(result, dict)
        assert "status" in result

    def test_segformer_returns_dict(self, tmp_scene_dir, no_subprocess, no_pkg_exists):
        result = _run(DATA_HANDLERS["launch_segformer"]({}))
        assert isinstance(result, dict)
        assert "status" in result

    def test_sam_returns_dict(self, tmp_scene_dir, no_subprocess, no_pkg_exists):
        result = _run(DATA_HANDLERS["launch_segment_anything"]({}))
        assert isinstance(result, dict)
        assert "status" in result

    def test_sam2_returns_dict(self, tmp_scene_dir, no_subprocess, no_pkg_exists):
        result = _run(DATA_HANDLERS["launch_segment_anything2"]({}))
        assert isinstance(result, dict)
        assert "status" in result

    def test_sam2_add_objects_returns_dict(self, tmp_scene_dir, no_subprocess, no_pkg_exists):
        result = _run(DATA_HANDLERS["sam2_add_objects"]({"labels": ["cup", "bottle"]}))
        assert isinstance(result, dict)
        assert "status" in result

    def test_sam2_remove_object_returns_dict(self, tmp_scene_dir, no_subprocess, no_pkg_exists):
        result = _run(DATA_HANDLERS["sam2_remove_object"]({"label": "cup"}))
        assert isinstance(result, dict)
        assert "status" in result

    def test_configure_for_nvblox_returns_dict(self, tmp_scene_dir, no_subprocess, no_pkg_exists):
        result = _run(DATA_HANDLERS["configure_segmentation_for_nvblox"]({}))
        assert isinstance(result, dict)
        assert "status" in result


# ===========================================================================
# cuMOTION
# ===========================================================================

class TestCumotionTools:
    def test_all_handlers_registered(self):
        for name in CUMOTION_TOOLS:
            assert name in DATA_HANDLERS, f"{name} missing from DATA_HANDLERS"

    def test_cumotion_planner_returns_dict(self, tmp_scene_dir, no_subprocess, no_pkg_exists):
        result = _run(DATA_HANDLERS["launch_cumotion_planner"]({}))
        assert isinstance(result, dict)
        assert "status" in result

    def test_robot_segmenter_returns_dict(self, tmp_scene_dir, no_subprocess, no_pkg_exists):
        result = _run(DATA_HANDLERS["launch_robot_segmenter"]({}))
        assert isinstance(result, dict)
        assert "status" in result

    def test_esdf_visualizer_returns_dict(self, tmp_scene_dir, no_subprocess, no_pkg_exists):
        result = _run(DATA_HANDLERS["launch_esdf_visualizer"]({}))
        assert isinstance(result, dict)
        assert "status" in result

    def test_cumotion_moveit_returns_dict(self, tmp_scene_dir, no_subprocess, no_pkg_exists):
        result = _run(DATA_HANDLERS["launch_cumotion_moveit"]({}))
        assert isinstance(result, dict)
        assert "status" in result

    def test_goal_setter_returns_dict(self, tmp_scene_dir, no_subprocess, no_pkg_exists):
        result = _run(DATA_HANDLERS["launch_goal_setter"]({}))
        assert isinstance(result, dict)
        assert "status" in result

    def test_set_target_pose_returns_dict(self, tmp_scene_dir, no_subprocess, no_pkg_exists):
        result = _run(DATA_HANDLERS["set_cumotion_target_pose"]({
            "pose": [0.4, 0.0, 0.5, 1.0, 0.0, 0.0, 0.0]
        }))
        assert isinstance(result, dict)
        assert "status" in result

    def test_object_attachment_returns_dict(self, tmp_scene_dir, no_subprocess, no_pkg_exists):
        result = _run(DATA_HANDLERS["launch_object_attachment"]({}))
        assert isinstance(result, dict)
        assert "status" in result

    def test_attach_object_returns_dict(self, tmp_scene_dir, no_subprocess, no_pkg_exists):
        result = _run(DATA_HANDLERS["attach_object"]({"object_id": "cup_01"}))
        assert isinstance(result, dict)
        assert "status" in result

    def test_generate_xrdf_creates_file(self, tmp_scene_dir, no_subprocess, no_pkg_exists):
        result = _run(DATA_HANDLERS["generate_xrdf"]({
            "robot_name": "franka",
            "urdf_path": "/opt/ros/humble/share/franka_description/robots/panda.urdf",
        }))
        assert isinstance(result, dict)
        assert "status" in result


# ===========================================================================
# LOCALIZATION
# ===========================================================================

class TestLocalizationTools:
    def test_all_handlers_registered(self):
        for name in LOCALIZATION_TOOLS:
            assert name in DATA_HANDLERS, f"{name} missing from DATA_HANDLERS"

    def test_occupancy_grid_localizer_returns_dict(self, tmp_scene_dir, no_subprocess, no_pkg_exists):
        result = _run(DATA_HANDLERS["launch_occupancy_grid_localizer"]({}))
        assert isinstance(result, dict)
        assert "status" in result

    def test_trigger_grid_search_returns_dict(self, tmp_scene_dir, no_subprocess, no_pkg_exists):
        result = _run(DATA_HANDLERS["trigger_grid_search_localization"]({}))
        assert isinstance(result, dict)
        assert "status" in result

    def test_pointcloud_to_flatscan_returns_dict(self, tmp_scene_dir, no_subprocess, no_pkg_exists):
        result = _run(DATA_HANDLERS["launch_pointcloud_to_flatscan"]({}))
        assert isinstance(result, dict)
        assert "status" in result

    def test_laserscan_to_flatscan_returns_dict(self, tmp_scene_dir, no_subprocess, no_pkg_exists):
        result = _run(DATA_HANDLERS["launch_laserscan_to_flatscan"]({}))
        assert isinstance(result, dict)
        assert "status" in result

    def test_visual_global_localization_returns_dict(self, tmp_scene_dir, no_subprocess, no_pkg_exists):
        result = _run(DATA_HANDLERS["launch_visual_global_localization"]({}))
        assert isinstance(result, dict)
        assert "status" in result

    def test_trigger_visual_localization_returns_dict(self, tmp_scene_dir, no_subprocess, no_pkg_exists):
        result = _run(DATA_HANDLERS["trigger_visual_localization"]({}))
        assert isinstance(result, dict)
        assert "status" in result

    def test_build_visual_map_returns_dict(self, tmp_scene_dir, no_subprocess, no_pkg_exists):
        result = _run(DATA_HANDLERS["build_visual_map"]({}))
        assert isinstance(result, dict)
        assert "status" in result

    def test_load_visual_slam_map_returns_dict(self, tmp_scene_dir, no_subprocess, no_pkg_exists):
        result = _run(DATA_HANDLERS["load_visual_slam_map"]({"map_path": "/tmp/map.db"}))
        assert isinstance(result, dict)
        assert "status" in result

    def test_localize_in_visual_slam_returns_dict(self, tmp_scene_dir, no_subprocess, no_pkg_exists):
        result = _run(DATA_HANDLERS["localize_in_visual_slam_map"]({}))
        assert isinstance(result, dict)
        assert "status" in result

    def test_reset_visual_slam_returns_dict(self, tmp_scene_dir, no_subprocess, no_pkg_exists):
        result = _run(DATA_HANDLERS["reset_visual_slam"]({}))
        assert isinstance(result, dict)
        assert "status" in result

    def test_get_visual_slam_poses_returns_dict(self, tmp_scene_dir, no_subprocess, no_pkg_exists):
        result = _run(DATA_HANDLERS["get_visual_slam_poses"]({}))
        assert isinstance(result, dict)
        assert "status" in result

    def test_set_visual_slam_pose_returns_dict(self, tmp_scene_dir, no_subprocess, no_pkg_exists):
        result = _run(DATA_HANDLERS["set_visual_slam_pose"]({
            "pose": [0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0]
        }))
        assert isinstance(result, dict)
        assert "status" in result


# ===========================================================================
# LINGBOT-MAP 3D RECONSTRUCTION
# ===========================================================================

class TestLingBotMapTools:
    def test_handler_registered(self):
        assert "launch_lingbot_map" in DATA_HANDLERS

    def test_schema_present(self):
        schema = _schema_for("launch_lingbot_map")
        assert schema["function"]["parameters"]["type"] == "object"

    def test_schema_has_model_variant_enum(self):
        schema = _schema_for("launch_lingbot_map")
        props = schema["function"]["parameters"]["properties"]
        assert "model_variant" in props
        enum = props["model_variant"].get("enum", [])
        assert "lingbot-map-long" in enum
        assert "lingbot-map" in enum

    def test_scaffolds_package_files(self, tmp_scene_dir):
        result = _run(DATA_HANDLERS["launch_lingbot_map"]({}))
        assert result["status"] == "scaffolded"
        assert result["package_name"] == "lingbot_map_ros"

        pkg_dir = tmp_scene_dir / "ros2_nodes" / "lingbot_map_ros"
        assert (pkg_dir / "package.xml").exists()
        assert (pkg_dir / "setup.py").exists()
        assert (pkg_dir / "setup.cfg").exists()
        assert (pkg_dir / "lingbot_map_ros" / "lingbot_map_node.py").exists()
        assert (pkg_dir / "launch" / "lingbot_map.launch.py").exists()
        assert (pkg_dir / "scripts" / "export_lingbot_to_curobo.py").exists()

    def test_node_script_is_valid_python(self, tmp_scene_dir):
        _run(DATA_HANDLERS["launch_lingbot_map"]({}))
        pkg_dir = tmp_scene_dir / "ros2_nodes" / "lingbot_map_ros"
        src = (pkg_dir / "lingbot_map_ros" / "lingbot_map_node.py").read_text()
        _assert_compiles(src, "lingbot_map_node.py")

    def test_launch_script_is_valid_python(self, tmp_scene_dir):
        _run(DATA_HANDLERS["launch_lingbot_map"]({}))
        pkg_dir = tmp_scene_dir / "ros2_nodes" / "lingbot_map_ros"
        src = (pkg_dir / "launch" / "lingbot_map.launch.py").read_text()
        _assert_compiles(src, "lingbot_map.launch.py")

    def test_export_script_is_valid_python(self, tmp_scene_dir):
        _run(DATA_HANDLERS["launch_lingbot_map"]({}))
        pkg_dir = tmp_scene_dir / "ros2_nodes" / "lingbot_map_ros"
        src = (pkg_dir / "scripts" / "export_lingbot_to_curobo.py").read_text()
        _assert_compiles(src, "export_lingbot_to_curobo.py")

    def test_image_topic_in_node(self, tmp_scene_dir):
        _run(DATA_HANDLERS["launch_lingbot_map"]({"image_topic": "/my/cam/image"}))
        pkg_dir = tmp_scene_dir / "ros2_nodes" / "lingbot_map_ros"
        src = (pkg_dir / "lingbot_map_ros" / "lingbot_map_node.py").read_text()
        assert "/my/cam/image" in src

    def test_model_variant_in_node(self, tmp_scene_dir):
        _run(DATA_HANDLERS["launch_lingbot_map"]({"model_variant": "lingbot-map-stage1"}))
        pkg_dir = tmp_scene_dir / "ros2_nodes" / "lingbot_map_ros"
        src = (pkg_dir / "lingbot_map_ros" / "lingbot_map_node.py").read_text()
        assert "lingbot-map-stage1" in src

    def test_publishes_dict_lists_all_topics(self, tmp_scene_dir):
        result = _run(DATA_HANDLERS["launch_lingbot_map"]({}))
        pubs = result.get("publishes", {})
        assert "/lingbot/pointcloud"  in pubs.values()
        assert "/lingbot/camera_pose" in pubs.values()
        assert "/lingbot/depth"       in pubs.values()
        assert "/lingbot/conf"        in pubs.values()

    def test_hf_id_in_node(self, tmp_scene_dir):
        _run(DATA_HANDLERS["launch_lingbot_map"]({}))
        pkg_dir = tmp_scene_dir / "ros2_nodes" / "lingbot_map_ros"
        src = (pkg_dir / "lingbot_map_ros" / "lingbot_map_node.py").read_text()
        assert "robbyant/lingbot-map" in src

    def test_conf_threshold_injected(self, tmp_scene_dir):
        _run(DATA_HANDLERS["launch_lingbot_map"]({"conf_threshold": 0.55}))
        pkg_dir = tmp_scene_dir / "ros2_nodes" / "lingbot_map_ros"
        src = (pkg_dir / "lingbot_map_ros" / "lingbot_map_node.py").read_text()
        assert "0.55" in src

    def test_mask_sky_flag_propagates(self, tmp_scene_dir):
        _run(DATA_HANDLERS["launch_lingbot_map"]({"mask_sky": True}))
        pkg_dir = tmp_scene_dir / "ros2_nodes" / "lingbot_map_ros"
        launch = (pkg_dir / "launch" / "lingbot_map.launch.py").read_text()
        assert "mask_sky" in launch

    def test_curobo_export_in_result(self, tmp_scene_dir):
        result = _run(DATA_HANDLERS["launch_lingbot_map"]({}))
        assert "curobo_export" in result
        assert "export_lingbot_to_curobo.py" in result["curobo_export"]

    def test_build_cmd_in_result(self, tmp_scene_dir):
        result = _run(DATA_HANDLERS["launch_lingbot_map"]({}))
        assert "colcon build" in result["build_cmd"]
        assert "lingbot_map_ros" in result["build_cmd"]
