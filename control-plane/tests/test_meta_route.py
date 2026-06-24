from fastapi import FastAPI
from fastapi.testclient import TestClient

from control_plane.routes.meta import build_meta_router


def _client(**kw):
    app = FastAPI()
    app.include_router(build_meta_router(**kw))
    return TestClient(app)


def test_version_endpoint_reports_git_sha_version_and_start_time():
    client = _client(git_sha="abc1234", version="0.1.0", started_at="2026-06-04T12:00:00+00:00")
    r = client.get("/version")
    assert r.status_code == 200
    body = r.json()
    assert body["git_sha"] == "abc1234"
    assert body["version"] == "0.1.0"
    assert body["started_at"] == "2026-06-04T12:00:00+00:00"


def test_version_defaults_to_unknown_sha_when_not_baked():
    client = _client(git_sha="unknown", version="0.1.0", started_at="x")
    assert client.get("/version").json()["git_sha"] == "unknown"
