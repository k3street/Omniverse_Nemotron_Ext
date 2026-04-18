"""
L0 tests for the 10 Tier 1 — USD Core atomic tools (see
docs/specs/atomic_tools_catalog.md).

Nine handlers are DATA handlers that ship a print-json snippet to Kit through
queue_exec_patch; we stub queue_exec_patch so the test suite never needs a
running Kit RPC server. The lone CODE_GEN handler (set_prim_metadata) is
exercised through CODE_GEN_HANDLERS like the Tier 0 generators.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.l0

from service.isaac_assist_service.chat.tools.tool_executor import (
    CODE_GEN_HANDLERS,
    DATA_HANDLERS,
)
from service.isaac_assist_service.chat.tools.tool_schemas import ISAAC_SIM_TOOLS


# ---------------------------------------------------------------------------
# Catalog of the 10 Tier 1 tools
# ---------------------------------------------------------------------------

TIER1_TOOLS = [
    "list_attributes",
    "list_relationships",
    "list_applied_schemas",
    "get_prim_metadata",
    "set_prim_metadata",
    "get_prim_type",
    "find_prims_by_schema",
    "find_prims_by_name",
    "get_kind",
    "get_active_state",
]

TIER1_DATA_HANDLERS = [t for t in TIER1_TOOLS if t != "set_prim_metadata"]
TIER1_CODE_GEN_HANDLERS = ["set_prim_metadata"]


# ---------------------------------------------------------------------------
# Shared fixture — capture queue_exec_patch submissions
# ---------------------------------------------------------------------------

@pytest.fixture()
def capture_kit_patches(monkeypatch):
    """Intercept kit_tools.queue_exec_patch and record the submitted code."""
    captured: list = []

    async def fake_queue(code, description=""):
        captured.append({"code": code, "description": description})
        return {"queued": True, "patch_id": "test_tier1"}

    import service.isaac_assist_service.chat.tools.kit_tools as kt
    monkeypatch.setattr(kt, "queue_exec_patch", fake_queue)
    return captured


def _assert_compiles(code: str, label: str) -> None:
    try:
        compile(code, f"<{label}>", "exec")
    except SyntaxError as exc:
        pytest.fail(f"{label} produced invalid python:\n{exc}\n\nCode:\n{code}")


# ---------------------------------------------------------------------------
# Coverage / dispatch tests
# ---------------------------------------------------------------------------

class TestTier1Coverage:
    """Every Tier 1 tool must be in the schema and have a handler registered."""

    def test_exactly_ten_tier1_tools(self):
        assert len(TIER1_TOOLS) == 10
        assert len(set(TIER1_TOOLS)) == 10

    @pytest.mark.parametrize("name", TIER1_TOOLS)
    def test_tool_in_schema(self, name):
        names = {t["function"]["name"] for t in ISAAC_SIM_TOOLS}
        assert name in names, f"Tier 1 tool '{name}' missing from ISAAC_SIM_TOOLS"

    @pytest.mark.parametrize("name", TIER1_TOOLS)
    def test_tool_has_handler(self, name):
        assert name in CODE_GEN_HANDLERS or name in DATA_HANDLERS, (
            f"Tier 1 tool '{name}' has no handler registered"
        )

    @pytest.mark.parametrize("name", TIER1_DATA_HANDLERS)
    def test_data_handler_registered(self, name):
        assert name in DATA_HANDLERS, f"{name} should be a DATA handler"
        assert callable(DATA_HANDLERS[name]), f"{name} handler is not callable"

    def test_set_prim_metadata_is_code_gen(self):
        assert "set_prim_metadata" in CODE_GEN_HANDLERS
        assert "set_prim_metadata" not in DATA_HANDLERS


# ---------------------------------------------------------------------------
# Per-tool DATA handler tests — verify the snippet emitted to Kit
# ---------------------------------------------------------------------------

class TestListAttributes:
    @pytest.mark.asyncio
    async def test_emits_get_attributes_loop(self, capture_kit_patches):
        handler = DATA_HANDLERS["list_attributes"]
        result = await handler({"prim_path": "/World/Cube"})
        assert result["queued"] is True
        assert len(capture_kit_patches) == 1
        code = capture_kit_patches[0]["code"]
        _assert_compiles(code, "list_attributes")
        assert "'/World/Cube'" in code
        assert "GetAttributes" in code
        assert "json.dumps" in code

    @pytest.mark.asyncio
    async def test_path_with_special_chars(self, capture_kit_patches):
        handler = DATA_HANDLERS["list_attributes"]
        await handler({"prim_path": "/World/My Robot (v2)"})
        code = capture_kit_patches[0]["code"]
        _assert_compiles(code, "list_attributes")
        assert "/World/My Robot (v2)" in code


class TestListRelationships:
    @pytest.mark.asyncio
    async def test_emits_get_relationships_loop(self, capture_kit_patches):
        handler = DATA_HANDLERS["list_relationships"]
        await handler({"prim_path": "/World/Cube"})
        code = capture_kit_patches[0]["code"]
        _assert_compiles(code, "list_relationships")
        assert "'/World/Cube'" in code
        assert "GetRelationships" in code
        assert "GetTargets" in code


class TestListAppliedSchemas:
    @pytest.mark.asyncio
    async def test_emits_get_applied_schemas(self, capture_kit_patches):
        handler = DATA_HANDLERS["list_applied_schemas"]
        await handler({"prim_path": "/World/Robot"})
        code = capture_kit_patches[0]["code"]
        _assert_compiles(code, "list_applied_schemas")
        assert "'/World/Robot'" in code
        assert "GetAppliedSchemas" in code
        assert "applied_schemas" in code


class TestGetPrimMetadata:
    @pytest.mark.asyncio
    async def test_emits_get_metadata(self, capture_kit_patches):
        handler = DATA_HANDLERS["get_prim_metadata"]
        await handler({"prim_path": "/World/Asset", "key": "kind"})
        code = capture_kit_patches[0]["code"]
        _assert_compiles(code, "get_prim_metadata")
        assert "'/World/Asset'" in code
        assert "'kind'" in code
        assert "GetMetadata" in code
        assert "HasMetadata" in code

    @pytest.mark.asyncio
    async def test_arbitrary_key(self, capture_kit_patches):
        handler = DATA_HANDLERS["get_prim_metadata"]
        await handler({"prim_path": "/World/X", "key": "documentation"})
        code = capture_kit_patches[0]["code"]
        assert "'documentation'" in code


class TestSetPrimMetadata:
    """The lone CODE_GEN handler in Tier 1."""

    def test_generates_valid_python_string_value(self):
        gen = CODE_GEN_HANDLERS["set_prim_metadata"]
        code = gen({"prim_path": "/World/Cube", "key": "kind", "value": "component"})
        _assert_compiles(code, "set_prim_metadata")
        assert "'/World/Cube'" in code
        assert "'kind'" in code
        assert "'component'" in code
        assert "SetMetadata" in code

    def test_generates_valid_python_bool_value(self):
        gen = CODE_GEN_HANDLERS["set_prim_metadata"]
        code = gen({"prim_path": "/World/X", "key": "hidden", "value": True})
        _assert_compiles(code, "set_prim_metadata")
        assert "True" in code
        assert "'hidden'" in code

    def test_generates_valid_python_dict_value(self):
        gen = CODE_GEN_HANDLERS["set_prim_metadata"]
        code = gen({
            "prim_path": "/World/X",
            "key": "customData",
            "value": {"author": "test", "version": 1},
        })
        _assert_compiles(code, "set_prim_metadata")
        assert "customData" in code
        # Either order — repr(dict) ordering preserved in 3.7+
        assert "'author'" in code
        assert "'test'" in code

    def test_validates_prim_exists(self):
        """Generated code should error if prim is missing."""
        gen = CODE_GEN_HANDLERS["set_prim_metadata"]
        code = gen({"prim_path": "/Missing", "key": "kind", "value": "group"})
        assert "RuntimeError" in code
        assert "IsValid" in code


class TestGetPrimType:
    @pytest.mark.asyncio
    async def test_emits_get_type_name(self, capture_kit_patches):
        handler = DATA_HANDLERS["get_prim_type"]
        await handler({"prim_path": "/World/Cube"})
        code = capture_kit_patches[0]["code"]
        _assert_compiles(code, "get_prim_type")
        assert "'/World/Cube'" in code
        assert "GetTypeName" in code


class TestFindPrimsBySchema:
    @pytest.mark.asyncio
    async def test_default_root_and_limit(self, capture_kit_patches):
        handler = DATA_HANDLERS["find_prims_by_schema"]
        await handler({"schema_name": "PhysicsRigidBodyAPI"})
        code = capture_kit_patches[0]["code"]
        _assert_compiles(code, "find_prims_by_schema")
        assert "'PhysicsRigidBodyAPI'" in code
        assert "HasAPI" in code
        assert "Usd.PrimRange" in code
        # Default limit
        assert "limit = 500" in code

    @pytest.mark.asyncio
    async def test_custom_root_and_limit(self, capture_kit_patches):
        handler = DATA_HANDLERS["find_prims_by_schema"]
        await handler({
            "schema_name": "PhysicsArticulationRootAPI",
            "root_path": "/World/Robots",
            "limit": 10,
        })
        code = capture_kit_patches[0]["code"]
        _assert_compiles(code, "find_prims_by_schema")
        assert "'/World/Robots'" in code
        assert "limit = 10" in code

    @pytest.mark.asyncio
    async def test_unknown_schema_handled(self, capture_kit_patches):
        """Unknown schemas should be reported, not crash."""
        handler = DATA_HANDLERS["find_prims_by_schema"]
        await handler({"schema_name": "NoSuchSchemaXYZ"})
        code = capture_kit_patches[0]["code"]
        _assert_compiles(code, "find_prims_by_schema")
        assert "unknown schema" in code


class TestFindPrimsByName:
    @pytest.mark.asyncio
    async def test_emits_regex_search(self, capture_kit_patches):
        handler = DATA_HANDLERS["find_prims_by_name"]
        await handler({"pattern": ".*panda_link.*"})
        code = capture_kit_patches[0]["code"]
        _assert_compiles(code, "find_prims_by_name")
        assert ".*panda_link.*" in code
        assert "re.compile" in code
        assert "rx.search" in code
        assert "limit = 500" in code

    @pytest.mark.asyncio
    async def test_invalid_regex_handled(self, capture_kit_patches):
        """An invalid regex should be reported by the snippet, not crash."""
        handler = DATA_HANDLERS["find_prims_by_name"]
        await handler({"pattern": "([unbalanced"})
        code = capture_kit_patches[0]["code"]
        _assert_compiles(code, "find_prims_by_name")
        assert "invalid regex" in code

    @pytest.mark.asyncio
    async def test_custom_root_and_limit(self, capture_kit_patches):
        handler = DATA_HANDLERS["find_prims_by_name"]
        await handler({
            "pattern": "Cube",
            "root_path": "/World/Props",
            "limit": 25,
        })
        code = capture_kit_patches[0]["code"]
        _assert_compiles(code, "find_prims_by_name")
        assert "'/World/Props'" in code
        assert "limit = 25" in code


class TestGetKind:
    @pytest.mark.asyncio
    async def test_emits_model_api_get_kind(self, capture_kit_patches):
        handler = DATA_HANDLERS["get_kind"]
        await handler({"prim_path": "/World/Asset"})
        code = capture_kit_patches[0]["code"]
        _assert_compiles(code, "get_kind")
        assert "'/World/Asset'" in code
        assert "Usd.ModelAPI" in code
        assert "GetKind" in code
        # Classification helpers
        assert "is_a_component" in code
        assert "is_a_assembly" in code


class TestGetActiveState:
    @pytest.mark.asyncio
    async def test_emits_is_active(self, capture_kit_patches):
        handler = DATA_HANDLERS["get_active_state"]
        await handler({"prim_path": "/World/Cube"})
        code = capture_kit_patches[0]["code"]
        _assert_compiles(code, "get_active_state")
        assert "'/World/Cube'" in code
        assert "IsActive" in code
        assert "is_active" in code


# ---------------------------------------------------------------------------
# Integration through execute_tool_call — verifies dispatch routes the right way
# ---------------------------------------------------------------------------

class TestTier1Dispatch:
    """Make sure execute_tool_call routes Tier 1 tools to the correct handler."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("name", TIER1_DATA_HANDLERS)
    async def test_data_dispatch(self, capture_kit_patches, name):
        from service.isaac_assist_service.chat.tools.tool_executor import execute_tool_call
        # Minimal valid args per tool
        args = {
            "list_attributes": {"prim_path": "/W/X"},
            "list_relationships": {"prim_path": "/W/X"},
            "list_applied_schemas": {"prim_path": "/W/X"},
            "get_prim_metadata": {"prim_path": "/W/X", "key": "kind"},
            "get_prim_type": {"prim_path": "/W/X"},
            "find_prims_by_schema": {"schema_name": "PhysicsRigidBodyAPI"},
            "find_prims_by_name": {"pattern": "Cube"},
            "get_kind": {"prim_path": "/W/X"},
            "get_active_state": {"prim_path": "/W/X"},
        }[name]
        result = await execute_tool_call(name, args)
        assert result["type"] == "data"

    @pytest.mark.asyncio
    async def test_set_prim_metadata_dispatch_is_code_patch(self, mock_kit_rpc):
        from service.isaac_assist_service.chat.tools.tool_executor import execute_tool_call
        result = await execute_tool_call(
            "set_prim_metadata",
            {"prim_path": "/W/X", "key": "kind", "value": "component"},
        )
        assert result["type"] == "code_patch"
        _assert_compiles(result["code"], "set_prim_metadata-dispatch")
