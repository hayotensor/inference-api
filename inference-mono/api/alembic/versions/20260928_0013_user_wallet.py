"""pay-as-you-go wallet: user_wallets + wallet_transactions (HYP-66)

Adds the token balance a user tops up with a one-time Stripe Checkout Session
and draws down once their subscription allowance for the period is exhausted.
`user_wallets` holds the current balance (one row per user); `wallet_transactions`
is the append-only ledger of every credit (purchase/refund/adjustment) and debit
(inference spend) that moved it, mirroring the ManualTokenAdjustment ledger
pattern already used for admin adjustments.

Revision ID: 20260928_0013
Revises: 20260908_0012
Create Date: 2026-09-28
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260928_0013"
down_revision: str | None = "20260908_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "inference_usage_events",
        sa.Column(
            "wallet_reserved_tokens", sa.BigInteger(), nullable=False, server_default="0"
        ),
    )

    op.create_table(
        "user_wallets",
        sa.Column("user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("balance_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="cascade"),
        sa.PrimaryKeyConstraint("user_id", name="pk_user_wallets"),
    )

    op.create_table(
        "wallet_transactions",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("delta_tokens", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("stripe_payment_intent_id", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="cascade"),
        sa.PrimaryKeyConstraint("id", name="pk_wallet_transactions"),
    )
    op.create_index(
        "ix_wallet_transactions_user_id", "wallet_transactions", ["user_id"]
    )
    op.create_index(
        "ix_wallet_transactions_stripe_payment_intent_id",
        "wallet_transactions",
        ["stripe_payment_intent_id"],
    )
    op.create_index(
        "ix_wallet_transactions_created_at", "wallet_transactions", ["created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_wallet_transactions_created_at", table_name="wallet_transactions")
    op.drop_index("ix_wallet_transactions_stripe_payment_intent_id", table_name="wallet_transactions")
    op.drop_index("ix_wallet_transactions_user_id", table_name="wallet_transactions")
    op.drop_table("wallet_transactions")
    op.drop_table("user_wallets")
    op.drop_column("inference_usage_events", "wallet_reserved_tokens")
