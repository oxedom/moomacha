import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, DateTime, Integer, LargeBinary, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator


class UTCDateTime(TypeDecorator):
    """Normalises datetimes to UTC on write and reattaches UTC tzinfo on read, compensating for drivers (e.g. SQLite) that return naive datetimes."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):  # noqa: ANN001
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    def process_result_value(self, value, dialect):  # noqa: ANN001
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


class Base(DeclarativeBase):
    pass


def _now() -> datetime:
    return datetime.now(UTC)


class AgentRow(Base):
    __tablename__ = "agents"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), unique=True)
    persona: Mapped[str] = mapped_column(Text)
    model_id: Mapped[str] = mapped_column(String(128), default="gpt-4o")
    zulip_bot_id: Mapped[int] = mapped_column(Integer)
    zulip_bot_email: Mapped[str] = mapped_column(String(255), unique=True)
    zulip_api_key_encrypted: Mapped[str] = mapped_column(Text)
    zulip_outgoing_token_encrypted: Mapped[str] = mapped_column(String(255))
    context_message_count: Mapped[int] = mapped_column(Integer, default=20)
    readable_channels: Mapped[list] = mapped_column(JSON, default=list)
    allowed_tools: Mapped[list] = mapped_column(JSON, default=list)
    provisioning_status: Mapped[str] = mapped_column(String(32), default="active")
    is_bastion: Mapped[bool] = mapped_column(Boolean, default=False)
    can_exec: Mapped[bool] = mapped_column(Boolean, default=False)
    is_librarian: Mapped[bool] = mapped_column(Boolean, default=False)
    knowledge_artifact_ids: Mapped[list] = mapped_column(JSON, default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    runtime_kind: Mapped[str] = mapped_column(String(32), default="openai_tool_loop")
    runtime_config: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class EventRow(Base):
    __tablename__ = "events"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    actor_type: Mapped[str] = mapped_column(String(16))
    actor_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    event_type: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    related_agent_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    related_channel: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    turn_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    seq: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str | None] = mapped_column(String(16), nullable=True)


class ScheduledJobRow(Base):
    __tablename__ = "scheduled_jobs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    agent_id: Mapped[uuid.UUID] = mapped_column(Uuid)  # firing identity and creator
    channel: Mapped[str] = mapped_column(String(255))
    topic: Mapped[str] = mapped_column(String(255))
    kind: Mapped[str] = mapped_column(String(16))  # "one_shot" | "recurring"
    cron_expression: Mapped[str | None] = mapped_column(String(255), nullable=True)
    run_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    instruction: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="active")  # active | completed | cancelled | missed | error
    next_run_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    last_run_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_now, onupdate=_now)


# New tables (archetypes / pool_bots / sessions): create_all makes these on first
# boot. The Neon "no auto-ALTER" gotcha applies only to new columns on EXISTING
# tables, not to whole new tables, so no manual migration is required here.
class ArchetypeRow(Base):
    __tablename__ = "archetypes"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), unique=True)
    persona: Mapped[str] = mapped_column(Text)
    model_id: Mapped[str] = mapped_column(String(128), default="gpt-4o")
    context_message_count: Mapped[int] = mapped_column(Integer, default=20)
    allowed_tools: Mapped[list] = mapped_column(JSON, default=list)
    knowledge_artifact_ids: Mapped[list] = mapped_column(JSON, default=list)
    mcp_servers: Mapped[list] = mapped_column(JSON, default=list)
    runtime_kind: Mapped[str] = mapped_column(String(32), default="deepagents")
    runtime_config: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_now)


class PoolBotRow(Base):
    __tablename__ = "pool_bots"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    zulip_bot_id: Mapped[int] = mapped_column(Integer)
    zulip_bot_email: Mapped[str] = mapped_column(String(255), unique=True)
    zulip_api_key_encrypted: Mapped[str] = mapped_column(Text)
    zulip_outgoing_token_encrypted: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(16), default="free")  # free | leased
    current_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    current_session_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    last_active_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_now)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_now)


class SessionRow(Base):
    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    channel: Mapped[str] = mapped_column(String(255))
    topic: Mapped[str] = mapped_column(String(255))
    archetype_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    pool_bot_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    memory_ns: Mapped[str] = mapped_column(String(512))
    granted_caps: Mapped[list] = mapped_column(JSON, default=list)
    state: Mapped[str] = mapped_column(String(16), default="live")  # live | dormant | closed
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_now)
    last_active_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_now)


class KnowledgeArtifactRow(Base):
    __tablename__ = "knowledge_artifacts"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), unique=True)
    body: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_now, onupdate=_now)


class SkillRow(Base):
    __tablename__ = "skills"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), unique=True)
    body: Mapped[str] = mapped_column(Text)
    model_era: Mapped[str] = mapped_column(String(64), default="")
    triggers: Mapped[list] = mapped_column(JSON, default=list)  # reserved; not used in v1 selection
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class InteractiveArtifactRow(Base):
    __tablename__ = "interactive_artifacts"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(Text)
    html_body: Mapped[str] = mapped_column(Text)
    creator_agent_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    source_channel: Mapped[str] = mapped_column(String(255))
    source_topic: Mapped[str] = mapped_column(String(255))
    source_message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    conversation_type: Mapped[str] = mapped_column(String(16), default="stream")
    token_hash: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16), default="open")  # open|submitted|expired|revoked
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
    submitted_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_now, onupdate=_now)
    storage_backend: Mapped[str] = mapped_column(String(32), default="postgres_text")
    storage_ref: Mapped[str | None] = mapped_column(Text, nullable=True)


class InteractiveSubmissionRow(Base):
    __tablename__ = "interactive_submissions"
    __table_args__ = (
        # One accepted submission per artifact; idempotent replay keys on (artifact, submission_id).
        UniqueConstraint("artifact_id", name="uq_submission_artifact"),
        UniqueConstraint("artifact_id", "submission_id", name="uq_submission_artifact_subid"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    artifact_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    submission_id: Mapped[str] = mapped_column(String(255))
    payload_full: Mapped[dict] = mapped_column(JSON, default=dict)
    summary_text: Mapped[str] = mapped_column(Text)
    summary_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    summary_status: Mapped[str] = mapped_column(String(16))  # generated|fallback|failed
    zulip_message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    agent_job_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_now)


class GeneratedMediaArtifactRow(Base):
    __tablename__ = "generated_media_artifacts"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    creator_agent_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    source_channel: Mapped[str] = mapped_column(String(255))
    source_topic: Mapped[str] = mapped_column(String(255))
    source_message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    conversation_type: Mapped[str] = mapped_column(String(16), default="stream")
    prompt: Mapped[str] = mapped_column(Text)
    revised_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    model: Mapped[str] = mapped_column(String(128))
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    mime_type: Mapped[str] = mapped_column(String(64))
    filename: Mapped[str] = mapped_column(String(255))
    sha256: Mapped[str] = mapped_column(String(64))
    byte_length: Mapped[int] = mapped_column(Integer)
    data: Mapped[bytes] = mapped_column(LargeBinary)
    storage_backend: Mapped[str] = mapped_column(String(32), default="postgres_binary")
    storage_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    zulip_upload_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    zulip_message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_now)
