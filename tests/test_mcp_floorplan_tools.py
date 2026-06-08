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
    assert ros_graph["fallback_node_namespace"] == "isaacsim.ros2.bridge"
    assert ros_graph["joint_states_topic"] == "/isaac_joint_states"
    assert ros_graph["joint_commands_topic"] == "/isaac_joint_commands"
    assert ros_graph["author_omnigraph"] is False
    assert ros_graph["omnigraph_policy"] == "defer_until_live_probe_passes"
    assert ros_graph["connect_articulation_controller"] is False
    assert ros_graph["connection_policy"] == "safe_bridge_until_live_probe_passes"
    assert any(obj["name"] == "Franka" for obj in spec["objects"])
    assert any(
        obj["metadata"].get("physics") == "dynamic_rigid_body"
        for obj in spec["objects"]
    )
    generated_code = response["build"]["instantiation"]["generated_code"]
    assert "/World/IsaacAssistControllers/FrankaPickPlaceController" in generated_code
    assert "/World/ROS2ControlGraph" in generated_code
    assert "isaacsim.core.nodes.IsaacArticulationController" in generated_code
    assert "isaacsim.ros2.nodes" in generated_code
    assert "isaacsim.ros2.bridge" in generated_code
    assert "ROS2PublishJointState" in generated_code
    assert "ROS2SubscribeJointState" in generated_code
    assert "isaac_assist:author_ros2_omnigraph" in generated_code
    assert "defer_until_live_probe_passes" in generated_code
    assert "isaac_assist:connect_articulation_controller" in generated_code
    assert "deferred_live_probe" in generated_code
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
    assert response["stage_preflight"]["status"] == "dry_run"
    assert response["stage_preflight"]["read_only"] is True
    assert response["stage_preflight"]["stage_checked"] is False
    assert response["stage_preflight"]["target_prim_paths"] == [
        "/World/Franka",
        "/World/PickObject_1",
        "/World/PickObject_2",
        "/World/DropBin",
    ]
    assert response["ros2_omnigraph_probe"]["status"] == "dry_run"
    assert response["ros2_omnigraph_probe"]["recommendation"]["author_omnigraph"] is False
    assert (package_root / "package.xml").exists()
    assert (package_root / "setup.py").exists()
    assert (package_root / "launch" / "warehouse_pick_place.launch.py").exists()
    assert (package_root / "warehouse_franka_demo_harness" / "warehouse_pick_place_node.py").exists()
    assert (package_root / "config" / "ros2_control.yaml").exists()
    assert (project_root / "generated" / "scene_setup.py").exists()
    contract = json.loads(contract_path.read_text())
    assert contract["schema_version"] == "isaac_assist.ros2_scene_harness.v1"
    assert contract["controller"]["ros2_control_graph"]["node_namespace"] == "isaacsim.ros2.nodes"
    assert contract["controller"]["ros2_control_graph"]["fallback_node_namespace"] == "isaacsim.ros2.bridge"
    assert contract["controller"]["ros2_control_graph"]["author_omnigraph"] is False
    assert contract["controller"]["ros2_control_graph"]["connect_articulation_controller"] is False
    assert contract["stage_preflight"]["status"] == "dry_run"
    assert contract["stage_preflight"]["target_prim_paths"] == [
        "/World/Franka",
        "/World/PickObject_1",
        "/World/PickObject_2",
        "/World/DropBin",
    ]
    assert "probe_code" not in contract["stage_preflight"]
    assert contract["ros2_omnigraph_probe"]["status"] == "dry_run"
    assert contract["ros2_omnigraph_probe"]["read_only"] is True
    assert contract["ros2_omnigraph_probe"]["graph_authoring_tested"] is False
    assert contract["ros2_omnigraph_probe"]["recommendation"]["author_omnigraph"] is False
    assert "probe_code" not in contract["ros2_omnigraph_probe"]
    assert contract["controller"]["source_paths"] == ["/World/PickObject_1", "/World/PickObject_2"]
    readme = (project_root / "README.md").read_text()
    assert "Active-stage target preflight: dry_run" in readme
    assert "preflight_isaac_stage_targets" in readme
    assert "/World/PickObject_2" in readme
    assert "ROS2 OmniGraph probe: dry_run" in readme
    assert "probe_ros2_omnigraph_compatibility" in readme
    assert "probe_ros2_omnigraph_creation" in readme
    assert "ros2 launch warehouse_franka_demo_harness warehouse_pick_place.launch.py" in "\n".join(response["next_commands"])


