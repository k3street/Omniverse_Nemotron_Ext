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


# ── Soft-reset cadence ─────────────────────────────────────────────────────


def test_should_soft_reset_off_when_disabled():
    cfg = SupervisorConfig(enable_soft_reset=False, soft_reset_every_n=5)
    sup = KitSupervisor(cfg)
    sup.state.cp_count_since_restart = 5
    assert sup.should_soft_reset() is False


def test_should_soft_reset_at_boundary():
    cfg = SupervisorConfig(enable_soft_reset=True, soft_reset_every_n=5)
    sup = KitSupervisor(cfg)
    sup.state.cp_count_since_restart = 5
    assert sup.should_soft_reset() is True


def test_should_soft_reset_off_at_zero():
    """Just-restarted state (count=0) should NOT soft-reset."""
    cfg = SupervisorConfig(enable_soft_reset=True, soft_reset_every_n=5)
    sup = KitSupervisor(cfg)
    sup.state.cp_count_since_restart = 0
    assert sup.should_soft_reset() is False


def test_should_soft_reset_off_between_boundaries():
    cfg = SupervisorConfig(enable_soft_reset=True, soft_reset_every_n=10)
    sup = KitSupervisor(cfg)
    sup.state.cp_count_since_restart = 7
    assert sup.should_soft_reset() is False


# ── Restart failure escalation ─────────────────────────────────────────────


class _FailingRestartManager:
    def __init__(self, fail_count: int):
        self.fail_count = fail_count
        self.calls = 0

    async def restart(self, probe) -> bool:
        self.calls += 1
        if self.calls <= self.fail_count:
            return False
        return True


@pytest.mark.asyncio
async def test_supervisor_retries_restart_once_on_failure():
    """One failed restart triggers exactly one retry."""
    sup = _make_supervisor()
    sup.manager = _FailingRestartManager(fail_count=1)  # fails first, succeeds
    await sup._do_hard_restart()
    assert sup.manager.calls == 2
    assert sup.state.consecutive_restart_failures == 0  # reset on success


@pytest.mark.asyncio
async def test_supervisor_aborts_after_consecutive_failures():
    """Two consecutive failures → SupervisorAbortError."""
    from scripts.qa.kit_supervisor import SupervisorAbortError

    cfg = SupervisorConfig(abort_after_failed_restarts=2)
    sup = KitSupervisor(cfg)
    sup.probe = _StubProbe()
    sup.manager = _FailingRestartManager(fail_count=10)  # always fails

    with pytest.raises(SupervisorAbortError) as exc:
        await sup._do_hard_restart()
    assert "unrecoverable" in str(exc.value)


# ── Telemetry emission ────────────────────────────────────────────────────


class _StubStore:
    def __init__(self):
        self.events: list = []

    def append_event(self, session_id, event_type, payload):
        self.events.append((session_id, event_type, payload))
        return len(self.events)


@pytest.mark.asyncio
async def test_supervisor_emits_drift_classification():
    cfg = SupervisorConfig(telemetry_emit=True)
    store = _StubStore()
    sup = KitSupervisor(cfg, store=store)
    sup.probe = _StubProbe()
    sup.manager = _StubRestartManager()

    normal = {"per_run": [{"cube_final": [0.5, 0.0, 0.5], "speed": 0.0}]}
    async def runner():
        return normal
    await sup.run_with_supervision("CP-01", runner)

    types = [e[1] for e in store.events]
    assert "supervisor_drift_classification" in types


@pytest.mark.asyncio
async def test_supervisor_emits_drift_detected_on_explosion():
    cfg = SupervisorConfig(telemetry_emit=True)
    store = _StubStore()
    sup = KitSupervisor(cfg, store=store)
    sup.probe = _StubProbe()
    sup.manager = _StubRestartManager()

    explode = {"per_run": [{"cube_final": [1e8, 1e7, 1e9], "speed": 1e7}]}
    clean = {"per_run": [{"cube_final": [0.5, 0.0, 0.5], "speed": 0.0}]}
    calls = [0]
    async def runner():
        calls[0] += 1
        return explode if calls[0] == 1 else clean
    await sup.run_with_supervision("CP-01", runner)

    types = [e[1] for e in store.events]
    assert "supervisor_drift_detected" in types
    assert "supervisor_restart_started" in types
    assert "supervisor_restart_completed" in types


