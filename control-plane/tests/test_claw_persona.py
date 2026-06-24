from control_plane.personas.claw_persona import render_persona

SAMPLE = {
    "name": "Claw",
    "identity": "You are Claw, the user's assistant. You operate in Zulip topics.",
    "voice": ["minimal emojis", "direct and concise"],
    "preferences": [
        {"topic": "Timezone", "detail": "UTC."},
        {"topic": "Briefing cadence", "detail": "Daily 9am briefing.", "deferred": True},
    ],
    "memory": {"distilled_facts": ["The user is building ExampleApp, a demo platform."]},
    "artifacts": [
        {"name": "Sample podcast", "kind": "audio", "path": "artifacts/claw/audio/sample-podcast.mp3", "sha256": "abc"}
    ],
    "resources": {"model": "gpt-4o", "channels": ["sandbox"], "tools": ["read_topic"], "bot": {"email": None, "bot_id": None}},
}


def test_render_includes_identity_and_voice():
    out = render_persona(SAMPLE)
    assert "You are Claw" in out
    assert "minimal emojis" in out


def test_deferred_preference_phrased_as_intent():
    out = render_persona(SAMPLE)
    assert "Daily 9am briefing." in out
    assert "Timezone" in out
    assert "not yet wired" in out.lower()


def test_render_includes_memory_and_artifact():
    out = render_persona(SAMPLE)
    assert "ExampleApp" in out
    assert "Sample podcast" in out
    assert "artifacts/claw/audio/sample-podcast.mp3" in out


def test_render_is_pure_returns_str():
    assert isinstance(render_persona(SAMPLE), str)
    assert '"sha256"' not in render_persona(SAMPLE)
