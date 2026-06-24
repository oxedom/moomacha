"""Real-SDK integration test (no network).

The DeepAgentRunner unit tests inject a fake compiled graph, so they cannot catch
shape mismatches against the real `create_deep_agent` (subagent TypedDict keys,
file-data format, tool object types). This test builds a REAL deep agent via the
production `build_deep_agent` factory using a fake chat model that returns a final
answer with no tool calls, exercising the skills + subagents + filesystem
middleware end-to-end without any network call.
"""
import itertools

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langchain_core.tools import StructuredTool
from langgraph.checkpoint.memory import MemorySaver
from pydantic import BaseModel

from control_plane.runtime.runners.deepagents_backend import (
    build_deep_agent, build_subagents, load_skill_files,
)


class _FakeToolModel(GenericFakeChatModel):
    """Fake chat model that accepts bind_tools (returns itself) so create_deep_agent
    can attach tools; it always returns a final answer with no tool calls."""

    def bind_tools(self, *args, **kwargs):
        return self


class _Args(BaseModel):
    q: str


async def _coro(**kwargs):
    return "ok"


async def test_build_deep_agent_runs_with_skills_and_subagents_no_network():
    read_topic = StructuredTool.from_function(
        coroutine=_coro, name="read_topic", description="Read a topic", args_schema=_Args
    )
    tools_by_name = {"read_topic": read_topic}

    model = _FakeToolModel(messages=itertools.cycle([AIMessage(content="FINAL ANSWER")]))
    agent = build_deep_agent(
        model=model,
        tools=[read_topic],
        system_prompt="sys",
        subagents=build_subagents(["researcher", "summarizer"], tools_by_name),
        skills=["/skills/personal-assistant/"],
        checkpointer=MemorySaver(),
    )

    files = load_skill_files(["personal-assistant"])
    result = await agent.ainvoke(
        {"messages": [{"role": "user", "content": "hi"}], "files": files},
        config={"configurable": {"thread_id": "real-sdk-test"}},
    )
    assert result["messages"][-1].content == "FINAL ANSWER"
