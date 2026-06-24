# tests/test_codex_backend.py
from pathlib import Path

from control_plane.runtime.runners.codex_backend import (
    BridgeWiring, CodexResult, build_codex_args, parse_codex_events,
)

FIXTURE = Path(__file__).parent / "fixtures" / "codex_exec_events.jsonl"


def test_build_codex_args_basic():
    args = build_codex_args(
        prompt="do the thing", workdir="/ws", model="gpt-5.1-codex",
        sandbox_mode="workspace-write",
    )
    assert args[:2] == ["codex", "exec"]
    assert "do the thing" in args
    assert "--cd" in args and "/ws" in args
    assert "--model" in args and "gpt-5.1-codex" in args
    assert "--sandbox" in args and "workspace-write" in args
    assert "--json" in args
    # not full-access -> no bypass flag
    assert "--dangerously-bypass-approvals-and-sandbox" not in args


def test_build_codex_args_full_access_adds_bypass():
    args = build_codex_args(
        prompt="p", workdir="/ws", model="m", sandbox_mode="danger-full-access",
    )
    assert "--sandbox" in args and "danger-full-access" in args
    assert "--dangerously-bypass-approvals-and-sandbox" in args


def test_parse_codex_events_extracts_final_message_and_tools():
    lines = FIXTURE.read_text().splitlines()
    result = parse_codex_events(lines)
    assert isinstance(result, CodexResult)
    # last agent_message wins over the preamble message
    assert result.final_response == "PONG"
    # item.started is skipped, so exactly one file_change tool_call (the completed one)
    assert result.tool_calls == [{"name": "edit /ws/probe.txt", "ok": True}]


def test_parse_codex_events_file_change():
    lines = [
        '{"item": {"type": "file_change", "changes": [{"path": "a.py", "kind": "add"}, {"path": "b.py", "kind": "update"}], "status": "completed"}}',
        '{"item": {"type": "file_change", "changes": [{"path": "c.py", "kind": "delete"}], "status": "failed"}}',
    ]
    result = parse_codex_events(lines)
    assert result.tool_calls == [
        {"name": "edit a.py, b.py", "ok": True},
        {"name": "edit c.py", "ok": False},
    ]


def test_parse_codex_events_file_change_caps_long_path_list():
    item = '{"item": {"type": "file_change", "changes": [{"path": "a"}, {"path": "b"}, {"path": "c"}, {"path": "d"}, {"path": "e"}], "status": "completed"}}'
    result = parse_codex_events([item])
    assert result.tool_calls == [{"name": "edit a, b, c (+2 more)", "ok": True}]


def test_parse_codex_events_mcp_and_web_search():
    lines = [
        '{"item": {"type": "mcp_tool_call", "tool": "tavily_search", "status": "completed"}}',
        '{"item": {"type": "web_search", "query": "weather", "status": "success"}}',
        '{"item": {"type": "web_search", "query": "boom", "status": "failed"}}',
    ]
    result = parse_codex_events(lines)
    assert result.tool_calls == [
        {"name": "tavily_search", "ok": True},
        {"name": "weather", "ok": True},
        {"name": "boom", "ok": False},
    ]


def test_parse_codex_events_tolerates_blank_and_garbage_lines():
    lines = ["", "   ", "not json", '{"type": "item.completed", "item": {"type": "agent_message", "text": "ok"}}']
    result = parse_codex_events(lines)
    assert result.final_response == "ok"


def test_parse_codex_events_empty_stream():
    result = parse_codex_events([])
    assert result.final_response == ""
    assert result.tool_calls == []


def test_parse_codex_events_skips_started_but_keeps_bare_and_completed():
    lines = [
        '{"type": "item.started", "item": {"type": "command_execution", "command": "echo hi"}}',
        '{"item": {"type": "command_execution", "command": "echo hi", "exit_code": 0}}',
        '{"type": "item.completed", "item": {"type": "agent_message", "text": "done"}}',
    ]
    result = parse_codex_events(lines)
    # started skipped; bare command_execution still surfaced
    assert result.tool_calls == [{"name": "echo hi", "ok": True}]
    assert result.final_response == "done"


# ---------------------------------------------------------------------------
# Task 4: run_codex_exec (subprocess orchestration)
# ---------------------------------------------------------------------------
import asyncio
import pytest

from control_plane.runtime.runners.codex_backend import run_codex_exec


class _FakeProc:
    """Minimal stand-in for an asyncio subprocess emitting canned stdout lines."""
    def __init__(self, lines, *, hang=False):
        self._lines = [(l + "\n").encode() for l in lines]
        self._hang = hang
        self.returncode = 0
        self.killed = False
        self.stdout = self  # we implement __aiter__ below

    def __aiter__(self):
        async def gen():
            for b in self._lines:
                yield b
            if self._hang:
                await asyncio.sleep(3600)
        return gen()

    async def wait(self):
        if self._hang and not self.killed:
            await asyncio.sleep(3600)
        return self.returncode

    def kill(self):
        self.killed = True


