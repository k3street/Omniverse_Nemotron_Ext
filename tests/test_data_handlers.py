"""
L0 tests for DATA_HANDLERS that can run without Kit RPC.
Handlers that need Kit are mocked via the mock_kit_rpc fixture.

This file restricts itself to handlers present on this branch — handlers
introduced in later phases (cloud_*, inspect_camera, behavior_tree, etc.)
are tested in their respective addendum branches.
"""
import json
import pytest
from unittest.mock import patch

pytestmark = pytest.mark.l0

from service.isaac_assist_service.chat.tools.tool_executor import (
    DATA_HANDLERS,
    _handle_lookup_product_spec,
    _handle_diagnose_physics_error,
    _handle_trace_config,
    _handle_validate_annotations,
    _handle_analyze_randomization,
    _handle_diagnose_domain_gap,
    _handle_suggest_physics_settings,
    _load_sensor_specs,
)

# These handlers may not exist on all branches — import conditionally
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

try:
    from service.isaac_assist_service.chat.tools.tool_executor import (
        _handle_diagnose_training,
        _handle_review_reward,
        _handle_profile_training_throughput,
    )
except ImportError:
    _handle_diagnose_training = None
    _handle_review_reward = None
    _handle_profile_training_throughput = None
























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


class TestGetRenderConfig:
    """Tier 8 — T8.1 get_render_config DATA handler."""

    @pytest.mark.asyncio
    async def test_get_render_config_queues_introspection(self, mock_kit_rpc):
        handler = DATA_HANDLERS["get_render_config"]
        result = await handler({})
        assert isinstance(result, dict)
        # The handler must always return a structured result with the shape
        # the LLM expects — keys: queued, patch_id, note. The note explains
        # what fields the eventual JSON will contain.
        assert "queued" in result
        assert "patch_id" in result
        assert "note" in result
        assert "renderer" in result["note"]
        assert "samples_per_pixel" in result["note"]
        assert "resolution" in result["note"]


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


class TestGenerateReward:
    """generate_reward Eureka data handler."""

    @pytest.mark.asyncio
    async def test_generate_reward_returns_eureka_config(self, tmp_path):
        # Create a fake DirectRLEnv file
        env_file = tmp_path / "reach_env.py"
        env_file.write_text(
            "class ReachEnv(DirectRLEnv):\n"
            "    def compute_reward(self):\n"
            "        return torch.zeros(self.num_envs)\n"
        )
        handler = DATA_HANDLERS["generate_reward"]
        result = await handler({
            "task_description": "reach a target position",
            "env_source_path": str(env_file),
            "num_candidates": 6,
            "num_iterations": 3,
        })
        assert "error" not in result
        assert result["task_description"] == "reach a target position"
        assert result["num_candidates"] == 6
        assert result["num_iterations"] == 3
        assert result["env_type"] == "DirectRLEnv"
        assert result["env_source_included"] is True
        assert "initial_prompt" in result
        assert "compute_reward" in result["initial_prompt"]

    @pytest.mark.asyncio
    async def test_generate_reward_rejects_manager_based(self, tmp_path):
        env_file = tmp_path / "bad_env.py"
        env_file.write_text(
            "class BadEnv(ManagerBasedRLEnv):\n    pass\n"
        )
        handler = DATA_HANDLERS["generate_reward"]
        result = await handler({
            "task_description": "locomotion",
            "env_source_path": str(env_file),
        })
        assert "error" in result
        assert "ManagerBasedRLEnv" in result["error"]

    @pytest.mark.asyncio
    async def test_generate_reward_file_not_found(self):
        handler = DATA_HANDLERS["generate_reward"]
        result = await handler({
            "task_description": "some task",
            "env_source_path": "/nonexistent/path/env.py",
        })
        # Should not crash — returns config with note about missing file
        assert result["env_source_included"] is False
        assert "initial_prompt" in result

    @pytest.mark.asyncio
    async def test_generate_reward_defaults(self, tmp_path):
        env_file = tmp_path / "env.py"
        env_file.write_text("class Env(DirectRLEnv): pass\n")
        handler = DATA_HANDLERS["generate_reward"]
        result = await handler({
            "task_description": "test",
            "env_source_path": str(env_file),
        })
        assert result["num_candidates"] == 4
        assert result["num_iterations"] == 5


class TestIterateReward:
    """iterate_reward Eureka mutation prompt handler."""

    @pytest.mark.asyncio
    async def test_iterate_with_feedback(self):
        handler = DATA_HANDLERS["iterate_reward"]
        result = await handler({
            "prev_reward_code": "def compute_reward(self):\n    return -dist",
            "metrics": {
                "fitness": 0.42,
                "components": {
                    "distance": {"mean": [1.0, 0.8, 0.5, 0.3, 0.2], "converged": True},
                    "action_penalty": {"mean": [0.1, 0.1, 0.1], "converged": False},
                },
                "task_success_rate": 0.35,
            },
            "user_feedback": "it keeps dropping the handle",
        })
        assert "mutation_prompt" in result
        prompt = result["mutation_prompt"]
        assert "Previous reward function:" in prompt
        assert "-dist" in prompt
        assert "distance" in prompt
        assert "action_penalty" in prompt
        assert "0.35" in prompt
        assert "it keeps dropping the handle" in prompt
        assert "generate an improved reward function" in prompt
        assert result["has_user_feedback"] is True
        assert result["prev_fitness"] == 0.42
        assert "distance" in result["components_analyzed"]

    @pytest.mark.asyncio
    async def test_iterate_without_feedback(self):
        handler = DATA_HANDLERS["iterate_reward"]
        result = await handler({
            "prev_reward_code": "def compute_reward(self):\n    return reward",
            "metrics": {
                "fitness": 0.7,
                "components": {},
                "task_success_rate": 0.5,
            },
        })
        prompt = result["mutation_prompt"]
        assert "User feedback" not in prompt
        assert result["has_user_feedback"] is False
        assert "No component metrics" in prompt


class TestEurekaStatus:
    """eureka_status data handler."""

    @pytest.mark.asyncio
    async def test_status_not_found(self):
        handler = DATA_HANDLERS["eureka_status"]
        result = await handler({"run_id": "nonexistent-run-123"})
        assert result["status"] == "not_found"
        assert result["run_id"] == "nonexistent-run-123"

    @pytest.mark.asyncio
    async def test_status_existing_run(self):
        import service.isaac_assist_service.chat.tools.tool_executor as te
        # Insert a fake run
        te._eureka_runs["test-run-001"] = {
            "status": "running",
            "current_iteration": 3,
            "total_iterations": 5,
            "candidates_evaluated": 12,
            "best_fitness": 0.85,
            "best_reward_code": "def compute_reward(self): return r",
        }
        try:
            handler = DATA_HANDLERS["eureka_status"]
            result = await handler({"run_id": "test-run-001"})
            assert result["status"] == "running"
            assert result["current_iteration"] == 3
            assert result["total_iterations"] == 5
            assert result["candidates_evaluated"] == 12
            assert result["best_fitness"] == 0.85
            assert result["best_reward_code"] is not None
        finally:
            del te._eureka_runs["test-run-001"]


class TestArenaLeaderboard:
    """arena_leaderboard data handler."""

    @pytest.mark.asyncio
    async def test_leaderboard_with_results(self):
        handler = DATA_HANDLERS["arena_leaderboard"]
        results_input = {
            "results": [
                {
                    "env_id": "Arena-Tabletop-Franka-v0",
                    "robot": "Franka",
                    "metrics": {"success_rate": 0.85, "episode_length": 120.5},
                },
                {
                    "env_id": "Arena-Tabletop-UR10-v0",
                    "robot": "UR10",
                    "metrics": {"success_rate": 0.72, "episode_length": 145.0},
                },
            ]
        }
        result = await handler(results_input)
        assert "leaderboard" in result
        assert result["count"] == 2
        assert len(result["entries"]) == 2
        assert "success_rate" in result["metric_columns"]
        assert "episode_length" in result["metric_columns"]
        # Franka has higher success_rate → should be rank 1
        assert result["entries"][0]["robot"] == "Franka"
        assert result["entries"][0]["rank"] == 1
        # Table should contain robot names
        assert "Franka" in result["leaderboard"]
        assert "UR10" in result["leaderboard"]

    @pytest.mark.asyncio
    async def test_leaderboard_empty_results(self):
        handler = DATA_HANDLERS["arena_leaderboard"]
        result = await handler({"results": []})
        assert result["leaderboard"] == "No results to display."
        assert result["entries"] == []

    @pytest.mark.asyncio
    async def test_leaderboard_single_result(self):
        handler = DATA_HANDLERS["arena_leaderboard"]
        result = await handler({
            "results": [
                {
                    "env_id": "Arena-Kitchen-Spot-v0",
                    "robot": "Spot",
                    "metrics": {"success_rate": 0.95},
                },
            ]
        })
        assert result["count"] == 1
        assert result["entries"][0]["rank"] == 1
        assert result["entries"][0]["robot"] == "Spot"


class TestLoadGrootPolicy:
    """load_groot_policy data handler."""

    @pytest.mark.asyncio
    async def test_vram_check_ok(self):
        handler = DATA_HANDLERS["load_groot_policy"]
        result = await handler({
            "robot_path": "/World/Franka",
            "embodiment": "LIBERO_PANDA",
        })
        assert result["vram_check"] == "ok"
        assert result["error"] is None
        assert result["robot_path"] == "/World/Franka"
        assert result["embodiment"] == "LIBERO_PANDA"
        assert "snapshot_download" in result["download_command"]
        assert "policy_server" in result["launch_command"]
        assert result["vram_required_gb"] == 24

    @pytest.mark.asyncio
    async def test_embodiment_lookup(self):
        handler = DATA_HANDLERS["load_groot_policy"]
        # Test each embodiment preset
        for emb in ["LIBERO_PANDA", "OXE_WIDOWX", "UNITREE_G1", "custom"]:
            result = await handler({
                "robot_path": "/World/Robot",
                "embodiment": emb,
            })
            assert result["embodiment"] == emb
            assert result["embodiment_config"]["obs_type"] == "rgb+proprio"
            assert "description" in result["embodiment_config"]

    @pytest.mark.asyncio
    async def test_default_model_id(self):
        handler = DATA_HANDLERS["load_groot_policy"]
        result = await handler({"robot_path": "/World/Robot"})
        assert result["model_id"] == "nvidia/GR00T-N1.6-3B"
        assert result["embodiment"] == "custom"

    @pytest.mark.asyncio
    async def test_custom_model_id(self):
        handler = DATA_HANDLERS["load_groot_policy"]
        result = await handler({
            "robot_path": "/World/Robot",
            "model_id": "nvidia/GR00T-N1.6-7B",
        })
        assert result["model_id"] == "nvidia/GR00T-N1.6-7B"
        assert "GR00T-N1.6-7B" in result["download_command"]
        assert "GR00T-N1.6-7B" in result["launch_command"]


class TestComparePolicies:
    """compare_policies data handler."""

    @pytest.mark.asyncio
    async def test_formatting_with_results(self):
        handler = DATA_HANDLERS["compare_policies"]
        result = await handler({
            "results": [
                {
                    "policy_name": "GR00T-zero-shot",
                    "model_id": "nvidia/GR00T-N1.6-3B",
                    "success_rate": 0.45,
                    "training_data_size": "0 demos",
                    "observation_type": "rgb+proprio",
                    "task_metrics": {"avg_steps": 120.5},
                },
                {
                    "policy_name": "GR00T-finetuned",
                    "model_id": "nvidia/GR00T-N1.6-3B",
                    "success_rate": 0.85,
                    "training_data_size": "100 demos",
                    "observation_type": "rgb+proprio",
                    "task_metrics": {"avg_steps": 80.2},
                },
            ]
        })
        assert result["count"] == 2
        assert len(result["entries"]) == 2
        # Sorted by success_rate descending
        assert result["entries"][0]["policy_name"] == "GR00T-finetuned"
        assert result["entries"][1]["policy_name"] == "GR00T-zero-shot"
        # Table contains policy names
        table = result["comparison_table"]
        assert "GR00T-zero-shot" in table
        assert "GR00T-finetuned" in table
        assert "85.0%" in table
        assert "45.0%" in table
        # Dimensions included
        assert len(result["dimensions"]) == 4
        assert "avg_steps" in result["metric_columns"]

    @pytest.mark.asyncio
    async def test_formatting_empty_results(self):
        handler = DATA_HANDLERS["compare_policies"]
        result = await handler({"results": []})
        assert result["comparison_table"] == "No results to compare."
        assert result["entries"] == []
        assert result["count"] == 0

    @pytest.mark.asyncio
    async def test_formatting_single_result(self):
        handler = DATA_HANDLERS["compare_policies"]
        result = await handler({
            "results": [
                {
                    "policy_name": "baseline",
                    "success_rate": 0.5,
                },
            ]
        })
        assert result["count"] == 1
        assert "baseline" in result["comparison_table"]
