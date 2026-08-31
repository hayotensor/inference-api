"""billing accounts: track a pending Stripe Checkout Session

create_checkout_session only refused a second checkout once our own DB had a
synced active subscription, and that sync happens on the webhook — so a
double-click, two tabs, or a retry before the webhook lands could open two
Checkout Sessions and get the customer billed twice. This adds a per-account
"pending checkout session" marker that closes that window; it is set when a
session is created and cleared once that session resolves.

Revision ID: 20260826_0011
Revises: 20260824_0010
Create Date: 2026-08-26
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260826_0011"
down_revision: str | None = "20260824_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "user_billing_accounts",
        sa.Column("pending_checkout_session_id", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "user_billing_accounts",
        sa.Column("pending_checkout_session_created_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("user_billing_accounts", "pending_checkout_session_created_at")
    op.drop_column("user_billing_accounts", "pending_checkout_session_id")
