import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from control_plane.schemas.agents import AgentCreate, AgentRead
from control_plane.services.agent_registry import AgentRegistry
from control_plane.services.exceptions import AgentAlreadyExistsError


class AgentCreateRequest(BaseModel):
    name: str
    persona: str
    model_id: str = "gpt-4o"
    context_message_count: int = 20
    readable_channels: list[str] = []
    allowed_tools: list[str] = []
    # When all four are supplied, manual registration is used (the human created
    # the outgoing-webhook bot in Zulip). Otherwise the bot is auto-provisioned.
    zulip_bot_id: int | None = None
    zulip_bot_email: str | None = None
    zulip_api_key: str | None = None
    zulip_outgoing_token: str | None = None


def build_agents_router(
    registry: AgentRegistry,
    admin_client,
    payload_url: str,
) -> APIRouter:
    router = APIRouter(prefix="/agents", tags=["agents"])

    @router.post("", status_code=201, response_model=AgentRead)
    async def create_agent(req: AgentCreateRequest) -> AgentRead:
        manual = all(
            [req.zulip_bot_email, req.zulip_api_key, req.zulip_outgoing_token, req.zulip_bot_id]
        )
        if manual:
            create = AgentCreate(
                name=req.name,
                persona=req.persona,
                model_id=req.model_id,
                context_message_count=req.context_message_count,
                readable_channels=req.readable_channels,
                allowed_tools=req.allowed_tools,
                zulip_bot_id=req.zulip_bot_id,
                zulip_bot_email=req.zulip_bot_email,
                zulip_api_key=req.zulip_api_key,
                zulip_outgoing_token=req.zulip_outgoing_token,
            )
        else:
            short_name = req.name.lower().replace(" ", "-") + "-bot"
            try:
                result = await admin_client.provision_bot(
                    full_name=req.name,
                    short_name=short_name,
                    payload_url=payload_url,
                    channels=req.readable_channels,
                )
            except Exception as exc:  # noqa: BLE001 - surface any provisioning failure as 502
                raise HTTPException(status_code=502, detail=f"Provisioning failed: {exc}") from exc
            create = AgentCreate(
                name=req.name,
                persona=req.persona,
                model_id=req.model_id,
                context_message_count=req.context_message_count,
                readable_channels=req.readable_channels,
                allowed_tools=req.allowed_tools,
                zulip_bot_id=result.bot_id,
                zulip_bot_email=result.bot_email,
                zulip_api_key=result.api_key,
                # Bot creation does not return the outgoing-webhook token, so it
                # must be set via a manual follow-up (or resolved by the
                # provisioning spike) before this bot's webhooks will validate.
                zulip_outgoing_token="",
            )
        try:
            return await registry.create(create)
        except AgentAlreadyExistsError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.get("", response_model=list[AgentRead])
    async def list_agents() -> list[AgentRead]:
        return await registry.list()

    @router.delete("/{agent_id}", status_code=204)
    async def delete_agent(agent_id: uuid.UUID) -> None:
        if not await registry.delete(agent_id):
            raise HTTPException(status_code=404, detail="agent not found")

    return router
