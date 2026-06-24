# tests/test_codex_runner.py
import asyncio
import uuid
from types import SimpleNamespace

import pytest

from control_plane.runtime.runners.base import RunnerInput
from control_plane.runtime.runners.codex_runner import CodexConfig, CodexRunner
from control_plane.services.job_queue import Job


class _FakeAgent:
    def __init__(self, **kw):
        self.id = kw.get("id", uuid.uuid4())
        self.name = kw.get("name", "worker")
        self.model_id = kw.get("model_id", "gpt-4o")
        self.runtime_config = kw.get("runtime_config", {})


class _FakeWorkspaces:
    def __init__(self):
        self.calls = []
        self._locks = {}

    async def ensure(self, channel, topic):
        self.calls.append((channel, topic))
        return f"/ws/{channel}/{topic}"

    def lock(self, path):
        return self._locks.setdefault(path, asyncio.Lock())


class _TmpWorkspaces:
    def __init__(self, path):
        self.path = str(path)
        self._locks = {}

    async def ensure(self, channel, topic):
        return self.path

    def lock(self, path):
        return self._locks.setdefault(path, asyncio.Lock())


class _FakeSkillCatalog:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    async def load(self, *, names, model_era):
        self.calls.append((names, model_era))
        return [row for row in self.rows if row.name in names]


def _job():
    return Job(agent_id=uuid.uuid4(), channel="dev", topic="t", content="build a script")


def test_codex_config_defaults_and_validation():
    cfg = CodexConfig.from_runtime_config({})
    assert cfg.sandbox_mode is None
    assert cfg.model is None
    with pytest.raises(ValueError):
        CodexConfig.from_runtime_config({"codex": {"sandbox_mode": "bogus"}})


@pytest.mark.asyncio
async def test_runner_passes_through_and_returns_final():
    captured = {}

    async def fake_exec(**kw):
        captured.update(kw)
        from control_plane.runtime.runners.codex_backend import CodexResult
        return CodexResult(final_response="all done", tool_calls=[])

    ws = _FakeWorkspaces()
    runner = CodexRunner(workspaces=ws, openai_key="sk-xyz", exec_codex=fake_exec)
    agent = _FakeAgent(runtime_config={"codex": {"sandbox_mode": "danger-full-access",
                                                 "model": "gpt-5.1-codex"}})
    out = await runner.run(RunnerInput(
        job=_job(), agent=agent, system_prompt="sys",
        user_message="build a script", tool_context=None,
    ))
    assert out == "all done"
    assert ws.calls == [("dev", "t")]
    assert captured["workdir"] == "/ws/dev/t"
    assert captured["model"] == "gpt-5.1-codex"        # runtime_config model wins, bare
    assert captured["sandbox_mode"] == "danger-full-access"
    assert captured["api_key"] == "sk-xyz"
    assert captured["prompt"] == "build a script"
    assert "on_tool_call" in captured


@pytest.mark.asyncio
async def test_runner_falls_back_to_agent_model():
    captured = {}

    async def capturing_exec(**kw):
        captured.update(kw)
        from control_plane.runtime.runners.codex_backend import CodexResult
        return CodexResult(final_response="x")

    runner = CodexRunner(workspaces=_FakeWorkspaces(), openai_key="k",
                         exec_codex=capturing_exec)
    agent = _FakeAgent(model_id="gpt-4o", runtime_config={})
    await runner.run(RunnerInput(
        job=_job(), agent=agent, system_prompt="s",
        user_message="m", tool_context=None,
    ))
    assert captured["model"] == "gpt-4o"


@pytest.mark.asyncio
async def test_runner_strips_openai_prefix_for_codex():
    captured = {}

    async def capturing_exec(**kw):
        captured.update(kw)
        from control_plane.runtime.runners.codex_backend import CodexResult
        return CodexResult(final_response="x")

    runner = CodexRunner(workspaces=_FakeWorkspaces(), openai_key="k",
                         exec_codex=capturing_exec)
    # an agent stored a LangChain-style prefixed id; codex needs it bare
    agent = _FakeAgent(model_id="openai:gpt-4o", runtime_config={})
    await runner.run(RunnerInput(
        job=_job(), agent=agent, system_prompt="s",
        user_message="m", tool_context=None,
    ))
    assert captured["model"] == "gpt-4o"


