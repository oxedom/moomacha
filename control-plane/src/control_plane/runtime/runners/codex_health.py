# src/control_plane/runtime/runners/codex_health.py
from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)


async def _default_spawn(*args: str, **kw: Any):
    return await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )


async def codex_available(spawn: Callable[..., Any] = _default_spawn) -> bool:
    """True if `codex --version` runs and exits 0. Logs a warning otherwise."""
    try:
        proc = await spawn("codex", "--version")
        await proc.communicate()
        return proc.returncode == 0
    except (FileNotFoundError, OSError) as exc:
        logger.warning("codex binary not available: %s (codex runtime will fail)", exc)
        return False
