from control_plane.tools.management.confirm import (
    confirmation_satisfied,
    requires_confirmation,
)


def test_destructive_tools_require_confirmation():
    assert requires_confirmation("delete_agent") is True
    assert requires_confirmation("disable_agent") is True


def test_reversible_tools_do_not_require_confirmation():
    assert requires_confirmation("create_agent") is False
    assert requires_confirmation("list_agents") is False


def test_confirmation_needs_both_keyword_and_target():
    assert confirmation_satisfied("confirm Echo", "Echo") is True
    assert confirmation_satisfied("CONFIRM echo", "Echo") is True  # case-insensitive
    assert confirmation_satisfied("yes", "Echo") is False  # no keyword
    assert confirmation_satisfied("confirm Scout", "Echo") is False  # wrong target
    assert confirmation_satisfied("", "Echo") is False
    assert confirmation_satisfied("confirm Echo", "") is False
