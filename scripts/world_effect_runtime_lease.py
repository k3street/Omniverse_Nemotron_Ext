"""Issue one revocable runtime lease for an exact validated tool invocation.

The issuer is deterministic: a reasoning model cannot choose lease identity,
duration, authority scope, or revocation wiring.  An issued lease is armed and
can be revoked or expire, but this module exposes no handler binding, executor
lookup, or dispatch method.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import re
import time
from typing import Any, Callable, Mapping

try:
    from .world_effect_execution_lease import (
        ShadowExecutionLeaseCandidateSet,
        ShadowExecutionLeaseDecision,
        revalidate_shadow_execution_lease_decision,
    )
    from .world_effect_tool_invocation import (
        ShadowToolInvocationCandidateSet,
        ShadowToolInvocationDecision,
        validate_materialized_invocation_arguments,
    )
except ImportError:  # Script execution adds this directory directly to sys.path.
    from world_effect_execution_lease import (  # type: ignore[no-redef]
        ShadowExecutionLeaseCandidateSet,
        ShadowExecutionLeaseDecision,
        revalidate_shadow_execution_lease_decision,
    )
    from world_effect_tool_invocation import (  # type: ignore[no-redef]
        ShadowToolInvocationCandidateSet,
        ShadowToolInvocationDecision,
        validate_materialized_invocation_arguments,
    )


WORLD_EFFECT_RUNTIME_LEASE_SCHEMA_VERSION = "world-effect-runtime-lease.v1"
_IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9_.:/-]{0,127}$")


class WorldEffectRuntimeLeaseError(ValueError):
    """Raised when runtime lease issuance exceeds the validated handoff."""


def _identifier(value: Any, path: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise WorldEffectRuntimeLeaseError(f"{path} has an invalid format")
    return value


def _text(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WorldEffectRuntimeLeaseError(f"{path} must be non-empty text")
    return value.strip()


def _json_copy(value: Any, path: str, *, depth: int = 0) -> Any:
    if depth > 12:
        raise WorldEffectRuntimeLeaseError(f"{path} exceeds maximum nesting depth")
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise WorldEffectRuntimeLeaseError(f"{path} contains a non-finite number")
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
                raise WorldEffectRuntimeLeaseError(
                    f"{path} keys must be non-empty strings"
                )
            result[key] = _json_copy(item, f"{path}.{key}", depth=depth + 1)
        return result
    raise WorldEffectRuntimeLeaseError(f"{path} must be JSON-compatible")


def _digest(value: Any) -> str:
    encoded = json.dumps(
        _json_copy(value, "digest_value"),
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


def _duration_seconds(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise WorldEffectRuntimeLeaseError("maximum_duration_s must be a number")
    duration = float(value)
    if not math.isfinite(duration) or duration <= 0.0:
        raise WorldEffectRuntimeLeaseError(
            "maximum_duration_s must be finite and greater than zero"
        )
    return duration


@dataclass(frozen=True)
class RuntimeLeaseInvalidationBinding:
    condition_id: str
    evidence_source_id: str
    target_entity_ids: tuple[str, ...]
    parameters: Mapping[str, Any]

    def __post_init__(self) -> None:
        _identifier(self.condition_id, "condition_id")
        _identifier(self.evidence_source_id, "evidence_source_id")
        for index, entity_id in enumerate(self.target_entity_ids):
            _identifier(entity_id, f"target_entity_ids[{index}]")
        if len(set(self.target_entity_ids)) != len(self.target_entity_ids):
            raise WorldEffectRuntimeLeaseError(
                "invalidation target_entity_ids must be unique"
            )
        _json_copy(self.parameters, "parameters")

    def to_dict(self) -> dict[str, Any]:
        return {
            "condition_id": self.condition_id,
            "evidence_source_id": self.evidence_source_id,
            "target_entity_ids": list(self.target_entity_ids),
            "parameters": _json_copy(self.parameters, "parameters"),
        }


@dataclass(frozen=True)
class IssuedWorldEffectRuntimeLease:
    issued_lease_id: str
    shadow_lease_id: str
    lease_observation_id: str
    invocation_observation_id: str
    runtime_observation_digest: str
    inventory_digest: str
    provider_instance_id: str
    operation_candidate_id: str
    invocation_candidate_id: str
    tool_id: str
    invocation_digest: str
    invocation_arguments: Mapping[str, Any]
    tool_configuration: Mapping[str, Any]
    invalidation_digest: str
    invalidation_bindings: tuple[RuntimeLeaseInvalidationBinding, ...]
    issued_at_monotonic_ns: int
    expires_at_monotonic_ns: int
    maximum_duration_s: float

    def __post_init__(self) -> None:
        for path, value in (
            ("issued_lease_id", self.issued_lease_id),
            ("shadow_lease_id", self.shadow_lease_id),
            ("lease_observation_id", self.lease_observation_id),
            ("invocation_observation_id", self.invocation_observation_id),
            ("runtime_observation_digest", self.runtime_observation_digest),
            ("inventory_digest", self.inventory_digest),
            ("provider_instance_id", self.provider_instance_id),
            ("operation_candidate_id", self.operation_candidate_id),
            ("invocation_candidate_id", self.invocation_candidate_id),
            ("tool_id", self.tool_id),
            ("invocation_digest", self.invocation_digest),
            ("invalidation_digest", self.invalidation_digest),
        ):
            _identifier(value, path)
        _json_copy(self.invocation_arguments, "invocation_arguments")
        _json_copy(self.tool_configuration, "tool_configuration")
        if not self.invalidation_bindings:
            raise WorldEffectRuntimeLeaseError(
                "issued lease requires at least one invalidation binding"
            )
        if isinstance(self.issued_at_monotonic_ns, bool) or not isinstance(
            self.issued_at_monotonic_ns, int
        ):
            raise WorldEffectRuntimeLeaseError(
                "issued_at_monotonic_ns must be an integer"
            )
        if self.expires_at_monotonic_ns <= self.issued_at_monotonic_ns:
            raise WorldEffectRuntimeLeaseError(
                "lease expiry must be later than issuance"
            )
        _duration_seconds(self.maximum_duration_s)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": WORLD_EFFECT_RUNTIME_LEASE_SCHEMA_VERSION,
            "issued_lease_id": self.issued_lease_id,
            "shadow_lease_id": self.shadow_lease_id,
            "lease_observation_id": self.lease_observation_id,
            "invocation_observation_id": self.invocation_observation_id,
            "runtime_observation_digest": self.runtime_observation_digest,
            "inventory_digest": self.inventory_digest,
            "provider_instance_id": self.provider_instance_id,
            "operation_candidate_id": self.operation_candidate_id,
            "invocation_candidate_id": self.invocation_candidate_id,
            "tool_id": self.tool_id,
            "invocation_digest": self.invocation_digest,
            "invocation_arguments": _json_copy(
                self.invocation_arguments, "invocation_arguments"
            ),
            "tool_configuration": _json_copy(
                self.tool_configuration, "tool_configuration"
            ),
            "invalidation_digest": self.invalidation_digest,
            "invalidation_bindings": [
                item.to_dict() for item in self.invalidation_bindings
            ],
            "issued_at_monotonic_ns": self.issued_at_monotonic_ns,
            "expires_at_monotonic_ns": self.expires_at_monotonic_ns,
            "maximum_duration_s": self.maximum_duration_s,
            "single_use": True,
            "authority_scope": ["invoke_exact_tool_once"],
        }


class RevocableWorldEffectRuntimeLease:
    """Mutable revocation state around an immutable exact-invocation lease."""

    def __init__(
        self,
        lease: IssuedWorldEffectRuntimeLease,
        *,
        clock_ns: Callable[[], int] = time.monotonic_ns,
    ) -> None:
        self.lease = lease
        self._clock_ns = clock_ns
        self._state = "armed"
        self._revocation_reason: str | None = None
        self._revocation_condition_id: str | None = None
        self._revocation_evidence: Mapping[str, Any] = {}
        self._revoked_at_monotonic_ns: int | None = None
        self._consumption_reason: str | None = None
        self._consumed_at_monotonic_ns: int | None = None
        self._condition_ids = {
            item.condition_id for item in lease.invalidation_bindings
        }

    @property
    def state(self) -> str:
        self._refresh_expiry()
        return self._state

    @property
    def active(self) -> bool:
        return self.state == "armed"

    def _refresh_expiry(self) -> None:
        if (
            self._state == "armed"
            and self._clock_ns() >= self.lease.expires_at_monotonic_ns
        ):
            self._state = "expired"
            self._revocation_reason = "runtime.maximum_duration_elapsed"
            self._revoked_at_monotonic_ns = self.lease.expires_at_monotonic_ns

    def assert_active(self) -> None:
        if not self.active:
            raise WorldEffectRuntimeLeaseError(
                f"runtime lease is not active: {self._state}"
            )

    def revoke(
        self,
        *,
        reason: str,
        condition_id: str | None = None,
        evidence: Mapping[str, Any] | None = None,
    ) -> None:
        self.assert_active()
        reason = _text(reason, "revocation reason")
        if condition_id is not None:
            condition_id = _identifier(condition_id, "condition_id")
            if condition_id not in self._condition_ids:
                raise WorldEffectRuntimeLeaseError(
                    "revocation condition was not bound to this lease"
                )
        evidence_copy = _json_copy(evidence or {}, "revocation_evidence")
        self._state = "revoked"
        self._revocation_reason = reason
        self._revocation_condition_id = condition_id
        self._revocation_evidence = evidence_copy
        self._revoked_at_monotonic_ns = self._clock_ns()

    def observe_invalidation(
        self, condition_id: str, evidence: Mapping[str, Any]
    ) -> None:
        condition_id = _identifier(condition_id, "condition_id")
        self.revoke(
            reason=f"invalidation:{condition_id}",
            condition_id=condition_id,
            evidence=evidence,
        )

    def consume(
        self,
        *,
        reason: str = "dispatch.single_use_consumed",
    ) -> None:
        """Permanently consume the lease after its one permitted invocation."""
        self.assert_active()
        self._state = "consumed"
        self._consumption_reason = _text(reason, "consumption reason")
        self._consumed_at_monotonic_ns = self._clock_ns()

    def to_dict(self) -> dict[str, Any]:
        state = self.state
        return {
            **self.lease.to_dict(),
            "state": state,
            "execution_lease_issued": True,
            "lease_armed": state == "armed",
            "revocable": True,
            "revocation_conditions_bound": True,
            "revocation_condition_ids": sorted(self._condition_ids),
            "revocation_reason": self._revocation_reason,
            "revocation_condition_id": self._revocation_condition_id,
            "revocation_evidence": _json_copy(
                self._revocation_evidence, "revocation_evidence"
            ),
            "revoked_at_monotonic_ns": self._revoked_at_monotonic_ns,
            "consumption_reason": self._consumption_reason,
            "consumed_at_monotonic_ns": self._consumed_at_monotonic_ns,
            "dispatch_permit_issued": False,
            "handler_bound": False,
            "tool_called": False,
            "dispatch_enabled": False,
            "motion_authority": False,
            "execution_authority": False,
        }


def issue_world_effect_runtime_lease(
    *,
    lease_candidates: ShadowExecutionLeaseCandidateSet,
    lease_decision: ShadowExecutionLeaseDecision,
    invocation_candidates: ShadowToolInvocationCandidateSet,
    invocation_decision: ShadowToolInvocationDecision,
    maximum_duration_s: float,
    clock_ns: Callable[[], int] = time.monotonic_ns,
) -> RevocableWorldEffectRuntimeLease:
    """Issue a runtime-owned lease for exactly one validated invocation."""
    duration = _duration_seconds(maximum_duration_s)
    if lease_decision.decision != "propose_lease" or not lease_decision.lease_id:
        raise WorldEffectRuntimeLeaseError(
            "runtime issuance requires a validated propose_lease decision"
        )
    if (
        invocation_decision.decision != "propose_invocation"
        or not invocation_decision.candidate_id
    ):
        raise WorldEffectRuntimeLeaseError(
            "runtime issuance requires a validated propose_invocation decision"
        )
    if lease_decision.observation_id != lease_candidates.observation_id:
        raise WorldEffectRuntimeLeaseError("stale execution-lease observation")
    if invocation_decision.observation_id != invocation_candidates.observation_id:
        raise WorldEffectRuntimeLeaseError("stale invocation observation")
    if invocation_candidates.lease_id != lease_decision.lease_id:
        raise WorldEffectRuntimeLeaseError(
            "invocation candidate set does not bind the validated lease"
        )
    try:
        lease_decision = revalidate_shadow_execution_lease_decision(
            lease_candidates, lease_decision
        )
    except Exception as exc:
        raise WorldEffectRuntimeLeaseError(
            f"execution lease failed authority revalidation: {exc}"
        ) from exc

    lease_candidate = next(
        (
            item
            for item in lease_candidates.candidates
            if item.candidate_id == lease_decision.candidate_id
        ),
        None,
    )
    invocation_candidate = next(
        (
            item
            for item in invocation_candidates.candidates
            if item.candidate_id == invocation_decision.candidate_id
        ),
        None,
    )
    if lease_candidate is None or invocation_candidate is None:
        raise WorldEffectRuntimeLeaseError(
            "runtime issuance candidate was not freshly advertised"
        )
    exact_ids = (
        invocation_decision.lease_id == lease_decision.lease_id
        and invocation_decision.tool_id == lease_decision.tool_id
        and invocation_candidate.lease_id == lease_decision.lease_id
        and invocation_candidate.lease_observation_id == lease_decision.observation_id
        and invocation_candidate.provider_instance_id
        == lease_decision.provider_instance_id
        and invocation_candidate.operation_candidate_id
        == lease_decision.operation_candidate_id
        and invocation_candidate.tool_id == lease_decision.tool_id
        and lease_candidate.provider_instance_id
        == lease_decision.provider_instance_id
        and lease_candidate.operation_candidate_id
        == lease_decision.operation_candidate_id
        and lease_candidate.tool_id == lease_decision.tool_id
    )
    if not exact_ids:
        raise WorldEffectRuntimeLeaseError(
            "lease, provider, operation, tool, and invocation identities differ"
        )
    selected_condition_ids = {
        item.condition_id for item in lease_decision.invalidation_conditions
    }
    if set(invocation_decision.acknowledged_invalidation_condition_ids) != (
        selected_condition_ids
    ):
        raise WorldEffectRuntimeLeaseError(
            "invocation does not preserve the exact lease invalidation set"
        )
    assessment = invocation_decision.grounding_assessment
    if assessment.get("lease_invalidations_preserved") is not True:
        raise WorldEffectRuntimeLeaseError(
            "invocation grounding did not attest lease invalidation preservation"
        )
    try:
        invocation_arguments = validate_materialized_invocation_arguments(
            invocation_candidate, invocation_decision.invocation_arguments
        )
    except Exception as exc:
        raise WorldEffectRuntimeLeaseError(
            f"materialized invocation failed runtime schema validation: {exc}"
        ) from exc

    invalidation_specs = {
        item.condition_id: item for item in lease_candidate.invalidation_candidates
    }
    bindings: list[RuntimeLeaseInvalidationBinding] = []
    for selection in lease_decision.invalidation_conditions:
        spec = invalidation_specs.get(selection.condition_id)
        if spec is None:
            raise WorldEffectRuntimeLeaseError(
                "selected invalidation is absent from the fresh lease candidate"
            )
        bindings.append(
            RuntimeLeaseInvalidationBinding(
                condition_id=selection.condition_id,
                evidence_source_id=spec.evidence_source_id,
                target_entity_ids=selection.target_entity_ids,
                parameters=selection.parameters,
            )
        )
    bindings.sort(key=lambda item: item.condition_id)
    invalidation_digest = "runtime-invalidations:" + _digest(
        [item.to_dict() for item in bindings]
    )
    invocation_digest = "runtime-invocation:" + _digest(
        {
            "candidate_id": invocation_decision.candidate_id,
            "arguments": invocation_arguments,
            "position_anchor_id": invocation_decision.position_anchor_id,
            "interaction_offset_from_anchor_m": list(
                invocation_decision.interaction_offset_from_anchor_m
            ),
            "orientation_alignment_id": (
                invocation_decision.orientation_alignment_id
            ),
            "grounding_assessment": assessment,
        }
    )
    issued_at_ns = clock_ns()
    if isinstance(issued_at_ns, bool) or not isinstance(issued_at_ns, int):
        raise WorldEffectRuntimeLeaseError("clock_ns must return an integer")
    duration_ns = max(1, int(round(duration * 1_000_000_000)))
    expires_at_ns = issued_at_ns + duration_ns
    issuance_seed = {
        "shadow_lease_id": lease_decision.lease_id,
        "lease_observation_id": lease_decision.observation_id,
        "invocation_observation_id": invocation_decision.observation_id,
        "runtime_observation_digest": invocation_candidates.runtime_observation_digest,
        "inventory_digest": lease_candidates.inventory_digest,
        "invocation_digest": invocation_digest,
        "invalidation_digest": invalidation_digest,
        "issued_at_monotonic_ns": issued_at_ns,
        "expires_at_monotonic_ns": expires_at_ns,
    }
    issued = IssuedWorldEffectRuntimeLease(
        issued_lease_id="runtime-execution-lease:" + _digest(issuance_seed),
        shadow_lease_id=lease_decision.lease_id,
        lease_observation_id=lease_decision.observation_id,
        invocation_observation_id=invocation_decision.observation_id,
        runtime_observation_digest=invocation_candidates.runtime_observation_digest,
        inventory_digest=lease_candidates.inventory_digest,
        provider_instance_id=_identifier(
            lease_decision.provider_instance_id, "provider_instance_id"
        ),
        operation_candidate_id=_identifier(
            lease_decision.operation_candidate_id, "operation_candidate_id"
        ),
        invocation_candidate_id=invocation_decision.candidate_id,
        tool_id=_identifier(lease_decision.tool_id, "tool_id"),
        invocation_digest=invocation_digest,
        invocation_arguments=invocation_arguments,
        tool_configuration=lease_decision.tool_configuration,
        invalidation_digest=invalidation_digest,
        invalidation_bindings=tuple(bindings),
        issued_at_monotonic_ns=issued_at_ns,
        expires_at_monotonic_ns=expires_at_ns,
        maximum_duration_s=duration,
    )
    return RevocableWorldEffectRuntimeLease(issued, clock_ns=clock_ns)
