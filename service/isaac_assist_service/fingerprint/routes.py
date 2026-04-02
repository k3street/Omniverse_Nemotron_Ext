from fastapi import APIRouter
from .collector import collect_fingerprint
from .compatibility import resolve_compatibility

router = APIRouter()

# Simple mock cache
_cached_fingerprint = None
_cached_resolution = None

@router.get("/collect")
def get_fingerprint():
    """ Returns the cached fingerprint """
    global _cached_fingerprint
    if not _cached_fingerprint:
        _cached_fingerprint = collect_fingerprint()
    return _cached_fingerprint

@router.post("/collect")
def force_refresh_fingerprint():
    """ Forces a hardware re-scan """
    global _cached_fingerprint, _cached_resolution
    _cached_fingerprint = collect_fingerprint()
    _cached_resolution = resolve_compatibility(_cached_fingerprint)
    return _cached_fingerprint

@router.get("/resolve")
def get_resolution():
    global _cached_fingerprint, _cached_resolution
    if not _cached_fingerprint:
        _cached_fingerprint = collect_fingerprint()
    if not _cached_resolution:
        _cached_resolution = resolve_compatibility(_cached_fingerprint)
    return _cached_resolution
