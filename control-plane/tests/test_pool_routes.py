import pytest
import httpx
from cryptography.fernet import Fernet
from httpx import ASGITransport
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from control_plane.db.tables import Base
from control_plane.services.crypto import SecretBox
from control_plane.services.pool_store import PoolStore
from control_plane.routes.pool import build_pool_router

KEY = Fernet.generate_key().decode()


@pytest.fixture
async def pool_client():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    pool = PoolStore(factory, SecretBox(KEY))
    app = FastAPI()
    app.include_router(build_pool_router(pool))
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client, pool
    await engine.dispose()


async def test_seed_bot_creates_free_bot(pool_client):
    client, pool = pool_client
    resp = await client.post("/pool/bots", json={
        "zulip_bot_id": 1,
        "zulip_bot_email": "worker1@example.com",
        "zulip_api_key": "somekey",
        "zulip_outgoing_token": "tok",
    })
    assert resp.status_code == 201
    assert resp.json() == {"status": "seeded"}
    assert await pool.count_free() == 1


async def test_list_bots_shows_status(pool_client):
    client, pool = pool_client
    await pool.seed(zulip_bot_id=1, zulip_bot_email="w1@x", api_key="k", outgoing_token="t")
    resp = await client.get("/pool/bots")
    assert resp.status_code == 200
    bots = resp.json()
    assert len(bots) == 1
    assert bots[0]["email"] == "w1@x"
    assert bots[0]["status"] == "free"
    assert bots[0]["current_name"] is None


async def test_list_bots_empty_pool(pool_client):
    client, pool = pool_client
    resp = await client.get("/pool/bots")
    assert resp.status_code == 200
    assert resp.json() == []
