"""custom referral codes + /r/<code> short links (HYP-93)

Widens `users.referral_code` / `referrals.referral_code_used` from 16 to 32
chars so a user can pick a custom code, adds a case-insensitive unique index on
`lower(users.referral_code)`, and adds `referral_code_aliases` -- a user's
previous codes, kept so links already shared with an old code keep attributing
signups to them (and so nobody else can claim it).

Revision ID: 20261016_0016
Revises: 20260928_0015
Create Date: 2026-10-16
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20261016_0016"
down_revision: str | None = "20260928_0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "users", "referral_code", existing_type=sa.String(length=16), type_=sa.String(length=32)
    )
    op.alter_column(
        "referrals",
        "referral_code_used",
        existing_type=sa.String(length=16),
        type_=sa.String(length=32),
        existing_nullable=False,
    )
    op.create_index(
        "ux_users_referral_code_lower", "users", [sa.text("lower(referral_code)")], unique=True
    )

    op.create_table(
        "referral_code_aliases",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="cascade"),
        sa.PrimaryKeyConstraint("id", name="pk_referral_code_aliases"),
    )
    op.create_index("ix_referral_code_aliases_user_id", "referral_code_aliases", ["user_id"])
    op.create_index(
        "ux_referral_code_aliases_code_lower",
        "referral_code_aliases",
        [sa.text("lower(code)")],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ux_referral_code_aliases_code_lower", table_name="referral_code_aliases")
    op.drop_index("ix_referral_code_aliases_user_id", table_name="referral_code_aliases")
    op.drop_table("referral_code_aliases")
    op.drop_index("ux_users_referral_code_lower", table_name="users")
    # Custom codes longer than the old limit can't survive the narrower column;
    # clear them so a fresh 8-char code is generated lazily on next access.
    op.execute("UPDATE users SET referral_code = NULL WHERE length(referral_code) > 16")
    op.execute(
        "UPDATE referrals SET referral_code_used = substr(referral_code_used, 1, 16) "
        "WHERE length(referral_code_used) > 16"
    )
    op.alter_column(
        "referrals",
        "referral_code_used",
        existing_type=sa.String(length=32),
        type_=sa.String(length=16),
        existing_nullable=False,
    )
    op.alter_column(
        "users", "referral_code", existing_type=sa.String(length=32), type_=sa.String(length=16)
    )
