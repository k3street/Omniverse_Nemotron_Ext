"""Phase 102 — Release: macOS / Windows binaries — contract tests.

Gates:
- release manifest validates required fields
- codesign status helpers return correct booleans
- platform detection returns one of the four defined Platform literals
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.l0

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PLATFORMS = {"macos_arm64", "macos_x86_64", "windows_x86_64", "windows_arm64"}
_GOOD_SHA256 = "a" * 64
_BAD_SHA256_SHORT = "deadbeef"


def _make_artifact(
    platform="macos_arm64",
    sha256=_GOOD_SHA256,
    signing_status="notarized",
    signed_at="2026-05-13T00:00:00Z",
    name=None,
):
    from service.isaac_assist_service.multimodal.release_macos_windows import (
        BinaryArtifact,
    )

    return BinaryArtifact(
        name=name or f"isaac_assist_{platform}.zip",
        platform=platform,
        version="1.2.3",
        file_path=f"/dist/{platform}.zip",
        sha256=sha256,
        size_bytes=1_000_000,
        signing_status=signing_status,
        signing_authority="Apple Inc." if "macos" in platform else "Acme Corp",
        signed_at=signed_at,
    )


def _make_stable_manifest():
    """Return a fully-populated stable manifest covering all 4 platforms."""
    from service.isaac_assist_service.multimodal.release_macos_windows import (
        ReleaseManifest,
        expected_artifacts_for_channel,
    )

    artifacts = [_make_artifact(platform=p) for p in expected_artifacts_for_channel("stable")]
    return ReleaseManifest(
        version="1.2.3",
        channel="stable",
        released_at="2026-05-13T12:00:00Z",
        artifacts=artifacts,
        release_notes="Initial release",
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_phase_102_metadata():
    from service.isaac_assist_service.multimodal.release_macos_windows import (
        get_phase_metadata,
    )

    md = get_phase_metadata()
    assert md["phase"] == 102
    assert md["status"] == "landed"
    assert "title" in md
    assert "spec_ref" in md


def test_detect_current_platform_is_valid_literal():
    from service.isaac_assist_service.multimodal.release_macos_windows import (
        detect_current_platform,
    )

    result = detect_current_platform()
    assert result in _PLATFORMS, f"Unexpected platform value: {result!r}"


def test_macos_signing_notarized_distribution_true():
    from service.isaac_assist_service.multimodal.release_macos_windows import (
        is_macos_signing_acceptable,
    )

    assert is_macos_signing_acceptable("notarized", for_distribution=True) is True


def test_macos_signing_developer_id_distribution_true():
    from service.isaac_assist_service.multimodal.release_macos_windows import (
        is_macos_signing_acceptable,
    )

    assert is_macos_signing_acceptable("developer_id", for_distribution=True) is True


def test_macos_signing_self_signed_distribution_false():
    from service.isaac_assist_service.multimodal.release_macos_windows import (
        is_macos_signing_acceptable,
    )

    assert is_macos_signing_acceptable("self_signed", for_distribution=True) is False


def test_macos_signing_self_signed_non_distribution_true():
    from service.isaac_assist_service.multimodal.release_macos_windows import (
        is_macos_signing_acceptable,
    )

    assert is_macos_signing_acceptable("self_signed", for_distribution=False) is True


def test_windows_signing_developer_id_true():
    from service.isaac_assist_service.multimodal.release_macos_windows import (
        is_windows_signing_acceptable,
    )

    assert is_windows_signing_acceptable("developer_id") is True


def test_windows_signing_ev_codesigned_true():
    from service.isaac_assist_service.multimodal.release_macos_windows import (
        is_windows_signing_acceptable,
    )

    assert is_windows_signing_acceptable("ev_codesigned") is True


def test_windows_signing_unsigned_false():
    from service.isaac_assist_service.multimodal.release_macos_windows import (
        is_windows_signing_acceptable,
    )

    assert is_windows_signing_acceptable("unsigned") is False


def test_expected_artifacts_stable_returns_four_platforms():
    from service.isaac_assist_service.multimodal.release_macos_windows import (
        expected_artifacts_for_channel,
    )

    result = expected_artifacts_for_channel("stable")
    assert len(result) == 4
    assert set(result) == _PLATFORMS


def test_expected_artifacts_beta_returns_two_x86_64():
    from service.isaac_assist_service.multimodal.release_macos_windows import (
        expected_artifacts_for_channel,
    )

    result = expected_artifacts_for_channel("beta")
    assert len(result) == 2
    assert set(result) == {"macos_x86_64", "windows_x86_64"}


def test_expected_artifacts_nightly_returns_two():
    from service.isaac_assist_service.multimodal.release_macos_windows import (
        expected_artifacts_for_channel,
    )

    result = expected_artifacts_for_channel("nightly")
    assert len(result) == 2
    assert "macos_arm64" in result
    assert "windows_x86_64" in result


def test_validator_clean_on_well_formed_manifest():
    from service.isaac_assist_service.multimodal.release_macos_windows import (
        ReleaseManifestValidator,
    )

    manifest = _make_stable_manifest()
    validator = ReleaseManifestValidator(require_signed=True)
    issues = validator.validate(manifest)
    assert issues == [], f"Expected no issues, got: {issues}"


def test_validator_flags_bad_sha256():
    from service.isaac_assist_service.multimodal.release_macos_windows import (
        ReleaseManifest,
        ReleaseManifestValidator,
    )

    art = _make_artifact(platform="macos_arm64", sha256=_BAD_SHA256_SHORT)
    manifest = ReleaseManifest(
        version="1.2.3",
        channel="nightly",
        released_at="2026-05-13T00:00:00Z",
        artifacts=[art],
    )
    validator = ReleaseManifestValidator(require_signed=False)
    issues = validator.validate(manifest)
    assert any("sha256" in i.lower() for i in issues), f"No sha256 issue found: {issues}"


def test_validator_flags_unsigned_when_require_signed():
    from service.isaac_assist_service.multimodal.release_macos_windows import (
        ReleaseManifest,
        ReleaseManifestValidator,
    )

    art = _make_artifact(
        platform="macos_arm64",
        signing_status="unsigned",
        signed_at=None,
    )
    manifest = ReleaseManifest(
        version="1.2.3",
        channel="nightly",
        released_at="2026-05-13T00:00:00Z",
        artifacts=[art],
    )
    validator = ReleaseManifestValidator(require_signed=True)
    issues = validator.validate(manifest)
    assert any("unsigned" in i.lower() for i in issues), f"Expected unsigned issue: {issues}"


def test_validator_flags_missing_platform_in_stable():
    from service.isaac_assist_service.multimodal.release_macos_windows import (
        ReleaseManifest,
        ReleaseManifestValidator,
    )

    # Only 2 platforms instead of the required 4 for stable
    artifacts = [
        _make_artifact(platform="macos_arm64"),
        _make_artifact(platform="windows_x86_64"),
    ]
    manifest = ReleaseManifest(
        version="1.2.3",
        channel="stable",
        released_at="2026-05-13T00:00:00Z",
        artifacts=artifacts,
    )
    validator = ReleaseManifestValidator(require_signed=True)
    issues = validator.validate(manifest)
    assert any("missing" in i.lower() for i in issues), f"Expected missing-platform issue: {issues}"


def test_validator_flags_bad_version_format():
    from service.isaac_assist_service.multimodal.release_macos_windows import (
        ReleaseManifest,
        ReleaseManifestValidator,
    )

    manifest = ReleaseManifest(
        version="v1-bad",
        channel="nightly",
        released_at="2026-05-13T00:00:00Z",
        artifacts=[],
    )
    validator = ReleaseManifestValidator(require_signed=False)
    issues = validator.validate(manifest)
    assert any("version" in i.lower() for i in issues), f"Expected version issue: {issues}"


def test_manifest_save_load_roundtrip(tmp_path: Path):
    from service.isaac_assist_service.multimodal.release_macos_windows import (
        load_manifest_json,
        save_manifest_json,
        MANIFEST_FILENAME,
    )

    manifest = _make_stable_manifest()
    dest = tmp_path / MANIFEST_FILENAME
    save_manifest_json(manifest, dest)

    assert dest.exists()
    loaded = load_manifest_json(dest)

    assert loaded.version == manifest.version
    assert loaded.channel == manifest.channel
    assert loaded.released_at == manifest.released_at
    assert loaded.release_notes == manifest.release_notes
    assert len(loaded.artifacts) == len(manifest.artifacts)

    for orig, restored in zip(manifest.artifacts, loaded.artifacts):
        assert restored.name == orig.name
        assert restored.platform == orig.platform
        assert restored.sha256 == orig.sha256
        assert restored.signing_status == orig.signing_status
        assert restored.signed_at == orig.signed_at


def test_binary_artifact_dataclass_fields():
    art = _make_artifact()
    assert hasattr(art, "name")
    assert hasattr(art, "platform")
    assert hasattr(art, "version")
    assert hasattr(art, "file_path")
    assert hasattr(art, "sha256")
    assert hasattr(art, "size_bytes")
    assert hasattr(art, "signing_status")
    assert hasattr(art, "signing_authority")
    assert hasattr(art, "signed_at")


def test_validator_signed_at_required_when_signed():
    from service.isaac_assist_service.multimodal.release_macos_windows import (
        ReleaseManifest,
        ReleaseManifestValidator,
    )

    art = _make_artifact(
        platform="macos_arm64",
        signing_status="developer_id",
        signed_at=None,  # signed but missing timestamp
    )
    manifest = ReleaseManifest(
        version="1.2.3",
        channel="nightly",
        released_at="2026-05-13T00:00:00Z",
        artifacts=[art],
    )
    validator = ReleaseManifestValidator(require_signed=False)
    issues = validator.validate(manifest)
    assert any("signed_at" in i.lower() for i in issues), f"Expected signed_at issue: {issues}"
