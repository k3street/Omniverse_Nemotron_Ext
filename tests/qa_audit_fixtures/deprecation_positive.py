"""POSITIVE fixtures for Q3 (utcnow) + Q4 (get_event_loop)."""
import asyncio
from datetime import datetime


def use_utcnow():
    """Deprecated — must be flagged by Q3."""
    return datetime.utcnow()  # AUDIT_EXPECT: Q3 hit at this line


def use_utcnow_twice():
    """Two violations on different lines."""
    a = datetime.utcnow()  # AUDIT_EXPECT: Q3 hit
    b = datetime.utcnow()  # AUDIT_EXPECT: Q3 hit
    return a, b


def use_get_event_loop():
    """Deprecated outside run_stdio — must be flagged by Q4."""
    loop = asyncio.get_event_loop()  # AUDIT_EXPECT: Q4 hit at this line
    return loop


def run_stdio():
    """Whitelist function — get_event_loop here is allowed."""
    # AUDIT_EXPECT: Q4 does NOT flag this even though it's a call
    return asyncio.get_event_loop()
