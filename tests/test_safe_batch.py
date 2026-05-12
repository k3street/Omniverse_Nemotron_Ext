"""Phase 2b — tests for the parallelization-safety helpers.

Three layers:
1. The phase-file-writes audit correctly extracts `Files (...)` blocks
   from synthetic spec fragments.
2. `safe_batch.check_batch()` returns green for disjoint phases and red
   for conflicting phases (per the spec's two example cases).
3. The handler cross-ref audit AST-walks a synthetic module and finds
   the expected edges.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.l0

_REPO_ROOT = Path(__file__).parent.parent


def _load_script(name: str):
    """Load `scripts/<name>.py` as a module (script dirs aren't packages)."""
    path = _REPO_ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def safe_batch():
    return _load_script("safe_batch")


@pytest.fixture(scope="module")
def apw():
    return _load_script("audit_phase_file_writes")


@pytest.fixture(scope="module")
def ahcr():
    return _load_script("audit_handler_cross_refs")


# ---------------------------------------------------------------------------
# safe_batch.check_batch — green / red verdict


def _audit_fixture(phases_map: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Build the same shape `audit_phase_file_writes` produces."""
    from collections import defaultdict

    file_to_phases: dict[str, list[str]] = defaultdict(list)
    for pid, pw in phases_map.items():
        for f in pw.get("files_changes", []) + pw.get("files_new", []):
            file_to_phases[f].append(pid)
    return {
        "phases": phases_map,
        "file_to_phases": dict(file_to_phases),
    }


def test_green_for_disjoint_phases(safe_batch):
    """Spec example 1: `safe_batch.py 73 49b 90` → green."""
    audit = _audit_fixture(
        {
            "73": {
                "phase_id": "73",
                "files_changes": ["workspace/knowledge/sensor_specs.jsonl"],
                "files_new": [],
                "line_start": 100,
            },
            "49b": {
                "phase_id": "49b",
                "files_changes": ["diagnose/cache.py", "diagnose/tool.py"],
                "files_new": ["tests/test_diagnose_cache_invalidation.py"],
                "line_start": 200,
            },
            "90": {
                "phase_id": "90",
                "files_changes": ["service/isaac_assist_service/governance/secret_redactor.py"],
                "files_new": [],
                "line_start": 300,
            },
        }
    )
    parallel_safe, conflicts, unknown = safe_batch.check_batch(
        ["73", "49b", "90"], audit
    )
    assert parallel_safe is True
    assert conflicts == {}
    assert unknown == []


def test_red_for_shared_file(safe_batch):
    """Spec example 2: `safe_batch.py 70b 70c 70d` → red on handlers/robot.py."""
    audit = _audit_fixture(
        {
            "70b": {
                "phase_id": "70b",
                "files_changes": ["handlers/robot.py", "tool_schemas.py"],
                "files_new": [],
                "line_start": 100,
            },
            "70c": {
                "phase_id": "70c",
                "files_changes": ["handlers/robot.py"],
                "files_new": ["handlers/articulated_pull.py"],
                "line_start": 200,
            },
            "70d": {
                "phase_id": "70d",
                "files_changes": ["handlers/robot.py"],
                "files_new": ["data/bin_interior_metadata.yaml"],
                "line_start": 300,
            },
        }
    )
    parallel_safe, conflicts, unknown = safe_batch.check_batch(
        ["70b", "70c", "70d"], audit
    )
    assert parallel_safe is False
    assert "handlers/robot.py" in conflicts
    assert set(conflicts["handlers/robot.py"]) == {"70b", "70c", "70d"}
    assert unknown == []


def test_red_two_phase_conflict(safe_batch):
    audit = _audit_fixture(
        {
            "A": {"phase_id": "A", "files_changes": ["x.py"], "files_new": [], "line_start": 1},
            "B": {"phase_id": "B", "files_changes": ["x.py"], "files_new": [], "line_start": 2},
        }
    )
    safe, conflicts, _ = safe_batch.check_batch(["A", "B"], audit)
    assert safe is False
    assert conflicts == {"x.py": ["A", "B"]}


def test_unknown_phase_id_reported(safe_batch):
    audit = _audit_fixture({"A": {"phase_id": "A", "files_changes": [], "files_new": [], "line_start": 1}})
    safe, conflicts, unknown = safe_batch.check_batch(["A", "ZZ"], audit)
    assert safe is False
    assert unknown == ["ZZ"]


# ---------------------------------------------------------------------------
# audit_phase_file_writes — parsing


def test_apw_extracts_files_blocks(apw, tmp_path):
    """Synthetic spec fragment with Files blocks gets parsed correctly."""
    spec = textwrap.dedent(
        """
        # Header

        ## Phase 1 — Some phase

        **Goal:** ...

        **Files (changes):**
        - `path/to/changed.py`
        - `other/file.py`

        **Files (new):**
        - `new/file.py`

        ---

        ## Phase 2 — Another

        **Files (new):**
        - `path/to/changed.py`

        ---
        """
    )
    p = tmp_path / "spec.md"
    p.write_text(spec)
    matrix = apw.build_matrix(p)
    assert "1" in matrix.phases
    assert "2" in matrix.phases
    assert matrix.phases["1"].files_changes == ["path/to/changed.py", "other/file.py"]
    assert matrix.phases["1"].files_new == ["new/file.py"]
    assert matrix.phases["2"].files_new == ["path/to/changed.py"]
    # Shared file detected
    assert sorted(matrix.file_to_phases["path/to/changed.py"]) == ["1", "2"]


def test_apw_handles_b_suffixed_phase_ids(apw, tmp_path):
    spec = textwrap.dedent(
        """
        ## Phase 0b — Foo

        **Files (new):**
        - `audit.py`

        ---

        ## Phase 17b — Bar

        **Files (new):**
        - `lint.py`
        """
    )
    p = tmp_path / "spec.md"
    p.write_text(spec)
    matrix = apw.build_matrix(p)
    assert "0b" in matrix.phases
    assert "17b" in matrix.phases


# ---------------------------------------------------------------------------
# audit_handler_cross_refs — AST walking


def test_ahcr_finds_handler_to_utility_edges(ahcr, tmp_path):
    """Synthetic module: two handlers calling one shared utility."""
    src = textwrap.dedent(
        """
        def _safe_set_translate(p, v):
            return v

        def _handle_create_cube(args):
            return _safe_set_translate("/World/Cube", [0, 0, 0])

        def _handle_create_sphere(args):
            return _safe_set_translate("/World/Sphere", [1, 0, 0])

        def _handle_unrelated(args):
            return None
        """
    )
    p = tmp_path / "fake_executor.py"
    p.write_text(src)
    report = ahcr.audit(p, min_fan_in=2)
    assert "_safe_set_translate" in report.utilities
    assert report.utility_fan_in.get("_safe_set_translate") == 2
    assert "_safe_set_translate" in report.high_fan_in_utilities
    assert ("_handle_create_cube", "_safe_set_translate") in [tuple(e) for e in report.edges]
    assert ("_handle_create_sphere", "_safe_set_translate") in [tuple(e) for e in report.edges]


def test_ahcr_threshold_filters_low_fan_in(ahcr, tmp_path):
    """A utility called by only 1 handler should not appear in high-fan-in."""
    src = textwrap.dedent(
        """
        def _util_a(): pass
        def _handle_x(args): return _util_a()
        """
    )
    p = tmp_path / "fake_executor.py"
    p.write_text(src)
    report = ahcr.audit(p, min_fan_in=2)
    assert "_util_a" not in report.high_fan_in_utilities


# ---------------------------------------------------------------------------
# End-to-end: run the auditors against the real spec + tool_executor


def test_apw_runs_against_real_spec(apw):
    """Audit must complete on the real IA_FULL_SPEC and produce ≥ 100 phases."""
    matrix = apw.build_matrix()
    assert len(matrix.phases) >= 100, f"Expected ≥100 phases, got {len(matrix.phases)}"
    # Phase 0b should be parsed
    assert "0b" in matrix.phases


def test_ahcr_runs_against_real_executor(ahcr):
    """Audit must complete on the real tool_executor.py and find some edges."""
    report = ahcr.audit()
    assert len(report.handlers) > 100, "Expected many handlers in tool_executor.py"
    assert len(report.edges) > 30, "Expected at least some handler→utility edges"


def test_safe_batch_round_trip_through_disk(safe_batch, apw, tmp_path):
    """`safe_batch` can load an audit JSON file from disk."""
    matrix = apw.build_matrix()
    audit_data = {
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
    p = tmp_path / "audit.json"
    p.write_text(json.dumps(audit_data))
    loaded = safe_batch._load_or_build_audit(p)
    assert "phases" in loaded
    assert "file_to_phases" in loaded
