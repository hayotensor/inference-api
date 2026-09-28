"""referral system: users.referral_code + referrals + referral_earnings (HYP-65)

Adds a unique, lazily-generated referral code per user, the `referrals` table
recording signup-time attribution (one row per referred user, ever), and the
`referral_earnings` table -- the audit trail of every 10%-of-payment bonus
credited to a referrer's HYP-66 wallet (see app/referrals/service.py). Paid in
bonus inference-token credits, never cash.

Revision ID: 20260928_0015
Revises: 20260928_0014
Create Date: 2026-09-28
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260928_0015"
down_revision: str | None = "20260928_0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("referral_code", sa.String(length=16), nullable=True))
    op.create_index("ix_users_referral_code", "users", ["referral_code"], unique=True)

    op.create_table(
        "referrals",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("referrer_user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("referred_user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("referral_code_used", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["referrer_user_id"], ["users.id"], ondelete="cascade"),
        sa.ForeignKeyConstraint(["referred_user_id"], ["users.id"], ondelete="cascade"),
        sa.PrimaryKeyConstraint("id", name="pk_referrals"),
        sa.UniqueConstraint("referred_user_id", name="uq_referrals_referred_user_id"),
    )
    op.create_index("ix_referrals_referrer_user_id", "referrals", ["referrer_user_id"])
    op.create_index("ix_referrals_created_at", "referrals", ["created_at"])

    op.create_table(
        "referral_earnings",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("referral_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("source_id", sa.String(length=255), nullable=False),
        sa.Column("referred_user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("referrer_user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("tokens_credited", sa.BigInteger(), nullable=False),
        sa.Column("usd_equivalent_cents", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["referral_id"], ["referrals.id"], ondelete="cascade"),
        sa.ForeignKeyConstraint(["referred_user_id"], ["users.id"], ondelete="cascade"),
        sa.ForeignKeyConstraint(["referrer_user_id"], ["users.id"], ondelete="cascade"),
        sa.PrimaryKeyConstraint("id", name="pk_referral_earnings"),
        sa.UniqueConstraint("source_id", name="uq_referral_earnings_source_id"),
    )
    op.create_index("ix_referral_earnings_referral_id", "referral_earnings", ["referral_id"])
    op.create_index("ix_referral_earnings_referrer_user_id", "referral_earnings", ["referrer_user_id"])
    op.create_index("ix_referral_earnings_created_at", "referral_earnings", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_referral_earnings_created_at", table_name="referral_earnings")
    op.drop_index("ix_referral_earnings_referrer_user_id", table_name="referral_earnings")
    op.drop_index("ix_referral_earnings_referral_id", table_name="referral_earnings")
    op.drop_table("referral_earnings")

    op.drop_index("ix_referrals_created_at", table_name="referrals")
    op.drop_index("ix_referrals_referrer_user_id", table_name="referrals")
    op.drop_table("referrals")

    op.drop_index("ix_users_referral_code", table_name="users")
    op.drop_column("users", "referral_code")
