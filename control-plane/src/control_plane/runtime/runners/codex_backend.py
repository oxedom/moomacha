# src/control_plane/runtime/runners/codex_backend.py
from __future__ import annotations

import asyncio
import contextlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable, Iterable


@dataclass
class CodexResult:
    final_response: str
    tool_calls: list[dict] = field(default_factory=list)  # [{"name": str, "ok": bool}]
    exit_code: int | None = None


@dataclass
class BridgeWiring:
    """How a codex turn reaches the control-plane tool bridge. `token` is only
    needed at exec time (injected into the child env); url + token_env go into the
    codex MCP config."""
    url: str
    token_env: str
    token: str | None = None


def build_codex_args(
    *, prompt: str, workdir: str, model: str, sandbox_mode: str,
    bridge: BridgeWiring | None = None,
) -> list[str]:
    args = [
        "codex", "exec", prompt,
        "--cd", workdir,
        "--model", model,
        "--sandbox", sandbox_mode,
        "--json",
        "--skip-git-repo-check",
    ]
    if sandbox_mode == "danger-full-access":
        args.append("--dangerously-bypass-approvals-and-sandbox")
    # MCP config is delivered via ~/.codex/config.toml (written at startup), not
    # via -c args. codex 0.135 silently ignores -c mcp_servers.* overrides.
    return args


def _event_tool_call(item: dict) -> dict | None:
    """Map a tool/command/edit item to {name, ok}. codex exec emits several item
    types; we surface the ones that represent agent *actions* for chat activity.
    `command_execution` carries an exit_code; `file_change`/`mcp_tool_call`/
    `web_search` carry a `status` string instead. Status values are from observed
    codex 0.135 output ('completed'/'in_progress'); 'success' is allowed
    defensively. Absent status/exit_code => outcome unknown => treated as ok."""
    itype = item.get("type", "")
    if itype in ("command_execution", "tool_call", "function_call"):
        name = item.get("command") or item.get("name") or itype
        exit_code = item.get("exit_code")
        # absent exit_code (None) => command outcome unknown; treat as ok
        ok = exit_code in (None, 0)
        return {"name": str(name), "ok": ok}
    if itype == "file_change":
        changes = [c for c in (item.get("changes") or []) if isinstance(c, dict)]
        paths = [c.get("path", "?") for c in changes]
        # bound the name: a large multi-file edit would otherwise bloat chat/DB
        if len(paths) > 3:
            shown = ", ".join(paths[:3]) + f" (+{len(paths) - 3} more)"
        else:
            shown = ", ".join(paths) or "(files)"
        ok = item.get("status") in (None, "completed", "success")
        return {"name": f"edit {shown}", "ok": ok}
    if itype in ("mcp_tool_call", "web_search"):
        name = item.get("tool") or item.get("query") or item.get("name") or itype
        ok = item.get("status") in (None, "completed", "success")
        return {"name": str(name), "ok": ok}
    return None


def parse_codex_events(lines: Iterable[str]) -> CodexResult:
    """Parse `codex exec --json` JSONL. Last agent_message wins as final_response;
    command/tool items are surfaced as tool_calls. Tolerant of blank/garbage lines."""
    final = ""
    tool_calls: list[dict] = []
    for raw in lines:
        raw = raw.strip()
        if not raw:
            continue
        try:
            evt = json.loads(raw)
        except (ValueError, TypeError):
            continue
        if isinstance(evt, dict) and evt.get("type") == "item.started":
            continue
        item = evt.get("item") if isinstance(evt, dict) else None
        if not isinstance(item, dict):
            continue
        if item.get("type") == "agent_message":
            text = item.get("text")
            if isinstance(text, str):
                final = text
        tc = _event_tool_call(item)
        if tc is not None:
            tool_calls.append(tc)
    return CodexResult(final_response=final, tool_calls=tool_calls)


# ---------------------------------------------------------------------------
# Subprocess orchestration
# ---------------------------------------------------------------------------

# A sandboxed `codex`/node/git child needs more than PATH/HOME to work in prod
# (TLS trust, locale, temp dir, proxy, git identity, codex config home). We pass
# an ALLOWLIST rather than os.environ.copy() ON PURPOSE: the control-plane holds
# secrets (other API keys, DB URL, Fernet key, Zulip tokens) and codex executes
# model-generated shell commands, so copying the whole env would leak those into
# the sandbox. Add new keys here only when a concrete need is confirmed.
_ENV_PASSTHROUGH = (
    "PATH", "HOME", "USER", "LOGNAME",
    "TMPDIR", "TEMP", "TMP",
    "LANG", "LC_ALL", "LC_CTYPE",
    "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "NODE_EXTRA_CA_CERTS",
    "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY",
    "http_proxy", "https_proxy", "no_proxy",
    "XDG_CONFIG_HOME",
    "GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL",
    "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL",
)


def _minimal_env(api_key: str, bridge: BridgeWiring | None = None) -> dict[str, str]:
    # Inherit only the allowlisted vars that are actually present; inject the key.
    base = {k: os.environ[k] for k in _ENV_PASSTHROUGH if k in os.environ}
    base["CODEX_API_KEY"] = api_key
    if bridge is not None and bridge.token is not None:
        base[bridge.token_env] = bridge.token
    return base


async def _default_spawn(args: list[str], cwd: str, env: dict[str, str]):
    return await asyncio.create_subprocess_exec(
        *args, cwd=cwd, env=env,
        start_new_session=True,  # own process group -> killable as a unit
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )


def _write_agents_md(workdir: str, system_prompt: str) -> None:
    """Codex has no --system-prompt; it reads AGENTS.md as project instructions.
    Rewritten each turn so persona edits take effect."""
    if system_prompt:
        Path(workdir).mkdir(parents=True, exist_ok=True)
        (Path(workdir) / "AGENTS.md").write_text(system_prompt)


async def run_codex_exec(
    *,
    prompt: str,
    system_prompt: str,
    workdir: str,
    model: str,
    sandbox_mode: str,
    api_key: str,
    bridge: BridgeWiring | None = None,
    on_tool_call: Callable[[str, bool], Awaitable[None]] | None = None,
    spawn: Callable[..., Awaitable] = _default_spawn,
) -> CodexResult:
    _write_agents_md(workdir, system_prompt)
    args = build_codex_args(
        prompt=prompt, workdir=workdir, model=model, sandbox_mode=sandbox_mode,
        bridge=bridge,
    )
    proc = await spawn(args, cwd=workdir, env=_minimal_env(api_key, bridge))
    final = ""
    tool_calls: list[dict] = []
    rc = None
    try:
        async for raw in proc.stdout:
            line = raw.decode(errors="replace").strip()
            if not line:
                continue
            sub = parse_codex_events([line])
            if sub.final_response:
                final = sub.final_response
            for tc in sub.tool_calls:
                tool_calls.append(tc)
                if on_tool_call is not None:
                    await on_tool_call(tc["name"], tc["ok"])
        rc = await proc.wait()
    except asyncio.CancelledError:
        proc.kill()
        with contextlib.suppress(Exception):
            await proc.wait()  # reap the killed child so it doesn't linger
        raise
    return CodexResult(final_response=final, tool_calls=tool_calls, exit_code=rc)
