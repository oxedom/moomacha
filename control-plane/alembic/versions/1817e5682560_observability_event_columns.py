"""observability event columns

Revision ID: 1817e5682560
Revises: cbc2e4768e4f
Create Date: 2026-06-01 11:23:45.289760

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1817e5682560'
down_revision: Union[str, Sequence[str], None] = 'cbc2e4768e4f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("events", sa.Column("trace_id", sa.String(64), nullable=True))
    op.add_column("events", sa.Column("turn_id", sa.String(64), nullable=True))
    op.add_column("events", sa.Column("seq", sa.Integer(), nullable=True))
    op.add_column("events", sa.Column("duration_ms", sa.Integer(), nullable=True))
    op.add_column("events", sa.Column("status", sa.String(16), nullable=True))
    op.create_index("ix_events_trace_id", "events", ["trace_id"])
    op.create_index("ix_events_turn_id", "events", ["turn_id"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_events_turn_id", table_name="events")
    op.drop_index("ix_events_trace_id", table_name="events")
    for col in ("status", "duration_ms", "seq", "turn_id", "trace_id"):
        op.drop_column("events", col)
