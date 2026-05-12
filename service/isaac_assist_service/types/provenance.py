"""
Shared provenance primitive for IA — Source.

A `Source` is a `(stage, confidence_0_1, ts_utc)` triple that lets any
field carry the origin of its value: which pipeline stage produced it
(analytical / sim / LLM extraction / user input), how confident that
stage was, and when. Pure metadata — never read by retrieval,
instantiation, or verification.

Zero internal IA dependencies — see __init__.py.
"""
from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Source(BaseModel):
    """Provenance triple for a single field value.

    Attributes:
        stage: name of the pipeline stage that produced the value
            (e.g. `"vlm_extract"`, `"sim_replicate"`, `"math_critic"`).
            Free-form string by design — registries are owned by
            consumers, not this primitive.
        confidence_0_1: confidence the producing stage assigns to the
            value, on `[0.0, 1.0]`. Semantics differ per stage; downstream
            consumers MUST document how they interpret it.
        ts_utc: UTC timestamp at the moment the value was produced.
            Timezone-aware; naive datetimes are rejected so cross-host
            log merging stays unambiguous.
    """

    model_config = ConfigDict(frozen=True)

    stage: str = Field(min_length=1)
    confidence_0_1: float = Field(ge=0.0, le=1.0)
    ts_utc: datetime

    @field_validator("ts_utc")
    @classmethod
    def _ts_must_be_utc_aware(cls, v: datetime) -> datetime:
        """Reject naive datetimes; normalize aware ones to UTC."""
        if v.tzinfo is None or v.tzinfo.utcoffset(v) is None:
            raise ValueError(
                "Source.ts_utc must be timezone-aware; got naive datetime "
                f"{v!r}. Use `datetime.now(timezone.utc)` or attach tzinfo."
            )
        # Normalize to UTC so equality comparisons across log sources work.
        return v.astimezone(timezone.utc)
