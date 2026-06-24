"""Compatibility runner: wraps the existing in-memory OpenAI tool loop."""
from __future__ import annotations

from dataclasses import dataclass

from control_plane.runtime.loop import LoopDeps, run_turn
from control_plane.runtime.runners.base import RunnerInput
from control_plane.runtime.tools.registry import ToolRegistry
from control_plane.runtime.tools.runtime import ToolRuntime


@dataclass
class OpenAIToolLoopRunner:
    registry: ToolRegistry
    runtime: ToolRuntime
    max_tool_calls: int

    async def run(self, inp: RunnerInput) -> str:
        loop_deps = LoopDeps(
            client=inp.llm_client,
            registry=self.registry,
            runtime=self.runtime,
            max_tool_calls=self.max_tool_calls,
            on_tool_call=inp.on_tool_call,
            events=inp.tool_context.events,
        )
        messages = [
            {"role": "system", "content": inp.system_prompt},
            {"role": "user", "content": inp.user_message},
        ]
        return await run_turn(messages, inp.agent, inp.tool_context, loop_deps)
