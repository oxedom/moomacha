from pydantic import BaseModel, ConfigDict


class ZulipDirectRecipient(BaseModel):
    model_config = ConfigDict(extra="ignore")

    email: str | None = None
    full_name: str | None = None
    id: int


class ZulipMessage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int
    content: str
    display_recipient: str | list[ZulipDirectRecipient]
    subject: str = ""
    stream_id: int | None = None
    sender_id: int | None = None
    sender_email: str | None = None
    type: str | None = None
    timestamp: int | None = None


class OutgoingWebhookPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    token: str
    trigger: str | None = None
    bot_email: str | None = None
    bot_full_name: str | None = None
    data: str | None = None
    message: ZulipMessage
