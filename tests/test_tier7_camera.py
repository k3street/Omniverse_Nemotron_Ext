"""
L0 tests for Tier 7 (Camera) atomic tools.

Covers all 5 tools in the catalog:
  T7.1 list_cameras           — DATA, exec_sync wrapped
  T7.2 get_camera_params      — DATA, exec_sync wrapped
  T7.3 set_camera_params      — CODE_GEN
  T7.4 capture_camera_image   — DATA, exec_sync wrapped
  T7.5 set_camera_look_at     — CODE_GEN

DATA handlers are exercised by patching kit_tools.exec_sync to return a
canned response, so no running Isaac Sim instance is required.
"""
import json
import pytest

pytestmark = pytest.mark.l0

from service.isaac_assist_service.chat.tools.tool_executor import (
    CODE_GEN_HANDLERS,
    DATA_HANDLERS,
    _parse_last_json_line,
)
from service.isaac_assist_service.chat.tools.tool_schemas import ISAAC_SIM_TOOLS


# ---------------------------------------------------------------------------
# Schema sanity — all 5 tier-7 tools must be declared with rich descriptions
# ---------------------------------------------------------------------------

_TIER7_NAMES = [
    "list_cameras",
    "get_camera_params",
    "set_camera_params",
    "capture_camera_image",
    "set_camera_look_at",
]


def _tool_by_name(name: str) -> dict:
    for t in ISAAC_SIM_TOOLS:
        if t["function"]["name"] == name:
            return t
    raise AssertionError(f"Tier-7 tool '{name}' not registered in ISAAC_SIM_TOOLS")


class TestTier7SchemaPresence:
    @pytest.mark.parametrize("name", _TIER7_NAMES)
    def test_tool_registered(self, name):
        tool = _tool_by_name(name)
        assert tool["type"] == "function"
        assert tool["function"]["name"] == name

    @pytest.mark.parametrize("name", _TIER7_NAMES)
    def test_description_is_rich(self, name):
        """Per spec, every Tier-7 tool description must be substantive (>= 120 chars
        and explicitly mention units OR returns/limitations)."""
        desc = _tool_by_name(name)["function"]["description"]
        assert len(desc) >= 120, f"{name} description too short: {len(desc)} chars"
        # rich descriptions mention either Returns:, Use for:, or units
        assert any(token in desc for token in ("Returns:", "Use for:", "NOTE:", "mm", "scene units"))

    def test_handler_routing(self):
        """Tier-7 tools are routed to the right handler dict."""
        assert "list_cameras" in DATA_HANDLERS
        assert "get_camera_params" in DATA_HANDLERS
        assert "capture_camera_image" in DATA_HANDLERS
        assert "set_camera_params" in CODE_GEN_HANDLERS
        assert "set_camera_look_at" in CODE_GEN_HANDLERS


# ---------------------------------------------------------------------------
# Helper: patch kit_tools.exec_sync to return a canned response
# ---------------------------------------------------------------------------

@pytest.fixture()
def mock_exec_sync(monkeypatch):
    """Patch kit_tools.exec_sync; tests set `responses['output']` per call."""
    state = {"output": "{}", "success": True, "calls": []}

    async def fake_exec_sync(code, timeout=30):
        state["calls"].append({"code": code, "timeout": timeout})
        return {"success": state["success"], "output": state["output"]}

    import service.isaac_assist_service.chat.tools.kit_tools as kt
    monkeypatch.setattr(kt, "exec_sync", fake_exec_sync)
    return state


# ---------------------------------------------------------------------------
# T7.1 — list_cameras
# ---------------------------------------------------------------------------

class TestListCameras:
    @pytest.mark.asyncio
    async def test_list_cameras_parses_kit_output(self, mock_exec_sync):
        mock_exec_sync["output"] = json.dumps({
            "cameras": [
                {"path": "/World/CamA", "name": "CamA", "projection": "perspective",
                 "purpose": "default", "kind": ""},
                {"path": "/World/CamB", "name": "CamB", "projection": "orthographic",
                 "purpose": "render", "kind": ""},
            ],
            "count": 2,
        })
        handler = DATA_HANDLERS["list_cameras"]
        result = await handler({})
        assert result["count"] == 2
        assert {c["path"] for c in result["cameras"]} == {"/World/CamA", "/World/CamB"}

    @pytest.mark.asyncio
    async def test_list_cameras_empty_stage(self, mock_exec_sync):
        mock_exec_sync["output"] = json.dumps({"cameras": [], "count": 0})
        result = await DATA_HANDLERS["list_cameras"]({})
        assert result["count"] == 0
        assert result["cameras"] == []

    @pytest.mark.asyncio
    async def test_list_cameras_kit_failure(self, mock_exec_sync):
        mock_exec_sync["success"] = False
        mock_exec_sync["output"] = "Kit RPC down"
        result = await DATA_HANDLERS["list_cameras"]({})
        assert "error" in result

    @pytest.mark.asyncio
    async def test_list_cameras_unparseable_output(self, mock_exec_sync):
        mock_exec_sync["output"] = "this is not json"
        result = await DATA_HANDLERS["list_cameras"]({})
        assert "error" in result
        assert "raw_output" in result


