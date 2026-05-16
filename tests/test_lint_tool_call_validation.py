"""
test_lint_tool_call_validation.py — Unit tests for the --validate-tool-calls lint pass.

All tests use small inline fixtures; no dependency on live workspace templates.
Tests are marked l0 (fast, no network, no Kit RPC).
"""

import json
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.l0

# Ensure scripts/ is importable
_SCRIPTS = Path(__file__).parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import canonical_schema as schema  # noqa: E402
from lint_canonical_templates import lint_tool_calls  # noqa: E402


# ── Helpers ───────────────────────────────────────────────────────────────────

def _fake_path(stem: str = "CP-TEST") -> Path:
    return Path(f"/fake/templates/{stem}.json")


def _issues_by_rule(issues, rule_prefix: str):
    return [i for i in issues if i.rule.startswith(rule_prefix)]


def _error_rules(issues):
    return {i.rule for i in issues if i.level == "ERROR"}


def _warn_rules(issues):
    return {i.rule for i in issues if i.level == "WARN"}


# ── Guard: model map must be available ───────────────────────────────────────

@pytest.fixture(scope="module")
def tool_map():
    m = schema.get_tool_model_map()
    if not m:
        pytest.skip("Tool model map unavailable (service/ not importable)")
    return m


# ── Test 1: all-correct tool calls → no issues ───────────────────────────────

def test_correct_tool_calls_no_issues(tool_map):
    """
    sim_control(action='play') has all required fields → no errors or warnings.
    """
    data = {
        "code": "sim_control(action='play')\n",
    }
    issues = lint_tool_calls(_fake_path(), data)
    # Filter out TC_MODEL_UNAVAILABLE in case import failed after fixture guard
    real = [i for i in issues if i.rule != "TC_MODEL_UNAVAILABLE"]
    assert not real, f"Unexpected issues: {real}"


# ── Test 2: unknown kwarg → TC_UNKNOWN_KWARG WARN ────────────────────────────

def test_unknown_kwarg_emits_warn(tool_map):
    """
    sim_control(action='play', nonexistent_kwarg=True) → TC_UNKNOWN_KWARG WARN.
    """
    data = {
        "code": "sim_control(action='play', nonexistent_kwarg=True)\n",
    }
    issues = lint_tool_calls(_fake_path(), data)
    warn_rules = _warn_rules(issues)
    assert "TC_UNKNOWN_KWARG" in warn_rules, (
        f"Expected TC_UNKNOWN_KWARG in warns, got: {warn_rules}"
    )


# ── Test 3: missing required field → TC_REQUIRED_MISSING ERROR ───────────────

def test_missing_required_field_emits_error(tool_map):
    """
    sim_control() with no 'action' kwarg → TC_REQUIRED_MISSING ERROR.
    sim_control.action is required per _models.SimControlArgs.
    """
    data = {
        "code": "sim_control()\n",
    }
    issues = lint_tool_calls(_fake_path(), data)
    err_rules = _error_rules(issues)
    assert "TC_REQUIRED_MISSING" in err_rules, (
        f"Expected TC_REQUIRED_MISSING in errors, got: {err_rules}"
    )


# ── Test 4: tool not in _models.py → TC_UNKNOWN_TOOL WARN ────────────────────

def test_unknown_tool_name_emits_warn(tool_map):
    """
    A call to a snake_case function not in the model map → TC_UNKNOWN_TOOL WARN.
    'completely_fictitious_tool' contains underscores and is not a builtin.
    """
    data = {
        "code": "completely_fictitious_tool(foo='bar')\n",
    }
    issues = lint_tool_calls(_fake_path(), data)
    warn_rules = _warn_rules(issues)
    assert "TC_UNKNOWN_TOOL" in warn_rules, (
        f"Expected TC_UNKNOWN_TOOL in warns, got: {warn_rules}"
    )


# ── Test 5: code_template with {{vars}} sanitizes correctly ──────────────────

def test_code_template_sanitized_correctly(tool_map):
    """
    code_template containing {{role.field}} placeholders is parsed after
    substitution.  A correct call with all-template-var args should produce
    zero TC_REQUIRED_MISSING errors (since the template sentinel string IS
    a value, satisfying presence checks — we do not type-check sentinel
    values against List[float] etc.).
    """
    code_template = (
        "sim_control(action={{ctrl.action}})\n"
    )
    data = {"code_template": code_template}
    issues = lint_tool_calls(_fake_path(), data, include_code_template=True)
    # Should have no syntax error and no required-missing error
    err_rules = _error_rules(issues)
    tc_syn = [i for i in issues if i.rule == "TC_SYNTAX_ERROR"]
    assert not tc_syn, f"Unexpected syntax errors: {tc_syn}"
    assert "TC_REQUIRED_MISSING" not in err_rules, (
        "Template-var value should satisfy presence check; "
        f"got errors: {err_rules}"
    )


# ── Test 6: code_template with a MISSING required field → ERROR ───────────────

def test_code_template_missing_required_emits_error(tool_map):
    """
    code_template calling sim_control() with no args, not even a template var,
    should still surface TC_REQUIRED_MISSING.
    """
    code_template = "sim_control()\n"
    data = {"code_template": code_template}
    issues = lint_tool_calls(_fake_path(), data, include_code_template=True)
    err_rules = _error_rules(issues)
    assert "TC_REQUIRED_MISSING" in err_rules, (
        f"Expected TC_REQUIRED_MISSING in errors, got: {err_rules}"
    )


# ── Test 7: **kwargs unpacking skips required-field check ────────────────────