@pytest.mark.asyncio
async def test_supervisor_telemetry_disabled_no_emit():
    cfg = SupervisorConfig(telemetry_emit=False)
    store = _StubStore()
    sup = KitSupervisor(cfg, store=store)
    sup.probe = _StubProbe()
    sup.manager = _StubRestartManager()

    async def runner():
        return {"per_run": [{"cube_final": [0.0, 0.0, 0.5], "speed": 0.0}]}
    await sup.run_with_supervision("CP-01", runner)
    assert len(store.events) == 0


# ── Config env-overrides ──────────────────────────────────────────────────


def test_config_from_env_int(monkeypatch):
    monkeypatch.setenv("SUPERVISOR_RESTART_EVERY_N", "42")
    c = SupervisorConfig.from_env()
    assert c.restart_every_n == 42


def test_config_from_env_bool(monkeypatch):
    monkeypatch.setenv("SUPERVISOR_ENABLE_SOFT_RESET", "false")
    c = SupervisorConfig.from_env()
    assert c.enable_soft_reset is False


def test_config_from_env_float(monkeypatch):
    monkeypatch.setenv("SUPERVISOR_ELAPSED_DRIFT_X", "3.5")
    c = SupervisorConfig.from_env()
    assert c.elapsed_drift_x == 3.5


def test_config_from_env_overrides_yield_to_kwargs(monkeypatch):
    monkeypatch.setenv("SUPERVISOR_RESTART_EVERY_N", "42")
    c = SupervisorConfig.from_env(restart_every_n=99)
    assert c.restart_every_n == 99


# ── Health URLs ────────────────────────────────────────────────────────────


def test_config_reset_url_derives_from_host_port():
    c = SupervisorConfig(kit_host="myhost", kit_port=9001)
    assert c.reset_url == "http://myhost:9001/admin/reset_world"


# ── DriftDetector with negative speed (defensive) ──────────────────────────


def test_drift_ignores_negative_speed():
    """Negative speed shouldn't trigger absurd-speed (only |speed| matters)."""
    d = DriftDetector()
    result = {"per_run": [{"cube_final": [0, 0, 0.5], "speed": -10}]}
    s = d.classify("CP-X", result, 70.0)
    assert s.level == "ok"


def test_drift_signal_evidence_is_serializable():
    """DriftSignal.evidence must be JSON-serializable for telemetry."""
    import json
    d = DriftDetector()
    result = {"per_run": [{"cube_final": [1e8, 1e7, 1e9], "speed": 0.0}]}
    s = d.classify("CP-X", result, 70.0)
    # Should not raise
    json.dumps({"level": s.level, "reason": s.reason, "evidence": s.evidence})


# ── Kit-failure verdicts trigger drift ─────────────────────────────────────


def test_drift_reset_failed_treated_as_drift():
    """RESET_FAILED verdict from runner → drift (Kit unresponsive)."""
    d = DriftDetector()
    result = {"verdict": "RESET_FAILED", "err": "exec_sync timeout"}
    s = d.classify("CP-49", result, elapsed_s=20.0)
    assert s.level == "drift"
    assert "kit_failure_verdict" in s.reason
    assert "RESET_FAILED" in s.reason


def test_drift_build_exc_treated_as_drift():
    d = DriftDetector()
    result = {"verdict": "BUILD_EXC", "err": "kit_rpc 500"}
    s = d.classify("CP-X", result, elapsed_s=5.0)
    assert s.level == "drift"


def test_drift_timeout_treated_as_drift():
    d = DriftDetector()
    result = {"verdict": "TIMEOUT_240"}
    s = d.classify("CP-X", result, elapsed_s=245.0)
    assert s.level == "drift"


def test_drift_normal_verdicts_not_drift():
    """stable_ok / stable_fail are not Kit failures."""
    d = DriftDetector()
    for v in ("stable_ok", "stable_fail", "flaky", "BUILD_OK"):
        # Note: "status" or "verdict" key
        result = {"verdict": v}
        s = d.classify("CP-X", result, elapsed_s=70.0)
        assert s.level == "ok", f"verdict {v} should not be drift"


# ── Stop + stats integration ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_supervisor_stop_emits_summary():
    cfg = SupervisorConfig(telemetry_emit=True)
    store = _StubStore()
    sup = KitSupervisor(cfg, store=store)

    stats = await sup.stop()
    types = [e[1] for e in store.events]
    assert "supervisor_stopped" in types
    assert "total_restarts" in stats


def test_supervisor_stats_includes_consecutive_failures():
    sup = _make_supervisor()
    sup.state.consecutive_restart_failures = 1
    s = sup.stats()
    assert s["consecutive_restart_failures"] == 1
    assert "total_cp_count" in s
