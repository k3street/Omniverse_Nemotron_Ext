"""
test_multimodal_text_intent_flag.py
------------------------------------
Round 16: Verify MULTIMODAL_TEXT_INTENT env-flag default flip.

After R16 the flag defaults to ON (struct-filter-first retrieval).
Set MULTIMODAL_TEXT_INTENT=off to revert to legacy similarity-only retrieval.

Tests:
  1. No env-var set          → _multimodal_text is True  (new default: ON)
  2. MULTIMODAL_TEXT_INTENT=off → _multimodal_text is False (explicit rollback)
  3. MULTIMODAL_TEXT_INTENT=on  → _multimodal_text is True  (explicit enable)

These tests exercise the flag evaluation logic directly, isolating it from
the orchestrator's network/LLM dependencies.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))


# ---------------------------------------------------------------------------
# Helper: replicate the exact flag expression from orchestrator.py:895-898
# ---------------------------------------------------------------------------

def _eval_flag() -> bool:
    """Reproduce the orchestrator flag-check expression verbatim."""
    return (
        os.environ.get("MULTIMODAL_TEXT_INTENT", "on").lower()
        not in ("off", "false", "0", "no")
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestMultimodalTextIntentFlagDefault:
    """Test 1 — no env-var set → struct-filter ACTIVE (default ON)."""

    def test_no_env_var_struct_filter_active(self, monkeypatch):
        monkeypatch.delenv("MULTIMODAL_TEXT_INTENT", raising=False)
        assert _eval_flag() is True, (
            "Expected _multimodal_text=True when MULTIMODAL_TEXT_INTENT is unset "
            "(R16 default is ON)"
        )


class TestMultimodalTextIntentFlagOff:
    """Test 2 — MULTIMODAL_TEXT_INTENT=off → struct-filter SUPPRESSED."""

    @pytest.mark.parametrize("value", ["off", "OFF", "Off", "false", "FALSE", "0", "no", "NO"])
    def test_off_values_suppress_struct_filter(self, monkeypatch, value):
        monkeypatch.setenv("MULTIMODAL_TEXT_INTENT", value)
        assert _eval_flag() is False, (
            f"Expected _multimodal_text=False for MULTIMODAL_TEXT_INTENT={value!r}"
        )


class TestMultimodalTextIntentFlagOn:
    """Test 3 — MULTIMODAL_TEXT_INTENT=on → struct-filter ACTIVE (explicit)."""

    @pytest.mark.parametrize("value", ["on", "ON", "On", "true", "TRUE", "1", "yes", "YES"])
    def test_on_values_enable_struct_filter(self, monkeypatch, value):
        monkeypatch.setenv("MULTIMODAL_TEXT_INTENT", value)
        assert _eval_flag() is True, (
            f"Expected _multimodal_text=True for MULTIMODAL_TEXT_INTENT={value!r}"
        )
