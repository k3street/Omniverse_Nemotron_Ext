"""End-to-end smoke for Block 2 wiring:

text prompt → produce_layout_spec_from_text → Intent dict →
retrieve_with_intent_filter → ranked template candidates.

This tests that the text-modality producer's output shape is compatible
with the intent-filter retriever's input shape. ChromaDB may not be
available in CI; the retriever has a fallback that returns candidates in
arbitrary order — we only assert reachability and shape, not ranking.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.l0


def test_text_to_intent_to_retrieval_smoke():
    """End-to-end: text → intent → retrieval returns shape-correct list."""
    from service.isaac_assist_service.multimodal import (
        produce_layout_spec_from_text,
    )
    from service.isaac_assist_service.chat.tools.template_retriever import (
        retrieve_with_intent_filter,
    )

    spec = produce_layout_spec_from_text(
        "franka picks 4 cubes from a conveyor and drops them in a bin",
    )
    assert spec.intent.pattern_hint == "pick_place"
    assert spec.intent.structural_features.uses_conveyor_transport is True

    intent_dump = spec.intent.model_dump(mode="json")
    hits = retrieve_with_intent_filter(intent_dump, top_k=3)

    # Should produce a list (possibly empty if ChromaDB unreachable)
    assert isinstance(hits, list)
    # Every hit is shape-correct
    for h in hits:
        assert "task_id" in h
        assert "similarity" in h
        assert "template" in h


def test_multi_robot_prompt_filters_by_n_stations():
    """Two-robot prompt produces n_robot_stations=2, used as structural filter."""
    from service.isaac_assist_service.multimodal import (
        produce_layout_spec_from_text,
    )

    spec = produce_layout_spec_from_text(
        "2 frankas pass cubes between them via a transfer conveyor",
    )
    assert spec.intent.structural_features.n_robot_stations == 2
    assert spec.intent.structural_features.uses_conveyor_transport is True


def test_sort_prompt_extracts_color_routing():
    from service.isaac_assist_service.multimodal import (
        produce_layout_spec_from_text,
    )

    spec = produce_layout_spec_from_text(
        "sort red and blue cubes into matching color bins",
    )
    assert spec.intent.pattern_hint == "sort"
    assert spec.intent.structural_features.has_color_routing is True


def test_reorient_prompt_implies_orientation_requirement():
    from service.isaac_assist_service.multimodal import (
        produce_layout_spec_from_text,
    )

    spec = produce_layout_spec_from_text(
        "the cube is on its side; flip it upright before placing in the bin",
    )
    assert spec.intent.pattern_hint == "reorient"
    assert spec.intent.structural_features.has_orientation_requirement is True