def test_harness_target_prim_paths_are_derived_from_generic_controller():
    from service.isaac_assist_service.mcp_floorplan_tools import _harness_target_prim_paths

    controller = {
        "robot_path": "/World/UR10e",
        "source_paths": ["/World/PartA", "/World/PartB"],
        "destination_path": "/World/Crate",
        "planning_obstacles": ["/World/Table"],
        "articulation_controller": {"robot_path": "/World/UR10e"},
    }

    assert _harness_target_prim_paths(controller) == [
        "/World/UR10e",
        "/World/PartA",
        "/World/PartB",
        "/World/Crate",
        "/World/Table",
    ]


def test_probe_ros2_omnigraph_compatibility_is_read_only_by_default():
    from service.isaac_assist_service.mcp_floorplan_tools import probe_ros2_omnigraph_compatibility

    response = asyncio.run(probe_ros2_omnigraph_compatibility({
        "runtime_profile": "isaacsim-6.0",
        "dry_run": True,
    }))

    assert response["status"] == "dry_run"
    assert response["read_only"] is True
    assert response["graph_authoring_tested"] is False
    assert response["runtime"]["profile"] == "isaacsim-6.0"
    assert response["candidate_namespaces"] == [
        "isaacsim.ros2.nodes",
        "isaacsim.ros2.bridge",
    ]
    assert response["recommendation"]["author_omnigraph"] is False
    assert response["recommendation"]["connect_articulation_controller"] is False
    probe_code = response["probe_code"]
    assert "get_registered_nodes" in probe_code
    assert "ROS2PublishJointState" in probe_code
    assert "ROS2SubscribeJointState" in probe_code
    assert "Controller.edit" not in probe_code
    assert "CreateNode" not in probe_code


def test_probe_ros2_omnigraph_creation_is_context_only_by_default():
    from service.isaac_assist_service.mcp_floorplan_tools import probe_ros2_omnigraph_creation

    response = asyncio.run(probe_ros2_omnigraph_creation({
        "runtime_profile": "isaacsim-6.0",
        "dry_run": True,
    }))

    assert response["status"] == "dry_run"
    assert response["read_only"] is False
    assert response["graph_authoring_tested"] is False
    assert response["probe_mode"] == "context_only"
    assert response["probe_path"] == "/World/IsaacAssistProbes/ROS2ContextCreationProbe"
    assert response["cleanup"] is True
    assert response["touches_scene_assets"] is False
    assert response["touches_robot"] is False
    assert response["touches_topics"] is False
    assert response["recommendation"]["author_omnigraph"] is False
    assert response["recommendation"]["connect_articulation_controller"] is False
    probe_code = response["probe_code"]
    assert "Controller.edit" in probe_code
    assert "ROS2Context" in probe_code
    assert "RemovePrim" in probe_code
    assert "ROS2PublishJointState" not in probe_code
    assert "ROS2SubscribeJointState" not in probe_code
    assert "ArticulationController" not in probe_code
    assert "topicName" not in probe_code
    assert "targetPrim" not in probe_code
    assert "joint_commands" not in probe_code
    assert "/World/Franka" not in probe_code


def test_probe_ros2_omnigraph_creation_pubsub_mode_stays_inert():
    from service.isaac_assist_service.mcp_floorplan_tools import probe_ros2_omnigraph_creation

    response = asyncio.run(probe_ros2_omnigraph_creation({
        "runtime_profile": "isaacsim-6.0",
        "probe_mode": "context_pubsub_no_targets",
        "node_namespace": "isaacsim.ros2.bridge",
        "dry_run": True,
    }))

    assert response["status"] == "dry_run"
    assert response["probe_mode"] == "context_pubsub_no_targets"
    assert response["candidate_namespaces"] == [
        "isaacsim.ros2.bridge",
        "isaacsim.ros2.nodes",
    ]
    assert response["touches_robot"] is False
    assert response["touches_topics"] is False
    assert response["recommendation"]["connect_articulation_controller"] is False
    probe_code = response["probe_code"]
    assert "ROS2Context" in probe_code
    assert "ROS2PublishJointState" in probe_code
    assert "ROS2SubscribeJointState" in probe_code
    assert "ROS2Context.outputs:context" in probe_code
    assert "PublishJointState.inputs:context" in probe_code
    assert "SubscribeJointState.inputs:context" in probe_code
    assert "OnPlaybackTick" not in probe_code
    assert "ArticulationController" not in probe_code
    assert "topicName" not in probe_code
    assert "targetPrim" not in probe_code
    assert "joint_commands" not in probe_code
    assert "/World/Franka" not in probe_code


