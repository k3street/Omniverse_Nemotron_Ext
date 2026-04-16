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
