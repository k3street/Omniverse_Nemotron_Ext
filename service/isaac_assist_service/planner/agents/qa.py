"""
QAAgent — runs each code block against the simulator and monitors logs.

Acceptance criteria:
  no_traceback      No Python exception traceback in stdout/stderr
  no_physx_errors   No [Error] / PhysX error lines in logs
  no_segfault       Process did not crash with SIGSEGV / core dump
  clean_exit        All code blocks exited with returncode 0
  within_timeout    All blocks completed before the timeout deadline
  no_antipatterns   No Isaac Sim crash-inducing patterns in code

For each block the agent also collects:
  - execution mode (usd_python / mock_omni / python_only / isaac_sim)
  - full stdout/stderr for the Critic and PM to inspect
"""

from __future__ import annotations

import textwrap
from typing import Any

from .base import AgentBase, AgentResult, Criterion
from .sim_harness import SimResult, extract_code_blocks, run_all_blocks


class QAAgent(AgentBase):
    """
    Runs generated code in an isolated subprocess and parses simulator logs.

    Parameters
    ----------
    sim_mode : str
        Execution mode passed to sim_harness.run_code.
        "auto" lets the harness pick based on detected imports.
    timeout : int
        Per-block subprocess timeout in seconds.
    """

    name = "qa"

    def __init__(
        self,
        sim_mode: str = "auto",
        timeout: int = 45,
    ) -> None:
        self.sim_mode = sim_mode
        self.timeout  = timeout

    def run(
        self,
        coder_result: Any,  # AgentResult from CoderAgent
        iteration: int = 0,
    ) -> AgentResult:
        """
        Extract code blocks from coder output, run each in the simulator,
        and aggregate results into acceptance criteria.
        """
        # Skip only if there is no code at all to execute.
        # Deliberate: QA runs even when Coder has failing criteria (e.g.
        # antipattern warnings) so we always get real simulator feedback.
        payload       = (coder_result.output or {}) if coder_result else {}
        code_blocks   = payload.get("code_blocks", [])
        full_response = payload.get("response", "")

        # Fallback: try extracting from full response text
        if not code_blocks:
            code_blocks = extract_code_blocks(full_response)

        if coder_result is None or not code_blocks:
            c = self._criterion(
                "coder_prerequisite",
                "Coder must produce at least one code block for QA to run",
                passed=False,
                detail="No code blocks found in coder output" if coder_result else "Coder result is None",
            )
            return AgentResult(
                agent=self.name,
                iteration=iteration,
                status="skip",
                criteria=[c],
                feedback="Coder agent must produce at least one ```python``` block.",
            )

        if not code_blocks:
            c = self._criterion(
                "code_available",
                "At least one code block available to run",
                passed=False,
                detail="No code blocks found in coder output",
            )
            return self._result(
                iteration=iteration,
                criteria=[c],
                feedback="No ```python``` blocks found — Coder must produce runnable code.",
            )

        # ── Run every code block ──────────────────────────────────────────────
        sim_results: list[tuple[str, SimResult]] = []
        for code in code_blocks:
            sr = self._run_block(code)
            sim_results.append((code, sr))

        # ── Aggregate across all blocks ───────────────────────────────────────
        any_timeout    = any(sr.timed_out              for _, sr in sim_results)
        any_traceback  = any(sr.has_traceback          for _, sr in sim_results)
        any_physx      = any(sr.has_physx_error        for _, sr in sim_results)
        any_segfault   = any(sr.has_segfault           for _, sr in sim_results)
        any_bad_exit   = any(sr.returncode != 0        for _, sr in sim_results)
        any_antipattern = any(sr.antipatterns_found    for _, sr in sim_results)

        all_errors   = []
        all_warnings = []
        for _, sr in sim_results:
            all_errors.extend(sr.errors)
            all_warnings.extend(sr.warnings)

        exec_modes = list({sr.execution_mode for _, sr in sim_results})

        # ── Acceptance criteria ───────────────────────────────────────────────
        no_tb = self._criterion(
            "no_traceback",
            "No Python exception traceback in any block",
            passed=not any_traceback,
            detail=_first_tb(sim_results) if any_traceback else "clean",
        )
        no_physx = self._criterion(
            "no_physx_errors",
            "No PhysX / Omniverse [Error] lines in logs",
            passed=not any_physx,
            detail="; ".join(all_errors[:3]) if all_errors else "clean",
        )
        no_seg = self._criterion(
            "no_segfault",
            "No segfault / core dump",
            passed=not any_segfault,
            detail="SIGSEGV detected" if any_segfault else "clean",
        )
        clean_exit = self._criterion(
            "clean_exit",
            "All blocks exited with returncode 0",
            passed=not any_bad_exit,
            detail=(
                "non-zero exit codes: "
                + ", ".join(str(sr.returncode) for _, sr in sim_results if sr.returncode != 0)
                if any_bad_exit else "OK"
            ),
        )
        no_timeout = self._criterion(
            "within_timeout",
            f"All blocks completed within {self.timeout}s",
            passed=not any_timeout,
            detail=f"Timeout exceeded ({self.timeout}s)" if any_timeout else "OK",
        )
        no_ap = self._criterion(
            "no_antipatterns",
            "No Isaac Sim crash-inducing antipatterns",
            passed=not any_antipattern,
            detail=(
                "; ".join(
                    f"{n}: {d}"
                    for _, sr in sim_results
                    for n, d in sr.antipatterns_found.items()
                )
                if any_antipattern else "clean"
            ),
        )

        criteria = [no_tb, no_physx, no_seg, clean_exit, no_timeout, no_ap]

        # ── Build feedback for Coder ──────────────────────────────────────────
        fb_parts: list[str] = []
        for i, (code, sr) in enumerate(sim_results):
            if not sr.passed:
                fb_parts.append(
                    f"--- Block {i+1} (mode={sr.execution_mode}) ---\n"
                    + sr.as_log_block()
                )
        if all_warnings:
            fb_parts.append(
                "Warnings (investigate):\n"
                + "\n".join(f"  {w}" for w in all_warnings[:5])
            )

        logs = []
        for _, sr in sim_results:
            logs.extend(sr.errors)
            logs.extend(sr.warnings)

        return self._result(
            iteration=iteration,
            criteria=criteria,
            output={
                "sim_results": [
                    {
                        "block_index":  i,
                        "mode":         sr.execution_mode,
                        "passed":       sr.passed,
                        "returncode":   sr.returncode,
                        "duration_s":   sr.duration_s,
                        "errors":       sr.errors,
                        "warnings":     sr.warnings,
                        "has_traceback": sr.has_traceback,
                        "stdout":       sr.stdout[:2000],
                        "stderr":       sr.stderr[:2000],
                        "antipatterns": sr.antipatterns_found,
                    }
                    for i, (_, sr) in enumerate(sim_results)
                ],
                "exec_modes": exec_modes,
                "num_blocks": len(sim_results),
            },
            logs=logs,
            feedback="\n\n".join(fb_parts) if fb_parts else "",
        )

    def _run_block(self, code: str) -> SimResult:
        from .sim_harness import run_code
        return run_code(code, mode=self.sim_mode, timeout=self.timeout)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _first_tb(sim_results: list[tuple[str, SimResult]]) -> str:
    """Extract the first traceback found across all sim results."""
    for _, sr in sim_results:
        combined = sr.stdout + "\n" + sr.stderr
        lines    = combined.splitlines()
        tb: list[str] = []
        in_tb = False
        for line in lines:
            if "Traceback" in line:
                in_tb = True
            if in_tb:
                tb.append(line)
                if len(tb) > 20:
                    break
        if tb:
            return textwrap.shorten("\n".join(tb), width=400, placeholder="…")
    return ""