@pytest.mark.skipif("inspect_camera" not in DATA_HANDLERS,
                    reason="inspect_camera not available on this branch")
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


@pytest.mark.skipif("cloud_launch" not in DATA_HANDLERS,
                    reason="cloud_launch not available on this branch")
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


@pytest.mark.skipif("cloud_estimate_cost" not in DATA_HANDLERS,
                    reason="cloud_estimate_cost not available on this branch")
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


@pytest.mark.skipif("cloud_teardown" not in DATA_HANDLERS,
                    reason="cloud_teardown not available on this branch")
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


@pytest.mark.skipif("cloud_status" not in DATA_HANDLERS,
                    reason="cloud_status not available on this branch")
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


@pytest.mark.skipif("visualize_behavior_tree" not in DATA_HANDLERS,
                    reason="visualize_behavior_tree not available on this branch")
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


@pytest.mark.skipif("generate_robot_description" not in DATA_HANDLERS,
                    reason="generate_robot_description not available on this branch")
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


@pytest.mark.skipif("validate_scene_blueprint" not in DATA_HANDLERS,
                    reason="validate_scene_blueprint not available on this branch")
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

@pytest.mark.skipif(_handle_diagnose_physics_error is None,
                    reason="diagnose_physics_error not available on this branch")
@pytest.mark.skipif(_handle_diagnose_physics_error is None, reason="Phase 2 addendum not merged")
# ── Physics Material Database ─────────────────────────────────────────────

class TestLookupMaterial:
    """lookup_material DATA handler — physics material property lookup."""

    @pytest.fixture(autouse=True)
    def _reset_cache(self):
        """Reset the cached physics materials between tests."""
        import service.isaac_assist_service.chat.tools.tool_executor as te
        old = te._physics_materials
        te._physics_materials = None
        yield
        te._physics_materials = old

    @pytest.mark.asyncio
    async def test_known_pair_returns_pair_specific(self):
        """A known pair (steel+rubber) returns pair-specific measured values."""
        result = await _handle_lookup_material({
            "material_a": "steel",
            "material_b": "rubber",
        })
        assert result["found"] is True
        assert result["lookup_type"] == "pair_specific"
        assert result["static_friction"] == 0.80
        assert result["dynamic_friction"] == 0.65
        assert result["restitution"] == 0.30

    @pytest.mark.asyncio
    async def test_no_pair_returns_average_combine(self):
        """Unknown pair computes average combine values."""
        result = await _handle_lookup_material({
            "material_a": "glass",
            "material_b": "cardboard",
        })
        assert result["found"] is True
        assert result["lookup_type"] == "average_combine"
        assert result["combine_mode"] == "average"
        # glass sf=0.95, cardboard sf=0.50 → avg = 0.725
        assert abs(result["static_friction"] - 0.725) < 0.001

    @pytest.mark.asyncio
    async def test_unknown_material_returns_error(self):
        """Unknown material returns helpful error with suggestions."""
        result = await _handle_lookup_material({
            "material_a": "unobtanium",
            "material_b": "steel",
        })
        assert result["found"] is False
        assert "available_materials" in result
        assert "steel_mild" in result["available_materials"]

    @pytest.mark.asyncio
    async def test_alias_resolution(self):
        """Aliases like 'steel' → 'steel_mild' and 'rubber' → 'rubber_natural' work."""
        result = await _handle_lookup_material({
            "material_a": "metal",
            "material_b": "rubber",
        })
        assert result["found"] is True
        assert result["material_a"] == "steel_mild"
        assert result["material_b"] == "rubber_natural"

    @pytest.mark.asyncio
    async def test_missing_args_returns_error(self):
        """Empty material names return an error."""
        result = await _handle_lookup_material({
            "material_a": "",
            "material_b": "steel",
        })
        assert "error" in result

    @pytest.mark.asyncio
    async def test_pair_reverse_order_found(self):
        """Pair lookup works regardless of argument order."""
        result = await _handle_lookup_material({
            "material_a": "rubber",
            "material_b": "steel",
        })
        assert result["found"] is True
        assert result["lookup_type"] == "pair_specific"
        assert result["static_friction"] == 0.80

    @pytest.mark.asyncio
    async def test_density_included(self):
        """Density for both materials is returned."""
        result = await _handle_lookup_material({
            "material_a": "steel",
            "material_b": "aluminum",
        })
        assert result["found"] is True
        assert result["density_a_kg_m3"] == 7850
        assert result["density_b_kg_m3"] == 2700

    @pytest.mark.asyncio
    async def test_handler_registered_in_data_handlers(self):
        """lookup_material is registered in DATA_HANDLERS."""
        assert "lookup_material" in DATA_HANDLERS
        assert DATA_HANDLERS["lookup_material"] is not None


# ── Phase 2 Addendum: Smart Debugging ─────────────────────────────────────

@pytest.mark.skipif(_handle_diagnose_physics_error is None, reason="diagnose_physics_error not available on this branch")
@pytest.mark.skipif(_handle_diagnose_physics_error is None, reason="diagnose_physics_error not on this branch")
@pytest.mark.skipif(_handle_diagnose_physics_error is None, reason="Handler not available on this branch")
@pytest.mark.skipif(
    _handle_diagnose_physics_error is None,
    reason="diagnose_physics_error handler not present on this branch",
)
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


@pytest.mark.skipif(_handle_trace_config is None,
                    reason="trace_config not available on this branch")
@pytest.mark.skipif(_handle_trace_config is None, reason="Phase 2 addendum not merged")
@pytest.mark.skipif(_handle_trace_config is None, reason="trace_config not available on this branch")
@pytest.mark.skipif(_handle_trace_config is None, reason="trace_config not on this branch")
@pytest.mark.skipif(_handle_trace_config is None, reason="Handler not available on this branch")
@pytest.mark.skipif(
    _handle_trace_config is None,
    reason="trace_config handler not present on this branch",
)
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

@pytest.mark.skipif("apply_robot_fix_profile" not in DATA_HANDLERS,
                    reason="apply_robot_fix_profile not available on this branch")
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


# ── Phase 7B Addendum: SDG Quality ──────────────────────────────────────────

class TestValidateAnnotations:
    """validate_annotations DATA handler — cross-checks SDG annotation quality."""

    @pytest.mark.asyncio
    async def test_clean_data(self, mock_kit_rpc):
        """Clean annotations should queue successfully."""
        mock_kit_rpc["/exec_patch"] = {"queued": True, "patch_id": "test_val_clean"}
        mock_kit_rpc["/exec"] = {"queued": True, "patch_id": "test_val_clean"}
        result = await _handle_validate_annotations({"num_samples": 5})
        assert isinstance(result, dict)
        assert result.get("queued") is True

    @pytest.mark.asyncio
    async def test_phantom_bbox_detection(self, mock_kit_rpc):
        """Handler should send code that checks for out-of-bounds bboxes."""
        mock_kit_rpc["/exec_patch"] = {"queued": True, "patch_id": "test_val_phantom"}
        mock_kit_rpc["/exec"] = {"queued": True, "patch_id": "test_val_phantom"}
        result = await _handle_validate_annotations({"num_samples": 10})
        assert result.get("queued") is True
        # Verify handler is registered correctly
        assert "validate_annotations" in DATA_HANDLERS
        handler = DATA_HANDLERS["validate_annotations"]
        assert handler is _handle_validate_annotations

    @pytest.mark.asyncio
    async def test_missing_class_detection(self, mock_kit_rpc):
        """Handler should send code that detects missing declared classes."""
        mock_kit_rpc["/exec_patch"] = {"queued": True, "patch_id": "test_val_class"}
        mock_kit_rpc["/exec"] = {"queued": True, "patch_id": "test_val_class"}
        # Default num_samples
        result = await _handle_validate_annotations({})
        assert result.get("queued") is True
        # Default should use 10 samples
        assert result["type"] == "data"


class TestAnalyzeRandomization:
    """analyze_randomization DATA handler — DR distribution analysis."""

    @pytest.mark.asyncio
    async def test_normal_distribution(self, mock_kit_rpc):
        """Normal DR distributions should queue analysis successfully."""
        mock_kit_rpc["/exec_patch"] = {"queued": True, "patch_id": "test_dr_normal"}
        mock_kit_rpc["/exec"] = {"queued": True, "patch_id": "test_dr_normal"}
        result = await _handle_analyze_randomization({"num_samples": 50})
        assert isinstance(result, dict)
        assert result.get("queued") is True
        assert result["type"] == "data"

    @pytest.mark.asyncio
    async def test_narrow_range_warning(self, mock_kit_rpc):
        """Handler should send code that flags near-constant DR values."""
        mock_kit_rpc["/exec_patch"] = {"queued": True, "patch_id": "test_dr_narrow"}
        mock_kit_rpc["/exec"] = {"queued": True, "patch_id": "test_dr_narrow"}
        result = await _handle_analyze_randomization({"num_samples": 20})
        assert result.get("queued") is True
        # Verify handler is registered
        assert "analyze_randomization" in DATA_HANDLERS
        handler = DATA_HANDLERS["analyze_randomization"]
        assert handler is _handle_analyze_randomization


class TestDiagnoseDomainGap:
    """diagnose_domain_gap DATA handler — synthetic vs real comparison."""

    @pytest.mark.asyncio
    async def test_without_checkpoint(self, mock_kit_rpc):
        """Domain gap diagnosis without model checkpoint."""
        mock_kit_rpc["/exec_patch"] = {"queued": True, "patch_id": "test_gap_nockpt"}
        mock_kit_rpc["/exec"] = {"queued": True, "patch_id": "test_gap_nockpt"}
        result = await _handle_diagnose_domain_gap({
            "synthetic_dir": "/tmp/sdg_output/synthetic",
            "real_dir": "/tmp/real_images",
        })
        assert isinstance(result, dict)
        assert result.get("queued") is True
        assert result["type"] == "data"

    @pytest.mark.asyncio
    async def test_with_checkpoint(self, mock_kit_rpc):
        """Domain gap diagnosis with model checkpoint for feature extraction."""
        mock_kit_rpc["/exec_patch"] = {"queued": True, "patch_id": "test_gap_ckpt"}
        mock_kit_rpc["/exec"] = {"queued": True, "patch_id": "test_gap_ckpt"}
        result = await _handle_diagnose_domain_gap({
            "synthetic_dir": "/tmp/sdg_output/synthetic",
            "real_dir": "/tmp/real_images",
            "model_checkpoint": "/tmp/model/checkpoint.pth",
        })
        assert isinstance(result, dict)
        assert result.get("queued") is True

    @pytest.mark.asyncio
    async def test_missing_dirs_returns_error(self):
        """Missing directories should return error without queuing."""
        result = await _handle_diagnose_domain_gap({
            "synthetic_dir": "",
            "real_dir": "",
        })
        assert "error" in result

    @pytest.mark.asyncio
    async def test_invalid_path_chars_returns_error(self):
        """Paths with shell metacharacters should be rejected."""
        result = await _handle_diagnose_domain_gap({
            "synthetic_dir": "/tmp/sdg; rm -rf /",
            "real_dir": "/tmp/real",
        })
        assert "error" in result
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
# ── Performance Diagnostics ─────────────────────────────────────────────────

