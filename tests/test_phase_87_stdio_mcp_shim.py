"""Phase 87 — Stdio MCP shim hardening: comprehensive unit tests.

Tests cover:
  - metadata (phase, status)
  - MCP_ERROR_CODES completeness
  - parse_envelope: valid request, valid response, error response
  - parse_envelope error paths: malformed JSON, missing jsonrpc, wrong jsonrpc version
  - begin_handshake → state "handshaking"
  - complete_handshake success → state "ready"
  - complete_handshake with error response → state "error"
  - serialize round-trip
  - is_size_within_limit: small (True) and huge (False)
  - make_error_response shape
  - close → "closed", reset → "uninitialized"
"""
from __future__ import annotations

import json

import pytest

pytestmark = pytest.mark.l0


# ---------------------------------------------------------------------------
# Import helpers
# ---------------------------------------------------------------------------

def _shim_module():
    from service.isaac_assist_service.multimodal import stdio_mcp_shim
    return stdio_mcp_shim


def _fresh_shim(**kwargs):
    mod = _shim_module()
    return mod.StdioMCPShim(**kwargs)


# ---------------------------------------------------------------------------
# Test 1 — metadata: phase landed
# ---------------------------------------------------------------------------

def test_phase_87_metadata():
    mod = _shim_module()
    md = mod.get_phase_metadata()
    assert md["phase"] == 87
    assert md["status"] == "landed"


# ---------------------------------------------------------------------------
# Test 2 — MCP_ERROR_CODES contains all 7 standard codes
# ---------------------------------------------------------------------------

def test_mcp_error_codes_complete():
    mod = _shim_module()
    codes = mod.MCP_ERROR_CODES
    expected_keys = {
        "parse_error",
        "invalid_request",
        "method_not_found",
        "invalid_params",
        "internal_error",
        "timeout",
        "payload_too_large",
    }
    assert expected_keys <= set(codes.keys()), (
        f"Missing keys: {expected_keys - set(codes.keys())}"
    )
    # Spot-check standard JSON-RPC values
    assert codes["parse_error"] == -32700
    assert codes["invalid_request"] == -32600
    assert codes["method_not_found"] == -32601
    assert codes["invalid_params"] == -32602
    assert codes["internal_error"] == -32603


# ---------------------------------------------------------------------------
# Test 3 — parse_envelope: valid request JSON → MCPRequestEnvelope
# ---------------------------------------------------------------------------

def test_parse_valid_request():
    mod = _shim_module()
    shim = _fresh_shim()
    raw = json.dumps({
        "jsonrpc": "2.0",
        "id": "req-1",
        "method": "tools/call",
        "params": {"name": "create_prim", "arguments": {}},
    })
    env = shim.parse_envelope(raw)
    assert isinstance(env, mod.MCPRequestEnvelope)
    assert env.id == "req-1"
    assert env.method == "tools/call"
    assert env.params["name"] == "create_prim"
    assert env.jsonrpc == "2.0"


# ---------------------------------------------------------------------------
# Test 4 — parse_envelope: valid response JSON with result → MCPResponseEnvelope
# ---------------------------------------------------------------------------

def test_parse_valid_response_with_result():
    mod = _shim_module()
    shim = _fresh_shim()
    raw = json.dumps({
        "jsonrpc": "2.0",
        "id": "req-1",
        "result": {"content": [{"type": "text", "text": "ok"}]},
    })
    env = shim.parse_envelope(raw)
    assert isinstance(env, mod.MCPResponseEnvelope)
    assert env.id == "req-1"
    assert env.result is not None
    assert env.error is None


# ---------------------------------------------------------------------------
# Test 5 — parse_envelope: error response → MCPResponseEnvelope with error
# ---------------------------------------------------------------------------

def test_parse_error_response():
    mod = _shim_module()
    shim = _fresh_shim()
    raw = json.dumps({
        "jsonrpc": "2.0",
        "id": "req-2",
        "error": {"code": -32601, "message": "Method not found"},
    })
    env = shim.parse_envelope(raw)
    assert isinstance(env, mod.MCPResponseEnvelope)
    assert env.error is not None
    assert env.error["code"] == -32601
    assert env.result is None


# ---------------------------------------------------------------------------
# Test 6 — parse_envelope: malformed JSON raises ValueError
# ---------------------------------------------------------------------------

def test_parse_malformed_json_raises():
    shim = _fresh_shim()
    with pytest.raises(ValueError, match="parse_error"):
        shim.parse_envelope("{not valid json")


# ---------------------------------------------------------------------------
# Test 7 — parse_envelope: missing 'jsonrpc' field raises ValueError
# ---------------------------------------------------------------------------

