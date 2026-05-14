#!/usr/bin/env python
"""
scrub_shared_data.py — redact /mnt/shared_data path references from local
workspace data so the agent stops emitting Kimate's machine-specific path.

Per docs/specs/2026-05-08-next-session-autonomous-plan.md Phase 0.1.

Scope (intentional, narrow):
  - workspace/audit.jsonl              — agent action audit log
  - workspace/knowledge/**/*.jsonl     — curated knowledge corpus

NOT in scope (historical artifacts retain forensic value, leave alone):
  - workspace/qa_runs/                 — past eval run logs
  - workspace/turn_snapshots/          — past USD scene snapshots
  - workspace/session_traces/          — past chat session traces

The path is replaced in-place with `<sanitized-asset-path>`. Idempotent —
re-run safely after new audit/knowledge entries accumulate.

NOTE on workspace/tool_index/: an earlier draft of this script also deleted
the ChromaDB tool/template index based on the plan's hypothesis that it
"may surface /mnt/shared_data". On inspection that index only embeds
tool-schema text + template goals, neither of which contain Kimate paths,
so the deletion was a no-op for our goal. Removed.

Usage:
    python scripts/qa/scrub_shared_data.py
"""
from __future__ import annotations

import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
WORKSPACE = REPO_ROOT / "workspace"
GHOST = "/mnt/shared_data"
SAFE = "<sanitized-asset-path>"


def scrub_file(path: pathlib.Path) -> int:
    text = path.read_text(encoding="utf-8")
    if GHOST not in text:
        return 0
    n = text.count(GHOST)
    path.write_text(text.replace(GHOST, SAFE), encoding="utf-8")
    return n


def main() -> int:
    targets: list[pathlib.Path] = []
    audit = WORKSPACE / "audit.jsonl"
    if audit.exists():
        targets.append(audit)
    knowledge = WORKSPACE / "knowledge"
    if knowledge.is_dir():
        targets.extend(sorted(knowledge.glob("**/*.jsonl")))

    total = 0
    for path in targets:
        n = scrub_file(path)
        if n:
            print(f"  scrubbed {n:5d} hits in {path.relative_to(REPO_ROOT)}")
        total += n
    print(f"redacted {total} hit(s) across {len(targets)} file(s) "
          f"to '{SAFE}'")
    return 0


if __name__ == "__main__":
    sys.exit(main())
