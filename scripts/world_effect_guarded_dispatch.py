"""Fresh-evidence, single-use dispatch for issued world-effect leases.

This is the first authority-bearing boundary in the world-effect pipeline.  It
can mint one short-lived permit and call one runtime-registered handler for the
exact invocation digest in an active lease.  Any fresh invalidation revokes the
lease before handler resolution, and the lease is consumed after one call.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import re
import time
from typing import Any, Callable, Mapping, Sequence

try:
    from .world_effect_runtime_lease import (
        RevocableWorldEffectRuntimeLease,
        WorldEffectRuntimeLeaseError,
    )
except ImportError:  # Script execution adds this directory directly to sys.path.
    from world_effect_runtime_lease import (  # type: ignore[no-redef]
        RevocableWorldEffectRuntimeLease,
        WorldEffectRuntimeLeaseError,
    )


WORLD_EFFECT_DISPATCH_EVIDENCE_SCHEMA_VERSION = "world-effect-dispatch-evidence.v1"
WORLD_EFFECT_DISPATCH_PERMIT_SCHEMA_VERSION = "world-effect-dispatch-permit.v1"
WORLD_EFFECT_DISPATCH_OUTCOME_SCHEMA_VERSION = "world-effect-dispatch-outcome.v1"
_IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9_.:/-]{0,127}$")
WorldEffectHandler = Callable[
    [Mapping[str, Any], Mapping[str, Any], RevocableWorldEffectRuntimeLease],
    Mapping[str, Any],
]


class WorldEffectGuardedDispatchError(RuntimeError):
    """Raised when fresh evidence or single-use authority rejects dispatch."""


def _identifier(value: Any, path: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise WorldEffectGuardedDispatchError(f"{path} has an invalid format")
    return value


def _text(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WorldEffectGuardedDispatchError(f"{path} must be non-empty text")
    return value.strip()


def _json_copy(value: Any, path: str, *, depth: int = 0) -> Any:
    if depth > 14:
        raise WorldEffectGuardedDispatchError(f"{path} exceeds maximum nesting depth")
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise WorldEffectGuardedDispatchError(f"{path} contains non-finite data")
        return value
    if isinstance(value, (list, tuple)):
        return [
            _json_copy(item, f"{path}[{index}]", depth=depth + 1)
            for index, item in enumerate(value)
        ]
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key:
                raise WorldEffectGuardedDispatchError(
                    f"{path} keys must be non-empty strings"
                )
            result[key] = _json_copy(item, f"{path}.{key}", depth=depth + 1)
        return result
    raise WorldEffectGuardedDispatchError(f"{path} must be JSON-compatible")


def _digest(value: Any) -> str:
    encoded = json.dumps(
        _json_copy(value, "digest_value"),
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


def _positive_seconds(value: Any, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise WorldEffectGuardedDispatchError(f"{path} must be a number")
    numeric = float(value)
    if not math.isfinite(numeric) or numeric <= 0.0:
        raise WorldEffectGuardedDispatchError(
            f"{path} must be finite and greater than zero"
        )
    return numeric


def _finite_vector3(value: Any) -> tuple[float, float, float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        return None
    if any(
        isinstance(item, bool)
        or not isinstance(item, (int, float))
        or not math.isfinite(float(item))
        for item in value
    ):
        return None
    return tuple(float(item) for item in value)


def _distance3(
    first: Sequence[float], second: Sequence[float]
) -> float:
    return math.sqrt(sum((left - right) ** 2 for left, right in zip(first, second)))


def assess_fused_target_geometry(
    *,
    baseline_geometry: Mapping[str, Any],
    current_geometry: Mapping[str, Any],
    maximum_center_shift_m: float,
    maximum_extent_change_fraction: float,
    baseline_tracked_position_m: Sequence[float] | None = None,
    current_tracked_position_m: Sequence[float] | None = None,
) -> dict[str, Any]:
    """Fuse tracked translation with RGB-D visibility/shape measurements.

    A segmented point-cloud centroid changes with viewpoint and occlusion, so it
    is retained as diagnostic evidence but is not treated as physical target
    translation when a runtime pose tracker is available.  This keeps the
    contract portable: simulation may back the tracker with rigid-body state,
    while a real deployment may use an RGB-D/ROS object-pose tracker.
    """
    if (
        isinstance(maximum_center_shift_m, bool)
        or not isinstance(maximum_center_shift_m, (int, float))
        or not math.isfinite(float(maximum_center_shift_m))
        or float(maximum_center_shift_m) <= 0.0
    ):
        raise WorldEffectGuardedDispatchError(
            "maximum_center_shift_m must be finite and greater than zero"
        )
    center_limit = float(maximum_center_shift_m)
    if (
        isinstance(maximum_extent_change_fraction, bool)
        or not isinstance(maximum_extent_change_fraction, (int, float))
        or not math.isfinite(float(maximum_extent_change_fraction))
        or float(maximum_extent_change_fraction) < 0.0
    ):
        raise WorldEffectGuardedDispatchError(
            "maximum_extent_change_fraction must be finite and non-negative"
        )
    extent_limit = float(maximum_extent_change_fraction)
    baseline_center = _finite_vector3(baseline_geometry.get("center_base_m"))
    current_center = _finite_vector3(current_geometry.get("center_base_m"))
    baseline_extent = _finite_vector3(
        baseline_geometry.get("visible_extent_base_m")
    )
    current_extent = _finite_vector3(current_geometry.get("visible_extent_base_m"))
    if baseline_center is None or current_center is None:
        raise WorldEffectGuardedDispatchError(
            "fused geometry assessment requires two finite RGB-D centers"
        )
    rgbd_center_shift = _distance3(baseline_center, current_center)
    tracked_baseline = _finite_vector3(baseline_tracked_position_m)
    tracked_current = _finite_vector3(current_tracked_position_m)
    tracker_available = tracked_baseline is not None and tracked_current is not None
    tracked_center_shift = (
        _distance3(tracked_baseline, tracked_current)
        if tracker_available
        else None
    )
    center_shift = (
        float(tracked_center_shift)
        if tracked_center_shift is not None
        else rgbd_center_shift
    )
    predicted_visible_center = baseline_center
    if tracker_available:
        assert tracked_baseline is not None and tracked_current is not None
        predicted_visible_center = tuple(
            visible + current - baseline
            for visible, baseline, current in zip(
                baseline_center, tracked_baseline, tracked_current
            )
        )
    rgbd_center_residual = _distance3(predicted_visible_center, current_center)
    extent_fraction = 0.0
    if baseline_extent is not None and current_extent is not None:
        extent_fraction = max(
            abs(current - baseline) / max(abs(baseline), 1.0e-6)
            for baseline, current in zip(baseline_extent, current_extent)
        )
    return {
        "center_translation_source": (
            "tracked_entity_pose" if tracker_available else "rgbd_visible_centroid"
        ),
        "center_shift_m": center_shift,
        "tracked_center_shift_m": tracked_center_shift,
        "rgbd_visible_center_shift_m": rgbd_center_shift,
        "rgbd_visible_center_residual_m": rgbd_center_residual,
        "maximum_center_shift_m": center_limit,
        "extent_change_fraction": extent_fraction,
        "maximum_extent_change_fraction": extent_limit,
        "center_shift_exceeded": center_shift > center_limit,
        "extent_change_exceeded": extent_fraction > extent_limit,
        "invalidated": (
            center_shift > center_limit or extent_fraction > extent_limit
        ),
    }


def interaction_obstacle_geometry(
    geometries: Mapping[str, Mapping[str, Any]],
    *,
    interaction_target_entity_id: str | None,
) -> dict[str, Mapping[str, Any]]:
    """Return collision geometry without the entity intentionally approached.

    A path-clearance lease must not classify its selected contact target as an
    obstacle: clearance is expected to converge to zero at contact. Every other
    observed entity remains in the obstacle set, including receptacles and
    nearby task objects.
    """
    if not isinstance(geometries, Mapping):
        raise WorldEffectGuardedDispatchError("geometries must be an object")
    if interaction_target_entity_id is not None:
        _identifier(
            interaction_target_entity_id,
            "interaction_target_entity_id",
        )
    result: dict[str, Mapping[str, Any]] = {}
    for entity_id, geometry in geometries.items():
        _identifier(entity_id, "geometry entity_id")
        if not isinstance(geometry, Mapping):
            raise WorldEffectGuardedDispatchError(
                f"geometry for {entity_id!r} must be an object"
            )
        if entity_id != interaction_target_entity_id:
            result[entity_id] = geometry
    return result


@dataclass(frozen=True)
class DispatchInvalidationEvent:
    condition_id: str
    evidence_source_id: str
    evidence: Mapping[str, Any]
    reason: str

    def __post_init__(self) -> None:
        _identifier(self.condition_id, "condition_id")
        _identifier(self.evidence_source_id, "evidence_source_id")
        _json_copy(self.evidence, "evidence")
        _text(self.reason, "reason")

    def to_dict(self) -> dict[str, Any]:
        return {
            "condition_id": self.condition_id,
            "evidence_source_id": self.evidence_source_id,
            "evidence": _json_copy(self.evidence, "evidence"),
            "reason": self.reason,
        }


@dataclass(frozen=True)
class FreshDispatchEvidence:
    evidence_id: str
    issued_lease_id: str
    invocation_digest: str
    observed_at_monotonic_ns: int
    source: str
    observation: Mapping[str, Any]
    invalidation_events: tuple[DispatchInvalidationEvent, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": WORLD_EFFECT_DISPATCH_EVIDENCE_SCHEMA_VERSION,
            "evidence_id": self.evidence_id,
            "issued_lease_id": self.issued_lease_id,
            "invocation_digest": self.invocation_digest,
            "observed_at_monotonic_ns": self.observed_at_monotonic_ns,
            "source": self.source,
            "observation": _json_copy(self.observation, "observation"),
            "invalidation_events": [
                item.to_dict() for item in self.invalidation_events
            ],
            "fresh_evidence": True,
        }


def build_fresh_dispatch_evidence(
    *,
    runtime_lease: RevocableWorldEffectRuntimeLease,
    source: str,
    observation: Mapping[str, Any],
    invalidation_events: tuple[DispatchInvalidationEvent, ...] = (),
    observed_at_monotonic_ns: int | None = None,
) -> FreshDispatchEvidence:
    """Bind a fresh runtime observation to one exact issued invocation."""
    source = _identifier(source, "source")
    observation_copy = _json_copy(observation, "observation")
    observed_at = (
        time.monotonic_ns()
        if observed_at_monotonic_ns is None
        else observed_at_monotonic_ns
    )
    if isinstance(observed_at, bool) or not isinstance(observed_at, int):
        raise WorldEffectGuardedDispatchError(
            "observed_at_monotonic_ns must be an integer"
        )
    event_ids = [item.condition_id for item in invalidation_events]
    if len(event_ids) != len(set(event_ids)):
        raise WorldEffectGuardedDispatchError(
            "fresh evidence invalidation condition ids must be unique"
        )
    seed = {
        "issued_lease_id": runtime_lease.lease.issued_lease_id,
        "invocation_digest": runtime_lease.lease.invocation_digest,
        "observed_at_monotonic_ns": observed_at,
        "source": source,
        "observation": observation_copy,
        "invalidation_events": [item.to_dict() for item in invalidation_events],
    }
    return FreshDispatchEvidence(
        evidence_id="dispatch-evidence:" + _digest(seed),
        issued_lease_id=runtime_lease.lease.issued_lease_id,
        invocation_digest=runtime_lease.lease.invocation_digest,
        observed_at_monotonic_ns=observed_at,
        source=source,
        observation=observation_copy,
        invalidation_events=invalidation_events,
    )


@dataclass(frozen=True)
class WorldEffectDispatchPermit:
    permit_id: str
    issued_lease_id: str
    invocation_digest: str
    evidence_id: str
    tool_id: str
    handler_registration_digest: str
    issued_at_monotonic_ns: int
    expires_at_monotonic_ns: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": WORLD_EFFECT_DISPATCH_PERMIT_SCHEMA_VERSION,
            "permit_id": self.permit_id,
            "issued_lease_id": self.issued_lease_id,
            "invocation_digest": self.invocation_digest,
            "evidence_id": self.evidence_id,
            "tool_id": self.tool_id,
            "handler_registration_digest": self.handler_registration_digest,
            "issued_at_monotonic_ns": self.issued_at_monotonic_ns,
            "expires_at_monotonic_ns": self.expires_at_monotonic_ns,
            "single_use": True,
            "dispatch_permit_issued": True,
            "execution_authority": True,
            "authority_scope": ["invoke_exact_tool_once"],
        }


@dataclass(frozen=True)
class WorldEffectDispatchOutcome:
    permit_id: str
    issued_lease_id: str
    invocation_digest: str
    tool_id: str
    handler_result: Mapping[str, Any]
    final_lease_state: str
    completed_at_monotonic_ns: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": WORLD_EFFECT_DISPATCH_OUTCOME_SCHEMA_VERSION,
            "permit_id": self.permit_id,
            "issued_lease_id": self.issued_lease_id,
            "invocation_digest": self.invocation_digest,
            "tool_id": self.tool_id,
            "handler_result": _json_copy(self.handler_result, "handler_result"),
            "final_lease_state": self.final_lease_state,
            "completed_at_monotonic_ns": self.completed_at_monotonic_ns,
            "handler_bound": True,
            "tool_called": True,
            "dispatch_performed": True,
            "dispatch_permit_consumed": True,
            "dispatch_enabled": False,
            "motion_authority": False,
            "execution_authority": False,
            "requires_fresh_observation": True,
        }


class RuntimeWorldEffectHandlerRegistry:
    """Resolve runtime handlers by the same tool id advertised to planning."""

    def __init__(self) -> None:
        self._handlers: dict[str, WorldEffectHandler] = {}

    def register(self, tool_id: str, handler: WorldEffectHandler) -> None:
        tool_id = _identifier(tool_id, "tool_id")
        if tool_id in self._handlers:
            raise WorldEffectGuardedDispatchError(
                f"handler for tool {tool_id!r} is already registered"
            )
        if not callable(handler):
            raise WorldEffectGuardedDispatchError("handler must be callable")
        self._handlers[tool_id] = handler

    def resolve(self, tool_id: str) -> WorldEffectHandler | None:
        return self._handlers.get(tool_id)

    def registration_digest(self, tool_id: str) -> str:
        if tool_id not in self._handlers:
            raise WorldEffectGuardedDispatchError(
                f"no runtime handler is registered for tool {tool_id!r}"
            )
        return "handler-registration:" + _digest(
            {"tool_id": tool_id, "registered": True}
        )


class GuardedWorldEffectDispatcher:
    """Mint and consume one exact permit for one active runtime lease."""

    def __init__(
        self,
        *,
        runtime_lease: RevocableWorldEffectRuntimeLease,
        handlers: RuntimeWorldEffectHandlerRegistry,
        maximum_evidence_age_s: float = 0.5,
        maximum_permit_lifetime_s: float = 0.5,
        clock_ns: Callable[[], int] = time.monotonic_ns,
    ) -> None:
        self.runtime_lease = runtime_lease
        self.handlers = handlers
        self.maximum_evidence_age_s = _positive_seconds(
            maximum_evidence_age_s, "maximum_evidence_age_s"
        )
        self.maximum_permit_lifetime_s = _positive_seconds(
            maximum_permit_lifetime_s, "maximum_permit_lifetime_s"
        )
        self._clock_ns = clock_ns
        self._permit: WorldEffectDispatchPermit | None = None
        self._permit_consumed = False

    def mint_permit(
        self, evidence: FreshDispatchEvidence
    ) -> WorldEffectDispatchPermit:
        if self._permit is not None:
            raise WorldEffectGuardedDispatchError(
                "this dispatcher has already minted its single permit"
            )
        self.runtime_lease.assert_active()
        lease = self.runtime_lease.lease
        if (
            evidence.issued_lease_id != lease.issued_lease_id
            or evidence.invocation_digest != lease.invocation_digest
        ):
            raise WorldEffectGuardedDispatchError(
                "fresh evidence does not bind the exact issued invocation"
            )
        now_ns = self._clock_ns()
        age_ns = now_ns - evidence.observed_at_monotonic_ns
        if age_ns < 0 or age_ns > int(self.maximum_evidence_age_s * 1_000_000_000):
            raise WorldEffectGuardedDispatchError(
                "dispatch evidence is stale or from the future"
            )
        if evidence.invalidation_events:
            event = evidence.invalidation_events[0]
            try:
                self.runtime_lease.observe_invalidation(
                    event.condition_id, event.evidence
                )
            except WorldEffectRuntimeLeaseError as exc:
                raise WorldEffectGuardedDispatchError(str(exc)) from exc
            raise WorldEffectGuardedDispatchError(
                f"fresh evidence invalidated the lease: {event.reason}"
            )
        handler_digest = self.handlers.registration_digest(lease.tool_id)
        permit_expiry_ns = min(
            lease.expires_at_monotonic_ns,
            now_ns + int(self.maximum_permit_lifetime_s * 1_000_000_000),
        )
        if permit_expiry_ns <= now_ns:
            raise WorldEffectGuardedDispatchError(
                "issued lease expires before a dispatch permit can be used"
            )
        seed = {
            "issued_lease_id": lease.issued_lease_id,
            "invocation_digest": lease.invocation_digest,
            "evidence_id": evidence.evidence_id,
            "tool_id": lease.tool_id,
            "handler_registration_digest": handler_digest,
            "issued_at_monotonic_ns": now_ns,
            "expires_at_monotonic_ns": permit_expiry_ns,
        }
        self._permit = WorldEffectDispatchPermit(
            permit_id="dispatch-permit:" + _digest(seed),
            issued_lease_id=lease.issued_lease_id,
            invocation_digest=lease.invocation_digest,
            evidence_id=evidence.evidence_id,
            tool_id=lease.tool_id,
            handler_registration_digest=handler_digest,
            issued_at_monotonic_ns=now_ns,
            expires_at_monotonic_ns=permit_expiry_ns,
        )
        return self._permit

    def dispatch(
        self, permit: WorldEffectDispatchPermit
    ) -> WorldEffectDispatchOutcome:
        if self._permit is None or permit.permit_id != self._permit.permit_id:
            raise WorldEffectGuardedDispatchError(
                "dispatch permit was not minted by this dispatcher"
            )
        if self._permit_consumed:
            raise WorldEffectGuardedDispatchError(
                "dispatch permit has already been consumed"
            )
        now_ns = self._clock_ns()
        if now_ns >= permit.expires_at_monotonic_ns:
            raise WorldEffectGuardedDispatchError("dispatch permit has expired")
        self.runtime_lease.assert_active()
        lease = self.runtime_lease.lease
        if (
            permit.issued_lease_id != lease.issued_lease_id
            or permit.invocation_digest != lease.invocation_digest
            or permit.tool_id != lease.tool_id
            or permit.handler_registration_digest
            != self.handlers.registration_digest(lease.tool_id)
        ):
            raise WorldEffectGuardedDispatchError(
                "dispatch permit identity no longer matches the runtime lease"
            )
        handler = self.handlers.resolve(lease.tool_id)
        if handler is None:
            raise WorldEffectGuardedDispatchError(
                "runtime handler disappeared after permit issuance"
            )
        self._permit_consumed = True
        try:
            result = handler(
                lease.invocation_arguments,
                lease.tool_configuration,
                self.runtime_lease,
            )
            result_copy = _json_copy(result, "handler_result")
            if not isinstance(result_copy, dict):
                raise WorldEffectGuardedDispatchError(
                    "runtime handler result must be an object"
                )
        except Exception as exc:
            if self.runtime_lease.active:
                self.runtime_lease.revoke(
                    reason="dispatch.handler_error",
                    evidence={"type": type(exc).__name__, "message": str(exc)},
                )
            raise WorldEffectGuardedDispatchError(
                f"runtime handler failed: {type(exc).__name__}: {exc}"
            ) from exc
        if self.runtime_lease.active:
            self.runtime_lease.consume()
        completed_at_ns = self._clock_ns()
        return WorldEffectDispatchOutcome(
            permit_id=permit.permit_id,
            issued_lease_id=lease.issued_lease_id,
            invocation_digest=lease.invocation_digest,
            tool_id=lease.tool_id,
            handler_result=result_copy,
            final_lease_state=self.runtime_lease.state,
            completed_at_monotonic_ns=completed_at_ns,
        )
