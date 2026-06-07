import asyncio
import json

import pytest

pytestmark = pytest.mark.l0


def _install_temp_store(monkeypatch, tmp_path):
    from service.isaac_assist_service.multimodal.persistence import MultimodalStore
    from service.isaac_assist_service.multimodal import routes

    store = MultimodalStore(tmp_path / "state.db")
    monkeypatch.setattr(routes, "_store", store)
    return store


def test_create_floor_plan_from_text_and_verify_relations(monkeypatch, tmp_path):
    _install_temp_store(monkeypatch, tmp_path)
    from service.isaac_assist_service.mcp_floorplan_tools import (
        create_floor_plan_from_text,
        verify_scene_relations,
    )

    response = asyncio.run(create_floor_plan_from_text({
        "session_id": "mcp_text_scene",
        "description": "Create a table with a bowl on top of the table and fruit inside the bowl.",
    }))
    verify = verify_scene_relations({"session_id": "mcp_text_scene"})

    assert response["summary"]["objects"] >= 3
    assert response["revision"] == 1
    assert verify["valid"] is True
    assert any(rel["relation"] == "inside" for rel in verify["relations"])
    assert any(rel["relation"] == "on_top_of" for rel in verify["relations"])


def test_create_franka_physics_pick_scene_builds_controller_plan(monkeypatch, tmp_path):
    _install_temp_store(monkeypatch, tmp_path)
    from service.isaac_assist_service.mcp_floorplan_tools import (
        create_franka_physics_pick_scene,
        verify_scene_relations,
    )

    response = asyncio.run(create_franka_physics_pick_scene({
        "session_id": "franka_physics_pick",
        "motion_backend": "curobo",
        "object_count": 2,
        "dry_run": True,
        "build": True,
    }))
    spec = response["spec"]
    controller = response["controller_plan"]
    verify = verify_scene_relations({"session_id": "franka_physics_pick"})

    assert response["created_from"] == "franka_physics_pick_scene"
    assert response["build"]["status"] == "ok"
    assert spec["parameters"]["physics"]["enabled"] is True
    assert spec["parameters"]["controller"]["live_target_source"] == "curobo"
    articulation = spec["parameters"]["controller"]["articulation_controller"]
    ros_graph = spec["parameters"]["controller"]["ros2_control_graph"]
    assert articulation["path"] == "/World/IsaacAssistControllers/FrankaPickPlaceController"
    assert articulation["type"] == "isaacsim.core.nodes.IsaacArticulationController"
    assert ros_graph["path"] == "/World/ROS2ControlGraph"
    assert ros_graph["runtime_profile"] == "isaacsim-6.0"
    assert ros_graph["node_namespace"] == "isaacsim.ros2.nodes"
    assert ros_graph["joint_states_topic"] == "/isaac_joint_states"
    assert ros_graph["joint_commands_topic"] == "/isaac_joint_commands"
    assert any(obj["name"] == "Franka" for obj in spec["objects"])
    assert any(
        obj["metadata"].get("physics") == "dynamic_rigid_body"
        for obj in spec["objects"]
    )
    generated_code = response["build"]["instantiation"]["generated_code"]
    assert "/World/IsaacAssistControllers/FrankaPickPlaceController" in generated_code
    assert "/World/ROS2ControlGraph" in generated_code
    assert "isaacsim.core.nodes.IsaacArticulationController" in generated_code
    assert "isaacsim.ros2.nodes.ROS2PublishJointState" in generated_code
    assert "isaacsim.ros2.nodes.ROS2SubscribeJointState" in generated_code
    assert "isaacsim.ros2.bridge." not in generated_code
    assert controller["live_controller_tool"] == "setup_pick_place_controller"
    assert controller["controller_code_generated"] is False
    assert controller["controller_args"]["source_paths"] == [
        "/World/PickObject_1",
        "/World/PickObject_2",
    ]
    assert "setup_pick_place_controller" in controller["moveit_cumotion_bridge"]["note"]
    assert verify["valid"] is True


def test_create_franka_physics_pick_scene_skips_build_by_default(monkeypatch, tmp_path):
    _install_temp_store(monkeypatch, tmp_path)
    from service.isaac_assist_service.mcp_floorplan_tools import create_franka_physics_pick_scene

    response = asyncio.run(create_franka_physics_pick_scene({
        "session_id": "franka_fast_create",
        "motion_backend": "auto",
        "object_count": 1,
    }))

    assert response["build"]["status"] == "skipped"
    assert response["asset_resolutions"] == []
    assert response["summary"]["objects"] == 5
    assert response["controller_plan"]["live_target_source"] == "auto"


def test_create_franka_physics_pick_scene_records_cumotion_bridge(monkeypatch, tmp_path):
    _install_temp_store(monkeypatch, tmp_path)
    from service.isaac_assist_service.mcp_floorplan_tools import create_franka_physics_pick_scene

    response = asyncio.run(create_franka_physics_pick_scene({
        "session_id": "franka_cumotion_pick",
        "motion_backend": "cumotion",
        "object_count": 1,
        "dry_run": True,
    }))

    controller = response["controller_plan"]
    assert response["spec"]["parameters"]["controller"]["planner_backend"] == "cumotion"
    assert response["spec"]["parameters"]["controller"]["live_target_source"] == "curobo"
    assert controller["moveit_cumotion_bridge"]["planner"] == "cumotion"
    assert controller["moveit_cumotion_bridge"]["dry_run_valid"] is True


