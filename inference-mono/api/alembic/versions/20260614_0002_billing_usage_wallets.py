"""billing usage wallets

Revision ID: 20260614_0002
Revises: 20260612_0001
Create Date: 2026-06-14
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260614_0002"
down_revision: str | None = "20260612_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "billing_plans",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("stripe_price_id", sa.String(length=255), nullable=True),
        sa.Column("monthly_token_allowance", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
        sa.UniqueConstraint("stripe_price_id"),
    )
    op.create_index("ix_billing_plans_active", "billing_plans", ["active"])
    op.create_index("ix_billing_plans_stripe_price_id", "billing_plans", ["stripe_price_id"])

    op.create_table(
        "user_billing_accounts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("stripe_customer_id", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="cascade"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("stripe_customer_id"),
        sa.UniqueConstraint("user_id"),
    )
    op.create_index("ix_user_billing_accounts_stripe_customer_id", "user_billing_accounts", ["stripe_customer_id"])
    op.create_index("ix_user_billing_accounts_user_id", "user_billing_accounts", ["user_id"])

    op.create_table(
        "user_subscriptions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("stripe_subscription_id", sa.String(length=255), nullable=True),
        sa.Column("stripe_price_id", sa.String(length=255), nullable=True),
        sa.Column("plan_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(length=64), nullable=False, server_default="free"),
        sa.Column("current_period_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("current_period_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_at_period_end", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["plan_id"], ["billing_plans.id"], ondelete="set null"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="cascade"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("stripe_subscription_id"),
        sa.UniqueConstraint("user_id"),
    )
    op.create_index("ix_user_subscriptions_plan_id", "user_subscriptions", ["plan_id"])
    op.create_index("ix_user_subscriptions_status", "user_subscriptions", ["status"])
    op.create_index("ix_user_subscriptions_stripe_price_id", "user_subscriptions", ["stripe_price_id"])
    op.create_index("ix_user_subscriptions_stripe_subscription_id", "user_subscriptions", ["stripe_subscription_id"])
    op.create_index("ix_user_subscriptions_user_id", "user_subscriptions", ["user_id"])

    op.create_table(
        "evm_wallets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("address", sa.String(length=42), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_balance_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="cascade"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("address"),
        sa.UniqueConstraint("user_id"),
    )
    op.create_index("ix_evm_wallets_address", "evm_wallets", ["address"])
    op.create_index("ix_evm_wallets_user_id", "evm_wallets", ["user_id"])

    op.create_table(
        "wallet_nonces",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("address", sa.String(length=42), nullable=False),
        sa.Column("nonce_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="cascade"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("nonce_hash"),
    )
    op.create_index("ix_wallet_nonces_address", "wallet_nonces", ["address"])
    op.create_index("ix_wallet_nonces_expires_at", "wallet_nonces", ["expires_at"])
    op.create_index("ix_wallet_nonces_used_at", "wallet_nonces", ["used_at"])
    op.create_index("ix_wallet_nonces_user_id", "wallet_nonces", ["user_id"])

    op.create_table(
        "crypto_balance_snapshots",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("wallet_address", sa.String(length=42), nullable=False),
        sa.Column("chain", sa.String(length=64), nullable=False),
        sa.Column("token_type", sa.String(length=64), nullable=False),
        sa.Column("token_contract_address", sa.String(length=42), nullable=True),
        sa.Column("raw_balance", sa.String(length=128), nullable=False),
        sa.Column("normalized_balance", sa.Numeric(48, 18), nullable=False),
        sa.Column("inference_token_allowance", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("block_number", sa.BigInteger(), nullable=True),
        sa.Column("error_message", sa.String(length=512), nullable=True),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="cascade"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_crypto_balance_snapshots_chain", "crypto_balance_snapshots", ["chain"])
    op.create_index("ix_crypto_balance_snapshots_checked_at", "crypto_balance_snapshots", ["checked_at"])
    op.create_index("ix_crypto_balance_snapshots_token_contract_address", "crypto_balance_snapshots", ["token_contract_address"])
    op.create_index("ix_crypto_balance_snapshots_token_type", "crypto_balance_snapshots", ["token_type"])
    op.create_index("ix_crypto_balance_snapshots_user_id", "crypto_balance_snapshots", ["user_id"])
    op.create_index("ix_crypto_balance_snapshots_wallet_address", "crypto_balance_snapshots", ["wallet_address"])

    op.create_table(
        "usage_periods",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("stripe_allowance", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("ethereum_erc20_allowance", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("substrate_native_allowance", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("manual_allowance", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("total_allowance", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("used_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("remaining_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("calculated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="cascade"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "period_start", "period_end", name="uq_usage_periods_user_period"),
    )
    op.create_index("ix_usage_periods_period_end", "usage_periods", ["period_end"])
    op.create_index("ix_usage_periods_period_start", "usage_periods", ["period_start"])
    op.create_index("ix_usage_periods_user_id", "usage_periods", ["user_id"])

    op.create_table(
        "inference_usage_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("usage_period_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("api_key_id", sa.Uuid(), nullable=True),
        sa.Column("request_id", sa.String(length=128), nullable=False),
        sa.Column("model", sa.String(length=120), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completion_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("raw_total_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("model_multiplier", sa.Numeric(10, 4), nullable=False, server_default="1"),
        sa.Column("reserved_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("charged_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="reserved"),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["api_key_id"], ["api_keys.id"], ondelete="set null"),
        sa.ForeignKeyConstraint(["usage_period_id"], ["usage_periods.id"], ondelete="cascade"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="cascade"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "request_id", name="uq_inference_usage_events_user_request"),
    )
    op.create_index("ix_inference_usage_events_api_key_id", "inference_usage_events", ["api_key_id"])
    op.create_index("ix_inference_usage_events_created_at", "inference_usage_events", ["created_at"])
    op.create_index("ix_inference_usage_events_model", "inference_usage_events", ["model"])
    op.create_index("ix_inference_usage_events_request_id", "inference_usage_events", ["request_id"])
    op.create_index("ix_inference_usage_events_status", "inference_usage_events", ["status"])
    op.create_index("ix_inference_usage_events_usage_period_id", "inference_usage_events", ["usage_period_id"])
    op.create_index("ix_inference_usage_events_user_id", "inference_usage_events", ["user_id"])

    op.create_table(
        "manual_token_adjustments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("amount", sa.BigInteger(), nullable=False),
        sa.Column("reason", sa.String(length=512), nullable=False),
        sa.Column("admin_actor_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["admin_actor_id"], ["users.id"], ondelete="set null"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="cascade"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_manual_token_adjustments_admin_actor_id", "manual_token_adjustments", ["admin_actor_id"])
    op.create_index("ix_manual_token_adjustments_created_at", "manual_token_adjustments", ["created_at"])
    op.create_index("ix_manual_token_adjustments_user_id", "manual_token_adjustments", ["user_id"])

    op.create_table(
        "model_pricing",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("model_name", sa.String(length=120), nullable=False),
        sa.Column("token_multiplier", sa.Numeric(10, 4), nullable=False, server_default="1"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("model_name"),
    )
    op.create_index("ix_model_pricing_active", "model_pricing", ["active"])
    op.create_index("ix_model_pricing_model_name", "model_pricing", ["model_name"])

    op.create_table(
        "stripe_webhook_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("stripe_event_id", sa.String(length=255), nullable=False),
        sa.Column("event_type", sa.String(length=120), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("stripe_event_id"),
    )
    op.create_index("ix_stripe_webhook_events_event_type", "stripe_webhook_events", ["event_type"])
    op.create_index("ix_stripe_webhook_events_processed_at", "stripe_webhook_events", ["processed_at"])
    op.create_index("ix_stripe_webhook_events_stripe_event_id", "stripe_webhook_events", ["stripe_event_id"])


def downgrade() -> None:
    op.drop_index("ix_stripe_webhook_events_stripe_event_id", table_name="stripe_webhook_events")
    op.drop_index("ix_stripe_webhook_events_processed_at", table_name="stripe_webhook_events")
    op.drop_index("ix_stripe_webhook_events_event_type", table_name="stripe_webhook_events")
    op.drop_table("stripe_webhook_events")
    op.drop_index("ix_model_pricing_model_name", table_name="model_pricing")
    op.drop_index("ix_model_pricing_active", table_name="model_pricing")
    op.drop_table("model_pricing")
    op.drop_index("ix_manual_token_adjustments_user_id", table_name="manual_token_adjustments")
    op.drop_index("ix_manual_token_adjustments_created_at", table_name="manual_token_adjustments")
    op.drop_index("ix_manual_token_adjustments_admin_actor_id", table_name="manual_token_adjustments")
    op.drop_table("manual_token_adjustments")
    op.drop_index("ix_inference_usage_events_user_id", table_name="inference_usage_events")
    op.drop_index("ix_inference_usage_events_usage_period_id", table_name="inference_usage_events")
    op.drop_index("ix_inference_usage_events_status", table_name="inference_usage_events")
    op.drop_index("ix_inference_usage_events_request_id", table_name="inference_usage_events")
    op.drop_index("ix_inference_usage_events_model", table_name="inference_usage_events")
    op.drop_index("ix_inference_usage_events_created_at", table_name="inference_usage_events")
    op.drop_index("ix_inference_usage_events_api_key_id", table_name="inference_usage_events")
    op.drop_table("inference_usage_events")
    op.drop_index("ix_usage_periods_user_id", table_name="usage_periods")
    op.drop_index("ix_usage_periods_period_start", table_name="usage_periods")
    op.drop_index("ix_usage_periods_period_end", table_name="usage_periods")
    op.drop_table("usage_periods")
    op.drop_index("ix_crypto_balance_snapshots_wallet_address", table_name="crypto_balance_snapshots")
    op.drop_index("ix_crypto_balance_snapshots_user_id", table_name="crypto_balance_snapshots")
    op.drop_index("ix_crypto_balance_snapshots_token_type", table_name="crypto_balance_snapshots")
    op.drop_index("ix_crypto_balance_snapshots_token_contract_address", table_name="crypto_balance_snapshots")
    op.drop_index("ix_crypto_balance_snapshots_checked_at", table_name="crypto_balance_snapshots")
    op.drop_index("ix_crypto_balance_snapshots_chain", table_name="crypto_balance_snapshots")
    op.drop_table("crypto_balance_snapshots")
    op.drop_index("ix_wallet_nonces_user_id", table_name="wallet_nonces")
    op.drop_index("ix_wallet_nonces_used_at", table_name="wallet_nonces")
    op.drop_index("ix_wallet_nonces_expires_at", table_name="wallet_nonces")
    op.drop_index("ix_wallet_nonces_address", table_name="wallet_nonces")
    op.drop_table("wallet_nonces")
    op.drop_index("ix_evm_wallets_user_id", table_name="evm_wallets")
    op.drop_index("ix_evm_wallets_address", table_name="evm_wallets")
    op.drop_table("evm_wallets")
    op.drop_index("ix_user_subscriptions_user_id", table_name="user_subscriptions")
    op.drop_index("ix_user_subscriptions_stripe_subscription_id", table_name="user_subscriptions")
    op.drop_index("ix_user_subscriptions_stripe_price_id", table_name="user_subscriptions")
    op.drop_index("ix_user_subscriptions_status", table_name="user_subscriptions")
    op.drop_index("ix_user_subscriptions_plan_id", table_name="user_subscriptions")
    op.drop_table("user_subscriptions")
    op.drop_index("ix_user_billing_accounts_user_id", table_name="user_billing_accounts")
    op.drop_index("ix_user_billing_accounts_stripe_customer_id", table_name="user_billing_accounts")
    op.drop_table("user_billing_accounts")
    op.drop_index("ix_billing_plans_stripe_price_id", table_name="billing_plans")
    op.drop_index("ix_billing_plans_active", table_name="billing_plans")
    op.drop_table("billing_plans")
