"""
L0 tests for DATA_HANDLERS that can run without Kit RPC.
Handlers that need Kit are mocked via the mock_kit_rpc fixture.
"""
import json
import pytest
from unittest.mock import patch

pytestmark = pytest.mark.l0

from service.isaac_assist_service.chat.tools.tool_executor import (
    DATA_HANDLERS,
    _handle_lookup_product_spec,
    _load_sensor_specs,
)

# Conditional imports for addendum handlers (may not exist on all branches)
try:
    from service.isaac_assist_service.chat.tools.tool_executor import _handle_diagnose_physics_error
except ImportError:
    _handle_diagnose_physics_error = None

try:
    from service.isaac_assist_service.chat.tools.tool_executor import _handle_trace_config
except ImportError:
    _handle_trace_config = None

try:
    from service.isaac_assist_service.chat.tools.tool_executor import _handle_diagnose_ros2
except ImportError:
    _handle_diagnose_ros2 = None


class TestLookupProductSpec:
    """Test the sensor spec lookup handler."""

    @pytest.fixture(autouse=True)
    def _reset_cache(self):
        """Reset the cached sensor specs between tests."""
        import service.isaac_assist_service.chat.tools.tool_executor as te
        old = te._sensor_specs
        te._sensor_specs = None
        yield
        te._sensor_specs = old

    @pytest.mark.asyncio
    async def test_no_match_returns_not_found(self, monkeypatch):
        import service.isaac_assist_service.chat.tools.tool_executor as te
        monkeypatch.setattr(te, "_sensor_specs", [])
        result = await _handle_lookup_product_spec({"product_name": "NonExistent9000"})
        assert result["found"] is False

    @pytest.mark.asyncio
    async def test_exact_match(self, monkeypatch):
        import service.isaac_assist_service.chat.tools.tool_executor as te
        fake_specs = [
            {"product": "Intel RealSense D435i", "type": "camera", "fov_h": 87},
        ]
        monkeypatch.setattr(te, "_sensor_specs", fake_specs)
        result = await _handle_lookup_product_spec({"product_name": "Intel RealSense D435i"})
        assert result["found"] is True
        assert result["spec"]["fov_h"] == 87

    @pytest.mark.asyncio
    async def test_case_insensitive_match(self, monkeypatch):
        import service.isaac_assist_service.chat.tools.tool_executor as te
        fake_specs = [
            {"product": "Velodyne VLP-16", "type": "lidar"},
        ]
        monkeypatch.setattr(te, "_sensor_specs", fake_specs)
        result = await _handle_lookup_product_spec({"product_name": "velodyne vlp-16"})
        assert result["found"] is True

    @pytest.mark.asyncio
    async def test_substring_match(self, monkeypatch):
        import service.isaac_assist_service.chat.tools.tool_executor as te
        fake_specs = [
            {"product": "Intel RealSense D435i", "type": "camera"},
            {"product": "Intel RealSense L515", "type": "camera"},
        ]
        monkeypatch.setattr(te, "_sensor_specs", fake_specs)
        result = await _handle_lookup_product_spec({"product_name": "realsense"})
        assert result["found"] is True

    @pytest.mark.asyncio
    async def test_type_based_suggestion(self, monkeypatch):
        import service.isaac_assist_service.chat.tools.tool_executor as te
        fake_specs = [
            {"product": "Velodyne VLP-16", "type": "lidar", "subtype": "3d"},
        ]
        monkeypatch.setattr(te, "_sensor_specs", fake_specs)
        result = await _handle_lookup_product_spec({"product_name": "lidar"})
        assert result["found"] is False
        assert "suggestions" in result


class TestSceneSummary:
    """scene_summary needs Kit RPC, so we use mock_kit_rpc."""

    @pytest.mark.asyncio
    async def test_scene_summary_with_mock_kit(self, mock_kit_rpc):
        handler = DATA_HANDLERS["scene_summary"]
        result = await handler({})
        # When Kit RPC is mocked the summary should include stage info
        # (or at least not crash)
        assert isinstance(result, dict)


class TestGetDebugInfo:
    @pytest.mark.asyncio
    async def test_get_debug_info_with_mock(self, mock_kit_rpc):
        handler = DATA_HANDLERS["get_debug_info"]
        result = await handler({})
        assert isinstance(result, dict)


