import asyncio
import contextlib
import logging
import os
import uuid
from pathlib import Path
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from functools import partial
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as metadata_version

import uvicorn

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from control_plane.config import Settings
from control_plane.db.engine import build_session_factory, create_all
from control_plane.events.writer import write_event
from control_plane.routes.agent_types import build_agent_types_router
from control_plane.routes.agents import build_agents_router
from control_plane.routes.browser_goals import build_browser_goals_router
from control_plane.routes.dashboard import build_dashboard_router
from control_plane.routes.meta import build_meta_router
from control_plane.routes.zulip_webhook import build_webhook_router
from control_plane.services.agent_registry import AgentRegistry
from control_plane.services.bastion_seeder import seed_bastion
from control_plane.services.orphan_recovery import recover_pool_consistency
from control_plane.services.browser_goal_runner import BrowserGoalRunner
from control_plane.services.crypto import SecretBox
from control_plane.runtime.llm_client import default_client_factory
from control_plane.runtime.tools.agent_memory import AgentMemoryRest, register_agent_memory_tools
from control_plane.runtime.tools.knowledge_artifacts import register_knowledge_artifact_tools
from control_plane.runtime.tools.exec_mcp import ExecMcp, register_exec_tools
from control_plane.runtime.tools.tavily import TavilyMcp, register_tavily_tools
from control_plane.runtime.tools.context7 import Context7Mcp, register_context7_tools
from control_plane.runtime.tools.google_api import GoogleClient
from control_plane.runtime.tools.gcal import register_gcal_tools
from control_plane.runtime.tools.gtasks import register_gtasks_tools
from control_plane.runtime.tools.images import register_image_tools
from control_plane.runtime.tools.messages import register_message_tools
from control_plane.runtime.tools.playwright_cli import PlaywrightCli, register_playwright_cli_tools
from control_plane.runtime.tools.registry import ToolRegistry
from control_plane.runtime.tools.scheduling import register_scheduling_tools
from control_plane.runtime.tools.runtime import ToolRuntime
from control_plane.services.knowledge_artifact_store import KnowledgeArtifactStore
from control_plane.tools.management.adapters import register_management_tools
from control_plane.tools.management.session_adapters import register_session_tools
from control_plane.services.pool_store import PoolStore
from control_plane.services.session_store import SessionStore
from control_plane.routes.pool import build_pool_router
from control_plane.services.pool_resolver import resolve_pool_bot_for_webhook
from control_plane.services.session_reaper import SessionReaperDeps, SessionReaperLoop
from control_plane.runtime.runners.router import AgentRunnerRouter
from control_plane.runtime.runners.openai_loop import OpenAIToolLoopRunner
from control_plane.runtime.runners.deepagents_runner import DeepAgentRunner
from control_plane.runtime.runners.codex_runner import CodexRunner
from control_plane.runtime.runners.relay_runner import RelayRunner
from control_plane.runtime.runners.codex_workspace import WorkspaceManager
from control_plane.runtime.runners.codex_health import codex_available
from control_plane.runtime.runners.codex_tool_bridge import CodexToolBridge
from control_plane.services.skill_catalog import SkillCatalog
from control_plane.routes.artifacts import build_artifacts_router
from control_plane.runtime.tools.interactive_response import register_interactive_response_tools
from control_plane.services.artifact_store import ArtifactStore
from control_plane.services.generated_media_store import GeneratedMediaStore
from control_plane.observability.live_bus import LiveBus
from control_plane.observability.otel_sink import setup_tracing
from control_plane.routes.observability import build_observability_router
from control_plane.services.job_queue import Job, JobDeps, JobQueue, worker_loop
from control_plane.services.job_source import enqueue_agent_turn
from control_plane.services.schedule_store import ScheduleStore
from control_plane.services.scheduler import SchedulerDeps, SchedulerLoop
from control_plane.services.zulip_admin import ZulipAdminClient
from control_plane.zulip_client import ZulipClient

logger = logging.getLogger("control_plane")


