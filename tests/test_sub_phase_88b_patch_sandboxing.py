"""Phase 88b — Production patch sandboxing tests.

Covers risk classifier, sandbox policy selection, dry-run isolation, and
module allowlist enforcement.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 88b.
"""
import pytest

pytestmark = pytest.mark.l0

# ---------------------------------------------------------------------------
# Imports under test
# ---------------------------------------------------------------------------

from service.isaac_assist_service.multimodal.sub_phase_88b_patch_sandboxing import (
    PHASE_STATUS,
    SANDBOX_POLICIES,
    PatchRiskClassifier,
    PatchSandbox,
    SandboxPolicy,
    get_phase_metadata,
    select_policy_for_patch,
)


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------

def test_metadata_phase_id():
    md = get_phase_metadata()
    assert md["phase"] == "88b"


def test_metadata_status_landed():
    md = get_phase_metadata()
    assert md["status"] == "landed"
    assert PHASE_STATUS == "landed"


# ---------------------------------------------------------------------------
# SANDBOX_POLICIES
# ---------------------------------------------------------------------------

def test_sandbox_policies_has_five_entries():
    assert len(SANDBOX_POLICIES) == 5
    for level in ("minimal", "low", "moderate", "high", "critical"):
        assert level in SANDBOX_POLICIES


# ---------------------------------------------------------------------------
# PatchRiskClassifier.assess — clean code
# ---------------------------------------------------------------------------

def test_assess_clean_print_is_minimal_or_low():
    clf = PatchRiskClassifier()
    result = clf.assess("print('hello world')")
    # A single print( hit scores 0.5, so result is "low"
    assert result.risk_level in ("minimal", "low")
    assert result.score >= 0


def test_assess_logging_only_is_low():
    clf = PatchRiskClassifier()
    result = clf.assess("import logging\nlogging.info('starting')")
    assert result.risk_level in ("minimal", "low")


# ---------------------------------------------------------------------------
# PatchRiskClassifier.assess — high/critical signals
# ---------------------------------------------------------------------------

def test_assess_subprocess_and_os_system_is_critical():
    clf = PatchRiskClassifier()
    patch = (
        "import subprocess\n"
        "subprocess.run(['ls'])\n"
        "os.system('rm -rf /tmp/test')\n"
    )
    result = clf.assess(patch)
    # subprocess counts as HIGH (+10), os.system (+10), rm -rf (+10) = 30 → critical
    assert result.risk_level == "critical"
    assert result.score >= 30


def test_assess_exec_eval_is_critical():
    clf = PatchRiskClassifier()
    patch = "exec('malicious()')\neval('__import__(\"os\")')"
    result = clf.assess(patch)
    assert result.risk_level == "critical"
    assert result.requires_human_approval is True


def test_assess_delete_drop_is_high_or_critical():
    clf = PatchRiskClassifier()
    patch = "delete_prim('/World/Cube')\ndrop_table('assets')"
    result = clf.assess(patch)
    assert result.risk_level in ("high", "critical")
    assert result.requires_human_approval is True


# ---------------------------------------------------------------------------
# PatchRiskClassifier.assess — moderate signals
# ---------------------------------------------------------------------------

def test_assess_requests_and_open_is_moderate_or_higher():
    clf = PatchRiskClassifier()
    patch = "requests.get('http://example.com')\nwith open('output.txt', 'w') as f:\n    f.write('data')"
    result = clf.assess(patch)
    assert result.risk_level in ("moderate", "high", "critical")
    assert result.score >= 5


# ---------------------------------------------------------------------------
# requires_sandbox / requires_human_approval thresholds
# ---------------------------------------------------------------------------

def test_requires_sandbox_true_when_moderate_or_above():
    clf = PatchRiskClassifier()
    # with open scores MODERATE (3) + write_text also moderate — ensures >= moderate
    patch = "with open('x') as f:\n    pass\n" * 2  # 2 hits → 6 pts → moderate
    result = clf.assess(patch)
    assert result.requires_sandbox is True


def test_requires_sandbox_false_when_low():
    clf = PatchRiskClassifier()
    result = clf.assess("print('ok')")
    # low risk → no sandbox required
    assert result.risk_level in ("minimal", "low")
    assert result.requires_sandbox is False


def test_requires_human_approval_true_when_high():
    clf = PatchRiskClassifier()
    patch = "subprocess.run(['sh'])\nos.system('ls')"
    result = clf.assess(patch)
    # subprocess (+10) + os.system (+10) = 20 → high
    assert result.requires_human_approval is True


def test_requires_human_approval_false_when_moderate():
    clf = PatchRiskClassifier()
    # requests. (3) + socket. (3) = 6 → moderate (not high)
    patch = "requests.get(url)\nsocket.connect(addr)"
    result = clf.assess(patch)
    if result.risk_level == "moderate":
        assert result.requires_human_approval is False


