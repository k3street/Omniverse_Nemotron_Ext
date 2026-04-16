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
    _handle_check_collision_mesh,
    _handle_lookup_product_spec,
    _gen_check_collision_mesh_code,
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
