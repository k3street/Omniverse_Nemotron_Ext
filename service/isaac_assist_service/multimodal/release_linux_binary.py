"""Phase 101 — Release: Linux pre-built binary (SPEC/MANIFEST layer).

Provides manifest schema, validator, distro detector, and JSON round-trip
helpers for Linux binary release artifacts.  This module is pure data +
Python — it does NOT require a running Kit instance.

Per specs/IA_FULL_SPEC_2026-05-10.md Phase 101.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

LinuxArch = Literal["x86_64", "aarch64", "armv7l"]

LinuxDistro = Literal[
    "ubuntu_22_04",
    "ubuntu_24_04",
    "debian_12",
    "rhel_9",
    "fedora_40",
    "arch",
    "unknown",
]

PackageFormat = Literal["deb", "rpm", "tar_gz", "appimage", "snap", "flatpak"]

# ---------------------------------------------------------------------------
# Phase metadata
# ---------------------------------------------------------------------------

PHASE_ID = 101
PHASE_TITLE = "Release: Linux pre-built binary"
PHASE_STATUS = "landed"
MANIFEST_FILENAME = "linux_release_manifest.json"


def get_phase_metadata() -> Dict[str, Any]:
    """Return phase identification and status for this phase.

    Returns:
        Dict[str, Any]: Keys ``phase``, ``title``, ``status``, and ``spec_ref``.
    """
    return {
        "phase": PHASE_ID,
        "title": PHASE_TITLE,
        "status": PHASE_STATUS,
        "spec_ref": "specs/IA_FULL_SPEC_2026-05-10.md Phase 101",
    }


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class LinuxBinaryArtifact:
    """A single release artifact for a Linux platform."""

    name: str
    arch: LinuxArch
    distro: Optional[LinuxDistro]
    package_format: PackageFormat
    version: str
    file_path: str
    sha256: str
    sha512: Optional[str] = None
    size_bytes: int = 0
    depends_on: List[str] = field(default_factory=list)


@dataclass
class LinuxReleaseManifest:
    """Full manifest for a Linux binary release."""

    version: str
    released_at: str
    artifacts: List[LinuxBinaryArtifact]
    release_notes: str = ""
    changelog_url: Optional[str] = None
    signing_key_fingerprint: Optional[str] = None


# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------

_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(-[\w.]+)?$")
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")

_DEFAULT_ALLOWED_ARCHS: List[LinuxArch] = ["x86_64", "aarch64"]


class LinuxReleaseValidator:
    """Validates a :class:`LinuxReleaseManifest` against consistency rules.

    All validation issues are returned as a list of human-readable strings.
    An empty list means the manifest is valid.
    """

    def __init__(
        self,
        require_signed: bool = False,
        allowed_archs: Optional[List[LinuxArch]] = None,
    ) -> None:
        """Initialise the validator with signing requirement and allowed architecture list."""
        self.require_signed = require_signed
        self.allowed_archs: List[LinuxArch] = (
            allowed_archs if allowed_archs is not None else list(_DEFAULT_ALLOWED_ARCHS)
        )

    def validate(self, manifest: LinuxReleaseManifest) -> List[str]:
        """Return list of issue strings; empty list → manifest is valid."""
        issues: List[str] = []

        # 1. Version format
        if not _SEMVER_RE.match(manifest.version):
            issues.append(
                f"version '{manifest.version}' does not match semver "
                r"^\d+\.\d+\.\d+(-[\w.]+)?$"
            )

        # 2. released_at parses as ISO datetime
        try:
            datetime.fromisoformat(manifest.released_at)
        except (ValueError, TypeError):
            issues.append(
                f"released_at '{manifest.released_at}' is not a valid ISO datetime"
            )

        # 3. signing_key_fingerprint required when require_signed=True
        if self.require_signed and not manifest.signing_key_fingerprint:
            issues.append(
                "signing_key_fingerprint is required when require_signed=True"
            )

        # Per-artifact checks
        seen_pairs: set = set()
        for idx, art in enumerate(manifest.artifacts):
            prefix = f"artifact[{idx}] '{art.name}'"

            # 4. sha256 looks like 64 hex chars
            if not _SHA256_RE.match(art.sha256):
                issues.append(
                    f"{prefix}: sha256 must be 64 hex characters, "
                    f"got '{art.sha256}'"
                )

            # 5. arch in allowed_archs
            if art.arch not in self.allowed_archs:
                issues.append(
                    f"{prefix}: arch '{art.arch}' not in allowed_archs "
                    f"{self.allowed_archs}"
                )

            # 6. unique (arch, package_format) pair
            pair = (art.arch, art.package_format)
            if pair in seen_pairs:
                issues.append(
                    f"{prefix}: duplicate (arch, package_format) pair {pair}"
                )
            seen_pairs.add(pair)

            # 7. depends_on is list of strings
            if not isinstance(art.depends_on, list) or not all(
                isinstance(d, str) for d in art.depends_on
            ):
                issues.append(
                    f"{prefix}: depends_on must be a list of strings"
                )

        return issues


# ---------------------------------------------------------------------------
# Distro detection
# ---------------------------------------------------------------------------

_DISTRO_MAP: Dict[tuple, LinuxDistro] = {
    ("ubuntu", "22.04"): "ubuntu_22_04",
    ("ubuntu", "24.04"): "ubuntu_24_04",
    ("debian", "12"): "debian_12",
    ("rhel", "9"): "rhel_9",
    ("fedora", "40"): "fedora_40",
    ("arch", ""): "arch",
    ("arch", None): "arch",
}


def detect_distro_from_os_release(os_release_text: str) -> LinuxDistro:
    """Parse ``/etc/os-release`` content and return a :data:`LinuxDistro`.

    Returns ``"unknown"`` if the ID / VERSION_ID combination is not
    recognised.
    """
    id_val: Optional[str] = None
    version_id_val: Optional[str] = None

    for line in os_release_text.splitlines():
        line = line.strip()
        if line.startswith("ID="):
            id_val = line[3:].strip().strip('"').strip("'").lower()
        elif line.startswith("VERSION_ID="):
            version_id_val = line[11:].strip().strip('"').strip("'")

    if id_val is None:
        return "unknown"

    # Exact (id, version) lookup
    key = (id_val, version_id_val)
    if key in _DISTRO_MAP:
        return _DISTRO_MAP[key]

    # Fallback: check id alone for distros without a meaningful version
    if id_val == "arch":
        return "arch"

    # Prefix match for rhel (e.g. "9.2" → rhel_9)
    if id_val == "rhel" and version_id_val and version_id_val.startswith("9"):
        return "rhel_9"

    # Prefix match for fedora
    if id_val == "fedora" and version_id_val == "40":
        return "fedora_40"

    # Debian minor versions (e.g. "12.1")
    if id_val == "debian" and version_id_val and version_id_val.startswith("12"):
        return "debian_12"

    return "unknown"


# ---------------------------------------------------------------------------
# Expected package formats per distro
# ---------------------------------------------------------------------------


def expected_package_formats_for_distro(distro: LinuxDistro) -> List[PackageFormat]:
    """Return the canonical list of package formats for *distro*."""
    if distro in ("ubuntu_22_04", "ubuntu_24_04", "debian_12"):
        return ["deb", "tar_gz", "appimage"]
    if distro in ("rhel_9", "fedora_40"):
        return ["rpm", "tar_gz", "appimage"]
    if distro == "arch":
        return ["tar_gz", "appimage"]
    # unknown
    return ["tar_gz", "appimage"]


# ---------------------------------------------------------------------------
# JSON serialisation helpers
# ---------------------------------------------------------------------------


def _artifact_to_dict(art: LinuxBinaryArtifact) -> Dict[str, Any]:
    """Serialise a :class:`LinuxBinaryArtifact` to a JSON-safe dict."""
    return {
        "name": art.name,
        "arch": art.arch,
        "distro": art.distro,
        "package_format": art.package_format,
        "version": art.version,
        "file_path": art.file_path,
        "sha256": art.sha256,
        "sha512": art.sha512,
        "size_bytes": art.size_bytes,
        "depends_on": art.depends_on,
    }


def _artifact_from_dict(d: Dict[str, Any]) -> LinuxBinaryArtifact:
    """Deserialise a :class:`LinuxBinaryArtifact` from a plain dict."""
    return LinuxBinaryArtifact(
        name=d["name"],
        arch=d["arch"],
        distro=d.get("distro"),
        package_format=d["package_format"],
        version=d["version"],
        file_path=d["file_path"],
        sha256=d["sha256"],
        sha512=d.get("sha512"),
        size_bytes=d.get("size_bytes", 0),
        depends_on=d.get("depends_on", []),
    )


def save_manifest_json(m: LinuxReleaseManifest, path: Path) -> None:
    """Serialise *m* to JSON at *path*."""
    payload: Dict[str, Any] = {
        "version": m.version,
        "released_at": m.released_at,
        "release_notes": m.release_notes,
        "changelog_url": m.changelog_url,
        "signing_key_fingerprint": m.signing_key_fingerprint,
        "artifacts": [_artifact_to_dict(a) for a in m.artifacts],
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def load_manifest_json(path: Path) -> LinuxReleaseManifest:
    """Deserialise a :class:`LinuxReleaseManifest` from JSON at *path*."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    artifacts = [_artifact_from_dict(a) for a in payload.get("artifacts", [])]
    return LinuxReleaseManifest(
        version=payload["version"],
        released_at=payload["released_at"],
        artifacts=artifacts,
        release_notes=payload.get("release_notes", ""),
        changelog_url=payload.get("changelog_url"),
        signing_key_fingerprint=payload.get("signing_key_fingerprint"),
    )
