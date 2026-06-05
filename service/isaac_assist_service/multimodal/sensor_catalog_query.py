"""Phase 74 — Sensor catalog query: structured filter.

Adds structured filter support to the sensor catalog lookup so callers
can narrow results by numeric range, resolution, fps, manufacturer,
type, and subtype without hand-rolling fuzzy logic.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 74.
"""
from __future__ import annotations

import functools
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Phase metadata
# ---------------------------------------------------------------------------

PHASE_ID = 74
PHASE_TITLE = "Sensor catalog query: structured filter"
PHASE_STATUS = "landed"

_CATALOG_PATH = Path(__file__).resolve().parent.parent.parent.parent / (
    "workspace/knowledge/sensor_specs.jsonl"
)

def get_phase_metadata() -> Dict[str, Any]:
    """Return phase metadata for spec-coverage audits."""
    return {
        "phase": PHASE_ID,
        "title": PHASE_TITLE,
        "status": PHASE_STATUS,
        "spec_ref": "specs/IA_FULL_SPEC_2026-05-10.md Phase 74",
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

@functools.lru_cache(maxsize=1)
def _load_catalog() -> List[Dict]:
    """Load and cache the sensor catalog from disk."""
    catalog: List[Dict] = []
    if _CATALOG_PATH.exists():
        for line in _CATALOG_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                catalog.append(json.loads(line))
    return catalog


def _text_score(sensor: Dict, query: str) -> int:
    """
    Return a relevance score for *sensor* against *query*.

    Words in the query are matched against the product name, type, and
    subtype fields.  Each matching word contributes 1 point; an exact
    full-string match of the query against any field contributes an
    extra 5 points.
    """
    query_lower = query.lower()
    query_words = query_lower.split()

    product = sensor.get("product", "").lower()
    stype = sensor.get("type", "").lower()
    subtype = sensor.get("subtype", "").lower()
    searchable = f"{product} {stype} {subtype}"

    score = 0
    for word in query_words:
        if word in searchable:
            score += 1
    if query_lower in searchable:
        score += 5
    return score


def _get_range(sensor: Dict) -> Optional[List[float]]:
    """Return the sensor's effective detection range as [min_m, max_m], or None."""
    r = sensor.get("depth_range_m") or sensor.get("range_m")
    if r is None:
        return None
    if isinstance(r, (int, float)):
        # Scalar range: treat as [0, r]
        return [0.0, float(r)]
    if isinstance(r, list) and len(r) == 2:
        return [float(r[0]), float(r[1])]
    return None


def _apply_numeric_filters(sensor: Dict, filters: Dict) -> bool:
    """
    Return True if *sensor* passes all numeric and categorical filters.

    Filter semantics
    ----------------
    min_range_m   : sensor must be able to detect *at* this distance — i.e.
                    sensor_range_min <= min_range_m (no blind zone past here).
    max_range_m   : sensor must reach *at least* this far —
                    sensor_range_max >= max_range_m.
    min_resolution: sensor resolution must be [w, h] where
                    sensor_w >= w AND sensor_h >= h.
    min_fps       : sensor fps >= min_fps.
    manufacturer  : case-insensitive substring match.
    type          : exact match (camera / lidar / imu / gripper / …).
    subtype       : exact match (depth_stereo / rotating_lidar / …).
    """
    if not filters:
        return True

    # ---- range filters -------------------------------------------------
    sensor_range = _get_range(sensor)

    min_range_m = filters.get("min_range_m")
    if min_range_m is not None:
        if sensor_range is None:
            return False  # range required but not present
        # sensor must be usable at min_range_m: its closest measurement point
        # must be at or before min_range_m
        if sensor_range[0] > float(min_range_m):
            return False

    max_range_m = filters.get("max_range_m")
    if max_range_m is not None:
        if sensor_range is None:
            return False  # range required but not present
        # sensor must reach at least max_range_m
        if sensor_range[1] < float(max_range_m):
            return False

    # ---- resolution filter ---------------------------------------------
    min_res = filters.get("min_resolution")
    if min_res is not None:
        res = sensor.get("resolution") or sensor.get("depth_resolution")
        if res is None or len(res) < 2:
            return False
        if res[0] < min_res[0] or res[1] < min_res[1]:
            return False

    # ---- fps filter ----------------------------------------------------
    min_fps = filters.get("min_fps")
    if min_fps is not None:
        fps = sensor.get("fps")
        if fps is None or fps < float(min_fps):
            return False

    # ---- categorical filters -------------------------------------------
    manufacturer = filters.get("manufacturer")
    if manufacturer is not None:
        sensor_mfr = sensor.get("manufacturer", "")
        if manufacturer.lower() not in sensor_mfr.lower():
            return False

    type_filter = filters.get("type")
    if type_filter is not None:
        if sensor.get("type", "") != type_filter:
            return False

    subtype_filter = filters.get("subtype")
    if subtype_filter is not None:
        if sensor.get("subtype", "") != subtype_filter:
            return False

    return True


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def query_sensors(
    query: str,
    filters: Optional[Dict[str, Any]] = None,
    *,
    min_score: int = 1,
    limit: int = 20,
) -> List[Dict]:
    """Query the sensor catalog with optional structured filters.

    Parameters
    ----------
    query:
        Free-text description, e.g. ``"depth camera"`` or ``"rotating lidar"``.
        Results are ranked by text relevance.
    filters:
        Optional dict with zero or more of the following keys:

        ``min_range_m``    – sensor's minimum detection distance must be ≤ this
        ``max_range_m``    – sensor's maximum detection distance must be ≥ this
        ``min_resolution`` – ``[width, height]`` both dimensions must be met
        ``min_fps``        – sensor fps must be ≥ this
        ``manufacturer``   – case-insensitive substring match
        ``type``           – exact match (``"camera"``, ``"lidar"``, etc.)
        ``subtype``        – exact match (``"depth_stereo"``, etc.)

    min_score:
        Minimum text relevance score; sensors scoring below this are excluded
        even if they pass all numeric filters.  Defaults to 1.
    limit:
        Maximum number of results returned (default 20).

    Returns
    -------
    list[dict]
        Matching sensor records sorted by descending relevance score.
    """
    catalog = _load_catalog()
    results: List[tuple[int, Dict]] = []

    for sensor in catalog:
        score = _text_score(sensor, query)
        if score < min_score:
            continue
        if not _apply_numeric_filters(sensor, filters or {}):
            continue
        results.append((score, sensor))

    results.sort(key=lambda t: -t[0])
    return [s for _, s in results[:limit]]
