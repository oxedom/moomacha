import uuid
from control_plane.runtime.runners.base import RunnerInput
from control_plane.runtime.runners.openai_loop import OpenAIToolLoopRunner
from control_plane.runtime.tools.registry import ToolRegistry
from control_plane.runtime.tools.runtime import ToolRuntime, ToolContext
from control_plane.schemas.agents import ResolvedAgent
from control_plane.services.job_queue import Job


class _Msg:
    def __init__(self, content):
        self.content = content
        self.tool_calls = []


class _Choice:
    def __init__(self, content):
        self.message = _Msg(content)


class _Completion:
    def __init__(self, content):
        self.choices = [_Choice(content)]


class _FakeLLM:
    """Minimal stand-in for AsyncOpenAI with .chat.completions.create."""

    def __init__(self, text):
        self._text = text
        self.chat = self
        self.completions = self

    async def create(self, **kwargs):
        return _Completion(self._text)


def _agent():
    return ResolvedAgent(
        id=uuid.uuid4(), name="echo", persona="p", model_id="gpt-4o",
        zulip_bot_email="e@x", zulip_api_key="k", zulip_outgoing_token="t",
        context_message_count=20, readable_channels=["sandbox"], allowed_tools=[],
    )


async def test_runner_returns_model_text():
    agent = _agent()
    registry = ToolRegistry()
    runner = OpenAIToolLoopRunner(registry=registry, runtime=ToolRuntime(registry), max_tool_calls=10)
    ctx = ToolContext(agent=agent, zulip=object(), channel="sandbox", topic="t")
    job = Job(agent_id=agent.id, channel="sandbox", topic="t", content="hi")
    inp = RunnerInput(
        job=job, agent=agent, system_prompt="sys", user_message="hi",
        tool_context=ctx, llm_client=_FakeLLM("hello back"),
    )
    assert await runner.run(inp) == "hello back"