class TestAnalyzePerformance:
    """Unit tests for the _analyze_performance analysis function (no Kit needed)."""

    def test_narrow_phase_bottleneck(self):
        stats = {"nb_dynamic_rigids": 10, "solver_iterations": 4}
        timing = {"narrow_phase_ms": 25, "solver_ms": 1, "broad_phase_ms": 1}
        mem = {"used_mb": 2000, "total_mb": 8000}
        issues = _analyze_performance(stats, timing, mem)
        assert len(issues) >= 1
        cats = [i["category"] for i in issues]
        assert "physics_narrow_phase" in cats
        narrow = [i for i in issues if i["category"] == "physics_narrow_phase"][0]
        assert narrow["severity"] == "high"
        assert "25" in narrow["message"]
        assert "convex" in narrow["fix"].lower()

    def test_vram_pressure(self):
        stats = {}
        timing = {"narrow_phase_ms": 1, "solver_ms": 1, "broad_phase_ms": 1}
        mem = {"used_mb": 7500, "total_mb": 8000}
        issues = _analyze_performance(stats, timing, mem)
        cats = [i["category"] for i in issues]
        assert "memory" in cats
        mem_issue = [i for i in issues if i["category"] == "memory"][0]
        assert mem_issue["severity"] == "high"
        assert "7500" in mem_issue["message"]

    def test_solver_convergence(self):
        stats = {"solver_iterations": 32}
        timing = {"narrow_phase_ms": 1, "solver_ms": 12, "broad_phase_ms": 1}
        mem = {"used_mb": 2000, "total_mb": 8000}
        issues = _analyze_performance(stats, timing, mem)
        cats = [i["category"] for i in issues]
        assert "solver" in cats
        solver = [i for i in issues if i["category"] == "solver"][0]
        assert solver["severity"] == "medium"
        assert "32" in solver["message"]

    def test_no_issues_when_healthy(self):
        stats = {"nb_dynamic_rigids": 10, "solver_iterations": 4}
        timing = {"narrow_phase_ms": 2, "solver_ms": 1, "broad_phase_ms": 1}
        mem = {"used_mb": 2000, "total_mb": 8000}
        issues = _analyze_performance(stats, timing, mem)
        assert len(issues) == 0

    def test_broad_phase_bottleneck(self):
        stats = {"nb_dynamic_rigids": 10}
        timing = {"narrow_phase_ms": 1, "solver_ms": 1, "broad_phase_ms": 15}
        mem = {"used_mb": 1000, "total_mb": 8000}
        issues = _analyze_performance(stats, timing, mem)
        cats = [i["category"] for i in issues]
        assert "physics_broad_phase" in cats

    def test_high_dynamic_body_count(self):
        stats = {"nb_dynamic_rigids": 1000}
        timing = {"narrow_phase_ms": 1, "solver_ms": 1, "broad_phase_ms": 1}
        mem = {"used_mb": 1000, "total_mb": 8000}
        issues = _analyze_performance(stats, timing, mem)
        cats = [i["category"] for i in issues]
        assert "scene_complexity" in cats

    def test_multiple_issues_combined(self):
        stats = {"nb_dynamic_rigids": 800, "solver_iterations": 20}
        timing = {"narrow_phase_ms": 20, "solver_ms": 8, "broad_phase_ms": 12}
        mem = {"used_mb": 7800, "total_mb": 8000}
        issues = _analyze_performance(stats, timing, mem)
        # Should detect all 5 issue types
        cats = [i["category"] for i in issues]
        assert "physics_narrow_phase" in cats
        assert "memory" in cats
        assert "solver" in cats
        assert "physics_broad_phase" in cats
        assert "scene_complexity" in cats


class TestDiagnosePerformance:
    """diagnose_performance DATA handler — sends profiling code to Kit RPC."""

    @pytest.mark.asyncio
    async def test_diagnose_performance_queues_code(self, mock_kit_rpc):
        mock_kit_rpc["/exec_patch"] = {"queued": True, "patch_id": "test_perf"}
        handler = DATA_HANDLERS["diagnose_performance"]
        result = await handler({})
        assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_diagnose_performance_registered(self):
        assert "diagnose_performance" in DATA_HANDLERS
        assert DATA_HANDLERS["diagnose_performance"] is not None


class TestFindHeavyPrims:
    """find_heavy_prims DATA handler — sends traversal code to Kit RPC."""

    @pytest.mark.asyncio
    async def test_find_heavy_prims_queues_code(self, mock_kit_rpc):
        mock_kit_rpc["/exec_patch"] = {"queued": True, "patch_id": "test_heavy"}
        handler = DATA_HANDLERS["find_heavy_prims"]
        result = await handler({"threshold_triangles": 5000})
        assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_find_heavy_prims_default_threshold(self, mock_kit_rpc):
        mock_kit_rpc["/exec_patch"] = {"queued": True, "patch_id": "test_heavy_default"}
        handler = DATA_HANDLERS["find_heavy_prims"]
        result = await handler({})
        assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_find_heavy_prims_registered(self):
        assert "find_heavy_prims" in DATA_HANDLERS
        assert DATA_HANDLERS["find_heavy_prims"] is not None
# ── Scene Diff ──────────────────────────────────────────────────────────────

from service.isaac_assist_service.chat.tools.tool_executor import (
    _parse_unified_diff_to_changes,
    _summarize_changes,
)


class TestParseUnifiedDiffToChanges:
    """L0 tests for _parse_unified_diff_to_changes — pure function, no Kit needed."""

    def test_empty_diff(self):
        result = _parse_unified_diff_to_changes([])
        assert result == []

    def test_added_prim(self):
        diff = [
            "--- a",
            "+++ b",
            "@@ -0,0 +1,3 @@",
            '+    def Xform "MyCube"',
            "+    {",
            "+    }",
        ]
        result = _parse_unified_diff_to_changes(diff)
        assert len(result) >= 1
        added = [c for c in result if c["change_type"] == "added"]
        assert len(added) >= 1
        assert added[0]["prim_path"] == "MyCube"
        assert added[0]["details"]["prim_type"] == "Xform"

    def test_removed_prim(self):
        diff = [
            "--- a",
            "+++ b",
            "@@ -1,3 +0,0 @@",
            '-    def Mesh "OldTable"',
            "-    {",
            "-    }",
        ]
        result = _parse_unified_diff_to_changes(diff)
        removed = [c for c in result if c["change_type"] == "removed"]
        assert len(removed) >= 1
        assert removed[0]["prim_path"] == "OldTable"
        assert removed[0]["details"]["prim_type"] == "Mesh"

    def test_modified_attribute(self):
        diff = [
            "--- a",
            "+++ b",
            "@@ -1,5 +1,5 @@",
            '     def Xform "Robot"',
            "     {",
            '-        double3 xformOp:translate = (0, 0, 0)',
            '+        double3 xformOp:translate = (0.3, 0, 0)',
            "     }",
        ]
        result = _parse_unified_diff_to_changes(diff)
        modified = [c for c in result if c["change_type"] == "modified"]
        assert len(modified) >= 1
        assert modified[0]["prim_path"] == "Robot"
        assert "0.3" in modified[0]["details"]["new_line"]

    def test_headers_only_no_changes(self):
        """Diff with only headers and context lines should produce no changes."""
        diff = [
            "--- a/scene.usda",
            "+++ b/scene.usda",
            "@@ -1,3 +1,3 @@",
            " #usda 1.0",
            " (",
            " )",
        ]
        result = _parse_unified_diff_to_changes(diff)
        assert result == []


class TestSummarizeChanges:
    """L0 tests for _summarize_changes — pure function."""

    def test_no_changes(self):
        summary = _summarize_changes([])
        assert "No changes" in summary

    def test_added_and_removed(self):
        changes = [
            {"prim_path": "/World/Cube", "change_type": "added", "details": {"prim_type": "Cube"}},
            {"prim_path": "/World/Old", "change_type": "removed", "details": {"prim_type": "Mesh"}},
        ]
        summary = _summarize_changes(changes)
        assert "2 change(s)" in summary
        assert "+ Added Cube: /World/Cube" in summary
        assert "- Removed Mesh: /World/Old" in summary

    def test_modified(self):
        changes = [
            {
                "prim_path": "/World/Robot",
                "change_type": "modified",
                "details": {"old_line": "translate = (0,0,0)", "new_line": "translate = (1,0,0)"},
            },
        ]
        summary = _summarize_changes(changes)
        assert "1 change(s)" in summary
        assert "~ Modified: /World/Robot" in summary
        assert "translate = (1,0,0)" in summary


class TestSceneDiff:
    """scene_diff DATA handler — needs mock Kit RPC."""

    @pytest.mark.asyncio
    async def test_scene_diff_missing_args(self, mock_kit_rpc):
        handler = DATA_HANDLERS["scene_diff"]
        result = await handler({})
        assert "error" in result
        assert "Provide" in result["error"]

    @pytest.mark.asyncio
    async def test_scene_diff_last_save(self, mock_kit_rpc):
        mock_kit_rpc["/exec_patch"] = {"queued": True, "patch_id": "diff_patch"}
        handler = DATA_HANDLERS["scene_diff"]
        result = await handler({"since": "last_save"})
        assert isinstance(result, dict)
        # With mock, the handler queues the patch and parses output
        # Since the mock returns no output, changes will be empty
        assert "queued" in result or "changes" in result or "error" not in result

    @pytest.mark.asyncio
    async def test_scene_diff_explicit_snapshots(self, mock_kit_rpc):
        mock_kit_rpc["/exec_patch"] = {"queued": True, "patch_id": "diff_patch_explicit"}
        handler = DATA_HANDLERS["scene_diff"]
        result = await handler({"snapshot_a": "snap_1", "snapshot_b": "snap_2"})
        assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_scene_diff_invalid_snapshot_name(self, mock_kit_rpc):
        handler = DATA_HANDLERS["scene_diff"]
        result = await handler({"snapshot_a": "../etc/passwd", "snapshot_b": "snap_2"})
        assert "error" in result
        assert "Invalid" in result["error"]


class TestWatchChanges:
    """watch_changes DATA handler — needs mock Kit RPC."""

    @pytest.mark.asyncio
    async def test_watch_start(self, mock_kit_rpc):
        mock_kit_rpc["/exec_patch"] = {"queued": True, "patch_id": "watch_start"}
        handler = DATA_HANDLERS["watch_changes"]
        result = await handler({"action": "start"})
        assert result["status"] == "tracking_started"
        assert result["queued"] is True

    @pytest.mark.asyncio
    async def test_watch_stop(self, mock_kit_rpc):
        mock_kit_rpc["/exec_patch"] = {"queued": True, "patch_id": "watch_stop"}
        handler = DATA_HANDLERS["watch_changes"]
        result = await handler({"action": "stop"})
        assert isinstance(result, dict)
        # With mock returning no parsable output, should still return status
        assert "status" in result or "queued" in result

    @pytest.mark.asyncio
    async def test_watch_query(self, mock_kit_rpc):
        mock_kit_rpc["/exec_patch"] = {"queued": True, "patch_id": "watch_query"}
        handler = DATA_HANDLERS["watch_changes"]
        result = await handler({"action": "query"})
        assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_watch_unknown_action(self, mock_kit_rpc):
        handler = DATA_HANDLERS["watch_changes"]
        result = await handler({"action": "invalid_action"})
        assert "error" in result
        assert "Unknown action" in result["error"]
