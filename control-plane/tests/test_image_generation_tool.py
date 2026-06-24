import base64
import json
import uuid
from types import SimpleNamespace

from control_plane.db.engine import build_session_factory, create_all
from control_plane.db.tables import GeneratedMediaArtifactRow
from control_plane.runtime.tools.images import register_image_tools
from control_plane.runtime.tools.registry import ToolRegistry
from control_plane.runtime.tools.runtime import ToolContext, ToolRuntime
from control_plane.services.generated_media_store import GeneratedMediaStore


class FakeImages:
    def __init__(self, payload: bytes = b"png-bytes") -> None:
        self.payload = payload
        self.calls = []

    async def generate(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            data=[
                SimpleNamespace(
                    b64_json=base64.b64encode(self.payload).decode("ascii"),
                    revised_prompt="revised prompt",
                )
            ],
            size=kwargs["size"],
            quality=kwargs["quality"],
            output_format=kwargs["output_format"],
        )


class FakeOpenAIClient:
    def __init__(self, images: FakeImages) -> None:
        self.images = images
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class FakeZulip:
    def __init__(self) -> None:
        self.uploads = []
        self.stream_messages = []
        self.direct_messages = []

    async def upload_file(self, *, filename: str, content: bytes, content_type: str) -> dict:
        self.uploads.append(
            {"filename": filename, "content": content, "content_type": content_type}
        )
        return {"url": f"/user_uploads/1/a/{filename}", "result": "success"}

    async def send_message(self, channel: str, topic: str, content: str) -> int:
        self.stream_messages.append({"channel": channel, "topic": topic, "content": content})
        return 7001

    async def send_direct_message(self, recipient_ids: list[int], content: str) -> int:
        self.direct_messages.append({"recipient_ids": recipient_ids, "content": content})
        return 7002


async def _runtime(payload: bytes = b"png-bytes", *, model: str = "gpt-image-2", max_bytes: int = 20_000_000):
    factory, engine = build_session_factory("sqlite+aiosqlite://")
    await create_all(engine)
    events = []

    async def fake_write_event(**kwargs):
        events.append(kwargs)

    store = GeneratedMediaStore(factory, fake_write_event)
    images = FakeImages(payload)
    client = FakeOpenAIClient(images)
    registry = ToolRegistry()
    register_image_tools(
        registry,
        store,
        client_factory=lambda: client,
        model=model,
        default_size="1024x1024",
        default_quality="low",
        default_format="png",
        timeout_s=120.0,
        max_bytes=max_bytes,
    )
    return ToolRuntime(registry), factory, engine, events, images, client


def _agent():
    return SimpleNamespace(
        id=uuid.uuid4(),
        name="Claw",
        allowed_tools=["generate_image"],
        is_bastion=False,
        can_exec=False,
    )


async def test_generate_image_posts_stream_and_persists_artifact():
    runtime, factory, engine, events, images, client = await _runtime()
    zulip = FakeZulip()
    agent = _agent()
    ctx = ToolContext(
        agent=agent,
        zulip=zulip,
        channel="sandbox",
        topic="images",
        source_message_id=123,
        conversation_type="stream",
    )
    try:
        result = await runtime.execute(
            "generate_image",
            json.dumps({"prompt": "Draw a small green square", "title": "Green square"}),
            ctx,
        )

        assert result.ok is True
        assert images.calls[0]["model"] == "gpt-image-2"
        assert images.calls[0]["size"] == "1024x1024"
        assert images.calls[0]["quality"] == "low"
        assert images.calls[0]["output_format"] == "png"
        assert images.calls[0]["timeout"] == 120.0
        assert client.closed is True
        assert zulip.uploads[0]["content"] == b"png-bytes"
        assert zulip.uploads[0]["content_type"] == "image/png"
        assert zulip.stream_messages[0]["channel"] == "sandbox"
        assert "Generated image: Green square" in zulip.stream_messages[0]["content"]
        assert "Artifact:" in zulip.stream_messages[0]["content"]
        async with factory() as session:
            artifact = await session.get(
                GeneratedMediaArtifactRow,
                uuid.UUID(result.content.split("artifact ", 1)[1].split(" ", 1)[0]),
            )
            assert artifact.prompt == "Draw a small green square"
            assert artifact.revised_prompt == "revised prompt"
            assert artifact.zulip_message_id == 7001
            assert artifact.zulip_upload_url.startswith("/user_uploads/")
        assert [e["event_type"] for e in events] == [
            "generated_media_created",
            "generated_media_posted",
        ]
    finally:
        await engine.dispose()


async def test_generate_image_posts_direct_message_when_recipient_ids_are_available():
    runtime, _factory, engine, _events, _images, _client = await _runtime()
    zulip = FakeZulip()
    ctx = ToolContext(
        agent=_agent(),
        zulip=zulip,
        channel="",
        topic="",
        conversation_type="direct",
        direct_recipient_ids=[10, 11],
    )
    try:
        result = await runtime.execute(
            "generate_image",
            json.dumps({"prompt": "Draw a tiny icon"}),
            ctx,
        )

        assert result.ok is True
        assert zulip.stream_messages == []
        assert zulip.direct_messages[0]["recipient_ids"] == [10, 11]
    finally:
        await engine.dispose()


async def test_generate_image_rejects_transparent_background_for_gpt_image_2():
    runtime, _factory, engine, _events, images, _client = await _runtime()
    ctx = ToolContext(
        agent=_agent(),
        zulip=FakeZulip(),
        channel="sandbox",
        topic="images",
    )
    try:
        result = await runtime.execute(
            "generate_image",
            json.dumps({"prompt": "Draw a logo", "background": "transparent"}),
            ctx,
        )

        assert result.ok is False
        assert "Transparent backgrounds are not supported" in result.content
        assert images.calls == []
    finally:
        await engine.dispose()


async def test_generate_image_rejects_oversized_decoded_image_without_uploading():
    runtime, _factory, engine, _events, _images, _client = await _runtime(
        payload=b"too-large",
        max_bytes=3,
    )
    zulip = FakeZulip()
    ctx = ToolContext(agent=_agent(), zulip=zulip, channel="sandbox", topic="images")
    try:
        result = await runtime.execute(
            "generate_image",
            json.dumps({"prompt": "Draw something"}),
            ctx,
        )

        assert result.ok is False
        assert "too large" in result.content
        assert zulip.uploads == []
        assert zulip.stream_messages == []
    finally:
        await engine.dispose()
