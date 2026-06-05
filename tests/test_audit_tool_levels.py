"""Phase 18b — tests for the action-level auditor.

Covers:
  * ``classify()`` on synthetic schemas (L1, L2, L3, missing key, junk value).
  * The full ``run_audit`` runs against the real ``ISAAC_SIM_TOOLS`` without
    crashing (today every tool is expected to be UNANNOTATED — that's OK;
    the assertion is only that the auditor executes end-to-end and writes
    a report).
  * ``--warn`` exits 0 even with unannotated tools.
  * ``--strict`` (default) exits 1 with unannotated tools.

The bulk annotation of ``tool_schemas.py`` is deferred to a serial pass;
these tests intentionally do NOT assert on a passing CI gate today.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.l0


_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "audit_tool_levels.py"


def _load_auditor():
    """Load ``scripts/audit_tool_levels.py`` as an importable module.

    The ``scripts/`` directory is not a Python package, so we register a
    spec for the script file directly.
    """
    if "audit_tool_levels_module" in sys.modules:
        return sys.modules["audit_tool_levels_module"]
    spec = importlib.util.spec_from_file_location(
        "audit_tool_levels_module", _SCRIPT_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["audit_tool_levels_module"] = module
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# Synthetic-schema fixtures
# ---------------------------------------------------------------------------


def _make_schema(name: str, level: str | None) -> dict:
    """Build a tool schema in the ISAAC_SIM_TOOLS shape.

    Spec example places ``x-action-level`` parallel to ``name``, i.e.,
    inside the ``function`` sub-dict.
    """
    fn: dict = {"name": name, "description": f"stub {name}"}
    if level is not None:
        fn["x-action-level"] = level
    return {"type": "function", "function": fn}


@pytest.fixture()
def auditor():
    return _load_auditor()


@pytest.fixture()
def synthetic_tools():
    """Five schemas covering every classification path."""
    return [
        _make_schema("create_prim", "L1"),
        _make_schema("build_scene_from_blueprint", "L2"),
        _make_schema("start_workflow", "L3"),
        _make_schema("unannotated_tool", None),
        _make_schema("junk_level_tool", "L9"),  # not in {L1, L2, L3}
    ]


# ---------------------------------------------------------------------------
# classify()
# ---------------------------------------------------------------------------


def test_classify_l1(auditor):
    result = auditor.classify(_make_schema("create_prim", "L1"))
    assert result.name == "create_prim"
    assert result.level == "L1"


def test_classify_l2(auditor):
    result = auditor.classify(
        _make_schema("build_scene_from_blueprint", "L2")
    )
    assert result.level == "L2"


def test_classify_l3(auditor):
    result = auditor.classify(_make_schema("start_workflow", "L3"))
    assert result.level == "L3"


def test_classify_missing_key_is_unannotated(auditor):
    result = auditor.classify(_make_schema("foo", None))
    assert result.level == auditor.UNANNOTATED == "UNANNOTATED"


def test_classify_junk_value_is_unannotated(auditor):
    result = auditor.classify(_make_schema("foo", "L9"))
    assert result.level == "UNANNOTATED"


def test_classify_all_preserves_order(auditor, synthetic_tools):
    classified = auditor.classify_all(synthetic_tools)
    levels = [c.level for c in classified]
    assert levels == ["L1", "L2", "L3", "UNANNOTATED", "UNANNOTATED"]


def test_classify_accepts_top_level_annotation(auditor):
    """Be forgiving of the alternative placement (parallel to ``function``)."""
    schema = {
        "type": "function",
        "function": {"name": "alt_placement", "description": "stub"},
        "x-action-level": "L2",
    }
    result = auditor.classify(schema)
    assert result.level == "L2"


# ---------------------------------------------------------------------------
# run_audit against the real ISAAC_SIM_TOOLS
# ---------------------------------------------------------------------------


def test_run_audit_against_real_tools_does_not_crash(
    auditor, tmp_path, capsys
):
    """End-to-end: the auditor walks the real catalogue and writes a report.

    Today every (or almost every) tool is UNANNOTATED — that's the
    expected pre-bulk-edit state. We assert that the auditor *runs* and
    produces output; we do NOT assert that the CI gate passes.
    """
    exit_code = auditor.run_audit(report_dir=tmp_path, warn_mode=True)
    # warn_mode is True → exit 0 regardless of UNANNOTATED count.
    assert exit_code == 0
    captured = capsys.readouterr()
    assert "Total tools:" in captured.out
    # A report file for today's date must have been written.
    reports = list(tmp_path.glob("tool_levels_*.md"))
    assert len(reports) == 1, f"expected one report, found {reports}"
    text = reports[0].read_text(encoding="utf-8")
    assert "Tool action-level audit" in text
    assert "Summary" in text


# ---------------------------------------------------------------------------
# CLI exit-code contract
# ---------------------------------------------------------------------------


def test_warn_flag_exits_zero_with_unannotated(
    auditor, monkeypatch, tmp_path, capsys
):
    monkeypatch.setattr(auditor, "_load_tools", lambda: [
        _make_schema("ok", "L1"),
        _make_schema("missing", None),
        _make_schema("junk", "L9"),
    ])
    exit_code = auditor.main(
        ["--warn", "--report-dir", str(tmp_path)]
    )
    assert exit_code == 0
    err = capsys.readouterr().err
    assert "WARNING" in err
    assert "2" in err  # two unannotated


def test_strict_flag_exits_one_with_unannotated(
    auditor, monkeypatch, tmp_path, capsys
):
    monkeypatch.setattr(auditor, "_load_tools", lambda: [
        _make_schema("ok", "L1"),
        _make_schema("missing", None),
    ])
    exit_code = auditor.main(
        ["--strict", "--report-dir", str(tmp_path)]
    )
    assert exit_code == 1
    err = capsys.readouterr().err
    assert "FAIL" in err


def test_default_flag_is_strict(
    auditor, monkeypatch, tmp_path
):
    """No flag = strict mode → exit 1 when something is unannotated."""
    monkeypatch.setattr(auditor, "_load_tools", lambda: [
        _make_schema("missing", None),
    ])
    exit_code = auditor.main(["--report-dir", str(tmp_path)])
    assert exit_code == 1


def test_strict_exits_zero_when_all_annotated(
    auditor, monkeypatch, tmp_path
):
    monkeypatch.setattr(auditor, "_load_tools", lambda: [
        _make_schema("ok1", "L1"),
        _make_schema("ok2", "L2"),
        _make_schema("ok3", "L3"),
    ])
    exit_code = auditor.main(
        ["--strict", "--report-dir", str(tmp_path)]
    )
    assert exit_code == 0
