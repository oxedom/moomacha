from control_plane.runtime.tools.registry import ToolRegistry
from control_plane.tools.management.session_adapters import register_session_tools


def test_registers_four_management_tools():
    reg = ToolRegistry()
    register_session_tools(reg)
    for name in ("search_archetypes", "build_archetype", "spin_up_session", "close_session"):
        entry = reg.get(name)
        assert entry is not None, name
        assert entry.management is True


def test_session_tools_appear_only_for_bastion_schema():
    reg = ToolRegistry()
    register_session_tools(reg)
    bastion = {s["function"]["name"] for s in reg.build_schemas([], is_bastion=True)}
    normal = {s["function"]["name"] for s in reg.build_schemas([], is_bastion=False)}
    assert "spin_up_session" in bastion
    assert "spin_up_session" not in normal