def test_create_ros2_scene_harness_writes_project_stack(monkeypatch, tmp_path):
    _install_temp_store(monkeypatch, tmp_path)
    from service.isaac_assist_service import mcp_floorplan_tools

    class FakeCompleted:
        returncode = 0
        stdout = "ros2 help"
        stderr = ""

    monkeypatch.setenv("ROS_DISTRO", "jazzy")
    monkeypatch.setenv("AMENT_PREFIX_PATH", "/opt/ros/jazzy")
    monkeypatch.setenv("ROS_DOMAIN_ID", "7")
    monkeypatch.setattr(mcp_floorplan_tools.shutil, "which", lambda name: "/opt/ros/jazzy/bin/ros2")
    monkeypatch.setattr(mcp_floorplan_tools.subprocess, "run", lambda *args, **kwargs: FakeCompleted())

    response = asyncio.run(mcp_floorplan_tools.create_ros2_scene_harness({
        "project_name": "Warehouse Franka Demo",
        "workspace_root": str(tmp_path / "harnesses"),
        "dry_run": True,
        "build_scene": True,
        "object_count": 2,
    }))

    project_root = tmp_path / "harnesses" / "warehouse_franka_demo_harness"
    package_root = project_root / "src" / "warehouse_franka_demo_harness"
    contract_path = package_root / "config" / "scene_contract.json"

    assert response["status"] == "ready"
    assert response["precheck"]["ok"] is True
    assert response["runtime"]["profile"] == "isaacsim-6.0"
    assert response["ros2"]["ros_distro"] == "jazzy"
    assert response["package_name"] == "warehouse_franka_demo_harness"
    assert response["build"]["instantiation"]["has_generated_code"] is True
    assert (package_root / "package.xml").exists()
    assert (package_root / "setup.py").exists()
    assert (package_root / "launch" / "warehouse_pick_place.launch.py").exists()
    assert (package_root / "warehouse_franka_demo_harness" / "warehouse_pick_place_node.py").exists()
    assert (package_root / "config" / "ros2_control.yaml").exists()
    assert (project_root / "generated" / "scene_setup.py").exists()
    contract = json.loads(contract_path.read_text())
    assert contract["schema_version"] == "isaac_assist.ros2_scene_harness.v1"
    assert contract["controller"]["ros2_control_graph"]["node_namespace"] == "isaacsim.ros2.nodes"
    assert contract["controller"]["source_paths"] == ["/World/PickObject_1", "/World/PickObject_2"]
    assert "ros2 launch warehouse_franka_demo_harness warehouse_pick_place.launch.py" in "\n".join(response["next_commands"])


def test_set_object_asset_updates_reviewed_ref(monkeypatch, tmp_path):
    _install_temp_store(monkeypatch, tmp_path)
    from service.isaac_assist_service.mcp_floorplan_tools import (
        create_floor_plan_from_text,
        set_object_asset,
    )

    created = asyncio.run(create_floor_plan_from_text({
        "session_id": "mcp_asset_scene",
        "description": "Create a microwave on a table.",
    }))
    obj = created["spec"]["objects"][0]

    updated = asyncio.run(set_object_asset({
        "session_id": "mcp_asset_scene",
        "object_id": obj["id"],
        "asset_ref": "/home/kimate/Desktop/assets/example.usd",
        "asset_label": "Example Asset",
    }))

    updated_obj = next(item for item in updated["spec"]["objects"] if item["id"] == obj["id"])
    assert updated_obj["metadata"]["reviewed_asset_ref"] == "/home/kimate/Desktop/assets/example.usd"
    assert updated_obj["metadata"]["reviewed_asset_source"] == "mcp"


def test_search_local_assets_uses_configured_root(monkeypatch, tmp_path):
    from service.isaac_assist_service.multimodal import asset_resolution
    from service.isaac_assist_service.mcp_floorplan_tools import search_local_assets

    asset = tmp_path / "Kitchen/Microwave017.usd"
    asset.parent.mkdir(parents=True)
    asset.write_text("#usda 1.0\n")
    monkeypatch.setenv("ISAAC_ASSIST_ASSET_ROOTS", str(tmp_path))
    asset_resolution._load_asset_catalog.cache_clear()
    asset_resolution._load_local_asset_files.cache_clear()

    response = search_local_assets({"query": "microwave", "limit": 5})

    assert response["count"] == 1
    assert response["options"][0]["usd_ref"] == str(asset)


def test_mcp_server_exposes_floorplan_tools(monkeypatch):
    from service.isaac_assist_service.settings.manager import SettingsManager

    monkeypatch.setattr(SettingsManager, "get_settings", lambda self: {})
    monkeypatch.setattr(SettingsManager, "update_settings", lambda self, settings: True)

    from service.isaac_assist_service.mcp_server import MCPServer

    server = MCPServer()
    names = {tool["name"] for tool in server._mcp_tools}

    assert "create_floor_plan_from_text" in names
    assert "search_local_assets" in names
    assert "verify_scene_relations" in names
    assert "create_franka_physics_pick_scene" in names
    assert "create_ros2_scene_harness" in names


def test_mcp_server_dispatches_floorplan_tool(monkeypatch, tmp_path):
    _install_temp_store(monkeypatch, tmp_path)
    from service.isaac_assist_service.settings.manager import SettingsManager

    monkeypatch.setattr(SettingsManager, "get_settings", lambda self: {})
    monkeypatch.setattr(SettingsManager, "update_settings", lambda self, settings: True)

    from service.isaac_assist_service.mcp_server import MCPServer

    server = MCPServer()
    response = asyncio.run(server.handle_request({
        "jsonrpc": "2.0",
        "id": 77,
        "method": "tools/call",
        "params": {
            "name": "create_floor_plan_from_text",
            "arguments": {
                "session_id": "mcp_rpc_scene",
                "description": "Create a table with a bowl on top of the table.",
            },
        },
    }))

    result = response["result"]
    assert result["isError"] is False
    assert "mcp_rpc_scene" in result["content"][0]["text"]