def test_codex_config_rejects_unknown_keys():
    with pytest.raises(ValueError):
        CodexConfig.from_runtime_config({"codex": {"mpc_servers": []}})  # typo of mcp_servers


@pytest.mark.asyncio
async def test_same_topic_turns_are_serialized():
    ws = _FakeWorkspaces()
    active = 0
    max_concurrent = 0
    release = asyncio.Event()

    async def slow_exec(**kw):
        nonlocal active, max_concurrent
        active += 1
        max_concurrent = max(max_concurrent, active)
        await release.wait()   # hold the workspace
        active -= 1
        from control_plane.runtime.runners.codex_backend import CodexResult
        return CodexResult(final_response="ok")

    runner = CodexRunner(workspaces=ws, openai_key="k", exec_codex=slow_exec)
    agent = _FakeAgent(runtime_config={})

    def _mk():
        return runner.run(RunnerInput(
            job=Job(agent_id=uuid.uuid4(), channel="dev", topic="t", content="c"),
            agent=agent, system_prompt="s", user_message="m", tool_context=None,
        ))

    t1 = asyncio.create_task(_mk())
    t2 = asyncio.create_task(_mk())
    await asyncio.sleep(0.05)   # let both tasks reach the lock
    assert active == 1          # only ONE got past the lock into exec
    assert max_concurrent == 1
    release.set()
    await asyncio.gather(t1, t2)
    assert max_concurrent == 1  # never overlapped


@pytest.mark.asyncio
async def test_runner_uses_default_sandbox_when_agent_unset():
    captured = {}

    async def cap(**kw):
        captured.update(kw)
        from control_plane.runtime.runners.codex_backend import CodexResult
        return CodexResult(final_response="x")

    runner = CodexRunner(workspaces=_FakeWorkspaces(), openai_key="k",
                         exec_codex=cap, default_sandbox_mode="danger-full-access")
    agent = _FakeAgent(runtime_config={})  # no sandbox_mode
    await runner.run(RunnerInput(
        job=_job(), agent=agent, system_prompt="s",
        user_message="m", tool_context=None,
    ))
    assert captured["sandbox_mode"] == "danger-full-access"


@pytest.mark.asyncio
async def test_runner_agent_sandbox_overrides_default():
    captured = {}

    async def cap(**kw):
        captured.update(kw)
        from control_plane.runtime.runners.codex_backend import CodexResult
        return CodexResult(final_response="x")

    runner = CodexRunner(workspaces=_FakeWorkspaces(), openai_key="k",
                         exec_codex=cap, default_sandbox_mode="danger-full-access")
    agent = _FakeAgent(runtime_config={"codex": {"sandbox_mode": "read-only"}})
    await runner.run(RunnerInput(
        job=_job(), agent=agent, system_prompt="s",
        user_message="m", tool_context=None,
    ))
    assert captured["sandbox_mode"] == "read-only"  # explicit agent value wins


def test_runner_rejects_invalid_default_sandbox():
    with pytest.raises(ValueError):
        CodexRunner(workspaces=_FakeWorkspaces(), openai_key="k",
                    default_sandbox_mode="bogus")


@pytest.mark.asyncio
async def test_different_topics_run_concurrently():
    ws = _FakeWorkspaces()
    active = 0
    max_concurrent = 0
    release = asyncio.Event()

    async def slow_exec(**kw):
        nonlocal active, max_concurrent
        active += 1
        max_concurrent = max(max_concurrent, active)
        await release.wait()
        active -= 1
        from control_plane.runtime.runners.codex_backend import CodexResult
        return CodexResult(final_response="ok")

    runner = CodexRunner(workspaces=ws, openai_key="k", exec_codex=slow_exec)
    agent = _FakeAgent(runtime_config={})

    def _mk(topic):
        return runner.run(RunnerInput(
            job=Job(agent_id=uuid.uuid4(), channel="dev", topic=topic, content="c"),
            agent=agent, system_prompt="s", user_message="m", tool_context=None,
        ))

    t1 = asyncio.create_task(_mk("t1"))
    t2 = asyncio.create_task(_mk("t2"))
    await asyncio.sleep(0.05)
    assert max_concurrent == 2   # different workspaces -> concurrent
    release.set()
    await asyncio.gather(t1, t2)


