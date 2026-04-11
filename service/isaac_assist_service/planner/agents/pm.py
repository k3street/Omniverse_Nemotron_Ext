"""
ProjectManagerAgent — orchestrates the Coder→QA→Critic feedback loop.

Responsibilities:
  1. Define task acceptance criteria before the loop starts
  2. After each iteration, aggregate sub-agent results
  3. Synthesise targeted feedback for the next Coder iteration
  4. Decide PASS / FAIL (max iterations reached, all criteria met, or budget exceeded)
  5. Produce a structured final report for MLflow / JSON output

Acceptance criteria (PM level — gate for the overall task):
  coder_passed     CoderAgent scored ≥ coder_threshold (default 0.75)
  qa_passed        QAAgent: all 6 criteria pass
  critic_passed    CriticAgent quality_score ≥ critic_threshold (default 0.70)
  budget_ok        Completed within max_iterations

The PM never calls an LLM itself — feedback synthesis is deterministic and
inspectable, making it easy to debug iteration failures.
"""

from __future__ import annotations

import textwrap
import time
from dataclasses import dataclass, field
from typing import Any

from .base import AgentBase, AgentResult, Criterion
from .coder import CoderAgent
from .critic import CriticAgent
from .qa import QAAgent


# ── Data types ────────────────────────────────────────────────────────────────

@dataclass
class IterationRecord:
    """Full snapshot of one loop iteration."""
    iteration:     int
    coder_result:  AgentResult | None = None
    qa_result:     AgentResult | None = None
    critic_result: AgentResult | None = None
    feedback_sent: str                = ""
    duration_s:    float              = 0.0

    def to_dict(self) -> dict:
        def _safe(r: AgentResult | None) -> dict | None:
            return r.to_dict() if r else None
        return {
            "iteration":     self.iteration,
            "coder":         _safe(self.coder_result),
            "qa":            _safe(self.qa_result),
            "critic":        _safe(self.critic_result),
            "feedback_sent": self.feedback_sent[:400],
            "duration_s":    round(self.duration_s, 2),
        }


@dataclass
class LoopResult:
    """Final result of a complete PM-orchestrated loop."""
    task_id:      str
    model_tag:    str
    status:       str             # "pass" | "fail" | "error"
    iterations:   list[IterationRecord] = field(default_factory=list)
    final_code:   list[str]             = field(default_factory=list)
    final_scores: dict                  = field(default_factory=dict)
    total_time_s: float                 = 0.0
    notes:        list[str]             = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "task_id":      self.task_id,
            "model_tag":    self.model_tag,
            "status":       self.status,
            "total_time_s": round(self.total_time_s, 2),
            "final_scores": self.final_scores,
            "final_code_blocks": len(self.final_code),
            "num_iterations": len(self.iterations),
            "notes":        self.notes,
            "iterations":   [it.to_dict() for it in self.iterations],
        }


# ── ProjectManagerAgent ───────────────────────────────────────────────────────