class TestNoneHandlers:
    """Handlers set to None should be safe to call through execute_tool_call."""

    @pytest.mark.asyncio
    async def test_explain_error_is_none(self):
        assert DATA_HANDLERS.get("explain_error") is None

    @pytest.mark.asyncio
    async def test_execute_none_handler(self, mock_kit_rpc):
        from service.isaac_assist_service.chat.tools.tool_executor import execute_tool_call
        result = await execute_tool_call("explain_error", {"error_text": "some error"})
        assert result["type"] == "data"
        assert "handled by the LLM" in result.get("note", "")


class TestCatalogSearch:
    """catalog_search handler."""

    @pytest.mark.asyncio
    async def test_catalog_search_robots(self, monkeypatch):
        import service.isaac_assist_service.chat.tools.tool_executor as te
        # Reset cached index
        monkeypatch.setattr(te, "_asset_index", None)
        handler = DATA_HANDLERS["catalog_search"]
        result = await handler({"query": "franka", "asset_type": "robot", "limit": 5})
        assert "results" in result
        assert result["total_matches"] > 0
        # "franka" should appear in results
        names = [r["name"] for r in result["results"]]
        assert any("franka" in n.lower() for n in names)

    @pytest.mark.asyncio
    async def test_catalog_search_no_results(self, monkeypatch):
        import service.isaac_assist_service.chat.tools.tool_executor as te
        monkeypatch.setattr(te, "_asset_index", None)
        handler = DATA_HANDLERS["catalog_search"]
        result = await handler({"query": "zzzznonexistent"})
        assert result["total_matches"] == 0


@pytest.mark.skipif("inspect_camera" not in DATA_HANDLERS, reason="Phase 8A not merged")
class TestInspectCamera:
    """inspect_camera DATA handler — sends read-only code to Kit RPC."""

    @pytest.mark.asyncio
    async def test_inspect_camera_queues_code(self, mock_kit_rpc):
        # Add the /exec_patch endpoint to the mock
        mock_kit_rpc["/exec_patch"] = {"queued": True, "patch_id": "test_patch_cam"}
        handler = DATA_HANDLERS["inspect_camera"]
        result = await handler({"camera_path": "/World/Camera"})
        assert isinstance(result, dict)
        assert result.get("queued") is True

    @pytest.mark.asyncio
    async def test_inspect_camera_code_contains_usdgeom(self, mock_kit_rpc):
        """Verify the generated code references UsdGeom.Camera."""
        from service.isaac_assist_service.chat.tools.tool_executor import _gen_inspect_camera
        code = _gen_inspect_camera({"camera_path": "/World/MainCam"})
        assert "UsdGeom.Camera" in code
        assert "/World/MainCam" in code
        assert "focal_length" in code
        assert "json.dumps" in code


@pytest.mark.skipif("cloud_launch" not in DATA_HANDLERS, reason="Phase 7H not merged")
class TestCloudLaunch:
    """cloud_launch data handler."""

    @pytest.mark.asyncio
    async def test_valid_launch(self):
        handler = DATA_HANDLERS["cloud_launch"]
        result = await handler({
            "provider": "aws",
            "instance_type": "g5.2xlarge",
            "isaac_version": "5.1.0",
            "script_template": "training",
            "num_gpus": 1,
        })
        assert "error" not in result
        assert result["provider"] == "aws"
        assert result["instance_type"] == "g5.2xlarge"
        assert result["gpu_model"] == "A10G"
        assert result["estimated_cost_per_hour"] == 1.21
        assert result["always_require_approval"] is True
        assert result["job_id"].startswith("cloud-aws-")
        assert "deploy-aws" in result["deploy_command"]
        assert "training" in result["deploy_command"]
        assert len(result["prerequisites"]) > 0

    @pytest.mark.asyncio
    async def test_invalid_script_template(self):
        handler = DATA_HANDLERS["cloud_launch"]
        result = await handler({
            "provider": "aws",
            "instance_type": "g5.2xlarge",
            "script_template": "malicious_script",
        })
        assert "error" in result
        assert "malicious_script" in result["error"]
        assert "Allowed" in result["error"]

    @pytest.mark.asyncio
    async def test_unknown_instance_type(self):
        handler = DATA_HANDLERS["cloud_launch"]
        result = await handler({
            "provider": "gcp",
            "instance_type": "n1-standard-4",
            "script_template": "sdg",
        })
        assert "error" not in result
        assert result["estimated_cost_per_hour"] is None
        assert result["gpu_model"] == "unknown"

    @pytest.mark.asyncio
    async def test_gcp_provider(self):
        handler = DATA_HANDLERS["cloud_launch"]
        result = await handler({
            "provider": "gcp",
            "instance_type": "g2-standard-8",
            "script_template": "evaluation",
        })
        assert result["provider"] == "gcp"
        assert result["gpu_model"] == "L4"
        assert result["estimated_cost_per_hour"] == 1.35
        assert "deploy-gcp" in result["deploy_command"]


