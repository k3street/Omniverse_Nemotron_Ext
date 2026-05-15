"""
test_template_cache_rehydration.py
-----------------------------------
Tests for the _template_cache rehydration fix (Patch B, 2026-05-15).

Bug: `_template_cache` was only populated by `_build_index()`, which is
only called on first-time index creation. When ChromaDB loads from a
persistent index, `_template_cache` remained empty, making
`filter_templates_by_intent` a silent no-op.

Fix: `_rehydrate_cache()` is called from `_get_collection()` on the
persistent-load path (collection found + count > 0).

Tests use temporary ChromaDB paths and a small fixture set of templates
(real CP-01..05, CP-09..11 on disk) to avoid polluting the production index.
"""
from __future__ import annotations

import importlib
import json
import shutil
import sys
from pathlib import Path
from typing import Dict

import pytest

pytestmark = pytest.mark.l0

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

_TEMPLATES_DIR = _REPO_ROOT / "workspace" / "templates"
_INTENT_TASK_IDS = ["CP-01", "CP-02", "CP-03", "CP-04", "CP-05", "CP-09", "CP-10", "CP-11"]
_COLLECTION_NAME = "isaac_assist_templates"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fresh_module(tmp_index_path: Path):
    """Import a clean copy of template_retriever with module-globals reset.

    We reload the module and monkey-patch the internal path constants so
    each test gets isolated state (no shared _collection, _client,
    _template_cache).  Returns the module object.
    """
    # Remove cached module so importlib gives a fresh instance
    mod_name = "service.isaac_assist_service.chat.tools.template_retriever"
    if mod_name in sys.modules:
        del sys.modules[mod_name]

    mod = importlib.import_module(mod_name)
    # Redirect persisted index to a temp dir
    mod._PERSIST_DIR = tmp_index_path
    mod._TEMPLATES_DIR = _TEMPLATES_DIR
    # Reset global state (module-level globals were freshly initialised by importlib)
    mod._client = None
    mod._collection = None
    mod._template_cache = {}
    return mod


# ---------------------------------------------------------------------------
# Test 1: First-time index build populates _template_cache
# ---------------------------------------------------------------------------

class TestFirstTimeBuild:
    """After _build_index() (first-time path), _template_cache must be populated."""

    def test_cache_populated_after_first_build(self, tmp_path):
        try:
            import chromadb  # noqa: F401
        except ImportError:
            pytest.skip("chromadb not installed")

        mod = _fresh_module(tmp_path / "index_build")

        # Trigger first-time build (collection does not exist yet → exception path)
        col = mod._get_collection()
        assert col is not None, "ChromaDB collection should be created"

        # _template_cache must have entries
        assert len(mod._template_cache) > 0, (
            "_template_cache must be populated after _build_index()"
        )

    def test_intent_templates_in_cache_after_first_build(self, tmp_path):
        try:
            import chromadb  # noqa: F401
        except ImportError:
            pytest.skip("chromadb not installed")

        mod = _fresh_module(tmp_path / "index_build_intent")
        mod._get_collection()

        # All 8 intent-bearing templates must be present
        for tid in _INTENT_TASK_IDS:
            assert tid in mod._template_cache, (
                f"{tid} should be in _template_cache after first build"
            )
            assert mod._template_cache[tid].get("intent") is not None, (
                f"{tid} must have intent field"
            )


# ---------------------------------------------------------------------------
# Test 2: Persistent-index load populates _template_cache via _rehydrate_cache
# ---------------------------------------------------------------------------

class TestPersistentIndexRehydration:
    """When loading an existing ChromaDB index, _template_cache must still be populated."""

    def _build_then_reload(self, tmp_path):
        """Build an index in one module instance, then load it in another."""
        try:
            import chromadb  # noqa: F401
        except ImportError:
            pytest.skip("chromadb not installed")

        index_dir = tmp_path / "persistent_index"

        # Pass 1 — first-time build (populates ChromaDB)
        mod1 = _fresh_module(index_dir)
        mod1._get_collection()
        count_after_build = mod1._collection.count()
        assert count_after_build > 0, "Index must have entries after first build"

        # Pass 2 — simulate a fresh process: reload module, reset globals
        mod2 = _fresh_module(index_dir)  # index_dir already has data → persistent load
        return mod2

    def test_cache_populated_on_persistent_load(self, tmp_path):
        try:
            import chromadb  # noqa: F401
        except ImportError:
            pytest.skip("chromadb not installed")

        mod2 = self._build_then_reload(tmp_path)
        # _template_cache empty BEFORE _get_collection
        assert len(mod2._template_cache) == 0, "Cache must start empty"

        col = mod2._get_collection()
        assert col is not None

        # After persistent-load path, cache must be populated by _rehydrate_cache
        assert len(mod2._template_cache) > 0, (
            "_template_cache must be populated via _rehydrate_cache on persistent-index load"
        )

    def test_intent_templates_present_on_persistent_load(self, tmp_path):
        try:
            import chromadb  # noqa: F401
        except ImportError:
            pytest.skip("chromadb not installed")

        mod2 = self._build_then_reload(tmp_path)
        mod2._get_collection()

        for tid in _INTENT_TASK_IDS:
            assert tid in mod2._template_cache, (
                f"{tid} must be in _template_cache after persistent-index load (rehydration)"
            )


