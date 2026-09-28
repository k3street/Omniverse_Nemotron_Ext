"""L0 tests for the provider credential check.

The case that matters is the one that actually happened: a placeholder pasted
from a command in chat passed the emptiness check, python-dotenv declined to
override it with the real key in .env, and the run booted the simulator before
failing on repeated 401s.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.l0

from scripts.provider_credentials import check_api_key

REAL = "sk-proj-" + "a" * 150


def test_a_real_looking_key_is_returned():
    assert check_api_key("OPENAI_API_KEY", REAL) == REAL


def test_the_sk_ellipsis_placeholder_is_rejected():
    with pytest.raises(RuntimeError, match="placeholder"):
        check_api_key("OPENAI_API_KEY", "sk-...")


@pytest.mark.parametrize("value", ["sk-xxxxxxxxxxxxxxxxxxxxx", "your-key-here-abcdefghij", "<YOUR_KEY_HERE_ABCDEF>"])
def test_other_placeholder_spellings_are_rejected(value):
    with pytest.raises(RuntimeError, match="placeholder"):
        check_api_key("OPENAI_API_KEY", value)


def test_a_short_key_is_rejected_even_without_placeholder_markers():
    with pytest.raises(RuntimeError, match="placeholder"):
        check_api_key("OPENAI_API_KEY", "sk-abc123")


def test_a_missing_key_says_where_to_put_one():
    with pytest.raises(RuntimeError, match="\\.env"):
        check_api_key("OPENAI_API_KEY", None)


def test_both_messages_warn_that_the_shell_beats_dotenv():
    # This is the non-obvious part: exporting a placeholder silently wins.
    for value in (None, "sk-..."):
        with pytest.raises(RuntimeError) as caught:
            check_api_key("OPENAI_API_KEY", value)
        assert "shell" in str(caught.value)
