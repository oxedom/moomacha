"""Pure, testable command runner for the exec-mcp service.

Runs a shell command in a fixed repo directory with a *scrubbed* environment
(built from scratch, never inheriting the parent process env), a hard timeout,
and bounded output. This is the only place that touches a subprocess; the MCP
server (server.py) is a thin transport around it.
"""

from __future__ import annotations

import asyncio
import os

# The only environment variables passed through to commands. Anything else in the
# launching environment (API keys, tokens, DB URLs) is intentionally dropped so a
# command cannot read a secret that happened to be exported where the service runs.
_PASSTHROUGH_ENV = ("PATH", "HOME", "LANG", "LC_ALL", "TERM", "USER", "SHELL")
_DEFAULT_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"


def scrubbed_env() -> dict[str, str]:
    env: dict[str, str] = {}
    for key in _PASSTHROUGH_ENV:
        value = os.environ.get(key)
        if value is not None:
            env[key] = value
    env.setdefault("PATH", _DEFAULT_PATH)
    return env


def _cap(raw: bytes, limit: int) -> str:
    text = raw.decode("utf-8", "replace")
    if len(text) > limit:
        return text[:limit] + f"\n... truncated to {limit} characters ..."
    return text


async def run_command(
    command: str,
    *,
    repo_dir: str,
    timeout_s: float = 60.0,
    output_cap: int = 4000,
) -> dict:
    """Run ``command`` via ``bash -c`` in ``repo_dir`` with a scrubbed env.

    Uses ``bash -c`` (not ``-lc``) so login profiles can neither pollute stdout
    nor re-introduce a scrubbed-out secret; ``PATH`` is carried through explicitly
    so normal tooling still resolves.

    Returns ``{exit_code, stdout, stderr, timed_out}``. Never raises for command
    failure or timeout (those are reported in the result); only truly unexpected
    errors (e.g. bash missing) propagate.
    """
    proc = await asyncio.create_subprocess_exec(
        "bash",
        "-c",
        command,
        cwd=repo_dir,
        env=scrubbed_env(),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    timed_out = False
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
    except asyncio.TimeoutError:
        timed_out = True
        proc.kill()
        stdout, stderr = await proc.communicate()
    return {
        "exit_code": proc.returncode,
        "stdout": _cap(stdout, output_cap),
        "stderr": _cap(stderr, output_cap),
        "timed_out": timed_out,
    }
