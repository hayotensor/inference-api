"""usage_periods: add subnet_stake_allowance source column

Records the weekly, stake-proportional inference-token allowance earned by
continuous subnet delegators (read from the ht-indexer). Summed into
total_allowance alongside the stripe/erc20/substrate/manual sources.

Revision ID: 20260706_0007
Revises: 20260625_0006
Create Date: 2026-07-06
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260706_0007"
down_revision: str | None = "20260625_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "usage_periods",
        sa.Column(
            "subnet_stake_allowance",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
    )


def downgrade() -> None:
    op.drop_column("usage_periods", "subnet_stake_allowance")