class TestSuggestPhysicsSettings:
    """suggest_physics_settings DATA handler — scene type to settings lookup."""

    @pytest.mark.asyncio
    async def test_rl_training_settings(self):
        result = await _handle_suggest_physics_settings({"scene_type": "rl_training"})
        settings = result["settings"]
        assert settings["solver"] == "TGS"
        assert settings["gpu_dynamics"] is True
        assert settings["ccd"] is False
        assert settings["solver_position_iterations"] == 4
        assert settings["time_steps_per_second"] == 120

    @pytest.mark.asyncio
    async def test_manipulation_settings(self):
        result = await _handle_suggest_physics_settings({"scene_type": "manipulation"})
        settings = result["settings"]
        assert settings["solver"] == "TGS"
        assert settings["ccd"] is True
        assert settings["solver_position_iterations"] == 16
        assert settings["time_steps_per_second"] == 240

    @pytest.mark.asyncio
    async def test_mobile_robot_settings(self):
        result = await _handle_suggest_physics_settings({"scene_type": "mobile_robot"})
        settings = result["settings"]
        assert settings["gpu_dynamics"] is True
        assert settings["time_steps_per_second"] == 60

    @pytest.mark.asyncio
    async def test_digital_twin_settings(self):
        result = await _handle_suggest_physics_settings({"scene_type": "digital_twin"})
        settings = result["settings"]
        assert settings["solver"] == "PGS"
        assert settings["gpu_dynamics"] is False
        assert settings["ccd"] is False

    @pytest.mark.asyncio
    async def test_unknown_scene_type_returns_error(self):
        result = await _handle_suggest_physics_settings({"scene_type": "unknown_type"})
        assert "error" in result
        assert "valid_types" in result
        assert "rl_training" in result["valid_types"]

    @pytest.mark.asyncio
    async def test_default_scene_type(self):
        result = await _handle_suggest_physics_settings({})
        settings = result["settings"]
        assert settings["scene_type"] == "manipulation"

    @pytest.mark.asyncio
    async def test_handler_registered_in_data_handlers(self):
        assert "suggest_physics_settings" in DATA_HANDLERS
        assert DATA_HANDLERS["suggest_physics_settings"] is not None
# ─── Phase 7A Addendum: RL Training Debugging & Quality ────────────────────

class TestDiagnoseTraining:
    """diagnose_training reads TB scalars + RSL-RL perf logs from a run dir."""

    @pytest.mark.asyncio
    async def test_missing_run_dir_returns_error(self):
        result = await _handle_diagnose_training({"run_dir": "/tmp/_no_such_run_dir_xyz"})
        assert "error" in result
        assert "does not exist" in result["error"]

    @pytest.mark.asyncio
    async def test_empty_run_dir_returns_unknown_status(self, tmp_path, monkeypatch):
        """Empty run dir → all checks 'unknown', 0 issues, no crash."""
        # Force the helpers to return empty even if tensorboard is installed.
        import service.isaac_assist_service.chat.tools.tool_executor as te
        monkeypatch.setattr(te, "_read_tb_scalars", lambda *a, **kw: [])
        monkeypatch.setattr(te, "_read_checkpoint_action_std", lambda *a, **kw: None)

        result = await _handle_diagnose_training({"run_dir": str(tmp_path)})
        assert "checks" in result
        assert result["status"].startswith("0 issue")
        for name in ("action_collapse", "entropy", "reward_hacking", "bimodal", "nan", "throughput"):
            assert name in result["checks"]

    @pytest.mark.asyncio
    async def test_action_collapse_detected(self, tmp_path, monkeypatch):
        import service.isaac_assist_service.chat.tools.tool_executor as te
        monkeypatch.setattr(te, "_read_tb_scalars", lambda *a, **kw: [])
        monkeypatch.setattr(te, "_read_checkpoint_action_std", lambda *a, **kw: 0.001)

        result = await _handle_diagnose_training({"run_dir": str(tmp_path)})
        assert result["checks"]["action_collapse"]["status"] == "critical"
        assert result["checks"]["action_collapse"]["value"] == 0.001
        assert any("init_noise_std" in s for s in result["suggestions"])

    @pytest.mark.asyncio
    async def test_entropy_collapse_detected(self, tmp_path, monkeypatch):
        import service.isaac_assist_service.chat.tools.tool_executor as te

        def fake_tb(run_dir, tag):
            if tag == "Loss/entropy":
                return [1.0, 0.5, 0.2, 0.05]  # collapse to <0.1
            return []

        monkeypatch.setattr(te, "_read_tb_scalars", fake_tb)
        monkeypatch.setattr(te, "_read_checkpoint_action_std", lambda *a, **kw: 0.5)

        result = await _handle_diagnose_training({"run_dir": str(tmp_path)})
        assert result["checks"]["entropy"]["status"] == "warning"
        assert result["checks"]["entropy"]["value"] == 0.05

    @pytest.mark.asyncio
    async def test_reward_hacking_detected(self, tmp_path, monkeypatch):
        import service.isaac_assist_service.chat.tools.tool_executor as te

        def fake_tb(run_dir, tag):
            if tag == "Train/mean_reward":
                return [1.0, 5.0, 10.0, 20.0]  # increasing
            if tag == "Episode/success_rate":
                return [0.05, 0.05, 0.05, 0.05]  # flat
            return []

        monkeypatch.setattr(te, "_read_tb_scalars", fake_tb)
        monkeypatch.setattr(te, "_read_checkpoint_action_std", lambda *a, **kw: 0.5)

        result = await _handle_diagnose_training({"run_dir": str(tmp_path)})
        assert result["checks"]["reward_hacking"]["status"] == "warning"

    @pytest.mark.asyncio
    async def test_nan_in_scalars_flags_critical(self, tmp_path, monkeypatch):
        import service.isaac_assist_service.chat.tools.tool_executor as te
        nan = float("nan")

        def fake_tb(run_dir, tag):
            if tag == "Train/mean_reward":
                return [1.0, 2.0, nan]
            return []

        monkeypatch.setattr(te, "_read_tb_scalars", fake_tb)
        monkeypatch.setattr(te, "_read_checkpoint_action_std", lambda *a, **kw: 0.5)

        result = await _handle_diagnose_training({"run_dir": str(tmp_path), "physics_dt": 0.01})
        assert result["checks"]["nan"]["status"] == "critical"
        assert "physics_dt" in result["checks"]["nan"]


class TestReviewReward:
    """review_reward runs static analysis on a reward function."""

    @pytest.mark.asyncio
    async def test_empty_reward_code_errors(self):
        result = await _handle_review_reward({"reward_code": ""})
        assert "error" in result

    @pytest.mark.asyncio
    async def test_alive_bonus_without_termination_flagged(self):
        code = '''
rewards = {
    "alive_bonus": RewTerm(func=mdp.alive_bonus, weight=0.5),
    "action_penalty": RewTerm(func=mdp.action_penalty, weight=-0.01),
}
'''
        result = await _handle_review_reward({
            "reward_code": code,
            "has_fall_termination": False,
        })
        checks = [i["check"] for i in result["issues"]]
        assert "hacking_risk" in checks
        assert any("alive_bonus" in s for s in result["suggestions"])

    @pytest.mark.asyncio
    async def test_alive_bonus_with_termination_not_flagged(self):
        code = '''
rewards = {
    "alive_bonus": RewTerm(func=mdp.alive_bonus, weight=0.5),
    "distance_to_goal": RewTerm(func=mdp.distance_to_goal, weight=-1.0),
    "reach_goal": RewTerm(func=mdp.reach_goal, weight=10.0),
}
'''
        result = await _handle_review_reward({
            "reward_code": code,
            "has_fall_termination": True,
        })
        checks = [i["check"] for i in result["issues"]]
        assert "hacking_risk" not in checks

    @pytest.mark.asyncio
    async def test_dominant_term_detected(self):
        code = '''
rewards = {
    "huge": RewTerm(func=mdp.huge, weight=500.0),
    "tiny": RewTerm(func=mdp.tiny, weight=0.001),
    "distance": RewTerm(func=mdp.distance, weight=1.0),
}
'''
        result = await _handle_review_reward({
            "reward_code": code,
            "has_fall_termination": True,
        })
        checks = [i["check"] for i in result["issues"]]
        assert "dominant_term" in checks

    @pytest.mark.asyncio
    async def test_scale_issue_detected(self):
        code = '''
rewards = {
    "tiny": RewTerm(func=mdp.tiny, weight=0.001),
    "distance": RewTerm(func=mdp.distance, weight=0.001),
}
'''
        result = await _handle_review_reward({
            "reward_code": code,
            "has_fall_termination": True,
            "max_possible_reward": 0.002,
        })
        checks = [i["check"] for i in result["issues"]]
        assert "scale" in checks

    @pytest.mark.asyncio
    async def test_clean_reward_passes(self):
        # Weights chosen so max/min ratio stays under the 100x dominant threshold.
        code = '''
rewards = {
    "distance_to_goal": RewTerm(func=mdp.distance_to_goal, weight=-1.0),
    "reach_goal": RewTerm(func=mdp.reach_goal, weight=10.0),
    "action_penalty": RewTerm(func=mdp.action_penalty, weight=-0.5),
}
'''
        result = await _handle_review_reward({
            "reward_code": code,
            "has_fall_termination": True,
        })
        # No critical / warning level issues for hacking or dominant term.
        warning_checks = {
            i["check"] for i in result["issues"] if i["status"] == "warning"
        }
        assert "hacking_risk" not in warning_checks
        assert "dominant_term" not in warning_checks


class TestProfileTrainingThroughput:
    """profile_training_throughput identifies sim-bound vs train-bound runs."""

    @pytest.mark.asyncio
    async def test_missing_run_dir_returns_error(self):
        result = await _handle_profile_training_throughput(
            {"run_dir": "/tmp/_no_such_perf_run_xyz"}
        )
        assert "error" in result

    @pytest.mark.asyncio
    async def test_missing_perf_scalars_returns_error(self, tmp_path, monkeypatch):
        import service.isaac_assist_service.chat.tools.tool_executor as te
        monkeypatch.setattr(te, "_read_tb_scalars", lambda *a, **kw: [])
        result = await _handle_profile_training_throughput({"run_dir": str(tmp_path)})
        assert "error" in result
        assert "missing" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_sim_bound_diagnosis(self, tmp_path, monkeypatch):
        import service.isaac_assist_service.chat.tools.tool_executor as te

        def fake_tb(run_dir, tag):
            if tag == "Perf/collection_time":
                return [90.0]
            if tag == "Perf/learning_time":
                return [10.0]
            if tag == "Perf/total_fps":
                return [12000.0]
            return []

        monkeypatch.setattr(te, "_read_tb_scalars", fake_tb)
        result = await _handle_profile_training_throughput({"run_dir": str(tmp_path)})
        assert result["bottleneck"] == "sim_bound"
        assert "TiledCamera" in result["suggestion"]
        assert result["collection_fraction"] > 0.8
        assert result["total_fps"] == 12000.0

    @pytest.mark.asyncio
    async def test_train_bound_diagnosis(self, tmp_path, monkeypatch):
        import service.isaac_assist_service.chat.tools.tool_executor as te

        def fake_tb(run_dir, tag):
            if tag == "Perf/collection_time":
                return [10.0]
            if tag == "Perf/learning_time":
                return [40.0]
            if tag == "Perf/total_fps":
                return [4000.0]
            return []

        monkeypatch.setattr(te, "_read_tb_scalars", fake_tb)
        result = await _handle_profile_training_throughput({"run_dir": str(tmp_path)})
        assert result["bottleneck"] == "train_bound"
        assert "PPO epochs" in result["suggestion"] or "network size" in result["suggestion"]

    @pytest.mark.asyncio
    async def test_balanced_diagnosis(self, tmp_path, monkeypatch):
        import service.isaac_assist_service.chat.tools.tool_executor as te

        def fake_tb(run_dir, tag):
            if tag == "Perf/collection_time":
                return [50.0]
            if tag == "Perf/learning_time":
                return [50.0]
            if tag == "Perf/total_fps":
                return [10000.0]
            return []

        monkeypatch.setattr(te, "_read_tb_scalars", fake_tb)
        result = await _handle_profile_training_throughput({"run_dir": str(tmp_path)})
        assert result["bottleneck"] == "balanced"
