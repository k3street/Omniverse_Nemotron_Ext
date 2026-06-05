#!/usr/bin/env python3
"""Phase 2b (deliverable 3/3) — `safe_batch.py` dispatch helper.

Given a list of phase IDs, decide whether they can be dispatched to
parallel agents without file-write collisions.

Reads `docs/audits/phase_file_writes.json` (produced by
`audit_phase_file_writes.py`) — if absent, regenerates it on the
fly so this script is self-contained for ad-hoc use.

Exit codes:
  0 — green: parallel-safe (no overlapping file writes)
  1 — red:   conflict detected (printed details)
  2 — error: unknown phase ID or missing audit data

Usage:
    python scripts/safe_batch.py 73 49b 90
    python scripts/safe_batch.py 70b 70c 70d --verbose

Per `specs/IA_FULL_SPEC_2026-05-10.md` Phase 2b.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Iterable

_REPO_ROOT = Path(__file__).parent.parent
_AUDIT_JSON = _REPO_ROOT / "docs" / "audits" / "phase_file_writes.json"


def _load_or_build_audit(audit_path: Path = _AUDIT_JSON) -> dict:
    """Read the audit JSON or rebuild it via the audit script."""
    if not audit_path.exists():
        # Import the sibling auditor and rebuild on the fly.
        sys.path.insert(0, str(_REPO_ROOT / "scripts"))
        import audit_phase_file_writes as apw  # type: ignore[import-not-found]

        matrix = apw.build_matrix()
        # Materialise in the same shape the file would have.
        return {
            "phases": {
                pid: {
                    "phase_id": pw.phase_id,
                    "files_changes": pw.files_changes,
                    "files_new": pw.files_new,
                    "line_start": pw.line_start,
                }
                for pid, pw in matrix.phases.items()
            },
            "file_to_phases": matrix.file_to_phases,
        }
    return json.loads(audit_path.read_text())


def check_batch(
    phase_ids: list[str], audit: dict
) -> tuple[bool, dict[str, list[str]], list[str]]:
    """Return (parallel_safe, conflicts_by_file, unknown_phases).

    conflicts_by_file: file → [phase_ids touching it] when ≥ 2 phases
    in the batch write the same file.
    unknown_phases: phase IDs not present in the audit.
    """
    phases_data = audit.get("phases", {})
    unknown = [p for p in phase_ids if p not in phases_data]

    # Collect file → batch-phases that touch it.
    file_to_batch: dict[str, list[str]] = defaultdict(list)
    for pid in phase_ids:
        pw = phases_data.get(pid)
        if not pw:
            continue
        seen: set[str] = set()
        for f in pw.get("files_changes", []) + pw.get("files_new", []):
            if f in seen:
                continue
            seen.add(f)
            file_to_batch[f].append(pid)

    conflicts = {f: sorted(set(phs)) for f, phs in file_to_batch.items() if len(set(phs)) >= 2}
    parallel_safe = not conflicts and not unknown
    return parallel_safe, conflicts, unknown


def _format_verdict(
    parallel_safe: bool,
    conflicts: dict[str, list[str]],
    unknown: list[str],
    phase_ids: list[str],
    verbose: bool,
) -> str:
    if unknown:
        return (
            f"error: phase ID(s) not found in audit: {unknown}\n"
            "Re-run scripts/audit_phase_file_writes.py and verify the\n"
            "phase IDs match the spec's phase headers exactly."
        )

    lines: list[str] = []
    if parallel_safe:
        lines.append(f"green: parallel-safe ({len(phase_ids)} phases, no file conflicts)")
        if verbose:
            lines.append("")
            lines.append(f"Phases: {', '.join(phase_ids)}")
        return "\n".join(lines)

    lines.append(f"red: conflict ({len(conflicts)} shared file(s))")
    lines.append("")
    for path, phs in sorted(conflicts.items()):
        joined = ", ".join(phs)
        lines.append(f"  - `{path}` written by: {joined}")
    lines.append("")
    lines.append("Resolution options:")
    lines.append("  1. Serialise — dispatch one phase at a time on the contended file.")
    lines.append("  2. Pre-refactor — split the file so conflicting phases write to")
    lines.append("     disjoint targets (e.g. spec already splits some 70* phases")
    lines.append("     into separate handlers/* modules).")
    lines.append("  3. Re-batch — drop a phase to break the conflict and run the rest.")
    return "\n".join(lines)


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "phase_ids",
        nargs="+",
        help="One or more phase IDs (e.g. 0b 1 49b 70b)",
    )
    parser.add_argument("--audit-json", default=str(_AUDIT_JSON))
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)

    audit = _load_or_build_audit(Path(args.audit_json))
    parallel_safe, conflicts, unknown = check_batch(args.phase_ids, audit)
    print(_format_verdict(parallel_safe, conflicts, unknown, args.phase_ids, args.verbose))

    if unknown:
        return 2
    return 0 if parallel_safe else 1


if __name__ == "__main__":
    sys.exit(main())
