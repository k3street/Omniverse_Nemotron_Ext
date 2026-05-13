"""Phase 97 contract tests — Performance regression CI."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.l0


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

def _mod():
    from service.isaac_assist_service.multimodal import performance_regression_ci
    return performance_regression_ci


def _sample(name: str, duration_ms: float, throughput=None, memory_mb=None):
    from service.isaac_assist_service.multimodal.performance_regression_ci import BenchmarkSample
    return BenchmarkSample(
        name=name,
        duration_ms=duration_ms,
        throughput_ops_per_s=throughput,
        memory_mb=memory_mb,
    )


def _suite(name: str, samples):
    from service.isaac_assist_service.multimodal.performance_regression_ci import BenchmarkSuite
    return BenchmarkSuite(suite_name=name, samples=samples)


def _detector(**kwargs):
    from service.isaac_assist_service.multimodal.performance_regression_ci import PerformanceRegressionDetector
    return PerformanceRegressionDetector(**kwargs)


# ---------------------------------------------------------------------------
# Test 1 — metadata
# ---------------------------------------------------------------------------

def test_phase_97_metadata():
    md = _mod().get_phase_metadata()
    assert md["phase"] == 97
    assert md["status"] == "landed"
    assert "title" in md
    assert "spec_ref" in md


# ---------------------------------------------------------------------------
# Test 2 — BENCHMARK_SPEC has >= 8 entries with required keys
# ---------------------------------------------------------------------------

def test_benchmark_spec_minimum_entries():
    spec = _mod().BENCHMARK_SPEC
    assert len(spec) >= 8, f"Expected >= 8 benchmark entries, got {len(spec)}"


def test_benchmark_spec_required_keys():
    spec = _mod().BENCHMARK_SPEC
    required = {"description", "target_ms", "max_ms", "sla_pct"}
    for entry_name, entry in spec.items():
        missing = required - set(entry.keys())
        assert not missing, f"Entry '{entry_name}' missing keys: {missing}"


def test_benchmark_spec_expected_names():
    spec = _mod().BENCHMARK_SPEC
    expected_names = {
        "chat_endpoint_p50",
        "chat_endpoint_p99",
        "tool_call_dispatch",
        "pydantic_validation",
        "stage_query_p50",
        "patch_validator_pipeline",
        "omnigraph_node_creation",
        "vision_scene_summary",
    }
    for name in expected_names:
        assert name in spec, f"Missing benchmark spec entry: '{name}'"


# ---------------------------------------------------------------------------
# Test 3 — latency regression at exactly 10 % threshold → warn
# ---------------------------------------------------------------------------

def test_latency_regression_warn_at_threshold():
    """A 10 % latency increase with default threshold should produce a warn."""
    baseline = _suite("base", [_sample("chat_endpoint_p50", duration_ms=100.0)])
    current = _suite("cur", [_sample("chat_endpoint_p50", duration_ms=110.0)])
    report = _detector(latency_threshold_pct=10.0).compare(baseline, current)

    assert len(report.regressions) == 1
    finding = report.regressions[0]
    assert finding.metric == "latency"
    assert finding.benchmark == "chat_endpoint_p50"
    assert finding.severity == "warn"
    assert abs(finding.delta_pct - 10.0) < 0.01


# ---------------------------------------------------------------------------
# Test 4 — latency regression at 25 % → critical (>= 2x threshold = 20%)
# ---------------------------------------------------------------------------

def test_latency_regression_critical_at_25pct():
    """25 % latency increase with 10 % threshold → 2.5x threshold → critical."""
    baseline = _suite("base", [_sample("tool_call_dispatch", duration_ms=100.0)])
    current = _suite("cur", [_sample("tool_call_dispatch", duration_ms=125.0)])
    report = _detector(latency_threshold_pct=10.0).compare(baseline, current)

    assert len(report.regressions) == 1
    finding = report.regressions[0]
    assert finding.severity == "critical"
    assert abs(finding.delta_pct - 25.0) < 0.01


# ---------------------------------------------------------------------------
# Test 5 — 50 % throughput drop → critical
# ---------------------------------------------------------------------------

def test_throughput_50pct_drop_is_critical():
    """50 % drop in ops/s with default -10 % threshold → critical."""
    baseline = _suite("base", [_sample("chat_endpoint_p50", duration_ms=100.0, throughput=200.0)])
    current = _suite("cur", [_sample("chat_endpoint_p50", duration_ms=100.0, throughput=100.0)])
    report = _detector().compare(baseline, current)

    thr_findings = [f for f in report.regressions if f.metric == "throughput"]
    assert len(thr_findings) == 1
    finding = thr_findings[0]
    assert finding.severity == "critical"
    assert finding.delta_pct < -40.0  # -50%


# ---------------------------------------------------------------------------
# Test 6 — memory regression at exactly 20 % → warn
# ---------------------------------------------------------------------------

def test_memory_regression_warn_at_threshold():
    """20 % memory growth with default 20 % threshold → warn."""
    baseline = _suite("base", [_sample("pydantic_validation", duration_ms=2.0, memory_mb=100.0)])
    current = _suite("cur", [_sample("pydantic_validation", duration_ms=2.0, memory_mb=120.0)])
    report = _detector(memory_threshold_pct=20.0).compare(baseline, current)

    mem_findings = [f for f in report.regressions if f.metric == "memory"]
    assert len(mem_findings) == 1
    finding = mem_findings[0]
    assert finding.metric == "memory"
    assert finding.severity == "warn"
    assert abs(finding.delta_pct - 20.0) < 0.01


# ---------------------------------------------------------------------------
# Test 7 — 5 % latency change is NOT a regression (below threshold)
# ---------------------------------------------------------------------------

def test_latency_5pct_below_threshold_not_regression():
    """5 % increase with 10 % threshold → unchanged (not a regression)."""
    baseline = _suite("base", [_sample("stage_query_p50", duration_ms=100.0)])
    current = _suite("cur", [_sample("stage_query_p50", duration_ms=105.0)])
    report = _detector(latency_threshold_pct=10.0).compare(baseline, current)

    lat_regressions = [f for f in report.regressions if f.metric == "latency"]
    assert len(lat_regressions) == 0
    assert report.unchanged == 1


# ---------------------------------------------------------------------------
# Test 8 — compare unchanged when difference < threshold
# ---------------------------------------------------------------------------

def test_compare_unchanged_within_threshold():
    """1 % change → below any default threshold → unchanged."""
    baseline = _suite("base", [_sample("patch_validator_pipeline", duration_ms=30.0)])
    current = _suite("cur", [_sample("patch_validator_pipeline", duration_ms=30.3)])
    report = _detector().compare(baseline, current)

    assert report.unchanged == 1
    assert len(report.regressions) == 0
    assert len(report.improvements) == 0


# ---------------------------------------------------------------------------
# Test 9 — load / save round-trip
# ---------------------------------------------------------------------------

def test_load_save_baseline_roundtrip():
    from service.isaac_assist_service.multimodal.performance_regression_ci import (
        BenchmarkSample,
        BenchmarkSuite,
        save_baseline_to_json,
        load_baseline_from_json,
    )

    original = BenchmarkSuite(
        suite_name="roundtrip-test",
        git_sha="abc123",
        samples=[
            BenchmarkSample("chat_endpoint_p50", 120.5, throughput_ops_per_s=80.0, memory_mb=256.0),
            BenchmarkSample("tool_call_dispatch", 12.0),
        ],
    )

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "subdir" / "baseline.json"
        save_baseline_to_json(original, path)
        assert path.exists()

        loaded = load_baseline_from_json(path)

    assert loaded.suite_name == original.suite_name
    assert loaded.git_sha == original.git_sha
    assert len(loaded.samples) == len(original.samples)

    s0 = loaded.samples[0]
    assert s0.name == "chat_endpoint_p50"
    assert s0.duration_ms == pytest.approx(120.5)
    assert s0.throughput_ops_per_s == pytest.approx(80.0)
    assert s0.memory_mb == pytest.approx(256.0)

    s1 = loaded.samples[1]
    assert s1.name == "tool_call_dispatch"
    assert s1.throughput_ops_per_s is None
    assert s1.memory_mb is None


# ---------------------------------------------------------------------------
# Test 10 — improvement (faster latency) goes to improvements, not regressions
# ---------------------------------------------------------------------------

def test_improvement_faster_latency_goes_to_improvements():
    """A 15 % speedup exceeds the 10 % threshold → improvement, not regression."""
    baseline = _suite("base", [_sample("vision_scene_summary", duration_ms=200.0)])
    current = _suite("cur", [_sample("vision_scene_summary", duration_ms=170.0)])  # -15 %
    report = _detector(latency_threshold_pct=10.0).compare(baseline, current)

    assert len(report.regressions) == 0
    assert len(report.improvements) == 1
    imp = report.improvements[0]
    assert imp.metric == "latency"
    assert imp.delta_pct < -10.0


# ---------------------------------------------------------------------------
# Test 11 — benchmarks missing from one suite are skipped gracefully
# ---------------------------------------------------------------------------

def test_missing_benchmark_in_current_skipped():
    """Benchmarks only in baseline (not current) do not crash compare."""
    baseline = _suite("base", [
        _sample("chat_endpoint_p50", 100.0),
        _sample("tool_call_dispatch", 15.0),
    ])
    current = _suite("cur", [
        _sample("chat_endpoint_p50", 100.0),
        # tool_call_dispatch absent
    ])
    report = _detector().compare(baseline, current)
    # Only 1 common benchmark
    assert report.total_benchmarks == 1


# ---------------------------------------------------------------------------
# Test 12 — total_benchmarks count is correct
# ---------------------------------------------------------------------------

def test_total_benchmarks_count():
    names = ["chat_endpoint_p50", "tool_call_dispatch", "pydantic_validation"]
    samples = [_sample(n, 50.0) for n in names]
    baseline = _suite("base", samples)
    current = _suite("cur", [_sample(n, 50.0) for n in names])
    report = _detector().compare(baseline, current)
    assert report.total_benchmarks == 3


# ---------------------------------------------------------------------------
# Test 13 — RegressionReport unchanged + regressions + improvements are consistent
# ---------------------------------------------------------------------------

def test_report_counts_consistent():
    """Sum of regressions (latency-only), improvements, unchanged == total."""
    samples_b = [
        _sample("chat_endpoint_p50", 100.0),   # will regress
        _sample("tool_call_dispatch", 100.0),   # will improve
        _sample("pydantic_validation", 100.0),  # unchanged
    ]
    samples_c = [
        _sample("chat_endpoint_p50", 120.0),    # +20% → regression
        _sample("tool_call_dispatch", 80.0),    # -20% → improvement
        _sample("pydantic_validation", 101.0),  # +1% → unchanged
    ]
    baseline = _suite("base", samples_b)
    current = _suite("cur", samples_c)
    report = _detector(latency_threshold_pct=10.0).compare(baseline, current)

    lat_regressions = [f for f in report.regressions if f.metric == "latency"]
    lat_improvements = [f for f in report.improvements if f.metric == "latency"]
    assert len(lat_regressions) == 1
    assert len(lat_improvements) == 1
    assert report.unchanged == 1
    assert report.total_benchmarks == 3