@pytest.mark.skipif("cloud_estimate_cost" not in DATA_HANDLERS, reason="Phase 7H not merged")
class TestCloudEstimateCost:
    """cloud_estimate_cost data handler."""

    @pytest.mark.asyncio
    async def test_known_instance_math(self):
        handler = DATA_HANDLERS["cloud_estimate_cost"]
        result = await handler({
            "provider": "aws",
            "instance_type": "g5.2xlarge",
            "hours": 10.0,
        })
        assert result["price_per_hour"] == 1.21
        assert result["cost_usd"] == 12.10
        assert result["gpu"] == "A10G"

    @pytest.mark.asyncio
    async def test_azure_instance(self):
        handler = DATA_HANDLERS["cloud_estimate_cost"]
        result = await handler({
            "provider": "azure",
            "instance_type": "NCasT4_v3",
            "hours": 5.0,
        })
        assert result["price_per_hour"] == 1.10
        assert result["cost_usd"] == 5.50
        assert result["gpu"] == "T4"

    @pytest.mark.asyncio
    async def test_unknown_instance(self):
        handler = DATA_HANDLERS["cloud_estimate_cost"]
        result = await handler({
            "provider": "aws",
            "instance_type": "p5.48xlarge",
            "hours": 1.0,
        })
        assert result["cost_usd"] is None
        assert result["price_per_hour"] is None
        assert result["gpu"] == "unknown"


@pytest.mark.skipif("cloud_teardown" not in DATA_HANDLERS, reason="Phase 7H not merged")
class TestCloudTeardown:
    """cloud_teardown data handler."""

    @pytest.mark.asyncio
    async def test_teardown_known_job(self):
        import service.isaac_assist_service.chat.tools.tool_executor as te
        te._cloud_jobs["test-cloud-job-001"] = {
            "status": "running",
            "provider": "aws",
            "instance_type": "g5.2xlarge",
            "gpu_model": "A10G",
            "price_per_hour": 1.21,
        }
        try:
            handler = DATA_HANDLERS["cloud_teardown"]
            result = await handler({"job_id": "test-cloud-job-001"})
            assert result["always_require_approval"] is True
            assert result["provider"] == "aws"
            assert "destroy-aws" in result["teardown_command"]
            assert "test-cloud-job-001" in result["teardown_command"]
            assert "$1.21" in result["cost_warning"]
        finally:
            del te._cloud_jobs["test-cloud-job-001"]

    @pytest.mark.asyncio
    async def test_teardown_unknown_job(self):
        handler = DATA_HANDLERS["cloud_teardown"]
        result = await handler({"job_id": "nonexistent-job-999"})
        assert result["always_require_approval"] is True
        assert result["provider"] == "unknown"
        assert "not found" in result["message"]


@pytest.mark.skipif("cloud_status" not in DATA_HANDLERS, reason="Phase 7H not merged")
class TestCloudStatus:
    """cloud_status data handler."""

    @pytest.mark.asyncio
    async def test_status_not_found(self):
        handler = DATA_HANDLERS["cloud_status"]
        result = await handler({"job_id": "nonexistent-cloud-job"})
        assert result["status"] == "not_found"
        assert result["job_id"] == "nonexistent-cloud-job"
        assert result["gpu_utilization"] is None

    @pytest.mark.asyncio
    async def test_status_existing_job(self):
        import service.isaac_assist_service.chat.tools.tool_executor as te
        te._cloud_jobs["test-cloud-status-001"] = {
            "status": "running",
            "gpu_utilization": "85%",
            "estimated_remaining": "2h 15m",
            "cost_so_far": "$4.84",
        }
        try:
            handler = DATA_HANDLERS["cloud_status"]
            result = await handler({"job_id": "test-cloud-status-001"})
            assert result["status"] == "running"
            assert result["gpu_utilization"] == "85%"
            assert result["estimated_remaining"] == "2h 15m"
            assert result["cost_so_far"] == "$4.84"
        finally:
            del te._cloud_jobs["test-cloud-status-001"]