# ---------------------------------------------------------------------------
# T7.2 — get_camera_params
# ---------------------------------------------------------------------------

class TestGetCameraParams:
    @pytest.mark.asyncio
    async def test_get_camera_params_returns_full_dict(self, mock_exec_sync):
        mock_exec_sync["output"] = json.dumps({
            "camera_path": "/World/Camera",
            "projection": "perspective",
            "focal_length_mm": 35.0,
            "horizontal_aperture_mm": 20.955,
            "vertical_aperture_mm": 15.2908,
            "horizontal_fov_deg": 33.4,
            "vertical_fov_deg": 24.5,
            "clipping_range_m": [0.1, 1000.0],
            "focus_distance_m": 2.5,
            "f_stop": 2.8,
        })
        result = await DATA_HANDLERS["get_camera_params"]({"camera_path": "/World/Camera"})
        assert result["focal_length_mm"] == 35.0
        assert result["clipping_range_m"] == [0.1, 1000.0]
        assert "horizontal_fov_deg" in result

    @pytest.mark.asyncio
    async def test_get_camera_params_missing_path(self, mock_exec_sync):
        result = await DATA_HANDLERS["get_camera_params"]({})
        assert "error" in result
        # No Kit call should be made when validation fails
        assert mock_exec_sync["calls"] == []

    @pytest.mark.asyncio
    async def test_get_camera_params_rejects_bad_path(self, mock_exec_sync):
        result = await DATA_HANDLERS["get_camera_params"](
            {"camera_path": "/World/Cam; rm -rf /"}
        )
        assert "error" in result
        assert mock_exec_sync["calls"] == []

    @pytest.mark.asyncio
    async def test_get_camera_params_camera_not_found(self, mock_exec_sync):
        mock_exec_sync["output"] = json.dumps(
            {"error": "Camera prim not found", "camera_path": "/World/Missing"}
        )
        result = await DATA_HANDLERS["get_camera_params"]({"camera_path": "/World/Missing"})
        assert "error" in result


# ---------------------------------------------------------------------------
# T7.4 — capture_camera_image
# ---------------------------------------------------------------------------

class TestCaptureCameraImage:
    @pytest.mark.asyncio
    async def test_capture_returns_base64(self, mock_exec_sync):
        mock_exec_sync["output"] = json.dumps({
            "camera_path": "/World/Camera",
            "resolution": [640, 480],
            "image_base64": "iVBOR...",
            "format": "png",
            "message": "Rendered 1 frame from /World/Camera at 640x480",
        })
        result = await DATA_HANDLERS["capture_camera_image"]({
            "camera_path": "/World/Camera",
            "resolution": [640, 480],
        })
        assert result["format"] == "png"
        assert result["image_base64"] == "iVBOR..."
        assert result["resolution"] == [640, 480]

    @pytest.mark.asyncio
    async def test_capture_default_resolution_used(self, mock_exec_sync):
        mock_exec_sync["output"] = json.dumps({
            "camera_path": "/World/Camera",
            "resolution": [1280, 720],
            "image_base64": "iVBOR...",
            "format": "png",
            "message": "ok",
        })
        await DATA_HANDLERS["capture_camera_image"]({"camera_path": "/World/Camera"})
        # The injected snippet must reflect the default resolution
        sent_code = mock_exec_sync["calls"][0]["code"]
        assert "(1280, 720)" in sent_code

    @pytest.mark.asyncio
    async def test_capture_rejects_bad_resolution(self, mock_exec_sync):
        result = await DATA_HANDLERS["capture_camera_image"]({
            "camera_path": "/World/Camera",
            "resolution": [-1, 200],
        })
        assert "error" in result
        assert mock_exec_sync["calls"] == []

    @pytest.mark.asyncio
    async def test_capture_rejects_missing_path(self, mock_exec_sync):
        result = await DATA_HANDLERS["capture_camera_image"]({})
        assert "error" in result


# ---------------------------------------------------------------------------
# Helper coverage
# ---------------------------------------------------------------------------

class TestParseLastJsonLine:
    def test_parses_last_object(self):
        out = "noise line\n{\"a\": 1}\n{\"b\": 2}"
        parsed = _parse_last_json_line(out)
        assert parsed == {"b": 2}

    def test_returns_none_when_no_json(self):
        assert _parse_last_json_line("just some text\nmore text") is None

    def test_skips_invalid_json_and_picks_next_valid(self):
        out = "{not json}\n{\"valid\": true}"
        parsed = _parse_last_json_line(out)
        assert parsed == {"valid": True}
