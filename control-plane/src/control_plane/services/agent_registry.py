import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from control_plane.db.tables import AgentRow
from control_plane.schemas.agents import AgentCreate, AgentRead, AgentUpdate, ResolvedAgent
from control_plane.services.crypto import SecretBox
from control_plane.services.exceptions import AgentAlreadyExistsError


class AgentRegistry:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        secret_box: SecretBox,
    ) -> None:
        self._sessions = session_factory
        self._box = secret_box

    async def create(self, data: AgentCreate) -> AgentRead:
        row = AgentRow(
            name=data.name,
            persona=data.persona,
            model_id=data.model_id,
            zulip_bot_id=data.zulip_bot_id,
            zulip_bot_email=data.zulip_bot_email,
            zulip_api_key_encrypted=self._box.encrypt(data.zulip_api_key),
            zulip_outgoing_token_encrypted=self._box.encrypt(data.zulip_outgoing_token),
            context_message_count=data.context_message_count,
            readable_channels=data.readable_channels,
            allowed_tools=data.allowed_tools,
            provisioning_status="active",
            runtime_kind=data.runtime_kind,
            runtime_config=data.runtime_config,
            is_librarian=data.is_librarian,
            knowledge_artifact_ids=data.knowledge_artifact_ids,
        )
        async with self._sessions() as session:
            session.add(row)
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise AgentAlreadyExistsError(
                    f"agent with name '{data.name}' or bot_email "
                    f"'{data.zulip_bot_email}' already exists"
                ) from exc
            await session.refresh(row)
            return AgentRead.model_validate(row)

    async def list(self) -> list[AgentRead]:
        async with self._sessions() as session:
            rows = (
                await session.execute(select(AgentRow).order_by(AgentRow.created_at))
            ).scalars().all()
            return [AgentRead.model_validate(r) for r in rows]

    async def get(self, agent_id: uuid.UUID) -> AgentRead | None:
        async with self._sessions() as session:
            row = await session.get(AgentRow, agent_id)
            return AgentRead.model_validate(row) if row else None

    async def delete(self, agent_id: uuid.UUID) -> bool:
        async with self._sessions() as session:
            row = await session.get(AgentRow, agent_id)
            if row is None:
                return False
            await session.delete(row)
            await session.commit()
            return True

    async def update(self, agent_id: uuid.UUID, data: AgentUpdate) -> AgentRead | None:
        async with self._sessions() as session:
            row = await session.get(AgentRow, agent_id)
            if row is None:
                return None
            if data.persona is not None:
                row.persona = data.persona
            if data.model_id is not None:
                row.model_id = data.model_id
            if data.readable_channels is not None:
                row.readable_channels = data.readable_channels
            if data.context_message_count is not None:
                row.context_message_count = data.context_message_count
            if data.allowed_tools is not None:
                row.allowed_tools = data.allowed_tools
            if data.zulip_bot_id is not None:
                row.zulip_bot_id = data.zulip_bot_id
            if data.zulip_bot_email is not None:
                row.zulip_bot_email = data.zulip_bot_email
            if data.zulip_api_key is not None:
                row.zulip_api_key_encrypted = self._box.encrypt(data.zulip_api_key)
            if data.zulip_outgoing_token is not None:
                row.zulip_outgoing_token_encrypted = self._box.encrypt(data.zulip_outgoing_token)
            if data.provisioning_status is not None:
                row.provisioning_status = data.provisioning_status
            if data.runtime_kind is not None:
                row.runtime_kind = data.runtime_kind
            if data.runtime_config is not None:
                row.runtime_config = data.runtime_config
            if data.is_librarian is not None:
                row.is_librarian = data.is_librarian
            if data.knowledge_artifact_ids is not None:
                row.knowledge_artifact_ids = data.knowledge_artifact_ids
            await session.commit()
            await session.refresh(row)
            return AgentRead.model_validate(row)

    async def set_enabled(self, agent_id: uuid.UUID, enabled: bool) -> bool:
        async with self._sessions() as session:
            row = await session.get(AgentRow, agent_id)
            if row is None:
                return False
            row.enabled = enabled
            await session.commit()
            return True

    async def resolve_by_bot_email(self, bot_email: str) -> ResolvedAgent | None:
        async with self._sessions() as session:
            row = (
                await session.execute(
                    select(AgentRow).where(AgentRow.zulip_bot_email == bot_email)
                )
            ).scalar_one_or_none()
            if row is None:
                return None
            return ResolvedAgent(
                id=row.id,
                name=row.name,
                persona=row.persona,
                model_id=row.model_id,
                zulip_bot_id=row.zulip_bot_id,
                zulip_bot_email=row.zulip_bot_email,
                zulip_api_key=self._box.decrypt(row.zulip_api_key_encrypted),
                zulip_outgoing_token=self._box.decrypt(row.zulip_outgoing_token_encrypted),
                context_message_count=row.context_message_count,
                readable_channels=row.readable_channels,
                allowed_tools=row.allowed_tools,
                is_bastion=row.is_bastion,
                can_exec=row.can_exec,
                is_librarian=row.is_librarian,
                knowledge_artifact_ids=row.knowledge_artifact_ids or [],
                enabled=row.enabled,
                runtime_kind=row.runtime_kind,
                runtime_config=row.runtime_config,
            )
