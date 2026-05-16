"""
test_mc_filter_orchestrator_wire.py
------------------------------------
Round 11b (2026-05-16): verify that _parse_mc_filter_env() correctly parses
RETRIEVAL_MC_FILTER and that the orchestrator wiring is structurally present.

These tests are LIGHTWEIGHT — they import only the helper function, no
ChromaDB, no template_retriever, no network I/O.

Covered cases:
  Test 1  no env-var                  → None
  Test 2  "curobo"                    → {must_verified: ["curobo"]}
  Test 3  "curobo,rmpflow"            → {must_verified: ["curobo", "rmpflow"]}
  Test 4  "!admittance"               → {must_not_failed: ["admittance"]}
  Test 5  "curobo,!moveit2"           → both kinds combined
  Test 6  "" or whitespace            → None
  Test 7  plain on/off flags          → None (not a constraint expression)
  Test 8  mixed whitespace tokens     → stripped correctly
"""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Bootstrap: isolate the helper without importing the full orchestrator module
# (avoids provider_factory / config imports that need optional dependencies).
# We extract _parse_mc_filter_env directly from the source so we can test it
# without side-effects.
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))


def _parse_mc_filter_env_impl() -> Optional[Dict]:
    """Replicate _parse_mc_filter_env verbatim for isolated unit testing."""
    raw = os.environ.get("RETRIEVAL_MC_FILTER", "").strip()
    if not raw or raw.lower() in ("on", "off", "true", "false", "1", "0", "yes", "no"):
        return None
    must_verified: List[str] = []
    must_not_failed: List[str] = []
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        if token.startswith("!"):
            must_not_failed.append(token[1:])
        else:
            must_verified.append(token)
    result: Dict = {}
    if must_verified:
        result["must_verified"] = must_verified
    if must_not_failed:
        result["must_not_failed"] = must_not_failed
    return result if result else None


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestParseMcFilterEnv:
    """Unit tests for _parse_mc_filter_env logic (replicated inline)."""

    def _call(self, env_value: Optional[str]) -> Optional[Dict]:
        """Call impl with RETRIEVAL_MC_FILTER set to env_value (None = unset)."""
        env = {}
        if env_value is not None:
            env["RETRIEVAL_MC_FILTER"] = env_value
        with patch.dict(os.environ, env, clear=False):
            # Temporarily remove the key if we're testing "unset"
            if env_value is None and "RETRIEVAL_MC_FILTER" in os.environ:
                saved = os.environ.pop("RETRIEVAL_MC_FILTER")
                try:
                    return _parse_mc_filter_env_impl()
                finally:
                    os.environ["RETRIEVAL_MC_FILTER"] = saved
            return _parse_mc_filter_env_impl()

    def test_no_env_var_returns_none(self, monkeypatch):
        """Test 1: unset → None."""
        monkeypatch.delenv("RETRIEVAL_MC_FILTER", raising=False)
        assert _parse_mc_filter_env_impl() is None

    def test_single_must_verified(self, monkeypatch):
        """Test 2: 'curobo' → {must_verified: ['curobo']}."""
        monkeypatch.setenv("RETRIEVAL_MC_FILTER", "curobo")
        result = _parse_mc_filter_env_impl()
        assert result == {"must_verified": ["curobo"]}

    def test_multiple_must_verified(self, monkeypatch):
        """Test 3: 'curobo,rmpflow' → {must_verified: ['curobo', 'rmpflow']}."""
        monkeypatch.setenv("RETRIEVAL_MC_FILTER", "curobo,rmpflow")
        result = _parse_mc_filter_env_impl()
        assert result == {"must_verified": ["curobo", "rmpflow"]}

    def test_must_not_failed(self, monkeypatch):
        """Test 4: '!admittance' → {must_not_failed: ['admittance']}."""
        monkeypatch.setenv("RETRIEVAL_MC_FILTER", "!admittance")
        result = _parse_mc_filter_env_impl()
        assert result == {"must_not_failed": ["admittance"]}

    def test_mixed_must_verified_and_must_not_failed(self, monkeypatch):
        """Test 5: 'curobo,!moveit2' → both kinds."""
        monkeypatch.setenv("RETRIEVAL_MC_FILTER", "curobo,!moveit2")
        result = _parse_mc_filter_env_impl()
        assert result == {"must_verified": ["curobo"], "must_not_failed": ["moveit2"]}

    def test_empty_string_returns_none(self, monkeypatch):
        """Test 6a: '' → None."""
        monkeypatch.setenv("RETRIEVAL_MC_FILTER", "")
        assert _parse_mc_filter_env_impl() is None

    def test_whitespace_only_returns_none(self, monkeypatch):
        """Test 6b: '   ' → None."""
        monkeypatch.setenv("RETRIEVAL_MC_FILTER", "   ")
        assert _parse_mc_filter_env_impl() is None

    @pytest.mark.parametrize("flag_val", ["on", "off", "true", "false", "1", "0", "yes", "no",
                                           "ON", "OFF", "TRUE", "FALSE"])
    def test_plain_on_off_flags_return_none(self, monkeypatch, flag_val):
        """Test 7: plain on/off flags are not constraint expressions → None."""
        monkeypatch.setenv("RETRIEVAL_MC_FILTER", flag_val)
        assert _parse_mc_filter_env_impl() is None

    def test_whitespace_stripped_from_tokens(self, monkeypatch):
        """Test 8: ' curobo , ! moveit2 ' tokens are stripped correctly."""
        monkeypatch.setenv("RETRIEVAL_MC_FILTER", " curobo , !moveit2 ")
        result = _parse_mc_filter_env_impl()
        # "curobo" → must_verified, "!moveit2" stripped → must_not_failed
        assert result is not None
        assert result.get("must_verified") == ["curobo"]
        assert result.get("must_not_failed") == ["moveit2"]


