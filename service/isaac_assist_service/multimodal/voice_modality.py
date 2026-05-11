"""
Voice-modality producer per spec §10.4.

Routes through STT → text-prompt path. No new boundaries beyond chaining
existing components. LayoutSpec.source.modality is "voice" for telemetry;
the production pipe is the same as text after transcription.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Callable, Optional

from .text_modality import extract_intent_rules
from .types import LayoutSpec, Source

logger = logging.getLogger(__name__)


def produce_layout_spec_from_voice(
    audio_or_transcript,
    *,
    transcribe: Optional[Callable[[bytes], str]] = None,
    extractor: Optional[Callable[[str], "Intent"]] = None,  # noqa: F821
    confidence: float = 0.6,
) -> LayoutSpec:
    """Top-level voice-modality producer per spec §10.4.

    Args:
        audio_or_transcript: either raw audio bytes (transcribe must be
            provided) or a pre-transcribed string (transcribe ignored)
        transcribe: optional callable that converts audio bytes → text;
            required when audio_or_transcript is bytes
        extractor: optional Intent extractor override; defaults to
            rule-based text extractor
        confidence: default voice confidence (0.5-0.8 per §7.2 reliability
            profile; 0.6 mid-range default)

    Returns:
        LayoutSpec with source.modality = "voice", raw_input = transcript
    """
    if isinstance(audio_or_transcript, (bytes, bytearray)):
        if transcribe is None:
            raise ValueError(
                "audio bytes given but no transcribe callable provided"
            )
        transcript = transcribe(bytes(audio_or_transcript))
    elif isinstance(audio_or_transcript, str):
        transcript = audio_or_transcript
    else:
        raise TypeError(
            f"audio_or_transcript must be bytes or str; got "
            f"{type(audio_or_transcript).__name__}"
        )

    extract = extractor or extract_intent_rules
    intent = extract(transcript)

    return LayoutSpec(
        intent=intent,
        source=Source(
            modality="voice",
            confidence=confidence,
            timestamp=datetime.now(timezone.utc),
            raw_input=transcript,
        ),
        objects=[],
        bindings=None,
        revision=1,
    )
