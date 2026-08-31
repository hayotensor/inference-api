"""miner statistics: per-attempt records, epoch aggregates, signed epoch snapshots

Replaces the removed merkle-DAG statistics plane. The platform now records every
forward attempt first-hand (``miner_request_stats``), reconciles it against the
enclave's signed usage chain, aggregates a finalized per-epoch view
(``miner_epoch_stats``), and stores the platform-signed artifact validators score
from (``miner_epoch_snapshots``).

Also:
- ``miner_models.receipt_cursor_row_id`` — durable cursor into each enclave's
  ``/usage/receipts`` feed (the collector used to keep this in memory, so a
  restart re-read the chain from row 0).
- drops ``inference_usage_events.miner_receipt_node_id`` — a merkle-DAG node id
  that no code ever wrote and that has no meaning without the DAG.

Revision ID: 20260810_0009
Revises: 20260716_0008
Create Date: 2026-08-10
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260810_0009"
down_revision: str | None = "20260716_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- durable receipts cursor -------------------------------------------
    op.add_column("miner_models", sa.Column("receipt_cursor_row_id", sa.BigInteger(), nullable=True))

    # --- miner_request_stats: one row per forward attempt ------------------
    op.create_table(
        "miner_request_stats",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("epoch", sa.Integer(), nullable=True),
        sa.Column("miner_id", sa.Uuid(), nullable=True),
        sa.Column("miner_hotkey", sa.String(length=128), nullable=False),
        sa.Column("subnet_node_id", sa.Integer(), nullable=True),
        sa.Column("miner_model_id", sa.Uuid(), nullable=True),
        sa.Column("model_id", sa.String(length=200), nullable=False),
        sa.Column("model_version", sa.String(length=120), nullable=True),
        sa.Column("model_hash", sa.String(length=128), nullable=True),
        sa.Column("request_id", sa.String(length=128), nullable=True),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("ttft_ms", sa.Integer(), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completion_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("verified", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["miner_id"], ["miners.id"], ondelete="set null"),
        sa.ForeignKeyConstraint(["miner_model_id"], ["miner_models.id"], ondelete="set null"),
    )
    op.create_index("ix_miner_request_stats_epoch", "miner_request_stats", ["epoch"])
    op.create_index("ix_miner_request_stats_miner_id", "miner_request_stats", ["miner_id"])
    op.create_index("ix_miner_request_stats_miner_hotkey", "miner_request_stats", ["miner_hotkey"])
    op.create_index("ix_miner_request_stats_miner_model_id", "miner_request_stats", ["miner_model_id"])
    op.create_index("ix_miner_request_stats_model_id", "miner_request_stats", ["model_id"])
    op.create_index("ix_miner_request_stats_request_id", "miner_request_stats", ["request_id"])
    op.create_index("ix_miner_request_stats_outcome", "miner_request_stats", ["outcome"])
    op.create_index("ix_miner_request_stats_created_at", "miner_request_stats", ["created_at"])
    # Composite: the per-epoch aggregation scan.
    op.create_index("ix_miner_request_stats_epoch_hotkey", "miner_request_stats", ["epoch", "miner_hotkey"])
    # Composite: the receipt -> attempt reconciliation lookup.
    op.create_index(
        "ix_miner_request_stats_miner_request", "miner_request_stats", ["miner_id", "request_id"]
    )

    # --- miner_epoch_stats: finalized per-(epoch, hotkey, model) aggregate --
    op.create_table(
        "miner_epoch_stats",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("epoch", sa.Integer(), nullable=False),
        sa.Column("miner_hotkey", sa.String(length=128), nullable=False),
        sa.Column("subnet_node_id", sa.Integer(), nullable=True),
        sa.Column("model_id", sa.String(length=200), nullable=False),
        # "" not NULL: part of the UNIQUE key, and NULLs never compare equal.
        sa.Column("model_version", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("requests_ok", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("requests_failed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("prompt_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("completion_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("total_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("verified_prompt_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("verified_completion_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("verified_total_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("latency_p50_ms", sa.Integer(), nullable=True),
        sa.Column("latency_p95_ms", sa.Integer(), nullable=True),
        sa.Column("tee_usage_count_end", sa.BigInteger(), nullable=True),
        sa.Column("tee_usage_chain_head_end", sa.String(length=128), nullable=True),
        sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "epoch",
            "miner_hotkey",
            "model_id",
            "model_version",
            name="uq_miner_epoch_stats_epoch_miner_model",
        ),
    )
    op.create_index("ix_miner_epoch_stats_epoch", "miner_epoch_stats", ["epoch"])
    op.create_index("ix_miner_epoch_stats_miner_hotkey", "miner_epoch_stats", ["miner_hotkey"])

    # --- miner_epoch_snapshots: the signed artifact, stored verbatim --------
    op.create_table(
        "miner_epoch_snapshots",
        # autoincrement=False: the epoch is always supplied explicitly. Without it
        # PostgreSQL makes an integer PK SERIAL, and an insert that forgot the epoch
        # would silently get a sequence value instead of failing.
        sa.Column("epoch", sa.Integer(), nullable=False, autoincrement=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("signature", sa.String(length=256), nullable=False),
        sa.Column("signer_pubkey", sa.String(length=128), nullable=True),
        sa.Column("entry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("epoch"),
    )
    op.create_index("ix_miner_epoch_snapshots_finalized_at", "miner_epoch_snapshots", ["finalized_at"])

    # --- drop the dead merkle-DAG receipt pointer ---------------------------
    op.drop_column("inference_usage_events", "miner_receipt_node_id")


def downgrade() -> None:
    op.add_column(
        "inference_usage_events",
        sa.Column("miner_receipt_node_id", sa.String(length=128), nullable=True),
    )

    op.drop_index("ix_miner_epoch_snapshots_finalized_at", table_name="miner_epoch_snapshots")
    op.drop_table("miner_epoch_snapshots")

    op.drop_index("ix_miner_epoch_stats_miner_hotkey", table_name="miner_epoch_stats")
    op.drop_index("ix_miner_epoch_stats_epoch", table_name="miner_epoch_stats")
    op.drop_table("miner_epoch_stats")

    for name in (
        "ix_miner_request_stats_miner_request",
        "ix_miner_request_stats_epoch_hotkey",
        "ix_miner_request_stats_created_at",
        "ix_miner_request_stats_outcome",
        "ix_miner_request_stats_request_id",
        "ix_miner_request_stats_model_id",
        "ix_miner_request_stats_miner_model_id",
        "ix_miner_request_stats_miner_hotkey",
        "ix_miner_request_stats_miner_id",
        "ix_miner_request_stats_epoch",
    ):
        op.drop_index(name, table_name="miner_request_stats")
    op.drop_table("miner_request_stats")

    op.drop_column("miner_models", "receipt_cursor_row_id")
