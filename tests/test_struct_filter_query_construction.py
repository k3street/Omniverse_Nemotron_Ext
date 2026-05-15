"""
test_struct_filter_query_construction.py
-----------------------------------------
Round 15 regression tests for the struct-filter query-construction bugs
fixed in template_retriever.py.

Bug 1 (Failure Mode A): Stage 2 re-embed used the structural fingerprint
    instead of the original user prompt for embedding similarity within
    the struct-filtered candidate set.

Bug 2 (Failure Mode B): The fallback path (Stage 1 returns 0 candidates)
    also used the fingerprint string as the query to retrieve_templates_with_scores,
    instead of the original user prompt.

Fix: retrieve_with_intent_filter now accepts `original_query` and uses it
    (when provided) for ALL embedding calls — both Stage 2 and the fallback.
    The fingerprint is only used for metadata filtering (Stage 1).

These tests use a mock embedding-call hook to capture what string was
actually embedded, and assert the user prompt was used.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import List, Optional
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.l0

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from service.isaac_assist_service.chat.tools.template_retriever import (
    retrieve_with_intent_filter,
    canonical_structural_fingerprint,
)

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_SAMPLE_PROMPT = "Pick and place a cube from the conveyor belt into a bin using a Franka robot"

_SAMPLE_INTENT = {
    "pattern_hint": "pick_place",
    "structural_features": {
        "destination_kind": "single_bin",
        "n_robot_stations": 1,
    },
    "counts": {"robots": 1, "conveyors": 1, "bins": 1},
    "structural_tags": [],
}

_FINGERPRINT = canonical_structural_fingerprint(_SAMPLE_INTENT)


# ---------------------------------------------------------------------------
# Helper: build a fake ChromaDB collection + template cache for isolation
# ---------------------------------------------------------------------------

def _make_fake_collection(task_ids: List[str]):
    """Return a MagicMock ChromaDB collection that echos the task_ids back."""
    col = MagicMock()
    col.count.return_value = len(task_ids)

    # col.query(query_texts=[...], n_results=N, where=...) → fake result
    def _query_side_effect(query_texts, n_results=3, where=None, **_kw):
        # Capture the query text via side_effect attribute (tests read it)
        _query_side_effect.last_query = query_texts[0] if query_texts else ""
        # Return first N ids from task_ids list
        ids_to_return = task_ids[:n_results]
        metas = [{"task_id": tid} for tid in ids_to_return]
        dists = [0.3] * len(ids_to_return)
        return {"metadatas": [metas], "distances": [dists]}

    _query_side_effect.last_query = None
    col.query.side_effect = _query_side_effect
    return col


def _make_fake_cache(task_ids: List[str], with_intent: bool = True) -> dict:
    """Return a fake _template_cache dict."""
    cache = {}
    for tid in task_ids:
        t = {
            "task_id": tid,
            "goal": f"Goal for {tid}",
            "thoughts": f"Thoughts for {tid}",
            "tools_used": ["create_prim"],
            "code": "",
            "failure_modes": [],
        }
        if with_intent:
            # Give matching intent so Stage 1 passes
            t["intent"] = {
                "pattern_hint": "pick_place",
                "structural_features": {"destination_kind": "single_bin"},
                "counts": {"robots": 1, "conveyors": 1, "bins": 1},
                "structural_tags": [],
            }
        cache[tid] = t
    return cache


# ---------------------------------------------------------------------------
# Test 1: Stage 2 uses original_query, not fingerprint
# ---------------------------------------------------------------------------

class TestStage2UsesOriginalQuery:
    """When Stage 1 finds candidates and original_query is provided, Stage 2
    must embed the user prompt — NOT the structural fingerprint."""

    def test_stage2_embeds_prompt_not_fingerprint(self):
        """Stage 2 query_texts should be [original_query], not [fingerprint]."""
        task_ids = ["CP-01", "CP-02", "CP-03"]
        fake_col = _make_fake_collection(task_ids)
        fake_cache = _make_fake_cache(task_ids, with_intent=True)

        with (
            patch(
                "service.isaac_assist_service.chat.tools.template_retriever._get_collection",
                return_value=fake_col,
            ),
            patch(
                "service.isaac_assist_service.chat.tools.template_retriever._template_cache",
                fake_cache,
            ),
            patch(
                "service.isaac_assist_service.chat.tools.template_retriever._load_template",
                side_effect=lambda tid: fake_cache.get(tid),
            ),
        ):
            results = retrieve_with_intent_filter(
                _SAMPLE_INTENT,
                top_k=3,
                original_query=_SAMPLE_PROMPT,
            )

        # col.query should have been called with the user prompt, not the fingerprint
        assert fake_col.query.called, "col.query was never called"
        actual_query = fake_col.query.side_effect.last_query
        assert actual_query == _SAMPLE_PROMPT, (
            f"Stage 2 embedded {actual_query!r} instead of user prompt {_SAMPLE_PROMPT!r}. "
            "Bug 1 (Failure Mode A) regression."
        )
        assert actual_query != _FINGERPRINT, (
            "Stage 2 used the fingerprint string for embedding — this is the R14 bug."
        )

    def test_stage2_fallback_to_fingerprint_when_no_original_query(self):
        """When original_query is None (legacy caller), fingerprint is used (backward compat)."""
        task_ids = ["CP-01", "CP-02"]
        fake_col = _make_fake_collection(task_ids)
        fake_cache = _make_fake_cache(task_ids, with_intent=True)

        with (
            patch(
                "service.isaac_assist_service.chat.tools.template_retriever._get_collection",
                return_value=fake_col,
            ),
            patch(
                "service.isaac_assist_service.chat.tools.template_retriever._template_cache",
                fake_cache,
            ),
            patch(
                "service.isaac_assist_service.chat.tools.template_retriever._load_template",
                side_effect=lambda tid: fake_cache.get(tid),
            ),
        ):
            retrieve_with_intent_filter(
                _SAMPLE_INTENT,
                top_k=3,
                original_query=None,  # legacy call — no prompt available
            )

        actual_query = fake_col.query.side_effect.last_query
        assert actual_query == _FINGERPRINT, (
            f"Without original_query, Stage 2 should embed the fingerprint (legacy). "
            f"Got: {actual_query!r}"
        )


# ---------------------------------------------------------------------------
# Test 2: Fallback path uses original_query, not fingerprint
# ---------------------------------------------------------------------------

class TestFallbackUsesOriginalQuery:
    """When Stage 1 returns 0 candidates, the fallback embedding search must
    use the original user prompt — not the fingerprint."""

    def test_fallback_embeds_prompt_not_fingerprint(self):
        """When no candidates pass Stage 1, retrieve_templates_with_scores
        must be called with the user prompt, not the fingerprint."""
        # Use an intent with a pattern_hint that no template has → 0 candidates
        intent_no_match = {
            "pattern_hint": "nonexistent_pattern_xyz",
            "structural_features": {},
            "counts": {},
            "structural_tags": [],
        }
        # Cache has templates but none match the above pattern_hint
        fake_cache = _make_fake_cache(["CP-01", "CP-02"], with_intent=True)
        # (Their pattern_hint is pick_place, not nonexistent_pattern_xyz)

        captured_queries: List[str] = []

        def _fake_retrieve_with_scores(query: str, top_k: int = 3, **kw):
            captured_queries.append(query)
            return []

        with (
            patch(
                "service.isaac_assist_service.chat.tools.template_retriever._template_cache",
                fake_cache,
            ),
            patch(
                "service.isaac_assist_service.chat.tools.template_retriever.retrieve_templates_with_scores",
                side_effect=_fake_retrieve_with_scores,
            ),
            # _get_collection needed for filter_templates_by_intent cache init
            patch(
                "service.isaac_assist_service.chat.tools.template_retriever._get_collection",
                return_value=MagicMock(count=lambda: 2),
            ),
        ):
            retrieve_with_intent_filter(
                intent_no_match,
                top_k=3,
                original_query=_SAMPLE_PROMPT,
                fallback_to_embedding_only=True,
            )

        assert len(captured_queries) == 1, (
            f"Expected exactly 1 call to retrieve_templates_with_scores, got {len(captured_queries)}"
        )
        assert captured_queries[0] == _SAMPLE_PROMPT, (
            f"Fallback embedded {captured_queries[0]!r} instead of user prompt {_SAMPLE_PROMPT!r}. "
            "Bug 2 (Failure Mode B) regression."
        )

    def test_fallback_uses_fingerprint_when_no_original_query(self):
        """Legacy caller (no original_query) → fallback uses fingerprint (backward compat)."""
        intent_no_match = {
            "pattern_hint": "nonexistent_pattern_xyz",
            "structural_features": {},
            "counts": {},
            "structural_tags": [],
        }
        fake_cache = _make_fake_cache(["CP-01"], with_intent=True)
        captured_queries: List[str] = []

        def _fake_retrieve_with_scores(query: str, top_k: int = 3, **kw):
            captured_queries.append(query)
            return []

        expected_fingerprint = canonical_structural_fingerprint(intent_no_match)

        with (
            patch(
                "service.isaac_assist_service.chat.tools.template_retriever._template_cache",
                fake_cache,
            ),
            patch(
                "service.isaac_assist_service.chat.tools.template_retriever.retrieve_templates_with_scores",
                side_effect=_fake_retrieve_with_scores,
            ),
            patch(
                "service.isaac_assist_service.chat.tools.template_retriever._get_collection",
                return_value=MagicMock(count=lambda: 1),
            ),
        ):
            retrieve_with_intent_filter(
                intent_no_match,
                top_k=3,
                original_query=None,  # legacy
                fallback_to_embedding_only=True,
            )

        assert len(captured_queries) == 1
        assert captured_queries[0] == expected_fingerprint, (
            f"Without original_query, fallback should use fingerprint. "
            f"Got: {captured_queries[0]!r}"
        )

    def test_fallback_not_called_when_disabled(self):
        """When fallback_to_embedding_only=False and Stage 1 returns 0 candidates,
        the function returns [] without calling retrieve_templates_with_scores."""
        intent_no_match = {
            "pattern_hint": "nonexistent_pattern_xyz",
            "structural_features": {},
            "counts": {},
            "structural_tags": [],
        }
        fake_cache = _make_fake_cache(["CP-01"], with_intent=True)

        with (
            patch(
                "service.isaac_assist_service.chat.tools.template_retriever._template_cache",
                fake_cache,
            ),
            patch(
                "service.isaac_assist_service.chat.tools.template_retriever.retrieve_templates_with_scores"
            ) as mock_rts,
            patch(
                "service.isaac_assist_service.chat.tools.template_retriever._get_collection",
                return_value=MagicMock(count=lambda: 1),
            ),
        ):
            result = retrieve_with_intent_filter(
                intent_no_match,
                top_k=3,
                original_query=_SAMPLE_PROMPT,
                fallback_to_embedding_only=False,
            )

        assert result == [], "Should return [] when fallback disabled"
        mock_rts.assert_not_called()


# ---------------------------------------------------------------------------
# Test 3: Signature contract — original_query param exists and is Optional[str]
# ---------------------------------------------------------------------------

class TestSignatureContract:
    """Ensure retrieve_with_intent_filter accepts original_query as Optional[str]."""

    def test_original_query_param_accepted(self):
        """Calling with original_query=None should not raise TypeError."""
        fake_cache = _make_fake_cache([], with_intent=False)
        with (
            patch(
                "service.isaac_assist_service.chat.tools.template_retriever._template_cache",
                fake_cache,
            ),
            patch(
                "service.isaac_assist_service.chat.tools.template_retriever.retrieve_templates_with_scores",
                return_value=[],
            ),
            patch(
                "service.isaac_assist_service.chat.tools.template_retriever._get_collection",
                return_value=MagicMock(count=lambda: 0),
            ),
        ):
            # Both call forms must work without TypeError
            retrieve_with_intent_filter(_SAMPLE_INTENT, original_query=None)
            retrieve_with_intent_filter(_SAMPLE_INTENT, original_query=_SAMPLE_PROMPT)
            retrieve_with_intent_filter(_SAMPLE_INTENT)  # omitted = None

    def test_canonical_fingerprint_not_used_as_query_when_original_provided(self):
        """Defensive: even if fingerprint starts with 'pattern_hint=', the
        embedding call should never receive a string starting with 'pattern_hint='
        when original_query is a natural-language prompt."""
        task_ids = ["CP-01"]
        fake_col = _make_fake_collection(task_ids)
        fake_cache = _make_fake_cache(task_ids, with_intent=True)

        with (
            patch(
                "service.isaac_assist_service.chat.tools.template_retriever._get_collection",
                return_value=fake_col,
            ),
            patch(
                "service.isaac_assist_service.chat.tools.template_retriever._template_cache",
                fake_cache,
            ),
            patch(
                "service.isaac_assist_service.chat.tools.template_retriever._load_template",
                side_effect=lambda tid: fake_cache.get(tid),
            ),
        ):
            retrieve_with_intent_filter(
                _SAMPLE_INTENT,
                top_k=1,
                original_query=_SAMPLE_PROMPT,
            )

        actual_query = fake_col.query.side_effect.last_query
        assert not actual_query.startswith("pattern_hint="), (
            f"Embedding query started with 'pattern_hint=' — this is the fingerprint, "
            f"not the user prompt. R14 bug regression detected. Query was: {actual_query!r}"
        )
