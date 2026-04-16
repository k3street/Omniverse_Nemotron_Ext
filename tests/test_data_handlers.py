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


# ── Community & Remote Addendum ──────────────────────────────────────────

from service.isaac_assist_service.chat.tools.tool_executor import (  # noqa: E402
    _handle_share_scene_to_community,
    _handle_search_community_scenes,
    _handle_remote_session_invite,
    _handle_connect_remote_kit,
)


def _redirect_community_paths(monkeypatch, tmp_path):
    """Point the addendum module-level paths at tmp_path."""
    import service.isaac_assist_service.chat.tools.tool_executor as te
    from pathlib import Path as _P
    base = _P(tmp_path) / "community"
    monkeypatch.setattr(te, "_COMMUNITY_DIR", base)
    monkeypatch.setattr(te, "_COMMUNITY_REGISTRY", base / "registry.jsonl")
    monkeypatch.setattr(te, "_COMMUNITY_INVITES", base / "invites.jsonl")
    monkeypatch.setattr(te, "_COMMUNITY_REMOTE_KITS", base / "remote_kits.json")
    monkeypatch.setattr(te, "_COMMUNITY_SKILLS_DIR", base / "skills")
    return base


class TestShareSceneToCommunity:
    """share_scene_to_community — manifest + registry write."""

    @pytest.mark.asyncio
    async def test_writes_manifest_and_registry(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        base = _redirect_community_paths(monkeypatch, tmp_path)
        # Create a fake export dir with a couple of files.
        export_dir = tmp_path / "workspace" / "scene_exports" / "demo_pickplace"
        export_dir.mkdir(parents=True)
        (export_dir / "scene_setup.py").write_text("print('hello')\n")
        (export_dir / "README.md").write_text("# Demo\n")

        result = await _handle_share_scene_to_community({
            "scene_name": "demo_pickplace",
            "author": "anton@example.com",
            "description": "Franka pick-and-place tutorial",
            "tags": ["franka", "pick-and-place"],
            "license": "MIT",
        })
        assert result["shared"] is True
        from pathlib import Path
        manifest_path = Path(result["manifest_path"])
        assert manifest_path.exists()
        manifest = json.loads(manifest_path.read_text())
        assert manifest["name"] == "demo_pickplace"
        assert manifest["author"] == "anton@example.com"
        assert manifest["license"] == "MIT"
        assert "scene_setup.py" in manifest["files"]
        assert "README.md" in manifest["files"]
        assert "scene_setup.py" in manifest["checksums"]
        # Registry row appended
        registry_path = Path(result["registry_path"])
        assert registry_path.exists()
        rows = [json.loads(line) for line in registry_path.read_text().splitlines() if line]
        assert len(rows) == 1
        assert rows[0]["name"] == "demo_pickplace"

    @pytest.mark.asyncio
    async def test_missing_export_dir_returns_error(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _redirect_community_paths(monkeypatch, tmp_path)
        result = await _handle_share_scene_to_community({
            "scene_name": "never_exported",
            "author": "x",
            "description": "y",
        })
        assert result.get("shared") is False
        assert "error" in result
        assert "not found" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_checksum_stable_for_known_content(self, tmp_path, monkeypatch):
        import hashlib
        monkeypatch.chdir(tmp_path)
        _redirect_community_paths(monkeypatch, tmp_path)
        export_dir = tmp_path / "workspace" / "scene_exports" / "stable"
        export_dir.mkdir(parents=True)
        content = b"deterministic content\n"
        (export_dir / "file.txt").write_bytes(content)
        expected = hashlib.sha256(content).hexdigest()

        result = await _handle_share_scene_to_community({
            "scene_name": "stable",
            "author": "x",
            "description": "y",
        })
        assert result["checksums"]["file.txt"] == expected

    @pytest.mark.asyncio
    async def test_unknown_license_rejected(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _redirect_community_paths(monkeypatch, tmp_path)
        result = await _handle_share_scene_to_community({
            "scene_name": "x",
            "author": "y",
            "description": "z",
            "license": "GPL-2.0",
        })
        assert result["shared"] is False
        assert "license" in result["error"].lower()


class TestSearchCommunityScenes:
    """search_community_scenes — registry lookup."""

    @pytest.mark.asyncio
    async def test_empty_registry_no_crash(self, tmp_path, monkeypatch):
        _redirect_community_paths(monkeypatch, tmp_path)
        result = await _handle_search_community_scenes({"query": "anything"})
        assert result["count"] == 0
        assert result["total_indexed"] == 0
        assert result["results"] == []

    @pytest.mark.asyncio
    async def test_tag_filter(self, tmp_path, monkeypatch):
        base = _redirect_community_paths(monkeypatch, tmp_path)
        base.mkdir(parents=True, exist_ok=True)
        (base / "registry.jsonl").write_text(
            json.dumps({"name": "a", "tags": ["franka"], "license": "MIT", "description": ""}) + "\n"
            + json.dumps({"name": "b", "tags": ["ur10"], "license": "MIT", "description": ""}) + "\n"
        )
        result = await _handle_search_community_scenes({"tag": "franka"})
        assert result["count"] == 1
        assert result["results"][0]["name"] == "a"

    @pytest.mark.asyncio
    async def test_license_filter(self, tmp_path, monkeypatch):
        base = _redirect_community_paths(monkeypatch, tmp_path)
        base.mkdir(parents=True, exist_ok=True)
        (base / "registry.jsonl").write_text(
            json.dumps({"name": "permissive", "tags": [], "license": "MIT", "description": ""}) + "\n"
            + json.dumps({"name": "closed", "tags": [], "license": "proprietary", "description": ""}) + "\n"
        )
        result = await _handle_search_community_scenes({"license": "MIT"})
        assert result["count"] == 1
        assert result["results"][0]["name"] == "permissive"

    @pytest.mark.asyncio
    async def test_score_ordering_tag_outranks_description(self, tmp_path, monkeypatch):
        base = _redirect_community_paths(monkeypatch, tmp_path)
        base.mkdir(parents=True, exist_ok=True)
        # Both rows match the query "franka", but in different ways:
        #   tag_match     — has "franka" in tags only        (tag score 3)
        #   name_match    — has "franka" in name only         (name score 2)
        (base / "registry.jsonl").write_text(
            json.dumps({
                "name": "name_match_franka", "tags": ["other"],
                "description": "unrelated", "license": "MIT",
            }) + "\n"
            + json.dumps({
                "name": "other", "tags": ["franka"],
                "description": "unrelated", "license": "MIT",
            }) + "\n"
        )
        # With both tag and query filters set, the tag-matching row wins
        # because it satisfies the (hard) tag filter and scores 3.
        result = await _handle_search_community_scenes({
            "tag": "franka", "query": "franka",
        })
        assert result["count"] == 1
        assert result["results"][0]["name"] == "other"
        # Without the tag filter both rows surface; the name-match wins
        # (score 2 from query-in-name beats the 0 of the other row whose
        # description and name both lack "franka").
        result = await _handle_search_community_scenes({"query": "franka"})
        assert result["count"] == 2
        assert result["results"][0]["name"] == "name_match_franka"

    @pytest.mark.asyncio
    async def test_unknown_license_filter_returns_error(self, tmp_path, monkeypatch):
        _redirect_community_paths(monkeypatch, tmp_path)
        result = await _handle_search_community_scenes({"license": "GPL-3.0"})
        assert "error" in result
        assert result["count"] == 0


class TestRemoteSessionInvite:
    """remote_session_invite — token + URLs + persistence."""

    @pytest.mark.asyncio
    async def test_generates_urls_and_persists(self, tmp_path, monkeypatch):
        base = _redirect_community_paths(monkeypatch, tmp_path)
        result = await _handle_remote_session_invite({
            "session_name": "pair-debug",
            "expires_in_minutes": 30,
            "allow_write": False,
        })
        assert len(result["token"]) == 32  # 16 bytes hex
        assert result["deep_link"].startswith("isaac-assist://join/")
        assert result["https_url"].startswith("https://")
        assert "write=0" in result["deep_link"]
        assert result["expires_in_minutes"] == 30
        from pathlib import Path
        invites_path = Path(result["invites_path"])
        assert invites_path.exists()
        rows = [json.loads(l) for l in invites_path.read_text().splitlines() if l]
        assert len(rows) == 1
        assert rows[0]["session_name"] == "pair-debug"

    @pytest.mark.asyncio
    async def test_clamps_expiry(self, tmp_path, monkeypatch):
        _redirect_community_paths(monkeypatch, tmp_path)
        result = await _handle_remote_session_invite({
            "session_name": "long-session",
            "expires_in_minutes": 99999,
        })
        assert result["expires_in_minutes"] == 1440  # max
        result2 = await _handle_remote_session_invite({
            "session_name": "tiny",
            "expires_in_minutes": -5,
        })
        assert result2["expires_in_minutes"] == 1  # min

    @pytest.mark.asyncio
    async def test_write_flag_toggles_url(self, tmp_path, monkeypatch):
        _redirect_community_paths(monkeypatch, tmp_path)
        result_w = await _handle_remote_session_invite({
            "session_name": "co-edit",
            "allow_write": True,
        })
        assert "write=1" in result_w["deep_link"]
        assert result_w["allow_write"] is True


class TestConnectRemoteKit:
    """connect_remote_kit — profile persistence + token redaction."""

    @pytest.mark.asyncio
    async def test_valid_profile_written(self, tmp_path, monkeypatch):
        base = _redirect_community_paths(monkeypatch, tmp_path)
        result = await _handle_connect_remote_kit({
            "host": "lab-gpu.example.com",
            "port": 8001,
            "auth_token": "supersecret-token-1234",
            "name": "lab",
        })
        assert result["saved"] is True
        from pathlib import Path
        kits_path = Path(result["remote_kits_path"])
        data = json.loads(kits_path.read_text())
        assert len(data["profiles"]) == 1
        prof = data["profiles"][0]
        assert prof["name"] == "lab"
        assert prof["host"] == "lab-gpu.example.com"
        assert prof["port"] == 8001
        assert prof["url"] == "http://lab-gpu.example.com:8001"
        assert prof["ws_url"].startswith("ws://")
        # Token must be redacted on disk.
        assert "supersecret-token-1234" not in kits_path.read_text()
        assert prof["auth_token_preview"].startswith("supe")
        assert prof["auth_token_preview"].endswith("1234")

    @pytest.mark.asyncio
    async def test_invalid_port_rejected(self, tmp_path, monkeypatch):
        _redirect_community_paths(monkeypatch, tmp_path)
        result = await _handle_connect_remote_kit({
            "host": "ok",
            "port": 99999,
            "auth_token": "longenough-token",
        })
        assert result["saved"] is False
        assert "port" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_short_token_rejected(self, tmp_path, monkeypatch):
        _redirect_community_paths(monkeypatch, tmp_path)
        result = await _handle_connect_remote_kit({
            "host": "ok",
            "port": 8001,
            "auth_token": "shrt",
        })
        assert result["saved"] is False
        assert "8 character" in result["error"]

    @pytest.mark.asyncio
    async def test_invalid_host_rejected(self, tmp_path, monkeypatch):
        _redirect_community_paths(monkeypatch, tmp_path)
        result = await _handle_connect_remote_kit({
            "host": "https://lab.example.com",
            "port": 8001,
            "auth_token": "longenoughtoken",
        })
        assert result["saved"] is False
        assert "host" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_replaces_same_name_profile(self, tmp_path, monkeypatch):
        _redirect_community_paths(monkeypatch, tmp_path)
        await _handle_connect_remote_kit({
            "host": "first-host",
            "port": 8001,
            "auth_token": "longenoughtoken1",
            "name": "primary",
        })
        result2 = await _handle_connect_remote_kit({
            "host": "second-host",
            "port": 8002,
            "auth_token": "longenoughtoken2",
            "name": "primary",
        })
        assert result2["profile_count"] == 1  # replaced, not duplicated
        from pathlib import Path
        data = json.loads(Path(result2["remote_kits_path"]).read_text())
        assert data["profiles"][0]["host"] == "second-host"
        assert data["profiles"][0]["port"] == 8002
