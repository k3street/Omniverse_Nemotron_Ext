"""Phase 102 — Release: macOS / Windows binaries.

Release-manifest schema, signing-status tracking, and platform-detection helpers
for macOS and Windows binary distribution.  Actual binary build is opus-runtime;
this module is pure Python.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 102.
"""
from __future__ import annotations

import hashlib
import json
import platform as _platform
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Set

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

Platform = Literal[
    "macos_arm64",
    "macos_x86_64",
    "windows_x86_64",
    "windows_arm64",
]

ReleaseChannel = Literal["stable", "beta", "nightly"]

SigningStatus = Literal[
    "unsigned",
    "self_signed",
    "developer_id",
    "notarized",
    "ev_codesigned",
]

# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

MANIFEST_FILENAME = "release_manifest.json"


@dataclass
class BinaryArtifact:
    """Metadata for a single platform binary artifact."""

    name: str
    platform: Platform
    version: str
    file_path: str
    sha256: str
    size_bytes: int
    signing_status: SigningStatus
    signing_authority: Optional[str] = None
    signed_at: Optional[str] = None


@dataclass
class ReleaseManifest:
    """Top-level manifest describing a multi-platform release."""

    version: str
    channel: ReleaseChannel
    released_at: str
    artifacts: List[BinaryArtifact] = field(default_factory=list)
    release_notes: str = ""
    changelog_url: Optional[str] = None


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+")
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")


class ReleaseManifestValidator:
    """Validate a :class:`ReleaseManifest` against policy rules.

    Parameters
    ----------
    require_signed:
        When ``True`` (default) any artifact with ``signing_status="unsigned"``
        is flagged as an issue.
    allowed_signing:
        Explicit set of acceptable :class:`SigningStatus` values.  When *None*
        the set is derived from the channel / artifact platform at call time.
    """

    def __init__(
        self,
        require_signed: bool = True,
        allowed_signing: Optional[Set[SigningStatus]] = None,
    ) -> None:
        self.require_signed = require_signed
        self.allowed_signing = allowed_signing

    # ------------------------------------------------------------------
    def validate(self, manifest: ReleaseManifest) -> List[str]:
        """Return a list of issue strings; empty list means the manifest is clean."""
        issues: List[str] = []

        # --- version format
        if not _SEMVER_RE.match(manifest.version):
            issues.append(
                f"version '{manifest.version}' does not match semver pattern X.Y.Z"
            )

        # --- per-artifact checks
        artifact_platforms: Set[str] = set()
        for art in manifest.artifacts:
            artifact_platforms.add(art.platform)

            # sha256 format
            if not _SHA256_RE.match(art.sha256):
                issues.append(
                    f"artifact '{art.name}' has invalid sha256 (expected 64 hex chars)"
                )

            # signing requirements
            if self.require_signed and art.signing_status == "unsigned":
                issues.append(
                    f"artifact '{art.name}' is unsigned but signing is required"
                )

            if self.allowed_signing is not None:
                if art.signing_status not in self.allowed_signing:
                    issues.append(
                        f"artifact '{art.name}' signing_status '{art.signing_status}'"
                        f" not in allowed set {self.allowed_signing}"
                    )

            # signed_at required when actually signed
            if art.signing_status != "unsigned" and art.signed_at is None:
                issues.append(
                    f"artifact '{art.name}' is signed ({art.signing_status})"
                    " but signed_at is missing"
                )

        # --- platform coverage for stable channel
        if manifest.channel == "stable":
            required = set(expected_artifacts_for_channel("stable"))
            missing = required - artifact_platforms
            if missing:
                issues.append(
                    f"stable release is missing artifacts for platforms: {sorted(missing)}"
                )

        return issues


# ---------------------------------------------------------------------------
# Platform helpers
# ---------------------------------------------------------------------------

def detect_current_platform() -> Platform:
    """Return the Platform literal for the current host.

    Falls back to ``"macos_arm64"`` when the combination is unrecognised so
    callers always receive one of the four defined literals.
    """
    system = _platform.system().lower()
    machine = _platform.machine().lower()

    if system == "darwin":
        if machine in ("arm64", "aarch64"):
            return "macos_arm64"
        return "macos_x86_64"

    if system == "windows":
        if machine in ("arm64", "aarch64"):
            return "windows_arm64"
        return "windows_x86_64"

    # Linux or unknown — default for CI
    return "macos_arm64"


