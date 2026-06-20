"""router service clients and expiring reservations

Revision ID: 20260619_0004
Revises: 20260617_0003
Create Date: 2026-06-19
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260619_0004"
down_revision: str | None = "20260617_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "service_clients",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("role", sa.String(length=6), nullable=False),
        sa.Column("prefix", sa.String(length=32), nullable=False),
        sa.Column("last_four", sa.String(length=4), nullable=False),
        sa.Column("hashed_token", sa.String(length=64), nullable=False),
        sa.Column("rate_limit_per_minute", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("hashed_token"),
    )
    op.create_index("ix_service_clients_expires_at", "service_clients", ["expires_at"])
    op.create_index("ix_service_clients_prefix", "service_clients", ["prefix"])
    op.create_index("ix_service_clients_revoked_at", "service_clients", ["revoked_at"])
    op.create_index("ix_service_clients_role", "service_clients", ["role"])

    op.add_column("inference_usage_events", sa.Column("router_client_id", sa.Uuid(), nullable=True))
    op.add_column("inference_usage_events", sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("inference_usage_events", sa.Column("released_at", sa.DateTime(timezone=True), nullable=True))
    op.execute(
        """
        UPDATE inference_usage_events
        SET expires_at = created_at + INTERVAL '900 seconds'
        WHERE expires_at IS NULL
        """
    )
    op.alter_column("inference_usage_events", "expires_at", nullable=False)
    op.create_foreign_key(
        "fk_inference_usage_events_router_client_id_service_clients",
        "inference_usage_events",
        "service_clients",
        ["router_client_id"],
        ["id"],
        ondelete="set null",
    )
    op.create_index(
        "ix_inference_usage_events_router_client_id",
        "inference_usage_events",
        ["router_client_id"],
    )
    op.create_index("ix_inference_usage_events_expires_at", "inference_usage_events", ["expires_at"])
    op.create_index(
        "ix_inference_usage_events_status_expires_at",
        "inference_usage_events",
        ["status", "expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_inference_usage_events_status_expires_at", table_name="inference_usage_events")
    op.drop_index("ix_inference_usage_events_expires_at", table_name="inference_usage_events")
    op.drop_index("ix_inference_usage_events_router_client_id", table_name="inference_usage_events")
    op.drop_constraint(
        "fk_inference_usage_events_router_client_id_service_clients",
        "inference_usage_events",
        type_="foreignkey",
    )
    op.drop_column("inference_usage_events", "released_at")
    op.drop_column("inference_usage_events", "expires_at")
    op.drop_column("inference_usage_events", "router_client_id")

    op.drop_index("ix_service_clients_role", table_name="service_clients")
    op.drop_index("ix_service_clients_revoked_at", table_name="service_clients")
    op.drop_index("ix_service_clients_prefix", table_name="service_clients")
    op.drop_index("ix_service_clients_expires_at", table_name="service_clients")
    op.drop_table("service_clients")
