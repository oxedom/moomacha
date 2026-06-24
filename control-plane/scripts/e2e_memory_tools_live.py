"""Live e2e of the memory TOOLS (through ToolRuntime) against the real store.

Verifies, end to end: namespaced writes, read-set scoping (own + channel, no
sideways topic/other-agent reads), the librarian write gate, and episodic
records. Uses isolated probe namespaces. Run from control-plane/:
    uv run python scripts/e2e_memory_tools_live.py
"""
from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace

from control_plane.config import Settings
from control_plane.runtime.tools.agent_memory import AgentMemoryRest, register_agent_memory_tools
from control_plane.runtime.tools.registry import ToolRegistry
from control_plane.runtime.tools.runtime import ToolContext, ToolRuntime

TAG = uuid.uuid4().hex[:8]
OK = "\033[92m✓\033[0m"
NO = "\033[91m✗\033[0m"


def check(label: str, cond: bool) -> bool:
    print(f"  {OK if cond else NO} {label}")
    return cond


def _ctx(rt_ns: str, channel: str, topic: str, *, is_librarian=False):
    agent = SimpleNamespace(
        id=f"probe-{TAG}",
        is_librarian=is_librarian,
        allowed_tools=["remember", "record_episode", "search_long_term_memory"],
    )
    return ToolContext(agent=agent, zulip=None, channel=channel, topic=topic, memory_ns=rt_ns)


async def main() -> None:
    s = Settings()
    rest = AgentMemoryRest(
        endpoint=s.agent_memory_endpoint, store_id=s.agent_memory_store_id,
        api_key=s.agent_memory_api_key, timeout=s.agent_memory_timeout_s,
    )
    reg = ToolRegistry()
    register_agent_memory_tools(reg, rest)
    rt = ToolRuntime(reg)

    me = f"agent:archetype:probe-{TAG}"
    other = f"agent:archetype:other-{TAG}"
    chan = f"probechan-{TAG}"
    j = lambda **k: __import__("json").dumps(k)

    print(f"\nprobe tag={TAG}  my-ns={me}  channel={chan}\n")
    all_ok = True

    # Writes: my self-fact, a channel-fact (as librarian), and another agent's fact.
    await rt.execute("remember", j(text=f"SELF marker {TAG} apricot"), _ctx(me, chan, "T1"))
    await rt.execute("remember", j(text=f"CHAN marker {TAG} apricot", scope="channel"),
                     _ctx(me, chan, "T1", is_librarian=True))
    await rt.execute("remember", j(text=f"OTHER marker {TAG} apricot"), _ctx(other, chan, "T1"))
    # Another agent in a DIFFERENT channel (must be invisible to me).
    await rt.execute("remember", j(text=f"OFFCHAN marker {TAG} apricot"),
                     _ctx(other, f"elsewhere-{TAG}", "T9"))
    await asyncio.sleep(2.5)

    print("read-set scoping (search as me in my channel):")
    r = await rt.execute("search_long_term_memory", j(query=f"apricot {TAG}", limit=20), _ctx(me, chan, "T1"))
    c = r.content
    all_ok &= check("sees my self-tier write", "SELF marker" in c)
    all_ok &= check("sees this-channel write", "CHAN marker" in c)
    all_ok &= check("does NOT see another agent's self memory", "OTHER marker" not in c)
    all_ok &= check("does NOT see another channel's memory", "OFFCHAN marker" not in c)

    print("write gate:")
    gate = await rt.execute("remember", j(text="x", scope="channel"), _ctx(me, chan, "T1"))
    all_ok &= check("non-librarian channel write refused", gate.ok is False and "librarian" in gate.content.lower())
    libw = await rt.execute("remember", j(text=f"LIBW {TAG}", scope="channel"),
                            _ctx(me, chan, "T1", is_librarian=True))
    all_ok &= check("librarian channel write allowed", libw.ok)

    print("episodic:")
    ep = await rt.execute("record_episode", j(text=f"EP {TAG} apricot", event_date="2026-05-26"),
                          _ctx(me, chan, "T1"))
    all_ok &= check("record_episode accepted", ep.ok)

    print(f"\n{'ALL CHECKS PASSED' if all_ok else 'SOME CHECKS FAILED'}\n")
    raise SystemExit(0 if all_ok else 1)


if __name__ == "__main__":
    asyncio.run(main())
