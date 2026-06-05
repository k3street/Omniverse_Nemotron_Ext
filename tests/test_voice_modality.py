"""Tests for service/isaac_assist_service/multimodal/voice_modality.py.

Block 3 Step 20: voice → STT → LayoutSpec.intent producer.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.l0

from service.isaac_assist_service.multimodal.voice_modality import (
    produce_layout_spec_from_voice,
)


def test_voice_from_pretranscribed_string():
    spec = produce_layout_spec_from_voice("pick a cube into a bin")
    assert spec.source.modality == "voice"
    assert spec.intent.pattern_hint == "pick_place"
    assert spec.objects == []


def test_voice_confidence_default_lower_than_text():
    """Voice default confidence (0.6) is in 0.5-0.8 band per §7.2 profile."""
    spec = produce_layout_spec_from_voice("anything")
    assert 0.5 <= spec.source.confidence <= 0.8


def test_voice_audio_bytes_requires_transcribe_callback():
    with pytest.raises(ValueError, match="transcribe"):
        produce_layout_spec_from_voice(b"\x00\x01\x02")


def test_voice_audio_bytes_uses_transcribe_callback():
    """Pipe: audio bytes → transcribe(bytes) → extract_intent_rules → LayoutSpec."""
    def stub_stt(audio: bytes) -> str:
        assert isinstance(audio, bytes)
        return "sort cubes by color"

    spec = produce_layout_spec_from_voice(b"audio", transcribe=stub_stt)
    assert spec.intent.pattern_hint == "sort"
    assert spec.source.raw_input == "sort cubes by color"


def test_voice_rejects_invalid_input_type():
    with pytest.raises(TypeError):
        produce_layout_spec_from_voice(123)


def test_voice_custom_extractor_override():
    from service.isaac_assist_service.multimodal.types import Intent

    spec = produce_layout_spec_from_voice(
        "ignored",
        extractor=lambda _p: Intent(pattern_hint="reorient"),
    )
    assert spec.intent.pattern_hint == "reorient"


def test_voice_preserves_transcript_as_raw_input():
    spec = produce_layout_spec_from_voice("hello world")
    assert spec.source.raw_input == "hello world"
