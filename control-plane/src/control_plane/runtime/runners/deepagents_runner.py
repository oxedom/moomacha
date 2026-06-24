"""DeepAgents-backed runner. build_agent is injectable so unit tests never touch
the real SDK or the network; the real factory lives in deepagents_backend.py.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from control_plane.runtime.model_ids import normalize_model_id
from control_plane.runtime.runners.base import RunnerInput
from control_plane.runtime.runners.deepagents_backend import (
    build_deep_agent, build_subagents, load_skill_files,
)
from control_plane.runtime.runners.thread_id import make_thread_id
from control_plane.runtime.runners.tool_bridge import bridge_tools
from control_plane.runtime.tools.registry import ToolRegistry
from control_plane.runtime.tools.runtime import ToolRuntime
from control_plane.services.model_era import model_era_for


async def resolve_db_skills(skill_catalog, names: list[str], model_id: str) -> dict[str, str]:
    """Return a virtual-path -> FileData map of active, era-matching skills among
    `names`, in the same format DeepAgents' StateBackend seeds (see load_skill_files).
    The skill is *activated* by name via build_deep_agent(skills=...); these files
    supply its body. SKILL.md bodies must carry valid YAML frontmatter or
    DeepAgents' SkillsMiddleware silently skips them."""
    if skill_catalog is None or not names:
        return {}
    from deepagents.backends.utils import create_file_data
    bare_names = [n.strip("/").split("/")[-1] for n in names]
    rows = await skill_catalog.load(names=bare_names, model_era=model_era_for(model_id))
    return {f"/skills/{r.name}/SKILL.md": create_file_data(r.body) for r in rows}


def _flatten_content(content: Any) -> str:
    """LangChain message .content may be a plain string or a list of content
    blocks (e.g. [{"type": "text", "text": ...}]). Flatten to the text we post to
    Zulip; posting the raw list str() shows users a dict blob (live-e2e bug)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
            elif isinstance(block, str):
                parts.append(block)
        return "".join(parts)
    return str(content)


def _default_checkpointer() -> Any:
    # Phase 1: in-process only (no HITL). Durable Postgres checkpointer is a
    # prerequisite of the HITL slice, not this one.
    from langgraph.checkpoint.memory import MemorySaver
    return MemorySaver()


@dataclass
class DeepAgentRunner:
    registry: ToolRegistry
    runtime: ToolRuntime
    build_agent: Callable[..., Any] = build_deep_agent
    checkpointer_factory: Callable[[], Any] = _default_checkpointer
    skill_catalog: Any = None  # SkillCatalog | None; when set, skills load from Postgres

    async def run(self, inp: RunnerInput) -> str:
        cfg = (inp.agent.runtime_config or {}).get("deepagents", {})
        tools = bridge_tools(
            self.registry, inp.agent, self.runtime, inp.tool_context, inp.on_tool_call
        )
        tools_by_name = {t.name: t for t in tools}
        graph = self.build_agent(
            model=normalize_model_id(inp.agent.model_id),
            tools=tools,
            system_prompt=inp.system_prompt,
            subagents=build_subagents(cfg.get("subagents", []), tools_by_name),
            skills=cfg.get("skills", []),
            checkpointer=self.checkpointer_factory(),
        )
        skill_names = cfg.get("skills", [])
        if self.skill_catalog is not None:
            files = await resolve_db_skills(self.skill_catalog, skill_names, inp.agent.model_id)
        else:
            # legacy fallback: treat entries as on-disk skill paths
            legacy_names = [s.strip("/").split("/")[-1] for s in skill_names]
            files = load_skill_files(legacy_names)
        state: dict[str, Any] = {"messages": [{"role": "user", "content": inp.user_message}]}
        if files:
            state["files"] = files
        result = await graph.ainvoke(
            state, config={"configurable": {"thread_id": make_thread_id(inp.job, inp.agent)}}
        )
        return _flatten_content(result["messages"][-1].content)
