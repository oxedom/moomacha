import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from control_plane.observability.events import EventEmitter
from control_plane.observability.sink import MultiSink
from control_plane.observability.audit_sink import AuditSink
from control_plane.runtime.loop import BudgetExceeded
from control_plane.runtime.tools.registry import ToolRegistry
from control_plane.runtime.tools.runtime import ToolContext, ToolRuntime
from control_plane.schemas.agents import ResolvedAgent
from control_plane.services.context_assembly import build_context_prompt
from control_plane.tools.management.context import ManagementToolContext
from control_plane.services.archetype_catalog import ArchetypeCatalog
from control_plane.services.pool_resolver import agent_from_snapshot
from control_plane.services.pool_store import PoolStore
from control_plane.services.session_store import SessionStore
from control_plane.services.tripwire import (
    DARKCLAW_BASELINE_TOOLS,
    classify,
    fire_tripwire,
    tripwire_enabled,
)

logger = logging.getLogger("control_plane")


class AgentClientProtocol(Protocol):
    """The subset of ZulipClient the worker needs, so the seam is explicit."""

    async def send_message(self, channel: str, topic: str, content: str) -> int: ...
    async def send_direct_message(self, recipient_ids: list[int], content: str) -> int: ...
    async def upload_file(self, *, filename: str, content: bytes, content_type: str) -> dict: ...
    async def get_messages(self, channel: str, topic: str, num_before: int) -> list[dict]: ...
    async def get_direct_messages(self, recipient_ids: list[int], num_before: int) -> list[dict]: ...
    async def get_channel_messages(self, channel: str, num_before: int) -> list[dict]: ...
    async def update_message(self, message_id: int, content: str) -> None: ...


@dataclass
class Job:
    agent_id: uuid.UUID
    channel: str
    topic: str
    content: str
    conversation_type: Literal["stream", "direct"] = "stream"
    direct_recipient_ids: list[int] | None = None
    source_kind: Literal["zulip_mention", "schedule", "interactive_submission"] = "zulip_mention"
    source_message_id: int | None = None  # real Zulip id for mentions; None for schedules
    schedule_id: uuid.UUID | None = None
    fire_key: str | None = None
    invoking_user: str | None = None  # human sender email for mentions; None for scheduled fires
    session_id: uuid.UUID | None = None
    trace_id: str | None = None  # minted at ingress when available; else by process_job
    turn_id: str | None = None


@dataclass
class JobDeps:
    session_factory: async_sessionmaker[AsyncSession]
    resolve_agent: Callable[[uuid.UUID], Awaitable[ResolvedAgent | None]]
    make_agent_client: Callable[[str, str], AgentClientProtocol]
    tool_registry: ToolRegistry
    tool_runtime: ToolRuntime
    client_factory: Callable[[str, str | None], Any]
    llm_api_key: str
    llm_base_url: str | None
    max_tool_calls: int
    context_default_n: int
    agent_registry: Any = None  # AgentRegistry; required for bastion turns
    admin_client: Any = None  # ZulipAdminClient; required for bastion turns
    payload_url: str = ""
    default_model: str = "gpt-4o"
    runner_router: Any = None  # AgentRunnerRouter; default openai-only router built if None
    secret_box: Any = None  # SecretBox; required for cattle-agent pool construction
    pool_store: Any = None  # PoolStore; required for session-aware turns
    session_store: Any = None  # SessionStore; required for session-aware turns
    turn_timeout_seconds: float = 300.0  # wall-clock bound per turn; see process_job
    live_bus: Any = None  # LiveBus | None; observability live feed
    otel_tracer: Any = None  # opentelemetry Tracer | None


class JobQueue:
    def __init__(self) -> None:
        self._q: asyncio.Queue[Job] = asyncio.Queue()

    async def enqueue(self, job: Job) -> None:
        await self._q.put(job)

    async def get(self) -> Job:
        return await self._q.get()

    def task_done(self) -> None:
        self._q.task_done()


async def _resolve_session_agent(job: Job, deps: JobDeps):
    """Build a ResolvedAgent from the live session's snapshot + pool bot creds."""
    session = await deps.session_store.resolve_for_topic(job.channel, job.topic)
    if session is None or session.pool_bot_id is None:
        return None
    creds = await deps.pool_store.resolve_creds(session.pool_bot_id)
    if creds is None:
        return None
    bot = await deps.pool_store.get(session.pool_bot_id)
    if bot is None:
        return None
    return agent_from_snapshot(session.archetype_snapshot, creds, bot.id, bot.zulip_bot_id, job.channel)


