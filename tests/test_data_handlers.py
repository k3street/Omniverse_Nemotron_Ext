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
