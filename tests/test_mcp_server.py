"""
L2 tests for the MCP server — schema conversion, tool listing, dispatch.
"""
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

pytestmark = [pytest.mark.l2, pytest.mark.asyncio]


@pytest.fixture()
def mcp_server(monkeypatch):
    """Create an MCPServer instance with mocked execute_tool_call."""
    # Patch execute_tool_call before importing MCPServer
    monkeypatch.setattr(
        "service.isaac_assist_service.chat.tools.tool_executor.execute_tool_call",
        AsyncMock(return_value={"type": "data", "result": "mock_result"}),
    )
    # Patch SettingsManager to avoid touching .env
    from service.isaac_assist_service.settings.manager import SettingsManager
    monkeypatch.setattr(
        SettingsManager, "get_settings",
        MagicMock(return_value={"LLM_MODE": "local", "LOCAL_MODEL_NAME": "test"}),
    )
    monkeypatch.setattr(
        SettingsManager, "update_settings",
        MagicMock(return_value=True),
    )

    from service.isaac_assist_service.mcp_server import MCPServer
    return MCPServer()


class TestMCPSchemaConversion:

    def test_mcp_tools_have_required_fields(self, mcp_server):
        for tool in mcp_server._mcp_tools:
            assert "name" in tool, f"MCP tool missing 'name'"
            assert "description" in tool, f"Tool {tool.get('name')} missing 'description'"
            assert "inputSchema" in tool, f"Tool {tool.get('name')} missing 'inputSchema'"
            assert tool["inputSchema"]["type"] == "object"

    def test_mcp_includes_settings_tools(self, mcp_server):
        names = {t["name"] for t in mcp_server._mcp_tools}
        assert "get_settings" in names
        assert "update_settings" in names

    def test_mcp_tool_count_matches_openai_plus_settings(self, mcp_server):
        from service.isaac_assist_service.chat.tools.tool_schemas import ISAAC_SIM_TOOLS
        # OpenAI tools + get_settings + update_settings
        expected = len(ISAAC_SIM_TOOLS) + 2
        assert len(mcp_server._mcp_tools) == expected

    def test_no_duplicate_mcp_tool_names(self, mcp_server):
        names = [t["name"] for t in mcp_server._mcp_tools]
        assert len(names) == len(set(names))


class TestMCPHandleRequest:

    async def test_initialize(self, mcp_server):
        resp = await mcp_server.handle_request({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {},
        })
        assert resp["id"] == 1
        result = resp["result"]
        assert result["protocolVersion"] == "2024-11-05"
        assert "tools" in result["capabilities"]
        assert result["serverInfo"]["name"] == "isaac-assist"

    async def test_tools_list(self, mcp_server):
        resp = await mcp_server.handle_request({
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
            "params": {},
        })
        result = resp["result"]
        assert "tools" in result
        assert len(result["tools"]) > 0

    async def test_ping(self, mcp_server):
        resp = await mcp_server.handle_request({
            "jsonrpc": "2.0",
            "id": 3,
            "method": "ping",
            "params": {},
        })
        assert resp["result"] == {}

    async def test_resources_list_empty(self, mcp_server):
        resp = await mcp_server.handle_request({
            "jsonrpc": "2.0",
            "id": 4,
            "method": "resources/list",
            "params": {},
        })
        assert resp["result"]["resources"] == []

    async def test_prompts_list_empty(self, mcp_server):
        resp = await mcp_server.handle_request({
            "jsonrpc": "2.0",
            "id": 5,
            "method": "prompts/list",
            "params": {},
        })
        assert resp["result"]["prompts"] == []

    async def test_unknown_method_returns_error(self, mcp_server):
        resp = await mcp_server.handle_request({
            "jsonrpc": "2.0",
            "id": 6,
            "method": "nonexistent/method",
            "params": {},
        })
        assert "error" in resp
        assert resp["error"]["code"] == -32601

    async def test_get_settings(self, mcp_server):
        resp = await mcp_server.handle_request({
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": {"name": "get_settings", "arguments": {}},
        })
        result = resp["result"]
        assert result["isError"] is False
        assert len(result["content"]) > 0

    async def test_update_settings(self, mcp_server):
        resp = await mcp_server.handle_request({
            "jsonrpc": "2.0",
            "id": 8,
            "method": "tools/call",
            "params": {
                "name": "update_settings",
                "arguments": {"settings": {"AUTO_APPROVE": "true"}},
            },
        })
        result = resp["result"]
        assert result["isError"] is False

    async def test_unknown_tool_returns_error(self, mcp_server):
        resp = await mcp_server.handle_request({
            "jsonrpc": "2.0",
            "id": 9,
            "method": "tools/call",
            "params": {"name": "nonexistent_tool", "arguments": {}},
        })
        result = resp["result"]
        assert result["isError"] is True
        assert "Unknown tool" in result["content"][0]["text"]


class TestMCPResponseFormat:

    async def test_success_response_format(self, mcp_server):
        resp = await mcp_server.handle_request({
            "jsonrpc": "2.0",
            "id": 10,
            "method": "ping",
            "params": {},
        })
        assert resp["jsonrpc"] == "2.0"
        assert resp["id"] == 10
        assert "result" in resp

    async def test_error_response_format(self, mcp_server):
        resp = await mcp_server.handle_request({
            "jsonrpc": "2.0",
            "id": 11,
            "method": "bad",
            "params": {},
        })
        assert resp["jsonrpc"] == "2.0"
        assert resp["id"] == 11
        assert "error" in resp
        assert "code" in resp["error"]
        assert "message" in resp["error"]