async def process_job(job: Job, deps: JobDeps) -> None:
    if job.session_id is not None and deps.pool_store is not None and deps.session_store is not None:
        agent = await _resolve_session_agent(job, deps)
        if agent is None:
            logger.warning("Session job dropped: no active session for channel=%s topic=%s", job.channel, job.topic)
            return
    else:
        agent = await deps.resolve_agent(job.agent_id)
        if agent is None:
            logger.warning("Job for unknown agent_id=%s dropped", job.agent_id)
            return

    client = deps.make_agent_client(agent.zulip_bot_email, agent.zulip_api_key)
    if job.conversation_type == "direct":
        recipient_ids = job.direct_recipient_ids or []
        progress_id = await client.send_direct_message(recipient_ids, "🤔 Working on it…")
    else:
        progress_id = await client.send_message(job.channel, job.topic, "🤔 Working on it…")
    llm_client = deps.client_factory(deps.llm_api_key, deps.llm_base_url)

    trace_id = job.trace_id or uuid.uuid4().hex
    turn_id = job.turn_id or uuid.uuid4().hex
    sinks = [AuditSink(deps.session_factory).emit]
    if deps.live_bus is not None:
        from control_plane.observability.live_bus import LiveSink
        sinks.append(LiveSink(deps.live_bus).emit)
    if deps.otel_tracer is not None:
        from control_plane.observability.otel_sink import OTelSink
        sinks.append(OTelSink(deps.otel_tracer).emit)
    emitter = EventEmitter(trace_id=trace_id, turn_id=turn_id, emit_fn=MultiSink(sinks).emit)
    turn_started = time.monotonic()

    # Everything after the placeholder is in the try so any failure still edits
    # the placeholder into an error message instead of leaving a spinner.
    try:
        await emitter.turn_start(
            agent_id=str(agent.id),
            runtime_kind=getattr(agent, "runtime_kind", "openai_tool_loop"),
            model=agent.model_id, channel=job.channel, topic=job.topic,
            source_message_id=job.source_message_id, invoking_user=job.invoking_user,
        )
        # Escalation tripwire (DarkClaw): if a flagged agent holds any tool beyond
        # its sanctioned baseline, shut it down before the runner/bridge ever runs.
        if tripwire_enabled(agent):
            verdict = classify(agent.allowed_tools, DARKCLAW_BASELINE_TOOLS)
            if verdict.tripped:
                await fire_tripwire(
                    agent=agent, job=job, client=client,
                    registry=deps.agent_registry, emitter=emitter,
                    progress_id=progress_id, verdict=verdict,
                )
                await emitter.turn_end(
                    status="tripwire", agent_id=str(agent.id),
                    channel=job.channel, source_message_id=job.source_message_id,
                    duration_ms=int((time.monotonic() - turn_started) * 1000),
                )
                return
        if job.conversation_type == "direct":
            history = await client.get_direct_messages(
                job.direct_recipient_ids or [],
                num_before=agent.context_message_count or deps.context_default_n,
            )
        else:
            history = await client.get_messages(
                job.channel,
                job.topic,
                num_before=agent.context_message_count or deps.context_default_n,
            )
        tool_descriptions = deps.tool_registry.describe_tools(
            agent.allowed_tools,
            is_bastion=getattr(agent, "is_bastion", False),
            can_exec=getattr(agent, "can_exec", False),
        )
        prompt = build_context_prompt(
            agent.persona, history, job.channel, job.topic, tools=tool_descriptions
        )
        management = None
        if getattr(agent, "is_bastion", False):
            management = ManagementToolContext(
                registry=deps.agent_registry,
                admin_client=deps.admin_client,
                payload_url=deps.payload_url,
                default_model=deps.default_model,
                invoking_message_text=job.content,
                session_factory=deps.session_factory,
                archetypes=ArchetypeCatalog(deps.session_factory),
                pool=PoolStore(deps.session_factory, deps.secret_box) if deps.secret_box else None,
                sessions=SessionStore(deps.session_factory),
            )
        # Agent-tier memory namespace: a cattle session carries it on its row;
        # a persistent agent falls back to its own id. (Distinct from the per-topic
        # working-memory key in agent_memory.py — that's topic-scoped, this is agent-scoped.)
        _store = deps.session_store or SessionStore(deps.session_factory)
        _session = await _store.resolve_for_topic(job.channel, job.topic)
        memory_ns = _session.memory_ns if _session is not None else f"agent:{agent.id}"
        ctx = ToolContext(
            agent=agent,
            zulip=client,
            channel=job.channel,
            topic=job.topic,
            management=management,
            invoking_user=job.invoking_user,
            invoking_text=job.content,
            source_message_id=job.source_message_id,
            memory_ns=memory_ns,
            conversation_type=job.conversation_type,
            direct_recipient_ids=job.direct_recipient_ids,
            events=emitter,
        )

        from control_plane.runtime.runners.base import RunnerInput, UnknownRuntimeKind
        from control_plane.runtime.runners.router import AgentRunnerRouter

        router = deps.runner_router or AgentRunnerRouter.default(
            deps.tool_registry, deps.tool_runtime, max_tool_calls=deps.max_tool_calls
        )
        try:
            runner = router.select(agent)
        except UnknownRuntimeKind as exc:
            logger.warning("Agent=%s has unknown runtime_kind=%r", agent.name, exc.kind)
            await client.update_message(
                progress_id, "⚠️ I'm not configured with a valid runtime; an admin needs to fix this."
            )
            await emitter.error(error_type="unknown_runtime_kind", message=f"runtime_kind={exc.kind}", agent_id=str(agent.id))
            await emitter.turn_end(status="failed", agent_id=str(agent.id), channel=job.channel, source_message_id=job.source_message_id, duration_ms=int((time.monotonic() - turn_started) * 1000))
            return
        try:
            async with asyncio.timeout(deps.turn_timeout_seconds):
                text = await runner.run(
                    RunnerInput(
                        job=job,
                        agent=agent,
                        system_prompt=prompt,
                        user_message=job.content,
                        tool_context=ctx,
                        on_tool_call=None,
                        llm_client=llm_client,
                        events=emitter,
                    )
                )
        except TimeoutError:
            # Caught BEFORE the outer `except Exception` so it surfaces as a clear
            # timeout rather than a generic error. asyncio.timeout cancels the
            # runner; the `finally` below still closes llm_client.
            logger.warning(
                "Turn timed out after %ss for agent=%s", deps.turn_timeout_seconds, agent.name
            )
            await client.update_message(
                progress_id, f"⏱️ This turn timed out after {deps.turn_timeout_seconds}s."
            )
            await emitter.error(error_type="turn_timeout", message=f"timed out after {deps.turn_timeout_seconds}s", agent_id=str(agent.id))
            await emitter.turn_end(status="timeout", agent_id=str(agent.id), channel=job.channel, source_message_id=job.source_message_id, duration_ms=int((time.monotonic() - turn_started) * 1000))
            return
        await client.update_message(progress_id, text)
        if job.session_id is not None and deps.session_store is not None:
            await deps.session_store.touch(job.session_id)
        await emitter.turn_end(
            status="ok", reply=text[:500], agent_id=str(agent.id),
            channel=job.channel, source_message_id=job.source_message_id,
            duration_ms=int((time.monotonic() - turn_started) * 1000),
        )
    except BudgetExceeded as exc:
        logger.warning("Tool-call budget exceeded for agent=%s", agent.name)
        await client.update_message(progress_id, "⚠️ I hit the tool-call limit for this turn.")
        await emitter.error(error_type="tool_budget_exceeded", message=f"calls={exc.count}", agent_id=str(agent.id))
        await emitter.turn_end(status="failed", agent_id=str(agent.id), channel=job.channel, source_message_id=job.source_message_id, duration_ms=int((time.monotonic() - turn_started) * 1000))
    except Exception as exc:  # noqa: BLE001 - worker must survive any job failure
        logger.exception("Job failed for agent=%s", agent.name)
        await client.update_message(progress_id, f"⚠️ I hit an error: {exc}")
        await emitter.error(error_type=type(exc).__name__, message=str(exc), agent_id=str(agent.id))
        await emitter.turn_end(status="failed", agent_id=str(agent.id), channel=job.channel, source_message_id=job.source_message_id, duration_ms=int((time.monotonic() - turn_started) * 1000))
    finally:
        close = getattr(llm_client, "close", None)
        if close is not None:
            await close()


async def worker_loop(queue: JobQueue, deps: JobDeps) -> None:
    while True:
        job = await queue.get()
        try:
            await process_job(job, deps)
        finally:
            queue.task_done()
