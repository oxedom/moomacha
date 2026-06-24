from control_plane.observability.events import AgentEvent, EventEmitter, EventType


async def test_emitter_assigns_monotonic_seq_and_ids():
    captured: list[AgentEvent] = []

    async def sink(ev: AgentEvent) -> None:
        captured.append(ev)

    em = EventEmitter(trace_id="tr", turn_id="tn", emit_fn=sink)
    await em.turn_start(agent_id="a", runtime_kind="openai_tool_loop", model="gpt-4o",
                        channel="c", topic="t", source_message_id=7, invoking_user="u@x")
    await em.tool_call(name="search", args='{"q":1}', result="ok", ok=True, latency_ms=12)
    await em.turn_end(status="ok", duration_ms=100, reply="hi")

    assert [e.type for e in captured] == [
        EventType.TURN_START, EventType.TOOL_CALL, EventType.TURN_END,
    ]
    assert [e.seq for e in captured] == [0, 1, 2]
    assert all(e.trace_id == "tr" and e.turn_id == "tn" for e in captured)
    assert captured[1].attrs["name"] == "search"
    assert captured[1].attrs["ok"] is True
