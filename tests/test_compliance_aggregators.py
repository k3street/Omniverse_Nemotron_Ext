"""
Tests for CRM-D2 compliance telemetry aggregators.

Covers:
- compliance_usage_breakdown: mode counting, params_updates, releases, active_now tracking
- contact_phase_success_rate: phase counts, insertion outcomes, success_rate math
"""
from __future__ import annotations

import pytest

from scripts.qa.analyze_multimodal_usage import (
    compliance_usage_breakdown,
    contact_phase_success_rate,
)

pytestmark = pytest.mark.l0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _evt(event_type: str, **payload) -> dict:
    """Build a minimal telemetry event dict."""
    return {
        "event_type": event_type,
        "session_id": "sess-test",
        "event_id": 0,
        "payload": payload,
    }


# ---------------------------------------------------------------------------
# compliance_usage_breakdown
# ---------------------------------------------------------------------------

class TestComplianceUsageBreakdownEmpty:
    def test_empty_events_zero_counts(self) -> None:
        """Empty event list → all counts 0, active_now empty, by_mode empty."""
        result = compliance_usage_breakdown([])
        assert result["by_mode"] == {}
        assert result["params_updates"] == 0
        assert result["releases"] == 0
        assert result["active_now"] == {}


class TestComplianceUsageBreakdownInstalled:
    def test_three_admittance_installs(self) -> None:
        """3 compliance_installed with mode=admittance → by_mode['admittance'] == 3."""
        events = [
            _evt("compliance_installed", mode="admittance", robot_path=f"/robot/{i}")
            for i in range(3)
        ]
        result = compliance_usage_breakdown(events)
        assert result["by_mode"].get("admittance") == 3

    def test_mixed_modes_counted_separately(self) -> None:
        """admittance and impedance events must be tracked independently."""
        events = [
            _evt("compliance_installed", mode="admittance", robot_path="/robot/0"),
            _evt("compliance_installed", mode="admittance", robot_path="/robot/1"),
            _evt("compliance_installed", mode="impedance", robot_path="/robot/2"),
        ]
        result = compliance_usage_breakdown(events)
        assert result["by_mode"]["admittance"] == 2
        assert result["by_mode"]["impedance"] == 1

    def test_install_then_release_active_now_empty(self) -> None:
        """Install followed by release on the same robot → active_now is empty."""
        events = [
            _evt("compliance_installed", mode="admittance", robot_path="/World/Franka"),
            _evt("compliance_released", robot_path="/World/Franka"),
        ]
        result = compliance_usage_breakdown(events)
        assert result["active_now"] == {}
        assert result["releases"] == 1

    def test_install_without_release_active_now_has_robot(self) -> None:
        """Install with no matching release → robot remains in active_now."""
        events = [
            _evt("compliance_installed", mode="impedance", robot_path="/World/UR10"),
        ]
        result = compliance_usage_breakdown(events)
        assert result["active_now"] == {"/World/UR10": "impedance"}

    def test_params_updates_counted(self) -> None:
        """compliance_params_updated events increment params_updates."""
        events = [
            _evt("compliance_params_updated", robot_path="/World/Franka", K_xyz=[100, 100, 100]),
            _evt("compliance_params_updated", robot_path="/World/Franka", K_xyz=[200, 200, 200]),
        ]
        result = compliance_usage_breakdown(events)
        assert result["params_updates"] == 2

    def test_irrelevant_events_ignored(self) -> None:
        """Non-compliance events must not affect the breakdown."""
        events = [
            _evt("modality_invoked", modality="voice", ms=120.0),
            _evt("build_progress", tool="create_prim", status="ok", ms=50.0),
        ]
        result = compliance_usage_breakdown(events)
        assert result["by_mode"] == {}
        assert result["params_updates"] == 0
        assert result["releases"] == 0
        assert result["active_now"] == {}


# ---------------------------------------------------------------------------
# contact_phase_success_rate
# ---------------------------------------------------------------------------

class TestContactPhaseSuccessRateEmpty:
    def test_empty_events_all_zero(self) -> None:
        """Empty event list → all counts 0, success_rate 0.0."""
        result = contact_phase_success_rate([])
        assert result["phases_entered"] == 0
        assert result["phases_exited"] == 0
        assert result["insertion_succeeded"] == 0
        assert result["insertion_failed"] == 0
        assert result["success_rate"] == 0.0


class TestContactPhaseSuccessRateInsertions:
    def test_two_succeeded_one_failed(self) -> None:
        """2 succeeded + 1 failed → success_rate ≈ 0.667."""
        events = [
            _evt("insertion_succeeded"),
            _evt("insertion_succeeded"),
            _evt("insertion_failed"),
        ]
        result = contact_phase_success_rate(events)
        assert result["insertion_succeeded"] == 2
        assert result["insertion_failed"] == 1
        assert abs(result["success_rate"] - 2 / 3) < 0.001

    def test_all_succeeded_rate_one(self) -> None:
        """All succeeded and no failures → success_rate 1.0."""
        events = [_evt("insertion_succeeded")] * 5
        result = contact_phase_success_rate(events)
        assert result["success_rate"] == 1.0

    def test_exits_without_inserts_rate_zero(self) -> None:
        """Phase exits with no insertion events → success_rate 0.0 (no denominator)."""
        events = [
            _evt("contact_phase_entered"),
            _evt("contact_phase_exited"),
            _evt("contact_phase_exited"),
        ]
        result = contact_phase_success_rate(events)
        assert result["phases_entered"] == 1
        assert result["phases_exited"] == 2
        assert result["insertion_succeeded"] == 0
        assert result["insertion_failed"] == 0
        assert result["success_rate"] == 0.0

    def test_phase_counts_tracked(self) -> None:
        """contact_phase_entered and contact_phase_exited are counted separately."""
        events = [
            _evt("contact_phase_entered"),
            _evt("contact_phase_entered"),
            _evt("contact_phase_exited"),
        ]
        result = contact_phase_success_rate(events)
        assert result["phases_entered"] == 2
        assert result["phases_exited"] == 1

    def test_mixed_events_combined(self) -> None:
        """All four CRM event types present together — counts are independent."""
        events = [
            _evt("contact_phase_entered"),
            _evt("insertion_succeeded"),
            _evt("contact_phase_exited"),
            _evt("insertion_failed"),
            _evt("insertion_succeeded"),
        ]
        result = contact_phase_success_rate(events)
        assert result["phases_entered"] == 1
        assert result["phases_exited"] == 1
        assert result["insertion_succeeded"] == 2
        assert result["insertion_failed"] == 1
        assert abs(result["success_rate"] - 2 / 3) < 0.001
