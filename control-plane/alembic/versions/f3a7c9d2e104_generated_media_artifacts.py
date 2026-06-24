"""generated media artifacts

Revision ID: f3a7c9d2e104
Revises: 1817e5682560
Create Date: 2026-06-04 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import control_plane.db.tables


# revision identifiers, used by Alembic.
revision: str = "f3a7c9d2e104"
down_revision: Union[str, Sequence[str], None] = "1817e5682560"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "generated_media_artifacts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("creator_agent_id", sa.Uuid(), nullable=False),
        sa.Column("source_channel", sa.String(length=255), nullable=False),
        sa.Column("source_topic", sa.String(length=255), nullable=False),
        sa.Column("source_message_id", sa.Integer(), nullable=True),
        sa.Column("conversation_type", sa.String(length=16), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("revised_prompt", sa.Text(), nullable=True),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("params", sa.JSON(), nullable=False),
        sa.Column("mime_type", sa.String(length=64), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("byte_length", sa.Integer(), nullable=False),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.Column("storage_backend", sa.String(length=32), nullable=False),
        sa.Column("storage_ref", sa.Text(), nullable=True),
        sa.Column("zulip_upload_url", sa.Text(), nullable=True),
        sa.Column("zulip_message_id", sa.Integer(), nullable=True),
        sa.Column("created_at", control_plane.db.tables.UTCDateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("generated_media_artifacts")
