"""Phase 88 — Linux pre-built binary CI pipeline (SPEC/CONFIG layer).

Defines the CI matrix and build-target enumeration for the Linux
pre-built binary pipeline.  The matrix here is the single source of
truth; the GitHub Actions YAML at
  .github/workflows/linux_prebuilt_binary.yml
is generated from / validated against this module.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 88.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

# ---------------------------------------------------------------------------
# Phase metadata
# ---------------------------------------------------------------------------

PHASE_ID = 88
PHASE_TITLE = "Linux pre-built binary CI pipeline"
PHASE_STATUS = "landed"


def get_phase_metadata() -> Dict[str, Any]:
    """Return phase metadata for spec-coverage audits."""
    return {
        "phase": PHASE_ID,
        "title": PHASE_TITLE,
        "status": PHASE_STATUS,
        "spec_ref": "specs/IA_FULL_SPEC_2026-05-10.md Phase 88",
        "agent": "sonnet-mechanical",
        "gate": "pytest",
    }


# ---------------------------------------------------------------------------
# CI matrix
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CIMatrixEntry:
    """One cell in the CI matrix.

    scheduled_only=True means the job should only run on the ``schedule``
    event (nightly), not on push/PR.  aarch64 builds are scheduled-only
    because cross-compilation is expensive and slow.
    """

    os: str
    python: str
    arch: str
    scheduled_only: bool = False


class LinuxCIMatrix:
    """Full Cartesian CI matrix mirroring the GitHub Actions YAML.

    Dimensions:
      * os      — ubuntu-22.04, ubuntu-24.04          (2)
      * python  — '3.10', '3.11', '3.12'              (3)
      * arch    — x86_64, aarch64                     (2)
                                                     ------
      Total                                            12
    """

    _OS = ["ubuntu-22.04", "ubuntu-24.04"]
    _PYTHON = ["3.10", "3.11", "3.12"]
    _ARCH = ["x86_64", "aarch64"]
    _SCHEDULED_ARCHS = {"aarch64"}

    def __init__(self) -> None:
        self._entries: List[CIMatrixEntry] = [
            CIMatrixEntry(
                os=os,
                python=py,
                arch=arch,
                scheduled_only=(arch in self._SCHEDULED_ARCHS),
            )
            for os in self._OS
            for py in self._PYTHON
            for arch in self._ARCH
        ]

    def expand(self) -> List[CIMatrixEntry]:
        """Return all 12 matrix entries."""
        return list(self._entries)

    def expand_for_event(
        self, event: Literal["push", "pull_request", "schedule"]
    ) -> List[CIMatrixEntry]:
        """Return the entries that would run for the given GitHub event.

        * ``push`` / ``pull_request`` — excludes scheduled-only entries
          (i.e. aarch64 is filtered out).
        * ``schedule`` — returns all entries.
        """
        if event == "schedule":
            return list(self._entries)
        # push / pull_request: skip scheduled-only entries
        return [e for e in self._entries if not e.scheduled_only]

    def count(self, event: Optional[str] = None) -> int:
        """Return entry count, optionally filtered by event name."""
        if event is None:
            return len(self._entries)
        return len(self.expand_for_event(event))  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Build targets
# ---------------------------------------------------------------------------

@dataclass
class CIBuildTarget:
    """One build target produced by the CI pipeline."""

    name: str
    dockerfile: Optional[str]
    build_command: str
    output_artifact: str


#: Canonical build targets — extend as more packaging formats are added.
BUILD_TARGETS: List[CIBuildTarget] = [
    CIBuildTarget(
        name="isaac_assist_wheel",
        dockerfile=None,
        build_command="python -m build --wheel --outdir dist/",
        output_artifact="dist/*.whl",
    ),
    CIBuildTarget(
        name="isaac_assist_pyinstaller",
        dockerfile=None,
        build_command=(
            "pyinstaller --onefile --name isaac_assist "
            "--distpath dist/pyinstaller/ "
            "service/isaac_assist_service/__main__.py"
        ),
        output_artifact="dist/pyinstaller/isaac_assist",
    ),
    CIBuildTarget(
        name="isaac_assist_docker",
        dockerfile="Dockerfile",
        build_command="docker build -t isaac_assist:latest .",
        output_artifact="isaac_assist:latest",
    ),
]


# ---------------------------------------------------------------------------
# Workflow YAML helpers
# ---------------------------------------------------------------------------

def parse_workflow_yaml(yaml_path: Path) -> Dict[str, Any]:
    """Parse the GitHub Actions workflow YAML and return a dict.

    Raises ``FileNotFoundError`` if the file does not exist.
    Raises ``ImportError`` if PyYAML is not installed.
    """
    try:
        import yaml  # type: ignore
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "PyYAML is required: pip install pyyaml"
        ) from exc

    with open(yaml_path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def validate_workflow_matrix_matches_spec(yaml_path: Path) -> bool:
    """Return True iff the YAML matrix entries match LinuxCIMatrix.expand().

    Checks that the ``matrix`` block in the first job contains the same
    os / python / arch lists as the Python spec.  Does NOT check
    cartesian order — only set membership.
    """
    data = parse_workflow_yaml(yaml_path)

    # Navigate: jobs -> first job -> strategy -> matrix
    jobs: Dict[str, Any] = data.get("jobs", {})
    if not jobs:
        return False

    # Allow either "build_linux_binary" or any single job key
    job = jobs.get("build_linux_binary") or next(iter(jobs.values()), None)
    if job is None:
        return False

    yaml_matrix: Dict[str, Any] = (
        job.get("strategy", {}).get("matrix", {})
    )
    if not yaml_matrix:
        return False

    spec = LinuxCIMatrix()
    spec_os = set(spec._OS)
    spec_py = set(spec._PYTHON)
    spec_arch = set(spec._ARCH)

    yaml_os = set(yaml_matrix.get("os", []))
    yaml_py = set(str(p) for p in yaml_matrix.get("python", []))
    yaml_arch = set(yaml_matrix.get("arch", []))

    return (
        yaml_os == spec_os
        and yaml_py == spec_py
        and yaml_arch == spec_arch
    )
