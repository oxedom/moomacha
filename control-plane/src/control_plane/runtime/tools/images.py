"""OpenAI image generation tool."""

from __future__ import annotations

import base64
import binascii
import inspect
import re
import uuid
from collections.abc import Callable
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from control_plane.runtime.tools.registry import ToolRegistry
from control_plane.runtime.tools.runtime import ToolContext, ToolResult
from control_plane.services.generated_media_store import GeneratedMediaStore

MAX_PROMPT_CHARS = 32_000
ALLOWED_SIZES = {"1024x1024", "1536x1024", "1024x1536", "auto"}
MIME_TYPES = {"png": "image/png", "jpeg": "image/jpeg", "webp": "image/webp"}


class GenerateImageInput(BaseModel):
    prompt: str = Field(description="Image prompt. Be specific about subject, style, and composition.")
    title: str | None = Field(default=None, description="Short title for the chat post.")
    size: str | None = Field(default=None, description="One of 1024x1024, 1536x1024, 1024x1536, or auto.")
    quality: Literal["low", "medium", "high", "auto"] | None = None
    output_format: Literal["png", "jpeg", "webp"] | None = None
    background: Literal["transparent", "opaque", "auto"] | None = None

    @field_validator("prompt")
    @classmethod
    def _validate_prompt(cls, value: str) -> str:
        prompt = value.strip()
        if not prompt:
            raise ValueError("prompt must not be empty")
        if len(prompt) > MAX_PROMPT_CHARS:
            raise ValueError(f"prompt must be at most {MAX_PROMPT_CHARS} characters")
        return prompt

    @field_validator("title")
    @classmethod
    def _validate_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        title = re.sub(r"\s+", " ", value).strip()
        return title[:120] or None

    @field_validator("size")
    @classmethod
    def _validate_size(cls, value: str | None) -> str | None:
        if value is not None and value not in ALLOWED_SIZES:
            raise ValueError(f"size must be one of {sorted(ALLOWED_SIZES)}")
        return value


def _get(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _agent_slug(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return (slug or "agent")[:40]


async def _maybe_close(client: Any) -> None:
    close = getattr(client, "close", None)
    if close is None:
        return
    result = close()
    if inspect.isawaitable(result):
        await result


def _requested_params(
    *,
    size: str,
    quality: str,
    output_format: str,
    background: str | None,
) -> dict:
    params: dict[str, str] = {
        "size": size,
        "quality": quality,
        "output_format": output_format,
    }
    if background is not None:
        params["background"] = background
    return params


async def _generate_image(
    inp: GenerateImageInput,
    ctx: ToolContext,
    *,
    store: GeneratedMediaStore,
    client_factory: Callable[[], Any],
    model: str,
    default_size: str,
    default_quality: str,
    default_format: str,
    timeout_s: float,
    max_bytes: int,
) -> ToolResult:
    size = inp.size or default_size
    quality = inp.quality or default_quality
    output_format = inp.output_format or default_format
    background = inp.background
    if size not in ALLOWED_SIZES:
        return ToolResult(ok=False, content=f"Configured image size {size!r} is not supported.")
    if output_format not in MIME_TYPES:
        return ToolResult(ok=False, content=f"Configured image format {output_format!r} is not supported.")
    if background == "transparent" and output_format == "jpeg":
        return ToolResult(ok=False, content="Transparent backgrounds require png or webp output.")
    if background == "transparent" and model == "gpt-image-2":
        return ToolResult(
            ok=False,
            content="Transparent backgrounds are not supported by gpt-image-2; use opaque/auto.",
        )
    if ctx.conversation_type == "direct" and not ctx.direct_recipient_ids:
        return ToolResult(
            ok=False,
            content="Image generation in direct messages needs Zulip recipient ids, but none were available.",
        )

    client = client_factory()
    generation_args: dict[str, Any] = {
        "model": model,
        "prompt": inp.prompt,
        "size": size,
        "quality": quality,
        "output_format": output_format,
        "n": 1,
        "timeout": timeout_s,
    }
    if background is not None:
        generation_args["background"] = background
    try:
        response = await client.images.generate(**generation_args)
    finally:
        await _maybe_close(client)

    data = _get(response, "data") or []
    if not data:
        return ToolResult(ok=False, content="OpenAI returned no image data.")
    first = data[0]
    b64_json = _get(first, "b64_json")
    if not b64_json:
        return ToolResult(ok=False, content="OpenAI returned no base64 image data.")
    try:
        image_bytes = base64.b64decode(b64_json, validate=True)
    except binascii.Error:
        return ToolResult(ok=False, content="OpenAI returned invalid base64 image data.")
    if len(image_bytes) > max_bytes:
        return ToolResult(ok=False, content=f"Generated image is too large ({len(image_bytes)} bytes).")

    artifact_id = uuid.uuid4()
    ext = "jpeg" if output_format == "jpeg" else output_format
    filename = f"{_agent_slug(getattr(ctx.agent, 'name', 'agent'))}-{artifact_id}.{ext}"
    params = _requested_params(
        size=size, quality=quality, output_format=output_format, background=background
    )
    response_params = {
        "size": _get(response, "size"),
        "quality": _get(response, "quality"),
        "output_format": _get(response, "output_format"),
        "background": _get(response, "background"),
    }
    params["response"] = {k: v for k, v in response_params.items() if v is not None}
    artifact = await store.create(
        artifact_id=artifact_id,
        creator_agent_id=ctx.agent.id,
        source_channel=ctx.channel,
        source_topic=ctx.topic,
        source_message_id=ctx.source_message_id,
        conversation_type=ctx.conversation_type,
        prompt=inp.prompt,
        revised_prompt=_get(first, "revised_prompt"),
        model=model,
        params=params,
        mime_type=MIME_TYPES[output_format],
        filename=filename,
        data=image_bytes,
    )

    upload = await ctx.zulip.upload_file(
        filename=artifact.filename,
        content=image_bytes,
        content_type=artifact.mime_type,
    )
    upload_url = upload.get("url") or upload.get("uri")
    if not upload_url:
        return ToolResult(ok=False, content="Zulip upload succeeded but returned no upload URL.")

    title = inp.title or "Generated image"
    chat_message = (
        f"Generated image: {title}\n"
        f"[{artifact.filename}]({upload_url})\n"
        f"Artifact: {artifact.id}"
    )
    if ctx.conversation_type == "direct":
        message_id = await ctx.zulip.send_direct_message(ctx.direct_recipient_ids or [], chat_message)
    else:
        message_id = await ctx.zulip.send_message(ctx.channel, ctx.topic, chat_message)
    await store.mark_posted(artifact.id, zulip_upload_url=upload_url, zulip_message_id=message_id)
    return ToolResult(
        ok=True,
        content=f"Generated image artifact {artifact.id} and posted it to Zulip: {upload_url}",
    )


def register_image_tools(
    registry: ToolRegistry,
    store: GeneratedMediaStore,
    *,
    client_factory: Callable[[], Any],
    model: str,
    default_size: str,
    default_quality: str,
    default_format: str,
    timeout_s: float,
    max_bytes: int,
) -> None:
    registry.register(
        "generate_image",
        "Generate one image from a prompt, upload it to Zulip, and post it back to the current conversation.",
        GenerateImageInput,
        lambda inp, ctx: _generate_image(
            inp,
            ctx,
            store=store,
            client_factory=client_factory,
            model=model,
            default_size=default_size,
            default_quality=default_quality,
            default_format=default_format,
            timeout_s=timeout_s,
            max_bytes=max_bytes,
        ),
    )