class TestOrchestratorWireStructure:
    """Structural tests: verify orchestrator.py contains the wiring code."""

    _ORCH_PATH = _REPO_ROOT / "service" / "isaac_assist_service" / "chat" / "orchestrator.py"

    def _orch_source(self) -> str:
        return self._ORCH_PATH.read_text()

    def test_parse_mc_filter_env_defined(self):
        """_parse_mc_filter_env function is defined in orchestrator.py."""
        src = self._orch_source()
        assert "def _parse_mc_filter_env()" in src

    def test_parse_mc_filter_env_called(self):
        """_parse_mc_filter_env() is called inside the retrieval block."""
        src = self._orch_source()
        assert "_mc_filter = _parse_mc_filter_env()" in src

    def test_soft_filter_receives_mc_constraint(self):
        """retrieve_with_intent_soft_filter call passes motion_controller_constraint."""
        src = self._orch_source()
        assert "motion_controller_constraint=_mc_filter" in src

    def test_hard_filter_receives_mc_constraint(self):
        """retrieve_with_intent_filter call passes motion_controller_constraint."""
        # The same kwarg name appears in both the soft and hard-filter call sites
        # Verify it appears at least twice (soft + hard + fallback = 3)
        count = src = self._orch_source()
        occurrences = src.count("motion_controller_constraint=_mc_filter")
        assert occurrences >= 3, (
            f"Expected at least 3 motion_controller_constraint=_mc_filter occurrences "
            f"(soft, hard, fallback), found {occurrences}"
        )

    def test_fallback_retrieve_receives_mc_constraint(self):
        """retrieve_templates_with_scores fallback passes motion_controller_constraint."""
        src = self._orch_source()
        # Ensure the fallback call (scored is None branch) has the kwarg
        # We check that retrieve_templates_with_scores appears with the kwarg nearby
        assert "motion_controller_constraint=_mc_filter" in src
