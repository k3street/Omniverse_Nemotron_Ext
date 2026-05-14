"""Phase 77 — Vision tool: viewport-hash cache.

LRU cache keyed by sha256(viewport_bytes + canonical_camera_params).
Caches any value (e.g. Gemini Vision responses) with TTL and byte-budget
eviction so the same frozen scene does not trigger a new Gemini call.

Populating the cache with real viewport bytes requires a running Kit instance
(Phase 76); this module is pure Python and tested independently.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 77.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


PHASE_ID = 77
PHASE_TITLE = "Vision tool: viewport-hash cache"
PHASE_STATUS = "landed"


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def canonical_camera_params(params: Optional[Dict[str, Any]]) -> str:
    """Return a deterministic JSON string for *params*.

    Key ordering is normalised (sorted_keys=True) and whitespace collapsed so
    the string is identical regardless of the dict construction order.  Returns
    the empty string when *params* is None or empty.
    """
    if not params:
        return ""
    return json.dumps(params, sort_keys=True, separators=(",", ":"))


# ---------------------------------------------------------------------------
# Cache data structures
# ---------------------------------------------------------------------------

@dataclass
class CacheEntry:
    """One cached viewport vision result with access-time metadata."""
    key: str
    value: Any
    created_at: float
    accessed_at: float
    hit_count: int = 0
    size_bytes: int = 0


@dataclass
class CacheStats:
    """Snapshot of ``ViewportHashCache`` counters and current occupancy."""
    hits: int
    misses: int
    evictions: int
    entries: int
    total_bytes: int
    hit_rate: float


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

class ViewportHashCache:
    """LRU + TTL + byte-budget cache for viewport-keyed vision results.

    Thread-safety: NOT guaranteed — callers must serialise access if needed.
    """

    def __init__(
        self,
        max_entries: int = 100,
        ttl_seconds: float = 300.0,
        max_total_bytes: int = 100 * 1024 * 1024,  # 100 MB
    ) -> None:
        """Initialise the cache.

        Args:
            max_entries (int): Maximum number of entries before LRU eviction.
            ttl_seconds (float): Time-to-live per entry in seconds.
            max_total_bytes (int): Byte budget before additional LRU eviction.
        """
        self._max_entries = max_entries
        self._ttl_seconds = ttl_seconds
        self._max_total_bytes = max_total_bytes

        self._store: Dict[str, CacheEntry] = {}
        self._hits = 0
        self._misses = 0
        self._evictions = 0

    # ------------------------------------------------------------------
    # Hashing
    # ------------------------------------------------------------------

    def _compute_hash(
        self,
        viewport_bytes: bytes,
        camera_params: Optional[Dict[str, Any]] = None,
    ) -> str:
        """sha256 over *viewport_bytes* concatenated with canonical camera JSON.

        Returns the hex digest (64 characters).
        """
        h = hashlib.sha256()
        h.update(viewport_bytes)
        cam_str = canonical_camera_params(camera_params)
        if cam_str:
            h.update(cam_str.encode("utf-8"))
        return h.hexdigest()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(
        self,
        viewport_bytes: bytes,
        camera_params: Optional[Dict[str, Any]] = None,
    ) -> Optional[Any]:
        """Return the cached value for these bytes, or None on miss.

        Expired entries are treated as misses and removed on access.
        """
        key = self._compute_hash(viewport_bytes, camera_params)
        entry = self._store.get(key)
        if entry is None:
            self._misses += 1
            return None

        now = time.monotonic()
        if now - entry.created_at > self._ttl_seconds:
            # Expired — purge and report miss.
            del self._store[key]
            self._evictions += 1
            self._misses += 1
            return None

        entry.accessed_at = now
        entry.hit_count += 1
        self._hits += 1
        return entry.value

    def put(
        self,
        viewport_bytes: bytes,
        value: Any,
        camera_params: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Store *value* under the hash key derived from *viewport_bytes*.

        Returns the computed key string.

        Eviction order:
        1. Expired entries (oldest-first, all of them).
        2. LRU entries if still over max_entries.
        3. LRU entries if still over max_total_bytes after the new entry is
           tentatively added.
        """
        key = self._compute_hash(viewport_bytes, camera_params)
        now = time.monotonic()

        # Estimate size: len of bytes + rough serialised-value size.
        size_estimate = len(viewport_bytes)
        try:
            size_estimate += len(str(value).encode("utf-8"))
        except Exception:
            pass

        # First sweep: expired entries.
        self._evict_expired(now=now)

        # Second sweep: capacity.
        while len(self._store) >= self._max_entries and self._store:
            self._evict_lru()

        # Insert / overwrite.
        entry = CacheEntry(
            key=key,
            value=value,
            created_at=now,
            accessed_at=now,
            hit_count=0,
            size_bytes=size_estimate,
        )
        self._store[key] = entry

        # Third sweep: byte budget.
        self._evict_to_fit_bytes()

        return key

    # ------------------------------------------------------------------
    # Eviction helpers
    # ------------------------------------------------------------------

    def _evict_lru(self) -> int:
        """Remove the single entry with the oldest *accessed_at*.

        Returns the number of entries removed (0 or 1).
        """
        if not self._store:
            return 0
        lru_key = min(self._store, key=lambda k: self._store[k].accessed_at)
        del self._store[lru_key]
        self._evictions += 1
        return 1

    def _evict_expired(self, now: Optional[float] = None) -> int:
        """Remove all entries whose age exceeds *ttl_seconds*.

        Returns the count of entries removed.
        """
        if now is None:
            now = time.monotonic()
        expired = [
            k
            for k, e in self._store.items()
            if now - e.created_at > self._ttl_seconds
        ]
        for k in expired:
            del self._store[k]
            self._evictions += 1
        return len(expired)

    def _evict_to_fit_bytes(self) -> int:
        """Evict LRU entries until total_bytes ≤ max_total_bytes.

        Returns the number of entries removed.
        """
        removed = 0
        while self._total_bytes() > self._max_total_bytes and self._store:
            removed += self._evict_lru()
        return removed

    # ------------------------------------------------------------------
    # Aggregates
    # ------------------------------------------------------------------

    def _total_bytes(self) -> int:
        """Return the sum of ``size_bytes`` across all current entries."""
        return sum(e.size_bytes for e in self._store.values())

    def stats(self) -> CacheStats:
        """Return a snapshot of current cache statistics."""
        total = self._hits + self._misses
        hit_rate = self._hits / total if total > 0 else 0.0
        return CacheStats(
            hits=self._hits,
            misses=self._misses,
            evictions=self._evictions,
            entries=len(self._store),
            total_bytes=self._total_bytes(),
            hit_rate=hit_rate,
        )

    def clear(self) -> None:
        """Drop all entries (counters are preserved)."""
        self._store.clear()

    def __len__(self) -> int:
        """Return the current number of entries in the cache."""
        return len(self._store)


# ---------------------------------------------------------------------------
# Phase metadata
# ---------------------------------------------------------------------------

def get_phase_metadata() -> Dict[str, Any]:
    """Return phase identification and status for the viewport hash cache phase.

    Returns:
        Dict[str, Any]: Keys ``phase``, ``title``, ``status``, and ``spec_ref``.
    """
    return {
        "phase": PHASE_ID,
        "title": PHASE_TITLE,
        "status": PHASE_STATUS,
        "spec_ref": "specs/IA_FULL_SPEC_2026-05-10.md Phase 77",
    }
