"""
L0 tests for the 7 Tier 4 — Geometry & Spatial Analysis atomic tools (see
docs/specs/atomic_tools_catalog.md, Tier 4).

Six handlers are DATA handlers that ship a print-json snippet to Kit through
queue_exec_patch; we stub queue_exec_patch so the test suite never needs a
running Kit RPC server. One handler is a CODE_GEN handler
(compute_convex_hull) and is exercised through CODE_GEN_HANDLERS.
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
# Catalog of the 7 Tier 4 tools
# ---------------------------------------------------------------------------

TIER4_TOOLS = [
    "raycast",                # T4.1 DATA
    "overlap_sphere",         # T4.2 DATA
    "overlap_box",            # T4.3 DATA
    "sweep_sphere",           # T4.4 DATA
    "compute_volume",         # T4.5 DATA
    "compute_surface_area",   # T4.6 DATA
    "compute_convex_hull",    # T4.7 CODE_GEN
]

TIER4_DATA_HANDLERS = [
    "raycast",
    "overlap_sphere",
    "overlap_box",
    "sweep_sphere",
    "compute_volume",
    "compute_surface_area",
]

TIER4_CODE_GEN_HANDLERS = [
    "compute_convex_hull",
]


# ---------------------------------------------------------------------------
# Shared fixture — capture queue_exec_patch submissions
# ---------------------------------------------------------------------------

@pytest.fixture()
def capture_kit_patches(monkeypatch):
    """Intercept kit_tools.queue_exec_patch and record the submitted code."""
    captured: list = []

    async def fake_queue(code, description=""):
        captured.append({"code": code, "description": description})
        return {"queued": True, "patch_id": "test_tier4"}

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

class TestTier4Coverage:
    """Every Tier 4 tool must be in the schema and have a handler registered."""

    def test_exactly_seven_tier4_tools(self):
        assert len(TIER4_TOOLS) == 7
        assert len(set(TIER4_TOOLS)) == 7

    def test_split_is_six_data_one_code_gen(self):
        assert len(TIER4_DATA_HANDLERS) == 6
        assert len(TIER4_CODE_GEN_HANDLERS) == 1
        assert set(TIER4_DATA_HANDLERS) | set(TIER4_CODE_GEN_HANDLERS) == set(TIER4_TOOLS)
        assert set(TIER4_DATA_HANDLERS) & set(TIER4_CODE_GEN_HANDLERS) == set()

    @pytest.mark.parametrize("name", TIER4_TOOLS)
    def test_tool_in_schema(self, name):
        names = {t["function"]["name"] for t in ISAAC_SIM_TOOLS}
        assert name in names, f"Tier 4 tool '{name}' missing from ISAAC_SIM_TOOLS"

    @pytest.mark.parametrize("name", TIER4_TOOLS)
    def test_tool_has_handler(self, name):
        assert name in CODE_GEN_HANDLERS or name in DATA_HANDLERS, (
            f"Tier 4 tool '{name}' has no handler registered"
        )

    @pytest.mark.parametrize("name", TIER4_DATA_HANDLERS)
    def test_data_handler_registered(self, name):
        assert name in DATA_HANDLERS, f"{name} should be a DATA handler"
        assert name not in CODE_GEN_HANDLERS, f"{name} should NOT also be CODE_GEN"
        assert callable(DATA_HANDLERS[name]), f"{name} handler is not callable"

    @pytest.mark.parametrize("name", TIER4_CODE_GEN_HANDLERS)
    def test_code_gen_handler_registered(self, name):
        assert name in CODE_GEN_HANDLERS, f"{name} should be a CODE_GEN handler"
        assert name not in DATA_HANDLERS, f"{name} should NOT also be DATA"
        assert callable(CODE_GEN_HANDLERS[name]), f"{name} handler is not callable"

    @pytest.mark.parametrize("name", TIER4_TOOLS)
    def test_schema_documents_required_params(self, name):
        for tool in ISAAC_SIM_TOOLS:
            if tool["function"]["name"] != name:
                continue
            params = tool["function"]["parameters"]
            assert params["type"] == "object"
            assert "properties" in params
            assert "required" in params
            return
        pytest.fail(f"{name} not in schema list")


# ---------------------------------------------------------------------------
# Per-tool DATA handler tests — verify the snippet emitted to Kit
# ---------------------------------------------------------------------------

class TestRaycast:
    @pytest.mark.asyncio
    async def test_emits_raycast_call(self, capture_kit_patches):
        handler = DATA_HANDLERS["raycast"]
        result = await handler({
            "origin": [0.0, 0.0, 1.0],
            "direction": [0.0, 0.0, -1.0],
            "max_distance": 5.0,
        })
        assert result["queued"] is True
        assert len(capture_kit_patches) == 1
        code = capture_kit_patches[0]["code"]
        _assert_compiles(code, "raycast")
        assert "get_physx_scene_query_interface" in code
        assert "raycast_closest" in code
        # All three input arguments embedded
        assert "[0.0, 0.0, 1.0]" in code
        assert "[0.0, 0.0, -1.0]" in code
        assert "5.0" in code

    @pytest.mark.asyncio
    async def test_default_max_distance(self, capture_kit_patches):
        handler = DATA_HANDLERS["raycast"]
        await handler({"origin": [0.0, 0.0, 0.0], "direction": [1.0, 0.0, 0.0]})
        code = capture_kit_patches[0]["code"]
        _assert_compiles(code, "raycast")
        assert "1000.0" in code  # default max_distance

    @pytest.mark.asyncio
    async def test_zero_direction_rejected_inside_snippet(self, capture_kit_patches):
        handler = DATA_HANDLERS["raycast"]
        await handler({"origin": [0.0, 0.0, 0.0], "direction": [0.0, 0.0, 0.0]})
        code = capture_kit_patches[0]["code"]
        _assert_compiles(code, "raycast")
        # Snippet detects zero-length direction and emits an error
        assert "zero length" in code


class TestOverlapSphere:
    @pytest.mark.asyncio
    async def test_emits_overlap_sphere_call(self, capture_kit_patches):
        handler = DATA_HANDLERS["overlap_sphere"]
        result = await handler({"center": [1.0, 2.0, 3.0], "radius": 0.5})
        assert result["queued"] is True
        code = capture_kit_patches[0]["code"]
        _assert_compiles(code, "overlap_sphere")
        assert "overlap_sphere" in code
        assert "_report_fn" in code
        assert "[1.0, 2.0, 3.0]" in code
        assert "0.5" in code

    @pytest.mark.asyncio
    async def test_report_fn_collects_paths(self, capture_kit_patches):
        handler = DATA_HANDLERS["overlap_sphere"]
        await handler({"center": [0.0, 0.0, 0.0], "radius": 1.0})
        code = capture_kit_patches[0]["code"]
        # Make sure the callback returns True so PhysX keeps streaming hits
        assert "return True" in code
        assert "hits.append" in code


class TestOverlapBox:
    @pytest.mark.asyncio
    async def test_emits_overlap_box_call(self, capture_kit_patches):
        handler = DATA_HANDLERS["overlap_box"]
        result = await handler({
            "center": [0.0, 0.0, 0.0],
            "half_extents": [0.5, 0.5, 0.5],
            "rotation": [0.0, 0.0, 0.0, 1.0],
        })
        assert result["queued"] is True
        code = capture_kit_patches[0]["code"]
        _assert_compiles(code, "overlap_box")
        assert "overlap_box" in code
        assert "[0.5, 0.5, 0.5]" in code
        assert "[0.0, 0.0, 0.0, 1.0]" in code
        assert "_report_fn" in code

    @pytest.mark.asyncio
    async def test_default_rotation_is_identity(self, capture_kit_patches):
        handler = DATA_HANDLERS["overlap_box"]
        await handler({
            "center": [1.0, 1.0, 1.0],
            "half_extents": [1.0, 1.0, 1.0],
        })
        code = capture_kit_patches[0]["code"]
        _assert_compiles(code, "overlap_box")
        # Identity quaternion injected when caller omits rotation
        assert "[0.0, 0.0, 0.0, 1.0]" in code


class TestSweepSphere:
    @pytest.mark.asyncio
    async def test_emits_sweep_sphere_call(self, capture_kit_patches):
        handler = DATA_HANDLERS["sweep_sphere"]
        result = await handler({
            "start": [0.0, 0.0, 1.0],
            "end": [0.0, 0.0, 0.0],
            "radius": 0.1,
        })
        assert result["queued"] is True
        code = capture_kit_patches[0]["code"]
        _assert_compiles(code, "sweep_sphere")
        assert "sweep_sphere" in code
        assert "[0.0, 0.0, 1.0]" in code
        assert "[0.0, 0.0, 0.0]" in code
        assert "0.1" in code

    @pytest.mark.asyncio
    async def test_zero_length_sweep_short_circuits(self, capture_kit_patches):
        handler = DATA_HANDLERS["sweep_sphere"]
        await handler({
            "start": [1.0, 2.0, 3.0],
            "end": [1.0, 2.0, 3.0],
            "radius": 0.1,
        })
        code = capture_kit_patches[0]["code"]
        _assert_compiles(code, "sweep_sphere")
        assert "zero length" in code


class TestComputeVolume:
    @pytest.mark.asyncio
    async def test_emits_volume_walk(self, capture_kit_patches):
        handler = DATA_HANDLERS["compute_volume"]
        result = await handler({"prim_path": "/World/MyMesh"})
        assert result["queued"] is True
        code = capture_kit_patches[0]["code"]
        _assert_compiles(code, "compute_volume")
        assert "'/World/MyMesh'" in code
        assert "UsdGeom.Mesh" in code
        assert "GetFaceVertexCountsAttr" in code
        assert "GetFaceVertexIndicesAttr" in code
        # Both backends should be available
        assert "trimesh" in code
        assert "manual_tetrahedra" in code

    @pytest.mark.asyncio
    async def test_uses_world_transform(self, capture_kit_patches):
        handler = DATA_HANDLERS["compute_volume"]
        await handler({"prim_path": "/World/Cube"})
        code = capture_kit_patches[0]["code"]
        assert "ComputeLocalToWorldTransform" in code


class TestComputeSurfaceArea:
    @pytest.mark.asyncio
    async def test_emits_area_sum(self, capture_kit_patches):
        handler = DATA_HANDLERS["compute_surface_area"]
        result = await handler({"prim_path": "/World/MyMesh"})
        assert result["queued"] is True
        code = capture_kit_patches[0]["code"]
        _assert_compiles(code, "compute_surface_area")
        assert "'/World/MyMesh'" in code
        assert "UsdGeom.Mesh" in code
        # Cross product magnitude / 2 = triangle area
        assert "math.sqrt" in code
        assert "0.5" in code
        assert "surface_area" in code

    @pytest.mark.asyncio
    async def test_triangulates_polygons(self, capture_kit_patches):
        handler = DATA_HANDLERS["compute_surface_area"]
        await handler({"prim_path": "/World/Quad"})
        code = capture_kit_patches[0]["code"]
        # Fan triangulation marker
        assert "triangles" in code
        assert "face[0]" in code
        assert "face[k]" in code


# ---------------------------------------------------------------------------
# CODE_GEN handler tests
# ---------------------------------------------------------------------------

class TestComputeConvexHull:
    def test_basic_application(self):
        gen = CODE_GEN_HANDLERS["compute_convex_hull"]
        code = gen({"prim_path": "/World/MyMesh"})
        _assert_compiles(code, "compute_convex_hull")
        assert "'/World/MyMesh'" in code
        assert "UsdPhysics.MeshCollisionAPI" in code
        assert "convexHull" in code
        # No export path → hull mesh authoring branch is conditional
        assert "if export_hull_path:" in code

    def test_with_export_hull(self):
        gen = CODE_GEN_HANDLERS["compute_convex_hull"]
        code = gen({
            "prim_path": "/World/MyMesh",
            "export_hull_path": "/World/MyMeshHull",
        })
        _assert_compiles(code, "compute_convex_hull")
        assert "/World/MyMeshHull" in code
        assert "DefinePrim" in code
        # scipy ConvexHull is the preferred backend
        assert "ConvexHull" in code
        # AABB fallback marker (for environments without scipy)
        assert "Manual fallback" in code

    def test_path_with_special_chars(self):
        gen = CODE_GEN_HANDLERS["compute_convex_hull"]
        code = gen({"prim_path": "/World/My Robot (v2)/MyMesh"})
        _assert_compiles(code, "compute_convex_hull")
        assert "/World/My Robot (v2)/MyMesh" in code


# ---------------------------------------------------------------------------
# Round-trip dispatch through execute_tool_call
# ---------------------------------------------------------------------------

class TestExecuteToolCallTier4:
    """Ensure each Tier 4 tool round-trips cleanly through execute_tool_call."""

    @pytest.mark.asyncio
    async def test_raycast_returns_data_envelope(self, capture_kit_patches):
        from service.isaac_assist_service.chat.tools.tool_executor import execute_tool_call
        result = await execute_tool_call(
            "raycast",
            {"origin": [0.0, 0.0, 0.0], "direction": [1.0, 0.0, 0.0]},
        )
        assert result["type"] == "data"
        assert result.get("queued") is True

    @pytest.mark.asyncio
    async def test_overlap_sphere_round_trip(self, capture_kit_patches):
        from service.isaac_assist_service.chat.tools.tool_executor import execute_tool_call
        result = await execute_tool_call(
            "overlap_sphere",
            {"center": [0.0, 0.0, 0.0], "radius": 1.0},
        )
        assert result["type"] == "data"
        assert result.get("queued") is True

    @pytest.mark.asyncio
    async def test_overlap_box_round_trip(self, capture_kit_patches):
        from service.isaac_assist_service.chat.tools.tool_executor import execute_tool_call
        result = await execute_tool_call(
            "overlap_box",
            {"center": [0.0, 0.0, 0.0], "half_extents": [1.0, 1.0, 1.0]},
        )
        assert result["type"] == "data"
        assert result.get("queued") is True

    @pytest.mark.asyncio
    async def test_sweep_sphere_round_trip(self, capture_kit_patches):
        from service.isaac_assist_service.chat.tools.tool_executor import execute_tool_call
        result = await execute_tool_call(
            "sweep_sphere",
            {"start": [0.0, 0.0, 0.0], "end": [1.0, 0.0, 0.0], "radius": 0.1},
        )
        assert result["type"] == "data"
        assert result.get("queued") is True

    @pytest.mark.asyncio
    async def test_compute_volume_round_trip(self, capture_kit_patches):
        from service.isaac_assist_service.chat.tools.tool_executor import execute_tool_call
        result = await execute_tool_call(
            "compute_volume",
            {"prim_path": "/World/Cube"},
        )
        assert result["type"] == "data"
        assert result.get("queued") is True

    @pytest.mark.asyncio
    async def test_compute_surface_area_round_trip(self, capture_kit_patches):
        from service.isaac_assist_service.chat.tools.tool_executor import execute_tool_call
        result = await execute_tool_call(
            "compute_surface_area",
            {"prim_path": "/World/Cube"},
        )
        assert result["type"] == "data"
        assert result.get("queued") is True

    @pytest.mark.asyncio
    async def test_compute_convex_hull_round_trip(self, monkeypatch):
        from service.isaac_assist_service.chat.tools import tool_executor as te

        async def fake_queue(code, description=""):
            return {"queued": True, "patch_id": "tier4_convex"}

        monkeypatch.setattr(te.kit_tools, "queue_exec_patch", fake_queue)
        result = await te.execute_tool_call(
            "compute_convex_hull",
            {"prim_path": "/World/MyMesh", "export_hull_path": "/World/MyMeshHull"},
        )
        assert result["type"] == "code_patch"
        assert "code" in result
        assert "convexHull" in result["code"]
        assert "/World/MyMeshHull" in result["code"]