def test_parse_missing_jsonrpc_raises():
    shim = _fresh_shim()
    raw = json.dumps({"id": "1", "method": "foo", "params": {}})
    with pytest.raises(ValueError):
        shim.parse_envelope(raw)


# ---------------------------------------------------------------------------
# Test 8 — parse_envelope: jsonrpc != "2.0" raises ValueError
# ---------------------------------------------------------------------------

def test_parse_wrong_jsonrpc_version_raises():
    shim = _fresh_shim()
    raw = json.dumps({"jsonrpc": "1.0", "id": "1", "method": "foo", "params": {}})
    with pytest.raises(ValueError, match="2.0"):
        shim.parse_envelope(raw)


# ---------------------------------------------------------------------------
# Test 9 — begin_handshake transitions state to "handshaking"
# ---------------------------------------------------------------------------

def test_begin_handshake_state():
    mod = _shim_module()
    shim = _fresh_shim()
    assert shim.state == "uninitialized"
    req = shim.begin_handshake("2024-11-05")
    assert shim.state == "handshaking"
    assert isinstance(req, mod.MCPRequestEnvelope)
    assert req.method == "initialize"


# ---------------------------------------------------------------------------
# Test 10 — complete_handshake success → state "ready"
# ---------------------------------------------------------------------------

def test_complete_handshake_success():
    mod = _shim_module()
    shim = _fresh_shim()
    shim.begin_handshake("2024-11-05")
    success_response = mod.MCPResponseEnvelope(id="some-id", result={"capabilities": {}})
    shim.complete_handshake(success_response)
    assert shim.state == "ready"


# ---------------------------------------------------------------------------
# Test 11 — complete_handshake with error response → state "error"
# ---------------------------------------------------------------------------

def test_complete_handshake_error_response():
    mod = _shim_module()
    shim = _fresh_shim()
    shim.begin_handshake("2024-11-05")
    err_response = mod.MCPResponseEnvelope(
        id="some-id",
        error={"code": -32600, "message": "Invalid request"},
    )
    shim.complete_handshake(err_response)
    assert shim.state == "error"


# ---------------------------------------------------------------------------
# Test 12 — serialize round-trips an MCPRequestEnvelope
# ---------------------------------------------------------------------------

def test_serialize_request_round_trip():
    mod = _shim_module()
    shim = _fresh_shim()
    original = mod.MCPRequestEnvelope(
        id="abc",
        method="tools/list",
        params={"cursor": None},
    )
    serialized = shim.serialize(original)
    parsed = json.loads(serialized)
    assert parsed["jsonrpc"] == "2.0"
    assert parsed["id"] == "abc"
    assert parsed["method"] == "tools/list"
    assert parsed["params"] == {"cursor": None}


# ---------------------------------------------------------------------------
# Test 13 — serialize round-trips an MCPResponseEnvelope
# ---------------------------------------------------------------------------

def test_serialize_response_round_trip():
    mod = _shim_module()
    shim = _fresh_shim()
    original = mod.MCPResponseEnvelope(id="xyz", result={"tools": []})
    serialized = shim.serialize(original)
    parsed = json.loads(serialized)
    assert parsed["jsonrpc"] == "2.0"
    assert parsed["id"] == "xyz"
    assert parsed["result"] == {"tools": []}
    assert "error" not in parsed


# ---------------------------------------------------------------------------
# Test 14 — is_size_within_limit: True for small, False for huge
# ---------------------------------------------------------------------------

def test_is_size_within_limit_small():
    shim = _fresh_shim()
    assert shim.is_size_within_limit('{"jsonrpc":"2.0","id":"1","method":"ping","params":{}}')


def test_is_size_within_limit_huge():
    shim = _fresh_shim(max_message_bytes=10)
    assert not shim.is_size_within_limit("x" * 100)


# ---------------------------------------------------------------------------
# Test 15 — make_error_response returns well-shaped MCPResponseEnvelope
# ---------------------------------------------------------------------------

def test_make_error_response_shape():
    mod = _shim_module()
    shim = _fresh_shim()
    resp = shim.make_error_response("req-99", -32603, "Internal error")
    assert isinstance(resp, mod.MCPResponseEnvelope)
    assert resp.id == "req-99"
    assert resp.error is not None
    assert resp.error["code"] == -32603
    assert resp.error["message"] == "Internal error"
    assert resp.result is None


# ---------------------------------------------------------------------------
# Test 16 — close → "closed"; reset → "uninitialized"
# ---------------------------------------------------------------------------

def test_close_and_reset_lifecycle():
    shim = _fresh_shim()
    shim.begin_handshake("2024-11-05")
    assert shim.state == "handshaking"

    shim.close()
    assert shim.state == "closed"

    shim.reset()
    assert shim.state == "uninitialized"
