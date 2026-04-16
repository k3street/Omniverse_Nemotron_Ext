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
# Optional imports — present only on branches that ship those handlers.
try:
    from service.isaac_assist_service.chat.tools.tool_executor import (
        _handle_diagnose_physics_error,
    )
except ImportError:
    _handle_diagnose_physics_error = None
try:
    from service.isaac_assist_service.chat.tools.tool_executor import (
        _handle_trace_config,
    )
except ImportError:
    _handle_trace_config = None


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


@pytest.mark.skipif("inspect_camera" not in DATA_HANDLERS, reason="inspect_camera handler not on this branch")
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


@pytest.mark.skipif("cloud_launch" not in DATA_HANDLERS, reason="cloud_launch handler not on this branch")
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


@pytest.mark.skipif("cloud_estimate_cost" not in DATA_HANDLERS, reason="cloud_estimate_cost handler not on this branch")
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


@pytest.mark.skipif("cloud_teardown" not in DATA_HANDLERS, reason="cloud_teardown handler not on this branch")
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


@pytest.mark.skipif("cloud_status" not in DATA_HANDLERS, reason="cloud_status handler not on this branch")
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


@pytest.mark.skipif("visualize_behavior_tree" not in DATA_HANDLERS, reason="visualize_behavior_tree handler not on this branch")
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


@pytest.mark.skipif("generate_robot_description" not in DATA_HANDLERS, reason="generate_robot_description handler not on this branch")
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


@pytest.mark.skipif("validate_scene_blueprint" not in DATA_HANDLERS, reason="validate_scene_blueprint handler not on this branch")
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

@pytest.mark.skipif(_handle_diagnose_physics_error is None, reason="Phase 2 addendum not present on this branch")
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


@pytest.mark.skipif(_handle_trace_config is None, reason="Phase 2 addendum not present on this branch")
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


# ── Safety & Compliance Addendum ─────────────────────────────────────────

from service.isaac_assist_service.chat.tools.tool_executor import (  # noqa: E402
    _handle_validate_iso10218_limits,
    _handle_gdpr_sdg_scan,
    _handle_generate_compliance_report,
)


class TestValidateIso10218Limits:
    """validate_iso10218_limits — motion-envelope check against ISO bounds."""

    @pytest.mark.asyncio
    async def test_compliant_collaborative_motion(self):
        result = await _handle_validate_iso10218_limits({
            "robot_type": "franka",
            "max_joint_velocity_deg_s": 60.0,
            "max_tcp_speed_m_s": 0.2,
            "scenario": "collaborative",
        })
        assert result["verdict"] == "compliant"
        assert all(c["passed"] for c in result["checks"])
        assert result["robot_type"] == "franka"

    @pytest.mark.asyncio
    async def test_tcp_speed_violation_collaborative(self):
        result = await _handle_validate_iso10218_limits({
            "robot_type": "franka",
            "max_joint_velocity_deg_s": 60.0,
            "max_tcp_speed_m_s": 0.5,  # > 0.25 m/s collaborative limit
            "scenario": "collaborative",
        })
        assert result["verdict"] == "violation"
        tcp_checks = [c for c in result["checks"] if "TCP speed" in c["limit"]]
        assert len(tcp_checks) == 1
        assert tcp_checks[0]["passed"] is False
        assert "ISO/TS 15066" in tcp_checks[0]["clause"]

    @pytest.mark.asyncio
    async def test_industrial_scenario_passes_high_speed(self):
        # 120 deg/s + 1.0 m/s passes industrial, would fail collaborative.
        result_industrial = await _handle_validate_iso10218_limits({
            "robot_type": "ur10",
            "max_joint_velocity_deg_s": 120.0,
            "max_tcp_speed_m_s": 1.0,
            "scenario": "industrial",
        })
        assert result_industrial["verdict"] == "compliant"

        result_collab = await _handle_validate_iso10218_limits({
            "robot_type": "ur10",
            "max_joint_velocity_deg_s": 120.0,
            "max_tcp_speed_m_s": 1.0,
            "scenario": "collaborative",
        })
        assert result_collab["verdict"] == "violation"

    @pytest.mark.asyncio
    async def test_unknown_scenario_returns_error(self):
        result = await _handle_validate_iso10218_limits({
            "robot_type": "franka",
            "max_joint_velocity_deg_s": 60.0,
            "max_tcp_speed_m_s": 0.2,
            "scenario": "no_such_scenario",
        })
        assert result["verdict"] == "error"
        assert "Unknown scenario" in result["error"]

    @pytest.mark.asyncio
    async def test_payload_force_check_fires(self):
        # collaborative scenario triggers the payload force check.
        result = await _handle_validate_iso10218_limits({
            "robot_type": "franka",
            "max_joint_velocity_deg_s": 60.0,
            "max_tcp_speed_m_s": 0.2,
            "payload_kg": 5.0,
            "scenario": "collaborative",
        })
        # F = 5 * 0.04 / 0.01 = 20 N — well under the 140 N limit, so passes.
        force_checks = [c for c in result["checks"] if "force" in c["limit"]]
        assert len(force_checks) == 1
        assert force_checks[0]["passed"] is True