@pytest.mark.skipif("visualize_behavior_tree" not in DATA_HANDLERS, reason="Phase 8C not merged")
class TestVisualizeBehaviorTree:
    """visualize_behavior_tree DATA handler."""

    @pytest.mark.asyncio
    async def test_known_behavior_pick_and_place(self):
        handler = DATA_HANDLERS["visualize_behavior_tree"]
        result = await handler({"network_name": "pick_and_place"})
        assert result["network_name"] == "pick_and_place"
        assert result["structure"] is not None
        assert result["structure"]["type"] == "DfStateMachineDecider"
        assert "approach" in result["tree"]
        assert "grasp" in result["tree"]
        assert "lift" in result["tree"]
        assert "place" in result["tree"]

    @pytest.mark.asyncio
    async def test_known_behavior_follow_target(self):
        handler = DATA_HANDLERS["visualize_behavior_tree"]
        result = await handler({"network_name": "follow_target"})
        assert result["network_name"] == "follow_target"
        assert result["structure"] is not None
        assert result["structure"]["type"] == "DfDecider"
        assert "follow" in result["tree"]

    @pytest.mark.asyncio
    async def test_unknown_behavior(self):
        handler = DATA_HANDLERS["visualize_behavior_tree"]
        result = await handler({"network_name": "custom_something"})
        assert result["network_name"] == "custom_something"
        assert result["structure"] is None
        assert "No pre-built visualization" in result["tree"]
        assert "pick_and_place" in result["tree"]


@pytest.mark.skipif("generate_robot_description" not in DATA_HANDLERS, reason="Phase 8D not merged")
class TestGenerateRobotDescription:
    """generate_robot_description DATA handler."""

    @pytest.mark.asyncio
    async def test_known_robot_franka(self):
        handler = DATA_HANDLERS["generate_robot_description"]
        result = await handler({
            "articulation_path": "/World/Franka",
            "robot_type": "franka",
        })
        assert result["supported"] is True
        assert result["robot_type"] == "franka"
        assert "config_files" in result
        assert result["config_files"]["end_effector_frame"] == "panda_hand"
        assert "rmpflow_config" in result["config_files"]
        assert "robot_descriptor" in result["config_files"]
        assert "urdf" in result["config_files"]
        assert "pre-supported" in result["message"]

    @pytest.mark.asyncio
    async def test_unknown_robot(self):
        handler = DATA_HANDLERS["generate_robot_description"]
        result = await handler({
            "articulation_path": "/World/MyCustomArm",
            "robot_type": "my_custom_arm",
        })
        assert result["supported"] is False
        assert "XRDF Editor" in result["instructions"]
        assert "CollisionSphereEditor" in result["instructions"]
        assert "not pre-supported" in result["message"]

    @pytest.mark.asyncio
    async def test_auto_detect_from_path(self):
        """Should detect robot type from articulation path when not provided."""
        handler = DATA_HANDLERS["generate_robot_description"]
        result = await handler({
            "articulation_path": "/World/ur10_robot",
        })
        assert result["supported"] is True
        assert result["robot_type"] == "ur10"

    @pytest.mark.asyncio
    async def test_empty_robot_type_unknown_path(self):
        """No robot_type and unrecognizable path should return unsupported."""
        handler = DATA_HANDLERS["generate_robot_description"]
        result = await handler({
            "articulation_path": "/World/SomeRandomRobot",
        })
        assert result["supported"] is False


