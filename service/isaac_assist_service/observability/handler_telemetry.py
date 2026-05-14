"""Handler telemetry decorator.

Wraps every ``_handle_*`` function to auto-emit a structured event
recording handler name, success/failure, and wall-clock duration.

Usage::

    from service.isaac_assist_service.observability.handler_telemetry import with_telemetry

    @with_telemetry
    async def _handle_foo(args):
        ...
"""
from __future__ import annotations

import asyncio
import functools
import json
import logging
import time
from typing import Any, Callable

logger = logging.getLogger("isaac_assist.telemetry")


def emit_event(event_name: str, payload: dict) -> None:
    """Emit a structured telemetry event.

    Logs a JSON line to the ``isaac_assist.telemetry`` logger so the
    record can be parsed by log-aggregation pipelines.  Failures are
    swallowed — telemetry must never break the calling code path.
    """
    try:
        logger.info(json.dumps({"event": event_name, **payload}))
    except Exception:
        pass


def with_telemetry(handler: Callable) -> Callable:
    """Decorator that emits a ``handler.complete`` or ``handler.error`` event
    for each invocation of the wrapped ``_handle_*`` function.

    Supports both async and sync handlers.  The decorator is transparent:
    it preserves the return value exactly, re-raises any exception, and
    adds no new dependencies beyond stdlib.
    """
    handler_name = handler.__name__.removeprefix("_handle_")

    if asyncio.iscoroutinefunction(handler):
        @functools.wraps(handler)
        async def async_wrapper(args: Any) -> Any:
            t0 = time.perf_counter()
            try:
                result = await handler(args)
                if isinstance(result, dict):
                    success = bool(result.get("success", "error" not in result))
                else:
                    success = True
                duration_ms = (time.perf_counter() - t0) * 1000
                emit_event(
                    "handler.complete",
                    {
                        "handler": handler_name,
                        "success": success,
                        "duration_ms": round(duration_ms, 2),
                    },
                )
                return result
            except Exception as exc:
                emit_event(
                    "handler.error",
                    {
                        "handler": handler_name,
                        "error_type": type(exc).__name__,
                    },
                )
                raise

        return async_wrapper
    else:
        @functools.wraps(handler)
        def sync_wrapper(args: Any) -> Any:
            t0 = time.perf_counter()
            try:
                result = handler(args)
                if isinstance(result, dict):
                    success = bool(result.get("success", "error" not in result))
                else:
                    success = True
                duration_ms = (time.perf_counter() - t0) * 1000
                emit_event(
                    "handler.complete",
                    {
                        "handler": handler_name,
                        "success": success,
                        "duration_ms": round(duration_ms, 2),
                    },
                )
                return result
            except Exception as exc:
                emit_event(
                    "handler.error",
                    {
                        "handler": handler_name,
                        "error_type": type(exc).__name__,
                    },
                )
                raise

        return sync_wrapper
