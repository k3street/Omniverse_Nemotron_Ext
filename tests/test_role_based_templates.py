"""Phase 20 — role-based template retrieval tests.

Gate: retrieve_with_roles returns role_based matches ahead of legacy.

pytest tests/test_role_based_templates.py
"""
import pytest
pytestmark = pytest.mark.l0

from service.isaac_assist_service.chat.tools.role_retriever import (
    TemplateMatch,
    RoleRetriever,
    fuzzy_score,
    get_phase_metadata,
    PHASE_STATUS,
)
from service.isaac_assist_service.multimodal.role_template_index import (
    RoleTemplateIndex,
    RoleTemplateEntry,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

PICKER_ENTRY = RoleTemplateEntry(
    template_id="TP-PCK-01",
    role="picker",
    sub_role="bin_picker",
    robot_class="franka_panda",
    gripper="parallel_jaw",
    tags=["picking", "bin", "disordered"],
    notes="Bin-picking test entry.",
)
WELDER_ENTRY = RoleTemplateEntry(
    template_id="TP-WLD-01",
    role="welder",
    sub_role="spot_welder",
    robot_class="fanuc_arc_mate",
    gripper="spot_welding_gun",
    tags=["welding", "spot"],
    notes="Spot welding test entry.",
)

LEGACY_TEMPLATES = [
    {"task_id": "LEGACY-01", "notes": "pick boxes from conveyor", "roles": None},
    {"task_id": "LEGACY-02", "notes": "weld automotive panels"},
]


def _make_retriever() -> RoleRetriever:
    idx = RoleTemplateIndex([PICKER_ENTRY, WELDER_ENTRY])
    return RoleRetriever(role_index=idx, legacy_templates=LEGACY_TEMPLATES)


# ---------------------------------------------------------------------------
# 1. Phase metadata
# ---------------------------------------------------------------------------

def test_phase_metadata_status():
    meta = get_phase_metadata()
    assert meta["status"] == "landed"
    assert meta["phase"] == "20"


def test_phase_status_constant():
    assert PHASE_STATUS == "landed"


# ---------------------------------------------------------------------------
# 2. fuzzy_score
# ---------------------------------------------------------------------------

def test_fuzzy_score_identical():
    assert fuzzy_score("pick boxes", "pick boxes") == 1.0


def test_fuzzy_score_no_overlap():
    assert fuzzy_score("welding", "navigation") == 0.0


def test_fuzzy_score_partial_overlap():
    score = fuzzy_score("pick boxes from bin", "bin picker robot")
    assert 0.0 < score < 1.0


def test_fuzzy_score_empty_query():
    assert fuzzy_score("", "something") == 0.0


def test_fuzzy_score_empty_text():
    assert fuzzy_score("something", "") == 0.0


# ---------------------------------------------------------------------------
# 3. retrieve_with_roles — role_hints exact match
# ---------------------------------------------------------------------------

def test_retrieve_with_roles_hint_picker_score_1():
    ret = _make_retriever()
    results = ret.retrieve_with_roles("anything", role_hints=["picker"])
    assert len(results) >= 1
    assert all(m.source == "role_based" for m in results if m.matched_role == "picker")
    assert all(m.match_score == 1.0 for m in results if m.matched_role == "picker")


def test_retrieve_with_roles_role_based_before_legacy():
    """Spec gate: role-based entries must rank before legacy entries."""
    ret = _make_retriever()
    results = ret.retrieve_with_roles("pick boxes", role_hints=["picker"])
    role_indices = [i for i, m in enumerate(results) if m.source == "role_based"]
    legacy_indices = [i for i, m in enumerate(results) if m.source == "legacy"]
    if role_indices and legacy_indices:
        assert max(role_indices) < min(legacy_indices), (
            "All role-based matches must appear before any legacy match"
        )


def test_retrieve_with_roles_max_results_respected():
    ret = _make_retriever()
    results = ret.retrieve_with_roles("pick weld bin box", max_results=2)
    assert len(results) <= 2


def test_retrieve_with_roles_unknown_role_no_role_based():
    ret = _make_retriever()
    results = ret.retrieve_with_roles("something", role_hints=["nonexistent_role_xyz"])
    role_based = [m for m in results if m.source == "role_based"]
    # With an unknown role hint there are no exact role matches;
    # fuzzy fallback may or may not produce role-based matches.
    # Guarantee: if legacy templates exist, we may still get some results.
    assert isinstance(results, list)
    # No exact-match role-based from the bad hint
    assert all(m.match_score < 1.0 for m in role_based)


# ---------------------------------------------------------------------------
# 4. retrieve_with_roles — fuzzy fallback (no role_hints)
# ---------------------------------------------------------------------------

def test_retrieve_with_roles_fuzzy_picking_returns_picker():
    """Fuzzy query using token 'picking' (present in PICKER_ENTRY tags) should match."""
    ret = _make_retriever()
    results = ret.retrieve_with_roles("picking from bin")
    matched_roles = [m.matched_role for m in results if m.source == "role_based"]
    assert "picker" in matched_roles


def test_retrieve_with_roles_no_role_hints_returns_list():
    ret = _make_retriever()
    results = ret.retrieve_with_roles("bin picker robot assembly")
    assert isinstance(results, list)
    assert len(results) > 0


# ---------------------------------------------------------------------------
# 5. score_template_against_query
# ---------------------------------------------------------------------------

def test_score_template_against_query_sane():
    ret = _make_retriever()
    score = ret.score_template_against_query("TP-PCK-01", "pck robot")
    assert 0.0 <= score <= 1.0


def test_score_template_against_query_empty_query():
    ret = _make_retriever()
    assert ret.score_template_against_query("TP-PCK-01", "") == 0.0


# ---------------------------------------------------------------------------
# 6. retrieve_legacy
# ---------------------------------------------------------------------------

def test_retrieve_legacy_returns_only_legacy():
    ret = _make_retriever()
    results = ret.retrieve_legacy("pick boxes")
    assert all(m.source == "legacy" for m in results)


def test_retrieve_legacy_max_results():
    ret = _make_retriever()
    results = ret.retrieve_legacy("something", max_results=1)
    assert len(results) <= 1


# ---------------------------------------------------------------------------
# 7. TemplateMatch dataclass round-trip
# ---------------------------------------------------------------------------

def test_template_match_dataclass_round_trip():
    match = TemplateMatch(
        template_id="TP-TEST-01",
        source="role_based",
        match_score=0.75,
        matched_role="picker",
        matched_tags=["picking", "bin"],
        notes="test note",
    )
    assert match.template_id == "TP-TEST-01"
    assert match.source == "role_based"
    assert match.match_score == 0.75
    assert match.matched_role == "picker"
    assert "picking" in match.matched_tags
    assert match.notes == "test note"


def test_template_match_defaults():
    match = TemplateMatch(
        template_id="X",
        source="legacy",
        match_score=0.0,
        matched_role=None,
    )
    assert match.matched_tags == []
    assert match.notes == ""


# ---------------------------------------------------------------------------
# 8. Retriever with no legacy templates
# ---------------------------------------------------------------------------

def test_retrieve_no_legacy_returns_role_based_only():
    idx = RoleTemplateIndex([PICKER_ENTRY])
    ret = RoleRetriever(role_index=idx, legacy_templates=[])
    results = ret.retrieve_with_roles("pick", role_hints=["picker"])
    assert len(results) >= 1
    assert all(m.source == "role_based" for m in results)


# ---------------------------------------------------------------------------
# 9. retrieve_with_roles with both role_hints present in index
# ---------------------------------------------------------------------------

def test_retrieve_with_roles_returns_results_for_welder():
    ret = _make_retriever()
    results = ret.retrieve_with_roles("weld steel", role_hints=["welder"])
    assert any(m.matched_role == "welder" for m in results)
    assert all(m.match_score == 1.0 for m in results if m.matched_role == "welder")