def test_probe_ros2_omnigraph_creation_dummy_target_mode_assigns_probe_attrs():
    from service.isaac_assist_service.mcp_floorplan_tools import probe_ros2_omnigraph_creation

    response = asyncio.run(probe_ros2_omnigraph_creation({
        "runtime_profile": "isaacsim-6.0",
        "probe_mode": "context_pubsub_dummy_target",
        "node_namespace": "isaacsim.ros2.bridge",
        "dry_run": True,
    }))

    assert response["status"] == "dry_run"
    assert response["probe_mode"] == "context_pubsub_dummy_target"
    assert response["dummy_target_path"] == "/World/IsaacAssistProbes/DummyJointTarget"
    assert response["sets_topic_names"] is True
    assert response["sets_target_prim"] is True
    assert response["uses_dummy_target"] is True
    assert response["touches_robot"] is False
    assert response["touches_scene_assets"] is False
    assert response["touches_topics"] is False
    assert response["recommendation"]["connect_articulation_controller"] is False
    probe_code = response["probe_code"]
    assert "ROS2Context" in probe_code
    assert "ROS2PublishJointState" in probe_code
    assert "ROS2SubscribeJointState" in probe_code
    assert "Controller.attribute" in probe_code
    assert "set_attrs_ok" in probe_code
    assert "PublishJointState.inputs:targetPrim" in probe_code
    assert "PublishJointState.inputs:topicName" in probe_code
    assert "SubscribeJointState.inputs:topicName" in probe_code
    assert "/World/IsaacAssistProbes/DummyJointTarget" in probe_code
    assert "/isaac_assist_probe/joint_states" in probe_code
    assert "/isaac_assist_probe/joint_commands" in probe_code
    assert "OnPlaybackTick" not in probe_code
    assert "ArticulationController" not in probe_code
    assert "/World/Franka" not in probe_code


def test_probe_ros2_omnigraph_creation_tick_mode_requires_stopped_timeline():
    from service.isaac_assist_service.mcp_floorplan_tools import probe_ros2_omnigraph_creation

    response = asyncio.run(probe_ros2_omnigraph_creation({
        "runtime_profile": "isaacsim-6.0",
        "probe_mode": "context_pubsub_dummy_target_tick",
        "node_namespace": "isaacsim.ros2.bridge",
        "dry_run": True,
    }))

    assert response["status"] == "dry_run"
    assert response["probe_mode"] == "context_pubsub_dummy_target_tick"
    assert response["allow_when_playing"] is False
    assert response["requires_timeline_stopped"] is True
    assert response["wires_tick"] is True
    assert response["sets_topic_names"] is True
    assert response["sets_target_prim"] is True
    assert response["touches_robot"] is False
    assert response["touches_scene_assets"] is False
    assert response["touches_topics"] is False
    assert response["recommendation"]["connect_articulation_controller"] is False
    probe_code = response["probe_code"]
    assert "omni.timeline" in probe_code
    assert "timeline_playing" in probe_code
    assert "allow_when_playing" in probe_code
    assert "OnPlaybackTick" in probe_code
    assert "omni.graph.action.OnPlaybackTick" in probe_code
    assert "OnPlaybackTick.outputs:tick" in probe_code
    assert "PublishJointState.inputs:execIn" in probe_code
    assert "SubscribeJointState.inputs:execIn" in probe_code
    assert "PublishJointState.inputs:targetPrim" in probe_code
    assert "PublishJointState.inputs:topicName" in probe_code
    assert "SubscribeJointState.inputs:topicName" in probe_code
    assert "/World/IsaacAssistProbes/DummyJointTarget" in probe_code
    assert "ArticulationController" not in probe_code
    assert "/World/Franka" not in probe_code