@pytest.mark.skipif("validate_scene_blueprint" not in DATA_HANDLERS, reason="Phase 8A not merged")
class TestValidateSceneBlueprintPhysX:
    """Test PhysX overlap validation in validate_scene_blueprint."""

    @pytest.mark.asyncio
    async def test_physx_collision_detected(self):
        """When Kit RPC reports collisions, issues should be populated."""
        handler = DATA_HANDLERS["validate_scene_blueprint"]

        async def mock_is_alive():
            return True

        async def mock_post(endpoint, data):
            if endpoint == "/check_placement":
                return {"collisions": ["/World/Table"], "clear": False}
            return {}

        with patch("service.isaac_assist_service.chat.tools.tool_executor.kit_tools") as mock_kit:
            mock_kit.is_kit_rpc_alive = mock_is_alive
            mock_kit.post = mock_post

            result = await handler({
                "blueprint": {
                    "objects": [
                        {"name": "Box", "position": [1, 0, 0.5], "prim_type": "Cube", "scale": [1, 1, 1]},
                    ]
                }
            })
            assert any("collides" in issue for issue in result["issues"])
            assert result["valid"] is False

    @pytest.mark.asyncio
    async def test_physx_no_collision(self):
        """When Kit RPC reports clear, no collision issues added."""
        handler = DATA_HANDLERS["validate_scene_blueprint"]

        async def mock_is_alive():
            return True

        async def mock_post(endpoint, data):
            return {"collisions": [], "clear": True}

        with patch("service.isaac_assist_service.chat.tools.tool_executor.kit_tools") as mock_kit:
            mock_kit.is_kit_rpc_alive = mock_is_alive
            mock_kit.post = mock_post

            result = await handler({
                "blueprint": {
                    "objects": [
                        {"name": "Box", "position": [5, 5, 0.5], "prim_type": "Cube", "scale": [1, 1, 1]},
                    ]
                }
            })
            collision_issues = [i for i in result["issues"] if "collides" in i]
            assert len(collision_issues) == 0

    @pytest.mark.asyncio
    async def test_physx_kit_rpc_down_graceful(self):
        """When Kit RPC is not available, PhysX check is skipped gracefully."""
        handler = DATA_HANDLERS["validate_scene_blueprint"]

        async def mock_is_alive():
            return False

        with patch("service.isaac_assist_service.chat.tools.tool_executor.kit_tools") as mock_kit:
            mock_kit.is_kit_rpc_alive = mock_is_alive

            result = await handler({
                "blueprint": {
                    "objects": [
                        {"name": "Box", "position": [0, 0, 0.5], "prim_type": "Cube", "scale": [1, 1, 1]},
                    ]
                }
            })
            # Should still work — just without PhysX validation
            assert "object_count" in result
            assert result["object_count"] == 1


# ── Phase 2 Addendum: Smart Debugging ─────────────────────────────────────

@pytest.mark.skipif(_handle_diagnose_physics_error is None, reason="Phase 2 addendum not merged")
class TestDiagnosePhysicsError:
    """diagnose_physics_error DATA handler — pattern matching against known PhysX errors."""

    @pytest.mark.asyncio
    async def test_negative_mass_detected(self):
        result = await _handle_diagnose_physics_error({
            "error_text": "PhysX error: negative mass detected on prim: /World/Robot/link3"
        })
        assert len(result["matches"]) >= 1
        match = result["matches"][0]
        assert match["category"] == "mass_configuration"
        assert match["severity"] == "critical"
        assert match["prim_path"] == "/World/Robot/link3"
        assert "positive" in match["fix"].lower()

    @pytest.mark.asyncio
    async def test_solver_divergence_no_prim(self):
        result = await _handle_diagnose_physics_error({
            "error_text": "Warning: solver divergence detected in physics step"
        })
        assert len(result["matches"]) >= 1
        match = result["matches"][0]
        assert match["category"] == "solver_divergence"
        assert match["prim_path"] is None
        assert "timestep" in match["fix"].lower()

    @pytest.mark.asyncio
    async def test_multiple_errors_deduplicated(self):
        error_text = (
            "collision mesh invalid on prim: /World/Table\n"
            "collision mesh invalid on prim: /World/Table\n"
            "collision mesh invalid on prim: /World/Table\n"
            "solver divergence detected\n"
        )
        result = await _handle_diagnose_physics_error({"error_text": error_text})
        assert len(result["matches"]) == 2  # deduplicated to 2 categories
        mesh_match = [m for m in result["matches"] if m["category"] == "collision_mesh"][0]
        assert mesh_match["occurrences"] == 3
        assert mesh_match["dedup_hint"] is not None
        assert "3 time" in mesh_match["dedup_hint"]

    @pytest.mark.asyncio
    async def test_no_match_returns_empty(self):
        result = await _handle_diagnose_physics_error({
            "error_text": "This is a generic Python error: list index out of range"
        })
        assert len(result["matches"]) == 0
        assert "No known PhysX error" in result["message"]

    @pytest.mark.asyncio
    async def test_empty_error_text(self):
        result = await _handle_diagnose_physics_error({"error_text": ""})
        assert result["matches"] == []
        assert "No error text" in result["message"]


