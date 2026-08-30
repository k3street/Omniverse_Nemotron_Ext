from dataclasses import replace
from pathlib import Path

import pytest

from scripts.world_effect_runtime_lease import (
    WORLD_EFFECT_RUNTIME_LEASE_SCHEMA_VERSION,
    WorldEffectRuntimeLeaseError,
    issue_world_effect_runtime_lease,
)
from scripts.world_effect_execution_lease import ShadowExecutionLeaseGate
from scripts.world_effect_tool_invocation import (
    ShadowToolInvocationGate,
    build_shadow_tool_invocation_candidates,
)
from tests.test_world_effect_tool_invocation import fixture, proposal


class FakeClock:
    def __init__(self, nanoseconds=10_000_000_000):
        self.nanoseconds = nanoseconds

    def __call__(self):
        return self.nanoseconds


def issued_fixture(*, duration=5.0):
    (
        instance,
        lease_candidates,
        source_lease_decision,
        runtime_observation,
        _,
    ) = fixture()
    lease_decision = ShadowExecutionLeaseGate(lease_candidates).dispatch(
        {
            "schema_version": "world-effect-execution-lease.v1",
            "observation_id": source_lease_decision.observation_id,
            "decision": source_lease_decision.decision,
            "candidate_id": source_lease_decision.candidate_id,
            "provider_instance_id": source_lease_decision.provider_instance_id,
            "operation_candidate_id": source_lease_decision.operation_candidate_id,
            "tool_id": source_lease_decision.tool_id,
            "grounding_entity_ids": list(
                source_lease_decision.grounding_entity_ids
            ),
            "tool_configuration": source_lease_decision.tool_configuration,
            "invalidation_conditions": [
                item.to_dict()
                for item in source_lease_decision.invalidation_conditions
            ],
            "confidence": source_lease_decision.confidence,
            "reason": source_lease_decision.reason,
        }
    )
    invocation_candidates = build_shadow_tool_invocation_candidates(
        instance,
        lease_candidates,
        lease_decision,
        runtime_observation,
    )
    invocation_decision = ShadowToolInvocationGate(
        invocation_candidates
    ).dispatch(proposal(invocation_candidates))
    clock = FakeClock()
    lease = issue_world_effect_runtime_lease(
        lease_candidates=lease_candidates,
        lease_decision=lease_decision,
        invocation_candidates=invocation_candidates,
        invocation_decision=invocation_decision,
        maximum_duration_s=duration,
        clock_ns=clock,
    )
    return (
        lease_candidates,
        lease_decision,
        invocation_candidates,
        invocation_decision,
        clock,
        lease,
    )


def test_issuer_arms_exact_single_use_invocation_without_dispatch_permit():
    _, lease_decision, _, invocation_decision, _, runtime_lease = issued_fixture()
    serialized = runtime_lease.to_dict()

    assert serialized["schema_version"] == WORLD_EFFECT_RUNTIME_LEASE_SCHEMA_VERSION
    assert serialized["state"] == "armed"
    assert serialized["execution_lease_issued"]
    assert serialized["lease_armed"]
    assert serialized["revocable"]
    assert serialized["revocation_conditions_bound"]
    assert serialized["single_use"]
    assert serialized["authority_scope"] == ["invoke_exact_tool_once"]
    assert serialized["shadow_lease_id"] == lease_decision.lease_id
    assert serialized["tool_id"] == invocation_decision.tool_id
    assert serialized["invocation_arguments"] == (
        invocation_decision.invocation_arguments
    )
    assert not serialized["dispatch_permit_issued"]
    assert not serialized["handler_bound"]
    assert not serialized["tool_called"]
    assert not serialized["dispatch_enabled"]
    assert not serialized["execution_authority"]


def test_issuer_binds_every_selected_invalidation_to_runtime_evidence_source():
    _, lease_decision, _, _, _, runtime_lease = issued_fixture()
    bindings = runtime_lease.lease.invalidation_bindings

    assert {item.condition_id for item in bindings} == {
        item.condition_id for item in lease_decision.invalidation_conditions
    }
    assert all(item.evidence_source_id for item in bindings)
    assert runtime_lease.to_dict()["revocation_condition_ids"] == sorted(
        item.condition_id for item in bindings
    )


def test_bound_invalidation_revokes_immediately_and_cannot_be_rearmed():
    *_, runtime_lease = issued_fixture()
    condition_id = runtime_lease.lease.invalidation_bindings[0].condition_id

    runtime_lease.observe_invalidation(
        condition_id, {"observation_id": "fresh:changed"}
    )

    serialized = runtime_lease.to_dict()
    assert serialized["state"] == "revoked"
    assert not serialized["lease_armed"]
    assert serialized["revocation_condition_id"] == condition_id
    assert serialized["revocation_evidence"] == {
        "observation_id": "fresh:changed"
    }
    with pytest.raises(WorldEffectRuntimeLeaseError, match="not active"):
        runtime_lease.revoke(reason="second revocation")


