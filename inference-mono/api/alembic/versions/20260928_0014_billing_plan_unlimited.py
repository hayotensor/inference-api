"""billing plans: is_unlimited flag (HYP-76)

Adds the "Unlimited" tier's marker column. A subscriber on a plan with this set
skips the allowance ceiling entirely in app/usage/service.py -- no 402, and no
draw against the HYP-66 pay-as-you-go wallet either -- while still writing
usage events for observability.

Revision ID: 20260928_0014
Revises: 20260928_0013
Create Date: 2026-09-28
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260928_0014"
down_revision: str | None = "20260928_0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "billing_plans",
        sa.Column("is_unlimited", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("billing_plans", "is_unlimited")