@pytest.mark.skipif(_handle_trace_config is None, reason="Phase 2 addendum not merged")
class TestTraceConfig:
    """trace_config DATA handler — AST-based parameter tracing."""

    @pytest.mark.asyncio
    async def test_trace_annotated_assignment(self, tmp_path):
        source = tmp_path / "env_cfg.py"
        source.write_text(
            "from dataclasses import dataclass\n"
            "\n"
            "@dataclass\n"
            "class SimCfg:\n"
            "    dt: float = 0.005\n"
            "    gravity: float = -9.81\n",
            encoding="utf-8",
        )
        result = await _handle_trace_config({
            "param_name": "sim.dt",
            "env_source_path": str(source),
        })
        assert result["final_value"] == 0.005
        assert len(result["resolution_chain"]) == 1
        assert result["resolution_chain"][0]["status"] == "active"
        assert result["resolution_chain"][0]["line"] == 5

    @pytest.mark.asyncio
    async def test_trace_param_not_found(self, tmp_path):
        source = tmp_path / "env_cfg.py"
        source.write_text(
            "class Cfg:\n"
            "    something_else: int = 42\n",
            encoding="utf-8",
        )
        result = await _handle_trace_config({
            "param_name": "nonexistent_param",
            "env_source_path": str(source),
        })
        assert result["final_value"] is None
        assert len(result["resolution_chain"]) == 0
        assert "not found" in result["message"]

    @pytest.mark.asyncio
    async def test_trace_missing_file(self):
        result = await _handle_trace_config({
            "param_name": "sim.dt",
            "env_source_path": "/nonexistent/path/env.py",
        })
        assert "error" in result
        assert "not found" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_trace_no_param_name(self):
        result = await _handle_trace_config({"param_name": ""})
        assert "error" in result


# ── Phase 3 Addendum: URDF Post-Processor ───────────────────────────────────

@pytest.mark.skipif("apply_robot_fix_profile" not in DATA_HANDLERS, reason="Phase 3 addendum not merged")
class TestApplyRobotFixProfile:
    """apply_robot_fix_profile DATA handler — lookup table of known robot import issues."""

    @pytest.mark.asyncio
    async def test_known_robot_franka(self):
        handler = DATA_HANDLERS["apply_robot_fix_profile"]
        result = await handler({
            "articulation_path": "/World/Franka",
            "robot_name": "franka",
        })
        assert result["found"] is True
        assert result["robot_name"] == "franka"
        assert result["display_name"] == "Franka Emika Panda"
        assert len(result["fixes"]) > 0
        assert result["drive_gains"]["kp"] == 1000
        assert result["drive_gains"]["kd"] == 100
        assert "/World/Franka" in result["articulation_path"]
        # Fix code should have art_path substituted
        assert any("/World/Franka" in f["code"] for f in result["fixes"])

    @pytest.mark.asyncio
    async def test_unknown_robot(self):
        handler = DATA_HANDLERS["apply_robot_fix_profile"]
        result = await handler({
            "articulation_path": "/World/CustomArm",
            "robot_name": "my_custom_robot",
        })
        assert result["found"] is False
        assert "verify_import" in result["message"]
        assert "my_custom_robot" in result["robot_name"]

    @pytest.mark.asyncio
    async def test_auto_detect_from_path(self):
        """Should detect robot name from articulation path when not provided."""
        handler = DATA_HANDLERS["apply_robot_fix_profile"]
        result = await handler({
            "articulation_path": "/World/ur10_robot",
        })
        assert result["found"] is True
        assert result["robot_name"] == "ur10"
        assert result["display_name"] == "Universal Robots UR10"

    @pytest.mark.asyncio
    async def test_g1_profile(self):
        handler = DATA_HANDLERS["apply_robot_fix_profile"]
        result = await handler({
            "articulation_path": "/World/G1",
            "robot_name": "g1",
        })
        assert result["found"] is True
        assert result["robot_name"] == "g1"
        assert any("zero mass" in issue.lower() or "zero-mass" in issue.lower() or "zero mass" in issue
                    for issue in result["known_issues"])
        assert result["drive_gains"]["kp"] == 500

    @pytest.mark.asyncio
    async def test_allegro_profile(self):
        handler = DATA_HANDLERS["apply_robot_fix_profile"]
        result = await handler({
            "articulation_path": "/World/AllegroHand",
            "robot_name": "allegro",
        })
        assert result["found"] is True
        assert result["robot_name"] == "allegro"
        assert result["drive_gains"]["kp"] == 100


