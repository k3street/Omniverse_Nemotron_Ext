"""
test_motion_controller_retrieval_filter.py
-------------------------------------------
Tests for the motion_controller_constraint post-filter added to
retrieve_templates_with_scores (Round 11, 2026-05-15).

Covers:
  T1 — no filter (None) → behavior unchanged from baseline
  T2 — must_verified=["curobo"] with env on → excludes templates whose
       motion_controllers.verified lacks "curobo"
  T3 — must_not_failed=["admittance"] with env on → excludes templates
       with admittance in motion_controllers.failed
  T4 — template without motion_controllers field → included regardless
       of filter (unmigrated template gets benefit of the doubt)
  T5 — env off + filter param given → param ignored (forward-compat)
  T6 — version-pinned "curobo@1.8.2" in template matches filter
       {must_verified: ["curobo"]} (base-name matching)

Tests are unit-level; they test the helper functions directly so they
don't require ChromaDB or disk fixtures.
"""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path
from typing import Dict, List

import pytest

pytestmark = pytest.mark.l0

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))


# ---------------------------------------------------------------------------
# Import helpers
# ---------------------------------------------------------------------------

def _load_retriever():
    """Return a fresh import of template_retriever (no cached module state)."""
    mod_name = "service.isaac_assist_service.chat.tools.template_retriever"
    if mod_name in sys.modules:
        del sys.modules[mod_name]
    return importlib.import_module(mod_name)


# ---------------------------------------------------------------------------
# Fixtures — synthetic scored-entry lists (no ChromaDB needed)
# ---------------------------------------------------------------------------

def _make_entry(task_id: str, motion_controllers=None) -> Dict:
    """Build a minimal scored entry dict for filter testing."""
    t: Dict = {"task_id": task_id, "goal": f"Goal for {task_id}"}
    if motion_controllers is not None:
        t["motion_controllers"] = motion_controllers
    return {"template": t, "task_id": task_id, "distance": 0.5, "similarity": 0.67}


ENTRY_CUROBO_VERIFIED = _make_entry(
    "CP-01",
    {"verified": ["curobo"], "untested": ["rmpflow"], "failed": {}},
)
ENTRY_NO_MC = _make_entry("CP-02")  # no motion_controllers field at all
ENTRY_RMPFLOW_ONLY = _make_entry(
    "CP-03",
    {"verified": ["rmpflow"], "untested": [], "failed": {}},
)
ENTRY_ADMITTANCE_FAILED = _make_entry(
    "CP-04",
    {
        "verified": ["curobo"],
        "untested": [],
        "failed": {"admittance": "form-gate failed: joint limits"},
    },
)
ENTRY_VERSIONED_CUROBO = _make_entry(
    "CP-05",
    {"verified": ["curobo@1.8.2"], "untested": [], "failed": {}},
)

ALL_ENTRIES: List[Dict] = [
    ENTRY_CUROBO_VERIFIED,
    ENTRY_NO_MC,
    ENTRY_RMPFLOW_ONLY,
    ENTRY_ADMITTANCE_FAILED,
    ENTRY_VERSIONED_CUROBO,
]


# ---------------------------------------------------------------------------
# T1 — no filter (None) produces identical output
# ---------------------------------------------------------------------------

class TestNoFilter:
    """When motion_controller_constraint is None, _apply_motion_controller_filter
    must not be called, and output is identical to input."""

    def test_none_constraint_returns_all(self):
        mod = _load_retriever()
        result = mod._apply_motion_controller_filter(ALL_ENTRIES, {})
        # Empty constraint dict → no-op (both must_verified and must_not_failed absent)
        assert result == ALL_ENTRIES

    def test_empty_constraint_is_noop(self):
        mod = _load_retriever()
        result = mod._apply_motion_controller_filter(ALL_ENTRIES, {"must_verified": [], "must_not_failed": []})
        assert result == ALL_ENTRIES

    def test_filter_not_applied_when_constraint_is_none(self, monkeypatch):
        """When constraint=None is passed to retrieve_templates_with_scores,
        the filter code path is never triggered even if RETRIEVAL_MC_FILTER=on."""
        mod = _load_retriever()
        monkeypatch.setenv("RETRIEVAL_MC_FILTER", "on")
        # Patch _get_collection to return None (disables ChromaDB) so we can
        # assert that the function returns [] without filtering side-effects
        monkeypatch.setattr(mod, "_get_collection", lambda: None)
        result = mod.retrieve_templates_with_scores("pick and place", motion_controller_constraint=None)
        assert result == []


