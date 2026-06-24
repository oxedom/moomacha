"""DeepAgents backend, skill-file loading, subagent profiles, and the real
build_deep_agent factory.

Phase 1 uses a StateBackend (thread-scoped scratch) only; CompositeBackend +
StoreBackend memory routes and a durable Postgres checkpointer are deferred to
the memory-writes / HITL slices.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

# control-plane/skills/<name>/SKILL.md  (repo skills dir, above src/)
_SKILLS_ROOT = Path(__file__).resolve().parents[4] / "skills"

# Subagent profiles. `tools` lists the project tool NAMES a profile wants; the
# real BaseTool objects are resolved per-agent in build_subagents() against the
# tools that were ACL-bridged for that agent, so a subagent can never receive a
# tool the parent agent isn't allowed. Keep all read-only until HITL exists.
# NB: DeepAgents' SubAgent TypedDict requires `name`, `description`, and
# `system_prompt` (NOT `prompt`), and `tools` must be BaseTool objects.
SUBAGENT_PROFILES: dict[str, dict[str, Any]] = {
    "researcher": {
        "name": "researcher",
        "description": "Web/topic research with context quarantine.",
        "system_prompt": "Research the question using search/extract and read tools. Return findings only.",
        "tools": ["tavily_search", "tavily_extract", "read_topic", "read_channel"],
    },
    "summarizer": {
        "name": "summarizer",
        "description": "Compress long Zulip histories, docs, and tool results.",
        "system_prompt": "Summarize the provided material faithfully and concisely.",
        "tools": ["read_topic", "read_channel"],
    },
}


def load_skill_files(skill_names: list[str]) -> dict[str, Any]:
    """Read SKILL.md files into the virtual-path -> FileData map DeepAgents seeds.

    StateBackend's default v2 file format expects each entry to be a FileData
    dict (content/encoding/timestamps), not a bare string, so wrap via
    create_file_data. SKILL.md files must carry valid YAML frontmatter
    (name/description) or DeepAgents' SkillsMiddleware silently skips them.
    """
    from deepagents.backends.utils import create_file_data

    files: dict[str, Any] = {}
    for name in skill_names:
        path = _SKILLS_ROOT / name / "SKILL.md"
        if path.is_file():
            files[f"/skills/{name}/SKILL.md"] = create_file_data(path.read_text(encoding="utf-8"))
    return files


def build_subagents(names: list[str], tools_by_name: dict[str, Any]) -> list[dict[str, Any]]:
    """Build DeepAgents SubAgent dicts for the named profiles.

    Each subagent's `tools` are the profile's requested tools intersected with
    the parent agent's already-ACL-bridged tools (`tools_by_name`), so a subagent
    cannot acquire a tool the parent agent lacks. Returns SDK-ready dicts with
    `system_prompt` and real BaseTool objects.
    """
    subagents: list[dict[str, Any]] = []
    for name in names:
        profile = SUBAGENT_PROFILES.get(name)
        if profile is None:
            continue
        resolved_tools = [tools_by_name[t] for t in profile["tools"] if t in tools_by_name]
        subagents.append(
            {
                "name": profile["name"],
                "description": profile["description"],
                "system_prompt": profile["system_prompt"],
                "tools": resolved_tools,
            }
        )
    return subagents


def build_deep_agent(
    *,
    model: Any,
    tools: list,
    system_prompt: str,
    subagents: list[dict[str, Any]],
    skills: list[str],
    checkpointer: Any,
):
    """Real factory. Exercised against the real SDK (no network) in
    test_deepagents_real_sdk.py and in the live e2e step; DeepAgentRunner unit
    tests inject a fake build_agent.

    `subagents` must already be SDK-ready (see build_subagents). `model` may be a
    provider-prefixed string or a BaseChatModel instance. Built-in planning +
    virtual filesystem middleware are on by default; do NOT enable any
    shell/sandbox backend.
    """
    from deepagents import create_deep_agent
    from deepagents.backends import StateBackend

    return create_deep_agent(
        model=model,
        tools=tools,
        system_prompt=system_prompt,
        subagents=subagents or None,
        skills=skills or None,
        backend=StateBackend(),
        checkpointer=checkpointer,
    )
