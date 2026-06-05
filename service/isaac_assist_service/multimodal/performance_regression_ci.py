"""Phase 97 — Performance regression CI.

Provides a benchmark spec registry, dataclasses for capturing benchmark
results, and a regression-detection engine that compares a current suite
against a saved baseline.

Usage (pure Python — no Isaac Sim / Kit required)::

    detector = PerformanceRegressionDetector(latency_threshold_pct=10.0)
    report = detector.compare(baseline_suite, current_suite)
    for finding in report.regressions:
        print(finding)

Persistence helpers::

    save_baseline_to_json(suite, Path("baselines/main.json"))
    suite = load_baseline_from_json(Path("baselines/main.json"))

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 97.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional


PHASE_ID = 97
PHASE_TITLE = "Performance regression CI"
PHASE_STATUS = "landed"


def get_phase_metadata() -> Dict[str, Any]:
    """Return phase identification and status for this phase.

    Returns:
        Dict[str, Any]: Keys ``phase``, ``title``, ``status``, and ``spec_ref``.
    """
    return {
        "phase": PHASE_ID,
        "title": PHASE_TITLE,
        "status": PHASE_STATUS,
        "spec_ref": "specs/IA_FULL_SPEC_2026-05-10.md Phase 97",
    }


# ---------------------------------------------------------------------------
# Benchmark spec registry
# ---------------------------------------------------------------------------

#: Canonical benchmark definitions.  Each entry describes an endpoint /
#: operation that should be tracked in CI.
#:
#: Fields per entry:
#:   description  — human-readable purpose
#:   target_ms    — aspirational P50 latency in milliseconds
#:   max_ms       — hard SLA ceiling; exceeding this is always critical
#:   sla_pct      — fraction of requests that must finish within ``target_ms``
#:                  (expressed as a value in [0, 1])
BENCHMARK_SPEC: Dict[str, Dict[str, Any]] = {
    "chat_endpoint_p50": {
        "description": "Chat /v1/chat completion endpoint — median latency",
        "target_ms": 400.0,
        "max_ms": 2000.0,
        "sla_pct": 0.50,
    },
    "chat_endpoint_p99": {
        "description": "Chat /v1/chat completion endpoint — 99th-percentile latency",
        "target_ms": 1500.0,
        "max_ms": 5000.0,
        "sla_pct": 0.99,
    },
    "tool_call_dispatch": {
        "description": "MCP tool-call dispatch overhead (routing only, no tool execution)",
        "target_ms": 15.0,
        "max_ms": 100.0,
        "sla_pct": 0.95,
    },
    "pydantic_validation": {
        "description": "Pydantic model validation for a canonical ToolInput payload",
        "target_ms": 2.0,
        "max_ms": 20.0,
        "sla_pct": 0.99,
    },
    "stage_query_p50": {
        "description": "USD stage query (prim list + attribute read) — median latency",
        "target_ms": 50.0,
        "max_ms": 500.0,
        "sla_pct": 0.50,
    },
    "patch_validator_pipeline": {
        "description": "Full patch-validator pipeline (parse → validate → apply diff)",
        "target_ms": 30.0,
        "max_ms": 200.0,
        "sla_pct": 0.95,
    },
    "omnigraph_node_creation": {
        "description": "OmniGraph node creation via Kit RPC",
        "target_ms": 80.0,
        "max_ms": 600.0,
        "sla_pct": 0.90,
    },
    "vision_scene_summary": {
        "description": "Vision scene-summary multimodal inference (image → JSON)",
        "target_ms": 800.0,
        "max_ms": 3000.0,
        "sla_pct": 0.90,
    },
}

_REQUIRED_SPEC_KEYS = {"description", "target_ms", "max_ms", "sla_pct"}


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class BenchmarkSample:
    """A single timing / resource measurement for one benchmark.

    Attributes
    ----------
    name:
        Identifier matching a key in ``BENCHMARK_SPEC`` (or a free-form
        label for ad-hoc benchmarks).
    duration_ms:
        Wall-clock duration in milliseconds.
    throughput_ops_per_s:
        Optional operations-per-second metric (e.g. requests/s).
    memory_mb:
        Optional peak resident-set memory in megabytes.
    timestamp:
        ISO-8601 string when the sample was collected.
    """

    name: str
    duration_ms: float
    throughput_ops_per_s: Optional[float] = None
    memory_mb: Optional[float] = None
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


@dataclass
class BenchmarkSuite:
    """A collection of benchmark samples from one CI run.

    Attributes
    ----------
    suite_name:
        Human-readable label (e.g. ``"nightly-main"``).
    samples:
        Ordered list of ``BenchmarkSample`` records.
    collected_at:
        ISO-8601 string for when the suite was collected.
    git_sha:
        Optional git commit SHA for traceability.
    """

    suite_name: str
    samples: List[BenchmarkSample]
    collected_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    git_sha: Optional[str] = None


@dataclass
class RegressionFinding:
    """One detected performance regression (or improvement).

    Attributes
    ----------
    metric:
        Which metric regressed: ``"latency"``, ``"throughput"``, or
        ``"memory"``.
    benchmark:
        Name of the benchmark that regressed.
    baseline_value:
        Reference value from the saved baseline suite.
    current_value:
        Observed value in the current run.
    delta_pct:
        ``(current - baseline) / baseline * 100``.  Positive means the
        current run is *higher* than baseline.
    severity:
        ``"critical"`` if |delta_pct| ≥ 2 × threshold, ``"warn"`` if
        |delta_pct| ≥ threshold, ``"info"`` if below threshold.
    """

    metric: Literal["latency", "throughput", "memory"]
    benchmark: str
    baseline_value: float
    current_value: float
    delta_pct: float
    severity: Literal["info", "warn", "critical"]


@dataclass
class RegressionReport:
    """Summary produced by ``PerformanceRegressionDetector.compare``.

    Attributes
    ----------
    total_benchmarks:
        Number of benchmarks compared (benchmarks present in both suites).
    regressions:
        Findings where performance is *worse* than baseline by at least the
        configured threshold.
    improvements:
        Findings where performance is *better* than baseline by at least the
        configured threshold.
    unchanged:
        Count of benchmarks whose delta is within the threshold band.
    """

    total_benchmarks: int
    regressions: List[RegressionFinding]
    improvements: List[RegressionFinding]
    unchanged: int


# ---------------------------------------------------------------------------
# Detector
# ---------------------------------------------------------------------------

class PerformanceRegressionDetector:
    """Compare a current benchmark suite against a saved baseline.

    Parameters
    ----------
    latency_threshold_pct:
        Percentage increase in ``duration_ms`` that counts as a regression.
        Default 10.0 → a 10 % slowdown triggers a warning.
    memory_threshold_pct:
        Percentage increase in ``memory_mb`` that counts as a regression.
        Default 20.0.
    throughput_threshold_pct:
        Percentage *decrease* in ``throughput_ops_per_s`` that counts as a
        regression.  Supplied as a **negative** number, e.g. ``-10.0`` means
        a 10 % drop triggers a warning.  Default ``-10.0``.
    """

    def __init__(
        self,
        latency_threshold_pct: float = 10.0,
        memory_threshold_pct: float = 20.0,
        throughput_threshold_pct: float = -10.0,
    ) -> None:
        """Initialise the detector with regression thresholds.

        Args:
            latency_threshold_pct (float, optional): % increase in ``duration_ms``
                that triggers a regression. Defaults to 10.0.
            memory_threshold_pct (float, optional): % increase in ``memory_mb``
                that triggers a regression. Defaults to 20.0.
            throughput_threshold_pct (float, optional): % drop in ops/s (negative)
                that triggers a regression. Defaults to -10.0.
        """
        self.latency_threshold_pct = latency_threshold_pct
        self.memory_threshold_pct = memory_threshold_pct
        # Stored as positive magnitude for internal comparisons
        self._throughput_threshold_abs = abs(throughput_threshold_pct)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def compare(
        self, baseline: BenchmarkSuite, current: BenchmarkSuite
    ) -> RegressionReport:
        """Detect regressions by comparing *current* to *baseline*.

        Only benchmarks whose ``name`` appears in **both** suites are
        evaluated.  Missing benchmarks are silently skipped.

        Returns
        -------
        RegressionReport
        """
        baseline_map: Dict[str, BenchmarkSample] = {
            s.name: s for s in baseline.samples
        }
        current_map: Dict[str, BenchmarkSample] = {
            s.name: s for s in current.samples
        }
        common_names = sorted(set(baseline_map) & set(current_map))

        regressions: List[RegressionFinding] = []
        improvements: List[RegressionFinding] = []
        unchanged_count = 0

        for name in common_names:
            b = baseline_map[name]
            c = current_map[name]

            # --- latency ---
            lat_finding = self._eval_latency(name, b, c)
            if self._is_regression_latency(lat_finding.delta_pct):
                regressions.append(lat_finding)
            elif self._is_improvement_latency(lat_finding.delta_pct):
                improvements.append(lat_finding)
            else:
                unchanged_count += 1

            # --- throughput (optional) ---
            if b.throughput_ops_per_s is not None and c.throughput_ops_per_s is not None:
                thr_finding = self._eval_throughput(name, b, c)
                if self._is_regression_throughput(thr_finding.delta_pct):
                    regressions.append(thr_finding)
                elif self._is_improvement_throughput(thr_finding.delta_pct):
                    improvements.append(thr_finding)
                # throughput within band: not counted separately in unchanged

            # --- memory (optional) ---
            if b.memory_mb is not None and c.memory_mb is not None:
                mem_finding = self._eval_memory(name, b, c)
                if self._is_regression_memory(mem_finding.delta_pct):
                    regressions.append(mem_finding)
                elif self._is_improvement_memory(mem_finding.delta_pct):
                    improvements.append(mem_finding)

        return RegressionReport(
            total_benchmarks=len(common_names),
            regressions=regressions,
            improvements=improvements,
            unchanged=unchanged_count,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _delta_pct(baseline_val: float, current_val: float) -> float:
        """``(current - baseline) / baseline * 100``."""
        if baseline_val == 0.0:
            return 0.0
        return (current_val - baseline_val) / baseline_val * 100.0

    def _severity(
        self, abs_delta: float, threshold: float
    ) -> Literal["info", "warn", "critical"]:
        """Map an absolute delta percentage to a severity level.

        Args:
            abs_delta (float): Absolute percentage change (always positive).
            threshold (float): Base threshold for ``"warn"``; 2× triggers ``"critical"``.

        Returns:
            Literal["info", "warn", "critical"]: Severity label.
        """
        if abs_delta >= 2.0 * threshold:
            return "critical"
        if abs_delta >= threshold:
            return "warn"
        return "info"

    # latency — higher duration_ms is worse
    def _eval_latency(
        self, name: str, b: BenchmarkSample, c: BenchmarkSample
    ) -> RegressionFinding:
        """Build a RegressionFinding for the latency metric of *name*.

        Args:
            name (str): Benchmark name.
            b (BenchmarkSample): Baseline sample.
            c (BenchmarkSample): Current sample.

        Returns:
            RegressionFinding: Finding with ``metric="latency"``.
        """
        delta = self._delta_pct(b.duration_ms, c.duration_ms)
        abs_delta = abs(delta)
        severity = self._severity(abs_delta, self.latency_threshold_pct)
        return RegressionFinding(
            metric="latency",
            benchmark=name,
            baseline_value=b.duration_ms,
            current_value=c.duration_ms,
            delta_pct=delta,
            severity=severity,
        )

    def _is_regression_latency(self, delta_pct: float) -> bool:
        """Return ``True`` when latency increased beyond the threshold.

        Args:
            delta_pct (float): Percentage change (positive = slower).

        Returns:
            bool: ``True`` when ``delta_pct >= latency_threshold_pct``.
        """
        return delta_pct >= self.latency_threshold_pct

    def _is_improvement_latency(self, delta_pct: float) -> bool:
        """Return ``True`` when latency improved beyond the threshold.

        Args:
            delta_pct (float): Percentage change (negative = faster).

        Returns:
            bool: ``True`` when ``delta_pct <= -latency_threshold_pct``.
        """
        return delta_pct <= -self.latency_threshold_pct

    # throughput — lower ops/s is worse (delta_pct is negative when slower)
    def _eval_throughput(
        self, name: str, b: BenchmarkSample, c: BenchmarkSample
    ) -> RegressionFinding:
        """Build a RegressionFinding for the throughput metric of *name*.

        Args:
            name (str): Benchmark name.
            b (BenchmarkSample): Baseline sample (must have ``throughput_ops_per_s``).
            c (BenchmarkSample): Current sample (must have ``throughput_ops_per_s``).

        Returns:
            RegressionFinding: Finding with ``metric="throughput"``.
        """
        assert b.throughput_ops_per_s is not None
        assert c.throughput_ops_per_s is not None
        delta = self._delta_pct(b.throughput_ops_per_s, c.throughput_ops_per_s)
        abs_delta = abs(delta)
        severity = self._severity(abs_delta, self._throughput_threshold_abs)
        return RegressionFinding(
            metric="throughput",
            benchmark=name,
            baseline_value=b.throughput_ops_per_s,
            current_value=c.throughput_ops_per_s,
            delta_pct=delta,
            severity=severity,
        )

    def _is_regression_throughput(self, delta_pct: float) -> bool:
        """Return ``True`` when throughput dropped beyond the threshold.

        Args:
            delta_pct (float): Percentage change (negative = fewer ops/s).

        Returns:
            bool: ``True`` when the drop exceeds ``throughput_threshold_pct``.
        """
        # regression = throughput dropped (negative delta exceeds threshold)
        return delta_pct <= -self._throughput_threshold_abs

    def _is_improvement_throughput(self, delta_pct: float) -> bool:
        """Return ``True`` when throughput improved beyond the threshold.

        Args:
            delta_pct (float): Percentage change (positive = more ops/s).

        Returns:
            bool: ``True`` when ``delta_pct >= throughput_threshold_abs``.
        """
        return delta_pct >= self._throughput_threshold_abs

    # memory — higher memory_mb is worse
    def _eval_memory(
        self, name: str, b: BenchmarkSample, c: BenchmarkSample
    ) -> RegressionFinding:
        """Build a RegressionFinding for the memory metric of *name*.

        Args:
            name (str): Benchmark name.
            b (BenchmarkSample): Baseline sample (must have ``memory_mb``).
            c (BenchmarkSample): Current sample (must have ``memory_mb``).

        Returns:
            RegressionFinding: Finding with ``metric="memory"``.
        """
        assert b.memory_mb is not None
        assert c.memory_mb is not None
        delta = self._delta_pct(b.memory_mb, c.memory_mb)
        abs_delta = abs(delta)
        severity = self._severity(abs_delta, self.memory_threshold_pct)
        return RegressionFinding(
            metric="memory",
            benchmark=name,
            baseline_value=b.memory_mb,
            current_value=c.memory_mb,
            delta_pct=delta,
            severity=severity,
        )

    def _is_regression_memory(self, delta_pct: float) -> bool:
        """Return ``True`` when memory usage increased beyond the threshold.

        Args:
            delta_pct (float): Percentage change (positive = more MB used).

        Returns:
            bool: ``True`` when ``delta_pct >= memory_threshold_pct``.
        """
        return delta_pct >= self.memory_threshold_pct

    def _is_improvement_memory(self, delta_pct: float) -> bool:
        """Return ``True`` when memory usage improved beyond the threshold.

        Args:
            delta_pct (float): Percentage change (negative = fewer MB used).

        Returns:
            bool: ``True`` when ``delta_pct <= -memory_threshold_pct``.
        """
        return delta_pct <= -self.memory_threshold_pct


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------

def _sample_to_dict(s: BenchmarkSample) -> Dict[str, Any]:
    """Serialise a BenchmarkSample to a plain dict for JSON persistence.

    Args:
        s (BenchmarkSample): Sample to serialise.

    Returns:
        Dict[str, Any]: All fields as primitive values.
    """
    return {
        "name": s.name,
        "duration_ms": s.duration_ms,
        "throughput_ops_per_s": s.throughput_ops_per_s,
        "memory_mb": s.memory_mb,
        "timestamp": s.timestamp,
    }


def _sample_from_dict(d: Dict[str, Any]) -> BenchmarkSample:
    """Deserialise a BenchmarkSample from a plain dict (e.g. parsed from JSON).

    Args:
        d (Dict[str, Any]): Dict produced by :func:`_sample_to_dict`.

    Returns:
        BenchmarkSample: Reconstructed sample.
    """
    return BenchmarkSample(
        name=d["name"],
        duration_ms=float(d["duration_ms"]),
        throughput_ops_per_s=d.get("throughput_ops_per_s"),
        memory_mb=d.get("memory_mb"),
        timestamp=d.get("timestamp", datetime.now(timezone.utc).isoformat()),
    )


def save_baseline_to_json(suite: BenchmarkSuite, path: Path) -> None:
    """Persist *suite* to a JSON file at *path*.

    Parent directories are created automatically.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: Dict[str, Any] = {
        "suite_name": suite.suite_name,
        "collected_at": suite.collected_at,
        "git_sha": suite.git_sha,
        "samples": [_sample_to_dict(s) for s in suite.samples],
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def load_baseline_from_json(path: Path) -> BenchmarkSuite:
    """Load a ``BenchmarkSuite`` from a JSON file previously written by
    :func:`save_baseline_to_json`.
    """
    path = Path(path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    return BenchmarkSuite(
        suite_name=raw["suite_name"],
        samples=[_sample_from_dict(d) for d in raw.get("samples", [])],
        collected_at=raw.get("collected_at", datetime.now(timezone.utc).isoformat()),
        git_sha=raw.get("git_sha"),
    )
