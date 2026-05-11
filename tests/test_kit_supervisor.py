"""Tests for scripts/qa/kit_supervisor.py.

L0: pure-function tests for DriftDetector + MemoryMonitor classifiers.
L1: light integration of supervisor state transitions with stubbed RPC.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import pytest

pytestmark = pytest.mark.l0

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.qa.kit_supervisor import (
    DriftDetector,
    DriftSignal,
    HealthProbe,
    KitSupervisor,
    MemoryMonitor,
    RestartManager,
    SupervisorConfig,
    SupervisorState,
)


# ── DriftDetector ──────────────────────────────────────────────────────────


def test_drift_ok_for_normal_result():
    d = DriftDetector()
    result = {"per_run": [{"cube_final": [0.5, -0.4, 0.78], "speed": 0.0}]}
    s = d.classify("CP-01", result, elapsed_s=70.0)
    assert s.level == "ok"


def test_drift_detected_on_absurd_position():
    d = DriftDetector()
    result = {"per_run": [{"cube_final": [1e8, 1e7, 1e9], "speed": 1e8}]}
    s = d.classify("CP-01", result, elapsed_s=70.0)
    assert s.level == "drift"
    assert "position_absurd" in s.reason


def test_drift_detected_on_speed_explosion():
    d = DriftDetector()
    result = {"per_run": [{"cube_final": [0.0, 0.0, 0.5], "speed": 1e7}]}
    s = d.classify("CP-01", result, elapsed_s=70.0)
    assert s.level == "drift"
    assert "speed_absurd" in s.reason


def test_drift_warn_on_elapsed_above_baseline():
    d = DriftDetector(elapsed_warn_x=1.5, elapsed_drift_x=2.5)
    d.record_elapsed("CP-01", 70.0)  # baseline
    result = {"per_run": [{"cube_final": [0.0, 0.0, 0.5], "speed": 0.0}]}
    s = d.classify("CP-01", result, elapsed_s=130.0)  # 1.86x → warn (>1.5 <2.5)
    assert s.level == "warn"


def test_drift_drift_on_elapsed_far_above():
    d = DriftDetector(elapsed_warn_x=1.5, elapsed_drift_x=2.5)
    d.record_elapsed("CP-01", 70.0)
    result = {"per_run": [{"cube_final": [0.0, 0.0, 0.5], "speed": 0.0}]}
    s = d.classify("CP-01", result, elapsed_s=200.0)  # 2.86x → drift
    assert s.level == "drift"
    assert "elapsed_far_above" in s.reason


def test_drift_no_baseline_no_elapsed_signal():
    """Without a baseline, elapsed alone doesn't trigger drift."""
    d = DriftDetector()
    result = {"per_run": [{"cube_final": [0.0, 0.0, 0.5], "speed": 0.0}]}
    s = d.classify("CP-01", result, elapsed_s=500.0)
    assert s.level == "ok"


def test_drift_records_ema_baseline():
    """Baseline uses EMA smoothing."""
    d = DriftDetector()
    d.record_elapsed("CP-01", 100.0)
    d.record_elapsed("CP-01", 50.0)
    # EMA: 100 * 0.7 + 50 * 0.3 = 85
    assert abs(d._elapsed_baseline["CP-01"] - 85.0) < 1e-6


def test_drift_handles_empty_per_run():
    d = DriftDetector()
    result = {"per_run": []}
    s = d.classify("CP-X", result, elapsed_s=10.0)
    assert s.level == "ok"


def test_drift_handles_missing_cube_final():
    d = DriftDetector()
    result = {"per_run": [{}]}
    s = d.classify("CP-X", result, elapsed_s=10.0)
    assert s.level == "ok"


# ── MemoryMonitor ──────────────────────────────────────────────────────────


def test_memory_has_grown_rss_true():
    m = MemoryMonitor(baseline_mb=1000.0)
    assert m.has_grown_rss(1900.0, threshold_x=1.8) is True


def test_memory_has_grown_rss_false_below_threshold():
    m = MemoryMonitor(baseline_mb=1000.0)
    assert m.has_grown_rss(1500.0, threshold_x=1.8) is False


def test_memory_has_grown_rss_false_no_baseline():
    m = MemoryMonitor(baseline_mb=0.0)
    assert m.has_grown_rss(5000.0) is False


def test_memory_has_grown_gpu():
    m = MemoryMonitor(baseline_gpu_mb=4000.0)
    assert m.has_grown_gpu(6500.0, threshold_x=1.5) is True
    assert m.has_grown_gpu(5500.0, threshold_x=1.5) is False


# ── SupervisorConfig ───────────────────────────────────────────────────────


def test_config_defaults():
    c = SupervisorConfig()
    assert c.restart_every_n == 25
    assert c.health_timeout_s == 2.0
    assert c.health_url.startswith("http://localhost:8001")


