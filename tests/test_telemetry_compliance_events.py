"""
Tests for CRM-D1 compliance telemetry event constants.

Verifies:
- All 8 EVENT_* constants exist and are str-typed (§8).
- Constants are registered in ALL_EVENT_TYPES (no dispatch gap).
- New constants don't collide with pre-existing EVENT_* names.
- emit() accepts each new event without raising (smoke-test).
- Round-trip: constant value → look up in ALL_EVENT_TYPES → matches constant.
"""
from __future__ import annotations

import pytest

import service.isaac_assist_service.multimodal.telemetry as tel

pytestmark = pytest.mark.l0  # all tests here are pure-Python, no external deps


# ---------------------------------------------------------------------------
# The 8 new constants required by §8
# ---------------------------------------------------------------------------

_COMPLIANCE_CONSTANTS = [
    ("EVENT_COMPLIANCE_INSTALLED",     "compliance_installed"),
    ("EVENT_COMPLIANCE_PARAMS_UPDATED","compliance_params_updated"),
    ("EVENT_COMPLIANCE_RELEASED",      "compliance_released"),
    ("EVENT_FT_SENSOR_ATTACHED",       "ft_sensor_attached"),
    ("EVENT_CONTACT_PHASE_ENTERED",    "contact_phase_entered"),
    ("EVENT_CONTACT_PHASE_EXITED",     "contact_phase_exited"),
    ("EVENT_INSERTION_SUCCEEDED",      "insertion_succeeded"),
    ("EVENT_INSERTION_FAILED",         "insertion_failed"),
]


# ---------------------------------------------------------------------------
# Gate 1 — all 8 constants exist and carry the correct str value
# ---------------------------------------------------------------------------

class TestComplianceConstantsExist:
    @pytest.mark.parametrize("attr_name,expected_value", _COMPLIANCE_CONSTANTS)
    def test_constant_exists_and_is_str(self, attr_name: str, expected_value: str) -> None:
        """Constant must exist on the module and be a str."""
        assert hasattr(tel, attr_name), (
            f"telemetry module is missing {attr_name!r}"
        )
        value = getattr(tel, attr_name)
        assert isinstance(value, str), (
            f"{attr_name} must be str, got {type(value).__name__}"
        )
        assert value == expected_value, (
            f"{attr_name} = {value!r}, expected {expected_value!r}"
        )


# ---------------------------------------------------------------------------
# Gate 2 — all 8 constants are registered in ALL_EVENT_TYPES
# ---------------------------------------------------------------------------

class TestConstantsInRegistry:
    @pytest.mark.parametrize("attr_name,expected_value", _COMPLIANCE_CONSTANTS)
    def test_constant_in_all_event_types(
        self, attr_name: str, expected_value: str
    ) -> None:
        """Each new event must appear in ALL_EVENT_TYPES for emit() acceptance."""
        assert expected_value in tel.ALL_EVENT_TYPES, (
            f"{expected_value!r} (from {attr_name}) not found in ALL_EVENT_TYPES"
        )


# ---------------------------------------------------------------------------
# Gate 3 — no collision with pre-existing EVENT_* values
# ---------------------------------------------------------------------------

_PRE_EXISTING_EVENTS = {
    "modality_invoked",
    "intent_extracted",
    "retrieval_completed",
    "ratify_completed",
    "rebind_role",
    "build_started",
    "build_progress",
    "build_completed",
    "verify_check_run",
    "canvas_proposed_resolved",
    "canonical_match_shown",
    "canonical_match_resolved",
    "user_correction",
    "supervisor_started",
    "supervisor_stopped",
    "supervisor_drift_classification",
    "supervisor_drift_detected",
    "supervisor_restart_decision",
    "supervisor_restart_started",
    "supervisor_restart_completed",
    "supervisor_restart_failed",
    "supervisor_soft_reset",
    "supervisor_memory_growth",
    "supervisor_runner_exception",
    "supervisor_abort",
}


class TestNoCollisions:
    @pytest.mark.parametrize("attr_name,expected_value", _COMPLIANCE_CONSTANTS)
    def test_no_collision_with_existing(
        self, attr_name: str, expected_value: str
    ) -> None:
        """New constant values must not shadow pre-existing event names."""
        assert expected_value not in _PRE_EXISTING_EVENTS, (
            f"{expected_value!r} collides with a pre-existing event name"
        )

    def test_all_event_types_no_duplicates(self) -> None:
        """ALL_EVENT_TYPES must not contain duplicate strings."""
        seen: dict[str, int] = {}
        for idx, ev in enumerate(tel.ALL_EVENT_TYPES):
            assert ev not in seen, (
                f"Duplicate event type {ev!r} at indices {seen[ev]} and {idx}"
            )
            seen[ev] = idx


# ---------------------------------------------------------------------------
# Gate 4 — emit() smoke-test: each new event accepted without raising
# ---------------------------------------------------------------------------

class _MockStore:
    """Minimal MultimodalStore stand-in that records calls."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict]] = []

    def append_event(
        self,
        session_id: str,
        event_type: str,
        payload: dict,
    ) -> int:
        self.calls.append((session_id, event_type, payload))
        return len(self.calls)


class TestEmitSmoke:
    @pytest.mark.parametrize("attr_name,expected_value", _COMPLIANCE_CONSTANTS)
    def test_emit_does_not_raise(
        self, attr_name: str, expected_value: str
    ) -> None:
        """Calling emit() with each new event type must not raise."""
        store = _MockStore()
        result = tel.emit(store, "sess-crm-d1", expected_value, detail="smoke")
        # emit returns the event_id (int) on success
        assert result is not None and result > 0, (
            f"emit() returned {result!r} for {expected_value!r}; expected int > 0"
        )

    @pytest.mark.parametrize("attr_name,expected_value", _COMPLIANCE_CONSTANTS)
    def test_emit_stores_correct_event_type(
        self, attr_name: str, expected_value: str
    ) -> None:
        """Emitted event must be stored with the correct event_type string."""
        store = _MockStore()
        tel.emit(store, "sess-crm-d1", expected_value)
        assert len(store.calls) == 1
        _sid, stored_type, _payload = store.calls[0]
        assert stored_type == expected_value


# ---------------------------------------------------------------------------
# Gate 5 — round-trip: name → constant value → found in ALL_EVENT_TYPES → name
# ---------------------------------------------------------------------------

class TestRoundTrip:
    def test_round_trip_all_compliance_events(self) -> None:
        """All 8 constant values must survive a value → ALL_EVENT_TYPES round-trip."""
        for attr_name, expected_value in _COMPLIANCE_CONSTANTS:
            constant_value: str = getattr(tel, attr_name)
            # constant_value is in ALL_EVENT_TYPES
            assert constant_value in tel.ALL_EVENT_TYPES
            # The matching entry equals the expected string
            matched = next(
                (ev for ev in tel.ALL_EVENT_TYPES if ev == constant_value), None
            )
            assert matched == expected_value, (
                f"Round-trip failed for {attr_name}: "
                f"found {matched!r}, expected {expected_value!r}"
            )

    def test_compliance_event_count_in_registry(self) -> None:
        """Exactly 8 compliance event values must be present in ALL_EVENT_TYPES."""
        compliance_values = {val for _, val in _COMPLIANCE_CONSTANTS}
        found = [ev for ev in tel.ALL_EVENT_TYPES if ev in compliance_values]
        assert len(found) == 8, (
            f"Expected 8 compliance events in ALL_EVENT_TYPES, found {len(found)}: {found}"
        )
