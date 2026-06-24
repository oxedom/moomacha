from datetime import datetime, timezone

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from control_plane.observability.events import AgentEvent, EventType
from control_plane.observability.otel_sink import OTelSink


def _ev(type_, seq, attrs):
    return AgentEvent(type=type_, trace_id="tr", turn_id="tn", seq=seq,
                      ts=datetime.now(timezone.utc), attrs=attrs)


async def test_otel_sink_emits_tool_and_turn_spans():
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("test")

    sink = OTelSink(tracer)
    await sink.emit(_ev(EventType.TURN_START, 0, {"agent_id": "a", "model": "gpt-4o"}))
    await sink.emit(_ev(EventType.TOOL_CALL, 1, {"name": "search", "ok": True}))
    await sink.emit(_ev(EventType.TURN_END, 2, {"status": "ok", "duration_ms": 12}))

    spans = exporter.get_finished_spans()
    names = {s.name for s in spans}
    assert any("execute_tool" in n for n in names)
    assert any("invoke_agent" in n for n in names)
    tool_span = next(s for s in spans if "execute_tool" in s.name)
    assert tool_span.attributes["gen_ai.tool.name"] == "search"


async def test_tool_and_llm_spans_are_children_of_the_turn_span():
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("test")

    sink = OTelSink(tracer)
    await sink.emit(_ev(EventType.TURN_START, 0, {"agent_id": "a", "model": "gpt-4o"}))
    await sink.emit(_ev(EventType.TOOL_CALL, 1, {"name": "search", "ok": True}))
    await sink.emit(_ev(EventType.LLM_CALL, 2, {"model": "gpt-4o", "prompt_tokens": 5}))
    await sink.emit(_ev(EventType.TURN_END, 3, {"status": "ok"}))

    spans = {s.name.split()[0]: s for s in exporter.get_finished_spans()}
    turn = spans["invoke_agent"]
    tool = spans["execute_tool"]
    chat = spans["chat"]
    # children must nest under the turn span (same trace, parent = turn span)
    assert tool.parent is not None and tool.parent.span_id == turn.context.span_id
    assert chat.parent is not None and chat.parent.span_id == turn.context.span_id
    assert tool.context.trace_id == turn.context.trace_id
    assert chat.context.trace_id == turn.context.trace_id


async def test_turn_trace_id_derives_from_audit_trace_id():
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("test")

    # a real uuid hex, as produced by the job queue (uuid4().hex)
    trace_hex = "0123456789abcdef0123456789abcdef"
    turn_hex = "fedcba9876543210fedcba9876543210"
    ev = AgentEvent(type=EventType.TURN_START, trace_id=trace_hex, turn_id=turn_hex,
                    seq=0, ts=datetime.now(timezone.utc), attrs={"agent_id": "a"})
    sink = OTelSink(tracer)
    await sink.emit(ev)
    await sink.emit(AgentEvent(type=EventType.TURN_END, trace_id=trace_hex, turn_id=turn_hex,
                               seq=1, ts=datetime.now(timezone.utc), attrs={"status": "ok"}))

    turn = exporter.get_finished_spans()[0]
    # OTel trace_id is pinned to the audit trace_id so logs/audit/traces correlate
    assert format(turn.context.trace_id, "032x") == trace_hex
