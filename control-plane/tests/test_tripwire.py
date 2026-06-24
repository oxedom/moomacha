import uuid
from types import SimpleNamespace

import pytest

from control_plane.services.tripwire import (
    DARKCLAW_BASELINE_TOOLS,
    TripVerdict,
    classify,
    fire_tripwire,
    tripwire_enabled,
)


def test_baseline_has_19_tools():
    assert len(DARKCLAW_BASELINE_TOOLS) == 19


def test_classify_exact_baseline_not_tripped():
    v = classify(list(DARKCLAW_BASELINE_TOOLS))
    assert v == TripVerdict(tripped=False, offending=())


def test_classify_subset_not_tripped():
    v = classify(["read_topic", "remember"])
    assert v.tripped is False
    assert v.offending == ()


def test_classify_extra_tool_is_tripped():
    v = classify(list(DARKCLAW_BASELINE_TOOLS) + ["run_command"])
    assert v.tripped is True
    assert v.offending == ("run_command",)


def test_classify_swap_same_length_is_tripped():
    # Drop one baseline tool, add one non-baseline tool: length unchanged,
    # but the superset check still catches it.
    tools = (list(DARKCLAW_BASELINE_TOOLS)[:-1]) + ["spawn_agent"]
    v = classify(tools)
    assert v.tripped is True
    assert v.offending == ("spawn_agent",)


def test_classify_offending_is_sorted_and_deduped():
    v = classify(list(DARKCLAW_BASELINE_TOOLS) + ["zzz_tool", "aaa_tool", "aaa_tool"])
    assert v.offending == ("aaa_tool", "zzz_tool")


def test_tripwire_enabled_true_for_flagged_codex_agent():
    agent = SimpleNamespace(runtime_config={"codex": {"tripwire": True}})
    assert tripwire_enabled(agent) is True


def test_tripwire_enabled_false_when_flag_absent():
    assert tripwire_enabled(SimpleNamespace(runtime_config={"codex": {}})) is False
    assert tripwire_enabled(SimpleNamespace(runtime_config={})) is False
    assert tripwire_enabled(SimpleNamespace(runtime_config=None)) is False


def test_tripwire_enabled_false_when_flag_not_true():
    assert tripwire_enabled(SimpleNamespace(runtime_config={"codex": {"tripwire": "yes"}})) is False
    assert tripwire_enabled(SimpleNamespace(runtime_config={"codex": {"tripwire": False}})) is False


class _FakeClient:
    def __init__(self):
        self.sent = []
        self.direct_sent = []
        self.updated = []

    async def send_message(self, channel, topic, content):
        self.sent.append((channel, topic, content))
        return 999

    async def send_direct_message(self, recipient_ids, content):
        self.direct_sent.append((recipient_ids, content))
        return 999

    async def update_message(self, message_id, content):
        self.updated.append((message_id, content))


class _FakeRegistry:
    def __init__(self, raises=False):
        self.disabled = []
        self._raises = raises

    async def set_enabled(self, agent_id, enabled):
        if self._raises:
            raise RuntimeError("db down")
        self.disabled.append((agent_id, enabled))
        return True


class _FakeEmitter:
    def __init__(self):
        self.errors = []

    async def error(self, **attrs):
        self.errors.append(attrs)


def _agent(aid=None):
    return SimpleNamespace(id=aid or uuid.uuid4(), name="DarkClaw")


def _job(conversation_type="stream", recipients=None):
    return SimpleNamespace(
        channel="sandbox",
        topic="lair",
        conversation_type=conversation_type,
        direct_recipient_ids=recipients,
        source_message_id=42,
    )


@pytest.mark.asyncio
async def test_fire_tripwire_stream_posts_fuck_disables_and_audits():
    agent, job = _agent(), _job()
    client, registry, emitter = _FakeClient(), _FakeRegistry(), _FakeEmitter()
    verdict = TripVerdict(tripped=True, offending=("run_command",))

    await fire_tripwire(
        agent=agent, job=job, client=client, registry=registry,
        emitter=emitter, progress_id=555, verdict=verdict,
    )

    assert client.sent == [("sandbox", "lair", "Fuck")]
    assert registry.disabled == [(agent.id, False)]
    assert len(emitter.errors) == 1
    assert emitter.errors[0]["error_type"] == "tripwire_tripped"
    assert emitter.errors[0]["offending_tools"] == ["run_command"]
    assert emitter.errors[0]["agent_id"] == str(agent.id)
    assert client.updated == [(555, "—")]


@pytest.mark.asyncio
async def test_fire_tripwire_direct_uses_dm():
    agent, job = _agent(), _job(conversation_type="direct", recipients=[7])
    client, registry, emitter = _FakeClient(), _FakeRegistry(), _FakeEmitter()
    verdict = TripVerdict(tripped=True, offending=("x",))

    await fire_tripwire(
        agent=agent, job=job, client=client, registry=registry,
        emitter=emitter, progress_id=1, verdict=verdict,
    )

    assert client.direct_sent == [([7], "Fuck")]
    assert client.sent == []
    assert registry.disabled == [(agent.id, False)]
    assert len(emitter.errors) == 1


@pytest.mark.asyncio
async def test_fire_tripwire_survives_registry_failure():
    agent, job = _agent(), _job()
    client, registry, emitter = _FakeClient(), _FakeRegistry(raises=True), _FakeEmitter()
    verdict = TripVerdict(tripped=True, offending=("x",))

    # Must not raise: the "Fuck" + audit still happen even if disable fails.
    await fire_tripwire(
        agent=agent, job=job, client=client, registry=registry,
        emitter=emitter, progress_id=1, verdict=verdict,
    )
    assert client.sent == [("sandbox", "lair", "Fuck")]
    assert len(emitter.errors) == 1


@pytest.mark.asyncio
async def test_fire_tripwire_tolerates_missing_registry():
    agent, job = _agent(), _job()
    client, emitter = _FakeClient(), _FakeEmitter()
    verdict = TripVerdict(tripped=True, offending=("x",))

    await fire_tripwire(
        agent=agent, job=job, client=client, registry=None,
        emitter=emitter, progress_id=1, verdict=verdict,
    )
    assert client.sent == [("sandbox", "lair", "Fuck")]
    assert len(emitter.errors) == 1


@pytest.mark.asyncio
async def test_fire_tripwire_survives_client_failure():
    class _BoomClient(_FakeClient):
        async def send_message(self, channel, topic, content):
            raise RuntimeError("zulip down")

    agent, job = _agent(), _job()
    client, registry, emitter = _BoomClient(), _FakeRegistry(), _FakeEmitter()
    verdict = TripVerdict(tripped=True, offending=("x",))

    # Step 1 raising must NOT prevent disable + audit.
    await fire_tripwire(
        agent=agent, job=job, client=client, registry=registry,
        emitter=emitter, progress_id=1, verdict=verdict,
    )
    assert registry.disabled == [(agent.id, False)]
    assert len(emitter.errors) == 1
