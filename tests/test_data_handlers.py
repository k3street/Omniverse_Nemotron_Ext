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
        captured = {}
        async def fake_queue(code, desc):
            captured["code"] = code
            captured["desc"] = desc
            return {"queued": True, "patch_id": "tier10_timeline_001"}
        import service.isaac_assist_service.chat.tools.kit_tools as kt
        monkeypatch.setattr(kt, "queue_exec_patch", fake_queue)

        handler = DATA_HANDLERS["get_timeline_state"]
        result = await handler({})
        assert result["queued"] is True
        assert result["patch_id"] == "tier10_timeline_001"
        # Script must touch both timeline interface AND stage time-code metadata.
        assert "omni.timeline" in captured["code"]
        assert "GetTimeCodesPerSecond" in captured["code"]
        assert "GetStartTimeCode" in captured["code"]
        assert "GetEndTimeCode" in captured["code"]
        assert "json.dumps" in captured["code"]
        # Note must describe the response shape so the LLM knows what keys to expect.
        assert "current_time" in result["note"]
        assert "fps" in result["note"]
        assert "is_playing" in result["note"]

    @pytest.mark.asyncio
    async def test_get_timeline_state_script_compiles(self, mock_kit_rpc, monkeypatch):
        async def fake_queue(code, desc):
            compile(code, "<get_timeline_state>", "exec")
            return {"queued": True, "patch_id": "ok"}
        import service.isaac_assist_service.chat.tools.kit_tools as kt
        monkeypatch.setattr(kt, "queue_exec_patch", fake_queue)

        handler = DATA_HANDLERS["get_timeline_state"]
        result = await handler({})
        assert result["queued"] is True


@pytest.mark.skipif(
    "list_keyframes" not in DATA_HANDLERS,
    reason="Tier 10 (Animation & Timeline) not merged on this branch",
)
class TestListKeyframes:
    """list_keyframes enumerates TimeSamples on a single attribute."""

    @pytest.mark.asyncio
    async def test_list_keyframes_embeds_both_args(self, mock_kit_rpc, monkeypatch):
        captured = {}
        async def fake_queue(code, desc):
            captured["code"] = code
            captured["desc"] = desc
            return {"queued": True, "patch_id": "tier10_kf_001"}
        import service.isaac_assist_service.chat.tools.kit_tools as kt
        monkeypatch.setattr(kt, "queue_exec_patch", fake_queue)

        handler = DATA_HANDLERS["list_keyframes"]
        result = await handler({"prim_path": "/World/Cube", "attr": "xformOp:translate"})
        assert result["queued"] is True
        assert result["prim_path"] == "/World/Cube"
        assert result["attr"] == "xformOp:translate"
        # Both arguments must reach the introspection script.
        assert "/World/Cube" in captured["code"]
        assert "xformOp:translate" in captured["code"]
        assert "GetTimeSamples" in captured["code"]
        # Must report has_timesamples + time_range so empty results are not "errors".
        assert "has_timesamples" in captured["code"]
        assert "time_range_codes" in captured["code"]

    @pytest.mark.asyncio
    async def test_list_keyframes_path_with_special_chars(self, mock_kit_rpc, monkeypatch):
        """Special chars in prim path / attr must round-trip through repr() without breaking syntax."""
        async def fake_queue(code, desc):
            compile(code, "<list_keyframes>", "exec")
            return {"queued": True, "patch_id": "ok"}
        import service.isaac_assist_service.chat.tools.kit_tools as kt
        monkeypatch.setattr(kt, "queue_exec_patch", fake_queue)

        handler = DATA_HANDLERS["list_keyframes"]
        result = await handler({
            "prim_path": "/World/Robot's (v2)",
            "attr": "drive:angular:physics:targetPosition",
        })
        assert result["queued"] is True
        assert result["attr"] == "drive:angular:physics:targetPosition"
