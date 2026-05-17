"""
test_lint_sandbox_validation.py — Unit tests for the --validate-sandbox lint pass.

Covers the four S-series rules introduced 2026-05-17 to catch template
code-body patterns that pass --validate-tool-calls but fail in the Kit
capture-phase sandbox (defined in
service/isaac_assist_service/chat/canonical_instantiator.py).

Rules under test:
    S1_IMPORT_IN_CODE      — top-level imports
    S2_FOREIGN_API_ACCESS  — attribute access on non-tool / non-builtin / non-local
    S3_DEREF_TOOL_RESULT   — inline subscript/attribute on a tool-call expression
    S4_SANDBOX_EXEC_FAIL   — replicated capture-phase exec raises an exception

All tests are l0 (no Kit RPC, no network) — they rely only on AST + a tiny
in-process exec namespace.
"""

import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.l0

# Ensure scripts/ is importable
_SCRIPTS = Path(__file__).parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import canonical_schema as schema  # noqa: E402
from lint_canonical_templates import lint_sandbox_safety  # noqa: E402


# ── Helpers ───────────────────────────────────────────────────────────────────

def _fake_path(stem: str = "CP-SANDBOX-TEST") -> Path:
    return Path(f"/fake/templates/{stem}.json")


def _rules(issues, level=None):
    if level is None:
        return {i.rule for i in issues}
    return {i.rule for i in issues if i.level == level}


# Guard: model map must be available for sandbox validation
@pytest.fixture(scope="module")
def tool_map():
    m = schema.get_tool_model_map()
    if not m:
        pytest.skip("Tool model map unavailable (service/ not importable)")
    return m


# ── S1: imports rejected ─────────────────────────────────────────────────────

def test_s1_plain_import_emits_error(tool_map):
    """`import os` at the top of a code body must produce S1_IMPORT_IN_CODE."""
    data = {"code": "import os\nscene_summary()\n"}
    issues = lint_sandbox_safety(_fake_path(), data)
    assert "S1_IMPORT_IN_CODE" in _rules(issues, "ERROR")


def test_s1_from_import_emits_error(tool_map):
    """`from pxr import Usd` must also emit S1_IMPORT_IN_CODE."""
    data = {"code": "from pxr import Usd, Gf\nscene_summary()\n"}
    issues = lint_sandbox_safety(_fake_path(), data)
    s1_errors = [i for i in issues if i.rule == "S1_IMPORT_IN_CODE"]
    assert s1_errors
    assert any("from pxr" in i.message for i in s1_errors)


def test_s1_import_with_alias_emits_error(tool_map):
    """`import math as m` should still be flagged (alias doesn't matter)."""
    data = {"code": "import math as m\nscene_summary()\n"}
    issues = lint_sandbox_safety(_fake_path(), data)
    assert "S1_IMPORT_IN_CODE" in _rules(issues, "ERROR")


def test_s1_import_inside_string_literal_not_flagged(tool_map):
    """Embedding `import X` inside a string literal (e.g. inside a
    triple-quoted run_usd_script argument) must NOT trigger S1 — that
    string is shipped to Kit's USD script runtime, not parsed in the
    sandbox."""
    data = {
        "code": (
            'run_usd_script(script="""\n'
            'import omni\n'
            'print("inside")\n'
            '""")\n'
        ),
    }
    issues = lint_sandbox_safety(_fake_path(), data)
    assert "S1_IMPORT_IN_CODE" not in _rules(issues, "ERROR")


def test_s1_clean_code_no_error(tool_map):
    """No imports → no S1 error."""
    data = {"code": "scene_summary()\nsim_control(action='play')\n"}
    issues = lint_sandbox_safety(_fake_path(), data)
    assert "S1_IMPORT_IN_CODE" not in _rules(issues, "ERROR")


# ── S2: foreign API access ───────────────────────────────────────────────────

def test_s2_omni_attribute_access_emits_error(tool_map):
    """`omni.usd.get_context()` references foreign module → S2."""
    data = {"code": "stage = omni.usd.get_context().get_stage()\n"}
    issues = lint_sandbox_safety(_fake_path(), data)
    assert "S2_FOREIGN_API_ACCESS" in _rules(issues, "ERROR")


def test_s2_os_path_attribute_emits_error(tool_map):
    """`os.path.join(...)` flagged as foreign-module access."""
    data = {"code": "p = os.path.join('a', 'b')\n"}
    issues = lint_sandbox_safety(_fake_path(), data)
    s2 = [i for i in issues if i.rule == "S2_FOREIGN_API_ACCESS"]
    assert s2
    assert any("os" in i.message for i in s2)