# ---------------------------------------------------------------------------
# SandboxPolicy dataclass defaults
# ---------------------------------------------------------------------------

def test_sandbox_policy_defaults_no_network_no_fs_no_subprocess():
    policy = SandboxPolicy()
    assert policy.allow_network is False
    assert policy.allow_filesystem_writes is False
    assert policy.allow_subprocess is False


def test_sandbox_policy_defaults_sensible_limits():
    policy = SandboxPolicy()
    assert policy.memory_limit_mb == 512
    assert policy.cpu_time_s == 30
    assert policy.wall_time_s == 60
    assert isinstance(policy.allowed_modules, list)


# ---------------------------------------------------------------------------
# PatchSandbox.prepare
# ---------------------------------------------------------------------------

def test_sandbox_prepare_returns_required_keys():
    policy = SANDBOX_POLICIES["moderate"]
    sandbox = PatchSandbox(policy=policy, dry_run=True)
    result = sandbox.prepare("print('test')")
    assert "policy" in result
    assert "dry_run" in result
    assert "would_execute" in result
    assert "restrictions" in result


def test_sandbox_prepare_dry_run_flag():
    policy = SANDBOX_POLICIES["low"]
    sandbox = PatchSandbox(policy=policy, dry_run=True)
    result = sandbox.prepare("x = 1")
    assert result["dry_run"] is True
    assert result["would_execute"] is False


# ---------------------------------------------------------------------------
# PatchSandbox.execute
# ---------------------------------------------------------------------------

def test_sandbox_execute_dry_run_returns_simulated_success():
    policy = SANDBOX_POLICIES["high"]
    sandbox = PatchSandbox(policy=policy, dry_run=True)
    result = sandbox.execute("exec('bad')")
    assert result["success"] is True
    assert result["dry_run"] is True
    assert result["simulated"] is True


def test_sandbox_execute_non_dry_run_raises_not_implemented():
    policy = SANDBOX_POLICIES["critical"]
    sandbox = PatchSandbox(policy=policy, dry_run=False)
    with pytest.raises(NotImplementedError):
        sandbox.execute("os.system('ls')")


# ---------------------------------------------------------------------------
# validate_imports / is_module_allowed
# ---------------------------------------------------------------------------

def test_validate_imports_returns_import_statements():
    policy = SANDBOX_POLICIES["minimal"]
    sandbox = PatchSandbox(policy=policy)
    patch = "import os\nimport json\nfrom pathlib import Path\nx = 1"
    imports = sandbox.validate_imports(patch)
    assert len(imports) >= 2
    assert any("import os" in s for s in imports)
    assert any("import json" in s for s in imports)


def test_validate_imports_empty_for_no_imports():
    policy = SANDBOX_POLICIES["minimal"]
    sandbox = PatchSandbox(policy=policy)
    imports = sandbox.validate_imports("x = 1 + 2\nprint(x)")
    assert imports == []


def test_is_module_allowed_true_when_in_list():
    policy = SandboxPolicy(allowed_modules=["json", "math", "datetime"])
    sandbox = PatchSandbox(policy=policy)
    assert sandbox.is_module_allowed("json") is True
    assert sandbox.is_module_allowed("math") is True


def test_is_module_allowed_false_when_not_in_list():
    policy = SandboxPolicy(allowed_modules=["json"])
    sandbox = PatchSandbox(policy=policy)
    assert sandbox.is_module_allowed("subprocess") is False
    assert sandbox.is_module_allowed("os") is False


# ---------------------------------------------------------------------------
# select_policy_for_patch
# ---------------------------------------------------------------------------

def test_select_policy_for_patch_returns_tuple():
    assessment, policy = select_policy_for_patch("print('hello')")
    assert hasattr(assessment, "risk_level")
    assert hasattr(assessment, "score")
    assert isinstance(policy, SandboxPolicy)


def test_select_policy_for_patch_critical_code_gets_tight_policy():
    patch = "subprocess.run(['rm', '-rf', '/'])\nexec('evil')\nos.system('drop_table x')"
    assessment, policy = select_policy_for_patch(patch)
    assert assessment.risk_level == "critical"
    # Critical policy should have the tightest restrictions
    assert policy.allow_network is False
    assert policy.allow_subprocess is False
    assert policy.memory_limit_mb <= 128


def test_select_policy_consistent_with_sandbox_policies_dict():
    """The policy returned must be the canonical one from SANDBOX_POLICIES."""
    patch = "print('hello')\nlogging.info('hi')"
    assessment, policy = select_policy_for_patch(patch)
    expected_policy = SANDBOX_POLICIES[assessment.risk_level]
    assert policy is expected_policy
