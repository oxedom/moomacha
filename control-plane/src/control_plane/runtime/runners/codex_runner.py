# src/control_plane/runtime/runners/codex_runner.py
from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field

from control_plane.runtime.runners.base import RunnerInput
from control_plane.runtime.runners.codex_backend import BridgeWiring, CodexResult, run_codex_exec
from control_plane.services.model_era import model_era_for

SandboxMode = Literal["read-only", "workspace-write", "danger-full-access"]
_DB_SKILL_MARKER = ".control-plane-db-skill"


class CodexConfig(BaseModel):
    """Validated view of runtime_config['codex']. Bad sandbox_mode -> ValueError.
    extra='forbid' so a misspelled key surfaces as an error instead of being dropped."""
    model_config = ConfigDict(extra="forbid")
    sandbox_mode: SandboxMode | None = None
    model: str | None = None
    expose_tools: bool = False  # opt-in: bridge the agent's allowed_tools into codex via MCP
    mcp_servers: list[dict] = Field(default_factory=list)  # reserved; not used in MVP
    skills: list[str] = Field(default_factory=list)
    tripwire: bool = False  # opt-in: escalation tripwire (DarkClaw); enforced in process_job

    @classmethod
    def from_runtime_config(cls, runtime_config: dict | None) -> "CodexConfig":
        section = (runtime_config or {}).get("codex", {})
        return cls(**section)


def _codex_model(model_id: str) -> str:
    """codex's --model wants a bare model name (e.g. 'gpt-5.1-codex'). Strip a
    LangChain-style 'openai:' provider prefix if present; bare ids (the common
    case) pass through unchanged. (normalize_model_id is deliberately NOT used —
    it ADDS the 'openai:' prefix, which codex would reject.)"""
    return model_id.split(":", 1)[1] if model_id.startswith("openai:") else model_id


def _skill_name(raw: str) -> str:
    name = raw.strip().strip("/").split("/")[-1]
    if (
        not name
        or name in {".", ".."}
        or "/" in name
        or "\\" in name
        or name.startswith(".")
    ):
        raise ValueError(f"unsafe codex skill name/path: {raw!r}")
    return name


async def _load_db_skills(skill_catalog: Any, names: list[str], model_id: str) -> list[Any]:
    if skill_catalog is None:
        if names:
            raise RuntimeError("codex skills configured but no SkillCatalog is wired")
        return []
    bare_names = [_skill_name(name) for name in names]
    if not bare_names:
        return []
    return await skill_catalog.load(names=bare_names, model_era=model_era_for(model_id))


def _mount_db_skills(workdir: str, rows: list[Any]) -> None:
    """Materialize DB skill rows where Codex discovers repo-scoped skills.

    Codex scans .agents/skills from cwd upward. These per-topic workspaces are
    generated repos, so mounted DB skills live at <workdir>/.agents/skills.
    Only marker-bearing folders are overwritten or garbage-collected.
    """
    root = Path(workdir) / ".agents" / "skills"
    desired = {_skill_name(row.name) for row in rows}

    if root.exists():
        for child in root.iterdir():
            if (
                child.is_dir()
                and (child / _DB_SKILL_MARKER).is_file()
                and child.name not in desired
            ):
                shutil.rmtree(child)

    if not rows:
        return

    root.mkdir(parents=True, exist_ok=True)
    for row in rows:
        name = _skill_name(row.name)
        skill_dir = root / name
        if skill_dir.exists():
            if not (skill_dir / _DB_SKILL_MARKER).is_file():
                raise RuntimeError(
                    f"refusing to overwrite unmanaged codex skill folder: {skill_dir}"
                )
            shutil.rmtree(skill_dir)
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(row.body, encoding="utf-8")
        (skill_dir / _DB_SKILL_MARKER).write_text(
            "managed by control-plane DB skill mounting\n", encoding="utf-8"
        )


@dataclass(kw_only=True)
class CodexRunner:
    workspaces: Any                  # WorkspaceManager (duck-typed: .ensure(channel, topic))
    openai_key: str                  # settings.openai_key, reused as CODEX_API_KEY
    exec_codex: Callable[..., Awaitable[CodexResult]] = run_codex_exec
    default_sandbox_mode: str = "workspace-write"
    tool_bridge: Any = None          # CodexToolBridge | None (opt-in tool exposure)
    tool_runtime: Any = None         # ToolRuntime | None; dispatch chokepoint for bridged tools
    bridge_url: str | None = None    # loopback MCP url, e.g. http://127.0.0.1:9110/mcp
    skill_catalog: Any = None        # SkillCatalog | None; DB skills mounted as files for codex

    def __post_init__(self) -> None:
        # Validate at construction (app boot) so a bad CODEX_DEFAULT_SANDBOX_MODE
        # fails loudly at startup, not silently on the first codex turn.
        if self.default_sandbox_mode not in get_args(SandboxMode):
            raise ValueError(
                f"invalid default_sandbox_mode {self.default_sandbox_mode!r}; "
                f"expected one of {get_args(SandboxMode)}"
            )

    async def run(self, inp: RunnerInput) -> str:
        cfg = CodexConfig.from_runtime_config(inp.agent.runtime_config)
        workdir = await self.workspaces.ensure(inp.job.channel, inp.job.topic)
        model = _codex_model(cfg.model or inp.agent.model_id)
        sandbox_mode = cfg.sandbox_mode or self.default_sandbox_mode
        skill_rows = await _load_db_skills(self.skill_catalog, cfg.skills, model)

        bridge = None
        token = None
        allowed = list(getattr(inp.agent, "allowed_tools", []) or [])
        if cfg.expose_tools and self.tool_bridge and self.bridge_url and allowed:
            token = self.tool_bridge.mint(
                ctx=inp.tool_context, runtime=self.tool_runtime, allowed=allowed
            )
            bridge = BridgeWiring(
                url=self.bridge_url, token_env="CP_BRIDGE_TOKEN", token=token
            )

        try:
            async with self.workspaces.lock(workdir):
                if self.skill_catalog is not None:
                    _mount_db_skills(workdir, skill_rows)
                result = await self.exec_codex(
                    prompt=inp.user_message,
                    system_prompt=inp.system_prompt,
                    workdir=workdir,
                    model=model,
                    sandbox_mode=sandbox_mode,
                    api_key=self.openai_key,
                    bridge=bridge,
                    on_tool_call=inp.on_tool_call,
                )
        finally:
            if token is not None:
                self.tool_bridge.release(token)
        return result.final_response
