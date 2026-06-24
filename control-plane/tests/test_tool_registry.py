from pydantic import BaseModel

from control_plane.runtime.tools.registry import ToolRegistry
from control_plane.runtime.tools.runtime import ToolResult


class _In(BaseModel):
    x: int


async def _adapter(inp: _In, ctx) -> ToolResult:
    return ToolResult(ok=True, content="ok")


def _registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register("alpha", "Alpha tool", _In, _adapter)
    reg.register("beta", "Beta tool", _In, _adapter)
    return reg


def test_build_schemas_filters_to_allowed_and_drops_unknown():
    schemas = _registry().build_schemas(["alpha", "ghost"])
    names = [s["function"]["name"] for s in schemas]
    assert names == ["alpha"]
    fn = schemas[0]["function"]
    assert fn["description"] == "Alpha tool"
    assert "x" in fn["parameters"]["properties"]
    assert schemas[0]["type"] == "function"


def test_build_schemas_empty_allowed_returns_empty():
    assert _registry().build_schemas([]) == []


def test_get_returns_entry_or_none():
    reg = _registry()
    entry = reg.get("beta")
    assert entry is not None
    assert entry.input_model is _In
    assert entry.adapter is _adapter
    assert reg.get("ghost") is None


def test_build_schemas_includes_management_only_for_bastion():
    reg = ToolRegistry()
    reg.register("read_topic", "read", _In, _adapter)
    reg.register("delete_agent", "del", _In, _adapter, management=True)

    normal = reg.build_schemas(["read_topic"], is_bastion=False)
    assert {s["function"]["name"] for s in normal} == {"read_topic"}

    bastion = reg.build_schemas(["read_topic"], is_bastion=True)
    assert {s["function"]["name"] for s in bastion} == {"read_topic", "delete_agent"}


def test_build_schemas_drops_management_tool_named_in_allowed_without_flag():
    # Privilege follows the is_bastion flag, not the model-editable allowed_tools:
    # naming a management tool in allowed_tools must NOT grant it.
    reg = ToolRegistry()
    reg.register("read_topic", "read", _In, _adapter)
    reg.register("delete_agent", "del", _In, _adapter, management=True)

    sneaky = reg.build_schemas(["read_topic", "delete_agent"], is_bastion=False)
    assert {s["function"]["name"] for s in sneaky} == {"read_topic"}


def test_build_schemas_drops_exec_tool_named_in_allowed_without_can_exec():
    reg = ToolRegistry()
    reg.register("read_topic", "read", _In, _adapter)
    reg.register("run_command", "shell", _In, _adapter, requires_exec=True)

    sneaky = reg.build_schemas(["read_topic", "run_command"], can_exec=False)
    assert {s["function"]["name"] for s in sneaky} == {"read_topic"}

    with_exec = reg.build_schemas(["read_topic"], can_exec=True)
    assert {s["function"]["name"] for s in with_exec} == {"read_topic", "run_command"}


def test_describe_tools_drops_privileged_tool_named_in_allowed_without_flag():
    reg = ToolRegistry()
    reg.register("read_topic", "read", _In, _adapter)
    reg.register("delete_agent", "del", _In, _adapter, management=True)
    reg.register("run_command", "shell", _In, _adapter, requires_exec=True)

    pairs = reg.describe_tools(
        ["read_topic", "delete_agent", "run_command"], is_bastion=False, can_exec=False
    )
    assert dict(pairs) == {"read_topic": "read"}


def test_describe_tools_returns_name_description_pairs_and_drops_unknown():
    pairs = _registry().describe_tools(["alpha", "ghost"])
    assert pairs == [("alpha", "Alpha tool")]


def test_describe_tools_empty_allowed_returns_empty():
    assert _registry().describe_tools([]) == []


def test_describe_tools_includes_management_only_for_bastion():
    reg = ToolRegistry()
    reg.register("read_topic", "read", _In, _adapter)
    reg.register("delete_agent", "del", _In, _adapter, management=True)

    normal = reg.describe_tools(["read_topic"], is_bastion=False)
    assert dict(normal) == {"read_topic": "read"}

    bastion = reg.describe_tools(["read_topic"], is_bastion=True)
    assert dict(bastion) == {"read_topic": "read", "delete_agent": "del"}


def test_describe_tools_includes_exec_only_when_can_exec():
    reg = ToolRegistry()
    reg.register("read_topic", "read", _In, _adapter)
    reg.register("run_command", "shell", _In, _adapter, requires_exec=True)

    without = reg.describe_tools(["read_topic"], can_exec=False)
    assert dict(without) == {"read_topic": "read"}

    with_exec = reg.describe_tools(["read_topic"], can_exec=True)
    assert dict(with_exec) == {"read_topic": "read", "run_command": "shell"}
