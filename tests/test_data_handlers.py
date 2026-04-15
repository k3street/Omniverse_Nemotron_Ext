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
