"""Phase 21 — tests for role_template_index.

Gate: index lookup by role returns matching templates.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.l0


# ---------------------------------------------------------------------------
# Imports
# ---------------------------------------------------------------------------

from service.isaac_assist_service.multimodal.role_template_index import (
    ROLE_TEMPLATE_INDEX,
    RoleTemplateEntry,
    RoleTemplateIndex,
    get_phase_metadata,
)


# ---------------------------------------------------------------------------
# Test: phase metadata
# ---------------------------------------------------------------------------

def test_phase_metadata_shape():
    meta = get_phase_metadata()
    assert meta["phase"] == "21"
    assert meta["status"] == "landed"
    assert isinstance(meta["entry_count"], int)
    assert meta["entry_count"] >= 30


# ---------------------------------------------------------------------------
# Test: registry size
# ---------------------------------------------------------------------------

def test_registry_has_at_least_30_entries():
    assert len(ROLE_TEMPLATE_INDEX) >= 30


# ---------------------------------------------------------------------------
# Test: required roles have minimum entry counts
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("role, min_count", [
    ("welder", 4),
    ("picker", 4),
    ("assembler", 3),
    ("inspector", 3),
    ("palletizer", 3),
    ("machine_tender", 3),
    ("packer", 3),
    ("AMR_driver", 3),
    ("dispenser", 2),
    ("kitter", 2),
])
def test_required_role_minimum_entries(role, min_count):
    idx = RoleTemplateIndex()
    entries = idx.by_role(role)
    assert len(entries) >= min_count, (
        f"Role '{role}' has {len(entries)} entries, expected ≥{min_count}"
    )


# ---------------------------------------------------------------------------
# Test: by_role exact match
# ---------------------------------------------------------------------------

def test_by_role_exact_match():
    idx = RoleTemplateIndex()
    welders = idx.by_role("welder")
    assert all(e.role == "welder" for e in welders)
    assert len(welders) >= 4


def test_by_role_unknown_returns_empty():
    idx = RoleTemplateIndex()
    assert idx.by_role("nonexistent_role_xyz") == []


# ---------------------------------------------------------------------------
# Test: by_robot_class filter
# ---------------------------------------------------------------------------

def test_by_robot_class_returns_matching_entries():
    idx = RoleTemplateIndex()
    results = idx.by_robot_class("ur10e")
    assert len(results) >= 1
    assert all(e.robot_class == "ur10e" for e in results)


def test_by_robot_class_unknown_returns_empty():
    idx = RoleTemplateIndex()
    assert idx.by_robot_class("nonexistent_robot_xyz") == []


# ---------------------------------------------------------------------------
# Test: by_tag filter
# ---------------------------------------------------------------------------

def test_by_tag_returns_entries_with_tag():
    idx = RoleTemplateIndex()
    results = idx.by_tag("welding")
    assert len(results) >= 4
    assert all("welding" in e.tags for e in results)


def test_by_tag_unknown_returns_empty():
    idx = RoleTemplateIndex()
    assert idx.by_tag("this_tag_does_not_exist_zzz") == []


# ---------------------------------------------------------------------------
# Test: all_roles sorted and unique
# ---------------------------------------------------------------------------

def test_all_roles_sorted_and_unique():
    idx = RoleTemplateIndex()
    roles = idx.all_roles()
    assert roles == sorted(set(roles)), "all_roles() must be sorted and deduplicated"
    # At minimum our 10 required roles must be present
    required = {
        "welder", "picker", "assembler", "inspector", "palletizer",
        "machine_tender", "packer", "AMR_driver", "dispenser", "kitter",
    }
    assert required.issubset(set(roles))


# ---------------------------------------------------------------------------
# Test: count_by_role correctness
# ---------------------------------------------------------------------------

def test_count_by_role_totals_match_registry():
    idx = RoleTemplateIndex()
    counts = idx.count_by_role()
    assert sum(counts.values()) == len(ROLE_TEMPLATE_INDEX)


def test_count_by_role_matches_by_role_len():
    idx = RoleTemplateIndex()
    for role, count in idx.count_by_role().items():
        assert len(idx.by_role(role)) == count


# ---------------------------------------------------------------------------
# Test: every entry has non-empty template_id and role
# ---------------------------------------------------------------------------

def test_every_entry_has_nonempty_template_id_and_role():
    for entry in ROLE_TEMPLATE_INDEX:
        assert entry.template_id, f"Empty template_id found: {entry}"
        assert entry.role, f"Empty role found: {entry}"


# ---------------------------------------------------------------------------
# Test: custom index uses supplied entries
# ---------------------------------------------------------------------------

def test_custom_entries_override_defaults():
    custom = [
        RoleTemplateEntry(
            template_id="CUSTOM-01",
            role="test_role",
            tags=["custom"],
        ),
    ]
    idx = RoleTemplateIndex(entries=custom)
    assert len(idx.by_role("test_role")) == 1
    assert idx.by_role("welder") == []


# ---------------------------------------------------------------------------
# Test: RoleTemplateEntry dataclass defaults
# ---------------------------------------------------------------------------

def test_entry_defaults():
    entry = RoleTemplateEntry(template_id="T-01", role="picker")
    assert entry.sub_role is None
    assert entry.robot_class is None
    assert entry.gripper is None
    assert entry.tags == []
    assert entry.notes == ""
