"""Live e2e: prove the codex tool bridge works. Posts a #sandbox mention to the
codex test agent (sandbox-helper) asking it to list Google tasks (forcing a
gtasks_list call THROUGH the bridge), polls for the bot reply, and checks the Neon
events trail for that turn:

  turn.start -> tool.call(gtasks_list) -> reply (and turn.end status != failed)

    uv run --no-sync python scripts/verify_codex_tools_live.py
"""
import asyncio
import base64
import json
import os
import sys
import time
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from sqlalchemy import select

from control_plane.config import Settings
from control_plane.db.engine import build_session_factory
from control_plane.db.tables import AgentRow, EventRow

ROOT = Path(__file__).resolve().parents[1]
TEST_AGENT_NAME = "sandbox-helper"

_ENV_KEYS = {
    "ZULIP_SITE", "ZULIP_ADMIN_EMAIL", "ZULIP_ADMIN_API_KEY",
}


def _env() -> dict:
    """Read creds from .env if present, else fall back to os.environ (container)."""
    out = {}
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k] = v.strip().strip("'\"")
    for key in _ENV_KEYS:
        if key not in out and key in os.environ:
            out[key] = os.environ[key]
    missing = _ENV_KEYS - out.keys()
    if missing:
        raise SystemExit(f"Missing env vars: {missing}")
    return out


def _zulip(env, method, path, data=None, params=None):
    site = env["ZULIP_SITE"].rstrip("/")
    auth = base64.b64encode(
        f'{env["ZULIP_ADMIN_EMAIL"]}:{env["ZULIP_ADMIN_API_KEY"]}'.encode()
    ).decode()
    url = f"{site}/api/v1{path}"
    body = None
    if params:
        url += "?" + urllib.parse.urlencode(params)
    if data:
        body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("Authorization", f"Basic {auth}")
    if body:
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


async def _poll_reply(env, topic, bot_id, after_id, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        res = _zulip(env, "GET", "/messages", params={
            "anchor": "newest", "num_before": 20, "num_after": 0,
            "narrow": json.dumps([
                {"operator": "stream", "operand": "sandbox"},
                {"operator": "topic", "operand": topic},
            ]),
        })
        for m in res.get("messages", []):
            placeholder = "Working on it" in m["content"] or "thinking" in m["content"]
            if m["sender_id"] == bot_id and m["id"] > after_id and not placeholder:
                return m
        await asyncio.sleep(5)
    return None


async def main() -> None:
    env = _env()
    settings = Settings()
    factory, engine = build_session_factory(settings.neon_database_url)

    async with factory() as s:
        agent = (await s.execute(
            select(AgentRow).where(AgentRow.name == TEST_AGENT_NAME)
        )).scalar_one()
    bot_id = int(agent.zulip_bot_id)
    print(f"== {TEST_AGENT_NAME}: id={agent.id} runtime_kind={agent.runtime_kind!r} "
          f"allowed_tools={agent.allowed_tools} bot_id={bot_id} ==")
    if agent.runtime_kind != "codex":
        print("  !! not on the codex runtime - run scripts/seed_codex_test_agent.py first")
        await engine.dispose()
        sys.exit(1)

    runid = uuid.uuid4().hex[:8]
    topic = f"codex-tools-e2e-{runid}"
    content = (
        f"@**{TEST_AGENT_NAME}** Runtime check ({runid}): use your gtasks_list_task_lists tool to "
        f"list my Google task lists, then reply with how many lists you found."
    )
    print(f"== posting trigger to sandbox / {topic} ==")
    posted = _zulip(env, "POST", "/messages", data={
        "type": "stream", "to": "sandbox", "topic": topic, "content": content,
    })
    trigger_id = posted["id"]
    print("  message id:", trigger_id, "result:", posted.get("result"))

    print("== waiting for reply (up to 240s; codex turns are slower) ==")
    reply = await _poll_reply(env, topic, bot_id, after_id=trigger_id, timeout=240)
    if reply:
        print("  REPLY:", reply["content"][:300].replace("\n", " "))
    else:
        print("  !! no reply within timeout")

    await asyncio.sleep(3)
    async with factory() as s:
        turn = list((await s.execute(
            select(EventRow).where(EventRow.source_message_id == trigger_id)
            .order_by(EventRow.timestamp.asc())
        )).scalars())
    for e in turn:
        print(f"  [{e.timestamp.isoformat()}] {e.event_type} {json.dumps(e.payload)[:140]}")

    types = [e.event_type for e in turn]
    payloads = " ".join(json.dumps(e.payload) for e in turn)
    has_started = "turn.start" in types
    turn_failed = any(
        e.event_type == "turn.end" and (e.payload or {}).get("status") == "failed"
        for e in turn
    )
    used_tool = ("gtasks_list_task_lists" in payloads) or ("tool.call" in types) or ("mcp_tool_call" in payloads)
    ok = bool(reply) and has_started and used_tool and not turn_failed

    site = env["ZULIP_SITE"].rstrip("/")
    narrow = f"{site}/#narrow/channel/123456-sandbox/topic/{urllib.parse.quote(topic)}"
    print("\nSCREENSHOT_URL=" + narrow)
    print(f"RESULT: reply={bool(reply)} turn_start={has_started} used_tool={used_tool} "
          f"turn_failed={turn_failed}")
    print("VERDICT:", "PASS — codex used a control-plane tool via the bridge" if ok else "FAIL")
    await engine.dispose()
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    asyncio.run(main())
