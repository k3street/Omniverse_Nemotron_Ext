"""Fingerprint API routes — cached hardware snapshot and compatibility verdict.

Fingerprint collection runs a set of shell probes (nvidia-smi, nvcc) and is
cached with a 5-minute TTL to avoid hammering the OS on every request.
The ``POST /collect`` endpoint forces an immediate re-scan.
"""
import asyncio
import time
from fastapi import APIRouter
from .collector import collect_fingerprint
from .compatibility import resolve_compatibility

router = APIRouter()

# Thread-safe cache with TTL
_cache_lock = asyncio.Lock()
_cached_fingerprint = None
_cached_resolution = None
_cache_timestamp: float = 0
_CACHE_TTL: float = 300  # 5 minutes

@router.get("/collect")
async def get_fingerprint():
    """Return the cached hardware fingerprint, refreshing if the 5-minute TTL has expired.

    Returns:
        dict: Fingerprint as returned by ``collect_fingerprint()``; keys include
        ``fingerprint_id``, ``gpu_devices``, ``isaac_sim_version``, etc.
    """
    global _cached_fingerprint, _cache_timestamp
    async with _cache_lock:
        now = time.monotonic()
        if _cached_fingerprint is None or (now - _cache_timestamp) > _CACHE_TTL:
            _cached_fingerprint = collect_fingerprint()
            _cache_timestamp = now
    return _cached_fingerprint

@router.post("/collect")
async def force_refresh_fingerprint():
    """Force an immediate hardware re-scan, bypassing the cache TTL.

    Also recomputes the compatibility verdict so both cache entries are
    consistent after the refresh.

    Returns:
        dict: Freshly collected fingerprint (same shape as ``GET /collect``).
    """
    global _cached_fingerprint, _cached_resolution, _cache_timestamp
    async with _cache_lock:
        _cached_fingerprint = collect_fingerprint()
        _cached_resolution = resolve_compatibility(_cached_fingerprint)
        _cache_timestamp = time.monotonic()
    return _cached_fingerprint

@router.get("/resolve")
async def get_resolution():
    """Return the cached hardware compatibility verdict, refreshing if expired.

    Returns:
        dict: Compatibility verdict as returned by ``resolve_compatibility()``.
    """
    global _cached_fingerprint, _cached_resolution, _cache_timestamp
    async with _cache_lock:
        now = time.monotonic()
        if _cached_fingerprint is None or (now - _cache_timestamp) > _CACHE_TTL:
            _cached_fingerprint = collect_fingerprint()
            _cache_timestamp = now
        if _cached_resolution is None:
            _cached_resolution = resolve_compatibility(_cached_fingerprint)
    return _cached_resolution
