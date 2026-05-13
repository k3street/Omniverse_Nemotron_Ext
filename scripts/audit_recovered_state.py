"""Phase 8 prerequisite — audit module-level symbols in tool_executor.py's
'recovered state' block (lines 32-1572) to identify safe migration paths.

For each symbol (constant / class / function) in the block, classify by
how it's used elsewhere:

  - INTERNAL_ONLY: referenced only inside tool_executor.py itself
                   → candidate to delete if all internal uses are dead
  - HANDLER_USED: referenced by handlers/*.py (direct OR via `_te.NAME`)
                  → must be migrated to handlers/_shared.py or _state.py
  - EXTERNAL_USED: referenced from elsewhere (service/, tests/, scripts/)
                   → migrate cautiously, update import sites

Output: markdown table written to docs/audits/recovered_state_audit.md.

Per spec/IA_FULL_SPEC_2026-05-10.md Phase 8 (extract shared utilities) +
Phase 13 (archive recovered-state block).
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Tuple

_REPO_ROOT = Path(__file__).parent.parent
_EXEC_PATH = (
    _REPO_ROOT
    / "service"
    / "isaac_assist_service"
    / "chat"
    / "tools"
    / "tool_executor.py"
)
_RECOVERED_END_MARKER = "# End recovered state"


def _find_block_range() -> Tuple[int, int]:
    """Return (start_line, end_line) 1-indexed for the recovered-state block."""
    text = _EXEC_PATH.read_text()
    lines = text.split("\n")
    start = None
    for i, line in enumerate(lines):
        if "Recovered state for bundled PR handlers" in line:
            start = i
            break
    assert start is not None, "Recovered-state header not found"
    end = None
    for i in range(start, len(lines)):
        if lines[i].strip() == _RECOVERED_END_MARKER:
            end = i
            break
    assert end is not None, "End-of-recovered-state marker not found"
    return start + 1, end + 1


def _extract_top_level_symbols(start: int, end: int) -> List[Tuple[int, str, str]]:
    """Return [(line_no, name, kind), ...] for top-level symbols in range."""
    text = _EXEC_PATH.read_text()
    lines = text.split("\n")
    out: List[Tuple[int, str, str]] = []
    for i in range(start - 1, end):
        line = lines[i]
        m = re.match(r"^(_?[A-Z][A-Z0-9_]*)\s*=", line)
        if m:
            out.append((i + 1, m.group(1), "constant"))
            continue
        m = re.match(r"^class\s+(\w+)", line)
        if m:
            out.append((i + 1, m.group(1), "class"))
            continue
        m = re.match(r"^def\s+(_?\w+)", line)
        if m:
            out.append((i + 1, m.group(1), "function"))
    return out


def _ref_locations(name: str) -> Dict[str, List[str]]:
    """Run grep -wn for `name` across the codebase and bucket by category."""
    result = subprocess.run(
        ["grep", "-rn", "-w", name,
         "service/", "tests/", "scripts/"],
        capture_output=True, text=True, cwd=_REPO_ROOT,
    )
    buckets = {"definition": [], "internal": [], "handler": [], "external": []}
    for line in result.stdout.split("\n"):
        if not line:
            continue
        # `path:lineno:content`
        try:
            path, lineno, _ = line.split(":", 2)
        except ValueError:
            continue
        if path.endswith("tool_executor.py"):
            buckets["internal"].append(f"{path}:{lineno}")
        elif "/handlers/" in path:
            buckets["handler"].append(f"{path}:{lineno}")
        elif path.startswith("tests/") or path.startswith("scripts/"):
            buckets["external"].append(f"{path}:{lineno}")
        else:
            buckets["external"].append(f"{path}:{lineno}")
    # Also look for `_te.NAME` and `tool_executor.NAME` patterns
    result_te = subprocess.run(
        ["grep", "-rn", f"_te\\.{name}\\|tool_executor\\.{name}",
         "service/", "tests/", "scripts/"],
        capture_output=True, text=True, cwd=_REPO_ROOT,
    )
    for line in result_te.stdout.split("\n"):
        if not line:
            continue
        try:
            path, lineno, _ = line.split(":", 2)
        except ValueError:
            continue
        bucket = "handler" if "/handlers/" in path else "external"
        ref = f"{path}:{lineno}"
        if ref not in buckets[bucket]:
            buckets[bucket].append(ref)
    return buckets


def classify_symbol(name: str) -> str:
    """Return one of: DEAD, INTERNAL_ONLY, HANDLER_USED, EXTERNAL_USED."""
    refs = _ref_locations(name)
    has_handler = len(refs["handler"]) > 0
    has_external = len(refs["external"]) > 0
    has_internal = len(refs["internal"]) > 1  # >1 because the definition itself counts as 1
    if has_handler:
        return "HANDLER_USED"
    if has_external:
        return "EXTERNAL_USED"
    if has_internal:
        return "INTERNAL_ONLY"
    return "DEAD"


def render_markdown(symbols: List[Tuple[int, str, str]], classifications: Dict[str, str]) -> str:
    """Build the audit report markdown."""
    bucket_counts = {"DEAD": 0, "INTERNAL_ONLY": 0, "HANDLER_USED": 0, "EXTERNAL_USED": 0}
    for _, name, _ in symbols:
        bucket_counts[classifications[name]] += 1
    lines = [
        "# Recovered-state audit — tool_executor.py lines 32-1572",
        "",
        "Generated by `scripts/audit_recovered_state.py`. Used to plan Phase 8 (extract",
        "shared utilities) and Phase 13 (archive recovered-state block).",
        "",
        "## Summary",
        "",
        f"- Total symbols: {len(symbols)}",
        f"- DEAD (no references anywhere): **{bucket_counts['DEAD']}** — safe to delete",
        f"- INTERNAL_ONLY (only used inside tool_executor.py): **{bucket_counts['INTERNAL_ONLY']}** — delete after auditing internal uses",
        f"- HANDLER_USED (used by handlers/*.py): **{bucket_counts['HANDLER_USED']}** — migrate to handlers/_shared.py or _state.py",
        f"- EXTERNAL_USED (used by service/, tests/, scripts/ outside handlers/): **{bucket_counts['EXTERNAL_USED']}** — migrate cautiously",
        "",
        "## Classification",
        "",
        "| Line | Kind | Name | Classification |",
        "|-----:|------|------|----------------|",
    ]
    for lineno, name, kind in symbols:
        lines.append(f"| {lineno} | {kind} | `{name}` | {classifications[name]} |")
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out", type=Path,
        default=_REPO_ROOT / "docs" / "audits" / "recovered_state_audit.md",
    )
    parser.add_argument(
        "--summary-only", action="store_true",
        help="Print summary counts to stdout; skip writing the file.",
    )
    args = parser.parse_args(argv)

    start, end = _find_block_range()
    print(f"Recovered-state block: lines {start}..{end}", file=sys.stderr)
    symbols = _extract_top_level_symbols(start, end)
    print(f"Found {len(symbols)} top-level symbols", file=sys.stderr)

    classifications: Dict[str, str] = {}
    for i, (_, name, _) in enumerate(symbols):
        if i % 10 == 0:
            print(f"  Classifying {i}/{len(symbols)}...", file=sys.stderr)
        classifications[name] = classify_symbol(name)

    counts = {"DEAD": 0, "INTERNAL_ONLY": 0, "HANDLER_USED": 0, "EXTERNAL_USED": 0}
    for c in classifications.values():
        counts[c] += 1
    print(f"\nSummary: {counts}", file=sys.stderr)

    if args.summary_only:
        return 0

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render_markdown(symbols, classifications))
    print(f"Wrote {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
