import uuid

from pydantic import BaseModel, Field

from control_plane.runtime.tools.registry import ToolRegistry
from control_plane.runtime.tools.runtime import ToolContext, ToolResult
from control_plane.services.knowledge_artifact_store import KnowledgeArtifactStore

OUTPUT_CAP = 12_000


def _bound_ids(ctx: ToolContext) -> list[uuid.UUID]:
    out = []
    for raw in getattr(ctx.agent, "knowledge_artifact_ids", []) or []:
        try:
            out.append(uuid.UUID(str(raw)))
        except ValueError:
            continue
    return out


class ListArtifactsInput(BaseModel):
    pass


class ReadArtifactInput(BaseModel):
    name: str = Field(description="The artifact name to read (from list_artifacts).")


async def _list(inp: ListArtifactsInput, ctx: ToolContext, store: KnowledgeArtifactStore) -> ToolResult:
    rows = await store.list_by_ids(_bound_ids(ctx))
    if not rows:
        return ToolResult(ok=True, content="(no knowledge artifacts bound to you)")
    lines = [f"- {r.name}: {r.body.splitlines()[0] if r.body else ''}" for r in rows]
    return ToolResult(ok=True, content="\n".join(lines))


async def _read(inp: ReadArtifactInput, ctx: ToolContext, store: KnowledgeArtifactStore) -> ToolResult:
    bound = await store.list_by_ids(_bound_ids(ctx))
    match = next((r for r in bound if r.name == inp.name), None)
    if match is None:
        return ToolResult(ok=False, content=f"Artifact '{inp.name}' is not available to you.")
    body = match.body[:OUTPUT_CAP]
    return ToolResult(ok=True, content=body)


def register_knowledge_artifact_tools(registry: ToolRegistry, store: KnowledgeArtifactStore) -> None:
    registry.register(
        "list_artifacts",
        "List the knowledge artifacts available to you (names + first line).",
        ListArtifactsInput,
        lambda inp, ctx: _list(inp, ctx, store),
    )
    registry.register(
        "read_artifact",
        "Read the full body of one knowledge artifact by name.",
        ReadArtifactInput,
        lambda inp, ctx: _read(inp, ctx, store),
    )
