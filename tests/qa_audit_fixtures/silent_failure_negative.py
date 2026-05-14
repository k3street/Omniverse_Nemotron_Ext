"""NEGATIVE fixtures for Q21b — all returns include error info, must NOT flag."""
from typing import Dict


def returns_with_error_key() -> Dict:
    """Classic error path."""
    return {"success": False, "error": "param required"}


def returns_with_output_key() -> Dict:
    """`output` is the convention in kit_tools.py — accepted alias."""
    return {"success": False, "output": "Kit RPC returned 500"}


def returns_with_reason_key() -> Dict:
    """`reason` is the convention in workflow gates."""
    return {"success": False, "reason": "workflow already cancelled"}


def returns_with_message_key() -> Dict:
    """`message` is also a common error-info alias."""
    return {"success": False, "message": "Unknown action"}


def returns_with_unpack() -> Dict:
    """`**err` spread — error info is in the unpacked dict."""
    err = {"error_type": "TimeoutError", "detail": "Kit unresponsive"}
    return {"service": "kit_rpc", "success": False, **err}


def returns_success_true_ignored() -> Dict:
    """success=True returns are out of scope for this check."""
    return {"success": True}
