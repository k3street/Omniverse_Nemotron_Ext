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
