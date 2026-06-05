"""Phase 88 — Linux pre-built binary CI pipeline: SPEC/CONFIG layer tests.

Gate: all tests must pass with pytest -m l0 (no external dependencies).

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 88.
"""
from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.l0

# ---------------------------------------------------------------------------
# Repo root helper
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "linux_prebuilt_binary.yml"


# ---------------------------------------------------------------------------
# Module-level imports (fail-fast if module is broken)
# ---------------------------------------------------------------------------

from service.isaac_assist_service.multimodal.linux_ci_pipeline import (
    BUILD_TARGETS,
    PHASE_STATUS,
    CIBuildTarget,
    CIMatrixEntry,
    LinuxCIMatrix,
    get_phase_metadata,
    parse_workflow_yaml,
    validate_workflow_matrix_matches_spec,
)


# ---------------------------------------------------------------------------
# 1. Metadata
# ---------------------------------------------------------------------------

class TestMetadata:
    def test_phase_metadata_keys(self):
        md = get_phase_metadata()
        assert md["phase"] == 88
        assert md["title"] == "Linux pre-built binary CI pipeline"
        assert md["status"] == "landed"
        assert "spec_ref" in md

    def test_phase_status_constant(self):
        assert PHASE_STATUS == "landed"


# ---------------------------------------------------------------------------
# 2. Workflow YAML file existence
# ---------------------------------------------------------------------------

class TestWorkflowYamlExists:
    def test_yaml_file_exists(self):
        assert WORKFLOW_PATH.is_file(), (
            f"Expected workflow file at {WORKFLOW_PATH}"
        )


# ---------------------------------------------------------------------------
# 3. YAML parses to dict
# ---------------------------------------------------------------------------

class TestWorkflowYamlParseable:
    def test_yaml_parses_to_dict(self):
        data = parse_workflow_yaml(WORKFLOW_PATH)
        assert isinstance(data, dict), "YAML must parse to a dict"
        assert len(data) > 0


# ---------------------------------------------------------------------------
# 4. YAML triggers
# ---------------------------------------------------------------------------

class TestWorkflowTriggers:
    def setup_method(self):
        self.data = parse_workflow_yaml(WORKFLOW_PATH)
        # GitHub Actions uses 'on' key
        self.on = self.data.get("on") or self.data.get(True, {})

    def test_has_push_trigger(self):
        assert "push" in self.on, "Workflow must have 'on.push' trigger"

    def test_has_pull_request_trigger(self):
        assert "pull_request" in self.on, (
            "Workflow must have 'on.pull_request' trigger"
        )

    def test_has_workflow_dispatch_trigger(self):
        assert "workflow_dispatch" in self.on, (
            "Workflow must have 'on.workflow_dispatch' trigger"
        )


# ---------------------------------------------------------------------------
# 5. YAML matrix shape
# ---------------------------------------------------------------------------

class TestWorkflowMatrix:
    def setup_method(self):
        data = parse_workflow_yaml(WORKFLOW_PATH)
        jobs = data.get("jobs", {})
        job = jobs.get("build_linux_binary") or next(iter(jobs.values()), {})
        self.matrix = job.get("strategy", {}).get("matrix", {})

    def test_matrix_has_os_key(self):
        assert "os" in self.matrix

    def test_matrix_has_python_key(self):
        assert "python" in self.matrix

    def test_matrix_has_arch_key(self):
        assert "arch" in self.matrix

    def test_matrix_os_values(self):
        assert set(self.matrix["os"]) == {"ubuntu-22.04", "ubuntu-24.04"}

    def test_matrix_python_values(self):
        assert set(str(p) for p in self.matrix["python"]) == {
            "3.10",
            "3.11",
            "3.12",
        }

    def test_matrix_arch_values(self):
        assert set(self.matrix["arch"]) == {"x86_64", "aarch64"}


# ---------------------------------------------------------------------------
# 6. LinuxCIMatrix — expand()
# ---------------------------------------------------------------------------

