from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from control_plane.runtime.tools.registry import ToolRegistry
from control_plane.runtime.tools.runtime import ToolContext, ToolRuntime


class BudgetExceeded(Exception):
    def __init__(self, count: int) -> None:
        self.count = count
        super().__init__(f"tool-call budget exceeded after {count} calls")


@dataclass
class LoopDeps:
    client: Any  # AsyncOpenAI (or a fake with .chat.completions.create)
    registry: ToolRegistry
    runtime: ToolRuntime
    max_tool_calls: int
    on_tool_call: Callable[[str, bool], Awaitable[None]] | None = None
    events: Any = None  # EventEmitter | None


def _assistant_message(msg: Any, tool_calls: list) -> dict:
    return {
        "role": "assistant",
        "content": msg.content,
        "tool_calls": [
            {
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.function.name, "arguments": tc.function.arguments},
            }
            for tc in tool_calls
        ],
    }


def _tool_result_message(tool_call_id: str, content: str) -> dict:
    return {"role": "tool", "tool_call_id": tool_call_id, "content": content}


async def run_turn(
    messages: list[dict],
    agent: Any,
    ctx: ToolContext,
    deps: LoopDeps,
) -> str:
    """Loop model<->tools until the model emits final text. In-memory only."""
    schemas = deps.registry.build_schemas(
        agent.allowed_tools,
        is_bastion=getattr(agent, "is_bastion", False),
        can_exec=getattr(agent, "can_exec", False),
    )
    calls_made = 0
    while True:
        completion = await deps.client.chat.completions.create(
            model=agent.model_id,
            messages=messages,
            tools=schemas or None,
            temperature=0.3,
            max_tokens=1024,
        )
        if deps.events is not None:
            usage = getattr(completion, "usage", None)
            await deps.events.llm_call(
                model=agent.model_id,
                prompt_tokens=getattr(usage, "prompt_tokens", None),
                completion_tokens=getattr(usage, "completion_tokens", None),
                finish_reason=getattr(completion.choices[0], "finish_reason", None),
            )
        msg = completion.choices[0].message
        tool_calls = msg.tool_calls or []
        if not tool_calls:
            if msg.content is None:
                raise RuntimeError("LLM returned no text content")
            return str(msg.content)
        messages.append(_assistant_message(msg, tool_calls))
        for tc in tool_calls:
            calls_made += 1
            if calls_made > deps.max_tool_calls:  # allow exactly max_tool_calls executions; the next raises
                raise BudgetExceeded(calls_made)
            result = await deps.runtime.execute(tc.function.name, tc.function.arguments, ctx)
            if deps.on_tool_call is not None:
                await deps.on_tool_call(tc.function.name, result.ok)
            messages.append(_tool_result_message(tc.id, result.content))
