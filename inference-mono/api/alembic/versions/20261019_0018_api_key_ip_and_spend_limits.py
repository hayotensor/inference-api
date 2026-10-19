"""api_keys: add IP allowlist and spend-limit columns (HYP-45)

Purely additive, nullable columns -- every existing key keeps its current
behavior (usable from any IP, no spend cap):

* ``allowed_ips``     JSON list of IPs/CIDRs; NULL/empty = unrestricted.
* ``limit_period``    "daily" | "monthly" | "never"; applies to both caps.
* ``credit_limit``    cap on credits (charged tokens) consumed per period.
* ``usd_limit_cents`` cap on pay-as-you-go wallet spend per period.

Revision ID: 20261019_0018
Revises: 20261016_0017
Create Date: 2026-10-19
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20261019_0018"
down_revision: str | None = "20261016_0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("api_keys", sa.Column("allowed_ips", sa.JSON(), nullable=True))
    op.add_column("api_keys", sa.Column("limit_period", sa.String(length=16), nullable=True))
    op.add_column("api_keys", sa.Column("credit_limit", sa.BigInteger(), nullable=True))
    op.add_column("api_keys", sa.Column("usd_limit_cents", sa.BigInteger(), nullable=True))


def downgrade() -> None:
    op.drop_column("api_keys", "usd_limit_cents")
    op.drop_column("api_keys", "credit_limit")
    op.drop_column("api_keys", "limit_period")
    op.drop_column("api_keys", "allowed_ips")
