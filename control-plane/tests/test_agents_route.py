import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from httpx import ASGITransport

from control_plane.db.engine import build_session_factory, create_all
from control_plane.routes.agents import build_agents_router
from control_plane.services.agent_registry import AgentRegistry
from control_plane.services.crypto import SecretBox
from control_plane.services.zulip_admin import ProvisionResult


class FakeAdmin:
    async def provision_bot(self, full_name, short_name, payload_url, channels):
        return ProvisionResult(
            bot_id=99, api_key="provisioned-key", bot_email=f"{short_name}@example.zulipchat.com"
        )


@pytest.fixture
async def client():
    # Run the app in the SAME event loop as the async engine via ASGITransport.
    # A sync TestClient would use a separate loop and break the aiosqlite engine.
    factory, engine = build_session_factory("sqlite+aiosqlite://")
    await create_all(engine)
    registry = AgentRegistry(factory, SecretBox(Fernet.generate_key().decode()))
    app = FastAPI()
    app.include_router(
        build_agents_router(
            registry=registry,
            admin_client=FakeAdmin(),
            payload_url="https://tunnel/zulip/incoming",
        )
    )
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    await engine.dispose()


MANUAL = {
    "name": "manual",
    "persona": "p",
    "zulip_bot_id": 5,
    "zulip_bot_email": "manual-bot@x",
    "zulip_api_key": "manual-key",
    "zulip_outgoing_token": "tok",
    "readable_channels": ["sandbox"],
}


async def test_manual_registration_persists_supplied_creds(client):
    resp = await client.post("/agents", json=MANUAL)
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "manual"
    assert body["zulip_bot_email"] == "manual-bot@x"
    assert "zulip_api_key" not in body
    assert "zulip_outgoing_token" not in body


async def test_auto_provision_when_no_creds(client):
    resp = await client.post("/agents", json={"name": "auto", "persona": "p", "readable_channels": ["sandbox"]})
    assert resp.status_code == 201
    body = resp.json()
    assert body["zulip_bot_id"] == 99
    assert body["zulip_bot_email"] == "auto-bot@example.zulipchat.com"


async def test_duplicate_name_returns_409(client):
    assert (await client.post("/agents", json={"name": "dup", "persona": "p"})).status_code == 201
    second = await client.post("/agents", json={"name": "dup", "persona": "p"})
    assert second.status_code == 409


async def test_list_agents(client):
    await client.post("/agents", json={"name": "auto", "persona": "p"})
    resp = await client.get("/agents")
    assert resp.status_code == 200
    assert len(resp.json()) == 1


async def test_delete_agent(client):
    created = (await client.post("/agents", json={"name": "auto", "persona": "p"})).json()
    assert (await client.delete(f"/agents/{created['id']}")).status_code == 204
    assert (await client.get("/agents")).json() == []


async def test_delete_unknown_returns_404(client):
    import uuid

    assert (await client.delete(f"/agents/{uuid.uuid4()}")).status_code == 404
