"""
CoderAgent — generates and repairs code using the local isaac-expert Ollama model.

Acceptance criteria:
  syntax_valid          At least one code block is syntactically valid Python
  has_code_block        Response contains at least one fenced code block
  required_keywords     ≥ 60 % of task required_keywords present in the code
  no_fatal_antipatterns No Isaac Sim crash-inducing antipatterns detected

On iterations > 0, the prompt is augmented with QA error logs and Critic
feedback so the model can self-repair.
"""

from __future__ import annotations

import re
import time
from typing import Any

from .base import AgentBase, AgentResult, Criterion
from .sim_harness import (
    check_antipatterns,
    extract_code_blocks,
    validate_syntax,
)

_THINK_RE   = re.compile(r"<think>.*?</think>", re.DOTALL)

SYSTEM_PROMPT = """\
You are an expert NVIDIA robotics and simulation engineer with deep knowledge of:
- Isaac Lab (ManagerBasedRLEnv, managers, scene configs, @configclass)
- Isaac Sim / Omniverse Kit extensions (omni.isaac.core, omni.kit.commands)
- OpenUSD Python API (pxr, UsdPhysics, UsdGeom, Sdf, Gf)
- PhysX via USD schema (UsdPhysics.RigidBodyAPI, DriveAPI, ArticulationRootAPI)
- Newton Physics and Warp

Rules:
1. Always write complete, runnable Python code in ```python fenced blocks.
2. Use UsdPhysics Python API names (not raw C++ PxScene / PxRigidDynamic names).
3. Apply RigidBodyAPI only to the topmost prim — never nested.
4. Call .Apply(prim) before accessing physics schema attributes.
5. Use absolute USD paths (/World/Robot/base_link) not relative.
6. When asked to fix code, output the COMPLETE corrected code, not diffs.
7. [DYNAMIC DOC SEARCH]: If you are unsure of the API syntax for Isaac Sim 5.1.0, you are AUTHORIZED to write an exploratory web-scraping script. Use `urllib.request` and `html.parser` to fetch `https://docs.isaacsim.omniverse.nvidia.com/5.1.0/py/index.html` (or its subdirectories), search for the target string, and `print()` the results. Your script will run locally in Omniverse and its `stdout` trace will automatically be routed back to your next prompt!
"""

def _strip_thinking(text: str) -> str:
    return _THINK_RE.sub("", text).strip()


def _build_prompt(
    task: dict,
    iteration: int,
    qa_feedback: str = "",
    critic_feedback: str = "",
) -> str:
    """Build a Coder prompt, injecting prior-iteration error feedback."""
    task_section = (
        f"## Task: {task.get('id', 'unknown')}\n"
        f"Category: {task.get('category', '?')} | "
        f"Difficulty: {task.get('difficulty', '?')}\n\n"
        f"{task['prompt']}\n"
    )

    api_hint = ""
    if task.get("reference_apis"):
        api_hint = "\nRequired APIs: " + ", ".join(task["reference_apis"]) + "\n"

    kw_hint = ""
    if task.get("required_keywords"):
        kw_hint = "\nMust include these in your response: " + ", ".join(task["required_keywords"]) + "\n"

    if iteration == 0:
        return task_section + api_hint + kw_hint

    # Iteration > 0: include prior-run errors
    repair_header = (
        f"\n## ⚠ Iteration {iteration} — Previous attempt had errors. FIX ALL OF THEM.\n"
    )
    qa_section = ""
    if qa_feedback:
        qa_section = f"\n### QA Simulator Errors (you MUST fix these):\n```\n{qa_feedback}\n```\n"

    critic_section = ""
    if critic_feedback:
        critic_section = f"\n### Critic Review (address all issues):\n{critic_feedback}\n"

    return (
        task_section
        + api_hint
        + kw_hint
        + repair_header
        + qa_section
        + critic_section
        + "\nRewrite the COMPLETE code fixing every issue above.\n"
    )


