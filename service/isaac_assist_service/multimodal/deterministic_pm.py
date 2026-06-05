"""Phase 46 — deterministic post-mortem enforcement.

When a workflow fails, the PM artifact must be produced by a
deterministic feature-extractor over the failure log, NOT a free-form
LLM generation. Prevents hallucinated root causes.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 46.
"""
from __future__ import annotations
from typing import Any, Dict, List


def deterministic_pm(failure_log: List[str]) -> Dict[str, Any]:
    """Extract error signatures from a failure log deterministically.

    Scans each log line for ``raise``, ``error``, or ``traceback`` keywords
    (case-insensitive) and collects up to 10 matching snippets.  No LLM is
    used; the result is fully reproducible given the same input.

    Args:
        failure_log (List[str]): Raw log lines from a failed workflow run.

    Returns:
        Dict[str, Any]: Keys ``signatures`` (list of up to 10 matched lines,
            truncated to 200 chars each), ``error_count`` (total matches found),
            and ``is_deterministic`` (always ``True``).
    """
    error_signatures = []
    for line in failure_log:
        line_lower = line.lower()
        if "raise" in line_lower or "error" in line_lower or "traceback" in line_lower:
            error_signatures.append(line[:200])
    return {
        "signatures": error_signatures[:10],
        "error_count": len(error_signatures),
        "is_deterministic": True,
    }
