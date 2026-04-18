"""
L0 tests for the Enterprise Scale addendum
(docs/specs/addendum_enterprise_scale.md).

Covers:
    E.1  scene_summary / list_all_prims now accept prim_scope
    E.2  build_stage_index / query_stage_index
    E.3  save_delta_snapshot / restore_delta_snapshot
    E.4  batch_delete_prims / batch_set_attributes
    E.5  queue_write_locked_patch
    E.6  activate_area

Every test runs without Kit, without a live LLM, and without touching the
repo filesystem outside tmp_path.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

pytestmark = pytest.mark.l0


from service.isaac_assist_service.chat.tools import tool_executor as te
from service.isaac_assist_service.chat.tools.tool_executor import (
    CODE_GEN_HANDLERS,
    DATA_HANDLERS,
    _gen_activate_area,
    _gen_batch_delete_prims,
    _gen_batch_set_attributes,
    _gen_build_stage_index,
    _gen_restore_delta_snapshot,
    _gen_save_delta_snapshot,
    _handle_build_stage_index,
    _handle_list_all_prims,
    _handle_query_stage_index,
    _handle_restore_delta_snapshot,
    _handle_save_delta_snapshot,
    _handle_scene_summary,
    _handle_queue_write_locked_patch,
)
from service.isaac_assist_service.chat.tools.tool_schemas import ISAAC_SIM_TOOLS


_ADDENDUM_TOOLS = {
    "build_stage_index",
    "query_stage_index",
    "save_delta_snapshot",
    "restore_delta_snapshot",
    "batch_delete_prims",
    "batch_set_attributes",
    "activate_area",
    "queue_write_locked_patch",
}


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _tool_by_name(name: str):
    for t in ISAAC_SIM_TOOLS:
        if t["function"]["name"] == name:
            return t
    raise AssertionError(f"Tool '{name}' not declared in ISAAC_SIM_TOOLS")


@pytest.fixture()
def fake_queue_exec(monkeypatch):
    """Replace kit_tools.queue_exec_patch with a recorder; returns the recorder."""
    calls = []

    async def _fake(code, desc):
        calls.append({"code": code, "description": desc})
        return {"queued": True, "patch_id": f"test_{len(calls)}"}

    monkeypatch.setattr(te.kit_tools, "queue_exec_patch", _fake)
    return calls


@pytest.fixture(autouse=True)
def _reset_stage_index():
    """The stage index is module-level — keep tests isolated."""
    te._STAGE_INDEX.clear()
    te._STAGE_INDEX_META.update({"prim_scope": None, "prim_count": 0})
    yield
    te._STAGE_INDEX.clear()
    te._STAGE_INDEX_META.update({"prim_scope": None, "prim_count": 0})


# ---------------------------------------------------------------------------
# Schema registration sanity
# ---------------------------------------------------------------------------

class TestAddendumSchemas:
    @pytest.mark.parametrize("name", sorted(_ADDENDUM_TOOLS))
    def test_tool_is_declared(self, name):
        tool = _tool_by_name(name)
        fn = tool["function"]
        assert fn["description"]
        assert fn["parameters"]["type"] == "object"

    @pytest.mark.parametrize("name", sorted(_ADDENDUM_TOOLS))
    def test_tool_has_handler(self, name):
        assert name in CODE_GEN_HANDLERS or name in DATA_HANDLERS, (
            f"Addendum tool '{name}' has no handler registered"
        )

    def test_build_stage_index_optional_scope(self):
        params = _tool_by_name("build_stage_index")["function"]["parameters"]
        # Default scope is handled in the handler — neither param required.
        assert params.get("required", []) == []
        assert "prim_scope" in params["properties"]
        assert "max_prims" in params["properties"]

    def test_query_stage_index_requires_keywords(self):
        params = _tool_by_name("query_stage_index")["function"]["parameters"]
        assert params.get("required", []) == ["keywords"]

    def test_batch_delete_requires_prim_paths(self):
        params = _tool_by_name("batch_delete_prims")["function"]["parameters"]
        assert params.get("required", []) == ["prim_paths"]

    def test_batch_set_attributes_shape(self):
        params = _tool_by_name("batch_set_attributes")["function"]["parameters"]
        change_schema = params["properties"]["changes"]
        assert change_schema["type"] == "array"
        item = change_schema["items"]
        assert set(item["required"]) == {"prim_path", "attr_name", "value"}

    def test_activate_area_requires_scope(self):
        params = _tool_by_name("activate_area")["function"]["parameters"]
        assert params.get("required", []) == ["prim_scope"]


# ---------------------------------------------------------------------------
# E.1  prim_scope on scene_summary / list_all_prims
# ---------------------------------------------------------------------------

class TestE1ScopedTraversal:
    def test_scene_summary_schema_has_prim_scope(self):
        params = _tool_by_name("scene_summary")["function"]["parameters"]
        assert "prim_scope" in params["properties"]

    def test_list_all_prims_schema_has_prim_scope(self):
        params = _tool_by_name("list_all_prims")["function"]["parameters"]
        assert "prim_scope" in params["properties"]

    @pytest.mark.asyncio
    async def test_scene_summary_default_scope_is_world(self, monkeypatch):
        async def _fake_ctx(full=False):
            return {"stage": {"prim_count": 2}, "recent_logs": []}

        monkeypatch.setattr(te.kit_tools, "get_stage_context", _fake_ctx)
        monkeypatch.setattr(te.kit_tools, "format_stage_context_for_llm", lambda ctx: "summary")
        result = await _handle_scene_summary({})
        assert result["prim_scope"] == "/World"

    @pytest.mark.asyncio
    async def test_scene_summary_preserves_explicit_scope(self, monkeypatch):
        async def _fake_ctx(full=False):
            return {"stage": {"prim_count": 2}, "recent_logs": []}

        monkeypatch.setattr(te.kit_tools, "get_stage_context", _fake_ctx)
        monkeypatch.setattr(te.kit_tools, "format_stage_context_for_llm", lambda ctx: "summary")
        result = await _handle_scene_summary({"prim_scope": "/World/Cell_A"})
        assert result["prim_scope"] == "/World/Cell_A"

    @pytest.mark.asyncio
    async def test_list_all_prims_filters_by_scope(self, monkeypatch):
        async def _fake_ctx(full=True):
            return {
                "stage": {
                    "prim_count": 4,
                    "prims": [
                        {"path": "/World/Cell_A/Robot", "type": "Xform"},
                        {"path": "/World/Cell_A/Robot/Arm", "type": "Xform"},
                        {"path": "/World/Cell_B/Robot", "type": "Xform"},
                        {"path": "/Environments/Warehouse", "type": "Xform"},
                    ],
                }
            }

        monkeypatch.setattr(te.kit_tools, "get_stage_context", _fake_ctx)
        result = await _handle_list_all_prims({"prim_scope": "/World/Cell_A"})
        paths = [p["path"] for p in result["prims"]]
        assert paths == ["/World/Cell_A/Robot", "/World/Cell_A/Robot/Arm"]
        assert result["prim_scope"] == "/World/Cell_A"

    @pytest.mark.asyncio
    async def test_list_all_prims_under_path_is_legacy_alias(self, monkeypatch):
        async def _fake_ctx(full=True):
            return {
                "stage": {
                    "prim_count": 2,
                    "prims": [
                        {"path": "/World/A/x", "type": "Xform"},
                        {"path": "/World/B/x", "type": "Xform"},
                    ],
                }
            }

        monkeypatch.setattr(te.kit_tools, "get_stage_context", _fake_ctx)
        result = await _handle_list_all_prims({"under_path": "/World/B"})
        assert [p["path"] for p in result["prims"]] == ["/World/B/x"]

    @pytest.mark.asyncio
    async def test_list_all_prims_also_applies_filter_type(self, monkeypatch):
        async def _fake_ctx(full=True):
            return {
                "stage": {
                    "prim_count": 3,
                    "prims": [
                        {"path": "/World/Cell/Robot", "type": "Xform"},
                        {"path": "/World/Cell/Cam", "type": "Camera"},
                        {"path": "/World/Cell/Light", "type": "DistantLight"},
                    ],
                }
            }

        monkeypatch.setattr(te.kit_tools, "get_stage_context", _fake_ctx)
        result = await _handle_list_all_prims(
            {"prim_scope": "/World/Cell", "filter_type": "Camera"}
        )
        assert [p["path"] for p in result["prims"]] == ["/World/Cell/Cam"]


# ---------------------------------------------------------------------------
# E.2  StageIndex
# ---------------------------------------------------------------------------

class TestE2StageIndex:
    def test_build_code_uses_prim_range_not_traverse_all(self):
        code = _gen_build_stage_index({"prim_scope": "/World/Cell", "max_prims": 500})
        assert "Usd.PrimRange" in code
        assert "TraverseAll" not in code
        assert "'/World/Cell'" in code
        assert "500" in code
        compile(code, "build_stage_index", "exec")

    def test_build_code_defaults_scope_to_world(self):
        code = _gen_build_stage_index({})
        assert "'/World'" in code
        assert "50000" in code

    @pytest.mark.asyncio
    async def test_build_handler_queues_patch(self, fake_queue_exec):
        result = await _handle_build_stage_index({"prim_scope": "/World/Cell_X"})
        assert result["prim_scope"] == "/World/Cell_X"
        assert result["queued"] is True
        assert len(fake_queue_exec) == 1
        assert "/World/Cell_X" in fake_queue_exec[0]["code"]

    @pytest.mark.asyncio
    async def test_query_empty_index_is_graceful(self):
        result = await _handle_query_stage_index({"keywords": ["robot"]})
        assert result["results"] == []
        assert "empty" in result.get("note", "").lower()

    @pytest.mark.asyncio
    async def test_query_scores_keyword_hits(self):
        te._STAGE_INDEX.update({
            "/World/Franka": {"type": "Xform", "schemas": ["PhysicsRigidBodyAPI"], "has_physics": True},
            "/World/Cube": {"type": "Cube", "schemas": [], "has_physics": False},
            "/World/Franka/Arm": {"type": "Xform", "schemas": [], "has_physics": False},
        })
        result = await _handle_query_stage_index({"keywords": ["franka"], "max_results": 10})
        paths = [r["path"] for r in result["results"]]
        assert "/World/Franka" in paths
        assert "/World/Franka/Arm" in paths
        assert "/World/Cube" not in paths
        assert result["match_count"] == 2

    @pytest.mark.asyncio
    async def test_query_includes_neighbours_of_selected_prim(self):
        te._STAGE_INDEX.update({
            "/World": {"type": "Xform", "schemas": [], "has_physics": False},
            "/World/Cell_A": {"type": "Xform", "schemas": [], "has_physics": False},
            "/World/Cell_A/Robot": {"type": "Xform", "schemas": [], "has_physics": False},
            "/World/Cell_B": {"type": "Xform", "schemas": [], "has_physics": False},
            "/World/Physics/Scene": {"type": "PhysicsScene", "schemas": [], "has_physics": True},
        })
        result = await _handle_query_stage_index({
            "keywords": ["physicsscene"],
            "selected_prim": "/World/Cell_A",
        })
        paths = [r["path"] for r in result["results"]]
        # The keyword hit must be included AND the neighbours of Cell_A.
        assert "/World/Physics/Scene" in paths
        assert "/World/Cell_B" in paths  # sibling
        assert "/World" in paths          # parent
        assert "/World/Cell_A/Robot" in paths  # child

    @pytest.mark.asyncio
    async def test_query_respects_max_results(self):
        for i in range(150):
            te._STAGE_INDEX[f"/World/Robot_{i}"] = {"type": "Xform", "schemas": [], "has_physics": False}
        result = await _handle_query_stage_index({"keywords": ["robot"], "max_results": 50})
        assert len(result["results"]) <= 50
        assert result["total_indexed"] == 150

    @pytest.mark.asyncio
    async def test_query_accepts_string_keywords(self):
        te._STAGE_INDEX["/World/Franka"] = {"type": "Xform", "schemas": [], "has_physics": False}
        # Bare string instead of a list — should be coerced.
        result = await _handle_query_stage_index({"keywords": "franka"})
        assert any(r["path"] == "/World/Franka" for r in result["results"])


# ---------------------------------------------------------------------------
# E.3  Delta snapshots
# ---------------------------------------------------------------------------

class TestE3DeltaSnapshots:
    def test_save_code_uses_dirty_layers(self):
        code = _gen_save_delta_snapshot("snap_1", None)
        assert "get_dirty_layers" in code
        assert "ExportToString" in code
        assert "'snap_1'" in code
        compile(code, "save_delta_snapshot", "exec")

    def test_restore_code_uses_import_from_string(self):
        code = _gen_restore_delta_snapshot("snap_1", {"/tmp/a.usd": "#usda 1.0\n"})
        assert "ImportFromString" in code
        compile(code, "restore_delta_snapshot", "exec")

    @pytest.mark.asyncio
    async def test_save_handler_writes_manifest(self, tmp_path, monkeypatch, fake_queue_exec):
        monkeypatch.setattr(te, "_DELTA_ROOT", tmp_path / "deltas")
        result = await _handle_save_delta_snapshot(
            {"snapshot_id": "snap_42", "base_snapshot_id": "full_1"}
        )
        manifest_path = Path(result["manifest_path"])
        assert manifest_path.exists()
        manifest = json.loads(manifest_path.read_text())
        assert manifest["snapshot_id"] == "snap_42"
        assert manifest["base_snapshot_id"] == "full_1"
        assert result["queued"] is True
        # The generated code for Kit must reference the snapshot id.
        assert fake_queue_exec and "'snap_42'" in fake_queue_exec[0]["code"]

    @pytest.mark.asyncio
    async def test_restore_missing_manifest_returns_error(self, tmp_path, monkeypatch):
        monkeypatch.setattr(te, "_DELTA_ROOT", tmp_path / "deltas")
        result = await _handle_restore_delta_snapshot({"snapshot_id": "nope"})
        assert result["restored"] is False
        assert "error" in result

    @pytest.mark.asyncio
    async def test_restore_replays_saved_deltas(self, tmp_path, monkeypatch, fake_queue_exec):
        root = tmp_path / "deltas"
        root.mkdir(parents=True)
        manifest = {
            "snapshot_id": "snap_99",
            "base_snapshot_id": "full_7",
            "deltas": {"/foo/a.usd": "#usda 1.0\n"},
        }
        (root / "snap_99.json").write_text(json.dumps(manifest))
        monkeypatch.setattr(te, "_DELTA_ROOT", root)
        result = await _handle_restore_delta_snapshot({"snapshot_id": "snap_99"})
        assert result["snapshot_id"] == "snap_99"
        assert result["layer_count"] == 1
        assert result["queued"] is True
        assert fake_queue_exec and "ImportFromString" in fake_queue_exec[0]["code"]


# ---------------------------------------------------------------------------
# E.4  Batch operations
# ---------------------------------------------------------------------------

class TestE4BatchOperations:
    def test_batch_delete_uses_batch_namespace_edit(self):
        code = _gen_batch_delete_prims({"prim_paths": ["/World/a", "/World/b", "/World/c"]})
        assert "Sdf.BatchNamespaceEdit" in code
        assert "/World/a" in code and "/World/c" in code
        compile(code, "batch_delete_prims", "exec")

    def test_batch_delete_empty_input_is_noop(self):
        code = _gen_batch_delete_prims({"prim_paths": []})
        assert "no paths" in code.lower()
        compile(code, "batch_delete_noop", "exec")

    def test_batch_set_attributes_uses_change_block(self):
        changes = [
            {"prim_path": "/World/a", "attr_name": "visibility", "value": "invisible"},
            {"prim_path": "/World/b", "attr_name": "radius", "value": 0.5},
        ]
        code = _gen_batch_set_attributes({"changes": changes})
        assert "Sdf.ChangeBlock" in code
        # The payload must be embedded as JSON literal so Kit re-hydrates it.
        assert '"visibility"' in code and '"radius"' in code
        compile(code, "batch_set_attributes", "exec")

    def test_batch_set_attributes_empty(self):
        code = _gen_batch_set_attributes({"changes": []})
        assert "no changes" in code.lower()
        compile(code, "batch_set_attributes_noop", "exec")


# ---------------------------------------------------------------------------
# E.5  Write lock queue
# ---------------------------------------------------------------------------

class TestE5WriteLockQueue:
    @pytest.mark.asyncio
    async def test_queue_write_locked_patch_blocks_empty_code(self, fake_queue_exec):
        result = await _handle_queue_write_locked_patch(
            {"code": "", "description": "noop"}
        )
        assert result.get("type") == "error"
        # Empty code must NOT reach Kit.
        assert fake_queue_exec == []

    @pytest.mark.asyncio
    async def test_queue_write_locked_patch_serializes(self, fake_queue_exec):
        # Two patches submitted back-to-back — both must reach Kit via the queue
        # and report a non-error queued dict.
        code = "import omni.usd\nstage = omni.usd.get_context().get_stage()"
        r1 = await _handle_queue_write_locked_patch(
            {"code": code, "description": "patch-1", "priority": 0}
        )
        r2 = await _handle_queue_write_locked_patch(
            {"code": code, "description": "patch-2", "priority": 5}
        )
        assert r1["queued"] is True and r2["queued"] is True
        assert r2["priority"] == 5
        assert len(fake_queue_exec) == 2
        # Queue drains after each patch completes.
        assert te._WRITE_LOCK_QUEUE.pending() == 0

    @pytest.mark.asyncio
    async def test_queue_write_locked_patch_validates_dangerous_code(self, fake_queue_exec):
        # Use a patch that the validator should flag. We patch the validator
        # to force a blocking issue so we don't depend on the real rule set.
        import service.isaac_assist_service.chat.tools.tool_executor as te_mod

        def _fake_validate(code):
            return [{"severity": "error", "message": "test block"}]

        def _fake_blocking(issues):
            return True

        def _fake_format(issues):
            return "blocked by test"

        orig_validate = te_mod.validate_patch
        orig_blocking = te_mod.has_blocking_issues
        orig_format = te_mod.format_issues_for_llm
        te_mod.validate_patch = _fake_validate
        te_mod.has_blocking_issues = _fake_blocking
        te_mod.format_issues_for_llm = _fake_format
        try:
            result = await _handle_queue_write_locked_patch(
                {"code": "print('x')", "description": "bad"}
            )
        finally:
            te_mod.validate_patch = orig_validate
            te_mod.has_blocking_issues = orig_blocking
            te_mod.format_issues_for_llm = orig_format
        assert result.get("validation_blocked") is True
        assert fake_queue_exec == []


# ---------------------------------------------------------------------------
# E.6  Area activation
# ---------------------------------------------------------------------------

class TestE6ActivateArea:
    def test_generated_code_keeps_scope_active(self):
        code = _gen_activate_area({"prim_scope": "/World/Cell_A"})
        assert "'/World/Cell_A'" in code
        assert "SetActive(True)" in code
        assert "SetActive(False)" in code
        compile(code, "activate_area", "exec")

    def test_sibling_only_flag_propagates(self):
        code_true = _gen_activate_area(
            {"prim_scope": "/World/Cell_A", "deactivate_siblings_only": True}
        )
        code_false = _gen_activate_area(
            {"prim_scope": "/World/Cell_A", "deactivate_siblings_only": False}
        )
        assert "sibling_only = True" in code_true
        assert "sibling_only = False" in code_false

    def test_activate_area_registered_as_code_gen(self):
        assert "activate_area" in CODE_GEN_HANDLERS
        # activate_area must NOT be a data handler (it needs approval).
        assert "activate_area" not in DATA_HANDLERS


# ---------------------------------------------------------------------------
# Dispatcher integration
# ---------------------------------------------------------------------------

class TestAddendumDispatch:
    @pytest.mark.asyncio
    async def test_execute_tool_call_routes_batch_delete(self, fake_queue_exec, monkeypatch):
        # Bypass the patch validator — the generated code is already safe.
        monkeypatch.setattr(te, "validate_patch", lambda code: [])
        monkeypatch.setattr(te, "has_blocking_issues", lambda issues: False)
        result = await te.execute_tool_call(
            "batch_delete_prims", {"prim_paths": ["/World/a", "/World/b"]}
        )
        assert result["type"] == "code_patch"
        assert "Sdf.BatchNamespaceEdit" in result["code"]

    @pytest.mark.asyncio
    async def test_execute_tool_call_routes_query_stage_index(self, fake_queue_exec):
        te._STAGE_INDEX["/World/Foo"] = {"type": "Xform", "schemas": [], "has_physics": False}
        result = await te.execute_tool_call(
            "query_stage_index", {"keywords": ["foo"]}
        )
        assert result["type"] == "data"
        assert result["match_count"] == 1
