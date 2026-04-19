"""
Shared data types and base class for all agents in the multi-agent loop.

Flow:
  ProjectManagerAgent
    └─► CoderAgent       (generates / repairs code)
    └─► QAAgent          (runs code against simulator, parses logs)
    └─► CriticAgent      (reviews code quality + execution output)
    └─► decision: pass all criteria → done, else feedback → Coder again
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Literal
import httpx
import time
from ...config import config


@dataclass
class Criterion:
    """A single acceptance criterion checked by an agent."""
    name: str
    description: str
    passed: bool  = False
    score: float  = 0.0   # 0.0–1.0
    detail: str   = ""    # human-readable evidence / error text

    def __str__(self) -> str:
        icon = "✓" if self.passed else "✗"
        tail = f" — {self.detail}" if self.detail else ""
        return f"{icon} [{self.name}] {self.description}{tail}"


@dataclass
class AgentResult:
    """
    The complete output from one agent's execution in a single iteration.

    Attributes:
        agent       : agent name (coder / qa / critic / pm)
        iteration   : 0-based loop counter
        status      : pass | fail | error | skip
        criteria    : list of checked Criterion objects
        output      : agent-specific payload (code str, analysis dict, …)
        logs        : raw log lines captured during this run
        feedback    : synthesised text to pass to the Coder in the next iteration
    """
    agent:      str
    iteration:  int
    status:     Literal["pass", "fail", "error", "skip"]
    criteria:   list[Criterion]       = field(default_factory=list)
    output:     Any                   = None
    logs:       list[str]             = field(default_factory=list)
    feedback:   str                   = ""

    # ── Convenience properties ────────────────────────────────────────────────

    @property
    def passed(self) -> bool:
        return self.status == "pass"

    @property
    def score(self) -> float:
        """Mean score across all criteria (0.0–1.0)."""
        if not self.criteria:
            return 1.0 if self.passed else 0.0
        return sum(c.score for c in self.criteria) / len(self.criteria)

    @property
    def failed_criteria(self) -> list[Criterion]:
        return [c for c in self.criteria if not c.passed]

    # ── Serialisation ─────────────────────────────────────────────────────────

    def summary(self) -> str:
        lines = [
            f"[{self.agent.upper()}] iter={self.iteration} "
            f"status={self.status} score={self.score:.2f}"
        ]
        for c in self.criteria:
            lines.append(f"  {c}")
        if self.feedback:
            lines.append(f"  → feedback: {self.feedback[:120]}…")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        # Serialize output: include code_blocks + latency for coder results;
        # truncate large strings so JSON stays readable.
        output_serial: Any = None
        if isinstance(self.output, dict):
            output_serial = {}
            for k, v in self.output.items():
                if k == "code_blocks" and isinstance(v, list):
                    output_serial[k] = [b[:2000] if isinstance(b, str) else b for b in v]
                elif isinstance(v, str) and len(v) > 2000:
                    output_serial[k] = v[:2000] + "…"
                else:
                    output_serial[k] = v
        elif isinstance(self.output, str):
            output_serial = self.output[:2000] + ("…" if len(self.output) > 2000 else "")

        return {
            "agent":     self.agent,
            "iteration": self.iteration,
            "status":    self.status,
            "score":     round(self.score, 3),
            "criteria":  [
                {
                    "name":        c.name,
                    "passed":      c.passed,
                    "score":       round(c.score, 3),
                    "description": c.description,
                    "detail":      c.detail,
                }
                for c in self.criteria
            ],
            "output":   output_serial,
            "feedback": self.feedback,
        }


# ── Base class ────────────────────────────────────────────────────────────────

class AgentBase(ABC):
    """Abstract base for all agents."""

    name: str = "agent"

    @abstractmethod
    def run(self, *args: Any, **kwargs: Any) -> AgentResult:
        ...

    def call_llm_api(self, model_tag: str, prompt: str, system: str, temperature: float = 0.2, max_tokens: int = 1500, timeout: int = 600) -> dict[str, Any]:
        """Calls the configured LLM provider — Anthropic, OpenAI, Grok, or Ollama."""
        mode = config.llm_mode.lower()

        if mode == "anthropic":
            return self._call_anthropic(model_tag, prompt, system, temperature, max_tokens, timeout)
        elif mode in ("openai", "grok"):
            return self._call_openai_compat(model_tag, prompt, system, temperature, max_tokens, timeout, mode)
        else:
            # Default: Ollama via OpenAI-compatible endpoint
            return self._call_openai_compat(model_tag, prompt, system, temperature, max_tokens, timeout, "ollama")

    def _call_anthropic(self, model_tag: str, prompt: str, system: str, temperature: float, max_tokens: int, timeout: int) -> dict[str, Any]:
        """Call Anthropic Claude Messages API with streaming."""
        import json

        api_key = config.api_key_anthropic
        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {
            "model": model_tag,
            "system": system,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "stream": True,
        }

        t0 = time.perf_counter()
        content_parts = []

        try:
            http_timeout = httpx.Timeout(connect=30.0, read=float(timeout), write=30.0, pool=30.0)
            with httpx.Client(timeout=http_timeout) as client:
                with client.stream("POST", "https://api.anthropic.com/v1/messages", json=payload, headers=headers) as r:
                    r.raise_for_status()
                    for line in r.iter_lines():
                        line = line.strip()
                        if not line or line.startswith("event:"):
                            continue
                        if line.startswith("data: "):
                            line = line[6:]
                        try:
                            chunk = json.loads(line)
                            if chunk.get("type") == "content_block_delta":
                                delta = chunk.get("delta", {})
                                if delta.get("type") == "text_delta":
                                    content_parts.append(delta["text"])
                        except Exception:
                            pass
        except httpx.TimeoutException:
            elapsed = time.perf_counter() - t0
            partial = "".join(content_parts)
            if partial:
                return {"content": partial, "latency_s": elapsed, "partial": True}
            return {"error": f"timeout after {elapsed:.0f}s", "content": "", "latency_s": elapsed}
        except Exception as exc:
            return {"error": str(exc), "content": "", "latency_s": 0.0}

        return {"content": "".join(content_parts), "latency_s": time.perf_counter() - t0}

    def _call_openai_compat(self, model_tag: str, prompt: str, system: str, temperature: float, max_tokens: int, timeout: int, mode: str) -> dict[str, Any]:
        """Call any OpenAI-compatible endpoint (OpenAI, Grok, Ollama) with streaming."""
        import json

        if mode == "openai":
            api_base = "https://api.openai.com/v1"
            api_key = config.api_key_openai
        elif mode == "grok":
            api_base = "https://api.x.ai/v1"
            api_key = config.api_key_grok
        else:
            api_base = getattr(config, "openai_api_base", "http://localhost:11434/v1")
            api_key = "ollama"
        
        payload = {
            "model": model_tag,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt}
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True
        }
        
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        
        t0 = time.perf_counter()
        content_parts = []
        
        try:
            http_timeout = httpx.Timeout(connect=30.0, read=float(timeout), write=30.0, pool=30.0)
            with httpx.Client(timeout=http_timeout) as client:
                with client.stream("POST", f"{api_base.rstrip('/')}/chat/completions", json=payload, headers=headers) as r:
                    r.raise_for_status()
                    for line in r.iter_lines():
                        line = line.strip()
                        if not line or line == "data: [DONE]":
                            continue
                        if line.startswith("data: "):
                            line = line[6:]
                        try:
                            chunk = json.loads(line)
                            choices = chunk.get("choices", [])
                            if choices:
                                delta = choices[0].get("delta", {})
                                if "content" in delta and delta["content"] is not None:
                                    content_parts.append(delta["content"])
                        except Exception:
                            pass
        except httpx.TimeoutException:
            elapsed = time.perf_counter() - t0
            partial = "".join(content_parts)
            if partial:
                return {"content": partial, "latency_s": elapsed, "partial": True}
            return {"error": f"timeout after {elapsed:.0f}s", "content": "", "latency_s": elapsed}
        except Exception as exc:
            return {"error": str(exc), "content": "", "latency_s": 0.0}

        return {"content": "".join(content_parts), "latency_s": time.perf_counter() - t0}

    # ── Helper factories ──────────────────────────────────────────────────────

    def _criterion(
        self,
        name: str,
        description: str,
        passed: bool,
        detail: str = "",
        score: float | None = None,
    ) -> Criterion:
        return Criterion(
            name=name,
            description=description,
            passed=passed,
            score=(1.0 if passed else 0.0) if score is None else score,
            detail=detail,
        )

    def _result(
        self,
        iteration: int,
        criteria: list[Criterion],
        output: Any = None,
        logs: list[str] | None = None,
        feedback: str = "",
    ) -> AgentResult:
        all_passed = all(c.passed for c in criteria)
        return AgentResult(
            agent=self.name,
            iteration=iteration,
            status="pass" if all_passed else "fail",
            criteria=criteria,
            output=output,
            logs=logs or [],
            feedback=feedback,
        )
