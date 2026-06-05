"""Phase 98 contract tests — Markdown documentation polish linter."""
from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.l0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _polisher(max_line_length: int = 100):
    from service.isaac_assist_service.multimodal.documentation_polish import MarkdownPolisher
    return MarkdownPolisher(max_line_length=max_line_length)


# ---------------------------------------------------------------------------
# Test 1 — metadata contract
# ---------------------------------------------------------------------------

def test_phase_98_metadata():
    from service.isaac_assist_service.multimodal.documentation_polish import get_phase_metadata
    md = get_phase_metadata()
    assert md["phase"] == 98
    assert md["status"] == "landed"
    assert "title" in md
    assert "spec_ref" in md


# ---------------------------------------------------------------------------
# Test 2 — trailing_whitespace detected
# ---------------------------------------------------------------------------

def test_trailing_whitespace_detected():
    content = "# Title\n\nGood line\nBad line   \nAnother good\n"
    issues = _polisher().check(content)
    kinds = [i.kind for i in issues]
    assert "trailing_whitespace" in kinds
    # Only the one bad line flagged
    assert sum(1 for k in kinds if k == "trailing_whitespace") == 1
    hit = next(i for i in issues if i.kind == "trailing_whitespace")
    assert hit.line == 4


# ---------------------------------------------------------------------------
# Test 3 — double_blank detected
# ---------------------------------------------------------------------------

def test_double_blank_detected():
    content = "Line A\n\n\nLine B\n"
    issues = _polisher().check(content)
    kinds = [i.kind for i in issues]
    assert "double_blank" in kinds
    hit = next(i for i in issues if i.kind == "double_blank")
    # Third line is the second consecutive blank
    assert hit.line == 3


def test_single_blank_not_flagged():
    content = "Line A\n\nLine B\n"
    issues = _polisher().check(content)
    kinds = [i.kind for i in issues]
    assert "double_blank" not in kinds


# ---------------------------------------------------------------------------
# Test 4 — heading_skip detected (H1 → H3)
# ---------------------------------------------------------------------------

def test_heading_skip_h1_to_h3():
    content = "# Main Title\n\n### Section Detail\n"
    issues = _polisher().check(content)
    kinds = [i.kind for i in issues]
    assert "heading_skip" in kinds
    hit = next(i for i in issues if i.kind == "heading_skip")
    assert hit.line == 3


def test_heading_h1_h2_h3_no_skip():
    content = "# Title\n\n## Section\n\n### Subsection\n"
    issues = _polisher().check(content)
    kinds = [i.kind for i in issues]
    assert "heading_skip" not in kinds


# ---------------------------------------------------------------------------
# Test 5 — todo_marker detected
# ---------------------------------------------------------------------------

def test_todo_marker_detected():
    content = "# Doc\n\nThis is fine.\n\n<!-- TODO: fix this -->\n\nDone.\n"
    issues = _polisher().check(content)
    kinds = [i.kind for i in issues]
    assert "todo_marker" in kinds


def test_fixme_and_xxx_detected():
    content = "Line with FIXME here\nAnother with XXX\n"
    issues = _polisher().check(content)
    kinds = [i.kind for i in issues]
    assert kinds.count("todo_marker") == 2


# ---------------------------------------------------------------------------
# Test 6 — tab_indent detected
# ---------------------------------------------------------------------------

def test_tab_indent_detected():
    content = "Normal line\n\tTabbed line\nAnother normal\n"
    issues = _polisher().check(content)
    kinds = [i.kind for i in issues]
    assert "tab_indent" in kinds
    hit = next(i for i in issues if i.kind == "tab_indent")
    assert hit.line == 2


# ---------------------------------------------------------------------------
# Test 7 — long_line detected; code fence content NOT flagged
# ---------------------------------------------------------------------------

def test_long_line_detected():
    long = "x" * 101
    content = f"# Title\n\n{long}\n"
    issues = _polisher(max_line_length=100).check(content)
    kinds = [i.kind for i in issues]
    assert "long_line" in kinds


def test_code_fence_long_line_not_flagged():
    long = "x" * 200
    content = f"# Title\n\n```python\n{long}\n```\n\nNormal line.\n"
    issues = _polisher(max_line_length=100).check(content)
    kinds = [i.kind for i in issues]
    assert "long_line" not in kinds


def test_long_line_outside_fence_flagged():
    long = "x" * 150
    content = f"```python\n{'y' * 200}\n```\n\n{long}\n"
    issues = _polisher(max_line_length=100).check(content)
    kinds = [i.kind for i in issues]
    assert "long_line" in kinds
    # The line INSIDE the fence must not be the flagged one
    for i in issues:
        if i.kind == "long_line":
            assert i.line == 5  # the long line after the fence


# ---------------------------------------------------------------------------
# Test 8 — stale_year detected; current year (2026) NOT flagged
# ---------------------------------------------------------------------------

