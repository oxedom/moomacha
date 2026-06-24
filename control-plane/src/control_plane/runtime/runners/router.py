from __future__ import annotations

from typing import Any

from control_plane.runtime.runners.base import AgentRunner, UnknownRuntimeKind
from control_plane.runtime.runners.openai_loop import OpenAIToolLoopRunner
from control_plane.runtime.tools.registry import ToolRegistry
from control_plane.runtime.tools.runtime import ToolRuntime


class AgentRunnerRouter:
    def __init__(self, runners: dict[str, AgentRunner]) -> None:
        self._runners = runners

    @classmethod
    def default(
        cls, registry: ToolRegistry, runtime: ToolRuntime, *, max_tool_calls: int
    ) -> "AgentRunnerRouter":
        """Openai-only router. process_job falls back to this when no router is wired."""
        return cls({"openai_tool_loop": OpenAIToolLoopRunner(registry, runtime, max_tool_calls)})

    def select(self, agent: Any) -> AgentRunner:
        kind = getattr(agent, "runtime_kind", None) or "openai_tool_loop"
        runner = self._runners.get(kind)
        if runner is None:
            raise UnknownRuntimeKind(kind)
        return runner
