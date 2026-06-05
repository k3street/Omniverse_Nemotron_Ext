"""Phase 85 — MCPResult type discrimination.

Pydantic v2 discriminated union for all MCP result kinds.
Each variant carries a Literal ``kind`` field used as the discriminator
so that ``decode(payload)`` can reconstruct the exact subtype without
inspecting arbitrary payload structure.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 85.
"""
from __future__ import annotations

from typing import Annotated, Any, Dict, Literal, Union

from pydantic import BaseModel, Field, ValidationError


# ---------------------------------------------------------------------------
# Phase metadata
# ---------------------------------------------------------------------------

PHASE_ID = 85
PHASE_TITLE = "MCPResult type discrimination"
PHASE_STATUS = "landed"


def get_phase_metadata() -> Dict[str, Any]:
    """Return phase identification and status for Phase 85.

    Returns:
        Dict[str, Any]: Keys ``phase``, ``title``, ``status``, and ``spec_ref``.
    """
    return {
        "phase": PHASE_ID,
        "title": PHASE_TITLE,
        "status": PHASE_STATUS,
        "spec_ref": "specs/IA_FULL_SPEC_2026-05-10.md Phase 85",
    }


# ---------------------------------------------------------------------------
# Discriminator type alias
# ---------------------------------------------------------------------------

MCPResultKind = Literal["data", "code_patch", "error", "stream_chunk", "stream_end"]


# ---------------------------------------------------------------------------
# Concrete result models
# ---------------------------------------------------------------------------

class MCPDataResult(BaseModel):
    """A plain structured data payload returned by a tool handler."""
    kind: Literal["data"] = "data"
    data: Dict[str, Any]


class MCPCodePatchResult(BaseModel):
    """A queued code patch produced by a code-generation tool handler."""
    kind: Literal["code_patch"] = "code_patch"
    code: str
    description: str
    queued: bool


class MCPErrorResult(BaseModel):
    """An error result returned when a tool handler fails or is blocked."""
    kind: Literal["error"] = "error"
    error: str
    validation_blocked: bool = False


class MCPStreamChunkResult(BaseModel):
    """One partial-payload chunk in a streaming tool response."""
    kind: Literal["stream_chunk"] = "stream_chunk"
    chunk_id: int
    partial_payload: Dict[str, Any]


class MCPStreamEndResult(BaseModel):
    """Terminal end-result marker carrying the final payload of a stream."""
    kind: Literal["stream_end"] = "stream_end"
    final_payload: Dict[str, Any]


# ---------------------------------------------------------------------------
# Discriminated union
# ---------------------------------------------------------------------------

MCPResult = Annotated[
    Union[
        MCPDataResult,
        MCPCodePatchResult,
        MCPErrorResult,
        MCPStreamChunkResult,
        MCPStreamEndResult,
    ],
    Field(discriminator="kind"),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def encode(result: BaseModel) -> Dict[str, Any]:
    """Serialise an MCPResult variant to a plain dict.

    Uses Pydantic v2 ``model_dump`` so nested models are converted too.
    """
    return result.model_dump()


def decode(payload: Dict[str, Any]) -> MCPDataResult | MCPCodePatchResult | MCPErrorResult | MCPStreamChunkResult | MCPStreamEndResult:
    """Deserialise a plain dict to the correct MCPResult variant.

    The ``kind`` field acts as the discriminator.  Raises
    ``pydantic.ValidationError`` when the payload is invalid or when
    ``kind`` does not match any known variant.
    """
    from pydantic import TypeAdapter

    adapter: TypeAdapter[Any] = TypeAdapter(MCPResult)
    return adapter.validate_python(payload)
