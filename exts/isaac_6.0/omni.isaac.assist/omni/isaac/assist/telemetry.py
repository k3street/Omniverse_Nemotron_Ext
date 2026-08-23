"""Optional local tracing for the Isaac Assist Kit extension.

Telemetry is disabled by default and never imports or configures OpenTelemetry
until the user opts in through ``IA_TELEMETRY`` or the shared preference file.
"""
from __future__ import annotations

import functools
import inspect
import logging
import os
from pathlib import Path
from typing import Any, Callable, TypeVar

logger = logging.getLogger("omni.isaac.assist.telemetry")

tracer = None
_provider = None
F = TypeVar("F", bound=Callable[..., Any])


def is_telemetry_enabled() -> bool:
    """Return whether the user explicitly enabled anonymous telemetry."""
    value = os.environ.get("IA_TELEMETRY", "").strip().lower()
    if value in {"1", "true", "on", "yes"}:
        return True
    if value in {"0", "false", "off", "no"}:
        return False
    preference = Path.home() / ".isaac_assist" / "telemetry.txt"
    try:
        return preference.is_file() and preference.read_text(encoding="utf-8").strip().lower() in {
            "enabled", "on", "1",
        }
    except OSError:
        return False


def init_telemetry() -> bool:
    """Initialize a local span exporter once; return whether tracing is active."""
    global tracer, _provider
    if tracer is not None:
        return True
    if not is_telemetry_enabled():
        logger.info("[IsaacAssist Telemetry] Disabled (opt in with IA_TELEMETRY=1).")
        return False
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SimpleSpanProcessor
    except ImportError as exc:
        logger.warning("[IsaacAssist Telemetry] Optional OpenTelemetry SDK unavailable: %s", exc)
        return False

    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
    trace.set_tracer_provider(provider)
    _provider = provider
    tracer = trace.get_tracer("omni.isaac.assist")
    logger.info("[IsaacAssist Telemetry] OpenTelemetry initialized.")
    return True


def shutdown_telemetry() -> None:
    """Flush and release the provider created by :func:`init_telemetry`."""
    global tracer, _provider
    provider, _provider, tracer = _provider, None, None
    if provider is not None:
        try:
            provider.shutdown()
        except Exception as exc:
            logger.warning("[IsaacAssist Telemetry] Shutdown failed: %s", exc)


def trace_error(operation_name: str) -> Callable[[F], F]:
    """Trace failures when enabled and always preserve exception logging."""
    def decorator(func: F) -> F:
        if inspect.iscoroutinefunction(func):
            @functools.wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                if tracer is None:
                    try:
                        return await func(*args, **kwargs)
                    except Exception:
                        logger.exception("[IsaacAssist Crash] %s failed", operation_name)
                        raise
                from opentelemetry import trace
                with tracer.start_as_current_span(operation_name) as span:
                    try:
                        return await func(*args, **kwargs)
                    except Exception as exc:
                        span.record_exception(exc)
                        span.set_status(trace.StatusCode.ERROR, str(exc))
                        logger.exception("[IsaacAssist Telemetry Crash] %s failed", operation_name)
                        raise
            return async_wrapper  # type: ignore[return-value]

        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            if tracer is None:
                try:
                    return func(*args, **kwargs)
                except Exception:
                    logger.exception("[IsaacAssist Crash] %s failed", operation_name)
                    raise
            from opentelemetry import trace
            with tracer.start_as_current_span(operation_name) as span:
                try:
                    return func(*args, **kwargs)
                except Exception as exc:
                    span.record_exception(exc)
                    span.set_status(trace.StatusCode.ERROR, str(exc))
                    logger.exception("[IsaacAssist Telemetry Crash] %s failed", operation_name)
                    raise
        return sync_wrapper  # type: ignore[return-value]
    return decorator
