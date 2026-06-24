from pydantic import BaseModel
from langchain_core.tools import StructuredTool

from control_plane.runtime.runners.deepagents_backend import (
    load_skill_files, SUBAGENT_PROFILES, build_subagents,
)


class _Args(BaseModel):
    q: str


async def _coro(**k):
    return "ok"


def _tool(name):
    return StructuredTool.from_function(coroutine=_coro, name=name, description="d", args_schema=_Args)


def test_load_skill_files_seeds_filedata_dicts():
    files = load_skill_files(["personal-assistant"])
    key = "/skills/personal-assistant/SKILL.md"
    assert key in files
    # StateBackend v2 needs FileData dicts (not bare strings).
    assert isinstance(files[key], dict)
    assert files[key]["content"]


def test_profiles_use_system_prompt_key():
    # DeepAgents SubAgent requires system_prompt (not prompt).
    for name in ("researcher", "summarizer"):
        assert "system_prompt" in SUBAGENT_PROFILES[name]
        assert "prompt" not in SUBAGENT_PROFILES[name]


def test_build_subagents_resolves_real_tools_and_is_acl_filtered():
    tools_by_name = {"read_topic": _tool("read_topic")}  # only this tool available to the agent
    subs = build_subagents(["researcher", "summarizer"], tools_by_name)
    assert {s["name"] for s in subs} == {"researcher", "summarizer"}
    researcher = next(s for s in subs if s["name"] == "researcher")
    assert "system_prompt" in researcher
    # tavily_* are NOT available -> filtered out; only read_topic survives (ACL-safe).
    assert [t.name for t in researcher["tools"]] == ["read_topic"]


def test_build_subagents_ignores_unknown_names():
    assert build_subagents(["nope"], {}) == []
