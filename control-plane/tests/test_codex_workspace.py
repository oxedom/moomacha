# tests/test_codex_workspace.py
from pathlib import Path

import pytest

from control_plane.runtime.runners.codex_workspace import WorkspaceManager, slug


def test_slug_sanitizes_and_blocks_traversal():
    assert slug("dev-team") == "dev-team"
    assert slug("Hello World") == "hello-world"
    # path traversal / separators must not survive
    assert "/" not in slug("../etc/passwd")
    assert ".." not in slug("../../x")
    assert slug("") == "_"


@pytest.mark.asyncio
async def test_ensure_creates_git_repo(tmp_path):
    wm = WorkspaceManager(root=tmp_path)
    path = await wm.ensure("dev", "my topic")
    p = Path(path)
    assert p.is_dir()
    assert p == tmp_path / "dev" / "my-topic"
    assert (p / ".git").is_dir()
    assert (p / ".gitignore").is_file()


@pytest.mark.asyncio
async def test_ensure_is_idempotent(tmp_path):
    wm = WorkspaceManager(root=tmp_path)
    first = await wm.ensure("dev", "t")
    # drop a file; second ensure must NOT re-init or wipe it
    marker = Path(first) / "marker.txt"
    marker.write_text("keep me")
    second = await wm.ensure("dev", "t")
    assert first == second
    assert marker.read_text() == "keep me"


@pytest.mark.asyncio
async def test_run_raises_on_nonzero_exit(tmp_path):
    from control_plane.runtime.runners.codex_workspace import _run
    with pytest.raises(RuntimeError):
        # `git` with a bogus subcommand exits non-zero
        await _run("git", "definitely-not-a-real-subcommand", cwd=tmp_path)


@pytest.mark.asyncio
async def test_lock_is_same_object_per_path_and_distinct_across_paths(tmp_path):
    wm = WorkspaceManager(root=tmp_path)
    a1 = wm.lock("/ws/a")
    a2 = wm.lock("/ws/a")
    b = wm.lock("/ws/b")
    assert a1 is a2          # memoized per path
    assert a1 is not b       # distinct per path
