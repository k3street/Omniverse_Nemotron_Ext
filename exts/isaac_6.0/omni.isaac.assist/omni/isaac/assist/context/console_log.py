"""
console_log.py
--------------
Captures recent carb log messages from inside Kit so they can be
summarised by the LLM ("any errors in console?").
"""
from __future__ import annotations
import carb
import threading
from typing import List, Dict
from collections import deque

_MAX_BUFFER = 200
_buffer: deque = deque(maxlen=_MAX_BUFFER)
_lock = threading.Lock()
_subscription = None


def attach_log_listener() -> None:
    """Call once from extension.on_startup() to start buffering log lines."""
    global _subscription

    def _on_log(level: int, filename: str, line: int, func: str, msg: str) -> None:
        entry = {
            "level": _level_name(level),
            "msg": msg.strip(),
            "source": f"{filename}:{line}",
        }
        with _lock:
            _buffer.append(entry)

    try:
        _subscription = carb.log.get_framework().add_listener(_on_log)
        carb.log_info("[IsaacAssist] Console log listener attached")
    except Exception as e:
        carb.log_warn(f"[IsaacAssist] Could not attach log listener: {e}")


def detach_log_listener() -> None:
    """Call from extension.on_shutdown()."""
    global _subscription
    if _subscription is not None:
        try:
            carb.log.get_framework().remove_listener(_subscription)
        except Exception:
            pass
        _subscription = None


def get_recent_logs(n: int = 50, min_level: str = "warning") -> List[Dict]:
    """
    Return the last n log entries at >= min_level.
    min_level options: 'verbose', 'info', 'warning', 'error', 'fatal'
    """
    level_rank = {"verbose": 0, "info": 1, "warning": 2, "error": 3, "fatal": 4}
    min_rank = level_rank.get(min_level.lower(), 2)

    with _lock:
        entries = list(_buffer)

    filtered = [e for e in entries if level_rank.get(e["level"].lower(), 0) >= min_rank]
    return filtered[-n:]


def _level_name(level: int) -> str:
    mapping = {
        carb.log.LEVEL_VERBOSE: "verbose",
        carb.log.LEVEL_INFO: "info",
        carb.log.LEVEL_WARN: "warning",
        carb.log.LEVEL_ERROR: "error",
        carb.log.LEVEL_FATAL: "fatal",
    }
    return mapping.get(level, "unknown")
