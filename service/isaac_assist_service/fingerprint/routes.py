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
    """Returns the cached fingerprint, refreshing if TTL expired."""
    global _cached_fingerprint, _cache_timestamp
    async with _cache_lock:
        now = time.monotonic()
        if _cached_fingerprint is None or (now - _cache_timestamp) > _CACHE_TTL:
            _cached_fingerprint = collect_fingerprint()
            _cache_timestamp = now
    return _cached_fingerprint

@router.post("/collect")
async def force_refresh_fingerprint():
    """Forces a hardware re-scan."""
    global _cached_fingerprint, _cached_resolution, _cache_timestamp
    async with _cache_lock:
        _cached_fingerprint = collect_fingerprint()
        _cached_resolution = resolve_compatibility(_cached_fingerprint)
        _cache_timestamp = time.monotonic()
    return _cached_fingerprint

@router.get("/resolve")
async def get_resolution():
    global _cached_fingerprint, _cached_resolution, _cache_timestamp
    async with _cache_lock:
        now = time.monotonic()
        if _cached_fingerprint is None or (now - _cache_timestamp) > _CACHE_TTL:
            _cached_fingerprint = collect_fingerprint()
            _cache_timestamp = now
        if _cached_resolution is None:
            _cached_resolution = resolve_compatibility(_cached_fingerprint)
    return _cached_resolution