def test_stale_year_detected():
    content = "Copyright 2020 Acme Corp\n"
    issues = _polisher().check(content, min_year=2024)
    kinds = [i.kind for i in issues]
    assert "stale_year" in kinds


def test_current_year_not_flagged():
    content = "Copyright 2026 Acme Corp\n"
    issues = _polisher().check(content, min_year=2024)
    kinds = [i.kind for i in issues]
    assert "stale_year" not in kinds


def test_boundary_year_not_flagged():
    # min_year=2024 means 2024 is acceptable (strictly-less-than check)
    content = "Copyright 2024 Acme Corp\n"
    issues = _polisher().check(content, min_year=2024)
    kinds = [i.kind for i in issues]
    assert "stale_year" not in kinds


def test_stale_year_copyright_c_notation():
    content = "(c) 2022 SomeOrg\n"
    issues = _polisher().check(content, min_year=2024)
    kinds = [i.kind for i in issues]
    assert "stale_year" in kinds


# ---------------------------------------------------------------------------
# Test 9 — auto_fix strips trailing whitespace + collapses double-blanks
#          but preserves TODO markers
# ---------------------------------------------------------------------------

def test_auto_fix_trailing_whitespace():
    from service.isaac_assist_service.multimodal.documentation_polish import auto_fix
    content = "Good line\nBad line   \nAnother   \n"
    fixed = auto_fix(content)
    for line in fixed.splitlines():
        assert not line.endswith(" "), f"Trailing space remains: {line!r}"


def test_auto_fix_collapses_double_blank():
    from service.isaac_assist_service.multimodal.documentation_polish import auto_fix
    content = "A\n\n\n\nB\n"
    fixed = auto_fix(content)
    # Should have at most one consecutive blank line
    lines = fixed.splitlines()
    for idx in range(len(lines) - 1):
        assert not (lines[idx] == "" and lines[idx + 1] == ""), (
            f"Double blank at lines {idx+1}/{idx+2}"
        )


def test_auto_fix_preserves_todo():
    from service.isaac_assist_service.multimodal.documentation_polish import auto_fix
    content = "# Title\n\nTODO: fix this\n"
    fixed = auto_fix(content)
    assert "TODO: fix this" in fixed


def test_auto_fix_preserves_trailing_newline():
    from service.isaac_assist_service.multimodal.documentation_polish import auto_fix
    content = "line one\n"
    fixed = auto_fix(content)
    assert fixed.endswith("\n")


def test_auto_fix_no_trailing_newline_preserved():
    from service.isaac_assist_service.multimodal.documentation_polish import auto_fix
    content = "line one"
    fixed = auto_fix(content)
    assert not fixed.endswith("\n")


# ---------------------------------------------------------------------------
# Test 10 — polish_dir aggregates multiple files into dict
# ---------------------------------------------------------------------------

def test_polish_dir_aggregates(tmp_path: Path):
    (tmp_path / "a.md").write_text("# A\n\nClean file.\n", encoding="utf-8")
    (tmp_path / "b.md").write_text(
        "# B\n\nTrailing space   \n\nAnother line.\n", encoding="utf-8"
    )
    (tmp_path / "not_md.txt").write_text("ignored\n", encoding="utf-8")

    reports = _polisher().polish_dir(tmp_path, glob="*.md")
    # Only .md files included
    assert len(reports) == 2
    paths = {Path(p).name for p in reports}
    assert paths == {"a.md", "b.md"}

    # a.md should be clean
    a_report = next(r for p, r in reports.items() if Path(p).name == "a.md")
    assert a_report.issue_count == 0

    # b.md should have trailing_whitespace
    b_report = next(r for p, r in reports.items() if Path(p).name == "b.md")
    assert b_report.issue_count > 0
    assert "trailing_whitespace" in b_report.issues_by_kind


# ---------------------------------------------------------------------------
# Test 11 — polish_file returns DocPolishReport with correct structure
# ---------------------------------------------------------------------------

def test_polish_file_structure(tmp_path: Path):
    from service.isaac_assist_service.multimodal.documentation_polish import DocPolishReport
    md = tmp_path / "test.md"
    md.write_text("# Title\n\n### Skip   \n\nTODO: something\n\n\nEnd\n", encoding="utf-8")

    report = _polisher().polish_file(md)

    assert isinstance(report, DocPolishReport)
    assert report.path == str(md)
    assert report.issue_count == len(report.issues)
    assert report.issue_count == sum(report.issues_by_kind.values())
    # Should flag heading_skip, trailing_whitespace, todo_marker, double_blank
    assert "heading_skip" in report.issues_by_kind
    assert "trailing_whitespace" in report.issues_by_kind
    assert "todo_marker" in report.issues_by_kind
    assert "double_blank" in report.issues_by_kind
