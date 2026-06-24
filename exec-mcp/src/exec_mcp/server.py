"""MCP (SSE) server exposing a single scoped ``run_command`` tool.

Thin transport around ``runner.run_command``. Binds loopback by default and
requires a bearer token (defense in depth behind the localhost bind). All config
comes from the environment so the service is launched independently of the
control plane and never shares its secrets.

Run: ``cd exec-mcp && uv run python -m exec_mcp.server``
"""

from __future__ import annotations

import json
import logging
import os

import uvicorn
from mcp.server.fastmcp import FastMCP
from starlette.responses import JSONResponse

from exec_mcp.runner import run_command

logger = logging.getLogger("exec_mcp")

REPO_DIR = os.environ.get("EXEC_MCP_REPO_DIR", os.getcwd())
TIMEOUT_S = float(os.environ.get("EXEC_MCP_TIMEOUT_S", "60"))
OUTPUT_CAP = int(os.environ.get("EXEC_MCP_OUTPUT_CAP", "4000"))
TOKEN = os.environ.get("EXEC_MCP_TOKEN")
HOST = os.environ.get("EXEC_MCP_HOST", "127.0.0.1")
PORT = int(os.environ.get("EXEC_MCP_PORT", "9100"))

mcp = FastMCP("exec", host=HOST, port=PORT)


@mcp.tool(name="run_command")
async def run_command_tool(command: str) -> str:
    """Run a shell command in the service's repo directory. Returns a JSON object
    {exit_code, stdout, stderr, timed_out}."""
    result = await run_command(
        command, repo_dir=REPO_DIR, timeout_s=TIMEOUT_S, output_cap=OUTPUT_CAP
    )
    return json.dumps(result)


class BearerAuthMiddleware:
    """Pure-ASGI bearer auth.

    Implemented at the ASGI layer rather than as a ``BaseHTTPMiddleware`` because
    that base buffers the response and is incompatible with the SSE *streaming*
    responses this server serves: on a streaming response it raises
    ``AssertionError: Unexpected message: {'type': 'http.response.start', ...}``,
    which silently breaks the MCP SSE handshake and hangs every client turn.
    A pass-through ASGI middleware leaves the streaming body untouched.
    """

    def __init__(self, app, token: str) -> None:
        self.app = app
        self._expected = f"Bearer {token}"

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "http":
            headers = dict(scope.get("headers") or [])
            if headers.get(b"authorization", b"").decode() != self._expected:
                await JSONResponse({"error": "unauthorized"}, status_code=401)(
                    scope, receive, send
                )
                return
        await self.app(scope, receive, send)


def build_app():
    app = mcp.sse_app()
    if TOKEN:
        # Starlette 1.1 add_middleware() only updates user_middleware but does NOT
        # force-rebuild the middleware_stack, so the new middleware is silently
        # missing from the live stack. Wrap explicitly to guarantee it is outermost.
        return BearerAuthMiddleware(app, token=TOKEN)
    return app


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    if HOST not in ("127.0.0.1", "localhost", "::1"):
        logger.warning(
            "EXEC_MCP_HOST=%s is not loopback; the exec service should bind localhost only", HOST
        )
    if not TOKEN:
        logger.warning("EXEC_MCP_TOKEN is unset; serving WITHOUT auth (localhost bind only)")
    logger.info("exec-mcp serving on %s:%d (repo=%s)", HOST, PORT, REPO_DIR)
    uvicorn.run(build_app(), host=HOST, port=PORT, log_level="info")


if __name__ == "__main__":
    main()
