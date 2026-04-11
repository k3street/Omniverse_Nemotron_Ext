import functools
import logging

logger = logging.getLogger("omni.isaac.assist.telemetry")

tracer = None

def init_telemetry():
    global tracer
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SimpleSpanProcessor
        
        provider = TracerProvider()
        processor = SimpleSpanProcessor(ConsoleSpanExporter())
        provider.add_span_processor(processor)
        trace.set_tracer_provider(provider)
        
        tracer = trace.get_tracer("omni.isaac.assist")
        logger.info("[IsaacAssist Telemetry] OpenTelemetry successfully initialized.")
    except ImportError as e:
        logger.error(f"[IsaacAssist Telemetry] Boot Error: {e}")

def trace_error(operation_name: str):
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            global tracer
            if not tracer:
                # Fallback if telemetry failed to boot
                try:
                    return func(*args, **kwargs)
                except Exception as ex:
                    logger.error(f"[IsaacAssist Crash] {operation_name} failed: {ex}", exc_info=True)
                    raise
                    
            from opentelemetry import trace
            with tracer.start_as_current_span(operation_name) as span:
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    span.record_exception(e)
                    span.set_status(trace.StatusCode.ERROR, str(e))
                    logger.error(f"[IsaacAssist Telemetry Crash] {operation_name} explicitly failed: {e}", exc_info=True)
                    raise
        return wrapper
    return decorator
