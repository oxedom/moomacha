import uuid
from datetime import UTC, datetime

from control_plane.services.job_source import (
    InteractiveSubmissionSource,
    ScheduleSource,
    ZulipMentionSource,
    enqueue_agent_turn,
)


async def test_mention_source_builds_zulip_job_and_event():
    events, jobs = [], []

    async def write_event(**kw):
        events.append(kw)

    async def enqueue_job(job):
        jobs.append(job)

    aid = uuid.uuid4()
    await enqueue_agent_turn(
        agent_id=aid, channel="sandbox", topic="t", content="@**r** hi",
        source=ZulipMentionSource(message_id=112),
        write_event=write_event, enqueue_job=enqueue_job,
    )
    job = jobs[0]
    assert job.agent_id == aid
    assert job.channel == "sandbox"
    assert job.topic == "t"
    assert job.content == "@**r** hi"
    assert job.source_kind == "zulip_mention"
    assert job.source_message_id == 112
    assert events[0]["event_type"] == "webhook_received"
    assert events[0]["source_message_id"] == 112


async def test_schedule_source_builds_schedule_job_without_fake_message_id():
    events, jobs = [], []

    async def write_event(**kw):
        events.append(kw)

    async def enqueue_job(job):
        jobs.append(job)

    aid, sid = uuid.uuid4(), uuid.uuid4()
    when = datetime(2026, 5, 25, 9, 0, tzinfo=UTC)
    await enqueue_agent_turn(
        agent_id=aid, channel="sandbox", topic="t", content="post standup",
        source=ScheduleSource(schedule_id=sid, scheduled_for=when),
        write_event=write_event, enqueue_job=enqueue_job,
    )
    job = jobs[0]
    assert job.source_kind == "schedule"
    assert job.source_message_id is None
    assert job.schedule_id == sid
    assert job.fire_key == f"{sid}:{when.isoformat()}"
    assert events[0]["event_type"] == "schedule_fired"
    assert events[0]["payload"]["fire_key"] == job.fire_key
    assert events[0]["source_message_id"] is None


async def test_schedule_source_direct_channel_routes_as_dm():
    """A scheduled job whose channel encodes `direct:<user_id>` fires as a Zulip DM
    to those recipients, not a stream post (the scheduler has no other place to carry
    a DM recipient)."""
    jobs = []

    async def write_event(**kw):
        pass

    async def enqueue_job(job):
        jobs.append(job)

    aid, sid = uuid.uuid4(), uuid.uuid4()
    when = datetime(2026, 5, 25, 9, 0, tzinfo=UTC)
    await enqueue_agent_turn(
        agent_id=aid, channel="direct:9990001", topic="Claw · morning briefing",
        content="brief me", source=ScheduleSource(schedule_id=sid, scheduled_for=when),
        write_event=write_event, enqueue_job=enqueue_job,
    )
    job = jobs[0]
    assert job.conversation_type == "direct"
    assert job.direct_recipient_ids == [9990001]
    assert job.channel == "direct"  # normalized so downstream labels/logs stay clean


async def test_schedule_source_multiple_dm_recipients():
    jobs = []

    async def write_event(**kw):
        pass

    async def enqueue_job(job):
        jobs.append(job)

    await enqueue_agent_turn(
        agent_id=uuid.uuid4(), channel="direct:9990001,1085008", topic="",
        content="hi", source=ScheduleSource(schedule_id=uuid.uuid4(),
                                            scheduled_for=datetime(2026, 5, 25, 9, 0, tzinfo=UTC)),
        write_event=write_event, enqueue_job=enqueue_job,
    )
    assert jobs[0].direct_recipient_ids == [9990001, 1085008]


async def test_schedule_source_plain_stream_channel_unaffected():
    """Regression: a normal stream channel must keep conversation_type='stream'."""
    jobs = []

    async def write_event(**kw):
        pass

    async def enqueue_job(job):
        jobs.append(job)

    await enqueue_agent_turn(
        agent_id=uuid.uuid4(), channel="testing", topic="t", content="hi",
        source=ScheduleSource(schedule_id=uuid.uuid4(),
                              scheduled_for=datetime(2026, 5, 25, 9, 0, tzinfo=UTC)),
        write_event=write_event, enqueue_job=enqueue_job,
    )
    assert jobs[0].conversation_type == "stream"
    assert jobs[0].direct_recipient_ids is None
    assert jobs[0].channel == "testing"


def test_schedule_source_rejects_naive_datetime():
    import uuid

    import pytest

    from control_plane.services.job_source import ScheduleSource

    with pytest.raises(ValueError):
        ScheduleSource(schedule_id=uuid.uuid4(), scheduled_for=datetime(2026, 5, 25, 9, 0))  # naive


async def test_enqueue_mints_shared_trace_id():
    events = []
    jobs = []

    async def fake_write_event(**kw):
        events.append(kw)

    async def fake_enqueue(job):
        jobs.append(job)

    await enqueue_agent_turn(
        agent_id=uuid.uuid4(), channel="c", topic="t", content="hi",
        source=ZulipMentionSource(message_id=1, sender_email="u@x"),
        write_event=fake_write_event, enqueue_job=fake_enqueue,
    )
    assert events[0]["trace_id"]
    assert jobs[0].trace_id == events[0]["trace_id"]


async def test_interactive_submission_source_enqueues_job_and_event():
    enqueued = []
    events = []

    async def fake_enqueue(job):
        enqueued.append(job)

    async def fake_write_event(**kwargs):
        events.append(kwargs)

    aid = uuid.uuid4()
    await enqueue_agent_turn(
        agent_id=aid,
        channel="sandbox",
        topic="Deploy approval",
        content="Interactive response submitted for ...",
        source=InteractiveSubmissionSource(
            artifact_id=uuid.uuid4(), submission_id="sub-1", zulip_message_id=999
        ),
        write_event=fake_write_event,
        enqueue_job=fake_enqueue,
    )
    assert len(enqueued) == 1
    job = enqueued[0]
    assert job.source_kind == "interactive_submission"
    assert job.source_message_id == 999  # so the agent reply threads under the summary
    assert any(e["event_type"] == "interactive_submission_received" for e in events)