def test_star_kwargs_skips_required_check(tool_map):
    """
    A call using **kwargs unpacking is unpredictable at static-analysis time;
    we must not emit TC_REQUIRED_MISSING for it.
    """
    data = {
        "code": (
            "params = {'action': 'play'}\n"
            "sim_control(**params)\n"
        ),
    }
    issues = lint_tool_calls(_fake_path(), data)
    err_rules = _error_rules(issues)
    assert "TC_REQUIRED_MISSING" not in err_rules, (
        f"Should not flag required-missing for **kwargs call; got: {err_rules}"
    )


# ── Test 8: Python builtins are silently skipped ──────────────────────────────

def test_python_builtins_not_flagged(tool_map):
    """
    Calls to Python builtins (range, print, enumerate, sorted, etc.) should
    NOT produce TC_UNKNOWN_TOOL warnings.
    """
    data = {
        "code": (
            "for i in range(10):\n"
            "    print(i)\n"
            "items = sorted([3, 1, 2])\n"
            "for j, v in enumerate(items):\n"
            "    pass\n"
        ),
    }
    issues = lint_tool_calls(_fake_path(), data)
    tc_unknown = [i for i in issues if i.rule == "TC_UNKNOWN_TOOL"]
    assert not tc_unknown, f"Builtins should not be flagged; got: {tc_unknown}"


# ── Test 9: multiple errors in one code block ────────────────────────────────

def test_multiple_errors_reported(tool_map):
    """
    Two broken calls → two TC_REQUIRED_MISSING errors, one per call site.
    """
    data = {
        "code": (
            "sim_control()\n"          # missing 'action'
            "delete_prim()\n"           # missing 'prim_path'
        ),
    }
    issues = lint_tool_calls(_fake_path(), data)
    missing_errs = [i for i in issues if i.rule == "TC_REQUIRED_MISSING"]
    assert len(missing_errs) >= 2, (
        f"Expected at least 2 TC_REQUIRED_MISSING, got: {missing_errs}"
    )


# ── Test 10: no 'code' field → zero issues ───────────────────────────────────

def test_no_code_field_returns_empty(tool_map):
    """Templates without a 'code' field produce no tool-call issues."""
    data = {"thoughts": "no code here"}
    issues = lint_tool_calls(_fake_path(), data)
    assert issues == [], f"Expected empty list, got: {issues}"


# ── Test 11: SyntaxError in code field → TC_SYNTAX_ERROR WARN ─────────────────

def test_syntax_error_emits_warn(tool_map):
    """Unparseable code emits TC_SYNTAX_ERROR WARN and does not crash."""
    data = {"code": "def broken(\n"}  # unclosed paren
    issues = lint_tool_calls(_fake_path(), data)
    warn_rules = _warn_rules(issues)
    assert "TC_SYNTAX_ERROR" in warn_rules, (
        f"Expected TC_SYNTAX_ERROR in warns, got: {warn_rules}"
    )


# ── Test 12: TC_REQUIRED_MISSING message includes field names ─────────────────

def test_required_missing_message_includes_field_names(tool_map):
    """
    The TC_REQUIRED_MISSING message must name the missing field(s)
    so developers know exactly what to add.
    """
    data = {"code": "sim_control()\n"}
    issues = lint_tool_calls(_fake_path(), data)
    missing_errs = [i for i in issues if i.rule == "TC_REQUIRED_MISSING"]
    assert missing_errs, "No TC_REQUIRED_MISSING emitted"
    msg = missing_errs[0].message
    assert "action" in msg, (
        f"Expected 'action' in error message, got: {msg!r}"
    )


# ── Test 13: TC_UNKNOWN_KWARG message includes kwarg name ────────────────────

def test_unknown_kwarg_message_includes_kwarg_name(tool_map):
    """TC_UNKNOWN_KWARG message must name the offending kwarg."""
    data = {
        "code": "sim_control(action='play', totally_fake_arg=99)\n",
    }
    issues = lint_tool_calls(_fake_path(), data)
    unknown_warns = [i for i in issues if i.rule == "TC_UNKNOWN_KWARG"]
    assert unknown_warns, "No TC_UNKNOWN_KWARG emitted"
    msg = unknown_warns[0].message
    assert "totally_fake_arg" in msg, (
        f"Expected 'totally_fake_arg' in warn message, got: {msg!r}"
    )


# ── Test 14: tool with no required fields → no TC_REQUIRED_MISSING ───────────

def test_tool_with_no_required_fields(tool_map):
    """
    scene_summary() has no required fields per the schema.
    Calling it with no args must not emit TC_REQUIRED_MISSING.
    """
    data = {"code": "scene_summary()\n"}
    issues = lint_tool_calls(_fake_path(), data)
    err_rules = _error_rules(issues)
    assert "TC_REQUIRED_MISSING" not in err_rules, (
        f"scene_summary has no required fields; got: {err_rules}"
    )


# ── Test 15: include_code_template=False skips code_template ─────────────────

def test_include_code_template_false_skips_template(tool_map):
    """
    When include_code_template=False (the default), errors in code_template
    are not reported.
    """
    data = {
        "code": "sim_control(action='play')\n",
        "code_template": "sim_control()\n",  # missing 'action' — ERROR if checked
    }
    # Default: do not include code_template
    issues = lint_tool_calls(_fake_path(), data, include_code_template=False)
    err_rules = _error_rules(issues)
    assert "TC_REQUIRED_MISSING" not in err_rules, (
        "code_template should not be checked when include_code_template=False"
    )
