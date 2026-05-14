"""Phase 87 — Stdio MCP shim hardening.

Real implementation: error-handling + lifecycle state machine +
request/response envelope parsing.  Pure Python — no Kit/GPU deps.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 87.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Literal, Optional, Union


# ---------------------------------------------------------------------------
# Phase metadata
# ---------------------------------------------------------------------------

PHASE_ID = 87
PHASE_TITLE = "Stdio MCP shim hardening"
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
        "spec_ref": "specs/IA_FULL_SPEC_2026-05-10.md Phase 87",
    }


# ---------------------------------------------------------------------------
# Standard JSON-RPC 2.0 / MCP error codes
# ---------------------------------------------------------------------------

MCP_ERROR_CODES: Dict[str, int] = {
    "parse_error": -32700,
    "invalid_request": -32600,
    "method_not_found": -32601,
    "invalid_params": -32602,
    "internal_error": -32603,
    "timeout": -32000,
    "payload_too_large": -32001,
}


# ---------------------------------------------------------------------------
# State type
# ---------------------------------------------------------------------------

MCPState = Literal["uninitialized", "handshaking", "ready", "error", "closed"]


# ---------------------------------------------------------------------------
# Error dataclass
# ---------------------------------------------------------------------------

@dataclass
class MCPShimError:
    """Structured shim error, maps to JSON-RPC error object."""

    code: int
    message: str
    data: Any = None

    def to_dict(self) -> Dict[str, Any]:
        """Serialise to a JSON-RPC error object dict, omitting ``data`` when absent."""
        d: Dict[str, Any] = {"code": self.code, "message": self.message}
        if self.data is not None:
            d["data"] = self.data
        return d


# ---------------------------------------------------------------------------
# Envelope dataclasses
# ---------------------------------------------------------------------------

@dataclass
class MCPRequestEnvelope:
    """Represents an incoming or outgoing JSON-RPC 2.0 request."""

    id: str
    method: str
    params: Dict[str, Any]
    jsonrpc: str = "2.0"


@dataclass
class MCPResponseEnvelope:
    """Represents an incoming or outgoing JSON-RPC 2.0 response."""

    id: Optional[str]
    result: Any = None
    error: Optional[Dict[str, Any]] = None
    jsonrpc: str = "2.0"


# ---------------------------------------------------------------------------
# Size helpers (module-level)
# ---------------------------------------------------------------------------

def validate_envelope_size(raw_line: str, max_bytes: int) -> bool:
    """Return True if the encoded byte-length of *raw_line* does not exceed *max_bytes*."""
    return len(raw_line.encode("utf-8")) <= max_bytes


# ---------------------------------------------------------------------------
# StdioMCPShim
# ---------------------------------------------------------------------------

class StdioMCPShim:
    """Lightweight JSON-RPC 2.0 shim for the stdio MCP transport.

    Responsibilities
    ----------------
    - Parse and validate inbound JSON lines into typed envelopes.
    - Serialize outbound envelopes to JSON lines.
    - Drive a simple lifecycle state machine:
        uninitialized → handshaking → ready (or error) → closed
      with ``reset()`` returning to *uninitialized*.
    - Reject oversized messages before parse.

    Does **not** own I/O; the caller owns stdin/stdout.
    """

    _HANDSHAKE_METHOD = "initialize"

    def __init__(
        self,
        handshake_timeout_s: float = 5.0,
        max_message_bytes: int = 1024 * 1024,
    ) -> None:
        """Initialise the shim with handshake timeout and per-message size limit."""
        self._handshake_timeout_s = handshake_timeout_s
        self._max_message_bytes = max_message_bytes
        self._state: MCPState = "uninitialized"
        self._handshake_request_id: Optional[str] = None

    # ------------------------------------------------------------------
    # State property
    # ------------------------------------------------------------------

    @property
    def state(self) -> MCPState:
        """Current lifecycle state."""
        return self._state

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    def parse_envelope(
        self, raw_line: str
    ) -> Union[MCPRequestEnvelope, MCPResponseEnvelope]:
        """Parse a raw JSON line into a typed envelope.

        Raises
        ------
        ValueError
            If the line is not valid JSON, is missing required fields,
            has ``jsonrpc != "2.0"``, or cannot be classified as a
            request or response.
        """
        try:
            obj = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"parse_error: {exc}") from exc

        if not isinstance(obj, dict):
            raise ValueError("invalid_request: envelope must be a JSON object")

        # jsonrpc version check
        jsonrpc = obj.get("jsonrpc")
        if jsonrpc != "2.0":
            raise ValueError(
                f"invalid_request: jsonrpc must be '2.0', got {jsonrpc!r}"
            )

        # id is required in both request and response
        if "id" not in obj:
            raise ValueError("invalid_request: 'id' field is missing")

        msg_id = obj["id"]

        # Classify: request has 'method'; response has 'result' or 'error'
        if "method" in obj:
            method = obj["method"]
            if not isinstance(method, str) or not method:
                raise ValueError("invalid_request: 'method' must be a non-empty string")
            params = obj.get("params", {})
            if not isinstance(params, dict):
                raise ValueError("invalid_params: 'params' must be a JSON object")
            return MCPRequestEnvelope(
                id=str(msg_id),
                method=method,
                params=params,
                jsonrpc=jsonrpc,
            )

        if "result" in obj or "error" in obj:
            error_val = obj.get("error")
            if error_val is not None and not isinstance(error_val, dict):
                raise ValueError("invalid_request: 'error' must be a JSON object")
            return MCPResponseEnvelope(
                id=str(msg_id) if msg_id is not None else None,
                result=obj.get("result"),
                error=error_val,
                jsonrpc=jsonrpc,
            )

        raise ValueError(
            "invalid_request: envelope must have 'method' (request) "
            "or 'result'/'error' (response)"
        )

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def serialize(
        self, envelope: Union[MCPRequestEnvelope, MCPResponseEnvelope]
    ) -> str:
        """Serialise an envelope to a compact JSON string (no trailing newline)."""
        if isinstance(envelope, MCPRequestEnvelope):
            obj: Dict[str, Any] = {
                "jsonrpc": envelope.jsonrpc,
                "id": envelope.id,
                "method": envelope.method,
                "params": envelope.params,
            }
        else:
            obj = {
                "jsonrpc": envelope.jsonrpc,
                "id": envelope.id,
            }
            if envelope.error is not None:
                obj["error"] = envelope.error
            else:
                obj["result"] = envelope.result
        return json.dumps(obj, separators=(",", ":"))

    # ------------------------------------------------------------------
    # Size guard
    # ------------------------------------------------------------------

    def is_size_within_limit(self, raw_line: str) -> bool:
        """Return True if *raw_line* fits within ``max_message_bytes``."""
        return validate_envelope_size(raw_line, self._max_message_bytes)

    # ------------------------------------------------------------------
    # Handshake
    # ------------------------------------------------------------------

    def begin_handshake(self, client_version: str) -> MCPRequestEnvelope:
        """Emit an *initialize* request and move to the **handshaking** state.

        Parameters
        ----------
        client_version:
            Semantic-version string the client advertises to the server.

        Returns
        -------
        MCPRequestEnvelope
            The envelope the caller should write to stdout.
        """
        request_id = str(uuid.uuid4())
        self._handshake_request_id = request_id
        self._state = "handshaking"
        return MCPRequestEnvelope(
            id=request_id,
            method=self._HANDSHAKE_METHOD,
            params={"protocolVersion": client_version},
        )

    def complete_handshake(self, response: MCPResponseEnvelope) -> None:
        """Process the server's *initialize* response and settle state.

        Transitions to **ready** on success or **error** if the response
        carries an error object or the state is not *handshaking*.
        """
        if self._state != "handshaking":
            self._state = "error"
            return

        if response.error is not None:
            self._state = "error"
            return

        self._state = "ready"

    # ------------------------------------------------------------------
    # Error helpers
    # ------------------------------------------------------------------

    def make_error_response(
        self,
        request_id: str,
        code: int,
        message: str,
        data: Any = None,
    ) -> MCPResponseEnvelope:
        """Build a well-formed JSON-RPC 2.0 error response."""
        error_obj: Dict[str, Any] = {"code": code, "message": message}
        if data is not None:
            error_obj["data"] = data
        return MCPResponseEnvelope(id=request_id, error=error_obj)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Transition to **closed** — no further operations are valid."""
        self._state = "closed"

    def reset(self) -> None:
        """Reset to **uninitialized**, clearing all transient state."""
        self._state = "uninitialized"
        self._handshake_request_id = None
