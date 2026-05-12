#!/usr/bin/env python3
"""Audit honesty-charter back-links in IA_FULL_SPEC_2026-05-10.md.

For every phase in the implementing register (Honesty Charter §3), check
whether the phase body in the spec contains a back-link to the charter
document (`docs/architecture/honesty.md` or "Honesty Charter:").

Default mode prints a summary and exits 0. `--strict` exits non-zero if
any register phase lacks a back-link.

This script never mutates the spec — back-link insertion is a manual
editing pass the reviewer handles.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# Resolve project root from this script's location: scripts/<this file>
REPO_ROOT = Path(__file__).resolve().parent.parent
SPEC_PATH = REPO_ROOT / "specs" / "IA_FULL_SPEC_2026-05-10.md"
CHARTER_PATH = REPO_ROOT / "docs" / "architecture" / "honesty.md"

# Phases listed in Honesty Charter §3 (Phase-by-phase implementing register).
# Keep this list in sync with docs/architecture/honesty.md §3.
IMPLEMENTING_REGISTER: list[tuple[str, str]] = [
    ("11", "patch validator pluggable pipeline"),
    ("11b", "generic ConstraintViolation framework"),
    ("11c", "controller ctrl:* namespace unification"),
    ("31b", "bridge lifecycle honesty"),
    ("42", "governance & runtime safety"),
    ("45", "Math Critic replacement"),
    ("47", "validator rule enforcement"),
    ("47b", "honesty long-tail"),
    ("49b", "cache key honesty"),
    ("53", "dual contract"),
    ("54", "gap log schema"),
    ("56", "recalibration policy"),
    ("56b", "BCa confidence interval gate"),
    ("78c", "mock-mode tagging"),
    ("83", "overnight chain governance"),
    ("88b", "production sandboxing"),
]

# Strings that count as a back-link to the charter.
BACKLINK_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"Honesty\s+Charter\s*:?\s*§", re.IGNORECASE),
    re.compile(r"docs/architecture/honesty\.md", re.IGNORECASE),
    re.compile(r"honesty\s+charter", re.IGNORECASE),
]


def split_phases(spec_text: str) -> dict[str, str]:
    """Return a mapping {phase_id: phase_body} from the spec.

    A phase starts at `## Phase <id> —` and ends at the next `## Phase` or
    the next top-level `# ` heading. `<id>` is the captured token after
    "Phase " up to the first whitespace.
    """
    phase_header_re = re.compile(
        r"^##\s+Phase\s+([A-Za-z0-9]+)\s+[—-]",
        re.MULTILINE,
    )
    matches = list(phase_header_re.finditer(spec_text))
    phases: dict[str, str] = {}
    for i, m in enumerate(matches):
        phase_id = m.group(1)
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(spec_text)
        phases[phase_id] = spec_text[start:end]
    return phases


def has_backlink(phase_body: str) -> bool:
    return any(p.search(phase_body) for p in BACKLINK_PATTERNS)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--strict",
        action="store_true",
        help="exit non-zero if any register phase lacks a back-link",
    )
    parser.add_argument(
        "--spec",
        type=Path,
        default=SPEC_PATH,
        help=f"path to spec markdown (default: {SPEC_PATH})",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="suppress per-phase reporting; show summary only",
    )
    args = parser.parse_args(argv)

    spec_path: Path = args.spec
    if not spec_path.is_file():
        print(f"ERROR: spec file not found: {spec_path}", file=sys.stderr)
        return 2

    spec_text = spec_path.read_text(encoding="utf-8")
    phases = split_phases(spec_text)

    if not phases:
        print(
            "ERROR: no phase headers found in spec — header pattern may have drifted",
            file=sys.stderr,
        )
        return 2

    missing: list[tuple[str, str]] = []
    present: list[tuple[str, str]] = []
    not_in_spec: list[tuple[str, str]] = []

    for phase_id, label in IMPLEMENTING_REGISTER:
        body = phases.get(phase_id)
        if body is None:
            not_in_spec.append((phase_id, label))
            continue
        if has_backlink(body):
            present.append((phase_id, label))
        else:
            missing.append((phase_id, label))

    total_phases_in_spec = len(phases)
    total_register = len(IMPLEMENTING_REGISTER)
    total_in_spec = total_register - len(not_in_spec)
    total_with_backlink = len(present)
    total_missing = len(missing)

    # Reporting
    if not args.quiet:
        print(f"Honesty Charter back-link audit")
        print(f"  spec:    {spec_path}")
        print(f"  charter: {CHARTER_PATH}")
        print(f"  phases in spec: {total_phases_in_spec}")
        print(f"  register size:  {total_register}")
        print()
        if present:
            print(f"BACK-LINK PRESENT ({len(present)}):")
            for pid, label in present:
                print(f"  - Phase {pid} ({label})")
            print()
        if missing:
            print(f"BACK-LINK MISSING ({len(missing)}):")
            for pid, label in missing:
                print(f"  - Phase {pid} ({label})")
            print()
        if not_in_spec:
            print(f"REGISTER ENTRY NOT FOUND IN SPEC ({len(not_in_spec)}):")
            for pid, label in not_in_spec:
                print(f"  - Phase {pid} ({label})")
            print()

    print(
        "SUMMARY: "
        f"register={total_register} "
        f"found_in_spec={total_in_spec} "
        f"with_backlink={total_with_backlink} "
        f"missing_backlink={total_missing} "
        f"not_in_spec={len(not_in_spec)}"
    )

    if args.strict and (missing or not_in_spec):
        print(
            f"STRICT: failing — {total_missing} missing back-link(s), "
            f"{len(not_in_spec)} register entr(ies) absent from spec",
            file=sys.stderr,
        )
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
