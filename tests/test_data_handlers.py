"""
L0 tests for tier-11 SDG-annotation DATA handlers.

The handlers all queue an introspection script via kit_tools.queue_exec_patch.
We patch that function so we can capture the script and verify:
  1. The expected USD / Semantics API calls are present
  2. The script compiles as valid Python
  3. The handler returns the documented response shape

Skipif guards keep the file runnable on every other tier branch.
"""
import pytest

pytestmark = pytest.mark.l0

from service.isaac_assist_service.chat.tools.tool_executor import DATA_HANDLERS


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
            return {"queued": True, "patch_id": "tier11_classes_001"}
        import service.isaac_assist_service.chat.tools.kit_tools as kt
        monkeypatch.setattr(kt, "queue_exec_patch", fake_queue)

        handler = DATA_HANDLERS["list_semantic_classes"]
        result = await handler({})
        assert result["queued"] is True
        assert result["patch_id"] == "tier11_classes_001"
        # The introspection script must walk the stage and read SemanticsAPI.
        assert "stage.Traverse" in captured["code"]
        assert "Semantics" in captured["code"]
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