# ── Addendum H: Humanoid Advanced — diagnose_whole_body ─────────────────────

class TestDiagnoseWholeBody:
    """The diagnose_whole_body data handler returns a structured checklist."""

    @pytest.mark.asyncio
    async def test_default_thresholds_present(self):
        from service.isaac_assist_service.chat.tools.tool_executor import (
            _handle_diagnose_whole_body,
        )
        result = await _handle_diagnose_whole_body(
            {"articulation_path": "/World/G1"}
        )
        assert result["articulation_path"] == "/World/G1"
        assert result["support_polygon_margin_m"] == 0.05
        assert result["ee_accel_threshold_m_s2"] == 5.0
        assert isinstance(result["checks"], list)
        check_ids = {c["id"] for c in result["checks"]}
        assert {
            "balance_margin",
            "com_projection",
            "arm_payload_effect",
            "ee_acceleration",
        }.issubset(check_ids)
        # Every check must have a name and description for the LLM to surface
        for c in result["checks"]:
            assert c["name"]
            assert c["description"]

    @pytest.mark.asyncio
    async def test_custom_thresholds_propagated(self):
        from service.isaac_assist_service.chat.tools.tool_executor import (
            _handle_diagnose_whole_body,
        )
        result = await _handle_diagnose_whole_body({
            "articulation_path": "/World/H1",
            "support_polygon_margin_m": 0.1,
            "ee_accel_threshold_m_s2": 2.5,
        })
        assert result["support_polygon_margin_m"] == 0.1
        assert result["ee_accel_threshold_m_s2"] == 2.5
        # The thresholds should appear in the descriptions
        balance = next(c for c in result["checks"] if c["id"] == "balance_margin")
        assert "0.1" in balance["description"]
        ee = next(c for c in result["checks"] if c["id"] == "ee_acceleration")
        assert "2.5" in ee["description"]
# ── Collision Mesh Quality Addendum ──────────────────────────────────────


class TestCheckCollisionMesh:
    """check_collision_mesh DATA handler — generates read-only USD/trimesh
    analysis code, ships to Kit RPC, parses the JSON the script prints."""

    @pytest.mark.asyncio
    async def test_invalid_prim_path_returns_error(self):
        result = await _handle_check_collision_mesh({"prim_path": ""})
        assert "error" in result
        assert "prim_path" in result["error"]

    @pytest.mark.asyncio
    async def test_relative_prim_path_returns_error(self):
        result = await _handle_check_collision_mesh({"prim_path": "Robot/link3"})
        assert "error" in result

    @pytest.mark.asyncio
    async def test_handler_registered(self):
        assert "check_collision_mesh" in DATA_HANDLERS
        assert DATA_HANDLERS["check_collision_mesh"] is _handle_check_collision_mesh

    @pytest.mark.asyncio
    async def test_kit_rpc_failure_propagated(self, monkeypatch):
        """If Kit RPC call fails, the handler should surface a structured error."""
        import service.isaac_assist_service.chat.tools.tool_executor as te

        async def fake_exec_sync(code, timeout=20):
            return {"success": False, "output": "Kit RPC unavailable"}

        monkeypatch.setattr(te.kit_tools, "exec_sync", fake_exec_sync)
        result = await _handle_check_collision_mesh({"prim_path": "/World/Robot"})
        assert "error" in result
        assert "Kit RPC" in result["error"]
        assert "hint" in result

    @pytest.mark.asyncio
    async def test_kit_rpc_success_returns_parsed_dict(self, monkeypatch):
        """When Kit returns a valid JSON line on stdout, handler should parse it."""
        import service.isaac_assist_service.chat.tools.tool_executor as te

        async def fake_exec_sync(code, timeout=20):
            payload = {
                "prim": "/World/Robot/link3",
                "triangle_count": 45000,
                "is_watertight": False,
                "is_manifold": False,
                "degenerate_faces": 12,
                "collision_approximation": "none",
                "issues": [
                    {"type": "non_watertight", "severity": "warning"},
                    {"type": "degenerate_faces", "severity": "error", "count": 12},
                ],
                "recommendation": "Switch to convexDecomposition.",
            }
            return {"success": True, "output": json.dumps(payload)}

        monkeypatch.setattr(te.kit_tools, "exec_sync", fake_exec_sync)
        result = await _handle_check_collision_mesh({"prim_path": "/World/Robot/link3"})
        assert result["prim"] == "/World/Robot/link3"
        assert result["triangle_count"] == 45000
        assert result["is_watertight"] is False
        assert result["degenerate_faces"] == 12
        assert any(i["type"] == "non_watertight" for i in result["issues"])
        assert "convexDecomposition" in result["recommendation"]

    @pytest.mark.asyncio
    async def test_unparseable_output_returns_error(self, monkeypatch):
        import service.isaac_assist_service.chat.tools.tool_executor as te

        async def fake_exec_sync(code, timeout=20):
            return {"success": True, "output": "no json here, just chatter"}

        monkeypatch.setattr(te.kit_tools, "exec_sync", fake_exec_sync)
        result = await _handle_check_collision_mesh({"prim_path": "/World/Robot"})
        assert "error" in result
        assert "raw_output" in result


class TestCheckCollisionMeshCodeGen:
    """The read-only Kit script must compile and reference the right APIs."""

    def test_compiles(self):
        code = _gen_check_collision_mesh_code("/World/Robot/link3")
        compile(code, "<check_collision_mesh>", "exec")

    def test_references_required_apis(self):
        code = _gen_check_collision_mesh_code("/World/Robot/link3")
        assert "UsdGeom.Mesh" in code
        assert "UsdPhysics.CollisionAPI" in code
        assert "UsdPhysics.MeshCollisionAPI" in code
        assert "GetPointsAttr" in code
        assert "GetFaceVertexCountsAttr" in code
        assert "GetFaceVertexIndicesAttr" in code

    def test_includes_two_tier_checks(self):
        """Spec: fatal (out_of_range_indices, degenerate_faces, hull_exceeds_*,
        missing_collision_api) and silent degradation (non_watertight,
        non_manifold_edges, oversized_triangles)."""
        code = _gen_check_collision_mesh_code("/World/X")
        for marker in [
            "out_of_range_indices",
            "degenerate_faces",
            "missing_collision_api",
            "non_watertight",
            "non_manifold_edges",
            "oversized_triangles",
            "hull_exceeds_gpu_limit",
            "hull_exceeds_polygon_limit",
        ]:
            assert marker in code, f"Missing severity-tag marker: {marker}"

    def test_uses_physx_limits(self):
        """Spec: 255 polygon limit, 64 GPU vertex limit per hull."""
        code = _gen_check_collision_mesh_code("/World/X")
        assert "64" in code  # GPU vertex limit
        assert "255" in code  # polygon limit

    def test_returns_required_fields(self):
        """Result schema per spec."""
        code = _gen_check_collision_mesh_code("/World/X")
        for field in [
            "triangle_count",
            "is_watertight",
            "is_manifold",
            "degenerate_faces",
            "collision_approximation",
            "issues",
            "recommendation",
        ]:
            assert f'"{field}"' in code

    def test_path_is_sanitized(self):
        """Quotes in the prim path must not break the literal."""
        code = _gen_check_collision_mesh_code('/World/"weird"/path')
        compile(code, "<sanitized>", "exec")
class TestSimToRealGap:
    """Test sim-to-real gap measurement and recommendations."""

    @pytest.mark.asyncio
    async def test_measure_gap_missing_files(self):
        handler = DATA_HANDLERS["measure_sim_real_gap"]
        result = await handler({"sim_trajectory": "/nonexistent/sim.h5", "real_trajectory": "/nonexistent/real.h5"})
        assert "error" in result
        assert "not found" in result["error"]

    @pytest.mark.asyncio
    async def test_measure_gap_csv(self, tmp_path):
        # Create matched CSV trajectories
        sim_csv = tmp_path / "sim.csv"
        real_csv = tmp_path / "real.csv"
        sim_csv.write_text("joint_0,joint_1\n0.0,0.0\n0.1,0.1\n0.2,0.2\n")
        real_csv.write_text("joint_0,joint_1\n0.0,0.0\n0.1,0.1\n0.2,0.2\n")
        handler = DATA_HANDLERS["measure_sim_real_gap"]
        # CSV path doesn't have joint_positions key — should error gracefully
        result = await handler({"sim_trajectory": str(sim_csv), "real_trajectory": str(real_csv)})
        assert "error" in result or "joint_errors" in result

    @pytest.mark.asyncio
    async def test_suggest_no_gap_report(self):
        handler = DATA_HANDLERS["suggest_parameter_adjustment"]
        result = await handler({})
        assert "error" in result

    @pytest.mark.asyncio
    async def test_suggest_with_high_error(self):
        handler = DATA_HANDLERS["suggest_parameter_adjustment"]
        gap = {
            "joint_errors": {"joint_0": {"mean_error_deg": 6.0, "max_error_deg": 8.0}},
            "worst_joint": "joint_0",
            "ee_error_mm": {"mean_mm": 15.0, "max_mm": 20.0},
        }
        result = await handler({"gap_report": gap})
        assert "suggestions" in result
        assert any("damping" in s.get("parameter", "") for s in result["suggestions"])

    @pytest.mark.asyncio
    async def test_suggest_within_tolerance(self):
        handler = DATA_HANDLERS["suggest_parameter_adjustment"]
        gap = {
            "joint_errors": {"joint_0": {"mean_error_deg": 0.5, "max_error_deg": 1.0}},
            "worst_joint": "joint_0",
        }
        result = await handler({"gap_report": gap})
        assert "suggestions" in result
        assert "no adjustments needed" in str(result["suggestions"]).lower()

    @pytest.mark.asyncio
    async def test_compare_video_missing_files(self):
        handler = DATA_HANDLERS["compare_sim_real_video"]
        result = await handler({"sim_video_path": "/nonexistent/sim.mp4", "real_video_path": "/nonexistent/real.mp4"})
        assert "error" in result

    @pytest.mark.asyncio
    async def test_compare_video_returns_prompt(self, tmp_path):
        sim_vid = tmp_path / "sim.mp4"
        real_vid = tmp_path / "real.mp4"
        sim_vid.write_bytes(b"fake mp4")
        real_vid.write_bytes(b"fake mp4")
        handler = DATA_HANDLERS["compare_sim_real_video"]
        result = await handler({"sim_video_path": str(sim_vid), "real_video_path": str(real_vid)})
        assert "analysis_prompt" in result
        assert "next_step" in result
