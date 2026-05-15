"""
test_canonical_lint.py — Unit tests for the canonical template lint script.

Uses small inline JSON fixtures; no dependency on real workspace/templates/*.json files.
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
from lint_canonical_templates import lint_one, apply_fixes  # noqa: E402


# ── Fixture helpers ───────────────────────────────────────────────────────────

def _make_path(stem: str) -> Path:
    """Return a fake Path whose stem is the given task_id, for lint_one()."""
    # We never read this path; it's only used for task_id-mismatch checks.
    return Path(f"/fake/templates/{stem}.json")


def _t2_base(task_id="A-01") -> dict:
    """Minimal valid T2 (non-CP dialogue) template."""
    return {
        "task_id": task_id,
        "goal": "Test goal description.",
        "tools_used": ["import_robot", "sim_control"],
        "thoughts": "Some coaching text.",
        "code": "import_robot(urdf_path='/tmp/arm.urdf')\nsim_control(action='play')",
        "failure_modes": ["Robot falls through floor if fix_base omitted"],
    }


def _t1_base(task_id="CP-10") -> dict:
    """Minimal valid T1 (CP) template — no role fields, no settle_state."""
    return {
        "task_id": task_id,
        "goal": "Pick-and-place test template.",
        "tools_used": ["robot_wizard", "create_conveyor", "setup_pick_place_controller"],
        "thoughts": "CPU dynamics required for belt surface velocity.",
        "code": "robot_wizard(robot_name='franka_panda', dest_path='/World/Franka')\n",
        "failure_modes": ["Belt does not move cubes under GPU broadphase"],
        "verify_args": {
            "stages": [
                {"robot_path": "/World/Franka", "pick_path": "/World/ConveyorBelt", "place_path": "/World/Bin"}
            ]
        },
        "simulate_args": {
            "cube_path": "/World/Cube_1",
            "target_path": "/World/Bin",
            "duration_s": 120,
        },
        "diagnose_args": {"robot_path": "/World/Franka"},
        "verified_status": "build-spec-2026-05-08; form-gate ✓; function-gate ✓",
        "extends": "CP-01",
        "extension_notes": "Extends CP-01 with a second belt.",
        "settle_state": {
            "cubes": {"/World/Cube_1": [0.4, 0.4, 0.835]},
            "conveyors": {"/World/ConveyorBelt": [0.2, 0.0, 0.0]},
        },
    }


def issues_by_level(issues, level):
    return [i for i in issues if i.level == level]


# ── Test 1: Clean T2 template — no issues ─────────────────────────────────────

def test_clean_t2_template():
    data = _t2_base()
    issues = lint_one(_make_path("A-01"), data)
    assert issues == [], f"Expected no issues, got: {issues}"


# ── Test 2: Clean T1 template — no ERRORs ─────────────────────────────────────

def test_clean_t1_template():
    data = _t1_base()
    issues = lint_one(_make_path("CP-10"), data)
    errors = issues_by_level(issues, "ERROR")
    assert errors == [], f"Expected no ERRORs, got: {errors}"
    # May have INFOs (missing intent/roles are INFO)
    infos = issues_by_level(issues, "INFO")
    assert any("intent" in i.message or "role" in i.message.lower() for i in infos), (
        "Expected INFO about missing intent/role fields"
    )


# ── Test 3: Missing mandatory core field → ERROR ───────────────────────────────

def test_missing_core_field_goal():
    data = _t2_base()
    del data["goal"]
    issues = lint_one(_make_path("A-01"), data)
    errors = issues_by_level(issues, "ERROR")
    assert any(i.rule == "C1_MISSING_CORE_FIELD" and "goal" in i.message for i in errors), (
        f"Expected C1_MISSING_CORE_FIELD for 'goal', got: {errors}"
    )


def test_missing_core_field_code():
    data = _t2_base()
    del data["code"]
    issues = lint_one(_make_path("A-01"), data)
    errors = issues_by_level(issues, "ERROR")
    assert any(i.rule == "C1_MISSING_CORE_FIELD" and "code" in i.message for i in errors)


def test_empty_core_field():
    data = _t2_base()
    data["failure_modes"] = []
    issues = lint_one(_make_path("A-01"), data)
    errors = issues_by_level(issues, "ERROR")
    assert any(i.rule == "C1_EMPTY_CORE_FIELD" and "failure_modes" in i.message for i in errors)


# ── Test 4: task_id mismatch → ERROR ──────────────────────────────────────────

def test_task_id_mismatch():
    data = _t2_base(task_id="A-99")
    # File stem is A-01, but task_id says A-99
    issues = lint_one(_make_path("A-01"), data)
    errors = issues_by_level(issues, "ERROR")
    assert any(i.rule == "C2_TASK_ID_MISMATCH" for i in errors), (
        f"Expected C2_TASK_ID_MISMATCH, got: {errors}"
    )


# ── Test 5: Deprecated field present → ERROR ──────────────────────────────────

def test_deprecated_field_delivery():
    data = _t1_base()
    data["delivery"] = {"cubes": 4}
    issues = lint_one(_make_path("CP-10"), data)
    errors = issues_by_level(issues, "ERROR")
    assert any(i.rule == "DEP_FIELD_PRESENT" and "delivery" in i.message for i in errors), (
        f"Expected DEP_FIELD_PRESENT for 'delivery', got: {errors}"
    )


def test_deprecated_field_extends_notes():
    data = _t1_base()
    data["extends_notes"] = "Typo of extension_notes"
    issues = lint_one(_make_path("CP-10"), data)
    errors = issues_by_level(issues, "ERROR")
    assert any(i.rule == "DEP_FIELD_PRESENT" and "extends_notes" in i.message for i in errors)


def test_deprecated_field_compute_stack_prefix():
    data = _t1_base()
    data["compute_stack_placement_verified_2026_05_07"] = {"note": "memo"}
    issues = lint_one(_make_path("CP-10"), data)
    errors = issues_by_level(issues, "ERROR")
    assert any(
        i.rule == "DEP_FIELD_PRESENT" and "compute_stack_placement_verified_" in i.message
        for i in errors
    )


# ── Test 6: Missing T1-mandatory field (verify_args) → ERROR ─────────────────

def test_missing_t1_field_verify_args():
    data = _t1_base()
    del data["verify_args"]
    issues = lint_one(_make_path("CP-10"), data)
    errors = issues_by_level(issues, "ERROR")
    assert any(i.rule == "T1_MISSING_FIELD" and "verify_args" in i.message for i in errors)


# ── Test 7: Missing recommended field (settle_state) → WARN ──────────────────

def test_missing_settle_state_warn():
    data = _t1_base()
    del data["settle_state"]
    issues = lint_one(_make_path("CP-10"), data)
    warns = issues_by_level(issues, "WARN")
    assert any(i.rule == "T1_MISSING_SETTLE_STATE" for i in warns), (
        f"Expected T1_MISSING_SETTLE_STATE WARN, got: {warns}"
    )


# ── Test 8: extends without extension_notes → ERROR ───────────────────────────

def test_extends_without_extension_notes():
    data = _t1_base()
    data["extends"] = "CP-01"
    del data["extension_notes"]
    issues = lint_one(_make_path("CP-10"), data)
    errors = issues_by_level(issues, "ERROR")
    assert any(i.rule == "T1_EXTENDS_NO_NOTES" for i in errors)


# ── Test 9: Missing role fields → INFO (not ERROR) ───────────────────────────

def test_missing_role_fields_is_info_not_error():
    data = _t1_base()
    # No intent, roles, role_defaults, code_template
    issues = lint_one(_make_path("CP-10"), data)
    errors = issues_by_level(issues, "ERROR")
    role_errors = [e for e in errors if "role" in e.rule.lower() or "intent" in e.rule.lower()]
    assert role_errors == [], (
        f"Missing role fields should be INFO, not ERROR; got errors: {role_errors}"
    )
    infos = issues_by_level(issues, "INFO")
    assert any("R1_MISSING_INTENT" == i.rule for i in infos)
    assert any("R2_MISSING_ROLE_FIELDS" == i.rule for i in infos)


# ── Test 10: Partial role fields (roles without code_template) → ERROR ────────

def test_partial_role_fields_error():
    data = _t1_base()
    data["roles"] = {"primary_robot": {"constraints": ["franka_panda"], "expected_count": 1, "required": True}}
    data["role_defaults"] = {"primary_robot": {"path": "/World/Franka"}}
    # code_template is missing → partial trio
    issues = lint_one(_make_path("CP-10"), data)
    errors = issues_by_level(issues, "ERROR")
    assert any(i.rule == "R2_PARTIAL_ROLE_FIELDS" for i in errors), (
        f"Expected R2_PARTIAL_ROLE_FIELDS error for partial role fields, got: {errors}"
    )


# ── Test 11: --fix flag idempotency ──────────────────────────────────────────

def test_fix_flag_idempotency():
    """Applying fixes twice should produce the same result as applying once."""
    data = _t1_base()
    del data["verified_status"]
    path = _make_path("CP-10")

    issues1 = lint_one(path, data)
    data_copy1 = json.loads(json.dumps(data))  # deep copy
    fixed1, _ = apply_fixes(path, data_copy1, issues1)

    issues2 = lint_one(path, fixed1)
    data_copy2 = json.loads(json.dumps(fixed1))  # deep copy after first fix
    fixed2, _ = apply_fixes(path, data_copy2, issues2)

    assert fixed1.get("verified_status") == fixed2.get("verified_status"), (
        "Fix idempotency failed: verified_status changed on second application"
    )
    assert fixed1.get("verified_status") == "draft", (
        f"Expected 'draft' as default verified_status, got {fixed1.get('verified_status')!r}"
    )


# ── Test 12: blocked CP-06 pattern — no false DEP errors ─────────────────────

def test_blocked_template_no_false_dep_errors():
    """CP-06-style blocked template: `blocked` field must NOT trigger DEP_FIELD_PRESENT."""
    data = _t1_base(task_id="CP-06")
    data["blocked"] = {
        "since": "2026-05-07",
        "status": "infrastructure-built-but-controller-fails-delivery",
        "reason": "Cube never reaches bin.",
        "next_steps": ["Fix FixedJoint attachment"],
    }
    # CP-06 is allowed to omit verified_status and diagnose_args when blocked
    del data["verified_status"]
    del data["diagnose_args"]
    issues = lint_one(_make_path("CP-06"), data)
    dep_errors = [i for i in issues if i.rule == "DEP_FIELD_PRESENT" and "blocked" in i.message]
    assert dep_errors == [], f"blocked field should not be flagged as deprecated: {dep_errors}"


# ── Test 13: Invalid intent.pattern_hint → ERROR ──────────────────────────────

def test_invalid_pattern_hint():
    data = _t1_base()
    data["intent"] = {
        "pattern_hint": "teleport",  # invalid
        "structural_tags": ["isaac:robot.fixed_base.arm"],
    }
    data["roles"] = {"primary_robot": {"constraints": ["franka_panda"], "expected_count": 1, "required": True}}
    data["role_defaults"] = {"primary_robot": {"path": "/World/Franka"}}
    data["code_template"] = "robot_wizard(robot_name='{{primary_robot.name}}')"
    issues = lint_one(_make_path("CP-10"), data)
    errors = issues_by_level(issues, "ERROR")
    assert any(i.rule == "R1_BAD_PATTERN_HINT" for i in errors), (
        f"Expected R1_BAD_PATTERN_HINT, got: {errors}"
    )


# ── Test 14: Non-CP template does not get T1 rules applied ───────────────────

def test_non_cp_no_t1_rules():
    """Dialogue templates (A/D/E/etc.) must not be checked for T1 fields."""
    data = _t2_base(task_id="D-07")
    # No verify_args etc. — that's correct for T2
    issues = lint_one(_make_path("D-07"), data)
    t1_errors = [i for i in issues if i.rule.startswith("T1_")]
    assert t1_errors == [], (
        f"T1 rules should not apply to non-CP templates, got: {t1_errors}"
    )


# ── Test 15: motion_controllers WARN when motion-planning tool used ──────────

def test_motion_controllers_warn_when_tool_used():
    """T1 template that uses move_to_pose but lacks motion_controllers → WARN."""
    data = _t1_base()
    data["tools_used"] = ["robot_wizard", "move_to_pose", "create_bin"]
    issues = lint_one(_make_path("CP-10"), data)
    warns = issues_by_level(issues, "WARN")
    assert any(i.rule == "T1_MC_MISSING" for i in warns), (
        f"Expected T1_MC_MISSING WARN, got: {[(i.rule, i.level) for i in issues]}"
    )


def test_motion_controllers_info_when_no_motion_tool():
    """T1 template with no motion-planning tools → INFO only (not WARN)."""
    data = _t1_base()
    data["tools_used"] = ["robot_wizard", "create_bin", "create_conveyor"]
    issues = lint_one(_make_path("CP-10"), data)
    warns = [i for i in issues_by_level(issues, "WARN") if i.rule.startswith("T1_MC")]
    infos = [i for i in issues_by_level(issues, "INFO") if i.rule.startswith("T1_MC")]
    assert warns == [], f"Should not WARN when no motion tool, got: {warns}"
    assert any(i.rule == "T1_MC_MISSING_INFO" for i in infos), (
        f"Expected T1_MC_MISSING_INFO INFO, got: {[(i.rule, i.level) for i in issues]}"
    )


def test_motion_controllers_valid_structure():
    """Well-formed motion_controllers dict → no T1_MC issues."""
    data = _t1_base()
    data["tools_used"] = ["move_to_pose", "plan_trajectory"]
    data["motion_controllers"] = {
        "verified": ["curobo@1.8.2", "rmpflow"],
        "failed": {"admittance": "physx_instability_at_contact"},
        "untested": ["moveit2"],
    }
    issues = lint_one(_make_path("CP-10"), data)
    mc_issues = [i for i in issues if i.rule.startswith("T1_MC")]
    assert mc_issues == [], f"Valid motion_controllers should produce no T1_MC issues, got: {mc_issues}"


def test_motion_controllers_unknown_name_warn():
    """Unknown controller name in verified → WARN (typo guard)."""
    data = _t1_base()
    data["tools_used"] = ["move_to_pose"]
    data["motion_controllers"] = {"verified": ["curoboo"]}  # typo
    issues = lint_one(_make_path("CP-10"), data)
    warns = [i for i in issues if i.rule == "T1_MC_UNKNOWN_NAME"]
    assert warns, f"Expected T1_MC_UNKNOWN_NAME WARN, got: {[(i.rule, i.level) for i in issues]}"


def test_motion_controllers_bad_type_error():
    """motion_controllers as list (not dict) → ERROR."""
    data = _t1_base()
    data["tools_used"] = ["move_to_pose"]
    data["motion_controllers"] = ["curobo"]  # wrong shape
    issues = lint_one(_make_path("CP-10"), data)
    errs = [i for i in issues if i.rule == "T1_MC_TYPE"]
    assert errs, f"Expected T1_MC_TYPE ERROR, got: {[(i.rule, i.level) for i in issues]}"


def test_motion_controllers_failed_must_have_reason():
    """motion_controllers.failed[k] = "" → ERROR."""
    data = _t1_base()
    data["tools_used"] = ["move_to_pose"]
    data["motion_controllers"] = {"failed": {"admittance": ""}}
    issues = lint_one(_make_path("CP-10"), data)
    errs = [i for i in issues if i.rule == "T1_MC_FAILED_REASON"]
    assert errs, f"Expected T1_MC_FAILED_REASON ERROR, got: {[(i.rule, i.level) for i in issues]}"
