"""Tests for Phase 70d — bin interior metadata loader.

Gate: bin metadata loader returns interior dimensions for 5+ bin SKUs.
"""
from __future__ import annotations

import pytest
from pathlib import Path

from service.isaac_assist_service.multimodal.sub_phase_70d_bin_interior_metadata import (
    BinMetadataLoader,
    BinSpec,
    get_phase_metadata,
    PHASE_STATUS,
)

pytestmark = pytest.mark.l0


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def loader() -> BinMetadataLoader:
    return BinMetadataLoader()


@pytest.fixture(scope="module")
def registry(loader: BinMetadataLoader):
    return loader.load()


# ---------------------------------------------------------------------------
# Test 1 — phase metadata
# ---------------------------------------------------------------------------

def test_phase_metadata_landed():
    meta = get_phase_metadata()
    assert meta["phase"] == "70d"
    assert meta["status"] == "landed"
    assert PHASE_STATUS == "landed"


# ---------------------------------------------------------------------------
# Test 2 — load returns 5+ entries
# ---------------------------------------------------------------------------

def test_load_returns_at_least_five_skus(registry):
    assert len(registry) >= 5, f"Expected ≥5 SKUs, got {len(registry)}"


# ---------------------------------------------------------------------------
# Test 3 — get(known sku) returns correct interior dimensions
# ---------------------------------------------------------------------------

def test_get_known_sku_schaefer(loader):
    spec = loader.get("SCHAEFER-LF6280")
    assert spec is not None, "SCHAEFER-LF6280 must be present in registry"
    assert isinstance(spec, BinSpec)
    assert spec.interior_mm[0] == pytest.approx(565.0)
    assert spec.interior_mm[1] == pytest.approx(366.0)
    assert spec.interior_mm[2] == pytest.approx(261.0)
    assert spec.max_payload_kg == pytest.approx(50.0)
    assert spec.stackable is True
    assert spec.opening_orientation == "top"


# ---------------------------------------------------------------------------
# Test 4 — list_skus returns all expected SKUs
# ---------------------------------------------------------------------------

EXPECTED_SKUS = {
    "AKRO-MILS-30220",
    "QUANTUM-QSB-211",
    "LEWISBINS-NEX2415-6",
    "SCHAEFER-LF6280",
    "STEEL-TOTE-241612",
    "AKRO-MILS-39105",
    "QUANTUM-DG93000",
}


def test_list_skus_complete(loader):
    skus = set(loader.list_skus())
    assert EXPECTED_SKUS.issubset(skus), (
        f"Missing SKUs: {EXPECTED_SKUS - skus}"
    )


# ---------------------------------------------------------------------------
# Test 5 — find_by_payload filter
# ---------------------------------------------------------------------------

def test_find_by_payload_high_threshold(loader):
    # Only heavy-duty bins should qualify at 100 kg
    results = loader.find_by_payload(min_kg=100.0)
    assert len(results) >= 1
    for spec in results:
        assert spec.max_payload_kg >= 100.0


def test_find_by_payload_low_threshold_returns_all(loader, registry):
    results = loader.find_by_payload(min_kg=0.0)
    assert len(results) == len(registry)
    # Must be sorted ascending by payload
    payloads = [s.max_payload_kg for s in results]
    assert payloads == sorted(payloads)


# ---------------------------------------------------------------------------
# Test 6 — find_by_interior_min filter
# ---------------------------------------------------------------------------

def test_find_by_interior_min_large_request(loader):
    # Request bins with at least 550mm wide x 350mm deep x 200mm tall
    results = loader.find_by_interior_min(w_mm=550.0, d_mm=350.0, h_mm=200.0)
    assert len(results) >= 1
    for spec in results:
        assert spec.interior_w >= 550.0
        assert spec.interior_d >= 350.0
        assert spec.interior_h >= 200.0


def test_find_by_interior_min_impossible_request(loader):
    # Request larger than any bin — must return empty list
    results = loader.find_by_interior_min(w_mm=10_000.0, d_mm=10_000.0, h_mm=10_000.0)
    assert results == []


# ---------------------------------------------------------------------------
# Test 7 — get(unknown sku) returns None
# ---------------------------------------------------------------------------

def test_get_unknown_sku_returns_none(loader):
    result = loader.get("THIS-SKU-DOES-NOT-EXIST")
    assert result is None


# ---------------------------------------------------------------------------
# Test 8 — BinSpec convenience accessors
# ---------------------------------------------------------------------------

def test_binspec_interior_accessors(loader):
    spec = loader.get("STEEL-TOTE-241612")
    assert spec is not None
    assert spec.interior_w == pytest.approx(spec.interior_mm[0])
    assert spec.interior_d == pytest.approx(spec.interior_mm[1])
    assert spec.interior_h == pytest.approx(spec.interior_mm[2])
    assert spec.stackable is False