# ── Phase 8F Addendum: ROS2 Quality Diagnostics ─────────────────────────────

class TestDiagnoseRos2:
    """diagnose_ros2 DATA handler — comprehensive ROS2 health check."""

    @pytest.mark.asyncio
    async def test_all_clear(self, mock_kit_rpc):
        """When Kit reports a healthy ROS2 setup, no issues should be raised."""
        import json as _json

        healthy_scene = _json.dumps({
            "ros2_context_found": True,
            "ros2_context_path": "/World/ActionGraph/ROS2Context",
            "distro": "humble",
            "domain_id": "0",
            "domain_id_node": 0,
            "clock_publisher_found": True,
            "use_sim_time": True,
            "og_graphs": ["/World/ActionGraph"],
            "dangling_connections": [],
            "qos_nodes": [],
        })

        async def mock_post(path, body):
            return {"queued": True, "output": healthy_scene}

        import service.isaac_assist_service.chat.tools.kit_tools as kt
        with patch.object(kt, "_post", mock_post):
            handler = DATA_HANDLERS["diagnose_ros2"]
            result = await handler({})

        assert result["issue_count"] == 0
        assert "no issues" in result["message"].lower() or result["issue_count"] == 0
        assert result["ros2_context_found"] is True
        assert result["clock_publishing"] is True

    @pytest.mark.asyncio
    async def test_missing_context(self, mock_kit_rpc):
        """When no ROS2Context node exists, a critical issue should be raised."""
        import json as _json

        no_context_scene = _json.dumps({
            "ros2_context_found": False,
            "ros2_context_path": None,
            "distro": "humble",
            "domain_id": "0",
            "clock_publisher_found": False,
            "use_sim_time": None,
            "og_graphs": ["/World/ActionGraph"],
            "dangling_connections": [],
            "qos_nodes": [],
        })

        async def mock_post(path, body):
            return {"queued": True, "output": no_context_scene}

        import service.isaac_assist_service.chat.tools.kit_tools as kt
        with patch.object(kt, "_post", mock_post):
            handler = DATA_HANDLERS["diagnose_ros2"]
            result = await handler({})

        assert result["issue_count"] >= 1
        assert result["ros2_context_found"] is False
        # Should have "no_ros2_context" issue
        issue_ids = [i["id"] for i in result["issues"]]
        assert "no_ros2_context" in issue_ids
        context_issue = next(i for i in result["issues"] if i["id"] == "no_ros2_context")
        assert context_issue["severity"] == "critical"

    @pytest.mark.asyncio
    async def test_qos_mismatch_detected(self, mock_kit_rpc):
        """When a publisher has wrong QoS for its topic type, a warning should be raised."""
        import json as _json

        qos_mismatch_scene = _json.dumps({
            "ros2_context_found": True,
            "ros2_context_path": "/World/ActionGraph/ROS2Context",
            "distro": "humble",
            "domain_id": "0",
            "clock_publisher_found": True,
            "use_sim_time": True,
            "og_graphs": ["/World/ActionGraph"],
            "dangling_connections": [],
            "qos_nodes": [
                {
                    "node_type": "isaacsim.ros2.bridge.ROS2PublishLaserScan",
                    "node_path": "/World/ActionGraph/PublishScan",
                    "topic": "/scan",
                    "qos": "RELIABLE, VOLATILE",
                },
            ],
        })

        async def mock_post(path, body):
            return {"queued": True, "output": qos_mismatch_scene}

        import service.isaac_assist_service.chat.tools.kit_tools as kt
        with patch.object(kt, "_post", mock_post):
            handler = DATA_HANDLERS["diagnose_ros2"]
            result = await handler({})

        # /scan should be BEST_EFFORT, but we set RELIABLE → should flag it
        qos_issues = [i for i in result["issues"] if i["id"] == "qos_mismatch"]
        assert len(qos_issues) >= 1
        assert "BEST_EFFORT" in qos_issues[0]["message"]
        assert qos_issues[0]["severity"] == "warning"
