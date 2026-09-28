"""Validate model-provider credentials before a run spends anything on them.

An unusable key is cheap to spot and expensive to discover late: the planner
boots the simulator, builds the scene, and only then retries its way through
several 401s. Checking here costs nothing and fails in the first second.
"""
from __future__ import annotations

import re

# Placeholders copied out of a README or a chat message look enough like a key
# to pass an emptiness check. "sk-..." is the common one.
PLACEHOLDER = re.compile(r"\.\.\.|xxx+|your[-_ ]?key|<.*>", re.IGNORECASE)
MIN_PLAUSIBLE_LENGTH = 20


def check_api_key(name: str, key: str | None) -> str:
    """Return the key, or explain why it cannot work.

    The shell-beats-dotenv note is in both messages on purpose: python-dotenv
    does not override a variable that is already exported, so a placeholder
    pasted on the command line silently wins over a real key in .env -- which
    is exactly how this fails in practice.
    """
    if not key:
        raise RuntimeError(
            f"Set {name} in the environment, or put it in .env. "
            "A variable already exported in the shell wins over .env."
        )
    if PLACEHOLDER.search(key) or len(key) < MIN_PLAUSIBLE_LENGTH:
        raise RuntimeError(
            f"{name} looks like a placeholder, not a key: {key[:12]!r}. "
            "A value exported in the shell overrides .env, so unset it to use "
            "the key stored there."
        )
    return key
