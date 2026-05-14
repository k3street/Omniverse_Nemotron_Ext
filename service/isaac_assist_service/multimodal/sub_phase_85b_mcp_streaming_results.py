"""Phase 85b — MCP streaming results.

Chunked result emission system built on Phase 85's MCPResult discriminated
union.  Provides a builder (producer side) and a collector (consumer side),
plus an async generator helper for end-to-end streaming pipelines.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 85b.
"""
from __future__ import annotations

from typing import Any, AsyncIterator, Callable, Dict

from service.isaac_assist_service.multimodal.mcp_result_type_discrimination import (
    MCPStreamChunkResult,
    MCPStreamEndResult,
)

# Re-export MCPResult for callers who import from this module.
from service.isaac_assist_service.multimodal.mcp_result_type_discrimination import (  # noqa: F401
    MCPResult,
)


# ---------------------------------------------------------------------------
# Phase metadata
# ---------------------------------------------------------------------------

PHASE_ID = "85b"
PHASE_TITLE = "mcp streaming results"
PHASE_STATUS = "landed"


def get_phase_metadata() -> Dict[str, Any]:
    """Return phase identification and status for this phase.

    Returns:
        Dict[str, Any]: Keys ``phase``, ``title``, ``status``, and ``spec_ref``.
    """
    return {
        "phase": PHASE_ID,
        "title": PHASE_TITLE,
        "status": PHASE_STATUS,
        "spec_ref": "specs/IA_FULL_SPEC_2026-05-10.md Phase 85b",
    }


# ---------------------------------------------------------------------------
# StreamingResultBuilder — producer side
# ---------------------------------------------------------------------------

class StreamingResultBuilder:
    """Emit sequential chunks followed by a terminal end-result.

    Parameters
    ----------
    stream_id:
        Opaque identifier for the stream (used by consumers for routing).
    """

    def __init__(self, stream_id: str) -> None:
        self._stream_id = stream_id
        self._chunk_counter: int = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def emit_chunk(self, partial_payload: Dict[str, Any]) -> MCPStreamChunkResult:
        """Emit the next chunk and auto-increment the chunk_id counter.

        Parameters
        ----------
        partial_payload:
            Partial data for this chunk.  Must be a plain dict.

        Returns
        -------
        MCPStreamChunkResult
            Immutable chunk result with the current chunk_id.
        """
        chunk = MCPStreamChunkResult(
            chunk_id=self._chunk_counter,
            partial_payload=partial_payload,
        )
        self._chunk_counter += 1
        return chunk

    def emit_end(self, final_payload: Dict[str, Any]) -> MCPStreamEndResult:
        """Emit the terminal end-result for this stream.

        Parameters
        ----------
        final_payload:
            Final data that completes the stream.

        Returns
        -------
        MCPStreamEndResult
            Terminal marker carrying the final payload.
        """
        return MCPStreamEndResult(final_payload=final_payload)

    def chunks_so_far(self) -> int:
        """Return the number of chunks emitted so far (not counting end)."""
        return self._chunk_counter


# ---------------------------------------------------------------------------
# StreamingResultCollector — consumer side
# ---------------------------------------------------------------------------

class StreamingResultCollector:
    """Consume chunks and the terminal end-result; assemble the final payload.

    Usage::

        collector = StreamingResultCollector()
        for result in source:
            collector.accept(result)
        payload = collector.assembled_payload()
    """

    def __init__(self) -> None:
        self._chunks: list[MCPStreamChunkResult] = []
        self._end: MCPStreamEndResult | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def accept(self, result: MCPStreamChunkResult | MCPStreamEndResult) -> None:
        """Accept one result from the stream.

        Parameters
        ----------
        result:
            Either an ``MCPStreamChunkResult`` or an ``MCPStreamEndResult``.

        Raises
        ------
        RuntimeError
            If a chunk arrives after the end-result has already been received.
        """
        if isinstance(result, MCPStreamChunkResult):
            if self._end is not None:
                raise RuntimeError(
                    f"Received chunk (id={result.chunk_id}) after stream end."
                )
            self._chunks.append(result)
        elif isinstance(result, MCPStreamEndResult):
            self._end = result
        else:
            raise TypeError(
                f"accept() expects MCPStreamChunkResult or MCPStreamEndResult, "
                f"got {type(result).__name__}"
            )

    def is_complete(self) -> bool:
        """Return True once the end-result has been received."""
        return self._end is not None

    def assembled_payload(self) -> Dict[str, Any]:
        """Merge all partial payloads and the final payload into one dict.

        Later keys win: chunk payloads are applied in order of arrival, then
        the final payload is overlaid last.

        Raises
        ------
        RuntimeError
            If called before the end-result has been received.
        """
        if self._end is None:
            raise RuntimeError("Stream is not complete; call after accept()-ing the end result.")
        merged: Dict[str, Any] = {}
        for chunk in self._chunks:
            merged.update(chunk.partial_payload)
        merged.update(self._end.final_payload)
        return merged

    def chunks_received(self) -> int:
        """Return the number of chunk results received (not counting end)."""
        return len(self._chunks)


# ---------------------------------------------------------------------------
# Async generator helper
# ---------------------------------------------------------------------------

async def stream_iter(
    builder: StreamingResultBuilder,
    n_chunks: int,
    payload_fn: Callable[[int], Dict[str, Any]],
) -> AsyncIterator[MCPStreamChunkResult | MCPStreamEndResult]:
    """Async generator that emits *n_chunks* chunks then a terminal end-result.

    Parameters
    ----------
    builder:
        The ``StreamingResultBuilder`` used to produce results.
    n_chunks:
        Number of chunk results to emit before the end-result.
    payload_fn:
        Callable ``(chunk_index: int) -> dict`` that supplies the partial
        payload for each chunk.  The end-result receives an empty dict ``{}``.

    Yields
    ------
    MCPStreamChunkResult | MCPStreamEndResult
        Yields *n_chunks* chunk results followed by one end-result (total
        ``n_chunks + 1`` items).
    """
    for i in range(n_chunks):
        yield builder.emit_chunk(payload_fn(i))
    yield builder.emit_end({})
