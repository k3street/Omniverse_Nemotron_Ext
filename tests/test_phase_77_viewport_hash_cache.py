"""Phase 77 — viewport-hash cache: unit tests.

Gate: pytest — cache hit/miss, TTL expiry, LRU eviction, hash determinism.

Spec: specs/IA_FULL_SPEC_2026-05-10.md Phase 77
"""
from __future__ import annotations

import time
from typing import Any, Dict, Optional

import pytest

pytestmark = pytest.mark.l0

# ---------------------------------------------------------------------------
# Imports
# ---------------------------------------------------------------------------

from service.isaac_assist_service.multimodal.viewport_hash_cache import (
    CacheEntry,
    CacheStats,
    ViewportHashCache,
    canonical_camera_params,
    get_phase_metadata,
)


# ---------------------------------------------------------------------------
# 1. Phase metadata
# ---------------------------------------------------------------------------

def test_phase_77_metadata():
    """Phase is landed, not scaffold."""
    md = get_phase_metadata()
    assert md["phase"] == 77
    assert md["status"] == "landed"
    assert "title" in md
    assert "spec_ref" in md


# ---------------------------------------------------------------------------
# 2. Hash determinism — same inputs → same key, every time
# ---------------------------------------------------------------------------

def test_hash_determinism_across_calls():
    """Same bytes + same camera params must yield the same key for 10 calls."""
    cache = ViewportHashCache()
    sample_bytes = b"pixel_data_abc_123"
    params: Dict[str, Any] = {"fov": 60.0, "near": 0.1, "far": 100.0}

    keys = {cache._compute_hash(sample_bytes, params) for _ in range(10)}
    assert len(keys) == 1, "Hash must be deterministic across 10 calls"


def test_hash_determinism_no_params():
    """Hash is deterministic when camera_params is None."""
    cache = ViewportHashCache()
    sample_bytes = b"viewport_frame"
    keys = {cache._compute_hash(sample_bytes, None) for _ in range(10)}
    assert len(keys) == 1


# ---------------------------------------------------------------------------
# 3. Hash sensitivity — different inputs → different keys
# ---------------------------------------------------------------------------

def test_hash_changes_on_byte_change():
    """A single-byte difference must produce a different hash."""
    cache = ViewportHashCache()
    key_a = cache._compute_hash(b"frame_v1")
    key_b = cache._compute_hash(b"frame_v2")
    assert key_a != key_b


def test_hash_changes_on_camera_params_change():
    """Changing camera params must produce a different hash."""
    cache = ViewportHashCache()
    same_bytes = b"same_frame"
    key_a = cache._compute_hash(same_bytes, {"fov": 60.0})
    key_b = cache._compute_hash(same_bytes, {"fov": 90.0})
    assert key_a != key_b


def test_hash_insensitive_to_camera_params_order():
    """Dict key order must NOT affect the hash (canonical JSON)."""
    cache = ViewportHashCache()
    same_bytes = b"same_frame"
    params_a = {"fov": 60.0, "near": 0.1, "far": 100.0}
    params_b = {"far": 100.0, "fov": 60.0, "near": 0.1}
    assert cache._compute_hash(same_bytes, params_a) == cache._compute_hash(
        same_bytes, params_b
    )


# ---------------------------------------------------------------------------
# 4. canonical_camera_params helper
# ---------------------------------------------------------------------------

def test_canonical_camera_params_sorted_keys():
    """canonical_camera_params uses sorted keys with compact separators."""
    result = canonical_camera_params({"z": 3, "a": 1, "m": 2})
    assert result == '{"a":1,"m":2,"z":3}'


def test_canonical_camera_params_none_returns_empty():
    """None input must return empty string."""
    assert canonical_camera_params(None) == ""


def test_canonical_camera_params_empty_dict_returns_empty():
    """Empty dict must return empty string."""
    assert canonical_camera_params({}) == ""


