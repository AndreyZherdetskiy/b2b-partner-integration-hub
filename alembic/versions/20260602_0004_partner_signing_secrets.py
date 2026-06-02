"""Add partner_signing_secrets rotation history table.

Revision ID: 20260602_0004
Revises: 20260602_0003
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from uuid6 import uuid7

revision: str = "20260602_0004"
down_revision: str | Sequence[str] | None = "20260602_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "partner_signing_secrets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("partner_id", sa.BigInteger(), nullable=False),
        sa.Column("secret_encrypted", sa.LargeBinary(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["partner_id"], ["partners.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_partner_signing_secrets_partner_id",
        "partner_signing_secrets",
        ["partner_id"],
    )

    conn = op.get_bind()
    partners = conn.execute(
        sa.text(
            "SELECT id, signing_secret_encrypted FROM partners "
            "WHERE signing_secret_encrypted IS NOT NULL"
        )
    ).fetchall()
    for partner_id, secret_encrypted in partners:
        conn.execute(
            sa.text(
                """
                INSERT INTO partner_signing_secrets (
                    id,
                    partner_id,
                    secret_encrypted,
                    version,
                    status,
                    valid_from,
                    valid_until,
                    created_at
                ) VALUES (
                    :id,
                    :partner_id,
                    :secret_encrypted,
                    1,
                    'primary',
                    now(),
                    NULL,
                    now()
                )
                """
            ),
            {
                "id": str(uuid7()),
                "partner_id": partner_id,
                "secret_encrypted": secret_encrypted,
            },
        )


def downgrade() -> None:
    op.drop_index("ix_partner_signing_secrets_partner_id", table_name="partner_signing_secrets")
    op.drop_table("partner_signing_secrets")