def test_config_custom_port():
    c = SupervisorConfig(kit_port=9999)
    assert "9999" in c.health_url


# ── KitSupervisor: state transitions ──────────────────────────────────────


class _StubProbe:
    def __init__(self, healthy: bool = True):
        self.healthy = healthy
        self.calls = 0

    async def is_healthy(self) -> Tuple[bool, str]:
        self.calls += 1
        return (self.healthy, "" if self.healthy else "stub_unhealthy")


class _StubRestartManager:
    def __init__(self, success: bool = True):
        self.success = success
        self.restart_count = 0

    async def restart(self, probe) -> bool:
        self.restart_count += 1
        return self.success


def _make_supervisor(restart_every_n: int = 5) -> KitSupervisor:
    cfg = SupervisorConfig(restart_every_n=restart_every_n)
    sup = KitSupervisor(cfg)
    sup.probe = _StubProbe()
    sup.manager = _StubRestartManager()
    return sup


@pytest.mark.asyncio
async def test_supervisor_should_restart_pre_after_n_cps():
    sup = _make_supervisor(restart_every_n=3)
    # After 3 CPs without restart, should request restart
    sup.state.cp_count_since_restart = 3
    reason = await sup.should_restart_pre()
    assert reason is not None
    assert "restart_every_n" in reason


@pytest.mark.asyncio
async def test_supervisor_should_restart_pre_on_unhealthy():
    sup = _make_supervisor()
    sup.probe = _StubProbe(healthy=False)
    reason = await sup.should_restart_pre()
    assert reason is not None
    assert "health_failed" in reason


@pytest.mark.asyncio
async def test_supervisor_should_not_restart_when_normal():
    sup = _make_supervisor()
    reason = await sup.should_restart_pre()
    assert reason is None


@pytest.mark.asyncio
async def test_supervisor_run_with_supervision_happy_path():
    sup = _make_supervisor()
    cube_normal = {"per_run": [{"cube_final": [0.5, -0.4, 0.78], "speed": 0.0}]}

    async def runner():
        return cube_normal

    result = await sup.run_with_supervision("CP-01", runner)
    assert result["_supervisor"]["drift_level"] == "ok"
    assert sup.state.cp_count_since_restart == 1
    assert sup.state.total_restarts == 0
    assert sup.state.total_drift_events == 0


@pytest.mark.asyncio
async def test_supervisor_run_with_supervision_drift_triggers_restart():
    sup = _make_supervisor()
    cube_exploded = {"per_run": [{"cube_final": [1e8, 1e7, 1e9], "speed": 1e8}]}

    call_count = [0]

    async def runner():
        call_count[0] += 1
        if call_count[0] == 1:
            return cube_exploded  # first call: drift
        return {"per_run": [{"cube_final": [0.5, -0.4, 0.78], "speed": 0.0}]}

    result = await sup.run_with_supervision("CP-01", runner)
    assert sup.state.total_drift_events == 1
    assert sup.state.total_restarts == 1  # restart fired
    assert call_count[0] == 2  # retry happened
    # Final result is the retry's clean result
    assert result["_supervisor"]["drift_level"] == "ok"


@pytest.mark.asyncio
async def test_supervisor_periodic_restart():
    sup = _make_supervisor(restart_every_n=2)
    normal = {"per_run": [{"cube_final": [0.0, 0.0, 0.5], "speed": 0.0}]}

    async def runner():
        return normal

    # First 2 CPs — no restart
    await sup.run_with_supervision("CP-01", runner)
    await sup.run_with_supervision("CP-02", runner)
    assert sup.state.total_restarts == 0

    # 3rd CP — pre-check triggers restart (count >= 2)
    await sup.run_with_supervision("CP-03", runner)
    assert sup.state.total_restarts == 1
    assert sup.state.cp_count_since_restart == 1  # reset, then +1


def test_supervisor_stats():
    sup = _make_supervisor()
    sup.state.total_restarts = 3
    sup.state.total_drift_events = 5
    stats = sup.stats()
    assert stats["total_restarts"] == 3
    assert stats["total_drift_events"] == 5
    assert "elapsed_baselines" in stats


# ── Integration smoke: detector → supervisor flow ──────────────────────────


@pytest.mark.asyncio
async def test_supervisor_record_baseline_on_success():
    """After a successful CP, the elapsed baseline is recorded."""
    sup = _make_supervisor()
    normal = {"per_run": [{"cube_final": [0.0, 0.0, 0.5], "speed": 0.0}]}

    async def runner():
        return normal

    await sup.run_with_supervision("CP-01", runner)
    assert "CP-01" in sup.detector._elapsed_baseline
    assert sup.detector._elapsed_baseline["CP-01"] > 0