# ---------------------------------------------------------------------------
# 5. put + get round-trip
# ---------------------------------------------------------------------------

def test_put_get_round_trip():
    """put followed by get returns the stored value."""
    cache = ViewportHashCache()
    data = b"viewport_frame_data"
    value = {"result": "a robot arm", "confidence": 0.95}

    key = cache.put(data, value)
    retrieved = cache.get(data)

    assert retrieved == value
    assert isinstance(key, str)
    assert len(key) == 64  # sha256 hex digest


def test_put_get_round_trip_with_params():
    """put+get with camera_params uses the same key derivation."""
    cache = ViewportHashCache()
    data = b"frame"
    params = {"fov": 45.0}
    value = "scene description"

    cache.put(data, value, camera_params=params)
    assert cache.get(data, camera_params=params) == value
    assert cache.get(data, camera_params=None) is None  # different key


# ---------------------------------------------------------------------------
# 6. get returns None on miss
# ---------------------------------------------------------------------------

def test_get_returns_none_on_miss():
    """get on a key that was never put must return None."""
    cache = ViewportHashCache()
    result = cache.get(b"never_stored")
    assert result is None


def test_miss_increments_miss_counter():
    """A cache miss increments the miss counter."""
    cache = ViewportHashCache()
    cache.get(b"miss_1")
    cache.get(b"miss_2")
    assert cache.stats().misses == 2


# ---------------------------------------------------------------------------
# 7. hit_count increments on each hit
# ---------------------------------------------------------------------------

def test_hit_count_increments():
    """Each successful get increments CacheEntry.hit_count."""
    cache = ViewportHashCache()
    data = b"repeat_frame"
    cache.put(data, "vision_result")

    for expected_hits in range(1, 6):
        cache.get(data)
        # Peek at the internal entry.
        key = cache._compute_hash(data)
        entry = cache._store[key]
        assert entry.hit_count == expected_hits


def test_hit_increments_stats_hits():
    """Each cache hit increments the stats.hits counter."""
    cache = ViewportHashCache()
    data = b"frame_for_hit"
    cache.put(data, "result")
    cache.get(data)
    cache.get(data)
    assert cache.stats().hits == 2


# ---------------------------------------------------------------------------
# 8. TTL expiry
# ---------------------------------------------------------------------------

def test_ttl_expired_entry_returns_none():
    """An entry past its TTL returns None and is evicted."""
    cache = ViewportHashCache(ttl_seconds=0.05)  # 50 ms TTL
    data = b"short_lived_frame"
    cache.put(data, "some_result")

    # Expire by advancing the entry's created_at into the past.
    key = cache._compute_hash(data)
    cache._store[key].created_at -= 1.0  # 1 second ago — well past 50 ms TTL

    result = cache.get(data)
    assert result is None
    assert key not in cache._store  # entry must be removed


def test_ttl_non_expired_entry_returns_value():
    """An entry within TTL is not evicted."""
    cache = ViewportHashCache(ttl_seconds=300.0)
    data = b"long_lived_frame"
    cache.put(data, "fresh_result")
    assert cache.get(data) == "fresh_result"


def test_evict_expired_removes_all_stale():
    """_evict_expired removes every entry past TTL."""
    cache = ViewportHashCache(ttl_seconds=10.0)
    for i in range(5):
        cache.put(f"frame_{i}".encode(), f"val_{i}")

    # Age all entries artificially.
    for entry in cache._store.values():
        entry.created_at -= 20.0  # 20 seconds ago

    removed = cache._evict_expired()
    assert removed == 5
    assert len(cache) == 0


# ---------------------------------------------------------------------------
# 9. LRU eviction at max_entries
# ---------------------------------------------------------------------------

