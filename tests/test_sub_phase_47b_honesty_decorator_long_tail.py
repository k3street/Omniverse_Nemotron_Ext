"""Phase 47b contract tests — Honesty-decorator long-tail rollout.

Gate: pytest — decorator catches silent-success returns; rule catalogue
detects ≥8 silent-success patterns.
"""
from __future__ import annotations

import textwrap
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.l0

# ---------------------------------------------------------------------------
# Import helpers
# ---------------------------------------------------------------------------

MODULE = "service.isaac_assist_service.multimodal.sub_phase_47b_honesty_decorator_long_tail"


def _import():
    import importlib
    return importlib.import_module(MODULE)


# ---------------------------------------------------------------------------
# T01 — metadata
# ---------------------------------------------------------------------------

def test_metadata():
    mod = _import()
    md = mod.get_phase_metadata()
    assert md["phase"] == "47b"
    assert md["status"] == "landed"
    assert "spec_ref" in md
    assert "47b" in md["spec_ref"]


# ---------------------------------------------------------------------------
# T02 — rule catalogue size
# ---------------------------------------------------------------------------

def test_rule_catalogue_has_at_least_8_entries():
    mod = _import()
    assert len(mod.SILENT_SUCCESS_RULES) >= 8


# ---------------------------------------------------------------------------
# T03 — every rule has non-empty rule_id and description
# ---------------------------------------------------------------------------

def test_all_rules_have_non_empty_rule_id_and_description():
    mod = _import()
    for rule in mod.SILENT_SUCCESS_RULES:
        assert rule.rule_id, f"rule_id is empty for rule: {rule}"
        assert rule.description, f"description is empty for rule: {rule.rule_id}"


# ---------------------------------------------------------------------------
# T04 — detect missing_success_key
# ---------------------------------------------------------------------------

def test_audit_detects_missing_success_key():
    mod = _import()
    decorator = mod.HonestyDecorator()
    findings = decorator.audit("some_tool", {"value": 5})
    rule_ids = [f.rule_id for f in findings]
    assert "missing_success_key" in rule_ids


# ---------------------------------------------------------------------------
# T05 — detect success_true_no_proof
# ---------------------------------------------------------------------------

def test_audit_detects_success_true_no_proof():
    mod = _import()
    decorator = mod.HonestyDecorator()
    # success=True but no output/result/prim_path/value
    findings = decorator.audit("some_tool", {"success": True})
    rule_ids = [f.rule_id for f in findings]
    assert "success_true_no_proof" in rule_ids


# ---------------------------------------------------------------------------
# T06 — detect error_field_with_success_true
# ---------------------------------------------------------------------------

def test_audit_detects_error_field_with_success_true():
    mod = _import()
    decorator = mod.HonestyDecorator()
    findings = decorator.audit("some_tool", {"success": True, "error": "fail"})
    rule_ids = [f.rule_id for f in findings]
    assert "error_field_with_success_true" in rule_ids


# ---------------------------------------------------------------------------
# T07 — detect empty_output_with_success
# ---------------------------------------------------------------------------

def test_audit_detects_empty_output_with_success_dict():
    mod = _import()
    decorator = mod.HonestyDecorator()
    findings = decorator.audit("some_tool", {"success": True, "output": {}})
    rule_ids = [f.rule_id for f in findings]
    assert "empty_output_with_success" in rule_ids


def test_audit_detects_empty_output_with_success_list():
    mod = _import()
    decorator = mod.HonestyDecorator()
    findings = decorator.audit("some_tool", {"success": True, "output": []})
    rule_ids = [f.rule_id for f in findings]
    assert "empty_output_with_success" in rule_ids


# ---------------------------------------------------------------------------
# T08 — detect kit_returned_string_only
# ---------------------------------------------------------------------------

def test_audit_detects_kit_returned_string_only():
    mod = _import()
    decorator = mod.HonestyDecorator()
    findings = decorator.audit("some_tool", "just a string")
    rule_ids = [f.rule_id for f in findings]
    assert "kit_returned_string_only" in rule_ids


# ---------------------------------------------------------------------------
# T09 — detect boolean_string_confusion
# ---------------------------------------------------------------------------

