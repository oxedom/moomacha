import uuid

import pytest

from control_plane.runtime.runners.base import RunnerInput
from control_plane.runtime.runners.relay_runner import RelayRunner, RELAY_MAX_CHARS
from control_plane.runtime.tools.runtime import ToolContext
from control_plane.schemas.agents import ResolvedAgent
from control_plane.services.job_queue import Job


def _agent(name="relaybot", persona="PERSONA-MARKER"):
    return ResolvedAgent(
        id=uuid.uuid4(), name=name, persona=persona, model_id="gpt-4o",
        zulip_bot_email="e@x", zulip_api_key="k", zulip_outgoing_token="t",
        context_message_count=20, readable_channels=["sandbox"], allowed_tools=[],
    )


def _input(system_prompt, user_message, channel="sandbox", topic="t"):
    agent = _agent()
    ctx = ToolContext(agent=agent, zulip=object(), channel=channel, topic=topic)
    job = Job(agent_id=agent.id, channel=channel, topic=topic, content=user_message)
    return RunnerInput(
        job=job, agent=agent, system_prompt=system_prompt,
        user_message=user_message, tool_context=ctx,
    )


@pytest.mark.asyncio
async def test_relay_echoes_system_prompt_and_user_message():
    inp = _input(
        system_prompt="PERSONA-MARKER\n\n## Recent conversation in #sandbox > t\nAlice: hi",
        user_message="UNIQUE-USER-MARKER",
    )
    out = await RelayRunner().run(inp)
    assert "## system_prompt" in out
    assert "PERSONA-MARKER" in out
    assert "## Recent conversation in #sandbox > t" in out
    assert "## user_message" in out
    assert "UNIQUE-USER-MARKER" in out
    assert "relaybot" in out  # agent name in the header line


@pytest.mark.asyncio
async def test_relay_truncates_oversized_system_prompt():
    huge = "X" * (RELAY_MAX_CHARS + 5000)
    inp = _input(system_prompt=huge, user_message="SMALL-MARKER")
    out = await RelayRunner().run(inp)
    assert len(out) <= RELAY_MAX_CHARS
    assert "[truncated" in out
    assert "SMALL-MARKER" in out  # user_message always survives in full


@pytest.mark.asyncio
async def test_relay_direct_message_location():
    inp = _input(
        system_prompt="PERSONA-MARKER",
        user_message="DM-MARKER",
        channel="direct",
    )
    out = await RelayRunner().run(inp)
    assert "direct message" in out


@pytest.mark.asyncio
async def test_relay_clamps_oversized_user_message():
    # Regression for the size-guard bug: a pathologically large user_message
    # must not push the whole reply past the cap.
    huge_user = "U" * (RELAY_MAX_CHARS + 5000)
    inp = _input(system_prompt="PERSONA-MARKER", user_message=huge_user)
    out = await RelayRunner().run(inp)
    assert len(out) <= RELAY_MAX_CHARS
