"""L0 unit tests for llm_gemini's retry-after parsing.

Covers _retry_after_wait — the helper that combines exponential backoff
with provider-supplied retry-after header. Pure function; no aiohttp.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.l0


def _imp():
    from service.isaac_assist_service.chat.llm_gemini import _retry_after_wait
    return _retry_after_wait


def test_no_header_returns_backoff():
    f = _imp()
    assert f(2.0, None) == 2.0


def test_empty_header_returns_backoff():
    f = _imp()
    assert f(2.0, "") == 2.0


def test_numeric_seconds_longer_than_backoff_used():
    """retry-after=30 with backoff=2 → use retry-after."""
    f = _imp()
    assert f(2.0, "30") == 30.0


def test_numeric_seconds_shorter_than_backoff_keeps_backoff():
    """retry-after=1 with backoff=2 → keep backoff (never shorten)."""
    f = _imp()
    assert f(2.0, "1") == 2.0


def test_numeric_seconds_capped_at_max():
    """retry-after=200 with max_wait=60 → 60."""
    f = _imp()
    assert f(2.0, "200", max_wait_s=60.0) == 60.0


def test_zero_retry_after_ignored():
    f = _imp()
    assert f(2.0, "0") == 2.0


def test_negative_retry_after_ignored():
    f = _imp()
    assert f(2.0, "-5") == 2.0


def test_http_date_retry_after_ignored():
    """HTTP-date form is intentionally not parsed."""
    f = _imp()
    assert f(2.0, "Wed, 21 Oct 2026 07:28:00 GMT") == 2.0


def test_garbage_retry_after_ignored():
    f = _imp()
    assert f(2.0, "abc") == 2.0


def test_decimal_seconds_accepted():
    f = _imp()
    assert f(2.0, "30.5") == 30.5


def test_max_wait_default_is_60_seconds():
    """Default cap should be 60s."""
    f = _imp()
    assert f(2.0, "120") == 60.0


def test_custom_max_wait_respected():
    f = _imp()
    assert f(2.0, "30", max_wait_s=10.0) == 10.0


def test_zero_backoff_with_valid_retry_after():
    """Edge: zero backoff + retry-after → use retry-after."""
    f = _imp()
    assert f(0.0, "5") == 5.0


def test_huge_backoff_unchanged_by_short_retry_after():
    """Edge: backoff already huge, retry-after smaller → keep backoff
    BUT clamped to max_wait_s."""
    f = _imp()
    # backoff=120, retry-after=30, max=60 → max(120, min(30, 60)) = max(120,30) = 120
    # but caller's responsibility to clamp backoff itself; this helper
    # just respects retry-after as a floor.
    assert f(120.0, "30") == 120.0
