"""Phase 98 — Documentation polish.

Provides a rule-based Markdown linter and auto-fixer for Isaac Assist
documentation files.  Detects common quality issues and can safely
fix a subset of them in-place.

Rules implemented:
    trailing_whitespace  — line ends with space or tab
    double_blank         — two or more consecutive blank lines
    heading_skip         — H3 immediately follows H1 with no H2 between
    stale_year           — copyright/license year is below a configurable cutoff
    todo_marker          — line contains TODO / FIXME / XXX
    tab_indent           — line starts with a tab character
    long_line            — line exceeds max_line_length (code fences exempted)

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 98.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional


PHASE_ID = 98
PHASE_TITLE = "Documentation polish"
PHASE_STATUS = "landed"


def get_phase_metadata() -> Dict[str, Any]:
    return {
        "phase": PHASE_ID,
        "title": PHASE_TITLE,
        "status": PHASE_STATUS,
        "spec_ref": "specs/IA_FULL_SPEC_2026-05-10.md Phase 98",
    }


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

IssueKind = Literal[
    "trailing_whitespace",
    "double_blank",
    "heading_skip",
    "stale_year",
    "todo_marker",
    "tab_indent",
    "long_line",
]


@dataclass
class MarkdownIssue:
    """A single linting issue found in a Markdown document.

    Attributes:
        line: 1-based line number where the issue was detected.
        kind: Category of the issue (one of the IssueKind literals).
        snippet: Short excerpt of the offending text (≤ 120 chars).
    """

    line: int
    kind: IssueKind
    snippet: str


@dataclass
class DocPolishReport:
    """Aggregated linting result for one Markdown file.

    Attributes:
        path: Absolute or relative path of the inspected file.
        issue_count: Total number of issues found.
        issues_by_kind: Mapping of kind → count for quick dashboarding.
        issues: Ordered list of all individual issues.
    """

    path: str
    issue_count: int
    issues_by_kind: Dict[str, int]
    issues: List[MarkdownIssue] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Core linter
# ---------------------------------------------------------------------------

_STALE_YEAR_RE = re.compile(
    r"(?:Copyright\s+|©\s*|\(c\)\s*)(\d{4})",
    re.IGNORECASE,
)

_TODO_RE = re.compile(r"\b(TODO|FIXME|XXX)\b")

_HEADING_RE = re.compile(r"^(#{1,6})\s")

_CODE_FENCE_RE = re.compile(r"^```")


class MarkdownPolisher:
    """Lint and optionally auto-fix Markdown files.

    Args:
        max_line_length: Lines longer than this value (excluding code-fence
            blocks) are flagged as ``long_line``.  Default: 100.
    """

    def __init__(self, max_line_length: int = 100) -> None:
        self.max_line_length = max_line_length

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check(
        self,
        content: str,
        min_year: int = 2024,
    ) -> List[MarkdownIssue]:
        """Run all lint rules against *content* and return a list of issues.

        Args:
            content: Full text of the Markdown document.
            min_year: Copyright years strictly less than this value are
                flagged as ``stale_year``.  Default: 2024.

        Returns:
            Ordered list of :class:`MarkdownIssue` objects, sorted by line
            number.
        """
        lines = content.splitlines()
        issues: List[MarkdownIssue] = []

        in_code_fence = False
        last_heading_level: Optional[int] = None
        prev_blank = False

        for i, raw_line in enumerate(lines, start=1):
            # Track code-fence state (toggle on opening/closing ```)
            if _CODE_FENCE_RE.match(raw_line):
                in_code_fence = not in_code_fence

            is_blank = raw_line.strip() == ""

            # ---- trailing_whitespace ----------------------------------------
            if raw_line != raw_line.rstrip(" \t"):
                issues.append(
                    MarkdownIssue(
                        line=i,
                        kind="trailing_whitespace",
                        snippet=repr(raw_line[:80]),
                    )
                )

            # ---- tab_indent --------------------------------------------------
            if raw_line.startswith("\t"):
                issues.append(
                    MarkdownIssue(
                        line=i,
                        kind="tab_indent",
                        snippet=repr(raw_line[:80]),
                    )
                )

            # ---- double_blank ------------------------------------------------
            if is_blank and prev_blank:
                issues.append(
                    MarkdownIssue(
                        line=i,
                        kind="double_blank",
                        snippet="(consecutive blank lines)",
                    )
                )

            # ---- todo_marker -------------------------------------------------
            if not is_blank and _TODO_RE.search(raw_line):
                issues.append(
                    MarkdownIssue(
                        line=i,
                        kind="todo_marker",
                        snippet=raw_line[:80],
                    )
                )

            # ---- stale_year --------------------------------------------------
            m = _STALE_YEAR_RE.search(raw_line)
            if m:
                year = int(m.group(1))
                if year < min_year:
                    issues.append(
                        MarkdownIssue(
                            line=i,
                            kind="stale_year",
                            snippet=raw_line[:80],
                        )
                    )

            # ---- heading_skip (H1 → H3 without H2) -------------------------
            hm = _HEADING_RE.match(raw_line)
            if hm:
                level = len(hm.group(1))
                if (
                    last_heading_level == 1
                    and level == 3
                ):
                    issues.append(
                        MarkdownIssue(
                            line=i,
                            kind="heading_skip",
                            snippet=raw_line[:80],
                        )
                    )
                last_heading_level = level

            # ---- long_line (skip code-fence interiors) ----------------------
            if not in_code_fence and len(raw_line) > self.max_line_length:
                issues.append(
                    MarkdownIssue(
                        line=i,
                        kind="long_line",
                        snippet=raw_line[: self.max_line_length + 20] + "...",
                    )
                )

            prev_blank = is_blank

        return issues

    def polish_file(self, path: Path) -> DocPolishReport:
        """Lint a single Markdown file and return a :class:`DocPolishReport`.

        Args:
            path: Path to the ``.md`` file.

        Returns:
            A :class:`DocPolishReport` for the file.
        """
        content = path.read_text(encoding="utf-8")
        issues = self.check(content)
        by_kind: Dict[str, int] = {}
        for issue in issues:
            by_kind[issue.kind] = by_kind.get(issue.kind, 0) + 1
        return DocPolishReport(
            path=str(path),
            issue_count=len(issues),
            issues_by_kind=by_kind,
            issues=issues,
        )

    def polish_dir(
        self,
        dir_path: Path,
        glob: str = "*.md",
    ) -> Dict[str, DocPolishReport]:
        """Lint all Markdown files matching *glob* under *dir_path*.

        Args:
            dir_path: Directory to search (non-recursive by default; use
                ``**/*.md`` for recursive).
            glob: Glob pattern relative to *dir_path*.

        Returns:
            Mapping of file path string → :class:`DocPolishReport`.
        """
        reports: Dict[str, DocPolishReport] = {}
        for md_file in sorted(dir_path.glob(glob)):
            if md_file.is_file():
                report = self.polish_file(md_file)
                reports[str(md_file)] = report
        return reports


# ---------------------------------------------------------------------------
# Auto-fixer
# ---------------------------------------------------------------------------

def auto_fix(content: str) -> str:
    """Auto-fix safe issues in *content* and return the rewritten string.

    Fixes applied:
        - ``trailing_whitespace``: strips trailing spaces and tabs from
          every line.
        - ``double_blank``: collapses runs of 2+ blank lines to a single
          blank line.

    Issues intentionally NOT auto-fixed (require human judgment):
        - ``heading_skip``
        - ``todo_marker``
        - ``long_line``
        - ``stale_year``
        - ``tab_indent``

    Args:
        content: Full Markdown text to fix.

    Returns:
        Rewritten text with safe fixes applied.  A trailing newline is
        preserved if the original ended with one.
    """
    trailing_newline = content.endswith("\n")
    lines = content.splitlines()

    # Strip trailing whitespace from every line
    lines = [line.rstrip(" \t") for line in lines]

    # Collapse consecutive blank lines
    collapsed: List[str] = []
    prev_blank = False
    for line in lines:
        is_blank = line == ""
        if is_blank and prev_blank:
            continue  # skip the extra blank
        collapsed.append(line)
        prev_blank = is_blank

    result = "\n".join(collapsed)
    if trailing_newline:
        result += "\n"
    return result
