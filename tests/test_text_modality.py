"""Tests for service/isaac_assist_service/multimodal/text_modality.py.

Block 2 Step 19: text-prompt → LayoutSpec.intent producer.
"""
from __future__ import annotations

import json
from typing import Dict

import pytest

pytestmark = pytest.mark.l0

from service.isaac_assist_service.multimodal.text_modality import (
    LLM_INTENT_JSON_SCHEMA,
    extract_intent_llm,
    extract_intent_rules,
    produce_layout_spec_from_text,
)


# ── pattern_hint detection ─────────────────────────────────────────────────


def test_pattern_pick_and_place_default():
    intent = extract_intent_rules("pick up a cube and drop it in a bin")
    assert intent.pattern_hint == "pick_place"


def test_pattern_sort():
    intent = extract_intent_rules("sort colored cubes into matching bins")
    assert intent.pattern_hint == "sort"


def test_pattern_reorient():
    intent = extract_intent_rules("flip the cube upright then deliver it")
    assert intent.pattern_hint == "reorient"


def test_pattern_navigate():
    intent = extract_intent_rules("navigate the AMR to the delivery zone")
    assert intent.pattern_hint == "navigate"


def test_pattern_default_falls_to_pick_place():
    """Truly ambiguous prompt: defaults to pick_place (broadest match)."""
    intent = extract_intent_rules("do something with a robot")
    assert intent.pattern_hint == "pick_place"


# ── structural_features detection ──────────────────────────────────────────


def test_features_conveyor():
    intent = extract_intent_rules("franka picks cubes off a conveyor belt")
    assert intent.structural_features.uses_conveyor_transport is True


def test_features_navigation():
    intent = extract_intent_rules("AMR navigates to bin")
    assert intent.structural_features.uses_navigation is True


def test_features_color_routing():
    intent = extract_intent_rules("sort cubes by color into bins")
    assert intent.structural_features.has_color_routing is True


def test_features_orientation_from_reorient_pattern():
    """Reorient pattern implies has_orientation_requirement even without
    explicit upright wording."""
    intent = extract_intent_rules("reorient cube and place")
    assert intent.structural_features.has_orientation_requirement is True


def test_features_bounded_footprint():
    intent = extract_intent_rules("compact cell fitting in a 2m footprint")
    assert intent.structural_features.has_bounded_footprint is True


def test_features_human():
    intent = extract_intent_rules("robot collaborates with human worker")
    assert intent.structural_features.has_human_in_workspace is True


def test_features_default_off():
    """No flag should fire on a minimal prompt."""
    intent = extract_intent_rules("move the part")
    assert intent.structural_features.uses_conveyor_transport is False
    assert intent.structural_features.has_color_routing is False
    assert intent.structural_features.has_human_in_workspace is False


# ── counts detection ───────────────────────────────────────────────────────


def test_count_robots_numeric():
    intent = extract_intent_rules("2 robots picking cubes")
    assert intent.counts.robots == 2


def test_count_robots_word():
    intent = extract_intent_rules("two robots picking cubes")
    assert intent.counts.robots == 2


def test_count_conveyors():
    intent = extract_intent_rules("3 conveyors connect the cells")
    assert intent.counts.conveyors == 3


def test_count_cubes_synonym_block():
    intent = extract_intent_rules("5 blocks on the belt")
    assert intent.counts.cubes == 5


def test_count_humans():
    intent = extract_intent_rules("one human and one robot share the cell")
    assert intent.counts.humans == 1
    assert intent.counts.robots == 1


def test_count_zero_when_unmentioned():
    intent = extract_intent_rules("franka picks cubes")
    # franka is a robot synonym → counts.robots stays default 0 (no number)
    # cubes mentioned but no count → 0
    assert intent.counts.bins == 0


# ── derived feature: n_robot_stations follows counts.robots ────────────────


def test_n_robot_stations_derived_from_count():
    intent = extract_intent_rules("2 robots assembly line")
    assert intent.structural_features.n_robot_stations == 2


def test_n_robot_stations_default_one():
    intent = extract_intent_rules("pick and place")
    assert intent.structural_features.n_robot_stations == 1


# ── n_handoffs derived from multi-conveyor ─────────────────────────────────


def test_n_handoffs_inferred_from_two_conveyors():
    intent = extract_intent_rules("2 robots and 2 conveyors with handoff")
    assert intent.structural_features.uses_conveyor_transport
    assert intent.structural_features.n_handoffs >= 1


# ── produce_layout_spec_from_text ──────────────────────────────────────────


def test_produce_spec_text_modality():
    spec = produce_layout_spec_from_text("pick a cube and drop in bin")
    assert spec.source.modality == "text"
    assert spec.source.confidence == 0.7
    assert spec.objects == []
    assert spec.bindings is None
    assert spec.intent.pattern_hint == "pick_place"


def test_produce_spec_preserves_raw_input():
    p = "sort colored cubes"
    spec = produce_layout_spec_from_text(p)
    assert spec.source.raw_input == p


def test_produce_spec_custom_extractor():
    """Caller can swap extractor (e.g., LLM-backed)."""
    from service.isaac_assist_service.multimodal.types import Intent

    spec = produce_layout_spec_from_text(
        "ignored",
        extractor=lambda _p: Intent(pattern_hint="navigate"),
    )
    assert spec.intent.pattern_hint == "navigate"


def test_produce_spec_custom_confidence():
    spec = produce_layout_spec_from_text("x", confidence=0.95)
    assert spec.source.confidence == 0.95


# ── LLM extractor ──────────────────────────────────────────────────────────


def test_llm_extractor_with_stub():
    """LLM hook accepts a callable that returns JSON string."""
    def stub_llm(system: str, user: str, schema: Dict) -> str:
        assert "extract structured intent" in system.lower()
        return json.dumps({
            "pattern_hint": "sort",
            "structural_features": {"has_color_routing": True},
            "structural_tags": ["isaac:routing.color"],
        })

    intent = extract_intent_llm("sort cubes by color", stub_llm)
    assert intent.pattern_hint == "sort"
    assert intent.structural_features.has_color_routing is True
    assert "isaac:routing.color" in intent.structural_tags


def test_llm_extractor_rejects_non_json():
    def bad_llm(s, u, schema):
        return "not json"

    with pytest.raises(ValueError, match="JSON"):
        extract_intent_llm("x", bad_llm)


def test_llm_extractor_rejects_invalid_pattern():
    def bad_pattern_llm(s, u, schema):
        return json.dumps({"pattern_hint": "custom"})  # not in enum

    with pytest.raises(Exception):
        extract_intent_llm("x", bad_pattern_llm)


def test_llm_schema_has_closed_enum():
    """Schema constrains pattern_hint to closed enum."""
    enum = LLM_INTENT_JSON_SCHEMA["properties"]["pattern_hint"]["enum"]
    assert set(enum) == {"pick_place", "sort", "reorient", "navigate"}


def test_llm_schema_rejects_additional_properties():
    """Schema is closed (additionalProperties=False) on top level."""
    assert LLM_INTENT_JSON_SCHEMA["additionalProperties"] is False
