from control_plane.services.artifact_summary import deterministic_preview, summarize_payload


class _FakeChat:
    """Minimal stand-in for an AsyncOpenAI client exposing .chat.completions.create."""

    def __init__(self, text=None, raise_exc=False):
        self._text = text
        self._raise = raise_exc

    @property
    def chat(self):
        return self

    @property
    def completions(self):
        return self

    async def create(self, **kwargs):
        if self._raise:
            raise RuntimeError("model down")
        text = self._text

        class _Msg:
            content = text

        class _Choice:
            message = _Msg()

        class _Resp:
            choices = [_Choice()]

        return _Resp()


def test_deterministic_preview_is_compact_and_truncated():
    out = deterministic_preview({"approved": True, "notes": "x" * 500})
    assert "approved" in out
    assert len(out) <= 300


async def test_summarize_payload_uses_model_when_available():
    client = _FakeChat(text="Approved deployment with note: Ship it")
    text, model, status = await summarize_payload(
        client, model="gpt-4o-mini", title="Deploy", payload={"approved": True},
    )
    assert status == "generated"
    assert model == "gpt-4o-mini"
    assert "Approved" in text


async def test_summarize_payload_falls_back_on_model_error():
    client = _FakeChat(raise_exc=True)
    text, model, status = await summarize_payload(
        client, model="gpt-4o-mini", title="Deploy", payload={"approved": True},
    )
    assert status == "fallback"
    assert model is None
    assert "approved" in text
