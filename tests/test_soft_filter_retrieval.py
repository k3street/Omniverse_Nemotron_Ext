"""
test_soft_filter_retrieval.py
------------------------------
Unit tests for retrieve_with_intent_soft_filter (R15d).

Tests are isolated from ChromaDB by monkeypatching
retrieve_templates_with_scores and _template_cache so no real index is
needed.  Each test focuses on one aspect of the soft-filter algorithm.

Run with:
    python -m pytest tests/test_soft_filter_retrieval.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Dict, List
from unittest.mock import patch

import pytest

# All tests in this file are pure-unit (no external dependencies)
pytestmark = pytest.mark.l0

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from service.isaac_assist_service.chat.tools.template_retriever import (
    retrieve_with_intent_soft_filter,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_template(task_id: str, pattern_hint: str = "pick_place") -> Dict:
    """Minimal template fixture with an intent field."""
    return {
        "task_id": task_id,
        "goal": f"Goal for {task_id}",
        "intent": {"pattern_hint": pattern_hint, "counts": {}, "structural_features": {}},
    }


def _make_template_no_intent(task_id: str) -> Dict:
    """Minimal template fixture WITHOUT an intent field (legacy template)."""
    return {
        "task_id": task_id,
        "goal": f"Legacy goal for {task_id}",
    }


def _make_scored(task_id: str, similarity: float, template: Dict) -> Dict:
    """Minimal scored-entry dict as returned by retrieve_templates_with_scores."""
    return {
        "task_id": task_id,
        "similarity": similarity,
        "distance": 1.0 - similarity,
        "template": template,
    }


def _make_spec(pattern_hint: str = "pick_place") -> Dict:
    """Minimal spec intent dict that is NOT null-signal (has a non-default pattern_hint)."""
    return {
        "pattern_hint": pattern_hint,
        "counts": {"robots": 1},
        "structural_features": {},
        "structural_tags": [],
    }


def _make_null_spec() -> Dict:
    """Spec intent that _spec_is_null_signal returns True for."""
    return {
        "pattern_hint": "pick_place",
        "counts": {"robots": 0, "conveyors": 0, "bins": 0, "cubes": 0, "sensors": 0, "humans": 0},
        "structural_features": {
            "multi_robot": False,
            "has_conveyor": False,
            "destination_kind": "single_bin",
        },
        "structural_tags": [],
    }


# ---------------------------------------------------------------------------
# Test 1: matching pattern_hint → similarity is boosted
# ---------------------------------------------------------------------------

def test_matching_pattern_hint_gets_boosted():
    """A candidate whose intent.pattern_hint matches the spec gets similarity * boost."""
    tmpl_a = _make_template("CP-01", pattern_hint="pick_place")
    tmpl_b = _make_template("CP-02", pattern_hint="sort")

    raw_results = [
        _make_scored("CP-01", 0.70, tmpl_a),
        _make_scored("CP-02", 0.80, tmpl_b),
    ]
    spec = _make_spec(pattern_hint="pick_place")

    fake_cache = {"CP-01": tmpl_a, "CP-02": tmpl_b}

    with (
        patch(
            "service.isaac_assist_service.chat.tools.template_retriever"
            ".retrieve_templates_with_scores",
            return_value=raw_results,
        ),
        patch(
            "service.isaac_assist_service.chat.tools.template_retriever._template_cache",
            fake_cache,
        ),
    ):
        results = retrieve_with_intent_soft_filter(
            spec, top_k=2, original_query="pick and place", boost=1.15, oversample=1
        )

    # CP-01 should have boost_applied=True; CP-02 should not
    cp01 = next(r for r in results if r["task_id"] == "CP-01")
    cp02 = next(r for r in results if r["task_id"] == "CP-02")

    assert cp01["boost_applied"] is True
    assert abs(cp01["similarity_boosted"] - 0.70 * 1.15) < 1e-9, (
        f"Expected {0.70 * 1.15}, got {cp01['similarity_boosted']}"
    )
    assert cp02["boost_applied"] is False
    assert abs(cp02["similarity_boosted"] - 0.80) < 1e-9, (
        f"Expected 0.80 unchanged, got {cp02['similarity_boosted']}"
    )


# ---------------------------------------------------------------------------
# Test 2: candidate WITHOUT intent field → no penalty (similarity unchanged)
# ---------------------------------------------------------------------------

def test_no_intent_field_no_penalty():
    """Legacy templates (no intent field) must not be penalized — similarity unchanged."""
    tmpl_legacy = _make_template_no_intent("M-08")

    raw_results = [_make_scored("M-08", 0.75, tmpl_legacy)]
    spec = _make_spec(pattern_hint="train")

    fake_cache = {"M-08": tmpl_legacy}

    with (
        patch(
            "service.isaac_assist_service.chat.tools.template_retriever"
            ".retrieve_templates_with_scores",
            return_value=raw_results,
        ),
        patch(
            "service.isaac_assist_service.chat.tools.template_retriever._template_cache",
            fake_cache,
        ),
    ):
        results = retrieve_with_intent_soft_filter(
            spec, top_k=1, original_query="train allegro", boost=1.15, oversample=1
        )

    assert len(results) == 1
    r = results[0]
    assert r["boost_applied"] is False
    assert abs(r["similarity_boosted"] - 0.75) < 1e-9, (
        f"Legacy template should be unchanged; got {r['similarity_boosted']}"
    )
    assert r["similarity"] == 0.75


# ---------------------------------------------------------------------------
# Test 3: non-matching pattern_hint → no boost (similarity unchanged)
# ---------------------------------------------------------------------------

def test_nonmatching_pattern_hint_no_boost():
    """Candidates with a different pattern_hint get no boost and no penalty."""
    tmpl = _make_template("CP-55", pattern_hint="navigate")

    raw_results = [_make_scored("CP-55", 0.65, tmpl)]
    spec = _make_spec(pattern_hint="sort")

    fake_cache = {"CP-55": tmpl}

    with (
        patch(
            "service.isaac_assist_service.chat.tools.template_retriever"
            ".retrieve_templates_with_scores",
            return_value=raw_results,
        ),
        patch(
            "service.isaac_assist_service.chat.tools.template_retriever._template_cache",
            fake_cache,
        ),
    ):
        results = retrieve_with_intent_soft_filter(
            spec, top_k=1, original_query="sort by color", boost=1.15, oversample=1
        )

    r = results[0]
    assert r["boost_applied"] is False
    assert abs(r["similarity_boosted"] - 0.65) < 1e-9


# ---------------------------------------------------------------------------
# Test 4: null-signal spec → no boost applied to anyone
# ---------------------------------------------------------------------------

def test_null_signal_spec_no_boost():
    """When spec is null-signal, no template should receive a boost."""
    tmpl_a = _make_template("CP-01", pattern_hint="pick_place")
    tmpl_b = _make_template("CP-02", pattern_hint="pick_place")

    raw_results = [
        _make_scored("CP-01", 0.80, tmpl_a),
        _make_scored("CP-02", 0.70, tmpl_b),
    ]
    spec = _make_null_spec()

    fake_cache = {"CP-01": tmpl_a, "CP-02": tmpl_b}

    with (
        patch(
            "service.isaac_assist_service.chat.tools.template_retriever"
            ".retrieve_templates_with_scores",
            return_value=raw_results,
        ),
        patch(
            "service.isaac_assist_service.chat.tools.template_retriever._template_cache",
            fake_cache,
        ),
    ):
        results = retrieve_with_intent_soft_filter(
            spec, top_k=2, original_query="robot task", boost=1.15, oversample=1
        )

    for r in results:
        assert r["boost_applied"] is False, (
            f"{r['task_id']} should not be boosted for null-signal spec"
        )
        assert r["similarity_boosted"] == r["similarity"]


# ---------------------------------------------------------------------------
# Test 5: oversample parameter — fetches top_k * oversample candidates
# ---------------------------------------------------------------------------

def test_oversample_fetches_extended_set():
    """oversample=3 with top_k=3 means retrieve_templates_with_scores is called with n=9."""
    spec = _make_spec(pattern_hint="pick_place")

    call_args_holder = {}

    def fake_retrieve(query: str, top_k: int = 3, **kwargs) -> List[Dict]:
        call_args_holder["top_k"] = top_k
        # Return exactly top_k items to satisfy the slice
        return [
            _make_scored(f"CP-{i:02d}", 0.9 - i * 0.01, _make_template(f"CP-{i:02d}"))
            for i in range(top_k)
        ]

    with (
        patch(
            "service.isaac_assist_service.chat.tools.template_retriever"
            ".retrieve_templates_with_scores",
            side_effect=fake_retrieve,
        ),
        patch(
            "service.isaac_assist_service.chat.tools.template_retriever._template_cache",
            {},
        ),
    ):
        retrieve_with_intent_soft_filter(
            spec, top_k=3, original_query="test", boost=1.15, oversample=3
        )

    assert call_args_holder.get("top_k") == 9, (
        f"Expected 9 (3*3), got {call_args_holder.get('top_k')}"
    )


# ---------------------------------------------------------------------------
# Test 6: re-sort order is correct after boost
# ---------------------------------------------------------------------------

def test_resort_order_after_boost():
    """After boosting, the result list should be ordered by similarity_boosted desc."""
    # CP-A: raw similarity 0.70, matching pattern → boosted to 0.70*1.20=0.840
    # CP-B: raw similarity 0.80, non-matching pattern → stays 0.80
    # CP-C: raw similarity 0.60, matching pattern → boosted to 0.60*1.20=0.720
    tmpl_a = _make_template("CP-A", pattern_hint="sort")
    tmpl_b = _make_template("CP-B", pattern_hint="navigate")
    tmpl_c = _make_template("CP-C", pattern_hint="sort")

    raw_results = [
        _make_scored("CP-B", 0.80, tmpl_b),  # highest raw, but no boost
        _make_scored("CP-A", 0.70, tmpl_a),  # gets boost: 0.840 → should be rank 1
        _make_scored("CP-C", 0.60, tmpl_c),  # gets boost: 0.720 → should be rank 2 or 3
    ]
    spec = _make_spec(pattern_hint="sort")

    fake_cache = {"CP-A": tmpl_a, "CP-B": tmpl_b, "CP-C": tmpl_c}

    with (
        patch(
            "service.isaac_assist_service.chat.tools.template_retriever"
            ".retrieve_templates_with_scores",
            return_value=raw_results,
        ),
        patch(
            "service.isaac_assist_service.chat.tools.template_retriever._template_cache",
            fake_cache,
        ),
    ):
        results = retrieve_with_intent_soft_filter(
            spec, top_k=3, original_query="sort items", boost=1.20, oversample=1
        )

    assert len(results) == 3
    # Expected order: CP-A (0.840) > CP-B (0.80) > CP-C (0.720)
    assert results[0]["task_id"] == "CP-A", f"Expected CP-A first, got {results[0]['task_id']}"
    assert results[1]["task_id"] == "CP-B", f"Expected CP-B second, got {results[1]['task_id']}"
    assert results[2]["task_id"] == "CP-C", f"Expected CP-C third, got {results[2]['task_id']}"

    # Verify the boosted similarities are descending
    sims = [r["similarity_boosted"] for r in results]
    assert sims == sorted(sims, reverse=True), f"Not descending: {sims}"
    assert abs(sims[0] - 0.70 * 1.20) < 1e-9
    assert abs(sims[1] - 0.80) < 1e-9
    assert abs(sims[2] - 0.60 * 1.20) < 1e-9