@pytest.mark.asyncio
async def test_run_codex_exec_returns_final_and_fires_callback(tmp_path):
    fired = []

    async def on_tool_call(name, ok):
        fired.append((name, ok))

    lines = [
        '{"item": {"type": "command_execution", "command": "echo hi", "exit_code": 0}}',
        '{"item": {"type": "agent_message", "text": "DONE"}}',
    ]
    captured = {}

    async def fake_spawn(args, cwd, env):
        captured["args"] = args
        captured["env"] = env
        return _FakeProc(lines)

    result = await run_codex_exec(
        prompt="p", system_prompt="sys", workdir=str(tmp_path), model="m",
        sandbox_mode="workspace-write", api_key="sk-test",
        on_tool_call=on_tool_call, spawn=fake_spawn,
    )
    assert result.final_response == "DONE"
    assert result.exit_code == 0
    assert fired == [("echo hi", True)]
    assert captured["env"]["CODEX_API_KEY"] == "sk-test"
    assert captured["args"][:2] == ["codex", "exec"]


@pytest.mark.asyncio
async def test_run_codex_exec_kills_child_on_cancel(tmp_path):
    proc = _FakeProc([], hang=True)

    async def fake_spawn(args, cwd, env):
        return proc

    task = asyncio.create_task(run_codex_exec(
        prompt="p", system_prompt="", workdir=str(tmp_path), model="m",
        sandbox_mode="workspace-write", api_key="k", spawn=fake_spawn,
    ))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert proc.killed is True


@pytest.mark.asyncio
async def test_minimal_env_is_allowlist_not_full_copy(monkeypatch):
    from control_plane.runtime.runners.codex_backend import _minimal_env
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setenv("NEON_DATABASE_URL", "postgres://secret")  # must NOT leak
    env = _minimal_env("sk-abc")
    assert env["CODEX_API_KEY"] == "sk-abc"
    assert env["PATH"] == "/usr/bin"
    assert "NEON_DATABASE_URL" not in env


# ---------------------------------------------------------------------------
# Task 3: BridgeWiring — MCP bridge injection
# ---------------------------------------------------------------------------

def test_build_codex_args_bridge_does_not_add_c_args():
    # MCP config is delivered via ~/.codex/config.toml (written at startup),
    # not via -c args. codex 0.135 silently ignores -c mcp_servers.* overrides.
    args = build_codex_args(
        prompt="p", workdir="/ws", model="m", sandbox_mode="danger-full-access",
        bridge=BridgeWiring(url="http://127.0.0.1:9110/mcp/", token_env="CP_BRIDGE_TOKEN"),
    )
    assert "-c" not in args
    assert not any("mcp_servers" in a for a in args)


def test_build_codex_args_no_bridge_omits_mcp_config():
    args = build_codex_args(prompt="p", workdir="/ws", model="m", sandbox_mode="workspace-write")
    assert not any(a.startswith("mcp_servers.cp") for a in args)


@pytest.mark.asyncio
async def test_run_codex_exec_injects_bridge_token_env(tmp_path):
    captured = {}

    async def fake_spawn(args, cwd, env):
        captured["env"] = env
        captured["args"] = args
        return _FakeProc(['{"item": {"type": "agent_message", "text": "ok"}}'])

    await run_codex_exec(
        prompt="p", system_prompt="", workdir=str(tmp_path), model="m",
        sandbox_mode="danger-full-access", api_key="sk",
        bridge=BridgeWiring(url="http://127.0.0.1:9110/mcp", token_env="CP_BRIDGE_TOKEN", token="tok-123"),
        spawn=fake_spawn,
    )
    assert captured["env"]["CP_BRIDGE_TOKEN"] == "tok-123"
    # MCP config is now delivered via config.toml, not -c args
    assert "-c" not in captured["args"]


@pytest.mark.asyncio
async def test_run_codex_exec_bridge_without_token_does_not_inject_env(tmp_path):
    captured = {}

    async def fake_spawn(args, cwd, env):
        captured["env"] = env
        return _FakeProc(['{"item": {"type": "agent_message", "text": "ok"}}'])

    await run_codex_exec(
        prompt="p", system_prompt="", workdir=str(tmp_path), model="m",
        sandbox_mode="danger-full-access", api_key="sk",
        bridge=BridgeWiring(url="http://127.0.0.1:9110/mcp", token_env="CP_BRIDGE_TOKEN"),
        spawn=fake_spawn,
    )
    assert "CP_BRIDGE_TOKEN" not in captured["env"]
