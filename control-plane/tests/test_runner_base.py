import uuid
from control_plane.runtime.runners.base import RunnerInput, AgentRunner, UnknownRuntimeKind
from control_plane.runtime.tools.runtime import ToolContext
from control_plane.services.job_queue import Job


def test_runner_input_holds_fields():
    job = Job(agent_id=uuid.uuid4(), channel="sandbox", topic="t", content="hello")
    ctx = ToolContext(agent=object(), zulip=object(), channel="sandbox", topic="t")
    inp = RunnerInput(
        job=job, agent=object(), system_prompt="sys",
        user_message="hello", tool_context=ctx,
    )
    assert inp.system_prompt == "sys"
    assert inp.user_message == "hello"
    assert inp.on_tool_call is None
    assert inp.llm_client is None


def test_unknown_runtime_kind_is_an_exception():
    assert issubclass(UnknownRuntimeKind, Exception)


def test_protocol_runtime_checkable():
    class _R:
        async def run(self, inp: RunnerInput) -> str:
            return "ok"

    assert isinstance(_R(), AgentRunner)
