import uuid

from pydantic import BaseModel, ConfigDict, Field


class AgentCreate(BaseModel):
    name: str
    persona: str
    zulip_bot_id: int
    zulip_bot_email: str
    zulip_api_key: str
    zulip_outgoing_token: str
    model_id: str = "gpt-4o"
    context_message_count: int = 20
    readable_channels: list[str] = Field(default_factory=list)
    allowed_tools: list[str] = Field(default_factory=list)
    runtime_kind: str = "openai_tool_loop"
    runtime_config: dict = Field(default_factory=dict)
    is_librarian: bool = False
    knowledge_artifact_ids: list[str] = Field(default_factory=list)


class AgentRead(BaseModel):
    """Public view of an agent. Never contains secrets."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    persona: str
    model_id: str
    zulip_bot_id: int
    zulip_bot_email: str
    context_message_count: int
    readable_channels: list[str]
    allowed_tools: list[str]
    provisioning_status: str
    enabled: bool = True
    runtime_kind: str = "openai_tool_loop"
    runtime_config: dict = Field(default_factory=dict)
    is_librarian: bool = False
    knowledge_artifact_ids: list[str] = Field(default_factory=list)


class AgentUpdate(BaseModel):
    """Partial update of an agent. Only non-None fields are applied."""

    persona: str | None = None
    model_id: str | None = None
    readable_channels: list[str] | None = None
    context_message_count: int | None = None
    allowed_tools: list[str] | None = None
    zulip_bot_id: int | None = None
    zulip_bot_email: str | None = None
    zulip_api_key: str | None = None
    zulip_outgoing_token: str | None = None
    provisioning_status: str | None = None
    runtime_kind: str | None = None
    runtime_config: dict | None = None
    is_librarian: bool | None = None
    knowledge_artifact_ids: list[str] | None = None


class ResolvedAgent(BaseModel):
    """Internal view used by the runtime; includes decrypted secrets."""

    id: uuid.UUID
    name: str
    persona: str
    model_id: str
    zulip_bot_id: int | None = None
    zulip_bot_email: str
    zulip_api_key: str
    zulip_outgoing_token: str
    context_message_count: int
    readable_channels: list[str]
    allowed_tools: list[str] = Field(default_factory=list)
    is_bastion: bool = False
    can_exec: bool = False
    is_librarian: bool = False
    knowledge_artifact_ids: list[str] = Field(default_factory=list)
    enabled: bool = True
    runtime_kind: str = "openai_tool_loop"
    runtime_config: dict = Field(default_factory=dict)
