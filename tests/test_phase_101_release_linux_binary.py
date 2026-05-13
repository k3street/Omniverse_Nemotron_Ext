"""Phase 101 contract tests — Release: Linux pre-built binary (SPEC/MANIFEST layer).

All tests are pure-Python (no Kit / GR00T required).
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.l0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mod():
    from service.isaac_assist_service.multimodal import release_linux_binary as m
    return m


def _good_artifact(
    name="ia-service_1.2.3_amd64.deb",
    arch="x86_64",
    distro="ubuntu_22_04",
    package_format="deb",
    version="1.2.3",
    file_path="/dist/ia-service_1.2.3_amd64.deb",
    sha256="a" * 64,
):
    from service.isaac_assist_service.multimodal.release_linux_binary import LinuxBinaryArtifact
    return LinuxBinaryArtifact(
        name=name,
        arch=arch,
        distro=distro,
        package_format=package_format,
        version=version,
        file_path=file_path,
        sha256=sha256,
    )


def _good_manifest(artifacts=None):
    from service.isaac_assist_service.multimodal.release_linux_binary import LinuxReleaseManifest
    return LinuxReleaseManifest(
        version="1.2.3",
        released_at="2026-05-13T12:00:00",
        artifacts=artifacts if artifacts is not None else [_good_artifact()],
    )


def _validator(**kwargs):
    from service.isaac_assist_service.multimodal.release_linux_binary import LinuxReleaseValidator
    return LinuxReleaseValidator(**kwargs)


# ---------------------------------------------------------------------------
# Test 1 — metadata contract
# ---------------------------------------------------------------------------


def test_phase_101_metadata():
    m = _mod()
    md = m.get_phase_metadata()
    assert md["phase"] == 101
    assert md["status"] == "landed"
    assert "title" in md
    assert "spec_ref" in md


# ---------------------------------------------------------------------------
# Test 2 — MANIFEST_FILENAME constant present
# ---------------------------------------------------------------------------


def test_manifest_filename_constant():
    m = _mod()
    assert m.MANIFEST_FILENAME == "linux_release_manifest.json"


# ---------------------------------------------------------------------------
# Test 3 — LinuxBinaryArtifact dataclass fields
# ---------------------------------------------------------------------------


def test_linux_binary_artifact_fields():
    art = _good_artifact()
    assert art.name == "ia-service_1.2.3_amd64.deb"
    assert art.arch == "x86_64"
    assert art.distro == "ubuntu_22_04"
    assert art.package_format == "deb"
    assert art.version == "1.2.3"
    assert art.sha256 == "a" * 64
    # defaults
    assert art.sha512 is None
    assert art.size_bytes == 0
    assert art.depends_on == []


# ---------------------------------------------------------------------------
# Test 4 — LinuxReleaseManifest dataclass fields
# ---------------------------------------------------------------------------


def test_linux_release_manifest_fields():
    m = _good_manifest()
    assert m.version == "1.2.3"
    assert m.released_at == "2026-05-13T12:00:00"
    assert len(m.artifacts) == 1
    # defaults
    assert m.release_notes == ""
    assert m.changelog_url is None
    assert m.signing_key_fingerprint is None


# ---------------------------------------------------------------------------
# Test 5 — validator: clean manifest → no issues
# ---------------------------------------------------------------------------


def test_validator_clean_manifest_returns_no_issues():
    v = _validator()
    issues = v.validate(_good_manifest())
    assert issues == [], f"Unexpected issues: {issues}"


# ---------------------------------------------------------------------------
# Test 6 — validator: bad version (no semver) → issue
# ---------------------------------------------------------------------------


def test_validator_bad_version():
    from service.isaac_assist_service.multimodal.release_linux_binary import LinuxReleaseManifest
    m = LinuxReleaseManifest(
        version="v1.2",  # missing patch, has "v" prefix
        released_at="2026-05-13T12:00:00",
        artifacts=[_good_artifact()],
    )
    issues = _validator().validate(m)
    assert any("version" in i.lower() for i in issues), f"Expected version issue, got: {issues}"


# ---------------------------------------------------------------------------
# Test 7 — validator: bad sha256 (wrong length) → issue
# ---------------------------------------------------------------------------


def test_validator_bad_sha256():
    art = _good_artifact(sha256="deadbeef")  # too short
    issues = _validator().validate(_good_manifest(artifacts=[art]))
    assert any("sha256" in i.lower() for i in issues), f"Expected sha256 issue, got: {issues}"


# ---------------------------------------------------------------------------
# Test 8 — validator: unsupported arch → issue
# ---------------------------------------------------------------------------


def test_validator_unsupported_arch():
    art = _good_artifact(arch="s390x")  # type: ignore[arg-type]
    issues = _validator(allowed_archs=["x86_64", "aarch64"]).validate(
        _good_manifest(artifacts=[art])
    )
    assert any("arch" in i.lower() for i in issues), f"Expected arch issue, got: {issues}"


# ---------------------------------------------------------------------------
# Test 9 — validator: duplicate (arch, package_format) → issue
# ---------------------------------------------------------------------------


def test_validator_duplicate_arch_package_format():
    art1 = _good_artifact(name="art1")
    art2 = _good_artifact(name="art2")  # same arch + package_format as art1
    issues = _validator().validate(_good_manifest(artifacts=[art1, art2]))
    assert any("duplicate" in i.lower() for i in issues), f"Expected duplicate issue, got: {issues}"


# ---------------------------------------------------------------------------
# Test 10 — validator: missing signing key with require_signed=True → issue
# ---------------------------------------------------------------------------


def test_validator_missing_signing_key():
    m = _good_manifest()
    assert m.signing_key_fingerprint is None
    issues = _validator(require_signed=True).validate(m)
    assert any("signing" in i.lower() for i in issues), f"Expected signing issue, got: {issues}"


# ---------------------------------------------------------------------------
# Test 11 — validator: signing key present with require_signed=True → no issue
# ---------------------------------------------------------------------------


def test_validator_signing_key_present_ok():
    from service.isaac_assist_service.multimodal.release_linux_binary import LinuxReleaseManifest
    m = LinuxReleaseManifest(
        version="1.2.3",
        released_at="2026-05-13T12:00:00",
        artifacts=[_good_artifact()],
        signing_key_fingerprint="ABCD1234ABCD1234ABCD1234",
    )
    issues = _validator(require_signed=True).validate(m)
    assert not any("signing" in i.lower() for i in issues), f"Unexpected signing issue: {issues}"


# ---------------------------------------------------------------------------
# Test 12 — detect_distro_from_os_release: ubuntu 22.04 → "ubuntu_22_04"
# ---------------------------------------------------------------------------


def test_detect_distro_ubuntu_2204():
    m = _mod()
    os_release = 'ID=ubuntu\nVERSION_ID="22.04"\nNAME="Ubuntu"\n'
    assert m.detect_distro_from_os_release(os_release) == "ubuntu_22_04"


# ---------------------------------------------------------------------------
# Test 13 — detect_distro_from_os_release: fedora 40 → "fedora_40"
# ---------------------------------------------------------------------------


def test_detect_distro_fedora_40():
    m = _mod()
    os_release = 'ID=fedora\nVERSION_ID=40\nNAME="Fedora Linux"\n'
    assert m.detect_distro_from_os_release(os_release) == "fedora_40"


# ---------------------------------------------------------------------------
# Test 14 — detect_distro_from_os_release: empty/unknown → "unknown"
# ---------------------------------------------------------------------------


def test_detect_distro_unknown():
    m = _mod()
    assert m.detect_distro_from_os_release("") == "unknown"
    assert m.detect_distro_from_os_release("NAME=SomeObscureOS\n") == "unknown"


# ---------------------------------------------------------------------------
# Test 15 — expected_package_formats_for_distro("ubuntu_22_04") → contains "deb"
# ---------------------------------------------------------------------------


def test_expected_formats_ubuntu_contains_deb():
    m = _mod()
    fmts = m.expected_package_formats_for_distro("ubuntu_22_04")
    assert "deb" in fmts, f"Expected 'deb' in {fmts}"


# ---------------------------------------------------------------------------
# Test 16 — expected_package_formats_for_distro("fedora_40") → contains "rpm"
# ---------------------------------------------------------------------------


def test_expected_formats_fedora_contains_rpm():
    m = _mod()
    fmts = m.expected_package_formats_for_distro("fedora_40")
    assert "rpm" in fmts, f"Expected 'rpm' in {fmts}"


# ---------------------------------------------------------------------------
# Test 17 — expected_package_formats_for_distro("arch") → tar_gz + appimage
# ---------------------------------------------------------------------------


def test_expected_formats_arch():
    m = _mod()
    fmts = m.expected_package_formats_for_distro("arch")
    assert "tar_gz" in fmts
    assert "appimage" in fmts


# ---------------------------------------------------------------------------
# Test 18 — save/load JSON round-trip via tmp_path
# ---------------------------------------------------------------------------


def test_save_load_manifest_roundtrip(tmp_path):
    from service.isaac_assist_service.multimodal.release_linux_binary import (
        LinuxBinaryArtifact,
        LinuxReleaseManifest,
        save_manifest_json,
        load_manifest_json,
        MANIFEST_FILENAME,
    )

    art = LinuxBinaryArtifact(
        name="ia-service_2.0.0_amd64.deb",
        arch="x86_64",
        distro="ubuntu_24_04",
        package_format="deb",
        version="2.0.0",
        file_path="/dist/ia-service_2.0.0_amd64.deb",
        sha256="b" * 64,
        sha512="c" * 128,
        size_bytes=12345678,
        depends_on=["libssl3", "libcurl4"],
    )
    manifest = LinuxReleaseManifest(
        version="2.0.0",
        released_at="2026-05-13T00:00:00",
        artifacts=[art],
        release_notes="First stable release.",
        changelog_url="https://example.com/changelog",
        signing_key_fingerprint="DEADBEEF12345678",
    )

    out_path = tmp_path / MANIFEST_FILENAME
    save_manifest_json(manifest, out_path)
    assert out_path.exists(), "Manifest file was not created"

    loaded = load_manifest_json(out_path)

    assert loaded.version == manifest.version
    assert loaded.released_at == manifest.released_at
    assert loaded.release_notes == manifest.release_notes
    assert loaded.changelog_url == manifest.changelog_url
    assert loaded.signing_key_fingerprint == manifest.signing_key_fingerprint

    assert len(loaded.artifacts) == 1
    la = loaded.artifacts[0]
    assert la.name == art.name
    assert la.arch == art.arch
    assert la.distro == art.distro
    assert la.package_format == art.package_format
    assert la.sha256 == art.sha256
    assert la.sha512 == art.sha512
    assert la.size_bytes == art.size_bytes
    assert la.depends_on == art.depends_on


# ---------------------------------------------------------------------------
# Test 19 — round-trip: validator accepts loaded manifest
# ---------------------------------------------------------------------------


def test_roundtrip_manifest_passes_validation(tmp_path):
    from service.isaac_assist_service.multimodal.release_linux_binary import (
        save_manifest_json,
        load_manifest_json,
    )
    m = _good_manifest()
    path = tmp_path / "manifest.json"
    save_manifest_json(m, path)
    loaded = load_manifest_json(path)
    issues = _validator().validate(loaded)
    assert issues == [], f"Loaded manifest failed validation: {issues}"
