"""Tests for service/isaac_assist_service/multimodal/vlm_modality.py.

Block 3 Steps 22-23: sketch + photo VLM-based modality producers.
"""
from __future__ import annotations

import json
from typing import Optional

import pytest

pytestmark = pytest.mark.l0

from service.isaac_assist_service.multimodal.vlm_modality import (
    VLM_LAYOUT_SYSTEM_PROMPT,
    produce_layout_spec_from_photo,
    produce_layout_spec_from_sketch,
)


def _stub_vlm_with(payload: dict):
    def _call(system: str, image: bytes, ctx: Optional[str]) -> str:
        assert isinstance(image, bytes)
        return json.dumps(payload)
    return _call


# ── Sketch ─────────────────────────────────────────────────────────────────


def test_sketch_emits_intent_only():
    """Minimal: pattern only, no objects."""
    vlm = _stub_vlm_with({"pattern_hint": "pick_place"})
    spec = produce_layout_spec_from_sketch(b"img", vlm)
    assert spec.source.modality == "sketch"
    assert spec.intent.pattern_hint == "pick_place"
    assert spec.objects == []


def test_sketch_emits_objects():
    vlm = _stub_vlm_with({
        "pattern_hint": "pick_place",
        "objects": [
            {
                "class": "franka_panda",
                "name": "Franka_1",
                "position": {"x": 0.0, "y": 0.0},
                "size": {"w": 0.5, "h": 0.5},
            },
            {
                "class": "bin",
                "name": "Bin_1",
                "position": {"x": 0.5, "y": -0.4},
                "size": {"w": 0.3, "h": 0.3},
            },
        ],
    })
    spec = produce_layout_spec_from_sketch(b"img", vlm)
    assert len(spec.objects) == 2
    assert spec.objects[0].object_class == "franka_panda"
    assert spec.objects[1].name == "Bin_1"


def test_sketch_emits_role_hint_as_binding():
    """VLM-emitted role_hint becomes a modality-emitted RoleBinding."""
    vlm = _stub_vlm_with({
        "pattern_hint": "pick_place",
        "objects": [
            {
                "class": "franka_panda",
                "name": "Franka_1",
                "position": {"x": 0.0, "y": 0.0},
                "size": {"w": 0.5, "h": 0.5},
                "role_hint": "primary_robot",
                "confidence": 0.85,
            },
        ],
    })
    spec = produce_layout_spec_from_sketch(b"img", vlm)
    assert spec.bindings is not None
    assert "primary_robot" in spec.bindings
    rb = spec.bindings["primary_robot"]
    assert rb.source == "modality_emitted"
    assert rb.confidence == 0.85


def test_sketch_default_confidence_in_band():
    """Sketch default confidence ∈ [0.4, 0.7] per §7.2."""
    vlm = _stub_vlm_with({"pattern_hint": "pick_place"})
    spec = produce_layout_spec_from_sketch(b"img", vlm)
    assert 0.4 <= spec.source.confidence <= 0.7


def test_sketch_respects_vlm_overall_confidence():
    vlm = _stub_vlm_with({
        "pattern_hint": "pick_place",
        "overall_confidence": 0.65,
    })
    spec = produce_layout_spec_from_sketch(b"img", vlm)
    assert spec.source.confidence == 0.65


def test_sketch_clamps_oob_confidence():
    vlm = _stub_vlm_with({
        "pattern_hint": "pick_place",
        "overall_confidence": 1.5,
    })
    spec = produce_layout_spec_from_sketch(b"img", vlm)
    assert spec.source.confidence == 1.0


def test_sketch_skips_malformed_object():
    """Object missing required fields is logged + skipped, not fatal."""
    vlm = _stub_vlm_with({
        "pattern_hint": "pick_place",
        "objects": [
            {"name": "broken"},  # missing class, position, size
            {
                "class": "bin",
                "name": "Bin_1",
                "position": {"x": 0, "y": 0},
                "size": {"w": 0.3, "h": 0.3},
            },
        ],
    })
    spec = produce_layout_spec_from_sketch(b"img", vlm)
    assert len(spec.objects) == 1
    assert spec.objects[0].name == "Bin_1"


def test_sketch_rejects_non_json_vlm():
    vlm = lambda s, i, c: "not json"
    with pytest.raises(ValueError, match="JSON"):
        produce_layout_spec_from_sketch(b"img", vlm)


def test_sketch_passes_prompt_context_through():
    seen = {}
    def vlm(system: str, image: bytes, ctx):
        seen["ctx"] = ctx
        return json.dumps({"pattern_hint": "pick_place"})
    produce_layout_spec_from_sketch(b"img", vlm, prompt_context="a Franka next to a bin")
    assert seen["ctx"] == "a Franka next to a bin"


# ── Photo ──────────────────────────────────────────────────────────────────


def test_photo_modality_distinct():
    vlm = _stub_vlm_with({"pattern_hint": "pick_place"})
    spec = produce_layout_spec_from_photo(b"img", vlm)
    assert spec.source.modality == "photo"


def test_photo_default_confidence_lower_than_sketch():
    """Photo confidence band 0.3-0.6 per §7.2 reliability profile."""
    vlm = _stub_vlm_with({"pattern_hint": "pick_place"})
    spec = produce_layout_spec_from_photo(b"img", vlm)
    assert 0.3 <= spec.source.confidence <= 0.6


# ── System prompt contract ─────────────────────────────────────────────────


def test_vlm_system_prompt_lists_closed_pattern_enum():
    """System prompt must include all four pattern_hints exactly."""
    sp = VLM_LAYOUT_SYSTEM_PROMPT
    for p in ("pick_place", "sort", "reorient", "navigate"):
        assert p in sp


def test_vlm_system_prompt_documents_role_hint_optionality():
    """The system prompt tells the VLM to emit null when role is unclear."""
    assert "role_hint" in VLM_LAYOUT_SYSTEM_PROMPT
    assert "null" in VLM_LAYOUT_SYSTEM_PROMPT


# ── Common: structural_tags pass through ───────────────────────────────────


def test_sketch_passes_structural_tags():
    vlm = _stub_vlm_with({
        "pattern_hint": "sort",
        "structural_tags": ["isaac:routing.color", "isaac:transport.conveyor"],
    })
    spec = produce_layout_spec_from_sketch(b"img", vlm)
    assert "isaac:routing.color" in spec.intent.structural_tags
    assert "isaac:transport.conveyor" in spec.intent.structural_tags
