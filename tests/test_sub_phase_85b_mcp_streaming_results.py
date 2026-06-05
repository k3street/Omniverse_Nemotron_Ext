"""Tests for Phase 85b — MCP streaming results.

Covers: metadata, builder chunk emission with sequential ids,
emit_end produces MCPStreamEndResult, collector assembly (later wins),
chunk-after-end raises, is_complete flips after end, stream_iter async
generator yields N+1 results.
"""
from __future__ import annotations

import asyncio

import pytest

pytestmark = pytest.mark.l0


# ---------------------------------------------------------------------------
# Imports under test
# ---------------------------------------------------------------------------

from service.isaac_assist_service.multimodal.sub_phase_85b_mcp_streaming_results import (
    StreamingResultBuilder,
    StreamingResultCollector,
    get_phase_metadata,
    stream_iter,
)
from service.isaac_assist_service.multimodal.mcp_result_type_discrimination import (
    MCPStreamChunkResult,
    MCPStreamEndResult,
)


# ---------------------------------------------------------------------------
# 1. Metadata
# ---------------------------------------------------------------------------

def test_phase_85b_metadata():
    md = get_phase_metadata()
    assert md["phase"] == "85b"
    assert md["status"] == "landed"
    assert "title" in md
    assert "spec_ref" in md


# ---------------------------------------------------------------------------
# 2. Builder — sequential chunk ids
# ---------------------------------------------------------------------------

def test_builder_emits_chunks_with_sequential_ids():
    builder = StreamingResultBuilder(stream_id="s1")
    c0 = builder.emit_chunk({"a": 1})
    c1 = builder.emit_chunk({"b": 2})
    c2 = builder.emit_chunk({"c": 3})

    assert isinstance(c0, MCPStreamChunkResult)
    assert c0.chunk_id == 0
    assert c1.chunk_id == 1
    assert c2.chunk_id == 2


# ---------------------------------------------------------------------------
# 3. Builder — chunks_so_far reflects emitted count
# ---------------------------------------------------------------------------

def test_builder_chunks_so_far():
    builder = StreamingResultBuilder(stream_id="s2")
    assert builder.chunks_so_far() == 0
    builder.emit_chunk({"x": 10})
    builder.emit_chunk({"x": 20})
    assert builder.chunks_so_far() == 2


# ---------------------------------------------------------------------------
# 4. Builder — emit_end produces MCPStreamEndResult
# ---------------------------------------------------------------------------

def test_builder_emit_end_produces_correct_type():
    builder = StreamingResultBuilder(stream_id="s3")
    builder.emit_chunk({"step": 1})
    end = builder.emit_end({"summary": "done"})

    assert isinstance(end, MCPStreamEndResult)
    assert end.final_payload == {"summary": "done"}
    # emit_end must NOT increment the chunk counter
    assert builder.chunks_so_far() == 1


# ---------------------------------------------------------------------------
# 5. Collector — assembles partial + final payloads, later keys win
# ---------------------------------------------------------------------------

def test_collector_assembles_payload_later_wins():
    builder = StreamingResultBuilder(stream_id="s4")
    c0 = builder.emit_chunk({"key": "chunk0", "extra": "from_chunk"})
    c1 = builder.emit_chunk({"key": "chunk1"})
    end = builder.emit_end({"key": "final", "done": True})

    collector = StreamingResultCollector()
    collector.accept(c0)
    collector.accept(c1)
    collector.accept(end)

    payload = collector.assembled_payload()
    # final_payload overlays last → "key" should be "final"
    assert payload["key"] == "final"
    assert payload["done"] is True
    assert payload["extra"] == "from_chunk"


# ---------------------------------------------------------------------------
# 6. Collector — chunk after end raises RuntimeError
# ---------------------------------------------------------------------------

def test_collector_chunk_after_end_raises():
    builder = StreamingResultBuilder(stream_id="s5")
    c0 = builder.emit_chunk({"a": 1})
    end = builder.emit_end({})
    late_chunk = builder.emit_chunk({"late": True})

    collector = StreamingResultCollector()
    collector.accept(c0)
    collector.accept(end)

    with pytest.raises(RuntimeError, match="after stream end"):
        collector.accept(late_chunk)


# ---------------------------------------------------------------------------
# 7. Collector — is_complete flips after end
# ---------------------------------------------------------------------------

def test_collector_is_complete_flips_after_end():
    builder = StreamingResultBuilder(stream_id="s6")
    c0 = builder.emit_chunk({"i": 0})
    end = builder.emit_end({"finished": True})

    collector = StreamingResultCollector()
    assert not collector.is_complete()
    collector.accept(c0)
    assert not collector.is_complete()
    collector.accept(end)
    assert collector.is_complete()


# ---------------------------------------------------------------------------
# 8. stream_iter async generator yields N+1 results
# ---------------------------------------------------------------------------

def test_stream_iter_yields_n_plus_one_results():
    async def collect():
        builder = StreamingResultBuilder(stream_id="s7")
        n = 4
        results = []
        async for item in stream_iter(builder, n, payload_fn=lambda i: {"i": i}):
            results.append(item)
        return n, results

    n, results = asyncio.run(collect())

    assert len(results) == n + 1
    # First N are chunks
    for idx, r in enumerate(results[:n]):
        assert isinstance(r, MCPStreamChunkResult)
        assert r.chunk_id == idx
        assert r.partial_payload == {"i": idx}
    # Last is end
    assert isinstance(results[-1], MCPStreamEndResult)
