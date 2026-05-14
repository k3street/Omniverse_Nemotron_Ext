"""POSITIVE fixtures for Q21b silent-failure check — must be flagged."""
from typing import Dict


def returns_success_false_only() -> Dict:
    """Genuine silent failure — no error info."""
    return {"success": False}  # AUDIT_EXPECT: Q21b hit


def returns_success_false_with_unrelated_keys() -> Dict:
    """success=False with only non-error keys."""
    return {"success": False, "name": "foo", "count": 0}  # AUDIT_EXPECT: Q21b hit