def _write_codex_mcp_config(host: str, port: int) -> None:
    """Write the MCP bridge endpoint into ~/.codex/config.toml at startup.

    codex 0.135 silently ignores -c mcp_servers.* command-line overrides, so
    we deliver the config via the file that codex always reads. Written once at
    startup; the per-turn CP_BRIDGE_TOKEN is still injected into the child env
    by _minimal_env (see codex_backend.py)."""
    config_dir = Path.home() / ".codex"
    config_dir.mkdir(parents=True, exist_ok=True)
    url = f"http://{host}:{port}/mcp/"
    # Preserve any existing TOML (trust_level blocks written by prior codex runs)
    # by patching only the [mcp_servers.cp] section, not rewriting from scratch.
    config_path = config_dir / "config.toml"
    existing = config_path.read_text() if config_path.exists() else ""
    if "[mcp_servers.cp]" not in existing:
        with config_path.open("a") as f:
            f.write(
                f'\n[mcp_servers.cp]\n'
                f'url = "{url}"\n'
                f'bearer_token_env_var = "CP_BRIDGE_TOKEN"\n'
            )
        logger.info("codex MCP bridge config written to %s (url=%s)", config_path, url)
    else:
        logger.debug("codex MCP bridge config already present in %s", config_path)


def _resolve_by_id(registry: AgentRegistry):
    """Adapt the registry to JobDeps.resolve_agent (by id).

    process_job resolves by agent_id; the registry resolves by email, so look up
    the (secret-free) row by id, then re-fetch with decrypted creds by email.
    """

    async def _resolve(agent_id: uuid.UUID):
        read = await registry.get(agent_id)
        if read is None:
            return None
        return await registry.resolve_by_bot_email(read.zulip_bot_email)

    return _resolve


def build_scheduler_loop(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    enqueue_job: Callable[[Job], Awaitable[None]],
) -> SchedulerLoop:
    """Build the SchedulerLoop wired to fire due schedules through enqueue_agent_turn.

    Takes enqueue_job (the JobQueue.enqueue handle from create_app) so the loop
    creates jobs through the same internal path a Zulip mention uses.
    """
    write_event_bound = partial(write_event, session_factory)
    store = ScheduleStore(session_factory, write_event_bound)

    async def fire_turn(*, agent_id, channel, topic, content, source):
        await enqueue_agent_turn(
            agent_id=agent_id,
            channel=channel,
            topic=topic,
            content=content,
            source=source,
            write_event=write_event_bound,
            enqueue_job=enqueue_job,
        )

    return SchedulerLoop(
        SchedulerDeps(
            store=store,
            enqueue_turn=fire_turn,
            clock=lambda: datetime.now(UTC),
            grace_seconds=settings.schedule_misfire_grace_seconds,
            max_due_per_tick=settings.schedule_max_due_per_tick,
        )
    )