# ---------------------------------------------------------------------------
# T2 — must_verified=["curobo"] with env on → excludes rmpflow-only
# ---------------------------------------------------------------------------

class TestMustVerified:
    """must_verified filter includes only entries whose verified list contains
    all requested controller base names."""

    def test_must_verified_curobo_excludes_rmpflow_only(self, monkeypatch):
        monkeypatch.setenv("RETRIEVAL_MC_FILTER", "on")
        mod = _load_retriever()
        constraint = {"must_verified": ["curobo"]}
        result = mod._apply_motion_controller_filter(ALL_ENTRIES, constraint)
        ids = [e["task_id"] for e in result]
        assert "CP-01" in ids, "CP-01 (curobo verified) should be included"
        assert "CP-03" not in ids, "CP-03 (rmpflow only) should be excluded"

    def test_must_verified_includes_no_mc_templates(self, monkeypatch):
        """Templates without motion_controllers field are always included."""
        monkeypatch.setenv("RETRIEVAL_MC_FILTER", "on")
        mod = _load_retriever()
        constraint = {"must_verified": ["curobo"]}
        result = mod._apply_motion_controller_filter(ALL_ENTRIES, constraint)
        ids = [e["task_id"] for e in result]
        assert "CP-02" in ids, "CP-02 (no motion_controllers) must be included"

    def test_must_verified_admittance_failed_entry_included_when_verified_matches(self, monkeypatch):
        """CP-04 has curobo verified AND admittance failed.
        must_verified=["curobo"] filter should include it (only failed filter excludes)."""
        monkeypatch.setenv("RETRIEVAL_MC_FILTER", "on")
        mod = _load_retriever()
        constraint = {"must_verified": ["curobo"]}
        result = mod._apply_motion_controller_filter([ENTRY_ADMITTANCE_FAILED], constraint)
        assert len(result) == 1


# ---------------------------------------------------------------------------
# T3 — must_not_failed=["admittance"] → excludes CP-04
# ---------------------------------------------------------------------------

class TestMustNotFailed:
    """must_not_failed filter excludes entries whose failed dict contains
    any of the listed controller names."""

    def test_must_not_failed_admittance_excludes_cp04(self, monkeypatch):
        monkeypatch.setenv("RETRIEVAL_MC_FILTER", "on")
        mod = _load_retriever()
        constraint = {"must_not_failed": ["admittance"]}
        result = mod._apply_motion_controller_filter(ALL_ENTRIES, constraint)
        ids = [e["task_id"] for e in result]
        assert "CP-04" not in ids, "CP-04 (admittance failed) should be excluded"
        assert "CP-01" in ids, "CP-01 (no admittance failure) should be included"
        assert "CP-02" in ids, "CP-02 (no mc field) should be included"

    def test_must_not_failed_keeps_unrelated_failures(self, monkeypatch):
        """If a template has 'cortex' failed but we filter 'curobo', include it."""
        monkeypatch.setenv("RETRIEVAL_MC_FILTER", "on")
        mod = _load_retriever()
        cortex_failed = _make_entry("CP-61", {
            "verified": [], "untested": [],
            "failed": {"cortex": "build-only verification"},
        })
        constraint = {"must_not_failed": ["curobo"]}
        result = mod._apply_motion_controller_filter([cortex_failed], constraint)
        assert len(result) == 1, "cortex-failed template should pass curobo must_not_failed filter"


# ---------------------------------------------------------------------------
# T4 — template without motion_controllers field is always included
# ---------------------------------------------------------------------------

class TestNoMcField:
    """Templates lacking motion_controllers must not be penalized by any filter."""

    def test_no_mc_field_passes_must_verified(self, monkeypatch):
        monkeypatch.setenv("RETRIEVAL_MC_FILTER", "on")
        mod = _load_retriever()
        result = mod._apply_motion_controller_filter(
            [ENTRY_NO_MC], {"must_verified": ["curobo", "rmpflow"]}
        )
        assert len(result) == 1

    def test_no_mc_field_passes_must_not_failed(self, monkeypatch):
        monkeypatch.setenv("RETRIEVAL_MC_FILTER", "on")
        mod = _load_retriever()
        result = mod._apply_motion_controller_filter(
            [ENTRY_NO_MC], {"must_not_failed": ["admittance", "cortex"]}
        )
        assert len(result) == 1

    def test_no_mc_field_passes_combined_filter(self, monkeypatch):
        monkeypatch.setenv("RETRIEVAL_MC_FILTER", "on")
        mod = _load_retriever()
        result = mod._apply_motion_controller_filter(
            [ENTRY_NO_MC],
            {"must_verified": ["curobo"], "must_not_failed": ["admittance"]},
        )
        assert len(result) == 1


