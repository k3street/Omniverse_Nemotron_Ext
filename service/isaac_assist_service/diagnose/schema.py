"""Constraint / Violation / Verdict types.

Mirrors `robotics_lab/lib/constraint_handler.py:90-98` (proven shape, do not
import — robotics_lab is a separate venv per memory).
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Dict, List, Optional


class Severity(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class Verdict(str, Enum):
    FEASIBLE = "feasible"
    TIGHTLY_FEASIBLE = "tightly_feasible"
    OVERCONSTRAINED = "overconstrained"
    INFEASIBLE = "infeasible"


@dataclass
class Violation:
    axis: str  # e.g. "reach_utilization", "ik_feasible", "clearance_pct"
    severity: Severity
    value: Any
    threshold: Any
    message: str  # human-readable, from messages.py templates
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["severity"] = self.severity.value
        return d


@dataclass
class Alternative:
    """Suggested fix; structured for both LLM and human consumption."""
    axis: str
    suggestion: str
    expected_value: Optional[Any] = None
    delta: Optional[Dict[str, Any]] = None  # e.g. {"axis":"x", "shift_m": 0.05}

    def to_dict(self) -> Dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}


@dataclass
class FeasibilityReport:
    verdict: Verdict
    metrics: Dict[str, Any]
    violations: List[Violation] = field(default_factory=list)
    alternatives: List[Alternative] = field(default_factory=list)
    seed_used: int = 42
    cache_hit: bool = False
    elapsed_ms: int = 0
    per_cycle: Optional[List[Dict[str, Any]]] = None  # multi-robot mode (Opus §E)
    aggregate: Optional[Dict[str, Any]] = None        # multi-robot mode

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "verdict": self.verdict.value,
            "metrics": self.metrics,
            "violations": [v.to_dict() for v in self.violations],
            "alternatives": [a.to_dict() for a in self.alternatives],
            "seed_used": self.seed_used,
            "cache_hit": self.cache_hit,
            "elapsed_ms": self.elapsed_ms,
        }
        if self.per_cycle is not None:
            out["per_cycle"] = self.per_cycle
        if self.aggregate is not None:
            out["aggregate"] = self.aggregate
        return out


def classify_verdict(violations: List[Violation]) -> Verdict:
    """Verdict taxonomy per spec §Verdict taxonomy.

    - infeasible: at least one CRITICAL
    - overconstrained: at least one ERROR (no CRITICAL)
    - tightly_feasible: at least one WARNING (no ERROR/CRITICAL)
    - feasible: nothing above INFO
    """
    has_crit = any(v.severity == Severity.CRITICAL for v in violations)
    has_err = any(v.severity == Severity.ERROR for v in violations)
    has_warn = any(v.severity == Severity.WARNING for v in violations)
    if has_crit:
        return Verdict.INFEASIBLE
    if has_err:
        return Verdict.OVERCONSTRAINED
    if has_warn:
        return Verdict.TIGHTLY_FEASIBLE
    return Verdict.FEASIBLE


# Threshold table — single source of truth used by metrics.py.
# Mirrors spec §Metrics computed.
THRESHOLDS = {
    "ik_feasible": {
        "fail_severity": Severity.CRITICAL,  # not feasible at all
    },
    "collision_distance": {
        "critical": 0.0,    # < 0 → in collision
        "error":    0.005,  # < 5mm → too close to obstacle
    },
    "manipulability": {
        "warning": 0.05,    # < 0.05 → singular/near-singular
    },
    "reach_utilization": {
        "critical": 1.0,    # > 1.0 → out of reach
        "warning":  0.95,   # > 95% → IK fragile near edge
    },
    "inside_obstacle_bbox": {
        "fail_severity": Severity.CRITICAL,
    },
    "clearance_pct": {
        "error":   60.0,    # < 60% → straight-line mostly blocked
        "warning": 90.0,    # < 90% → partially blocked
    },
    "cube_in_sensor_zone_at_settle": {
        "fail_severity": Severity.ERROR,  # controller never claims
    },
    "mutex_conflict": {
        "fail_severity": Severity.ERROR,  # multi-robot corridor overlap w/o mutex
    },
}