class TestGr00tTooling:
    """Test GR00T addendum data handlers."""

    @pytest.mark.asyncio
    async def test_detect_ood_tier1_stable(self):
        handler = DATA_HANDLERS["detect_ood"]
        action_seq = [[0.0, 0.0], [0.01, 0.01], [0.02, 0.02], [0.03, 0.03]]
        result = await handler({"tier": 1, "action_sequence": action_seq})
        assert result["tier"] == 1
        assert result["is_ood"] is False or "max_action_variance" in result

    @pytest.mark.asyncio
    async def test_detect_ood_tier1_unstable(self):
        handler = DATA_HANDLERS["detect_ood"]
        action_seq = [[0.0, 0.0], [5.0, -5.0], [-3.0, 4.0], [10.0, -8.0]]
        result = await handler({"tier": 1, "action_sequence": action_seq})
        assert result["is_ood"] is True
        assert "warning" in result and result["warning"] is not None

    @pytest.mark.asyncio
    async def test_detect_ood_tier1_no_data(self):
        handler = DATA_HANDLERS["detect_ood"]
        result = await handler({"tier": 1})
        assert "error" in result

    @pytest.mark.asyncio
    async def test_detect_ood_invalid_tier(self):
        handler = DATA_HANDLERS["detect_ood"]
        result = await handler({"tier": 5})
        assert "error" in result

    @pytest.mark.asyncio
    async def test_suggest_data_mix_balanced(self):
        handler = DATA_HANDLERS["suggest_data_mix"]
        result = await handler({
            "task_type": "tabletop pick-and-place",
            "available_data": {"real_demos": 200, "sim_demos": 5000, "video_demos": 0},
        })
        assert result["recommendation"]["real_demos_to_use"] == 200
        assert result["recommendation"]["sim_demos_to_use"] == 200  # 1:1 ratio

    @pytest.mark.asyncio
    async def test_suggest_data_mix_no_real(self):
        handler = DATA_HANDLERS["suggest_data_mix"]
        result = await handler({
            "task_type": "navigation",
            "available_data": {"real_demos": 0, "sim_demos": 1000, "video_demos": 0},
        })
        assert result["warnings"]
        assert "No real demos" in result["warnings"][0]

    @pytest.mark.asyncio
    async def test_suggest_finetune_similar(self):
        handler = DATA_HANDLERS["suggest_finetune_config"]
        result = await handler({"task_type": "similar_to_pretrain", "hardware": "A6000"})
        assert "vision_encoder" in result["freeze_layers"]
        assert "language_model" in result["freeze_layers"]
        assert result["recommended_batch_size"] == 200

    @pytest.mark.asyncio
    async def test_suggest_finetune_new_visual(self):
        handler = DATA_HANDLERS["suggest_finetune_config"]
        result = await handler({"task_type": "new_visual_domain", "hardware": "A6000"})
        assert "warning" in result
        assert "Don't Blind" in result["warning"]

    @pytest.mark.asyncio
    async def test_suggest_finetune_invalid(self):
        handler = DATA_HANDLERS["suggest_finetune_config"]
        result = await handler({"task_type": "bogus", "hardware": "A6000"})
        assert "error" in result

    @pytest.mark.asyncio
    async def test_monitor_forgetting_missing_dir(self):
        handler = DATA_HANDLERS["monitor_forgetting"]
        result = await handler({"checkpoint_dir": "/nonexistent", "base_model": "/base"})
        assert "error" in result

    @pytest.mark.asyncio
    async def test_monitor_forgetting_returns_thresholds(self, tmp_path):
        handler = DATA_HANDLERS["monitor_forgetting"]
        result = await handler({"checkpoint_dir": str(tmp_path), "base_model": "/base"})
        assert "alert_thresholds" in result
        assert result["alert_thresholds"]["vqa_score_drop_pct"] == 20

    @pytest.mark.asyncio
    async def test_analyze_checkpoint_missing(self):
        handler = DATA_HANDLERS["analyze_checkpoint"]
        result = await handler({"checkpoint_path": "/nonexistent.pt"})
        assert "error" in result

    @pytest.mark.asyncio
    async def test_analyze_checkpoint_exists(self, tmp_path):
        ckpt = tmp_path / "model.pt"
        ckpt.write_bytes(b"fake")
        handler = DATA_HANDLERS["analyze_checkpoint"]
        result = await handler({"checkpoint_path": str(ckpt)})
        assert "expected_structure" in result
        assert "embodiment" in result["expected_structure"]
# ─── Tier 0 Atomic Tools — DATA handlers ─────────────────────────────────────
# Each handler emits a small snippet through kit_tools.queue_exec_patch; in
# tests we stub queue_exec_patch so we can assert on the generated snippet
# without requiring a running Kit RPC server.


@pytest.fixture()
def capture_kit_patches(monkeypatch):
    """Intercept kit_tools.queue_exec_patch and record the submitted code."""
    captured: list = []

    async def fake_queue(code, description=""):
        captured.append({"code": code, "description": description})
        return {"queued": True, "patch_id": "test_tier0"}

    import service.isaac_assist_service.chat.tools.kit_tools as kt
    monkeypatch.setattr(kt, "queue_exec_patch", fake_queue)
    return captured


class TestTier0GetAttribute:
    @pytest.mark.asyncio
    async def test_get_attribute_emits_lookup_code(self, capture_kit_patches):
        handler = DATA_HANDLERS["get_attribute"]
        result = await handler({"prim_path": "/World/Cube", "attr_name": "radius"})
        assert result["queued"] is True
        assert len(capture_kit_patches) == 1
        code = capture_kit_patches[0]["code"]
        compile(code, "<get_attribute>", "exec")
        assert "'/World/Cube'" in code
        assert "'radius'" in code
        assert "GetAttribute" in code


class TestTier0GetWorldTransform:
    @pytest.mark.asyncio
    async def test_get_world_transform_emits_xformable(self, capture_kit_patches):
        handler = DATA_HANDLERS["get_world_transform"]
        result = await handler({"prim_path": "/World/Robot"})
        assert result["queued"] is True
        code = capture_kit_patches[0]["code"]
        compile(code, "<get_world_transform>", "exec")
        assert "ComputeLocalToWorldTransform" in code
        assert "'/World/Robot'" in code

    @pytest.mark.asyncio
    async def test_get_world_transform_time_code(self, capture_kit_patches):
        handler = DATA_HANDLERS["get_world_transform"]
        await handler({"prim_path": "/World/A", "time_code": 12.5})
        code = capture_kit_patches[0]["code"]
        assert "12.5" in code


class TestTier0GetBoundingBox:
    @pytest.mark.asyncio
    async def test_get_bounding_box_default_purpose(self, capture_kit_patches):
        handler = DATA_HANDLERS["get_bounding_box"]
        await handler({"prim_path": "/World/Box"})
        code = capture_kit_patches[0]["code"]
        compile(code, "<get_bounding_box>", "exec")
        assert "BBoxCache" in code
        assert "UsdGeom.Tokens.default" in code

    @pytest.mark.asyncio
    async def test_get_bounding_box_render_purpose(self, capture_kit_patches):
        handler = DATA_HANDLERS["get_bounding_box"]
        await handler({"prim_path": "/World/Box", "purpose": "render"})
        code = capture_kit_patches[0]["code"]
        assert "UsdGeom.Tokens.render" in code


class TestTier0GetJointLimits:
    @pytest.mark.asyncio
    async def test_get_joint_limits_emits_revolute_check(self, capture_kit_patches):
        handler = DATA_HANDLERS["get_joint_limits"]
        await handler({"articulation": "/World/Franka", "joint_name": "panda_joint1"})
        code = capture_kit_patches[0]["code"]
        compile(code, "<get_joint_limits>", "exec")
        assert "'panda_joint1'" in code
        assert "physics:lowerLimit" in code
        assert "physics:upperLimit" in code


class TestTier0GetContactReport:
    @pytest.mark.asyncio
    async def test_get_contact_report_emits_filter(self, capture_kit_patches):
        handler = DATA_HANDLERS["get_contact_report"]
        await handler({"prim_path": "/World/Gripper", "max_contacts": 10})
        code = capture_kit_patches[0]["code"]
        compile(code, "<get_contact_report>", "exec")
        assert "_ATOMIC_CONTACT_BUFFER" in code
        assert "'/World/Gripper'" in code
        assert "max_contacts = 10" in code


class TestTier0GetTrainingStatus:
    @pytest.mark.asyncio
    async def test_missing_log_dir_returns_missing(self, tmp_path):
        handler = DATA_HANDLERS["get_training_status"]
        result = await handler({"run_id": "nonexistent_run", "log_dir": str(tmp_path / "no_such_dir")})
        assert result["state"] == "missing"
        assert "error" in result

    @pytest.mark.asyncio
    async def test_starting_when_no_event_files(self, tmp_path):
        handler = DATA_HANDLERS["get_training_status"]
        result = await handler({"run_id": "fresh_run", "log_dir": str(tmp_path)})
        assert result["state"] == "starting"
        assert result["events_found"] == 0

    @pytest.mark.asyncio
    async def test_running_when_event_file_present(self, tmp_path):
        # Create a dummy tfevents file so events_found > 0
        (tmp_path / "events.out.tfevents.1700000000.host").write_bytes(b"dummy")
        handler = DATA_HANDLERS["get_training_status"]
        result = await handler({"run_id": "live_run", "log_dir": str(tmp_path)})
        # state should be 'running' when events are present and no pid file
        assert result["events_found"] == 1
        assert result["state"] == "running"


class TestTier0PixelToWorld:
    @pytest.mark.asyncio
    async def test_pixel_to_world_emits_projection_code(self, capture_kit_patches):
        handler = DATA_HANDLERS["pixel_to_world"]
        await handler({"camera": "/World/Camera", "x": 640, "y": 360})
        code = capture_kit_patches[0]["code"]
        compile(code, "<pixel_to_world>", "exec")
        assert "'/World/Camera'" in code
        assert "px = 640" in code
        assert "py = 360" in code
        assert "ComputeProjectionMatrix" in code

    @pytest.mark.asyncio
    async def test_pixel_to_world_resolution_override(self, capture_kit_patches):
        handler = DATA_HANDLERS["pixel_to_world"]
        await handler({"camera": "/World/C", "x": 0, "y": 0, "resolution": [1920, 1080]})
        code = capture_kit_patches[0]["code"]
        assert "[1920, 1080]" in code


# ─── Tier 0 — dispatch coverage ──────────────────────────────────────────────
# Sanity check: every Tier 0 tool name has a handler registered in either
# CODE_GEN_HANDLERS or DATA_HANDLERS.

TIER0_TOOLS = [
    "get_attribute",
    "get_world_transform",
    "get_bounding_box",
    "set_semantic_label",
    "get_joint_limits",
    "set_drive_gains",
    "get_contact_report",
    "set_render_mode",
    "set_variant",
    "get_training_status",
    "pixel_to_world",
    "record_trajectory",
]


class TestTier0Coverage:
    """All 12 Tier 0 tools must be dispatchable."""

    def test_exactly_twelve_tier0_tools(self):
        assert len(TIER0_TOOLS) == 12
        assert len(set(TIER0_TOOLS)) == 12

    @pytest.mark.parametrize("name", TIER0_TOOLS)
    def test_tool_registered(self, name):
        from service.isaac_assist_service.chat.tools.tool_executor import (
            CODE_GEN_HANDLERS,
            DATA_HANDLERS,
        )
        assert name in CODE_GEN_HANDLERS or name in DATA_HANDLERS, (
            f"Tier 0 tool '{name}' has no handler"
        )

    @pytest.mark.parametrize("name", TIER0_TOOLS)
    def test_tool_in_schema(self, name):
        from service.isaac_assist_service.chat.tools.tool_schemas import ISAAC_SIM_TOOLS
        names = {t["function"]["name"] for t in ISAAC_SIM_TOOLS}
        assert name in names, f"Tier 0 tool '{name}' missing from ISAAC_SIM_TOOLS"
# ─── Atomic Tier 5 — OmniGraph data handlers ────────────────────────────────


class TestListGraphs:
    """list_graphs enumerates OmniGraph prims via Kit RPC."""

    @pytest.mark.asyncio
    async def test_returns_graphs_when_kit_succeeds(self, monkeypatch):
        """exec_sync stdout should be parsed into a {graphs, count} dict."""
        import service.isaac_assist_service.chat.tools.kit_tools as kt

        async def fake_exec_sync(code, timeout=10):
            assert "OmniGraph" in code or "ComputeGraph" in code
            return {
                "success": True,
                "output": '{"graphs": [{"path": "/World/G", "type": "OmniGraph", "name": "G"}], "count": 1}',
            }

        monkeypatch.setattr(kt, "exec_sync", fake_exec_sync)
        handler = DATA_HANDLERS["list_graphs"]
        result = await handler({})
        assert result["count"] == 1
        assert result["graphs"][0]["path"] == "/World/G"

    @pytest.mark.asyncio
    async def test_kit_unavailable_returns_empty(self, monkeypatch):
        import service.isaac_assist_service.chat.tools.kit_tools as kt

        async def fake_exec_sync(code, timeout=10):
            return {"success": False, "output": "Kit RPC offline"}

        monkeypatch.setattr(kt, "exec_sync", fake_exec_sync)
        handler = DATA_HANDLERS["list_graphs"]
        result = await handler({})
        assert result["count"] == 0
        assert result["graphs"] == []
        assert "Kit RPC offline" in result["error"]

    @pytest.mark.asyncio
    async def test_handler_registered(self):
        assert "list_graphs" in DATA_HANDLERS
        assert DATA_HANDLERS["list_graphs"] is not None