def test_codex_config_expose_tools_defaults_false_and_parses():
    assert CodexConfig.from_runtime_config({}).expose_tools is False
    cfg = CodexConfig.from_runtime_config({"codex": {"expose_tools": True}})
    assert cfg.expose_tools is True


def test_codex_config_parses_skills():
    cfg = CodexConfig.from_runtime_config({"codex": {"skills": ["/skills/briefings/"]}})
    assert cfg.skills == ["/skills/briefings/"]


class _SpyBridge:
    def __init__(self):
        self.minted = []
        self.released = []
        self._n = 0

    def mint(self, *, ctx, runtime, allowed):
        self._n += 1
        tok = f"tok-{self._n}"
        self.minted.append((tok, ctx, runtime, allowed))
        return tok

    def release(self, token):
        self.released.append(token)


def _agent_with_tools(**kw):
    a = _FakeAgent(**kw)
    a.allowed_tools = kw.get("allowed_tools", ["read_topic"])
    return a


@pytest.mark.asyncio
async def test_runner_mints_and_passes_bridge_when_expose_tools():
    captured = {}

    async def fake_exec(**kw):
        captured.update(kw)
        from control_plane.runtime.runners.codex_backend import CodexResult
        return CodexResult(final_response="ok")

    bridge = _SpyBridge()
    runner = CodexRunner(
        workspaces=_FakeWorkspaces(), openai_key="k", exec_codex=fake_exec,
        tool_bridge=bridge, tool_runtime="RT", bridge_url="http://127.0.0.1:9110/mcp",
    )
    agent = _agent_with_tools(runtime_config={"codex": {"expose_tools": True}})
    await runner.run(RunnerInput(
        job=_job(), agent=agent, system_prompt="s", user_message="m", tool_context="CTX",
    ))
    assert bridge.minted and bridge.minted[0][1] == "CTX" and bridge.minted[0][3] == ["read_topic"]
    assert captured["bridge"].url == "http://127.0.0.1:9110/mcp"
    assert captured["bridge"].token == "tok-1"
    assert bridge.released == ["tok-1"]


@pytest.mark.asyncio
async def test_runner_no_bridge_when_expose_tools_false():
    captured = {}

    async def fake_exec(**kw):
        captured.update(kw)
        from control_plane.runtime.runners.codex_backend import CodexResult
        return CodexResult(final_response="ok")

    bridge = _SpyBridge()
    runner = CodexRunner(
        workspaces=_FakeWorkspaces(), openai_key="k", exec_codex=fake_exec,
        tool_bridge=bridge, tool_runtime="RT", bridge_url="http://x/mcp",
    )
    agent = _agent_with_tools(runtime_config={})
    await runner.run(RunnerInput(
        job=_job(), agent=agent, system_prompt="s", user_message="m", tool_context="CTX",
    ))
    assert bridge.minted == []
    assert captured["bridge"] is None


@pytest.mark.asyncio
async def test_runner_releases_token_even_when_exec_raises():
    bridge = _SpyBridge()

    async def boom_exec(**kw):
        # at exec time the token is minted but NOT yet released — release must
        # happen in the runner's finally, AFTER (well, despite) this raise.
        assert bridge.released == []
        assert kw["bridge"].token == "tok-1"
        raise RuntimeError("codex blew up")

    runner = CodexRunner(
        workspaces=_FakeWorkspaces(), openai_key="k", exec_codex=boom_exec,
        tool_bridge=bridge, tool_runtime="RT", bridge_url="http://x/mcp",
    )
    agent = _agent_with_tools(runtime_config={"codex": {"expose_tools": True}})
    with pytest.raises(RuntimeError):
        await runner.run(RunnerInput(
            job=_job(), agent=agent, system_prompt="s", user_message="m", tool_context="CTX",
        ))
    assert bridge.released == ["tok-1"]  # finally released despite the raise


