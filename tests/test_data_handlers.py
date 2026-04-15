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
