"""Add message_key to outbox_events for Kafka partition key."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260602_0003"
down_revision: str | Sequence[str] | None = "20260601_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "outbox_events",
        sa.Column("message_key", sa.String(length=64), nullable=False, server_default=""),
    )
    op.alter_column("outbox_events", "message_key", server_default=None)


def downgrade() -> None:
    op.drop_column("outbox_events", "message_key")
