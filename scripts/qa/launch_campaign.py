"""Orchestrate a QA campaign: spawn a Claude Code subprocess per (persona, task) pair.

Each session writes a JSONL transcript to:
    workspace/qa_runs/<run_id>/<persona>__<task>.jsonl

The transcript file format is one JSON object per line:
    {"event": "session_start", "persona": "01_maya", "task": "M-01", "modifiers": {...}, "prompt": "..."}
    {"event": "claude_stdout_line", "text": "...", "ts": 1.23}
    {"event": "claude_stderr_line", "text": "...", "ts": 1.45}
    {"event": "session_end", "rc": 0, "duration_s": 92.4, "estimated_cost_usd": 0.18}

The launcher is mock-friendly: pass `--dry-run` to skip the actual subprocess and
just write the prompt + a stub session_end event. This is what the tests use.

CLI:
    python -m scripts.qa.launch_campaign --persona 01_maya --task M-01 --dry-run
    python -m scripts.qa.launch_campaign --plan plan.json --budget-usd 5
    python -m scripts.qa.launch_campaign --all --budget-usd 50

A "plan" is a JSON list of {persona, task} objects. Use --all to auto-build a
plan from every persona × every task on disk.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional

from scripts.qa.build_session_prompt import (
    Modifiers,
    QA_DIR,
    REPO_ROOT,
    build_session_prompt,
    random_modifiers,
)

# Default workspace location for transcripts
DEFAULT_RUNS_DIR = REPO_ROOT / "workspace" / "qa_runs"

# Hard ceilings — never run a session for longer than this
DEFAULT_SESSION_TIMEOUT_S = 1800  # 30 min
DEFAULT_BUDGET_USD = 50.0

# Crude cost estimate. Real values come from Claude Code's --output-format json.
COST_PER_SESSION_FALLBACK = 0.18


@dataclass(frozen=True)
class CampaignItem:
    persona: str
    task: str


@dataclass
class SessionResult:
    persona: str
    task: str
    transcript_path: Path
    rc: int
    duration_s: float
    estimated_cost_usd: float


# ---------------------------------------------------------------------------
# Plan construction
# ---------------------------------------------------------------------------


def list_personas(qa_dir: Path = QA_DIR) -> List[str]:
    return sorted(p.stem for p in (qa_dir / "personas").glob("*.md"))


def list_tasks(qa_dir: Path = QA_DIR) -> List[str]:
    return sorted(p.stem for p in (qa_dir / "tasks").glob("*.md"))


def build_full_plan(qa_dir: Path = QA_DIR) -> List[CampaignItem]:
    """Cartesian product of all personas × all tasks. The launcher will need a
    `task_applies_to_persona()` filter for the full 243-task library, but at
    sample size we keep it simple."""
    personas = list_personas(qa_dir)
    tasks = list_tasks(qa_dir)
    return [CampaignItem(persona=p, task=t) for p in personas for t in tasks]


def load_plan(path: Path) -> List[CampaignItem]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"Plan file must contain a JSON array, got {type(data).__name__}")
    items: List[CampaignItem] = []
    for entry in data:
        if not isinstance(entry, dict) or "persona" not in entry or "task" not in entry:
            raise ValueError(f"Bad plan entry: {entry!r}")
        items.append(CampaignItem(persona=entry["persona"], task=entry["task"]))
    return items


# ---------------------------------------------------------------------------
# Cost estimation
# ---------------------------------------------------------------------------


def estimate_cost(claude_stdout_json: str) -> float:
    """Best-effort cost extraction from Claude Code's --output-format json blob."""
    try:
        payload = json.loads(claude_stdout_json)
    except (json.JSONDecodeError, TypeError):
        return COST_PER_SESSION_FALLBACK

    # Claude Code emits "total_cost_usd" or "cost_usd" depending on version
    for key in ("total_cost_usd", "cost_usd", "cost"):
        if isinstance(payload, dict) and key in payload:
            try:
                return float(payload[key])
            except (TypeError, ValueError):
                continue
    return COST_PER_SESSION_FALLBACK


# ---------------------------------------------------------------------------
# Single-session execution
# ---------------------------------------------------------------------------


def _write_jsonl(path: Path, event: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, ensure_ascii=False))
        fh.write("\n")