def test_probe_ros2_omnigraph_creation_articulation_mode_is_isolated():
    from service.isaac_assist_service.mcp_floorplan_tools import probe_ros2_omnigraph_creation

    response = asyncio.run(probe_ros2_omnigraph_creation({
        "runtime_profile": "isaacsim-6.0",
        "probe_mode": "articulation_dummy_target",
        "dry_run": True,
    }))

    assert response["status"] == "dry_run"
    assert response["probe_mode"] == "articulation_dummy_target"
    assert response["probe_path"] == "/World/IsaacAssistProbes/ArticulationControllerProbe"
    assert response["dummy_target_path"] == "/World/IsaacAssistProbes/DummyJointTarget"
    assert response["creates_articulation_controller"] is True
    assert response["sets_target_prim"] is True
    assert response["sets_topic_names"] is False
    assert response["uses_dummy_target"] is True
    assert response["wires_tick"] is False
    assert response["requires_timeline_stopped"] is False
    assert response["touches_robot"] is False
    assert response["touches_scene_assets"] is False
    assert response["touches_topics"] is False
    assert response["recommendation"]["connect_articulation_controller"] is False
    probe_code = response["probe_code"]
    assert "isaacsim.core.nodes.IsaacArticulationController" in probe_code
    assert "ArticulationController.inputs:targetPrim" in probe_code
    assert "/World/IsaacAssistProbes/DummyJointTarget" in probe_code
    assert "Controller.attribute" in probe_code
    assert "ROS2Context" not in probe_code
    assert "ROS2PublishJointState" not in probe_code
    assert "ROS2SubscribeJointState" not in probe_code
    assert "OnPlaybackTick" not in probe_code
    assert "topicName" not in probe_code
    assert "joint_commands" not in probe_code
    assert "/World/Franka" not in probe_code


def test_probe_ros2_omnigraph_creation_subscribe_articulation_mode_is_disposable():
    from service.isaac_assist_service.mcp_floorplan_tools import probe_ros2_omnigraph_creation

    response = asyncio.run(probe_ros2_omnigraph_creation({
        "runtime_profile": "isaacsim-6.0",
        "probe_mode": "subscribe_articulation_dummy_target_tick",
        "node_namespace": "isaacsim.ros2.bridge",
        "dry_run": True,
    }))

    assert response["status"] == "dry_run"
    assert response["probe_mode"] == "subscribe_articulation_dummy_target_tick"
    assert response["connects_joint_command_outputs"] is True
    assert response["creates_articulation_controller"] is True
    assert response["sets_target_prim"] is True
    assert response["sets_topic_names"] is True
    assert response["uses_dummy_target"] is True
    assert response["wires_tick"] is True
    assert response["requires_timeline_stopped"] is True
    assert response["touches_robot"] is False
    assert response["touches_scene_assets"] is False
    assert response["touches_topics"] is False
    assert response["recommendation"]["connect_articulation_controller"] is False
    probe_code = response["probe_code"]
    assert "ROS2Context" in probe_code
    assert "ROS2SubscribeJointState" in probe_code
    assert "ROS2PublishJointState" not in probe_code
    assert "isaacsim.core.nodes.IsaacArticulationController" in probe_code
    assert "omni.graph.action.OnPlaybackTick" in probe_code
    assert "ROS2Context.outputs:context" in probe_code
    assert "SubscribeJointState.inputs:context" in probe_code
    assert "OnPlaybackTick.outputs:tick" in probe_code
    assert "SubscribeJointState.inputs:execIn" in probe_code
    assert "ArticulationController.inputs:execIn" in probe_code
    assert "SubscribeJointState.outputs:jointNames" in probe_code
    assert "ArticulationController.inputs:jointNames" in probe_code
    assert "SubscribeJointState.outputs:positionCommand" in probe_code
    assert "ArticulationController.inputs:positionCommand" in probe_code
    assert "SubscribeJointState.outputs:velocityCommand" in probe_code
    assert "ArticulationController.inputs:velocityCommand" in probe_code
    assert "SubscribeJointState.outputs:effortCommand" in probe_code
    assert "ArticulationController.inputs:effortCommand" in probe_code
    assert "SubscribeJointState.inputs:topicName" in probe_code
    assert "ArticulationController.inputs:targetPrim" in probe_code
    assert "/isaac_assist_probe/joint_commands" in probe_code
    assert "/World/IsaacAssistProbes/DummyJointTarget" in probe_code
    assert "/World/Franka" not in probe_code


