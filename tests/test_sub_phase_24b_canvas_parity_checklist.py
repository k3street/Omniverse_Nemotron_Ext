"""Phase 24b contract tests — Canvas SPA interactive editing parity checklist.

Gate: pytest — checklist registry has 12 items, status tracker advances per
item, verification criteria define acceptance.
"""
from __future__ import annotations

import importlib

import pytest

pytestmark = pytest.mark.l0

# ---------------------------------------------------------------------------
# Import helper
# ---------------------------------------------------------------------------

MODULE = (
    "service.isaac_assist_service.multimodal.sub_phase_24b_canvas_parity_checklist"
)


def _import():
    return importlib.import_module(MODULE)


# ---------------------------------------------------------------------------
# T01 — metadata
# ---------------------------------------------------------------------------


def test_metadata():
    mod = _import()
    md = mod.get_phase_metadata()
    assert md["phase"] == "24b"
    assert md["status"] == "landed"
    assert "spec_ref" in md
    assert "24b" in md["spec_ref"]
    assert md["parity_items"] == 12


# ---------------------------------------------------------------------------
# T02 — PARITY_ITEMS has exactly 12 entries
# ---------------------------------------------------------------------------


def test_parity_items_has_exactly_12_entries():
    mod = _import()
    assert len(mod.PARITY_ITEMS) == 12


# ---------------------------------------------------------------------------
# T03 — all 4 categories are represented
# ---------------------------------------------------------------------------


def test_all_four_categories_represented():
    mod = _import()
    categories = {item.category for item in mod.PARITY_ITEMS}
    assert categories == {"interaction", "persistence", "ui_polish", "integration"}


# ---------------------------------------------------------------------------
# T04 — every item has at least 2 acceptance_criteria
# ---------------------------------------------------------------------------


def test_every_item_has_at_least_two_acceptance_criteria():
    mod = _import()
    for item in mod.PARITY_ITEMS:
        assert len(item.acceptance_criteria) >= 2, (
            f"Item {item.item_id} ({item.name!r}) has fewer than 2 acceptance_criteria"
        )


# ---------------------------------------------------------------------------
# T05 — item_ids are unique and span 1..12
# ---------------------------------------------------------------------------


def test_unique_item_ids_one_to_twelve():
    mod = _import()
    ids = [item.item_id for item in mod.PARITY_ITEMS]
    assert sorted(ids) == list(range(1, 13)), (
        f"Expected item_ids 1..12, got {sorted(ids)}"
    )


# ---------------------------------------------------------------------------
# T06 — mark_status advances an item
# ---------------------------------------------------------------------------


def test_mark_status_advances_item():
    mod = _import()
    tracker = mod.CanvasParityTracker()
    assert tracker.get_item(1).status == "scaffold"
    tracker.mark_status(1, "wip", landed_in_phase="24b", notes="in progress")
    item = tracker.get_item(1)
    assert item.status == "wip"
    assert item.landed_in_phase == "24b"
    assert item.notes == "in progress"


# ---------------------------------------------------------------------------
# T07 — by_status filters correctly
# ---------------------------------------------------------------------------


def test_by_status_filters_correctly():
    mod = _import()
    tracker = mod.CanvasParityTracker()
    # Initially everything is "scaffold"
    scaffolded = tracker.by_status("scaffold")
    assert len(scaffolded) == 12
    tracker.mark_status(3, "landed")
    scaffolded_after = tracker.by_status("scaffold")
    landed = tracker.by_status("landed")
    assert len(scaffolded_after) == 11
    assert len(landed) == 1
    assert landed[0].item_id == 3


# ---------------------------------------------------------------------------
# T08 — by_category filters correctly
# ---------------------------------------------------------------------------


def test_by_category_filters_correctly():
    mod = _import()
    tracker = mod.CanvasParityTracker()
    interaction_items = tracker.by_category("interaction")
    persistence_items = tracker.by_category("persistence")
    integration_items = tracker.by_category("integration")
    ui_polish_items = tracker.by_category("ui_polish")
    # All 12 items must be accounted for across the four categories
    total = (
        len(interaction_items)
        + len(persistence_items)
        + len(integration_items)
        + len(ui_polish_items)
    )
    assert total == 12
    # Spot-check: item 6 and 7 are persistence
    persistence_ids = {i.item_id for i in persistence_items}
    assert 6 in persistence_ids
    assert 7 in persistence_ids