def test_s2_local_variable_not_flagged(tool_map):
    """`r = some_tool(...); r.foo` — `r` is a local binding, so `r.foo`
    must NOT trigger S2 (the runtime sentinel proxies attribute access)."""
    data = {
        "code": (
            "r = scene_summary()\n"
            "x = r.foo\n"
            "y = r.bar.baz\n"
        ),
    }
    issues = lint_sandbox_safety(_fake_path(), data)
    # r is locally bound → r.foo and r.bar.baz must not emit S2
    s2_for_r = [i for i in issues if i.rule == "S2_FOREIGN_API_ACCESS" and "`r`" in i.message]
    assert not s2_for_r, f"r.* should be allowed; got: {s2_for_r}"


def test_s2_for_loop_target_not_flagged(tool_map):
    """For-loop targets are local bindings."""
    data = {
        "code": (
            "for item in [1, 2, 3]:\n"
            "    z = item.bit_length()\n"
        ),
    }
    issues = lint_sandbox_safety(_fake_path(), data)
    s2_for_item = [i for i in issues if i.rule == "S2_FOREIGN_API_ACCESS" and "`item`" in i.message]
    assert not s2_for_item, f"item.* should be allowed; got: {s2_for_item}"


def test_s2_tool_call_attribute_not_flagged(tool_map):
    """`scene_summary` is a tool name, so `scene_summary.some_attr` must
    not be flagged as foreign (even though dereffing it is independently
    caught by S3 if it's on a Call rather than a Name)."""
    data = {
        "code": "x = scene_summary\n"  # bare reference, no call
                "y = x.something\n"
    }
    issues = lint_sandbox_safety(_fake_path(), data)
    # x is locally bound; y = x.something is fine
    assert "S2_FOREIGN_API_ACCESS" not in _rules(issues, "ERROR")


# ── S3: dereference of tool-call results ─────────────────────────────────────

def test_s3_subscript_on_tool_call_emits_error(tool_map):
    """`scene_summary()['prims']` is dereferenced inline → S3."""
    data = {"code": "x = scene_summary()['prims']\n"}
    issues = lint_sandbox_safety(_fake_path(), data)
    assert "S3_DEREF_TOOL_RESULT" in _rules(issues, "ERROR")


def test_s3_attribute_on_tool_call_emits_error(tool_map):
    """`get_bounding_box(...).max` is dereferenced inline → S3."""
    data = {
        "code": "z = get_bounding_box(prim_path='/World/A').max\n",
    }
    issues = lint_sandbox_safety(_fake_path(), data)
    s3 = [i for i in issues if i.rule == "S3_DEREF_TOOL_RESULT"]
    assert s3
    assert any("get_bounding_box" in i.message for i in s3)


def test_s3_assigned_then_used_not_flagged(tool_map):
    """`r = tool(); r['x']` is fine — only the inline pattern is S3."""
    data = {
        "code": (
            "r = scene_summary()\n"
            "y = r['prims']\n"
        ),
    }
    issues = lint_sandbox_safety(_fake_path(), data)
    s3 = [i for i in issues if i.rule == "S3_DEREF_TOOL_RESULT"]
    assert not s3, f"Assigned-then-used pattern should be permitted; got: {s3}"


# ── S4: capture-phase exec smoke ─────────────────────────────────────────────

def test_s4_import_triggers_exec_fail(tool_map):
    """`import os` should ALSO trigger S4 (alongside S1)."""
    data = {"code": "import os\n"}
    issues = lint_sandbox_safety(_fake_path(), data)
    err_rules = _rules(issues, "ERROR")
    assert "S4_SANDBOX_EXEC_FAIL" in err_rules
    assert "S1_IMPORT_IN_CODE" in err_rules


def test_s4_undefined_name_triggers_exec_fail(tool_map):
    """Reference to a name that isn't a tool, builtin, or local var
    crashes at exec time → S4."""
    data = {"code": "x = some_completely_undefined_thing\n"}
    issues = lint_sandbox_safety(_fake_path(), data)
    assert "S4_SANDBOX_EXEC_FAIL" in _rules(issues, "ERROR")


