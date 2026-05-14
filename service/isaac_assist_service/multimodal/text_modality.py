"""
Text-prompt modality producer per spec §7.3.

A text prompt becomes a `LayoutSpec` containing only `intent` (no objects,
no bindings). The LLM extracts structured intent; deterministic
rule-based extraction is the offline fallback.

Per the modality boundary contract:
- text modality intersects the build path at exactly one place — intent
  extraction — and produces a typed artifact with no string-substitution
  downstream
- objects and bindings remain empty; canonical template's authored
  positions become bindings at exec time via code_template substitution

Two production paths:
1. `extract_intent_rules(prompt)` — deterministic rule-based extractor.
   Used for unit tests, offline workflows, and as LLM-fallback when
   LLM call fails.
2. `extract_intent_llm(prompt, llm_client)` — LLM-based extractor that
   prompts a model with a strict JSON schema constraint to produce
   pattern_hint + features + tags + counts.

Both return a `LayoutSpec` validated against multimodal/validate.py.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from .types import (
    Counts,
    Intent,
    LayoutSpec,
    PatternHint,
    Source,
    StructuralFeatures,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Rule-based pattern_hint detection
# ---------------------------------------------------------------------------

#: Match keywords → pattern_hint. Order matters: more specific patterns first.
#: All matching is case-insensitive; word-boundary regexes prevent
#: "navigation" from matching "navigate".
_PATTERN_RULES: List[tuple] = [
    # reorient: flip / upright / orient
    ("reorient", re.compile(r"\b(reorient|flip|upright|tip\s+over|stand\s+up|rotate.*correct)\b", re.I)),
    # sort: routing by color/class/label
    ("sort", re.compile(r"\b(sort|sortation|route|routing|classify|by\s+color|color.*bin|by\s+class|by\s+label)\b", re.I)),
    # navigate: AMR / mobile / move-to-pose
    ("navigate", re.compile(r"\b(navigate|amr|mobile|drive\s+to|navigate\s+to|wheeled|move\s+to)\b", re.I)),
    # pick_place: the catchall for pick / place / pickup / drop
    ("pick_place", re.compile(r"\b(pick.{0,15}place|pickplace|pick.up|pick\s+and\s+drop|move.*bin|put.*bin)\b", re.I)),
]


def _detect_pattern_hint(prompt: str) -> PatternHint:
    """Returns the first matching pattern_hint per rule order. Defaults to
    pick_place — the broadest match — when nothing fires."""
    for pat, regex in _PATTERN_RULES:
        if regex.search(prompt):
            return pat  # type: ignore[return-value]
    return "pick_place"


# ---------------------------------------------------------------------------
# Rule-based structural-feature detection
# ---------------------------------------------------------------------------

#: Feature flag → list of triggering regexes. ALL matches set the flag True;
#: no match leaves the default. Word-boundary anchored where possible.
_FEATURE_RULES: Dict[str, List[re.Pattern]] = {
    "uses_conveyor_transport": [
        re.compile(r"\b(conveyors?|belts?|conveyance)\b", re.I),
    ],
    "uses_navigation": [
        re.compile(r"\b(navigate|amrs?|mobile\s+robots?|wheeled\s+robots?)\b", re.I),
    ],
    "has_color_routing": [
        re.compile(r"\b(color.*bin|by\s+color|color.*routing|red.*blue|sort.*color)\b", re.I),
    ],
    "has_orientation_requirement": [
        re.compile(r"\b(upright|orient|flip|reorient|stand|side|side.up)\b", re.I),
    ],
    "has_bounded_footprint": [
        re.compile(r"\b(within\s+\d|footprint|compact|bounded|fit.*\d.*m|small.*cell)\b", re.I),
    ],
    "has_human_in_workspace": [
        re.compile(r"\b(human|worker|operator|collaborative|cobot.*human|alongside)\b", re.I),
    ],
}


def _detect_features(prompt: str, pattern: PatternHint) -> StructuralFeatures:
    """Extract structural feature flags from a prompt string using ``_FEATURE_RULES``."""
    feats: Dict[str, Any] = {}
    for flag, regexes in _FEATURE_RULES.items():
        if any(r.search(prompt) for r in regexes):
            feats[flag] = True

    # Pattern-implied features
    if pattern == "reorient":
        feats["has_orientation_requirement"] = True

    return StructuralFeatures(**feats)


# ---------------------------------------------------------------------------
# Rule-based count + cardinality detection
# ---------------------------------------------------------------------------

_NUM_WORDS: Dict[str, int] = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "a": 1, "an": 1,
}


def _parse_count_phrase(prompt: str, noun_pat: str) -> Optional[int]:
    """Find phrases like '2 robots' / 'two conveyors' / 'a robot' before the
    given noun pattern. Returns integer count or None."""
    re_num = re.compile(rf"(\d+)\s+{noun_pat}s?\b", re.I)
    m = re_num.search(prompt)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            pass
    re_word = re.compile(rf"\b({'|'.join(_NUM_WORDS.keys())})\s+{noun_pat}s?\b", re.I)
    m = re_word.search(prompt)
    if m:
        return _NUM_WORDS[m.group(1).lower()]
    return None


def _detect_counts(prompt: str) -> Counts:
    """Best-effort count extraction. Missing counts leave defaults."""
    c: Dict[str, int] = {}
    for noun, pat in [
        ("robots", r"(?:robot|franka|ur5e|kinova|arm)"),
        ("conveyors", r"(?:conveyor|belt)"),
        ("bins", r"(?:bin|container)"),
        ("cubes", r"(?:cube|block|workpiece|part)"),
        ("sensors", r"sensor"),
        ("humans", r"(?:human|worker|operator)"),
    ]:
        n = _parse_count_phrase(prompt, pat)
        if n is not None and n > 0:
            c[noun] = n
    return Counts(**c)


def _derive_n_robot_stations(counts: Counts, prompt: str) -> int:
    """n_robot_stations is a structural feature distinct from counts.robots —
    it counts distinct robot stations in the topology. For most pick_place
    layouts robot_stations == counts.robots. Returns at least 1."""
    if counts.robots > 0:
        return counts.robots
    return 1


# ---------------------------------------------------------------------------
# Public API — rule-based extractor
# ---------------------------------------------------------------------------

def extract_intent_rules(prompt: str) -> Intent:
    """Deterministic rule-based intent extraction.

    Used in tests, offline workflows, and as fallback when LLM extraction
    is unavailable or fails. Output is conservative (no false-positive
    feature flags). Confidence is intentionally not exposed here — the
    rule-based path is deterministic and observable; LayoutSpec.source
    carries the confidence number.
    """
    pattern = _detect_pattern_hint(prompt)
    counts = _detect_counts(prompt)
    features = _detect_features(prompt, pattern)

    # n_robot_stations may exceed default if counts.robots > 1
    if counts.robots > 1:
        features.n_robot_stations = counts.robots
    if features.uses_conveyor_transport and counts.conveyors > 1:
        features.n_handoffs = max(features.n_handoffs, counts.conveyors - 1)

    return Intent(
        pattern_hint=pattern,
        counts=counts,
        structural_features=features,
        structural_tags=[],
    )


def produce_layout_spec_from_text(
    prompt: str,
    *,
    extractor: Optional[Callable[[str], Intent]] = None,
    confidence: float = 0.7,
) -> LayoutSpec:
    """Top-level text-modality producer per spec §7.3.

    Returns a LayoutSpec with:
    - intent populated by `extractor` (default: extract_intent_rules)
    - objects = []
    - bindings = {} (None per type)
    - source.modality = "text", source.confidence default 0.7

    Text-prompt path: no objects, no bindings. Canonical template's
    authored positions provide them at exec time via code_template
    substitution.
    """
    extract = extractor or extract_intent_rules
    intent = extract(prompt)

    return LayoutSpec(
        intent=intent,
        source=Source(
            modality="text",
            confidence=confidence,
            timestamp=datetime.now(timezone.utc),
            raw_input=prompt,
        ),
        objects=[],
        bindings=None,
        revision=1,
    )


# ---------------------------------------------------------------------------
# LLM-based extractor (hook — concrete client wired by orchestrator)
# ---------------------------------------------------------------------------

LLM_INTENT_JSON_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["pattern_hint"],
    "properties": {
        "pattern_hint": {
            "type": "string",
            "enum": ["pick_place", "sort", "reorient", "navigate"],
        },
        "counts": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "robots": {"type": "integer", "minimum": 0},
                "conveyors": {"type": "integer", "minimum": 0},
                "bins": {"type": "integer", "minimum": 0},
                "cubes": {"type": "integer", "minimum": 0},
                "sensors": {"type": "integer", "minimum": 0},
                "humans": {"type": "integer", "minimum": 0},
            },
        },
        "structural_features": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "n_robot_stations": {"type": "integer", "minimum": 1},
                "n_handoffs": {"type": "integer", "minimum": 0},
                "n_destinations": {"type": "integer", "minimum": 1},
                "destination_kind": {
                    "type": "string",
                    "enum": ["single_bin", "n_bins_routed", "shelf", "fixture"],
                },
                "routing_axis": {
                    "type": ["string", "null"],
                    "enum": ["color", "size", "shape", "label", None],
                },
                "uses_conveyor_transport": {"type": "boolean"},
                "uses_navigation": {"type": "boolean"},
                "has_color_routing": {"type": "boolean"},
                "has_orientation_requirement": {"type": "boolean"},
                "has_bounded_footprint": {"type": "boolean"},
                "has_human_in_workspace": {"type": "boolean"},
            },
        },
        "structural_tags": {
            "type": "array",
            "items": {
                "type": "string",
                "pattern": r"^(isaac|cad|user):[a-z0-9_]+(\.[a-z0-9_]+)*$",
            },
        },
    },
}


LLM_INTENT_SYSTEM_PROMPT = """You extract structured intent from a text
prompt describing an Isaac Sim robotics task. Output JSON matching the
schema. Use only these patterns:

- pick_place: workpiece picked from source and delivered to destination
- sort: workpiece routed to class-specific destination
- reorient: workpiece picked and oriented (e.g. upright) at destination
- navigate: mobile platform driven to goal pose

Output ONLY the JSON object. No prose, no markdown."""


def extract_intent_llm(
    prompt: str,
    llm_call: Callable[[str, str, Dict], str],
) -> Intent:
    """LLM-based intent extraction.

    Args:
        prompt: the user's text prompt
        llm_call: a function (system, user, schema) → json_string that
            calls whatever LLM client the orchestrator has available

    Returns:
        validated Intent

    Raises:
        ValueError if LLM output is not JSON or fails Intent validation.
    """
    import json

    raw = llm_call(LLM_INTENT_SYSTEM_PROMPT, prompt, LLM_INTENT_JSON_SCHEMA)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"LLM did not return JSON: {e}") from e

    # Pydantic does the rest of the validation
    return Intent.model_validate(data)
