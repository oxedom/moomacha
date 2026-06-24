"""Live e2e verification of the Task 8 scheduling tools.

Drives a real Zulip round-trip via the admin API (no browser needed for the
trigger, per CLAUDE.md), then verifies against Neon:

1. Post `@**Echo** ... use schedule_task (delay_seconds=60) ...` to sandbox.
2. Poll the topic for Echo's confirmation reply.
3. Assert a scheduled_jobs row was created with our marker instruction.
4. Wait for the scheduler to fire it; assert a schedule_fired event + the
   bot's fired reply land in the topic.

Prints a topic narrow URL for the Playwright screenshot.
"""

import asyncio
import base64
import json
import sys
import time
import urllib.request
import uuid
from pathlib import Path

from sqlalchemy import select

from control_plane.config import Settings
from control_plane.db.engine import build_session_factory
from control_plane.db.tables import EventRow, ScheduledJobRow

ROOT = Path(__file__).resolve().parents[1]


def _env() -> dict[str, str]:
    out = {}
    for line in (ROOT / ".env").read_text().splitlines():
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            out[k] = v.strip().strip("'\"")
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


import urllib.parse  # noqa: E402

BOT_ID = 1085008  # Echo


async def main() -> None:
    env = _env()
    settings = Settings()
    factory, engine = build_session_factory(settings.neon_database_url)
    runid = uuid.uuid4().hex[:8]
    topic = f"schedule-e2e-{runid}"
    marker = f"FIRE-{runid}"
    content = (
        f"@**Echo** Use the schedule_task tool now to schedule a ONE-SHOT task "
        f"60 seconds from now (delay_seconds=60). The instruction must be exactly: "
        f'"{marker} say hello". After scheduling, confirm in one sentence.'
    )

    print(f"== posting trigger to sandbox / {topic} ==")
    posted = _zulip(env, "POST", "/messages", data={
        "type": "stream", "to": "sandbox", "topic": topic, "content": content,
    })
    print("  message id:", posted.get("id"), "result:", posted.get("result"))

    # 1) poll the topic for Echo's confirmation reply
    print("== waiting for Echo's confirmation reply ==")
    reply = await _poll_bot_reply(env, topic, after_id=posted["id"], timeout=90)
    if reply:
        print("  REPLY:", reply["content"][:200].replace("\n", " "))
    else:
        print("  !! no confirmation reply within timeout")

    # 2) assert a scheduled_jobs row with our marker
    print("== checking scheduled_jobs row in Neon ==")
    row = None
    async with factory() as s:
        rows = list((await s.execute(
            select(ScheduledJobRow).where(ScheduledJobRow.topic == topic)
        )).scalars())
    for r in rows:
        if marker in r.instruction:
            row = r
    if not row:
        print("  !! NO scheduled_jobs row found for marker", marker, "- FAIL")
        print("  rows in topic:", [(str(x.id), x.kind, x.instruction[:40]) for x in rows])
        await engine.dispose()
        sys.exit(1)
    print(f"  ROW id={row.id} kind={row.kind} status={row.status} "
          f"next_run_at={row.next_run_at.isoformat()} instr={row.instruction!r}")

    # 3) wait for the fire (delay 60s + poll interval 30s + slack)
    print("== waiting for scheduler to fire (up to 150s) ==")
    fired_event = await _poll_fired_event(factory, row.id, timeout=150)
    print("  schedule_fired event:", "FOUND" if fired_event else "NOT FOUND")

    # 4) the fired turn should post a new bot reply in the topic
    after = reply["id"] if reply else posted["id"]
    fired_reply = await _poll_bot_reply(env, topic, after_id=after, timeout=60)
    if fired_reply:
        print("  FIRED REPLY:", fired_reply["content"][:200].replace("\n", " "))

    # final row state
    async with factory() as s:
        fresh = (await s.execute(
            select(ScheduledJobRow).where(ScheduledJobRow.id == row.id)
        )).scalar_one()
    print(f"== final row status: {fresh.status} (one_shot should be 'completed') ==")

    site = env["ZULIP_SITE"].rstrip("/")
    narrow = f"{site}/#narrow/channel/123456-sandbox/topic/{urllib.parse.quote(topic)}"
    print("\nSCREENSHOT_URL=" + narrow)
    print(f"RESULT: created={bool(row)} fired_event={bool(fired_event)} "
          f"fired_reply={bool(fired_reply)} final_status={fresh.status}")
    await engine.dispose()


async def _poll_bot_reply(env, topic, after_id, timeout):
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
            if m["sender_id"] == BOT_ID and m["id"] > after_id and "🤔" not in m["content"]:
                return m
        await asyncio.sleep(5)
    return None


async def _poll_fired_event(factory, schedule_id, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        async with factory() as s:
            evs = list((await s.execute(
                select(EventRow).where(EventRow.event_type == "schedule_fired")
            )).scalars())
        for e in evs:
            if str(schedule_id) in json.dumps(e.payload):
                return e
        await asyncio.sleep(5)
    return None


if __name__ == "__main__":
    asyncio.run(main())
