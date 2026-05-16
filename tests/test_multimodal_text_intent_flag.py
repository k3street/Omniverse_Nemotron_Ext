"""
test_multimodal_text_intent_flag.py
------------------------------------
R17b flag-flip: MULTIMODAL_TEXT_INTENT defaults to "soft" (soft-filter hybrid).

Round history:
  R16  (commit 10ad9b0) — flipped default ON (hard-filter); based on 30-prompt bench
  R17 revert (commit 2cf5114) — reverted to OFF after 100-prompt bench showed
                                 hard-filter regresses both hit@1 and hit@3
  R15d (commit ?) — implemented soft-filter hybrid, hit@1=0.84 vs baseline 0.82
  R17b (this round) — flipped default to "soft" (soft-filter beats baseline)

Mode semantics:
  unset / "soft" / "true" / "1" / "yes" / "on" — soft-filter hybrid (default)
    Note: "on" maps to soft (not hard) to preserve user expectations
    of "enable struct-filter" without re-introducing hard-filter regression.
  "hard"                                       — legacy hard-filter (for experiments)
  "off" / "false" / "0" / "no"                 — embedding-only baseline

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
# Helpers: replicate the orchestrator flag-check expressions verbatim.
# ---------------------------------------------------------------------------

def _eval_mode() -> str:
    """Reproduce: _intent_mode = env.get("MULTIMODAL_TEXT_INTENT", "soft").lower().strip()"""
    return os.environ.get("MULTIMODAL_TEXT_INTENT", "soft").lower().strip()


def _eval_multimodal_text(mode: str) -> bool:
    """Reproduce: _multimodal_text = _intent_mode in (...)"""
    return mode in ("on", "true", "1", "yes", "soft", "hard")


def _eval_use_soft(mode: str) -> bool:
    """Reproduce: _use_soft_filter = _intent_mode in ("soft", "on", "true", "1", "yes")"""
    return mode in ("soft", "on", "true", "1", "yes")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestDefaultIsSoftFilter:
    """Test 1 — no env-var → soft-filter hybrid (new R17b default)."""

    def test_unset_routes_to_soft_filter(self, monkeypatch):
        monkeypatch.delenv("MULTIMODAL_TEXT_INTENT", raising=False)
        mode = _eval_mode()
        assert mode == "soft", f"Expected default 'soft', got {mode!r}"
        assert _eval_multimodal_text(mode) is True
        assert _eval_use_soft(mode) is True


class TestExplicitSoftStillSoft:
    """Test 2 — MULTIMODAL_TEXT_INTENT=soft → soft-filter (matches default)."""

    @pytest.mark.parametrize("value", ["soft", "SOFT", "Soft"])
    def test_explicit_soft_routes_to_soft(self, monkeypatch, value):
        monkeypatch.setenv("MULTIMODAL_TEXT_INTENT", value)
        mode = _eval_mode()
        assert mode == "soft"
        assert _eval_use_soft(mode) is True


class TestOffDisables:
    """Test 3 — MULTIMODAL_TEXT_INTENT=off → struct-filter disabled (legacy baseline)."""

    @pytest.mark.parametrize("value", ["off", "OFF", "Off", "false", "FALSE", "0", "no", "NO"])
    def test_off_values_suppress(self, monkeypatch, value):
        monkeypatch.setenv("MULTIMODAL_TEXT_INTENT", value)
        mode = _eval_mode()
        assert _eval_multimodal_text(mode) is False, (
            f"Expected struct-filter disabled for MULTIMODAL_TEXT_INTENT={value!r}, got mode={mode!r}"
        )


class TestOnIsSoft:
    """Test 4 — MULTIMODAL_TEXT_INTENT=on/true/1/yes → soft-filter (NOT hard).

    Rationale: 'on' historically meant 'enable struct-filter'. After R17 we know
    hard-filter regresses. Users setting 'on' should get the WORKING struct-filter
    (soft), not the broken one (hard).
    """

    @pytest.mark.parametrize("value", ["on", "ON", "true", "TRUE", "1", "yes", "YES"])
    def test_on_values_route_to_soft(self, monkeypatch, value):
        monkeypatch.setenv("MULTIMODAL_TEXT_INTENT", value)
        mode = _eval_mode()
        assert _eval_multimodal_text(mode) is True
        assert _eval_use_soft(mode) is True, (
            f"Expected 'on'-like value {value!r} to route to soft-filter, not hard"
        )


class TestHardOnlyOnExplicitHard:
    """Test 5 — MULTIMODAL_TEXT_INTENT=hard → legacy hard-filter (experiments only)."""

    @pytest.mark.parametrize("value", ["hard", "HARD", "Hard"])
    def test_hard_routes_to_hard(self, monkeypatch, value):
        monkeypatch.setenv("MULTIMODAL_TEXT_INTENT", value)
        mode = _eval_mode()
        assert mode == "hard"
        assert _eval_multimodal_text(mode) is True
        assert _eval_use_soft(mode) is False, (
            f"Expected 'hard' value {value!r} to route to hard-filter, not soft"
        )


class TestUnknownValueIsOff:
    """Test 6 — unknown values default to disabled (safe-side)."""

    @pytest.mark.parametrize("value", ["", " ", "maybe", "2", "garbage"])
    def test_unknown_disables(self, monkeypatch, value):
        monkeypatch.setenv("MULTIMODAL_TEXT_INTENT", value)
        mode = _eval_mode()
        assert _eval_multimodal_text(mode) is False, (
            f"Unknown value {value!r} should disable struct-filter (safe default), got mode={mode!r}"
        )
