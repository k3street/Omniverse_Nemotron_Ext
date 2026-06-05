"""
test_pattern_hint_extension.py — Round 12 schema extension tests.

Covers:
  1. New pattern_hint values pass canonical_schema lint (value in VALID_PATTERN_HINTS)
  2. Rule-based extractor classifies prompts to new patterns
  3. Backward compat: original 4 patterns still work
  4. PatternHint Literal in types.py accepts all 7 values

All tests are L0 (no Kit, no filesystem, no network).
"""

import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.l0

# Make scripts/ importable
_SCRIPTS = Path(__file__).parent.parent / "scripts"
_SERVICE = Path(__file__).parent.parent / "service"
sys.path.insert(0, str(_SCRIPTS))
sys.path.insert(0, str(_SERVICE))

import canonical_schema as schema  # noqa: E402
from isaac_assist_service.multimodal.text_modality import _detect_pattern_hint  # noqa: E402
from isaac_assist_service.multimodal.types import Intent, PatternHint  # noqa: E402
from isaac_assist_service.multimodal.types import Counts, StructuralFeatures  # noqa: E402


# ---------------------------------------------------------------------------
# §1 — VALID_PATTERN_HINTS contains all 7 values
# ---------------------------------------------------------------------------

EXPECTED_HINTS = {"pick_place", "sort", "reorient", "navigate", "insert", "train", "other"}


def test_valid_pattern_hints_contains_all_new_values():
    assert EXPECTED_HINTS == schema.VALID_PATTERN_HINTS


def test_valid_pattern_hints_contains_original_values():
    """Backward compat: original 4 values must still be present."""
    for v in ("pick_place", "sort", "reorient", "navigate"):
        assert v in schema.VALID_PATTERN_HINTS, f"original hint {v!r} missing"


def test_valid_pattern_hints_contains_insert():
    assert "insert" in schema.VALID_PATTERN_HINTS


def test_valid_pattern_hints_contains_train():
    assert "train" in schema.VALID_PATTERN_HINTS


def test_valid_pattern_hints_contains_other():
    assert "other" in schema.VALID_PATTERN_HINTS


# ---------------------------------------------------------------------------
# §2 — PatternHint Literal accepts all 7 values via Intent construction
# ---------------------------------------------------------------------------

def _make_intent(hint: str) -> Intent:
    return Intent(
        pattern_hint=hint,  # type: ignore[arg-type]
        counts=Counts(),
        structural_features=StructuralFeatures(),
        structural_tags=[],
    )


@pytest.mark.parametrize("hint", sorted(EXPECTED_HINTS))
def test_pattern_hint_accepted_by_intent(hint):
    """Each value in EXPECTED_HINTS must be accepted by the PatternHint Literal."""
    intent = _make_intent(hint)
    assert intent.pattern_hint == hint


def test_invalid_pattern_hint_rejected():
    """A value outside the enum must be rejected by Intent."""
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        _make_intent("custom")


# ---------------------------------------------------------------------------
# §3 — Rule-based extractor: new patterns
# ---------------------------------------------------------------------------

# insert prompts — should fire the insert rule
@pytest.mark.parametrize("prompt", [
    "Build a peg-in-hole insertion array: 4 pegs, 4 holes",
    "hole insertion task with force torque sensor",
    "tactile-feedback insertion task: Franka inserts peg",
    "force-compliant insert into the fixture",
])
def test_extractor_detects_insert(prompt):
    assert _detect_pattern_hint(prompt) == "insert", f"expected insert for: {prompt!r}"


# train prompts — should fire the train rule
@pytest.mark.parametrize("prompt", [
    "Build an RL training scaffold: 64 parallel envs via clone_envs",
    "Launch RL-Games or RSL-RL on isaaclab env",
    "Build a defect-introduction SDG pipeline with domain randomization",
    "sim-to-real gap measurement: replay rosbag and compare sim observations",
    "sim2real transfer: measure sim to real gap on joint trajectories",
])
def test_extractor_detects_train(prompt):
    assert _detect_pattern_hint(prompt) == "train", f"expected train for: {prompt!r}"


# ---------------------------------------------------------------------------
# §4 — Rule-based extractor: backward compat with original 4 patterns
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("hint,prompt", [
    ("reorient", "reorient the cube so it stands upright"),
    ("reorient", "flip the block to correct orientation"),
    ("sort", "sort cubes by color into bins"),
    ("sort", "route by class to correct destination"),
    ("navigate", "navigate the AMR to the pickup station"),
    ("navigate", "drive to goal using wheeled robot"),
    ("pick_place", "pick the cube and place it in the bin"),
    ("pick_place", "pick up workpiece and put it in bin"),
])
def test_extractor_backward_compat(hint, prompt):
    assert _detect_pattern_hint(prompt) == hint, f"expected {hint!r} for: {prompt!r}"


# ---------------------------------------------------------------------------
# §5 — Extractor conservative: ambiguous prompts do NOT fire insert/train
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("prompt", [
    # These should NOT fire insert — no strong insertion keyword
    "pick a cylinder and place it in the slot",
    "move part to fixture",
    # These should NOT fire train — no strong training keyword
    "build a conveyor with sensors",
    "pick_place with a ROS2 bridge",
])
def test_extractor_no_false_positive_insert(prompt):
    result = _detect_pattern_hint(prompt)
    assert result != "insert", f"false positive insert for: {prompt!r}"


@pytest.mark.parametrize("prompt", [
    "build a conveyor with sensors",
    "pick_place with a ROS2 bridge",
    "sort cubes by label",
])
def test_extractor_no_false_positive_train(prompt):
    result = _detect_pattern_hint(prompt)
    assert result != "train", f"false positive train for: {prompt!r}"


# ---------------------------------------------------------------------------
# §6 — No false positive for "other" (extractor never emits "other")
# ---------------------------------------------------------------------------

def test_extractor_never_emits_other():
    """Rule-based extractor has no 'other' rule — unknown prompts fall back to pick_place."""
    prompts = [
        "build a recirculation loop with 4 conveyor segments",
        "drive 12 conveyors via OPC-UA tags at 1 Hz",
        "plc-in-the-loop conveyor with modbus tcp",
    ]
    for p in prompts:
        result = _detect_pattern_hint(p)
        assert result != "other", f"extractor should never emit 'other' for: {p!r}"


# ---------------------------------------------------------------------------
# §7 — LLM_INTENT_JSON_SCHEMA enum matches VALID_PATTERN_HINTS
# ---------------------------------------------------------------------------

def test_llm_json_schema_enum_matches_valid_hints():
    from isaac_assist_service.multimodal.text_modality import LLM_INTENT_JSON_SCHEMA
    schema_enum = set(
        LLM_INTENT_JSON_SCHEMA["properties"]["pattern_hint"]["enum"]
    )
    assert schema_enum == EXPECTED_HINTS, (
        f"LLM_INTENT_JSON_SCHEMA enum {schema_enum} != VALID_PATTERN_HINTS {EXPECTED_HINTS}"
    )