# ---------------------------------------------------------------------------
# T09 — progress dict has correct keys and sums
# ---------------------------------------------------------------------------


def test_progress_dict_has_correct_keys():
    mod = _import()
    tracker = mod.CanvasParityTracker()
    p = tracker.progress()
    for key in ("total", "scaffold", "wip", "landed", "verified", "pct_landed"):
        assert key in p, f"Missing key {key!r} in progress dict"
    assert p["total"] == 12
    assert p["scaffold"] == 12
    assert p["wip"] == 0
    assert p["landed"] == 0
    assert p["verified"] == 0
    assert p["pct_landed"] == 0.0


def test_progress_updates_after_status_changes():
    mod = _import()
    tracker = mod.CanvasParityTracker()
    tracker.mark_status(1, "landed")
    tracker.mark_status(2, "verified")
    tracker.mark_status(3, "wip")
    p = tracker.progress()
    assert p["scaffold"] == 9
    assert p["wip"] == 1
    assert p["landed"] == 1
    assert p["verified"] == 1
    # pct_landed = (landed + verified) / total * 100 = 2/12 * 100 ≈ 16.7
    assert p["pct_landed"] == pytest.approx(16.7, abs=0.1)


# ---------------------------------------------------------------------------
# T10 — report returns a markdown table containing all 12 items
# ---------------------------------------------------------------------------


def test_report_returns_markdown_with_all_12_items():
    mod = _import()
    tracker = mod.CanvasParityTracker()
    report = tracker.report()
    assert isinstance(report, str)
    # Every item name must appear in the report
    for item in mod.PARITY_ITEMS:
        assert item.name in report, (
            f"Item {item.item_id} name {item.name!r} missing from report"
        )
    # Must have a markdown table header
    assert "|" in report


# ---------------------------------------------------------------------------
# T11 — verify + verification_status round-trip
# ---------------------------------------------------------------------------


def test_verify_and_verification_status_round_trip():
    mod = _import()
    tracker = mod.CanvasParityTracker()
    # Initially empty
    assert tracker.verification_status(1) == {}
    tracker.verify(1, 0, True)
    tracker.verify(1, 1, False)
    vs = tracker.verification_status(1)
    assert vs[0] is True
    assert vs[1] is False


def test_verify_out_of_range_raises():
    mod = _import()
    tracker = mod.CanvasParityTracker()
    with pytest.raises(IndexError):
        tracker.verify(1, 999, True)


# ---------------------------------------------------------------------------
# T12 — get_item unknown returns None
# ---------------------------------------------------------------------------


def test_get_item_unknown_returns_none():
    mod = _import()
    tracker = mod.CanvasParityTracker()
    assert tracker.get_item(999) is None
    assert tracker.get_item(0) is None


# ---------------------------------------------------------------------------
# T13 — mark_status unknown item raises KeyError
# ---------------------------------------------------------------------------


def test_mark_status_unknown_item_raises():
    mod = _import()
    tracker = mod.CanvasParityTracker()
    with pytest.raises(KeyError):
        tracker.mark_status(999, "landed")


# ---------------------------------------------------------------------------
# T14 — expected_categories returns all four categories
# ---------------------------------------------------------------------------


def test_expected_categories():
    mod = _import()
    cats = set(mod.expected_categories())
    assert cats == {"interaction", "persistence", "ui_polish", "integration"}


# ---------------------------------------------------------------------------
# T15 — custom items list in constructor is respected
# ---------------------------------------------------------------------------


def test_custom_items_list_in_constructor():
    mod = _import()
    custom = [
        mod.ParityItem(
            item_id=42,
            name="Custom item",
            description="Test",
            category="ui_polish",
            acceptance_criteria=["Criterion A", "Criterion B"],
        )
    ]
    tracker = mod.CanvasParityTracker(items=custom)
    assert tracker.get_item(42) is not None
    assert tracker.get_item(1) is None
    p = tracker.progress()
    assert p["total"] == 1


# ---------------------------------------------------------------------------
# T16 — module-level PARITY_ITEMS not mutated by tracker operations
# ---------------------------------------------------------------------------


def test_module_parity_items_not_mutated():
    mod = _import()
    original_status = mod.PARITY_ITEMS[0].status
    tracker = mod.CanvasParityTracker()
    tracker.mark_status(1, "verified")
    # Module-level list should be unchanged
    assert mod.PARITY_ITEMS[0].status == original_status