@pytest.mark.asyncio
async def test_runner_mounts_db_skills_as_codex_repo_skill_files(tmp_path):
    body = "---\nname: briefings\ndescription: Prepare a briefing.\n---\n\nDo briefing work."
    catalog = _FakeSkillCatalog([SimpleNamespace(name="briefings", body=body)])

    async def fake_exec(**kw):
        from control_plane.runtime.runners.codex_backend import CodexResult

        skill = tmp_path / ".agents" / "skills" / "briefings" / "SKILL.md"
        assert skill.read_text(encoding="utf-8") == body
        return CodexResult(final_response="ok")

    runner = CodexRunner(
        workspaces=_TmpWorkspaces(tmp_path),
        openai_key="k",
        exec_codex=fake_exec,
        skill_catalog=catalog,
    )
    agent = _FakeAgent(
        model_id="gpt-5.1-codex",
        runtime_config={"codex": {"skills": ["/skills/briefings/"]}},
    )

    await runner.run(RunnerInput(
        job=_job(), agent=agent, system_prompt="s", user_message="m", tool_context=None,
    ))

    assert catalog.calls == [(["briefings"], "gpt-5.1-codex")]
    assert (tmp_path / ".agents" / "skills" / "briefings" / ".control-plane-db-skill").is_file()


@pytest.mark.asyncio
async def test_runner_removes_stale_db_skill_mounts_but_keeps_unmanaged(tmp_path):
    root = tmp_path / ".agents" / "skills"
    stale = root / "old"
    unmanaged = root / "custom"
    stale.mkdir(parents=True)
    unmanaged.mkdir(parents=True)
    (stale / ".control-plane-db-skill").write_text("managed\n", encoding="utf-8")
    (stale / "SKILL.md").write_text("old", encoding="utf-8")
    (unmanaged / "SKILL.md").write_text("custom", encoding="utf-8")

    async def fake_exec(**kw):
        from control_plane.runtime.runners.codex_backend import CodexResult
        return CodexResult(final_response="ok")

    runner = CodexRunner(
        workspaces=_TmpWorkspaces(tmp_path),
        openai_key="k",
        exec_codex=fake_exec,
        skill_catalog=_FakeSkillCatalog([]),
    )
    await runner.run(RunnerInput(
        job=_job(), agent=_FakeAgent(runtime_config={}), system_prompt="s",
        user_message="m", tool_context=None,
    ))

    assert not stale.exists()
    assert (unmanaged / "SKILL.md").is_file()


@pytest.mark.asyncio
async def test_runner_refuses_to_overwrite_unmanaged_skill_folder(tmp_path):
    root = tmp_path / ".agents" / "skills" / "briefings"
    root.mkdir(parents=True)
    (root / "SKILL.md").write_text("custom", encoding="utf-8")

    async def fake_exec(**kw):  # pragma: no cover - should not be reached
        raise AssertionError("exec should not run")

    runner = CodexRunner(
        workspaces=_TmpWorkspaces(tmp_path),
        openai_key="k",
        exec_codex=fake_exec,
        skill_catalog=_FakeSkillCatalog([SimpleNamespace(name="briefings", body="db")]),
    )
    agent = _FakeAgent(runtime_config={"codex": {"skills": ["briefings"]}})

    with pytest.raises(RuntimeError, match="refusing to overwrite unmanaged"):
        await runner.run(RunnerInput(
            job=_job(), agent=agent, system_prompt="s", user_message="m", tool_context=None,
        ))


def test_codex_config_accepts_tripwire_flag():
    from control_plane.runtime.runners.codex_runner import CodexConfig

    cfg = CodexConfig.from_runtime_config({"codex": {"tripwire": True}})
    assert cfg.tripwire is True


def test_codex_config_tripwire_defaults_false():
    from control_plane.runtime.runners.codex_runner import CodexConfig

    cfg = CodexConfig.from_runtime_config({"codex": {}})
    assert cfg.tripwire is False
