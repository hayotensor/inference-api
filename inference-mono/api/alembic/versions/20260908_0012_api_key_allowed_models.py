"""api_keys: add allowed_models scoping column

Optional per-key model allowlist. ``NULL``/empty means unrestricted (the key
can reach every model), matching how ``scopes`` already works -- this is the
same "opt-in restriction" pattern, just scoped to model ids instead of
capability strings.

Revision ID: 20260908_0012
Revises: 20260826_0011
Create Date: 2026-09-08
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260908_0012"
down_revision: str | None = "20260826_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "api_keys",
        sa.Column("allowed_models", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("api_keys", "allowed_models")