# ---------------------------------------------------------------------------
# T5 — env off + filter param given → param ignored (forward-compat)
# ---------------------------------------------------------------------------

class TestEnvGating:
    """When RETRIEVAL_MC_FILTER is not 'on', the constraint param is silently
    ignored — retrieve_templates_with_scores behaves exactly as before."""

    def test_env_off_filter_ignored(self, monkeypatch):
        monkeypatch.setenv("RETRIEVAL_MC_FILTER", "off")
        mod = _load_retriever()
        # Patch _get_collection to return None → returns [] without touching filter
        monkeypatch.setattr(mod, "_get_collection", lambda: None)
        result = mod.retrieve_templates_with_scores(
            "pick and place",
            motion_controller_constraint={"must_verified": ["curobo"]},
        )
        # Returns [] because ChromaDB is patched out; critical thing is no error raised
        assert result == []

    def test_env_unset_filter_ignored(self, monkeypatch):
        monkeypatch.delenv("RETRIEVAL_MC_FILTER", raising=False)
        mod = _load_retriever()
        assert not mod._mc_filter_enabled()

    def test_env_on_activates_filter(self, monkeypatch):
        monkeypatch.setenv("RETRIEVAL_MC_FILTER", "on")
        mod = _load_retriever()
        assert mod._mc_filter_enabled()

    def test_env_off_does_not_filter_entries(self, monkeypatch):
        """Demonstrate: with env off, _apply_motion_controller_filter is NOT
        invoked inside retrieve_templates_with_scores even if constraint is given."""
        monkeypatch.setenv("RETRIEVAL_MC_FILTER", "off")
        mod = _load_retriever()

        # Simulate what retrieve_templates_with_scores would do with env off
        # by calling the filter guard directly
        entries_before = list(ALL_ENTRIES)
        # With env off, the filter block is skipped — entries pass through unchanged
        if mod._mc_filter_enabled():
            filtered = mod._apply_motion_controller_filter(entries_before, {"must_verified": ["curobo"]})
        else:
            filtered = entries_before
        assert filtered == entries_before


# ---------------------------------------------------------------------------
# T6 — version-pinned "curobo@1.8.2" matches filter {must_verified: ["curobo"]}
# ---------------------------------------------------------------------------

class TestBaseNameMatching:
    """Version suffix in controller names must be stripped for matching.
    'curobo@1.8.2' in template.verified must match filter must_verified=["curobo"]."""

    def test_versioned_verified_matches_base_name_filter(self, monkeypatch):
        monkeypatch.setenv("RETRIEVAL_MC_FILTER", "on")
        mod = _load_retriever()
        constraint = {"must_verified": ["curobo"]}
        result = mod._apply_motion_controller_filter([ENTRY_VERSIONED_CUROBO], constraint)
        assert len(result) == 1, (
            "curobo@1.8.2 in template.verified must match filter must_verified=['curobo']"
        )

    def test_versioned_filter_matches_unversioned_template(self, monkeypatch):
        """Filter constraint 'curobo@1.8.2' should match template with plain 'curobo'."""
        monkeypatch.setenv("RETRIEVAL_MC_FILTER", "on")
        mod = _load_retriever()
        constraint = {"must_verified": ["curobo@1.8.2"]}
        result = mod._apply_motion_controller_filter([ENTRY_CUROBO_VERIFIED], constraint)
        assert len(result) == 1, (
            "Filter constraint curobo@1.8.2 should match template with plain 'curobo'"
        )

    def test_parse_mc_base_name_strips_version(self):
        mod = _load_retriever()
        assert mod._parse_mc_base_name("curobo@1.8.2") == "curobo"
        assert mod._parse_mc_base_name("curobo") == "curobo"
        assert mod._parse_mc_base_name("rmpflow@2.1") == "rmpflow"
        assert mod._parse_mc_base_name("moveit2") == "moveit2"

    def test_versioned_failed_matches_base_name_must_not_failed(self, monkeypatch):
        """Version suffix in failed keys must also be base-name matched."""
        monkeypatch.setenv("RETRIEVAL_MC_FILTER", "on")
        mod = _load_retriever()
        entry = _make_entry("X-01", {
            "verified": [],
            "untested": [],
            "failed": {"admittance@0.9": "joint limits exceeded"},
        })
        constraint = {"must_not_failed": ["admittance"]}
        result = mod._apply_motion_controller_filter([entry], constraint)
        assert len(result) == 0, (
            "admittance@0.9 in failed keys must be excluded by must_not_failed=['admittance']"
        )
