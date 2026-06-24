from datetime import UTC, datetime

from control_plane.app import build_scheduler_loop
from control_plane.config import Settings
from control_plane.db.engine import build_session_factory


def _settings():
    return Settings(
        zulip_site="https://x.zulipchat.com",
        neon_database_url="sqlite+aiosqlite://",
        openai_key="sk-x",
        agent_fernet_key="k",
        schedule_misfire_grace_seconds=1800,
        schedule_max_due_per_tick=42,
    )


async def test_build_scheduler_loop_uses_settings_and_enqueue():
    factory, engine = build_session_factory("sqlite+aiosqlite://")
    try:
        jobs = []

        async def enqueue_job(job):
            jobs.append(job)

        loop = build_scheduler_loop(_settings(), factory, enqueue_job)
        assert loop._deps.grace_seconds == 1800
        assert loop._deps.max_due_per_tick == 42
        now = loop._deps.clock()
        assert isinstance(now, datetime)
        assert now.tzinfo == UTC
    finally:
        await engine.dispose()