def test_audit_detects_boolean_string_confusion():
    mod = _import()
    decorator = mod.HonestyDecorator()
    findings = decorator.audit("some_tool", {"success": "true"})
    rule_ids = [f.rule_id for f in findings]
    assert "boolean_string_confusion" in rule_ids


# ---------------------------------------------------------------------------
# T10 — clean dict returns no findings
# ---------------------------------------------------------------------------

def test_audit_returns_empty_on_clean_dict():
    mod = _import()
    decorator = mod.HonestyDecorator()
    findings = decorator.audit("some_tool", {"success": True, "prim_path": "/World/Cube"})
    assert findings == []


# ---------------------------------------------------------------------------
# T11 — HonestyDecorator.wrap returns a callable that runs the inner handler
# ---------------------------------------------------------------------------

def test_wrap_returns_callable_and_executes_handler():
    mod = _import()
    decorator = mod.HonestyDecorator()

    sentinel = {"called": False}

    def clean_handler() -> dict:
        sentinel["called"] = True
        return {"success": True, "prim_path": "/World/Cube"}

    wrapped = decorator.wrap(clean_handler, tool_name="clean_tool")
    assert callable(wrapped)

    result = wrapped()
    assert sentinel["called"] is True
    assert result["success"] is True


# ---------------------------------------------------------------------------
# T12 — raise_on_critical=True raises on critical-severity findings
# ---------------------------------------------------------------------------

def test_raise_on_critical_raises_for_critical_findings():
    mod = _import()
    decorator = mod.HonestyDecorator(raise_on_critical=True)

    def bad_handler() -> str:
        return "bare string result"  # triggers kit_returned_string_only (critical)

    wrapped = decorator.wrap(bad_handler, tool_name="bad_tool")
    with pytest.raises(Exception):
        wrapped()


# ---------------------------------------------------------------------------
# T13 — raise_on_critical=False does NOT raise even for critical findings
# ---------------------------------------------------------------------------

def test_raise_on_critical_false_does_not_raise():
    mod = _import()
    decorator = mod.HonestyDecorator(raise_on_critical=False)

    def bad_handler() -> str:
        return "bare string"

    wrapped = decorator.wrap(bad_handler, tool_name="bad_tool")
    result = wrapped()  # must not raise
    assert result == "bare string"


# ---------------------------------------------------------------------------
# T14 — findings attached to result dict when raise_on_critical=False
# ---------------------------------------------------------------------------

def test_findings_attached_to_result_dict():
    mod = _import()
    decorator = mod.HonestyDecorator(raise_on_critical=False)

    def missing_success_handler() -> dict:
        return {"value": 42}  # no "success" key

    wrapped = decorator.wrap(missing_success_handler, tool_name="ms_tool")
    result = wrapped()
    assert "_honesty_findings" in result
    assert isinstance(result["_honesty_findings"], list)
    assert any(f["rule_id"] == "missing_success_key" for f in result["_honesty_findings"])


# ---------------------------------------------------------------------------
# T15 — audit_handler_module flags bare-string returns
# ---------------------------------------------------------------------------

def test_audit_handler_module_flags_string_return(tmp_path: Path):
    mod = _import()
    src = textwrap.dedent("""\
        def handler_a():
            return "bare string"

        def handler_b():
            return {"success": True, "prim_path": "/World/Cube"}
    """)
    py_file = tmp_path / "sample_handlers.py"
    py_file.write_text(src, encoding="utf-8")

    report = mod.audit_handler_module(py_file)
    # handler_a returns a string — should be flagged
    assert "handler_a" in report
    flagged_tags = report["handler_a"]
    assert "string_return" in flagged_tags or "no_success_key" in flagged_tags

    # handler_b has a success key — should not appear (or have no string_return)
    if "handler_b" in report:
        assert "string_return" not in report["handler_b"]


# ---------------------------------------------------------------------------
# T16 — SilentSuccessFinding has required fields
# ---------------------------------------------------------------------------

def test_finding_fields():
    mod = _import()
    finding = mod.SilentSuccessFinding(
        rule_id="test_rule",
        tool_name="test_tool",
        severity="warn",
        detail="some detail",
    )
    assert finding.rule_id == "test_rule"
    assert finding.tool_name == "test_tool"
    assert finding.severity == "warn"
    assert finding.detail == "some detail"
