"""Phase 73 — sensor catalog inventory and expansion validation."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List

PHASE_ID = 73
PHASE_TITLE = "Sensor catalog expansion to 100+"
PHASE_STATUS = "landed"

DEFAULT_CATALOG_PATH = (
    Path(__file__).resolve().parents[3]
    / "workspace"
    / "knowledge"
    / "sensor_specs.jsonl"
)

MINIMUM_COUNTS = {
    "total": 100,
    "camera": 30,
    "lidar": 20,
    "force_torque_sensor": 20,
    "sensor": 15,
}


def get_phase_metadata() -> Dict[str, Any]:
    return {
        "phase": PHASE_ID,
        "title": PHASE_TITLE,
        "status": PHASE_STATUS,
        "spec_ref": "specs/IA_FULL_SPEC_2026-05-10.md Phase 73",
        "catalog_path": str(DEFAULT_CATALOG_PATH),
    }


def load_sensor_catalog(path: Path = DEFAULT_CATALOG_PATH) -> List[Dict[str, Any]]:
    """Load non-empty JSONL records and fail with the offending line number."""
    records: List[Dict[str, Any]] = []
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip():
            continue
        try:
            record = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid sensor catalog JSON at line {line_number}") from exc
        if not isinstance(record, dict):
            raise ValueError(f"sensor catalog line {line_number} is not an object")
        records.append(record)
    return records


def catalog_counts(records: Iterable[Dict[str, Any]]) -> Dict[str, int]:
    """Return total and per-type counts for catalog release gates."""
    items = list(records)
    counts = Counter(str(item.get("type", "")) for item in items)
    return {"total": len(items), **dict(sorted(counts.items()))}


def validate_sensor_catalog(
    records: Iterable[Dict[str, Any]],
    minimum_counts: Dict[str, int] = MINIMUM_COUNTS,
) -> List[str]:
    """Return deterministic validation errors; an empty list means releasable."""
    items = list(records)
    errors: List[str] = []
    counts = catalog_counts(items)
    for category, minimum in minimum_counts.items():
        actual = counts.get(category, 0)
        if actual < minimum:
            errors.append(f"{category}: expected >= {minimum}, got {actual}")

    names: set[str] = set()
    for index, item in enumerate(items, 1):
        for field in ("product", "type", "manufacturer"):
            if not item.get(field):
                errors.append(f"record {index}: missing {field}")
        name = str(item.get("product", ""))
        if name and name in names:
            errors.append(f"duplicate product: {name}")
        names.add(name)
    return errors