# ---------------------------------------------------------------------------
# Test 3: filter_templates_by_intent returns CP-01 for a matching query
# ---------------------------------------------------------------------------

class TestFilterTemplatesByIntent:
    """filter_templates_by_intent must return correct candidates after rehydration."""

    _spec_intent_cp01 = {
        "pattern_hint": "pick_place",
        "structural_features": {
            "n_robot_stations": 1,
            "n_handoffs": 0,
            "destination_kind": "single_bin",
            "uses_conveyor_transport": True,
        },
        "counts": {},
    }

    def test_filter_returns_cp01_after_first_build(self, tmp_path):
        try:
            import chromadb  # noqa: F401
        except ImportError:
            pytest.skip("chromadb not installed")

        mod = _fresh_module(tmp_path / "filter_first_build")
        mod._get_collection()

        results = mod.filter_templates_by_intent(self._spec_intent_cp01)
        task_ids = [t.get("task_id") for t in results]
        assert "CP-01" in task_ids, (
            f"filter_templates_by_intent should return CP-01 for exact intent match; got {task_ids}"
        )

    def test_filter_returns_cp01_after_persistent_load(self, tmp_path):
        """The key regression test: filter must work after rehydration, not just after _build_index."""
        try:
            import chromadb  # noqa: F401
        except ImportError:
            pytest.skip("chromadb not installed")

        index_dir = tmp_path / "filter_persistent"

        # Pass 1 — build
        mod1 = _fresh_module(index_dir)
        mod1._get_collection()

        # Pass 2 — reload (persistent path)
        mod2 = _fresh_module(index_dir)
        mod2._get_collection()

        results = mod2.filter_templates_by_intent(self._spec_intent_cp01)
        task_ids = [t.get("task_id") for t in results]
        assert "CP-01" in task_ids, (
            f"filter_templates_by_intent must return CP-01 after persistent-index rehydration; "
            f"got {task_ids}"
        )

    def test_filter_returns_all_pick_place_templates(self, tmp_path):
        """All 8 intent-bearing pick_place templates should appear for broad pick_place query."""
        try:
            import chromadb  # noqa: F401
        except ImportError:
            pytest.skip("chromadb not installed")

        index_dir = tmp_path / "filter_broad"
        mod1 = _fresh_module(index_dir)
        mod1._get_collection()

        mod2 = _fresh_module(index_dir)
        mod2._get_collection()

        # Broad intent: only pattern_hint, no structural_features or count constraints
        broad_intent = {"pattern_hint": "pick_place", "structural_features": {}, "counts": {}}
        results = mod2.filter_templates_by_intent(broad_intent)
        task_ids = {t.get("task_id") for t in results}
        # All 8 CP templates with pattern_hint=pick_place should match
        for tid in _INTENT_TASK_IDS:
            assert tid in task_ids, (
                f"{tid} should appear in broad pick_place filter; got {task_ids}"
            )


# ---------------------------------------------------------------------------
# Test 4: Graceful handling when no templates have intent field
# ---------------------------------------------------------------------------

class TestNoIntentTemplates:
    """filter_templates_by_intent must return empty list (not crash) when no templates have intent."""

    def test_empty_result_when_no_intent_templates(self, tmp_path):
        try:
            import chromadb  # noqa: F401
        except ImportError:
            pytest.skip("chromadb not installed")

        # Create a minimal templates dir with intent-free templates
        fake_templates = tmp_path / "fake_templates"
        fake_templates.mkdir()
        (fake_templates / "X-01.json").write_text(json.dumps({
            "task_id": "X-01",
            "goal": "A simple task with no intent field",
            "tools_used": ["create_prim"],
            "thoughts": "",
        }))

        index_dir = tmp_path / "no_intent_index"

        mod = _fresh_module(index_dir)
        mod._TEMPLATES_DIR = fake_templates
        mod._get_collection()

        spec = {"pattern_hint": "pick_place", "structural_features": {}, "counts": {}}
        results = mod.filter_templates_by_intent(spec)

        assert results == [], (
            f"filter_templates_by_intent should return [] when no templates have intent; got {results}"
        )

    def test_no_crash_on_empty_cache(self, tmp_path):
        """Calling filter_templates_by_intent with empty cache should not raise."""
        try:
            import chromadb  # noqa: F401
        except ImportError:
            pytest.skip("chromadb not installed")

        fake_templates = tmp_path / "empty_templates"
        fake_templates.mkdir()

        index_dir = tmp_path / "empty_index"
        mod = _fresh_module(index_dir)
        mod._TEMPLATES_DIR = fake_templates
        mod._get_collection()

        # Should not raise
        results = mod.filter_templates_by_intent({"pattern_hint": "pick_place"})
        assert results == []
