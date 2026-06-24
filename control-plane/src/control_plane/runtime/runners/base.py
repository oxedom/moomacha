from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Protocol, runtime_checkable

from control_plane.runtime.tools.runtime import ToolContext
from control_plane.services.job_queue import Job


@dataclass
class RunnerInput:
    job: Job
    agent: Any  # ResolvedAgent (duck-typed to avoid a schema import cycle)
    system_prompt: str
    user_message: str
    tool_context: ToolContext
    on_tool_call: Callable[[str, bool], Awaitable[None]] | None = None
    llm_client: Any = None  # used only by OpenAIToolLoopRunner (per-job OpenAI client)
    events: Any = None  # EventEmitter | None; observability seam for this turn


@runtime_checkable
class AgentRunner(Protocol):
    async def run(self, inp: RunnerInput) -> str: ...


class UnknownRuntimeKind(Exception):
    """Raised when an agent's runtime_kind has no registered runner."""

    def __init__(self, kind: str) -> None:
        super().__init__(f"No runner registered for runtime_kind={kind!r}")
        self.kind = kind
