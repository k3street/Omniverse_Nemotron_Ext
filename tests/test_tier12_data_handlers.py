"""
L0 tests for tier-12 Asset Management DATA handlers.

The handlers all queue an introspection script via kit_tools.queue_exec_patch.
We patch that function so we can capture the script and verify:
  1. The expected USD / References / Payloads / assetInfo API calls are present
  2. The script compiles as valid Python
  3. The handler returns the documented response shape
"""
import pytest

pytestmark = pytest.mark.l0

from service.isaac_assist_service.chat.tools.tool_executor import DATA_HANDLERS


def _patch_queue(monkeypatch, captured: dict):
    async def fake_queue(code, desc):
        captured["code"] = code
        captured["desc"] = desc
        compile(code, "<tier12-data>", "exec")
        return {"queued": True, "patch_id": "tier12_data_001"}
    import service.isaac_assist_service.chat.tools.kit_tools as kt
    monkeypatch.setattr(kt, "queue_exec_patch", fake_queue)


# ---------------------------------------------------------------------------
# Tier 12 — list_references
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    "list_references" not in DATA_HANDLERS,
    reason="Tier 12 (Asset Management) not merged on this branch",
)
class TestListReferences:
    """Enumerates USD reference arcs composed onto a prim."""

    @pytest.mark.asyncio
    async def test_queues_and_compiles(self, monkeypatch):
        captured: dict = {}
        _patch_queue(monkeypatch, captured)
        handler = DATA_HANDLERS["list_references"]
        result = await handler({"prim_path": "/World/Robot"})

        assert result["queued"] is True
        assert result["patch_id"] == "tier12_data_001"
        assert result["prim_path"] == "/World/Robot"
        # Script must call the references API + composition query.
        code = captured["code"]
        assert "/World/Robot" in code
        assert "GetPrimAtPath" in code
        assert "GetReferences" in code
        assert "GetAllReferences" in code
        assert "PrimCompositionQuery" in code
        assert "json.dumps" in code
        # Note must describe the response shape so the LLM knows what to expect.
        for k in ("has_references", "references", "count", "list_payloads"):
            assert k in result["note"], f"note missing keyword '{k}'"

    @pytest.mark.asyncio
    async def test_path_with_special_chars(self, monkeypatch):
        """Special chars in prim path must round-trip through repr() without breaking syntax."""
        captured: dict = {}
        _patch_queue(monkeypatch, captured)
        handler = DATA_HANDLERS["list_references"]
        result = await handler({"prim_path": "/World/Robot's (v2)/joint"})
        assert result["queued"] is True
        assert result["prim_path"] == "/World/Robot's (v2)/joint"
        # The repr() round-trip must keep the path intact in the script.
        assert "Robot" in captured["code"]
        # Compile happened in the fake_queue helper.


# ---------------------------------------------------------------------------
# Tier 12 — list_payloads
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    "list_payloads" not in DATA_HANDLERS,
    reason="Tier 12 (Asset Management) not merged on this branch",
)
class TestListPayloads:
    """Enumerates USD payload arcs (deferred-load) on a prim."""

    @pytest.mark.asyncio
    async def test_queues_and_compiles(self, monkeypatch):
        captured: dict = {}
        _patch_queue(monkeypatch, captured)
        handler = DATA_HANDLERS["list_payloads"]
        result = await handler({"prim_path": "/World/Environment"})

        assert result["queued"] is True
        assert result["prim_path"] == "/World/Environment"
        code = captured["code"]
        assert "/World/Environment" in code
        assert "GetPayloads" in code
        assert "GetAllPayloads" in code
        assert "GetLoadSet" in code  # must check current load-set membership
        assert "is_loaded" in code
        assert "json.dumps" in code
        for k in ("has_payloads", "payloads", "is_loaded", "prim_is_loaded"):
            assert k in result["note"], f"note missing keyword '{k}'"

    @pytest.mark.asyncio
    async def test_response_shape_keys(self, monkeypatch):
        captured: dict = {}
        _patch_queue(monkeypatch, captured)
        handler = DATA_HANDLERS["list_payloads"]
        result = await handler({"prim_path": "/World/X"})
        for k in ("queued", "patch_id", "prim_path", "note"):
            assert k in result, f"list_payloads response missing '{k}'"


# ---------------------------------------------------------------------------
# Tier 12 — get_asset_info
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    "get_asset_info" not in DATA_HANDLERS,
    reason="Tier 12 (Asset Management) not merged on this branch",
)
class TestGetAssetInfo:
    """Reads assetInfo metadata + introducing layer + sha256 for a prim."""

    @pytest.mark.asyncio
    async def test_queues_and_compiles(self, monkeypatch):
        captured: dict = {}
        _patch_queue(monkeypatch, captured)
        handler = DATA_HANDLERS["get_asset_info"]
        result = await handler({"prim_path": "/World/Robot"})

        assert result["queued"] is True
        assert result["prim_path"] == "/World/Robot"
        code = captured["code"]
        # Must read assetInfo + walk the introducing layer + try sha256.
        assert "/World/Robot" in code
        assert "GetAssetInfo" in code
        assert "GetPrimStack" in code
        assert "hashlib" in code
        assert "sha256" in code
        # Must guard against hashing huge layers synchronously.
        assert "256 * 1024 * 1024" in code or "256*1024*1024" in code
        for k in ("has_asset_info", "asset_info", "introducing_layer", "sha256"):
            assert k in result["note"], f"note missing keyword '{k}'"

    @pytest.mark.asyncio
    async def test_handler_response_keys(self, monkeypatch):
        async def fake_queue(code, desc):
            return {"queued": True, "patch_id": "ok"}
        import service.isaac_assist_service.chat.tools.kit_tools as kt
        monkeypatch.setattr(kt, "queue_exec_patch", fake_queue)

        handler = DATA_HANDLERS["get_asset_info"]
        result = await handler({"prim_path": "/World/Y"})
        # Handler return shape is the contract with the orchestrator.
        for k in ("queued", "patch_id", "prim_path", "note"):
            assert k in result, f"get_asset_info response missing '{k}'"