def test_preflight_isaac_stage_targets_is_generic_by_default():
    from service.isaac_assist_service.mcp_floorplan_tools import preflight_isaac_stage_targets

    response = asyncio.run(preflight_isaac_stage_targets({
        "dry_run": True,
    }))

    assert response["status"] == "dry_run"
    assert response["read_only"] is True
    assert response["target_prim_paths"] == []
    assert response["match_terms"] == []
    assert response["stage_checked"] is False
    probe_code = response["probe_code"]
    assert "ISAAC_ASSIST_STAGE_PREFLIGHT" in probe_code
    assert "stage_identifier" in probe_code
    assert "stage_real_path" in probe_code
    assert "target_prims" in probe_code
    assert "matching_prims" in probe_code
    assert "/World/Franka" not in probe_code


def test_preflight_isaac_stage_targets_uses_explicit_target_path():
    from service.isaac_assist_service.mcp_floorplan_tools import preflight_isaac_stage_targets

    response = asyncio.run(preflight_isaac_stage_targets({
        "target_prim_path": "/World/RobotArmA",
        "dry_run": True,
    }))

    assert response["status"] == "dry_run"
    assert response["target_prim_paths"] == ["/World/RobotArmA"]
    assert response["match_terms"] == ["RobotArmA"]
    probe_code = response["probe_code"]
    assert "/World/RobotArmA" in probe_code
    assert "/World/Franka" not in probe_code


def test_probe_ros2_omnigraph_creation_real_target_requires_explicit_target():
    from service.isaac_assist_service.mcp_floorplan_tools import probe_ros2_omnigraph_creation

    with pytest.raises(ValueError, match="target_prim_path is required"):
        asyncio.run(probe_ros2_omnigraph_creation({
            "runtime_profile": "isaacsim-6.0",
            "probe_mode": "subscribe_articulation_real_target_tick",
            "dry_run": True,
        }))


def test_probe_ros2_omnigraph_creation_real_target_mode_requires_stage_target():
    from service.isaac_assist_service.mcp_floorplan_tools import probe_ros2_omnigraph_creation

    response = asyncio.run(probe_ros2_omnigraph_creation({
        "runtime_profile": "isaacsim-6.0",
        "probe_mode": "subscribe_articulation_real_target_tick",
        "node_namespace": "isaacsim.ros2.bridge",
        "target_prim_path": "/World/Franka",
        "dry_run": True,
    }))

    assert response["status"] == "dry_run"
    assert response["probe_mode"] == "subscribe_articulation_real_target_tick"
    assert response["target_prim_path"] == "/World/Franka"
    assert response["requires_existing_target"] is True
    assert response["connects_joint_command_outputs"] is True
    assert response["creates_articulation_controller"] is True
    assert response["sets_target_prim"] is True
    assert response["sets_topic_names"] is True
    assert response["uses_dummy_target"] is False
    assert response["wires_tick"] is True
    assert response["requires_timeline_stopped"] is True
    assert response["touches_robot"] is True
    assert response["touches_scene_assets"] is False
    assert response["touches_topics"] is False
    probe_code = response["probe_code"]
    assert "ROS2Context" in probe_code
    assert "ROS2SubscribeJointState" in probe_code
    assert "ROS2PublishJointState" not in probe_code
    assert "isaacsim.core.nodes.IsaacArticulationController" in probe_code
    assert "omni.graph.action.OnPlaybackTick" in probe_code
    assert "target_prim_exists" in probe_code
    assert "stage_identifier" in probe_code
    assert "stage_real_path" in probe_code
    assert "target_missing" in probe_code
    assert "SubscribeJointState.outputs:jointNames" in probe_code
    assert "ArticulationController.inputs:jointNames" in probe_code
    assert "SubscribeJointState.outputs:positionCommand" in probe_code
    assert "ArticulationController.inputs:positionCommand" in probe_code
    assert "SubscribeJointState.inputs:topicName" in probe_code
    assert "ArticulationController.inputs:targetPrim" in probe_code
    assert "/isaac_assist_probe/joint_commands" in probe_code
    assert "/World/Franka" in probe_code
    assert "/World/IsaacAssistProbes/DummyJointTarget" not in probe_code


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
    assert "preflight_isaac_stage_targets" in names
    assert "probe_ros2_omnigraph_compatibility" in names
    assert "probe_ros2_omnigraph_creation" in names


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
