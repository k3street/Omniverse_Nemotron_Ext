"""Determinism harness — IA-internal reproducibility infrastructure.

One root seed per session, deterministic per-call seed derivation via
canonical SHA-256 hashing, and a versioned `DeterminismToken` that
travels with every stochastic result so a future replay can verify
inputs match.

Used by:
- Snapshot replay (`turn_snapshot.py`)
- MockSimulationRunner (Phase 53 addition)
- DR replicates (Phase 53 / 56b)
- Planner swarm seed flow (`planner/agents/sim_harness.py`)
- Render-side stochastic knobs (`multimodal/render.py`)
- Gap-log re-run (Phase 54+)

Design constraints:
- **No `hash()`** anywhere — Python's hash randomisation makes it
  non-deterministic across processes. Use `hashlib.sha256` over a
  canonical JSON form.
- **No mutation of the registered root** — once a session declares its
  seed_root, that value is the contract. Re-runs must use the same
  number to be reproducible.
- **Token versioning** — `DeterminismToken.version` so future schema
  changes can coexist with v1 tokens in archived gap logs.

Per `specs/IA_FULL_SPEC_2026-05-10.md` Phase 8b.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any


_TOKEN_VERSION = "v1"


# ---------------------------------------------------------------------------
# Hash primitives


def content_hash(payload: Any) -> bytes:
    """Return the 32-byte SHA-256 of a canonical JSON form of `payload`.

    The payload is serialised with `sort_keys=True` and a `default=str`
    fallback for non-JSON types (datetimes, dataclasses, etc.). This
    means two semantically-equal payloads always hash to the same
    bytes, regardless of key order or in-memory representation.
    """
    canonical = json.dumps(payload, sort_keys=True, default=str).encode()
    return hashlib.sha256(canonical).digest()


def derive_seed(root: int, *parts: bytes) -> int:
    """Deterministic per-call seed derivation.

    Returns `int.from_bytes(SHA256(root || parts[0] || parts[1] || ...))`
    truncated to 8 bytes. Same `(root, *parts)` → always the same int.

    `root` is treated as an unsigned 64-bit integer; values outside
    `[0, 2**64 - 1]` raise `OverflowError` (intentional — surfaces
    misuse rather than silently truncating).

    `parts` should be `bytes` objects. Strings should be `.encode()`d
    by the caller; if a `str` is passed it is auto-encoded as UTF-8
    so the most common call sites don't have to remember.
    """
    h = hashlib.sha256()
    h.update(int(root).to_bytes(8, "little", signed=False))
    for p in parts:
        if isinstance(p, str):
            h.update(p.encode("utf-8"))
        else:
            h.update(p)
    return int.from_bytes(h.digest()[:8], "little", signed=False)


# ---------------------------------------------------------------------------
# DeterminismToken — travels with every stochastic result


@dataclass(frozen=True)
class DeterminismToken:
    """A 40-byte token (8-byte seed + 32-byte content hash + version).

    Attach to every stochastic result so a future replay can:
    1. Re-derive the same seed from the same `root` + `parts`.
    2. Verify the input content hasn't drifted (compare
       `content_hash`).
    3. Tell which token schema produced it (`version` for forward
       compatibility).
    """

    seed: int
    content_hash: bytes
    version: str = _TOKEN_VERSION

    def __post_init__(self) -> None:
        """Validate seed range (0 ≤ seed < 2**64) and content_hash length (32 bytes)."""
        if not (0 <= self.seed < 2**64):
            raise ValueError(
                f"seed must fit in unsigned 64-bit, got {self.seed!r}"
            )
        if len(self.content_hash) != 32:
            raise ValueError(
                f"content_hash must be 32 bytes (SHA-256), "
                f"got {len(self.content_hash)} bytes"
            )

    def to_hex(self) -> str:
        """Stable hex serialisation: `<16-char seed>:<64-char hash>:<version>`."""
        return f"{self.seed:016x}:{self.content_hash.hex()}:{self.version}"

    @classmethod
    def from_hex(cls, encoded: str) -> "DeterminismToken":
        """Inverse of `to_hex`. Raises `ValueError` on malformed input."""
        parts = encoded.split(":")
        if len(parts) != 3:
            raise ValueError(
                f"DeterminismToken hex must have exactly 3 colon-separated "
                f"parts, got {len(parts)}: {encoded!r}"
            )
        seed_hex, hash_hex, version = parts
        if len(seed_hex) != 16:
            raise ValueError(f"seed hex must be 16 chars, got {len(seed_hex)}")
        if len(hash_hex) != 64:
            raise ValueError(f"hash hex must be 64 chars, got {len(hash_hex)}")
        try:
            seed = int(seed_hex, 16)
        except ValueError as e:
            raise ValueError(f"seed hex is not valid hex: {seed_hex!r}") from e
        try:
            ch = bytes.fromhex(hash_hex)
        except ValueError as e:
            raise ValueError(f"content_hash hex is not valid hex: {hash_hex!r}") from e
        return cls(seed=seed, content_hash=ch, version=version)


def make_token(root: int, payload: Any, *extra_parts: bytes) -> DeterminismToken:
    """Convenience constructor: hash `payload`, derive seed from the
    hash, return a token. Most call sites want this rather than
    composing `content_hash` + `derive_seed` + `DeterminismToken`
    by hand.
    """
    ch = content_hash(payload)
    seed = derive_seed(root, ch, *extra_parts)
    return DeterminismToken(seed=seed, content_hash=ch)
