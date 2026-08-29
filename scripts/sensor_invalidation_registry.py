"""Sensor-source-agnostic invalidation predicates for bounded motion leases.

ROS 2 nodes, simulator adapters, and hardware drivers publish observations.
This module only normalizes those observations and evaluates runtime-registered
predicates.  It deliberately does not know about robots, tasks, objects,
camera message types, or motion executors.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import re
from typing import Any, Callable, Mapping, Sequence


_IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9_.:/-]{0,127}$")


class SensorInvalidationError(ValueError):
    """Raised for malformed observation or predicate contracts."""


def _identifier(value: Any, path: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise SensorInvalidationError(f"{path} has an invalid format")
    return value


def _json_value(value: Any, path: str) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise SensorInvalidationError(f"{path} must contain finite values")
        return value
    if isinstance(value, (list, tuple)):
        return [_json_value(item, f"{path}[]") for item in value]
    if isinstance(value, Mapping):
        copied: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key:
                raise SensorInvalidationError(f"{path} keys must be strings")
            copied[key] = _json_value(item, f"{path}.{key}")
        return copied
    raise SensorInvalidationError(f"{path} must be JSON-compatible")


@dataclass(frozen=True)
class SensorObservation:
    """One normalized value from any ROS 2, simulation, or hardware source."""

    channel_id: str
    source_id: str
    sequence: int
    timestamp_s: float
    value: Any
    valid: bool = True
    frame_id: str | None = None

    def __post_init__(self) -> None:
        _identifier(self.channel_id, "channel_id")
        _identifier(self.source_id, "source_id")
        if isinstance(self.sequence, bool) or not isinstance(self.sequence, int):
            raise SensorInvalidationError("sequence must be an integer")
        if self.sequence < 0:
            raise SensorInvalidationError("sequence must be non-negative")
        timestamp = float(self.timestamp_s)
        if not math.isfinite(timestamp) or timestamp < 0.0:
            raise SensorInvalidationError(
                "timestamp_s must be finite and non-negative"
            )
        if not isinstance(self.valid, bool):
            raise SensorInvalidationError("valid must be boolean")
        if self.frame_id is not None:
            _identifier(self.frame_id, "frame_id")
        _json_value(self.value, "value")

    def to_dict(self) -> dict[str, Any]:
        return {
            "channel_id": self.channel_id,
            "source_id": self.source_id,
            "sequence": self.sequence,
            "timestamp_s": float(self.timestamp_s),
            "value": _json_value(self.value, "value"),
            "valid": self.valid,
            "frame_id": self.frame_id,
        }


class SensorObservationSnapshot:
    """Latest normalized observation for each runtime-advertised channel."""

    def __init__(self, observations: Sequence[SensorObservation]):
        self._by_channel: dict[str, SensorObservation] = {}
        for observation in observations:
            if not isinstance(observation, SensorObservation):
                raise SensorInvalidationError(
                    "snapshot entries must be SensorObservation instances"
                )
            if observation.channel_id in self._by_channel:
                raise SensorInvalidationError(
                    f"duplicate observation channel {observation.channel_id!r}"
                )
            self._by_channel[observation.channel_id] = observation

    def get(self, channel_id: str) -> SensorObservation | None:
        return self._by_channel.get(channel_id)

    def channels(self) -> tuple[str, ...]:
        return tuple(sorted(self._by_channel))

    def to_dict(self) -> dict[str, Any]:
        return {
            channel: self._by_channel[channel].to_dict()
            for channel in sorted(self._by_channel)
        }


@dataclass(frozen=True)
class PredicateResult:
    """Result returned by one runtime predicate plugin."""

    valid: bool
    reason: str
    evidence: Mapping[str, Any]

    def __post_init__(self) -> None:
        if not isinstance(self.valid, bool):
            raise SensorInvalidationError("predicate valid must be boolean")
        _identifier(self.reason, "predicate reason")
        _json_value(self.evidence, "predicate evidence")


PredicateEvaluator = Callable[
    [Mapping[str, Any], Mapping[str, Any]], PredicateResult
]


@dataclass(frozen=True)
class SensorPredicateSpec:
    """A predicate plugin registered by an available sensing capability."""

    predicate_id: str
    description: str
    required_channels: tuple[str, ...]
    maximum_age_s: float
    evaluator: PredicateEvaluator

    def __post_init__(self) -> None:
        _identifier(self.predicate_id, "predicate_id")
        if not isinstance(self.description, str) or not self.description.strip():
            raise SensorInvalidationError("description must be non-empty")
        if not self.required_channels:
            raise SensorInvalidationError("required_channels must not be empty")
        for channel in self.required_channels:
            _identifier(channel, "required_channels[]")
        if len(set(self.required_channels)) != len(self.required_channels):
            raise SensorInvalidationError("required_channels contains duplicates")
        maximum_age = float(self.maximum_age_s)
        if not math.isfinite(maximum_age) or maximum_age <= 0.0:
            raise SensorInvalidationError(
                "maximum_age_s must be finite and positive"
            )
        if not callable(self.evaluator):
            raise SensorInvalidationError("evaluator must be callable")


@dataclass(frozen=True)
class SensorPredicateLease:
    """One predicate and parameters attached to a bounded motion lease."""

    predicate_id: str
    parameters: Mapping[str, Any]

    def __post_init__(self) -> None:
        _identifier(self.predicate_id, "predicate_id")
        _json_value(self.parameters, "parameters")

    def to_dict(self) -> dict[str, Any]:
        return {
            "predicate_id": self.predicate_id,
            "parameters": _json_value(self.parameters, "parameters"),
        }


@dataclass(frozen=True)
class SensorPredicateEvaluation:
    predicate_id: str
    valid: bool
    reason: str
    source_ids: tuple[str, ...]
    channel_sequences: Mapping[str, int]
    evidence: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "predicate_id": self.predicate_id,
            "valid": self.valid,
            "reason": self.reason,
            "source_ids": list(self.source_ids),
            "channel_sequences": dict(self.channel_sequences),
            "evidence": _json_value(self.evidence, "evidence"),
        }


@dataclass(frozen=True)
class SensorLeaseAssessment:
    valid: bool
    evaluations: tuple[SensorPredicateEvaluation, ...]

    @property
    def invalidation_events(self) -> tuple[SensorPredicateEvaluation, ...]:
        return tuple(item for item in self.evaluations if not item.valid)

    @property
    def invalidation_reasons(self) -> tuple[str, ...]:
        return tuple(item.reason for item in self.invalidation_events)

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "invalidation_reasons": list(self.invalidation_reasons),
            "invalidation_events": [
                item.to_dict() for item in self.invalidation_events
            ],
            "evaluations": [item.to_dict() for item in self.evaluations],
        }


class SensorPredicateRegistry:
    """Discover and evaluate sensor predicates without encoding their meaning."""

    def __init__(self) -> None:
        self._specs: dict[str, SensorPredicateSpec] = {}

    def register(self, spec: SensorPredicateSpec) -> None:
        if not isinstance(spec, SensorPredicateSpec):
            raise SensorInvalidationError(
                "predicate registration requires a SensorPredicateSpec"
            )
        if spec.predicate_id in self._specs:
            raise SensorInvalidationError(
                f"predicate {spec.predicate_id!r} is already registered"
            )
        self._specs[spec.predicate_id] = spec

    def specs(self) -> tuple[SensorPredicateSpec, ...]:
        return tuple(self._specs[key] for key in sorted(self._specs))

    def assess(
        self,
        leases: Sequence[SensorPredicateLease],
        snapshot: SensorObservationSnapshot,
        *,
        evaluated_at_s: float,
    ) -> SensorLeaseAssessment:
        if not isinstance(snapshot, SensorObservationSnapshot):
            raise SensorInvalidationError(
                "snapshot must be a SensorObservationSnapshot"
            )
        now = float(evaluated_at_s)
        if not math.isfinite(now) or now < 0.0:
            raise SensorInvalidationError(
                "evaluated_at_s must be finite and non-negative"
            )
        evaluations: list[SensorPredicateEvaluation] = []
        for lease in leases:
            if not isinstance(lease, SensorPredicateLease):
                raise SensorInvalidationError(
                    "leases must contain SensorPredicateLease instances"
                )
            spec = self._specs.get(lease.predicate_id)
            if spec is None:
                evaluations.append(
                    SensorPredicateEvaluation(
                        predicate_id=lease.predicate_id,
                        valid=False,
                        reason="predicate_not_registered",
                        source_ids=(),
                        channel_sequences={},
                        evidence={"parameters": lease.to_dict()["parameters"]},
                    )
                )
                continue
            records: dict[str, SensorObservation] = {}
            unavailable_reason: str | None = None
            unavailable_channel: str | None = None
            for channel in spec.required_channels:
                record = snapshot.get(channel)
                if record is None:
                    unavailable_reason = "sensor_observation_missing"
                elif not record.valid:
                    unavailable_reason = "sensor_observation_invalid"
                elif now < float(record.timestamp_s):
                    unavailable_reason = "sensor_observation_from_future"
                elif now - float(record.timestamp_s) > spec.maximum_age_s:
                    unavailable_reason = "sensor_observation_stale"
                else:
                    records[channel] = record
                    continue
                unavailable_channel = channel
                break
            if unavailable_reason is not None:
                evaluations.append(
                    SensorPredicateEvaluation(
                        predicate_id=spec.predicate_id,
                        valid=False,
                        reason=unavailable_reason,
                        source_ids=tuple(
                            sorted({item.source_id for item in records.values()})
                        ),
                        channel_sequences={
                            channel: item.sequence for channel, item in records.items()
                        },
                        evidence={
                            "channel_id": unavailable_channel,
                            "maximum_age_s": spec.maximum_age_s,
                        },
                    )
                )
                continue
            values = {
                channel: records[channel].value for channel in spec.required_channels
            }
            try:
                result = spec.evaluator(values, lease.parameters)
                if not isinstance(result, PredicateResult):
                    raise SensorInvalidationError(
                        "predicate evaluator must return PredicateResult"
                    )
            except Exception as exc:  # predicate plugins fail closed
                result = PredicateResult(
                    valid=False,
                    reason="predicate_evaluation_error",
                    evidence={"error": f"{type(exc).__name__}: {exc}"},
                )
            evaluations.append(
                SensorPredicateEvaluation(
                    predicate_id=spec.predicate_id,
                    valid=result.valid,
                    reason=result.reason,
                    source_ids=tuple(
                        sorted({item.source_id for item in records.values()})
                    ),
                    channel_sequences={
                        channel: item.sequence for channel, item in records.items()
                    },
                    evidence={
                        **_json_value(result.evidence, "predicate evidence"),
                        "parameters": _json_value(lease.parameters, "parameters"),
                    },
                )
            )
        return SensorLeaseAssessment(
            valid=all(item.valid for item in evaluations),
            evaluations=tuple(evaluations),
        )
