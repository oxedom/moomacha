from control_plane.services.context_assembly import build_context_prompt, truncate


def test_truncate_short_message_unchanged():
    assert truncate("hello", limit=1000) == "hello"


def test_truncate_long_message_marks_remainder():
    body = "x" * 1500
    out = truncate(body, limit=1000)
    assert out.startswith("x" * 200)
    assert out.endswith("…[truncated, 1500 chars]")
    assert len(out) < len(body)


def test_build_context_prompt_includes_persona_and_messages():
    messages = [
        {"sender_full_name": "Alice", "content": "first"},
        {"sender_full_name": "Bob", "content": "second"},
    ]
    prompt = build_context_prompt(
        persona="You are helpful.",
        messages=messages,
        channel="sandbox",
        topic="greetings",
    )
    assert prompt.startswith("You are helpful.")
    assert "#sandbox > greetings" in prompt
    assert "Alice: first" in prompt
    assert "Bob: second" in prompt
    assert "## Your task" in prompt


def test_build_context_prompt_renders_tools_section():
    prompt = build_context_prompt(
        persona="You are helpful.",
        messages=[],
        channel="sandbox",
        topic="greetings",
        tools=[("read_topic", "Read a topic"), ("remember", "Save a fact")],
    )
    assert "## Your tools" in prompt
    assert "- read_topic — Read a topic" in prompt
    assert "- remember — Save a fact" in prompt


def test_build_context_prompt_omits_tools_section_when_absent():
    prompt = build_context_prompt(
        persona="You are helpful.",
        messages=[],
        channel="sandbox",
        topic="greetings",
    )
    assert "## Your tools" not in prompt

    empty = build_context_prompt(
        persona="You are helpful.",
        messages=[],
        channel="sandbox",
        topic="greetings",
        tools=[],
    )
    assert "## Your tools" not in empty
