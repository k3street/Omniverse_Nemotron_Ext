"""Compatibility helpers for the optional Isaac Sim Python fallback."""
from __future__ import annotations

import asyncio
import functools
import os
import sys
from typing import Any, Callable, TypeVar

T = TypeVar("T")


def is_isaac_python() -> bool:
    """Return whether the service is using Kit's thread-limited interpreter."""
    executable = sys.executable.replace("\\", "/").lower()
    return os.environ.get("ISAAC_ASSIST_ISAAC_PYTHON") == "1" or "/kit/python/" in executable


async def run_sync_compatible(fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """Run sync work without hanging Isaac Python's executor bridge.

    Normal service environments retain worker-thread isolation. Isaac Sim's
    bundled standalone interpreter hangs in ``run_in_executor``/``to_thread``;
    there the callable runs inline as a compatibility fallback.
    """
    call = functools.partial(fn, *args, **kwargs)
    if is_isaac_python():
        return call()
    return await asyncio.to_thread(call)
