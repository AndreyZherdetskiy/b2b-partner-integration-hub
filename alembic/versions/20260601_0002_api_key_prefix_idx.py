"""Index on partner_api_keys.key_prefix for inbound API key lookup."""

from collections.abc import Sequence

from alembic import op

revision: str = "20260601_0002"
down_revision: str | Sequence[str] | None = "20260601_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_partner_api_keys_key_prefix",
        "partner_api_keys",
        ["key_prefix"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_partner_api_keys_key_prefix", table_name="partner_api_keys")
