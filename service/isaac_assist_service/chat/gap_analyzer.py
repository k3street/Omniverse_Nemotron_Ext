"""Match a StructuredSpec's expected_tool per step against the registered
tool catalog. Pure Python, deterministic, no LLM.

Given a spec with N steps each naming an expected_tool and the union of
CODE_GEN_HANDLERS + DATA_HANDLERS keys from tool_executor, classify each
step as:

  matched  — expected_tool is a registered tool name (exact match)
  partial  — expected_tool is close to a registered name (Levenshtein
             distance ≤ 3 OR shared prefix ≥ 6 chars). Likely a typo or
             the agent guessed a plausible-but-wrong name. Orchestrator
             surfaces the suggestion so the agent can correct in the
             next round.
  missing  — no registered tool name is within Levenshtein ≤ 3 or prefix
             ≥ 6. The step's intent needs a new tool OR a run_usd_script
             manual approach.

The GapReport is shown to the agent via context injection so it can
either (a) use the matched tools for steps that have them and manually
author missing ones, or (b) report back that a named step can't be done
and ask the user to add a tool.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from .spec_generator import SpecStep, StructuredSpec

logger = logging.getLogger(__name__)


@dataclass
class GapStep:
    """A step + its gap classification."""
    step: SpecStep
    status: str  # "matched" | "partial" | "missing"
    suggested_tool: Optional[str] = None  # for partial/missing: closest real tool
    distance: Optional[int] = None


@dataclass
class GapReport:
    total_steps: int = 0
    matched: List[GapStep] = field(default_factory=list)
    partial: List[GapStep] = field(default_factory=list)
    missing: List[GapStep] = field(default_factory=list)

    @property
    def coverage(self) -> float:
        if self.total_steps == 0:
            return 0.0
        return len(self.matched) / self.total_steps

    def to_dict(self) -> dict:
        def _render(gs: GapStep) -> dict:
            return {
                "n": gs.step.n,
                "intent": gs.step.intent,
                "expected_tool": gs.step.expected_tool,
                "suggested_tool": gs.suggested_tool,
                "distance": gs.distance,
            }
        return {
            "total_steps": self.total_steps,
            "coverage": round(self.coverage, 2),
            "matched": [_render(g) for g in self.matched],
            "partial": [_render(g) for g in self.partial],
            "missing": [_render(g) for g in self.missing],
        }

    def summary_text(self) -> str:
        """Human-readable summary for injection into the system prompt."""
        parts = [
            f"Spec has {self.total_steps} steps. "
            f"Tool coverage: {len(self.matched)}/{self.total_steps} "
            f"({int(self.coverage * 100)}%)."
        ]
        if self.matched:
            parts.append("\nMatched steps:")
            for g in self.matched:
                parts.append(
                    f"  - Step {g.step.n} ({g.step.intent[:50]}) → {g.step.expected_tool}"
                )
        if self.partial:
            parts.append("\nPartial (likely typo / wrong name):")
            for g in self.partial:
                parts.append(
                    f"  - Step {g.step.n} ({g.step.intent[:50]}) → "
                    f"expected={g.step.expected_tool!r}, did you mean "
                    f"{g.suggested_tool!r}? (edit distance {g.distance})"
                )
        if self.missing:
            parts.append("\nMissing — no tool covers these steps; use run_usd_script or ask the user:")
            for g in self.missing:
                parts.append(
                    f"  - Step {g.step.n} ({g.step.intent[:50]}) → "
                    f"expected={g.step.expected_tool!r} (no close match)"
                )
        return "\n".join(parts)


def _levenshtein(a: str, b: str) -> int:
    """Classic Levenshtein with row-array optimization. O(len(a)*len(b))."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        curr = [i]
        for j, cb in enumerate(b, 1):
            if ca == cb:
                curr.append(prev[j - 1])
            else:
                curr.append(1 + min(prev[j], curr[-1], prev[j - 1]))
        prev = curr
    return prev[-1]


def _closest(tool_name: str, catalog: Set[str]) -> tuple[Optional[str], int]:
    """Return (closest_real_tool, distance) — None if nothing is close."""
    if not tool_name:
        return None, 999
    best_name: Optional[str] = None
    best_dist = 999
    for candidate in catalog:
        d = _levenshtein(tool_name, candidate)
        if d < best_dist:
            best_dist = d
            best_name = candidate
    return best_name, best_dist


def analyze(spec: StructuredSpec, tool_catalog: Set[str]) -> GapReport:
    """Classify each spec step against the registered tool catalog.

    The catalog is the set of CODE_GEN_HANDLERS ∪ DATA_HANDLERS keys
    from tool_executor — see load_tool_catalog() below for the canonical
    loader.
    """
    report = GapReport(total_steps=len(spec.steps))
    for step in spec.steps:
        et = (step.expected_tool or "").strip()
        if not et:
            report.missing.append(GapStep(step=step, status="missing"))
            continue

        if et in tool_catalog:
            report.matched.append(GapStep(
                step=step, status="matched",
                suggested_tool=et, distance=0,
            ))
            continue

        closest, dist = _closest(et, tool_catalog)

        # Shared-prefix check catches e.g. "create_conveyor_belt" vs
        # "create_conveyor_track" where edit distance is > 3 but they're
        # clearly in the same family.
        shared_prefix = 0
        if closest:
            for a, b in zip(et, closest):
                if a == b:
                    shared_prefix += 1
                else:
                    break

        if dist <= 3 or shared_prefix >= 6:
            report.partial.append(GapStep(
                step=step, status="partial",
                suggested_tool=closest, distance=dist,
            ))
        else:
            report.missing.append(GapStep(
                step=step, status="missing",
                suggested_tool=closest, distance=dist,
            ))

    return report


def load_tool_catalog() -> Set[str]:
    """Pull the union of registered CODE_GEN_HANDLERS + DATA_HANDLERS keys
    from tool_executor. Deferred import to avoid circular dependency at
    module load time.
    """
    try:
        from .tools.tool_executor import CODE_GEN_HANDLERS, DATA_HANDLERS
        return set(CODE_GEN_HANDLERS.keys()) | set(DATA_HANDLERS.keys())
    except Exception as e:
        logger.warning(f"[gap_analyzer] tool catalog import failed: {e}")
        return set()