def create_app(settings: Settings) -> FastAPI:
    # LangChain (the DeepAgents runner) reads OPENAI_API_KEY from the environment,
    # but this app configures OPENAI_KEY. Bridge them so deepagents turns can
    # authenticate (the legacy runner passes the key explicitly and is unaffected).
    os.environ.setdefault("OPENAI_API_KEY", settings.openai_key)
    otel_tracer = setup_tracing(settings) if settings.otel_enabled else None
    live_bus = LiveBus()
    session_factory, engine = build_session_factory(settings.neon_database_url)
    secret_box = SecretBox(settings.agent_fernet_key)
    registry = AgentRegistry(session_factory, secret_box)
    admin_client = ZulipAdminClient(
        site=settings.zulip_site,
        email=settings.zulip_admin_email or "",
        api_key=settings.zulip_admin_api_key or "",
    )

    def make_agent_client(email: str, api_key: str) -> ZulipClient:
        return ZulipClient(site=settings.zulip_site, email=email, api_key=api_key)

    tool_registry = ToolRegistry()
    register_message_tools(tool_registry)
    register_knowledge_artifact_tools(tool_registry, KnowledgeArtifactStore(session_factory))
    register_playwright_cli_tools(tool_registry, PlaywrightCli())
    register_management_tools(tool_registry)
    register_session_tools(tool_registry)
    # Scheduling tools are always registered; per-agent access is gated by
    # allowed_tools (no enable flag — like knowledge/playwright/management).
    register_scheduling_tools(
        tool_registry, ScheduleStore(session_factory, partial(write_event, session_factory))
    )
    # Interactive response artifact tool — always registered; per-agent access gated by allowed_tools.
    artifact_base_url = (settings.public_base_url or settings.zulip_site).rstrip("/")
    artifact_store = ArtifactStore(session_factory, partial(write_event, session_factory))
    generated_media_store = GeneratedMediaStore(session_factory, partial(write_event, session_factory))
    register_interactive_response_tools(
        tool_registry,
        artifact_store,
        base_url=artifact_base_url,
        default_expiry_minutes=settings.artifact_default_expiry_minutes,
        max_expiry_minutes=settings.artifact_max_expiry_minutes,
        max_html_bytes=settings.artifact_max_html_bytes,
    )
    if settings.openai_images_enabled and settings.openai_key:
        register_image_tools(
            tool_registry,
            generated_media_store,
            client_factory=lambda: default_client_factory(settings.openai_key, None),
            model=settings.openai_image_model,
            default_size=settings.openai_image_default_size,
            default_quality=settings.openai_image_default_quality,
            default_format=settings.openai_image_default_format,
            timeout_s=settings.openai_image_timeout_s,
            max_bytes=settings.openai_image_max_bytes,
        )
        logger.info("OpenAI image generation tool ENABLED (model=%s)", settings.openai_image_model)
    elif settings.openai_images_enabled:
        logger.warning("OpenAI image generation tool DISABLED: OPENAI_KEY is empty")
    else:
        logger.info("OpenAI image generation tool disabled")
    if settings.agent_memory_enabled and (
        (settings.agent_memory_store_id and settings.agent_memory_api_key)
        or not settings.agent_memory_store_id
    ):
        register_agent_memory_tools(
            tool_registry,
            AgentMemoryRest(
                endpoint=settings.agent_memory_endpoint,
                store_id=settings.agent_memory_store_id,
                api_key=settings.agent_memory_api_key,
                timeout=settings.agent_memory_timeout_s,
            ),
        )
        logger.warning("Agent memory REST tools ENABLED (store=%s)", settings.agent_memory_store_id)
    if settings.exec_mcp_enabled and settings.exec_mcp_url:
        register_exec_tools(
            tool_registry,
            ExecMcp(
                url=settings.exec_mcp_url,
                token=settings.exec_mcp_token,
                timeout_seconds=settings.exec_mcp_timeout_s,
            ),
            channels=settings.exec_channel_list,
            users=settings.exec_user_list,
            require_confirm=settings.exec_require_confirm,
        )
        logger.warning(
            "Exec MCP tool ENABLED (url=%s, channels=%s, users=%s, confirm=%s)",
            settings.exec_mcp_url,
            settings.exec_channel_list,
            settings.exec_user_list,
            settings.exec_require_confirm,
        )
    if settings.tavily_mcp_enabled and settings.tavily_api_key:
        register_tavily_tools(
            tool_registry,
            TavilyMcp(
                url=settings.tavily_mcp_url,
                api_key=settings.tavily_api_key,
                timeout_seconds=settings.tavily_mcp_timeout_s,
            ),
        )
        logger.info("Tavily MCP tools ENABLED (url=%s)", settings.tavily_mcp_url)
    if settings.google_enabled and (
        settings.google_client_id and settings.google_client_secret and settings.google_refresh_token
    ):
        google_client = GoogleClient(
            client_id=settings.google_client_id,
            client_secret=settings.google_client_secret,
            refresh_token=settings.google_refresh_token,
            timeout=settings.google_timeout_s,
        )
        register_gcal_tools(tool_registry, google_client)
        register_gtasks_tools(tool_registry, google_client)
        logger.info("Google Calendar + Tasks tools ENABLED")
    elif settings.google_enabled:
        logger.warning(
            "Google tools DISABLED: google_enabled=true but client_id/secret/refresh_token incomplete"
        )
    if settings.context7_enabled:
        import shutil
        _ctx7_cmd = settings.context7_command[0] if settings.context7_command else None
        if _ctx7_cmd and shutil.which(_ctx7_cmd):
            register_context7_tools(
                tool_registry,
                Context7Mcp(
                    command=settings.context7_command,
                    timeout_seconds=settings.context7_timeout_s,
                ),
            )
            logger.info("Context7 MCP tools ENABLED (command=%s)", settings.context7_command)
        else:
            logger.warning(
                "Context7 MCP DISABLED: %r not found on PATH (set context7_enabled=false to suppress)",
                _ctx7_cmd,
            )
    tool_runtime = ToolRuntime(tool_registry)
    browser_goal_runner = BrowserGoalRunner(
        client_factory=default_client_factory,
        llm_api_key=settings.openai_key,
        llm_base_url=None,
        default_model=settings.bastion_model_id or "gpt-4o",
        registry=tool_registry,
        runtime=tool_runtime,
    )

    queue = JobQueue()
    pool_store = PoolStore(session_factory, secret_box)
    session_store_instance = SessionStore(session_factory)
    skill_catalog = SkillCatalog(session_factory)
    scheduler_loop = build_scheduler_loop(settings, session_factory, queue.enqueue)
    session_reaper_loop = SessionReaperLoop(
        SessionReaperDeps(
            store=session_store_instance,
            clock=lambda: datetime.now(UTC),
            idle_seconds=settings.session_idle_seconds,
        )
    )
    codex_bridge = CodexToolBridge(tool_registry)
    runners = {
        "openai_tool_loop": OpenAIToolLoopRunner(tool_registry, tool_runtime, settings.max_tool_calls_per_turn),
        "deepagents": DeepAgentRunner(tool_registry, tool_runtime, skill_catalog=skill_catalog),
        "codex": CodexRunner(
            workspaces=WorkspaceManager(Path(settings.codex_workspace_root)),
            openai_key=settings.openai_key,
            default_sandbox_mode=settings.codex_default_sandbox_mode,
            tool_bridge=codex_bridge,
            tool_runtime=tool_runtime,
            bridge_url=(
                f"http://{settings.codex_bridge_host}:{settings.codex_bridge_port}/mcp/"
                if settings.codex_bridge_enabled
                else None
            ),
            skill_catalog=skill_catalog,
        ),
    }
    if settings.relay_runner_enabled:
        runners["relay"] = RelayRunner()
    runner_router = AgentRunnerRouter(runners)
    deps = JobDeps(
        session_factory=session_factory,
        resolve_agent=_resolve_by_id(registry),
        make_agent_client=make_agent_client,
        tool_registry=tool_registry,
        tool_runtime=tool_runtime,
        client_factory=default_client_factory,
        llm_api_key=settings.openai_key,
        llm_base_url=None,
        max_tool_calls=settings.max_tool_calls_per_turn,
        context_default_n=settings.context_default_n,
        agent_registry=registry,
        admin_client=admin_client,
        payload_url=(settings.public_base_url or settings.zulip_site).rstrip("/") + "/zulip/incoming",
        default_model=settings.bastion_model_id or "gpt-4o",
        runner_router=runner_router,
        secret_box=secret_box,
        pool_store=pool_store,
        session_store=session_store_instance,
        turn_timeout_seconds=settings.turn_timeout_seconds,
        live_bus=live_bus,
        otel_tracer=otel_tracer,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await create_all(engine)
        await seed_bastion(session_factory, settings, secret_box, admin_client=admin_client)
        try:
            await recover_pool_consistency(pool_store, session_store_instance)
        except Exception:  # noqa: BLE001 - cleanup must never block boot / crash-loop the box
            logger.exception("orphan_recovery failed at startup; continuing without it")
        if not await codex_available():
            logger.warning(
                "codex runtime registered but `codex` binary missing; "
                "codex-kind agents will error until it is installed"
            )
        bridge_server = None
        bridge_server_task = None
        if settings.codex_bridge_enabled:
            if settings.codex_bridge_host not in ("127.0.0.1", "localhost", "::1"):
                logger.warning(
                    "codex_bridge_host=%s is not loopback; the tool bridge must bind localhost only",
                    settings.codex_bridge_host,
                )
            bridge_config = uvicorn.Config(
                codex_bridge.app(),
                host=settings.codex_bridge_host,
                port=settings.codex_bridge_port,
                log_level="warning",
            )
            bridge_server = uvicorn.Server(bridge_config)
            bridge_server_task = asyncio.create_task(bridge_server.serve())
            logger.info(
                "codex tool bridge on %s:%d",
                settings.codex_bridge_host, settings.codex_bridge_port,
            )
            deadline = asyncio.get_running_loop().time() + 5.0
            while not bridge_server.started and asyncio.get_running_loop().time() < deadline:
                await asyncio.sleep(0.05)
            if not bridge_server.started:
                logger.warning(
                    "codex tool bridge did not start within 5s on %s:%d; "
                    "codex tool calls will fail until it is reachable",
                    settings.codex_bridge_host, settings.codex_bridge_port,
                )
            # Write MCP server config to ~/.codex/config.toml so codex picks it
            # up automatically. codex 0.135 silently ignores -c mcp_servers.*
            # overrides passed as command-line args.
            _write_codex_mcp_config(
                host=settings.codex_bridge_host,
                port=settings.codex_bridge_port,
            )
        workers = [
            asyncio.create_task(worker_loop(queue, deps))
            for _ in range(settings.job_worker_count)
        ]
        scheduler_task = (
            asyncio.create_task(scheduler_loop.run_forever(settings.schedule_poll_interval_seconds))
            if settings.scheduler_enabled
            else None
        )
        reaper_task = asyncio.create_task(
            session_reaper_loop.run_forever(settings.session_reaper_poll_seconds)
        )
        try:
            yield
        finally:
            for w in workers:
                w.cancel()
            if scheduler_task is not None:
                scheduler_task.cancel()
            reaper_task.cancel()
            if bridge_server_task is not None:
                bridge_server.should_exit = True
                with contextlib.suppress(Exception, asyncio.CancelledError):
                    await asyncio.wait_for(bridge_server_task, timeout=5.0)
                if not bridge_server_task.done():
                    bridge_server_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await bridge_server_task
            await browser_goal_runner.aclose()
            await asyncio.gather(
                *workers,
                *(task for task in [scheduler_task, reaper_task] if task is not None),
                return_exceptions=True,
            )
            await engine.dispose()

    async def _resolve_pool_bot_turn(bot_email: str, channel: str, topic: str):
        return await resolve_pool_bot_for_webhook(pool_store, session_store_instance, bot_email, channel, topic)

    app = FastAPI(lifespan=lifespan)
    app.state.tool_registry = tool_registry
    try:
        _app_version = metadata_version("control-plane")
    except PackageNotFoundError:  # pragma: no cover - dist always installed in prod/CI
        _app_version = "unknown"
    app.include_router(
        build_meta_router(
            git_sha=settings.git_sha,
            version=_app_version,
            started_at=datetime.now(UTC).isoformat(),
        )
    )
    app.include_router(build_observability_router(live_bus))
    if settings.otel_enabled:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        FastAPIInstrumentor.instrument_app(app)
    # Keep the concurrently-developed agent-types catalog router.
    app.include_router(build_agent_types_router())
    app.include_router(build_dashboard_router(session_factory))
    app.include_router(
        build_webhook_router(
            resolve_agent_by_email=registry.resolve_by_bot_email,
            make_agent_client=make_agent_client,
            enqueue_job=queue.enqueue,
            write_event=partial(write_event, session_factory),
            resolve_pool_bot_turn=_resolve_pool_bot_turn,
        )
    )
    app.include_router(build_pool_router(pool_store))
    app.include_router(
        build_agents_router(
            registry=registry,
            admin_client=admin_client,
            payload_url=f"{settings.public_base_url or settings.zulip_site}/zulip/incoming",
        )
    )
    app.include_router(build_browser_goals_router(browser_goal_runner))
    write_event_bound = partial(write_event, session_factory)

    async def _artifact_enqueue_turn(*, agent_id, channel, topic, content, source):
        await enqueue_agent_turn(
            agent_id=agent_id,
            channel=channel,
            topic=topic,
            content=content,
            source=source,
            write_event=write_event_bound,
            enqueue_job=queue.enqueue,
        )

    app.include_router(
        build_artifacts_router(
            store=artifact_store,
            resolve_agent=_resolve_by_id(registry),
            make_agent_client=make_agent_client,
            enqueue_turn=_artifact_enqueue_turn,
            llm_client_factory=lambda: default_client_factory(settings.openai_key, None),
            summary_model=settings.artifact_summary_model,
            max_payload_bytes=settings.artifact_max_payload_bytes,
            base_url=artifact_base_url,
            clock=lambda: datetime.now(UTC),
        )
    )
    return app
