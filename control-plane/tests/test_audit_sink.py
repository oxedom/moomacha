import uuid
from datetime import datetime, timezone

from sqlalchemy import select

from control_plane.db.engine import build_session_factory, create_all
from control_plane.db.tables import EventRow
from control_plane.observability.audit_sink import AuditSink
from control_plane.observability.events import AgentEvent, EventType


def _ev(type_, seq, attrs):
    return AgentEvent(type=type_, trace_id="tr", turn_id="tn", seq=seq,
                      ts=datetime.now(timezone.utc), attrs=attrs)


async def test_audit_sink_writes_event_rows():
    factory, engine = build_session_factory("sqlite+aiosqlite://")
    await create_all(engine)
    aid = uuid.uuid4()
    sink = AuditSink(factory)

    await sink.emit(_ev(EventType.TURN_START, 0, {
        "agent_id": str(aid), "channel": "c", "source_message_id": 9}))
    await sink.emit(_ev(EventType.TOOL_CALL, 1, {
        "agent_id": str(aid), "name": "search", "ok": True, "latency_ms": 5,
        "args": '{"q":1}', "result": "found", "channel": "c", "source_message_id": 9}))

    async with factory() as s:
        rows = (await s.execute(select(EventRow).order_by(EventRow.seq))).scalars().all()
    assert [r.event_type for r in rows] == [EventType.TURN_START, EventType.TOOL_CALL]
    assert rows[0].trace_id == "tr" and rows[0].turn_id == "tn" and rows[0].seq == 0
    assert rows[1].payload["name"] == "search"
    assert rows[1].related_agent_id == aid
    assert rows[1].related_channel == "c"
    assert rows[1].source_message_id == 9
