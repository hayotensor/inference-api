"""wallet login: nullable wallet_nonces.user_id + users.wallet_verified_at

Enables sign-in-with-wallet (SIWE) login. A login nonce is created before any
user exists (user_id null), and wallet-only accounts are marked eligible for the
API-key gate via users.wallet_verified_at (parallels phone_verified_at).

Revision ID: 20260716_0008
Revises: 20260706_0007
Create Date: 2026-07-16
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260716_0008"
down_revision: str | None = "20260706_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("wallet_verified_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.alter_column(
        "wallet_nonces",
        "user_id",
        existing_type=sa.Uuid(as_uuid=True),
        nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "wallet_nonces",
        "user_id",
        existing_type=sa.Uuid(as_uuid=True),
        nullable=False,
    )
    op.drop_column("users", "wallet_verified_at")