class TestInspectGraph:
    """inspect_graph returns nodes + connections for a single graph."""

    @pytest.mark.asyncio
    async def test_returns_node_list(self, monkeypatch):
        import service.isaac_assist_service.chat.tools.kit_tools as kt

        payload = {
            "graph_path": "/World/G",
            "nodes": [
                {"name": "tick", "path": "/World/G/tick", "type": "omni.graph.action.OnPlaybackTick", "attributes": {}},
            ],
            "connections": [],
            "node_count": 1,
        }

        async def fake_exec_sync(code, timeout=15):
            assert "/World/G" in code
            assert "og.Controller.graph" in code
            return {"success": True, "output": json.dumps(payload)}

        monkeypatch.setattr(kt, "exec_sync", fake_exec_sync)
        handler = DATA_HANDLERS["inspect_graph"]
        result = await handler({"graph_path": "/World/G"})
        assert result["node_count"] == 1
        assert result["nodes"][0]["name"] == "tick"

    @pytest.mark.asyncio
    async def test_kit_unavailable_returns_error(self, monkeypatch):
        import service.isaac_assist_service.chat.tools.kit_tools as kt

        async def fake_exec_sync(code, timeout=15):
            return {"success": False, "output": "Kit not running"}

        monkeypatch.setattr(kt, "exec_sync", fake_exec_sync)
        handler = DATA_HANDLERS["inspect_graph"]
        result = await handler({"graph_path": "/World/G"})
        assert result["graph_path"] == "/World/G"
        assert "Kit not running" in result["error"]

    @pytest.mark.asyncio
    async def test_handler_registered(self):
        assert "inspect_graph" in DATA_HANDLERS
        assert DATA_HANDLERS["inspect_graph"] is not None
# ── Tier 6 — Lighting DATA handlers ──────────────────────────────────────────

class TestListLights:
    """list_lights queues a Kit-side script that traverses the stage for UsdLux prims."""

    @pytest.mark.asyncio
    async def test_list_lights_registered(self):
        assert "list_lights" in DATA_HANDLERS
        assert DATA_HANDLERS["list_lights"] is not None

    @pytest.mark.asyncio
    async def test_list_lights_queues_traversal_script(self, mock_kit_rpc):
        # Patch the exact /exec_patch endpoint that queue_exec_patch posts to
        mock_kit_rpc["/exec_patch"] = {"queued": True, "patch_id": "test_lights_001"}
        handler = DATA_HANDLERS["list_lights"]
        result = await handler({})
        assert isinstance(result, dict)
        assert result.get("queued") is True


class TestGetLightProperties:
    """get_light_properties queues a Kit script reading UsdLux attributes."""

    @pytest.mark.asyncio
    async def test_get_light_properties_registered(self):
        assert "get_light_properties" in DATA_HANDLERS
        assert DATA_HANDLERS["get_light_properties"] is not None

    @pytest.mark.asyncio
    async def test_get_light_properties_requires_path(self, mock_kit_rpc):
        handler = DATA_HANDLERS["get_light_properties"]
        with pytest.raises(KeyError):
            await handler({})  # no light_path

    @pytest.mark.asyncio
    async def test_get_light_properties_queues_script(self, mock_kit_rpc):
        mock_kit_rpc["/exec_patch"] = {"queued": True, "patch_id": "test_lights_002"}
        handler = DATA_HANDLERS["get_light_properties"]
        result = await handler({"light_path": "/World/SunLight"})
        assert isinstance(result, dict)
        assert result.get("queued") is True
# ---------------------------------------------------------------------------
# Tier 9 — USD Layers & Variants data handlers
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    "list_layers" not in DATA_HANDLERS,
    reason="Tier 9 (USD Layers & Variants) not merged on this branch",
)
class TestListLayers:
    """list_layers introspects the stage's layer stack via Kit RPC."""

    @pytest.mark.asyncio
    async def test_list_layers_queues_introspection(self, mock_kit_rpc, monkeypatch):
        # Patch queue_exec_patch so we can capture the script that would be queued
        captured = {}
        async def fake_queue(code, desc):
            captured["code"] = code
            return {"queued": True, "patch_id": "ok"}
        import service.isaac_assist_service.chat.tools.kit_tools as kt
        monkeypatch.setattr(kt, "queue_exec_patch", fake_queue)

        handler = DATA_HANDLERS["list_layers"]
        result = await handler({})
        assert result["queued"] is True


# ---------------------------------------------------------------------------
# Tier 11 — list_semantic_classes
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    "list_semantic_classes" not in DATA_HANDLERS,
    reason="Tier 11 (SDG Annotation) not merged on this branch",
)
class TestListSemanticClasses:
    """Walks the stage and gathers every Semantics.SemanticsAPI label."""

    @pytest.mark.asyncio
    async def test_queues_introspection(self, mock_kit_rpc, monkeypatch):
        captured = {}
        async def fake_queue(code, desc):
            captured["code"] = code
            captured["desc"] = desc
            return {"queued": True, "patch_id": "tier9_layers_001"}
        import service.isaac_assist_service.chat.tools.kit_tools as kt
        monkeypatch.setattr(kt, "queue_exec_patch", fake_queue)

        handler = DATA_HANDLERS["list_layers"]
        result = await handler({})
        assert result["queued"] is True
        assert result["patch_id"] == "tier9_layers_001"
        # The introspection script must walk the layer stack and emit a JSON dict
        assert "GetLayerStack" in captured["code"] or "GetRootLayer" in captured["code"]
        assert "GetEditTarget" in captured["code"]
        assert "json.dumps" in captured["code"]
        # The note must describe the response shape so the LLM knows what to expect
        assert "root_layer" in result["note"]
        assert "edit_target" in result["note"]


@pytest.mark.skipif(
    "list_variant_sets" not in DATA_HANDLERS,
    reason="Tier 9 (USD Layers & Variants) not merged on this branch",
)
class TestListVariantSets:
    """list_variant_sets reads prim.GetVariantSets() via Kit RPC."""

    @pytest.mark.asyncio
    async def test_list_variant_sets_embeds_prim_path(self, mock_kit_rpc, monkeypatch):
        captured = {}
        async def fake_queue(code, desc):
            captured["code"] = code
            return {"queued": True, "patch_id": "ok"}
        import service.isaac_assist_service.chat.tools.kit_tools as kt
        monkeypatch.setattr(kt, "queue_exec_patch", fake_queue)

        handler = DATA_HANDLERS["list_variant_sets"]
        result = await handler({"prim_path": "/World/Robot"})
        assert result["queued"] is True
        assert "/World/Robot" in captured["code"]
        assert "GetSemanticDataAttr" in captured["code"]
        assert "json.dumps" in captured["code"]
        # The note must describe the response shape so the LLM knows what to expect.
        assert "classes" in result["note"]
        assert "total_classes" in result["note"]
        assert "total_labeled_prims" in result["note"]

    @pytest.mark.asyncio
    async def test_script_compiles(self, mock_kit_rpc, monkeypatch):
        async def fake_queue(code, desc):
            compile(code, "<list_semantic_classes>", "exec")
            return {"queued": True, "patch_id": "ok"}
        import service.isaac_assist_service.chat.tools.kit_tools as kt
        monkeypatch.setattr(kt, "queue_exec_patch", fake_queue)

        handler = DATA_HANDLERS["list_semantic_classes"]
        result = await handler({})
        assert result["queued"] is True


# ---------------------------------------------------------------------------
# Tier 11 — get_semantic_label
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    "get_semantic_label" not in DATA_HANDLERS,
    reason="Tier 11 (SDG Annotation) not merged on this branch",
)
class TestGetSemanticLabel:
    """Reads every Semantics.SemanticsAPI instance applied to a single prim."""

    @pytest.mark.asyncio
    async def test_embeds_prim_path(self, mock_kit_rpc, monkeypatch):
        captured = {}
        async def fake_queue(code, desc):
            captured["code"] = code
            captured["desc"] = desc
            return {"queued": True, "patch_id": "tier9_vsets_001"}
        import service.isaac_assist_service.chat.tools.kit_tools as kt
        monkeypatch.setattr(kt, "queue_exec_patch", fake_queue)

        handler = DATA_HANDLERS["list_variant_sets"]
        result = await handler({"prim_path": "/World/Asset"})
        assert result["queued"] is True
        assert result["prim_path"] == "/World/Asset"
        # Prim path must be embedded into the introspection script (via repr())
        assert "/World/Asset" in captured["code"]
        assert "GetVariantSets" in captured["code"]
        assert "GetVariantSelection" in captured["code"]

    @pytest.mark.asyncio
    async def test_list_variant_sets_path_with_special_chars(self, mock_kit_rpc, monkeypatch):
        """Special chars in prim path must round-trip through repr() without breaking syntax."""
        async def fake_queue(code, desc):
            # Verify the generated introspection script is valid Python
            compile(code, "<list_variant_sets>", "exec")
            return {"queued": True, "patch_id": "tier11_label_001"}
        import service.isaac_assist_service.chat.tools.kit_tools as kt
        monkeypatch.setattr(kt, "queue_exec_patch", fake_queue)

        handler = DATA_HANDLERS["get_semantic_label"]
        result = await handler({"prim_path": "/World/Tray/bottle_03"})
        assert result["queued"] is True
        assert result["prim_path"] == "/World/Tray/bottle_03"
        # Prim path must be embedded into the introspection script (via repr()).
        assert "/World/Tray/bottle_03" in captured["code"]
        assert "GetPrimAtPath" in captured["code"]
        assert "Semantics" in captured["code"]
        assert "GetAll" in captured["code"]
        # The script must report has_semantics so empty results are not "errors".
        assert "has_semantics" in captured["code"]
        # The handler note must describe the response shape.
        assert "labels" in result["note"]
        assert "has_semantics" in result["note"]

    @pytest.mark.asyncio
    async def test_path_with_special_chars(self, mock_kit_rpc, monkeypatch):
        """Special chars in prim path must round-trip through repr() without breaking syntax."""
        async def fake_queue(code, desc):
            compile(code, "<get_semantic_label>", "exec")
            return {"queued": True, "patch_id": "ok"}
        import service.isaac_assist_service.chat.tools.kit_tools as kt
        monkeypatch.setattr(kt, "queue_exec_patch", fake_queue)

        handler = DATA_HANDLERS["list_variant_sets"]
        result = await handler({"prim_path": "/World/Asset's (v2)"})
        assert result["queued"] is True


@pytest.mark.skipif(
    "list_variants" not in DATA_HANDLERS,
    reason="Tier 9 (USD Layers & Variants) not merged on this branch",
)
class TestListVariants:
    """list_variants enumerates a single variant set on a prim."""

    @pytest.mark.asyncio
    async def test_list_variants_embeds_both_args(self, mock_kit_rpc, monkeypatch):
        captured = {}
        async def fake_queue(code, desc):
            captured["code"] = code
            captured["desc"] = desc
            return {"queued": True, "patch_id": "tier9_vlist_001"}
        import service.isaac_assist_service.chat.tools.kit_tools as kt
        monkeypatch.setattr(kt, "queue_exec_patch", fake_queue)

        handler = DATA_HANDLERS["list_variants"]
        result = await handler({"prim_path": "/World/Asset", "variant_set": "shadingVariant"})
        assert result["queued"] is True
        assert result["prim_path"] == "/World/Asset"
        assert result["variant_set"] == "shadingVariant"
        # Both arguments must reach the introspection script
        assert "/World/Asset" in captured["code"]
        assert "shadingVariant" in captured["code"]
        assert "GetVariantNames" in captured["code"]
        # The script must report 'available' when the requested set is missing,
        # so the LLM can hint at the closest match
        assert "available" in captured["code"]

    @pytest.mark.asyncio
    async def test_list_variants_generated_script_compiles(self, mock_kit_rpc, monkeypatch):
        async def fake_queue(code, desc):
            compile(code, "<list_variants>", "exec")
            return {"queued": True, "patch_id": "ok"}
        import service.isaac_assist_service.chat.tools.kit_tools as kt
        monkeypatch.setattr(kt, "queue_exec_patch", fake_queue)

        handler = DATA_HANDLERS["list_variants"]
        result = await handler({"prim_path": "/World/X", "variant_set": "lod"})
        assert result["queued"] is True


