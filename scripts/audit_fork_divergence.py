#!/usr/bin/env python3
"""Phase 0b — Fork reconciliation audit.

Compares anton/feat/multimodal-foundation (working branch) against
origin/master (k3street's fork) and classifies every divergent commit
into one of five buckets: adopt | reject | defer | merged | unknown.

The output is *advisory* — no commits are auto-cherry-picked. The user
reviews the report and decides which features to actually port. The next
run incorporates the user's classification as new classifier rules.

Usage:
    python scripts/audit_fork_divergence.py [--out PATH]
                                            [--base REF] [--head REF]

Per IA_FULL_SPEC_2026-05-10.md Phase 0b.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Sequence

# ---------------------------------------------------------------------------
# Classification

# Verdict buckets — ordered for report sorting (most-actionable first).
VERDICTS = ("adopt", "defer", "unknown", "merged", "reject")

# Subject-keyword → verdict rules. Order matters: first match wins.
# Source: IA_FULL_SPEC Phase 0b's example feature list + build sketch.
_SUBJECT_RULES: list[tuple[str, str]] = [
    # Adopt — known IA-shaped extensions worth porting
    ("deploy_rl_policy", "adopt"),
    ("preflight_check", "adopt"),
    ("preflight check", "adopt"),
    ("isaac_ros_image_pipeline", "adopt"),
    ("rviz2", "adopt"),
    ("rtx lidar", "adopt"),
    ("sceneworkspace", "adopt"),
    ("scene workspace", "adopt"),
    ("multi-provider vision", "adopt"),
    ("ollama", "adopt"),
    ("robot motion diagnostic", "adopt"),
    # Defer — vendor-specific or lower-priority work
    ("lingbot", "defer"),
    ("mediapipe", "defer"),
    ("ira actor", "defer"),
    ("ira_actor", "defer"),
    ("cloud-llm agent-swarm", "defer"),
    ("agent-swarm", "defer"),
]


def classify(commit_subject: str, diff_files: Sequence[str]) -> str:
    """Five-way classifier — returns one of {adopt, reject, defer, merged, unknown}.

    Pure function — no I/O. Order:
    1. Only-spec changes → merged (spec edits land via PR).
    2. Subject-keyword rules in _SUBJECT_RULES.
    3. Fallback → unknown (human review).
    """
    subj = commit_subject.lower()

    # Rule 1: only-spec changes are merged (i.e. already accounted for).
    if diff_files and all(f.startswith("specs/") for f in diff_files):
        return "merged"

    # Rule 2: subject-keyword classifier.
    for keyword, verdict in _SUBJECT_RULES:
        if keyword in subj:
            return verdict

    # Rule 3: fallback.
    return "unknown"


# ---------------------------------------------------------------------------
# Git parsing


@dataclass
class Commit:
    sha: str
    subject: str
    author: str
    date: str
    files: list[str] = field(default_factory=list)
    verdict: str = "unknown"


# Delimiters that are extremely unlikely to appear in commit subjects.
_REC_SEP = "\x1e"  # ASCII record separator
_FIELD_SEP = "\x1f"  # ASCII unit separator


def parse_git_log(base: str, head: str) -> List[Commit]:
    """Run `git log base..head` and return parsed commits with file lists.

    Format design: the record separator is at the START of each commit
    block, NOT the end. With `--name-only` the file list follows the
    formatted header on subsequent lines; a trailing separator marks the
    boundary between header and files instead of between commits, which
    fragments the parse. A leading separator gives us clean "one chunk =
    one commit" semantics after `split(_REC_SEP)`.
    """
    fmt = _REC_SEP + _FIELD_SEP.join(("%H", "%s", "%an", "%ad"))
    cmd = [
        "git",
        "log",
        f"{base}..{head}",
        f"--format={fmt}",
        "--name-only",
        "--date=short",
    ]
    raw = subprocess.run(
        cmd, capture_output=True, text=True, check=True
    ).stdout

    commits: List[Commit] = []
    for record in raw.split(_REC_SEP):
        record = record.strip()
        if not record:
            continue
        header, *file_lines = record.split("\n")
        parts = header.split(_FIELD_SEP)
        if len(parts) != 4:
            # Defensive — malformed record, skip
            continue
        sha, subject, author, date = parts
        files = [f.strip() for f in file_lines if f.strip()]
        commits.append(
            Commit(sha=sha, subject=subject, author=author, date=date, files=files)
        )
    return commits


# ---------------------------------------------------------------------------
# Report rendering


_PHASE_LINK_HINT = {
    "deploy_rl_policy": "evaluate as Phase 79b sibling (RL policy deploy path)",
    "preflight_check": "evaluate against Phase 7b / scene-feasibility coverage",
    "isaac_ros_image_pipeline": "evaluate against Phase 7 (ros2 themed module)",
    "rviz2": "evaluate against Phase 7 / 7b ROS2 split",
    "rtx lidar": "evaluate against Phase 73 sensor catalog",
    "sceneworkspace": "evaluate against Phase 26 (CAS-versioned LayoutSpec history)",
    "multi-provider vision": "evaluate against Phase 76 (vision tool handlers)",
    "ollama": "evaluate against Phase 76 / multi-provider plumbing",
    "robot motion diagnostic": "evaluate against Phase 63c (per-robot cuRobo debug)",
    "lingbot": "vendor-specific; out of scope for current epoch",
    "mediapipe": "evaluate against Phase 79b (locomanip teleop)",
    "ira actor": "evaluate against teleop track (Phase 79b)",
    "cloud-llm agent-swarm": "evaluate against future agent-orchestration work",
}


def _phase_link(subject: str) -> str:
    sl = subject.lower()
    for keyword, hint in _PHASE_LINK_HINT.items():
        if keyword in sl:
            return hint
    return ""


def render_markdown(commits: Sequence[Commit], base: str, head: str) -> str:
    """Render the audit report sorted by verdict, then by date desc."""
    by_verdict: dict[str, list[Commit]] = {v: [] for v in VERDICTS}
    for c in commits:
        by_verdict.setdefault(c.verdict, []).append(c)
    for v in by_verdict:
        by_verdict[v].sort(key=lambda c: c.date, reverse=True)

    counts = Counter(c.verdict for c in commits)
    now = _dt.date.today().isoformat()

    lines: list[str] = []
    lines.append(f"# Fork Divergence Audit — {now}")
    lines.append("")
    lines.append(f"**Base (working branch):** `{base}`")
    lines.append(f"**Head (k3street fork):** `{head}`")
    lines.append(f"**Total divergent commits:** {len(commits)}")
    lines.append("")
    lines.append("**Verdict counts:**")
    for v in VERDICTS:
        lines.append(f"- `{v}`: {counts.get(v, 0)}")
    lines.append("")
    lines.append(
        "**Advisory only.** No commits are auto-cherry-picked. Review each "
        "row; promote `unknown` rows to one of the four other verdicts; "
        "incorporate stable patterns back into `_SUBJECT_RULES` in "
        "`scripts/audit_fork_divergence.py` for next run."
    )
    lines.append("")

    for v in VERDICTS:
        rows = by_verdict.get(v, [])
        if not rows:
            continue
        lines.append(f"## `{v}` ({len(rows)})")
        lines.append("")
        lines.append("| sha | date | subject | hint |")
        lines.append("|---|---|---|---|")
        for c in rows:
            hint = _phase_link(c.subject)
            subj = c.subject.replace("|", "\\|")
            lines.append(
                f"| `{c.sha[:8]}` | {c.date} | {subj} | {hint} |"
            )
        lines.append("")

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# CLI


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base",
        default="anton/feat/multimodal-foundation",
        help="Base ref (default: anton/feat/multimodal-foundation)",
    )
    parser.add_argument(
        "--head",
        default="origin/master",
        help="Head ref to diff against base (default: origin/master)",
    )
    parser.add_argument(
        "--out",
        default=None,
        help=(
            "Output path. Default: docs/audits/fork_divergence_{today}.md"
        ),
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    try:
        commits = parse_git_log(args.base, args.head)
    except subprocess.CalledProcessError as exc:
        print(f"ERROR: git log failed: {exc.stderr}", file=sys.stderr)
        return 2

    for c in commits:
        c.verdict = classify(c.subject, c.files)

    out_path = (
        Path(args.out)
        if args.out
        else Path("docs/audits") / f"fork_divergence_{_dt.date.today().isoformat()}.md"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render_markdown(commits, args.base, args.head))

    counts = Counter(c.verdict for c in commits)
    print(f"Wrote {out_path} — {len(commits)} commits", end="")
    print(" — " + ", ".join(f"{v}={counts.get(v, 0)}" for v in VERDICTS))
    return 0


if __name__ == "__main__":
    sys.exit(main())
