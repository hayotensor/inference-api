"""billing_events: user-facing billing history (HYP-91)

Adds `billing_events`, an append-only per-user log of subscription creations,
renewals, plan changes, scheduled/actual cancellations, payment failures and
pay-as-you-go wallet top-ups (see app/db/models/billing_event.py). Backfills a
best-effort history from what already exists: one "subscription_created" row
per Stripe-backed user_subscriptions row (plus "subscription_canceled" if it
has since ended) and one "wallet_topup" row per wallet_transactions purchase.

Revision ID: 20261016_0017
Revises: 20261016_0016
Create Date: 2026-10-16
"""

import uuid
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20261016_0017"
down_revision: str | None = "20261016_0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "billing_events",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("description", sa.String(length=512), nullable=False),
        sa.Column("plan_name", sa.String(length=255), nullable=True),
        sa.Column("amount_cents", sa.Integer(), nullable=True),
        sa.Column("currency", sa.String(length=16), nullable=True),
        sa.Column("tokens", sa.BigInteger(), nullable=True),
        sa.Column("stripe_reference", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_billing_events_user_id_users", ondelete="cascade"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_billing_events"),
    )
    op.create_index("ix_billing_events_user_id", "billing_events", ["user_id"])
    op.create_index("ix_billing_events_event_type", "billing_events", ["event_type"])
    op.create_index("ix_billing_events_stripe_reference", "billing_events", ["stripe_reference"])
    op.create_index("ix_billing_events_created_at", "billing_events", ["created_at"])

    _backfill()


def _backfill() -> None:
    bind = op.get_bind()
    events = sa.table(
        "billing_events",
        sa.column("id", sa.Uuid(as_uuid=True)),
        sa.column("user_id", sa.Uuid(as_uuid=True)),
        sa.column("event_type", sa.String),
        sa.column("description", sa.String),
        sa.column("plan_name", sa.String),
        sa.column("currency", sa.String),
        sa.column("tokens", sa.BigInteger),
        sa.column("stripe_reference", sa.String),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )
    subscriptions = sa.table(
        "user_subscriptions",
        sa.column("user_id", sa.Uuid(as_uuid=True)),
        sa.column("stripe_subscription_id", sa.String),
        sa.column("plan_id", sa.Uuid(as_uuid=True)),
        sa.column("status", sa.String),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    plans = sa.table(
        "billing_plans",
        sa.column("id", sa.Uuid(as_uuid=True)),
        sa.column("name", sa.String),
        sa.column("currency", sa.String),
    )
    wallet_transactions = sa.table(
        "wallet_transactions",
        sa.column("user_id", sa.Uuid(as_uuid=True)),
        sa.column("delta_tokens", sa.BigInteger),
        sa.column("kind", sa.String),
        sa.column("stripe_payment_intent_id", sa.String),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )

    rows: list[dict] = []
    subscription_rows = bind.execute(
        sa.select(
            subscriptions.c.user_id,
            subscriptions.c.stripe_subscription_id,
            subscriptions.c.status,
            subscriptions.c.created_at,
            subscriptions.c.updated_at,
            plans.c.name,
            plans.c.currency,
        )
        .select_from(subscriptions.outerjoin(plans, plans.c.id == subscriptions.c.plan_id))
        .where(subscriptions.c.stripe_subscription_id.is_not(None))
    ).all()
    for sub in subscription_rows:
        label = sub.name or "Subscription"
        base = {
            "user_id": sub.user_id,
            "plan_name": sub.name,
            "currency": sub.currency,
            "tokens": None,
            "stripe_reference": sub.stripe_subscription_id,
        }
        rows.append(
            {
                **base,
                "id": uuid.uuid4(),
                "event_type": "subscription_created",
                "description": f"Subscribed to {label}",
                "created_at": sub.created_at,
            }
        )
        if sub.status == "canceled":
            rows.append(
                {
                    **base,
                    "id": uuid.uuid4(),
                    "event_type": "subscription_canceled",
                    "description": f"Canceled {label}",
                    "created_at": sub.updated_at,
                }
            )

    topups = bind.execute(
        sa.select(
            wallet_transactions.c.user_id,
            wallet_transactions.c.delta_tokens,
            wallet_transactions.c.stripe_payment_intent_id,
            wallet_transactions.c.created_at,
        ).where(wallet_transactions.c.kind == "purchase")
    ).all()
    for topup in topups:
        rows.append(
            {
                "id": uuid.uuid4(),
                "user_id": topup.user_id,
                "event_type": "wallet_topup",
                "description": f"Pay-as-you-go top-up: {topup.delta_tokens:,} credits",
                "plan_name": None,
                "currency": None,
                "tokens": topup.delta_tokens,
                "stripe_reference": topup.stripe_payment_intent_id,
                "created_at": topup.created_at,
            }
        )

    if rows:
        op.bulk_insert(events, rows)


def downgrade() -> None:
    op.drop_index("ix_billing_events_created_at", table_name="billing_events")
    op.drop_index("ix_billing_events_stripe_reference", table_name="billing_events")
    op.drop_index("ix_billing_events_event_type", table_name="billing_events")
    op.drop_index("ix_billing_events_user_id", table_name="billing_events")
    op.drop_table("billing_events")
