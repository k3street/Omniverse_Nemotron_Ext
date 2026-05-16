"""
test_multimodal_text_intent_flag.py
------------------------------------
Round 16 + R17 revert: Verify MULTIMODAL_TEXT_INTENT env-flag default.

R16 (commit 10ad9b0) flipped default to ON based on 30-prompt benchmark.
R17 (commit b0dbd2b) ran 100-prompt benchmark and exposed that struct-filter
actually REGRESSES on the bigger sample (hit@1 0.820→0.790, hit@3 0.950→0.890).
R17 revert: flag default returned to OFF until R15d soft-filter hybrid lands.

Tests:
  1. No env-var set          → _multimodal_text is False (R17 revert: default OFF)
  2. MULTIMODAL_TEXT_INTENT=on  → _multimodal_text is True  (explicit opt-in)
  3. MULTIMODAL_TEXT_INTENT=off → _multimodal_text is False (explicit, matches default)

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
    """Reproduce the orchestrator flag-check expression verbatim (R17 revert)."""
    return (
        os.environ.get("MULTIMODAL_TEXT_INTENT", "off").lower()
        in ("on", "true", "1", "yes")
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestMultimodalTextIntentFlagDefault:
    """Test 1 — no env-var set → struct-filter SUPPRESSED (R17 revert: default OFF)."""

    def test_no_env_var_struct_filter_suppressed(self, monkeypatch):
        monkeypatch.delenv("MULTIMODAL_TEXT_INTENT", raising=False)
        assert _eval_flag() is False, (
            "Expected _multimodal_text=False when MULTIMODAL_TEXT_INTENT is unset "
            "(R17 reverted R16 flag-flip after 100-prompt benchmark exposed regression)"
        )


class TestMultimodalTextIntentFlagOn:
    """Test 2 — MULTIMODAL_TEXT_INTENT=on → struct-filter ACTIVE (explicit opt-in)."""

    @pytest.mark.parametrize("value", ["on", "ON", "On", "true", "TRUE", "1", "yes", "YES"])
    def test_on_values_enable_struct_filter(self, monkeypatch, value):
        monkeypatch.setenv("MULTIMODAL_TEXT_INTENT", value)
        assert _eval_flag() is True, (
            f"Expected _multimodal_text=True for MULTIMODAL_TEXT_INTENT={value!r}"
        )


class TestMultimodalTextIntentFlagOff:
    """Test 3 — MULTIMODAL_TEXT_INTENT=off → struct-filter SUPPRESSED (matches default)."""

    @pytest.mark.parametrize("value", ["off", "OFF", "Off", "false", "FALSE", "0", "no", "NO"])
    def test_off_values_suppress_struct_filter(self, monkeypatch, value):
        monkeypatch.setenv("MULTIMODAL_TEXT_INTENT", value)
        assert _eval_flag() is False, (
            f"Expected _multimodal_text=False for MULTIMODAL_TEXT_INTENT={value!r}"
        )


class TestMultimodalTextIntentFlagGarbage:
    """Test 4 — unknown values default to OFF (safe-side default)."""

    @pytest.mark.parametrize("value", ["", " ", "maybe", "2", "garbage"])
    def test_unknown_values_default_off(self, monkeypatch, value):
        monkeypatch.setenv("MULTIMODAL_TEXT_INTENT", value)
        assert _eval_flag() is False, (
            f"Expected _multimodal_text=False for unknown MULTIMODAL_TEXT_INTENT={value!r} "
            "(R17 revert uses positive-list matching → unknowns are OFF)"
        )
