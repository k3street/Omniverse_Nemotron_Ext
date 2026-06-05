#!/usr/bin/env python3
"""Phase 2b (deliverable 2/3) — Per-phase file-write-set matrix.

Parses `specs/IA_FULL_SPEC_2026-05-10.md`, extracts each phase's
declared file writes (the `**Files (changes):**` and
`**Files (new):**` bullet lists), and produces an inverted index
`{filepath → list[phase_id]}`.

Outputs:
- `docs/audits/phase_file_writes.json` — machine-readable index
- `docs/audits/phase_file_writes.md`   — human-readable summary

The "shared file" table highlights files written by multiple
phases — those are the parallel-dispatch conflict points that
`safe_batch.py` consults.

Per `specs/IA_FULL_SPEC_2026-05-10.md` Phase 2b.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable

_REPO_ROOT = Path(__file__).parent.parent
_SPEC_PATH = _REPO_ROOT / "specs" / "IA_FULL_SPEC_2026-05-10.md"


# Phase header: "## Phase 0b — ..." or "# Phase 0b — ..." (Phase 0b is H1).
_PHASE_HEADER_RE = re.compile(r"^#{1,2}\s+Phase\s+([\w.]+)\s*[—\-]", re.MULTILINE)
# Files-block opener: "**Files (changes):**" or "**Files (new):**" — bold mandatory.
_FILES_BLOCK_RE = re.compile(
    r"\*\*Files \((?:changes|new)\):\*\*", re.IGNORECASE
)
# Bullet line: starts with `-` then a backtick-quoted path (we accept both
# raw text and code-fenced paths; the spec uses backticks consistently for
# real file paths).
_PATH_IN_BULLET_RE = re.compile(r"^\s*-\s+`([^`]+)`")


@dataclass
class PhaseFileWrites:
    phase_id: str
    files_changes: list[str] = field(default_factory=list)
    files_new: list[str] = field(default_factory=list)
    line_start: int = 0  # 1-indexed line in spec where this phase begins


@dataclass
class FileWriteMatrix:
    phases: dict[str, PhaseFileWrites] = field(default_factory=dict)
    file_to_phases: dict[str, list[str]] = field(default_factory=dict)


def _parse_phase_sections(text: str) -> list[tuple[str, int, int]]:
    """Return list of (phase_id, start_line_idx, end_line_idx_exclusive).

    Lines are 0-indexed for slicing. Each section runs from the phase
    header line to (but not including) the next phase header or end of
    file.
    """
    lines = text.splitlines()
    headers: list[tuple[str, int]] = []
    for i, line in enumerate(lines):
        m = _PHASE_HEADER_RE.match(line)
        if m:
            headers.append((m.group(1), i))
    sections: list[tuple[str, int, int]] = []
    for idx, (phase_id, start) in enumerate(headers):
        end = headers[idx + 1][1] if idx + 1 < len(headers) else len(lines)
        sections.append((phase_id, start, end))
    return sections


def _extract_files_from_section(lines: list[str]) -> tuple[list[str], list[str]]:
    """Within one phase section, find `Files (changes)` and `Files (new)`
    bullet lists. Stop at the next `**...**` block or section end.
    """
    changes: list[str] = []
    new: list[str] = []
    current: list[str] | None = None
    for raw in lines:
        line = raw.rstrip("\n")
        m_block = _FILES_BLOCK_RE.search(line)
        if m_block:
            if "Files (changes)" in line.lower() or "files (changes)" in line.lower():
                current = changes
            else:
                current = new
            continue
        # Another `**Header:**` line ends the current block.
        if line.lstrip().startswith("**") and "Files (" not in line and current is not None:
            current = None
        if current is None:
            continue
        m_path = _PATH_IN_BULLET_RE.match(line)
        if m_path:
            current.append(m_path.group(1))
    return changes, new


def build_matrix(spec_path: Path = _SPEC_PATH) -> FileWriteMatrix:
    text = spec_path.read_text()
    matrix = FileWriteMatrix()
    sections = _parse_phase_sections(text)
    lines = text.splitlines()

    for phase_id, start, end in sections:
        section_lines = lines[start:end]
        changes, new = _extract_files_from_section(section_lines)
        matrix.phases[phase_id] = PhaseFileWrites(
            phase_id=phase_id,
            files_changes=changes,
            files_new=new,
            line_start=start + 1,
        )

    inv: dict[str, list[str]] = defaultdict(list)
    for phase_id, pw in matrix.phases.items():
        seen: set[str] = set()
        for path in pw.files_changes + pw.files_new:
            if path in seen:
                continue
            seen.add(path)
            inv[path].append(phase_id)
    matrix.file_to_phases = {p: sorted(set(ph), key=_phase_sort_key) for p, ph in inv.items()}
    return matrix


def _phase_sort_key(phase_id: str) -> tuple[int, str]:
    """Sort phases by numeric prefix then suffix string."""
    m = re.match(r"(\d+)(.*)", phase_id)
    if m:
        return (int(m.group(1)), m.group(2))
    return (10_000, phase_id)


def render_markdown(matrix: FileWriteMatrix, top_n: int = 30) -> str:
    lines: list[str] = []
    lines.append("# Phase File-Write-Set Matrix")
    lines.append("")
    lines.append(f"**Phases parsed:** {len(matrix.phases)}")
    lines.append(f"**Unique files referenced:** {len(matrix.file_to_phases)}")
    lines.append("")
    shared = {p: phs for p, phs in matrix.file_to_phases.items() if len(phs) >= 2}
    lines.append(f"**Files written by ≥ 2 phases (potential conflicts):** {len(shared)}")
    lines.append("")
    lines.append(
        "Use `scripts/safe_batch.py PHASE_IDS...` to check whether a "
        "proposed batch of phases can run in parallel safely (no "
        "overlapping file writes)."
    )
    lines.append("")

    lines.append(f"## Most-contended files (top {top_n})")
    lines.append("")
    if not shared:
        lines.append("(none — every file is written by at most one phase)")
    else:
        sorted_shared = sorted(
            shared.items(), key=lambda kv: (-len(kv[1]), kv[0])
        )[:top_n]
        lines.append("| file | phase count | phases |")
        lines.append("|---|---|---|")
        for path, phs in sorted_shared:
            phase_list = ", ".join(f"`{p}`" for p in phs)
            lines.append(f"| `{path}` | {len(phs)} | {phase_list} |")
    lines.append("")

    lines.append("## Per-phase file declarations")
    lines.append("")
    lines.append("(See `docs/audits/phase_file_writes.json` for the full machine-readable index.)")
    lines.append("")
    for phase_id in sorted(matrix.phases, key=_phase_sort_key):
        pw = matrix.phases[phase_id]
        total = len(pw.files_changes) + len(pw.files_new)
        if total == 0:
            continue
        lines.append(f"### Phase {phase_id}  (line {pw.line_start})")
        lines.append("")
        if pw.files_changes:
            lines.append(f"- **changes** ({len(pw.files_changes)}):")
            for f in pw.files_changes:
                lines.append(f"  - `{f}`")
        if pw.files_new:
            lines.append(f"- **new** ({len(pw.files_new)}):")
            for f in pw.files_new:
                lines.append(f"  - `{f}`")
        lines.append("")
    return "\n".join(lines) + "\n"


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", default=str(_SPEC_PATH))
    parser.add_argument(
        "--md-out",
        default=str(_REPO_ROOT / "docs" / "audits" / "phase_file_writes.md"),
    )
    parser.add_argument(
        "--json-out",
        default=str(_REPO_ROOT / "docs" / "audits" / "phase_file_writes.json"),
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    matrix = build_matrix(Path(args.spec))

    md_out = Path(args.md_out)
    json_out = Path(args.json_out)
    md_out.parent.mkdir(parents=True, exist_ok=True)
    json_out.parent.mkdir(parents=True, exist_ok=True)

    md_out.write_text(render_markdown(matrix))
    json_out.write_text(
        json.dumps(
            {
                "phases": {pid: asdict(pw) for pid, pw in matrix.phases.items()},
                "file_to_phases": matrix.file_to_phases,
            },
            indent=2,
            sort_keys=True,
        )
    )

    print(
        f"Wrote {md_out} + {json_out} — "
        f"phases={len(matrix.phases)}, files={len(matrix.file_to_phases)}, "
        f"shared(≥2)={sum(1 for v in matrix.file_to_phases.values() if len(v) >= 2)}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
