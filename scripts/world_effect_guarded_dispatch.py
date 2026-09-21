"""Fresh-evidence, single-use dispatch for issued world-effect leases.

This is the first authority-bearing boundary in the world-effect pipeline.  It
can mint one short-lived permit and call one runtime-registered handler for the
exact invocation digest in an active lease.  Any fresh invalidation revokes the
lease before handler resolution, and the lease is consumed after one call.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
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
_MAX_RELIABLE_EXTENT_CENTROID_RESIDUAL_FRACTION = 0.10
WorldEffectHandler = Callable[
    [Mapping[str, Any], Mapping[str, Any], RevocableWorldEffectRuntimeLease],
    Mapping[str, Any],
]


@dataclass
class TemporalShapeDriftConfirmation:
    """Confirm RGB-D extent-only drift across a bounded observation window.

    Independently tracked translation remains an immediate invalidation.  The
    temporal gate applies only when that tracker says the entity center is
    stable and the sole invalidating signal is a reliable visible-extent
    change.  This filters one-frame segmentation/visibility noise without
    weakening fail-closed behavior when no independent tracker is available.
    """

    required_observations: int = 2
    observation_window: int = 3
    _history: dict[str, deque[bool]] = field(default_factory=dict, init=False)
    _observation_counts: dict[str, int] = field(default_factory=dict, init=False)
    _latest_assessments: dict[str, dict[str, Any]] = field(
        default_factory=dict, init=False
    )

    def __post_init__(self) -> None:
        for name, value in (
            ("required_observations", self.required_observations),
            ("observation_window", self.observation_window),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise WorldEffectGuardedDispatchError(
                    f"{name} must be a positive integer"
                )
        if self.required_observations > self.observation_window:
            raise WorldEffectGuardedDispatchError(
                "required_observations cannot exceed observation_window"
            )

    def assess(
        self,
        entity_id: str,
        assessment: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Apply temporal evidence to one fused geometry assessment."""
        entity_id = _identifier(entity_id, "entity_id")
        result = _json_copy(assessment, "assessment")
        tracker_available = (
            result.get("center_translation_source") == "tracked_entity_pose"
        )
        tracked_center_stable = bool(
            tracker_available
            and not result.get("center_shift_exceeded")
            and isinstance(result.get("tracked_center_shift_m"), (int, float))
        )
        extent_only_candidate = bool(
            tracked_center_stable
            and result.get("extent_change_invalidating")
            and not result.get("center_shift_exceeded")
        )
        history = self._history.setdefault(
            entity_id, deque(maxlen=self.observation_window)
        )
        history.append(extent_only_candidate)
        self._observation_counts[entity_id] = (
            self._observation_counts.get(entity_id, 0) + 1
        )
        positive_count = sum(history)
        confirmed = bool(
            extent_only_candidate
            and positive_count >= self.required_observations
        )

        # Any fused invalidation that is not the tracked-stable, extent-only
        # case remains immediate.  In particular, missing independent tracking
        # never gains temporal grace.
        raw_invalidated = bool(result.get("invalidated"))
        immediate_invalidation = bool(raw_invalidated and not extent_only_candidate)
        result["invalidated"] = bool(immediate_invalidation or confirmed)
        result["temporal_shape_drift_confirmation"] = {
            "eligible": extent_only_candidate,
            "history": list(history),
            "positive_observations": positive_count,
            "required_observations": self.required_observations,
            "observation_window": self.observation_window,
            "confirmed": confirmed,
            "decision": (
                "confirmed_invalidation"
                if confirmed
                else (
                    "pending_confirmation"
                    if extent_only_candidate
                    else "not_applicable"
                )
            ),
        }
        self._latest_assessments[entity_id] = result
        return result

    def to_dict(self) -> dict[str, Any]:
        return {
            "required_observations": self.required_observations,
            "observation_window": self.observation_window,
            "entities": {
                entity_id: {
                    "history": list(history),
                    "positive_observations": sum(history),
                    "total_observations": self._observation_counts.get(entity_id, 0),
                    "latest_assessment": self._latest_assessments.get(entity_id),
                }
                for entity_id, history in sorted(self._history.items())
            },
        }


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
    extent_reference_m = (
        min(value for value in baseline_extent if value > 0.0)
        if baseline_extent is not None
        else None
    )
    extent_centroid_residual_fraction = (
        rgbd_center_residual / extent_reference_m
        if extent_reference_m is not None
        else None
    )
    # A visible AABB is not a stable shape measurement when the segmented
    # surface centroid has shifted materially relative to an independently
    # tracked object center.  That combination is characteristic of partial
    # visibility (for example, an approaching tool occluding one face).  Keep
    # reporting the raw extent change, but only let it revoke the lease when
    # this RGB-D measurement is internally consistent.  Translation and the
    # separate RGB-D orientation/contact predicates remain independently live.
    extent_measurement_reliable = bool(
        not tracker_available
        or (
            extent_centroid_residual_fraction is not None
            and extent_centroid_residual_fraction
            <= _MAX_RELIABLE_EXTENT_CENTROID_RESIDUAL_FRACTION
        )
    )
    extent_change_exceeded = extent_fraction > extent_limit
    extent_change_invalidating = bool(
        extent_change_exceeded and extent_measurement_reliable
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
        "extent_reference_m": extent_reference_m,
        "extent_centroid_residual_fraction": (
            extent_centroid_residual_fraction
        ),
        "maximum_reliable_extent_centroid_residual_fraction": (
            _MAX_RELIABLE_EXTENT_CENTROID_RESIDUAL_FRACTION
        ),
        "extent_measurement_reliable": extent_measurement_reliable,
        "center_shift_exceeded": center_shift > center_limit,
        "extent_change_exceeded": extent_change_exceeded,
        "extent_change_invalidating": extent_change_invalidating,
        "invalidated": (
            center_shift > center_limit or extent_change_invalidating
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


def swept_carried_aabb_clearance_m(
    *,
    current_interaction_position_m: Sequence[float],
    target_interaction_position_m: Sequence[float],
    carried_geometry: Mapping[str, Any],
    obstacle_geometries: Mapping[str, Mapping[str, Any]],
    sample_count: int = 65,
) -> tuple[float, str | None]:
    """Measure signed clearance for a translated RGB-D carried-object AABB.

    Positive values are separation, zero is touching, and negative values are
    penetration.  An object that begins in support contact may move away from
    that support; a route that maintains or deepens the overlap is unsafe.
    Rotation is handled conservatively by refreshing the observed AABB at each
    motion iteration rather than assuming an object-specific collision model.
    """
    if isinstance(sample_count, bool) or not isinstance(sample_count, int):
        raise WorldEffectGuardedDispatchError("sample_count must be an integer")
    if sample_count < 2:
        raise WorldEffectGuardedDispatchError("sample_count must be at least two")

    def vector3(value: Any, name: str) -> tuple[float, float, float]:
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
            raise WorldEffectGuardedDispatchError(f"{name} must be a three-value array")
        if len(value) != 3:
            raise WorldEffectGuardedDispatchError(f"{name} must have three values")
        result: list[float] = []
        for component in value:
            if (
                isinstance(component, bool)
                or not isinstance(component, (int, float))
                or not math.isfinite(float(component))
            ):
                raise WorldEffectGuardedDispatchError(f"{name} must be finite")
            result.append(float(component))
        return result[0], result[1], result[2]

    current = vector3(
        current_interaction_position_m,
        "current_interaction_position_m",
    )
    target = vector3(
        target_interaction_position_m,
        "target_interaction_position_m",
    )
    carried_lower = vector3(
        carried_geometry.get("visible_aabb_min_base_m"),
        "carried_geometry.visible_aabb_min_base_m",
    )
    carried_upper = vector3(
        carried_geometry.get("visible_aabb_max_base_m"),
        "carried_geometry.visible_aabb_max_base_m",
    )
    if any(lower > upper for lower, upper in zip(carried_lower, carried_upper)):
        raise WorldEffectGuardedDispatchError("carried AABB bounds are inverted")

    def signed_aabb_clearance(
        moving_lower: Sequence[float],
        moving_upper: Sequence[float],
        obstacle_lower: Sequence[float],
        obstacle_upper: Sequence[float],
    ) -> float:
        gaps = [
            max(
                obstacle_lower[index] - moving_upper[index],
                moving_lower[index] - obstacle_upper[index],
            )
            for index in range(3)
        ]
        positive_gaps = [max(gap, 0.0) for gap in gaps]
        if any(gap > 0.0 for gap in gaps):
            return math.sqrt(sum(gap * gap for gap in positive_gaps))
        overlaps = [
            min(moving_upper[index], obstacle_upper[index])
            - max(moving_lower[index], obstacle_lower[index])
            for index in range(3)
        ]
        return -min(overlaps)

    minimum = math.inf
    nearest_id: str | None = None
    for entity_id, geometry in obstacle_geometries.items():
        if not isinstance(entity_id, str) or not entity_id:
            raise WorldEffectGuardedDispatchError(
                "obstacle geometry ids must be non-empty strings"
            )
        if not isinstance(geometry, Mapping):
            raise WorldEffectGuardedDispatchError(
                f"obstacle geometry for {entity_id!r} must be an object"
            )
        obstacle_lower = vector3(
            geometry.get("visible_aabb_min_base_m"),
            f"obstacle_geometries[{entity_id!r}].visible_aabb_min_base_m",
        )
        obstacle_upper = vector3(
            geometry.get("visible_aabb_max_base_m"),
            f"obstacle_geometries[{entity_id!r}].visible_aabb_max_base_m",
        )
        if any(lower > upper for lower, upper in zip(obstacle_lower, obstacle_upper)):
            raise WorldEffectGuardedDispatchError(
                f"obstacle AABB for {entity_id!r} is inverted"
            )
        samples: list[float] = []
        for sample_index in range(sample_count):
            alpha = sample_index / (sample_count - 1)
            translation = tuple(
                alpha * (target[index] - current[index]) for index in range(3)
            )
            moving_lower = tuple(
                carried_lower[index] + translation[index] for index in range(3)
            )
            moving_upper = tuple(
                carried_upper[index] + translation[index] for index in range(3)
            )
            samples.append(
                signed_aabb_clearance(
                    moving_lower,
                    moving_upper,
                    obstacle_lower,
                    obstacle_upper,
                )
            )
        starts_in_contact = samples[0] <= 0.0
        monotonically_egressing = starts_in_contact and all(
            later >= earlier - 1.0e-6
            for earlier, later in zip(samples, samples[1:])
        ) and samples[-1] > samples[0] + 1.0e-6
        entity_clearance = samples[-1] if monotonically_egressing else min(samples)
        if entity_clearance < minimum:
            minimum = entity_clearance
            nearest_id = entity_id
    return minimum, nearest_id


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


def classify_expected_post_release_geometry_change(
    event: DispatchInvalidationEvent,
    *,
    invocation_arguments: Mapping[str, Any],
    actuator_report: Mapping[str, Any],
    target_entity_ids: Sequence[str],
) -> dict[str, Any] | None:
    """Recognize target settling caused by a physically confirmed release.

    Geometry drift remains authority-bearing before dispatch and for every
    operation except a completed disengagement.  After disengagement, movement
    of the released entity is the commanded effect rather than evidence that
    the already-used actuator permit was unsafe.  The exception fails closed:
    it requires opposing retained contact before the command, an observed open
    gripper with no retained contact afterward, and an event for the exact
    released target.
    """
    if event.condition_id != "scene.target_geometry_drift":
        return None
    if invocation_arguments.get("state") != "disengage":
        return None
    if actuator_report.get("requested_state") != "disengage":
        return None
    if actuator_report.get("engaged_after") is not False:
        return None

    entity_id = event.evidence.get("entity_id")
    released_entity_ids = {
        _identifier(item, "target_entity_ids[]") for item in target_entity_ids
    }
    if not isinstance(entity_id, str) or entity_id not in released_entity_ids:
        return None

    state_before = actuator_report.get("state_before")
    state_after = actuator_report.get("state_after")
    if not isinstance(state_before, Mapping) or not isinstance(
        state_after, Mapping
    ):
        return None
    contact_before = state_before.get("current_contact")
    contact_after = state_after.get("current_contact")
    if not isinstance(contact_before, Mapping) or not isinstance(
        contact_after, Mapping
    ):
        return None

    retained_before = contact_before.get("retained_force_n")
    retained_after = contact_after.get("retained_force_n")
    open_fraction = state_after.get("gripper_closed_fraction")
    numeric_values = (retained_before, retained_after, open_fraction)
    if any(
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        for value in numeric_values
    ):
        return None
    if (
        not bool(contact_before.get("touch"))
        or float(retained_before) <= 0.0
        or bool(contact_after.get("touch"))
        or float(retained_after) > 0.0
        or float(open_fraction) > 0.10
    ):
        return None

    return {
        "classification": "expected_post_effect_observation",
        "reason": "released_entity_settled_after_confirmed_disengagement",
        "condition_id": event.condition_id,
        "entity_id": entity_id,
        "pre_release_retained_force_n": float(retained_before),
        "post_release_retained_force_n": float(retained_after),
        "post_release_gripper_closed_fraction": float(open_fraction),
        "lease_revocation_authority": False,
        "execution_authority": False,
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
