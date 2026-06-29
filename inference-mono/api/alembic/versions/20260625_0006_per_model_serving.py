"""per-(miner, model) serving: miner_models enclave state, per-model tokens,
usage-event model link, model_allowlist

Revision ID: 20260625_0006
Revises: 20260623_0005
Create Date: 2026-06-25
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260625_0006"
down_revision: str | None = "20260623_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- miner_models: per-model-enclave serving + attestation state ---
    op.add_column("miner_models", sa.Column("tee_endpoint", sa.String(length=512), nullable=True))
    op.add_column("miner_models", sa.Column("tls_cert_fingerprint", sa.String(length=128), nullable=True))
    op.add_column("miner_models", sa.Column("enclave_verify_key", sa.String(length=128), nullable=True))
    op.add_column(
        "miner_models",
        sa.Column("attestation_status", sa.String(length=16), nullable=False, server_default="pending"),
    )
    op.add_column("miner_models", sa.Column("attestation_mode", sa.String(length=32), nullable=True))
    op.add_column("miner_models", sa.Column("attestation_verified_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("miner_models", sa.Column("attestation_expiry", sa.DateTime(timezone=True), nullable=True))
    op.add_column("miner_models", sa.Column("usage_chain_head", sa.String(length=128), nullable=True))
    op.add_column("miner_models", sa.Column("usage_count", sa.Integer(), nullable=True))
    op.add_column("miner_models", sa.Column("usage_total_tokens", sa.Integer(), nullable=True))
    op.create_index("ix_miner_models_attestation_status", "miner_models", ["attestation_status"])
    op.create_index("ix_miner_models_attestation_expiry", "miner_models", ["attestation_expiry"])

    # --- provisioned_tokens: token becomes per-(miner, model) ---
    op.add_column("provisioned_tokens", sa.Column("model_id", sa.String(length=200), nullable=True))
    op.create_index("ix_provisioned_tokens_model_id", "provisioned_tokens", ["model_id"])
    # Swap the partial-unique active index from (miner_id) to (miner_id, model_id).
    op.drop_index("uq_provisioned_tokens_miner_active", table_name="provisioned_tokens")
    op.create_index(
        "uq_provisioned_tokens_miner_model_active",
        "provisioned_tokens",
        ["miner_id", "model_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
        sqlite_where=sa.text("status = 'active'"),
    )

    # --- inference_usage_events: link to the model-enclave + receipt reconciliation ---
    op.add_column("inference_usage_events", sa.Column("miner_model_id", sa.Uuid(), nullable=True))
    op.add_column(
        "inference_usage_events", sa.Column("miner_receipt_node_id", sa.String(length=128), nullable=True)
    )
    op.create_foreign_key(
        "fk_inference_usage_events_miner_model_id_miner_models",
        "inference_usage_events",
        "miner_models",
        ["miner_model_id"],
        ["id"],
        ondelete="set null",
    )
    op.create_index("ix_inference_usage_events_miner_model_id", "inference_usage_events", ["miner_model_id"])

    # --- model_allowlist: platform-approved model artifacts ---
    op.create_table(
        "model_allowlist",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("model_id", sa.String(length=200), nullable=False),
        sa.Column("model_version", sa.String(length=120), nullable=True),
        sa.Column("model_hash", sa.String(length=128), nullable=False),
        sa.Column("args_hash", sa.String(length=128), nullable=True),
        sa.Column("gpu_hash", sa.String(length=128), nullable=True),
        sa.Column("label", sa.String(length=120), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "model_id", "model_version", "model_hash", name="uq_model_allowlist_model_version_hash"
        ),
    )
    op.create_index("ix_model_allowlist_model_id", "model_allowlist", ["model_id"])
    op.create_index("ix_model_allowlist_active", "model_allowlist", ["active"])


def downgrade() -> None:
    op.drop_index("ix_model_allowlist_active", table_name="model_allowlist")
    op.drop_index("ix_model_allowlist_model_id", table_name="model_allowlist")
    op.drop_table("model_allowlist")

    op.drop_index("ix_inference_usage_events_miner_model_id", table_name="inference_usage_events")
    op.drop_constraint(
        "fk_inference_usage_events_miner_model_id_miner_models",
        "inference_usage_events",
        type_="foreignkey",
    )
    op.drop_column("inference_usage_events", "miner_receipt_node_id")
    op.drop_column("inference_usage_events", "miner_model_id")

    # Restore the per-miner partial-unique active index.
    op.drop_index("uq_provisioned_tokens_miner_model_active", table_name="provisioned_tokens")
    op.create_index(
        "uq_provisioned_tokens_miner_active",
        "provisioned_tokens",
        ["miner_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
        sqlite_where=sa.text("status = 'active'"),
    )
    op.drop_index("ix_provisioned_tokens_model_id", table_name="provisioned_tokens")
    op.drop_column("provisioned_tokens", "model_id")

    op.drop_index("ix_miner_models_attestation_expiry", table_name="miner_models")
    op.drop_index("ix_miner_models_attestation_status", table_name="miner_models")
    op.drop_column("miner_models", "usage_total_tokens")
    op.drop_column("miner_models", "usage_count")
    op.drop_column("miner_models", "usage_chain_head")
    op.drop_column("miner_models", "attestation_expiry")
    op.drop_column("miner_models", "attestation_verified_at")
    op.drop_column("miner_models", "attestation_mode")
    op.drop_column("miner_models", "attestation_status")
    op.drop_column("miner_models", "enclave_verify_key")
    op.drop_column("miner_models", "tls_cert_fingerprint")
    op.drop_column("miner_models", "tee_endpoint")