def test_unknown_invalidation_is_rejected_without_revoking_lease():
    *_, runtime_lease = issued_fixture()

    with pytest.raises(WorldEffectRuntimeLeaseError, match="not bound"):
        runtime_lease.observe_invalidation(
            "scene.invented_condition", {"changed": True}
        )

    assert runtime_lease.active


def test_runtime_owned_expiry_disarms_lease():
    *_, clock, runtime_lease = issued_fixture(duration=0.25)
    clock.nanoseconds += 250_000_000

    serialized = runtime_lease.to_dict()
    assert serialized["state"] == "expired"
    assert serialized["revocation_reason"] == "runtime.maximum_duration_elapsed"
    assert not serialized["lease_armed"]
    with pytest.raises(WorldEffectRuntimeLeaseError, match="not active"):
        runtime_lease.assert_active()


def test_issuer_rejects_identity_drift_and_materialized_argument_tampering():
    (
        lease_candidates,
        lease_decision,
        invocation_candidates,
        invocation_decision,
        clock,
        _,
    ) = issued_fixture()

    with pytest.raises(WorldEffectRuntimeLeaseError, match="identities differ"):
        issue_world_effect_runtime_lease(
            lease_candidates=lease_candidates,
            lease_decision=lease_decision,
            invocation_candidates=invocation_candidates,
            invocation_decision=replace(
                invocation_decision, tool_id="different_runtime_tool"
            ),
            maximum_duration_s=5.0,
            clock_ns=clock,
        )

    tampered_arguments = dict(
        invocation_decision.invocation_arguments,
        invented_argument=True,
    )
    with pytest.raises(WorldEffectRuntimeLeaseError, match="schema validation"):
        issue_world_effect_runtime_lease(
            lease_candidates=lease_candidates,
            lease_decision=lease_decision,
            invocation_candidates=invocation_candidates,
            invocation_decision=replace(
                invocation_decision,
                invocation_arguments=tampered_arguments,
            ),
            maximum_duration_s=5.0,
            clock_ns=clock,
        )

    with pytest.raises(WorldEffectRuntimeLeaseError, match="authority revalidation"):
        issue_world_effect_runtime_lease(
            lease_candidates=lease_candidates,
            lease_decision=replace(
                lease_decision,
                tool_configuration={"invented_configuration": True},
            ),
            invocation_candidates=invocation_candidates,
            invocation_decision=invocation_decision,
            maximum_duration_s=5.0,
            clock_ns=clock,
        )


def test_issuer_rejects_unvalidated_decisions_and_invalid_duration():
    (
        lease_candidates,
        lease_decision,
        invocation_candidates,
        invocation_decision,
        clock,
        _,
    ) = issued_fixture()

    with pytest.raises(WorldEffectRuntimeLeaseError, match="propose_invocation"):
        issue_world_effect_runtime_lease(
            lease_candidates=lease_candidates,
            lease_decision=lease_decision,
            invocation_candidates=invocation_candidates,
            invocation_decision=replace(
                invocation_decision,
                decision="observe_again",
                candidate_id=None,
            ),
            maximum_duration_s=5.0,
            clock_ns=clock,
        )
    with pytest.raises(WorldEffectRuntimeLeaseError, match="greater than zero"):
        issue_world_effect_runtime_lease(
            lease_candidates=lease_candidates,
            lease_decision=lease_decision,
            invocation_candidates=invocation_candidates,
            invocation_decision=invocation_decision,
            maximum_duration_s=0.0,
            clock_ns=clock,
        )


def test_runner_issues_after_invocation_gate_and_before_shadow_boundary():
    source = (
        Path(__file__).parents[1]
        / "scripts"
        / "run_gemini_robotics_robolab.py"
    ).read_text()
    invocation_gate = source.index("ShadowToolInvocationGate(")
    issuance = source.index(
        "issue_world_effect_runtime_lease(", invocation_gate
    )
    issuance_trace = source.index(
        '"world_effect_runtime_lease"', issuance
    )
    hard_boundary = source.index("if args_cli.shadow_plan_only:", issuance_trace)

    assert invocation_gate < issuance < issuance_trace < hard_boundary
    block = source[issuance:hard_boundary]
    assert '"execution_lease_issued": True' in block
    assert '"lease_armed": (' in block
    assert '"dispatch_permit_issued": False' in block
    assert '"handler_bound": False' in block
    assert '"tool_called": False' in block
    assert '"dispatch_enabled": False' in block
    assert '"execution_authority": False' in block
    assert "_execute_adaptive_stage(" not in block
    assert "actuator_transition_handler(" not in block
