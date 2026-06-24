# src/control_plane/runtime/runners/codex_workspace.py
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from pathlib import Path

_SLUG_RE = re.compile(r"[^a-z0-9._-]+")


def slug(value: str) -> str:
    """Turn an arbitrary channel/topic name into a single safe path segment.
    Lowercased; runs of unsafe chars collapse to '-'; leading dots stripped so
    no traversal ('..') or hidden-dir escapes survive. Empty -> '_'."""
    s = _SLUG_RE.sub("-", value.strip().lower()).strip("-.")
    return s or "_"


async def _run(*args: str, cwd: Path) -> None:
    proc = await asyncio.create_subprocess_exec(
        *args, cwd=str(cwd),
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(
            f"{args[0]} exited {proc.returncode}: {stderr.decode(errors='replace')[:200]}"
        )


@dataclass
class WorkspaceManager:
    """Resolves a persistent per-topic workspace at <root>/<channel>/<topic>/.
    Created once as a git repo so (a) Codex is happy and (b) each turn's changes
    are a diff against the prior commit (built-in audit trail)."""

    root: Path
    _locks: dict[str, asyncio.Lock] = field(default_factory=dict, repr=False, compare=False)

    async def ensure(self, channel: str, topic: str) -> str:
        path = (Path(self.root) / slug(channel) / slug(topic)).resolve()
        if not (path / ".git").is_dir():
            path.mkdir(parents=True, exist_ok=True)
            (path / ".gitignore").write_text("# codex workspace\n")
            await _run("git", "init", "-q", cwd=path)
        return str(path)

    def lock(self, path: str) -> asyncio.Lock:
        """Return a process-wide asyncio.Lock for this workspace path, created on
        first use. Serializes concurrent turns that resolve to the same workspace
        so two `codex exec` runs never share one git working tree."""
        return self._locks.setdefault(path, asyncio.Lock())
