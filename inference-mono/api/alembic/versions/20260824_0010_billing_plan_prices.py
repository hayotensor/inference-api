"""billing plans: real money columns (monthly + annual Stripe prices)

Plans previously stored only a single Stripe price ID and no amount, so the
dashboard and the public pricing page had nothing to render and showed "$0" for
every tier. This adds the displayed amount, the currency, and a second
(annual) Stripe price per plan, then backfills the shipped catalog amounts for
the rows the seeder already created.

Revision ID: 20260824_0010
Revises: 20260810_0009
Create Date: 2026-08-24
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260824_0010"
down_revision: str | None = "20260810_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (slug, monthly cents, previous allowance, catalog allowance) — mirrors
# app/billing/plans.py. Annual amounts are left NULL here: the seeder fills them in
# only alongside an annual price ID, so the UI can never advertise an annual price
# that Stripe could not charge.
#
# The allowances moved with the prices (a $9 plan granting 100k tokens is $90 per
# million). Rows still holding exactly the previous allowance were never touched by
# an operator, so they are safe to correct; anything else is left alone.
_CATALOG_AMOUNTS = (
    ("starter", 900, 100_000, 1_000_000),
    ("pro", 4900, 1_000_000, 10_000_000),
    ("business", 29900, 10_000_000, 100_000_000),
)


def upgrade() -> None:
    op.add_column("billing_plans", sa.Column("stripe_annual_price_id", sa.String(length=255), nullable=True))
    op.add_column("billing_plans", sa.Column("price_cents", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("billing_plans", sa.Column("annual_price_cents", sa.Integer(), nullable=True))
    op.add_column(
        "billing_plans", sa.Column("currency", sa.String(length=3), nullable=False, server_default="usd")
    )
    op.create_index(
        "ix_billing_plans_stripe_annual_price_id",
        "billing_plans",
        ["stripe_annual_price_id"],
        unique=True,
    )

    # Backfill the catalog amounts. Only touches rows that still have no amount,
    # so an operator-set price survives the migration.
    plans = sa.table(
        "billing_plans",
        sa.column("slug", sa.String),
        sa.column("price_cents", sa.Integer),
        sa.column("monthly_token_allowance", sa.BigInteger),
    )
    for slug, price_cents, previous_allowance, catalog_allowance in _CATALOG_AMOUNTS:
        op.execute(
            plans.update()
            .where(sa.and_(plans.c.slug == slug, sa.or_(plans.c.price_cents == 0, plans.c.price_cents.is_(None))))
            .values(price_cents=price_cents)
        )
        op.execute(
            plans.update()
            .where(
                sa.and_(
                    plans.c.slug == slug,
                    plans.c.monthly_token_allowance == previous_allowance,
                )
            )
            .values(monthly_token_allowance=catalog_allowance)
        )


def downgrade() -> None:
    op.drop_index("ix_billing_plans_stripe_annual_price_id", table_name="billing_plans")
    op.drop_column("billing_plans", "currency")
    op.drop_column("billing_plans", "annual_price_cents")
    op.drop_column("billing_plans", "price_cents")
    op.drop_column("billing_plans", "stripe_annual_price_id")
