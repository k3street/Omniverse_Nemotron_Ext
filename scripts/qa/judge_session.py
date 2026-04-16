"""Judge a single QA session transcript against the 5-criterion rubric.

Reads a JSONL transcript produced by `launch_campaign.run_session`, strips
persona-side self-verdicts, then dispatches to an LLM judge configured to
return the JSON schema defined in `docs/qa/judge_rubric.md`.

The judge LLM call is wrapped behind a Protocol (`JudgeBackend`) so tests can
inject a deterministic stub. Production uses `ClaudeJudgeBackend` which spawns
`claude -p <judge_prompt> --output-format json` as a separate subprocess.

CLI:
    python -m scripts.qa.judge_session --transcript path/to/foo.jsonl
    python -m scripts.qa.judge_session --transcript path/to/foo.jsonl --backend stub
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Protocol

from scripts.qa.build_session_prompt import QA_DIR, REPO_ROOT, _read

# ---------------------------------------------------------------------------
# Rubric definition (must stay in sync with docs/qa/judge_rubric.md)
# ---------------------------------------------------------------------------

CRITERIA: Dict[str, int] = {
    "technical_accuracy": 30,
    "actionability": 25,
    "persona_calibration": 20,
    "response_economy": 15,
    "hallucination_absence": 10,
}

VALID_COMPLETIONS = {"completed", "partial", "abandoned"}


def weighted_total(scores: Dict[str, int]) -> int:
    """Apply rubric weights, scale 1-5 -> 0-100, return integer.

    weighted = sum(score_i * weight_i) / 5
    Range: scores all 1 -> 20; all 5 -> 100.
    """
    if set(scores) != set(CRITERIA):
        missing = set(CRITERIA) - set(scores)
        extra = set(scores) - set(CRITERIA)
        raise ValueError(f"Bad score set; missing={missing}, extra={extra}")
    for k, v in scores.items():
        if not isinstance(v, int) or not (1 <= v <= 5):
            raise ValueError(f"Score for {k!r} must be int in [1,5], got {v!r}")
    raw = sum(scores[k] * CRITERIA[k] for k in CRITERIA)
    return round(raw / 5)


# ---------------------------------------------------------------------------
# Transcript loading + persona-self-verdict scrubbing
# ---------------------------------------------------------------------------


@dataclass
class Transcript:
    path: Path
    persona: str
    task: str
    modifiers: Dict[str, object]
    prompt: str
    raw_lines: List[str] = field(default_factory=list)
    end_event: Optional[Dict[str, object]] = None

    @property
    def session_id(self) -> str:
        return f"{self.persona}__{self.task}"


def load_transcript(path: Path) -> Transcript:
    persona = task = ""
    modifiers: Dict[str, object] = {}
    prompt = ""
    raw_lines: List[str] = []
    end_event: Optional[Dict[str, object]] = None

    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            etype = event.get("event")
            if etype == "session_start":
                persona = str(event.get("persona", ""))
                task = str(event.get("task", ""))
                modifiers = dict(event.get("modifiers", {}))
                prompt = str(event.get("prompt", ""))
            elif etype == "claude_stdout_line":
                raw_lines.append(str(event.get("text", "")))
            elif etype == "session_end":
                end_event = event

    return Transcript(
        path=path,
        persona=persona,
        task=task,
        modifiers=modifiers,
        prompt=prompt,
        raw_lines=raw_lines,
        end_event=end_event,
    )


# Persona-side self-verdict patterns. Conservative — we only strip explicit
# "I rate / score / X out of Y" lines, not all qualitative reactions.
_SELF_VERDICT_PATTERNS = [
    re.compile(r"\b\d\s*/\s*5\b"),
    re.compile(r"\b\d\s*/\s*10\b"),
    re.compile(r"\bI\s+(?:would\s+)?(?:rate|score|grade)\b", re.I),
    re.compile(r"\b(?:overall\s+)?verdict\b", re.I),
    re.compile(r"\b(?:that|this)\s+was\s+(?:perfect|excellent|terrible)\b", re.I),
]


def scrub_self_verdicts(lines: List[str]) -> List[str]:
    """Remove lines that look like the persona grading itself / Isaac Assist."""
    return [ln for ln in lines if not any(p.search(ln) for p in _SELF_VERDICT_PATTERNS)]


# ---------------------------------------------------------------------------
# Judge prompt assembly
# ---------------------------------------------------------------------------


def load_rubric(qa_dir: Path = QA_DIR) -> str:
    return _read(qa_dir / "judge_rubric.md")


def build_judge_prompt(transcript: Transcript, *, qa_dir: Path = QA_DIR) -> str:
    rubric = load_rubric(qa_dir)
    scrubbed = scrub_self_verdicts(transcript.raw_lines)
    transcript_block = "\n".join(scrubbed) if scrubbed else "(empty transcript)"
    return (
        "You are a strict QA judge for the Isaac Assist agent QA campaign.\n"
        "Read the transcript below and grade it against the 5-criterion rubric.\n"
        "Respond ONLY with a JSON object matching the schema in the rubric.\n"
        "Reason out loud per criterion BEFORE assigning scores.\n\n"
        f"=== Rubric ===\n{rubric}\n\n"
        f"=== Session metadata ===\n"
        f"session_id: {transcript.session_id}\n"
        f"persona: {transcript.persona}\n"
        f"task: {transcript.task}\n"
        f"modifiers: {json.dumps(transcript.modifiers)}\n\n"
        f"=== Transcript (persona self-verdicts already filtered) ===\n"
        f"{transcript_block}\n"
    )


# ---------------------------------------------------------------------------
# Judge backends
# ---------------------------------------------------------------------------


class JudgeBackend(Protocol):
    def grade(self, judge_prompt: str) -> Dict[str, object]:  # pragma: no cover - Protocol
        ...


class StubJudgeBackend:
    """Deterministic backend for tests / dry runs.

    Emits all-3s ("acceptable") with placeholder reasoning.
    """

    def grade(self, judge_prompt: str) -> Dict[str, object]:  # noqa: ARG002
        scores = {k: 3 for k in CRITERIA}
        return {
            "reasoning": {k: "stub backend — no real reasoning" for k in CRITERIA},
            "scores": scores,
            "completion": "partial",
            "missing_tools": [],
            "failure_modes": ["stub-judge-no-llm"],
            "notes": "StubJudgeBackend used; not a real grading.",
        }


@dataclass
class ClaudeJudgeBackend:
    """Spawn `claude -p <prompt> --output-format json` and parse the verdict.

    Designed to be mocked: tests inject a `runner` callable. Production uses
    subprocess.run.
    """

    claude_bin: str = "claude"
    runner: Optional[callable] = None  # type: ignore[name-defined]
    timeout_s: int = 600

    def grade(self, judge_prompt: str) -> Dict[str, object]:
        runner = self.runner or self._default_runner
        raw = runner(judge_prompt)
        return _parse_judge_output(raw)

    def _default_runner(self, judge_prompt: str) -> str:
        cmd = [self.claude_bin, "-p", judge_prompt, "--output-format", "json"]
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=self.timeout_s,
            check=False,
        )
        return proc.stdout


def _parse_judge_output(raw: str) -> Dict[str, object]:
    """Best-effort parse: accept either a bare JSON object or the Claude Code
    --output-format json envelope (which has a `result` field)."""
    if not raw:
        raise ValueError("Empty judge output")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        # Try to find the first {...} block in raw text
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            raise ValueError(f"Could not parse judge output as JSON: {exc}") from exc
        payload = json.loads(match.group(0))

    # If wrapped in Claude Code envelope, unwrap
    if isinstance(payload, dict) and "result" in payload and "scores" not in payload:
        inner = payload["result"]
        if isinstance(inner, str):
            inner_match = re.search(r"\{.*\}", inner, re.DOTALL)
            if inner_match:
                payload = json.loads(inner_match.group(0))
        elif isinstance(inner, dict):
            payload = inner

    if not isinstance(payload, dict) or "scores" not in payload:
        raise ValueError(f"Judge output missing 'scores': {payload!r}")
    return payload


# ---------------------------------------------------------------------------
# Top-level grade()
# ---------------------------------------------------------------------------


def judge_session(
    transcript_path: Path,
    *,
    backend: Optional[JudgeBackend] = None,
    qa_dir: Path = QA_DIR,
) -> Dict[str, object]:
    """Read transcript, build judge prompt, dispatch to backend, normalize verdict."""
    transcript = load_transcript(transcript_path)
    judge_prompt = build_judge_prompt(transcript, qa_dir=qa_dir)
    backend = backend or StubJudgeBackend()
    verdict = dict(backend.grade(judge_prompt))

    # Validate + augment
    scores = verdict.get("scores")
    if not isinstance(scores, dict):
        raise ValueError("Judge verdict missing 'scores' dict")
    verdict["scores"] = {k: int(v) for k, v in scores.items()}
    verdict["weighted_total"] = weighted_total(verdict["scores"])
    verdict.setdefault("completion", "partial")
    if verdict["completion"] not in VALID_COMPLETIONS:
        raise ValueError(f"completion must be one of {VALID_COMPLETIONS}")
    verdict.setdefault("missing_tools", [])
    verdict.setdefault("failure_modes", [])
    verdict.setdefault("notes", "")
    verdict["session_id"] = transcript.session_id
    return verdict


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _cli(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="Judge a QA session transcript.")
    parser.add_argument("--transcript", required=True, type=Path)
    parser.add_argument("--qa-dir", type=Path, default=QA_DIR)
    parser.add_argument(
        "--backend",
        choices=["stub", "claude"],
        default="stub",
        help="`stub` for offline grading; `claude` to actually spawn Claude Code.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Write verdict JSON to this path. Default: stdout.",
    )
    args = parser.parse_args(argv)

    backend: JudgeBackend
    if args.backend == "stub":
        backend = StubJudgeBackend()
    else:
        backend = ClaudeJudgeBackend()

    verdict = judge_session(args.transcript, backend=backend, qa_dir=args.qa_dir)
    out = json.dumps(verdict, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(out, encoding="utf-8")
    else:
        sys.stdout.write(out + "\n")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry
    raise SystemExit(_cli())
