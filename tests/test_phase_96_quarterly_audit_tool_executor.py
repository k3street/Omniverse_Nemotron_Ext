"""Phase 96 — quarterly_audit_tool_executor tests.

Tests cover:
1. Metadata status is "landed"
2. audit_tool_executor_size returns correct line count
3. audit_tool_executor_size warns when file exceeds 500 lines
4. audit_ghost_handlers returns list (should be 0 currently — dispatch is clean)
5. audit_phase_completion uses real yaml and reports >= 80 landed
6. run_full_audit returns the expected top-level dict shape
7. scripts/quarterly_audit.py runs via subprocess and exits 0
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.l0

# ---------------------------------------------------------------------------
# Module import helpers
# ---------------------------------------------------------------------------

from service.isaac_assist_service.multimodal.quarterly_audit_tool_executor import (
    audit_ghost_handlers,
    audit_phase_completion,
    audit_tool_executor_size,
    get_phase_metadata,
    run_full_audit,
    _METADATA_DEFAULT,
    _TOOL_EXECUTOR_DEFAULT,
    _TOOL_SCHEMAS_DEFAULT,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# 1. Metadata
# ---------------------------------------------------------------------------

def test_phase_96_metadata():
    md = get_phase_metadata()
    assert md["phase"] == 96
    assert md["status"] == "landed"
    assert "title" in md
    assert "spec_ref" in md
    assert "Phase 96" in md["spec_ref"]


# ---------------------------------------------------------------------------
# 2. audit_tool_executor_size — correct line count
# ---------------------------------------------------------------------------

def test_audit_tool_executor_size_correct_count():
    result = audit_tool_executor_size(_TOOL_EXECUTOR_DEFAULT)
    assert "lines" in result
    assert isinstance(result["lines"], int)
    assert result["lines"] > 0
    assert "under_500_lines" in result
    assert "warnings" in result
    # The actual line count should match wc -l
    actual_lines = len(
        _TOOL_EXECUTOR_DEFAULT.read_text(encoding="utf-8").splitlines()
    )
    assert result["lines"] == actual_lines


# ---------------------------------------------------------------------------
# 3. audit_tool_executor_size — warning fires when > 500 lines
# ---------------------------------------------------------------------------

def test_audit_tool_executor_size_warns_over_500(tmp_path: Path):
    big_file = tmp_path / "tool_executor.py"
    big_file.write_text("\n".join(["# line"] * 501), encoding="utf-8")
    result = audit_tool_executor_size(big_file)
    assert result["lines"] == 501
    assert result["under_500_lines"] is False
    assert len(result["warnings"]) >= 1
    assert "exceeds" in result["warnings"][0].lower()


def test_audit_tool_executor_size_no_warning_under_500(tmp_path: Path):
    small_file = tmp_path / "tool_executor.py"
    small_file.write_text("\n".join(["# line"] * 100), encoding="utf-8")
    result = audit_tool_executor_size(small_file)
    assert result["lines"] == 100
    assert result["under_500_lines"] is True
    assert result["warnings"] == []


# ---------------------------------------------------------------------------
# 4. audit_ghost_handlers — returns list (0 ghosts expected)
# ---------------------------------------------------------------------------

def test_audit_ghost_handlers_returns_list():
    result = audit_ghost_handlers(_TOOL_SCHEMAS_DEFAULT)
    assert "total_tools" in result
    assert "ghost_handlers" in result
    assert "registered_count" in result
    assert isinstance(result["ghost_handlers"], list)
    assert result["total_tools"] > 0
    assert result["registered_count"] >= 0
    # registered_count + ghosts = total_tools
    assert result["registered_count"] + len(result["ghost_handlers"]) == result["total_tools"]


def test_audit_ghost_handlers_result_is_sorted_list():
    """ghost_handlers must be a sorted list; registered_count must add up."""
    result = audit_ghost_handlers(_TOOL_SCHEMAS_DEFAULT)
    ghosts = result["ghost_handlers"]
    assert isinstance(ghosts, list)
    # List must be sorted (alphabetical)
    assert ghosts == sorted(ghosts)
    # registered_count + ghost count = total
    assert result["registered_count"] + len(ghosts) == result["total_tools"]
    # None-sentinel ros2 tools must NOT appear in ghost list (they are registered)
    ros2_sentinel = "ros2_connect"
    assert ros2_sentinel not in ghosts, (
        f"{ros2_sentinel} is a None-sentinel, not a true ghost — should not appear in list"
    )


# ---------------------------------------------------------------------------
# 5. audit_phase_completion — real yaml, >= 80 landed
# ---------------------------------------------------------------------------

def test_audit_phase_completion_real_yaml():
    result = audit_phase_completion(_METADATA_DEFAULT)
    assert "landed" in result
    assert "scaffold" in result
    assert "total" in result
    assert "landed_pct" in result
    assert result["total"] > 0
    assert result["landed"] >= 80, (
        f"Expected >= 80 landed phases, found {result['landed']}"
    )
    assert 0.0 <= result["landed_pct"] <= 100.0


def test_audit_phase_completion_missing_file(tmp_path: Path):
    missing = tmp_path / "no_such_file.yaml"
    result = audit_phase_completion(missing)
    assert result["landed"] == 0
    assert result["total"] == 0
    assert "warnings" in result


# ---------------------------------------------------------------------------
# 6. run_full_audit — dict shape
# ---------------------------------------------------------------------------

def test_run_full_audit_dict_shape():
    report = run_full_audit()
    assert "timestamp" in report
    assert "tool_executor_size" in report
    assert "ghost_handlers" in report
    assert "phase_completion" in report
    # timestamp is ISO-8601
    ts = report["timestamp"]
    assert "T" in ts or "-" in ts
    # nested dicts have expected keys
    assert "lines" in report["tool_executor_size"]
    assert "ghost_handlers" in report["ghost_handlers"]
    assert "landed" in report["phase_completion"]


# ---------------------------------------------------------------------------
# 7. scripts/quarterly_audit.py via subprocess
# ---------------------------------------------------------------------------

def test_scripts_quarterly_audit_exits_zero():
    script = _REPO_ROOT / "scripts" / "quarterly_audit.py"
    assert script.exists(), f"Script not found: {script}"
    result = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True,
        text=True,
        cwd=str(_REPO_ROOT),
        timeout=60,
    )
    assert result.returncode == 0, (
        f"Script exited {result.returncode}\n"
        f"stdout: {result.stdout[:500]}\n"
        f"stderr: {result.stderr[:500]}"
    )
    # Output should contain key markdown headers
    assert "# Quarterly Audit" in result.stdout
    assert "## tool_executor.py size" in result.stdout
    assert "## Ghost handlers" in result.stdout
    assert "## Phase completion" in result.stdout


def test_scripts_quarterly_audit_writes_out_file():
    script = _REPO_ROOT / "scripts" / "quarterly_audit.py"
    with tempfile.TemporaryDirectory() as tmpdir:
        out_file = Path(tmpdir) / "report.md"
        result = subprocess.run(
            [sys.executable, str(script), "--out", str(out_file)],
            capture_output=True,
            text=True,
            cwd=str(_REPO_ROOT),
            timeout=60,
        )
        assert result.returncode == 0
        assert out_file.exists()
        content = out_file.read_text(encoding="utf-8")
        assert "# Quarterly Audit" in content