class ProjectManagerAgent(AgentBase):
    """
    Orchestrates the Coder → QA → Critic loop.

    Parameters
    ----------
    coder             : CoderAgent instance
    qa                : QAAgent instance
    critic            : CriticAgent instance
    max_iterations    : maximum loop iterations before declaring FAIL
    coder_threshold   : minimum Coder score to proceed to QA
    critic_threshold  : minimum Critic quality_score to declare PASS
    """

    name = "pm"

    def __init__(
        self,
        coder: CoderAgent,
        qa: QAAgent,
        critic: CriticAgent,
        max_iterations: int = 3,
        coder_threshold: float = 0.75,
        critic_threshold: float = 0.70,
    ) -> None:
        self.coder            = coder
        self.qa               = qa
        self.critic           = critic
        self.max_iterations   = max_iterations
        self.coder_threshold  = coder_threshold
        self.critic_threshold = critic_threshold

    # ── Public API ────────────────────────────────────────────────────────────

    def run(self, task: dict) -> LoopResult:            # type: ignore[override]
        """
        Run the full Coder → QA → Critic loop for a single task.

        Returns a LoopResult with the iteration history and final status.
        """
        loop_start = time.perf_counter()
        result = LoopResult(
            task_id=task.get("id", "unknown"),
            model_tag=self.coder.model_tag,
            status="fail",
        )

        qa_feedback     = ""
        critic_feedback = ""

        for iteration in range(self.max_iterations):
            iter_start = time.perf_counter()
            record     = IterationRecord(iteration=iteration)

            # ── 1. Coder ──────────────────────────────────────────────────────
            coder_result = self.coder.run(
                task=task,
                iteration=iteration,
                qa_feedback=qa_feedback,
                critic_feedback=critic_feedback,
            )
            record.coder_result = coder_result

            # If Coder fails hard (model down, no code), bail early
            if coder_result.status == "error":
                result.notes.append(f"iter={iteration}: Coder error — aborting loop")
                record.duration_s = time.perf_counter() - iter_start
                result.iterations.append(record)
                break

            # ── 2. QA ─────────────────────────────────────────────────────────
            qa_result = self.qa.run(coder_result=coder_result, iteration=iteration)
            record.qa_result = qa_result

            # ── 3. Critic ─────────────────────────────────────────────────────
            critic_result = self.critic.run(
                task=task,
                coder_result=coder_result,
                qa_result=qa_result,
                iteration=iteration,
            )
            record.critic_result = critic_result

            # ── 4. Evaluate acceptance ────────────────────────────────────────
            record.duration_s = time.perf_counter() - iter_start
            result.iterations.append(record)

            pm_criteria, all_pass = self._evaluate_acceptance(
                coder_result, qa_result, critic_result
            )

            if all_pass:
                result.status       = "pass"
                result.final_code   = (coder_result.output or {}).get("code_blocks", [])
                result.final_scores = self._build_scores(coder_result, qa_result, critic_result)
                result.notes.append(
                    f"PASSED on iteration {iteration} "
                    f"(coder={coder_result.score:.2f} "
                    f"qa={'pass' if qa_result.passed else 'fail'} "
                    f"critic={critic_result.score:.2f})"
                )
                break

            # Still failing — synthesise feedback for next Coder call
            qa_feedback, critic_feedback = self._synthesise_feedback(
                coder_result, qa_result, critic_result
            )
            record.feedback_sent = (qa_feedback + "\n" + critic_feedback).strip()

            if iteration == self.max_iterations - 1:
                result.status       = "fail"
                result.final_code   = (coder_result.output or {}).get("code_blocks", [])
                result.final_scores = self._build_scores(coder_result, qa_result, critic_result)
                result.notes.append(
                    f"FAILED after {self.max_iterations} iteration(s) — "
                    f"remaining issues: {self._remaining_issues(qa_result, critic_result)}"
                )

        result.total_time_s = time.perf_counter() - loop_start
        return result

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _evaluate_acceptance(
        self,
        coder: AgentResult,
        qa: AgentResult,
        critic: AgentResult,
    ) -> tuple[list[Criterion], bool]:
        """
        Check four PM-level acceptance criteria.
        Returns (criteria_list, all_pass).
        """
        coder_ok = self._criterion(
            "coder_passed",
            f"Coder score ≥ {self.coder_threshold:.0%}",
            passed=coder.score >= self.coder_threshold,
            score=coder.score,
            detail=f"score={coder.score:.2f}",
        )
        qa_ok = self._criterion(
            "qa_passed",
            "QA: all simulator criteria pass",
            passed=qa.passed and qa.status != "skip",
            score=qa.score,
            detail=(
                "skip" if qa.status == "skip"
                else ("pass" if qa.passed else _format_failed(qa))
            ),
        )
        critic_ok = self._criterion(
            "critic_passed",
            f"Critic quality ≥ {self.critic_threshold:.0%}",
            passed=critic.passed and critic.score >= self.critic_threshold,
            score=critic.score,
            detail=f"score={critic.score:.2f}",
        )
        criteria = [coder_ok, qa_ok, critic_ok]
        return criteria, all(c.passed for c in criteria)

    def _synthesise_feedback(
        self,
        coder: AgentResult,
        qa: AgentResult,
        critic: AgentResult,
    ) -> tuple[str, str]:
        """
        Build concise, actionable feedback for the next Coder iteration.

        Returns (qa_feedback_str, critic_feedback_str).
        """
        # QA feedback — simulator error logs
        qa_parts: list[str] = []
        if qa.output and qa.output.get("sim_results"):
            for sr in qa.output["sim_results"]:
                if not sr["passed"]:
                    qa_parts.append(
                        f"Block {sr['block_index']+1} failed "
                        f"(exit={sr['returncode']}, mode={sr['mode']}):"
                    )
                    if sr["errors"]:
                        qa_parts.extend(f"  ERROR: {e}" for e in sr["errors"][:3])
                    if sr["stderr"]:
                        qa_parts.append("  STDERR: " + sr["stderr"][:300])
                    if sr["antipatterns"]:
                        for ap_name, ap_desc in sr["antipatterns"].items():
                            qa_parts.append(f"  ANTIPATTERN ({ap_name}): {ap_desc}")
        qa_feedback = "\n".join(qa_parts)

        # Critic feedback — issues list
        critic_parts: list[str] = []
        if coder.feedback:
            critic_parts.append("Coder self-check issues:\n" + coder.feedback)

        if critic.output and isinstance(critic.output, dict):
            issues = critic.output.get("issues", [])
            for issue in issues:
                if issue.get("severity") in ("critical", "warning"):
                    critic_parts.append(
                        f"[{issue['severity'].upper()}] ({issue.get('category','')}) "
                        f"{issue['description']}\n"
                        f"  → Fix: {issue.get('fix','')}"
                    )
            if critic.output.get("summary"):
                critic_parts.append(
                    "Overall assessment: " + critic.output["summary"][:200]
                )
        critic_feedback = "\n".join(critic_parts)

        return qa_feedback, critic_feedback

    @staticmethod
    def _build_scores(
        coder: AgentResult,
        qa: AgentResult,
        critic: AgentResult,
    ) -> dict:
        return {
            "coder_score":  round(coder.score, 3),
            "qa_score":     round(qa.score, 3),
            "critic_score": round(critic.score, 3),
            "overall":      round(
                (coder.score + qa.score + critic.score) / 3, 3
            ),
        }

    @staticmethod
    def _remaining_issues(qa: AgentResult, critic: AgentResult) -> str:
        issues: list[str] = []
        for c in qa.failed_criteria:
            issues.append(f"qa:{c.name}")
        for c in critic.failed_criteria:
            issues.append(f"critic:{c.name}")
        return ", ".join(issues) if issues else "unknown"


def _format_failed(result: AgentResult) -> str:
    """Short summary of what failed."""
    names = [c.name for c in result.failed_criteria]
    return "failed: " + ", ".join(names[:3])
