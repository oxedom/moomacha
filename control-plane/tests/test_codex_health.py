# tests/test_codex_health.py
import pytest

from control_plane.runtime.runners.codex_health import codex_available


@pytest.mark.asyncio
async def test_codex_available_true_when_version_succeeds():
    async def fake_spawn(*args, **kw):
        class _P:
            returncode = 0
            async def communicate(self):
                return (b"codex 0.135.0\n", b"")
        return _P()
    assert await codex_available(spawn=fake_spawn) is True


@pytest.mark.asyncio
async def test_codex_available_false_when_missing():
    async def fake_spawn(*args, **kw):
        raise FileNotFoundError("codex")
    assert await codex_available(spawn=fake_spawn) is False