class CoderAgent(AgentBase):
    """
    Uses the local Ollama model (isaac-expert) to generate or repair code.

    Acceptance criteria (all must pass):
      - has_code_block        : response contains ≥1 fenced code block
      - syntax_valid          : at least one block has valid Python syntax
      - required_keywords     : ≥60 % of task keywords present
      - no_fatal_antipatterns : no crash-inducing Isaac Sim patterns
    """

    name = "coder"

    def __init__(
        self,
        model_tag: str = "isaac-expert:v0.1.0",
        keyword_threshold: float = 0.60,
        timeout: int = 300,
    ) -> None:
        self.model_tag          = model_tag
        self.keyword_threshold  = keyword_threshold
        self.timeout            = timeout

    def run(
        self,
        task: dict,
        iteration: int = 0,
        qa_feedback: str = "",
        critic_feedback: str = "",
    ) -> AgentResult:
        """Generate code for the task, optionally repairing from prior errors."""
        prompt = _build_prompt(task, iteration, qa_feedback, critic_feedback)
        resp   = self.call_llm_api(self.model_tag, prompt, system=SYSTEM_PROMPT, max_tokens=1500, timeout=self.timeout)

        if resp.get("error"):
            c = self._criterion(
                "model_available",
                "Ollama model responded without error",
                passed=False,
                detail=str(resp["error"]),
            )
            return AgentResult(
                agent=self.name,
                iteration=iteration,
                status="error",
                criteria=[c],
                output=None,
                feedback=f"Model error: {resp['error']}",
            )

        raw_text    = resp.get("content", "")
        clean_text  = _strip_thinking(raw_text)
        code_blocks = extract_code_blocks(clean_text)
        all_code    = "\n\n".join(code_blocks)

        # ── Criteria ──────────────────────────────────────────────────────────

        # 1. has_code_block
        has_block = self._criterion(
            "has_code_block",
            "Response contains at least one ```python``` block",
            passed=bool(code_blocks),
            detail=f"{len(code_blocks)} block(s) found",
        )

        # 2. syntax_valid (check each block; pass if at least one is valid)
        syntax_errors: list[str] = []
        at_least_one_valid = False
        for blk in code_blocks:
            ok, err = validate_syntax(blk)
            if ok:
                at_least_one_valid = True
            else:
                syntax_errors.append(err)

        syntax_ok = self._criterion(
            "syntax_valid",
            "At least one code block has valid Python syntax",
            passed=at_least_one_valid,
            detail="; ".join(syntax_errors) if syntax_errors else "OK",
        )

        # 3. required_keywords coverage (in combined code + text)
        required_kw = task.get("required_keywords", [])
        if required_kw:
            full_lower = (clean_text + all_code).lower()
            hits = [kw for kw in required_kw if kw.lower() in full_lower]
            ratio = len(hits) / len(required_kw)
            missed = [kw for kw in required_kw if kw.lower() not in full_lower]
            kw_ok = self._criterion(
                "required_keywords",
                f"≥{int(self.keyword_threshold*100)}% of required keywords present",
                passed=ratio >= self.keyword_threshold,
                score=ratio,
                detail=(
                    f"{len(hits)}/{len(required_kw)} present"
                    + (f" — missing: {', '.join(missed)}" if missed else "")
                ),
            )
        else:
            kw_ok = self._criterion(
                "required_keywords", "No keywords required", passed=True, detail="N/A"
            )

        # 4. no_fatal_antipatterns
        ap = check_antipatterns(all_code)
        ap_ok = self._criterion(
            "no_fatal_antipatterns",
            "No Isaac Sim crash-inducing antipatterns",
            passed=not bool(ap),
            detail="; ".join(f"{n}: {d}" for n, d in ap.items()) if ap else "clean",
        )

        criteria   = [has_block, syntax_ok, kw_ok, ap_ok]
        all_passed = all(c.passed for c in criteria)

        # ── Feedback for next iteration ───────────────────────────────────────
        fb_parts: list[str] = []
        if not has_block.passed:
            fb_parts.append("Your response contained no fenced ```python``` code blocks.")
        if not syntax_ok.passed:
            fb_parts.append("Syntax errors found:\n" + "\n".join(syntax_errors))
        if not kw_ok.passed:
            fb_parts.append(f"Missing required keywords: {kw_ok.detail}")
        if not ap_ok.passed:
            fb_parts.append(f"Antipatterns to fix: {ap_ok.detail}")

        return self._result(
            iteration=iteration,
            criteria=criteria,
            output={
                "response":     clean_text,
                "code_blocks":  code_blocks,
                "latency_s":    round(resp.get("latency_s", 0), 2),
            },
            feedback="\n".join(fb_parts),
        )
