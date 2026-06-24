from types import SimpleNamespace

from control_plane.runtime.tools.registry import ToolRegistry
from control_plane.runtime.tools.runtime import ToolContext, ToolRuntime
from control_plane.tools.management.adapters import register_management_tools

_ALL = [
    "list_agents", "get_agent", "create_agent", "update_agent",
    "enable_agent", "disable_agent", "provision_bot", "attach_bot",
    "set_bot_avatar", "delete_agent",
]


def _registry() -> ToolRegistry:
    reg = ToolRegistry()
    register_management_tools(reg)
    return reg


def test_all_management_tools_registered_and_tagged():
    reg = _registry()
    for name in _ALL:
        entry = reg.get(name)
        assert entry is not None, name
        assert entry.management is True, name


def test_management_schemas_only_exposed_to_bastion():
    reg = _registry()
    assert reg.build_schemas([], is_bastion=False) == []
    names = {s["function"]["name"] for s in reg.build_schemas([], is_bastion=True)}
    assert names == set(_ALL)


async def test_adapter_blocks_without_management_context():
    reg = _registry()
    runtime = ToolRuntime(reg)
    agent = SimpleNamespace(allowed_tools=[], readable_channels=[], is_bastion=True)
    ctx = ToolContext(agent=agent, zulip=None, channel="c", topic="t")  # management=None

    result = await runtime.execute("list_agents", "{}", ctx)
    assert result.ok is False
    assert "bastion" in result.content.lower()
