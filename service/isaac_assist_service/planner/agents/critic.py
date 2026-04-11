"""
CriticAgent — uses Claude to review code quality and simulator execution logs.

Acceptance criteria:
  no_critical_antipatterns  No patterns that would crash Isaac Sim
  api_usage_correct         APIs are called in the right order / with right args
  code_quality              Claude's quality score ≥ 0.70
  usd_schema_compliance     USD schema used correctly (Apply before access, etc.)

The Critic receives:
  - The task description (prompt + required_keywords + required_patterns)
  - All code blocks from the Coder
  - The QA execution logs (stdout, stderr, errors, antipatterns)

It returns a structured analysis with a quality score and itemised issues
that the PM uses to build the next Coder brief.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

from ...config import config
from .base import AgentBase, AgentResult, Criterion

CRITIC_MODEL   = getattr(config, "cloud_model_name", "gemini-1.5-pro-latest")
QUALITY_PASS   = 0.70          # minimum Claude quality score to pass
_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)

# ── Critic system prompt ──────────────────────────────────────────────────────

_CRITIC_SYSTEM = """\
You are a senior NVIDIA simulation engineer performing a code review against the Isaac Sim 5.1.0 specification (https://docs.isaacsim.omniverse.nvidia.com/5.1.0/py/index.html).
Your role is to critically evaluate Python code written for Isaac Sim / Isaac Lab /
OpenUSD / PhysX and return a structured JSON analysis.

Focus on:
1. Correctness of USD Physics schema usage (RigidBodyAPI, DriveAPI, MassAPI, etc.) according to 5.1.0 standards.
2. Isaac Sim crash patterns (nested RigidBodyAPI, missing .Apply(), wrong joint paths)
3. Proper API call ordering (stage creation → prim creation → API application → attributes)
4. Completeness relative to the task requirements
5. Code quality (clarity, error handling, comments)

Return ONLY a JSON object with this exact structure:
{
  "quality_score": <float 0.0-1.0>,
  "issues": [
    {
      "severity": "critical|warning|info",
      "category": "antipattern|api_order|schema|completeness|style",
      "description": "<what is wrong>",
      "fix": "<how to fix it>"
    }
  ],
  "strengths": ["<what the code does well>", ...],
  "summary": "<one-paragraph overall assessment>"
}
"""


def _build_critic_prompt(
    task: dict,
    code_blocks: list[str],
    qa_output: dict | None,
) -> str:
    """Build the prompt for Claude's code review."""
    parts: list[str] = []

    # Task context
    parts.append(f"## Task: {task.get('id', 'unknown')}")
    parts.append(f"**Prompt:** {task['prompt']}")
    if task.get("required_keywords"):
        parts.append("**Required keywords:** " + ", ".join(task["required_keywords"]))
    if task.get("required_patterns"):
        parts.append("**Required patterns:** " + ", ".join(task["required_patterns"]))
    if task.get("reference_apis"):
        parts.append("**Reference APIs:** " + ", ".join(task["reference_apis"]))

    # Code to review
    parts.append("\n## Code to Review")
    for i, code in enumerate(code_blocks, 1):
        parts.append(f"### Block {i}\n```python\n{code}\n```")

    # QA execution results
    if qa_output and qa_output.get("sim_results"):
        parts.append("\n## QA Simulator Execution Results")
        for sr in qa_output["sim_results"]:
            status = "PASS" if sr["passed"] else "FAIL"
            parts.append(
                f"- Block {sr['block_index']+1}: {status} "
                f"| mode={sr['mode']} | exit={sr['returncode']} | {sr['duration_s']:.2f}s"
            )
            if sr["errors"]:
                parts.append("  Errors: " + "; ".join(sr["errors"][:3]))
            if sr["antipatterns"]:
                parts.append("  Antipatterns: " + str(sr["antipatterns"]))
            if sr["stderr"]:
                stderr_snippet = sr["stderr"][:500]
                parts.append(f"  stderr snippet:\n  ```\n  {stderr_snippet}\n  ```")

    return "\n\n".join(parts)


def _parse_claude_response(text: str) -> dict | None:
    """Extract and parse the JSON analysis from Claude's response."""
    # Try fenced JSON first
    m = _JSON_FENCE_RE.search(text)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    # Try bare JSON (find first { ... })
    start = text.find("{")
    end   = text.rfind("}")
    if start != -1 and end != -1:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            pass
    return None


class CriticAgent(AgentBase):
    """
    Uses Claude to perform deep code review against Isaac Sim / USD standards.

    Falls back to rule-based scoring if the Anthropic SDK is unavailable
    or the API key is missing.
    """

    name = "critic"

    def __init__(
        self,
        model: str = CRITIC_MODEL,
        quality_threshold: float = QUALITY_PASS,
        timeout: int = 120,
    ) -> None:
        self.model              = model
        self.quality_threshold  = quality_threshold
        self.timeout            = timeout

    def run(
        self,
        task: dict,
        coder_result: Any,
        qa_result: Any,
        iteration: int = 0,
    ) -> AgentResult:
        """Review code + QA logs and return acceptance criteria + feedback."""
        # Collect code blocks
        code_blocks: list[str] = []
        if coder_result and coder_result.output:
            code_blocks = coder_result.output.get("code_blocks", [])

        if not code_blocks:
            c = self._criterion(
                "code_available",
                "Code must be available for review",
                passed=False,
                detail="No code blocks from Coder",
            )
            return AgentResult(
                agent=self.name,
                iteration=iteration,
                status="skip",
                criteria=[c],
                feedback="No code to review.",
            )

        qa_output = qa_result.output if (qa_result and qa_result.output) else None

        # ── Run analysis ──────────────────────────────────────────────────────
        analysis = self._analyze_with_llm(task, code_blocks, qa_output)

        # ── Acceptance criteria ───────────────────────────────────────────────
        issues    = analysis.get("issues", [])
        critical  = [i for i in issues if i.get("severity") == "critical"]
        schema_issues = [
            i for i in issues
            if i.get("category") in ("antipattern", "schema", "api_order")
            and i.get("severity") in ("critical", "warning")
        ]

        no_critical = self._criterion(
            "no_critical_antipatterns",
            "No critical Isaac Sim crash-inducing issues",
            passed=not bool(critical),
            detail=(
                " | ".join(i["description"] for i in critical[:2])
                if critical else "clean"
            ),
        )

        api_correct = self._criterion(
            "api_usage_correct",
            "APIs are used in the correct order and with correct arguments",
            passed=len(schema_issues) == 0,
            detail=(
                " | ".join(i["description"] for i in schema_issues[:2])
                if schema_issues else "OK"
            ),
        )

        quality_score = float(analysis.get("quality_score", 0.5))
        quality_ok = self._criterion(
            "code_quality",
            f"Quality score ≥ {self.quality_threshold:.0%}",
            passed=quality_score >= self.quality_threshold,
            score=quality_score,
            detail=f"score={quality_score:.2f} — {analysis.get('summary', '')[:120]}",
        )

        usd_issues = [
            i for i in issues
            if i.get("category") == "schema" and i.get("severity") == "critical"
        ]
        usd_ok = self._criterion(
            "usd_schema_compliance",
            "USD schema used correctly (Apply → access ordering, valid paths)",
            passed=not bool(usd_issues),
            detail=(
                " | ".join(i["description"] for i in usd_issues[:2])
                if usd_issues else "OK"
            ),
        )

        criteria = [no_critical, api_correct, quality_ok, usd_ok]

        # ── Build Coder feedback ──────────────────────────────────────────────
        fb_parts: list[str] = []
        for issue in issues:
            if issue.get("severity") in ("critical", "warning"):
                fb_parts.append(
                    f"[{issue['severity'].upper()}] {issue['description']}\n"
                    f"  Fix: {issue.get('fix', 'N/A')}"
                )
        if analysis.get("summary"):
            fb_parts.append(f"\nSummary: {analysis['summary']}")

        return self._result(
            iteration=iteration,
            criteria=criteria,
            output=analysis,
            feedback="\n".join(fb_parts),
        )

    # ── Analysis backends ─────────────────────────────────────────────────────

    def _analyze_with_llm(
        self,
        task: dict,
        code_blocks: list[str],
        qa_output: dict | None,
    ) -> dict:
        prompt = _build_critic_prompt(task, code_blocks, qa_output)
        try:
            resp = self.call_llm_api(self.model, prompt, system=_CRITIC_SYSTEM, max_tokens=2048, timeout=self.timeout)
            
            if resp.get("error"):
                raise RuntimeError(resp["error"])
                
            raw = resp.get("content", "")
            parsed = _parse_claude_response(raw)
            if parsed:
                return parsed
            # Model didn't return valid JSON — treat as low quality
            return {
                "quality_score": 0.5,
                "issues": [{"severity": "warning", "category": "style",
                            "description": "Model response was not valid JSON",
                            "fix": "N/A"}],
                "strengths": [],
                "summary": raw[:300],
            }
        except Exception as exc:
            return self._analyze_rule_based(task, code_blocks, qa_output, note=str(exc))

    def _analyze_rule_based(
        self,
        task: dict,
        code_blocks: list[str],
        qa_output: dict | None,
        note: str = "",
    ) -> dict:
        """
        Lightweight rule-based fallback when Claude is unavailable.
        Checks known Isaac Sim antipatterns + keyword coverage.
        """
        from .sim_harness import check_antipatterns, validate_syntax

        all_code = "\n\n".join(code_blocks)
        issues: list[dict] = []

        # Antipattern scan
        ap = check_antipatterns(all_code)
        for name, desc in ap.items():
            issues.append({
                "severity": "critical",
                "category": "antipattern",
                "description": desc,
                "fix": f"Remove the '{name}' pattern from your code",
            })

        # Syntax check
        for i, blk in enumerate(code_blocks):
            ok, err = validate_syntax(blk)
            if not ok:
                issues.append({
                    "severity": "critical",
                    "category": "schema",
                    "description": f"Block {i+1} has syntax error: {err}",
                    "fix": "Fix the syntax error",
                })

        # Keyword coverage
        required_kw  = task.get("required_keywords", [])
        full_lower    = all_code.lower()
        missing_kw    = [kw for kw in required_kw if kw.lower() not in full_lower]
        if missing_kw:
            issues.append({
                "severity": "warning",
                "category": "completeness",
                "description": f"Missing required keywords: {', '.join(missing_kw)}",
                "fix": f"Include these APIs in your code: {', '.join(missing_kw)}",
            })

        # QA errors → add as issues
        if qa_output:
            for sr in qa_output.get("sim_results", []):
                for err in sr.get("errors", []):
                    issues.append({
                        "severity": "critical",
                        "category": "api_order",
                        "description": f"Simulator error: {err[:200]}",
                        "fix": "Fix the runtime error shown above",
                    })

        critical_count = sum(1 for i in issues if i["severity"] == "critical")
        quality = max(0.0, 1.0 - critical_count * 0.2 - len(issues) * 0.05)
        if note:
            issues.append({
                "severity": "info",
                "category": "style",
                "description": f"Claude unavailable ({note[:80]}); rule-based analysis only",
                "fix": "N/A",
            })

        return {
            "quality_score": round(quality, 2),
            "issues": issues,
            "strengths": (
                ["Code contains required APIs", "Follows USD Python naming"]
                if not critical_count else []
            ),
            "summary": (
                f"Rule-based review: {critical_count} critical issue(s), "
                f"{len(issues)} total. quality={quality:.2f}"
            ),
        }