# ---------------------------------------------------------------------------
# Tier 10 — Animation & Timeline data handlers
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    "get_timeline_state" not in DATA_HANDLERS,
    reason="Tier 10 (Animation & Timeline) not merged on this branch",
)
class TestGetTimelineState:
    """get_timeline_state introspects omni.timeline + stage time-code metadata."""

    @pytest.mark.asyncio
    async def test_get_timeline_state_queues_introspection(self, mock_kit_rpc, monkeypatch):
        handler = DATA_HANDLERS["get_semantic_label"]
        result = await handler({"prim_path": "/World/Robot's (v2)/joint"})
        assert result["queued"] is True
        assert result["prim_path"] == "/World/Robot's (v2)/joint"


# ---------------------------------------------------------------------------
# Tier 11 — validate_semantic_labels
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    "validate_semantic_labels" not in DATA_HANDLERS,
    reason="Tier 11 (SDG Annotation) not merged on this branch",
)
class TestValidateSemanticLabels:
    """Lints every Semantics.SemanticsAPI annotation on the current stage."""

    @pytest.mark.asyncio
    async def test_queues_validation(self, mock_kit_rpc, monkeypatch):
        captured = {}
        async def fake_queue(code, desc):
            captured["code"] = code
            captured["desc"] = desc
            return {"queued": True, "patch_id": "tier11_validate_001"}
        import service.isaac_assist_service.chat.tools.kit_tools as kt
        monkeypatch.setattr(kt, "queue_exec_patch", fake_queue)

        handler = DATA_HANDLERS["validate_semantic_labels"]
        result = await handler({})
        assert result["queued"] is True
        assert result["patch_id"] == "tier11_validate_001"
        # Script must walk the stage and report each issue category.
        assert "stage.Traverse" in captured["code"]
        assert "Semantics" in captured["code"]
        # Issue categories the LLM relies on:
        assert "empty_class_name" in captured["code"]
        assert "singleton_class" in captured["code"]
        assert "conflicting_class_labels" in captured["code"]
        # Visibility / active checks because labels on hidden prims are dead weight:
        assert "invisible_labeled_prim" in captured["code"] or "inactive_labeled_prim" in captured["code"]
        # Output must report the documented schema.
        assert "summary" in captured["code"]
        assert "issues" in captured["code"]
        # Note must distinguish from PR #23 validate_annotations.
        assert "validate_annotations" in result["note"]
        assert "USD" in result["note"]

    @pytest.mark.asyncio
    async def test_script_compiles(self, mock_kit_rpc, monkeypatch):
        async def fake_queue(code, desc):
            compile(code, "<validate_semantic_labels>", "exec")
            return {"queued": True, "patch_id": "ok"}
        import service.isaac_assist_service.chat.tools.kit_tools as kt
        monkeypatch.setattr(kt, "queue_exec_patch", fake_queue)

        handler = DATA_HANDLERS["validate_semantic_labels"]
        result = await handler({})
        assert result["queued"] is True

    @pytest.mark.asyncio
    async def test_response_has_documented_keys(self, mock_kit_rpc, monkeypatch):
        async def fake_queue(code, desc):
            return {"queued": True, "patch_id": "ok"}
        import service.isaac_assist_service.chat.tools.kit_tools as kt
        monkeypatch.setattr(kt, "queue_exec_patch", fake_queue)

        handler = DATA_HANDLERS["validate_semantic_labels"]
        result = await handler({})
        # The handler's own return shape is the contract with the orchestrator.
        for k in ("queued", "patch_id", "note"):
            assert k in result, f"validate_semantic_labels response missing key: {k}"
# ── Tier 14.3: select_by_criteria ───────────────────────────────────────────

class TestSelectByCriteria:
    """select_by_criteria runs Kit-side via queue_exec_patch.

    The handler returns the queued status + the generated query code; the
    actual matches are produced when the patch executes inside Kit. Tests
    validate:
      1. The handler is registered.
      2. The generated query code is syntactically valid Python.
      3. Each criterion key produces the expected USD/regex code fragment.
      4. The handler returns a well-formed dict via the Kit RPC mock.
    """

    def test_handler_registered(self):
        if "select_by_criteria" not in DATA_HANDLERS:
            pytest.skip("select_by_criteria not registered (different branch)")
        assert DATA_HANDLERS["select_by_criteria"] is not None

    def test_query_code_compiles_minimal(self):
        from service.isaac_assist_service.chat.tools.tool_executor import (
            _build_select_by_criteria_code,
        )
        code = _build_select_by_criteria_code({"type": "Mesh"})
        compile(code, "<select_by_criteria>", "exec")
        assert "stage.Traverse()" in code
        assert "GetTypeName() != _type" in code

    def test_query_code_uses_subtree_when_parent_given(self):
        from service.isaac_assist_service.chat.tools.tool_executor import (
            _build_select_by_criteria_code,
        )
        code = _build_select_by_criteria_code({
            "type": "Mesh",
            "parent": "/World/Robot",
        })
        compile(code, "<select_by_criteria>", "exec")
        assert "Usd.PrimRange(_root)" in code
        assert "/World/Robot" in code

    def test_query_code_handles_all_criteria_keys(self):
        """All documented criteria keys must produce code fragments."""
        from service.isaac_assist_service.chat.tools.tool_executor import (
            _build_select_by_criteria_code,
        )
        code = _build_select_by_criteria_code({
            "type": "Mesh",
            "has_schema": "PhysicsRigidBodyAPI",
            "name_pattern": "^cam_",
            "path_pattern": "/World/Robot",
            "has_attribute": "physics:mass",
            "kind": "component",
            "parent": "/World",
            "active": True,
        })
        compile(code, "<select_by_criteria>", "exec")
        # Every criterion key should produce a corresponding gate
        assert "GetTypeName()" in code
        assert "GetAppliedSchemas" in code
        assert "_name_re" in code
        assert "_path_re" in code
        assert "GetAttribute(_has_attr)" in code
        assert "GetKind()" in code
        assert "Usd.PrimRange(_root)" in code
        assert "IsActive()" in code

    @pytest.mark.asyncio
    async def test_handler_returns_queued_status(self, mock_kit_rpc):
        if "select_by_criteria" not in DATA_HANDLERS:
            pytest.skip("select_by_criteria not registered (different branch)")
        handler = DATA_HANDLERS["select_by_criteria"]
        result = await handler({"criteria": {"type": "Mesh"}})
        assert isinstance(result, dict)
        assert "queued" in result
        assert "criteria" in result
        assert result["criteria"] == {"type": "Mesh"}
        # query_code must round-trip through the response so the LLM/UI can
        # show the user what's about to run
        assert "query_code" in result
        compile(result["query_code"], "<roundtrip>", "exec")

    @pytest.mark.asyncio
    async def test_handler_rejects_non_dict_criteria(self, mock_kit_rpc):
        if "select_by_criteria" not in DATA_HANDLERS:
            pytest.skip("select_by_criteria not registered (different branch)")
        handler = DATA_HANDLERS["select_by_criteria"]
        result = await handler({"criteria": "not a dict"})
        assert "error" in result
        assert result["count"] == 0
# ──────────────────────────────────────────────────────────────────────────────
# Tier 15-18 data handlers
# ──────────────────────────────────────────────────────────────────────────────

class TestTier15Tier18DataHandlers:
    """Each data handler should be registered and produce valid Kit code via mock_kit_rpc."""

    @pytest.mark.asyncio
    async def test_get_viewport_camera_registered(self):
        assert "get_viewport_camera" in DATA_HANDLERS
        assert DATA_HANDLERS["get_viewport_camera"] is not None

    @pytest.mark.asyncio
    async def test_get_viewport_camera_runs_with_mock(self, monkeypatch):
        captured = {}

        async def fake_queue(code, desc=""):
            captured["code"] = code
            captured["desc"] = desc
            return {"queued": True, "patch_id": "p1"}

        import service.isaac_assist_service.chat.tools.kit_tools as kt
        monkeypatch.setattr(kt, "queue_exec_patch", fake_queue)
        result = await DATA_HANDLERS["get_viewport_camera"]({})
        assert "code" in captured
        # Generated Kit-side code must be valid Python
        compile(captured["code"], "<gvc>", "exec")
        assert "get_active_viewport" in captured["code"]
        assert "camera_path" in captured["code"]

    @pytest.mark.asyncio
    async def test_get_selected_prims_registered(self):
        assert "get_selected_prims" in DATA_HANDLERS
        assert DATA_HANDLERS["get_selected_prims"] is not None

    @pytest.mark.asyncio
    async def test_get_selected_prims_runs_with_mock(self, monkeypatch):
        captured = {}

        async def fake_queue(code, desc=""):
            captured["code"] = code
            return {"queued": True, "patch_id": "p2"}

        import service.isaac_assist_service.chat.tools.kit_tools as kt
        monkeypatch.setattr(kt, "queue_exec_patch", fake_queue)
        result = await DATA_HANDLERS["get_selected_prims"]({})
        compile(captured["code"], "<gsp>", "exec")
        assert "get_selected_prim_paths" in captured["code"]

    @pytest.mark.asyncio
    async def test_list_opened_stages_registered(self):
        assert "list_opened_stages" in DATA_HANDLERS
        assert DATA_HANDLERS["list_opened_stages"] is not None

    @pytest.mark.asyncio
    async def test_list_opened_stages_runs_with_mock(self, monkeypatch):
        captured = {}

        async def fake_queue(code, desc=""):
            captured["code"] = code
            return {"queued": True, "patch_id": "p3"}

        import service.isaac_assist_service.chat.tools.kit_tools as kt
        monkeypatch.setattr(kt, "queue_exec_patch", fake_queue)
        result = await DATA_HANDLERS["list_opened_stages"]({})
        compile(captured["code"], "<los>", "exec")
        assert "get_context_names" in captured["code"]
        assert "stage_url" in captured["code"]

    @pytest.mark.asyncio
    async def test_list_extensions_registered(self):
        assert "list_extensions" in DATA_HANDLERS
        assert DATA_HANDLERS["list_extensions"] is not None

    @pytest.mark.asyncio
    async def test_list_extensions_runs_with_mock(self, monkeypatch):
        captured = {}

        async def fake_queue(code, desc=""):
            captured["code"] = code
            return {"queued": True, "patch_id": "p4"}

        import service.isaac_assist_service.chat.tools.kit_tools as kt
        monkeypatch.setattr(kt, "queue_exec_patch", fake_queue)
        result = await DATA_HANDLERS["list_extensions"]({"enabled_only": True, "name_filter": "isaac"})
        compile(captured["code"], "<le>", "exec")
        assert "get_extension_manager" in captured["code"]
        # Args propagated into the generated Kit-side code
        assert "True" in captured["code"]
        assert "isaac" in captured["code"]

    @pytest.mark.asyncio
    async def test_list_extensions_default_args(self, monkeypatch):
        captured = {}

        async def fake_queue(code, desc=""):
            captured["code"] = code
            return {"queued": True, "patch_id": "p5"}

        import service.isaac_assist_service.chat.tools.kit_tools as kt
        monkeypatch.setattr(kt, "queue_exec_patch", fake_queue)
        result = await DATA_HANDLERS["list_extensions"]({})
        compile(captured["code"], "<le_def>", "exec")
        # Default name filter is empty string, default enabled_only is False
        assert "False" in captured["code"]