class TestLinuxCIMatrixExpand:
    def test_expand_returns_12_entries(self):
        m = LinuxCIMatrix()
        entries = m.expand()
        assert len(entries) == 12, f"Expected 12 entries, got {len(entries)}"

    def test_expand_entries_are_ci_matrix_entry(self):
        m = LinuxCIMatrix()
        for e in m.expand():
            assert isinstance(e, CIMatrixEntry)

    def test_all_os_represented(self):
        m = LinuxCIMatrix()
        os_set = {e.os for e in m.expand()}
        assert os_set == {"ubuntu-22.04", "ubuntu-24.04"}

    def test_all_python_represented(self):
        m = LinuxCIMatrix()
        py_set = {e.python for e in m.expand()}
        assert py_set == {"3.10", "3.11", "3.12"}

    def test_all_arch_represented(self):
        m = LinuxCIMatrix()
        arch_set = {e.arch for e in m.expand()}
        assert arch_set == {"x86_64", "aarch64"}

    def test_aarch64_is_scheduled_only(self):
        m = LinuxCIMatrix()
        for e in m.expand():
            if e.arch == "aarch64":
                assert e.scheduled_only is True
            else:
                assert e.scheduled_only is False


# ---------------------------------------------------------------------------
# 7. LinuxCIMatrix — expand_for_event('push') filters out aarch64
# ---------------------------------------------------------------------------

class TestLinuxCIMatrixExpandForEvent:
    def test_push_returns_6_entries(self):
        m = LinuxCIMatrix()
        entries = m.expand_for_event("push")
        assert len(entries) == 6, (
            f"push event should yield 6 entries (x86_64 only), got {len(entries)}"
        )

    def test_pull_request_returns_6_entries(self):
        m = LinuxCIMatrix()
        entries = m.expand_for_event("pull_request")
        assert len(entries) == 6

    def test_push_has_no_aarch64(self):
        m = LinuxCIMatrix()
        for e in m.expand_for_event("push"):
            assert e.arch != "aarch64", "aarch64 must not appear on push event"

    def test_schedule_returns_all_12(self):
        m = LinuxCIMatrix()
        entries = m.expand_for_event("schedule")
        assert len(entries) == 12, (
            f"schedule event should yield all 12 entries, got {len(entries)}"
        )

    def test_schedule_includes_aarch64(self):
        m = LinuxCIMatrix()
        archs = {e.arch for e in m.expand_for_event("schedule")}
        assert "aarch64" in archs


# ---------------------------------------------------------------------------
# 8. LinuxCIMatrix — count()
# ---------------------------------------------------------------------------

class TestLinuxCIMatrixCount:
    def test_count_no_event_is_12(self):
        assert LinuxCIMatrix().count() == 12

    def test_count_push_is_6(self):
        assert LinuxCIMatrix().count("push") == 6

    def test_count_schedule_is_12(self):
        assert LinuxCIMatrix().count("schedule") == 12


# ---------------------------------------------------------------------------
# 9. BUILD_TARGETS
# ---------------------------------------------------------------------------

class TestBuildTargets:
    def test_at_least_3_build_targets(self):
        assert len(BUILD_TARGETS) >= 3, (
            f"Expected ≥3 build targets, got {len(BUILD_TARGETS)}"
        )

    def test_build_targets_are_ci_build_target(self):
        for t in BUILD_TARGETS:
            assert isinstance(t, CIBuildTarget)

    def test_wheel_target_present(self):
        names = [t.name for t in BUILD_TARGETS]
        assert "isaac_assist_wheel" in names

    def test_pyinstaller_target_present(self):
        names = [t.name for t in BUILD_TARGETS]
        assert "isaac_assist_pyinstaller" in names

    def test_docker_target_present(self):
        names = [t.name for t in BUILD_TARGETS]
        assert "isaac_assist_docker" in names

    def test_docker_target_has_dockerfile(self):
        docker_targets = [t for t in BUILD_TARGETS if t.name == "isaac_assist_docker"]
        assert docker_targets
        assert docker_targets[0].dockerfile is not None

    def test_all_targets_have_build_command(self):
        for t in BUILD_TARGETS:
            assert t.build_command, f"Target {t.name} has empty build_command"

    def test_all_targets_have_output_artifact(self):
        for t in BUILD_TARGETS:
            assert t.output_artifact, f"Target {t.name} has empty output_artifact"


# ---------------------------------------------------------------------------
# 10. validate_workflow_matrix_matches_spec
# ---------------------------------------------------------------------------

class TestValidateWorkflowMatrix:
    def test_validate_returns_true(self):
        result = validate_workflow_matrix_matches_spec(WORKFLOW_PATH)
        assert result is True, (
            "YAML matrix does not match LinuxCIMatrix spec — "
            "ensure os/python/arch lists in the YAML match the Python class"
        )
