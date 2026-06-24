"""Bridge project tools into LangChain structured tools for DeepAgents.

Each bridged tool serializes its validated args back to JSON and calls
ToolRuntime.execute, so every existing ACL (allowed_tools, is_bastion,
can_exec) and arg-validation still applies. DeepAgents never gets a tool path
that bypasses ToolRuntime.
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from langchain_core.tools import StructuredTool

from control_plane.runtime.tools.registry import ToolRegistry
from control_plane.runtime.tools.runtime import ToolContext, ToolRuntime


def _selected_names(registry: ToolRegistry, agent: Any) -> list[str]:
    """Reuse build_schemas' selection logic (allowed + bastion + exec)."""
    schemas = registry.build_schemas(
        agent.allowed_tools,
        is_bastion=getattr(agent, "is_bastion", False),
        can_exec=getattr(agent, "can_exec", False),
    )
    return [s["function"]["name"] for s in schemas]


def bridge_tools(
    registry: ToolRegistry,
    agent: Any,
    runtime: ToolRuntime,
    ctx: ToolContext,
    on_tool_call: Callable[[str, bool], Awaitable[None]] | None = None,
) -> list[StructuredTool]:
    tools: list[StructuredTool] = []
    for name in _selected_names(registry, agent):
        entry = registry.get(name)
        if entry is None:
            continue

        def _make(tool_name: str, model_cls):
            async def _run(**kwargs) -> str:
                raw = model_cls(**kwargs).model_dump_json()
                result = await runtime.execute(tool_name, raw, ctx)
                if on_tool_call is not None:
                    await on_tool_call(tool_name, result.ok)
                return result.content
            return _run

        tools.append(
            StructuredTool.from_function(
                coroutine=_make(name, entry.input_model),
                name=name,
                description=entry.description,
                args_schema=entry.input_model,
            )
        )
    return tools