def run_session(
    item: CampaignItem,
    run_dir: Path,
    *,
    modifiers: Optional[Modifiers] = None,
    dry_run: bool = False,
    timeout_s: int = DEFAULT_SESSION_TIMEOUT_S,
    claude_bin: str = "claude",
    qa_dir: Path = QA_DIR,
    rng: Optional[random.Random] = None,
) -> SessionResult:
    """Run a single (persona, task) Claude Code session.

    `dry_run=True` skips the subprocess entirely; useful for tests and pilots.
    """
    mods = modifiers or random_modifiers(item.persona, rng=rng)
    prompt = build_session_prompt(item.persona, item.task, modifiers=mods, qa_dir=qa_dir)

    transcript_path = run_dir / f"{item.persona}__{item.task}.jsonl"
    # Truncate any prior transcript for this pair in this run
    if transcript_path.exists():
        transcript_path.unlink()

    _write_jsonl(
        transcript_path,
        {
            "event": "session_start",
            "persona": item.persona,
            "task": item.task,
            "modifiers": mods.as_dict(),
            "prompt": prompt,
            "ts": time.time(),
        },
    )

    start = time.time()

    if dry_run:
        rc = 0
        cost = 0.0
        _write_jsonl(
            transcript_path,
            {
                "event": "claude_stdout_line",
                "text": "[dry-run] prompt assembled but Claude Code not spawned",
                "ts": time.time() - start,
            },
        )
    else:
        cmd = [claude_bin, "-p", prompt, "--output-format", "json"]
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout_s,
                check=False,
            )
            rc = proc.returncode
            cost = estimate_cost(proc.stdout)
            for line in (proc.stdout or "").splitlines():
                _write_jsonl(
                    transcript_path,
                    {"event": "claude_stdout_line", "text": line, "ts": time.time() - start},
                )
            for line in (proc.stderr or "").splitlines():
                _write_jsonl(
                    transcript_path,
                    {"event": "claude_stderr_line", "text": line, "ts": time.time() - start},
                )
        except subprocess.TimeoutExpired:
            rc = 124
            cost = COST_PER_SESSION_FALLBACK
            _write_jsonl(
                transcript_path,
                {
                    "event": "claude_stderr_line",
                    "text": f"[launcher] session timed out after {timeout_s}s",
                    "ts": time.time() - start,
                },
            )
        except FileNotFoundError as exc:
            rc = 127
            cost = 0.0
            _write_jsonl(
                transcript_path,
                {
                    "event": "claude_stderr_line",
                    "text": f"[launcher] {exc}",
                    "ts": time.time() - start,
                },
            )

    duration = time.time() - start
    _write_jsonl(
        transcript_path,
        {
            "event": "session_end",
            "rc": rc,
            "duration_s": duration,
            "estimated_cost_usd": cost,
            "ts": time.time(),
        },
    )

    return SessionResult(
        persona=item.persona,
        task=item.task,
        transcript_path=transcript_path,
        rc=rc,
        duration_s=duration,
        estimated_cost_usd=cost,
    )


# ---------------------------------------------------------------------------
# Campaign loop
# ---------------------------------------------------------------------------


def run_campaign(
    plan: Iterable[CampaignItem],
    *,
    runs_dir: Path = DEFAULT_RUNS_DIR,
    run_id: Optional[str] = None,
    budget_usd: float = DEFAULT_BUDGET_USD,
    dry_run: bool = False,
    timeout_s: int = DEFAULT_SESSION_TIMEOUT_S,
    claude_bin: str = "claude",
    qa_dir: Path = QA_DIR,
    rng: Optional[random.Random] = None,
) -> List[SessionResult]:
    """Run a campaign sequentially. Stops as soon as cumulative cost would exceed
    `budget_usd`. Real-world parallelism would wrap this in a thread/process pool;
    we keep it sequential here so transcripts and budget accounting stay clean."""
    run_id = run_id or time.strftime("run_%Y%m%dT%H%M%S") + "_" + uuid.uuid4().hex[:6]
    run_dir = runs_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    spent = 0.0
    results: List[SessionResult] = []
    for item in plan:
        if spent >= budget_usd:
            break
        result = run_session(
            item,
            run_dir,
            dry_run=dry_run,
            timeout_s=timeout_s,
            claude_bin=claude_bin,
            qa_dir=qa_dir,
            rng=rng,
        )
        results.append(result)
        spent += result.estimated_cost_usd

    # Write a manifest for the campaign
    manifest = {
        "run_id": run_id,
        "started_at": time.time(),
        "budget_usd": budget_usd,
        "spent_usd": spent,
        "session_count": len(results),
        "dry_run": dry_run,
        "sessions": [
            {
                "persona": r.persona,
                "task": r.task,
                "transcript": r.transcript_path.name,
                "rc": r.rc,
                "duration_s": r.duration_s,
                "estimated_cost_usd": r.estimated_cost_usd,
            }
            for r in results
        ],
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _cli(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Launch a QA campaign: spawn a Claude Code session per (persona, task)."
    )
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--persona", help="Run a single session for this persona id")
    src.add_argument("--plan", type=Path, help="JSON file: list of {persona, task}")
    src.add_argument("--all", action="store_true", help="Cartesian product of all personas × all tasks")

    parser.add_argument("--task", help="Required when --persona is set")
    parser.add_argument("--budget-usd", type=float, default=DEFAULT_BUDGET_USD)
    parser.add_argument("--timeout-s", type=int, default=DEFAULT_SESSION_TIMEOUT_S)
    parser.add_argument("--dry-run", action="store_true", help="Skip subprocess, write prompt only")
    parser.add_argument("--runs-dir", type=Path, default=DEFAULT_RUNS_DIR)
    parser.add_argument("--qa-dir", type=Path, default=QA_DIR)
    parser.add_argument("--claude-bin", default=os.environ.get("CLAUDE_BIN", "claude"))
    parser.add_argument("--seed", type=int, default=None)
    args = parser.parse_args(argv)

    if args.persona and not args.task:
        parser.error("--task is required when --persona is set")

    if args.persona:
        plan = [CampaignItem(persona=args.persona, task=args.task)]
    elif args.plan:
        plan = load_plan(args.plan)
    else:
        plan = build_full_plan(args.qa_dir)

    rng = random.Random(args.seed) if args.seed is not None else None
    results = run_campaign(
        plan,
        runs_dir=args.runs_dir,
        budget_usd=args.budget_usd,
        dry_run=args.dry_run,
        timeout_s=args.timeout_s,
        claude_bin=args.claude_bin,
        qa_dir=args.qa_dir,
        rng=rng,
    )

    sys.stdout.write(
        json.dumps(
            {
                "session_count": len(results),
                "spent_usd": sum(r.estimated_cost_usd for r in results),
                "transcripts": [str(r.transcript_path) for r in results],
            },
            indent=2,
        )
        + "\n"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry
    raise SystemExit(_cli())
