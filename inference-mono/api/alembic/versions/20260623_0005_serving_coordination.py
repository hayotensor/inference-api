"""serving / coordination plane: miners, miner_models, provisioned_tokens

Revision ID: 20260623_0005
Revises: 20260619_0004
Create Date: 2026-06-23
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260623_0005"
down_revision: str | None = "20260619_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "miners",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("hotkey", sa.String(length=128), nullable=False),
        sa.Column("subnet_node_id", sa.Integer(), nullable=True),
        sa.Column("peer_id", sa.String(length=128), nullable=True),
        sa.Column("tee_endpoint", sa.String(length=512), nullable=False),
        sa.Column("tls_cert_fingerprint", sa.String(length=128), nullable=True),
        sa.Column("enclave_verify_key", sa.String(length=128), nullable=True),
        sa.Column("attestation_status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("attestation_mode", sa.String(length=32), nullable=True),
        sa.Column("attestation_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attestation_expiry", sa.DateTime(timezone=True), nullable=True),
        sa.Column("miner_hash", sa.String(length=128), nullable=True),
        sa.Column("chain_class", sa.String(length=64), nullable=True),
        sa.Column("health", sa.String(length=16), nullable=False, server_default="unknown"),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=True),
        sa.Column("capacity", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("usage_chain_head", sa.String(length=128), nullable=True),
        sa.Column("usage_count", sa.Integer(), nullable=True),
        sa.Column("usage_total_tokens", sa.Integer(), nullable=True),
        sa.Column("registered_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("hotkey"),
    )
    op.create_index("ix_miners_hotkey", "miners", ["hotkey"])
    op.create_index("ix_miners_subnet_node_id", "miners", ["subnet_node_id"])
    op.create_index("ix_miners_attestation_status", "miners", ["attestation_status"])
    op.create_index("ix_miners_attestation_expiry", "miners", ["attestation_expiry"])
    op.create_index("ix_miners_miner_hash", "miners", ["miner_hash"])
    op.create_index("ix_miners_health", "miners", ["health"])
    op.create_index("ix_miners_last_seen", "miners", ["last_seen"])

    op.create_table(
        "miner_models",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("miner_id", sa.Uuid(), nullable=False),
        sa.Column("model_id", sa.String(length=200), nullable=False),
        sa.Column("model_version", sa.String(length=120), nullable=True),
        sa.Column("model_hash", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="loaded"),
        sa.Column("loaded", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_advertised_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["miner_id"], ["miners.id"], ondelete="cascade"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("miner_id", "model_id", "model_version", name="uq_miner_models_miner_model_version"),
    )
    op.create_index("ix_miner_models_miner_id", "miner_models", ["miner_id"])
    op.create_index("ix_miner_models_model_id", "miner_models", ["model_id"])

    op.create_table(
        "provisioned_tokens",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("miner_id", sa.Uuid(), nullable=False),
        sa.Column("key_id", sa.String(length=128), nullable=False),
        sa.Column("encrypted_token", sa.LargeBinary(), nullable=False),
        sa.Column("admin_encrypted_token", sa.LargeBinary(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column("provisioned_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["miner_id"], ["miners.id"], ondelete="cascade"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_provisioned_tokens_miner_id", "provisioned_tokens", ["miner_id"])
    op.create_index("ix_provisioned_tokens_status", "provisioned_tokens", ["status"])
    op.create_index("ix_provisioned_tokens_expires_at", "provisioned_tokens", ["expires_at"])
    # At most one ACTIVE provisioned token per miner (partial unique index).
    op.create_index(
        "uq_provisioned_tokens_miner_active",
        "provisioned_tokens",
        ["miner_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
        sqlite_where=sa.text("status = 'active'"),
    )

    op.add_column("inference_usage_events", sa.Column("miner_id", sa.Uuid(), nullable=True))
    op.add_column("inference_usage_events", sa.Column("miner_hotkey", sa.String(length=128), nullable=True))
    op.add_column("inference_usage_events", sa.Column("miner_model_hash", sa.String(length=128), nullable=True))
    op.create_foreign_key(
        "fk_inference_usage_events_miner_id_miners",
        "inference_usage_events",
        "miners",
        ["miner_id"],
        ["id"],
        ondelete="set null",
    )
    op.create_index("ix_inference_usage_events_miner_id", "inference_usage_events", ["miner_id"])
    op.create_index("ix_inference_usage_events_miner_hotkey", "inference_usage_events", ["miner_hotkey"])


def downgrade() -> None:
    op.drop_index("ix_inference_usage_events_miner_hotkey", table_name="inference_usage_events")
    op.drop_index("ix_inference_usage_events_miner_id", table_name="inference_usage_events")
    op.drop_constraint(
        "fk_inference_usage_events_miner_id_miners",
        "inference_usage_events",
        type_="foreignkey",
    )
    op.drop_column("inference_usage_events", "miner_model_hash")
    op.drop_column("inference_usage_events", "miner_hotkey")
    op.drop_column("inference_usage_events", "miner_id")

    op.drop_index("uq_provisioned_tokens_miner_active", table_name="provisioned_tokens")
    op.drop_index("ix_provisioned_tokens_expires_at", table_name="provisioned_tokens")
    op.drop_index("ix_provisioned_tokens_status", table_name="provisioned_tokens")
    op.drop_index("ix_provisioned_tokens_miner_id", table_name="provisioned_tokens")
    op.drop_table("provisioned_tokens")

    op.drop_index("ix_miner_models_model_id", table_name="miner_models")
    op.drop_index("ix_miner_models_miner_id", table_name="miner_models")
    op.drop_table("miner_models")

    op.drop_index("ix_miners_last_seen", table_name="miners")
    op.drop_index("ix_miners_health", table_name="miners")
    op.drop_index("ix_miners_miner_hash", table_name="miners")
    op.drop_index("ix_miners_attestation_expiry", table_name="miners")
    op.drop_index("ix_miners_attestation_status", table_name="miners")
    op.drop_index("ix_miners_subnet_node_id", table_name="miners")
    op.drop_index("ix_miners_hotkey", table_name="miners")
    op.drop_table("miners")
