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
