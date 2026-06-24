import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime

from control_plane.services.job_queue import Job


@dataclass
class ZulipMentionSource:
    message_id: int
    sender_email: str | None = None


@dataclass
class ScheduleSource:
    schedule_id: uuid.UUID
    scheduled_for: datetime

    def __post_init__(self) -> None:
        # fire_key is derived from scheduled_for.isoformat(); a naive datetime would
        # drift the key format between environments, so require an aware (UTC) value.
        if self.scheduled_for.tzinfo is None:
            raise ValueError("ScheduleSource.scheduled_for must be timezone-aware")


@dataclass
class InteractiveSubmissionSource:
    artifact_id: uuid.UUID
    submission_id: str
    zulip_message_id: int  # the visible summary message; the resumed turn threads under it


JobSource = ZulipMentionSource | ScheduleSource | InteractiveSubmissionSource


async def enqueue_agent_turn(
    *,
    agent_id: uuid.UUID,
    channel: str,
    topic: str,
    content: str,
    conversation_type: str = "stream",
    direct_recipient_ids: list[int] | None = None,
    source: JobSource,
    write_event: Callable[..., Awaitable[None]],
    enqueue_job: Callable[[Job], Awaitable[None]],
    session_id: uuid.UUID | None = None,
) -> None:
    """The single internal Job-creation + source-event path.

    Both the Zulip webhook and the scheduler call this so a scheduled fire enters
    the queue exactly like a mention — without re-POSTing the webhook or faking a
    Zulip message id (scheduled jobs carry source_message_id=None + a fire_key).
    """
    trace_id = uuid.uuid4().hex
    if isinstance(source, ZulipMentionSource):
        # System-actor event (a human triggered it); the schedule branch below is
        # an agent-actor event since the agent's own schedule fired it.
        await write_event(
            actor_type="system",
            event_type="webhook_received",
            payload={"message_id": source.message_id},
            related_agent_id=agent_id,
            related_channel=channel,
            source_message_id=source.message_id,
            trace_id=trace_id,
        )
        job = Job(
            agent_id=agent_id, channel=channel, topic=topic, content=content,
            conversation_type=conversation_type,
            direct_recipient_ids=direct_recipient_ids,
            source_kind="zulip_mention", source_message_id=source.message_id,
            invoking_user=source.sender_email,
            session_id=session_id,
            trace_id=trace_id,
        )
    elif isinstance(source, ScheduleSource):
        # A scheduled job has no Zulip message to infer a DM recipient from, so the
        # recipient is encoded in the channel as `direct:<id>[,<id>...]`. Anything
        # else is a normal stream post.
        if channel.startswith("direct:"):
            conversation_type = "direct"
            direct_recipient_ids = [
                int(x) for x in channel.split(":", 1)[1].split(",") if x.strip()
            ]
            channel = "direct"  # normalize so downstream labels/logs/memory ns stay clean
        fire_key = f"{source.schedule_id}:{source.scheduled_for.isoformat()}"
        await write_event(
            actor_type="agent",
            event_type="schedule_fired",
            payload={"schedule_id": str(source.schedule_id), "fire_key": fire_key},
            actor_id=agent_id,
            related_agent_id=agent_id,
            related_channel=channel,
            source_message_id=None,
            trace_id=trace_id,
        )
        job = Job(
            agent_id=agent_id, channel=channel, topic=topic, content=content,
            conversation_type=conversation_type,
            direct_recipient_ids=direct_recipient_ids,
            source_kind="schedule", schedule_id=source.schedule_id, fire_key=fire_key,
            trace_id=trace_id,
        )
    elif isinstance(source, InteractiveSubmissionSource):
        await write_event(
            actor_type="agent",
            event_type="interactive_submission_received",
            payload={
                "artifact_id": str(source.artifact_id),
                "submission_id": source.submission_id,
            },
            actor_id=agent_id,
            related_agent_id=agent_id,
            related_channel=channel,
            source_message_id=source.zulip_message_id,
            trace_id=trace_id,
        )
        job = Job(
            agent_id=agent_id, channel=channel, topic=topic, content=content,
            source_kind="interactive_submission",
            source_message_id=source.zulip_message_id,
            trace_id=trace_id,
        )
    else:
        raise TypeError(f"unknown job source: {source!r}")
    await enqueue_job(job)