def test_lru_eviction_at_capacity():
    """When the cache is full, the oldest-accessed entry is evicted."""
    cache = ViewportHashCache(max_entries=3)

    # Insert three entries.
    cache.put(b"old_frame", "old_value")
    cache.put(b"mid_frame", "mid_value")
    cache.put(b"new_frame", "new_value")

    # Touch "old_frame" AFTER "mid_frame" so mid becomes the LRU.
    cache.get(b"new_frame")
    cache.get(b"old_frame")
    # "mid_frame" now has the oldest accessed_at.

    # Inserting a 4th entry must evict "mid_frame".
    cache.put(b"fourth_frame", "fourth_value")

    assert len(cache) == 3
    assert cache.get(b"mid_frame") is None, "LRU entry must have been evicted"
    assert cache.get(b"old_frame") is not None
    assert cache.get(b"new_frame") is not None
    assert cache.get(b"fourth_frame") is not None


def test_lru_evictions_counted_in_stats():
    """LRU evictions are reflected in stats.evictions."""
    cache = ViewportHashCache(max_entries=2)
    cache.put(b"a", "a")
    cache.put(b"b", "b")
    cache.put(b"c", "c")  # triggers 1 eviction
    assert cache.stats().evictions >= 1


# ---------------------------------------------------------------------------
# 10. max_total_bytes byte-budget eviction
# ---------------------------------------------------------------------------

def test_byte_budget_eviction():
    """Inserting a large entry evicts older entries to stay under max_total_bytes."""
    # Very tight budget: 10 KB.
    cache = ViewportHashCache(max_entries=1000, max_total_bytes=10 * 1024)

    # Fill with small entries first.
    for i in range(5):
        cache.put(f"small_{i}".encode(), "v" * 500)

    initial_count = len(cache)
    assert initial_count > 0

    # Insert a large entry that alone exceeds the budget.
    large_payload = b"X" * (12 * 1024)  # 12 KB > budget
    cache.put(large_payload, "big_result")

    # Byte budget must now be satisfied (or only the new entry remains).
    assert cache.stats().total_bytes <= cache._max_total_bytes or len(cache) == 1


# ---------------------------------------------------------------------------
# 11. clear()
# ---------------------------------------------------------------------------

def test_clear_empties_cache():
    """clear() removes all entries."""
    cache = ViewportHashCache()
    for i in range(10):
        cache.put(f"f{i}".encode(), f"val_{i}")

    cache.clear()
    assert len(cache) == 0
    assert cache.stats().entries == 0


def test_clear_preserves_counters():
    """clear() does not reset hit/miss/eviction counters."""
    cache = ViewportHashCache()
    cache.put(b"frame", "v")
    cache.get(b"frame")   # +1 hit
    cache.get(b"miss")    # +1 miss
    cache.clear()

    stats = cache.stats()
    assert stats.hits == 1
    assert stats.misses == 1


# ---------------------------------------------------------------------------
# 12. stats hit_rate
# ---------------------------------------------------------------------------

def test_stats_hit_rate_formula():
    """hit_rate = hits / (hits + misses)."""
    cache = ViewportHashCache()
    cache.put(b"f1", "v1")
    cache.put(b"f2", "v2")

    cache.get(b"f1")  # hit
    cache.get(b"f2")  # hit
    cache.get(b"f3")  # miss

    stats = cache.stats()
    assert stats.hits == 2
    assert stats.misses == 1
    assert abs(stats.hit_rate - 2 / 3) < 1e-9


def test_stats_hit_rate_zero_when_no_requests():
    """hit_rate is 0.0 when no get() calls have been made."""
    cache = ViewportHashCache()
    assert cache.stats().hit_rate == 0.0


# ---------------------------------------------------------------------------
# 13. __len__
# ---------------------------------------------------------------------------

def test_len_tracks_entries():
    """__len__ returns the number of live entries."""
    cache = ViewportHashCache()
    assert len(cache) == 0
    cache.put(b"a", 1)
    assert len(cache) == 1
    cache.put(b"b", 2)
    assert len(cache) == 2
    cache.clear()
    assert len(cache) == 0
