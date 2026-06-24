"""Verify the Plan-2 additions are reachable through the assembled app."""
import httpx
from cryptography.fernet import Fernet
from httpx import ASGITransport

from control_plane.app import create_app
from control_plane.config import Settings


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        zulip_site="https://example.zulipchat.com",
        neon_database_url="sqlite+aiosqlite://",
        openai_key="sk-x",
        agent_fernet_key=Fernet.generate_key().decode(),
    )


async def test_pool_routes_are_registered():
    app = create_app(_settings())
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            resp = await c.get("/pool/bots")
            assert resp.status_code == 200
            assert resp.json() == []


async def test_seed_and_list_pool_bot_via_api():
    app = create_app(_settings())
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            seed = await c.post("/pool/bots", json={
                "zulip_bot_id": 1,
                "zulip_bot_email": "worker1@example.com",
                "zulip_api_key": "k",
                "zulip_outgoing_token": "t",
            })
            assert seed.status_code == 201
            bots = (await c.get("/pool/bots")).json()
            assert len(bots) == 1
            assert bots[0]["status"] == "free"
