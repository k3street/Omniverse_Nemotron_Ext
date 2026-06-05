"""NEGATIVE fixtures for Q3 (utcnow) + Q4 (get_event_loop) — must NOT flag."""
import asyncio
from datetime import datetime, timezone


def use_now_tz():
    """Correct replacement for utcnow — must not be flagged."""
    return datetime.now(timezone.utc)


def use_running_loop():
    """get_running_loop is the modern API — must not be flagged."""
    return asyncio.get_running_loop()


def utcnow_in_string_or_comment():
    """The string 'datetime.utcnow()' as text must not be flagged.

    Audit is AST-based, not grep-based, so this string is safe:
    >>> datetime.utcnow()  # docstring example
    """
    msg = "Old code used datetime.utcnow() — switched to datetime.now(timezone.utc)"
    return msg
