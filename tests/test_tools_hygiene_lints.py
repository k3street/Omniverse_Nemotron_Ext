"""Phase 17 — tests for pre-commit lint scripts.

Two scripts:
  1. `scripts/lint/no_handler_in_dispatch.py` — forbids new handlers
     in `tool_executor.py` (post-Phase-9 contract).
  2. `scripts/lint/regen_models_check.py` — warns/fails when
     `tool_schemas.py` is newer than `handlers/_models.py`.

Per spec/IA_FULL_SPEC_2026-05-10.md Phase 17.
"""
from __future__ import annotations

import importlib.util
import sys
import textwrap
from pathlib import Path

import pytest

pytestmark = pytest.mark.l0

_REPO_ROOT = Path(__file__).parent.parent


def _load_script(name: str):
    """Load `scripts/lint/<name>.py` as a module (script dirs aren't packages)."""
    path = _REPO_ROOT / "scripts" / "lint" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# no_handler_in_dispatch


@pytest.fixture(scope="module")
def nhd():
    return _load_script("no_handler_in_dispatch")


def test_nhd_flags_handler_def(nhd, tmp_path):
    """A top-level `_handle_X` def is flagged."""
    src = textwrap.dedent(
        """
        def _handle_my_new_tool(args):
            return {"ok": True}
        """
    )
    p = tmp_path / "fake_executor.py"
    p.write_text(src)
    violations = nhd.scan(p)
    assert len(violations) == 1
    assert "_handle_my_new_tool" in violations[0][1]


def test_nhd_flags_gen_def(nhd, tmp_path):
    """A top-level `_gen_X` def is flagged."""
    src = "def _gen_my_codegen(args):\n    return 'code'\n"
    p = tmp_path / "fake_executor.py"
    p.write_text(src)
    violations = nhd.scan(p)
    assert len(violations) == 1
    assert "_gen_my_codegen" in violations[0][1]


def test_nhd_flags_fix_error_after_migration(nhd, tmp_path):
    """Post-Phase-9-followup (2026-05-13): `_handle_fix_error` was migrated
    to handlers/diagnostics.py. The allowlist is now empty — even
    `_handle_fix_error` in tool_executor.py would be flagged.
    """
    src = "def _handle_fix_error(args):\n    return ''\n"
    p = tmp_path / "fake_executor.py"
    p.write_text(src)
    violations = nhd.scan(p)
    assert len(violations) == 1
    assert "_handle_fix_error" in violations[0][1]


def test_nhd_flags_dispatch_assignment(nhd, tmp_path):
    """A `DATA_HANDLERS["X"] = _h` line is flagged."""
    src = textwrap.dedent(
        """
        DATA_HANDLERS = {}
        DATA_HANDLERS["my_tool"] = some_handler
        """
    )
    p = tmp_path / "fake_executor.py"
    p.write_text(src)
    violations = nhd.scan(p)
    assert len(violations) == 1
    assert "my_tool" in violations[0][1]


def test_nhd_flags_fix_error_dispatch_after_migration(nhd, tmp_path):
    """`CODE_GEN_HANDLERS["fix_error"] = ...` is also flagged after the
    Phase 9 followup migration. The allowlist is empty.
    """
    src = textwrap.dedent(
        """
        CODE_GEN_HANDLERS = {}
        CODE_GEN_HANDLERS["fix_error"] = _handle_fix_error
        """
    )
    p = tmp_path / "fake_executor.py"
    p.write_text(src)
    violations = nhd.scan(p)
    assert len(violations) == 1
    assert "fix_error" in violations[0][1]


def test_nhd_passes_on_real_tool_executor(nhd):
    """Live tool_executor.py must lint clean (Phase 9 contract holds)."""
    real_path = (
        _REPO_ROOT
        / "service"
        / "isaac_assist_service"
        / "chat"
        / "tools"
        / "tool_executor.py"
    )
    assert real_path.exists()
    violations = nhd.scan(real_path)
    assert violations == [], (
        "Live tool_executor.py contains handler defs or dispatch assignments "
        f"that violate Phase 9 contract: {violations[:5]}"
    )


# ---------------------------------------------------------------------------
# regen_models_check


@pytest.fixture(scope="module")
def rmc():
    return _load_script("regen_models_check")


def test_rmc_soft_fail_when_models_missing(rmc, monkeypatch, tmp_path, capsys):
    """Pre-Phase-10: `_models.py` missing → soft warning, exit 0."""
    fake_schemas = tmp_path / "schemas.py"
    fake_models = tmp_path / "models.py"
    fake_schemas.write_text("# fake")
    # Note: do NOT create models — that's the soft-fail case
    monkeypatch.setattr(rmc, "_SCHEMAS", fake_schemas)
    monkeypatch.setattr(rmc, "_MODELS", fake_models)
    exit_code = rmc.main([])
    assert exit_code == 0
    err = capsys.readouterr().err
    assert "does not exist yet" in err


def test_rmc_hard_fail_when_strict_and_missing(rmc, monkeypatch, tmp_path):
    """`--strict`: `_models.py` missing → exit 1."""
    fake_schemas = tmp_path / "schemas.py"
    fake_models = tmp_path / "models.py"
    fake_schemas.write_text("# fake")
    monkeypatch.setattr(rmc, "_SCHEMAS", fake_schemas)
    monkeypatch.setattr(rmc, "_MODELS", fake_models)
    exit_code = rmc.main(["--strict"])
    assert exit_code == 1


def test_rmc_passes_when_models_newer(rmc, monkeypatch, tmp_path):
    """`_models.py` newer than `tool_schemas.py` → exit 0."""
    import time
    schemas = tmp_path / "schemas.py"
    models = tmp_path / "models.py"
    schemas.write_text("# fake")
    time.sleep(0.01)
    models.write_text("# fake")
    monkeypatch.setattr(rmc, "_SCHEMAS", schemas)
    monkeypatch.setattr(rmc, "_MODELS", models)
    exit_code = rmc.main([])
    assert exit_code == 0


def test_rmc_fails_when_models_stale(rmc, monkeypatch, tmp_path):
    """`_models.py` older than `tool_schemas.py` → exit 1."""
    import os
    import time
    models = tmp_path / "models.py"
    schemas = tmp_path / "schemas.py"
    models.write_text("# fake")
    time.sleep(0.01)
    schemas.write_text("# fake")
    # Force older mtime on models
    os.utime(models, (1, 1))
    monkeypatch.setattr(rmc, "_SCHEMAS", schemas)
    monkeypatch.setattr(rmc, "_MODELS", models)
    exit_code = rmc.main([])
    assert exit_code == 1
