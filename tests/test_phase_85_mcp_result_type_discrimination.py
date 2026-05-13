"""Phase 85 — MCPResult type discrimination tests.

Tests cover:
- Encode→decode round-trip for every variant
- Bad discriminator raises ValidationError
- Missing required field raises ValidationError
- JSON serialization round-trip
- dict round-trip via encode/decode
- Metadata status is "landed"
"""
import json
import pytest
from pydantic import ValidationError

pytestmark = pytest.mark.l0


# ---------------------------------------------------------------------------
# Import helpers
# ---------------------------------------------------------------------------

from service.isaac_assist_service.multimodal.mcp_result_type_discrimination import (
    MCPDataResult,
    MCPCodePatchResult,
    MCPErrorResult,
    MCPStreamChunkResult,
    MCPStreamEndResult,
    encode,
    decode,
    get_phase_metadata,
)


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------

def test_phase_85_metadata():
    md = get_phase_metadata()
    assert md["phase"] == 85
    assert md["status"] == "landed"
    assert md["title"] == "MCPResult type discrimination"


# ---------------------------------------------------------------------------
# Round-trip: MCPDataResult
# ---------------------------------------------------------------------------

def test_data_result_round_trip():
    original = MCPDataResult(data={"key": "value", "count": 42})
    payload = encode(original)
    restored = decode(payload)
    assert isinstance(restored, MCPDataResult)
    assert restored.kind == "data"
    assert restored.data == {"key": "value", "count": 42}


# ---------------------------------------------------------------------------
# Round-trip: MCPCodePatchResult
# ---------------------------------------------------------------------------

def test_code_patch_result_round_trip():
    original = MCPCodePatchResult(
        code="def foo(): pass",
        description="Add foo stub",
        queued=True,
    )
    payload = encode(original)
    restored = decode(payload)
    assert isinstance(restored, MCPCodePatchResult)
    assert restored.kind == "code_patch"
    assert restored.code == "def foo(): pass"
    assert restored.description == "Add foo stub"
    assert restored.queued is True


# ---------------------------------------------------------------------------
# Round-trip: MCPErrorResult
# ---------------------------------------------------------------------------

def test_error_result_round_trip():
    original = MCPErrorResult(error="prim not found", validation_blocked=True)
    payload = encode(original)
    restored = decode(payload)
    assert isinstance(restored, MCPErrorResult)
    assert restored.kind == "error"
    assert restored.error == "prim not found"
    assert restored.validation_blocked is True


def test_error_result_default_validation_blocked():
    original = MCPErrorResult(error="timeout")
    assert original.validation_blocked is False
    restored = decode(encode(original))
    assert restored.validation_blocked is False


# ---------------------------------------------------------------------------
# Round-trip: MCPStreamChunkResult
# ---------------------------------------------------------------------------

def test_stream_chunk_result_round_trip():
    original = MCPStreamChunkResult(
        chunk_id=3,
        partial_payload={"progress": 0.5, "items": [1, 2, 3]},
    )
    payload = encode(original)
    restored = decode(payload)
    assert isinstance(restored, MCPStreamChunkResult)
    assert restored.kind == "stream_chunk"
    assert restored.chunk_id == 3
    assert restored.partial_payload["progress"] == 0.5


# ---------------------------------------------------------------------------
# Round-trip: MCPStreamEndResult
# ---------------------------------------------------------------------------

def test_stream_end_result_round_trip():
    original = MCPStreamEndResult(final_payload={"status": "ok", "total": 10})
    payload = encode(original)
    restored = decode(payload)
    assert isinstance(restored, MCPStreamEndResult)
    assert restored.kind == "stream_end"
    assert restored.final_payload == {"status": "ok", "total": 10}


# ---------------------------------------------------------------------------
# Discriminator errors
# ---------------------------------------------------------------------------

def test_bad_discriminator_raises():
    with pytest.raises(ValidationError):
        decode({"kind": "nonexistent_kind", "data": {}})


def test_missing_kind_raises():
    with pytest.raises(ValidationError):
        decode({"data": {"foo": "bar"}})


def test_missing_required_field_raises():
    # MCPCodePatchResult requires code, description, queued
    with pytest.raises(ValidationError):
        decode({"kind": "code_patch", "code": "x"})  # missing description + queued


def test_wrong_type_for_required_field_raises():
    # chunk_id must be int
    with pytest.raises(ValidationError):
        decode({"kind": "stream_chunk", "chunk_id": "not-an-int", "partial_payload": {}})


# ---------------------------------------------------------------------------
# JSON serialization
# ---------------------------------------------------------------------------

def test_json_serialization_round_trip():
    original = MCPDataResult(data={"nested": {"a": 1}, "list": [1, 2, 3]})
    json_str = json.dumps(encode(original))
    payload = json.loads(json_str)
    restored = decode(payload)
    assert isinstance(restored, MCPDataResult)
    assert restored.data == {"nested": {"a": 1}, "list": [1, 2, 3]}


# ---------------------------------------------------------------------------
# Dict round-trip via model_dump / decode
# ---------------------------------------------------------------------------

def test_dict_round_trip_all_variants():
    variants = [
        MCPDataResult(data={"x": 1}),
        MCPCodePatchResult(code="pass", description="noop", queued=False),
        MCPErrorResult(error="fail"),
        MCPStreamChunkResult(chunk_id=0, partial_payload={}),
        MCPStreamEndResult(final_payload={"done": True}),
    ]
    for original in variants:
        restored = decode(encode(original))
        assert restored.kind == original.kind
        assert type(restored) is type(original)