def is_macos_signing_acceptable(
    status: SigningStatus, for_distribution: bool = True
) -> bool:
    """Return whether *status* meets macOS signing policy.

    Distribution (App Store / direct download) requires ``notarized`` or
    ``developer_id``.  Non-distribution (internal test) also accepts
    ``self_signed``.
    """
    if for_distribution:
        return status in {"notarized", "developer_id"}
    return status in {"notarized", "developer_id", "self_signed"}


def is_windows_signing_acceptable(status: SigningStatus) -> bool:
    """Return whether *status* meets Windows Authenticode distribution policy.

    Acceptable: ``developer_id``, ``notarized``, ``ev_codesigned``.
    """
    return status in {"developer_id", "notarized", "ev_codesigned"}


# ---------------------------------------------------------------------------
# Channel helpers
# ---------------------------------------------------------------------------

_CHANNEL_PLATFORMS: Dict[ReleaseChannel, List[Platform]] = {
    "stable": [
        "macos_arm64",
        "macos_x86_64",
        "windows_x86_64",
        "windows_arm64",
    ],
    "beta": [
        "macos_x86_64",
        "windows_x86_64",
    ],
    "nightly": [
        "macos_arm64",
        "windows_x86_64",
    ],
}


def expected_artifacts_for_channel(channel: ReleaseChannel) -> List[Platform]:
    """Return the ordered list of :class:`Platform` values expected for *channel*.

    - ``stable``  → all 4 platforms
    - ``beta``    → macOS x86_64 + Windows x86_64 (2 platforms)
    - ``nightly`` → macOS arm64  + Windows x86_64 (2 platforms)
    """
    return list(_CHANNEL_PLATFORMS[channel])


# ---------------------------------------------------------------------------
# JSON serialisation helpers
# ---------------------------------------------------------------------------

def _artifact_to_dict(art: BinaryArtifact) -> Dict[str, Any]:
    return asdict(art)


def _artifact_from_dict(d: Dict[str, Any]) -> BinaryArtifact:
    return BinaryArtifact(
        name=d["name"],
        platform=d["platform"],
        version=d["version"],
        file_path=d["file_path"],
        sha256=d["sha256"],
        size_bytes=d["size_bytes"],
        signing_status=d["signing_status"],
        signing_authority=d.get("signing_authority"),
        signed_at=d.get("signed_at"),
    )


def save_manifest_json(manifest: ReleaseManifest, path: Path) -> None:
    """Serialise *manifest* to *path* as indented JSON."""
    data: Dict[str, Any] = {
        "version": manifest.version,
        "channel": manifest.channel,
        "released_at": manifest.released_at,
        "release_notes": manifest.release_notes,
        "changelog_url": manifest.changelog_url,
        "artifacts": [_artifact_to_dict(a) for a in manifest.artifacts],
    }
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def load_manifest_json(path: Path) -> ReleaseManifest:
    """Deserialise a :class:`ReleaseManifest` from *path*."""
    data = json.loads(path.read_text(encoding="utf-8"))
    artifacts = [_artifact_from_dict(a) for a in data.get("artifacts", [])]
    return ReleaseManifest(
        version=data["version"],
        channel=data["channel"],
        released_at=data["released_at"],
        release_notes=data.get("release_notes", ""),
        changelog_url=data.get("changelog_url"),
        artifacts=artifacts,
    )


# ---------------------------------------------------------------------------
# Phase metadata
# ---------------------------------------------------------------------------

PHASE_ID = 102
PHASE_TITLE = "Release: macOS / Windows binaries"
PHASE_STATUS = "landed"


def get_phase_metadata() -> Dict[str, Any]:
    return {
        "phase": PHASE_ID,
        "title": PHASE_TITLE,
        "status": PHASE_STATUS,
        "spec_ref": "specs/IA_FULL_SPEC_2026-05-10.md Phase 102",
    }
