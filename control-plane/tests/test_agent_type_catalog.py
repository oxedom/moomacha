from fastapi import FastAPI
from fastapi.testclient import TestClient

from control_plane.routes.agent_types import build_agent_types_router
from control_plane.services.agent_type_catalog import get_agent_type, list_agent_types


def test_agent_type_catalog_matches_current_plan():
    items = list_agent_types()
    ids = {item.id for item in items}

    assert len(ids) == len(items)
    assert "strands_context_responder" in ids
    assert "read_only_researcher" in ids
    assert "supervised_devops" in ids
    assert "autonomous_pr_maker" in ids
    assert "intern" in ids
    assert sum(item.lifecycle == "current_slice" for item in items) == 1


def test_get_agent_type_returns_none_for_unknown_id():
    assert get_agent_type("missing") is None


def test_agent_types_json_route_is_read_only():
    app = FastAPI()
    app.include_router(build_agent_types_router())
    client = TestClient(app)

    resp = client.get("/api/agent-types")

    assert resp.status_code == 200
    body = resp.json()
    assert body[0]["id"] == "strands_context_responder"
    assert "zulip_api_key" not in str(body)
    assert "outgoing_token" not in str(body)


def test_agent_type_detail_route_404s_unknown_id():
    app = FastAPI()
    app.include_router(build_agent_types_router())
    client = TestClient(app)

    resp = client.get("/api/agent-types/nope")

    assert resp.status_code == 404


def test_agent_types_dashboard_renders_catalog():
    app = FastAPI()
    app.include_router(build_agent_types_router())
    client = TestClient(app)

    resp = client.get("/dev/agent-types")

    assert resp.status_code == 200
    assert "Read-only catalog" in resp.text
    assert "DB-registered Strands context responder" in resp.text
    assert "supervised_devops" in resp.text
