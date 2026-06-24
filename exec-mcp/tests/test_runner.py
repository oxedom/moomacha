import asyncio
import os

import pytest

from exec_mcp.runner import run_command, scrubbed_env

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def test_run_command_success():
    res = await run_command("echo hello", repo_dir=REPO)
    assert res["exit_code"] == 0
    assert res["stdout"].strip() == "hello"
    assert res["timed_out"] is False


async def test_run_command_nonzero_exit():
    res = await run_command("exit 3", repo_dir=REPO)
    assert res["exit_code"] == 3
    assert res["timed_out"] is False


async def test_run_command_runs_in_repo_dir():
    res = await run_command("pwd", repo_dir=REPO)
    assert res["stdout"].strip() == REPO


async def test_run_command_timeout_is_killed():
    res = await run_command("sleep 5", repo_dir=REPO, timeout_s=0.5)
    assert res["timed_out"] is True


async def test_run_command_caps_output():
    res = await run_command("for i in $(seq 1 1000); do echo line$i; done", repo_dir=REPO, output_cap=200)
    assert "truncated to 200 characters" in res["stdout"]


async def test_env_is_scrubbed_of_parent_secrets(monkeypatch):
    monkeypatch.setenv("SUPER_SECRET_KEY", "leakme-123")
    # Not in the passthrough set -> must be absent in the child.
    assert "SUPER_SECRET_KEY" not in scrubbed_env()
    res = await run_command("echo [$SUPER_SECRET_KEY]", repo_dir=REPO)
    assert res["stdout"].strip() == "[]"
    assert "leakme-123" not in res["stdout"]
