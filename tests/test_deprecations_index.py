"""Tests for the deprecations keyword index + lookup_api_deprecation tool.

Pins the 2026-04-19 deprecations-index behavior: deterministic
keyword-scored lookup over a small JSONL corpus, returning verbatim
cite-facts the agent quotes. See
`docs/qa/deprecations_index_proposal.md` for the design.

Corpus: `service/isaac_assist_service/knowledge/deprecations.jsonl`
Loader: `service/isaac_assist_service/knowledge/deprecations_index.py`
Tool handler: `_handle_lookup_api_deprecation` in tool_executor.py.

L0 — pure string/dict processing, no Kit, no LLM.
"""
from __future__ import annotations

import asyncio
import json

import pytest

pytestmark = pytest.mark.l0


def _index():
    from service.isaac_assist_service.knowledge.deprecations_index import (
        all_rows,
        lookup,
    )
    return lookup, all_rows


def test_corpus_is_loadable_and_nonempty():
    lookup, all_rows = _index()
    rows = all_rows()
    assert len(rows) >= 4, f"expected ≥4 rows, got {len(rows)}"
    for row in rows:
        assert "id" in row
        assert "keywords" in row
        assert isinstance(row["keywords"], list)
        assert row["keywords"], f"row {row['id']!r} has empty keywords"
        assert "cite" in row, f"row {row['id']!r} missing 'cite' field"


def test_corpus_ids_are_unique():
    lookup, all_rows = _index()
    ids = [r["id"] for r in all_rows()]
    assert len(ids) == len(set(ids)), f"duplicate ids in corpus: {ids}"


def test_t13_canary_returns_deterministic_replay_row():
    """The anchor use case: T-13-style deterministic-replay cite query."""
    lookup, _ = _index()
    hits = lookup("I need a cite-able statement about deterministic replay for CI regression", top_k=1)
    assert hits, "deterministic query should match"
    assert hits[0]["id"] == "deterministic_replay"
    # Core fields must be present and exact
    assert hits[0]["tool_5x"] == "enable_deterministic_mode"
    assert any(
        "SimulationContext.set_deterministic" in d
        for d in hits[0]["deprecated_4x"]
    ), "deprecated_4x must include SimulationContext.set_deterministic"


def test_ros2_bridge_query():
    lookup, _ = _index()
    hits = lookup("ROS2 bridge migration 4.x 5.x namespace", top_k=1)
    assert hits and hits[0]["id"] == "ros2_bridge_namespace"


def test_urdf_import_query():
    lookup, _ = _index()
    hits = lookup("urdf importer 5.x module path", top_k=1)
    assert hits and hits[0]["id"] == "urdf_import_api"


def test_empty_query_returns_empty():
    lookup, _ = _index()
    assert lookup("") == []
    assert lookup("   ") == []


def test_unrelated_query_returns_empty():
    """Query with no keyword matches should cleanly return []."""
    lookup, _ = _index()
    assert lookup("what time is it in Stockholm?") == []


def test_scoring_prefers_more_keyword_matches():
    """When multiple rows match, the row with more keyword hits wins."""
    lookup, _ = _index()
    # Query touches deterministic_replay keywords heavily
    hits = lookup(
        "deterministic bit-identical seed fixed timestep ci regression TGS",
        top_k=3,
    )
    assert hits, "multi-keyword query should hit"
    assert hits[0]["id"] == "deterministic_replay"


def test_top_k_is_respected():
    lookup, _ = _index()
    hits = lookup("isaac sim", top_k=2)  # 'isaac' likely matches several rows
    assert len(hits) <= 2


def test_tool_handler_shape():
    """The async handler returns the documented dict shape."""
    from service.isaac_assist_service.chat.tools.tool_executor import (
        _handle_lookup_api_deprecation,
    )
    r = asyncio.run(_handle_lookup_api_deprecation({"query": "deterministic replay", "top_k": 1}))
    assert isinstance(r, dict)
    assert r["count"] == 1
    assert r["query"] == "deterministic replay"
    assert len(r["results"]) == 1
    assert r["results"][0]["tool_5x"] == "enable_deterministic_mode"
    assert "note" in r


def test_tool_handler_empty_query_shape():
    """No-match query returns a well-formed empty result.
    Uses a sentinel string with no plausible corpus overlap — the older
    "what colour is the sky on Mars" now fuzzy-matches expanded corpus
    entries since the cite handler grew beyond pure deprecations."""
    from service.isaac_assist_service.chat.tools.tool_executor import (
        _handle_lookup_api_deprecation,
    )
    sentinel = "qZ_xyz_no_corpus_match_sentinel_abc123"
    r = asyncio.run(_handle_lookup_api_deprecation({"query": sentinel}))
    assert r["count"] == 0, (
        f"expected count=0 for sentinel query, got {r['count']} "
        f"with results: {[x.get('id') for x in r.get('results', [])]}"
    )
    assert r["results"] == []
    # Note should explain fallback
    assert "lookup_knowledge" in r["note"] or "No " in r["note"]


def test_corpus_rows_have_ready_to_paste_cite():
    """Every row must have a non-trivial cite paragraph — it's what the
    agent quotes verbatim. Empty/stub cite text is a broken row."""
    lookup, all_rows = _index()
    for row in all_rows():
        assert len(row.get("cite", "")) > 100, (
            f"row {row['id']!r} has stub cite (length {len(row.get('cite',''))}); "
            "cite paragraphs must be substantive — the agent quotes them verbatim"
        )


def test_lookup_is_case_insensitive_on_keywords():
    """Query's case should not affect matching; keywords are lowercased
    in the index."""
    lookup, _ = _index()
    a = lookup("DETERMINISTIC REPLAY", top_k=1)
    b = lookup("deterministic replay", top_k=1)
    c = lookup("Deterministic Replay", top_k=1)
    assert a == b == c