class TestDeclareSafetyZoneCodeGen:
    """declare_safety_zone — USD code generation for audited zones."""

    def test_restricted_zone_compiles(self):
        from service.isaac_assist_service.chat.tools.tool_executor import (
            CODE_GEN_HANDLERS,
        )
        gen = CODE_GEN_HANDLERS["declare_safety_zone"]
        code = gen({
            "zone_name": "danger_zone_a",
            "zone_type": "restricted",
            "geometry": {"min": [0, 0, 0], "max": [1, 1, 1]},
            "linked_robot_path": "/World/Franka",
        })
        compile(code, "<test>", "exec")
        assert "SafetyZones/danger_zone_a" in code
        assert "safety:iso13855_classification" in code
        assert "safety:protects" in code
        # Restricted → red (R component close to 0.85)
        assert "0.85" in code

    def test_zone_color_matches_type(self):
        from service.isaac_assist_service.chat.tools.tool_executor import (
            CODE_GEN_HANDLERS,
        )
        gen = CODE_GEN_HANDLERS["declare_safety_zone"]
        # Monitored should produce amber (R ~0.95, G ~0.65)
        code_mon = gen({
            "zone_name": "watch_a",
            "zone_type": "monitored",
            "geometry": {"min": [0, 0, 0], "max": [1, 1, 1]},
        })
        compile(code_mon, "<test>", "exec")
        assert "0.95" in code_mon
        assert "0.65" in code_mon
        # Collaborative → green
        code_collab = gen({
            "zone_name": "shared_a",
            "zone_type": "collaborative",
            "geometry": {"min": [0, 0, 0], "max": [1, 1, 1]},
        })
        compile(code_collab, "<test>", "exec")
        assert "0.7" in code_collab  # green G channel

    def test_zone_name_with_quote_safely_quoted(self):
        from service.isaac_assist_service.chat.tools.tool_executor import (
            CODE_GEN_HANDLERS,
        )
        gen = CODE_GEN_HANDLERS["declare_safety_zone"]
        code = gen({
            "zone_name": "weird'name",
            "zone_type": "monitored",
            "geometry": {"min": [0, 0, 0], "max": [1, 1, 1]},
        })
        # Must still compile despite the quote in the name.
        compile(code, "<test>", "exec")

    def test_unknown_zone_type_raises(self):
        from service.isaac_assist_service.chat.tools.tool_executor import (
            CODE_GEN_HANDLERS,
        )
        gen = CODE_GEN_HANDLERS["declare_safety_zone"]
        with pytest.raises(ValueError):
            gen({
                "zone_name": "x",
                "zone_type": "unknown_type",
                "geometry": {"min": [0, 0, 0], "max": [1, 1, 1]},
            })


class TestGdprSdgScan:
    """gdpr_sdg_scan — DPIA starter record."""

    @pytest.mark.asyncio
    async def test_no_people_no_dpia(self):
        result = await _handle_gdpr_sdg_scan({
            "scene_description": "Empty warehouse with crates",
            "generates_people": False,
            "generates_biometrics": False,
        })
        assert result["dpia_required"] is False
        assert result["risk_class"] == "none"
        assert result["lawful_basis_hint"] is None

    @pytest.mark.asyncio
    async def test_synthetic_people_low_risk(self):
        result = await _handle_gdpr_sdg_scan({
            "scene_description": "Warehouse with synthetic workers",
            "generates_people": True,
            "generates_biometrics": False,
        })
        assert result["dpia_required"] is True
        assert result["risk_class"] == "low"
        assert "Art. 6(1)(f)" in result["lawful_basis_hint"]
        assert any("Pseudonymisation" in c for c in result["minimum_controls"])

    @pytest.mark.asyncio
    async def test_biometric_high_risk(self):
        result = await _handle_gdpr_sdg_scan({
            "scene_description": "Face crops for re-identification benchmark",
            "generates_people": True,
            "generates_biometrics": True,
            "data_recipients": ["internal training", "academic partners"],
        })
        assert result["dpia_required"] is True
        assert result["risk_class"] == "high"
        assert "Art. 9(2)(a)" in result["lawful_basis_hint"]
        # Recipients list is reflected in controls
        assert any("internal training" in c for c in result["minimum_controls"])
        # Explicit consent control is added for biometrics
        assert any("consent" in c.lower() for c in result["minimum_controls"])


class TestGenerateComplianceReport:
    """generate_compliance_report — Markdown artifact written to disk."""

    @pytest.mark.asyncio
    async def test_writes_file(self, tmp_path, monkeypatch):
        # Redirect workspace to tmp_path
        monkeypatch.chdir(tmp_path)
        result = await _handle_generate_compliance_report({
            "scene_name": "demo_cell",
        })
        from pathlib import Path
        report_path = Path(result["report_path"])
        assert report_path.exists()
        assert report_path.name == "demo_cell.md"
        text = report_path.read_text()
        assert "demo_cell" in text
        assert "Sign-off" in text

    @pytest.mark.asyncio
    async def test_includes_requested_standards(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        standards = ["ISO 10218-1", "GDPR Art. 35", "Custom Internal Policy 42"]
        result = await _handle_generate_compliance_report({
            "scene_name": "audit_cell",
            "standards": standards,
        })
        from pathlib import Path
        text = Path(result["report_path"]).read_text()
        for std in standards:
            assert std in text
        # Result mirrors the standards list
        assert result["standards"] == standards
        assert len(result["clauses"]) == len(standards)

    @pytest.mark.asyncio
    async def test_filename_sanitised(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = await _handle_generate_compliance_report({
            "scene_name": "weird/name with spaces & quotes!",
        })
        from pathlib import Path
        report_path = Path(result["report_path"])
        # No raw slashes / spaces / specials in the filename
        assert "/" not in report_path.name
        assert " " not in report_path.name
        assert report_path.exists()