def test_s4_isinstance_not_in_safe_builtins(tool_map):
    """`isinstance` is NOT in canonical_instantiator._SAFE_BUILTINS —
    a template that calls it will crash at capture-phase. The lint must
    surface this as S4 (see Repair Wave 2 commit 54bcedd)."""
    data = {
        "code": (
            "result = scene_summary()\n"
            "if isinstance(result, dict):\n"
            "    pass\n"
        ),
    }
    issues = lint_sandbox_safety(_fake_path(), data)
    assert "S4_SANDBOX_EXEC_FAIL" in _rules(issues, "ERROR")


def test_s4_clean_template_passes(tool_map):
    """A clean template using only tool calls + safe builtins must pass S4."""
    data = {
        "code": (
            "create_prim(prim_path='/World/Cube', prim_type='Cube')\n"
            "for i in range(3):\n"
            "    set_attribute(\n"
            "        prim_path=f'/World/Cube_{i}',\n"
            "        attr_name='xformOp:translate',\n"
            "        value=[0, 0, i * 0.1],\n"
            "    )\n"
            "scene_summary()\n"
        ),
    }
    issues = lint_sandbox_safety(_fake_path(), data)
    err_rules = _rules(issues, "ERROR")
    assert not err_rules, f"Clean template should pass; got errors: {err_rules}"


# ── Combined: multi-rule fixture ─────────────────────────────────────────────

def test_combined_broken_template_emits_all_four_rules(tool_map):
    """A synthetic fixture exercising every rule simultaneously."""
    data = {
        "code": (
            "import os\n"                                     # S1 + S4
            "stage = omni.usd.get_context().get_stage()\n"    # S2
            "first = scene_summary()['prims']\n"              # S3
        ),
    }
    issues = lint_sandbox_safety(_fake_path(), data)
    err_rules = _rules(issues, "ERROR")
    assert "S1_IMPORT_IN_CODE" in err_rules
    assert "S2_FOREIGN_API_ACCESS" in err_rules
    assert "S3_DEREF_TOOL_RESULT" in err_rules
    assert "S4_SANDBOX_EXEC_FAIL" in err_rules


# ── Field-handling sanity ────────────────────────────────────────────────────

def test_no_code_field_returns_empty(tool_map):
    """Templates without code or code_template produce no S-rule issues."""
    data = {"thoughts": "no code here"}
    issues = lint_sandbox_safety(_fake_path(), data)
    # May have S0_MODEL_UNAVAILABLE WARN if model map import failed; filter it
    sx = [i for i in issues if i.rule.startswith("S")]
    assert not sx, f"Empty template should produce no S-rule issues; got: {sx}"


def test_code_template_var_substitution_does_not_crash(tool_map):
    """code_template with {{role.field}} placeholders must be sanitized
    before parse — should not raise SyntaxError on the placeholders."""
    data = {
        "code_template": (
            "create_prim(prim_path={{primary_robot.path}}, prim_type='Xform')\n"
        ),
    }
    issues = lint_sandbox_safety(_fake_path(), data, include_code_template=True)
    # Should not have syntax error from {{...}}
    tc_syn = [i for i in issues if i.rule == "TC_SYNTAX_ERROR"]
    assert not tc_syn, f"Template-var sanitizer should prevent syntax errors: {tc_syn}"


def test_syntax_error_emits_warn(tool_map):
    """Unparseable code emits TC_SYNTAX_ERROR WARN (mirrors --validate-tool-calls)."""
    data = {"code": "def broken(\n"}
    issues = lint_sandbox_safety(_fake_path(), data)
    assert "TC_SYNTAX_ERROR" in _rules(issues, "WARN")


def test_s4_only_runs_on_code_field_not_code_template(tool_map):
    """S4 is restricted to the `code` field because template-var sanitizer
    string-substitution mis-types role-spec dicts; that's not a real
    sandbox failure, it's an artifact of static analysis."""
    # Code_template that would explode on naive substitution
    # (string indices must be int, etc.) — must NOT raise S4 from
    # code_template.
    data = {
        "code_template": (
            "for tray in [{{trays[0]}}, {{trays[1]}}]:\n"
            "    create_prim(prim_path=tray['path'], prim_type='Cube')\n"
        ),
    }
    issues = lint_sandbox_safety(_fake_path(), data, include_code_template=True)
    # No S4 from code_template
    s4_from_template = [i for i in issues if i.rule == "S4_SANDBOX_EXEC_FAIL"
                        and "code_template" in i.message]
    assert not s4_from_template, (
        f"S4 must NOT fire on code_template (substitution artifacts); "
        f"got: {s4_from_template}"
    )
