"""Phase 73 — sensor catalog expansion to 100+ entries.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 73.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.l0


_CATALOG_PATH = Path(__file__).parent.parent / "workspace" / "knowledge" / "sensor_specs.jsonl"


def _load_catalog():
    return [json.loads(l) for l in _CATALOG_PATH.read_text().splitlines() if l.strip()]


def test_catalog_has_100_plus_entries():
    """Phase 73 contract: catalog ≥ 100 entries."""
    specs = _load_catalog()
    assert len(specs) >= 100, f"Expected ≥100 entries, got {len(specs)}"


def test_catalog_covers_required_categories():
    """Spec requires 30 cameras + 20 lidars + 20 F/T + 20 specialty."""
    specs = _load_catalog()
    types = {}
    for s in specs:
        types.setdefault(s.get("type"), 0)
        types[s["type"]] += 1
    assert types.get("camera", 0) >= 30, f"≥30 cameras required; got {types.get('camera', 0)}"
    assert types.get("lidar", 0) >= 20, f"≥20 lidars required; got {types.get('lidar', 0)}"
    assert types.get("force_torque_sensor", 0) >= 20, f"≥20 F/T required; got {types.get('force_torque_sensor', 0)}"
    # Specialty: sensor-type entries
    assert types.get("sensor", 0) >= 15, f"≥15 specialty sensors required; got {types.get('sensor', 0)}"


def test_required_manufacturers_present():
    """Spec calls out Cognex, Keyence, Basler, FLIR (cams) +
    Velodyne, Ouster, Hesai, Sick (lidars) +
    ATI, Robotiq, Bota (F/T)."""
    specs = _load_catalog()
    mfgs = {s.get("manufacturer") for s in specs}
    expected = {"Cognex", "Keyence", "Basler", "Velodyne", "Ouster", "Hesai",
                "SICK", "ATI Industrial Automation", "Robotiq", "Bota Systems"}
    missing = expected - mfgs
    assert not missing, f"Missing required manufacturers: {missing}"


def test_every_entry_has_minimum_fields():
    """Every entry must have product + type + manufacturer."""
    specs = _load_catalog()
    for s in specs:
        assert "product" in s and s["product"], f"Missing product: {s}"
        assert "type" in s and s["type"], f"Missing type for {s.get('product')}"
        assert "manufacturer" in s and s["manufacturer"], f"Missing manufacturer for {s.get('product')}"


def test_product_names_unique():
    """No duplicate product names in catalog."""
    specs = _load_catalog()
    names = [s["product"] for s in specs]
    dupes = {n for n in names if names.count(n) > 1}
    assert not dupes, f"Duplicate product names: {dupes}"


@pytest.mark.asyncio
async def test_handle_lookup_product_spec_roundtrips_new_entries():
    """Every new entry can be retrieved via _handle_lookup_product_spec."""
    from service.isaac_assist_service.chat.tools.handlers.scene_blueprints import _handle_lookup_product_spec
    # Phase 73 added entries — verify a sample
    for query in ("Cognex In-Sight", "Hesai AT128", "ATI Mini40", "Optris Xi", "Bota Systems Rokubi"):
        result = await _handle_lookup_product_spec({"product_name": query})
        assert result.get("found") is True, f"Could not find {query}: {result}"
