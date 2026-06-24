from __future__ import annotations

import logging

from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.trace import (
    NonRecordingSpan,
    Span,
    SpanContext,
    Status,
    StatusCode,
    TraceFlags,
    set_span_in_context,
)

from control_plane.observability.events import AgentEvent, EventType

logger = logging.getLogger("control_plane")


def _trace_context_from_ids(trace_id: str, turn_id: str) -> Context | None:
    """Build a parent context that pins the OTel trace_id to the audit `trace_id`
    (and span_id to the `turn_id`), so a turn's spans share the same trace_id used
    in the audit log and structured logs — making the three correlatable.

    Returns None when the ids aren't valid 128/64-bit hex (e.g. unit-test stubs),
    in which case the turn span starts a fresh OTel-generated trace."""
    try:
        tid = int(trace_id, 16)
        sid = int(turn_id[:16], 16)
    except (ValueError, TypeError):
        return None
    if tid == 0 or sid == 0:
        return None
    span_ctx = SpanContext(
        trace_id=tid,
        span_id=sid,
        is_remote=True,
        trace_flags=TraceFlags(TraceFlags.SAMPLED),
    )
    return set_span_in_context(NonRecordingSpan(span_ctx))


def setup_tracing(settings) -> trace.Tracer:
    """Install a global TracerProvider exporting OTLP to the Collector and
    auto-instrument httpx/asyncpg. Idempotent-ish; call once at startup.
    When otel is disabled, return a no-op tracer from the default provider."""
    if not getattr(settings, "otel_enabled", False):
        return trace.get_tracer("control_plane")

    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.sdk.trace.sampling import TraceIdRatioBased
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

    resource = Resource.create({"service.name": settings.otel_service_name})
    provider = TracerProvider(
        resource=resource,
        sampler=TraceIdRatioBased(settings.otel_traces_sampler_ratio),
    )
    exporter = OTLPSpanExporter(endpoint=settings.otel_exporter_otlp_endpoint, insecure=True)
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    HTTPXClientInstrumentor().instrument()
    try:
        from opentelemetry.instrumentation.asyncpg import AsyncPGInstrumentor
        AsyncPGInstrumentor().instrument()
    except Exception:  # noqa: BLE001 - asyncpg absent in sqlite-only runs
        logger.info("asyncpg instrumentation not installed; skipping")
    return trace.get_tracer("control_plane")


class OTelSink:
    """Map AgentEvents to gen_ai spans. Turn span is opened on turn.start and closed
    on turn.end; tool/llm calls become short child spans. The Collector forwards these
    to Sentry, where exceptions recorded here surface as production errors."""

    def __init__(self, tracer: trace.Tracer) -> None:
        self._tracer = tracer
        self._turn_spans: dict[str, Span] = {}

    def _turn_context(self, turn_id: str) -> Context | None:
        """Context whose active span is the open turn span, so child tool/llm spans
        nest under it. None (the ambient context) when the turn span is unknown."""
        span = self._turn_spans.get(turn_id)
        return set_span_in_context(span) if span is not None else None

    async def emit(self, event: AgentEvent) -> None:
        a = event.attrs
        if event.type == EventType.TURN_START:
            parent = _trace_context_from_ids(event.trace_id, event.turn_id)
            span = self._tracer.start_span(f"invoke_agent {a.get('agent_id', '')}", context=parent)
            span.set_attribute("gen_ai.operation.name", "invoke_agent")
            if a.get("model"):
                span.set_attribute("gen_ai.request.model", a["model"])
            self._turn_spans[event.turn_id] = span
        elif event.type == EventType.TURN_END:
            span = self._turn_spans.pop(event.turn_id, None)
            if span is not None:
                if a.get("status") and a["status"] != "ok":
                    span.set_status(Status(StatusCode.ERROR, a["status"]))
                span.end()
        elif event.type == EventType.TOOL_CALL:
            with self._tracer.start_as_current_span(
                f"execute_tool {a.get('name','')}", context=self._turn_context(event.turn_id)
            ) as span:
                span.set_attribute("gen_ai.operation.name", "execute_tool")
                span.set_attribute("gen_ai.tool.name", a.get("name", ""))
                span.set_attribute("tool.ok", bool(a.get("ok")))
                if not a.get("ok"):
                    span.set_status(Status(StatusCode.ERROR))
        elif event.type == EventType.LLM_CALL:
            with self._tracer.start_as_current_span(
                f"chat {a.get('model','')}", context=self._turn_context(event.turn_id)
            ) as span:
                span.set_attribute("gen_ai.operation.name", "chat")
                if a.get("model"):
                    span.set_attribute("gen_ai.request.model", a["model"])
                for key in ("prompt_tokens", "completion_tokens"):
                    if a.get(key) is not None:
                        span.set_attribute(f"gen_ai.usage.{key}", a[key])
        elif event.type == EventType.ERROR:
            span = self._turn_spans.get(event.turn_id)
            if span is not None:
                span.set_status(Status(StatusCode.ERROR, a.get("message", "error")))
                span.add_event("error", {"error.type": a.get("error_type", "")})
